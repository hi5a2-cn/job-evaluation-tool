import json
import pytest

from jet.llm.prompt import ParseError
from jet.llm.prompt_v4 import PROMPT_VERSION, SYSTEM_PROMPT, build_messages, parse_facts


def _valid_v4_json(**kwargs) -> str:
    base = {
        "facts": {
            "summary": {
                "text": "负责后端系统设计与接口开发，配合前端联调。",
                "quotes": ["负责后端系统设计与接口开发"],
            },
            "work_type": {
                "value": "数据与技术",
                "subtype": "开发与测试",
                "secondary": [{"category": "运营", "subtype": "用户运营"}],
                "quotes": ["接口开发"],
            },
            "sales_level": {
                "value": "低",
                "signals": [],
            },
            "experience": {
                "requirement": "本科以上学历，3年以上Python经验",
                "requirement_type": "硬性",
                "value": "满足",
                "gap": "",
            },
            "work_intensity": {
                "value": "双休",
                "quotes": ["周末双休"],
            },
            "risk_signals": [],
        },
        "verdict": "apply",
        "derivation": [
            "职责为纯技术开发，与偏好一致",
            "3年经验满足候选人背景",
        ],
        "verdict_reason": "核心开发职责，无销售，完全匹配",
        "hr_questions": [],
    }
    for k, v in kwargs.items():
        if k == "facts":
            base["facts"].update(v)
        else:
            base[k] = v
    return json.dumps(base, ensure_ascii=False)


def test_parse_facts_success():
    raw = _valid_v4_json()
    facts, verdict, derivation, verdict_reason, hr_questions = parse_facts(raw)

    assert verdict == "apply"
    assert facts["work_type"]["value"] == "数据与技术"
    assert facts["work_type"]["subtype"] == "开发与测试"
    assert facts["work_type"]["secondary"] == [{"category": "运营", "subtype": "用户运营"}]
    assert facts["work_intensity"]["value"] == "双休"
    assert facts["work_intensity"]["quotes"] == ["周末双休"]
    assert facts["sales_level"]["value"] == "低"
    assert facts["experience"]["value"] == "满足"
    assert facts["risk_signals"] == []
    assert hr_questions == []
    assert len(derivation) == 2
    assert verdict_reason == "核心开发职责，无销售，完全匹配"


def test_parse_facts_subtype_not_in_category_raises():
    # 细分与大类不匹配
    raw = _valid_v4_json(
        facts={
            "work_type": {
                "value": "运营",
                "subtype": "数据分析",  # 数据分析属于 数据与技术
                "secondary": [],
                "quotes": [],
            }
        }
    )
    with pytest.raises(ParseError, match="不属于大类"):
        parse_facts(raw)

    # 大类为'其他'但细分不为 null
    raw_other = _valid_v4_json(
        facts={
            "work_type": {
                "value": "其他",
                "subtype": "用户运营",
                "secondary": [],
                "quotes": [],
            }
        }
    )
    with pytest.raises(ParseError, match="不属于大类"):
        parse_facts(raw_other)


def test_parse_facts_invalid_work_intensity_raises():
    # 旧的加班选项或越界值
    for bad_intensity in ["有", "明确双休或不加班", "偶尔加班", "未知"]:
        raw = _valid_v4_json(
            facts={
                "work_intensity": {
                    "value": bad_intensity,
                    "quotes": [],
                }
            }
        )
        with pytest.raises(ParseError, match="work_intensity"):
            parse_facts(raw)


def test_parse_facts_legacy_verdict_raises():
    # 旧结论 fit / unsure / unfit 必须报错
    for bad_verdict in ["fit", "unsure", "unfit", "unknown"]:
        raw = _valid_v4_json(verdict=bad_verdict)
        with pytest.raises(ParseError, match="verdict"):
            parse_facts(raw)


def test_parse_facts_secondary_normalization():
    # secondary 规整：去重、过滤与主要类型相同的项、上限 3 个
    raw = _valid_v4_json(
        facts={
            "work_type": {
                "value": "数据与技术",
                "subtype": "开发与测试",
                "secondary": [
                    {"category": "数据与技术", "subtype": "开发与测试"},  # 同主类型 -> 过滤
                    {"category": "运营", "subtype": "用户运营"},
                    {"category": "运营", "subtype": "用户运营"},  # 重复 -> 去重
                    {"category": "产品与项目", "subtype": "项目管理"},
                    {"category": "职能", "subtype": "行政"},
                    {"category": "其他", "subtype": None},  # 超过 3 项截断
                ],
                "quotes": [],
            }
        }
    )
    facts, _, _, _, _ = parse_facts(raw)
    sec = facts["work_type"]["secondary"]
    assert len(sec) == 3
    assert sec == [
        {"category": "运营", "subtype": "用户运营"},
        {"category": "产品与项目", "subtype": "项目管理"},
        {"category": "职能", "subtype": "行政"},
    ]


def test_parse_facts_experience_enforcement_rules():
    # 1. "优先"项不满足时强制为"差一点"
    raw_pref = _valid_v4_json(
        facts={
            "experience": {
                "requirement": "有大厂背景优先",
                "requirement_type": "优先",
                "value": "不满足",
                "gap": "无大厂经历",
            }
        }
    )
    facts, _, _, _, _ = parse_facts(raw_pref)
    assert facts["experience"]["value"] == "差一点"

    # 2. 未提及经验要求时强制满足
    raw_none = _valid_v4_json(
        facts={
            "experience": {
                "requirement": None,
                "requirement_type": "未提及",
                "value": "不满足",
                "gap": "无",
            }
        }
    )
    facts, _, _, _, _ = parse_facts(raw_none)
    assert facts["experience"]["value"] == "满足"
    assert facts["experience"]["requirement"] is None


def test_parse_facts_risk_signals_validation():
    # 1. Valid risk signals
    raw = _valid_v4_json(
        facts={
            "risk_signals": [
                {"type": "非法金融", "description": "按交易手数提成", "quote": "每天600手为标准"},
                {"type": "其他", "description": "年龄限制过窄", "quote": "年龄要求18-26"},
            ]
        },
        verdict="skip",
    )
    facts, verdict, derivation, _, _ = parse_facts(raw)
    assert verdict == "skip"
    assert len(facts["risk_signals"]) == 2
    assert facts["risk_signals"][0]["type"] == "非法金融"

    # 2. Invalid risk signal type raises ParseError
    raw_bad_type = _valid_v4_json(
        facts={
            "risk_signals": [
                {"type": "未定义的风险类型", "description": "xxx", "quote": "yyy"}
            ]
        },
        verdict="skip",
    )
    with pytest.raises(ParseError, match="不在合法枚举"):
        parse_facts(raw_bad_type)

    # 3. 风险信号缺原句：保留该信号（风险提示不能被吞掉），核对后标 found=false，卡片显示"未在原文找到"
    raw_no_quote = _valid_v4_json(
        facts={
            "risk_signals": [
                {"type": "非法金融", "description": "xxx", "quote": ""}
            ]
        },
        verdict="skip",
    )
    from jet.domain.quotes import annotate_facts

    facts_nq, verdict_nq, _, _, _ = parse_facts(raw_no_quote)
    assert len(facts_nq["risk_signals"]) == 1
    assert verdict_nq == "skip"
    annotated_nq = annotate_facts(facts_nq, "职位描述正文")
    assert annotated_nq["risk_signals"][0]["quote"]["found"] is False

    # 4. Code fallback: risk_signals non-empty but verdict != 'skip' -> forced to 'skip'
    raw_not_skip = _valid_v4_json(
        facts={
            "risk_signals": [
                {"type": "非法金融", "description": "疑似期货高频刷量", "quote": "每天600手为标准"}
            ]
        },
        verdict="apply",
    )
    facts, verdict, derivation, _, _ = parse_facts(raw_not_skip)
    assert verdict == "skip"
    assert derivation[0] == "命中风险信号，结论改为不建议投"


def test_parse_facts_hr_questions_rules():
    # 1. check / try keeps questions
    raw_check = _valid_v4_json(
        verdict="check",
        hr_questions=["实际对接银行客户的时间大概占多少？", "有没有个人业绩或拉新指标？"],
    )
    _, verdict, _, _, questions = parse_facts(raw_check)
    assert verdict == "check"
    assert len(questions) == 2
    assert "对接银行" in questions[0]

    # 2. apply / skip clears questions to empty list
    raw_apply = _valid_v4_json(
        verdict="apply",
        hr_questions=["这个问题应该被清除"],
    )
    _, _, _, _, questions_apply = parse_facts(raw_apply)
    assert questions_apply == []

    # 3. Truncate > 3 questions and > 60 chars each
    long_q = "这是一段非常非常长的提问内容" * 10
    raw_long = _valid_v4_json(
        verdict="try",
        hr_questions=[long_q, "问题2", "问题3", "问题4多余截断"],
    )
    _, _, _, _, questions_trunc = parse_facts(raw_long)
    assert len(questions_trunc) == 3
    assert len(questions_trunc[0]) == 60


def test_risk_finance_quotes_annotation():
    from pathlib import Path
    from jet.domain.quotes import annotate_facts, count_quotes

    jd_path = Path(__file__).resolve().parent.parent / "fixtures" / "boss" / "risk_finance_jd.txt"
    jd_text = jd_path.read_text(encoding="utf-8")

    raw = _valid_v4_json(
        facts={
            "summary": {"text": "金融运营", "quotes": ["数字产品的日常运营工作"]},
            "risk_signals": [
                {"type": "非法金融", "description": "按交易手数提成", "quote": "每天600手为标准"},
                {"type": "其他", "description": "年龄限制过窄", "quote": "年龄要求18-26"},
            ],
        },
        verdict="skip",
    )
    facts, _, _, _, _ = parse_facts(raw)
    annotated = annotate_facts(facts, jd_text)
    signals = annotated["risk_signals"]
    assert len(signals) == 2
    assert signals[0]["quote"]["found"] is True
    assert signals[1]["quote"]["found"] is True

    total, missing = count_quotes(annotated)
    # 1 from summary, 1 from work_intensity ("周末双休" not in risk jd -> missing), 2 from risk_signals
    assert total >= 3


def test_build_messages_structure():
    profile = {
        "directions": ["Python开发"],
        "keywords": ["后端"],
        "cities": ["深圳"],
        "work_preference": "不愿做销售",
        "background": "3年开发经验",
    }
    job_version = {
        "title": "后端工程师",
        "salary_raw": "20-30K",
        "salary_visible": True,
        "city": "深圳",
        "description": "开发后台服务，周末双休",
    }
    msgs = build_messages(profile, job_version)
    assert len(msgs) >= 2
    assert msgs[0]["role"] == "system"
    assert "工作类型两层分类" in msgs[0]["content"]
    assert "work_intensity" in msgs[0]["content"]
    assert "建议四档及含义" in msgs[0]["content"]
    assert "apply" in msgs[0]["content"]
