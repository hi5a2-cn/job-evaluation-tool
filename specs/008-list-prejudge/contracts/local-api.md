# API Contracts: 本机接口规范 (008-list-prejudge)

**Feature**: `008-list-prejudge`
**Base URL**: `http://127.0.0.1:47615`
**Authentication**: 所有业务接口均通过现有本机配对鉴权，请求头携带 `Authorization: Bearer <jetToken>`。

---

## 1. 列表页岗位打包预判 (`POST /v1/prejudge`)

对当前列表页新加载且未正式判断的岗位进行批量大模型预判，一次请求打包处理。

- **Method**: `POST`
- **Path**: `/v1/prejudge`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (Request Body)

接收岗位数组，每批次最多 40 条。

```json
{
  "jobs": [
    {
      "platform_job_id": "job_abc123",
      "title": "Python 后端开发工程师",
      "company_name": "示例科技",
      "company_industry": "互联网",
      "salary_raw": "15-25K·14薪",
      "city": "深圳",
      "district": "南山区",
      "experience": "3-5年",
      "degree": "本科",
      "job_labels": ["后端开发", "微服务"],
      "skills": ["Python", "FastAPI", "MySQL"]
    }
  ]
}
```

#### 字段定义与约束：
- `jobs`: `array`，长度 1..40。
  - `platform_job_id`: `string`，平台岗位唯一标识（必填）。
  - `title`: `string`，岗位名称（必填）。
  - `company_name`: `string | null`，公司名称。
  - `company_industry`: `string | null`，公司行业。
  - `salary_raw`: `string | null`，页面薪资原始字符串。
  - `city`: `string`，城市名称。
  - `district`: `string | null`，区县名称。
  - `experience`: `string | null`，经验要求（如 "3-5年"、"应届生"、"经验不限"）。
  - `degree`: `string | null`，学历要求（如 "本科"、"大专"、"学历不限"）。
  - `job_labels`: `string[]`，页面岗位特征标签。
  - `skills`: `string[]`，页面技能标签。

---

### 成功响应 (`200 OK`)

#### 场景 1：成功预判或命中缓存 (`status: "ok"`)

```json
{
  "status": "ok",
  "prejudgements": {
    "job_abc123": {
      "level": "open",
      "reason": "薪资与技术栈匹配，南山区通勤便利，值得深入了解",
      "created_at": "2026-09-30T01:15:00.000000Z"
    },
    "job_xyz789": {
      "level": "skip",
      "reason": "要求经验年限超出且非目标方向",
      "created_at": "2026-09-30T01:15:00.000000Z"
    }
  },
  "usage": {
    "used": 4,
    "limit": 20,
    "remaining": 16
  }
}
```

#### 场景 2：每日预判额度耗尽 (`status: "quota_exhausted"`)

当天已消耗预判页数达到 `daily_prejudge_limit`，不调用大模型、不排队。若请求中有命中数据库已有缓存的岗位，仍正常返回其缓存。

```json
{
  "status": "quota_exhausted",
  "prejudgements": {
    "job_cached": {
      "level": "neutral",
      "reason": "薪资偏低但技术契合",
      "created_at": "2026-09-30T00:50:00.000000Z"
    }
  },
  "usage": {
    "used": 20,
    "limit": 20,
    "remaining": 0
  }
}
```

#### 场景 3：未配置 API Key (`status: "no_llm_key"`) 或未设置画像 (`status: "no_profile"`)

```json
{
  "status": "no_llm_key",
  "prejudgements": {},
  "usage": {
    "used": 0,
    "limit": 20,
    "remaining": 20
  }
}
```

#### 场景 4：候选集为空 (`status: "empty"`)

请求的所有岗位已拥有正式判断（`status = 'done'`），不调用大模型。

```json
{
  "status": "empty",
  "prejudgements": {},
  "usage": {
    "used": 4,
    "limit": 20,
    "remaining": 16
  }
}
```

#### 场景 5：调用失败 (`status: "failed"`)（2026-10-06 体检第 59 条补记）

大模型调用失败（网络、超时、HTTP 错误等），或返回后解析、入库出错时，仍返回 200：`prejudgements` 只含这次请求之前已有的预判（入库失败时本次结果不会出现）。连接阶段就失败、请求没发出去时（`not_sent`）不占当天额度；请求已发出的失败照常计一页额度。插件收到后保持静默，不提示用户。

```json
{
  "status": "failed",
  "prejudgements": {},
  "usage": {
    "used": 5,
    "limit": 20,
    "remaining": 15
  }
}
```

---

### 错误响应

- `401 Unauthorized`: 缺少或无效配对 Token
  ```json
  {"error": "unpaired", "message": "未配对"}
  ```
- `422 Unprocessable Entity`: 请求体验证失败（如超过 40 条、缺少必填字段）
  ```json
  {"error": "invalid_payload", "message": "岗位列表最多 40 条"}
  ```

---

## 2. 查询列表预判设置与今日配额 (`GET /v1/prejudge/settings`)

获取当前配置的预判每日上限及今日使用情况。

- **Method**: `GET`
- **Path**: `/v1/prejudge/settings`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)

### 成功响应 (`200 OK`)

```json
{
  "daily_prejudge_limit": 20,
  "used_today": 5,
  "remaining_today": 15
}
```

字段说明：
- `daily_prejudge_limit`: `integer`，当前设定的每日预判页数上限（0–200，0 为关闭）。
- `used_today`: `integer`，今日已调用的预判大模型请求次数。
- `remaining_today`: `integer`，今日剩余可用预判次数。

---

## 3. 修改列表预判设置 (`PUT /v1/prejudge/settings`)

更新列表预判每日上限，立即热生效。

- **Method**: `PUT`
- **Path**: `/v1/prejudge/settings`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (Request Body)

```json
{
  "daily_prejudge_limit": 50
}
```

- `daily_prejudge_limit`: `integer`，范围 0..200（必填）。0 表示关闭列表预判。

### 成功响应 (`200 OK`)

```json
{
  "ok": true,
  "daily_prejudge_limit": 50,
  "used_today": 5,
  "remaining_today": 45
}
```

### 错误响应

- `422 Unprocessable Entity`: 参数超出 0..200 限制
  ```json
  {"error": "invalid_payload", "message": "daily_prejudge_limit 必须在 0 到 200 之间"}
  ```
