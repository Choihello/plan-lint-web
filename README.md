# plan-lint-web

정부 창업지원사업 사업계획서를 제출 전에 진단하는 웹 서비스.
[plan-lint CLI](https://github.com/Choihello/plan-lint)의 엔진(`planlint`)을 그대로 사용한다.

- 파일 업로드(.hwpx/.pdf/.docx) 또는 텍스트 붙여넣기 → 결함 진단 리포트
- 룰 검사 3종(missing-section·length-violation·numeric-consistency) 무료·무제한
- AI 정밀 검사 **4종**(logic-gap·unsupported-claim·internal-contradiction·vague-goal) + 보강 제안 1회는 IP당 하루 1회 (전역 일일 상한 있음)
- **저장하지 않음**: 업로드 문서는 메모리에서만 처리하고 즉시 폐기.
  단 AI 정밀 검사를 켜면 분석을 위해 **문서 내용이 외부 제공자(OpenAI)로 전송**된다. 끄면 전송 없음.

### 요청당 비용 상한

엔진의 per-section 체커(unsupported-claim·vague-goal)는 **문서의 헤딩 섹션 수만큼** 호출한다
(소제목 20개 문서 실측: 42회). 따라서 요청당 호출 수를 `PLW_MAX_LLM_CALLS`(기본 24)로 강제한다.
예산을 넘으면 오류가 아니라 "앞부분 위주로 N회까지만 검사했다"고 고지하고 결과를 반환한다.

- 문서 1건 최대 24회 · 전역 하루 50건 → **하루 최대 약 1,200회 호출**
- gpt-4.1 기준 문서당 최대 약 300~350원, 하루 최대 약 1.5만~1.7만원 (상한값이며 실제는 문서 길이에 비례)

## 로컬 실행

    pip install -r requirements-dev.txt
    uvicorn app.main:app --port 8000
    # AI 정밀 검사를 켜려면: .env.example을 .env로 복사해 OPENAI_API_KEY 입력 (자동 로드)

## 테스트 / 배포 후 확인

    pytest -q
    python scripts/smoke.py https://<배포 URL>

## 설정 (환경변수)

| 변수 | 기본값 | 의미 |
|---|---|---|
| PLW_PER_IP_DAILY | 1 | IP당 하루 LLM 검사 횟수 |
| PLW_GLOBAL_DAILY | 50 | 전역 하루 LLM 검사 횟수 |
| PLW_MAX_FILE_BYTES | 5242880 | 업로드 파일 상한 |
| PLW_MAX_TEXT_CHARS | 100000 | 변환 후 텍스트 상한 |
| PLW_LLM_TIMEOUT | 60 | 요청 전체 LLM 검사 타임아웃(초) |
| PLW_LLM_REQUEST_TIMEOUT | 25 | LLM 공급자 SDK 단일 호출 타임아웃(초) |
| PLW_MAX_LLM_CALLS | 24 | **요청당 LLM 호출 절대 상한** (초과 시 부분 검사로 고지) |
| PLW_LLM_CONCURRENCY | 3 | 동시 LLM 검사 상한 |
| PLW_CONVERT_CONCURRENCY | 2 | 동시 파일 변환 상한 (검사와 별도 풀) |
| PLW_MAX_INFLIGHT_LINT | 8 | 동시 진단 요청 상한 — 초과 시 503 + Retry-After |
| PLW_QUOTA_DB | quota.sqlite3 | 쿼터 DB 경로 |
| PLW_QUOTA_SALT | (내장) | IP 해시 솔트 — 운영 시 변경 권장 |
| PLW_ADMIN_TOKEN | (없음) | 관리자 무제한 토큰 — `/?admin=<토큰>`으로 접속하면 쿼터 없이 AI 검사. 비우면 비활성 |
| PLW_TRUST_PROXY_HEADERS | 1 | 프록시 IP 헤더 신뢰 여부 — 신뢰 프록시(Fly 등) 뒤가 아니면 0으로 |

## Docker 빌드·배포

Docker 미설치 환경이므로 로컬 빌드 검증은 배포 시점으로 미룬다.
배포 환경에서 다음 명령으로 빌드·테스트:

    docker build -t plan-lint-web .
    docker run --rm -p 8080:8080 plan-lint-web
    # 다른 터미널에서: curl -s http://localhost:8080/api/quota
    # Expected: {"remaining_today":1}

## Fly.io 배포

설정 파일(`fly.toml`)이 준비됐으므로 사용자가 다음 명령으로 배포:

    fly launch --copy-config --no-deploy   # 즉시 배포는 건너뛴다 — 볼륨이 아직 없음
    fly volumes create plan_lint_data --size 1  # 첫 배포 전 필수 — 쿼터 DB 영속화용
    fly secrets import --app plan-lint-web < .env   # .env 파일의 키를 서버에 등록
    fly deploy
    python scripts/smoke.py https://plan-lint-web.fly.dev

쿼터 DB(`/data/quota.sqlite3`)는 볼륨에 저장되며 머신별로 분리된다.
따라서 `fly scale count 1`로 머신 수를 1대로 고정하는 것이 전제 조건이다
(스케일아웃 시 IP당·전역 일일 상한이 머신 수만큼 사실상 늘어난다).
