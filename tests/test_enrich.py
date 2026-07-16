import json

from app.enrich import enrich_suggestions

FINDINGS = [
    {"checker": "unsupported-claim", "severity": "warning", "message": "출처 없음",
     "section": "시장 분석", "quotes": ["600억 시장"], "suggestion": "출처를 붙이세요.", "next_action": None},
    {"checker": "vague-goal", "severity": "warning", "message": "정량 지표 없음",
     "section": "성장전략", "quotes": ["빠르게 성장"], "suggestion": None, "next_action": None},
]


class GoodClient:
    def complete(self, system: str, user: str) -> str:
        return json.dumps([
            {"index": 0, "suggestion": "출처를 붙이세요. 예: ○○보고서(기관명, 연도) 기준 ○○억원"},
            {"index": 1, "suggestion": "정량 목표로 바꾸세요. 예: ○○년까지 고객 ○○명"},
        ], ensure_ascii=False)


class BrokenJsonClient:
    def complete(self, system: str, user: str) -> str:
        return "제안은 다음과 같습니다: 열심히 하세요"


class OutOfRangeClient:
    def complete(self, system: str, user: str) -> str:
        return json.dumps([{"index": 99, "suggestion": "엉뚱한 제안"}])


class ExplodingClient:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("api down")


def _copy():
    return [dict(f) for f in FINDINGS]


def test_replaces_suggestions_by_index():
    out = enrich_suggestions(_copy(), "원문", GoodClient())
    assert "○○보고서" in out[0]["suggestion"]
    assert "○○명" in out[1]["suggestion"]


def test_broken_json_keeps_originals():
    out = enrich_suggestions(_copy(), "원문", BrokenJsonClient())
    assert out[0]["suggestion"] == "출처를 붙이세요."
    assert out[1]["suggestion"] is None


def test_out_of_range_index_ignored():
    out = enrich_suggestions(_copy(), "원문", OutOfRangeClient())
    assert out[0]["suggestion"] == "출처를 붙이세요."


def test_client_exception_keeps_originals():
    out = enrich_suggestions(_copy(), "원문", ExplodingClient())
    assert out[0]["suggestion"] == "출처를 붙이세요."


def test_empty_findings_or_no_client_passthrough():
    assert enrich_suggestions([], "원문", GoodClient()) == []
    same = _copy()
    assert enrich_suggestions(same, "원문", None) is same
