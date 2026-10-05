import dataclasses
from fastapi.testclient import TestClient

from jet.api.app import create_app
from jet.config import Settings
from tests.conftest import FakeLlmHelper


def test_judgement_prompt_version_from_settings(settings: Settings, fake_llm: FakeLlmHelper):
    llm_settings = dataclasses.replace(
        settings,
        llm_api_key="test-api-key",
        review_enabled=False,
        prompt_version="v5",
        daily_llm_limit=0,
    )
    app = create_app(llm_settings, admin_secret="test-admin", llm_transport=fake_llm.transport)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        conn = app.state.conn
        conn.execute("UPDATE user_settings SET daily_llm_limit = 0 WHERE user_id = 'me'")

        # 配对以获得鉴权 headers
        pairing_codes = app.state.pairing_codes
        code = pairing_codes.issue()
        origin = "chrome-extension://testextid"
        pair_resp = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
        assert pair_resp.status_code == 200
        token = pair_resp.json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": origin,
        }

        # 1. 设置画像（深圳后端开发，排除销售）
        profile = {
            "directions": ["后端开发"],
            "cities": ["深圳"],
            "exclude_keywords": ["销售"],
        }
        prof_resp = client.put("/v1/profile", json=profile, headers=headers)
        assert prof_resp.status_code == 200

        # 2. 画像不要让岗位被规则排除；上报一个岗位详情后，
        #    断言数据库里这条判断 status 为 'quota_exhausted'、source 为空（NULL）、prompt_version 为 "v5"
        platform_job_id = "job_backend_v5"
        obs = {
            "page_type": "detail",
            "observed_at": "2026-09-25T10:00:00Z",
            "jobs": [
                {
                    "platform_job_id": platform_job_id,
                    "title": "后端开发工程师",
                    "salary_raw": "20-30K",
                    "city": "深圳",
                    "description": "负责后端微服务开发与架构，Golang/Python/Java",
                }
            ],
        }
        obs_resp = client.post("/v1/observations", json=obs, headers=headers)
        assert obs_resp.status_code == 200
        j_obs = obs_resp.json()["jobs"][platform_job_id]["judgement"]
        assert j_obs["status"] == "quota_exhausted"
        assert j_obs["source"] is None
        assert j_obs["prompt_version"] == "v5"

        row1 = conn.execute(
            "SELECT * FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = ?) AND superseded_by IS NULL",
            (platform_job_id,),
        ).fetchone()
        assert row1 is not None
        assert row1["status"] == "quota_exhausted"
        assert row1["source"] is None
        assert row1["prompt_version"] == "v5"

        # 3. 再对 POST /v1/jobs/{platform_job_id}/judge（force=True）触发的新判断做同样断言
        rejudge_resp = client.post(
            f"/v1/jobs/{platform_job_id}/judge",
            json={"force": True},
            headers=headers,
        )
        assert rejudge_resp.status_code == 200
        j_rejudge = rejudge_resp.json()["jobs"][platform_job_id]["judgement"]
        assert j_rejudge["status"] == "quota_exhausted"
        assert j_rejudge["source"] is None
        assert j_rejudge["prompt_version"] == "v5"

        row2 = conn.execute(
            "SELECT * FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = ?) AND superseded_by IS NULL",
            (platform_job_id,),
        ).fetchone()
        assert row2 is not None
        assert row2["status"] == "quota_exhausted"
        assert row2["source"] is None
        assert row2["prompt_version"] == "v5"

        # 4. 另加一条断言：被规则排除的岗位，其判断记录的 prompt_version 仍是 'rule'（证明规则判断没被改动）
        rule_job_id = "job_rule_exclude_sales"
        obs_rule = {
            "page_type": "detail",
            "observed_at": "2026-09-25T10:05:00Z",
            "jobs": [
                {
                    "platform_job_id": rule_job_id,
                    "title": "电话销售专员",
                    "salary_raw": "10-15K",
                    "city": "深圳",
                    "description": "负责客户电话沟通与销售拓展",
                }
            ],
        }
        obs_rule_resp = client.post("/v1/observations", json=obs_rule, headers=headers)
        assert obs_rule_resp.status_code == 200
        j_rule = obs_rule_resp.json()["jobs"][rule_job_id]["judgement"]
        assert j_rule["source"] == "rule"
        assert j_rule["verdict"] == "skip"
        assert j_rule["prompt_version"] == "rule"

        row_rule = conn.execute(
            "SELECT * FROM judgements WHERE job_id = (SELECT id FROM jobs WHERE platform_job_id = ?) AND superseded_by IS NULL",
            (rule_job_id,),
        ).fetchone()
        assert row_rule is not None
        assert row_rule["source"] == "rule"
        assert row_rule["prompt_version"] == "rule"
