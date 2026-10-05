# 契约：插件页面读取与插件内部消息

## 1. 读取方式（原则 IV、FR-001/008）

- BOSS 的岗位数据在页面 Vue 实例上（`el.__vue__.jobList` / `jobDetail.jobInfo`），只有页面自己的 JS 环境（MAIN world）看得到。
- 读取由后台 service worker 调用 `chrome.scripting.executeScript({ world: "MAIN", func: readBossPage })` 完成：
  - `readBossPage` 只读属性、复制成普通对象返回；不写任何属性、不留全局变量、不注册事件、不发请求。
  - 结果直接返回给插件，**不经过 `window.postMessage` / DOM 事件**（这些页面也能看到）。
  - 这是实验插件 v0.0.2 已在真实页面上验证过的方式（FACT，1 次记录）。
- 何时读：内容脚本（ISOLATED world，只看 DOM）用 `MutationObserver` 观察列表容器和详情面板的**子节点变化**，防抖 400 ms 后通知 service worker 读一次；页面加载完成时也读一次。MutationObserver 只观察、不改 DOM。
- 是否"变了"只看读出的岗位 ID 集合 / 详情岗位 ID，不看网址（FR-003）。

> 备选（未采用）：MAIN world 常驻脚本 + 定时器 —— 会在页面环境里留下常驻代码，被页面察觉的面更大。

## 2. readBossPage 返回结构

```json
{
  "reader_version": 1,
  "page_kind": "search_list",          // search_list | job_detail_page | captcha_or_blank | other
  "list": {
    "ok": true,
    "has_more": true,
    "jobs": [ { "platform_job_id": "...", "title": "...", "salary_raw": "...|null", "city": "...", "district": "...|null" } ]
  },
  "detail": {
    "ok": true,
    "job": { "platform_job_id": "...", "title": "...", "salary_raw": "...|null", "city": "...", "district": null, "description": "..." }
  },
  "problems": ["jobList 不是数组"]      // 读不出时的具体原因，给"页面无法识别"用
}
```

字段映射（来自实验插件 FACT）：

| 返回字段 | 列表来源 | 详情来源 |
|---|---|---|
| platform_job_id | `encryptJobId` | `jobInfo.encryptId` |
| title | `jobName` | `jobInfo.jobName` |
| salary_raw | `salaryDesc`（空 → null） | `jobInfo.salaryDesc`（空 → null） |
| city | `cityName` | `jobInfo.locationName` |
| district | `areaDistrict`（空 → null） | — |
| description | — | `jobInfo.postDescription`（原文，不压缩空白） |
| company_name | `brandName`（US8） | 列表中同一岗位 ID 那条的 `brandName`，回退 `jobDetail.brandComInfo.brandName`（2026-09-25 FACT：详情里能读到公司名；是哪条路径生效未区分） |

判定：
- `page_kind = job_detail_page`（路径 `/job_detail/`）→ 最初（002）插件显示"此页暂不支持读取"、不发给 Jet（FR-005）；**004 起改为读取独立职位页并按详情页发给 Jet**，见下方 004 变更说明。
- `page_kind = search_list` 但找不到 Vue 实例、`jobList` 不是数组、或岗位缺 `platform_job_id`/`title` → `ok = false`，插件显示"页面无法识别"，不发给 Jet，不退回读页面文字（FR-006）。
- `page_kind = captcha_or_blank`（路径含验证 / 安全检查页，或页面主体为空）→ 插件什么都不显示、什么都不做（Edge Case）。
- 岗位数量上限：单次最多 200 条，超出截断并在 `problems` 中说明。

> **004 变更说明**：指向 [specs/004-list-screen-job-detail/contracts/local-api.md](../../004-list-screen-job-detail/contracts/local-api.md)：列表 `jobs[]` 新增 `job_labels`、`skills`；`page_kind` 为 `job_detail_page` 时读取独立职位页并返回 `detail.job`（含 `company_legal_name`）。

## 3. 插件内部消息（chrome.runtime 消息）

| 方向 | type | 内容 |
|---|---|---|
| 内容脚本 → SW | `page_changed` | `{}`（只是通知，读取由 SW 做） |
| SW → 内容脚本 | `render_state` | `{ detail: {platform_job_id, view_state}, list_marks: {id: view_state}, marks_enabled }` |
| 侧边栏 → SW | `get_page_summary` | `{ tabId }` → 本页已判断岗位列表 |
| 设置页 → SW | `pair` / `save_profile` / `get_status` | 转发到 Jet 接口 |
| 内容脚本 → SW | `save_label` | `{platform_job_id, label}` → `PUT /v1/jobs/{id}/label`（US7） |

`view_state` 是插件界面状态，由 SW 从 Jet 响应或连接错误映射而来：

`jet_down` · `unpaired` · `no_profile` · `list_only` · `queued`/`running`（判断中）· `done_apply`（适合投递，绿）· `done_try`（可以一试，蓝）· `done_check`（需要确认，黄）· `done_skip`（不建议投，红）；旧判断 `fit` / `unsure` / `unfit` 分别映射到 `done_apply` / `done_check` / `done_skip` · `stale` · `failed` · `quota_exhausted` · `interrupted` · `unrecognized` · `unsupported_page`

（US7）`render_state.detail` 另带 `facts`、`derivation`、`verdict_reason`、`user_label`（用户标注；`label` 仍是状态文字）、`prompt_version`，供卡片显示事实、推导和标注区；标注经内容脚本 → SW 消息 `save_label` `{platform_job_id, label}` 转发到 `PUT /v1/jobs/{id}/label`。

## 4. 页面标记（FR-027/028）

- 开关存 `chrome.storage.local.marksEnabled`，默认 `true`。
- 打开：内容脚本只在**自己创建的 Shadow DOM 宿主元素**里显示标记和详情旁的结论卡片，宿主元素带 `data-jet` 属性；不修改 BOSS 原有元素的内容、样式和事件。
- 列表标记（US4，2026-09-25）：用**一个覆盖层**宿主 `data-jet="list-marks"`（fixed、`pointer-events: none`），按卡片的 `getBoundingClientRect()` 把徽标叠在卡片右上角，滚动 / 缩放时重算；**不向 BOSS 的卡片内部或旁边插入任何元素**。卡片与岗位的对应：只读查询 `a[href*="/job_detail/"]`，从链接取岗位 ID，卡片取最近的 `li`（NOT VERIFIED；取不到则不显示列表标记，侧边栏照常）。
- 关闭：立即移除所有 `[data-jet]` 元素，之后不再向页面添加任何东西；结论只在侧边栏（`chrome.sidePanel`）显示。
- 测试断言：关闭后页面中 `[data-jet]` 数量为 0（SC-006）。

## 5. 去重（FR-004）

SW 按标签页保存 `lastSent = { list: Set<id+contentKey>, detail: id+contentKey }`：
- 列表：只把"本标签页还没发过"的岗位发给 Jet（翻页 / 加载更多只发新 ID，US5-3）。
- 详情：详情岗位 ID 或内容摘要与上次不同才发。
- 刷新页面会清空 `lastSent`，再发一次也没关系：Jet 端合并入库，且"已有现行判断就不再判断"（FR-021），所以不会重复调用大模型。
