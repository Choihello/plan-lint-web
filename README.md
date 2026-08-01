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

**예산은 체커별 몫으로 배분한다.** 총량만 막으면 먼저 도는 체커가 독식해 뒤쪽 검사가
아예 실행되지 않는다(섹션 38개 문서 실측: unsupported-claim이 24회를 전부 소진하고
내부 모순·구체성 부족·보강 제안이 미실행). 문서 전체를 한 번만 보는 검사를 먼저 확보한다:

| 검사 | 몫 (총 24 기준) | 성격 |
|---|---|---|
| logic-gap (논리 단절) | 2 | 프로파일 섹션 쌍 — 고정 |
| internal-contradiction (내부 모순) | 1 | 문서 전체 1회 — 고정 |
| enrich (보강 제안) | 1 | 문서 전체 1회 — 고정 |
| unsupported-claim (근거 없는 주장) | 10 | 섹션 수에 비례 — 남은 예산 균등 분배 |
| vague-goal (구체성 부족) | 10 | 섹션 수에 비례 — 남은 예산 균등 분배 |

몫을 넘으면 오류가 아니라 **어떤 검사가 앞부분까지만 수행됐는지 이름으로 고지**하고
나머지 결과는 그대로 반환한다.

- 문서 1건 최대 24회 · 전역 하루 50건 → **하루 최대 약 1,200회 호출**

**비용 (모델 `gpt-5.6-luna` 기준, 입력 $0.20 / 출력 $1.20 per 1M tokens, 2026-07-30 인하가)**

| 구분 | 요청 1건 | 전역 50건/일 | 월 환산 |
|---|---|---|---|
| **gpt-5.6-luna (현재)** | 약 **18원** | 약 **920원** | 약 **2.8만원** |
| gpt-4.1 (이전) | 약 144원 | 약 7,200원 | 약 21.5만원 |

- 근거: 6.5만 자 문서 실측 입력 22,500토큰 + 출력 24콜 × 약 300토큰, 환율 1,400원 가정
- 위 값은 **상한**이다(전역 캡이 꽉 찬 경우). 관리자 100건/일까지 더해도 하루 약 1,840원.
- 272K 토큰 초과 요청은 입력 2배·출력 1.5배로 과금되나, 텍스트 상한(10만 자)상 도달하지 않는다.

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
| PLW_LLM_MODEL | (엔진 기본 gpt-4.1) | 사용할 LLM 모델 ID — 현재 운영값 `gpt-5.6-luna` |
| PLW_LLM_TIMEOUT | 60 | 요청 전체 LLM 검사 타임아웃(초) |
| PLW_LLM_REQUEST_TIMEOUT | 25 | LLM 공급자 SDK 단일 호출 타임아웃(초) |
| PLW_MAX_LLM_CALLS | 24 | **요청당 LLM 호출 절대 상한** (초과 시 부분 검사로 고지) |
| PLW_LLM_CONCURRENCY | 3 | 동시 LLM 검사 상한 |
| PLW_CONVERT_CONCURRENCY | 2 | 동시 파일 변환 상한 (검사와 별도 풀) |
| PLW_MAX_INFLIGHT_LINT | 8 | 동시 진단 요청 상한 — 초과 시 503 + Retry-After |
| PLW_QUOTA_DB | quota.sqlite3 | 쿼터 DB 경로 |
| PLW_QUOTA_SALT | (내장) | IP 해시 솔트 — **운영에서는 반드시 고엔트로피 값으로 설정** (기본값은 코드에 공개돼 있어 해시 역산 가능) |
| PLW_ADMIN_TOKEN | (없음) | 관리자 토큰 — `/?admin=<토큰>`으로 접속. 비우면 관리자 모드 비활성 |
| PLW_ADMIN_DAILY | 100 | **관리자 일일 상한** — 토큰 유출 시 무제한 호출 차단. 공개 전역 캡과 별도 축 |
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
