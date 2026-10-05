from typing import Any, Mapping, Sequence

from jet.domain.industry import (
    DEFAULT_STRICT_INDUSTRY_RULES_PATH,
    read_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
)
from jet.domain.normalize import normalize_city
from jet.llm import prompt_v4
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
    parse_facts,
)

PROMPT_VERSION = "v5"


def build_strict_industry_instruction(
    industries: Sequence[str] | None,
    aliases: Mapping[str, Sequence[str]] | None = None,
    keywords: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """构建重点排查行业的提示词说明（FR-057, 004 修订）。列表为空返回空字符串。
    只传一个参数时输出现有文字保持兼容；传了 aliases 时使用新文字并为有同义名的行业补充 BOSS 行业名说明；
    传了 keywords 时补充职位名/公司名关键词说明并更新判断顺序。
    """
    if not industries:
        return ""

    if keywords is None:
        if aliases is None:
            joined = "、".join(industries)
            point2 = '    - 先根据公司名称、职位名称和职位描述判断公司或岗位是否属于上述行业之一。\n'
        else:
            items: list[str] = []
            for ind in industries:
                synonyms = [
                    a for a in aliases.get(ind, [])
                    if isinstance(a, str) and a.strip() and a.strip() != ind.strip()
                ]
                if synonyms:
                    items.append(f"{ind}（BOSS 行业名如：{'、'.join(synonyms)}）")
                else:
                    items.append(ind)
            joined = "、".join(items)
            point2 = (
                '    - 优先依据"公司行业"判断公司是否属于上述行业（公司行业与行业名或其同义名相同或包含即算属于）；'
                '公司行业为"未提供"时，再根据公司名称、职位名称和职位描述判断。\n'
            )
    else:
        items: list[str] = []
        aliases_map = aliases if isinstance(aliases, (dict, Mapping)) else {}
        keywords_map = keywords if isinstance(keywords, (dict, Mapping)) else {}
        for ind in industries:
            parts: list[str] = []
            synonyms = [
                a for a in aliases_map.get(ind, [])
                if isinstance(a, str) and a.strip() and a.strip() != ind.strip()
            ]
            if synonyms:
                parts.append(f"BOSS 行业名如：{'、'.join(synonyms)}")
            kws = [
                k for k in keywords_map.get(ind, [])
                if isinstance(k, str) and k.strip()
            ]
            if kws:
                parts.append(f"职位名或公司名含：{'、'.join(kws)}")
            if parts:
                items.append(f"{ind}（{'；'.join(parts)}）")
            else:
                items.append(ind)
        joined = "、".join(items)
        point2 = (
            '    - 先看公司行业（与行业名或同义名相同或包含即算）；'
            '公司行业未命中或为"未提供"时，职位名或公司名含该行业关键词也算属于；'
            '都没有时再根据职位描述判断。\n'
        )

    return (
        f"13. 重点排查行业（{joined}）：\n"
        f"{point2}"
        '    - 属于时，从严判断销售与客户对接成分：职责描述模糊、无法确认是否需要对接客户或承担业绩指标时，按"含销售"处理，sales_level 至少为"中"，并在 derivation 中写明"属于重点排查行业（某行业），职责描述模糊，按含销售处理"。\n'
        '    - 属于且 hr_questions 非空时，必须包含一个确认销售成分的问题（如是否需要对接客户、是否有业绩或销售指标）。\n'
        '    - 这些行业不直接排除：数据、技术、运营等岗位职责明确不含销售时，按实际职责判断。'
    )


CITY_SALARY_INSTRUCTION = """
12. 城市与薪资说明：
    - 偏好城市只是偏好，不在偏好城市不等于不适合，要结合薪资等权衡。
    - "不去的城市"与"最低月薪"已由规则处理。
    - 若给出了"非偏好城市的最低月薪"，岗位不在偏好城市且薪资上限低于它时，结论最多 check，并在推导中说明。
"""

SYSTEM_PROMPT = V4_SYSTEM_PROMPT + "\n" + CITY_SALARY_INSTRUCTION


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
    strict_industries: Sequence[str] | None = None,
    strict_industry_aliases: Mapping[str, Sequence[str]] | None = None,
    strict_industry_keywords: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, str]]:
    """Build messages array with prompt v5 system prompt, profile details, and job details."""
    profile = dict(profile)
    job_version = dict(job_version)

    if strict_industries is None:
        industries = read_strict_industries()
        aliases = read_strict_industry_aliases() if strict_industry_aliases is None else strict_industry_aliases
        keywords = read_strict_industry_keywords() if strict_industry_keywords is None else strict_industry_keywords
    else:
        industries = list(strict_industries)
        aliases = strict_industry_aliases
        keywords = strict_industry_keywords

    instruction = build_strict_industry_instruction(industries, aliases=aliases, keywords=keywords)
    if instruction:
        system_content = SYSTEM_PROMPT + "\n" + instruction
    else:
        system_content = SYSTEM_PROMPT

    directions = "、".join(profile.get("directions") or [])
    raw_keywords = profile.get("keywords") or []
    keywords = "、".join(raw_keywords) if raw_keywords else "无"

    raw_pref = profile.get("preferred_cities")
    if raw_pref is None:
        raw_pref = profile.get("cities") or []
    preferred_cities = "、".join(raw_pref) if raw_pref else "无"

    raw_excluded = profile.get("excluded_cities") or []
    excluded_cities = "、".join(raw_excluded) if raw_excluded else "无"

    min_k = f"{profile.get('min_monthly_k')}K" if profile.get("min_monthly_k") is not None else "不限"
    nonpref_k = (
        f"{profile.get('nonpref_min_monthly_k')}K"
        if profile.get("nonpref_min_monthly_k") is not None
        else "不限"
    )

    raw_exclude = profile.get("exclude_keywords") or []
    exclude_keywords = "、".join(raw_exclude) if raw_exclude else "无"
    work_pref = str(profile.get("work_preference") or "").strip() or "无"
    background = str(profile.get("background") or "").strip() or "无"

    title = str(job_version.get("title") or "")
    raw_company = job_version.get("company_name")
    company = str(raw_company).strip() if raw_company is not None and str(raw_company).strip() else "未提供"
    raw_industry = job_version.get("company_industry")
    industry = str(raw_industry).strip() if raw_industry is not None and str(raw_industry).strip() else "未提供"

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
- 偏好城市（为空表示城市都可以）：{preferred_cities}
- 不去的城市：{excluded_cities}
- 最低月薪：{min_k}
- 非偏好城市的最低月薪：{nonpref_k}
- 不接受条件：{exclude_keywords}
- 工作内容偏好：{work_pref}
- 我的背景：{background}

【岗位信息】
- 职位名称：{title}
- 公司名称：{company}
- 公司行业：{industry}
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
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def apply_city_salary_cap(
    verdict: str,
    derivation: list[str],
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """
    Apply city & salary cap rule:
    When nonpref_min_monthly_k is set, salary_visible and salary_parse_ok are true,
    salary_max_k is not None, job city (normalized) is not in preferred_cities (normalized,
    empty preferred_cities treated as all preferred and does not trigger),
    and salary_max_k < nonpref_min_monthly_k:
    if verdict is apply or try -> change to check, insert explanation at index 0 of derivation
    (max 5 items in derivation).
    """
    profile = dict(profile)
    job_version = dict(job_version)

    nonpref_min_monthly_k = profile.get("nonpref_min_monthly_k")
    if nonpref_min_monthly_k is None:
        return verdict, list(derivation)

    salary_visible = bool(job_version.get("salary_visible"))
    salary_parse_ok = bool(job_version.get("salary_parse_ok"))
    if not (salary_visible and salary_parse_ok):
        return verdict, list(derivation)

    salary_max_k = job_version.get("salary_max_k")
    if salary_max_k is None:
        return verdict, list(derivation)

    raw_pref = profile.get("preferred_cities")
    if raw_pref is None:
        raw_pref = profile.get("cities") or []

    # 偏好城市为空时视为"都偏好"，不触发
    if not raw_pref:
        return verdict, list(derivation)

    job_city = str(job_version.get("city") or "")
    job_city_norm = normalize_city(job_city)
    pref_cities_norm = [normalize_city(c) for c in raw_pref]

    # 岗位城市在偏好城市中，不触发
    if job_city_norm in pref_cities_norm:
        return verdict, list(derivation)

    try:
        max_k_val = float(salary_max_k)
        nonpref_k_val = float(nonpref_min_monthly_k)
    except (ValueError, TypeError):
        return verdict, list(derivation)

    if max_k_val >= nonpref_k_val:
        return verdict, list(derivation)

    if verdict in ("apply", "try"):
        new_verdict = "check"
        fmt_x = f"{max_k_val:g}"
        fmt_y = f"{nonpref_k_val:g}"
        note = f"岗位不在偏好城市且薪资上限 {fmt_x}K 低于非偏好城市最低月薪 {fmt_y}K，结论最多需要确认"
        new_derivation = [note] + list(derivation)
        return new_verdict, new_derivation[:5]

    return verdict, list(derivation)
