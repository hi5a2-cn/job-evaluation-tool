# Research: 岗位信息跨页面联通（第一批）

**Feature**: `005-cross-page-job-link` | **Date**: 2026-09-29

代码现状由 Antigravity 只读调研，对照既有代码与既有 spec（002、003、004）逐项核实。

---

## 一、代码现状与事实（S1–S14）

| 编号 | 事实 | 出处（文件:行号） |
|---|---|---|
| S1 | `jobs.current_version_id` 为可空列（`INTEGER REFERENCES job_versions(id)`），`completeness` 仅约束为 `'list_only'` 或 `'full'` | `src/jet/db/schema.sql:13-21` |
| S2 | `job_versions` 表对 `city`、`salary_visible`、`content_hash` 均声明了 `NOT NULL`，聊天页无法提供这些字段，强行写入会破坏约束 | `src/jet/db/schema.sql:23-42` |
| S3 | `_ensure_current_schema` 每次启动都会执行 `conn.executescript(schema_sql)`，并在迁移后运行；`schema.sql` 中用 `CREATE TABLE IF NOT EXISTS` 新增表可幂等自动生效，无需升 user_version | `src/jet/db/store.py:63-65, 176` |
| S4 | 详情入库与版本比对逻辑假定 `existing_job["current_version_id"]` 始终非空，直接 `SELECT FROM job_versions WHERE id = ?` 并解构 `current_version["version_no"]` / `["title"]` | `src/jet/domain/jobs.py:148-151, 202-205` |
| S5 | `_job_entry` 函数在构造岗位条目时，直接根据 `current_version_id` 查 `job_versions` 取 `title`，当 `current_version_id` 为 NULL 时 `title` 为 `None` | `src/jet/api/routes.py:137-146` |
| S6 | 岗位库列表 `list_my_jobs` 中，`all_jobs` 口径当前为 `job_status ∪ judgements ∪ hr_notes`，标题取自 `job_versions`（为 NULL 时变空串 `""`），搜索 `q` 也仅查 `job_versions.title` | `src/jet/domain/job_status.py:137-149, 245-257, 283-287, 353-361` |
| S7 | 判断流程 `request_judgement` 与 `rejudge` 对 `completeness == 'list_only'` 的岗位直接返回空或抛 409 异常拦截，绝不调用规则粗筛或大模型 | `src/jet/domain/judgements.py:99-100, 280-282` |
| S8 | 聊天页切换检测核心在 `content.js`（MutationObserver 监听顶部变化触发 `chat_top_changed`），由 `background.js` 执行 `readBossChatJobId` 获取 `encrypt_job_id`，独立于侧边栏运行 | `extension/src/content.js:3857-3882`、`extension/src/background.js:1221-1348` |
| S9 | `readBossChatJobId` 当前仅返回 `encrypt_job_id`，未提取职位名 `title` 与公司名 `company_name`；完整读取需查 Vue 根组件或顶部 DOM | `extension/src/page-reader.js:708-800` |
| S10 | 侧边栏当前岗位卡片 `renderChatJob` 仅支持展示 Jet 结论和 HR 实际情况编辑，尚无状态按钮组（收藏/已投递/不考虑） | `extension/src/sidepanel.js:237-450` |
| S11 | 列表卡片标记布局 `planMarks` 仅支持渲染左侧色条与左上角文本标签，尚未支持投递状态标识 | `extension/src/marks-layout.js:106-268`、`extension/src/content.js:350-381` |
| S12 | 列表卡片数据 `listJudgements` 已经包含 `my_status`，但 `buildListMarks` 未将 `my_status` 提取为可渲染标识 | `extension/src/page-summary.js:60-109` |
| S13 | 自动标记「已投递」`decideAutoApply` 仅放行 `/web/geek/job` 路径，且 DOM 识别严格绑定 `a.op-btn.op-btn-chat`；独立职位页 `/job_detail/` 路径被拦截 | `extension/src/auto-apply.js:43-46`、`extension/src/content.js:4095-4096` |
| S14 | 大模型提示词与请求字段构建全部集中于 `src/jet/llm/` 目录，本功能完全不触及提示词生成与 DeepSeek 发送管道 | `src/jet/llm/prompt_v5.py`、`src/jet/llm/assist.py` |

---

## 二、全仓库 `current_version_id` 与 `job_versions` 查询盘点（D2 专项）

对全仓库涉及 `jobs.current_version_id`、`SELECT FROM job_versions` 或 `JOIN job_versions` 的位置逐一排查，分析当 `current_version_id` 为 NULL（即聊天入库但尚未打开详情的岗位）时的安全性及必须的改造方案：

| 序号 | 文件:行号 | 代码语句/上下文 | `current_version_id` 为 NULL 时安全性 | 需要怎么改（适配方案） |
|---|---|---|---|---|
| 1 | `src/jet/api/routes.py:137-146` | `_job_entry`: `SELECT title FROM job_versions WHERE id = ?` | ⚠️ 不安全（无异常但 `title` 会变为 `None`） | 若 `cur_version_id` 为 NULL，改为从 `job_chat_seen` 查 `chat_title` 补齐；并查 `job_chat_seen` 返回 `seen_in_chat` 布尔字段。 |
| 2 | `src/jet/api/routes.py:373-381` | `observations` 列表分支: `SELECT * FROM job_versions WHERE id = ?` 计算粗筛 | ✅ 安全。`v_row` 为 `None`，`screen_hints` 内部已对 `job_version=None` 做了安全字典兜底，不崩溃。 | 无需修改代码。 |
| 3 | `src/jet/domain/jobs.py:148-151` | `ingest` 列表分支: `SELECT * FROM job_versions WHERE id = existing_job["current_version_id"]` | ❌ **严重不安全**：查询返回 `None`，随后第 153 行访问 `current_version["title"]` 直接抛 `TypeError` | 当 `existing_job["current_version_id"]` 为 NULL 时，说明该岗位此前仅在聊天中入库；进入列表分支时，应视为首次创建版本：插入 `job_versions`（`version_no=1`, `source='list'`），并更新 `jobs.current_version_id`。 |
| 4 | `src/jet/domain/jobs.py:202-205` | `ingest` 详情分支: `if existing_job["completeness"] == "list_only": new_version_no = current_version["version_no"] + 1` | ❌ **严重不安全**：`current_version` 为 `None`，解构版本号直接崩溃 | 当 `existing_job["current_version_id"]` 为 NULL 时，详情入库应作为第 1 版：插入 `job_versions`（`version_no=1`, `source='detail'`），并更新 `jobs.completeness = 'full'` 与 `current_version_id`。 |
| 5 | `src/jet/domain/job_status.py:283-287` | `list_my_jobs` 检索 `q`: `EXISTS (SELECT 1 FROM job_versions jv WHERE ... jv.title LIKE ?)` | ⚠️ 逻辑缺失：不崩溃，但聊天入库岗位的标题无法被搜索到 | 在 `WHERE` 条件中增加 `OR EXISTS (SELECT 1 FROM job_chat_seen jcs WHERE jcs.job_id = j.id AND jcs.user_id = ? AND jcs.chat_title LIKE ? ESCAPE '/')`。 |
| 6 | `src/jet/domain/job_status.py:353-361` | `list_my_jobs` 条目构造: `SELECT title, salary_raw, city FROM job_versions WHERE id = ?` | ⚠️ 逻辑缺失：不崩溃，但 `title` 会变空串 `""` | 若 `cur_version_id` 为 NULL，改为从 `job_chat_seen` 查 `chat_title`；`salary_raw` 保持 `None`，`city` 保持 `""`。 |
| 7 | `src/jet/domain/job_status.py:375` | `list_my_jobs` 过期判断: `job_changed = bool(latest["job_version_id"] != cur_version_id)` | ✅ 安全。该分支仅在 `latest is not None` 时执行；聊天入库岗位尚无判断，`latest` 为 `None`，跳过该分支。 | 无需修改。 |
| 8 | `src/jet/domain/judgements.py:60-64` | `staleness`: `SELECT current_version_id FROM jobs ... job_changed = ...` | ✅ 安全。仅在已有判断时调用，未判断岗位不执行。 | 无需修改。 |
| 9 | `src/jet/domain/judgements.py:99-100` | `request_judgement`: `if job_row["completeness"] == "list_only": return None, False, None` | ✅ 安全。聊天岗位 `completeness` 设为 `'list_only'`，在入口处即短路返回，绝不读取 `current_version_id`。 | 无需修改。 |
| 10 | `src/jet/domain/judgements.py:280-282` | `rejudge`: `if job["completeness"] == "list_only": raise HTTPException(409)` | ✅ 安全。`completeness == 'list_only'` 直接拦截抛错，不会执行后续版本读取。 | 无需修改。 |
| 11 | `src/jet/domain/judgements.py:427-429` | `to_api`: `SELECT salary_visible FROM job_versions WHERE id = judgement_row["job_version_id"]` | ✅ 安全。直接使用判断行已记录的 `job_version_id`。 | 无需修改。 |
| 12 | `src/jet/domain/labels.py:140-141` | `save_label`: `if job["completeness"] == "list_only": raise HTTPException(409)` | ✅ 安全。拦截仅列表岗位，不读取 `current_version_id`。 | 无需修改。 |
| 13 | `src/jet/worker.py:230-234` | `JudgementWorker._process_judgement`: `SELECT * FROM job_versions WHERE id = j_row["job_version_id"]` | ✅ 安全。工作队列中的任务均已有有效版本 ID。 | 无需修改。 |
| 14 | `src/jet/eval/runner.py:873` | `export_jobs`: `JOIN job_versions jv ON j.current_version_id = jv.id WHERE j.completeness = 'full'` | ✅ 安全。明确带有 `completeness = 'full'` 过滤条件，聊天入库的 `list_only` 岗位天然被排查在外。 | 无需修改。 |
| 15 | `src/jet/api/routes.py:769-781, 842-854` | 聊天预览与生成 `post_chat_preview` / `post_chat_generate`: `SELECT id FROM jobs WHERE ...` | ✅ 安全。仅根据平台 ID 查 `jobs.id`，再查 `latest_judgement`。未判断时 `latest_judgement` 为 `None`，`has_jet_judgement` 正确返回 `False`，不碰 `current_version_id`。 | 无需修改。 |

---

## 三、架构与设计决定（对齐 D1–D9）

### R1 数据存储设计（D1、D2）
- **表结构变更**：
  在 `src/jet/db/schema.sql` 增加按用户表：
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
- **迁移机制**：由于 SQLite 支持 `CREATE TABLE IF NOT EXISTS`，且 `store.py` 的 `_ensure_current_schema` 每次启动均会自动调用 `conn.executescript(schema_sql)`，此改动无破坏性，数据库结构保持 `user_version 6`，无需新增升版本迁移脚本。
- **写入行为**：
  - 岗位首次在聊天遇到：插入 `jobs`（`platform='boss'`, `platform_job_id`, `completeness='list_only'`, `current_version_id=NULL`, `company_name`, `first_seen_at=now`, `last_seen_at=now`）。绝对不写 `job_versions`。
  - 写入 `job_chat_seen`：`INSERT INTO job_chat_seen ... ON CONFLICT(user_id, job_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, chat_title = CASE WHEN length(trim(job_chat_seen.chat_title)) = 0 THEN excluded.chat_title ELSE job_chat_seen.chat_title END`。
  - 岗位已存在时：绝不修改已有 `jobs` 列（`company_name` 仅在原值为 NULL 时补齐），绝不修改已有判断和投递状态。
- **标题提取规则**：岗位条目的 `title = 当前版本 title`，若 `current_version_id` 为 NULL 则取 `job_chat_seen.chat_title`。

### R2 岗位库全集口径与服务端条目（D3）
- **口径定义**：
  `all_jobs` = `有状态 ∪ 判断过 ∪ 有非空 HR 记录 ∪ job_chat_seen 中有该用户记录`。
  在 `src/jet/domain/job_status.py` 的 `count_all_jobs` CTE 和 `filter == "all_jobs"` 条件中，增加 `UNION SELECT job_id FROM job_chat_seen WHERE user_id = ?`。
- **条目新增布尔字段**：
  服务端 `_job_entry` 新增字段 `"seen_in_chat": bool`（当该用户的 `job_chat_seen` 包含该岗位时为 `True`，否则为 `False`）。
- **插件端判定同步**：
  `extension/src/chat-view.js` 的 `isJobInLibrary(jobEntry)` 同步扩展：
  ```javascript
  export function isJobInLibrary(jobEntry) {
    if (!jobEntry || typeof jobEntry !== "object") return false;
    const hasStatus = Boolean(jobEntry.my_status && jobEntry.my_status.status);
    const hasJudgement = Boolean(jobEntry.judgement);
    const hasHrNote = typeof jobEntry.hr_note === "string" && jobEntry.hr_note.trim().length > 0;
    const seenInChat = Boolean(jobEntry.seen_in_chat);
    return hasStatus || hasJudgement || hasHrNote || seenInChat;
  }
  ```
- **岗位库与侧边栏未判断展示**：
  未判断岗位在岗位库列表展示为"尚未判断"徽章，薪资、城市等展示为空；侧边栏卡片展示职位名、公司名、投递状态按钮组、HR 实际情况输入区，并显示提示原文："点「查看职位」获取详情并判断"（FR-015）。

### R3 本机接口设计（D4）
- **端点**：`POST /v1/chat/job`
- **请求体**：`{ "platform_job_id": str, "title": str, "company_name": str | null }`
- **校验规则**：`platform_job_id` 或 `title` 为空（或仅含空白）直接返回 422 `invalid_payload`。
- **鉴权**：复用现有已配对鉴权（`Depends(require_paired)`）。
- **幂等性与执行边界**：
  - 仅执行 R1 的入库与记录逻辑；
  - **绝不**调用 `request_judgement`、`rejudge`、`screen_hints`、任何规则粗筛或大模型；
  - **绝不**写入 `job_status` 或 `job_status_events`；
  - 返回与 `GET /v1/judgements` 结构相同的岗位条目：`{ "jobs": { [platform_job_id]: entry } }`。

### R4 插件聊天页检测与调用时机（D5）
- **检测机制位置**：
  现有聊天切换检测由两层配合：
  1. `extension/src/content.js` 的 MutationObserver 监听聊天顶部容器文字变动（`getChatTopText()`），防抖后向后台发送 `chat_top_changed`；
  2. `extension/src/background.js` 监听到 `chat_top_changed` 后在页面 MAIN world 执行轻量脚本读取会话数据。
- **侧边栏关闭时也要入库**：
  `background.js` 是常驻 Service Worker，不受侧边栏开闭影响。调用 `POST /v1/chat/job` 必须放在 `background.js` 处理聊天切换广播处（即 `decideChatSwitchRetry` 决定广播新岗位时），**绝不能**放在仅在 `sidepanel.js` 打开时才运行的渲染代码中。
- **读取函数补全**：
  当前 `readBossChatJobId` 只读取 `encrypt_job_id`，未提取职位名与公司名。需要让该轻量读取函数或伴随读取一并提取顶部职位名 `job_title` 与公司名 `company_name`（从 Vue `message-list.boss` 或 DOM `.chat-position-content` 取）。当且仅当读到非空 `encrypt_job_id` 与非空 `title` 时，`background.js` 异步调用 `POST /v1/chat/job`。
- **容错边界**：
  若 Jet 未配对或服务未运行，捕获异常后静默忽略，不阻塞聊天浏览，不发起无限重试。

### R5 侧边栏当前岗位状态按钮组（D6）
- **UI 布局**：
  在侧边栏 `renderChatJob` 当前岗位卡片顶部（结论/提示与公司名之间）增加投递状态按钮组：收藏（`saved`）、已投递（`applied`）、不考虑（`skipped`）。
- **交互逻辑**：
  - 点击非选中按钮：设置为该状态；
  - 再次点击已选中按钮：取消状态（设为 `null`）；
  - 保存期间按钮禁用，防重复提交；
  - 保存失败恢复点击前选中态，并在按钮旁提示"保存失败"（2 秒后消失）；
  - 防乱序：请求发出与响应处理使用 003 既有的 `isChatJobResponseCurrent` 检验当前激活岗位 ID，避免旧岗位的修改回调写回新卡片。
- **在库未判断提示**：
  当岗位在库但无判断结果时，显示提示原文："点「查看职位」获取详情并判断"。

### R6 搜索列表卡片状态标识与即时重绘（D7）
- **数据源与提取**：
  `extension/src/page-summary.js` 的 `buildListMarks(listJudgements)`：
  遍历 `listJudgements` 时，提取已有的 `entry.my_status`。若存在有效状态（`saved` / `applied` / `skipped`），在返回的 mark 对象上增加 `status_key`、`status_label`（"收藏"/"已投递"/"不考虑"）及 `status_tone`。
- **布局共存不遮挡**：
  查阅 `extension/src/marks-layout.js` 与 `content.js`：
  现有卡片标记包含"左侧色条（strip）"与"左上角空白处标签（label）"。
  为满足 FR-006"与卡片上已有的 Jet 结论标识或粗筛提示同时可见、互不遮挡"：
  - 在卡片顶部留白行（`space >= 12` 区域）水平并排排列：
    - 结论标签或粗筛提示保持 004 原位置与宽度不动（Claude 审核改定：不移动已验收的标识）；
    - 状态胶囊排在结论/提示标签右侧（间距 4px）；没有结论/提示标签时放在 `rect.left + 8`；空间不足时状态胶囊截断，不覆盖已有标签；
    - 左侧色条规则不变；仅有状态的卡片不新增色条。
- **页面内联动重绘**：
  在 `extension/src/content.js` 中所有修改 `currentDetail.my_status` 的三处位置：
  1. 浮层卡片手动改状态成功回调（约 line 2280）；
  2. 点击「立即沟通」自动标记已投递成功回调（约 line 4150）；
  3. 点击撤销自动标记成功回调（约 line 4030）。
  在这三处成功保存后，立即更新当前内存中的 `currentListMarks[targetJobId]`，并调用 `updateMarksPositions()` 立即执行局部重绘，确保列表卡片 1 秒内无感更新，无需等待页面刷新。

### R7 独立职位页「立即沟通」自动标记（D8）
- **路径放开**：
  修改 `extension/src/auto-apply.js` 中的 `decideAutoApply`：
  放开 `pathname.startsWith("/job_detail/")`。
- **岗位 ID 严格双重核对**：
  - 必须由 004 独立职位页被动读取成功，获得 `currentJobId`；
  - 提取当前 URL 中的岗位 ID（正则匹配 `/\/job_detail\/([^/?#.]+)\.html/`）；
  - 两者必须严格全等才允许标记；若 004 读取失败或 ID 不一致，直接 `skip`（reason: `"job_id_mismatch"` 或 `"no_current_job_id"`）。
- **按钮匹配规则**：
  - 在点击事件捕获阶段，通过 `target.closest("a, button")` 查找最近的按钮元素；
  - 检查按钮文字：`textContent.trim()` 必须严格等于 `"立即沟通"`；若是"继续沟通"等其他文案直接跳过；
  - 检查按钮 `ka` 属性：若 `ka` 存在且不包含当前岗位 ID，直接跳过；
  - 当前状态已是 `applied` 时直接跳过。
- **页面跳转与卸载防护（FR-058 机制核查）**：
  核实 `content.js:4138-4145`：当触发标记时，内容脚本通过 `chrome.runtime.sendMessage({ type: "set_job_status", ... })` 将请求委托给后台 Service Worker 发出。因为网络请求由独立的后台进程处理，即使 BOSS 在点击后发生页面跳转、刷新或卸载，请求也绝不会被浏览器中断。现有 FR-058 的后台委托实现完全满足本条要求。
- **真实页面选择器探测现状**：
  **NOT VERIFIED**：独立职位页上的「立即沟通」按钮具体 class 类名在 0.2/0.4 探测阶段未单独抓取。当前通用选择器策略使用 `target.closest("a, button")` 配合 `textContent.trim() === "立即沟通"` 进行自适应匹配，并在实现阶段利用真实页面核验。

### R8 外部模型字段零变动（D9）
- **字段与提示词核验**：
  本功能新增的全部逻辑仅限于本机 SQLite 记录（`job_chat_seen`）及本地 UI 渲染（状态按钮组、列表状态标记）。
  检查以下提示词与 LLM 管道文件，确认均未被触及、未增加任何字段、未修改任何提示词：
  - `src/jet/llm/prompt_v5.py`
  - `src/jet/llm/prompt_v4.py`
  - `src/jet/llm/assist.py`
  - `src/jet/llm/sanitize.py`
  - `src/jet/llm/prompts/*`
- 完全符合宪法与 CLAUDE.md 中关于"发往外部模型内容先确认后生效"的纪律。

---

## 四、冲突分析与验证结论

经对用户指定的 D1–D9 决策与现有代码进行逐行对碰，未发现不可调和的底层冲突，但发现 2 处潜在实现风险点已在方案中化解：
1. **风险点 1（D2 关联）**：现有 `src/jet/domain/jobs.py` 中存在两处直接访问 `current_version["version_no"]` 的代码（行 148–151 与 202–205），原代码假定所有已有岗位必有版本。若直接插入 `current_version_id=NULL` 的岗位，后续用户在列表或详情页遇到该岗位时会导致后端崩溃。
   - **对策**：在 `jobs.py` 的合并逻辑中增加分支：当发现 `existing_job["current_version_id"] is None` 时，视为补录首个版本，执行 `version_no = 1` 插入并回填 `current_version_id`。
2. **风险点 2（D5 关联）**：插件端现有轻量读取 `readBossChatJobId` 只取了 `encrypt_job_id`，若直接在后台调 `POST /v1/chat/job` 会缺少 `title`。
   - **对策**：扩展该 MAIN world 读取函数的提取范围，一并读取顶部 `.chat-position-content` 或 Vue 实例中的 `job_title` 与 `company_name`。
