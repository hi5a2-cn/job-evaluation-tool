# Implementation Plan: BOSS 岗位只读插件 + 本机判断（第一阶段）

**Branch**: `002-boss-readonly-extension` | **Date**: 2026-09-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-boss-readonly-extension/spec.md`

## Summary

Chrome 插件（MV3）在用户正常浏览 BOSS 搜索列表页时，由后台 service worker 用 `chrome.scripting.executeScript(world: "MAIN")` 一次性读取页面 Vue 实例上已加载的岗位数据（实验插件 v0.0.2 已在真实页面验证），经配对令牌发给只监听 `127.0.0.1` 的本机 Jet。
本机 Jet 是一个 Python 进程（FastAPI + uvicorn + 标准库 sqlite3），负责：岗位入公共库（按 `encryptJobId` 合并、保留版本）、记录个人查看、规则粗筛、在进程内后台队列里调用大模型做"适合 / 不适合 + ≤3 条理由"的二元判断，并用事务保证每日调用上限。
插件用 Shadow DOM 标记或侧边栏展示结果；所有"不知道"的状态都有独立文案，不会显示成"不适合"。

## 已确认的决定（2026-09-24 用户拍板）

> 详细比较见 [research.md](research.md) §1–§3。

1. **大模型**：默认 **DeepSeek `deepseek-flash`**。接入层按 OpenAI 兼容接口写，换模型只改配置。开发到真实判断阶段时，用 **10 个真实岗位**对比 DeepSeek 和通义千问 `qwen3.8-flash`，再确认默认值。
2. **本机 Jet**：Python + FastAPI + uvicorn，`uv` 管理；第一版**手动运行** `jet serve`，开机自启（launchd）以后再说。配对：终端 `jet pair` 显示 6 位一次性码 → 插件设置页输入 → 换取长期令牌；之后每个请求核对令牌 + 扩展来源。
3. **从 jet-demo 搬什么**：同意 research.md §3 清单——薪资解析（修 3 处缺陷后搬）、日志脱敏函数（直接搬）、测试隔离思路（重写）、大模型 HTTP 重试骨架（重写）、标题清洗和部分规则词表（按需搬）。**V3 评分引擎和 JEV 裁判不搬**。

## US7 判断质量改进（2026-09-24 修订，用户确认）

**起因（FACT，真实页面）**：主线跑通后，"银行合作产品运营助理"（职责：银行营销活动策划、对接银行、配合商务）被判为"适合"，理由只是对照画像关键词打勾。二元结论 + 关键词画像不足以判断"这份工作实际干什么"。

**决定**：

1. **先讲事实，再对照偏好**（提示词 v2）：一次调用输出事实（白话职责、工作类型、销售成分、经验门槛与背景对照、加班信号，每项引用原句）→ 推导 → 三档建议（适合 / 存疑 / 不适合）。依据职责不依据职位名；先摘变相销售信号原句再定销售成分。v1 提示词保留，只用于评测对比。
2. **画像**加"工作内容偏好""我的背景"两个自由文本栏（各 ≤ 500 字），只给大模型。
3. **引用核对**在 Jet 端做：规范化后子串匹配，找不到标 `found=false`，不重试（评测指标之一）。
4. **标注**：卡片上用选择按钮标事实（工作类型、销售成分、经验是否满足、加班信号），总体结论与备注选填；每个岗位版本保留最新一条；与 Jet 不一致即纠正。本轮只记录纠正，不作为提示词示例。
5. **评测** `jet eval run`：组合 A（v1）、B（v2 + deepseek-flash 关思考）、C（v2 + 开思考）、D（TypeSafe Jev 答事实选择题，最高概率 < 阈值判存疑，默认 0.6；再由 deepseek-flash 关思考写概括、引用、推导，Jev 的事实作为输入）。评测调用 `purpose='eval'`，单独计数，每次运行上限默认 300；结果存 `<数据目录>/evals/`，Jev 概率一并保存，阈值可离线重算。
6. **日常判断默认**：评测前用组合 B；评测后按事实准确率与"误判为适合"次数选默认组合（改配置 `JET_JUDGE_ENGINE`，不改代码）。Jev 若被选中，再核实单价并加独立每日上限。
7. **提示词版本**：判断记录 `prompt_version`；当前版本（配置 `JET_PROMPT_VERSION`，默认 `v2`）更高时标"可能过时（判断方式已更新）"，不自动重判。
8. **结构迁移**：`PRAGMA user_version` 0/1 → 2；启动时先备份 `jet.db` 到同目录，再在事务中重建 `judgements`、`llm_calls`（SQLite 不能修改 CHECK / NOT NULL），校验行数与外键后提交（见 data-model.md"结构迁移"）。
9. **依赖**：新增 `typesafe-sdk`（异步客户端，会带入 `httpx2`）；Jev 调用封装在 `jet/llm/jev.py`，测试中注入假客户端，不联网。参考 jet-demo `evaluators/jev_judge` 的调用方式（`system_one(state, questions={key: Choice(instructions, criteria)})`，返回每题选项与概率），不搬代码。jet-demo 旧的 Jev 对比使用合成标注，不作为依据。
10. **超时**：开启思考的调用超时单独配置（默认 60 秒）；v2 输出更长，`max_tokens` 调到 1200。

**Constitution Check（US7 复查）**

| 原则 | 满足方式 | 结果 |
|---|---|---|
| I 主线优先 | 服务主线"判断"与"反馈"两步 | ✅ |
| II 数据归属 | `labels` 为个人表，`user_id NOT NULL`；评测结果只在本机数据目录 | ✅ |
| IV 只读 | 标注区在插件自己的 Shadow DOM 内，不改 BOSS 元素 | ✅ |
| VII 成本上限 | 评测调用独立计数与上限，不占每日判断额度；评测只在用户运行命令时发生；Jev 本轮只用于评测，接入日常前另设上限 | ✅ |
| VIII 测试与数据安全 | 测试用假 DeepSeek 与假 Jev；迁移测试用临时库；`JET_TYPESAFE_API_KEY` 在数据目录 `.env`；评测集真实职位描述不进 Git | ✅ |
| IX 每个数字有定义 | spec 指标表新增评测集标注数、事实准确率、误判为适合次数、引用未找到率、评测费用 | ✅ |
| X 证据 | Jev 单价 NOT VERIFIED；默认组合由评测结果决定并记录到 research.md | ✅ |

## US8 我的岗位库（2026-09-25，用户确认）

- 卡片加"收藏 / 已投递 / 不考虑"（互斥、可取消）；当前状态表 `job_status` + 变化记录表 `job_status_events`（Issue #3 的数据来源，本轮只记录）。
- 插件新页面 `extension/src/myjobs.html`：筛选 全部（标过状态）/ 收藏 / 已投递 / 不考虑 / 最近看过（Jet 判断过的岗位，按最后查看时间倒序）；入口：设置页顶部链接、工具栏图标。
- 公司名：页面读取增加公司字段（列表大概率为 `brandName`，详情字段待确认，NOT VERIFIED），存 `jobs.company_name`（公共岗位信息，不属于个人）；没读到显示"公司未读取"。
- **原则 IV 说明**：点击"我的岗位库"中的一行，插件用 `chrome.tabs.create` 在新标签页打开 BOSS 原岗位链接。**用户点击触发的打开链接不属于自动化，不违反原则 IV**；插件从不在没有用户点击的情况下导航或打开页面，也不在打开的页面上做任何操作。

## Technical Context

**Language/Version**: Python 3.12+（本机 Jet）；JavaScript（ES2022 模块，插件，无构建步骤）

**Primary Dependencies**: FastAPI、uvicorn、httpx（调用大模型，OpenAI 兼容 `/chat/completions`）、pydantic（随 FastAPI）、typesafe-sdk（US7，仅评测调用 TypeSafe Jev）；插件无第三方依赖

**Storage**: SQLite（标准库 `sqlite3`，WAL 模式），单文件，位于可注入的数据目录（默认 `~/Library/Application Support/Jet/`）

**Testing**: pytest（+ FastAPI TestClient、假大模型、临时数据目录）；插件纯函数用 `node --test`；真实环境测试需 `JET_LIVE=1` 显式开启

**Target Platform**: macOS（Apple Silicon）本机；Chrome 稳定版（MV3，需要 `chrome.sidePanel`，Chrome 114+）

**Project Type**: 浏览器插件 + 本机后台服务（单仓库，两个目录）

**Performance Goals**: 已判断岗位 1 秒内显示（SC-001，本机 SQLite 查询 + 本机 HTTP，余量很大）；新岗位 10 秒内出结论（受大模型响应时间约束，调用超时设 20 秒，超时显示"判断失败"）

**Constraints**: 只绑定 `127.0.0.1`；插件不向 zhipin.com 发任何请求；每日大模型上限默认 50 且在事务中强制；测试不联网、不需 Key

**Scale/Scope**: 1 个用户；每天约几十到几百个列表岗位、≤50 次大模型调用；岗位库一年量级 1 万条以内

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本计划如何满足 | 结果 |
|---|---|---|
| I 主线优先 | 每个模块对应主线一步：读取（page reader）→ 入库（observations）→ 判断（rules + llm worker）→ 提醒（标记 / 侧边栏）→ 反馈（本版只有"重新判断 / 重试"，其余反馈进 Issues） | ✅ |
| II 数据归属 | `jobs`/`job_versions` 无用户字段；个人表 `user_id NOT NULL` + 外键；启动自检"无主人记录 = 0"（data-model.md） | ✅ |
| III 插件与本机 Jet 分离 | 插件只读、发送、显示；判断、画像、额度都在 Jet。画像编辑界面在插件页里，但数据只存 Jet。唯一接口是 contracts/local-api.md，只绑定 127.0.0.1、只接受已配对插件 | ✅ |
| IV 只读 | `host_permissions` 只有 `zhipin.com`（读取用）和 `127.0.0.1`；service worker 只 fetch `127.0.0.1`（加单元测试扫描源码中的 fetch 目标）。读取函数只读属性不写；不导航、不点击、不滚动；不包装页面函数。薪资空 → "薪资不可见" | ✅ |
| V 平台风险 | 无任何页面操作；验证码 / 空白页时插件静默；读不出 → "页面无法识别"，无整页文字降级；不对抗开发者工具检测 | ✅ |
| VI 后台化 | `POST /v1/observations` 立即返回；判断在进程内 asyncio 队列里做；同一用户同一岗位 in-flight 合并 + 数据库部分唯一索引；打开插件界面只读状态，不触发判断 | ✅ |
| VII 成本上限 | 规则先筛；`(user, job_version, profile_version)` 唯一，已判断不重复调用；每日上限在 `BEGIN IMMEDIATE` 事务里预占；`list_only` 不判断；大模型额度独立配置（不与其他数量共用） | ✅ |
| VIII 测试与数据安全 | 数据目录由参数 / 环境变量注入；默认测试用假大模型和临时目录；conftest 在测试结束时检查真实数据目录无新文件；页面样本脱敏后入库（只保留岗位字段，去掉 securityId、lid、招聘者姓名）；令牌、API Key、数据库都在数据目录，不在仓库 | ✅ |
| IX 每个数字有定义 | 插件只显示 spec 指标表中的 5 个数字；`/v1/status` 字段与指标表一一对应 | ✅ |
| X 证据与最小改动 | research.md 每条结论标 FACT / INFERENCE / NOT VERIFIED；滚动加载、未登录薪资留到 quickstart R5/R6 实测 | ✅ |
| XI 代码组织 | Python 在 `src/jet/`（包名 `jet`）；插件在 `extension/`；从 jet-demo 只"复制并附带测试"，不 import | ✅ |
| 附加约束 | Python + SQLite + 127.0.0.1 HTTP；Chrome MV3；画像编辑在插件页；不部署服务器 | ✅ |

**Phase 1 设计后复查**：data-model、两份 contracts、quickstart 均未引入新的违反项。一个需要注意的点（不是违反）：插件在 MAIN world 执行读取函数，页面理论上能察觉脚本执行；这与实验插件的做法相同，已接受，并在 research.md §4 记录。→ **通过**。

## Project Structure

### Documentation (this feature)

```text
specs/002-boss-readonly-extension/
├── plan.md              # 本文件
├── research.md          # Phase 0：模型选择、本机服务、配对、jet-demo 迁移、读取方式
├── data-model.md        # Phase 1：表结构、状态机、额度扣减
├── quickstart.md        # Phase 1：自动测试 + 真实页面验证清单
├── contracts/
│   ├── local-api.md     # 本机 Jet HTTP 接口 v1
│   └── page-reader.md   # 页面读取返回结构、插件内部消息、页面标记规则
└── tasks.md             # Phase 2（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
pyproject.toml               # uv 项目；入口脚本 jet = jet.cli:main
src/jet/
├── cli.py                   # jet serve | jet pair | jet stats
├── config.py                # 数据目录、端口、模型、单价、每日上限（环境变量 / 数据目录内 .env）
├── db/
│   ├── schema.sql
│   ├── migrations.py        # user_version 迁移 + 备份（US7）
│   └── store.py             # 连接、事务辅助；启动时 running → interrupted
├── domain/
│   ├── normalize.py         # NFKC、全半角、城市后缀
│   ├── salary.py            # 薪资解析（源自 jet-demo，修正后附测试）
│   ├── jobs.py              # 入库、版本判定
│   ├── profiles.py
│   ├── rules.py             # 规则粗筛
│   ├── quotes.py            # 引用核对（US7）
│   ├── labels.py            # 标注 / 纠正（US7）
│   └── judgements.py        # 状态机、可能过时计算
├── llm/
│   ├── client.py            # OpenAI 兼容 httpx 客户端、超时、重试
│   ├── prompt.py            # 提示词 v1：二元判断（保留用于评测）
│   ├── prompt_v2.py         # 提示词 v2：事实 → 推导 → 三档建议（US7）
│   ├── jev.py               # TypeSafe Jev 事实选择题（US7，评测用）
│   └── quota.py             # 事务内预占 / 退还
├── eval/
│   └── runner.py            # jet eval run / report（US7）
├── worker.py                # 后台线程队列、in-flight 合并
├── api/
│   ├── app.py               # FastAPI 应用、只绑定 127.0.0.1
│   ├── auth.py              # 配对码、令牌、Origin 校验
│   └── routes.py
└── logging.py               # 日志脱敏（源自 jet-demo mask_sensitive_data）

tests/
├── conftest.py              # 临时数据目录 + 真实目录污染检查
├── fixtures/boss/           # 脱敏后的页面读取样本（JSON）
├── unit/                    # normalize、salary、rules、judgements、quota
├── api/                     # TestClient：认证、observations、额度并发
└── live/                    # JET_LIVE=1 才运行：真实大模型一次调用

extension/
├── manifest.json            # MV3；host_permissions: zhipin.com、127.0.0.1
├── src/
│   ├── background.js        # service worker：读取调度、去重、调用 Jet、状态映射
│   ├── page-reader.js       # readBossPage（MAIN world 执行，纯读取）
│   ├── content.js           # ISOLATED：MutationObserver 通知、Shadow DOM 标记
│   ├── jet-client.js        # 只允许 127.0.0.1 的 fetch 封装
│   ├── view-state.js        # Jet 响应 → 界面状态（纯函数）
│   ├── options.html / options.js     # 配对、画像、每日上限、标记开关
│   └── sidepanel.html / sidepanel.js # 本页已判断岗位、今日剩余次数
└── tests/                   # node --test：page-reader（用 fixtures）、view-state、jet-client 目标检查
```

**Structure Decision**: 单仓库两部分：`src/jet/`（Python 包，src 布局避免从仓库根目录误导入，吸取 jet-demo C4 教训）和 `extension/`（原生 JS，无构建）。`experiments/boss-probe-ext/` 保留为实验记录，不被引用；它的读取逻辑"复制并附带测试"进 `extension/src/page-reader.js`。

## Complexity Tracking

无违反项，不需要填写。
