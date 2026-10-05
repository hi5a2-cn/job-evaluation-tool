"""Unit tests for HR facts, city question recognition, request card prompts, and profile current_city."""

from pathlib import Path
import sqlite3

import pytest
from starlette.testclient import TestClient

from jet.db.migrations import LATEST_VERSION
from jet.db.store import init_db, open_db
from jet.domain.profiles import get_current_profile, save_profile
from jet.llm.assist import (
    assemble_assist_request,
    build_assist_prompt,
    determine_request_notice,
    determine_unanswered_facts,
    is_city_question,
    read_self_fact_rules,
    verify_and_filter_assist_response,
)
from jet.llm.sanitize import sanitize_chat_messages


def _sample_11_messages() -> list[dict]:
    """真实会话 11 条消息夹具（按顺序，使用虚构姓名李明）。"""
    return [
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 1, "text": "你好！在吗？方便沟通一下吗"},
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 16, "text": "你与该职位竞争者PK情况"},
        {"sender": "我", "is_self": True, "type": 1, "body_type": 1, "text": "您好，在的。想先确认下这个管培生岗位主要做什么，是否含销售？"},
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 1, "text": "管培生前期主要是轮岗学习。"},
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 1, "text": "方便发一份包含你基本情况和工作经历的简历过来吗？简单了解一下"},
        {"sender": "HR", "is_self": False, "type": 3, "body_type": 7, "text": "我想要一份您的附件简历，您是否同意"},
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 1, "text": "[疑问]"},
        {"sender": "系统", "is_self": False, "is_system": True, "type": 3, "body_type": 12, "text": "您的附件简历 李明_简历 已发送给Boss点击查看附件"},
        {"sender": "系统", "is_self": False, "is_system": True, "type": 4, "body_type": 1, "text": "对方已查看了您的附件简历"},
        {"sender": "HR", "is_self": False, "type": 3, "body_type": 7, "text": "您是否接受此工作地点?"},
        {"sender": "HR", "is_self": False, "type": 1, "body_type": 1, "text": "你在成都吗？"},
    ]


def test_11_messages_fixture_prompt_and_orchestration():
    """用 11 条会话消息进行端到端清洗、组装、提示词及后处理断言。"""
    raw_messages = _sample_11_messages()
    sanitized = sanitize_chat_messages(raw_messages, hr_name="王经理", user_name="李明")

    # 1. 组装请求（current_city 为空）
    assembled = assemble_assist_request(
        job_title="管培生",
        company_name="某科技有限公司",
        location_name="成都",
        sanitized_messages=sanitized,
        experiences=[],
        hr_name="王经理",
        user_name="李明",
        current_city="",
    )
    prompt = assembled["prompt_text"]

    # 提示词行格式化验证
    assert "我：[已发送附件简历]" in prompt
    assert "系统：[HR 已查看附件简历]" in prompt
    assert "【HR 地点确认卡片】HR：您是否接受此工作地点?（岗位城市：成都；BOSS 按钮：暂不考虑 / 可以接受）" in prompt
    assert "岗位城市：成都" in prompt

    # 生成要求验证
    assert "- 附件简历已经发给 HR（HR 已查看时也一样），话术不得再说要发简历、不得再问是否需要简历。" in prompt
    assert '- 严格回应HR地点确认卡片：话术必须明确回应能否接受在成都工作，按"可以接受"来写，例如"工作地点在成都我可以接受"。' in prompt
    assert "好的，我这就发您" not in prompt

    # "你在成都吗？"识别为所在城市问题 & current_city 为空时的占位与点名要求
    rules = read_self_fact_rules()
    city_rule = next(r for r in rules if r["name"] == "所在城市")
    assert is_city_question("你在成都吗？", city_rule, location_name="成都", current_city="") is True
    assert any(f["name"] == "所在城市" for f in assembled["unanswered_facts"])
    assert "【填写：所在城市】" in prompt
    assert "- 本次 HR 问到你资料里没有的事实（所在城市），话术必须正面回应，用【填写：类别名】占位，不得绕开。" in prompt

    # request_notice 是地点确认文案（含"可以接受"），不是简历
    req_notice = determine_request_notice(sanitized)
    assert req_notice is not None
    assert "可以接受" in req_notice
    assert "工作地点" in req_notice
    assert "简历" not in req_notice

    # 2. current_city="重庆" 时的组装
    assembled_cq = assemble_assist_request(
        job_title="管培生",
        company_name="某科技有限公司",
        location_name="成都",
        sanitized_messages=sanitized,
        experiences=[],
        hr_name="王经理",
        user_name="李明",
        current_city="重庆",
    )
    prompt_cq = assembled_cq["prompt_text"]
    assert "【我的资料】" in prompt_cq
    assert "目前所在城市：重庆" in prompt_cq
    assert not any(f["name"] == "所在城市" for f in assembled_cq["unanswered_facts"])
    assert "我的资料：目前所在城市" in assembled_cq["fields"]

    # 3. 后处理过滤核验
    # 3.1 含【填写：所在城市】的话术不被丢弃
    raw_resp_with_placeholder = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "您好，我目前在【填写：所在城市】，工作地点在成都我可以接受。",
                "referenced_experience_ids": [],
            }
        ],
        "questions": ["想了解下前期的轮岗安排具体是怎样的？"],
    }
    verified_placeholder = verify_and_filter_assist_response(
        raw_response=raw_resp_with_placeholder,
        valid_experience_ids=set(),
        sanitized_messages=sanitized,
        experiences=[],
        current_city="",
        location_name="成都",
    )
    assert len(verified_placeholder["suggestions"]) == 1
    assert "【填写：所在城市】" in verified_placeholder["suggestions"][0]["text"]

    # 3.2 简历已发送时，"好的，我这就把简历发您"被丢弃并计入 dropped 汇总
    raw_resp_resend = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的，我这就把简历发您。",
                "referenced_experience_ids": [],
            }
        ],
        "questions": ["想了解下前期的轮岗安排具体是怎样的？"],
    }
    verified_resend = verify_and_filter_assist_response(
        raw_response=raw_resp_resend,
        valid_experience_ids=set(),
        sanitized_messages=sanitized,
        experiences=[],
        current_city="成都",
        location_name="成都",
    )
    assert len(verified_resend["suggestions"]) == 0
    assert any(
        d["reason"] == "resume_already_sent" and d["category"] == "简历已发送"
        for d in verified_resend["dropped_summary"]
    )

    # 3.3 "简历已经发您了，您看下"在简历已发送时不被丢弃
    raw_resp_done = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "简历已经发您了，您看下，工作地点在成都我可以接受。",
                "referenced_experience_ids": [],
            }
        ],
        "questions": ["想了解下前期的轮岗安排具体是怎样的？"],
    }
    verified_done = verify_and_filter_assist_response(
        raw_response=raw_resp_done,
        valid_experience_ids=set(),
        sanitized_messages=sanitized,
        experiences=[],
        current_city="成都",
        location_name="成都",
    )
    assert len(verified_done["suggestions"]) == 1
    assert "简历已经发您了" in verified_done["suggestions"][0]["text"]

    # 3.4 简历未发送时，"好的，我这就把简历发您"不被丢弃
    messages_no_resume_sent = [
        {"sender": "HR", "is_self": False, "body_type": 7, "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]
    verified_unsent = verify_and_filter_assist_response(
        raw_response=raw_resp_resend,
        valid_experience_ids=set(),
        sanitized_messages=messages_no_resume_sent,
        experiences=[],
        current_city="成都",
        location_name="成都",
    )
    assert len(verified_unsent["suggestions"]) == 1
    assert "好的，我这就把简历发您" in verified_unsent["suggestions"][0]["text"]


def test_city_question_recognition_accuracy():
    """城市问题识别精准度：覆盖正例与负例。"""
    rules = read_self_fact_rules()
    city_rule = next(r for r in rules if r["name"] == "所在城市")

    positive_cases = [
        "你现在人在哪",
        "人在哪里",
        "在哪个城市",
        "在本地吗",
        "是本地人吗",
        "你在成都吗",
        "现在在成都么",
    ]
    for text in positive_cases:
        assert is_city_question(text, city_rule, location_name="成都", current_city="") is True, f"Failed on positive: {text}"

    negative_cases = [
        "你在吗",
        "你在上班吗",
        "还在看机会吗",
        "在职吗",
        "您想了解哪方面",
        "你是哪个学校毕业的",
        "公司在哪里",
        "你在哪方面有经验？",
        "您在哪个平台看到的我们？",
        "你愿意去成都工作吗？",
        "打算什么时候到成都发展呢？",
    ]
    for text in negative_cases:
        assert is_city_question(text, city_rule, location_name="成都", current_city="") is False, f"Failed on negative: {text}"


def test_request_card_prompt_classification():
    """卡片提示词分类：简历卡片、地点卡片、微信/电话卡片各自生成对应要求一句。"""
    tone_rules = "1. 语气规则"

    # 1. 简历卡片
    prompt_resume = build_assist_prompt(
        job_title="测试工程师",
        company_name="测试公司",
        location_name="深圳",
        sanitized_messages=[
            {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
        ],
        experiences=[],
        tone_rules=tone_rules,
    )
    assert '好的，我这就把简历发您' in prompt_resume
    assert '工作地点在' not in prompt_resume
    assert '可以加微信沟通' not in prompt_resume

    # 2. 地点确认卡片
    prompt_location = build_assist_prompt(
        job_title="测试工程师",
        company_name="测试公司",
        location_name="成都",
        sanitized_messages=[
            {"sender": "HR", "text": "您是否接受此工作地点?", "is_request_card": True, "card_kind": "location_confirm"},
        ],
        experiences=[],
        tone_rules=tone_rules,
    )
    assert '工作地点在成都我可以接受' in prompt_location
    assert '好的，我这就把简历发您' not in prompt_location
    assert '可以加微信沟通' not in prompt_location

    # 3. 微信/电话卡片
    prompt_wechat = build_assist_prompt(
        job_title="测试工程师",
        company_name="测试公司",
        location_name="深圳",
        sanitized_messages=[
            {"sender": "HR", "text": "我想与您交换微信，您是否同意？", "is_request_card": True},
        ],
        experiences=[],
        tone_rules=tone_rules,
    )
    assert '好的，可以加微信沟通' in prompt_wechat
    assert '工作地点在' not in prompt_wechat
    assert '好的，我这就把简历发您' not in prompt_wechat


def test_profile_current_city_api_and_legacy_db_migration(paired_client: tuple[TestClient, dict[str, str]], data_dir: Path):
    """画像 current_city API GET/PUT 往返、字数校验及旧库补列且 user_version 不变测试。"""
    client, headers = paired_client

    # 1. 初始 GET 尚未设置画像 -> 404
    resp_init = client.get("/v1/profile", headers=headers)
    assert resp_init.status_code == 404

    # 2. PUT 画像不带 current_city -> 默认空字符串
    put_payload = {
        "directions": ["后端开发"],
        "keywords": ["Python"],
        "current_city": "",
    }
    resp_put1 = client.put("/v1/profile", json=put_payload, headers=headers)
    assert resp_put1.status_code == 200
    assert resp_put1.json()["version_no"] == 1

    resp_get1 = client.get("/v1/profile", headers=headers)
    assert resp_get1.status_code == 200
    assert resp_get1.json()["current_city"] == ""

    # 3. PUT 更新 current_city="成都"
    put_payload2 = {
        "directions": ["后端开发"],
        "keywords": ["Python"],
        "current_city": "成都",
    }
    resp_put2 = client.put("/v1/profile", json=put_payload2, headers=headers)
    assert resp_put2.status_code == 200
    assert resp_put2.json()["version_no"] == 2
    assert resp_put2.json()["changed"] is True

    resp_get2 = client.get("/v1/profile", headers=headers)
    assert resp_get2.status_code == 200
    assert resp_get2.json()["current_city"] == "成都"

    # 4. current_city 超过 20 字报错 (422)
    put_payload_too_long = {
        "directions": ["后端开发"],
        "keywords": ["Python"],
        "current_city": "成" * 21,
    }
    resp_put_long = client.put("/v1/profile", json=put_payload_too_long, headers=headers)
    assert resp_put_long.status_code == 422
    assert "current_city" in resp_put_long.json()["message"]

    # 6. 旧库补列测试：模拟一个 profiles 表没有 current_city 的当前版本 SQLite 库
    legacy_dir = data_dir / "legacy_test"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_db_path = legacy_dir / "jet.db"

    # 用旧 schema 创建库并设置为当前版本
    conn_legacy = sqlite3.connect(str(legacy_db_path))
    try:
        conn_legacy.execute("CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT NOT NULL)")
        conn_legacy.execute(
            """
            CREATE TABLE profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                version_no INTEGER NOT NULL,
                directions TEXT NOT NULL,
                keywords TEXT NOT NULL,
                cities TEXT NOT NULL,
                min_monthly_k REAL,
                exclude_keywords TEXT NOT NULL,
                work_preference TEXT NOT NULL DEFAULT '',
                background TEXT NOT NULL DEFAULT '',
                excluded_cities TEXT NOT NULL DEFAULT '[]',
                nonpref_min_monthly_k REAL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn_legacy.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    finally:
        conn_legacy.close()

    # 执行 init_db
    res_mig = init_db(legacy_dir)
    assert res_mig is None

    # 检查 profiles 表是否已有 current_city 列，且 user_version 保持不变
    conn_check = open_db(legacy_dir)
    try:
        cols = [r[1] for r in conn_check.execute("PRAGMA table_info(profiles)").fetchall()]
        assert "current_city" in cols
        v = conn_check.execute("PRAGMA user_version").fetchone()[0]
        assert v == LATEST_VERSION
    finally:
        conn_check.close()
