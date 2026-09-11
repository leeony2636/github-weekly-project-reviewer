from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from reviewer.diff_parser import (
    DiffParseError,
    build_changed_line_index,
    build_valid_lines,
)
from reviewer.github_service import (
    GitHubService,
    GitHubServiceError,
    IncompletePullRequestDiff,
    PullRequestFile,
    calculate_diff_hash,
)
from reviewer.security import (
    SensitiveContentError,
    find_sensitive_content,
    scan_text,
)


class WeeklyCollectionLimitError(GitHubServiceError):
    pass


@dataclass(frozen=True, slots=True)
class WeeklyCommit:
    sha: str
    message: str
    url: str
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class WeeklyPullRequest:
    number: int
    title: str
    url: str
    state: str
    updated_at: datetime
    merged_at: datetime | None


@dataclass(frozen=True, slots=True)
class WeeklyReviewInput:
    repository: str
    since: datetime
    until: datetime
    base_sha: str
    head_sha: str
    commits: tuple[WeeklyCommit, ...]
    pull_requests: tuple[WeeklyPullRequest, ...]
    files: tuple[PullRequestFile, ...]
    valid_lines: dict[str, frozenset[int]]
    changed_lines: dict[str, dict[int, str]]
    diff_sha256: str
# 기존 테스트/호출 코드와의 호환성을 위해 기본값 제공
    core_files: dict[str, str] = field(default_factory=dict)

def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _first(items: Iterable[Any]) -> Any | None:
    return next(iter(items), None)


class WeeklyGitHubService(GitHubService):
    def __init__(
        self,
        *,
        repository: Any,
        target_repo: str,
        client: Any | None = None,
        max_files: int = 50,
        max_commits: int = 30,
        max_pull_requests: int = 20,
    ) -> None:
        super().__init__(
            repository=repository,
            target_repo=target_repo,
            client=client,
            max_files=max_files,
        )

        if not 1 <= max_commits <= 100:
            raise ValueError(
                "max_commits는 1 이상 100 이하여야 합니다."
            )

        if not 1 <= max_pull_requests <= 100:
            raise ValueError(
                "max_pull_requests는 1 이상 100 이하여야 합니다."
            )

        self.max_commits = max_commits
        self.max_pull_requests = max_pull_requests

    def _collect_core_files(
        self,
    ) -> dict[str, str]:
        """주간 리뷰에 사용할 핵심 산출물만 가져온다."""

        # AI가 항상 확인할 핵심 파일
        core_paths = (
            "main.py",
            "README.md",
        )

        core_files: dict[str, str] = {}

        for path in core_paths:
            try:
                # 저장소 기본 브랜치의 최신 파일 가져오기
                item = self.repository.get_contents(
                    path,
                    ref=self.repository.default_branch,
                )

                # 폴더가 아니라 파일인지 확인
                decoded_content = getattr(
                    item,
                    "decoded_content",
                    None,
                )

                if decoded_content is None:
                    continue

                # GitHub에서 받은 bytes → 문자열 변환
                content = decoded_content.decode(
                    "utf-8",
                    errors="replace",
                )

                core_files[path] = content

            except Exception:
                # 핵심 파일 하나가 없더라도
                # 전체 주간 리뷰 수집을 바로 중단하지 않음
                continue

        return core_files

    def _collect_commits(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[list[Any], tuple[WeeklyCommit, ...]]:
        raw_commits: list[Any] = []
        commits: list[WeeklyCommit] = []

        items = self.repository.get_commits(
            sha=self.repository.default_branch,
            since=since,
            until=until,
        )

        for index, item in enumerate(items):
            if index >= self.max_commits:
                raise WeeklyCollectionLimitError(
                    "주간 커밋 수가 제한을 초과했습니다. "
                    "일부 커밋만 외부 모델에 보내지 않고 "
                    "이번 검토를 중단합니다."
                )

            committed_at = _as_utc(
                item.commit.author.date
            )
            message = str(
                item.commit.message
            ).splitlines()[0].strip()

            raw_commits.append(item)
            commits.append(
                WeeklyCommit(
                    sha=str(item.sha),
                    message=message,
                    url=str(item.html_url),
                    committed_at=committed_at,
                )
            )

        return raw_commits, tuple(commits)

    def _collect_pull_requests(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[WeeklyPullRequest, ...]:
        pull_requests: list[WeeklyPullRequest] = []

        items = self.repository.get_pulls(
            state="all",
            sort="updated",
            direction="desc",
        )

        for item in items:
            updated_at = _as_utc(item.updated_at)

            if updated_at < since:
                break

            if updated_at > until:
                continue

            if len(pull_requests) >= self.max_pull_requests:
                raise WeeklyCollectionLimitError(
                    "주간 Pull Request 수가 제한을 "
                    "초과했습니다. 일부 항목만 외부 모델에 "
                    "보내지 않고 이번 검토를 중단합니다."
                )

            merged_at = getattr(
                item,
                "merged_at",
                None,
            )

            pull_requests.append(
                WeeklyPullRequest(
                    number=int(item.number),
                    title=str(item.title).strip(),
                    url=str(item.html_url),
                    state=str(item.state),
                    updated_at=updated_at,
                    merged_at=(
                        _as_utc(merged_at)
                        if merged_at is not None
                        else None
                    ),
                )
            )

        return tuple(pull_requests)

    def _resolve_comparison(
        self,
        *,
        since: datetime,
        until: datetime,
        recent_commits: list[Any],
    ) -> tuple[str, str]:
        branch = self.repository.default_branch

        head_commit = _first(
            self.repository.get_commits(
                sha=branch,
                until=until,
            )
        )

        if head_commit is None:
            return "", ""

        base_commit = _first(
            self.repository.get_commits(
                sha=branch,
                until=since,
            )
        )

        if base_commit is not None:
            return (
                str(base_commit.sha),
                str(head_commit.sha),
            )

        if recent_commits:
            oldest_commit = recent_commits[-1]
            parents = list(
                getattr(oldest_commit, "parents", ())
            )

            if parents:
                return (
                    str(parents[0].sha),
                    str(head_commit.sha),
                )

        return (
            str(head_commit.sha),
            str(head_commit.sha),
        )

    def _collect_changed_files(
        self,
        *,
        base_sha: str,
        head_sha: str,
    ) -> tuple[PullRequestFile, ...]:
        if (
            not base_sha
            or not head_sha
            or base_sha == head_sha
        ):
            return ()

        comparison = self.repository.compare(
            base_sha,
            head_sha,
        )
        raw_files = list(comparison.files)

        if len(raw_files) > self.max_files:
            raise WeeklyCollectionLimitError(
                "주간 변경 파일 수가 제한을 초과했습니다. "
                "일부 파일만 외부 모델에 보내지 않고 "
                "이번 검토를 중단합니다."
            )

        files: list[PullRequestFile] = []

        for item in raw_files:
            filename = str(item.filename).strip()
            additions = int(item.additions or 0)
            deletions = int(item.deletions or 0)
            patch = getattr(item, "patch", None)

            if patch is None:
                if additions > 0:
                    raise IncompletePullRequestDiff(
                        "추가 라인이 있지만 GitHub에서 "
                        f"patch를 제공하지 않았습니다: {filename}"
                    )

                patch = ""

            if not isinstance(patch, str):
                raise IncompletePullRequestDiff(
                    "patch 형식이 올바르지 않습니다: "
                    f"{filename}"
                )

            files.append(
                PullRequestFile(
                    filename=filename,
                    status=str(item.status),
                    additions=additions,
                    deletions=deletions,
                    patch=patch,
                )
            )

        return tuple(files)

    @staticmethod
    def _assert_safe(
        *,
        commits: tuple[WeeklyCommit, ...],
        pull_requests: tuple[WeeklyPullRequest, ...],
        files: tuple[PullRequestFile, ...],
        core_files: dict[str, str],
    ) -> None:
        locations = []

        for commit in commits:
            locations.extend(
                scan_text(
                    file_path="<commit-message>",
                    content=commit.message,
                )
            )

        for pull_request in pull_requests:
            locations.extend(
                scan_text(
                    file_path="<pull-request-title>",
                    content=pull_request.title,
                )
            )

        locations.extend(
            find_sensitive_content(
                [item.to_payload() for item in files]
            )
        )

        # 핵심 산출물(main.py, README.md)도 민감정보 검사
        for file_path, content in core_files.items():
            locations.extend(
                scan_text(
                    file_path=file_path,
                    content=content,
                )
            )
        
        if locations:
            raise SensitiveContentError(locations)

    def collect_weekly_review(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> WeeklyReviewInput:
        since = _as_utc(since)
        until = _as_utc(until)

        if since >= until:
            raise ValueError(
                "주간 검토 시작 시간은 종료 시간보다 "
                "이전이어야 합니다."
            )

        try:
            raw_commits, commits = (
                self._collect_commits(
                    since=since,
                    until=until,
                )
            )
            pull_requests = (
                self._collect_pull_requests(
                    since=since,
                    until=until,
                )
            )
            base_sha, head_sha = (
                self._resolve_comparison(
                    since=since,
                    until=until,
                    recent_commits=raw_commits,
                )
            )
            files = self._collect_changed_files(
                base_sha=base_sha,
                head_sha=head_sha,
            )
            core_files = self._collect_core_files()
        except (
            WeeklyCollectionLimitError,
            IncompletePullRequestDiff,
        ):
            raise
        except Exception as exc:
            raise GitHubServiceError(
                "주간 GitHub 변경사항 수집에 실패했습니다. "
                f"error_type={type(exc).__name__}"
            ) from exc

        self._assert_safe(
            commits=commits,
            pull_requests=pull_requests,
            files=files,
            core_files=core_files,
        )

        file_payloads = [
            item.to_payload()
            for item in files
        ]

        try:
            valid_lines = build_valid_lines(
                file_payloads
            )
            changed_lines = (
                build_changed_line_index(
                    file_payloads
                )
            )
        except DiffParseError as exc:
            raise IncompletePullRequestDiff(
                "주간 누적 Diff가 불완전하여 "
                "외부 모델 검토를 중단합니다."
            ) from exc

        return WeeklyReviewInput(
            repository=self.target_repo,
            since=since,
            until=until,
            base_sha=base_sha,
            head_sha=head_sha,
            commits=commits,
            pull_requests=pull_requests,
            files=files,
            valid_lines=valid_lines,
            changed_lines=changed_lines,
            diff_sha256=calculate_diff_hash(files),
            core_files=core_files,
        )