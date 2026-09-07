import difflib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from json_repair import repair_json


load_dotenv()


# -----------------------------
# Configuration
# -----------------------------
GITHUB_TOKEN = os.environ["TARGET_GITHUB_TOKEN"].strip()
HF_TOKEN = os.environ["HF_TOKEN"].strip()

TARGET_REPO = os.getenv(
    "TARGET_REPO",
    "leeony2636/docker-fastapi-sentiment-api",
)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
MAX_COMMITS = int(os.getenv("MAX_COMMITS", "20"))
MAX_PULL_REQUESTS = int(os.getenv("MAX_PULL_REQUESTS", "10"))
MAX_HF_ATTEMPTS = min(int(os.getenv("MAX_HF_ATTEMPTS", "2")), 2)
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "6500"))
MAX_PATCH_CHARS = int(os.getenv("MAX_PATCH_CHARS", "1200"))
MAX_FINDINGS = int(os.getenv("MAX_FINDINGS", "5"))
MAX_REACT_STEPS = 4
FORCE_REVIEW = os.getenv("FORCE_REVIEW", "false").lower() == "true"

GITHUB_API = "https://api.github.com"
HF_API = "https://router.huggingface.co/featherless-ai/v1/chat/completions"
HF_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
DB_PATH = Path(os.getenv("REVIEW_DB_PATH", "review_trace.db"))

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


# -----------------------------
# SQLite trace
# -----------------------------
def init_trace_db(run_id):
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            repository TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            force_review INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            file_name TEXT,
            source_text TEXT,
            FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS model_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            latency_seconds REAL,
            status TEXT NOT NULL,
            error_type TEXT,
            FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            finding_text TEXT NOT NULL,
            reason TEXT,
            confidence REAL,
            evidence TEXT,
            review_status TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
        );
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO agent_runs
        (run_id, repository, started_at, model, status, force_review)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            TARGET_REPO,
            datetime.now(timezone.utc).isoformat(),
            HF_MODEL,
            "running",
            int(FORCE_REVIEW),
        ),
    )
    conn.commit()
    conn.close()


def update_run(run_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE agent_runs SET status = ?, finished_at = ? WHERE run_id = ?",
        (status, datetime.now(timezone.utc).isoformat(), run_id),
    )
    conn.commit()
    conn.close()


def record_evidence(run_id, activity):
    conn = sqlite3.connect(DB_PATH)
    rows = []

    for commit in activity["commits"]:
        rows.append(
            (
                run_id,
                "commit",
                commit["sha"],
                None,
                commit.get("message", ""),
            )
        )
        for file_info in commit.get("files", []):
            rows.append(
                (
                    run_id,
                    "commit_file",
                    commit["sha"],
                    file_info.get("filename"),
                    file_info.get("patch", "")[:MAX_PATCH_CHARS],
                )
            )

    for pull_request in activity["pull_requests"]:
        rows.append(
            (
                run_id,
                "pull_request",
                str(pull_request["number"]),
                None,
                pull_request.get("title", ""),
            )
        )

    conn.executemany(
        """
        INSERT INTO evidence
        (run_id, source_type, source_id, file_name, source_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def record_model_call(run_id, attempt, metrics, status, error_type=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO model_calls
        (run_id, attempt, input_tokens, output_tokens, latency_seconds,
         status, error_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            attempt,
            metrics.get("estimated_input_tokens"),
            metrics.get("estimated_output_tokens"),
            metrics.get("elapsed_seconds"),
            status,
            error_type,
        ),
    )
    conn.commit()
    conn.close()


def record_findings(run_id, report):
    conn = sqlite3.connect(DB_PATH)
    rows = []
    for item in report.get("improvements", []):
        rows.append(
            (
                run_id,
                item.get("text", ""),
                item.get("reason", ""),
                item.get("confidence"),
                json.dumps(item.get("evidence", []), ensure_ascii=False),
                "needs-review",
            )
        )

    conn.executemany(
        """
        INSERT INTO findings
        (run_id, finding_text, reason, confidence, evidence, review_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


# -----------------------------
# GitHub API
# -----------------------------
def github_request(method, path, params=None, body=None):
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = f"{GITHUB_API}{path}"

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=github_headers,
            params=params,
            json=body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"GitHub API 네트워크 오류: {exc}") from exc

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
        params={"since": since.isoformat(), "per_page": MAX_COMMITS},
    )

    result = []
    for commit in commits:
        sha = commit["sha"]
        detail = github_request(
            "GET",
            f"/repos/{TARGET_REPO}/commits/{sha}",
        )

        files = []
        for file_info in (detail or {}).get("files", [])[:30]:
            files.append(
                {
                    "filename": file_info.get("filename", ""),
                    "status": file_info.get("status", ""),
                    "patch": (file_info.get("patch") or "")[:MAX_PATCH_CHARS],
                }
            )

        result.append(
            {
                "message": commit["commit"]["message"].split("\n")[0],
                "sha": sha[:7],
                "url": commit["html_url"],
                "date": commit["commit"]["author"]["date"],
                "files": files,
            }
        )

    return result


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
            f"/repos/{TARGET_REPO}/pulls/{pull_request['number']}/files",
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
                    {
                        "filename": item.get("filename", ""),
                        "status": item.get("status", ""),
                        "patch": (item.get("patch") or "")[:MAX_PATCH_CHARS],
                    }
                    for item in files[:30]
                ],
            }
        )

    return recent_prs


def get_previous_reviews():
    issues = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/issues",
        params={
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": 30,
        },
    )

    reviews = []
    for issue in issues:
        title = issue.get("title", "")
        if not title.startswith("Weekly Project Review -"):
            continue

        reviews.append(
            {
                "title": title,
                "url": issue["html_url"],
                "state": issue["state"],
                "created_at": issue["created_at"],
                "body": (issue.get("body") or "")[:2200],
            }
        )

    return reviews[:5]


def get_last_review_time(previous_reviews):
    if not previous_reviews:
        return None

    return datetime.fromisoformat(
        previous_reviews[0]["created_at"].replace("Z", "+00:00")
    )


# -----------------------------
# Bounded evidence loop
# -----------------------------
def build_activity(commits, pull_requests, previous_reviews, since, now):
    changed_files = set()
    evidence_steps = []

    for commit in commits:
        evidence_steps.append(
            {
                "step": len(evidence_steps) + 1,
                "phase": "Observation",
                "action": "inspect_commit",
                "source": commit["sha"],
                "result": f"{len(commit.get('files', []))}개 파일 확인",
            }
        )
        for file_info in commit.get("files", []):
            changed_files.add(file_info["filename"])

    for pull_request in pull_requests:
        evidence_steps.append(
            {
                "step": len(evidence_steps) + 1,
                "phase": "Observation",
                "action": "inspect_pull_request",
                "source": str(pull_request["number"]),
                "result": f"{len(pull_request.get('files', []))}개 파일 확인",
            }
        )
        for file_info in pull_request.get("files", []):
            changed_files.add(file_info["filename"])

    # ReAct-style bounded stop condition: evidence is collected once, no
    # repeated action is allowed, and the loop never exceeds MAX_REACT_STEPS.
    evidence_steps = evidence_steps[:MAX_REACT_STEPS]

    changed_file_names = " ".join(changed_files).lower()
    patch_text = " ".join(
        file_info.get("patch", "")
        for source in [*commits, *pull_requests]
        for file_info in source.get("files", [])
    ).lower()
    static_checks = []

    if "dockerfile" in changed_file_names:
        static_checks.append({
            "text": "Docker 빌드 및 실행 검증 필요",
            "reason": "Dockerfile 변경은 확인되지만 실제 빌드·실행 결과가 근거에 없습니다.",
            "evidence": [name for name in sorted(changed_files) if "dockerfile" in name.lower()],
            "priority": "P1",
            "confidence": 0.75,
        })

    if any(name.lower().endswith(".py") for name in changed_files):
        if not any("test" in name.lower() for name in changed_files):
            static_checks.append({
                "text": "변경된 Python 코드에 대한 테스트 추가 필요",
                "reason": "Python 코드 변경은 있으나 대응하는 테스트 파일 변경이 확인되지 않습니다.",
                "evidence": [
                    name
                    for name in sorted(changed_files)
                    if name.lower().endswith(".py")
                ],
                "priority": "P1",
                "confidence": 0.80,
            })

    if re.search(
        r"(api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"]+['\"]",
        patch_text,
        re.IGNORECASE,
    ):
        static_checks.append({
            "text": "민감정보 하드코딩 여부 확인 필요",
            "reason": "변경 diff에서 API 키·토큰·비밀번호 형태의 문자열이 발견되었습니다.",
            "evidence": [
                name
                for name in sorted(changed_files)
                if re.search(
                    r"(api[_-]?key|password|secret|token)",
                    name,
                    re.IGNORECASE,
                )
            ] or sorted(changed_files),
            "priority": "P0",
            "confidence": 0.90,
        })

    return {
        "repository": TARGET_REPO,
        "period": {"from": since.isoformat(), "to": now.isoformat()},
        "commits": commits,
        "pull_requests": pull_requests,
        "changed_files": sorted(changed_files),
        "previous_reviews": previous_reviews,
        "evidence_steps": evidence_steps,
        "static_checks": static_checks,
    }


# -----------------------------
# Prompt, JSON parsing, validation
# -----------------------------
def build_prompt(activity, repair=False):
    activity_text = json.dumps(activity, ensure_ascii=False, indent=2)
    if len(activity_text) > MAX_INPUT_CHARS:
        activity_text = activity_text[:MAX_INPUT_CHARS]

    repair_instruction = ""
    if repair:
        repair_instruction = """
이전 응답의 JSON 형식이 잘못되었습니다.
이번에는 JSON 객체 하나만 출력하세요.
문장, Markdown, 코드블록, trailing comma를 출력하지 마세요.
모든 문자열 안의 큰따옴표는 JSON 규칙에 맞게 escape하세요.
""".strip()

    system_prompt = f"""
당신은 GitHub 프로젝트 주간 리뷰 Agent입니다.

{repair_instruction}

## 제한된 검토 순서
1. 커밋·PR의 실제 변경 파일과 patch를 확인합니다.
2. 변경사항에 직접 연결되는 근거를 선택합니다.
3. 이전 리뷰와 같은 추천인지 비교합니다.
4. 근거가 충분한 새로운 개선점만 작성합니다.

이 순서는 제한된 ReAct형 검토 흐름입니다. 최대 {MAX_REACT_STEPS}단계이며,
입력에 없는 내용을 추측하지 마세요.

## Few-shot 예시
좋은 결과:
{{
  "text": "README에 실행 환경변수 설명을 추가합니다.",
  "reason": "실행에 필요한 환경변수 사용법이 변경 파일에 명확히 드러나지 않습니다.",
  "evidence": ["README.md"],
  "confidence": 0.86
}}

나쁜 결과:
{{
  "text": "전체 코드를 리팩터링하세요.",
  "reason": "일반적으로 좋은 practice입니다.",
  "evidence": [],
  "confidence": 0.20
}}

나쁜 결과처럼 근거 없는 일반론은 작성하지 마세요.

## 출력 형식
반드시 아래 JSON 객체 하나만 출력하세요.
{{
  "summary": "변경사항 요약",
  "progress": [
    {{
      "text": "실제 변경으로 확인되는 진행사항",
      "evidence": ["파일명 또는 파일:줄번호"],
      "confidence": 0.0
    }}
  ],
  "improvements": [
    {{
      "text": "구체적인 개선점",
      "reason": "개선이 필요한 이유",
      "evidence": ["파일명 또는 URL"],
      "priority": "P1",
      "confidence": 0.0
    }}
  ],
  "next_tasks": [
    {{
      "task": "실행 가능한 다음 작업",
      "reason": "추천 이유",
      "evidence": ["파일명 또는 URL"],
      "priority": "P1"
    }}
  ]
}}

## 규칙
- 모든 결과는 한국어로 작성하세요.
- improvements와 next_tasks는 각각 최대 {MAX_FINDINGS}개입니다.
- 변경 파일 목록만 반복하지 말고 실제 patch 내용을 평가하세요.
- improvements와 next_tasks는 의미가 겹치면 안 됩니다.
- 같은 주제의 작업은 하나로 합치세요.
- 모든 improvement와 next_task에는 실제 근거가 있어야 합니다.
- 근거에는 가능한 경우 파일명과 줄번호를 작성하세요.
- confidence는 0.0 이상 1.0 이하 숫자입니다.
- priority는 P0, P1, P2 중 하나입니다.
- P0는 보안·실행 불가, P1은 기능·테스트 문제, P2는 문서·개선 사항입니다.
- 테스트, Dockerfile, requirements.txt, API 실행 가능성을 확인하세요.
- 이전 리뷰에 이미 나온 추천과 완료된 작업은 반복하지 마세요.
- 확인하지 못한 내용은 "검증 필요"라고 표시하세요.
- 전체 사고과정은 출력하지 말고 짧은 판단 근거만 출력하세요.
""".strip()

    user_prompt = f"아래 GitHub 활동을 분석하세요.\n\n{activity_text}"
    return system_prompt, user_prompt


def extract_json(text):
    text = text.strip()
    fenced_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced_match:
        text = fenced_match.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise HFFormatError("AI 응답에서 JSON 객체를 찾지 못했습니다.")

    candidate = text[start:]

    try:
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(candidate)
        return parsed
    except json.JSONDecodeError as original_error:
        try:
            repaired = json.loads(repair_json(candidate))
            print("AI JSON 형식을 로컬에서 보정했습니다.")
            return repaired
        except (json.JSONDecodeError, TypeError, ValueError) as repair_error:
            raise HFFormatError(
                f"AI 응답 JSON 파싱 실패: {original_error}"
            ) from repair_error


def validate_report(report):
    required_keys = {"summary", "progress", "improvements", "next_tasks"}
    if not isinstance(report, dict) or not required_keys.issubset(report):
        raise HFFormatError("AI 응답에 필요한 항목이 없습니다.")

    if not isinstance(report["summary"], str) or not report["summary"].strip():
        raise HFFormatError("summary가 비어 있습니다.")

    for key in ("progress", "improvements", "next_tasks"):
        if not isinstance(report[key], list):
            raise HFFormatError(f"{key}가 목록이 아닙니다.")

    report["improvements"] = report["improvements"][:MAX_FINDINGS]
    report["next_tasks"] = report["next_tasks"][:MAX_FINDINGS]

    for item in report["improvements"]:
        if not isinstance(item, dict):
            raise HFFormatError("improvements 항목이 객체가 아닙니다.")
        if not item.get("text") or not item.get("evidence"):
            raise HFFormatError("개선점에 text 또는 evidence가 없습니다.")
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise HFFormatError("confidence가 숫자가 아닙니다.") from exc
        if not 0.0 <= confidence <= 1.0:
            raise HFFormatError("confidence는 0과 1 사이여야 합니다.")
        item["confidence"] = confidence

        if item.get("priority") not in {"P0", "P1", "P2"}:
            item["priority"] = "P2"

    for item in report["next_tasks"]:
        if not isinstance(item, dict):
            raise HFFormatError("next_tasks 항목이 객체가 아닙니다.")
        if not item.get("task") or not item.get("evidence"):
            raise HFFormatError("추천 작업에 task 또는 evidence가 없습니다.")
        if item.get("priority") not in {"P0", "P1", "P2"}:
            item["priority"] = "P2"

    return report


# -----------------------------
# Qwen call and retry policy
# -----------------------------
def call_huggingface(activity, repair=False):
    system_prompt, user_prompt = build_prompt(activity, repair=repair)
    input_chars = len(system_prompt) + len(user_prompt)
    started_at = time.perf_counter()

    payload = {
        "model": HF_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 600,
        "temperature": 0.1,
    }

    try:
        response = requests.post(
            HF_API,
            headers=hf_headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise HFRequestError(None, f"네트워크 오류: {exc}") from exc

    elapsed_seconds = time.perf_counter() - started_at
    if response.status_code != 200:
        raise HFRequestError(response.status_code, response.text[:1000])

    try:
        result = response.json()
        content = result["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HFFormatError("Hugging Face 응답 구조가 올바르지 않습니다.") from exc

    report = validate_report(extract_json(content))
    output_chars = len(content)
    metrics = {
        "elapsed_seconds": round(elapsed_seconds, 2),
        "input_chars": input_chars,
        "output_chars": output_chars,
        "estimated_input_tokens": max(1, input_chars // 4),
        "estimated_output_tokens": max(1, output_chars // 4),
    }
    return report, metrics


def request_review_with_retry(activity, run_id):
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
            report, metrics = call_huggingface(activity, repair=repair)
            for key in (
                "elapsed_seconds",
                "estimated_input_tokens",
                "estimated_output_tokens",
            ):
                total_metrics[key] += metrics[key]
            record_model_call(run_id, attempts, metrics, "success")
            return report, total_metrics

        except HFFormatError as exc:
            record_model_call(
                run_id,
                attempts,
                {"elapsed_seconds": 0},
                "format_error",
                type(exc).__name__,
            )
            if attempts >= MAX_HF_ATTEMPTS:
                raise RuntimeError(f"AI 형식 오류가 반복되었습니다: {exc}") from exc
            print("AI JSON 형식 오류입니다. 1회 보정 요청을 시도합니다.")
            repair = True

        except HFRequestError as exc:
            record_model_call(
                run_id,
                attempts,
                {"elapsed_seconds": 0},
                "request_error",
                type(exc).__name__,
            )
            retryable = (
                exc.status_code is None
                or exc.status_code == 429
                or exc.status_code >= 500
            )
            if not retryable or attempts >= MAX_HF_ATTEMPTS:
                raise RuntimeError(
                    f"Hugging Face 호출 실패 status={exc.status_code}: {exc.message}"
                ) from exc
            print(f"일시적 오류 status={exc.status_code}. 1회 재시도합니다.")
            time.sleep(2)

    raise RuntimeError("AI 호출이 완료되지 않았습니다.")


# -----------------------------
# Deterministic duplicate checks
# -----------------------------
def normalize_text(value):
    value = value.lower()
    value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
    return " ".join(value.split())


def previous_suggestion_texts(previous_reviews):
    texts = []
    for review in previous_reviews:
        body = review.get("body", "")
        section = body
        if "## 다음 추천 작업" in body:
            section = body.split("## 다음 추천 작업", 1)[1]
            section = section.split("## 근거 커밋", 1)[0]
        texts.append(normalize_text(section))
    return texts


def remove_duplicate_findings(report, previous_reviews):
    previous_texts = previous_suggestion_texts(previous_reviews)
    improvements = []
    next_tasks = []
    removed = 0
    progress_items = [
        item
        for item in report.get("progress", [])
        if isinstance(item, dict)
    ]

    def as_finding(item, kind):
        if kind == "improvement":
            return item
        return {
            "text": item.get("task", ""),
            "reason": item.get("reason", ""),
            "evidence": item.get("evidence", []),
        }

    def similar(left, right):
        left_text = normalize_text(
            f"{left.get('text', '')} {left.get('reason', '')}"
        )
        right_text = normalize_text(
            f"{right.get('text', '')} {right.get('reason', '')}"
        )

        if not left_text or not right_text:
            return False

        ratio = difflib.SequenceMatcher(
            None,
            left_text,
            right_text,
        ).ratio()

        left_evidence = {
            normalize_text(str(value))
            for value in left.get("evidence", [])
        }
        right_evidence = {
            normalize_text(str(value))
            for value in right.get("evidence", [])
        }

        return (
            left_text == right_text
            or ratio >= 0.78
            or bool(left_evidence & right_evidence) and ratio >= 0.45
        )

    def is_duplicate_in(item, collection, kind):
        text = item.get("text", "") if kind == "improvement" else item.get("task", "")
        if not normalize_text(text):
            return True

        for old_item in collection:
            if similar(
                as_finding(item, kind),
                as_finding(old_item, kind),
            ):
                return True

        candidate = normalize_text(text)
        return any(candidate in previous for previous in previous_texts)

    for item in report.get("improvements", []):
        completed = any(
            similar(
                as_finding(item, "improvement"),
                progress_item,
            )
            for progress_item in progress_items
        )

        if completed and item.get("priority") != "P0":
            removed += 1
            continue

        if is_duplicate_in(item, improvements, "improvement"):
            removed += 1
        else:
            item.setdefault("priority", "P2")
            improvements.append(item)

    report["improvements"] = improvements[:MAX_FINDINGS]

    for item in report.get("next_tasks", []):
        if any(
            similar(as_finding(item, "task"), improvement)
            for improvement in report["improvements"]
        ):
            removed += 1
            continue

        if is_duplicate_in(item, next_tasks, "task"):
            removed += 1
            continue

        item.setdefault("priority", "P2")
        next_tasks.append(item)

    report["next_tasks"] = next_tasks[:MAX_FINDINGS]
    report["_removed_duplicates"] = removed
    return report


def find_existing_issue(title):
    issues = github_request(
        "GET",
        f"/repos/{TARGET_REPO}/issues",
        params={"state": "open", "per_page": 100},
    )

    for issue in issues:
        if issue.get("pull_request"):
            continue
        if issue.get("title") == title:
            return issue
    return None


# -----------------------------
# Issue output
# -----------------------------
def list_to_markdown(items, item_type):
    if not items:
        return "- 해당 없음"

    lines = []
    for item in items:
        if isinstance(item, str):
            lines.append(f"- {item}")
            continue

        text = item.get("text", "") if item_type == "improvement" else item.get("task", "")
        line = f"- {text}"

        if item.get("reason"):
            line += f"\n  - 이유: {item['reason']}"

        if item_type == "improvement" and item.get("confidence") is not None:
            line += f"\n  - 신뢰도: {float(item['confidence']):.2f}"

        evidence = item.get("evidence", [])
        if evidence:
            line += "\n  - 근거: " + ", ".join(evidence)

        lines.append(line)

    return "\n".join(lines)


def make_issue_body(report, activity, metrics):
    commits = activity["commits"]
    pull_requests = activity["pull_requests"]

    commit_lines = "\n".join(
        f"- [{commit['sha']}]({commit['url']}) {commit['message']}"
        for commit in commits
    ) or "- 해당 없음"

    pr_lines = "\n".join(
        f"- [#{pr['number']}]({pr['url']}) {pr['title']}"
        for pr in pull_requests
    ) or "- 해당 없음"

    return f"""
> 이 보고서는 AI가 생성한 초안입니다.
> 실제 코드와 변경 내용을 확인한 뒤 작업을 결정해야 합니다.
> 에이전트가 코드를 자동 수정하거나 Issue를 자동 종료하지 않습니다.

## 분석 정보

- Repository: `{TARGET_REPO}`
- 분석 기간: `{activity['period']['from']}` ~ `{activity['period']['to']}`
- AI 모델: `{HF_MODEL}`
- AI 호출 횟수: {metrics['attempts']}회
- 예상 입력 토큰: 약 {metrics['estimated_input_tokens']}개
- 예상 출력 토큰: 약 {metrics['estimated_output_tokens']}개
- 처리 시간: {metrics['elapsed_seconds']:.2f}초
- 중복 제거 건수: {report.get('_removed_duplicates', 0)}개

## 이번 주 변경사항

{report['summary']}

## 잘 진행된 점

{list_to_markdown(report['progress'], 'progress')}

## 개선이 필요한 점

{list_to_markdown(report['improvements'], 'improvement')}

## 다음 추천 작업

{list_to_markdown(report['next_tasks'], 'task')}

## 근거 커밋

{commit_lines}

## 관련 Pull Request

{pr_lines}

---

이 Issue는 `github-weekly-project-reviewer`에 의해 자동 생성되었습니다.
""".strip()


def create_issue(title, body):
    payload = {"title": title, "body": body}
    try:
        issue = github_request(
            "POST",
            f"/repos/{TARGET_REPO}/issues",
            body={**payload, "labels": ["ai-generated", "needs-review", "weekly-report"]},
        )
    except RuntimeError as exc:
        # Labels may not exist in a newly created repository. The Issue itself
        # should still be created without labels.
        if "GitHub API 오류 422" not in str(exc):
            raise
        issue = github_request(
            "POST",
            f"/repos/{TARGET_REPO}/issues",
            body=payload,
        )

    print(f"Issue 생성 완료: {issue['html_url']}")


def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    init_trace_db(run_id)

    try:
        now = datetime.now(timezone.utc)
        lookback_since = now - timedelta(days=LOOKBACK_DAYS)
        previous_reviews = get_previous_reviews()
        last_review_time = get_last_review_time(previous_reviews)

        if FORCE_REVIEW:
            since = lookback_since
            print("강제 리뷰 모드입니다.")
        elif last_review_time and last_review_time > lookback_since:
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
            update_run(run_id, "skipped_no_activity")
            return

        issue_date = now.strftime("%Y-%m-%d")
        issue_title = f"Weekly Project Review - {issue_date}"

        if find_existing_issue(issue_title):
            print("같은 날짜의 열린 Issue가 이미 있습니다.")
            print("중복 생성을 건너뜁니다.")
            update_run(run_id, "skipped_duplicate")
            return

        activity = build_activity(
            commits=commits,
            pull_requests=pull_requests,
            previous_reviews=previous_reviews,
            since=since,
            now=now,
        )
        record_evidence(run_id, activity)

        report, metrics = request_review_with_retry(activity, run_id)

        for finding in activity.get("static_checks", []):
            report.setdefault("improvements", []).append(finding)
            report.setdefault("next_tasks", []).append(
                {
                    "task": finding["text"],
                    "reason": finding["reason"],
                    "evidence": finding["evidence"],
                    "priority": finding["priority"],
                }
            )

        report = remove_duplicate_findings(report, previous_reviews)
        record_findings(run_id, report)

        issue_body = make_issue_body(report, activity, metrics)
        create_issue(issue_title, issue_body)
        update_run(run_id, "success")

    except Exception:
        update_run(run_id, "failed")
        raise


if __name__ == "__main__":
    main()
