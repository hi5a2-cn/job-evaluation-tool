"""End-to-end API integration tests for assist endpoints (T018).

Specs:
- specs/003-hr-assistant/spec.md (R1, R2, FR-009-FR-040, SC-004, SC-005)
- specs/003-hr-assistant/contracts/local-api.md (Section 1 & 5, error code table)
- specs/003-hr-assistant/tasks.md (T017, T018)
"""

import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient

from jet.db.store import open_db, utc_now
from tests.conftest import FakeLlmHelper


def _create_job_with_judgement(data_dir: Path, platform_job_id: str = "job-0000aaaa") -> int:
    """Helper to insert a full job with done judgement and hr_note into the test SQLite database."""
    conn = open_db(data_dir)
    try:
        now_str = utc_now()
        cur = conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
            "VALUES ('boss', ?, 'full', '某新能源科技公司', ?, ?)",
            (platform_job_id, now_str, now_str),
        )
        job_id = cur.lastrowid

        v_cur = conn.execute(
            "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, salary_parse_ok, city, content_hash, created_at) "
            "VALUES (?, 1, 'detail', '储能海外销售（驻尼日利亚等）', '25-35K', 1, 1, '深圳', 'hash_test_1', ?)",
            (job_id, now_str),
        )
        version_id = v_cur.lastrowid
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (version_id, job_id))

        facts_json = json.dumps({
            "summary": {"text": "西非市场海外销售，需驻外及渠道开拓"},
            "sales_level": {"value": "高"},
            "risk_signals": [{"description": "涉及长期驻外安全与补贴说明"}],
        }, ensure_ascii=False)
        hr_questions_json = json.dumps(["驻外期间的安全保障及具体常驻津贴政策是怎样的？"], ensure_ascii=False)

        p_row = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()
        if not p_row:
            p_cur = conn.execute(
                "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, min_monthly_k, exclude_keywords, created_at) "
                "VALUES ('me', 1, '[]', '[]', '[]', 0, '[]', ?)",
                (now_str,),
            )
            profile_id = p_cur.lastrowid
        else:
            profile_id = p_row["id"]

        conn.execute(
            "INSERT INTO judgements (user_id, job_id, job_version_id, profile_id, status, verdict, source, facts, hr_questions, rule_result, created_at, finished_at) "
            "VALUES ('me', ?, ?, ?, 'done', 'try', 'llm', ?, ?, '[]', ?, ?)",
            (job_id, version_id, profile_id, facts_json, hr_questions_json, now_str, now_str),
        )

        conn.execute(
            "INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) "
            "VALUES ('me', ?, 'HR告知前半年在深圳培训，后常驻西非办事处。', ?, ?)",
            (job_id, now_str, now_str),
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def test_assist_preview_unpaired_rejected(client: TestClient):
    """未配对 401 约定：POST /v1/chat/preview。"""
    resp = client.post("/v1/chat/preview", json={
        "encrypt_job_id": "job-1",
        "job_title": "测试职位",
        "company_name": "测试公司",
        "location_name": "北京",
        "hr_name": "王女士",
        "user_name": "李四",
        "messages": [],
    })
    assert resp.status_code == 401
    assert resp.json()["error"] == "unpaired"


def test_assist_generate_unpaired_rejected(client: TestClient):
    """未配对 401 约定：POST /v1/chat/generate。"""
    resp = client.post("/v1/chat/generate", json={
        "encrypt_job_id": "job-1",
        "job_title": "测试职位",
        "company_name": "测试公司",
        "location_name": "北京",
        "hr_name": "王女士",
        "user_name": "李四",
        "messages": [],
        "prompt_hash": "a" * 64,
    })
    assert resp.status_code == 401
    assert resp.json()["error"] == "unpaired"


def test_assist_preview_no_llm_and_no_llm_calls(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """preview 只脱敏组装算指纹，不调用大模型、不写 llm_calls。"""
    client, headers = llm_client
    payload = {
        "encrypt_job_id": "job-preview-1",
        "job_title": "储能海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {
                "sender": "我",
                "text": "您好，请问岗位还在招聘吗？",
                "is_self": True,
                "type": 3,
                "body_type": 1,
            },
            {
                "sender": "HR",
                "text": "在招的，这是我的电话 13800138000，微信同号，方便发份简历吗？",
                "is_self": False,
                "type": 1,
                "body_type": 1,
            },
        ],
    }

    resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 1. 契约字段检查
    assert "fields" in data
    assert "sanitized_prompt" in data
    assert "prompt_hash" in data
    assert len(data["prompt_hash"]) == 64
    assert data["mode"] == "reply"
    assert data["message_count"] == 2
    assert data["has_jet_judgement"] is False
    assert data["has_consent"] is False

    # 2. 脱敏验证：电话号码和姓名已被隐藏
    assert "13800138000" not in data["sanitized_prompt"]
    assert "刘女士" not in data["sanitized_prompt"]
    assert "张三" not in data["sanitized_prompt"]

    # 3. 验证未调用大模型、未记录 llm_calls
    assert fake_llm.call_count == 0
    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_preview_and_generate_same_hash(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """preview 与 generate 对同一输入组装得到的 prompt_hash 完全一致。"""
    client, headers = llm_client
    # 先同意
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-hash-check",
        "job_title": "海外技术支持",
        "company_name": "某太阳能科技公司",
        "location_name": "合肥",
        "hr_name": "王经理",
        "user_name": "李四",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "您好，在招的。", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # 0. 没有录入经历时，大模型返回不引用经历的话术 (referenced_experience_ids=[])
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "好的呀，在招的。", "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，简历已发您附件。", "referenced_experience_ids": []},
        ],
        questions=["请问具体对接哪些客户？"],
    )

    # 1. 获取 preview 指纹
    preview_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert preview_resp.status_code == 200
    preview_hash = preview_resp.json()["prompt_hash"]

    # 2. 用该指纹调用 generate，指纹比对通过且大模型调用成功
    gen_payload = {**payload, "prompt_hash": preview_hash}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 200
    assert fake_llm.call_count == 1


def test_assist_preview_self_name_unavailable(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """preview 读不到我方姓名时返回 422 self_name_unavailable，且不写 llm_calls。"""
    client, headers = llm_client

    # 1. user_name 为空字符串
    resp1 = client.post("/v1/chat/preview", json={
        "encrypt_job_id": "job-1",
        "job_title": "测试职位",
        "company_name": "测试公司",
        "location_name": "北京",
        "hr_name": "王女士",
        "user_name": "   ",
        "messages": [],
    }, headers=headers)
    assert resp1.status_code == 422
    assert resp1.json()["error"] == "self_name_unavailable"
    assert resp1.json()["message"] == "读不到你的姓名，暂时不能生成"

    # 2. user_name 缺失 (None)
    resp2 = client.post("/v1/chat/preview", json={
        "encrypt_job_id": "job-1",
        "job_title": "测试职位",
        "company_name": "测试公司",
        "location_name": "北京",
        "hr_name": "王女士",
        "user_name": None,
        "messages": [],
    }, headers=headers)
    assert resp2.status_code == 422
    assert resp2.json()["error"] == "self_name_unavailable"

    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_generate_consent_required(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """generate 检查顺序 1: 未同意时返回 403 consent_required，且 llm_calls 无新增。"""
    client, headers = llm_client

    payload = {
        "encrypt_job_id": "job-consent-check",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [],
        "prompt_hash": "b" * 64,
    }

    resp = client.post("/v1/chat/generate", json=payload, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == "consent_required"
    assert "尚未同意" in resp.json()["message"]

    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_generate_self_name_unavailable(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """generate 检查顺序 2: 读不到我的姓名时返回 422 self_name_unavailable，且 llm_calls 无新增。"""
    client, headers = llm_client
    # 先记录同意
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-name-check",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "",
        "messages": [],
        "prompt_hash": "c" * 64,
    }

    resp = client.post("/v1/chat/generate", json=payload, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "self_name_unavailable"
    assert resp.json()["message"] == "读不到你的姓名，暂时不能生成"

    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_generate_hash_mismatch(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """generate 检查顺序 3: 指纹不符返回 400 hash_mismatch，且无新增、fake_llm 未被调用。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-hash-tamper",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1}
        ],
        "prompt_hash": "tampered_hash_value_1234567890abcdef1234567890abcdef1234567890abcdef",
    }

    resp = client.post("/v1/chat/generate", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "hash_mismatch"
    assert fake_llm.call_count == 0

    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_generate_empty_or_none_hash_rejected(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """generate 检查顺序 3: prompt_hash 为空时返回 400 hash_mismatch，且不预占、不记录 llm_calls。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-empty-hash",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1}
        ],
        "prompt_hash": "",
    }

    resp = client.post("/v1/chat/generate", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "hash_mismatch"
    assert fake_llm.call_count == 0

    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_assist_generate_quota_exhausted(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """generate 检查顺序 4: 次数用尽返回 429 quota_exhausted，不调用大模型。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-quota-exhausted",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [],
    }

    # 1. 通过 preview 取得正确哈希
    preview_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert preview_resp.status_code == 200
    valid_hash = preview_resp.json()["prompt_hash"]

    # 2. 将用户当日 assist 上限设为 0
    conn = open_db(data_dir)
    conn.execute("UPDATE user_settings SET daily_assist_limit = 0 WHERE user_id = 'me'")
    conn.commit()
    conn.close()

    # 3. 触发 generate
    gen_payload = {**payload, "prompt_hash": valid_hash}
    resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert resp.status_code == 429
    assert resp.json()["error"] == "quota_exhausted"
    assert resp.json()["message"] == "今日生成次数已用完"
    assert fake_llm.call_count == 0


def test_assist_generate_normal_success_and_sc004(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """正常生成返回 2 个版本与问题，llm_calls 新增 1 条 purpose='assist' 且不含聊天与话术正文 (SC-004)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    # 1. 在本地岗位库录入该岗位和 Jet 判断
    job_id = _create_job_with_judgement(data_dir, platform_job_id="job-sc004-test")

    # 录入 1 条经历素材
    client.put("/v1/experience", json=[
        {"item_no": 1, "content": "主导西非市场逆变器渠道销售翻倍。"}
    ], headers=headers)

    chat_phrase_1 = "请问贵公司的海外技术助理还在招人吗？"
    chat_phrase_2 = "这是我的电话 13800138000，微信同号"
    suggestion_1 = "好的呀，我这就发您一份。请问西非业务情况如何？"
    suggestion_2 = "嗯嗯好的，稍后发您简历。想确认对接客户类型。"

    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": suggestion_1, "referenced_experience_ids": [1]},
            {"version": 2, "tone_desc": "沉稳专业", "text": suggestion_2, "referenced_experience_ids": [1]},
        ],
        questions=["想确认一下平时主要对接的是海外代理商还是终端客户？"],
    )

    base_payload = {
        "encrypt_job_id": "job-sc004-test",
        "job_title": "储能海外销售（驻尼日利亚等）",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": chat_phrase_1, "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": chat_phrase_2, "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # 2. 预览取得指纹
    prev_resp = client.post("/v1/chat/preview", json=base_payload, headers=headers)
    assert prev_resp.status_code == 200
    prompt_hash = prev_resp.json()["prompt_hash"]

    # 3. 生成话术
    gen_payload = {**base_payload, "prompt_hash": prompt_hash}
    resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 验证响应体
    assert data["company_name"] == "某新能源科技公司"
    assert data["job_title"] == "储能海外销售（驻尼日利亚等）"
    assert data["mode"] == "reply"
    assert data["mode_label"] == "建议回复"
    assert data["has_jet_judgement"] is True
    assert data["message_count"] == 2
    assert len(data["suggestions"]) == 2
    assert data["suggestions"][0]["text"] == suggestion_1
    assert data["suggestions"][0]["referenced_experience_ids"] == [1]
    assert data["suggestions"][1]["text"] == suggestion_2
    assert len(data["questions"]) == 1
    assert "quota_remaining" in data

    # 4. 验证 llm_calls 记录与 SC-004 隐私断言
    conn = open_db(data_dir)
    try:
        rows = conn.execute("SELECT * FROM llm_calls WHERE purpose = 'assist'").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["billed"] == 1
        assert row["outcome"] == "ok"
        assert row["provider"] != ""
        assert row["model"] != ""
        assert row["started_at"] != ""
        assert row["finished_at"] is not None
        assert row["input_tokens"] is not None and row["input_tokens"] > 0
        assert row["output_tokens"] is not None and row["output_tokens"] > 0
        assert row["cost_cny"] is not None and row["cost_cny"] > 0

        # SC-004 核心断言：遍历 llm_calls 该行所有文本列，绝不包含任何聊天原文与话术正文
        for key in row.keys():
            val = row[key]
            if isinstance(val, str):
                assert chat_phrase_1 not in val
                assert chat_phrase_2 not in val
                assert "13800138000" not in val
                assert suggestion_1 not in val
                assert suggestion_2 not in val
                assert "海外技术助理" not in val
    finally:
        conn.close()


def test_assist_generate_job_not_in_db(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """岗位不在本机岗位库时正常生成，has_jet_judgement=false (FR-011)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "您好，想先确认一下该岗位主要职责与性质？", "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，请问该岗位是否有销售指标要求？", "referenced_experience_ids": []},
        ],
        questions=["请问岗位的具体考核方式是怎样的？"],
    )

    payload = {
        "encrypt_job_id": "nonexistent-job-999",
        "job_title": "海外销售专员",
        "company_name": "新辉能源",
        "location_name": "广州",
        "hr_name": "陈经理",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
        ],
    }

    # 1. preview 显示无 Jet 判断
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert prev_data["has_jet_judgement"] is False

    # 2. generate 成功生成且 has_jet_judgement=False
    gen_payload = {**payload, "prompt_hash": prev_data["prompt_hash"]}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["has_jet_judgement"] is False
    assert len(gen_data["suggestions"]) == 2
    assert len(gen_data["questions"]) > 0


def test_assist_generate_waiting_hr_mode(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """我方最后发言且在等 HR 回复时，mode='waiting_hr'，话术为空只有提问 (FR-012, FR-014)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-waiting-hr",
        "job_title": "逆变器售前工程师",
        "company_name": "某电气科技有限公司",
        "location_name": "苏州",
        "hr_name": "赵女士",
        "user_name": "李四",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "在招的，方便发份简历吗？", "is_self": False, "type": 1, "body_type": 1},
            {"sender": "我", "text": "好的呀，简历已发送附件，您查收看看。", "is_self": True, "type": 1, "body_type": 1},
        ],
    }

    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert prev_data["mode"] == "waiting_hr"
    assert prev_data["mode_label"] == "正在等 HR 回复"

    gen_payload = {**payload, "prompt_hash": prev_data["prompt_hash"]}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["mode"] == "waiting_hr"
    assert gen_data["mode_label"] == "正在等 HR 回复"
    assert gen_data["suggestions"] == []
    assert len(gen_data["questions"]) > 0


def test_assist_generate_llm_failure_timeout(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """大模型超时时返回 504 llm_failed，llm_calls 记录 timeout 且计入次数 (billed=1)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-timeout-fail",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    # 模拟超时
    fake_llm.set_timeout()

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 504
    assert gen_resp.json()["error"] == "llm_failed"
    assert "超时" in gen_resp.json()["message"]

    # 校验计次
    conn = open_db(data_dir)
    try:
        rows = conn.execute("SELECT outcome, billed FROM llm_calls WHERE purpose = 'assist'").fetchall()
        assert len(rows) == 1
        assert rows[0]["outcome"] == "timeout"
        assert rows[0]["billed"] == 1
    finally:
        conn.close()


def test_assist_generate_llm_failure_http_500(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """大模型服务 500 时返回 502 llm_failed，llm_calls 记录 http_error 且计入次数 (billed=1)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-http-500-fail",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    # 模拟 HTTP 500
    fake_llm.set_http_500()

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 502
    assert gen_resp.json()["error"] == "llm_failed"

    conn = open_db(data_dir)
    try:
        rows = conn.execute("SELECT outcome, billed FROM llm_calls WHERE purpose = 'assist'").fetchall()
        assert len(rows) == 1
        assert rows[0]["outcome"] == "http_error"
        assert rows[0]["billed"] == 1
    finally:
        conn.close()


def test_assist_generate_all_suggestions_dropped(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """大模型返回非法经历引用导致全部话术被丢弃时返回 422 all_suggestions_dropped，且计次 (billed=1)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    # 模拟返回非法经历编号 888
    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我有非法经历编号。",
                "referenced_experience_ids": [888],
            }
        ],
        questions=[],
    )

    payload = {
        "encrypt_job_id": "job-drop-all",
        "job_title": "储能销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "HR", "text": "方便发简历吗？", "is_self": False, "type": 1, "body_type": 1}
        ],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 422
    assert gen_resp.json()["error"] == "all_suggestions_dropped"
    assert "没有可用的建议" in gen_resp.json()["message"]

    conn = open_db(data_dir)
    try:
        rows = conn.execute("SELECT outcome, billed FROM llm_calls WHERE purpose = 'assist'").fetchall()
        assert len(rows) == 1
        assert rows[0]["billed"] == 1
    finally:
        conn.close()


def test_assist_generate_unsupported_city_both_dropped_returns_200_with_questions_and_notice(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """经历素材为空、HR 问'目前是在深圳吗'、两个版本都含'我目前在深圳' -> 两个都被丢弃，但有未答事实和问题，返回 200 (FR-054, R2)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以沟通。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，我目前在深圳，希望能进一步交流。",
                "referenced_experience_ids": [],
            },
        ],
        questions=["想了解一下具体工作时间？"],
    )

    payload = {
        "encrypt_job_id": "job-city-drop-both",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 200
    data = gen_resp.json()
    assert data["suggestions"] == []
    assert data["questions"] == ["想了解一下具体工作时间？"]
    assert len(data["unanswered_facts"]) == 1
    assert data["unanswered_facts"][0]["name"] == "所在城市"
    assert data["dropped_summary"] == [
        {
            "reason": "unsupported_fact",
            "category": "所在城市",
            "count": 2,
            "text": "2 个版本因提到你资料里没有的'所在城市'被去掉",
        }
    ]


def test_assist_generate_unsupported_city_one_retained_with_unanswered_facts(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """经历素材为空，一个含'我目前在深圳'一个不含 -> 只留下不含的那个，unanswered_facts 含'所在城市'。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以沟通。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，贵公司的职位要求我已了解，随时可以沟通。",
                "referenced_experience_ids": [],
            },
        ],
        questions=["想了解团队规模？"],
    )

    payload = {
        "encrypt_job_id": "job-city-drop-one",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 200
    data = gen_resp.json()
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["text"] == "您好，贵公司的职位要求我已了解，随时可以沟通。"
    assert len(data["unanswered_facts"]) == 1
    assert data["unanswered_facts"][0] == {
        "name": "所在城市",
        "notice": "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
    }


def test_assist_generate_city_in_experience_retained_without_unanswered_facts(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """经历素材写明'目前在深圳'时，含'我目前在深圳'的话术保留，unanswered_facts 不含'所在城市'。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)
    client.put("/v1/experience", json=[{"item_no": 1, "content": "目前在深圳，从事海外储能业务"}], headers=headers)

    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以沟通。",
                "referenced_experience_ids": [1],
            },
        ],
        questions=["想了解团队规模？"],
    )

    payload = {
        "encrypt_job_id": "job-city-in-exp",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    prompt_hash = prev_resp.json()["prompt_hash"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 200
    data = gen_resp.json()
    assert len(data["suggestions"]) == 1
    assert "我目前在深圳" in data["suggestions"][0]["text"]
    assert data["unanswered_facts"] == []


def test_assist_generate_request_notice_positions(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """FR-055: 请求卡片在我最后一条消息之后 -> request_notice 含卡片文字；之前 -> null。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份简历，您查收看看。",
                "referenced_experience_ids": [],
            }
        ],
        questions=[],
    )

    # 1. 卡片在"我"最后一条消息之后 -> request_notice 包含卡片文字
    payload_after = {
        "encrypt_job_id": "job-req-after",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {
                "sender": "HR",
                "text": "我想要一份您的附件简历，您是否同意",
                "is_self": False,
                "type": 1,
                "body_type": 7,
            },
        ],
    }
    prev_after = client.post("/v1/chat/preview", json=payload_after, headers=headers)
    prompt_hash_after = prev_after.json()["prompt_hash"]

    resp_after = client.post(
        "/v1/chat/generate",
        json={**payload_after, "prompt_hash": prompt_hash_after},
        headers=headers,
    )
    assert resp_after.status_code == 200
    assert resp_after.json()["request_notice"] == "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝。话术按你同意来写，不同意的话请自己回复"

    # 2. 卡片在"我"最后一条消息之前 -> request_notice 为 null
    payload_before = {
        "encrypt_job_id": "job-req-before",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {
                "sender": "HR",
                "text": "我想要一份您的附件简历，您是否同意",
                "is_self": False,
                "type": 1,
                "body_type": 7,
            },
            {"sender": "我", "text": "好的，已同意并发送附件简历。", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "好的收到！", "is_self": False, "type": 1, "body_type": 1},
        ],
    }
    prev_before = client.post("/v1/chat/preview", json=payload_before, headers=headers)
    prompt_hash_before = prev_before.json()["prompt_hash"]

    resp_before = client.post(
        "/v1/chat/generate",
        json={**payload_before, "prompt_hash": prompt_hash_before},
        headers=headers,
    )
    assert resp_before.status_code == 200
    assert resp_before.json()["request_notice"] is None


def test_assist_preview_marks_hr_request_card(
    llm_client: tuple[TestClient, dict[str, str]],
):
    """提示词中请求卡片被标出【HR 请求卡片】。"""
    client, headers = llm_client
    payload = {
        "encrypt_job_id": "job-preview-card",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {
                "sender": "HR",
                "text": "我想要一份您的附件简历，您是否同意",
                "is_self": False,
                "type": 1,
                "body_type": 7,
            },
        ],
    }
    resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert resp.status_code == 200
    sanitized_prompt = resp.json()["sanitized_prompt"]
    assert "【HR 请求卡片】HR：我想要一份您的附件简历，您是否同意" in sanitized_prompt


def test_assist_generate_all_dropped_with_request_card_and_questions_success_200(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """经历素材为空，HR问所在城市，HR请求卡片在最后发言后，模型2个版本含城市但有2个问题 -> 200，内容不入库 (FR-054, FR-055)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    fake_text_1 = "好的呀，我目前在深圳，随时可以沟通。"
    fake_text_2 = "您好，我目前在深圳，希望能进一步交流。"
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": fake_text_1, "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": fake_text_2, "referenced_experience_ids": []},
        ],
        questions=["平时主要对接海外代理商还是终端客户？", "尼日利亚业务目前是刚起步还是已有成熟渠道？"],
    )

    payload = {
        "encrypt_job_id": "job-fact-drop-success",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
            {
                "sender": "HR",
                "text": "我想要一份您的附件简历，您是否同意",
                "is_self": False,
                "type": 1,
                "body_type": 7,
            },
        ],
    }
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prompt_hash = prev_resp.json()["prompt_hash"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prompt_hash}, headers=headers)
    assert gen_resp.status_code == 200
    data = gen_resp.json()

    assert data["suggestions"] == []
    assert len(data["questions"]) == 2
    assert any(f["name"] == "所在城市" for f in data["unanswered_facts"])
    assert data["request_notice"] is not None
    assert "我想要一份您的附件简历" in data["request_notice"]
    assert data["dropped_summary"] == [
        {
            "reason": "unsupported_fact",
            "category": "所在城市",
            "count": 2,
            "text": "2 个版本因提到你资料里没有的'所在城市'被去掉",
        }
    ]

    # 响应中不含"我目前在深圳"原文
    resp_text = json.dumps(data, ensure_ascii=False)
    assert "我目前在深圳" not in resp_text

    # llm_calls 中也不含"我目前在深圳"原文
    conn = open_db(data_dir)
    try:
        rows = conn.execute("SELECT * FROM llm_calls WHERE purpose = 'assist'").fetchall()
        assert len(rows) == 1
        for key in rows[0].keys():
            val = rows[0][key]
            if isinstance(val, str):
                assert "我目前在深圳" not in val
    finally:
        conn.close()


def test_assist_prompt_lists_unanswered_facts_do_not_answer_and_hash_consistent(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """提示词中列出了'所在城市'并包含占位与点名要求；预览与生成指纹仍相同 (FR-054)。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-prompt-unanswered-hash",
        "job_title": "海外销售",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # 1. 检查预览生成的 prompt 文本中包含要求
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    preview_prompt = prev_resp.json()["sanitized_prompt"]
    assert "所在城市" in preview_prompt
    assert "用【填写：类别名】占位，不得绕开" in preview_prompt
    assert "本次 HR 问到你资料里没有的事实（所在城市），话术必须正面回应，用【填写：类别名】占位，不得绕开。" in preview_prompt
    assert "【填写：所在城市】" in preview_prompt
    preview_hash = prev_resp.json()["prompt_hash"]

    # 2. 模拟合法话术（不提具体经历），调用 generate 验证哈希一致通过
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "好的呀，职位要求已了解。", "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，非常希望能进一步交流。", "referenced_experience_ids": []},
        ],
        questions=["请问团队目前的业务布局是怎样的？"],
    )
    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": preview_hash}, headers=headers)
    assert gen_resp.status_code == 200
    assert fake_llm.call_count == 1


def test_assist_generate_known_facts_excluded_and_not_stored(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """(a): 模型输出含 known_facts 时，返回给插件的 JSON 不含 known_facts 字段也不含其中任何文字；数据库 llm_calls 无新增文本。"""
    client, headers = llm_client
    client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)

    payload = {
        "encrypt_job_id": "job-known-facts-test",
        "job_title": "海外销售助理",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "在招的，双休包中餐。", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    preview_hash = prev_resp.json()["prompt_hash"]

    # 模型输出包含明确的 known_facts
    kf_text_1 = "深南大道大族激光科技中心"
    kf_text_2 = "试用期全薪待遇不变"

    custom_calls: list[httpx.Request] = []

    def custom_handler(request: httpx.Request) -> httpx.Response:
        custom_calls.append(request)
        fake_llm.call_count += 1
        fake_llm.requests.append(request)
        content = json.dumps(
            {
                "known_facts": [
                    f"办公地点在{kf_text_1}",
                    kf_text_2,
                ],
                "suggestions": [
                    {
                        "version": 1,
                        "tone_desc": "自然直接",
                        "text": "好的呀，我这就发您一份简历。",
                        "referenced_experience_ids": [],
                    },
                    {
                        "version": 2,
                        "tone_desc": "沉稳专业",
                        "text": "您好，非常希望能进一步交流。",
                        "referenced_experience_ids": [],
                    },
                ],
                "questions": ["想了解一下日常对接渠道？"],
                "experience_note": None,
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    fake_llm.handler = custom_handler

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": preview_hash}, headers=headers)
    assert gen_resp.status_code == 200
    data = gen_resp.json()

    # 0. 确认这次生成确实用的是上面带 known_facts 的假响应，否则后面的「不包含」断言测不到东西
    assert len(custom_calls) == 1
    assert data["suggestions"], "自定义响应里的话术应当返回"

    # 1. 响应 JSON 结构中不包含 known_facts 字段
    assert "known_facts" not in data

    # 2. 响应报文全文不包含 known_facts 中的任何文字
    assert kf_text_1 not in gen_resp.text
    assert kf_text_2 not in gen_resp.text

    # 3. 数据库 llm_calls 表记录无新增已知事实文本
    conn = open_db(data_dir)
    try:
        row = conn.execute("SELECT * FROM llm_calls WHERE purpose = 'assist' ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        for key in row.keys():
            val = str(row[key] or "")
            assert kf_text_1 not in val
            assert kf_text_2 not in val
            assert "known_facts" not in val
    finally:
        conn.close()


def test_assist_preview_and_generate_with_quote_context(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    data_dir: Path,
):
    """POST /v1/chat/preview 与 POST /v1/chat/generate 携带 mid 与 quote_id 预览和指纹比对一致且正常生成。"""
    client, headers = llm_client
    job_id = _create_job_with_judgement(data_dir, platform_job_id="job-quote-e2e")

    # 1. 记录同意
    client.post("/v1/chat/consent", json={"fields_version": 2}, headers=headers)

    payload = {
        "encrypt_job_id": "job-quote-e2e",
        "job_title": "储能海外销售（驻尼日利亚等）",
        "company_name": "某新能源科技公司",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {
                "sender": "我",
                "text": "请问还在招聘吗？",
                "is_self": True,
                "type": 1,
                "body_type": 1,
                "mid": 10001,
                "quote_id": None,
            },
            {
                "sender": "HR",
                "text": "在招的",
                "is_self": False,
                "type": 1,
                "body_type": 1,
                "mid": 10002,
                "quote_id": 10001,
            },
        ],
    }

    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert "HR（回复我：'请问还在招聘吗？'）：在招的" in prev_data["sanitized_prompt"]
    preview_hash = prev_data["prompt_hash"]

    # 2. 调用 generate
    gen_payload = {**payload, "prompt_hash": preview_hash}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["mode"] == "reply"

    sent_body = fake_llm.requests[-1].read().decode("utf-8")
    assert "HR（回复我：'请问还在招聘吗？'）：在招的" in sent_body
    assert "10001" not in sent_body
    assert "10002" not in sent_body
