# Research & Codebase Analysis: 给朋友安装的两个入口

**Feature**: `007-friend-install`
**Date**: 2026-09-29
**Branch**: `007-friend-install`

---

## 1. 现状调研与代码事实（FACT / INFERENCE / HYPOTHESIS）

### S1: API Key 存储、加载与运行时更新机制（D1）

- **FACT [代码事实] (`src/jet/config.py:9-35`)**：
  `Settings` 类由 `@dataclass(frozen=True)` 修饰。属性不可变，直接执行 `settings.llm_api_key = ...` 会抛出 `dataclasses.FrozenInstanceError`。必须使用 `dataclasses.replace(settings, llm_api_key=...)` 创建新实例。
- **FACT [代码事实] (`src/jet/config.py:58-90`)**：
  `load_settings` 目前通过 `_parse_env_file` 读取 `<data_dir>/.env` 及环境变量 `JET_LLM_API_KEY`。目前尚未检测独立文件 `llm_api_key`。
- **FACT [代码事实] (`src/jet/api/app.py:26-36, 78-81`)**：
  在 `create_app` 中：
  ```python
  worker = JudgementWorker(settings, transport=llm_transport)
  ...
  app.state.settings = settings
  app.state.worker = worker
  ```
  `app.state.settings` 保存了 `Settings` 引用；同时后台工作线程 `worker` 内部也保存了 `self.settings = settings`（`src/jet/worker.py:49-53`）。
- **INFERENCE [关键推论]**：
  当用户在设置页 PUT 保存或 DELETE 清除 API Key 时，更新运行时必须同时执行：
  ```python
  new_settings = dataclasses.replace(request.app.state.settings, llm_api_key=new_key)
  request.app.state.settings = new_settings
  request.app.state.worker.settings = new_settings
  ```
  否则 `JudgementWorker` 在后台执行判断时仍读取旧的 `self.settings.llm_api_key`。
- **FACT [代码事实] (`src/jet/cli.py:96-104`)**：
  数据目录下文件的安全权限现有实践：`admin.secret` 使用 `os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` 创建，权限 0600。
  对 `llm_api_key` 的原子写入，应先在 `settings.data_dir` 下写入临时文件（权限 0600），再通过 `os.replace` 原子重命名至目标文件 `settings.data_dir / "llm_api_key"`。

---

### S2: 接口设计与测试连接规范（D2）

- **FACT [代码事实] (`src/jet/api/auth.py:27-46` 及 `src/jet/api/routes.py:11, 234`)**：
  受鉴权保护的接口使用 `user_id: str = Depends(require_paired)`。新增的 API Key 操作接口必须全部依赖 `require_paired`。
- **FACT [代码事实] (`src/jet/api/routes.py:231-263`)**：
  `/v1/status` 接口目前返回：
  ```json
  {
    "user_id": "me",
    "profile": { "set": bool, "version_no": int },
    "quota": { ... },
    "assist_quota": { ... },
    "rule_excluded_today": int,
    "labels": { ... }
  }
  ```
  需要在此响应中扩展 `"llm_key_configured": bool(request.app.state.settings.llm_api_key)`，供前端侧边栏及卡片秒级读取连接状态。
- **FACT [代码事实] (`src/jet/llm/client.py:70-130`)**：
  DeepSeek API 兼容 OpenAI 标准。`GET {llm_base_url}/models` 是官方免 Token 消耗的基础接口。
  测试连接调用要求：
  - 构造 `GET {settings.llm_base_url.rstrip('/')}/models`
  - 带请求头 `Authorization: Bearer <key>`
  - 超时严格限制为 10 秒
  - 绝对不调用 `jet.llm.quota.reserve()`，不写入 `llm_calls` 表，不占用每日限额
  - 状态码 200..299 返回 `{"ok": true, "reason": "ok"}`
  - 状态码 401 或 403 返回 `{"ok": false, "reason": "invalid_key"}`
  - 网络错误、连接超时、5xx 等其它返回 `{"ok": false, "reason": "unreachable"}`
  - 若未传入 key 且当前 settings 也无 key，返回 `{"ok": false, "reason": "no_key"}`

---

### S3: 无 Key 门禁与状态处理冲突排查（D3）

- **FACT [代码事实] (`src/jet/llm/client.py:162-168`)**：
  目前如果 `not settings.llm_api_key`，`run_llm_judgement` 返回：
  ```python
  return LlmOutcome(
      status="failed",
      error="未配置 API Key（在数据目录 .env 中设置 JET_LLM_API_KEY）",
      prompt_version=p_ver,
      engine=eng,
  )
  ```
  紧接着在 `src/jet/worker.py:269-276`：
  ```python
  if outcome.status != "done":
      now_str = utc_now()
      with transaction(self._conn):
          self._conn.execute(
              "UPDATE judgements SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
              (outcome.error or "大模型调用失败", now_str, judgement_id),
          )
      return
  ```
  **冲突与风险（CRITICAL）**：
  如果任由其排队进入 worker，worker 会把该岗位写成 `status = 'failed'`。
  而在 `src/jet/domain/judgements.py:109-126` 的 `request_judgement` 中，只要 `latest["status"]` 为 `failed`，若岗位内容与画像未变化，再次打开岗位时**不会自动重判**！这直接违反 D3："不要把岗位标成'判断失败'导致以后不重判。有 Key 后按现有规则可重新判断。"
- **FACT [代码事实] (`src/jet/api/routes.py:1056-1106`)**：
  在 `/v1/chat/generate` 接口中，目前第 1057 行直接调用 `reserve(conn, ...)` 预扣了一次每日沟通额度！
  随后才进入 `generate_assist_suggestions`，如果无 Key 则发送空 Authorization 触发 401 报错，被作为 `http_error` 计费并记录调用（`finish(conn, call_id, outcome="http_error", settings=settings)`）。
  **冲突与风险（CRITICAL）**：
  无 Key 时点击生成不仅调用了外部接口，还记录了失败调用、扣除了每日限额！这严重违背 D3 与 FR-007。
- **DECISION [解决方案设计]**：
  1. **话术生成门禁 (`routes.py:post_chat_generate`)**：
     在第 1056 行 `reserve` 之前，前置检查：
     ```python
     if not request.app.state.settings.llm_api_key:
         return JSONResponse(
             status_code=409,
             content={"error": "no_llm_key", "message": "请先在设置页填写 DeepSeek API Key"},
         )
     ```
     彻底杜绝无 Key 时的额度扣减与模型请求。
  2. **岗位详情观察门禁 (`routes.py:observations` & `domain/judgements.py`)**：
     当打开岗位详情页（`body.page_type == "detail"`）时：
     - 若画像已设置，首先照常跑规则粗筛 `screen(version_row, profile)`（规则计算在本地，不需网络和 Key）。
     - 若规则未通过（命中淘汰规则），直接落库 `status='done', verdict='skip', source='rule'`，不受 Key 影响（符合主线原则）。
     - 若规则通过（需要大模型判断）：
       若当前 `not settings.llm_api_key`，**绝对不写入 `status='queued'` 的判断行，也不提交给 worker**！
       直接返回 `notice = "no_llm_key"`，岗位库中该岗位暂无判断记录（`judgement = null`）。
       当用户后续配置 Key 后，再次打开该岗位，因其尚无判断记录，`request_judgement` 会自然触发入库并提交判断，零历史脏状态。
  3. **手动重新判断门禁 (`routes.py:rejudge`)**：
     若 `not request.app.state.settings.llm_api_key`，直接抛出 HTTP 409：
     ```json
     {"error": "no_llm_key", "message": "请先在设置页填写 DeepSeek API Key"}
     ```
  4. **视图状态扩展 (`extension/src/view-state.js`)**：
     在 `VIEW_STATES` 中新增 `"no_llm_key"`：
     `LABELS["no_llm_key"] = "请先在设置页填写 DeepSeek API Key"`。
     `fromObservation` 遇到 `response.notice === "no_llm_key"` 时返回 `"no_llm_key"`。
     卡片与侧边栏渲染时显示文案，不标为判断失败。

---

### S4: 插件设置页与侧边栏展示（D4）

- **FACT [代码事实] (`extension/src/options.html`, `options.js`)**：
  设置页已有连接状态卡片、页面标记、聊天自动生成、我的画像、我的简历、从严行业、经历素材、配对设置等模块。
  新增 API Key 区域宜放置在紧邻「连接状态」卡片下方或「配对设置」旁。
  包含元素：
  - 密码输入框 (`<input type="password" id="llm-key-input" placeholder="sk-..." />`)
  - 当前状态说明：
    - `source === "settings_page"` 时："已保存：••••<后 4 位>"
    - `source === "env"` 时："正在使用 .env 中的 Key：••••<后 4 位>"
    - 无 Key 时："尚未配置 API Key"
  - 操作按钮：保存、测试连接、清除。
  - 消息提示条：`#llm-key-msg`。
- **FACT [代码事实] (`extension/src/sidepanel.js:100-143`)**：
  `renderQuota(statusRes)` 处理顶部横条状态：
  当 `statusRes.state === "paired"` 时，检测 `statusRes.status?.llm_key_configured`：
  若为 `false`，横条变为警告色（`status-warning`），文本显示「请先在设置页填写 DeepSeek API Key」。
- **FACT [代码事实] (`extension/src/jet-client.js:27-111`)**：
  封装 `call(method, path, body)`。增加以下专属方法：
  - `getLlmKey()`: `GET /v1/llm-key`
  - `putLlmKey(apiKey)`: `PUT /v1/llm-key`
  - `deleteLlmKey()`: `DELETE /v1/llm-key`
  - `testLlmKey(apiKey)`: `POST /v1/llm-key/test`
- **FACT [代码事实] (`extension/src/background.js:720-750`)**：
  增加对应的消息转发器：
  `get_llm_key`, `put_llm_key`, `delete_llm_key`, `test_llm_key`。

---

### S5: 日志安全与脱敏覆盖排查（D5）

- **FACT [代码事实] (`src/jet/logging.py:6-25`)**：
  `API_KEY_PATTERNS` 正则列表：
  ```python
  API_KEY_PATTERNS = [
      (re.compile(r'(sk-[a-zA-Z0-9_\-]{3})[a-zA-Z0-9_\-]{8,}([a-zA-Z0-9_\-]{3})'), r'\1***\2'),
      (re.compile(r'(sk-[a-zA-Z0-9_\-]{6,})'), r'sk-***'),
      (re.compile(r'(Bearer\s+)[a-zA-Z0-9_\-\.]{8,}'), r'\1***'),
      (re.compile(r'((?:api[_-]?key|secret|token|password)\s*[:=]\s*["\']?)[a-zA-Z0-9_\-]{6,}(["\']?)', re.IGNORECASE), r'\1***\2'),
  ]
  ```
  DeepSeek 的 Key 格式为 `sk-` 开头的 32 位十六进制字符串（如 `sk-0123456789abcdef0123456789abcdef`）。
  经核验：
  - Pattern 1 完整匹配 `sk-...` 并在两端保留 3 位中间脱敏；
  - Pattern 3 匹配 HTTP 请求头中的 `Bearer sk-...` 并脱敏；
  - 完全能有效覆盖 DeepSeek Key 及其请求头。
- **FACT [代码事实] (`src/jet/api/app.py:86-100`)**：
  `request_duration_middleware` 的实现：
  ```python
  _http_log.info(f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms")
  ```
  该中间件**只记录**请求方法、路径、状态码与耗时，**绝不记录**请求体和查询参数。
  因此，`PUT /v1/llm-key` 与 `POST /v1/llm-key/test` 的请求体天然不会被 HTTP 中间件打印到日志。
  在业务路由处理函数中，亦不打印请求体，确保日志绝对干净。

---

### S6: macOS 一键安装与卸载脚本（D6）

- **FACT [代码事实] (`README.md:21-78`)**：
  现有手动 LaunchAgent 使用的是仓库维护者个人机器的专用 Label（不应出现在给他人使用的脚本中）。
- **DECISION [无害共存与独立性]**：
  一键安装脚本统一使用通用 Label：`local.jet.serve`。
  安装脚本必须做安全检查：
  1. 检测当前是否为 macOS (`uname -s == "Darwin"`)，非 macOS 则打印说明退出；
  2. 动态定位脚本真实目录所在的上一级作为项目根目录（支持路径中包含空格）；
  3. 检查 `command -v uv`。若不存在，输出官方安装命令 `curl -LsSf https://astral.sh/uv/install.sh | sh` 并退出，退出码 1；
  4. 检查端口 47615：
     若 `curl http://127.0.0.1:47615/v1/health` 有响应，且 `launchctl print gui/$UID/local.jet.serve` 不在运行，则判定端口已被其他服务占用（如已运行其他 Label 的 LaunchAgent 或前台 `jet serve`），打印明确警告并以退出码 1 退出，绝不误杀其他服务；
  5. 创建日志与 plist 目录：`mkdir -p ~/Library/Logs/jet ~/Library/LaunchAgents`；
  6. 若已安装 `local.jet.serve`，先执行 `launchctl bootout gui/$UID/local.jet.serve` 停止旧服务并等待；
  7. 使用 heredoc 生成 `~/Library/LaunchAgents/local.jet.serve.plist`（正确注入 uv 绝对路径、项目根路径、PATH、日志路径等）；
  8. 执行 `plutil -lint` 校验 plist 语法；
  9. 执行 `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/local.jet.serve.plist` 载入启动；
  10. 循环最多 20 秒探测 `http://127.0.0.1:47615/v1/health`；
      成功则以绿色高亮输出后续步骤指引（Chrome 加载插件、配对、设置页填 Key）；
      失败则打印日志路径及 `tail -n 20 ~/Library/Logs/jet/serve.log`，退出码 1；
  11. 全程不使用 `sudo`。
- **DECISION [卸载脚本 `scripts/uninstall-macos.sh`]**：
  1. 检查并执行 `launchctl bootout gui/$UID/local.jet.serve`；未安装时输出友好提示；
  2. 删除 `~/Library/LaunchAgents/local.jet.serve.plist`；
  3. 明确提示保留数据目录（`~/Library/Application Support/Jet`）、日志（`~/Library/Logs/jet`）与仓库文件，打印路径由用户自行决定是否删除；
  4. 全程不使用 `sudo`。

---

### S7: README 文档结构演进（D7）

- **FACT [代码事实] (`README.md:5-119`)**：
  现有文档从 `uv sync`、`uv run jet serve`、LaunchAgent、`uv run jet pair`、`uv run jet stats` 逐步展开。
- **DECISION [文档编排]**：
  在「快速上手」前新增面向朋友的独立章节「给朋友的安装步骤」：
  - 简明 5 步：下载解压 ZIP -> 运行 `bash scripts/install-macos.sh` -> Chrome 加载扩展 -> 配对 -> 设置页填写 API Key；
  - 明确标注：Windows 暂不支持；
  - 现有 LaunchAgent 章节补充标注：此为开发者手动定制方式，与一键脚本二选一。

---

## 2. D1–D7 决策一致性与冲突排查汇总

| 编号 | 核心设计要求 | 代码现状/证据 | 是否冲突 | 处理方案 |
|---|---|---|---|---|
| **D1** | 数据目录 `llm_api_key`，0600，原子写入，覆盖 settings 并通知 worker | `Settings` 为只读 dataclass，worker 持有独立引用 (`worker.py:49`) | 存在引用隔离隐患 | PUT/DELETE 时用 `dataclasses.replace` 同时更新 `app.state.settings` 与 `worker.settings` |
| **D2** | 4 个 key 接口 + status 增加 `llm_key_configured` | 现有 routes 结构规范，鉴权已有 `require_paired` | 无冲突 | 依约扩展，测试请求走 `GET /models`，超时 10s，0 额度 0 记录 |
| **D3** | 无 Key 门禁：不调模型、不写 llm_calls、不占额度、不标 failed | 现有 `client.py:162` 返回 failed 会导致 worker 落库 failed 阻塞未来重判；chat/generate 预扣额度 (`routes.py:1057`) | **严重冲突** | 必须在 `routes.py` 入口处（`observations` 粗筛通过后、`rejudge`、`chat/generate` 预占前）彻底拦截，返回 `no_llm_key` 提示与 409 状态码 |
| **D4** | 设置页 Key 区（密码框、打码、来源、测试）；侧边栏顶部与卡片提示 | `options.html`、`sidepanel.js` 体系完备 | 无冲突 | 新增对应 DOM、状态机映射与样式 |
| **D5** | 日志覆盖 `sk-...`，PUT/测试接口请求体不进日志 | 中间件已只打方法与路径，`logging.py` 正则覆盖 | 无冲突 | 业务处理函数坚决不打 request body 日志 |
| **D6** | `install-macos.sh`（通用 Label、防争抢、uv 检查、plist 生成、健康检查） | 现有手动方式使用个人 Label | 无冲突 | 统一采用 `local.jet.serve`，健康检查防占端口，纯 bash 实现 |
| **D7** | README 新增朋友步骤，Windows 暂不支持，LaunchAgent 手动说明 | 现有 README 面向开发者 | 无冲突 | 新增朋友一键步骤，补充手动说明 |
