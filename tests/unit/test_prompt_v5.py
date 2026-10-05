import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.domain.industry import save_user_strict_industries
from jet.domain.judgements import is_method_changed, staleness, to_api
from jet.llm.prompt_v5 import (
    DEFAULT_STRICT_INDUSTRY_RULES_PATH,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    apply_city_salary_cap,
    build_messages,
    build_strict_industry_instruction,
    parse_facts,
    read_strict_industries,
    read_strict_industry_aliases,
    read_strict_industry_keywords,
)
from jet.worker import JudgementWorker


def test_prompt_version():
    assert PROMPT_VERSION == "v5"


def test_system_prompt_contains_city_salary_instruction():
    assert "偏好城市只是偏好" in SYSTEM_PROMPT
    assert "不在偏好城市不等于不适合" in SYSTEM_PROMPT
    assert "非偏好城市的最低月薪" in SYSTEM_PROMPT
    assert "不去的城市" in SYSTEM_PROMPT


def test_build_messages_lists_new_fields():
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳", "上海"],
        "excluded_cities": ["北京"],
        "min_monthly_k": 15.0,
        "nonpref_min_monthly_k": 20.0,
        "exclude_keywords": ["外包"],
        "work_preference": "不愿做销售",
        "background": "3年开发经验",
    }
    job_version = {
        "title": "后端开发",
        "salary_raw": "18-25K",
        "salary_visible": True,
        "city": "深圳",
        "district": "南山区",
        "description": "系统开发，双休",
    }

    msgs = build_messages(profile, job_version)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    user_content = msgs[1]["content"]

    assert "偏好城市（为空表示城市都可以）：深圳、上海" in user_content
    assert "不去的城市：北京" in user_content
    assert "最低月薪：15.0K" in user_content
    assert "非偏好城市的最低月薪：20.0K" in user_content

    # Empty preferred_cities and None nonpref_min_monthly_k
    empty_profile = {
        "directions": ["前端开发"],
        "preferred_cities": [],
        "excluded_cities": [],
        "min_monthly_k": None,
        "nonpref_min_monthly_k": None,
    }
    msgs_empty = build_messages(empty_profile, job_version)
    user_empty = msgs_empty[1]["content"]
    assert "偏好城市（为空表示城市都可以）：无" in user_empty
    assert "不去的城市：无" in user_empty
    assert "最低月薪：不限" in user_empty
    assert "非偏好城市的最低月薪：不限" in user_empty


def test_apply_city_salary_cap_in_preferred_city():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "深圳",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]

    # Normalization check: "深圳市" matches "深圳"
    job_norm = dict(job, city="深圳市")
    v_norm, d_norm = apply_city_salary_cap("apply", ["职责匹配"], profile, job_norm)
    assert v_norm == "apply"
    assert d_norm == ["职责匹配"]


def test_apply_city_salary_cap_preferred_cities_empty():
    profile = {
        "preferred_cities": [],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]


def test_apply_city_salary_cap_salary_invisible():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": False,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]


def test_apply_city_salary_cap_salary_not_parse_ok():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": False,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]


def test_apply_city_salary_cap_no_nonpref_min_monthly_k():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": None,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]


def test_apply_city_salary_cap_salary_max_ge_nonpref():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 20.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["职责匹配"], profile, job)
    assert verdict == "apply"
    assert derivation == ["职责匹配"]


def test_apply_city_salary_cap_verdict_already_check_or_skip():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    v_check, d_check = apply_city_salary_cap("check", ["需要确认业务"], profile, job)
    assert v_check == "check"
    assert d_check == ["需要确认业务"]

    v_skip, d_skip = apply_city_salary_cap("skip", ["明确冲突"], profile, job)
    assert v_skip == "skip"
    assert d_skip == ["明确冲突"]


def test_apply_city_salary_cap_triggers_apply_to_check():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 15.0,
    }
    verdict, derivation = apply_city_salary_cap("apply", ["各项条件匹配"], profile, job)
    assert verdict == "check"
    assert len(derivation) == 2
    expected_note = "岗位不在偏好城市且薪资上限 15K 低于非偏好城市最低月薪 20K，结论最多需要确认"
    assert derivation[0] == expected_note
    assert derivation[1] == "各项条件匹配"


def test_apply_city_salary_cap_triggers_try_to_check_and_truncates_to_five():
    profile = {
        "preferred_cities": ["深圳"],
        "nonpref_min_monthly_k": 20.0,
    }
    job = {
        "city": "广州",
        "salary_visible": True,
        "salary_parse_ok": True,
        "salary_max_k": 18.5,
    }
    existing_derivation = ["理由1", "理由2", "理由3", "理由4", "理由5"]
    verdict, derivation = apply_city_salary_cap("try", existing_derivation, profile, job)
    assert verdict == "check"
    assert len(derivation) == 5
    expected_note = "岗位不在偏好城市且薪资上限 18.5K 低于非偏好城市最低月薪 20K，结论最多需要确认"
    assert derivation[0] == expected_note
    assert derivation[1:] == ["理由1", "理由2", "理由3", "理由4"]


def test_read_strict_industries_scenarios(tmp_path: Path):
    """测试 read_strict_industries 正常/文件不存在/坏 JSON/industries 不是列表/去空与去重。"""
    # 1. 正常读取
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps({"industries": ["餐饮", "快消"]}), encoding="utf-8")
    assert read_strict_industries(valid_file) == ["餐饮", "快消"]

    # 2. 文件不存在
    missing_file = tmp_path / "nonexistent.json"
    assert read_strict_industries(missing_file) == []

    # 3. 坏 JSON
    bad_json_file = tmp_path / "bad.json"
    bad_json_file.write_text("{industries: [unquoted]}", encoding="utf-8")
    assert read_strict_industries(bad_json_file) == []

    # 4. industries 不是列表（字符串、数字、对象等）
    not_list_str = tmp_path / "not_list_str.json"
    not_list_str.write_text(json.dumps({"industries": "餐饮"}), encoding="utf-8")
    assert read_strict_industries(not_list_str) == []

    not_list_num = tmp_path / "not_list_num.json"
    not_list_num.write_text(json.dumps({"industries": 123}), encoding="utf-8")
    assert read_strict_industries(not_list_num) == []

    not_dict = tmp_path / "not_dict.json"
    not_dict.write_text(json.dumps(["餐饮", "快消"]), encoding="utf-8")
    assert read_strict_industries(not_dict) == []

    # 5. 去空、去空白字符、去重保序、过滤非字符串项
    dirty_file = tmp_path / "dirty.json"
    dirty_file.write_text(
        json.dumps({
            "industries": [
                "  餐饮  ",
                "",
                "   ",
                "快消",
                "餐饮",
                " 汽车 ",
                None,
                456,
                "快消",
                "美妆",
            ]
        }),
        encoding="utf-8",
    )
    assert read_strict_industries(dirty_file) == ["餐饮", "快消", "汽车", "美妆"]


def test_build_strict_industry_instruction():
    """测试 build_strict_industry_instruction 空列表返回空串、非空包含每个行业与从严判断文案。"""
    # 空列表与 None 返回空字符串
    assert build_strict_industry_instruction([]) == ""
    assert build_strict_industry_instruction(None) == ""

    # 非空列表
    industries = ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"]
    instruction = build_strict_industry_instruction(industries)
    for ind in industries:
        assert ind in instruction
    assert "重点排查行业（餐饮、保险、汽车、房地产、美妆、快消）：" in instruction
    assert '按"含销售"处理' in instruction
    assert 'sales_level 至少为"中"' in instruction
    assert "属于重点排查行业（某行业），职责描述模糊，按含销售处理" in instruction
    assert "必须包含一个确认销售成分的问题" in instruction
    assert "这些行业不直接排除" in instruction


def test_build_messages_strict_industries_parameter():
    """测试 build_messages 传 strict_industries 包含说明、传 [] 等于 SYSTEM_PROMPT。"""
    profile = {"directions": ["Python"]}
    job_version = {"title": "后端工程师", "city": "深圳", "description": "研发"}

    # 传入特定行业列表
    msgs_custom = build_messages(profile, job_version, strict_industries=["餐饮", "保险"])
    expected_instruction = build_strict_industry_instruction(["餐饮", "保险"])
    assert msgs_custom[0]["role"] == "system"
    assert msgs_custom[0]["content"].startswith(SYSTEM_PROMPT)
    assert expected_instruction in msgs_custom[0]["content"]
    assert msgs_custom[0]["content"] == SYSTEM_PROMPT + "\n" + expected_instruction

    # 传入空列表：系统消息必须严格等于 SYSTEM_PROMPT
    msgs_empty = build_messages(profile, job_version, strict_industries=[])
    assert msgs_empty[0]["content"] == SYSTEM_PROMPT


def test_build_messages_company_name():
    """测试【岗位信息】中公司名称行（有值/去除首尾空格/空值或未提供写'未提供'）。"""
    profile = {"directions": ["Python"]}

    # 有公司名
    job_with_co = {
        "title": "后端开发",
        "company_name": "腾讯科技",
        "city": "深圳",
        "description": "开发",
    }
    msgs1 = build_messages(profile, job_with_co)
    assert "- 职位名称：后端开发\n- 公司名称：腾讯科技\n" in msgs1[1]["content"]

    # 有公司名且有前后空格：应 trim
    job_with_space = {
        "title": "后端开发",
        "company_name": "  阿里巴巴集团  ",
        "city": "杭州",
        "description": "开发",
    }
    msgs2 = build_messages(profile, job_with_space)
    assert "- 职位名称：后端开发\n- 公司名称：阿里巴巴集团\n" in msgs2[1]["content"]

    # 公司名为空字符串
    job_empty_str = {
        "title": "后端开发",
        "company_name": "",
        "city": "深圳",
        "description": "开发",
    }
    msgs3 = build_messages(profile, job_empty_str)
    assert "- 职位名称：后端开发\n- 公司名称：未提供\n" in msgs3[1]["content"]

    # 公司名为纯空格
    job_whitespace = {
        "title": "后端开发",
        "company_name": "    ",
        "city": "深圳",
        "description": "开发",
    }
    msgs4 = build_messages(profile, job_whitespace)
    assert "- 职位名称：后端开发\n- 公司名称：未提供\n" in msgs4[1]["content"]

    # 公司名为 None
    job_none = {
        "title": "后端开发",
        "company_name": None,
        "city": "深圳",
        "description": "开发",
    }
    msgs5 = build_messages(profile, job_none)
    assert "- 职位名称：后端开发\n- 公司名称：未提供\n" in msgs5[1]["content"]

    # 未包含 company_name 键
    job_missing = {
        "title": "后端开发",
        "city": "深圳",
        "description": "开发",
    }
    msgs6 = build_messages(profile, job_missing)
    assert "- 职位名称：后端开发\n- 公司名称：未提供\n" in msgs6[1]["content"]


def test_build_messages_default_reads_repo_rules_file():
    """测试 build_messages 默认读取仓库里的真实规则文件，说明里包含'餐饮'和'快消'及关键词说明。"""
    profile = {"directions": ["Python"]}
    job_version = {"title": "后端开发", "city": "深圳", "description": "系统开发"}
    msgs = build_messages(profile, job_version)

    sys_content = msgs[0]["content"]
    assert "餐饮" in sys_content
    assert "快消" in sys_content
    assert "13. 重点排查行业" in sys_content

    real_industries = read_strict_industries()
    real_aliases = read_strict_industry_aliases()
    real_keywords = read_strict_industry_keywords()
    assert "餐饮" in real_industries
    assert "快消" in real_industries
    expected_instruction = build_strict_industry_instruction(
        real_industries, aliases=real_aliases, keywords=real_keywords
    )
    assert sys_content == SYSTEM_PROMPT + "\n" + expected_instruction


def test_worker_passes_company_name_to_llm(data_dir: Path, settings: Settings):
    """测试 JudgementWorker 在判断时传给大模型的用户消息中包含 jobs.company_name。"""
    conn = open_db(data_dir)
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
        "VALUES (99, 'boss', 'job_worker_company_test', 'full', '字节跳动科技有限公司', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (990, 99, 1, 'detail', '高并发工程师', '30-50K', 1, 30.0, 50.0, 12, 1, '北京', '负责分布式系统', 'hash_w_test', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 990 WHERE id = 99")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (9, 'me', 1, '[\"Python\"]', '[\"高并发\"]', '[\"北京\"]', 25.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (9999, 'me', 99, 990, 9, 'queued', '{}', ?)",
        (now_str,),
    )
    conn.close()

    captured_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_requests.append(body)
        content = json.dumps(
            {
                "facts": {
                    "summary": {"text": "分布式系统", "quotes": ["负责分布式系统"]},
                    "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": ["负责分布式系统"]},
                    "sales_level": {"value": "低", "signals": []},
                    "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                    "work_intensity": {"value": "双休", "quotes": []},
                    "risk_signals": [],
                },
                "verdict": "apply",
                "derivation": ["技术方向匹配"],
                "verdict_reason": "适合投递",
                "hr_questions": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    transport = httpx.MockTransport(handler)
    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        review_enabled=False,
    )
    worker = JudgementWorker(worker_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(9999)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    assert len(captured_requests) == 1
    messages = captured_requests[0]["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "- 职位名称：高并发工程师" in user_msg
    assert "- 公司名称：字节跳动科技有限公司" in user_msg


def test_strict_industry_change_does_not_make_judgement_stale(data_dir: Path):
    """测试已有判断在重点排查行业改动后，staleness 三项仍为 False（不触发过时）。"""
    conn = open_db(data_dir)
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (88, 'boss', 'job_v5_staleness_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (880, 88, 1, 'detail', 'Python工程师', 1, 1, '深圳', 'hash_v5_stale', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 880 WHERE id = 88")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (8, 'me', 1, '[\"Python\"]', '[]', '[\"深圳\"]', 15.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (8888, 'me', 88, 880, 8, 'done', 'apply', 'llm', 'v9', 'deepseek-flash:no-think', '{}', ?, ?)",
        (now_str, now_str),
    )

    # 用领域层保存当前用户的从严行业勾选
    save_user_strict_industries(conn, "me", ["餐饮", "快消"])

    j_row = conn.execute("SELECT * FROM judgements WHERE id = 8888").fetchone()
    # staleness 检查
    stale_dict = staleness(conn, j_row)
    assert stale_dict["method_changed"] is False
    assert stale_dict["job_changed"] is False
    assert stale_dict["profile_changed"] is False

    # is_method_changed 检查
    assert is_method_changed(j_row) is False

    # to_api 返回结构中的 stale 检查
    api_res = to_api(conn, j_row)
    assert api_res["stale"]["method_changed"] is False
    assert api_res["stale"]["job_changed"] is False
    assert api_res["stale"]["profile_changed"] is False

    conn.close()


def test_read_strict_industry_aliases_from_repo_file():
    aliases = read_strict_industry_aliases()
    assert isinstance(aliases, dict)
    assert "餐饮" in aliases
    assert aliases["餐饮"] == ["餐饮"]
    assert "快消" in aliases
    assert "快速消费品" in aliases["快消"]
    assert "汽车" in aliases
    assert "新能源汽车" in aliases["汽车"]
    assert "房地产" in aliases
    assert "美妆" in aliases
    assert "保险" in aliases


def test_read_strict_industry_aliases_missing_or_corrupt(tmp_path: Path):
    # 1. Non-existent file
    missing = tmp_path / "nonexistent.json"
    assert read_strict_industry_aliases(missing) == {}

    # 2. Corrupt JSON
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{invalid json", encoding="utf-8")
    assert read_strict_industry_aliases(corrupt) == {}

    # 3. Not a dict
    not_dict = tmp_path / "not_dict.json"
    not_dict.write_text("[]", encoding="utf-8")
    assert read_strict_industry_aliases(not_dict) == {}

    # 4. Filter only industries present in rules file industries list; clean whitespace, duplicates, empty
    custom_rules = tmp_path / "custom_rules.json"
    custom_rules.write_text(
        json.dumps(
            {
                "industries": ["汽车", "快消"],
                "aliases": {
                    "汽车": [" 汽车 ", "新能源汽车", "汽车", "", "   "],
                    "快消": ["日化", 123, None, "食品"],
                    "未知行业": ["其他"],  # 不在 industries 中，应被丢弃
                    "无效值": "不是列表",  # 非列表，应被丢弃
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    parsed = read_strict_industry_aliases(custom_rules)
    assert "未知行业" not in parsed
    assert "无效值" not in parsed
    assert parsed["汽车"] == ["汽车", "新能源汽车"]
    assert parsed["快消"] == ["日化", "食品"]


def test_build_strict_industry_instruction_single_arg_unchanged():
    # 保持只传一个参数时的现有输出完全不变
    industries = ["餐饮", "快消"]
    res = build_strict_industry_instruction(industries)
    assert "重点排查行业（餐饮、快消）：" in res
    assert "- 先根据公司名称、职位名称和职位描述判断公司或岗位是否属于上述行业之一。" in res
    assert '优先依据"公司行业"' not in res


def test_build_strict_industry_instruction_with_aliases():
    industries = ["餐饮", "快消", "汽车"]
    aliases = {
        "餐饮": ["餐饮"],  # 同义词与行业名相同，不附加别名标注
        "快消": ["快速消费品", "日化"],  # 附加别名标注
        "汽车": ["新能源汽车"],  # 附加别名标注
    }
    res = build_strict_industry_instruction(industries, aliases=aliases)
    # 第一行行业列表标注同义名
    assert "快消（BOSS 行业名如：快速消费品、日化）" in res
    assert "汽车（BOSS 行业名如：新能源汽车）" in res
    assert "餐饮（" not in res  # 只有自身时无额外别名标注
    # 第二个要点文字
    new_bullet = (
        '- 优先依据"公司行业"判断公司是否属于上述行业（公司行业与行业名或其同义名相同或包含即算属于）；'
        '公司行业为"未提供"时，再根据公司名称、职位名称和职位描述判断。'
    )
    assert new_bullet in res
    assert "- 先根据公司名称、职位名称和职位描述判断公司是否属于上述行业。" not in res

    # 传空字典 aliases={} 时同样使用新文字，第一行无别名标注
    res_empty = build_strict_industry_instruction(industries, aliases={})
    assert new_bullet in res_empty
    assert "重点排查行业（餐饮、快消、汽车）：" in res_empty


def test_build_messages_company_industry():
    profile = {"directions": ["Python"]}

    # 有公司行业
    job_with_ind = {
        "title": "后端开发",
        "company_name": "蔚来汽车",
        "company_industry": "新能源汽车",
        "city": "上海",
        "description": "车机系统开发",
    }
    msgs1 = build_messages(profile, job_with_ind)
    user_content1 = msgs1[1]["content"]
    assert "- 职位名称：后端开发\n- 公司名称：蔚来汽车\n- 公司行业：新能源汽车\n" in user_content1

    # 有公司行业且有首尾空格
    job_with_ind_spaces = {
        "title": "后端开发",
        "company_name": "蔚来汽车",
        "company_industry": "  新能源汽车  ",
        "city": "上海",
        "description": "车机系统开发",
    }
    msgs2 = build_messages(profile, job_with_ind_spaces)
    assert "- 职位名称：后端开发\n- 公司名称：蔚来汽车\n- 公司行业：新能源汽车\n" in msgs2[1]["content"]

    # 公司行业为空字符串 / 纯空格 / None / 未提供
    for empty_val in ["", "   ", None]:
        job_empty = {
            "title": "后端开发",
            "company_name": "蔚来汽车",
            "company_industry": empty_val,
            "city": "上海",
            "description": "车机系统开发",
        }
        msgs3 = build_messages(profile, job_empty)
        assert "- 职位名称：后端开发\n- 公司名称：蔚来汽车\n- 公司行业：未提供\n" in msgs3[1]["content"]

    # 完全未包含 company_industry 键
    job_no_key = {
        "title": "后端开发",
        "company_name": "蔚来汽车",
        "city": "上海",
        "description": "车机系统开发",
    }
    msgs4 = build_messages(profile, job_no_key)
    assert "- 职位名称：后端开发\n- 公司名称：蔚来汽车\n- 公司行业：未提供\n" in msgs4[1]["content"]


def test_worker_passes_company_industry_to_llm(data_dir: Path, settings: Settings):
    """测试 JudgementWorker 在判断时传给大模型的用户消息中包含 jobs.company_industry（有值与未提供）。"""
    conn = open_db(data_dir)
    now_str = utc_now()

    # 岗位 1：有 company_industry
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, company_industry, first_seen_at, last_seen_at) "
        "VALUES (101, 'boss', 'job_worker_ind_1', 'full', '比亚迪股份有限公司', '新能源汽车', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1010, 101, 1, 'detail', '自动驾驶工程师', '35-50K', 1, 35.0, 50.0, 12, 1, '深圳', '自动驾驶系统开发', 'hash_ind_1', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1010 WHERE id = 101")

    # 岗位 2：无 company_industry (NULL)
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, company_industry, first_seen_at, last_seen_at) "
        "VALUES (102, 'boss', 'job_worker_ind_2', 'full', '无行业科技有限公司', NULL, ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1020, 102, 1, 'detail', '算法工程师', '35-50K', 1, 35.0, 50.0, 12, 1, '深圳', '算法模型训练', 'hash_ind_2', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1020 WHERE id = 102")

    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (10, 'me', 1, '[\"Python\"]', '[]', '[\"深圳\"]', 25.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (10001, 'me', 101, 1010, 10, 'queued', '{}', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (10002, 'me', 102, 1020, 10, 'queued', '{}', ?)",
        (now_str,),
    )
    conn.close()

    captured_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_requests.append(body)
        content = json.dumps(
            {
                "facts": {
                    "summary": {"text": "系统研发", "quotes": ["系统研发"]},
                    "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": ["系统研发"]},
                    "sales_level": {"value": "低", "signals": []},
                    "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                    "work_intensity": {"value": "双休", "quotes": []},
                    "risk_signals": [],
                },
                "verdict": "apply",
                "derivation": ["方向匹配"],
                "verdict_reason": "合适",
                "hr_questions": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    transport = httpx.MockTransport(handler)
    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        review_enabled=False,
    )
    worker = JudgementWorker(worker_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(10001)
        assert worker.wait_idle(5.0)
        assert worker.submit(10002)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    assert len(captured_requests) == 2
    # Verify job 1 contains company_industry: 新能源汽车
    user_msg_1 = next(m["content"] for m in captured_requests[0]["messages"] if m["role"] == "user")
    assert "- 公司名称：比亚迪股份有限公司" in user_msg_1
    assert "- 公司行业：新能源汽车" in user_msg_1

    # Verify job 2 contains company_industry: 未提供
    user_msg_2 = next(m["content"] for m in captured_requests[1]["messages"] if m["role"] == "user")
    assert "- 公司名称：无行业科技有限公司" in user_msg_2
    assert "- 公司行业：未提供" in user_msg_2


def test_build_strict_industry_instruction_with_keywords():
    """测试 build_strict_industry_instruction 传 keywords 与不传 keywords 的表现 (004 修订)。"""
    industries = ["餐饮", "房地产", "汽车"]
    aliases = {
        "房地产": ["房地产开发经营", "物业管理"],
    }
    keywords = {
        "餐饮": ["餐厅", "茶饮"],
        "房地产": ["房地产", "房产", "置业", "中介"],
    }

    # 1. 传 keywords 时：第一行补充关键词说明，并更新判断要点
    res = build_strict_industry_instruction(industries, aliases=aliases, keywords=keywords)
    # 餐饮无同义名，有关键词
    assert "餐饮（职位名或公司名含：餐厅、茶饮）" in res
    # 房地产有同义名，也有关键词
    assert "房地产（BOSS 行业名如：房地产开发经营、物业管理；职位名或公司名含：房地产、房产、置业、中介）" in res
    # 汽车无同义名无关键词
    assert "汽车" in res
    assert "汽车（" not in res
    # 新三级判断文字
    expected_point = (
        '- 先看公司行业（与行业名或同义名相同或包含即算）；'
        '公司行业未命中或为"未提供"时，职位名或公司名含该行业关键词也算属于；'
        '都没有时再根据职位描述判断。'
    )
    assert expected_point in res

    # 2. 不传 keywords（keywords=None）时：输出与改前完全一致
    # 2a. 单参数
    res_single = build_strict_industry_instruction(industries)
    assert "重点排查行业（餐饮、房地产、汽车）：" in res_single
    assert "- 先根据公司名称、职位名称和职位描述判断公司或岗位是否属于上述行业之一。" in res_single
    assert "职位名或公司名含：" not in res_single
    assert "先看公司行业" not in res_single

    # 2b. 传 industries 与 aliases
    res_aliases = build_strict_industry_instruction(industries, aliases=aliases)
    assert "房地产（BOSS 行业名如：房地产开发经营、物业管理）" in res_aliases
    assert "职位名或公司名含：" not in res_aliases
    assert '优先依据"公司行业"' in res_aliases
    assert "先看公司行业" not in res_aliases


def test_build_messages_default_contains_keywords_instruction():
    """测试 build_messages 默认生成的系统提示词中包含关键词说明与更新的判断顺序 (004 修订)。"""
    profile = {"directions": ["Python"]}
    job_version = {"title": "Python后端开发", "city": "广州", "description": "系统研发"}
    msgs = build_messages(profile, job_version)

    sys_content = msgs[0]["content"]
    assert "职位名或公司名含：" in sys_content
    assert "房地产、房产、地产、置业、中介" in sys_content
    expected_point = (
        '- 先看公司行业（与行业名或同义名相同或包含即算）；'
        '公司行业未命中或为"未提供"时，职位名或公司名含该行业关键词也算属于；'
        '都没有时再根据职位描述判断。'
    )
    assert expected_point in sys_content
