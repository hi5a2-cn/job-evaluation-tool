import json
import pytest

from jet.llm.prompt import ParseError
from jet.llm.prompt_v3 import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    VALID_REQUIREMENT_TYPES,
    build_messages,
    parse_facts,
)


def test_prompt_version():
    assert PROMPT_VERSION == "v3"


def test_system_prompt_and_build_messages():
    assert "硬性" in SYSTEM_PROMPT
    assert "优先" in SYSTEM_PROMPT
    assert "未提及" in SYSTEM_PROMPT
    assert "verdict_reason" in SYSTEM_PROMPT
    assert "先事实后建议" in SYSTEM_PROMPT
    assert "依据职责而非职位名" in SYSTEM_PROMPT

    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "cities": ["深圳"],
        "min_monthly_k": 20.0,
        "work_preference": "做微服务研发，不愿背销售业绩",
        "background": "本科，3年Python研发经验",
    }
    job_version = {
        "title": "高级Python工程师",
        "salary_visible": 1,
        "salary_raw": "20-30K",
        "city": "深圳",
        "district": "南山区",
        "description": "岗位职责：\n1. 负责FastAPI后端研发；\n2. 3年以上经验优先；\n3. 双休不加班。",
    }
    messages = build_messages(profile, job_version)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "硬性" in messages[0]["content"]
    assert "优先" in messages[0]["content"]
    assert "高级Python工程师" in messages[1]["content"]
    assert "本科，3年Python研发经验" in messages[1]["content"]

    # With known facts
    known = {"work_type": "技术支持"}
    messages_known = build_messages(profile, job_version, known_facts=known)
    assert "已知判定事实" in messages_known[1]["content"]
    assert "work_type: 技术支持" in messages_known[1]["content"]


def test_parse_facts_success():
    sample = {
        "facts": {
            "summary": {"text": "日常负责后端微服务开发与维护", "quotes": ["微服务研发", "负责核心接口"]},
            "work_type": {"value": "技术支持", "quotes": ["技术支持与排障"]},
            "sales_level": {
                "value": "低",
                "signals": [{"signal": "对接客户或渠道", "quote": "配合对接客户需求"}],
            },
            "experience": {
                "requirement": "3年以上经验",
                "requirement_type": "硬性",
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "明确双休或不加班", "quotes": ["周末双休不加班"]},
        },
        "verdict": "fit",
        "derivation": ["技术要求与画像完全一致", "薪资符合预期"],
        "verdict_reason": "核心技术栈匹配，工作年限符合要求",
    }
    text = json.dumps(sample, ensure_ascii=False)
    facts, verdict, derivation, verdict_reason = parse_facts(text)

    assert verdict == "fit"
    assert len(derivation) == 2
    assert verdict_reason == "核心技术栈匹配，工作年限符合要求"
    assert facts["experience"]["requirement_type"] == "硬性"
    assert facts["experience"]["value"] == "满足"
    assert facts["experience"]["gap"] == ""
    assert facts["work_type"]["value"] == "技术支持"
    assert facts["work_type"]["secondary"] == []


def test_parse_facts_supports_markdown_code_fences():
    sample = {
        "facts": {
            "summary": {"text": "做数据分析", "quotes": []},
            "work_type": {"value": "数据", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": None,
                "requirement_type": "未提及",
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["方向一致"],
        "verdict_reason": "数据方向匹配",
    }
    text = f"```json\n{json.dumps(sample, ensure_ascii=False)}\n```"
    facts, verdict, derivation, verdict_reason = parse_facts(text)
    assert verdict == "fit"
    assert verdict_reason == "数据方向匹配"
    assert facts["experience"]["requirement_type"] == "未提及"


def test_parse_facts_requirement_type_invalid():
    sample = {
        "facts": {
            "summary": {"text": "做开发", "quotes": []},
            "work_type": {"value": "技术支持", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": "本科",
                "requirement_type": "强行必须",  # Invalid enum
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["理由"],
        "verdict_reason": "一句话理由",
    }
    with pytest.raises(ParseError, match="requirement_type"):
        parse_facts(json.dumps(sample, ensure_ascii=False))

    # Missing requirement_type
    sample_missing = dict(sample)
    sample_missing["facts"] = dict(sample["facts"], experience={"requirement": "本科", "value": "满足", "gap": ""})
    with pytest.raises(ParseError, match="requirement_type"):
        parse_facts(json.dumps(sample_missing, ensure_ascii=False))


def test_parse_facts_verdict_reason_missing_or_empty():
    sample = {
        "facts": {
            "summary": {"text": "做开发", "quotes": []},
            "work_type": {"value": "技术支持", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": "本科",
                "requirement_type": "硬性",
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["理由"],
    }
    # Missing verdict_reason
    with pytest.raises(ParseError, match="verdict_reason"):
        parse_facts(json.dumps(sample, ensure_ascii=False))

    # Empty string verdict_reason
    sample_empty = dict(sample, verdict_reason="")
    with pytest.raises(ParseError, match="verdict_reason"):
        parse_facts(json.dumps(sample_empty, ensure_ascii=False))

    # Whitespace-only verdict_reason
    sample_ws = dict(sample, verdict_reason="   \n  ")
    with pytest.raises(ParseError, match="verdict_reason"):
        parse_facts(json.dumps(sample_ws, ensure_ascii=False))

    # Non-string verdict_reason
    sample_non_str = dict(sample, verdict_reason=12345)
    with pytest.raises(ParseError, match="verdict_reason"):
        parse_facts(json.dumps(sample_non_str, ensure_ascii=False))


def test_parse_facts_priority_unfit_forced_to_almost():
    """'优先'项不满足必须被代码强制改为'差一点'，并在 gap 前加'（优先项）'。"""
    sample = {
        "facts": {
            "summary": {"text": "产品运营工作", "quotes": []},
            "work_type": {"value": "运营", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": "1年以上相关经验优先",
                "requirement_type": "优先",
                "value": "不满足",
                "gap": "应届毕业生无相关经验",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "unsure",
        "derivation": ["缺少优先经验要求"],
        "verdict_reason": "缺少优先要求的相关经验",
    }
    facts, verdict, derivation, verdict_reason = parse_facts(json.dumps(sample, ensure_ascii=False))

    assert facts["experience"]["requirement_type"] == "优先"
    assert facts["experience"]["value"] == "差一点"
    assert facts["experience"]["gap"] == "（优先项）应届毕业生无相关经验"

    # Also check when gap is empty
    sample2 = json.loads(json.dumps(sample))
    sample2["facts"]["experience"]["gap"] = ""
    facts2, _, _, _ = parse_facts(json.dumps(sample2, ensure_ascii=False))
    assert facts2["experience"]["value"] == "差一点"
    assert facts2["experience"]["gap"] == "（优先项）"

    # Also check when gap already starts with （优先项）
    sample3 = json.loads(json.dumps(sample))
    sample3["facts"]["experience"]["gap"] = "（优先项）已有前缀"
    facts3, _, _, _ = parse_facts(json.dumps(sample3, ensure_ascii=False))
    assert facts3["experience"]["value"] == "差一点"
    assert facts3["experience"]["gap"] == "（优先项）已有前缀"


def test_parse_facts_unmentioned_forced_to_fit():
    """描述里没有经验要求时，不满足或差一点必须被代码强制改为'满足'。"""
    # 1. 未提及 + 不满足 -> 满足
    sample_unfit = {
        "facts": {
            "summary": {"text": "日常工作", "quotes": []},
            "work_type": {"value": "运营", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": None,
                "requirement_type": "未提及",
                "value": "不满足",
                "gap": "未说明原因的不满足",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["岗位未设经验门槛"],
        "verdict_reason": "岗位无经验门槛要求",
    }
    facts1, _, _, _ = parse_facts(json.dumps(sample_unfit, ensure_ascii=False))
    assert facts1["experience"]["requirement_type"] == "未提及"
    assert facts1["experience"]["value"] == "满足"
    assert facts1["experience"]["gap"] == ""

    # 2. 未提及 + 差一点 -> 满足
    sample_almost = json.loads(json.dumps(sample_unfit))
    sample_almost["facts"]["experience"]["value"] = "差一点"
    sample_almost["facts"]["experience"]["gap"] = "差半年"
    facts2, _, _, _ = parse_facts(json.dumps(sample_almost, ensure_ascii=False))
    assert facts2["experience"]["requirement_type"] == "未提及"
    assert facts2["experience"]["value"] == "满足"
    assert facts2["experience"]["gap"] == ""


def test_parse_facts_verdict_reason_truncated():
    long_reason = "这是一个非常详细而且字数特别长的判断理由，超过了系统规定的四十个字符上限，应当被安全截断"
    assert len(long_reason) > 40

    sample = {
        "facts": {
            "summary": {"text": "做数据分析", "quotes": []},
            "work_type": {"value": "数据", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": "1-3年",
                "requirement_type": "硬性",
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["方向一致"],
        "verdict_reason": long_reason,
    }
    _, _, _, verdict_reason = parse_facts(json.dumps(sample, ensure_ascii=False))
    assert len(verdict_reason) == 40
    assert verdict_reason == long_reason[:40]


def test_parse_facts_secondary_work_types():
    base_sample = {
        "facts": {
            "summary": {"text": "做数据与运营", "quotes": []},
            "work_type": {"value": "数据", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {
                "requirement": None,
                "requirement_type": "未提及",
                "value": "满足",
                "gap": "",
            },
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["方向一致"],
        "verdict_reason": "核心方向匹配",
    }

    # 1. 正常解析
    s1 = json.loads(json.dumps(base_sample))
    s1["facts"]["work_type"]["secondary"] = ["运营", "营销"]
    facts1, _, _, _ = parse_facts(json.dumps(s1, ensure_ascii=False))
    assert facts1["work_type"]["secondary"] == ["运营", "营销"]

    # 2. 缺省为 []
    s2 = json.loads(json.dumps(base_sample))
    assert "secondary" not in s2["facts"]["work_type"]
    facts2, _, _, _ = parse_facts(json.dumps(s2, ensure_ascii=False))
    assert facts2["work_type"]["secondary"] == []

    # 3. 显式 None 缺省为 []
    s3 = json.loads(json.dumps(base_sample))
    s3["facts"]["work_type"]["secondary"] = None
    facts3, _, _, _ = parse_facts(json.dumps(s3, ensure_ascii=False))
    assert facts3["work_type"]["secondary"] == []

    # 4. 非法选项 -> ParseError
    s4 = json.loads(json.dumps(base_sample))
    s4["facts"]["work_type"]["secondary"] = ["打杂"]
    with pytest.raises(ParseError, match="secondary"):
        parse_facts(json.dumps(s4, ensure_ascii=False))

    # 不是列表 -> ParseError
    s4_str = json.loads(json.dumps(base_sample))
    s4_str["facts"]["work_type"]["secondary"] = "运营"
    with pytest.raises(ParseError, match="secondary"):
        parse_facts(json.dumps(s4_str, ensure_ascii=False))

    # 列表项不是字符串 -> ParseError
    s4_num = json.loads(json.dumps(base_sample))
    s4_num["facts"]["work_type"]["secondary"] = [123]
    with pytest.raises(ParseError, match="secondary"):
        parse_facts(json.dumps(s4_num, ensure_ascii=False))

    # 5. 与主要类型相同的项被去掉
    s5 = json.loads(json.dumps(base_sample))
    s5["facts"]["work_type"]["value"] = "数据"
    s5["facts"]["work_type"]["secondary"] = ["数据", "运营", "营销"]
    facts5, _, _, _ = parse_facts(json.dumps(s5, ensure_ascii=False))
    assert facts5["work_type"]["secondary"] == ["运营", "营销"]

    # 6. 去重且保持顺序
    s6 = json.loads(json.dumps(base_sample))
    s6["facts"]["work_type"]["secondary"] = ["运营", "运营", "营销"]
    facts6, _, _, _ = parse_facts(json.dumps(s6, ensure_ascii=False))
    assert facts6["work_type"]["secondary"] == ["运营", "营销"]

    # 7. 超过 3 个截断
    s7 = json.loads(json.dumps(base_sample))
    s7["facts"]["work_type"]["value"] = "其他"
    s7["facts"]["work_type"]["secondary"] = ["数据", "运营", "营销", "客服"]
    facts7, _, _, _ = parse_facts(json.dumps(s7, ensure_ascii=False))
    assert facts7["work_type"]["secondary"] == ["数据", "运营", "营销"]
    assert len(facts7["work_type"]["secondary"]) == 3
