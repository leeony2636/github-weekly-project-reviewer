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

    # 시스템 지시문은 사용자 입력과 별도로 전송된다.
    system_prompt_char_reserve: int = 2_000

    # 실제 설정에서 허용 가능한 최대 출력값을
    # 보수적으로 예약한다.
    max_output_tokens: int = 4_096

    # 무료 API 할당량을 보호하기 위해 실제 모델 한도보다
    # 보수적인 8,192토큰 상한을 적용한다.
    max_estimated_tokens_per_call: int = 8_192

    def __post_init__(self) -> None:
        for name, value in (
            ("max_calls", self.max_calls),
            ("max_input_chars", self.max_input_chars),
            (
                "system_prompt_char_reserve",
                self.system_prompt_char_reserve,
            ),
            (
                "max_output_tokens",
                self.max_output_tokens,
            ),
            (
                "max_estimated_tokens_per_call",
                self.max_estimated_tokens_per_call,
            ),
        ):
            if value < 0:
                raise ValueError(
                    f"{name}은 0 이상이어야 합니다."
                )

        if self.max_estimated_tokens_per_call < 1:
            raise ValueError(
                "max_estimated_tokens_per_call은 "
                "1 이상이어야 합니다."
            )


@dataclass(slots=True)
class ProviderUsage:
    reserved_calls: int = 0
    input_chars: int = 0
    reserved_output_tokens: int = 0
    estimated_total_tokens: int = 0
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
        self._validate_provider(provider)

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
                self._block_usage(
                    usage,
                    "per_run_call_limit",
                )
                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            if (
                usage.input_chars + prompt_size
                > budget.max_input_chars
            ):
                self._block_usage(
                    usage,
                    "input_character_limit",
                )
                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            estimated_input_tokens = (
                prompt_size
                + budget.system_prompt_char_reserve
                + 1
            ) // 2

            estimated_tokens = (
                estimated_input_tokens
                + budget.max_output_tokens
            )

            if (
                estimated_tokens
                > budget.max_estimated_tokens_per_call
            ):
                self._block_usage(
                    usage,
                    "estimated_token_limit",
                )
                raise QuotaBlockedError(
                    provider,
                    usage.blocked_reason,
                )

            usage.reserved_calls += 1
            usage.input_chars += prompt_size
            usage.reserved_output_tokens += (
                budget.max_output_tokens
            )
            usage.estimated_total_tokens += (
                estimated_tokens
            )

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
                self._block_usage(
                    usage,
                    "provider_quota_or_rate_limit",
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
            self._block_usage(
                self.usage[provider],
                safe_reason,
            )

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
                    "system_prompt_char_reserve": (
                        self.budgets[
                            provider
                        ].system_prompt_char_reserve
                    ),
                    "max_output_tokens": (
                        self.budgets[
                            provider
                        ].max_output_tokens
                    ),
                    "max_estimated_tokens_per_call": (
                        self.budgets[
                            provider
                        ].max_estimated_tokens_per_call
                    ),
                    "reserved_calls": (
                        usage.reserved_calls
                    ),
                    "input_chars": (
                        usage.input_chars
                    ),
                    "reserved_output_tokens": (
                        usage.reserved_output_tokens
                    ),
                    "estimated_total_tokens": (
                        usage.estimated_total_tokens
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
    def _block_usage(
        usage: ProviderUsage,
        reason: str,
    ) -> None:
        usage.blocked = True
        usage.blocked_reason = reason

    @staticmethod
    def _validate_provider(
        provider: str,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(
                f"지원하지 않는 provider입니다: {provider}"
            )