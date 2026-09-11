from typing import Sequence

from openai import OpenAI

from reviewer.langfuse_observer import generation_context
from reviewer.prompts.cross_review_prompt import (
    CROSS_REVIEW_SYSTEM_PROMPT,
)
from reviewer.prompts.qwen_prompt import QWEN_SYSTEM_PROMPT
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
    parse_cross_review_response,
    parse_model_response,
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
            raise ValueError(
                "HF_TOKEN이 비어 있습니다."
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

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def review(
        self,
        user_prompt: str,
    ) -> list[Finding]:
        try:
            # Qwen 1차 리뷰 호출을 Langfuse에 기록한다.
            # 실제 프롬프트 전문은 저장하지 않고 문자 수만 기록한다.
            with generation_context(
                name="qwen-review",
                model=self.model,
                input_data={
                    "prompt_chars": len(user_prompt),
                },
                metadata={
                    "provider": self.provider,
                    "task": "review",
                },
            ) as generation:
                response = (
                    self.client.chat.completions.create(
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
                )

                content = (
                    response.choices[0].message.content
                    or ""
                )

                if generation is not None:
                    usage = response.usage

                    input_tokens = (
                        getattr(
                            usage,
                            "prompt_tokens",
                            0,
                        )
                        or 0
                    )

                    output_tokens = (
                        getattr(
                            usage,
                            "completion_tokens",
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
            # Qwen 교차평가 호출을 별도 generation으로 기록한다.
            with generation_context(
                name="qwen-cross-review",
                model=self.model,
                input_data={
                    "prompt_chars": len(user_prompt),
                    "candidate_count": len(candidate_ids),
                },
                metadata={
                    "provider": self.provider,
                    "task": "cross_review",
                },
            ) as generation:
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
                        max_tokens=self.max_output_tokens,
                    )
                )

                content = (
                    response.choices[0].message.content
                    or ""
                )

                if generation is not None:
                    usage = response.usage

                    input_tokens = (
                        getattr(
                            usage,
                            "prompt_tokens",
                            0,
                        )
                        or 0
                    )

                    output_tokens = (
                        getattr(
                            usage,
                            "completion_tokens",
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