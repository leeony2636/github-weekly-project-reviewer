import json
import unittest
from datetime import datetime, timedelta, timezone

from reviewer.github_service import (
    PullRequestFile,
    PullRequestReviewInput,
)
from reviewer.review_selector import (
    ReviewSelectionError,
    select_review_inputs,
)
from reviewer.weekly_service import (
    WeeklyCommit,
    WeeklyReviewInput,
)


def make_file(
    index: int,
    *,
    filename: str | None = None,
) -> PullRequestFile:
    name = filename or f"src/file_{index}.py"

    return PullRequestFile(
        filename=name,
        status="modified",
        additions=1,
        deletions=0,
        patch=(
            "@@ -0,0 +1 @@\n"
            f"+value_{index} = {index}"
        ),
    )


def make_files() -> tuple[PullRequestFile, ...]:
    return (
        make_file(
            0,
            filename="src/auth_service.py",
        ),
        make_file(
            1,
            filename="requirements.txt",
        ),
        make_file(2),
        make_file(3),
        make_file(4),
        make_file(5),
        make_file(6),
        make_file(7),
        make_file(8),
        make_file(9),
    )


def make_line_indexes(
    files: tuple[PullRequestFile, ...],
) -> tuple[
    dict[str, frozenset[int]],
    dict[str, dict[int, str]],
]:
    valid_lines = {
        item.filename: frozenset({1})
        for item in files
    }
    changed_lines = {
        item.filename: {
            1: item.patch.splitlines()[-1][1:]
        }
        for item in files
    }

    return valid_lines, changed_lines


def make_review_input() -> PullRequestReviewInput:
    files = make_files()
    valid_lines, changed_lines = (
        make_line_indexes(files)
    )

    return PullRequestReviewInput(
        repository="owner/repository",
        number=1,
        title="테스트 PR",
        url="https://example.invalid/pull/1",
        base_sha="base",
        head_sha="head",
        files=files,
        valid_lines=valid_lines,
        changed_lines=changed_lines,
        diff_sha256="a" * 64,
    )


def make_weekly_review_input() -> WeeklyReviewInput:
    files = make_files()
    valid_lines, changed_lines = (
        make_line_indexes(files)
    )
    since = datetime(
        2026,
        9,
        1,
        tzinfo=timezone.utc,
    )

    return WeeklyReviewInput(
        repository="owner/repository",
        since=since,
        until=since + timedelta(days=7),
        base_sha="base",
        head_sha="head",
        commits=(
            WeeklyCommit(
                sha="head",
                message="주간 변경",
                url=(
                    "https://example.invalid/"
                    "commit/head"
                ),
                committed_at=since,
            ),
        ),
        pull_requests=(),
        files=files,
        valid_lines=valid_lines,
        changed_lines=changed_lines,
        diff_sha256="b" * 64,
    )


class ReviewSelectorTests(unittest.TestCase):
    def test_routes_every_file_to_two_models(
        self,
    ) -> None:
        selection = select_review_inputs(
            make_review_input()
        )

        provider_sets = {
            provider: set(files)
            for provider, files
            in selection.selected_files.items()
        }
        selected_names = set().union(
            *provider_sets.values()
        )

        for filename in selected_names:
            model_count = sum(
                filename in filenames
                for filenames
                in provider_sets.values()
            )
            self.assertGreaterEqual(
                model_count,
                2,
                filename,
            )

    def test_qwen_receives_bounded_risk_files(
        self,
    ) -> None:
        selection = select_review_inputs(
            make_review_input(),
            gpt_percent=20,
        )

        self.assertEqual(
            len(selection.selected_files["qwen"]),
            2,
        )
        self.assertIn(
            "src/auth_service.py",
            selection.selected_files["qwen"],
        )
        self.assertLess(
            len(selection.selected_files["qwen"]),
            10,
        )

    def test_gpt_connects_both_review_areas(
        self,
    ) -> None:
        selection = select_review_inputs(
            make_review_input(),
            gpt_percent=20,
            gemini_percent=10,
        )

        gpt_files = set(
            selection.selected_files["gpt"]
        )
        qwen_files = set(
            selection.selected_files["qwen"]
        )
        gemini_files = set(
            selection.selected_files["gemini"]
        )

        self.assertTrue(
            qwen_files.issubset(gpt_files)
        )
        self.assertTrue(
            gemini_files.issubset(gpt_files)
        )
        self.assertEqual(
            selection.selected_files["gemini"],
            ("requirements.txt",),
        )

    def test_prompts_contain_selected_files_only(
        self,
    ) -> None:
        selection = select_review_inputs(
            make_review_input()
        )

        for provider in (
            "qwen",
            "gpt",
            "gemini",
        ):
            payload = json.loads(
                selection.provider_prompts[provider]
            )
            prompt_files = {
                item["filename"]
                for item in payload["files"]
            }

            self.assertEqual(
                prompt_files,
                set(
                    selection.selected_files[
                        provider
                    ]
                ),
            )

    def test_builds_weekly_review_prompts(
        self,
    ) -> None:
        selection = select_review_inputs(
            make_weekly_review_input()
        )

        for provider, prompt in (
            selection.provider_prompts.items()
        ):
            payload = json.loads(prompt)

            self.assertIn(
                "weekly_review",
                payload,
                provider,
            )
            self.assertNotIn(
                "pull_request",
                payload,
                provider,
            )
            self.assertEqual(
                payload["weekly_review"][
                    "commit_count"
                ],
                1,
            )
            self.assertLessEqual(
                len(prompt),
                4_800,
            )

    def test_rejects_unsafe_character_limits(
        self,
    ) -> None:
        review_input = make_review_input()

        with self.subTest(provider="qwen"):
            with self.assertRaises(
                ReviewSelectionError
            ):
                select_review_inputs(
                    review_input,
                    qwen_input_char_limit=100,
                )

        with self.subTest(provider="cloud"):
            with self.assertRaises(
                ReviewSelectionError
            ):
                select_review_inputs(
                    review_input,
                    cloud_input_char_limit=100,
                )


if __name__ == "__main__":
    unittest.main()