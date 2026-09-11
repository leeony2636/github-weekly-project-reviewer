import argparse
import json
import os
import sys
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from reviewer.config import ConfigError, RuntimeConfig
from reviewer.github_service import (
    GitHubServiceError,
    IncompletePullRequestDiff,
)
from reviewer.orchestrator import (
    OrchestrationError,
    ReviewRun,
)
from reviewer.review_selector import (
    ReviewSelection,
    select_review_inputs,
)
from reviewer.security import SensitiveContentError
from reviewer.trace_store import TraceStore
from reviewer.weekly_service import (
    WeeklyCollectionLimitError,
    WeeklyGitHubService,
    WeeklyReviewInput,
)
from scripts.pr_review import (
    create_orchestrator,
    write_output,
)


KST = timezone(timedelta(hours=9))
SAFE_INPUT_CHAR_LIMIT = 4_800


def parse_arguments(
    arguments: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "최근 7일 GitHub 변경사항을 세 모델로 "
            "검토하고 한국어 주간 이슈를 만듭니다."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="검토할 최근 일수입니다. 기본값은 7입니다.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="검토 결과를 GitHub Issue로 등록합니다.",
    )

    args = parser.parse_args(arguments)

    if not 1 <= args.days <= 31:
        raise ValueError(
            "days는 1 이상 31 이하여야 합니다."
        )

    return args


def make_issue_title(
    until: datetime,
) -> str:
    local_date = until.astimezone(
        KST
    ).strftime("%Y-%m-%d")

    return f"주간 프로젝트 리뷰 - {local_date}"


def make_subject_id(
    review_input: WeeklyReviewInput,
) -> str:
    return (
        f"{review_input.since.date()}_"
        f"{review_input.until.date()}"
    )


def _single_line(value: str) -> str:
    return " ".join(
        str(value).replace(
            "\r",
            " ",
        ).replace(
            "\n",
            " ",
        ).split()
    )


def _commit_markdown(
    review_input: WeeklyReviewInput,
) -> str:
    if not review_input.commits:
        return "- 최근 커밋이 없습니다."

    return "\n".join(
        (
            f"- [`{commit.sha[:12]}`]"
            f"({commit.url}) "
            f"{_single_line(commit.message)}"
        )
        for commit in review_input.commits
    )


def _pull_request_markdown(
    review_input: WeeklyReviewInput,
) -> str:
    if not review_input.pull_requests:
        return "- 최근 Pull Request가 없습니다."

    return "\n".join(
        (
            f"- [#{pull_request.number}]"
            f"({pull_request.url}) "
            f"{_single_line(pull_request.title)} "
            f"(`{pull_request.state}`)"
        )
        for pull_request
        in review_input.pull_requests
    )


def _finding_markdown(
    result: ReviewRun,
) -> str:
    if not result.consensus_findings:
        return (
            "- 두 모델 이상이 같은 변경 라인에서 "
            "합의한 문제를 발견하지 못했습니다."
        )

    sections: list[str] = []

    for index, finding in enumerate(
        result.consensus_findings,
        start=1,
    ):
        providers = ", ".join(
            finding.providers
        )
        evidence = finding.evidence.strip()

        sections.append(
            "\n".join(
                [
                    (
                        f"### {index}. "
                        f"[{finding.severity}] "
                        f"{_single_line(finding.message)}"
                    ),
                    "",
                    (
                        f"- 위치: `{finding.file}:"
                        f"{finding.line}`"
                    ),
                    (
                        f"- 분류: `{finding.category}`"
                    ),
                    (
                        f"- 합의 모델: {providers} "
                        f"({finding.match_count}개)"
                    ),
                    (
                        f"- 신뢰도: "
                        f"{finding.confidence:.2f}"
                    ),
                    (
                        f"- 판단 이유: "
                        f"{_single_line(finding.reason)}"
                    ),
                    "- 실제 변경 근거:",
                    "",
                    "```text",
                    evidence,
                    "```",
                ]
            )
        )

    return "\n\n".join(sections)

def _raw_findings_markdown(
    result: ReviewRun,
) -> str:
    """각 모델의 1차 의견을 짧게 표시한다."""

    if not result.raw_findings:
        return "- 모델별 1차 의견이 없습니다."

    sections: list[str] = []

    # provider별로 묶기
    for provider in ("qwen", "gpt", "gemini"):
        provider_findings = [
            finding
            for finding in result.raw_findings
            if finding.provider == provider
        ]

        sections.append(
            f"### {provider.upper()}"
        )

        if not provider_findings:
            sections.append(
                "- 별도 지적 없음"
            )
            sections.append("")
            continue

        # 출력이 너무 길어지지 않도록 최대 3건만 표시
        for finding in provider_findings[:3]:
            sections.append(
                (
                    f"- [{finding.severity}] "
                    f"`{finding.file}:{finding.line}` "
                    f"{_single_line(finding.message)}"
                )
            )
            sections.append(
                (
                    f"  - 이유: "
                    f"{_single_line(finding.reason)}"
                )
            )

        sections.append("")

    return "\n".join(sections)

def _cross_review_markdown(
    result: ReviewRun,
) -> str:
    """모델 간 교차검증 결과를 짧게 표시한다."""

    if not result.cross_review_votes:
        return "- 교차검증 의견이 없습니다."

    lines: list[str] = []

    # 너무 길어지지 않도록 최대 9개까지만 표시
    for vote in result.cross_review_votes[:9]:
        lines.append(
            (
                f"- {vote.provider.upper()} "
                f"→ `{vote.candidate_id}` "
                f": **{vote.decision}** "
                f"(신뢰도 {vote.confidence:.2f})"
            )
        )

        lines.append(
            f"  - 이유: {_single_line(vote.reason)}"
        )

    return "\n".join(lines)

def _final_summary_markdown(
    result: ReviewRun,
) -> str:
    """모델별 의견과 교차검증 결과를 간단히 종합한다."""

    lines: list[str] = []

    raw_count = len(result.raw_findings)
    vote_count = len(result.cross_review_votes)
    consensus_count = len(result.consensus_findings)

    lines.append(
        f"- 모델별 1차 지적: {raw_count}건"
    )
    lines.append(
        f"- 교차검증 의견: {vote_count}건"
    )
    lines.append(
        f"- 최종 합의 항목: {consensus_count}건"
    )

    if consensus_count > 0:
        lines.append(
            "- 결론: 두 모델 이상이 동의한 개선 항목이 있습니다."
        )
        lines.append(
            "- 우선순위: 최종 합의 항목부터 확인하는 것이 좋습니다."
        )
    elif raw_count > 0:
        lines.append(
            "- 결론: 개별 모델의 지적은 있었지만 "
            "최종 합의까지 이어진 항목은 없습니다."
        )
        lines.append(
            "- 해석: 모델별 관점 차이가 있었으므로 "
            "1차 의견과 교차검증 내용을 함께 확인하세요."
        )
    else:
        lines.append(
            "- 결론: 세 모델 모두 별도 문제를 제시하지 않았습니다."
        )

    return "\n".join(lines)

def _selected_files_markdown(
    selection: ReviewSelection | None,
) -> str:
    if selection is None:
        return "- 변경 파일이 없어 모델을 호출하지 않았습니다."

    lines: list[str] = []

    for provider in (
        "qwen",
        "gpt",
        "gemini",
    ):
        files = selection.selected_files.get(
            provider,
            (),
        )
        file_text = (
            ", ".join(
                f"`{filename}`"
                for filename in files
            )
            if files
            else "선택된 파일 없음"
        )
        lines.append(
            f"- {provider}: {file_text}"
        )

    return "\n".join(lines)


def _model_status_markdown(
    result: ReviewRun,
) -> str:
    if not result.successful_providers:
        return "- 변경 파일이 없어 모델 호출을 생략했습니다."

    successful = ", ".join(
        result.successful_providers
    )
    lines = [
        f"- 성공한 모델: {successful}",
    ]

    if result.provider_failures:
        failures = ", ".join(
            (
                f"{provider}"
                f"({error_type})"
            )
            for provider, error_type
            in sorted(
                result.provider_failures.items()
            )
        )
        lines.append(
            f"- 실패한 모델: {failures}"
        )
    else:
        lines.append("- 실패한 모델: 없음")

    lines.extend(
        [
            (
                "- 모델 원본 지적 수: "
                f"{result.raw_finding_count}"
            ),
            (
                "- 실제 변경 라인이 아니어서 제외: "
                f"{result.invalid_line_count}"
            ),
            (
                "- 실제 코드 근거가 달라서 제외: "
                f"{result.invalid_evidence_count}"
            ),
            (
                "- 최종 합의 항목 수: "
                f"{len(result.consensus_findings)}"
            ),
        ]
    )

    return "\n".join(lines)


def make_issue_body(
    *,
    review_input: WeeklyReviewInput,
    result: ReviewRun,
    selection: ReviewSelection | None,
) -> str:
    since = review_input.since.astimezone(
        KST
    ).strftime("%Y-%m-%d %H:%M")
    until = review_input.until.astimezone(
        KST
    ).strftime("%Y-%m-%d %H:%M")

    return f"""# 주간 프로젝트 리뷰

## 검토 범위

- 저장소: `{review_input.repository}`
- 기간: {since} ~ {until} (한국 시간)
- 커밋: {len(review_input.commits)}개
- Pull Request: {len(review_input.pull_requests)}개
- 변경 파일: {len(review_input.files)}개
- 기준 커밋: `{review_input.base_sha or "없음"}`
- 최종 커밋: `{review_input.head_sha or "없음"}`

## 모델별 1차 의견

{_raw_findings_markdown(result)}

## 모델 간 교차검증

{_cross_review_markdown(result)}

## 최종 교차검증 결과

{_finding_markdown(result)}

## 최종 종합 의견

{_final_summary_markdown(result)}

## 모델별 검토 파일

{_selected_files_markdown(selection)}

## 모델 실행 상태

{_model_status_markdown(result)}

## 이번 주 커밋

{_commit_markdown(review_input)}

## 이번 주 Pull Request

{_pull_request_markdown(review_input)}

---

이 보고서는 모델의 자유로운 코드 분석 결과를 그대로 승인하지 않습니다.
실제 변경 라인과 근거 코드를 검증한 뒤, 최소 두 모델이 같은 위치에서
합의한 항목만 최종 결과에 포함합니다.
"""


def make_output_payload(
    *,
    issue_title: str,
    issue_body: str,
    review_input: WeeklyReviewInput,
    result: ReviewRun,
    selection: ReviewSelection | None,
    published: bool,
) -> dict[str, object]:
    return {
        "review_type": "weekly",
        "repository": review_input.repository,
        "period": {
            "since": (
                review_input.since.isoformat()
            ),
            "until": (
                review_input.until.isoformat()
            ),
        },
        "base_sha": review_input.base_sha,
        "head_sha": review_input.head_sha,
        "diff_sha256": review_input.diff_sha256,
        "commit_count": len(
            review_input.commits
        ),
        "pull_request_count": len(
            review_input.pull_requests
        ),
        "changed_file_count": len(
            review_input.files
        ),
        "selected_files": (
            {
                provider: list(files)
                for provider, files
                in sorted(
                    selection.selected_files.items()
                )
            }
            if selection is not None
            else {}
        ),
        "successful_providers": list(
            result.successful_providers
        ),
        "provider_failures": (
            result.provider_failures
        ),
        "quota_snapshot": (
            result.quota_snapshot
        ),
        "raw_finding_count": (
            result.raw_finding_count
        ),
        "invalid_line_count": (
            result.invalid_line_count
        ),
        "invalid_evidence_count": (
            result.invalid_evidence_count
        ),
        "consensus_count": len(
            result.consensus_findings
        ),
        "findings": [
            finding.to_dict()
            for finding
            in result.consensus_findings
        ],
        "issue": {
            "title": issue_title,
            "body": issue_body,
            "published": published,
        },
    }


def find_existing_issue(
    repository: Any,
    title: str,
) -> Any | None:
    issues = repository.get_issues(
        state="all",
        sort="created",
        direction="desc",
    )

    for index, issue in enumerate(issues):
        if index >= 50:
            break

        if getattr(
            issue,
            "pull_request",
            None,
        ) is not None:
            continue

        if str(issue.title) == title:
            return issue

    return None


def publish_issue(
    *,
    repository: Any,
    title: str,
    body: str,
) -> str:
    issue = repository.create_issue(
        title=title,
        body=body,
    )

    return str(
        getattr(issue, "html_url", "")
    )


def empty_review_result() -> ReviewRun:
    return ReviewRun(
        consensus_findings=(),
        successful_providers=(),
        provider_failures={},
        raw_finding_count=0,
        invalid_line_count=0,
        invalid_evidence_count=0,
        degraded=False,
        quota_snapshot={},
    )


def _require_model_secrets(
    config: RuntimeConfig,
) -> None:
    missing = []

    if not config.hf_token:
        missing.append("HF_TOKEN")

    if not config.cohere_api_key:
        missing.append("COHERE_API_KEY")

    if not config.gemini_api_key:
        missing.append("GEMINI_API_KEY")

    if missing:
        raise ConfigError(
            "변경 파일 검토에 필요한 Secret이 "
            "없습니다: "
            + ", ".join(missing)
        )

def make_weekly_cache_key(
    *,
    config: RuntimeConfig,
    selection: ReviewSelection,
    review_input: WeeklyReviewInput,
) -> str:
    prompt_hashes = {
        provider: hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        for provider, prompt
        in sorted(
            selection.provider_prompts.items()
        )
    }

    identity = {
        "schema_version": 1,
        "review_type": "weekly",
        "repository": review_input.repository,
        "base_sha": review_input.base_sha,
        "head_sha": review_input.head_sha,
        "diff_sha256": (
            review_input.diff_sha256
        ),
        "models": {
            "qwen": config.qwen_model,
            "gpt": config.gpt_model,
            "gemini": config.gemini_model,
        },
        "prompt_hashes": prompt_hashes,
        "line_tolerance": (
            config.line_tolerance
        ),
        "min_match_count": (
            config.min_match_count
        ),
    }

    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


def get_cached_issue_body(
    payload: dict[str, Any],
) -> str | None:
    if payload.get("review_type") != "weekly":
        return None

    issue = payload.get("issue")

    if not isinstance(issue, dict):
        return None

    body = issue.get("body")

    if (
        not isinstance(body, str)
        or not body.strip()
    ):
        return None

    return body


def make_failure_issue_body(
    *,
    repository: str,
    since: datetime,
    until: datetime,
    error_type: str,
) -> str:
    since_text = since.astimezone(
        KST
    ).strftime("%Y-%m-%d %H:%M")
    until_text = until.astimezone(
        KST
    ).strftime("%Y-%m-%d %H:%M")

    return f"""# 주간 프로젝트 리뷰 실행 상태

## 검토 범위

- 저장소: `{repository}`
- 기간: {since_text} ~ {until_text} (한국 시간)
- 실행 결과: 검토 미완료
- 오류 유형: `{error_type}`

## 안내

이번 주 자동 코드 검토가 완료되지 않았습니다.

코드 내용, API 키, 토큰, 오류 메시지와 같은 민감할 수 있는
정보는 이 이슈에 포함하지 않았습니다.

무료 할당량, 외부 모델 상태 또는 GitHub 연결 상태를 확인한 뒤
수동으로 다시 실행해야 합니다.
"""


def make_failure_output_payload(
    *,
    repository: str,
    since: datetime,
    until: datetime,
    issue_title: str,
    issue_body: str,
    error_type: str,
    published: bool,
) -> dict[str, object]:
    return {
        "review_type": "weekly",
        "status": "failed",
        "repository": repository,
        "period": {
            "since": since.isoformat(),
            "until": until.isoformat(),
        },
        "error_type": error_type,
        "issue": {
            "title": issue_title,
            "body": issue_body,
            "published": published,
        },
    }

def finish_failed_review(
    *,
    repository: Any | None,
    repository_name: str,
    since: datetime,
    until: datetime,
    issue_title: str,
    error: Exception,
    publish: bool,
    output_path: str,
    trace_store: TraceStore,
    run_id: str | None,
    trace_status: str,
) -> None:
    if run_id is not None:
        trace_store.fail_run(
            run_id=run_id,
            status=trace_status,
            error=error,
        )

    issue_body = make_failure_issue_body(
        repository=repository_name,
        since=since,
        until=until,
        error_type=type(error).__name__,
    )

    published = False

    if publish and repository is not None:
        try:
            existing_issue = find_existing_issue(
                repository,
                issue_title,
            )

            if existing_issue is not None:
                published = True
            else:
                publish_issue(
                    repository=repository,
                    title=issue_title,
                    body=issue_body,
                )
                published = True
        except Exception as publish_error:
            print(
                "실패 상태 이슈 생성도 실패했습니다. "
                f"error_type="
                f"{type(publish_error).__name__}"
            )

    payload = make_failure_output_payload(
        repository=repository_name,
        since=since,
        until=until,
        issue_title=issue_title,
        issue_body=issue_body,
        error_type=type(error).__name__,
        published=published,
    )

    payload["cache_hit"] = False

    try:
        write_output(
            output_path,
            payload,
        )
    except Exception as output_error:
        print(
            "실패 결과 JSON 저장에 실패했습니다. "
            f"error_type="
            f"{type(output_error).__name__}"
        )

def run_weekly_review(
    *,
    config: RuntimeConfig,
    trace_store: TraceStore,
    until: datetime,
    days: int,
    publish: bool,
) -> int:
    run_id: str | None = None
    github_service: WeeklyGitHubService | None = None

    since = until - timedelta(days=days)
    issue_title = make_issue_title(until)

    output_path = os.getenv(
        "WEEKLY_REVIEW_OUTPUT_PATH",
        "weekly_review_result.json",
    )

    try:
        github_service = WeeklyGitHubService.connect(
            token=config.target_github_token,
            target_repo=config.target_repo,
            max_files=50,
        )

        if publish:
            existing_issue = find_existing_issue(
                github_service.repository,
                issue_title,
            )

            if existing_issue is not None:
                print(
                    "같은 날짜의 주간 이슈가 "
                    "이미 있어 모델을 호출하지 않았습니다."
                )
                return 0

        review_input = (
            github_service.collect_weekly_review(
                since=since,
                until=until,
            )
        )

        selection: ReviewSelection | None = None
        cache_key: str | None = None

        if review_input.files:
            _require_model_secrets(config)

            selection = select_review_inputs(
                review_input,
                gpt_percent=(
                    config.gpt_review_percent
                ),
                gemini_percent=(
                    config.gemini_review_percent
                ),
                qwen_input_char_limit=min(
                    config.qwen_input_char_limit,
                    SAFE_INPUT_CHAR_LIMIT,
                ),
                cloud_input_char_limit=min(
                    config.cloud_input_char_limit,
                    SAFE_INPUT_CHAR_LIMIT,
                ),
            )

            cache_key = make_weekly_cache_key(
                config=config,
                selection=selection,
                review_input=review_input,
            )

            if config.cache_enabled:
                cached_payload = (
                    trace_store.get_cached_payload(
                        cache_key
                    )
                )
                cached_body = (
                    get_cached_issue_body(
                        cached_payload
                    )
                    if cached_payload is not None
                    else None
                )

                if (
                    cached_payload is not None
                    and cached_body is not None
                ):
                    published = False

                    if publish:
                        publish_issue(
                            repository=(
                                github_service.repository
                            ),
                            title=issue_title,
                            body=cached_body,
                        )
                        published = True

                    output_payload = dict(
                        cached_payload
                    )
                    cached_issue = dict(
                        output_payload["issue"]
                    )
                    cached_issue["title"] = (
                        issue_title
                    )
                    cached_issue["published"] = (
                        published
                    )

                    output_payload["issue"] = (
                        cached_issue
                    )
                    output_payload["cache_hit"] = True

                    write_output(
                        output_path,
                        output_payload,
                    )

                    if published:
                        print(
                            "저장된 검토 결과로 "
                            "주간 이슈를 생성했습니다. "
                            "모델 API 호출 없음."
                        )
                    else:
                        print(
                            "저장된 검토 결과를 "
                            "사용했습니다. "
                            "모델 API 호출 없음."
                        )

                    return 0

        run_id = trace_store.start_run(
            repository=config.target_repo,
            pull_request_number=0,
            run_kind="weekly",
            subject_id=make_subject_id(
                review_input
            ),
            diff_sha256=(
                review_input.diff_sha256
            ),
        )

        if review_input.files:
            if selection is None:
                raise RuntimeError(
                    "모델 검토 파일 선택 결과가 없습니다."
                )

            orchestrator = create_orchestrator(
                config,
                selection,
            )
            result = orchestrator.run(
                provider_prompts=(
                    selection.provider_prompts
                ),
                valid_lines=(
                    review_input.valid_lines
                ),
                changed_lines=(
                    review_input.changed_lines
                ),
            )

            if result.provider_failures:
                result = replace(
                    result,
                    degraded=True,
                )

            if (
                result.degraded
                and not config.degraded_mode_on_quota
            ):
                raise OrchestrationError(
                    "일부 모델이 실패했고 "
                    "제한 모드가 비활성화되어 "
                    "결과를 승인하지 않습니다."
                )
        else:
            result = empty_review_result()

        issue_body = make_issue_body(
            review_input=review_input,
            result=result,
            selection=selection,
        )

        published = False

        if publish:
            issue_url = publish_issue(
                repository=(
                    github_service.repository
                ),
                title=issue_title,
                body=issue_body,
            )
            published = True

            print(
                "주간 이슈 생성 완료: "
                f"{issue_url}"
            )
        else:
            print(
                "미리보기 완료: GitHub Issue는 "
                "생성하지 않았습니다."
            )

        payload = make_output_payload(
            issue_title=issue_title,
            issue_body=issue_body,
            review_input=review_input,
            result=result,
            selection=selection,
            published=published,
        )
        payload["cache_hit"] = False

        write_output(
            output_path,
            payload,
        )

        trace_store.complete_run(
            run_id=run_id,
            result=result,
        )

        if (
            config.cache_enabled
            and cache_key is not None
        ):
            trace_store.save_cached_payload(
                cache_key=cache_key,
                payload=payload,
            )

        return 0

    except SensitiveContentError as exc:
        if run_id is not None:
            trace_store.fail_run(
                run_id=run_id,
                status="blocked_sensitive_content",
                error=exc,
            )

        print(
            "민감정보로 의심되는 내용이 있어 "
            "외부 모델 전송과 이슈 생성을 "
            "차단했습니다."
        )
        return 2

    except (
        IncompletePullRequestDiff,
        WeeklyCollectionLimitError,
    ) as exc:
        finish_failed_review(
            repository=(
                github_service.repository
                if github_service is not None
                else None
            ),
            repository_name=config.target_repo,
            since=since,
            until=until,
            issue_title=issue_title,
            error=exc,
            publish=publish,
            output_path=output_path,
            trace_store=trace_store,
            run_id=run_id,
            trace_status=(
                "blocked_incomplete_diff"
            ),
        )

        print(
            "주간 변경사항이 제한을 초과하거나 "
            "불완전하여 검토를 중단했습니다."
        )
        return 3

    except OrchestrationError as exc:
        finish_failed_review(
            repository=(
                github_service.repository
                if github_service is not None
                else None
            ),
            repository_name=config.target_repo,
            since=since,
            until=until,
            issue_title=issue_title,
            error=exc,
            publish=publish,
            output_path=output_path,
            trace_store=trace_store,
            run_id=run_id,
            trace_status="failed",
        )

        print(
            "교차검증에 필요한 모델 수가 "
            f"부족했습니다. {exc}"
        )
        return 4

    except Exception as exc:
        finish_failed_review(
            repository=(
                github_service.repository
                if github_service is not None
                else None
            ),
            repository_name=config.target_repo,
            since=since,
            until=until,
            issue_title=issue_title,
            error=exc,
            publish=publish,
            output_path=output_path,
            trace_store=trace_store,
            run_id=run_id,
            trace_status="failed",
        )

        print(
            "주간 리뷰 실행 실패: "
            f"error_type={type(exc).__name__}"
        )
        return 1

    finally:
        if github_service is not None:
            github_service.close()


def main(
    arguments: Sequence[str] | None = None,
) -> int:
    try:
        args = parse_arguments(arguments)
        config = RuntimeConfig.from_env(
            require_secrets=False
        )

        if not config.target_github_token:
            raise ConfigError(
                "TARGET_GITHUB_TOKEN 또는 "
                "GITHUB_TOKEN이 필요합니다."
            )
    except (ConfigError, ValueError) as exc:
        print(str(exc))
        return 1

    until = datetime.now(timezone.utc)
    database_path = os.getenv(
        "REVIEW_DB_PATH",
        "review_trace.db",
    )

    with TraceStore(database_path) as trace_store:
        return run_weekly_review(
            config=config,
            trace_store=trace_store,
            until=until,
            days=args.days,
            publish=args.publish,
        )


if __name__ == "__main__":
    sys.exit(main())