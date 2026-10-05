"""v10 提示词：销售从严按画像生效，简历建议段编号改为 17。

在 v9 基础之上：
1. 画像不接受关键词中含「销售」时，规则 15 为完整版（销售成分从严）；
   不含「销售」时，规则 15 为识别版（标题为「15. 销售成分识别：」，只保留前两句识别说明，不含结论与优先规则）；
2. 简历建议段编号由 14 改为 17；
3. parse_facts 兜底（销售高转 skip）只对不接受销售的用户生效；
   且执行兜底时，若推导首条为 v7 的「一句话理由写的是需要确认，结论改为需要确认」，先将其移除；
4. apply_city_salary_cap 同 v9。
"""

import json
from typing import Any, Mapping, Sequence

from jet.llm import prompt_v6, prompt_v7, prompt_v8, prompt_v9
from jet.llm.prompt_v5 import apply_city_salary_cap

PROMPT_VERSION = "v10"

# 识别版规则 15（对不含「销售」的画像使用）：保留识别说明前两句，不含结论后三句
SALES_RECOGNITION_INSTRUCTION = """15. 销售成分识别：
    - 职责中单独成条地写了对接外部客户、渠道或合作机构（如"对接银行""对接商家""对接渠道商""维护客户关系"），而不是"配合""协助"他人对接的，算主要职责，sales_level 判"高"。
    - 策划、统筹面向客户或渠道的营销产品与营销活动（如"策划面向全国银行的营销活动"），算"营销推广或活动策划"信号，必须摘出原句。"""

# 完整版规则 15 直接复用 prompt_v8.SALES_STRICT_INSTRUCTION
SALES_STRICT_INSTRUCTION = prompt_v8.SALES_STRICT_INSTRUCTION

SYSTEM_PROMPT = prompt_v7.SYSTEM_PROMPT + "\n" + SALES_STRICT_INSTRUCTION + "\n" + prompt_v9.RULE_16_INSTRUCTION
SYSTEM_PROMPT_RECOGNITION = prompt_v7.SYSTEM_PROMPT + "\n" + SALES_RECOGNITION_INSTRUCTION + "\n" + prompt_v9.RULE_16_INSTRUCTION


def rejects_sales(profile: Mapping[str, Any] | Sequence[Any] | str | None) -> bool:
    """判断画像是否不接受销售。

    画像 exclude_keywords（列表或兼容 JSON 字符串写法）中任一项含「销售」二字时为 True。
    profile 为 None 或未配置不接受关键词时为 False。
    """
    if profile is None:
        return False

    if isinstance(profile, (list, tuple, set)):
        items = list(profile)
    elif isinstance(profile, Mapping):
        raw_exclude = profile.get("exclude_keywords")
        if not raw_exclude:
            return False
        if isinstance(raw_exclude, str):
            raw_exclude = raw_exclude.strip()
            if not raw_exclude:
                return False
            try:
                parsed = json.loads(raw_exclude)
                items = parsed if isinstance(parsed, (list, tuple, set)) else [str(parsed)]
            except Exception:
                items = [raw_exclude]
        elif isinstance(raw_exclude, (list, tuple, set)):
            items = list(raw_exclude)
        else:
            return False
    elif isinstance(profile, str):
        cleaned = profile.strip()
        if not cleaned:
            return False
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, Mapping):
                return rejects_sales(parsed)
            elif isinstance(parsed, (list, tuple, set)):
                items = list(parsed)
            else:
                items = [str(parsed)]
        except Exception:
            items = [cleaned]
    else:
        return False

    return any(isinstance(item, str) and "销售" in item for item in items)


def build_resume_instruction(resumes: Sequence[Mapping[str, Any]] | None) -> str:
    """构建 v10 简历建议说明段（编号为 17）。"""
    raw = prompt_v6.build_resume_instruction(resumes)
    if not raw:
        return ""
    assert "14. 简历建议：" in raw, "预期 prompt_v6.build_resume_instruction 包含 '14. 简历建议：'"
    replaced = raw.replace("14. 简历建议：", "17. 简历建议：", 1)
    assert "14. 简历建议：" not in replaced, "替换后不应再有 '14. 简历建议：'"
    return replaced


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
    resumes: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """构建 v10 提示词消息数组。

    复用 v7 基础（含规则 12、13、14）；
    追加规则 15（不接受销售时为从严版，接受销售时为识别版）；
    追加规则 16（重点排查行业的归属与 BOSS 标签）；
    有效简历画像 >= 2 时追加简历建议段（编号 17）；
    用户消息【岗位信息】在 "- 所属区域：…" 行后插入 BOSS 经验要求与学历要求标签。
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

    # 规则 15
    if rejects_sales(profile):
        sys_content += prompt_v8.SALES_STRICT_INSTRUCTION
    else:
        sys_content += SALES_RECOGNITION_INSTRUCTION

    if not sys_content.endswith("\n"):
        sys_content += "\n\n"
    elif not sys_content.endswith("\n\n"):
        sys_content += "\n"

    # 规则 16
    sys_content += prompt_v9.RULE_16_INSTRUCTION

    # 简历建议段（编号 17）
    if resumes:
        instruction = build_resume_instruction(resumes)
        if instruction:
            sys_content = sys_content + "\n\n" + instruction

    msgs[0] = {
        "role": "system",
        "content": sys_content,
    }

    # 用户消息【岗位信息】：在 "- 所属区域：…" 这一行之后插入 BOSS 经验要求与学历要求标签
    raw_exp = job_version.get("experience_req")
    exp_val = str(raw_exp).strip() if raw_exp is not None and str(raw_exp).strip() else "未提供"

    raw_deg = job_version.get("degree_req")
    deg_val = str(raw_deg).strip() if raw_deg is not None and str(raw_deg).strip() else "未提供"

    user_content = msgs[1]["content"]
    lines = user_content.split("\n")
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and line.startswith("- 所属区域："):
            new_lines.append(f"- 经验要求（BOSS 标签）：{exp_val}")
            new_lines.append(f"- 学历要求（BOSS 标签）：{deg_val}")
            inserted = True

    msgs[1] = {
        "role": "user",
        "content": "\n".join(new_lines),
    }

    return msgs


def parse_facts(
    text: str,
    truncate_derivation: bool = False,
    valid_slots: set[int] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str, list[str], str, list[str], dict[str, Any] | None]:
    """Parse and validate LLM output into
    (facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion) for v10 prompt.

    先调用 prompt_v7.parse_facts（含其 skip→check 兜底）；
    仅当 rejects_sales(profile) 为 True 时执行销售兜底：
    facts.sales_level.value == "高" 且 verdict != "skip" 时：
    - verdict 改为 "skip"；
    - 执行兜底时，如果推导第一条等于 prompt_v7.FALLBACK_DERIVATION_NOTE，先去掉它；
    - derivation 最前面插入 prompt_v8.SALES_FALLBACK_DERIVATION_NOTE，最多截断至 5 条；
    - verdict_reason 改为 prompt_v8.SALES_FALLBACK_VERDICT_REASON；
    - hr_questions 置为 []；
    - resume_suggestion 保持原样。
    """
    facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion = prompt_v7.parse_facts(
        text, truncate_derivation=truncate_derivation, valid_slots=valid_slots
    )

    if rejects_sales(profile):
        sl = facts.get("sales_level")
        sl_val = sl.get("value") if isinstance(sl, dict) else None
        if sl_val == "高" and verdict != "skip":
            verdict = "skip"
            der_list = list(derivation)
            if der_list and der_list[0] == prompt_v7.FALLBACK_DERIVATION_NOTE:
                der_list = der_list[1:]
            derivation = [prompt_v8.SALES_FALLBACK_DERIVATION_NOTE] + der_list
            if len(derivation) > 5:
                derivation = derivation[:5]
            verdict_reason = prompt_v8.SALES_FALLBACK_VERDICT_REASON
            hr_questions = []

    return facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion
