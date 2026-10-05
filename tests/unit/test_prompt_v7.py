"""v7 提示词与经验门槛规则测试。

覆盖：
1. v7 PROMPT_VERSION 标识；
2. 系统提示词含规则 14 全文；
3. 有重点排查行业时规则 14 在规则 13 之后；
4. 有 2 份以上简历时规则 14 在简历说明之前；
5. 无重点排查行业时规则 14 在规则 12 之后；
6. v4/v5/v6 的 build_messages 输出与改动前一致（不含规则 14）；
7. 代码兜底：
   - skip + 无风险 + 理由含"需确认" → check 且 derivation 首条为兜底说明；
   - skip + 有风险信号 + 理由含"需确认" → 仍为 skip；
   - skip + 理由不含这些词 → 仍为 skip；
   - v6 解析不受影响；
8. 过时判断：012 起基准为 v8，prompt_version 为 v4、v5、v6 的 llm 判断 is_method_changed 均为 True；
9. JET_PROMPT_VERSION=v7 时配置可用、分派到 v7 并由 worker 保存至数据库。
"""

import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings, load_settings
from jet.db.store import open_db, utc_now
from jet.domain.judgements import is_method_changed, to_api
from jet.domain.profiles import save_profile
from jet.domain.resume import save_resumes
from jet.llm.versions import DEFAULT_PROMPT_VERSION
from jet.llm import prompt_v4, prompt_v5, prompt_v6, prompt_v7
from jet.llm.client import run_llm_judgement
from jet.worker import JudgementWorker


def test_v7_prompt_version():
    assert prompt_v7.PROMPT_VERSION == "v7"


def test_v7_system_prompt_contains_rule_14_full_text():
    """验证 v7 系统提示词包含规则 14 全文（逐字匹配）。"""
    sys_prompt = prompt_v7.SYSTEM_PROMPT
    assert "14. 经验门槛与结论：" in sys_prompt
    assert '硬性经验要求的年限下限不超过 1 年（如"半年以上""1 年以上""1-3 年"），或只写"有相关经验"而没有年限时：若方向对口且与偏好、底线无其他冲突，experience.value 判"差一点"，verdict 用 try，不得仅因经验门槛判 skip。' in sys_prompt
    assert '硬性经验要求的年限下限为 2 年（如"2 年以上""2-4 年"）时：verdict 用 check，不得仅因经验门槛判 skip。' in sys_prompt
    assert '硬性要求 3 年及以上，或要求带团队、管理经验时，才可仅因经验门槛判 skip。' in sys_prompt
    assert '以上前两种情况，hr_questions 都必须包含一个问题：是否接受应届生，或经验要求能否放宽。' in sys_prompt
    assert '届别限制、语言要求、执业资格等硬性资格不符时，仍可判 skip。' in sys_prompt
    assert 'verdict 为 skip 而 verdict_reason 写的是"需要确认""需确认""需要核实""需核实""待确认"时，改判 check。' in sys_prompt
    assert '给出 skip 之前先检查：如果唯一原因是经验年限下限 ≤ 1 年，或者没写年限的经验要求，不得判 skip。' in sys_prompt


def test_v7_messages_order_no_strict_industry():
    """无重点排查行业时，规则 14 接在规则 12（城市与薪资说明）后面。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }

    msgs = prompt_v7.build_messages(profile, job_version, strict_industries=[])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业" not in sys_content
    assert "14. 经验门槛与结论：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    assert idx_12 < idx_14


def test_v7_messages_order_with_strict_industry():
    """有重点排查行业时，规则 14 接在规则 13 后面。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }

    msgs = prompt_v7.build_messages(profile, job_version, strict_industries=["餐饮"])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业（餐饮）：" in sys_content
    assert "14. 经验门槛与结论：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_13 = sys_content.index("13. 重点排查行业（餐饮）：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    assert idx_12 < idx_13 < idx_14


def test_v7_messages_order_with_resumes_before_resume_instruction():
    """有 2 份以上简历时，规则 14 在简历说明段之前。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端开发"},
        {"slot": 2, "name": "简历2", "profile": "数据开发"},
    ]

    # 1. 包含重点排查行业 + 简历
    msgs_strict = prompt_v7.build_messages(
        profile,
        job_version,
        strict_industries=["房地产"],
        resumes=resumes,
    )
    sys_strict = msgs_strict[0]["content"]
    idx_13 = sys_strict.index("13. 重点排查行业（房地产）：")
    idx_14 = sys_strict.index("14. 经验门槛与结论：")
    idx_resume = sys_strict.index("候选人准备了以下几份不同侧重点的简历：")
    assert idx_13 < idx_14 < idx_resume

    # 2. 不包含重点排查行业 + 简历
    msgs_no_strict = prompt_v7.build_messages(
        profile,
        job_version,
        strict_industries=[],
        resumes=resumes,
    )
    sys_no_strict = msgs_no_strict[0]["content"]
    idx_12 = sys_no_strict.index("12. 城市与薪资说明：")
    idx_14_b = sys_no_strict.index("14. 经验门槛与结论：")
    idx_resume_b = sys_no_strict.index("候选人准备了以下几份不同侧重点的简历：")
    assert idx_12 < idx_14_b < idx_resume_b


def test_v4_v5_v6_build_messages_unaffected():
    """v4/v5/v6 的 build_messages 输出与改动前一致（对同一输入断言不含"14. 经验门槛与结论"）。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端开发"},
        {"slot": 2, "name": "简历2", "profile": "数据开发"},
    ]

    # v4
    msgs_v4 = prompt_v4.build_messages(profile, job_version)
    assert "14. 经验门槛与结论" not in msgs_v4[0]["content"]

    # v5
    msgs_v5 = prompt_v5.build_messages(profile, job_version)
    assert "14. 经验门槛与结论" not in msgs_v5[0]["content"]

    # v6 (无简历)
    msgs_v6_no_resume = prompt_v6.build_messages(profile, job_version)
    assert "14. 经验门槛与结论" not in msgs_v6_no_resume[0]["content"]

    # v6 (有简历)
    msgs_v6_resume = prompt_v6.build_messages(profile, job_version, resumes=resumes)
    assert "14. 经验门槛与结论" not in msgs_v6_resume[0]["content"]


def _make_llm_json(
    verdict: str = "skip",
    verdict_reason: str = "需要确认经验是否达标",
    risk_signals: list[dict[str, str]] | None = None,
    derivation: list[str] | None = None,
    slot: int | None = None,
    reason: str | None = None,
) -> str:
    data = {
        "facts": {
            "summary": {"text": "负责后端开发", "quotes": ["负责后端开发"]},
            "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": ["接口开发"]},
            "sales_level": {"value": "低", "signals": []},
            "experience": {"requirement": "1-3年", "requirement_type": "硬性", "value": "差一点", "gap": "年限略不足"},
            "work_intensity": {"value": "双休", "quotes": []},
            "risk_signals": risk_signals or [],
        },
        "verdict": verdict,
        "derivation": derivation if derivation is not None else ["经验要求1-3年，候选人背景略少"],
        "verdict_reason": verdict_reason,
        "hr_questions": [],
    }
    if slot is not None and reason is not None:
        data["resume_suggestion"] = {"slot": slot, "reason": reason}
    return json.dumps(data, ensure_ascii=False)


def test_v7_parse_facts_code_fallback_scenarios():
    """测试代码兜底：
    - skip + 无风险 + 理由含"需确认"等关键词 → check 且 derivation 首条为兜底说明；
    - skip + 有风险信号 + 理由含"需确认" → 仍为 skip；
    - skip + 理由不含这些词 → 仍为 skip；
    - v6 解析不受影响。
    """
    check_words = ["需要确认", "需确认", "需要核实", "需核实", "待确认"]
    for kw in check_words:
        raw_json = _make_llm_json(
            verdict="skip",
            verdict_reason=f"岗位职责相符，但经验门槛{kw}",
            risk_signals=[],
            derivation=["推导原因1", "推导原因2"],
        )

        # v7 解析：转为 check，首条为兜底说明
        _, verdict_v7, der_v7, v_reason_v7, hr_q_v7, _ = prompt_v7.parse_facts(raw_json)
        assert verdict_v7 == "check"
        assert der_v7[0] == "一句话理由写的是需要确认，结论改为需要确认"
        assert der_v7[1] == "推导原因1"
        assert der_v7[2] == "推导原因2"
        assert len(der_v7) == 3
        # 改为 check 后 hr_questions 若为空，保持现有 check 处理方式（不编造问题）
        assert hr_q_v7 == []

        # v6 解析不受影响：仍为 skip，推导未插入说明
        _, verdict_v6, der_v6, _, hr_q_v6, _ = prompt_v6.parse_facts(raw_json)
        assert verdict_v6 == "skip"
        assert der_v6[0] == "推导原因1"

    # 兜底截断：若原有 5 条 derivation，插入后仍最多 5 条
    five_derivations = ["理由1", "理由2", "理由3", "理由4", "理由5"]
    raw_5_json = _make_llm_json(
        verdict="skip",
        verdict_reason="岗位经验需确认",
        risk_signals=[],
        derivation=five_derivations,
    )
    _, verdict_5, der_5, _, _, _ = prompt_v7.parse_facts(raw_5_json)
    assert verdict_5 == "check"
    assert len(der_5) == 5
    assert der_5[0] == "一句话理由写的是需要确认，结论改为需要确认"
    assert der_5[1:5] == five_derivations[:4]

    # 分支：skip + 有风险信号 + 理由含"需确认" → 仍为 skip
    risk_item = {
        "type": "诈骗",
        "description": "疑似诈骗兼职",
        "quote": "日结高薪无门槛",
    }
    raw_risk_json = _make_llm_json(
        verdict="skip",
        verdict_reason="岗位需确认，但存在诈骗嫌疑",
        risk_signals=[risk_item],
        derivation=["命中风险信号"],
    )
    _, verdict_risk, der_risk, _, _, _ = prompt_v7.parse_facts(raw_risk_json)
    assert verdict_risk == "skip"
    assert "一句话理由写的是需要确认" not in der_risk[0]

    # 分支：skip + 理由不含这些词 → 仍为 skip
    raw_no_kw_json = _make_llm_json(
        verdict="skip",
        verdict_reason="行业与方向不匹配",
        risk_signals=[],
        derivation=["方向完全不一致"],
    )
    _, verdict_no_kw, der_no_kw, _, _, _ = prompt_v7.parse_facts(raw_no_kw_json)
    assert verdict_no_kw == "skip"
    assert der_no_kw == ["方向完全不一致"]


def test_is_method_changed_under_v7(data_dir: Path):
    """012 起过时基准为 v8：prompt_version 为 v4、v5、v6 的 llm 判断 is_method_changed 均为 True。"""
    conn = open_db(data_dir)
    now_str = utc_now()
    p_id, _ = save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})

    # 创建测试岗位
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (80, 'boss', 'job_stale_v7_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (800, 80, 1, 'detail', 'Python专家', 1, 1, '深圳', '研发', 'hash_800', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 800 WHERE id = 80")

    # 1. prompt_version = v5 的 llm 判断 -> is_method_changed 为 True
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (8005, 'me', 80, 800, ?, 'done', 'apply', 'llm', '[]', 'v5', 'deepseek-flash:no-think', '{}', ?, ?)",
        (p_id, now_str, now_str),
    )
    j_v5 = conn.execute("SELECT * FROM judgements WHERE id = 8005").fetchone()
    assert is_method_changed(j_v5) is True
    assert to_api(conn, j_v5)["stale"]["method_changed"] is True
    api_v5 = to_api(conn, j_v5)
    assert api_v5["stale"]["method_changed"] is True

    # 2. prompt_version = v6 的 llm 判断 -> is_method_changed 为 True
    conn.execute("DELETE FROM judgements WHERE id = 8005")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (8006, 'me', 80, 800, ?, 'done', 'apply', 'llm', '[]', 'v6', 'deepseek-flash:no-think', '{}', ?, ?)",
        (p_id, now_str, now_str),
    )
    j_v6 = conn.execute("SELECT * FROM judgements WHERE id = 8006").fetchone()
    assert is_method_changed(j_v6) is True
    assert to_api(conn, j_v6)["stale"]["method_changed"] is True
    api_v6 = to_api(conn, j_v6)
    assert api_v6["stale"]["method_changed"] is True

    # 3. prompt_version = v4 的 llm 判断 -> is_method_changed 为 True
    conn.execute("DELETE FROM judgements WHERE id = 8006")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (8004, 'me', 80, 800, ?, 'done', 'apply', 'llm', '[]', 'v4', 'deepseek-flash:no-think', '{}', ?, ?)",
        (p_id, now_str, now_str),
    )
    j_v4 = conn.execute("SELECT * FROM judgements WHERE id = 8004").fetchone()
    assert is_method_changed(j_v4) is True
    assert to_api(conn, j_v4)["stale"]["method_changed"] is True
    api_v4 = to_api(conn, j_v4)
    assert api_v4["stale"]["method_changed"] is True

    conn.close()


def test_default_config_prompt_version(data_dir: Path):
    """默认配置 prompt_version 为 DEFAULT_PROMPT_VERSION。"""
    settings = load_settings(data_dir=data_dir, env={})
    assert settings.prompt_version == DEFAULT_PROMPT_VERSION
    assert Settings(data_dir=data_dir).prompt_version == DEFAULT_PROMPT_VERSION


def test_v7_config_and_dispatch(data_dir: Path, settings: Settings):
    """JET_PROMPT_VERSION=v7 时配置可用，分派到 v7 并由 worker 保存至数据库。"""
    # 1. 验证配置解析
    s = load_settings(data_dir=data_dir, env={"JET_PROMPT_VERSION": "v7"})
    assert s.prompt_version == "v7"

    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (90, 'boss', 'job_v7_dispatch', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (900, 90, 1, 'detail', 'Python工程师', '20-30K', 1, 20.0, 30.0, 12, 1, '深圳', '开发微服务', 'hash_900', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 900 WHERE id = 90")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (9, 'me', 1, '[\"Python\"]', '[]', '[\"深圳\"]', 20.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (9000, 'me', 90, 900, 9, 'queued', '{}', ?)",
        (now_str,),
    )
    save_resumes(
        conn,
        user_id,
        [
            {"slot": 1, "name": "简历1", "profile": "Python后端开发"},
            {"slot": 2, "name": "简历2", "profile": "全栈开发"},
        ],
    )
    conn.close()

    recorded_prompt = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        recorded_prompt["messages"] = body.get("messages")
        # 模拟模型输出：给出一个 skip 且理由含"需确认"的回答，触发 v7 代码兜底与简历建议
        content = _make_llm_json(
            verdict="skip",
            verdict_reason="技术对口但年限需确认",
            risk_signals=[],
            derivation=["经验要求2年，候选人背景1年"],
            slot=1,
            reason="技术方向对口Python微服务",
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    active_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v7",
        review_enabled=False,
    )
    worker = JudgementWorker(active_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        assert worker.submit(9000)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证消息由 prompt_v7 构造，包含规则 14
    sys_msg = recorded_prompt["messages"][0]["content"]
    assert "14. 经验门槛与结论：" in sys_msg
    assert "简历建议：" in sys_msg

    # 验证数据库中持久化的结果走 v7 逻辑（含兜底降级为 check）
    conn = open_db(data_dir)
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 9000").fetchone()
    assert j_row["status"] == "done"
    assert j_row["prompt_version"] == "v7"
    assert j_row["verdict"] == "check"  # 经过代码兜底变为 check
    assert j_row["resume_direction"] == "1"
    assert j_row["resume_reason"] == "技术方向对口Python微服务"

    api_res = to_api(conn, j_row)
    assert api_res["prompt_version"] == "v7"
    assert api_res["verdict"] == "check"
    assert api_res["derivation"][0] == "一句话理由写的是需要确认，结论改为需要确认"
    assert api_res["resume_suggestion"]["slot"] == 1
    assert api_res["resume_suggestion"]["name"] == "简历1"
    conn.close()
