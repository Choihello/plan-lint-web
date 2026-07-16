import io
import zipfile

import pytest

from app.converters import ConversionError
from app.converters.hwpx import convert_hwpx

_SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>1. 사업 개요</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>우리 서비스는 </hp:t></hp:run><hp:run><hp:t>이렇습니다.</hp:t></hp:run></hp:p>
  <hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>표 안 텍스트</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl>
</hs:sec>"""


def _hwpx(section_xml: str = _SECTION) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", section_xml)
    return buf.getvalue()


def test_extracts_paragraphs_and_promotes_headings():
    r = convert_hwpx(_hwpx())
    assert "## 1. 사업 개요" in r.text
    assert "우리 서비스는 이렇습니다." in r.text  # 같은 문단의 run은 이어붙임


def test_table_text_flattened_with_warning():
    r = convert_hwpx(_hwpx())
    assert "표 안 텍스트" in r.text
    assert any("표" in w for w in r.warnings)


_TABLE_SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>추진 일정</hp:t></hp:run></hp:p>
  <hp:tbl>
    <hp:tr>
      <hp:tc><hp:p><hp:run><hp:t>순번</hp:t></hp:run></hp:p></hp:tc>
      <hp:tc><hp:p><hp:run><hp:t>내용</hp:t></hp:run></hp:p></hp:tc>
      <hp:tc><hp:p><hp:run><hp:t>시기</hp:t></hp:run></hp:p></hp:tc>
    </hp:tr>
    <hp:tr>
      <hp:tc><hp:p><hp:run><hp:t>8</hp:t></hp:run></hp:p></hp:tc>
      <hp:tc><hp:p><hp:run><hp:t>R&amp;D 및 설계 수주</hp:t></hp:run></hp:p></hp:tc>
      <hp:tc><hp:p><hp:run><hp:t>2027년 상반기</hp:t></hp:run></hp:p></hp:tc>
    </hp:tr>
  </hp:tbl>
  <hp:p><hp:run><hp:t>마무리 문단</hp:t></hp:run></hp:p>
</hs:sec>"""


def test_table_rows_joined_horizontally():
    # 표는 셀 세로 나열이 아니라 행 단위 '셀 | 셀 | 셀'로 재구성돼야 한다
    r = convert_hwpx(_hwpx(_TABLE_SECTION))
    assert "순번 | 내용 | 시기" in r.text
    assert "8 | R&D 및 설계 수주 | 2027년 상반기" in r.text
    # 세로 나열(각 셀이 별도 문단)이 아니어야 함
    assert "순번\n\n내용" not in r.text


def test_table_rows_keep_document_position():
    r = convert_hwpx(_hwpx(_TABLE_SECTION))
    assert r.text.index("추진 일정") < r.text.index("순번 | 내용 | 시기") < r.text.index("마무리 문단")


def test_table_cell_text_not_duplicated():
    r = convert_hwpx(_hwpx(_TABLE_SECTION))
    assert r.text.count("R&D 및 설계 수주") == 1


def test_empty_hwpx_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", "<sec/>")
    with pytest.raises(ConversionError):
        convert_hwpx(buf.getvalue())


def test_section_files_sorted_numerically():
    """Regression test: section10.xml should come after section2.xml, not before."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        # Create 11 section files with numeric indices
        for i in range(11):
            section_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>섹션{i} 본문</hp:t></hp:run></hp:p>
</hs:sec>"""
            z.writestr(f"Contents/section{i}.xml", section_xml)

    r = convert_hwpx(buf.getvalue())
    # Verify that section2 appears before section10 in the output
    idx_2 = r.text.index("섹션2 본문")
    idx_10 = r.text.index("섹션10 본문")
    assert idx_2 < idx_10, f"섹션2({idx_2}) should come before 섹션10({idx_10})"
