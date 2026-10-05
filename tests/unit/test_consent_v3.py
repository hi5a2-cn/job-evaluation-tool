"""Unit tests for consent fields version upgrade to 3.

Specs:
- specs/003-hr-assistant/spec.md (FR-025, R1)
- specs/003-hr-assistant/contracts/local-api.md
"""

from pathlib import Path
from fastapi.testclient import TestClient

from jet.db.store import open_db, utc_now
from jet.domain.consent import (
    CONSENT_FIELDS_VERSION,
    consent_status,
    has_valid_consent,
    record_consent,
    revoke_consent,
)


def test_consent_fields_version_is_3():
    """CONSENT_FIELDS_VERSION 常量为 3。"""
    assert CONSENT_FIELDS_VERSION == 3


def test_legacy_version_2_consent_invalid_and_requires_reconsent(data_dir: Path):
    """版本 2 的同意记录在升级后无效，重新同意后变为有效，撤回后无效。"""
    conn = open_db(data_dir)
    try:
        now_str = utc_now()
        # 插入版本 2 的旧同意记录
        conn.execute(
            """
            INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at)
            VALUES ('me', ?, NULL, 2, ?)
            """,
            (now_str, now_str),
        )

        # 1. 版本 2 且未撤回 -> 在当前系统判定为无效
        assert has_valid_consent(conn, "me") is False
        st = consent_status(conn, "me")
        assert st["has_consent"] is False
        assert st["fields_version"] == 2

        # 2. 重新记录同意 -> 写入版本 3，判定为有效
        t_reconsent = record_consent(conn, "me")
        assert has_valid_consent(conn, "me") is True

        st2 = consent_status(conn, "me")
        assert st2["has_consent"] is True
        assert st2["consented_at"] == t_reconsent
        assert st2["fields_version"] == 3

        # 3. 撤回后 -> 判定为无效
        revoke_consent(conn, "me")
        assert has_valid_consent(conn, "me") is False
        st3 = consent_status(conn, "me")
        assert st3["has_consent"] is False
    finally:
        conn.close()


def test_legacy_version_2_blocks_chat_generate_with_403(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    """旧版本 2 同意记录在 POST /v1/chat/generate 时返回 403 consent_required，重新同意后通过同意检查。"""
    client, headers = paired_client

    # 1. 模拟旧版数据：直接在数据库插入 fields_version = 2 记录
    conn = open_db(data_dir)
    try:
        now_str = utc_now()
        conn.execute(
            """
            INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at)
            VALUES ('me', ?, NULL, 2, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                consented_at = excluded.consented_at,
                revoked_at = NULL,
                fields_version = 2,
                updated_at = excluded.updated_at
            """,
            (now_str, now_str),
        )
        conn.commit()
    finally:
        conn.close()

    # 查询状态：接口返回 has_consent=False
    status_resp = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["has_consent"] is False
    assert status_resp.json()["fields_version"] == 2

    # 尝试调用 generate，被 403 consent_required 拦截
    gen_payload = {
        "encrypt_job_id": "job-test-v3",
        "job_title": "测试职位",
        "company_name": "测试公司",
        "location_name": "深圳",
        "hr_name": "王女士",
        "user_name": "李四",
        "messages": [],
        "prompt_hash": "a" * 64,
    }
    resp = client.post("/v1/chat/generate", json=gen_payload, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == "consent_required"

    # 2. 用户重新同意：调用 POST /v1/chat/consent
    consent_resp = client.post("/v1/chat/consent", json={}, headers=headers)
    assert consent_resp.status_code == 200
    assert consent_resp.json()["ok"] is True

    # 查询状态：fields_version 升级为 3，has_consent=True
    status_resp2 = client.get("/v1/chat/consent-status", headers=headers)
    assert status_resp2.status_code == 200
    assert status_resp2.json()["has_consent"] is True
    assert status_resp2.json()["fields_version"] == 3
