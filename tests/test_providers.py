import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from reviewer.providers import ProviderError
from reviewer.providers.gemini_client import GeminiClient
from reviewer.providers.gpt_client import GPTClient
from reviewer.providers.qwen_client import QwenClient


def model_response_json() -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "file": "src/example.py",
                    "line": 10,
                    "category": "bug",
                    "evidence": "value = source.value",
                    "severity": "P1",
                    "message": (
                        "None 입력에서 예외가 발생합니다."
                    ),
                    "reason": (
                        "입력값 확인 없이 속성에 접근합니다."
                    ),
                    "confidence": 0.85,
                }
            ]
        },
        ensure_ascii=False,
    )


def openai_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=model_response_json()
                )
            )
        ]
    )


class ProviderTests(unittest.TestCase):
    @patch(
        "reviewer.providers.qwen_client.OpenAI"
    )
    def test_qwen_parses_json_response(
        self,
        openai_class,
    ) -> None:
        sdk_client = openai_class.return_value
        sdk_client.chat.completions.create.return_value = (
            openai_response()
        )

        provider = QwenClient(
            api_key="test-hf-token",
        )
        findings = provider.review("PR Diff")

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].provider,
            "qwen",
        )
        self.assertEqual(findings[0].line, 10)

        constructor_arguments = (
            openai_class.call_args.kwargs
        )
        self.assertIn(
            "huggingface.co",
            constructor_arguments["base_url"],
        )

    @patch(
        "reviewer.providers.gpt_client.cohere.ClientV2"
    )
    def test_gpt_uses_cohere_and_requests_json(
        self,
        cohere_class,
    ) -> None:
        sdk_client = cohere_class.return_value
        response_text = (
            openai_response()
            .choices[0]
            .message.content
        )
        sdk_client.chat.return_value = (
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="thinking",
                            thinking="internal reasoning",
                        ),
                        SimpleNamespace(
                            type="text",
                            text=response_text,
                        ),
                    ],
                ),
            )
        )

        provider = GPTClient(
            api_key="test-cohere-key",
        )
        findings = provider.review("PR Diff")

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].provider,
            "gpt",
        )

        constructor_arguments = (
            cohere_class.call_args.kwargs
        )
        self.assertEqual(
            constructor_arguments["api_key"],
            "test-cohere-key",
        )
        self.assertEqual(
            constructor_arguments["timeout"],
            120.0,
        )

        call_arguments = (
            sdk_client.chat.call_args.kwargs
        )
        self.assertEqual(
            call_arguments["model"],
            "command-a-plus-05-2026",
        )
        self.assertEqual(
            call_arguments["messages"][0]["role"],
            "system",
        )

        response_format = call_arguments[
            "response_format"
        ]
        self.assertEqual(
            response_format["type"],
            "json_object",
        )
        self.assertEqual(
            response_format["schema"]["required"],
            ["findings"],
        )
        self.assertEqual(
            call_arguments["max_tokens"],
            800,
        )


    @patch(
        "reviewer.providers.gemini_client.genai.Client"
    )
    def test_gemini_parses_json_response(
        self,
        client_class,
    ) -> None:
        sdk_client = client_class.return_value
        sdk_client.models.generate_content.return_value = (
            SimpleNamespace(
                text=model_response_json()
            )
        )

        provider = GeminiClient(
            api_key="test-gemini-key",
            model="test-gemini-model",
        )
        findings = provider.review("PR Diff")

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].provider,
            "gemini",
        )
        self.assertEqual(findings[0].line, 10)

    @patch(
        "reviewer.providers.qwen_client.OpenAI"
    )
    def test_qwen_error_does_not_expose_secret(
        self,
        openai_class,
    ) -> None:
        sdk_client = openai_class.return_value
        secret_text = "do-not-expose-this-value"

        sdk_client.chat.completions.create.side_effect = (
            RuntimeError(secret_text)
        )

        provider = QwenClient(
            api_key="test-hf-token",
        )

        with self.assertRaises(
            ProviderError
        ) as context:
            provider.review("PR Diff")

        self.assertNotIn(
            secret_text,
            str(context.exception),
        )
        self.assertIn(
            "RuntimeError",
            str(context.exception),
        )

    @patch(
        "reviewer.providers.gpt_client.cohere.ClientV2"
    )
    def test_gpt_rejects_invalid_json(
        self,
        cohere_class,
    ) -> None:
        sdk_client = cohere_class.return_value
        sdk_client.chat.return_value = (
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text="not-json",
                        ),
                    ],
                ),
            )
        )

        provider = GPTClient(
            api_key="test-cohere-key",
        )

        with self.assertRaises(ProviderError):
            provider.review("PR Diff")

    @patch(
        "reviewer.providers.gemini_client.genai.Client"
    )
    def test_gemini_uses_safe_json_configuration(
        self,
        client_class,
    ) -> None:
        sdk_client = client_class.return_value
        sdk_client.models.generate_content.return_value = (
            SimpleNamespace(
                text='{"findings":[]}'
            )
        )

        provider = GeminiClient(
            api_key="test-gemini-key",
            model="gemini-3.8-flash",
            thinking_level="low",
        )
        provider.review("PR Diff")

        call_arguments = (
            sdk_client.models.generate_content
            .call_args.kwargs
        )
        config = call_arguments["config"]

        self.assertEqual(
            config.response_mime_type,
            "application/json",
        )
        self.assertEqual(
            config.thinking_config
            .thinking_level.value.lower(),
            "low",
        )
        self.assertIsNone(
            config.temperature,
        )

if __name__ == "__main__":
    unittest.main()