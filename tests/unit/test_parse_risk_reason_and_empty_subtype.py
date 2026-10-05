"""体检第 35、36 条：v4 起共用的判断结果解析。

35：风险信号兜底把结论改成「不建议投」时，一句话理由换成第一条风险信号，不再保留模型原来可能是正面的理由。
36：工作类型细分输出成空字符串时按 null 处理，不让整次判断失败。
"""

import json

import pytest

from jet.llm import prompt_v8, prompt_v10
from jet.llm.prompt import ParseError
from jet.llm.prompt_v4 import RISK_FALLBACK_REASON_PREFIX, parse_facts
from jet.llm.versions import DEFAULT_PROMPT_VERSION, get_prompt_version

RISK = {"type": "其他", "description": "需先自行垫付出差酒店费用，存在垫资风险", "quote": "需要先自行垫付"}


def _llm_json(
    verdict: str = "apply",
    verdict_reason: str = "核心开发职责，无销售，完全匹配",
    risk_signals: list[dict] | None = None,
    subtype: str | None = "开发与测试",
    sales_level: str = "低",
) -> str:
    work_type = {"value": "数据与技术", "subtype": subtype, "secondary": [], "quotes": ["接口开发"]}
    data = {
        "facts": {
            "summary": {"text": "负责后端系统设计与接口开发。", "quotes": ["负责后端系统设计与接口开发"]},
            "work_type": work_type,
            "sales_level": {"value": sales_level, "signals": []},
            "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
            "work_intensity": {"value": "双休", "quotes": []},
            "risk_signals": risk_signals or [],
        },
        "verdict": verdict,
        "derivation": ["职责为纯技术开发，与偏好一致"],
        "verdict_reason": verdict_reason,
        "hr_questions": [],
    }
    return json.dumps(data, ensure_ascii=False)


# ---------- 35 ----------


def test_risk_fallback_replaces_positive_reason():
    _, verdict, derivation, reason, _ = parse_facts(_llm_json(verdict="apply", risk_signals=[RISK]))
    assert verdict == "skip"
    assert derivation[0] == "命中风险信号，结论改为不建议投"
    assert reason == RISK_FALLBACK_REASON_PREFIX + RISK["description"]
    assert reason == "有风险：需先自行垫付出差酒店费用，存在垫资风险"


def test_risk_fallback_reason_uses_first_signal_and_is_capped_at_40_chars():
    long_desc = "疑" * 60
    risks = [{"type": "诈骗", "description": long_desc, "quote": ""}, RISK]
    _, verdict, _, reason, _ = parse_facts(_llm_json(verdict="try", risk_signals=risks))
    assert verdict == "skip"
    assert reason.startswith(RISK_FALLBACK_REASON_PREFIX + "疑")
    assert len(reason) == 40


def test_model_skip_with_risks_keeps_model_reason():
    _, verdict, _, reason, _ = parse_facts(
        _llm_json(verdict="skip", verdict_reason="存在垫资风险，不建议投", risk_signals=[RISK])
    )
    assert verdict == "skip"
    assert reason == "存在垫资风险，不建议投"


def test_no_risks_keeps_model_reason():
    _, verdict, _, reason, _ = parse_facts(_llm_json())
    assert verdict == "apply"
    assert reason == "核心开发职责，无销售，完全匹配"


def test_default_version_uses_risk_fallback_reason():
    version = get_prompt_version(DEFAULT_PROMPT_VERSION)
    parsed = version.parse_func(
        _llm_json(verdict="apply", risk_signals=[RISK]),
        valid_slots=set(),
        profile={"exclude_keywords": ["外包"]},
    )
    verdict, reason = parsed[1], parsed[3]
    assert verdict == "skip"
    assert reason == RISK_FALLBACK_REASON_PREFIX + RISK["description"]


def test_risk_reason_shown_when_risk_and_sales_fallbacks_both_apply():
    # 风险兜底先执行，结论已是不建议投，销售兜底不再改理由；风险比销售成分更严重，显示风险
    parsed = prompt_v10.parse_facts(
        _llm_json(verdict="apply", risk_signals=[RISK], sales_level="高"),
        profile={"exclude_keywords": ["销售"]},
    )
    assert parsed[1] == "skip"
    assert parsed[3] == RISK_FALLBACK_REASON_PREFIX + RISK["description"]


def test_sales_fallback_reason_unchanged_without_risks():
    parsed = prompt_v10.parse_facts(
        _llm_json(verdict="apply", sales_level="高"),
        profile={"exclude_keywords": ["销售"]},
    )
    assert parsed[1] == "skip"
    assert parsed[3] == prompt_v8.SALES_FALLBACK_VERDICT_REASON


# ---------- 36 ----------


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_subtype_is_treated_as_null(empty: str):
    facts, verdict, _, _, _ = parse_facts(_llm_json(subtype=empty))
    assert facts["work_type"]["subtype"] is None
    assert verdict == "apply"


def test_null_and_valid_subtype_unchanged():
    facts_null, _, _, _, _ = parse_facts(_llm_json(subtype=None))
    assert facts_null["work_type"]["subtype"] is None
    facts_ok, _, _, _, _ = parse_facts(_llm_json())
    assert facts_ok["work_type"]["subtype"] == "开发与测试"


def test_invalid_subtype_still_fails():
    with pytest.raises(ParseError):
        parse_facts(_llm_json(subtype="不存在的细分"))
