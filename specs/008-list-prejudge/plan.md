# Implementation Plan: 列表页岗位大模型预判 (008-list-prejudge)

**Branch**: `008-list-prejudge` | **Date**: 2026-09-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-list-prejudge/spec.md`

---

## Summary

本功能落实 2026-09-30 用户确认的列表页预判需求，核心解决求职者在列表页无法快速判断岗位是否匹配画像的问题：
1. **数据模型与独立存储（v8 迁移）**：
   - 新建 `prejudgements` 表（按用户隔离，独立存储，绝不写入 `judgements` 表）；
   - `user_settings` 新增 `daily_prejudge_limit`（默认 20 页，0–200 可配置，0 为关闭）；
   - 重建 `llm_calls` 表，扩展 `purpose` CHECK 约束加入 `'prejudge'`；
   - 额度模块独立计数（按"页"消耗，独立管理与计费，不与判断、话术额度共用）。
2. **服务端打包预判与提示词受限边界**：
   - 提供 `POST /v1/prejudge`（每页最多 40 条）；
   - 过滤逻辑：库内已有才处理、已有正式判断（done）的不预判、当前画像已预判的读缓存；
   - 额度用完返回 `quota_exhausted` 且不调用、不排队；
   - 候选岗位整页打包为 1 次请求，调用非思考引擎（`settings.judge_engine`，`deepseek-flash:no-think`）；
   - 提示词模块 `src/jet/llm/prejudge.py` 严格限制只发列表已有字段及用户画像/从严行业，严禁发送 HR 笔记、聊天记录等私密信息；
   - 提供 `GET/PUT /v1/prejudge/settings` 读写每日上限。
3. **插件端抓取扩展与视觉外观明显区隔**：
   - `page-reader.js` 增补列表项 `experience` 与 `degree`；
   - `background.js` 在 `handleSendList` 成功后对未正式判断的新增岗位发起打包预判，结果存入 `tabState.listPrejudge`；画像保存时清空预判缓存；
   - `page-summary.js` 确立优先级：正式判断 (done) > 预判 (prejudge) > 粗筛提示 > 仅状态；
   - `content.js` 与 `marks-layout.js` 统一外观：虚线/半透明色条 + 空心描边小字标签（带"预判"字样）+ tooltip 理由；正式判断样式保持不变；
   - `options.html` / `options.js` 增加"列表预判"设置卡片。

---

## Technical Context

**Language/Version**: Python 3.12+（Jet 本机服务）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建打包）。

**Primary Dependencies**: 后端 FastAPI、pydantic、httpx、dataclasses；前端原生 Web API 与 Chrome Extension API（`chrome.runtime`, `chrome.storage`, `chrome.tabs`）。

**Storage**:
- SQLite 数据库表迁移至 v8（新建 `prejudgements`、`user_settings` 加列、重建 `llm_calls`）；
- 前端内存：`tabState.listPrejudge`（Map 结构）。

**Testing**:
- 后端：`uv run --frozen pytest`（覆盖 v8 迁移与数据保留、独立配额与用尽拦截、过滤正式判断/缓存、打包单次请求、提示词字段白名单断言、响应校验、设置接口）；
- 插件单元测试：`node --test extension/tests/*.test.js`（覆盖 reader 字段抓取、background 批次预判与画像清理、summary 四级优先级、marks-layout 虚线描边排版、options 设置页逻辑）；
- 语法与静态检查：`node --check extension/src/*.js`。

**Target Platform**: macOS；Chrome 稳定版（Manifest V3）。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 浏览器插件）。

**Performance Goals**:
- 列表预判整页（20–40个岗位）打包单次调用响应时间 $< 3$ 秒；
- 缓存命中直接读取耗时 $< 5$ 毫秒；
- 插件卡片标记重绘无感知、不卡顿（$< 16$ 毫秒）。

**Constraints**:
- 绝对不向 BOSS 发起任何网络请求、不自动翻页或滚动；
- 预判数据绝不写入 `judgements` 表；
- 预判额度耗尽当天不再发起大模型调用、不排队，静默降级；
- 默认测试不联网、不写真实数据目录。

---

## Constitution Check

*GATE: 逐条对照 Constitution 5.0.0 核心原则 I–XI 及附加约束。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 符合。服务于主线中的"浏览 → 读取 → 入库 → 预判/判断 → 提醒"闭环。列表预判在用户浏览列表时提供轻量导向，辅助用户决定是否点开详情，不扩展自动投递或打招呼等外围功能。 | 符合 |
| **II. 数据归属从第一天明确** | 符合。列表预判数据写入独立的 `prejudgements` 表，按用户数据隔离（`user_id`），每条记录归属清晰；公共岗位库仅汇入岗位数据并标明 `list_only`；预判绝不写入 `judgements` 表，其他用户不可见。 | 符合 |
| **III. 插件与本机 Jet 分离** | 符合。插件仅负责读取页面已有数据、发给本机 Jet 并展示卡片标记；预判分析与大模型调用全部留在本机 Jet 后台执行。两者仅通过 127.0.0.1 交换数据。 | 符合 |
| **IV. 只读，不向招聘平台发起任何请求** | 符合。插件只读取当前页面已加载的 Vue 实例字段，不向 BOSS 发起任何网络请求，不点击、不滚动、不翻页，不改变页面行为。 | 符合 |
| **V. 平台风险控制** | 符合。不进行任何页面自动化操作；遇验证码或页面变动不干预；读取失败如实标注，不静默伪造数据。 | 符合 |
| **VI. 耗时任务后台化** | 符合。预判请求在后台异步完成，插件不阻塞用户的页面滚动与浏览操作；重复请求合并；打开设置页绝不触发预判。 | 符合 |
| **VII. 成本有上限** | 符合（对照 5.0.0 最新修订）。<br/>1. 明确标明"预判"，不写入判断表，不当正式结论；<br/>2. 独立每日上限（按"页"计，默认 20 页，可在设置页调整为 0–200），与正式判断、沟通配额彻底隔离；<br/>3. 整页打包为一次请求，调用轻量非思考引擎（`judge_engine`）；<br/>4. 已有正式判断的岗位不预判；当前画像已预判的岗位直接读缓存，不重复调用；<br/>5. 上限用完当天不再预判，不在额度不足时排队到以后执行。 | 符合 |
| **VIII. 测试与数据安全** | 符合。提示词严格限制在外发字段白名单内，严禁发送 HR 实际情况与聊天记录；默认测试不联网、不写真实数据目录；仓库中绝不包含任何用户个人隐私信息。 | 符合 |
| **IX. 每个数字都有定义** | 符合。预判每日配额定义清晰：以"页"（批次请求）为统计单位，记录当日已计费（billed=1）的 `purpose='prejudge'` 请求数，按用户过滤。 | 符合 |
| **X. 证据与最小改动** | 符合。改动严格聚焦于列表预判所需的最小集合（v8 迁移、prejudge 接口与提示词、插件抓取与标记渲染、设置页）；区分事实与推论。 | 符合 |
| **XI. 代码组织** | 符合。Python 代码全部位于 `jet` 包下（`src/jet/llm/prejudge.py`、`src/jet/api/routes.py` 等）；插件代码位于 `extension/`；无通用顶层包或多余依赖。 | 符合 |
| **附加约束** | 符合。本机 Python 3.12 + SQLite + 127.0.0.1；Chrome Manifest V3 扩展；只在本机运行，不部署云端。 | 符合 |

---

## 改动范围与预计改动量

| 模块 | 文件 | 改动内容 | 预计行数（不含测试） |
|---|---|---|---|
| 数据库架构 | `src/jet/db/schema.sql` | 1. 新建 `prejudgements` 表；<br/>2. `user_settings` 加 `daily_prejudge_limit` 列；<br/>3. `llm_calls.purpose` CHECK 加入 `'prejudge'` | ~35 |
| 数据库迁移 | `src/jet/db/migrations.py` | 编写 `migrate_to_v8`：检查、事务中建新表、加列、重建 `llm_calls`、版本升为 8 | ~120 |
| 配置管理 | — | （2026-10-06 修订）实际没有改 `config.py`：每日预判上限只存在本机数据库 `user_settings.daily_prejudge_limit`（默认 20 页，0–200），在设置页修改 | 0 |
| 配额控制 | `src/jet/llm/quota.py` | `usage_today`、`remaining_today`、`reserve` 增加对 `purpose='prejudge'` 支持，读取 `daily_prejudge_limit` | ~40 |
| 提示词工程 | `src/jet/llm/prejudge.py` | 全新编写：系统提示词构建、白名单字段提取、打包格式化、响应 JSON 解析校验与理由截断 | ~150 |
| API 路由 | `src/jet/api/routes.py` | 1. 新增 `POST /v1/prejudge`（库内检查、过滤已判断/已缓存、额度检查、打包调用与入库）；<br/>2. 新增 `GET/PUT /v1/prejudge/settings` | ~160 |
| 插件读取 | `extension/src/page-reader.js` | 列表项映射增补 `experience` 与 `degree` | ~10 |
| 插件通信 | `extension/src/jet-client.js` | 封装 `prejudgeJobs`、`getPrejudgeSettings`、`putPrejudgeSettings` 方法 | ~20 |
| 插件后台 | `extension/src/background.js` | 1. `handleSendList` 成功后发起单次打包预判；<br/>2. `tabState.listPrejudge` 管理；<br/>3. `handleSaveProfile` 同步清空预判缓存 | ~65 |
| 标记数据 | `extension/src/page-summary.js` | `buildListMarks` 实现四级优先级，生成 `prejudge` 标记与文案/色系映射 | ~45 |
| 视图渲染 | `extension/src/content.js` | 渲染预判虚线/半透明色条与空心描边标签，绑定 tooltip | ~40 |
| 布局算法 | `extension/src/marks-layout.js` | 同步预判色条与标签排版算法（与 content.js 保持一致） | ~25 |
| 选项设置 | `extension/src/options.html` | 新增"列表预判"卡片（每日上限输入框、已用展示、保存按钮） | ~25 |
| 选项交互 | `extension/src/options.js` | 加载、校验（0–200）、保存与回显 `daily_prejudge_limit` | ~45 |
| 后端测试 | `tests/unit/test_migrations.py`<br/>`tests/unit/test_prejudge_quota.py`<br/>`tests/unit/test_prejudge_prompt.py`<br/>`tests/api/test_prejudge_routes.py` | 覆盖 v8 迁移、配额独立与拦截、字段白名单断言、整页打包与校验、接口契约 | ~450 |
| 插件测试 | `extension/tests/page-summary.test.js`<br/>`extension/tests/marks-layout.test.js`<br/>`extension/tests/options-prejudge.test.js` | 覆盖优先级流转、样式排版计算、选项页数值校验 | ~200 |

预计生产代码变动约 760 行，测试代码约 650 行。

---

## Project Structure

### Documentation (this feature)

```text
specs/008-list-prejudge/
├── spec.md              # 需求规格说明书
├── plan.md              # 本实施计划（逐条对照 Constitution 5.0.0）
├── research.md          # 现状调研、代码事实 S1–S7、D1–D7 决策与理由
├── data-model.md        # 数据库模型、DTO 契约、状态流转与优先级图
├── quickstart.md        # 自动化测试与真实页面验证步骤清单
├── contracts/
│   ├── local-api.md     # 本机 HTTP 接口规范 (/v1/prejudge, /v1/prejudge/settings)
│   └── plugin-protocol.md # 插件内部通信与标记视觉规范
└── tasks.md             # 任务清单（按依赖拓扑排序）
```

### Source Code (repository root)

```text
src/jet/
├── db/
│   ├── schema.sql           # v8 DDL 同步
│   └── migrations.py        # migrate_to_v8 迁移实现
├── config.py                # Settings 增加 daily_prejudge_limit
├── llm/
│   ├── quota.py             # purpose='prejudge' 独立配额
│   └── prejudge.py          # 列表预判提示词与解析
└── api/
    └── routes.py            # /v1/prejudge 与 /v1/prejudge/settings 路由

extension/src/
├── page-reader.js           # 列表项增加 experience, degree
├── jet-client.js            # 预判相关 API 请求封装
├── background.js            # handleSendList 编排与预判缓存维护
├── page-summary.js          # 四级优先级 buildListMarks
├── marks-layout.js          # 预判标记布局算法同步
├── content.js               # 预判虚线色条与空心标签 DOM 渲染
├── options.html             # 设置页增加列表预判配置卡片
└── options.js               # 列表预判上限加载与保存

tests/
├── unit/
│   ├── test_migrations.py       # v8 迁移与历史数据保留测试
│   ├── test_prejudge_quota.py   # 独立额度计算与耗尽拦截测试
│   └── test_prejudge_prompt.py  # 提示词构建与白名单字段断言
└── api/
    └── test_prejudge_routes.py  # 预判路由契约、过滤逻辑与错误场景

extension/tests/
├── page-summary.test.js         # 四级优先级断言
├── marks-layout.test.js         # 预判标记视觉属性计算测试
└── options-prejudge.test.js     # 设置页纯逻辑测试
```

---

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*无违反宪法原则的设计，已严格遵循 Constitution 5.0.0 所有核心原则。*
