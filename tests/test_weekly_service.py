import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from reviewer.github_service import (
    IncompletePullRequestDiff,
)
from reviewer.security import SensitiveContentError
from reviewer.weekly_service import (
    WeeklyCollectionLimitError,
    WeeklyGitHubService,
)


START = datetime(
    2026,
    9,
    1,
    tzinfo=timezone.utc,
)
END = START + timedelta(days=7)


def make_commit(
    sha: str,
    message: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        sha=sha,
        html_url=f"https://example.invalid/commit/{sha}",
        parents=[SimpleNamespace(sha="parent-sha")],
        commit=SimpleNamespace(
            message=message,
            author=SimpleNamespace(date=START),
        ),
    )


def make_file(
    *,
    patch: str | None = (
        "@@ -1 +1 @@\n"
        "-old_value = 1\n"
        "+new_value = 2"
    ),
    additions: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        filename="src/example.py",
        status="modified",
        additions=additions,
        deletions=1,
        patch=patch,
    )


class FakeRepository:
    default_branch = "main"

    def __init__(
        self,
        *,
        recent_commits=None,
        pull_requests=None,
        files=None,
    ) -> None:
        self.recent_commits = (
            recent_commits
            if recent_commits is not None
            else [make_commit("head-sha", "정상 변경")]
        )
        self.pull_requests = (
            pull_requests
            if pull_requests is not None
            else []
        )
        self.files = (
            files
            if files is not None
            else [make_file()]
        )

    def get_commits(
        self,
        *,
        sha,
        since=None,
        until=None,
    ):
        if since is not None:
            return self.recent_commits

        if until == END:
            return [make_commit("head-sha", "head")]

        if until == START:
            return [make_commit("base-sha", "base")]

        return []

    def get_pulls(
        self,
        *,
        state,
        sort,
        direction,
    ):
        return self.pull_requests

    def compare(
        self,
        base_sha,
        head_sha,
    ):
        return SimpleNamespace(files=self.files)


def make_service(
    repository: FakeRepository,
    **limits,
) -> WeeklyGitHubService:
    return WeeklyGitHubService(
        repository=repository,
        target_repo="owner/repository",
        **limits,
    )


class WeeklyGitHubServiceTests(
    unittest.TestCase
):
    def test_collects_weekly_diff_and_valid_lines(
        self,
    ) -> None:
        repository = FakeRepository()

        result = make_service(
            repository
        ).collect_weekly_review(
            since=START,
            until=END,
        )

        self.assertEqual(
            result.repository,
            "owner/repository",
        )
        self.assertEqual(
            result.base_sha,
            "base-sha",
        )
        self.assertEqual(
            result.head_sha,
            "head-sha",
        )
        self.assertEqual(
            len(result.commits),
            1,
        )
        self.assertEqual(
            len(result.files),
            1,
        )
        self.assertEqual(
            result.valid_lines["src/example.py"],
            frozenset({1}),
        )
        self.assertEqual(
            result.changed_lines[
                "src/example.py"
            ][1],
            "new_value = 2",
        )

    def test_collects_recent_pull_request(
        self,
    ) -> None:
        pull_request = SimpleNamespace(
            number=7,
            title="설정 개선",
            html_url=(
                "https://example.invalid/pull/7"
            ),
            state="closed",
            updated_at=END - timedelta(days=1),
            merged_at=END - timedelta(days=2),
        )
        repository = FakeRepository(
            pull_requests=[pull_request]
        )

        result = make_service(
            repository
        ).collect_weekly_review(
            since=START,
            until=END,
        )

        self.assertEqual(
            len(result.pull_requests),
            1,
        )
        self.assertEqual(
            result.pull_requests[0].number,
            7,
        )

    def test_blocks_sensitive_diff(
        self,
    ) -> None:
        repository = FakeRepository(
            files=[
                make_file(
                    patch=(
                        "@@ -0,0 +1 @@\n"
                        "+api_key = "
                        "'abcdefghijklmnop123456'"
                    ),
                )
            ]
        )

        with self.assertRaises(
            SensitiveContentError
        ):
            make_service(
                repository
            ).collect_weekly_review(
                since=START,
                until=END,
            )

    def test_rejects_missing_patch(
        self,
    ) -> None:
        repository = FakeRepository(
            files=[
                make_file(
                    patch=None,
                    additions=1,
                )
            ]
        )

        with self.assertRaises(
            IncompletePullRequestDiff
        ):
            make_service(
                repository
            ).collect_weekly_review(
                since=START,
                until=END,
            )

    def test_stops_when_commit_limit_exceeded(
        self,
    ) -> None:
        repository = FakeRepository(
            recent_commits=[
                make_commit("one", "첫 번째"),
                make_commit("two", "두 번째"),
            ]
        )

        with self.assertRaises(
            WeeklyCollectionLimitError
        ):
            make_service(
                repository,
                max_commits=1,
            ).collect_weekly_review(
                since=START,
                until=END,
            )


if __name__ == "__main__":
    unittest.main()