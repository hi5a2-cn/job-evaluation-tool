"""Tests for force_mode support in chat preview and generate endpoints."""

from fastapi.testclient import TestClient

from tests.conftest import FakeLlmHelper


def test_assist_force_mode_preview_and_generate(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
):
    """带 force_mode='opening' 切换模式时预览与生成指纹一致且能成功生成，并验证与自动模式指纹不同。"""
    client, headers = llm_client

    # 1. 先同意发送脱敏数据
    consent_resp = client.post("/v1/chat/consent", headers=headers)
    assert consent_resp.status_code == 200

    # 2. 配置 mock 大模型返回
    fake_llm.set_assist(
        suggestions=[
            {"version": 1, "tone_desc": "自然直接", "text": "您好，我对该岗位很感兴趣，期待进一步交流。", "referenced_experience_ids": []},
            {"version": 2, "tone_desc": "沉稳专业", "text": "您好，已查阅贵司岗位要求，附上简要背景供参考。", "referenced_experience_ids": []},
        ],
        questions=["请问具体职责主要偏向哪块业务？"],
    )

    # 3. 聊天记录：最后一条由 HR 发送提问，自动模式为 reply
    chat_payload = {
        "encrypt_job_id": "job-force-mode-test-1",
        "job_title": "Python高级工程师",
        "company_name": "某高新技术企业",
        "location_name": "深圳",
        "hr_name": "张经理",
        "user_name": "李明",
        "messages": [
            {"sender": "我", "text": "您好，请问该职位目前还在招聘中吗？", "is_self": True, "type": 1, "body_type": 1},
            {"sender": "HR", "text": "在招的，方便发一份最新的简历看看吗？", "is_self": False, "type": 1, "body_type": 1},
        ],
    }

    # 4. 不带 force_mode 请求 preview，断言自动模式为 reply 并获得指纹
    auto_preview_resp = client.post("/v1/chat/preview", json=chat_payload, headers=headers)
    assert auto_preview_resp.status_code == 200
    auto_data = auto_preview_resp.json()
    assert auto_data["mode"] == "reply"
    auto_hash = auto_data["prompt_hash"]

    # 5. 带 force_mode="opening" 请求 preview，断言模式为 opening
    opening_preview_payload = {**chat_payload, "force_mode": "opening"}
    opening_preview_resp = client.post("/v1/chat/preview", json=opening_preview_payload, headers=headers)
    assert opening_preview_resp.status_code == 200
    opening_data = opening_preview_resp.json()
    assert opening_data["mode"] == "opening"
    opening_hash = opening_data["prompt_hash"]

    # 6. 断言不带 force_mode 的预览指纹与带 force_mode 的不同（证明确实覆盖了切换场景）
    assert auto_hash != opening_hash, "不带 force_mode 的预览指纹必须与带 force_mode 的指纹不同"

    # 7. 携带 opening_hash 和相同的 force_mode="opening" 发起 chat_generate 请求，断言 200
    generate_payload = {
        **opening_preview_payload,
        "prompt_hash": opening_hash,
    }
    gen_resp = client.post("/v1/chat/generate", json=generate_payload, headers=headers)
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["mode"] == "opening"
    assert len(gen_data["suggestions"]) > 0
