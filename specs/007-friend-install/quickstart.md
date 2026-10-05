# Quickstart & Verification Guide: 给朋友安装的两个入口

**Feature**: `007-friend-install`
**Date**: 2026-09-29

---

## 1. 自动化测试套件

在开发与合入分支前，必须执行以下全量自动化验证命令，保证所有测试全部通过。

### 1.1 Python 后端与 API 测试
```bash
uv run --frozen pytest
```
覆盖模块：
- `tests/unit/test_config_llm_key.py`: Key 文件原子读写、0600 权限、Settings 热替换、来源优先级测试；
- `tests/unit/test_logging_mask.py`: DeepSeek Key（`sk-...` 格式）及 Bearer 请求头的脱敏验证；
- `tests/api/test_llm_key_routes.py`: `GET/PUT/DELETE /v1/llm-key` 与 `POST /v1/llm-key/test` 契约测试（含 0 额度 0 调用断言）；
- `tests/api/test_no_key_gating.py`: 无 Key 门禁测试（`observations` 粗筛通过但无 Key、`rejudge` 409、`chat/generate` 409、额度扣减与调用记录均为 0 断言）。

### 1.2 浏览器插件单元测试
```bash
node --test extension/tests/*.test.js
```
覆盖模块：
- `view-state.test.js`: `no_llm_key` 视图状态解析与 `LABELS` 映射；
- `jet-client.test.js`: `getLlmKey`, `putLlmKey`, `deleteLlmKey`, `testLlmKey` 契约断言；
- `options-key.test.js`: 设置页 API Key 状态机判定、打码展示与错误处理。

### 1.3 静态语法与代码风格校验
```bash
# 检查前端 JavaScript 语法
node --check extension/src/*.js

# 检查 Shell 脚本语法有效性
bash -n scripts/install-macos.sh
bash -n scripts/uninstall-macos.sh

# 若本地环境安装了 shellcheck，执行静态代码分析（可选推荐）
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/install-macos.sh scripts/uninstall-macos.sh
fi
```

---

## 2. 手动端到端验证清单 (E2E Checklist)

### 验证场景 1：macOS 一键安装与幂等性
- **操作步骤**：
  1. 打开终端进入项目根目录，运行 `bash scripts/install-macos.sh`；
  2. 观察控制台逐步打印：检测 macOS、定位路径、检查 uv、创建目录、生成 plist、启动服务、20 秒健康检查通过、打印成功指引；
  3. 执行 `curl http://127.0.0.1:47615/v1/health` 确认服务运行；
  4. 再次连续运行 `bash scripts/install-macos.sh` 两次（模拟重复执行/升级更新）。
- **预期结果**：
  - 首次安装顺利成功，耗时 < 5 秒；
  - 重复运行时，自动停止旧服务并重新 bootstrap，无报错，端口无冲突，`launchctl print gui/$UID/local.jet.serve` 始终仅有 1 个实例在运行。

### 验证场景 2：端口冲突防护
- **操作步骤**：
  1. 在前台终端运行临时监听 47615 端口的程序（例如 `python3 -m http.server 47615` 或前台运行的独立测试服务）；
  2. 在另一个终端运行 `bash scripts/install-macos.sh`。
- **预期结果**：
  - 脚本检测到端口已有响应且不是 `local.jet.serve`；
  - 打印明确警告：「端口 47615 已被其他程序占用...」；
  - 以退出码 1 退出，绝不杀死前台正在运行的程序。

### 验证场景 3：无 Key 门禁与状态展示
- **操作步骤**：
  1. 保证测试数据目录下既无 `llm_api_key` 文件，`.env` 中也未配置 `JET_LLM_API_KEY`；
  2. 在 Chrome 中加载扩展并完成配对；
  3. 打开 Chrome 侧边栏，观察顶部横条；
  4. 在 BOSS 直聘打开一个未判断的岗位详情页；
  5. 在聊天页点击「生成」沟通建议。
- **预期结果**：
  - 侧边栏顶部横条呈现黄底提示「请先在设置页填写 DeepSeek API Key」；
  - 岗位详情卡片与侧边栏当前岗位显示「请先在设置页填写 DeepSeek API Key」，**绝对不显示「判断失败」**；
  - 查看服务统计（`uv run jet stats`）与数据库，`llm_calls` 记录为 0，额度消耗为 0；
  - 聊天页点击生成，提示「请先在设置页填写 DeepSeek API Key」，额度不扣减。

### 验证场景 4：设置页填写 Key 与即时生效
- **操作步骤**：
  1. 打开插件设置页，在「DeepSeek API Key」输入框中输入有效 Key，点击「保存」；
  2. 观察界面刷新；
  3. 输入框输入错误 Key，点击「测试连接」；再清空输入框，点击「测试连接」；
  4. 切换回刚才打开的岗位详情页并刷新。
- **预期结果**：
  - 保存后输入框清空，状态显示「已保存：••••<后 4 位>」；
  - 测试错误 Key 提示「Key 无效」；测试已生效的正确 Key 提示「连接成功」；
  - 刷新岗位详情页，因前一步未产生 failed 记录，岗位立刻进入正常排队并成功给出适合度判断；
  - 无需重启 `jet serve` 服务。

### 验证场景 5：清除 Key 与 .env 回退
- **操作步骤**：
  1. 在数据目录 `.env` 中预置 `JET_LLM_API_KEY=sk-env-backup-key-1234`；
  2. 设置页保存了另外一个 Key（`sk-settings-5678`）；
  3. 在设置页点击「清除」并确认。
- **预期结果**：
  - 设置页状态平滑变更为「正在使用 .env 中的 Key：••••1234」；
  - 再次判断岗位，自动使用 `.env` 中的 Key；
  - 若 `.env` 也未设置，点击清除后状态变为「尚未配置 API Key」。

### 验证场景 6：日志安全性核查
- **操作步骤**：
  1. 完成上述所有操作后，打开日志文件 `~/Library/Logs/jet/serve.log`；
  2. 使用搜索工具检索完整的 API Key。
- **预期结果**：
  - 完整 Key 出现次数严格为 0；
  - 仅能看到脱敏后的记录（如 `sk-***`、`Bearer ***`）或标准 HTTP 请求日志。

### 验证场景 7：卸载脚本验证
- **操作步骤**：
  1. 在终端运行 `bash scripts/uninstall-macos.sh`；
  2. 执行 `launchctl print gui/$UID/local.jet.serve`；
  3. 检查文件系统。
- **预期结果**：
  - 脚本输出「已停止后台服务 local.jet.serve」与「已移除配置文件...」；
  - `launchctl print` 报告找不到服务；
  - 数据目录（`~/Library/Application Support/Jet`）与日志目录（`~/Library/Logs/jet`）完好无损保留。
