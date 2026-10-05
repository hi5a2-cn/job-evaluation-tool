# Data Model: 岗位信息跨页面联通（第一批）

**Feature**: `005-cross-page-job-link` | **Date**: 2026-09-29

本功能在现有数据模型（user_version 6）基础上新增一张用户关联表，调整已有岗位条目计算字段与岗位库统计口径，不修改现有表的字段定义与外键约束。

---

## 一、新增实体：聊天遇到记录（`job_chat_seen`）

记录某位用户在招聘平台聊天会话中遇到过某个岗位的事实，以及在聊天中读到的职位名称。每个用户对同一个岗位只有一条记录。

### 1. DDL 定义（追加到 `src/jet/db/schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS job_chat_seen (
    user_id TEXT NOT NULL REFERENCES users(id),
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    chat_title TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(user_id, job_id)
);
```

### 2. 字段规范与约束

| 字段 | 类型 | 约束 | 来源与说明 |
|---|---|---|---|
| `user_id` | TEXT | NOT NULL, REFERENCES users(id) | 所属用户 ID（第一阶段固定为 `'me'`） |
| `job_id` | INTEGER | NOT NULL, REFERENCES jobs(id) | 对应公共岗位库 `jobs.id` |
| `chat_title` | TEXT | NOT NULL | 聊天页当前会话展示的职位名（去除首尾空白） |
| `first_seen_at` | TEXT | NOT NULL | 首次在聊天中遇到该岗位的时间（UTC ISO-8601，带 Z） |
| `last_seen_at` | TEXT | NOT NULL | 最近一次在聊天中遇到该岗位的时间（UTC ISO-8601，带 Z） |

- **主键**：`(user_id, job_id)`，保证每个用户对每个岗位仅一条聊天遇到记录。
- **Upsert 语义**：
  ```sql
  INSERT INTO job_chat_seen (user_id, job_id, chat_title, first_seen_at, last_seen_at)
  VALUES (?, ?, ?, ?, ?)
  ON CONFLICT(user_id, job_id) DO UPDATE SET
      last_seen_at = excluded.last_seen_at,
      chat_title = CASE
          WHEN length(trim(job_chat_seen.chat_title)) = 0 THEN excluded.chat_title
          ELSE job_chat_seen.chat_title
      END;
  ```

---

## 二、已有表行为变化

### 1. 公共岗位库（`jobs` 表）

聊天入库时（`POST /v1/chat/job`）：

| 字段 | 首次遇到该岗位时写入值 | 岗位已存在时更新规则 |
|---|---|---|
| `platform` | `'boss'` | 保持不变 |
| `platform_job_id` | 页面读到的 `encrypt_job_id` | 保持不变 |
| `completeness` | `'list_only'` | 保持不变（已是 `full` 绝不降级） |
| `current_version_id` | `NULL` | 保持不变（绝不覆盖已有版本 ID） |
| `company_name` | 页面读到的公司名（为空则 `NULL`） | **仅在现有 `company_name` 为 NULL 或空串时补填**；已有非空公司名绝不覆盖 |
| `company_industry` | `NULL` | 保持不变 |
| `first_seen_at` | 当前时间（UTC ISO-8601） | 保持不变 |
| `last_seen_at` | 当前时间（UTC ISO-8601） | 更新为当前时间 |

- **关键约束**：聊天入库**绝对不写** `job_versions` 表（因为 `job_versions` 中的 `city`、`salary_visible`、`content_hash` 等均为 `NOT NULL`，聊天页无法提供这些数据）。
- **后续版本晋级机制（补全详情时）**：
  当用户随后打开该岗位的独立职位页（004 流程）或搜索列表页时，`jobs.py` 的合并逻辑检测到 `existing_job.current_version_id IS NULL` 时，将自动作为**第 1 版**（`version_no = 1`）插入 `job_versions`，并将 `current_version_id` 指向该新建版本，将 `completeness` 升级为 `'full'`（独立职位页）或保持 `'list_only'`（搜索列表页）。

### 2. 投递状态（`job_status` 表，沿用）

- 侧边栏当前岗位卡片修改投递状态时，完全复用现有 `job_status` 与 `job_status_events` 表结构与写入逻辑。
- 聊天自动入库**绝不写** `job_status`，不默认添加任何投递状态。

---

## 三、查询口径与衍生字段

### 1. 岗位库「全部岗位」统计与查询口径（D3）

`all_jobs` 口径扩展为四大事实的并集：
$$\text{all\_jobs} = \text{有状态} \cup \text{判断过} \cup \text{有非空 HR 记录} \cup \text{job\_chat\_seen 中有该用户记录}$$

#### SQL 计数逻辑（`list_my_jobs` 中的 `count_all_jobs`）：
```sql
WITH all_target_jobs AS (
    SELECT job_id FROM job_status WHERE user_id = ?
    UNION
    SELECT job_id FROM judgements WHERE user_id = ?
    UNION
    SELECT job_id FROM hr_notes WHERE user_id = ? AND length(trim(note)) > 0
    UNION
    SELECT job_id FROM job_chat_seen WHERE user_id = ?
)
SELECT COUNT(*) FROM all_target_jobs;
```

#### SQL 过滤与检索逻辑（`filter == "all_jobs"` 及搜索 `q`）：
- 过滤条件：`j.id IN (SELECT job_id FROM all_target_jobs)`。
- 关键字搜索 `q`：
  ```sql
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
  ```

### 2. 规范岗位条目（Canonical `_job_entry`）字典定义

服务端返回的岗位条目对象（用于 `GET /v1/judgements`、`POST /v1/chat/job`、`POST /v1/observations` 等接口）：

```python
{
    "completeness": "list_only" | "full",
    "company_name": str | None,
    "title": str | None,             # 优先取当前版本 title，若 current_version_id 为 NULL 则取 job_chat_seen.chat_title
    "judgement": JudgementDict | None,
    "hr_note": str | None,
    "my_status": { "status": "saved" | "applied" | "skipped", "updated_at": str } | None,
    "seen_in_chat": bool,            # 新增：当前用户是否在聊天中遇到过该岗位
}
```

### 3. 插件端列表卡片标记数据模型（List Mark）

用于搜索列表页卡片标记的渲染规划（`buildListMarks` / `planMarks`）：

```javascript
{
    // 原有结论或粗筛提示字段
    verdict_label: string | null,    // "适合投递" / "需要确认" / "粗筛：高风险行业" 等
    verdict_tone: string,           // "green" | "blue" | "yellow" | "red" | "slate" | "gray"
    stale: boolean,
    judged_at: string | null,
    is_hint: boolean,

    // 新增：投递状态字段（源自 listJudgements[pid].my_status）
    status_key: "saved" | "applied" | "skipped" | null,
    status_label: "收藏" | "已投递" | "不考虑" | null,
    status_tone: "saved" | "applied" | "skipped" | null,
}
```

#### 标记元素视觉与布局结构：
- **`status_badge`（状态胶囊标签）**：
  - 位置：已有结论标签或粗筛提示**原位置不动**（沿用 004 布局）；状态胶囊排在其右侧（间距 4px）；卡片没有结论/提示标签时，状态胶囊放在原标签位置（`left = rect.left + 8`）；右侧空间不足时状态胶囊截断或省略文字，不得覆盖已有标签（2026-09-29 Claude 审核改定，不移动 004 已验收的标识）；
  - 尺寸：宽约 40px，高 12px/14px，字号 10px/11px；
  - 配色方案：
    - `saved`（收藏）：中性浅黄底 / 褐黄字；
    - `applied`（已投递）：低饱和绿底 / 深绿字；
    - `skipped`（不考虑）：中性灰底 / 深灰字。
- **`verdict_label`（结论或提示标签）**：
  - 位置与宽度完全维持 004 现状，不因状态胶囊移动。
- **`strip`（左侧边缘色条）**：
  - 若已完成判断：展示对应结论色条（绿色/蓝色/黄色/红色）；
  - 若未完成判断但有粗筛提示：展示中性灰蓝色条（slate）；
  - 仅有投递状态（未判断、无粗筛提示）：不新增色条，只显示状态胶囊。
