import json
from pathlib import Path
import time
import httpx
import pytest
from fastapi.testclient import TestClient

from jet.db.store import open_db, utc_now


def _setup_profile(client: TestClient, headers: dict[str, str], min_monthly_k: int = 0) -> None:
    client.put(
        "/v1/profile",
        json={
            "directions": ["后端开发"],
            "cities": ["深圳"],
            "min_monthly_k": min_monthly_k,
        },
        headers=headers,
    )


def test_observation_detail_without_key_does_not_queue(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _setup_profile(client, headers)

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_detail_no_key_1",
                "title": "后端开发工程师",
                "company_name": "测试网络公司",
                "salary_raw": "25-40K",
                "city": "深圳",
                "description": "Golang / Python 高并发架构研发",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["notice"] == "no_llm_key"
    entry = data["jobs"]["job_detail_no_key_1"]
    assert entry["judgement"] is None

    # Verify no judgement row inserted in SQLite
    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT count(*) as cnt FROM judgements").fetchone()["cnt"]
        assert count == 0
    finally:
        conn.close()


def test_observation_detail_rule_excluded_still_judged_without_key(
    paired_client: tuple[TestClient, dict[str, str]],
):
    client, headers = paired_client
    # Require 30K min salary so 10-15K is rule-excluded
    _setup_profile(client, headers, min_monthly_k=30)

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rule_skip_1",
                "title": "初级开发工程师",
                "company_name": "小微科技",
                "salary_raw": "10-15K",
                "city": "深圳",
                "description": "简单维护",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("notice") is None
    entry = data["jobs"]["job_rule_skip_1"]
    assert entry["judgement"] is not None
    assert entry["judgement"]["status"] == "done"
    assert entry["judgement"]["verdict"] == "skip"
    assert entry["judgement"]["source"] == "rule"


def test_judge_job_without_key_returns_409(
    paired_client: tuple[TestClient, dict[str, str]],
):
    client, headers = paired_client
    _setup_profile(client, headers)

    # First observe the job so it exists in DB
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rejudge_test_1",
                "title": "后端架构师",
                "company_name": "科技创新公司",
                "salary_raw": "30-50K",
                "city": "深圳",
                "description": "分布式系统架构设计",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)

    # POST /judge without key
    resp = client.post("/v1/jobs/job_rejudge_test_1/judge", headers=headers)
    assert resp.status_code == 409
    assert resp.json() == {
        "error": "no_llm_key",
        "message": "请先在设置页填写 DeepSeek API Key",
    }


def test_chat_generate_without_key_returns_409_before_reserve(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-chat-no-key",
        "job_title": "后端架构师",
        "company_name": "某知名公司",
        "location_name": "深圳",
        "hr_name": "张经理",
        "user_name": "李四",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "您好，在招的。", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # preview works fine without LLM call
    preview_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert preview_resp.status_code == 200
    prompt_hash = preview_resp.json()["prompt_hash"]

    # generate fails at key check (before reserve)
    gen_payload = {**payload, "prompt_hash": prompt_hash}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 409
    assert gen_resp.json() == {
        "error": "no_llm_key",
        "message": "请先在设置页填写 DeepSeek API Key",
    }

    # Verify 0 quota reserved (0 llm_calls)
    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT count(*) as cnt FROM llm_calls").fetchone()["cnt"]
        assert count == 0
    finally:
        conn.close()


def test_worker_handles_missing_key_as_interrupted(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _setup_profile(client, headers)

    # Ingest job as list item first so job and version exist
    client.post(
        "/v1/observations",
        json={
            "page_type": "list",
            "observed_at": "2026-09-29T10:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "job_worker_no_key",
                    "title": "后端架构师",
                    "company_name": "某知名公司",
                    "salary_raw": "30-50K",
                    "city": "深圳",
                    "description": "分布式系统架构设计",
                }
            ],
        },
        headers=headers,
    )

    conn = open_db(data_dir)
    try:
        j_row = conn.execute("SELECT id FROM jobs WHERE platform_job_id = 'job_worker_no_key'").fetchone()
        p_row = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()
        v_row = conn.execute("SELECT current_version_id FROM jobs WHERE id = ?", (j_row["id"],)).fetchone()

        cur = conn.execute(
            "INSERT INTO judgements (user_id, job_id, job_version_id, profile_id, status, verdict, source, rule_result, prompt_version, created_at) "
            "VALUES ('me', ?, ?, ?, 'queued', NULL, 'llm', '[]', 'v5', ?)",
            (j_row["id"], v_row["current_version_id"], p_row["id"], utc_now()),
        )
        conn.commit()
        judgement_id = cur.lastrowid
    finally:
        conn.close()

    # Submit to worker directly while key is not configured
    worker = client.app.state.worker
    worker.submit(judgement_id)

    # Wait for worker to finish processing
    timeout = time.time() + 3.0
    while time.time() < timeout:
        c = open_db(data_dir)
        try:
            status_row = c.execute("SELECT status, error FROM judgements WHERE id = ?", (judgement_id,)).fetchone()
            if status_row and status_row["status"] in ("interrupted", "failed", "done"):
                break
        finally:
            c.close()
        time.sleep(0.05)

    c = open_db(data_dir)
    try:
        status_row = c.execute("SELECT status, error FROM judgements WHERE id = ?", (judgement_id,)).fetchone()
        assert status_row["status"] == "interrupted"
        assert status_row["status"] != "failed"
        assert "未配置 API Key" in (status_row["error"] or "")
    finally:
        c.close()


def test_transition_from_no_key_to_configured_key(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _setup_profile(client, headers)

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "facts": {
                    "summary": {"text": "白话职责概括", "quotes": []},
                    "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
                    "sales_level": {"value": "低", "signals": []},
                    "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                    "work_intensity": {"value": "双休", "quotes": []},
                    "risk_signals": [],
                },
                "verdict": "check",
                "derivation": ["职责需进一步确认"],
                "verdict_reason": "职责需进一步确认",
                "hr_questions": ["平时主要工作内容是什么？"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 30},
            },
        )

    # Attach mock transport to app and worker
    client.app.state.llm_transport = httpx.MockTransport(handler)
    client.app.state.worker.transport = httpx.MockTransport(handler)

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_flow_test_1",
                "title": "Golang 工程师",
                "company_name": "极客科技",
                "salary_raw": "25-35K",
                "city": "深圳",
                "description": "Golang 研发",
            }
        ],
    }

    # 1. First observation without key: notice returned, no judgement queued
    r1 = client.post("/v1/observations", json=obs, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["notice"] == "no_llm_key"
    assert r1.json()["jobs"]["job_flow_test_1"]["judgement"] is None

    # 2. Put key
    r2 = client.put("/v1/llm-key", json={"api_key": "sk-test-configured-12345678"}, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["configured"] is True

    # 3. Second observation with key: queued or completed
    r3 = client.post("/v1/observations", json=obs, headers=headers)
    assert r3.status_code == 200
    assert r3.json().get("notice") is None

    # Wait for worker to finish
    timeout = time.time() + 3.0
    while time.time() < timeout:
        c = open_db(data_dir)
        try:
            row = c.execute(
                "SELECT j.status, j.verdict FROM judgements j JOIN jobs b ON j.job_id = b.id WHERE b.platform_job_id = 'job_flow_test_1' ORDER BY j.id DESC LIMIT 1"
            ).fetchone()
            if row and row["status"] in ("done", "failed"):
                break
        finally:
            c.close()
        time.sleep(0.05)

    c = open_db(data_dir)
    try:
        final_row = c.execute(
            "SELECT j.status, j.verdict FROM judgements j JOIN jobs b ON j.job_id = b.id WHERE b.platform_job_id = 'job_flow_test_1' ORDER BY j.id DESC LIMIT 1"
        ).fetchone()
        assert final_row is not None
        assert final_row["status"] == "done"
        assert final_row["verdict"] == "check"
    finally:
        c.close()
