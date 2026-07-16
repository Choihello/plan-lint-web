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
