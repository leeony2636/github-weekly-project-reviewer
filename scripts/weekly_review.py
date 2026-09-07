import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


load_dotenv()


# GitHub Secrets에서 전달받는 인증정보
GITHUB_TOKEN = os.environ["TARGET_GITHUB_TOKEN"].strip()
HF_TOKEN = os.environ["HF_TOKEN"].strip()


# 분석 대상 저장소
TARGET_REPO = os.getenv(
    "TARGET_REPO",
    "leeony2636/docker-fastapi-sentiment-api",
)


# GitHub API
GITHUB_API = "https://api.github.com"


# Hugging Face Router + Qwen 모델
HF_API = "https://router.huggingface.co/featherless-ai/v1/chat/completions"
HF_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


github_headers = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}


hf_headers = {
    "Authorization": f"Bearer {HF_TOKEN}",
    "Content-Type": "application/json",
}


def get_recent_pull_requests():
    """최근 Pull Request 목록을 가져온다."""
    url = f"{GITHUB_API}/repos/{TARGET_REPO}/pulls"

    params = {
        "state": "all",
        "sort": "updated",
        "direction": "desc",
        "per_page": 10,
    }

    response = requests.get(
        url,
        headers=github_headers,
        params=params,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def get_recent_commits():
    """최근 커밋 목록을 가져온다."""
    url = f"{GITHUB_API}/repos/{TARGET_REPO}/commits"

    params = {
        "per_page": 15,
    }

    response = requests.get(
        url,
        headers=github_headers,
        params=params,
        timeout=30,
    )

    response.raise_for_status()
    return response.json()


def build_project_summary(pull_requests, commits):
    """PR과 커밋 정보를 Hugging Face에 보낼 텍스트로 정리한다."""
    lines = [
        f"분석 대상 저장소: {TARGET_REPO}",
        "",
        "최근 Pull Request:",
    ]

    if pull_requests:
        for pull_request in pull_requests:
            title = pull_request.get("title", "")
            number = pull_request.get("number", "")
            state = pull_request.get("state", "")

            lines.append(
                f"- #{number} {title} ({state})"
            )
    else:
        lines.append("- 최근 Pull Request 없음")

    lines.extend(
        [
            "",
            "최근 커밋:",
        ]
    )

    if commits:
        for commit in commits:
            message = commit["commit"]["message"].split("\n")[0]
            lines.append(f"- {message}")
    else:
        lines.append("- 최근 커밋 없음")

    # 너무 긴 요청을 방지한다.
    return "\n".join(lines)[:6000]


def request_huggingface_analysis(project_summary):
    """Qwen 모델에 한국어 주간 리뷰를 요청한다."""
    system_prompt = """
당신은 GitHub 프로젝트 주간 리뷰 담당자입니다.

입력된 커밋과 Pull Request 정보만 근거로 한국어 보고서를 작성하세요.

보고서에는 반드시 다음 항목을 포함하세요.

## 이번 주 변경사항
## 잘 진행된 점
## 개선이 필요한 점
## 다음 추천 작업 3가지

입력에 없는 내용은 추측하지 마세요.
투자 판단이나 근거 없는 평가를 하지 마세요.
짧고 명확하게 작성하세요.
""".strip()

    user_prompt = f"""
다음은 GitHub 프로젝트의 최근 활동입니다.

{project_summary}

위 정보를 바탕으로 한국어 주간 프로젝트 리뷰를 작성하세요.
""".strip()

    payload = {
        "model": HF_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "max_tokens": 700,
        "temperature": 0.2,
    }

    response = requests.post(
        HF_API,
        headers=hf_headers,
        json=payload,
        timeout=120,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Hugging Face API 오류 "
            f"{response.status_code}: {response.text[:1000]}"
        )

    result = response.json()

    try:
        report = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"Hugging Face 응답 형식 오류: {str(result)[:1000]}"
        ) from exc

    return report.strip()


def create_github_issue(report):
    """생성된 주간 리뷰를 대상 저장소의 Issue로 등록한다."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    title = f"Weekly Project Review - {today}"

    body = f"""
# Weekly Project Review

- Repository: `{TARGET_REPO}`
- Generated at: {today} UTC
- AI Model: `{HF_MODEL}`

---

{report}

---

이 리포트는 `github-weekly-project-reviewer`에 의해 자동 생성되었습니다.
""".strip()

    url = f"{GITHUB_API}/repos/{TARGET_REPO}/issues"

    payload = {
        "title": title,
        "body": body,
    }

    response = requests.post(
        url,
        headers=github_headers,
        json=payload,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub Issue 생성 오류 "
            f"{response.status_code}: {response.text[:1000]}"
        )

    issue = response.json()

    print(f"GitHub Issue 생성 완료: {issue['html_url']}")


def main():
    print(f"분석 대상 저장소: {TARGET_REPO}")
    print(f"사용 모델: {HF_MODEL}")

    pull_requests = get_recent_pull_requests()
    commits = get_recent_commits()

    print(
        f"수집 완료 - PR: {len(pull_requests)}개, "
        f"커밋: {len(commits)}개"
    )

    project_summary = build_project_summary(
        pull_requests,
        commits,
    )

    report = request_huggingface_analysis(
        project_summary
    )

    create_github_issue(report)


if __name__ == "__main__":
    main()