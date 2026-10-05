import json
import sqlite3
from typing import Any

from jet.db.store import transaction, utc_now


def get_current_profile(conn: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
    """Retrieve the most recent profile version for the given user, or None if unset."""
    row = conn.execute(
        "SELECT id, user_id, version_no, directions, keywords, cities, min_monthly_k, exclude_keywords, "
        "work_preference, background, excluded_cities, nonpref_min_monthly_k, current_city, created_at "
        "FROM profiles WHERE user_id = ? ORDER BY version_no DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return None

    preferred_cities = json.loads(row["cities"]) if row["cities"] else []
    excluded_cities = json.loads(row["excluded_cities"]) if row["excluded_cities"] else []

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "version_no": row["version_no"],
        "directions": json.loads(row["directions"]),
        "keywords": json.loads(row["keywords"]),
        "cities": preferred_cities,
        "preferred_cities": preferred_cities,
        "excluded_cities": excluded_cities,
        "min_monthly_k": row["min_monthly_k"],
        "nonpref_min_monthly_k": row["nonpref_min_monthly_k"],
        "exclude_keywords": json.loads(row["exclude_keywords"]),
        "work_preference": row["work_preference"] or "",
        "background": row["background"] or "",
        "current_city": row["current_city"] or "",
        "created_at": row["created_at"],
    }


def save_profile(conn: sqlite3.Connection, user_id: str, data: dict[str, Any]) -> tuple[int, bool]:
    """
    Save profile for user.

    Validates that directions has at least 1 non-empty item after stripping.
    Deduplicates preferred_cities/cities, excluded_cities, and keywords while preserving ordering.
    Validates work_preference and background are <= 500 characters after stripping.
    Validates min_monthly_k and nonpref_min_monthly_k >= 0.
    If the content is identical to the current version, version_no is kept and changed=False.
    Otherwise, increments version_no and returns (version_no, True).
    """
    raw_directions = data.get("directions") or []
    raw_preferred = data.get("preferred_cities")
    if raw_preferred is None:
        raw_preferred = data.get("cities") or []
    raw_excluded = data.get("excluded_cities") or []
    raw_keywords = data.get("keywords") or []
    raw_exclude = data.get("exclude_keywords") or []
    raw_min_k = data.get("min_monthly_k")
    raw_nonpref_k = data.get("nonpref_min_monthly_k")

    directions = list(dict.fromkeys(s.strip() for s in raw_directions if isinstance(s, str) and s.strip()))
    preferred_cities = list(dict.fromkeys(s.strip() for s in raw_preferred if isinstance(s, str) and s.strip()))
    excluded_cities = list(dict.fromkeys(s.strip() for s in raw_excluded if isinstance(s, str) and s.strip()))

    if not directions:
        raise ValueError("directions 至少包含一项")

    keywords = list(dict.fromkeys(s.strip() for s in raw_keywords if isinstance(s, str) and s.strip()))
    exclude_keywords = list(dict.fromkeys(s.strip() for s in raw_exclude if isinstance(s, str) and s.strip()))

    min_monthly_k: float | None = None
    if raw_min_k is not None:
        try:
            min_monthly_k = float(raw_min_k)
            if min_monthly_k < 0:
                raise ValueError("min_monthly_k 不能为负数")
        except (ValueError, TypeError) as e:
            raise ValueError(f"无效的 min_monthly_k: {raw_min_k}") from e

    nonpref_min_monthly_k: float | None = None
    if raw_nonpref_k is not None:
        try:
            nonpref_min_monthly_k = float(raw_nonpref_k)
            if nonpref_min_monthly_k < 0:
                raise ValueError("nonpref_min_monthly_k 不能为负数")
        except (ValueError, TypeError) as e:
            raise ValueError(f"无效的 nonpref_min_monthly_k: {raw_nonpref_k}") from e

    raw_work_preference = data.get("work_preference") or ""
    raw_background = data.get("background") or ""
    raw_current_city = data.get("current_city") or ""
    work_preference = str(raw_work_preference).strip()
    background = str(raw_background).strip()
    current_city = str(raw_current_city).strip()

    if len(work_preference) > 500:
        raise ValueError("work_preference 不能超过 500 字")
    if len(background) > 500:
        raise ValueError("background 不能超过 500 字")
    if len(current_city) > 20:
        raise ValueError("current_city 不能超过 20 字")

    current = get_current_profile(conn, user_id)
    if current is not None:
        if (
            current["directions"] == directions
            and current["keywords"] == keywords
            and current["preferred_cities"] == preferred_cities
            and current["excluded_cities"] == excluded_cities
            and current["min_monthly_k"] == min_monthly_k
            and current["nonpref_min_monthly_k"] == nonpref_min_monthly_k
            and current["exclude_keywords"] == exclude_keywords
            and current["work_preference"] == work_preference
            and current["background"] == background
            and current.get("current_city", "") == current_city
        ):
            return current["version_no"], False
        next_version = current["version_no"] + 1
    else:
        next_version = 1

    now_str = utc_now()
    with transaction(conn):
        conn.execute(
            "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, min_monthly_k, "
            "exclude_keywords, work_preference, background, excluded_cities, nonpref_min_monthly_k, current_city, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                next_version,
                json.dumps(directions, ensure_ascii=False),
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(preferred_cities, ensure_ascii=False),
                min_monthly_k,
                json.dumps(exclude_keywords, ensure_ascii=False),
                work_preference,
                background,
                json.dumps(excluded_cities, ensure_ascii=False),
                nonpref_min_monthly_k,
                current_city,
                now_str,
            ),
        )

    return next_version, True
