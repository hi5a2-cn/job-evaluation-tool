import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from jet.api.routes import ObservationJob
from jet.db.store import open_db, utc_now
from jet.domain.jobs import ingest
from jet.domain.judgements import staleness, to_api
from jet.domain.profiles import save_profile


def test_observation_job_model_validation():
    """测试 ObservationJob 模型中 company_industry 字段的校验（可选、最长 50 字符）。"""
    # 1. 允许 None 或未提供
    job1 = ObservationJob(platform_job_id="job_1", title="Python开发", city="深圳")
    assert job1.company_industry is None

    # 2. 正常字符串 <= 50 字符
    job2 = ObservationJob(
        platform_job_id="job_2",
        title="Python开发",
        city="深圳",
        company_industry="新能源汽车",
    )
    assert job2.company_industry == "新能源汽车"

    # 恰好 50 字符
    job3 = ObservationJob(
        platform_job_id="job_3",
        title="Python开发",
        city="深圳",
        company_industry="字" * 50,
    )
    assert job3.company_industry == "字" * 50

    # 超过 50 字符触发校验失败
    with pytest.raises(ValidationError):
        ObservationJob(
            platform_job_id="job_4",
            title="Python开发",
            city="深圳",
            company_industry="字" * 51,
        )


def test_company_industry_ingest_does_not_bump_version_or_stale_judgement(data_dir: Path):
    """
    FR-057 核心要求测试：
    证明同一岗位先无行业入库、再带行业入库，岗位版本号不变、已有判断 staleness 的 job_changed 仍为 False。
    且非空才写入/更新，空值不覆盖已有行业。
    """
    conn = open_db(data_dir)
    now_str = utc_now()

    # 1. 创建用户画像
    p_id, _ = save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})
    prof = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()

    pid = "job_test_industry_001"
    base_job_payload = {
        "platform_job_id": pid,
        "title": "Python后端开发工程师",
        "company_name": "未来汽车科技",
        "salary_raw": "20-30K",
        "city": "深圳",
        "district": "南山区",
        "description": "负责自动驾驶车机端云协同系统研发。",
    }

    # 2. 第一次入库：无 company_industry
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[dict(base_job_payload, company_industry=None)],
        observed_at=now_str,
    )

    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row is not None
    job_id = job_row["id"]
    first_version_id = job_row["current_version_id"]
    assert job_row["company_industry"] is None

    version_rows_1 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_id,)).fetchall()
    assert len(version_rows_1) == 1
    assert version_rows_1[0]["version_no"] == 1

    # 3. 对该版本打一个判断
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (7001, 'me', ?, ?, ?, 'done', 'apply', 'llm', '[\"技术匹配\"]', 'v5', 'deepseek', '{}', ?, ?)",
        (job_id, first_version_id, prof["id"], now_str, now_str),
    )
    j_row_1 = conn.execute("SELECT * FROM judgements WHERE id = 7001").fetchone()
    stale_1 = staleness(conn, j_row_1)
    assert stale_1["job_changed"] is False
    assert stale_1["profile_changed"] is False

    # 4. 第二次入库：带 company_industry="新能源汽车"（且职位名、薪资、城市、描述相同）
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[dict(base_job_payload, company_industry="新能源汽车")],
        observed_at=now_str,
    )

    # 验证 jobs.company_industry 成功更新
    job_row_after = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job_row_after["company_industry"] == "新能源汽车"

    # 验证版本完全没有增加，仍为第 1 版
    version_rows_2 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_id,)).fetchall()
    assert len(version_rows_2) == 1
    assert version_rows_2[0]["version_no"] == 1
    assert job_row_after["current_version_id"] == first_version_id

    # 验证已有判断 staleness 的 job_changed 依然为 False！
    stale_2 = staleness(conn, j_row_1)
    assert stale_2["job_changed"] is False
    api_res = to_api(conn, j_row_1)
    assert api_res["stale"]["job_changed"] is False

    # 5. 第三次入库：传入空字符串或全空格，不覆盖已有行业
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[dict(base_job_payload, company_industry="   ")],
        observed_at=now_str,
    )
    job_row_after_empty = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job_row_after_empty["company_industry"] == "新能源汽车"

    conn.close()


def test_observations_api_company_industry_validation(
    llm_client: tuple[TestClient, dict[str, str]],
):
    """测试通过 /v1/observations 接口提交 company_industry 时的长度校验。"""
    client, headers = llm_client

    # 超长 51 字符 -> 422
    payload_too_long = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ind_toolong",
                "title": "Python开发",
                "company_name": "某科技",
                "company_industry": "a" * 51,
                "city": "深圳",
                "description": "做后端开发",
            }
        ],
    }
    resp_err = client.post("/v1/observations", json=payload_too_long, headers=headers)
    assert resp_err.status_code == 422
    assert resp_err.json()["error"] == "invalid_payload"

    # 合法 50 字符 -> 200
    payload_valid = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ind_valid",
                "title": "Python开发",
                "company_name": "某科技",
                "company_industry": "a" * 50,
                "city": "深圳",
                "description": "做后端开发",
            }
        ],
    }
    resp_ok = client.post("/v1/observations", json=payload_valid, headers=headers)
    assert resp_ok.status_code == 200
