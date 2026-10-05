import pytest

from jet.llm.assist import apply_tone_fix


def test_tone_fix_bridge_word_at_start_not_rewritten():
    """当开头的口语词本身就在承接词（bridge_words）中时，不进行二次改写，原样返回。"""
    # 1. "了解了，想确认下这个岗位需要加班吗？" 原样返回，不应被改写为 "了解了，了解了。那..."
    t1 = "了解了，想确认下这个岗位需要加班吗？"
    assert apply_tone_fix(t1) == t1

    # 2. "明白了，请问…？" 原样返回
    t2 = "明白了，请问这个岗位的核心业务是什么？"
    assert apply_tone_fix(t2) == t2


def test_tone_fix_regular_colloquial_start_still_rewritten():
    """普通的口语词（非承接词）后直接提问仍按原有逻辑插入承接词。"""
    # "好的，想确认下这个岗位需要加班吗？" 仍按原逻辑插入承接词
    t3 = "好的，想确认下这个岗位需要加班吗？"
    expected = "好的，了解了。那想确认下这个岗位需要加班吗？"
    assert apply_tone_fix(t3) == expected
