import json
import unittest

from reviewer.schemas import (
    SchemaError,
    build_cross_review_response_schema,
    parse_cross_review_response,
    parse_model_response,
)

def valid_payload() -> dict[str, object]:
    return {
        "file": "src/example.py",
        "line": 10,
        "category": "bug",
        "severity": "P1",
        "message": "None 입력에서 예외가 발생합니다.",
        "reason": "입력 검증 없이 속성에 접근합니다.",
        "confidence": 0.85,
        "evidence": "value = source.value",
    }

def valid_cross_review_vote(
    candidate_id: str = "candidate-001",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "decision": "accept",
        "reason": "코드 근거를 확인했습니다.",
        "severity": "P1",
        "message": "None 입력에서 예외가 발생합니다.",
        "confidence": 0.85,
    }
class SchemaTests(unittest.TestCase):
    def test_parses_evidence(self) -> None:
        content = json.dumps(
            {"findings": [valid_payload()]},
            ensure_ascii=False,
        )

        findings = parse_model_response(
            content,
            provider="qwen",
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0].evidence,
            "value = source.value",
        )
        self.assertEqual(
            findings[0].evidence_hash,
            "",
        )

    def test_rejects_missing_evidence(self) -> None:
        payload = valid_payload()
        del payload["evidence"]

        with self.assertRaises(SchemaError):
            parse_model_response(
                json.dumps(payload),
                provider="qwen",
            )

    def test_rejects_extra_model_field(self) -> None:
        payload = valid_payload()
        payload["evidence_hash"] = "model-created-hash"

        with self.assertRaises(SchemaError):
            parse_model_response(
                json.dumps(
                    {"findings": [payload]}
                ),
                provider="qwen",
            )

    def test_rejects_absolute_path(self) -> None:
        payload = valid_payload()
        payload["file"] = "C:/secret/file.py"

        with self.assertRaises(SchemaError):
            parse_model_response(
                json.dumps(
                    {"findings": [payload]}
                ),
                provider="gpt",
            )

    def test_rejects_markdown_wrapper(self) -> None:
        content = (
            "```json\n"
            '{"findings":[]}'
            "\n```"
        )

        with self.assertRaises(SchemaError):
            parse_model_response(
                content,
                provider="gemini",
            )

    def test_rejects_unknown_provider(self) -> None:
        with self.assertRaises(SchemaError):
            parse_model_response(
                '{"findings":[]}',
                provider="unknown",
            )

    def test_rejects_excessive_findings(self) -> None:
        content = json.dumps(
            {
                "findings": [
                    valid_payload(),
                    valid_payload(),
                ]
            }
        )

        with self.assertRaises(SchemaError):
            parse_model_response(
                content,
                provider="qwen",
                max_findings=1,
            )

    def test_builds_cross_review_schema(
        self,
    ) -> None:
        schema = build_cross_review_response_schema(
            ["candidate-001"]
        )

        vote_schema = (
            schema["properties"]["votes"]
            ["items"]
        )

        self.assertFalse(
            vote_schema["additionalProperties"]
        )
        self.assertEqual(
            vote_schema["properties"]
            ["candidate_id"]["enum"],
            ["candidate-001"],
        )

    def test_parses_cross_review_response(
        self,
    ) -> None:
        content = json.dumps(
            {
                "votes": [
                    valid_cross_review_vote()
                ]
            },
            ensure_ascii=False,
        )

        votes = parse_cross_review_response(
            content,
            provider="qwen",
            expected_candidate_ids=(
                "candidate-001",
            ),
        )

        self.assertEqual(len(votes), 1)
        self.assertEqual(
            votes[0].candidate_id,
            "candidate-001",
        )
        self.assertEqual(
            votes[0].decision,
            "accept",
        )

    def test_rejects_missing_cross_review_vote(
        self,
    ) -> None:
        content = json.dumps(
            {
                "votes": [
                    valid_cross_review_vote(
                        "candidate-001"
                    )
                ]
            }
        )

        with self.assertRaises(SchemaError):
            parse_cross_review_response(
                content,
                provider="gpt",
                expected_candidate_ids=(
                    "candidate-001",
                    "candidate-002",
                ),
            )

    def test_rejects_duplicate_cross_review_vote(
        self,
    ) -> None:
        vote = valid_cross_review_vote()

        content = json.dumps(
            {
                "votes": [
                    vote,
                    vote,
                ]
            }
        )

        with self.assertRaises(SchemaError):
            parse_cross_review_response(
                content,
                provider="gemini",
                expected_candidate_ids=(
                    "candidate-001",
                ),
            )

    def test_rejects_extra_cross_review_field(
        self,
    ) -> None:
        vote = valid_cross_review_vote()
        vote["unexpected"] = True

        content = json.dumps(
            {"votes": [vote]}
        )

        with self.assertRaises(SchemaError):
            parse_cross_review_response(
                content,
                provider="qwen",
                expected_candidate_ids=(
                    "candidate-001",
                ),
            )

if __name__ == "__main__":
    unittest.main()