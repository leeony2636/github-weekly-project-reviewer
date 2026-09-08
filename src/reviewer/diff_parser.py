import re
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Iterable, Mapping

from reviewer.schemas import Finding


HUNK_HEADER = re.compile(
    r"^@@ "
    r"-(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? "
    r"@@(?: .*)?$"
)


class DiffParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedPatch:
    file: str
    valid_lines: frozenset[int]
    added_lines: dict[int, str]
    hunk_count: int
    complete: bool


def normalize_repository_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiffParseError(
            "파일 경로가 비어 있습니다."
        )

    value = value.strip().replace("\\", "/")

    while value.startswith("./"):
        value = value[2:]

    if (
        value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or ".." in value.split("/")
    ):
        raise DiffParseError(
            "저장소 기준 상대 경로만 허용됩니다."
        )

    return value


def make_evidence_hash(
    *,
    file_path: str,
    line: int,
    source_text: str,
) -> str:
    normalized_file = normalize_repository_path(
        file_path
    )

    payload = (
        f"{normalized_file}\0"
        f"{line}\0"
        f"{source_text}"
    )

    return sha256(
        payload.encode("utf-8")
    ).hexdigest()


def parse_patch(
    file_path: str,
    patch: str | None,
) -> ParsedPatch:
    normalized_file = normalize_repository_path(
        file_path
    )

    if patch is None or not patch.strip():
        return ParsedPatch(
            file=normalized_file,
            valid_lines=frozenset(),
            added_lines={},
            hunk_count=0,
            complete=True,
        )

    valid_lines: set[int] = set()
    added_lines: dict[int, str] = {}

    hunk_count = 0
    complete = True

    in_hunk = False
    new_line = 0
    old_remaining = 0
    new_remaining = 0

    for raw_line in patch.splitlines():
        header = HUNK_HEADER.match(raw_line)

        if header:
            if in_hunk and (
                old_remaining != 0
                or new_remaining != 0
            ):
                complete = False

            hunk_count += 1
            in_hunk = True

            old_count_text = header.group(
                "old_count"
            )
            new_count_text = header.group(
                "new_count"
            )

            old_remaining = (
                int(old_count_text)
                if old_count_text is not None
                else 1
            )
            new_remaining = (
                int(new_count_text)
                if new_count_text is not None
                else 1
            )
            new_line = int(
                header.group("new_start")
            )
            continue

        if not in_hunk:
            continue

        if raw_line.startswith("\\"):
            continue

        if raw_line.startswith("+"):
            if new_remaining <= 0:
                complete = False
                continue

            source_text = raw_line[1:]

            valid_lines.add(new_line)
            added_lines[new_line] = source_text

            new_line += 1
            new_remaining -= 1

        elif raw_line.startswith("-"):
            if old_remaining <= 0:
                complete = False
                continue

            old_remaining -= 1

        elif raw_line.startswith(" "):
            if (
                old_remaining <= 0
                or new_remaining <= 0
            ):
                complete = False
                continue

            old_remaining -= 1
            new_remaining -= 1
            new_line += 1

        else:
            complete = False

    if in_hunk and (
        old_remaining != 0
        or new_remaining != 0
    ):
        complete = False

    return ParsedPatch(
        file=normalized_file,
        valid_lines=frozenset(valid_lines),
        added_lines=added_lines,
        hunk_count=hunk_count,
        complete=complete,
    )


def _parse_complete_files(
    files: Iterable[Mapping[str, Any]],
) -> list[ParsedPatch]:
    parsed_files: list[ParsedPatch] = []

    for item in files:
        parsed = parse_patch(
            item.get("filename"),
            item.get("patch"),
        )

        if not parsed.complete:
            raise DiffParseError(
                "불완전하거나 잘린 patch입니다: "
                f"{parsed.file}"
            )

        parsed_files.append(parsed)

    return parsed_files


def build_valid_lines(
    files: Iterable[Mapping[str, Any]],
) -> dict[str, frozenset[int]]:
    result: dict[str, frozenset[int]] = {}

    for parsed in _parse_complete_files(files):
        existing = set(
            result.get(
                parsed.file,
                frozenset(),
            )
        )
        existing.update(parsed.valid_lines)

        result[parsed.file] = frozenset(
            existing
        )

    return result


def build_changed_line_index(
    files: Iterable[Mapping[str, Any]],
) -> dict[str, dict[int, str]]:
    result: dict[str, dict[int, str]] = {}

    for parsed in _parse_complete_files(files):
        file_lines = result.setdefault(
            parsed.file,
            {},
        )

        for line, source_text in (
            parsed.added_lines.items()
        ):
            existing = file_lines.get(line)

            if (
                existing is not None
                and existing != source_text
            ):
                raise DiffParseError(
                    "같은 변경 라인에 서로 다른 코드가 "
                    f"있습니다: {parsed.file}:{line}"
                )

            file_lines[line] = source_text

    return result


def is_valid_changed_line(
    valid_lines: Mapping[str, frozenset[int]],
    *,
    file_path: str,
    line: int,
) -> bool:
    normalized_file = normalize_repository_path(
        file_path
    )

    if (
        isinstance(line, bool)
        or not isinstance(line, int)
    ):
        return False

    return line in valid_lines.get(
        normalized_file,
        frozenset(),
    )


def ground_finding(
    finding: Finding,
    changed_lines: Mapping[
        str,
        Mapping[int, str],
    ],
) -> Finding | None:
    file_lines = changed_lines.get(
        finding.file,
        {},
    )
    actual_source = file_lines.get(
        finding.line
    )

    if actual_source is None:
        return None

    # 들여쓰기 앞뒤 차이는 허용하지만 코드 내용은
    # 실제 변경 라인과 정확히 일치해야 한다.
    if (
        finding.evidence.strip()
        != actual_source.strip()
    ):
        return None

    if not actual_source.strip():
        return None

    evidence_hash = make_evidence_hash(
        file_path=finding.file,
        line=finding.line,
        source_text=actual_source,
    )

    return replace(
        finding,
        evidence=actual_source,
        evidence_hash=evidence_hash,
    )


def ground_findings(
    findings: Iterable[Finding],
    changed_lines: Mapping[
        str,
        Mapping[int, str],
    ],
) -> tuple[list[Finding], int]:
    grounded: list[Finding] = []
    rejected_count = 0

    for finding in findings:
        verified = ground_finding(
            finding,
            changed_lines,
        )

        if verified is None:
            rejected_count += 1
            continue

        grounded.append(verified)

    return grounded, rejected_count