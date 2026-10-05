# API Contracts: 本机接口规范（007-friend-install）

**Feature**: `007-friend-install`
**Base URL**: `http://127.0.0.1:47615`
**Authentication**: 所有业务接口均通过现有本机配对鉴权，请求头携带 `Authorization: Bearer <jetToken>`。

---

## 1. 查询 API Key 状态 (`GET /v1/llm-key`)

查询当前服务端是否配置了 DeepSeek API Key 及其来源与打码展示值。

- **Method**: `GET`
- **Path**: `/v1/llm-key`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)

### 成功响应 (`200 OK`)

```json
{
  "configured": true,
  "source": "settings_page",
  "masked": "••••abcd"
}
```

或者未配置时：

```json
{
  "configured": false,
  "source": null,
  "masked": null
}
```

字段说明：
- `configured`: `boolean`，当前服务是否有可用 Key；
- `source`: `"settings_page"`（来自设置页文件保存）或 `"env"`（来自环境变量或 `.env`）或 `null`；
- `masked`: 仅显示前置四个点加最后 4 位字符，其余隐藏；未配置为 `null`。

### 错误响应

- `401 Unauthorized`: 缺少或无效配对 Token
  ```json
  {"error": "unpaired", "message": "未配对"}
  ```

---

## 2. 保存 API Key (`PUT /v1/llm-key`)

保存用户在设置页输入的 API Key，写入本机数据目录 `llm_api_key` 并立即在服务端热生效。

- **Method**: `PUT`
- **Path**: `/v1/llm-key`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (`application/json`)

```json
{
  "api_key": "sk-0123456789abcdef0123456789abcdef"
}
```

### 校验规则
- `api_key` 自动前后去除空白字符（`strip()`）；
- 若为空或仅包含空白字符，拒绝保存，返回 422 错误。

### 成功响应 (`200 OK`)

```json
{
  "configured": true,
  "source": "settings_page",
  "masked": "••••cdef"
}
```

### 错误响应

- `422 Unprocessable Entity`: Key 为空或无效载荷
  ```json
  {"error": "invalid_payload", "message": "API Key 不能为空"}
  ```
- `401 Unauthorized`: 未配对

---

## 3. 清除 API Key (`DELETE /v1/llm-key`)

删除数据目录下由设置页保存的 `llm_api_key` 文件。若本地 `.env` 或环境变量中有 Key，将自动回退生效。

- **Method**: `DELETE`
- **Path**: `/v1/llm-key`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)

### 成功响应 (`200 OK`)

若无 `.env` 回退：
```json
{
  "configured": false,
  "source": null,
  "masked": null
}
```

若回退到 `.env` 中的 Key：
```json
{
  "configured": true,
  "source": "env",
  "masked": "••••1234"
}
```

---

## 4. 测试连接 (`POST /v1/llm-key/test`)

向模型服务商发出免 Token 的模型列表查询请求（`GET {base_url}/models`），测试 Key 是否有效以及服务是否连通。**绝不记录 `llm_calls`，绝不占用每日限额**。

- **Method**: `POST`
- **Path**: `/v1/llm-key/test`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (`application/json`，可选)

```json
{
  "api_key": "sk-optional-key-to-test"
}
```
*注：`api_key` 为可选参数。若不传或为 `null`/空，则自动测试当前已在服务端生效的 Key。*

### 成功响应 (`200 OK`)

> 注意：无论测试成功与否，HTTP 状态码均为 200，具体连通原因在响应体内说明。

#### 1. 验证成功
```json
{
  "ok": true,
  "reason": "ok"
}
```

#### 2. Key 无效（模型端返回 HTTP 401 或 403）
```json
{
  "ok": false,
  "reason": "invalid_key"
}
```

#### 3. 网络不可达或服务异常（超时、DNS 失败、5xx 错误等）
```json
{
  "ok": false,
  "reason": "unreachable"
}
```

#### 4. 未提供待测 Key 且当前服务亦未配置
```json
{
  "ok": false,
  "reason": "no_key"
}
```

---

## 5. 系统状态查询增量 (`GET /v1/status`)

- **Method**: `GET`
- **Path**: `/v1/status`

### 响应体（增量属性）

```json
{
  "user_id": "me",
  "profile": { ... },
  "quota": { ... },
  "assist_quota": { ... },
  "rule_excluded_today": 0,
  "labels": { ... },
  "llm_key_configured": true
}
```

---

## 6. 无 Key 门禁相关接口表现

### 6.1 岗位详情观察 (`POST /v1/observations`)
- 场景：打开岗位详情页，规则粗筛通过，但未配置 Key。
- 响应：返回 `jobs` 字典，并在响应根节点附带 `notice: "no_llm_key"`；不生成 `judgement` 排队记录。
```json
{
  "jobs": {
    "123456": {
      "completeness": "full",
      "company_name": "示例公司",
      "title": "高级研发工程师",
      "judgement": null,
      "hr_note": null,
      "my_status": null,
      "seen_in_chat": false
    }
  },
  "notice": "no_llm_key"
}
```

### 6.2 手动重新判断 (`POST /v1/jobs/{platform_job_id}/judge`)
- 场景：未配置 Key 时点击「重新判断」。
- 响应 (`409 Conflict`)：
```json
{
  "error": "no_llm_key",
  "message": "请先在设置页填写 DeepSeek API Key"
}
```

### 6.3 话术建议生成 (`POST /v1/chat/generate`)
- 场景：未配置 Key 时点击「生成」建议。
- 响应 (`409 Conflict`)：
```json
{
  "error": "no_llm_key",
  "message": "请先在设置页填写 DeepSeek API Key"
}
```
*注：该拦截在预扣额度函数 `reserve()` 之前发生，完全不消耗每日额度。*
