from pathlib import Path
import sqlite3
import pytest

from jet.db.store import open_db, utc_now
from jet.llm.quota import finish, remaining_today, reserve, usage_today


def test_resume_profile_quota_default_and_usage(data_dir: Path):
    """测试简历画像每日配额默认 10 次，调用后正确扣减 (T002, T003)。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 1. 初始状态：limit=10, used=0, remaining=10
    usage = usage_today(conn, user_id, purpose="resume_profile")
    assert usage["limit"] == 10
    assert usage["used"] == 0
    assert usage["remaining"] == 10
    assert remaining_today(conn, user_id, purpose="resume_profile") == 10

    # 2. 预占一次配额
    call_id = reserve(
        conn,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose="resume_profile",
    )
    assert call_id is not None

    # 验证 llm_calls 中记录
    row = conn.execute("SELECT purpose, outcome, billed FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    assert row["purpose"] == "resume_profile"
    assert row["outcome"] == "reserved"
    assert row["billed"] == 1

    finish(conn, call_id, outcome="ok")

    # 验证已用 1 次，剩余 9 次
    usage_after = usage_today(conn, user_id, purpose="resume_profile")
    assert usage_after["used"] == 1
    assert usage_after["remaining"] == 9
    assert remaining_today(conn, user_id, purpose="resume_profile") == 9

    conn.close()


def test_resume_profile_quota_exhaustion(data_dir: Path):
    """测试用尽配额后 reserve 返回 None 拦截调用 (T002, T003)。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 将配额设为 3
    conn.execute(
        "UPDATE user_settings SET daily_resume_profile_limit = 3 WHERE user_id = ?",
        (user_id,),
    )

    # 成功预占 3 次
    for _ in range(3):
        cid = reserve(
            conn,
            user_id=user_id,
            provider="deepseek",
            model="deepseek-flash",
            purpose="resume_profile",
        )
        assert cid is not None
        finish(conn, cid, outcome="ok")

    assert remaining_today(conn, user_id, purpose="resume_profile") == 0

    # 第 4 次预占失败，返回 None
    cid_blocked = reserve(
        conn,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose="resume_profile",
    )
    assert cid_blocked is None
    conn.close()


def test_resume_profile_quota_isolated_from_judge_and_assist(data_dir: Path):
    """测试 resume_profile 配额与 judge、assist、prejudge 完全隔离 (原则 VII)。"""
    conn = open_db(data_dir)
    user_id = "me"

    # 初始 judge 剩余 150，assist 50，prejudge 20，resume_profile 剩余 10
    assert remaining_today(conn, user_id, purpose="judge") == 150
    assert remaining_today(conn, user_id, purpose="assist") == 50
    assert remaining_today(conn, user_id, purpose="prejudge") == 20
    assert remaining_today(conn, user_id, purpose="resume_profile") == 10

    # 消耗 1 次 resume_profile
    cid = reserve(
        conn,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose="resume_profile",
    )
    assert cid is not None
    finish(conn, cid, outcome="ok")

    # 验证只有 resume_profile 扣减，其他三项丝毫不受影响
    assert remaining_today(conn, user_id, purpose="resume_profile") == 9
    assert remaining_today(conn, user_id, purpose="judge") == 150
    assert remaining_today(conn, user_id, purpose="assist") == 50
    assert remaining_today(conn, user_id, purpose="prejudge") == 20

    # 消耗 1 次 judge
    cid_judge = reserve(
        conn,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose="judge",
    )
    assert cid_judge is not None
    finish(conn, cid_judge, outcome="ok")

    # 验证 judge 扣减，resume_profile 仍为 9
    assert remaining_today(conn, user_id, purpose="judge") == 149
    assert remaining_today(conn, user_id, purpose="resume_profile") == 9

    conn.close()


def test_resume_profile_zero_limit_blocks(data_dir: Path):
    """测试配额设为 0 时立即阻断 (T002)。"""
    conn = open_db(data_dir)
    user_id = "me"

    conn.execute(
        "UPDATE user_settings SET daily_resume_profile_limit = 0 WHERE user_id = ?",
        (user_id,),
    )
    assert remaining_today(conn, user_id, purpose="resume_profile") == 0
    cid = reserve(
        conn,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose="resume_profile",
    )
    assert cid is None
    conn.close()
