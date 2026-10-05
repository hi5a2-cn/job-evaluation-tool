import json
import re
from typing import Any, Mapping, Sequence

VALID_PREJUDGE_LEVELS = {"open", "neutral", "skip"}

SYSTEM_PROMPT = """你是一个专业的求职决策分析助手。
本判断仅基于列表粗略信息，不构成正式结论。
请根据候选人画像与重点排查行业，对列表中的每个岗位快速做出粗略筛选导向：
- open（值得点开）：整体与目标方向、技能、薪资、地点较为匹配，偏好相符，建议点开详情深入了解；
- neutral（一般）：匹配度一般、信息模糊、或部分条件略有偏差但非核心冲突；
- skip（可以跳过）：核心条件明确冲突（如工作地点在排除城市、薪资远低于底线、明确不想做的方向、命中从严行业且大概率变相销售等）。

评估准则：
1. 仅依据列表页客观字段（职位名、公司名、行业、薪资、城市区县、经验、学历、标签技能）与用户画像对照。
2. 每个岗位输出一句简明具体的理由（≤30字），说明为什么建议点开、一般或跳过。
3. 严格只输出 JSON 格式，不得包含任何 markdown 标记、解释或额外文字。

输出 JSON 格式规范：
{
  "results": [
    {
      "id": "序号",
      "level": "open|neutral|skip",
      "reason": "一句话理由（≤30字）"
    }
  ]
}
"""

def build_prejudge_messages(
    profile: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    strict_industries: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """
    Build LLM messages for batch list prejudgement (T006).

    STRICT WHITELIST ENFORCEMENT:
    - Profile: directions, keywords, preferred_cities, excluded_cities, min_monthly_k,
      nonpref_min_monthly_k, exclude_keywords, work_preference, background, strict_industries.
    - Jobs: title, company_name, company_industry, salary_raw,
      city, district, experience, degree, job_labels, skills.
    ABSOLUTELY NO platform_job_id, hr_notes, chats, recruiter info, resumes, or full job descriptions.
    """
    prof = dict(profile)

    # 1. Profile fields (whitelisted)
    directions_val = prof.get("directions") or []
    directions = "、".join(directions_val) if isinstance(directions_val, list) else str(directions_val)

    keywords_val = prof.get("keywords") or []
    keywords = "、".join(keywords_val) if isinstance(keywords_val, list) else str(keywords_val)

    pref_cities_val = prof.get("preferred_cities")
    if pref_cities_val is None:
        pref_cities_val = prof.get("cities") or []
    pref_cities = "、".join(pref_cities_val) if isinstance(pref_cities_val, list) else str(pref_cities_val)

    excluded_cities_val = prof.get("excluded_cities") or []
    excluded_cities = "、".join(excluded_cities_val) if isinstance(excluded_cities_val, list) else str(excluded_cities_val)

    min_k = f"{prof.get('min_monthly_k')}K" if prof.get("min_monthly_k") is not None else "不限"
    nonpref_k = (
        f"{prof.get('nonpref_min_monthly_k')}K"
        if prof.get("nonpref_min_monthly_k") is not None
        else "不限"
    )

    raw_exclude = prof.get("exclude_keywords") or []
    exclude_keywords = "、".join(raw_exclude) if isinstance(raw_exclude, list) else str(raw_exclude)
    work_pref = str(prof.get("work_preference") or "").strip() or "无"
    background = str(prof.get("background") or "").strip() or "无"

    strict_inds = "、".join(strict_industries) if strict_industries else "无"

    user_lines = [
        "【候选人画像】",
        f"- 目标方向：{directions or '无'}",
        f"- 技能与关键词：{keywords or '无'}",
        f"- 偏好城市：{pref_cities or '无'}",
        f"- 不去的城市：{excluded_cities or '无'}",
        f"- 最低月薪底线：{min_k}",
        f"- 非偏好城市最低月薪：{nonpref_k}",
        f"- 不接受条件：{exclude_keywords or '无'}",
        f"- 工作内容偏好：{work_pref}",
        f"- 我的背景：{background}",
        f"- 重点排查行业：{strict_inds}",
        "",
        "【待预判岗位列表】",
    ]

    for idx, j in enumerate(jobs, 1):
        job_dict = dict(j)
        title = str(job_dict.get("title") or "")
        company = str(job_dict.get("company_name") or "未提供").strip()
        industry = str(job_dict.get("company_industry") or "未提供").strip()
        salary = str(job_dict.get("salary_raw") or "未提供").strip()
        city = str(job_dict.get("city") or "")
        district = str(job_dict.get("district") or "")
        loc = f"{city} {district}".strip() if district else city

        exp = str(job_dict.get("experience") or "未提及").strip()
        deg = str(job_dict.get("degree") or "未提及").strip()

        labels = job_dict.get("job_labels") or []
        labels_str = "、".join(labels) if isinstance(labels, list) and labels else "无"

        skills = job_dict.get("skills") or []
        skills_str = "、".join(skills) if isinstance(skills, list) and skills else "无"

        user_lines.append(f"[{idx}] 序号: {idx}")
        user_lines.append(f"  职位名称: {title}")
        user_lines.append(f"  公司名称: {company} | 公司行业: {industry}")
        user_lines.append(f"  薪资: {salary} | 地点: {loc}")
        user_lines.append(f"  经验: {exp} | 学历: {deg}")
        user_lines.append(f"  岗位标签: {labels_str}")
        user_lines.append(f"  技能标签: {skills_str}")
        user_lines.append("")

    user_lines.append("请依据上述画像对上述每个岗位给出粗略导向预判（open/neutral/skip）与一句理由（≤30字），只返回符合要求的 JSON。")

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_lines)},
    ]


def parse_prejudge_response(
    content: str,
    requested_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    """
    Parse LLM prejudge response JSON and validate fields (T006).

    Returns a dict mapping platform_job_id -> {"level": level, "reason": reason}.
    Tolerates format variations, unknown IDs, invalid levels, and truncates reason to <= 40 chars.
    """
    if not content or not str(content).strip():
        return {}

    cleaned = content.strip()
    # Strip markdown code blocks if wrapped
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        # Try extracting JSON substring
        match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except Exception:
                return {}
        else:
            return {}

    raw_items: list[Any] = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        for candidate_key in ("results", "jobs", "prejudgements", "items"):
            if candidate_key in data and isinstance(data[candidate_key], list):
                raw_items = data[candidate_key]
                break
        else:
            # Maybe the dict is keyed by job id: {"job_1": {"level": ..., "reason": ...}}
            for k, v in data.items():
                if isinstance(v, dict) and "level" in v:
                    raw_items.append({"id": k, **v})

    valid_id_set = set(str(x) for x in requested_ids)
    results: dict[str, dict[str, str]] = {}

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id not in valid_id_set:
            continue

        raw_level = str(item.get("level") or "").strip().lower()
        if raw_level not in VALID_PREJUDGE_LEVELS:
            continue

        raw_reason = str(item.get("reason") or "").strip()
        reason = raw_reason[:40] if len(raw_reason) > 40 else raw_reason

        results[item_id] = {
            "level": raw_level,
            "reason": reason,
        }

    return results
