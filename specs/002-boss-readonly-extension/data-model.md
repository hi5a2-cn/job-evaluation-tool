# 数据模型：002 BOSS 只读插件 + 本机判断

> 存储：一个 SQLite 文件（WAL 模式），位于可注入的数据目录（默认 `~/Library/Application Support/Jet/jet.db`，测试时由参数指定临时目录）。
> 所有时间戳以 UTC ISO-8601 存储；"今日"按本机时区换算（见 spec 指标定义）。
> 公共表：`jobs`、`job_versions`。个人表（每行必须有 `user_id`，NOT NULL + 外键）：`profiles`、`views`、`judgements`、`labels`、`llm_calls`、`pairings`、`user_settings`。
> 数据库结构版本记在 `PRAGMA user_version`（见文末"结构迁移"）。当前版本：**4**（2026-09-25：judgements.origin）。

## 关系总览

```text
users 1─* profiles        (画像版本)
users 1─* views *─1 jobs  (查看记录)
users 1─* judgements *─1 job_versions *─1 jobs
                 *─1 profiles
judgements 1─* llm_calls  (调用记录，也按 user_id 计额度；评测调用 judgement_id 为空)
users 1─* labels *─1 job_versions  (标注 / 评测集)
users 1─* pairings        (插件配对)
jobs 1─* job_versions     (岗位版本，jobs.current_version_id 指向最新)
```

## 公共数据（不属于任何个人）

### jobs — 公共岗位

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | 内部 ID |
| platform | TEXT NOT NULL | 本版固定 `boss` |
| platform_job_id | TEXT NOT NULL | BOSS 的 `encryptJobId`；`UNIQUE(platform, platform_job_id)`（FR-009） |
| completeness | TEXT NOT NULL | `list_only` / `full`（FR-010）；只能从 `list_only` → `full`，不能反向 |
| current_version_id | INTEGER FK → job_versions.id | 最新版本 |
| first_seen_at / last_seen_at | TEXT NOT NULL | 任一用户首次 / 最近读到的时间 |
| company_name | TEXT NULL | 公司名（US8，页面读取到才有；NULL 界面显示"公司未读取"）；有值后再读到空值不覆盖 |

不存任何用户字段（原则 II：其他用户看不到谁看过）。

### job_versions — 岗位版本

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| job_id | INTEGER FK NOT NULL | |
| version_no | INTEGER NOT NULL | 每个岗位从 1 递增；`UNIQUE(job_id, version_no)` |
| source | TEXT NOT NULL | `list` / `detail`（这个版本由哪类页面读到） |
| title | TEXT NOT NULL | 原文，不改动 |
| salary_raw | TEXT NULL | 原文，如 `15-25K·14薪`；读到空值时为 NULL |
| salary_visible | INTEGER NOT NULL | 0 = "薪资不可见"（FR-013）；1 = 有值。"不可见"与"值为空字符串"不混用 |
| salary_min_k / salary_max_k | REAL NULL | 解析出的月薪区间（千元/月）；无法解析时为 NULL，并且 `salary_parse_ok = 0` |
| salary_months | INTEGER NULL | `·N薪` 中的 N |
| salary_parse_ok | INTEGER NOT NULL | 解析失败 ≠ 薪资低；规则层把它当"不知道" |
| city | TEXT NOT NULL | 原文 |
| district | TEXT NULL | 空 → NULL，界面显示"未提供" |
| description | TEXT NULL | 完整职位描述原文；`source = list` 时为 NULL |
| content_hash | TEXT NOT NULL | 见下方"变化判定" |
| created_at | TEXT NOT NULL | |

**变化判定（FR-011）**：实质字段 = 职位名、薪资（原文 + 可见性）、城市、职位描述。
- 比较前先做字符规范化（NFKC + 去多余空白），只用于比较，不改存储的原文（FR-019）。
- 从列表页读到 `list_only` 岗位时，没有职位描述，只比较职位名、薪资、城市三项；**缺失的描述不算"变了"**。相同 → 只更新 `jobs.last_seen_at`；不同 → 新增版本，`current_version_id` 指向新版本。
- 已是 `full` 的岗位从列表页读到时，不比较、不新建版本，只更新 `jobs.last_seen_at`；内容变化以详情页为准（Issue #6，2026-09-26）。
- `list_only` 岗位第一次读到详情 → 新增 `source = detail` 的版本，`completeness` 改为 `full`。
- 已是 `full` 的岗位又从列表读到，且三项都没变 → 只更新时间，不降级（FR-010）。

## 个人数据（每行都属于某个用户）

### users

| 字段 | 类型 | 规则 |
|---|---|---|
| id | TEXT PK | 第一阶段只有一行（首次启动时创建，默认 `me`） |
| display_name | TEXT | |
| created_at | TEXT | |

### profiles — 画像（带版本，FR-014/015）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| version_no | INTEGER NOT NULL | 每次保存 +1；`UNIQUE(user_id, version_no)`；当前画像 = 最大版本号 |
| directions | TEXT(JSON) NOT NULL | 岗位类型，字符串数组，至少 1 项 |
| keywords | TEXT(JSON) NOT NULL | 关键词数组，可为空 |
| cities | TEXT(JSON) NOT NULL | **偏好城市**数组（2026-09-25 起含义改变，列名保持不变；接口字段名 `preferred_cities`），可为空数组（= 城市都可以）；只给大模型，不做规则排除；比较时去掉"市"等后缀 |
| excluded_cities | TEXT(JSON) NOT NULL DEFAULT '[]' | 不去的城市（规则排除） |
| nonpref_min_monthly_k | REAL NULL | 非偏好城市的最低月薪（千元/月）；见 FR-048 |
| min_monthly_k | REAL NULL | 最低月薪（千元/月）；NULL = 不设 |
| exclude_keywords | TEXT(JSON) NOT NULL | "不接受"关键词数组 |
| work_preference | TEXT NOT NULL DEFAULT '' | 工作内容偏好，自由文本，≤ 500 字；只给大模型（US7） |
| background | TEXT NOT NULL DEFAULT '' | 我的背景（学历、工作年限、主要经历），自由文本，≤ 500 字；只给大模型（US7） |
| created_at | TEXT NOT NULL | |

保存内容与当前版本完全相同 → 不新增版本（避免把判断无意义地标成"可能过时"）。

### views — 查看记录（FR-012）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| job_id | INTEGER FK NOT NULL | |
| page_type | TEXT NOT NULL | `list` / `detail` |
| seen_at | TEXT NOT NULL | |

插件端已按"同一页面状态只发一次"去重（FR-004），所以一次页面加载每个岗位最多一条记录。

### judgements — 判断

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| job_id | INTEGER FK NOT NULL | |
| job_version_id | INTEGER FK NOT NULL | 判断时的岗位版本 |
| profile_id | INTEGER FK NOT NULL | 判断时的画像版本 |
| status | TEXT NOT NULL | 见状态机 |
| verdict | TEXT NULL | v4 起四档：`apply` 适合投递 / `try` 可以一试 / `check` 需要确认 / `skip` 不建议投；规则排除为 `skip`。旧判断保留 `fit` / `unsure` / `unfit`（界面分别按适合投递 / 需要确认 / 不建议投显示，并标可能过时）；只有 `done` 时有值 |
| source | TEXT NULL | `rule` / `llm` |
| reasons | TEXT(JSON) NULL | 规则判定时写命中的规则；提示词 v1 的 ≤ 3 条理由 |
| prompt_version | TEXT NOT NULL | `rule`（规则排除）/ `v1` / `v2` / `v3`；迁移前的旧判断记为 `v1`（FR-034）。v3 = v2 + 经验要求区分硬性/优先 + 一句话理由 |
| engine | TEXT NULL | 大模型判断所用组合，如 `deepseek-flash:no-think`、`jev+deepseek-flash`；规则判定记规则版本，如 `rules:r3`（当前版本见 `src/jet/domain/rules.py` 的 `RULES_VERSION`）（NULL 视为 `rules:r1`） |
| facts | TEXT(JSON) NULL | v2 的事实部分，结构见下方"事实结构" |
| derivation | TEXT(JSON) NULL | v2/v3 的推导过程：≤ 5 条字符串，每条说明哪项事实与画像哪一条一致或冲突 |
| origin | TEXT NULL | 判断的触发来源：`initial`（首次）/ `auto_refresh`（打开详情时自动更新过时判断，FR-052）/ `manual`（用户点重新判断 / 重试）；结构版本 4 新增，旧行为 NULL |
| review | TEXT(JSON) NULL | 复核（FR-051）：`{"outcome": "pending|downgraded|kept|failed|quota_exhausted", "engine", "first_verdict", "review_verdict", "error"}`；未复核为 NULL（可空列，启动时补列） |
| hr_questions | TEXT(JSON) NULL | v4：结论为 try / check 时 2–3 个建议问 HR 的问题（每个 ≤ 60 字）；其他结论为 NULL（可空列，启动时补列） |
| verdict_reason | TEXT NULL | v3 的一句话理由（≤ 40 字），卡片结论摘要行用；v2 及以前为 NULL，界面用推导第一条代替。（只加可空列，结构版本仍为 2：`init_db` 在列缺失时 `ALTER TABLE ADD COLUMN`） |
| rule_result | TEXT(JSON) NOT NULL | 粗筛结果（通过 / 命中哪些规则），额度用完时也保存（US6-3） |
| error | TEXT NULL | `failed` 时的原因 |
| created_at / finished_at | TEXT | |
| superseded_by | INTEGER NULL FK → judgements.id | 重新判断后指向新判断；旧判断保留为历史（FR-022） |

**唯一性（FR-021）**：`UNIQUE(user_id, job_version_id, profile_id) WHERE superseded_by IS NULL`（部分唯一索引）。
同一用户、同一岗位版本、同一画像版本只有一条"现行"判断；已有 `done` 就直接返回，不再调用大模型。

**"可能过时"是算出来的，不存**（FR-022、FR-034）：
- `job_version_id ≠ jobs.current_version_id` → 可能过时（岗位已变）
- `profile_id ≠ 当前画像 id` → 可能过时（画像已变）
- 大模型判断的 `prompt_version` 低于当前配置的提示词版本 → 可能过时（判断方式已更新）。规则判定（`rule`）不受影响。
- 多个原因同时成立时都显示。

**事实结构（`facts`，提示词 v2）**：每段引用都是 `{"text": 原句, "found": bool}`，`found` 由 Jet 核对（规范化后是否为职位描述的子串，FR-033）。

```json
{
  "summary":     {"text": "白话概括", "quotes": [{"text": "…", "found": true}]},
  "work_type":   {"value": "<大类>|存疑", "subtype": "<细分>|null", "secondary": [{"category": "<大类>", "subtype": "<细分>|null"}], "quotes": [...], "probabilities": {"运营": 0.71, "...": 0.1} | null},
  "sales_level": {"value": "高|中|低|存疑", "signals": [{"signal": "对接客户或渠道", "quote": {"text": "…", "found": true}}], "probabilities": {...} | null},
  "experience":  {"requirement": {"text": "…", "found": true} | null, "requirement_type": "硬性|优先|未提及", "value": "满足|差一点|不满足|无法判断", "gap": "差什么"},
  "work_intensity": {"value": "高强度|单休|大小周|双休|未提及", "quotes": [...]},
  "risk_signals": [{"type": "非法金融|诈骗|传销|收费入职|按交易量提成|名实不符|其他", "description": "一句话说明", "quote": {"text": "…", "found": true}}]   // v4：可为空数组；非空时 verdict 必须为 skip
}
```

`probabilities` 只在事实由 TypeSafe Jev 回答时有值；最高概率低于阈值时 `value` 为 `存疑`。
（v4）`work_type.value` 是**主要类型的大类**，`subtype` 是其细分（必须属于该大类，可为 null；大类"其他"没有细分）；`secondary` 是次要类型（0–3 项，每项 `{category, subtype|null}`，不与主要类型相同）。分类见 spec"工作类型分类"。组合 D 中 Jev 只回答主要大类；DeepSeek 的细分若不属于 Jev 的大类则置 null，次要类型中与主要类型相同的项去掉。v2/v3 旧判断的 `work_type.value` 是旧选项（数据 / 运营 / 营销 / 销售 / 客服 / 技术支持 / 其他），`overtime` 为旧加班字段，界面原样显示。

**状态机**：

```text
          ┌────────── 规则排除 ─────────→ done(unfit, source=rule)
queued ──→ running ── 大模型成功 ──→ done(fit|unfit, source=llm)
   │          ├──── 大模型失败 ──→ failed ──(用户点重试)──→ queued
   │          └──── Jet 被关掉 ──→ interrupted（启动时把遗留 running 改成它）──(再次点开或重试)──→ queued
   └── 粗筛通过但今日额度用完 ──→ quota_exhausted ──(用户手动判断且额度有剩余)──→ queued
```

- 没有画像时不创建判断（FR-016），插件显示"请先设置画像"。
- `list_only` 岗位不创建判断（FR-017）。
- `failed`、`quota_exhausted`、`interrupted` 都不是结论，界面上绝不显示成"不适合"（FR-026）。
- 重试在原判断行上把状态改回 `queued`；"重新判断"（针对可能过时的判断）新建一行，并把旧行的 `superseded_by` 指向它。


### labels — 标注 / 评测集（FR-035、FR-036，US7）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| job_version_id | INTEGER FK NOT NULL | 标注针对的岗位版本；`UNIQUE(user_id, job_version_id)`，再次标注覆盖（只保留最新） |
| judgement_id | INTEGER FK NULL | 标注时卡片显示的判断（用于判定"纠正"） |
| work_type | TEXT NULL | 主要类型的大类（8 个大类之一） |
| work_subtype | TEXT NULL | 主要类型的细分，必须属于 `work_type`；选填 |
| secondary_work_types | TEXT(JSON) NULL | 次要类型数组 `[{category, subtype|null}]`，0–3 项，不重复、不与主要类型相同；选填；只记录、展示，不参与纠正与评测 |
| sales_level | TEXT NULL | 高 / 中 / 低 |
| experience_fit | TEXT NULL | 满足 / 差一点 / 不满足 / 无法判断 |
| work_intensity | TEXT NULL | 工作强度：高强度 / 单休 / 大小周 / 双休 / 未提及（原 `overtime` 列，结构版本 3 迁移时改名并转换取值） |
| overall | TEXT NULL | `apply` / `try` / `check` / `skip`，选填 |
| note | TEXT NULL | 选填，≤ 100 字 |
| created_at / updated_at | TEXT NOT NULL | |

- 至少填一项事实或总体结论，否则拒绝保存。
- **纠正**是算出来的：某项已填的标注与 `judgement_id` 对应判断的同项事实（或总体结论）不一致。
- 评测时，以标注为准答案；只比较标注里已填的项；工作类型只比较主要类型。

### hr_notes — HR 说的实际情况（FR-041）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| job_id | INTEGER FK NOT NULL | 按岗位保存（不按版本）；`UNIQUE(user_id, job_id)` |
| note | TEXT NOT NULL | 一句话，≤ 200 字；清空即删除该行 |
| created_at / updated_at | TEXT NOT NULL | |

### reference_labels — 模型参考标注（FR-042）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | 参考标注对照当前用户的画像（经验、结论依赖画像），属于该用户 |
| job_version_id | INTEGER FK NOT NULL | `UNIQUE(user_id, job_version_id, source)`；再次导入覆盖 |
| source | TEXT NOT NULL | 如 `model:gemini-3.8-flash-high`；界面与报告显示为"模型参考标注，非人工" |
| profile_version_no | INTEGER NOT NULL | 标注时的画像版本 |
| used_hr_note | INTEGER NOT NULL | 标注时是否有 HR 说的实际情况 |
| work_type / work_subtype / secondary_work_types / sales_level / experience_fit / work_intensity / overall | 同 labels | 同 labels 的取值与校验 |
| rationale | TEXT NULL | 标注模型的简短依据（≤ 200 字） |
| overall_void | INTEGER NOT NULL DEFAULT 0 | 1 = 总体结论已作废（FR-050；启动时对已有行一次性置 1，只执行一次）；评测不使用作废的总体结论 |
| created_at | TEXT NOT NULL | |

两张新表都是个人表，计入无主人记录自检；启动时 `CREATE TABLE IF NOT EXISTS` 补建（不升结构版本）。

### job_status — 我的岗位状态（US8，FR-044）

| 字段 | 类型 | 规则 |
|---|---|---|
| user_id | TEXT FK NOT NULL | |
| job_id | INTEGER FK NOT NULL | `PRIMARY KEY(user_id, job_id)` |
| status | TEXT NOT NULL | `saved` 收藏 / `applied` 已投递 / `skipped` 不考虑；取消即删除该行 |
| updated_at | TEXT NOT NULL | |

### job_status_events — 状态变化记录（US8；Issue #3 数据来源）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| job_id | INTEGER FK NOT NULL | |
| status | TEXT NULL | 变化后的状态；NULL = 取消 |
| previous_status | TEXT NULL | 变化前的状态 |
| created_at | TEXT NOT NULL | 只追加，不修改、不删除 |

两张表都是个人表，计入无主人记录自检；启动时补建（不升结构版本）。

### llm_calls — 大模型调用记录（FR-023，指标"今日大模型调用次数"）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| judgement_id | INTEGER FK NULL | 判断用途时必填；评测用途为 NULL |
| purpose | TEXT NOT NULL DEFAULT 'judge' | `judge`（计入每日额度）/ `eval`（单独计数，FR-038） |
| eval_run_id | TEXT NULL | 评测用途时为该次运行 ID |
| provider / model | TEXT NOT NULL | provider 如 `api.deepseek.com`、`typesafe`；model 如 `deepseek-flash`、`jev-latest` |
| started_at / finished_at | TEXT | |
| outcome | TEXT NOT NULL | `reserved` → `ok` / `timeout` / `http_error` / `parse_error` / `not_sent` |
| billed | INTEGER NOT NULL | 是否计入额度（见下） |
| input_tokens / cached_tokens / output_tokens | INTEGER NULL | 来自接口返回的 usage |
| cost_cny | REAL NULL | 按配置单价估算，仅用于显示 |

**额度扣减（原则 VII、SC-003）**：发请求之前，在一个 `BEGIN IMMEDIATE` 事务里数"今日 `purpose = 'judge'` 且 billed = 1 的行数"，小于上限才插入一条 `outcome = reserved, billed = 1` 的行，然后才发请求。
- 请求确定没发出去（连接被拒、本地参数错误）→ 改为 `not_sent, billed = 0`，退还额度。
- 超时、服务端错误、返回无法解析 → 保持 `billed = 1`（无法确认是否计费时按"已计费"处理，保守）。
- 这样即使并发，今日调用次数也不会超过上限。
- 评测调用（`purpose = 'eval'`）用同样的预占方式，但数的是"本次运行 `eval_run_id` 的行数"，上限为评测上限（默认 300），与每日判断额度互不影响。

### user_settings

| 字段 | 类型 | 规则 |
|---|---|---|
| user_id | TEXT PK FK | |
| daily_llm_limit | INTEGER NOT NULL | 默认 150，范围 0–500（0 = 只用规则）；每次 `jet serve` 启动时用配置 `JET_DAILY_LLM_LIMIT` 覆盖 |

页面标记开关（FR-028）只影响插件显示，保存在插件的 `chrome.storage.local`，不进 Jet。

### pairings — 插件配对（FR-007）

| 字段 | 类型 | 规则 |
|---|---|---|
| id | INTEGER PK | |
| user_id | TEXT FK NOT NULL | |
| extension_origin | TEXT NOT NULL | `chrome-extension://<扩展 ID>`，配对时记录，之后每次请求核对 `Origin` |
| token_hash | TEXT NOT NULL | 只存 SHA-256；明文令牌只在配对时返回一次 |
| created_at / last_used_at / revoked_at | TEXT | |

一次性配对码只存在 Jet 进程内存里（6 位数字，5 分钟过期，最多尝试 5 次），不落盘。

## 校验规则汇总

| 规则 | 来源 |
|---|---|
| 个人表 `user_id` NOT NULL + 外键；启动自检"无主人记录数 = 0" | 原则 II、SC-008 |
| `platform_job_id` 唯一 | FR-009 |
| `completeness` 只升不降 | FR-010 |
| 薪资空 → `salary_visible = 0`，不写 0 或空字符串 | FR-013、原则 IV |
| 规则层遇到 `salary_visible = 0` 或 `salary_parse_ok = 0` 时跳过薪资规则 | FR-018 |
| 同一用户 × 岗位版本 × 画像版本只有一条现行判断 | FR-021 |
| 同一用户 × 岗位版本只有一条标注 | FR-035 |
| 引用必须标注 found（核对结果），不删除找不到的引用 | FR-033 |
| 今日 billed 调用数 ≤ 上限，在事务里检查 | FR-023、SC-003 |

## 结构迁移（US7）

- 数据库版本记在 `PRAGMA user_version`：0 或 1 = 初始结构（Phase 1–5），2 = US7。
- `jet serve` 启动时（`init_db`）检查版本；低于当前版本时：
  1. 先把 `jet.db` 复制为同目录的 `jet.db.bak-<UTC 时间戳>-v<旧版本>`（复制前执行 `PRAGMA wal_checkpoint(TRUNCATE)`，保证备份完整）；
  2. 在一个事务里迁移：`profiles` 加两列；`labels` 新建；`llm_calls` 重建（`judgement_id` 改为可空、加 `purpose`、`eval_run_id`）；`judgements` 重建（`verdict` 的 CHECK 加入 `unsure`，加 `prompt_version`、`engine`、`facts`、`derivation`；原有行 `prompt_version` 按 `source` 填：`rule` → `rule`，其他 → `v1`）；
  3. 迁移后行数与迁移前一致，外键检查（`PRAGMA foreign_key_check`）为空，才写入新版本号并提交；否则回滚并拒绝启动（备份保留）。
- 迁移只在版本低于当前版本时执行，重复启动不会重复迁移。

### 结构版本 2 → 3（2026-09-25，工作类型两层、工作强度、四档结论）

- 同样先备份 `jet.db.bak-<UTC 时间戳>-v2`，在事务中重建 `judgements`（`verdict` CHECK 增加 `apply` / `try` / `check` / `skip`，保留旧值）与 `labels`（新 CHECK 与新列），校验行数与外键后写 `user_version=3`。
- `labels` 旧数据转换：
  - `work_type`：运营 → 运营；数据 → 数据与技术；技术支持 → 数据与技术 / 技术支持与实施；营销 → 市场与销售 / 市场营销与品牌；销售 → 市场与销售 / 销售与商务拓展；客服 → 市场与销售 / 客服与客户成功；其他 → 其他。（没写细分的，细分为空）
  - `secondary_work_types`：每项按上表转成 `{category, subtype}`，去重、去掉与主要类型相同的项。
  - `overtime` → `work_intensity`：明确双休或不加班 → 双休；未提及 → 未提及；有 → **清空**（分不清高强度 / 单休 / 大小周）。
  - `overall`：fit → apply；unfit → skip；unsure → **清空**（分不清可以一试 / 需要确认）。
- `judgements` 的旧 `facts` 与 `verdict` 原样保留（它们会因提示词版本低于 v4 而标"可能过时"）。
- 迁移后在终端打印每条被清空字段的标注（岗位名 + 字段），提示用户重新标注。

### 结构版本 3 → 4（2026-09-25，自动更新过时判断）

- 与此前迁移相同：先 `wal_checkpoint(TRUNCATE)` 并备份为 `jet.db.bak-<UTC 时间戳>-v3`，再在事务中 `ALTER TABLE judgements ADD COLUMN origin TEXT`，校验行数与外键后写 `user_version=4`；失败回滚、备份保留（用户要求：加列也要备份）。
