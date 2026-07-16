from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("planlint.web")

_SOURCE_CAP = 20_000  # 결함 인용은 목록에 이미 있으므로 원문은 앞부분만으로 충분

_SYSTEM = (
    "너는 정부 창업지원사업 사업계획서의 결함을 어떻게 보강할지 알려주는 조언자다. "
    "각 결함에 대해 '무엇을 어떻게 채워야 하는지'를 한두 문장으로 구체적으로 제안하라. "
    "채워야 할 값 자체는 ○○ 빈칸으로 남겨라 — 예: \"○○보고서(기관명, 연도) 기준 ○○억원\". "
    "지원자의 문장을 대신 완성해 주지 마라(대필 금지). 점수 평가나 합격 예측을 하지 마라. "
    '출력은 JSON 배열만: [{"index": <결함 번호>, "suggestion": "..."}]. 다른 텍스트를 붙이지 마라.'
)

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _build_user_prompt(findings: list[dict], source: str) -> str:
    items = []
    for i, f in enumerate(findings):
        quotes = " / ".join(f.get("quotes") or [])
        items.append(
            f"[{i}] 유형: {f.get('checker')} · 섹션: {f.get('section') or '전체'}\n"
            f"    지적: {f.get('message')}\n"
            f"    인용: {quotes or '(없음)'}\n"
            f"    기존 제안: {f.get('suggestion') or '(없음)'}"
        )
    return (
        "다음은 한 사업계획서에서 발견된 결함 목록이다. 각 결함의 보강 방법을 제안하라.\n\n"
        + "\n".join(items)
        + "\n\n[원문]\n"
        + source[:_SOURCE_CAP]
    )


def enrich_suggestions(findings: list[dict], source: str, llm_client) -> list[dict]:
    """결함별 suggestion을 '방법 + 빈칸 템플릿' 심화 제안으로 교체한다.

    어떤 실패(콜 예외·JSON 깨짐·index 불일치)에도 예외를 전파하지 않고
    기존 suggestion을 유지한다 — 검사 결과 자체는 항상 유효해야 한다.
    """
    if not findings or llm_client is None:
        return findings
    try:
        raw = llm_client.complete(_SYSTEM, _build_user_prompt(findings, source))
        m = _JSON_ARRAY.search(raw)
        items = json.loads(m.group(0) if m else raw)
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            suggestion = str(item.get("suggestion", "")).strip()
            if isinstance(idx, int) and 0 <= idx < len(findings) and suggestion:
                findings[idx]["suggestion"] = suggestion
    except Exception as e:
        # 제안 심화는 부가 기능 — 실패해도 기본 제안으로 충분하다 (본문 미로깅)
        logger.warning("제안 심화 실패: %s", type(e).__name__)
    return findings
