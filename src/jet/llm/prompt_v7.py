"""v7 提示词与经验门槛规则。

在 v6 基础之上：
1. 系统提示词增加规则 14（经验门槛与结论），位于规则 13（若有）或规则 12 之后，简历建议之前；
2. parse_facts 沿用 v6（含简历建议），增加代码兜底：
   verdict 为 skip、无风险信号且 verdict_reason 含有需要确认/需确认/需要核实/需核实/待确认时，
   改结论为 check，并在 derivation 最前面插入"一句话理由写的是需要确认，结论改为需要确认"（最多 5 条）。
"""

from typing import Any, Mapping, Sequence

from jet.llm import prompt_v5
from jet.llm import prompt_v6
from jet.llm.prompt_v5 import SYSTEM_PROMPT as V5_SYSTEM_PROMPT, apply_city_salary_cap
from jet.llm.prompt_v6 import build_resume_instruction

PROMPT_VERSION = "v7"

EXPERIENCE_VERDICT_INSTRUCTION = """14. 经验门槛与结论：
    - 硬性经验要求的年限下限不超过 1 年（如"半年以上""1 年以上""1-3 年"），或只写"有相关经验"而没有年限时：若方向对口且与偏好、底线无其他冲突，experience.value 判"差一点"，verdict 用 try，不得仅因经验门槛判 skip。
    - 硬性经验要求的年限下限为 2 年（如"2 年以上""2-4 年"）时：verdict 用 check，不得仅因经验门槛判 skip。
    - 硬性要求 3 年及以上，或要求带团队、管理经验时，才可仅因经验门槛判 skip。
    - 以上前两种情况，hr_questions 都必须包含一个问题：是否接受应届生，或经验要求能否放宽。
    - 届别限制、语言要求、执业资格等硬性资格不符时，仍可判 skip。
    - verdict 为 skip 而 verdict_reason 写的是"需要确认""需确认""需要核实""需核实""待确认"时，改判 check。
    - 给出 skip 之前先检查：如果唯一原因是经验年限下限 ≤ 1 年，或者没写年限的经验要求，不得判 skip。"""

SYSTEM_PROMPT = V5_SYSTEM_PROMPT + "\n" + EXPERIENCE_VERDICT_INSTRUCTION

FALLBACK_KEYWORDS = ("需要确认", "需确认", "需要核实", "需核实", "待确认")
FALLBACK_DERIVATION_NOTE = "一句话理由写的是需要确认，结论改为需要确认"


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
    resumes: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """构建 v7 提示词消息数组。
    复用 v5 的消息构造；在规则 13（若有）或规则 12 之后追加规则 14（经验门槛与结论）；
    当有效简历（非空画像）数 >= 2 时再追加简历说明段与输出字段要求。
    """
    msgs = prompt_v5.build_messages(
        profile,
        job_version,
        known_facts=known_facts,
        strict_industries=strict_industries,
        strict_industry_aliases=strict_industry_aliases,
        strict_industry_keywords=strict_industry_keywords,
    )

    sys_content = msgs[0]["content"]
    if not sys_content.endswith("\n"):
        sys_content += "\n\n"
    elif not sys_content.endswith("\n\n"):
        sys_content += "\n"
    sys_content += EXPERIENCE_VERDICT_INSTRUCTION

    if resumes:
        instruction = build_resume_instruction(resumes)
        if instruction:
            sys_content = sys_content + "\n\n" + instruction

    msgs[0] = {
        "role": "system",
        "content": sys_content,
    }
    return msgs


def parse_facts(
    text: str,
    truncate_derivation: bool = False,
    valid_slots: set[int] | None = None,
) -> tuple[dict[str, Any], str, list[str], str, list[str], dict[str, Any] | None]:
    """Parse and validate LLM output into
    (facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion) for v7 prompt.

    复用 v6 的解析逻辑（含 resume_suggestion 等）；
    代码兜底：v7 解析结果 verdict 为 skip、没有风险信号（risk_signals 为空）、
    且 verdict_reason 含"需要确认""需确认""需要核实""需核实""待确认"之一时，
    把 verdict 改为 check，并在 derivation 最前面插入"一句话理由写的是需要确认，结论改为需要确认"
    （derivation 仍最多 5 条）。改为 check 后 hr_questions 若为空，保持现有 check 的处理方式。
    """
    facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion = prompt_v6.parse_facts(
        text, truncate_derivation=truncate_derivation, valid_slots=valid_slots
    )

    risk_signals = facts.get("risk_signals") or []
    if verdict == "skip" and not risk_signals:
        if any(k in verdict_reason for k in FALLBACK_KEYWORDS):
            verdict = "check"
            derivation = [FALLBACK_DERIVATION_NOTE] + list(derivation)
            if len(derivation) > 5:
                derivation = derivation[:5]

    return facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion
