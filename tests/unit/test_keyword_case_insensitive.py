from jet.domain.industry import match_strict_industry
from jet.domain.rules import RuleResult, _matched_exclude_keywords, screen, screen_hints


def test_matched_exclude_keywords_case_insensitive():
    """不接受关键词大小写不敏感匹配，且返回值中保留关键词原文。"""
    # 1. 'BD' 命中职位名 'bd经理'，返回 ('BD', 'title')
    res_bd = _matched_exclude_keywords("bd经理", tags=None, exclude_keywords=["BD"])
    assert res_bd == [("BD", "title")]

    # 2. 'ToB' 命中职位名 'TOB 销售'，返回 ('ToB', 'title')
    res_tob = _matched_exclude_keywords("TOB 销售", tags=None, exclude_keywords=["ToB"])
    assert res_tob == [("ToB", "title")]

    # 3. 'saas' 命中标签 'SaaS'，返回 ('saas', 'tags')
    res_saas = _matched_exclude_keywords("软件产品经理", tags=["SaaS", "企业服务"], exclude_keywords=["saas"])
    assert res_saas == [("saas", "tags")]

    # 4. 中文关键词行为不变
    res_zh_title = _matched_exclude_keywords("资深外包工程师", tags=None, exclude_keywords=["外包"])
    assert res_zh_title == [("外包", "title")]

    res_zh_tag = _matched_exclude_keywords("开发工程师", tags=["人力外包"], exclude_keywords=["外包"])
    assert res_zh_tag == [("外包", "tags")]

    res_zh_none = _matched_exclude_keywords("核心自研系统", tags=["自研"], exclude_keywords=["外包"])
    assert res_zh_none == []


def test_match_strict_industry_case_insensitive():
    """从严行业匹配支持大小写不敏感，且返回规则中定义的行业名原文。"""
    industries = ["汽车", "IT服务", "企业云原生"]
    aliases = {
        "企业云原生": ["SaaS平台", "PaaS中间件"],
    }
    keywords = {
        "汽车": ["4S", "二手车"],
    }

    # 1. 关键词 '4S' 命中职位名 '4s店销售顾问'，返回行业名原文 '汽车'
    res_title_kw = match_strict_industry(
        company_industry=None,
        industries=industries,
        keywords=keywords,
        title="4s店销售顾问",
    )
    assert res_title_kw == "汽车"

    # 2. 公司名大小写不同也命中：'4S' 命中公司名 '某某4s店汽车销售公司'
    res_company_kw = match_strict_industry(
        company_industry=None,
        industries=industries,
        keywords=keywords,
        company_name="某某4s店汽车销售公司",
    )
    assert res_company_kw == "汽车"

    # 反向：关键词小写 '4s' 命中公司名大写 '4S'
    res_company_kw2 = match_strict_industry(
        company_industry=None,
        industries=["汽车"],
        keywords={"汽车": ["4s"]},
        company_name="某某4S店汽车销售公司",
    )
    assert res_company_kw2 == "汽车"

    # 3. 公司行业与行业名自身大小写不同也命中：'it服务' 命中 'IT服务'
    res_industry_name = match_strict_industry(
        company_industry="it服务",
        industries=industries,
    )
    assert res_industry_name == "IT服务"

    # 4. 公司行业与行业同义名大小写不同也命中：'saas平台开发' 命中同义词 'SaaS平台'
    res_alias = match_strict_industry(
        company_industry="saas平台开发",
        industries=industries,
        aliases=aliases,
    )
    assert res_alias == "企业云原生"


def test_screen_rules_and_hints_case_insensitive():
    """经由 rules.py 的公开函数验证大小写不同时岗位被规则排除或产生提示。"""
    # 1. screen(): 职位名命中不接受关键词 'BD'（小写 'bd'）
    job_bd = {"title": "高级bd拓展经理", "city": "深圳", "salary_visible": 0}
    profile_bd = {"exclude_keywords": ["BD"]}
    res_bd: RuleResult = screen(job_bd, profile_bd)
    assert res_bd.passed is False
    assert any("职位名称命中不接受关键词：BD" in h for h in res_bd.hits)

    # 2. screen(): 标签命中不接受关键词 'saas'（大写标签 'SaaS'）
    job_saas = {"title": "售前顾问", "city": "深圳", "salary_visible": 0}
    profile_saas = {"exclude_keywords": ["saas"]}
    res_saas: RuleResult = screen(job_saas, profile_saas, tags=["SaaS"])
    assert res_saas.passed is False
    assert any("岗位标签命中不接受关键词：saas" in h for h in res_saas.hits)

    # 3. screen_hints(): 提示词包含不接受条件原文（大小写转换后匹配）
    hints_tob = screen_hints(
        job_version={"title": "TOB 业务线主管"},
        profile={"exclude_keywords": ["ToB"]},
    )
    assert any(h["type"] == "exclude_keyword" and h["text"] == "命中不接受条件：ToB" for h in hints_tob)

    # 4. screen_hints(): 从严行业通过职位名关键词不区分大小写命中
    hints_4s = screen_hints(
        job_version={"title": "4s店销售顾问"},
        profile=None,
        industries=["汽车"],
        keywords={"汽车": ["4S"]},
    )
    assert any(h["type"] == "strict_industry" and "汽车" in h["text"] for h in hints_4s)

    # 5. screen_hints(): 从严行业通过公司名关键词不区分大小写命中
    hints_company_4s = screen_hints(
        job_version={"title": "服务专员"},
        profile=None,
        company_name="豪华汽车4s店",
        industries=["汽车"],
        keywords={"汽车": ["4S"]},
    )
    assert any(h["type"] == "strict_industry" and "汽车" in h["text"] for h in hints_company_4s)

    # 6. screen_hints(): 从严行业通过公司行业同义词不区分大小写命中
    hints_alias = screen_hints(
        job_version={"title": "软件架构师"},
        profile=None,
        company_industry="企业级saas软件",
        industries=["云计算"],
        aliases={"云计算": ["SaaS软件"]},
    )
    assert any(h["type"] == "strict_industry" and "云计算" in h["text"] for h in hints_alias)
