from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from planlint.cli import RULE_CHECKERS, build_llm_checkers
from planlint.core.engine import run_checks
from planlint.core.models import Document
from planlint.core.profile import Profile, load_profile

from .enrich import enrich_suggestions

logger = logging.getLogger("planlint.web")

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
        checkers = list(RULE_CHECKERS) + build_llm_checkers(llm_client)
        findings = run_checks(doc, profile, checkers, llm_available=True)
        dicts = [f.to_dict() for f in findings]
        # 컨설팅 모드: AI 검사가 성공한 경우에만 제안 심화 (+1콜, 실패 시 기존 제안 유지)
        dicts = enrich_suggestions(dicts, text, llm_client)
        return LintOutcome(findings=dicts, llm_ran=True)
    except Exception as e:
        # LLM 어느 단계가 죽어도 룰 결과는 반드시 돌려준다 (정직성 원칙)
        # 예외 타입명만 로깅 — 메시지/본문은 무저장 원칙상 기록하지 않는다.
        logger.warning("LLM 검사 실패: %s", type(e).__name__)
        return LintOutcome(findings=_rules_only(doc, profile), llm_error=True)
