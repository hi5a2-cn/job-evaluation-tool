from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from jet.db.migrations import LATEST_VERSION
from jet.db.store import (
    count_ownerless_rows,
    local_day_bounds_utc,
    open_db,
    reset_running_to_interrupted,
    transaction,
    utc_now,
)


def test_open_db_initialization(data_dir: Path):
    conn = open_db(data_dir)

    # 1. PRAGMAs
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal.lower() == "wal"

    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1

    # 2. Tables exist
    tables = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "job_chat_seen",
    ]
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in cur.fetchall()}
    for tbl in tables:
        assert tbl in existing_tables

    # 3. Default user 'me' and user_settings exist
    u = conn.execute("SELECT id FROM users WHERE id = 'me'").fetchone()
    assert u is not None

    s = conn.execute("SELECT daily_llm_limit FROM user_settings WHERE user_id = 'me'").fetchone()
    assert s is not None
    assert s["daily_llm_limit"] == 150

    # 4. Zero ownerless rows initially
    assert count_ownerless_rows(conn) == 0

    conn.close()


def test_count_ownerless_rows(data_dir: Path):
    conn = open_db(data_dir)

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('ghost', 999, 'list', ?)",
        (utc_now(),),
    )
    assert count_ownerless_rows(conn) == 1

    conn.execute("DELETE FROM views WHERE user_id = 'ghost'")
    assert count_ownerless_rows(conn) == 0
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()


def test_reset_running_to_interrupted(data_dir: Path):
    conn = open_db(data_dir)
    now_str = utc_now()

    # Create dummy job, version, profile
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job1', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', 'hash1', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', ?)",
        (now_str,),
    )

    # Insert a running judgement
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'running', '{}', ?)",
        (now_str,),
    )

    count = reset_running_to_interrupted(conn)
    assert count == 1

    row = conn.execute("SELECT status FROM judgements WHERE id = 1").fetchone()
    assert row["status"] == "interrupted"

    conn.close()


def test_partial_unique_index_on_judgements(data_dir: Path):
    conn = open_db(data_dir)
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (10, 'boss', 'job10', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (10, 10, 1, 'detail', 'Dev', 1, 1, '北京', 'hash10', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (10, 'me', 1, '[]', '[]', '[]', '[]', ?)",
        (now_str,),
    )

    # First active judgement (superseded_by IS NULL)
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at, superseded_by) "
        "VALUES (10, 'me', 10, 10, 10, 'queued', '{}', ?, NULL)",
        (now_str,),
    )

    # Inserting duplicate active judgement must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at, superseded_by) "
            "VALUES (11, 'me', 10, 10, 10, 'queued', '{}', ?, NULL)",
            (now_str,),
        )

    # Supersede the first judgement and insert the new one in one transaction
    # (superseded_by FK is DEFERRABLE INITIALLY DEFERRED)
    with transaction(conn):
        conn.execute("UPDATE judgements SET superseded_by = 12 WHERE id = 10")
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at, superseded_by) "
            "VALUES (12, 'me', 10, 10, 10, 'queued', '{}', ?, NULL)",
            (now_str,),
        )
    assert conn.execute("SELECT superseded_by FROM judgements WHERE id = 10").fetchone()[0] == 12

    # Superseding with a dangling id is rejected at commit
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("UPDATE judgements SET superseded_by = 999 WHERE id = 12")

    conn.close()


def test_check_constraints(data_dir: Path):
    conn = open_db(data_dir)
    now_str = utc_now()

    # 1. completeness check
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
            "VALUES ('boss', 'p1', 'unknown_type', ?, ?)",
            (now_str, now_str),
        )

    # 2. daily_llm_limit check (0 - 500)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE user_settings SET daily_llm_limit = -1 WHERE user_id = 'me'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE user_settings SET daily_llm_limit = 501 WHERE user_id = 'me'")

    # Valid value
    conn.execute("UPDATE user_settings SET daily_llm_limit = 500 WHERE user_id = 'me'")
    conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")

    # 3. NOT NULL user_id on views
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES (NULL, 1, 'list', ?)",
            (now_str,),
        )

    conn.close()


def test_local_day_bounds_utc():
    start_iso, end_iso, date_str = local_day_bounds_utc()
    assert start_iso < end_iso
    assert len(date_str) == 10  # YYYY-MM-DD

    dt_start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    dt_end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    assert (dt_end - dt_start).total_seconds() == 86400


def test_local_day_bounds_utc_uses_local_date_for_given_now(monkeypatch):
    """体检第 83 条合并了两个相同分支：传入带时区或不带时区的时间，都按本机时区取当天。"""
    import time

    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    try:
        expected = ("2026-10-05T16:00:00Z", "2026-10-06T16:00:00Z", "2026-10-06")
        # UTC 10/5 20:00 = 上海 10/6 04:00
        assert local_day_bounds_utc(datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)) == expected
        # 不带时区的时间按本机时间理解
        assert local_day_bounds_utc(datetime(2026, 10, 6, 4, 0)) == expected
    finally:
        monkeypatch.undo()
        time.tzset()


def test_transaction_context_manager(data_dir: Path):
    conn = open_db(data_dir)

    # 1. Success commits
    with transaction(conn):
        conn.execute("UPDATE user_settings SET daily_llm_limit = 123 WHERE user_id = 'me'")

    row = conn.execute("SELECT daily_llm_limit FROM user_settings WHERE user_id = 'me'").fetchone()
    assert row["daily_llm_limit"] == 123

    # 2. Exception rolls back
    with pytest.raises(ValueError):
        with transaction(conn):
            conn.execute("UPDATE user_settings SET daily_llm_limit = 456 WHERE user_id = 'me'")
            raise ValueError("Rollback please")

    row = conn.execute("SELECT daily_llm_limit FROM user_settings WHERE user_id = 'me'").fetchone()
    assert row["daily_llm_limit"] == 123

    conn.close()


def test_job_chat_seen_created_in_existing_db(tmp_path: Path):
    """T003: 验证已有的当前版本数据库（无 job_chat_seen 表）在 init_db 后补建 job_chat_seen 表，不升版本。"""
    from jet.db.store import init_db, open_db

    # 1. 先初始化一个完整的数据库，然后手动 DROP TABLE job_chat_seen，模拟新增表发布前的既有数据库
    conn = open_db(tmp_path)
    conn.execute("DROP TABLE IF EXISTS job_chat_seen")
    # 验证此时已无 job_chat_seen
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_chat_seen'").fetchone() is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    conn.close()

    # 2. 重新执行 init_db，应该自动补建 schema 中的 job_chat_seen 表
    init_db(tmp_path)

    conn = sqlite3.connect(str(tmp_path / "jet.db"))
    # 验证 job_chat_seen 表存在
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_chat_seen'")
    assert cur.fetchone() is not None

    # 验证字段定义
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(job_chat_seen)").fetchall()}
    assert cols["user_id"] == "TEXT"
    assert cols["job_id"] == "INTEGER"
    assert cols["chat_title"] == "TEXT"
    assert cols["first_seen_at"] == "TEXT"
    assert cols["last_seen_at"] == "TEXT"

    # 验证主键为 (user_id, job_id)
    pk_cols = [
        r[1]
        for r in sorted(
            conn.execute("PRAGMA table_info(job_chat_seen)").fetchall(),
            key=lambda r: r[5],
        )
        if r[5] > 0
    ]
    assert pk_cols == ["user_id", "job_id"]

    # 验证 user_version 保持不变
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    conn.close()


def test_job_chat_seen_ownerless_check(data_dir: Path):
    """T003: 验证 count_ownerless_rows 正确统计 job_chat_seen 中的无主记录。"""
    conn = open_db(data_dir)
    assert count_ownerless_rows(conn) == 0

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO job_chat_seen (user_id, job_id, chat_title, first_seen_at, last_seen_at) "
        "VALUES ('ghost_user', 9999, '测试职位', ?, ?)",
        (utc_now(), utc_now()),
    )
    assert count_ownerless_rows(conn) == 1

    conn.execute("DELETE FROM job_chat_seen WHERE user_id = 'ghost_user'")
    assert count_ownerless_rows(conn) == 0
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
