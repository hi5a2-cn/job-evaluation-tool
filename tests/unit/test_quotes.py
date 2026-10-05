from jet.domain.quotes import annotate_facts, check_quote, count_quotes


def test_check_quote_exact_and_variations():
    desc = "本岗位负责招商银⾏核心系统的研发与运维，要求精通 Python 3.12，每周工作 5 天。"

    # 1. Exact substring
    assert check_quote("精通 Python 3.12", desc) is True

    # 2. Kangxi radical difference ("银行" vs "银⾏")
    assert check_quote("招商银行核心系统", desc) is True

    # 3. Full-width vs half-width numbers
    assert check_quote("Python ３.１２", desc) is True

    # 4. Whitespace differences
    assert check_quote("每周  工作 5 天", desc) is True
    assert check_quote("每周工作5天", desc) is True

    # 5. Paraphrased / rewritten text -> False
    assert check_quote("熟练使用 Python", desc) is False
    assert check_quote("负责银行开发", desc) is False

    # 6. Empty or None quote or description -> False
    assert check_quote("", desc) is False
    assert check_quote("   ", desc) is False
    assert check_quote(None, desc) is False
    assert check_quote("Python", "") is False
    assert check_quote("Python", None) is False


def test_annotate_facts_and_count_quotes():
    desc = "岗位职责：\n1. 负责数据仓库建设与 ETL 研发；\n2. 具备 3 年以上数据开发经验；\n3. 偶尔需要配合商务部门进行技术交流。"

    facts = {
        "summary": {
            "text": "数仓建设",
            "quotes": ["负责数据仓库建设与 ETL 研发", "编造的原句找不到"],
        },
        "work_type": {
            "value": "数据",
            "quotes": ["ETL 研发"],
        },
        "sales_level": {
            "value": "低",
            "signals": [
                {"signal": "商务拓展", "quote": "配合商务部门进行技术交流"},
                {"signal": "业绩指标", "quote": "未在文中出现的指标要求"},
            ],
        },
        "experience": {
            "requirement": "具备 3 年以上数据开发经验",
            "value": "满足",
            "gap": "",
        },
        "overtime": {
            "value": "未提及",
            "quotes": ["完全不存在的加班原句"],
        },
    }

    annotated = annotate_facts(facts, desc)

    # Check summary quotes
    assert annotated["summary"]["quotes"][0] == {"text": "负责数据仓库建设与 ETL 研发", "found": True}
    assert annotated["summary"]["quotes"][1] == {"text": "编造的原句找不到", "found": False}

    # Check work_type quotes
    assert annotated["work_type"]["quotes"][0] == {"text": "ETL 研发", "found": True}

    # Check sales_level signals
    assert annotated["sales_level"]["signals"][0]["quote"] == {"text": "配合商务部门进行技术交流", "found": True}
    assert annotated["sales_level"]["signals"][1]["quote"] == {"text": "未在文中出现的指标要求", "found": False}

    # Check experience requirement
    assert annotated["experience"]["requirement"] == {"text": "具备 3 年以上数据开发经验", "found": True}

    # Check overtime quotes
    assert annotated["overtime"]["quotes"][0] == {"text": "完全不存在的加班原句", "found": False}

    # Check count_quotes
    # Total quotes: 2 (summary) + 1 (work_type) + 2 (sales_level) + 1 (experience) + 1 (overtime) = 7
    # Missing quotes: 1 (summary) + 0 + 1 (sales_level) + 0 + 1 (overtime) = 3
    total, missing = count_quotes(annotated)
    assert total == 7
    assert missing == 3


def test_annotate_facts_with_empty_and_null_fields():
    desc = "简单的岗位介绍"
    facts = {
        "summary": {"text": "概括", "quotes": []},
        "work_type": {"value": "其他", "quotes": []},
        "sales_level": {"value": "低", "signals": []},
        "experience": {"requirement": None, "value": "无法判断", "gap": ""},
        "overtime": {"value": "未提及", "quotes": []},
    }
    annotated = annotate_facts(facts, desc)
    assert annotated["experience"]["requirement"] is None
    total, missing = count_quotes(annotated)
    assert total == 0
    assert missing == 0


def test_annotate_facts_with_work_intensity():
    desc = "岗位职责：双休不加班，早九晚六。"
    facts = {
        "summary": {"text": "概括", "quotes": []},
        "work_type": {"value": "其他", "quotes": []},
        "sales_level": {"value": "低", "signals": []},
        "experience": {"requirement": None, "value": "满足", "gap": ""},
        "work_intensity": {"value": "双休", "quotes": ["双休不加班", "不存在的语句"]},
    }
    annotated = annotate_facts(facts, desc)
    assert annotated["work_intensity"]["quotes"][0] == {"text": "双休不加班", "found": True}
    assert annotated["work_intensity"]["quotes"][1] == {"text": "不存在的语句", "found": False}
    total, missing = count_quotes(annotated)
    assert total == 2
    assert missing == 1


def test_annotate_facts_with_risk_signals():
    desc = "岗位薪资：每天600手为标准，年龄要求18-26岁。"
    facts = {
        "risk_signals": [
            {"type": "非法金融", "description": "按手数提成", "quote": "每天600手为标准"},
            {"type": "其他", "description": "年龄门槛过窄", "quote": "年龄要求18-26"},
            {"type": "诈骗", "description": "虚假信息", "quote": "入职缴纳保证金5000元"},
        ]
    }
    annotated = annotate_facts(facts, desc)
    signals = annotated["risk_signals"]
    assert len(signals) == 3
    assert signals[0]["quote"] == {"text": "每天600手为标准", "found": True}
    assert signals[1]["quote"] == {"text": "年龄要求18-26", "found": True}
    assert signals[2]["quote"] == {"text": "入职缴纳保证金5000元", "found": False}

    total, missing = count_quotes(annotated)
    assert total == 3
    assert missing == 1
