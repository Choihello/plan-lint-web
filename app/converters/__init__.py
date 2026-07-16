from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from .headings import promote_headings

_MAX_UNZIPPED = 50 * 1024 * 1024  # ZIP 폭탄 방어: 해제 합계 상한
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # 구형 .hwp (OLE2)
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF"

HWP_GUIDANCE = (
    "구형 한글(.hwp) 파일이에요. 한글에서 '다른 이름으로 저장 → HWPX 문서(*.hwpx)'로 "
    "저장한 뒤 다시 올려주세요. 어렵다면 본문을 복사해 '텍스트 붙여넣기' 탭을 이용해주세요."
)


@dataclass
class ConversionResult:
    text: str
    warnings: list[str] = field(default_factory=list)


class ConversionError(Exception):
    """str()이 그대로 사용자에게 보여줄 한국어 안내문."""


def check_zip_safety(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile as e:
        raise ConversionError("파일이 손상됐거나 형식이 올바르지 않아요. 텍스트 붙여넣기로 시도해주세요.") from e
    if total > _MAX_UNZIPPED:
        raise ConversionError("파일 내부 데이터가 너무 커요. 텍스트 붙여넣기로 시도해주세요.")


def normalize_pasted(text: str) -> ConversionResult:
    if any(line.lstrip().startswith("#") for line in text.splitlines()):
        return ConversionResult(text=text)  # 이미 마크다운이면 그대로
    promoted, warnings = promote_headings(text)
    return ConversionResult(text=promoted, warnings=warnings)


def convert(data: bytes, filename: str) -> ConversionResult:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "hwp" or data[:4] == _OLE_MAGIC:
        raise ConversionError(HWP_GUIDANCE)
    if ext == "hwpx":
        if not data.startswith(_ZIP_MAGIC):
            raise ConversionError("올바른 HWPX 파일이 아니에요. 텍스트 붙여넣기로 시도해주세요.")
        from .hwpx import convert_hwpx

        return convert_hwpx(data)
    if ext == "docx":
        if not data.startswith(_ZIP_MAGIC):
            raise ConversionError("올바른 DOCX 파일이 아니에요. 텍스트 붙여넣기로 시도해주세요.")
        from .docx import convert_docx

        return convert_docx(data)
    if ext == "pdf":
        if not data.startswith(_PDF_MAGIC):
            raise ConversionError("올바른 PDF 파일이 아니에요. 텍스트 붙여넣기로 시도해주세요.")
        from .pdf import convert_pdf

        return convert_pdf(data)
    raise ConversionError("지원하는 형식은 .hwpx / .pdf / .docx 예요. 다른 형식은 텍스트 붙여넣기를 이용해주세요.")
