# 本机接口变更（004）

在 002 `specs/002-boss-readonly-extension/contracts/local-api.md` 的基础上只列出变化；未列出的接口与字段不变。

## POST /v1/observations

### 请求：`jobs[]` 新增可选字段

| 字段 | 适用 | 类型 | 说明 |
|---|---|---|---|
| `job_labels` | `page_type = "list"` | 字符串数组，≤ 20 项，每项 ≤ 30 字，可省略 | 列表 `jobLabels`；只用于粗筛，不入库 |
| `skills` | `page_type = "list"` | 同上 | 列表 `skills`；只用于粗筛，不入库 |
| `company_legal_name` | `page_type = "detail"`，来自独立职位页 | 字符串，≤ 100 字，可省略 | 工商登记名；只在岗位没有公司名时写入 `jobs.company_name`；同一条里不传 `company_name` |

超长时截断（与现有 `company_name` / `company_industry` 的处理一致）。

### 处理：新增一步

`page_type = "list"` 时，在入库之后读取当前用户画像与重点排查行业规则，为每个岗位计算粗筛提示（见 data-model"粗筛提示"）。不写入任何表、不调用大模型、不占额度。"高风险行业"只按用户在设置页勾选的从严行业计算（006 起，`strict_industry_selection`），一个都没勾就不计算；没有画像时只计算这一项（2026-10-06 修订）。

### 响应：`jobs[id]` 新增字段

```json
{
  "jobs": {
    "abc123~": {
      "completeness": "list_only",
      "judgement": null,
      "screen_hints": [
        { "type": "strict_industry", "text": "高风险行业（快消）" },
        { "type": "exclude_keyword", "text": "命中不接受条件：销售" }
      ]
    }
  }
}
```

- `screen_hints` 只在 `page_type = "list"` 的响应中出现；没有命中时为 `[]`。
- 已有判断的岗位也会返回 `screen_hints`，由插件决定不显示（FR-004）。

## 插件内部（chrome.runtime 消息）

- `save_profile` 成功后：后台清空各 BOSS 标签页的 `listSent`，并对当前为搜索列表页的标签页各重读一次（R4）。
- 独立职位页入库成功后：后台同步各标签页中同一岗位的聊天状态缓存，并发送已有的 `chat_job_status_updated`（R8）。

## readBossPage 返回结构（在 002 `contracts/page-reader.md` 第 2 节基础上）

- `page_kind = "job_detail_page"` 时不再直接返回空结果：读取成功时 `detail.ok = true`，`detail.job` 含 `platform_job_id`、`title`、`salary_raw`、`city`、`district`（固定为 `null`）、`description`、`company_legal_name`（可为 `null`）；读取失败时 `detail.ok = false` 并在 `problems` 中说明缺少的必需字段。
- 列表 `list.jobs[]` 每项新增 `job_labels`、`skills`（字符串数组，读不到为 `[]`）。
