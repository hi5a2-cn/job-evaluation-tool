"""Unit tests for request notice appended text and prompt instructions (T066, T067).

Specs:
- specs/003-hr-assistant/spec.md (FR-055, FR-064, SC-025)
- specs/003-hr-assistant/tasks.md (T066, T067)
"""

from jet.llm.assist import (
    assemble_assist_request,
    build_assist_preview,
    determine_request_notice,
)


def test_determine_request_notice_contains_appended_instruction():
    """T066: determine_request_notice 的提示末尾追加'话术按你同意来写，不同意的话请自己回复'。"""
    messages_after = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]

    notice = determine_request_notice(messages_after)
    assert notice is not None
    assert notice.endswith("话术按你同意来写，不同意的话请自己回复")
    assert notice == "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝。话术按你同意来写，不同意的话请自己回复"

    # 卡片在我最后一条消息之前 -> 返回 None
    messages_before = [
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
        {"sender": "我", "text": "好的，稍后发您。", "is_request_card": False},
    ]
    assert determine_request_notice(messages_before) is None


def test_prompt_contains_request_card_agreement_requirements():
    """T066: 提示词中对请求卡片的要求明确：话术按同意来写、只写将要做的动作、不得声称已完成。"""
    messages = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]

    assembled = assemble_assist_request(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="刘女士",
        user_name="张三",
    )

    prompt = assembled["prompt_text"]
    assert "按同意来写" in prompt
    assert "只说将要发简历" in prompt
    assert "不得声称已发送" in prompt


def test_preview_and_generate_fingerprint_consistent_with_request_card():
    """T066: 含有请求卡片时，预览与生成计算的哈希指纹仍保持完全一致。"""
    messages = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]

    assembled = assemble_assist_request(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="刘女士",
        user_name="张三",
    )

    preview = build_assist_preview(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="刘女士",
        user_name="张三",
    )

    assert preview["prompt_hash"] == assembled["prompt_hash"]
    assert preview["sanitized_prompt"] == assembled["prompt_text"]


def test_prompt_with_unhandled_request_card_instructions_and_fingerprint_match():
    """T074: 有未处理请求卡片时提示词含回应请求要求与示例、不含'不要主动提出'，预览与生成指纹一致。"""
    messages = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]

    assembled = assemble_assist_request(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="刘女士",
        user_name="张三",
    )
    prompt = assembled["prompt_text"]

    # 含回应请求要求与示例
    assert "严格回应HR请求" in prompt
    assert "按同意来写" in prompt
    assert "只说将要发简历" in prompt
    assert "不得声称已发送" in prompt
    assert "好的，我这就把简历发您" in prompt

    # 不含"不要主动提出"
    assert "不要主动提出" not in prompt

    # 预览与生成指纹一致
    preview = build_assist_preview(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="刘女士",
        user_name="张三",
    )
    assert preview["prompt_hash"] == assembled["prompt_hash"]
    assert preview["sanitized_prompt"] == assembled["prompt_text"]


def test_prompt_without_request_card_omits_response_requirements_and_fingerprint_match():
    """T074: 没有卡片时提示词不含回应请求要求与'我这就发您'示例、含'不要主动提出发简历、加微信、留电话'，指纹一致。"""
    messages = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "在招的，岗位是前端开发", "is_request_card": False},
    ]

    assembled = assemble_assist_request(
        job_title="前端开发",
        company_name="某互联网公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="王女士",
        user_name="李四",
    )
    prompt = assembled["prompt_text"]

    # 不含回应请求要求与示例
    assert "严格回应HR请求" not in prompt
    assert "话术按同意来写" not in prompt
    assert "我这就发您" not in prompt

    # 含"不要主动提出发简历、加微信、留电话"
    assert "不要主动提出发简历、加微信、留电话" in prompt
    assert "聊天里没有需要回应的请求，话术不要主动提出发简历、加微信、留电话这类动作。" in prompt

    # 预览与生成指纹一致
    preview = build_assist_preview(
        job_title="前端开发",
        company_name="某互联网公司",
        location_name="深圳",
        messages=messages,
        experiences=[],
        hr_name="王女士",
        user_name="李四",
    )
    assert preview["prompt_hash"] == assembled["prompt_hash"]
    assert preview["sanitized_prompt"] == assembled["prompt_text"]


def test_prompt_with_card_before_self_message_omits_response_requirements():
    """T074: 卡片在我最后一条消息之前时提示词不含回应请求要求与'我这就发您'示例、含'不要主动提出发简历、加微信、留电话'。"""
    messages = [
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
        {"sender": "我", "text": "好的，稍后发您。", "is_request_card": False},
        {"sender": "HR", "text": "期待您的简历！", "is_request_card": False},
    ]

    assembled = assemble_assist_request(
        job_title="后端开发",
        company_name="某电商公司",
        location_name="杭州",
        messages=messages,
        experiences=[],
        hr_name="赵先生",
        user_name="张三",
    )
    prompt = assembled["prompt_text"]

    # 不含回应请求要求与示例
    assert "严格回应HR请求" not in prompt
    assert "我这就发您" not in prompt

    # 含"不要主动提出发简历、加微信、留电话"
    assert "不要主动提出发简历、加微信、留电话" in prompt

    # 预览与生成指纹一致
    preview = build_assist_preview(
        job_title="后端开发",
        company_name="某电商公司",
        location_name="杭州",
        messages=messages,
        experiences=[],
        hr_name="赵先生",
        user_name="张三",
    )
    assert preview["prompt_hash"] == assembled["prompt_hash"]
    assert preview["sanitized_prompt"] == assembled["prompt_text"]


def test_prompt_contains_job_info_source_clarification():
    """T074: 提示词在【岗位信息】后包含'岗位以【岗位信息】为准'等说明，且预览与生成指纹一致。"""
    assembled = assemble_assist_request(
        job_title="测试工程师",
        company_name="某科技公司",
        location_name="深圳",
        messages=[],
        experiences=[],
        hr_name="孙女士",
        user_name="张三",
    )
    prompt = assembled["prompt_text"]

    assert "岗位以【岗位信息】为准" in prompt
    assert "岗位以【岗位信息】为准；聊天中出现的 HR 本人职务（如部门、经理、主管等）是 HR 的职务，不是这个岗位。" in prompt

    preview = build_assist_preview(
        job_title="测试工程师",
        company_name="某科技公司",
        location_name="深圳",
        messages=[],
        experiences=[],
        hr_name="孙女士",
        user_name="张三",
    )
    assert preview["prompt_hash"] == assembled["prompt_hash"]
    assert preview["sanitized_prompt"] == assembled["prompt_text"]
