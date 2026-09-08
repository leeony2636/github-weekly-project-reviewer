import os
import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from typing import cast
from reviewer.config import RuntimeConfig
from reviewer.github_service import PullRequestFile
from reviewer.orchestrator import (
    OrchestrationError,
    ReviewRun,
)
from reviewer.review_selector import (
    ReviewSelection,
    select_review_inputs,
)
from reviewer.schemas import ConsensusFinding
from reviewer.trace_store import TraceStore
from reviewer.weekly_service import (
    WeeklyCommit,
    WeeklyReviewInput,
)
from scripts.weekly_cross_review import (
    empty_review_result,
    find_existing_issue,
    get_cached_issue_body,
    make_failure_issue_body,
    make_failure_output_payload,
    make_issue_body,
    make_issue_title,
    make_output_payload,
    make_weekly_cache_key,
    publish_issue,
    run_weekly_review,
)

NOW = datetime(
    2026,
    9,
    8,
    tzinfo=timezone.utc,
)


def make_weekly_input(
    *,
    with_files: bool = True,
) -> WeeklyReviewInput:
    files = (
        (
            PullRequestFile(
                filename="src/example.py",
                status="modified",
                additions=1,
                deletions=0,
                patch=(
                    "@@ -0,0 +1 @@\n"
                    "+value = 1"
                ),
            ),
        )
        if with_files
        else ()
    )

    return WeeklyReviewInput(
        repository="owner/repository",
        since=NOW - timedelta(days=7),
        until=NOW,
        base_sha="base-sha",
        head_sha="head-sha",
        commits=(
            WeeklyCommit(
                sha="abcdef1234567890",
                message="기능 개선",
                url=(
                    "https://example.invalid/"
                    "commit/abcdef"
                ),
                committed_at=NOW,
            ),
        ),
        pull_requests=(),
        files=files,
        valid_lines=(
            {"src/example.py": frozenset({1})}
            if with_files
            else {}
        ),
        changed_lines=(
            {"src/example.py": {1: "value = 1"}}
            if with_files
            else {}
        ),
        diff_sha256="a" * 64,
    )


def make_result() -> ReviewRun:
    finding = ConsensusFinding(
        file="src/example.py",
        line=1,
        category="bug",
        severity="P1",
        message="값 검사가 필요합니다.",
        reason="잘못된 값이 저장될 수 있습니다.",
        evidence="value = 1",
        confidence=0.9,
        providers=("gpt", "qwen"),
        match_count=2,
    )

    return ReviewRun(
        consensus_findings=(finding,),
        successful_providers=(
            "gemini",
            "gpt",
            "qwen",
        ),
        provider_failures={},
        raw_finding_count=3,
        invalid_line_count=0,
        invalid_evidence_count=0,
        quota_snapshot={},
    )


class FakeRepository:
    def __init__(self, issues=None) -> None:
        self.issues = issues or []
        self.created = []

    def get_issues(
        self,
        *,
        state,
        sort,
        direction,
    ):
        return self.issues

    def create_issue(
        self,
        *,
        title,
        body,
    ):
        self.created.append(
            {
                "title": title,
                "body": body,
            }
        )
        return SimpleNamespace(
            html_url=(
                "https://example.invalid/issues/1"
            )
        )

class FakeWeeklyService:
    def __init__(
        self,
        review_input: WeeklyReviewInput,
    ) -> None:
        self.review_input = review_input
        self.repository = FakeRepository()
        self.closed = False

    def collect_weekly_review(
        self,
        *,
        since,
        until,
    ) -> WeeklyReviewInput:
        return self.review_input

    def close(self) -> None:
        self.closed = True


def make_config(
    *,
    cache_enabled: bool,
) -> RuntimeConfig:
    return cast(
        RuntimeConfig,
        SimpleNamespace(
            target_github_token="test-github-token",
            target_repo="owner/repository",
            hf_token="test-hf-token",
            cohere_api_key="test-cohere-key",
            gemini_api_key="test-gemini-key",
            qwen_model="qwen-model",
            gpt_model="gpt-model",
            gemini_model="gemini-model",
            gpt_review_percent=20,
            gemini_review_percent=10,
            qwen_input_char_limit=4_800,
            cloud_input_char_limit=4_800,
            line_tolerance=1,
            min_match_count=2,
            cache_enabled=cache_enabled,
            degraded_mode_on_quota=True,
        ),
    )


class FailingOrchestrator:
    def run(self, **kwargs):
        raise OrchestrationError(
            "모델 수 부족"
        )


class WeeklyCrossReviewTests(unittest.TestCase):
    def test_makes_korean_issue_title(
        self,
    ) -> None:
        self.assertEqual(
            make_issue_title(NOW),
            "주간 프로젝트 리뷰 - 2026-09-08",
        )

    def test_report_contains_consensus(
        self,
    ) -> None:
        review_input = make_weekly_input()
        selection = ReviewSelection(
            provider_prompts={
                "qwen": "prompt",
                "gpt": "prompt",
                "gemini": "prompt",
            },
            selected_files={
                "qwen": ("src/example.py",),
                "gpt": ("src/example.py",),
                "gemini": (),
            },
        )

        body = make_issue_body(
            review_input=review_input,
            result=make_result(),
            selection=selection,
        )

        self.assertIn(
            "# 주간 프로젝트 리뷰",
            body,
        )
        self.assertIn(
            "src/example.py:1",
            body,
        )
        self.assertIn(
            "gpt, qwen",
            body,
        )
        self.assertIn(
            "value = 1",
            body,
        )

    def test_no_changes_skips_models_in_report(
        self,
    ) -> None:
        body = make_issue_body(
            review_input=make_weekly_input(
                with_files=False
            ),
            result=empty_review_result(),
            selection=None,
        )

        self.assertIn(
            "모델 호출을 생략했습니다",
            body,
        )
        self.assertIn(
            "변경 파일: 0개",
            body,
        )

    def test_finds_existing_issue(
        self,
    ) -> None:
        expected = SimpleNamespace(
            title=(
                "주간 프로젝트 리뷰 - 2026-09-08"
            ),
            pull_request=None,
        )
        repository = FakeRepository(
            issues=[expected]
        )

        result = find_existing_issue(
            repository,
            expected.title,
        )

        self.assertIs(result, expected)

    def test_publishes_new_issue(
        self,
    ) -> None:
        repository = FakeRepository()

        url = publish_issue(
            repository=repository,
            title="주간 프로젝트 리뷰",
            body="보고서",
        )

        self.assertEqual(
            len(repository.created),
            1,
        )
        self.assertEqual(
            repository.created[0]["body"],
            "보고서",
        )
        self.assertEqual(
            url,
            "https://example.invalid/issues/1",
        )

    def test_output_marks_preview_state(
        self,
    ) -> None:
        payload = make_output_payload(
            issue_title="주간 프로젝트 리뷰",
            issue_body="보고서",
            review_input=make_weekly_input(),
            result=make_result(),
            selection=None,
            published=False,
        )

        self.assertEqual(
            payload["review_type"],
            "weekly",
        )
        issue = payload["issue"]
        self.assertIsInstance(issue, dict)
        assert isinstance(issue, dict)
        self.assertFalse(issue["published"])

    def test_weekly_cache_key_is_stable(
        self,
    ) -> None:
        config = make_config(
            cache_enabled=True,
        )
        selection = ReviewSelection(
            provider_prompts={
                "qwen": "qwen prompt",
                "gpt": "gpt prompt",
                "gemini": "gemini prompt",
            },
            selected_files={
                "qwen": ("src/example.py",),
                "gpt": ("src/example.py",),
                "gemini": (),
            },
        )
        review_input = make_weekly_input()

        first = make_weekly_cache_key(
            config=config,
            selection=selection,
            review_input=review_input,
        )
        second = make_weekly_cache_key(
            config=config,
            selection=selection,
            review_input=review_input,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_reads_only_weekly_cached_body(
        self,
    ) -> None:
        payload = {
            "review_type": "weekly",
            "issue": {
                "body": "저장된 주간 보고서",
            },
        }

        self.assertEqual(
            get_cached_issue_body(payload),
            "저장된 주간 보고서",
        )
        self.assertIsNone(
            get_cached_issue_body(
                {
                    "review_type": "pull_request",
                    "issue": {
                        "body": "PR 보고서",
                    },
                }
            )
        )

    def test_failure_report_excludes_error_message(
        self,
    ) -> None:
        secret_message = (
            "secret-token-must-not-appear"
        )
        body = make_failure_issue_body(
            repository="owner/repository",
            since=NOW - timedelta(days=7),
            until=NOW,
            error_type="QuotaBlockedError",
        )
        payload = make_failure_output_payload(
            repository="owner/repository",
            since=NOW - timedelta(days=7),
            until=NOW,
            issue_title="주간 프로젝트 리뷰",
            issue_body=body,
            error_type="QuotaBlockedError",
            published=False,
        )

        self.assertIn(
            "검토 미완료",
            body,
        )
        self.assertNotIn(
            secret_message,
            body,
        )
        self.assertEqual(
            payload["status"],
            "failed",
        )
        issue = payload["issue"]
        self.assertIsInstance(issue, dict)
        assert isinstance(issue, dict)
        self.assertFalse(issue["published"])

    def test_cached_result_skips_model_call(
        self,
    ) -> None:
        review_input = make_weekly_input()
        config = make_config(
            cache_enabled=True
        )
        selection = select_review_inputs(
            review_input
        )
        cache_key = make_weekly_cache_key(
            config=config,
            selection=selection,
            review_input=review_input,
        )
        service = FakeWeeklyService(
            review_input
        )

        with TraceStore(":memory:") as store:
            store.save_cached_payload(
                cache_key=cache_key,
                payload={
                    "review_type": "weekly",
                    "issue": {
                        "title": "저장된 보고서",
                        "body": "저장된 주간 보고서",
                        "published": False,
                    },
                },
            )

            with TemporaryDirectory() as directory:
                output_path = os.path.join(
                    directory,
                    "weekly.json",
                )

                with (
                    patch.dict(
                        os.environ,
                        {
                            "WEEKLY_REVIEW_OUTPUT_PATH": (
                                output_path
                            )
                        },
                    ),
                    patch(
                        "scripts.weekly_cross_review."
                        "WeeklyGitHubService.connect",
                        return_value=service,
                    ),
                    patch(
                        "scripts.weekly_cross_review."
                        "create_orchestrator"
                    ) as create_mock,
                ):
                    exit_code = run_weekly_review(
                        config=config,
                        trace_store=store,
                        until=NOW,
                        days=7,
                        publish=False,
                    )

        self.assertEqual(exit_code, 0)
        create_mock.assert_not_called()
        self.assertTrue(service.closed)

    def test_model_failure_creates_safe_issue(
        self,
    ) -> None:
        review_input = make_weekly_input()
        config = make_config(
            cache_enabled=False
        )
        service = FakeWeeklyService(
            review_input
        )

        with TraceStore(":memory:") as store:
            with TemporaryDirectory() as directory:
                output_path = os.path.join(
                    directory,
                    "weekly.json",
                )

                with (
                    patch.dict(
                        os.environ,
                        {
                            "WEEKLY_REVIEW_OUTPUT_PATH": (
                                output_path
                            )
                        },
                    ),
                    patch(
                        "scripts.weekly_cross_review."
                        "WeeklyGitHubService.connect",
                        return_value=service,
                    ),
                    patch(
                        "scripts.weekly_cross_review."
                        "create_orchestrator",
                        return_value=(
                            FailingOrchestrator()
                        ),
                    ),
                ):
                    exit_code = run_weekly_review(
                        config=config,
                        trace_store=store,
                        until=NOW,
                        days=7,
                        publish=True,
                    )

        self.assertEqual(exit_code, 4)
        self.assertEqual(
            len(service.repository.created),
            1,
        )
        self.assertIn(
            "검토 미완료",
            service.repository.created[0][
                "body"
            ],
        )
        self.assertTrue(service.closed)


if __name__ == "__main__":
    unittest.main()