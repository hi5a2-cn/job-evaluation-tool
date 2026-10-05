"""Unit tests for action card rules conversion and handling (SC-013).

Spec: specs/003-hr-assistant/spec.md (R8, SC-003, SC-013, FR-055)
"""

from pathlib import Path
import pytest

from jet.llm.assist import (
    assemble_assist_request,
    determine_communication_stage,
    determine_request_notice,
)
from jet.llm.sanitize import (
    read_action_card_rules,
    sanitize_chat_messages,
)


def test_action_card_sequence_waiting_hr():
    """测试真实动作卡片序列与正在等 HR 回复判定：

    消息依次为：
    1. 我的招呼语 (is_self=True, type=3, body_type=1)
    2. 竞争者PK卡片 (body_type=16)
    3. HR'方便的话发一份简历过来' (body_type=1)
    4. '附件简历请求已发送' (is_system=True, body_type=4, is_self=True)
    5. 简历文件卡片 (is_system=True, body_type=12, is_self=False)
    6. 未知系统消息 (is_system=True, body_type=99)

    断言：
    - 结果含'我：[附件简历请求已发送]'与'我：[已发送附件简历]'；
    - 模式为 waiting_hr，依据消息为'我：[已发送附件简历]'；
    - N 的计数为 4，包含两个动作标记；
    - 发送文本中不含文件名'简历A'、'简历A.pdf'等任何卡片原文；
    - body_type=16 与未知系统消息仍不出现；
    - body_type=12 即使 is_self=False 仍归'我'。
    """
    messages = [
        # 1. 我的招呼语
        {
            "is_self": True,
            "type": 3,
            "body_type": 1,
            "text": "您好，我对技术助理岗位很感兴趣，这是张三发送的招呼语。",
        },
        # 2. 竞争者PK卡片
        {
            "is_self": False,
            "body_type": 16,
            "text": "你与该职位竞争者PK情况：处于优势区间",
        },
        # 3. HR 消息
        {
            "is_self": False,
            "body_type": 1,
            "text": "方便的话发一份简历过来",
        },
        # 4. 附件简历请求已发送
        {
            "is_system": True,
            "is_self": True,
            "type": 1,
            "body_type": 4,
            "text": "附件简历请求已发送",
        },
        # 5. 简历文件卡片（注意：真实数据中 is_self 为 False）
        {
            "is_system": True,
            "is_self": False,
            "type": 3,
            "body_type": 12,
            "text": "张三_简历A.pdf 点击查看附件",
        },
        # 6. 未知系统消息
        {
            "is_system": True,
            "is_self": False,
            "body_type": 99,
            "text": "系统安全提醒：请注意防范求职诈骗",
        },
    ]

    sanitized = sanitize_chat_messages(
        messages=messages,
        hr_name="王先生",
        user_name="张三",
    )

    # 1. 过滤与转换后的条数：招呼语 + HR文字 + 请求已发送标记 + 已发送简历标记 = 4 条
    assert len(sanitized) == 4

    # 2. 检查每条发送者与文本
    assert sanitized[0]["sender"] == "我"
    assert "方便的话发一份简历过来" not in sanitized[0]["text"]

    assert sanitized[1]["sender"] == "HR"
    assert sanitized[1]["text"] == "方便的话发一份简历过来"

    assert sanitized[2]["sender"] == "我"
    assert sanitized[2]["text"] == "[附件简历请求已发送]"
    assert sanitized[2]["is_request_card"] is False

    assert sanitized[3]["sender"] == "我"
    assert sanitized[3]["text"] == "[已发送附件简历]"
    assert sanitized[3]["is_request_card"] is False

    # 3. bodyType 12 的 is_self=False 仍归"我"
    assert sanitized[3]["sender"] == "我"

    # 4. 发送文本中不含文件名与卡片原文
    all_sanitized_text = " ".join(item["text"] for item in sanitized)
    assert "简历A" not in all_sanitized_text
    assert "简历A.pdf" not in all_sanitized_text
    assert "点击查看附件" not in all_sanitized_text
    assert "PK情况" not in all_sanitized_text
    assert "系统安全提醒" not in all_sanitized_text

    # 5. 组装提示词与阶段判定测试
    assembled = assemble_assist_request(
        job_title="技术助理",
        company_name="某某科技",
        location_name="北京",
        messages=messages,
        experiences=[],
        hr_name="王先生",
        user_name="张三",
    )

    # 模式为 waiting_hr，N 的计数包含两个标记
    assert assembled["mode"] == "waiting_hr"
    assert assembled["mode_label"] == "正在等 HR 回复"
    assert assembled["mode_basis"] == "我：[已发送附件简历]"
    assert assembled["message_count"] == 4

    # 提示词中包含两个标记且不含卡片原文
    prompt_text = assembled["prompt_text"]
    assert "我：[附件简历请求已发送]" in prompt_text
    assert "我：[已发送附件简历]" in prompt_text
    assert "简历A" not in prompt_text
    assert "简历A.pdf" not in prompt_text
    assert "PK情况" not in prompt_text
    assert "系统安全提醒" not in prompt_text


def test_action_card_rules_dynamic_reading(tmp_path: Path):
    """动作卡片规则文件每次调用时动态读取，修改即时生效。"""
    custom_rules_path = tmp_path / "custom_action_card_rules.json"
    custom_rules_path.write_text(
        """[
            {
                "name": "自定义卡片",
                "body_type": 12,
                "sender": "我",
                "marker": "[自定义简历标记]"
            }
        ]""",
        encoding="utf-8",
    )

    rules = read_action_card_rules(custom_rules_path)
    assert len(rules) == 1
    assert rules[0]["marker"] == "[自定义简历标记]"

    messages = [
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "测试简历.pdf",
        }
    ]
    sanitized = sanitize_chat_messages(
        messages=messages,
        hr_name="李四",
        user_name="张三",
        rules_path=custom_rules_path,
    )
    assert len(sanitized) == 1
    assert sanitized[0]["text"] == "[自定义简历标记]"
    assert sanitized[0]["sender"] == "我"


def test_action_cards_with_camel_case_and_request_notice():
    """测试真实页面 camelCase 字段以及 FR-055 请求卡片判定。"""
    # 当 HR 请求卡片在我最后一条动作标记之前时，不触发 request_notice
    messages = [
        {
            "isSelf": False,
            "bodyType": 7,
            "isSystem": False,
            "text": "我想要一份您的附件简历，您是否同意",
        },
        {
            "isSelf": True,
            "bodyType": 4,
            "isSystem": True,
            "text": "附件简历请求已发送",
        },
        {
            "isSelf": False,
            "bodyType": 12,
            "isSystem": True,
            "text": "您的附件简历 张三_通用版.pdf 已发送给Boss点击查看附件",
        },
    ]

    sanitized = sanitize_chat_messages(
        messages=messages,
        hr_name="王先生",
        user_name="张三",
    )
    assert len(sanitized) == 3
    assert sanitized[0]["sender"] == "HR"
    assert sanitized[0]["is_request_card"] is True
    assert sanitized[1]["sender"] == "我"
    assert sanitized[1]["text"] == "[附件简历请求已发送]"
    assert sanitized[2]["sender"] == "我"
    assert sanitized[2]["text"] == "[已发送附件简历]"
    assert "通用版.pdf" not in sanitized[2]["text"]

    # 请求卡片在我最后一条消息（动作标记）之前，notice 为 None
    notice = determine_request_notice(sanitized)
    assert notice is None


def test_body_type_12_non_resume_cards_filtered_out():
    """body_type=12 文字为'查看职位详情'或'点击查看'之类不含简历特征的卡片不出现在结果中。"""
    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "text": "您好，想了解一下该职位。",
        },
        {
            "is_self": False,
            "body_type": 1,
            "text": "你好，岗位目前还在招聘中。",
        },
        # body_type=12 且文字为"查看职位详情"，不含简历特征
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "查看职位详情",
        },
        # body_type=12 且文字为"点击查看"，不含简历特征
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "点击查看",
        },
        # body_type=12 非系统消息但不含简历特征
        {
            "is_system": False,
            "is_self": False,
            "body_type": 12,
            "text": "点击查看职位要求与公司主页",
        },
    ]

    sanitized = sanitize_chat_messages(
        messages=messages,
        hr_name="李四",
        user_name="张三",
    )

    # 3 条不满足简历特征条件的 body_type=12 卡片均被按未知卡片过滤，仅保留 2 条普通文本消息
    assert len(sanitized) == 2
    assert sanitized[0]["text"] == "您好，想了解一下该职位。"
    assert sanitized[1]["text"] == "你好，岗位目前还在招聘中。"

    all_sanitized_text = " ".join(item["text"] for item in sanitized)
    assert "查看职位详情" not in all_sanitized_text
    assert "点击查看" not in all_sanitized_text
    assert "公司主页" not in all_sanitized_text
    assert "[已发送附件简历]" not in all_sanitized_text

    # 验证 assemble_assist_request 中 N 计数与依据均不包含过滤掉的卡片
    assembled = assemble_assist_request(
        job_title="测试工程师",
        company_name="测试公司",
        location_name="北京",
        messages=messages,
        experiences=[],
        hr_name="李四",
        user_name="张三",
    )
    assert assembled["message_count"] == 2
    assert assembled["mode"] == "reply"
    assert "查看职位详情" not in assembled["prompt_text"]
    assert "点击查看" not in assembled["prompt_text"]
    assert "[已发送附件简历]" not in assembled["prompt_text"]


def test_body_type_12_resume_cards_converted_without_filename():
    """body_type=12 包含'简历'或 .pdf/.doc/.docx 扩展名才转为标记，且标记中永远不含文件名。"""
    messages = [
        # 已知样本 1
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "张三_简历A.pdf 点击查看附件",
        },
        # 已知样本 2
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "您的附件简历 张三_通用版.pdf 已发送给Boss点击查看附件",
        },
        # 仅含 .pdf 扩展名不含"简历"
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "my_portfolio_v2.pdf 点击查看",
        },
        # 仅含 .doc 扩展名
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "work_history_2026.doc 点击查看",
        },
        # 仅含 .docx 扩展名
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "cv_english_version.docx 点击查看",
        },
        # 仅含"简历"不含扩展名
        {
            "is_system": True,
            "is_self": False,
            "body_type": 12,
            "text": "候选人在线个人简历 点击查看",
        },
    ]

    sanitized = sanitize_chat_messages(
        messages=messages,
        hr_name="李四",
        user_name="张三",
    )

    # 6 条样本均满足条件并转为标记
    assert len(sanitized) == 6

    for item in sanitized:
        assert item["sender"] == "我"
        assert item["text"] == "[已发送附件简历]"
        assert item["is_request_card"] is False

    # 标记中永远不含任何文件名与卡片原文特征
    all_sanitized_text = " ".join(item["text"] for item in sanitized)
    assert "张三" not in all_sanitized_text
    assert "简历A" not in all_sanitized_text
    assert "通用版" not in all_sanitized_text
    assert "portfolio" not in all_sanitized_text
    assert "work_history" not in all_sanitized_text
    assert "english" not in all_sanitized_text
    assert ".pdf" not in all_sanitized_text
    assert ".doc" not in all_sanitized_text
    assert ".docx" not in all_sanitized_text
    assert "点击查看" not in all_sanitized_text
