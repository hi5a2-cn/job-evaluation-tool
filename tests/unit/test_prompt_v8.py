"""v8 提示词与销售成分从严规则测试。

覆盖：
1. v8 PROMPT_VERSION 标识；
2. 系统提示词含规则 15 全文（逐字匹配）；
3. 规则 15 文本出现且位置正确：
   - 无规则 13 时：12 -> 14 -> 15；
   - 有规则 13 时：12 -> 13 -> 14 -> 15；
   - 有简历说明时：14 -> 15 -> 简历说明；
4. v4/v5/v6/v7 的 build_messages 输出与提示词文本不变（不含规则 15）；
5. 代码兜底：
   - sales_level 为"高"且 verdict != "skip"（如 try / check / apply）：
     verdict 转为 skip，derivation 最前面插入"销售与客户对接成分为高，结论改为不建议投"，
     derivation 最多截断至 5 条，verdict_reason 改为"销售与客户对接成分高，与不接受销售冲突"，
     hr_questions 清空为 []，resume_suggestion 保持原样；
   - sales_level 为"中"或"低"时不触发兜底；
   - 已是 skip 不重复插入兜底推导，不覆盖已有 verdict_reason；
   - 先后顺序：v7 的 skip→check 兜底先执行，随后 v8 的高→skip 兜底执行（sales 高 + 理由含"需确认"最终仍为 skip）；
6. dispatch 与配置：JET_PROMPT_VERSION=v8 时配置可用、分派到 v8 并由 worker 正常保存至数据库。
"""

import dataclasses
import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings, load_settings
from jet.db.store import open_db, utc_now
from jet.domain.judgements import is_method_changed, to_api
from jet.domain.profiles import save_profile
from jet.domain.resume import save_resumes
from jet.llm import prompt_v4, prompt_v5, prompt_v6, prompt_v7, prompt_v8
from jet.llm.client import run_llm_judgement
from jet.worker import JudgementWorker


def test_v8_prompt_version():
    assert prompt_v8.PROMPT_VERSION == "v8"


def test_v8_system_prompt_contains_rule_15_full_text():
    """验证 v8 系统提示词包含规则 15 全文（逐字匹配）。"""
    sys_prompt = prompt_v8.SYSTEM_PROMPT
    assert "15. 销售成分从严：" in sys_prompt
    assert '职责中单独成条地写了对接外部客户、渠道或合作机构（如"对接银行""对接商家""对接渠道商""维护客户关系"），而不是"配合""协助"他人对接的，算主要职责，sales_level 判"高"。' in sys_prompt
    assert '策划、统筹面向客户或渠道的营销产品与营销活动（如"策划面向全国银行的营销活动"），算"营销推广或活动策划"信号，必须摘出原句。' in sys_prompt
    assert 'sales_level 为"高"时，verdict 必须为 skip，verdict_reason 写明含销售或客户对接。' in sys_prompt
    assert '若有规则 13 的重点排查行业，且公司或岗位属于其中之一、sales_level 为"中"时，verdict 也必须为 skip，并在 derivation 中写明"属于重点排查行业（某行业），含销售成分，判不建议投"。' in sys_prompt
    assert '本条优先于规则 14：经验门槛再低，也不能把含销售的岗位判 try 或 check。' in sys_prompt


def test_v8_messages_order_no_strict_industry():
    """无重点排查行业时，规则 15 接在规则 14 后面（12 -> 14 -> 15）。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }

    msgs = prompt_v8.build_messages(profile, job_version, strict_industries=[])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业" not in sys_content
    assert "14. 经验门槛与结论：" in sys_content
    assert "15. 销售成分从严：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    idx_15 = sys_content.index("15. 销售成分从严：")
    assert idx_12 < idx_14 < idx_15


def test_v8_messages_order_with_strict_industry():
    """有重点排查行业时，顺序为 12 -> 13 -> 14 -> 15。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }

    msgs = prompt_v8.build_messages(profile, job_version, strict_industries=["餐饮"])
    sys_content = msgs[0]["content"]

    assert "12. 城市与薪资说明：" in sys_content
    assert "13. 重点排查行业（餐饮）：" in sys_content
    assert "14. 经验门槛与结论：" in sys_content
    assert "15. 销售成分从严：" in sys_content

    idx_12 = sys_content.index("12. 城市与薪资说明：")
    idx_13 = sys_content.index("13. 重点排查行业（餐饮）：")
    idx_14 = sys_content.index("14. 经验门槛与结论：")
    idx_15 = sys_content.index("15. 销售成分从严：")
    assert idx_12 < idx_13 < idx_14 < idx_15


def test_v8_messages_order_with_resumes():
    """有 2 份以上简历时，规则 15 位于规则 14 之后、简历说明段之前。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
        "min_monthly_k": 20.0,
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发，双休",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端开发"},
        {"slot": 2, "name": "简历2", "profile": "数据开发与架构"},
    ]

    msgs = prompt_v8.build_messages(profile, job_version, resumes=resumes)
    sys_content = msgs[0]["content"]

    assert "14. 经验门槛与结论：" in sys_content
    assert "15. 销售成分从严：" in sys_content
    assert "14. 简历建议：" in sys_content

    idx_14 = sys_content.index("14. 经验门槛与结论：")
    idx_15 = sys_content.index("15. 销售成分从严：")
    idx_resume = sys_content.index("14. 简历建议：")
    assert idx_14 < idx_15 < idx_resume


def test_v7_and_earlier_prompts_unaffected():
    """v4/v5/v6/v7 的提示词文本与 build_messages 均不受 v8 影响（不含规则 15）。"""
    profile = {
        "directions": ["Python开发"],
        "keywords": ["FastAPI"],
        "preferred_cities": ["深圳"],
    }
    job_version = {
        "title": "后端开发工程师",
        "city": "深圳",
        "description": "负责系统研发",
    }
    resumes = [
        {"slot": 1, "name": "简历1", "profile": "Python 后端开发"},
        {"slot": 2, "name": "简历2", "profile": "数据开发"},
    ]

    assert "15. 销售成分从严" not in prompt_v4.SYSTEM_PROMPT
    assert "15. 销售成分从严" not in prompt_v5.SYSTEM_PROMPT
    assert "15. 销售成分从严" not in prompt_v6.SYSTEM_PROMPT
    assert "15. 销售成分从严" not in prompt_v7.SYSTEM_PROMPT

    assert "15. 销售成分从严" not in prompt_v4.build_messages(profile, job_version)[0]["content"]
    assert "15. 销售成分从严" not in prompt_v5.build_messages(profile, job_version)[0]["content"]
    assert "15. 销售成分从严" not in prompt_v6.build_messages(profile, job_version, resumes=resumes)[0]["content"]
    assert "15. 销售成分从严" not in prompt_v7.build_messages(profile, job_version, resumes=resumes)[0]["content"]


def _make_v8_json(
    verdict: str = "try",
    sales_level: str = "高",
    verdict_reason: str = "经验门槛低可以一试",
    derivation: list[str] | None = None,
    hr_questions: list[str] | None = None,
    slot: int | None = 1,
    reason: str | None = "最匹配开发背景",
) -> str:
    data = {
        "facts": {
            "summary": {"text": "银行合作产品运营助理", "quotes": ["对接银行"]},
            "work_type": {"value": "运营", "subtype": "产品运营", "secondary": [], "quotes": ["产品运营"]},
            "sales_level": {"value": sales_level, "signals": [{"signal": "对接外部渠道", "quote": "对接银行"}]},
            "experience": {"requirement": "经验不限", "requirement_type": "未提及", "value": "满足", "gap": ""},
            "work_intensity": {"value": "双休", "quotes": []},
            "risk_signals": [],
        },
        "verdict": verdict,
        "derivation": derivation if derivation is not None else ["经验门槛不限，适合应届投递"],
        "verdict_reason": verdict_reason,
        "hr_questions": hr_questions if hr_questions is not None else ["确认日常工作内容"],
    }
    if slot is not None and reason is not None:
        data["resume_suggestion"] = {"slot": slot, "reason": reason}
    return json.dumps(data, ensure_ascii=False)


def test_v8_parse_facts_sales_high_fallback():
    """sales_level 为高且结论不为 skip 时触发兜底：
    - verdict 改为 skip；
    - derivation 最前面插入"销售与客户对接成分为高，结论改为不建议投"；
    - verdict_reason 改为"销售与客户对接成分高，与不接受销售冲突"；
    - hr_questions 置为 []；
    - resume_suggestion 保留。
    """
    for non_skip in ("try", "check", "apply"):
        raw_json = _make_v8_json(
            verdict=non_skip,
            sales_level="高",
            verdict_reason="虽然门槛低，但方向大体可以一试",
            derivation=["推导原因1", "推导原因2"],
            hr_questions=["问题1"],
            slot=1,
            reason="推荐简历1",
        )
        facts, verdict, derivation, verdict_reason, hr_questions, resume_suggestion = prompt_v8.parse_facts(
            raw_json, valid_slots={1, 2}
        )

        assert verdict == "skip"
        assert derivation[0] == "销售与客户对接成分为高，结论改为不建议投"
        assert derivation[1:] == ["推导原因1", "推导原因2"]
        assert len(derivation) == 3
        assert verdict_reason == "销售与客户对接成分高，与不接受销售冲突"
        assert hr_questions == []
        assert resume_suggestion == {"slot": 1, "reason": "推荐简历1"}


def test_v8_parse_facts_derivation_truncation():
    """兜底插入后 derivation 截断至最多 5 条。"""
    raw_json = _make_v8_json(
        verdict="try",
        sales_level="高",
        derivation=["原因1", "原因2", "原因3", "原因4", "原因5"],
    )
    _, verdict, derivation, _, _, _ = prompt_v8.parse_facts(raw_json)
    assert verdict == "skip"
    assert len(derivation) == 5
    assert derivation[0] == "销售与客户对接成分为高，结论改为不建议投"
    assert derivation[1:] == ["原因1", "原因2", "原因3", "原因4"]


def test_v8_parse_facts_medium_and_low_not_triggered():
    """sales_level 为中或低时，不触发高→skip 兜底。"""
    for sl in ("中", "低"):
        raw_json = _make_v8_json(
            verdict="try",
            sales_level=sl,
            verdict_reason="门槛低可以一试",
            derivation=["推导原因A"],
            hr_questions=["问题A"],
        )
        _, verdict, derivation, verdict_reason, hr_questions, _ = prompt_v8.parse_facts(raw_json)
        assert verdict == "try"
        assert derivation == ["推导原因A"]
        assert verdict_reason == "门槛低可以一试"
        assert hr_questions == ["问题A"]


def test_v8_parse_facts_already_skip_not_duplicated():
    """已是 skip 时，不重复插入兜底推导，不覆盖已有理由。"""
    raw_json = _make_v8_json(
        verdict="skip",
        sales_level="高",
        verdict_reason="职责含大量销售对接，不符合画像",
        derivation=["职责对接银行商户，属于销售性质"],
        hr_questions=[],
    )
    _, verdict, derivation, verdict_reason, hr_questions, _ = prompt_v8.parse_facts(raw_json)
    assert verdict == "skip"
    assert derivation == ["职责对接银行商户，属于销售性质"]
    assert verdict_reason == "职责含大量销售对接，不符合画像"
    assert hr_questions == []


def test_v8_fallback_ordering_skip_to_check_then_high_to_skip():
    """先后顺序：v7 的 skip→check 兜底与 v8 的高→skip 兜底。
    若模型返回 skip，sales_level 为高，但 reason 写了"需确认"（无风险）：
    v7 会将其转为 check；紧接着 v8 因 sales_level 为高再次将其转为 skip。
    """
    raw_json = _make_v8_json(
        verdict="skip",
        sales_level="高",
        verdict_reason="销售对接成分需确认",
        derivation=["原推导1"],
    )

    # 在 v7 中，这会被转为 check：
    _, v7_verdict, v7_der, _, _, _ = prompt_v7.parse_facts(raw_json)
    assert v7_verdict == "check"
    assert v7_der[0] == "一句话理由写的是需要确认，结论改为需要确认"

    # 在 v8 中，最终必须为 skip：
    _, v8_verdict, v8_der, v8_reason, v8_hr, _ = prompt_v8.parse_facts(raw_json)
    assert v8_verdict == "skip"
    assert v8_der[0] == "销售与客户对接成分为高，结论改为不建议投"
    assert v8_reason == "销售与客户对接成分高，与不接受销售冲突"
    assert v8_hr == []


def test_v8_config_and_dispatch(data_dir: Path, settings: Settings):
    """JET_PROMPT_VERSION=v8 时配置可用，分派到 v8 并由 worker 保存至数据库。"""
    # 1. 验证配置解析
    s = load_settings(data_dir=data_dir, env={"JET_PROMPT_VERSION": "v8"})
    assert s.prompt_version == "v8"

    conn = open_db(data_dir)
    user_id = "me"
    now_str = utc_now()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (91, 'boss', 'job_v8_dispatch', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (910, 91, 1, 'detail', '银行合作产品运营助理', '8-12K', 1, 1, '深圳', "
        "'负责对接银行，策划微信支付营销活动', 'h_v8_disp', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 910 WHERE id = 91")
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (91, 'me', 1, '[\"运营\"]', '[]', '[\"深圳\"]', 8.0, '[]', ?)",
        (now_str,),
    )

    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (9100, ?, 91, 910, 91, 'queued', NULL, 'llm', 'v8', 'deepseek-flash:no-think', '{}', ?, NULL)",
        (user_id, now_str),
    )
    conn.commit()

    # 模拟 LLM 响应（含 sales_level 高）
    mock_payload = _make_v8_json(verdict="try", sales_level="高", slot=1, reason="推荐简历")
    mock_response = {
        "choices": [
            {
                "message": {"content": mock_payload},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }

    def mock_transport_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    transport = httpx.MockTransport(mock_transport_handler)

    test_settings = dataclasses.replace(
        settings,
        data_dir=data_dir,
        prompt_version="v8",
        llm_api_key="test-key-v8",
        review_enabled=False,
    )
    worker = JudgementWorker(test_settings, transport=transport)
    worker.start()
    try:
        assert worker.submit(9100)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 验证存盘结果为 skip（被 v8 兜底转为 skip）
    j_saved = conn.execute("SELECT * FROM judgements WHERE id = 9100").fetchone()
    assert j_saved["status"] == "done"
    assert j_saved["verdict"] == "skip"
    assert j_saved["prompt_version"] == "v8"
    assert j_saved["verdict_reason"] == "销售与客户对接成分高，与不接受销售冲突"
    der_saved = json.loads(j_saved["derivation"])
    assert der_saved[0] == "销售与客户对接成分为高，结论改为不建议投"

    conn.close()
