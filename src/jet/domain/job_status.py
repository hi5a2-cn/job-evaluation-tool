import sqlite3
from typing import Any

from jet.db.store import transaction, utc_now
from jet.domain import labels
from jet.domain.hr_notes import hr_note_for
from jet.domain.judgements import effective_verdict, latest_judgement, staleness
from jet.domain.taxonomy import VERDICT_LABELS

VALID_STATUSES = {"saved", "applied", "skipped"}
VALID_FILTERS = {"all_jobs", "all", "saved", "applied", "skipped", "recent"}


def escape_like(query: str, escape_char: str = "/") -> str:
    """Escape SQLite LIKE wildcard characters % and _ using escape_char."""
    return (
        query.replace(escape_char, escape_char + escape_char)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )


def status_for(conn: sqlite3.Connection, user_id: str, job_id: int) -> dict[str, str] | None:
    """Retrieve current status and updated_at for a given user and job, or None."""
    row = conn.execute(
        "SELECT status, updated_at FROM job_status WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "status": row["status"],
        "updated_at": row["updated_at"],
    }


def set_status(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    platform_job_id: str,
    status: str | None,
) -> dict[str, str] | None:
    """
    Set, update, or cancel personal status for a platform job.

    - status is None means cancel.
    - If job does not exist, raises LookupError.
    - If status is not None and not in ('saved', 'applied', 'skipped'), raises ValueError.
    - If status is identical to current status, returns without writing an event.
    - Otherwise, upserts or deletes job_status, and appends an event to job_status_events.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    def _execute() -> dict[str, str] | None:
        job = conn.execute(
            "SELECT id FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
            (platform_job_id,),
        ).fetchone()
        if job is None:
            raise LookupError(f"Job not found: {platform_job_id}")

        job_id = job["id"]

        cur_row = conn.execute(
            "SELECT status, updated_at FROM job_status WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        ).fetchone()
        current_status = cur_row["status"] if cur_row else None

        if current_status == status:
            if cur_row is not None:
                return {"status": cur_row["status"], "updated_at": cur_row["updated_at"]}
            return None

        now_str = utc_now()
        # Append an immutable event
        conn.execute(
            "INSERT INTO job_status_events (user_id, job_id, status, previous_status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, job_id, status, current_status, now_str),
        )

        if status is None:
            conn.execute(
                "DELETE FROM job_status WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
            return None
        else:
            conn.execute(
                """
                INSERT INTO job_status (user_id, job_id, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (user_id, job_id, status, now_str),
            )
            return {"status": status, "updated_at": now_str}

    if conn.in_transaction:
        return _execute()
    else:
        with transaction(conn, immediate=True):
            return _execute()


def list_my_jobs(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    filter: str,
    hr_only: bool = False,
    q: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """
    List user's jobs with statistics counts and detailed items.

    Filters:
    - 'all_jobs': jobs with status OR judged OR with HR notes (deduplicated)
    - 'all': jobs with any personal status
    - 'saved', 'applied', 'skipped': jobs with matching status
    - 'recent': jobs with at least one judgement, sorted by last view time
    """
    if filter not in VALID_FILTERS:
        raise ValueError(f"Invalid filter: {filter}")
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    # 1. Calculate counts for filter categories
    if not hr_only:
        count_all_jobs = conn.execute(
            """
            WITH all_target_jobs AS (
                SELECT job_id FROM job_status WHERE user_id = ?
                UNION
                SELECT job_id FROM judgements WHERE user_id = ?
                UNION
                SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0
                UNION
                SELECT job_id FROM job_chat_seen WHERE user_id = ?
            )
            SELECT COUNT(*) FROM all_target_jobs
            """,
            (user_id, user_id, user_id, user_id),
        ).fetchone()[0]

        count_all = conn.execute(
            "SELECT COUNT(*) FROM job_status WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

        count_saved = conn.execute(
            "SELECT COUNT(*) FROM job_status WHERE user_id = ? AND status = 'saved'",
            (user_id,),
        ).fetchone()[0]

        count_applied = conn.execute(
            "SELECT COUNT(*) FROM job_status WHERE user_id = ? AND status = 'applied'",
            (user_id,),
        ).fetchone()[0]

        count_skipped = conn.execute(
            "SELECT COUNT(*) FROM job_status WHERE user_id = ? AND status = 'skipped'",
            (user_id,),
        ).fetchone()[0]

        count_recent = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM judgements WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
    else:
        # When hr_only=True, counts reflect jobs that have non-empty HR notes within each category
        count_all_jobs = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0",
            (user_id,),
        ).fetchone()[0]

        count_all = conn.execute(
            """
            SELECT COUNT(*)
            FROM job_status js
            WHERE js.user_id = ?
              AND js.job_id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

        count_saved = conn.execute(
            """
            SELECT COUNT(*)
            FROM job_status js
            WHERE js.user_id = ? AND js.status = 'saved'
              AND js.job_id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

        count_applied = conn.execute(
            """
            SELECT COUNT(*)
            FROM job_status js
            WHERE js.user_id = ? AND js.status = 'applied'
              AND js.job_id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

        count_skipped = conn.execute(
            """
            SELECT COUNT(*)
            FROM job_status js
            WHERE js.user_id = ? AND js.status = 'skipped'
              AND js.job_id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

        count_recent = conn.execute(
            """
            SELECT COUNT(DISTINCT jd.job_id)
            FROM judgements jd
            WHERE jd.user_id = ?
              AND jd.job_id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)
            """,
            (user_id, user_id),
        ).fetchone()[0]

    counts = {
        "all_jobs": count_all_jobs,
        "all": count_all,
        "saved": count_saved,
        "applied": count_applied,
        "skipped": count_skipped,
        "recent": count_recent,
    }

    # 2. Build filter conditions
    where_clauses: list[str] = []
    params: list[Any] = []

    if filter == "all_jobs":
        where_clauses.append(
            """
            j.id IN (
                SELECT job_id FROM job_status WHERE user_id = ?
                UNION
                SELECT job_id FROM judgements WHERE user_id = ?
                UNION
                SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0
                UNION
                SELECT job_id FROM job_chat_seen WHERE user_id = ?
            )
            """
        )
        params.extend([user_id, user_id, user_id, user_id])
    elif filter == "all":
        where_clauses.append("j.id IN (SELECT job_id FROM job_status WHERE user_id = ?)")
        params.append(user_id)
    elif filter in ("saved", "applied", "skipped"):
        where_clauses.append("j.id IN (SELECT job_id FROM job_status WHERE user_id = ? AND status = ?)")
        params.extend([user_id, filter])
    elif filter == "recent":
        where_clauses.append("j.id IN (SELECT DISTINCT job_id FROM judgements WHERE user_id = ?)")
        params.append(user_id)

    if hr_only:
        where_clauses.append("j.id IN (SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0)")
        params.append(user_id)

    has_search = bool(q is not None and q.strip() != "")
    if has_search:
        like_pat = f"%{escape_like(q.strip())}%"
        where_clauses.append(
            """
            (
                j.company_name LIKE ? ESCAPE '/'
                OR EXISTS (
                    SELECT 1 FROM hr_notes hn
                    WHERE hn.job_id = j.id AND hn.user_id = ? AND hn.note LIKE ? ESCAPE '/'
                )
                OR EXISTS (
                    SELECT 1 FROM job_versions jv
                    WHERE jv.job_id = j.id AND jv.title LIKE ? ESCAPE '/'
                )
                OR EXISTS (
                    SELECT 1 FROM job_chat_seen jcs
                    WHERE jcs.job_id = j.id AND jcs.user_id = ? AND jcs.chat_title LIKE ? ESCAPE '/'
                )
            )
            """
        )
        params.extend([like_pat, user_id, like_pat, like_pat, user_id, like_pat])

    where_sql = " AND ".join(where_clauses)

    # 3. Calculate total_matches
    count_sql = f"SELECT COUNT(*) FROM jobs j WHERE {where_sql}"
    total_matches = conn.execute(count_sql, params).fetchone()[0]

    # 4. Determine ordering
    if filter == "all_jobs" or has_search:
        order_clause = "ORDER BY recent_updated_at DESC, id DESC"
    elif filter in ("all", "saved", "applied", "skipped"):
        order_clause = "ORDER BY status_updated_at DESC, id DESC"
    else:  # filter == "recent" and not has_search
        order_clause = "ORDER BY (last_seen IS NULL), last_seen DESC, last_judged_at DESC, id DESC"

    # 5. Fetch rows with computed fields
    query_sql = f"""
    WITH matched AS (
        SELECT j.*,
               js.status AS my_status,
               js.updated_at AS status_updated_at,
               hn.note AS hr_note_text,
               hn.updated_at AS hr_note_updated_at,
               jcs.chat_title AS chat_title,
               jcs.last_seen_at AS chat_last_seen_at,
               (
                   SELECT MAX(v.seen_at)
                   FROM views v
                   WHERE v.job_id = j.id AND v.user_id = ? AND v.page_type = 'detail'
               ) AS last_seen,
               (
                   SELECT MAX(jd.created_at)
                   FROM judgements jd
                   WHERE jd.job_id = j.id AND jd.user_id = ?
               ) AS last_judged_at
        FROM jobs j
        LEFT JOIN job_status js ON js.job_id = j.id AND js.user_id = ?
        LEFT JOIN hr_notes hn ON hn.job_id = j.id AND hn.user_id = ?
        LEFT JOIN job_chat_seen jcs ON jcs.job_id = j.id AND jcs.user_id = ?
        WHERE {where_sql}
    )
    SELECT *,
           MAX(
               COALESCE(status_updated_at, ''),
               COALESCE(hr_note_updated_at, ''),
               COALESCE(last_seen, ''),
               COALESCE(last_judged_at, ''),
               COALESCE(chat_last_seen_at, '')
           ) AS recent_updated_at
    FROM matched
    {order_clause}
    LIMIT ?
    """

    full_params = [user_id, user_id, user_id, user_id, user_id] + params + [limit]
    rows = conn.execute(query_sql, full_params).fetchall()


    items: list[dict[str, Any]] = []
    for r in rows:
        job_id = r["id"]
        platform_job_id = r["platform_job_id"]
        company_name = r["company_name"]

        # Current version fields
        cur_version_id = r["current_version_id"]
        v_row = conn.execute(
            "SELECT title, salary_raw, city FROM job_versions WHERE id = ?",
            (cur_version_id,),
        ).fetchone() if cur_version_id else None

        title = v_row["title"] if v_row else (r["chat_title"] or "")
        salary_raw = v_row["salary_raw"] if v_row else None
        city = v_row["city"] if v_row else ""

        # Latest active judgement
        latest = latest_judgement(conn, user_id, job_id)
        verdict = None
        verdict_label = None
        model_verdict = None
        verdict_overridden = False
        stale = False

        if latest is not None:
            # 与岗位卡片（judgements.to_api）共用同一套「生效结论」和「是否过时」
            if latest["status"] == "done":
                label_row = labels.label_for(conn, user_id, latest["job_version_id"])
                verdict, model_verdict, verdict_overridden = effective_verdict(latest, label_row)
                if verdict is not None:
                    verdict_label = VERDICT_LABELS.get(verdict)
            stale = any(staleness(conn, latest).values())

        # HR note
        hr_note_val = r["hr_note_text"]
        hr_note = hr_note_val if (hr_note_val is not None and hr_note_val.strip() != "") else None
        hr_note_recorded = hr_note is not None

        # Personal status
        my_status = r["my_status"]
        status_updated_at = r["status_updated_at"]

        # Last seen time
        last_seen_at = r["last_seen"]

        # Recent updated at
        recent_updated_raw = r["recent_updated_at"]
        recent_updated_at = recent_updated_raw if recent_updated_raw else None

        url = f"https://www.zhipin.com/job_detail/{platform_job_id}.html"

        items.append(
            {
                "platform_job_id": platform_job_id,
                "title": title,
                "company_name": company_name,
                "salary_raw": salary_raw,
                "city": city,
                "verdict": verdict,
                "model_verdict": model_verdict,
                "verdict_overridden": verdict_overridden,
                "verdict_label": verdict_label,
                "stale": stale,
                "hr_note": hr_note,
                "hr_note_recorded": hr_note_recorded,
                "my_status": my_status,
                "status_updated_at": status_updated_at,
                "last_seen_at": last_seen_at,
                "recent_updated_at": recent_updated_at,
                "url": url,
            }
        )

    return {
        "counts": counts,
        "total_matches": total_matches,
        "items": items,
    }
