from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from fastapi import Request

from jet.db.migrations import (
    LATEST_VERSION,
    MigrationResult,
    migrate_to_v2,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    migrate_to_v6,
    migrate_to_v7,
    migrate_to_v8,
    migrate_to_v9,
    migrate_to_v10,
)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class Row(sqlite3.Row):
    """sqlite3.Row that also supports dict-style .get(), so domain code can treat rows and dicts alike."""

    def get(self, key, default=None):
        try:
            return self[key]
        except (IndexError, KeyError):
            return default


def utc_now() -> str:
    """Return current UTC time in ISO-8601 format with second precision and 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day_bounds_utc(now: datetime | None = None) -> tuple[str, str, str]:
    """Calculate local day start and end in UTC ISO-8601, returning (start_utc, end_utc, local_date_str)."""
    local_now = (now or datetime.now()).astimezone()

    local_date = local_now.date()
    date_str = local_date.isoformat()
    local_tz = local_now.tzinfo

    start_local = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)

    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    return (
        start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        date_str,
    )


def _ensure_current_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Idempotently create missing tables / indexes and add nullable columns (no version bump).

    新表、新索引写进 schema.sql；这里只补「纯新增、可为空（或带常量默认值）」的列。
    其他结构改动走 migrations.py 里带编号和备份的迁移，规矩见 migrations.py 开头。
    """
    conn.executescript(schema_sql)
    job_cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "company_name" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_name TEXT")
    if "company_industry" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_industry TEXT")
    if "experience_req" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN experience_req TEXT")
    if "degree_req" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN degree_req TEXT")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
    if "verdict_reason" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN verdict_reason TEXT")
    if "hr_questions" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN hr_questions TEXT")
    if "review" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN review TEXT")
    if "origin" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN origin TEXT")
    if "resume_direction" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN resume_direction TEXT")
    if "resume_reason" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN resume_reason TEXT")
    label_cols = [r[1] for r in conn.execute("PRAGMA table_info(labels)").fetchall()]
    if "work_subtype" not in label_cols:
        conn.execute("ALTER TABLE labels ADD COLUMN work_subtype TEXT")
    if "secondary_work_types" not in label_cols:
        conn.execute("ALTER TABLE labels ADD COLUMN secondary_work_types TEXT")
    if "work_intensity" not in label_cols:
        conn.execute("ALTER TABLE labels ADD COLUMN work_intensity TEXT")

    profile_cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
    if "excluded_cities" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN excluded_cities TEXT NOT NULL DEFAULT '[]'")
    if "nonpref_min_monthly_k" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN nonpref_min_monthly_k REAL")
    if "current_city" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN current_city TEXT NOT NULL DEFAULT ''")

    ref_cols = [r[1] for r in conn.execute("PRAGMA table_info(reference_labels)").fetchall()]
    if "overall_void" not in ref_cols:
        conn.execute("ALTER TABLE reference_labels ADD COLUMN overall_void INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE reference_labels SET overall_void = 1")

    settings_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "daily_assist_limit" not in settings_cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_assist_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500)"
        )
    if "daily_prejudge_limit" not in settings_cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_prejudge_limit INTEGER NOT NULL DEFAULT 20 CHECK(daily_prejudge_limit BETWEEN 0 AND 200)"
        )
    if "daily_resume_profile_limit" not in settings_cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_resume_profile_limit INTEGER NOT NULL DEFAULT 10 CHECK(daily_resume_profile_limit BETWEEN 0 AND 50)"
        )


def init_db(data_dir: Path) -> MigrationResult | None:
    """
    Initialize SQLite database directory, WAL mode, schema version LATEST_VERSION, and initial user rows.

    Returns MigrationResult if migration was performed, or None otherwise.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "jet.db"

    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = Row

    migration_res: MigrationResult | None = None
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")

        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        has_tables = cur is not None
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

        schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")

        if not has_tables:
            # Clean database: execute schema and set user_version
            conn.executescript(schema_sql)
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        elif user_version < LATEST_VERSION:
            # Existing database with old version: close and migrate
            conn.close()
            start_version = user_version
            cleared_all: list[tuple[int, str, str, str]] = []
            # 连续迁移时每一步都会整库备份；提示里给第一份，即保存着升级前原始数据的那份
            first_backup = ""
            if user_version < 2:
                res_v2 = migrate_to_v2(db_path)
                first_backup = first_backup or res_v2.backup_filename
            if user_version < 3:
                res_v3 = migrate_v2_to_v3(db_path)
                first_backup = first_backup or res_v3.backup_filename
                cleared_all = res_v3.cleared
            if user_version < 4:
                res_v4 = migrate_v3_to_v4(db_path)
                first_backup = first_backup or res_v4.backup_filename
            if user_version < 5:
                res_v5 = migrate_v4_to_v5(db_path)
                first_backup = first_backup or res_v5.backup_filename
            if user_version < 6:
                res_v6 = migrate_to_v6(db_path)
                first_backup = first_backup or res_v6.backup_filename
            if user_version < 7:
                res_v7 = migrate_to_v7(db_path)
                first_backup = first_backup or res_v7.backup_filename
            if user_version < 8:
                res_v8 = migrate_to_v8(db_path)
                first_backup = first_backup or res_v8.backup_filename
            if user_version < 9:
                res_v9 = migrate_to_v9(db_path)
                first_backup = first_backup or res_v9.backup_filename
            if user_version < 10:
                res_v10 = migrate_to_v10(db_path)
                first_backup = first_backup or res_v10.backup_filename
            migration_res = MigrationResult(
                from_version=start_version,
                to_version=LATEST_VERSION,
                backup_filename=first_backup,
                cleared=cleared_all,
            )
            conn = sqlite3.connect(
                str(db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")

        # 迁移之后也要执行：补建之后新增的表与可空列
        _ensure_current_schema(conn, schema_sql)

        # Ensure user 'me' and its user_settings exist
        now_str = utc_now()
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
            (now_str,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_settings (user_id, daily_llm_limit) VALUES ('me', 150)",
        )
    finally:
        conn.close()

    return migration_res


def connect(data_dir: Path) -> sqlite3.Connection:
    """Open a new connection to an existing SQLite database."""
    db_path = data_dir / "jet.db"
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def open_db(data_dir: Path) -> sqlite3.Connection:
    """Initialize DB and return a connection (compatibility with existing tests)."""
    init_db(data_dir)
    return connect(data_dir)


def get_conn(request: Request) -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency yielding a dedicated SQLite connection per request."""
    conn = connect(request.app.state.settings.data_dir)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Transaction context manager for explicit BEGIN [IMMEDIATE] / COMMIT / ROLLBACK."""
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reset_pending_reviews_to_failed(conn: sqlite3.Connection) -> int:
    """Reset any judgements with review outcome='pending' to 'failed' (e.g. on Jet restart)."""
    rows = conn.execute(
        "SELECT id, review FROM judgements WHERE review IS NOT NULL AND review LIKE '%\"pending\"%'"
    ).fetchall()
    to_update = []
    for row in rows:
        try:
            data = json.loads(row["review"])
            if isinstance(data, dict) and data.get("outcome") == "pending":
                data["outcome"] = "failed"
                data["error"] = "Jet 重启，复核未完成"
                to_update.append((json.dumps(data, ensure_ascii=False), row["id"]))
        except Exception:
            continue

    if not to_update:
        return 0

    if conn.in_transaction:
        for new_json, j_id in to_update:
            conn.execute("UPDATE judgements SET review = ? WHERE id = ?", (new_json, j_id))
    else:
        with transaction(conn):
            for new_json, j_id in to_update:
                conn.execute("UPDATE judgements SET review = ? WHERE id = ?", (new_json, j_id))
    return len(to_update)


def reset_running_to_interrupted(conn: sqlite3.Connection) -> int:
    """Reset any hanging 'running' judgements to 'interrupted' and pending reviews to 'failed'."""
    reset_pending_reviews_to_failed(conn)
    cur = conn.execute("UPDATE judgements SET status = 'interrupted' WHERE status = 'running'")
    return cur.rowcount


# 带 user_id 的个人数据表，启动自检逐表检查归属（constitution 原则 II）；新增个人数据表时要加进来
OWNED_TABLES: tuple[str, ...] = (
    "profiles",
    "views",
    "judgements",
    "labels",
    "llm_calls",
    "pairings",
    "user_settings",
    "hr_notes",
    "reference_labels",
    "job_status",
    "job_status_events",
    "experience_items",
    "llm_consents",
    "job_chat_seen",
    "prejudgements",
    "resume_slots",
    "strict_industry_selection",
)


def count_ownerless_rows(conn: sqlite3.Connection) -> int:
    """Count rows across personal tables where user_id IS NULL or user_id NOT IN users."""
    total = 0
    for tbl in OWNED_TABLES:
        has_tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not has_tbl:
            continue
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE user_id IS NULL OR user_id NOT IN (SELECT id FROM users)"
        )
        row = cur.fetchone()
        if row:
            total += row[0]
    return total
