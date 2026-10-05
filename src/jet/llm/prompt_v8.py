"""v8 提示词与销售成分从严规则。

在 v7 基础之上：
1. 系统提示词增加规则 15（销售成分从严），位于规则 14 之后、简历说明段（若有）之前；
2. parse_facts 先调用 prompt_v7.parse_facts（含其 skip→check 兜底），然后加代码兜底：
   facts.sales_level.value == "高" 且 verdict != "skip" 时：
   - verdict 改为 "skip"；
   - derivation 最前面插入"销售与客户对接成分为高，结论改为不建议投"，最多 5 条；
   - verdict_reason 改为"销售与客户对接成分高，与不接受销售冲突"；
   - hr_questions 置为 []；
   - resume_suggestion 保持 v6/v7 处理方式不变。
"""

from typing import Any, Mapping, Sequence

from jet.llm import prompt_v7
from jet.llm.prompt_v5 import apply_city_salary_cap
from jet.llm.prompt_v6 import build_resume_instruction
from jet.llm.prompt_v7 import SYSTEM_PROMPT as V7_SYSTEM_PROMPT

PROMPT_VERSION = "v8"

SALES_STRICT_INSTRUCTION = """15. 销售成分从严：
    - 职责中单独成条地写了对接外部客户、渠道或合作机构（如"对接银行""对接商家""对接渠道商""维护客户关系"），而不是"配合""协助"他人对接的，算主要职责，sales_level 判"高"。
    - 策划、统筹面向客户或渠道的营销产品与营销活动（如"策划面向全国银行的营销活动"），算"营销推广或活动策划"信号，必须摘出原句。
    - sales_level 为"高"时，verdict 必须为 skip，verdict_reason 写明含销售或客户对接。
    - 若有规则 13 的重点排查行业，且公司或岗位属于其中之一、sales_level 为"中"时，verdict 也必须为 skip，并在 derivation 中写明"属于重点排查行业（某行业），含销售成分，判不建议投"。
    - 本条优先于规则 14：经验门槛再低，也不能把含销售的岗位判 try 或 check。"""

SYSTEM_PROMPT = V7_SYSTEM_PROMPT + "\n" + SALES_STRICT_INSTRUCTION

SALES_FALLBACK_DERIVATION_NOTE = "销售与客户对接成分为高，结论改为不建议投"
SALES_FALLBACK_VERDICT_REASON = "销售与客户对接成分高，与不接受销售冲突"


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
    resumes: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """构建 v8 提示词消息数组。
    复用 v7 的消息构造（不含简历）；在规则 14 之后追加规则 15（销售成分从严）；
    当有效简历（非空画像）数 >= 2 时再在规则 15 之后追加简历说明段与输出字段要求。
    """
    msgs = prompt_v7.build_messages(
        profile,
        job_version,
        known_facts=known_facts,
        strict_industries=strict_industries,
        strict_industry_aliases=strict_industry_aliases,
        strict_industry_keywords=strict_industry_keywords,
        resumes=None,
    )

    sys_content = msgs[0]["content"]
    if not sys_content.endswith("\n"):
        sys_content += "\n\n"
    elif not sys_content.endswith("\n\n"):
        sys_content += "\n"
    sys_content += SALES_STRICT_INSTRUCTION

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
    (facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion) for v8 prompt.

    先调用 prompt_v7.parse_facts（含其 skip→check 兜底）；
    代码兜底：facts.sales_level.value == "高" 且 verdict != "skip" 时：
    - verdict 改为 "skip"；
    - derivation 最前面插入"销售与客户对接成分为高，结论改为不建议投"，最多 5 条；
    - verdict_reason 改为"销售与客户对接成分高，与不接受销售冲突"；
    - hr_questions 置为 []；
    - resume_suggestion 保持原样。
    """
    facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion = prompt_v7.parse_facts(
        text, truncate_derivation=truncate_derivation, valid_slots=valid_slots
    )

    sl = facts.get("sales_level")
    sl_val = sl.get("value") if isinstance(sl, dict) else None
    if sl_val == "高" and verdict != "skip":
        verdict = "skip"
        derivation = [SALES_FALLBACK_DERIVATION_NOTE] + list(derivation)
        if len(derivation) > 5:
            derivation = derivation[:5]
        verdict_reason = SALES_FALLBACK_VERDICT_REASON
        hr_questions = []

    return facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion
