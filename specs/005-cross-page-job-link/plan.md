# Implementation Plan: 岗位信息跨页面联通（第一批）

**Branch**: `005-cross-page-job-link` | **Date**: 2026-09-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-cross-page-job-link/spec.md`

## Summary

本功能补齐四个位置，让同一个岗位的**投递状态**（收藏 / 已投递 / 不考虑）在各处一致可见、可改，并让聊天中遇到的岗位也能进入岗位库：

1. **聊天页侧边栏投递状态（US1，P1）**：侧边栏当前岗位卡片增加投递状态按钮组（收藏/已投递/不考虑，再点取消），复用现有投递状态接口与写入逻辑；沿用 003 防乱序规则；在库但未判断的岗位显示提示原文"点「查看职位」获取详情并判断"（FR-001–FR-004、FR-015）。
2. **搜索列表卡片显示投递状态（US2，P1）**：列表卡片标记布局 `planMarks` 提取 `listJudgements` 中的 `my_status`，在卡片顶部留白行渲染状态胶囊标识；与现有的 Jet 结论标识或粗筛提示水平并列、同时可见、互不遮挡；右侧详情区改动状态（手动、自动标记或撤销）后，同一页面上的卡片标识在 1 秒内即时联动重绘，无需刷新页面（FR-005–FR-008）。
3. **独立职位页「立即沟通」自动标记已投递（US3，P2）**：`decideAutoApply` 放开 `/job_detail/` 路径；岗位 ID 严格比对 004 被动读取结果与 URL 路径，一致才标记；按钮检测最近的 `a` 或 `button` 且文字严格为"立即沟通"；`ka` 存在且不包含该 ID 则跳过；成功后弹出约 8 秒可撤销提示；保存请求由后台 Service Worker 异步发出，避免页面跳转中断（FR-009–FR-010）。
4. **聊天岗位自动被动入库（US4，P2）**：在聊天页切换检测读到有效岗位 ID 与职位名时，后台 Service Worker 被动调用新增端点 `POST /v1/chat/job`，将岗位以 `completeness='list_only'` 且 `current_version_id=NULL` 写入 `jobs` 表（绝不写 `job_versions`），并按用户写入 `job_chat_seen` 记录；岗位库 `all_jobs` 统计口径将聊天记录纳入并集；绝不调用判断、绝不粗筛、绝不调大模型、不占额度；已有岗位不覆盖已有信息与状态（FR-011–FR-018）。

所有位置读写的都是本机公共岗位库中同一份状态记录；发往外部大模型（DeepSeek）的内容完全不变（FR-019–FR-020）。

---

## 已定设计（Claude 决定，按此实施）

- **D1 数据**：聊天入库岗位在 `jobs` 表插入一行：`completeness='list_only'`、`current_version_id=NULL`、`company_name=页面公司名`（空则 NULL）、`first_seen_at/last_seen_at=now`；不写 `job_versions`（该表 `city/salary_visible/content_hash` 为 NOT NULL，聊天页无此数据）。新增按用户表 `job_chat_seen(user_id, job_id, chat_title TEXT NOT NULL, first_seen_at, last_seen_at, PRIMARY KEY(user_id, job_id))`，在 `schema.sql` 用 `CREATE TABLE IF NOT EXISTS` 新增（与现有 `_ensure_current_schema` 方式一致，保持 user_version 6）。岗位已存在时：不改 `jobs` 已有列（`company_name` 仅在原为 NULL 时补齐）、不改判断和状态；只 upsert `job_chat_seen`（首次插入，之后仅更新 `last_seen_at`，`chat_title` 仅在原为空时补）。
- **D2 标题**：岗位条目的 `title = 当前版本 title`，若 `current_version_id` 为 NULL 则用 `job_chat_seen.chat_title`。已在 `research.md` 盘点全仓库所有读取 `current_version_id` 或 `JOIN job_versions` 的位置（文件:行号），重点修复 `jobs.py` 中 004 详情与列表补录版本时的 NULL 崩溃风险。
- **D3 岗位库口径**：`all_jobs` = `有状态 ∪ 判断过 ∪ 有非空 HR 记录 ∪ job_chat_seen 中有该用户记录`。服务端岗位条目新增布尔字段 `seen_in_chat`；插件 `chat-view.js` 的 `isJobInLibrary` 同步加入该条件。岗位库列表对 `current_version_id` 为 NULL 的岗位能正常展示（薪资、城市等为空），并标明"尚未判断"。
- **D4 接口**：新增 `POST /v1/chat/job`，请求体 `{platform_job_id, title, company_name}`，鉴权同现有接口；幂等；返回与 `/v1/judgements` 相同结构的岗位条目；绝不调用 `request_judgement`/`rejudge`/规则粗筛/任何 LLM，不写 `job_status`。`title` 为空或 `platform_job_id` 为空返回 422。
- **D5 调用点**：插件在聊天页检测到当前会话岗位（现有切换检测，读到 `encrypt_job_id` 且职位名非空）时，每次切换由 `background.js` 调用一次 `POST /v1/chat/job`，再按现有方式取岗位条目渲染侧边栏；侧边栏关闭时也要入库。未配对/服务不可用时不入库、不重试。
- **D6 侧边栏状态**：sidepanel 当前岗位卡片加状态按钮组（收藏/已投递/不考虑，再点取消），复用现有设置状态的接口与 jet-client 方法；防乱序沿用 003 规则；在库未判断时显示提示原文"点「查看职位」获取详情并判断"。
- **D7 列表卡片状态**：`page-summary.js` `buildListMarks` 用 `listJudgements` 中已有的 `my_status` 生成状态标识，`marks-layout.js` 规划布局使其与结论/粗筛标识共存不遮挡；`content.js` 中所有修改 `currentDetail.my_status` 的位置（手动改状态、立即沟通自动标记、撤销）成功后同步更新列表数据并重绘该卡片标识。
- **D8 独立职位页立即沟通**：`auto-apply.js` `decideAutoApply` 放开 `/job_detail/` 路径；岗位 ID 取 004 `job_detail` 读取结果并与地址 `/job_detail/<id>.html` 中的 ID 核对，一致才标记；读取失败不标记；按钮识别用"最近的 a/button 且文字 trim 后恰好为 立即沟通"，`ka` 若存在且不含该 ID 则不标记；保存请求由后台 Service Worker 异步发出，避免页面卸载中断（确认现有 FR-058 架构已完全满足此点）。
- **D9 外发不变**：本功能完全不修改任何发往 DeepSeek 的字段或提示词，所有提示词生成文件均保持未触及。

---

## Technical Context

**Language/Version**: Python 3.12+（本机 Jet）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建步骤）。

**Primary Dependencies**: 后端 FastAPI、pydantic、sqlite3；插件端原生 Web API 与 Chrome Extension API（`chrome.runtime`、`chrome.scripting`），无第三方依赖。

**Storage**: SQLite（WAL 模式），在 `schema.sql` 中新增 `job_chat_seen` 表（`CREATE TABLE IF NOT EXISTS`），保持 `user_version 6`，由 `_ensure_current_schema` 自动生效，无需升版本迁移脚本。

**Testing**:
- 后端：`uv run --frozen pytest`（覆盖 `POST /v1/chat/job`、`job_chat_seen` upsert、`all_jobs` 口径、`jobs.py` NULL 版本安全补录、`my-jobs` 展示与检索）；
- 插件单元测试：`node --test extension/tests/*.test.js`（覆盖 `decideAutoApply`、`isJobInLibrary`、`formatChatJobStatus`、`buildListMarks`、`planMarks`）；
- 静态语法校验：`node --check extension/src/*.js`。

**Target Platform**: macOS 本机；Chrome 稳定版（Manifest V3）。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 插件）。

**Performance Goals**: 列表详情区修改状态后同一卡片状态标识 1 秒内无感重绘（SC-003）；聊天切换入库完全异步进行，不增加页面切换阻塞时间；列表状态标识利用已有内存数据，不产生额外网络请求。

**Constraints**: 不向 zhipin.com 发送任何网络请求；不代替用户自动点击或导航；不改变发往 DeepSeek 的任何字段与提示词；未配对或服务未运行时静默容错、不重试。

**Scale/Scope**: 单用户；支持快速切换聊天；每个岗位仅存一份状态；无复杂跨标签即时推送（其他标签下次刷新或重查同步）。

---

## Constitution Check

*GATE: 逐条对照 Constitution（4.0.2）原则 I–XI 及附加约束。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 投递状态多处同步服务于"提醒"与"反馈"；聊天岗位自动入库让 HR 主动发起的沟通也能进入"入库"与"反馈"，打通从聊天到岗位库的断点。不自动投递、不自动发消息。 | 符合 |
| **II. 数据归属从第一天明确** | 岗位汇入公共库 `jobs`（以平台岗位 ID 去重）；聊天遇到记录 `job_chat_seen` 与投递状态 `job_status` 全部按用户严格隔离，每条记录均归属 `user_id`。 | 符合 |
| **III. 插件与本机 Jet 分离** | 插件只负责被动读取页面 ID/职位名/公司名并转发给本机 Jet；状态保存与岗位库管理由本机 Jet 负责；两者仅通过 127.0.0.1 HTTP 接口交换数据。 | 符合 |
| **IV. 只读，不向招聘平台发起任何请求** | 插件和本机 Jet 均不向 BOSS 发任何请求；聊天入库只读页面已加载数据；独立职位页立即沟通只监听用户本人的真实点击（`capture/passive`），不阻止、不延迟、不代替点击。 | 符合 |
| **V. 平台风险控制** | 绝对不自动打招呼、不自动投递、不代替用户操作；页面结构无法识别时不入库、不标记；验证码或风控页面不处理。 | 符合 |
| **VI. 耗时任务后台化** | 聊天岗位入库是极轻量的本地数据库写入，不做任何耗时的大模型判断；独立职位页「立即沟通」通过后台 Service Worker 异步请求，不阻塞页面交互。 | 符合 |
| **VII. 成本有上限** | 聊天入库的岗位信息不全（仅列表级信息），绝对不做规则粗筛、不调用大模型、不创建判断、不占额度（FR-012）；本功能的大模型调用与额度消耗完全为 0。 | 符合 |
| **VIII. 测试与数据安全** | 测试在临时目录与虚拟数据库运行，不联网、不需要 API Key；不记录任何招聘者姓名或个人隐私数据；测试绝不写入真实数据目录。 | 符合 |
| **IX. 每个数字都有定义** | 岗位库 `all_jobs` 统计口径明确新增"在聊天中出现过"（`job_chat_seen`），指标定义清晰无歧义。 | 符合 |
| **X. 证据与最小改动** | 仅改动实现四处联通所必需的代码；全仓库所有 `current_version_id` 位置已逐行排查证据并列入表格；不升表结构大版本。 | 符合 |
| **XI. 代码组织** | 后端代码位于 `src/jet/`，插件代码位于 `extension/`；模块间保持清晰的依赖单向性，不引入循环依赖。 | 符合 |
| **附加约束** | 本机 Python 3.12 + SQLite + 127.0.0.1；Chrome Manifest V3 扩展；不部署云端服务器。 | 符合 |

---

## 改动范围与预计改动量

| 模块 | 文件 | 改动内容 | 预计行数（不含测试） |
|---|---|---|---|
| 存储 | `src/jet/db/schema.sql` | 新增 `job_chat_seen` 表 DDL（`CREATE TABLE IF NOT EXISTS`） | ~12 |
| 领域核心 | `src/jet/domain/jobs.py` | 补齐合并逻辑：当 `current_version_id is None` 时安全补录第 1 版，避免解构崩溃 | ~25 |
| 领域核心 | `src/jet/domain/job_status.py` | `all_jobs` 口径增加 `job_chat_seen` 并集；`q` 检索支持 `chat_title`；条目提取支持 `chat_title` 回退 | ~35 |
| API 路由 | `src/jet/api/routes.py` | 新增 `POST /v1/chat/job` 端点；`_job_entry` 增加 `seen_in_chat` 与 `title` 回退 | ~60 |
| 插件阅读器 | `extension/src/page-reader.js` | 扩展聊天轻量读取，一并返回 `job_title` 与 `company_name` 供入库使用 | ~25 |
| 插件后台 | `extension/src/background.js` | 聊天切换广播时调用 `POST /v1/chat/job`；侧边栏关闭时依然入库；状态变动后下发更新后的标记 | ~35 |
| 插件纯函数 | `extension/src/chat-view.js` | `isJobInLibrary` 增加 `seen_in_chat`；`formatChatJobStatus` 增加在库未判断提示文案 | ~20 |
| 插件纯函数 | `extension/src/auto-apply.js` | `decideAutoApply` 放开 `/job_detail/` 路径并比对 URL 与读取到的岗位 ID | ~25 |
| 插件纯函数 | `extension/src/page-summary.js` | `buildListMarks` 提取条目中的 `my_status` 生成状态标识字段 | ~25 |
| 插件布局 | `extension/src/marks-layout.js` | `planMarks` 支持状态胶囊与结论/粗筛标签并列排布、不遮挡 | ~40 |
| 插件内容脚本 | `extension/src/content.js` | 同步 `auto-apply.js` 与 `marks-layout.js` 改动；状态修改成功后即时刷新卡片标记；放宽沟通按钮匹配 | ~60 |
| 插件侧边栏 | `extension/src/sidepanel.js` | 当前岗位卡片增加状态按钮组（收藏/已投递/不考虑）；绑定防乱序与保存逻辑 | ~50 |
| 插件岗位库 | `extension/src/myjobs.js` | 列表条目对 `verdict` 为空的未判断岗位显示"尚未判断"徽章，薪资城市留空 | ~15 |
| 测试代码 | `tests/api/`, `tests/unit/`, `extension/tests/` | 新增后端与插件端自动化测试 | ~350 |

生产代码预计变动约 420 行，测试代码约 350 行。

---

## Project Structure

### Documentation (this feature)

```text
specs/005-cross-page-job-link/
├── spec.md
├── plan.md              # 本文件
├── research.md          # 现状排查、全库 current_version_id 盘点与决定
├── data-model.md        # job_chat_seen 表、jobs 变动规则、查询口径与条目模型
├── quickstart.md        # 自动化测试命令与真实页面验证清单 Q1–Q12
├── contracts/
│   ├── local-api.md     # POST /v1/chat/job 契约与已有端点变更
│   └── page-behavior.md # 四项页面行为契约（输入、输出、不做什么）
└── tasks.md             # 后续 /speckit-tasks 生成
```

### Source Code (repository root)

```text
src/jet/
├── db/
│   └── schema.sql       # 新增 job_chat_seen 表
├── domain/
│   ├── jobs.py          # 补全 current_version_id 为 NULL 时的版本补录逻辑
│   └── job_status.py    # all_jobs 口径扩充、检索与标题回退
└── api/
    └── routes.py        # POST /v1/chat/job 与 _job_entry 变更

extension/src/
├── page-reader.js       # 聊天轻量读取增加职位名与公司名
├── background.js        # 聊天切换自动入库调用、状态同步下发
├── chat-view.js         # isJobInLibrary 扩充、在库未判断提示
├── auto-apply.js        # 放开 /job_detail/ 路径与双重 ID 核对
├── page-summary.js      # buildListMarks 提取状态标记
├── marks-layout.js      # 状态胶囊与结论标签并排布局
├── content.js           # 独立职位页按钮捕获、状态修改后即时局部重绘
├── sidepanel.js         # 侧边栏当前岗位卡片状态按钮组与交互
└── myjobs.js            # 岗位库列表对未判断岗位的展示适配

tests/
├── api/test_chat_job.py # POST /v1/chat/job 接口与鉴权/幂等测试
├── unit/test_jobs.py    # jobs.py NULL 版本补录与安全合并测试
└── unit/test_status.py  # all_jobs 统计口径与 job_chat_seen 检索测试

extension/tests/
├── auto-apply.test.js   # 独立职位页自动标记纯函数决策测试
├── chat-view.test.js    # 在库判定与未判断状态展示测试
├── page-summary.test.js # 状态标记提取测试
└── marks-layout.test.js # 状态胶囊与结论标签并排不遮挡布局测试
```

**Structure Decision**: 沿用现有单仓库体系（`src/jet/` + `extension/`），不新增额外的顶层目录。

---

## Complexity Tracking

无违反原则的设计，不需要说明。
