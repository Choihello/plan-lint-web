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
        # 다른 연결이 쓰기 중이면 즉시 실패하지 않고 기다린다
        self._conn.execute("PRAGMA busy_timeout = 5000")
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

    def _counts(self, ip: str, today: str) -> tuple[int, int]:
        """(이 IP의 오늘 사용량, 전역 오늘 사용량). 읽기 전용 — 쓰기 트랜잭션을 열지 않는다.

        예전에는 여기서 지난 날짜 행을 DELETE했는데, 조회만 하는 remaining()에서도
        미커밋 쓰기 트랜잭션이 열린 채 남아 다른 연결이 'database is locked'로 실패했다.
        정리는 쓰기 경로(try_consume)에서 커밋과 함께 수행한다.
        """
        # 관리자 카운터는 별도 축이므로 공개 전역 합계에서 제외한다
        total = self._conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM usage WHERE date = ? AND ip_hash != ?",
            (today, self.ADMIN_KEY),
        ).fetchone()[0]
        row = self._conn.execute(
            "SELECT count FROM usage WHERE ip_hash = ? AND date = ?", (self._hash(ip), today)
        ).fetchone()
        return (row[0] if row else 0, total)

    def try_consume(self, ip: str) -> str | None:
        with self._lock:
            today = self._today()
            self._conn.execute("DELETE FROM usage WHERE date != ?", (today,))  # 커밋은 아래에서
            used, total = self._counts(ip, today)
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

    # 관리자용 카운터는 IP가 없으므로 해시 대신 예약 키를 쓴다.
    # 공개 사용자 전역 캡과 분리된 축이라, 관리자 사용이 일반 쿼터를 잠식하지 않는다.
    ADMIN_KEY = "__admin__"

    def try_consume_admin(self, cap: int) -> str | None:
        """관리자 일일 상한. 토큰이 유출돼도 무제한 유료 호출이 되지 않게 한다."""
        with self._lock:
            today = self._today()
            self._conn.execute("DELETE FROM usage WHERE date != ?", (today,))
            row = self._conn.execute(
                "SELECT count FROM usage WHERE ip_hash = ? AND date = ?", (self.ADMIN_KEY, today)
            ).fetchone()
            if (row[0] if row else 0) >= cap:
                self._conn.commit()
                return "quota_admin"
            self._conn.execute(
                "INSERT INTO usage (ip_hash, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(ip_hash, date) DO UPDATE SET count = count + 1",
                (self.ADMIN_KEY, today),
            )
            self._conn.commit()
            return None

    def remaining_admin(self, cap: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE ip_hash = ? AND date = ?",
                (self.ADMIN_KEY, self._today()),
            ).fetchone()
            return max(0, cap - (row[0] if row else 0))

    def refund_admin(self) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE usage SET count = MAX(count - 1, 0) WHERE ip_hash = ? AND date = ?",
                (self.ADMIN_KEY, self._today()),
            )
            self._conn.commit()

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
            used, total = self._counts(ip, self._today())
            return max(0, min(self._per_ip - used, self._global - total))
