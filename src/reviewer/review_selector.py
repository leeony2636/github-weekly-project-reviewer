import json
import math
from dataclasses import dataclass

from reviewer.github_service import (
    PullRequestFile,
    PullRequestReviewInput,
)


class ReviewSelectionError(ValueError):
    pass


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


def _build_selected_prompt(
    review_input: PullRequestReviewInput,
    *,
    provider: str,
    selected_files: list[PullRequestFile],
    max_chars: int,
) -> str:
    selected_names = {
        item.filename
        for item in selected_files
    }

    payload = {
        "task": (
            "선택된 PR Diff를 다른 모델의 판단에 "
            "의존하지 말고 독립적으로 검토하세요. "
            "finding의 evidence에는 지적한 라인의 "
            "실제 코드를 정확히 복사하세요."
        ),
        "provider": provider,
        "pull_request": {
            "repository": review_input.repository,
            "number": review_input.number,
            "title": review_input.title,
            "base_sha": review_input.base_sha,
            "head_sha": review_input.head_sha,
            "diff_sha256": review_input.diff_sha256,
        },
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


def select_review_inputs(
    review_input: PullRequestReviewInput,
    *,
    gpt_percent: int = 20,
    gemini_percent: int = 10,
    cloud_input_char_limit: int = 30_000,
) -> ReviewSelection:
    if not 1 <= gpt_percent <= 100:
        raise ReviewSelectionError(
            "gpt_percent는 1 이상 100 이하여야 합니다."
        )

    if not 1 <= gemini_percent <= 100:
        raise ReviewSelectionError(
            "gemini_percent는 1 이상 100 이하여야 합니다."
        )

    if cloud_input_char_limit < 2_000:
        raise ReviewSelectionError(
            "cloud_input_char_limit는 "
            "2000 이상이어야 합니다."
        )

    qwen_files = list(review_input.files)

    gpt_files = _select_files(
        review_input.files,
        provider="gpt",
        percent=gpt_percent,
    )
    gemini_files = _select_files(
        review_input.files,
        provider="gemini",
        percent=gemini_percent,
    )

    provider_prompts = {
        "qwen": review_input.build_model_prompt(),
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