from __future__ import annotations

import re
from functools import lru_cache

from planlint.core.profile import load_profile

# "1. 제목" / "2) 제목" / "IV. 제목" / "가. 제목" 꼴의 짧은 줄만 제목으로 승격 (한글 기호는 14종 순서 마커만)
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}|[IVXivx]{1,4}|[가나다라마바사아자차카타파하])[.)]\s+\S.{0,38}$")

# 실제 서식의 제목 줄 장식: 앞머리 불릿·체크박스·번호 ("□ 1. 문제인식 (Problem)" 등)
_PROFILE_NAME = "psst-standard"
_DECOR = re.compile(r"^[\s□■◇◆○●◦·•*\-]*(?:\d{1,2}\s*[.)]\s*)?")
_PAREN = re.compile(r"\([^)]*\)")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    # 엔진 matching._norm과 같은 규칙(공백 제거 + 소문자)이어야 헤딩이 프로파일에 매칭된다
    return _WS.sub("", s).lower()


@lru_cache(maxsize=1)
def _profile_labels() -> dict[str, str]:
    """정규화 라벨 → 프로파일 정식 제목 (top-level 섹션만 — 엔진 annotate_sections와 동일 범위)."""
    profile = load_profile(_PROFILE_NAME)
    labels: dict[str, str] = {}
    for spec in profile.sections:
        for label in (spec.title, *spec.aliases):
            labels[_norm(label)] = spec.title
    return labels


def _match_profile_title(line: str) -> str | None:
    """줄 전체가 서식 섹션 제목 꼴이면 프로파일 정식 제목을 돌려준다."""
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return None
    cleaned = _DECOR.sub("", stripped, count=1)
    cleaned = _PAREN.sub("", cleaned).strip(" :：-·")
    if not cleaned:
        return None
    return _profile_labels().get(_norm(cleaned))


def structure_headings(text: str) -> tuple[str, list[str]]:
    """서식 섹션 제목(프로파일 대조)은 '# '로, 번호 매긴 짧은 줄은 '## '로 승격한다.

    프로파일 제목은 정식 명칭으로 치환해 승격한다 — 엔진의 섹션 매칭이 정규화
    후 '정확히 일치'만 보므로, 장식('□ 1.', '(Problem)')이 남으면 매칭이 깨진다.
    """
    lines = text.splitlines()
    out: list[str] = []
    profile_hits, numbered = 0, 0
    for line in lines:
        canonical = _match_profile_title(line)
        if canonical:
            out.append("# " + canonical)
            profile_hits += 1
        elif _NUMBERED.match(line):
            out.append("## " + line.strip())
            numbered += 1
        else:
            out.append(line)
    warnings: list[str] = []
    if profile_hits:
        warnings.append(f"서식 섹션 제목 {profile_hits}개를 자동 인식했어요")
    if numbered:
        warnings.append(f"번호 매긴 줄 {numbered}개를 제목으로 자동 인식했어요 (휴리스틱)")
    return "\n".join(out), warnings


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
