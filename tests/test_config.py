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


def test_llm_model_defaults_to_engine_default(monkeypatch):
    monkeypatch.delenv("PLW_LLM_MODEL", raising=False)
    # 빈 값 → make_client(model=None) → 엔진 기본 모델을 쓴다
    assert load_settings().llm_model == ""


def test_llm_model_override(monkeypatch):
    monkeypatch.setenv("PLW_LLM_MODEL", "  some-model-id  ")
    assert load_settings().llm_model == "some-model-id"  # 앞뒤 공백 제거


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
