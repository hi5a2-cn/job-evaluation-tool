# Tasks: 岗位信息跨页面联通（第一批） (005-cross-page-job-link)

**Input**: Design documents from `/specs/005-cross-page-job-link/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`contracts/page-behavior.md`、`quickstart.md`）

**Prerequisites**: main 已合入 004（`5b7c25d`）。

**Tests**: 包含自动化测试与真实页面验证。测试遵循宪法原则 VIII（不联网、不需 Key、不开浏览器、不写真实数据目录）；插件端 DOM 测试用手写 `MockElement`（参照 `extension/tests/chat-reader.test.js`），样本只含结构、不含真实页面文字。

**Organization**: 阶段 1 共用基础（没有版本的岗位也能安全合并）→ 阶段 2 US1 侧边栏投递状态（P1）→ 阶段 3 US2 列表卡片投递状态（P1）→ 阶段 4 US3 独立职位页自动标已投递（P2）→ 阶段 5 US4 聊天岗位自动入库（P2）→ 阶段 6 收尾。每个阶段末尾有检查点（全量测试、Codex 审稿、提交）。

## 任务格式规范

`- [ ] T001 [P] [Story] 任务说明（含文件路径） 〔执行：执行者〕`
- **[P]**: 可并行任务（与其他任务无文件冲突且不依赖前置任务产物）
- **[Story]**: 所属用户故事（US1–US4，对应 `spec.md`）
- **执行者标注**:
  - `〔执行：Antigravity〕`: 写代码、写测试、跑测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）
  - `〔执行：Claude Code〕`: 审核 diff、Codex 审稿、Git 提交、重启 jet serve、真实页面验证协调
  - `〔执行：用户〕`: 真实 BOSS 页面上的手工操作与验收

**所有写代码任务的共同硬性要求**（写进每次委托）：生产代码不含针对测试的特殊处理；不全局替换标准库或第三方行为；不删除、不放宽已有测试断言（确需改已有测试须逐条说明）；保持现有代码风格与中文注释密度；发给大模型的内容不变（FR-020）；不向 BOSS 发任何请求、不自动点击或导航（FR-019）。

---

## 阶段目标与依赖关系

```text
阶段 1: 无版本岗位安全合并 ──────────────────────────────┐
阶段 2: US1 侧边栏状态（P1）─┐                           │
阶段 3: US2 列表卡片状态（P1）┼──（互不依赖）             ├──> 阶段 5: US4 聊天岗位入库（P2）──> 阶段 6: 收尾
阶段 4: US3 独立职位页（P2）─┘                           │
                                                          └── US4 的侧边栏卡片复用阶段 2 的状态按钮组
```

---

## 阶段 1: 共用基础（没有版本的岗位也能安全合并）

**阶段目标**: 允许 `jobs` 中存在 `current_version_id` 为 NULL 的岗位（US4 聊天入库产生），之后在列表或详情遇到时安全补录第 1 版（research 第二节 #3、#4）。现有行为不变。

- [x] T001 新增 `job_chat_seen` 表到 `src/jet/db/schema.sql`（`CREATE TABLE IF NOT EXISTS`；列 `user_id TEXT NOT NULL REFERENCES users(id)`、`job_id INTEGER NOT NULL REFERENCES jobs(id)`、`chat_title TEXT NOT NULL`、`first_seen_at TEXT NOT NULL`、`last_seen_at TEXT NOT NULL`、`PRIMARY KEY(user_id, job_id)`）；确认 `src/jet/db/store.py` 的 `_ensure_current_schema` 对已有数据库也会建表（必要时按现有迁移写法补上），user_version 不变或按现有规则处理〔执行：Antigravity〕
- [x] T002 修改 `src/jet/domain/jobs.py` 的 `ingest()`：已有岗位 `current_version_id` 为 NULL 时，列表分支与详情分支都按"首个版本"处理（插入 `version_no = 1`、`source` 分别为 `'list'`/`'detail'`，回填 `jobs.current_version_id`；详情分支同时置 `completeness='full'`），其余合并规则不变〔执行：Antigravity〕
- [x] T003 [P] 测试：`tests/unit/test_store.py` 或 `tests/unit/test_migrations.py` 覆盖新表在新库与已有库都存在；`tests/api/test_observations_list.py`、`tests/api/test_observations_detail.py`（或新文件）覆盖"先有无版本岗位行 → 列表观测补录第 1 版""→ 详情观测补录第 1 版并置 full、正常排队判断"〔执行：Antigravity〕
- [x] T004 检查点：`uv run --frozen pytest`、`node --test extension/tests/*.test.js`、`node --check` 全部通过；审核 diff；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 2: US1 聊天页侧边栏显示并修改投递状态（P1）

**阶段目标**: 侧边栏当前岗位卡片显示收藏/已投递/不考虑按钮组，可切换与取消，复用 `PUT /v1/jobs/{platform_job_id}/status` 与 background 现有 `set_job_status` 消息（FR-001–FR-004）。

**Independent Test**: 在库岗位的聊天中，侧边栏显示状态按钮；点击后岗位库显示同一状态；再点取消。

- [x] T005 [US1] `extension/src/chat-view.js`：新增纯函数，根据当前岗位条目 `my_status` 与点击的状态计算下一状态（再点已选中的即取消为 null），以及按钮组展示数据（三个按钮、选中项）；`formatChatJobStatus` 输出保留 `my_status`〔执行：Antigravity〕
- [x] T006 [US1] `extension/src/sidepanel.js`、`extension/src/sidepanel.html`：当前岗位卡片渲染状态按钮组；点击经 background 现有 `set_job_status` 消息保存；保存中禁用按钮防重复；失败恢复原选中态并提示；成功后更新本地岗位条目；响应到达时若当前聊天岗位 ID 已变则丢弃（沿用 003 防乱序纯函数）〔执行：Antigravity〕
- [x] T007 [US1] `extension/src/background.js`：若 `set_job_status` 目前只接受来自内容脚本的消息或依赖 tab 信息，补齐侧边栏调用路径；保存成功后更新该标签页 `tabState.chatJobStatus` 中的 `my_status`，保证切走再切回显示新状态〔执行：Antigravity〕
- [x] T008 [P] [US1] 测试：`extension/tests/chat-job-status.test.js` 或新文件覆盖下一状态计算（设置、切换、取消）、按钮组展示数据、乱序响应丢弃〔执行：Antigravity〕
- [x] T009 检查点：全量测试通过；审核 diff；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 3: US2 搜索列表卡片显示投递状态（P1）

**阶段目标**: 列表卡片显示"收藏""已投递""不考虑"标识；已有结论标签与粗筛提示保持 004 原位置不动，状态标识排在其右侧（间距 4px），没有结论/提示标签时放在 `rect.left + 8`；空间不足时截断状态标识文字、不覆盖已有标签；仅有状态的卡片不新增色条；同页状态修改后 1 秒内更新（FR-005–FR-008，data-model 列表标记部分）。

**Independent Test**: 岗位库设好状态后打开搜索列表页，核对标识；在右侧详情区改状态，卡片标识随之变化。

- [x] T010 [US2] `extension/src/page-summary.js`：从 `listJudgements` 条目已有的 `my_status` 生成 `status_key`（`"saved" | "applied" | "skipped" | null`）与 `status_label`（`"收藏" | "已投递" | "不考虑" | null`）；无状态为 null；不因此触发判断或粗筛〔执行：Antigravity〕
- [x] T011 [US2] `extension/src/marks-layout.js`、`extension/src/content.js`：按阶段目标的布局规则渲染状态标识；已有结论/粗筛标签的位置与宽度计算不得改变〔执行：Antigravity〕
- [x] T012 [US2] `extension/src/content.js`：所有修改 `currentDetail.my_status` 的位置（手动改状态成功、「立即沟通」自动标记成功、撤销成功，research 所列约 2281、4033、4152 行附近）同时更新列表数据中该岗位的 `my_status` 并重绘该卡片标识〔执行：Antigravity〕
- [x] T013 [P] [US2] 测试：`extension/tests/page-summary.test.js`、`extension/tests/marks.test.js`（或新文件）覆盖状态字段生成、无状态不显示、与结论/粗筛标签共存且已有标签位置不变〔执行：Antigravity〕
- [x] T014 检查点：全量测试通过；审核 diff（重点确认已有 004 标记断言未被改动）；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 4: US3 独立职位页点「立即沟通」自动标记已投递（P2）

**阶段目标**: 把 002 FR-058 的行为扩展到 `/job_detail/` 页面（FR-009、FR-010）。

**Independent Test**: 状态未设置的独立职位页上点「立即沟通」，出现可撤销提示，岗位库变为已投递。

- [x] T015 [US3] `extension/src/auto-apply.js`：`decideAutoApply` 允许 `/web/geek/job` 与 `/job_detail/` 两类路径；独立职位页要求读取到的岗位 ID 与地址 `/job_detail/<id>.html` 中的 ID 一致，否则 skip；读取失败（无岗位 ID）skip；按钮文字 trim 后恰好为"立即沟通"；`ka` 存在且不含该 ID 时 skip；已是 applied 时 skip〔执行：Antigravity〕
- [x] T016 [US3] `extension/src/content.js`：独立职位页挂同样的捕获阶段 passive 点击监听，按钮识别用"最近的 a/button 且文字 trim 后恰好为立即沟通"；岗位 ID 取 004 job_detail 读取结果；保存经 background `set_job_status`（页面卸载不中断）；复用现有 8 秒撤销提示与失败提示〔执行：Antigravity〕
- [x] T017 [P] [US3] 测试：`extension/tests/auto-apply.test.js` 覆盖独立职位页路径通过、地址 ID 不一致 skip、读取失败 skip、"继续沟通" skip、ka 不含 ID skip、已投递 skip；搜索列表页原有用例全部保留〔执行：Antigravity〕
- [x] T018 检查点：全量测试通过；审核 diff；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 5: US4 聊天中的岗位自动入库（P2）

**阶段目标**: 打开聊天会话即在本机入库岗位 ID、职位名、公司名；不判断、不粗筛、不调外部模型、不设状态；计入"全部岗位"；侧边栏提示"点「查看职位」获取详情并判断"（FR-011–FR-017）。

**Independent Test**: 打开一个库里没有的聊天岗位，侧边栏显示职位名、公司名、状态按钮与提示；岗位库"全部岗位"出现该岗位；大模型调用记录与额度不变。

- [x] T019 [US4] `src/jet/api/routes.py`：新增 `POST /v1/chat/job`（body `{platform_job_id, title, company_name}`，鉴权同现有接口；`platform_job_id` 或 `title` trim 后为空返回 422）；岗位不存在时插入 `jobs`（`completeness='list_only'`、`current_version_id=NULL`、`company_name` 为空则 NULL、`first_seen_at`/`last_seen_at`=now），不写 `job_versions`；岗位已存在时不改已有列（仅 `company_name` 为 NULL 时补上）；upsert `job_chat_seen`（首次插入，之后只更新 `last_seen_at`，`chat_title` 仅为空时补）；幂等；返回与 `/v1/judgements` 相同结构的岗位条目；绝不调用 `request_judgement`、`rejudge`、规则粗筛或任何 LLM，不写 `job_status`〔执行：Antigravity〕
- [x] T020 [US4] `src/jet/api/routes.py` 的 `_job_entry`：`current_version_id` 为 NULL 时 `title` 取 `job_chat_seen.chat_title`；新增布尔字段 `seen_in_chat`〔执行：Antigravity〕
- [x] T021 [US4] `src/jet/domain/job_status.py`：`all_jobs` 口径（计数与列表）= 有状态 ∪ 判断过 ∪ 非空 HR 记录 ∪ `job_chat_seen` 中该用户的岗位；列表条目 `current_version_id` 为 NULL 时标题取 `chat_title`、薪资与城市留空；搜索 `q` 同时匹配 `chat_title`〔执行：Antigravity〕
- [x] T022 [US4] `extension/src/page-reader.js`：聊天页轻量读取在返回 `encrypt_job_id` 的同时返回职位名（先取页面数据里与 `encrypt_job_id` 同一对象的 `jobName`，为空时才读顶部岗位栏 `.chat-position-content .position-name`；2026-10-06 按代码修订，原写法顺序相反）与公司名（`brandName`），读法与 `readBossChatPage` 现有写法一致；不读其他字段〔执行：Antigravity〕
- [x] T023 [US4] `extension/src/background.js`：聊天切换检测广播时（research 所列约 1268 行附近），已配对且读到岗位 ID 与非空职位名时，先调用 `POST /v1/chat/job`（jet-client 新增方法），再按现有方式取岗位条目；未配对或服务不可用时不入库、不重试；侧边栏关闭时也执行〔执行：Antigravity〕
- [x] T024 [US4] `extension/src/chat-view.js`：`isJobInLibrary` 增加 `seen_in_chat`；在库但无判断时 `notice` 为原文"点「查看职位」获取详情并判断"；`extension/src/sidepanel.js` 在该状态下仍显示职位名、公司名、状态按钮组（阶段 2）与 HR 实际情况编辑区〔执行：Antigravity〕
- [x] T025 [US4] `extension/src/myjobs.js`（及 `extension/src/myjobs-view.js` 如需要）：未判断岗位显示"尚未判断"，薪资、城市为空时不显示占位错误〔执行：Antigravity〕（2026-09-29 Claude 审核：岗位库已有"未判断"标识与空薪资占位，改动会波及所有未判断岗位，撤回，沿用现有显示）
- [x] T026 [P] [US4] 后端测试：新文件 `tests/api/test_chat_job.py` 覆盖新岗位入库、已有岗位不覆盖（标题、公司名、判断、状态不变）、`company_name` 为 NULL 时补上、幂等无重复、422、鉴权、调用后 `judgements` 与 `llm_calls` 行数不变且不写 `job_status`；`tests/api/test_my_jobs.py` 或 `tests/unit/test_job_status.py` 覆盖 `all_jobs` 计入聊天岗位、标题回退、`q` 匹配 `chat_title`；`tests/api/test_job_entry_title.py` 覆盖 `seen_in_chat` 与标题回退〔执行：Antigravity〕
- [x] T027 [P] [US4] 插件测试：覆盖轻量读取返回职位名与公司名（MockElement）、`isJobInLibrary` 的 `seen_in_chat`、在库未判断提示原文、background 入库调用条件（纯函数部分）〔执行：Antigravity〕
- [x] T028 检查点：全量测试通过；审核 diff；Codex 审稿；提交；重启 jet serve 并确认 `serve.log` 无异常〔执行：Claude Code〕

---

## 阶段 6: 收尾

- [x] T029 [P] `README.md`：补充 005 的四项行为说明（与 004 的写法一致）〔执行：Antigravity〕
- [x] T030 检查点：全量测试通过；提交；按 `quickstart.md` 整理真实页面验证步骤交给用户〔执行：Claude Code〕
- [ ] T031 真实页面验证（quickstart 各项）〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）

---

## 并行机会

- 阶段 2、3、4 互不依赖，可分开进行；为保证每阶段单独提交，按顺序执行。
- 各阶段内标 [P] 的测试任务可与实现任务同批委托。

## 实施策略

- MVP：阶段 2（US1）。其后依次阶段 3、4、5；每个阶段独立可测、单独提交。
- 阶段 5 改服务端，提交后重启 jet serve；本功能不改变发往外部模型的内容，无需另行征求外发同意（FR-020）。
