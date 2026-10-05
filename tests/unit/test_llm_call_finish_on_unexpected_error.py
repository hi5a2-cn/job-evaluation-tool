import dataclasses
import json
from pathlib import Path
import sqlite3
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.llm import prompt_v5
from jet.llm.client import run_llm_judgement
from tests.conftest import FakeLlmHelper


@pytest.fixture
def c1_test_setup(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_c1_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python工程师', '15-25K', 1, 15.0, 25.0, 12, 1, '深圳', '后端研发架构与微服务', 'h1', ?)",
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
    active_settings = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v5")

    yield conn, active_settings, profile, job_version
    conn.close()


def test_c1_run_llm_judgement_annotate_facts_error_finishes_failed(
    c1_test_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    """C1: 后处理 annotate_facts 抛出异常时，run_llm_judgement 将记录 finish 为 failed 并重新抛出。"""
    conn, settings, profile, job_version = c1_test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "职责概括", "quotes": []},
                "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["方向匹配", "薪资符合预期"],
            "verdict_reason": "各项条件均符合画像要求",
            "hr_questions": [],
        }, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 50},
            },
        )

    transport = httpx.MockTransport(handler)

    # 打桩点: jet.llm.client 内部引用的 annotate_facts 抛出 RuntimeError
    def mock_annotate_facts(facts: dict[str, Any], desc: str) -> dict[str, Any]:
        raise RuntimeError("Mock unexpected error during annotate_facts")

    monkeypatch.setattr("jet.llm.client.annotate_facts", mock_annotate_facts)

    with pytest.raises(RuntimeError, match="Mock unexpected error during annotate_facts"):
        run_llm_judgement(
            conn,
            settings,
            user_id="me",
            judgement_id=1,
            profile=profile,
            job_version=job_version,
            transport=transport,
            backoff_delays=(0, 0),
        )

    # 断言 llm_calls 里记录 outcome='failed'、billed=1、不是 'reserved'
    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 1
    call = calls[0]
    assert call["outcome"] == "failed"
    assert call["billed"] == 1
    assert call["outcome"] != "reserved"

    # 断言没有任何 outcome='reserved' 的残留记录
    reserved = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE outcome = 'reserved'").fetchone()[0]
    assert reserved == 0


def test_c1_run_llm_judgement_apply_cap_error_finishes_failed(
    c1_test_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    """C1 附加打桩点: 后处理 apply_city_salary_cap 抛出异常时，同样 finish 为 failed 并重新抛出。"""
    conn, settings, profile, job_version = c1_test_setup

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "职责概括", "quotes": []},
                "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["方向匹配", "薪资符合预期"],
            "verdict_reason": "各项条件均符合画像要求",
            "hr_questions": [],
        }, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 50},
            },
        )

    transport = httpx.MockTransport(handler)

    # 打桩点: prompt_v5 模块里的 apply_city_salary_cap 抛出 RuntimeError
    def mock_apply_cap(verdict, derivation, prof, jv):
        raise RuntimeError("Mock unexpected error during apply_city_salary_cap")

    monkeypatch.setattr(prompt_v5, "apply_city_salary_cap", mock_apply_cap)

    with pytest.raises(RuntimeError, match="Mock unexpected error during apply_city_salary_cap"):
        run_llm_judgement(
            conn,
            settings,
            user_id="me",
            judgement_id=1,
            profile=profile,
            job_version=job_version,
            transport=transport,
            backoff_delays=(0, 0),
        )

    calls = conn.execute("SELECT * FROM llm_calls WHERE judgement_id = 1").fetchall()
    assert len(calls) == 1
    call = calls[0]
    assert call["outcome"] == "failed"
    assert call["billed"] == 1
    assert call["outcome"] != "reserved"

    # 没有任何 outcome='reserved' 的残留记录
    reserved = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE outcome = 'reserved'").fetchone()[0]
    assert reserved == 0


def test_c2_chat_generate_unexpected_error_finishes_failed(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """C2: /v1/chat/generate 中 generate_assist_suggestions 抛意外异常时 finish 为 failed 并返回 502。"""
    client, headers = llm_client

    # 1. 完成数据知情同意
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-c2-unexpected",
        "job_title": "储能海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "在招的，方便发份简历吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # 2. 获取 preview 与合法 prompt_hash
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prompt_hash = prev_resp.json()["prompt_hash"]

    # 3. 打桩点: routes 模块里的 generate_assist_suggestions 抛 RuntimeError
    def mock_generate_assist(*args, **kwargs):
        raise RuntimeError("Mock unexpected crash in generate_assist_suggestions")

    monkeypatch.setattr("jet.api.routes.generate_assist_suggestions", mock_generate_assist)

    # 4. 发起 generate 请求
    gen_resp = client.post(
        "/v1/chat/generate",
        json={**payload, "prompt_hash": prompt_hash},
        headers=headers,
    )
    assert gen_resp.status_code == 502
    assert gen_resp.json()["error"] == "llm_failed"

    # 5. 断言 llm_calls 最新一条 purpose='assist' 的 outcome='failed'，billed=1
    conn = open_db(data_dir)
    try:
        call = conn.execute(
            "SELECT outcome, billed, purpose FROM llm_calls WHERE purpose = 'assist' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert call is not None
        assert call["outcome"] == "failed"
        assert call["billed"] == 1
        assert call["outcome"] != "reserved"

        # 没有任何 outcome='reserved' 的残留记录
        reserved = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE outcome = 'reserved'").fetchone()[0]
        assert reserved == 0
        assert conn.execute("SELECT COUNT(*) FROM prejudgements").fetchone()[0] == 0
    finally:
        conn.close()


def test_c3_prejudge_parse_response_error_finishes_failed(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    fake_llm: FakeLlmHelper,
    monkeypatch: pytest.MonkeyPatch,
):
    """C3: 列表预判中 parse_prejudge_response 抛意外异常时 finish 为 failed 并返回 status='failed'。"""
    client, headers = llm_client

    # 1. 准备画像与岗位数据
    conn = open_db(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, excluded_cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[\"FastAPI\"]', '[\"深圳\"]', '[\"北京\"]', '[\"外包\"]', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_c3_parse_1', '科技公司A', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES (2, 'boss', 'job_c3_parse_2', '销售公司B', 'full', ?, ?)",
        (now, now),
    )
    conn.close()

    # 2. fake_llm 正常返回可被解析的 JSON
    fake_llm.set_prejudge([
        {"id": "1", "level": "open", "reason": "薪资契合"},
        {"id": "2", "level": "skip", "reason": "方向冲突"},
    ])

    # 3. 打桩点: routes 模块里的 parse_prejudge_response 抛 RuntimeError
    def mock_parse_prejudge(*args, **kwargs):
        raise RuntimeError("Mock unexpected error in parse_prejudge_response")

    monkeypatch.setattr("jet.api.routes.parse_prejudge_response", mock_parse_prejudge)

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_c3_parse_1",
                "title": "Python 后端开发",
                "company_name": "科技公司A",
                "salary_raw": "20-30K",
            },
            {
                "platform_job_id": "job_c3_parse_2",
                "title": "电话销售代表",
                "company_name": "销售公司B",
            },
        ]
    }

    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 断言返回 status='failed'，且返回的 prejudgements 里没有它们
    assert data["status"] == "failed"
    assert "job_c3_parse_1" not in data["prejudgements"]
    assert "job_c3_parse_2" not in data["prejudgements"]
    assert len(data["prejudgements"]) == 0

    # 4. 断言数据库状态
    conn = open_db(data_dir)
    try:
        # prejudgements 表里没有这批岗位的新行
        pjs = conn.execute("SELECT * FROM prejudgements").fetchall()
        assert len(pjs) == 0

        # llm_calls 最新一条 purpose='prejudge' 的 outcome='failed'，billed=1
        call = conn.execute(
            "SELECT outcome, billed, purpose FROM llm_calls WHERE purpose = 'prejudge' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert call is not None
        assert call["outcome"] == "failed"
        assert call["billed"] == 1
        assert call["outcome"] != "reserved"

        # 没有任何 outcome='reserved' 的残留记录
        reserved = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE outcome = 'reserved'").fetchone()[0]
        assert reserved == 0
        assert conn.execute("SELECT COUNT(*) FROM prejudgements").fetchone()[0] == 0
    finally:
        conn.close()


def test_c3_prejudge_db_insert_error_finishes_failed(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    fake_llm: FakeLlmHelper,
):
    """C3: 列表预判在入库过程中发生数据库异常时，事务回滚且 finish 为 failed，返回 status='failed'。"""
    client, headers = llm_client

    # 1. 准备画像与岗位数据
    conn = open_db(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, excluded_cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[\"FastAPI\"]', '[\"深圳\"]', '[\"北京\"]', '[\"外包\"]', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_c3_db_1', '科技公司A', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES (2, 'boss', 'job_c3_db_2', '科技公司B', 'full', ?, ?)",
        (now, now),
    )
    # 打桩点: 第 1 个岗位正常写入，第 2 个岗位写入时触发器抛错，整个事务回滚
    conn.execute(
        "CREATE TRIGGER fail_prejudge_insert BEFORE INSERT ON prejudgements "
        "WHEN NEW.job_id = 2 "
        "BEGIN SELECT RAISE(ABORT, 'Mock database insert failure'); END;"
    )
    conn.close()

    fake_llm.set_prejudge([
        {"id": "1", "level": "open", "reason": "薪资契合"},
        {"id": "2", "level": "open", "reason": "方向契合"},
    ])

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_c3_db_1",
                "title": "Python 后端开发",
                "company_name": "科技公司A",
                "salary_raw": "20-30K",
            },
            {
                "platform_job_id": "job_c3_db_2",
                "title": "Python 数据开发",
                "company_name": "科技公司B",
                "salary_raw": "20-30K",
            },
        ]
    }

    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 断言返回 status='failed'，且返回的 prejudgements 里没有它们
    assert data["status"] == "failed"
    # 第 1 个岗位虽然先写入了，但事务已回滚，也不能出现在返回结果里
    assert "job_c3_db_1" not in data["prejudgements"]
    assert "job_c3_db_2" not in data["prejudgements"]
    assert len(data["prejudgements"]) == 0

    conn = open_db(data_dir)
    try:
        # llm_calls 最新一条 purpose='prejudge' 的 outcome='failed'，billed=1
        call = conn.execute(
            "SELECT outcome, billed, purpose FROM llm_calls WHERE purpose = 'prejudge' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert call is not None
        assert call["outcome"] == "failed"
        assert call["billed"] == 1
        assert call["outcome"] != "reserved"

        # 没有任何 outcome='reserved' 的残留记录
        reserved = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE outcome = 'reserved'").fetchone()[0]
        assert reserved == 0
        assert conn.execute("SELECT COUNT(*) FROM prejudgements").fetchone()[0] == 0
    finally:
        conn.close()
