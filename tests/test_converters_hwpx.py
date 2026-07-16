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


def test_empty_hwpx_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", "<sec/>")
    with pytest.raises(ConversionError):
        convert_hwpx(buf.getvalue())
