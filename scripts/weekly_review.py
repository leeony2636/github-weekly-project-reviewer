import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv


load_dotenv()


# GitHub Secrets
GITHUB_TOKEN = os.environ["TARGET_GITHUB_TOKEN"].strip()
HF_TOKEN = os.environ["HF_TOKEN"].strip()


# 기본 설정
TARGET_REPO = os.getenv(
    "TARGET_REPO",
    "leeony2636/docker-fastapi-sentiment-api",
)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
MAX_COMMITS = int(os.getenv("MAX_COMMITS", "20"))
MAX_PULL_REQUESTS = int(os.getenv("MAX_PULL_REQUESTS", "10"))
MAX_HF_ATTEMPTS = int(os.getenv("MAX_HF_ATTEMPTS", "2"))
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "7000"))

FORCE_REVIEW = (os.getenv("FORCE_REVIEW", "false").lower() == "true")

# API 설정
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


class HFRequestError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class HFFormatError(Exception):
    pass


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
            "per_page": MAX_COMMITS,
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
            "per_page": MAX_PULL_REQUESTS,
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
                "body": (pull_request.get("body") or "")[:800],
                "files": [
                    file_info["filename"]
                    for file_info in files
                ],
            }
        )

    return recent_prs


def get_previous_reviews():
    """
    이전 Weekly Project Review Issue를 가져온다.
    이전 리뷰의 생성 시점을 기준으로 중복 분석을 줄인다.
    """
    issues = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/issues",
        params={
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": 20,
        },
    )

    previous_reviews = []

    for issue in issues:
        title = issue.get("title", "")

        if not title.startswith("Weekly Project Review -"):
            continue

        previous_reviews.append(
            {
                "title": title,
                "url": issue["html_url"],
                "state": issue["state"],
                "created_at": issue["created_at"],
                "body": (issue.get("body") or "")[:1800],
            }
        )

    return previous_reviews[:5]

def get_last_review_time(previous_reviews):
    if not previous_reviews:
        return None

    created_at = previous_reviews[0]["created_at"]

    return datetime.fromisoformat(
        created_at.replace("Z", "+00:00")
    )

def build_activity(commits, pull_requests, previous_reviews, since, now):
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
        "previous_reviews": previous_reviews,
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
        raise HFFormatError(
            "AI 응답에서 JSON 객체를 찾지 못했습니다."
        )

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise HFFormatError(
            f"AI 응답 JSON 파싱 실패: {exc}"
        ) from exc


def validate_report(report):
    required_keys = {
        "summary",
        "progress",
        "improvements",
        "next_tasks",
    }

    if not required_keys.issubset(report):
        raise HFFormatError(
            "AI 응답에 필요한 항목이 모두 없습니다."
        )

    if not isinstance(report["summary"], str):
        raise HFFormatError("summary가 문자열이 아닙니다.")

    if not isinstance(report["progress"], list):
        raise HFFormatError("progress가 목록이 아닙니다.")

    if not isinstance(report["improvements"], list):
        raise HFFormatError("improvements가 목록이 아닙니다.")

    if not isinstance(report["next_tasks"], list):
        raise HFFormatError("next_tasks가 목록이 아닙니다.")

    if len(report["improvements"]) > 3:
        report["improvements"] = report["improvements"][:3]

    if len(report["next_tasks"]) > 3:
        report["next_tasks"] = report["next_tasks"][:3]

    return report


def build_prompt(activity, repair=False):
    activity_text = json.dumps(
        activity,
        ensure_ascii=False,
        indent=2,
    )

    if len(activity_text) > MAX_INPUT_CHARS:
        activity_text = activity_text[:MAX_INPUT_CHARS]

    repair_instruction = ""

    if repair:
        repair_instruction = """
이전 응답이 JSON 형식 검증에 실패했습니다.
이번에는 반드시 올바른 JSON 객체만 출력하세요.
Markdown 코드블록과 설명 문장은 출력하지 마세요.
""".strip()

    system_prompt = f"""
당신은 GitHub 프로젝트 주간 리뷰 에이전트입니다.

{repair_instruction}

## 작업 순서

다음 순서로 내부적으로 판단하세요.

1. 최근 커밋과 Pull Request를 확인합니다.
2. 변경된 파일과 실제 근거를 연결합니다.
3. 이전 리뷰에서 이미 제안된 내용인지 확인합니다.
4. 이미 완료된 작업인지 확인합니다.
5. 새롭고 근거가 있는 개선점만 제안합니다.

이 과정을 ReAct 방식의 제한된 검토 흐름으로 사용하세요.
최대 5단계까지만 판단하고, 입력에 없는 내용은 추측하지 마세요.

## Few-shot 예시

좋은 개선점 예시:

{{
  "text": "README에 환경변수 설정 방법을 추가하면 좋습니다.",
  "reason": "사용자가 실행에 필요한 설정을 확인하기 어렵습니다.",
  "evidence": ["README.md"],
  "confidence": 0.86
}}

나쁜 개선점 예시:

{{
  "text": "전체 코드를 대대적으로 리팩터링하세요.",
  "reason": "일반적인 권장사항입니다.",
  "evidence": [],
  "confidence": 0.20
}}

나쁜 예시는 근거가 없으므로 절대 작성하지 마세요.

## 출력 형식

반드시 아래 JSON 객체만 출력하세요.

{{
  "summary": "이번 주 변경사항 요약",
  "progress": [
    "실제 커밋 또는 파일로 확인되는 진행사항"
  ],
  "improvements": [
    {{
      "text": "구체적인 개선점",
      "reason": "개선이 필요한 이유",
      "evidence": [
        "근거가 되는 파일명 또는 URL"
      ],
      "confidence": 0.0
    }}
  ],
  "next_tasks": [
    {{
      "task": "실행 가능한 다음 작업",
      "reason": "추천 이유",
      "evidence": [
        "근거가 되는 파일명 또는 URL"
      ]
    }}
  ]
}}

## 작성 규칙

- 결과는 반드시 한국어로 작성하세요.
- 개선점은 최대 3개만 작성하세요.
- 다음 작업은 최대 3개만 작성하세요.
- 각 개선점에는 반드시 근거를 포함하세요.
- confidence는 0.0부터 1.0 사이의 숫자입니다.
- 근거가 없으면 개선점을 작성하지 마세요.
- 이전 리뷰와 같은 내용은 다시 추천하지 마세요.
- 이미 완료된 작업은 다시 추천하지 마세요.
- 일반적인 조언보다 실제 파일과 커밋을 우선하세요.
- 전체 사고과정은 출력하지 말고, 짧은 판단 근거만 작성하세요.
""".strip()

    user_prompt = f"""
아래 GitHub 활동을 분석하세요.

{activity_text}
""".strip()

    return system_prompt, user_prompt


def call_huggingface(activity, repair=False):
    system_prompt, user_prompt = build_prompt(
        activity,
        repair=repair,
    )

    input_chars = len(system_prompt) + len(user_prompt)
    started_at = time.perf_counter()

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

    try:
        response = requests.post(
            HF_API,
            headers=hf_headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise HFRequestError(
            None,
            f"네트워크 오류: {exc}",
        ) from exc

    elapsed_seconds = time.perf_counter() - started_at

    if response.status_code != 200:
        raise HFRequestError(
            response.status_code,
            response.text[:1000],
        )

    try:
        result = response.json()
        content = result["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HFFormatError(
            f"Hugging Face 응답 형식 오류: {response.text[:1000]}"
        ) from exc

    report = validate_report(
        extract_json(content)
    )

    output_chars = len(content)

    metrics = {
        "elapsed_seconds": round(elapsed_seconds, 2),
        "input_chars": input_chars,
        "output_chars": output_chars,
        "estimated_input_tokens": max(1, input_chars // 4),
        "estimated_output_tokens": max(1, output_chars // 4),
    }

    return report, metrics


def request_review_with_retry(activity):
    """
    최대 2회까지만 호출한다.

    - 정상 응답: 1회
    - JSON 형식 오류: 1회 보정
    - 429, 5xx, 네트워크 오류: 1회 재시도
    - 400, 401, 403: 재시도하지 않음
    """
    attempts = 0
    total_metrics = {
        "attempts": 0,
        "elapsed_seconds": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
    }

    repair = False

    while attempts < MAX_HF_ATTEMPTS:
        attempts += 1
        total_metrics["attempts"] = attempts

        try:
            report, metrics = call_huggingface(
                activity,
                repair=repair,
            )

            total_metrics["elapsed_seconds"] += metrics[
                "elapsed_seconds"
            ]
            total_metrics["estimated_input_tokens"] += metrics[
                "estimated_input_tokens"
            ]
            total_metrics["estimated_output_tokens"] += metrics[
                "estimated_output_tokens"
            ]

            return report, total_metrics

        except HFFormatError as exc:
            if attempts >= MAX_HF_ATTEMPTS:
                raise RuntimeError(
                    f"AI 형식 오류가 반복되었습니다: {exc}"
                ) from exc

            print("AI JSON 형식 오류입니다. 1회 보정 요청을 시도합니다.")
            repair = True

        except HFRequestError as exc:
            status = exc.status_code

            retryable = (
                status is None
                or status == 429
                or status >= 500
            )

            if not retryable or attempts >= MAX_HF_ATTEMPTS:
                raise RuntimeError(
                    f"Hugging Face 호출 실패 "
                    f"status={status}: {exc.message}"
                ) from exc

            print(
                f"일시적 오류 status={status}. "
                "1회 재시도합니다."
            )
            time.sleep(2)

    raise RuntimeError("AI 호출이 완료되지 않았습니다.")


def find_existing_issue(title):
    response = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/issues",
        params={
            "state": "open",
            "per_page": 100,
        },
    )

    for issue in response:
        if issue.get("pull_request"):
            continue

        if issue.get("title") == title:
            return issue

    return None

def list_to_markdown(items, item_type):
    if not items:
        return "- 해당 없음"

    lines = []

    for item in items:
        if isinstance(item, str):
            lines.append(f"- {item}")
            continue

        if item_type == "improvement":
            text = item.get("text", "")
        else:
            text = item.get("task", "")

        reason = item.get("reason", "")
        evidence = item.get("evidence", [])

        line = f"- {text}"

        if reason:
            line += f"\n  - 이유: {reason}"

        if item_type == "improvement":
            confidence = item.get("confidence")

            if confidence is not None:
                try:
                    confidence_text = f"{float(confidence):.2f}"
                    line += f"\n  - 신뢰도: {confidence_text}"
                except (TypeError, ValueError):
                    pass

        if evidence:
            line += (
                "\n  - 근거: "
                + ", ".join(evidence)
            )

        lines.append(line)

    return "\n".join(lines)


def make_issue_body(report, activity, metrics):
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
> 이 보고서는 AI가 생성한 초안입니다.
> 실제 코드와 변경 내용을 확인한 뒤 작업을 결정해야 합니다.
> 에이전트가 코드를 자동으로 수정하거나 Issue를 자동 종료하지 않습니다.

## 분석 정보

- Repository: `{TARGET_REPO}`
- 분석 기간: 최근 {LOOKBACK_DAYS}일
- AI 모델: `{HF_MODEL}`
- AI 호출 횟수: {metrics["attempts"]}회
- 예상 입력 토큰: 약 {metrics["estimated_input_tokens"]}개
- 예상 출력 토큰: 약 {metrics["estimated_output_tokens"]}개
- 처리 시간: {metrics["elapsed_seconds"]:.2f}초

## 이번 주 변경사항

{report["summary"]}

## 잘 진행된 점

{list_to_markdown(report["progress"], "progress")}

## 개선이 필요한 점

{list_to_markdown(report["improvements"], "improvement")}

## 다음 추천 작업

{list_to_markdown(report["next_tasks"], "task")}

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
    lookback_since = now - timedelta(days=LOOKBACK_DAYS)

    previous_reviews = get_previous_reviews()
    last_review_time = get_last_review_time(previous_reviews)

    if FORCE_REVIEW:
        since = lookback_since
        print("강제 리뷰 모드입니다.")
    else:
        if last_review_time and last_review_time > lookback_since:
            since = last_review_time
        else:
            since = lookback_since

    print(f"분석 대상: {TARGET_REPO}")
    print(f"분석 시작: {since.isoformat()}")

    commits = get_recent_commits(since)
    pull_requests = get_recent_pull_requests(since)

    if not commits and not pull_requests and not FORCE_REVIEW:
        print("최근 변경사항이 없습니다.")
        print("AI 호출과 Issue 생성을 건너뜁니다.")
        return

    issue_date = now.strftime("%Y-%m-%d")
    issue_title = f"Weekly Project Review - {issue_date}"

    if find_existing_issue(issue_title):
        print("같은 날짜의 열린 Issue가 이미 있습니다.")
        print("중복 생성을 건너뜁니다.")
        return

    activity = build_activity(
        commits=commits,
        pull_requests=pull_requests,
        previous_reviews=previous_reviews,
        since=since,
        now=now,
    )

    report, metrics = request_review_with_retry(activity)

    print(
        f"AI 호출 완료: {metrics['attempts']}회, "
        f"{metrics['elapsed_seconds']:.2f}초"
    )

    issue_body = make_issue_body(
        report,
        activity,
        metrics,
    )

    create_issue(
        issue_title,
        issue_body,
    )


if __name__ == "__main__":
    main()
