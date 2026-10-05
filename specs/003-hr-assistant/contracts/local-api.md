# 契约：本机 Jet HTTP 接口 (003 HR 助手与岗位库改版)

- 地址：`http://127.0.0.1:<port>`，默认端口 `47615`。**只绑定 `127.0.0.1`**。
- 格式：JSON，UTF-8。时间格式为 UTC ISO-8601。
- 鉴权：除 `/v1/health` 和 `/v1/pair` 外，所有接口强制要求：
  - `Authorization: Bearer <token>`（配对令牌）；
  - `Origin`：若携带则必须匹配配对的扩展 ID（`chrome-extension://<扩展 ID>`），禁止普通网页调用（403 `forbidden_origin`）。
- 错误格式统一为：`{ "error": "<code>", "message": "给人看的中文说明" }`。

---

## 错误码与侧边栏映射表 (对应 spec R2)

| HTTP 状态 | error 代码 | 侧边栏/页面展示文案 | 是否调用大模型 |
|---|---|---|---|
| (连接失败) | — | "Jet 没有运行，请先启动本机 Jet" | 否 |
| 401 | `unpaired` | "未配对" | 否 |
| 403 | `consent_required` | 弹出数据发送知情同意页 | 否 |
| 429 | `quota_exhausted` | "今日生成次数已用完" | 否 |
| 400 | `hash_mismatch` | "发送内容校验不一致，请重新预览后再试" | 否 |
| 422 | `self_name_unavailable` | "读不到你的姓名，暂时不能生成" | 否 |
| 422 | `all_suggestions_dropped` | "这次没有可用的建议，可以再点一次生成"（话术、问题均为空且无未答事实与请求卡片提示时触发） | **已调用（计入次数）** |
| 502 / 504 | `llm_failed` | "生成失败：<原因>，可以再点一次生成" | **已调用（计入次数）** |
| 422 | `invalid_payload` | "参数错误或缺少必要字段" | 否 |
| 404 | `not_found` | "指定资源不存在" | 否 |

---

## 1. POST /v1/chat/preview (脱敏与发送预览)

用户首次使用、撤回同意后或点击查看发送内容时调用。本机 Jet 服务执行纯函数脱敏并组装即将发送给大模型的完整文本内容，计算 SHA-256 哈希值并返回。

- **是否调用大模型**：**否**（仅在服务端进行本地脱敏与哈希计算）
- **路径**：`POST /v1/chat/preview`
- **请求体 (JSON)**：

```json
{
  "encrypt_job_id": "0000aaaa1111bbbbX1-fakeJobId1",
  "job_title": "储能海外销售（驻尼日利亚等）",
  "company_name": "某新能源科技公司",
  "location_name": "深圳",
  "hr_name": "刘女士",
  "user_name": "张三",
  "messages": [
    {
      "sender": "我",
      "text": "您好，请问这个岗位还在招人吗？",
      "is_self": true,
      "is_system": false,                  // 可选：系统消息（如"附件简历已发送"），未单独处理的系统消息不发给模型（2026-10-06 补）
      "type": 3,
      "body_type": 1,
      "time": 1727230000000,
      "mid": 1001,
      "quote_id": null
    },
    {
      "sender": "HR",
      "text": "在招的，这是我的电话 13800138000，微信同号，方便发份简历吗？",
      "is_self": false,
      "type": 1,
      "body_type": 1,
      "time": 1727230100000,
      "mid": 1002,
      "quote_id": null
    },
    {
      "sender": "HR",
      "text": "我想要一份您的附件简历，您是否同意",
      "is_self": false,
      "type": 1,
      "body_type": 7,
      "time": 1727230200000,
      "mid": 1003,
      "quote_id": 1001
    }
  ]
}
```

- **处理逻辑**：
  1. 提取结构化字段：职位名（优先页面顶部完整名）、公司名、城市；
  2. 结合本机 Jet 数据：查找该岗位是否存在 Jet 判断结果（如有则提取结论、白话职责、销售对接成分、风险信号、建议问 HR 问题；无判断则标记 `has_jet_judgement=false`）、HR 实际情况（如有则提取全文 ≤ 200 字）、当前用户的全部「我的经历素材」（最多 10 条）、语气规则文件；
  3. 执行**纯函数脱敏**（FR-021、FR-022、FR-073）：
     - 剔除 HR 姓名（`hr_name` 原词及其在正文中的出现）；
     - 剔除我方姓名：检查结构化字段 `user_name`；若读不到我方姓名（为空或未提供），返回 422 `self_name_unavailable`，提示"读不到你的姓名，暂时不能生成"，不调用大模型（2026-09-26 用户决定 7-A）；
     - 剔除"姓 + 称呼"写法（如"刘女士"、"刘经理"、"刘总"等）；
     - 剔除电话号码、微信号、电子邮箱；
     - 过滤掉系统卡片（`bodyType=16`、`item-system` 样式等）；对 `bodyType=7` 卡片仅提取卡片文本并脱敏，且输出的消息中携带是否为 HR 请求卡片的标记（`is_request_card: true`）；
     - 自动招呼语（`type=3, bodyType=1`）归入我方消息；
     - 最多取最近 30 条已加载非系统消息；
     - 引用回复上下文提取（FR-073）：
       - 请求中携带 `mid`（数字或 null）与 `quote_id`（数字或 null）；
       - 在过滤、动作卡片处理及截取最近 30 条后，对保留的消息若 `quote_id` 非空，在同一批保留发送的消息中查找 `mid == quote_id` 的那一条：
         - 找到：生成 `quote_context` = `{"from": "我" 或 "HR", "text": "已脱敏文字截断至最多 40 字（超长加'……'）"}`；
         - 找不到（在 30 条窗口外、被过滤掉或 mid 缺失）：生成 `quote_context` = `{"from": null, "text": null}`；
         - 无 `quote_id` 的消息不加 `quote_context` 字段；`mid` 与 `quote_id` 不进入 sanitize 输出，不发送给模型；
  4. 组装发往大模型的完整 Prompt 文本与脱敏预览显示格式：
     - 普通消息：`发送方：正文`；
     - HR 请求卡片前标出【HR 请求卡片】；
     - 引用回复消息显示格式：
       - 找到：`HR（回复我：'被引用原文'）：都有的`；被引用方与发送方相同时写"回复自己"；我引用 HR 时写 `我（回复HR：'……'）：……`；
       - 找不到：`HR（回复了一条较早的消息）：都有的`（我方同理：`我（回复了一条较早的消息）：……`）；
       - 被引用原文仅作为上下文展示，不并入消息正文 `text`，所有基于正文的判定（沟通模式、卡片识别、未答事实检测、问题去重）均不受 `quote_context` 影响；
     - 计算完整文本的 SHA-256 字符串（`prompt_hash`）；
  5. 自动判定当前沟通阶段（开场白 `opening` / 建议回复 `reply` / 正在等 HR 回复 `waiting_hr`）并给出判定依据消息。

- **成功响应 (200 OK)**：

```json
{
  "fields": [
    "职位名、公司、城市",
    "Jet 判断结论与风险信号（如有）",
    "HR 实际情况（如有，已脱敏）",
    "最近已加载的非系统聊天消息（已脱敏）",
    "我的经历素材（已录入条目）",
    "语气规则（8 条）",
    "我的资料：目前所在城市（填写时才有）"
  ],
  "sanitized_prompt": "【岗位信息】\n职位：储能海外销售（驻尼日利亚等）\n公司：某新能源科技公司\n城市：深圳\n...\n【聊天记录】\n我：您好，请问这个岗位还在招人吗？\nHR：在招的，方便发份简历吗？\n【HR 请求卡片】HR：我想要一份您的附件简历，您是否同意\n...",
  "prompt_hash": "b2c943ef17688c83a1290342938cf15a81239128301823901238912830129381",
  "mode": "reply",
  "mode_label": "建议回复",                // opening=开场白 / reply=建议回复 / waiting_hr=正在等 HR 回复
  "mode_basis": "HR：方便发份简历吗？",
  "message_count": 3,
  "has_jet_judgement": true,
  "has_consent": false
}
```

---

## 2. POST /v1/chat/consent (记录用户同意)

用户在同意预览页点击"同意"时调用。

- **是否调用大模型**：**否**
- **路径**：`POST /v1/chat/consent`
- **请求体 (JSON)**：`{}`。字段版本由服务端决定，请求不用带版本号；带了也会被忽略（2026-10-05 体检第 47 条修订）。

- **处理逻辑**：
  在 `llm_consents` 表中更新或插入记录，设置 `consented_at = utc_now()`, `revoked_at = NULL`, `fields_version = CONSENT_FIELDS_VERSION`（`src/jet/domain/consent.py`，当前为 3；发送字段变化时升版本，旧的同意需要重新确认）。

- **成功响应 (200 OK)**：

```json
{
  "ok": true,
  "consented_at": "2026-09-26T05:30:00Z"
}
```

---

## 3. POST /v1/chat/revoke-consent (撤回同意)

用户在插件设置页点击"撤回同意"时调用。

- **是否调用大模型**：**否**
- **路径**：`POST /v1/chat/revoke-consent`
- **请求体 (JSON)**：`{}`
- **处理逻辑**：
  在 `llm_consents` 表中设置 `revoked_at = utc_now()`。此后再次点击生成必须重新展示同意页。

- **成功响应 (200 OK)**：

```json
{
  "ok": true,
  "revoked_at": "2026-09-26T06:00:00Z"
}
```

---

## 4. GET /v1/chat/consent-status (查询同意状态)

查询当前用户是否具有有效的大模型发送同意。

- **是否调用大模型**：**否**
- **路径**：`GET /v1/chat/consent-status`
- **成功响应 (200 OK)**：

```json
{
  "has_consent": true,
  "consented_at": "2026-09-26T05:30:00Z",
  "fields_version": 3
}
```

---

## 5. POST /v1/chat/generate (生成话术)

用户在侧边栏点击"生成"（或手动切换类型重新生成）时调用。

- **是否调用大模型**：**是**（调用 DeepSeek completions 接口，单次完成，关闭思考模式）
- **路径**：`POST /v1/chat/generate`
- **请求体 (JSON)**：

```json
{
  "encrypt_job_id": "0000aaaa1111bbbbX1-fakeJobId1",
  "job_title": "储能海外销售（驻尼日利亚等）",
  "company_name": "某新能源科技公司",
  "location_name": "深圳",
  "hr_name": "刘女士",
  "user_name": "张三",
  "messages": [ ... ],
  "prompt_hash": "b2c943ef17688c83a1290342938cf15a81239128301823901238912830129381",
  "force_mode": null
}
```

- **参数说明**：
  - `prompt_hash`: 用户在预览或点击时确认的 Prompt SHA-256 哈希值；
  - `force_mode`: 可选 `opening` 或 `reply`。当用户认为系统自动判定不准、手动点击切换重新生成时传入。
- **处理逻辑**：
  1. **检查同意记录**：若未同意或已撤回（或 `fields_version` 不匹配），返回 403 `consent_required`；
  2. **检查我方姓名**：若读不到我方姓名（`user_name` 为空或缺失），返回 422 `self_name_unavailable`，提示"读不到你的姓名，暂时不能生成"，不调用大模型，不预占额度、不写 `llm_calls`（2026-09-26 用户决定 7-A）；
  3. **防篡改与纯函数一致性比对**：
     - 服务端用同样的确定性纯函数对 `messages` 及上下文执行脱敏并拼装发送报文，计算本地 SHA-256；
     - 若本地计算的哈希与客户端传入的 `prompt_hash` 不一致：立即拒绝并返回 400 `hash_mismatch`，不预占额度、不写 `llm_calls`（杜绝未经脱敏或篡改的数据发给模型）；
  4. **检查并预占每日额度**：前面检查都通过后，才在 `BEGIN IMMEDIATE` 事务中检查今日 `purpose='assist'` 且 `billed=1` 的调用次数是否达到 `daily_assist_limit`。若已达到，返回 429 `quota_exhausted`；
  5. **特殊分支处理**：
     - 若当前状态为"我方最后说话且 HR 尚未回复"（`waiting_hr`）且未指定 `force_mode`：不生成建议回复话术，仅生成最多 3 个建议问 HR 的问题，侧边栏提示"正在等 HR 回复"（FR-012 第 3 条）；
  6. **调用大模型**：
     - 通过 `call_once` 发送至 DeepSeek，关闭思考模式（`thinking: {"type": "disabled"}`），超时 20 秒；
     - 记录调用结果到 `llm_calls` 表（`purpose='assist'`，记录 Token 与费用，绝对不存文本内容）；
   7. **大模型返回核验（FR-031, FR-054, FR-064, FR-069）**：
     - 模型输出中包含的 `known_facts`（HR 在聊天中已经明确的信息，最多 8 条）仅作为模型前置核对信息，**不存储（不写库、不写日志），且接口响应绝不包含 known_facts 字段及其文字**；
     - 检查返回的建议话术中引用的经历条目编号（`referenced_experience_ids`）；
     - 校验编号是否在当前用户的 `experience_items` 中真实存在；引用不存在编号的话术**直接丢弃**；
     - 对规则文件（`self_fact_rules.json`）中列出的关于我的事实类别（所在城市、期望薪资、到岗时间、出差或外派），若经历素材中未写明，含有该类第一人称陈述的话术**直接丢弃**（FR-054）；
     - 对规则文件（`completed_action_rules.json`）中列出的完成声称（如"简历已发送"），含有该类第一人称陈述的话术**直接丢弃**（FR-064）；
     - 话术原文本身超过 50 字的话术**直接丢弃**；改写后若超过 50 字则不改写保留原文，不予丢弃（FR-069）；
     - 记录每个被丢弃版本的原因（引用不存在的经历编号 `invalid_experience` / 资料里没有的事实类别 `unsupported_fact` / 声称已完成操作 `claims_done` / 原文超过 50 字 `too_long`），汇总生成 `dropped_summary`（只含原因代码、类别、数量和各自提示文字，不含话术原文）；
     - 检查经历素材是否为空：若为空，注明"没有可引用的经历"，生成 2 个都不提经历的版本（FR-032）；
     - 校验返回的问题：排除已在当前聊天中出现过、或在 HR 实际情况中已有明确答案的问题（FR-015）；
     - 说明：只有话术、问题都为空且 `unanswered_facts` 为空、`request_notice` 为 null 时才返回 422 `all_suggestions_dropped` 错误，否则返回 200（`suggestions` 可以为空数组）；
   8. **未答事实与请求卡片提示判定（FR-054, FR-055）**：
     - 根据最近的 HR 消息是否命中某类提问关键词且经历素材没有写明，由代码生成 `unanswered_facts`（每项 `{"name": "...", "notice": "HR 问了..."}`）；
     - 根据消息顺序判断 HR 请求卡片是否出现在我最后一条消息之后，由代码生成 `request_notice`（字符串或 null）。

- **成功响应 (200 OK)**：

```json
{
  "company_name": "某新能源科技公司",
  "job_title": "储能海外销售（驻尼日利亚等）",
  "mode": "reply",
  "mode_label": "建议回复",
  "mode_basis": "HR：方便发份简历吗？",
  "has_jet_judgement": true,
  "message_count": 3,
  "suggestions": [
    {
      "version": 1,
      "tone_desc": "自然直接",
      "text": "好的呀，我这就发您一份。请问贵公司在尼日利亚那边的具体业务开展情况大概是怎样的呢？",
      "referenced_experience_ids": [1]
    },
    {
      "version": 2,
      "tone_desc": "沉稳专业",
      "text": "嗯嗯好的，简历已通过附件发您，您查收看看。另外想先确认一下该岗位主要负责对接哪些类型的客户？",
      "referenced_experience_ids": [1]
    }
  ],
  "experience_note": null,
  "questions": [
    "尼日利亚那边的业务目前是刚起步还是已经有成熟渠道？",
    "想确认一下平时主要对接的是海外代理商还是终端客户？"
  ],
  "quota_remaining": 47,
  "unanswered_facts": [
    {
      "name": "所在城市",
      "notice": "HR 问了所在城市，你的资料里没有，这部分请你自己回答"
    }
  ],
  "request_notice": "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
  "dropped_summary": [
    {
      "reason": "unsupported_fact",
      "category": "所在城市",
      "count": 2,
      "text": "2 个版本因提到你资料里没有的'所在城市'被去掉"
    }
  ]
}
```

> **注**：在"正在等 HR 回复"模式下，`mode = "waiting_hr"`, `suggestions = []`, `questions = ["...", "..."]`。当话术全部被丢弃但仍有问题或提示时，`suggestions = []`，`dropped_summary` 列出丢弃原因与数量。

### dropped_summary 原因代码与提示文字对照表

| 原因代码 (`reason`) | 类别 (`category`) | 提示文字 (`text`) | 触发说明 |
|---|---|---|---|
| `too_long` | `null` | `N 个版本因原文超过 50 字被去掉` | 模型原文本身超过 50 字（改写后若超过 50 字则不改写保留原文，不计入此项） |
| `invalid_experience` | `null` | `N 个版本因引用了不存在的经历条目被去掉` | 引用了不存在的经历条目编号 |
| `claims_done` | 动作类别（如"简历已发送"） | `N 个版本因声称'X'这类还没做的操作被去掉` | 话术含有规则文件中声称已完成某操作的第一人称表述 |
| `unsupported_fact` | 事实类别（如"所在城市"） | `N 个版本因提到你资料里没有的'X'被去掉` | 经历素材未写明该事实，话术含有该类第一人称陈述（【填写：…】占位先去掉再判断，见 FR-054 修订） |
| `resume_already_sent` | `"简历已发送"` | `简历已发送，去掉了要再发简历的话术`（多于 1 个时为 `N 个版本因简历已发送，去掉了要再发简历的话术`） | 聊天里已出现附件简历已发送或 HR 已查看的系统消息，话术仍表示要发简历（规则文件 `resume_sent_rules.json`） |

> **响应不含 known_facts 说明**：大模型输出中先列出的 `known_facts` 仅作为模型生成话术与提问时的前置核对信息；`known_facts` 不存储（不写入数据库、不记录日志），响应 JSON 中绝不包含 `known_facts` 字段及其文字。

- **失败响应示例**：
  - 未同意：`403 {"error": "consent_required", "message": "尚未同意发送脱敏数据"}`
  - 读不到姓名：`422 {"error": "self_name_unavailable", "message": "读不到你的姓名，暂时不能生成"}`（不调用大模型）
  - 额度用完：`429 {"error": "quota_exhausted", "message": "今日生成次数已用完"}`
  - 哈希不一致：`400 {"error": "hash_mismatch", "message": "发送内容哈希不一致，已被安全拦截"}`
  - 话术与问题均为空且无未答事实与请求提示：`422 {"error": "all_suggestions_dropped", "message": "这次没有可用的建议，可以再点一次生成"}`
  - 调用失败：`502 {"error": "llm_failed", "message": "生成失败：服务响应超时，可以再点一次生成"}`

---

## 6. GET /v1/experience · PUT /v1/experience (我的经历素材读写)

插件设置页编辑个人经历素材。

- **是否调用大模型**：**否**
- **GET /v1/experience**：
  - **响应 (200 OK)**：

```json
[
  {
    "item_no": 1,
    "content": "在新能源外贸公司负责西非市场的逆变器渠道拓展，主导拜访了 10 余家本地分销商，实现季度销售额翻倍。",
    "updated_at": "2026-09-26T04:00:00Z"
  },
  {
    "item_no": 2,
    "content": "独立带过 3 人的海外客户成功小团队，负责售后技术工单跟进，客户满意度提升 15%。",
    "updated_at": "2026-09-26T04:10:00Z"
  }
]
```

- **PUT /v1/experience**：
  - **请求体 (JSON)**：全量提交当前有效条目数组（最多 10 条，每条 ≤ 200 字，按 `item_no` 顺序 1–10 编号）：

```json
[
  {
    "item_no": 1,
    "content": "在新能源外贸公司负责西非市场的逆变器渠道拓展..."
  }
]
```

  - **校验规则**：
    - 数组长度 ≤ 10，超过返回 422 `too_many_items`；
    - 单条内容经 `strip()` 后不能为空，长度 ≤ 200 字，否则返回 422 `content_invalid`；
    - `item_no` 必须为 1 到 10 的整数且不重复；
  - **处理逻辑**：在一个事务中物理替换当前用户的 `experience_items`。
  - **响应 (200 OK)**：`{ "ok": true, "count": 1 }`

---

## 7. GET /v1/my-jobs (岗位库查询扩展)

扩展 002 原有的岗位库查询接口。

- **是否调用大模型**：**否**
- **路径**：`GET /v1/my-jobs`
- **查询参数**：
  - `filter`: 必选。新增 `all_jobs`（全部岗位：有状态 ∪ 判断过 ∪ 有 HR 记录），兼容 002 原有的 `all`（已标记）、`saved`、`applied`、`skipped`、`recent`；
  - `hr_only`: 可选，布尔值（默认 `false`）。为 `true` 时仅查看有 HR 实际情况记录的岗位；
  - `q`: 可选，搜索关键词。在当前标签和 `hr_only` 范围内搜索职位名（所有历史版本）、公司名、HR 实际情况；
  - `limit`: 可选，整数，默认 200。
- **响应体 (200 OK)**：

```json
{
  "counts": {
    "all_jobs": 158,
    "all": 32,
    "saved": 15,
    "applied": 12,
    "skipped": 5,
    "recent": 86
  },
  "total_matches": 245,
  "items": [
    {
      "platform_job_id": "0000aaaa1111bbbbX1-fakeJobId1",
      "title": "储能海外销售（驻尼日利亚等）",
      "company_name": "某新能源科技公司",
      "salary_raw": "18-28K·14薪",
      "city": "深圳",
      "verdict": "try",
      "verdict_label": "可以一试",
      "stale": false,
      "hr_note": "HR 说前半年在深圳培训，之后常驻尼日利亚办事处，有常驻补贴。",
      "hr_note_recorded": true,
      "my_status": "saved",
      "status_updated_at": "2026-09-26T04:20:00Z",
      "last_seen_at": "2026-09-26T05:10:00Z",
      "recent_updated_at": "2026-09-26T05:10:00Z",
      "url": "https://www.zhipin.com/job_detail/0000aaaa1111bbbbX1-fakeJobId1.html"
    }
  ]
}
```

- **说明**：
  - `counts`: 当 `hr_only = true` 时，返回的每个数字均为对应分类下有 HR 实际情况记录的岗位数量；
  - `total_matches`: 当前搜索条件下的全局去重总匹配数。若 `total_matches > 200`，列表上方提示"只显示了前 200 条，共 N 条"；
  - `hr_note`: 返回该岗位 HR 记录全文（≤ 200 字，无记录为 `null`）；
  - `recent_updated_at`: spec R7 计算出的最近更新时间，列表默认按此字段倒序排列。

---

## 8. PUT /v1/jobs/{platform_job_id}/hr-note (修改 HR 实际情况复用)

岗位库行内就地修改 HR 实际情况。复用 002 接口。

- **是否调用大模型**：**否**
- **路径**：`PUT /v1/jobs/{platform_job_id}/hr-note`
- **请求体 (JSON)**：

```json
{
  "note": "HR 告知主要负责对接非洲本地代理商，无冷启动陌生拜访要求。",
  "source": "myjobs"
}
```

- **参数说明**：
  - `note`: 新的 HR 实际情况文本内容（≤ 200 字）；
  - `source`: 可选，`myjobs`（来自岗位库列表）、`chat_sidebar`（来自聊天页侧边栏的「当前岗位」卡片）或 `card`（来自 BOSS 页面悬浮卡片）。
- **处理规则**：
  - 若 `source` 为 `myjobs` 或 `chat_sidebar` 且 `note` 经 `strip()` 后为空：返回 422 `empty_not_allowed`，提示"清空请到岗位卡片操作"（2026-09-26 用户决定 1-A）；
  - 若 `source == 'card'` 且 `note` 为空：允许清空（删除该行记录，保持 002 语义）。
- **响应体 (200 OK)**：

```json
{
  "jobs": {
    "0000aaaa1111bbbbX1-fakeJobId1": {
      "completeness": "full",
      "company_name": "某新能源科技公司",
      "judgement": { ... },
      "hr_note": "HR 告知主要负责对接非洲本地代理商，无冷启动陌生拜访要求。",
      "my_status": { "status": "saved", "updated_at": "..." }
    }
  }
}
```

---

## 9. PUT /v1/jobs/{platform_job_id}/status (行内修改状态复用)

岗位库行内直接修改岗位标记状态。

- **是否调用大模型**：**否**
- **路径**：`PUT /v1/jobs/{platform_job_id}/status`
- **请求体 (JSON)**：`{ "status": "saved" | "applied" | "skipped" | null }`（null 为取消状态）
- **响应体 (200 OK)**：`{ "jobs": { "<platform_job_id>": <最新岗位条目> } }`，不含各标签计数；岗位库页面在本地更新计数（2026-10-06 体检第 56 条修订）。
