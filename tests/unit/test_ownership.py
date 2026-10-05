import dataclasses
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from jet.api.app import create_app
from jet.config import Settings
from jet.db.store import OWNED_TABLES, count_ownerless_rows, open_db, utc_now
from tests.conftest import FakeLlmHelper


def test_full_workflow_ownership(
    data_dir: Path,
    settings: Settings,
    fake_llm: FakeLlmHelper,
) -> None:
    """T098: 走完完整业务流程后，断言所有个人数据都有归属 (user_id='me') 且无主记录数为 0。"""
    llm_settings = dataclasses.replace(settings, llm_api_key="test-api-key", review_enabled=False)
    app = create_app(llm_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        # 步骤 1: 配对
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        pair_resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert pair_resp.status_code == 200
        pair_data = pair_resp.json()
        token = pair_data["token"]
        user_id = pair_data["user_id"]
        assert user_id == "me"
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": origin,
        }

        # 步骤 2: 保存画像 (PUT /v1/profile)
        prof_payload = {
            "directions": ["Python后端开发"],
            "keywords": ["Python", "FastAPI"],
            "cities": ["深圳"],
            "preferred_cities": ["深圳"],
            "min_monthly_k": 15.0,
            "work_preference": "后端核心研发",
            "background": "本科，3年Python微服务开发经验",
        }
        prof_resp = client.put("/v1/profile", json=prof_payload, headers=headers)
        assert prof_resp.status_code == 200
        assert prof_resp.json()["version_no"] == 1

        # 步骤 3: 上报列表页观测 (POST /v1/observations, page_type=list, 2 个岗位)
        list_obs = {
            "page_type": "list",
            "observed_at": "2026-10-06T10:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "job_owned_01",
                    "title": "Python后端开发工程师",
                    "company_name": "阿尔法科技",
                    "company_industry": "互联网",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "district": "南山区",
                    "experience": "3-5年",
                    "degree": "本科",
                    "job_labels": ["Python", "FastAPI"],
                    "skills": ["MySQL", "Redis"],
                },
                {
                    "platform_job_id": "job_owned_02",
                    "title": "全栈开发工程师",
                    "company_name": "贝塔网络",
                    "company_industry": "电子商务",
                    "salary_raw": "18-25K",
                    "city": "深圳",
                    "district": "福田区",
                    "experience": "1-3年",
                    "degree": "本科",
                    "job_labels": ["Vue", "Python"],
                    "skills": ["JavaScript", "Python"],
                },
            ],
        }
        list_resp = client.post("/v1/observations", json=list_obs, headers=headers)
        assert list_resp.status_code == 200
        jobs_dict = list_resp.json()["jobs"]
        assert "job_owned_01" in jobs_dict
        assert "job_owned_02" in jobs_dict

        # 步骤 4: 上报详情页观测 (POST /v1/observations, page_type=detail) 触发判断并等待完成
        fake_llm.set_fit(["技术栈完全匹配", "薪资符合预期"])
        detail_obs = {
            "page_type": "detail",
            "observed_at": "2026-10-06T10:05:00Z",
            "jobs": [
                {
                    "platform_job_id": "job_owned_01",
                    "title": "Python后端开发工程师",
                    "company_name": "阿尔法科技",
                    "company_industry": "互联网",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "district": "南山区",
                    "experience": "3-5年",
                    "degree": "本科",
                    "description": "岗位职责：\n1. 负责核心微服务系统设计与开发；\n2. 深入掌握 Python 及异步框架；\n3. 团队周末双休不加班。",
                }
            ],
        }
        detail_resp = client.post("/v1/observations", json=detail_obs, headers=headers)
        assert detail_resp.status_code == 200
        assert app.state.worker.wait_idle(timeout=5.0) is True

        resp_j = client.get("/v1/judgements?ids=job_owned_01", headers=headers)
        assert resp_j.status_code == 200
        j_entry = resp_j.json()["jobs"]["job_owned_01"]["judgement"]
        assert j_entry is not None
        assert j_entry["status"] == "done"

        # 步骤 5: 对同一岗位重新判断 (POST /v1/jobs/{id}/judge)
        rejudge_resp = client.post(
            "/v1/jobs/job_owned_01/judge",
            json={"force": True},
            headers=headers,
        )
        assert rejudge_resp.status_code == 200
        assert app.state.worker.wait_idle(timeout=5.0) is True

        # 步骤 6: 设岗位状态 (PUT /v1/jobs/{id}/status)
        status_resp = client.put(
            "/v1/jobs/job_owned_01/status",
            json={"status": "saved"},
            headers=headers,
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["jobs"]["job_owned_01"]["my_status"]["status"] == "saved"

        # 步骤 7: 写 HR 实际情况 (PUT /v1/jobs/{id}/hr-note)
        hr_resp = client.put(
            "/v1/jobs/job_owned_01/hr-note",
            json={"note": "HR沟通态度好，双休不加班已确认", "source": "card"},
            headers=headers,
        )
        assert hr_resp.status_code == 200
        assert hr_resp.json()["jobs"]["job_owned_01"]["hr_note"] == "HR沟通态度好，双休不加班已确认"

        # 步骤 8: 写标注 (PUT /v1/jobs/{id}/label)
        label_payload = {
            "work_type": "数据与技术",
            "work_subtype": "技术支持与实施",
            "sales_level": "低",
            "work_intensity": "双休",
            "overall": "apply",
            "note": "测试人工标注",
        }
        label_resp = client.put(
            "/v1/jobs/job_owned_01/label",
            json=label_payload,
            headers=headers,
        )
        assert label_resp.status_code == 200
        assert label_resp.json()["labels"]["total"] >= 1

        # 步骤 9: 记录聊天同意 (POST /v1/chat/consent)
        consent_resp = client.post("/v1/chat/consent", headers=headers)
        assert consent_resp.status_code == 200
        assert consent_resp.json()["ok"] is True

        # 步骤 10: 聊天岗位入库 (POST /v1/chat/job)
        chat_job_payload = {
            "platform_job_id": "job_chat_01",
            "title": "后端架构师",
            "company_name": "伽马数码",
        }
        chat_job_resp = client.post("/v1/chat/job", json=chat_job_payload, headers=headers)
        assert chat_job_resp.status_code == 200
        assert "job_chat_01" in chat_job_resp.json()["jobs"]

        # 步骤 11: 保存从严行业勾选 (PUT /v1/strict-industries)
        strict_resp = client.put(
            "/v1/strict-industries",
            json={"selected": ["金融", "餐饮"]},
            headers=headers,
        )
        assert strict_resp.status_code == 200
        assert strict_resp.json()["ok"] is True

        # 步骤 12: 保存经历素材 (PUT /v1/experience)
        exp_payload = [
            {"item_no": 1, "content": "主导过高并发微服务架构设计与重构"},
            {"item_no": 2, "content": "熟悉 Python 异步编程与 FastAPI 框架"},
        ]
        exp_resp = client.put("/v1/experience", json=exp_payload, headers=headers)
        assert exp_resp.status_code == 200
        assert exp_resp.json()["ok"] is True
        assert exp_resp.json()["count"] == 2

    # 流程走完后打开数据库进行全量个人数据归属断言
    conn = open_db(data_dir)
    try:
        # 1. 断言 count_ownerless_rows(conn) == 0
        ownerless = count_ownerless_rows(conn)
        assert ownerless == 0, f"发现无主数据: {ownerless} 行"

        # 2. 对 OWNED_TABLES 里的每张表，SELECT DISTINCT user_id，结果只能是配对得到的用户 ID（'me'）
        for tbl in OWNED_TABLES:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if not has_tbl:
                continue
            rows = conn.execute(f"SELECT DISTINCT user_id FROM {tbl}").fetchall()
            distinct_users = {r[0] for r in rows}
            assert distinct_users.issubset({"me"}), f"表 {tbl} 包含非 'me' 的 user_id: {distinct_users}"

        # 3. 统计每张表的行数，断言指定个人数据表必须有数据
        required_tables = [
            "profiles",
            "views",
            "judgements",
            "llm_calls",
            "pairings",
            "job_status",
            "hr_notes",
            "labels",
            "llm_consents",
            "strict_industry_selection",
            "experience_items",
        ]
        table_counts: dict[str, int] = {}
        for tbl in OWNED_TABLES:
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if has_tbl:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                table_counts[tbl] = cnt

        for tbl in required_tables:
            assert table_counts.get(tbl, 0) > 0, f"表 {tbl} 预期有数据，但实际行数为 0"
    finally:
        conn.close()


def test_ownerless_rows_detection_verification(
    data_dir: Path,
    settings: Settings,
    fake_llm: FakeLlmHelper,
) -> None:
    """T098 负向测试：注入 user_id='ghost' 证明 count_ownerless_rows 能准确检出无主数据。"""
    llm_settings = dataclasses.replace(settings, llm_api_key="test-api-key", review_enabled=False)
    app = create_app(llm_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        # 配对
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        pair_resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert pair_resp.status_code == 200
        token = pair_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}", "Origin": origin}

        # 保存画像
        prof_resp = client.put(
            "/v1/profile",
            json={
                "directions": ["Python后端开发"],
                "keywords": ["Python"],
                "cities": ["深圳"],
            },
            headers=headers,
        )
        assert prof_resp.status_code == 200

        # 一次列表观测
        list_obs = {
            "page_type": "list",
            "observed_at": "2026-10-06T10:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "job_ghost_test",
                    "title": "Python工程师",
                    "city": "深圳",
                }
            ],
        }
        obs_resp = client.post("/v1/observations", json=list_obs, headers=headers)
        assert obs_resp.status_code == 200

    conn = open_db(data_dir)
    try:
        # 初始无主数据应为 0
        assert count_ownerless_rows(conn) == 0

        # 关闭外键检查以允许插入不存在于 users 表的外键 user_id='ghost'
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('ghost', 9999, 'list', ?)",
            (utc_now(),),
        )

        # 断言检测到了 1 行无主数据
        assert count_ownerless_rows(conn) == 1
    finally:
        conn.close()
