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
