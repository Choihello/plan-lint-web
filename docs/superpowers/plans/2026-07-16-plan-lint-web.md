# plan-lint-web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비개발 예비창업자가 파일 업로드/붙여넣기만으로 사업계획서 결함 진단을 받는 공개 운영 웹 서비스 (스펙: `docs/superpowers/specs/2026-07-16-plan-lint-web-design.md`).

**Architecture:** 단일 FastAPI 앱이 전부 담당 — `planlint` 엔진 직접 import, 파일 변환(converters), LLM 횟수제한(quota, SQLite), 정적 프론트 서빙. 컨테이너 1개로 배포. 문서 본문은 어디에도 저장하지 않음.

**Tech Stack:** Python 3.12 · FastAPI · uvicorn · pymupdf(PDF) · python-docx(DOCX) · stdlib zipfile+ElementTree(HWPX) · SQLite(쿼터만) · 바닐라 HTML/CSS/JS 프론트(빌드 도구 없음) · pytest

## Global Constraints

- 저장소 루트: `C:/Users/zerat/OneDrive/바탕 화면/Teddy/plan-lint-web` (git 초기화 완료, main 브랜치). 모든 경로는 이 루트 기준.
- 엔진은 `planlint @ git+https://github.com/Choihello/plan-lint@main`으로 설치. **엔진 코드 복사·수정 금지.**
- 프로파일은 `psst-standard` 고정 (v1).
- 쿼터 기본값: **IP당 하루 1회, 전역 하루 50회** (LLM 검사만; 룰 검사는 무제한).
- 크기 상한: 파일 5MB, 변환 후 텍스트 100,000자. LLM 타임아웃 60초, 동시 LLM 세마포어 3.
- 위 수치는 전부 `PLW_*` 환경변수로 조정 가능 (Task 1의 `Settings` 참조).
- **완전 무저장**: 업로드 파일은 메모리에서만 처리(디스크 임시파일 금지), 응답 후 참조 소멸. 로그에 본문·파일명 기록 금지. SQLite에는 IP 해시·날짜·카운트만.
- 모든 사용자 대상 문구는 한국어, "친숙한 진단 리포트" 톤. 점수·합격예측 표시 금지 (CLI 원칙).
- 모든 테스트는 API 키 없이 통과해야 함 (LLM은 mock).
- 각 태스크 완료 시 커밋. 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**엔진 공개 API (검증 완료 — 이 시그니처가 진실):**

```python
from planlint.cli import RULE_CHECKERS, build_llm_checkers   # 인스턴스 리스트 / (client) -> list
from planlint.core.engine import run_checks                  # (doc, profile, checkers, *, llm_available) -> list[Finding]
from planlint.core.models import Document                    # Document.from_markdown(text)
from planlint.core.profile import load_profile               # ("psst-standard") -> Profile
from planlint.llm.client import LLMUnavailable, make_client  # (provider="auto", model=None) -> LLMClient
# Finding.to_dict() -> {"checker","severity","message","section","quotes","suggestion","next_action"}
# LLMClient.complete(system: str, user: str) -> str — "[]"를 반환하는 fake면 LLM 무결함 mock이 됨
```

---

### Task 1: 프로젝트 스캐폴드 + 설정 모듈

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `app/__init__.py`, `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (frozen dataclass — 필드는 아래 코드 그대로), `app.config.load_settings() -> Settings`

- [ ] **Step 1: 의존성·gitignore 작성**

`requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
python-multipart>=0.0.9
pymupdf>=1.24
python-docx>=1.1
planlint @ git+https://github.com/Choihello/plan-lint@main
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
httpx>=0.27
```

`.gitignore`:
```
__pycache__/
*.sqlite3
.pytest_cache/
.venv/
```

`app/__init__.py`: 빈 파일.

- [ ] **Step 2: 설치 확인**

Run: `python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt` (bash 기준 `.venv/Scripts/`, 이후 모든 명령은 이 venv 사용)
Expected: planlint 포함 전부 설치 성공. `python -c "from planlint.cli import RULE_CHECKERS; print(len(RULE_CHECKERS))"` → `3`

- [ ] **Step 3: 실패하는 테스트 작성** — `tests/test_config.py`

```python
from app.config import load_settings


def test_defaults():
    s = load_settings()
    assert s.per_ip_daily == 1
    assert s.global_daily == 50
    assert s.max_file_bytes == 5 * 1024 * 1024
    assert s.max_text_chars == 100_000
    assert s.llm_timeout_seconds == 60
    assert s.llm_concurrency == 3


def test_env_override(monkeypatch):
    monkeypatch.setenv("PLW_PER_IP_DAILY", "5")
    monkeypatch.setenv("PLW_GLOBAL_DAILY", "999")
    s = load_settings()
    assert s.per_ip_daily == 5
    assert s.global_daily == 999
```

- [ ] **Step 4: 실패 확인**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 5: 구현** — `app/config.py`

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    per_ip_daily: int
    global_daily: int
    max_file_bytes: int
    max_text_chars: int
    llm_timeout_seconds: int
    llm_concurrency: int
    quota_db_path: str
    quota_salt: str


def load_settings() -> Settings:
    return Settings(
        per_ip_daily=int(os.environ.get("PLW_PER_IP_DAILY", "1")),
        global_daily=int(os.environ.get("PLW_GLOBAL_DAILY", "50")),
        max_file_bytes=int(os.environ.get("PLW_MAX_FILE_BYTES", str(5 * 1024 * 1024))),
        max_text_chars=int(os.environ.get("PLW_MAX_TEXT_CHARS", "100000")),
        llm_timeout_seconds=int(os.environ.get("PLW_LLM_TIMEOUT", "60")),
        llm_concurrency=int(os.environ.get("PLW_LLM_CONCURRENCY", "3")),
        quota_db_path=os.environ.get("PLW_QUOTA_DB", "quota.sqlite3"),
        quota_salt=os.environ.get("PLW_QUOTA_SALT", "plan-lint-web-v1"),
    )
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 7: 커밋**

```bash
git add requirements.txt requirements-dev.txt .gitignore app/ tests/
git commit -m "feat: 프로젝트 스캐폴드 + 환경변수 설정 모듈"
```

---

### Task 2: lint 어댑터 — planlint 엔진 호출

**Files:**
- Create: `app/lint.py`
- Test: `tests/test_lint.py`

**Interfaces:**
- Consumes: 엔진 공개 API (Global Constraints 참조)
- Produces: `app.lint.LintOutcome` (`findings: list[dict]`, `llm_ran: bool`, `llm_error: bool`), `app.lint.run_lint(text: str, llm_client=None) -> LintOutcome`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_lint.py`

```python
from app.lint import run_lint

TINY = "# 개요\n\n한 줄짜리 계획서."


class FakeClient:
    """complete가 '[]'를 돌려주면 LLM 체커가 결함 0건으로 동작한다."""

    def complete(self, system: str, user: str) -> str:
        return "[]"


class BrokenClient:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("api down")


def test_rules_only():
    out = run_lint(TINY)
    assert out.llm_ran is False and out.llm_error is False
    assert any(f["checker"] == "missing-section" for f in out.findings)
    assert all(isinstance(f, dict) for f in out.findings)


def test_llm_ok():
    out = run_lint(TINY, llm_client=FakeClient())
    assert out.llm_ran is True and out.llm_error is False
    # FakeClient는 결함 0건 — 룰 결함은 그대로 있어야 함
    assert any(f["checker"] == "missing-section" for f in out.findings)


def test_llm_error_falls_back_to_rules():
    out = run_lint(TINY, llm_client=BrokenClient())
    assert out.llm_ran is False and out.llm_error is True
    assert any(f["checker"] == "missing-section" for f in out.findings)
```

참고: `missing-section` 체커 이름은 `planlint/checkers/rule_missing_section.py`의 `name` 속성에서 확인. 다르면 테스트를 실제 이름에 맞출 것 (스펙 기준 룰 체커: missing-section / length-violation / numeric-consistency).

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.lint'`

- [ ] **Step 3: 구현** — `app/lint.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from planlint.cli import RULE_CHECKERS, build_llm_checkers
from planlint.core.engine import run_checks
from planlint.core.models import Document
from planlint.core.profile import Profile, load_profile

PROFILE_NAME = "psst-standard"  # v1 고정 — 공고 선택 UI는 v2


@lru_cache(maxsize=1)
def _profile() -> Profile:
    return load_profile(PROFILE_NAME)


@dataclass
class LintOutcome:
    findings: list[dict] = field(default_factory=list)
    llm_ran: bool = False
    llm_error: bool = False


def _rules_only(doc: Document, profile: Profile) -> list[dict]:
    findings = run_checks(doc, profile, list(RULE_CHECKERS), llm_available=False)
    return [f.to_dict() for f in findings]


def run_lint(text: str, llm_client=None) -> LintOutcome:
    doc = Document.from_markdown(text)
    profile = _profile()
    if llm_client is None:
        return LintOutcome(findings=_rules_only(doc, profile))
    try:
        checkers = list(RULE_CHECKERS) + build_llm_checkers(llm_client)
        findings = run_checks(doc, profile, checkers, llm_available=True)
        return LintOutcome(findings=[f.to_dict() for f in findings], llm_ran=True)
    except Exception:
        # LLM 어느 단계가 죽어도 룰 결과는 반드시 돌려준다 (정직성 원칙)
        return LintOutcome(findings=_rules_only(doc, profile), llm_error=True)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_lint.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add app/lint.py tests/test_lint.py
git commit -m "feat: planlint 엔진 호출 어댑터 — LLM 실패 시 룰 폴백"
```

---

### Task 3: 쿼터 — IP별·전역 LLM 횟수제한

**Files:**
- Create: `app/quota.py`
- Test: `tests/test_quota.py`

**Interfaces:**
- Produces: `app.quota.Quota(db_path, per_ip, global_cap, salt)` — 메서드 `try_consume(ip) -> str | None` (None=성공, `"quota_ip"` | `"quota_global"`), `refund(ip) -> None`, `remaining(ip) -> int`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_quota.py`

```python
from app.quota import Quota


def make(tmp_path, per_ip=1, global_cap=50):
    return Quota(str(tmp_path / "q.sqlite3"), per_ip, global_cap, "salt")


def test_per_ip_limit(tmp_path):
    q = make(tmp_path)
    assert q.try_consume("1.1.1.1") is None
    assert q.try_consume("1.1.1.1") == "quota_ip"
    assert q.try_consume("2.2.2.2") is None  # 다른 IP는 영향 없음


def test_global_cap(tmp_path):
    q = make(tmp_path, per_ip=10, global_cap=2)
    assert q.try_consume("1.1.1.1") is None
    assert q.try_consume("2.2.2.2") is None
    assert q.try_consume("3.3.3.3") == "quota_global"


def test_refund(tmp_path):
    q = make(tmp_path)
    q.try_consume("1.1.1.1")
    q.refund("1.1.1.1")
    assert q.try_consume("1.1.1.1") is None


def test_remaining(tmp_path):
    q = make(tmp_path, per_ip=1, global_cap=50)
    assert q.remaining("1.1.1.1") == 1
    q.try_consume("1.1.1.1")
    assert q.remaining("1.1.1.1") == 0


def test_no_raw_ip_stored(tmp_path):
    import sqlite3

    q = make(tmp_path)
    q.try_consume("203.0.113.7")
    rows = sqlite3.connect(str(tmp_path / "q.sqlite3")).execute("SELECT ip_hash FROM usage").fetchall()
    assert rows and all("203.0.113.7" not in r[0] for r in rows)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_quota.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.quota'`

- [ ] **Step 3: 구현** — `app/quota.py`

```python
from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import threading


class Quota:
    """LLM 검사 횟수제한. SQLite에 (ip_hash, date, count)만 저장 — 원본 IP 저장 금지."""

    def __init__(self, db_path: str, per_ip: int, global_cap: int, salt: str):
        self._per_ip = per_ip
        self._global = global_cap
        self._salt = salt
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS usage ("
            "ip_hash TEXT NOT NULL, date TEXT NOT NULL, count INTEGER NOT NULL, "
            "PRIMARY KEY (ip_hash, date))"
        )
        self._conn.commit()

    def _hash(self, ip: str) -> str:
        return hashlib.sha256(f"{self._salt}:{ip}".encode()).hexdigest()

    @staticmethod
    def _today() -> str:
        return dt.date.today().isoformat()

    def _counts(self, ip: str) -> tuple[int, int]:
        """(이 IP의 오늘 사용량, 전역 오늘 사용량). 지난 날짜 행은 겸사겸사 청소."""
        today = self._today()
        self._conn.execute("DELETE FROM usage WHERE date != ?", (today,))
        total = self._conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM usage WHERE date = ?", (today,)
        ).fetchone()[0]
        row = self._conn.execute(
            "SELECT count FROM usage WHERE ip_hash = ? AND date = ?", (self._hash(ip), today)
        ).fetchone()
        return (row[0] if row else 0, total)

    def try_consume(self, ip: str) -> str | None:
        with self._lock:
            used, total = self._counts(ip)
            if total >= self._global:
                return "quota_global"
            if used >= self._per_ip:
                return "quota_ip"
            self._conn.execute(
                "INSERT INTO usage (ip_hash, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(ip_hash, date) DO UPDATE SET count = count + 1",
                (self._hash(ip), self._today()),
            )
            self._conn.commit()
            return None

    def refund(self, ip: str) -> None:
        """LLM 호출이 실패했을 때 소비한 횟수를 돌려준다."""
        with self._lock:
            self._conn.execute(
                "UPDATE usage SET count = MAX(count - 1, 0) WHERE ip_hash = ? AND date = ?",
                (self._hash(ip), self._today()),
            )
            self._conn.commit()

    def remaining(self, ip: str) -> int:
        with self._lock:
            used, total = self._counts(ip)
            return max(0, min(self._per_ip - used, self._global - total))
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_quota.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add app/quota.py tests/test_quota.py
git commit -m "feat: IP 해시 기반 LLM 쿼터 (IP당 1회·전역 50회, refund 지원)"
```

---

### Task 4: 변환기 골격 — 디스패처 · 붙여넣기 · 제목 휴리스틱 · ZIP 방어

**Files:**
- Create: `app/converters/__init__.py`, `app/converters/headings.py`
- Test: `tests/test_converters_base.py`

**Interfaces:**
- Produces:
  - `app.converters.ConversionResult` (`text: str`, `warnings: list[str]`)
  - `app.converters.ConversionError(Exception)` — str()이 사용자 안내문
  - `app.converters.convert(data: bytes, filename: str) -> ConversionResult` — 확장자+매직바이트로 hwpx/docx/pdf 디스패치
  - `app.converters.normalize_pasted(text: str) -> ConversionResult` — 붙여넣기 경로
  - `app.converters.check_zip_safety(data: bytes) -> None` — ZIP 폭탄 방어 (해제 합계 50MB 초과 시 ConversionError). Task 5·6에서 사용
  - `app.converters.headings.promote_headings(text: str) -> tuple[str, list[str]]` — "1. 제목" 꼴 줄을 `## `로 승격. Task 5·6·7에서 사용

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_converters_base.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_converters_base.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `app/converters/headings.py`

```python
from __future__ import annotations

import re

# "1. 제목" / "2) 제목" / "IV. 제목" / "가. 제목" 꼴의 짧은 줄만 제목으로 승격
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}|[IVXivx]{1,4}|[가-하])[.)]\s+\S.{0,38}$")


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
```

- [ ] **Step 4: 구현** — `app/converters/__init__.py`

```python
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
```

참고: `.hwpx`/`.docx` 분기는 Task 5·6에서 모듈이 생기기 전까지 import 에러가 난다 — 이 태스크의 테스트는 그 분기에 도달하지 않으므로 무방.

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/test_converters_base.py -v`
Expected: 7 passed

- [ ] **Step 6: 커밋**

```bash
git add app/converters/ tests/test_converters_base.py
git commit -m "feat: 변환 디스패처 — 매직바이트 검증·ZIP 폭탄 방어·제목 승격 휴리스틱"
```

---

### Task 5: HWPX 변환기

**Files:**
- Create: `app/converters/hwpx.py`
- Test: `tests/test_converters_hwpx.py`

**Interfaces:**
- Consumes: `check_zip_safety`, `promote_headings`, `ConversionResult`, `ConversionError` (Task 4)
- Produces: `app.converters.hwpx.convert_hwpx(data: bytes) -> ConversionResult`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_converters_hwpx.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_converters_hwpx.py -v`
Expected: FAIL — `No module named 'app.converters.hwpx'`

- [ ] **Step 3: 구현** — `app/converters/hwpx.py`

```python
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from . import ConversionError, ConversionResult, check_zip_safety
from .headings import promote_headings


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def convert_hwpx(data: bytes) -> ConversionResult:
    check_zip_safety(data)
    paragraphs: list[str] = []
    has_table = False
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sections = sorted(n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml"))
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
    text, warnings = promote_headings("\n\n".join(paragraphs))
    if has_table:
        warnings.append("표가 텍스트로 평탄화됐어요 — 표 안 수치 검사는 부정확할 수 있어요")
    return ConversionResult(text=text, warnings=warnings)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_converters_hwpx.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add app/converters/hwpx.py tests/test_converters_hwpx.py
git commit -m "feat: HWPX 변환기 — stdlib zip+XML, 표 평탄화 고지"
```

---

### Task 6: DOCX 변환기

**Files:**
- Create: `app/converters/docx.py`
- Test: `tests/test_converters_docx.py`

**Interfaces:**
- Consumes: Task 4의 공통 요소
- Produces: `app.converters.docx.convert_docx(data: bytes) -> ConversionResult`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_converters_docx.py` (픽스처는 python-docx로 즉석 생성 — 바이너리 커밋 없음)

```python
import io

import docx as docx_lib
import pytest

from app.converters import ConversionError
from app.converters.docx import convert_docx


def _docx_with_heading() -> bytes:
    d = docx_lib.Document()
    d.add_heading("사업 개요", level=1)
    d.add_paragraph("본문 문단입니다.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "항목"
    t.rows[0].cells[1].text = "금액 1억원"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_headings_become_markdown():
    r = convert_docx(_docx_with_heading())
    assert "# 사업 개요" in r.text
    assert "본문 문단입니다." in r.text


def test_table_flattened_with_warning():
    r = convert_docx(_docx_with_heading())
    assert "금액 1억원" in r.text
    assert any("표" in w for w in r.warnings)


def test_empty_docx_rejected():
    d = docx_lib.Document()
    buf = io.BytesIO()
    d.save(buf)
    with pytest.raises(ConversionError):
        convert_docx(buf.getvalue())
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_converters_docx.py -v`
Expected: FAIL — `No module named 'app.converters.docx'`

- [ ] **Step 3: 구현** — `app/converters/docx.py`

```python
from __future__ import annotations

import io
import re

import docx as docx_lib

from . import ConversionError, ConversionResult, check_zip_safety
from .headings import promote_headings

_HEADING_STYLE = re.compile(r"^(?:Heading|제목)\s*(\d)", re.IGNORECASE)


def convert_docx(data: bytes) -> ConversionResult:
    check_zip_safety(data)
    try:
        d = docx_lib.Document(io.BytesIO(data))
    except Exception as e:
        raise ConversionError("DOCX 파일을 읽지 못했어요. 텍스트 붙여넣기로 시도해주세요.") from e

    lines: list[str] = []
    has_heading = False
    for para in d.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        m = _HEADING_STYLE.match(para.style.name or "")
        if m:
            has_heading = True
            lines.append("#" * min(int(m.group(1)), 6) + " " + text)
        else:
            lines.append(text)

    warnings: list[str] = []
    if d.tables:
        for table in d.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        warnings.append("표가 텍스트로 평탄화됐어요 — 표 안 수치 검사는 부정확할 수 있어요")

    if not lines:
        raise ConversionError("파일에서 텍스트를 찾지 못했어요. 텍스트 붙여넣기로 시도해주세요.")

    text = "\n\n".join(lines)
    if not has_heading:
        text, extra = promote_headings(text)
        warnings.extend(extra)
    return ConversionResult(text=text, warnings=warnings)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_converters_docx.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add app/converters/docx.py tests/test_converters_docx.py
git commit -m "feat: DOCX 변환기 — 헤딩 스타일→마크다운, 표 평탄화"
```

---

### Task 7: PDF 변환기

**Files:**
- Create: `app/converters/pdf.py`
- Test: `tests/test_converters_pdf.py`

**Interfaces:**
- Consumes: Task 4의 공통 요소
- Produces: `app.converters.pdf.convert_pdf(data: bytes) -> ConversionResult`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_converters_pdf.py` (픽스처는 pymupdf로 즉석 생성; 폰트 이슈 회피를 위해 ASCII 사용 — 변환기는 언어 무관)

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_converters_pdf.py -v`
Expected: FAIL — `No module named 'app.converters.pdf'`

- [ ] **Step 3: 구현** — `app/converters/pdf.py`

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_converters_pdf.py -v`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
git add app/converters/pdf.py tests/test_converters_pdf.py
git commit -m "feat: PDF 변환기 — pymupdf, 스캔본 안내"
```

---

### Task 8: API — `/api/lint` · `/api/quota` · 정적 서빙

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `load_settings`(T1), `run_lint`/`LintOutcome`(T2), `Quota`(T3), `convert`/`normalize_pasted`/`ConversionError`(T4)
- Produces: FastAPI 앱 `app.main:app`. 응답 스키마(스펙 §4):
  `{"findings": [...], "converted_text": "...", "meta": {"llm_ran", "llm_skipped_reason": null|"quota_ip"|"quota_global"|"llm_error", "remaining_today", "conversion_warnings"}}`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_api.py`

```python
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod


class FakeClient:
    def complete(self, system: str, user: str) -> str:
        return "[]"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    import importlib

    importlib.reload(main_mod)  # 환경변수 반영해 앱 재생성
    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: FakeClient())
    return TestClient(main_mod.app)


def test_text_lint_rules_and_llm(client):
    resp = client.post("/api/lint", data={"text": "# 개요\n\n한 줄.", "use_llm": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["llm_ran"] is True
    assert body["meta"]["llm_skipped_reason"] is None
    assert any(f["checker"] == "missing-section" for f in body["findings"])
    assert body["converted_text"].startswith("# 개요")


def test_quota_exhausted_degrades_to_rules(client):
    client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"})  # 1회 소비
    resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"})
    body = resp.json()
    assert resp.status_code == 200  # 거부가 아니라 강등
    assert body["meta"]["llm_ran"] is False
    assert body["meta"]["llm_skipped_reason"] == "quota_ip"
    assert body["findings"]  # 룰 결과는 있음


def test_use_llm_false_skips_quota(client):
    for _ in range(3):
        resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "false"})
        assert resp.json()["meta"]["llm_ran"] is False
    assert client.get("/api/quota").json()["remaining_today"] == 1  # 소비 안 됨


def test_llm_error_refunds_quota(client, monkeypatch):
    def boom(*a, **k):
        raise main_mod.LLMUnavailable("no key")

    monkeypatch.setattr(main_mod, "make_client", boom)
    resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"})
    assert resp.json()["meta"]["llm_skipped_reason"] == "llm_error"
    assert client.get("/api/quota").json()["remaining_today"] == 1  # 환불됨


def test_global_cap(client, monkeypatch):
    monkeypatch.setattr(main_mod.quota, "_global", 1)
    # client_ip()가 x-forwarded-for를 읽으므로 헤더로 서로 다른 IP를 흉내 낸다
    client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"},
                headers={"x-forwarded-for": "1.1.1.1"})
    resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"},
                       headers={"x-forwarded-for": "2.2.2.2"})
    assert resp.json()["meta"]["llm_skipped_reason"] == "quota_global"


def test_no_input_rejected(client):
    assert client.post("/api/lint", data={}).status_code == 422


def test_oversize_text_rejected(client):
    resp = client.post("/api/lint", data={"text": "가" * 100_001, "use_llm": "false"})
    assert resp.status_code == 413


def test_conversion_error_maps_to_422(client):
    resp = client.post(
        "/api/lint",
        files={"file": ("plan.hwp", b"\xd0\xcf\x11\xe0" + b"\x00" * 10)},
    )
    assert resp.status_code == 422
    assert "hwpx" in resp.json()["error"].lower()


def test_quota_endpoint(client):
    assert client.get("/api/quota").json() == {"remaining_today": 1}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — `No module named 'app.main'`

- [ ] **Step 3: 구현** — `app/main.py`

```python
from __future__ import annotations

import concurrent.futures
import threading

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from planlint.llm.client import LLMUnavailable, make_client

from .config import load_settings
from .converters import ConversionError, convert, normalize_pasted
from .lint import run_lint
from .quota import Quota

settings = load_settings()
app = FastAPI(title="plan-lint-web", docs_url=None, redoc_url=None)
quota = Quota(settings.quota_db_path, settings.per_ip_daily, settings.global_daily, settings.quota_salt)
_llm_sem = threading.BoundedSemaphore(settings.llm_concurrency)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(settings.llm_concurrency * 2, 4))

RULES_TIMEOUT = 30  # 룰만 돌 때의 안전 타임아웃(초)


def client_ip(request: Request) -> str:
    for header in ("fly-client-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/quota")
def get_quota(request: Request):
    return {"remaining_today": quota.remaining(client_ip(request))}


@app.post("/api/lint")
async def lint(
    request: Request,
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    use_llm: bool = Form(True),
):
    warnings: list[str] = []
    if file is not None and file.filename:
        data = await file.read()  # 메모리에서만 처리 — 디스크 기록 없음 (무저장 원칙)
        if len(data) > settings.max_file_bytes:
            mb = settings.max_file_bytes // (1024 * 1024)
            return JSONResponse(status_code=413, content={"error": f"파일이 너무 커요. {mb}MB 이하로 올려주세요."})
        try:
            result = convert(data, file.filename)
        except ConversionError as e:
            return JSONResponse(status_code=422, content={"error": str(e)})
        source, warnings = result.text, result.warnings
    elif text and text.strip():
        result = normalize_pasted(text)
        source, warnings = result.text, result.warnings
    else:
        return JSONResponse(status_code=422, content={"error": "파일을 올리거나 텍스트를 붙여넣어주세요."})

    if len(source) > settings.max_text_chars:
        return JSONResponse(
            status_code=413,
            content={"error": f"텍스트가 너무 길어요. {settings.max_text_chars:,}자 이하로 줄여주세요."},
        )
    if not source.strip():
        return JSONResponse(status_code=422, content={"error": "파일에서 텍스트를 찾지 못했어요. 텍스트 붙여넣기로 시도해주세요."})

    ip = client_ip(request)
    llm_client = None
    skipped_reason: str | None = None
    if use_llm:
        skipped_reason = quota.try_consume(ip)
        if skipped_reason is None:
            try:
                llm_client = make_client()
            except LLMUnavailable:
                quota.refund(ip)
                skipped_reason = "llm_error"

    def work():
        if llm_client is not None:
            with _llm_sem:
                return run_lint(source, llm_client)
        return run_lint(source)

    timeout = settings.llm_timeout_seconds if llm_client is not None else RULES_TIMEOUT
    future = _executor.submit(work)
    try:
        outcome = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        if llm_client is not None:
            quota.refund(ip)
            skipped_reason = "llm_error"
            outcome = run_lint(source)  # 룰만이라도 반환
        else:
            return JSONResponse(status_code=500, content={"error": "검사가 예상보다 오래 걸려요. 잠시 후 다시 시도해주세요."})

    if outcome.llm_error:
        quota.refund(ip)
        skipped_reason = "llm_error"

    return {
        "findings": outcome.findings,
        "converted_text": source,
        "meta": {
            "llm_ran": outcome.llm_ran,
            "llm_skipped_reason": skipped_reason,
            "remaining_today": quota.remaining(ip),
            "conversion_warnings": warnings,
        },
    }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
```

주의: `StaticFiles` mount 때문에 `app/static/` 디렉터리가 있어야 앱이 뜬다 — 이 태스크에서 빈 `app/static/index.html`(내용: `<!doctype html><title>plan-lint</title>`)을 함께 만들 것. Task 9가 실제 프론트로 교체.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_api.py -v`
Expected: 9 passed. 전체도 확인: `pytest -q` → 전부 passed

- [ ] **Step 5: 커밋**

```bash
git add app/main.py app/static/index.html tests/test_api.py
git commit -m "feat: /api/lint·/api/quota — 쿼터 강등·환불·타임아웃·무저장 처리"
```

---

### Task 9: 프론트 — 입력 화면 + 진단 리포트

**Files:**
- Create: `app/static/index.html` (Task 8의 플레이스홀더 교체), `app/static/style.css`, `app/static/app.js`

**Interfaces:**
- Consumes: `POST /api/lint`(FormData: `file` 또는 `text`, `use_llm`), `GET /api/quota` (Task 8 스키마)
- Produces: 한 페이지 두 상태 UI. 자동화 테스트 없음 — Step 4의 브라우저 수동 검증으로 대체(브라우저 도구로 확인).

- [ ] **Step 1: `app/static/index.html` 작성 (전체 교체)**

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>plan-lint — 사업계획서 사전 진단</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header>
  <h1>plan-lint</h1>
  <p class="tagline">사업계획서를 제출하기 전에, 심사자가 볼 결함을 미리 확인하세요</p>
</header>

<main id="input-view">
  <div class="tabs">
    <button id="tab-file" class="tab active" type="button">파일 올리기</button>
    <button id="tab-text" class="tab" type="button">텍스트 붙여넣기</button>
  </div>

  <div id="panel-file" class="panel">
    <div id="dropzone" tabindex="0">
      <p><strong>.hwpx / .pdf / .docx</strong> 파일을 끌어다 놓거나 클릭해서 선택하세요</p>
      <p class="hint">구형 .hwp는 한글에서 "다른 이름으로 저장 → HWPX"로 저장한 뒤 올려주세요</p>
      <input type="file" id="file-input" accept=".hwpx,.pdf,.docx" hidden>
    </div>
    <p id="file-name" class="file-name"></p>
  </div>

  <div id="panel-text" class="panel" hidden>
    <textarea id="text-input" rows="14" placeholder="사업계획서 본문을 붙여넣으세요"></textarea>
  </div>

  <label class="llm-toggle">
    <input type="checkbox" id="use-llm" checked>
    AI 정밀 검사 (논리 단절·근거 없는 주장·내부 모순)
    <span id="quota-info" class="quota"></span>
  </label>

  <button id="submit" type="button" class="primary">진단 시작</button>
  <p id="error-box" class="error" hidden></p>
  <p class="privacy">🔒 업로드한 문서는 서버에 저장되지 않으며, 진단 후 즉시 폐기됩니다</p>
</main>

<main id="report-view" hidden>
  <div id="banner" class="banner" hidden></div>
  <div id="summary" class="summary"></div>
  <div class="columns">
    <section class="col"><h2>원문</h2><div id="source-pane" class="source"></div></section>
    <section class="col"><h2>진단 결과</h2><div id="cards-pane"></div></section>
  </div>
  <div class="actions">
    <button id="copy-btn" type="button">결과 복사</button>
    <button id="again-btn" type="button">다시 진단하기</button>
  </div>
</main>

<div id="loading" hidden><p>진단 중이에요… 문서 길이에 따라 최대 1분쯤 걸릴 수 있어요</p></div>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: `app/static/style.css` 작성** — "친숙한 진단 리포트" 톤: 밝은 배경, 부드러운 카드, 심각도 색(치명 `#d64545` / 주의 `#e8a13c` / 참고 `#4a90d9`)

```css
* { box-sizing: border-box; margin: 0; }
body { font-family: "Pretendard", "Malgun Gothic", sans-serif; background: #f7f8fa; color: #24292f; line-height: 1.6; }
header { text-align: center; padding: 2.5rem 1rem 1rem; }
header h1 { font-size: 1.6rem; }
.tagline { color: #57606a; margin-top: .3rem; }
main { max-width: 960px; margin: 1.5rem auto; padding: 0 1rem; }
.tabs { display: flex; gap: .5rem; margin-bottom: 1rem; }
.tab { padding: .55rem 1.1rem; border: 1px solid #d0d7de; background: #fff; border-radius: 8px; cursor: pointer; font-size: 1rem; }
.tab.active { background: #24292f; color: #fff; border-color: #24292f; }
#dropzone { border: 2px dashed #d0d7de; border-radius: 12px; background: #fff; padding: 2.5rem 1rem; text-align: center; cursor: pointer; }
#dropzone.dragover { border-color: #4a90d9; background: #eef5fc; }
.hint { color: #57606a; font-size: .875rem; margin-top: .5rem; }
.file-name { margin-top: .5rem; font-weight: 600; }
textarea { width: 100%; border: 1px solid #d0d7de; border-radius: 12px; padding: 1rem; font-size: .95rem; font-family: inherit; }
.llm-toggle { display: block; margin: 1rem 0; }
.quota { color: #57606a; font-size: .875rem; margin-left: .5rem; }
button.primary { width: 100%; padding: .9rem; font-size: 1.05rem; background: #2da44e; color: #fff; border: 0; border-radius: 10px; cursor: pointer; }
button.primary:disabled { background: #94d3a2; }
.privacy { text-align: center; color: #57606a; font-size: .875rem; margin-top: 1rem; }
.error { background: #ffebe9; border: 1px solid #ffc1bc; border-radius: 8px; padding: .8rem 1rem; margin-top: 1rem; }
.banner { background: #fff8c5; border: 1px solid #eed888; border-radius: 8px; padding: .8rem 1rem; margin-bottom: 1rem; }
.summary { display: flex; gap: .6rem; margin-bottom: 1rem; flex-wrap: wrap; }
.badge { padding: .35rem .8rem; border-radius: 999px; font-weight: 600; font-size: .9rem; color: #fff; }
.badge.critical { background: #d64545; } .badge.warning { background: #e8a13c; } .badge.info { background: #4a90d9; }
.badge.clean { background: #2da44e; }
.columns { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
@media (max-width: 800px) { .columns { grid-template-columns: 1fr; } }
.col h2 { font-size: 1rem; margin-bottom: .5rem; color: #57606a; }
.source { background: #fff; border: 1px solid #d0d7de; border-radius: 12px; padding: 1rem; max-height: 70vh; overflow-y: auto; white-space: pre-wrap; font-size: .9rem; }
mark { border-radius: 3px; padding: 0 2px; scroll-margin: 30vh; }
mark.critical { background: #ffd7d5; } mark.warning { background: #fff0c2; } mark.info { background: #d8e9fb; }
mark.focused { outline: 2px solid #24292f; }
.card { background: #fff; border: 1px solid #d0d7de; border-left-width: 5px; border-radius: 10px; padding: .9rem 1rem; margin-bottom: .8rem; cursor: pointer; }
.card.critical { border-left-color: #d64545; } .card.warning { border-left-color: #e8a13c; } .card.info { border-left-color: #4a90d9; }
.card h3 { font-size: .95rem; margin-bottom: .3rem; }
.card .sev { font-size: .8rem; font-weight: 700; }
.card.critical .sev { color: #d64545; } .card.warning .sev { color: #b07a1e; } .card.info .sev { color: #2f6fad; }
.card blockquote { border-left: 3px solid #d0d7de; margin: .4rem 0; padding-left: .6rem; color: #57606a; font-size: .875rem; }
.card .suggestion { background: #f0fbf4; border-radius: 6px; padding: .5rem .7rem; font-size: .875rem; margin-top: .4rem; }
.actions { margin-top: 1.2rem; display: flex; gap: .6rem; }
.actions button { padding: .7rem 1.2rem; border: 1px solid #d0d7de; background: #fff; border-radius: 8px; cursor: pointer; }
#loading { position: fixed; inset: 0; background: rgba(255,255,255,.85); display: flex; align-items: center; justify-content: center; font-size: 1.1rem; }
```

- [ ] **Step 3: `app/static/app.js` 작성**

```javascript
const CHECKER_LABELS = {
  "missing-section": "필수 항목이 빠졌어요",
  "length-violation": "분량 기준을 벗어났어요",
  "numeric-consistency": "숫자가 서로 맞지 않아요",
  "logic-gap": "논리 연결이 끊겨요",
  "unsupported-claim": "근거가 없는 주장이에요",
  "internal-contradiction": "문서 안에서 말이 엇갈려요",
};
const SEV_LABELS = { critical: "치명", warning: "주의", info: "참고" };
const SKIP_MESSAGES = {
  quota_ip: "오늘 AI 정밀 검사 횟수를 다 썼어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  quota_global: "오늘 전체 AI 정밀 검사가 마감됐어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  llm_error: "AI 정밀 검사 중 문제가 생겨 기본 검사 결과만 보여드려요. 사용 횟수는 차감되지 않았어요.",
};

const $ = (id) => document.getElementById(id);
let selectedFile = null;
let lastResult = null;

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// --- 탭 ---
$("tab-file").onclick = () => switchTab(true);
$("tab-text").onclick = () => switchTab(false);
function switchTab(isFile) {
  $("tab-file").classList.toggle("active", isFile);
  $("tab-text").classList.toggle("active", !isFile);
  $("panel-file").hidden = !isFile;
  $("panel-text").hidden = isFile;
}

// --- 파일 선택/드롭 ---
const dz = $("dropzone");
dz.onclick = () => $("file-input").click();
$("file-input").onchange = (e) => pickFile(e.target.files[0]);
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
dz.ondragleave = () => dz.classList.remove("dragover");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("dragover"); pickFile(e.dataTransfer.files[0]); };
function pickFile(f) {
  if (!f) return;
  if (f.name.toLowerCase().endsWith(".hwp")) {
    showError("구형 한글(.hwp) 파일이에요. 한글에서 \"다른 이름으로 저장 → HWPX\"로 저장한 뒤 다시 올려주세요. 어렵다면 텍스트 붙여넣기를 이용해주세요.");
    return;
  }
  selectedFile = f;
  $("file-name").textContent = "선택됨: " + f.name;
  hideError();
}

function showError(msg) { const b = $("error-box"); b.textContent = msg; b.hidden = false; }
function hideError() { $("error-box").hidden = true; }

// --- 쿼터 표시 ---
async function refreshQuota() {
  try {
    const r = await (await fetch("/api/quota")).json();
    $("quota-info").textContent = `(오늘 남은 횟수: ${r.remaining_today}회)`;
  } catch { /* 표시는 부가 기능 — 실패해도 무시 */ }
}
refreshQuota();

// --- 진단 요청 ---
$("submit").onclick = async () => {
  hideError();
  const fd = new FormData();
  const fileTab = $("tab-file").classList.contains("active");
  if (fileTab) {
    if (!selectedFile) { showError("파일을 먼저 선택해주세요."); return; }
    fd.append("file", selectedFile);
  } else {
    const t = $("text-input").value.trim();
    if (!t) { showError("텍스트를 붙여넣어주세요."); return; }
    fd.append("text", t);
  }
  fd.append("use_llm", $("use-llm").checked ? "true" : "false");

  $("loading").hidden = false;
  $("submit").disabled = true;
  try {
    const resp = await fetch("/api/lint", { method: "POST", body: fd });
    const body = await resp.json();
    if (!resp.ok) {
      showError(body.error || "진단에 실패했어요. 잠시 후 다시 시도해주세요.");
      if (resp.status === 422 && fileTab) switchTab(false); // 변환 실패 → 붙여넣기로 유도
      return;
    }
    lastResult = body;
    renderReport(body);
  } catch {
    showError("서버에 연결하지 못했어요. 잠시 후 다시 시도해주세요.");
  } finally {
    $("loading").hidden = true;
    $("submit").disabled = false;
    refreshQuota();
  }
};

// --- 리포트 렌더 ---
function renderReport(body) {
  $("input-view").hidden = true;
  $("report-view").hidden = false;

  const banner = $("banner");
  const notes = [];
  if (body.meta.llm_skipped_reason) notes.push(SKIP_MESSAGES[body.meta.llm_skipped_reason]);
  for (const w of body.meta.conversion_warnings) notes.push(w);
  banner.hidden = notes.length === 0;
  banner.textContent = notes.join(" · ");

  // 요약 배지
  const counts = { critical: 0, warning: 0, info: 0 };
  for (const f of body.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;
  $("summary").innerHTML = body.findings.length === 0
    ? '<span class="badge clean">발견된 결함이 없어요</span>'
    : Object.entries(counts).filter(([, n]) => n > 0)
        .map(([sev, n]) => `<span class="badge ${sev}">${SEV_LABELS[sev]} ${n}</span>`).join("");

  // 원문 + 하이라이트: quotes를 문서 등장 순으로 <mark> 치환 (엔진이 인용 실존을 보증)
  let html = esc(body.converted_text);
  body.findings.forEach((f, idx) => {
    for (const q of f.quotes || []) {
      const eq = esc(q);
      if (html.includes(eq)) {
        html = html.replace(eq, `<mark class="${f.severity}" data-idx="${idx}">${eq}</mark>`);
      } // 매칭 실패 시 카드만 표시 (스펙 §7)
    }
  });
  $("source-pane").innerHTML = html;

  // 결함 카드
  $("cards-pane").innerHTML = body.findings.map((f, idx) => `
    <div class="card ${f.severity}" data-idx="${idx}">
      <span class="sev">${SEV_LABELS[f.severity]}</span>
      <h3>${esc(CHECKER_LABELS[f.checker] || f.checker)}</h3>
      <p>${esc(f.message)}</p>
      ${(f.quotes || []).map((q) => `<blockquote>${esc(q)}</blockquote>`).join("")}
      ${f.suggestion ? `<p class="suggestion">💡 ${esc(f.suggestion)}</p>` : ""}
    </div>`).join("");

  // 카드 ↔ 하이라이트 상호 스크롤
  document.querySelectorAll(".card").forEach((card) => {
    card.onclick = () => focusMark("#source-pane mark", card.dataset.idx);
  });
  document.querySelectorAll("#source-pane mark").forEach((m) => {
    m.onclick = () => focusMark("#cards-pane .card", m.dataset.idx);
  });
}

function focusMark(selector, idx) {
  const el = document.querySelector(`${selector}[data-idx="${idx}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  document.querySelectorAll(".focused").forEach((x) => x.classList.remove("focused"));
  el.classList.add("focused");
}

// --- 결과 복사 (마크다운) ---
$("copy-btn").onclick = () => {
  if (!lastResult) return;
  const lines = ["# plan-lint 진단 결과", ""];
  for (const f of lastResult.findings) {
    lines.push(`## [${SEV_LABELS[f.severity]}] ${CHECKER_LABELS[f.checker] || f.checker}`);
    lines.push(f.message);
    for (const q of f.quotes || []) lines.push(`> ${q}`);
    if (f.suggestion) lines.push(`제안: ${f.suggestion}`);
    lines.push("");
  }
  navigator.clipboard.writeText(lines.join("\n"));
  $("copy-btn").textContent = "복사됐어요!";
  setTimeout(() => ($("copy-btn").textContent = "결과 복사"), 1500);
};

$("again-btn").onclick = () => {
  $("report-view").hidden = true;
  $("input-view").hidden = false;
};
```

- [ ] **Step 4: 브라우저 수동 검증**

Run: `.venv/Scripts/uvicorn app.main:app --port 8000` 실행 후 브라우저에서 `http://localhost:8000` 열기.
확인 항목: ① 붙여넣기 탭에 `# 개요\n\n한 줄.` 입력 → 진단 → 결함 카드·배지 표시 ② 카드 클릭 시 원문 하이라이트로 스크롤 ③ .hwp 파일 선택 시 재저장 안내 ④ 무저장 문구·쿼터 표시 보임 ⑤ 결과 복사 동작.
(ANTHROPIC_API_KEY 없이 실행 → `llm_error` 배너와 "차감되지 않았어요" 문구도 함께 확인.)

- [ ] **Step 5: 커밋**

```bash
git add app/static/
git commit -m "feat: 프론트 — 업로드/붙여넣기 입력 + 진단 리포트 (하이라이트·카드 상호 스크롤)"
```

---

### Task 10: 배포 준비 — Dockerfile · CI · 스모크 · README

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `fly.toml`, `.github/workflows/ci.yml`, `scripts/smoke.py`, `README.md`

**Interfaces:**
- Consumes: 전체 앱
- Produces: 배포 가능한 컨테이너와 CI. `python scripts/smoke.py <base_url>`이 배포 후 E2E 확인 도구.

- [ ] **Step 1: `Dockerfile` + `.dockerignore`**

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PLW_QUOTA_DB=/tmp/quota.sqlite3
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

(`git`은 planlint git 의존성 설치용. 쿼터 DB는 `/tmp` — 재배포 시 카운터가 리셋되지만 무저장 원칙상 볼륨을 안 쓰는 의도적 트레이드오프.)

`.dockerignore`:
```
.git
.venv
tests
docs
__pycache__
*.sqlite3
```

- [ ] **Step 2: 빌드·기동 확인**

Run: `docker build -t plan-lint-web . && docker run --rm -p 8080:8080 plan-lint-web` 후 `curl -s http://localhost:8080/api/quota`
Expected: `{"remaining_today":1}` (Docker 미설치 환경이면 이 스텝은 배포 시점으로 미루고 넘어감 — README에 표기)

- [ ] **Step 3: `fly.toml`**

```toml
app = "plan-lint-web"
primary_region = "nrt"

[build]

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"
```

(배포는 사용자가 `fly launch --copy-config` + `fly secrets set ANTHROPIC_API_KEY=...`로 직접 실행 — 계획 범위는 설정 파일까지.)

- [ ] **Step 4: `.github/workflows/ci.yml`**

```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements-dev.txt
      - run: pytest -q
```

- [ ] **Step 5: `scripts/smoke.py`** — 배포 후 E2E 확인 (CLI 하네스 철학의 웹 버전)

```python
"""배포된 서버에 실제 요청을 보내 룰 검사가 동작하는지 확인한다.

사용법: python scripts/smoke.py https://plan-lint-web.fly.dev
"""
from __future__ import annotations

import sys

import httpx

FIXTURE = "# 개요\n\n한 줄짜리 계획서."  # missing-section이 반드시 잡혀야 함


def main(base: str) -> int:
    r = httpx.post(f"{base}/api/lint", data={"text": FIXTURE, "use_llm": "false"}, timeout=60)
    r.raise_for_status()
    body = r.json()
    checkers = {f["checker"] for f in body["findings"]}
    assert "missing-section" in checkers, f"missing-section 미검출: {checkers}"
    assert body["meta"]["llm_ran"] is False
    q = httpx.get(f"{base}/api/quota", timeout=30)
    assert "remaining_today" in q.json()
    print(f"OK — 결함 {len(body['findings'])}건 검출, quota 응답 정상")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/")))
```

- [ ] **Step 6: `README.md`**

```markdown
# plan-lint-web

정부 창업지원사업 사업계획서를 제출 전에 진단하는 웹 서비스.
[plan-lint CLI](https://github.com/Choihello/plan-lint)의 엔진(`planlint`)을 그대로 사용한다.

- 파일 업로드(.hwpx/.pdf/.docx) 또는 텍스트 붙여넣기 → 결함 진단 리포트
- 룰 검사 3종 무료·무제한 / AI 정밀 검사 3종은 IP당 하루 1회 (전역 일일 상한 있음)
- **완전 무저장**: 업로드 문서는 메모리에서만 처리하고 즉시 폐기

## 로컬 실행

    pip install -r requirements-dev.txt
    uvicorn app.main:app --port 8000
    # AI 정밀 검사를 켜려면: ANTHROPIC_API_KEY 환경변수 설정

## 테스트 / 배포 후 확인

    pytest -q
    python scripts/smoke.py https://<배포 URL>

## 설정 (환경변수)

| 변수 | 기본값 | 의미 |
|---|---|---|
| PLW_PER_IP_DAILY | 1 | IP당 하루 LLM 검사 횟수 |
| PLW_GLOBAL_DAILY | 50 | 전역 하루 LLM 검사 횟수 |
| PLW_MAX_FILE_BYTES | 5242880 | 업로드 파일 상한 |
| PLW_MAX_TEXT_CHARS | 100000 | 변환 후 텍스트 상한 |
| PLW_LLM_TIMEOUT | 60 | LLM 검사 타임아웃(초) |
| PLW_LLM_CONCURRENCY | 3 | 동시 LLM 검사 상한 |
| PLW_QUOTA_DB | quota.sqlite3 | 쿼터 DB 경로 |
| PLW_QUOTA_SALT | (내장) | IP 해시 솔트 — 운영 시 변경 권장 |
```

- [ ] **Step 7: 전체 테스트 + 커밋**

Run: `pytest -q`
Expected: 전부 passed

```bash
git add Dockerfile .dockerignore fly.toml .github/ scripts/ README.md
git commit -m "chore: Dockerfile·fly 설정·CI·스모크 스크립트·README"
```

- [ ] **Step 8: 실파일 수동 검증 (스펙 §6 요구)**

한글(또는 한컴독스)에서 만든 실제 `.hwpx`, Word `.docx`, 한글에서 내보낸 `.pdf`를 각 1개 이상 로컬 서버에 업로드해 변환 품질 확인. 표 평탄화 경고·제목 승격이 실파일에서 자연스러운지 본다. 문제 발견 시 해당 변환기 태스크로 돌아가 수정 (이 검증 전에는 공개 배포 금지).

---

## 후속 (계획 범위 밖)

- GitHub 원격 repo 생성·푸시, Fly.io 배포·시크릿 설정 — 사용자 승인 후 진행
- v2 백로그: 로그인·히스토리, .hwp 직접 변환, 프로파일 선택 UI, PDF 내보내기 (스펙 §11)
