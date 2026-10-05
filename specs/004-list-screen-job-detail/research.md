# Research: 列表页规则粗筛 + 独立职位页被动读取

**Feature**: `004-list-screen-job-detail` | **Date**: 2026-09-29

代码现状由 Antigravity（Gemini 3.8 Flash (High)）只读调研，关键结论由 Claude 对照原文核实（下文标"已核实"）。页面事实见 spec"已确认的页面事实"。

## 现状（已核实）

| 编号 | 事实 | 出处 |
|---|---|---|
| S1 | 列表读取只取职位名、公司名、公司行业、薪资、城市、区域和岗位 ID，**未读** `jobLabels`、`skills` | `extension/src/page-reader.js` 列表映射 |
| S2 | 列表发送按 `listSent` 去重，已发送的岗位不会再发；页面刷新（`reason: "load"`）才清空 | `background.js` `sendListObservations`、`dedupe.js` `newListItems`、`scheduler.js` `resetTabStateOnLoad` |
| S3 | `POST /v1/observations` 只在 `page_type == "detail"` 时读取画像，列表分支不读 | `src/jet/api/routes.py` observations（已核实） |
| S4 | 列表标记只显示判断完成（`status == "done"`）且有结论的岗位，其余岗位不显示任何标记 | `extension/src/page-summary.js` `buildListMarks`（已核实） |
| S5 | `rules.screen(job_version, profile, tags)` 已实现不去的城市、薪资底线、不接受条件三条规则，`tags` 参数已预留；列表岗位入库时 `job_versions` 已有 `salary_max_k`、`salary_visible`、`salary_parse_ok` | `src/jet/domain/rules.py`、`src/jet/domain/jobs.py` |
| S6 | 保存画像后插件不通知页面、不重读 | `background.js` `save_profile`（已核实） |
| S7 | 重点排查行业读取函数在 `src/jet/llm/prompt_v5.py`；domain 层直接导入它属于反向依赖 | `prompt_v5.py` `read_strict_industries` / `read_strict_industry_aliases` |
| S8 | 内容脚本覆盖 `https://www.zhipin.com/*`，含独立职位页；岗位卡片是固定定位浮层，在该页能显示 | `extension/manifest.json`（已核实）、`content.js` |
| S9 | `page-reader.js` 对 `/job_detail/` 直接返回空结果，`scheduler.js` `decideDetailAction` 对 `job_detail_page` 返回 `unsupported` | `page-reader.js`、`scheduler.js` |
| S10 | 入库时公司名"非空即覆盖" | `src/jet/domain/jobs.py` `ingest`（已核实） |
| S11 | 聊天页"当前岗位"命中缓存直接返回；已有 `syncChatJobStatusInTabStates` 可在状态变化时同步各标签页（设状态时已在用） | `background.js`、`chat-view.js`（已核实） |
| S12 | 插件测试只用 `node:test`，无 jsdom；聊天读取测试用手写 `MockElement` 模拟 DOM | `extension/tests/chat-reader.test.js` |

## 决定

### R1 粗筛在哪里算

- **Decision**：服务端在 `POST /v1/observations` 的列表分支读取当前画像，对每个岗位计算粗筛提示，放在响应每个岗位的新字段 `screen_hints` 里。
- **Rationale**：画像在本机 Jet（原则 III：插件不做判断、不保存画像以外的个人数据副本）；规则 2–4 复用 `rules.screen()`，保证与点开后的规则排除一致（FR-002、SC-004）；列表入库本来就会发这批岗位，不增加请求。
- **Alternatives**：插件端计算——需要把画像和行业规则下发到插件，违背原则 III，且规则重复实现。

### R2 标签字段只用于计算、不入库

- **Decision**：列表 payload 每个岗位增加 `job_labels`、`skills`（字符串数组），服务端只用来计算粗筛，不写入岗位表、不参与内容指纹。
- **Rationale**：满足 FR-001、FR-009；不改表结构、不触发"岗位已变"；标签只在列表出现，存下来也没有其他用途。
- **Alternatives**：存入 `job_versions`——需要迁移，且会改变内容指纹导致已有判断被标"可能过时"。

### R3 行业规则的读取与匹配移到 domain 层

- **Decision**：新建 `src/jet/domain/industry.py`，放规则文件路径、`read_strict_industries`、`read_strict_industry_aliases` 和匹配函数 `match_strict_industry(company_industry, industries, aliases)`（公司行业与行业名或同义名相同或包含 → 返回行业名）；`prompt_v5.py` 改为从这里导入，函数名保持不变。
- **Rationale**：粗筛（domain）与大模型提示词（llm）共用同一份规则（FR-003），依赖方向为 llm → domain；已有测试从 `prompt_v5` 导入这些函数，名字不变即可继续通过。规则文件位置不变。
- **Alternatives**：domain 直接导入 `prompt_v5`——反向依赖。

### R4 画像变化后如何重算

- **Decision**：插件保存画像成功后，后台清空所有 BOSS 标签页的 `listSent`，并对当前是搜索列表页的标签页各重读一次（读的是页面已加载的数据），列表岗位重新发送，服务端按新画像重算。
- **Rationale**：满足 FR-006；只读页面已有数据，不向 BOSS 发请求（原则 IV）；列表岗位重复入库只更新"最近读到时间"（002 T102），不产生新版本、不调大模型。
- **Alternatives**：等用户刷新页面——画像改完回到列表时仍显示旧提示，与 FR-006 不符。

### R5 列表标记怎么显示提示

- **Decision**：沿用现有列表标记（左侧色条 + 左上角小字标签，FR-054 的不遮挡规则不变）。没有完成判断的岗位若有粗筛提示，显示一种与四档结论都不同的中性色条，小字标签为"粗筛：提示1 · 提示2 …"；放不下时截断，鼠标悬停显示全部提示。已完成判断的岗位照旧只显示判断标记。
- **Rationale**：FR-004 只在卡片标记上显示；标签位只有一行（12–14px），多条提示需要截断，悬停补全文。
- **Alternatives**：多行标签——会遮挡职位名，违反 FR-054。
- **需要用户确认**：色条颜色与"截断 + 悬停看全文"的方式（见 plan"待用户拍板"）。

### R6 独立职位页读取

- **Decision**：`readBossPage` 增加 `job_detail_page` 分支，在页面主环境里按 spec 读取位置表读 DOM，返回与列表页详情相同结构的 `detail.job`（外加 `company_legal_name`）；岗位 ID 取地址。`decideDetailAction` 对独立职位页：读到必需字段 → `send`；读不到 → 沿用自动重读；重读用完仍失败 → 显示"此页暂不支持读取"（沿用 `unsupported_page` 文案，而不是"页面无法识别"，符合 FR-012）。
- **Rationale**：该页没有页面状态数据，只能读已显示的内容（原则 IV 允许）；复用现有 `handleSendDetail`、判断、轮询、卡片。
- **Alternatives**：单独写一套独立职位页流程——重复代码。

### R7 公司名不覆盖

- **Decision**：独立职位页的公司工商登记名放在 payload 新字段 `company_legal_name`（`company_name` 不传）；服务端入库时只在岗位没有公司名时写入。已有 `company_name` 的"非空即覆盖"规则不变。
- **Rationale**：满足 FR-014；不影响列表页和列表页详情区现有逻辑与测试。
- **Alternatives**：改 `company_name` 规则为"只在为空时写"——会让品牌名更正不再生效，改变现有行为。

### R8 独立职位页入库后同步聊天页

- **Decision**：独立职位页入库成功后，后台用 `syncChatJobStatusInTabStates` 更新各标签页里同一岗位的聊天状态缓存，并发出已有的 `chat_job_status_updated` 消息，侧边栏"当前岗位"区块随之更新。
- **Rationale**：满足 SC-005（回到聊天页即显示在库），复用现有机制。
- **Alternatives**：要求用户切换会话——SC-005 不满足。

### R9 测试方式

- **Decision**：Python 用现有 `data_dir` / `settings` fixture 测粗筛计算、行业匹配、`company_legal_name` 入库与 API 响应；插件用 `node:test` + 手写 `MockElement`（参照 `chat-reader.test.js`）测独立职位页 DOM 读取、`decideDetailAction`、`buildListMarks` 的提示标记；独立职位页 DOM 结构按 0.2 版探测的层级构造，不含任何真实页面文字。
- **Rationale**：沿用现有测试方式，不引入新依赖；符合原则 VIII（样本脱敏）。

### R10 独立职位页的公司行业

- **Decision**：第一版不读（FR-015）；判断时由 worker 从岗位库取 `company_industry`，没有则提示词写"未提供"，由大模型按 002 FR-057 推断。无需改代码。
