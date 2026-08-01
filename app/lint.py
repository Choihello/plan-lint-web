from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from planlint.cli import RULE_CHECKERS, build_llm_checkers
from planlint.core.engine import run_checks
from planlint.core.models import Document
from planlint.core.profile import Profile, load_profile

from .enrich import enrich_suggestions
from .llm_budget import BudgetPool, plan_shares

logger = logging.getLogger("planlint.web")

# 문서 전체를 한 번만 보는 검사 — 섹션이 많아도 호출 수가 늘지 않으므로 몫을 먼저 확보한다.
# (확보하지 않으면 섹션 수만큼 호출하는 검사가 예산을 독식해 아예 실행되지 않는다)
_FIXED_SHARES = {"logic-gap": 2, "internal-contradiction": 1, "enrich": 1}

PROFILE_NAME = "psst-standard"  # v1 고정 — 공고 선택 UI는 v2


@lru_cache(maxsize=1)
def _profile() -> Profile:
    return load_profile(PROFILE_NAME)


@dataclass
class LintOutcome:
    findings: list[dict] = field(default_factory=list)
    llm_ran: bool = False
    llm_error: bool = False


def _rules_only(doc: Document, profile: Profile) -> list[dict]:
    findings = run_checks(doc, profile, list(RULE_CHECKERS), llm_available=False)
    return [f.to_dict() for f in findings]


def run_lint(text: str, llm_client=None) -> LintOutcome:
    doc = Document.from_markdown(text)
    profile = _profile()
    if llm_client is None:
        return LintOutcome(findings=_rules_only(doc, profile))
    try:
        llm_checkers = build_llm_checkers(llm_client)
        enrich_client = llm_client
        if isinstance(llm_client, BudgetPool):
            # 체커별로 예산 몫을 배정한다. 총량만 막으면 먼저 도는 체커가 독식해
            # 뒤쪽 검사(내부 모순)와 보강 제안이 아예 실행되지 않는다(실측).
            per_section = [c.name for c in llm_checkers if c.name not in _FIXED_SHARES]
            shares = plan_shares(llm_client._total, per_section, _FIXED_SHARES)
            for checker in llm_checkers:
                # LLMChecker의 공개 속성 — 생성 후 클라이언트를 몫으로 교체한다
                checker.client = llm_client.share(checker.name, shares.get(checker.name, 0))
            enrich_client = llm_client.share("enrich", shares.get("enrich", 0))

        checkers = list(RULE_CHECKERS) + llm_checkers
        findings = run_checks(doc, profile, checkers, llm_available=True)
        dicts = [f.to_dict() for f in findings]
        # 컨설팅 모드: AI 검사가 성공한 경우에만 제안 심화 (+1콜, 실패 시 기존 제안 유지)
        dicts = enrich_suggestions(dicts, text, enrich_client)
        return LintOutcome(findings=dicts, llm_ran=True)
    except Exception as e:
        # LLM 어느 단계가 죽어도 룰 결과는 반드시 돌려준다 (정직성 원칙)
        # 예외 타입명만 로깅 — 메시지/본문은 무저장 원칙상 기록하지 않는다.
        logger.warning("LLM 검사 실패: %s", type(e).__name__)
        return LintOutcome(findings=_rules_only(doc, profile), llm_error=True)
