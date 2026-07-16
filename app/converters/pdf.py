from __future__ import annotations

import pymupdf

from . import ConversionError, ConversionResult
from .headings import promote_headings


def convert_pdf(data: bytes) -> ConversionResult:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ConversionError("PDF 파일을 읽지 못했어요. 텍스트 붙여넣기로 시도해주세요.") from e
    try:
        pages = [page.get_text().strip() for page in doc]
    except Exception as e:
        raise ConversionError("PDF 내용을 읽는 중 문제가 생겼어요. 텍스트 붙여넣기로 시도해주세요.") from e
    finally:
        doc.close()
    body = "\n\n".join(p for p in pages if p)
    if not body.strip():
        raise ConversionError(
            "PDF에서 텍스트를 찾지 못했어요 (스캔본일 수 있어요). 본문을 복사해 텍스트 붙여넣기로 시도해주세요."
        )
    text, warnings = promote_headings(body)
    warnings.append("PDF 레이아웃에 따라 줄바꿈·표가 부정확할 수 있어요")
    return ConversionResult(text=text, warnings=warnings)
