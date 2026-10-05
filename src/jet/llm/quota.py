import sqlite3
from typing import Any, Mapping

from jet.config import Settings
from jet.db.store import local_day_bounds_utc, transaction, utc_now


# 每种用途的每日上限：user_settings 里的列名，以及没有设置行时的兜底值（与 config.py 的默认值一致）。
# 加新用途只改这里。不在表里的用途按判断（judge）的上限算（eval 按批次计数，见 reserve）。
DAILY_LIMITS: dict[str, tuple[str, int]] = {
    "judge": ("daily_llm_limit", 150),
    "assist": ("daily_assist_limit", 50),
    "prejudge": ("daily_prejudge_limit", 20),
    "resume_profile": ("daily_resume_profile_limit", 10),
}


def _daily_limit(conn: sqlite3.Connection, user_id: str, purpose: str) -> int:
    column, default = DAILY_LIMITS.get(purpose, DAILY_LIMITS["judge"])
    row = conn.execute(f"SELECT {column} FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[column]) if row and row[column] is not None else default


def _billed_today(conn: sqlite3.Connection, user_id: str, purpose: str) -> int:
    start_utc, end_utc, _ = local_day_bounds_utc()
    used_row = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE user_id = ? AND billed = 1 AND started_at >= ? AND started_at < ? AND purpose = ?",
        (user_id, start_utc, end_utc, purpose),
    ).fetchone()
    return used_row[0] if used_row else 0


def usage_today(conn: sqlite3.Connection, user_id: str, purpose: str = "judge") -> dict[str, Any]:
    """Calculate today's LLM call quota usage for the user for one purpose (see DAILY_LIMITS)."""
    _, _, date_str = local_day_bounds_utc()
    limit = _daily_limit(conn, user_id, purpose)
    used = _billed_today(conn, user_id, purpose)
    remaining = max(limit - used, 0) if limit > 0 else 0

    return {
        "date": date_str,
        "limit": limit,
        "used": used,
        "remaining": remaining,
    }


def remaining_today(conn: sqlite3.Connection, user_id: str, purpose: str = "judge") -> int:
    """Calculate remaining LLM call quota for the user today for one purpose (see DAILY_LIMITS)."""
    return int(usage_today(conn, user_id, purpose)["remaining"])



def reserve(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    judgement_id: int | None = None,
    provider: str,
    model: str,
    purpose: str = "judge",
    eval_run_id: str | None = None,
    limit_override: int | None = None,
) -> int | None:
    """
    Reserve quota for an LLM call inside a BEGIN IMMEDIATE transaction.

    For purpose='eval', counts billed=1 rows with the same eval_run_id and compares to limit_override (default 300).
    Other purposes count today's billed=1 rows of that purpose and compare to its DAILY_LIMITS column.
    Returns the call ID if reserved, or None if quota is exhausted.
    """
    with transaction(conn, immediate=True):
        if purpose == "eval":
            # 评测按批次（eval_run_id）计数，不按天
            limit = limit_override if limit_override is not None else 300
            if limit <= 0:
                return None
            used_row = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE user_id = ? AND billed = 1 AND purpose = 'eval' AND eval_run_id = ?",
                (user_id, eval_run_id),
            ).fetchone()
            used = used_row[0] if used_row else 0
        else:
            # 不在 DAILY_LIMITS 里的用途按判断的上限和计数（与原来的兜底分支一致）
            counted = purpose if purpose in DAILY_LIMITS else "judge"
            limit = limit_override if limit_override is not None else _daily_limit(conn, user_id, counted)
            if limit <= 0:
                return None
            used = _billed_today(conn, user_id, counted)
        if used >= limit:
            return None

        now_str = utc_now()
        cur = conn.execute(
            "INSERT INTO llm_calls (user_id, judgement_id, purpose, eval_run_id, provider, model, started_at, outcome, billed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', 1)",
            (user_id, judgement_id, purpose, eval_run_id, provider, model, now_str),
        )
        return cur.lastrowid


def finish(
    conn: sqlite3.Connection,
    call_id: int,
    *,
    outcome: str,
    usage: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
) -> float | None:
    """
    Record completion of an LLM call.

    If outcome is 'not_sent', billed is updated to 0 (refunding quota).
    Otherwise, billed remains 1. Computes and returns estimated cost in CNY.
    """
    billed = 0 if outcome == "not_sent" else 1
    finished_at = utc_now()

    input_tokens = None
    cached_tokens = None
    output_tokens = None
    cost_cny = None

    if usage:
        input_tokens = usage.get("prompt_tokens")
        cached_tokens = usage.get("prompt_cache_hit_tokens")
        if cached_tokens is None:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached_tokens = details.get("cached_tokens")
        if cached_tokens is None and input_tokens is not None:
            cached_tokens = 0

        output_tokens = usage.get("completion_tokens")

    if input_tokens is not None and output_tokens is not None and settings is not None:
        cached = cached_tokens or 0
        uncached = max(input_tokens - cached, 0)
        cost_cny = (
            uncached * settings.price_input_per_m
            + cached * settings.price_cached_per_m
            + output_tokens * settings.price_output_per_m
        ) / 1_000_000.0

    with transaction(conn):
        conn.execute(
            "UPDATE llm_calls SET finished_at = ?, outcome = ?, billed = ?, input_tokens = ?, "
            "cached_tokens = ?, output_tokens = ?, cost_cny = ? WHERE id = ?",
            (finished_at, outcome, billed, input_tokens, cached_tokens, output_tokens, cost_cny, call_id),
        )

    return cost_cny
