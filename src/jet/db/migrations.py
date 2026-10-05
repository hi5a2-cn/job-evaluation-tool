"""数据库结构迁移。

改表规矩（2026-10-05 体检第 49 条定下）：
- 新表、新索引（写进 schema.sql，启动时 CREATE ... IF NOT EXISTS）和纯新增、可为空（或带常量默认值）的列
  （在 store._ensure_current_schema 里「缺列就补」）：不升结构版本号、不备份；
- 其余改动（改已有列的类型、默认值或约束，清洗或搬移已有数据等）：一律写带编号、整库备份的迁移（本文件），
  并升 PRAGMA user_version。
旧迁移不回头改。v10（jobs.experience_req / degree_req）按这条规矩本可只「缺列就补」，
但它在规矩定下前已在用户数据库上执行过，保留以免版本号对不上。
v2–v10 各自抄了一份「读版本、整库备份、开新连接、行数和外键校验、回滚」的样板（体检第 80 条）；它们都已执行过，不再改。
下次真要写新的编号迁移时，先把这套样板抽成一个公共骨架函数给新迁移用，别再整段复制；同时把 LATEST_VERSION 加一。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from jet.domain.industry import read_strict_industries
from jet.domain.taxonomy import (
    CATEGORIES,
    LEGACY_WORK_TYPE_MAP,
    VERDICTS,
    WORK_INTENSITY,
    normalize_secondary,
)


class MigrationError(Exception):
    """Raised when database schema migration fails."""
    pass


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    backup_filename: str
    cleared: list[tuple[int, str, str, str]] = field(default_factory=list)


def migrate_to_v2(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 0 or 1 to version 2 (US7).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - ALTER TABLE profiles ADD COLUMN work_preference / background.
       - CREATE TABLE labels.
       - Rebuild judgements (verdict CHECK includes unsure, prompt_version, engine, facts, derivation).
       - Rebuild llm_calls (judgement_id nullable, purpose, eval_run_id).
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - COMMIT.
    5. PRAGMA user_version = 2, PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
    ]
    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Alter profiles table
        conn.execute("ALTER TABLE profiles ADD COLUMN work_preference TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE profiles ADD COLUMN background TEXT NOT NULL DEFAULT ''")

        # Create labels table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_version_id INTEGER NOT NULL,
            judgement_id INTEGER,
            work_type TEXT CHECK(work_type IS NULL OR work_type IN ('数据', '运营', '营销', '销售', '客服', '技术支持', '其他')),
            secondary_work_types TEXT,
            sales_level TEXT CHECK(sales_level IS NULL OR sales_level IN ('高', '中', '低')),
            experience_fit TEXT CHECK(experience_fit IS NULL OR experience_fit IN ('满足', '差一点', '不满足', '无法判断')),
            overtime TEXT CHECK(overtime IS NULL OR overtime IN ('有', '未提及', '明确双休或不加班')),
            overall TEXT CHECK(overall IS NULL OR overall IN ('fit', 'unsure', 'unfit')),
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, job_version_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_version_id) REFERENCES job_versions(id),
            FOREIGN KEY (judgement_id) REFERENCES judgements(id)
        )
        """)

        # Rebuild judgements table
        conn.execute("""
        CREATE TABLE judgements_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            job_version_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed', 'quota_exhausted', 'interrupted')),
            verdict TEXT CHECK(verdict IS NULL OR verdict IN ('fit', 'unsure', 'unfit')),
            source TEXT CHECK(source IS NULL OR source IN ('rule', 'llm')),
            reasons TEXT,
            prompt_version TEXT NOT NULL DEFAULT 'v3',
            engine TEXT,
            facts TEXT,
            derivation TEXT,
            verdict_reason TEXT,
            rule_result TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            superseded_by INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_id) REFERENCES jobs(id),
            FOREIGN KEY (job_version_id) REFERENCES job_versions(id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id),
            FOREIGN KEY (superseded_by) REFERENCES judgements(id) DEFERRABLE INITIALLY DEFERRED
        )
        """)

        conn.execute("""
        INSERT INTO judgements_new (
            id, user_id, job_id, job_version_id, profile_id, status, verdict, source,
            reasons, prompt_version, engine, facts, derivation, verdict_reason, rule_result, error,
            created_at, finished_at, superseded_by
        )
        SELECT
            id, user_id, job_id, job_version_id, profile_id, status, verdict, source,
            reasons,
            CASE WHEN source='rule' THEN 'rule' ELSE 'v1' END,
            NULL, NULL, NULL, NULL,
            rule_result, error, created_at, finished_at, superseded_by
        FROM judgements
        """)

        conn.execute("DROP TABLE judgements")
        conn.execute("ALTER TABLE judgements_new RENAME TO judgements")
        conn.execute("""
        CREATE UNIQUE INDEX idx_judgements_active
        ON judgements(user_id, job_version_id, profile_id)
        WHERE superseded_by IS NULL
        """)

        # Rebuild llm_calls table
        conn.execute("""
        CREATE TABLE llm_calls_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            judgement_id INTEGER,
            purpose TEXT NOT NULL DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval')),
            eval_run_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            outcome TEXT NOT NULL,
            billed INTEGER NOT NULL,
            input_tokens INTEGER,
            cached_tokens INTEGER,
            output_tokens INTEGER,
            cost_cny REAL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (judgement_id) REFERENCES judgements(id)
        )
        """)

        conn.execute("""
        INSERT INTO llm_calls_new (
            id, user_id, judgement_id, purpose, eval_run_id, provider, model,
            started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
            output_tokens, cost_cny
        )
        SELECT
            id, user_id, judgement_id, 'judge', NULL, provider, model,
            started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
            output_tokens, cost_cny
        FROM llm_calls
        """)

        conn.execute("DROP TABLE llm_calls")
        conn.execute("ALTER TABLE llm_calls_new RENAME TO llm_calls")

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v2 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set user_version=2 and foreign_keys=ON
    conn.execute("PRAGMA user_version = 2")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(from_version=old_version, to_version=2, backup_filename=backup_filename)


def migrate_v2_to_v3(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 2 to version 3 (two-layer work_type, work_intensity, 4-tier verdicts).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables (including labels and judgements).
       - Rebuild judgements: verdict CHECK includes apply, try, check, skip (keeping fit, unsure, unfit).
       - Rebuild labels: new columns and CHECK constraints, convert legacy data, collect cleared fields.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - COMMIT.
    5. PRAGMA user_version = 3, PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    cleared_list: list[tuple[int, str, str, str]] = []

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Rebuild judgements table
        conn.execute("""
        CREATE TABLE judgements_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            job_version_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed', 'quota_exhausted', 'interrupted')),
            verdict TEXT CHECK(verdict IS NULL OR verdict IN ('fit', 'unsure', 'unfit', 'apply', 'try', 'check', 'skip')),
            source TEXT CHECK(source IS NULL OR source IN ('rule', 'llm')),
            reasons TEXT,
            prompt_version TEXT NOT NULL DEFAULT 'v4',
            engine TEXT,
            facts TEXT,
            derivation TEXT,
            verdict_reason TEXT,
            rule_result TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            superseded_by INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_id) REFERENCES jobs(id),
            FOREIGN KEY (job_version_id) REFERENCES job_versions(id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id),
            FOREIGN KEY (superseded_by) REFERENCES judgements(id) DEFERRABLE INITIALLY DEFERRED
        )
        """)

        conn.execute("""
        INSERT INTO judgements_new (
            id, user_id, job_id, job_version_id, profile_id, status, verdict, source,
            reasons, prompt_version, engine, facts, derivation, verdict_reason, rule_result, error,
            created_at, finished_at, superseded_by
        )
        SELECT
            id, user_id, job_id, job_version_id, profile_id, status, verdict, source,
            reasons, prompt_version, engine, facts, derivation, verdict_reason, rule_result, error,
            created_at, finished_at, superseded_by
        FROM judgements
        """)

        conn.execute("DROP TABLE judgements")
        conn.execute("ALTER TABLE judgements_new RENAME TO judgements")
        conn.execute("""
        CREATE UNIQUE INDEX idx_judgements_active
        ON judgements(user_id, job_version_id, profile_id)
        WHERE superseded_by IS NULL
        """)

        # Rebuild labels table
        conn.execute("""
        CREATE TABLE labels_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_version_id INTEGER NOT NULL,
            judgement_id INTEGER,
            work_type TEXT CHECK(work_type IS NULL OR work_type IN ('数据与技术', '运营', '产品与项目', '内容与设计', '市场与销售', '科研与专业', '职能', '其他')),
            work_subtype TEXT,
            secondary_work_types TEXT,
            sales_level TEXT CHECK(sales_level IS NULL OR sales_level IN ('高', '中', '低')),
            experience_fit TEXT CHECK(experience_fit IS NULL OR experience_fit IN ('满足', '差一点', '不满足', '无法判断')),
            work_intensity TEXT CHECK(work_intensity IS NULL OR work_intensity IN ('高强度', '单休', '大小周', '双休', '未提及')),
            overall TEXT CHECK(overall IS NULL OR overall IN ('apply', 'try', 'check', 'skip')),
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, job_version_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (job_version_id) REFERENCES job_versions(id),
            FOREIGN KEY (judgement_id) REFERENCES judgements(id)
        )
        """)

        # Fetch old labels joined with job_versions to obtain job title for cleared fields
        old_labels = conn.execute("""
            SELECT l.*, jv.title as job_title
            FROM labels l
            LEFT JOIN job_versions jv ON l.job_version_id = jv.id
            ORDER BY l.id ASC
        """).fetchall()

        for row in old_labels:
            old_id = row["id"]
            job_title = row["job_title"] or "未知岗位"

            # 1. work_type & work_subtype conversion
            old_wt = row["work_type"]
            if old_wt in LEGACY_WORK_TYPE_MAP:
                new_wt, new_sub = LEGACY_WORK_TYPE_MAP[old_wt]
            elif old_wt in CATEGORIES:
                new_wt = old_wt
                new_sub = row["work_subtype"] if "work_subtype" in row.keys() else None
            else:
                new_wt = None
                new_sub = None

            # 2. secondary_work_types conversion
            sec_raw = row["secondary_work_types"] if "secondary_work_types" in row.keys() else None
            new_sec_json = None
            if sec_raw:
                try:
                    old_sec = json.loads(sec_raw) if isinstance(sec_raw, str) else sec_raw
                    if isinstance(old_sec, list):
                        mapped_sec = []
                        for it in old_sec:
                            if isinstance(it, str) and it in LEGACY_WORK_TYPE_MAP:
                                cat, sub = LEGACY_WORK_TYPE_MAP[it]
                                mapped_sec.append({"category": cat, "subtype": sub})
                            elif isinstance(it, dict) and "category" in it:
                                mapped_sec.append(it)
                            elif isinstance(it, (tuple, list)):
                                mapped_sec.append(it)
                        normalized = normalize_secondary(mapped_sec, primary_category=new_wt, primary_subtype=new_sub)
                        if normalized:
                            new_sec_json = json.dumps(normalized, ensure_ascii=False)
                except Exception:
                    new_sec_json = None

            # 3. overtime -> work_intensity conversion
            old_ot = row["overtime"] if "overtime" in row.keys() else None
            new_wi = None
            if old_ot == "明确双休或不加班":
                new_wi = "双休"
            elif old_ot == "未提及":
                new_wi = "未提及"
            elif old_ot == "有":
                new_wi = None
                cleared_list.append((old_id, job_title, "工作强度", old_ot))
            elif old_ot in WORK_INTENSITY:
                new_wi = old_ot
            elif old_ot is not None:
                # Other non-null legacy value cannot be mapped -> clear
                new_wi = None
                cleared_list.append((old_id, job_title, "工作强度", old_ot))

            # 4. overall conversion
            old_overall = row["overall"]
            new_overall = None
            if old_overall == "fit":
                new_overall = "apply"
            elif old_overall == "unfit":
                new_overall = "skip"
            elif old_overall == "unsure":
                new_overall = None
                cleared_list.append((old_id, job_title, "总体结论", old_overall))
            elif old_overall in VERDICTS:
                new_overall = old_overall
            elif old_overall is not None:
                new_overall = None
                cleared_list.append((old_id, job_title, "总体结论", old_overall))

            conn.execute("""
            INSERT INTO labels_new (
                id, user_id, job_version_id, judgement_id, work_type, work_subtype,
                secondary_work_types, sales_level, experience_fit, work_intensity,
                overall, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                old_id,
                row["user_id"],
                row["job_version_id"],
                row["judgement_id"],
                new_wt,
                new_sub,
                new_sec_json,
                row["sales_level"],
                row["experience_fit"],
                new_wi,
                new_overall,
                row["note"],
                row["created_at"],
                row["updated_at"],
            ))

        conn.execute("DROP TABLE labels")
        conn.execute("ALTER TABLE labels_new RENAME TO labels")

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v3 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set user_version=3 and foreign_keys=ON
    conn.execute("PRAGMA user_version = 3")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=3,
        backup_filename=backup_filename,
        cleared=cleared_list,
    )


def migrate_v3_to_v4(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 3 to version 4 (add origin column to judgements).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - ALTER TABLE judgements ADD COLUMN origin TEXT.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - COMMIT.
    5. PRAGMA user_version = 4, PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in ["hr_notes", "reference_labels", "job_status", "job_status_events"]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
        if "origin" not in cols:
            conn.execute("ALTER TABLE judgements ADD COLUMN origin TEXT")

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v4 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set user_version=4 and foreign_keys=ON
    conn.execute("PRAGMA user_version = 4")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=4,
        backup_filename=backup_filename,
        cleared=[],
    )


def _apply_v5_schema_changes(conn: sqlite3.Connection) -> None:
    # Rebuild llm_calls table
    conn.execute("""
    CREATE TABLE llm_calls_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        judgement_id INTEGER,
        purpose TEXT NOT NULL DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval', 'assist')),
        eval_run_id TEXT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT NOT NULL,
        billed INTEGER NOT NULL,
        input_tokens INTEGER,
        cached_tokens INTEGER,
        output_tokens INTEGER,
        cost_cny REAL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (judgement_id) REFERENCES judgements(id)
    )
    """)

    conn.execute("""
    INSERT INTO llm_calls_new (
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    )
    SELECT
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    FROM llm_calls
    """)

    conn.execute("DROP TABLE llm_calls")
    conn.execute("ALTER TABLE llm_calls_new RENAME TO llm_calls")

    # Create experience_items table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS experience_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        item_no INTEGER NOT NULL CHECK(item_no BETWEEN 1 AND 10),
        content TEXT NOT NULL CHECK(length(content) <= 200 AND length(trim(content)) > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, item_no),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # Create llm_consents table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS llm_consents (
        user_id TEXT NOT NULL PRIMARY KEY,
        consented_at TEXT NOT NULL,
        revoked_at TEXT,
        fields_version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # Add daily_assist_limit to user_settings if not present
    cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "daily_assist_limit" not in cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_assist_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500)"
        )


def migrate_v4_to_v5(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 4 to version 5 (US1).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - Rebuild llm_calls: purpose CHECK includes 'assist'.
       - Create experience_items table.
       - Create llm_consents table.
       - ALTER TABLE user_settings ADD COLUMN daily_assist_limit.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - COMMIT.
    5. PRAGMA user_version = 5, PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in ["hr_notes", "reference_labels", "job_status", "job_status_events"]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v5_schema_changes(conn)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v5 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set user_version=5 and foreign_keys=ON
    conn.execute("PRAGMA user_version = 5")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=5,
        backup_filename=backup_filename,
        cleared=[],
    )


def _apply_v6_schema_changes(conn: sqlite3.Connection) -> None:
    # Add company_industry to jobs table if not present
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "company_industry" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_industry TEXT")


def migrate_to_v6(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 5 to version 6 (FR-057: jobs.company_industry).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - ALTER TABLE jobs ADD COLUMN company_industry TEXT.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - COMMIT.
    5. PRAGMA user_version = 6, PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in [
        "hr_notes",
        "reference_labels",
        "job_status",
        "job_status_events",
        "experience_items",
        "llm_consents",
    ]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v6_schema_changes(conn)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v6 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set user_version=6 and foreign_keys=ON
    conn.execute("PRAGMA user_version = 6")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=6,
        backup_filename=backup_filename,
        cleared=[],
    )


migrate_v5_to_v6 = migrate_to_v6


def _apply_v7_schema_changes(conn: sqlite3.Connection, rules_path: Path | str | None = None) -> None:
    # 1. 创建 strict_industry_selection 表
    conn.execute("""
    CREATE TABLE IF NOT EXISTS strict_industry_selection (
        user_id TEXT NOT NULL,
        industry TEXT NOT NULL,
        PRIMARY KEY(user_id, industry),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # 2. 为迁移前已存在的每个用户插入规则文件当前全部行业（以规则文件实际内容为准，读文件而不是写死）
    industries = read_strict_industries(rules_path=rules_path)
    user_rows = conn.execute("SELECT id FROM users").fetchall()
    for u in user_rows:
        u_id = u[0]
        for ind in industries:
            conn.execute(
                "INSERT OR IGNORE INTO strict_industry_selection (user_id, industry) VALUES (?, ?)",
                (u_id, ind),
            )


def migrate_to_v7(db_path: Path, rules_path: Path | str | None = None) -> MigrationResult:
    """
    Migrate database schema from version 6 to version 7 (FR-010–FR-014: strict_industry_selection).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - Create strict_industry_selection table.
       - Insert all available industries for existing users.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - PRAGMA user_version = 7.
       - COMMIT.
    5. PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in [
        "hr_notes",
        "reference_labels",
        "job_status",
        "job_status_events",
        "experience_items",
        "llm_consents",
        "resume_slots",
    ]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v7_schema_changes(conn, rules_path=rules_path)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("PRAGMA user_version = 7")
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v7 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set foreign_keys=ON
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=7,
        backup_filename=backup_filename,
        cleared=[],
    )


migrate_v6_to_v7 = migrate_to_v7


def _apply_v8_schema_changes(conn: sqlite3.Connection) -> None:
    # 1. user_settings 新增 daily_prejudge_limit 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "daily_prejudge_limit" not in cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_prejudge_limit INTEGER NOT NULL DEFAULT 20 CHECK(daily_prejudge_limit BETWEEN 0 AND 200)"
        )

    # 2. 重建 llm_calls 表以扩展 purpose CHECK 约束加入 'prejudge'
    conn.execute("""
    CREATE TABLE llm_calls_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        judgement_id INTEGER,
        purpose TEXT NOT NULL DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval', 'assist', 'prejudge')),
        eval_run_id TEXT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT NOT NULL,
        billed INTEGER NOT NULL,
        input_tokens INTEGER,
        cached_tokens INTEGER,
        output_tokens INTEGER,
        cost_cny REAL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (judgement_id) REFERENCES judgements(id)
    )
    """)

    conn.execute("""
    INSERT INTO llm_calls_new (
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    )
    SELECT
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    FROM llm_calls
    """)

    conn.execute("DROP TABLE llm_calls")
    conn.execute("ALTER TABLE llm_calls_new RENAME TO llm_calls")

    # 3. 创建 prejudgements 表与索引
    conn.execute("""
    CREATE TABLE IF NOT EXISTS prejudgements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        level TEXT NOT NULL CHECK(level IN ('open', 'neutral', 'skip')),
        reason TEXT NOT NULL,
        profile_id INTEGER NOT NULL,
        engine TEXT,
        llm_call_id INTEGER,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, job_id, profile_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (job_id) REFERENCES jobs(id),
        FOREIGN KEY (profile_id) REFERENCES profiles(id),
        FOREIGN KEY (llm_call_id) REFERENCES llm_calls(id)
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_prejudgements_user_job ON prejudgements(user_id, job_id)
    """)


def migrate_to_v8(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 7 to version 8 (008-list-prejudge).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - Alter user_settings ADD COLUMN daily_prejudge_limit.
       - Rebuild llm_calls table with 'prejudge' in purpose CHECK constraint.
       - Create prejudgements table and idx_prejudgements_user_job.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - PRAGMA user_version = 8.
       - COMMIT.
    5. PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in [
        "hr_notes",
        "reference_labels",
        "job_status",
        "job_status_events",
        "experience_items",
        "llm_consents",
        "resume_slots",
        "strict_industry_selection",
        "job_chat_seen",
    ]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v8_schema_changes(conn)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("PRAGMA user_version = 8")
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v8 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set foreign_keys=ON
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=8,
        backup_filename=backup_filename,
        cleared=[],
    )


def _apply_v9_schema_changes(conn: sqlite3.Connection) -> None:
    # 1. user_settings 新增 daily_resume_profile_limit 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
    if "daily_resume_profile_limit" not in cols:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN daily_resume_profile_limit INTEGER NOT NULL DEFAULT 10 CHECK(daily_resume_profile_limit BETWEEN 0 AND 50)"
        )

    # 2. 重建 llm_calls 表以扩展 purpose CHECK 约束加入 'resume_profile'
    conn.execute("""
    CREATE TABLE llm_calls_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        judgement_id INTEGER,
        purpose TEXT NOT NULL DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval', 'assist', 'prejudge', 'resume_profile')),
        eval_run_id TEXT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT NOT NULL,
        billed INTEGER NOT NULL,
        input_tokens INTEGER,
        cached_tokens INTEGER,
        output_tokens INTEGER,
        cost_cny REAL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (judgement_id) REFERENCES judgements(id)
    )
    """)

    conn.execute("""
    INSERT INTO llm_calls_new (
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    )
    SELECT
        id, user_id, judgement_id, purpose, eval_run_id, provider, model,
        started_at, finished_at, outcome, billed, input_tokens, cached_tokens,
        output_tokens, cost_cny
    FROM llm_calls
    """)

    conn.execute("DROP TABLE llm_calls")
    conn.execute("ALTER TABLE llm_calls_new RENAME TO llm_calls")

    # 3. 重建或创建 resume_slots 表以支持 resume_text 及 profile (旧 job_types 迁移为 profile)
    has_resume_slots = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resume_slots'"
    ).fetchone() is not None

    if has_resume_slots:
        r_cols = [r[1] for r in conn.execute("PRAGMA table_info(resume_slots)").fetchall()]
        if "profile" not in r_cols:
            conn.execute("""
            CREATE TABLE resume_slots_new (
                user_id TEXT NOT NULL,
                slot INTEGER NOT NULL CHECK(slot IN (1, 2, 3)),
                name TEXT NOT NULL CHECK(length(name) <= 100),
                resume_text TEXT,
                profile TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, slot),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """)

            conn.execute("""
            INSERT INTO resume_slots_new (
                user_id, slot, name, resume_text, profile, updated_at
            )
            SELECT
                user_id, slot, name, NULL, job_types, updated_at
            FROM resume_slots
            """)

            conn.execute("DROP TABLE resume_slots")
            conn.execute("ALTER TABLE resume_slots_new RENAME TO resume_slots")
    else:
        conn.execute("""
        CREATE TABLE resume_slots (
            user_id TEXT NOT NULL,
            slot INTEGER NOT NULL CHECK(slot IN (1, 2, 3)),
            name TEXT NOT NULL CHECK(length(name) <= 100),
            resume_text TEXT,
            profile TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, slot),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)


def migrate_to_v9(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 8 to version 9 (009-resume-pdf).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - Alter user_settings ADD COLUMN daily_resume_profile_limit.
       - Rebuild llm_calls table with 'resume_profile' in purpose CHECK constraint.
       - Rebuild resume_slots table with resume_text and profile (job_types -> profile).
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - PRAGMA user_version = 9.
       - COMMIT.
    5. PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in [
        "hr_notes",
        "reference_labels",
        "job_status",
        "job_status_events",
        "experience_items",
        "llm_consents",
        "resume_slots",
        "strict_industry_selection",
        "job_chat_seen",
        "prejudgements",
    ]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v9_schema_changes(conn)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("PRAGMA user_version = 9")
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v9 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set foreign_keys=ON
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=9,
        backup_filename=backup_filename,
        cleared=[],
    )


def _apply_v10_schema_changes(conn: sqlite3.Connection) -> None:
    # jobs 表新增 experience_req 与 degree_req 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "experience_req" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN experience_req TEXT")
    if "degree_req" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN degree_req TEXT")


def migrate_to_v10(db_path: Path) -> MigrationResult:
    """
    Migrate database schema from version 9 to version 10 (add experience_req, degree_req to jobs).

    Steps:
    1. Check current PRAGMA user_version and checkpoint WAL.
    2. Close connection and backup db to jet.db.bak-<UTC YYYYmmddTHHMMSSZ>-v<old_version>.
    3. New connection, turn PRAGMA foreign_keys=OFF outside transaction.
    4. BEGIN IMMEDIATE:
       - Record row counts for existing tables.
       - Alter jobs ADD COLUMN experience_req TEXT.
       - Alter jobs ADD COLUMN degree_req TEXT.
       - Verify row counts match before migration.
       - PRAGMA foreign_key_check is clean.
       - PRAGMA user_version = 10.
       - COMMIT.
    5. PRAGMA foreign_keys = ON.
    """
    # 1. Read current user_version and checkpoint
    check_conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    try:
        old_version = check_conn.execute("PRAGMA user_version").fetchone()[0]
        check_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        check_conn.close()

    # 2. Backup database file
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"jet.db.bak-{now_utc}-v{old_version}"
    backup_path = db_path.parent / backup_filename
    shutil.copy2(db_path, backup_path)

    # 3. New connection with foreign_keys=OFF
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=OFF")

    tables_to_check = [
        "users",
        "jobs",
        "job_versions",
        "profiles",
        "views",
        "judgements",
        "llm_calls",
        "user_settings",
        "pairings",
        "labels",
    ]
    for extra_tbl in [
        "hr_notes",
        "reference_labels",
        "job_status",
        "job_status_events",
        "experience_items",
        "llm_consents",
        "resume_slots",
        "strict_industry_selection",
        "job_chat_seen",
        "prejudgements",
    ]:
        cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{extra_tbl}'").fetchone()
        if cur:
            tables_to_check.append(extra_tbl)

    counts_before: dict[str, int] = {}
    for tbl in tables_to_check:
        cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        counts_before[tbl] = cur[0] if cur else 0

    # 4. Migrate inside transaction
    conn.execute("BEGIN IMMEDIATE")
    try:
        _apply_v10_schema_changes(conn)

        # Verify row counts
        for tbl in tables_to_check:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            after_count = cur[0] if cur else 0
            if after_count != counts_before[tbl]:
                raise MigrationError(
                    f"数据行数校验不一致: 表 {tbl} 原有 {counts_before[tbl]} 行，迁移后有 {after_count} 行。备份文件：{backup_filename}"
                )

        # Verify foreign keys
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"外键检查失败: {fk_errors}。备份文件：{backup_filename}")

        conn.execute("PRAGMA user_version = 10")
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if isinstance(e, MigrationError):
            raise
        raise MigrationError(f"数据库结构迁移到 v10 失败: {e}。备份文件：{backup_filename}") from e

    # 5. Outside transaction, set foreign_keys=ON
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    return MigrationResult(
        from_version=old_version,
        to_version=10,
        backup_filename=backup_filename,
        cleared=[],
    )


# 当前结构版本：新空库直接写这个版本，init_db 把旧库逐步迁到这里；测试也读它，加新的编号迁移时只改这一处
LATEST_VERSION = 10
