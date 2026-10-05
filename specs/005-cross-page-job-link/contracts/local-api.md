# 本机接口契约变更（005）

**Feature**: `005-cross-page-job-link` | **Date**: 2026-09-29

在 002、003、004 既有接口契约基础上增补，未列出的端点与行为保持不变。

---

## 一、新增端点：POST /v1/chat/job

在招聘平台聊天会话中检测到有效岗位时，由后台 Service Worker 调用，将当前会话岗位信息被动记录到本机公共岗位库与用户聊天记录中。

### 1. 请求契约

- **Path**: `POST /v1/chat/job`
- **Headers**:
  - `Origin: chrome-extension://<extension-id>`（由中间件校验）
  - `Authorization: Bearer <jetToken>`（配对凭证，由 `require_paired` 鉴权）
- **Content-Type**: `application/json`
- **Request Body**:
  ```json
  {
    "platform_job_id": "abc123xyz~",
    "title": "数据分析师",
    "company_name": "某某科技"
  }
  ```

| 字段 | 类型 | 必需 | 约束与说明 |
|---|---|---|---|
| `platform_job_id` | 字符串 | 是 | BOSS 平台加密岗位 ID（`encryptJobId`），不能为空或纯空白 |
| `title` | 字符串 | 是 | 聊天页展示的职位名称，不能为空或纯空白 |
| `company_name` | 字符串 \| null | 否 | 页面展示的公司名称；若读不到或为空可为 null |

### 2. 响应契约

- **Status**: `200 OK`
- **Content-Type**: `application/json`
- **Response Body**（结构与 `GET /v1/judgements` 单岗位响应严格一致）：
  ```json
  {
    "jobs": {
      "abc123xyz~": {
        "completeness": "list_only",
        "company_name": "某某科技",
        "title": "数据分析师",
        "judgement": null,
        "hr_note": null,
        "my_status": null,
        "seen_in_chat": true
      }
    }
  }
  ```

### 3. 处理行为与硬性约束

1. **幂等性**：
   - 若岗位不存在：在 `jobs` 表插入新行（`completeness='list_only'`, `current_version_id=NULL`, `first_seen_at/last_seen_at=now`）；
   - 若岗位已存在：**绝不修改**已有 `jobs` 列（`company_name` 仅在数据库原值为 NULL 时补齐），**绝不修改**已有判断和状态；
   - 记录 `job_chat_seen`：首次遇到插入行，重复遇到更新 `last_seen_at`，`chat_title` 仅在数据库原值为空时补齐；
   - **绝对不写** `job_versions` 表。
2. **零副作用**：
   - **绝不**调用 `request_judgement`；
   - **绝不**调用 `rejudge`；
   - **绝不**计算规则粗筛 `screen_hints`；
   - **绝不**调用 DeepSeek 或任何外部大模型；
   - **绝不**写入 `job_status` 或 `job_status_events`。

### 4. 异常与错误响应

- **401 Unauthorized**: 未提供有效的配对 Token
  ```json
  { "error": "unpaired", "message": "未配对" }
  ```
- **422 Unprocessable Entity**: 缺少 `platform_job_id` 或 `title`
  ```json
  { "error": "invalid_payload", "message": "岗位必须包含非空的 platform_job_id 和 title" }
  ```

---

## 二、已有端点返回结构变更

### 1. 规范岗位条目（Canonical `_job_entry`）输出调整

适用于 `GET /v1/judgements`、`POST /v1/observations`、`POST /v1/jobs/{platform_job_id}/judge`、`PUT /v1/jobs/{platform_job_id}/status`、`PUT /v1/jobs/{platform_job_id}/hr-note` 等返回岗位条目的所有端点：

1. **`seen_in_chat`（布尔）**：
   - 新增布尔字段，表示当前用户是否在聊天中遇到过该岗位（即 `job_chat_seen` 表中是否存在 `(user_id, job_id)` 记录）。
2. **`title`（回退逻辑）**：
   - 原逻辑：从 `job_versions` 取当前版本的 `title`；
   - 新逻辑：若 `current_version_id` 为 NULL（聊天入库尚未打开详情的岗位），回退读取 `job_chat_seen.chat_title` 作为条目标题，避免返回 `null`。

### 2. GET /v1/my-jobs 响应变更

- **`filter=all_jobs` 统计与列表**：
  - 返回的岗位列表包含 `job_chat_seen` 中记录的岗位（无论是否有状态、判断或 HR 记录）；
  - `counts.all_jobs` 计数同步包含该范围（去重后的岗位数）。
- **条目数据适配**：
  - 对尚未生成版本的岗位（`current_version_id` 为 NULL）：
    - `title`: `job_chat_seen.chat_title`；
    - `salary_raw`: `null`；
    - `city`: `""`；
    - `verdict`: `null`；
    - `verdict_label`: `null`；
    - `stale`: `false`。

---

## 三、插件内部通信契约（chrome.runtime 消息）

### 1. 聊天页岗位入库调用（Service Worker 内部发起）

由 `extension/src/background.js` 在处理 `chat_top_changed` 并成功读取到 `encrypt_job_id` 与 `title` 时内部调用 `jetClient.call("POST", "/v1/chat/job", payload)`。
- **失败容错**：若 Jet 服务不可用或未配对，静默忽略，不重试，不弹窗打扰用户；
- **侧边栏解耦**：无论侧边栏处于打开还是关闭状态，该入库流程均正常执行。

### 2. 状态变动广播与列表卡片重绘（content.js 与 background.js 协同）

- **`set_job_status`（内容脚本/侧边栏 → 后台）**：
  - 请求参数：`{ type: "set_job_status", platform_job_id: string, status: "saved" | "applied" | "skipped" | null }`；
  - 后台处理：通过 HTTP PUT 请求 Jet 本机服务；
  - 成功后后台广播：调用 `syncChatJobStatusInTabStates` 同步所有标签页，并对发送方标签页下发更新后的 `render_state`（含更新后的 `list_marks`）；
  - 内容脚本即时响应：在收到回调成功的第一时间，就地修改内存中 `currentListMarks[targetJobId]` 并调用 `updateMarksPositions()` 重绘卡片标识，确保 1 秒内无感完成更新。
