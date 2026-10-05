from fastapi.testclient import TestClient
from jet.domain.industry import (
    read_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
)
from jet.domain.rules import screen, screen_hints


def test_screen_hints_four_types_hit_and_miss():
    """测试四类提示各自命中与不命中 (FR-001, FR-007)。"""
    industries = ["快消", "餐饮"]
    aliases = {"快消": ["食品/饮料/烟酒"]}

    profile = {
        "min_monthly_k": 10.0,
        "exclude_keywords": ["销售"],
        "excluded_cities": ["北京"],
    }

    # 1. strict_industry
    # 命中（同义名）
    hints = screen_hints(
        job_version=None,
        profile=None,
        company_industry="食品/饮料/烟酒",
        industries=industries,
        aliases=aliases,
    )
    assert hints == [{"type": "strict_industry", "text": "高风险行业（快消）"}]

    # 不命中（非重点行业、空值、空白字符）
    assert screen_hints(None, None, "互联网", industries=industries, aliases=aliases) == []
    assert screen_hints(None, None, "", industries=industries, aliases=aliases) == []
    assert screen_hints(None, None, "   ", industries=industries, aliases=aliases) == []
    assert screen_hints(None, None, None, industries=industries, aliases=aliases) == []

    # 2. salary_floor
    # 命中（薪资上限 8K < 底线 10K）
    job_low = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 8.0,
    }
    hints = screen_hints(job_low, profile)
    assert hints == [{"type": "salary_floor", "text": "低于薪资底线"}]

    # 不命中（薪资上限 12K >= 底线 10K）
    job_ok = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 12.0,
    }
    assert screen_hints(job_ok, profile) == []

    # 3. exclude_keyword
    # 命中（职位名含销售）
    job_kw = {"city": "深圳", "title": "电话销售经理"}
    hints = screen_hints(job_kw, profile)
    assert hints == [{"type": "exclude_keyword", "text": "命中不接受条件：销售"}]

    # 不命中（职位名不含销售）
    job_kw_miss = {"city": "深圳", "title": "后端架构师"}
    assert screen_hints(job_kw_miss, profile) == []

    # 4. excluded_city
    # 命中（城市为北京）
    job_city = {"city": "北京市", "title": "开发"}
    hints = screen_hints(job_city, profile)
    assert hints == [{"type": "excluded_city", "text": "不去的城市"}]

    # 不命中（城市为深圳）
    job_city_miss = {"city": "深圳", "title": "开发"}
    assert screen_hints(job_city_miss, profile) == []


def test_screen_hints_fixed_order():
    """测试四类提示同时命中时的固定顺序：strict_industry → salary_floor → exclude_keyword → excluded_city。"""
    industries = ["快消"]
    aliases = {"快消": ["食品/饮料/烟酒"]}

    profile = {
        "min_monthly_k": 15.0,
        "exclude_keywords": ["销售"],
        "excluded_cities": ["北京"],
    }

    # 四类全部命中
    job_all = {
        "city": "北京市",
        "title": "大客户销售",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 10.0,
    }
    hints = screen_hints(
        job_version=job_all,
        profile=profile,
        company_industry="食品/饮料/烟酒",
        tags=["快消行业"],
        industries=industries,
        aliases=aliases,
    )
    expected = [
        {"type": "strict_industry", "text": "高风险行业（快消）"},
        {"type": "salary_floor", "text": "低于薪资底线"},
        {"type": "exclude_keyword", "text": "命中不接受条件：销售"},
        {"type": "excluded_city", "text": "不去的城市"},
    ]
    assert hints == expected

    # 命中其中两项：行业 + 城市，顺序依然保证 strict_industry → excluded_city
    job_partial = {
        "city": "北京",
        "title": "后端开发",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 20.0,
    }
    hints_partial = screen_hints(
        job_version=job_partial,
        profile=profile,
        company_industry="食品/饮料/烟酒",
        industries=industries,
        aliases=aliases,
    )
    assert hints_partial == [
        {"type": "strict_industry", "text": "高风险行业（快消）"},
        {"type": "excluded_city", "text": "不去的城市"},
    ]


def test_screen_hints_multiple_hits_same_type():
    """测试同一类提示可有多条（命中多个不接受条件词，按规则顺序输出）。"""
    profile = {
        "exclude_keywords": ["销售", "催收", "外包"],
    }
    job = {
        "city": "深圳",
        "title": "外包催收专员兼销售",
    }
    hints = screen_hints(job, profile)
    assert hints == [
        {"type": "exclude_keyword", "text": "命中不接受条件：销售"},
        {"type": "exclude_keyword", "text": "命中不接受条件：催收"},
        {"type": "exclude_keyword", "text": "命中不接受条件：外包"},
    ]


def test_screen_hints_no_profile():
    """测试无画像时只出重点排查行业提示，其余三类规则不计算、不报错 (FR-001, Edge Cases)。"""
    industries = ["餐饮"]
    aliases = {}

    job_all_hit = {
        "city": "北京",
        "title": "电话销售",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 3.0,
    }
    # profile 为 None 且行业命中 -> 仅出 strict_industry
    hints = screen_hints(
        job_version=job_all_hit,
        profile=None,
        company_industry="餐饮",
        tags=["销售", "电销"],
        industries=industries,
        aliases=aliases,
    )
    assert hints == [{"type": "strict_industry", "text": "高风险行业（餐饮）"}]

    # profile 为 None 且行业未命中 -> 返回空列表
    hints_empty = screen_hints(
        job_version=job_all_hit,
        profile=None,
        company_industry="互联网科技",
        industries=industries,
        aliases=aliases,
    )
    assert hints_empty == []


def test_screen_hints_salary_edge_cases():
    """测试薪资不可见、面议、无法解析不产生 salary_floor (FR-007)。"""
    profile = {"min_monthly_k": 10.0}

    # 1. 薪资不可见 (salary_visible = 0)
    job_invisible = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 0,
        "salary_parse_ok": 0,
        "salary_max_k": None,
    }
    assert screen_hints(job_invisible, profile) == []

    # 2. 面议 / 解析失败 (salary_visible = 1, salary_parse_ok = 0)
    job_negotiable = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 1,
        "salary_parse_ok": 0,
        "salary_max_k": None,
    }
    assert screen_hints(job_negotiable, profile) == []

    # 3. salary_max_k 为 None
    job_none_k = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": None,
    }
    assert screen_hints(job_none_k, profile) == []

    # 4. profile 未设置最低月薪 (min_monthly_k = None)
    profile_no_min = {"min_monthly_k": None}
    job_low = {
        "city": "深圳",
        "title": "开发",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 5.0,
    }
    assert screen_hints(job_low, profile_no_min) == []


def test_screen_hints_tags_hit_exclude_keyword():
    """测试技能标签和岗位标签命中不接受条件 (FR-001, FR-002)。"""
    profile = {"exclude_keywords": ["销售"]}

    # 职位名不含销售，但 tags（岗位标签或技能标签）含销售
    job = {"city": "深圳", "title": "后端开发工程师"}
    tags = ["服务端", "Python", "电话销售经验", "FastAPI"]
    hints = screen_hints(job, profile, tags=tags)
    assert hints == [{"type": "exclude_keyword", "text": "命中不接受条件：销售"}]

    # 职位名与 tags 均含销售，不产生重复提示
    job_both = {"city": "深圳", "title": "销售技术支持"}
    tags_both = ["销售沟通", "售后服务"]
    hints_both = screen_hints(job_both, profile, tags=tags_both)
    assert hints_both == [{"type": "exclude_keyword", "text": "命中不接受条件：销售"}]


def test_screen_hints_consistent_with_screen():
    """测试粗筛提示中后三类规则与 screen() 排除结论完全一致 (FR-002, SC-004)。"""
    profile = {
        "min_monthly_k": 12.0,
        "exclude_keywords": ["客服", "外包"],
        "excluded_cities": ["北京", "上海"],
    }

    test_cases = [
        # (job_version, tags, expect_screen_passed, expected_hint_types)
        (
            {"city": "深圳", "title": "Python开发", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 20.0},
            ["Python", "Django"],
            True,
            [],
        ),
        (
            {"city": "北京", "title": "Python开发", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 20.0},
            ["Python"],
            False,
            ["excluded_city"],
        ),
        (
            {"city": "深圳", "title": "Python开发", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 8.0},
            ["Python"],
            False,
            ["salary_floor"],
        ),
        (
            {"city": "深圳", "title": "技术客服", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 15.0},
            ["服务"],
            False,
            ["exclude_keyword"],
        ),
        (
            {"city": "广州", "title": "开发工程师", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 15.0},
            ["金融外包", "Java"],
            False,
            ["exclude_keyword"],
        ),
        (
            {"city": "上海市", "title": "外包客服", "salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 6.0},
            ["电话沟通"],
            False,
            ["salary_floor", "exclude_keyword", "excluded_city"],
        ),
        # 薪资不可见 / 面议：screen 和 screen_hints 都不排除薪资
        (
            {"city": "深圳", "title": "开发", "salary_visible": 0, "salary_parse_ok": 0, "salary_max_k": None},
            [],
            True,
            [],
        ),
        (
            {"city": "深圳", "title": "开发", "salary_visible": 1, "salary_parse_ok": 0, "salary_max_k": None},
            [],
            True,
            [],
        ),
    ]

    for jv, tags, expect_passed, expect_hint_types in test_cases:
        res = screen(jv, profile, tags=tags)
        assert res.passed is expect_passed

        hints = screen_hints(jv, profile, company_industry=None, tags=tags)
        actual_hint_types = [h["type"] for h in hints]
        # 只要 screen() 未通过，screen_hints 就必须产出对应规则提示
        if not expect_passed:
            assert len(actual_hint_types) > 0
        else:
            assert len(actual_hint_types) == 0

        for ht in expect_hint_types:
            assert ht in actual_hint_types


def test_private_rule_helpers_extracted():
    """测试重构提取的私有辅助函数 _salary_below_floor, _matched_exclude_keywords, _is_excluded_city (004 阶段 2 审核修改)。"""
    from jet.domain.rules import (
        RULES_VERSION,
        _is_excluded_city,
        _matched_exclude_keywords,
        _salary_below_floor,
    )

    assert RULES_VERSION == "r3"

    # 1. _is_excluded_city: 验证城市归一化匹配与排除
    profile_city = {"excluded_cities": ["北京", "上海市"]}
    assert _is_excluded_city({"city": "北京"}, profile_city) is True
    assert _is_excluded_city({"city": "北京市"}, profile_city) is True
    assert _is_excluded_city({"city": "上海"}, profile_city) is True
    assert _is_excluded_city({"city": "上海市"}, profile_city) is True
    assert _is_excluded_city({"city": "深圳"}, profile_city) is False
    assert _is_excluded_city({"city": "广州市"}, profile_city) is False
    assert _is_excluded_city({"city": ""}, profile_city) is False
    assert _is_excluded_city({}, profile_city) is False
    assert _is_excluded_city({"city": "北京"}, {}) is False
    assert _is_excluded_city({"city": "北京"}, {"excluded_cities": []}) is False

    # 2. _salary_below_floor: 验证薪资可见且解析成功时低于最低月薪
    profile_sal = {"min_monthly_k": 20.0}
    # 薪资上限低于底线 -> True
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 18.0}, profile_sal) is True
    # 薪资上限等于或高于底线 -> False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 20.0}, profile_sal) is False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 25.0}, profile_sal) is False
    # 薪资不可见 / 面议 / 解析失败 -> False
    assert _salary_below_floor({"salary_visible": 0, "salary_parse_ok": 1, "salary_max_k": 10.0}, profile_sal) is False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 0, "salary_max_k": 10.0}, profile_sal) is False
    assert _salary_below_floor({"salary_visible": 0, "salary_parse_ok": 0, "salary_max_k": None}, profile_sal) is False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": None}, profile_sal) is False
    # profile 未配置最低月薪 -> False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 10.0}, {}) is False
    assert _salary_below_floor({"salary_visible": 1, "salary_parse_ok": 1, "salary_max_k": 10.0}, {"min_monthly_k": None}) is False

    # 3. _matched_exclude_keywords: 验证职位名称与标签命中关键词列表及元组 (关键词, 来源)
    exclude_kws = ["销售", "催收", "外包"]

    # 职位名称命中
    m1 = _matched_exclude_keywords("电话销售专员", None, exclude_kws)
    assert m1 == [("销售", "title")]
    kw, src = m1[0]
    assert kw == "销售" and src == "title"

    # 标签命中
    m2 = _matched_exclude_keywords("后端架构师", ["驻场", "金融催收"], exclude_kws)
    assert m2 == [("催收", "tags")]

    # 职位名与标签均有命中（按 exclude_keywords 顺序返回，title 优先）
    m3 = _matched_exclude_keywords("外包研发工程师", ["电话销售"], exclude_kws)
    assert m3 == [("销售", "tags"), ("外包", "title")]

    # 同一关键词在 title 和 tags 都出现时，title 优先且不重复产出该关键词
    m4 = _matched_exclude_keywords("电话销售", ["销售能力"], ["销售"])
    assert m4 == [("销售", "title")]

    # 空或无效输入
    assert _matched_exclude_keywords(None, None, exclude_kws) == []
    assert _matched_exclude_keywords("后端开发", None, []) == []
    assert _matched_exclude_keywords("后端开发", None, None) == []


def test_screen_hints_keywords_hit_strict_industry_and_screen_unchanged():
    """测试 screen_hints 在只有关键词命中时产生 strict_industry；且 screen() 输出保持不变 (004 修订)。"""
    industries = read_strict_industries()
    aliases = read_strict_industry_aliases()
    keywords = read_strict_industry_keywords()

    profile = {
        "min_monthly_k": 10.0,
        "exclude_keywords": ["销售"],
        "excluded_cities": ["北京"],
    }

    # 1. 公司行业为空(None)，职位名含"房地产/厂房(中介)" -> 产生 strict_industry
    job_v1 = {
        "city": "广州",
        "title": "房地产/厂房(中介)",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 15.0,
    }
    hints1 = screen_hints(
        job_version=job_v1,
        profile=profile,
        company_industry=None,
        industries=industries,
        aliases=aliases,
        keywords=keywords,
    )
    assert hints1 == [{"type": "strict_industry", "text": "高风险行业（房地产）"}]

    # screen() 规则排除不受重点排查行业影响，此处职位名不含画像不接受词销售、薪资合规、城市合规 -> passed 为 True
    res1 = screen(job_v1, profile)
    assert res1.passed is True
    assert res1.hits == []

    # 2. 公司行业为空("")，职位名不含关键词，但公司名含"置业" -> 产生 strict_industry
    job_v2 = {
        "city": "深圳",
        "title": "后端架构师",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 30.0,
    }
    hints2 = screen_hints(
        job_version=job_v2,
        profile=profile,
        company_industry="",
        company_name="华润置业发展有限公司",
        industries=industries,
        aliases=aliases,
        keywords=keywords,
    )
    assert hints2 == [{"type": "strict_industry", "text": "高风险行业（房地产）"}]
    res2 = screen(job_v2, profile)
    assert res2.passed is True
    assert res2.hits == []


def test_observations_list_api_keywords_hit_strict_industry(
    llm_client: tuple[TestClient, dict[str, str]],
):
    """测试列表接口：公司行业为空、职位名含"中介"的岗位返回"高风险行业（房地产）" (004 修订)。"""
    client, headers = llm_client
    client.put(
        "/v1/strict-industries",
        json={"selected": ["房地产"]},
        headers=headers,
    )

    payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "real_regression_job_001",
                "title": "房地产/厂房(中介)",
                "company_name": "广州伙伴产业服务",
                "company_industry": "",
                "salary_raw": "10-15K",
                "city": "广州",
            }
        ],
    }

    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert "real_regression_job_001" in jobs
    hints = jobs["real_regression_job_001"]["screen_hints"]
    assert {"type": "strict_industry", "text": "高风险行业（房地产）"} in hints
