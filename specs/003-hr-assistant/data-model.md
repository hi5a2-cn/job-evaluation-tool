# 数据模型：003 HR 沟通助手 + 我的岗位库改版

> 存储：单 SQLite 文件（WAL 模式），位于可注入的数据目录（默认 `~/Library/Application Support/Jet/jet.db`，测试时由参数或环境变量注入临时目录）。
> 所有时间戳以 UTC ISO-8601 格式存储；"今日"按本机时区换算（见 spec 指标定义）。
> 个人数据表每行必须有 `user_id`（NOT NULL + 外键关联 `users(id)`）。
> 数据库结构版本记在 `PRAGMA user_version`。从版本 4 升级到版本 **5**。

---

## 关系总览

```text
users 1─* profiles              (画像版本)
users 1─* views *─1 jobs        (查看记录)
users 1─* judgements *─1 jobs   (岗位判断)
users 1─* labels                (人工标注)
users 1─* hr_notes *─1 jobs     (HR 实际情况，按岗位)
users 1─* job_status *─1 jobs   (岗位状态)
users 1─* job_status_events     (状态变化流水)
users 1─* pairings              (插件配对)
users 1─* experience_items      (【003 新增】我的经历素材，最多 10 条)
users 1─1 llm_consents          (【003 新增】大模型发送同意记录)
users 1─* llm_calls             (【003 改动】大模型调用流水，新增 assist 用途)
jobs 1─* job_versions           (公共岗位版本)
```

---

## 改动与新增表结构

### 1. llm_calls — 大模型调用记录（改动）

| 字段 | 类型 | 约束 / 规则 | 说明 |
|---|---|---|---|
| id | INTEGER PK | AUTOINCREMENT | 内部自增 ID |
| user_id | TEXT NOT NULL | FK → users(id) | 所属用户 |
| judgement_id | INTEGER NULL | FK → judgements(id) | 判断用途时必填；assist 与 eval 用途为 NULL |
| **purpose** | **TEXT NOT NULL** | **DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval', 'assist'))** | **【003 改动】扩展用途：judge（岗位判断）/ eval（评测）/ assist（生成话术）** |
| eval_run_id | TEXT NULL | | 评测用途时的运行 ID |
| provider | TEXT NOT NULL | | 大模型提供商，如 `api.deepseek.com` |
| model | TEXT NOT NULL | | 模型名称，如 `deepseek-flash` |
| started_at | TEXT NOT NULL | | 请求预占/发起时间（UTC ISO-8601） |
| finished_at | TEXT NULL | | 请求完成时间 |
| outcome | TEXT NOT NULL | | `reserved` → `ok` / `timeout` / `http_error` / `parse_error` / `not_sent` |
| billed | INTEGER NOT NULL | | 是否计入额度（1 = 计入，0 = 退还） |
| input_tokens | INTEGER NULL | | 输入 Token 数（来自 usage） |
| cached_tokens | INTEGER NULL | | 缓存命中 Token 数（来自 usage） |
| output_tokens | INTEGER NULL | | 输出 Token 数（来自 usage） |
| cost_cny | REAL NULL | | 按配置单价估算的费用（元） |

> **关键约束与隐私保证（FR-036、FR-037）**：
> - `llm_calls` 表中**绝对不包含任何请求文本内容、聊天原文、脱敏文本或大模型返回的建议话术**。
> - 生成话术用途（`purpose = 'assist'`）记录纯粹用于统计今日调用次数、Token 消耗用量和费用估算。
> - `user_id` NOT NULL，计入"无主人记录 = 0"的自检范畴。

### 2. experience_items — 我的经历素材（003 新增，个人数据）

用户在插件设置页一条一条录入的真实工作经历素材。大模型生成话术时只能引用这些素材，不得虚构。

| 字段 | 类型 | 约束 / 规则 | 说明 |
|---|---|---|---|
| id | INTEGER PK | AUTOINCREMENT | 内部自增 ID |
| user_id | TEXT NOT NULL | FK → users(id) | 所属用户 |
| item_no | INTEGER NOT NULL | CHECK(item_no BETWEEN 1 AND 10) | 条目编号（1–10），用户可见并供大模型引用 |
| content | TEXT NOT NULL | CHECK(length(content) <= 200 AND length(trim(content)) > 0) | 大白话经历内容，≤ 200 字，禁止为空 |
| created_at | TEXT NOT NULL | UTC ISO-8601 | 创建时间 |
| updated_at | TEXT NOT NULL | UTC ISO-8601 | 最近修改时间 |

- **唯一性约束**：`UNIQUE(user_id, item_no)`。每个用户最多 10 条经历素材，编号 1 至 10。
- **无历史版本（FR-033）**：不设历史版本表，修改直接覆写该行并更新 `updated_at`；删除则直接物理删除。
- **数据独立性（FR-029）**：独立于 002 `profiles` 表中的画像字段（`background`、`work_preference` 继续仅用于岗位适合度判断，不作为经历素材）。

### 3. llm_consents — 大模型数据发送同意记录（003 新增，个人数据）

记录用户对于将脱敏聊天数据发送给外部大模型（DeepSeek）的显式知情同意状态。

| 字段 | 类型 | 约束 / 规则 | 说明 |
|---|---|---|---|
| user_id | TEXT PRIMARY KEY | FK → users(id) | 所属用户，每人仅 1 行状态 |
| consented_at | TEXT NOT NULL | UTC ISO-8601 | 用户点击"同意"的时间 |
| revoked_at | TEXT NULL | UTC ISO-8601 | 用户撤回同意的时间；为 NULL 表示当前处于同意状态 |
| fields_version | INTEGER NOT NULL | DEFAULT 1 | 用户同意时的发送字段定义版本号（初始为 1） |
| updated_at | TEXT NOT NULL | UTC ISO-8601 | 状态更新时间 |

- **有效同意判定逻辑**：
  用户处于"有效同意"状态当且仅当满足以下全部条件：
  1. `revoked_at IS NULL`；
  2. `fields_version == 当前系统字段版本`（当前为 1；若未来功能扩展导致发送字段种类增加，字段版本升级为 2，原同意记录因版本不匹配自动失效，必须重新弹出同意页）（FR-025）。
- **撤回同意（FR-026）**：
  用户在设置页点击"撤回同意"时，执行 `UPDATE llm_consents SET revoked_at = utc_now(), updated_at = utc_now() WHERE user_id = ?`。

### 4. user_settings — 用户设置（现有表说明）

| 字段 | 类型 | 约束 / 规则 | 说明 |
|---|---|---|---|
| user_id | TEXT PRIMARY KEY | FK → users(id) | |
| daily_llm_limit | INTEGER NOT NULL | DEFAULT 150 CHECK(...) | 岗位判断每日上限（002） |
| daily_assist_limit | INTEGER NOT NULL | DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500) | **【003 新增列】话术生成每日上限（默认 50，范围 0–500）** |

> 注：`daily_assist_limit` 可通过数据目录 `.env` 中的 `JET_DAILY_ASSIST_LIMIT` 环境变量配置，服务启动时写入/覆盖该值，保持与 002 `daily_llm_limit` 的行为完全一致。

---

## 岗位库查询与改版数据计算方法

### 1. 「全部岗位」范围与去重计算（FR-044、SC-009）

「全部岗位」包含当前用户产生过任何交互的所有岗位。定义为以下三个集合的**并集（去重）**：
1. **有状态的岗位**：`SELECT job_id FROM job_status WHERE user_id = :uid`
2. **判断过的岗位**：`SELECT DISTINCT job_id FROM judgements WHERE user_id = :uid`
3. **有 HR 实际情况记录的岗位**：`SELECT job_id FROM hr_notes WHERE user_id = :uid AND length(trim(note)) > 0`

**SQL 集合计算**：
```sql
WITH all_target_jobs AS (
    SELECT job_id FROM job_status WHERE user_id = :uid
    UNION
    SELECT job_id FROM judgements WHERE user_id = :uid
    UNION
    SELECT job_id FROM hr_notes WHERE user_id = :uid AND length(trim(note)) > 0
)
SELECT COUNT(*) FROM all_target_jobs;
```

### 2. 「最近更新时间」计算规范（spec R7、FR-051）

单个岗位针对当前用户的"最近更新时间"（`recent_updated_at`），定义为以下 4 个时间点中最晚的一个（仅限当前用户，列表页读到不算）：
1. **状态最后一次变化时间**：`job_status.updated_at`（无状态则为 NULL）
2. **HR 实际情况最后一次保存时间**：`hr_notes.updated_at`（无记录则为 NULL）
3. **最后一次打开详情页时间**：`MAX(views.seen_at) WHERE page_type = 'detail'`（未打开过详情则为 NULL）
4. **最新判断创建时间**：`MAX(judgements.created_at)`（无判断则为 NULL）

**SQL 聚合计算公式**：
```sql
MAX(
    COALESCE(js.updated_at, ''),
    COALESCE(hn.updated_at, ''),
    COALESCE(v.max_seen_at, ''),
    COALESCE(jd.max_created_at, '')
) AS recent_updated_at
```
「全部岗位」（`filter=all_jobs`）和搜索结果列表**严格按照 `recent_updated_at DESC, j.id DESC`** 倒序排列。

### 3. 「只看有 HR 记录」过滤与各标签数量（FR-047）

- **过滤行为**：当请求参数 `hr_only = true` 时，列表查询强制连接 `hr_notes`：
  `JOIN hr_notes hn ON hn.job_id = j.id AND hn.user_id = :uid AND length(trim(hn.note)) > 0`
- **标签旁数量显示（counts）**：
  - `hr_only = false`：
    - `all_jobs`：全部岗位并集总数；
    - `all`（已标记）：`COUNT(*) FROM job_status WHERE user_id = :uid`；
    - `saved` / `applied` / `skipped`：对应状态岗位数；
    - `recent`（最近看过）：`COUNT(DISTINCT job_id) FROM judgements WHERE user_id = :uid`。
  - `hr_only = true`：
    - 各标签统计数量立即变为：**该分类下存在 HR 实际情况记录（且非空）的岗位数**。

### 4. 岗位库搜索匹配规则（FR-048、FR-049、FR-050）

- **搜索范围**：当前选中的标签（`filter`）以及 `hr_only` 过滤条件下的全量岗位（不受单次 200 条限制）。
- **字段匹配**：
  1. **职位名**：匹配 `job_versions` 中该岗位**所有历史版本**的 `title`（即使当前版本职位名被改动，搜历史版本仍能搜到）；
  2. **公司名**：匹配 `jobs.company_name`；
  3. **HR 实际情况**：匹配 `hr_notes.note`。
- **转义规则**：
  SQLite `LIKE` 查询中，用户输入的 `%` 和 `_` 属于通配符。服务端在拼装查询前执行字符转义：
  ```python
  def escape_like(query: str, escape_char: str = "/") -> str:
      return (
          query.replace(escape_char, escape_char + escape_char)
               .replace("%", escape_char + "%")
               .replace("_", escape_char + "_")
      )
  ```
  SQL 语句统一添加 `ESCAPE '/'`：
  ```sql
  WHERE (
      jv.title LIKE :pattern ESCAPE '/'
      OR j.company_name LIKE :pattern ESCAPE '/'
      OR hn.note LIKE :pattern ESCAPE '/'
  )
  ```
- **截断与提示**：单次最多返回 `limit = 200` 条。如果满足条件的去重总数 `N > 200`，接口返回 `total_matches = N`，前端展示"只显示了前 200 条，共 N 条"。

---

## 额度扣减与并发安全状态机

```text
               [用户点击生成]
                     │
         检查同意状态 (llm_consents)
                     │
        ┌────────────┴────────────┐
       有效                      未同意/已撤回
        │                         │
  BEGIN IMMEDIATE 预占额度        返回 403 (前端弹同意页)
        │
  ┌─────┴─────────────────┐
今日已达上限             未达上限
  │                       │
返回 429             插入 llm_calls (outcome='reserved', billed=1)
(今日生成次数已用完)      │
                     纯函数重新脱敏并比对哈希 (prompt_hash)
                          │
                   ┌──────┴──────┐
                  一致          不一致
                   │             │
             调用大模型      退还额度 (billed=0, outcome='not_sent')
                   │         返回 400 (内容被篡改)
             ┌─────┴─────┐
            成功        失败/超时
             │           │
       校验经历条目编号   记录 llm_calls (billed=1, outcome='http_error'/'timeout')
             │           返回 502/504
       返回生成话术建议
       (内存临时保存)
```

- **预占机制**：在 `src/jet/llm/quota.py` 中，使用 `BEGIN IMMEDIATE` 事务计算当天 `purpose = 'assist'` 且 `billed = 1` 的记录数。未超过 `daily_assist_limit` 时才插入一条 `reserved` 记录。
- **退还机制**：若在本地参数校验失败或哈希比对不一致而未发出外部 HTTP 请求，更新为 `outcome = 'not_sent', billed = 0` 退还额度；若已向 DeepSeek 发出请求但发生超时或网络断开，按保守原则保持 `billed = 1`。

---

## 结构迁移（版本 4 → 5）

1. **版本识别**：`PRAGMA user_version` 当前为 4。迁移目标为 5。
2. **前置备份**：
   - 检查 `PRAGMA user_version == 4`；
   - 执行 `PRAGMA wal_checkpoint(TRUNCATE)` 清理 WAL；
   - 将 `jet.db` 复制备份为同目录的 `jet.db.bak-<UTC 时间戳>-v4`。
3. **事务内迁移**：
   - 设置 `PRAGMA foreign_keys = OFF`；
   - 记录各既有表的总行数（`counts_before`）；
   - 执行 `BEGIN IMMEDIATE`；
   - **重建 llm_calls 表**：
     - 创建临时表 `llm_calls_new`，CHECK 约束扩展为 `CHECK(purpose IN ('judge', 'eval', 'assist'))`；
     - `INSERT INTO llm_calls_new SELECT * FROM llm_calls`；
     - `DROP TABLE llm_calls`；
     - `ALTER TABLE llm_calls_new RENAME TO llm_calls`；
   - **创建新表**：
     - `CREATE TABLE IF NOT EXISTS experience_items (...)`；
     - `CREATE TABLE IF NOT EXISTS llm_consents (...)`；
   - **增加配置列**：
     - 检查 `user_settings` 表，若无 `daily_assist_limit` 列则执行 `ALTER TABLE user_settings ADD COLUMN daily_assist_limit INTEGER NOT NULL DEFAULT 50`；
   - **完整性检查**：
     - 校验迁移后各表行数与 `counts_before` 完全相等；
     - 执行 `PRAGMA foreign_key_check`，确保外键检查结果为空；
     - 执行 `COMMIT`。
4. **版本更新**：
   - 事务外部执行 `PRAGMA user_version = 5`；
   - 重新开启 `PRAGMA foreign_keys = ON`。
