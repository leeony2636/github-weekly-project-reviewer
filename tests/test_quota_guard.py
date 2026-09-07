import unittest

from reviewer.quota_guard import (
    ProviderBudget,
    QuotaBlockedError,
    QuotaGuard,
)


class FakeRateLimitError(RuntimeError):
    status_code = 429


def make_guard(
    *,
    max_calls: int = 1,
    max_input_chars: int = 100,
) -> QuotaGuard:
    budgets = {
        provider: ProviderBudget(
            max_calls=max_calls,
            max_input_chars=max_input_chars,
        )
        for provider in (
            "qwen",
            "gpt",
            "gemini",
        )
    }

    return QuotaGuard(budgets)


class QuotaGuardTests(unittest.TestCase):
    def test_allows_first_call(self) -> None:
        guard = make_guard()

        guard.reserve(
            provider="qwen",
            prompt="review",
        )
        guard.record_success("qwen")

        snapshot = guard.snapshot()

        self.assertEqual(
            snapshot["qwen"]["reserved_calls"],
            1,
        )
        self.assertEqual(
            snapshot["qwen"]["successful_calls"],
            1,
        )

    def test_blocks_second_call(self) -> None:
        guard = make_guard(max_calls=1)

        guard.reserve(
            provider="gpt",
            prompt="first",
        )

        with self.assertRaises(
            QuotaBlockedError
        ):
            guard.reserve(
                provider="gpt",
                prompt="second",
            )

        self.assertTrue(
            guard.is_blocked("gpt")
        )

    def test_blocks_large_prompt(self) -> None:
        guard = make_guard(
            max_input_chars=5
        )

        with self.assertRaises(
            QuotaBlockedError
        ):
            guard.reserve(
                provider="gemini",
                prompt="123456",
            )

    def test_blocks_after_429(self) -> None:
        guard = make_guard(max_calls=2)

        guard.reserve(
            provider="gpt",
            prompt="review",
        )
        guard.record_failure(
            "gpt",
            FakeRateLimitError("limited"),
        )

        self.assertTrue(
            guard.is_blocked("gpt")
        )

        snapshot = guard.snapshot()

        self.assertEqual(
            snapshot["gpt"]["quota_errors"],
            1,
        )
        self.assertEqual(
            snapshot["gpt"]["blocked_reason"],
            "provider_quota_or_rate_limit",
        )

    def test_normal_error_is_recorded(
        self,
    ) -> None:
        guard = make_guard(max_calls=2)

        guard.reserve(
            provider="qwen",
            prompt="review",
        )
        guard.record_failure(
            "qwen",
            RuntimeError("failure"),
        )

        snapshot = guard.snapshot()

        self.assertEqual(
            snapshot["qwen"]["failed_calls"],
            1,
        )
        self.assertFalse(
            snapshot["qwen"]["blocked"]
        )

    def test_snapshot_does_not_store_prompt(
        self,
    ) -> None:
        guard = make_guard()
        secret_text = "do-not-store-this"

        guard.reserve(
            provider="qwen",
            prompt=secret_text,
        )

        self.assertNotIn(
            secret_text,
            str(guard.snapshot()),
        )

    def test_rejects_unknown_provider(
        self,
    ) -> None:
        guard = make_guard()

        with self.assertRaises(ValueError):
            guard.reserve(
                provider="unknown",
                prompt="review",
            )


if __name__ == "__main__":
    unittest.main()