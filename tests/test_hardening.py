"""P0/P1 보완 회귀 테스트 — 이벤트 루프 차단, LLM 예산·환불, 백프레셔, 헤더, SQLite."""
import asyncio
import importlib
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.llm_budget import BudgetPool, LLMCancelled, plan_shares
from app.quota import Quota


class FakeClient:
    def complete(self, system: str, user: str) -> str:
        return "[]"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: FakeClient())
    return TestClient(main_mod.app)


# ---------- P0-1: 이벤트 루프 차단 ----------

def test_slow_lint_does_not_block_other_requests(tmp_path, monkeypatch):
    """느린 검사가 도는 동안에도 /api/quota가 즉시 응답해야 한다 (실측 1.5초 차단 회귀).

    커버 범위: `Future.result(timeout=)`을 async 핸들러에서 직접 호출하는 패턴의 회귀.
    미커버: GIL을 붙잡는 순수 CPU 작업. 실제 워크로드에서 지배적인 대기는 LLM 네트워크
    I/O(GIL 해제)이며, 실서버 실측에서는 무부하 0.207초 대비 부하 중 0.21초로 차단이
    관측되지 않았다. CPU 경계 격리(프로세스 풀)는 별도 과제로 남겨둔다.
    """
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    importlib.reload(main_mod)

    def slow_run_lint(text, llm_client=None):
        time.sleep(1.5)
        return main_mod.run_lint(text)

    monkeypatch.setattr(main_mod, "run_lint", slow_run_lint)

    async def scenario():
        transport = httpx.ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            lint_task = asyncio.create_task(
                ac.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "false"}, timeout=30)
            )
            await asyncio.sleep(0.3)  # 검사가 진행 중인 시점
            started = time.perf_counter()
            quota_resp = await ac.get("/api/quota", timeout=10)
            elapsed = time.perf_counter() - started
            await lint_task
            return quota_resp.status_code, elapsed

    status, elapsed = asyncio.run(scenario())
    assert status == 200
    assert elapsed < 0.3, f"검사 중 quota 응답이 {elapsed:.2f}초 지연 — 이벤트 루프가 차단됨"


# ---------- P0-2: LLM 호출 예산·취소·환불 ----------

def test_budget_caps_calls_and_degrades_honestly():
    pool = BudgetPool(FakeClient(), total_calls=3)
    for _ in range(5):
        pool.complete("s", "u")
    assert pool.calls_started == 3
    assert pool.budget_exhausted is True


def test_budget_pool_passes_through_until_cap():
    calls = []

    class Counting:
        def complete(self, system, user):
            calls.append(1)
            return "[]"

    pool = BudgetPool(Counting(), total_calls=2)
    for _ in range(3):
        pool.complete("s", "u")  # 예산 초과분은 내부 클라이언트를 호출하지 않는다
    assert len(calls) == 2


def test_cancel_stops_further_calls():
    pool = BudgetPool(FakeClient(), total_calls=10)
    pool.complete("s", "u")
    pool.cancel()
    with pytest.raises(LLMCancelled):
        pool.complete("s", "u")
    assert pool.calls_started == 1


def test_shares_reserve_fixed_checkers_first():
    shares = plan_shares(24, ["unsupported-claim", "vague-goal"],
                         {"logic-gap": 2, "internal-contradiction": 1, "enrich": 1})
    assert shares["internal-contradiction"] == 1
    assert shares["enrich"] == 1
    assert shares["unsupported-claim"] == shares["vague-goal"] == 10
    assert sum(shares.values()) <= 24


def test_share_cannot_exceed_own_allocation():
    pool = BudgetPool(FakeClient(), total_calls=10)
    small = pool.share("unsupported-claim", 2)
    for _ in range(5):
        small.complete("s", "u")
    assert pool.calls_started == 2, "자기 몫을 넘어 총 예산을 잠식함"
    assert "unsupported-claim" in pool.limited
    # 몫을 다 쓴 체커가 있어도 다른 체커의 몫은 살아 있다
    other = pool.share("enrich", 1)
    other.complete("s", "u")
    assert pool.calls_started == 3


def test_request_enforces_call_budget(tmp_path, monkeypatch):
    """헤딩이 많은 문서라도 요청당 호출 수가 설정 상한을 넘지 않는다."""
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("PLW_MAX_LLM_CALLS", "5")
    importlib.reload(main_mod)

    calls = {"n": 0}

    class Counting:
        def complete(self, system, user):
            calls["n"] += 1
            return "[]"

    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: Counting())
    doc = "\n\n".join(f"{i}. 항목 {i}\n본문 내용입니다." for i in range(1, 16))
    with TestClient(main_mod.app) as c:
        body = c.post("/api/lint", data={"text": doc, "use_llm": "true"}).json()
    assert calls["n"] <= 5, f"예산 5회를 초과해 {calls['n']}회 호출됨"
    # 어떤 검사가 잘렸는지 이름으로 고지해야 한다 (무엇이 빠졌는지 보이지 않으면 안 됨)
    warned = " ".join(body["meta"]["conversion_warnings"])
    assert "앞부분까지만" in warned, warned
    assert any(k in warned for k in ("근거 없는 주장", "구체성 부족", "보강 제안", "내부 모순")), warned


def test_long_document_still_runs_whole_doc_checks_and_enrich():
    """섹션이 많아도 내부 모순·보강 제안이 굶지 않아야 한다.

    회귀: 총량만 막았을 때 unsupported-claim이 예산 24회를 전부 먹고
    internal-contradiction과 보강 제안이 아예 실행되지 않았다(실측 섹션 38개).
    """
    from app.converters import normalize_pasted
    from app.lint import run_lint

    doc = "\n\n".join(f"{i}. 항목 {i}\n사업 내용을 서술합니다." for i in range(1, 40))
    src = normalize_pasted(doc).text

    seen = []

    class Tracer:
        def complete(self, system, user):
            if "빈칸" in system:
                seen.append("enrich")
            elif "모순" in system:
                seen.append("internal-contradiction")
            elif "출처" in system:
                seen.append("unsupported-claim")
            elif "목표" in system:
                seen.append("vague-goal")
            return "[]"

    pool = BudgetPool(Tracer(), total_calls=24)
    run_lint(src, llm_client=pool)

    assert "internal-contradiction" in seen, "문서 전체 검사가 예산 부족으로 실행되지 않음"
    assert "enrich" in seen, "보강 제안이 예산 부족으로 실행되지 않음"
    assert "vague-goal" in seen, "구체성 검사가 실행되지 않음"
    assert pool.calls_started <= 24


def test_no_refund_when_external_calls_already_started(tmp_path, monkeypatch):
    """타임아웃돼도 이미 유료 호출이 나갔으면 환불하지 않는다 (비용 상한 우회 차단)."""
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("PLW_LLM_TIMEOUT", "1")
    importlib.reload(main_mod)

    class SlowClient:
        def complete(self, system, user):
            time.sleep(3)
            return "[]"

    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: SlowClient())
    with TestClient(main_mod.app) as c:
        body = c.post("/api/lint", data={"text": "# 개요\n\n본문", "use_llm": "true"}).json()
        assert body["meta"]["llm_skipped_reason"] == "llm_error"
        assert body["meta"]["remaining_today"] == 0, "외부 호출 발생 후에도 쿼터가 환불됨"


def test_refund_when_no_external_call_happened(tmp_path, monkeypatch):
    """키 부재 등 외부 호출 전에 실패하면 환불한다."""
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    importlib.reload(main_mod)

    def boom(*a, **k):
        raise main_mod.LLMUnavailable("no key")

    monkeypatch.setattr(main_mod, "make_client", boom)
    with TestClient(main_mod.app) as c:
        body = c.post("/api/lint", data={"text": "# 개요\n\n본문", "use_llm": "true"}).json()
        assert body["meta"]["remaining_today"] == 1


# ---------- P0-4(부분): 관리자 절대 상한 ----------

ADMIN = {"x-admin-token": "test-admin-token"}


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("PLW_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("PLW_ADMIN_DAILY", "2")
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: FakeClient())
    return TestClient(main_mod.app)


def test_admin_has_daily_cap(admin_client):
    """토큰이 유출돼도 무제한 유료 호출이 되지 않아야 한다."""
    for i in range(2):
        body = admin_client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"},
                                 headers=ADMIN).json()
        assert body["meta"]["llm_ran"] is True, f"{i+1}회차가 상한 전에 막힘"
    body = admin_client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"},
                             headers=ADMIN).json()
    assert body["meta"]["llm_ran"] is False
    assert body["meta"]["llm_skipped_reason"] == "quota_admin"
    assert body["findings"], "상한 초과여도 룰 결과는 반환해야 한다"


def test_admin_cap_does_not_consume_public_quota(tmp_path, monkeypatch):
    """관리자 사용이 공개 전역 캡을 잠식하면 일반 사용자가 굶는다."""
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("PLW_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("PLW_ADMIN_DAILY", "5")
    monkeypatch.setenv("PLW_GLOBAL_DAILY", "2")  # 전역 캡을 좁혀 잠식을 드러낸다
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: FakeClient())
    with TestClient(main_mod.app) as c:
        for _ in range(3):
            c.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers=ADMIN)
        # 관리자가 3회 썼어도 공개 전역 2회는 그대로 남아 있어야 한다
        body = c.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}).json()
        assert body["meta"]["llm_skipped_reason"] is None, "관리자 사용이 공개 쿼터를 잠식함"
        assert body["meta"]["llm_ran"] is True


def test_admin_quota_endpoint_reports_remaining(admin_client):
    r = admin_client.get("/api/quota", headers=ADMIN).json()
    assert r["admin"] is True
    assert r["remaining_today"] == 2
    admin_client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers=ADMIN)
    assert admin_client.get("/api/quota", headers=ADMIN).json()["remaining_today"] == 1


# ---------- P0-3(부분): 백프레셔 ----------

def test_saturation_returns_503_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(main_mod._inflight, "acquire", lambda blocking=False: False)
    resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "false"})
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "10"


# ---------- P1-1: SQLite ----------

def test_remaining_leaves_no_open_transaction(tmp_path):
    q = Quota(str(tmp_path / "q.sqlite3"), 1, 50, "salt")
    q.try_consume("1.1.1.1")
    q.remaining("1.1.1.1")
    assert q._conn.in_transaction is False


def test_second_connection_can_write_after_remaining(tmp_path):
    """조회가 쓰기 잠금을 남기면 다른 연결이 'database is locked'로 실패한다."""
    path = str(tmp_path / "q.sqlite3")
    q = Quota(path, 1, 50, "salt")
    q.try_consume("1.1.1.1")
    q.remaining("1.1.1.1")
    other = sqlite3.connect(path, timeout=2)
    other.execute("INSERT INTO usage (ip_hash, date, count) VALUES ('x', '2000-01-01', 1)")
    other.commit()
    other.close()


def test_old_rows_still_purged_on_consume(tmp_path):
    path = str(tmp_path / "q.sqlite3")
    q = Quota(path, 5, 50, "salt")
    q._conn.execute("INSERT INTO usage (ip_hash, date, count) VALUES ('old', '2000-01-01', 3)")
    q._conn.commit()
    q.try_consume("1.1.1.1")
    rows = q._conn.execute("SELECT COUNT(*) FROM usage WHERE date = '2000-01-01'").fetchone()[0]
    assert rows == 0


# ---------- P1-3/P1-4: 헤더 ----------

def test_ai_off_makes_no_external_call(tmp_path, monkeypatch):
    """화면 고지('끄면 전송 없음')가 실제 동작과 일치하는지 검증한다."""
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    importlib.reload(main_mod)
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return FakeClient()

    monkeypatch.setattr(main_mod, "make_client", spy)
    with TestClient(main_mod.app) as c:
        body = c.post("/api/lint", data={"text": "# 개요\n\n본문", "use_llm": "false"}).json()
    assert called["n"] == 0, "AI 검사를 껐는데 외부 클라이언트가 생성됨"
    assert body["meta"]["llm_ran"] is False


def test_security_headers_present(client):
    r = client.get("/")
    for h in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy",
              "Permissions-Policy", "X-Frame-Options"):
        assert h in r.headers, f"{h} 누락"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_api_responses_are_no_store(client):
    """진단 응답에는 문서 본문이 담긴다 — 캐시에 남으면 안 된다."""
    assert client.get("/api/quota").headers["Cache-Control"] == "no-store"
    r = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "false"})
    assert r.headers["Cache-Control"] == "no-store"
