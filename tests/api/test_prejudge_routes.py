from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from jet.db.store import connect, utc_now
from tests.conftest import FakeLlmHelper


def _setup_active_profile(data_dir: Path, user_id: str = "me") -> int:
    conn = connect(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, excluded_cities, exclude_keywords, created_at) "
        "VALUES (1, ?, 1, '[\"Python\"]', '[\"FastAPI\"]', '[\"深圳\"]', '[\"北京\"]', '[\"外包\"]', ?)",
        (user_id, now),
    )
    conn.close()
    return 1


def _observe_job(data_dir: Path, platform_job_id: str, title: str = "Python Dev") -> None:
    conn = connect(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO jobs (platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES ('boss', ?, 'Tech Inc', 'full', ?, ?)",
        (platform_job_id, now, now),
    )
    conn.close()


def test_prejudge_require_paired(client: TestClient):
    # Unpaired request rejected with 401
    resp1 = client.post("/v1/prejudge", json={"jobs": []})
    assert resp1.status_code == 401

    resp2 = client.get("/v1/prejudge/settings")
    assert resp2.status_code == 401

    resp3 = client.put("/v1/prejudge/settings", json={"daily_prejudge_limit": 20})
    assert resp3.status_code == 401


def test_prejudge_settings_get_and_put(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # Default settings
    get_res = client.get("/v1/prejudge/settings", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["daily_prejudge_limit"] == 20
    assert data["used_today"] == 0
    assert data["remaining_today"] == 20

    # PUT valid limit
    put_res = client.put("/v1/prejudge/settings", json={"daily_prejudge_limit": 50}, headers=headers)
    assert put_res.status_code == 200
    data2 = put_res.json()
    assert data2["daily_prejudge_limit"] == 50
    assert data2["remaining_today"] == 50

    # PUT invalid limit (<0 or >200)
    err_res1 = client.put("/v1/prejudge/settings", json={"daily_prejudge_limit": -1}, headers=headers)
    assert err_res1.status_code == 422

    err_res2 = client.put("/v1/prejudge/settings", json={"daily_prejudge_limit": 201}, headers=headers)
    assert err_res2.status_code == 422


def test_prejudge_no_profile(
    llm_client: tuple[TestClient, dict[str, str]],
):
    client, headers = llm_client

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_1",
                "title": "Python Dev",
                "company_name": "Tech Corp",
            }
        ]
    }
    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_profile"
    assert resp.json()["prejudgements"] == {}


def test_prejudge_no_llm_key(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _setup_active_profile(data_dir)
    _observe_job(data_dir, "job_1")

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_1",
                "title": "Python Dev",
                "company_name": "Tech Corp",
            }
        ]
    }
    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_llm_key"
    assert resp.json()["prejudgements"] == {}


def test_prejudge_quota_exhausted(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = llm_client
    _setup_active_profile(data_dir)
    _observe_job(data_dir, "job_1")

    # Set limit to 0
    client.put("/v1/prejudge/settings", json={"daily_prejudge_limit": 0}, headers=headers)

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_1",
                "title": "Python Dev",
                "company_name": "Tech Corp",
            }
        ]
    }
    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "quota_exhausted"
    assert resp.json()["prejudgements"] == {}


def test_prejudge_all_already_done(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = llm_client
    _setup_active_profile(data_dir)
    _observe_job(data_dir, "job_done_1")

    # Insert a formal done judgement
    conn = connect(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', 'hash_done', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'apply', '{}', ?)",
        (now,),
    )
    conn.close()

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_done_1",
                "title": "Python Dev",
                "company_name": "Tech Corp",
            }
        ]
    }
    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "empty"
    assert res_data["prejudgements"] == {}


def test_prejudge_success_and_cache(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    fake_llm: FakeLlmHelper,
):
    client, headers = llm_client
    _setup_active_profile(data_dir)
    _observe_job(data_dir, "job_pj_1")
    _observe_job(data_dir, "job_pj_2")

    fake_llm.set_prejudge([
        {"id": "1", "level": "open", "reason": "薪资契合"},
        {"id": "2", "level": "skip", "reason": "行业不符"},
    ])

    payload = {
        "jobs": [
            {
                "platform_job_id": "job_pj_1",
                "title": "Python 后端",
                "company_name": "科技公司A",
                "salary_raw": "20-30K",
            },
            {
                "platform_job_id": "job_pj_2",
                "title": "销售代表",
                "company_name": "销售公司B",
            },
        ]
    }

    # 1. 首次调用：触发大模型预判
    resp = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["prejudgements"]) == 2
    assert data["prejudgements"]["job_pj_1"]["level"] == "open"
    assert data["prejudgements"]["job_pj_1"]["reason"] == "薪资契合"
    assert data["prejudgements"]["job_pj_2"]["level"] == "skip"

    # 断言 usage 扣除 1 页
    assert data["usage"]["used"] == 1
    assert data["usage"]["remaining"] == 19
    assert fake_llm.call_count == 1

    # 断言发出的请求体严格遵循白名单
    import json
    llm_req_body = json.loads(fake_llm.requests[0].content)
    assert llm_req_body["max_tokens"] == 3000
    sent_prompt = "".join(m["content"] for m in llm_req_body["messages"])
    assert "Python 后端" in sent_prompt
    assert "科技公司A" in sent_prompt
    assert "20-30K" in sent_prompt
    # 严格排除 platform_job_id 与违禁信息
    assert "job_pj_1" not in sent_prompt
    assert "job_pj_2" not in sent_prompt
    assert "platform_job_id" not in sent_prompt
    assert "[1] 序号: 1" in sent_prompt
    assert "[2] 序号: 2" in sent_prompt
    assert "hr_notes" not in sent_prompt
    assert "chat" not in sent_prompt
    assert "resume" not in sent_prompt
    assert "experience_items" not in sent_prompt

    # 验证数据库 prejudgements 记录
    conn = connect(data_dir)
    pjs = conn.execute("SELECT * FROM prejudgements ORDER BY id").fetchall()
    assert len(pjs) == 2
    assert pjs[0]["level"] == "open"
    assert pjs[0]["engine"] == "deepseek-flash:no-think"
    conn.close()

    # 2. 第二次调用相同岗位：命中 prejudgements 缓存，不再调用大模型
    resp2 = client.post("/v1/prejudge", json=payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ok"
    assert len(data2["prejudgements"]) == 2
    # call_count 仍为 1，无重复网络调用
    assert fake_llm.call_count == 1
    # 额度使用不新增扣费
    assert data2["usage"]["used"] == 1
