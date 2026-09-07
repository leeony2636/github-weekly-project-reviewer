# GitHub Weekly Project Reviewer

GitHub 저장소의 최근 커밋과 Pull Request를 AI로 분석하여 주간 리뷰 Issue를 생성하는 자동화 도구입니다.

잘 진행된 점, 개선이 필요한 점, 다음 추천 작업을 변경 파일과 커밋을 근거로 정리합니다.

> 생성된 리뷰는 AI가 작성한 초안입니다.  
> 코드를 자동으로 수정하거나 Issue를 자동으로 종료하지 않습니다.

## 동작 원리

```text
GitHub Actions 실행
→ 최근 커밋과 Pull Request 수집
→ 변경 파일과 diff 분석
→ 정적 검사와 AI 분석
→ 결과 검증 및 중복 제거
→ 분석 대상 저장소에 Issue 생성
→ 실행 기록을 SQLite에 저장
```

리뷰어는 다음 순서로 동작합니다.

1. 최근 7일 동안의 커밋과 Pull Request를 수집합니다.
2. 변경된 파일과 코드 diff를 분석 근거로 정리합니다.
3. 정적 검사와 Hugging Face AI 분석을 실행합니다.
4. 낮은 신뢰도, 중복 항목, 잘못된 근거를 검사합니다.
5. 최종 검증을 통과한 경우에만 리뷰 Issue를 생성합니다.

## 주요 기능

- 최근 커밋과 Pull Request 수집
- 변경 파일과 diff 기반 분석
- 잘 진행된 점, 개선점, 다음 작업 분류
- Python 변경 시 테스트 파일 존재 여부 확인
- Dockerfile 변경 시 빌드·실행 검증 여부 확인
- API 키, 토큰, 비밀번호 형태의 하드코딩 검사
- 이전 리뷰 및 현재 결과의 중복 제거
- 실제 변경 파일과 일치하지 않는 근거 차단
- 낮은 신뢰도의 개선점 제외
- 잘못된 JSON 응답 차단
- 같은 날짜의 열린 리뷰 Issue 중복 생성 방지
- 실행 및 AI 호출 기록 저장
- 각 결과 항목을 최대 5개까지 출력

## 다른 사용자가 사용하는 방법

이 저장소를 Fork하면 자신의 GitHub 저장소를 주간 단위로 분석할 수 있습니다.

각 사용자는 자신의 GitHub 토큰과 Hugging Face 토큰을 등록합니다. 따라서 토큰과 API 사용량을 다른 사용자와 공유하지 않습니다.

### 1. 저장소 Fork

이 저장소 오른쪽 위의 `Fork` 버튼을 눌러 자신의 GitHub 계정으로 복사합니다.

Fork한 저장소에서 Actions가 비활성화되어 있다면 `Actions` 메뉴에서 워크플로 사용을 활성화합니다.

### 2. Hugging Face 토큰 준비

Hugging Face 계정에서 AI 모델 호출에 사용할 Access Token을 발급합니다.

각 사용자의 AI 호출량과 사용 제한은 자신이 등록한 `HF_TOKEN` 계정에 개별적으로 적용됩니다.

### 3. GitHub 토큰 준비

분석 대상 저장소를 읽고 리뷰 Issue를 생성할 수 있는 GitHub 토큰을 준비합니다.

토큰에는 최소한 다음 권한이 필요합니다.

- 저장소 내용 읽기
- 커밋과 Pull Request 정보 읽기
- Issue 읽기 및 생성

비공개 저장소를 분석하려면 해당 저장소에 접근할 수 있는 토큰이 필요합니다.

### 4. Actions Secrets 등록

Fork한 리뷰어 저장소에서 다음 메뉴로 이동합니다.

```text
Settings
→ Secrets and variables
→ Actions
→ Secrets
→ New repository secret
```

다음 두 개의 Secret을 등록합니다.

| 이름 | 설명 |
|---|---|
| `HF_TOKEN` | 본인의 Hugging Face Access Token |
| `TARGET_GITHUB_TOKEN` | 본인의 GitHub Access Token |

> 토큰 값은 코드, README, Issue 또는 공개된 워크플로 파일에 직접 입력하지 마세요.

### 5. 분석 대상 저장소 등록

같은 설정 화면에서 다음 메뉴로 이동합니다.

```text
Settings
→ Secrets and variables
→ Actions
→ Variables
→ New repository variable
```

다음 변수를 등록합니다.

| 이름 | 예시 | 설명 |
|---|---|---|
| `TARGET_REPO` | `username/repository` | 분석할 GitHub 저장소 |

예시:

```text
Name: TARGET_REPO
Value: octocat/my-project
```

`TARGET_REPO`를 등록하지 않으면 Fork한 리뷰어 저장소 자체를 분석합니다.

## 실행 방법

### 수동 실행

Fork한 저장소의 `Actions` 메뉴에서 `Weekly Project Review`를 선택합니다.

오른쪽의 `Run workflow` 버튼을 누르면 리뷰를 바로 실행할 수 있습니다.


<img width="1642" height="514" alt="01-run-workflow png" src="https://github.com/user-attachments/assets/c61305ec-ee3d-4e89-9e32-c36c8e2fe8b7" />

새로운 변경사항이 없어도 다시 분석하려면 다음 항목을 선택합니다.

```text
새 변경사항이 없어도 강제로 다시 분석합니다.
```

강제 분석은 기능 확인이나 테스트가 필요할 때 사용합니다.

### 자동 실행

워크플로는 매주 일요일 오전 9시(KST)에 자동 실행됩니다.

GitHub Actions의 예약 시간은 UTC 기준이므로 워크플로에는 다음과 같이 설정되어 있습니다.

```yaml
schedule:
  - cron: "0 0 * * 0"
```

### 실행 결과 확인

정상적으로 완료되면 Actions 실행 화면에 초록색 성공 표시가 나타납니다.


<img width="1896" height="621" alt="02-workflow-success" src="https://github.com/user-attachments/assets/d6c7668c-8a61-4088-833e-04bbef14c8eb" />

## 생성되는 리뷰 Issue

분석이 완료되면 `TARGET_REPO`로 설정한 저장소에 다음 형식의 Issue가 생성됩니다.

```text
Weekly Project Review - YYYY-MM-DD
```


<img width="1714" height="473" alt="03-generated-issue" src="https://github.com/user-attachments/assets/c990dcfb-3aa4-4a4a-938e-a34298952bdb" />

Issue 상단에는 다음 정보가 표시됩니다.

- 분석 대상 저장소
- 분석 기간
- 사용한 AI 모델
- AI 호출 횟수
- 예상 입력·출력 토큰
- 처리 시간
- 중복 및 검증 제외 건수
- 이번 주 변경사항
- 잘 진행된 점


<img width="955" height="743" alt="04-review-summary" src="https://github.com/user-attachments/assets/ec52ee87-8f06-4e0b-bfb0-b3358c06f1b7" />

이어서 개선이 필요한 항목의 이유, 우선순위, 신뢰도와 근거 파일이 표시됩니다.


<img width="902" height="534" alt="05-review-improvements" src="https://github.com/user-attachments/assets/ef9ecf2e-add6-4eef-9c3e-46df0cda159d" />

마지막에는 다음 추천 작업과 분석에 사용된 근거 커밋 및 Pull Request가 표시됩니다.


<img width="904" height="826" alt="06-review-next-tasks" src="https://github.com/user-attachments/assets/ababe35f-9f06-472b-9a74-350dd11f7004" />

## AI 모델과 검증

기본 모델은 다음과 같습니다.

```text
Qwen/Qwen2.5-3B-Instruct
```

Issue 생성 전 다음 조건을 검사합니다.

- 비어 있는 진행사항을 출력하지 않습니다.
- `-`, `없음` 같은 자리표시자 항목을 허용하지 않습니다.
- 기본 신뢰도 기준 `0.75` 미만의 개선점을 제외합니다.
- 완료된 작업이 개선점에 포함되었는지 검사합니다.
- 개선점이 있으면 대응하는 다음 작업을 생성합니다.
- 수집한 변경 파일과 일치하지 않는 근거를 차단합니다.
- 필수 함수가 정의되지 않은 경우 실행을 중단합니다.
- AI가 올바른 JSON을 반환하지 않으면 Issue를 생성하지 않습니다.

## 프로젝트 구조

```text
github-weekly-project-reviewer/
├── .github/
│   └── workflows/
│       └── weekly-review.yml
├── docs/
│   └── images/
│       ├── 01-run-workflow.png
│       ├── 02-workflow-success.png
│       ├── 03-generated-issue.png
│       ├── 04-review-summary.png
│       ├── 05-review-improvements.png
│       └── 06-review-next-tasks.png
├── scripts/
│   └── weekly_review.py
├── .gitignore
├── README.md
└── requirements.txt
```

## 실행 기록

실행 결과는 기본적으로 다음 SQLite 파일에 저장됩니다.

```text
review_trace.db
```

GitHub Actions 실행 후 `weekly-review-trace` Artifact를 내려받으면 실행 기록을 확인할 수 있습니다.

저장되는 정보는 다음과 같습니다.

- 실행 시작 및 종료 시간
- 성공, 실패, 건너뜀 상태
- 수집한 분석 근거
- AI 호출 횟수와 처리 시간
- 예상 토큰 사용량
- 생성된 개선점과 신뢰도

SQLite는 Python 기본 기능이며 Docker와는 관계가 없습니다.

## 현재 상태와 제한사항

현재 다음 기능까지 구현되어 있습니다.

- GitHub 활동 수집
- Qwen2.5-3B 기반 한국어 리뷰 생성
- 정적 검사와 AI 분석 결과 통합
- JSON 및 근거 검증
- 낮은 신뢰도 필터링
- 중복 결과 제거
- GitHub Issue 자동 생성
- SQLite 실행 기록

AI가 완료된 작업과 아직 필요한 작업을 문맥에 따라 잘못 구분할 가능성은 남아 있습니다. 따라서 생성된 리뷰는 최종 결정이 아니라 사람이 확인하는 초안으로 사용해야 합니다.

이 리뷰어는 다음 작업을 자동으로 수행하지 않습니다.

- 대상 저장소 코드 수정
- Docker 이미지 직접 빌드 및 실행
- 테스트 코드 자동 작성
- Pull Request 생성 또는 병합
- 생성한 Issue 자동 종료

실제 개선 작업은 리뷰 결과와 코드를 확인한 뒤 사용자가 결정합니다.
