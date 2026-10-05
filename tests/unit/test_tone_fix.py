"""Unit tests for tone fix rules and rewrite logic (SC-013).

Spec: specs/003-hr-assistant/spec.md (SC-005, SC-013)
"""

from pathlib import Path
import pytest

from jet.llm.assist import (
    apply_tone_fix,
    read_tone_fix_rules,
    verify_and_filter_assist_response,
)


def test_tone_fix_must_rewrite():
    """必须改写的话术测试：

    - '嗯嗯，想确认下这个岗位有销售指标或业绩考核吗？' -> '嗯嗯，了解了。那想确认下这个岗位有销售指标或业绩考核吗？'
    - '好的，请问试用期多久？' -> '好的，了解了。那请问试用期多久？'
    """
    input1 = "嗯嗯，想确认下这个岗位有销售指标或业绩考核吗？"
    expected1 = "嗯嗯，了解了。那想确认下这个岗位有销售指标或业绩考核吗？"
    assert apply_tone_fix(input1) == expected1

    input2 = "好的，请问试用期多久？"
    expected2 = "好的，了解了。那请问试用期多久？"
    assert apply_tone_fix(input2) == expected2


def test_tone_fix_must_keep():
    """必须保留（不改）的话术测试：

    - '嗯嗯，了解了。那想确认下这个岗位有销售指标吗？'（已有承接）
    - '好的，我这就发您'（非提问）
    - '嗯嗯，明白了，谢谢您'（已有承接且非提问）
    - '想确认一下工作地点在哪里？'（不以口语词开头）
    """
    # 1. 已有承接词"了解了"
    t1 = "嗯嗯，了解了。那想确认下这个岗位有销售指标吗？"
    assert apply_tone_fix(t1) == t1

    # 2. 正常陈述动作，不是提问
    t2 = "好的，我这就发您"
    assert apply_tone_fix(t2) == t2

    # 3. 礼貌答谢，不是提问，且含承接"明白了"
    t3 = "嗯嗯，明白了，谢谢您"
    assert apply_tone_fix(t3) == t3

    # 4. 不以口语词开头
    t4 = "想确认一下工作地点在哪里？"
    assert apply_tone_fix(t4) == t4


def test_tone_fix_in_verify_and_filter_response():
    """在核验大模型返回结果时，符合条件的话术被自动改写。"""
    raw_response = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": "嗯嗯，想确认下这个岗位有销售指标或业绩考核吗？",
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "好的，我这就发您",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["请问试用期多久？"],
    }

    result = verify_and_filter_assist_response(
        raw_response=raw_response,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )

    # 版本 1 自动改写，插入承接
    assert (
        result["suggestions"][0]["text"]
        == "嗯嗯，了解了。那想确认下这个岗位有销售指标或业绩考核吗？"
    )
    # 版本 2 保持原样
    assert result["suggestions"][1]["text"] == "好的，我这就发您"


def test_tone_fix_exceeding_50_chars_dropped_and_recorded():
    """FR-069: 改写后若超过 50 字不改写保留原文；只有原文超过 50 字才去掉，原因为模型原文超过 50 字。"""
    # 构造原文本 48 字，改写加入"了解了。那"（5 字）后变为 53 字（> 50）
    base_q = "想确认下这个岗位在海外常驻期间是否有关于人身安全保障以及家属随行等具体的相关规定与政策吗？"
    raw_text_48 = f"嗯嗯，{base_q}"
    assert len(raw_text_48) == 48
    assert apply_tone_fix(raw_text_48) == raw_text_48  # 改写后超 50 字，返回原文

    # 构造原文本 51 字
    raw_text_51 = f"嗯嗯，{base_q}对吧？"
    assert len(raw_text_51) == 51

    raw_response = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": raw_text_48,
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "好的，我这就发您",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["请问试用期具体考核指标？"],
    }

    result = verify_and_filter_assist_response(
        raw_response=raw_response,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )

    # 原文 48 字、改写后 53 字 -> 返回原文、不去掉、不计入 dropped_summary
    assert len(result["suggestions"]) == 2
    assert result["suggestions"][0]["text"] == raw_text_48
    assert result["suggestions"][1]["text"] == "好的，我这就发您"
    assert result["dropped_summary"] == []

    # 原文 51 字 -> 去掉，原因为"模型原文超过 50 字"
    raw_response_51 = {
        "suggestions": [
            {
                "version": 1,
                "tone_desc": "自然直接",
                "text": raw_text_51,
                "referenced_experience_ids": [],
            },
            {
                "version": 2,
                "tone_desc": "沉稳专业",
                "text": "好的，我这就发您",
                "referenced_experience_ids": [],
            },
        ],
        "questions": ["请问试用期具体考核指标？"],
    }
    result_51 = verify_and_filter_assist_response(
        raw_response=raw_response_51,
        valid_experience_ids=set(),
        sanitized_messages=[],
    )
    assert len(result_51["suggestions"]) == 1
    assert result_51["suggestions"][0]["text"] == "好的，我这就发您"
    reasons = [d["reason"] for d in result_51["dropped_summary"]]
    assert "too_long" in reasons
    too_long_summary = next(d for d in result_51["dropped_summary"] if d["reason"] == "too_long")
    assert too_long_summary["count"] == 1
    assert too_long_summary["text"] == "1 个版本因原文超过 50 字被去掉"


def test_tone_fix_rules_dynamic_reading(tmp_path: Path):
    """tone_fix_rules.json 动态实时读取，修改即时生效。"""
    custom_rules_path = tmp_path / "custom_tone_fix_rules.json"
    custom_rules_path.write_text(
        """{
            "colloquial_words": ["哈喽"],
            "bridge_words": ["已收到"],
            "question_features": ["咨询一下"],
            "question_endings": ["吗"],
            "insert_bridge": "收到。那"
        }""",
        encoding="utf-8",
    )

    rules = read_tone_fix_rules(custom_rules_path)
    assert rules["insert_bridge"] == "收到。那"

    # 测试使用自定义规则改写
    res = apply_tone_fix("哈喽，咨询一下岗位的待遇吗", rules=rules)
    assert res == "哈喽，收到。那咨询一下岗位的待遇吗"

    # 包含自定义承接词"已收到"时不改写
    res_keep = apply_tone_fix("哈喽，已收到，咨询一下岗位的待遇吗", rules=rules)
    assert res_keep == "哈喽，已收到，咨询一下岗位的待遇吗"
