import os
import re
from dataclasses import dataclass


class ConfigError(ValueError):
    pass


def _read_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, str(default)).strip()

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{name}은 정수여야 합니다."
        ) from exc

    if not minimum <= value <= maximum:
        raise ConfigError(
            f"{name}은 {minimum} 이상 "
            f"{maximum} 이하여야 합니다."
        )

    return value


def _read_bool(
    name: str,
    default: bool,
) -> bool:
    raw = os.getenv(
        name,
        "true" if default else "false",
    ).strip().lower()

    if raw in {"1", "true", "yes", "on"}:
        return True

    if raw in {"0", "false", "no", "off"}:
        return False

    raise ConfigError(
        f"{name}은 true 또는 false여야 합니다."
    )


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    hf_token: str
    cohere_api_key: str
    gemini_api_key: str
    target_github_token: str
    target_repo: str

    qwen_model: str
    qwen_base_url: str
    gpt_model: str
    gpt_base_url: str
    gemini_model: str
    gemini_thinking_level: str

    provider_timeout_seconds: int
    max_findings_per_model: int
    max_output_tokens: int
    gpt_max_output_tokens: int
    qwen_input_char_limit: int
    cloud_input_char_limit: int

    line_tolerance: int
    min_match_count: int
    min_successful_providers: int

    gpt_review_percent: int
    gemini_review_percent: int

    max_qwen_calls_per_run: int
    max_gpt_calls_per_run: int
    max_gemini_calls_per_run: int

    free_only_mode: bool
    cache_enabled: bool
    degraded_mode_on_quota: bool

    @classmethod
    def from_env(
        cls,
        *,
        require_secrets: bool = False,
    ) -> "RuntimeConfig":
        config = cls(
            hf_token=os.getenv(
                "HF_TOKEN",
                "",
            ).strip(),
            cohere_api_key=os.getenv(
                "COHERE_API_KEY",
                "",
            ).strip(),
            gemini_api_key=os.getenv(
                "GEMINI_API_KEY",
                "",
            ).strip(),
            target_github_token=(
                os.getenv("TARGET_GITHUB_TOKEN", "")
                or os.getenv("GITHUB_TOKEN", "")
            ).strip(),
            target_repo=os.getenv(
                "TARGET_REPO",
                "",
            ).strip(),

            qwen_model=os.getenv(
                "QWEN_MODEL",
                "Qwen/Qwen2.5-Coder-3B-Instruct",
            ).strip(),
            qwen_base_url=os.getenv(
                "QWEN_BASE_URL",
                (
                    "https://router.huggingface.co/"
                    "featherless-ai/v1"
                ),
            ).strip(),
            gpt_model=os.getenv(
                "GPT_MODEL",
                "command-a-plus-05-2026",
            ).strip(),
            gpt_base_url=os.getenv(
                "GPT_BASE_URL",
                (
                    "https://api.cohere.ai/"
                    "compatibility/v1"
                ),
            ).strip(),


            gemini_model=os.getenv(
                "GEMINI_MODEL",
                "gemini-3.8-flash",
            ).strip(),
            gemini_thinking_level=os.getenv(
                "GEMINI_THINKING_LEVEL",
                "low",
            ).strip().lower(),

            provider_timeout_seconds=_read_int(
                "PROVIDER_TIMEOUT_SECONDS",
                90,
                minimum=10,
                maximum=300,
            ),
            max_findings_per_model=_read_int(
                "MAX_FINDINGS_PER_MODEL",
                5,
                minimum=1,
                maximum=20,
            ),
            max_output_tokens=_read_int(
                "MAX_OUTPUT_TOKENS",
                800,
                minimum=100,
                maximum=4096,
            ),
            gpt_max_output_tokens=_read_int(
                "GPT_MAX_OUTPUT_TOKENS",
                800,
                minimum=100,
                maximum=64_000,
            ),
            qwen_input_char_limit=_read_int(
                "QWEN_INPUT_CHAR_LIMIT",
                4_800,
                minimum=2_000,
                maximum=4_800,
            ),
            cloud_input_char_limit=_read_int(
                "CLOUD_INPUT_CHAR_LIMIT",
                4_800,
                minimum=2_000,
                maximum=4_800,
            ),

            line_tolerance=_read_int(
                "LINE_TOLERANCE",
                1,
                minimum=0,
                maximum=3,
            ),
            min_match_count=_read_int(
                "MIN_MATCH_COUNT",
                2,
                minimum=2,
                maximum=3,
            ),
            min_successful_providers=_read_int(
                "MIN_SUCCESSFUL_PROVIDERS",
                2,
                minimum=2,
                maximum=3,
            ),

            gpt_review_percent=_read_int(
                "GPT_REVIEW_PERCENT",
                20,
                minimum=1,
                maximum=100,
            ),
            gemini_review_percent=_read_int(
                "GEMINI_REVIEW_PERCENT",
                10,
                minimum=1,
                maximum=100,
            ),

            max_qwen_calls_per_run=_read_int(
                "MAX_QWEN_CALLS_PER_RUN",
                1,
                minimum=0,
                maximum=1,
            ),
            max_gpt_calls_per_run=_read_int(
                "MAX_GPT_CALLS_PER_RUN",
                1,
                minimum=0,
                maximum=1,
            ),
            max_gemini_calls_per_run=_read_int(
                "MAX_GEMINI_CALLS_PER_RUN",
                1,
                minimum=0,
                maximum=1,
            ),

            free_only_mode=_read_bool(
                "FREE_ONLY_MODE",
                True,
            ),
            cache_enabled=_read_bool(
                "CACHE_ENABLED",
                True,
            ),
            degraded_mode_on_quota=_read_bool(
                "DEGRADED_MODE_ON_QUOTA",
                True,
            ),
        )

        config.validate_non_secret_values()

        if require_secrets:
            config.validate_runtime_secrets()

        return config

    def validate_non_secret_values(self) -> None:
        if self.target_repo and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            self.target_repo,
        ):
            raise ConfigError(
                "TARGET_REPO는 owner/repository "
                "형식이어야 합니다."
            )

        for name, value in (
            ("QWEN_MODEL", self.qwen_model),
            ("QWEN_BASE_URL", self.qwen_base_url),
            ("GPT_MODEL", self.gpt_model),
            ("GPT_BASE_URL", self.gpt_base_url),
            ("GEMINI_MODEL", self.gemini_model),
        ):
            if not value:
                raise ConfigError(
                    f"{name}이 비어 있습니다."
                )

        if self.gemini_thinking_level not in {
            "low",
            "medium",
            "high",
        }:
            raise ConfigError(
                "GEMINI_THINKING_LEVEL은 low, "
                "medium, high 중 하나여야 합니다."
            )

        if (
            self.gpt_review_percent
            + self.gemini_review_percent
            > 100
        ):
            raise ConfigError(
                "GPT_REVIEW_PERCENT와 "
                "GEMINI_REVIEW_PERCENT의 합은 "
                "100 이하여야 합니다."
            )

        if (
            self.max_qwen_calls_per_run == 0
            and self.max_gpt_calls_per_run == 0
            and self.max_gemini_calls_per_run == 0
        ):
            raise ConfigError(
                "최소 한 모델의 호출 제한은 "
                "1 이상이어야 합니다."
            )

    def validate_runtime_secrets(self) -> None:
        missing: list[str] = []

        if not self.target_github_token:
            missing.append("TARGET_GITHUB_TOKEN")

        if not self.target_repo:
            missing.append("TARGET_REPO")

        if (
            self.max_qwen_calls_per_run > 0
            and not self.hf_token
        ):
            missing.append("HF_TOKEN")

        if (
            self.max_gpt_calls_per_run > 0
            and not self.cohere_api_key
        ):
            missing.append("COHERE_API_KEY")

        if (
            self.max_gemini_calls_per_run > 0
            and not self.gemini_api_key
        ):
            missing.append("GEMINI_API_KEY")

        if missing:
            raise ConfigError(
                "필수 환경변수가 없습니다: "
                + ", ".join(missing)
            )

    def safe_summary(self) -> dict[str, object]:
        return {
            "target_repo": self.target_repo,
            "qwen_model": self.qwen_model,
            "qwen_base_url": self.qwen_base_url,
            "gpt_model": self.gpt_model,
            "gpt_base_url": self.gpt_base_url,
            "gemini_model": self.gemini_model,
            "gemini_thinking_level": (
                self.gemini_thinking_level
            ),
            "provider_timeout_seconds": (
                self.provider_timeout_seconds
            ),
            "max_findings_per_model": (
                self.max_findings_per_model
            ),
            "max_output_tokens": self.max_output_tokens,
            "gpt_max_output_tokens": (
                self.gpt_max_output_tokens
            ),
            "qwen_input_char_limit": (
                self.qwen_input_char_limit
            ),
            "cloud_input_char_limit": (
                self.cloud_input_char_limit
            ),
            "line_tolerance": self.line_tolerance,
            "min_match_count": self.min_match_count,
            "min_successful_providers": (
                self.min_successful_providers
            ),
            "gpt_review_percent": (
                self.gpt_review_percent
            ),
            "gemini_review_percent": (
                self.gemini_review_percent
            ),
            "max_qwen_calls_per_run": (
                self.max_qwen_calls_per_run
            ),
            "max_gpt_calls_per_run": (
                self.max_gpt_calls_per_run
            ),
            "max_gemini_calls_per_run": (
                self.max_gemini_calls_per_run
            ),
            "free_only_mode": self.free_only_mode,
            "cache_enabled": self.cache_enabled,
            "degraded_mode_on_quota": (
                self.degraded_mode_on_quota
            ),
        }