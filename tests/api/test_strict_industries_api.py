"""API tests for strict industries endpoints GET /v1/strict-industries and PUT /v1/strict-industries (Phase 7: T030-T034).

Specs:
- specs/006-resume-suggestion/spec.md (US1 场景 4, US4, FR-010-FR-014)
- specs/006-resume-suggestion/tasks.md (Phase 7: T030-T034)
"""

from fastapi.testclient import TestClient

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.domain.industry import read_strict_industries
from jet.llm.versions import STALE_METHOD_BASELINE



def test_strict_industries_api_unpaired_rejected(client: TestClient):
    """未配对 401 约定。"""
    # 1. GET /v1/strict-industries
    resp1 = client.get("/v1/strict-industries")
    assert resp1.status_code == 401
    assert resp1.json()["error"] == "unpaired"

    # 2. PUT /v1/strict-industries
    resp2 = client.put("/v1/strict-industries", json={"selected": []})
    assert resp2.status_code == 401
    assert resp2.json()["error"] == "unpaired"


def test_get_strict_industries_default(paired_client: tuple[TestClient, dict[str, str]]):
    """新用户（新库初始化）查询从严行业：available 包含全部6个行业，selected 默认为空。"""
    client, headers = paired_client
    resp = client.get("/v1/strict-industries", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    expected_all = read_strict_industries()
    assert data["available"] == expected_all
    assert data["selected"] == []


def test_put_and_get_strict_industries_lifecycle(paired_client: tuple[TestClient, dict[str, str]]):
    """测试完整读写流程：保存部分行业 -> 查询验证规则文件顺序 -> 清空 -> 查询验证。"""
    client, headers = paired_client

    # 1. 写入子集（乱序传入 "房地产", "汽车"）
    put_resp = client.put(
        "/v1/strict-industries",
        json={"selected": ["房地产", "汽车"]},
        headers=headers,
    )
    assert put_resp.status_code == 200
    assert put_resp.json() == {"ok": True, "selected": ["汽车", "房地产"], "count": 2}

    # 2. 查询并验证顺序与规则文件一致（汽车在房地产之前）
    get_resp = client.get("/v1/strict-industries", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["selected"] == ["汽车", "房地产"]

    # 3. 清空行业
    put_empty_resp = client.put(
        "/v1/strict-industries",
        json={"selected": []},
        headers=headers,
    )
    assert put_empty_resp.status_code == 200
    assert put_empty_resp.json() == {"ok": True, "selected": [], "count": 0}

    # 4. 查询确认清空
    get_empty_resp = client.get("/v1/strict-industries", headers=headers)
    assert get_empty_resp.status_code == 200
    assert get_empty_resp.json()["selected"] == []


def test_put_strict_industries_invalid_rejected_422(paired_client: tuple[TestClient, dict[str, str]]):
    """PUT 包含规则文件不存在的行业时返回 422 拒绝。"""
    client, headers = paired_client

    resp = client.put(
        "/v1/strict-industries",
        json={"selected": ["汽车", "不存在的未知行业"]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_industry"
    assert "不存在的未知行业" in resp.json()["message"]


def test_put_strict_industries_does_not_affect_profile_version_or_staleness(
    paired_client: tuple[TestClient, dict[str, str]],
    settings: Settings,
):
    """修改勾选不得影响画像版本、不得使判断'可能过时'、不得触发自动重判。"""
    client, headers = paired_client

    # 1. 准备画像和已有判断
    conn = open_db(settings.data_dir)
    now = utc_now()
    try:
        user_row = conn.execute("SELECT user_id FROM pairings WHERE revoked_at IS NULL").fetchone()
        user_id = user_row["user_id"]

        # 插入 profile v1
        conn.execute(
            "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
            "VALUES (?, 1, '[]', '[]', '[]', '[]', '', '', ?)",
            (user_id, now),
        )
        profile_id = conn.execute("SELECT id FROM profiles WHERE user_id = ?", (user_id,)).fetchone()["id"]
        # 插入 job & version & judgement
        conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
            "VALUES ('boss', 'job_strict_test_1', 'full', ?, ?)",
            (now, now),
        )
        job_id = conn.execute("SELECT id FROM jobs WHERE platform_job_id = 'job_strict_test_1'").fetchone()["id"]
        conn.execute(
            "INSERT INTO job_versions (job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
            "VALUES (?, 1, 'detail', '运营主管', 1, 1, '深圳', '运营职责', 'hash_test_1', ?)",
            (job_id, now),
        )
        version_id = conn.execute("SELECT id FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()["id"]
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (version_id, job_id))
        conn.execute(
            "INSERT INTO judgements (user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, prompt_version, rule_result, created_at, superseded_by) "
            "VALUES (?, ?, ?, ?, 'done', 'apply', 'llm', '[]', ?, '{}', ?, NULL)",
            (user_id, job_id, version_id, profile_id, STALE_METHOD_BASELINE, now),
        )
        conn.commit()
    finally:
        conn.close()

    # 2. 修改从严行业勾选
    put_resp = client.put(
        "/v1/strict-industries",
        json={"selected": ["餐饮", "美妆"]},
        headers=headers,
    )
    assert put_resp.status_code == 200

    # 3. 校验 profiles 未产生新版本、行数不变
    conn_check = open_db(settings.data_dir)
    try:
        profile_rows = conn_check.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchall()
        assert len(profile_rows) == 1
        assert profile_rows[0]["version_no"] == 1

        # 4. 校验 judgements 状态保持 done，未被标为过时（superseded_by 仍为 NULL）
        judgements = conn_check.execute("SELECT * FROM judgements WHERE user_id = ?", (user_id,)).fetchall()
        assert len(judgements) == 1
        assert judgements[0]["status"] == "done"
        assert judgements[0]["superseded_by"] is None
    finally:
        conn_check.close()

    # 5. 通过接口读取该岗位判断，断言 stale 的三项均为 False
    get_resp = client.get("/v1/judgements?ids=job_strict_test_1", headers=headers)
    assert get_resp.status_code == 200
    judgement_entry = get_resp.json()["jobs"]["job_strict_test_1"]["judgement"]
    assert judgement_entry is not None
    assert judgement_entry["stale"] == {
        "job_changed": False,
        "profile_changed": False,
        "method_changed": False,
    }
