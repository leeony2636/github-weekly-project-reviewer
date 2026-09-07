from dataclasses import dataclass
from threading import Lock
from typing import Mapping


PROVIDERS = {
    "qwen",
    "gpt",
    "gemini",
}


class QuotaError(RuntimeError):
    pass


class QuotaBlockedError(QuotaError):
    def __init__(
        self,
        provider: str,
        reason: str,
    ) -> None:
        self.provider = provider
        self.reason = reason

        super().__init__(
            f"{provider} 호출 차단: {reason}"
        )


@dataclass(frozen=True, slots=True)
class ProviderBudget:
    max_calls: int
    max_input_chars: int

    def __post_init__(self) -> None:
        if self.max_calls < 0:
            raise ValueError(
                "max_calls는 0 이상이어야 합니다."
            )

        if self.max_input_chars < 0:
            raise ValueError(
                "max_input_chars는 0 이상이어야 합니다."
            )


@dataclass(slots=True)
class ProviderUsage:
    reserved_calls: int = 0
    input_chars: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    quota_errors: int = 0
    blocked: bool = False
    blocked_reason: str = ""


class QuotaGuard:
    def __init__(
        self,
        budgets: Mapping[
            str,
            ProviderBudget,
        ],
    ) -> None:
        if set(budgets) != PROVIDERS:
            raise ValueError(
                "qwen, gpt, gemini 예산이 "
                "각각 필요합니다."
            )

        self.budgets = dict(budgets)
        self.usage = {
            provider: ProviderUsage()
            for provider in PROVIDERS
        }
        self._lock = Lock()

    def reserve(
        self,
        *,
        provider: str,
        prompt: str,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(
                f"지원하지 않는 provider입니다: {provider}"
            )

        if not isinstance(prompt, str):
            raise ValueError(
                "prompt는 문자열이어야 합니다."
            )

        prompt_size = len(prompt)

        with self._lock:
            budget = self.budgets[provider]
            usage = self.usage[provider]

            if usage.blocked:
                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            if (
                usage.reserved_calls
                >= budget.max_calls
            ):
                usage.blocked = True
                usage.blocked_reason = (
                    "per_run_call_limit"
                )

                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            if (
                usage.input_chars + prompt_size
                > budget.max_input_chars
            ):
                usage.blocked = True
                usage.blocked_reason = (
                    "input_character_limit"
                )

                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            usage.reserved_calls += 1
            usage.input_chars += prompt_size

    def record_success(
        self,
        provider: str,
    ) -> None:
        self._validate_provider(provider)

        with self._lock:
            self.usage[
                provider
            ].successful_calls += 1

    def record_failure(
        self,
        provider: str,
        error: Exception,
    ) -> None:
        self._validate_provider(provider)

        status_code = getattr(
            error,
            "status_code",
            None,
        )

        if status_code is None:
            status_code = getattr(
                error,
                "code",
                None,
            )

        error_type = type(error).__name__.lower()

        is_quota_error = (
            status_code == 429
            or "ratelimit" in error_type
            or "quota" in error_type
            or "resourceexhausted" in error_type
        )

        with self._lock:
            usage = self.usage[provider]
            usage.failed_calls += 1

            if is_quota_error:
                usage.quota_errors += 1
                usage.blocked = True
                usage.blocked_reason = (
                    "provider_quota_or_rate_limit"
                )

    def block(
        self,
        *,
        provider: str,
        reason: str,
    ) -> None:
        self._validate_provider(provider)

        safe_reason = (
            reason.strip()
            if isinstance(reason, str)
            else ""
        )

        if not safe_reason:
            safe_reason = "manually_blocked"

        with self._lock:
            usage = self.usage[provider]
            usage.blocked = True
            usage.blocked_reason = safe_reason

    def is_blocked(
        self,
        provider: str,
    ) -> bool:
        self._validate_provider(provider)

        with self._lock:
            return self.usage[
                provider
            ].blocked

    def available_provider_count(self) -> int:
        with self._lock:
            return sum(
                not usage.blocked
                and (
                    usage.reserved_calls
                    < self.budgets[
                        provider
                    ].max_calls
                )
                for provider, usage
                in self.usage.items()
            )

    def snapshot(
        self,
    ) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                provider: {
                    "max_calls": (
                        self.budgets[
                            provider
                        ].max_calls
                    ),
                    "max_input_chars": (
                        self.budgets[
                            provider
                        ].max_input_chars
                    ),
                    "reserved_calls": (
                        usage.reserved_calls
                    ),
                    "input_chars": (
                        usage.input_chars
                    ),
                    "successful_calls": (
                        usage.successful_calls
                    ),
                    "failed_calls": (
                        usage.failed_calls
                    ),
                    "quota_errors": (
                        usage.quota_errors
                    ),
                    "blocked": usage.blocked,
                    "blocked_reason": (
                        usage.blocked_reason
                    ),
                }
                for provider, usage
                in sorted(self.usage.items())
            }

    @staticmethod
    def _validate_provider(
        provider: str,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(
                f"지원하지 않는 provider입니다: {provider}"
            )