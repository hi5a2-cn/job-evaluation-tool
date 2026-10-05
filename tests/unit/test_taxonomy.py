import pytest

from jet.domain.taxonomy import (
    CATEGORIES,
    LEGACY_VERDICT_MAP,
    LEGACY_WORK_TYPE_MAP,
    SUBTYPE_TO_CATEGORY,
    VERDICT_LABELS,
    VERDICTS,
    WORK_INTENSITY,
    is_valid_pair,
    normalize_secondary,
)


def test_categories_completeness():
    expected_categories = [
        "数据与技术",
        "运营",
        "产品与项目",
        "内容与设计",
        "市场与销售",
        "科研与专业",
        "职能",
        "其他",
    ]
    assert list(CATEGORIES.keys()) == expected_categories

    assert CATEGORIES["数据与技术"] == ["数据分析", "数据处理与标注", "技术支持与实施", "开发与测试", "AI 相关"]
    assert CATEGORIES["运营"] == ["用户运营", "内容运营", "产品运营", "活动运营", "电商运营", "新媒体与社群"]
    assert CATEGORIES["产品与项目"] == ["产品经理或助理", "项目管理"]
    assert CATEGORIES["内容与设计"] == ["编辑与文案", "设计与视频"]
    assert CATEGORIES["市场与销售"] == ["市场营销与品牌", "销售与商务拓展", "客服与客户成功"]
    assert CATEGORIES["科研与专业"] == ["实验与研发", "质检", "动物医学相关", "农牧生产"]
    assert CATEGORIES["职能"] == ["行政", "人事", "财务"]
    assert CATEGORIES["其他"] == []


def test_subtype_global_uniqueness():
    all_subtypes = [sub for subs in CATEGORIES.values() for sub in subs]
    assert len(all_subtypes) == len(set(all_subtypes))
    assert len(SUBTYPE_TO_CATEGORY) == len(all_subtypes)

    for cat, subs in CATEGORIES.items():
        for sub in subs:
            assert SUBTYPE_TO_CATEGORY[sub] == cat


def test_work_intensity_and_verdicts():
    assert WORK_INTENSITY == ["高强度", "单休", "大小周", "双休", "未提及"]
    assert VERDICTS == ["apply", "try", "check", "skip"]
    assert VERDICT_LABELS == {
        "apply": "适合投递",
        "try": "可以一试",
        "check": "需要确认",
        "skip": "不建议投",
    }


def test_legacy_maps():
    assert LEGACY_VERDICT_MAP == {
        "fit": "apply",
        "unsure": "check",
        "unfit": "skip",
    }
    assert LEGACY_WORK_TYPE_MAP == {
        "运营": ("运营", None),
        "数据": ("数据与技术", None),
        "技术支持": ("数据与技术", "技术支持与实施"),
        "营销": ("市场与销售", "市场营销与品牌"),
        "销售": ("市场与销售", "销售与商务拓展"),
        "客服": ("市场与销售", "客服与客户成功"),
        "其他": ("其他", None),
    }


def test_is_valid_pair():
    # Valid category and valid subtype
    assert is_valid_pair("数据与技术", "数据分析") is True
    assert is_valid_pair("市场与销售", "销售与商务拓展") is True

    # Valid category with None subtype
    assert is_valid_pair("数据与技术", None) is True
    assert is_valid_pair("运营", None) is True
    assert is_valid_pair("其他", None) is True

    # Category "其他" cannot have non-None subtype
    assert is_valid_pair("其他", "数据分析") is False

    # Subtype belongs to different category
    assert is_valid_pair("运营", "数据分析") is False
    assert is_valid_pair("职能", "用户运营") is False

    # Unknown category or None category
    assert is_valid_pair("未知大类", None) is False
    assert is_valid_pair("未知大类", "数据分析") is False
    assert is_valid_pair(None, None) is False
    assert is_valid_pair(None, "数据分析") is False


def test_normalize_secondary():
    # 1. Empty / None
    assert normalize_secondary(None) == []
    assert normalize_secondary([]) == []

    # 2. Non-list raises ValueError
    with pytest.raises(ValueError, match="secondary_work_types"):
        normalize_secondary("not a list")
    with pytest.raises(ValueError, match="secondary_work_types"):
        normalize_secondary({"category": "运营"})

    # 3. Deduplication (preserving order)
    raw_dup = [
        {"category": "运营", "subtype": "用户运营"},
        {"category": "运营", "subtype": "用户运营"},
        {"category": "市场与销售", "subtype": "市场营销与品牌"},
    ]
    norm_dup = normalize_secondary(raw_dup)
    assert norm_dup == [
        {"category": "运营", "subtype": "用户运营"},
        {"category": "市场与销售", "subtype": "市场营销与品牌"},
    ]

    # 4. Filter against primary
    raw_with_pri = [
        {"category": "运营", "subtype": "用户运营"},
        {"category": "市场与销售", "subtype": "市场营销与品牌"},
    ]
    norm_filtered = normalize_secondary(raw_with_pri, primary_category="运营", primary_subtype="用户运营")
    assert norm_filtered == [
        {"category": "市场与销售", "subtype": "市场营销与品牌"},
    ]

    # Filter with primary having None subtype
    raw_with_pri_none = [
        {"category": "其他", "subtype": None},
        {"category": "运营", "subtype": "用户运营"},
    ]
    norm_filtered_none = normalize_secondary(raw_with_pri_none, primary_category="其他", primary_subtype=None)
    assert norm_filtered_none == [
        {"category": "运营", "subtype": "用户运营"},
    ]

    # 5. Max 3 items truncation
    raw_four = [
        {"category": "数据与技术", "subtype": "开发与测试"},
        {"category": "运营", "subtype": "活动运营"},
        {"category": "内容与设计", "subtype": "编辑与文案"},
        {"category": "职能", "subtype": "行政"},
    ]
    norm_four = normalize_secondary(raw_four)
    assert len(norm_four) == 3
    assert norm_four == [
        {"category": "数据与技术", "subtype": "开发与测试"},
        {"category": "运营", "subtype": "活动运营"},
        {"category": "内容与设计", "subtype": "编辑与文案"},
    ]

    # 6. String inputs (category alone or unique subtype)
    raw_strings = ["运营", "编辑与文案", "开发与测试"]
    norm_strings = normalize_secondary(raw_strings)
    assert norm_strings == [
        {"category": "运营", "subtype": None},
        {"category": "内容与设计", "subtype": "编辑与文案"},
        {"category": "数据与技术", "subtype": "开发与测试"},
    ]

    # 7. Tuple / list inputs
    raw_tuples = [("运营", "内容运营"), ("职能", None)]
    norm_tuples = normalize_secondary(raw_tuples)
    assert norm_tuples == [
        {"category": "运营", "subtype": "内容运营"},
        {"category": "职能", "subtype": None},
    ]

    # 8. Invalid category / invalid subtype raises ValueError
    with pytest.raises(ValueError):
        normalize_secondary([{"category": "不存在的类别", "subtype": None}])

    with pytest.raises(ValueError):
        normalize_secondary([{"category": "运营", "subtype": "数据分析"}])

    with pytest.raises(ValueError):
        normalize_secondary(["未知字符串类型"])

    with pytest.raises(ValueError):
        normalize_secondary([12345])
