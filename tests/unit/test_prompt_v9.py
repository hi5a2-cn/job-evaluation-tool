"""v9 提示词、重点排查行业归属与 BOSS 标签规则、及复核截断重试测试。

覆盖：
1. v9 PROMPT_VERSION 标识；
2. 系统提示词含规则 16 全文（逐字匹配）；
3. 规则 16 文本出现且位置正确（15 之后、简历段之前；无规则 13、有规则 13、有简历说明均正确）；
4. 用户消息在"- 所属区域："之后插入两行 BOSS 经验与学历要求，空值写"未提供"；
5. v4–v8 的提示词文本（系统与用户消息）不变；
6. parse_facts 沿用 v8 兜底（销售高→skip 仍生效）；
7. v9 配置与分派（JET_PROMPT_VERSION=v9，默认值为 v9）；
8. 过时基准 v9（v8 及更早过时，v9 不过时）；
9. worker _job_version_with_company 带出 experience_req / degree_req；
10. jobs 观察入库写入与更新（新建写入、已有非空才覆盖、null 不覆盖）；
11. 复核输出被截断重试（第一次截断第二次成功、两次都截断、非截断失败不重试）。
"""

import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings, load_settings
from jet.db.store import open_db, utc_now
from jet.domain.jobs import ingest
from jet.domain.judgements import is_method_changed, to_api
from jet.domain.profiles import save_profile
from jet.llm.versions import DEFAULT_PROMPT_VERSION
from jet.llm import prompt_v4, prompt_v5, prompt_v6, prompt_v7, prompt_v8, prompt_v9
from jet.worker import JudgementWorker, _job_version_with_company


def test_v9_prompt_version():
    assert prompt_v9.PROMPT_VERSION == "v9"


def test_v9_system_prompt_contains_rule_16_full_text():
    """验证 v9 系统提示词包含规则 16 全文（逐字匹配）。"""
    sys_prompt = prompt_v9.SYSTEM_PROMPT
    assert "16. 重点排查行业的归属与 BOSS 标签：" in sys_prompt
    assert '若有规则 13 的重点排查行业，以下三项任一命中就算属于该行业：①公司行业与行业名或同义名相同或包含；②职位名或公司名含该行业关键词；③职位描述写明岗位业务所在行业或服务对象属于该行业（如要求"保险行业经验"、负责保险产品、服务银行客户）。公司行业未命中不等于不属于，必须继续看②③。福利待遇里的"商业保险""补充医疗保险"等不算。' in sys_prompt
    assert "判断是否属于重点排查行业时，derivation 中写明依据的是哪一项。" in sys_prompt
    assert '岗位信息中的"经验要求（BOSS 标签）"是招聘方填写的硬性经验要求，按规则 14 的硬性要求处理（如"3-5年"即硬性 3 年及以上）；与职位描述里"优先"的表述同时出现时，以 BOSS 标签为准。标签为"经验不限""在校/应届"或"未提供"时不作为门槛。' in sys_prompt
    assert '"学历要求（BOSS 标签）"同样是硬性要求，对照候选人背景判断是否满足；"学历不限"或"未提供"时不作为门槛。' in sys_prompt


def test_v9_messages_order_no_strict_industry():
    """无重点排查行业时，顺序为 12 -> 14 -> 15 -> 16。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "district": "南山区",
        "description": "负责系统研发，双休",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }

    msgs = prompt_v9.build_messages(profile, job_version, strict_industries=[])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业" not in sys_content
    assert "14. 经验门槛与结论：" in sys_content
    assert "15. 销售成分从严：" in sys_content
    assert "16. 重点排查行业的归属与 BOSS 标签：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    idx_15 = sys_content.index("15. 销售成分从严：")
    idx_16 = sys_content.index("16. 重点排查行业的归属与 BOSS 标签：")
    assert idx_12 < idx_14 < idx_15 < idx_16


def test_v9_messages_order_with_strict_industry():
    """有重点排查行业时，顺序为 12 -> 13 -> 14 -> 15 -> 16。"""
    profile = {
        "directions": ["产品运营"],
        "keywords": ["保险"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 15.0,
    }
    job_version = {
        "title": "产品运营 线上面试",
        "city": "深圳",
        "district": "福田区",
        "description": "有2年以上保险行业数字化产品设计经验优先",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }

    msgs = prompt_v9.build_messages(profile, job_version, strict_industries=["保险"])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业（保险）：" in sys_content
    assert "14. 经验门槛与结论：" in sys_content
    assert "15. 销售成分从严：" in sys_content
    assert "16. 重点排查行业的归属与 BOSS 标签：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_13 = sys_content.index("13. 重点排查行业（保险）：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    idx_15 = sys_content.index("15. 销售成分从严：")
    idx_16 = sys_content.index("16. 重点排查行业的归属与 BOSS 标签：")
    assert idx_12 < idx_13 < idx_14 < idx_15 < idx_16


def test_v9_messages_order_with_resumes():
    """有 2 份以上简历时，规则 16 位于规则 15 之后、简历说明段之前。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端开发"},
        {"slot": 2, "name": "简历2", "profile": "数据开发与挖掘"},
    ]

    msgs = prompt_v9.build_messages(profile, job_version, resumes=resumes)
    sys_content = msgs[0]["content"]

    assert "15. 销售成分从严：" in sys_content
    assert "16. 重点排查行业的归属与 BOSS 标签：" in sys_content
    assert "简历建议：" in sys_content

    idx_15 = sys_content.index("15. 销售成分从严：")
    idx_16 = sys_content.index("16. 重点排查行业的归属与 BOSS 标签：")
    idx_resume = sys_content.index("简历建议：")
    assert idx_15 < idx_16 < idx_resume


def test_v9_user_message_boss_tags_present_and_defaults():
    """用户消息【岗位信息】在"- 所属区域："之后插入两行 BOSS 经验与学历标签，空值写"未提供"。"""
    profile = {
        "directions": ["产品运营"],
        "cities": ["深圳"],
    }

    # 1. 正常包含标签值
    jv_with_tags = {
        "title": "产品运营",
        "company_name": "法本",
        "company_industry": "计算机软件",
        "salary_raw": "10-15K",
        "salary_visible": True,
        "city": "深圳",
        "district": "南山区",
        "description": "岗位职责描述",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }
    msgs1 = prompt_v9.build_messages(profile, jv_with_tags)
    user_content1 = msgs1[1]["content"]
    assert "- 所属区域：南山区\n- 经验要求（BOSS 标签）：3-5年\n- 学历要求（BOSS 标签）：本科\n- 职位描述：" in user_content1

    # 2. 标签为空/空白/None -> 写"未提供"
    jv_empty_tags = {
        "title": "产品运营",
        "company_name": "法本",
        "city": "深圳",
        "district": "未提供",
        "description": "岗位职责描述",
        "experience_req": "   ",
        "degree_req": None,
    }
    msgs2 = prompt_v9.build_messages(profile, jv_empty_tags)
    user_content2 = msgs2[1]["content"]
    assert "- 所属区域：未提供\n- 经验要求（BOSS 标签）：未提供\n- 学历要求（BOSS 标签）：未提供\n- 职位描述：" in user_content2


def test_earlier_prompts_unchanged():
    """v4–v8 的提示词文本（系统和用户消息）不得改变，不含规则 16 与 BOSS 标签两行。"""
    profile = {
        "directions": ["Python"],
        "cities": ["深圳"],
    }
    job_version = {
        "title": "后端开发",
        "city": "深圳",
        "district": "南山区",
        "description": "开发职责",
        "experience_req": "3-5年",
        "degree_req": "本科",
    }

    # v8 提示词不变
    msgs_v8 = prompt_v8.build_messages(profile, job_version)
    assert "16. 重点排查行业的归属与 BOSS 标签" not in msgs_v8[0]["content"]
    assert "经验要求（BOSS 标签）" not in msgs_v8[1]["content"]
    assert "学历要求（BOSS 标签）" not in msgs_v8[1]["content"]
    assert "- 所属区域：南山区\n- 职位描述：" in msgs_v8[1]["content"]

    # v7 提示词不变
    msgs_v7 = prompt_v7.build_messages(profile, job_version)
    assert "16. 重点排查行业的归属与 BOSS 标签" not in msgs_v7[0]["content"]
    assert "经验要求（BOSS 标签）" not in msgs_v7[1]["content"]

    # v5 提示词不变
    msgs_v5 = prompt_v5.build_messages(profile, job_version)
    assert "16. 重点排查行业的归属与 BOSS 标签" not in msgs_v5[0]["content"]
    assert "经验要求（BOSS 标签）" not in msgs_v5[1]["content"]

    # v4 提示词不变
    msgs_v4 = prompt_v4.build_messages(profile, job_version)
    assert "16. 重点排查行业的归属与 BOSS 标签" not in msgs_v4[0]["content"]
    assert "经验要求（BOSS 标签）" not in msgs_v4[1]["content"]


def test_v9_parse_facts_inherits_v8_fallbacks():
    """parse_facts 直接沿用 prompt_v8.parse_facts（销售成分为高时仍兜底为 skip）。"""
    raw_json = json.dumps(
        {
            "facts": {
                "summary": {"text": "职责", "quotes": ["对接银行"]},
                "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": ["职责"]},
                "sales_level": {"value": "高", "signals": ["对接银行"]},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "try",
            "derivation": ["方向契合"],
            "verdict_reason": "契合度高",
            "hr_questions": ["是否接受应届生"],
        },
        ensure_ascii=False,
    )
    facts, verdict, derivation, verdict_reason, hr_questions, resume_sug = prompt_v9.parse_facts(raw_json)
    assert verdict == "skip"
    assert derivation[0] == "销售与客户对接成分为高，结论改为不建议投"
    assert verdict_reason == "销售与客户对接成分高，与不接受销售冲突"
    assert hr_questions == []


def test_v9_config_and_dispatch(data_dir: Path, settings: Settings):
    """JET_PROMPT_VERSION=v9 时配置可用、默认配置为 v9、分派到 v9 并由 worker 保存至数据库。"""
    # 1. 验证默认配置与环境变量配置
    s_default = load_settings(data_dir=data_dir, env={})
    assert s_default.prompt_version == DEFAULT_PROMPT_VERSION

    s_env = load_settings(data_dir=data_dir, env={"JET_PROMPT_VERSION": "v9"})
    assert s_env.prompt_version == "v9"

    # 2. 端到端分派与保存
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, experience_req, degree_req, first_seen_at, last_seen_at) "
        "VALUES (95, 'boss', 'job_v9_dispatch', 'full', '3-5年', '本科', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (950, 95, 1, 'detail', '保险产品运营', '15-20K', 1, 1, '深圳', '有保险数字化经验优先', 'hash_v9_disp', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 950 WHERE id = 95")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (95, 'me', 1, '[\"产品运营\"]', '[]', '[\"深圳\"]', 12.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, engine, rule_result, created_at) "
        "VALUES (9500, ?, 95, 950, 95, 'queued', NULL, 'llm', 'v9', 'deepseek-flash:no-think', '{}', ?)",
        (user_id, now_str),
    )
    conn.commit()

    captured_requests: list[dict] = []

    def mock_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        captured_requests.append(body)
        mock_payload = json.dumps(
            {
                "facts": {
                    "summary": {"text": "保险产品运营职责", "quotes": ["职责"]},
                    "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
                    "sales_level": {"value": "低", "signals": []},
                    "experience": {"requirement": "3年", "requirement_type": "硬性", "value": "不满足", "gap": "差2年"},
                    "work_intensity": {"value": "双休", "quotes": []},
                    "risk_signals": [],
                },
                "verdict": "check",
                "derivation": ["属于保险行业③", "BOSS标签硬性3年，判check"],
                "verdict_reason": "经验硬性要求需核实",
                "hr_questions": ["是否接受应届生或经验放宽"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": mock_payload}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    test_settings = dataclasses.replace(
        settings,
        data_dir=data_dir,
        prompt_version="v9",
        llm_api_key="test-key-v9",
        review_enabled=False,
    )
    worker = JudgementWorker(test_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(9500)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证发送给模型的提示词中包含 BOSS 标签
    assert len(captured_requests) == 1
    sys_sent = next(m["content"] for m in captured_requests[0]["messages"] if m["role"] == "system")
    user_sent = next(m["content"] for m in captured_requests[0]["messages"] if m["role"] == "user")
    assert "16. 重点排查行业的归属与 BOSS 标签：" in sys_sent
    assert "- 经验要求（BOSS 标签）：3-5年" in user_sent
    assert "- 学历要求（BOSS 标签）：本科" in user_sent

    # 验证存盘结果为 v9
    j_saved = conn.execute("SELECT * FROM judgements WHERE id = 9500").fetchone()
    assert j_saved["status"] == "done"
    assert j_saved["verdict"] == "check"
    assert j_saved["prompt_version"] == "v9"
    assert j_saved["verdict_reason"] == "经验硬性要求需核实"

    conn.close()


def test_v9_staleness_baseline():
    """过时基准为 v9：v8 及更早的 llm 判断视为判断方式已变（自动重判），v9 不过时。合并了原 test_v8_staleness_baseline 的全部断言。"""
    assert is_method_changed({"source": "llm", "prompt_version": "v9"}) is False
    assert is_method_changed({"source": "llm", "prompt_version": "v8"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v7"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v6"}) is True
    assert is_method_changed({"source": "llm", "prompt_version": "v5"}) is True


def test_job_version_with_company_boss_tags(data_dir: Path):
    """_job_version_with_company 同 company_industry 一样从 jobs 表读出 experience_req、degree_req。"""
    conn = open_db(data_dir)
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, company_industry, experience_req, degree_req, first_seen_at, last_seen_at) "
        "VALUES (101, 'boss', 'job_boss_tags', 'full', '法本', '计算机软件', '3-5年', '本科', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1010, 101, 1, 'detail', '产品运营', '10-15K', 1, 1, '深圳', '描述', 'hash_1010', ?)",
        (now_str,),
    )
    v_row = conn.execute("SELECT * FROM job_versions WHERE id = 1010").fetchone()

    jv = _job_version_with_company(conn, v_row)
    assert jv["company_name"] == "法本"
    assert jv["company_industry"] == "计算机软件"
    assert jv["experience_req"] == "3-5年"
    assert jv["degree_req"] == "本科"

    # 测试 jobs 表为空或无 job_id 时默认值为 None
    jv_empty = _job_version_with_company(conn, {"title": "无绑定岗位"})
    assert jv_empty["experience_req"] is None
    assert jv_empty["degree_req"] is None

    conn.close()


def test_jobs_ingest_boss_tags(data_dir: Path):
    """jobs 观察入库：新建写入 experience_req、degree_req，已有岗位非空才覆盖，null 不覆盖。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    # 1. 新建岗位写入 experience 和 degree
    job1_payload = [
        {
            "platform_job_id": "boss_job_001",
            "title": "产品运营",
            "city": "深圳",
            "description": "初次观察描述",
            "experience": "3-5年",
            "degree": "本科",
            "company_name": "测试企业",
        }
    ]
    res1 = ingest(conn, user_id=user_id, page_type="detail", jobs=job1_payload, observed_at=now_str)
    row1 = res1["boss_job_001"]
    assert row1["experience_req"] == "3-5年"
    assert row1["degree_req"] == "本科"

    # 2. 已有岗位：传入非空新值，覆盖更新
    job2_payload = [
        {
            "platform_job_id": "boss_job_001",
            "title": "产品运营",
            "city": "深圳",
            "description": "再次观察描述",
            "experience": "5-10年",
            "degree": "硕士",
        }
    ]
    res2 = ingest(conn, user_id=user_id, page_type="detail", jobs=job2_payload, observed_at=now_str)
    row2 = res2["boss_job_001"]
    assert row2["experience_req"] == "5-10年"
    assert row2["degree_req"] == "硕士"

    # 3. 已有岗位：传入 None 或空值，不覆盖已有值
    job3_payload = [
        {
            "platform_job_id": "boss_job_001",
            "title": "产品运营",
            "city": "深圳",
            "description": "第三次观察描述",
            "experience": None,
            "degree": "   ",
        }
    ]
    res3 = ingest(conn, user_id=user_id, page_type="detail", jobs=job3_payload, observed_at=now_str)
    row3 = res3["boss_job_001"]
    assert row3["experience_req"] == "5-10年"
    assert row3["degree_req"] == "硕士"

    conn.close()


def test_review_retry_on_length_truncation_success(data_dir: Path, settings: Settings):
    """复核输出因 finish_reason=length 截断时重试一次；第二次成功时复核结果为第二次的。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (201, 'boss', 'job_review_retry_succ', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (2010, 201, 1, 'detail', '产品运营', '15-20K', 1, 1, '深圳', '职位描述', 'h_rev_1', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 2010 WHERE id = 201")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (201, 'me', 1, '[\"产品运营\"]', '[]', '[\"深圳\"]', 10.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, engine, rule_result, created_at) "
        "VALUES (20100, ?, 201, 2010, 201, 'queued', NULL, 'llm', 'v9', 'deepseek-flash:no-think', '{}', ?)",
        (user_id, now_str),
    )
    conn.commit()

    review_call_count = 0

    def mock_handler(req: httpx.Request) -> httpx.Response:
        nonlocal review_call_count
        body = json.loads(req.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"

        if not thinking:
            # 初判：返回 apply，触发后续复核
            initial_content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "初判描述", "quotes": ["职责"]},
                        "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                        "work_intensity": {"value": "双休", "quotes": []},
                        "risk_signals": [],
                    },
                    "verdict": "apply",
                    "derivation": ["适合投递"],
                    "verdict_reason": "初判理由",
                    "hr_questions": [],
                },
                ensure_ascii=False,
            )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": initial_content}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 100},
                },
            )

        # 复核调用
        review_call_count += 1
        if review_call_count == 1:
            # 第一次复核：思考过程超长导致输出截断，正文为空
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"total_tokens": 1200},
                },
            )
        else:
            # 第二次复核：重试成功，降档为 try
            review_content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "复核描述", "quotes": ["职责"]},
                        "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": "3年", "requirement_type": "硬性", "value": "差一点", "gap": "差1年"},
                        "work_intensity": {"value": "双休", "quotes": []},
                        "risk_signals": [],
                    },
                    "verdict": "try",
                    "derivation": ["经验要求偏高，可尝试"],
                    "verdict_reason": "建议尝试",
                    "hr_questions": ["是否接受放宽"],
                },
                ensure_ascii=False,
            )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": review_content}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 500},
                },
            )

    transport = httpx.MockTransport(mock_handler)
    test_settings = dataclasses.replace(
        settings,
        data_dir=data_dir,
        prompt_version="v9",
        llm_api_key="test-key",
        review_enabled=True,
    )
    worker = JudgementWorker(test_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(20100)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证复核调用了 2 次（重试 1 次）
    assert review_call_count == 2

    # 验证最终判断结果采用第二次复核的结果（由 apply 降为 try）
    j_saved = conn.execute("SELECT * FROM judgements WHERE id = 20100").fetchone()
    assert j_saved["status"] == "done"
    assert j_saved["verdict"] == "try"
    rev_info = json.loads(j_saved["review"])
    assert rev_info["outcome"] == "downgraded"
    assert rev_info["first_verdict"] == "apply"
    assert rev_info["review_verdict"] == "try"

    conn.close()


def test_review_retry_on_length_truncation_both_fail(data_dir: Path, settings: Settings):
    """复核两次都被截断时：只重试一次，记录复核失败。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (202, 'boss', 'job_review_retry_fail', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (2020, 202, 1, 'detail', '产品运营', '15-20K', 1, 1, '深圳', '职位描述', 'h_rev_2', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 2020 WHERE id = 202")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (202, 'me', 1, '[\"产品运营\"]', '[]', '[\"深圳\"]', 10.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, engine, rule_result, created_at) "
        "VALUES (20200, ?, 202, 2020, 202, 'queued', NULL, 'llm', 'v9', 'deepseek-flash:no-think', '{}', ?)",
        (user_id, now_str),
    )
    conn.commit()

    review_call_count = 0

    def mock_handler(req: httpx.Request) -> httpx.Response:
        nonlocal review_call_count
        body = json.loads(req.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"

        if not thinking:
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "初判描述", "quotes": ["职责"]},
                        "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                        "work_intensity": {"value": "双休", "quotes": []},
                        "risk_signals": [],
                    },
                    "verdict": "apply",
                    "derivation": ["适合投递"],
                    "verdict_reason": "初判理由",
                    "hr_questions": [],
                },
                ensure_ascii=False,
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 100}})

        review_call_count += 1
        # 两次复核调用均返回截断
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {"total_tokens": 1200}})

    transport = httpx.MockTransport(mock_handler)
    test_settings = dataclasses.replace(
        settings,
        data_dir=data_dir,
        prompt_version="v9",
        llm_api_key="test-key",
        review_enabled=True,
    )
    worker = JudgementWorker(test_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(20200)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证复核正好调用了 2 次
    assert review_call_count == 2

    # 验证初判 verdict 保留为 apply，review 记录为 failed
    j_saved = conn.execute("SELECT * FROM judgements WHERE id = 20200").fetchone()
    assert j_saved["status"] == "done"
    assert j_saved["verdict"] == "apply"
    rev_info = json.loads(j_saved["review"])
    assert rev_info["outcome"] == "failed"
    assert "finish_reason=length" in rev_info["error"]

    conn.close()


def test_review_non_length_failure_no_retry(data_dir: Path, settings: Settings):
    """复核因非截断原因失败时（如超时/其他报错）：不重试，调用次数为 1。"""
    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (203, 'boss', 'job_review_no_retry', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (2030, 203, 1, 'detail', '产品运营', '15-20K', 1, 1, '深圳', '职位描述', 'h_rev_3', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 2030 WHERE id = 203")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (203, 'me', 1, '[\"产品运营\"]', '[]', '[\"深圳\"]', 10.0, '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, engine, rule_result, created_at) "
        "VALUES (20300, ?, 203, 2030, 203, 'queued', NULL, 'llm', 'v9', 'deepseek-flash:no-think', '{}', ?)",
        (user_id, now_str),
    )
    conn.commit()

    review_call_count = 0

    def mock_handler(req: httpx.Request) -> httpx.Response:
        nonlocal review_call_count
        body = json.loads(req.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"

        if not thinking:
            content = json.dumps(
                {
                    "facts": {
                        "summary": {"text": "初判描述", "quotes": ["职责"]},
                        "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["职责"]},
                        "sales_level": {"value": "低", "signals": []},
                        "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                        "work_intensity": {"value": "双休", "quotes": []},
                        "risk_signals": [],
                    },
                    "verdict": "apply",
                    "derivation": ["适合投递"],
                    "verdict_reason": "初判理由",
                    "hr_questions": [],
                },
                ensure_ascii=False,
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 100}})

        review_call_count += 1
        # 非截断错误：内容为空但 finish_reason 不是 length（例如 stop）
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"total_tokens": 100}})

    transport = httpx.MockTransport(mock_handler)
    test_settings = dataclasses.replace(
        settings,
        data_dir=data_dir,
        prompt_version="v9",
        llm_api_key="test-key",
        review_enabled=True,
    )
    worker = JudgementWorker(test_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(20300)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证复核仅调用了 1 次（不重试）
    assert review_call_count == 1

    j_saved = conn.execute("SELECT * FROM judgements WHERE id = 20300").fetchone()
    assert j_saved["status"] == "done"
    assert j_saved["verdict"] == "apply"
    rev_info = json.loads(j_saved["review"])
    assert rev_info["outcome"] == "failed"
    assert "finish_reason=length" not in rev_info["error"]

    conn.close()
