# Implementation Plan: 给朋友安装的两个入口（设置页 API Key + macOS 一键安装）

**Branch**: `007-friend-install` | **Date**: 2026-09-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-friend-install/spec.md`

## Summary

本功能解决"让朋友不改文件、不敲复杂命令就能用上 Jet"的核心需求，提供两个关键入口：
1. **设置页「DeepSeek API Key」管理**：
   - 凭据存储于本机数据目录下独立文件 `llm_api_key`（0600 权限，原子写入）；
   - 生效优先级：设置页保存的 Key > `.env` / 环境变量 `JET_LLM_API_KEY`；
   - 保存或清除后立即热生效至运行中 `app.state.settings` 与 `worker.settings`，无需重启服务；
   - 接口与日志全程脱敏，仅返回打码值 `••••abcd` 与来源，绝对不泄露完整 Key；
   - 提供「测试连接」功能（通过 `GET {base_url}/models`，超时 10 秒，401/403 视为无效，0 额外调用记录，0 额度消耗）；
   - **无 Key 门禁**：没有 Key 时，岗位粗筛通过后不排队、不调用模型、不写入失败记录导致阻塞未来重判、`/v1/chat/generate` 不预占额度；设置页、侧边栏及卡片以统一状态呈现「请先在设置页填写 DeepSeek API Key」。
2. **macOS 一键安装与卸载脚本**：
   - `scripts/install-macos.sh`：检测 macOS 与 `uv`，检查 47615 端口与服务归属，生成标准 LaunchAgent（Label `local.jet.serve`，WorkingDirectory 指向项目根，EnvironmentVariables PATH 注入 uv 目录，KeepAlive 意外退出重启），经 `plutil -lint` 校验后通过 `launchctl bootstrap` 启动，并在 20 秒内进行健康检查，最后给出清晰的 Chrome 加载与配对指引；
   - `scripts/uninstall-macos.sh`：安全停止并移除 `local.jet.serve` 及其 plist，保留个人数据与日志目录；
   - 两个脚本全程不使用 `sudo`，可重复运行（幂等），不影响开发者个人现存的 LaunchAgent。
3. **文档与说明更新**：
   - `README.md` 新增「给朋友的安装步骤」，写明 Windows 暂不支持；现有 LaunchAgent 章节注明为手动方式。

---

## Technical Context

**Language/Version**: Python 3.12+（本机 Jet 服务）；原生 JavaScript（ES2022，Chrome MV3 扩展，无构建步骤）；Bash 4+（`set -euo pipefail`）。

**Primary Dependencies**: 后端 FastAPI、pydantic、httpx、dataclasses；前端原生 Web API 与 Chrome Extension API（`chrome.runtime`, `chrome.storage`, `chrome.scripting`）。

**Storage**:
- 本机文件系统：`<data_dir>/llm_api_key`（UTF-8 单行，0600 权限，原子替换写入）；
- SQLite 数据库表无 DDL 变更（API Key 绝不进数据库，维持 schema version 7）。

**Testing**:
- 后端：`uv run --frozen pytest`（覆盖独立 Key 文件读写与 0600 权限、Settings 热替换、来源优先级、脱敏正则、Key 测试接口及无 Key 门禁断言）；
- 插件单元测试：`node --test extension/tests/*.test.js`（覆盖 `no_llm_key` 视图状态机、打码逻辑、设置页加载状态与 jet-client 契约）；
- 静态语法与脚本检查：`node --check extension/src/*.js`、`bash -n scripts/*.sh`、（若安装）`shellcheck scripts/*.sh`。

**Target Platform**: macOS（脚本仅支持 Darwin）；Chrome 稳定版（Manifest V3）。

**Project Type**: 单仓库（`src/jet/` 本机服务 + `extension/` 浏览器插件 + `scripts/` 运维脚本）。

**Performance Goals**:
- 本机 Key 文件读写耗时 $< 5$ms；
- 运行时 Key 保存/清除内存热生效 $< 1$ms；
- 测试连接网络超时严格限制为 10 秒；
- 安装脚本从运行到健康检查通过 $< 5$ 秒。

**Constraints**:
- 不向 BOSS 招聘平台发起任何网络请求；
- 接口响应与服务端日志中完整 API Key 出现次数严格为 0；
- 测试连接绝对不调用 `reserve()`，不写入 `llm_calls` 表，不占用每日限额；
- 无 Key 时大模型请求数为 0，且不得把岗位标为 `failed` 导致以后不重判；
- 脚本全程不使用 `sudo`，不争抢已存在的其他服务端口。

**Scale/Scope**: 单用户本机运行；面向作者本人及朋友的简化安装分发。

---

## Constitution Check

*GATE: 逐条对照 Constitution（4.0.2）核心原则 I–XI 及附加约束。*

| 原则 | 本计划如何满足 | 评估 |
|---|---|---|
| **I. 主线优先** | 让朋友能零配置编辑直接用上主线功能（浏览→读取→入库→判断→提醒→沟通辅助）。设置页配 Key 与一键安装是主线运作的物理前置条件，不扩展任何自动化打招呼/投递外围功能。 | 符合 |
| **II. 数据归属从第一天明确** | API Key 是个人私有凭据，保存在个人电脑的数据目录独立文件中，既不汇入公共岗位库，也不进 SQLite 数据库，更不进 Git 仓库。公共岗位库与个人判断归属完全保持原样。 | 符合 |
| **III. 插件与本机 Jet 分离** | 插件只在设置页提供输入/查看/测试界面，通过 HTTP 接口与本机通信；真正的 Key 存储、来源判定与大模型调用均留在本机 Jet，插件不直接向大模型发请求。两者仅通过 127.0.0.1 交换。 | 符合 |
| **IV. 只读，不向招聘平台发起任何请求** | 插件和本机 Jet 均不向 BOSS 发任何请求；测试连接仅访问已配置的第三方大模型服务地址（`GET /models`）。 | 符合 |
| **V. 平台风险控制** | 安装脚本与插件配置不触碰任何反爬或风控逻辑；无 Key 时在界面明确提示"请先在设置页填写 DeepSeek API Key"，不伪造数据，不静默降级。 | 符合 |
| **VI. 耗时任务后台化** | 岗位判断依然在后台 worker 中异步执行；无 Key 时在入口处短路拦截，不向 worker 队列塞入无效任务，避免占用后台调度资源。打开或刷新设置页不触发任何判断任务。 | 符合 |
| **VII. 成本有上限** | 测试连接采用免 Token 的模型列表接口，不记调用、不占额度；无 Key 时短路拦截，不发起调用、不占额度、不写失败日志；已有岗位在填 Key 后正常判断，绝不批量重判。 | 符合 |
| **VIII. 测试与数据安全** | API Key 存储于数据目录 `llm_api_key`（0600 权限），禁止进入 Git；测试代码全部在内存或临时目录隔离运行，不依赖真实 Key；请求日志只记方法路径不记请求体，`logging.py` 脱敏覆盖 `sk-...` 与请求头。 | 符合 |
| **IX. 每个数字都有定义** | 测试连接与无 Key 拦截不改变判断调用与沟通生成的统计口径，指标定义表无需修改。 | 符合 |
| **X. 证据与最小改动** | 调研中详尽指出现有 `client.py:162` 返回 failed 导致岗位被永久标记 failed 的缺陷并精准修复；改动严格限制在 Key 管理与安装脚本所必需的代码。 | 符合 |
| **XI. 代码组织** | 后端代码位于 `src/jet/`，前端代码位于 `extension/`，运维脚本位于 `scripts/`，无任何外部脏依赖。 | 符合 |
| **附加约束** | 本机 Python 3.12 + SQLite + 127.0.0.1；Chrome Manifest V3 扩展；不部署云端，脚本不使用 sudo。 | 符合 |

---

## 改动范围与预计改动量

| 模块 | 文件 | 改动内容 | 预计行数（不含测试） |
|---|---|---|---|
| 服务配置 | `src/jet/config.py` | `load_settings` 优先读取 `<data_dir>/llm_api_key`，提供 Key 读取与来源判定辅助函数 | ~25 |
| 服务启动 | `src/jet/api/app.py` | 确保 `app.state.settings` 与 `app.state.worker` 协同持有最新 Settings 实例 | ~10 |
| API 路由 | `src/jet/api/routes.py` | 1. 新增 `GET/PUT/DELETE /v1/llm-key` 及 `POST /v1/llm-key/test`；<br/>2. `/v1/status` 增加 `llm_key_configured` 字段；<br/>3. `observations`、`rejudge` 及 `post_chat_generate` 增加无 Key 门禁拦截 | ~120 |
| 领域核心 | `src/jet/domain/judgements.py` | `request_judgement` 配合无 Key 场景，规则通过但无 Key 时返回 `notice="no_llm_key"` 且不入库 | ~15 |
| 插件通信 | `extension/src/jet-client.js` | 封装 `getLlmKey`, `putLlmKey`, `deleteLlmKey`, `testLlmKey` 方法 | ~25 |
| 视图状态 | `extension/src/view-state.js` | `VIEW_STATES` 与 `LABELS` 新增 `no_llm_key` 状态；`fromObservation` 遇到 `no_llm_key` 映射 | ~20 |
| 插件后台 | `extension/src/background.js` | 增加 API Key 相关消息处理器，透传至 `jetClient` | ~45 |
| 插件设置 | `extension/src/options.html` | 新增「DeepSeek API Key」卡片（密码输入框、状态提示、保存、测试、清除按钮） | ~35 |
| 插件设置 | `extension/src/options.js` | 处理 API Key 的加载、打码回显、来源展示、保存、测试连接、清除与错误提示 | ~110 |
| 插件侧栏 | `extension/src/sidepanel.js` | `renderQuota` 根据 `llm_key_configured` 在顶部呈现黄底提示，当前岗位卡片无 Key 状态渲染 | ~25 |
| 安装脚本 | `scripts/install-macos.sh` | 全新编写：macOS 检测、uv 检测、端口防占排查、plist 生成、校验、bootstrap 与健康检查 | ~90 |
| 卸载脚本 | `scripts/uninstall-macos.sh` | 全新编写：bootout 停止服务、清理 plist、输出数据保留说明 | ~35 |
| 仓库说明 | `README.md` | 新增「给朋友的安装步骤」，写明 Windows 暂不支持；现有 LaunchAgent 标明为手动方式 | ~40 |
| 后端测试 | `tests/unit/test_config_llm_key.py`, `tests/api/test_llm_key_routes.py`, `tests/api/test_no_key_gating.py` | 覆盖原子写入、权限、热更新、来源优先级、4 个新接口、0 额度 0 调用断言与无 Key 门禁 | ~350 |
| 插件测试 | `extension/tests/view-state.test.js`, `extension/tests/options-key.test.js` | 覆盖新 view_state 映射、打码工具函数与设置页状态机判定 | ~160 |

生产代码预计变动约 595 行，测试代码约 510 行。

---

## Project Structure

### Documentation (this feature)

```text
specs/007-friend-install/
├── spec.md              # 需求规格说明书
├── plan.md              # 本实施计划
├── research.md          # 现状调研、代码事实排查 S1–S7、D1–D7 对齐与风险对策
├── data-model.md        # 凭据存储模型、DTO 契约、状态机流转与 plist 实体
├── quickstart.md        # 自动化测试验证命令与端到端手动验证清单
├── contracts/
│   ├── local-api.md     # 本机 HTTP 接口（/v1/llm-key, /v1/status, 门禁拦截）规范
│   └── install-script.md# macOS 安装与卸载脚本逐步行为、退出码与输出规范
└── tasks.md             # 后续 /speckit-tasks 生成
```

### Source Code (repository root)

```text
src/jet/
├── config.py            # Settings 增补、数据目录 llm_api_key 优先读取逻辑
├── api/
│   ├── app.py           # Settings 与 worker 同步持有
│   └── routes.py        # /v1/llm-key 路由家族、/v1/status 增量、无 Key 门禁拦截
└── domain/
    └── judgements.py    # request_judgement 无 Key 场景协同

extension/src/
├── jet-client.js        # API Key 请求封装
├── view-state.js        # 新增 no_llm_key 状态与文案
├── background.js        # 消息转发与状态透传
├── options.html         # 设置页 API Key 卡片 DOM
├── options.js           # API Key 输入、打码展示、测试连接与交互逻辑
└── sidepanel.js         # 侧边栏顶部横条与卡片无 Key 状态联动

scripts/
├── install-macos.sh     # macOS 一键安装与服务编排脚本
└── uninstall-macos.sh   # macOS 一键卸载与服务清理脚本

README.md                # 新增给朋友的安装指南与 LaunchAgent 补充说明

tests/
├── unit/
│   ├── test_config_llm_key.py  # Key 存储原子性、0600 权限与优先级
│   └── test_logging_mask.py    # DeepSeek Key 与 Bearer 请求头脱敏
└── api/
    ├── test_llm_key_routes.py  # /v1/llm-key 与 test 接口契约
    └── test_no_key_gating.py   # 无 Key 门禁（0 调用、0 扣额、不写 failed）

extension/tests/
├── view-state.test.js          # no_llm_key 状态解析测试
└── options-key.test.js         # 设置页打码与状态展示纯函数测试
```

**Structure Decision**: 严格维持现有单仓库结构（`src/jet/` + `extension/` + `scripts/`），无新增多余顶层目录。

---

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

*无违反宪法原则的设计，无需填写。*
