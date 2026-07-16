from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from . import ConversionError, ConversionResult, check_zip_safety
from .headings import structure_headings


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _section_index(name: str) -> int:
    stem = name.rsplit("section", 1)[-1].rsplit(".", 1)[0]
    try:
        return int(stem)
    except ValueError:
        return 0


def convert_hwpx(data: bytes) -> ConversionResult:
    check_zip_safety(data)
    paragraphs: list[str] = []
    has_table = False
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sections = sorted(
            (n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml")),
            key=_section_index,
        )
        for name in sections:
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError as e:
                raise ConversionError("HWPX 내부 구조를 읽지 못했어요. 텍스트 붙여넣기로 시도해주세요.") from e
            for elem in root.iter():
                if _localname(elem.tag) == "tbl":
                    has_table = True
                if _localname(elem.tag) != "p":
                    continue
                # 직접 자식 run의 직접 자식 t만 수집 — run 안에 중첩된 표(tbl)의
                # 텍스트는 제외해서, 표 안 문단(p)이 root.iter()에서 별도로
                # 한 번만 수집되게 한다 (중복·내용 훼손 방지)
                runs = [
                    t.text
                    for run in elem
                    if _localname(run.tag) == "run"
                    for t in run
                    if _localname(t.tag) == "t" and t.text
                ]
                text = "".join(runs).strip()
                if text:
                    paragraphs.append(text)
    if not paragraphs:
        raise ConversionError("파일에서 텍스트를 찾지 못했어요. 텍스트 붙여넣기로 시도해주세요.")
    text, warnings = structure_headings("\n\n".join(paragraphs))
    if has_table:
        warnings.append("표가 텍스트로 평탄화됐어요 — 표 안 수치 검사는 부정확할 수 있어요")
    return ConversionResult(text=text, warnings=warnings)
