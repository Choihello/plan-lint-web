import io
import zipfile

import pytest

from app.converters import ConversionError, check_zip_safety, convert, normalize_pasted
from app.converters.headings import promote_headings


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_hwp_rejected_with_guidance():
    ole = b"\xd0\xcf\x11\xe0" + b"\x00" * 100  # 구형 .hwp = OLE 컨테이너
    with pytest.raises(ConversionError) as e:
        convert(ole, "plan.hwp")
    assert "hwpx" in str(e.value).lower()  # 재저장 안내 포함


def test_unknown_extension_rejected():
    with pytest.raises(ConversionError):
        convert(b"hello", "plan.xlsx")


def test_magic_mismatch_rejected():
    with pytest.raises(ConversionError):
        convert(b"not a zip at all", "plan.hwpx")


def test_zip_bomb_rejected():
    big = _zip_bytes({"a.xml": b"\x00" * (51 * 1024 * 1024)})
    with pytest.raises(ConversionError):
        check_zip_safety(big)


def test_normalize_pasted_promotes_headings():
    r = normalize_pasted("1. 사업 개요\n본문입니다.\n2) 시장 분석\n내용.")
    assert "## 1. 사업 개요" in r.text
    assert "## 2) 시장 분석" in r.text


def test_normalize_pasted_keeps_markdown():
    r = normalize_pasted("# 이미 마크다운\n본문")
    assert r.text.startswith("# 이미 마크다운")  # 헤딩 있으면 손대지 않음


def test_promote_headings_ignores_long_lines():
    text = "1. " + "가" * 60
    promoted, _ = promote_headings(text)
    assert "##" not in promoted


def test_promote_headings_korean_ordinal_markers_only():
    promoted, _ = promote_headings("가. 사업 개요\n본문.")
    assert "## 가. 사업 개요" in promoted


def test_promote_headings_ignores_ordinary_hangul_prose():
    promoted, _ = promote_headings("강. 여기서부터 본문입니다\n표. 아래 표를 참고하세요")
    assert "##" not in promoted


# 실제 예비창업패키지 서식(표 기반 한글 양식)의 변환본을 본뜬 픽스처 —
# 섹션 제목이 장식 문자·번호·영문 병기와 함께 평문 줄로 나온다.
PSST_FORM = """예비창업패키지 예비창업자 사업계획서
□ 일반현황
창업아이템명
AI 수재해 예측 플랫폼

□ 1. 문제인식 (Problem)
도시 침수 피해가 매년 늘고 있으나 예측 도구가 없다.

2. 실현가능성 (Solution)
재해영향평가 데이터로 침수 위험을 예측하는 모델을 만든다.

3. 성장전략 (Scale-up)
지자체 시범사업으로 시작해 민간 보험사로 확장한다.

4. 팀 구성 (Team)
대표는 방재 분야 10년 경력이다."""


def test_profile_titles_promoted_to_sections():
    r = normalize_pasted(PSST_FORM)
    for title in ("# 문제인식", "# 실현가능성", "# 성장전략", "# 팀 구성"):
        assert title in r.text, f"{title} 미승격"
    assert any("서식" in w for w in r.warnings)


def test_profile_title_promotion_resolves_missing_section():
    from app.lint import run_lint

    r = normalize_pasted(PSST_FORM)
    out = run_lint(r.text)
    missing = {f["message"] for f in out.findings if f["checker"] == "missing-section"}
    for title in ("문제인식", "실현가능성", "성장전략", "팀 구성"):
        assert not any(title in m for m in missing), f"'{title}'이 여전히 누락 판정: {missing}"


def test_profile_title_not_promoted_inside_prose():
    # 본문 문장 속에 섹션 단어가 있어도 줄 전체가 제목 꼴이 아니면 승격 금지
    r = normalize_pasted("이 문서는 문제인식이 부족하다는 평가를 받았다.\n다음 분기에 보완한다.")
    assert "#" not in r.text


def test_ole_file_with_docx_name_gets_docx_error_not_hwp_guidance():
    ole = b"\xd0\xcf\x11\xe0" + b"\x00" * 100
    with pytest.raises(ConversionError) as e:
        convert(ole, "plan.docx")
    assert "hwpx" not in str(e.value).lower()
    assert "docx" in str(e.value).lower()
