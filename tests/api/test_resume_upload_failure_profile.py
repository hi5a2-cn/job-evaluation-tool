"""体检第 29 条：同一槽位换了另一份简历、画像生成失败时，不再让新简历配着旧画像。"""

import base64
import dataclasses
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from jet.db.store import open_db
from jet.domain.resume import get_resume_slot
from tests.api.test_resume_routes import _make_ascii_pdf_bytes

RESUME_A = [
    "Li Ming",
    "Phone: 13800000000",
    "Software Engineer with 6 years experience in Python and FastAPI backend development",
    "Skilled in MySQL, Redis, Kafka, Distributed Systems and Cloud Computing architecture",
]
RESUME_B = [
    "Li Ming",
    "Phone: 13800000000",
    "Product Operations Specialist with 4 years experience in growth and user research",
    "Skilled in data analysis, A/B testing, community operations and content strategy",
]


def _ok_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "direction": "Python 后端高并发架构研发",
                "highlights": [
                    "6 年 Python/FastAPI 开发经验",
                    "深入掌握分布式系统与 MySQL/Redis 调优",
                    "具备高可用架构与性能调优实战经验",
                ],
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}},
        )

    return httpx.MockTransport(handler)


def _connect_error_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def _garbage_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "不是 JSON"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}},
        )

    return httpx.MockTransport(handler)


def _upload(client: TestClient, headers: dict[str, str], lines: list[str], name: str) -> httpx.Response:
    payload = {
        "slot": 1,
        "name": name,
        "pdf_base64": base64.b64encode(_make_ascii_pdf_bytes(lines)).decode("ascii"),
        "self_name": "Li Ming",
    }
    return client.post("/v1/resumes/upload", json=payload, headers=headers)


def _first_upload_ok(client: TestClient, headers: dict[str, str]) -> str:
    client.app.state.llm_transport = _ok_transport()
    client.app.state.settings = dataclasses.replace(client.app.state.settings, llm_api_key="test-fake-key")
    resp = _upload(client, headers, RESUME_A, "后端版")
    assert resp.status_code == 200
    profile = resp.json()["profile"]
    assert profile
    return profile


def _slot_row(data_dir: Path):
    conn = open_db(data_dir)
    try:
        return get_resume_slot(conn, "me", 1)
    finally:
        conn.close()


def test_changed_resume_without_key_clears_old_profile(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _first_upload_ok(client, headers)

    client.app.state.settings = dataclasses.replace(client.app.state.settings, llm_api_key=None)
    resp = _upload(client, headers, RESUME_B, "运营版")
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "no_llm_key"
    assert data["has_text"] is True
    assert data["profile"] == ""

    row = _slot_row(data_dir)
    assert row["name"] == "运营版"
    assert "Product Operations" in row["resume_text"]
    assert row["profile"] == ""


def test_same_resume_without_key_keeps_profile(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    old_profile = _first_upload_ok(client, headers)

    client.app.state.settings = dataclasses.replace(client.app.state.settings, llm_api_key=None)
    resp = _upload(client, headers, RESUME_A, "后端版-改名")
    assert resp.status_code == 409
    assert resp.json()["profile"] == old_profile

    row = _slot_row(data_dir)
    assert row["name"] == "后端版-改名"
    assert row["profile"] == old_profile


@pytest.mark.parametrize("make_transport", [_connect_error_transport, _garbage_transport])
def test_changed_resume_llm_failure_clears_old_profile(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    make_transport,
):
    client, headers = paired_client
    _first_upload_ok(client, headers)

    client.app.state.llm_transport = make_transport()
    resp = _upload(client, headers, RESUME_B, "运营版")
    assert resp.status_code == 502
    data = resp.json()
    assert data["error"] == "llm_failed"
    # 文字已保存，前端据此显示「重新生成画像」按钮，并把画像框清空
    assert data["has_text"] is True
    assert data["profile"] == ""
    # 和未配 Key、额度用完两种失败一样带上槽位信息
    assert data["slot"] == 1
    assert data["name"] == "运营版"
    assert data["text_chars"] > 0

    row = _slot_row(data_dir)
    assert row["name"] == "运营版"
    assert row["profile"] == ""


def test_same_resume_llm_failure_keeps_profile(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    old_profile = _first_upload_ok(client, headers)

    client.app.state.llm_transport = _connect_error_transport()
    resp = _upload(client, headers, RESUME_A, "后端版")
    assert resp.status_code == 502
    assert resp.json()["profile"] == old_profile
    assert _slot_row(data_dir)["profile"] == old_profile
