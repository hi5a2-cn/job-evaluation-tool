"""v10 提示词与销售从严按画像生效测试。

覆盖：
1. rejects_sales 纯函数逻辑与各种格式兼容（列表、JSON 字符串、字典画像、空与 None）；
2. build_messages 对比：
   - 含「销售」画像：与 v9 相比，系统消息唯一差别是「14. 简历建议：」->「17. 简历建议：」，用户消息完全相等；
   - 不含「销售」画像：规则 15 为「15. 销售成分识别：」，包含前两句，不含后三句；
3. parse_facts 兜底：
   - 模型返回 sales_level 高、verdict try：
     - 不含「销售」画像：结论保持 try，推导不含销售说明；
     - 含「销售」画像：结论改为 skip，推导首条为销售说明，理由为固定文案，hr_questions 置空；
   - 模型返回 sales_level 高、verdict skip、理由含「需要确认」（触发 v7 兜底）：
     - 含「销售」画像：最终仍为 skip，推导首条为销售说明，且推导中不含 v7 兜底文案；
     - 不含「销售」画像：保持 v7 兜底后的 check；
   - profile 为 None 时不触发销售兜底；
4. 版本表登记：v10 在表中，能力 parse_needs_profile=True，DEFAULT_PROMPT_VERSION=="v10"，STALE_METHOD_BASELINE=="v9"，数字排序；
5. run_llm_judgement 端到端测试：同输入与响应下，含 / 不含销售画像分别产出 skip / try。
"""

import dataclasses
import json
from pathlib import Path
from typing import Any
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.llm import prompt_v7, prompt_v8, prompt_v9, prompt_v10
from jet.llm.client import run_llm_judgement
from jet.llm.versions import (
    DEFAULT_PROMPT_VERSION,
    STALE_METHOD_BASELINE,
    get_prompt_version,
    list_prompt_versions,
)


# ==============================================================================
# 1. rejects_sales 单元测试
# ==============================================================================

def test_rejects_sales_various_inputs():
    # 列表入参
    assert prompt_v10.rejects_sales({"exclude_keywords": ["销售", "外包"]}) is True
    assert prompt_v10.rejects_sales({"exclude_keywords": ["电话销售"]}) is True
    assert prompt_v10.rejects_sales({"exclude_keywords": ["电销", "地推", "BD"]}) is False
    assert prompt_v10.rejects_sales({"exclude_keywords": []}) is False
    assert prompt_v10.rejects_sales({"exclude_keywords": None}) is False
    assert prompt_v10.rejects_sales({}) is False
    assert prompt_v10.rejects_sales(None) is False

    # 直接传列表 / 集合 / 元组形式
    assert prompt_v10.rejects_sales(["销售", "外包"]) is True
    assert prompt_v10.rejects_sales(["电话销售"]) is True
    assert prompt_v10.rejects_sales(["电销", "地推", "BD"]) is False
    assert prompt_v10.rejects_sales([]) is False

    # JSON 字符串写法兼容
    assert prompt_v10.rejects_sales({"exclude_keywords": '["销售", "外包"]'}) is True
    assert prompt_v10.rejects_sales({"exclude_keywords": '["电话销售"]'}) is True
    assert prompt_v10.rejects_sales({"exclude_keywords": '["电销", "地推", "BD"]'}) is False
    assert prompt_v10.rejects_sales({"exclude_keywords": "[]"}) is False
    assert prompt_v10.rejects_sales('["销售", "外包"]') is True
    assert prompt_v10.rejects_sales('{"exclude_keywords": ["电话销售"]}') is True
    assert prompt_v10.rejects_sales('{"exclude_keywords": ["电销"]}') is False


# ==============================================================================
# 2. build_messages 与提示词对比测试
# ==============================================================================

def test_build_messages_with_sales_profile_differs_from_v9_only_in_resume_rule_number():
    """含销售画像：v10 与 v9 生成的消息相比，唯一差别是简历建议段编号由 14 变为 17。"""
    profile = {
        "directions": ["后端开发", "Python工程师"],
        "keywords": ["Python", "FastAPI"],
        "preferred_cities": ["深圳", "广州"],
        "excluded_cities": ["北京"],
        "min_monthly_k": 20.0,
        "nonpref_min_monthly_k": 25.0,
        "exclude_keywords": ["销售", "外包"],
        "work_preference": "做自研产品，不接受纯维护",
        "background": "本科5年经验",
    }
    job_version = {
        "title": "Python资深开发工程师",
        "company_name": "某科技发展有限公司",
        "company_industry": "互联网金融",
        "salary_raw": "25-35K",
        "salary_visible": 1,
        "city": "深圳",
        "district": "南山区",
        "description": "负责核心业务系统架构与开发",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "后端开发画像"},
        {"slot": 2, "name": "简历2", "profile": "全栈开发画像"},
    ]
    strict_industries = ["金融", "房地产"]

    msgs_v9 = prompt_v9.build_messages(
        profile,
        job_version,
        strict_industries=strict_industries,
        resumes=resumes,
    )
    msgs_v10 = prompt_v10.build_messages(
        profile,
        job_version,
        strict_industries=strict_industries,
        resumes=resumes,
    )

    # 用户消息逐字完全相等
    assert msgs_v10[1]["content"] == msgs_v9[1]["content"]

    # 系统提示词中，v10 的简历段编号为 17
    sys_v9 = msgs_v9[0]["content"]
    sys_v10 = msgs_v10[0]["content"]
    assert "14. 简历建议：" in sys_v9
    assert "17. 简历建议：" in sys_v10
    assert "14. 简历建议：" not in sys_v10

    # 将 v10 的 "17. 简历建议：" 换回 "14. 简历建议：" 后，与 v9 系统提示词完全相等
    sys_v10_reverted = sys_v10.replace("17. 简历建议：", "14. 简历建议：", 1)
    assert sys_v10_reverted == sys_v9


def test_build_messages_without_sales_profile_uses_recognition_rule_15():
    """不含销售画像：v10 规则 15 为「15. 销售成分识别：」，包含前两句，不含后三句结论。"""
    profile = {
        "directions": ["后端开发", "Python工程师"],
        "keywords": ["Python", "FastAPI"],
        "preferred_cities": ["深圳"],
        "exclude_keywords": ["外包", "电话客服"],  # 不含销售
    }
    job_version = {
        "title": "Python开发工程师",
        "company_name": "某科技公司",
        "company_industry": "互联网",
        "salary_raw": "20-30K",
        "salary_visible": 1,
        "city": "深圳",
        "district": "南山区",
        "description": "负责系统研发",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "后端画像"},
        {"slot": 2, "name": "简历2", "profile": "架构画像"},
    ]

    msgs_v10 = prompt_v10.build_messages(
        profile,
        job_version,
        strict_industries=["餐饮"],
        resumes=resumes,
    )
    sys_v10 = msgs_v10[0]["content"]

    # 规则 15 标题为「15. 销售成分识别：」
    assert "15. 销售成分识别：" in sys_v10
    assert "15. 销售成分从严：" not in sys_v10

    # 包含前两句识别说明
    sentence1 = '职责中单独成条地写了对接外部客户、渠道或合作机构（如"对接银行""对接商家""对接渠道商""维护客户关系"），而不是"配合""协助"他人对接的，算主要职责，sales_level 判"高"。'
    sentence2 = '策划、统筹面向客户或渠道的营销产品与营销活动（如"策划面向全国银行的营销活动"），算"营销推广或活动策划"信号，必须摘出原句。'
    assert sentence1 in sys_v10
    assert sentence2 in sys_v10

    # 不包含后三句结论与优先规则
    assert "sales_level 为\"高\"时，verdict 必须为 skip" not in sys_v10
    assert "属于重点排查行业（某行业），含销售成分，判不建议投" not in sys_v10
    assert "本条优先于规则 14" not in sys_v10

    # 简历建议段编号为 17
    assert "17. 简历建议：" in sys_v10


# ==============================================================================
# 3. parse_facts 兜底逻辑测试
# ==============================================================================

def _make_llm_json(
    sales_level: str = "高",
    verdict: str = "try",
    verdict_reason: str = "经验大体符合，可以一试",
    derivation: list[str] | None = None,
    risk_signals: list[dict[str, str]] | None = None,
) -> str:
    data = {
        "facts": {
            "summary": {"text": "职责描述", "quotes": ["对接银行"]},
            "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
            "sales_level": {"value": sales_level, "signals": [{"signal": "对接客户", "quote": "对接银行"}]},
            "experience": {"requirement": "1-3年", "requirement_type": "硬性", "value": "满足", "gap": ""},
            "work_intensity": {"value": "双休", "quotes": []},
            "risk_signals": risk_signals or [],
        },
        "verdict": verdict,
        "derivation": derivation if derivation is not None else ["经验门槛适中", "方向吻合"],
        "verdict_reason": verdict_reason,
        "hr_questions": ["日常对接频率"],
    }
    return json.dumps(data, ensure_ascii=False)


def test_parse_facts_sales_high_verdict_try_by_profile():
    raw_json = _make_llm_json(sales_level="高", verdict="try", verdict_reason="经验门槛低，可以一试")

    # 1. 不含「销售」的画像：不触发销售兜底，结论保持 try，推导不含销售说明
    profile_no_sales = {"exclude_keywords": ["外包", "驻场"]}
    facts, verdict, derivation, verdict_reason, hr_questions, _ = prompt_v10.parse_facts(
        raw_json, profile=profile_no_sales
    )
    assert verdict == "try"
    assert prompt_v8.SALES_FALLBACK_DERIVATION_NOTE not in derivation
    assert verdict_reason == "经验门槛低，可以一试"
    assert hr_questions == ["日常对接频率"]

    # 2. profile 为 None：按不接受销售为 False 处理，结论保持 try
    facts_none, verdict_none, derivation_none, _, _, _ = prompt_v10.parse_facts(
        raw_json, profile=None
    )
    assert verdict_none == "try"
    assert prompt_v8.SALES_FALLBACK_DERIVATION_NOTE not in derivation_none

    # 3. 含「销售」的画像：触发销售兜底，结论改为 skip，推导首条为销售说明
    profile_sales = {"exclude_keywords": ["销售", "外包"]}
    facts_s, verdict_s, derivation_s, verdict_reason_s, hr_questions_s, _ = prompt_v10.parse_facts(
        raw_json, profile=profile_sales
    )
    assert verdict_s == "skip"
    assert derivation_s[0] == prompt_v8.SALES_FALLBACK_DERIVATION_NOTE
    assert verdict_reason_s == prompt_v8.SALES_FALLBACK_VERDICT_REASON
    assert hr_questions_s == []


def test_parse_facts_cleans_v7_fallback_note_when_sales_fallback_triggered():
    """模型返回 sales_level 高、verdict skip 且一句话理由含「需要确认」：
    v7 会先将其转为 check 并插入 v7 说明；
    含「销售」的画像触发 v10 销售兜底时，必须去掉 v7 说明后再插入销售说明，最终为 skip。
    """
    raw_json = _make_llm_json(
        sales_level="高",
        verdict="skip",
        verdict_reason="岗位经验需确认",
        derivation=["原推导1", "原推导2"],
        risk_signals=[],
    )

    # 含「销售」画像
    profile_sales = {"exclude_keywords": ["电话销售"]}
    facts, verdict, derivation, verdict_reason, hr_questions, _ = prompt_v10.parse_facts(
        raw_json, profile=profile_sales
    )

    assert verdict == "skip"
    assert prompt_v7.FALLBACK_DERIVATION_NOTE not in derivation
    assert derivation[0] == prompt_v8.SALES_FALLBACK_DERIVATION_NOTE
    assert derivation[1:] == ["原推导1", "原推导2"]
    assert verdict_reason == prompt_v8.SALES_FALLBACK_VERDICT_REASON
    assert hr_questions == []

    # 不含「销售」画像：不会被销售兜底覆盖，保持 v7 兜底改判的 check
    profile_no_sales = {"exclude_keywords": ["外包"]}
    facts_ns, verdict_ns, derivation_ns, verdict_reason_ns, hr_questions_ns, _ = prompt_v10.parse_facts(
        raw_json, profile=profile_no_sales
    )
    assert verdict_ns == "check"
    assert derivation_ns[0] == prompt_v7.FALLBACK_DERIVATION_NOTE


# ==============================================================================
# 4. 版本表登记测试
# ==============================================================================

def test_v10_in_versions_table():
    versions = list_prompt_versions()
    assert "v10" in versions
    assert versions == [f"v{i}" for i in range(1, 11)]

    # 验证不是字符串字典序（v10 必须排在 v9 后面，而不是 v1 后面）
    assert versions.index("v10") == 9
    assert versions.index("v9") == 8
    assert versions.index("v2") == 1

    assert DEFAULT_PROMPT_VERSION == "v10"
    assert STALE_METHOD_BASELINE == "v9"

    v10 = get_prompt_version("v10")
    assert v10.name == "v10"
    assert v10.module is prompt_v10
    assert v10.parse_func is prompt_v10.parse_facts
    assert v10.parse_needs_profile is True
    assert v10.needs_strict_industries is True
    assert v10.needs_resumes is True
    assert v10.has_facts_and_derivation is True
    assert v10.has_verdict_reason is True
    assert v10.has_hr_questions is True
    assert v10.has_resume_suggestion is True
    assert v10.apply_city_salary_cap is True
    assert v10.supports_truncate_derivation is True
    assert v10.annotate_facts is True
    assert v10.supports_review is True
    assert v10.needs_known_facts is True
    assert v10.max_tokens == 1200


# ==============================================================================
# 5. run_llm_judgement 端到端集成测试
# ==============================================================================

def test_run_llm_judgement_v10_end_to_end(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) VALUES ('me', 100, 100)"
    )

    mock_content = _make_llm_json(
        sales_level="高",
        verdict="try",
        verdict_reason="门槛低可以一试",
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": mock_content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    job_version = {
        "id": 1,
        "title": "产品运营",
        "city": "深圳",
        "description": "负责对接银行客户与活动运营",
    }

    test_settings = dataclasses.replace(settings, llm_api_key="test-api-key")

    # 1. 画像含「销售」：端到端结果被改为 skip
    profile_with_sales = {
        "directions": ["产品运营"],
        "cities": ["深圳"],
        "exclude_keywords": ["销售"],
    }
    outcome_sales = run_llm_judgement(
        conn,
        test_settings,
        user_id=user_id,
        profile=profile_with_sales,
        job_version=job_version,
        prompt_version="v10",
        transport=httpx.MockTransport(mock_handler),
    )
    assert outcome_sales.status == "done"
    assert outcome_sales.verdict == "skip"
    assert outcome_sales.verdict_reason == prompt_v8.SALES_FALLBACK_VERDICT_REASON
    assert outcome_sales.derivation[0] == prompt_v8.SALES_FALLBACK_DERIVATION_NOTE
    assert outcome_sales.hr_questions == []

    # 2. 画像不含「销售」：端到端结果保持 try
    profile_without_sales = {
        "directions": ["产品运营"],
        "cities": ["深圳"],
        "exclude_keywords": ["外包"],
    }
    outcome_no_sales = run_llm_judgement(
        conn,
        test_settings,
        user_id=user_id,
        profile=profile_without_sales,
        job_version=job_version,
        prompt_version="v10",
        transport=httpx.MockTransport(mock_handler),
    )
    assert outcome_no_sales.status == "done"
    assert outcome_no_sales.verdict == "try"
    assert outcome_no_sales.verdict_reason == "门槛低可以一试"
    assert prompt_v8.SALES_FALLBACK_DERIVATION_NOTE not in outcome_no_sales.derivation
    assert outcome_no_sales.hr_questions == ["日常对接频率"]

    conn.close()
