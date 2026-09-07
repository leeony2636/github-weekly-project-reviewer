import os
import unittest
from unittest.mock import patch

from reviewer.config import ConfigError, RuntimeConfig


class ConfigTests(unittest.TestCase):
    def test_uses_requested_default_models(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = RuntimeConfig.from_env()

        self.assertEqual(
            config.qwen_model,
            "Qwen/Qwen2.5-Coder-3B-Instruct",
        )
        self.assertEqual(
            config.gpt_model,
            "openai/gpt-oss-20b",
        )
        self.assertEqual(
            config.gemini_model,
            "gemini-3.8-flash",
        )

    def test_uses_free_first_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = RuntimeConfig.from_env()

        self.assertTrue(config.free_only_mode)
        self.assertTrue(config.cache_enabled)
        self.assertEqual(config.gpt_review_percent, 20)
        self.assertEqual(
            config.gemini_review_percent,
            10,
        )
        self.assertEqual(
            config.max_gpt_calls_per_run,
            1,
        )

    def test_rejects_invalid_boolean(self) -> None:
        with patch.dict(
            os.environ,
            {"FREE_ONLY_MODE": "maybe"},
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                RuntimeConfig.from_env()

    def test_rejects_excessive_percent_total(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "GPT_REVIEW_PERCENT": "80",
                "GEMINI_REVIEW_PERCENT": "40",
            },
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                RuntimeConfig.from_env()

    def test_requires_enabled_provider_keys(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "test",
                "TARGET_GITHUB_TOKEN": "test",
                "TARGET_REPO": "owner/repository",
            },
            clear=True,
        ):
            with self.assertRaises(ConfigError) as context:
                RuntimeConfig.from_env(
                    require_secrets=True
                )

        message = str(context.exception)
        self.assertIn("GROQ_API_KEY", message)
        self.assertIn("GEMINI_API_KEY", message)

    def test_safe_summary_excludes_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "hidden-hf",
                "GROQ_API_KEY": "hidden-groq",
                "GEMINI_API_KEY": "hidden-gemini",
                "TARGET_GITHUB_TOKEN": "hidden-github",
            },
            clear=True,
        ):
            config = RuntimeConfig.from_env()

        summary_text = str(config.safe_summary())

        self.assertNotIn("hidden-hf", summary_text)
        self.assertNotIn("hidden-groq", summary_text)
        self.assertNotIn("hidden-gemini", summary_text)
        self.assertNotIn("hidden-github", summary_text)


if __name__ == "__main__":
    unittest.main()
