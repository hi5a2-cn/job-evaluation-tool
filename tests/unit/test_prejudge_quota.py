from pathlib import Path
import pytest

from jet.db.store import connect, open_db, utc_now
from jet.llm.quota import finish, remaining_today, reserve, usage_today


def _setup_db_with_limits(
    data_dir: Path,
    daily_llm_limit: int = 150,
    daily_assist_limit: int = 50,
    daily_prejudge_limit: int = 20,
) -> None:
    conn = open_db(data_dir)
    conn.execute(
        "UPDATE user_settings SET daily_llm_limit = ?, daily_assist_limit = ?, daily_prejudge_limit = ? WHERE user_id = 'me'",
        (daily_llm_limit, daily_assist_limit, daily_prejudge_limit),
    )
    conn.close()


def test_prejudge_quota_isolation(data_dir: Path):
    _setup_db_with_limits(data_dir, daily_llm_limit=10, daily_assist_limit=5, daily_prejudge_limit=2)
    conn = connect(data_dir)
    try:
        # Initially 2 remaining for prejudge
        assert remaining_today(conn, user_id="me", purpose="prejudge") == 2
        assert usage_today(conn, user_id="me", purpose="prejudge")["used"] == 0

        # Reserve 1 prejudge
        res1 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="prejudge")
        assert res1 is not None
        call_id1 = res1

        # Check remaining
        assert remaining_today(conn, user_id="me", purpose="prejudge") == 1
        assert usage_today(conn, user_id="me", purpose="prejudge")["used"] == 1

        # Judge and assist remain untouched
        assert remaining_today(conn, user_id="me", purpose="judge") == 10
        assert remaining_today(conn, user_id="me", purpose="assist") == 5

        # Reserve 2nd prejudge
        res2 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="prejudge")
        assert res2 is not None
        call_id2 = res2

        # Now prejudge is exhausted
        assert remaining_today(conn, user_id="me", purpose="prejudge") == 0
        res3 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="prejudge")
        assert res3 is None

        # But judge can still reserve
        res_judge = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="judge")
        assert res_judge is not None
    finally:
        conn.close()


def test_prejudge_quota_zero_limit(data_dir: Path):
    _setup_db_with_limits(data_dir, daily_prejudge_limit=0)
    conn = connect(data_dir)
    try:
        assert remaining_today(conn, user_id="me", purpose="prejudge") == 0
        res = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="prejudge")
        assert res is None
        assert usage_today(conn, user_id="me", purpose="prejudge")["used"] == 0
    finally:
        conn.close()


def test_prejudge_quota_finish(data_dir: Path):
    _setup_db_with_limits(data_dir, daily_prejudge_limit=5)
    conn = connect(data_dir)
    try:
        res = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="prejudge")
        assert res is not None
        call_id = res

        finish(
            conn,
            call_id=call_id,
            outcome="ok",
            usage={
                "prompt_tokens": 400,
                "completion_tokens": 50,
            },
        )

        row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
        assert row["purpose"] == "prejudge"
        assert row["outcome"] == "ok"
        assert row["input_tokens"] == 400
        assert row["output_tokens"] == 50
        assert row["billed"] == 1
    finally:
        conn.close()
