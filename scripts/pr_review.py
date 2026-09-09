import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence, TypeVar, cast

from reviewer.config import ConfigError, RuntimeConfig
from reviewer.github_service import (
    GitHubService,
    IncompletePullRequestDiff,
)
from reviewer.orchestrator import (
    OrchestrationError,
    ReviewOrchestrator,
    ReviewRun,
)
from reviewer.quota_guard import (
    ProviderBudget,
    QuotaGuard,
)
from reviewer.review_selector import (
    ReviewSelection,
    select_review_inputs,
)
from reviewer.security import SensitiveContentError
from reviewer.trace_store import TraceStore


def parse_arguments(
    arguments: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="3개 모델 기반 GitHub PR 리뷰어"
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="분석할 Pull Request 번호",
    )

    return parser.parse_args(arguments)


def resolve_pull_request_number(
    cli_value: int | None,
) -> int:
    if cli_value is not None:
        number = cli_value
    else:
        raw_value = os.getenv(
            "PR_NUMBER",
            "",
        ).strip()

        if not raw_value:
            raise ValueError(
                "--pr 또는 PR_NUMBER가 필요합니다."
            )

        try:
            number = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                "PR_NUMBER는 정수여야 합니다."
            ) from exc

    if number < 1:
        raise ValueError(
            "Pull Request 번호는 1 이상이어야 합니다."
        )

    return number


_ConfigValueT = TypeVar("_ConfigValueT")


def get_config_value(
    config: RuntimeConfig,
    name: str,
    default: _ConfigValueT,
) -> _ConfigValueT:
    return cast(
        _ConfigValueT,
        getattr(
            config,
            name,
            default,
        ),
    )

def create_orchestrator(
    config: RuntimeConfig,
    selection: ReviewSelection,
) -> ReviewOrchestrator:
    # 실제 실행할 때만 외부 SDK 모듈을 불러온다.
    from reviewer.providers.gemini_client import (
        GeminiClient,
    )
    from reviewer.providers.gpt_client import GPTClient
    from reviewer.providers.qwen_client import QwenClient

    max_output_tokens = int(
        get_config_value(
            config,
            "max_output_tokens",
            800,
        )
    )
    gpt_max_output_tokens = int(
        get_config_value(
            config,
            "gpt_max_output_tokens",
            max_output_tokens,
        )
    )
    max_findings = int(
        get_config_value(
            config,
            "max_findings_per_model",
            5,
        )
    )
    qwen_input_char_limit = int(
        get_config_value(
            config,
            "qwen_input_char_limit",
            4_800,
        )
    )
    cloud_input_char_limit = int(
        get_config_value(
            config,
            "cloud_input_char_limit",
            4_800,
        )
    )

    providers = [
        QwenClient(
            api_key=config.hf_token,
            model=config.qwen_model,
            base_url=config.qwen_base_url,
            timeout_seconds=(
                config.provider_timeout_seconds
            ),
            max_output_tokens=(
                max_output_tokens
            ),
            max_findings=max_findings,
        ),
        GPTClient(
            api_key=config.cohere_api_key,
            model=config.gpt_model,
            base_url=config.gpt_base_url,
            timeout_seconds=(
                config.provider_timeout_seconds
            ),
            max_output_tokens=(
                gpt_max_output_tokens
            ),
            max_findings=max_findings,
        ),
        GeminiClient(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            thinking_level=str(
                get_config_value(
                    config,
                    "gemini_thinking_level",
                    "low",
                )
            ),
            max_output_tokens=max_output_tokens,
            max_findings=max_findings,
        ),
    ]

    quota_guard = QuotaGuard(
        {
            "qwen": ProviderBudget(
                max_calls=int(
                    get_config_value(
                        config,
                        "max_qwen_calls_per_run",
                        5,
                    )
                ),
                max_input_chars=(
                    qwen_input_char_limit
                ),
                max_output_tokens=(
                    max_output_tokens
                ),
            ),
            "gpt": ProviderBudget(
                max_calls=int(
                    get_config_value(
                        config,
                        "max_gpt_calls_per_run",
                        5,
                    )
                ),
                max_input_chars=(
                    cloud_input_char_limit
                ),
                max_output_tokens=(
                    gpt_max_output_tokens
                ),
                max_estimated_tokens_per_call=(
                    gpt_max_output_tokens
                    + 4_096
                ),
            ),
            "gemini": ProviderBudget(
                max_calls=int(
                    get_config_value(
                        config,
                        "max_gemini_calls_per_run",
                        6,
                    )
                ),
                max_input_chars=(
                    cloud_input_char_limit
                ),
                max_output_tokens=(
                    max_output_tokens
                ),
            ),
        }
    )

    return ReviewOrchestrator(
        providers,
        quota_guard=quota_guard,
        timeout_seconds=(
            config.provider_timeout_seconds
        ),
        line_tolerance=config.line_tolerance,
        min_match_count=config.min_match_count,
        min_successful_providers=int(
            get_config_value(
                config,
                "min_successful_providers",
                3,
            )
        ),
    )

def make_cache_key(
    *,
    config: RuntimeConfig,
    selection: ReviewSelection,
    diff_sha256: str,
) -> str:
    prompt_hashes = {
        provider: hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        for provider, prompt
        in sorted(
            selection.provider_prompts.items()
        )
    }

    cache_identity = {
        "schema_version": 1,
        "repository": config.target_repo,
        "diff_sha256": diff_sha256,
        "models": {
            "qwen": config.qwen_model,
            "gpt": config.gpt_model,
            "gemini": config.gemini_model,
        },
        "prompt_hashes": prompt_hashes,
        "line_tolerance": config.line_tolerance,
        "min_match_count": config.min_match_count,
    }

    encoded = json.dumps(
        cache_identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


def make_output_payload(
    *,
    repository: str,
    pull_request_number: int,
    diff_sha256: str,
    selected_files: dict[
        str,
        tuple[str, ...],
    ],
    result: ReviewRun,
    cache_hit: bool = False,
) -> dict[str, object]:
    return {
        "repository": repository,
        "pull_request_number": (
            pull_request_number
        ),
        "diff_sha256": diff_sha256,
        "cache_hit": cache_hit,
        "degraded": result.degraded,
        "selected_files": {
            provider: list(files)
            for provider, files
            in sorted(selected_files.items())
        },
        "successful_providers": list(
            result.successful_providers
        ),
        "provider_failures": (
            result.provider_failures
        ),
        "quota_snapshot": (
            result.quota_snapshot
        ),
        "raw_finding_count": (
            result.raw_finding_count
        ),
        "invalid_line_count": (
            result.invalid_line_count
        ),
        "invalid_evidence_count": (
            result.invalid_evidence_count
        ),
        "consensus_count": len(
            result.consensus_findings
        ),
        "findings": [
            finding.to_dict()
            for finding
            in result.consensus_findings
        ],
    }


def write_output(
    output_path: str | Path,
    payload: dict[str, object],
) -> None:
    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def run_review(
    *,
    config: RuntimeConfig,
    pull_request_number: int,
    trace_store: TraceStore,
) -> int:
    run_id: str | None = None

    output_path = os.getenv(
        "REVIEW_OUTPUT_PATH",
        "review_result.json",
    )

    try:
        with GitHubService.connect(
            token=config.target_github_token,
            target_repo=config.target_repo,
        ) as github_service:
            review_input = (
                github_service.collect_pull_request(
                    pull_request_number
                )
            )

        selection = select_review_inputs(
            review_input,
            gpt_percent=int(
                get_config_value(
                    config,
                    "gpt_review_percent",
                    20,
                )
            ),
            gemini_percent=int(
                get_config_value(
                    config,
                    "gemini_review_percent",
                    10,
                )
            ),
            qwen_input_char_limit=int(
                get_config_value(
                    config,
                    "qwen_input_char_limit",
                    4_800,
                )
            ),
            cloud_input_char_limit=int(
                get_config_value(
                    config,
                    "cloud_input_char_limit",
                    4_800,
                )
            ),
        )

        cache_key = make_cache_key(
            config=config,
            selection=selection,
            diff_sha256=review_input.diff_sha256,
        )

        cache_enabled = bool(
            get_config_value(
                config,
                "cache_enabled",
                True,
            )
        )

        if cache_enabled:
            cached_payload = (
                trace_store.get_cached_payload(
                    cache_key
                )
            )

            if cached_payload is not None:
                output_payload = dict(
                    cached_payload
                )
                output_payload["cache_hit"] = True

                write_output(
                    output_path,
                    output_payload,
                )

                print(
                    "동일한 Diff의 캐시 결과를 "
                    "사용했습니다. API 호출 없음."
                )
                return 0

        run_id = trace_store.start_run(
            repository=config.target_repo,
            pull_request_number=(
                pull_request_number
            ),
            diff_sha256=(
                review_input.diff_sha256
            ),
        )

        orchestrator = create_orchestrator(
            config,
            selection,
        )

        result = orchestrator.run(
            provider_prompts=(
                selection.provider_prompts
            ),
            valid_lines=review_input.valid_lines,
            changed_lines=(
                review_input.changed_lines
            ),
        )

        if result.provider_failures:
            result = replace(
                result,
                degraded=True,
            )

        degraded_mode_enabled = bool(
            get_config_value(
                config,
                "degraded_mode_on_quota",
                True,
            )
        )

        if (
            result.degraded
            and not degraded_mode_enabled
        ):
            raise OrchestrationError(
                "일부 provider가 실패했고 "
                "degraded 모드가 비활성화되어 "
                "결과를 승인하지 않습니다."
            )

        payload = make_output_payload(
            repository=config.target_repo,
            pull_request_number=(
                pull_request_number
            ),
            diff_sha256=(
                review_input.diff_sha256
            ),
            selected_files=(
                selection.selected_files
            ),
            result=result,
        )

        write_output(
            output_path,
            payload,
        )

        trace_store.complete_run(
            run_id=run_id,
            result=result,
        )

        if cache_enabled:
            trace_store.save_cached_payload(
                cache_key=cache_key,
                payload=payload,
            )

        print(
            "리뷰 완료: "
            f"합의 항목 "
            f"{len(result.consensus_findings)}개, "
            f"라인 검증 제외 "
            f"{result.invalid_line_count}개, "
            f"근거 검증 제외 "
            f"{result.invalid_evidence_count}개"
        )

        if result.provider_failures:
            failed_names = ", ".join(
                sorted(
                    result.provider_failures
                )
            )
            print(
                "일부 모델 실패: "
                f"{failed_names}"
            )

        return 0

    except SensitiveContentError as exc:
        if run_id is None:
            run_id = trace_store.start_run(
                repository=config.target_repo,
                pull_request_number=(
                    pull_request_number
                ),
            )

        trace_store.fail_run(
            run_id=run_id,
            status="blocked_sensitive_content",
            error=exc,
        )
        print(
            "민감정보로 의심되는 내용이 있어 "
            "리뷰를 중단했습니다."
        )
        return 2

    except IncompletePullRequestDiff as exc:
        if run_id is None:
            run_id = trace_store.start_run(
                repository=config.target_repo,
                pull_request_number=(
                    pull_request_number
                ),
            )

        trace_store.fail_run(
            run_id=run_id,
            status="blocked_incomplete_diff",
            error=exc,
        )
        print(
            "PR Diff가 완전하지 않아 "
            "리뷰를 중단했습니다."
        )
        return 3

    except OrchestrationError as exc:
        if run_id is None:
            run_id = trace_store.start_run(
                repository=config.target_repo,
                pull_request_number=(
                    pull_request_number
                ),
            )

        trace_store.fail_run(
            run_id=run_id,
            status="failed",
            error=exc,
        )
        print(str(exc))
        return 4

    except Exception as exc:
        if run_id is None:
            run_id = trace_store.start_run(
                repository=config.target_repo,
                pull_request_number=(
                    pull_request_number
                ),
            )

        trace_store.fail_run(
            run_id=run_id,
            status="failed",
            error=exc,
        )
        print(
            "리뷰 실행 실패: "
            f"error_type={type(exc).__name__}"
        )
        return 1


def main(
    arguments: Sequence[str] | None = None,
) -> int:
    try:
        args = parse_arguments(arguments)
        pull_request_number = (
            resolve_pull_request_number(
                args.pr
            )
        )
        config = RuntimeConfig.from_env(
            require_secrets=True
        )
    except (ConfigError, ValueError) as exc:
        print(str(exc))
        return 1

    database_path = os.getenv(
        "REVIEW_DB_PATH",
        "review_trace.db",
    )

    with TraceStore(database_path) as trace_store:
        return run_review(
            config=config,
            pull_request_number=(
                pull_request_number
            ),
            trace_store=trace_store,
        )


if __name__ == "__main__":
    sys.exit(main())
