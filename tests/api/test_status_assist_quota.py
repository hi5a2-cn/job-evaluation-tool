"""Tests for assist_quota in /v1/status and prompt changes for position nature and answered questions."""

import socket
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from jet.db.store import local_day_bounds_utc, open_db
from jet.llm.assist import build_assist_prompt
from jet.llm.quota import remaining_today, usage_today


def test_status_returns_assist_quota_structure(paired_client: tuple[TestClient, dict[str, str]]):
    """GET /v1/status returns assist_quota with date, limit, used, remaining."""
    client, headers = paired_client
    resp = client.get("/v1/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert "quota" in data
    assert "assist_quota" in data

    start_utc, end_utc, date_str = local_day_bounds_utc()
    aq = data["assist_quota"]
    assert aq["date"] == date_str
    assert aq["limit"] == 50
    assert aq["used"] == 0
    assert aq["remaining"] == 50


def test_status_assist_quota_counting_rules(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """
    assist_quota used correctly counts only today's billed=1 assist calls:
    - purpose='assist' and billed=1 today -> counted
    - purpose='assist' and billed=0 today (e.g. not_sent) -> NOT counted
    - purpose='judge' and billed=1 today -> NOT counted in assist_quota (counted in quota)
    - purpose='assist' and billed=1 yesterday (cross-day) -> NOT counted
    - purpose='assist' and billed=1 tomorrow (cross-day) -> NOT counted
    """
    client, headers = paired_client
    start_utc, end_utc, date_str = local_day_bounds_utc()

    start_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    mid_today_str = (start_dt + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    yesterday_str = (start_dt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tomorrow_str = (end_dt + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = open_db(data_dir)
    try:
        # 1. 当日 assist 计费 (counted)
        conn.execute(
            "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
            "VALUES ('me', 'deepseek', 'deepseek-flash', 'assist', ?, 'ok', 1)",
            (mid_today_str,),
        )
        # 2. 当日 assist 非计费 (billed=0, not counted)
        conn.execute(
            "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
            "VALUES ('me', 'deepseek', 'deepseek-flash', 'assist', ?, 'not_sent', 0)",
            (mid_today_str,),
        )
        # 3. 当日 judge 计费 (not counted in assist_quota, counted in quota)
        conn.execute(
            "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
            "VALUES ('me', 'deepseek', 'deepseek-flash', 'judge', ?, 'ok', 1)",
            (mid_today_str,),
        )
        # 4. 跨日 (昨日) assist 计费 (not counted)
        conn.execute(
            "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
            "VALUES ('me', 'deepseek', 'deepseek-flash', 'assist', ?, 'ok', 1)",
            (yesterday_str,),
        )
        # 5. 跨日 (明日) assist 计费 (not counted)
        conn.execute(
            "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
            "VALUES ('me', 'deepseek', 'deepseek-flash', 'assist', ?, 'ok', 1)",
            (tomorrow_str,),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/v1/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # assist_quota: only the 1 billed assist call today counts
    assert data["assist_quota"]["used"] == 1
    assert data["assist_quota"]["limit"] == 50
    assert data["assist_quota"]["remaining"] == 49

    # quota (judge): only the 1 judge call counts
    assert data["quota"]["used"] == 1


def test_status_assist_quota_custom_limit_and_exhaustion(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """assist_quota respects user_settings.daily_assist_limit and remaining cannot be negative."""
    client, headers = paired_client
    start_utc, end_utc, _ = local_day_bounds_utc()

    conn = open_db(data_dir)
    try:
        conn.execute("UPDATE user_settings SET daily_assist_limit = 2 WHERE user_id = 'me'")
        # Insert 3 billed assist calls (exceeding limit 2)
        for _ in range(3):
            conn.execute(
                "INSERT INTO llm_calls (user_id, provider, model, purpose, started_at, outcome, billed) "
                "VALUES ('me', 'deepseek', 'deepseek-flash', 'assist', ?, 'ok', 1)",
                (start_utc,),
            )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/v1/status", headers=headers)
    assert resp.status_code == 200
    aq = resp.json()["assist_quota"]
    assert aq["limit"] == 2
    assert aq["used"] == 3
    assert aq["remaining"] == 0  # max(limit - used, 0)


def test_prompt_assembly_new_position_nature_and_answered_rules():
    """
    Prompt assembly verifies:
    - contains new position nature wording
    - does not contain old position nature wording
    - contains new answered questions wording
    - does not contain old answered questions wording
    """
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "在招的"},
    ]
    prompt = build_assist_prompt(
        job_title="测试助理",
        company_name="测试公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=[],
        tone_rules="1. 语气规则",
        judgement=None,
    )

    # (c) New position nature wording is present
    assert "如果聊天中还没弄清岗位的主要职责或是否含销售成分，请在问题中带上一个确认岗位性质的问题；聊天或 HR 实际情况中已经说明过，或岗位明显不是销售类的，不要再问。" in prompt
    assert "- 该岗位没有 Jet 适合度判断：只有聊天中尚未说明岗位主要职责或销售成分时，才加入一个确认岗位性质的问题；已说明过的不要再问。" in prompt

    # (c) Old wording is NOT present
    assert "请在建议中带上确认岗位性质（如主要职责、是否有销售指标/业绩考核等）的问题" not in prompt
    assert "- 该岗位没有 Jet 适合度判断，请在话术或问题中包含确认岗位性质（例如确认主要职责、是否有销售指标等）的内容。" not in prompt

    # (b) New answered questions wording is present
    assert "- 提出最多 3 个建议问 HR 的问题。提问前逐条核对聊天：HR 已经回答过的内容都视为已回答，不得再问，也不得换个说法再问，话术里同样不要再问。已回答包括：HR 用\"都有的\"\"是的\"\"可以\"\"对\"等简短词回应我的提问（表示我列出的选项都成立或得到肯定）；HR 主动说明过的内容（如职责、转正、晋升、薪资构成）。" in prompt

    # (b) Old wording is NOT present
    assert "- 提出最多 3 个建议问 HR 的问题，排除聊天中已经问过的或已有答案的问题。" not in prompt


def test_quota_usage_today_direct(data_dir: Path):
    """Direct test for usage_today in quota.py for judge and assist."""
    start_utc, end_utc, date_str = local_day_bounds_utc()
    conn = open_db(data_dir)
    try:
        judge_usage = usage_today(conn, "me", purpose="judge")
        assert judge_usage == {
            "date": date_str,
            "limit": 150,
            "used": 0,
            "remaining": 150,
        }
        assist_usage = usage_today(conn, "me", purpose="assist")
        assert assist_usage == {
            "date": date_str,
            "limit": 50,
            "used": 0,
            "remaining": 50,
        }
        assert remaining_today(conn, "me", purpose="judge") == 150
        assert remaining_today(conn, "me", purpose="assist") == 50
    finally:
        conn.close()
