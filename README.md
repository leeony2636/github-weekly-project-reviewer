# GitHub Weekly Three-Model Project Reviewer

GitHub 저장소의 최근 변경사항을 **Qwen · Command A · Gemini**가 독립적으로 분석하고, 실제 코드 근거 검증과 모델 간 교차평가를 거쳐 한국어 주간 리뷰를 GitHub Issue로 기록하는 자동화 프로젝트입니다.

GitHub Actions로 매주 한 번 실행하며, **변경사항이 없는 주에는 모델 호출을 생략하고 변경 없음 기록을 남깁니다.**

AI가 코드를 수정하거나 커밋·배포하지 않습니다. 검토 결과와 실행 근거를 사람이 확인할 수 있도록 정리하는 것이 목적입니다.

---

## 📌 현재 구현 및 운영 상태

| 항목 | 현재 상태 |
|---|---|
| 주간 예약 실행 | GitHub Actions에 구성 |
| 주간 Issue 생성 | 대상 저장소에서 실제 생성 기록 확인 |
| 세 모델 독립 분석 | 구현 및 실제 실행 기록 확인 |
| 실제 변경 라인·코드 근거 검증 | 구현 |
| 모델 간 교차평가·합의 | 구현 |
| 변경 없음 처리 | 모델 호출 없이 주간 기록 생성 |
| 같은 날짜의 Issue 중복 방지 | 기존 Issue 확인 후 실행 생략 |
| JSON·SQLite 실행 기록 | 구현 |
| Langfuse 모델 호출 관측·저장 확인 | 주간 워크플로에 연결 |
| GCP Langfuse VM 시작·종료 | 주간 워크플로에 연결 |
| 특정 PR 교차검증 | 수동 실행 워크플로 구성 |
| 기존 단일 Qwen 리뷰 | 수동 실행용 Legacy 경로로 유지 |

### 운영 기록 — 2026-10-14 확인 기준

확인한 최근 주간 리뷰의 대상은 `leeony2636/docker-fastapi-sentiment-api`입니다.

| 날짜 | 확인된 결과 | 근거 |
|---|---|---|
| 2026-09-17 | 세 모델 실행 성공 기록. 원본 지적 6건 중 변경 라인·코드 근거 검증으로 제외된 항목이 있으며 최종 합의는 0건 | [Issue #25](https://github.com/leeony2636/docker-fastapi-sentiment-api/issues/25) |
| 2026-09-21 | 예약 실행 성공. 변경 없음으로 모델 호출 생략 | [Issue #26](https://github.com/leeony2636/docker-fastapi-sentiment-api/issues/26) |
| 2026-09-28 | 예약 실행 성공. 변경 없음으로 모델 호출 생략 | [Issue #27](https://github.com/leeony2636/docker-fastapi-sentiment-api/issues/27) |
| 2026-10-05 | 예약 실행 성공. 변경 없음으로 모델 호출 생략 | [Issue #28](https://github.com/leeony2636/docker-fastapi-sentiment-api/issues/28) |
| 2026-10-12 | 단위 테스트·Langfuse 준비 확인 이후 주간 리뷰 단계에서 `GitHubServiceError`로 실패 | [Actions 실행 기록](https://github.com/leeony2636/github-weekly-project-reviewer/actions/runs/38210357439) |

10월 12일 기록에서는 **125개 테스트와 2개 subtest 통과**가 확인됩니다. 이는 해당 실행의 기록이며, 새로운 환경에서 다시 실행한 검증 결과를 뜻하지 않습니다.

최신 실패의 상세 원인은 아직 확정하지 않았습니다. 토큰 만료나 모델 오류로 단정하지 않습니다. 해당 실행에서는 VM 종료 단계가 생략되었으며, 이후 실제 VM 상태는 별도로 확인해야 합니다.

---

## 🎯 프로젝트 목적

단일 모델의 지적을 바로 채택하지 않고 다음 과정을 거쳐 검토 결과의 근거를 확인합니다.

1. 최근 변경사항과 프로젝트 핵심 파일을 수집합니다.
2. 세 모델이 각각 독립적으로 분석합니다.
3. 지적한 파일·라인·코드 근거가 실제 변경사항과 맞는지 검사합니다.
4. 검증을 통과한 후보를 세 모델이 다시 교차평가합니다.
5. 서로 다른 모델 중 최소 2개가 수용한 항목을 최종 결과로 채택합니다.
6. 모델별 의견과 제외 사유, 최종 합의를 Issue와 실행 기록으로 남깁니다.

모델 간 합의는 검토 기준이며, 실제 버그나 수정 필요성을 최종 확정하는 판단은 사람이 수행합니다.

---

## 🔄 전체 실행 흐름

```text
GitHub Actions 예약 또는 수동 실행
    ↓
GCP 인증 및 Langfuse VM 시작 요청
    ↓
Python 의존성 설치·단위 테스트
    ↓
Langfuse 준비 상태 확인
    ↓
대상 저장소의 최근 변경사항 수집
    ├─ 같은 날짜 Issue 존재 → 중복 실행 생략
    ├─ 변경 없음 → 모델 호출 없이 주간 기록 생성
    └─ 변경 있음
          ↓
       핵심 파일 기반 세 모델 독립 분석
          ↓
       실제 변경 라인·코드 근거 검증
          ↓
       후보 생성 및 세 모델 교차평가
          ↓
       최소 2개 모델 합의
    ↓
대상 저장소에 Issue 생성
    ↓
JSON·SQLite 실행 기록
    ↓
Langfuse 전송 및 observation 저장 확인
    ↓
리뷰 단계 성공 시 VM 종료 요청
```

VM 시작은 비동기로 요청하여 Python 설치·테스트와 부팅 시간을 겹칩니다. 모델 실행 전에는 Langfuse가 준비되었는지 확인합니다.

---

## 🤖 사용 모델

현재 워크플로에 선언된 모델 설정입니다.

| 내부 식별자 | Provider | 모델 ID |
|---|---|---|
| `qwen` | Hugging Face | `Qwen/Qwen2.5-Coder-3B-Instruct` |
| `gpt` | Cohere | `command-a-plus-05-2026` |
| `gemini` | Google Gemini | `gemini-3.8-flash` |

`gpt`라는 내부 이름과 `gpt_client.py`, `GPT_MODEL`은 기존 코드의 명칭입니다. **현재 해당 경로는 Cohere Command A를 사용합니다.**

모델 ID는 실행 설정이며, Provider의 이용 가능 여부·사용 한도·실제 응답 성공을 보장하지 않습니다.

---

## 🔍 검토 범위와 채택 기준

### 주간 검토 입력

- 최근 7일의 Commit·Pull Request·변경 파일
- 실제 Git diff와 변경 라인
- 대상 저장소 기본 브랜치의 `main.py`
- 대상 저장소 기본 브랜치의 `README.md`

주간 1차 분석은 프로젝트 목적·핵심 구조·주요 위험·다음 개선 우선순위를 짧게 검토합니다.

현재 입력 구성은 다음과 같습니다.

- README는 앞부분 최대 800자를 사용합니다.
- 주간 핵심 프롬프트는 최대 2,200자로 제한합니다.
- 필요하면 `main.py`도 앞부분 위주로 제한합니다.
- 핵심 파일이 없으면 해당 파일을 건너뜁니다.
- 변경 파일이 없으면 세 모델을 호출하지 않습니다.

따라서 저장소 전체의 모든 모듈을 정밀 검사하는 방식은 아닙니다.

### 근거 검증

모델이 제시한 지적은 다음 조건을 확인합니다.

- 파일과 라인이 실제 변경 범위에 포함되는가
- 제시한 코드 근거가 실제 변경 코드와 일치하는가
- 응답이 정해진 Schema를 지키는가
- 모델 식별자가 실제 호출한 Provider와 일치하는가

검증에 실패한 지적은 최종 합의 후보에서 제외합니다.

### 합의 기준

- 1차 분석에는 세 모델 모두 성공적으로 참여해야 합니다.
- 교차평가도 세 모델의 참여를 확인합니다.
- 서로 다른 모델 중 최소 2개가 수용한 후보를 채택합니다.
- 같은 모델의 여러 지적을 여러 모델의 합의로 계산하지 않습니다.
- 변경 라인 비교의 기본 허용 범위는 ±1입니다.

---

## ⏰ 예약 실행과 Issue 생성

주간 워크플로는 **매주 월요일 오전 9시 20분(KST)**으로 예약되어 있습니다.

```yaml
schedule:
  - cron: "20 0 * * 1"
```

이는 예약 기준 시각입니다. 실제 시작 시각은 GitHub Actions 실행 기록에서 확인합니다.

예약 실행에는 `--publish`가 자동으로 적용되며, 결과는 `TARGET_REPO`의 Issue로 생성됩니다.

Issue 제목:

```text
주간 프로젝트 리뷰 - YYYY-MM-DD
```

### Issue에 기록하는 내용

- 대상 저장소와 검토 기간
- Commit·Pull Request·변경 파일 수
- 기준 Commit과 최종 Commit
- 모델별 1차 의견
- 교차평가 의견과 최종 합의
- 모델별 검토 파일 및 실행 상태
- 변경 라인·코드 근거 불일치로 제외된 지적 수
- 해당 기간 Commit·Pull Request 목록

이미 생성된 의견을 Issue에 출력할 때 추가 모델 호출을 하지 않습니다.

### 결과 해석

| 결과 | 의미 |
|---|---|
| 변경 없음·모델 호출 없음 | 해당 기간에 검토할 변경 파일이 없다는 기록 |
| 세 모델 실행 성공·합의 0건 | 현재 검토 범위와 채택 기준을 통과한 지적이 없음 |
| 일부 단계 실패 | 정상 검토 완료와 구분하여 실행 상태 확인 필요 |
| Issue 생성 완료 | 보고서 게시 완료. 전체 코드 품질 보증과는 별개 |

---

## 📡 Langfuse와 GCP 운영

현재 주간 워크플로에서는 Langfuse가 필수입니다.

1. Google Cloud에 Workload Identity Federation으로 인증합니다.
2. Langfuse VM이 실행 중인지 확인하고 필요하면 시작합니다.
3. Langfuse HTTP 준비 상태를 최대 180초 동안 확인합니다.
4. 모델 호출을 generation으로 관측합니다.
5. 실행 종료 시 `flush()`로 기록을 전송합니다.
6. 이번 실행의 observation이 Langfuse API에서 조회되는지 최대 45초 동안 확인합니다.
7. 리뷰 단계가 성공한 경우 VM 종료를 비동기로 요청합니다.

저장 확인 실패를 성공으로 숨기지 않습니다. 확인할 observation이 없는 실행은 별도로 처리합니다.

로컬 Python 실행에서는 Langfuse 설정이 없으면 관측을 생략할 수 있지만, 현재 주간 Actions 워크플로는 Langfuse 설정과 준비 상태를 요구합니다.

---

## ⚙️ GitHub Actions 설정

현재 환경을 유지할 때는 기존 설정을 사용합니다. 별도 환경에 설치할 경우 아래 연결이 필요합니다.

### Repository Secrets

경로: `Settings → Secrets and variables → Actions → Secrets`

| 이름 | 용도 |
|---|---|
| `HF_TOKEN` | Qwen 호출 |
| `COHERE_API_KEY` | Command A 호출 |
| `GEMINI_API_KEY` | Gemini 호출 |
| `TARGET_GITHUB_TOKEN` | 대상 저장소 조회 및 Issue 생성 |
| `GCP_WIF_PROVIDER` | Google Cloud 인증 |
| `GCP_SERVICE_ACCOUNT` | VM 시작·종료에 사용하는 서비스 계정 |
| `LANGFUSE_PUBLIC_KEY` | Langfuse 연결 |
| `LANGFUSE_SECRET_KEY` | Langfuse 인증 |
| `LANGFUSE_BASE_URL` | Langfuse 서버 주소 |

대상 저장소의 조회·Issue 생성 권한과 GCP VM 시작·종료 권한을 각각 확인해야 합니다.

GCP 프로젝트·존·VM 이름은 현재 `weekly-cross-review.yml`에 선언되어 있습니다. 다른 환경에 설치할 때는 해당 환경의 값으로 설정합니다.

### Repository Variables

경로: `Settings → Secrets and variables → Actions → Variables`

| 이름 | 설명 |
|---|---|
| `TARGET_REPO` | 검토할 저장소. 형식: `owner/repository` |

값이 없으면 워크플로가 실행되는 현재 저장소를 대상으로 사용합니다.

API 키·토큰·개인 인증값은 README나 소스 코드에 작성하지 않습니다.

---

## ▶️ 수동 실행

### 주간 리뷰

`Actions → Weekly Three-Model Review → Run workflow`

| 입력 | 동작 |
|---|---|
| `lookback_days` | 최근 검토 기간. 기본 7일 |
| `publish_issue` | 수동 실행 결과를 Issue로 게시할지 선택 |
| `upload_artifacts` | 입력은 정의되어 있으나 현재 주간 워크플로에 실제 업로드 단계는 연결되어 있지 않음 |

수동 실행의 `publish_issue` 기본값은 `false`입니다. 예약 실행은 Issue 게시를 활성화합니다.

### PR 교차검증

`Actions → PR Cross Review → Run workflow`

- `pr_number`: 검토할 PR 번호
- `upload_artifacts`: 결과 JSON과 SQLite 기록 업로드 여부

PR 워크플로에는 실제 Artifact 업로드 단계가 있으며 보존 기간은 7일입니다. 주간 리뷰와 달리 현재 PR 워크플로에는 GCP VM 시작·종료 절차가 없습니다.

### Legacy Qwen 리뷰

`Actions → Legacy Qwen Weekly Review → Run workflow`

기존 단일 Qwen 방식의 수동 실행 경로입니다. 현재 주간 예약 실행의 주 경로는 세 모델 교차검증입니다.

---

## 🛡️ 호출 제한·보안·실행 기록

- 입력 문자 수와 Provider별 호출 횟수 제한
- Provider timeout과 응답 Schema 검사
- 실제 변경 라인·코드 근거 검증
- 민감정보 의심 내용의 외부 모델 전송·Issue 생성 차단
- 동일 날짜 Issue 중복 방지
- 기존 SQLite 기록을 사용할 수 있는 환경의 결과 캐시

현재 예약 워크플로는 일반 출력 상한을 4,096 tokens, Command A 출력 상한을 8,192 tokens로 설정합니다. PR 워크플로와 `.env.example`의 기본 출력 상한은 800 tokens이므로 실행 경로별 설정을 구분합니다.

Provider별 호출 제한도 실행 경로에 따라 다릅니다. Python 기본값은 각 6회이고, `.env.example` 및 PR 워크플로에는 Qwen 5회·Command A 5회·Gemini 6회가 선언되어 있습니다.

모델 API와 GCP VM 비용은 실제 사용량과 서비스 요금 정책에 따릅니다.

### 생성되는 파일

| 파일 | 내용 |
|---|---|
| `weekly_review_result.json` | 주간 리뷰 결과와 Issue 본문 |
| `review_result.json` | PR 리뷰 결과 |
| `review_trace.db` | SQLite 실행·Provider 호출·검증 기록 |

파일 경로는 환경변수로 변경할 수 있습니다.

현재 주간 Actions에는 결과 파일의 Artifact 업로드·이전 SQLite 기록 복원 단계가 없습니다. 따라서 로컬 파일 생성과 실행 간 보존을 구분합니다. PR 워크플로는 사용자가 선택하면 Artifact를 업로드합니다.

---

## 📁 현재 폴더 구조

```text
github-weekly-project-reviewer/
├── .github/
│   └── workflows/
│       ├── weekly-cross-review.yml   # 주간 예약·수동 리뷰
│       ├── pr-cross-review.yml       # 수동 PR 교차검증
│       └── weekly-review.yml         # Legacy Qwen 수동 리뷰
├── scripts/
│   ├── weekly_cross_review.py        # 주간 리뷰·Issue 생성
│   ├── pr_review.py                  # PR 리뷰 실행
│   └── weekly_review.py              # Legacy 실행기
├── src/
│   └── reviewer/
│       ├── providers/
│       │   ├── qwen_client.py
│       │   ├── gpt_client.py         # 현재 Cohere Command A
│       │   └── gemini_client.py
│       ├── prompts/
│       │   ├── qwen_prompt.py
│       │   ├── gpt_prompt.py
│       │   ├── gemini_prompt.py
│       │   └── cross_review_prompt.py
│       ├── config.py
│       ├── consensus.py
│       ├── diff_parser.py
│       ├── github_service.py
│       ├── langfuse_observer.py
│       ├── orchestrator.py
│       ├── quota_guard.py
│       ├── review_selector.py
│       ├── schemas.py
│       ├── security.py
│       ├── trace_store.py
│       └── weekly_service.py
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-legacy.txt
└── README.md
```

`langfuse_observer.py`는 모델 호출 관측·전송·저장 확인을 담당합니다. `scripts/`는 실행 진입점이고 `src/reviewer/`는 검토·검증·기록 로직을 담당합니다.

---

## 🧪 로컬 테스트

프로젝트 루트의 PowerShell에서 실행합니다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
```

환경 설정, Diff 처리, Provider 응답, 합의, 입력 선택, 보안, 호출 제한, 실행 기록과 주간 처리 등을 검사합니다.

테스트 통과와 실제 Provider 호출·Issue 게시·Langfuse 저장 성공은 구분합니다.

Docker 실행 구성도 포함되어 있으며 기본 실행 대상은 `scripts/pr_review.py`입니다. GCP와 Langfuse를 사용하는 주간 Actions 전체 절차와 동일한 실행 환경은 아닙니다.

---

## 📚 기술 스택

`Python 3.11` · `GitHub Actions` · `PyGithub` · `Hugging Face` · `Cohere` · `Google Gemini` · `SQLite` · `Langfuse` · `Google Cloud Compute Engine` · `Workload Identity Federation` · `Docker` · `pytest`

---

## 확인 범위

이 README는 2026-10-14 확인한 기본 브랜치의 폴더 구조·코드·워크플로 설정과 기존 Actions·Issue 기록을 기준으로 작성했습니다.

문서 정리를 위해 모델 호출, 워크플로 재실행, VM 제어, 인증 설정 변경 또는 새로운 Issue 생성을 수행하지 않았습니다.

최신 `GitHubServiceError`의 상세 원인과 이후 복구 상태는 별도 확인이 필요합니다.
