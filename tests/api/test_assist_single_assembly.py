"""体检第 74 条：生成沟通建议时只组装一次提示词，发给大模型的内容与预览完全一致。"""

import json

import pytest
from starlette.testclient import TestClient

import jet.api.routes as routes_mod
import jet.llm.assist as assist_mod
from jet.llm.assist import build_assist_preview, generate_assist_suggestions
from tests.conftest import FakeLlmHelper

PAYLOAD = {
    "encrypt_job_id": "job-single-assembly",
    "job_title": "海外技术支持",
    "company_name": "某太阳能科技公司",
    "location_name": "合肥",
    "hr_name": "王经理",
    "user_name": "李四",
    "messages": [
        {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
        {"sender": "HR", "text": "您好，在招的。电话 13800138000。你现在在哪个城市？", "is_self": False, "type": 1, "body_type": 1},
    ],
}


def _set_fake(fake_llm: FakeLlmHelper) -> None:
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "您好，我目前在【填写：所在城市】，可以随时沟通。", "referenced_experience_ids": [1]},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，很高兴了解这个岗位。", "referenced_experience_ids": []},
        ],
        questions=["请问具体对接哪些客户？"],
    )


def test_generate_assembles_once_and_sends_preview_prompt(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers = llm_client
    client.post("/v1/chat/consent", json={}, headers=headers)
    client.put("/v1/experience", json=[{"item_no": 1, "content": "光伏电站运维 2 年"}], headers=headers)
    _set_fake(fake_llm)

    calls: list[str] = []
    real = assist_mod.assemble_assist_request

    def counting(*args, **kwargs):
        calls.append("assemble")
        return real(*args, **kwargs)

    monkeypatch.setattr(routes_mod, "assemble_assist_request", counting)
    monkeypatch.setattr(assist_mod, "assemble_assist_request", counting)

    preview = client.post("/v1/chat/preview", json=PAYLOAD, headers=headers)
    assert preview.status_code == 200
    assert len(calls) == 1
    calls.clear()

    gen = client.post("/v1/chat/generate", json={**PAYLOAD, "prompt_hash": preview.json()["prompt_hash"]}, headers=headers)
    assert gen.status_code == 200, gen.text
    assert len(calls) == 1  # 只在接口层组装一次，生成函数不再重复组装

    assert fake_llm.call_count == 1
    sent = json.loads(fake_llm.requests[0].content)
    assert sent["messages"][0]["content"] == preview.json()["sanitized_prompt"]
    assert "13800138000" not in sent["messages"][0]["content"]


def test_generate_with_and_without_preassembled_is_identical(settings, fake_llm: FakeLlmHelper):
    _set_fake(fake_llm)
    messages = [{"sender": "我", "text": "您好！"}, {"sender": "HR", "text": "在招的，你在哪个城市？"}]
    experiences = [{"item_no": 1, "content": "光伏电站运维 2 年"}]
    kwargs = dict(
        settings=settings, job_title="海外技术支持", company_name="某太阳能科技公司", location_name="合肥",
        sanitized_messages=messages, experiences=experiences, hr_note="对接海外代理商", current_city="",
    )
    preview = build_assist_preview(
        job_title="海外技术支持", company_name="某太阳能科技公司", location_name="合肥",
        sanitized_messages=messages, experiences=experiences, hr_note="对接海外代理商",
    )
    assembled = assist_mod.assemble_assist_request(
        job_title="海外技术支持", company_name="某太阳能科技公司", location_name="合肥",
        messages=None, experiences=experiences, sanitized_messages=messages, hr_note="对接海外代理商",
        current_city="",
    )
    assert assembled["prompt_hash"] == preview["prompt_hash"]

    r1 = generate_assist_suggestions(**kwargs, expected_hash=preview["prompt_hash"], transport=fake_llm.transport)
    r2 = generate_assist_suggestions(
        **kwargs, expected_hash=preview["prompt_hash"], transport=fake_llm.transport, assembled=assembled
    )
    strip = lambda r: {k: v for k, v in r.items() if k != "usage"}  # noqa: E731
    assert strip(r1) == strip(r2)
    assert fake_llm.requests[0].content == fake_llm.requests[1].content


def test_preassembled_still_checks_hash(settings, fake_llm: FakeLlmHelper):
    messages = [{"sender": "HR", "text": "在招的"}]
    assembled = assist_mod.assemble_assist_request(
        job_title="岗位", company_name="公司", location_name="深圳", messages=None,
        experiences=[], sanitized_messages=messages,
    )
    with pytest.raises(assist_mod.HashMismatchError):
        generate_assist_suggestions(
            settings=settings, job_title="岗位", company_name="公司", location_name="深圳",
            sanitized_messages=messages, experiences=[], expected_hash="not-the-hash",
            transport=fake_llm.transport, assembled=assembled,
        )
    assert fake_llm.call_count == 0
