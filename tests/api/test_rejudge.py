from fastapi.testclient import TestClient

from tests.conftest import FakeLlmHelper


def test_rejudge_lifecycle_and_stale_detection(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Set initial profile
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    # 2. Ingest first version of job
    obs_1 = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rejudge_01",
                "title": "Python开发工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    client.post("/v1/observations", json=obs_1, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # Check that initial judgement is done and NOT stale
    resp_j1 = client.get("/v1/judgements?ids=job_rejudge_01", headers=headers)
    j1 = resp_j1.json()["jobs"]["job_rejudge_01"]["judgement"]
    assert j1["status"] == "done"
    assert j1["stale"]["job_changed"] is False
    assert j1["stale"]["profile_changed"] is False

    # 3. Job salary changes: observe again (mark as skipped so detail observation does not auto-refresh per FR-052)
    client.put("/v1/jobs/job_rejudge_01/status", json={"status": "skipped"}, headers=headers)
    obs_2 = {
        "page_type": "detail",
        "observed_at": "2026-09-24T13:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rejudge_01",
                "title": "Python开发工程师",
                "salary_raw": "25-35K",  # changed salary
                "city": "深圳",
                "description": "FastAPI高并发微服务",
            }
        ],
    }
    resp_obs2 = client.post("/v1/observations", json=obs_2, headers=headers)
    assert resp_obs2.status_code == 200
    # No auto re-judgement! Call count stays 1 (FR-052: skipped jobs not auto-refreshed)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # But judgement is now stale.job_changed = True!
    resp_j2 = client.get("/v1/judgements?ids=job_rejudge_01", headers=headers)
    j2 = resp_j2.json()["jobs"]["job_rejudge_01"]["judgement"]
    assert j2["stale"]["job_changed"] is True
    assert j2["stale"]["profile_changed"] is False

    # 4. Modify profile
    client.put(
        "/v1/profile",
        json={"directions": ["Python", "Go"], "cities": ["深圳"], "min_monthly_k": 18.0},
        headers=headers,
    )
    # Judgement now also has stale.profile_changed = True!
    resp_j3 = client.get("/v1/judgements?ids=job_rejudge_01", headers=headers)
    j3 = resp_j3.json()["jobs"]["job_rejudge_01"]["judgement"]
    assert j3["stale"]["job_changed"] is True
    assert j3["stale"]["profile_changed"] is True

    # 5. User clicks "rejudge": POST /v1/jobs/{id}/judge
    rejudge_resp = client.post("/v1/jobs/job_rejudge_01/judge", headers=headers)
    assert rejudge_resp.status_code == 200
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 2  # New LLM call executed!

    # Check database: old judgement has superseded_by pointing to new judgement
    judgements = conn.execute(
        "SELECT id, status, superseded_by FROM judgements ORDER BY id ASC"
    ).fetchall()
    assert len(judgements) == 2
    old_j, new_j = judgements[0], judgements[1]
    assert old_j["superseded_by"] == new_j["id"]
    assert new_j["superseded_by"] is None

    # New judgement is fresh (not stale)
    resp_j4 = client.get("/v1/judgements?ids=job_rejudge_01", headers=headers)
    j4 = resp_j4.json()["jobs"]["job_rejudge_01"]["judgement"]
    assert j4["status"] == "done"
    assert j4["stale"]["job_changed"] is False
    assert j4["stale"]["profile_changed"] is False


def test_rejudge_retry_failed_in_place(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"]},
        headers=headers,
    )

    # First attempt fails with timeout
    fake_llm.set_timeout()
    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_retry_test",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "研发描述",
            }
        ],
    }
    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    j_before = client.get("/v1/judgements?ids=job_retry_test", headers=headers).json()["jobs"]["job_retry_test"]["judgement"]
    assert j_before["status"] == "failed"

    # Now recover LLM to fit mode and retry
    fake_llm.set_fit()
    retry_resp = client.post("/v1/jobs/job_retry_test/judge", headers=headers)
    assert retry_resp.status_code == 200
    assert app.state.worker.wait_idle() is True

    # Same row updated in-place (no new row created)
    rows = conn.execute(
        "SELECT id, status, superseded_by FROM judgements WHERE user_id = 'me'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "done"
    assert rows[0]["superseded_by"] is None


def test_rejudge_list_only_rejected(llm_client: tuple[TestClient, dict[str, str]]):
    client, headers = llm_client

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"]},
        headers=headers,
    )

    # Ingest list_only job
    payload = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "list_only_job",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    client.post("/v1/observations", json=payload, headers=headers)

    # Calling judge on list_only job returns 409 list_only
    resp = client.post("/v1/jobs/list_only_job/judge", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"] == "list_only"


def test_rejudge_when_quota_exhausted(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # Set daily limit to 0
    conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")

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
                "platform_job_id": "quota_ex_job",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "研发",
            }
        ],
    }
    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    # Initial status is quota_exhausted
    j_res = client.get("/v1/judgements?ids=quota_ex_job", headers=headers).json()["jobs"]["quota_ex_job"]["judgement"]
    assert j_res["status"] == "quota_exhausted"

    # Rejudging while quota is still 0 returns 200 with status=quota_exhausted
    rejudge_resp = client.post("/v1/jobs/quota_ex_job/judge", headers=headers)
    assert rejudge_resp.status_code == 200
    assert rejudge_resp.json()["jobs"]["quota_ex_job"]["judgement"]["status"] == "quota_exhausted"
    assert fake_llm.call_count == 0


def test_queued_judgement_resumes_after_restart(settings, data_dir):
    """Jet 重启时，已排队但未执行的判断会重新交给 worker（队列只在内存中）。"""
    import dataclasses
    import json

    import httpx
    from fastapi.testclient import TestClient

    from jet.api.app import create_app
    from jet.db.store import connect, init_db, utc_now

    init_db(data_dir)
    conn = connect(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"数据分析\"]', '[]', '[\"深圳\"]', '[]', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'resume1', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, "
        "description, content_hash, created_at) VALUES (1, 1, 1, 'detail', '数据分析师', 0, 0, '深圳', '描述', 'h', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'queued', '{}', ?)",
        (now,),
    )
    conn.close()

    def handler(request):
        body = {"choices": [{"message": {"content": json.dumps({"verdict": "fit", "reasons": ["方向匹配"]})}}]}
        return httpx.Response(200, json=body)

    s = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v1")
    app = create_app(s, admin_secret="test-admin", llm_transport=httpx.MockTransport(handler))
    with TestClient(app, client=("127.0.0.1", 50000)):
        assert app.state.worker.wait_idle(5.0)
        conn = connect(data_dir)
        row = conn.execute("SELECT status, verdict FROM judgements WHERE id = 1").fetchone()
        conn.close()
    assert row["status"] == "done"
    assert row["verdict"] == "fit"


def test_old_rule_judgement_stale_and_rejudge_to_llm(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    from pathlib import Path
    from jet.db.store import utc_now

    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Profile with exclude_keywords: ["销售"]
    client.put(
        "/v1/profile",
        json={"directions": ["互联网金融"], "cities": ["深圳"], "exclude_keywords": ["销售"]},
        headers=headers,
    )

    # 2. Ingest "互联网金融" from tests/fixtures/boss/risk_finance_jd.txt
    jd_path = Path(__file__).resolve().parent.parent / "fixtures" / "boss" / "risk_finance_jd.txt"
    desc = jd_path.read_text(encoding="utf-8")

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "risk_finance_01",
                "title": "互联网金融",
                "salary_raw": "6-10K",
                "city": "深圳",
                "description": desc,
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True

    job = conn.execute("SELECT * FROM jobs WHERE platform_job_id = 'risk_finance_01'").fetchone()
    prof = conn.execute("SELECT * FROM profiles WHERE user_id = 'me'").fetchone()
    now = utc_now()

    # Clear any auto-generated judgement from ingest and simulate legacy rule judgement:
    # source='rule', engine=NULL, verdict='unfit', reasons=['命中不接受关键词：销售']
    conn.execute("DELETE FROM llm_calls WHERE judgement_id IN (SELECT id FROM judgements WHERE job_id = ?)", (job["id"],))
    conn.execute("DELETE FROM judgements WHERE job_id = ?", (job["id"],))
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (8001, 'me', ?, ?, ?, 'done', 'unfit', 'rule', '[\"命中不接受关键词：销售\"]', 'rule', NULL, '{}', ?, ?)",
        (job["id"], job["current_version_id"], prof["id"], now, now),
    )

    # Reset fake_llm call count
    fake_llm.call_count = 0

    # 3. Verify stale.method_changed = True
    resp_get = client.get("/v1/judgements?ids=risk_finance_01", headers=headers)
    j_old = resp_get.json()["jobs"]["risk_finance_01"]["judgement"]
    assert j_old["status"] == "done"
    assert j_old["source"] == "rule"
    assert j_old["engine"] is None
    assert j_old["stale"]["method_changed"] is True

    # 4. POST judge {}
    resp_judge = client.post("/v1/jobs/risk_finance_01/judge", json={}, headers=headers)
    assert resp_judge.status_code == 200
    j_new = resp_judge.json()["jobs"]["risk_finance_01"]["judgement"]
    # Under rules:r2, "销售" is only checked against title ("互联网金融"), not JD text.
    # So rule check passes and job is queued for LLM judgement!
    assert j_new["status"] == "queued"
    assert j_new["source"] is None

    # Worker completes LLM judgement
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    # 5. Check new judgement is done by LLM
    resp_done = client.get("/v1/judgements?ids=risk_finance_01", headers=headers)
    j_final = resp_done.json()["jobs"]["risk_finance_01"]["judgement"]
    assert j_final["status"] == "done"
    assert j_final["source"] == "llm"
    assert j_final["stale"]["method_changed"] is False

    # 6. Old judgement superseded_by points to new judgement
    old_row = conn.execute("SELECT * FROM judgements WHERE id = 8001").fetchone()
    assert old_row["superseded_by"] is not None
    new_row = conn.execute("SELECT * FROM judgements WHERE id != 8001 AND job_id = ?", (job["id"],)).fetchone()
    assert old_row["superseded_by"] == new_row["id"]
    assert new_row["superseded_by"] is None


def test_new_rule_judgement_has_current_engine_and_not_stale(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["运营"], "cities": ["深圳"], "exclude_keywords": ["销售"]},
        headers=headers,
    )

    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_rule_r2_test",
                "title": "电话销售专员",
                "salary_raw": "10-15K",
                "city": "深圳",
                "description": "负责客户电话沟通",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 0  # Screened by rule, no LLM call

    resp = client.get("/v1/judgements?ids=job_rule_r2_test", headers=headers)
    j = resp.json()["jobs"]["job_rule_r2_test"]["judgement"]
    assert j["status"] == "done"
    assert j["verdict"] == "skip"
    assert j["source"] == "rule"
    assert j["engine"] == "rules:r3"
    assert j["stale"]["method_changed"] is False
    assert j["stale"]["job_changed"] is False
    assert j["stale"]["profile_changed"] is False


def test_force_rejudge_lifecycle_and_quota(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. Profile and ingest job
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"]},
        headers=headers,
    )
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T10:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_force_test",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "研发岗位",
            }
        ],
    }
    client.post("/v1/observations", json=obs, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    resp_j1 = client.get("/v1/judgements?ids=job_force_test", headers=headers)
    j1 = resp_j1.json()["jobs"]["job_force_test"]["judgement"]
    assert j1["status"] == "done"
    assert j1["stale"]["job_changed"] is False
    assert j1["stale"]["profile_changed"] is False
    assert j1["stale"]["method_changed"] is False

    # 2. POST judge {} without force on not-stale done judgement -> returns same done judgement, no new LLM call
    resp_rejudge_noforce = client.post("/v1/jobs/job_force_test/judge", json={}, headers=headers)
    assert resp_rejudge_noforce.status_code == 200
    assert resp_rejudge_noforce.json()["jobs"]["job_force_test"]["judgement"]["status"] == "done"
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1
    rows_1 = conn.execute(
        "SELECT id FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_force_test')"
    ).fetchall()
    assert len(rows_1) == 1

    # 3. POST judge {"force": true} on not-stale done judgement -> creates new queued judgement, supersedes old, bills quota
    resp_force = client.post("/v1/jobs/job_force_test/judge", json={"force": True}, headers=headers)
    assert resp_force.status_code == 200
    j_forced = resp_force.json()["jobs"]["job_force_test"]["judgement"]
    assert j_forced["status"] == "queued"

    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 2  # New LLM call executed and counted towards quota

    resp_j2 = client.get("/v1/judgements?ids=job_force_test", headers=headers)
    j2 = resp_j2.json()["jobs"]["job_force_test"]["judgement"]
    assert j2["status"] == "done"
    assert j2["source"] == "llm"

    rows_2 = conn.execute(
        "SELECT id, status, superseded_by FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_force_test') ORDER BY id ASC"
    ).fetchall()
    assert len(rows_2) == 2
    assert rows_2[0]["superseded_by"] == rows_2[1]["id"]
    assert rows_2[1]["superseded_by"] is None

    # 4. force=true when quota is 0 -> status is quota_exhausted
    conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")
    resp_quota_0 = client.post("/v1/jobs/job_force_test/judge", json={"force": True}, headers=headers)
    assert resp_quota_0.status_code == 200
    j_q0 = resp_quota_0.json()["jobs"]["job_force_test"]["judgement"]
    assert j_q0["status"] == "quota_exhausted"
    assert fake_llm.call_count == 2  # No LLM call

    rows_3 = conn.execute(
        "SELECT id, status, superseded_by FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_force_test') ORDER BY id ASC"
    ).fetchall()
    assert len(rows_3) == 3
    assert rows_3[1]["superseded_by"] == rows_3[2]["id"]
    assert rows_3[2]["status"] == "quota_exhausted"
    assert rows_3[2]["superseded_by"] is None

    # 5. When latest is queued/running -> force=true merges and returns same queued judgement
    conn.execute("UPDATE judgements SET status = 'queued' WHERE id = ?", (rows_3[2]["id"],))
    resp_queued_merge = client.post("/v1/jobs/job_force_test/judge", json={"force": True}, headers=headers)
    assert resp_queued_merge.status_code == 200
    j_merge = resp_queued_merge.json()["jobs"]["job_force_test"]["judgement"]
    assert j_merge["status"] == "queued"

    rows_4 = conn.execute(
        "SELECT id FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_force_test') AND superseded_by IS NULL"
    ).fetchall()
    assert len(rows_4) == 1
    assert rows_4[0]["id"] == rows_3[2]["id"]  # Same row returned, no new row created
