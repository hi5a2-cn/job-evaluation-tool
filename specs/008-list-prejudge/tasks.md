# Tasks: 列表页岗位大模型预判 (008-list-prejudge)

**Input**: `/specs/008-list-prejudge/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`contracts/plugin-protocol.md`、`quickstart.md`）

**Tests**: 自动化测试严格遵循宪法原则 VIII（不联网、不需真实 Key、不写真实数据目录、使用保存的样本与 MockTransport/假 jetClient）。生产代码严禁包含针对测试的特殊判断；不删除、不放宽已有测试断言。

**Organization**: 阶段 1 数据库迁移与独立配额模型 → 阶段 2 服务端预判提示词与 API 接口 → 阶段 3 插件列表抓取与预判调度 → 阶段 4 列表标记四级优先级与视觉区隔渲染 → 阶段 5 设置页管理与全链路回归。每阶段末尾：全量测试、Codex 审核、提交；改服务端的阶段提交后 `launchctl kickstart -k` 重启。

`〔执行：Antigravity〕` 编写代码与测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）；`〔执行：Claude Code〕` 审核、Codex、提交、重启与真实页面验收。

**共同硬性要求**：
- 严格遵循 Constitution 5.0.0 核心原则；
- 预判数据独立存储在 `prejudgements` 表，绝不写入 `judgements` 表；
- 预判配额按“页”独立管理，与判断、沟通配额彻底隔离；
- 提示词严格遵守字段白名单，绝对不得包含 HR 笔记、聊天记录等私密信息；
- 仓库内任何文件均不得写入用户真实姓名、简历文件名、个人城市薪资偏好等隐私信息。

---

## 阶段 1: 数据库迁移与独立配额模型

- [x] T001 数据库迁移 v8（research S1, data-model 2）：在 `src/jet/db/migrations.py` 实现 `migrate_to_v8`（新建 `prejudgements` 表含 `UNIQUE(user_id, job_id, profile_id)` 与外键；`user_settings` 新增 `daily_prejudge_limit` 默认 20、0..200；重建 `llm_calls` 表增加 `'prejudge'` 枚举并完整保留数据；事务与外键检查；更新 `LATEST_VERSION = 8`）；同步更新 `src/jet/db/schema.sql`〔执行：Antigravity〕
- [x] T002 不做（上限只在设置页改，2026-09-30 Claude 决定）（2026-10-06：已决定不做，见本行说明）
- [x] T003 独立每日配额（research S2, data-model 3）：在 `src/jet/llm/quota.py` 中扩展 `usage_today`、`remaining_today` 与 `reserve` 支持 `purpose='prejudge'`；读取 `daily_prejudge_limit`；额度用完（`used >= limit` 或 `limit <= 0`）返回 None；与判断、话术额度完全隔离〔执行：Antigravity〕
- [x] T004 [P] 阶段 1 单元测试：在 `tests/unit/test_migrations.py` 增加 v7 到 v8 迁移测试（含表创建、默认值、llm_calls 历史数据完整保留、事务失败回滚）；在 `tests/unit/test_prejudge_quota.py` 测试预判配额独立计数与耗尽拦截〔执行：Antigravity〕
- [x] T005 检查点：全量测试（`uv run --frozen pytest`）；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 2: 服务端预判提示词与 API 接口

- [x] T006 提示词与响应解析（research S4, contracts/local-api.md）：新建 `src/jet/llm/prejudge.py`；构建系统提示明确声明"只根据列表信息的粗略预判，不是正式判断"；仅提取白名单字段（职位名、公司名、行业、薪资、城市区县、经验、学历、标签技能，以及画像和从严行业）；调用非思考引擎（`settings.judge_engine`）；解析响应 JSON 数组，过滤非法 ID 与非法 level，截断 reason 至 ≤40 字〔执行：Antigravity〕
- [x] T007 预判业务路由（contracts/local-api.md §1）：在 `src/jet/api/routes.py` 实现 `POST /v1/prejudge`（`require_paired`；仅处理库内已有岗位；排除该用户已有正式判断 done 的岗位；提取当前画像预判缓存；无候选集不调模型；无 Key 或无画像返回对应状态；配额耗尽返回 `quota_exhausted`；打包调用非思考引擎；合规数据批量写入 `prejudgements` 并结算 `llm_calls`）〔执行：Antigravity〕
- [x] T008 预判设置路由（contracts/local-api.md §2-3）：在 `src/jet/api/routes.py` 实现 `GET /v1/prejudge/settings` 与 `PUT /v1/prejudge/settings`（读取和修改 `daily_prejudge_limit`，校验 0..200 整数，返回今日已用与剩余页数）〔执行：Antigravity〕
- [x] T009 [P] 阶段 2 测试：在 `tests/unit/test_prejudge_prompt.py` 测试提示词字段白名单断言（绝对不含 HR 笔记、聊天、隐私数据）与容错解析；在 `tests/api/test_prejudge_routes.py` 测试路由鉴权、已判断排除、缓存直出、配额拦截、整页打包与设置读写〔执行：Antigravity〕
- [x] T010 检查点：全量测试；审核 diff；Codex 审计；提交；`launchctl kickstart -k` 重启服务〔执行：Claude Code〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 3: 插件列表抓取与预判调度

- [x] T011 列表项数据抓取扩展（contracts/plugin-protocol.md §1）：在 `extension/src/page-reader.js` 的 `readBossPage()` 列表项映射中新增 `experience`（`item.jobExperience`）与 `degree`（`item.jobDegree`），缺失为 null；严格只读已加载 Vue 数据〔执行：Antigravity〕
- [x] T012 客户端网络调用封装：在 `extension/src/jet-client.js` 中新增 `prejudgeJobs(jobs)`、`getPrejudgeSettings()`、`putPrejudgeSettings(daily_prejudge_limit)` 请求方法〔执行：Antigravity〕
- [x] T013 后台预判编排与状态维护（contracts/plugin-protocol.md §2）：在 `extension/src/background.js` 中扩展 `tabState.listPrejudge` Map；在 `handleSendList` 成功后对本批新发现且无正式判断的岗位发起单次 `POST /v1/prejudge`；返回结果写入 `tabState.listPrejudge` 并触发 `sendRenderState`；额度耗尽当天不再重试；在 `handleSaveProfile` 中清空 `listPrejudge` 与 `listSent`〔执行：Antigravity〕
- [x] T014 [P] 阶段 3 单元测试：在 `extension/tests/page-reader.test.js` 测试经验与学历提取；在 `extension/tests/jet-client.test.js` 测试预判相关方法契约〔执行：Antigravity〕
- [x] T015 检查点：全量测试；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 4: 列表标记四级优先级与视觉区隔渲染

- [x] T016 四级优先级标记生成（contracts/plugin-protocol.md §3）：在 `extension/src/page-summary.js` 扩展 `buildListMarks(listJudgements, listPrejudge)`，严格实现四级优先级：正式判断 (done 且有结论) > 预判 (prejudge) > 粗筛提示 > 仅状态；生成预判标记（`type: "prejudge"`, `is_prejudge: true`, `verdict_label` 为 "预判·值得点开"/"预判·一般"/"预判·可跳过", `reason`）〔执行：Antigravity〕
- [x] T017 视觉外观排版算法同步（contracts/plugin-protocol.md §4）：在 `extension/src/marks-layout.js` 与 `extension/src/content.js` 中同步预判样式：色条采用虚线或半透明（`border-left: dashed`，`opacity: 0.7`）；标签为空心描边小字样式且带"预判"前缀；tooltip 绑定预判理由；正式判断样式保持不变〔执行：Antigravity〕
- [x] T018 [P] 阶段 4 测试：在 `extension/tests/page-summary.test.js` 测试四级优先级与各种组合；在 `extension/tests/marks-layout.test.js` 测试预判色条与描边排版计算〔执行：Antigravity〕
- [x] T019 检查点：全量测试；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 5: 设置页管理与全链路回归

- [x] T020 设置页 UI 与交互：在 `extension/src/options.html` 新增"列表预判"卡片（每日上限输入框、今日已用/剩余展示、保存按钮）；在 `extension/src/options.js` 实现上限加载、0..200 校验、保存与回显，0 提示为关闭〔执行：Antigravity〕
- [x] T021 [P] 阶段 5 测试：在 `extension/tests/options-prejudge.test.js` 测试设置页数值校验与状态文案；运行 `node --check extension/src/*.js` 语法检查〔执行：Antigravity〕
- [x] T022 全量自动化与 E2E 验证：执行 `uv run --frozen pytest` 与 `node --test extension/tests/*.test.js`（已全部通过）〔执行：Antigravity〕；按 `quickstart.md` 在真实页面完整走通 5 个场景〔执行：用户〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）
- [x] T023 最终检查点：审核全部 diff；Codex 完整审计；提交〔执行：Claude Code〕（2026-10-06 补勾：008 已于 fe9e203 合入 main；当时是否逐项执行以提交记录为准）
