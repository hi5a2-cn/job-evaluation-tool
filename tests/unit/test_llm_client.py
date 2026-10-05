import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.llm.client import run_llm_judgement


@pytest.fixture
def test_setup(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_client_test', 'full', ?, ?)",
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


def test_llm_success_fit_and_truncate_reasons(test_setup):
    conn, settings, profile, job_version = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "verdict": "fit",
            "reasons": [
                "方向非常匹配候选人背景",
                "技能树包含FastAPI与高并发架构经验",
                "薪资与城市完全符合要求",
                "多余的第四条理由应该被截断",
            ],
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 40},
            },
        )

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

    assert outcome.status == "done"
    assert outcome.verdict == "fit"
    assert len(outcome.reasons) == 3
    assert outcome.reasons[0] == "方向非常匹配候选人背景"

    # Check llm_calls table recorded 'ok' and billed = 1
    call = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchone()
    assert call["outcome"] == "ok"
    assert call["billed"] == 1


def test_llm_invalid_json_format_fails(test_setup):
    conn, settings, profile, job_version = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not a valid json"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

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
    call = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchone()
    assert call["outcome"] == "parse_error"
    assert call["billed"] == 1  # parse_error is billed


def test_llm_timeout_retries_and_bills(test_setup):
    conn, settings, profile, job_version = test_setup
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Timeout", request=request)

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
    assert attempts == 3  # 1 initial + 2 retries
    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 3
    for c in calls:
        assert c["outcome"] == "timeout"
        assert c["billed"] == 1


def test_llm_500_retries_and_eventually_fails(test_setup):
    conn, settings, profile, job_version = test_setup
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": "server error"})

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
    assert attempts == 3
    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 3
    for c in calls:
        assert c["outcome"] == "http_error"
        assert c["billed"] == 1


def test_llm_connect_error_refunds_quota(test_setup):
    conn, settings, profile, job_version = test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

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
    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 3
    for c in calls:
        assert c["outcome"] == "not_sent"
        assert c["billed"] == 0  # not_sent refunds quota


def test_thinking_uses_large_max_tokens_and_empty_content_is_explained(test_setup):
    """开启思考：max_tokens 为 8192；返回空内容时错误写明 finish_reason 与截断原因，并记录用量（仍计费）。"""
    conn, settings, profile, job_version = test_setup
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["max_tokens"] = body["max_tokens"]
        seen["thinking"] = body["thinking"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "", "reasoning_content": "……"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 1200},
            },
        )

    think_settings = dataclasses.replace(settings, prompt_version="v4", judge_engine="deepseek-flash:think")
    outcome = run_llm_judgement(
        conn,
        think_settings,
        user_id="me",
        judgement_id=1,
        profile=profile,
        job_version=job_version,
        transport=httpx.MockTransport(handler),
        backoff_delays=(0, 0),
    )

    assert seen["thinking"] == {"type": "enabled"}
    assert seen["max_tokens"] == 8192
    assert outcome.status == "failed"
    assert "finish_reason=length" in outcome.error
    assert "截断" in outcome.error
    call = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchone()
    assert call["outcome"] == "parse_error"
    assert call["billed"] == 1
    assert call["input_tokens"] == 2000
    assert call["output_tokens"] == 1200


def test_no_think_keeps_small_max_tokens(test_setup):
    conn, settings, profile, job_version = test_setup
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["max_tokens"] = json.loads(request.content)["max_tokens"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}], "usage": {}})

    v4_settings = dataclasses.replace(settings, prompt_version="v4", judge_engine="deepseek-flash:no-think")
    run_llm_judgement(
        conn,
        v4_settings,
        user_id="me",
        judgement_id=1,
        profile=profile,
        job_version=job_version,
        transport=httpx.MockTransport(handler),
        backoff_delays=(0, 0),
    )
    assert seen["max_tokens"] == 1200


def test_v6_llm_judgement_injects_resume_profile(test_setup):
    """测试 v6 判断流程正确读取并注入简历画像，且不发送简历名称 (T017)"""
    from jet.domain.resume import save_resumes

    conn, settings, profile, job_version = test_setup

    save_resumes(
        conn,
        "me",
        [
            {"slot": 1, "name": "前端简历", "profile": "前端架构与性能优化"},
            {"slot": 2, "name": "全栈简历", "profile": "全栈开发与DevOps"},
        ],
    )

    seen_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_messages.extend(body["messages"])
        content = json.dumps({
            "facts": {
                "summary": {"text": "开发", "quotes": []},
                "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["匹配"],
            "verdict_reason": "符合",
            "hr_questions": [],
            "resume_suggestion": {"slot": 2, "reason": "契合全栈开发"},
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    v6_settings = dataclasses.replace(settings, prompt_version="v6", judge_engine="deepseek-flash:no-think")
    outcome = run_llm_judgement(
        conn,
        v6_settings,
        user_id="me",
        judgement_id=1,
        profile=profile,
        job_version=job_version,
        transport=httpx.MockTransport(handler),
        backoff_delays=(0, 0),
    )
    assert outcome.status == "done"
    assert outcome.resume_suggestion == {"slot": 2, "reason": "契合全栈开发"}

    sys_prompt = seen_messages[0]["content"]
    assert "- 简历1：前端架构与性能优化" in sys_prompt
    assert "- 简历2：全栈开发与DevOps" in sys_prompt
    assert "前端简历" not in sys_prompt
    assert "全栈简历" not in sys_prompt
