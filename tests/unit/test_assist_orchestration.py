"""Unit tests for assist orchestration, prompt assembly, and response verification (T015).

Specs:
- specs/003-hr-assistant/spec.md (FR-011-FR-015, FR-018, FR-031, FR-032, FR-034, FR-035, SC-005, R1, R2)
- specs/003-hr-assistant/contracts/local-api.md (Section 1 & 5)
- specs/003-hr-assistant/tasks.md (T015, T016)
"""

import json
from pathlib import Path
import pytest

from jet.config import Settings
from jet.llm.assist import (
    ASSIST_PREVIEW_FIELDS,
    AllSuggestionsDroppedError,
    HashMismatchError,
    assemble_assist_request,
    build_assist_preview,
    build_assist_prompt,
    determine_communication_stage,
    determine_request_notice,
    determine_unanswered_facts,
    generate_assist_suggestions,
    normalize_question,
    read_tone_rules,
    verify_and_filter_assist_response,
)
from tests.conftest import FakeLlmHelper


# ---------------------------------------------------------------------------
# 1. Communication stage determination tests (FR-012)
# ---------------------------------------------------------------------------


def test_determine_stage_no_hr_messages_is_opening():
    """没有 HR 消息时，判定为开场白 opening (FR-012 第 1 条)。"""
    # 场景 A: 消息列表为空
    mode, label, basis = determine_communication_stage([])
    assert mode == "opening"
    assert label == "开场白"
    assert basis == "HR尚未回复"

    # 场景 B: 仅有我方自动招呼语或消息
    messages = [{"sender": "我", "text": "您好，请问岗位还在招聘吗？"}]
    mode, label, basis = determine_communication_stage(messages)
    assert mode == "opening"
    assert label == "开场白"
    assert basis == "我：您好，请问岗位还在招聘吗？"


def test_determine_stage_last_message_hr_is_reply():
    """最后一条是 HR 消息时，判定为建议回复 reply (FR-012 第 2 条)。"""
    messages = [
        {"sender": "我", "text": "您好，请问岗位还在招聘吗？"},
        {"sender": "HR", "text": "在招的，方便发份简历吗？"},
    ]
    mode, label, basis = determine_communication_stage(messages)
    assert mode == "reply"
    assert label == "建议回复"
    assert basis == "HR：在招的，方便发份简历吗？"


def test_determine_stage_last_message_self_after_hr_is_waiting_hr():
    """最后一条是我且之前 HR 说过话，判定为 waiting_hr (FR-012 第 3 条)。"""
    messages = [
        {"sender": "我", "text": "您好，请问岗位还在招聘吗？"},
        {"sender": "HR", "text": "在招的，方便发份简历吗？"},
        {"sender": "我", "text": "好的呀，简历已发您附件。"},
    ]
    mode, label, basis = determine_communication_stage(messages)
    assert mode == "waiting_hr"
    assert label == "正在等 HR 回复"
    assert basis == "我：好的呀，简历已发您附件。"


def test_determine_stage_force_mode_overrides():
    """手动 force_mode 覆盖自动判定结果 (FR-013)。"""
    # 原本是 reply，强制设为 opening
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "您好，在招的。"},
    ]
    mode, label, basis = determine_communication_stage(messages, force_mode="opening")
    assert mode == "opening"
    assert label == "开场白"
    assert basis == "HR：您好，在招的。"

    # 原本是 waiting_hr，强制设为 reply
    messages_waiting = [
        {"sender": "HR", "text": "在招的"},
        {"sender": "我", "text": "好的"},
    ]
    mode2, label2, _ = determine_communication_stage(messages_waiting, force_mode="reply")
    assert mode2 == "reply"
    assert label2 == "建议回复"


# ---------------------------------------------------------------------------
# 2. Prompt assembly tests (R1, FR-010, FR-011, FR-034, FR-035)
# ---------------------------------------------------------------------------


def test_build_prompt_includes_job_company_city():
    """提示词包含职位名、公司、城市 (R1 岗位字段)。"""
    prompt = build_assist_prompt(
        job_title="储能海外销售（驻尼日利亚等）",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=[],
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "职位：储能海外销售（驻尼日利亚等）" in prompt
    assert "公司：某新能源科技公司" in prompt
    assert "城市：深圳" in prompt


def test_build_prompt_with_jet_judgement_includes_r1_fields():
    """有 Jet 判断时包含 R1 列出的判断字段，不包含 JD 全文。"""
    judgement = {
        "verdict": "try",
        "summary": "西非市场海外销售，需驻外及渠道开拓",
        "sales_level": "高",
        "risk_signals": ["涉及长期驻外安全与补贴说明"],
        "hr_questions": ["驻外期间的安全保障及具体常驻津贴政策是怎样的？"],
    }
    prompt = build_assist_prompt(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=[],
        experiences=[],
        tone_rules="1. 语气规则",
        judgement=judgement,
    )
    assert "【Jet 判断结论与风险】" in prompt
    assert "结论：可以一试" in prompt or "try" in prompt
    assert "西非市场海外销售" in prompt
    assert "长期驻外安全与补贴说明" in prompt
    assert "常驻津贴政策" in prompt


def test_build_prompt_without_jet_judgement_annotates_missing_and_asks_position_nature():
    """无 Jet 判断时显眼标注无判断，并要求提问确认岗位性质 (FR-011)。"""
    prompt = build_assist_prompt(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=[],
        experiences=[],
        tone_rules="1. 语气规则",
        judgement=None,
    )
    assert "无 Jet 判断" in prompt
    assert "确认岗位性质" in prompt


def test_build_prompt_includes_hr_note_messages_and_numbered_experiences():
    """包含 HR 实际情况、脱敏后消息、带编号的经历素材 (R1)。"""
    messages = [
        {"sender": "我", "text": "您好，请问招人吗？"},
        {"sender": "HR", "text": "招的，方便发份简历吗？"},
    ]
    experiences = [
        {"item_no": 1, "content": "在新能源公司拓展西非逆变器渠道，达成季度翻倍。"},
        {"item_no": 2, "content": "带过 3 人海外客户成功团队，客户满意度提升 15%。"},
    ]
    prompt = build_assist_prompt(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=experiences,
        tone_rules="1. 规则",
        hr_note="HR 告知前半年深圳培训，后常驻西非办事处。",
    )
    assert "【HR 实际情况】" in prompt
    assert "HR 告知前半年深圳培训" in prompt

    assert "【聊天记录】" in prompt
    assert "我：您好，请问招人吗？" in prompt
    assert "HR：招的，方便发份简历吗？" in prompt

    assert "【经历素材】" in prompt
    assert "[1] 在新能源公司拓展西非逆变器渠道" in prompt
    assert "[2] 带过 3 人海外客户成功团队" in prompt


def test_build_prompt_reads_tone_rules_dynamically(tmp_path: Path):
    """语气规则每次动态从文件读取，修改文件后下次组装即生效 (FR-034)。"""
    rules_file = tmp_path / "custom_tone_rules.txt"
    rules_file.write_text("1. 第一条规则版本A\n", encoding="utf-8")

    # 1. 首次读取版本 A
    content1 = read_tone_rules(rules_file)
    assert "版本A" in content1

    # 2. 修改文件为版本 B
    rules_file.write_text("1. 第一条规则版本B（已更新）\n", encoding="utf-8")
    content2 = read_tone_rules(rules_file)
    assert "版本B" in content2
    assert "版本A" not in content2


# ---------------------------------------------------------------------------
# 3. Model return verification & filtering tests (FR-014, FR-015, FR-031, SC-005)
# ---------------------------------------------------------------------------


def test_verify_drops_suggestion_with_nonexistent_experience_id():
    """引用不存在经历编号的话术被丢弃 (FR-031)。"""
    valid_ids = {1}
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份，我有西非渠道经验。",
                "referenced_experience_ids": [1],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "简历已发您查收，我曾主导过欧洲大客户项目。",
                "referenced_experience_ids": [99],  # 非法编号 99
            },
        ],
        "questions": ["业务在尼日利亚具体开展情况如何？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=valid_ids,
        sanitized_messages=[],
    )
    # 版本 2 引用非法经历编号 99，被丢弃；保留版本 1
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["version"] == 1
    assert result["suggestions"][0]["referenced_experience_ids"] == [1]


def test_verify_raises_when_all_suggestions_dropped():
    """全部话术因引用非法编号被丢弃时，抛出 AllSuggestionsDroppedError (FR-031, R2)。"""
    valid_ids = {1}
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "我有非法经验 A。",
                "referenced_experience_ids": [8],  # 不存在
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "我有非法经验 B。",
                "referenced_experience_ids": [9],  # 不存在
            },
        ],
        "questions": [],
    }
    with pytest.raises(AllSuggestionsDroppedError) as exc_info:
        verify_and_filter_assist_response(
            raw_response=raw,
            valid_experience_ids=valid_ids,
            sanitized_messages=[],
        )
    assert "没有可用的建议" in str(exc_info.value)
    assert exc_info.value.error == "all_suggestions_dropped"


def test_verify_drops_suggestions_exceeding_50_chars():
    """超过 50 字的话术不显示，严格遵守 SC-005。"""
    valid_ids = {1}
    long_text = "这是一段非常非常长的话术，超过了整整五十个字的限制要求。" * 3
    assert len(long_text) > 50

    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份简历，您查收看看。",  # <= 50 字
                "referenced_experience_ids": [1],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": long_text,  # > 50 字
                "referenced_experience_ids": [1],
            },
        ],
        "questions": ["主要对接哪些客户？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=valid_ids,
        sanitized_messages=[],
    )
    # 超长的版本 2 被丢弃
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["text"] == "好的呀，我这就发您一份简历，您查收看看。"


def test_verify_limits_questions_to_three():
    """问题最多 3 个 (FR-014, SC-005)。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，简历已发。",
                "referenced_experience_ids": [],
            }
        ],
        "questions": ["问题一？", "问题二？", "问题三？", "问题四？", "问题五？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )
    assert len(result["questions"]) == 3
    assert result["questions"] == ["问题一？", "问题二？", "问题三？"]


def test_verify_removes_duplicate_questions_already_in_chat():
    """与已加载聊天中已问过的问题（规范化后相同）重复的问题被去掉 (FR-015)。"""
    messages = [
        {"sender": "我", "text": "请问平时主要对接的是海外代理商还是终端客户？"},
    ]
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，简历已发。",
                "referenced_experience_ids": [],
            }
        ],
        "questions": [
            # 文本与已问过的问题在规范化后相同（去除标点/空格等）
            "请问平时主要对接的是海外代理商还是终端客户",
            # 新问题
            "尼日利亚那边的业务目前是刚起步还是已有成熟渠道？",
        ],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=messages,
    )
    assert len(result["questions"]) == 1
    assert "尼日利亚那边的业务" in result["questions"][0]


def test_verify_waiting_hr_suggestions_empty_only_questions():
    """waiting_hr 模式下话术为空，只给出建议问 HR 的问题 (FR-012, FR-014)。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，请问业务进展？",
                "referenced_experience_ids": [],
            }
        ],
        "questions": ["想先确认一下该岗位主要负责对接哪些类型的客户？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
        mode="waiting_hr",
    )
    assert result["suggestions"] == []
    assert len(result["questions"]) == 1


def test_verify_no_experience_generates_two_versions_with_note():
    """没有经历可引用时仍保留 2 个版本并注明没有可引用的经历 (FR-014, FR-032)。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份简历，您查收看看。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，我这就发您一份简历，您查收看看。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["想确认一下团队目前的规模？"],
        "experience_note": "没有可引用的经历",
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )
    assert len(result["suggestions"]) == 2
    assert result["experience_note"] == "没有可引用的经历"


# ---------------------------------------------------------------------------
# 4. Orchestration & LLM calling parameters tests (FR-018)
# ---------------------------------------------------------------------------


def test_orchestration_calls_once_with_disabled_thinking(settings: Settings, fake_llm: FakeLlmHelper):
    """只调用一次、关闭思考模式 (FR-018)，使用 fake_llm 不联网。"""
    fake_llm.set_assist(
        suggestions=[
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份。想先确认一下业务情况。",
                "referenced_experience_ids": [1],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "嗯嗯好的，稍后发您简历。想确认一下对接的客户类型。",
                "referenced_experience_ids": [1],
            },
        ],
        questions=["平时主要对接海外代理商还是终端客户？"],
    )

    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "在招的，方便发份简历吗？"},
    ]
    experiences = [
        {"item_no": 1, "content": "西非市场渠道拓展经验"},
    ]

    preview = build_assist_preview(
        job_title="储能海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=experiences,
    )

    result = generate_assist_suggestions(
        settings=settings,
        job_title="储能海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=experiences,
        expected_hash=preview["prompt_hash"],
        transport=fake_llm.transport,
    )

    # 1. 验证只调用一次 (FR-018)
    assert fake_llm.call_count == 1
    assert len(fake_llm.requests) == 1

    # 2. 验证关闭思考模式 (FR-018)
    req_body = json.loads(fake_llm.requests[0].content.decode("utf-8"))
    assert req_body.get("thinking") == {"type": "disabled"}

    # 3. 验证返回结果结构
    assert result["mode"] == "reply"
    assert result["mode_label"] == "建议回复"
    assert len(result["suggestions"]) == 2
    assert len(result["questions"]) == 1
    assert result["suggestions"][0]["referenced_experience_ids"] == [1]


def test_build_assist_preview_structure():
    """验证预览结构符合 contracts/local-api.md 第 1 节。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "在招的，方便发份简历吗？"},
    ]
    experiences = [{"item_no": 1, "content": "经历内容"}]
    preview = build_assist_preview(
        job_title="储能海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=experiences,
    )
    assert preview["fields"] == ASSIST_PREVIEW_FIELDS
    assert "sanitized_prompt" in preview
    assert len(preview["prompt_hash"]) == 64  # SHA-256
    assert preview["mode"] == "reply"
    assert preview["message_count"] == 2
    assert preview["has_jet_judgement"] is False


def test_generate_assist_suggestions_expected_hash_validation(
    settings: Settings, fake_llm: FakeLlmHelper
):
    """expected_hash 必须传入；不传/传 None/传空字符串时抛错且 fake_llm 调用次数为 0。"""
    messages = [{"sender": "我", "text": "您好！"}]
    experiences = [{"item_no": 1, "content": "西非市场渠道拓展经验"}]

    # 1. 不传 expected_hash: 抛出 TypeError，fake_llm 未被调用
    with pytest.raises(TypeError):
        generate_assist_suggestions(  # type: ignore[call-arg]
            settings=settings,
            job_title="海外销售",
            company_name="测试公司",
            location_name="深圳",
            sanitized_messages=messages,
            experiences=experiences,
            transport=fake_llm.transport,
        )
    assert fake_llm.call_count == 0

    # 2. 传 None: 抛出 HashMismatchError，fake_llm 未被调用
    with pytest.raises(HashMismatchError) as exc_none:
        generate_assist_suggestions(
            settings=settings,
            job_title="海外销售",
            company_name="测试公司",
            location_name="深圳",
            sanitized_messages=messages,
            experiences=experiences,
            expected_hash=None,  # type: ignore[arg-type]
            transport=fake_llm.transport,
        )
    assert exc_none.value.error == "hash_mismatch"
    assert fake_llm.call_count == 0

    # 3. 传空字符串: 抛出 HashMismatchError，fake_llm 未被调用
    with pytest.raises(HashMismatchError) as exc_empty:
        generate_assist_suggestions(
            settings=settings,
            job_title="海外销售",
            company_name="测试公司",
            location_name="深圳",
            sanitized_messages=messages,
            experiences=experiences,
            expected_hash="",
            transport=fake_llm.transport,
        )
    assert exc_empty.value.error == "hash_mismatch"
    assert fake_llm.call_count == 0


def test_assemble_assist_request_truncation_and_hash():
    """assemble_assist_request 统一负责脱敏、截取最近 30 条、阶段判定、读取语气规则、组装提示词与计算指纹。"""
    messages = [
        {"sender": "我", "text": f"消息 {i}", "is_self": True, "type": 1, "body_type": 1}
        for i in range(35)
    ]
    experiences = [{"item_no": 1, "content": "我的经历素材"}]

    assembled = assemble_assist_request(
        job_title="储能海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        messages=messages,
        experiences=experiences,
        user_name="张三",
    )

    # 截取最近 30 条
    assert assembled["message_count"] == 30
    assert len(assembled["sanitized_messages"]) == 30
    assert assembled["sanitized_messages"][-1]["text"] == "消息 34"

    # 指纹长度为 64 (SHA-256)
    assert len(assembled["prompt_hash"]) == 64
    assert assembled["mode"] == "opening"
    assert assembled["fields"] == ASSIST_PREVIEW_FIELDS


# ---------------------------------------------------------------------------
# 5. FR-054 and FR-055 Self-fact and HR request card tests (2026-09-26)
# ---------------------------------------------------------------------------


def test_build_prompt_marks_hr_request_card_and_includes_fr054_fr055_rules():
    """提示词中请求卡片被标出【HR 请求卡片】，并包含 FR-054 和 FR-055 的要求。"""
    messages = [
        {"sender": "我", "text": "您好，想了解岗位信息。", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]
    prompt = build_assist_prompt(
        job_title="海外销售",
        company_name="某新能源科技公司",
        location_name="深圳",
        sanitized_messages=messages,
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "【HR 请求卡片】HR：我想要一份您的附件简历，您是否同意" in prompt
    assert "严格遵循真实性原则（FR-054）" in prompt
    assert "严格回应HR请求卡片：按同意来写" in prompt
    assert "只说将要发简历" in prompt


def test_determine_request_notice_positions():
    """FR-055: 请求卡片在我最后一条消息之后 -> 含卡片文字；之前 -> null；无卡片 -> null。"""
    # 场景 1: 请求卡片在我最后一条消息之后
    messages_after = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]
    notice_after = determine_request_notice(messages_after)
    assert notice_after == "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝。话术按你同意来写，不同意的话请自己回复"

    # 场景 2: 请求卡片在我最后一条消息之前
    messages_before = [
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
        {"sender": "我", "text": "好的，已同意并发送。", "is_request_card": False},
    ]
    notice_before = determine_request_notice(messages_before)
    assert notice_before is None

    # 场景 3: 没有请求卡片
    messages_none = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "在招的，方便发份简历吗？", "is_request_card": False},
    ]
    assert determine_request_notice(messages_none) is None


def test_determine_unanswered_facts():
    """FR-054: HR 问到经历素材未写明的事实时生成对应提示；已写明时不生成。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "你好，目前是在深圳吗？"},
    ]
    # 经历素材为空 -> 提示所在城市
    unanswered_empty = determine_unanswered_facts(messages, experiences=[])
    assert len(unanswered_empty) == 1
    assert unanswered_empty[0]["name"] == "所在城市"
    assert unanswered_empty[0]["notice"] == "HR 问了所在城市，你的资料里没有，这部分请你自己回答"

    # 经历素材写明"目前在深圳" -> 不生成未答提示
    experiences_with_city = [{"item_no": 1, "content": "目前在深圳，从事储能海外业务"}]
    unanswered_with_city = determine_unanswered_facts(messages, experiences=experiences_with_city)
    assert unanswered_with_city == []


def test_verify_drops_unsupported_city_claim_both_retains_questions_and_notice():
    """经历素材为空、HR 问'目前是在深圳吗'、两个版本都含'我目前在深圳' -> 两个都被丢弃，但有未答事实和问题，返回正常结构 (FR-054, R2)。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "目前是在深圳吗？"},
    ]
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以进一步沟通。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，我目前在深圳，方便了解一下具体职责吗？",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["想了解一下具体职责？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=messages,
        experiences=[],
    )
    assert result["suggestions"] == []
    assert result["questions"] == ["想了解一下具体职责？"]
    assert len(result["unanswered_facts"]) == 1
    assert result["unanswered_facts"][0]["name"] == "所在城市"
    assert result["dropped_summary"] == [
        {
            "reason": "unsupported_fact",
            "category": "所在城市",
            "count": 2,
            "text": "2 个版本因提到你资料里没有的'所在城市'被去掉",
        }
    ]


def test_verify_drops_one_unsupported_city_claim_and_retains_other():
    """经历素材为空，一个含'我目前在深圳'一个不含 -> 只留下不含的那个，unanswered_facts 含'所在城市'。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "目前是在深圳吗？"},
    ]
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以沟通。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，贵公司的职位要求我已了解，希望能进一步沟通。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["想了解一下具体职责？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=messages,
        experiences=[],
    )
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["text"] == "您好，贵公司的职位要求我已了解，希望能进一步沟通。"
    assert len(result["unanswered_facts"]) == 1
    assert result["unanswered_facts"][0] == {
        "name": "所在城市",
        "notice": "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
    }


def test_verify_retains_city_claim_when_specified_in_experience():
    """经历素材写明'目前在深圳'时，含'我目前在深圳'的话术保留，unanswered_facts 不含'所在城市'。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "目前是在深圳吗？"},
    ]
    experiences = [{"item_no": 1, "content": "目前在深圳，负责西非渠道开拓"}]
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我目前在深圳，随时可以沟通。",
                "referenced_experience_ids": [1],
            },
        ],
        "questions": ["想了解一下具体职责？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids={1},
        sanitized_messages=messages,
        experiences=experiences,
    )
    assert len(result["suggestions"]) == 1
    assert "我目前在深圳" in result["suggestions"][0]["text"]
    assert result["unanswered_facts"] == []


def test_verify_drops_unsupported_salary_claim():
    """经历素材未提及期望薪资时，含'我的期望薪资是15k'的话术被丢弃。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "您好，我的期望薪资是15k，可以进一步沟通。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，对贵公司岗位非常感兴趣，期待进一步交流。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": [],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
    )
    assert len(result["suggestions"]) == 1
    assert "期望薪资" not in result["suggestions"][0]["text"]


def test_verify_drops_unsupported_arrival_time_claim():
    """经历素材未提及到岗时间时，含'我随时可以到岗'的话术被丢弃。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "您好，我随时可以到岗，希望能加入贵公司。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，岗位职责已了解，期待与您详聊。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": [],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
    )
    assert len(result["suggestions"]) == 1
    assert "到岗" not in result["suggestions"][0]["text"]


def test_verify_drops_unsupported_travel_claim():
    """经历素材未提及出差时，含'我可以接受长期出差'的话术被丢弃。"""
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "您好，我可以接受长期出差，适应高强度工作。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，我的过往经验比较匹配，随时可沟通。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": [],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
    )
    assert len(result["suggestions"]) == 1
    assert "出差" not in result["suggestions"][0]["text"]


def test_verify_retains_legitimate_suggestions_when_experiences_empty():
    """FR-054 规则精化：经历素材为空时，包含正常经历陈述或确认提问的话术保留不被误伤。"""
    legitimate_texts = [
        "我在之前实习中负责过数据分析，想了解下这个岗位的具体工作",
        "想确认一下入职后主要负责哪些工作？",
        "想确认一下薪资4500是底薪还是含绩效？",
        "想确认一下这个岗位是否需要外派？",
    ]
    raw = {
        "suggestions": [
            {
                "version": idx + 1,
                "tone_desc": "自然直接",
                "text": text,
                "referenced_experience_ids": [],
            }
            for idx, text in enumerate(legitimate_texts)
        ],
        "questions": ["想了解一下具体工作内容？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
    )
    assert len(result["suggestions"]) == 4
    retained_texts = [s["text"] for s in result["suggestions"]]
    assert retained_texts == legitimate_texts


def test_determine_unanswered_facts_no_city_notice_for_general_hr_statement():
    """FR-054 规则精化：HR 发'我们目前在招运营岗位'等普通陈述时不得产生'所在城市'提示。"""
    messages = [
        {"sender": "我", "text": "您好！"},
        {"sender": "HR", "text": "我们目前在招运营岗位"},
    ]
    unanswered = determine_unanswered_facts(messages, experiences=[])
    city_notices = [f for f in unanswered if f.get("name") == "所在城市"]
    assert city_notices == []


def test_verify_drops_unsupported_fact_claims_when_experiences_empty():
    """FR-054: 经历素材为空时，未录入素材的第一人称事实陈述仍必须全部被丢弃。"""
    unsupported_texts = [
        "我目前在深圳",
        "我期望月薪8000",
        "我随时可以到岗",
        "我可以接受外派",
    ]
    for text in unsupported_texts:
        raw = {
            "suggestions": [
                {
                    "version": 1,
                    "tone_desc": "自然直接",
                    "text": text,
                    "referenced_experience_ids": [],
                }
            ],
            "questions": [],
        }
        with pytest.raises(AllSuggestionsDroppedError):
            verify_and_filter_assist_response(
                raw_response=raw,
                valid_experience_ids=set(),
                sanitized_messages=[],
                experiences=[],
            )


def test_verify_all_suggestions_dropped_retains_questions_and_dropped_summary():
    """话术全部被丢弃但有问题或提示时，不抛错，保留问题与 dropped_summary (FR-054, R2)。"""
    valid_ids = {1}
    raw = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "我有非法经验 A。",
                "referenced_experience_ids": [8],  # 不存在
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "我有非法经验 B。",
                "referenced_experience_ids": [9],  # 不存在
            },
        ],
        "questions": ["具体对接哪些客户？"],
    }
    result = verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=valid_ids,
        sanitized_messages=[],
    )
    assert result["suggestions"] == []
    assert result["questions"] == ["具体对接哪些客户？"]
    assert result["dropped_summary"] == [
        {
            "reason": "invalid_experience",
            "category": None,
            "count": 2,
            "text": "2 个版本因引用了不存在的经历条目被去掉",
        }
    ]


def test_build_prompt_includes_unanswered_facts_instruction_and_fingerprint_match():
    """提示词中明确列出未答事实类别并要求占位正面回应，预览与生成指纹完全一致 (FR-054)。"""
    messages = [
        {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
        {"sender": "HR", "text": "目前是在深圳吗？", "is_self": False, "type": 1, "body_type": 1},
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
    prompt_text = assembled["prompt_text"]
    assert "所在城市" in prompt_text
    assert "用【填写：类别名】占位，不得绕开" in prompt_text
    assert "本次 HR 问到你资料里没有的事实（所在城市），话术必须正面回应，用【填写：类别名】占位，不得绕开。" in prompt_text
    assert "【填写：所在城市】" in prompt_text

    # 预览与生成共用 assemble_assist_request，哈希指纹完全一致
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


def test_build_prompt_includes_job_info_source_clarification():
    """T074: 提示词包含'岗位以【岗位信息】为准；聊天中出现的 HR 本人职务（如部门、经理、主管等）是 HR 的职务，不是这个岗位。'。"""
    prompt = build_assist_prompt(
        job_title="产品经理",
        company_name="某科技公司",
        location_name="北京",
        sanitized_messages=[],
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "岗位以【岗位信息】为准" in prompt
    assert "岗位以【岗位信息】为准；聊天中出现的 HR 本人职务（如部门、经理、主管等）是 HR 的职务，不是这个岗位。" in prompt


def test_build_prompt_conditional_request_card_rules():
    """T074: build_assist_prompt 根据 determine_request_notice 条件加入回应请求或禁止主动发简历要求。"""
    # 场景 1: 有未处理请求卡片
    messages_with_unhandled_card = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
    ]
    prompt_unhandled = build_assist_prompt(
        job_title="产品经理",
        company_name="某科技公司",
        location_name="北京",
        sanitized_messages=messages_with_unhandled_card,
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "严格回应HR请求" in prompt_unhandled
    assert "按同意来写" in prompt_unhandled
    assert "只说将要发简历" in prompt_unhandled
    assert "不得声称已发送" in prompt_unhandled
    assert "好的，我这就把简历发您" in prompt_unhandled
    assert "不要主动提出" not in prompt_unhandled

    # 场景 2: 没有请求卡片
    messages_no_card = [
        {"sender": "我", "text": "您好！", "is_request_card": False},
        {"sender": "HR", "text": "您好，在招的。", "is_request_card": False},
    ]
    prompt_no_card = build_assist_prompt(
        job_title="产品经理",
        company_name="某科技公司",
        location_name="北京",
        sanitized_messages=messages_no_card,
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "严格回应HR请求" not in prompt_no_card
    assert "我这就发您" not in prompt_no_card
    assert "不要主动提出发简历、加微信、留电话" in prompt_no_card
    assert "聊天里没有需要回应的请求，话术不要主动提出发简历、加微信、留电话这类动作。" in prompt_no_card

    # 场景 3: 请求卡片在我最后一条消息之前
    messages_card_before_self = [
        {"sender": "HR", "text": "我想要一份您的附件简历，您是否同意", "is_request_card": True},
        {"sender": "我", "text": "好的，发您了。", "is_request_card": False},
        {"sender": "HR", "text": "收到，谢谢！", "is_request_card": False},
    ]
    prompt_card_before = build_assist_prompt(
        job_title="产品经理",
        company_name="某科技公司",
        location_name="北京",
        sanitized_messages=messages_card_before_self,
        experiences=[],
        tone_rules="1. 语气规则",
    )
    assert "严格回应HR请求" not in prompt_card_before
    assert "我这就发您" not in prompt_card_before
    assert "不要主动提出发简历、加微信、留电话" in prompt_card_before


def test_build_prompt_char_limit_rule_first_and_known_facts_json_format():
    """(a) & (b): 字数限制规则前置为第一条，提问前加入 known_facts 要求，JSON 格式最前加 known_facts 且标明标点也算。"""
    prompt = build_assist_prompt(
        job_title="产品经理",
        company_name="某科技公司",
        location_name="北京",
        sanitized_messages=[
            {"sender": "我", "text": "您好！"},
            {"sender": "HR", "text": "您好，在招的。"},
        ],
        experiences=[{"item_no": 1, "content": "我的经历素材"}],
        tone_rules="1. 称呼用您",
    )

    # 1. 字数规则前置：每条话术字数必须在 50 字以内 为生成要求的第一条规则
    assert "- 每条话术字数必须在 50 字以内（严格限制 ≤ 50 字）。" in prompt
    assert "- 请生成 2 个不同语气版本的话术" in prompt
    pos_50_limit = prompt.find("- 每条话术字数必须在 50 字以内（严格限制 ≤ 50 字）。")
    pos_versions = prompt.find("- 请生成 2 个不同语气版本的话术")
    pos_tone_rule = prompt.find("- 严格遵循上述语气规则")
    assert pos_50_limit < pos_versions < pos_tone_rule

    # 2. known_facts 提示词规则放在提问要求附近（在提问要求之前）
    known_facts_rule = "- 先列出 HR 在聊天中已经明确的信息（known_facts，每条 ≤ 20 字，最多 8 条），再写话术和问题；已列入的内容不得再问，也不得换个说法再问。"
    question_rule = "- 提出最多 3 个建议问 HR 的问题。"
    assert known_facts_rule in prompt
    assert question_rule in prompt
    pos_kf_rule = prompt.find(known_facts_rule)
    pos_q_rule = prompt.find(question_rule)
    assert pos_kf_rule < pos_q_rule

    # 3. 输出格式最前面包含 known_facts
    kf_json_str = '"known_facts": [\n    "HR 在聊天中已经明确的信息（每条 ≤ 20 字）"\n  ],'
    assert kf_json_str in prompt
    pos_kf_json = prompt.find(kf_json_str)
    pos_sug_json = prompt.find('"suggestions": [')
    assert pos_kf_json < pos_sug_json

    # 4. 输出格式中两处话术文本标明（≤ 50 字，标点也算）
    target_text_desc = '"text": "话术文本（≤ 50 字，标点也算）"'
    assert prompt.count(target_text_desc) == 2
    # 旧写法不再出现
    assert '"text": "话术文本（≤ 50 字）"' not in prompt


def test_verify_and_filter_known_facts_excluded():
    """(a): 模型输出含 known_facts 时，返回给插件的核验结果不含 known_facts 字段也不含其中任何文字。"""
    raw_response = {
        "known_facts": [
            "工作地点在上海徐汇区",
            "每周需要现场办公4天",
        ],
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "好的呀，我这就发您一份。",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "您好，简历稍后通过附件发您。",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["想了解一下技术团队规模？"],
    }

    result = verify_and_filter_assist_response(
        raw_response=raw_response,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )

    # 验证响应字典不含 known_facts 字段
    assert "known_facts" not in result

    # 验证结果转字符串后不包含 known_facts 中的任何文字
    res_str = json.dumps(result, ensure_ascii=False)
    assert "工作地点在上海徐汇区" not in res_str
    assert "每周需要现场办公4天" not in res_str
    assert "known_facts" not in res_str
