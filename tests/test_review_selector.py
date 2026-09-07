import json
import unittest

from reviewer.github_service import (
    PullRequestFile,
    PullRequestReviewInput,
)
from reviewer.review_selector import (
    ReviewSelectionError,
    select_review_inputs,
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


def make_review_input() -> PullRequestReviewInput:
    files = (
        make_file(0, filename="src/auth_service.py"),
        make_file(1, filename="requirements.txt"),
        make_file(2),
        make_file(3),
        make_file(4),
        make_file(5),
        make_file(6),
        make_file(7),
        make_file(8),
        make_file(9),
    )

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


class ReviewSelectorTests(unittest.TestCase):
    def test_qwen_receives_all_files(self) -> None:
        review_input = make_review_input()

        selection = select_review_inputs(
            review_input
        )

        self.assertEqual(
            len(selection.selected_files["qwen"]),
            10,
        )

    def test_gpt_receives_twenty_percent(
        self,
    ) -> None:
        review_input = make_review_input()

        selection = select_review_inputs(
            review_input,
            gpt_percent=20,
        )

        self.assertEqual(
            len(selection.selected_files["gpt"]),
            2,
        )
        self.assertIn(
            "src/auth_service.py",
            selection.selected_files["gpt"],
        )

    def test_gemini_receives_ten_percent(
        self,
    ) -> None:
        review_input = make_review_input()

        selection = select_review_inputs(
            review_input,
            gemini_percent=10,
        )

        self.assertEqual(
            len(selection.selected_files["gemini"]),
            1,
        )
        self.assertEqual(
            selection.selected_files["gemini"][0],
            "requirements.txt",
        )

    def test_prompt_contains_only_selected_files(
        self,
    ) -> None:
        review_input = make_review_input()

        selection = select_review_inputs(
            review_input,
            gpt_percent=20,
        )

        payload = json.loads(
            selection.provider_prompts["gpt"]
        )

        prompt_files = {
            item["filename"]
            for item in payload["files"]
        }

        self.assertEqual(
            prompt_files,
            set(selection.selected_files["gpt"]),
        )

    def test_rejects_small_character_limit(
        self,
    ) -> None:
        review_input = make_review_input()

        with self.assertRaises(
            ReviewSelectionError
        ):
            select_review_inputs(
                review_input,
                cloud_input_char_limit=100,
            )


if __name__ == "__main__":
    unittest.main()