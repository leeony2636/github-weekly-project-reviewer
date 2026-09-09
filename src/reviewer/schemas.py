import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Sequence


PROVIDERS = {"qwen", "gpt", "gemini"}

CATEGORIES = {
    "syntax",
    "quality",
    "bug",
    "security",
    "performance",
    "compatibility",
    "architecture",
    "test",
}

SEVERITIES = {"P0", "P1", "P2"}

CROSS_REVIEW_DECISIONS = {
    "accept",
    "reject_unsupported",
    "reject_not_issue",
    "reject_handled",
    "revise",
}

CROSS_REVIEW_VOTE_KEYS = {
    "candidate_id",
    "decision",
    "reason",
    "severity",
    "message",
    "confidence",
}
MODEL_FINDING_KEYS = {
    "file",
    "line",
    "category",
    "severity",
    "message",
    "reason",
    "confidence",
    "evidence",
}


class SchemaError(ValueError):
    pass

def build_model_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                        },
                        "line": {
                            "type": "integer",
                        },
                        "category": {
                            "type": "string",
                            "enum": sorted(CATEGORIES),
                        },
                        "severity": {
                            "type": "string",
                            "enum": sorted(SEVERITIES),
                        },
                        "message": {
                            "type": "string",
                        },
                        "reason": {
                            "type": "string",
                        },
                        "confidence": {
                            "type": "number",
                        },
                        "evidence": {
                            "type": "string",
                        },
                    },
                    "required": sorted(
                        MODEL_FINDING_KEYS
                    ),
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "findings",
        ],
        "additionalProperties": False,
    }

def build_cross_review_response_schema(
    candidate_ids: Sequence[str],
) -> dict[str, Any]:
    ordered_candidate_ids = list(
        candidate_ids
    )

    if not ordered_candidate_ids:
        raise ValueError(
            "교차평가 후보가 비어 있습니다."
        )

    if (
        len(set(ordered_candidate_ids))
        != len(ordered_candidate_ids)
    ):
        raise ValueError(
            "교차평가 후보 ID가 중복되었습니다."
        )

    return {
        "type": "object",
        "properties": {
            "votes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {
                            "type": "string",
                            "enum": (
                                ordered_candidate_ids
                            ),
                        },
                        "decision": {
                            "type": "string",
                            "enum": sorted(
                                CROSS_REVIEW_DECISIONS
                            ),
                        },
                        "reason": {
                            "type": "string",
                        },
                        "severity": {
                            "type": "string",
                            "enum": sorted(
                                SEVERITIES
                            ),
                        },
                        "message": {
                            "type": "string",
                        },
                        "confidence": {
                            "type": "number",
                        },
                    },
                    "required": sorted(
                        CROSS_REVIEW_VOTE_KEYS
                    ),
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "votes",
        ],
        "additionalProperties": False,
    }

def _required_text(
    payload: dict[str, Any],
    key: str,
    *,
    max_length: int,
) -> str:
    value = payload.get(key)

    if not isinstance(value, str) or not value.strip():
        raise SchemaError(
            f"{key}는 비어 있지 않은 문자열이어야 합니다."
        )

    value = value.strip()

    if len(value) > max_length:
        raise SchemaError(
            f"{key}가 최대 길이 "
            f"{max_length}자를 초과했습니다."
        )

    return value


def _normalize_file_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(
            "file은 비어 있지 않은 문자열이어야 합니다."
        )

    value = value.strip().replace("\\", "/")

    while value.startswith("./"):
        value = value[2:]

    if (
        value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or ".." in value.split("/")
    ):
        raise SchemaError(
            "file은 저장소 기준 상대 경로여야 합니다."
        )

    return value


@dataclass(frozen=True, slots=True)
class Finding:
    provider: str
    file: str
    line: int
    category: str
    severity: str
    message: str
    reason: str
    confidence: float

    # evidence_hash는 모델이 생성하지 않는다.
    # 실제 코드와 evidence가 일치한 뒤 프로그램이 계산한다.
    evidence: str = ""
    evidence_hash: str = ""

    @classmethod
    def from_payload(
        cls,
        payload: Any,
        *,
        provider: str,
    ) -> "Finding":
        if provider not in PROVIDERS:
            raise SchemaError(
                f"지원하지 않는 provider입니다: {provider}"
            )

        if not isinstance(payload, dict):
            raise SchemaError(
                "finding은 JSON 객체여야 합니다."
            )

        if set(payload) != MODEL_FINDING_KEYS:
            raise SchemaError(
                "finding 필드가 정확하지 않습니다. "
                f"필수 필드: {sorted(MODEL_FINDING_KEYS)}"
            )

        line = payload["line"]

        if (
            isinstance(line, bool)
            or not isinstance(line, int)
            or line < 1
        ):
            raise SchemaError(
                "line은 1 이상의 정수여야 합니다."
            )

        category = payload["category"]

        if category not in CATEGORIES:
            raise SchemaError(
                f"허용되지 않은 category입니다: {category}"
            )

        severity = payload["severity"]

        if severity not in SEVERITIES:
            raise SchemaError(
                f"허용되지 않은 severity입니다: {severity}"
            )

        confidence = payload["confidence"]

        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise SchemaError(
                "confidence는 0.0 이상 "
                "1.0 이하 숫자여야 합니다."
            )

        return cls(
            provider=provider,
            file=_normalize_file_path(payload["file"]),
            line=line,
            category=category,
            severity=severity,
            message=_required_text(
                payload,
                "message",
                max_length=500,
            ),
            reason=_required_text(
                payload,
                "reason",
                max_length=1000,
            ),
            confidence=float(confidence),
            evidence=_required_text(
                payload,
                "evidence",
                max_length=1000,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    candidate_id: str
    file: str
    line: int
    category: str
    severity: str
    message: str
    reason: str
    confidence: float
    evidence: str
    evidence_hash: str
    source_providers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_providers"] = list(
            self.source_providers
        )
        return result

    def to_cross_review_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }
@dataclass(frozen=True, slots=True)
class CrossReviewVote:
    provider: str
    candidate_id: str
    decision: str
    reason: str
    severity: str
    message: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
@dataclass(frozen=True, slots=True)
class ConsensusFinding:
    file: str
    line: int
    category: str
    severity: str
    message: str
    reason: str
    confidence: float
    providers: tuple[str, ...]
    match_count: int
    evidence: str = ""
    evidence_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["providers"] = list(self.providers)
        return result


def parse_model_response(
    content: str,
    *,
    provider: str,
    max_findings: int = 50,
    max_response_chars: int = 100_000,
) -> list[Finding]:
    if provider not in PROVIDERS:
        raise SchemaError(
            f"지원하지 않는 provider입니다: {provider}"
        )

    if not isinstance(content, str) or not content.strip():
        raise SchemaError(
            f"{provider} 응답이 비어 있습니다."
        )

    if len(content) > max_response_chars:
        raise SchemaError(
            f"{provider} 응답이 최대 크기를 초과했습니다."
        )

    candidate = content.strip()

    if (
        candidate.startswith("```")
        or candidate.endswith("```")
    ):
        raise SchemaError(
            f"{provider}가 Markdown 코드 블록을 출력했습니다."
        )

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"{provider} JSON 파싱 실패: {exc}"
        ) from exc

    if (
        not isinstance(payload, dict)
        or set(payload) != {"findings"}
    ):
        raise SchemaError(
            f"{provider} 최상위 JSON은 "
            "findings만 포함해야 합니다."
        )

    findings = payload["findings"]

    if not isinstance(findings, list):
        raise SchemaError(
            f"{provider}.findings는 배열이어야 합니다."
        )

    if len(findings) > max_findings:
        raise SchemaError(
            f"{provider} finding 개수가 제한 "
            f"{max_findings}개를 초과했습니다."
        )

    return [
        Finding.from_payload(
            item,
            provider=provider,
        )
        for item in findings
    ]

def parse_cross_review_response(
    content: str,
    *,
    provider: str,
    expected_candidate_ids: Sequence[str],
    max_response_chars: int = 100_000,
) -> list[CrossReviewVote]:
    if provider not in PROVIDERS:
        raise SchemaError(
            f"지원하지 않는 provider입니다: {provider}"
        )

    ordered_candidate_ids = list(
        expected_candidate_ids
    )

    if not ordered_candidate_ids:
        raise SchemaError(
            "교차평가 후보가 비어 있습니다."
        )

    if (
        len(set(ordered_candidate_ids))
        != len(ordered_candidate_ids)
    ):
        raise SchemaError(
            "교차평가 후보 ID가 중복되었습니다."
        )

    if not isinstance(content, str) or not content.strip():
        raise SchemaError(
            f"{provider} 교차평가 응답이 비어 있습니다."
        )

    if len(content) > max_response_chars:
        raise SchemaError(
            f"{provider} 교차평가 응답이 "
            "최대 크기를 초과했습니다."
        )

    candidate = content.strip()

    if (
        candidate.startswith("```")
        or candidate.endswith("```")
    ):
        raise SchemaError(
            f"{provider}가 교차평가 응답에 "
            "Markdown 코드 블록을 출력했습니다."
        )

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"{provider} 교차평가 JSON "
            f"파싱 실패: {exc}"
        ) from exc

    if (
        not isinstance(payload, dict)
        or set(payload) != {"votes"}
    ):
        raise SchemaError(
            f"{provider} 교차평가 최상위 JSON은 "
            "votes만 포함해야 합니다."
        )

    votes = payload["votes"]

    if not isinstance(votes, list):
        raise SchemaError(
            f"{provider}.votes는 배열이어야 합니다."
        )

    votes_by_candidate: dict[
        str,
        CrossReviewVote,
    ] = {}

    expected_id_set = set(
        ordered_candidate_ids
    )

    for vote_payload in votes:
        if not isinstance(vote_payload, dict):
            raise SchemaError(
                "교차평가 vote는 "
                "JSON 객체여야 합니다."
            )

        if set(vote_payload) != CROSS_REVIEW_VOTE_KEYS:
            raise SchemaError(
                "교차평가 vote 필드가 "
                "정확하지 않습니다. "
                f"필수 필드: "
                f"{sorted(CROSS_REVIEW_VOTE_KEYS)}"
            )

        candidate_id = vote_payload[
            "candidate_id"
        ]

        if (
            not isinstance(candidate_id, str)
            or candidate_id
            not in expected_id_set
        ):
            raise SchemaError(
                "허용되지 않은 "
                f"candidate_id입니다: "
                f"{candidate_id}"
            )

        if candidate_id in votes_by_candidate:
            raise SchemaError(
                "같은 후보에 대한 vote가 "
                f"중복되었습니다: {candidate_id}"
            )

        decision = vote_payload["decision"]

        if decision not in CROSS_REVIEW_DECISIONS:
            raise SchemaError(
                "허용되지 않은 "
                f"decision입니다: {decision}"
            )

        severity = vote_payload["severity"]

        if severity not in SEVERITIES:
            raise SchemaError(
                "허용되지 않은 "
                f"severity입니다: {severity}"
            )

        confidence = vote_payload[
            "confidence"
        ]

        if (
            isinstance(confidence, bool)
            or not isinstance(
                confidence,
                (int, float),
            )
            or not 0.0
            <= float(confidence)
            <= 1.0
        ):
            raise SchemaError(
                "교차평가 confidence는 "
                "0.0 이상 1.0 이하 "
                "숫자여야 합니다."
            )

        votes_by_candidate[candidate_id] = (
            CrossReviewVote(
                provider=provider,
                candidate_id=candidate_id,
                decision=decision,
                reason=_required_text(
                    vote_payload,
                    "reason",
                    max_length=400,
                ),
                severity=severity,
                message=_required_text(
                    vote_payload,
                    "message",
                    max_length=500,
                ),
                confidence=float(
                    confidence
                ),
            )
        )

    received_id_set = set(
        votes_by_candidate
    )

    if received_id_set != expected_id_set:
        missing_ids = sorted(
            expected_id_set
            - received_id_set
        )

        raise SchemaError(
            f"{provider}가 일부 후보를 "
            "평가하지 않았습니다: "
            f"{missing_ids}"
        )

    return [
        votes_by_candidate[candidate_id]
        for candidate_id
        in ordered_candidate_ids
    ]