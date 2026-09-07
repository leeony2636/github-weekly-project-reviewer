import unittest

from reviewer.consensus import build_consensus
from reviewer.schemas import Finding


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


if __name__ == "__main__":
    unittest.main()