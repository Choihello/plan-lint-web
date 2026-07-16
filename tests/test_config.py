from app.config import load_settings


def test_defaults():
    s = load_settings()
    assert s.per_ip_daily == 1
    assert s.global_daily == 50
    assert s.max_file_bytes == 5 * 1024 * 1024
    assert s.max_text_chars == 100_000
    assert s.llm_timeout_seconds == 60
    assert s.llm_concurrency == 3
    assert s.trust_proxy_headers is True


def test_env_override(monkeypatch):
    monkeypatch.setenv("PLW_PER_IP_DAILY", "5")
    monkeypatch.setenv("PLW_GLOBAL_DAILY", "999")
    s = load_settings()
    assert s.per_ip_daily == 5
    assert s.global_daily == 999


def test_trust_proxy_headers_override(monkeypatch):
    monkeypatch.setenv("PLW_TRUST_PROXY_HEADERS", "0")
    s = load_settings()
    assert s.trust_proxy_headers is False
