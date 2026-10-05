from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from jet.domain.industry import match_strict_industry
from jet.domain.normalize import normalize_city, normalize_text

# r1 = 不接受关键词匹配职位描述正文；r2 = 只匹配职位名称，2026-09-25
# r3 = 城市只按"不去的城市"排除，2026-09-25
RULES_VERSION = "r3"


@dataclass
class RuleResult:
    passed: bool
    hits: list[str]

    def to_json(self) -> str:
        return json.dumps({"passed": self.passed, "hits": self.hits}, ensure_ascii=False)


def _is_excluded_city(job_version: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    """
    判断城市是否在画像的「不去的城市」中。
    """
    job_city = str(job_version.get("city") or "")
    job_city_norm = normalize_city(job_city)
    if not job_city_norm:
        return False
    excluded_cities = list(profile.get("excluded_cities") or [])
    excluded_cities_norm = [normalize_city(c) for c in excluded_cities]
    return job_city_norm in excluded_cities_norm


def _salary_below_floor(job_version: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    """
    判断岗位薪资是否低于画像设定的最低月薪底线。
    薪资可见且解析成功、薪资上限低于最低月薪时返回 True，其余情况返回 False。
    """
    salary_visible = bool(job_version.get("salary_visible", 1))
    salary_parse_ok = bool(job_version.get("salary_parse_ok", 0))
    salary_max_k = job_version.get("salary_max_k")
    min_monthly_k = profile.get("min_monthly_k")

    if salary_visible and salary_parse_ok and salary_max_k is not None and min_monthly_k is not None:
        return bool(salary_max_k < min_monthly_k)
    return False


def _matched_exclude_keywords(
    title: str | None,
    tags: Any,
    exclude_keywords: Sequence[str] | None,
) -> list[tuple[str, str]]:
    """
    检查职位名称和标签是否命中画像的不接受关键词，按 exclude_keywords 传入顺序返回命中词与来源元组列表。
    每项为 (关键词原文, 来源)，来源取 "title" 或 "tags"。
    """
    if not exclude_keywords:
        return []

    # 英文缩写（BD、ToB、SaaS）大小写写法不统一，比较时不区分大小写
    title_norm = normalize_text(str(title or "")).lower()

    tags_norm: list[str] = []
    if tags is not None:
        if isinstance(tags, str):
            tags_norm = [normalize_text(tags).lower()]
        elif hasattr(tags, "__iter__"):
            tags_norm = [normalize_text(str(t)).lower() for t in tags if str(t).strip()]

    matched: list[tuple[str, str]] = []
    for kw in exclude_keywords:
        kw_norm = normalize_text(kw).lower()
        if not kw_norm:
            continue
        if kw_norm in title_norm:
            matched.append((kw, "title"))
        elif tags_norm and any(kw_norm in t for t in tags_norm):
            matched.append((kw, "tags"))

    return matched


def screen(
    job_version: Mapping[str, Any],
    profile: Mapping[str, Any],
    tags: Any = None,
) -> RuleResult:
    """
    Screen a job version against user profile using coarse deterministic rules.

    Rules applied:
    1. Excluded cities check: job city must not match any of profile's excluded_cities (after suffix stripping).
    2. Salary check: if salary is visible, parse_ok, and min_monthly_k is set,
       job's salary_max_k must not be less than min_monthly_k.
    3. Exclude keywords check: none of profile's exclude_keywords should appear
       in the normalized title (or tags if provided).
       职位描述正文不参与排除（正文里的否定表达和变相情况交给大模型判断）。
    """
    # 调用方可能传入 sqlite3.Row（没有 .get），统一转成 dict
    job_version = dict(job_version)
    profile = dict(profile)
    hits: list[str] = []

    # 1. Excluded cities check
    if _is_excluded_city(job_version, profile):
        job_city = str(job_version.get("city") or "")
        hits.append(f"城市在不去的城市中：{job_city}")

    # 2. Salary check
    if _salary_below_floor(job_version, profile):
        salary_max_k = job_version.get("salary_max_k")
        min_monthly_k = profile.get("min_monthly_k")
        fmt_max = f"{float(salary_max_k):g}"
        fmt_min = f"{float(min_monthly_k):g}"
        hits.append(f"薪资上限 {fmt_max}K 低于底线 {fmt_min}K")

    # 3. Exclude keywords check
    # 标签字段名待真实页面确认（NOT VERIFIED），暂不从 job_version 自动读取，
    # 留 tags 参数供调用方传入或后续扩展。
    exclude_keywords = list(profile.get("exclude_keywords") or [])
    title = str(job_version.get("title") or "")
    for kw, source in _matched_exclude_keywords(title, tags, exclude_keywords):
        if source == "title":
            hits.append(f"职位名称命中不接受关键词：{kw}")
        else:
            hits.append(f"岗位标签命中不接受关键词：{kw}")

    passed = len(hits) == 0
    return RuleResult(passed=passed, hits=hits)


def screen_hints(
    job_version: Mapping[str, Any] | None,
    profile: Mapping[str, Any] | None,
    company_industry: str | None = None,
    tags: Any = None,
    industries: Sequence[str] | None = None,
    aliases: Mapping[str, Sequence[str]] | None = None,
    company_name: str | None = None,
    keywords: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, str]]:
    """
    计算列表页岗位的粗筛提示 (FR-001, FR-002, FR-007, 004 修订)。

    返回 {"type", "text"} 列表：
    - type 取值: strict_industry / salary_floor / exclude_keyword / excluded_city
    - text 分别为:
      * 高风险行业（<行业名>）
      * 低于薪资底线
      * 命中不接受条件：<词>
      * 不去的城市
    - 顺序固定为: strict_industry → salary_floor → exclude_keyword → excluded_city
    - 同一类可有多条（例如命中多个不接受条件词）
    - profile 为 None 时只计算 strict_industry
    - 后三类复用现有 screen() 的同一套规则与画像字段
    - 薪资不可见、"面议"、无法解析不产生 salary_floor
    - 公司行业为空且未命中关键词时不产生 strict_industry
    """
    hints: list[dict[str, str]] = []

    job_version_dict = dict(job_version) if job_version is not None else {}
    title = job_version_dict.get("title")

    # 1. 重点排查行业（不依赖画像，支持公司行业匹配与职位名/公司名关键词匹配）
    matched_ind = match_strict_industry(
        company_industry,
        industries=industries,
        aliases=aliases,
        keywords=keywords,
        title=title,
        company_name=company_name,
    )
    if matched_ind:
        hints.append({"type": "strict_industry", "text": f"高风险行业（{matched_ind}）"})

    # 若未设置画像，其余三类规则不计算，直接返回（FR-001, Edge Cases）
    if profile is None:
        return hints

    profile_dict = dict(profile)

    # 2. 薪资底线（规则与 screen() 一致：薪资可见且能解析、薪资上限低于最低月薪）
    if _salary_below_floor(job_version_dict, profile_dict):
        hints.append({"type": "salary_floor", "text": "低于薪资底线"})

    # 3. 命中不接受条件（职位名、岗位标签与技能标签参与匹配）
    exclude_keywords = list(profile_dict.get("exclude_keywords") or [])
    matched_kws = _matched_exclude_keywords(
        job_version_dict.get("title"), tags, exclude_keywords
    )
    seen_kw: set[str] = set()
    for kw, _source in matched_kws:
        kw_norm = normalize_text(kw)
        if kw_norm not in seen_kw:
            seen_kw.add(kw_norm)
            hints.append({"type": "exclude_keyword", "text": f"命中不接受条件：{kw}"})

    # 4. 不去的城市
    if _is_excluded_city(job_version_dict, profile_dict):
        hints.append({"type": "excluded_city", "text": "不去的城市"})

    return hints
