"""Consent management domain module (T009).

Manages user consent records for transmitting sanitized data to external LLM.
Specs:
- specs/003-hr-assistant/spec.md (FR-023-FR-026, R2)
- specs/003-hr-assistant/data-model.md (llm_consents)
- specs/003-hr-assistant/contracts/local-api.md
"""

from typing import Any
import sqlite3

from jet.db.store import transaction, utc_now

CONSENT_FIELDS_VERSION: int = 3


def has_valid_consent(
    conn: sqlite3.Connection,
    user_id: str,
    current_version: int = CONSENT_FIELDS_VERSION,
) -> bool:
    """Check if the user has an active, unrevoked consent matching current_version.

    A consent is valid if and only if:
    1. A record exists for user_id.
    2. revoked_at IS NULL.
    3. fields_version == current_version.
    """
    row = conn.execute(
        "SELECT revoked_at, fields_version FROM llm_consents WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return False
    return row["revoked_at"] is None and row["fields_version"] == current_version


def record_consent(
    conn: sqlite3.Connection,
    user_id: str,
    fields_version: int = CONSENT_FIELDS_VERSION,
) -> str:
    """Record or renew user consent to transmit sanitized chat data to LLM.

    Resets revoked_at to NULL and updates consented_at and fields_version.
    Returns the consented_at timestamp string (UTC ISO-8601).
    """
    if fields_version < 1:
        raise ValueError("fields_version 必须为正整数")

    now_str = utc_now()
    upsert_sql = """
        INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at)
        VALUES (?, ?, NULL, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            consented_at = excluded.consented_at,
            revoked_at = NULL,
            fields_version = excluded.fields_version,
            updated_at = excluded.updated_at
    """
    if conn.in_transaction:
        conn.execute(upsert_sql, (user_id, now_str, fields_version, now_str))
    else:
        with transaction(conn, immediate=True):
            conn.execute(upsert_sql, (user_id, now_str, fields_version, now_str))

    return now_str


def revoke_consent(conn: sqlite3.Connection, user_id: str) -> str:
    """Revoke user consent to transmit data to LLM.

    Sets revoked_at = utc_now(). Returns the revoked_at timestamp string (UTC ISO-8601).
    """
    now_str = utc_now()
    sql_update = "UPDATE llm_consents SET revoked_at = ?, updated_at = ? WHERE user_id = ?"
    sql_insert = """
        INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            revoked_at = excluded.revoked_at,
            updated_at = excluded.updated_at
    """

    if conn.in_transaction:
        cur = conn.execute(sql_update, (now_str, now_str, user_id))
        if cur.rowcount == 0:
            conn.execute(sql_insert, (user_id, now_str, now_str, CONSENT_FIELDS_VERSION, now_str))
    else:
        with transaction(conn, immediate=True):
            cur = conn.execute(sql_update, (now_str, now_str, user_id))
            if cur.rowcount == 0:
                conn.execute(sql_insert, (user_id, now_str, now_str, CONSENT_FIELDS_VERSION, now_str))

    return now_str


def consent_status(
    conn: sqlite3.Connection,
    user_id: str,
    current_version: int = CONSENT_FIELDS_VERSION,
) -> dict[str, Any]:
    """Retrieve user consent status dictionary.

    Returns:
        {
            "has_consent": bool,
            "consented_at": str | None,
            "fields_version": int | None,
        }
    """
    row = conn.execute(
        "SELECT consented_at, revoked_at, fields_version FROM llm_consents WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {
            "has_consent": False,
            "consented_at": None,
            "fields_version": None,
        }

    has_consent = (row["revoked_at"] is None) and (row["fields_version"] == current_version)
    return {
        "has_consent": has_consent,
        "consented_at": row["consented_at"],
        "fields_version": row["fields_version"],
    }
