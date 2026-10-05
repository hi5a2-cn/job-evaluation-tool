"""Tests for chat messages sanitization and hash computation (T006).

Spec: specs/003-hr-assistant/spec.md (FR-003, FR-021, FR-022, SC-002, SC-003)
Tasks: specs/003-hr-assistant/tasks.md (T006, T007)
Plan: specs/003-hr-assistant/plan.md (Decisions 2, 7)
"""

import pytest

from jet.llm.sanitize import ID_CARD_PATTERN, PLACEHOLDER, get_sensitive_name_terms, sanitize_text


def test_sanitize_hr_name_stripped():
    """1. HR 姓名（如'王丽'）在正文中出现时被完整删去。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "你好，我是王丽，负责本岗位的初步沟通。",
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "王丽你好，请问岗位的具体业务方向是什么？",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    all_text = " ".join(item["text"] for item in sanitized)
    assert "王丽" not in all_text


def test_sanitize_user_name_stripped():
    """2. 我自己的姓名（如'张三'）在正文中出现时被完整删去。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "您好，我是候选人张三，这是我过往的项目总结。",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "收到，张三你的履历很匹配，我们进一步聊聊。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    all_text = " ".join(item["text"] for item in sanitized)
    assert "张三" not in all_text


def test_sanitize_single_surname_honorifics():
    """3. 单姓+称呼（王女士/王先生/王经理/王老师/王总/张先生等）删去，无关单姓称呼（李女士）保留。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "王女士您好！王先生和王经理在吗？另外王老师与王总何时方便沟通？",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "张先生你好，张女士和张经理也可以一起沟通。前同事李女士也向我推荐了李总的项目。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    all_text = " ".join(item["text"] for item in sanitized)

    # 属于 HR 或我的姓 + 称呼必须被删去
    for term in ["王女士", "王先生", "王经理", "王老师", "王总"]:
        assert term not in all_text
    for term in ["张先生", "张女士", "张经理"]:
        assert term not in all_text

    # 与这两个姓名无关的单姓称呼绝对不应被误删
    assert "李女士" in all_text
    assert "李总" in all_text


def test_sanitize_compound_surname_honorifics():
    """4. 常见复姓+称呼（如欧阳女士/欧阳经理）删去且不残留单字称呼（阳女士），非复姓普通双字名按首字处理。"""
    from jet.llm.sanitize import sanitize_chat_messages

    # 复姓匹配测试：欧阳娜娜（复姓欧阳）
    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "欧阳女士您好，请问欧阳经理或者欧阳老师在吗？",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "我是欧阳娜娜，诸葛先生你好，诸葛总有交代过。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="欧阳娜娜", user_name="诸葛孔明")
    all_text = " ".join(item["text"] for item in sanitized)

    # 复姓+称呼完整删去，且不得残留单字称呼（如"阳女士"、"葛先生"）
    for term in ["欧阳女士", "欧阳经理", "欧阳老师", "诸葛先生", "诸葛总"]:
        assert term not in all_text
    for term in ["阳女士", "阳经理", "阳老师", "葛先生", "葛总"]:
        assert term not in all_text

    # 普通双字名（不在复姓表中）按首字单姓处理：关羽（姓关）
    messages_regular = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "关女士您好，请问关经理在吗？",
        }
    ]
    sanitized_regular = sanitize_chat_messages(messages_regular, hr_name="关羽", user_name="刘备")
    regular_text = " ".join(item["text"] for item in sanitized_regular)
    assert "关女士" not in regular_text
    assert "关经理" not in regular_text


def test_sanitize_phone_numbers():
    """5. HR 与我的手机号（连续数字、带破折号、+86国际区号等格式）均被删去。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "可以直接拨打我的手机：13812345678，或者座机转分机。",
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "好的，我的联系电话是 138-1234-5678，海外号码也可以打 +86 13812345678 或 +86-13900001111。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    all_text = " ".join(item["text"] for item in sanitized)

    for phone in ["13812345678", "138-1234-5678", "+86 13812345678", "+86-13900001111"]:
        assert phone not in all_text


def test_sanitize_wechat_ids():
    """6. 微信号（微信 wxid_...、vx: ...、加我微信：...）均被删去。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "这是我的微信 wxid_abc123，欢迎添加。",
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "我的 vx: zhang_san88，或者加我微信：abc-12345 都可以。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    all_text = " ".join(item["text"] for item in sanitized)

    for wx in ["wxid_abc123", "zhang_san88", "abc-12345"]:
        assert wx not in all_text


def test_sanitize_emails():
    """7. 双方电子邮箱（如 hr@example.com、zhang.san@qq.com）均被删去。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "简历请发送至 hr@example.com，我们会尽快评估。",
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "已发送，我的发信邮箱是 zhang.san@qq.com，请查收。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    all_text = " ".join(item["text"] for item in sanitized)

    for email in ["hr@example.com", "zhang.san@qq.com"]:
        assert email not in all_text


def test_sanitize_card_and_system_messages_sc003():
    """8. SC-003：body_type=16 卡片与 is_system=True 系统消息被剔除，body_type=7 HR 请求卡片保留文字并脱敏。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 16,
            "is_system": False,
            "text": "你与该职位竞争者PK情况：综合竞争力排名前 10%",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": True,
            "text": "你撤回了一条消息",
        },
        {
            "is_self": True,
            "body_type": 12,
            "is_system": True,
            "text": "附件简历已发送",
        },
        {
            "is_self": False,
            "body_type": 7,
            "is_system": False,
            "text": "我是王丽，我想要一份您的附件简历，您是否同意",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "方便发一份过来看看吗？",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    # SC-013: body_type=12 转为固定标记保留，body_type=7 请求卡片与普通文字消息保留，其余 2 条过滤
    assert len(sanitized) == 3

    all_text = " ".join(item["text"] for item in sanitized)
    assert "PK情况" not in all_text
    assert "撤回了一条消息" not in all_text
    assert "附件简历已发送" not in all_text

    # body_type=12 转为固定标记归属我方
    assert sanitized[0]["sender"] == "我"
    assert sanitized[0]["text"] == "[已发送附件简历]"

    # body_type=7 卡片文字保留且其中的 HR 姓名脱敏
    req_msg = sanitized[1]
    assert req_msg["sender"] == "HR"
    assert "我想要一份您的附件简历，您是否同意" in req_msg["text"]
    assert "王丽" not in req_msg["text"]


def test_sanitize_auto_greeting_attributed_to_self():
    """9. BOSS 自动招呼语（is_self=True、body_type=1）归属为'我'，HR 消息归属为'HR'。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "您好，请问贵公司的技术助理岗位还在招人吗？",
        },
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "在招的，随时可以沟通。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    assert len(sanitized) == 2
    assert sanitized[0]["sender"] == "我"
    assert sanitized[1]["sender"] == "HR"


def test_sanitize_user_name_unavailable_raises():
    """10. 读不到我方姓名：user_name 为空字符串、全空白或 None 时抛出 SelfNameUnavailable。"""
    from jet.llm.sanitize import SelfNameUnavailable, sanitize_chat_messages

    messages = [
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "您好，想了解岗位信息。",
        }
    ]

    with pytest.raises(SelfNameUnavailable):
        sanitize_chat_messages(messages, hr_name="王丽", user_name="")

    with pytest.raises(SelfNameUnavailable):
        sanitize_chat_messages(messages, hr_name="王丽", user_name=None)

    with pytest.raises(SelfNameUnavailable):
        sanitize_chat_messages(messages, hr_name="王丽", user_name="   ")


def test_sanitize_determinism_and_prompt_hash():
    """11. 纯函数确定性：同一输入两次调用结果与 SHA-256 完全一致；输入改一个字哈希变化。"""
    from jet.llm.sanitize import compute_prompt_hash, sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "王丽经理在招人，电话 13812345678。",
        }
    ]

    res1 = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    res2 = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    assert res1 == res2

    text_a = "【岗位信息】\n职位：Python开发\n公司：测试公司"
    text_b = "【岗位信息】\n职位：Python开发\n公司：测试企业"

    hash_a1 = compute_prompt_hash(text_a)
    hash_a2 = compute_prompt_hash(text_a)
    hash_b = compute_prompt_hash(text_b)

    assert hash_a1 == hash_a2
    assert hash_a1 != hash_b
    assert len(hash_a1) == 64
    assert all(c in "0123456789abcdef" for c in hash_a1)


def test_sanitize_comprehensive_chat_sample():
    """12. 综合样本测试：把全部敏感类型放入同一段真实交互聊天，逐项断言脱敏后绝无任何残留。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        # 1. 自动打招呼（归我方）
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "您好，我对贵公司的岗位很感兴趣，这是张三发送的招呼语。",
        },
        # 2. HR 回复（包含 HR 姓名、我方姓名、单姓称呼、联系方式）
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "张三你好，我是王丽，担任招聘负责人。张先生可以叫我王经理或者王老师。有疑问加微信 wxid_abc123 或发邮件到 hr@example.com，电话是 13812345678。",
        },
        # 3. 系统提示（必须剔除）
        {
            "is_self": False,
            "body_type": 1,
            "is_system": True,
            "text": "你撤回了一条消息",
        },
        # 4. PK 卡片（body_type=16，必须剔除）
        {
            "is_self": False,
            "body_type": 16,
            "is_system": False,
            "text": "你与该职位竞争者PK情况：处于优势区间",
        },
        # 5. 我方回复（包含 HR 称呼、我方联系方式、第三方推荐人称呼）
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "好的王总！我的手机号是 138-1234-5678（国际也可 +86 13812345678），vx: zhang_san88，邮箱 zhang.san@qq.com。之前前同事李女士也向我推荐过贵公司。",
        },
        # 6. HR 追问微信
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "收到，或者加我微信：abc-12345 也可以。",
        },
        # 7. HR 请求卡片（body_type=7，保留文本但脱敏）
        {
            "is_self": False,
            "body_type": 7,
            "is_system": False,
            "text": "王丽发来请求：我想要一份您的附件简历，您是否同意",
        },
        # 8. 简历已发送卡片（body_type=12 / is_system=True，必须剔除）
        {
            "is_self": True,
            "body_type": 12,
            "is_system": True,
            "text": "附件简历已发送",
        },
    ]

    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    # SC-013: 剔除系统消息、PK卡片后，简历发送卡片转为固定标记保留，保留 6 条消息
    assert len(sanitized) == 6

    # 检查保留的消息发送者角色对应
    expected_senders = ["我", "HR", "我", "HR", "HR", "我"]
    actual_senders = [item["sender"] for item in sanitized]
    assert actual_senders == expected_senders
    assert sanitized[5]["text"] == "[已发送附件简历]"

    all_sanitized_text = " ".join(item["text"] for item in sanitized)

    # 严禁残留项检查
    forbidden_terms = [
        "王丽",              # HR 姓名
        "张三",              # 我方姓名
        "王经理",            # HR 姓 + 称呼
        "王老师",            # HR 姓 + 称呼
        "王总",              # HR 姓 + 称呼
        "张先生",            # 我方 姓 + 称呼
        "13812345678",       # 手机号（无连字符）
        "138-1234-5678",     # 手机号（有连字符）
        "+86 13812345678",   # 带国际区号手机号
        "wxid_abc123",       # 微信号
        "zhang_san88",       # 微信号
        "abc-12345",         # 微信号
        "hr@example.com",    # HR 邮箱
        "zhang.san@qq.com",  # 我方邮箱
        "PK情况",            # body_type=16 卡片内容
        "你撤回了一条消息",   # 系统消息
        "附件简历已发送",     # 简历卡片系统消息
    ]

    for term in forbidden_terms:
        assert term not in all_sanitized_text, f"脱敏后文本中仍然残留敏感信息: '{term}'"

    # 无关单姓称呼绝对不被误删
    assert "李女士" in all_sanitized_text

    # body_type=7 请求卡片的核心文字必须保留
    assert "我想要一份您的附件简历，您是否同意" in all_sanitized_text


def test_sanitize_hr_greeting_type3_attributed_to_hr():
    """is_self=False、type=3、body_type=1 的消息归'HR'；is_self=True、type=3 的归'我'。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "type": 3,
            "body_type": 1,
            "is_system": False,
            "text": "同学你好，对我们带薪实习生岗位感兴趣吗？",
        },
        {
            "is_self": True,
            "type": 3,
            "body_type": 1,
            "is_system": False,
            "text": "您好，我想了解一下具体的实习要求。",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    assert len(sanitized) == 2
    assert sanitized[0]["sender"] == "HR"
    assert sanitized[1]["sender"] == "我"


def test_sanitize_output_contains_only_sender_and_text():
    """输入消息带额外字段（如 fromName、avatar、mid）时，输出每项的键集合恰好是 {"sender","text","is_request_card"}。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "is_system": False,
            "text": "你好，请发一份简历。",
            "fromName": "王丽",
            "avatar": "https://img.bosszhipin.com/avatar.png",
            "mid": "m123456",
            "time": 1727330000,
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "好的，已发送。",
            "fromName": "张三",
            "avatar": "https://img.bosszhipin.com/myavatar.png",
            "mid": "m123457",
            "extra_info": {"foo": "bar"},
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    assert len(sanitized) == 2
    for item in sanitized:
        assert set(item.keys()) == {"sender", "text", "is_request_card"}


def test_sanitize_missing_body_type_excluded():
    """没有 body_type 的消息不出现在结果中。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "is_system": False,
            "text": "这是一条缺失 body_type 的消息",
        },
        {
            "is_self": True,
            "body_type": 1,
            "is_system": False,
            "text": "这是正常保留的消息",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    assert len(sanitized) == 1
    assert sanitized[0]["text"] == "这是正常保留的消息"


def test_sanitize_is_request_card_flag():
    """body_type == 7 时 is_request_card 为 True，body_type == 1 时为 False (FR-055)。"""
    from jet.llm.sanitize import sanitize_chat_messages

    messages = [
        {
            "is_self": False,
            "body_type": 1,
            "text": "普通文字消息",
        },
        {
            "is_self": False,
            "body_type": 7,
            "text": "我想要一份您的附件简历，您是否同意",
        },
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    assert len(sanitized) == 2
    assert sanitized[0]["is_request_card"] is False
    assert sanitized[1]["is_request_card"] is True


def test_sanitize_quote_context_found_all_parties():
    """按编号找到时正确生成 quote_context 并正确拼接（回复我 / 回复 HR / 回复自己）。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import format_chat_message_line

    messages = [
        {"is_self": True, "body_type": 1, "text": "请问有销售指标吗？", "mid": 101, "quote_id": None},
        {"is_self": False, "body_type": 1, "text": "都有的", "mid": 102, "quote_id": 101},  # HR 回复我
        {"is_self": True, "body_type": 1, "text": "好的明白", "mid": 103, "quote_id": 102},  # 我回复 HR
        {"is_self": False, "body_type": 1, "text": "另外补充一下", "mid": 104, "quote_id": 102},  # HR 回复自己
        {"is_self": True, "body_type": 1, "text": "更正一下我的表述", "mid": 105, "quote_id": 103},  # 我回复自己
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    # 1. 结构与 quote_context 检验
    assert "quote_context" not in sanitized[0]
    assert sanitized[1]["quote_context"] == {"from": "我", "text": "请问有销售指标吗？"}
    assert sanitized[2]["quote_context"] == {"from": "HR", "text": "都有的"}
    assert sanitized[3]["quote_context"] == {"from": "HR", "text": "都有的"}
    assert sanitized[4]["quote_context"] == {"from": "我", "text": "好的明白"}

    # 2. 格式拼接检验
    assert format_chat_message_line(sanitized[1]) == "HR（回复我：'请问有销售指标吗？'）：都有的"
    assert format_chat_message_line(sanitized[2]) == "我（回复HR：'都有的'）：好的明白"
    assert format_chat_message_line(sanitized[3]) == "HR（回复自己：'都有的'）：另外补充一下"
    assert format_chat_message_line(sanitized[4]) == "我（回复自己：'好的明白'）：更正一下我的表述"


def test_sanitize_quote_context_truncation_over_40():
    """超过 40 字截断并加'……'，不超过 40 字原样保留。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import format_chat_message_line

    long_45 = "这是一段非常非常长的提问文本用于测试截断逻辑是否正确生效超过四十个字时会被截断处理"
    exact_40 = "这是一段恰好整整四十个字的消息文本用于验证刚好四十个字符的时候不会被截断啊"
    assert len(long_45) == 42 or len(long_45) > 40
    long_45 = "A" * 45
    exact_40 = "B" * 40
    assert len(long_45) == 45
    assert len(exact_40) == 40

    messages = [
        {"is_self": True, "body_type": 1, "text": long_45, "mid": 201},
        {"is_self": False, "body_type": 1, "text": "回复长消息", "mid": 202, "quote_id": 201},
        {"is_self": True, "body_type": 1, "text": exact_40, "mid": 203},
        {"is_self": False, "body_type": 1, "text": "回复40字消息", "mid": 204, "quote_id": 203},
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    # 45 字截断为前 40 字 + ……
    expected_truncated = long_45[:40] + "……"
    assert sanitized[1]["quote_context"]["text"] == expected_truncated
    assert format_chat_message_line(sanitized[1]) == f"HR（回复我：'{expected_truncated}'）：回复长消息"

    # 40 字不截断，不加 ……
    assert sanitized[3]["quote_context"]["text"] == exact_40
    assert format_chat_message_line(sanitized[3]) == f"HR（回复我：'{exact_40}'）：回复40字消息"


def test_sanitize_quote_context_sensitive_name_masked():
    """被引用原文中的姓名（我方姓名、HR姓名、称呼等）被隐藏。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import format_chat_message_line

    messages = [
        {"is_self": True, "body_type": 1, "text": "我是候选人张三，想联系王丽经理", "mid": 301},
        {"is_self": False, "body_type": 1, "text": "收到了", "mid": 302, "quote_id": 301},
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    quoted_text = sanitized[1]["quote_context"]["text"]
    assert "张三" not in quoted_text
    assert "王丽" not in quoted_text
    assert "[已隐藏]" in quoted_text
    line = format_chat_message_line(sanitized[1])
    assert "张三" not in line
    assert "王丽" not in line


def test_sanitize_quote_context_not_found_outside_window():
    """被引用消息在 30 条窗口之外时，quote_context 设为 None，写'回复了一条较早的消息'且不含其文字。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import format_chat_message_line

    messages = []
    # 第 1 条被引用消息包含特殊文字
    messages.append({
        "is_self": True,
        "body_type": 1,
        "text": "这是很早的一条特殊消息SECRET_TEXT",
        "mid": 999,
    })
    # 填充 30 条中间消息，使得第 1 条被挤出 30 条窗口
    for i in range(30):
        messages.append({
            "is_self": i % 2 == 0,
            "body_type": 1,
            "text": f"中间消息 {i}",
            "mid": 1000 + i,
        })
    # 最后一条 HR 引用第 1 条消息
    messages.append({
        "is_self": False,
        "body_type": 1,
        "text": "回复很早以前的消息",
        "mid": 2000,
        "quote_id": 999,
    })

    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    assert len(sanitized) == 30  # 最多取 30 条
    last_msg = sanitized[-1]
    assert last_msg["quote_context"] == {"from": None, "text": None}
    assert format_chat_message_line(last_msg) == "HR（回复了一条较早的消息）：回复很早以前的消息"

    # 绝不读取未保留消息的文字
    all_sanitized_str = str(sanitized)
    assert "SECRET_TEXT" not in all_sanitized_str


def test_sanitize_quote_context_not_found_filtered_out():
    """被引用消息被过滤（如 body_type=16 或 item-system）时，写'回复了一条较早的消息'且不含其文字。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import format_chat_message_line

    messages = [
        # body_type=16 被过滤
        {"is_self": False, "body_type": 16, "text": "PK情况卡片SECRET_PK", "mid": 501},
        # 普通系统消息被过滤
        {"is_self": False, "is_system": True, "body_type": 1, "text": "你撤回了一条消息SECRET_SYS", "mid": 502},
        # HR 引用了被过滤的 501
        {"is_self": False, "body_type": 1, "text": "针对PK情况进行说明", "mid": 503, "quote_id": 501},
        # 我引用了被过滤的 502
        {"is_self": True, "body_type": 1, "text": "好的收到", "mid": 504, "quote_id": 502},
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    assert len(sanitized) == 2
    assert sanitized[0]["quote_context"] == {"from": None, "text": None}
    assert sanitized[1]["quote_context"] == {"from": None, "text": None}

    assert format_chat_message_line(sanitized[0]) == "HR（回复了一条较早的消息）：针对PK情况进行说明"
    assert format_chat_message_line(sanitized[1]) == "我（回复了一条较早的消息）：好的收到"

    # 绝不包含未保留消息的文字
    all_str = str(sanitized)
    assert "SECRET_PK" not in all_str
    assert "SECRET_SYS" not in all_str


def test_sanitize_mid_and_quote_id_never_in_output_or_prompt():
    """mid 与 quote_id 不出现在 sanitize 输出中，也不出现在发送给模型的 prompt 中。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import assemble_assist_request

    messages = [
        {"is_self": True, "body_type": 1, "text": "提问", "mid": 7771, "quote_id": None},
        {"is_self": False, "body_type": 1, "text": "回答", "mid": 7772, "quote_id": 7771},
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")
    for item in sanitized:
        assert "mid" not in item
        assert "quote_id" not in item
        assert "_mid" not in item
        assert "_quote_id" not in item

    assembled = assemble_assist_request(
        job_title="产品运营",
        company_name="某公司",
        location_name="北京",
        messages=messages,
        experiences=[],
        hr_name="王丽",
        user_name="张三",
    )
    prompt = assembled["prompt_text"]
    assert "7771" not in prompt
    assert "7772" not in prompt
    assert "HR（回复我：'提问'）：回答" in prompt


def test_quote_context_does_not_affect_mode_and_unanswered_facts():
    """quote_context 不影响沟通模式判定与'资料里没有的事实'检测（例如 HR 引用了我问'期望薪资'的消息，不应被判为 HR 在问我的期望薪资）。"""
    from jet.llm.sanitize import sanitize_chat_messages
    from jet.llm.assist import (
        assemble_assist_request,
        determine_communication_stage,
        determine_unanswered_facts,
    )

    # 我问期望薪资与所在城市，HR 回复引用该消息，HR 自身文字仅为"都有的"
    messages = [
        {"is_self": True, "body_type": 1, "text": "请问岗位对所在城市和期望薪资有硬性要求吗？", "mid": 801},
        {"is_self": False, "body_type": 1, "text": "都有的", "mid": 802, "quote_id": 801},
    ]
    sanitized = sanitize_chat_messages(messages, hr_name="王丽", user_name="张三")

    # 1. 资料里没有的事实类别检测：HR 自身文字是"都有的"，并未向我提问期望薪资或所在城市
    unanswered = determine_unanswered_facts(sanitized_messages=sanitized, experiences=[])
    assert unanswered == []  # 不应被误判为 HR 在问期望薪资

    # 2. 沟通模式判定：最后一条是 HR 的"都有的"，判为 reply，依据为 "HR：都有的"，不受 quote_context 干扰
    mode, label, basis = determine_communication_stage(sanitized)
    assert mode == "reply"
    assert basis == "HR：都有的"

    assembled = assemble_assist_request(
        job_title="产品运营",
        company_name="某公司",
        location_name="北京",
        messages=messages,
        experiences=[],
        hr_name="王丽",
        user_name="张三",
    )
    assert assembled["mode"] == "reply"
    assert assembled["unanswered_facts"] == []


# ---- 以下三条原在 tests/unit/test_sanitize.py（体检第 77 条合并：两个文件都测 sanitize.py）----


def test_id_card_pattern_recognition():
    """测试 18 位中国居民身份证正则表达式匹配 (T005)。"""
    # 纯数字
    id1 = "110101199001011234"
    assert ID_CARD_PATTERN.search(id1) is not None
    assert ID_CARD_PATTERN.search(id1).group(0) == id1

    # 尾号 X / x
    id2 = "11010119900101123X"
    assert ID_CARD_PATTERN.search(id2) is not None
    assert ID_CARD_PATTERN.search(id2).group(0) == id2

    id3 = "44030119950512345x"
    assert ID_CARD_PATTERN.search(id3) is not None
    assert ID_CARD_PATTERN.search(id3).group(0) == id3

    # 上下文嵌入
    text = "候选人身份证号：110101199001011234，户籍所在地北京。"
    match = ID_CARD_PATTERN.search(text)
    assert match is not None
    assert match.group(0) == "110101199001011234"

    # 非法号码不误判
    # 17 位数字（少一位）
    assert ID_CARD_PATTERN.search("11010119900101123") is None
    # 19 位连续数字不匹配（前后有数字边界界定）
    assert ID_CARD_PATTERN.search("11101011990010112345") is None
    # 月份非法（如 13 月）
    assert ID_CARD_PATTERN.search("110101199013011234") is None
    # 日期非法（如 32 日）
    assert ID_CARD_PATTERN.search("110101199001321234") is None


def test_sanitize_text_with_id_card():
    """测试 sanitize_text 对身份证号进行掩码替换 (T005)。"""
    text = "联系人：李明，身份证：110101199001011234，电话：13800000000。"
    terms = get_sensitive_name_terms(None, "李明")
    sanitized = sanitize_text(text, terms)

    assert "110101199001011234" not in sanitized
    assert "13800000000" not in sanitized
    assert "李明" not in sanitized
    assert sanitized == f"联系人：{PLACEHOLDER}，身份证：{PLACEHOLDER}，电话：{PLACEHOLDER}。"


def test_sanitize_text_all_privacy_fields():
    """测试聊天文字里的姓名、手机、邮箱、微信、身份证全量脱敏 (FR-009, SC-002)。"""
    # sanitize_text 是聊天用的脱敏（简历专用的脱敏见 test_resume_pdf.py），这里用一段聊天文字
    raw_chat = (
        "您好，我是李明。\n"
        "我的电话：13800000000，邮箱：liming@example.com\n"
        "微信：wxid_liming999 或 加微信: liming_dev\n"
        "身份证：110101199001011234\n"
        "想投 Python 高级研发工程师，"
        "李先生在某科技公司负责后端分布式系统架构与性能优化。"
    )
    terms = get_sensitive_name_terms(None, "李明")
    sanitized = sanitize_text(raw_chat, terms)

    # 隐私信息 100% 被替换为 [已隐藏]
    assert "李明" not in sanitized
    assert "李先生" not in sanitized
    assert "13800000000" not in sanitized
    assert "liming@example.com" not in sanitized
    assert "wxid_liming999" not in sanitized
    assert "liming_dev" not in sanitized
    assert "110101199001011234" not in sanitized

    # 非隐私工作经验保留
    assert "Python 高级研发工程师" in sanitized
    assert "后端分布式系统架构与性能优化" in sanitized
