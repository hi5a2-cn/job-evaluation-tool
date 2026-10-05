import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence

DEFAULT_STRICT_INDUSTRY_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "llm" / "prompts" / "strict_industry_rules.json"
)


def read_strict_industries(rules_path: Path | str | None = None) -> list[str]:
    """从规则文件中读取重点排查行业列表，改文件即生效 (FR-057)。"""
    path = Path(rules_path) if rules_path else DEFAULT_STRICT_INDUSTRY_RULES_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        raw_industries = data.get("industries")
        if not isinstance(raw_industries, list):
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in raw_industries:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    result.append(cleaned)
        return result
    except Exception:
        return []


def read_strict_industry_aliases(rules_path: Path | str | None = None) -> dict[str, list[str]]:
    """从规则文件中读取重点排查行业的 BOSS 行业名称同义名 (FR-057)。
    只保留 industries 里有的键、值为非空字符串列表，去空去重；文件缺失/格式不对返回 {}。
    """
    path = Path(rules_path) if rules_path else DEFAULT_STRICT_INDUSTRY_RULES_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        raw_industries = data.get("industries")
        if not isinstance(raw_industries, list):
            return {}
        valid_industries: set[str] = set()
        for item in raw_industries:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    valid_industries.add(cleaned)

        raw_aliases = data.get("aliases")
        if not isinstance(raw_aliases, dict):
            return {}

        result: dict[str, list[str]] = {}
        for k, v in raw_aliases.items():
            if not isinstance(k, str):
                continue
            k_clean = k.strip()
            if k_clean not in valid_industries:
                continue
            if not isinstance(v, list):
                continue
            seen: set[str] = set()
            clean_list: list[str] = []
            for item in v:
                if isinstance(item, str):
                    val = item.strip()
                    if val and val not in seen:
                        seen.add(val)
                        clean_list.append(val)
            result[k_clean] = clean_list
        return result
    except Exception:
        return {}


def read_strict_industry_keywords(rules_path: Path | str | None = None) -> dict[str, list[str]]:
    """从规则文件中读取重点排查行业的职位名/公司名关键词 (004 修订)。
    只保留 industries 里有的键、值为非空字符串列表，去空去重；文件缺失/格式不对返回 {}。
    """
    path = Path(rules_path) if rules_path else DEFAULT_STRICT_INDUSTRY_RULES_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        raw_industries = data.get("industries")
        if not isinstance(raw_industries, list):
            return {}
        valid_industries: set[str] = set()
        for item in raw_industries:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    valid_industries.add(cleaned)

        raw_keywords = data.get("keywords")
        if not isinstance(raw_keywords, dict):
            return {}

        result: dict[str, list[str]] = {}
        for k, v in raw_keywords.items():
            if not isinstance(k, str):
                continue
            k_clean = k.strip()
            if k_clean not in valid_industries:
                continue
            if not isinstance(v, list):
                continue
            seen: set[str] = set()
            clean_list: list[str] = []
            for item in v:
                if isinstance(item, str):
                    val = item.strip()
                    if val and val not in seen:
                        seen.add(val)
                        clean_list.append(val)
            result[k_clean] = clean_list
        return result
    except Exception:
        return {}


def match_strict_industry(
    company_industry: str | None,
    industries: Sequence[str] | None = None,
    aliases: Mapping[str, Sequence[str]] | None = None,
    keywords: Mapping[str, Sequence[str]] | None = None,
    title: str | None = None,
    company_name: str | None = None,
) -> str | None:
    """匹配公司行业或职位名/公司名是否属于重点排查行业 (FR-001, FR-003, 004 修订)。

    先按现有规则用公司行业匹配：
    公司行业去首尾空格后与某行业名或其同义名相同，或包含该行业名或同义名即命中，返回行业名；
    多个行业命中取规则文件中靠前的一个。

    公司行业没命中时，再按行业顺序检查该行业的关键词是否出现在职位名或公司名中
    （去首尾空格后包含即命中），命中返回行业名。

    keywords 默认 None 时仅用公司行业匹配，行为与改前完全一致。
    规则文件缺失或格式错误时（industries 为空）返回 None 且不报错。
    """
    if not industries:
        return None

    # 1. 优先按公司行业匹配
    if company_industry is not None and isinstance(company_industry, str):
        # 比较时不区分大小写（IT、SaaS、4S 等写法不统一）
        clean_ci = company_industry.strip().lower()
        if clean_ci:
            aliases_map = aliases if isinstance(aliases, (dict, Mapping)) else {}
            for ind in industries:
                if not isinstance(ind, str):
                    continue
                ind_clean = ind.strip()
                if not ind_clean:
                    continue

                # 行业名自身相同或包含
                if ind_clean.lower() in clean_ci:
                    return ind_clean

                # 行业同义名相同或包含
                alias_list = aliases_map.get(ind_clean) or aliases_map.get(ind) or []
                if isinstance(alias_list, (list, tuple, Sequence)):
                    for alias in alias_list:
                        if not isinstance(alias, str):
                            continue
                        alias_clean = alias.strip()
                        if not alias_clean:
                            continue
                        if alias_clean.lower() in clean_ci:
                            return ind_clean

    # 2. 公司行业未命中时，按行业顺序检查关键词是否出现在职位名或公司名中
    if keywords is None or not isinstance(keywords, (dict, Mapping)):
        return None

    clean_title = title.strip().lower() if isinstance(title, str) else ""
    clean_company = company_name.strip().lower() if isinstance(company_name, str) else ""
    if not clean_title and not clean_company:
        return None

    for ind in industries:
        if not isinstance(ind, str):
            continue
        ind_clean = ind.strip()
        if not ind_clean:
            continue

        kw_list = keywords.get(ind_clean) or keywords.get(ind) or []
        if isinstance(kw_list, (list, tuple, Sequence)):
            for kw in kw_list:
                if not isinstance(kw, str):
                    continue
                kw_clean = kw.strip().lower()
                if not kw_clean:
                    continue
                if (clean_title and kw_clean in clean_title) or (clean_company and kw_clean in clean_company):
                    return ind_clean

    return None


class InvalidIndustryError(Exception):
    """Raised when an invalid strict industry is provided."""
    pass


def get_user_strict_industries(
    conn: sqlite3.Connection,
    user_id: str,
    rules_path: Path | str | None = None,
) -> list[str]:
    """读取指定用户勾选的从严行业列表 (FR-010, FR-011)。
    只返回当前规则文件中存在的行业，按规则文件中的顺序排列；
    已勾选但规则文件中已不存在的行业自动忽略 (Edge Cases)。
    未勾选时返回空列表 []。
    """
    available = read_strict_industries(rules_path=rules_path)
    cur = conn.execute(
        "SELECT industry FROM strict_industry_selection WHERE user_id = ?",
        (user_id,),
    )
    rows = cur.fetchall()
    selected_set = {r["industry"] if hasattr(r, "keys") else r[0] for r in rows}
    return [ind for ind in available if ind in selected_set]


def save_user_strict_industries(
    conn: sqlite3.Connection,
    user_id: str,
    selected: Sequence[str],
    rules_path: Path | str | None = None,
) -> list[str]:
    """保存指定用户勾选的从严行业列表 (FR-010, FR-014)。
    校验只接受规则文件中存在的行业；其余行业抛出 InvalidIndustryError。
    去重并保持规则文件中的先后顺序保存。
    """
    available = read_strict_industries(rules_path=rules_path)
    available_set = set(available)

    clean_selected: list[str] = []
    seen: set[str] = set()
    for item in selected:
        if not isinstance(item, str):
            raise InvalidIndustryError("行业名称必须为字符串")
        cleaned = item.strip()
        if not cleaned:
            continue
        if cleaned not in available_set:
            raise InvalidIndustryError(f"未知的从严行业: {cleaned}")
        if cleaned not in seen:
            seen.add(cleaned)
            clean_selected.append(cleaned)

    ordered_selected = [ind for ind in available if ind in seen]

    conn.execute("DELETE FROM strict_industry_selection WHERE user_id = ?", (user_id,))
    for ind in ordered_selected:
        conn.execute(
            "INSERT INTO strict_industry_selection (user_id, industry) VALUES (?, ?)",
            (user_id, ind),
        )
    return ordered_selected
