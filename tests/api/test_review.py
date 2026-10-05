"""复核（2026-09-25）：B（关闭思考）给出 apply / try 时，用 C（开启思考）再判一次，只允许降档。"""
import dataclasses
import json
import threading

import httpx
from fastapi.testclient import TestClient

from jet.api.app import create_app
from jet.db.store import connect, init_db, reset_pending_reviews_to_failed, utc_now


def _v5_content(verdict: str, derivation: list[str] | str) -> str:
    hr_questions = ["实际对接客户的时间占多少？", "有没有业绩指标？"] if verdict in ("try", "check") else []
    return json.dumps(
        {
            "facts": {
                "summary": {"text": "白话职责概括", "quotes": []},
                "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": []},
                "sales_level": {"value": "中", "signals": []},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": verdict,
            "derivation": derivation,
            "verdict_reason": f"{verdict} 的一句话理由",
            "hr_questions": hr_questions,
        },
        ensure_ascii=False,
    )


class ModeTransport:
    """按请求体里的 thinking 开关返回不同结论；记录每种调用的次数。"""

    def __init__(self, first: str, review: str | None):
        self.first = first
        self.review = review  # None = 复核返回不合规内容
        self.calls = {"no_think": 0, "think": 0}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        if thinking:
            self.calls["think"] += 1
            if self.review is None:
                # 推导为空列表：解析失败（超过 5 条的情况自 2026-09-28 起改为截断保留，不再作为不合规示例）。
                content = _v5_content("check", [])
            else:
                content = _v5_content(self.review, ["复核推导一", "复核推导二"])
        else:
            self.calls["no_think"] += 1
            content = _v5_content(self.first, ["初判推导"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        )


class PausableTransport:
    """按请求体里的 thinking 开关返回不同结论；thinking 请求支持在返回前等待 Event，以便测试复核中途状态。"""

    def __init__(self, first: str, review: str):
        self.first = first
        self.review = review
        self.c_entered = threading.Event()
        self.c_proceed = threading.Event()
        self.calls = {"no_think": 0, "think": 0}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        if thinking:
            self.calls["think"] += 1
            self.c_entered.set()
            self.c_proceed.wait(timeout=5.0)
            content = _v5_content(self.review, ["复核推导一", "复核推导二"])
        else:
            self.calls["no_think"] += 1
            content = _v5_content(self.first, ["初判推导"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        )


def _run(settings, transport: ModeTransport, *, daily_limit: int = 50, review_enabled: bool = True):
    s = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v5", review_enabled=review_enabled)
    app = create_app(s, admin_secret="test-admin", llm_transport=httpx.MockTransport(transport.handler))
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        token = c.post("/v1/pair", json={"code": code}, headers={"Origin": origin}).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "Origin": origin}
        conn = connect(s.data_dir)
        conn.execute("UPDATE user_settings SET daily_llm_limit = ? WHERE user_id = 'me'", (daily_limit,))
        conn.close()
        assert c.put(
            "/v1/profile", json={"directions": ["运营"], "preferred_cities": ["深圳"]}, headers=headers
        ).status_code == 200
        obs = {
            "page_type": "detail",
            "observed_at": "2026-09-25T00:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "review_job",
                    "title": "产品运营",
                    "salary_raw": "10-15K",
                    "city": "深圳",
                    "description": "负责产品运营与数据分析",
                }
            ],
        }
        assert c.post("/v1/observations", json=obs, headers=headers).status_code == 200
        assert app.state.worker.wait_idle(5.0)
        j = c.get("/v1/judgements?ids=review_job", headers=headers).json()["jobs"]["review_job"]["judgement"]
        conn = connect(s.data_dir)
        used = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE purpose = 'judge' AND billed = 1"
        ).fetchone()[0]
        conn.close()
    return j, used


def test_review_downgrades_try_to_check(settings):
    t = ModeTransport(first="try", review="check")
    j, used = _run(settings, t)
    assert j["status"] == "done"
    assert j["verdict"] == "check"
    assert j["derivation"][0].startswith("复核（开启思考）认为应降档")
    assert "可以一试" in j["derivation"][0] and "需要确认" in j["derivation"][0]
    assert j["review"]["outcome"] == "downgraded"
    assert j["review"]["first_verdict"] == "try"
    assert "+review:" in j["engine"]
    assert t.calls == {"no_think": 1, "think": 1}
    assert used == 2  # 复核计入每日额度


def test_review_in_progress_and_downgrade(settings):
    """
    测试：B 返回 try 后、C 还没返回时，GET /v1/judgements 得到 status == "done"、
    verdict == "try"、review.outcome == "pending"；
    放行 C（返回 check）后变为 verdict == "check"、review.outcome == "downgraded"、推导首条含"降为「需要确认」"。
    """
    t = PausableTransport(first="try", review="check")
    s = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v5", review_enabled=True)
    app = create_app(s, admin_secret="test-admin", llm_transport=httpx.MockTransport(t.handler))
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        token = c.post("/v1/pair", json={"code": code}, headers={"Origin": origin}).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "Origin": origin}
        conn = connect(s.data_dir)
        conn.execute("UPDATE user_settings SET daily_llm_limit = 50 WHERE user_id = 'me'")
        conn.close()
        assert c.put(
            "/v1/profile", json={"directions": ["运营"], "preferred_cities": ["深圳"]}, headers=headers
        ).status_code == 200
        obs = {
            "page_type": "detail",
            "observed_at": "2026-09-25T00:00:00Z",
            "jobs": [
                {
                    "platform_job_id": "review_job",
                    "title": "产品运营",
                    "salary_raw": "10-15K",
                    "city": "深圳",
                    "description": "负责产品运营与数据分析",
                }
            ],
        }
        assert c.post("/v1/observations", json=obs, headers=headers).status_code == 200
        # 等待 C 收到请求并暂停（此时 B 已经运行完毕并写入 DB）
        assert t.c_entered.wait(timeout=5.0)

        # C 还没返回时查询：B 的完整结果已写入 done 与 try，review 为 pending
        resp = c.get("/v1/judgements?ids=review_job", headers=headers).json()
        j_pending = resp["jobs"]["review_job"]["judgement"]
        assert j_pending["status"] == "done"
        assert j_pending["verdict"] == "try"
        assert j_pending["review"]["outcome"] == "pending"
        assert j_pending["review"]["first_verdict"] == "try"

        # 放行 C
        t.c_proceed.set()
        assert app.state.worker.wait_idle(5.0)

        # C 完成后查询：更新为 check 与 downgraded，推导首条含"降为「需要确认」"
        resp_after = c.get("/v1/judgements?ids=review_job", headers=headers).json()
        j_final = resp_after["jobs"]["review_job"]["judgement"]
        assert j_final["status"] == "done"
        assert j_final["verdict"] == "check"
        assert j_final["review"]["outcome"] == "downgraded"
        assert j_final["review"]["first_verdict"] == "try"
        assert "降为「需要确认」" in j_final["derivation"][0]


def test_review_keeps_b_when_c_is_not_lower(settings):
    t = ModeTransport(first="try", review="apply")
    j, used = _run(settings, t)
    assert j["status"] == "done"
    assert j["verdict"] == "try"
    assert j["derivation"] == ["初判推导"]
    assert j["review"]["outcome"] == "kept"
    assert j["review"]["first_verdict"] == "try"
    assert used == 2


def test_review_same_verdict_keeps_b(settings):
    """C 返回相同档位 → review.outcome == 'kept'，verdict 不变。"""
    t = ModeTransport(first="try", review="try")
    j, used = _run(settings, t)
    assert j["status"] == "done"
    assert j["verdict"] == "try"
    assert j["derivation"] == ["初判推导"]
    assert j["review"]["outcome"] == "kept"
    assert j["review"]["first_verdict"] == "try"
    assert j["review"]["review_verdict"] == "try"
    assert used == 2


def test_review_failure_keeps_b(settings):
    """C 不合规（解析失败）→ review.outcome == 'failed'，verdict 不变。"""
    t = ModeTransport(first="apply", review=None)
    j, used = _run(settings, t)
    assert j["status"] == "done"
    assert j["verdict"] == "apply"
    assert j["derivation"] == ["初判推导"]
    assert j["review"]["outcome"] == "failed"
    assert j["review"]["error"]
    assert j["review"]["first_verdict"] == "apply"
    assert used == 2  # 不合规的复核调用也已计费


def test_no_review_for_check_or_skip(settings):
    t = ModeTransport(first="check", review="skip")
    j, used = _run(settings, t)
    assert j["status"] == "done"
    assert j["verdict"] == "check"
    assert j["review"] is None
    assert t.calls["think"] == 0
    assert used == 1


def test_review_skipped_when_quota_exhausted(settings):
    t = ModeTransport(first="apply", review="skip")
    j, used = _run(settings, t, daily_limit=1)
    assert j["status"] == "done"
    assert j["verdict"] == "apply"
    assert j["review"]["outcome"] == "quota_exhausted"
    assert j["review"]["first_verdict"] == "apply"
    assert t.calls["think"] == 0
    assert used == 1


def test_review_can_be_disabled(settings):
    t = ModeTransport(first="apply", review="skip")
    j, used = _run(settings, t, review_enabled=False)
    assert j["status"] == "done"
    assert j["verdict"] == "apply"
    assert j["review"] is None
    assert t.calls["think"] == 0


def _seed_pending_review(data_dir, platform_job_id: str, review: dict, verdict: str, derivation: list[str]) -> None:
    """按真实表结构准备一条"B 已出结论、复核未完成"的判断。"""
    init_db(data_dir)
    conn = connect(data_dir)
    now = utc_now()
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[\"运营\"]', '[]', '[\"深圳\"]', NULL, '[]', '', '', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', ?, 'full', NULL, ?, ?)",
        (platform_job_id, now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_min_k, "
        "salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', '产品运营', '10-15K', 1, 10, 15, 12, 1, '深圳', '运营工作', 'h1', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, source, verdict, "
        "prompt_version, engine, facts, derivation, verdict_reason, hr_questions, review, rule_result, "
        "origin, finished_at, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'llm', ?, 'v5', 'deepseek-flash:no-think', NULL, ?, '理由', '[]', ?, '{}', "
        "'initial', ?, ?)",
        (verdict, json.dumps(derivation, ensure_ascii=False), json.dumps(review, ensure_ascii=False), now, now),
    )
    conn.close()


def test_startup_resets_pending_review_to_failed(settings):
    """启动时把 pending 复核改为 failed 且 verdict 保持 B 的结论。"""
    s = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v5", review_enabled=True)
    _seed_pending_review(
        s.data_dir,
        "pending_job",
        {"outcome": "pending", "engine": "deepseek-flash:think", "first_verdict": "try"},
        "try",
        ["初判推导"],
    )

    app = create_app(s, admin_secret="test-admin")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        code = app.state.pairing_codes.issue()
        origin = "chrome-extension://testextid"
        token = c.post("/v1/pair", json={"code": code}, headers={"Origin": origin}).json()["token"]
        headers = {"Authorization": f"Bearer {token}", "Origin": origin}
        j = c.get("/v1/judgements?ids=pending_job", headers=headers).json()["jobs"]["pending_job"]["judgement"]
        assert j["status"] == "done"
        assert j["verdict"] == "try"
        assert j["derivation"] == ["初判推导"]
        assert j["review"]["outcome"] == "failed"
        assert j["review"]["error"] == "Jet 重启，复核未完成"
        assert j["review"]["engine"] == "deepseek-flash:think"
        assert j["review"]["first_verdict"] == "try"


def test_reset_pending_reviews_to_failed_direct(settings):
    """直接测试 reset_pending_reviews_to_failed：保留其余字段，只把 review 改为 failed。"""
    review = {"outcome": "pending", "engine": "eng1", "first_verdict": "apply", "custom_key": 42}
    _seed_pending_review(settings.data_dir, "pending_job_2", review, "apply", ["初判推导"])
    conn = connect(settings.data_dir)
    count = reset_pending_reviews_to_failed(conn)
    assert count == 1
    row = conn.execute("SELECT review, verdict, status FROM judgements WHERE id = 1").fetchone()
    assert row["status"] == "done"
    assert row["verdict"] == "apply"
    rev = json.loads(row["review"])
    assert rev["outcome"] == "failed"
    assert rev["error"] == "Jet 重启，复核未完成"
    assert rev["engine"] == "eng1"
    assert rev["first_verdict"] == "apply"
    assert rev["custom_key"] == 42
    # 再执行一次不再改动
    assert reset_pending_reviews_to_failed(conn) == 0
    conn.close()
