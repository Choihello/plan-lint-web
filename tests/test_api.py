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


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PLW_QUOTA_DB", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("PLW_ADMIN_TOKEN", "test-admin-token")
    import importlib

    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "make_client", lambda *a, **k: FakeClient())
    return TestClient(main_mod.app)


ADMIN = {"x-admin-token": "test-admin-token"}


def test_admin_bypasses_quota(admin_client):
    # per_ip 기본 1회인데 관리자는 몇 번을 돌려도 LLM이 돈다
    for _ in range(3):
        resp = admin_client.post(
            "/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers=ADMIN
        )
        assert resp.json()["meta"]["llm_ran"] is True
        assert resp.json()["meta"]["llm_skipped_reason"] is None
    # 관리자 사용이 일반 쿼터를 소모하지 않음
    assert admin_client.get("/api/quota").json()["remaining_today"] == 1


def test_admin_quota_endpoint_unlimited(admin_client):
    r = admin_client.get("/api/quota", headers=ADMIN)
    assert r.json()["remaining_today"] == -1  # 프론트가 '무제한'으로 표시


def test_wrong_admin_token_uses_normal_quota(admin_client):
    bad = {"x-admin-token": "wrong"}
    admin_client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers=bad)
    resp = admin_client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers=bad)
    assert resp.json()["meta"]["llm_skipped_reason"] == "quota_ip"


def test_admin_header_ignored_when_token_unconfigured(client):
    # 서버에 토큰이 설정 안 됐으면 (기본 "") 어떤 헤더로도 관리자 불가
    client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers={"x-admin-token": ""})
    resp = client.post("/api/lint", data={"text": "# a\n\nb", "use_llm": "true"}, headers={"x-admin-token": ""})
    assert resp.json()["meta"]["llm_skipped_reason"] == "quota_ip"


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


def test_whitespace_only_text_gets_specific_message(client):
    resp = client.post("/api/lint", data={"text": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"] == "붙여넣은 내용이 비어 있어요. 본문을 붙여넣어주세요."


def test_multipart_spool_threshold_covers_file_cap(client):
    from starlette.formparsers import MultiPartParser

    # 설치된 starlette(1.3.1)에서는 `max_file_size`가 아니라 `spool_max_size`가
    # SpooledTemporaryFile을 디스크로 넘기는 임계값을 제어한다.
    assert MultiPartParser.spool_max_size > main_mod.settings.max_file_bytes + 64 * 1024


def test_oversized_content_length_rejected_before_parse(client):
    resp = client.post(
        "/api/lint",
        headers={"content-length": str(200 * 1024 * 1024)},
        content=b"",
    )
    assert resp.status_code == 413


def test_chunked_body_without_content_length_rejected(client):
    resp = client.post(
        "/api/lint",
        headers={"transfer-encoding": "chunked"},
        content=iter([b"x" * 1024]),
    )
    assert resp.status_code == 411
