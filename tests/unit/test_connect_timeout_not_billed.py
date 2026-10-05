import dataclasses
from pathlib import Path
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.llm.client import run_llm_judgement
from jet.llm.quota import remaining_today


@pytest.fixture
def test_setup(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_connect_timeout_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python工程师', '15-25K', 1, 15.0, 25.0, 12, 1, '深圳', '后端研发', 'h1', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[\"FastAPI\"]', '[\"深圳\"]', 15.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'queued', '{}', ?)",
        (now_str,),
    )

    profile = {
        "id": 1,
        "user_id": "me",
        "version_no": 1,
        "directions": ["Python"],
        "keywords": ["FastAPI"],
        "cities": ["深圳"],
        "min_monthly_k": 15.0,
        "exclude_keywords": [],
    }
    job_version = conn.execute("SELECT * FROM job_versions WHERE id = 1").fetchone()
    active_settings = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v1")

    yield conn, active_settings, profile, job_version
    conn.close()


def test_connect_timeout_is_not_sent_and_not_billed(test_setup):
    """连接阶段超时转为 NotSent，不扣除额度，outcome 为 not_sent，billed 为 0。"""
    conn, settings, profile, job_version = test_setup
    quota_before = remaining_today(conn, "me")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Mock connection timeout", request=request)

    transport = httpx.MockTransport(handler)
    outcome = run_llm_judgement(
        conn,
        settings,
        user_id="me",
        judgement_id=1,
        profile=profile,
        job_version=job_version,
        transport=transport,
        backoff_delays=(0, 0),
    )

    assert outcome.status == "failed"
    assert "连接超时" in outcome.error

    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 3
    for c in calls:
        assert c["outcome"] == "not_sent"
        assert c["billed"] == 0

    quota_after = remaining_today(conn, "me")
    assert quota_after == quota_before


def test_read_timeout_remains_timeout_and_is_billed(test_setup):
    """读响应阶段超时仍按 Timeout 计费，outcome 为 timeout，billed 为 1。"""
    conn, settings, profile, job_version = test_setup
    quota_before = remaining_today(conn, "me")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Mock read timeout", request=request)

    transport = httpx.MockTransport(handler)
    outcome = run_llm_judgement(
        conn,
        settings,
        user_id="me",
        judgement_id=1,
        profile=profile,
        job_version=job_version,
        transport=transport,
        backoff_delays=(0, 0),
    )

    assert outcome.status == "failed"
    assert "超时" in outcome.error

    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 3
    for c in calls:
        assert c["outcome"] == "timeout"
        assert c["billed"] == 1

    quota_after = remaining_today(conn, "me")
    assert quota_after == quota_before - 3
