# Implementation Plan: 判断时建议投哪份简历

> **2026-10-06 补记（体检第 61 条）：简历部分已被 009 取代。** 009 起简历在设置页上传 PDF：上传时把**脱敏后的简历正文（最多 8000 字）**发给 DeepSeek 生成「简历画像」，判断时发送画像（编号简历 1/2/3，不发简历名称）。本文件里「不发送任何简历内容」「只发编号与适合的岗位类型」的说法已不成立，发送内容以 `specs/009-resume-pdf/` 为准；模型只输出简历编号 `slot` 和理由，接口返回的简历建议为 `{slot, name, reason}`（`name` 由本机按编号填回），不再有方向代号和文件名；文中提到的 `resume_directions.json` 已删除。

> **2026-09-29 修订**：简历方向不再放在仓库配置文件 `resume_directions.json`，改为设置页"我的简历"（本机数据库 `resume_slots`，编号 1–3，只发编号与适合的岗位类型）；从严行业改为设置页勾选（`strict_industry_selection`，迁移 v7）。本文件中涉及 `resume_directions.json` 与方向代号（`ai_ops` 等）的设计已被 `tasks.md`"2026-09-29 修订"一节取代，以该节与 `spec.md` 为准。


**Branch**: `006-resume-suggestion` | **Date**: 2026-09-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-resume-suggestion/spec.md`

## Summary

求职者在招聘平台上有针对不同方向的 3 份简历（AI/运营、数据/技术支持、动物/生命科学）。本功能让大模型在现有岗位判断的同一次调用中，顺带给出**建议投哪一份简历（三选一）和一句理由**。

1. **零调用与额度成本（FR-002, SC-002）**：在既有的大模型判断 Prompt 中追加简历方向说明，由模型同一次输出返回，不增加调用次数、不增加 API 成本；
2. **零个人隐私外泄（FR-003, SC-004）**：发给模型的只有代号、方向名与适合岗位类型说明，绝对不发文件名（含姓名）或简历内容；由本机按本地配置文件换成文件名展示；
3. **老旧判断无缝过渡（FR-005, SC-001）**：升级提示词版本至 `v6`，但将"方法变了（可能过时）"比较基准定为基准 `v5`，所有历史 `v5` 判断不标"可能过时"、不自动重跑，仅在用户主动点"重新判断"时升级为 `v6` 并生成简历建议；
4. **两处核心界面呈现（FR-007, US1）**：岗位卡片（搜索列表右侧详情区、独立职位页）与聊天页侧边栏「当前岗位」展示"建议投：<文件名>"，悬停或展开看理由；结论为"不建议投"（`skip`）时屏蔽显示；
5. **聊天页 HR 索要简历突出提示（FR-008, US3）**：在 BOSS 聊天页，复用当前会话已读取的聊天消息，检测到 HR 发出简历请求卡片（`bodyType == 7`）时，侧边栏以醒目样式突出显示简历建议；无建议时提示"点重新判断可获得简历建议"；切换会话后即时重置。

---

## 已定设计（Claude 决定，按此实施）

- **D1 配置与读取模块**：
  - 配置文件置于 `src/jet/llm/prompts/resume_directions.json`：
    ```json
    {
      "directions": [
        {
          "key": "ai_ops",
          "name": "AI / 运营",
          "file_name": "简历A.pdf",
          "job_types": ["AI 产品运营", "内容运营", "平台和用户运营", "AIGC", "AI 训练师"]
        },
        {
          "key": "data_support",
          "name": "数据 / 技术支持",
          "file_name": "简历B.pdf",
          "job_types": ["数据分析", "数据管理", "临床数据", "实施", "技术支持", "数据标注和质检"]
        },
        {
          "key": "animal_life",
          "name": "动物 / 生命科学",
          "file_name": "简历C.pdf",
          "job_types": ["宠物", "实验动物和动物手术支持", "生物医药 / CRO", "农牧科技企业的技术或数据岗"]
        }
      ]
    }
    ```
  - 新建 `src/jet/domain/resume.py` 仿照 `industry.py`：提供 `read_resume_directions()`、`get_resume_direction_map()` 与 `resolve_resume_suggestion()`；若文件缺失、格式错误或方向数 $<2$，记录一次警告日志并返回空列表 `[]`。
- **D2 提示词 v6**：
  - 新建 `src/jet/llm/prompt_v6.py`，复用 `prompt_v5.build_messages` 生成的内容，在其基础上追加第 14 条"简历方向"说明段（只含 `key`、`name`、`job_types`，绝不含 `file_name`）与输出字段 `resume_suggestion: {"direction": <key>, "reason": <一句话>}`；
  - 配置为空或方向数 $<2$ 时，与 `v5` 完全相同（不加该说明段）；
  - `src/jet/llm/client.py` 接入 `v6` 版本分派；`src/jet/config.py` 默认 `prompt_version` 改为 `"v6"`。
- **D3 存储**：
  - `judgements` 表新增两列：`resume_direction TEXT, resume_reason TEXT`；
  - `schema.sql` 补充建表定义，`store.py` 在 `_ensure_current_schema` 中通过 `ALTER TABLE` 幂等添加，维持 `user_version 6`；
  - Worker（`worker.py`）解析模型输出：`direction` 必须是当前配置里的 `key`、`reason` 非空（截断至最多 40 字符，与 `verdict_reason` 一致），否则两列存 `NULL`；规则判断两列存 `NULL`。
- **D4 版本与过时**：
  - `src/jet/domain/judgements.py` 增加常量 `STALE_METHOD_BASELINE = "v5"`；
  - `is_method_changed` 改为"判断版本早于基准 v5 才算方法变了"（`old_v < baseline_v`），而非早于当前配置版本；
  - `v5` 与 `v6` 均不过时；`v4` 及更早历史判断的行为完全不变；升级后绝不自动重判任何岗位。
- **D5 API**：
  - `to_api` 组装返回新增 `resume_suggestion` 属性：有效时为 `{"direction", "name", "file_name", "reason"}`（`name` 与 `file_name` 读取时按当前配置文件动态解析；若配置中已无该 key 则为 `null`）；无效、无建议或结论为 `skip` 时为 `null`。
- **D6 插件显示**：
  - 详情卡片（`content.js`，搜索列表右侧详情区与独立职位页共用）与侧边栏当前岗位（`chat-view.js` `formatChatJobStatus` 增加字段 + `sidepanel.js` `renderChatJob` 渲染）显示"建议投：<file_name>"，理由用 `title` 悬停或点击展开；
  - 结论为 `skip`（不建议投）或无 `resume_suggestion` 时不显示。
- **D7 聊天页突出**：
  - 确定检测方案：复用 `readBossChatPage` 已经提取的消息数组，检测是否存在 `(body_type === 7 || bodyType === 7) && !is_self` 且消息文字含"简历"（bodyType 7 是 HR 通用请求卡片，也用于交换微信/电话等，必须按文字区分；Claude 审核补充）；
  - 侧边栏当前岗位突出显示"HR 在要简历，建议投：<file_name>"及理由；
  - 若当前会话 HR 索要简历但岗位无建议（如 v5 判断），显示提示："点重新判断可获得简历建议"；
  - 切换会话后即时复位，按新会话重新判定。
- **D8 外发大模型内容全量核查**：
  - 详尽列出 v6 相对 v5 的全部差异（仅新增方向代号/名称/岗位类型及输出 JSON 要求），核实并确认发往模型的全部文本 100% 不含文件名与求职者姓名。

---

## Technical Context

**Language/Version**: Python 3.12+（本机 Jet）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建步骤）。

**Primary Dependencies**: 后端 FastAPI、pydantic、sqlite3、httpx；插件端原生 Web API 与 Chrome Extension API（`chrome.runtime`、`chrome.scripting`），无额外第三方库。

**Storage**: SQLite（WAL 模式），在 `judgements` 表新增 `resume_direction TEXT, resume_reason TEXT`，由 `_ensure_current_schema` 自动幂等维护，保持 `user_version 6`。

**Testing**:
- 后端：`uv run --frozen pytest`（覆盖方向配置加载、v6 提示词生成与解析、版本过时基准断言、API 序列化与 Worker 入库）；
- 插件单元测试：`node --test extension/tests/*.test.js`（覆盖 `hasHrResumeRequest`、`formatChatJobStatus` 简历建议挂载与 `skip` 过滤）；
- 静态语法校验：`node --check extension/src/*.js`。

**Target Platform**: macOS 本机；Chrome 稳定版（Manifest V3）。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 浏览器插件）。

**Performance Goals**: 简历方向匹配为本地常数级字典映射（$< 1$ms）；复用已有大模型调用，大模型请求端到端响应耗时与 v5 持平（$< 2$s）；侧边栏与卡片渲染不增加任何阻塞。

**Constraints**: 不向 zhipin.com 发送任何网络请求；不代替用户自动点击或发送简历；不向大模型外发简历文件名与求职者姓名；已有岗位判断绝对不发生批量重判。

**Scale/Scope**: 单用户；3 份不同侧重点简历；支持随时调整配置。

---

## Constitution Check

*GATE: 逐条对照 Constitution（4.0.2）原则 I–XI 及附加约束。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 简历建议直接服务于主线的"提醒"（投递前选对版本）与"沟通辅助"（HR 索要时精准匹配）。只给建议，由用户自己在平台选择发送，绝对不代替用户点击、不自动发送，不进入外围自动操作。 | 符合 |
| **II. 数据归属从第一天明确** | 简历方向配置为本地个人配置，不进公共库；判断结果中的简历建议存储在按用户隔离的 `judgements` 表中（归属 `user_id`）；公共岗位库 `jobs` 仅保留客观职位信息。 | 符合 |
| **III. 插件与本机 Jet 分离** | 插件只负责读取当前页面已加载的数据并在卡片/侧边栏展示建议；大模型判断、代号到文件名的转换均在本机 Jet 运行，插件不保存判断大模型逻辑。两者仅通过 127.0.0.1 HTTP 通信。 | 符合 |
| **IV. 只读，不向招聘平台发起任何请求** | 插件和本机 Jet 均不向 BOSS 发任何请求；HR 简历请求卡片仅通过读取页面已加载的 Vue/DOM 消息数据识别；不操作页面按钮，不发送简历附件。 | 符合 |
| **V. 平台风险控制** | 绝对不自动打招呼、不自动发送简历、不代替用户操作；页面结构无法识别时如实退回"无建议"，不退回脏数据降级。 | 符合 |
| **VI. 耗时任务后台化** | 大模型判断依然在后台 Worker 队列执行，卡片不阻塞页面浏览；同一岗位判断合并处理；打开侧边栏或切换会话绝不触发判断任务。 | 符合 |
| **VII. 成本有上限** | 复用现有大模型判断调用，大模型调用次数 0 增加；将过时基准定为基准 `v5`，升级不会使已有海量 `v5` 判断被标为可能过时，不批量重判，大模型每日额度消耗 0 额外增长。 | 符合 |
| **VIII. 测试与数据安全** | 测试在内存与临时目录执行，不联网、不需要 API Key；发往大模型的提示词绝对不包含求职者真实姓名与简历文件名；真实敏感信息不进 Git。 | 符合 |
| **IX. 每个数字都有定义** | 涉及的模型调用计数指标完全沿用既有标准，不新增统计歧义。 | 符合 |
| **X. 证据与最小改动** | 仅改动完成简历建议所必需的代码；全库代码分发与过时调用链已在 `research.md` 中逐行标明文件:行号事实证据。 | 符合 |
| **XI. 代码组织** | 后端代码位于 `src/jet/`，插件代码位于 `extension/`，配置文件位于 `src/jet/llm/prompts/`。 | 符合 |
| **附加约束** | 本机 Python 3.12 + SQLite + 127.0.0.1；Chrome Manifest V3 扩展；不部署云端服务器。 | 符合 |

---

## 改动范围与预计改动量

| 模块 | 文件 | 改动内容 | 预计行数（不含测试） |
|---|---|---|---|
| 配置 | `src/jet/llm/prompts/resume_directions.json` | 新建 3 份简历方向的 JSON 配置文件（代号、名称、文件名、岗位类型） | ~35 |
| 领域核心 | `src/jet/domain/resume.py` | 新建模块：动态读取方向配置、校验下限（$\ge 2$）、容错告警、`key -> file_name` 解析 | ~80 |
| 存储定义 | `src/jet/db/schema.sql` | `judgements` 建表 DDL 追加 `resume_direction TEXT, resume_reason TEXT` | ~5 |
| 存储迁移 | `src/jet/db/store.py` | `_ensure_current_schema` 检查并添加 `resume_direction` 与 `resume_reason` 字段 | ~10 |
| 提示词 | `src/jet/llm/prompt_v6.py` | 新建 v6 提示词：复用 v5，追加简历方向说明段与 `resume_suggestion` 输出格式，实现 `parse_facts` 容错与截断 | ~85 |
| LLM 管道 | `src/jet/llm/client.py` | `run_llm_judgement` 接入 `v6` 消息生成与输出解析分派 | ~35 |
| 配置默认 | `src/jet/config.py` | `Settings.prompt_version` 默认值改为 `"v6"` | ~5 |
| 领域核心 | `src/jet/domain/judgements.py` | 增加 `STALE_METHOD_BASELINE = "v5"`；改造 `is_method_changed` 固定对齐基准 v5；`to_api` 动态装配 `resume_suggestion` | ~30 |
| 后台 Worker | `src/jet/worker.py` | `_process_judgement` 解析 `v6` 简历建议并写入数据库两列，适配复核链路 | ~30 |
| 插件纯函数 | `extension/src/chat-view.js` | `formatChatJobStatus` 挂载 `resume_suggestion` 并过滤 `skip`；新增 `hasHrResumeRequest(messages)` 纯函数 | ~30 |
| 插件内容脚本 | `extension/src/content.js` | 详情卡片（搜索列表与独立职位页）渲染"建议投：<file_name>"及悬停理由，`skip` 结论屏蔽 | ~40 |
| 插件侧边栏 | `extension/src/sidepanel.js` | `renderChatJob` 渲染简历建议；检测 HR 索要简历卡片并呈现醒目高亮条；切换会话复位 | ~65 |
| 单元与契约测试 | `tests/unit/`, `tests/api/`, `extension/tests/` | 新增配置解析、v6 提示词、过时基准、API 输出及插件高亮纯函数测试 | ~380 |

生产代码预计变动约 445 行，测试代码约 380 行。

---

## Project Structure

### Documentation (this feature)

```text
specs/006-resume-suggestion/
├── spec.md
├── plan.md              # 本文件
├── research.md          # 现状调研、代码事实排查 S1–S15、D1–D8 逐项分析与风险对策
├── data-model.md        # 配置文件模型、judgements 表变更、API 实体与前端状态流转
├── quickstart.md        # 自动化测试验证命令与真实招聘页面 Q1–Q12 验证清单
├── contracts/
│   ├── local-api.md     # 本机接口与规范岗位条目中 resume_suggestion 契约
│   └── llm-output.md    # Prompt v6 增量差异清单、发往模型内容隐私审查与输出 JSON 校验规范
└── tasks.md             # 后续 /speckit-tasks 生成
```

### Source Code (repository root)

```text
src/jet/
├── config.py            # 默认 prompt_version 改为 "v6"
├── db/
│   ├── schema.sql       # judgements 表新增两列
│   └── store.py         # _ensure_current_schema 增加两列 ALTER TABLE
├── domain/
│   ├── resume.py        # 新建简历方向配置读取与动态装配模块
│   └── judgements.py    # STALE_METHOD_BASELINE = "v5"、防重判改造与 to_api 装配
├── llm/
│   ├── client.py        # run_llm_judgement 接入 v6 分派
│   ├── prompt_v6.py     # 新建 v6 提示词与输出校验解析
│   └── prompts/
│       └── resume_directions.json # 新建简历方向与文件名配置文件
└── worker.py            # Worker 解析 resume_suggestion 并写入数据库

extension/src/
├── chat-view.js         # formatChatJobStatus 扩展与 hasHrResumeRequest 纯函数
├── content.js           # 岗位卡片（列表右侧与独立页）简历建议展示与 skip 屏蔽
└── sidepanel.js         # 侧边栏当前岗位简历建议展示与 HR 索要简历突出提示

tests/
├── unit/
│   ├── test_resume.py         # 简历配置文件加载与容错测试
│   ├── test_prompt_v6.py      # v6 提示词与输出解析截断测试
│   └── test_staleness_v6.py   # 过时判定基准 v5 与防自动重判测试
└── api/
    └── test_judgements_resume.py # to_api 装配与 HTTP 接口端到端测试

extension/tests/
├── chat-view.test.js          # formatChatJobStatus 扩展测试
└── resume-suggestion.test.js  # hasHrResumeRequest 与突出展示决策测试
```

**Structure Decision**: 沿用现有单仓库体系（`src/jet/` + `extension/`），不新增额外的顶层目录。

---

## Complexity Tracking

无违反原则的设计，不需要说明。
