-- Jet Database Schema (Version 3 - US7 v4 分类)
-- Preserved for migration testing: src/jet/db/schema.sql as of 3857e9e, the last commit before the v3 -> v4 migration
-- (audit finding 80; table and column set matches a real v3 backup)

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
