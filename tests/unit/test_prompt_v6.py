"""v6 提示词与简历建议测试 (FR-001–FR-004, T025–T027)。

覆盖：
1. v6 PROMPT_VERSION 标识；
2. 提示词只包含编号与适合岗位类型，绝不包含简历名称、真实人名与文件名；
3. 有效简历少于 2 份时，v6 生成的消息与 v5 逐字完全一致；
4. parse_facts 解析 slot 编号（整数/字符串）与理由截断，无效 slot 或异常返回 None；
5. JudgementWorker 端到端保存 slot 编号至 judgements.resume_direction，并由 to_api 动态解析；
6. 历史 v5 判断在默认配置 v6 下不过时、不触发自动重判；
7. run_llm_judgement 直接执行流程。
"""

import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.domain.judgements import is_method_changed, request_judgement, to_api
from jet.domain.profiles import save_profile
from jet.domain.resume import save_resumes
from jet.llm import prompt_v5, prompt_v6
from jet.llm.client import run_llm_judgement
from jet.llm.prompt import ParseError
from jet.llm.versions import DEFAULT_PROMPT_VERSION
from jet.worker import JudgementWorker


def test_v6_prompt_version():
    assert prompt_v6.PROMPT_VERSION == "v6"


def test_v6_messages_contain_slots_and_no_resume_names():
    """测试 v6 提示词包含编号与岗位类型，绝不包含简历名称与个人信息 (FR-003, SC-004)。"""
    profile = {
        "directions": ["AI", "运营"],
        "keywords": ["大模型"],
        "cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "AIGC产品运营",
        "company_name": "创新科技有限公司",
        "company_industry": "互联网",
        "salary_raw": "25-35K",
        "salary_visible": True,
        "city": "深圳",
        "description": "负责大模型提示词设计和用户社群运营，双休",
    }

    resumes = [
        {"slot": 1, "name": "简历A", "profile": "AI 产品运营、提示词设计"},
        {"slot": 2, "name": "简历B", "profile": "数据分析、指标搭建"},
        {"slot": 3, "name": "简历C", "profile": "算法研发、模型微调"},
    ]

    msgs = prompt_v6.build_messages(profile, job_version, resumes=resumes)
    assert len(msgs) == 2
    sys_content = msgs[0]["content"]
    user_content = msgs[1]["content"]

    # 1. 包含 3 份简历的编号与画像
    assert "14. 简历建议：" in sys_content
    assert "- 简历1：AI 产品运营、提示词设计" in sys_content
    assert "- 简历2：数据分析、指标搭建" in sys_content
    assert "- 简历3：算法研发、模型微调" in sys_content
    assert "简历1 / 简历2 / 简历3" in sys_content
    assert '"slot": 1|2|3' in sys_content
    assert "resume_suggestion" in sys_content

    # 2. 绝不包含简历名称（"简历A"、"简历B"、"简历C" 均不会发给大模型）
    assert "简历A" not in sys_content
    assert "简历B" not in sys_content
    assert "简历C" not in sys_content

    # 3. 绝不包含任何文件名
    all_text = sys_content + "\n" + user_content
    assert ".pdf" not in all_text

    # 4. User message 与 v5 完全相同
    msgs_v5 = prompt_v5.build_messages(profile, job_version)
    assert user_content == msgs_v5[1]["content"]


def test_v6_messages_identical_to_v5_when_fewer_than_two_resumes():
    """测试有效简历少于 2 份时，v6 生成的消息与 v5 逐字完全一致 (FR-010, T026)。"""
    profile = {
        "directions": ["Python"],
        "cities": ["深圳"],
        "min_monthly_k": 15.0,
    }
    job_version = {
        "title": "后端开发",
        "city": "深圳",
        "description": "系统开发",
    }
    msgs_v5 = prompt_v5.build_messages(profile, job_version)

    # 1. resumes 传 None
    msgs_v6_none = prompt_v6.build_messages(profile, job_version, resumes=None)
    assert msgs_v6_none == msgs_v5
    assert msgs_v6_none[0]["content"] == msgs_v5[0]["content"]
    assert msgs_v6_none[1]["content"] == msgs_v5[1]["content"]

    # 2. resumes 传空列表 []
    msgs_v6_empty = prompt_v6.build_messages(profile, job_version, resumes=[])
    assert msgs_v6_empty == msgs_v5

    # 3. resumes 传 1 份简历 (< 2)
    one_resume = [{"slot": 1, "name": "简历A", "profile": "AI 运营"}]
    msgs_v6_one = prompt_v6.build_messages(profile, job_version, resumes=one_resume)
    assert msgs_v6_one == msgs_v5


def test_v6_messages_skip_empty_profiles_and_omit_when_fewer_than_two():
    """测试跳过画像为空的简历；非空画像不足 2 份时不发送简历段落 (需求 6)。"""
    profile = {
        "directions": ["Python"],
        "cities": ["深圳"],
        "min_monthly_k": 15.0,
    }
    job_version = {
        "title": "后端开发",
        "city": "深圳",
        "description": "系统开发",
    }
    msgs_v5 = prompt_v5.build_messages(profile, job_version)

    # 1. 2 份简历中 1 份画像为空，另 1 份非空 -> 非空不足 2 份，不发送简历段落，与 v5 完全一致
    resumes_one_empty = [
        {"slot": 1, "name": "简历1", "profile": ""},
        {"slot": 2, "name": "简历2", "profile": "Python 后端开发"},
    ]
    assert prompt_v6.build_resume_instruction(resumes_one_empty) == ""
    msgs_res = prompt_v6.build_messages(profile, job_version, resumes=resumes_one_empty)
    assert msgs_res == msgs_v5

    # 2. 3 份简历中 2 份画像为空，只有 1 份非空 -> 同样不足 2 份，返回空
    resumes_two_empty = [
        {"slot": 1, "name": "简历1", "profile": "   "},
        {"slot": 2, "name": "简历2", "profile": None},
        {"slot": 3, "name": "简历3", "profile": "算法画像"},
    ]
    assert prompt_v6.build_resume_instruction(resumes_two_empty) == ""
    assert prompt_v6.build_messages(profile, job_version, resumes=resumes_two_empty) == msgs_v5

    # 3. 3 份简历中 1 份画像为空，另 2 份非空 -> 正确跳过空画像简历，只为非空的 2 份生成简历段落
    resumes_partial_empty = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端架构"},
        {"slot": 2, "name": "简历2", "profile": ""},
        {"slot": 3, "name": "简历3", "profile": "大模型应用工程"},
    ]
    instruction = prompt_v6.build_resume_instruction(resumes_partial_empty)
    assert "14. 简历建议：" in instruction
    assert "- 简历1：Python 后端架构" in instruction
    assert "- 简历3：大模型应用工程" in instruction
    assert "简历2" not in instruction
    assert "简历1 / 简历3" in instruction
    assert '"slot": 1|3' in instruction


def _make_v6_llm_json(
    verdict: str = "apply",
    slot: int | str | None = 1,
    reason: str | None = "岗位职责以AI提示词为主",
    omit_suggestion: bool = False,
    suggestion_not_dict: bool = False,
) -> str:
    data = {
        "facts": {
            "summary": {"text": "负责AI提示词与社群", "quotes": ["负责AI提示词与社群"]},
            "work_type": {"value": "运营", "subtype": "用户运营", "secondary": [], "quotes": ["负责AI提示词与社群"]},
            "sales_level": {"value": "低", "signals": []},
            "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
            "work_intensity": {"value": "双休", "quotes": []},
            "risk_signals": [],
        },
        "verdict": verdict,
        "derivation": ["职责契合用户运营偏好"],
        "verdict_reason": "职责符合AI用户运营方向",
        "hr_questions": [],
    }
    if not omit_suggestion:
        if suggestion_not_dict:
            data["resume_suggestion"] = "invalid_string_format"  # type: ignore[assignment]
        else:
            sug = {}
            if slot is not None:
                sug["slot"] = slot
            if reason is not None:
                sug["reason"] = reason
            data["resume_suggestion"] = sug
    return json.dumps(data, ensure_ascii=False)


def test_v6_parse_facts_scenarios():
    """测试 v6 parse_facts 解析：正常建议、slot 解析、非法 slot、空理由、理由截断、非对象容错。"""
    valid_slots = {1, 2}

    # 1. 正常有效建议（整数 slot）
    valid_json = _make_v6_llm_json(slot=1, reason="高度契合AI运营方向")
    facts, verdict, der, v_reason, hr_q, sug = prompt_v6.parse_facts(valid_json, valid_slots=valid_slots)
    assert verdict == "apply"
    assert sug == {
        "slot": 1,
        "reason": "高度契合AI运营方向",
    }

    # 2. 字符串 slot ("2" 或 "简历2")
    str_slot_json = _make_v6_llm_json(slot="2", reason="数据分析匹配")
    _, _, _, _, _, sug_str = prompt_v6.parse_facts(str_slot_json, valid_slots=valid_slots)
    assert sug_str == {
        "slot": 2,
        "reason": "数据分析匹配",
    }
    str_prefix_json = _make_v6_llm_json(slot="简历2", reason="数据分析匹配")
    _, _, _, _, _, sug_prefix = prompt_v6.parse_facts(str_prefix_json, valid_slots=valid_slots)
    assert sug_prefix == sug_str

    # 3. 未知 slot（不在 valid_slots 中） -> 视为无建议 None，不抛出 ParseError
    invalid_slot_json = _make_v6_llm_json(slot=3, reason="一些理由")
    _, _, _, _, _, sug_inv = prompt_v6.parse_facts(invalid_slot_json, valid_slots=valid_slots)
    assert sug_inv is None

    # 4. 空理由或纯空白理由 -> None
    empty_reason_json = _make_v6_llm_json(slot=1, reason="   ")
    _, _, _, _, _, sug_empty = prompt_v6.parse_facts(empty_reason_json, valid_slots=valid_slots)
    assert sug_empty is None

    # 5. 理由超过 40 字截断
    long_reason = "这是一个超过四十个汉字的超长推荐理由测试字符串用于验证截断逻辑是否严格生效并且不会抛出任何异常问题"
    assert len(long_reason) > 40
    long_reason_json = _make_v6_llm_json(slot=1, reason=long_reason)
    _, _, _, _, _, sug_trunc = prompt_v6.parse_facts(long_reason_json, valid_slots=valid_slots)
    assert sug_trunc is not None
    assert sug_trunc["slot"] == 1
    assert len(sug_trunc["reason"]) == 40
    assert sug_trunc["reason"] == long_reason[:40]

    # 6. 模型未返回 resume_suggestion 字段 -> None，不抛错
    omit_json = _make_v6_llm_json(omit_suggestion=True)
    _, _, _, _, _, sug_omit = prompt_v6.parse_facts(omit_json, valid_slots=valid_slots)
    assert sug_omit is None

    # 7. resume_suggestion 不是 dict（如字符串） -> None，不抛错
    not_dict_json = _make_v6_llm_json(suggestion_not_dict=True)
    _, _, _, _, _, sug_not_dict = prompt_v6.parse_facts(not_dict_json, valid_slots=valid_slots)
    assert sug_not_dict is None

    # 8. 非法 JSON / 缺少 facts 核心字段：照常抛出 ParseError
    with pytest.raises(ParseError):
        prompt_v6.parse_facts("{bad json")

    with pytest.raises(ParseError, match="缺失 facts"):
        prompt_v6.parse_facts(json.dumps({"verdict": "apply"}))


def test_v6_end_to_end_worker_saving_and_to_api(data_dir: Path, settings: Settings):
    """测试通过 JudgementWorker 保存 v6 判断并由 to_api 输出完整建议 (T026, T027)。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
        "VALUES (50, 'boss', 'job_v6_test', 'full', '某人工智能科技有限公司', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (500, 50, 1, 'detail', 'AI产品运营', '20-30K', 1, 20.0, 30.0, 12, 1, '北京', '大模型落地运营', 'hash_v6', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 500 WHERE id = 50")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (5, 'me', 1, '[\"AI\", \"运营\"]', '[\"AIGC\"]', '[\"北京\"]', 20.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (5000, 'me', 50, 500, 5, 'queued', '{}', ?)",
        (now_str,),
    )

    # 用户在设置页录入了 2 份简历（虚构名称）
    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "AI 产品运营、提示词设计"},
            {"slot": 2, "name": "简历B", "profile": "数据分析、用户增长"},
        ],
    )
    conn.close()

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        content = _make_v6_llm_json(
            verdict="apply",
            slot=1,
            reason="岗位是大模型落地与用户运营，契合该方向",
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 150, "completion_tokens": 40},
            },
        )

    transport = httpx.MockTransport(handler)
    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v6",
        review_enabled=False,
    )
    worker = JudgementWorker(worker_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(5000)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 1. 验证一次判断只调用一次大模型
    assert call_count == 1

    # 2. 验证数据库中正确保存了 slot 编号（字符串 "1"）与理由
    conn = open_db(data_dir)
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 5000").fetchone()
    assert j_row["status"] == "done"
    assert j_row["verdict"] == "apply"
    assert j_row["prompt_version"] == "v6"
    assert j_row["resume_direction"] == "1"
    assert j_row["resume_reason"] == "岗位是大模型落地与用户运营，契合该方向"

    # 3. 验证 to_api 动态解析当前简历设置
    api_res = to_api(conn, j_row)
    assert api_res["resume_suggestion"] == {
        "slot": 1,
        "name": "简历A",
        "reason": "岗位是大模型落地与用户运营，契合该方向",
    }
    conn.close()


def test_v5_judgement_stale_and_auto_rejudged_under_v8(data_dir: Path):
    """012 起过时基准为 v8：已有 v5 判断视为判断方式已变，详情页请求时自动排队用最新版本重判（原 FR-005 不过时的要求已撤销）。"""
    conn = open_db(data_dir)
    now_str = utc_now()

    # 创建岗位与画像
    p_id, _ = save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})
    prof = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (60, 'boss', 'job_v5_not_stale', 'full', NULL, ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (600, 60, 1, 'detail', 'Python专家', 1, 1, '深圳', '后端研发岗位', 'hash_600', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 600 WHERE id = 60")

    # 插入一个 v5 判断
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (6000, 'me', 60, 600, ?, 'done', 'apply', 'llm', '[]', 'v5', 'deepseek-flash:no-think', '{}', ?, ?)",
        (prof["id"], now_str, now_str),
    )

    j_v5 = conn.execute("SELECT * FROM judgements WHERE id = 6000").fetchone()

    # 1. 核心判定：基准下，is_method_changed 为 True
    assert is_method_changed(j_v5) is True

    # 2. to_api 的 stale 字典中 method_changed 为 True，其余为 False
    api_res = to_api(conn, j_v5)
    assert api_res["stale"]["method_changed"] is True
    assert api_res["stale"]["job_changed"] is False
    assert api_res["stale"]["profile_changed"] is False
    assert api_res["resume_suggestion"] is None

    # 3. 模拟详情页请求 request_judgement：新建 queued 自动重判，旧判断被取代
    job_row = conn.execute("SELECT * FROM jobs WHERE id = 60").fetchone()
    res_row, is_queued, notice = request_judgement(
        conn,
        user_id="me",
        job_row=job_row,
        prompt_version=DEFAULT_PROMPT_VERSION,
    )
    assert is_queued is True
    assert res_row["id"] != 6000
    assert res_row["status"] == "queued"
    assert res_row["prompt_version"] == DEFAULT_PROMPT_VERSION

    assert res_row["origin"] == "auto_refresh"
    assert notice is None
    old = conn.execute("SELECT superseded_by FROM judgements WHERE id = 6000").fetchone()
    assert old["superseded_by"] == res_row["id"]

    conn.close()


def test_v6_run_llm_judgement_direct(data_dir: Path, settings: Settings):
    """直接测试 run_llm_judgement 分派 v6 提示词与输出解析。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (70, 'boss', 'job_v6_direct', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (700, 70, 1, 'detail', '数据分析师', '18-25K', 1, 18.0, 25.0, 12, 1, '深圳', '负责业务数据指标搭建与分析', 'hash_700', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 700 WHERE id = 70")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (7, 'me', 1, '[\"数据分析\"]', '[]', '[\"深圳\"]', 15.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (7000, 'me', 70, 700, 7, 'queued', '{}', ?)",
        (now_str,),
    )

    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历A", "profile": "运营"},
            {"slot": 2, "name": "简历B", "profile": "数据分析"},
        ],
    )

    profile = {
        "id": 7,
        "user_id": user_id,
        "version_no": 1,
        "directions": ["数据分析"],
        "keywords": [],
        "cities": ["深圳"],
        "preferred_cities": ["深圳"],
        "excluded_cities": [],
        "min_monthly_k": 15.0,
        "exclude_keywords": [],
    }
    job_version = conn.execute("SELECT * FROM job_versions WHERE id = 700").fetchone()

    # 1. 有效建议
    def handler_valid(request: httpx.Request) -> httpx.Response:
        content = _make_v6_llm_json(
            verdict="apply",
            slot=2,
            reason="工作内容以数据指标与分析为主",
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    active_settings = dataclasses.replace(settings, llm_api_key="test-key", prompt_version="v6")
    outcome = run_llm_judgement(
        conn,
        active_settings,
        user_id=user_id,
        judgement_id=7000,
        profile=profile,
        job_version=job_version,
        transport=httpx.MockTransport(handler_valid),
        backoff_delays=(0, 0),
    )
    assert outcome.status == "done"
    assert outcome.prompt_version == "v6"
    assert outcome.verdict == "apply"
    assert outcome.resume_suggestion == {
        "slot": 2,
        "reason": "工作内容以数据指标与分析为主",
    }

    # 2. 无建议（缺失 resume_suggestion）仍正常完成
    def handler_no_sug(request: httpx.Request) -> httpx.Response:
        content = _make_v6_llm_json(verdict="try", omit_suggestion=True)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    outcome_no_sug = run_llm_judgement(
        conn,
        active_settings,
        user_id=user_id,
        judgement_id=7000,
        profile=profile,
        job_version=job_version,
        transport=httpx.MockTransport(handler_no_sug),
        backoff_delays=(0, 0),
    )
    assert outcome_no_sug.status == "done"
    assert outcome_no_sug.verdict == "try"
    assert outcome_no_sug.resume_suggestion is None

    conn.close()
