import re
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from reviewer.schemas import ConsensusFinding, Finding


SEVERITY_ORDER = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
}

PROVIDER_ORDER = {
    "qwen": 0,
    "gpt": 1,
    "gemini": 2,
}


def normalize_message(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
    return " ".join(value.split())


def message_similarity(left: str, right: str) -> float:
    left_normalized = normalize_message(left)
    right_normalized = normalize_message(right)

    if not left_normalized or not right_normalized:
        return 0.0

    if left_normalized == right_normalized:
        return 1.0

    if (
        min(len(left_normalized), len(right_normalized)) >= 10
        and (
            left_normalized in right_normalized
            or right_normalized in left_normalized
        )
    ):
        return 0.9

    sequence_score = SequenceMatcher(
        None,
        left_normalized,
        right_normalized,
    ).ratio()

    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    union = left_tokens | right_tokens

    token_score = (
        len(left_tokens & right_tokens) / len(union)
        if union
        else 0.0
    )

    return max(sequence_score, token_score)


def findings_match(
    left: Finding,
    right: Finding,
    *,
    line_tolerance: int,
    similarity_threshold: float = 0.42,
) -> bool:
    if left.provider == right.provider:
        return False

    if left.file != right.file:
        return False

    if left.category != right.category:
        return False

    if abs(left.line - right.line) > line_tolerance:
        return False

    return (
        message_similarity(left.message, right.message)
        >= similarity_threshold
    )


def filter_valid_line_findings(
    findings: Iterable[Finding],
    valid_lines: Mapping[str, frozenset[int]],
) -> list[Finding]:
    return [
        finding
        for finding in findings
        if finding.line
        in valid_lines.get(finding.file, frozenset())
    ]


def _select_representative(
    findings: list[Finding],
) -> Finding:
    return sorted(
        findings,
        key=lambda item: (
            -item.confidence,
            SEVERITY_ORDER[item.severity],
            PROVIDER_ORDER[item.provider],
            item.line,
        ),
    )[0]


def _make_consensus(
    findings: list[Finding],
) -> ConsensusFinding:
    representative = _select_representative(findings)
    providers = tuple(
        sorted(
            {item.provider for item in findings},
            key=lambda name: PROVIDER_ORDER[name],
        )
    )

    severity = min(
        (item.severity for item in findings),
        key=lambda value: SEVERITY_ORDER[value],
    )

    confidence = round(
        sum(item.confidence for item in findings)
        / len(findings),
        4,
    )

    return ConsensusFinding(
        file=representative.file,
        line=representative.line,
        category=representative.category,
        severity=severity,
        message=representative.message,
        reason=representative.reason,
        confidence=confidence,
        providers=providers,
        match_count=len(providers),
        evidence=representative.evidence,
        evidence_hash=representative.evidence_hash,
    )

def build_consensus(
    findings: Iterable[Finding],
    valid_lines: Mapping[str, frozenset[int]],
    *,
    line_tolerance: int = 1,
    min_match_count: int = 2,
) -> list[ConsensusFinding]:
    if not 0 <= line_tolerance <= 3:
        raise ValueError(
            "line_tolerance는 0 이상 3 이하여야 합니다."
        )

    if not 2 <= min_match_count <= 3:
        raise ValueError(
            "min_match_count는 2 이상 3 이하여야 합니다."
        )

    candidates = filter_valid_line_findings(
        findings,
        valid_lines,
    )

    candidates.sort(
        key=lambda item: (
            item.file,
            item.line,
            item.category,
            PROVIDER_ORDER[item.provider],
            -item.confidence,
        )
    )

    used_indexes: set[int] = set()
    results: list[ConsensusFinding] = []

    for anchor_index, anchor in enumerate(candidates):
        if anchor_index in used_indexes:
            continue

        cluster = [anchor]
        cluster_indexes = [anchor_index]
        used_providers = {anchor.provider}

        for provider in ("qwen", "gpt", "gemini"):
            if provider in used_providers:
                continue

            matches = []

            for candidate_index, candidate in enumerate(
                candidates
            ):
                if candidate_index in used_indexes:
                    continue

                if candidate.provider != provider:
                    continue

                if findings_match(
                    anchor,
                    candidate,
                    line_tolerance=line_tolerance,
                ):
                    matches.append(
                        (
                            message_similarity(
                                anchor.message,
                                candidate.message,
                            ),
                            candidate.confidence,
                            -abs(anchor.line - candidate.line),
                            candidate_index,
                            candidate,
                        )
                    )

            if not matches:
                continue

            matches.sort(reverse=True, key=lambda item: item[:4])
            _, _, _, candidate_index, candidate = matches[0]

            cluster.append(candidate)
            cluster_indexes.append(candidate_index)
            used_providers.add(candidate.provider)

        if len(used_providers) >= min_match_count:
            results.append(_make_consensus(cluster))
            used_indexes.update(cluster_indexes)
        else:
            used_indexes.add(anchor_index)

    results.sort(
        key=lambda item: (
            SEVERITY_ORDER[item.severity],
            -item.match_count,
            -item.confidence,
            item.file,
            item.line,
        )
    )

    return results