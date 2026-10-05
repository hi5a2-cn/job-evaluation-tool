"""体检第 32、33、34 条：话术里「我的事实 / 已完成操作」的识别，以及 HR「出差或外派」提问的识别。

32：省略主语、位于分句开头的陈述（「目前在深圳」「期望月薪15k」「随时可以到岗」「可以接受出差」）也算；
    分句是问句（含「吗」或问号）时不算，正常提问不受影响。
33：「已同意」「已添加微信」「已交换联系方式」这类声称已完成也要拦下。
34：HR 只是提到「驻外」不算在问出差或外派；真正的问句仍能识别。
"""

import pytest

from jet.llm.assist import (
    determine_unanswered_facts,
    expand_self_claim_patterns,
    matches_self_claim,
    read_completed_action_rules,
    read_self_fact_rules,
    verify_and_filter_assist_response,
)

FACTS = {r["name"]: r for r in read_self_fact_rules()}
DONE = {r["name"]: r for r in read_completed_action_rules()}


def _fact_claim(name: str, text: str) -> bool:
    return matches_self_claim(text, expand_self_claim_patterns(FACTS[name]))


@pytest.mark.parametrize(
    "name, text",
    [
        ("所在城市", "目前在深圳，随时可沟通"),
        ("所在城市", "您好，现在人在杭州"),
        ("期望薪资", "期望月薪15k"),
        ("期望薪资", "好的，期望薪资是20K"),
        ("期望薪资", "期望15000左右"),
        ("到岗时间", "随时可以到岗"),
        ("到岗时间", "您好，可以随时入职"),
        ("出差或外派", "可以接受出差"),
        ("出差或外派", "愿意外派"),
    ],
)
def test_subjectless_claims_at_clause_start_are_recognized(name: str, text: str):
    assert _fact_claim(name, text)


@pytest.mark.parametrize(
    "name, text",
    [
        ("所在城市", "贵公司在深圳吗"),
        ("所在城市", "目前团队在深圳"),
        ("所在城市", "目前在深圳有办公室吗？"),
        ("所在城市", "工作地点在成都我可以接受"),
        ("期望薪资", "期望1周内入职"),
        ("期望薪资", "期望薪资15k可以吗"),
        ("期望薪资", "期望能加入团队"),
        ("到岗时间", "可以随时到岗吗？"),
        ("到岗时间", "请问到岗时间有要求吗"),
        ("出差或外派", "能接受出差吗"),
        ("出差或外派", "请问需要出差吗"),
    ],
)
def test_questions_and_unrelated_sentences_are_not_claims(name: str, text: str):
    assert not _fact_claim(name, text)


@pytest.mark.parametrize(
    "text",
    ["好的，我已同意交换微信", "我已经同意了", "我已添加您的微信", "已加您好友", "已和您交换了联系方式"],
)
def test_agreed_or_added_claims_are_recognized(text: str):
    assert matches_self_claim(text, DONE["已同意或已添加"]["self_claim_patterns"])


@pytest.mark.parametrize(
    "text",
    ["好的，我同意交换微信", "好的，可以交换微信", "我已加入过类似团队", "我已经了解岗位要求"],
)
def test_present_agreement_and_unrelated_sentences_are_kept(text: str):
    assert not matches_self_claim(text, DONE["已同意或已添加"]["self_claim_patterns"])


def _filter(text: str) -> dict:
    raw = {
        "suggestions": [
            {"version": 1, "tone_desc": "自然直接", "text": text, "referenced_experience_ids": []},
        ],
        "questions": ["想了解下团队目前的规模？"],
    }
    return verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[{"sender": "HR", "text": "方便交换个微信吗？"}],
        experiences=[],
    )


def test_filter_drops_agreed_claim_with_reason():
    result = _filter("好的，我已同意交换微信，稍后联系")
    assert result["suggestions"] == []
    assert any("已同意或已添加" in d["text"] for d in result["dropped_summary"])


def test_filter_drops_subjectless_salary_claim():
    result = _filter("好的，期望月薪15k，随时可以沟通")
    assert result["suggestions"] == []
    assert any("期望薪资" in d["text"] for d in result["dropped_summary"])


def test_filter_keeps_present_agreement():
    result = _filter("好的，可以交换微信")
    assert len(result["suggestions"]) == 1


def _asked(hr_text: str) -> list[str]:
    facts = determine_unanswered_facts([{"sender": "HR", "text": hr_text}], experiences=[])
    return [f["name"] for f in facts]


@pytest.mark.parametrize("hr_text", ["这个岗位不需要驻外，主要在深圳办公", "表现好的后期可以驻外"])
def test_mentioning_zhuwai_is_not_a_question(hr_text: str):
    assert "驻外" not in FACTS["出差或外派"]["hr_question_keywords"]
    assert "出差或外派" not in _asked(hr_text)


@pytest.mark.parametrize("hr_text", ["这个岗位要驻外吗？", "你能否驻外？", "可以驻外吗", "是否愿意驻外吗", "接受驻外吗"])
def test_zhuwai_questions_still_recognized(hr_text: str):
    assert "出差或外派" in _asked(hr_text)
