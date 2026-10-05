# Research & Codebase Analysis: 列表页岗位大模型预判

**Feature**: `008-list-prejudge`
**Date**: 2026-09-30
**Branch**: `008-list-prejudge`

---

## 1. 现状调研与代码事实（FACT / INFERENCE / HYPOTHESIS）

### S1: 数据库现有结构与 v8 迁移设计（D1, D2）

- **FACT [代码事实] (`src/jet/db/migrations.py:24-42`)**：
  当前系统数据库版本为 7（`LATEST_VERSION = 7`），最近一次迁移为 `migrate_to_v7`（引入 `strict_industry_selection` 规则勾选表）。
- **FACT [代码事实] (`src/jet/db/schema.sql:129-155`)**：
  - `llm_calls.purpose` 的 CHECK 约束当前定义为：
    `CHECK(purpose IN ('judge', 'eval', 'assist'))`；
  - `user_settings` 当前定义为：
    ```sql
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT PRIMARY KEY,
        daily_llm_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_llm_limit BETWEEN 0 AND 500),
        daily_assist_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    ```
- **FACT [代码事实] (`src/jet/db/migrations.py:116-160`)**：
  SQLite 不支持通过 `ALTER TABLE` 直接修改已有的 `CHECK` 约束。在 `migrate_to_v6` 与 `migrate_to_v7` 中，修改表约束的标准做法是：
  1. 创建新临时表 `llm_calls_new`，定义新的 `CHECK(purpose IN ('judge', 'eval', 'assist', 'prejudge'))`；
  2. 执行 `INSERT INTO llm_calls_new SELECT ... FROM llm_calls` 拷贝全量历史数据；
  3. 删除旧表 `DROP TABLE llm_calls` 并将新表重命名为 `llm_calls`；
  4. 重建可能受影响的索引或触发器。
- **INFERENCE [关键推论]**：
  - 迁移至 v8（`migrate_to_v8`）应遵循既有安全实践：
    1. 在 `user_settings` 执行 `ALTER TABLE user_settings ADD COLUMN daily_prejudge_limit INTEGER NOT NULL DEFAULT 20 CHECK(daily_prejudge_limit BETWEEN 0 AND 200)`；
    2. 重建 `llm_calls` 表增加 `'prejudge'` 枚举值；
    3. 新建 `prejudgements` 表：
       ```sql
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
       );
       ```
    4. 整个迁移包裹在 `BEGIN IMMEDIATE` 事务中，并在迁移前后检查外键完整性（`PRAGMA foreign_key_check`）和各表行数一致性。

---

### S2: 独立每日配额与扣减机制（D2）

- **FACT [代码事实] (`src/jet/llm/quota.py:8-90`)**：
  `usage_today`、`remaining_today` 与 `reserve` 函数目前支持 `purpose="judge"` 与 `purpose="assist"`。
  - `usage_today` 读取 `user_settings` 中对应额度列，并通过 `SELECT COUNT(*) FROM llm_calls WHERE ... AND purpose = ?` 统计今天已计费次数（`billed = 1`）。
  - `reserve` 在事务中进行预占检查。
- **INFERENCE [关键推论]**：
  - 扩展 `purpose == "prejudge"`：
    1. 读取 `user_settings.daily_prejudge_limit`（默认 20，0 表示关闭）；
    2. 若 `limit <= 0`，`reserve` 立即返回 `None`；
    3. 统计当日 `purpose = 'prejudge' AND billed = 1` 的记录数，达到 `limit` 则返回 `None`；
    4. 成功预占后在 `llm_calls` 插入一条初始记录（`purpose = 'prejudge'`），返回 `call_id`。
  - 预判配额按“页”（单次请求）消耗 1 个计数，与正式判断（逐个岗位）和沟通生成彻底隔离。

---

### S3: 预判候选集过滤与整页打包流程（D3, D4）

- **FACT [代码事实] (`src/jet/api/routes.py:507-545`)**：
  在列表页场景下，前端首先调用 `POST /v1/observations`。后端将岗位写入 `jobs` 表，并设置 `completeness = 'list_only'`。
- **FACT [代码事实] (`src/jet/domain/judgements.py:34-80`)**：
  正式判断记录存储在 `judgements` 表中，状态为 `status IN ('queued', 'running', 'done', 'failed', 'quota_exhausted', 'interrupted')`。
- **INFERENCE [关键推论]**：
  新接口 `POST /v1/prejudge` 在 `observations` 成功之后触发。单次处理逻辑如下：
  1. **入库校验**：只处理已经在 `jobs` 表存在的岗位；
  2. **正式判断排除**：检查当前用户在 `judgements` 表中是否已有对该岗位 `status = 'done'` 且 `verdict IS NOT NULL` 的记录。若存在，说明已有正式结论，**绝不进行预判**，从候选集剔除；
  3. **画像缓存匹配**：检查 `prejudgements` 表中是否已存在 `user_id = ? AND job_id = ? AND profile_id = ?`（使用当前用户最新生效的画像 ID）。若存在，直接将缓存的 `level` 与 `reason` 放入返回字典，从大模型候选集剔除；
  4. **候选集判断**：
     - 若剩余候选集为空：大模型调用次数为 0，直接返回已命中缓存的预判；
     - 若候选集非空：
       - 检查是否配置 API Key（`settings.llm_api_key`）与画像，若无则返回 `{"status": "no_llm_key" | "no_profile"}`，不调用；
       - 调用 `reserve(purpose='prejudge')`，若额度耗尽返回 `{"status": "quota_exhausted"}`，不调用大模型、不排队；
       - 候选岗位最多 40 条，打包成一个提示词请求，调用非思考引擎（`settings.judge_engine`，默认 `deepseek-flash:no-think`）；
       - 解析大模型返回的 JSON（`{"results": [...]}`），校验合规性，将有效条目批量写入 `prejudgements`，并结算 `llm_calls`；
       - 返回汇总预判结果及今日配额使用情况。

---

### S4: 提示词工程与模型外发数据边界（D4）

- **FACT [代码事实] (`src/jet/llm/judge.py:30-100`, `src/jet/domain/rules.py:15-60`)**：
  正式判断提示词包含大量岗位详情字段（职责、要求、团队、HR 信息等）。
- **FACT [宪法原则 VII / 008 需求]**：
  列表预判基于信息不充分的列表页，提示词必须严格受限：
  - 明确系统提示：“这是只根据列表信息的粗略预判，不是正式判断”；
  - 允许外发的岗位字段（2026-10-06 体检第 62 条修订：**不发岗位 ID `platform_job_id`**，每条岗位用本批内的序号 1、2、3… 代替，返回后在本机换回岗位 ID）：`title`、`company_name`、`company_industry`、`salary_raw`、`city`（含区县）、`experience`、`degree`、`job_labels`、`skills`；
  - 允许外发的画像字段：`directions`、`keywords`、`cities`/`preferred_cities`、`excluded_cities`、`min_monthly_k`、`nonpref_min_monthly_k`、`exclude_keywords`、`work_preference`、`background`，以及勾选的从严行业（来自 `strict_industry_selection`）；
  - **严禁外发的数据**：HR 实际情况（`hr_notes`）、聊天记录、简历名称、求职者姓名及任何未公开字段。
- **INFERENCE [关键推论]**：
  在 `src/jet/llm/prejudge.py` 中独立实现提示词构建与响应解析，要求大模型输出确定性 JSON 格式：
  ```json
  {
    "results": [
      {"id": "1", "level": "open", "reason": "薪资与技术栈匹配，值得深入了解"},
      {"id": "2", "level": "skip", "reason": "工作地点不符且行业限制"}
    ]
  }
  ```
  （2026-10-06 修订：实际格式是带 `results` 的对象，`id` 是本批序号，不是岗位 ID。）
  校验函数必须容错：丢弃未在当前请求批次中的 ID、非法 level（非 open/neutral/skip），将 reason 截断至最多 40 字。

---

### S5: 浏览器插件列表抓取与调用编排（D3, D5）

- **FACT [代码事实] (`extension/src/page-reader.js:260-315`)**：
  在 `page-reader.js` 的 `readBossPage()` 中，列表项映射当前仅抓取了：
  `platform_job_id`, `title`, `company_name`, `company_industry`, `salary_raw`, `city`, `district`, `job_labels`, `skills`。
  未读取经验（`item.jobExperience`）和学历（`item.jobDegree`）。
- **FACT [代码事实] (`extension/src/background.js:380-436`)**：
  在 `handleSendList` 中：
  - 先用 `newListItems(jobs, tabState.listSent)` 过滤出新出现的岗位 `toSend` 与去重键 `keys`；
  - 调用 `POST /v1/observations` 成功后，将 `keys` 放入 `tabState.listSent`，并将返回的 `jobs` 存入 `tabState.listJudgements`。
- **INFERENCE [关键推论]**：
  - 在 `page-reader.js` 列表映射中增加 `experience: item.jobExperience || null` 和 `degree: item.jobDegree || null`；
  - 在 `handleSendList` 中，当 `/v1/observations` 成功后：
    1. 收集 `toSend` 中尚未在 `tabState.listJudgements` 中拥有完成状态（`status === 'done'`）的岗位；
    2. 构造 `POST /v1/prejudge` 请求体发给后端；
    3. 返回的预判结果合并保存至 `tabState.listPrejudge`（Map）；
    4. 重新调用 `sendRenderState` 刷新页面标记；
    5. 若返回 `quota_exhausted`，当日不再对新批次发起预判；
    6. 当用户保存画像时（`handleSaveProfile`），清空 `tabState.listPrejudge` 与 `tabState.listSent`，确保下次浏览列表时基于新画像重新预判。

---

### S6: 列表标记优先级与视觉呈现（D5, D6）

- **FACT [代码事实] (`extension/src/page-summary.js:103-180`)**：
  当前 `buildListMarks(listJudgements)` 仅支持两种状态：
  1. `judgement && judgement.status === "done"` → 正式判断标记；
  2. `screen_hints.length > 0` → 粗筛提示标记（type: "hint"）；
  3. `hasStatus` → 仅投递状态标记。
- **FACT [代码事实] (`extension/src/content.js:3945-3985`, `extension/src/marks-layout.js:300-330`)**：
  页面卡片渲染：
  - 左侧色条：`stripEl.style.background = toneColor`；若 `item.stale` 则采用虚线。
  - 标签文字：`labelEl.className = "jet-mark-label " + toneCls`，实心背景。
- **INFERENCE [关键推论]**：
  - `buildListMarks` 应接收第二个参数 `listPrejudge`（或从 Map 合并），优先级明确为：
    `正式判断(done 且有结论) > 预判(prejudge) > 粗筛提示(screen_hints) > 仅投递状态`；
  - 预判标记定义：
    - `type: "prejudge"`, `is_prejudge: true`；
    - 标签文字：
      - `level === 'open'` → `"预判·值得点开"`；
      - `level === 'neutral'` → `"预判·一般"`；
      - `level === 'skip'` → `"预判·可跳过"`；
    - `verdict_tone`：对应绿色（open）、灰色/石板色（neutral）、橙色/不推荐（skip）；
    - `title`（鼠标悬停 tooltip）：显示预判理由；
  - 视觉样式区分：
    - 色条：`border-left: 4px dashed <toneColor>; background: transparent; opacity: 0.7;`；
    - 标签：空心描边小字样式，带有"预判"前缀；
    - 正式判断样式保持原样不变；两者视觉差异一眼可识。

---

### S7: 选项页设置管理（D2）

- **FACT [代码事实] (`extension/src/options.html:245-340`, `extension/src/options.js:120-220`)**：
  现有选项页包含求职画像、我的简历、从严行业、经历素材、数据发送同意、配对设置等卡片。
- **INFERENCE [关键推论]**：
  在 `options.html` 中新增"列表预判"卡片：
  - 显示每日上限输入框（0–200，默认 20，0 表示关闭）；
  - 显示今日已用页数与今日剩余页数；
  - 提供保存按钮，调用 `PUT /v1/prejudge/settings`；
  - 保存后即时回显并更新状态。

---

## 2. 关键决定及理由（D1–D7）

### D1: 预判数据采用独立表 `prejudgements`，绝不写入 `judgements` 表

- **决定**：预判数据写入独立表 `prejudgements`，绝不写入 `judgements` 表，两者模型无任何外键或继承关联。
- **理由**：
  - 宪法原则 II 与 VII 明确要求：列表信息不完整，预判绝不能被视作正式结论；
  - 若混入 `judgements` 表，会导致统计口径混乱、历史版本错位，并可能破坏详情页“已判断不重复调用”的状态机判定；
  - 独立成表后，预判可以独立设置淘汰周期、画像关联以及独立清理，完全解耦。

### D2: 预判配额独立按"页"管理，与正式判断额度完全隔离

- **决定**：预判配额单位为"页"（即单次请求），独立字段 `daily_prejudge_limit`（默认 20，0–200 可调），与正式判断每日限额（默认 150）及沟通辅助限额（默认 50）完全隔离。
- **理由**：
  - 符合宪法原则 VII：“每一种花钱的调用都有独立的数量上限和预算配置，不与其他数量共用”；
  - 列表浏览频次高、岗位数量大，如果与正式判断共用额度，用户浏览几页列表就会将正式判断额度耗尽；
  - 按“页”打包扣费，使单次调用的成本高度确定且可预测。

### D3: 整页列表一次性打包请求，使用轻量非思考引擎

- **决定**：每页（或每次加载更多）的所有未判断岗位打包在单次大模型请求中处理，强制使用非思考模型（`judge_engine`，默认 `deepseek-flash:no-think`）。
- **理由**：
  - 列表预判定位为“粗略筛选、轻量导向”，无需深入多轮思考；
  - 打包整页可将 Token 共享开销最大化（系统提示与画像只需发送一次），大幅节省调用开销与网络时延；
  - 非思考模型响应迅速（通常 1~2 秒），避免用户在浏览列表时产生明显卡顿感。

### D4: 提示词严格边界，仅发送声明的列表字段与画像字段

- **决定**：预判提示词严格限制在外发字段白名单内，严禁携带 HR 实际情况（`hr_notes`）、聊天记录、简历文件等额外数据。
- **理由**：
  - 尊重用户授权边界与隐私保护要求（原则 VIII）；
  - 列表预判只依据页面当前呈现给用户的客观事实和用户求职意向做初步对齐，不应产生虚假的“私密信息洞察”。

### D5: 正式判断始终拥有最高优先级，点开详情后照常正式判断

- **决定**：
  1. 列表中已有正式判断（status='done' 且有结论）的岗位，直接排除在预判候选集之外，卡片只展示正式判断；
  2. 带有预判标记的岗位被用户点击打开详情后，照常触发后台正式判断；
  3. 正式判断完成后，列表与卡片立即更新为正式判断，预判标记退场。
- **理由**：
  - 正式判断拥有完整的岗位职责、要求与规则校验，结论权威；
  - 列表预判仅作为“点开前的决策辅助”，一旦有了正式结论，预判便完成了使命，必须让位于正式判断。

### D6: 视觉上明确区隔预判与正式判断，强调"仅供参考"

- **决定**：预判标记在视觉呈现上与正式判断必须有一眼可辨的区别：色条使用虚线/半透明，标签使用空心描边小字并带有“预判”前缀，悬停展示理由；正式判断保持实线与实心样式。
- **理由**：
  - 视觉一致性会导致求职者误以为列表预判即为完整评估结果，从而可能错失岗位或盲目投递；
  - 空心与虚线样式在设计心理学上天然具备“推测/草稿/临时”的语义，与“正式判断”的实心稳固感形成鲜明对比。

### D7: 额度用完静默降级，不弹窗、不重试、不排队

- **决定**：当每日预判额度耗尽（返回 `quota_exhausted`）或未配置 Key 时，服务端与插件保持静默，当天不再发起新的大模型请求，不将请求排队到以后，不弹出错误提醒，卡片正常退回到粗筛提示或无标记状态。
- **理由**：
  - 遵循宪法原则 VI 与 VII：“不批量重判，不在额度不足时排队到以后执行”；
  - 预判是增值辅助能力，额度耗尽不应打断求职者正常浏览网页的核心体验。
