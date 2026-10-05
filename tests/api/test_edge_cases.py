import json
import logging
from pathlib import Path
from fastapi.testclient import TestClient
import httpx
import pytest

from jet.api.app import create_app
from jet.cli import main
from jet.config import Settings
from jet.db.store import connect, init_db, utc_now
from tests.conftest import FakeLlmHelper


def test_request_duration_log_and_header(
    paired_client: tuple[TestClient, dict[str, str]],
    caplog: pytest.LogCaptureFixture,
):
    """请求耗时日志（spec FR-056）：
    - 用 TestClient 请求 /v1/health 与带认证的 /v1/status，响应头有 X-Jet-Duration-Ms 且为非负数字；
    - 用 caplog 断言 /v1/status 有一条 INFO 日志，且日志文本不含 Bearer 令牌；
    - path 不含查询串；不打印请求体、令牌或任何个人内容。
    """
    client, headers = paired_client
    caplog.set_level(logging.INFO)

    # 1. 请求 /v1/health
    resp_health = client.get("/v1/health")
    assert resp_health.status_code == 200
    assert "X-Jet-Duration-Ms" in resp_health.headers
    dur_h = resp_health.headers["X-Jet-Duration-Ms"]
    assert dur_h.isdigit()
    assert int(dur_h) >= 0

    # 2. 带认证请求 /v1/status
    caplog.clear()
    token = headers["Authorization"].split("Bearer ")[1]
    resp_status = client.get("/v1/status", headers=headers)
    assert resp_status.status_code == 200
    assert "X-Jet-Duration-Ms" in resp_status.headers
    dur_s = resp_status.headers["X-Jet-Duration-Ms"]
    assert dur_s.isdigit()
    assert int(dur_s) >= 0

    # 3. 断言 /v1/status 有一条 INFO 日志
    status_records = [
        r for r in caplog.records
        if r.name == "jet.http"
        if r.levelname == "INFO" and "/v1/status" in r.getMessage()
    ]
    assert len(status_records) == 1
    log_msg = status_records[0].getMessage()

    # 格式：{method} {path} {status} {ms}ms
    parts = log_msg.split()
    assert parts[0] == "GET"
    assert parts[1] == "/v1/status"
    assert parts[2] == "200"
    assert parts[3].endswith("ms")
    ms_val = parts[3][:-2]
    assert ms_val.isdigit()
    assert int(ms_val) >= 0

    # 4. 断言日志文本不含 Bearer 令牌
    assert "Bearer" not in caplog.text
    assert token not in caplog.text

    # 5. 验证 path 不含查询串
    caplog.clear()
    resp_query = client.get("/v1/status?filter=all&page=1", headers=headers)
    assert resp_query.status_code == 200
    query_records = [
        r for r in caplog.records
        if r.name == "jet.http"
        if r.levelname == "INFO" and "/v1/status" in r.getMessage()
    ]
    assert len(query_records) == 1
    query_msg = query_records[0].getMessage()
    assert query_msg.split()[1] == "/v1/status"
    assert "filter=" not in query_msg
    assert "page=" not in query_msg


def test_cli_serve_logging_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """验证 jet serve 启动前配置 logging.getLogger('jet') 输出到终端（INFO 级别，格式 %(asctime)s %(message)s，时间精确到秒）。"""
    # 模拟端口检查时连接失败（表示端口可用）
    monkeypatch.setattr(
        "httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("Connection refused")),
    )
    called = {}

    def mock_run(app, **kwargs):
        called["app"] = app

    monkeypatch.setattr("uvicorn.run", mock_run)

    exit_code = main(["serve", "--data-dir", str(tmp_path)])
    assert exit_code == 0
    assert "app" in called

    jet_logger = logging.getLogger("jet")
    assert jet_logger.level == logging.INFO

    stream_handlers = [h for h in jet_logger.handlers if isinstance(h, logging.StreamHandler)]
    assert len(stream_handlers) >= 1
    handler = stream_handlers[0]
    assert handler.level == logging.INFO
    assert handler.formatter is not None
    assert handler.formatter._fmt == "%(asctime)s %(message)s"
    assert handler.formatter.datefmt == "%Y-%m-%d %H:%M:%S"


def test_edge_case_salary_empty_ingest_and_judge(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """边界情况：薪资为空入库 → job_versions.salary_visible = 0、salary_raw 为 NULL；判断时不按最低月薪排除；Judgement 对象 salary_visible == false。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. 设置画像：设置较高的最低月薪（25.0K），如果薪资参与规则排除会被过滤
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 25.0},
        headers=headers,
    )

    # 2. 薪资为 None 的情况
    obs_none = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_salary_none_01",
                "title": "Python高级开发工程师",
                "salary_raw": None,
                "city": "深圳",
                "description": "精通 Python 后端高并发架构",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=obs_none, headers=headers)
    assert resp1.status_code == 200

    # 验证数据库：job_versions.salary_visible = 0、salary_raw 为 NULL
    v_row1 = conn.execute(
        "SELECT v.salary_visible, v.salary_raw, v.salary_min_k, v.salary_max_k, v.salary_parse_ok "
        "FROM job_versions v JOIN jobs j ON j.current_version_id = v.id "
        "WHERE j.platform_job_id = 'job_salary_none_01'"
    ).fetchone()
    assert v_row1["salary_visible"] == 0
    assert v_row1["salary_raw"] is None
    assert v_row1["salary_min_k"] is None
    assert v_row1["salary_max_k"] is None
    assert v_row1["salary_parse_ok"] == 0

    # 判断时不按最低月薪排除：规则放行，worker 收到并完成判断
    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 1

    resp_j1 = client.get("/v1/judgements?ids=job_salary_none_01", headers=headers)
    j1 = resp_j1.json()["jobs"]["job_salary_none_01"]["judgement"]
    assert j1["status"] == "done"
    assert j1["verdict"] == "apply"
    # Judgement 对象 salary_visible == false
    assert j1["salary_visible"] is False

    # 3. 薪资为空字符串 "" 的情况
    obs_empty = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_salary_empty_02",
                "title": "Python研发工程师",
                "salary_raw": "",
                "city": "深圳",
                "description": "精通 Python 后端高并发架构",
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=obs_empty, headers=headers)
    assert resp2.status_code == 200

    v_row2 = conn.execute(
        "SELECT v.salary_visible, v.salary_raw, v.salary_parse_ok "
        "FROM job_versions v JOIN jobs j ON j.current_version_id = v.id "
        "WHERE j.platform_job_id = 'job_salary_empty_02'"
    ).fetchone()
    assert v_row2["salary_visible"] == 0
    assert v_row2["salary_raw"] is None
    assert v_row2["salary_parse_ok"] == 0

    assert app.state.worker.wait_idle() is True
    assert fake_llm.call_count == 2

    resp_j2 = client.get("/v1/judgements?ids=job_salary_empty_02", headers=headers)
    j2 = resp_j2.json()["jobs"]["job_salary_empty_02"]["judgement"]
    assert j2["status"] == "done"
    assert j2["verdict"] == "apply"
    assert j2["salary_visible"] is False


def test_edge_case_district_empty_string(paired_client: tuple[TestClient, dict[str, str]]):
    """边界情况：区域为空字符串 → district 为 NULL。"""
    client, headers = paired_client
    app = client.app
    conn = app.state.conn

    # 1. 列表页观察：district 为空字符串 ""
    obs_list = {
        "page_type": "list",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_district_empty_str",
                "title": "Python开发",
                "salary_raw": "15-25K",
                "city": "深圳",
                "district": "",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=obs_list, headers=headers)
    assert resp1.status_code == 200

    v_row1 = conn.execute(
        "SELECT v.district FROM job_versions v JOIN jobs j ON j.current_version_id = v.id "
        "WHERE j.platform_job_id = 'job_district_empty_str'"
    ).fetchone()
    assert v_row1["district"] is None

    # 2. 详情页观察：district 为空字符串 ""
    obs_detail = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_district_empty_detail",
                "title": "Python开发工程师",
                "salary_raw": "18-28K",
                "city": "深圳",
                "district": "",
                "description": "后台微服务架构开发",
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=obs_detail, headers=headers)
    assert resp2.status_code == 200

    v_row2 = conn.execute(
        "SELECT v.district FROM job_versions v JOIN jobs j ON j.current_version_id = v.id "
        "WHERE j.platform_job_id = 'job_district_empty_detail'"
    ).fetchone()
    assert v_row2["district"] is None

    # 3. 区域仅包含空格 "   "
    obs_spaces = {
        "page_type": "list",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_district_spaces",
                "title": "全栈工程师",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "   ",
            }
        ],
    }
    resp3 = client.post("/v1/observations", json=obs_spaces, headers=headers)
    assert resp3.status_code == 200

    v_row3 = conn.execute(
        "SELECT v.district FROM job_versions v JOIN jobs j ON j.current_version_id = v.id "
        "WHERE j.platform_job_id = 'job_district_spaces'"
    ).fetchone()
    assert v_row3["district"] is None


def test_edge_case_leftover_running_reset_to_interrupted_and_requeue(
    settings: Settings, fake_llm: FakeLlmHelper
):
    """边界情况：遗留 running：在数据目录里直接插一条 status='running' 的判断，再 create_app 启动 → 变为 interrupted；之后对该岗位再 detail 观察（或 POST /v1/jobs/{id}/judge）可以重新排队。"""
    # 1. 直接在数据库中插入一条 status='running' 的遗留判断
    init_db(settings.data_dir)
    conn = connect(settings.data_dir)
    now_str = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[]', '[\"深圳\"]', '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_running_leftover', 'full', NULL, ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_min_k, salary_max_k, salary_parse_ok, city, district, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python资深工程师', '20-30K', 1, 20.0, 30.0, 1, '深圳', '南山区', 'FastAPI服务架构', 'hash_leftover', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, prompt_version, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'running', 'v5', '{}', ?)",
        (now_str,),
    )
    conn.close()

    # 2. create_app 启动应用（lifespan 启动期间执行 reset_running_to_interrupted）
    import dataclasses

    # 需要 API Key 才会真正调用（假）大模型；关闭复核，使调用次数与断言一致
    run_settings = dataclasses.replace(settings, llm_api_key="test-key", review_enabled=False)
    app = create_app(run_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        # 验证数据库中遗留的 running 已变为 interrupted
        db_conn = app.state.conn
        row = db_conn.execute("SELECT status FROM judgements WHERE id = 1").fetchone()
        assert row["status"] == "interrupted"

        # 配对
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        pair_resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert pair_resp.status_code == 200
        token = pair_resp.json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": origin,
        }

        # 3. 对该岗位再次 detail 观察 → 重新排队
        obs = {
            "page_type": "detail",
            "observed_at": "2026-09-25T13:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "job_running_leftover",
                    "title": "Python资深工程师",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "district": "南山区",
                    "description": "FastAPI服务架构",
                }
            ],
        }
        obs_resp = client.post("/v1/observations", json=obs, headers=headers)
        assert obs_resp.status_code == 200
        j_obs = obs_resp.json()["jobs"]["job_running_leftover"]["judgement"]
        assert j_obs["status"] in ("queued", "done")

        # worker 执行完成
        assert app.state.worker.wait_idle() is True
        assert fake_llm.call_count == 1

        resp_check = client.get("/v1/judgements?ids=job_running_leftover", headers=headers)
        j_done = resp_check.json()["jobs"]["job_running_leftover"]["judgement"]
        assert j_done["status"] == "done"
        assert j_done["verdict"] == "apply"

        # 4. 验证 POST /v1/jobs/{id}/judge 亦可重新排队 interrupted 判断
        db_conn.execute("UPDATE judgements SET status = 'interrupted' WHERE id = 1")
        judge_resp = client.post("/v1/jobs/job_running_leftover/judge", json={}, headers=headers)
        assert judge_resp.status_code == 200
        j_judge = judge_resp.json()["jobs"]["job_running_leftover"]["judgement"]
        assert j_judge["status"] in ("queued", "done")

        assert app.state.worker.wait_idle() is True
        assert fake_llm.call_count == 2

        resp_check2 = client.get("/v1/judgements?ids=job_running_leftover", headers=headers)
        assert resp_check2.json()["jobs"]["job_running_leftover"]["judgement"]["status"] == "done"


def test_edge_case_zero_quota_rule_passed_quota_exhausted(
    llm_client: tuple[TestClient, dict[str, str]], fake_llm: FakeLlmHelper
):
    """边界情况：每日额度为 0 且规则通过时，判断为 quota_exhausted，rule_result 仍然保存（非空、可解析为 JSON，passed 为真）。"""
    client, headers = llm_client
    app = client.app
    conn = app.state.conn

    # 1. 设置用户画像
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 15.0},
        headers=headers,
    )

    # 2. 将今日大模型每日额度设为 0
    conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")

    # 3. 观察符合规则的岗位详情
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "job_quota_0_pass",
                "title": "Python高并发架构师",
                "salary_raw": "25-35K",
                "city": "深圳",
                "description": "分布式微服务与高并发系统研发",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200
    j = resp.json()["jobs"]["job_quota_0_pass"]["judgement"]

    # 判断状态为 quota_exhausted
    assert j["status"] == "quota_exhausted"
    assert fake_llm.call_count == 0  # 额度为 0，不应发起大模型调用

    # 检查数据库：rule_result 仍然保存（非空、可解析为 JSON，passed 为真）
    row = conn.execute(
        "SELECT status, rule_result FROM judgements "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_quota_0_pass')"
    ).fetchone()
    assert row["status"] == "quota_exhausted"
    assert row["rule_result"] is not None
    assert len(row["rule_result"].strip()) > 0
    rule_data = json.loads(row["rule_result"])
    assert isinstance(rule_data, dict)
    assert rule_data.get("passed") is True
    assert isinstance(rule_data.get("hits"), list)
    assert len(rule_data.get("hits")) == 0

    # 4. 验证 POST /v1/jobs/{id}/judge 重新排队时的表现
    conn.execute(
        "DELETE FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_quota_0_pass')"
    )
    rejudge_resp = client.post("/v1/jobs/job_quota_0_pass/judge", json={}, headers=headers)
    assert rejudge_resp.status_code == 200
    j_re = rejudge_resp.json()["jobs"]["job_quota_0_pass"]["judgement"]
    assert j_re["status"] == "quota_exhausted"

    row2 = conn.execute(
        "SELECT status, rule_result FROM judgements "
        "WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = 'job_quota_0_pass')"
    ).fetchone()
    assert row2["status"] == "quota_exhausted"
    assert row2["rule_result"] is not None
    rule_data2 = json.loads(row2["rule_result"])
    assert rule_data2.get("passed") is True
