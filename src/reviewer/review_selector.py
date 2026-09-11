import json
import math
from dataclasses import dataclass

from reviewer.github_service import (
    PullRequestFile,
    PullRequestReviewInput,
)
from reviewer.weekly_service import WeeklyReviewInput


class ReviewSelectionError(ValueError):
    pass


ReviewInput = PullRequestReviewInput | WeeklyReviewInput


@dataclass(frozen=True, slots=True)
class ReviewSelection:
    provider_prompts: dict[str, str]
    selected_files: dict[str, tuple[str, ...]]


GPT_PATH_KEYWORDS = {
    "auth",
    "login",
    "payment",
    "billing",
    "database",
    "service",
    "controller",
    "api",
    "worker",
    "transaction",
}

GPT_CODE_KEYWORDS = {
    "if ",
    "else:",
    "except",
    "raise ",
    "return ",
    "async ",
    "await ",
    "transaction",
    "commit(",
    "rollback(",
    "none",
    "null",
}

GEMINI_PATH_KEYWORDS = {
    "dockerfile",
    "docker-compose",
    "requirements",
    "workflow",
    ".github",
    "config",
    "settings",
    "schema",
    "migration",
    "package.json",
    "pyproject",
}

GEMINI_CODE_KEYWORDS = {
    "import ",
    "from ",
    "environment",
    "os.getenv",
    "os.environ",
    "depends_on",
    "permissions:",
    "runs-on:",
    "version",
    "migration",
}


def _risk_score(
    file: PullRequestFile,
    *,
    provider: str,
) -> int:
    path = file.filename.lower()
    patch = file.patch.lower()

    if provider == "gpt":
        path_keywords = GPT_PATH_KEYWORDS
        code_keywords = GPT_CODE_KEYWORDS
    elif provider == "gemini":
        path_keywords = GEMINI_PATH_KEYWORDS
        code_keywords = GEMINI_CODE_KEYWORDS
    else:
        return 0

    score = 0

    for keyword in path_keywords:
        if keyword in path:
            score += 10

    for keyword in code_keywords:
        if keyword in patch:
            score += 3

    if file.status in {"added", "renamed"}:
        score += 2

    score += min(
        file.additions + file.deletions,
        100,
    ) // 10

    return score


def _selection_count(
    total_files: int,
    percent: int,
) -> int:
    if total_files == 0:
        return 0

    return max(
        1,
        math.ceil(total_files * percent / 100),
    )


def _select_files(
    files: tuple[PullRequestFile, ...],
    *,
    provider: str,
    percent: int,
) -> list[PullRequestFile]:
    count = _selection_count(
        len(files),
        percent,
    )

    ranked = sorted(
        files,
        key=lambda item: (
            -_risk_score(
                item,
                provider=provider,
            ),
            -(item.additions + item.deletions),
            item.filename,
        ),
    )

    return ranked[:count]


def _build_review_context(
    review_input: ReviewInput,
) -> tuple[str, str, dict[str, object]]:
    if isinstance(
        review_input,
        WeeklyReviewInput,
    ):
        return (
            (
                "선택된 최근 주간 누적 Diff를 다른 모델의 "
                "판단에 의존하지 말고 독립적으로 검토하세요. "
                "finding의 evidence에는 지적한 라인의 "
                "실제 코드를 정확히 복사하세요."
            ),
            "weekly_review",
            {
                "repository": (
                    review_input.repository
                ),
                "since": (
                    review_input.since.isoformat()
                ),
                "until": (
                    review_input.until.isoformat()
                ),
                "base_sha": review_input.base_sha,
                "head_sha": review_input.head_sha,
                "diff_sha256": (
                    review_input.diff_sha256
                ),
                "commit_count": len(
                    review_input.commits
                ),
                "pull_request_count": len(
                    review_input.pull_requests
                ),
            },
        )

    return (
        (
            "선택된 PR Diff를 다른 모델의 판단에 "
            "의존하지 말고 독립적으로 검토하세요. "
            "finding의 evidence에는 지적한 라인의 "
            "실제 코드를 정확히 복사하세요."
        ),
        "pull_request",
        {
            "repository": review_input.repository,
            "number": review_input.number,
            "title": review_input.title,
            "url": review_input.url,
            "base_sha": review_input.base_sha,
            "head_sha": review_input.head_sha,
            "diff_sha256": (
                review_input.diff_sha256
            ),
        },
    )


def _build_selected_prompt(
    review_input: ReviewInput,
    *,
    provider: str,
    selected_files: list[PullRequestFile],
    max_chars: int,
) -> str:
    selected_names = {
        item.filename
        for item in selected_files
    }

    task, context_name, context = (
        _build_review_context(review_input)
    )

    payload = {
        "task": task,
        "provider": provider,
        context_name: context,
        "valid_lines": {
            filename: sorted(lines)
            for filename, lines in sorted(
                review_input.valid_lines.items()
            )
            if filename in selected_names
        },
        "files": [
            item.to_payload()
            for item in selected_files
        ],
    }

    prompt = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )

    if len(prompt) > max_chars:
        raise ReviewSelectionError(
            f"{provider} 입력이 무료 한도용 "
            f"최대 크기 {max_chars}자를 초과했습니다."
        )

    return prompt

def _build_weekly_core_prompt(
    review_input: WeeklyReviewInput,
    *,
    max_chars: int,
) -> str:
    """주간 리뷰에서는 핵심 산출물만 짧게 전달한다."""

    # 1차 주간 리뷰가 전체 4800자를 다 쓰지 않도록 제한
    weekly_limit = min(max_chars, 2_200)

    # main.py는 실제 실행 진입점이므로 우선순위가 가장 높음
    main_content = review_input.core_files.get(
        "main.py",
        "",
    )

    # README는 프로젝트 목적과 주요 기능 확인용
    readme_content = review_input.core_files.get(
        "README.md",
        "",
    )

    # README는 상단 핵심 설명만 사용
    readme_content = readme_content[:800]

    payload = {
        "task": (
            "프로젝트의 현재 상태를 짧게 평가하세요. "
            "세부 버그 전체를 찾지 말고, "
            "목적과 구조의 큰 불일치, 주요 위험, "
            "다음 개선 우선순위만 판단하세요."
        ),

        # 기존 주간 리뷰 메타정보 유지
        "weekly_review": {
            "repository": review_input.repository,
            "since": review_input.since.isoformat(),
            "until": review_input.until.isoformat(),
            "base_sha": review_input.base_sha,
            "head_sha": review_input.head_sha,
            "diff_sha256": review_input.diff_sha256,
            "commit_count": len(review_input.commits),
            "pull_request_count": len(
                review_input.pull_requests
            ),
        },

        # 실제 모델이 집중해서 볼 핵심 산출물
        "core_files": {
            "main.py": main_content,
            "README.md": readme_content,
        },
    }

    prompt = json.dumps(
        payload,
        ensure_ascii=False,
    )

    # main.py까지 포함했을 때 제한을 넘으면
    # main.py도 마지막 부분만 잘라서 맞춤
    if len(prompt) > weekly_limit:
        overflow = len(prompt) - weekly_limit

        keep_length = max(
            500,
            len(main_content) - overflow - 50,
        )

        payload["core_files"]["main.py"] = (
            main_content[:keep_length]
        )

        prompt = json.dumps(
            payload,
            ensure_ascii=False,
        )

    # 그래도 제한을 넘는 경우에만 중단
    if len(prompt) > weekly_limit:
        raise ReviewSelectionError(
            "주간 핵심 리뷰 입력을 "
            f"{weekly_limit}자 이하로 만들 수 없습니다."
        )

    return prompt

def _merge_unique_files(
    *groups: list[PullRequestFile],
) -> list[PullRequestFile]:
    merged: list[PullRequestFile] = []
    seen_names: set[str] = set()

    for group in groups:
        for item in group:
            if item.filename in seen_names:
                continue

            seen_names.add(item.filename)
            merged.append(item)

    return merged


def select_review_inputs(
    review_input: ReviewInput,
    *,
    gpt_percent: int = 20,
    gemini_percent: int = 10,
    qwen_input_char_limit: int = 4_800,
    cloud_input_char_limit: int = 4_800,
) -> ReviewSelection:
    if not 1 <= gpt_percent <= 100:
        raise ReviewSelectionError(
            "gpt_percent는 1 이상 100 이하여야 합니다."
        )

    if not 1 <= gemini_percent <= 100:
        raise ReviewSelectionError(
            "gemini_percent는 1 이상 100 이하여야 합니다."
        )

    if qwen_input_char_limit < 2_000:
        raise ReviewSelectionError(
            "qwen_input_char_limit는 "
            "2000 이상이어야 합니다."
        )

    if cloud_input_char_limit < 2_000:
        raise ReviewSelectionError(
            "cloud_input_char_limit는 "
            "2000 이상이어야 합니다."
        )

    # 주간 리뷰는 최근 Diff 전체 대신
    # main.py + README.md 핵심 산출물만 검토한다.
    if isinstance(review_input, WeeklyReviewInput):
        common_input_char_limit = min(
            qwen_input_char_limit,
            cloud_input_char_limit,
        )

        common_prompt = _build_weekly_core_prompt(
            review_input,
            max_chars=common_input_char_limit,
        )

        # 실제로 수집된 핵심 파일명만 기록
        core_file_names = tuple(
            filename
            for filename in ("main.py", "README.md")
            if filename in review_input.core_files
        )

        return ReviewSelection(
            provider_prompts={
                "qwen": common_prompt,
                "gpt": common_prompt,
                "gemini": common_prompt,
            },
            selected_files={
                "qwen": core_file_names,
                "gpt": core_file_names,
                "gemini": core_file_names,
            },
        )

    code_risk_files = _select_files(
        review_input.files,
        provider="gpt",
        percent=gpt_percent,
    )

    configuration_risk_files = _select_files(
        review_input.files,
        provider="gemini",
        percent=gemini_percent,
    )

    common_files = _merge_unique_files(
        code_risk_files,
        configuration_risk_files,
    )

    common_input_char_limit = min(
        qwen_input_char_limit,
        cloud_input_char_limit,
    )

    common_prompt = _build_selected_prompt(
        review_input,
        provider="shared",
        selected_files=common_files,
        max_chars=common_input_char_limit,
    )

    common_file_names = tuple(
        item.filename
        for item in common_files
    )

    return ReviewSelection(
        provider_prompts={
            "qwen": common_prompt,
            "gpt": common_prompt,
            "gemini": common_prompt,
        },
        selected_files={
            "qwen": common_file_names,
            "gpt": common_file_names,
            "gemini": common_file_names,
        },
    )

    selected_files = {
        "qwen": tuple(
            item.filename
            for item in qwen_files
        ),
        "gpt": tuple(
            item.filename
            for item in gpt_files
        ),
        "gemini": tuple(
            item.filename
            for item in gemini_files
        ),
    }

    return ReviewSelection(
        provider_prompts=provider_prompts,
        selected_files=selected_files,
    )