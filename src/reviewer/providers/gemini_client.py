from google import genai
from google.genai import types

from reviewer.prompts.gemini_prompt import GEMINI_SYSTEM_PROMPT
from reviewer.schemas import Finding, parse_model_response

from . import ProviderError


class GeminiClient:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int = 800,
        max_findings: int = 20,
    ) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY가 비어 있습니다.")
        if not model.strip():
            raise ValueError("GEMINI_MODEL이 비어 있습니다.")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens는 1 이상이어야 합니다.")
        if max_findings <= 0:
            raise ValueError("max_findings는 1 이상이어야 합니다.")

        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_findings = max_findings
        self.client = genai.Client(api_key=api_key)

    def review(self, user_prompt: str) -> list[Finding]:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=GEMINI_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_output_tokens=self.max_output_tokens,
                    response_mime_type="application/json",
                ),
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