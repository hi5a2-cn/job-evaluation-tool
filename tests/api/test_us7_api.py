import json
from fastapi.testclient import TestClient
import pytest

from jet.llm.versions import DEFAULT_PROMPT_VERSION
from tests.conftest import FakeLlmHelper


def test_profile_api_work_preference_and_background(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # 1. Successful PUT with work_preference and background
    payload = {
        "directions": ["数据分析", "算法"],
        "keywords": ["Python", "SQL"],
        "cities": ["深圳"],
        "min_monthly_k": 20.0,
        "work_preference": "希望做核心业务数据分析，不想做对接客户背业绩的工作",
        "background": "统计学本科，3年互联网数据分析经验",
    }
    resp = client.put("/v1/profile", json=payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["version_no"] == 1
    assert resp.json()["changed"] is True

    # 2. GET returns the new fields and version_no
    get_resp = client.get("/v1/profile", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["version_no"] == 1
    assert data["work_preference"] == payload["work_preference"]
    assert data["background"] == payload["background"]

    # 3. Work preference exceeding 500 characters -> 422
    too_long = "a" * 501
    bad_resp_1 = client.put("/v1/profile", json=dict(payload, work_preference=too_long), headers=headers)
    assert bad_resp_1.status_code == 422
    assert bad_resp_1.json()["error"] == "invalid_payload"

    # 4. Background exceeding 500 characters -> 422
    bad_resp_2 = client.put("/v1/profile", json=dict(payload, background=too_long), headers=headers)
    assert bad_resp_2.status_code == 422
    assert bad_resp_2.json()["error"] == "invalid_payload"


def test_v2_judgement_output_and_facts_annotation(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={
            "directions": ["后端开发"],
            "cities": ["深圳"],
            "work_preference": "微服务研发",
            "background": "本科2年经验",
        },
        headers=headers,
    )

    desc = "岗位职责：\n1. 负责 FastAPI 微服务开发与维护；\n2. 具备 2 年以上 Python 经验；\n3. 团队周末双休不加班。"
    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_us7_detail",
                "title": "Python研发工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": desc,
            }
        ],
    }

    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    assert app.state.worker.wait_idle() is True

    # Fetch Judgement
    j_resp = client.get("/v1/judgements?ids=job_us7_detail", headers=headers)
    assert j_resp.status_code == 200
    judgement = j_resp.json()["jobs"]["job_us7_detail"]["judgement"]

    assert judgement["status"] == "done"
    assert judgement["verdict"] == "apply"
    assert judgement["source"] == "llm"
    assert judgement["prompt_version"] == DEFAULT_PROMPT_VERSION
    assert judgement["verdict_reason"] is not None
    assert judgement["facts"]["experience"]["requirement_type"] is not None
    assert judgement["engine"] == "deepseek-flash:no-think"
    assert judgement["facts"] is not None
    assert judgement["derivation"] is not None
    assert len(judgement["derivation"]) > 0
    assert judgement["stale"]["method_changed"] is False


def test_method_changed_and_manual_rejudge(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # Setup profile and job
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
                "platform_job_id": "job_method_stale",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "系统研发",
            }
        ],
    }
    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    # Force the existing judgement to be prompt_version='v2'
    conn.execute("UPDATE judgements SET prompt_version = 'v2' WHERE user_id = 'me'")

    # Query judgements: method_changed is True because settings has prompt_version='v3'
    resp = client.get("/v1/judgements?ids=job_method_stale", headers=headers)
    j = resp.json()["jobs"]["job_method_stale"]["judgement"]
    assert j["stale"]["method_changed"] is True

    # Re-observe the job while it is marked "不考虑": NO auto re-judgement（FR-052：skipped / applied 不自动更新；
    # 自动更新本身见 tests/api/test_auto_refresh.py）
    assert client.put("/v1/jobs/job_method_stale/status", json={"status": "skipped"}, headers=headers).status_code == 200
    call_count_before = fake_llm.call_count
    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == call_count_before

    # Manual rejudge triggers new judgement and supersedes
    rejudge_resp = client.post("/v1/jobs/job_method_stale/judge", headers=headers)
    assert rejudge_resp.status_code == 200
    assert app.state.worker.wait_idle() is True

    # New judgement is fresh
    resp2 = client.get("/v1/judgements?ids=job_method_stale", headers=headers)
    j2 = resp2.json()["jobs"]["job_method_stale"]["judgement"]
    assert j2["status"] == "done"
    assert j2["prompt_version"] == DEFAULT_PROMPT_VERSION
    assert j2["stale"]["method_changed"] is False


def test_put_label_api_lifecycle_and_status(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"]},
        headers=headers,
    )

    # 1. Ingest 1 detail job and 1 list job
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_label_test",
                "title": "Python后端开发",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "后端接口研发",
            }
        ],
    }
    client.post("/v1/observations", json=detail_payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_label_list",
                "title": "Python仅列表",
                "city": "深圳",
            }
        ],
    }
    client.post("/v1/observations", json=list_payload, headers=headers)

    # 2. Labeling non-existent job -> 404
    r_404 = client.put("/v1/jobs/non_existent/label", json={"work_type": "数据"}, headers=headers)
    assert r_404.status_code == 404
    assert r_404.json()["error"] == "not_found"

    # 3. Labeling list_only job -> 409
    r_409 = client.put("/v1/jobs/job_label_list/label", json={"work_type": "数据"}, headers=headers)
    assert r_409.status_code == 409
    assert r_409.json()["error"] == "list_only"

    # 4. Labeling with all nulls -> 422
    r_422 = client.put("/v1/jobs/job_label_test/label", json={}, headers=headers)
    assert r_422.status_code == 422
    assert r_422.json()["error"] == "invalid_payload"

    # 5. Labeling with invalid option -> 422
    r_422_bad = client.put("/v1/jobs/job_label_test/label", json={"work_type": "不存在"}, headers=headers)
    assert r_422_bad.status_code == 422

    # 5a. secondary_work_types with invalid option -> 422
    r_422_sec_invalid = client.put(
        "/v1/jobs/job_label_test/label",
        json={"secondary_work_types": ["非法类型"]},
        headers=headers,
    )
    assert r_422_sec_invalid.status_code == 422

    # 5b. work_subtype without work_type -> 422
    r_422_sub_no_main = client.put(
        "/v1/jobs/job_label_test/label",
        json={"work_subtype": "技术支持与实施"},
        headers=headers,
    )
    assert r_422_sub_no_main.status_code == 422

    # 5c. work_subtype not belonging to work_type -> 422
    r_422_sub_mismatch = client.put(
        "/v1/jobs/job_label_test/label",
        json={"work_type": "运营", "work_subtype": "技术支持与实施"},
        headers=headers,
    )
    assert r_422_sub_mismatch.status_code == 422

    # 6. Successful label save with secondary_work_types
    label_req = {
        "work_type": "市场与销售",
        "work_subtype": "销售与商务拓展",
        "secondary_work_types": [{"category": "运营", "subtype": None}],
        "sales_level": "高",
        "experience_fit": "差一点",
        "work_intensity": "高强度",
        "overall": "skip",
        "note": "实测要求电话销售",
    }
    r_success = client.put("/v1/jobs/job_label_test/label", json=label_req, headers=headers)
    assert r_success.status_code == 200
    res_data = r_success.json()

    # Verify response structure per contracts/local-api.md
    assert "jobs" in res_data
    assert "labels" in res_data
    assert res_data["labels"] == {"total": 1, "with_overall": 1}

    judgement = res_data["jobs"]["job_label_test"]["judgement"]
    assert judgement["label"] is not None
    assert judgement["label"]["work_type"] == "市场与销售"
    assert judgement["label"]["work_subtype"] == "销售与商务拓展"
    assert judgement["label"]["secondary_work_types"] == [{"category": "运营", "subtype": None}]
    assert judgement["label"]["sales_level"] == "高"
    assert judgement["label"]["work_intensity"] == "高强度"
    assert judgement["label"]["overall"] == "skip"
    assert judgement["label"]["note"] == "实测要求电话销售"
    # LLM judged it as apply/数据与技术, but user labelled as skip/市场与销售 -> corrected is True!
    assert judgement["label"]["corrected"] is True

    # 7. Check /v1/status reflects labels count
    status_resp = client.get("/v1/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["labels"] == {"total": 1, "with_overall": 1}


def test_v2_judgement_unsure_verdict(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["运营"], "cities": ["深圳"]},
        headers=headers,
    )

    fake_llm.set_check(["职责混杂，需与HR进一步核实销售比例"])

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_unsure_test",
                "title": "社群运营",
                "salary_raw": "10-15K",
                "city": "深圳",
                "description": "社群日常运营与用户维护",
            }
        ],
    }

    client.post("/v1/observations", json=payload, headers=headers)
    assert app.state.worker.wait_idle() is True

    j_resp = client.get("/v1/judgements?ids=job_unsure_test", headers=headers)
    assert j_resp.status_code == 200
    judgement = j_resp.json()["jobs"]["job_unsure_test"]["judgement"]

    assert judgement["status"] == "done"
    assert judgement["verdict"] == "check"
    assert judgement["source"] == "llm"
    assert judgement["prompt_version"] == DEFAULT_PROMPT_VERSION
    assert judgement["verdict_reason"] == "职责混杂，需与HR进一步核实销售比例"
    assert judgement["derivation"] == ["职责混杂，需与HR进一步核实销售比例"]


def test_v2_explicit_judgement_output(settings, fake_llm: FakeLlmHelper):
    """When configured with prompt_version='v2', returns v2 format and no verdict_reason."""
    import dataclasses
    from jet.api.app import create_app

    v2_settings = dataclasses.replace(settings, llm_api_key="test-api-key", prompt_version="v2")
    app = create_app(v2_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        code = app.state.pairing_codes.issue()
        resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": "chrome-extension://testextid"})
        headers = {"Authorization": f"Bearer {resp.json()['token']}", "Origin": "chrome-extension://testextid"}
        client.put("/v1/profile", json={"directions": ["后端开发"], "cities": ["深圳"]}, headers=headers)
        payload = {
            "page_type": "detail",
            "observed_at": "2026-09-24T12:00:00Z",
            "jobs": [{"platform_job_id": "job_v2_explicit", "title": "Dev", "city": "深圳", "description": "Python开发"}],
        }
        client.post("/v1/observations", json=payload, headers=headers)
        assert app.state.worker.wait_idle() is True
        j_resp = client.get("/v1/judgements?ids=job_v2_explicit", headers=headers)
        judgement = j_resp.json()["jobs"]["job_v2_explicit"]["judgement"]
        assert judgement["prompt_version"] == "v2"
        assert judgement["verdict_reason"] is None
        assert "requirement_type" not in (judgement["facts"]["experience"] or {})


def test_hr_note_crud_and_propagation(llm_client: tuple[TestClient, dict[str, str]]):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["深圳"]},
        headers=headers,
    )

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_hr_note_1",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "后端研发",
            }
        ],
    }
    obs_resp = client.post("/v1/observations", json=payload, headers=headers)
    assert obs_resp.status_code == 200
    assert obs_resp.json()["jobs"]["job_hr_note_1"]["hr_note"] is None
    assert app.state.worker.wait_idle() is True

    # 1. 404 for nonexistent job
    r_404 = client.put("/v1/jobs/nonexistent_job/hr-note", json={"note": "hello"}, headers=headers)
    assert r_404.status_code == 404

    # 2. 422 for note > 200 characters
    r_422 = client.put("/v1/jobs/job_hr_note_1/hr-note", json={"note": "x" * 201}, headers=headers)
    assert r_422.status_code == 422

    # 3. Successful save
    note_text = "HR说每周只有半天对接银行，没有业绩指标"
    r_save = client.put("/v1/jobs/job_hr_note_1/hr-note", json={"note": note_text}, headers=headers)
    assert r_save.status_code == 200
    assert r_save.json()["jobs"]["job_hr_note_1"]["hr_note"] == note_text

    # 4. Check GET /v1/judgements returns hr_note
    j_resp = client.get("/v1/judgements?ids=job_hr_note_1", headers=headers)
    assert j_resp.status_code == 200
    assert j_resp.json()["jobs"]["job_hr_note_1"]["hr_note"] == note_text

    # 5. Clear note by sending empty or whitespace
    r_clear = client.put("/v1/jobs/job_hr_note_1/hr-note", json={"note": "   "}, headers=headers)
    assert r_clear.status_code == 200
    assert r_clear.json()["jobs"]["job_hr_note_1"]["hr_note"] is None

    # Verify cleared in GET /v1/judgements
    j_resp2 = client.get("/v1/judgements?ids=job_hr_note_1", headers=headers)
    assert j_resp2.json()["jobs"]["job_hr_note_1"]["hr_note"] is None


def test_hr_questions_in_judgement(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    client, headers = llm_client
    app = client.app

    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["深圳"]},
        headers=headers,
    )

    # 1. Check verdict returns hr_questions
    fake_llm.set_check(["部分职责存疑"])
    payload1 = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_q_check",
                "title": "Python工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "后端研发",
            }
        ],
    }
    client.post("/v1/observations", json=payload1, headers=headers)
    assert app.state.worker.wait_idle() is True

    j1 = client.get("/v1/judgements?ids=job_q_check", headers=headers).json()["jobs"]["job_q_check"]["judgement"]
    assert j1["verdict"] == "check"
    assert isinstance(j1["hr_questions"], list)
    assert len(j1["hr_questions"]) >= 1

    # 2. Apply verdict has hr_questions as None
    fake_llm.set_apply(["各项符合"])
    payload2 = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_q_apply",
                "title": "Python架构师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "架构设计",
            }
        ],
    }
    client.post("/v1/observations", json=payload2, headers=headers)
    assert app.state.worker.wait_idle() is True

    j2 = client.get("/v1/judgements?ids=job_q_apply", headers=headers).json()["jobs"]["job_q_apply"]["judgement"]
    assert j2["verdict"] == "apply"
    assert j2["hr_questions"] is None


def test_risk_finance_jd_rule_and_risk_signals(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    from pathlib import Path

    client, headers = llm_client
    app = client.app

    jd_path = Path(__file__).resolve().parent.parent / "fixtures" / "boss" / "risk_finance_jd.txt"
    jd_text = jd_path.read_text(encoding="utf-8")

    # Profile excludes "销售"
    client.put(
        "/v1/profile",
        json={
            "directions": ["运营"],
            "cities": ["深圳"],
            "exclude_keywords": ["销售"],
        },
        headers=headers,
    )

    # Job title is "互联网金融" -> NOT excluded by rule even though desc mentions "销售"
    fake_llm.set_risk_signals(
        signals=[
            {"type": "非法金融", "description": "按交易手数提成", "quote": "每天600手为标准"},
            {"type": "其他", "description": "年龄要求过窄", "quote": "年龄要求18-26"},
        ],
        verdict="skip",
        reason="命中非法金融风险信号",
    )

    payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_risk_finance",
                "title": "互联网金融",
                "salary_raw": "6-10K",
                "city": "深圳",
                "description": jd_text,
            }
        ],
    }

    obs_resp = client.post("/v1/observations", json=payload, headers=headers)
    assert obs_resp.status_code == 200
    # Immediate response should NOT be rule-excluded done; it should be queued for LLM
    imm_j = obs_resp.json()["jobs"]["job_risk_finance"]["judgement"]
    assert imm_j["status"] == "queued"
    # 排队中的判断还没有来源；关键是它没有被规则（正文里的"销售"）直接排除
    assert imm_j["source"] != "rule"

    assert app.state.worker.wait_idle() is True

    # Fetched judgement should be done from llm with verdict=skip and risk_signals
    j_resp = client.get("/v1/judgements?ids=job_risk_finance", headers=headers)
    assert j_resp.status_code == 200
    judgement = j_resp.json()["jobs"]["job_risk_finance"]["judgement"]

    assert judgement["status"] == "done"
    assert judgement["verdict"] == "skip"
    assert judgement["source"] == "llm"
    assert "risk_signals" in judgement["facts"]
    assert len(judgement["facts"]["risk_signals"]) == 2
    assert judgement["facts"]["risk_signals"][0]["type"] == "非法金融"
    assert judgement["facts"]["risk_signals"][0]["quote"]["found"] is True
    assert judgement["facts"]["risk_signals"][1]["quote"]["found"] is True
