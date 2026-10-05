import sqlite3
from fastapi import HTTPException

from jet.db.store import utc_now


def hr_note_for(conn: sqlite3.Connection, user_id: str, job_id: int) -> str | None:
    """Retrieve the HR note for a given job and user, if any."""
    row = conn.execute(
        "SELECT note FROM hr_notes WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    ).fetchone()
    if row is None:
        return None
    return str(row["note"])


def save_hr_note(
    conn: sqlite3.Connection,
    user_id: str,
    platform_job_id: str,
    note: str | None,
) -> str | None:
    """
    Save, update, or clear HR note for a platform job.

    - Strips whitespace.
    - If empty or None -> deletes the HR note row, returns None.
    - If length > 200 -> raises ValueError.
    - If job does not exist -> raises HTTPException(404).
    """
    job = conn.execute(
        "SELECT id FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (platform_job_id,),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "岗位不存在"})

    cleaned = note.strip() if note is not None else ""
    if not cleaned:
        conn.execute(
            "DELETE FROM hr_notes WHERE user_id = ? AND job_id = ?",
            (user_id, job["id"]),
        )
        return None

    if len(cleaned) > 200:
        raise ValueError("HR 说的实际情况不能超过 200 字")

    now_str = utc_now()
    conn.execute(
        """
        INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, job_id) DO UPDATE SET
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (user_id, job["id"], cleaned, now_str, now_str),
    )
    return cleaned
