from jet.domain.rules import screen


def test_city_rule_matching():
    profile = {
        "preferred_cities": ["深圳", "上海"],
        "cities": ["深圳", "上海"],
        "excluded_cities": ["北京"],
        "min_monthly_k": None,
        "exclude_keywords": [],
    }

    # 偏好城市以外的城市（广州）不再被排除
    job_gz = {"city": "广州", "title": "开发", "description": "", "salary_visible": 0}
    res_gz = screen(job_gz, profile)
    assert res_gz.passed is True
    assert len(res_gz.hits) == 0

    # 偏好城市（深圳市）正常通过
    job_sz = {"city": "深圳市", "title": "开发", "description": "", "salary_visible": 0}
    res_sz = screen(job_sz, profile)
    assert res_sz.passed is True
    assert len(res_sz.hits) == 0

    # 不去的城市被排除（"北京" 与 "北京市" 规范化后都命中）
    job_bj1 = {"city": "北京", "title": "开发", "description": "", "salary_visible": 0}
    res_bj1 = screen(job_bj1, profile)
    assert res_bj1.passed is False
    assert any("城市在不去的城市中：北京" in h for h in res_bj1.hits)

    job_bj2 = {"city": "北京市", "title": "开发", "description": "", "salary_visible": 0}
    res_bj2 = screen(job_bj2, profile)
    assert res_bj2.passed is False
    assert any("城市在不去的城市中：北京市" in h for h in res_bj2.hits)

    # 不去的城市配"上海"，"上海"与"上海市"都命中排除
    profile_ex_sh = {
        "preferred_cities": ["深圳"],
        "excluded_cities": ["上海"],
    }
    job_sh1 = {"city": "上海", "title": "开发", "description": "", "salary_visible": 0}
    res_sh1 = screen(job_sh1, profile_ex_sh)
    assert res_sh1.passed is False
    assert any("城市在不去的城市中：上海" in h for h in res_sh1.hits)

    job_sh2 = {"city": "上海市", "title": "开发", "description": "", "salary_visible": 0}
    res_sh2 = screen(job_sh2, profile_ex_sh)
    assert res_sh2.passed is False
    assert any("城市在不去的城市中：上海市" in h for h in res_sh2.hits)


def test_salary_rule_matching():
    profile = {
        "cities": ["深圳"],
        "min_monthly_k": 20.0,
        "exclude_keywords": [],
    }

    # Max salary 18K < min 20K -> excluded
    job_low = {
        "city": "深圳",
        "title": "开发",
        "description": "",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 18.0,
    }
    res_low = screen(job_low, profile)
    assert res_low.passed is False
    assert any("薪资上限 18K 低于底线 20K" in h for h in res_low.hits)

    # Max salary 25K >= min 20K -> passed
    job_ok = {
        "city": "深圳",
        "title": "开发",
        "description": "",
        "salary_visible": 1,
        "salary_parse_ok": 1,
        "salary_max_k": 25.0,
    }
    assert screen(job_ok, profile).passed is True

    # Salary invisible -> rule skipped
    job_invisible = {
        "city": "深圳",
        "title": "开发",
        "description": "",
        "salary_visible": 0,
        "salary_parse_ok": 0,
        "salary_max_k": None,
    }
    assert screen(job_invisible, profile).passed is True

    # Salary parse failed -> rule skipped
    job_unparsed = {
        "city": "深圳",
        "title": "开发",
        "description": "",
        "salary_visible": 1,
        "salary_parse_ok": 0,
        "salary_max_k": None,
    }
    assert screen(job_unparsed, profile).passed is True


from pathlib import Path


def test_exclude_keywords_rule_matching():
    profile = {
        "cities": ["深圳"],
        "min_monthly_k": None,
        "exclude_keywords": ["外包", "驻场", "银行"],
    }

    # Title matches exclude keyword
    job_title_hit = {
        "city": "深圳",
        "title": "Python外包开发",
        "description": "无特殊说明",
        "salary_visible": 0,
    }
    res_t = screen(job_title_hit, profile)
    assert res_t.passed is False
    assert any("职位名称命中不接受关键词：外包" in h for h in res_t.hits)

    # Title matches exclude keyword with NFKC normalization (银⾏ -> 银行)
    job_nfkc_hit = {
        "city": "深圳",
        "title": "银\u2f8f核心开发",
        "description": "核心业务研发",
        "salary_visible": 0,
    }
    res_nfkc = screen(job_nfkc_hit, profile)
    assert res_nfkc.passed is False
    assert any("职位名称命中不接受关键词：银行" in h for h in res_nfkc.hits)

    # Description contains exclude keyword ("不涉及电商、销售、客服"), but title is "互联网金融" -> NOT excluded
    jd_path = Path(__file__).resolve().parent.parent / "fixtures" / "boss" / "risk_finance_jd.txt"
    desc = jd_path.read_text(encoding="utf-8")
    profile_sales = {
        "cities": ["深圳"],
        "min_monthly_k": None,
        "exclude_keywords": ["销售"],
    }
    job_finance = {
        "city": "深圳",
        "title": "互联网金融",
        "description": desc,
        "salary_visible": 0,
    }
    res_finance = screen(job_finance, profile_sales)
    assert res_finance.passed is True
    assert len(res_finance.hits) == 0

    # Title is "销售代表" -> excluded
    job_sales_rep = {
        "city": "深圳",
        "title": "销售代表",
        "description": desc,
        "salary_visible": 0,
    }
    res_sales = screen(job_sales_rep, profile_sales)
    assert res_sales.passed is False
    assert any("职位名称命中不接受关键词：销售" in h for h in res_sales.hits)

    # Optional tags parameter matches exclude keyword
    job_tags_hit = {
        "city": "深圳",
        "title": "开发工程师",
        "description": "自研系统",
        "salary_visible": 0,
    }
    res_tags = screen(job_tags_hit, profile_sales, tags=["金融", "电话销售"])
    assert res_tags.passed is False
    assert any("岗位标签命中不接受关键词：销售" in h for h in res_tags.hits)

    # No exclude keywords hit
    job_clean = {
        "city": "深圳",
        "title": "自研产品后端",
        "description": "自研SaaS系统研发",
        "salary_visible": 0,
    }
    assert screen(job_clean, profile).passed is True


def test_rules_version():
    from jet.domain.rules import RULES_VERSION

    assert RULES_VERSION == "r3"


def test_rule_judgement_engine_and_method_changed(tmp_path):
    from jet.db.store import open_db, utc_now
    from jet.domain.judgements import is_method_changed, request_judgement, to_api
    from jet.domain.profiles import save_profile
    from jet.llm.versions import DEFAULT_PROMPT_VERSION

    conn = open_db(tmp_path / "jet-data")
    now = utc_now()
    save_profile(
        conn,
        "me",
        {
            "directions": ["Python开发"],
            "preferred_cities": ["深圳"],
            "excluded_cities": ["北京"],
        },
    )

    # Insert job in 北京 -> screened out by rule
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_bj', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, "
        "city, description, content_hash, created_at) "
        "VALUES (10, 1, 1, 'detail', 'Python开发', 1, 1, '北京', '描述', 'h1', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 10 WHERE id = 1")
    job_row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()

    j_row, is_queued, _notice = request_judgement(
        conn,
        user_id="me",
        job_row=job_row,
        prompt_version=DEFAULT_PROMPT_VERSION,
    )
    assert is_queued is False
    assert j_row["source"] == "rule"
    assert j_row["engine"] == "rules:r3"

    api_data = to_api(conn, j_row)
    assert api_data["stale"]["method_changed"] is False

    # Old r2 rule judgement -> stale.method_changed is True
    conn.execute("UPDATE judgements SET engine = 'rules:r2' WHERE id = ?", (j_row["id"],))
    j_row_r2 = conn.execute("SELECT * FROM judgements WHERE id = ?", (j_row["id"],)).fetchone()
    assert is_method_changed(j_row_r2) is True
    api_data_r2 = to_api(conn, j_row_r2)
    assert api_data_r2["stale"]["method_changed"] is True
    conn.close()
