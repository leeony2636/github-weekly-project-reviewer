from openai import OpenAI
from typing import Sequence
from reviewer.prompts.qwen_prompt import QWEN_SYSTEM_PROMPT
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
    parse_cross_review_response,
    parse_model_response,
)
from reviewer.prompts.cross_review_prompt import (
    CROSS_REVIEW_SYSTEM_PROMPT,
)
from . import ProviderError


class QwenClient:
    provider = "qwen"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "Qwen/Qwen2.5-Coder-3B-Instruct",
        base_url: str = (
            "https://router.huggingface.co/"
            "featherless-ai/v1"
        ),
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 800,
        max_findings: int = 5,
    ) -> None:
        if not api_key.strip():
            raise ValueError("HF_TOKEN이 비어 있습니다.")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens는 1 이상이어야 합니다.")
        if max_findings <= 0:
            raise ValueError("max_findings는 1 이상이어야 합니다.")

        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_findings = max_findings

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def review(self, user_prompt: str) -> list[Finding]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": QWEN_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=0.1,
                max_tokens=self.max_output_tokens,
            )

            content = response.choices[0].message.content or ""

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
            response = (
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[
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
                    temperature=0.1,
                    max_tokens=(
                        self.max_output_tokens
                    ),
                )
            )

            content = (
                response.choices[0]
                .message.content
                or ""
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