# plan-lint-web

정부 창업지원사업 사업계획서를 제출 전에 진단하는 웹 서비스.
[plan-lint CLI](https://github.com/Choihello/plan-lint)의 엔진(`planlint`)을 그대로 사용한다.

- 파일 업로드(.hwpx/.pdf/.docx) 또는 텍스트 붙여넣기 → 결함 진단 리포트
- 룰 검사 3종 무료·무제한 / AI 정밀 검사 3종은 IP당 하루 1회 (전역 일일 상한 있음)
- **완전 무저장**: 업로드 문서는 메모리에서만 처리하고 즉시 폐기

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
| PLW_LLM_TIMEOUT | 60 | LLM 검사 타임아웃(초) |
| PLW_LLM_CONCURRENCY | 3 | 동시 LLM 검사 상한 |
| PLW_QUOTA_DB | quota.sqlite3 | 쿼터 DB 경로 |
| PLW_QUOTA_SALT | (내장) | IP 해시 솔트 — 운영 시 변경 권장 |
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
