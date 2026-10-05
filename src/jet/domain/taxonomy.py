from typing import Any

CATEGORIES: dict[str, list[str]] = {
    "数据与技术": ["数据分析", "数据处理与标注", "技术支持与实施", "开发与测试", "AI 相关"],
    "运营": ["用户运营", "内容运营", "产品运营", "活动运营", "电商运营", "新媒体与社群"],
    "产品与项目": ["产品经理或助理", "项目管理"],
    "内容与设计": ["编辑与文案", "设计与视频"],
    "市场与销售": ["市场营销与品牌", "销售与商务拓展", "客服与客户成功"],
    "科研与专业": ["实验与研发", "质检", "动物医学相关", "农牧生产"],
    "职能": ["行政", "人事", "财务"],
    "其他": [],
}

SUBTYPE_TO_CATEGORY: dict[str, str] = {
    subtype: cat
    for cat, subtypes in CATEGORIES.items()
    for subtype in subtypes
}

WORK_INTENSITY = ["高强度", "单休", "大小周", "双休", "未提及"]

VERDICTS = ["apply", "try", "check", "skip"]

VERDICT_LABELS = {
    "apply": "适合投递",
    "try": "可以一试",
    "check": "需要确认",
    "skip": "不建议投",
}

LEGACY_VERDICT_MAP = {
    "fit": "apply",
    "unsure": "check",
    "unfit": "skip",
}

LEGACY_WORK_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "运营": ("运营", None),
    "数据": ("数据与技术", None),
    "技术支持": ("数据与技术", "技术支持与实施"),
    "营销": ("市场与销售", "市场营销与品牌"),
    "销售": ("市场与销售", "销售与商务拓展"),
    "客服": ("市场与销售", "客服与客户成功"),
    "其他": ("其他", None),
}


def is_valid_pair(category: str | None, subtype: str | None = None) -> bool:
    """Check whether a (category, subtype) pair is valid. Subtype may be None."""
    if category is None or category not in CATEGORIES:
        return False
    if subtype is None:
        return True
    return subtype in CATEGORIES[category]


def normalize_secondary(
    items: Any,
    primary_category: str | None = None,
    primary_subtype: str | None = None,
) -> list[dict[str, str | None]]:
    """
    Validate, deduplicate, filter against primary, and truncate secondary work types to at most 3.

    Raises ValueError if items is not a list, contains invalid categories or subtypes, or malformed entries.
    """
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError("secondary_work_types 必须是列表或 null")

    res: list[dict[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()

    for item in items:
        if isinstance(item, dict):
            cat = item.get("category")
            sub = item.get("subtype")
        elif isinstance(item, (tuple, list)):
            cat = item[0] if len(item) > 0 else None
            sub = item[1] if len(item) > 1 else None
        elif isinstance(item, str):
            if item in CATEGORIES:
                cat = item
                sub = None
            elif item in SUBTYPE_TO_CATEGORY:
                cat = SUBTYPE_TO_CATEGORY[item]
                sub = item
            else:
                raise ValueError(f"未知的工作类型 '{item}'")
        else:
            raise ValueError(f"无效的次要工作类型项: {item}")

        if not isinstance(cat, str) or not is_valid_pair(cat, sub):
            raise ValueError(f"无效的工作类型组合: ({cat}, {sub})")

        if cat == primary_category and sub == primary_subtype:
            continue

        pair = (cat, sub)
        if pair in seen:
            continue

        seen.add(pair)
        res.append({"category": cat, "subtype": sub})

    return res[:3]
