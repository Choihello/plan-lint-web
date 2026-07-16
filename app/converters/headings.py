from __future__ import annotations

import re

# "1. 제목" / "2) 제목" / "IV. 제목" / "가. 제목" 꼴의 짧은 줄만 제목으로 승격 (한글 기호는 14종 순서 마커만)
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}|[IVXivx]{1,4}|[가나다라마바사아자차카타파하])[.)]\s+\S.{0,38}$")


def promote_headings(text: str) -> tuple[str, list[str]]:
    """마크다운 헤딩이 없는 텍스트에서 번호 매긴 짧은 줄을 '## '로 승격한다."""
    lines = text.splitlines()
    out, promoted = [], 0
    for line in lines:
        if _NUMBERED.match(line):
            out.append("## " + line.strip())
            promoted += 1
        else:
            out.append(line)
    warnings = []
    if promoted:
        warnings.append(f"번호 매긴 줄 {promoted}개를 제목으로 자동 인식했어요 (휴리스틱)")
    return "\n".join(out), warnings
