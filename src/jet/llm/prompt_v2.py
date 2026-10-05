import json
import re
from typing import Any, Mapping

from jet.llm.prompt import ParseError

PROMPT_VERSION = "v2"

SYSTEM_PROMPT = """你是一个专业的求职决策分析专家。在评估岗位与候选人的匹配度时，你必须遵循以下原则：

1. 先事实后建议：先客观提取并判定岗位的事实属性，再基于这些事实与候选人画像对照得出建议。
2. 依据职责而非职位名：招聘职位名常有夸大或修饰，必须严格以职位描述中的日常工作职责为准。
3. 专门识别变相销售信号：仔细排查以下变相销售信号清单（包括但不限于：对接客户或渠道、商务拓展、业绩指标、营销推广或活动策划、获客转化、维护客户关系、陪同拜访）。先逐条摘出命中上述信号的原句，再综合判定销售与客户对接成分以及工作类型。职位名好听但实际职责以营销推广、对接客户、完成业绩为主的，一律按销售处理。
   - 销售与客户对接成分：高 = 对接客户或渠道、商务拓展、业绩指标、获客转化中至少一项是主要职责；中 = 有这类职责但只占一部分（如"配合商务""协助对接客户"）；低 = 基本不涉及。
   - 工作类型按占比最大的日常职责判定：以策划营销活动、推广获客为主是"营销"；以用户、内容、活动的日常运转和数据跟进为主才是"运营"。
   - 对照画像时，"工作内容偏好"中明确不想做的方向优先于关键词匹配；不能因为职位名或关键词命中就判"适合"。
4. 原句引用要求：所有 quote 必须逐字逐句摘自职位描述原文，严禁任何形式的改写或编造；如果在原文中找不到依据，绝对不要编造，直接留空数组 []。
5. 经验门槛对照：对照候选人提供的"我的背景"（学历、工作年限、主要经历），客观判定候选人是否满足岗位的经验门槛（满足 / 差一点 / 不满足 / 无法判断），若不满足或差一点，必须在 gap 中明确说明差距。
6. 建议三档含义：
   - fit（适合）：岗位事实属性与候选人画像及偏好基本一致。
   - unsure（存疑）：岗位职责混杂、描述信息不足、变相销售信号存疑或候选人画像未明确覆盖。
   - unfit（不适合）：岗位事实属性与候选人的工作偏好、技能方向或底线条件存在明确冲突。
7. 推导过程：给出 1–5 条明确的推导说明，每条必须明确指明哪项岗位事实与候选人画像的哪一条规则一致或冲突。
8. 格式约束：严格只输出合法 JSON，不要包含任何 markdown 代码块或解释文字。

输出 JSON 格式规范：
{
  "facts": {
    "summary": {
      "text": "一两句白话概括该岗位每天实际做什么",
      "quotes": ["原句"]
    },
    "work_type": {
      "value": "数据|运营|营销|销售|客服|技术支持|其他",
      "quotes": ["原句"]
    },
    "sales_level": {
      "value": "高|中|低",
      "signals": [
        {"signal": "命中信号名（如：对接客户或渠道）", "quote": "原句"}
      ]
    },
    "experience": {
      "requirement": "经验要求的原文原句，无则为 null",
      "value": "满足|差一点|不满足|无法判断",
      "gap": "差距说明，满足时为空字符串 \"\""
    },
    "overtime": {
      "value": "有|未提及|明确双休或不加班",
      "quotes": ["原句"]
    }
  },
  "verdict": "fit|unsure|unfit",
  "derivation": [
    "推导理由1",
    "推导理由2"
  ]
}
"""

VALID_WORK_TYPES = {"数据", "运营", "营销", "销售", "客服", "技术支持", "其他"}
VALID_SALES_LEVELS = {"高", "中", "低"}
VALID_EXPERIENCE_VALUES = {"满足", "差一点", "不满足", "无法判断"}
VALID_OVERTIME_VALUES = {"有", "未提及", "明确双休或不加班"}
VALID_VERDICTS = {"fit", "unsure", "unfit"}


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build messages array with prompt v2 system prompt, profile details, and job details."""
    profile = dict(profile)
    job_version = dict(job_version)

    directions = "、".join(profile.get("directions") or [])
    raw_keywords = profile.get("keywords") or []
    keywords = "、".join(raw_keywords) if raw_keywords else "无"
    cities = "、".join(profile.get("cities") or [])
    min_k = f"{profile.get('min_monthly_k')}K" if profile.get("min_monthly_k") is not None else "不限"
    raw_exclude = profile.get("exclude_keywords") or []
    exclude_keywords = "、".join(raw_exclude) if raw_exclude else "无"
    work_pref = str(profile.get("work_preference") or "").strip() or "无"
    background = str(profile.get("background") or "").strip() or "无"

    title = str(job_version.get("title") or "")
    if bool(job_version.get("salary_visible")) and job_version.get("salary_raw"):
        salary = str(job_version.get("salary_raw"))
    else:
        salary = "薪资不可见"

    city = str(job_version.get("city") or "")
    district = str(job_version.get("district") or "未提供")
    description = str(job_version.get("description") or "无")

    user_content = f"""【候选人画像】
- 目标方向：{directions}
- 技能与关键词：{keywords}
- 期望城市：{cities}
- 最低月薪底线：{min_k}
- 不接受条件：{exclude_keywords}
- 工作内容偏好：{work_pref}
- 我的背景：{background}

【岗位信息】
- 职位名称：{title}
- 薪资范围：{salary}
- 工作城市：{city}
- 所属区域：{district}
- 职位描述：
{description}
"""

    if known_facts:
        facts_summary = []
        for k, v in known_facts.items():
            facts_summary.append(f"  - {k}: {v}")
        known_str = "\n".join(facts_summary)
        user_content += f"""
【已知判定事实】
以下事实已由另一个模型判定，请沿用这些取值，只为它们找原句、写概括和推导：
{known_str}
"""

    user_content += "\n请依据上述画像和岗位信息给出评估结果，只返回符合要求的 JSON。"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_facts(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """
    Parse and validate LLM output into (facts, verdict, derivation).

    Raises ParseError on any schema or enum violation.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ParseError("大模型返回内容为空")

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ParseError(f"大模型返回无法解析为 JSON: {e}") from e

    if not isinstance(data, dict):
        raise ParseError("返回内容不是 JSON 对象")

    # 1. facts
    facts = data.get("facts")
    if not isinstance(facts, dict):
        raise ParseError("缺失 facts 字段或 facts 不是对象")

    # 1.1 summary
    summary = facts.get("summary")
    if not isinstance(summary, dict) or "text" not in summary:
        raise ParseError("facts.summary 缺失或不是对象")
    summary_text = str(summary.get("text") or "").strip()
    raw_summary_quotes = summary.get("quotes")
    if raw_summary_quotes is None:
        raw_summary_quotes = []
    elif not isinstance(raw_summary_quotes, list):
        raise ParseError("facts.summary.quotes 必须是列表")
    summary_quotes = [str(q).strip() for q in raw_summary_quotes if str(q).strip()][:3]

    # 1.2 work_type
    work_type = facts.get("work_type")
    if not isinstance(work_type, dict):
        raise ParseError("facts.work_type 缺失或不是对象")
    wt_val = work_type.get("value")
    if wt_val not in VALID_WORK_TYPES:
        raise ParseError(f"facts.work_type.value '{wt_val}' 不在合法枚举中: {VALID_WORK_TYPES}")
    raw_wt_quotes = work_type.get("quotes")
    if raw_wt_quotes is None:
        raw_wt_quotes = []
    elif not isinstance(raw_wt_quotes, list):
        raise ParseError("facts.work_type.quotes 必须是列表")
    wt_quotes = [str(q).strip() for q in raw_wt_quotes if str(q).strip()][:3]

    # 1.3 sales_level
    sales_level = facts.get("sales_level")
    if not isinstance(sales_level, dict):
        raise ParseError("facts.sales_level 缺失或不是对象")
    sl_val = sales_level.get("value")
    if sl_val not in VALID_SALES_LEVELS:
        raise ParseError(f"facts.sales_level.value '{sl_val}' 不在合法枚举中: {VALID_SALES_LEVELS}")
    raw_signals = sales_level.get("signals")
    if raw_signals is None:
        raw_signals = []
    elif not isinstance(raw_signals, list):
        raise ParseError("facts.sales_level.signals 必须是列表")
    signals = []
    for s in raw_signals:
        if isinstance(s, dict):
            sig_name = str(s.get("signal") or "").strip()
            quote_val = s.get("quote")
            quote_str = quote_val if isinstance(quote_val, str) else str(quote_val or "")
            signals.append({"signal": sig_name, "quote": quote_str.strip()})
    signals = signals[:6]

    # 1.4 experience
    experience = facts.get("experience")
    if not isinstance(experience, dict):
        raise ParseError("facts.experience 缺失或不是对象")
    exp_val = experience.get("value")
    if exp_val not in VALID_EXPERIENCE_VALUES:
        raise ParseError(f"facts.experience.value '{exp_val}' 不在合法枚举中: {VALID_EXPERIENCE_VALUES}")
    req_val = experience.get("requirement")
    exp_req = str(req_val).strip() if req_val is not None else None
    exp_gap = str(experience.get("gap") or "").strip()

    # 1.5 overtime
    overtime = facts.get("overtime")
    if not isinstance(overtime, dict):
        raise ParseError("facts.overtime 缺失或不是对象")
    ot_val = overtime.get("value")
    if ot_val not in VALID_OVERTIME_VALUES:
        raise ParseError(f"facts.overtime.value '{ot_val}' 不在合法枚举中: {VALID_OVERTIME_VALUES}")
    raw_ot_quotes = overtime.get("quotes")
    if raw_ot_quotes is None:
        raw_ot_quotes = []
    elif not isinstance(raw_ot_quotes, list):
        raise ParseError("facts.overtime.quotes 必须是列表")
    ot_quotes = [str(q).strip() for q in raw_ot_quotes if str(q).strip()][:3]

    parsed_facts = {
        "summary": {"text": summary_text, "quotes": summary_quotes},
        "work_type": {"value": wt_val, "quotes": wt_quotes},
        "sales_level": {"value": sl_val, "signals": signals},
        "experience": {"requirement": exp_req, "value": exp_val, "gap": exp_gap},
        "overtime": {"value": ot_val, "quotes": ot_quotes},
    }

    # 2. verdict
    verdict = data.get("verdict")
    if verdict not in VALID_VERDICTS:
        raise ParseError(f"verdict '{verdict}' 不在合法枚举中: {VALID_VERDICTS}")

    # 3. derivation
    derivation = data.get("derivation")
    if not isinstance(derivation, list) or not (1 <= len(derivation) <= 5):
        raise ParseError("derivation 必须是包含 1 到 5 条字符串的列表")

    parsed_derivation = []
    for d in derivation:
        if not isinstance(d, str) or not d.strip():
            raise ParseError("derivation 项必须是非空字符串")
        parsed_derivation.append(d.strip())

    return parsed_facts, verdict, parsed_derivation
