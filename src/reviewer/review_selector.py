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

    qwen_files = _select_files(
        review_input.files,
        provider="gpt",
        percent=gpt_percent,
    )

    gemini_files = _select_files(
        review_input.files,
        provider="gemini",
        percent=gemini_percent,
    )

    gpt_files = _merge_unique_files(
        qwen_files,
        gemini_files,
    )

    provider_prompts = {
        "qwen": _build_selected_prompt(
            review_input,
            provider="qwen",
            selected_files=qwen_files,
            max_chars=qwen_input_char_limit,
        ),
        "gpt": _build_selected_prompt(
            review_input,
            provider="gpt",
            selected_files=gpt_files,
            max_chars=cloud_input_char_limit,
        ),
        "gemini": _build_selected_prompt(
            review_input,
            provider="gemini",
            selected_files=gemini_files,
            max_chars=cloud_input_char_limit,
        ),
    }

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