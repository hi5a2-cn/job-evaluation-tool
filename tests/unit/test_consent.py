"""Unit tests for user consent domain and API (T008, T009).

Specs:
- specs/003-hr-assistant/spec.md (FR-023-FR-026, R2)
- specs/003-hr-assistant/data-model.md (llm_consents)
- specs/003-hr-assistant/contracts/local-api.md (POST /v1/chat/consent, POST /v1/chat/revoke-consent, GET /v1/chat/consent-status)
- specs/003-hr-assistant/tasks.md (T008, T009)
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from jet.db.store import open_db, utc_now
from jet.domain.consent import (
    CONSENT_FIELDS_VERSION,
    consent_status,
    has_valid_consent,
    record_consent,
    revoke_consent,
)


def test_has_valid_consent_rules(data_dir: Path):
    """有效同意判定规则：

    1. 无记录 -> 无效 (False)
    2. revoked_at 为空且 fields_version 等于当前版本 -> 有效 (True)
    3. 已撤回 (revoked_at 非空) -> 无效 (False)
    4. fields_version 低于当前版本 -> 无效 (False)
    5. fields_version 不匹配 -> 无效 (False)
    """
    conn = open_db(data_dir)
    try:
        # 1. 无记录
        assert has_valid_consent(conn, "me", current_version=1) is False
        status = consent_status(conn, "me", current_version=1)
        assert status["has_consent"] is False
        assert status["consented_at"] is None
        assert status["fields_version"] is None

        now_str = utc_now()
        # 2. revoked_at 为空且 fields_version 等于当前版本 -> 有效
        conn.execute(
            """
            INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at)
            VALUES ('me', ?, NULL, 1, ?)
            """,
            (now_str, now_str),
        )
        assert has_valid_consent(conn, "me", current_version=1) is True
        status = consent_status(conn, "me", current_version=1)
        assert status["has_consent"] is True
        assert status["consented_at"] == now_str
        assert status["fields_version"] == 1

        # 3. 已撤回 (revoked_at 非空) -> 无效
        revoked_time = utc_now()
        conn.execute(
            "UPDATE llm_consents SET revoked_at = ?, updated_at = ? WHERE user_id = 'me'",
            (revoked_time, revoked_time),
        )
        assert has_valid_consent(conn, "me", current_version=1) is False
        status = consent_status(conn, "me", current_version=1)
        assert status["has_consent"] is False
        assert status["consented_at"] == now_str
        assert status["fields_version"] == 1

        # 4. fields_version 低于当前版本 (如历史版本 1，当前系统升级到版本 2) -> 无效
        conn.execute(
            "UPDATE llm_consents SET revoked_at = NULL, fields_version = 1, updated_at = ? WHERE user_id = 'me'",
            (now_str,),
        )
        assert has_valid_consent(conn, "me", current_version=2) is False
        status = consent_status(conn, "me", current_version=2)
        assert status["has_consent"] is False
        assert status["consented_at"] == now_str
        assert status["fields_version"] == 1

        # 5. fields_version 不匹配（高于当前系统版本或其他不相等情况） -> 无效
        assert has_valid_consent(conn, "me", current_version=0) is False
    finally:
        conn.close()


def test_record_consent(data_dir: Path):
    """测试记录用户同意。"""
    conn = open_db(data_dir)
    try:
        assert has_valid_consent(conn, "me") is False

        consented_at = record_consent(conn, "me", fields_version=CONSENT_FIELDS_VERSION)
        assert isinstance(consented_at, str)
        assert has_valid_consent(conn, "me") is True

        st = consent_status(conn, "me")
        assert st["has_consent"] is True
        assert st["consented_at"] == consented_at
        assert st["fields_version"] == CONSENT_FIELDS_VERSION

        with pytest.raises(ValueError, match="fields_version 必须为正整数"):
            record_consent(conn, "me", fields_version=0)
    finally:
        conn.close()


def test_revoke_consent(data_dir: Path):
    """测试撤回用户同意。"""
    conn = open_db(data_dir)
    try:
        record_consent(conn, "me")
        assert has_valid_consent(conn, "me") is True

        revoked_at = revoke_consent(conn, "me")
        assert isinstance(revoked_at, str)
        assert has_valid_consent(conn, "me") is False

        st = consent_status(conn, "me")
        assert st["has_consent"] is False
        assert st["consented_at"] is not None
        assert st["fields_version"] == CONSENT_FIELDS_VERSION
    finally:
        conn.close()


def test_re_consent_after_revoke(data_dir: Path):
    """测试撤回后再同意：revoked_at 必须被清空恢复为 NULL。"""
    conn = open_db(data_dir)
    try:
        t1 = record_consent(conn, "me")
        assert has_valid_consent(conn, "me") is True

        t2 = revoke_consent(conn, "me")
        assert has_valid_consent(conn, "me") is False

        row = conn.execute("SELECT revoked_at FROM llm_consents WHERE user_id = 'me'").fetchone()
        assert row["revoked_at"] is not None

        # 撤回后再同意
        t3 = record_consent(conn, "me")
        assert has_valid_consent(conn, "me") is True

        row = conn.execute("SELECT consented_at, revoked_at FROM llm_consents WHERE user_id = 'me'").fetchone()
        assert row["revoked_at"] is None
        assert row["consented_at"] == t3

        st = consent_status(conn, "me")
        assert st["has_consent"] is True
    finally:
        conn.close()


def test_consent_isolated_between_users(data_dir: Path):
    """测试不同用户的同意状态互不影响。"""
    conn = open_db(data_dir)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES ('user2', 'user2', '2026-09-25T00:00:00Z')"
        )

        assert has_valid_consent(conn, "me") is False
        assert has_valid_consent(conn, "user2") is False

        # me 同意
        record_consent(conn, "me")
        assert has_valid_consent(conn, "me") is True
        assert has_valid_consent(conn, "user2") is False

        # user2 同意
        record_consent(conn, "user2")
        assert has_valid_consent(conn, "me") is True
        assert has_valid_consent(conn, "user2") is True

        # me 撤回
        revoke_consent(conn, "me")
        assert has_valid_consent(conn, "me") is False
        assert has_valid_consent(conn, "user2") is True

        # user2 撤回
        revoke_consent(conn, "user2")
        assert has_valid_consent(conn, "me") is False
        assert has_valid_consent(conn, "user2") is False
    finally:
        conn.close()


def test_chat_consent_api_unpaired_rejected(client: TestClient):
    """未配对 401 按现有约定。"""
    # 1. POST /v1/chat/consent
    resp1 = client.post("/v1/chat/consent", json={})
    assert resp1.status_code == 401
    assert resp1.json()["error"] == "unpaired"

    # 2. POST /v1/chat/revoke-consent
    resp2 = client.post("/v1/chat/revoke-consent", json={})
    assert resp2.status_code == 401
    assert resp2.json()["error"] == "unpaired"

    # 3. GET /v1/chat/consent-status
    resp3 = client.get("/v1/chat/consent-status")
    assert resp3.status_code == 401
    assert resp3.json()["error"] == "unpaired"


def test_chat_consent_api_lifecycle(paired_client: tuple[TestClient, dict[str, str]]):
    """测试完整接口流：初始状态 -> 同意 (有效) -> 撤回 (无效) -> 再同意 (有效)。"""
    client, headers = paired_client

    # 1. 初始状态：无同意记录
    status_resp = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["has_consent"] is False
    assert status_data["consented_at"] is None
    assert status_data["fields_version"] is None

    # 2. 同意 -> 状态有效
    consent_resp = client.post(
        "/v1/chat/consent",
        json={},
        headers=headers,
    )
    assert consent_resp.status_code == 200
    consent_data = consent_resp.json()
    assert consent_data["ok"] is True
    assert "consented_at" in consent_data
    t_consent = consent_data["consented_at"]

    # 查询状态有效
    status_resp2 = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp2.status_code == 200
    status_data2 = status_resp2.json()
    assert status_data2["has_consent"] is True
    assert status_data2["consented_at"] == t_consent
    assert status_data2["fields_version"] == CONSENT_FIELDS_VERSION

    # 3. 撤回 -> 状态无效
    revoke_resp = client.post(
        "/v1/chat/revoke-consent",
        json={},
        headers=headers,
    )
    assert revoke_resp.status_code == 200
    revoke_data = revoke_resp.json()
    assert revoke_data["ok"] is True
    assert "revoked_at" in revoke_data

    # 查询状态变为无效
    status_resp3 = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp3.status_code == 200
    status_data3 = status_resp3.json()
    assert status_data3["has_consent"] is False
    assert status_data3["consented_at"] == t_consent
    assert status_data3["fields_version"] == CONSENT_FIELDS_VERSION

    # 4. 撤回后再同意 -> 状态恢复有效
    reconsent_resp = client.post(
        "/v1/chat/consent",
        json={},
        headers=headers,
    )
    assert reconsent_resp.status_code == 200
    reconsent_data = reconsent_resp.json()
    assert reconsent_data["ok"] is True
    assert "consented_at" in reconsent_data

    status_resp4 = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp4.status_code == 200
    status_data4 = status_resp4.json()
    assert status_data4["has_consent"] is True
    assert status_data4["consented_at"] == reconsent_data["consented_at"]
    assert status_data4["fields_version"] == CONSENT_FIELDS_VERSION


def test_chat_consent_api_fields_version_ignored(paired_client: tuple[TestClient, dict[str, str]]):
    """第 0 条修正：客户端传入的 fields_version 被忽略，服务端一律记录 CONSENT_FIELDS_VERSION。"""
    client, headers = paired_client

    # 客户端即使传入任意 fields_version（如 999），服务端均忽略并成功记录当前 CONSENT_FIELDS_VERSION
    resp = client.post(
        "/v1/chat/consent",
        json={"fields_version": 999},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    status_resp = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["fields_version"] == CONSENT_FIELDS_VERSION
