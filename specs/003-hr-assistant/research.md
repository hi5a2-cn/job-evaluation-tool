# Phase 0 调研报告：HR 沟通助手 + 我的岗位库改版

**Branch**: `003-hr-assistant` | **Date**: 2026-09-26 | **Spec**: [spec.md](spec.md)

本文档针对 `specs/003-hr-assistant/spec.md` 与需求确认结论，对现有代码库进行针对性调研，为 `plan.md`、`data-model.md`、`contracts/local-api.md` 和 `quickstart.md` 提供事实依据与技术选型论证。
每个技术决定均按照"决定 / 理由 / 考虑过的其他做法"记录。

---

## R-a 002 插件读取机制与聊天页切换发现

### 1. 现有代码调研事实 (FACT)
- **MAIN world 脚本执行与调度**：
  - 文件与行号：`extension/src/background.js` 第 471–504 行。
  - 现有实现：在 `processPageRead(tabId, message)` 函数中，background service worker 通过 `chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func: readBossPage })` 将读取函数注入到页面的主世界（MAIN world）中执行，从而直接读取挂载在 DOM 元素上的 Vue 2 实例（`__vue__`），获取响应式数据对象并返回给 background。
- **DOM 变动监听与通知**：
  - 文件与行号：`extension/src/content.js` 第 3392–3431 行。
  - 现有实现：在 `startObserver()` 中，content script 在 ISOLATED world 中使用 `MutationObserver` 监听 `#wrap` 或 `document.body` 的子树变动（`{ childList: true, subtree: true }`），过滤掉带有 `[data-jet]` 标记的变动。设置了 150ms 的防抖定时器（`debounceTimer`）与 500ms 的最大等待定时器（`maxWaitTimer`），触发后通过 `chrome.runtime.sendMessage({ type: "page_changed", waited_ms })` 通知 background 调度页面读取。
- **真实聊天页结构与读取可行性**：
  - 文件与行号：`experiments/boss-chat-probe/reader.js` 第 288–396 行（已在真实 BOSS 聊天页验证，v0.0.1–v0.0.6）：
    - 聊天会话组件：当前会话由 `message-list` 组件承载，其 `$data.boss` 包含 `encryptJobId`、`jobName`、`brandName`、`locationName` 以及 HR 姓名 `name`（第 334–350 行）。
    - 活跃消息区区分：页面在用户切换会话时，旧会话的 DOM 元素并未销毁而是被隐藏；通过检查元素的可见性（`getClientRects().length > 0 && visibility !== 'hidden'`），可以精确锁定当前看得见（active）的一组消息区（第 321–344 行）。
    - 完整职位名：页面顶部的 `.chat-position-content` 下包含完整职位名（例如"储能海外销售（驻尼日利亚等）"），优先于会话数据中的 `jobName`（第 288–307 行，V2）。
    - 消息结构与卡片：每条消息对应 `ChatMessage` 组件，其 `$props.message` 包含 `isSelf`（布尔值）、`type`（数字，BOSS 自动招呼语为 3，用户发送为 1）、`bodyType`（数字，1 为文字，7 为附件简历请求卡片，12 为简历已发送，16 为竞争者 PK 卡片）以及 `text`（第 374–396 行）。

### 2. 技术决定
- **决定**：
  1. **切换发现**：平时 content script（ISOLATED world）在 MutationObserver 触发时只比较聊天顶部区域（`.chat-position-content` 所在的会话顶部）的文字是否变化；变化时由 background 在 MAIN world 只读一次当前会话的 `encryptJobId`，与上次比较，不同才通知侧边栏清空。不读消息区、不滚动、不发网络请求（原则 IV）。依据：实验中会话区的链接时有时无（v0.0.2 为 0 个、v0.0.4 为 1 个），`data-` 属性里没有岗位 ID，岗位 ID 只在 Vue 数据里（MAIN world）。**NOT VERIFIED**：顶部文字是否在每次切换时都变化，需真实页面验证；岗位 ID 变化时，background 通知 sidepanel 清空旧结果并提示"已切换到新的聊天，请重新生成"（FR-005）。用户点"复制"时，插件再读一次当前聊天的岗位 ID（MAIN world 只读），与生成结果绑定的岗位 ID 不一致就拒绝复制，并提示"已切换到新的聊天，请重新生成"。切换发现与复制前核对岗位 ID 记为 V8（NOT VERIFIED）。
  2. **深度读取**：聊天会话详细数据（公司、职位名、城市、HR 姓名、最近已加载的最多 30 条非系统消息）**仅在用户于侧边栏点击"生成"按钮时读取一次**（FR-001、FR-004）。由 background 触发 `chrome.scripting.executeScript(world: "MAIN")` 执行专门的 `readBossChatPage` 函数，读取完毕立即发往本机 Jet 服务。
- **理由**：
  - 严格遵循宪法原则 IV（只读、不向平台发请求、不代为滚动）。
  - 平时若频繁在 MAIN world 深入读取 30 条消息及其 Vue 组件属性，会带来不必要的 CPU 开销与页面抖动，甚至可能因访问频繁触发页面异常；将切换发现与生成读取彻底解耦，平时开销近乎为零。
- **考虑过的其他做法**：
  - *做法 B：每次 DOM 变动都执行完整的 MAIN world 聊天消息读取*。缺陷：用户在聊天框打字、HR 发送新消息、表情卡片渲染都会触发 MutationObserver，频繁读取 Vue 组件性能浪费严重，且违反"点击生成时才读取消息"的原则（FR-004）。
  - *做法 C：侧边栏每秒轮询当前页面的岗位 ID*。缺陷：轮询开销大，且标签页失焦或闲置时依然空耗资源。

---

## R-b 侧边栏消息通信与聊天页判断

### 1. 现有代码调研事实 (FACT)
- **侧边栏与后台消息流**：
  - 文件与行号：`extension/src/sidepanel.js` 第 260–332 行、`extension/src/background.js` 第 168–175、590–1029 行。
  - 现有关联：
    - `sidepanel.js` 初始化及定时（每 5 秒）通过 `chrome.runtime.sendMessage({ type: "get_status" })` 和 `{ type: "get_page_summary", tabId }` 向 background 查询当前状态与页面摘要。
    - `background.js` 在页面状态发生变化（如重新入库、重新判断完毕）时，通过 `chrome.runtime.sendMessage({ type: "tab_state_changed", tabId })` 广播给监听方。
    - `sidepanel.js` 监听 `chrome.runtime.onMessage`，匹配当前激活的 `activeTabId` 后调用 `refreshAll()` 局部重绘。
- **当前标签页判定方式**：
  - 文件与行号：`extension/src/background.js` 第 493–517 行、`extension/src/page-reader.js` 第 12–37 行。
  - 现有机制：`page-reader.js` 检查 `location.pathname`。若以 `/job_detail/` 开头判定为 `job_detail_page`，以 `/web/geek/job` 开头判定为 `search_list`，验证码页面为 `captcha_or_blank`，其他为 `other`。

### 2. 技术决定
- **决定**：
  1. 页面类型识别增加 `chat_page`：在 `readBossPage` / `page-reader.js` 中，当 `location.pathname.startsWith("/web/geek/chat")` 时，识别为 `chat_page`。
  2. 聊天状态通道：
     - background 维护当前 tab 的 `chatState`（包含当前会话的 `platform_job_id`、`company_name`、`job_title`、`is_chat`）。
     - 当 sidepanel 调用 `get_page_summary` 时，若当前页不是聊天页（`!is_chat`），返回原有 002 摘要信息，sidepanel 隐藏 HR 沟通助手界面模块；
     - 若当前页是聊天页，sidepanel 显示 HR 沟通助手卡片，展示当前绑定的岗位名与公司名；若读不到当前聊天（没有岗位 ID 或会话），显示"读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试"（spec R2）。
  3. 切换通知：content 发现岗位 ID 变动触发 `chat_switched` 事件，sidepanel 收到后立即置空内存中的生成结果与预览，并展示提示语。
- **理由**：
  - 复用现有的 background 状态中心与消息总线，sidepanel 保持纯展示和单向指令发送，架构清晰。
  - 严格满足 FR-006（非聊天页不显示生成按钮，原有 002 功能不受干扰）。
- **考虑过的其他做法**：
  - *做法 B：sidepanel 直接通过 `chrome.tabs.sendMessage` 与 content.js 双向通信*。缺陷：content.js 运行在 ISOLATED world，无法直接访问 Vue 内部数据，仍需 background 中转；绕过 background 会破坏 background 对 tab 全局状态的一致管理。

---

## R-c 本机服务架构、鉴权、LLM 客户端与额度扩展

### 1. 现有代码调研事实 (FACT)
- **路由与配对鉴权**：
  - 文件与行号：`src/jet/api/routes.py` 第 10、89 行，`src/jet/api/auth.py` 第 89–126 行。
  - 机制：所有业务接口通过 FastAPI 依赖注入 `require_paired(request, conn)` 进行鉴权，校验 `Authorization: Bearer <token>` 是否在 `pairings` 表中存在有效记录，并校验 Origin（若存在）。
- **LLM 客户端封装**：
  - 文件与行号：`src/jet/llm/client.py` 第 56–100 行。
  - 机制：`call_once(settings, messages, transport, thinking=False, max_tokens=...)` 封装了标准的 OpenAI 兼容 completions 调用，支持 DeepSeek 的 `thinking` 控制（通过 `{"type": "disabled"}` 关闭思考模式），内置针对网络异常的 `RetryableHttp`、`Timeout` 与 `NotSent` 处理。
- **额度管理机制**：
  - 文件与行号：`src/jet/llm/quota.py` 第 8–86 行。
  - 机制：`reserve(...)` 在 `BEGIN IMMEDIATE` 事务中进行额度预占。当前支持 `purpose='judge'`（按 `user_settings.daily_llm_limit` 控制）与 `purpose='eval'`（按评测单次上限控制）。
- **配置与环境变量**：
  - 文件与行号：`src/jet/config.py` 第 89–96 行。
  - 机制：`load_settings()` 从系统环境变量或数据目录 `.env` 中读取配置，`JET_DAILY_LLM_LIMIT` 必须为 0–500 的整数，默认 150。
- **数据库结构与 CHECK 约束**：
  - 文件与行号：`src/jet/db/schema.sql` 第 130 行：
    `purpose TEXT NOT NULL DEFAULT 'judge' CHECK(purpose IN ('judge', 'eval')),`
  - 机制：当前 `llm_calls` 表严格限定用途只能为 `judge` 或 `eval`。
- **数据库版本迁移机制**：
  - 文件与行号：`src/jet/db/migrations.py` 第 520–617 行。
  - 机制：当前结构版本为 `PRAGMA user_version = 4`。每次版本升级时：先执行 `PRAGMA wal_checkpoint(TRUNCATE)`，将数据库完整备份为 `jet.db.bak-<UTC 时间戳>-v<旧版本>`；然后在事务中执行 DDL；验证迁移后行数与原有行数一致，且外键检查 `PRAGMA foreign_key_check` 清空，最后写入新版本号。

### 2. 技术决定
- **决定**：
  1. **新增 LLM 用途名**：确定命名为 `assist`（生成话术），与 `judge`、`eval` 并列。
  2. **每日上限配置**：新增配置项 `JET_DAILY_ASSIST_LIMIT`，默认值为 50，取值范围 0–500（与 002 一致）。服务启动时从环境变量或 `.env` 加载，存入 `Settings.daily_assist_limit`。
  3. **配额检查逻辑扩展**：在 `src/jet/llm/quota.py` 中，针对 `purpose='assist'`，统计当日（本机时区 0 点至当前）`purpose='assist'` 且 `billed=1` 的记录数，与 `daily_assist_limit` 比对；超过上限则直接拒绝，不调用大模型（FR-038、FR-040）。
  4. **大模型调用模式**：沿用 `src/jet/llm/client.py` 中的 `call_once`，显式设置 `thinking=False`（关闭思考模式），单次调用，超时沿用配置（20秒）（FR-018）。
  5. **结构迁移版本**：将数据库版本升至 `PRAGMA user_version = 5`。在 `migrations.py` 中增加 `migrate_to_v5`，迁移前先备份 `jet.db.bak-<时间>-v4`，在事务中重建 `llm_calls` 表（扩展 CHECK 为 `IN ('judge', 'eval', 'assist')`），并新建 `experience_items` 与 `llm_consents` 两张新表。
  6. **复姓脱敏处理（2-A）**：服务端内置常见复姓表，姓名开头命中复姓时用"复姓 + 称呼"，否则用首字 + 称呼。
- **理由**：
  - 严格遵循宪法原则 VII（成本有上限，每种花钱的调用都有独立数量上限与配置）及 002 既有的结构迁移与备份规范。
- **考虑过的其他做法**：
  - *做法 B：不改 `llm_calls` 表的 CHECK 约束，借用 `purpose='judge'` 但在其他字段标记*。缺陷：破坏了宪法原则 IX（每个数字都有定义），导致统计调用次数时 SQL 过滤条件混淆脆弱，且容易误占判断额度。

---

## R-d 岗位库扩展与搜索机制

### 1. 现有代码调研事实 (FACT)
- **后端列表与计数**：
  - 文件与行号：`src/jet/domain/job_status.py` 第 102–281 行。
  - 机制：`list_my_jobs(conn, user_id, filter, limit=200)` 当前支持 5 个过滤项（`all`, `saved`, `applied`, `skipped`, `recent`），一次性返回各分类计数与条目数组。每个条目包含 `platform_job_id`、`title`、`company_name`、`salary_raw`、`city`、`verdict`、`verdict_label`、`stale`、`hr_note_recorded`、`my_status` 等。
- **前端渲染与交互**：
  - 文件与行号：`extension/src/myjobs.js` 第 11–17、50–57 行，`extension/src/myjobs-view.js` 第 51–158 行。
  - 机制：
    - `myjobs.js` 维护 `currentFilter`，绑定各个过滤按钮，并调用 `chrome.runtime.sendMessage({ type: "get_my_jobs", filter })` 获取数据。
    - `myjobs-view.js` 中的 `toRow(item, filter)` 仅展示职位名、公司、薪资、城市、Jet 结论以及"HR 实际情况：已记录 / 未记录"的文本标签，未展示 HR 实际情况全文，也未提供行内修改能力。

### 2. 技术决定
- **决定**：
  1. **标签体系扩展**：
     - 最前面新增第 6 个标签「全部岗位」（对应 API 参数 `filter=all_jobs`），其范围为：当前用户有状态的岗位 ∪ 判断过的岗位 ∪ 记过 HR 实际情况的岗位（去重）（FR-044）。
     - 002 原有的「全部」标签在界面上已于 2026-09-25 改名为「已标记」（只含当前有状态的岗位），对应 API 参数保持为 `filter=all` 以兼容历史。默认选中逻辑保持不变（优先「已标记」，若为 0 则选「最近看过」）（FR-046）。
  2. **叠加「只看有 HR 记录」**：
     - GET 接口新增布尔参数 `hr_only: bool`。
     - 勾选后，列表仅展示当前标签范围内存在 HR 实际情况记录的岗位；同时各标签旁的数量统计（counts）更新为对应状态下有 HR 记录的岗位数（FR-047）。
  3. **后端搜索（`q` 参数）**：
     - GET 接口新增搜索参数 `q: str`。由本机 Jet 服务端执行 SQLite 包含匹配（`LIKE`），匹配：该岗位所有历史版本的职位名（`job_versions.title`）、公司名（`jobs.company_name`）、HR 实际情况内容（`hr_notes.note`）（FR-049）。
     - 对用户输入的特殊字符 `%` 与 `_` 进行严格转义（`ESCAPE '/'`）。
     - 单次最多返回 200 条；当总匹配数超过 200 时，返回体中包含总数 `total_matches: N`，前端展示"只显示了前 200 条，共 N 条"（FR-050）。
  4. **排序规则**：
     - 「全部岗位」与搜索结果均按"最近更新时间"（spec R7）倒序排列。
     - 最近更新时间 = `MAX(status_updated_at, hr_note_updated_at, detail_last_seen_at, last_judged_at)`（只看当前用户，列表页读到不算）（FR-051）。
  5. **HR 实际情况行内展示与就地修改**：
     - 每行直接展示 HR 实际情况内容（最多两行，超出宽度省略，支持点击"展开"查看全文 ≤ 200 字；无记录显示"未记录"）（FR-041）。
     - 提供行内修改入口，提交时复用 `PUT /v1/jobs/{platform_job_id}/hr-note`。
     - 插件端严格限制：修改后若内容为空或仅剩空白字符（空格、换行），禁用保存按钮并提示"清空请到岗位卡片操作"（FR-043）。
  6. **行内修改状态与移出标注**：
     - 在行内直接修改状态，复用 `PUT /v1/jobs/{platform_job_id}/status`。修改后该行保留在原位不跳动，按钮更新为新状态，并显示移出标注；若改回原状态标注消失；取消状态同样保留原位并标注移出（FR-052、FR-053）。
- **理由**：
  - 彻底解决 Issue #5（缺乏真正全库视图、有 HR 记录但无状态岗位丢失、无法全局搜索 HR 内容与公司）。
  - 搜索与排序在服务端 SQLite 统一计算，避免前端多次拉取大数据量引发卡顿。
- **考虑过的其他做法**：
  - *做法 B：前端一次性拉取所有岗位在浏览器内存中搜索与过滤*。缺陷：破坏了 200 条单次加载上限，岗位库增长至数千条时网络和内存开销剧增。

---

## R-e 设置页交互与个人数据扩展

### 1. 现有代码调研事实 (FACT)
- **设置页数据交互**：
  - 文件与行号：`extension/src/options.html` 第 44–71 行，`extension/src/options.js` 第 106–168、240–300 行。
  - 机制：设置页展示配对状态、额度信息，并通过 `get_profile` 与 `save_profile` 消息与 background 通信，由 background 调用 `/v1/profile` 读写用户画像。

### 2. 技术决定
- **决定**：
  1. **「我的经历素材」编辑模块**：
     - 放置在 `options.html` 中画像配置的下方，作为独立的卡片模块。
     - 支持一条一条添加、修改或删除，最多 10 条，每条 ≤ 200 字，显示实时字数统计。
     - 新增本机接口 `GET /v1/experience` 与 `PUT /v1/experience`，经历素材保存在服务端数据库的新表 `experience_items` 中（按用户隔离，无历史版本）（FR-028、FR-033）。
  2. **「数据发送同意管理」模块**：
     - 放置在设置页显眼位置，展示当前同意状态（已同意 / 未同意）、最近同意时间以及同意时的字段版本。
     - 提供"撤回同意"按钮。点击后调用 `POST /v1/chat/revoke-consent`，服务端将同意记录标记为撤回；撤回后再次点击生成必须重新征得同意（FR-026）。
- **理由**：
  - 经历素材和同意记录均属于敏感个人数据，统一由本机 Jet 服务持久化管理，与 002 画像做法一致；设置页仅作为纯 UI 输入输出端。
- **考虑过的其他做法**：
  - *做法 B：经历素材保存在插件的 `chrome.storage.local` 中*。缺陷：违反"插件不做判断、不保存画像以外个人数据副本"（原则 III），且大模型生成是在服务端完成，若放在插件端每次生成都要重复上传大段素材，增加通信开销且难以保证跨设备一致性。

---

## R-f 测试组织、Fixture 与安全隔离

### 1. 现有代码调研事实 (FACT)
- **服务端测试与数据隔离**：
  - 文件与行号：`tests/conftest.py` 第 15–38、39–51、81–120 行。
  - 机制：
    - `_snapshot_dir` 检查：在测试 session 开始与结束时比对真实数据目录（`~/Library/Application Support/Jet`），如果测试过程中有任何写入真实目录的行为，直接将测试判为红（原则 VIII）。
    - 环境变量与临时目录隔离：`isolate_env` 自动将 `JET_DATA_DIR` 注入为 `tmp_path / "jet-data"`，清理所有 `JET_` 环境变量。
    - `FakeLlmHelper`：在测试中充当假的大模型传输层（mock transport），支持配置预设的回复内容（fit/unfit/unsure/json），并记录调用次数与请求体。
- **插件端测试**：
  - 文件与行号：`extension/tests/view-state.test.js`、`extension/tests/reader.test.js` 等。
  - 运行方式：使用 Node.js 原生测试运行器 `node --test extension/tests/*.test.js`，无构建、无第三方库依赖。

### 2. 技术决定
- **决定**：
  1. **服务端测试扩展（pytest）**：
     - 新增 `tests/unit/test_sanitization.py`：对脱敏纯函数进行详尽单测（覆盖 HR 姓名、我方姓名、姓+称呼、电话、微信、邮箱、各种系统卡片的剔除）。
     - 新增 `tests/unit/test_prompt_hash.py`：验证"预览文本哈希 = 实际发送文本哈希"的一致性检验与防篡改拒绝逻辑。
     - 新增 `tests/unit/test_experience.py`：验证经历素材的 10 条上限、单条 200 字限制、生成后引用的编号存在性校验与丢弃逻辑。
     - 新增 `tests/api/test_assist_api.py`：使用 `FakeLlmHelper` 测试预览、同意、撤回、生成、额度扣减与并发超额拒绝。
     - 新增 `tests/unit/test_myjobs_search.py`：测试「全部岗位」联合查询、「只看有 HR 记录」过滤、`LIKE` 特殊字符转义与排序。
  2. **插件端测试扩展（node --test）**：
     - 新增 `extension/tests/chat-reader.test.js`：使用脱敏后的聊天页真实样本（JSON 格式），验证 `readBossChatPage` 对消息、卡片、完整职位名的正确提取与系统消息过滤。
     - 新增 `extension/tests/myjobs-row.test.js`：验证岗位库改版后新视图行（HR 说的话两行截断、修改入口、移出标注）的状态机转换。
- **理由**：
  - 严格遵守宪法原则 VIII（默认测试不联网、不需 Key、不写死真实路径、真实数据防污染）。

---

## 调研总结与关键证据等级汇总

| 调研项 | 核心结论 | 证据等级 |
|---|---|---|
| 聊天页 Vue 组件 | `message-list` 包含 `boss`（含岗位 ID、HR 姓名等）；`ChatMessage` 包含 `message`（含正文与类型）；通过可见性精准过滤当前会话 | FACT |
| 完整职位名位置 | 页面顶部 `.chat-position-content` 中的 `.position-name` 为最全版本，优于会话数据中的 `jobName` | FACT |
| V8 切换发现与核对 | 平时由顶部文字变化触发、MAIN world 只读一次岗位 ID 比较发现切换；用户点"复制"时再读一次岗位 ID 核对，不一致拒绝复制；2026-09-28 T083 用户实测：切换聊天时立即显示'正在识别当前聊天…'，1～3 秒后显示新内容；来回切换 3～4 次都正常；停留 20 秒没有被误遮住 | FACT |
| 隐私脱敏机制 | 服务端纯函数脱敏 + 哈希强校验，保证"所见即所发"，服务端不保存聊天内容；复姓表（2-A）做法：服务端内置常见复姓表，姓名开头命中复姓时用"复姓 + 称呼"，否则用首字 + 称呼 | 设计（NOT VERIFIED，实现后由测试验证） |
| 数据库迁移 | 沿用 002 迁移规范，升至 `user_version = 5`，自动备份 `jet.db.bak-<时间>-v4` | FACT |
| 我自己的姓名字段 | 页面数据 `userInfo.name`；2026-09-28 同意预览中我的姓名显示为 [已隐藏] | FACT |
| HR 请求卡片样本 | `bodyType = 7` 卡片目前仅有 1 个真实样本 | NOT VERIFIED (V7) |

---

## SC-013 真实使用验收（2026-09-27）（2026-09-27 SC-013 真实使用后）

### 1. 真实使用验收事实 (FACT)
- **3 个真实聊天验收结果**：
  1. **李女士（某智能制造公司）** (FACT)：
     - 判断正确，生成的两个版本话术均能直接使用。
     - 语气问题："嗯嗯，想确认下这个岗位有销售指标或业绩考核吗？"在口语词后直接接提问，显得生硬 → 新增语气规则第 8 条与代码兜底改写。
     - 事实核查："转正后的管理岗"不是 bug 或模型幻觉，HR 在更早的消息里确实提及过"管理"和"转正"。
     - 姓名脱敏 (V6)：HR 引用消息中的"（用户姓名）：……"整段没有被读入，同意预览里只有"HR：都有的"；用户姓名没有被发送。
  2. **第 2 个聊天** (FACT)：
     - 判断正确，生成的话术能直接使用。
  3. **王先生（某研究所 · 技术助理）** (FACT)：
     - 验收失败。用户已发送附件简历（文件卡片），前面还有"附件简历请求已发送"系统提示。但原规则将 `item-system` 样式的卡片全部排除，导致 Jet 只读到 2 条消息（BOSS 自动招呼语 + HR"方便的话发一份简历过来"），系统误判为"建议回复"并给出了"简历我稍后发您"的错误话术 → 动作类卡片改为固定标记发送。
- **动作类卡片特征与样本 (FACT)**：
  - "附件简历请求已发送"：页面样式为 `item-system`，`isSelf = true`，`type = 1`，`bodyType = 4`，文字固定（1 个样本：王先生 · 技术助理聊天）。
  - 附件简历文件卡片：页面样式为 `item-system`，`isSelf = false`（虽是用户本人发送），`type = 3`，`bodyType = 12`，文字含文件名（2 个样本：技术助理聊天"……简历A……pdf"；实习生聊天"您的附件简历 …… 已发送给Boss点击查看附件"）。

### 2. 结论与技术决定 (INFERENCE / FACT)
- **V6 结论 (FACT / INFERENCE)**：
  - 李女士聊天中 HR 引用消息中的"（用户姓名）：……"整段未被读入，预览中只有"HR：都有的"，用户姓名未被发送。但"从页面字段取名再删除"的机制在真实聊天中没有遇到需要删除的场景。
  - 结论：真实聊天中未出现需删除的场景，风险低，不再专门验证（2026-09-27 SC-013 真实使用后）。
- **动作类卡片固定标记 (INFERENCE)**：
  - 为防止泄露文件名等个人信息并使大模型感知真实交互状态，已知动作类卡片按规则文件 `src/jet/llm/prompts/action_card_rules.json` 转为固定标记发送，不带文件名等任何原文：
    - `bodyType = 4` 且文字含"附件简历请求已发送" → 固定标记为"我：[附件简历请求已发送]"；
    - `bodyType = 12` 且文字含"简历"或 `.pdf`/`.doc`/`.docx`（大小写均支持） → 固定标记为"我：[已发送附件简历]"；
    - 发送方按规则统一固定为"我"（不看前端 `isSelf` 字段）；
    - 这些标记算作对应一方的消息，参与"判断为"、"HR 请求是否已处理"和"根据已加载的 N 条消息"的计数；
    - 未在规则中定义的未知卡片仍不发送；固定标记不含个人信息，属于聊天消息这一类，发送字段版本仍为 2，不需要重新同意。
- **语气第 8 条与代码兜底改写 (INFERENCE)**：
  - 新增语气规则第 8 条："口语词（嗯嗯、好的、了解了、明白了、好嘞等）后面不要直接接提问，要先有一句承接，例如'嗯嗯，了解了。那想确认下……'"。
  - 本机 Jet 服务代码按 `src/jet/llm/prompts/tone_fix_rules.json` 增加兜底改写机制：将口语词后直接接提问的话术自动改写为"口语词，了解了。那+原提问"（例如"嗯嗯，了解了。那想确认下……"）；改写后超过 50 字的话术按原规则去掉并不予展示，并计入 `dropped_summary`。

## 阶段 C / C1 真实页面验证记录（2026-09-26 至 2026-09-27）

- **FACT（同意与预览，T028 部分）**：撤回同意后点生成只出现同意页，没有生成调用；预览中无 HR 姓名、无竞争者PK卡片，附件简历请求带"【HR 请求卡片】"标注（V7 → FACT）；同意后才生成；设置页撤回（2026-09-26 17:11:51）后再次出现同意页。P3（点"取消"不发送）没有单独验证记录。
- **FACT（同意版本 2 与自动生成，T074）**：18:21:23 只有预览、弹出同意页，llm_calls 在 18:21:23–18:21:58 之间 assist 记录为 0 条（Claude Code 只读核对）；18:21:58 同意（版本 2）后 18:21:59 才生成；18:22:07–09 打开另一个聊天自动预览并生成；切回原聊天无新消息时直接显示原结果（缓存）；关闭开关后切换聊天不自动生成（18:24:50 为手动生成）。
- **FACT（切换与复制前核对，T031）**：复制能粘出版本 1 的文字；切换聊天后旧结果不再显示；侧边栏停在旧聊天、当前页面已是另一聊天时点复制，剪贴板为空、旧结果被清空。开关关闭时切换未更新的问题已由 `ac20695` 修复（重试读取岗位 ID + 侧边栏每 5 秒兜底核对）；修复后的专门复测没有单独记录。
- **FACT（SC-013 验收，T032）**：3 个真实聊天全部通过（修复后王先生聊天判断为"正在等 HR 回复"，依据"我：[已发送附件简历]"，只给问题、不再提发简历，根据已加载的 4 条消息生成）。
- **FACT（次数上限，T033）**：临时设置 `JET_DAILY_ASSIST_LIMIT=1` 后，打开当天未生成过的聊天，侧边栏提示"今日生成次数已用完"、没有新话术；只读核对当天 assist 记录只有 11:11:01、11:13:37 两条，其后没有新记录。用户已从 `.env` 删除该行，数据库中的上限恢复为 50。
- **FACT（话术行为）**：请求提示带"话术按你同意来写……"；话术不再声称已完成的操作，不再替用户回答所在城市；没有请求卡片时不再主动提发简历；HR 职务不再被当成岗位（提示词说明"岗位以【岗位信息】为准"）。
- **NOT VERIFIED**：G1（录入经历素材后话术引用条目）、G2（HR 未回复时生成开场白）在真实页面尚无记录（验证期间经历素材为空）。
- **费用参考（V4，仍未完成验证）**：2026-09-26 至 09-27 共 8 次 assist 计费调用，单次约 0.0021–0.0040 元。

## 阶段 C2 真实页面验证记录（2026-09-27）

- **FACT（T055）**：刷新插件后，点击 Chrome 工具栏 Jet 图标打开侧边栏（不再打开岗位库标签页）；侧边栏「我的岗位库」按钮在新标签页打开岗位库；设置页"打开我的岗位库"链接仍然可用。
- **FACT（实现依据）**：Chrome 官方文档 https://developer.chrome.com/docs/extensions/reference/api/sidePanel ：Side Panel API 标注 "Chrome 114+ MV3+"，setPanelBehavior / openPanelOnActionClick 无单独版本标注，manifest 的 minimum_chrome_version 114 未改。
- **决定**：侧边栏每 5 秒刷新的切换兜底检查保持"与正在显示的结果所属岗位 ID 比较"的方式（没有结果时不检查、不触发生成），用户 2026-09-27 确认不改。

## 阶段 E 真实页面验证记录（2026-09-27）

- **FACT（用户实测）**：
  - 「全部岗位」排第一、默认仍是「已标记」、各标签带数量：通过（M1，悬停说明未单独验证）。
  - 勾选"只看有 HR 记录"：全部岗位 3、收藏 1，数量联动：通过（M5）。
  - 在岗位库修改 HR 实际情况并保存：通过（M4）；删空后保存按钮不可用并提示"清空请到岗位卡片操作"：通过（M3）。
  - 搜"外包"：返回"服务部生产主管"（HR 实际情况含"外包"）与"互联网产品运营-大厂外包"（职位名含"外包"）：通过（M6 部分）。
  - 在「收藏」把"动物实验技术员"改为"不考虑"：行留在原位并显示"已改为 不考虑，切换标签或刷新后移出本列表"，收藏 3→2、不考虑 1→2：通过（M7）。
- **顺带确认**：此前卡片"保存失败"与卡片不随岗位切换，原因是未重启 jet serve（服务端仍为阶段 D 之前代码），重启后正常，不是 bug。
- **未验证**：M1 悬停说明；M2 两行显示与展开（且发现只有一行的内容也显示"展开"，待修）；M6 中 %/_ 转义、历史职位名匹配、超过 200 条提示；M8 改回原状态标注消失；M9 取消状态标注移出。
- **自动测试**：uv run --frozen pytest 377 passed；node --test extension/tests/*.test.js 169/169。

## T076–T079 修复与真实页面验证记录（2026-09-27）

- **FACT（用户实测，T079）**：
  - 卡片自动保存：停手 1～2 秒显示"已自动保存"；删空不保存并提示；刷新后内容保留。
  - 岗位库自动保存：追加文字后自动保存；删空不保存并提示"清空请到岗位卡片操作"；点"完成"后恢复原内容。
  - "展开"：内容两行能显示完时不出现；窗口拖窄到超过两行时出现，展开看全文、收起正常，拉宽后消失。
  - 侧边栏"当前岗位"：BOSS 页面上显示职位名（动物房管理员），不再显示编号；在岗位库页面显示"未打开岗位详情"，在岗位库改状态后不变。
  - content.js 语法错误修复后，BOSS 页面卡片正常加载，没有新的报错。
- **未验证**：卡片上点"完成"保留原内容关闭、点"清空"正常清空。
- **FACT（原因与修复）**：
  - 侧边栏显示岗位 ID：岗位库页面发 save_hr_note / set_job_status 时，background 把岗位库标签页当成岗位页建立"当前岗位"状态（background.js restoreTabStateIfNeeded / getOrCreateTabState），服务端 _job_entry 不返回 title，侧边栏无标题时显示 platform_job_id（sidepanel.js 第 125 行）。修复：_job_entry 返回当前版本 title；background 仅在消息来自 https://www.zhipin.com/ 页面（isBossPageSender）时读取/创建/修改标签页状态。用户决定："当前岗位"只表示用户在 BOSS 页面上正在看的岗位。
  - content.js 语法错误：把共用自动保存逻辑接入卡片时，renderHrNoteSection 编辑分支末尾少了一个 `}`，整个 content.js 无法加载（报错位置 content.js:3795 的 `})();`）。node 测试未发现，因为同步测试只截取 SYNC 块运行，其他测试只把 content.js 当文本读；git diff --check 只查空白。修复：补上 `}`；新增 extension/tests/syntax-check.test.js，对 extension/src 下每个 .js 执行 node --check，并按 manifest content_scripts 用 vm.Script 以普通脚本方式解析（因 extension/package.json 为 "type": "module"，node --check 会把 content.js 当模块解析）。
- **自动测试**：uv run --frozen pytest 379 passed；node --test extension/tests/*.test.js 214/214；node --check extension/src/*.js 16 个文件全部通过。

## 阶段 E2 真实页面验证与切换延迟记录（2026-09-28）

- **FACT（用户实测，T062 部分）**："不在库"：打开赵女士（某科技公司 · 带薪实习生）的聊天，侧边栏"当前岗位"显示"岗位库里没有这个岗位。在 BOSS 搜索列表里看到它时，Jet 会记录并判断"：通过。"在库"：现有聊天的岗位在 BOSS 搜索里都搜不到，无法进入岗位库，暂时验证不了；待用户下次从搜索列表点"立即沟通"开聊时顺带验证。
- **FACT（用户实测）**：切换聊天后侧边栏要等 2～3 秒才更新，期间 HR 沟通助手仍显示"这是给 某研究所 · 技术助理 的建议"。
- **INFERENCE（按代码推理，未实测计时）**：链路为 content.js 顶部文字变化 150ms 防抖 → background 读取 Vue 中 encryptJobId，与上次相同时按原 CHAT_SWITCH_RETRY_DELAYS = [300, 800, 1500, 3000] 重试，实际检查时刻约为顶部变化后 0.15 / 0.45 / 1.25 / 2.75 / 5.75 秒；实测 2～3 秒对应 2.75 秒那次才读到新 ID，推断 BOSS 页面岗位 ID 在顶部变化后约 1.25～2.75 秒才更新。E2 中"先查岗位库再广播"只增加一次本机请求（毫秒级），不是主因。改为每 200ms 重试后，识别时间约为"页面实际更新时刻 + 不超过 0.2 秒"，按上述推断仍可能需要 1.3～3 秒，因此以遮盖（FR-071）保证期间不显示旧内容。
- **决定**：用户 2026-09-28 同意侧边栏 HR 记录用自动保存 +"完成"、空内容不保存；聊天页复用现有"当前岗位"卡片（FR-058 补充说明）。
- **自动测试**：node --test extension/tests/*.test.js 240/240；uv run --frozen pytest 381 passed；node --check extension/src/*.js 全部通过。

## 2026-09-28 真实页面验证与 Codex 审核记录

- **FACT（用户实测，9/28 07:28–07:32，最新代码：jet serve 已重启、插件已重新加载）**：
  - 同意页预览串单：按 6 步复现，第 3 步（同意页开着时切换到另一会话）、第 4 步（不刷新，在新会话里重新生成），职位和公司都正确；第 5 步（刷新后再生成）显示正确。9/27 晚的现象出现在旧代码上（jet serve 于 9/27 23:35 启动，未加载 d8131c4，插件也未重载），记为"当前代码未复现"。
  - T028 · P3：多次点"取消"，日志中只有 preview、judgements 和心跳请求，没有 consent 或 generate 请求：通过。
  - T028 · V6：同意预览里我的姓名显示为 [已隐藏]：通过。
  - T083（部分）：立即遮盖、1～3 秒后显示新内容、来回切换 3～4 次正常、停留 20 秒没有误遮：通过；未验证：误报时恢复原显示、生成中遇到遮盖后生成按钮不会被重新启用。
- **V4**：本机 llm_calls 共 23 次 assist 计费调用，单次平均约 0.0032 元（0.0011–0.0041 元），改为已验证。
- **Codex 审核结论**：Codex 提出"bodyType 可能以字符串出现，需统一转成数字"并已被另一会话提交（9963452）。结论：不采纳。理由：实验记录 bodyType 为数字（research.md 消息结构一节）；插件 extension/src/page-reader.js 只在 typeof m.bodyType === "number" 时采用，否则默认 1，字符串不可能发到服务端；属于为不会发生的情况增加复杂度（过度防御）。用户已手动 revert（b7eab64）。

## 2026-09-28 G 系列真实页面验证记录（T030）

- FACT（用户实测，9/28 08:06–08:17，自动生成已关闭，手动点生成）：
  - G1：2 条经历素材保存成功（12 字、10 字），字数实时更新正确：通过。
  - G2（临床数据管理实习生聊天）：判定为开场白并显示依据；顶部显示公司和职位；2 个版本，每条 ≤ 50 字；来源标"经历条目 1"，正文无编号：通过。同意流程 08:10:32 preview → 08:10:43 consent → 08:10:44 generate。
  - G4（产品专员聊天，我最后一句为拒绝）：显示"正在等 HR 回复"，依据正确，没有生成回复话术，只给出问题：通过。
  - G6：三个聊天都显示"这个岗位没有 Jet 判断"，都包含确认岗位职责的提问：通过。
  - G3（实习生聊天）：不通过。HR 已回答的问题又出现（SC-005 语义层面）：我问"客户资源是公司提供，还是需要自己开发客户、地推或做销售业绩"，HR 回"都有的"，两个版本和问题 1 仍问"是否有销售指标或业绩考核"；问题 2 问"转正后的管理岗主要职责是团队管理还是个人业绩"，而 HR 已写明"三个月转正晋升管理岗，负责团队日常管理，项目运营等管理工作"。
  - 当日 3 次生成都出现"是否有销售指标或业绩考核"，其中两次 HR 已给过相关信息。
  - 侧边栏顶部在 3 次生成后仍显示"今日已用 0 / 上限 150"。
- FACT（Claude Code 只读核对）：
  - G7：llm_calls 今日 3 条 assist 记录（id 250–252，00:10:43Z、00:11:27Z、00:16:10Z），model 均为 deepseek-flash，均有 input/cached/output tokens 与 cost_cny（0.003694、0.00339224、0.00218）；llm_calls 表 15 列中没有任何文本内容列：通过。
  - 用量显示原因：侧边栏顶部横幅（extension/src/sidepanel.js renderQuota）显示的是 GET /v1/status 的 quota，而该接口只统计 purpose='judge'、上限取 daily_llm_limit（150）（src/jet/api/routes.py 状态接口注释"only counting purpose='judge'"）；沟通建议的次数由 src/jet/llm/quota.py remaining_today(purpose="assist") 单独计算（上限 daily_assist_limit=50），侧边栏没有显示。"今日"按本机时区（src/jet/db/store.py local_day_bounds_utc，datetime.now().astimezone()）。
  - 重复提问原因：提示词只有一句"提出最多 3 个建议问 HR 的问题，排除聊天中已经问过的或已有答案的问题。"（src/jet/llm/assist.py），没有说明 HR 用"都有的"等简短词回应多选提问应视为已回答；代码层只做逐字比对去重。无 Jet 判断时，assist.py 两处固定要求"请在建议中带上确认岗位性质（如主要职责、是否有销售指标/业绩考核等）的问题"，没有"已说明过则不问"的条件，与 FR-011 原文"MUST 包含……是否有销售指标"一致，要求过死；"6k底薪（5k无责+1k绩效）+奖金+提成"是 HR 的聊天消息，已在发送内容中（侧边栏显示"根据已加载的 4 条消息"，它是其中一条），模型能看到；问题在于提示词没有要求把 HR 主动说明过的内容视为已回答（2026-09-28 用户更正）。
- 观察（先不修，留到 SC-013 再看）：
  - G2 开场白重复了我已经发过两次的自我介绍。
  - G4 在我已明确拒绝（"不需要了哦"）后，仍建议继续向 HR 提问；spec 没有覆盖"我方已拒绝"这种情况。

## 2026-09-28 引用回复结构探测记录

- **FACT**：探测结果（quoteId 仅在引用回复消息上为 number；每条消息有 mid；消息对象无被引用原文字段；页面有 quote-message 等 class）；
- **决定**：只按 quoteId→mid 查找，不读 DOM 引用块，引用太早读不到原文是有意取舍；quoteId = mid 已验证（见下一节）。

## 2026-09-28 引用上下文真实生成验证（G3 复测）

- **FACT（用户实测，李女士聊天，12:33，沟通建议用量 12→13）**：
  - HR 以"都有的"引用回复的问题"客户资源是公司提供还是自己开发"不再出现：引用上下文生效，据此判定 quoteId = 被引用消息的 mid（FR-073 已验证）。
  - 两个版本和问题 1 仍问"是否有销售业绩指标或考核"。"都有的"只确认了要做销售业绩，没有说明指标和考核方式，算半个合理的追问。
  - "试用期 3800 是无责任底薪，还是和业绩挂钩"再次出现（上一次生成没有），而 HR 原话写了"无责任底薪3800元/月"：known_facts 在 deepseek-flash 上不稳定。
- **决定（用户，2026-09-28）**：不再继续改提示词。G3 记为部分通过：引用上下文生效；模型仍偶尔重复 HR 已经回答过的内容，作为已知局限，到 SC-013 真实使用时统计出现频率，频率高再评估换更强的模型。

## 2026-09-28 剩余手测项核对（T046、T047、T079）

用户决定本轮不再手测以下项，按"自动测试是否覆盖"归类（Claude Code 只读核对测试用例）：

- **自动测试已验证**（逻辑）：
  - M6 搜索 `%`、`_` 按字面匹配：tests/unit/test_myjobs_search.py::test_like_escape_percent_and_underscore、test_escape_like；tests/api/test_myjobs_api.py::test_api_get_my_jobs_like_escape。历史职位名匹配、超过 200 条提示、搜索词编码也有对应用例（见 tasks.md T047）。
  - M8 改回原状态后标注消失：extension/tests/myjobs-row.test.js「T041: 状态机：改回原状态移出标注消失」。
  - M9 取消状态出现"移出"标注：extension/tests/myjobs-row.test.js「T041: 状态机：取消状态生成移出标注」「「已标记」标签内互换状态不标注，取消状态标注移出」。
  - 卡片"完成"保留原内容（自动保存协调器）：extension/tests/hr-note-autosave.test.js「handleComplete 保存未保存变动或保留原内容」，及 hr-note-autosave-sync.test.js 对 content.js 副本的用例。
  - 卡片"清空"后服务端为空：tests/api/test_hr_note_api.py::test_put_hr_note_card_empty_allowed、test_put_hr_note_default_source_is_card（响应由数据库读回，刷新后同样为空）。
- **日常使用中观察**（界面效果，不挡合并）：M1 悬停"全部岗位"显示说明——title 文字由 myjobs-row.test.js「T042-T045: myjobs.html 结构与组件标记合规检查」验证存在，悬停显示由浏览器负责。
- **未覆盖、有出错风险**（列给用户决定，暂不补测试）：content.js 卡片"完成""清空"按钮的事件接线（点击后关闭编辑框、清空时先取消自动保存再发送清空）；自动保存请求在途时点"清空"的先后顺序（在途保存的成功回调可能在清空之后把旧内容写回卡片显示）。T079 因此保持未勾选。

## 2026-09-28 Codex 补审记录（7bd2cff、dfc7de0、710c632、930f6bc）

- **背景**：这 4 个代码提交在提交前漏了 CLAUDE.md 要求的 Codex 审稿，按用户要求补审。审稿范围：`review --base e51feab`（7bd2cff 的前一个提交）到当时的 HEAD；范围内的代码文件与这 4 个提交改动的代码文件完全一致（17 个），多出的只有中间纯文档提交的 `.md` 文件。
- **Codex 结论**：只有 1 条意见——[P2] src/jet/llm/sanitize.py：解析引用时在整批保留消息中找第一条 `mid` 相同的消息，可能匹配到回复之后的消息，建议只在回复之前的消息中查找。
- **判断（Claude Code）**：
  - 采纳：无。
  - 不采纳：[P2]。理由：被引用的消息必然先于回复存在，`mid` 是每条消息的编号，按编号找到的就是那一条；插件按页面顺序（时间顺序）读取消息。只有编号重复时才会匹配到之后的消息，没有证据，属于为不会发生的情况加防护（过度防御）。
- 因无采纳项，本次补审没有代码修改。
