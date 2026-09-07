import json
import re
from dataclasses import asdict, dataclass
from typing import Any


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