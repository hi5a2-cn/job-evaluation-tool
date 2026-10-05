import json
import pytest

from jet.llm.prompt import ParseError
from jet.llm.prompt_v2 import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_messages,
    parse_facts,
)


def test_prompt_version():
    assert PROMPT_VERSION == "v2"


def test_build_messages_contains_required_elements():
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI", "SQL"],
        "cities": ["深圳"],
        "min_monthly_k": 20.0,
        "exclude_keywords": ["外包"],
        "work_preference": "喜欢做后端微服务架构，不想做营销推广",
        "background": "计算机本科，3年后端经验",
    }
    job_version = {
        "title": "高级Python工程师",
        "salary_visible": 1,
        "salary_raw": "20-35K",
        "city": "深圳",
        "district": "南山区",
        "description": "负责核心后端服务开发，日常对接客户渠道配合推广",
    }

    messages = build_messages(profile, job_version)
    assert len(messages) == 2

    sys_msg = messages[0]["content"]
    assert "依据职责而非职位名" in sys_msg
    assert "先事实后建议" in sys_msg
    assert "变相销售信号" in sys_msg
    assert "对接客户或渠道" in sys_msg
    assert "商务拓展" in sys_msg
    assert "业绩指标" in sys_msg

    user_msg = messages[1]["content"]
    assert "喜欢做后端微服务架构" in user_msg
    assert "计算机本科，3年后端经验" in user_msg
    assert "高级Python工程师" in user_msg
    assert "20-35K" in user_msg
    assert "深圳" in user_msg
    assert "南山区" in user_msg


def test_build_messages_with_known_facts():
    profile = {
        "directions": ["数据分析"],
        "cities": ["深圳"],
    }
    job_version = {
        "title": "数据分析师",
        "salary_visible": 0,
        "city": "深圳",
        "description": "做报表",
    }
    known = {
        "work_type": "数据",
        "sales_level": "低",
    }
    messages = build_messages(profile, job_version, known_facts=known)
    user_msg = messages[1]["content"]
    assert "已知判定事实" in user_msg
    assert "work_type: 数据" in user_msg
    assert "sales_level: 低" in user_msg


def test_parse_facts_success():
    sample = {
        "facts": {
            "summary": {"text": "日常负责后端微服务开发与维护", "quotes": ["微服务架构研发", "负责核心接口"]},
            "work_type": {"value": "技术支持", "quotes": ["技术支持与排障"]},
            "sales_level": {
                "value": "低",
                "signals": [{"signal": "对接客户或渠道", "quote": "配合对接客户需求"}],
            },
            "experience": {"requirement": "3年以上经验", "value": "满足", "gap": ""},
            "overtime": {"value": "明确双休或不加班", "quotes": ["周末双休不加班"]},
        },
        "verdict": "fit",
        "derivation": ["技术要求与画像完全一致", "薪资符合预期"],
    }
    text = json.dumps(sample, ensure_ascii=False)
    facts, verdict, derivation = parse_facts(text)

    assert verdict == "fit"
    assert len(derivation) == 2
    assert facts["work_type"]["value"] == "技术支持"
    assert facts["sales_level"]["value"] == "低"
    assert facts["experience"]["value"] == "满足"
    assert facts["overtime"]["value"] == "明确双休或不加班"
    assert len(facts["summary"]["quotes"]) == 2


def test_parse_facts_supports_markdown_code_fences():
    sample = {
        "facts": {
            "summary": {"text": "做数据分析", "quotes": []},
            "work_type": {"value": "数据", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {"requirement": None, "value": "满足", "gap": ""},
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "unsure",
        "derivation": ["职责混合，需要进一步确认"],
    }
    text = f"```json\n{json.dumps(sample, ensure_ascii=False)}\n```"
    facts, verdict, derivation = parse_facts(text)
    assert verdict == "unsure"
    assert facts["work_type"]["value"] == "数据"


def test_parse_facts_truncates_lists():
    sample = {
        "facts": {
            "summary": {"text": "概括", "quotes": ["q1", "q2", "q3", "q4", "q5"]},
            "work_type": {"value": "运营", "quotes": ["w1", "w2", "w3", "w4"]},
            "sales_level": {
                "value": "高",
                "signals": [{"signal": f"sig{i}", "quote": f"q{i}"} for i in range(10)],
            },
            "experience": {"requirement": "5年", "value": "差一点", "gap": "差半年"},
            "overtime": {"value": "有", "quotes": ["o1", "o2", "o3", "o4"]},
        },
        "verdict": "unfit",
        "derivation": ["理由1", "理由2", "理由3", "理由4", "理由5"],
    }
    facts, verdict, derivation = parse_facts(json.dumps(sample))
    assert len(facts["summary"]["quotes"]) == 3
    assert len(facts["work_type"]["quotes"]) == 3
    assert len(facts["sales_level"]["signals"]) == 6
    assert len(facts["overtime"]["quotes"]) == 3
    assert len(derivation) == 5


def test_parse_facts_schema_and_enum_violations():
    # 1. Invalid JSON
    with pytest.raises(ParseError):
        parse_facts("not json")

    # 2. Empty text
    with pytest.raises(ParseError):
        parse_facts("   ")

    # 3. Missing facts
    with pytest.raises(ParseError):
        parse_facts(json.dumps({"verdict": "fit", "derivation": ["理由"]}))

    # 4. Invalid work_type enum
    base = {
        "facts": {
            "summary": {"text": "概括", "quotes": []},
            "work_type": {"value": "未知工种", "quotes": []},
            "sales_level": {"value": "低", "signals": []},
            "experience": {"requirement": None, "value": "满足", "gap": ""},
            "overtime": {"value": "未提及", "quotes": []},
        },
        "verdict": "fit",
        "derivation": ["理由"],
    }
    with pytest.raises(ParseError, match="work_type"):
        parse_facts(json.dumps(base))

    # 5. Invalid sales_level enum
    bad_sl = dict(base)
    bad_sl["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []}, sales_level={"value": "超高", "signals": []})
    with pytest.raises(ParseError, match="sales_level"):
        parse_facts(json.dumps(bad_sl))

    # 6. Invalid experience.value enum
    bad_exp = dict(base)
    bad_exp["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []}, experience={"value": "完全行", "gap": ""})
    with pytest.raises(ParseError, match="experience"):
        parse_facts(json.dumps(bad_exp))

    # 7. Invalid overtime.value enum
    bad_ot = dict(base)
    bad_ot["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []}, overtime={"value": "偶尔", "quotes": []})
    with pytest.raises(ParseError, match="overtime"):
        parse_facts(json.dumps(bad_ot))

    # 8. Invalid verdict enum
    bad_vd = dict(base)
    bad_vd["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []})
    bad_vd["verdict"] = "maybe"
    with pytest.raises(ParseError, match="verdict"):
        parse_facts(json.dumps(bad_vd))

    # 9. Derivation empty or > 5 items
    bad_der_empty = dict(base)
    bad_der_empty["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []})
    bad_der_empty["derivation"] = []
    with pytest.raises(ParseError, match="derivation"):
        parse_facts(json.dumps(bad_der_empty))

    bad_der_six = dict(base)
    bad_der_six["facts"] = dict(base["facts"], work_type={"value": "数据", "quotes": []})
    bad_der_six["derivation"] = ["1", "2", "3", "4", "5", "6"]
    with pytest.raises(ParseError, match="derivation"):
        parse_facts(json.dumps(bad_der_six))
