import json
import re
from typing import Any, Mapping

from jet.llm.prompt import ParseError
from jet.llm.prompt_v2 import (
    VALID_EXPERIENCE_VALUES,
    VALID_OVERTIME_VALUES,
    VALID_SALES_LEVELS,
    VALID_VERDICTS,
    VALID_WORK_TYPES,
)
from jet.llm import prompt_v2

PROMPT_VERSION = "v3"

SYSTEM_PROMPT = """你是一个专业的求职决策分析专家。在评估岗位与候选人的匹配度时，你必须遵循以下原则：

1. 先事实后建议：先客观提取并判定岗位的事实属性，再基于这些事实与候选人画像对照得出建议。
2. 依据职责而非职位名：招聘职位名常有夸大或修饰，必须严格以职位描述中的日常工作职责为准。
3. 专门识别变相销售信号：仔细排查以下变相销售信号清单（包括但不限于：对接客户或渠道、商务拓展、业绩指标、营销推广或活动策划、获客转化、维护客户关系、陪同拜访）。先逐条摘出命中上述信号的原句，再综合判定销售与客户对接成分以及工作类型。职位名好听但实际职责以营销推广、对接客户、完成业绩为主的，一律按销售处理。
   - 销售与客户对接成分：高 = 对接客户或渠道、商务拓展、业绩指标、获客转化中至少一项是主要职责；中 = 有这类职责但只占一部分（如"配合商务""协助对接客户"）；低 = 基本不涉及。
   - 工作类型判定：
     * work_type.value 为主要类型＝日常花时间最多的一类（数据/运营/营销/销售/客服/技术支持/其他）。
     * work_type.secondary 为次要类型，0–3 个，选项同上，不含主要类型，没有就填空数组 []。岗位职责很多是混合的，岗位混合时必须填次要类型。
     * 以策划营销活动、推广获客为主是"营销"；以用户、内容、活动的日常运转和数据跟进为主才是"运营"。
   - 对照画像时，"工作内容偏好"中明确不想做的方向优先于关键词匹配；不能因为职位名或关键词命中就判"适合"。
4. 原句引用要求：所有 quote 必须逐字逐句摘自职位描述原文，严禁任何形式的改写或编造；如果在原文中找不到依据，绝对不要编造，直接留空数组 []。
5. 经验门槛要求：
   - 经验要求必须区分 requirement_type：
     * 硬性（"要求""须""必须""至少"等，没有"优先"字样）
     * 优先（"优先""加分""者佳"等）
     * 未提及
   - 对照候选人提供的"我的背景"（学历、工作年限、主要经历），客观判定候选人是否满足岗位的经验门槛（满足 / 差一点 / 不满足 / 无法判断），若不满足或差一点，必须在 gap 中明确说明差距。
   - "优先"项不满足时 value 最多"差一点"，不得判"不满足"；硬性要求才可能"不满足"。
   - 描述里没有经验要求时，requirement_type="未提及"、value="满足"、requirement=null。
6. 建议三档含义：
   - fit（适合）：岗位事实属性与候选人画像及偏好基本一致。
   - unsure（存疑）：岗位职责混杂、描述信息不足、变相销售信号存疑或候选人画像未明确覆盖。
   - unfit（不适合）：岗位事实属性与候选人的工作偏好、技能方向或底线条件存在明确冲突。
7. 推导过程与一句话理由：
   - 给出 1–5 条明确的推导说明，每条必须明确指明哪项岗位事实与候选人画像的哪一条规则一致或冲突。
   - 顶层给出一句话理由 verdict_reason（≤ 40 字），用于卡片最上方的结论摘要行，讲清最核心的一致或冲突理由。
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
      "secondary": ["运营"],
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
      "requirement_type": "硬性|优先|未提及",
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
  ],
  "verdict_reason": "一句话理由，≤ 40 字"
}
"""

VALID_REQUIREMENT_TYPES = {"硬性", "优先", "未提及"}


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build messages array with prompt v3 system prompt, profile details, and job details."""
    msgs = prompt_v2.build_messages(profile, job_version, known_facts=known_facts)
    msgs[0] = {"role": "system", "content": SYSTEM_PROMPT}
    return msgs


def parse_facts(text: str) -> tuple[dict[str, Any], str, list[str], str]:
    """
    Parse and validate LLM output into (facts, verdict, derivation, verdict_reason).

    Raises ParseError on any schema or enum violation.
    Applies code enforcement rules:
    - requirement_type == "优先" and value == "不满足" -> "差一点" with "（优先项）" prepended to gap.
    - requirement_type == "未提及" and value in ("不满足", "差一点") -> "满足" with gap = "".
    - secondary work types validation, deduplication, removal of main work type, truncation to 3.
    """
    parsed_facts, verdict, derivation = prompt_v2.parse_facts(text)

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ParseError(f"大模型返回无法解析为 JSON: {e}") from e

    # 1. verdict_reason validation
    verdict_reason = data.get("verdict_reason")
    if not isinstance(verdict_reason, str) or not verdict_reason.strip():
        raise ParseError("缺失 verdict_reason 字段或不是非空字符串")
    verdict_reason = verdict_reason.strip()
    if len(verdict_reason) > 40:
        verdict_reason = verdict_reason[:40]

    # 2. facts.experience.requirement_type validation
    raw_exp = data.get("facts", {}).get("experience")
    if not isinstance(raw_exp, dict):
        raise ParseError("facts.experience 缺失或不是对象")
    req_type = raw_exp.get("requirement_type")
    if req_type not in VALID_REQUIREMENT_TYPES:
        raise ParseError(
            f"facts.experience.requirement_type '{req_type}' 不在合法枚举中: {VALID_REQUIREMENT_TYPES}"
        )

    # 3. facts.work_type.secondary validation
    raw_wt = data.get("facts", {}).get("work_type", {})
    raw_sec = raw_wt.get("secondary")
    if raw_sec is None:
        secondary: list[str] = []
    else:
        if not isinstance(raw_sec, list):
            raise ParseError("facts.work_type.secondary 必须是字符串列表")
        for item in raw_sec:
            if not isinstance(item, str):
                raise ParseError("facts.work_type.secondary 每项必须是字符串")
            if item not in VALID_WORK_TYPES:
                raise ParseError(f"facts.work_type.secondary 项 '{item}' 不在合法枚举中: {VALID_WORK_TYPES}")

        # 去重（保持顺序）、去掉与主要类型相同的项、最多保留 3 个
        seen = set()
        unique = []
        for item in raw_sec:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        wt_val = parsed_facts["work_type"]["value"]
        filtered = [item for item in unique if item != wt_val]
        secondary = filtered[:3]

    parsed_facts["work_type"]["secondary"] = secondary

    # 4. Code enforcement rules
    exp_val = parsed_facts["experience"]["value"]
    exp_gap = parsed_facts["experience"]["gap"]

    if req_type == "优先" and exp_val == "不满足":
        exp_val = "差一点"
        if not exp_gap.startswith("（优先项）"):
            exp_gap = f"（优先项）{exp_gap}"
    elif req_type == "未提及" and exp_val in ("不满足", "差一点"):
        exp_val = "满足"
        exp_gap = ""

    parsed_facts["experience"]["requirement_type"] = req_type
    parsed_facts["experience"]["value"] = exp_val
    parsed_facts["experience"]["gap"] = exp_gap

    return parsed_facts, verdict, derivation, verdict_reason
