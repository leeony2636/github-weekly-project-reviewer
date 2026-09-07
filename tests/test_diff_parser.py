import unittest
from pathlib import Path

from reviewer.diff_parser import (
    DiffParseError,
    build_changed_line_index,
    build_valid_lines,
    ground_finding,
    ground_findings,
    is_valid_changed_line,
    make_evidence_hash,
    parse_patch,
)
from reviewer.schemas import Finding


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "sample_pr.diff"
)


def make_finding(
    *,
    line: int,
    evidence: str,
) -> Finding:
    return Finding(
        provider="qwen",
        file="src/example.py",
        line=line,
        category="bug",
        severity="P1",
        message="변경 코드에 문제가 있습니다.",
        reason="실제 변경 라인을 확인했습니다.",
        confidence=0.8,
        evidence=evidence,
    )


class DiffParserTests(unittest.TestCase):
    def test_extracts_only_added_lines(self) -> None:
        patch = FIXTURE_PATH.read_text(
            encoding="utf-8"
        )

        result = parse_patch(
            "src/example.py",
            patch,
        )

        self.assertTrue(result.complete)
        self.assertEqual(result.hunk_count, 2)
        self.assertEqual(
            result.valid_lines,
            frozenset({2, 3, 12}),
        )

    def test_preserves_added_source_text(
        self,
    ) -> None:
        patch = FIXTURE_PATH.read_text(
            encoding="utf-8"
        )

        result = parse_patch(
            "src/example.py",
            patch,
        )

        self.assertEqual(
            result.added_lines,
            {
                2: "enabled = True",
                3: "timeout = 30",
                12: "    validate()",
            },
        )

    def test_deletion_is_not_valid_new_line(
        self,
    ) -> None:
        patch = (
            "@@ -4,1 +4,0 @@\n"
            "-removed_line = True"
        )

        result = parse_patch(
            "src/example.py",
            patch,
        )

        self.assertTrue(result.complete)
        self.assertEqual(
            result.valid_lines,
            frozenset(),
        )
        self.assertEqual(
            result.added_lines,
            {},
        )

    def test_detects_truncated_patch(self) -> None:
        patch = (
            "@@ -1,2 +1,2 @@\n"
            "-old_value\n"
            "+new_value"
        )

        result = parse_patch(
            "src/example.py",
            patch,
        )

        self.assertFalse(result.complete)

    def test_build_valid_lines_rejects_truncated_patch(
        self,
    ) -> None:
        files = [
            {
                "filename": "src/example.py",
                "patch": (
                    "@@ -1,2 +1,2 @@\n"
                    "-old_value\n"
                    "+new_value"
                ),
            }
        ]

        with self.assertRaises(
            DiffParseError
        ):
            build_valid_lines(files)

    def test_builds_changed_line_index(
        self,
    ) -> None:
        patch = FIXTURE_PATH.read_text(
            encoding="utf-8"
        )

        index = build_changed_line_index(
            [
                {
                    "filename": "src/example.py",
                    "patch": patch,
                }
            ]
        )

        self.assertEqual(
            index["src/example.py"][12],
            "    validate()",
        )

    def test_checks_file_and_line_together(
        self,
    ) -> None:
        valid_lines = {
            "src/example.py": frozenset(
                {2, 3, 12}
            )
        }

        self.assertTrue(
            is_valid_changed_line(
                valid_lines,
                file_path="src/example.py",
                line=3,
            )
        )
        self.assertFalse(
            is_valid_changed_line(
                valid_lines,
                file_path="src/example.py",
                line=4,
            )
        )

    def test_grounds_exact_evidence(self) -> None:
        finding = make_finding(
            line=12,
            evidence="validate()",
        )
        changed_lines = {
            "src/example.py": {
                12: "    validate()"
            }
        }

        grounded = ground_finding(
            finding,
            changed_lines,
        )

        self.assertIsNotNone(grounded)
        self.assertEqual(
            grounded.evidence,
            "    validate()",
        )
        self.assertEqual(
            len(grounded.evidence_hash),
            64,
        )

    def test_rejects_invented_evidence(
        self,
    ) -> None:
        finding = make_finding(
            line=12,
            evidence="delete_database()",
        )
        changed_lines = {
            "src/example.py": {
                12: "    validate()"
            }
        }

        self.assertIsNone(
            ground_finding(
                finding,
                changed_lines,
            )
        )

    def test_counts_rejected_evidence(
        self,
    ) -> None:
        findings = [
            make_finding(
                line=2,
                evidence="enabled = True",
            ),
            make_finding(
                line=3,
                evidence="wrong = True",
            ),
        ]
        changed_lines = {
            "src/example.py": {
                2: "enabled = True",
                3: "timeout = 30",
            }
        }

        grounded, rejected_count = (
            ground_findings(
                findings,
                changed_lines,
            )
        )

        self.assertEqual(len(grounded), 1)
        self.assertEqual(rejected_count, 1)

    def test_evidence_hash_is_deterministic(
        self,
    ) -> None:
        first = make_evidence_hash(
            file_path="src/example.py",
            line=10,
            source_text="value = 1",
        )
        second = make_evidence_hash(
            file_path="src/example.py",
            line=10,
            source_text="value = 1",
        )

        self.assertEqual(first, second)

    def test_rejects_parent_directory_path(
        self,
    ) -> None:
        with self.assertRaises(
            DiffParseError
        ):
            parse_patch(
                "../secret.txt",
                "@@ -0,0 +1 @@\n+x",
            )


if __name__ == "__main__":
    unittest.main()