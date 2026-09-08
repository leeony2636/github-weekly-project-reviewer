import unittest

from reviewer.orchestrator import (
    OrchestrationError,
    ReviewOrchestrator,
)
from reviewer.quota_guard import (
    ProviderBudget,
    QuotaGuard,
)
from reviewer.schemas import Finding


class FakeRateLimitError(RuntimeError):
    status_code = 429


class FakeProvider:
    def __init__(
        self,
        provider: str,
        findings: list[Finding] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.provider = provider
        self.findings = findings or []
        self.error = error
        self.received_prompts: list[str] = []

    def review(
        self,
        user_prompt: str,
    ) -> list[Finding]:
        self.received_prompts.append(
            user_prompt
        )

        if self.error is not None:
            raise self.error

        return self.findings


def finding(
    provider: str,
    *,
    line: int = 10,
    evidence: str = "value = source.value",
) -> Finding:
    return Finding(
        provider=provider,
        file="src/example.py",
        line=line,
        category="bug",
        severity="P1",
        message=(
            "None 입력에서 예외가 발생합니다."
        ),
        reason=(
            "입력 검증 없이 속성에 접근합니다."
        ),
        confidence=0.8,
        evidence=evidence,
    )


def make_guard(
    *,
    gemini_calls: int = 1,
) -> QuotaGuard:
    return QuotaGuard(
        {
            "qwen": ProviderBudget(
                max_calls=1,
                max_input_chars=10_000,
            ),
            "gpt": ProviderBudget(
                max_calls=1,
                max_input_chars=10_000,
            ),
            "gemini": ProviderBudget(
                max_calls=gemini_calls,
                max_input_chars=10_000,
            ),
        }
    )


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_lines = {
            "src/example.py": frozenset(
                {10, 11}
            )
        }
        self.changed_lines = {
            "src/example.py": {
                10: "value = source.value",
                11: "value = source.value",
            }
        }
        self.prompts = {
            "qwen": "Qwen 전체 Diff",
            "gpt": "GPT 선별 Diff",
            "gemini": "Gemini 선별 Diff",
        }

    def run_orchestrator(
        self,
        providers: list[FakeProvider],
        *,
        quota_guard: QuotaGuard | None = None,
    ):
        return ReviewOrchestrator(
            providers,
            quota_guard=(
                quota_guard or make_guard()
            ),
        ).run(
            provider_prompts=self.prompts,
            valid_lines=self.valid_lines,
            changed_lines=self.changed_lines,
        )

    def test_sends_provider_specific_prompts(
        self,
    ) -> None:
        qwen = FakeProvider(
            "qwen",
            [finding("qwen")],
        )
        gpt = FakeProvider(
            "gpt",
            [finding("gpt")],
        )
        gemini = FakeProvider(
            "gemini",
            [finding("gemini")],
        )

        result = self.run_orchestrator(
            [qwen, gpt, gemini]
        )

        self.assertEqual(
            qwen.received_prompts,
            ["Qwen 전체 Diff"],
        )
        self.assertEqual(
            gpt.received_prompts,
            ["GPT 선별 Diff"],
        )
        self.assertEqual(
            gemini.received_prompts,
            ["Gemini 선별 Diff"],
        )
        self.assertEqual(
            result.consensus_findings[0]
            .match_count,
            3,
        )
        self.assertTrue(
            result.consensus_findings[0]
            .evidence_hash
        )

    def test_continues_when_one_provider_fails(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
            ),
            FakeProvider(
                "gpt",
                [finding("gpt")],
            ),
            FakeProvider(
                "gemini",
                error=RuntimeError("failure"),
            ),
        ]

        result = self.run_orchestrator(
            providers
        )

        self.assertEqual(
            result.successful_providers,
            ("gpt", "qwen"),
        )
        self.assertEqual(
            result.provider_failures,
            {"gemini": "RuntimeError"},
        )

    def test_stops_when_two_providers_fail(
        self,
    ) -> None:
        wrapped_error = RuntimeError(
            "provider failed"
        )
        wrapped_error.__cause__ = (
            FakeRateLimitError("limited")
        )

        providers = [
            FakeProvider(
                "qwen",
                findings=[],
            ),
            FakeProvider(
                "gpt",
                error=wrapped_error,
            ),
            FakeProvider(
                "gemini",
                error=RuntimeError("failed"),
            ),
        ]

        with self.assertRaises(
            OrchestrationError
        ) as context:
            self.run_orchestrator(
                providers
            )

        message = str(context.exception)

        self.assertIn(
            "gpt(FakeRateLimitError[status=429])",
            message,
        )
        self.assertIn(
            "gemini(RuntimeError)",
            message,
        )

    def test_filters_invalid_model_line(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen", line=999)],
            ),
            FakeProvider(
                "gpt",
                [finding("gpt", line=999)],
            ),
            FakeProvider(
                "gemini",
                [finding("gemini")],
            ),
        ]

        result = self.run_orchestrator(
            providers
        )

        self.assertEqual(
            result.invalid_line_count,
            2,
        )
        self.assertEqual(
            result.consensus_findings,
            (),
        )

    def test_rejects_incorrect_evidence(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
            ),
           FakeProvider(
                "gpt",
                [
                    finding(
                        "gpt",
                        evidence="invented = True",
                    )
                ],
            ),
            FakeProvider(
                "gemini",
                [],
            ),
        ]

        result = self.run_orchestrator(
            providers
        )

        self.assertEqual(
            result.invalid_evidence_count,
            1,
        )
        self.assertEqual(
            result.consensus_findings,
            (),
        )

    def test_quota_blocks_disabled_provider(
        self,
    ) -> None:
        gemini = FakeProvider(
            "gemini",
            [finding("gemini")],
        )

        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
            ),
            FakeProvider(
                "gpt",
                [finding("gpt")],
            ),
            gemini,
        ]

        result = self.run_orchestrator(
            providers,
            quota_guard=make_guard(
                gemini_calls=0
            ),
        )

        self.assertEqual(
            gemini.received_prompts,
            [],
        )
        self.assertEqual(
            result.provider_failures,
            {
                "gemini": "QuotaBlockedError"
            },
        )

    def test_429_blocks_provider(self) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
            ),
            FakeProvider(
                "gpt",
                [finding("gpt")],
            ),
            FakeProvider(
                "gemini",
                error=FakeRateLimitError(
                    "limited"
                ),
            ),
        ]

        result = self.run_orchestrator(
            providers
        )

        self.assertTrue(
            result.quota_snapshot[
                "gemini"
            ]["blocked"]
        )
        self.assertEqual(
            result.quota_snapshot[
                "gemini"
            ]["quota_errors"],
            1,
        )


if __name__ == "__main__":
    unittest.main()