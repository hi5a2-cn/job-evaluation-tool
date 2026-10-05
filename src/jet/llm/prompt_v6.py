import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jet.llm import prompt_v4
from jet.llm import prompt_v5
from jet.llm.prompt_v4 import (
    CATEGORIES,
    ParseError,
    SYSTEM_PROMPT as V4_SYSTEM_PROMPT,
    VALID_EXPERIENCE_VALUES,
    VALID_REQUIREMENT_TYPES,
    VALID_RISK_TYPES,
    VALID_SALES_LEVELS,
    VERDICTS,
    WORK_INTENSITY,
)
from jet.llm.prompt_v5 import SYSTEM_PROMPT as V5_SYSTEM_PROMPT, apply_city_salary_cap

PROMPT_VERSION = "v6"
SYSTEM_PROMPT = V5_SYSTEM_PROMPT


def build_resume_instruction(
    resumes: Sequence[Mapping[str, Any]] | None,
) -> str:
    """构建简历建议的提示词说明（FR-003, T026）。
    跳过画像为空的简历；非空画像不足 2 份时返回空字符串。
    只含编号（简历1/2/3）与画像内容，绝不包含简历名称、姓名或简历全文。
    """
    if not resumes:
        return ""

    valid_resumes: list[tuple[Any, str]] = []
    for r in resumes:
        pf = str(r.get("profile") or "").strip()
        if pf:
            valid_resumes.append((r.get("slot"), pf))

    if len(valid_resumes) < 2:
        return ""

    lines: list[str] = [f"- 简历{slot}：{pf}" for slot, pf in valid_resumes]
    resumes_text = "\n".join(lines)
    slots_slash = " / ".join(f"简历{slot}" for slot, _ in valid_resumes)
    slots_pipe = "|".join(str(slot) for slot, _ in valid_resumes)

    return (
        f"14. 简历建议：\n"
        f"候选人准备了以下几份不同侧重点的简历：\n"
        f"{resumes_text}\n\n"
        f"请根据岗位实际日常职责，从上述简历（{slots_slash}）中挑选一份最适合投递的简历，"
        f"并在 resume_suggestion 中给出简历编号（数字 {slots_pipe}）和一句推荐理由（≤ 40 字）。"
        f"即使结论不是 apply，只要该岗位有投递价值或方向大体对口，也请给出最贴近的简历；"
        f"若完全不相干或岗位有严重风险，仍选一份相对最接近的并在理由中注明。\n\n"
        f"输出 JSON 格式中增加 resume_suggestion 字段：\n"
        f'  "resume_suggestion": {{\n'
        f'    "slot": {slots_pipe},\n'
        f'    "reason": "一句话推荐理由，≤ 40 字"\n'
        f'  }}'
    )


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
    resumes: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """构建 v6 提示词消息数组。
    复用 v5 的消息构造；当有效简历（非空画像）数 >= 2 时追加简历说明段与输出字段要求。
    非空画像少于 2 份时，生成的消息与 v5 完全相同。
    """
    msgs = prompt_v5.build_messages(
        profile,
        job_version,
        known_facts=known_facts,
        strict_industries=strict_industries,
        strict_industry_aliases=strict_industry_aliases,
        strict_industry_keywords=strict_industry_keywords,
    )

    if not resumes:
        return msgs

    instruction = build_resume_instruction(resumes)
    if instruction:
        msgs[0] = {
            "role": "system",
            "content": msgs[0]["content"] + "\n\n" + instruction,
        }
    return msgs


def parse_resume_suggestion(
    data: Any,
    valid_slots: set[int] | None = None,
) -> dict[str, Any] | None:
    """从模型输出 dict 中解析 resume_suggestion。
    校验 slot 必须在有效编号集合中、理由非空且截断至最多 40 字符。
    任何缺失或格式错误返回 None，绝不抛出 ParseError。
    """
    if not isinstance(data, dict):
        return None
    raw_sug = data.get("resume_suggestion")
    if not isinstance(raw_sug, dict):
        return None

    raw_slot = raw_sug.get("slot")
    raw_reason = raw_sug.get("reason")
    if raw_slot is None or not isinstance(raw_reason, str):
        return None

    slot_int: int | None = None
    if isinstance(raw_slot, int) and not isinstance(raw_slot, bool):
        slot_int = raw_slot
    elif isinstance(raw_slot, str):
        s = raw_slot.strip()
        if s.startswith("简历"):
            s = s[2:].strip()
        try:
            slot_int = int(s)
        except ValueError:
            return None
    else:
        return None

    if valid_slots is not None and slot_int not in valid_slots:
        return None

    reason = raw_reason.strip()
    if not reason:
        return None

    if len(reason) > 40:
        reason = reason[:40]

    return {
        "slot": slot_int,
        "reason": reason,
    }


def parse_facts(
    text: str,
    truncate_derivation: bool = False,
    valid_slots: set[int] | None = None,
) -> tuple[dict[str, Any], str, list[str], str, list[str], dict[str, Any] | None]:
    """Parse and validate LLM output into
    (facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion) for v6 prompt.

    Raises ParseError on core schema or enum violation.
    resume_suggestion parsing failure does not raise ParseError and falls back to None.
    """
    facts, verdict, derivation, verdict_reason, hr_questions = prompt_v4.parse_facts(
        text, truncate_derivation=truncate_derivation
    )

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    resume_suggestion = None
    try:
        data = json.loads(cleaned)
        resume_suggestion = parse_resume_suggestion(data, valid_slots=valid_slots)
    except Exception:
        resume_suggestion = None

    return facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion
