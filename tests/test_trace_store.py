import json
import unittest

from reviewer.orchestrator import ReviewRun
from reviewer.schemas import ConsensusFinding
from reviewer.trace_store import TraceStore


def make_result() -> ReviewRun:
    finding = ConsensusFinding(
        file="src/example.py",
        line=10,
        category="bug",
        severity="P1",
        message="None 입력에서 예외가 발생합니다.",
        reason="입력값 검사 없이 속성에 접근합니다.",
        confidence=0.85,
        providers=("qwen", "gpt"),
        match_count=2,
    )

    return ReviewRun(
        consensus_findings=(finding,),
        successful_providers=("gpt", "qwen"),
        provider_failures={
            "gemini": "ProviderError",
        },
        raw_finding_count=3,
        invalid_line_count=1,
    )


class TraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TraceStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_starts_run(self) -> None:
        run_id = self.store.start_run(
            repository="owner/repository",
            pull_request_number=7,
        )

        saved = self.store.get_run(run_id)

        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "running")
        self.assertEqual(
            saved["pull_request_number"],
            7,
        )

    def test_completes_run(self) -> None:
        run_id = self.store.start_run(
            repository="owner/repository",
            pull_request_number=7,
        )

        self.store.complete_run(
            run_id=run_id,
            result=make_result(),
        )

        saved = self.store.get_run(run_id)

        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["raw_finding_count"], 3)
        self.assertEqual(saved["invalid_line_count"], 1)
        self.assertEqual(saved["consensus_count"], 1)
        self.assertEqual(
            json.loads(saved["successful_providers"]),
            ["gpt", "qwen"],
        )

    def test_saves_consensus_findings(self) -> None:
        run_id = self.store.start_run(
            repository="owner/repository",
            pull_request_number=7,
        )

        self.store.complete_run(
            run_id=run_id,
            result=make_result(),
        )

        findings = self.store.get_consensus_findings(
            run_id
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0]["file_name"],
            "src/example.py",
        )
        self.assertEqual(
            findings[0]["line_number"],
            10,
        )
        self.assertEqual(
            json.loads(findings[0]["providers"]),
            ["qwen", "gpt"],
        )

    def test_records_only_error_type(self) -> None:
        run_id = self.store.start_run(
            repository="owner/repository",
            pull_request_number=7,
        )

        secret_text = "do-not-store-this-value"
        error = RuntimeError(secret_text)

        self.store.fail_run(
            run_id=run_id,
            error=error,
        )

        saved = self.store.get_run(run_id)

        self.assertEqual(saved["status"], "failed")
        self.assertEqual(
            saved["error_type"],
            "RuntimeError",
        )
        self.assertNotIn(
            secret_text,
            json.dumps(saved),
        )

    def test_records_security_block_status(self) -> None:
        run_id = self.store.start_run(
            repository="owner/repository",
            pull_request_number=7,
        )

        self.store.fail_run(
            run_id=run_id,
            status="blocked_sensitive_content",
            error=RuntimeError("blocked"),
        )

        saved = self.store.get_run(run_id)

        self.assertEqual(
            saved["status"],
            "blocked_sensitive_content",
        )


if __name__ == "__main__":
    unittest.main()