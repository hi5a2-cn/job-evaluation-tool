"""体检第 47 条：同意的字段版本由服务端决定，请求里带的版本号不起作用。"""

from starlette.testclient import TestClient

from jet.domain.consent import CONSENT_FIELDS_VERSION


def test_consent_records_server_version_without_body(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    resp = client.post("/v1/chat/consent", json={}, headers=headers)
    assert resp.status_code == 200
    status = client.get("/v1/chat/consent-status", headers=headers).json()
    assert status["has_consent"] is True
    assert status["fields_version"] == CONSENT_FIELDS_VERSION == 3


def test_consent_ignores_stale_version_in_body(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    resp = client.post("/v1/chat/consent", json={"fields_version": 1}, headers=headers)
    assert resp.status_code == 200
    status = client.get("/v1/chat/consent-status", headers=headers).json()
    assert status["fields_version"] == CONSENT_FIELDS_VERSION
    assert status["has_consent"] is True
