import json
from fastapi.testclient import TestClient
from tests.conftest import FakeLlmHelper


def test_auto_refresh_profile_change_queued_replacing_and_done(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """画像变化后再次 detail 观察同一岗位 → 自动新建 queued 判断（origin=auto_refresh），旧判断被取代；worker 完成后为新结论；Judgement 在 queued 时 replacing 为旧结论。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Initial profile
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    # 2. Initial detail observation
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ar_01",
                "title": "Python后端开发工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "精通 FastAPI 高并发开发",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=obs, headers=headers)
    assert resp1.status_code == 200
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    resp_j1 = client.get("/v1/judgements?ids=job_ar_01", headers=headers)
    j1 = resp_j1.json()["jobs"]["job_ar_01"]["judgement"]
    assert j1["status"] == "done"
    assert j1["verdict"] == "apply"
    assert j1["origin"] == "initial"
    assert j1["replacing"] is None
    old_finished_at = j1["judged_at"]

    # 3. Change profile (makes judgement stale)
    client.put(
        "/v1/profile",
        json={"directions": ["Go"], "cities": ["深圳"], "min_monthly_k": 25.0},
        headers=headers,
    )

    fake_llm.set_skip()

    # 4. Detail observation again of the same job
    resp2 = client.post("/v1/observations", json=obs, headers=headers)
    assert resp2.status_code == 200
    j2 = resp2.json()["jobs"]["job_ar_01"]["judgement"]

    # Judgement is newly queued with origin=auto_refresh and replacing information
    assert j2["status"] == "queued"
    assert j2["origin"] == "auto_refresh"
    assert j2["replacing"] == {
        "verdict": "apply",
        "verdict_label": "适合投递",
        "judged_at": old_finished_at,
    }

    # 5. Worker completes execution
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 2

    resp_j3 = client.get("/v1/judgements?ids=job_ar_01", headers=headers)
    j3 = resp_j3.json()["jobs"]["job_ar_01"]["judgement"]
    assert j3["status"] == "done"
    assert j3["verdict"] == "skip"
    assert j3["origin"] == "auto_refresh"
    assert j3["replacing"] is None

    # Check database superseded_by chain
    rows = conn.execute(
        "SELECT id, status, superseded_by, origin FROM judgements "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_ar_01') "
        "ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["superseded_by"] == rows[1]["id"]
    assert rows[0]["origin"] == "initial"
    assert rows[1]["superseded_by"] is None
    assert rows[1]["origin"] == "auto_refresh"


def test_auto_refresh_method_changed_rule_still_excluded(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """判断方式过时的规则判断（engine rules:r2，职位名命中不接受关键词）→ 新规则仍排除 → 直接新建规则判断（origin=auto_refresh，不调用大模型，额度不变）。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Profile with exclude_keywords: ["销售"]
    client.put(
        "/v1/profile",
        json={"directions": ["运营"], "cities": ["深圳"], "exclude_keywords": ["销售"]},
        headers=headers,
    )

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ar_rule_01",
                "title": "电话销售专员",
                "salary_raw": "10-15K",
                "city": "深圳",
                "description": "电话外呼沟通",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=obs, headers=headers)
    assert resp1.status_code == 200
    assert fake_llm.call_count == 0  # screened by rule

    j1 = resp1.json()["jobs"]["job_ar_rule_01"]["judgement"]
    assert j1["status"] == "done"
    assert j1["source"] == "rule"
    assert j1["origin"] == "initial"
    assert j1["engine"] == "rules:r3"

    # 2. Simulate legacy engine 'rules:r2' to make method_changed=True
    conn.execute(
        "UPDATE judgements SET engine = 'rules:r2' "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_ar_rule_01')"
    )

    resp_check = client.get("/v1/judgements?ids=job_ar_rule_01", headers=headers)
    assert resp_check.json()["jobs"]["job_ar_rule_01"]["judgement"]["stale"]["method_changed"] is True

    # 3. Detail observation again
    resp2 = client.post("/v1/observations", json=obs, headers=headers)
    assert resp2.status_code == 200
    j2 = resp2.json()["jobs"]["job_ar_rule_01"]["judgement"]

    # Still excluded by rule: created rule judgement directly, origin=auto_refresh, no LLM call
    assert j2["status"] == "done"
    assert j2["source"] == "rule"
    assert j2["verdict"] == "skip"
    assert j2["engine"] == "rules:r3"
    assert j2["origin"] == "auto_refresh"
    assert j2["stale"]["method_changed"] is False
    assert fake_llm.call_count == 0

    # Check database: 2 judgements, old superseded by new
    rows = conn.execute(
        "SELECT id, superseded_by, origin, engine FROM judgements "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_ar_rule_01') "
        "ORDER BY id ASC"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["superseded_by"] == rows[1]["id"]
    assert rows[0]["engine"] == "rules:r2"
    assert rows[1]["superseded_by"] is None
    assert rows[1]["origin"] == "auto_refresh"
    assert rows[1]["engine"] == "rules:r3"


def test_auto_refresh_method_changed_rule_now_passes_queued_for_llm(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """判断方式过时的规则判断，新规则不再排除 → queued 给大模型，计入额度。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Profile with exclude_keywords: ["销售"]
    client.put(
        "/v1/profile",
        json={"directions": ["互联网金融"], "cities": ["深圳"], "exclude_keywords": ["销售"]},
        headers=headers,
    )

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ar_rule_pass",
                "title": "互联网金融顾问",
                "salary_raw": "15-20K",
                "city": "深圳",
                "description": "涉及金融业务销售拓展",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True

    # 2. Simulate legacy r1 judgement that was excluded due to '销售' in description
    conn.execute("DELETE FROM llm_calls")
    conn.execute(
        "UPDATE judgements SET source = 'rule', engine = 'rules:r1', verdict = 'skip', "
        "reasons = '[\"职位描述命中不接受关键词：销售\"]', status = 'done', origin = 'initial' "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_ar_rule_pass')"
    )
    fake_llm.call_count = 0

    resp_check = client.get("/v1/judgements?ids=job_ar_rule_pass", headers=headers)
    assert resp_check.json()["jobs"]["job_ar_rule_pass"]["judgement"]["stale"]["method_changed"] is True

    # 3. Detail observation again: title does not contain '销售', passes r3 screen -> queued for LLM
    resp2 = client.post("/v1/observations", json=obs, headers=headers)
    assert resp2.status_code == 200
    j2 = resp2.json()["jobs"]["job_ar_rule_pass"]["judgement"]

    assert j2["status"] == "queued"
    assert j2["origin"] == "auto_refresh"
    assert j2["replacing"] is not None
    assert j2["replacing"]["verdict"] == "skip"
    assert j2["replacing"]["verdict_label"] == "不建议投"

    # Worker runs LLM
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1  # billed quota

    resp_done = client.get("/v1/judgements?ids=job_ar_rule_pass", headers=headers)
    j_done = resp_done.json()["jobs"]["job_ar_rule_pass"]["judgement"]
    assert j_done["status"] == "done"
    assert j_done["source"] == "llm"
    assert j_done["origin"] == "auto_refresh"


def test_auto_refresh_skipped_applied_vs_saved(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """用户状态 skipped 或 applied → 不自动更新，仍返回旧判断且 stale 为真；saved → 更新。"""
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    jobs = ["job_st_skip", "job_st_applied", "job_st_saved"]
    for pid in jobs:
        obs = {
            "page_type": "detail",
            "observed_at": "2026-09-25T12:00:00Z",
            "jobs": [
                {
                    "platform_job_id": pid,
                    "title": f"Python研发-{pid}",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "description": "FastAPI高并发微服务",
                }
            ],
        }
        client.post("/v1/observations", json=obs, headers=headers)

    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 3

    # Set personal statuses
    client.put("/v1/jobs/job_st_skip/status", json={"status": "skipped"}, headers=headers)
    client.put("/v1/jobs/job_st_applied/status", json={"status": "applied"}, headers=headers)
    client.put("/v1/jobs/job_st_saved/status", json={"status": "saved"}, headers=headers)

    # Change profile so all 3 become stale
    client.put(
        "/v1/profile",
        json={"directions": ["Python", "Rust"], "cities": ["深圳"], "min_monthly_k": 30.0},
        headers=headers,
    )

    # 1. Skipped job detail observation -> NOT auto refreshed, stale is True
    obs_skip = {
        "page_type": "detail",
        "observed_at": "2026-09-25T13:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_st_skip",
                "title": "Python研发-job_st_skip",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    resp_skip = client.post("/v1/observations", json=obs_skip, headers=headers)
    assert resp_skip.status_code == 200
    j_skip = resp_skip.json()["jobs"]["job_st_skip"]["judgement"]
    assert j_skip["status"] == "done"
    assert j_skip["stale"]["profile_changed"] is True
    assert j_skip["origin"] == "initial"
    assert fake_llm.call_count == 3

    # 2. Applied job detail observation -> NOT auto refreshed, stale is True
    obs_app = {
        "page_type": "detail",
        "observed_at": "2026-09-25T13:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_st_applied",
                "title": "Python研发-job_st_applied",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    resp_app = client.post("/v1/observations", json=obs_app, headers=headers)
    assert resp_app.status_code == 200
    j_app = resp_app.json()["jobs"]["job_st_applied"]["judgement"]
    assert j_app["status"] == "done"
    assert j_app["stale"]["profile_changed"] is True
    assert j_app["origin"] == "initial"
    assert fake_llm.call_count == 3

    # 3. Saved job detail observation -> AUTO REFRESHED!
    obs_saved = {
        "page_type": "detail",
        "observed_at": "2026-09-25T13:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_st_saved",
                "title": "Python研发-job_st_saved",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    resp_saved = client.post("/v1/observations", json=obs_saved, headers=headers)
    assert resp_saved.status_code == 200
    j_saved = resp_saved.json()["jobs"]["job_st_saved"]["judgement"]
    assert j_saved["status"] == "queued"
    assert j_saved["origin"] == "auto_refresh"
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 4


def test_auto_refresh_quota_exhausted_no_new_judgement_and_notice(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """额度为 0 → 不新建任何判断（judgements 行数不变）、返回旧判断、notice == 'auto_refresh_quota'，也没有新增 llm_calls。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_ar_quota",
                "title": "Python后端开发工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # Modify profile to make judgement stale
    client.put(
        "/v1/profile",
        json={"directions": ["Go"], "cities": ["深圳"], "min_monthly_k": 25.0},
        headers=headers,
    )

    # Set daily_llm_limit to 0
    conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")

    rows_before = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    llm_calls_before = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]

    # Detail observation again
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Returns notice = 'auto_refresh_quota'
    assert data["notice"] == "auto_refresh_quota"
    j = data["jobs"]["job_ar_quota"]["judgement"]
    # Returns original done judgement
    assert j["status"] == "done"
    assert j["stale"]["profile_changed"] is True
    assert j["origin"] == "initial"

    # Judgements rows and llm_calls unchanged
    rows_after = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    assert rows_after == rows_before
    llm_calls_after = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert llm_calls_after == llm_calls_before
    assert fake_llm.call_count == 1


def test_auto_refresh_not_stale_or_queued_or_list_obs(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """未过时 → 不更新；现行判断为 queued → 不重复；list 观察不触发。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_various_test",
                "title": "Python后端开发工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # 1. Not stale: detail observation does not re-judge
    resp_fresh = client.post("/v1/observations", json=obs, headers=headers)
    assert resp_fresh.status_code == 200
    assert resp_fresh.json()["notice"] is None
    j_fresh = resp_fresh.json()["jobs"]["job_various_test"]["judgement"]
    assert j_fresh["status"] == "done"
    assert fake_llm.call_count == 1

    # 2. Stale but already queued: returns same queued judgement, no duplicate
    client.put(
        "/v1/profile",
        json={"directions": ["Go"], "cities": ["深圳"], "min_monthly_k": 25.0},
        headers=headers,
    )
    conn.execute(
        "UPDATE judgements SET status = 'queued' "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_various_test')"
    )

    resp_queued = client.post("/v1/observations", json=obs, headers=headers)
    assert resp_queued.status_code == 200
    assert resp_queued.json()["notice"] is None
    j_q = resp_queued.json()["jobs"]["job_various_test"]["judgement"]
    assert j_q["status"] == "queued"
    row_count = conn.execute(
        "SELECT COUNT(*) FROM judgements "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_various_test')"
    ).fetchone()[0]
    assert row_count == 1  # not duplicated!

    # 3. List observation does not trigger auto-refresh
    conn.execute(
        "UPDATE judgements SET status = 'done' "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_various_test')"
    )
    obs_list = {
        "page_type": "list",
        "observed_at": "2026-09-25T14:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_various_test",
                "title": "Python后端开发工程师",
                "city": "深圳",
            }
        ],
    }
    resp_list = client.post("/v1/observations", json=obs_list, headers=headers)
    assert resp_list.status_code == 200
    assert resp_list.json()["notice"] is None
    j_l = resp_list.json()["jobs"]["job_various_test"]["judgement"]
    assert j_l["status"] == "done"
    assert fake_llm.call_count == 1
