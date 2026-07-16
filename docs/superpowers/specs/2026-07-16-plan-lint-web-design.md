# plan-lint-web 설계 문서

작성일: 2026-07-16 · 상태: 사용자 승인 완료 (브레인스토밍 세션)
선행 문서: `Teddy/plan-lint-web-kickoff.md`, CLI repo `github.com/Choihello/plan-lint`

## 1. 무엇을 만드나

비개발 예비창업자가 **파일 업로드(또는 붙여넣기)만으로** 정부 창업지원사업 사업계획서의 결함 진단을 받는 **공개 운영 웹 서비스**. CLI v1의 엔진 `planlint`를 그대로 재사용하고, 그 위에 파일 변환 → API → 결과 화면의 얇은 3겹만 얹는다. CLI repo는 완결 포폴로 불변, 웹은 이 새 repo(`plan-lint-web`, 로컬 `Teddy/plan-lint-web`)가 담당.

## 2. 확정된 제품 결정

| 결정 | 확정안 |
|---|---|
| 서비스 성격 | 공개 운영 서비스 (데모 아님) |
| LLM 비용 | 룰 검사 무료·무제한 + LLM 검사는 운영자 부담 횟수제한 |
| 횟수제한 | **IP당 하루 1회, 전역 하루 50회** (환경변수로 조정 가능) |
| 입력 포맷 | .hwpx / .pdf / .docx 업로드 + 텍스트 붙여넣기. 구형 .hwp는 ".hwpx로 재저장" 안내로 우회 |
| v1 범위 | 로그인 없음 · IP 기반 제한 · "업로드 → 결과 한 화면". 로그인·결제·히스토리는 v2 |
| 저장 정책 | **완전 무저장** — 파일·변환 텍스트·진단 결과 모두 응답 후 즉시 폐기. 화면에 명시 |
| 디자인 톤 | 친숙한 진단 리포트 (건강검진 결과지 느낌 — 부드럽되 진단의 신뢰감 유지) |

## 3. 아키텍처 — 단일 파이썬 풀스택

FastAPI 앱 하나가 전부 담당: `planlint` 직접 import, 파일 변환, LLM 호출, 횟수제한, 정적 프론트 서빙. 배포는 컨테이너 1개(Fly.io 기준, Render/Railway 호환 Dockerfile). DB 없음 — 유일한 상태는 쿼터 카운터(로컬 SQLite).

```
plan-lint-web/
├─ app/
│  ├─ main.py            # FastAPI 앱 — 정적 프론트 서빙 + API 라우트
│  ├─ converters/        # hwpx.py / pdf.py / docx.py / plain.py
│  ├─ lint.py            # planlint 엔진 호출 어댑터
│  ├─ quota.py           # IP별·전역 LLM 횟수제한 (SQLite)
│  └─ static/            # 프론트 — 빌드 도구 없는 바닐라 HTML/CSS/JS 1페이지
├─ tests/
├─ Dockerfile
└─ docs/superpowers/specs/  # 이 문서
```

- `planlint` 설치: `pip install git+https://github.com/Choihello/plan-lint` — 코드 복사 없음, CLI repo가 단일 진실 원천.
- 엔진 진입점(검증 완료): `Document.from_markdown(text)` → `run_checks(doc, profile, checkers, llm_available=)` → `Finding.to_dict()`. 엔진 수정 없이 import만으로 충분.
- `ANTHROPIC_API_KEY`는 서버 환경변수. LLM provider는 CLI의 `make_client()` 재사용.
- 프론트에 프레임워크·번들러 없음: 한 화면 서비스라 바닐라가 유지보수·배포 모두 유리.

## 4. API

### `POST /api/lint`
multipart `file` 또는 form `text` 중 하나 + `use_llm`(bool, 기본 true).

처리 순서:
1. 크기 검증 — 파일 5MB, 변환 후 텍스트 100,000자 상한 (환경변수)
2. 확장자+매직바이트로 변환기 선택 → 마크다운 텍스트. 실패 시 `422` + 붙여넣기 폴백 안내
3. `Document.from_markdown()` → **룰 체커 3종은 항상 실행** (missing-section / length-violation / numeric-consistency). 프로파일은 v1에서 CLI 기본값 `psst-standard` 고정 (공고 선택 UI는 v2)
4. `use_llm=true`이고 IP·전역 쿼터가 남아 있으면 LLM 체커 3종(logic-gap / unsupported-claim / internal-contradiction) 추가 실행
5. 응답:

```json
{
  "findings": [ /* Finding.to_dict() 배열 */ ],
  "converted_text": "…",
  "meta": {
    "llm_ran": false,
    "llm_skipped_reason": null | "quota_ip" | "quota_global" | "llm_error",
    "remaining_today": 0,
    "conversion_warnings": ["표 3개가 텍스트로 평탄화됨"]
  }
}
```

정직성 원칙(CLI 계승): LLM이 안 돌았으면 이유와 함께 명시하고, 룰 결과는 항상 반환. 쿼터 초과는 요청 거부가 아니라 "룰 검사만 실행"으로 강등.

### `GET /api/quota`
현재 IP의 오늘 남은 LLM 횟수 (입력 화면 표시용).

## 5. 횟수제한 (`quota.py`)

- IP당 하루 1회 LLM 검사, 전역 하루 50회 캡. 룰 검사는 무제한.
- 비용 상한: 문서당 LLM 3콜 × 50 = **하루 최대 150콜**이 산술적 절대 상한.
- SQLite에 `(ip_hash, date, count)` — IP는 해시로만 저장, 지난 날짜 행은 매일 삭제.
- 추가 방어: LLM 요청 타임아웃 60초, 동시 LLM 실행 세마포어 3.
- 모든 수치(1회/50회/5MB/100k자/60초/세마포어)는 환경변수로 운영 중 조정.

## 6. 파일 변환 (`converters/`)

공통 인터페이스: `convert(data: bytes, filename: str) -> ConversionResult(text, warnings)`.

| 포맷 | 방식 | 비고 |
|---|---|---|
| .hwpx | zipfile + XML 파싱 (표준 포맷) | 표는 텍스트로 평탄화, warnings에 고지 |
| .hwp | **지원 안 함** | "한글에서 .hwpx로 다시 저장" 안내 + 붙여넣기 폴백 |
| .docx | python-docx | |
| .pdf | pymupdf 텍스트 추출 | 스캔 PDF(텍스트 없음)는 변환 실패 처리 |
| 붙여넣기 | 그대로 사용 | 모든 실패의 최종 폴백 |

구현 시작 시 각 포맷 실제 샘플로 라이브러리 검증부터 (특히 hwpx 표 구조).

## 7. 프론트 — 한 페이지, 두 상태

**상태 1 (입력):** 서비스명 + "제출 전에 심사자가 볼 결함을 미리 확인하세요" / 탭 2개(파일 올리기·텍스트 붙여넣기, 드래그앤드롭) / .hwp 업로드 시 재저장 안내 / **"업로드한 문서는 서버에 저장되지 않으며 진단 후 즉시 폐기됩니다"** 고정 문구 / AI 정밀 검사 토글(기본 켬) + 오늘 남은 횟수.

**상태 2 (진단 리포트):** 요약 헤더 — 심각도별 배지(치명/주의/참고), 점수·합격예측 없음(CLI 원칙). 좌우 2단: 왼쪽 변환 원문(결함 인용 구간 심각도 색 하이라이트), 오른쪽 결함 카드(체커명은 한국어 라벨 — 예: logic-gap → "논리 연결이 끊겨요"; 설명 + 원문 인용 + 수정 제안). 카드 ↔ 하이라이트 상호 스크롤. 하이라이트는 `Finding.quotes`를 `converted_text`에서 문자열 매칭(엔진 사후검증이 인용 실존을 보장; 매칭 실패 시 카드만 표시). LLM 미실행 시 정직 배너. 하단 "결과 복사"(마크다운).

기존 진단 시연 아티팩트(원문 주석 + 결함 카드 + LLM 미실행 정직 표기)가 시각 원형.

## 8. 에러 처리

| 상황 | 동작 |
|---|---|
| 변환 실패·빈 텍스트 | "파일을 읽지 못했어요" + 붙여넣기 탭 자동 전환 |
| 크기 초과 | 상한 명시하고 거부 |
| LLM 실패·타임아웃 | 룰 결과 반환 + `llm_skipped_reason: "llm_error"` 배너. 전체 실패로 만들지 않음 |
| 변환 손실(표 등) | 결과 표시 + `conversion_warnings` 고지 |

## 9. 보안·개인정보

- 무저장: 업로드는 메모리에서만 처리(디스크 임시파일 없음), 응답 후 참조 소멸. 액세스 로그에 본문·파일명 미기록.
- IP 해시만 쿼터 테이블에, 매일 정리.
- 업로드 검증: 확장자+매직바이트, hwpx/docx ZIP 폭탄 방어(해제 크기 상한).
- 프롬프트 인젝션: CLI의 코드 사후검증(인용 실존 확인) 경로를 그대로 사용 — 환각·조작 출력 방화벽.

## 10. 테스트 (CLI와 동일한 검증 하네스 철학)

- 변환기: 포맷별 실제 샘플 픽스처 → 골든 텍스트 비교.
- API: FastAPI TestClient + LLM mock — 정상 / 쿼터 소진 / 전역 캡 / 변환 실패 / LLM 에러 시나리오.
- E2E 스모크: 배포 후 실제 픽스처 업로드 → 결함 검출 확인 스크립트.
- CI: GitHub Actions, LLM 키 없이 전부 통과(mock).

## 11. v2로 미룬 것

로그인·계정 / 결제 / 진단 히스토리 / .hwp 직접 변환 / 프로파일(공고) 선택 UI 확장 / 결과 PDF 내보내기.
