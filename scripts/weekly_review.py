import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv


load_dotenv()


GITHUB_TOKEN = os.environ["TARGET_GITHUB_TOKEN"].strip()
HF_TOKEN = os.environ["HF_TOKEN"].strip()

TARGET_REPO = os.getenv(
    "TARGET_REPO",
    "leeony2636/docker-fastapi-sentiment-api",
)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))

GITHUB_API = "https://api.github.com"
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


def github_request(method, path, params=None, body=None):
    response = requests.request(
        method=method,
        url=f"{GITHUB_API}{path}",
        headers=github_headers,
        params=params,
        json=body,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"GitHub API 오류 {response.status_code}: "
            f"{response.text[:1000]}"
        )

    if not response.text:
        return None

    return response.json()


def get_recent_commits(since):
    commits = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/commits",
        params={
            "since": since.isoformat(),
            "per_page": 30,
        },
    )

    return [
        {
            "message": commit["commit"]["message"].split("\n")[0],
            "sha": commit["sha"][:7],
            "url": commit["html_url"],
            "date": commit["commit"]["author"]["date"],
        }
        for commit in commits
    ]


def get_recent_pull_requests(since):
    pull_requests = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/pulls",
        params={
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": 20,
        },
    )

    recent_prs = []

    for pull_request in pull_requests:
        updated_at = datetime.fromisoformat(
            pull_request["updated_at"].replace("Z", "+00:00")
        )

        if updated_at < since:
            continue

        files = github_request(
            "GET",
            (
                f"/repos/{TARGET_REPO}/pulls/"
                f"{pull_request['number']}/files"
            ),
            params={"per_page": 30},
        )

        recent_prs.append(
            {
                "number": pull_request["number"],
                "title": pull_request["title"],
                "state": pull_request["state"],
                "url": pull_request["html_url"],
                "body": (pull_request.get("body") or "")[:1000],
                "files": [
                    file_info["filename"]
                    for file_info in files
                ],
            }
        )

    return recent_prs


def build_activity(commits, pull_requests, since, now):
    changed_files = set()

    for pull_request in pull_requests:
        changed_files.update(pull_request["files"])

    return {
        "repository": TARGET_REPO,
        "period": {
            "from": since.isoformat(),
            "to": now.isoformat(),
        },
        "commits": commits,
        "pull_requests": pull_requests,
        "changed_files": sorted(changed_files),
    }


def extract_json(text):
    text = text.strip()

    fenced_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        re.DOTALL,
    )

    if fenced_match:
        text = fenced_match.group(1)

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise RuntimeError("AI 응답에서 JSON 객체를 찾지 못했습니다.")

    return json.loads(text[start:end + 1])


def request_huggingface_review(activity):
    system_prompt = """
당신은 GitHub 프로젝트 주간 리뷰 담당자입니다.

반드시 JSON 객체만 출력하세요.
Markdown 코드블록이나 설명 문장을 JSON 앞뒤에 붙이지 마세요.

JSON 형식:
{
  "summary": "이번 주 변경사항 요약",
  "progress": ["잘 진행된 점"],
  "improvements": [
    {
      "text": "개선점",
      "evidence": ["근거가 되는 파일 또는 URL"]
    }
  ],
  "next_tasks": [
    {
      "task": "추천 작업",
      "reason": "추천 이유",
      "evidence": ["근거가 되는 파일 또는 URL"]
    }
  ]
}

규칙:
- 입력에 없는 내용은 추측하지 마세요.
- 실제 커밋, PR, 파일에 근거해서만 작성하세요.
- 최대 3개의 개선점만 작성하세요.
- 다음 작업은 최대 3개만 작성하세요.
- 이미 완료된 작업을 다시 추천하지 마세요.
- 결과는 한국어로 작성하세요.
""".strip()

    user_prompt = json.dumps(
        activity,
        ensure_ascii=False,
        indent=2,
    )

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
            f"Hugging Face API 오류 {response.status_code}: "
            f"{response.text[:1000]}"
        )

    result = response.json()

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"Hugging Face 응답 형식 오류: {str(result)[:1000]}"
        ) from exc

    report = extract_json(content)

    required_keys = {
        "summary",
        "progress",
        "improvements",
        "next_tasks",
    }

    if not required_keys.issubset(report):
        raise RuntimeError(
            "AI 응답에 필요한 항목이 모두 포함되지 않았습니다."
        )

    return report


def find_existing_issue(title):
    issues = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/issues",
        params={
            "state": "all",
            "per_page": 100,
        },
    )

    return any(
        issue["title"] == title
        for issue in issues
    )


def make_issue_body(report, activity):
    def make_list(items, key):
        if not items:
            return "- 해당 없음"

        lines = []

        for item in items:
            if isinstance(item, dict):
                text = item.get(key, "")
                evidence = item.get("evidence", [])
                line = f"- {text}"

                if evidence:
                    line += "\n  - 근거: " + ", ".join(evidence)

                lines.append(line)
            else:
                lines.append(f"- {item}")

        return "\n".join(lines)

    commits = activity["commits"]
    pull_requests = activity["pull_requests"]

    commit_lines = "\n".join(
        f"- [{commit['sha']}]({commit['url']}) "
        f"{commit['message']}"
        for commit in commits
    ) or "- 해당 없음"

    pr_lines = "\n".join(
        f"- [#{pr['number']}]({pr['url']}) {pr['title']}"
        for pr in pull_requests
    ) or "- 해당 없음"

    return f"""
> 이 보고서는 최근 변경사항을 바탕으로 생성한 AI 초안입니다.
> 실제 코드와 변경 내용을 확인한 뒤 작업을 결정해야 합니다.
> 에이전트가 코드를 자동으로 수정하거나 Issue를 자동으로 종료하지 않습니다.

## 분석 대상

- Repository: `{TARGET_REPO}`
- 분석 기간: 최근 {LOOKBACK_DAYS}일
- AI 모델: `{HF_MODEL}`

## 이번 주 변경사항

{report["summary"]}

## 잘 진행된 점

{make_list(report["progress"], "text")}

## 개선이 필요한 점

{make_list(report["improvements"], "text")}

## 다음 추천 작업

{make_list(report["next_tasks"], "task")}

## 근거 커밋

{commit_lines}

## 관련 Pull Request

{pr_lines}

---

이 Issue는 `github-weekly-project-reviewer`에 의해 자동 생성되었습니다.
""".strip()


def create_issue(title, body):
    issue = github_request(
        "POST",
        f"/repos/{TARGET_REPO}/issues",
        body={
            "title": title,
            "body": body,
            "labels": [
                "ai-generated",
                "needs-review",
                "weekly-report",
            ],
        },
    )

    print(f"Issue 생성 완료: {issue['html_url']}")


def main():
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)

    print(f"분석 대상: {TARGET_REPO}")
    print(f"분석 기간: {since.isoformat()} 이후")

    commits = get_recent_commits(since)
    pull_requests = get_recent_pull_requests(since)

    if not commits and not pull_requests:
        print("최근 변경사항이 없어 AI 호출과 Issue 생성을 건너뜁니다.")
        return

    issue_date = now.strftime("%Y-%m-%d")
    issue_title = f"Weekly Project Review - {issue_date}"

    if find_existing_issue(issue_title):
        print("같은 날짜의 Issue가 이미 있어 중복 생성을 건너뜁니다.")
        return

    activity = build_activity(
        commits=commits,
        pull_requests=pull_requests,
        since=since,
        now=now,
    )

    report = request_huggingface_review(activity)
    issue_body = make_issue_body(report, activity)

    create_issue(issue_title, issue_body)


if __name__ == "__main__":
    main()