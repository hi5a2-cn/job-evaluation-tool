"""Unit tests for completed action rules and response filtering (T064, T065).

Specs:
- specs/003-hr-assistant/spec.md (FR-064, SC-025)
- specs/003-hr-assistant/tasks.md (T064, T065)
"""

from pathlib import Path
import pytest

from jet.llm.assist import (
    DEFAULT_COMPLETED_ACTION_RULES_PATH,
    read_completed_action_rules,
    verify_and_filter_assist_response,
)


def test_completed_action_rules_file_exists_and_loads():
    """T064: 规则文件 completed_action_rules.json 存在且能够正常读取。"""
    assert DEFAULT_COMPLETED_ACTION_RULES_PATH.exists()
    rules = read_completed_action_rules()
    assert isinstance(rules, list)
    assert len(rules) >= 1
    rule_names = [r.get("name") for r in rules]
    assert "简历已发送" in rule_names


def test_must_drop_claims_done_suggestions():
    """T064: 必须丢弃声称已完成操作的话术，并在 dropped_summary 中记录 reason='claims_done'。

    必须丢弃样本：
    - "简历已同意发送"
    - "我已经把简历发给您了"
    - "简历已发送，请查收"
    以及 "已通过附件发您"、"简历已上传"、"简历已投递"。
    """
    drop_texts = [
        "简历已同意发送",
        "我已经把简历发给您了",
        "简历已发送，请查收",
        "已通过附件发您",
        "简历已上传",
        "简历已投递",
        "我已经发您了",
        "简历刚刚发过去了",
    ]

    for text in drop_texts:
        raw_response = {
            "suggestions": [
                {
                    "version": 1,
                    "tone_desc": "自然直接",
                    "text": text,
                    "referenced_experience_ids": [],
                }
            ],
            "questions": ["想了解一下具体工作内容？"],
        }

        result = verify_and_filter_assist_response(
            raw_response=raw_response,
            valid_experience_ids=set(),
            sanitized_messages=[],
            experiences=[],
        )

        assert result["suggestions"] == [], f"话术 '{text}' 未被丢弃"
        assert len(result["questions"]) == 1

        claims_done_summary = [d for d in result["dropped_summary"] if d.get("reason") == "claims_done"]
        assert len(claims_done_summary) == 1, f"话术 '{text}' 未在 dropped_summary 中产生 claims_done 记录"
        summary_entry = claims_done_summary[0]
        assert summary_entry["category"] == "简历已发送"
        assert summary_entry["count"] == 1
        assert "因声称'简历已发送'这类还没做的操作被去掉" in summary_entry["text"]
        assert text not in summary_entry["text"]


def test_must_retain_future_action_or_inquiry_suggestions():
    """T064: 必须保留将要执行动作或询问的话术。

    必须保留样本：
    - "好的，我这就发您"
    - "嗯嗯，同意，稍后发您简历"
    - "想确认一下简历发送到哪个邮箱？"
    - "好的，收到"
    """
    retain_texts = [
        "好的，我这就发您",
        "嗯嗯，同意，稍后发您简历",
        "想确认一下简历发送到哪个邮箱？",
        "好的，收到",
        "我已发现这个岗位和我的方向很契合",
        "我已经发现这个方向很适合我",
        "我刚刚发布了作品集链接的更新想确认一下是否方便查看",
    ]

    raw_response = {
        "suggestions": [
            {
                "version": idx + 1,
                "tone_desc": "自然直接",
                "text": text,
                "referenced_experience_ids": [],
            }
            for idx, text in enumerate(retain_texts)
        ],
        "questions": ["想了解一下具体职责？"],
    }

    result = verify_and_filter_assist_response(
        raw_response=raw_response,
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
    )

    result_texts = [s["text"] for s in result["suggestions"]]
    assert result_texts == retain_texts
    claims_done_summary = [d for d in result["dropped_summary"] if d.get("reason") == "claims_done"]
    assert claims_done_summary == []


def test_must_drop_additional_claims_done_suggestions():
    """T064: 必须丢弃新增声称已完成操作的话术样本。"""
    additional_drop_texts = [
        "我已经发您了",
        "简历刚刚发过去了",
    ]

    for text in additional_drop_texts:
        raw_response = {
            "suggestions": [
                {
                    "version": 1,
                    "tone_desc": "自然直接",
                    "text": text,
                    "referenced_experience_ids": [],
                }
            ],
            "questions": ["想了解一下具体工作内容？"],
        }

        result = verify_and_filter_assist_response(
            raw_response=raw_response,
            valid_experience_ids=set(),
            sanitized_messages=[],
            experiences=[],
        )

        assert result["suggestions"] == [], f"话术 '{text}' 未被丢弃"
        assert len(result["questions"]) == 1

        claims_done_summary = [d for d in result["dropped_summary"] if d.get("reason") == "claims_done"]
        assert len(claims_done_summary) == 1, f"话术 '{text}' 未在 dropped_summary 中产生 claims_done 记录"
        summary_entry = claims_done_summary[0]
        assert summary_entry["category"] == "简历已发送"
        assert summary_entry["count"] == 1
        assert "因声称'简历已发送'这类还没做的操作被去掉" in summary_entry["text"]
        assert text not in summary_entry["text"]


def test_rules_path_name_no_longer_picks_completed_action_rules(tmp_path: Path):
    """体检第 84 条：rules_path 只是自述事实规则；路径名里碰巧含 completed_action 时，
    不再被拿去当「已完成操作」规则，默认规则照常丢弃声称已发简历的话术。"""
    from jet.llm.assist import DEFAULT_SELF_FACT_RULES_PATH

    self_fact_copy = tmp_path / "completed_action_named_self_fact_rules.json"
    self_fact_copy.write_text(DEFAULT_SELF_FACT_RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    result = verify_and_filter_assist_response(
        raw_response={
            "suggestions": [
                {"version": 1, "tone_desc": "自然直接", "text": "我已经把简历发给您了", "referenced_experience_ids": []}
            ],
            "questions": ["想了解一下具体工作内容？"],
        },
        valid_experience_ids=set(),
        sanitized_messages=[],
        experiences=[],
        rules_path=self_fact_copy,
    )

    assert result["suggestions"] == []
    assert [d["category"] for d in result["dropped_summary"] if d.get("reason") == "claims_done"] == ["简历已发送"]
