# API Contracts: 本机接口规范 (009-resume-pdf)

**Feature**: `009-resume-pdf`
**Base URL**: `http://127.0.0.1:47615`
**Authentication**: 所有业务接口均通过现有本机配对鉴权，请求头携带 `Authorization: Bearer <jetToken>`。

---

## 1. 上传 PDF 并生成简历画像 (`POST /v1/resumes/upload`)

上传一份简历 PDF（Base64 编码），在服务端内存中提取全部页面文字、进行本地强脱敏，随后调用 DeepSeek 提炼简历画像并落库。

- **Method**: `POST`
- **Path**: `/v1/resumes/upload`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (Request Body)

```json
{
  "slot": 1,
  "name": "Python高级开发简历",
  "pdf_base64": "JVBERi0xLjQKJcTl8uXr...",
  "self_name": "李明"
}
```

#### 字段定义与约束：
- `slot`: `integer`，取值 `1`、`2` 或 `3`（必填）。
- `name`: `string`，简历名称（必填，长度 1..100 字符）。默认取自文件名去 `.pdf`，允许用户修改。仅在本地展示，绝不发给模型。
- 不含原始文件名（体检第 79 条起不再发送 `filename`：原始文件名常带真名，服务端从不使用；旧插件多带这个字段也会被忽略。）
- `pdf_base64`: `string`，PDF 二进制内容的 Base64 编码字符串（必填）。解码后字节大小不得超过 5MB（5 * 1024 * 1024 字节）。
- `self_name`: `string | null`，可选。用户在前端输入的主动姓名，用于辅助本机脱敏，绝不向外泄露。

---

### 成功响应 (`200 OK`)

成功提取文字、脱敏并生成画像入库。

```json
{
  "slot": 1,
  "name": "Python高级开发简历",
  "profile": "适合方向：Python 后端架构、高并发微服务与分布式系统研发。\n亮点：\n1. 具备大型分布式交易与结算系统核心重构实战经验；\n2. 精通 FastAPI/Django 框架，深入掌握 MySQL 索引与慢查询调优；\n3. 熟练运用 Redis 分布式锁与 Kafka 消息削峰，保障系统高可用。",
  "text_chars": 1580
}
```

字段说明：
- `slot`: 简历编号（1–3）。
- `name`: 最终保存的简历名称。
- `profile`: 大模型生成（或校验截断后）的结构化画像文本（≤300 字）。
- `text_chars`: 提取出的简历正文字符数。

---

### 降级响应与业务错误

#### 场景 1：有效文字不足（扫描件或图片 PDF）(`422 Unprocessable Entity`)

去除空白后字符数 $< 50$：

```json
{
  "error": "no_text",
  "message": "无法读取文字，请上传文字版 PDF"
}
```

#### 场景 2：PDF 加密或损坏 (`422 Unprocessable Entity`)

文件不可读或带有打开密码：

```json
{
  "error": "pdf_invalid",
  "message": "PDF 文件已加密或已损坏，无法解析"
}
```

#### 场景 3：无法识别姓名（三来源均空）(`422 Unprocessable Entity`)

用户表中无 display_name、请求未带 self_name 且 PDF 正文未提取出姓名：

```json
{
  "error": "self_name_required",
  "message": "没有识别出你的姓名，请在上传区填写姓名后重试（只在本机用于去掉姓名）"
}
```

#### 场景 4：文件过大 (`413 Payload Too Large` 或 `422`)

Base64 解码后大小 $> 5\text{MB}$：

```json
{
  "error": "file_too_large",
  "message": "PDF 文件大小不能超过 5MB"
}
```

#### 场景 5：每日画像生成配额耗尽 (`429 Too Many Requests`)

今日已调用画像生成达到 `daily_resume_profile_limit`。**特别说明**：服务端仍将解析出的 `name` 与 `resume_text` 正常保存至该 slot；该 slot 原来是同一份简历正文时 `profile` 保持旧值，换了另一份简历或原来没有时置空，响应里的 `profile` 是保存后的值（未配 Key、画像生成失败 `502 llm_failed` 时同样处理）：

```json
{
  "error": "quota_exhausted",
  "message": "今日简历画像生成额度已用完（文字已保存，请明天重试生成或手动填写）",
  "slot": 1,
  "name": "Python高级开发简历",
  "has_text": true,
  "text_chars": 1580,
  "profile": ""
}
```

#### 场景 6：未配置大模型 API Key (`409 Conflict`)

同样保存 `name` 与 `resume_text`，`profile` 按场景 5 的规则保留或置空：

```json
{
  "error": "no_llm_key",
  "message": "未配置 API Key（文字已保存，配置 Key 后可生成画像）",
  "slot": 1,
  "name": "Python高级开发简历",
  "has_text": true,
  "text_chars": 1580,
  "profile": ""
}
```

---

## 2. 重新生成简历画像 (`POST /v1/resumes/{slot}/regenerate`)

利用数据库中该 slot 已保存的 `resume_text`，重新执行姓名识别、脱敏、配额预占并调用大模型生成画像。

- **Method**: `POST`
- **Path**: `/v1/resumes/{slot}/regenerate` (slot 为 1、2 或 3)
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (Request Body)

```json
{
  "self_name": "李明"
}
```

`self_name`: 可选姓名覆盖。

### 成功响应 (`200 OK`)

```json
{
  "slot": 1,
  "name": "Python高级开发简历",
  "profile": "适合方向：Python 后端架构、高并发微服务与分布式系统研发。\n亮点：\n1. 具备大型分布式系统架构与微服务实战经验；\n2. 精通 FastAPI/MySQL/Redis 架构体系；\n3. 熟悉 Kubernetes 容器化部署。",
  "text_chars": 1580
}
```

### 错误响应

- `404 Not Found`: 该 slot 尚未保存或无 `resume_text` 文本。
  ```json
  {"error": "resume_not_found", "message": "该简历槽位未上传过文字版简历，请先上传 PDF"}
  ```
- `429 Too Many Requests`: 配额耗尽。

---

## 3. 查询所有简历列表 (`GET /v1/resumes`)

获取当前用户已录入的简历元数据与画像。

- **Method**: `GET`
- **Path**: `/v1/resumes`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)

### 成功响应 (`200 OK`)

```json
[
  {
    "slot": 1,
    "name": "Python架构方向",
    "profile": "适合方向：后端架构师/技术专家。\n亮点：\n1. 十万级 QPS 高并发架构实战；\n2. 擅长微服务治理与性能调优。",
    "has_text": true,
    "text_chars": 1580,
    "updated_at": "2026-09-30T02:00:00Z"
  },
  {
    "slot": 2,
    "name": "数据分析方向",
    "profile": "适合方向：商业数据分析师。\n亮点：\n1. 精通 SQL 与 Python 数据挖掘；\n2. 具备用户增长与精细化运营分析经验。",
    "has_text": true,
    "text_chars": 1210,
    "updated_at": "2026-09-30T02:10:00Z"
  }
]
```

> [!NOTE]
> 为保障接口响应性能与用户隐私，本接口绝不返回 `resume_text` 全文，仅返回 `has_text` 状态与字数 `text_chars`。

---

## 4. 保存简历修改 (`PUT /v1/resumes`)

用户在设置页手动编辑简历名称或调整画像内容后，进行原子保存。本操作不调用大模型、不修改底层已保存的 `resume_text`。

- **Method**: `PUT`
- **Path**: `/v1/resumes`
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)
  - `Content-Type`: `application/json`

### 请求体 (Request Body)

支持直接传入数组，或包裹在 `resumes` / `items` 属性中：

```json
{
  "items": [
    {
      "slot": 1,
      "name": "Python架构方向（精简）",
      "profile": "适合方向：Python 后端高并发研发。\n亮点：\n1. 5 年微服务核心研发经验；\n2. 深入掌握 MySQL 与 Redis 性能调优；\n3. 熟悉分布式事务与任务调度。"
    }
  ]
}
```

#### 校验规则：
- 数组长度：0..3；
- `slot`: 1..3 且不重复；
- `name`: 去除首尾空格后非空，长度 ≤ 100；
- `profile`: 去除首尾空格后非空，长度 ≤ 300。

### 成功响应 (`200 OK`)

```json
{
  "ok": true,
  "count": 1
}
```

### 错误响应

- `422 Unprocessable Entity`: 参数校验失败（如画像超长或为空）：
  ```json
  {"error": "resume_invalid", "message": "第 1 份简历画像不能超过 300 字"}
  ```

---

## 5. 删除简历 (`DELETE /v1/resumes/{slot}`)

删除指定 slot 的简历记录，彻底清除该 slot 的名称、提取源文本与画像。

- **Method**: `DELETE`
- **Path**: `/v1/resumes/{slot}` (slot 为 1、2 或 3)
- **Headers**:
  - `Authorization`: `Bearer <token>` (必填)

### 成功响应 (`200 OK`)

```json
{
  "ok": true,
  "slot": 1
}
```

### 错误响应

- `422 Unprocessable Entity`: 非法 slot 编号。
