from app.lint import run_lint

TINY = "# 개요\n\n한 줄짜리 계획서."


class FakeClient:
    """complete가 '[]'를 돌려주면 LLM 체커가 결함 0건으로 동작한다."""

    def complete(self, system: str, user: str) -> str:
        return "[]"


class BrokenClient:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("api down")


def test_rules_only():
    out = run_lint(TINY)
    assert out.llm_ran is False and out.llm_error is False
    assert any(f["checker"] == "missing-section" for f in out.findings)
    assert all(isinstance(f, dict) for f in out.findings)


def test_llm_ok():
    out = run_lint(TINY, llm_client=FakeClient())
    assert out.llm_ran is True and out.llm_error is False
    # FakeClient는 결함 0건 — 룰 결함은 그대로 있어야 함
    assert any(f["checker"] == "missing-section" for f in out.findings)


class EnrichAwareClient:
    """체커 콜엔 결함 0건, 제안 심화 콜엔 index 0 교체를 돌려주는 fake."""

    def complete(self, system: str, user: str) -> str:
        if "빈칸" in system:  # enrich 프롬프트 식별
            return '[{"index": 0, "suggestion": "심화 제안 ○○"}]'
        return "[]"


def test_llm_path_enriches_suggestions():
    out = run_lint(TINY, llm_client=EnrichAwareClient())
    assert out.llm_ran is True
    assert out.findings[0]["suggestion"] == "심화 제안 ○○"


def test_llm_error_falls_back_to_rules():
    out = run_lint(TINY, llm_client=BrokenClient())
    assert out.llm_ran is False and out.llm_error is True
    assert any(f["checker"] == "missing-section" for f in out.findings)
