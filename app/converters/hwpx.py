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
            consumed: set[int] = set()  # 표 처리에서 소비한 하위 요소(p·중첩 tbl) id
            for elem in root.iter():
                ln = _localname(elem.tag)
                if ln == "tbl":
                    if id(elem) in consumed:
                        continue  # 중첩 표는 바깥 표 처리에서 이미 셀 텍스트로 흡수됨
                    has_table = True
                    # 표는 행 단위 '셀 | 셀 | 셀'로 재구성 — 셀을 세로로 나열하면
                    # 행의 맥락(항목-내용-시기 대응)이 깨져 검사 품질이 떨어진다
                    for sub in elem.iter():
                        consumed.add(id(sub))
                    # 직접 자식 tr/tc만 행·셀로 취급 — 셀 안에 중첩된 표의 텍스트는
                    # tc.iter()를 통해 바깥 셀 내용으로 흡수된다 (별도 행 중복 방지)
                    for tr in (e for e in elem if _localname(e.tag) == "tr"):
                        cells = []
                        for tc in (c for c in tr if _localname(c.tag) == "tc"):
                            texts = [t.text for t in tc.iter() if _localname(t.tag) == "t" and t.text]
                            cells.append("".join(texts).strip())
                        if any(cells):
                            paragraphs.append(" | ".join(cells))
                    continue
                if ln != "p" or id(elem) in consumed:
                    continue
                # 직접 자식 run의 직접 자식 t만 수집 — run 안에 중첩된 표(tbl)의
                # 텍스트는 제외 (표 텍스트는 위의 행 단위 처리에서만 나온다)
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
