from fastapi.testclient import TestClient
import pytest

from tests.conftest import FakeLlmHelper
from tests.fixtures.boss.observations import to_detail_observation


def test_detail_validation_errors(llm_client: tuple[TestClient, dict[str, str]]):
    client, headers = llm_client

    # 1. More than 1 job in detail observation -> 422
    payload_two = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {"platform_job_id": "j1", "title": "Dev1", "city": "深圳", "description": "desc1"},
            {"platform_job_id": "j2", "title": "Dev2", "city": "深圳", "description": "desc2"},
        ],
    }
    resp1 = client.post("/v1/observations", json=payload_two, headers=headers)
    assert resp1.status_code == 422
    assert resp1.json()["error"] == "invalid_payload"

    # 2. Missing description in detail observation -> 422
    payload_nodesc = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {"platform_job_id": "j1", "title": "Dev1", "city": "深圳", "description": ""},
        ],
    }
    resp2 = client.post("/v1/observations", json=payload_nodesc, headers=headers)
    assert resp2.status_code == 422
    assert resp2.json()["error"] == "invalid_payload"


def test_detail_without_profile_stores_job_and_returns_notice(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_no_profile",
                "title": "Python开发",
                "salary_raw": "15-25K",
                "city": "深圳",
                "district": "南山区",
                "description": "做后端开发",
            }
        ],
    }
    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["notice"] == "no_profile"
    job_info = data["jobs"]["job_no_profile"]
    assert job_info["completeness"] == "full"
    assert job_info["judgement"] is None
    assert fake_llm.call_count == 0


def test_detail_end_to_end_judgement_and_deduplication(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    # Set profile
    client.put(
        "/v1/profile",
        json={
            "directions": ["Python后端"],
            "keywords": ["FastAPI"],
            "cities": ["深圳"],
            "min_monthly_k": 15.0,
            "exclude_keywords": ["外包"],
        },
        headers=headers,
    )

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_fit_01",
                "title": "Python高级工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
                "description": "基于FastAPI的高性能微服务架构研发",
            }
        ],
    }

    # 1. First observation -> immediate queued response
    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    j_data = resp.json()["jobs"]["job_fit_01"]["judgement"]
    assert j_data["status"] == "queued"

    # Wait for background worker to complete
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # Poll via /v1/judgements
    judgements_resp = client.get("/v1/judgements?ids=job_fit_01", headers=headers)
    assert judgements_resp.status_code == 200
    j_polled = judgements_resp.json()["jobs"]["job_fit_01"]["judgement"]
    assert j_polled["status"] == "done"
    assert j_polled["verdict"] == "apply"
    assert j_polled["source"] == "llm"
    assert len(j_polled["derivation"]) > 0

    # 2. Re-observe the exact same job 4 more times -> 0 new LLM calls (SC-002)
    for _ in range(4):
        rep_resp = client.post("/v1/observations", json=payload, headers=headers)
        assert rep_resp.status_code == 200
        assert rep_resp.json()["jobs"]["job_fit_01"]["judgement"]["status"] == "done"

    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1


def test_detail_rule_exclusion_bypasses_llm(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client

    client.put(
        "/v1/profile",
        json={
            "directions": ["Python"],
            "cities": ["深圳"],
            "min_monthly_k": 20.0,
            "exclude_keywords": ["外包"],
        },
        headers=headers,
    )

    # Job is in Guangzhou and has salary max 15K < 20K and is outsource -> fails rules
    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rule_unfit",
                "title": "Python驻场外包",
                "salary_raw": "10-15K",
                "city": "广州",
                "district": "天河区",
                "description": "银行外包项目",
            }
        ],
    }

    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    j_data = resp.json()["jobs"]["job_rule_unfit"]["judgement"]
    assert j_data["status"] == "done"
    assert j_data["verdict"] == "skip"
    assert j_data["source"] == "rule"
    assert len(j_data["reasons"]) > 0
    assert fake_llm.call_count == 0


def test_detail_with_fixture_sample(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client

    # The detail fixture sample has '外包' and '银⾏'
    # Profile excluding '外包' should result in rule exclusion
    client.put(
        "/v1/profile",
        json={
            "directions": ["Python"],
            "cities": ["深圳"],
            "min_monthly_k": 15.0,
            "exclude_keywords": ["外包"],
        },
        headers=headers,
    )

    payload = to_detail_observation()
    payload["jobs"][0]["title"] = "Python外包开发工程师"
    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    job_id = payload["jobs"][0]["platform_job_id"]
    j_data = resp.json()["jobs"][job_id]["judgement"]
    assert j_data["status"] == "done"
    assert j_data["verdict"] == "skip"
    assert j_data["source"] == "rule"
    assert any("外包" in r for r in j_data["reasons"])
    assert fake_llm.call_count == 0


def test_quota_exhausted_limits_calls(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # Set daily limit = 2
    conn.execute("UPDATE user_settings SET daily_llm_limit = 2 WHERE user_id = 'me'")

    client.put(
        "/v1/profile",
        json={
            "directions": ["Python"],
            "cities": ["深圳"],
            "min_monthly_k": 10.0,
            "exclude_keywords": [],
        },
        headers=headers,
    )

    # Observe 5 different matching jobs
    for i in range(1, 6):
        payload = {
            "page_type": "detail",
            "observed_at": "2026-09-24T12:00:00Z",
            "jobs": [
                {
                    "platform_job_id": f"quota_job_{i}",
                    "title": f"Python工程师 {i}",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "description": f"研发岗位职责描述 {i}",
                }
            ],
        }
        client.post("/v1/observations", json=payload, headers=headers)
        assert app.state.worker.wait_idle() is True

    # Check that exactly 2 LLM calls happened
    assert fake_llm.call_count == 2

    # Check status of all 5
    ids_query = ",".join(f"quota_job_{i}" for i in range(1, 6))
    j_resp = client.get(f"/v1/judgements?ids={ids_query}", headers=headers)
    jobs_map = j_resp.json()["jobs"]

    done_count = sum(1 for j in jobs_map.values() if j["judgement"]["status"] == "done")
    exhausted_count = sum(1 for j in jobs_map.values() if j["judgement"]["status"] == "quota_exhausted")
    assert done_count == 2
    assert exhausted_count == 3


def test_detail_llm_timeout_records_failed(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    fake_llm.set_timeout()

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"]},
        headers=headers,
    )

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_timeout",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "研发岗位职责",
            }
        ],
    }

    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    resp = client.get("/v1/judgements?ids=job_timeout", headers=headers)
    j_data = resp.json()["jobs"]["job_timeout"]["judgement"]
    assert j_data["status"] == "failed"
    assert j_data["verdict"] is None
    assert j_data["error"] is not None


def test_observations_detail_backfills_version_for_versionless_job_and_queues_judgement(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """
    T003: 覆盖先有无版本岗位行（current_version_id 为 NULL）→ 详情观测补录第 1 版并置 full、正常排队判断。
    - 数据库中预置无版本的 jobs 行（completeness='list_only', current_version_id=NULL）；
    - 设置有效画像；
    - 发送详情观测请求；
    - 验证创建 version_no=1、source='detail' 的 job_versions 记录，回填 jobs.current_version_id；
    - jobs.completeness 升级为 'full'；
    - 正常排队判断，响应中返回 queued 判断；
    - 后续该 full 岗位收到列表观测时，不降级、不新建版本，只更新 last_seen_at。
    """
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    # 1. 预先设置画像
    client.put(
        "/v1/profile",
        json={
            "directions": ["Python后端开发"],
            "keywords": ["FastAPI", "Python"],
            "cities": ["深圳"],
            "min_monthly_k": 15.0,
        },
        headers=headers,
    )

    # 2. 预置无版本的岗位行（模拟聊天入库的岗位）
    pid = "chat_orig_detail_001"
    now_str = "2026-09-24T10:00:00Z"
    with conn:
        conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, current_version_id, company_name, first_seen_at, last_seen_at) "
            "VALUES ('boss', ?, 'list_only', NULL, '未来科技', ?, ?)",
            (pid, now_str, now_str),
        )
        job_id = conn.execute("SELECT id FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO job_chat_seen (user_id, job_id, chat_title, first_seen_at, last_seen_at) "
            "VALUES ('me', ?, 'Python高级工程师', ?, ?)",
            (job_id, now_str, now_str),
        )

    # 3. 发送包含该岗位的详情观测
    obs_time = "2026-09-24T12:00:00Z"
    detail_payload = {
        "page_type": "detail",
        "observed_at": obs_time,
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python高级工程师",
                "company_name": "未来科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
                "description": "负责基于 FastAPI 的高性能微服务架构研发，要求精通 Python。",
            }
        ],
    }
    resp = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert pid in data["jobs"]
    entry = data["jobs"][pid]
    assert entry["completeness"] == "full"
    assert entry["judgement"] is not None
    assert entry["judgement"]["status"] == "queued"

    # 4. 验证数据库状态
    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["completeness"] == "full"
    assert job_row["current_version_id"] is not None
    assert job_row["last_seen_at"] == obs_time

    version_rows = conn.execute("SELECT * FROM job_versions WHERE job_id = ? ORDER BY version_no", (job_id,)).fetchall()
    assert len(version_rows) == 1
    v = version_rows[0]
    assert v["id"] == job_row["current_version_id"]
    assert v["version_no"] == 1
    assert v["source"] == "detail"
    assert v["title"] == "Python高级工程师"
    assert v["salary_raw"] == "20-30K"
    assert v["city"] == "深圳"
    assert v["district"] == "南山区"
    assert v["description"] == "负责基于 FastAPI 的高性能微服务架构研发，要求精通 Python。"

    # 5. 验证 views 记录与判断队列记录（等待后台 worker 完成）
    assert app.state.worker.wait_idle() is True

    views = conn.execute("SELECT * FROM views WHERE job_id = ?", (job_id,)).fetchall()
    assert len(views) == 1
    assert views[0]["page_type"] == "detail"

    judgement_rows = conn.execute("SELECT * FROM judgements WHERE job_id = ?", (job_id,)).fetchall()
    assert len(judgement_rows) == 1
    assert judgement_rows[0]["status"] in ("queued", "running", "done")
    assert judgement_rows[0]["job_version_id"] == v["id"]
    assert fake_llm.call_count == 1

    # 6. 后续该 full 岗位收到列表观测：不降级、不新建版本，只更新 last_seen_at
    list_obs_time = "2026-09-24T12:10:00Z"
    list_payload = {
        "page_type": "list",
        "observed_at": list_obs_time,
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python高级工程师",
                "company_name": "未来科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp_list = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp_list.status_code == 200
    entry_list = resp_list.json()["jobs"][pid]
    assert entry_list["completeness"] == "full"

    job_row_after_list = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_after_list["completeness"] == "full"
    assert job_row_after_list["current_version_id"] == v["id"]
    assert job_row_after_list["last_seen_at"] == list_obs_time

    version_count = conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert version_count == 1
