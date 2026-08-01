# plan-lint-web 보완 의견서

> 대상: Claude 코드 수정 작업
> 작성 기준: 2026-08-01 종합 코드 리뷰, 자동 테스트, 실제 API·브라우저 QA
> 현재 판정: **핵심 기능은 동작하지만 공개 운영 배포는 보류 권고**

## 1. 작업 목표

현재 기능과 사용자 경험을 유지하면서 아래 운영 위험을 제거한다.

1. 느린 변환·LLM 요청이 ASGI 이벤트 루프 전체를 막지 않아야 한다.
2. 타임아웃 이후 유료 LLM 작업이 계속되거나 쿼터 환불로 비용 상한이 우회되지 않아야 한다.
3. 공개 서비스가 파일 변환 요청이나 룰 검사 요청으로 쉽게 자원 고갈되지 않아야 한다.
4. 관리자 토큰이 URL, 브라우저 저장소, 접근 로그에 노출되지 않아야 한다.
5. 개인정보·외부 LLM 전송 고지가 실제 처리 방식과 일치해야 한다.
6. 동일 커밋은 항상 동일한 의존성으로 재현 가능하게 빌드돼야 한다.

기존 정상 동작인 파일 변환, 룰 검사, LLM 실패 시 룰 결과 강등, 쿼터 안내, 결과 화면은 회귀시키지 않는다.

## 2. 현재 검증 기준선

- `pytest`: **63 passed**
- `pip check`: 통과
- `node --check app/static/app.js`: 통과
- 실제 API·브라우저 QA: 22개 시나리오 중 20 PASS, 2 FAIL
- 현재 작업 트리에는 기존 `quota.sqlite3-journal` 변경이 있다. 작업 전 반드시 보존하고 무관한 내용으로 덮어쓰지 않는다.
- 로컬 `.env`에는 `PLW_ADMIN_TOKEN`이 설정돼 있다. 실제 값은 출력·로그·문서·테스트 픽스처에 포함하지 않는다.

## 3. P0: 배포 전 반드시 수정

### P0-1. 비동기 요청의 이벤트 루프 차단 제거

대상:

- `app/main.py:100-103`
- `app/main.py:141-164`

현재 `async def lint()` 안에서 `concurrent.futures.Future.result(timeout=...)`를 호출한다. 이는 작업을 스레드로 보냈더라도 ASGI 이벤트 루프를 동기 차단한다.

실측 재현에서는 1.5초짜리 lint 작업 하나가 실행되는 동안 별도 `GET /api/quota` 요청도 약 1.51초간 실행되지 못했다. 운영 환경에서는 변환 30초, LLM 60초 동안 전체 서비스가 정지할 수 있다.

요구 변경:

- 변환과 lint 결과 대기를 `await` 가능한 방식으로 전환한다.
- 이벤트 루프에서는 `Future.result()`, `time.sleep()`, 동기 LLM SDK 대기를 직접 수행하지 않는다.
- 룰 전용 폴백도 이벤트 루프에서 동기 실행하지 않는다.
- 변환 작업과 LLM 작업의 동시성 경계를 분리해 한 종류의 포화가 다른 종류를 모두 막지 않게 한다.

수용 기준:

- 2초 걸리는 lint 요청 중에도 `/api/quota`가 300ms 이내 응답한다.
- 동시에 여러 lint 요청을 보내도 정적 파일과 quota 엔드포인트가 응답한다.
- 기존 API 응답 스키마와 한국어 오류 메시지가 유지된다.

### P0-2. 타임아웃·취소·쿼터 환불 정책 수정

대상:

- `app/main.py:129-169`
- `app/lint.py:36-52`
- 상류 `planlint` LLM 클라이언트 설정

현재 `future.result(timeout=...)`의 타임아웃은 대기만 중단한다. 실행 중인 LLM 호출은 계속될 수 있는데 쿼터는 즉시 환불된다. 재현 결과 응답 시점에는 쿼터가 복구됐지만 백그라운드 작업은 이후 완료됐다.

또한 현재 `psst-standard` 문서는 다음과 같이 최대 **12회** LLM 호출이 가능하다.

- logic pair 2회
- unsupported-claim: 섹션별 최대 4회
- vague-goal: 섹션별 최대 4회
- internal-contradiction 1회
- suggestion enrichment 1회

문서에 적힌 3~4회 가정과 실제 비용 구조가 일치하지 않는다.

요구 변경:

- OpenAI/Anthropic SDK 자체에 연결·읽기·전체 요청 timeout을 명시한다.
- 하나의 웹 요청에 대한 전체 시간 예산과 호출 수·토큰 예산을 둔다.
- 외부 유료 호출이 시작된 요청을 단순 웹 타임아웃만으로 자동 환불하지 않는다.
- 환불은 “외부 호출이 시작되지 않았음”이 확실한 경우 또는 공급자 오류 정책상 비용이 발생하지 않은 경우로 제한한다.
- 실제 실행 중인 작업을 중단할 수 없는 구조라면 중단 가능하도록 클라이언트/프로세스 경계를 재설계한다.
- 관리자 요청에도 별도의 절대 비용 상한과 동시성 상한을 둔다.

수용 기준:

- 타임아웃 응답 이후 동일 작업의 LLM 호출이 계속되지 않는다.
- 계속될 수밖에 없는 작업은 쿼터가 환불되지 않으며 중복 재시도가 차단된다.
- 요청당 최대 호출 수와 최대 토큰이 코드·설정·README에 동일하게 명시된다.
- 타임아웃, semaphore 획득 실패, 공급자 오류별 쿼터 상태를 테스트한다.

### P0-3. 변환·룰 검사 자원 고갈 방어

대상:

- `app/main.py:29-33`
- `app/main.py:95-104`
- `app/converters/__init__.py:29-36`
- `app/converters/pdf.py`

현재 executor의 작업 큐는 제한이 없고, 타임아웃된 변환도 계속 실행된다. 공격자가 5MB 이하의 복잡한 PDF/DOCX/HWPX를 반복 제출하면 512MB Fly VM의 CPU·메모리와 작업 큐를 고갈시킬 수 있다. `use_llm=false` 요청에는 별도 속도 제한도 없다.

요구 변경:

- bounded queue 또는 명시적 backpressure를 도입한다.
- IP별·전역 요청 속도 제한과 동시 변환 제한을 둔다.
- 큐가 찼을 때 빠르게 `429` 또는 `503`으로 거절하고 `Retry-After`를 제공한다.
- 강제 중단이 필요한 변환은 격리 프로세스에서 실행하고 CPU·메모리·시간 제한을 적용한다.
- ZIP 파일은 총 해제 크기뿐 아니라 항목 수, 개별 항목 크기, 압축비를 제한한다.
- PDF는 페이지 수·객체 수 등 합리적인 처리 한계를 둔다.

수용 기준:

- 제한을 넘는 동시 요청에서 메모리가 요청 수에 비례해 무제한 증가하지 않는다.
- 포화 상태에서도 `/api/quota`와 정적 화면이 정상 응답한다.
- 큐 포화, ZIP 다수 항목, 고압축 파일, 과도한 PDF 페이지 테스트가 있다.

### P0-4. 관리자 인증 방식 교체

대상:

- `app/static/app.js:21-35`
- `app/main.py:36-42`
- `README.md:33`

현재 `/?admin=<token>`으로 토큰을 전달한 뒤 `localStorage`에 장기 저장한다. 최초 URL은 브라우저·프록시·접근 로그에 남을 수 있고, 동일 origin의 스크립트는 토큰을 읽을 수 있다. 토큰 탈취 시 쿼터 없는 유료 호출이 가능하다.

요구 변경:

- URL query token과 `localStorage` 사용을 제거한다.
- 가능하면 별도 관리자 인증 후 짧은 수명의 `HttpOnly`, `Secure`, `SameSite` 세션 쿠키를 사용한다.
- 관리자 권한에도 요청·비용·동시성 절대 상한을 둔다.
- 인증 실패와 관리자 요청에 비밀값을 제외한 감사 로그를 남긴다.
- 현재 사용 중인 관리자 토큰은 배포 후 회전한다.

수용 기준:

- 토큰이 URL, 브라우저 저장소, HTML, JS, 서버 접근 로그에 나타나지 않는다.
- 세션 만료·로그아웃·잘못된 인증·권한 없는 요청 테스트가 있다.
- 일반 사용자 쿼터와 관리자 상한이 각각 검증된다.

## 4. P1: 운영 안정성·보안 보완

### P1-1. SQLite 트랜잭션과 추적 저널 정리

대상:

- `app/quota.py:32-42`
- `app/quota.py:68-71`
- `.gitignore`
- `quota.sqlite3-journal`

`remaining()`이 `_counts()`를 호출하면 지난 날짜 행을 `DELETE`하지만 commit/rollback하지 않는다. 실제 재현에서 연결이 쓰기 트랜잭션을 유지했고 다른 연결의 쓰기가 `database is locked`로 실패했다.

또한 런타임 `quota.sqlite3-journal`이 Git에 추적되고 있다. 파일 내부에는 해시 형태 문자열과 날짜 데이터가 존재하므로 운영 데이터가 커밋될 위험이 있다.

요구 변경:

- 조회와 만료 데이터 정리를 분리하거나 명시적 transaction/commit을 적용한다.
- SQLite busy timeout, WAL 여부, 다중 프로세스 정책을 명시한다.
- `*.sqlite3-journal`, `*.sqlite3-wal`, `*.sqlite3-shm`을 ignore한다.
- 현재 추적 파일의 index 제거와 과거 Git 이력 정리는 별도 승인 후 수행한다. 임의로 history rewrite하지 않는다.
- 기본 공개 salt를 제거하고 운영 시 고엔트로피 `PLW_QUOTA_SALT`를 필수화한다.

수용 기준:

- `GET /api/quota` 이후 `connection.in_transaction`이 false다.
- 두 연결의 동시 조회·소비·환불·자정 전환 테스트가 통과한다.
- 새 런타임 DB 저널이 Git 상태에 나타나지 않는다.

### P1-2. 의존성 재현성과 공급망 보안

대상:

- `requirements.txt`
- `requirements-dev.txt`
- `Dockerfile`
- `.github/workflows/ci.yml`

현재 핵심 엔진을 `plan-lint@main`에서 설치하며 대부분의 패키지는 최소 버전만 지정돼 있다. 동일 프로젝트 커밋도 시점에 따라 다른 코드로 빌드될 수 있다.

요구 변경:

- `plan-lint`를 검증된 commit SHA 또는 불변 태그로 pin한다.
- 직접·전이 의존성을 lock하고 가능하면 hash 검증을 적용한다.
- CI에 dependency vulnerability scan, secret scan, SBOM 생성을 추가한다.
- Docker 이미지 base도 digest 또는 명확한 patch 버전 전략을 사용한다.
- 컨테이너를 non-root 사용자로 실행한다.

수용 기준:

- 동일 commit을 두 번 빌드했을 때 설치 의존성 버전과 엔진 SHA가 같다.
- CI가 취약점·비밀·lockfile 불일치 시 실패한다.

### P1-3. 개인정보·외부 LLM 처리 고지 정정

대상:

- `app/static/index.html:31-43`
- `app/static/index.html:79-100`
- `README.md:6-8`

현재 화면은 “완전 무저장·즉시 폐기”를 강조하지만 AI 검사가 기본으로 켜져 있고 문서 원문이 외부 LLM 제공자에게 전송된다. 제공자, 처리 목적, 보존 정책, 민감정보 주의가 명확하지 않다.

요구 변경:

- 로컬 서버 무저장과 외부 LLM 전송을 구분해 설명한다.
- AI 검사 전에 외부 제공자 전송 사실과 민감정보 제거 안내를 표시한다.
- 제공자·처리 목적·보존 정책·운영자의 저장 범위를 개인정보 처리방침에 명시한다.
- 법적·제품 결정에 따라 AI 검사를 명시적 opt-in으로 변경하는 안을 우선 검토한다.
- API 응답에는 `Cache-Control: no-store`를 명시한다.

수용 기준:

- 사용자가 AI 전송 여부를 제출 전에 알 수 있다.
- AI 비활성 상태에서는 외부 제공자 호출이 전혀 발생하지 않는다.
- 화면·README·실제 네트워크 동작의 설명이 일치한다.

### P1-4. 프록시와 HTTP 보안 헤더

대상:

- `app/config.py:36-38`
- `app/main.py:45-65`

요구 변경:

- `PLW_TRUST_PROXY_HEADERS` 기본값을 비신뢰로 두는 방안을 적용한다.
- 검증된 Fly 프록시에서 온 요청에 대해서만 플랫폼이 정규화한 IP 헤더를 사용한다.
- 최소한 다음 헤더를 적용한다.
  - `Content-Security-Policy`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `Permissions-Policy`
  - CSP `frame-ancestors` 또는 `X-Frame-Options`
- API 문서 응답에는 `Cache-Control: no-store`를 적용한다.

수용 기준:

- 신뢰 프록시 밖에서는 임의 `X-Forwarded-For`가 쿼터 ID를 바꾸지 못한다.
- 홈·정적 파일·API 응답 헤더 테스트가 있다.

## 5. P2: 품질·접근성·문서 보완

### P2-1. 탭과 결과 상호작용 접근성

대상:

- `app/static/index.html:49-77`
- `app/static/app.js:77-85`
- `app/static/app.js:202-221`

실제 브라우저 QA에서 텍스트 탭으로 전환한 뒤에도 파일 탭이 `aria-selected=true`로 남았다. 결과 카드와 원문 하이라이트도 클릭 전용 `div/mark`라 키보드 사용자가 상호 스크롤 기능을 사용할 수 없다.

요구 변경:

- 탭 전환 때 `aria-selected`, `tabindex`, `aria-controls`를 함께 갱신한다.
- tablist 방향키, Home/End 키보드 패턴을 지원한다.
- 패널에 `role="tabpanel"`과 적절한 연결을 추가한다.
- 결과 카드·하이라이트를 실제 버튼/링크로 만들거나 동등한 키보드·ARIA 동작을 제공한다.
- 리포트 전환 후 제목으로 포커스를 이동하고 로딩 상태를 명확히 알린다.

수용 기준:

- Playwright accessibility snapshot에서 현재 탭만 selected 상태다.
- 키보드만으로 탭 전환, 제출, 카드↔인용 이동, 다시 진단하기가 가능하다.

### P2-2. 보강 제안 줄바꿈과 클립보드 오류 처리

대상:

- `app/static/app.js:211`
- `app/static/app.js:232-248`
- `app/static/style.css:431-439`

요구 변경:

- LLM suggestion의 `\n`을 `white-space: pre-wrap` 등으로 보존한다.
- `navigator.clipboard.writeText()` 완료를 기다린 후에만 성공 메시지를 표시한다.
- 권한 거부·비보안 컨텍스트에서 사용자에게 실패 안내와 대체 복사 방법을 제공한다.

수용 기준:

- 멀티라인 suggestion이 원래 줄바꿈대로 렌더링된다.
- 클립보드 성공·실패 테스트가 각각 실제 UI 상태를 검증한다.

### P2-3. 파일 변환 정확성 보강

대상:

- `app/converters/docx.py:46-51`
- `tests/test_converters_*.py`
- `scripts/smoke.py`

DOCX 병합 셀은 `row.cells`에서 동일 셀이 여러 grid column에 반복될 수 있어 `병합 제목 | 병합 제목`처럼 중복 변환될 수 있다.

요구 변경:

- 동일 underlying cell의 중복을 제거하되 열 위치 정보는 보존한다.
- 실제 HWPX/PDF/DOCX 샘플 픽스처와 전체 변환 결과 golden test를 추가한다.
- 배포 smoke에 실제 파일 업로드 시나리오를 포함한다.
- 손상·암호화·병합 표·중첩 표·스캔 PDF를 검증한다.

### P2-4. 문서와 실제 구현 동기화

대상:

- `README.md`
- `docs/superpowers/specs/*.md`
- `.env.example`

현재 문서에는 Anthropic/OpenAI 전환 이력, LLM checker 수, 문서당 호출 수, 비용 상한이 서로 다르게 기재돼 있다.

요구 변경:

- 지원 provider와 필요한 환경변수를 하나의 표로 통일한다.
- 현재 AI checker는 4종임을 반영한다.
- 실제 요청당 호출·토큰 상한과 일일 최악 비용을 다시 계산한다.
- `/?admin=<token>` 설명은 안전한 새 인증 방식으로 교체한다.
- `favicon.ico` 404를 제거하거나 명시적으로 favicon을 제공한다.

## 6. 필수 회귀 테스트

다음 테스트는 수정과 함께 추가한다.

1. 느린 lint 중 `/api/quota`와 `/style.css`가 즉시 응답한다.
2. LLM timeout 이후 백그라운드 유료 호출이 남지 않는다.
3. timeout·공급자 오류·semaphore 실패별 쿼터 소비/환불 정책이 정확하다.
4. executor/queue 포화 시 빠르게 429 또는 503을 반환한다.
5. 여러 동시 IP의 전역 쿼터가 설정값을 넘지 않는다.
6. 관리자 세션이 URL·localStorage 없이 동작하고 만료된다.
7. 신뢰되지 않은 `X-Forwarded-For`로 IP 쿼터를 우회할 수 없다.
8. 자정 정리 후 SQLite 연결이 미커밋 transaction을 남기지 않는다.
9. 실제 크기 상한 근처 multipart 업로드가 디스크에 spool되지 않는다.
10. 탭 ARIA 상태와 키보드 조작이 정확하다.
11. suggestion 줄바꿈과 clipboard 실패 UI가 정확하다.
12. 실제 DOCX/HWPX/PDF 픽스처의 golden 변환 결과가 유지된다.

기존 테스트를 삭제하거나 약화해서 통과시키지 않는다. 프레임워크 내부 상수만 확인하는 테스트보다 사용자가 관찰할 수 있는 동작을 검증한다.

## 7. 최종 검증 명령

```bash
pytest -q
python -m pip check
node --check app/static/app.js
git diff --check
docker build -t plan-lint-web .
docker run --rm -p 8080:8080 plan-lint-web
python scripts/smoke.py http://127.0.0.1:8080
```

추가로 실제 브라우저에서 데스크톱·모바일 폭, 키보드 전용 탐색, LLM 오류 배너, 파일 변환 오류, 결과 복사와 다시 진단하기를 확인한다.

## 8. 완료 조건

- P0 항목이 모두 수정되고 동시성·취소·비용 상한 재현 테스트가 통과한다.
- 전체 자동 테스트와 Docker 스모크가 통과한다.
- 실제 브라우저 QA에서 P0/P1 실패가 없다.
- 비밀값·사용자 문서·IP 관련 데이터가 로그나 Git diff에 포함되지 않는다.
- `quota.sqlite3-journal` 외 기존 사용자 변경을 임의로 되돌리지 않는다.
- 최종 보고에는 수정 파일, 검증 결과, 미검증 항목, 남은 운영 위험을 구분해 기록한다.
