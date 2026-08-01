"""서식이 다른 문서에 '필수 항목 누락'이 쏟아지는 문제 — 실파일 검증에서 발견.

PSST 서식이 아닌 문서(다른 공고 서식·제목 형식이 다른 문서)를 넣으면 섹션 매칭이 0이 되고
치명 결함 4개가 나온다. 실제로는 '이 서식이 아니다'라는 한 가지 사실이므로 안내 1건으로 모은다.
"""
from app.converters import normalize_pasted
from app.lint import run_lint

# PSST 표준 서식 (섹션 4개 모두 인식되는 문서)
PSST_DOC = """# 문제인식

도시 침수 피해가 늘고 있다.

# 실현가능성

예측 모델을 만든다.

# 성장전략

지자체로 확장한다.

# 팀 구성

대표는 방재 10년 경력이다."""

# 다른 서식 (프로파일 섹션이 하나도 없는 문서)
OTHER_FORM = """1. 프로그램 개요

IR 연계 프로그램을 운영한다.

2. 추진 일정

상반기에 착수한다."""


def _checkers(findings):
    return [f["checker"] for f in findings]


def test_non_psst_document_gets_one_notice_not_four_criticals():
    out = run_lint(normalize_pasted(OTHER_FORM).text)
    assert "missing-section" not in _checkers(out.findings), \
        "서식이 다른 문서에 필수 항목 누락이 그대로 남음"
    notices = [f for f in out.findings if f["checker"] == "profile-mismatch"]
    assert len(notices) == 1, f"안내는 1건이어야 한다: {_checkers(out.findings)}"
    assert notices[0]["severity"] == "info", "서식 불일치는 치명 결함이 아니다"
    assert notices[0]["suggestion"], "다음 행동 안내가 있어야 한다"


def test_psst_document_has_no_mismatch_notice():
    out = run_lint(normalize_pasted(PSST_DOC).text)
    assert "profile-mismatch" not in _checkers(out.findings)
    assert "missing-section" not in _checkers(out.findings)


def test_partial_match_keeps_real_missing_section():
    """일부 섹션이 인식되면 나머지 누락은 진짜 결함이므로 그대로 둔다."""
    partial = PSST_DOC.replace("# 팀 구성", "# 인력 계획")  # 프로파일에 없는 제목
    out = run_lint(normalize_pasted(partial).text)
    assert "missing-section" in _checkers(out.findings), "실제 누락이 사라짐"
    assert "profile-mismatch" not in _checkers(out.findings), "부분 매칭인데 서식 불일치로 처리됨"


def test_notice_names_the_expected_sections():
    out = run_lint(normalize_pasted(OTHER_FORM).text)
    notice = next(f for f in out.findings if f["checker"] == "profile-mismatch")
    for title in ("문제인식", "실현가능성", "성장전략", "팀 구성"):
        assert title in notice["message"], f"{title}이 안내에 없음"


def test_severity_order_preserved():
    """치명이 먼저, 안내가 나중 — 기존 정렬 규약을 깨지 않는다."""
    doc = OTHER_FORM + "\n\n총 사업비 5억원\n인건비 1억원\n장비비 1억원"
    out = run_lint(normalize_pasted(doc).text)
    order = [f["severity"] for f in out.findings]
    assert order == sorted(order, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s])
