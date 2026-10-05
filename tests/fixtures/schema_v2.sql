-- Jet Database Schema (Version 2 - US7)
-- Preserved for migration testing

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
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_judgements_active
ON judgements(user_id, job_version_id, profile_id)
WHERE superseded_by IS NULL;

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
