import unittest
from typing import Sequence
from reviewer.orchestrator import (
    OrchestrationError,
    ReviewOrchestrator,
)
from reviewer.quota_guard import (
    ProviderBudget,
    QuotaGuard,
)
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
    SchemaError,
)


class FakeRateLimitError(RuntimeError):
    status_code = 429
class FakeServerError(RuntimeError):
    status_code = 503
class FakeProvider:
    def __init__(
        self,
        provider: str,
        findings: list[Finding] | None = None,
        error: Exception | None = None,
        cross_review_error: Exception | None = None,
        cross_review_decision: str = "accept",
    ) -> None:
        self.provider = provider
        self.findings = findings or []
        self.error = error
        self.cross_review_error = (
            cross_review_error
        )
        self.cross_review_decision = (
            cross_review_decision
        )
        self.received_prompts: list[str] = []
        self.received_cross_review_prompts: list[
            str
        ] = []

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

    def cross_review(
        self,
        user_prompt: str,
        *,
        candidate_ids: Sequence[str],
    ) -> list[CrossReviewVote]:
        self.received_cross_review_prompts.append(
            user_prompt
        )

        if self.cross_review_error is not None:
            error = self.cross_review_error
            self.cross_review_error = None
            raise error

        return [
            CrossReviewVote(
                provider=self.provider,
                candidate_id=candidate_id,
                decision=(
                    self.cross_review_decision
                ),
                reason=(
                    "코드 근거가 후보 내용을 "
                    "직접 뒷받침합니다."
                ),
                severity="P1",
                message=(
                    "None 입력에서 예외가 발생합니다."
                ),
                confidence=0.8,
            )
            for candidate_id in candidate_ids
        ]


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
    gemini_calls: int = 6,
) -> QuotaGuard:
    return QuotaGuard(
        {
            "qwen": ProviderBudget(
                max_calls=6,
                max_input_chars=10_000,
            ),
            "gpt": ProviderBudget(
                max_calls=6,
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
            "qwen": "세 모델 공통 Diff",
            "gpt": "세 모델 공통 Diff",
            "gemini": "세 모델 공통 Diff",
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

    def test_runs_two_round_review_with_same_input(
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
            ["세 모델 공통 Diff"],
        )
        self.assertEqual(
            qwen.received_prompts,
            gpt.received_prompts,
        )
        self.assertEqual(
            qwen.received_prompts,
            gemini.received_prompts,
        )

        self.assertEqual(
            len(qwen.received_cross_review_prompts),
            1,
        )
        self.assertEqual(
            qwen.received_cross_review_prompts,
            gpt.received_cross_review_prompts,
        )
        self.assertEqual(
            qwen.received_cross_review_prompts,
            gemini.received_cross_review_prompts,
        )

        self.assertEqual(
            len(result.consensus_findings),
            1,
        )
        self.assertEqual(
            result.consensus_findings[0].providers,
            ("qwen", "gpt", "gemini"),
        )
        self.assertEqual(
            result.consensus_findings[0].match_count,
            3,
        )
        self.assertTrue(
            result.consensus_findings[0].evidence_hash
        )

    def test_rejects_different_first_round_inputs(
        self,
    ) -> None:
        self.prompts = {
            "qwen": "Qwen 입력",
            "gpt": "Cohere 입력",
            "gemini": "Gemini 입력",
        }

        providers = [
            FakeProvider("qwen"),
            FakeProvider("gpt"),
            FakeProvider("gemini"),
        ]

        with self.assertRaises(
            ValueError
        ) as context:
            self.run_orchestrator(
                providers
            )

        self.assertIn(
            "동일한 공통 입력",
            str(context.exception),
        )

    def test_accepts_candidate_with_two_votes(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
                cross_review_decision="accept",
            ),
            FakeProvider(
                "gpt",
                [finding("gpt")],
                cross_review_decision="accept",
            ),
            FakeProvider(
                "gemini",
                [finding("gemini")],
                cross_review_decision=(
                    "reject_unsupported"
                ),
            ),
        ]

        result = self.run_orchestrator(
            providers
        )

        self.assertEqual(
            len(result.consensus_findings),
            1,
        )
        self.assertEqual(
            result.consensus_findings[0].providers,
            ("qwen", "gpt"),
        )
        self.assertEqual(
            result.consensus_findings[0].match_count,
            2,
        )

    def test_rejects_candidate_with_two_rejections(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
                cross_review_decision="accept",
            ),
            FakeProvider(
                "gpt",
                [finding("gpt")],
                cross_review_decision=(
                    "reject_not_issue"
                ),
            ),
            FakeProvider(
                "gemini",
                [finding("gemini")],
                cross_review_decision=(
                    "reject_unsupported"
                ),
            ),
        ]

        result = self.run_orchestrator(
            providers
        )


    def test_stops_when_cross_review_fails(
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
                [finding("gemini")],
                cross_review_error=RuntimeError(
                    "cross review failure"
                ),
            ),
        ]

        with self.assertRaises(
            OrchestrationError
        ) as context:
            self.run_orchestrator(
                providers
            )

        self.assertIn(
            "2차 교차평가",
            str(context.exception),
        )
        self.assertIn(
            "gemini(RuntimeError)",
            str(context.exception),
        )

    def test_retries_gemini_cross_review_once(
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
            cross_review_error=FakeServerError(
                "temporary server failure"
            ),
        )

        result = self.run_orchestrator(
            [qwen, gpt, gemini]
        )

        self.assertEqual(
            len(
                gemini.received_cross_review_prompts
            ),
            2,
        )
        self.assertEqual(
            len(result.consensus_findings),
            1,
        )

    def test_stops_when_one_provider_fails(
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

        with self.assertRaises(
            OrchestrationError
        ) as context:
            self.run_orchestrator(
                providers
            )

        self.assertIn(
            "gemini(RuntimeError)",
            str(context.exception),
        )

    def test_stops_on_response_validation_failure(
        self,
    ) -> None:
        providers = [
            FakeProvider(
                "qwen",
                [finding("qwen")],
            ),
            FakeProvider(
                "gpt",
                error=SchemaError(
                    "finding 필드가 정확하지 않습니다."
                ),
            ),
            FakeProvider(
                "gemini",
                [finding("gemini")],
            ),
        ]

        with self.assertRaises(
            OrchestrationError
        ) as context:
            self.run_orchestrator(
                providers
            )

        self.assertIn(
            (
                "gpt(ResponseValidationError"
                "[cause=SchemaError])"
            ),
            str(context.exception),
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

    def test_filters_invalid_lines_and_reviews_valid_one(
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
            len(result.consensus_findings),
            1,
        )
        self.assertEqual(
            result.consensus_findings[0].line,
            10,
        )



    def test_removes_bad_evidence_and_reviews_valid_one(
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
            len(result.consensus_findings),
            1,
        )
        self.assertEqual(
            result.consensus_findings[0].line,
            10,
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

        with self.assertRaises(
            OrchestrationError
        ) as context:
            self.run_orchestrator(
                providers,
                quota_guard=make_guard(
                    gemini_calls=0
                ),
            )

        self.assertEqual(
            gemini.received_prompts,
            [],
        )
        self.assertIn(
            "gemini(QuotaBlockedError)",
            str(context.exception),
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

        quota_guard = make_guard()

        with self.assertRaises(
            OrchestrationError
        ):
            self.run_orchestrator(
                providers,
                quota_guard=quota_guard,
            )

        quota_snapshot = quota_guard.snapshot()

        self.assertTrue(
            quota_snapshot[
                "gemini"
            ]["blocked"]
        )
        self.assertEqual(
            quota_snapshot[
                "gemini"
            ]["quota_errors"],
            1,
        )


if __name__ == "__main__":
    unittest.main()