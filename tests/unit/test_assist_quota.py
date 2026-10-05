"""Unit tests for assist quota configuration and reservation (T013).

Specs:
- specs/003-hr-assistant/spec.md (FR-037–FR-040, 指标定义表, R2)
- specs/003-hr-assistant/data-model.md (user_settings.daily_assist_limit, llm_calls)
- specs/003-hr-assistant/tasks.md (T013, T014)
- Constitution Principle VII: separate count, separate limit for judge and assist
"""

import concurrent.futures
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from jet.api.app import create_app
from jet.config import Settings, load_settings
from jet.db.store import connect, local_day_bounds_utc, open_db, utc_now
from jet.llm.quota import finish, remaining_today, reserve


def _setup_db_with_limits(data_dir: Path, judge_limit: int = 150, assist_limit: int = 50) -> None:
    conn = open_db(data_dir)
    conn.execute(
        "UPDATE user_settings SET daily_llm_limit = ?, daily_assist_limit = ? WHERE user_id = 'me'",
        (judge_limit, assist_limit),
    )
    conn.close()


# ---------------------------------------------------------------------------
# 1. Config loading & validation tests for JET_DAILY_ASSIST_LIMIT
# ---------------------------------------------------------------------------


def test_config_daily_assist_limit_default(tmp_path: Path):
    """JET_DAILY_ASSIST_LIMIT 默认值为 50。"""
    empty_dir = tmp_path / "empty_dir"
    settings = load_settings(data_dir=empty_dir, env={})
    assert settings.daily_assist_limit == 50


def test_config_daily_assist_limit_env_and_file(tmp_path: Path):
    """JET_DAILY_ASSIST_LIMIT 支持环境变量与 .env 文件加载，环境变量优先级更高。"""
    target_dir = tmp_path / "data_with_env"
    target_dir.mkdir()
    env_file = target_dir / ".env"
    env_file.write_text("JET_DAILY_ASSIST_LIMIT=80\n", encoding="utf-8")

    # 1. 从 .env 读取
    s1 = load_settings(data_dir=target_dir, env={})
    assert s1.daily_assist_limit == 80

    # 2. 环境变量优先于 .env
    s2 = load_settings(data_dir=target_dir, env={"JET_DAILY_ASSIST_LIMIT": "200"})
    assert s2.daily_assist_limit == 200


def test_config_daily_assist_limit_validation(data_dir: Path):
    """JET_DAILY_ASSIST_LIMIT 校验 0–500 的范围，非法值报错。"""
    data_dir.mkdir(parents=True, exist_ok=True)

    # 边界值 0 与 500 合法
    (data_dir / ".env").write_text("JET_DAILY_ASSIST_LIMIT=0\n", encoding="utf-8")
    assert load_settings(data_dir, env={}).daily_assist_limit == 0

    (data_dir / ".env").write_text("JET_DAILY_ASSIST_LIMIT=500\n", encoding="utf-8")
    assert load_settings(data_dir, env={}).daily_assist_limit == 500

    # 超出范围: < 0
    (data_dir / ".env").write_text("JET_DAILY_ASSIST_LIMIT=-1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0–500"):
        load_settings(data_dir, env={})

    # 超出范围: > 500
    (data_dir / ".env").write_text("JET_DAILY_ASSIST_LIMIT=501\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0–500"):
        load_settings(data_dir, env={})

    # 非整数
    (data_dir / ".env").write_text("JET_DAILY_ASSIST_LIMIT=abc\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0–500"):
        load_settings(data_dir, env={})


# ---------------------------------------------------------------------------
# 2. Quota reservation & counting tests for purpose='assist'
# ---------------------------------------------------------------------------


def test_assist_quota_counts_only_billed_and_assist(data_dir: Path):
    """assist 用途的计数只统计 purpose='assist' 且 billed=1。"""
    _setup_db_with_limits(data_dir, judge_limit=10, assist_limit=10)
    conn = connect(data_dir)
    try:
        now_str = utc_now()
        # 1. assist billed=1 (计入)
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now_str,),
        )
        # 2. assist billed=0 (退还，不计入)
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'assist', 'deepseek', 'deepseek-flash', ?, 'not_sent', 0)",
            (now_str,),
        )
        # 3. judge billed=1 (其他用途，不计入 assist)
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'judge', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now_str,),
        )
        # 4. eval billed=1 (其他用途，不计入 assist)
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'eval', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now_str,),
        )

        assert remaining_today(conn, "me", purpose="assist") == 9
    finally:
        conn.close()


def test_assist_quota_counts_only_local_day_today(data_dir: Path):
    """assist 用途按本机时区当天统计，昨天或过去的调用不计入今天。"""
    _setup_db_with_limits(data_dir, judge_limit=10, assist_limit=10)
    conn = connect(data_dir)
    try:
        start_utc_str, _, _ = local_day_bounds_utc()
        start_dt = datetime.fromisoformat(start_utc_str.replace("Z", "+00:00"))

        # 昨天（本地当天 0 点前 1 小时）
        yesterday_str = (start_dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 今天（本地当天 0 点后 1 小时）
        today_str = (start_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 昨天的调用
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (yesterday_str,),
        )
        # 今天的调用
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (today_str,),
        )

        # 昨天 1 次不计入今天，今天 1 次计入，剩余 9 次
        assert remaining_today(conn, "me", purpose="assist") == 9
    finally:
        conn.close()


def test_judge_and_assist_independent(data_dir: Path):
    """judge 与 assist 两种用途的次数互不影响（Constitution 原则 VII：单独计数、单独上限）。"""
    _setup_db_with_limits(data_dir, judge_limit=2, assist_limit=2)
    conn = connect(data_dir)
    try:
        # 耗尽 judge 额度 (2 次)
        j1 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="judge")
        j2 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="judge")
        assert j1 is not None and j2 is not None
        # judge 再次预占失败
        assert reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="judge") is None
        assert remaining_today(conn, "me", purpose="judge") == 0

        # assist 依然有完整 2 次额度，不受 judge 耗尽影响
        assert remaining_today(conn, "me", purpose="assist") == 2
        a1 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="assist")
        assert a1 is not None
        assert remaining_today(conn, "me", purpose="assist") == 1
        assert remaining_today(conn, "me", purpose="judge") == 0

        a2 = reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="assist")
        assert a2 is not None
        assert remaining_today(conn, "me", purpose="assist") == 0

        # assist 也耗尽
        assert reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="assist") is None
    finally:
        conn.close()


def test_assist_quota_exhaustion_and_concurrency(data_dir: Path):
    """到达上限后预占失败；多线程并发严格拦截。"""
    _setup_db_with_limits(data_dir, judge_limit=10, assist_limit=2)

    def attempt_reserve():
        conn = connect(data_dir)
        try:
            return reserve(
                conn,
                user_id="me",
                provider="deepseek",
                model="deepseek-flash",
                purpose="assist",
            )
        finally:
            conn.close()

    # 5 个并发线程竞争 2 个 assist 额度
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_reserve) for _ in range(5)]
        results = [f.result() for f in futures]

    reserved_ids = [r for r in results if r is not None]
    assert len(reserved_ids) == 2
    assert len(set(reserved_ids)) == 2

    # 数据库内严格只有 2 条 billed=1 的 assist 记录
    conn = connect(data_dir)
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE user_id = 'me' AND purpose = 'assist' AND billed = 1"
        ).fetchone()[0]
        assert cnt == 2
        assert remaining_today(conn, "me", purpose="assist") == 0

        # 后续调用直接返回 None
        assert reserve(conn, user_id="me", provider="deepseek", model="deepseek-flash", purpose="assist") is None
    finally:
        conn.close()


def test_failed_calls_count_towards_quota_if_billed(data_dir: Path):
    """已发出但失败且已计费的调用计入次数（R2）；not_sent 则退还额度。"""
    _setup_db_with_limits(data_dir, judge_limit=10, assist_limit=5)
    conn = connect(data_dir)
    try:
        # 1. 调用已发出，但发生超时/HTTP错误，billed=1，计入额度
        for err_outcome in ["timeout", "http_error", "parse_error", "llm_failed"]:
            cid = reserve(
                conn,
                user_id="me",
                provider="deepseek",
                model="deepseek-flash",
                purpose="assist",
            )
            assert cid is not None
            finish(conn, cid, outcome=err_outcome)
            row = conn.execute("SELECT outcome, billed FROM llm_calls WHERE id = ?", (cid,)).fetchone()
            assert row["outcome"] == err_outcome
            assert row["billed"] == 1

        # 4 次失败已调用，剩余 1 次
        assert remaining_today(conn, "me", purpose="assist") == 1

        # 2. 本地校验失败未发出 (not_sent)，billed=0，退还额度
        refund_cid = reserve(
            conn,
            user_id="me",
            provider="deepseek",
            model="deepseek-flash",
            purpose="assist",
        )
        assert refund_cid is not None
        assert remaining_today(conn, "me", purpose="assist") == 0

        finish(conn, refund_cid, outcome="not_sent")
        row = conn.execute("SELECT outcome, billed FROM llm_calls WHERE id = ?", (refund_cid,)).fetchone()
        assert row["outcome"] == "not_sent"
        assert row["billed"] == 0

        # 退还后依然剩余 1 次
        assert remaining_today(conn, "me", purpose="assist") == 1
    finally:
        conn.close()


def test_assist_zero_limit_does_not_reserve(data_dir: Path):
    """上限设为 0 时，立即拒绝预占。"""
    _setup_db_with_limits(data_dir, judge_limit=10, assist_limit=0)
    conn = connect(data_dir)
    try:
        assert remaining_today(conn, "me", purpose="assist") == 0
        cid = reserve(
            conn,
            user_id="me",
            provider="deepseek",
            model="deepseek-flash",
            purpose="assist",
        )
        assert cid is None
    finally:
        conn.close()


def test_app_startup_syncs_daily_assist_limit_to_db(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """应用启动时将 settings.daily_assist_limit 写入 user_settings 表。"""
    monkeypatch.setenv("JET_DAILY_ASSIST_LIMIT", "7")
    settings = load_settings(data_dir)
    assert settings.daily_assist_limit == 7

    app = create_app(settings, admin_secret="test-admin")
    with TestClient(app, client=("127.0.0.1", 50000)):
        conn = connect(data_dir)
        try:
            row = conn.execute("SELECT daily_assist_limit FROM user_settings WHERE user_id = 'me'").fetchone()
            assert row is not None
            assert row["daily_assist_limit"] == 7
        finally:
            conn.close()
