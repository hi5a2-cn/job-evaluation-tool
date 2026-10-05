# Research: 判断时建议投哪份简历 (006)

> **2026-09-29 修订**：简历方向不再放在仓库配置文件 `resume_directions.json`，改为设置页"我的简历"（本机数据库 `resume_slots`，编号 1–3，只发编号与适合的岗位类型）；从严行业改为设置页勾选（`strict_industry_selection`，迁移 v7）。本文件中涉及 `resume_directions.json` 与方向代号（`ai_ops` 等）的设计已被 `tasks.md`"2026-09-29 修订"一节取代，以该节与 `spec.md` 为准。


**Feature**: `006-resume-suggestion` | **Date**: 2026-09-29

代码现状由 Antigravity 只读调研，对照既有代码（`src/jet/` 与 `extension/`）与既有 spec（002、003、004、005）逐项核实。

---

## 一、代码现状与事实（S1–S15）

| 编号 | 事实 | 出处（文件:行号） |
|---|---|---|
| S1 | `Settings.prompt_version` 默认值为 `"v5"`，可在 `.env` 中通过 `JET_PROMPT_VERSION` 覆盖 | `src/jet/config.py:25, 120` |
| S2 | `src/jet/llm/prompt.py` 仅为 v1 提示词实现（`PROMPT_VERSION = "v1"`），全仓库的大模型版本分派（`v1`–`v5`）与输出解析实际在 `src/jet/llm/client.py` 中分发 | `src/jet/llm/prompt.py:5`、`src/jet/llm/client.py:163-173, 234-295` |
| S3 | `prompt_v5.py` 的 `build_messages` 负责拼接画像、岗位信息及重点排查行业说明；`build_strict_industry_instruction` 为动态读取规则拼接文本 | `src/jet/llm/prompt_v5.py:27-94, 107-212` |
| S4 | 重点排查行业规则由 `src/jet/domain/industry.py` 从 `src/jet/llm/prompts/strict_industry_rules.json` 动态读取，文件缺失或格式错误时静默返回空结构，不报错 | `src/jet/domain/industry.py:10-33` |
| S5 | `is_method_changed` 当前将判断记录的 `prompt_version`（如 `v4`、`v5`）转换为整数，与当前配置版本（`current_prompt_version`）直接比较（`old_v < cur_v`）；若配置升为 `v6`，所有 `v5` 判断会被判定为 `method_changed=True` | `src/jet/domain/judgements.py:14-34` |
| S6 | `staleness` 汇集 `job_changed`、`profile_changed`、`method_changed`；若任一为 `True`，在用户打开详情时 `request_judgement` 会自动为非 `skipped/applied` 岗位触发 `auto_refresh` 重判（占额度） | `src/jet/domain/judgements.py:54-76, 125-202` |
| S7 | `to_api` 函数将数据库 `judgements` 行转换为前端 API 合约对象，目前已组装 `verdict_reason`、`hr_questions`、`review`、`facts`、`derivation` 等字段 | `src/jet/domain/judgements.py:382-500` |
| S8 | `JudgementWorker._process_judgement` 从队列中提取任务并调用 `run_llm_judgement`，将返回的 `verdict`、`facts`、`derivation`、`verdict_reason`、`hr_questions` 存入 `judgements` 表 | `src/jet/worker.py:285-325` |
| S9 | `prompt_v4.py` 的 `parse_facts` 校验 `verdict_reason`（非空字符串，截断至最多 40 字）及 `hr_questions`（最多 3 项，每项最多 60 字） | `src/jet/llm/prompt_v4.py:338-361` |
| S10 | `judgements` 表由 `src/jet/db/schema.sql` 定义；`src/jet/db/store.py` 的 `_ensure_current_schema` 使用 `PRAGMA table_info` 检查列并在缺失时执行 `ALTER TABLE ADD COLUMN`，数据库保持 `user_version 6` | `src/jet/db/schema.sql:72-100`、`src/jet/db/store.py:71-79, 176` |
| S11 | `src/jet/eval/runner.py` 中变体 B 和变体 C 直接使用 `settings.prompt_version` 执行评测，指标计算仅提取 `facts`（大类、销售、经验、强度），不受额外顶层字段影响 | `src/jet/eval/runner.py:522, 561` |
| S12 | 详情卡片在 `extension/src/content.js` 中渲染，由搜索列表右侧详情区与独立职位页（`/job_detail/`）共用，已支持结论徽标、一句话理由、投递状态按钮、HR 实际情况等展示 | `extension/src/content.js:2101-2550` |
| S13 | 侧边栏当前岗位卡片由 `extension/src/chat-view.js` 的 `formatChatJobStatus` 格式化数据，由 `extension/src/sidepanel.js` 的 `renderChatJob` 渲染，包含标题、结论、状态按钮、公司名、理由与 HR 实际情况 | `extension/src/chat-view.js:919-981`、`extension/src/sidepanel.js:242-450` |
| S14 | `extension/src/page-reader.js` 的 `readBossChatPage` 通过读取 Vue 消息列表组件提取消息数组，每条消息已包含 `body_type`（数字）与 `is_self`（布尔） | `extension/src/page-reader.js:638-670` |
| S15 | `src/jet/llm/sanitize.py` 将 `body_type == 7` 且 `is_self == False` 的消息识别为 HR 简历请求卡片（`is_request_card: True`）；`action_card_rules.json` 覆盖了动作类卡片 | `src/jet/llm/sanitize.py:209-228`、`src/jet/llm/prompts/action_card_rules.json:1-58` |

---

## 二、已定设计与代码对碰分析（D1–D8 专项排查）

### D1 简历方向配置与加载模块（`resume_directions.json` + `resume.py`）
- **事实对碰**：
  配置放在 `src/jet/llm/prompts/resume_directions.json`，结构与 spec.md 声明一致：
  `{"directions": [{"key": "...", "name": "...", "file_name": "...", "job_types": [...]}, ...]}`。
- **模块设计**：
  新建 `src/jet/domain/resume.py`，仿照 `src/jet/domain/industry.py` 设计读取函数：
  - `read_resume_directions(config_path=None) -> list[dict[str, Any]]`：
    - 读取 JSON 文件。若文件缺失、非合法 JSON、根节点不是 dict、`directions` 不是 list，记录一次警告日志（`logger.warning`）并返回空列表 `[]`。
    - 逐项校验列表元素：必须为包含 `key`（str）、`name`（str）、`file_name`（str）、`job_types`（list of str）的合法 dict；去除空白后留存有效项。
    - **方向数量硬约束**：若有效方向数小于 2（`len(directions) < 2`），视为不可构成选择，记录一次警告日志并返回空列表 `[]`（对齐 Edge Case 约定）。
  - `get_resume_direction_map(config_path=None) -> dict[str, dict[str, Any]]`：
    - 返回以 `key` 为键的字典映射，方便 O(1) 检索。
  - `resolve_resume_suggestion(key: str | None, reason: str | None, config_path=None) -> dict[str, str] | None`：
    - 供 API 装配使用。仅当 `key` 存在于当前配置中且 `reason` 非空时，返回 `{"direction": key, "name": item["name"], "file_name": item["file_name"], "reason": reason.strip()}`；否则返回 `None`。
- **冲突排查**：无冲突。代码模式与 `industry.py` 完全契合。

---

### D2 提示词 v6 与版本分派（`prompt_v6.py` + `client.py` + `config.py`）
- **事实对碰与澄清（重要证据）**：
  - 用户需求要求"prompt.py 的版本分派加入 v6"。
  - **代码事实**：经只读排查，`src/jet/llm/prompt.py` 是最初的 v1 单版本实现文件，并不包含版本分派代码；仓库中所有版本的分发逻辑（`build_messages` 调用与 `parse_facts` 调用）统一在 `src/jet/llm/client.py:163-173` 与 `234-295`。
  - **实现方案**：
    1. 新建 `src/jet/llm/prompt_v6.py`：定义 `PROMPT_VERSION = "v6"`；其 `build_messages` 内部调用 `prompt_v5.build_messages(...)` 获取消息数组；
    2. 当 `read_resume_directions()` 返回有效方向列表（数量 $\ge 2$）时，在 System Prompt 尾部追加第 14 条说明（"14. 简历方向建议"），详细列出每个方向的 `key`、`name`、`job_types`，并在输出 JSON 结构说明中追加 `"resume_suggestion": {"direction": "方向代号", "reason": "一句话理由"}`；
    3. 若配置为空或方向数 $<2$，`prompt_v6.build_messages` 生成的内容与 `prompt_v5` 完全一致，不追加说明段；
    4. 在 `src/jet/llm/client.py` 的 `run_llm_judgement` 中，增加 `if p_ver == "v6":` 分支，调用 `prompt_v6.build_messages` 和 `prompt_v6.parse_facts`；
    5. 为防止未来调用方误从 `prompt.py` 寻找分派入口，在 `src/jet/llm/prompt.py` 中也可增加显式版本常量或别名映射，但核心生效点在 `client.py`；
    6. 将 `src/jet/config.py:25` 与 `src/jet/config.py:120` 的默认 `prompt_version` 改为 `"v6"`。
- **隐私核实**：提示词追加内容严格只包含代号、方向名与岗位类型列表，绝对不出现任何文件名（如 `简历A.pdf`）或个人姓名。

---

### D3 存储模型扩展与 Worker 解析（`schema.sql` + `store.py` + `worker.py`）
- **事实对碰**：
  - `judgements` 表需要新增两列：`resume_direction TEXT` 与 `resume_reason TEXT`。
- **迁移机制**：
  - 在 `src/jet/db/schema.sql` 的 `judgements` 建表 DDL 中追加 `resume_direction TEXT, resume_reason TEXT`；
  - 在 `src/jet/db/store.py` 的 `_ensure_current_schema` 中追加：
    ```python
    if "resume_direction" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN resume_direction TEXT")
    if "resume_reason" not in cols:
        conn.execute("ALTER TABLE judgements ADD COLUMN resume_reason TEXT")
    ```
  - 维持 `user_version 6`，与现有 `verdict_reason`、`hr_questions`、`review` 等字段扩展方式完全一致，无需重写迁移脚本，对既有数据安全无侵入。
- **Worker 解析行为**：
  - `src/jet/worker.py` 的 `JudgementWorker._process_judgement`：
    - `run_llm_judgement` 针对 `v6` 增加解析结果 `outcome.resume_suggestion`（类型为 `dict` 或 `None`）；
    - 若 `resume_suggestion` 包含 `direction` 且在当前配置的有效 key 列表中，且 `reason` 为非空字符串：提取并截断（最多 40 字，与 `verdict_reason` 保持同级精炼度），写入数据库两列；
    - 若输出缺失、格式不符、方向代号不在配置中，两列写入 `NULL`；
    - 规则判断（`source = 'rule'`）两列直接存 `NULL`。
    - 同步更新复核链路（`review_enabled`）：复核判定时两列沿用初审结果或同样安全处理。

---

### D4 版本过时比较基准与防重判设计（`STALE_METHOD_BASELINE = "v5"`）
- **事实对碰（防大批重判的关键核心）**：
  - 现状：`judgements.py` 第 31 行 `return old_v < cur_v`。若将 `current_prompt_version` 提升至 `"v6"`，系统在检查已有 `v5` 判断时，`5 < 6` 为 `True`，即 `method_changed = True`。
  - 影响：一旦 `method_changed` 为 `True`，用户在浏览页面、打开详情卡片时，`request_judgement` 会自动判定判断过时并对未投递/未排除岗位调用大模型重新判断（`_exec_auto_llm`），这直接违反了宪法原则 VII（成本有上限）和 spec SC-001。
- **设计决定落地**：
  - 在 `src/jet/domain/judgements.py` 顶层增加常量：
    ```python
    STALE_METHOD_BASELINE = "v5"
    ```
  - 修改 `is_method_changed(judgement_row, current_prompt_version)` 中 `source == "llm"` 分支：
    ```python
    try:
        old_v = int(str(p_ver)[1:])
        baseline_v = int(str(STALE_METHOD_BASELINE)[1:])
        return old_v < baseline_v
    except ValueError:
        return False
    ```
  - **行为矩阵对照**：
    | 历史判断版本 | 修改前（比较基准为 v6） | 修改后（比较基准固定为基准 v5） | 是否判定过时 | 结论说明 |
    |---|---|---|---|---|
    | `v1`, `v2`, `v3`, `v4` | `old_v < 6` (True) | `old_v < 5` (True) | **是** | 行为不变，历史老旧版本依然被正确识别为过时 |
    | `v5` | `5 < 6` (**True，会误触发大批重判**) | `5 < 5` (**False**) | **否** | **成功规避自动重判，已有 v5 判断稳定不过时** |
    | `v6` | `6 < 6` (False) | `6 < 5` (False) | **否** | 新判断不过时 |
  - **绝对不自动重判任何岗位**：已有 v5 判断打开时静默保持原样；仅在用户手动点击卡片上的"重新判断"时，才调用 v6 生成带有简历建议的新判断。

---

### D5 API 规范岗位条目装配（`to_api`）
- **事实对碰**：
  - `src/jet/domain/judgements.py` 的 `to_api` 组装 judgement 字典，被 `GET /v1/judgements`、`POST /v1/observations`、`POST /v1/jobs/{id}/judge`、`POST /v1/chat/job` 等所有端点共用。
- **装配逻辑**：
  - 从 `judgement_row` 中读取 `resume_direction` 与 `resume_reason`；
  - 内部调用 `resolve_resume_suggestion(direction, reason)`：
    - 若 `direction` 与 `reason` 均有效，且该代号依然存在于当前配置文件的 `directions` 中，组装字典：
      ```json
      {
        "direction": "ai_ops",
        "name": "AI / 运营",
        "file_name": "简历A.pdf",
        "reason": "岗位为AI提示词工程与用户运营混合职责，契合该方向"
      }
      ```
    - 若 `resume_direction` 为空、或用户事后在配置文件中删除了该代号，回退返回 `None`（JSON 输出 `null`）；
  - `to_api` 返回字典增加键：`"resume_suggestion": resume_suggestion`。

---

### D6 插件端显示（详情卡片与侧边栏）
- **详情卡片（`extension/src/content.js`）**：
  - 搜索列表右侧详情区与独立职位页共用 `renderDetailCard` / `buildCardDom`；
  - 在"结论摘要行"之后、"状态按钮行"之前（或紧随结论摘要区）渲染简历建议提示行：
    - **屏蔽规则**：若结论为 `skip`（不建议投），或者 `resume_suggestion` 为 `null`，**绝对不显示**该行；
    - **展示样式**：展示文本"建议投：<file_name>"；为该元素添加 `title` 属性（鼠标悬停展示 reason）；也可点击切换展开理由。
- **侧边栏当前岗位（`extension/src/chat-view.js` + `extension/src/sidepanel.js`）**：
  - `formatChatJobStatus(jobId, jobEntry)`：从 `jobEntry.judgement` 提取 `resume_suggestion`，挂载到返回对象上；
  - `renderChatJob(status)`：在公司名/理由下方渲染简历建议，规则同上：
    - 结论为 `skip` 或无建议时不显示；
    - 有建议时显示"建议投：<file_name>"，悬停或点击展开看理由。

---

### D7 聊天页 HR 简历请求识别与侧边栏突出展示
- **识别机制溯源与排查（优先复用已有数据）**：
  - 插件现有 `extension/src/page-reader.js` 的 `readBossChatPage()` 已经完整读取了所有可见聊天消息（`messages`），且每条消息均包含 `body_type` 与 `is_self`；
  - 服务端 `sanitize.py:227` 已明确硬编码规则：`is_request_card = (body_type == 7)`，当且仅当发送方为 HR 时表示简历索要卡片；
  - **纯函数判定**：在 `extension/src/chat-view.js` 中新增纯函数：
    ```javascript
    export function hasHrResumeRequest(messages) {
      if (!Array.isArray(messages) || messages.length === 0) return false;
      return messages.some(m => (m.body_type === 7 || m.bodyType === 7) && !m.is_self && !m.isSelf && String(m.text || "").includes("简历"));
      // Claude 审核补充：bodyType 7 是 HR 通用请求卡片（也用于交换微信/电话），须按文字含"简历"区分（003 FACT："我想要一份您的附件简历"）
    }
    ```
- **读取时机与轻量性**：
  - 侧边栏打开时，每当发生聊天切换（`chat_switched`）或新消息到达（`chat_messages_changed`），或者侧边栏初次打开时：
    - 异步调用一次 `read_chat_page`（仅在页面内部执行 `readBossChatPage` 读取已加载的 Vue/DOM 数据，耗时 < 2ms，不发任何网络请求）；
    - 得到 `messages` 后调用 `hasHrResumeRequest(messages)`，得到当前会话是否处于 HR 索要简历状态。
- **侧边栏突出渲染规则**：
  - **分支 1（HR 要简历 + 岗位有简历建议 + 结论非 skip）**：
    在侧边栏当前岗位卡片顶部以醒目高亮样式（如橙色警示背景卡片）展示：
    **"HR 在要简历，建议投：<file_name>"**，并附带展示一句话理由；
  - **分支 2（HR 要简历 + 岗位已有判断但无建议，如 v5 老判断）**：
    在侧边栏显示提示文案："点重新判断可获得简历建议"，用户可直接点击重新判断按钮获取建议；
  - **分支 3（HR 要简历 + 岗位未判断）**：
    按现有规则展示"点「查看职位」获取详情并判断"，不突出展示简历建议；
  - **分支 4（HR 未要简历）**：
    不展示醒目高亮条，按普通卡片样式展示"建议投：<file_name>"。
  - **切换会话隔离**：
    每当会话切换（`handleChatSwitched`），立即将卡片高亮状态复位为 `false`；重新读取新会话后决定是否高亮，绝不残留上一会话的提示。

---

### D8 外发大模型内容全量差异比对（D8 专项审计）
对比 `prompt_v5.py` 与 `prompt_v6.py` 发给外部大模型（DeepSeek）的完整 payload：

| 部件 | prompt_v5 内容 | prompt_v6 变动 | 敏感数据排查（姓名/文件名） |
|---|---|---|---|
| **System Prompt** | V4原则 + 城市薪资说明 + 重点排查行业说明（13条） | 仅在尾部追加第 14 条："14. 简历方向建议"说明段，以及在输出 JSON 格式模板中新增 `"resume_suggestion"` 属性 | ✅ **零泄漏**：仅包含配置中的 `key`、`name`、`job_types`，不含任何 `file_name`，不含个人姓名 |
| **User Prompt** | 候选人画像（方向、关键词、城市偏好、薪资等） + 岗位详情（职位名、公司、行业、薪资、城市、JD） | **完全相同**，不新增任何字符 | ✅ **零泄漏** |
| **调用次数** | 单次判断 1 次 | **完全相同**，同一次调用返回，0 新增调用 | ✅ 符合宪法成本上限 |

---

## 三、代码冲突与风险点排查总结

经只读排查全库，发现以下 2 处潜在实现细节差异与化解策略：
1. **冲突/差异点 1（D2 关联，文件分发位置）**：
   - 用户指令提及"prompt.py 的版本分派加入 v6"。
   - **事实证据**：`src/jet/llm/prompt.py` 并未包含 `v2`–`v5` 的分派逻辑，实际分派位于 `src/jet/llm/client.py`（行 163–173 与 234–295）。
   - **对策**：在 `client.py` 中规范接入 `prompt_v6` 的 `build_messages` 与 `parse_facts` 分派分支；在 `prompt.py` 中亦可做版本常量注释或兼容导出。
2. **冲突/差异点 2（D4 关联，自动重判陷阱）**：
   - 现有的 `request_judgement`（`judgements.py:125-202`）在打开详情时会自动调用 `staleness()`。若按老逻辑直接比较配置版本，升级默认版本后所有打开的旧岗位都会被后台任务静默重判。
   - **对策**：必须严格执行 D4，引入 `STALE_METHOD_BASELINE = "v5"`，并将 `old_v < cur_v` 改为 `old_v < baseline_v`，从根源切断非预期的大批自动重判。
