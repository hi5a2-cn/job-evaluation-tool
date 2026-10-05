# Tasks: 判断时建议投哪份简历 (006-resume-suggestion)

**Input**: Design documents from `/specs/006-resume-suggestion/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`contracts/llm-output.md`、`quickstart.md`）

**Prerequisites**: main 已合入 005（`db94f1f`）。

**Tests**: 包含自动化测试与真实页面验证。测试遵循宪法原则 VIII（不联网、不需 Key、不开浏览器、不写真实数据目录）；大模型调用一律用现有测试替身，不发真实请求。

**Organization**: 阶段 1 基础（配置、存储、过时基准、API 输出）→ 阶段 2 提示词 v6 与输出解析（发往模型的内容在此阶段变化，用户已于 2026-09-29 同意）→ 阶段 3 US1 卡片与侧边栏显示 → 阶段 4 US3 HR 要简历时突出提示 → 阶段 5 收尾。每阶段末尾检查点：全量测试、Codex 审稿、提交；改服务端的阶段提交后 `launchctl kickstart -k` 重启。

## 任务格式规范

`- [ ] T001 [P] [Story] 任务说明（含文件路径） 〔执行：执行者〕`
- `〔执行：Antigravity〕`: 写代码、写测试、跑测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）
- `〔执行：Claude Code〕`: 审核 diff、Codex 审稿、Git 提交、重启服务、真实页面验证协调
- `〔执行：用户〕`: 真实页面手工验收

**所有写代码任务的共同硬性要求**：生产代码不含针对测试的特殊处理；不全局替换标准库或第三方行为；不删除、不放宽已有测试断言（确需改须逐条说明）；不创建别名导出或参数重载；发往大模型的新增内容只能是方向代号、方向名、适合的岗位类型（FR-003），绝不含文件名、姓名或简历内容。

---

## 阶段 1: 基础（配置、存储、过时基准、API 输出）

**阶段目标**: 简历方向配置可读；判断表能存建议；v5 及以后判断不再因版本被标过时；API 能输出建议。本阶段不改提示词，发往模型的内容不变。

- [x] T001 新建 `src/jet/llm/prompts/resume_directions.json`（内容按 data-model.md：三个方向 `ai_ops`、`data_support`、`animal_life`，每项 `key`、`name`、`file_name`、`job_types`）；新建 `src/jet/domain/resume.py`，仿 `src/jet/domain/industry.py` 读取：文件缺失、格式错误或有效方向少于 2 个时返回空列表并记录一次警告；提供按 key 查方向的函数〔执行：Antigravity〕
- [x] T002 `src/jet/db/schema.sql` 与 `src/jet/db/store.py`：`judgements` 新增 `resume_direction TEXT`、`resume_reason TEXT`（按现有 ALTER TABLE 迁移写法，已有库自动补列）〔执行：Antigravity〕
- [x] T003 `src/jet/domain/judgements.py`：新增常量 `STALE_METHOD_BASELINE = "v5"`；`is_method_changed` 改为"判断的提示词版本早于基准 v5 才算方法变了"；v4 及更早判断行为不变；不改任何自动重判触发条件以外的逻辑〔执行：Antigravity〕
- [x] T004 `src/jet/domain/judgements.py` 的 `to_api`：输出新增 `resume_suggestion`——两列有效且 key 在当前配置中时为 `{"direction","name","file_name","reason"}`，否则 `null`〔执行：Antigravity〕
- [x] T005 [P] 测试：配置读取（正常、缺失、格式错、方向不足）；迁移补列；`is_method_changed` 在当前配置 v6 下对 v5/v6 返回 False、对 v4 返回 True；`to_api` 的 `resume_suggestion`（有效、无效 key、空理由、旧判断无列值）〔执行：Antigravity〕
- [x] T006 检查点：全量测试通过；审核 diff；Codex 审稿；提交；`launchctl kickstart -k` 重启并健康检查〔执行：Claude Code〕

---

## 阶段 2: 提示词 v6 与输出解析

**阶段目标**: 新判断在同一次调用里给出简历建议并保存；默认提示词版本改为 v6（FR-002–FR-005、FR-010）。

- [x] T007 新建 `src/jet/llm/prompt_v6.py`：复用 v5 的消息构造，追加"简历方向"说明段（只含每个方向的 `key`、`name`、`job_types`）与输出字段 `resume_suggestion: {"direction": <key>, "reason": <一句话>}` 的要求；配置为空时生成的内容与 v5 完全相同〔执行：Antigravity〕
- [x] T008 `src/jet/llm/client.py`：版本分派加入 v6（research 所列约 163-173、234-295 行），v6 输出解析复用 v5 并额外解析 `resume_suggestion`：`direction` 必须是当前配置中的 key、`reason` 非空（按现有 `verdict_reason` 的长度处理截断），否则视为无建议；解析失败不影响判断其余部分〔执行：Antigravity〕
- [x] T009 判断保存处（worker / `judgements.py` 写入 LLM 判断的位置）：把解析出的建议写入 `resume_direction`、`resume_reason`；规则判断不写〔执行：Antigravity〕
- [x] T010 `src/jet/config.py`：默认 `prompt_version` 改为 `"v6"`（环境变量 `JET_PROMPT_VERSION` 仍可覆盖）〔执行：Antigravity〕
- [x] T011 [P] 测试：v6 消息中含三个方向的 key/name/job_types、不含任何 `file_name` 与"（用户姓名）"；配置为空时 v6 消息与 v5 一致；输出解析的有效/无效 key/空理由/缺字段；保存后 `to_api` 输出建议；判断调用次数不变（一次判断一次调用）；v5 旧判断在 v6 配置下不过时、不触发自动重判〔执行：Antigravity〕
- [x] T012 检查点：全量测试通过；审核 diff（重点逐字核对 v6 相对 v5 的提示词差异）；Codex 审稿；提交；重启并健康检查〔执行：Claude Code〕

---

## 阶段 3: US1 岗位卡片与侧边栏显示

- [x] T013 [US1] `extension/src/content.js`（搜索列表右侧详情与独立职位页共用的岗位卡片）：结论不是"不建议投"且 `judgement.resume_suggestion` 非空时显示"建议投：<file_name>"，理由用悬停提示并可点击展开；否则不显示〔执行：Antigravity〕
- [x] T014 [US1] `extension/src/chat-view.js` 的 `formatChatJobStatus` 输出 `resume_suggestion`（同样的显示条件，写成可测纯函数）；`extension/src/sidepanel.js`、`sidepanel.html` 在「当前岗位」卡片显示〔执行：Antigravity〕
- [x] T015 [P] [US1] 测试：显示条件纯函数（有建议、无建议、skip 结论、规则判断）；formatChatJobStatus 输出〔执行：Antigravity〕
- [x] T016 检查点：全量测试通过；审核 diff；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 4: US3 HR 要简历时突出提示

- [x] T017 [US3] `extension/src/chat-view.js` 新增纯函数 `hasHrResumeRequest(messages)`：存在 `body_type`（或 `bodyType`）为 7、不是自己发的、`text` 含"简历"的消息即为 true（交换微信/电话等请求卡片不算）〔执行：Antigravity〕
- [x] T018 [US3] `extension/src/sidepanel.js`：复用已有的 `read_chat_page` 读取结果（只读页面已加载数据），当前会话 `hasHrResumeRequest` 为 true 且岗位有简历建议时，「当前岗位」以醒目样式显示"HR 在要简历，建议投：<file_name>"及理由；有请求、岗位已有判断但无建议时提示"点重新判断可获得简历建议"；切换会话按新会话重新判断，不残留〔执行：Antigravity〕
- [x] T019 [P] [US3] 测试：`hasHrResumeRequest`（要简历卡片、要微信卡片、自己发的、普通文字含"简历"但 bodyType 1）；突出显示决策纯函数〔执行：Antigravity〕
- [x] T020 检查点：全量测试通过；审核 diff；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 5: 收尾

- [x] T021 `README.md`：补充简历建议的说明（配置文件位置、发送内容、旧判断不变与重新判断）〔执行：Claude Code〕
- [x] T022 检查点：全量测试；提交；整理真实页面验证步骤〔执行：Claude Code〕
- [ ] T023 真实页面验证〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）

---

## 2026-09-29 修订：个人内容改为用户设置（阶段 6–8）

**背景**：Jet 也会给朋友用，仓库不能写死个人内容（spec 已修订：FR-001–FR-004、FR-010–FR-014）。阶段 1–4 中基于 `resume_directions.json` 的设计由本节取代：简历改为设置页"我的简历"，编号 1–3；从严行业改为设置页勾选。v6 版本号与 v5 过时基准不变。**本分支合并时 MUST squash，MUST NOT push。**

**本节设计（Claude 决定）**：
- 存储：新表 `resume_slots(user_id, slot INTEGER CHECK(slot IN (1,2,3)), name TEXT NOT NULL, job_types TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id, slot))`；新表 `strict_industry_selection(user_id, industry TEXT NOT NULL, PRIMARY KEY(user_id, industry))`。都不属于画像（profile），不参与画像版本与"可能过时"判定。
- `judgements.resume_direction` 改存简历编号（"1"/"2"/"3"），显示时按当前 `resume_slots` 换成名称；编号无对应简历时不显示。
- 从严行业迁移：`src/jet/db/migrations.py` 新增 `migrate_to_v7`（照 `migrate_to_v6` 写法，迁移前自动备份），为升级前已存在的每个用户插入原名单六个行业；新库直接建到 user_version 7，不插入任何勾选。
- 规则文件 `strict_industry_rules.json`：保留行业、同义名、关键词作为通用"可选行业"资料；原"名单即全部从严"的语义改为"可选列表"。

### 阶段 6: 我的简历（设置页）

- [x] T024 `src/jet/db/schema.sql`：新增 `resume_slots` 表；删除 `src/jet/llm/prompts/resume_directions.json`；`src/jet/domain/resume.py` 改为从数据库按用户读取简历（`list_resumes(conn, user_id)`、`save_resumes(...)`：最多 3 份，名称与岗位类型都必填、去首尾空格，岗位类型长度上限按现有文本字段做法；有效简历少于 2 份时判断不启用建议）〔执行：Antigravity〕
- [x] T025 `src/jet/api/routes.py`：新增 `GET /v1/resumes`、`PUT /v1/resumes`（鉴权同现有设置接口，如 `/v1/experience`）；`to_api` 的 `resume_suggestion` 改为 `{"slot","name","reason"}`，按当前用户的 `resume_slots` 解析（需要 conn 与 user_id 的调用链按现有方式传入）〔执行：Antigravity〕
- [x] T026 `src/jet/llm/prompt_v6.py`、`src/jet/llm/client.py`、`src/jet/worker.py`：简历段改为从当前用户的 `resume_slots` 生成，只含"简历1/2/3"编号与适合的岗位类型，绝不含名称；有效简历少于 2 份时与 v5 逐字相同；输出 `resume_suggestion: {"slot": 1|2|3, "reason"}`，校验编号在当前已填写简历中〔执行：Antigravity〕
- [x] T027 插件：`extension/src/options.html`、`options.js`（与"我的背景""经历素材"同页）新增"我的简历"区：最多 3 份，每份简历名称与适合的岗位类型，保存/删除；显示说明"简历的'适合的岗位类型'和勾选的从严行业会随岗位判断发给 DeepSeek；简历名称不会发送"；只填 1 份时提示"至少 2 份才会给出建议"。`jet-client.js`/`background.js` 按现有设置接口的方式接入。卡片与侧边栏显示改用 `resume_suggestion.name`（`view-state.js`、`content.js` 同步副本、`chat-view.js`、`sidepanel.js`）〔执行：Antigravity〕
- [x] T028 [P] 测试：简历保存与校验（最多 3 份、必填、去空格）；接口鉴权；v6 消息只含编号与岗位类型、不含名称；少于 2 份时与 v5 相同；编号解析与删除后不显示；修改简历不使判断过时、不触发重判；插件显示改用 name〔执行：Antigravity〕
- [x] T029 检查点：全量测试；审核 diff；Codex；提交；重启并健康检查〔执行：Claude Code〕

### 阶段 7: 从严行业（设置页勾选）

- [x] T030 `src/jet/db/schema.sql`、`src/jet/db/store.py`、`src/jet/db/migrations.py`：新增 `strict_industry_selection` 表与 `migrate_to_v7`（备份后为已存在用户插入原六个行业）；新库 user_version 7 且无勾选〔执行：Antigravity〕
- [x] T031 `src/jet/domain/industry.py`：可选行业仍读规则文件；新增按用户读取/保存勾选（勾选中已不在规则文件里的行业忽略）；`src/jet/api/routes.py` 新增 `GET /v1/strict-industries`（返回可选行业与已勾选）、`PUT /v1/strict-industries`〔执行：Antigravity〕
- [x] T032 粗筛（`routes.py` 约 370–415 行 observations 中的 `read_strict_industries()` 调用、`src/jet/domain/rules.py`）与判断提示词（`client.py` 传给 `prompt_v5`/`prompt_v6` 的从严行业）都只用当前用户勾选的行业（同义名与关键词仍取规则文件中对应行业的）；未勾选时不传，提示词不含从严行业段〔执行：Antigravity〕
- [x] T033 插件：`options.html`/`options.js` 新增"从严行业"勾选区（列出可选行业，显示发送说明）；保存后已打开的列表页按现有"保存画像后更新提示"的方式更新粗筛提示〔执行：Antigravity〕
- [x] T034 [P] 测试：迁移（已有用户得到六个勾选、新库无勾选、迁移前生成备份文件）；接口；粗筛与提示词只含勾选行业；未勾选时提示词不含从严段；对"六个全勾"的用户提示词与迁移前逐字相同；改勾选不使判断过时〔执行：Antigravity〕
- [x] T035 检查点：全量测试；审核 diff；Codex；备份真实数据库后提交、重启（触发迁移）并核对本机勾选为六个行业〔执行：Claude Code〕

### 阶段 8: 收尾

- [x] T036 `README.md` 与 006 plan 文档更新为设置页方案；汇报全仓库个人内容检查结果（只列不改）〔执行：Claude Code〕
