import unittest

from reviewer.consensus import (
    build_consensus,
    build_cross_review_consensus,
    build_review_candidates,
)
from reviewer.schemas import (
    CrossReviewVote,
    Finding,
)

def make_finding(
    provider: str,
    *,
    file: str = "src/example.py",
    line: int = 10,
    category: str = "bug",
    severity: str = "P1",
    message: str = "None 입력에서 예외가 발생합니다.",
    reason: str = "값 검증 없이 속성에 접근합니다.",
    confidence: float = 0.8,
) -> Finding:
    return Finding(
        provider=provider,
        file=file,
        line=line,
        category=category,
        severity=severity,
        message=message,
        reason=reason,
        confidence=confidence,
    )
def make_vote(
    provider: str,
    candidate_id: str,
    *,
    decision: str = "accept",
    confidence: float = 0.8,
) -> CrossReviewVote:
    return CrossReviewVote(
        provider=provider,
        candidate_id=candidate_id,
        decision=decision,
        reason="코드 근거를 확인했습니다.",
        severity="P1",
        message="None 입력에서 예외가 발생합니다.",
        confidence=confidence,
    )

class ConsensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_lines = {
            "src/example.py": frozenset({10, 11, 20})
        }

    def test_accepts_two_models_on_same_line(self) -> None:
        findings = [
            make_finding("qwen"),
            make_finding(
                "gpt",
                message="None 입력 시 예외가 발생합니다.",
                confidence=0.9,
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].match_count, 2)
        self.assertEqual(
            result[0].providers,
            ("qwen", "gpt"),
        )

    def test_accepts_nearby_line(self) -> None:
        findings = [
            make_finding("qwen", line=10),
            make_finding(
                "gemini",
                line=11,
                message="None 입력에서 예외가 발생할 수 있습니다.",
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
            line_tolerance=1,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].match_count, 2)

    def test_rejects_single_model_finding(self) -> None:
        result = build_consensus(
            [make_finding("qwen")],
            self.valid_lines,
        )

        self.assertEqual(result, [])

    def test_rejects_hallucinated_line(self) -> None:
        findings = [
            make_finding("qwen", line=999),
            make_finding("gpt", line=999),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(result, [])

    def test_same_provider_does_not_increase_count(
        self,
    ) -> None:
        findings = [
            make_finding("qwen"),
            make_finding(
                "qwen",
                message="None 입력에서 예외가 발생할 가능성이 있습니다.",
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(result, [])

    def test_different_categories_do_not_match(self) -> None:
        findings = [
            make_finding(
                "qwen",
                category="quality",
            ),
            make_finding(
                "gpt",
                category="bug",
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(result, [])

    def test_unrelated_messages_do_not_match(self) -> None:
        findings = [
            make_finding(
                "qwen",
                message="None 입력에서 예외가 발생합니다.",
            ),
            make_finding(
                "gpt",
                message="반복문에서 사용하지 않는 변수가 있습니다.",
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(result, [])

    def test_three_models_produce_match_count_three(
        self,
    ) -> None:
        findings = [
            make_finding("qwen", confidence=0.7),
            make_finding(
                "gpt",
                message="None 입력 시 예외가 발생합니다.",
                confidence=0.9,
            ),
            make_finding(
                "gemini",
                message="None 입력에서 예외가 발생할 수 있습니다.",
                confidence=0.8,
            ),
        ]

        result = build_consensus(
            findings,
            self.valid_lines,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].match_count, 3)
        self.assertEqual(
            result[0].providers,
            ("qwen", "gpt", "gemini"),
        )
        self.assertAlmostEqual(
            result[0].confidence,
            0.8,
        )

    def test_builds_common_candidate_from_models(
        self,
    ) -> None:
        candidates = build_review_candidates(
            [
                make_finding("qwen"),
                make_finding(
                    "gpt",
                    message=(
                        "None 입력 시 예외가 발생합니다."
                    ),
                ),
                make_finding(
                    "gemini",
                    message=(
                        "None 입력에서 예외가 "
                        "발생할 수 있습니다."
                    ),
                ),
            ]
        )

        self.assertEqual(
            len(candidates),
            1,
        )
        self.assertEqual(
            candidates[0].candidate_id,
            "candidate-001",
        )
        self.assertEqual(
            candidates[0].source_providers,
            ("qwen", "gpt", "gemini"),
        )

    def test_cross_review_accepts_two_votes(
        self,
    ) -> None:
        candidates = build_review_candidates(
            [make_finding("qwen")]
        )
        candidate_id = candidates[0].candidate_id

        result = build_cross_review_consensus(
            candidates,
            [
                make_vote(
                    "qwen",
                    candidate_id,
                    decision="accept",
                ),
                make_vote(
                    "gpt",
                    candidate_id,
                    decision="revise",
                ),
                make_vote(
                    "gemini",
                    candidate_id,
                    decision=(
                        "reject_unsupported"
                    ),
                ),
            ],
        )

        self.assertEqual(
            len(result),
            1,
        )
        self.assertEqual(
            result[0].providers,
            ("qwen", "gpt"),
        )
        self.assertEqual(
            result[0].match_count,
            2,
        )

    def test_cross_review_rejects_two_rejections(
        self,
    ) -> None:
        candidates = build_review_candidates(
            [make_finding("qwen")]
        )
        candidate_id = candidates[0].candidate_id

        result = build_cross_review_consensus(
            candidates,
            [
                make_vote(
                    "qwen",
                    candidate_id,
                    decision="accept",
                ),
                make_vote(
                    "gpt",
                    candidate_id,
                    decision="reject_not_issue",
                ),
                make_vote(
                    "gemini",
                    candidate_id,
                    decision=(
                        "reject_unsupported"
                    ),
                ),
            ],
        )

        self.assertEqual(result, [])

    def test_cross_review_requires_all_models(
        self,
    ) -> None:
        candidates = build_review_candidates(
            [make_finding("qwen")]
        )
        candidate_id = candidates[0].candidate_id

        with self.assertRaises(
            ValueError
        ) as context:
            build_cross_review_consensus(
                candidates,
                [
                    make_vote(
                        "qwen",
                        candidate_id,
                    ),
                    make_vote(
                        "gpt",
                        candidate_id,
                    ),
                ],
            )

        self.assertIn(
            "세 모델 모두 평가하지 않았습니다",
            str(context.exception),
        )

if __name__ == "__main__":
    unittest.main()