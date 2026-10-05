# Implementation Plan: 列表页规则粗筛 + 独立职位页被动读取

**Branch**: `004-list-screen-job-detail` | **Date**: 2026-09-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-list-screen-job-detail/spec.md`

## Summary

两部分都只在用户自己浏览时被动进行、不向 BOSS 发请求：

1. **列表页规则粗筛（US1，P1）**：列表岗位入库时，服务端按当前画像与重点排查行业规则计算"粗筛提示"（高风险行业 / 低于薪资底线 / 命中不接受条件 / 不去的城市），随列表响应返回；插件把它显示在未完成判断的岗位卡片标记上。规则 2–4 复用现有 `rules.screen()`，行业规则与判断提示词共用同一份规则文件。粗筛只是提示：不入判断表、不给结论、不调大模型、不占额度。
2. **独立职位页被动读取（US2，P2）**：`readBossPage` 增加独立职位页分支，按探测确认的位置读 DOM，读到必需字段就走现有详情入库与判断流程；公司工商登记名只在库里没有公司名时写入；入库后同步聊天页"当前岗位"。读不到显示"此页暂不支持读取"。

数据库结构不变；发给大模型的字段不变（FR-018 公司名来源变化已由用户 2026-09-29 确认）。

## 已定方向（用户已确认，不得修改）

- 宪法解释：粗筛只是提示，不写入判断表、不给结论、不调大模型、不占额度（2026-09-28）。
- 粗筛提示只显示在列表卡片标记上，不做侧边栏汇总；已判断岗位只显示判断标记（2026-09-29）。
- `skills` 参与"不接受条件"匹配。
- 独立职位页第一版不读公司行业；公司名不覆盖库里已有的公司名。
- 不跑 clarify（2026-09-29）。

## Technical Context

**Language/Version**: Python 3.12+（本机 Jet）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建步骤）。

**Primary Dependencies**: 后端 FastAPI、pydantic、sqlite3；插件端原生 Web API 与 Chrome Extension API，无第三方依赖。

**Storage**: SQLite（WAL），**本功能不改表结构**（仍为 user_version 6）。

**Testing**: pytest（现有 `data_dir` / `settings` fixture，不联网）；`node --test extension/tests/*.test.js`，独立职位页 DOM 用手写 `MockElement` 模拟（不引入 jsdom）。

**Target Platform**: macOS 本机；Chrome 稳定版。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 插件）。

**Performance Goals**: 粗筛提示随列表响应一起返回，列表标记出现后 0.5 秒内显示（SC-001）；一页 30 个岗位的粗筛为纯规则计算，不产生额外请求。

**Constraints**: 不向 zhipin.com 发任何请求；不自动打开、刷新任何页面；不读取招聘者、法定代表人、地址等个人信息；页面结构变化时明确提示，不退回读整页文字。

**Scale/Scope**: 单用户；每次列表发送最多 200 个岗位（现有去重上限）；每个岗位的标签与技能各 ≤ 20 项。

## Constitution Check

*GATE: 逐条对照 Constitution（4.0.2）原则 I–XI 与附加约束。设计完成后复核结论不变。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 粗筛服务"提醒"（列表页提示哪些值得点开）；独立职位页服务"读取 → 入库 → 判断"。 | 符合 |
| **II. 数据归属** | 粗筛提示按当前用户画像计算、不持久保存；标签与技能不入库；独立职位页岗位入公共岗位库，判断按用户隔离，沿用现有模型。 | 符合 |
| **III. 插件与本机 Jet 分离** | 粗筛在本机 Jet 计算，插件只显示；插件不保存画像副本；只通过本机接口通信。 | 符合 |
| **IV. 只读，不发请求** | 列表只读已加载的 `jobList`；独立职位页只在用户自己打开后读已显示的内容；画像变化后的重读只读页面已有数据。不导航、不点击、不打开页面。 | 符合 |
| **V. 平台风险控制** | 独立职位页读不到必需字段时显示"此页暂不支持读取"，不退回读整页文字；验证码页不处理。 | 符合 |
| **VI. 耗时任务后台化** | 粗筛是同步纯规则计算，不是判断任务；独立职位页判断沿用后台队列与去重。 | 符合 |
| **VII. 成本有上限** | 按已确认的解释：粗筛是提示不是判断，不调大模型、不占额度，"仅列表信息"岗位仍不判断；独立职位页判断沿用每日上限与去重（用户打开详情是明确动作）。 | 符合（按 2026-09-28 用户确认的解释） |
| **VIII. 测试与数据安全** | 测试用临时数据目录与假大模型；独立职位页测试样本只含结构、不含真实文字。 | 符合 |
| **IX. 每个数字都有定义** | 本功能不新增界面数字。 | 符合 |
| **X. 证据与最小改动** | 读取位置来自 0.2 版两页探测；不改表结构；复用 `rules.screen()`、`handleSendDetail`、`syncChatJobStatusInTabStates`。 | 符合 |
| **XI. 代码组织** | Python 在 `src/jet/`，插件在 `extension/`；行业规则读取移到 `src/jet/domain/industry.py`，依赖方向 llm → domain。 | 符合 |

## 待用户拍板的决定（2026-09-29 已决定：1 选 A，2 选 A）

1. **粗筛标记的样子**（R5）
   - **A（推荐）**：色条用一种与四档结论都不同的中性色（灰蓝）；小字标签"粗筛：提示1 · 提示2"，放不下时截断，鼠标悬停显示全部提示。
   - **B**：只显示第一条提示，后面写"等 N 项"，悬停显示全部。
2. **画像改完后的刷新**（R4）
   - **A（推荐）**：保存画像后，已打开的搜索列表页自动重读一次页面已有数据，提示立即按新画像更新。
   - **B**：不自动重读，等下次刷新页面才更新。

## 改动范围与预计改动量

| 部分 | 文件 | 改动 | 预计行数（不含测试） |
|---|---|---|---|
| A 粗筛 | `src/jet/domain/industry.py`（新） | 规则文件读取与行业匹配（从 `prompt_v5.py` 移入） | ~70 |
| A | `src/jet/llm/prompt_v5.py` | 改为从 `domain/industry.py` 导入，函数名不变 | ~−60 / +5 |
| A | `src/jet/domain/rules.py` | 新增 `screen_hints()`：行业提示 + 复用 `screen()` 生成其余提示 | ~50 |
| A | `src/jet/api/routes.py` | 请求字段 `job_labels`、`skills`；列表分支读取画像、计算并返回 `screen_hints` | ~30 |
| A | `extension/src/page-reader.js` | 列表读取 `jobLabels`、`skills` | ~15 |
| A | `extension/src/background.js` | 列表 payload 带标签；保存 `screen_hints`；保存画像后清空 `listSent` 并重读列表页 | ~30 |
| A | `extension/src/page-summary.js`、`marks-layout.js`、`content.js` | 未判断岗位的提示标记（色条 + 截断标签 + 悬停全文） | ~50 |
| B 独立职位页 | `extension/src/page-reader.js` | `job_detail_page` 分支按位置表读 DOM | ~60 |
| B | `extension/src/scheduler.js` | 独立职位页放行 `send`，重读用完显示"此页暂不支持读取" | ~20 |
| B | `extension/src/background.js` | payload 带 `company_legal_name`；入库后同步聊天状态 | ~20 |
| B | `src/jet/api/routes.py`、`src/jet/domain/jobs.py` | `company_legal_name` 字段，只在无公司名时写入 | ~20 |
| 测试 | `tests/unit/`、`tests/api/`、`extension/tests/` | 新增测试文件为主 | ~300 |

合计生产代码约 450 行（A 约 250、B 约 120，其余为移动代码），测试约 300 行。

## Project Structure

### Documentation (this feature)

```text
specs/004-list-screen-job-detail/
├── spec.md
├── plan.md              # 本文件
├── research.md          # 现状核实与决定 R1–R10
├── data-model.md        # 粗筛提示、临时字段、jobs 变化（无迁移）
├── quickstart.md        # 自动测试与真实页面验证 Q1–Q10
├── contracts/
│   └── local-api.md     # POST /v1/observations 与插件内部消息的变化
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks 生成
```

### Source Code (repository root)

```text
src/jet/
├── domain/
│   ├── industry.py      # 新：重点排查行业规则读取与匹配
│   ├── rules.py         # screen_hints()
│   └── jobs.py          # company_legal_name
├── api/routes.py        # 列表粗筛、新请求字段
└── llm/prompt_v5.py     # 改为导入 domain/industry.py

extension/src/
├── page-reader.js       # 列表标签字段；独立职位页 DOM 读取
├── scheduler.js         # 独立职位页放行
├── background.js        # payload、画像变化重读、聊天状态同步
├── page-summary.js      # 提示标记
├── marks-layout.js
└── content.js           # 提示标记渲染（与 marks-layout.js 保持一致）

tests/unit/, tests/api/, extension/tests/   # 新增测试
```

**Structure Decision**: 沿用现有单仓库结构，只新增 `src/jet/domain/industry.py` 一个模块。

## Complexity Tracking

无违反原则的设计，不需要说明。
