# Tasks: 给朋友安装的两个入口 (007-friend-install)

**Input**: `/specs/007-friend-install/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`contracts/install-script.md`、`quickstart.md`）

**Tests**: 自动化测试遵循宪法原则 VIII（不联网、不需 Key、不写真实数据目录）；测试连接用测试替身，不发真实请求。脚本用 `bash -n` 做语法检查，行为在真实机器上按 quickstart 验证。

**Organization**: 阶段 1 服务端 Key 与无 Key 门禁 → 阶段 2 插件设置页与提示 → 阶段 3 安装/卸载脚本与 README。每阶段末尾：全量测试、Codex、提交；改服务端的阶段提交后 `launchctl kickstart -k` 重启。

`〔执行：Antigravity〕` 写代码与测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）；`〔执行：Claude Code〕` 审核、Codex、提交、重启。

**共同硬性要求**：生产代码不含针对测试的特殊处理；不删除、不放宽已有测试断言（确需改须逐条说明）；不创建别名导出或参数重载；任何接口、错误信息、日志都不得出现完整 Key；测试不写真实数据目录。

## 阶段 1: 服务端 API Key 与无 Key 门禁

- [x] T001 Key 存储（research D1）：数据目录文件 `llm_api_key`（0600、原子写入）；启动时优先于 `.env`；保存/清除立即更新运行中的 settings（包括 worker 使用的对象）；记录来源 `settings_page` / `env` / 无〔执行：Antigravity〕
- [x] T002 接口（contracts/local-api.md）：`GET/PUT/DELETE /v1/llm-key`、`POST /v1/llm-key/test`（`GET {base_url}/models`，10 秒超时，不写 llm_calls、不占额度），全部 `require_paired`；`/v1/status` 增加 `llm_key_configured`〔执行：Antigravity〕
- [x] T003 无 Key 门禁（research S3）：无 Key 时 observations/rejudge 不排队判断、不写失败记录；`/v1/chat/generate` 在预占额度之前返回 `no_llm_key`；worker 若仍遇到无 Key 的任务，不把岗位写成会阻止以后重判的失败状态〔执行：Antigravity〕
- [x] T004 日志：确认 `mask_sensitive_data` 覆盖 `sk-` 开头的 Key 与 Authorization 头；Key 接口的请求体不进任何日志〔执行：Antigravity〕
- [x] T005 [P] 测试：存储文件权限与原子写入；优先级；立即生效；接口鉴权 401；响应只含打码值；测试连接的成功/401/网络错误（替身）且无 llm_calls；无 Key 时判断不排队、生成不扣额度；有 Key 后可正常判断；日志打码〔执行：Antigravity〕
- [x] T006 检查点：全量测试；审核 diff；Codex；提交；重启〔执行：Claude Code〕

## 阶段 2: 插件设置页与提示

- [x] T007 `extension/src/options.html`、`options.js`、`jet-client.js`、`background.js`：设置页"DeepSeek API Key"区（密码输入框、保存、清除、测试连接、打码值与来源说明；保存后清空输入框）〔执行：Antigravity〕
- [x] T008 `extension/src/sidepanel.js`、`view-state.js`、`content.js`：`llm_key_configured` 为 false 时侧边栏顶部与岗位卡片显示"请先在设置页填写 DeepSeek API Key"（不显示"判断失败"）；生成按钮返回 `no_llm_key` 时显示同样提示〔执行：Antigravity〕
- [x] T009 [P] 测试：纯函数（提示显示条件、打码显示、测试结果文案）〔执行：Antigravity〕
- [x] T010 检查点：全量测试；审核 diff；Codex；提交〔执行：Claude Code〕

## 阶段 3: 安装/卸载脚本与 README

- [x] T011 `scripts/install-macos.sh`、`scripts/uninstall-macos.sh`（contracts/install-script.md；Label `local.jet.serve`；不用 sudo；不触碰其他 Label）〔执行：Antigravity〕
- [x] T012 `README.md`：新增"给朋友的安装步骤"（下载 ZIP → 解压 → 运行脚本 → 加载插件 → 配对 → 填 API Key；Windows 暂不支持）；说明现有手动 LaunchAgent 一节与脚本二选一〔执行：Antigravity〕
- [x] T013 检查点：`bash -n` 两个脚本；全量测试；审核脚本逐行；Codex；提交〔执行：Claude Code〕
