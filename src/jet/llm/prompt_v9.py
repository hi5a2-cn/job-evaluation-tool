"""v9 提示词与重点排查行业归属及 BOSS 标签规则。

在 v8 基础之上：
1. 系统提示词增加规则 16（重点排查行业的归属与 BOSS 标签），位于规则 15 之后、简历说明段（若有）之前；
2. 用户消息的【岗位信息】在"- 所属区域：…"这一行之后插入两行：
   - 经验要求（BOSS 标签）：{值或"未提供"}
   - 学历要求（BOSS 标签）：{值或"未提供"}
   值取 job_version 的 experience_req / degree_req，去空白后为空则写"未提供"。
3. parse_facts 直接沿用 prompt_v8.parse_facts（含其全部兜底）。
4. apply_city_salary_cap 同 v8。
"""

from typing import Any, Mapping, Sequence

from jet.llm import prompt_v8
from jet.llm.prompt_v5 import apply_city_salary_cap
from jet.llm.prompt_v6 import build_resume_instruction
from jet.llm.prompt_v8 import SYSTEM_PROMPT as V8_SYSTEM_PROMPT

PROMPT_VERSION = "v9"

RULE_16_INSTRUCTION = """16. 重点排查行业的归属与 BOSS 标签：
    - 若有规则 13 的重点排查行业，以下三项任一命中就算属于该行业：①公司行业与行业名或同义名相同或包含；②职位名或公司名含该行业关键词；③职位描述写明岗位业务所在行业或服务对象属于该行业（如要求"保险行业经验"、负责保险产品、服务银行客户）。公司行业未命中不等于不属于，必须继续看②③。福利待遇里的"商业保险""补充医疗保险"等不算。
    - 判断是否属于重点排查行业时，derivation 中写明依据的是哪一项。
    - 岗位信息中的"经验要求（BOSS 标签）"是招聘方填写的硬性经验要求，按规则 14 的硬性要求处理（如"3-5年"即硬性 3 年及以上）；与职位描述里"优先"的表述同时出现时，以 BOSS 标签为准。标签为"经验不限""在校/应届"或"未提供"时不作为门槛。
    - "学历要求（BOSS 标签）"同样是硬性要求，对照候选人背景判断是否满足；"学历不限"或"未提供"时不作为门槛。"""

SYSTEM_PROMPT = V8_SYSTEM_PROMPT + "\n" + RULE_16_INSTRUCTION


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
    resumes: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """构建 v9 提示词消息数组。
    复用 v8 的消息构造（不含简历）；在规则 15 之后追加规则 16（重点排查行业的归属与 BOSS 标签）；
    当有效简历（非空画像）数 >= 2 时再在规则 16 之后追加简历说明段与输出字段要求。
    在用户消息【岗位信息】的"- 所属区域：…"行后插入 BOSS 经验要求与学历要求标签。
    """
    msgs = prompt_v8.build_messages(
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
    sys_content += RULE_16_INSTRUCTION

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


parse_facts = prompt_v8.parse_facts
