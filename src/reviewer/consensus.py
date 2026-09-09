import re
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from reviewer.schemas import (
    ConsensusFinding,
    CrossReviewVote,
    Finding,
    ReviewCandidate,
)


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

def build_review_candidates(
    findings: Iterable[Finding],
    *,
    line_tolerance: int = 1,
    similarity_threshold: float = 0.42,
) -> list[ReviewCandidate]:
    if not 0 <= line_tolerance <= 3:
        raise ValueError(
            "line_tolerance는 0 이상 3 이하여야 합니다."
        )

    ordered_findings = sorted(
        findings,
        key=lambda item: (
            item.file,
            item.line,
            item.category,
            PROVIDER_ORDER[item.provider],
            -item.confidence,
            item.message,
        ),
    )

    groups: list[list[Finding]] = []

    for finding in ordered_findings:
        matched_group: list[Finding] | None = None

        for group in groups:
            group_providers = {
                item.provider
                for item in group
            }

            if finding.provider in group_providers:
                continue

            anchor = group[0]

            if findings_match(
                anchor,
                finding,
                line_tolerance=line_tolerance,
                similarity_threshold=(
                    similarity_threshold
                ),
            ):
                matched_group = group
                break

        if matched_group is None:
            groups.append([finding])
        else:
            matched_group.append(finding)

    candidates: list[ReviewCandidate] = []

    for index, group in enumerate(
        groups,
        start=1,
    ):
        representative = _select_representative(
            group
        )

        source_providers = tuple(
            sorted(
                {
                    item.provider
                    for item in group
                },
                key=lambda name: (
                    PROVIDER_ORDER[name]
                ),
            )
        )

        severity = min(
            (
                item.severity
                for item in group
            ),
            key=lambda value: (
                SEVERITY_ORDER[value]
            ),
        )

        confidence = round(
            sum(
                item.confidence
                for item in group
            )
            / len(group),
            4,
        )

        candidates.append(
            ReviewCandidate(
                candidate_id=(
                    f"candidate-{index:03d}"
                ),
                file=representative.file,
                line=representative.line,
                category=(
                    representative.category
                ),
                severity=severity,
                message=representative.message,
                reason=representative.reason,
                confidence=confidence,
                evidence=representative.evidence,
                evidence_hash=(
                    representative.evidence_hash
                ),
                source_providers=(
                    source_providers
                ),
            )
        )

    return candidates

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

def build_cross_review_consensus(
    candidates: Iterable[ReviewCandidate],
    votes: Iterable[CrossReviewVote],
    *,
    min_accept_count: int = 2,
) -> list[ConsensusFinding]:
    if not 2 <= min_accept_count <= 3:
        raise ValueError(
            "min_accept_count는 2 이상 3 이하여야 합니다."
        )

    candidate_list = list(candidates)
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidate in candidate_list
    }

    if len(candidate_by_id) != len(candidate_list):
        raise ValueError(
            "교차평가 후보 ID가 중복되었습니다."
        )

    votes_by_candidate: dict[
        str,
        dict[str, CrossReviewVote],
    ] = {
        candidate.candidate_id: {}
        for candidate in candidate_list
    }

    for vote in votes:
        if vote.candidate_id not in candidate_by_id:
            raise ValueError(
                "등록되지 않은 후보에 대한 "
                "교차평가가 있습니다."
            )

        if vote.provider not in PROVIDER_ORDER:
            raise ValueError(
                "지원하지 않는 모델의 "
                "교차평가가 있습니다."
            )

        candidate_votes = votes_by_candidate[
            vote.candidate_id
        ]

        if vote.provider in candidate_votes:
            raise ValueError(
                "같은 모델이 동일 후보를 "
                "중복 평가했습니다."
            )

        candidate_votes[vote.provider] = vote

    expected_providers = set(PROVIDER_ORDER)
    results: list[ConsensusFinding] = []

    for candidate in candidate_list:
        candidate_votes = votes_by_candidate[
            candidate.candidate_id
        ]

        if set(candidate_votes) != expected_providers:
            raise ValueError(
                f"{candidate.candidate_id} 후보를 "
                "세 모델 모두 평가하지 않았습니다."
            )

        positive_votes = [
            vote
            for vote in candidate_votes.values()
            if vote.decision in {
                "accept",
                "revise",
            }
        ]

        if len(positive_votes) < min_accept_count:
            continue

        providers = tuple(
            sorted(
                (
                    vote.provider
                    for vote in positive_votes
                ),
                key=lambda name: (
                    PROVIDER_ORDER[name]
                ),
            )
        )

        message = candidate.message
        reason = candidate.reason

        revision_votes = [
            vote
            for vote in positive_votes
            if vote.decision == "revise"
        ]

        if len(revision_votes) >= min_accept_count:
            selected_revision = sorted(
                revision_votes,
                key=lambda vote: (
                    -vote.confidence,
                    PROVIDER_ORDER[vote.provider],
                ),
            )[0]

            message = selected_revision.message
            reason = selected_revision.reason

        severity_counts = {
            severity: sum(
                vote.severity == severity
                for vote in positive_votes
            )
            for severity in SEVERITY_ORDER
        }

        agreed_severities = [
            severity
            for severity, count
            in severity_counts.items()
            if count >= min_accept_count
        ]

        if agreed_severities:
            severity = min(
                agreed_severities,
                key=lambda value: (
                    SEVERITY_ORDER[value]
                ),
            )
        else:
            severity = candidate.severity

        confidence = round(
            sum(
                vote.confidence
                for vote in positive_votes
            )
            / len(positive_votes),
            4,
        )

        results.append(
            ConsensusFinding(
                file=candidate.file,
                line=candidate.line,
                category=candidate.category,
                severity=severity,
                message=message,
                reason=reason,
                confidence=confidence,
                providers=providers,
                match_count=len(providers),
                evidence=candidate.evidence,
                evidence_hash=(
                    candidate.evidence_hash
                ),
            )
        )

    results.sort(
        key=lambda item: (
            SEVERITY_ORDER[item.severity],
            item.file,
            item.line,
            item.category,
            item.message,
        )
    )

    return results