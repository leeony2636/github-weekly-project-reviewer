from typing import Any, cast

import cohere

from reviewer.prompts.gpt_prompt import GPT_SYSTEM_PROMPT
from reviewer.schemas import Finding, parse_model_response

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
                response_format=cast(
                    Any,
                    {
                        "type": "json_object",
                    },
                ),
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