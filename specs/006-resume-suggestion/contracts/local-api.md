# 本机接口契约变更 (006)

> **2026-10-06 补记（体检第 61 条）：简历部分已被 009 取代。** 009 起简历在设置页上传 PDF：上传时把**脱敏后的简历正文（最多 8000 字）**发给 DeepSeek 生成「简历画像」，判断时发送画像（编号简历 1/2/3，不发简历名称）。本文件里「不发送任何简历内容」「只发编号与适合的岗位类型」的说法已不成立，发送内容以 `specs/009-resume-pdf/` 为准；模型只输出简历编号 `slot` 和理由，接口返回的简历建议为 `{slot, name, reason}`（`name` 由本机按编号填回），不再有方向代号和文件名；文中提到的 `resume_directions.json` 已删除。

> **2026-09-29 修订**：简历方向不再放在仓库配置文件 `resume_directions.json`，改为设置页"我的简历"（本机数据库 `resume_slots`，编号 1–3，只发编号与适合的岗位类型）；从严行业改为设置页勾选（`strict_industry_selection`，迁移 v7）。本文件中涉及 `resume_directions.json` 与方向代号（`ai_ops` 等）的设计已被 `tasks.md`"2026-09-29 修订"一节取代，以该节与 `spec.md` 为准。


**Feature**: `006-resume-suggestion` | **Date**: 2026-09-29

在 002、003、004、005 既有接口契约基础上增补，未列出的端点与行为保持不变。

---

## 一、概述与影响端点清单

简历建议数据均作为判断对象（Judgement Dict）的衍生属性，通过既有端点中的规范岗位条目（Canonical `_job_entry`）统一返回。从严行业改为设置页勾选后，新增两个独立端点 `GET` / `PUT /v1/strict-industries`，见第五节（2026-10-06 体检第 57 条补记）。

受影响端点：
1. `GET /v1/judgements?ids=...`
2. `POST /v1/observations`
3. `POST /v1/jobs/{platform_job_id}/judge`
4. `POST /v1/chat/job`
5. `GET /v1/my-jobs`

---

## 二、规范岗位条目中的 `resume_suggestion` 契约

在所有返回岗位条目的响应中，`jobEntry.judgement` 对象新增属性 `resume_suggestion`。

### 1. 有简历建议时（v6 大模型判断且结论非 skip）

```json
{
  "jobs": {
    "abc123xyz~": {
      "completeness": "full",
      "company_name": "某某智能科技有限公司",
      "title": "AI产品运营专家",
      "judgement": {
        "status": "done",
        "verdict": "apply",
        "verdict_reason": "核心职责为提示词调试与内容运营，匹配画像方向",
        "resume_suggestion": {
          "direction": "ai_ops",
          "name": "AI / 运营",
          "file_name": "简历A.pdf",
          "reason": "岗位工作以大模型提示词设计和用户运营为主，高度匹配该简历方向"
        },
        "hr_questions": [],
        "stale": {
          "job_changed": false,
          "profile_changed": false,
          "method_changed": false
        },
        "prompt_version": "v6"
      }
    }
  }
}
```

### 2. 无简历建议时（v5 及更早老判断 / 规则排除 / 输出格式不符 / 配置文件失效）

```json
{
  "jobs": {
    "old456def~": {
      "completeness": "full",
      "company_name": "某某地产营销策划有限公司",
      "title": "渠道拓展主管",
      "judgement": {
        "status": "done",
        "verdict": "skip",
        "verdict_reason": "属于重点排查行业（房地产），涉变相销售",
        "resume_suggestion": null,
        "hr_questions": [],
        "stale": {
          "job_changed": false,
          "profile_changed": false,
          "method_changed": false
        },
        "prompt_version": "v5"
      }
    }
  }
}
```

---

## 三、各端点行为与约束

### 1. GET /v1/judgements
- **请求参数**：`ids=<encrypt_job_id>`
- **返回行为**：
  - 查询数据库中对应岗位的最新有效判断行；
  - 调用 `to_api` 转换为 Judgement 字典；
  - 根据判断行的 `resume_direction` 与 `resume_reason`，由本机当前 `resume_directions.json` 解析文件名与中文名；
  - 若代号在配置中已被用户删除，`resume_suggestion` 返回 `null`。

### 2. POST /v1/observations
- **请求载荷**：页面观察数据（列表或详情）
- **返回行为**：
  - 详情观察返回的 `jobs[pid].judgement` 包含 `resume_suggestion`；
  - 若为首次观察且由规则直接排除，`resume_suggestion` 为 `null`。

### 3. POST /v1/jobs/{platform_job_id}/judge
- **请求载荷**：`{ "force": true }`（重新判断）
- **返回行为**：
  - 手动触发的重新判断统一采用配置的默认版本（升级后为 `"v6"`）；
  - 任务入队后返回判断占位对象（`status="queued"`，`resume_suggestion=null`）；
  - 后台 Worker 执行完成后，轮询即可获取带有 `resume_suggestion` 的新结果。

### 4. POST /v1/chat/job
- **请求载荷**：`{ "platform_job_id": "...", "title": "...", "company_name": "..." }`
- **返回行为**：
  - 聊天被动入库岗位为 `completeness='list_only'` 且无判断，返回对象中 `judgement: null`，不涉及 `resume_suggestion`。

### 5. GET /v1/my-jobs
- **返回行为**：
  - 岗位库列表条目目前不新增独立简历建议列；返回条目中保留现有 `verdict`、`verdict_label` 等字段；
  - 详情弹层打开该岗位时按 `GET /v1/judgements` 或观察结果展示简历建议。

---

## 四、插件内部消息契约（`chrome.runtime`）

### 1. `get_chat_job_status`
- **调用方**：`sidepanel.js`
- **处理方**：`background.js`
- **响应载荷**：
  ```javascript
  {
    ok: true,
    jobId: "abc123xyz~",
    jobStatus: {
      platform_job_id: "abc123xyz~",
      in_library: true,
      title: "AI产品运营",
      company_name: "某某科技",
      verdict_label: "适合投递",
      verdict_tone: "green",
      summary_reason: "职责匹配，双休无销售",
      stale: false,
      resume_suggestion: {
        direction: "ai_ops",
        name: "AI / 运营",
        file_name: "简历A.pdf",
        reason: "岗位工作以大模型提示词设计和用户运营为主"
      }
    }
  }
  ```

### 2. `chat_job_status_updated`
- **广播方**：`background.js`
- **接收方**：`sidepanel.js`
- **载荷字段**：`{ type: "chat_job_status_updated", tabId: number, jobId: string, jobStatus: object }`
- **同步要求**：`jobStatus` 内部结构包含上述 `resume_suggestion` 属性。

---

## 五、从严行业勾选端点（2026-10-06 体检第 57 条补记）

两个端点都需要配对（未配对返回 401 `unpaired`）。设置页「从严行业」卡片读写走这两个端点。

### 1. GET /v1/strict-industries

- **成功响应 (200 OK)**：

```json
{
  "available": ["餐饮", "保险", "汽车", "房地产", "美妆", "快消", "金融"],
  "selected": ["保险", "金融"]
}
```

- `available`：规则文件 `src/jet/llm/prompts/strict_industry_rules.json` 里的行业，按文件顺序；文件缺失或格式错误时为空数组。
- `selected`：当前用户勾选的行业（表 `strict_industry_selection`）。

### 2. PUT /v1/strict-industries

- **请求体 (JSON)**：`{ "selected": ["保险", "金融"] }`，整组替换当前用户的勾选。
- **处理**：去掉首尾空白、跳过空字符串、去重，按规则文件里的顺序保存。
- **成功响应 (200 OK)**：

```json
{ "ok": true, "selected": ["保险", "金融"], "count": 2 }
```

- **错误响应 (422)**：含规则文件里没有的行业时，`{ "error": "invalid_industry", "message": "未知的从严行业: …" }`，原有勾选不变；`selected` 不是字符串数组时，由请求校验直接返回 FastAPI 标准的 422（`detail` 字段）。
- 修改勾选不改变画像版本，已有判断不会因此变为「可能过时」（`tests/api/test_strict_industries_api.py`）。
