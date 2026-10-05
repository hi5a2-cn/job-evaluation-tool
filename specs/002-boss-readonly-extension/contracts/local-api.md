# 契约：本机 Jet HTTP 接口 v1

- 地址：`http://127.0.0.1:<port>`，默认端口 `47615`（可配置）。**只绑定 `127.0.0.1`**，不绑定 `0.0.0.0` 或 `localhost`（避免解析到 IPv6 / 其他网卡）。
- 格式：JSON，UTF-8。时间为 UTC ISO-8601。
- 调用方：只有插件的后台 service worker（内容脚本不直接访问 Jet）。
- 认证：除 `GET /v1/health`、`POST /v1/pair` 和本机内部接口 `POST /internal/pair-code`（见下一条）外，所有请求都必须带
  - `Authorization: Bearer <token>`（配对时拿到的令牌）——这是真正的凭证
  - `Origin`：**如果带了**，必须与配对时记录的 `chrome-extension://<扩展 ID>` 一致；没带也接受。
    原因（2026-09-24 真实 Chrome 验证）：插件后台的 GET 请求到达 Jet 时没有 Origin 头，而 `POST /v1/pair` 带了；
    要求必带 Origin 会让配对后所有状态查询都变成"未配对"。网页无法读取插件保存的令牌，且带网页 Origin 的请求已被 403 拒绝。
  令牌不符 → `401 {"error":"unpaired"}`，插件显示"未配对"；Jet 终端打印拒绝原因（不含令牌）。
- 带 `Origin` 但不是 `chrome-extension://` 的请求（例如网页里的脚本）一律 `403`，并且不返回 CORS 允许头 —— 防止任意网页调用本机接口。
- 内部接口 `POST /internal/pair-code`（2026-10-06 体检第 60 条补记）：只给 `jet pair` 命令用，不需要令牌，靠三道限制保护，任一不满足都返回 `403 {"error":"forbidden"}`：
  1. 请求来源必须是 `127.0.0.1`；
  2. 不能带 `Origin` 头（浏览器里的网页脚本发不了）；
  3. `X-Jet-Admin` 头必须等于本次 `jet serve` 的管理密钥。
  管理密钥：`jet serve` 每次启动时随机生成，写入数据目录下的 `admin.secret`（只有本人可读写，权限 0600），服务退出时删除；`jet pair` 读取它调用本接口。
  成功返回 `{"code": "<6 位数字>", "expires_in": 300}`，配对码 5 分钟内有效，插件拿它调用 `POST /v1/pair`。
- 版本：路径前缀 `/v1`。不兼容的改动升 `/v2`。

## 错误格式

```json
{ "error": "<code>", "message": "给人看的中文说明" }
```

| HTTP | error | 插件显示 |
|---|---|---|
| 401 | `unpaired` | 未配对 |
| 403 | `forbidden_origin` | （不会出现在插件里） |
| 409 | `no_profile` | 请先设置画像 |
| 422 | `invalid_payload` | 页面无法识别（插件读取的数据缺字段时本地就会拦下，这里是兜底） |
| 连接失败 | — | Jet 未运行 |

## GET /v1/health（无需认证）

```json
{ "service": "jet", "api": 1, "version": "0.1.0" }
```

只用来判断"Jet 在不在运行"，不返回任何个人数据。

## POST /v1/pair（无需认证）

用户在终端运行 `jet pair`，得到一次性 6 位配对码（5 分钟有效，最多试 5 次），填到插件设置页。

请求：`{ "code": "482913" }`，Jet 从 `Origin` 头取扩展 ID。

- 200 → `{ "token": "<43 位随机串>", "user_id": "me" }`，插件存入 `chrome.storage.local`。
- 400 `{"error":"bad_code"}` / 429 `{"error":"too_many_attempts"}`。

## GET /v1/status

```json
{
  "user_id": "me",
  "profile": { "set": true, "version_no": 3 },
  "quota": { "date": "2026-09-24", "limit": 150, "used": 12, "remaining": 138 },
  "rule_excluded_today": 7,
  "labels": { "total": 12, "with_overall": 5 }
}
```

`remaining = max(limit − used, 0)`（FR-029）。指标口径见 spec"指标定义"。

## GET /v1/profile · PUT /v1/profile

```json
{
  "directions": ["数据分析", "产品经理"],
  "keywords": ["SQL", "增长"],
  "preferred_cities": ["深圳", "广州"],        // 原 cities；旧字段名 cities 仍接受（写入时视同 preferred_cities）
  "excluded_cities": ["哈尔滨"],
  "min_monthly_k": 20,
  "nonpref_min_monthly_k": 25,
  "exclude_keywords": ["外包", "驻场"],
  "work_preference": "想做数据分析、产品方向；不想做对接客户、背业绩、以营销推广为主的工作。",
  "background": "计算机本科，熟悉 Python，有一段后端实习。"
}
```

- GET 在没有画像时返回 `404 {"error":"no_profile"}`；有画像时除上面的字段外还返回 `version_no`、旧字段 `cities` 和 `current_city`（目前所在城市，010 起，可为空字符串）（2026-10-06 补）。
- PUT：`directions` 至少 1 项；`preferred_cities`、`excluded_cities` 可为空；关键词去掉首尾空白、去重；`work_preference`、`background` 选填，默认空字符串，各 ≤ 500 字，超长 → 422。内容与当前版本相同 → 200 且 `version_no` 不变；不同 → 新版本。
- 响应：`{ "version_no": 4, "changed": true }`。

## POST /v1/observations

插件每读到一次"新的页面状态"就发一次（FR-003/004）。

请求：

```json
{
  "page_type": "list",               // "list" | "detail"
  "observed_at": "2026-09-24T13:30:00Z",
  "jobs": [
    {
      "platform_job_id": "abc123~",
      "title": "数据分析师",
      "company_name": "某某科技",       // 读不到时为 null（2026-10-06 补：以下公司、经验、学历、标签字段由后续功能加入）
      "company_legal_name": null,       // 公司全称，独立职位页才有（004）
      "company_industry": "互联网",     // 公司行业（006）
      "experience": "1-3年",           // BOSS 经验要求标签（013）
      "degree": "本科",                // BOSS 学历要求标签（013）
      "job_labels": [],                 // 列表标签（004）
      "skills": ["SQL"],                // 技能标签（004）
      "salary_raw": "15-25K·14薪",     // 读到空值时为 null（Jet 记为"薪资不可见"）
      "city": "深圳",
      "district": null,
      "description": null              // 仅 page_type=detail 时必填，且只有 1 个 job
    }
  ]
}
```

处理（在一个事务里完成，不等待判断）：
1. 按 `platform_job_id` 入库 / 合并 / 生成新版本（见 data-model"变化判定"）。
2. 为当前用户每个岗位记一条查看记录。
3. `page_type = detail` 且有画像：若当前"岗位版本 × 画像版本"还没有现行判断 → 做规则粗筛；
   规则排除 → 直接写 `done / unfit / rule`；通过 → 建 `queued` 判断并交给后台（额度不足则直接 `quota_exhausted`）。
   已有现行判断 → 什么都不做（FR-021）。
4. 列表页不触发判断（FR-017）。

`page_type = detail` 时的自动更新（FR-052）：现行判断过时且用户状态不是 skipped / applied → 按重新判断的规则新建判断（`origin = auto_refresh`）；额度不足则不新建，响应 `notice = "auto_refresh_quota"` 并返回原判断。

响应（立即返回）：

```json
{
  "jobs": {
    "abc123~": { "completeness": "full", "judgement": { ...Judgement } }
  },
  "notice": null                     // 例如 "no_profile"、"auto_refresh_quota"；没有提示时为 null（2026-10-06 补）
}
```

列表页返回的 `judgement` 用于在卡片上打标记 / 在侧边栏列出"本页已判断过的岗位"；没有判断时为 `null`。

## GET /v1/judgements?ids=abc123~,def456~

插件在"判断中"或"复核中"时每 1.5 秒轮询一次（最多 150 秒，含复核；之后显示"判断仍在进行，稍后再看"）。返回同 observations 的 `jobs` 结构。

## POST /v1/jobs/{platform_job_id}/judge

用户点"重新判断"（可能过时）、"重试"（失败 / 中断）或"额度恢复后判断"。

- 请求体：`{}` 或 `{"force": true}`。`force` 为 true（卡片上对仍然有效的判断手动点"重新判断"）时，即使判断未过时也按当前版本新建判断并取代旧判断（受每日额度限制）；否则按下列规则。
- 该岗位必须是 `full`，否则 409 `{"error":"list_only"}`。
- 额度不足 → 200 并返回 `status = quota_exhausted` 的判断。
- 同一岗位已有 `queued/running` → 返回那一条（合并，FR-024）。

## Judgement 对象

```json
{
  "status": "done",                   // queued | running | done | failed | quota_exhausted | interrupted
  "verdict": "apply",                 // v4：apply | try | check | skip；旧判断可能为 fit | unsure | unfit；null
  "source": "llm",                    // rule | llm | null
  "reasons": ["方向匹配：数据分析", "薪资 15-25K 高于底线 20K 的下限区间", "要求 SQL，与关键词一致"],
  "judged_at": "2026-09-24T13:30:04Z",
  "error": null,
  "salary_visible": true,
  "prompt_version": "v4",             // rule | v1 | v2 | v3 | v4
  "verdict_reason": "职责以营销活动和对接银行为主，与偏好冲突",   // v3 起才有
  "hr_questions": ["实际对接银行客户的时间大概占多少？", "有没有个人业绩或拉新指标？"],   // v4 起才有：仅结论为 try / check 时有内容，否则为空数组
  "review": { "outcome": "downgraded", "engine": "deepseek-flash:think", "first_verdict": "try", "review_verdict": "check" },   // 复核结果；复核中时 status=running 且 outcome=pending；未复核为 null
  "origin": "auto_refresh",           // initial | auto_refresh | manual | null（旧判断）
  "replacing": { "verdict": "skip", "verdict_label": "不建议投", "judged_at": "…" } | null,   // 自动更新 / 重新判断进行中或失败时，被取代的旧结论（卡片淡色显示）
  "engine": "deepseek-flash:no-think",
  "facts": { ... },                   // v2 才有，结构见 data-model.md"事实结构"；v1 / 规则判定为 null
  "derivation": ["销售成分高 ↔ 偏好'不想对接客户' → 冲突", "..."],   // v2 才有
  "stale": { "job_changed": false, "profile_changed": true, "method_changed": false },
  "label": { "work_type": "市场与销售", "work_subtype": "市场营销与品牌", "secondary_work_types": [{"category": "运营", "subtype": "活动运营"}], "sales_level": "高", "experience_fit": null, "work_intensity": null, "overall": null, "note": null, "corrected": true } | null
}
```

插件显示规则（FR-025/026）：`verdict` 只在 `status = done` 时展示；四档颜色：适合投递绿、可以一试蓝、需要确认黄、不建议投红；旧判断 fit / unsure / unfit 分别按适合投递 / 需要确认 / 不建议投显示；其他状态各有固定文案，没有一种会显示成"不适合"。
`stale` 中 `method_changed` = 判断所用提示词版本低于当前版本（FR-034）。`label` 是当前用户对该判断所用岗位版本的标注；`corrected` = 标注中已填的某项与本判断不一致（FR-035）。

## PUT /v1/jobs/{platform_job_id}/label（US7）

请求（每项都可为 null，但至少一项非 null）：

```json
{ "work_type": "市场与销售", "work_subtype": "市场营销与品牌", "secondary_work_types": [{"category": "运营", "subtype": "活动运营"}], "sales_level": "高", "experience_fit": "差一点", "work_intensity": null, "overall": "skip", "note": "本质是商务" }
```

- 标注针对该岗位的**当前版本**；`judgement_id` 取该版本上当前用户的现行判断（没有则为 null）。
- 取值必须在 data-model.md `labels` 列出的选项内，`note` ≤ 100 字；`secondary_work_types` 为数组或 null，最多 3 个、不重复、不含主要类型；否则 422 `invalid_payload`；全为 null（空数组视同 null）→ 422。
- 岗位不存在 → 404 `not_found`；`list_only` → 409 `list_only`（没有职位描述，无法标注事实）。
- 响应：`{ "jobs": { "<id>": { "completeness": ..., "judgement": Judgement（含 label） } }, "labels": { "total": 13, "with_overall": 6 } }`。

## PUT /v1/jobs/{platform_job_id}/hr-note（FR-041）

请求 `{ "note": "HR 说每周只有半天对接银行，没有业绩指标" }`；`note` 为 null 或空白 → 删除。≤ 200 字，否则 422。岗位不存在 → 404。
响应 `{ "jobs": { "<id>": { "completeness": ..., "judgement": ..., "hr_note": "…" | null } } }`。
`POST /v1/observations`、`GET /v1/judgements`、judge、label 接口返回的每个岗位条目也带 `hr_note`。

## PUT /v1/jobs/{platform_job_id}/status（US8）

请求 `{ "status": "saved" | "applied" | "skipped" | null }`（null = 取消）。岗位不存在 → 404；值非法 → 422。
响应同 jobs 结构；各接口返回的岗位条目都带 `my_status: { "status": ..., "updated_at": ... } | null` 与 `company_name`。

## GET /v1/my-jobs?filter=all|saved|applied|skipped|recent&limit=200（US8）

```json
{
  "counts": { "all": 12, "saved": 5, "applied": 4, "skipped": 3, "recent": 40 },
  "items": [
    {
      "platform_job_id": "...", "title": "...", "company_name": null, "salary_raw": "...", "city": "...",
      "verdict": "apply|try|check|skip|null", "verdict_label": "适合投递", "stale": false,
      "hr_note_recorded": true, "my_status": "saved", "status_updated_at": "...", "last_seen_at": "...",
      "url": "https://www.zhipin.com/job_detail/<platform_job_id>.html"
    }
  ]
}
```

`all` 与各状态按 `status_updated_at` 倒序；`recent` = 当前用户有判断记录的岗位，按该用户最后一次查看时间倒序。

## 本机命令（不是 HTTP 接口）

- `jet eval run [--variants A,B,C,D] [--max-calls 300] [--threshold 0.6]`：对评测集跑各组合（FR-037/038），结果写入 `<数据目录>/evals/<运行 ID>.json`，终端打印汇总表。
- `jet eval report [<运行 ID>] [--threshold 0.5]`：重新汇总某次运行；对组合 D 可用新阈值离线重算（不再调用接口）。
- `jet eval export-jobs --out <文件>`：导出已入库的完整岗位（职位描述、岗位版本 ID）、HR 说的实际情况，以及画像中**仅限**以下字段：`background`、`work_preference`、`cities`、`min_monthly_k`、`exclude_keywords`（用户明确同意发给外部模型的范围）；不含方向、关键词、配对、令牌、Key；默认最多 100 条最近查看的岗位。
- `jet eval import-reference <文件> --source model:gemini-3.8-flash-high`：导入参考标注（逐条校验取值，非法条目跳过并报告），写入 `reference_labels`。
