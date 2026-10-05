import sqlite3
from fastapi.testclient import TestClient
import pytest

from jet.db.store import utc_now


def test_chat_job_new_job_ingest(paired_client: tuple[TestClient, dict[str, str]]):
    """新岗位首次在聊天中遇到：插入 jobs（completeness=list_only, current_version_id=NULL）和 job_chat_seen，不写 job_versions。"""
    client, headers = paired_client
    conn: sqlite3.Connection = client.app.state.conn

    # 记录调用前 judgements 与 llm_calls 行数
    j_count_before = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    llm_count_before = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    status_count_before = conn.execute("SELECT COUNT(*) FROM job_status").fetchone()[0]
    version_count_before = conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0]

    pid = "chat_job_001"
    payload = {
        "platform_job_id": pid,
        "title": "资深 Python 开发工程师",
        "company_name": "创新科技有限公司",
    }

    resp = client.post("/v1/chat/job", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert pid in data["jobs"]

    entry = data["jobs"][pid]
    assert entry["completeness"] == "list_only"
    assert entry["title"] == "资深 Python 开发工程师"
    assert entry["company_name"] == "创新科技有限公司"
    assert entry["judgement"] is None
    assert entry["hr_note"] is None
    assert entry["my_status"] is None
    assert entry["seen_in_chat"] is True

    # 验证数据库底层变化
    job_row = conn.execute(
        "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (pid,),
    ).fetchone()
    assert job_row is not None
    assert job_row["completeness"] == "list_only"
    assert job_row["current_version_id"] is None
    assert job_row["company_name"] == "创新科技有限公司"

    chat_row = conn.execute(
        "SELECT * FROM job_chat_seen WHERE user_id = 'me' AND job_id = ?",
        (job_row["id"],),
    ).fetchone()
    assert chat_row is not None
    assert chat_row["chat_title"] == "资深 Python 开发工程师"

    # 关键约束：绝不写 job_versions，不写 job_status，不触发判断或 LLM 调用
    version_count_after = conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0]
    status_count_after = conn.execute("SELECT COUNT(*) FROM job_status").fetchone()[0]
    j_count_after = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    llm_count_after = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]

    assert version_count_after == version_count_before
    assert status_count_after == status_count_before
    assert j_count_after == j_count_before
    assert llm_count_after == llm_count_before


def test_chat_job_idempotent(paired_client: tuple[TestClient, dict[str, str]]):
    """多次调用同一聊天岗位入库：幂等无重复，last_seen_at 刷新。"""
    client, headers = paired_client
    conn: sqlite3.Connection = client.app.state.conn

    pid = "chat_job_idempotent_01"
    payload = {
        "platform_job_id": pid,
        "title": "前端开发专家",
        "company_name": "某互联网大厂",
    }

    resp1 = client.post("/v1/chat/job", json=payload, headers=headers)
    assert resp1.status_code == 200

    resp2 = client.post("/v1/chat/job", json=payload, headers=headers)
    assert resp2.status_code == 200

    # 验证 jobs 与 job_chat_seen 仅 1 条记录
    jobs_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE platform_job_id = ?",
        (pid,),
    ).fetchone()[0]
    assert jobs_count == 1

    chat_seen_count = conn.execute(
        "SELECT COUNT(*) FROM job_chat_seen jcs JOIN jobs j ON j.id = jcs.job_id WHERE j.platform_job_id = ?",
        (pid,),
    ).fetchone()[0]
    assert chat_seen_count == 1


def test_chat_job_existing_job_not_overwritten(paired_client: tuple[TestClient, dict[str, str]]):
    """已有完整岗位：聊天入库不覆盖已有职位名、公司名、判断与投递状态。"""
    client, headers = paired_client
    conn: sqlite3.Connection = client.app.state.conn

    # 1. 预先设置画像并观测详情
    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["北京"]},
        headers=headers,
    )
    pid = "chat_job_existing_01"
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "原始职位名（全职）",
                "company_name": "原始公司名",
                "salary_raw": "30-50K",
                "city": "北京",
                "description": "服务端核心业务架构设计",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    client.put(f"/v1/jobs/{pid}/status", json={"status": "applied"}, headers=headers)

    # 2. 聊天入库传入不同的职位名和公司名
    chat_payload = {
        "platform_job_id": pid,
        "title": "聊天中简写的职位名",
        "company_name": "聊天中不同的公司名",
    }
    resp = client.post("/v1/chat/job", json=chat_payload, headers=headers)
    assert resp.status_code == 200
    entry = resp.json()["jobs"][pid]

    # 原有 title (来自版本) 和 company_name 不被覆盖
    assert entry["title"] == "原始职位名（全职）"
    assert entry["company_name"] == "原始公司名"
    assert entry["my_status"]["status"] == "applied"
    assert entry["seen_in_chat"] is True

    # 数据库 jobs 表字段保持未改动
    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["company_name"] == "原始公司名"
    assert job_row["completeness"] == "full"


def test_chat_job_backfills_null_company_name(paired_client: tuple[TestClient, dict[str, str]]):
    """已有岗位 company_name 为 NULL 时，聊天入库可补全公司名。"""
    client, headers = paired_client
    conn: sqlite3.Connection = client.app.state.conn

    pid = "chat_job_null_company"
    now_str = utc_now()
    conn.execute(
        """
        INSERT INTO jobs (platform, platform_job_id, completeness, current_version_id, company_name, first_seen_at, last_seen_at)
        VALUES ('boss', ?, 'list_only', NULL, NULL, ?, ?)
        """,
        (pid, now_str, now_str),
    )

    resp = client.post(
        "/v1/chat/job",
        json={"platform_job_id": pid, "title": "算法工程师", "company_name": "补全的公司名"},
        headers=headers,
    )
    assert resp.status_code == 200
    entry = resp.json()["jobs"][pid]
    assert entry["company_name"] == "补全的公司名"

    job_row = conn.execute("SELECT company_name FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["company_name"] == "补全的公司名"


def test_chat_job_validation_422(paired_client: tuple[TestClient, dict[str, str]]):
    """缺少 platform_job_id 或 title（或纯空白）返回 422。"""
    client, headers = paired_client

    # 1. 缺少 platform_job_id
    r1 = client.post("/v1/chat/job", json={"platform_job_id": "", "title": "职位名"}, headers=headers)
    assert r1.status_code == 422
    assert r1.json()["error"] == "invalid_payload"

    # 2. 缺少 title
    r2 = client.post("/v1/chat/job", json={"platform_job_id": "abc123xyz", "title": "   "}, headers=headers)
    assert r2.status_code == 422
    assert r2.json()["error"] == "invalid_payload"

    # 3. 两个都空白
    r3 = client.post("/v1/chat/job", json={"platform_job_id": "  ", "title": ""}, headers=headers)
    assert r3.status_code == 422
    assert r3.json()["error"] == "invalid_payload"


def test_chat_job_auth_401(paired_client: tuple[TestClient, dict[str, str]]):
    """未配对调用返回 401。"""
    client, _ = paired_client

    resp = client.post(
        "/v1/chat/job",
        json={"platform_job_id": "job_unauth", "title": "职位名"},
    )
    assert resp.status_code == 401
