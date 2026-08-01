from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 로컬 실행 시 repo 루트의 .env를 읽는다 (없으면 조용히 무시).
# 이미 설정된 환경변수가 우선 — 배포 환경(Fly secrets)을 덮어쓰지 않는다.
load_dotenv()


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
    admin_token: str
    max_llm_calls: int
    llm_request_timeout: int
    convert_concurrency: int
    max_inflight_lint: int  # 비어 있으면 관리자 모드 비활성


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
        admin_token=os.environ.get("PLW_ADMIN_TOKEN", ""),
        # 요청당 LLM 호출 절대 상한 — 엔진 체커가 문서 헤딩 수만큼 호출하므로 필수
        max_llm_calls=int(os.environ.get("PLW_MAX_LLM_CALLS", "24")),
        llm_request_timeout=int(os.environ.get("PLW_LLM_REQUEST_TIMEOUT", "25")),
        convert_concurrency=int(os.environ.get("PLW_CONVERT_CONCURRENCY", "2")),
        max_inflight_lint=int(os.environ.get("PLW_MAX_INFLIGHT_LINT", "8")),
    )
