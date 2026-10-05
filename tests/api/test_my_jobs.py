import json
from fastapi.testclient import TestClient

from jet.db.store import utc_now


def _setup_profile_and_job(client: TestClient, headers: dict[str, str], pid: str = "job_test_01", company: str = "字节跳动") -> None:
    # 确保画像存在
    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["深圳"]},
        headers=headers,
    )
    # Ingest 岗位
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T01:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Go 高级开发",
                "company_name": company,
                "salary_raw": "25-40K",
                "city": "深圳",
                "description": "微服务与高性能架构设计",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200


def test_put_status_branches(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    _setup_profile_and_job(client, headers, pid="job_put_test", company="字节跳动")

    # 1. 设置为 saved
    resp1 = client.put("/v1/jobs/job_put_test/status", json={"status": "saved"}, headers=headers)
    assert resp1.status_code == 200
    entry1 = resp1.json()["jobs"]["job_put_test"]
    assert entry1["company_name"] == "字节跳动"
    assert entry1["my_status"] is not None
    assert entry1["my_status"]["status"] == "saved"
    assert "updated_at" in entry1["my_status"]

    # 2. 互斥替换为 applied
    resp2 = client.put("/v1/jobs/job_put_test/status", json={"status": "applied"}, headers=headers)
    assert resp2.status_code == 200
    entry2 = resp2.json()["jobs"]["job_put_test"]
    assert entry2["my_status"]["status"] == "applied"

    # 3. 互斥替换为 skipped
    resp3 = client.put("/v1/jobs/job_put_test/status", json={"status": "skipped"}, headers=headers)
    assert resp3.status_code == 200
    entry3 = resp3.json()["jobs"]["job_put_test"]
    assert entry3["my_status"]["status"] == "skipped"

    # 4. 取消状态 (status = None)
    resp4 = client.put("/v1/jobs/job_put_test/status", json={"status": None}, headers=headers)
    assert resp4.status_code == 200
    entry4 = resp4.json()["jobs"]["job_put_test"]
    assert entry4["my_status"] is None

    # 5. 非法状态值 -> 422
    resp_bad = client.put("/v1/jobs/job_put_test/status", json={"status": "not_a_valid_status"}, headers=headers)
    assert resp_bad.status_code == 422
    assert resp_bad.json()["error"] == "invalid_payload"

    # 6. 不存在的岗位 -> 404
    resp_404 = client.put("/v1/jobs/nonexistent_job_xyz/status", json={"status": "saved"}, headers=headers)
    assert resp_404.status_code == 404
    assert resp_404.json()["error"] == "not_found"

    # 7. 未认证 -> 401
    resp_unauth = client.put("/v1/jobs/job_put_test/status", json={"status": "saved"})
    assert resp_unauth.status_code == 401


def test_job_entry_has_company_name_and_my_status_across_endpoints(llm_client: tuple[TestClient, dict[str, str]]):
    client, headers = llm_client
    _setup_profile_and_job(client, headers, pid="job_entry_test", company="阿里巴巴")

    # 1. 刚观察完时，my_status 为 None，company_name 为 "阿里巴巴"
    judgements_resp = client.get("/v1/judgements?ids=job_entry_test", headers=headers)
    assert judgements_resp.status_code == 200
    entry = judgements_resp.json()["jobs"]["job_entry_test"]
    assert entry["company_name"] == "阿里巴巴"
    assert entry["my_status"] is None

    # 2. 设置状态为 saved
    client.put("/v1/jobs/job_entry_test/status", json={"status": "saved"}, headers=headers)

    # 3. GET /v1/judgements
    j_resp = client.get("/v1/judgements?ids=job_entry_test", headers=headers)
    assert j_resp.status_code == 200
    e_j = j_resp.json()["jobs"]["job_entry_test"]
    assert e_j["company_name"] == "阿里巴巴"
    assert e_j["my_status"]["status"] == "saved"

    # 4. POST /v1/jobs/{id}/judge
    judge_resp = client.post("/v1/jobs/job_entry_test/judge", json={"force": True}, headers=headers)
    assert judge_resp.status_code == 200
    e_judge = judge_resp.json()["jobs"]["job_entry_test"]
    assert e_judge["company_name"] == "阿里巴巴"
    assert e_judge["my_status"]["status"] == "saved"

    # 5. PUT /v1/jobs/{id}/label
    label_resp = client.put(
        "/v1/jobs/job_entry_test/label",
        json={"work_type": "数据与技术", "work_subtype": "开发与测试", "overall": "apply"},
        headers=headers,
    )
    assert label_resp.status_code == 200
    e_label = label_resp.json()["jobs"]["job_entry_test"]
    assert e_label["company_name"] == "阿里巴巴"
    assert e_label["my_status"]["status"] == "saved"

    # 6. PUT /v1/jobs/{id}/hr-note
    hr_resp = client.put(
        "/v1/jobs/job_entry_test/hr-note",
        json={"note": "HR 表示双休"},
        headers=headers,
    )
    assert hr_resp.status_code == 200
    e_hr = hr_resp.json()["jobs"]["job_entry_test"]
    assert e_hr["company_name"] == "阿里巴巴"
    assert e_hr["my_status"]["status"] == "saved"


def test_get_my_jobs_counts_and_sorting_and_filters(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # 设置画像
    client.put(
        "/v1/profile",
        json={"directions": ["开发"], "cities": ["深圳"]},
        headers=headers,
    )

    conn = client.app.state.conn
    now = utc_now()

    # 准备 5 个岗位：
    # job1: saved (更新时间 10:00), 有判断记录, views seen_at 08:00
    # job2: applied (更新时间 11:00), 有判断记录, views seen_at 09:00
    # job3: skipped (更新时间 12:00), 有判断记录, views seen_at 07:00
    # job4: 无状态, 有判断记录, views seen_at 13:00
    # job5: 无状态, 无判断记录, views seen_at 14:00 (仅浏览)
    jobs_info = [
        ("job_1", "岗位一", "腾讯", 1, 10, "2026-09-25T08:00:00Z"),
        ("job_2", "岗位二", "阿里", 2, 20, "2026-09-25T09:00:00Z"),
        ("job_3", "岗位三", "美团", 3, 30, "2026-09-25T07:00:00Z"),
        ("job_4", "岗位四", "百度", 4, 40, "2026-09-25T13:00:00Z"),
        ("job_5", "岗位五", "网易", 5, 50, "2026-09-25T14:00:00Z"),
    ]

    for pid, title, company, jid, vid, seen_at in jobs_info:
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, current_version_id, first_seen_at, last_seen_at) "
            "VALUES (?, 'boss', ?, 'full', ?, NULL, ?, ?)",
            (jid, pid, company, seen_at, seen_at),
        )
        conn.execute(
            "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_parse_ok, city, content_hash, created_at) "
            "VALUES (?, ?, 1, 'detail', ?, '20-30K', 1, 1, '深圳', ?, ?)",
            (vid, jid, title, f"hash_{pid}", seen_at),
        )
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (vid, jid))
        conn.execute(
            "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('me', ?, 'detail', ?)",
            (jid, seen_at),
        )

    # job1, job2, job3, job4 写入判断记录 (profile_id = 1)
    prof_row = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()
    prof_id = prof_row["id"] if prof_row else 1

    for jid, vid, verdict in [(1, 10, "apply"), (2, 20, "try"), (3, 30, "skip"), (4, 40, "check")]:
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (?, 'me', ?, ?, ?, 'done', ?, '{}', ?, ?)",
            (100 + jid, jid, vid, prof_id, verdict, now, now),
        )

    # 写入状态：
    # job1: saved at 10:00
    conn.execute(
        "INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', '2026-09-25T10:00:00Z')"
    )
    # job2: applied at 11:00
    conn.execute(
        "INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 2, 'applied', '2026-09-25T11:00:00Z')"
    )
    # job3: skipped at 12:00
    conn.execute(
        "INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 3, 'skipped', '2026-09-25T12:00:00Z')"
    )

    # 1. 验证 counts（对所有筛选一致）
    resp_all = client.get("/v1/my-jobs?filter=all", headers=headers)
    assert resp_all.status_code == 200
    data_all = resp_all.json()

    assert data_all["counts"] == {
        "all_jobs": 4,
        "all": 3,
        "saved": 1,
        "applied": 1,
        "skipped": 1,
        "recent": 4,  # job1, job2, job3, job4 有判断记录；job5 没有
    }

    # 2. filter=all 排序：按 status updated_at 倒序
    # job3 (12:00) -> job2 (11:00) -> job1 (10:00)
    items_all = data_all["items"]
    assert len(items_all) == 3
    assert [i["platform_job_id"] for i in items_all] == ["job_3", "job_2", "job_1"]

    # 3. filter=saved
    resp_saved = client.get("/v1/my-jobs?filter=saved", headers=headers)
    assert resp_saved.status_code == 200
    data_saved = resp_saved.json()
    assert [i["platform_job_id"] for i in data_saved["items"]] == ["job_1"]

    # 4. filter=applied
    resp_applied = client.get("/v1/my-jobs?filter=applied", headers=headers)
    assert resp_applied.status_code == 200
    data_applied = resp_applied.json()
    assert [i["platform_job_id"] for i in data_applied["items"]] == ["job_2"]

    # 5. filter=skipped
    resp_skipped = client.get("/v1/my-jobs?filter=skipped", headers=headers)
    assert resp_skipped.status_code == 200
    data_skipped = resp_skipped.json()
    assert [i["platform_job_id"] for i in data_skipped["items"]] == ["job_3"]

    # 6. filter=recent：含有判断记录的岗位，按 views 中最大 seen_at 倒序
    # job4 (13:00) -> job2 (09:00) -> job1 (08:00) -> job3 (07:00)
    # job5 (14:00) 虽然浏览时间最新，但无判断记录，不在列表中
    resp_recent = client.get("/v1/my-jobs?filter=recent", headers=headers)
    assert resp_recent.status_code == 200
    data_recent = resp_recent.json()
    assert [i["platform_job_id"] for i in data_recent["items"]] == ["job_4", "job_2", "job_1", "job_3"]


def test_my_jobs_item_fields_and_url(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    _setup_profile_and_job(client, headers, pid="job_field_check", company="美团")
    client.put("/v1/jobs/job_field_check/status", json={"status": "saved"}, headers=headers)

    resp = client.get("/v1/my-jobs?filter=saved", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]

    # 字段完整性检查
    assert item["platform_job_id"] == "job_field_check"
    assert item["title"] == "Go 高级开发"
    assert item["company_name"] == "美团"
    assert item["salary_raw"] == "25-40K"
    assert item["city"] == "深圳"
    assert isinstance(item["stale"], bool)
    assert isinstance(item["hr_note_recorded"], bool)
    assert item["my_status"] == "saved"
    assert item["status_updated_at"] is not None
    assert item["last_seen_at"] is not None
    assert item["url"] == "https://www.zhipin.com/job_detail/job_field_check.html"


def test_legacy_verdict_mapping_and_hr_note_recorded(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    client.put(
        "/v1/profile",
        json={"directions": ["开发"], "cities": ["深圳"]},
        headers=headers,
    )

    conn = client.app.state.conn
    now = utc_now()
    prof_row = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()
    prof_id = prof_row["id"] if prof_row else 1

    # 插入 3 个具有旧 verdict (fit, unsure, unfit) 的岗位
    for idx, (pid, old_v) in enumerate([("j_fit", "fit"), ("j_unsure", "unsure"), ("j_unfit", "unfit")], start=10):
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, current_version_id, first_seen_at, last_seen_at) "
            "VALUES (?, 'boss', ?, 'full', '公司', NULL, ?, ?)",
            (idx, pid, now, now),
        )
        conn.execute(
            "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_parse_ok, city, content_hash, created_at) "
            "VALUES (?, ?, 1, 'detail', '开发', '20-30K', 1, 1, '深圳', 'hash', ?)",
            (idx, idx, now),
        )
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (idx, idx))
        conn.execute(
            "INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('me', ?, 'detail', ?)",
            (idx, now),
        )
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (?, 'me', ?, ?, ?, 'done', ?, '{}', ?, ?)",
            (idx, idx, idx, prof_id, old_v, now, now),
        )
        conn.execute(
            "INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', ?, 'saved', ?)",
            (idx, now),
        )

    # 给 j_fit 加 HR 备注
    client.put("/v1/jobs/j_fit/hr-note", json={"note": "HR已联系"}, headers=headers)

    resp = client.get("/v1/my-jobs?filter=saved", headers=headers)
    assert resp.status_code == 200
    items_by_id = {i["platform_job_id"]: i for i in resp.json()["items"]}

    # fit -> apply / 适合投递
    assert items_by_id["j_fit"]["verdict"] == "apply"
    assert items_by_id["j_fit"]["verdict_label"] == "适合投递"
    assert items_by_id["j_fit"]["hr_note_recorded"] is True

    # unsure -> check / 需要确认
    assert items_by_id["j_unsure"]["verdict"] == "check"
    assert items_by_id["j_unsure"]["verdict_label"] == "需要确认"
    assert items_by_id["j_unsure"]["hr_note_recorded"] is False

    # unfit -> skip / 不建议投
    assert items_by_id["j_unfit"]["verdict"] == "skip"
    assert items_by_id["j_unfit"]["verdict_label"] == "不建议投"
    assert items_by_id["j_unfit"]["hr_note_recorded"] is False


def test_invalid_filter_and_limits(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # 非法 filter -> 422
    resp_bad = client.get("/v1/my-jobs?filter=invalid_filter", headers=headers)
    assert resp_bad.status_code == 422
    assert resp_bad.json()["error"] == "invalid_payload"

    # limit <= 0 -> 422
    resp_bad_limit = client.get("/v1/my-jobs?filter=all&limit=0", headers=headers)
    assert resp_bad_limit.status_code == 422
    assert resp_bad_limit.json()["error"] == "invalid_payload"

    # 未认证 -> 401
    resp_unauth = client.get("/v1/my-jobs?filter=all")
    assert resp_unauth.status_code == 401


def test_recent_filter_only_considers_detail_views(llm_client: tuple[TestClient, dict[str, str]]):
    """"最近看过"的排序与"最后查看"只算打开详情的时间；列表页入库的查看记录不算。"""
    client, headers = llm_client
    client.put("/v1/profile", json={"directions": ["后端开发"], "preferred_cities": ["深圳"]}, headers=headers)

    def detail(pid: str, title: str, at: str):
        obs = {
            "page_type": "detail",
            "observed_at": at,
            "jobs": [{"platform_job_id": pid, "title": title, "salary_raw": "20-30K", "city": "深圳",
                      "description": f"{title} 职责"}],
        }
        assert client.post("/v1/observations", json=obs, headers=headers).status_code == 200

    # 1. 先打开两个岗位的详情（生成判断与 detail 查看记录）
    detail("job_a", "Python 后端", "2026-09-25T01:00:00Z")
    detail("job_b", "Java 后端", "2026-09-25T01:30:00Z")

    # 2. 之后在列表页又"看到"它们（list 查看记录，时间更晚）
    obs_list = {
        "page_type": "list",
        "observed_at": "2026-09-25T03:00:00Z",
        "jobs": [
            {"platform_job_id": "job_a", "title": "Python 后端", "salary_raw": "20-30K", "city": "深圳"},
            {"platform_job_id": "job_b", "title": "Java 后端", "salary_raw": "20-30K", "city": "深圳"},
        ],
    }
    assert client.post("/v1/observations", json=obs_list, headers=headers).status_code == 200

    items = client.get("/v1/my-jobs?filter=recent", headers=headers).json()["items"]
    by_id = {i["platform_job_id"]: i for i in items}
    assert by_id["job_a"]["last_seen_at"] == "2026-09-25T01:00:00Z"  # 列表页的 03:00 不算
    assert by_id["job_b"]["last_seen_at"] == "2026-09-25T01:30:00Z"
    assert [i["platform_job_id"] for i in items] == ["job_b", "job_a"]

    # 3. 再打开 job_a 的详情：只有它的最后查看时间更新，并排到最前
    detail("job_a", "Python 后端", "2026-09-25T04:00:00Z")
    items = client.get("/v1/my-jobs?filter=recent", headers=headers).json()["items"]
    assert [i["platform_job_id"] for i in items] == ["job_a", "job_b"]
    assert items[0]["last_seen_at"] == "2026-09-25T04:00:00Z"
    assert items[1]["last_seen_at"] == "2026-09-25T01:30:00Z"


def test_my_jobs_all_jobs_includes_chat_seen_and_search(paired_client: tuple[TestClient, dict[str, str]]):
    """
    US4: all_jobs 口径计入聊天入库的岗位：
    1. counts.all_jobs 同步计数，filter=all_jobs 列表包含该岗位；
    2. 无版本岗位标题回退为 chat_title，薪资与城市留空；
    3. q 搜索能命中 chat_title；
    4. saved/applied/skipped/recent 等其他筛选口径不受影响。
    """
    client, headers = paired_client

    # 1. 聊天岗位入库
    pid = "chat_seen_myjobs_test"
    resp = client.post(
        "/v1/chat/job",
        json={
            "platform_job_id": pid,
            "title": "大模型算法专家（多模态方向）",
            "company_name": "深蓝智算科技",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # 2. 查询 /v1/my-jobs?filter=all_jobs
    all_jobs_resp = client.get("/v1/my-jobs?filter=all_jobs", headers=headers)
    assert all_jobs_resp.status_code == 200
    data = all_jobs_resp.json()

    assert data["counts"]["all_jobs"] >= 1
    # 该岗位没有状态，没有判断记录
    assert data["counts"]["all"] == 0
    assert data["counts"]["saved"] == 0
    assert data["counts"]["applied"] == 0
    assert data["counts"]["skipped"] == 0
    assert data["counts"]["recent"] == 0

    item = next((i for i in data["items"] if i["platform_job_id"] == pid), None)
    assert item is not None
    assert item["title"] == "大模型算法专家（多模态方向）"
    assert item["company_name"] == "深蓝智算科技"
    assert item["salary_raw"] is None
    assert item["city"] == ""
    assert item["verdict"] is None
    assert item["verdict_label"] is None
    assert item["stale"] is False
    assert item["my_status"] is None

    # 3. 关键字搜索 q 匹配 chat_title
    search_resp = client.get("/v1/my-jobs?filter=all_jobs&q=多模态方向", headers=headers)
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert any(i["platform_job_id"] == pid for i in search_data["items"])

    # 4. 其他 filter 列表中不包含该岗位
    assert client.get("/v1/my-jobs?filter=saved", headers=headers).json()["counts"]["saved"] == 0
    assert client.get("/v1/my-jobs?filter=recent", headers=headers).json()["counts"]["recent"] == 0
