from __future__ import annotations

import io
import re

import docx as docx_lib
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import ConversionError, ConversionResult, check_zip_safety
from .headings import structure_headings

_HEADING_STYLE = re.compile(r"^(?:Heading|제목)\s*(\d)", re.IGNORECASE)


def _iter_blocks(d):
    """본문의 문단·표를 문서 순서 그대로 순회 — 표를 끝에 몰면 섹션 귀속이 깨진다."""
    for child in d.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, d)
        elif child.tag.endswith("}tbl"):
            yield Table(child, d)


def convert_docx(data: bytes) -> ConversionResult:
    check_zip_safety(data)
    try:
        d = docx_lib.Document(io.BytesIO(data))
    except Exception as e:
        raise ConversionError("DOCX 파일을 읽지 못했어요. 텍스트 붙여넣기로 시도해주세요.") from e

    lines: list[str] = []
    has_heading = False
    table_flattened = False
    for block in _iter_blocks(d):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            m = _HEADING_STYLE.match(block.style.name or "")
            if m:
                has_heading = True
                lines.append("#" * min(int(m.group(1)), 6) + " " + text)
            else:
                lines.append(text)
        else:  # Table — 행 단위 '셀 | 셀'로, 빈 셀은 자리 유지
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
                    table_flattened = True

    warnings: list[str] = []
    if table_flattened:
        warnings.append("표가 텍스트로 평탄화됐어요 — 표 안 수치 검사는 부정확할 수 있어요")

    if not lines:
        raise ConversionError("파일에서 텍스트를 찾지 못했어요. 텍스트 붙여넣기로 시도해주세요.")

    text = "\n\n".join(lines)
    if not has_heading:
        text, extra = structure_headings(text)
        warnings.extend(extra)
    return ConversionResult(text=text, warnings=warnings)
