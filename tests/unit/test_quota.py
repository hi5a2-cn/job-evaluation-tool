import concurrent.futures
from pathlib import Path
import pytest

from jet.config import Settings
from jet.db.store import connect, open_db, utc_now
from jet.llm.quota import finish, remaining_today, reserve


def _setup_db_with_limit(data_dir: Path, limit: int) -> int:
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "UPDATE user_settings SET daily_llm_limit = ? WHERE user_id = 'me'",
        (limit,),
    )
    # Dummy job, version, profile, judgement for FK
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_q', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Job', 1, 1, '深圳', 'hash_q', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'queued', '{}', ?)",
        (now_str,),
    )
    conn.close()
    return 1


def test_concurrent_quota_reservation(data_dir: Path):
    judgement_id = _setup_db_with_limit(data_dir, limit=2)

    def attempt_reserve():
        conn = connect(data_dir)
        try:
            return reserve(
                conn,
                user_id="me",
                judgement_id=judgement_id,
                provider="deepseek",
                model="deepseek-flash",
            )
        finally:
            conn.close()

    # 5 concurrent threads attempting to reserve when limit is 2
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_reserve) for _ in range(5)]
        results = [f.result() for f in futures]

    reserved_ids = [r for r in results if r is not None]
    assert len(reserved_ids) == 2
    assert len(set(reserved_ids)) == 2

    # Check database: exactly 2 billed=1 rows
    conn = connect(data_dir)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE user_id = 'me' AND billed = 1").fetchone()[0]
        assert cnt == 2
        assert remaining_today(conn, "me") == 0
    finally:
        conn.close()


def test_not_sent_refunds_quota(data_dir: Path):
    judgement_id = _setup_db_with_limit(data_dir, limit=1)
    conn = connect(data_dir)
    try:
        call_id = reserve(
            conn,
            user_id="me",
            judgement_id=judgement_id,
            provider="deepseek",
            model="deepseek-flash",
        )
        assert call_id is not None
        assert remaining_today(conn, "me") == 0

        # not_sent refunds quota (billed=0)
        finish(conn, call_id, outcome="not_sent")
        row = conn.execute("SELECT outcome, billed FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        assert row["outcome"] == "not_sent"
        assert row["billed"] == 0
        assert remaining_today(conn, "me") == 1
    finally:
        conn.close()


def test_timeout_and_errors_keep_billed(data_dir: Path):
    judgement_id = _setup_db_with_limit(data_dir, limit=5)
    conn = connect(data_dir)
    try:
        for err_outcome in ["timeout", "http_error", "parse_error"]:
            cid = reserve(
                conn,
                user_id="me",
                judgement_id=judgement_id,
                provider="deepseek",
                model="deepseek-flash",
            )
            assert cid is not None
            finish(conn, cid, outcome=err_outcome)
            row = conn.execute("SELECT outcome, billed FROM llm_calls WHERE id = ?", (cid,)).fetchone()
            assert row["outcome"] == err_outcome
            assert row["billed"] == 1
    finally:
        conn.close()


def test_zero_limit_does_not_reserve(data_dir: Path):
    judgement_id = _setup_db_with_limit(data_dir, limit=0)
    conn = connect(data_dir)
    try:
        assert remaining_today(conn, "me") == 0
        cid = reserve(
            conn,
            user_id="me",
            judgement_id=judgement_id,
            provider="deepseek",
            model="deepseek-flash",
        )
        assert cid is None
    finally:
        conn.close()
