import json
from pathlib import Path
import sqlite3
import pytest

from jet.domain.industry import (
    DEFAULT_STRICT_INDUSTRY_RULES_PATH,
    InvalidIndustryError,
    get_user_strict_industries,
    match_strict_industry,
    read_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
    save_user_strict_industries,
)
from jet.domain.rules import screen_hints
from jet.llm import prompt_v5, prompt_v6


def test_match_strict_industry_exact_match():
    """测试公司行业去首尾空格后与行业名或同义名完全相同 (FR-001)。"""
    industries = ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"]
    aliases = {
        "餐饮": ["餐饮"],
        "保险": ["保险"],
        "汽车": ["汽车", "新能源汽车", "汽车研发/制造"],
        "房地产": ["房地产", "房地产开发经营", "物业服务"],
        "美妆": ["美容/美发", "化妆品", "日化"],
        "快消": ["快速消费品", "日化", "食品/饮料/烟酒"],
    }

    # 1. 与行业名自身相同
    assert match_strict_industry("餐饮", industries, aliases) == "餐饮"
    assert match_strict_industry("保险", industries, aliases) == "保险"

    # 2. 与同义名相同（如"食品/饮料/烟酒"命中"快消"）
    assert match_strict_industry("食品/饮料/烟酒", industries, aliases) == "快消"
    assert match_strict_industry("新能源汽车", industries, aliases) == "汽车"
    assert match_strict_industry("物业服务", industries, aliases) == "房地产"

    # 3. 去首尾空格后相同
    assert match_strict_industry("  餐饮  ", industries, aliases) == "餐饮"
    assert match_strict_industry("  食品/饮料/烟酒  ", industries, aliases) == "快消"
    assert match_strict_industry("\t新能源汽车\n", industries, aliases) == "汽车"


def test_match_strict_industry_contains_match():
    """测试公司行业包含行业名或同义名即命中 (FR-001)。"""
    industries = ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"]
    aliases = {
        "餐饮": ["餐饮"],
        "保险": ["保险"],
        "汽车": ["汽车", "新能源汽车", "汽车研发/制造"],
        "房地产": ["房地产", "房地产开发经营", "物业服务"],
        "美妆": ["美容/美发", "化妆品", "日化"],
        "快消": ["快速消费品", "日化", "食品/饮料/烟酒"],
    }

    # 1. 包含行业名自身
    assert match_strict_industry("特色餐饮管理有限公司", industries, aliases) == "餐饮"
    assert match_strict_industry("互联网人寿保险经纪", industries, aliases) == "保险"

    # 2. 包含同义名
    assert match_strict_industry("高端快速消费品制造与零售", industries, aliases) == "快消"
    assert match_strict_industry("食品/饮料/烟酒连锁专营", industries, aliases) == "快消"
    assert match_strict_industry("新能源汽车动力电池研发", industries, aliases) == "汽车"
    assert match_strict_industry("商业物业服务集团", industries, aliases) == "房地产"


def test_match_strict_industry_no_match():
    """测试未命中的公司行业返回 None。"""
    industries = ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"]
    aliases = {
        "餐饮": ["餐饮"],
        "保险": ["保险"],
        "汽车": ["汽车", "新能源汽车"],
        "房地产": ["房地产"],
        "美妆": ["美容/美发", "化妆品"],
        "快消": ["快速消费品", "食品/饮料/烟酒"],
    }

    assert match_strict_industry("计算机软件", industries, aliases) is None
    assert match_strict_industry("互联网/游戏", industries, aliases) is None
    assert match_strict_industry("人工智能与大数据", industries, aliases) is None
    assert match_strict_industry("高等教育/职业培训", industries, aliases) is None
    assert match_strict_industry("医疗器械研发", industries, aliases) is None


def test_match_strict_industry_empty_or_none():
    """测试公司行业为空值（None、空字符串、纯空格、非字符串）时返回 None。"""
    industries = ["餐饮", "快消"]
    aliases = {"快消": ["食品/饮料/烟酒"]}

    assert match_strict_industry(None, industries, aliases) is None
    assert match_strict_industry("", industries, aliases) is None
    assert match_strict_industry("   ", industries, aliases) is None
    assert match_strict_industry("\t\n", industries, aliases) is None
    assert match_strict_industry(12345, industries, aliases) is None  # type: ignore[arg-type]
    assert match_strict_industry([], industries, aliases) is None  # type: ignore[arg-type]


def test_match_strict_industry_multiple_matches_order():
    """测试多个行业命中时取规则文件中靠前的一个。"""
    # 场景 1: "日化" 同时作为 "美妆" 与 "快消" 的同义名
    aliases = {
        "美妆": ["日化", "化妆品"],
        "快消": ["快速消费品", "日化"],
    }

    # 美妆在前 -> 返回美妆
    order1 = ["美妆", "快消"]
    assert match_strict_industry("日化", order1, aliases) == "美妆"
    assert match_strict_industry("日化用品生产", order1, aliases) == "美妆"

    # 快消在前 -> 返回快消
    order2 = ["快消", "美妆"]
    assert match_strict_industry("日化", order2, aliases) == "快消"
    assert match_strict_industry("日化用品生产", order2, aliases) == "快消"

    # 场景 2: 公司行业同时包含两个行业（如"汽车与房地产"）
    order_auto_first = ["汽车", "房地产"]
    assert match_strict_industry("汽车金融与房地产开发", order_auto_first, {}) == "汽车"

    order_realestate_first = ["房地产", "汽车"]
    assert match_strict_industry("汽车金融与房地产开发", order_realestate_first, {}) == "房地产"


def test_match_strict_industry_missing_or_corrupt_rules_file(tmp_path: Path):
    """测试规则文件缺失或格式错误时返回 None 且不报错。"""
    # 1. 文件不存在
    missing_file = tmp_path / "nonexistent_rules.json"
    industries_missing = read_strict_industries(missing_file)
    aliases_missing = read_strict_industry_aliases(missing_file)
    assert industries_missing == []
    assert aliases_missing == {}
    assert match_strict_industry("餐饮", industries_missing, aliases_missing) is None
    assert match_strict_industry("食品/饮料/烟酒", industries_missing, aliases_missing) is None

    # 2. 格式错误 (坏 JSON)
    corrupt_file = tmp_path / "corrupt_rules.json"
    corrupt_file.write_text("{bad json syntax", encoding="utf-8")
    industries_corrupt = read_strict_industries(corrupt_file)
    aliases_corrupt = read_strict_industry_aliases(corrupt_file)
    assert industries_corrupt == []
    assert aliases_corrupt == {}
    assert match_strict_industry("餐饮", industries_corrupt, aliases_corrupt) is None
    assert match_strict_industry("食品/饮料/烟酒", industries_corrupt, aliases_corrupt) is None

    # 3. 根对象不是字典
    not_dict_file = tmp_path / "not_dict.json"
    not_dict_file.write_text(json.dumps(["餐饮", "快消"]), encoding="utf-8")
    industries_not_dict = read_strict_industries(not_dict_file)
    aliases_not_dict = read_strict_industry_aliases(not_dict_file)
    assert industries_not_dict == []
    assert aliases_not_dict == {}
    assert match_strict_industry("餐饮", industries_not_dict, aliases_not_dict) is None

    # 4. industries 为 None 或空列表直接传入 match_strict_industry
    assert match_strict_industry("餐饮", None, None) is None
    assert match_strict_industry("餐饮", [], {}) is None
    assert match_strict_industry("食品/饮料/烟酒", [], {"快消": ["食品/饮料/烟酒"]}) is None


def test_domain_industry_default_rules():
    """测试 domain.industry 默认加载仓库真实规则文件，并成功匹配真实样本。"""
    assert DEFAULT_STRICT_INDUSTRY_RULES_PATH.exists()

    industries = read_strict_industries()
    aliases = read_strict_industry_aliases()

    assert "餐饮" in industries
    assert "快消" in industries
    assert "美妆" in industries
    assert "汽车" in industries

    # 真实样本匹配验证
    assert match_strict_industry("食品/饮料/烟酒", industries, aliases) == "快消"
    assert match_strict_industry("新能源汽车", industries, aliases) == "汽车"
    assert match_strict_industry("餐饮", industries, aliases) == "餐饮"
    assert match_strict_industry("房地产中介/租赁", industries, aliases) == "房地产"
    # 日化在美妆与快消中均有，美妆在规则文件中排在快消前
    assert match_strict_industry("日化", industries, aliases) == "美妆"


def test_read_strict_industry_keywords_scenarios(tmp_path: Path):
    """测试 read_strict_industry_keywords 正常读取/文件缺失/坏 JSON/过滤/去空去重。"""
    # 1. 默认仓库文件读取
    repo_keywords = read_strict_industry_keywords()
    assert "房地产" in repo_keywords
    assert "中介" in repo_keywords["房地产"]
    assert "置业" in repo_keywords["房地产"]
    assert "汽车" in repo_keywords
    assert "4S" in repo_keywords["汽车"]

    # 2. 文件不存在
    assert read_strict_industry_keywords(tmp_path / "nonexistent.json") == {}

    # 3. 坏 JSON 与根非字典
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    assert read_strict_industry_keywords(bad) == {}

    not_dict = tmp_path / "not_dict.json"
    not_dict.write_text("[]", encoding="utf-8")
    assert read_strict_industry_keywords(not_dict) == {}

    # 4. 只保留 industries 里有的键，去重去空过滤非字符串
    custom = tmp_path / "custom.json"
    custom.write_text(
        json.dumps({
            "industries": ["房地产", "汽车"],
            "keywords": {
                "房地产": [" 房产 ", "中介", "房产", "", 123, None, "  "],
                "未知": ["测试"],
                "汽车": "非列表值",
            },
        }),
        encoding="utf-8",
    )
    res = read_strict_industry_keywords(custom)
    assert "未知" not in res
    assert "汽车" not in res
    assert res["房地产"] == ["房产", "中介"]


def test_match_strict_industry_real_regression_and_keywords():
    """真实回归样例与职位名/公司名关键词命中 (004 修订)。
    - 公司行业"物业管理"命中房地产；
    - 公司行业为空、职位名"房地产/厂房(中介)"命中房地产；
    - 公司名含"置业"命中房地产。
    """
    industries = read_strict_industries()
    aliases = read_strict_industry_aliases()
    keywords = read_strict_industry_keywords()

    # 1. 真实回归样例 1：公司行业"物业管理"（BOSS 真实行业名）命中房地产
    assert match_strict_industry("物业管理", industries, aliases, keywords) == "房地产"
    assert match_strict_industry("  物业管理  ", industries, aliases, keywords) == "房地产"

    # 2. 真实回归样例 2：公司行业为空、职位名"房地产/厂房(中介)"命中房地产
    assert match_strict_industry(None, industries, aliases, keywords, title="房地产/厂房(中介)") == "房地产"
    assert match_strict_industry("", industries, aliases, keywords, title="房地产/厂房(中介)") == "房地产"
    assert match_strict_industry("   ", industries, aliases, keywords, title="  房地产/厂房(中介)  ") == "房地产"

    # 3. 真实回归样例 3：公司名含"置业"命中房地产（职位名不含该词）
    assert match_strict_industry(
        None,
        industries,
        aliases,
        keywords,
        title="广州伙伴产业服务管培生",
        company_name="保利置业集团有限公司",
    ) == "房地产"


def test_match_strict_industry_company_industry_priority_over_keywords():
    """公司行业命中优先于关键词：公司行业命中 A、职位名含 B 的关键词时返回 A。"""
    industries = ["餐饮", "汽车", "房地产"]
    aliases = {
        "餐饮": ["餐饮", "餐饮管理"],
        "汽车": ["汽车"],
        "房地产": ["房地产", "物业管理"],
    }
    keywords = {
        "餐饮": ["餐厅", "茶饮"],
        "汽车": ["新能源车", "4S"],
        "房地产": ["置业", "中介"],
    }

    # 公司行业命中"餐饮"，职位名含"新能源车"（汽车关键词）、公司名含"置业"（房地产关键词） -> 必须返回餐饮
    matched = match_strict_industry(
        company_industry="餐饮管理",
        industries=industries,
        aliases=aliases,
        keywords=keywords,
        title="新能源车业务主管",
        company_name="大唐置业科技",
    )
    assert matched == "餐饮"


def test_match_strict_industry_keywords_none_backward_compatible():
    """keywords 参数为 None 时 match_strict_industry 行为与改前完全一致。"""
    industries = ["房地产", "汽车"]
    aliases = {"房地产": ["物业管理"]}

    # keywords=None 时，职位名和公司名有关键词也不会被关键词匹配
    assert match_strict_industry(
        None,
        industries=industries,
        aliases=aliases,
        keywords=None,
        title="房地产中介",
        company_name="恒大置业",
    ) is None

    # keywords=None 时，公司行业正常命中
    assert match_strict_industry(
        "物业管理",
        industries=industries,
        aliases=aliases,
        keywords=None,
        title="开发",
    ) == "房地产"


def test_user_strict_industries_db_helpers(tmp_path: Path):
    """测试 get_user_strict_industries 与 save_user_strict_industries。"""
    db_path = tmp_path / "test_user_industries.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    conn.execute("""
    CREATE TABLE strict_industry_selection (
        user_id TEXT NOT NULL,
        industry TEXT NOT NULL,
        PRIMARY KEY(user_id, industry),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    conn.execute("INSERT INTO users (id) VALUES ('u1'), ('u2')")
    conn.commit()

    # 1. 初始空勾选
    assert get_user_strict_industries(conn, "u1") == []

    # 2. 保存合法子集（传入顺序故意打乱）
    save_user_strict_industries(conn, "u1", ["快消", "汽车", "餐饮"])
    # 读取返回必须严格遵循规则文件的顺序
    saved = get_user_strict_industries(conn, "u1")
    assert saved == ["餐饮", "汽车", "快消"]

    # 3. 库中包含已被规则文件移除的未知行业时静默忽略
    conn.execute("INSERT INTO strict_industry_selection (user_id, industry) VALUES ('u1', '已废弃行业')")
    conn.commit()
    assert get_user_strict_industries(conn, "u1") == ["餐饮", "汽车", "快消"]

    # 4. 保存包含未知行业时抛出 InvalidIndustryError
    with pytest.raises(InvalidIndustryError) as exc_info:
        save_user_strict_industries(conn, "u1", ["餐饮", "非法行业"])
    assert "非法行业" in str(exc_info.value)

    # 5. 清空勾选
    save_user_strict_industries(conn, "u1", [])
    assert get_user_strict_industries(conn, "u1") == []


def test_prompt_exact_character_equality_when_all_industries_selected():
    """对规则文件全部行业都勾选的用户，提示词必须与改动前逐字相同 (FR-012)。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI", "SQLAlchemy"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
        "exclude_keywords": ["外包"],
        "work_preference": "不愿做销售",
        "background": "5年后端开发经验",
    }
    job_version = {
        "title": "后端开发专家",
        "company_name": "某知名汽车科技有限公司",
        "salary_raw": "25-35K",
        "salary_visible": True,
        "city": "深圳",
        "district": "南山区",
        "description": "负责车联网后端架构设计与服务开发",
    }
    resumes = [
        {"slot": 1, "name": "通用简历", "job_types": "后端开发, 架构师"},
    ]

    all_industries = read_strict_industries()
    all_aliases = read_strict_industry_aliases()
    all_keywords = read_strict_industry_keywords()
    assert len(all_industries) == 7

    # 1. prompt_v5: 传入全部 6 个行业及对应同义词关键词 vs 之前默认（None）逐字完全相同
    msgs_v5_all = prompt_v5.build_messages(
        profile,
        job_version,
        strict_industries=all_industries,
        strict_industry_aliases=all_aliases,
        strict_industry_keywords=all_keywords,
    )
    msgs_v5_default = prompt_v5.build_messages(profile, job_version, strict_industries=None)
    assert msgs_v5_all == msgs_v5_default
    assert msgs_v5_all[0]["content"] == msgs_v5_default[0]["content"]
    assert msgs_v5_all[1]["content"] == msgs_v5_default[1]["content"]

    # 2. prompt_v6: 传入全部 6 个行业及对应同义词关键词 vs 之前默认（None）逐字完全相同
    msgs_v6_all = prompt_v6.build_messages(
        profile,
        job_version,
        strict_industries=all_industries,
        strict_industry_aliases=all_aliases,
        strict_industry_keywords=all_keywords,
        resumes=resumes,
    )
    msgs_v6_default = prompt_v6.build_messages(profile, job_version, resumes=resumes, strict_industries=None)
    assert msgs_v6_all == msgs_v6_default
    assert msgs_v6_all[0]["content"] == msgs_v6_default[0]["content"]
    assert msgs_v6_all[1]["content"] == msgs_v6_default[1]["content"]


def test_prompt_no_strict_industry_section_when_zero_selected():
    """未勾选任何从严行业时，提示词中不得包含从严行业段落 (FR-012)。"""
    profile = {"directions": ["Python开发"]}
    job_version = {
        "title": "后端开发",
        "company_name": "某餐饮数字化公司",
        "city": "深圳",
        "description": "开发",
    }
    resumes = [{"slot": 1, "name": "通用简历", "job_types": "后端开发"}]

    # prompt_v5 勾选为空
    msgs_v5 = prompt_v5.build_messages(profile, job_version, strict_industries=[])
    assert "重点排查行业" not in msgs_v5[0]["content"]
    assert "餐饮" not in msgs_v5[0]["content"]

    # prompt_v6 勾选为空
    msgs_v6 = prompt_v6.build_messages(profile, job_version, resumes=resumes, strict_industries=[])
    assert "重点排查行业" not in msgs_v6[0]["content"]
    assert "餐饮" not in msgs_v6[0]["content"]


def test_screen_hints_respects_user_strict_industries():
    """粗筛结果只受当前用户勾选行业影响：未勾选不给'高风险行业'提示，勾选对应行业才提示。"""
    all_industries = read_strict_industries()
    all_aliases = read_strict_industry_aliases()
    all_keywords = read_strict_industry_keywords()

    job = {"title": "置业顾问", "salary_visible": True, "salary_parse_ok": True, "salary_max_k": 20.0}

    # 1. 用户全部勾选：命中"房地产" -> 产生高风险行业提示
    hints_all = screen_hints(
        job,
        profile=None,
        company_industry="房地产/建筑",
        industries=all_industries,
        aliases=all_aliases,
        keywords=all_keywords,
    )
    assert any(h["type"] == "strict_industry" and "房地产" in h["text"] for h in hints_all)

    # 2. 用户仅勾选"餐饮"与"汽车"（未勾选"房地产"）：不产生高风险行业提示
    user_inds = ["餐饮", "汽车"]
    user_aliases = {k: all_aliases[k] for k in user_inds if k in all_aliases}
    user_keywords = {k: all_keywords[k] for k in user_inds if k in all_keywords}
    hints_partial = screen_hints(
        job,
        profile=None,
        company_industry="房地产/建筑",
        industries=user_inds,
        aliases=user_aliases,
        keywords=user_keywords,
    )
    assert not any(h["type"] == "strict_industry" for h in hints_partial)

    # 3. 用户 0 勾选：完全不产生高风险行业提示
    hints_empty = screen_hints(
        job,
        profile=None,
        company_industry="房地产/建筑",
        industries=[],
        aliases={},
        keywords={},
    )
    assert not any(h["type"] == "strict_industry" for h in hints_empty)
