from google import genai
from google.genai import types
from typing import Sequence
from reviewer.prompts.gemini_prompt import (
    GEMINI_SYSTEM_PROMPT,
)
from reviewer.prompts.cross_review_prompt import (
    CROSS_REVIEW_SYSTEM_PROMPT,
)
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
    build_cross_review_response_schema,
    build_model_response_schema,
    parse_cross_review_response,
    parse_model_response,
)

from . import ProviderError


class GeminiClient:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        thinking_level: str = "low",
        max_output_tokens: int = 800,
        max_findings: int = 5,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY가 비어 있습니다."
            )

        if not model.strip():
            raise ValueError(
                "GEMINI_MODEL이 비어 있습니다."
            )

        normalized_thinking_level = (
            thinking_level.strip().lower()
        )

        if normalized_thinking_level not in {
            "low",
            "medium",
            "high",
        }:
            raise ValueError(
                "thinking_level은 low, medium, "
                "high 중 하나여야 합니다."
            )

        if max_output_tokens <= 0:
            raise ValueError(
                "max_output_tokens는 1 이상이어야 합니다."
            )

        if max_findings <= 0:
            raise ValueError(
                "max_findings는 1 이상이어야 합니다."
            )

        self.model = model
        thinking_levels = {
            "low": types.ThinkingLevel.LOW,
            "medium": types.ThinkingLevel.MEDIUM,
            "high": types.ThinkingLevel.HIGH,
        }

        self.thinking_level = thinking_levels[
            normalized_thinking_level
        ]
        self.max_output_tokens = max_output_tokens
        self.max_findings = max_findings
        self.client = genai.Client(
            api_key=api_key,
        )

    def review(
        self,
        user_prompt: str,
    ) -> list[Finding]:
        try:
            response = (
                self.client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            GEMINI_SYSTEM_PROMPT
                        ),
                        thinking_config=(
                            types.ThinkingConfig(
                                thinking_level=(
                                    self.thinking_level
                                ),
                            )
                        ),
                        max_output_tokens=(
                            self.max_output_tokens
                        ),
                        response_mime_type=(
                            "application/json"
                        ),
                        response_json_schema=(
                            build_model_response_schema()
                        ),
                    ),
                )
            )

            return parse_model_response(
                response.text or "",
                provider=self.provider,
                max_findings=self.max_findings,
            )

        except ProviderError:
            raise

        except Exception as exc:
            raise ProviderError(
                self.provider,
                "리뷰 호출",
                exc,
            ) from exc

    def cross_review(
        self,
        user_prompt: str,
        *,
        candidate_ids: Sequence[str],
    ) -> list[CrossReviewVote]:
        if not candidate_ids:
            raise ValueError(
                "교차평가 후보가 비어 있습니다."
            )

        try:
            response = (
                self.client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            CROSS_REVIEW_SYSTEM_PROMPT
                        ),
                        thinking_config=(
                            types.ThinkingConfig(
                                thinking_level=(
                                    self.thinking_level
                                ),
                            )
                        ),
                        max_output_tokens=(
                            self.max_output_tokens
                        ),
                        response_mime_type=(
                            "application/json"
                        ),
                        response_json_schema=(
                            build_cross_review_response_schema(
                                candidate_ids
                            )
                        ),
                    ),
                )
            )

            content = response.text or ""

            return parse_cross_review_response(
                content,
                provider=self.provider,
                expected_candidate_ids=(
                    candidate_ids
                ),
            )

        except ProviderError:
            raise

        except Exception as exc:
            raise ProviderError(
                self.provider,
                "교차평가 호출",
                exc,
            ) from exc