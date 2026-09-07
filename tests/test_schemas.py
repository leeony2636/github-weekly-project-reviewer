import json
import unittest

from reviewer.schemas import (
    SchemaError,
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


if __name__ == "__main__":
    unittest.main()