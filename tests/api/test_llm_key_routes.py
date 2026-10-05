import logging
from pathlib import Path
import stat
import httpx
import pytest
from fastapi.testclient import TestClient

from jet.config import LLM_KEY_FILE_NAME
from jet.db.store import open_db


def test_unauthenticated_requests(client: TestClient):
    assert client.get("/v1/llm-key").status_code == 401
    assert client.put("/v1/llm-key", json={"api_key": "sk-123"}).status_code == 401
    assert client.delete("/v1/llm-key").status_code == 401
    assert client.post("/v1/llm-key/test", json={}).status_code == 401


def test_get_llm_key_unconfigured(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    resp = client.get("/v1/llm-key", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "configured": False,
        "masked": None,
        "source": None,
    }

    status_resp = client.get("/v1/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["llm_key_configured"] is False


def test_put_llm_key_validation(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # Empty string
    resp = client.put("/v1/llm-key", json={"api_key": ""}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_payload"

    # Whitespace only
    resp = client.put("/v1/llm-key", json={"api_key": "   \n\t  "}, headers=headers)
    assert resp.status_code == 422

    # Missing field
    resp = client.put("/v1/llm-key", json={}, headers=headers)
    assert resp.status_code == 422


def test_put_llm_key_success_and_runtime_sync(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    fake_key = "sk-0123456789abcdef0123456789abcdef"

    resp = client.put("/v1/llm-key", json={"api_key": f"  {fake_key}  "}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["source"] == "settings_page"
    assert data["masked"] == "••••cdef"
    assert fake_key not in resp.text

    # File check
    key_file = data_dir / LLM_KEY_FILE_NAME
    assert key_file.exists()
    assert key_file.read_text(encoding="utf-8") == fake_key
    file_perm = stat.S_IMODE(key_file.stat().st_mode)
    assert file_perm == 0o600

    # Runtime app and worker settings sync
    assert client.app.state.settings.llm_api_key == fake_key
    assert client.app.state.settings.llm_key_source == "settings_page"
    assert client.app.state.worker.settings.llm_api_key == fake_key
    assert client.app.state.worker.settings.llm_key_source == "settings_page"

    # Status check
    status_resp = client.get("/v1/status", headers=headers)
    assert status_resp.json()["llm_key_configured"] is True

    # GET check
    get_resp = client.get("/v1/llm-key", headers=headers)
    assert get_resp.json() == data


def test_delete_llm_key(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    key = "sk-delete-me-12345678"
    client.put("/v1/llm-key", json={"api_key": key}, headers=headers)

    key_file = data_dir / LLM_KEY_FILE_NAME
    assert key_file.exists()

    resp = client.delete("/v1/llm-key", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "configured": False,
        "masked": None,
        "source": None,
    }
    assert not key_file.exists()
    assert client.app.state.settings.llm_api_key is None
    assert client.app.state.settings.llm_key_source is None
    assert client.app.state.worker.settings.llm_api_key is None

    # Idempotent delete
    resp2 = client.delete("/v1/llm-key", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["configured"] is False


def test_delete_llm_key_fallback_to_env(
    paired_client: tuple[TestClient, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers = paired_client
    env_key = "sk-env-backup-12345678"
    monkeypatch.setenv("JET_LLM_API_KEY", env_key)

    # First PUT overrides env
    client.put("/v1/llm-key", json={"api_key": "sk-settings-custom-key"}, headers=headers)
    assert client.app.state.settings.llm_api_key == "sk-settings-custom-key"
    assert client.app.state.settings.llm_key_source == "settings_page"

    # DELETE falls back to env
    resp = client.delete("/v1/llm-key", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["source"] == "env"
    assert data["masked"] == "••••5678"
    assert client.app.state.settings.llm_api_key == env_key
    assert client.app.state.worker.settings.llm_api_key == env_key


def test_test_llm_key_no_key(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    resp = client.post("/v1/llm-key/test", json={}, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "no_key"}


def test_test_llm_key_upstream_outcomes(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client

    recorded_requests: list[httpx.Request] = []
    mock_status = 200
    mock_raise: Exception | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        if mock_raise:
            raise mock_raise
        return httpx.Response(mock_status, json={"data": [{"id": "deepseek-chat"}]})

    client.app.state.llm_transport = httpx.MockTransport(handler)

    # 1. 200 OK
    mock_status = 200
    mock_raise = None
    resp = client.post(
        "/v1/llm-key/test",
        json={"api_key": "sk-provided-key-1234"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "reason": "ok"}
    assert len(recorded_requests) == 1
    assert recorded_requests[-1].url.path == "/models"
    assert recorded_requests[-1].headers["authorization"] == "Bearer sk-provided-key-1234"

    # 2. 401 invalid_key
    mock_status = 401
    resp = client.post("/v1/llm-key/test", json={"api_key": "sk-invalid-key"}, headers=headers)
    assert resp.json() == {"ok": False, "reason": "invalid_key"}

    # 3. 403 invalid_key
    mock_status = 403
    resp = client.post("/v1/llm-key/test", json={"api_key": "sk-forbidden-key"}, headers=headers)
    assert resp.json() == {"ok": False, "reason": "invalid_key"}

    # 4. 500 unreachable
    mock_status = 500
    resp = client.post("/v1/llm-key/test", json={"api_key": "sk-server-err"}, headers=headers)
    assert resp.json() == {"ok": False, "reason": "unreachable"}

    # 5. timeout unreachable
    mock_raise = httpx.ReadTimeout("Timeout")
    resp = client.post("/v1/llm-key/test", json={"api_key": "sk-timeout-key"}, headers=headers)
    assert resp.json() == {"ok": False, "reason": "unreachable"}

    # Verify no llm_calls records inserted, 0 quota used
    conn = open_db(data_dir)
    try:
        count = conn.execute("SELECT count(*) as cnt FROM llm_calls").fetchone()["cnt"]
        assert count == 0
    finally:
        conn.close()


def test_no_key_leak_in_response_or_logs(
    paired_client: tuple[TestClient, dict[str, str]],
    caplog: pytest.LogCaptureFixture,
):
    client, headers = paired_client
    fake_key = "sk-test-1234567890abcd"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client.app.state.llm_transport = httpx.MockTransport(handler)

    with caplog.at_level(logging.DEBUG):
        r1 = client.put("/v1/llm-key", json={"api_key": fake_key}, headers=headers)
        r2 = client.get("/v1/llm-key", headers=headers)
        r3 = client.post("/v1/llm-key/test", json={"api_key": fake_key}, headers=headers)
        r4 = client.get("/v1/status", headers=headers)

    # Response JSON should never contain the full raw key
    assert fake_key not in r1.text
    assert fake_key not in r2.text
    assert fake_key not in r3.text
    assert fake_key not in r4.text

    # Caplog should never contain the full raw key
    assert fake_key not in caplog.text
