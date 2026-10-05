"""体检第 71 条：额度按「用途 → 列名、兜底值」一张表计算，各用途互不影响。"""

from pathlib import Path

import pytest

from jet.db.store import open_db, utc_now
from jet.llm.quota import DAILY_LIMITS, remaining_today, reserve, usage_today

PURPOSES = ["judge", "assist", "prejudge", "resume_profile"]


def _set_limit(conn, purpose: str, value: int) -> None:
    column, _ = DAILY_LIMITS[purpose]
    conn.execute(f"UPDATE user_settings SET {column} = ? WHERE user_id = 'me'", (value,))


def _reserve(conn, purpose: str, **kw):
    return reserve(conn, user_id="me", provider="test", model="m", purpose=purpose, **kw)


@pytest.mark.parametrize("purpose", PURPOSES)
def test_each_purpose_uses_its_own_column_and_count(data_dir: Path, purpose: str):
    conn = open_db(data_dir)
    try:
        for p in PURPOSES:
            _set_limit(conn, p, 5)
        _set_limit(conn, purpose, 2)
        assert usage_today(conn, "me", purpose)["limit"] == 2

        assert _reserve(conn, purpose) is not None
        assert _reserve(conn, purpose) is not None
        assert _reserve(conn, purpose) is None  # 用完
        assert usage_today(conn, "me", purpose) | {"date": None} == {"date": None, "limit": 2, "used": 2, "remaining": 0}

        # 其他用途的计数不受影响
        for other in PURPOSES:
            if other != purpose:
                assert usage_today(conn, "me", other)["used"] == 0
                assert remaining_today(conn, "me", other) == 5
                assert _reserve(conn, other) is not None
    finally:
        conn.close()


def test_fallback_defaults_when_no_settings_row(data_dir: Path):
    conn = open_db(data_dir)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM user_settings WHERE user_id = 'me'")
        limits = {p: usage_today(conn, "me", p)["limit"] for p in PURPOSES}
        assert limits == {"judge": 150, "assist": 50, "prejudge": 20, "resume_profile": 10}
    finally:
        conn.close()


def test_zero_limit_blocks_reserve(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _set_limit(conn, "assist", 0)
        assert _reserve(conn, "assist") is None
        assert usage_today(conn, "me", "assist")["remaining"] == 0
    finally:
        conn.close()


def test_limit_override_wins(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _set_limit(conn, "judge", 100)
        assert _reserve(conn, "judge", limit_override=1) is not None
        assert _reserve(conn, "judge", limit_override=1) is None
    finally:
        conn.close()


def test_eval_counts_per_run_not_per_day(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _set_limit(conn, "judge", 0)  # 评测不看判断的每日上限
        assert _reserve(conn, "eval", eval_run_id="r1", limit_override=1) is not None
        assert _reserve(conn, "eval", eval_run_id="r1", limit_override=1) is None
        assert _reserve(conn, "eval", eval_run_id="r2", limit_override=1) is not None
    finally:
        conn.close()


def test_unknown_purpose_uses_judge_limit_and_count(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _set_limit(conn, "judge", 1)
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'judge', 't', 'm', ?, 'ok', 1)",
            (utc_now(),),
        )
        # 与原来的兜底分支一致：按 judge 的上限，并按 judge 计数
        assert reserve(conn, user_id="me", provider="t", model="m", purpose="something_else") is None
    finally:
        conn.close()
