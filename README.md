# GitHub Weekly Project Reviewer

GitHub 저장소의 최근 변경사항을 수집하고 AI로 분석하여 주간 리뷰 Issue를 생성하는 자동화 도구입니다.

커밋과 Pull Request를 근거로 잘 진행된 점, 개선이 필요한 점, 다음 추천 작업을 정리합니다.

> 생성된 리뷰는 AI 초안입니다.  
> 코드를 자동으로 수정하거나 Issue를 자동으로 종료하지 않습니다.

## 동작 방식

```text
GitHub Actions 실행
→ 최근 커밋 및 Pull Request 수집
→ 변경 파일과 diff 분석
→ 정적 검사와 AI 분석
→ 결과 검증 및 중복 제거
→ GitHub Issue 생성
→ 실행 기록을 SQLite에 저장
```

1. 설정된 기간의 커밋과 Pull Request를 GitHub API로 수집합니다.
2. 변경 파일과 코드 diff를 분석 근거로 정리합니다.
3. Hugging Face의 AI 모델로 주간 변경사항을 분석합니다.
4. 정적 검사 결과와 AI 분석 결과를 합칩니다.
5. 낮은 신뢰도, 중복 항목, 완료된 작업의 잘못된 분류를 검사합니다.
6. 최종 검증을 통과한 경우에만 Weekly Project Review Issue를 생성합니다.

## 주요 기능

- 최근 커밋 및 Pull Request 수집
- 변경된 파일과 diff 기반 분석
- 잘 진행된 점, 개선점, 다음 작업 분류
- Python 변경 시 테스트 파일 존재 여부 확인
- Dockerfile 변경 시 빌드·실행 검증 여부 확인
- API 키, 토큰, 비밀번호 형태의 하드코딩 검사
- 이전 리뷰와 현재 결과의 중복 제거
- 각 항목을 최대 5개까지 출력
- AI 응답 JSON 형식 검증
- 실제 변경 파일과 일치하지 않는 근거 차단
- 개선점이 있으면 대응하는 다음 작업 생성
- SQLite를 이용한 실행 및 AI 호출 기록 저장
- 같은 날짜의 열린 리뷰 Issue 중복 생성 방지

## 사용 모델

기본 모델은 다음과 같습니다.

```text
Qwen/Qwen2.5-3B-Instruct
```

환경변수 `HF_MODEL`을 설정하면 다른 Hugging Face 모델로 변경할 수 있습니다.

## 프로젝트 구조

```text
github-weekly-project-reviewer/
├── .github/
│   └── workflows/
│       └── weekly-review.yml
├── scripts/
│   └── weekly_review.py
├── .gitignore
├── README.md
└── requirements.txt
```

## 설정 방법

### 1. GitHub Secrets 등록

저장소의 `Settings → Secrets and variables → Actions`에서 다음 값을 등록합니다.

| 이름 | 설명 |
|---|---|
| `TARGET_GITHUB_TOKEN` | 분석 대상 저장소를 조회하고 Issue를 생성할 GitHub 토큰 |
| `HF_TOKEN` | Hugging Face 모델 호출 토큰 |

GitHub 토큰에는 분석 대상 저장소에 대한 읽기 권한과 Issue 생성 권한이 필요합니다.

토큰 값을 코드나 README에 직접 작성하면 안 됩니다.

### 2. 분석 대상 저장소 설정

기본 분석 대상은 다음 저장소입니다.

```text
leeony2636/docker-fastapi-sentiment-api
```

다른 저장소를 분석하려면 워크플로에서 `TARGET_REPO` 환경변수를 설정합니다.

```yaml
env:
  TARGET_REPO: owner/repository
```

## 실행 방법

GitHub 저장소의 `Actions` 메뉴에서 Weekly Project Review 워크플로를 선택한 뒤 `Run workflow`를 실행합니다.

워크플로가 완료되면 분석 대상 저장소의 Issues에 다음 형식의 리뷰가 생성됩니다.

```text
Weekly Project Review - YYYY-MM-DD
```

## 생성되는 리뷰

리뷰 Issue에는 다음 내용이 포함됩니다.

- 분석 저장소와 기간
- 사용한 AI 모델
- AI 호출 횟수
- 예상 입력·출력 토큰
- 처리 시간
- 중복 및 검증 제외 건수
- 이번 주 변경사항
- 잘 진행된 점
- 개선이 필요한 점
- 다음 추천 작업
- 근거 커밋과 Pull Request

## 검증 규칙

Issue 생성 전 다음 조건을 검사합니다.

- 비어 있는 진행사항은 출력하지 않습니다.
- `-`, `없음` 같은 자리표시자 항목은 허용하지 않습니다.
- 설정된 기준보다 신뢰도가 낮은 개선점은 제외합니다.
- 완료된 작업은 개선점이 아닌 진행사항으로 분류합니다.
- 개선점이 있다면 대응하는 다음 작업을 생성합니다.
- 수집한 변경 파일과 일치하지 않는 근거는 허용하지 않습니다.
- 필수 함수가 정의되지 않은 경우 실행을 중단합니다.
- AI가 올바른 JSON을 반환하지 않으면 Issue를 생성하지 않습니다.

기본 개선점 신뢰도 기준은 `0.75`입니다.

## 실행 기록

실행 결과는 기본적으로 다음 SQLite 파일에 저장됩니다.

```text
review_trace.db
```

저장되는 정보는 다음과 같습니다.

- 실행 시작 및 종료 시간
- 성공, 실패, 건너뜀 상태
- 분석 근거
- AI 호출 횟수와 처리 시간
- 예상 토큰 사용량
- 생성된 개선점과 신뢰도

SQLite는 Python 기본 기능이며 Docker와는 관계가 없습니다.

## 현재 상태

현재 다음 기능까지 구현되어 있습니다.

- GitHub 활동 수집
- Qwen2.5-3B 기반 한국어 리뷰 생성
- 정적 검사와 AI 결과 통합
- JSON 및 근거 검증
- 낮은 신뢰도 필터링
- 중복 리뷰 제거
- GitHub Issue 자동 생성
- SQLite 실행 추적

AI가 완료된 작업과 필요한 작업을 문맥에 따라 잘못 구분할 가능성은 남아 있습니다. 따라서 생성된 리뷰는 최종 결정이 아니라 사람이 확인하는 초안으로 사용해야 합니다.

## 현재 하지 않는 작업

이 리뷰어는 다음 작업을 자동으로 수행하지 않습니다.

- 대상 저장소 코드 수정
- Docker 이미지 직접 빌드 및 실행
- 테스트 코드 자동 작성
- Pull Request 생성 또는 병합
- 생성한 Issue 자동 종료

실제 개선 작업은 리뷰 결과와 코드를 확인한 뒤 사용자가 결정합니다.
