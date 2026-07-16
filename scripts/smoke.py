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
    q.raise_for_status()
    assert "remaining_today" in q.json()
    print(f"OK — 결함 {len(body['findings'])}건 검출, quota 응답 정상")
    return 0


if __name__ == "__main__":
    # Windows cp949 콘솔에서 한국어·기호 출력이 깨지지 않게 UTF-8로 강제 (CLI와 동일 패턴)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    if len(sys.argv) < 2:
        print("사용법: python scripts/smoke.py <배포 URL>")
        sys.exit(2)
    sys.exit(main(sys.argv[1].rstrip("/")))
