# Implementation Plan: 我的简历：上传 PDF + AI 简历画像 (009-resume-pdf)

**Branch**: `009-resume-pdf` | **Date**: 2026-09-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-resume-pdf/spec.md`

---

## Summary

本功能落实 2026-09-30 用户确认的 009 需求，将 006"我的简历"的手填"适合岗位类型"改进为上传 PDF 并通过 AI 提炼简历画像：
1. **数据模型与独立存储（v9 迁移）**：
   - 重建 `resume_slots` 表（按用户隔离，增加 `resume_text` 字段，旧 `job_types` 迁移为 `profile`）；
   - `user_settings` 新增 `daily_resume_profile_limit`（默认 10 次，0–50 可配置）；
   - 重建 `llm_calls` 表，扩展 `purpose` CHECK 约束加入 `'resume_profile'`；
   - 额度模块（`quota.py`）增加独立管理（不占日常岗位判断额度）。
2. **纯内存 PDF 提取与姓名三来源强脱敏**：
   - 依赖 `pypdf`（6.19.0，纯 Python、BSD 许可），在内存中解析 PDF Base64 流，零磁盘写入、零数据库 BLOB；有效文字少于 50 字符判定为扫描件（422 `no_text`）；
   - 姓名三来源融合（`users.display_name`、上传区 `self_name`、PDF 规则提取），若均无姓名坚决阻断调用并报 422 `self_name_required`；
   - `sanitize.py` 增补 18 位身份证号码正则，并对邮箱、手机号、微信、姓名进行全面脱敏。
3. **服务端画像生成与 API 契约**：
   - 新建 `src/jet/llm/resume_profile.py` 提示词模块，调用非思考引擎（`settings.judge_engine`）生成 ≤300 字画像（适合方向 + 3～5 条亮点）；不记录提示词与输出全文至日志；
   - 提供 `POST /v1/resumes/upload`（提取、脱敏、计费、保存）、`POST /v1/resumes/{slot}/regenerate`（重新生成）、`GET /v1/resumes`（列表，不发全文）、`PUT /v1/resumes`（保存修改）、`DELETE /v1/resumes/{slot}`（彻底删除）；
   - 额度耗尽（429）或无 Key（409）时降级保存提取出的文本与名称，保留后续重试能力。
4. **岗位判断主线集成**：
   - `prompt_v6.build_resume_instruction` 提示格式升级为 `- 简历{slot}：{profile}`；依然 ≥2 份有效简历才发送；不发简历名称与全文；保持 `PROMPT_VERSION = "v6"` 不变（避免既有判断失效）。
5. **插件设置页交互与双处说明替换**：
   - `options.html` / `options.js` 支持 PDF 选文件、文件名自动填入名称、上传并生成画像、300 字实时统计、编辑保存、删除；上传区增加可选姓名输入框；
   - 原样替换设置页与从严行业卡片中的两处说明文字。

---

## Technical Context

**Language/Version**: Python 3.12+（Jet 本机服务）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建打包）。

**Primary Dependencies**: `pypdf`（6.19.0，已加入 `pyproject.toml`/`uv.lock`）、FastAPI、pydantic、httpx；前端原生 Web API 与 Chrome Extension API。

**Storage**:
- SQLite 数据库表迁移至 v9（重建 `resume_slots`、`user_settings` 加列、重建 `llm_calls`）；
- 严禁持久化存储任何 `.pdf` 文件或二进制流（仅限内存临时缓冲）。

**Testing**:
- 后端：`uv run --frozen pytest`（覆盖 v9 迁移与数据保留、pypdf 内存提取、空文本扫描件拦截、姓名三来源与无姓名阻断、脱敏正则断言、独立配额与耗尽降级、零落盘安全断言、判断提示词集成）；
- 插件单元测试：`node --test extension/tests/*.test.js`（覆盖 options 简历上传、Base64 封装、300 字校验、两处说明文案）；
- 语法与静态检查：`node --check extension/src/*.js`。

**Target Platform**: macOS；Chrome 稳定版（Manifest V3）。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 浏览器插件）。

**Performance Goals**:
- PDF 文本提取与脱敏耗时 $< 200$ 毫秒；
- 简历画像大模型调用响应时间 $< 3$ 秒；
- 岗位判断时读取画像与组装耗时 $< 5$ 毫秒。

**Constraints**:
- PDF 字节绝对不写入磁盘、不写入数据库、不写入日志；
- 若三来源均未识别出姓名，绝对不调用大模型；
- 岗位判断绝对不发送简历名称和简历原始全文；
- 仓库内任何测试与文档绝对不得写入真实个人信息（仅使用虚构人物）。

---

## Constitution Check

*GATE: 逐条对照 Constitution 5.0.0 核心原则 I–XI 及附加约束。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 符合。服务于主线中的"按个人画像判断"环节。在候选人拥有多份简历时，通过高质量的简历画像帮助大模型精确推荐最适合该岗位的简历版本，不扩展任何自动化打招呼/投递外围功能。 | 符合 |
| **II. 数据归属从第一天明确** | 符合。简历文本、画像及配置归属于当前用户（`user_id`），保存在 `resume_slots` 表中，按用户完全隔离，其他用户无法查看，绝不写入公共岗位库。 | 符合 |
| **III. 插件与本机 Jet 分离** | 符合。插件仅负责在设置页提供文件选择、Base64 封装与画像展示交互；PDF 解析、文字脱敏与大模型提炼全部留在本机 Jet 服务端执行，两者仅通过 `127.0.0.1` 交换数据。 | 符合 |
| **IV. 只读，不向招聘平台发起任何请求** | 符合。功能仅运行于设置页与本机 Jet 之间，不向 BOSS 或任何外部招聘平台发起请求。 | 符合 |
| **V. 平台风险控制** | 符合。不进行任何平台自动化操作；遇验证码或页面变动不干预。 | 符合 |
| **VI. 耗时任务后台化** | 符合。PDF 解析与画像生成仅由用户在设置页显式点击"上传并生成画像"触发，前端异步等待，不阻塞主线浏览；打开设置页绝不自动触发大模型；岗位判断直接使用已存画像。 | 符合 |
| **VII. 成本有上限** | 符合。建立独立的 `daily_resume_profile_limit`（默认 10 次，0–50 可配置），独立预占与审计，不与岗位判断（`daily_llm_limit`）、沟通辅助（`daily_assist_limit`）或预判（`daily_prejudge_limit`）混用；岗位判断时仅发送 ≤300 字的画像摘要，严格控制 Token 成本；额度用尽当天不再调用大模型、不排队。 | 符合 |
| **VIII. 测试与数据安全** | 符合。PDF 字节在内存处理，不落盘、不进库、不进日志；大模型外发前强脱敏；姓名三来源兜底阻断；测试夹具只用虚构人物，绝不包含真实隐私；默认测试不联网、不写真实数据目录。 | 符合 |
| **IX. 每个数字都有定义** | 符合。`daily_resume_profile_limit` 定义清晰：以"次"为单位，统计当日已计费（billed=1）的 `purpose='resume_profile'` 请求数，按用户过滤。 | 符合 |
| **X. 证据与最小改动** | 符合。改动严格聚焦于简历模块（v9 迁移、PDF 提取、脱敏扩展、画像生成、提示词集成、设置页交互）；区分事实与推论。 | 符合 |
| **XI. 代码组织** | 符合。Python 代码位于 `jet` 包下（`src/jet/llm/resume_profile.py`、`src/jet/domain/resume.py` 等）；插件代码位于 `extension/`；无通用顶层包或多余依赖。 | 符合 |
| **附加约束** | 符合。本机 Python 3.12 + SQLite + 127.0.0.1；Chrome Manifest V3 扩展；只在本机运行，不部署云端。 | 符合 |

> [!NOTE]
> **Constitution Check 结论**：本设计严格遵循 Constitution 5.0.0 的各项原则，**无需修订 Constitution 5.0.0**。

---

## 改动范围与预计改动量

| 模块 | 文件 | 改动内容 | 预计行数（不含测试） |
|---|---|---|---|
| 数据库架构 | `src/jet/db/schema.sql` | 1. 重建 `resume_slots`（加 `resume_text`，`profile` 约束 ≤300 字）；<br/>2. `user_settings` 加 `daily_resume_profile_limit`；<br/>3. `llm_calls.purpose` CHECK 加入 `'resume_profile'` | ~30 |
| 数据库迁移 | `src/jet/db/migrations.py` | 编写 `migrate_to_v9`：WAL checkpoint、备份、事务中重建两表、加列、历史行 `job_types` 迁移为 `profile`、外键校验、`user_version=9`、更新 `LATEST_VERSION = 9` | ~110 |
| 数据库初始化 | `src/jet/db/store.py` | `_ensure_current_schema` 与 `init_db` 适配版本 9 | ~15 |
| 隐私脱敏扩展 | `src/jet/llm/sanitize.py` | 增补 18 位身份证正则过滤；完善脱敏文本处理 | ~25 |
| 配额独立管理 | `src/jet/llm/quota.py` | `usage_today`、`remaining_today`、`reserve` 增加对 `purpose='resume_profile'` 支持，读取 `daily_resume_profile_limit` | ~35 |
| 简历领域服务 | `src/jet/domain/resume.py` | 适配新字段 `resume_text` 与 `profile`；实现 PDF 内存提取、姓名三来源提取、脱敏调度、画像更新与整行删除逻辑 | ~130 |
| 提示词工程 | `src/jet/llm/resume_profile.py` | 全新编写：简历画像生成系统提示词与输入构建、输出校验与 ≤300 字截断 | ~90 |
| 岗位判断提示词 | `src/jet/llm/prompt_v6.py` | `build_resume_instruction` 改为 `- 简历{slot}：{profile}`；保持 v6 不变 | ~10 |
| 岗位判断主线 | `src/jet/llm/client.py` | 读取 `resume_slots` 字段名映射改为 `profile` | ~5 |
| API 路由 | `src/jet/api/routes.py` | 新增 `POST /v1/resumes/upload`、`POST /v1/resumes/{slot}/regenerate`、`DELETE /v1/resumes/{slot}`；更新 `GET/PUT /v1/resumes` | ~160 |
| 插件网络通信 | `extension/src/jet-client.js` | 封装 `uploadResume`、`regenerateResume`、`deleteResume` 方法 | ~25 |
| 插件后台中继 | `extension/src/background.js` | 扩展 `upload_resume`、`regenerate_resume`、`delete_resume` 消息转发 | ~40 |
| 选项设置界面 | `extension/src/options.html` | 更新双处说明文字；改造简历项 DOM（文件选择、名称联动、画像文本框、300字统计、重新生成）；增加脱敏姓名输入框 | ~45 |
| 选项页交互 | `extension/src/options.js` | 实现文件选择转 Base64、5MB 校验、上传与状态显示、实时 300 字统计、保存修改与删除逻辑 | ~110 |
| 后端测试 | `tests/unit/test_migrations.py`<br/>`tests/unit/test_sanitize.py`<br/>`tests/unit/test_resume_profile.py`<br/>`tests/api/test_resume_routes.py` | 覆盖 v9 迁移、身份证脱敏、姓名三来源阻断、pypdf 提取与空文本扫描件拦截、配额独立与耗尽降级、零落盘断言、提示词断言 | ~420 |
| 插件测试 | `extension/tests/options-resume.test.js` | 覆盖文件名截取、字数实时统计、说明文案断言 | ~120 |

预计生产代码变动约 830 行，测试代码约 540 行。

---

## Project Structure

### Documentation (this feature)

```text
specs/009-resume-pdf/
├── spec.md              # 需求规格说明书
├── plan.md              # 本实施计划（逐条对照 Constitution 5.0.0）
├── research.md          # 现状调研、代码事实 S1–S7、D1–D8 决策与理由
├── data-model.md        # 数据库模型、DTO 契约、时序与状态流转图
├── quickstart.md        # 自动化测试与真实页面验证步骤清单
├── contracts/
│   ├── local-api.md     # 本机 HTTP 接口规范 (/v1/resumes/*)
│   └── plugin-protocol.md # 插件内部通信与选项页交互规范
└── tasks.md             # 任务清单（按依赖拓扑排序）
```

### Source Code (repository root)

```text
src/jet/
├── db/
│   ├── schema.sql           # v9 DDL 同步
│   ├── migrations.py        # migrate_to_v9 迁移实现
│   └── store.py             # init_db 与 _ensure_current_schema 适配 v9
├── llm/
│   ├── sanitize.py          # 18 位身份证号码正则脱敏
│   ├── quota.py             # purpose='resume_profile' 独立配额支持
│   ├── resume_profile.py    # 简历画像提炼提示词与输出校验
│   ├── prompt_v6.py         # 升级 build_resume_instruction 为画像格式
│   └── client.py            # 读取 resume 适配 profile 字段
├── domain/
│   └── resume.py            # PDF 内存提取、姓名识别、脱敏调度、领域存储
└── api/
    └── routes.py            # /v1/resumes/upload 等接口实现

extension/src/
├── jet-client.js            # 简历上传与删除等 API 请求封装
├── background.js            # 简历相关 runtime 消息中继
├── options.html             # 设置页 UI 改造与双处说明更新
└── options.js               # 文件读取、Base64 封装、实时字数统计与保存

tests/
├── unit/
│   ├── test_migrations.py       # v8 到 v9 迁移与历史数据保留测试
│   ├── test_sanitize.py         # 身份证正则与姓名三来源识别测试
│   ├── test_resume_pdf.py       # pypdf 提取、空文本扫描件拦截与零落盘断言
│   ├── test_resume_quota.py     # 独立配额计算与耗尽拦截测试
│   └── test_prompt_v6.py        # 岗位判断提示词仅含画像断言
└── api/
    └── test_resume_routes.py    # 上传、重新生成、读取、修改、删除路由测试

extension/tests/
└── options-resume.test.js       # 选项页纯逻辑与文案断言测试
```

---

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*无违反宪法原则的设计，已严格遵循 Constitution 5.0.0 所有核心原则。*
