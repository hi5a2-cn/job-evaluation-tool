"""API integration tests for GET /v1/my-jobs extensions (T039).

Specs:
- specs/003-hr-assistant/spec.md (FR-041-FR-051, SC-009, SC-010, R7)
- specs/003-hr-assistant/contracts/local-api.md (Section 7: GET /v1/my-jobs)
- specs/003-hr-assistant/tasks.md (T039)
"""

from fastapi.testclient import TestClient


def _setup_profile(client: TestClient, headers: dict[str, str]) -> None:
    client.put(
        "/v1/profile",
        json={"directions": ["销售", "开发"], "cities": ["深圳"]},
        headers=headers,
    )


def _ingest_detail_job(
    client: TestClient,
    headers: dict[str, str],
    pid: str,
    title: str,
    company: str,
    seen_at: str = "2026-09-26T01:00:00Z",
) -> None:
    obs = {
        "page_type": "detail",
        "observed_at": seen_at,
        "jobs": [
            {
                "platform_job_id": pid,
                "title": title,
                "company_name": company,
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": f"{title} 岗位职责描述",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200


def test_api_get_my_jobs_all_jobs_filter(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 GET /v1/my-jobs?filter=all_jobs 返回并集去重岗位、counts 与字段完整性。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    # 1. 注入 3 个岗位
    _ingest_detail_job(client, headers, "job_u1", "职位1", "公司1")
    _ingest_detail_job(client, headers, "job_u2", "职位2", "公司2")
    _ingest_detail_job(client, headers, "job_u3", "职位3", "公司3")

    # job_u1: 设为 saved
    client.put("/v1/jobs/job_u1/status", json={"status": "saved"}, headers=headers)
    # job_u2: 设为 applied，并写 hr-note
    client.put("/v1/jobs/job_u2/status", json={"status": "applied"}, headers=headers)
    client.put("/v1/jobs/job_u2/hr-note", json={"note": "HR说西非办事处有常驻补贴", "source": "myjobs"}, headers=headers)
    # job_u3: 仅写 hr-note，不设状态
    client.put("/v1/jobs/job_u3/hr-note", json={"note": "HR说双休不加班", "source": "myjobs"}, headers=headers)

    resp = client.get("/v1/my-jobs?filter=all_jobs", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # counts 校验
    assert data["counts"]["all_jobs"] == 3
    assert data["counts"]["all"] == 2  # job_u1 (saved) + job_u2 (applied)
    assert data["counts"]["saved"] == 1
    assert data["counts"]["applied"] == 1
    assert data["counts"]["skipped"] == 0

    assert data["total_matches"] == 3
    items = data["items"]
    assert len(items) == 3

    pids = {i["platform_job_id"] for i in items}
    assert pids == {"job_u1", "job_u2", "job_u3"}

    # 字段完整性校验：每条带 hr_note 与 recent_updated_at
    item_map = {i["platform_job_id"]: i for i in items}
    assert item_map["job_u1"]["hr_note"] is None
    assert item_map["job_u1"]["hr_note_recorded"] is False
    assert item_map["job_u1"]["recent_updated_at"] is not None

    assert item_map["job_u2"]["hr_note"] == "HR说西非办事处有常驻补贴"
    assert item_map["job_u2"]["hr_note_recorded"] is True
    assert item_map["job_u2"]["recent_updated_at"] is not None

    assert item_map["job_u3"]["hr_note"] == "HR说双休不加班"
    assert item_map["job_u3"]["hr_note_recorded"] is True
    assert item_map["job_u3"]["my_status"] is None  # 无标记状态


def test_api_get_my_jobs_hr_only(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 hr_only=true 过滤及各标签 counts 联动更新。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    _ingest_detail_job(client, headers, "job_h1", "职位1", "公司1")
    _ingest_detail_job(client, headers, "job_h2", "职位2", "公司2")

    # job_h1: saved，无 HR 记录
    client.put("/v1/jobs/job_h1/status", json={"status": "saved"}, headers=headers)
    # job_h2: saved，有 HR 记录
    client.put("/v1/jobs/job_h2/status", json={"status": "saved"}, headers=headers)
    client.put("/v1/jobs/job_h2/hr-note", json={"note": "HR已联系面试", "source": "myjobs"}, headers=headers)

    # 1. all_jobs + hr_only=true
    resp_all_hr = client.get("/v1/my-jobs?filter=all_jobs&hr_only=true", headers=headers)
    assert resp_all_hr.status_code == 200
    data_all_hr = resp_all_hr.json()
    assert data_all_hr["total_matches"] == 1
    assert [i["platform_job_id"] for i in data_all_hr["items"]] == ["job_h2"]
    assert data_all_hr["counts"]["all_jobs"] == 1
    assert data_all_hr["counts"]["all"] == 1
    assert data_all_hr["counts"]["saved"] == 1
    assert data_all_hr["counts"]["applied"] == 0

    # 2. saved + hr_only=true
    resp_saved_hr = client.get("/v1/my-jobs?filter=saved&hr_only=true", headers=headers)
    assert resp_saved_hr.status_code == 200
    data_saved_hr = resp_saved_hr.json()
    assert data_saved_hr["total_matches"] == 1
    assert [i["platform_job_id"] for i in data_saved_hr["items"]] == ["job_h2"]

    # 3. applied + hr_only=true
    resp_app_hr = client.get("/v1/my-jobs?filter=applied&hr_only=true", headers=headers)
    assert resp_app_hr.status_code == 200
    assert resp_app_hr.json()["total_matches"] == 0
    assert resp_app_hr.json()["items"] == []


def test_api_get_my_jobs_search_q_historical_title_and_fields(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 q 搜索命中历史版本职位名、公司名、HR 实际情况，以及职位名取当前版本。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    # 1. 列表页版本：职位名带"（出差智利等）"
    obs_list = {
        "page_type": "list",
        "observed_at": "2026-09-26T01:00:00Z",
        "jobs": [
            {"platform_job_id": "job_multi_v", "title": "储能海外销售（出差智利等）", "company_name": "某新能源科技公司", "salary_raw": "18-28K", "city": "深圳"},
        ],
    }
    assert client.post("/v1/observations", json=obs_list, headers=headers).status_code == 200

    # 2. 详情页版本更新：职位名变为"储能海外销售"
    _ingest_detail_job(client, headers, "job_multi_v", "储能海外销售", "某新能源科技公司", seen_at="2026-09-26T02:00:00Z")
    client.put("/v1/jobs/job_multi_v/status", json={"status": "saved"}, headers=headers)
    client.put("/v1/jobs/job_multi_v/hr-note", json={"note": "HR告知常驻智利办事处", "source": "myjobs"}, headers=headers)

    # 搜索"智利"（旧版本职位名与 HR 说明中均有）
    resp_q = client.get("/v1/my-jobs?filter=all_jobs&q=智利", headers=headers)
    assert resp_q.status_code == 200
    items = resp_q.json()["items"]
    assert len(items) == 1
    assert items[0]["platform_job_id"] == "job_multi_v"
    # 每行显示的职位名取当前版本（FR-042）
    assert items[0]["title"] == "储能海外销售"

    # 搜索公司名
    resp_comp = client.get("/v1/my-jobs?filter=all_jobs&q=某新能源", headers=headers)
    assert resp_comp.status_code == 200
    assert len(resp_comp.json()["items"]) == 1

    # 搜索 HR 记录
    resp_note = client.get("/v1/my-jobs?filter=all_jobs&q=办事处", headers=headers)
    assert resp_note.status_code == 200
    assert len(resp_note.json()["items"]) == 1


def test_api_get_my_jobs_like_escape(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 % 与 _ 的 LIKE ESCAPE 转义通过 API 生效。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    _ingest_detail_job(client, headers, "job_esc_1", "开发", "100%全麦公司")
    _ingest_detail_job(client, headers, "job_esc_2", "开发", "1000全麦公司")
    _ingest_detail_job(client, headers, "job_esc_3", "A_B测试专员", "普通公司")
    _ingest_detail_job(client, headers, "job_esc_4", "AXB测试专员", "普通公司")

    for pid in ("job_esc_1", "job_esc_2", "job_esc_3", "job_esc_4"):
        client.put(f"/v1/jobs/{pid}/status", json={"status": "saved"}, headers=headers)

    # 搜索 100%
    r1 = client.get("/v1/my-jobs?filter=all_jobs&q=100%", headers=headers)
    assert r1.status_code == 200
    assert [i["platform_job_id"] for i in r1.json()["items"]] == ["job_esc_1"]

    # 搜索 A_B
    r2 = client.get("/v1/my-jobs?filter=all_jobs&q=A_B", headers=headers)
    assert r2.status_code == 200
    assert [i["platform_job_id"] for i in r2.json()["items"]] == ["job_esc_3"]


def test_api_get_my_jobs_limit_and_total_matches(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 limit 参数截断与 total_matches 总匹配数。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    for i in range(1, 6):
        _ingest_detail_job(client, headers, f"job_lim_{i}", f"工程师_{i}", "测试企业")
        client.put(f"/v1/jobs/job_lim_{i}/status", json={"status": "saved"}, headers=headers)

    resp = client.get("/v1/my-jobs?filter=all_jobs&limit=2", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_matches"] == 5
    assert len(data["items"]) == 2


def test_api_get_my_jobs_recent_updated_at_sorting(paired_client: tuple[TestClient, dict[str, str]]):
    """测试全部岗位及搜索结果按 recent_updated_at 倒序排列。"""
    client, headers = paired_client
    _setup_profile(client, headers)

    _ingest_detail_job(client, headers, "job_ord_1", "岗位A", "同公司", seen_at="2026-09-26T01:00:00Z")
    _ingest_detail_job(client, headers, "job_ord_2", "岗位B", "同公司", seen_at="2026-09-26T01:00:00Z")

    client.put("/v1/jobs/job_ord_1/status", json={"status": "saved"}, headers=headers)
    client.put("/v1/jobs/job_ord_2/status", json={"status": "saved"}, headers=headers)

    conn = client.app.state.conn
    # 明确设置更新时间晚于 auto-judgement 创建时间（使用远未来时间），以精准测试排序逻辑
    # job_ord_2 (2099-01-01 12:00) 比 job_ord_1 (2099-01-01 10:00) 新
    conn.execute("UPDATE job_status SET updated_at = '2099-01-01T10:00:00Z' WHERE job_id IN (SELECT id FROM jobs WHERE platform_job_id = 'job_ord_1')")
    conn.execute("UPDATE job_status SET updated_at = '2099-01-01T12:00:00Z' WHERE job_id IN (SELECT id FROM jobs WHERE platform_job_id = 'job_ord_2')")
    conn.commit()

    # 检查 all_jobs 排序：job_ord_2 在前
    r1 = client.get("/v1/my-jobs?filter=all_jobs", headers=headers)
    assert [i["platform_job_id"] for i in r1.json()["items"]] == ["job_ord_2", "job_ord_1"]

    # 给 job_ord_1 写入 HR 记录，并设置更新时间为最新 (2099-01-01 14:00)
    client.put("/v1/jobs/job_ord_1/hr-note", json={"note": "最新HR信息", "source": "myjobs"}, headers=headers)
    conn.execute("UPDATE hr_notes SET updated_at = '2099-01-01T14:00:00Z' WHERE job_id IN (SELECT id FROM jobs WHERE platform_job_id = 'job_ord_1')")
    conn.commit()

    # 此时 job_ord_1 的 recent_updated_at 最新 (2099-01-01 14:00)，排到最前
    r2 = client.get("/v1/my-jobs?filter=all_jobs", headers=headers)
    assert [i["platform_job_id"] for i in r2.json()["items"]] == ["job_ord_1", "job_ord_2"]

    # 带搜索词时同样按 recent_updated_at 倒序
    r3 = client.get("/v1/my-jobs?filter=all_jobs&q=同公司", headers=headers)
    assert [i["platform_job_id"] for i in r3.json()["items"]] == ["job_ord_1", "job_ord_2"]


def test_api_get_my_jobs_saved_filter_counts_includes_all_jobs(llm_client: tuple[TestClient, dict[str, str]]):
    """测试 filter=saved 且不带 q、hr_only 时，counts 仍含 all_jobs 且数值正确。"""
    client, headers = llm_client
    _setup_profile(client, headers)

    # 1. 注入 3 个岗位
    _ingest_detail_job(client, headers, "job_s1", "职位1", "公司1")
    _ingest_detail_job(client, headers, "job_s2", "职位2", "公司2")
    _ingest_detail_job(client, headers, "job_s3", "职位3", "公司3")

    # job_s1: 设为 saved
    client.put("/v1/jobs/job_s1/status", json={"status": "saved"}, headers=headers)
    # job_s2: 设为 applied
    client.put("/v1/jobs/job_s2/status", json={"status": "applied"}, headers=headers)
    # job_s3: 仅写 hr-note，不设状态
    client.put("/v1/jobs/job_s3/hr-note", json={"note": "HR沟通顺利", "source": "myjobs"}, headers=headers)

    # 发起 filter=saved 请求（不带 q、hr_only）
    resp = client.get("/v1/my-jobs?filter=saved", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # counts 始终包含 6 项且含 all_jobs 并数值正确
    assert data["counts"] == {
        "all_jobs": 3,
        "all": 2,
        "saved": 1,
        "applied": 1,
        "skipped": 0,
        "recent": 3,
    }
    assert data["total_matches"] == 1
    assert [i["platform_job_id"] for i in data["items"]] == ["job_s1"]
