from typing import Any, Sequence, cast

import cohere
from cohere.types import (
    JsonObjectResponseFormatV2,
    Thinking,
)

from reviewer.langfuse_observer import generation_context
from reviewer.prompts.cross_review_prompt import (
    CROSS_REVIEW_SYSTEM_PROMPT,
)
from reviewer.prompts.gpt_prompt import GPT_SYSTEM_PROMPT
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
    build_cross_review_response_schema,
    build_model_response_schema,
    parse_cross_review_response,
    parse_model_response,
)

from . import ProviderError


class GPTClient:
    provider = "gpt"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "command-a-plus-05-2026",
        base_url: str = (
            "https://api.cohere.ai/compatibility/v1"
        ),
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 800,
        max_findings: int = 5,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "COHERE_API_KEY가 비어 있습니다."
            )

        if not model.strip():
            raise ValueError(
                "GPT_MODEL이 비어 있습니다."
            )

        if not base_url.strip():
            raise ValueError(
                "GPT_BASE_URL이 비어 있습니다."
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
        self.max_output_tokens = max_output_tokens
        self.max_findings = max_findings

        if self.max_output_tokens >= 2_048:
            self.thinking = Thinking(
                type="enabled",
                token_budget=min(
                    4_096,
                    self.max_output_tokens // 2,
                ),
            )
        else:
            self.thinking = Thinking(
                type="disabled",
            )

        # 기존 설정 인터페이스를 유지하기 위한 값이다.
        # 실제 호출은 Cohere SDK의 공식 기본 주소를 사용한다.
        self.base_url = base_url

        self.client = cohere.ClientV2(
            api_key=api_key,
            timeout=timeout_seconds,
        )

    def review(
        self,
        user_prompt: str,
    ) -> list[Finding]:
        try:
            # 프롬프트 전문 대신 문자 수만 Langfuse에 기록한다.
            with generation_context(
                name="command-a-review",
                model=self.model,
                input_data={
                    "prompt_chars": len(user_prompt),
                },
                metadata={
                    "provider": self.provider,
                    "actual_provider": "cohere",
                    "task": "review",
                },
            ) as generation:
                response = self.client.chat(
                    model=self.model,
                    messages=cast(
                        Any,
                        [
                            {
                                "role": "system",
                                "content": GPT_SYSTEM_PROMPT,
                            },
                            {
                                "role": "user",
                                "content": user_prompt,
                            },
                        ],
                    ),
                    temperature=0.1,
                    max_tokens=self.max_output_tokens,
                    thinking=self.thinking,
                    response_format=(
                        JsonObjectResponseFormatV2(
                            type="json_object",
                            json_schema=(
                                build_model_response_schema()
                            ),
                        )
                    ),
                )

                finish_reason = str(
                    getattr(
                        response,
                        "finish_reason",
                        "",
                    )
                ).upper()

                if "MAX_TOKENS" in finish_reason:
                    raise ValueError(
                        "Cohere 응답이 토큰 제한으로 잘렸습니다."
                    )

                content_items = (
                    response.message.content or []
                )
                text_parts: list[str] = []

                for content_item in content_items:
                    if (
                        getattr(
                            content_item,
                            "type",
                            None,
                        )
                        != "text"
                    ):
                        continue

                    text = getattr(
                        content_item,
                        "text",
                        None,
                    )

                    if isinstance(text, str):
                        text_parts.append(text)

                content = "".join(text_parts)

                if generation is not None:
                    usage = getattr(
                        response,
                        "usage",
                        None,
                    )
                    tokens = getattr(
                        usage,
                        "tokens",
                        None,
                    )

                    input_tokens = (
                        getattr(
                            tokens,
                            "input_tokens",
                            0,
                        )
                        or 0
                    )
                    output_tokens = (
                        getattr(
                            tokens,
                            "output_tokens",
                            0,
                        )
                        or 0
                    )

                    generation.update(
                        output={
                            "response_chars": len(content),
                        },
                        usage_details={
                            "input": input_tokens,
                            "output": output_tokens,
                        },
                    )

            return parse_model_response(
                content,
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
            # 교차평가도 실제 API 호출 단위로 Langfuse에 기록한다.
            with generation_context(
                name="command-a-cross-review",
                model=self.model,
                input_data={
                    "prompt_chars": len(user_prompt),
                    "candidate_count": len(candidate_ids),
                },
                metadata={
                    "provider": self.provider,
                    "actual_provider": "cohere",
                    "task": "cross_review",
                },
            ) as generation:
                response = self.client.chat(
                    model=self.model,
                    messages=cast(
                        Any,
                        [
                            {
                                "role": "system",
                                "content": (
                                    CROSS_REVIEW_SYSTEM_PROMPT
                                ),
                            },
                            {
                                "role": "user",
                                "content": user_prompt,
                            },
                        ],
                    ),
                    temperature=0.1,
                    max_tokens=self.max_output_tokens,
                    thinking=self.thinking,
                    response_format=(
                        JsonObjectResponseFormatV2(
                            type="json_object",
                            json_schema=(
                                build_cross_review_response_schema(
                                    candidate_ids
                                )
                            ),
                        )
                    ),
                )

                content_items = (
                    response.message.content or []
                )
                text_parts: list[str] = []

                for item in content_items:
                    text = getattr(
                        item,
                        "text",
                        None,
                    )

                    if isinstance(text, str):
                        text_parts.append(text)

                content = "".join(text_parts)

                if generation is not None:
                    usage = getattr(
                        response,
                        "usage",
                        None,
                    )
                    tokens = getattr(
                        usage,
                        "tokens",
                        None,
                    )

                    input_tokens = (
                        getattr(
                            tokens,
                            "input_tokens",
                            0,
                        )
                        or 0
                    )
                    output_tokens = (
                        getattr(
                            tokens,
                            "output_tokens",
                            0,
                        )
                        or 0
                    )

                    generation.update(
                        output={
                            "response_chars": len(content),
                        },
                        usage_details={
                            "input": input_tokens,
                            "output": output_tokens,
                        },
                    )

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