# GitHub Weekly Three-Model Project Reviewer

GitHub 저장소의 최근 커밋과 Pull Request를  
**Qwen + Command A + Gemini 세 AI 모델이 각각 독립적으로 분석한 뒤, 서로의 결과를 교차검증하여 신뢰도 높은 항목만 주간 리뷰 Issue로 생성하는 자동화 프로젝트**입니다.

단일 AI의 판단을 그대로 사용하는 것이 아니라,  
여러 모델이 서로 다른 관점에서 분석하고 다시 상호 검증하여 잘못된 지적이나 근거가 부족한 리뷰를 줄이는 것을 목표로 합니다.

> AI가 코드를 직접 수정하지 않습니다.  
> 실제 변경 코드와 근거를 검증하고, 합의된 리뷰 결과만 제공합니다.

---

## 핵심 구조

**1차 분석**

GitHub 변경사항  
→ Qwen 분석  
→ Command A 분석  
→ Gemini 분석

**2차 교차검증**

세 모델의 분석 결과  
→ Review Candidate 생성  
→ Qwen / Command A / Gemini가 후보를 다시 평가  
→ 실제 변경 라인 및 코드 근거 검증  
→ 최소 2개 모델이 동의한 항목만 최종 채택

**최종 결과**

검증된 합의 항목  
→ Weekly Review 생성  
→ GitHub Issue 등록  
→ JSON / SQLite 실행 기록 저장

---

## 사용 AI 모델

| 역할 | Provider | Model |
|---|---|---|
| 코드 분석 / 교차검증 | Hugging Face | `Qwen/Qwen2.5-Coder-3B-Instruct` |
| 코드 분석 / 교차검증 | Cohere | `command-a-plus-05-2026` |
| 코드 분석 / 교차검증 | Google Gemini | `gemini-3.8-flash` |

세 모델은 모두 단순 보조 모델이 아니라  
**1차 분석과 2차 교차검증에 참여합니다.**

---

## 동작 원리

### 1. GitHub 변경사항 수집

최근 7일 동안의 데이터를 수집합니다.

- Commit
- Pull Request
- 변경 파일
- 실제 diff
- 변경된 코드 라인

주간 리뷰는 기본적으로 최근 7일을 기준으로 분석합니다.

---

### 2. 모델별 독립 분석

변경된 파일을 세 모델이 각각 분석합니다.

- Qwen
- Command A
- Gemini

각 모델은 다음 내용을 구조화된 결과로 반환합니다.

- 파일 위치
- 변경 라인
- 문제 유형
- 심각도
- 판단 내용
- 판단 이유
- 신뢰도
- 코드 근거

각 모델은 최대 5개의 Finding을 생성합니다.

---

### 3. 실제 코드 근거 검증

AI가 생성한 결과를 바로 사용하지 않습니다.

다음 항목을 실제 Git diff와 비교합니다.

- AI가 지적한 파일이 실제 변경 파일인지
- AI가 지적한 라인이 실제 변경 라인인지
- AI가 제시한 코드 근거가 실제 코드와 일치하는지

실제 변경사항과 맞지 않는 결과는 제외됩니다.

---

### 4. Review Candidate 생성

세 모델이 만든 Finding을 다음 기준으로 묶습니다.

- 같은 파일
- 비슷한 변경 라인
- 같은 문제 분류
- 유사한 리뷰 내용

같은 모델이 생성한 결과끼리는 합의로 인정하지 않습니다.

---

### 5. 세 모델 교차검증

1차 분석이 끝나면 각 모델이 다른 모델의 분석 결과까지 다시 검토합니다.

각 후보에 대해 세 모델이 교차평가를 수행합니다.

즉 구조는 다음과 같습니다.

```text
Qwen ─────┐
Command A ├─ 1차 분석
Gemini ───┘
     ↓
Review Candidates
     ↓
Qwen ─────┐
Command A ├─ 교차평가
Gemini ───┘
     ↓
Consensus
```

---

## 합의 규칙

기본 설정:

```text
MIN_SUCCESSFUL_PROVIDERS = 3
MIN_MATCH_COUNT = 2
LINE_TOLERANCE = 1
```

의미는 다음과 같습니다.

- 세 모델이 정상적으로 분석에 참여
- 서로 다른 모델 중 최소 2개가 같은 문제에 동의
- 변경 라인은 ±1 범위까지 동일 위치로 판단 가능

최소 조건을 만족한 항목만 최종 리뷰 결과에 포함됩니다.

---

## 주간 리뷰 자동화

GitHub Actions의 `Weekly Three-Model Review`가 실행됩니다.

기본 흐름:

```text
GitHub Actions
→ Unit Test
→ 최근 7일 변경사항 수집
→ 세 모델 독립 분석
→ 코드 근거 검증
→ 세 모델 교차평가
→ Consensus 생성
→ 결과 JSON 저장
→ GitHub Issue 생성
```

---

## 자동 실행 시간

주간 리뷰는 **매주 월요일 오전 9시(KST)** 자동 실행됩니다.

GitHub Actions는 UTC 기준이므로 다음 Cron을 사용합니다.

```yaml
schedule:
  - cron: "0 0 * * 1"
```

---

## 생성되는 Issue

자동 실행 시 분석 결과는 `TARGET_REPO` 저장소의 Issue로 생성됩니다.

제목:

```text
주간 프로젝트 리뷰 - YYYY-MM-DD
```

Issue에는 다음 정보가 포함됩니다.

- 분석 대상 저장소
- 분석 기간
- Commit 수
- Pull Request 수
- 변경 파일 수
- 기준 Commit / 최종 Commit
- 최종 교차검증 결과
- 합의한 모델
- 심각도
- 신뢰도
- 실제 변경 코드 근거
- 모델별 검토 파일
- 모델 실행 성공 / 실패 상태
- 제외된 잘못된 Finding 수
- 최종 Consensus 수
- 해당 주의 Commit 목록
- 해당 주의 Pull Request 목록

---

## 최종 리뷰 예시

각 리뷰 항목에는 다음과 같은 정보가 표시됩니다.

```text
[P1] 입력값 검증이 누락되어 있습니다.

위치: src/example.py:42
분류: validation
합의 모델: qwen, gemini
신뢰도: 0.91

판단 이유:
사용자 입력이 검증 없이 함수 내부로 전달됩니다.

실제 변경 근거:
<실제 diff 코드>
```

두 모델 이상이 같은 문제를 확인하지 못한 경우에는  
해당 내용은 최종 결과에서 제외됩니다.

---

## PR Cross Review

주간 리뷰 외에 특정 Pull Request만 대상으로  
세 모델 교차검증을 실행하는 기능도 포함되어 있습니다.

GitHub Actions:

```text
PR Cross Review
```

실행할 때 분석할 PR 번호를 입력합니다.

```text
PR_NUMBER
```

흐름:

```text
특정 Pull Request
→ Diff 수집
→ 세 모델 독립 분석
→ 실제 변경 라인 검증
→ 교차평가
→ Consensus 생성
→ JSON / SQLite 기록
```

---

## Legacy Qwen Review

기존 단일 Qwen 리뷰 방식도 호환 및 비교를 위해 남겨두었습니다.

GitHub Actions:

```text
Legacy Qwen Weekly Review
```

이 워크플로우는 현재 메인 구조가 아니며,  
현재 주 기능은 **세 모델 교차검증 방식**입니다.

---

## GitHub Actions Secrets

Repository에서 다음 경로로 이동합니다.

```text
Settings
→ Secrets and variables
→ Actions
→ Repository secrets
```

다음 Secret을 등록합니다.

| Secret | 용도 |
|---|---|
| `HF_TOKEN` | Qwen 호출 |
| `COHERE_API_KEY` | Command A 호출 |
| `GEMINI_API_KEY` | Gemini 호출 |
| `TARGET_GITHUB_TOKEN` | 대상 Repository 조회 및 Issue 생성 |

API Key와 Token은 코드에 직접 작성하지 않습니다.

---

## 분석 대상 저장소 설정

다음 경로로 이동합니다.

```text
Settings
→ Secrets and variables
→ Actions
→ Variables
```

다음 Repository Variable을 등록합니다.

| 이름 | 예시 | 설명 |
|---|---|---|
| `TARGET_REPO` | `leeony2636/my-project` | 분석할 Repository |

등록하지 않으면 현재 Repository가 기본 대상이 됩니다.

---

## 수동 실행

GitHub Repository에서:

```text
Actions
→ Weekly Three-Model Review
→ Run workflow
```

수동 실행 시 다음 값을 설정할 수 있습니다.

### Lookback Days

분석할 최근 일수입니다.

기본값:

```text
7
```

### Publish Issue

활성화하면 실제 GitHub Issue를 생성합니다.

### Upload Artifacts

활성화하면 JSON 결과와 SQLite 실행 기록을 Artifact로 저장합니다.

---

## 결과 파일

주간 리뷰 결과:

```text
weekly_review_result.json
```

주간 실행 기록:

```text
weekly_review_trace.db
```

PR 리뷰 결과:

```text
review_result.json
```

PR 실행 기록:

```text
review_trace.db
```

---

## 실행 기록

SQLite에는 리뷰 실행과 AI 호출 관련 기록이 저장됩니다.

주요 기록:

- 실행 상태
- 분석 대상
- 모델 호출 결과
- Provider 성공 / 실패
- 검증된 Finding
- 제외된 Finding
- 교차검증 결과
- Consensus 결과

필요한 경우 GitHub Actions Artifact로 내려받아 확인할 수 있습니다.

---

## 안정성 검증

리뷰 결과를 생성하기 전에 여러 검증을 수행합니다.

- 실제 변경 파일 검증
- 실제 변경 라인 검증
- Evidence 코드 일치 확인
- 잘못된 JSON 응답 차단
- Provider 응답 형식 검사
- Provider Identity 검사
- 동일 모델끼리의 합의 방지
- 유사 Finding 그룹화
- 최소 합의 모델 수 검사
- API 호출 실패 처리
- Timeout 처리
- Quota Guard
- 민감정보 포함 여부 검사

---

## Quota / API 보호

모델별 API 호출량을 제한합니다.

기본 설정:

```text
MAX_QWEN_CALLS_PER_RUN=5
MAX_GPT_CALLS_PER_RUN=5
MAX_GEMINI_CALLS_PER_RUN=6
```

무료 또는 제한된 API 환경에서 과도한 호출을 방지하기 위한 설정입니다.

---

## 테스트

GitHub Actions에서 실제 AI 분석 전에 Unit Test를 먼저 실행합니다.

```bash
python -m unittest discover \
  -s tests \
  -p "test_*.py" \
  -v
```

테스트 대상에는 다음 기능이 포함됩니다.

- 환경 설정
- Consensus
- Diff Parser
- GitHub Service
- Orchestrator
- Prompt
- AI Provider
- Quota Guard
- Review Selector
- Schema
- Security
- Trace Store
- Weekly Service

테스트가 실패하면 본 리뷰 단계로 진행되지 않습니다.

---

## 프로젝트 구조

```text
github-weekly-project-reviewer/
├── .github/
│   └── workflows/
│       ├── weekly-cross-review.yml
│       ├── pr-cross-review.yml
│       └── weekly-review.yml
│
├── scripts/
│   ├── weekly_cross_review.py
│   ├── pr_review.py
│   └── weekly_review.py
│
├── src/
│   └── reviewer/
│       ├── providers/
│       │   ├── qwen_client.py
│       │   ├── gpt_client.py
│       │   └── gemini_client.py
│       │
│       ├── prompts/
│       │   ├── qwen_prompt.py
│       │   ├── gpt_prompt.py
│       │   ├── gemini_prompt.py
│       │   └── cross_review_prompt.py
│       │
│       ├── config.py
│       ├── consensus.py
│       ├── diff_parser.py
│       ├── github_service.py
│       ├── orchestrator.py
│       ├── quota_guard.py
│       ├── review_selector.py
│       ├── schemas.py
│       ├── security.py
│       ├── trace_store.py
│       └── weekly_service.py
│
├── tests/
│   ├── test_config.py
│   ├── test_consensus.py
│   ├── test_diff_parser.py
│   ├── test_github_service.py
│   ├── test_orchestrator.py
│   ├── test_prompts.py
│   ├── test_providers.py
│   ├── test_quota_guard.py
│   ├── test_review_selector.py
│   ├── test_schemas.py
│   ├── test_security.py
│   ├── test_trace_store.py
│   ├── test_weekly_cross_review.py
│   └── test_weekly_service.py
│
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-legacy.txt
└── README.md
```

---

## 환경 변수 예시

`.env.example` 기준 주요 설정:

```env
TARGET_REPO=

HF_TOKEN=
COHERE_API_KEY=
GEMINI_API_KEY=
TARGET_GITHUB_TOKEN=

QWEN_MODEL=Qwen/Qwen2.5-Coder-3B-Instruct
QWEN_BASE_URL=https://router.huggingface.co/featherless-ai/v1

GPT_MODEL=command-a-plus-05-2026
GPT_BASE_URL=https://api.cohere.ai/compatibility/v1

GEMINI_MODEL=gemini-3.8-flash
GEMINI_THINKING_LEVEL=low

MIN_MATCH_COUNT=2
MIN_SUCCESSFUL_PROVIDERS=3
LINE_TOLERANCE=1

MAX_QWEN_CALLS_PER_RUN=5
MAX_GPT_CALLS_PER_RUN=5
MAX_GEMINI_CALLS_PER_RUN=6
```

---

## 기술 스택

- Python 3.11
- GitHub Actions
- GitHub API / PyGithub
- Hugging Face API
- Cohere API
- Google Gemini API
- Qwen 2.5 Coder
- Command A
- Gemini
- SQLite
- Docker
- Unit Test
- Multi-Agent Cross Review
- Consensus Validation

---

## 프로젝트 핵심

이 프로젝트의 핵심은 단순히 AI에게 코드를 한 번 분석시키는 것이 아닙니다.

```text
3개 모델의 독립 분석
        ↓
실제 변경 코드 검증
        ↓
3개 모델의 교차평가
        ↓
최소 2개 모델 합의
        ↓
검증된 결과만 GitHub Issue 생성
```

서로 다른 모델의 장점을 활용하고  
한 모델의 오판을 다른 모델이 보완하도록 구성한 **다중 AI 교차검증 코드 리뷰 시스템**입니다.