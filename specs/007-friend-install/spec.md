# Feature Specification: 给朋友安装的两个入口（设置页 API Key + macOS 一键安装）

**Feature Branch**: `007-friend-install`

**Created**: 2026-09-29

**Status**: Draft

**Input**: 2026-09-29 用户需求：让朋友不改文件、不敲复杂命令就能用上 Jet——在设置页填写 DeepSeek API Key；用一个脚本在 macOS 上装好后台服务。遵循 constitution（`.specify/memory/constitution.md`）。

## 概述

1. **设置页"DeepSeek API Key"**：填写后保存在本机数据目录，服务端优先使用它，没有才读 `.env`；任何接口只返回打码后的 Key（只露最后 4 位），日志中不出现；"测试连接"用不产生费用的方式验证；没有 Key 时，设置页与侧边栏提示"请先在设置页填写 DeepSeek API Key"，判断与生成都不发请求。
2. **macOS 一键安装脚本** `scripts/install-macos.sh`（配 `scripts/uninstall-macos.sh`）：检查 uv、按实际路径生成 LaunchAgent 并启动、可重复运行、不用 sudo、每步打印说明、最后做健康检查并提示下一步。README 新增"给朋友的安装步骤"。Windows 暂不支持。

### 与 constitution 及本机现状的关系

- **原则 II / VIII**：API Key 是个人凭据，只存本机数据目录（不进仓库、不进数据库），测试不读写真实数据目录。
- **原则 IV**：不向 BOSS 发任何请求。测试连接只向用户配置的大模型服务发一次不计费的查询（模型列表）。
- **本机原有的手动 LaunchAgent**：安装脚本使用通用 Label `local.jet.serve`，不修改、不停止、不删除其他 Label 的服务；若端口已被其他 Jet 服务占用则停止安装并说明，不与之争抢端口。仓库中不出现任何个人 Label。（2026-09-29 用户已把本机切换为安装脚本方式，README 只保留通用写法。）

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 在设置页填写 DeepSeek API Key (Priority: P1)

我在设置页填写 DeepSeek API Key 并保存；之后判断与沟通建议都用这个 Key，不需要改任何文件或重启。再打开设置页，只看到打码后的 Key（例如 `••••••••abcd`）。我可以点"测试连接"确认 Key 有效，也可以清除 Key。

**Why this priority**: 朋友不会编辑 `.env`；这是能用起来的前提。

**Independent Test**: 在没有 `.env` Key 的数据目录中，于设置页填 Key 并保存，点"测试连接"显示成功；随后判断一个岗位成功；检查所有接口响应与服务日志，不出现完整 Key。

**Acceptance Scenarios**:

1. **Given** 我输入 Key 并保存，**When** 保存成功，**Then** 输入框清空，显示"已保存：••••<后 4 位>"；立即生效，无需重启服务。
2. **Given** 设置页与 `.env` 都有 Key，**When** 发起判断，**Then** 使用设置页的 Key。
3. **Given** 只有 `.env` 有 Key，**When** 打开设置页，**Then** 显示"正在使用 .env 中的 Key：••••<后 4 位>"，判断照常。
4. **Given** 我点"测试连接"，**When** Key 有效，**Then** 显示"连接成功"；Key 无效或网络不通时显示对应原因（Key 无效 / 无法连接），不产生费用，不占用每日判断额度。
5. **Given** 我点"清除"，**When** 确认，**Then** 删除设置页保存的 Key，之后回退到 `.env`（若有）。
6. **Given** 任何接口响应、错误信息与服务日志，**Then** 都不包含完整 Key。

---

### User Story 2 - 没有 Key 时明确提示、不发请求 (Priority: P1)

没有配置任何 Key 时，设置页与侧边栏显示"请先在设置页填写 DeepSeek API Key"，岗位判断与沟通建议都不向大模型发请求。

**Acceptance Scenarios**:

1. **Given** 没有任何 Key，**When** 打开侧边栏，**Then** 顶部显示"请先在设置页填写 DeepSeek API Key"。
2. **Given** 没有任何 Key，**When** 浏览列表或打开岗位，**Then** 不发起大模型请求，不记录失败的调用，不占用额度；卡片显示同样的提示而不是"判断失败"。
3. **Given** 没有 Key 时我点"生成"沟通建议，**Then** 显示同样的提示，不发请求。
4. **Given** 随后我填好 Key，**When** 再打开岗位，**Then** 按现有规则正常判断。

---

### User Story 3 - 朋友用一个脚本装好后台服务 (Priority: P1)

朋友下载仓库 ZIP、解压，在终端运行 `scripts/install-macos.sh`。脚本逐步说明在做什么：检查 uv、生成并启动 LaunchAgent、健康检查，最后告诉他下一步——在 Chrome 加载插件、在设置页填 API Key。再次运行（例如更新代码后）会先正常停止旧服务再更新。卸载脚本停止并移除服务。

**Acceptance Scenarios**:

1. **Given** 电脑上没有 uv，**When** 运行安装脚本，**Then** 打印 uv 的安装方法并退出，不自动安装任何东西、不做其他改动。
2. **Given** 有 uv，**When** 运行安装脚本，**Then** 用当前项目实际路径、`$HOME` 与 `command -v uv` 生成 `~/Library/LaunchAgents/local.jet.serve.plist`，日志写到 `~/Library/Logs/jet/serve.log`，启动后健康检查通过，打印下一步。
3. **Given** 已经装过，**When** 再次运行，**Then** 先正常停止已安装的 `local.jet.serve`，再写入新 plist 并启动；结果与首次安装相同。
4. **Given** 端口 47615 已被其他服务占用（例如另一套 Jet 服务），**When** 运行安装脚本，**Then** 停止安装并说明原因与处理方法，不修改、不停止那个服务。
5. **Given** 运行卸载脚本，**Then** 正常停止并移除 `local.jet.serve` 与其 plist；不删除数据目录、日志与仓库文件（打印它们的位置，由用户自行决定是否删除）。
6. **Given** 任何时候，**Then** 脚本不使用 sudo，不修改 `local.jet.serve` 以外的 LaunchAgent。
7. **Given** 在非 macOS 系统运行，**Then** 提示暂只支持 macOS 并退出。

### Edge Cases

- Key 前后有空格：保存时去掉。Key 为空字符串：视为未填写，不保存。
- 测试连接超时：显示"无法连接"，不影响已保存的 Key。
- 数据目录不存在：保存 Key 时按现有方式创建；Key 文件权限为仅本人可读写。
- 安装脚本从其他目录调用（例如 `bash ~/Downloads/jet/scripts/install-macos.sh`）：按脚本所在位置确定项目路径。
- 项目路径含空格：生成的 plist 与命令正确处理。
- 健康检查在限定时间内未通过：打印日志位置与最近几行日志提示，退出码非 0。

## Requirements *(mandatory)*

### Functional Requirements

**API Key**

- **FR-001**: 设置页 MUST 提供"DeepSeek API Key"输入、保存、清除与"测试连接"。
- **FR-002**: Key MUST 保存在本机数据目录的独立文件中（仅本人可读写），MUST NOT 写入仓库或数据库。
- **FR-003**: 服务端使用 Key 的优先级 MUST 为：设置页保存的 Key > `.env` / 环境变量中的 `JET_LLM_API_KEY`；保存或清除后 MUST 立即生效，无需重启。
- **FR-004**: 任何接口 MUST NOT 返回完整 Key，只返回是否已配置、来源（设置页 / .env / 无）与打码值（最后 4 位）；服务日志与错误信息 MUST NOT 出现完整 Key。
- **FR-005**: 读取、保存、清除与测试连接接口 MUST 受现有本机鉴权（配对）保护。
- **FR-006**: "测试连接"MUST 使用不产生费用的请求（查询模型列表）验证 Key，MUST NOT 计入每日判断或沟通额度，MUST NOT 写入大模型调用记录；可测试输入框中尚未保存的 Key（不保存）或已保存的 Key。
- **FR-007**: 没有任何 Key 时，岗位判断与沟通建议 MUST NOT 向大模型发请求、MUST NOT 占用额度；设置页、侧边栏与相关界面 MUST 显示"请先在设置页填写 DeepSeek API Key"。

**安装脚本**

- **FR-008**: `scripts/install-macos.sh` MUST：仅在 macOS 运行；检查 `uv`（没有则打印安装方法并退出，不自动安装）；按脚本所在位置确定项目路径；用项目路径、`$HOME`、`command -v uv` 生成 Label 为 `local.jet.serve` 的 LaunchAgent（登录自动启动、意外退出自动重启、日志 `~/Library/Logs/jet/serve.log`）并启动；可重复运行（已安装则先正常停止再更新）；不使用 sudo；每一步打印说明；最后健康检查并打印下一步（加载插件、在设置页填 API Key）。
- **FR-009**: 安装前若端口 47615 已有服务响应且不属于 `local.jet.serve`，脚本 MUST 停止并说明，MUST NOT 修改或停止其他服务。
- **FR-010**: `scripts/uninstall-macos.sh` MUST 正常停止并移除 `local.jet.serve` 及其 plist，MUST NOT 删除数据目录、日志与仓库文件。
- **FR-011**: README MUST 新增"给朋友的安装步骤"：下载 ZIP → 解压 → 运行安装脚本 → 在 Chrome 加载插件 → 配对 → 在设置页填 API Key；写明 Windows 暂不支持。

### Key Entities

- **API Key 设置**：本机数据目录中的一个文件；对外只暴露"是否配置、来源、打码值"。

## Success Criteria *(mandatory)*

- **SC-001**: 朋友按 README 步骤，在不编辑任何文件的情况下完成安装并完成第一次岗位判断。
- **SC-002**: 所有接口响应与服务日志中，完整 Key 出现次数为 0。
- **SC-003**: 测试连接不产生大模型调用记录与额度占用。
- **SC-004**: 没有 Key 时，大模型请求数为 0。
- **SC-005**: 安装脚本重复运行 3 次，结果一致且只有一个 `local.jet.serve` 在运行。

## Assumptions

- DeepSeek 兼容 OpenAI 接口，`GET <base_url>/models` 不计费；base_url 沿用现有 `JET_LLM_BASE_URL` 配置。
- 配对（插件与本机服务的鉴权）流程不变，朋友按 README 现有配对步骤操作。
- 只处理 DeepSeek Key（`JET_LLM_API_KEY`）；其他 Key（如评测用的 typesafe Key）不在本期范围。

## 范围外

- Windows / Linux 安装脚本；自动安装 uv 或 Python；自动更新代码。
- 修改用户本机原有的手动 LaunchAgent（由用户自行切换）。
