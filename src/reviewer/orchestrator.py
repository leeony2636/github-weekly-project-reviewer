from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from reviewer.consensus import (
    build_consensus,
    filter_valid_line_findings,
)
from reviewer.diff_parser import ground_findings
from reviewer.quota_guard import (
    QuotaBlockedError,
    QuotaGuard,
)
from reviewer.schemas import (
    ConsensusFinding,
    Finding,
    SchemaError,
)


EXPECTED_PROVIDERS = {
    "qwen",
    "gpt",
    "gemini",
}


class ReviewProvider(Protocol):
    provider: str

    def review(
        self,
        user_prompt: str,
    ) -> list[Finding]:
        ...


class OrchestrationError(RuntimeError):
    pass

def _safe_error_type(
    error: BaseException,
) -> str:
    current = error
    response_validation_failed = False

    while True:
        if isinstance(current, SchemaError):
            response_validation_failed = True

        if current.__cause__ is None:
            break

        current = current.__cause__

    if response_validation_failed:
        cause_name = type(current).__name__

        return (
            "ResponseValidationError"
            f"[cause={cause_name}]"
        )

    error_type = type(current).__name__
    status_code = getattr(
        current,
        "status_code",
        None,
    )

    if isinstance(status_code, int):
        return (
            f"{error_type}"
            f"[status={status_code}]"
        )

    return error_type

@dataclass(frozen=True, slots=True)
class ReviewRun:
    consensus_findings: tuple[
        ConsensusFinding,
        ...
    ]
    successful_providers: tuple[str, ...]
    provider_failures: dict[str, str]
    raw_finding_count: int
    invalid_line_count: int
    invalid_evidence_count: int = 0
    degraded: bool = False
    quota_snapshot: dict[
        str,
        dict[str, object],
    ] = field(default_factory=dict)


class ReviewOrchestrator:
    def __init__(
        self,
        providers: Sequence[ReviewProvider],
        *,
        quota_guard: QuotaGuard,
        timeout_seconds: int = 120,
        line_tolerance: int = 1,
        min_match_count: int = 2,
        min_successful_providers: int = 2,
    ) -> None:
        provider_names = [
            provider.provider
            for provider in providers
        ]

        if set(provider_names) != EXPECTED_PROVIDERS:
            raise ValueError(
                "qwen, gpt, gemini provider가 "
                "각각 하나씩 필요합니다."
            )

        if len(provider_names) != len(
            set(provider_names)
        ):
            raise ValueError(
                "provider 이름이 중복되었습니다."
            )

        if timeout_seconds < 1:
            raise ValueError(
                "timeout_seconds는 1 이상이어야 합니다."
            )

        if not 2 <= min_successful_providers <= 3:
            raise ValueError(
                "min_successful_providers는 "
                "2 이상 3 이하여야 합니다."
            )

        self.providers = tuple(providers)
        self.quota_guard = quota_guard
        self.timeout_seconds = timeout_seconds
        self.line_tolerance = line_tolerance
        self.min_match_count = min_match_count
        self.min_successful_providers = (
            min_successful_providers
        )

    def run(
        self,
        *,
        provider_prompts: Mapping[str, str],
        valid_lines: Mapping[
            str,
            frozenset[int],
        ],
        changed_lines: Mapping[
            str,
            Mapping[int, str],
        ],
    ) -> ReviewRun:
        if set(provider_prompts) != EXPECTED_PROVIDERS:
            raise ValueError(
                "qwen, gpt, gemini prompt가 "
                "각각 필요합니다."
            )

        for provider, prompt in (
            provider_prompts.items()
        ):
            if (
                not isinstance(prompt, str)
                or not prompt.strip()
            ):
                raise ValueError(
                    f"{provider} prompt가 비어 있습니다."
                )

        executor = ThreadPoolExecutor(
            max_workers=len(self.providers),
            thread_name_prefix="review-provider",
        )

        future_to_provider: dict[
            Future[list[Finding]],
            str,
        ] = {}

        failures: dict[str, str] = {}
        successful: list[str] = []
        raw_findings: list[Finding] = []

        try:
            for provider in self.providers:
                provider_name = provider.provider
                prompt = provider_prompts[
                    provider_name
                ]

                try:
                    self.quota_guard.reserve(
                        provider=provider_name,
                        prompt=prompt,
                    )
                except QuotaBlockedError as exc:
                    failures[provider_name] = (
                        type(exc).__name__
                    )
                    continue

                future = executor.submit(
                    provider.review,
                    prompt,
                )
                future_to_provider[future] = (
                    provider_name
                )

            done, not_done = wait(
                future_to_provider,
                timeout=self.timeout_seconds,
            )

            for future in done:
                provider_name = (
                    future_to_provider[future]
                )

                try:
                    findings = future.result()
                except Exception as exc:
                    self.quota_guard.record_failure(
                        provider_name,
                        exc,
                    )
                    failures[provider_name] = (
                        _safe_error_type(exc)
                    )
                    continue

                provider_identity_is_valid = all(
                    finding.provider
                    == provider_name
                    for finding in findings
                )

                if not provider_identity_is_valid:
                    mismatch_error = RuntimeError(
                        "provider identity mismatch"
                    )
                    self.quota_guard.record_failure(
                        provider_name,
                        mismatch_error,
                    )
                    failures[provider_name] = (
                        "ProviderIdentityMismatch"
                    )
                    continue

                self.quota_guard.record_success(
                    provider_name
                )
                successful.append(provider_name)
                raw_findings.extend(findings)

            for future in not_done:
                provider_name = (
                    future_to_provider[future]
                )

                timeout_error = TimeoutError(
                    "provider timeout"
                )

                self.quota_guard.record_failure(
                    provider_name,
                    timeout_error,
                )
                failures[provider_name] = (
                    "TimeoutError"
                )
                future.cancel()

        finally:
            executor.shutdown(
                wait=False,
                cancel_futures=True,
            )

        successful.sort()
        failures = dict(
            sorted(failures.items())
        )

        if (
            len(successful)
            < self.min_successful_providers
        ):
            failure_details = (
                ", ".join(
                    (
                        f"{provider}"
                        f"({error_type})"
                    )
                    for provider, error_type
                    in failures.items()
                )
                or "unknown"
            )

            raise OrchestrationError(
                "교차검증에 필요한 모델 수가 "
                "부족합니다. "
                f"성공={len(successful)}, "
                f"실패={failure_details}"
            )

        valid_findings = (
            filter_valid_line_findings(
                raw_findings,
                valid_lines,
            )
        )

        invalid_line_count = (
            len(raw_findings)
            - len(valid_findings)
        )

        grounded_findings, invalid_evidence_count = (
            ground_findings(
                valid_findings,
                changed_lines,
            )
        )

        consensus_findings = build_consensus(
            grounded_findings,
            valid_lines,
            line_tolerance=self.line_tolerance,
            min_match_count=self.min_match_count,
        )

        return ReviewRun(
            consensus_findings=tuple(
                consensus_findings
            ),
            successful_providers=tuple(
                successful
            ),
            provider_failures=failures,
            raw_finding_count=len(
                raw_findings
            ),
            invalid_line_count=(
                invalid_line_count
            ),
            invalid_evidence_count=(
                invalid_evidence_count
            ),
            degraded=False,
            quota_snapshot=(
                self.quota_guard.snapshot()
            ),
        )