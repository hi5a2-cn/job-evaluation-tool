from fastapi.testclient import TestClient


def _setup_profile_and_job(
    client: TestClient,
    headers: dict[str, str],
    pid: str = "job_title_test",
    title: str = "Python 后端开发",
    company: str = "字节跳动",
) -> None:
    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["北京"]},
        headers=headers,
    )
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-27T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": title,
                "company_name": company,
                "salary_raw": "30-50K",
                "city": "北京",
                "description": "负责核心服务端架构设计与微服务开发",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200


def test_put_hr_note_and_status_return_current_version_title(
    paired_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = paired_client
    pid = "job_entry_title_1"
    _setup_profile_and_job(client, headers, pid=pid, title="初级前端工程师")

    # 1. PUT hr-note 响应包含当前版本职位名
    resp_note = client.put(
        f"/v1/jobs/{pid}/hr-note",
        json={"note": "实际无前端框架要求，主要做维护"},
        headers=headers,
    )
    assert resp_note.status_code == 200
    entry_note = resp_note.json()["jobs"][pid]
    assert entry_note["title"] == "初级前端工程师"

    # 2. PUT status 响应包含当前版本职位名
    resp_status = client.put(
        f"/v1/jobs/{pid}/status",
        json={"status": "saved"},
        headers=headers,
    )
    assert resp_status.status_code == 200
    entry_status = resp_status.json()["jobs"][pid]
    assert entry_status["title"] == "初级前端工程师"


def test_put_hr_note_and_status_multiple_versions_take_current(
    paired_client: tuple[TestClient, dict[str, str]],
) -> None:
    client, headers = paired_client
    pid = "job_multi_ver_1"

    # 版本 1: 助理工程师
    _setup_profile_and_job(client, headers, pid=pid, title="助理数据分析师")

    # 版本 2: 详情发生实质性变更，生成新版本
    obs_v2 = {
        "page_type": "detail",
        "observed_at": "2026-09-27T11:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "资深数据分析师",
                "company_name": "字节跳动",
                "salary_raw": "40-60K",
                "city": "北京",
                "description": "主导业务指标体系搭建与深度专题分析",
            }
        ],
    }
    resp_v2 = client.post("/v1/observations", json=obs_v2, headers=headers)
    assert resp_v2.status_code == 200

    # 验证 PUT hr-note 返回当前版本职位名 "资深数据分析师"
    resp_note = client.put(
        f"/v1/jobs/{pid}/hr-note",
        json={"note": "业务方向确定为电商搜索推荐"},
        headers=headers,
    )
    assert resp_note.status_code == 200
    entry_note = resp_note.json()["jobs"][pid]
    assert entry_note["title"] == "资深数据分析师"

    # 验证 PUT status 返回当前版本职位名 "资深数据分析师"
    resp_status = client.put(
        f"/v1/jobs/{pid}/status",
        json={"status": "applied"},
        headers=headers,
    )
    assert resp_status.status_code == 200
    entry_status = resp_status.json()["jobs"][pid]
    assert entry_status["title"] == "资深数据分析师"


def test_job_entry_fallback_to_chat_title_and_seen_in_chat(
    paired_client: tuple[TestClient, dict[str, str]],
) -> None:
    """当 current_version_id 为 NULL 时，_job_entry 标题回退为 chat_title，且 seen_in_chat 为 True。"""
    client, headers = paired_client
    pid = "job_chat_title_fallback_01"

    # 通过聊天接口入库
    resp = client.post(
        "/v1/chat/job",
        json={"platform_job_id": pid, "title": "聊天中职能名称", "company_name": "某知名企业"},
        headers=headers,
    )
    assert resp.status_code == 200
    entry = resp.json()["jobs"][pid]
    assert entry["title"] == "聊天中职能名称"
    assert entry["seen_in_chat"] is True

    # 通过 GET /v1/judgements 查询该岗位条目
    j_resp = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    assert j_resp.status_code == 200
    entry_get = j_resp.json()["jobs"][pid]
    assert entry_get["title"] == "聊天中职能名称"
    assert entry_get["seen_in_chat"] is True


def test_job_entry_seen_in_chat_false_for_non_chat_job(
    paired_client: tuple[TestClient, dict[str, str]],
) -> None:
    """非聊天中遇到的岗位，seen_in_chat 字段应为 False。"""
    client, headers = paired_client
    pid = "job_non_chat_01"
    _setup_profile_and_job(client, headers, pid=pid, title="常规详情页岗位")

    resp = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    assert resp.status_code == 200
    entry = resp.json()["jobs"][pid]
    assert entry["title"] == "常规详情页岗位"
    assert entry["seen_in_chat"] is False
