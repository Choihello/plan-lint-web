# 컨설팅 모드(보강 제안 심화) 설계

작성일: 2026-07-16 · 상태: 사용자 승인 완료 · 선행: v1 스펙(2026-07-16-plan-lint-web-design.md)

## 1. 무엇을

결함 탐지에서 한 발 더 — AI 정밀 검사가 돈 문서에 한해, 각 결함 카드의 `suggestion`을
"방법 + 빈칸 템플릿" 수준의 심화 보강 제안으로 교체한다. 예:
"시장 규모 수치에 출처를 붙이세요. 예: ○○보고서(기관명, 연도) 기준 ○○억원 — 산출식이 자체 추산이면 가정을 함께 적으세요."

## 2. 확정 결정

| 결정 | 확정안 |
|---|---|
| 산출물 형태 | 기존 결함 카드의 suggestion 심화 (별도 리포트 아님) |
| 제공 범위 | AI 정밀 검사에 포함 (별도 버튼·관리자 전용 아님) |
| 구체성 수준 | 방법 + 빈칸 템플릿 — 완성 문장 대필 금지 (지원사업 대필 논란 회피) |
| 구현 위치 | 웹 계층 후처리 (엔진 불변 — 제안 품질은 하네스 검증 불가 영역이므로 엔진 밖) |
| 비용 | 문서당 LLM 콜 3→4회 (+~50원/건), 전역 캡 50/일 기준 하루 최대 약 1만 원 |

## 3. 컴포넌트

**신설 `app/enrich.py`** — 단일 공개 함수:

```python
def enrich_suggestions(findings: list[dict], source: str, llm_client) -> list[dict]
```

- findings가 비었거나 llm_client가 None이면 입력 그대로 반환.
- LLM 콜 1회: system 프롬프트가 형식 강제 —
  ① 각 제안은 "무엇을 어떻게 채울지"를 구체적으로, 내용 값은 ○○ 빈칸으로
  ② 사용자의 문장을 대신 완성하지 마라 (대필 금지)
  ③ 출력은 JSON 배열만: `[{"index": <결함 번호>, "suggestion": "..."}]`
- user 프롬프트: 결함 목록(번호·checker·message·quotes·기존 suggestion) + 원문(20,000자 초과 시 앞 20,000자로 절단 — 결함 인용은 목록에 이미 포함되므로 손실 무해).
- 응답 파싱: JSON 배열에서 index가 범위 안인 항목만 suggestion 교체. 파싱 실패·콜 예외·
  범위 밖 index → 해당(또는 전체) 결함의 기존 suggestion 유지. **어떤 실패도 전파하지 않는다.**

**수정 `app/lint.py`** — `run_lint`에서 LLM 체커가 성공한 경로에만 이어서
`findings = enrich_suggestions(findings_dicts, text, llm_client)` 적용.
enrich 내부 실패는 조용히 원본 유지이므로 llm_error 플래그와 무관.

**변경 없음**: main.py(쿼터·타임아웃·스키마), quota, converters, 프론트(기존 💡 칸에 그대로 표시 —
멀티라인 제안은 `\n` 그대로 두고 프론트 blockquote/p 렌더 확인만).

## 4. 에러 처리

enrich의 모든 실패 → 기존 suggestion 유지, 사용자 고지 없음 (검사 결과 자체는 항상 유효,
기본 제안도 유효하므로 배너 불필요). 타임아웃은 기존 `llm_timeout_seconds`(60초) 예산 안에 포함 —
초과 시 기존 경로대로 llm_error 강등(룰 결과 반환)이며 enrich도 함께 소실되는 것이 자연스러운 동작.

## 5. 테스트 (전부 키 없이 mock)

- enrich 단위: 정상 JSON → suggestion 교체 / 깨진 JSON → 원본 유지 / 범위 밖 index 무시 /
  빈 findings·None client → 입력 그대로 / client 예외 → 원본 유지.
- run_lint 통합: FakeClient로 LLM 경로에서 enrich가 호출되는지, 룰-만 경로에선 호출 안 되는지.

## 6. 범위 밖 (다음 라운드 후보)

섹션별 강평 리포트(심사관 관점 코칭), CLI 반영, 제안 형식 사용자 선택.
