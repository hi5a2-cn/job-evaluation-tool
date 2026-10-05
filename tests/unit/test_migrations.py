from pathlib import Path
import sqlite3
import pytest

from jet.db.migrations import LATEST_VERSION, MigrationError, migrate_to_v2, migrate_v2_to_v3, migrate_v3_to_v4
from jet.db.store import connect, init_db, utc_now

SCHEMA_V0_PATH = Path(__file__).parent.parent / "fixtures" / "schema_v0.sql"
SCHEMA_V2_PATH = Path(__file__).parent.parent / "fixtures" / "schema_v2.sql"
SCHEMA_V3_PATH = Path(__file__).parent.parent / "fixtures" / "schema_v3.sql"


def _setup_v0_db(db_path: Path) -> None:
    """Create a database with schema v0 and sample rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    schema_sql = SCHEMA_V0_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("PRAGMA user_version = 0")

    now = utc_now()
    # 1. User
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    # 2. User settings
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit) VALUES ('me', 50)",
    )
    # 3. Profile
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[\"FastAPI\"]', '[\"深圳\"]', '[]', ?)",
        (now,),
    )
    # 4. Job and Job Version
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_mig_1', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, "
        "description, content_hash, created_at) VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', '开发描述', 'h1', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")

    # 5. Judgements with superseded_by chain（先插入现行判断，再插入指向它的旧判断）
    # Active judgement (superseded_by IS NULL)
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, "
        "rule_result, created_at, finished_at, superseded_by) "
        "VALUES (2, 'me', 1, 1, 1, 'done', 'fit', 'llm', '[\"Great\"]', '{}', ?, ?, NULL)",
        (now, now),
    )
    # Old judgement superseded by new judgement
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, "
        "rule_result, created_at, finished_at, superseded_by) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'fit', 'llm', '[\"OK\"]', '{}', ?, ?, 2)",
        (now, now),
    )
    # Rule excluded judgement
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, "
        "rule_result, created_at, finished_at, superseded_by) "
        "VALUES (3, 'me', 1, 1, 1, 'done', 'unfit', 'rule', '[\"城市不符\"]', '{}', ?, ?, 2)",
        (now, now),
    )

    # 6. LLM Call
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, judgement_id, provider, model, started_at, finished_at, outcome, billed) "
        "VALUES (1, 'me', 2, 'deepseek', 'deepseek-flash', ?, ?, 'ok', 1)",
        (now, now),
    )

    # 7. Pairing
    conn.execute(
        "INSERT INTO pairings (id, user_id, extension_origin, token_hash, created_at) "
        "VALUES (1, 'me', 'chrome-extension://test', 'hash123', ?)",
        (now,),
    )

    conn.close()


def test_migration_v0_to_v2_success(tmp_path: Path):
    data_dir = tmp_path / "mig_test"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v0_db(db_path)

    # Trigger migration via migrate_to_v2
    mig_res = migrate_to_v2(db_path)
    assert mig_res is not None
    assert mig_res.from_version == 0
    assert mig_res.to_version == 2

    # 1. Backup file exists and can be opened
    bak_files = list(data_dir.glob("jet.db.bak-*-v0"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        # In backup, user_version is 0 and judgements has 3 rows
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert bak_conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 3
        # In backup, profiles has no work_preference column
        with pytest.raises(sqlite3.OperationalError):
            bak_conn.execute("SELECT work_preference FROM profiles")
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Check row counts
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM pairings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 1

        # Check prompt_version backfilled
        j1 = conn.execute("SELECT prompt_version, superseded_by FROM judgements WHERE id = 1").fetchone()
        assert j1["prompt_version"] == "v1"
        assert j1["superseded_by"] == 2

        j2 = conn.execute("SELECT prompt_version, superseded_by FROM judgements WHERE id = 2").fetchone()
        assert j2["prompt_version"] == "v1"
        assert j2["superseded_by"] is None

        j3 = conn.execute("SELECT prompt_version FROM judgements WHERE id = 3").fetchone()
        assert j3["prompt_version"] == "rule"

        # Check profiles new columns
        p = conn.execute("SELECT work_preference, background FROM profiles WHERE id = 1").fetchone()
        assert p["work_preference"] == ""
        assert p["background"] == ""

        # Check llm_calls purpose
        call = conn.execute("SELECT purpose, eval_run_id FROM llm_calls WHERE id = 1").fetchone()
        assert call["purpose"] == "judge"
        assert call["eval_run_id"] is None

        # 3. New capabilities: insert unsure verdict and eval call with null judgement_id
        now = utc_now()
        # 已有现行判断 2（同一岗位版本、同一画像）：新的 unsure 判断必须先 supersede 它
        conn.execute("BEGIN")
        conn.execute("UPDATE judgements SET superseded_by = 10 WHERE id = 2")
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
            "source, prompt_version, rule_result, created_at) "
            "VALUES (10, 'me', 1, 1, 1, 'done', 'unsure', 'llm', 'v2', '{}', ?)",
            (now,),
        )
        conn.execute("COMMIT")
        assert conn.execute("SELECT verdict FROM judgements WHERE id = 10").fetchone()["verdict"] == "unsure"

        conn.execute(
            "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, eval_run_id, provider, model, "
            "started_at, outcome, billed) "
            "VALUES (10, 'me', NULL, 'eval', 'run_01', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now,),
        )
        assert conn.execute("SELECT purpose FROM llm_calls WHERE id = 10").fetchone()["purpose"] == "eval"

        # 4. Labels table exists and works
        conn.execute(
            "INSERT INTO labels (user_id, job_version_id, judgement_id, work_type, sales_level, "
            "experience_fit, overtime, overall, note, created_at, updated_at) "
            "VALUES ('me', 1, 10, '数据', '低', '满足', '明确双休或不加班', 'fit', '很好', ?, ?)",
            (now, now),
        )
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1
    finally:
        conn.close()


def test_migration_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v0_db(db_path)

    # We can test rollback by testing migrate_to_v2 directly on a db with corrupted structure
    # Or corrupting foreign keys so foreign_key_check fails
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO judgements (user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
                 "VALUES ('me', 1, 99999, 1, 'queued', '{}', '2026-09-24T00:00:00Z')")
    conn.close()

    # foreign_key_check will fail due to dangling job_version_id
    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v2(db_path)

    assert "外键检查失败" in str(exc_info.value)
    # user_version remains 0
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        check_conn.close()

    # Backup file is preserved
    bak_files = list(data_dir.glob("jet.db.bak-*-v0"))
    assert len(bak_files) >= 1


def test_latest_db_missing_verdict_reason_column_added_without_backup(tmp_path: Path):
    """已是结构版本 2 但缺 verdict_reason 列的库，init_db 后有这一列、行数不变、不生成新备份。"""
    data_dir = tmp_path / "v2_no_col"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    now = utc_now()
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)", (now,))
    conn.execute("CREATE TABLE user_settings (user_id TEXT PRIMARY KEY, daily_llm_limit INTEGER NOT NULL DEFAULT 50)")
    conn.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, platform TEXT NOT NULL, platform_job_id TEXT NOT NULL, "
        "completeness TEXT NOT NULL, current_version_id INTEGER, first_seen_at TEXT NOT NULL, "
        "last_seen_at TEXT NOT NULL, UNIQUE(platform, platform_job_id))"
    )
    conn.execute(
        "CREATE TABLE job_versions (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, version_no INTEGER NOT NULL, "
        "source TEXT NOT NULL, title TEXT NOT NULL, salary_raw TEXT, salary_visible INTEGER NOT NULL, "
        "salary_min_k REAL, salary_max_k REAL, salary_months INTEGER, salary_parse_ok INTEGER NOT NULL, "
        "city TEXT NOT NULL, district TEXT, description TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE(job_id, version_no))"
    )
    conn.execute(
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, version_no INTEGER NOT NULL, "
        "directions TEXT NOT NULL, keywords TEXT NOT NULL, cities TEXT NOT NULL, min_monthly_k REAL, "
        "exclude_keywords TEXT NOT NULL, work_preference TEXT NOT NULL DEFAULT '', "
        "background TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(user_id, version_no))"
    )
    conn.execute(
        "CREATE TABLE views (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, job_id INTEGER NOT NULL, "
        "page_type TEXT NOT NULL, seen_at TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE judgements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            job_version_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            verdict TEXT,
            source TEXT,
            reasons TEXT,
            prompt_version TEXT NOT NULL DEFAULT 'v2',
            engine TEXT,
            facts TEXT,
            derivation TEXT,
            rule_result TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            superseded_by INTEGER
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_judgements_active ON judgements(user_id, job_version_id, profile_id) "
        "WHERE superseded_by IS NULL"
    )
    conn.execute(
        "CREATE TABLE labels (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, job_version_id INTEGER NOT NULL, "
        "judgement_id INTEGER, work_type TEXT, sales_level TEXT, experience_fit TEXT, overtime TEXT, overall TEXT, "
        "note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, job_version_id))"
    )
    conn.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, judgement_id INTEGER, "
        "purpose TEXT NOT NULL DEFAULT 'judge', eval_run_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT NOT NULL, billed INTEGER NOT NULL, "
        "input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, cost_cny REAL)"
    )
    conn.execute(
        "CREATE TABLE pairings (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, extension_origin TEXT NOT NULL, "
        "token_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)"
    )

    # Insert a dummy row in judgements
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'p1', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Dev', 1, 1, '深圳', 'h1', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, prompt_version, rule_result, created_at) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'fit', 'llm', 'v2', '{}', ?)",
        (now,),
    )
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    conn.close()

    # Pre-condition: no verdict_reason column, 1 row, 0 backups
    conn = sqlite3.connect(str(db_path))
    cols_before = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
    assert "verdict_reason" not in cols_before
    rows_before = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    assert rows_before == 1
    conn.close()

    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Call init_db: should add verdict_reason column without backup
    mig_res = init_db(data_dir)
    assert mig_res is None

    # Post-condition: no backup generated
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # verdict_reason column exists, user_version is still LATEST_VERSION, rows unchanged
    conn2 = connect(data_dir)
    try:
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols_after = [r[1] for r in conn2.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "verdict_reason" in cols_after

        rows_after = conn2.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
        assert rows_after == rows_before

        existing_j = conn2.execute("SELECT verdict_reason, prompt_version FROM judgements WHERE id = 1").fetchone()
        assert existing_j["verdict_reason"] is None
        assert existing_j["prompt_version"] == "v2"
    finally:
        conn2.close()


def test_latest_db_missing_secondary_work_types_column_added_without_backup(tmp_path: Path):
    """结构版本 2 但 labels 缺 secondary_work_types 的库，init_db 后补列、行数不变、无新备份。"""
    data_dir = tmp_path / "v2_no_sec_col"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    now = utc_now()
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)", (now,))
    conn.execute("CREATE TABLE user_settings (user_id TEXT PRIMARY KEY, daily_llm_limit INTEGER NOT NULL DEFAULT 50)")
    conn.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, platform TEXT NOT NULL, platform_job_id TEXT NOT NULL, "
        "completeness TEXT NOT NULL, current_version_id INTEGER, first_seen_at TEXT NOT NULL, "
        "last_seen_at TEXT NOT NULL, UNIQUE(platform, platform_job_id))"
    )
    conn.execute(
        "CREATE TABLE job_versions (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, version_no INTEGER NOT NULL, "
        "source TEXT NOT NULL, title TEXT NOT NULL, salary_raw TEXT, salary_visible INTEGER NOT NULL, "
        "salary_min_k REAL, salary_max_k REAL, salary_months INTEGER, salary_parse_ok INTEGER NOT NULL, "
        "city TEXT NOT NULL, district TEXT, description TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE(job_id, version_no))"
    )
    conn.execute(
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, version_no INTEGER NOT NULL, "
        "directions TEXT NOT NULL, keywords TEXT NOT NULL, cities TEXT NOT NULL, min_monthly_k REAL, "
        "exclude_keywords TEXT NOT NULL, work_preference TEXT NOT NULL DEFAULT '', "
        "background TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(user_id, version_no))"
    )
    conn.execute(
        "CREATE TABLE views (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, job_id INTEGER NOT NULL, "
        "page_type TEXT NOT NULL, seen_at TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE judgements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            job_version_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            verdict TEXT,
            source TEXT,
            reasons TEXT,
            prompt_version TEXT NOT NULL DEFAULT 'v2',
            engine TEXT,
            facts TEXT,
            derivation TEXT,
            verdict_reason TEXT,
            rule_result TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            superseded_by INTEGER
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_judgements_active ON judgements(user_id, job_version_id, profile_id) "
        "WHERE superseded_by IS NULL"
    )
    # labels without secondary_work_types
    conn.execute(
        "CREATE TABLE labels (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, job_version_id INTEGER NOT NULL, "
        "judgement_id INTEGER, work_type TEXT, sales_level TEXT, experience_fit TEXT, overtime TEXT, overall TEXT, "
        "note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, job_version_id))"
    )
    conn.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, judgement_id INTEGER, "
        "purpose TEXT NOT NULL DEFAULT 'judge', eval_run_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT NOT NULL, billed INTEGER NOT NULL, "
        "input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, cost_cny REAL)"
    )
    conn.execute(
        "CREATE TABLE pairings (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, extension_origin TEXT NOT NULL, "
        "token_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)"
    )

    # Insert 1 job, 1 version, 1 label
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'p1', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Dev', 1, 1, '深圳', 'h1', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, work_type, sales_level, created_at, updated_at) "
        "VALUES (1, 'me', 1, '数据', '低', ?, ?)",
        (now, now),
    )
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    conn.close()

    # Pre-condition: no secondary_work_types column, 1 row in labels, 0 backups
    conn = sqlite3.connect(str(db_path))
    cols_before = [r[1] for r in conn.execute("PRAGMA table_info(labels)").fetchall()]
    assert "secondary_work_types" not in cols_before
    rows_before = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    assert rows_before == 1
    conn.close()

    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Call init_db: should add secondary_work_types column without backup
    mig_res = init_db(data_dir)
    assert mig_res is None

    # Post-condition: no backup generated
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # secondary_work_types column exists, user_version is still LATEST_VERSION, rows unchanged
    conn2 = connect(data_dir)
    try:
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols_after = [r[1] for r in conn2.execute("PRAGMA table_info(labels)").fetchall()]
        assert "secondary_work_types" in cols_after

        rows_after = conn2.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        assert rows_after == rows_before

        existing_label = conn2.execute("SELECT secondary_work_types, work_type FROM labels WHERE id = 1").fetchone()
        assert existing_label["secondary_work_types"] is None
        assert existing_label["work_type"] == "数据"
    finally:
        conn2.close()


def _setup_v2_db(db_path: Path) -> None:
    """Create a database with schema v2 and sample rows (including 4 labels for v2->v3 migration testing)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    schema_sql = SCHEMA_V2_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("PRAGMA user_version = 2")

    now = utc_now()
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit) VALUES ('me', 50)",
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '', '', ?)",
        (now,),
    )

    for i in range(1, 5):
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
            "VALUES (?, 'boss', ?, 'full', NULL, ?, ?)",
            (i, f"job_{i}", now, now),
        )
        conn.execute(
            "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
            "VALUES (?, ?, 1, 'detail', ?, 1, 1, '深圳', '开发描述', ?, ?)",
            (i, i, f"岗位{i}", f"hash_{i}", now),
        )
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (i, i))
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, prompt_version, rule_result, created_at, superseded_by) "
            "VALUES (?, 'me', ?, ?, 1, 'done', 'fit', 'llm', '[]', 'v2', '{}', ?, NULL)",
            (i, i, i, now),
        )

    # 4 Labels matching requirements:
    # ① 运营/高/overtime=明确双休或不加班/overall=unfit
    # ② 运营/低/不满足/未提及/unsure
    # ③ 运营/满足/有/unfit
    # ④ 销售/高/有/unfit
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, judgement_id, work_type, sales_level, experience_fit, overtime, overall, note, created_at, updated_at) "
        "VALUES (1, 'me', 1, 1, '运营', '高', '满足', '明确双休或不加班', 'unfit', 'n1', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, judgement_id, work_type, sales_level, experience_fit, overtime, overall, note, created_at, updated_at) "
        "VALUES (2, 'me', 2, 2, '运营', '低', '不满足', '未提及', 'unsure', 'n2', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, judgement_id, work_type, sales_level, experience_fit, overtime, overall, note, created_at, updated_at) "
        "VALUES (3, 'me', 3, 3, '运营', '低', '满足', '有', 'unfit', 'n3', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, judgement_id, work_type, sales_level, experience_fit, overtime, overall, note, created_at, updated_at) "
        "VALUES (4, 'me', 4, 4, '销售', '高', '满足', '有', 'unfit', 'n4', ?, ?)",
        (now, now),
    )
    conn.close()


def test_migration_v2_to_v3_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v2_v3"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v2_db(db_path)

    res = migrate_v2_to_v3(db_path)
    assert res.from_version == 2
    assert res.to_version == 3

    # Backup exists
    bak_files = list(data_dir.glob("jet.db.bak-*-v2"))
    assert len(bak_files) == 1

    # cleared 正好列出 ②overall、③work_intensity、④work_intensity
    assert res.cleared == [
        (2, "岗位2", "总体结论", "unsure"),
        (3, "岗位3", "工作强度", "有"),
        (4, "岗位4", "工作强度", "有"),
    ]

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 4

        # ① work_intensity=双休、overall=skip
        l1 = conn.execute("SELECT * FROM labels WHERE id = 1").fetchone()
        assert l1["work_type"] == "运营"
        assert l1["work_subtype"] is None
        assert l1["work_intensity"] == "双休"
        assert l1["overall"] == "skip"

        # ② overall=NULL
        l2 = conn.execute("SELECT * FROM labels WHERE id = 2").fetchone()
        assert l2["work_type"] == "运营"
        assert l2["work_subtype"] is None
        assert l2["work_intensity"] == "未提及"
        assert l2["overall"] is None

        # ③ work_intensity=NULL
        l3 = conn.execute("SELECT * FROM labels WHERE id = 3").fetchone()
        assert l3["work_type"] == "运营"
        assert l3["work_subtype"] is None
        assert l3["work_intensity"] is None
        assert l3["overall"] == "skip"

        # ④ work_type=市场与销售、work_subtype=销售与商务拓展、work_intensity=NULL
        l4 = conn.execute("SELECT * FROM labels WHERE id = 4").fetchone()
        assert l4["work_type"] == "市场与销售"
        assert l4["work_subtype"] == "销售与商务拓展"
        assert l4["work_intensity"] is None
        assert l4["overall"] == "skip"
    finally:
        conn.close()


def test_migration_from_v0_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v0_v4"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v0_db(db_path)

    mig_res = init_db(data_dir)
    assert mig_res is not None
    assert mig_res.from_version == 0
    assert mig_res.to_version == LATEST_VERSION

    # Backups exist: v0, v2, v3
    bak_v0 = list(data_dir.glob("jet.db.bak-*-v0"))
    bak_v2 = list(data_dir.glob("jet.db.bak-*-v2"))
    bak_v3 = list(data_dir.glob("jet.db.bak-*-v3"))
    assert len(bak_v0) == 1
    assert len(bak_v2) == 1
    assert len(bak_v3) == 1

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 3
        cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "origin" in cols
    finally:
        conn.close()

    # Second init_db is idempotent
    mig_res2 = init_db(data_dir)
    assert mig_res2 is None


def test_migration_v2_to_v3_failure_rollback(tmp_path: Path):
    data_dir = tmp_path / "mig_v2_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v2_db(db_path)

    # Corrupt foreign key in labels (pointing to nonexistent job_version_id)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO labels (id, user_id, job_version_id, work_type, created_at, updated_at) "
        "VALUES (99, 'me', 99999, '运营', '2026-09-24T00:00:00Z', '2026-09-24T00:00:00Z')"
    )
    conn.close()

    with pytest.raises(MigrationError) as exc_info:
        migrate_v2_to_v3(db_path)

    assert "外键检查失败" in str(exc_info.value)

    # user_version remains 2
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        check_conn.close()

    # Backup file preserved
    bak_files = list(data_dir.glob("jet.db.bak-*-v2"))
    assert len(bak_files) >= 1


def test_latest_db_missing_columns_and_tables_patched_without_backup(tmp_path: Path):
    data_dir = tmp_path / "v3_patch_test"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"

    # Setup a clean v3 DB first
    init_db(data_dir)
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Drop hr_notes, reference_labels, and drop hr_questions by recreating judgements without it
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE IF EXISTS hr_notes")
    conn.execute("DROP TABLE IF EXISTS reference_labels")

    # Recreate judgements without hr_questions
    judgements_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='judgements'").fetchone()[0]
    # Remove hr_questions TEXT line
    import re
    stripped_sql = re.sub(r",?\s*hr_questions\s+TEXT", "", judgements_sql)
    conn.execute("CREATE TABLE judgements_old AS SELECT * FROM judgements")
    conn.execute("DROP TABLE judgements")
    conn.execute(stripped_sql)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
    assert "hr_questions" not in cols
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    conn.close()

    # Re-run init_db
    res = init_db(data_dir)
    assert res is None  # no major version bump

    # Verify no backup was created
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Verify hr_questions and new tables are restored
    conn = connect(data_dir)
    try:
        col_names = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "hr_questions" in col_names

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "hr_notes" in tables
        assert "reference_labels" in tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
    finally:
        conn.close()


def test_migration_from_v2_creates_new_tables_in_same_run(tmp_path):
    """从结构版本 2 迁移的那一次启动里，就要补齐 hr_notes、reference_labels 与 hr_questions 列。"""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    from jet.db.store import init_db

    data_dir = tmp_path / "v2_same_run"
    data_dir.mkdir()
    schema_v2 = (_Path(__file__).parent.parent / "fixtures" / "schema_v2.sql").read_text(encoding="utf-8")
    conn = _sqlite3.connect(str(data_dir / "jet.db"), isolation_level=None)
    conn.executescript(schema_v2)
    conn.execute("PRAGMA user_version = 2")
    conn.close()

    result = init_db(data_dir)
    assert result is not None and result.to_version == LATEST_VERSION

    conn = _sqlite3.connect(str(data_dir / "jet.db"))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"hr_notes", "reference_labels"} <= tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(judgements)")}
        assert "hr_questions" in cols
    finally:
        conn.close()


def test_reference_labels_overall_void_column_backfill_and_idempotency(tmp_path: Path):
    """已有 reference_labels 行的库补列后这些行 overall_void=1；之后新插入的行为 0；再次启动不再改动。"""
    import re
    data_dir = tmp_path / "overall_void_test"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"

    # 1. Initialize a DB first
    init_db(data_dir)

    # 2. Simulate a database where reference_labels exists but has no overall_void column
    conn = sqlite3.connect(str(db_path))
    ref_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reference_labels'").fetchone()[0]
    stripped_sql = re.sub(r",?\s*overall_void\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+0", "", ref_sql)
    conn.execute("CREATE TABLE reference_labels_old AS SELECT * FROM reference_labels")
    conn.execute("DROP TABLE reference_labels")
    conn.execute(stripped_sql)

    # Insert an existing reference label row (before column is added)
    now = utc_now()
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_ov_1', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Dev', 1, 1, '深圳', 'h1', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        """
        INSERT INTO reference_labels (
            id, user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, overall, created_at
        ) VALUES (1, 'me', 1, 'model:gemini', 1, 0, '数据与技术', 'apply', ?)
        """,
        (now,),
    )
    conn.commit()

    # Pre-condition: overall_void not in reference_labels
    cols = [r[1] for r in conn.execute("PRAGMA table_info(reference_labels)").fetchall()]
    assert "overall_void" not in cols
    conn.close()

    # 3. Call init_db -> _ensure_current_schema adds overall_void and updates existing rows to 1
    init_db(data_dir)

    conn2 = connect(data_dir)
    try:
        cols_after = [r[1] for r in conn2.execute("PRAGMA table_info(reference_labels)").fetchall()]
        assert "overall_void" in cols_after

        # Existing row must have overall_void = 1
        row1 = conn2.execute("SELECT overall_void, overall FROM reference_labels WHERE id = 1").fetchone()
        assert row1["overall_void"] == 1
        assert row1["overall"] == "apply"

        # 4. Insert a new row after column was added -> default is 0
        conn2.execute(
            """
            INSERT INTO reference_labels (
                id, user_id, job_version_id, source, profile_version_no, used_hr_note,
                work_type, overall, created_at
            ) VALUES (2, 'me', 1, 'model:gemini-new', 1, 0, '数据与技术', 'apply', ?)
            """,
            (now,),
        )
        row2 = conn2.execute("SELECT overall_void, overall FROM reference_labels WHERE id = 2").fetchone()
        assert row2["overall_void"] == 0
    finally:
        conn2.close()

    # 5. Run init_db again -> overall_void is already present, existing rows are NOT modified
    init_db(data_dir)

    conn3 = connect(data_dir)
    try:
        row1_after = conn3.execute("SELECT overall_void FROM reference_labels WHERE id = 1").fetchone()
        row2_after = conn3.execute("SELECT overall_void FROM reference_labels WHERE id = 2").fetchone()
        assert row1_after["overall_void"] == 1
        assert row2_after["overall_void"] == 0
    finally:
        conn3.close()


def _setup_v3_db(db_path: Path) -> None:
    """Create a database with the real schema v3 (frozen in tests/fixtures/schema_v3.sql) and sample rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 3")

    now = utc_now()
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit) VALUES ('me', 50)",
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '', '', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_v3_1', 'full', NULL, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', '开发描述', 'hash_v3', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, prompt_version, rule_result, created_at, superseded_by) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'apply', 'llm', '[]', 'v5', '{}', ?, NULL)",
        (now,),
    )
    conn.close()


def test_migration_v3_to_v4_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v3_v4"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v3_db(db_path)

    # Pre-condition: no origin column in judgements
    conn_pre = sqlite3.connect(str(db_path))
    cols_pre = [r[1] for r in conn_pre.execute("PRAGMA table_info(judgements)").fetchall()]
    assert "origin" not in cols_pre
    conn_pre.close()

    res = migrate_v3_to_v4(db_path)
    assert res.from_version == 3
    assert res.to_version == 4

    # Backup file exists
    bak_files = list(data_dir.glob("jet.db.bak-*-v3"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 3
        bak_cols = [r[1] for r in bak_conn.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "origin" not in bak_cols
    finally:
        bak_conn.close()

    # Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 1
        cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "origin" in cols
        j = conn.execute("SELECT origin FROM judgements WHERE id = 1").fetchone()
        assert j["origin"] is None
    finally:
        conn.close()


def test_fresh_db_restarted_does_not_migrate(tmp_path: Path):
    data_dir = tmp_path / "v4_clean"
    data_dir.mkdir()

    res = init_db(data_dir)
    assert res is None

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols = [r[1] for r in conn.execute("PRAGMA table_info(judgements)").fetchall()]
        assert "origin" in cols
    finally:
        conn.close()

    # No backup created
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Second init_db does not migrate
    res2 = init_db(data_dir)
    assert res2 is None
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0


def _table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    ]
    return {t: {r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")} for t in tables}


def test_migration_from_v3_via_init_db(tmp_path: Path):
    """体检第 80 条：从真实的 v3 结构一路升到最新版本，结果的表和列与新建空库一致，原有数据还在。"""
    data_dir = tmp_path / "mig_v3_latest"
    data_dir.mkdir()
    _setup_v3_db(data_dir / "jet.db")

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 3
    assert res.to_version == LATEST_VERSION
    assert res.backup_filename.endswith("-v3")

    fresh_dir = tmp_path / "fresh"
    init_db(fresh_dir)
    fresh = connect(fresh_dir)
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert _table_columns(conn) == _table_columns(fresh)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        job = conn.execute("SELECT platform_job_id FROM jobs WHERE id = 1").fetchone()
        assert job["platform_job_id"] == "job_v3_1"
        j = conn.execute("SELECT status, verdict, job_version_id FROM judgements WHERE id = 1").fetchone()
        assert (j["status"], j["job_version_id"]) == ("done", 1)
        assert j["verdict"] is not None
    finally:
        conn.close()
        fresh.close()


def test_migration_v3_to_v4_failure_rollback(tmp_path: Path):
    data_dir = tmp_path / "mig_v3_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v3_db(db_path)

    # Corrupt foreign key
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (999, 'me', 1, 99999, 1, 'queued', '{}', '2026-09-24T00:00:00Z')"
    )
    conn.close()

    with pytest.raises(MigrationError) as exc_info:
        migrate_v3_to_v4(db_path)

    assert "外键检查失败" in str(exc_info.value)

    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        check_conn.close()

    bak_files = list(data_dir.glob("jet.db.bak-*-v3"))
    assert len(bak_files) >= 1


SCHEMA_V4_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_job_id TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK(completeness IN ('list_only', 'full')),
    current_version_id INTEGER,
    company_name TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(platform, platform_job_id),
    FOREIGN KEY (current_version_id) REFERENCES job_versions(id)
);

CREATE TABLE IF NOT EXISTS job_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('list', 'detail')),
    title TEXT NOT NULL,
    salary_raw TEXT,
    salary_visible INTEGER NOT NULL,
    salary_min_k REAL,
    salary_max_k REAL,
    salary_months INTEGER,
    salary_parse_ok INTEGER NOT NULL,
    city TEXT NOT NULL,
    district TEXT,
    description TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, version_no),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    directions TEXT NOT NULL,
    keywords TEXT NOT NULL,
    cities TEXT NOT NULL,
    min_monthly_k REAL,
    exclude_keywords TEXT NOT NULL,
    work_preference TEXT NOT NULL DEFAULT '',
    background TEXT NOT NULL DEFAULT '',
    excluded_cities TEXT NOT NULL DEFAULT '[]',
    nonpref_min_monthly_k REAL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, version_no),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    page_type TEXT NOT NULL CHECK(page_type IN ('list', 'detail')),
    seen_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS judgements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    job_version_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed', 'quota_exhausted', 'interrupted')),
    verdict TEXT CHECK(verdict IS NULL OR verdict IN ('fit', 'unsure', 'unfit', 'apply', 'try', 'check', 'skip')),
    source TEXT CHECK(source IS NULL OR source IN ('rule', 'llm')),
    reasons TEXT,
    prompt_version TEXT NOT NULL DEFAULT 'v5',
    engine TEXT,
    facts TEXT,
    derivation TEXT,
    verdict_reason TEXT,
    hr_questions TEXT,
    review TEXT,
    rule_result TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    superseded_by INTEGER,
    origin TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (job_id) REFERENCES jobs(id),
    FOREIGN KEY (job_version_id) REFERENCES job_versions(id),
    FOREIGN KEY (profile_id) REFERENCES profiles(id),
    FOREIGN KEY (superseded_by) REFERENCES judgements(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_judgements_active
ON judgements(user_id, job_version_id, profile_id)
WHERE superseded_by IS NULL;

CREATE TABLE IF NOT EXISTS labels (
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
);

CREATE TABLE IF NOT EXISTS llm_calls (
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
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    daily_llm_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_llm_limit BETWEEN 0 AND 500),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS pairings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    extension_origin TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS hr_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, job_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS reference_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_version_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    profile_version_no INTEGER NOT NULL,
    used_hr_note INTEGER NOT NULL,
    work_type TEXT CHECK(work_type IS NULL OR work_type IN ('数据与技术', '运营', '产品与项目', '内容与设计', '市场与销售', '科研与专业', '职能', '其他')),
    work_subtype TEXT,
    secondary_work_types TEXT,
    sales_level TEXT CHECK(sales_level IS NULL OR sales_level IN ('高', '中', '低')),
    experience_fit TEXT CHECK(experience_fit IS NULL OR experience_fit IN ('满足', '差一点', '不满足', '无法判断')),
    work_intensity TEXT CHECK(work_intensity IS NULL OR work_intensity IN ('高强度', '单休', '大小周', '双休', '未提及')),
    overall TEXT CHECK(overall IS NULL OR overall IN ('apply', 'try', 'check', 'skip')),
    overall_void INTEGER NOT NULL DEFAULT 0,
    rationale TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, job_version_id, source),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (job_version_id) REFERENCES job_versions(id)
);

CREATE TABLE IF NOT EXISTS job_status (
    user_id TEXT NOT NULL REFERENCES users(id),
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NOT NULL CHECK(status IN ('saved', 'applied', 'skipped')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, job_id)
);

CREATE TABLE IF NOT EXISTS job_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NULL CHECK(status IS NULL OR status IN ('saved', 'applied', 'skipped')),
    previous_status TEXT NULL,
    created_at TEXT NOT NULL
);
"""

SCHEMA_V5_SQL = (
    SCHEMA_V4_SQL.replace(
        "CHECK(purpose IN ('judge', 'eval'))",
        "CHECK(purpose IN ('judge', 'eval', 'assist'))",
    ).replace(
        "    daily_llm_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_llm_limit BETWEEN 0 AND 500),",
        "    daily_llm_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_llm_limit BETWEEN 0 AND 500),\n"
        "    daily_assist_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500),",
    )
    + """
CREATE TABLE IF NOT EXISTS experience_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_no INTEGER NOT NULL CHECK(item_no BETWEEN 1 AND 10),
    content TEXT NOT NULL CHECK(length(content) <= 200 AND length(trim(content)) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, item_no),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS llm_consents (
    user_id TEXT PRIMARY KEY,
    consented_at TEXT NOT NULL,
    revoked_at TEXT,
    fields_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""
)

SCHEMA_V6_SQL = SCHEMA_V5_SQL.replace(
    "    company_name TEXT,\n    first_seen_at TEXT NOT NULL,",
    "    company_name TEXT,\n    company_industry TEXT,\n    first_seen_at TEXT NOT NULL,",
)


def _setup_v4_db(db_path: Path) -> None:
    """Create a database with schema v4 and sample rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_V4_SQL)
    conn.execute("PRAGMA user_version = 4")

    now = utc_now()
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit) VALUES ('me', 50)",
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '', '', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_v4_1', 'full', NULL, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', '开发描述', 'hash_v4', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, prompt_version, rule_result, created_at, superseded_by, origin) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'apply', 'llm', '[]', 'v5', '{}', ?, NULL, 'feed')",
        (now,),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, eval_run_id, provider, model, started_at, finished_at, outcome, billed, input_tokens, cached_tokens, output_tokens, cost_cny) "
        "VALUES (1, 'me', 1, 'judge', NULL, 'deepseek', 'deepseek-flash', ?, ?, 'ok', 1, 100, 20, 30, 0.001)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, eval_run_id, provider, model, started_at, finished_at, outcome, billed, input_tokens, cached_tokens, output_tokens, cost_cny) "
        "VALUES (2, 'me', NULL, 'eval', 'run_01', 'deepseek', 'deepseek-flash', ?, ?, 'ok', 1, 150, 0, 40, 0.002)",
        (now, now),
    )
    conn.close()


def _setup_v5_db(db_path: Path) -> None:
    """Create a database with schema v5 and sample rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_V5_SQL)
    conn.execute("PRAGMA user_version = 5")

    now = utc_now()
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) VALUES ('me', 50, 50)",
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '', '', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_v5_1', 'full', NULL, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', '开发描述', 'hash_v5', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, prompt_version, rule_result, created_at, superseded_by, origin) "
        "VALUES (1, 'me', 1, 1, 1, 'done', 'apply', 'llm', '[]', 'v5', '{}', ?, NULL, 'feed')",
        (now,),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, eval_run_id, provider, model, started_at, finished_at, outcome, billed, input_tokens, cached_tokens, output_tokens, cost_cny) "
        "VALUES (1, 'me', 1, 'judge', NULL, 'deepseek', 'deepseek-flash', ?, ?, 'ok', 1, 100, 20, 30, 0.001)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, eval_run_id, provider, model, started_at, finished_at, outcome, billed, input_tokens, cached_tokens, output_tokens, cost_cny) "
        "VALUES (2, 'me', NULL, 'assist', NULL, 'deepseek', 'deepseek-flash', ?, ?, 'ok', 1, 150, 0, 40, 0.002)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO experience_items (id, user_id, item_no, content, created_at, updated_at) "
        "VALUES (1, 'me', 1, '经历素材1', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at) "
        "VALUES ('me', ?, NULL, 1, ?)",
        (now, now),
    )
    conn.close()


def test_migration_v4_to_v5_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v4_v5"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v4_db(db_path)

    # Pre-condition: purpose='assist' not allowed in v4
    conn_pre = sqlite3.connect(str(db_path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn_pre.execute(
                "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, provider, model, started_at, outcome, billed) "
                "VALUES (99, 'me', NULL, 'assist', 'deepseek', 'deepseek-flash', '2026-09-24T00:00:00Z', 'ok', 1)"
            )
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_v4_to_v5

    res = migrate_v4_to_v5(db_path)
    assert res.from_version == 4
    assert res.to_version == 5
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v4")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v4"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert bak_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        assert bak_conn.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 1
        assert bak_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        bak_cols = [r[1] for r in bak_conn.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_assist_limit" not in bak_cols
        bak_tables = {r[0] for r in bak_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "experience_items" not in bak_tables
        assert "llm_consents" not in bak_tables
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # llm_calls row count unchanged and original field values preserved
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        c1 = conn.execute("SELECT * FROM llm_calls WHERE id = 1").fetchone()
        assert c1["purpose"] == "judge"
        assert c1["provider"] == "deepseek"
        assert c1["model"] == "deepseek-flash"
        assert c1["billed"] == 1
        assert c1["input_tokens"] == 100
        assert c1["cached_tokens"] == 20
        assert c1["output_tokens"] == 30
        assert c1["cost_cny"] == 0.001

        c2 = conn.execute("SELECT * FROM llm_calls WHERE id = 2").fetchone()
        assert c2["purpose"] == "eval"
        assert c2["eval_run_id"] == "run_01"
        assert c2["billed"] == 1

        # Can insert purpose='assist'
        now = utc_now()
        conn.execute(
            "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES (3, 'me', NULL, 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now,),
        )
        assert conn.execute("SELECT purpose FROM llm_calls WHERE id = 3").fetchone()["purpose"] == "assist"

        # Inserting purpose='other' fails
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_calls (id, user_id, judgement_id, purpose, provider, model, started_at, outcome, billed) "
                "VALUES (4, 'me', NULL, 'other', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
                (now,),
            )

        # experience_items table exists and constraints work
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "experience_items" in tables
        assert "llm_consents" in tables

        # item_no between 1 and 10
        conn.execute(
            "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
            "VALUES ('me', 1, '经历条目1', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
            "VALUES ('me', 10, '经历条目10', ?, ?)",
            (now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 0, '无效item_no 0', ?, ?)",
                (now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 11, '无效item_no 11', ?, ?)",
                (now, now),
            )

        # UNIQUE(user_id, item_no)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 1, '重复item_no 1', ?, ?)",
                (now, now),
            )

        # content length <= 200 and length(trim(content)) > 0
        content_200 = "文" * 200
        conn.execute(
            "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
            "VALUES ('me', 2, ?, ?, ?)",
            (content_200, now, now),
        )
        content_201 = "文" * 201
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 3, ?, ?, ?)",
                (content_201, now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 4, '', ?, ?)",
                (now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('me', 4, '   ', ?, ?)",
                (now, now),
            )

        # user_id NOT NULL and references users
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES (NULL, 5, '内容', ?, ?)",
                (now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at) "
                "VALUES ('nonexistent_user', 5, '内容', ?, ?)",
                (now, now),
            )

        # llm_consents table exists and constraints work
        conn.execute(
            "INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at) "
            "VALUES ('me', ?, NULL, 1, ?)",
            (now, now),
        )
        consent = conn.execute("SELECT * FROM llm_consents WHERE user_id = 'me'").fetchone()
        assert consent["fields_version"] == 1
        assert consent["revoked_at"] is None

        # UNIQUE / PRIMARY KEY(user_id)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at) "
                "VALUES ('me', ?, NULL, 1, ?)",
                (now, now),
            )
        # user_id NOT NULL
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at) "
                "VALUES (NULL, ?, NULL, 1, ?)",
                (now, now),
            )
        # user_id references users
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_consents (user_id, consented_at, revoked_at, fields_version, updated_at) "
                "VALUES ('nonexistent_user', ?, NULL, 1, ?)",
                (now, now),
            )

        # user_settings has daily_assist_limit column, default 50
        settings_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_assist_limit" in settings_cols

        s = conn.execute("SELECT daily_assist_limit FROM user_settings WHERE user_id = 'me'").fetchone()
        assert s["daily_assist_limit"] == 50

        # Allowed range 0-500
        conn.execute("UPDATE user_settings SET daily_assist_limit = 0 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_assist_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_assist_limit"] == 0
        conn.execute("UPDATE user_settings SET daily_assist_limit = 500 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_assist_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_assist_limit"] == 500

        # Outside 0-500 rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_assist_limit = -1 WHERE user_id = 'me'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_assist_limit = 501 WHERE user_id = 'me'")

        # PRAGMA foreign_key_check is clean
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_latest_db_with_v5_data_restarted_does_not_migrate(tmp_path: Path):
    data_dir = tmp_path / "v5_clean"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v5_db(db_path)
    conn_setup = sqlite3.connect(str(db_path))
    conn_setup.execute("ALTER TABLE jobs ADD COLUMN company_industry TEXT")
    conn_setup.execute(f"PRAGMA user_version = {LATEST_VERSION}")
    conn_setup.close()

    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    res = init_db(data_dir)
    assert res is None

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_assist_limit" in cols
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "experience_items" in tables
        assert "llm_consents" in tables
        assert conn.execute("SELECT COUNT(*) FROM experience_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_consents").fetchone()[0] == 1
    finally:
        conn.close()

    # No backup created
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    # Second init_db does not migrate
    res2 = init_db(data_dir)
    assert res2 is None
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0


def test_clean_db_init_creates_latest(tmp_path: Path):
    data_dir = tmp_path / "v5_clean_init"
    data_dir.mkdir()

    res = init_db(data_dir)
    assert res is None

    # No backup created on empty directory init
    assert len(list(data_dir.glob("jet.db.bak-*"))) == 0

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # New tables exist
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "experience_items" in tables
        assert "llm_consents" in tables

        # New column in user_settings exists
        settings_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_assist_limit" in settings_cols

        # Default row for user 'me' has daily_assist_limit=50
        me_settings = conn.execute("SELECT daily_assist_limit FROM user_settings WHERE user_id = 'me'").fetchone()
        assert me_settings is not None
        assert me_settings["daily_assist_limit"] == 50

        # llm_calls allows purpose='assist'
        now = utc_now()
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now,),
        )
        assert conn.execute("SELECT purpose FROM llm_calls WHERE user_id = 'me'").fetchone()["purpose"] == "assist"

        # llm_calls rejects purpose='other'
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
                "VALUES ('me', 'other', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
                (now,),
            )
    finally:
        conn.close()


def test_migration_v4_to_v5_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v4_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v4_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_v4_to_v5

    def mock_apply_v5_schema_changes(conn):
        conn.execute("""
        CREATE TABLE IF NOT EXISTS experience_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_no INTEGER NOT NULL,
            content TEXT NOT NULL
        )
        """)
        raise sqlite3.OperationalError("simulated error creating table")

    monkeypatch.setattr(jet.db.migrations, "_apply_v5_schema_changes", mock_apply_v5_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_v4_to_v5(db_path)

    assert "simulated error creating table" in str(exc_info.value) or "v5" in str(exc_info.value)

    # user_version remains 4
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert check_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        assert check_conn.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 1
        tables = {r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "experience_items" not in tables
        assert "llm_consents" not in tables
        cols = [r[1] for r in check_conn.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_assist_limit" not in cols
    finally:
        check_conn.close()

    # Backup file preserved
    bak_files = list(data_dir.glob("jet.db.bak-*-v4"))
    assert len(bak_files) >= 1


def test_migration_v5_to_v6_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v5_v6"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v5_db(db_path)

    # Pre-condition: company_industry not in jobs in v5
    conn_pre = sqlite3.connect(str(db_path))
    try:
        cols_pre = [r[1] for r in conn_pre.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "company_industry" not in cols_pre
        assert conn_pre.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_to_v6

    res = migrate_to_v6(db_path)
    assert res.from_version == 5
    assert res.to_version == 6
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v5")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v5"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert bak_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert bak_conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 1
        assert bak_conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 1
        assert bak_conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        assert bak_conn.execute("SELECT COUNT(*) FROM experience_items").fetchone()[0] == 1
        assert bak_conn.execute("SELECT COUNT(*) FROM llm_consents").fetchone()[0] == 1
        bak_cols = [r[1] for r in bak_conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "company_industry" not in bak_cols
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Row counts unchanged and existing rows preserved
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM experience_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_consents").fetchone()[0] == 1

        # Check company_industry column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "company_industry" in cols

        # Existing job row has company_industry is NULL
        job_row = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
        assert job_row["company_industry"] is None
        assert job_row["platform_job_id"] == "job_v5_1"

        # Update company_industry on existing job
        conn.execute("UPDATE jobs SET company_industry = '新能源汽车' WHERE id = 1")
        assert conn.execute("SELECT company_industry FROM jobs WHERE id = 1").fetchone()["company_industry"] == "新能源汽车"

        # Insert new job with company_industry
        now = utc_now()
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, company_name, company_industry, completeness, first_seen_at, last_seen_at) "
            "VALUES (2, 'boss', 'job_v6_new', '理想汽车', '汽车研发/制造', 'full', ?, ?)",
            (now, now),
        )
        j2 = conn.execute("SELECT company_name, company_industry FROM jobs WHERE id = 2").fetchone()
        assert j2["company_name"] == "理想汽车"
        assert j2["company_industry"] == "汽车研发/制造"

        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_v5_to_v6_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v5_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v5_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v6

    def mock_apply_v6_schema_changes(conn):
        conn.execute("ALTER TABLE jobs ADD COLUMN company_industry TEXT")
        raise sqlite3.OperationalError("simulated error during v6 migration")

    monkeypatch.setattr(jet.db.migrations, "_apply_v6_schema_changes", mock_apply_v6_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v6(db_path)

    assert "simulated error during v6 migration" in str(exc_info.value) or "v6" in str(exc_info.value)

    # user_version remains 5
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert check_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        cols = [r[1] for r in check_conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "company_industry" not in cols
    finally:
        check_conn.close()

    # Backup file preserved
    bak_files = list(data_dir.glob("jet.db.bak-*-v5"))
    assert len(bak_files) >= 1


def test_migration_from_v5_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v5_v6_init_db"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v5_db(db_path)

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 5
    assert res.to_version == LATEST_VERSION

    bak_files = list(data_dir.glob("jet.db.bak-*-v5"))
    assert len(bak_files) == 1

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "company_industry" in cols
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        conn.close()


def _setup_v6_db(db_path: Path) -> None:
    """Create a database with schema v6 and sample rows."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_V6_SQL)
    conn.execute("PRAGMA user_version = 6")

    now = utc_now()
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('me', 'me', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('user2', 'user2', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) VALUES ('me', 50, 50)",
    )
    conn.execute(
        "INSERT INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) VALUES ('user2', 50, 50)",
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, work_preference, background, created_at) "
        "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '', '', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, company_name, company_industry, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_v6_1', 'Tech Corp', '互联网', 'full', NULL, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (1, 1, 1, 'detail', 'Python Dev', 1, 1, '深圳', '开发描述', 'hash_v6', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 1 WHERE id = 1")
    conn.close()


def test_migration_v6_to_v7_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v6_v7"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v6_db(db_path)

    # Pre-condition: strict_industry_selection not in tables in v6
    conn_pre = sqlite3.connect(str(db_path))
    try:
        tables_pre = {r[0] for r in conn_pre.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" not in tables_pre
        assert conn_pre.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_to_v7
    from jet.domain.industry import read_strict_industries

    expected_industries = read_strict_industries()
    assert len(expected_industries) == 7

    res = migrate_to_v7(db_path)
    assert res.from_version == 6
    assert res.to_version == 7
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v6")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v6"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert bak_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert bak_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        bak_tables = {r[0] for r in bak_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" not in bak_tables
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Row counts unchanged and existing rows preserved
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 1

        # Check strict_industry_selection table exists and has all 6 industries for both users
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" in tables

        rows = conn.execute("SELECT user_id, industry FROM strict_industry_selection ORDER BY user_id, industry").fetchall()
        assert len(rows) == 14
        user_me_inds = {r["industry"] for r in rows if r["user_id"] == "me"}
        user_2_inds = {r["industry"] for r in rows if r["user_id"] == "user2"}
        assert user_me_inds == set(expected_industries)
        assert user_2_inds == set(expected_industries)

        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_v6_to_v7_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v6_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v6_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v7

    def mock_apply_v7_schema_changes(conn, rules_path=None):
        conn.execute("CREATE TABLE IF NOT EXISTS strict_industry_selection (user_id TEXT, industry TEXT)")
        raise sqlite3.OperationalError("simulated error during v7 migration")

    monkeypatch.setattr(jet.db.migrations, "_apply_v7_schema_changes", mock_apply_v7_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v7(db_path)

    assert "simulated error during v7 migration" in str(exc_info.value) or "v7" in str(exc_info.value)

    # user_version remains 6
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert check_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        tables = {r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" not in tables
    finally:
        check_conn.close()

    # Backup file preserved
    bak_files = list(data_dir.glob("jet.db.bak-*-v6"))
    assert len(bak_files) >= 1


def test_migration_from_v6_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v6_v7_init_db"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v6_db(db_path)

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 6
    assert res.to_version == LATEST_VERSION

    bak_files = list(data_dir.glob("jet.db.bak-*-v6"))
    assert len(bak_files) == 1

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("SELECT COUNT(*) FROM strict_industry_selection").fetchone()[0] == 14
    finally:
        conn.close()


def test_new_db_init_latest_version_no_selections(tmp_path: Path):
    data_dir = tmp_path / "v7_fresh_db"
    data_dir.mkdir()

    res = init_db(data_dir)
    assert res is None

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM strict_industry_selection").fetchone()[0] == 0
    finally:
        conn.close()


def test_migration_v6_to_v7_midway_failure_rollback_and_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """迁移事务中途失败时版本仍为 6 且新表与勾选都回滚；成功迁移后版本为 7。"""
    data_dir = tmp_path / "mig_v6_midway_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v6_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v7

    real_apply = jet.db.migrations._apply_v7_schema_changes

    # 1. 模拟事务中途失败：执行真实建表与勾选写入后，在事务提交前抛出异常
    def mock_apply_midway_fail(conn: sqlite3.Connection, rules_path=None):
        real_apply(conn, rules_path=rules_path)
        # 验证此时事务内已有新表与勾选数据
        count = conn.execute("SELECT COUNT(*) FROM strict_industry_selection").fetchone()[0]
        assert count == 14
        raise sqlite3.OperationalError("simulated mid-transaction failure before commit")

    monkeypatch.setattr(jet.db.migrations, "_apply_v7_schema_changes", mock_apply_midway_fail)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v7(db_path)

    assert "simulated mid-transaction failure before commit" in str(exc_info.value) or "v7" in str(exc_info.value)

    # 验证中途失败后：事务完整回滚，版本仍为 6，新表与勾选均不存在
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 6
        tables = {r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" not in tables
    finally:
        check_conn.close()

    # 2. 解除异常模拟，再次执行迁移应顺利成功
    monkeypatch.undo()

    res = migrate_to_v7(db_path)
    assert res.from_version == 6
    assert res.to_version == 7

    # 迁移成功后版本为 7，新表与全部 14 条勾选均已持久化
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "strict_industry_selection" in tables
        assert conn.execute("SELECT COUNT(*) FROM strict_industry_selection").fetchone()[0] == 14
    finally:
        conn.close()


def _setup_v7_db(db_path: Path) -> None:
    """Create a database with schema v7 and sample rows."""
    _setup_v6_db(db_path)
    from jet.db.migrations import migrate_to_v7
    migrate_to_v7(db_path)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    now = utc_now()
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, purpose, provider, model, started_at, outcome, billed) "
        "VALUES (10, 'me', 'judge', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
        (now,),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, purpose, provider, model, started_at, outcome, billed) "
        "VALUES (11, 'me', 'assist', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
        (now,),
    )
    conn.close()


def test_migration_v7_to_v8_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v7_v8"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v7_db(db_path)

    # Pre-condition check
    conn_pre = sqlite3.connect(str(db_path))
    try:
        assert conn_pre.execute("PRAGMA user_version").fetchone()[0] == 7
        tables_pre = {r[0] for r in conn_pre.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "prejudgements" not in tables_pre
        settings_cols = [r[1] for r in conn_pre.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_prejudge_limit" not in settings_cols
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_to_v8

    res = migrate_to_v8(db_path)
    assert res.from_version == 7
    assert res.to_version == 8
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v7")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v7"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 7
        bak_tables = {r[0] for r in bak_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "prejudgements" not in bak_tables
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # Tables exist
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "prejudgements" in tables

        # user_settings has daily_prejudge_limit default 20
        settings_rows = conn.execute("SELECT user_id, daily_prejudge_limit FROM user_settings ORDER BY user_id").fetchall()
        assert len(settings_rows) >= 2
        for s in settings_rows:
            assert s["daily_prejudge_limit"] == 20

        # Range 0-200 constraint
        conn.execute("UPDATE user_settings SET daily_prejudge_limit = 0 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_prejudge_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_prejudge_limit"] == 0
        conn.execute("UPDATE user_settings SET daily_prejudge_limit = 200 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_prejudge_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_prejudge_limit"] == 200
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_prejudge_limit = -1 WHERE user_id = 'me'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_prejudge_limit = 201 WHERE user_id = 'me'")

        # llm_calls: historical rows preserved
        calls = conn.execute("SELECT id, purpose FROM llm_calls ORDER BY id").fetchall()
        assert any(c["id"] == 10 and c["purpose"] == "judge" for c in calls)
        assert any(c["id"] == 11 and c["purpose"] == "assist" for c in calls)

        # llm_calls: supports purpose='prejudge'
        now = utc_now()
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'prejudge', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
                "VALUES ('me', 'invalid_purpose', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
                (now,),
            )

        # prejudgements table insertion and unique constraint
        conn.execute(
            "INSERT INTO prejudgements (user_id, job_id, level, reason, profile_id, engine, created_at) "
            "VALUES ('me', 1, 'open', '适合', 1, 'deepseek-flash:no-think', ?)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            # Duplicate user_id, job_id, profile_id
            conn.execute(
                "INSERT INTO prejudgements (user_id, job_id, level, reason, profile_id, engine, created_at) "
                "VALUES ('me', 1, 'skip', '不适合', 1, 'deepseek-flash:no-think', ?)",
                (now,),
            )
        # Invalid level
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO prejudgements (user_id, job_id, level, reason, profile_id, engine, created_at) "
                "VALUES ('me', 2, 'bad_level', '原因', 1, 'deepseek-flash:no-think', ?)",
                (now,),
            )

        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_v7_to_v8_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v7_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v7_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v8

    def mock_apply_v8_schema_changes(conn):
        conn.execute("ALTER TABLE user_settings ADD COLUMN daily_prejudge_limit INTEGER")
        raise sqlite3.OperationalError("simulated error during v8 migration")

    monkeypatch.setattr(jet.db.migrations, "_apply_v8_schema_changes", mock_apply_v8_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v8(db_path)

    assert "simulated error during v8 migration" in str(exc_info.value) or "v8" in str(exc_info.value)

    # user_version remains 7
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 7
        tables = {r[0] for r in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "prejudgements" not in tables
    finally:
        check_conn.close()

    bak_files = list(data_dir.glob("jet.db.bak-*-v7"))
    assert len(bak_files) >= 1


def test_migration_from_v7_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v7_v8_init_db"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v7_db(db_path)

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 7
    assert res.to_version == LATEST_VERSION

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        s = conn.execute("SELECT daily_prejudge_limit FROM user_settings WHERE user_id = 'me'").fetchone()
        assert s["daily_prejudge_limit"] == 20
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def _setup_v8_db(db_path: Path) -> None:
    """Create a database with schema v8 and sample rows."""
    _setup_v7_db(db_path)
    from jet.db.migrations import migrate_to_v8
    migrate_to_v8(db_path)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    now = utc_now()
    # Add resume_slots with old schema (user_id, slot, name, job_types, updated_at)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS resume_slots (
        user_id TEXT NOT NULL,
        slot INTEGER NOT NULL CHECK(slot IN (1, 2, 3)),
        name TEXT NOT NULL CHECK(length(name) <= 100),
        job_types TEXT NOT NULL CHECK(length(job_types) <= 200),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, slot),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    conn.execute(
        "INSERT INTO resume_slots (user_id, slot, name, job_types, updated_at) "
        "VALUES ('me', 1, '李明简历', 'Python开发、FastAPI', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO resume_slots (user_id, slot, name, job_types, updated_at) "
        "VALUES ('me', 2, '数据分析简历', 'SQL、数据挖掘', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO llm_calls (id, user_id, purpose, provider, model, started_at, outcome, billed) "
        "VALUES (20, 'me', 'prejudge', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
        (now,),
    )
    conn.close()


def test_migration_v8_to_v9_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v8_v9"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v8_db(db_path)

    # Pre-condition check
    conn_pre = sqlite3.connect(str(db_path))
    try:
        assert conn_pre.execute("PRAGMA user_version").fetchone()[0] == 8
        resume_cols = [r[1] for r in conn_pre.execute("PRAGMA table_info(resume_slots)").fetchall()]
        assert "job_types" in resume_cols
        assert "profile" not in resume_cols
        assert "resume_text" not in resume_cols
        settings_cols = [r[1] for r in conn_pre.execute("PRAGMA table_info(user_settings)").fetchall()]
        assert "daily_resume_profile_limit" not in settings_cols
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_to_v9

    res = migrate_to_v9(db_path)
    assert res.from_version == 8
    assert res.to_version == 9
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v8")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v8"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 8
        b_cols = [r[1] for r in bak_conn.execute("PRAGMA table_info(resume_slots)").fetchall()]
        assert "job_types" in b_cols
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # resume_slots structure and data migrated
        resume_cols = [r[1] for r in conn.execute("PRAGMA table_info(resume_slots)").fetchall()]
        assert "profile" in resume_cols
        assert "resume_text" in resume_cols
        assert "job_types" not in resume_cols

        resumes = conn.execute("SELECT user_id, slot, name, resume_text, profile FROM resume_slots ORDER BY slot").fetchall()
        assert len(resumes) == 2
        assert resumes[0]["slot"] == 1
        assert resumes[0]["name"] == "李明简历"
        assert resumes[0]["resume_text"] is None
        assert resumes[0]["profile"] == "Python开发、FastAPI"

        assert resumes[1]["slot"] == 2
        assert resumes[1]["name"] == "数据分析简历"
        assert resumes[1]["resume_text"] is None
        assert resumes[1]["profile"] == "SQL、数据挖掘"

        # user_settings has daily_resume_profile_limit default 10
        settings_rows = conn.execute("SELECT user_id, daily_resume_profile_limit FROM user_settings ORDER BY user_id").fetchall()
        assert len(settings_rows) >= 2
        for s in settings_rows:
            assert s["daily_resume_profile_limit"] == 10

        # Range 0-50 constraint
        conn.execute("UPDATE user_settings SET daily_resume_profile_limit = 0 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_resume_profile_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_resume_profile_limit"] == 0
        conn.execute("UPDATE user_settings SET daily_resume_profile_limit = 50 WHERE user_id = 'me'")
        assert conn.execute("SELECT daily_resume_profile_limit FROM user_settings WHERE user_id = 'me'").fetchone()["daily_resume_profile_limit"] == 50
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_resume_profile_limit = -1 WHERE user_id = 'me'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE user_settings SET daily_resume_profile_limit = 51 WHERE user_id = 'me'")

        # llm_calls: historical rows preserved
        calls = conn.execute("SELECT id, purpose FROM llm_calls ORDER BY id").fetchall()
        assert any(c["id"] == 10 and c["purpose"] == "judge" for c in calls)
        assert any(c["id"] == 11 and c["purpose"] == "assist" for c in calls)
        assert any(c["id"] == 20 and c["purpose"] == "prejudge" for c in calls)

        # llm_calls CHECK constraint allows 'resume_profile'
        now = utc_now()
        conn.execute(
            "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
            "VALUES ('me', 'resume_profile', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO llm_calls (user_id, purpose, provider, model, started_at, outcome, billed) "
                "VALUES ('me', 'invalid_purpose', 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
                (now,),
            )
    finally:
        conn.close()


def test_migration_v8_to_v9_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v8_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v8_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v9

    def mock_apply_v9_schema_changes(conn):
        conn.execute("ALTER TABLE user_settings ADD COLUMN daily_resume_profile_limit INTEGER")
        raise sqlite3.OperationalError("simulated error during v9 migration")

    monkeypatch.setattr(jet.db.migrations, "_apply_v9_schema_changes", mock_apply_v9_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v9(db_path)

    assert "simulated error during v9 migration" in str(exc_info.value) or "v9" in str(exc_info.value)

    # user_version remains 8
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 8
        cols = [r[1] for r in check_conn.execute("PRAGMA table_info(resume_slots)").fetchall()]
        assert "job_types" in cols
        assert "profile" not in cols
    finally:
        check_conn.close()

    bak_files = list(data_dir.glob("jet.db.bak-*-v8"))
    assert len(bak_files) >= 1


def test_migration_from_v8_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v8_v9_init_db"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v8_db(db_path)

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 8
    assert res.to_version == LATEST_VERSION

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        s = conn.execute("SELECT daily_resume_profile_limit FROM user_settings WHERE user_id = 'me'").fetchone()
        assert s["daily_resume_profile_limit"] == 10
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_v8_to_v9_overlong_job_types_preserved(tmp_path: Path):
    """测试迁移 v9 时：旧 job_types 即使超过 300 汉字也能完整迁移为 profile，断言完整保留 (不在数据库层加长度 CHECK)。"""
    data_dir = tmp_path / "mig_v8_v9_overlong"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v8_db(db_path)

    long_job_types = "Python高并发架构设计、微服务治理、分布式缓存优化、千万级消息队列中间件调优及大数据管道处理实践。" * 10
    assert len(long_job_types) > 300

    conn_setup = sqlite3.connect(str(db_path))
    try:
        # 重建无 <=200 CHECK 的旧表以容纳长文本测试用例
        conn_setup.execute("CREATE TABLE r_bak AS SELECT * FROM resume_slots")
        conn_setup.execute("DROP TABLE resume_slots")
        conn_setup.execute("""
        CREATE TABLE resume_slots (
            user_id TEXT NOT NULL,
            slot INTEGER NOT NULL,
            name TEXT NOT NULL,
            job_types TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, slot),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        conn_setup.execute("INSERT INTO resume_slots SELECT * FROM r_bak")
        conn_setup.execute("DROP TABLE r_bak")
        conn_setup.execute(
            "UPDATE resume_slots SET job_types = ? WHERE user_id = 'me' AND slot = 1",
            (long_job_types,),
        )
        conn_setup.commit()
    finally:
        conn_setup.close()

    from jet.db.migrations import migrate_to_v9
    res = migrate_to_v9(db_path)
    assert res.to_version == 9

    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT profile FROM resume_slots WHERE user_id = 'me' AND slot = 1"
        ).fetchone()
        assert row is not None
        assert row["profile"] == long_job_types
        assert len(row["profile"]) == len(long_job_types)
        assert len(row["profile"]) > 300
    finally:
        conn.close()


def _setup_v9_db(db_path: Path) -> None:
    """Create a database with schema v9 and sample rows."""
    _setup_v8_db(db_path)
    from jet.db.migrations import migrate_to_v9
    migrate_to_v9(db_path)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    now = utc_now()
    # Add a sample job without experience_req / degree_req
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, company_name, company_industry, first_seen_at, last_seen_at) "
        "VALUES (99, 'boss', 'job_v9_sample', 'full', NULL, '测试公司', '计算机软件', ?, ?)",
        (now, now),
    )
    conn.close()


def test_migration_v9_to_v10_success(tmp_path: Path):
    data_dir = tmp_path / "mig_v9_v10"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v9_db(db_path)

    # Pre-condition check
    conn_pre = sqlite3.connect(str(db_path))
    try:
        assert conn_pre.execute("PRAGMA user_version").fetchone()[0] == 9
        job_cols = [r[1] for r in conn_pre.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "experience_req" not in job_cols
        assert "degree_req" not in job_cols
    finally:
        conn_pre.close()

    from jet.db.migrations import migrate_to_v10

    res = migrate_to_v10(db_path)
    assert res.from_version == 9
    assert res.to_version == 10
    assert res.backup_filename.startswith("jet.db.bak-")
    assert res.backup_filename.endswith("-v9")

    # 1. Backup file verification
    bak_files = list(data_dir.glob("jet.db.bak-*-v9"))
    assert len(bak_files) == 1
    bak_conn = sqlite3.connect(str(bak_files[0]))
    try:
        assert bak_conn.execute("PRAGMA user_version").fetchone()[0] == 9
        b_cols = [r[1] for r in bak_conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "experience_req" not in b_cols
        assert "degree_req" not in b_cols
    finally:
        bak_conn.close()

    # 2. Migrated database verification
    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # jobs table has experience_req and degree_req
        job_cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "experience_req" in job_cols
        assert "degree_req" in job_cols

        # existing row 99 has NULL for new columns
        row99 = conn.execute("SELECT * FROM jobs WHERE id = 99").fetchone()
        assert row99 is not None
        assert row99["company_name"] == "测试公司"
        assert row99["experience_req"] is None
        assert row99["degree_req"] is None

        # new job can be inserted with experience_req and degree_req
        now = utc_now()
        conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, current_version_id, company_name, company_industry, experience_req, degree_req, first_seen_at, last_seen_at) "
            "VALUES ('boss', 'job_v10_new', 'full', NULL, '新公司', '互联网', '3-5年', '本科', ?, ?)",
            (now, now),
        )
        row_new = conn.execute("SELECT * FROM jobs WHERE platform_job_id = 'job_v10_new'").fetchone()
        assert row_new is not None
        assert row_new["experience_req"] == "3-5年"
        assert row_new["degree_req"] == "本科"
    finally:
        conn.close()


def test_migration_v9_to_v10_failure_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "mig_v9_fail"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v9_db(db_path)

    import jet.db.migrations
    from jet.db.migrations import MigrationError, migrate_to_v10

    def mock_apply_v10_schema_changes(conn):
        conn.execute("ALTER TABLE jobs ADD COLUMN experience_req TEXT")
        raise sqlite3.OperationalError("simulated error during v10 migration")

    monkeypatch.setattr(jet.db.migrations, "_apply_v10_schema_changes", mock_apply_v10_schema_changes)

    with pytest.raises(MigrationError) as exc_info:
        migrate_to_v10(db_path)

    assert "simulated error during v10 migration" in str(exc_info.value) or "v10" in str(exc_info.value)

    # user_version remains 9
    check_conn = sqlite3.connect(str(db_path))
    try:
        assert check_conn.execute("PRAGMA user_version").fetchone()[0] == 9
        cols = [r[1] for r in check_conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "experience_req" not in cols
        assert "degree_req" not in cols
    finally:
        check_conn.close()

    bak_files = list(data_dir.glob("jet.db.bak-*-v9"))
    assert len(bak_files) >= 1


def test_migration_from_v9_via_init_db(tmp_path: Path):
    data_dir = tmp_path / "mig_v9_v10_init_db"
    data_dir.mkdir()
    db_path = data_dir / "jet.db"
    _setup_v9_db(db_path)

    res = init_db(data_dir)
    assert res is not None
    assert res.from_version == 9
    assert res.to_version == LATEST_VERSION

    conn = connect(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "experience_req" in cols
        assert "degree_req" in cols
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
