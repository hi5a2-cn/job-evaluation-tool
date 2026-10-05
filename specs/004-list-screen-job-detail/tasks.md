# Tasks: 列表页规则粗筛 + 独立职位页被动读取 (004-list-screen-job-detail)

**Input**: Design documents from `/specs/004-list-screen-job-detail/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`quickstart.md`）

**Prerequisites**: main 上已合入 fix/003-followups（`jobs.company_industry`、user_version 6、FR-057 规则文件 `src/jet/llm/prompts/strict_industry_rules.json`）

**Tests**: 包含自动化测试与真实页面验证。测试遵循宪法原则 VIII（不联网、不需 Key、不开浏览器、不写真实数据目录）；插件端 DOM 测试用手写 `MockElement`（参照 `extension/tests/chat-reader.test.js`），样本只含结构、不含真实页面文字。

**Organization**: 阶段 1 共用基础（行业规则移到 domain 层）→ 阶段 2 US1 列表页粗筛（P1，MVP）→ 阶段 3 US2 独立职位页（P2）→ 阶段 4 收尾。US1 与 US2 互不依赖，阶段 1 完成后可按顺序或分开进行；每个阶段末尾有检查点（全量测试、Codex 审稿、提交）。

## 任务格式规范

`- [ ] T001 [P] [Story] 任务说明（含文件路径） 〔执行：执行者〕`
- **[P]**: 可并行任务（与其他任务无文件冲突且不依赖前置任务产物）
- **[Story]**: 所属用户故事（US1、US2，对应 `spec.md`）
- **执行者标注**:
  - `〔执行：Antigravity〕`: 写代码、写测试、跑测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）
  - `〔执行：Claude Code〕`: 审核 diff、Codex 审稿、Git 提交、重启 jet serve、真实页面验证协调
  - `〔执行：用户〕`: 真实 BOSS 页面上的手工操作与验收

**所有写代码任务的共同硬性要求**（写进每次委托）：生产代码不含针对测试的特殊处理；不全局替换标准库或第三方行为；不删除、不放宽已有测试断言（确需改已有测试须逐条说明）；保持现有代码风格与中文注释密度；发给大模型的内容不变。

---

## 阶段目标与依赖关系

```text
阶段 1: 行业规则移到 domain 层 ──┬──> 阶段 2: US1 列表页粗筛（P1，MVP）──┐
                                 └──> 阶段 3: US2 独立职位页（P2）──────┴──> 阶段 4: 收尾
```

---

## 阶段 1: 共用基础（行业规则读取与匹配移到 domain 层）

**阶段目标**: 粗筛（domain）与判断提示词（llm）共用同一份重点排查行业规则，依赖方向为 llm → domain（research R3）。行为不变。

- [x] T001 新建 `src/jet/domain/industry.py`：把 `src/jet/llm/prompt_v5.py` 中的规则文件路径常量、`read_strict_industries()`、`read_strict_industry_aliases()` 移入（规则文件位置仍为 `src/jet/llm/prompts/strict_industry_rules.json`，读取行为与异常处理完全不变）；新增 `match_strict_industry(company_industry, industries, aliases) -> str | None`：公司行业去首尾空格后与某行业名或其同义名**相同**，或**包含**该行业名或同义名即命中，返回行业名；多个行业命中取规则文件中靠前的一个；公司行业为空返回 `None`。`prompt_v5.py` 改为从 `jet.domain.industry` 导入这些函数（保留原名，已有测试从 `prompt_v5` 导入须继续通过）〔执行：Antigravity〕
- [x] T002 [P] 新增 `tests/unit/test_industry.py`：覆盖 `match_strict_industry` 的相同、包含（如"食品/饮料/烟酒"命中"快消"）、未命中、空值、多行业命中取靠前者；规则文件缺失或格式错误时返回 `None` 且不报错〔执行：Antigravity〕
- [x] T003 检查点：运行 `uv run --frozen pytest` 与 `node --test extension/tests/*.test.js` 全部通过；审核 diff（确认只是移动与新增、`prompt_v5` 行为不变）；Codex 审稿；提交〔执行：Claude Code〕

---

## 阶段 2: US1 列表页粗筛（P1，MVP）

**阶段目标**: 列表岗位入库时按当前画像与行业规则计算粗筛提示，随响应返回；插件在未完成判断的岗位卡片标记上显示。粗筛只是提示：不写入判断表、不给结论、不调大模型、不占额度。

**独立验证**: 设好画像打开搜索列表页，命中四类规则的岗位出现对应提示、未命中的没有；浏览前后大模型调用记录、每日额度、判断记录不变（quickstart Q1–Q6）。

**前置决定**: plan"待用户拍板"1（标记样子）、2（画像变化后的刷新）须在 T010、T008 开始前确定。

### 服务端

- [x] T004 [US1] 在 `src/jet/domain/rules.py` 新增 `screen_hints(job_version, profile, company_industry, tags, industries, aliases) -> list[dict]`：返回 `{"type", "text"}` 列表，`type` 取值 `strict_industry` / `salary_floor` / `exclude_keyword` / `excluded_city`，`text` 分别为 `高风险行业（<行业名>）`、`低于薪资底线`、`命中不接受条件：<词>`、`不去的城市`；顺序固定为 `strict_industry` → `salary_floor` → `exclude_keyword` → `excluded_city`，同一类可有多条；`profile` 为 `None` 时只计算 `strict_industry`；后三类复用现有 `screen()` 的同一套规则与画像字段（职位名以外，`tags` = 岗位标签 + 技能标签参与"不接受条件"匹配），保证与点开后规则排除一致；薪资不可见、"面议"、无法解析不产生 `salary_floor`，公司行业为空不产生 `strict_industry`（FR-001、FR-002、FR-007）〔执行：Antigravity〕
- [x] T005 [US1] 在 `src/jet/api/routes.py`：`ObservationJob` 增加可选 `job_labels`、`skills`（"字符串数组，≤ 20 项，每项 ≤ 30 字"，超出截断，与现有 `company_name` / `company_industry` 处理方式一致；只用于粗筛，**不入库、不参与内容指纹**）；`POST /v1/observations` 在 `page_type = "list"` 时于入库之后读取当前画像（`get_current_profile`）与行业规则（`jet.domain.industry`），为每个岗位调用 `screen_hints()`，放入响应 `jobs[id].screen_hints`（没有命中为 `[]`）；`screen_hints` 只在列表响应中出现；不写任何表、不调大模型、不占额度（FR-005，contracts/local-api.md）〔执行：Antigravity〕
- [x] T006 [P] [US1] 新增测试 `tests/unit/test_screen_hints.py` 与 `tests/api/test_observations_screen_hints.py`：四类提示各自命中与不命中；顺序；无画像只出行业提示；薪资不可见 / 面议 / 无法解析不出薪资提示；技能标签命中不接受条件；与 `screen()` 结果一致；列表请求后 `judgements` 行数、`llm_calls` 行数、当日额度均不变；`job_labels` / `skills` 超长截断且未写入任何表、岗位版本号不变；详情响应中没有 `screen_hints`〔执行：Antigravity〕

### 插件端

- [x] T007 [US1] 在 `extension/src/page-reader.js` 列表映射中增加 `job_labels`（取 `item.jobLabels`）、`skills`（取 `item.skills`）：只保留非空字符串、去首尾空格，读不到为 `[]`；不读取其他新字段（FR-009）；在 `extension/src/background.js` `sendListObservations` 的 payload 中原样带上这两个字段；把响应中的 `screen_hints` 存入 `tabState.listJudgements` 对应条目（与 `judgement`、`my_status` 并列），并随标签页状态保存 / 恢复〔执行：Antigravity〕
- [x] T008 [US1] 画像变化后的刷新（按 plan 待定决定 2 的选项）：选 A 时，在 `extension/src/background.js` `save_profile` 成功后清空所有 BOSS 标签页的 `listSent`，并对当前为搜索列表页的标签页各调用一次现有读取流程（只读页面已加载的数据，不导航、不刷新页面）；选 B 时本任务取消（FR-006）〔执行：Antigravity〕
- [x] T009 [US1] 在 `extension/src/page-summary.js` `buildListMarks` 中：判断完成（`status == "done"` 且有结论）的岗位照旧只生成判断标记；其余岗位若 `screen_hints` 非空，生成一种新的"提示"标记（含全部提示文字，按服务端顺序），不影响现有判断标记与侧边栏页面摘要（FR-004）〔执行：Antigravity〕
- [x] T010 [US1] 在 `extension/src/marks-layout.js` 与 `extension/src/content.js`（两处保持一致）渲染"提示"标记（按 plan 待定决定 1 的选项）：选 A 时色条用与四档结论都不同的中性色（灰蓝），小字标签"粗筛：提示1 · 提示2"，放不下截断、悬停显示全部；选 B 时标签只显示第一条加"等 N 项"，悬停显示全部；沿用 002 FR-054 的不遮挡规则〔执行：Antigravity〕
- [x] T011 [P] [US1] 新增测试 `extension/tests/screen-hints.test.js`：列表读取带出 `job_labels`、`skills`（含读不到为 `[]`）；`buildListMarks` 对已判断岗位只出判断标记、对未判断且有提示的岗位出提示标记、无提示不出标记；提示标记的截断文字与悬停全文；`content.js` 与 `marks-layout.js` 的对应函数一致（参照已有一致性检查）〔执行：Antigravity〕

### 检查点与验证

- [x] T012 [US1] 检查点：全量测试通过；审核 diff；Codex 审稿（两栏汇报）；提交；重启 jet serve 并检查健康状态〔执行：Claude Code〕
- [ ] T013 [US1] 【真实页面验证】quickstart Q1–Q6：高风险行业、薪资底线（含"面议"不提示）、技能标签命中不接受条件、已判断岗位只显示判断标记、改画像后提示更新、浏览后额度与判断记录不变〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）

---

## 阶段 3: US2 独立职位页被动读取（P2）

**阶段目标**: 用户自己打开独立职位页时，读取职位名、薪资、城市、职位描述、公司工商登记名，按现有详情流程入库与判断；公司名不覆盖；入库后同步聊天页"当前岗位"；读不到显示"此页暂不支持读取"。

**独立验证**: 从聊天页点「查看职位」打开独立职位页，卡片出现并完成判断，回到聊天页"当前岗位"显示在库；读不到的页面显示"此页暂不支持读取"且岗位库不增加（quickstart Q7–Q10）。

### 服务端

- [x] T014 [US2] 在 `src/jet/api/routes.py` `ObservationJob` 增加可选 `company_legal_name`（"字符串，≤ 100 字"，超长截断）；在 `src/jet/domain/jobs.py` `ingest` 中：`company_legal_name` 有值时，新岗位直接作为 `company_name` 写入，已有岗位**只在 `company_name` 为空时**写入；现有 `company_name` "非空即覆盖"规则不变（FR-014，research R7）〔执行：Antigravity〕
- [x] T015 [P] [US2] 新增测试 `tests/unit/test_company_legal_name.py`：新岗位写入工商名；已有品牌名不被覆盖；已有岗位无公司名时写入；与 `company_name` 同时出现时的行为；岗位版本号与内容指纹不因此变化〔执行：Antigravity〕

### 插件端

- [x] T016 [US2] 在 `extension/src/page-reader.js` `readBossPage` 增加 `job_detail_page` 分支（在页面主环境只读 DOM，不导航、不点击、不改页面）：岗位 ID 取地址 `/job_detail/<岗位 ID>.html`；职位名 `.job-banner .info-primary .name h1`；薪资 `.job-banner .info-primary .name span.salary`；城市 `.job-banner .info-primary a.text-desc.text-city`；职位描述取第一个 `.job-detail-section > .job-sec-text`（排除 `.salary-info` 内与 `.job-detail-company` 内的同名元素）；公司工商登记名 `.job-detail-company .business-info-box li.company-name`（去首尾空格，若带"公司名称"类前缀则去掉）。返回结构见 contracts/local-api.md："读取成功时 `detail.ok = true`，`detail.job` 含 `platform_job_id`、`title`、`salary_raw`、`city`、`district`（固定为 `null`）、`description`、`company_legal_name`（可为 `null`）；读取失败时 `detail.ok = false` 并在 `problems` 中说明缺少的必需字段"。必需字段为岗位 ID、职位名、职位描述。**绝不读取** `.job-boss-info`、`li.company-user`、`.company-address`、`.similar-job-*`、`.look-job-list`（FR-011、FR-016）〔执行：Antigravity〕
- [x] T017 [US2] 在 `extension/src/scheduler.js` `decideDetailAction` 中：独立职位页读到必需字段 → `send`（与列表页详情区相同的键与去重）；读不到 → 沿用现有自动重读；重读用完仍失败 → 显示"此页暂不支持读取"（沿用 `unsupported_page` 文案，不显示"页面无法识别"），不入库、不判断、不改读整页文字（FR-012）〔执行：Antigravity〕
- [x] T018 [US2] 在 `extension/src/background.js`：`handleSendDetail` 对独立职位页的岗位在 payload 中带 `company_legal_name`、不带 `company_name`；入库成功后用 `syncChatJobStatusInTabStates` 更新各标签页中同一岗位的聊天状态缓存，并发送已有的 `chat_job_status_updated` 消息，侧边栏"当前岗位"随之更新（research R8）〔执行：Antigravity〕
- [x] T019 [P] [US2] 新增测试 `extension/tests/job-detail-reader.test.js`：用 `MockElement` 按 0.2 探测的层级构造独立职位页（不含真实文字）：成功读取各字段；职位描述不会取到薪资说明或公司介绍；缺职位名 / 缺职位描述 / 地址无 ID 时 `detail.ok = false`；不读取招聘者、法定代表人、地址、相似岗位；`decideDetailAction` 的 `send` / 重读 / 最终"此页暂不支持读取"〔执行：Antigravity〕

### 检查点与验证

- [x] T020 [US2] 检查点：全量测试通过；审核 diff；Codex 审稿（两栏汇报）；提交；重启 jet serve 并检查健康状态（发给大模型的公司名来源变化已由用户 2026-09-29 确认，FR-018）〔执行：Claude Code〕
- [ ] T021 [US2] 【真实页面验证】quickstart Q7–Q10：从聊天页打开独立职位页出现卡片并完成判断；回到聊天页"当前岗位"显示在库；已在库岗位的公司名仍是品牌简称；异常页面显示"此页暂不支持读取"且岗位库不增加〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）

---

## 阶段 4: 收尾

- [x] T022 [P] 更新 `README.md` 功能简介：列表页粗筛提示（只是提示、不花钱）与独立职位页读取；更新 `specs/002-boss-readonly-extension/contracts/page-reader.md` 第 2 节，指向 004 的变化〔执行：Antigravity〕
- [x] T023 检查点：全量测试（`uv run --frozen pytest`、`node --test extension/tests/*.test.js`）；确认 spec 所有 FR 均有对应实现或测试；提交〔执行：Claude Code〕

---

## 并行机会

- 阶段 1：T002 可与 T001 的审核并行准备（同一次委托内完成亦可）。
- 阶段 2：T006（服务端测试）与 T007（插件读取）文件不重叠，可并行；T011 与 T006 可并行。
- 阶段 3：T015 与 T016、T019 文件不重叠，可并行；T014 与 T016/T017 可由两次委托并行完成。
- US1 与 US2 在阶段 1 之后互不依赖；但两者都改 `extension/src/page-reader.js`、`extension/src/background.js`、`src/jet/api/routes.py`，**同时进行时须分两次委托串行改这三个文件**，以便分别提交。

## 实施策略

1. **MVP**：阶段 1 + 阶段 2（US1），完成后即可在列表页得到零费用的粗筛提示，单独验收、单独提交。
2. **增量**：阶段 3（US2）独立交付；阶段 4 收尾。
3. 每个阶段检查点都跑全量测试、Codex 审稿，采纳的意见交 Antigravity 修改，Claude 审核 diff 后提交；有测试失败或不属于本阶段的改动即停下汇报。
