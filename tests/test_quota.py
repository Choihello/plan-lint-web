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
