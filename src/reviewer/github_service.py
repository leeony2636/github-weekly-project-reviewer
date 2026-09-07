import json
from dataclasses import dataclass
from typing import Any, Sequence
from hashlib import sha256

from reviewer.diff_parser import (
    DiffParseError,
    build_changed_line_index,
    build_valid_lines,
    normalize_repository_path,
)

from reviewer.security import (
    SensitiveContentError,
    find_sensitive_content,
    scan_text,
)


class GitHubServiceError(RuntimeError):
    pass


class IncompletePullRequestDiff(GitHubServiceError):
    pass


@dataclass(frozen=True, slots=True)
class PullRequestFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "patch": self.patch,
        }


@dataclass(frozen=True, slots=True)
class PullRequestReviewInput:
    repository: str
    number: int
    title: str
    url: str
    base_sha: str
    head_sha: str
    files: tuple[PullRequestFile, ...]
    valid_lines: dict[str, frozenset[int]]
    changed_lines: dict[str, dict[int, str]]
    diff_sha256: str

    def build_model_prompt(
        self,
        *,
        max_prompt_chars: int = 120_000,
    ) -> str:
        payload = {
            "task": (
                "다음 PR Diff만 검토하고 실제 추가·수정된 "
                "valid_lines에 해당하는 문제만 보고하세요. "
                "각 finding의 evidence에는 지적한 라인의 "
                "실제 코드를 정확히 복사하세요."
            ),
            "pull_request": {
                "repository": self.repository,
                "number": self.number,
                "title": self.title,
                "url": self.url,
                "base_sha": self.base_sha,
                "head_sha": self.head_sha,
                "diff_sha256": self.diff_sha256,
            },
            "valid_lines": {
                filename: sorted(lines)
                for filename, lines in sorted(
                    self.valid_lines.items()
                )
            },
            "files": [
                item.to_payload()
                for item in self.files
            ],
        }

        prompt = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        if len(prompt) > max_prompt_chars:
            raise IncompletePullRequestDiff(
                "PR 입력이 허용 크기를 초과했습니다. "
                "Diff를 임의로 자르지 않고 리뷰를 중단합니다."
            )

        return prompt

def calculate_diff_hash(
    files: Sequence[PullRequestFile],
) -> str:
    digest = sha256()

    for item in sorted(
        files,
        key=lambda value: value.filename,
    ):
        for value in (
            item.filename,
            item.status,
            str(item.additions),
            str(item.deletions),
            item.patch,
        ):
            digest.update(
                value.encode("utf-8")
            )
            digest.update(b"\0")

    return digest.hexdigest()

class GitHubService:
    def __init__(
        self,
        *,
        repository: Any,
        target_repo: str,
        client: Any | None = None,
        max_files: int = 100,
    ) -> None:
        if not target_repo.strip():
            raise ValueError("target_repo가 비어 있습니다.")

        if not 1 <= max_files <= 300:
            raise ValueError(
                "max_files는 1 이상 300 이하여야 합니다."
            )

        self.repository = repository
        self.target_repo = target_repo.strip()
        self.client = client
        self.max_files = max_files

    @classmethod
    def connect(
        cls,
        *,
        token: str,
        target_repo: str,
        timeout_seconds: int = 30,
        max_files: int = 100,
    ) -> "GitHubService":
        if not token.strip():
            raise ValueError(
                "TARGET_GITHUB_TOKEN이 비어 있습니다."
            )

        try:
            from github import Auth, Github

            client = Github(
                auth=Auth.Token(token),
                timeout=timeout_seconds,
            )
            repository = client.get_repo(target_repo)
        except Exception as exc:
            raise GitHubServiceError(
                "GitHub 저장소 연결에 실패했습니다. "
                f"error_type={type(exc).__name__}"
            ) from exc

        return cls(
            repository=repository,
            target_repo=target_repo,
            client=client,
            max_files=max_files,
        )

    def close(self) -> None:
        if self.client is not None:
            close_method = getattr(
                self.client,
                "close",
                None,
            )
            if callable(close_method):
                close_method()

    def __enter__(self) -> "GitHubService":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def collect_pull_request(
        self,
        number: int,
    ) -> PullRequestReviewInput:
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("PR 번호는 정수여야 합니다.")

        if number < 1:
            raise ValueError("PR 번호는 1 이상이어야 합니다.")

        try:
            pull_request = self.repository.get_pull(number)
            raw_files = list(pull_request.get_files())
        except Exception as exc:
            raise GitHubServiceError(
                "Pull Request 조회에 실패했습니다. "
                f"error_type={type(exc).__name__}"
            ) from exc

        if len(raw_files) > self.max_files:
            raise IncompletePullRequestDiff(
                "PR 파일 수가 제한을 초과했습니다. "
                "일부 파일만 분석하지 않고 리뷰를 중단합니다."
            )

        files: list[PullRequestFile] = []

        for raw_file in raw_files:
            filename = normalize_repository_path(
                getattr(raw_file, "filename", "")
            )
            additions = int(
                getattr(raw_file, "additions", 0) or 0
            )
            deletions = int(
                getattr(raw_file, "deletions", 0) or 0
            )
            patch = getattr(raw_file, "patch", None)

            if patch is None:
                if additions > 0:
                    raise IncompletePullRequestDiff(
                        "추가 라인이 있지만 patch가 제공되지 "
                        f"않았습니다: {filename}"
                    )
                patch = ""

            if not isinstance(patch, str):
                raise IncompletePullRequestDiff(
                    f"patch 형식이 올바르지 않습니다: {filename}"
                )

            files.append(
                PullRequestFile(
                    filename=filename,
                    status=str(
                        getattr(raw_file, "status", "")
                    ),
                    additions=additions,
                    deletions=deletions,
                    patch=patch,
                )
            )

        title = str(
            getattr(pull_request, "title", "")
        ).strip()

        sensitive_locations = scan_text(
            file_path="<pull-request-title>",
            content=title,
        )
        sensitive_locations.extend(
            find_sensitive_content(
                [item.to_payload() for item in files]
            )
        )

        if sensitive_locations:
            raise SensitiveContentError(
                sensitive_locations
            )

        file_payloads = [
            item.to_payload()
            for item in files
        ]

        try:
            valid_lines = build_valid_lines(
                file_payloads
            )
            changed_lines = build_changed_line_index(
                file_payloads
            )
        except DiffParseError as exc:
            raise IncompletePullRequestDiff(
                "PR Diff가 불완전하여 리뷰를 중단합니다."
            ) from exc

        diff_sha256 = calculate_diff_hash(files)

        base = getattr(pull_request, "base", None)
        head = getattr(pull_request, "head", None)

        return PullRequestReviewInput(
            repository=self.target_repo,
            number=number,
            title=title,
            url=str(
                getattr(pull_request, "html_url", "")
            ),
            base_sha=str(getattr(base, "sha", "")),
            head_sha=str(getattr(head, "sha", "")),
            files=tuple(files),
            valid_lines=valid_lines,
            changed_lines=changed_lines,
            diff_sha256=diff_sha256,
        )