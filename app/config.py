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
    trust_proxy_headers: bool


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
        trust_proxy_headers=os.environ.get("PLW_TRUST_PROXY_HEADERS", "1") == "1",
    )
