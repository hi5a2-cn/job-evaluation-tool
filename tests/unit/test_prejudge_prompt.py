import json
import pytest

from jet.llm.prejudge import (
    build_prejudge_messages,
    parse_prejudge_response,
)


def test_prejudge_prompt_whitelist_strictness():
    """断言发送给大模型的内容严格受限，不包含任何违禁字段。"""
    raw_job = {
        "platform_job_id": "job_boss_123",
        "title": "Python 后端开发",
        "company_name": "某科技公司",
        "company_industry": "互联网",
        "salary_raw": "25-35K",
        "city": "深圳",
        "district": "南山区",
        "experience": "3-5年",
        "degree": "本科",
        "job_labels": ["Python", "FastAPI"],
        "skills": ["MySQL", "Redis"],
        # 潜在的违禁字段，模拟可能意外混入的数据
        "hr_notes": "该 HR 很热情，回复快",
        "recruiter_name": "张三",
        "recruiter_title": "招聘专家",
        "description": "这是详细的职位描述包含很多内部敏感内容...",
        "chat_history": ["你好", "简历已发"],
        "candidate_name": "李四",
        "resume_name": "李四的主简历.pdf",
    }

    profile = {
        "directions": ["Python 后端"],
        "keywords": ["FastAPI", "Go"],
        "preferred_cities": ["深圳"],
        "excluded_cities": ["北京"],
        "min_monthly_k": 20,
        "nonpref_min_monthly_k": 25,
        "exclude_keywords": ["外包"],
        "work_preference": "不接受频繁加班",
        "background": "计算机本科",
        # 违禁画像字段
        "real_name": "李四",
        "resumes": [{"name": "简历1.pdf"}],
    }

    strict_industries = ["房地产中介/专业服务", "保险"]

    messages = build_prejudge_messages(profile, [raw_job], strict_industries)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "粗略信息" in messages[0]["content"] or "不构成正式结论" in messages[0]["content"]

    user_content = messages[1]["content"]

    # 包含白名单允许的字段内容
    assert "Python 后端开发" in user_content
    assert "某科技公司" in user_content
    assert "互联网" in user_content
    assert "25-35K" in user_content
    assert "深圳" in user_content
    assert "南山区" in user_content
    assert "3-5年" in user_content
    assert "本科" in user_content
    assert "FastAPI" in user_content
    assert "MySQL" in user_content
    assert "房地产中介/专业服务" in user_content

    # 严禁包含的数据（必须严格排除）
    assert "job_boss_123" not in user_content
    assert "platform_job_id" not in user_content
    assert "岗位ID" not in user_content
    assert "[1] 序号: 1" in user_content
    assert "该 HR 很热情" not in user_content
    assert "张三" not in user_content
    assert "招聘专家" not in user_content
    assert "这是详细的职位描述" not in user_content
    assert "聊天" not in user_content
    assert "你好" not in user_content
    assert "李四" not in user_content
    assert "李四的主简历" not in user_content
    assert "hr_notes" not in user_content
    assert "recruiter" not in user_content


def test_parse_prejudge_response():
    expected_ids = ["1", "2", "3"]

    # 1. 正常包含 markdown 代码块的 JSON
    text1 = """
    这是分析结果：
    ```json
    [
        {"id": "1", "level": "open", "reason": "薪资和方向完全契合，值得点开"},
        {"id": "2", "level": "neutral", "reason": "方向相符但薪资略低"},
        {"id": "3", "level": "skip", "reason": "命中不去的行业且要求不符"}
    ]
    ```
    """
    res1 = parse_prejudge_response(text1, expected_ids)
    assert len(res1) == 3
    assert res1["1"]["level"] == "open"
    assert res1["1"]["reason"] == "薪资和方向完全契合，值得点开"
    assert res1["2"]["level"] == "neutral"
    assert res1["3"]["level"] == "skip"

    # 2. 包含非法 ID（不在本批次）、非法 level 以及超过 40 字的 reason
    long_reason = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的预判理由，超过了四十个字符限制"
    text2 = json.dumps([
        {"id": "1", "level": "open", "reason": long_reason},
        {"id": "2", "level": "invalid_level", "reason": "非法 level 会被过滤"},
        {"id": "99", "level": "open", "reason": "不在批次内的 ID 会被丢弃"},
        {"id": "3", "level": "skip", "reason": "合格的理由"},
    ])
    res2 = parse_prejudge_response(text2, expected_ids)
    assert len(res2) == 2
    # 1 reason 截断至 <= 40 字
    assert "1" in res2
    assert len(res2["1"]["reason"]) <= 40
    # 2 被丢弃（非法 level）
    assert "2" not in res2
    # 99 被丢弃
    assert "99" not in res2
    # 3 正常保留
    assert res2["3"]["level"] == "skip"

    # 3. 损坏或非 JSON 文本返回空字典，不报错崩溃
    assert parse_prejudge_response("这不是合法的 JSON 格式内容", expected_ids) == {}
    assert parse_prejudge_response("", expected_ids) == {}
