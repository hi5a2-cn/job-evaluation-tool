# Tasks: HR 沟通助手 + 我的岗位库改版 (003-hr-assistant)

**Input**: Design documents from `/specs/003-hr-assistant/` (`spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/local-api.md`, `quickstart.md`)

**Prerequisites**: 002 既有架构（`src/jet/`、`extension/`、`tests/`、SQLite WAL 模式、配对鉴权）

**Tests**: 包含完整的自动化测试任务与真实页面验证任务。测试必须遵循宪法原则 VIII（默认测试不联网、不需 Key、不开浏览器、不污染真实数据目录）。

**Organization**: 按照规划分为 8 个阶段。P1 HR 沟通助手在前（阶段 A 数据库迁移 → 阶段 B 服务端核心与 API → 阶段 C 插件端 → 阶段 C2 工具栏图标打开侧边栏），P2 岗位库改版在后（阶段 D 岗位库服务端 → 阶段 E 岗位库插件端 → 阶段 E2 聊天页关联岗位库），最后统一进入阶段 F 收尾。每个阶段末尾均设有统一的自动化测试检查点任务。

## 任务格式规范

`- [ ] T001 [P] [Story] 任务说明（含文件路径） 〔执行：执行者〕`
- **[P]**: 可并行任务（与其他任务无文件冲突且不依赖前置任务产物）
- **[Story]**: 所属用户故事标签（US1–US8，对应 `spec.md` 中的用户故事）
- **执行者标注**:
  - `〔执行：Antigravity〕`: 写代码、写测试、跑测试等开发任务
  - `〔执行：Claude Code〕`: 架构审核、Git 提交、真实页面验证协调与证据归档
  - `〔执行：用户〕`: 真实 BOSS 页面上的实际手工交互与效果验收

---

## 阶段目标与依赖关系说明

```text
阶段 A: 数据库迁移 (user_version 5) ──┐
                                     ├──> 阶段 B: 服务端核心与 API ──> 阶段 C: HR 助手插件端 ──> 阶段 C1: 阶段 C 补充（自动生成、不替用户做决定） ──> 阶段 C2: 工具栏图标打开侧边栏 ──┐
                                     └──> 阶段 D: 岗位库服务端 ──────> 阶段 E: 岗位库插件端 ──> 阶段 E2: 聊天页关联岗位库 ──────────────────────────────────┴──> 阶段 F: 收尾与全量测试
```

1. **阶段 A（数据库迁移，user_version 5）**：基础前置阶段。为 `llm_calls` 扩展 `assist` 用途，新建经历素材表 `experience_items` 与发送同意表 `llm_consents`，升级 `user_settings`。后续所有服务端功能均强依赖此阶段。
2. **阶段 B（服务端核心与接口，P1）**：实现确定性纯函数脱敏、复姓处理、知情同意维护、Prompt 哈希与防篡改、生成接口（严格按四步顺序校验）、每日上限控制、经历素材读写及独立语气规则文件。
3. **阶段 C（插件端 HR 沟通助手，P1）**：在 BOSS 聊天页实现仅在点击时执行的 MAIN world 消息读取、基于会话顶部的轻量切换发现、侧边栏同意与结果展示、复制防错核对，并在设置页支持经历素材维护与撤回同意。末尾完成真实页面验证（P1–P5、G1–G10、L1–L3，含 V6、V7、V8 与 SC-013）。
3b. **阶段 C1（阶段 C 补充：自动生成、不替用户做决定，P1，2026-09-26 用户决定，真实页面验证后）**：服务端增加完成声称规则文件与核验过滤（含声称已完成某操作的话术直接丢弃并计入 dropped_summary）、请求提示末尾追加"话术按你同意来写，不同意的话请自己回复"；发送字段版本升为 2 并使旧记录失效；侧边栏实现打开聊天自动生成与内存缓存（按"岗位 ID + 最后一条消息"）、设置页增加"打开聊天自动生成"开关、同意页文案更新、次数用完提示；完成真实页面验证与检查点。（2026-09-26 用户决定，真实页面验证后）
4. **阶段 C2（工具栏图标打开侧边栏，P2，阶段 C 真实页面验证完成后）**：将点击工具栏图标行为改为打开侧边栏（`openPanelOnActionClick: true` 并移除原点击打开岗位库监听），并在侧边栏中增加「我的岗位库」按钮（新标签页打开 `myjobs.html`），变更 002 FR-045 岗位库入口。
5. **阶段 D（岗位库服务端，P2）**：扩展 `GET /v1/my-jobs` 接口，支持「全部岗位」（并集去重）、`hr_only` 过滤与数量联动、包含所有历史版本职位名的 `q` 搜索（LIKE 转义）及按最近更新时间排序；服务端拦截来自岗位库的空 HR 记录提交。
6. **阶段 E（岗位库插件端，P2）**：重构独立标签页 `myjobs.html`，展示 HR 实际情况（两行自适应截断与展开全文）、行内修改且禁止清空、新增「全部岗位」标签与「只看有 HR 记录」复选框、搜索框与超过 200 条提示、行内改状态与原位移出标注。末尾完成真实页面验证（M1–M9）。
7. **阶段 E2（聊天页关联岗位库，P2，复用阶段 E 组件）**：在 BOSS 聊天页依据当前聊天岗位 ID（`encryptJobId`）只读查询本机公共岗位库已有数据；侧边栏展示当前岗位区块（Jet 结论与 HR 实际情况，复用阶段 E 就地修改组件，新增来源标识并前后端均拦截清空；不在库显示提示原文；切换聊天随之更新）。
8. **阶段 F（收尾与全量测试）**：更新项目 README 与文档，运行全量 Python 与插件端测试，确认无任何回归后交付。

---

## 阶段 A: 数据库迁移（升到 user_version 5）

**阶段目标**: 将数据库结构版本升级至 5。迁移前执行 WAL 清理并自动备份旧库（`jet.db.bak-<时间>-v4`）；在事务中重建 `llm_calls` 表（CHECK 约束包含 `assist` 用途），新建 `experience_items` 与 `llm_consents` 个人数据表，增加 `user_settings.daily_assist_limit` 列；校验行数与外键完整性。

- [x] T001 [P] [US1] 编写数据库迁移单元测试：测试从版本 4 迁移到版本 5，验证迁移前 WAL checkpoint 与自动备份文件 `jet.db.bak-<UTC 时间戳>-v4` 生成、`llm_calls.purpose` 允许 `assist`、`experience_items` 与 `llm_consents` 新表创建、`user_settings.daily_assist_limit` 列添加、行数一致性与外键自检、重复迁移幂等性及异常回滚（`tests/unit/test_migrations.py`）〔执行：Antigravity〕
- [x] T002 [US1] 更新建表脚本至结构版本 5：更新 `src/jet/db/schema.sql`，将 `llm_calls.purpose` CHECK 约束扩展为 `IN ('judge', 'eval', 'assist')`，新增 `experience_items` 表（`item_no BETWEEN 1 AND 10`、`UNIQUE(user_id, item_no)`、`content <= 200`）、新增 `llm_consents` 表（`fields_version` 默认 1）、在 `user_settings` 中新增 `daily_assist_limit` 列（默认 50，CHECK 0–500）（`src/jet/db/schema.sql`）〔执行：Antigravity〕
- [x] T003 [US1] 实现数据库迁移函数 `migrate_v4_to_v5`：在 `src/jet/db/migrations.py` 中实现 v4 到 v5 迁移逻辑，迁移前执行 WAL checkpoint 并备份 `jet.db.bak-<时间>-v4`；事务外关闭外键，`BEGIN IMMEDIATE` 事务内重建 `llm_calls` 表、创建 `experience_items` 和 `llm_consents` 表、按需添加 `user_settings.daily_assist_limit`，校验行数和 `foreign_key_check`，提交后更新 `user_version = 5` 并恢复外键（`src/jet/db/migrations.py`）〔执行：Antigravity〕
- [x] T004 [US1] 更新数据库初始化与自检逻辑：修改 `src/jet/db/store.py`，使 `init_db` 在全新库时直接设 `user_version = 5`，在旧版本库时自动串联执行 `migrate_v4_to_v5`；更新 `_ensure_current_schema` 补建新表与列，确保与迁移结果一致（`src/jet/db/store.py`）〔执行：Antigravity〕
- [x] T005 [US1] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 B: 服务端核心与接口（脱敏、同意、预览、生成、额度、经历素材、语气规则）

**阶段目标**: 实现 HR 沟通助手的全部后端能力。先写测试再实现脱敏与复姓表；实现同意记录、撤回与状态查询；实现脱敏预览与 SHA-256 指纹计算；实现话术生成接口（严格遵循四步顺序校验：1.同意 → 2.我方姓名 → 3.指纹 → 4.预占额度；关闭思考模式）；实现每日上限控制（`JET_DAILY_ASSIST_LIMIT` 默认 50）；实现经历素材 CRUD 与 7 条语气规则文件。

- [x] T006 [P] [US2] 先写测试，确认失败后再实现：编写脱敏与复姓表单元测试，必须覆盖 SC-002 全部样本类型（HR 姓名、我方姓名、"姓 + 称呼"如"刘女士/刘经理/刘总/刘老师"含单姓与常见 30 个复姓如"欧阳女士"、双方电话、微信号、邮箱）、覆盖 SC-003（`bodyType=16` 与 `item-system` 剔除，`bodyType=7` 保留文字并脱敏）、覆盖读不到我方姓名时拒绝（FR-021 第 2 条，抛出或返回特定错误标识）、以及纯函数指纹 SHA-256 一致性（`tests/unit/test_sanitization.py`）〔执行：Antigravity〕
- [x] T007 [US2] 实现纯函数脱敏与复姓过滤模块：新建 `src/jet/llm/sanitize.py`，内置常见 30 个复姓词表，实现 `sanitize_chat_messages(messages, hr_name, user_name)` 纯函数，剔除双方姓名、单复姓+称呼、电话、微信号、邮箱，过滤 `bodyType=16` 与 `item-system` 卡片，提取 `bodyType=7` 请求卡片文字并脱敏；若 `user_name` 为空抛出姓名缺失异常；提供 `compute_prompt_hash(text)` 计算 SHA-256 字符串（`src/jet/llm/sanitize.py`）〔执行：Antigravity〕
- [x] T008 [P] [US2] 编写同意记录与撤回单元测试：测试 `llm_consents` 表的有效同意判定（`revoked_at IS NULL` 且 `fields_version == 1` 为有效；`revoked_at` 非空为已撤回；`fields_version` 不匹配为失效）、记录同意与撤回同意的数据库更新逻辑（`tests/unit/test_consent.py`）〔执行：Antigravity〕
- [x] T009 [US2] 实现同意记录业务逻辑与接口路由：新建 `src/jet/domain/consent.py`，实现 `has_valid_consent(conn, user_id, current_version=1)`、`record_consent(conn, user_id, fields_version=1)`、`revoke_consent(conn, user_id)`；在 `src/jet/api/routes.py` 增加 `POST /v1/chat/consent`、`POST /v1/chat/revoke-consent`、`GET /v1/chat/consent-status`（`src/jet/domain/consent.py`, `src/jet/api/routes.py`）〔执行：Antigravity〕
- [x] T010 [P] [US1] 编写独立语气规则文件：新建 `src/jet/llm/prompts/tone_rules.txt`，逐条录入 FR-035 规定的 7 条约定（称呼您、公司用贵公司、每条话术最多 1 问、敏感问题先铺垫、严禁反问句质疑、不使用客套长句不堆叠符号表情、允许简短自然口语、陈述真实经历严禁夸大捏造）；服务端每次调用时动态读取以支持热加载（`src/jet/llm/prompts/tone_rules.txt`）〔执行：Antigravity〕
- [x] T011 [P] [US3] 编写「我的经历素材」单元与接口测试：测试条目上限 10 条、单条 ≤ 200 字限制、空内容与纯空白字符拦截（422）、全量原子替换保存、编号 1–10 连续性、以及生成结果中引用非法经历编号的判定（`tests/unit/test_experience.py`, `tests/api/test_experience_api.py`）〔执行：Antigravity〕
- [x] T012 [US3] 实现「我的经历素材」领域逻辑与 API 接口：新建 `src/jet/domain/experience.py`，实现经历素材的查询与全量原子替换保存；在 `src/jet/api/routes.py` 中增加 `GET /v1/experience` 与 `PUT /v1/experience`，严格校验条数 ≤ 10 与单条长度 ≤ 200 字且非空白（`src/jet/domain/experience.py`, `src/jet/api/routes.py`）〔执行：Antigravity〕
- [x] T013 [P] [US1] 编写 assist 用途每日上限配置与额度预占测试：测试 `JET_DAILY_ASSIST_LIMIT` 环境变量加载与 0–500 范围校验；测试在 `BEGIN IMMEDIATE` 事务中按本机时区当天统计 `purpose='assist'` 且 `billed=1` 的预占逻辑；并发超过上限拦截；前置检查（同意、我方姓名、指纹）不通过时不预占、不写 `llm_calls`；已发出但失败的调用计入次数（R2）（`tests/unit/test_assist_quota.py`）〔执行：Antigravity〕
- [x] T014 [US1] 实现 assist 每日额度控制与配置扩展：在 `src/jet/config.py` 中增加 `daily_assist_limit`（默认 50，校验 0–500）；修改 `src/jet/llm/quota.py`，扩展 `reserve` 支持 `purpose='assist'`，在 `BEGIN IMMEDIATE` 事务中依据 `daily_assist_limit` 预占额度；修改 `tests/conftest.py` 扩展 `FakeLlmHelper` 支持 assist 话术生成模拟（`src/jet/config.py`, `src/jet/llm/quota.py`, `tests/conftest.py`）〔执行：Antigravity〕
- [x] T015 [US1] 编写预览接口与生成编排单元测试：测试 `POST /v1/chat/preview` 组装脱敏文本与 SHA-256 哈希；测试 `POST /v1/chat/generate` 校验顺序（1. 同意检查 403 → 2. 我方姓名检查 422 `self_name_unavailable` → 3. 指纹比对 400 `hash_mismatch` → 4. 额度预占 429 `quota_exhausted`）；测试沟通模式自动判定（开场白/建议回复/等待HR回复）；测试非法经历引用丢弃与全丢弃 422；测试问题去重过滤（`tests/unit/test_assist_orchestration.py`）〔执行：Antigravity〕
- [x] T016 [US1] 实现话术生成核心编排与 Prompt 组装：新建 `src/jet/llm/assist.py`，集成纯函数脱敏、动态读取语气规则、组装结构化 Prompt（含职位名、公司、城市、Jet 判断结论/风险/问题（如有）、HR 实际情况（如有）、已脱敏消息、经历素材）；实现沟通阶段判定（`opening` / `reply` / `waiting_hr`）；调用 `call_once`（关闭思考模式，20s 超时）；核验返回的话术经历编号（非法直接丢弃）与问题去重（排除已在聊天出现或已有答案的问题）（`src/jet/llm/assist.py`）〔执行：Antigravity〕
- [x] T017 [US1] 实现预览与生成 API 路由：在 `src/jet/api/routes.py` 中增加 `POST /v1/chat/preview` 与 `POST /v1/chat/generate`，严格按照契约顺序执行校验（同意 → 我方姓名 → 哈希一致性 → 预占额度），处理 `waiting_hr` 仅返回问题分支，记录 `llm_calls` 流水（绝对不存正文与话术文本）（`src/jet/api/routes.py`）〔执行：Antigravity〕
- [x] T018 [US1] 编写服务端 API 端到端集成测试：编写端到端 API 测试，验证未同意（403）、读不到姓名（422）、哈希被篡改（400）、超额（429）、正常生成返回 2 个版本与问题、无 Jet 判断下正常生成并包含确认岗位性质问题（`has_jet_judgement=false`）、以及大模型失败（502/504）场景（`tests/api/test_assist_api.py`）〔执行：Antigravity〕
- [x] T019 [US1] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 C: 插件端（聊天读取、切换发现、侧边栏交互、复制核对、设置页）

**阶段目标**: 实现 Chrome 插件在聊天页的全部交互。仅在点生成时在 MAIN world 深度读取聊天与顶部完整职位名；基于顶部文字轻量监控聊天切换；侧边栏实现同意弹窗、结果卡片、类型切换、无判断标注与失败提示；复制时再次核对当前岗位 ID；设置页维护经历素材与撤回同意。末尾完成真实页面验证（P1–P5, G1–G10, L1–L3, V6, V7, V8, SC-013）。

- [x] T020 [P] [US1] 编写聊天页数据读取单元测试：使用真实脱敏聊天 JSON 样本构造模拟 Vue 组件，编写 `extension/tests/chat-reader.test.js`，测试 `readBossChatPage` 提取当前活跃会话的岗位 ID、公司名、城市、HR 姓名、页面顶部完整职位名（及 `jobName` 备用）、最多 30 条已加载非系统消息（保留 `bodyType=1` 与 `bodyType=7` 卡片文本，自动招呼语归我方，排除 `bodyType=16` 与 `item-system` 样式）（`extension/tests/chat-reader.test.js`）〔执行：Antigravity〕
- [x] T021 [US1] 实现聊天页 MAIN world 深度读取函数：在 `extension/src/page-reader.js` 中新增 `chat_page` 识别（`/web/geek/chat`）与自包含函数 `readBossChatPage()`，仅在点击生成时由 background 执行一次，读取当前活跃会话数据、顶部完整职位名与最近 30 条非系统消息，不发请求、不改 DOM、不向上滚动（`extension/src/page-reader.js`）〔执行：Antigravity〕
- [x] T022 [US1] 实现聊天会话轻量切换发现与状态分发：修改 `extension/src/content.js`，在聊天页由 MutationObserver 仅监听顶部 `.chat-position-content` 文字变化；变化时通知 `extension/src/background.js`，由 background 在 MAIN world 仅读取当前 `encryptJobId` 与上次对比，发生变化则分发 `chat_switched` 事件给侧边栏；非聊天页不分发聊天状态（`extension/src/content.js`, `extension/src/background.js`）〔执行：Antigravity〕
- [x] T023 [US2] 侧边栏增加数据发送知情同意弹窗/页面：在 `extension/src/sidepanel.html` 与 `extension/src/sidepanel.js` 中新增知情同意界面，展示发送字段清单（R1）与只读已脱敏文本预览；点击"取消"关闭弹窗不发请求；点击"同意"调用 `POST /v1/chat/consent` 并继续触发生成（`extension/src/sidepanel.html`, `extension/src/sidepanel.js`）〔执行：Antigravity〕
- [x] T024 [US1] 侧边栏 HR 沟通助手卡片骨架与生成调用：在 `extension/src/sidepanel.html` 与 `extension/src/sidepanel.js` 中增加沟通助手卡片，非聊天页隐藏生成按钮（FR-006）；聊天页展示"这是给 [公司] · [职位] 的建议"（FR-016）；无 Jet 判断显眼标注"这个岗位没有 Jet 判断"（FR-011）；点击生成依次调用预览与生成接口（点击后按钮置灰防连击）；错误按 R2 展示中文提示（`extension/src/sidepanel.html`, `extension/src/sidepanel.js`）〔执行：Antigravity〕
- [x] T024b [US1] 侧边栏生成结果展示：在 `extension/src/sidepanel.js` 与 `extension/src/sidepanel.html` 中展示判定类型与依据消息、手动切换重新生成按钮（FR-013）、2 个语气版本话术（≤ 50 字）与复制按钮、来源说明中的经历条目编号（不夹在话术里，FR-030）、最多 3 个问题（FR-014）、"根据已加载的 N 条消息生成"（FR-017）、"正在等 HR 回复"时只展示问题（FR-012）（`extension/src/sidepanel.html`, `extension/src/sidepanel.js`）〔执行：Antigravity〕
- [x] T025 [US1] 实现聊天切换立即清空与复制时核对岗位 ID：在 `extension/src/sidepanel.js` 中监听 `chat_switched` 事件，收到后 100% 立即清空内存中的旧生成结果与预览，提示"已切换到新的聊天，请重新生成"（FR-005, SC-007）；在用户点击话术"复制"按钮时，向 background 发送消息再次读取当前聊天的岗位 ID，若与当前生成结果绑定的岗位 ID 不一致，拒绝复制并提示"已切换到新的聊天，请重新生成"（FR-005, V8）（`extension/src/sidepanel.js`, `extension/src/background.js`）〔执行：Antigravity〕
- [x] T026 [US3] 设置页实现「我的经历素材」与「撤回同意」模块：修改 `extension/src/options.html` 与 `extension/src/options.js`，增加经历素材卡片（支持逐条添加、修改、删除，最多 10 条，单条 ≤ 200 字实时字数统计，调用 `/v1/experience`）；增加数据发送同意管理卡片，展示同意状态、时间、字段版本，提供"撤回同意"按钮调用 `/v1/chat/revoke-consent`，撤回后即时更新状态（`extension/src/options.html`, `extension/src/options.js`）〔执行：Antigravity〕
- [x] T027 [P] [US1] 编写侧边栏状态机与视图纯函数测试：新建 `extension/tests/chat-view.test.js`，测试侧边栏错误码映射（unpaired, consent_required, quota_exhausted, self_name_unavailable, all_suggestions_dropped, llm_failed）、判定类型中文展示、来源说明格式化、复制核对失败处理（`extension/tests/chat-view.test.js`）〔执行：Antigravity〕
- [x] T028 [US2] 【真实页面验证】步骤 P1–P5：在 BOSS 真实聊天页验证隐私与知情同意（撤回同意后点生成弹出同意页与预览 P1；核对脱敏内容 P2，重点完成 V6 我方姓名取自 `userInfo.name` 脱敏确认、V7 HR 请求卡片 `bodyType=7` 脱敏且 `bodyType=16` 与系统提示不发送确认；点击取消不发请求 P3；点击同意正常记录 P4；设置页撤回同意 P5；覆盖 SC-001, SC-002, SC-003, V6, V7）〔执行：用户〕（2026-09-28 P3、V6 通过，全部步骤完成）
  - 2026-09-27 记录：P1（同意页出现、未发请求）、P2（预览脱敏，V7 已为 FACT；V6 结论见 research）、P4（同意后生成）、P5（撤回后重新出现同意页）已在真实页面验证；**P3（点'取消'不发送）没有单独验证记录**，本任务暂不勾选。
- [x] T029 [US2] 【真实页面验证】核对并记录 P1–P5 验证结果，确认 V6、V7 证据状态已由 NOT VERIFIED 转为 FACT，记录实际表现（`specs/003-hr-assistant/research.md`）〔执行：Claude Code〕
- [x] T030 [US3] 【真实页面验证】步骤 G1–G7：在 BOSS 真实聊天页验证经历素材录入与不同沟通模式生成（设置页录入 2 条经历素材 G1；HR 未回复聊天生成开场白 G2；HR 最后说话聊天生成建议回复与问题 G3；我方最后说话聊天提示等 HR 回复 G4；无 Jet 判断岗位生成话术且包含岗位性质提问 G6；检查数据库 `llm_calls` 产生 `assist` 记录且无聊天正文 G7；覆盖 SC-004, SC-005, SC-006, FR-011, FR-012）〔执行：用户〕（2026-09-28 完成，G3 例外：G1、G2、G4、G6、G7 通过；G3 部分通过——引用上下文生效，被引用的问题不再重复；模型仍偶尔重复 HR 已回答的内容（如"无责任底薪3800"），作为已知局限，到 SC-013 真实使用时统计频率，频率高再评估换更强的模型）
  - 2026-09-27 记录：G3（建议回复）、G4（正在等 HR 回复，王先生）、G6（岗位不在库、无 Jet 判断）、G7（llm_calls 不含内容，Claude Code 只读核对）已验证；**G1（录入经历素材并被引用）、G2（HR 未回复时生成开场白）没有验证记录**（验证时经历素材为空），本任务暂不勾选。
- [x] T031 [US1] 【真实页面验证】步骤 G9–G10：在 BOSS 真实聊天页验证切换发现与复制防错核对（V8）（连续切换 5 个聊天验证侧边栏旧结果立即清空 G5、G9；在另一聊天中尝试复制旧结果验证被拒绝并提示重新生成 G10；确认 V8 切换发现机制可靠；覆盖 SC-007, V8, FR-005）〔执行：用户〕
- [x] T032 [US1] 【真实页面验证】步骤 G8（SC-013）：在 3 个真实 BOSS 聊天中实测生成话术由用户验收，检查判定类型准确性、至少 1 个版本可用性，并按真实效果提出语气规则调整意见（覆盖 SC-013）〔执行：用户验收；语气规则文件 `src/jet/llm/prompts/tone_rules.txt` 按用户意见由 Antigravity 修改〕
- [x] T033 [US1] 【真实页面验证】步骤 L1–L3：验证每日生成上限控制（在数据目录 `.env` 中设置 `JET_DAILY_ASSIST_LIMIT=1` 重启服务 L1；生成 1 次成功 L2；再次点击生成直接提示"今日生成次数已用完"且不发请求 L3；覆盖 SC-008）〔执行：用户〕
- [x] T034 [US1] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 C1: 阶段 C 补充（自动生成、不替用户做决定，2026-09-26 真实页面验证后）

**阶段目标**: 根据 2026-09-26 真实页面验证后的用户决定，完善话术约束与交互流程。服务端建立独立完成声称规则文件并核验丢弃声称完成某操作（如"已发送""已同意"）的话术（计入 `dropped_summary`）、在请求提示末尾追加"话术按你同意来写，不同意的话请自己回复"；发送字段版本升为 2，旧版本记录失效；侧边栏实现打开聊天自动生成与内存缓存（按"岗位 ID + 最后一条消息"键缓存，关闭侧边栏清空）、设置页增加"打开聊天自动生成"开关（默认开）、同意页增加"打开聊天就会自动发送"文案、每日次数用完提示与拦截；完成阶段 C1 真实页面验证与检查点。（2026-09-26 用户决定，真实页面验证后）

- [x] T064 [P] [US1] 编写完成声称规则文件与核验过滤单元测试：测试 `completed_action_rules.json` 规则加载；测试话术中含有"简历已同意发送""已通过附件发您""已投递""已上传"等声称完成操作的第一人称表述时被判定丢弃，丢弃原因 `claimed_action` 计入 `dropped_summary`；测试将要执行的表述（如"好的，我这就发您"）通过核验（`tests/unit/test_completed_action.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T065 [US1] 实现完成声称规则文件与大模型返回核验过滤：新建 `src/jet/llm/prompts/completed_action_rules.json` 存放完成声称关键词与句式规则；在 `src/jet/llm/assist.py` 中实现对返回建议话术的完成声称检查，对含完成声称的话术直接丢弃，原因计入 `dropped_summary`（不存话术正文）（`src/jet/llm/prompts/completed_action_rules.json`, `src/jet/llm/assist.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T066 [P] [US1] 编写请求提示追加句与同意回复 Prompt 单元测试：测试当 HR 请求卡片（`bodyType=7`）出现在我最后一条消息之后时，`request_notice` 文本末尾追加"话术按你同意来写，不同意的话请自己回复"；测试 Prompt 中明确要求模型遇到请求卡片时按同意将要执行来写回复（`tests/unit/test_request_notice.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T067 [US1] 实现请求提示追加文案与 Prompt 同意表述要求：修改 `src/jet/llm/assist.py`，更新 `request_notice` 生成逻辑追加"话术按你同意来写，不同意的话请自己回复"；在组织发往大模型的 Prompt 中加入明确指示，遇到 HR 请求卡片时回复按同意将要执行来写（`src/jet/llm/assist.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T068 [P] [US2] 编写同意字段版本升级至 2 及旧记录失效单元测试：测试 `CONSENT_FIELDS_VERSION` 升为 2；测试 `fields_version == 1` 的旧同意记录在 `has_valid_consent` 中判定失效返回 False；测试 `record_consent` 写入 `fields_version = 2`；测试失效状态下 `POST /v1/chat/generate` 拒绝并返回 403 `consent_required`（`tests/unit/test_consent_v2.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T069 [US2] 升级同意字段版本为 2 及更新相关接口：修改 `src/jet/domain/consent.py` 将 `CONSENT_FIELDS_VERSION` 升为 2；修改 `src/jet/api/routes.py` 更新 consent 路由的校验逻辑与版本默认值（`src/jet/domain/consent.py`, `src/jet/api/routes.py`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T070 [P] [US1] 编写侧边栏自动生成触发、内存缓存与次数用完提示单元测试：新建 `extension/tests/chat-auto-generate.test.js`，测试在侧边栏已打开且处于聊天页时自动调用生成；测试按"岗位 ID + 最后一条消息"键进行侧边栏内存缓存，缓存命中不重复请求大模型；测试有新消息时重新生成；测试切换聊天回显当前聊天缓存；测试侧边栏关闭时缓存清空；测试 429 `quota_exhausted` 时展示"今日生成次数已用完"且不重复自动请求（`extension/tests/chat-auto-generate.test.js`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T071 [US1] 实现侧边栏打开聊天自动生成、内存缓存与次数用完提示：修改 `extension/src/sidepanel.js` 与 `extension/src/sidepanel.html`，实现侧边栏打开且处于聊天页时自动触发生成；建立 `chatResultsCache` 内存缓存字典（键为岗位 ID + 最后一条消息），缓存命中直接回显上次结果；切换聊天展示当前聊天缓存或自动生成；今日次数用完时拦截自动请求并提示"今日生成次数已用完"；保留手动重新生成按钮（`extension/src/sidepanel.js`, `extension/src/sidepanel.html`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T072 [P] [US2] 编写设置页自动生成开关与同意页文案交互测试：新建 `extension/tests/auto-generate-settings.test.js`，测试设置页"打开聊天自动生成"开关渲染与切换（默认开，保存在 `chrome.storage.local`）；测试开关关闭时侧边栏不自动生成并展示生成按钮等待点击；测试同意页弹窗文案包含"打开聊天就会自动发送"并在点击同意时提交版本 2（`extension/tests/auto-generate-settings.test.js`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T073 [US2] 实现设置页自动生成开关与同意页文案更新：修改 `extension/src/options.html` 与 `extension/src/options.js`，新增"打开聊天自动生成"勾选开关（默认勾选开启）；修改 `extension/src/sidepanel.js` 与 `extension/src/sidepanel.html`，更新知情同意页面文案明确写明"打开聊天就会自动发送"，并在同意请求中传递 `fields_version = 2`；在侧边栏中根据开关状态控制是否自动触发生成（`extension/src/options.html`, `extension/src/options.js`, `extension/src/sidepanel.js`）〔执行：Antigravity〕（2026-09-26 用户决定，真实页面验证后）
- [x] T074 [US1] 【真实页面验证】步骤 N1–N6：在 BOSS 真实聊天页验证阶段 C1 增强功能（打开聊天验证自动触发生成与版本 2 重新同意 N1；核对同意页明确包含"打开聊天就会自动发送"文案 N2；连续切换不同聊天验证回显各聊天缓存、未产生新消息时 llm_calls 不重复增加 N3；在设置页关闭"打开聊天自动生成"后切回聊天验证不自动请求、等待手动点击生成 N4；遇到 HR 请求卡片验证提示末尾含"话术按你同意来写，不同意的话请自己回复"且话术按同意表述 N5；验证话术绝不出现"已发送""已同意"等已完成声称 N6；覆盖 SC-021 至 SC-025）〔执行：用户〕（2026-09-26 用户决定，真实页面验证后）
- [x] T075 [US1] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕（2026-09-26 用户决定，真实页面验证后）

---

## 阶段 C2: 工具栏图标打开侧边栏（P2，阶段 C 真实页面验证完成后）

**阶段目标**: 将点击 Chrome 工具栏 Jet 图标的默认行为改为打开侧边栏（`openPanelOnActionClick: true`），移除原点击图标打开岗位库的监听；在侧边栏中增加「我的岗位库」按钮，点击在新标签页打开 `myjobs.html`（改变 002 FR-045 岗位库入口为侧边栏按钮与设置页链接）；编写相关插件测试并完成真实页面验证。

- [x] T052 [P] [US10] 编写工具栏图标行为与侧边栏岗位库按钮交互测试：新建 `extension/tests/toolbar-action.test.js`，测试 background 初始化调用 `chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })` 且移除 `chrome.action.onClicked` 监听；测试侧边栏「我的岗位库」按钮点击时调用 `chrome.tabs.create` 打开 `src/myjobs.html`（`extension/tests/toolbar-action.test.js`）〔执行：Antigravity〕
- [x] T053 [US10] 实现 background 工具栏图标点击打开侧边栏配置：修改 `extension/src/background.js`，在扩展初始化时调用 `chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })`，移除原 `chrome.action.onClicked.addListener` 中直接在新标签页打开 `myjobs.html` 的监听（`extension/src/background.js`）〔执行：Antigravity〕
- [x] T054 [US10] 侧边栏增加「我的岗位库」按钮与打开交互：修改 `extension/src/sidepanel.html` 与 `extension/src/sidepanel.js`，在侧边栏操作栏增加「我的岗位库」按钮，点击时调用 `chrome.tabs.create({ url: chrome.runtime.getURL("src/myjobs.html") })` 在新标签页打开岗位库（`extension/src/sidepanel.html`, `extension/src/sidepanel.js`）〔执行：Antigravity〕
- [x] T055 [US10] 【真实页面验证】点击工具栏图标打开侧边栏与通过侧边栏按钮打开岗位库：在浏览器中点击工具栏 Jet 图标验证直接展开侧边栏（覆盖 US10, FR-061, SC-020）；在侧边栏点击「我的岗位库」按钮验证在新标签页打开 `myjobs.html`（覆盖 FR-062, SC-020）；验证设置页进入岗位库链接依然可用（覆盖 FR-063）〔执行：用户〕
- [x] T056 [US10] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 D: 岗位库服务端（P2 岗位库服务端）

**阶段目标**: 扩展服务端 `GET /v1/my-jobs` 接口，支持「全部岗位」（并集去重）、`hr_only` 过滤与各标签数量实时联动、包含所有历史版本职位名的 `q` 搜索（带 `%` 和 `_` 的 LIKE ESCAPE 转义）及按 `recent_updated_at` 倒序排序；服务端对来自岗位库（`source=myjobs`）的空 HR 记录提交严格返回 422。

- [x] T035 [P] [US6] 编写岗位库「全部岗位」、筛选联动、搜索与排序单元测试：编写 `tests/unit/test_myjobs_search.py`，测试 `filter=all_jobs` 为有状态 ∪ 判断过 ∪ 有 HR 记录并集去重总数（SC-009）；测试 `hr_only=true` 仅返回有非空 HR 记录岗位且 counts 联动更新；测试 `q` 搜索匹配职位名所有历史版本、公司名、HR 实际情况；测试 `%` 与 `_` 的 LIKE ESCAPE 转义；测试单次最多 200 条与 `total_matches`；测试按 `recent_updated_at` 倒序排列（`tests/unit/test_myjobs_search.py`）〔执行：Antigravity〕
- [x] T036 [P] [US5] 编写 HR 实际情况来源空提交拦截单元测试：编写测试验证 `PUT /v1/jobs/{platform_job_id}/hr-note` 当 `source='myjobs'` 且提交内容经 `strip()` 为空时返回 422 `empty_not_allowed`，提示"清空请到岗位卡片操作"；当 `source='card'` 时允许清空（`tests/api/test_hr_note_api.py`）〔执行：Antigravity〕
- [x] T037 [US6] 扩展 `list_my_jobs` 领域查询：修改 `src/jet/domain/job_status.py`，支持 `filter='all_jobs'` 并集查询；支持 `hr_only` 过滤与 counts 联动更新；实现 SQLite `LIKE` 特殊字符转义纯函数 `escape_like`；在搜索 `q` 时匹配 `job_versions` 全部历史版本的 `title`、`jobs.company_name` 和 `hr_notes.note`；计算单个岗位的 `recent_updated_at`（状态更新、HR 记录更新、详情查看、判断时间之最大值）并倒序排列；返回条目包含 `hr_note` 全文与 `total_matches`（`src/jet/domain/job_status.py`）〔执行：Antigravity〕
- [x] T038 [US5] 修改 HR 实际情况修改路由与参数校验：修改 `src/jet/api/routes.py` 中的 `PUT /v1/jobs/{platform_job_id}/hr-note` 接口，支持可选字段 `source`（`myjobs` / `card`）；当 `source == 'myjobs'` 且提交文本为空或纯空白时，拒绝并返回 422 错误 `empty_not_allowed`，禁止在岗位库清空；保持卡片端原有清空逻辑（`src/jet/api/routes.py`）〔执行：Antigravity〕
- [x] T039 [US6] 编写岗位库接口集成测试：在 `tests/api/test_myjobs_api.py` 中测试扩展后的 `GET /v1/my-jobs` 各参数组合（`all_jobs`、`hr_only`、`q` 转义与历史版本命中、`limit=200` 与 `total_matches`、倒序时间正确性）（`tests/api/test_myjobs_api.py`）〔执行：Antigravity〕
- [x] T040 [US6] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 E: 岗位库插件端（P2 岗位库插件端）

**阶段目标**: 重构插件独立标签页 `myjobs.html` 与视图组件。展示 HR 实际情况（两行自适应截断、展开全文 ≤ 200 字）、行内就地修改且禁止清空；新增「全部岗位」标签（悬停提示范围）与「只看有 HR 记录」复选框（联动数量）；搜索框带防抖与超过 200 条数量提示；行内直接修改状态并保留原位显示移出标注。末尾完成真实页面验证（M1–M9）。

- [x] T041 [P] [US5] 编写岗位库视图组件与行内交互单元测试：新建 `extension/tests/myjobs-row.test.js`，测试 HR 实际情况两行截断样式与展开全文渲染；测试修改输入框纯空白时保存按钮禁用逻辑；测试就地修改状态后的移出标注（"已改为 X，切换标签或刷新后移出本列表"）、改回原状态标注消失、取消状态标注移出的状态机逻辑（`extension/tests/myjobs-row.test.js`）〔执行：Antigravity〕
- [x] T042 [US5] 实现 HR 实际情况两行显示、展开全文与行内就地修改：修改 `extension/src/myjobs-view.js` 与 `extension/src/myjobs.html`，在每行卡片上直接展示 HR 实际情况（最多两行，CSS 自适应截断，点击"展开/收起"查看全文 ≤ 200 字，无记录显示"未记录"）；提供行内修改入口，编辑时纯空白禁用保存按钮并提示"清空请到岗位卡片操作"，保存时带 `source: 'myjobs'` 调用 `PUT /v1/jobs/{id}/hr-note`（`extension/src/myjobs-view.js`, `extension/src/myjobs.html`）〔执行：Antigravity〕
- [x] T043 [US6] 实现「全部岗位」标签与「只看有 HR 记录」勾选联动：修改 `extension/src/myjobs.html` 与 `extension/src/myjobs.js`，在标签栏最前面增加「全部岗位」标签按钮，悬停显示"有状态、判断过或记过 HR 说的话的岗位"；保留默认选中逻辑；新增「只看有 HR 记录」复选框，勾选后传 `hr_only: true` 重新拉取列表，并实时更新各标签旁数字为有 HR 记录的岗位数（`extension/src/myjobs.html`, `extension/src/myjobs.js`）〔执行：Antigravity〕
- [x] T044 [US7] 实现岗位库服务端搜索与超过 200 条数量提示：修改 `extension/src/myjobs.html` 与 `extension/src/myjobs.js`，在顶部添加搜索框，300ms 防抖后带 `q` 参数向服务端发起检索；当服务端返回 `total_matches > 200` 时，在列表上方展示提示卡片"只显示了前 200 条，共 N 条"（`extension/src/myjobs.html`, `extension/src/myjobs.js`）〔执行：Antigravity〕
- [x] T045 [US8] 实现岗位库行内直接改状态与原位移出标注：修改 `extension/src/myjobs.js` 与 `extension/src/myjobs-view.js`，在每行提供收藏/已投递/不考虑/取消状态按钮；点击修改状态后该行保留在原位不跳动，按钮变为新状态，并在行上标注"已改为 X，切换标签或刷新后移出本列表"，标签数量同步更新；若改回原状态标注消失；取消状态标注移出；「已标记」标签内互换状态不显示移出标注；切换标签或刷新后才移出（`extension/src/myjobs.js`, `extension/src/myjobs-view.js`）〔执行：Antigravity〕
- [x] T046 [US5] 【真实页面验证】步骤 M1–M5：在插件"我的岗位库"页面验证「全部岗位」标签、HR 实际情况展示与修改、以及「只看有 HR 记录」过滤（检查全部岗位标签及悬停说明 M1；检查 HR 实际情况两行展示与展开 M2；尝试清空或纯空格验证保存禁用 M3；修改新文本并保存确认落库 M4；勾选只看有 HR 记录验证过滤与数量联动 M5；覆盖 SC-009, SC-011）〔执行：用户〕（2026-09-27 部分通过：M1「全部岗位」排第一、默认仍是「已标记」、各标签带数量，M5 勾选"只看有 HR 记录"数量联动（全部岗位 3、收藏 1），M4 在岗位库修改 HR 实际情况并保存，M3 删空后保存按钮不可用并提示"清空请到岗位卡片操作"；未验证：M1 悬停说明，M2 两行显示与展开（且发现只有一行的内容也显示"展开"，待修））（2026-09-28 收尾：M1 悬停说明——`myjobs.html` 中 title 文字由自动测试 extension/tests/myjobs-row.test.js「T042-T045: myjobs.html 结构与组件标记合规检查」验证，悬停显示效果为日常使用中观察，不挡合并；M2 两行显示与展开已在 T079 真实页面验证通过）
- [x] T047 [US7] 【真实页面验证】步骤 M6–M9：在插件"我的岗位库"页面验证搜索、转义、200 条提示与行内改状态原位标注（搜索框输入包含 `%` 或 `_` 验证转义与历史职位名匹配、验证超过 200 条提示 M6；在收藏下改已投递验证留在原位并标注移出 M7；改回收藏验证标注消失 M8；取消状态验证标注移出 M9；覆盖 SC-010, SC-012）〔执行：用户〕（2026-09-27 部分通过：M6 搜"外包"返回"服务部生产主管"（HR 实际情况含"外包"）与"互联网产品运营-大厂外包"（职位名含"外包"），M7 在「收藏」把"动物实验技术员"改为"不考虑"行留在原位并显示"已改为 不考虑，切换标签或刷新后移出本列表"，收藏 3→2、不考虑 1→2；未验证：M6 中 %/_ 转义、历史职位名匹配、超过 200 条提示，M8 改回原状态标注消失，M9 取消状态标注移出）（2026-09-28 收尾：自动测试已验证——M6 %/_ 转义：tests/unit/test_myjobs_search.py::test_like_escape_percent_and_underscore、test_escape_like，tests/api/test_myjobs_api.py::test_api_get_my_jobs_like_escape；历史职位名匹配：test_myjobs_search.py::test_q_search_matching_historical_versions_company_hr_note、test_myjobs_api.py::test_api_get_my_jobs_search_q_historical_title_and_fields；超过 200 条提示：test_myjobs_search.py::test_limit_and_total_matches、test_myjobs_api.py::test_api_get_my_jobs_limit_and_total_matches、myjobs-row.test.js「T041: 搜索超量提示」；搜索词编码：myjobs-messages.test.js「buildMyJobsPath: q 非空时 encodeURIComponent 拼入 &q=」；M8 改回原状态标注消失：myjobs-row.test.js「T041: 状态机：改回原状态移出标注消失」；M9 取消状态出现移出标注：myjobs-row.test.js「T041: 状态机：取消状态生成移出标注」「「已标记」标签内互换状态不标注，取消状态标注移出」）
- [x] T048 [US5] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕
- [x] T076 [US5] 实现 HR 实际情况自动保存与按钮调整（a）：在 `extension/src/label-form.js` 中实现防抖、保存判断纯函数 `decideHrNoteAutoSave` 与防抖调度器 `createAutoSaveCoordinator`，在 `extension/src/content.js`（岗位卡片）与 `extension/src/myjobs.js`（岗位库）接入自动保存（停止输入约 1 秒或失焦自动保存，空内容不保存，同一时刻仅一个请求在途防止乱序覆盖）；卡片改为"清空 + 完成"按钮且空内容提示"内容为空不会自动保存；要清空请点「清空」"，保存成功保持编辑状态与光标；岗位库改为"完成"按钮且空内容提示"清空请到岗位卡片操作"；新建 `extension/tests/hr-note-autosave.test.js` 覆盖自动保存纯函数（`extension/src/label-form.js`, `extension/src/content.js`, `extension/src/myjobs.js`, `extension/tests/hr-note-autosave.test.js`）〔执行：Antigravity〕
- [x] T077 [US5] 实现岗位库"展开"实际溢出两行测量展示（b）：在 `extension/src/myjobs-view.js` 实现溢出判断纯函数 `isHrNoteOverflowing`（两行截断下 `scrollHeight > clientHeight + 1` 为溢出）；修改 `extension/src/myjobs.js` 在渲染后测量内容元素决定是否显示"展开"，使用 `ResizeObserver` 监听宽度变化重新判断，自动保存更新内容后重新判断；在 `extension/tests/hr-note-autosave.test.js` 中覆盖溢出判断测试（`extension/src/myjobs-view.js`, `extension/src/myjobs.js`, `extension/tests/hr-note-autosave.test.js`）〔执行：Antigravity〕
- [x] T078 [US9] 服务端 _job_entry 增加当前版本 title 并在响应中返回（c）：修改 `src/jet/api/routes.py` 中的 `_job_entry` 函数，增加 `"title"` 字段（取当前版本职位名，无则为 null），使 PUT hr-note 与 PUT status 响应中带当前职位名，解决岗位库操作后侧边栏显示 ID 问题；新建 `tests/api/test_job_entry_title.py` 覆盖 PUT hr-note 与 PUT status 响应中的 title（含多版本取当前版本）（`src/jet/api/routes.py`, `tests/api/test_job_entry_title.py`）〔执行：Antigravity〕
- [ ] T079 [US5] 【真实页面验证】卡片与岗位库自动保存、空内容不保存、一行不显示展开、岗位库操作后侧边栏显示职位名：在真实 BOSS 页面卡片与岗位库验证输入后 1 秒或失焦自动保存；验证输入为空时不自动保存并给出对应提示；验证卡片点"完成"保留原内容关闭、点"清空"正常清空；验证岗位库一行内容不显示"展开"且多行内容正常展开收起、缩放窗口自适应；验证岗位库改状态或保存 HR 记录后侧边栏显示职位名而非岗位 ID（覆盖 FR-070）〔执行：用户〕（2026-09-27 部分通过：卡片与岗位库自动保存、空内容不保存及提示、岗位库"完成"、"展开"只在超过两行时出现并随窗口宽度变化、BOSS 页面侧边栏显示职位名；岗位库页面侧边栏按新决定显示"未打开岗位详情"且改状态后不变；未验证：卡片点"完成"保留原内容关闭、点"清空"正常清空）（2026-09-28 核对：卡片"完成/清空"的核心逻辑有自动测试——完成：hr-note-autosave.test.js「createAutoSaveCoordinator: handleComplete 保存未保存变动或保留原内容」及 hr-note-autosave-sync.test.js 对 content.js 副本的同类用例；清空：tests/api/test_hr_note_api.py::test_put_hr_note_card_empty_allowed、test_put_hr_note_default_source_is_card，extension/tests/chat-job-status.test.js「buildHrNotePayload: 放行 source=…」；未覆盖：content.js 中"完成""清空"按钮的事件接线（点击后关闭编辑框、清空时取消自动保存并发送清空），以及"自动保存请求在途时点清空"的先后顺序，待用户决定）（2026-09-28 用户决定：时序问题不修，作为已知低风险问题记录——只影响卡片显示，刷新即恢复，触发条件苛刻；日常使用中顺手验证"完成/清空"后再勾选本任务，不挡合并） **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）
- [x] T080 background 仅对 BOSS 页面消息读取/创建/修改标签页"当前岗位"状态（isBossPageSender，extension/src/scheduler.js、extension/src/background.js，测试 extension/tests/tab-state-scope.test.js）〔执行：Antigravity〕
- [x] T081 修复 content.js 缺失的 `}` 并新增 extension/tests/syntax-check.test.js（逐文件 node --check + content_scripts 普通脚本解析）〔执行：Antigravity〕
- [x] T082 切换聊天时立即遮盖旧结果、重试改为每 200ms、先广播后查岗位库、误报恢复原显示与按钮原状态（FR-071；extension/src/content.js、background.js、chat-view.js、sidepanel.js、sidepanel.html；测试 extension/tests/chat-switch-mask.test.js、chat-switch.test.js）〔执行：Antigravity〕
- [ ] T083 切换聊天时立即显示"正在识别当前聊天…"、确认后显示新内容、误报恢复原显示、生成中遇到遮盖后生成按钮不会被重新启用〔执行：用户〕（2026-09-28 部分通过：立即遮盖、1～3 秒后显示新内容、来回切换 3～4 次正常、停留 20 秒没有误遮；未验证：误报时恢复原显示、生成中遇到遮盖后生成按钮不会被重新启用） **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）

---

## 阶段 E2: 聊天页关联岗位库（P2，复用阶段 E 组件）

**阶段目标**: 在 BOSS 聊天页侧边栏实现当前聊天岗位与本机公共岗位库的关联。服务端 HR 实际情况保存接口新增来源"聊天页侧边栏"（`source='chat_sidebar'`）且同样拒绝空提交（422）；background 依据当前聊天岗位 ID（`encryptJobId`，只读查询、不改状态、不触发判断）查询岗位库已有数据并分发给侧边栏；侧边栏实现当前岗位区块（展示 Jet 结论与 HR 实际情况，复用阶段 E 的就地修改组件且禁止清空；不在库时展示提示原文；切换聊天随之更新）。编写插件测试并完成真实页面验证。

- [x] T057 [P] [US9] 编写服务端 HR 实际情况来源"聊天页侧边栏"空提交拦截单元测试：在 `tests/api/test_hr_note_api.py` 中增加测试，验证 `PUT /v1/jobs/{platform_job_id}/hr-note` 当 `source='chat_sidebar'`（聊天页侧边栏）且提交内容经 `strip()` 为空时返回 422 `empty_not_allowed`，拒绝清空并提示"清空请到岗位卡片操作"；非空内容正常保存并记录（`tests/api/test_hr_note_api.py`）〔执行：Antigravity〕
- [x] T058 [US9] 服务端 HR 实际情况接口支持"聊天页侧边栏"来源校验：修改 `src/jet/api/routes.py`，在 `PUT /v1/jobs/{platform_job_id}/hr-note` 接口的 `source` 合法值中增加 `'chat_sidebar'`（来源：聊天页侧边栏）；当 `source == 'chat_sidebar'` 且提交文本经 `strip()` 为空时，返回 422 错误 `empty_not_allowed` 拒绝清空；非空内容保存并更新（`src/jet/api/routes.py`）〔执行：Antigravity〕
- [x] T059 [P] [US9] 编写聊天页当前岗位查询与侧边栏岗位区块单元测试：新建 `extension/tests/chat-job-status.test.js`，测试 background 按当前聊天岗位 ID（`encryptJobId`）只读查询本机岗位库已有数据；测试侧边栏当前岗位区块展示 Jet 结论与 HR 实际情况；测试不在库时显示提示原文；测试就地修改禁止清空的前端交互与数据分发（`extension/tests/chat-job-status.test.js`）〔执行：Antigravity〕
- [x] T060 [US9] 实现 background 依据当前聊天岗位 ID 查询岗位库已有数据：修改 `extension/src/background.js`，在当前聊天会话建立或切换时，以当前 `encryptJobId` 向本机 Jet 接口（只读查询，沿用不会触发判断的现有读取方式，不改状态、不触发判断）查询该岗位数据，将结果包含在聊天状态中分发给侧边栏（`extension/src/background.js`）〔执行：Antigravity〕
- [x] T061 [US9] 侧边栏实现当前岗位区块展示与就地修改（复用阶段 E 组件）：修改 `extension/src/sidepanel.html` 与 `extension/src/sidepanel.js`，新增当前岗位区块；在库时展示 Jet 结论与 HR 实际情况，复用阶段 E 就地修改组件（带 `source: 'chat_sidebar'` 保存，空文本禁用保存）；不在库时显示提示原文"岗位库里没有这个岗位。在 BOSS 搜索列表里看到它时，Jet 会记录并判断"；切换聊天时自动刷新该区块（`extension/src/sidepanel.html`, `extension/src/sidepanel.js`）〔执行：Antigravity〕
- [ ] T062 [US9] 【真实页面验证】在 BOSS 真实聊天页验证关联岗位库与就地修改（前提：需要一个已在 BOSS 搜索列表里看过、因此已入库，并且和 HR 聊过的岗位；2026-09-26 只读查证时现有聊天的岗位均不在岗位库中）：在已入库岗位聊天中打开侧边栏，验证显示 Jet 结论与 HR 实际情况，验证与岗位库数据一致（覆盖 SC-016）；在侧边栏就地修改 HR 实际情况并尝试清空验证前后端拦截（覆盖 SC-017）；在未入库岗位聊天中验证展示不在库提示原文且不入库不判断（覆盖 SC-018）；连续切换聊天验证该区块随之更新（覆盖 SC-019）〔执行：用户〕（2026-09-28 部分通过："不在库"提示已通过（赵女士 · 某科技公司 · 带薪实习生）；"在库"未验证：现有聊天的岗位在 BOSS 搜索里都搜不到、无法入库，待下次从搜索列表点"立即沟通"开聊时顺带验证；切换聊天随之更新的遮盖效果待验证） **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）
- [x] T063 [US9] 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕

---

## 阶段 F: 收尾与全量测试

**阶段目标**: 更新相关项目文档与配置说明，执行全量自动化回归测试，确认端到端功能完全就绪后由 Claude Code 进行最终提交。

- [x] T049 [P] 更新项目文档：更新 `README.md`，增加 003 HR 沟通助手功能简介、配置项说明（`JET_DAILY_ASSIST_LIMIT`）、以及我的岗位库改版说明（`README.md`）〔执行：Antigravity〕
- [x] T050 运行全量自动化测试与回归检查：运行全部 Python 测试（`uv run --frozen pytest tests/`）与全部插件测试（`node --test extension/tests/*.test.js`），确保全绿且无任何回归（`tests/`, `extension/tests/`）〔执行：Antigravity（放行的 uv run --frozen pytest 与 node --test extension/tests/*.test.js）〕
- [x] T051 检查点：运行全部测试（uv run --frozen pytest；插件测试 node --test extension/tests/*.test.js），停下来等用户确认后提交〔执行：Claude Code〕
- [x] T084〔执行：Antigravity〕：更新 specs/003-hr-assistant/quickstart.md 中写"点生成"的步骤（G2、G3、G4、G6、L2），说明默认打开聊天自动生成，只有关闭"打开聊天自动生成"后才需手动点生成（2026-09-28 完成：由 Claude Code 直接修改 G2、G3、G4、G6、L2 与 3.2 节说明）

---

## 阶段 G: 003 后续修复（2026-09-28 用户决定，分支 `fix/003-followups`）

**阶段目标**: 修复"聊天中出现新消息后侧边栏不更新 / 仍显示正在等 HR 回复"（FR-004 修订、FR-074、SC-026），以及岗位卡片偶发一直停在"判断中"（002 插件端查询结果的定时器）。每个问题单独提交。

- [x] T085 修订 `spec.md`：FR-004（发现新消息时本机重新读取）、新增 FR-074（新消息与切回有新消息的聊天的处理规则）、SC-026，并同步 FR-005、FR-065、FR-066、SC-007、失败提示表、US1 场景 6、边界情况（`specs/003-hr-assistant/spec.md`）〔执行：Claude Code〕
- [x] T086 岗位卡片停在"判断中"：后端回复后若当前岗位已换，不再渲染旧岗位、不再停掉新岗位的查询；显示"判断中"但没有查询在跑时自动恢复查询（`extension/src/background.js`、`extension/src/scheduler.js`、`extension/tests/`）〔执行：Antigravity〕
- [x] T087 [US1] 按 FR-074 发现当前聊天的新消息：内容脚本只比较消息条数与最后一条并通知侧边栏；侧边栏本机重新读取并调用 `/v1/chat/preview` 更新判定类型与依据，按开关与最新发言方决定是否自动重新生成；切回有新消息的聊天按同一规则（`extension/src/content.js`、`extension/src/sidepanel.js`、`extension/src/chat-view.js`、`extension/tests/`）〔执行：Antigravity〕
- [ ] T088 【真实页面验证】SC-026：同一聊天我发消息、HR 发消息、开关关闭三种情况；列表页连续快速切换岗位后卡片不停在"判断中"〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）
