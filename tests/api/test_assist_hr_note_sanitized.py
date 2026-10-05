"""API integration tests for sanitized HR note in assist preview and generate (Fix H).

Specs:
- specs/003-hr-assistant/spec.md (R1)
- specs/003-hr-assistant/contracts/local-api.md (Section 1 & 5)
"""

import json
from pathlib import Path
from fastapi.testclient import TestClient
import httpx

from tests.conftest import FakeLlmHelper


def _setup_job(client: TestClient, headers: dict[str, str], pid: str = "job_note_sanitized_1") -> None:
    """初始化画像与岗位。"""
    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["深圳"]},
        headers=headers,
    )
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-26T01:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "后端开发",
                "company_name": "测试网络",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "服务端架构设计",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200


def test_assist_hr_note_sanitized_in_preview_and_generate(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """HR 实际情况中的 HR 姓名与手机号在 preview 与 generate 中均已脱敏，大模型收到的也是脱敏后的内容，数据库保留原文。"""
    client, headers = llm_client
    pid = "job_hr_note_sanitized_1"
    _setup_job(client, headers, pid=pid)

    # 1. 存入包含 HR 姓名和手机号的 HR 实际情况原文
    raw_note = "刘女士说底薪 5k，电话 13800000000"
    resp_note = client.put(
        f"/v1/jobs/{pid}/hr-note",
        json={"note": raw_note, "source": "myjobs"},
        headers=headers,
    )
    assert resp_note.status_code == 200

    # 2. 记录同意
    client.post("/v1/chat/consent", json={"fields_version": 3}, headers=headers)

    # 3. 设置假大模型响应
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "好的呀，在招的。", "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，非常希望能进一步交流。", "referenced_experience_ids": []},
        ],
        questions=["想了解日常工作内容？"],
    )

    payload = {
        "encrypt_job_id": pid,
        "job_title": "后端开发",
        "company_name": "测试网络",
        "location_name": "深圳",
        "hr_name": "刘女士",
        "user_name": "张三",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
        ],
    }

    # 4. 调用 /v1/chat/preview 验证脱敏
    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    sanitized_prompt = prev_data["sanitized_prompt"]
    assert "刘女士" not in sanitized_prompt
    assert "13800000000" not in sanitized_prompt
    assert "[已隐藏]" in sanitized_prompt
    preview_hash = prev_data["prompt_hash"]

    # 5. 调用 /v1/chat/generate 验证成功（200 指纹一致）
    fake_llm.call_count = 0
    gen_payload = {**payload, "prompt_hash": preview_hash}
    gen_resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert gen_resp.status_code == 200
    assert fake_llm.call_count == 1

    # 6. 验证大模型实际接收到的请求正文中没有 HR 姓名和手机号
    sent_body = fake_llm.requests[-1].read().decode("utf-8")
    assert "刘女士" not in sent_body
    assert "13800000000" not in sent_body
    assert "[已隐藏]" in sent_body

    # 7. 验证本机数据库中保留的是未脱敏原文
    get_res = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["jobs"][pid]["hr_note"] == raw_note


def test_assist_hr_note_sanitized_with_custom_worker_transport(
    llm_client: tuple[TestClient, dict[str, str]],
):
    """当需要自定义大模型响应时，通过 client.app.state.worker.transport 设置有效。"""
    client, headers = llm_client
    pid = "job_hr_note_sanitized_2"
    _setup_job(client, headers, pid=pid)

    raw_note = "王经理说加微信号 testwxid，电话 13900000000"
    client.put(
        f"/v1/jobs/{pid}/hr-note",
        json={"note": raw_note, "source": "myjobs"},
        headers=headers,
    )
    client.post("/v1/chat/consent", json={"fields_version": 3}, headers=headers)

    captured_requests: list[httpx.Request] = []

    def custom_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        content = json.dumps(
            {
                "suggestions": [
                    {"version": 1, "tone_desc": "自然直接", "text": "好的呀，收到。", "referenced_experience_ids": []},
                    {"version": 2, "tone_desc": "沉稳专业", "text": "您好，非常期待进一步交流。", "referenced_experience_ids": []},
                ],
                "questions": ["想了解日常工作排班？"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    # 设置 worker.transport
    client.app.state.worker.transport = httpx.MockTransport(custom_handler)

    payload = {
        "encrypt_job_id": pid,
        "job_title": "后端开发",
        "company_name": "测试网络",
        "location_name": "深圳",
        "hr_name": "王经理",
        "user_name": "李四",
        "messages": [
            {"sender": "我", "text": "您好！", "is_self": True, "type": 1, "body_type": 1},
        ],
    }

    prev_resp = client.post("/v1/chat/preview", json=payload, headers=headers)
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert "王经理" not in prev_data["sanitized_prompt"]
    assert "13900000000" not in prev_data["sanitized_prompt"]
    assert "[已隐藏]" in prev_data["sanitized_prompt"]

    gen_resp = client.post("/v1/chat/generate", json={**payload, "prompt_hash": prev_data["prompt_hash"]}, headers=headers)
    assert gen_resp.status_code == 200
    assert len(captured_requests) == 1

    sent_body = captured_requests[0].read().decode("utf-8")
    assert "王经理" not in sent_body
    assert "13900000000" not in sent_body
    assert "[已隐藏]" in sent_body
