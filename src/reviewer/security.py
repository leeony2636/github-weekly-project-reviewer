import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SENSITIVE_PATTERNS = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?"
            r"PRIVATE KEY-----"
        ),
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "openai_key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "generic_secret_assignment",
        re.compile(
            r"""(?ix)
            \b(
                api[_-]?key
                |access[_-]?token
                |auth[_-]?token
                |client[_-]?secret
                |password
                |passwd
                |private[_-]?key
                |secret
            )\b
            \s*[:=]\s*
            ["']?
            [A-Za-z0-9_./+=-]{16,}
            """
        ),
    ),
    (
        "email_address",
        re.compile(
            r"\b[A-Za-z0-9._%+-]+"
            r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        ),
    ),
)


BLOCKED_FILE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
}


BLOCKED_FILE_NAMES = {
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}


@dataclass(frozen=True, slots=True)
class SensitiveLocation:
    kind: str
    file: str
    content_line: int | None


class SensitiveContentError(RuntimeError):
    def __init__(
        self,
        locations: list[SensitiveLocation],
    ) -> None:
        self.locations = tuple(locations)

        safe_locations = ", ".join(
            (
                f"{item.file}:{item.content_line}"
                if item.content_line is not None
                else item.file
            )
            + f"({item.kind})"
            for item in self.locations
        )

        super().__init__(
            "외부 전송이 차단되었습니다. "
            f"민감정보 의심 위치: {safe_locations}"
        )


def _check_file_name(
    file_path: str,
) -> list[SensitiveLocation]:
    normalized = file_path.replace("\\", "/")
    base_name = normalized.rsplit("/", 1)[-1].lower()

    locations: list[SensitiveLocation] = []

    is_env_file = (
        base_name == ".env"
        or (
            base_name.startswith(".env.")
            and base_name != ".env.example"
        )
    )

    if is_env_file:
        locations.append(
            SensitiveLocation(
                kind="environment_file",
                file=normalized,
                content_line=None,
            )
        )

    if base_name in BLOCKED_FILE_NAMES:
        locations.append(
            SensitiveLocation(
                kind="credential_file",
                file=normalized,
                content_line=None,
            )
        )

    if any(
        base_name.endswith(suffix)
        for suffix in BLOCKED_FILE_SUFFIXES
    ):
        locations.append(
            SensitiveLocation(
                kind="key_or_certificate_file",
                file=normalized,
                content_line=None,
            )
        )

    return locations


def scan_text(
    *,
    file_path: str,
    content: str | None,
) -> list[SensitiveLocation]:
    locations = _check_file_name(file_path)

    if not content:
        return locations

    for line_number, line in enumerate(
        content.splitlines(),
        start=1,
    ):
        for kind, pattern in SENSITIVE_PATTERNS:
            if pattern.search(line):
                locations.append(
                    SensitiveLocation(
                        kind=kind,
                        file=file_path,
                        content_line=line_number,
                    )
                )

    return locations


def find_sensitive_content(
    files: Iterable[Mapping[str, Any]],
) -> list[SensitiveLocation]:
    locations: list[SensitiveLocation] = []

    for item in files:
        file_path = str(item.get("filename", "")).strip()
        patch = item.get("patch")

        if not file_path:
            continue

        locations.extend(
            scan_text(
                file_path=file_path,
                content=patch if isinstance(patch, str) else None,
            )
        )

    return locations


def assert_safe_for_external_transfer(
    files: Iterable[Mapping[str, Any]],
) -> None:
    locations = find_sensitive_content(files)

    if locations:
        raise SensitiveContentError(locations)