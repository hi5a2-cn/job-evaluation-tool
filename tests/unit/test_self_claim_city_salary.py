"""体检第 30、31 条：话术里「编造所在城市 / 期望薪资」的识别。

30：所在城市的陈述句式和识别 HR 提问用同一份城市名单（词表 + 本岗位城市），不再只认 19 个城市。
31：期望薪资的陈述必须是带单位的金额或 4 位以上数字，不把「1周内入职」「10人团队」当成薪资。
"""

import pytest

from jet.llm.assist import (
    expand_self_claim_patterns,
    is_city_question,
    matches_self_claim,
    read_self_fact_rules,
    rule_city_names,
    verify_and_filter_assist_response,
)

RULES = {r["name"]: r for r in read_self_fact_rules()}
CITY = RULES["所在城市"]
SALARY = RULES["期望薪资"]


def _city_claim(text: str, location_name: str = "") -> bool:
    return matches_self_claim(text, expand_self_claim_patterns(CITY, location_name))


def _salary_claim(text: str) -> bool:
    return matches_self_claim(text, expand_self_claim_patterns(SALARY))


def test_city_patterns_use_placeholder_not_hardcoded_list():
    assert all("{cities}" in p for p in CITY["self_claim_patterns"])
    expanded = expand_self_claim_patterns(CITY)
    assert all("{cities}" not in p for p in expanded)


@pytest.mark.parametrize("city", ["厦门", "福州", "无锡", "深圳", "宁波"])
def test_city_claim_covers_every_city_in_word_list(city: str):
    assert city in rule_city_names(CITY)
    assert _city_claim(f"我目前在{city}，可以随时面试")


def test_city_claim_includes_job_city_outside_word_list():
    assert "漳州" not in rule_city_names(CITY)
    assert _city_claim("我人在漳州", location_name="漳州市")
    assert not _city_claim("我人在漳州")


def test_city_question_and_claim_share_the_same_city_list():
    names = rule_city_names(CITY, location_name="漳州市")
    for city in names:
        assert is_city_question(f"你在{city}吗？", CITY, location_name="漳州市")
        assert _city_claim(f"我现在在{city}", location_name="漳州市")


def test_city_claim_ignores_sentences_without_a_city():
    assert not _city_claim("我在这边做了三年后端")
    assert not _city_claim("工作地点在成都我可以接受")


def test_non_city_words_are_not_treated_as_cities():
    names = rule_city_names(CITY)
    for word in ["常住", "现居", "居住在", "人在", "坐标", "所在地", "目前在"]:
        assert word not in names


@pytest.mark.parametrize(
    "text",
    [
        "我希望能在1周内入职",
        "我期望加入一个10人以上的团队",
        "我希望在3到5年内成为技术专家",
        "我希望2026年入职",
        "我想要在2个月内熟悉业务",
    ],
)
def test_ordinary_wishes_with_numbers_are_not_salary_claims(text: str):
    assert not _salary_claim(text)


@pytest.mark.parametrize(
    "text",
    [
        "我期望15000",
        "我期望月薪20k",
        "我希望薪资在1.5万左右",
        "我期望 8000 以上",
        "我想要18K",
        "我的期望薪资是18",
    ],
)
def test_salary_claims_still_recognized(text: str):
    assert _salary_claim(text)


def _filter(text: str, location_name: str = "") -> dict:
    raw = {
        "suggestions": [
            {"version": 1, "tone_desc": "自然直接", "text": text, "referenced_experience_ids": []},
        ],
        "questions": ["想了解下团队目前的规模？"],
    }
    return verify_and_filter_assist_response(
        raw_response=raw,
        valid_experience_ids=set(),
        sanitized_messages=[{"sender": "HR", "text": "您好，方便聊聊吗？"}],
        experiences=[],
        current_city="",
        location_name=location_name,
    )


def test_filter_drops_made_up_city_outside_old_list():
    result = _filter("我目前在厦门，可以随时面试", location_name="厦门")
    assert result["suggestions"] == []
    assert any("所在城市" in d["text"] for d in result["dropped_summary"])


def test_filter_keeps_ordinary_wish_without_salary_reason():
    result = _filter("我希望能在1周内入职，可以吗？")
    assert len(result["suggestions"]) == 1
    assert not any("期望薪资" in d["text"] for d in result["dropped_summary"])
