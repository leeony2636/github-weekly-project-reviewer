import json
import unittest

from reviewer.github_service import (
    GitHubService,
    IncompletePullRequestDiff,
)
from reviewer.security import SensitiveContentError


class FakeReference:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class FakeFile:
    def __init__(
        self,
        *,
        filename: str = "src/example.py",
        status: str = "modified",
        additions: int = 1,
        deletions: int = 1,
        patch: str | None = (
            "@@ -1 +1 @@\n"
            "-old_value = 1\n"
            "+new_value = 2"
        ),
    ) -> None:
        self.filename = filename
        self.status = status
        self.additions = additions
        self.deletions = deletions
        self.patch = patch


class FakePullRequest:
    def __init__(
        self,
        *,
        files: list[FakeFile],
        title: str = "입력 검증 개선",
    ) -> None:
        self.title = title
        self.html_url = "https://example.invalid/pull/1"
        self.base = FakeReference("base-sha")
        self.head = FakeReference("head-sha")
        self._files = files

    def get_files(self) -> list[FakeFile]:
        return self._files


class FakeRepository:
    def __init__(
        self,
        pull_request: FakePullRequest,
    ) -> None:
        self.pull_request = pull_request
        self.requested_number: int | None = None

    def get_pull(
        self,
        number: int,
    ) -> FakePullRequest:
        self.requested_number = number
        return self.pull_request


def make_service(
    *,
    files: list[FakeFile],
    title: str = "입력 검증 개선",
) -> GitHubService:
    pull_request = FakePullRequest(
        files=files,
        title=title,
    )
    repository = FakeRepository(pull_request)

    return GitHubService(
        repository=repository,
        target_repo="owner/repository",
    )


class GitHubServiceTests(unittest.TestCase):
    def test_collects_pr_and_valid_lines(self) -> None:
        service = make_service(
            files=[FakeFile()]
        )

        result = service.collect_pull_request(1)

        self.assertEqual(result.number, 1)
        self.assertEqual(
            result.repository,
            "owner/repository",
        )
        self.assertEqual(
            result.valid_lines["src/example.py"],
            frozenset({1}),
        )

    def test_builds_json_prompt(self) -> None:
        service = make_service(
            files=[FakeFile()]
        )
        review_input = service.collect_pull_request(1)

        prompt = review_input.build_model_prompt()
        payload = json.loads(prompt)

        self.assertEqual(
            payload["pull_request"]["number"],
            1,
        )
        self.assertEqual(
            payload["valid_lines"]["src/example.py"],
            [1],
        )
        self.assertEqual(
            payload["files"][0]["filename"],
            "src/example.py",
        )
        self.assertEqual(
            payload["pull_request"]["diff_sha256"],
            review_input.diff_sha256,
        )

    def test_rejects_missing_patch_with_additions(
        self,
    ) -> None:
        service = make_service(
            files=[
                FakeFile(
                    additions=1,
                    patch=None,
                )
            ]
        )

        with self.assertRaises(
            IncompletePullRequestDiff
        ):
            service.collect_pull_request(1)

    def test_rejects_truncated_patch(self) -> None:
        service = make_service(
            files=[
                FakeFile(
                    additions=1,
                    deletions=1,
                    patch=(
                        "@@ -1,2 +1,2 @@\n"
                        "-old_value\n"
                        "+new_value"
                    ),
                )
            ]
        )

        with self.assertRaises(
            IncompletePullRequestDiff
        ):
            service.collect_pull_request(1)

    def test_blocks_sensitive_diff(self) -> None:
        service = make_service(
            files=[
                FakeFile(
                    patch=(
                        "@@ -1 +1 @@\n"
                        "-token = None\n"
                        "+token = "
                        "'ghp_abcdefghijklmnopqrstuvwxyz1234'"
                    )
                )
            ]
        )

        with self.assertRaises(
            SensitiveContentError
        ):
            service.collect_pull_request(1)

    def test_blocks_personal_information_in_title(
        self,
    ) -> None:
        service = make_service(
            files=[FakeFile()],
            title="Contact user@example.com",
        )

        with self.assertRaises(
            SensitiveContentError
        ):
            service.collect_pull_request(1)

    def test_rejects_excessive_file_count(self) -> None:
        service = make_service(
            files=[FakeFile(), FakeFile()]
        )
        service.max_files = 1

        with self.assertRaises(
            IncompletePullRequestDiff
        ):
            service.collect_pull_request(1)

    def test_rejects_oversized_prompt(self) -> None:
        service = make_service(
            files=[FakeFile()]
        )
        review_input = service.collect_pull_request(1)

        with self.assertRaises(
            IncompletePullRequestDiff
        ):
            review_input.build_model_prompt(
                max_prompt_chars=10
            )
    def test_preserves_changed_line_content(
        self,
    ) -> None:
        service = make_service(
            files=[FakeFile()]
        )

        result = service.collect_pull_request(1)

        self.assertEqual(
            result.changed_lines[
                "src/example.py"
            ][1],
            "new_value = 2",
        )


    def test_creates_deterministic_diff_hash(
        self,
    ) -> None:
        first = make_service(
            files=[FakeFile()]
        ).collect_pull_request(1)

        second = make_service(
            files=[FakeFile()]
        ).collect_pull_request(1)

        self.assertEqual(
            first.diff_sha256,
            second.diff_sha256,
        )
        self.assertEqual(
            len(first.diff_sha256),
            64,
        )

if __name__ == "__main__":
    unittest.main()