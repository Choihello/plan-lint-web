import pymupdf
import pytest

from app.converters import ConversionError
from app.converters.pdf import convert_pdf


def _pdf_with_text() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "1. Business Overview")
    page.insert_text((72, 100), "Our service does X.")
    return doc.tobytes()


def _pdf_empty() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


def test_extracts_text_and_promotes_headings():
    r = convert_pdf(_pdf_with_text())
    assert "## 1. Business Overview" in r.text
    assert "Our service does X." in r.text


def test_scanned_pdf_rejected():
    with pytest.raises(ConversionError) as e:
        convert_pdf(_pdf_empty())
    assert "붙여넣기" in str(e.value)
