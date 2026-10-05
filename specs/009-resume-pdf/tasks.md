# Tasks: 我的简历：上传 PDF + AI 简历画像 (009-resume-pdf)

**Input**: `/specs/009-resume-pdf/`（`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/local-api.md`、`contracts/plugin-protocol.md`、`quickstart.md`）

**Tests**: 自动化测试严格遵循宪法原则 VIII（不联网、不需真实 Key、不写真实数据目录、使用保存的样本与 MockTransport/假 jetClient）。生产代码严禁包含针对测试的特殊判断；不删除、不放宽已有测试断言。仓库中绝对不写入真实个人信息，测试夹具只使用虚构人物（如"李明"、`13800000000`、`liming@example.com`）。

**Organization**: 阶段 1 数据库迁移 v9 与独立配额模型 → 阶段 2 敏感信息脱敏扩展与 PDF 文本提取 → 阶段 3 简历画像提示词工程与服务端 API → 阶段 4 岗位判断集成与旧数据兼容 → 阶段 5 插件设置页交互与说明文字更新 → 阶段 6 全链路回归与端到端验证。每阶段末尾：全量测试、Codex 审核、提交；改服务端的阶段提交后 `launchctl kickstart -k` 重启。

`〔执行：Antigravity〕` 编写代码与测试（Gemini 3.8 Flash (High)，不执行任何 git 命令，不运行任何终端命令）；`〔执行：Claude Code〕` 审核、Codex、提交、重启与真实页面验收；`〔执行：用户〕` 真实页面操作验证。本分支不合并、不推送。

**共同硬性要求**：
- 严格遵循 Constitution 5.0.0 核心原则；
- PDF 原文件仅在内存处理，绝对不写入磁盘、不写入数据库、不写入日志；
- 三来源识别姓名，若均无姓名坚决阻断调用大模型（422 `self_name_required`）；
- 简历画像生成设有独立每日上限（`daily_resume_profile_limit`），不占岗位判断每日额度；
- 岗位判断时只发送画像（编号为简历1/2/3），绝不发简历名称与简历原全文；
- 仓库内任何文件均不得写入用户真实姓名、简历文件名、个人隐私信息。

---

## 阶段 1: 数据库迁移 v9 与独立配额模型

- [x] T001 数据库迁移 v9（research S1, data-model 2）：在 `src/jet/db/migrations.py` 实现 `migrate_to_v9`（WAL checkpoint、备份；事务中重建 `resume_slots` 表加 `resume_text` 字段、`profile` 约束 ≤300 字；旧行 `job_types` 迁移为 `profile`、`resume_text` 设为 NULL；`user_settings` 新增 `daily_resume_profile_limit` 默认 10、0..50；重建 `llm_calls` 表增加 `'resume_profile'` 枚举并完整保留历史数据；行数与外键检查；更新 `LATEST_VERSION = 9`）；同步更新 `src/jet/db/schema.sql` 与 `src/jet/db/store.py`（`_ensure_current_schema` 与 `init_db`）〔执行：Antigravity〕
- [x] T002 独立每日配额（research S1, data-model 2.2）：在 `src/jet/llm/quota.py` 中扩展 `usage_today`、`remaining_today` 与 `reserve` 支持 `purpose='resume_profile'`；读取 `daily_resume_profile_limit`；额度用完（`used >= limit` 或 `limit <= 0`）返回 None；与判断、话术、预判额度完全隔离〔执行：Antigravity〕
- [x] T003 [P] 阶段 1 单元测试：在 `tests/unit/test_migrations.py` 增加 v8 到 v9 迁移测试（含表重建、旧数据 `job_types` 映射为 `profile`、默认值、llm_calls 历史数据完整保留、事务失败回滚）；在 `tests/unit/test_resume_quota.py` 测试画像配额独立计数与耗尽拦截〔执行：Antigravity〕
- [x] T004 检查点：全量测试（`uv run --frozen pytest`）；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 2: 敏感信息脱敏扩展与 PDF 文本提取

- [x] T005 身份证号码正则脱敏扩展（research S3）：在 `src/jet/llm/sanitize.py` 增加 18 位中国居民身份证正则表达式 `ID_CARD_PATTERN`；在 `sanitize_text` 中增加身份证掩码过滤（替换为 `[已隐藏]`）；在 `tests/unit/test_sanitize.py` 增加身份证脱敏测试用例〔执行：Antigravity〕
- [x] T006 内存 PDF 文本提取与扫描件校验（research S2）：在 `src/jet/domain/resume.py` 中利用 `pypdf`（6.19.0）实现内存提取函数 `extract_text_from_pdf_bytes(pdf_bytes)`；Base64 解码校验（≤5MB，超限报 `FileTooLargeError`）；提取全部页面文本；去除空白少于 50 字符抛出 `NoTextError`；加密/损坏抛出 `PdfInvalidError`；确保零磁盘写入〔执行：Antigravity〕
- [x] T007 姓名三来源融合与脱敏调度（research S3）：在 `src/jet/domain/resume.py` 中实现姓名三来源识别逻辑（`users.display_name`、上传可选 `self_name`、PDF 规则提取：首行 2–4 汉字/大写英文词、`姓名[:：]` 正则）；去重合并后构造敏感词并调用 `sanitize_text`；三来源均无姓名时抛出 `SelfNameRequiredError`；返回脱敏后的简历正文〔执行：Antigravity〕
- [x] T008 [P] 阶段 2 单元测试：在 `tests/unit/test_resume_pdf.py` 测试纯代码生成虚构文字 PDF 提取、扫描件/空文本拦截（`no_text`）、损坏/加密拦截、三来源姓名识别与缺失阻断（`self_name_required`）、零落盘安全断言（无 `.pdf` 文件、无数据库 BLOB、无敏感明文）〔执行：Antigravity〕
- [x] T009 检查点：全量测试；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 3: 简历画像提示词工程与服务端 API

- [x] T010 简历画像提示词构建与校验（research S4, contracts/local-api.md）：新建 `src/jet/llm/resume_profile.py`；编写系统提示词要求根据脱敏文本提炼适合方向与 3～5 条亮点；调用非思考引擎（`settings.judge_engine`）；解析与校验输出，强制截断至 ≤300 字；日志中严禁记录提示词与输出全文〔执行：Antigravity〕
- [x] T011 简历上传与重新生成路由（contracts/local-api.md §1, §2）：在 `src/jet/api/routes.py` 实现 `POST /v1/resumes/upload` 与 `POST /v1/resumes/{slot}/regenerate`（配对鉴权；5MB 校验；调用 domain 提取与脱敏；预占 `resume_profile` 额度；额度耗尽/无 Key 时降级保存 `name` 与 `resume_text` 并返回 429/409；正常生成后更新 `resume_slots` 并结算 `llm_calls`）〔执行：Antigravity〕
- [x] T012 简历查询、修改与删除路由（contracts/local-api.md §3, §4, §5）：在 `src/jet/api/routes.py` 与 `src/jet/domain/resume.py` 更新 `GET /v1/resumes`（返回列表，不含全文）、`PUT /v1/resumes`（更新名称与画像，≤300字校验，不改 `resume_text`）、`DELETE /v1/resumes/{slot}`（彻底物理删除该行）〔执行：Antigravity〕
- [x] T013 [P] 阶段 3 路由测试：在 `tests/api/test_resume_routes.py` 测试上传文字版 PDF 成功、扫描件拦截、无姓名拦截、配额耗尽/无 Key 降级保存、重新生成、读取不含全文、修改校验、删除清空〔执行：Antigravity〕
- [x] T014 检查点：全量测试；审核 diff；Codex 审计；提交；`launchctl kickstart -k` 重启服务〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 4: 岗位判断集成与旧数据兼容

- [x] T015 岗位判断提示词升级（research S5）：在 `src/jet/llm/prompt_v6.py` 中将 `build_resume_instruction` 的段落格式从 `适合岗位类型：{jt}` 替换为 `{profile}`；维持 `PROMPT_VERSION = "v6"` 不变；保持 ≥2 份有效简历才发送规则；绝不发送简历名称与全文〔执行：Antigravity〕
- [x] T016 岗位判断读取字段适配（research S5）：在 `src/jet/domain/resume.py` 的 `list_resumes` 与 `src/jet/llm/client.py` 中，将简历查询字段从 `job_types` 调整为 `profile`；适配历史迁移后的画像消费〔执行：Antigravity〕
- [x] T017 [P] 阶段 4 测试：在 `tests/unit/test_prompt_v6.py` 测试画像提示词构建与白名单断言（只含编号与画像，不含名称与全文）；在 `tests/unit/test_llm_client.py` 测试判断流程正确注入简历画像〔执行：Antigravity〕
- [x] T018 检查点：全量测试；审核 diff；Codex 审计；提交；`launchctl kickstart -k` 重启服务〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 5: 插件设置页交互与说明文字更新

- [x] T019 设置页 UI 改造与双处说明替换（contracts/plugin-protocol.md §1）：在 `extension/src/options.html` 中原样更新 `#resume-section` 与 `#strict-industry-section` 的说明文字；在简历卡片顶部增加脱敏姓名输入框；改造每份简历项 DOM（文件选择、名称联动、画像文本框、300 字计数、删除与重新生成）〔执行：Antigravity〕
- [x] T020 插件网络与后台中继扩展（contracts/plugin-protocol.md §2, §3）：在 `extension/src/jet-client.js` 中新增 `uploadResume`、`regenerateResume`、`deleteResume` 方法；在 `extension/src/background.js` 中增加对应 runtime 消息转发处理〔执行：Antigravity〕
- [x] T021 选项页上传与交互逻辑实现（contracts/plugin-protocol.md §4）：在 `extension/src/options.js` 中实现选文件自动填名称、5MB 前端校验、FileReader 转 Base64、上传加载状态与错误展示、画像实时字数统计（`x / 300`）与超限警示、保存修改与删除逻辑〔执行：Antigravity〕
- [x] T022 [P] 阶段 5 插件测试：在 `extension/tests/options-resume.test.js` 编写单元测试，覆盖文件名截取、5MB 拦截、字数统计计算、双处说明文案匹配断言；运行 `node --check extension/src/*.js` 语法检查〔执行：Antigravity〕
- [x] T023 检查点：全量测试；审核 diff；Codex 审计；提交〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 阶段 6: 全链路回归与端到端验证

- [x] T024 全量自动化回归：执行 `uv run --frozen pytest` 与 `node --test extension/tests/*.test.js`，保证所有测试 100% 通过〔执行：Antigravity〕
- [ ] T025 真实页面端到端验证：按 `quickstart.md` 在真实 Chrome 与 BOSS 详情页上完整走通 6 个场景（文字版上传、扫描件拦截、无姓名阻断、手动微调保存、详情页简历推荐、删除清空）〔执行：用户〕 **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）
- [x] T026 最终检查点：审核全部 diff；Codex 完整审计；提交（本分支不合并、不推送）〔执行：Claude Code〕（2026-10-06 补勾：009 已于 15a4052 合入 main；当时是否逐项执行以提交记录为准）

---

## 审查修正（2026-09-30）

- [x] 1. 姓名必须来自"确认过的来源"才允许外发（隐私底线）：
  - `src/jet/domain/resume.py` `sanitize_resume_text`：确认来源 = `self_name` 或 `users.display_name`（非空且不是 "me"）。两者都没有 → 抛 `SelfNameRequiredError`，不调用大模型。PDF 里识别出的姓名只作为额外脱敏词（补充），不能单独让检查通过。
  - 设置页（`extension/src/options.html` / `options.js`）：上传区的"你的姓名（只在本机用于去掉姓名，不发送）"改为上传前必填（为空时不发请求并提示）；只随上传请求发给本机 Jet。（2026-10-05 体检第 20 条修订：原先填过后记在 `chrome.storage.local` 的 `resumeSelfName` 键；现改为插件和本机 Jet 都不保存姓名，上传和重新生成画像前都要填写；设置页打开时删除旧键。）
- [x] 2. PDF 提取文字的姓名匹配要能覆盖 pypdf 常见排版：
  - 脱敏时每个姓名词允许字符之间有任意空白（例如 "李明" 也要匹配 "李 明"、"李\n明"；英文名单词间任意空白）。只在简历脱敏里这样做（`sanitize_resume_content`），不改变聊天脱敏 `sanitize_text` 的现有行为。
  - PDF 姓名识别补充：`姓\s*名\s*[:：]\s*` 后的 2–4 个汉字（中间可有空白）；首个非空行去掉字符间空白后，取第一个分隔符（空白、|、｜、/、·、,、，）前的片段，若为 2–4 个汉字才作为候选；排除常见标题词（个人简历、简历、求职简历、个人信息、基本信息、求职意向、联系方式、Resume、CV、Curriculum Vitae、Personal Resume 等）。
  - 测试：虚构简历文字样例覆盖"李 明"空格拆分、"李明 | 138 0000 0000 | liming@example.com"同一行、"姓 名：李明"、首行为"个人简历"；断言发给大模型的请求体里不含 李明 / 李 明 / 手机号 / 邮箱。
- [x] 3. 迁移 v9：`resume_slots.profile` 不在数据库层加长度 CHECK（长度 ≤300 只由应用层校验），保证旧 `job_types` 超过 300 字也能完整迁移；补一个超长旧值迁移测试，断言完整保留。
- [x] 4. `src/jet/llm/resume_profile.py` 提示词通用化：适用各行业（如"资深求职顾问"；亮点示例：核心能力、代表性成果与量化结果、行业/领域经验、工具或方法）。发送给大模型的脱敏正文最多 8000 字（超出截断），以控制费用；测试断言。
- [x] 5. 删除 `resume.py` 里重复定义的 `get_resume_slot`（保留一个，调用方都能用）。
- [x] 6. `prompt_v6.build_resume_instruction`：跳过画像为空的简历；非空画像不足 2 份时不发送简历段落。测试。
- [x] 7. PDF 提取文本 Unicode NFKC 规范化：
  - `src/jet/domain/resume.py` 的 `extract_text_from_pdf_bytes`：拼接全文后先通过 `normalize_extracted_text` 做 `unicodedata.normalize("NFKC", ...)`，再做有效字数校验并返回，解决 pypdf 从部分 PDF 提取中文时得到康熙部首兼容字符（例如 "⼈" U+2F08 代替 "人"、"⽤" U+2F64 代替 "用"）导致姓名脱敏匹配不上的问题。
  - 测试（`tests/unit/test_resume_pdf.py`）：对含 "⼈" "⽤" 字符串验证规范化后为 "人" "用"；验证当 `self_name="王大人"` 而文字里是 "王⼤⼈"（U+2F24 ⼤, U+2F08 ⼈）时，规范化+脱敏后不再含该姓名。
- [x] 8. `src/jet/api/routes.py` POST `/v1/resumes/upload` Base64 长度前置检查与合法性校验：
  - 解码前先检查 `pdf_base64` 长度（超过 5MB 对应的 Base64 长度，即 `ceil(5*1024*1024/3)*4`，直接返回 `file_too_large`，不解码）；
  - 解码用 `base64.b64decode(..., validate=True)`，非法字符返回 `pdf_invalid`；测试覆盖。
- [x] 9. `extension/src/options.js` 删除简历确认与结果处理：
  - 点删除先弹 `confirm("确定删除简历 N？提取的文字和画像会一起删除，不能恢复。")`；
  - 确认后发送 `delete_resume` 并等待结果；
  - 成功才移除这一项并提示"简历 N 已删除"；
  - 失败保留这一项并显示错误（沿用 `formatResumeErrorMessage`）；
  - 取消则什么都不做；
  - 背景页 `delete_resume` 处理把 `jetClient` 的结果通过 `sendResponse` 返回；测试覆盖确认/取消/成功/失败。
- [x] 10. DeepSeek `response_format={"type": "json_object"}` 限制适配与简历画像结构化：
  - 原因：`call_once` 总是带 `response_format={"type": "json_object"}`，而 DeepSeek 限制提示词中必须包含 "json" 字样（HTTP 400: `Prompt must contain the word 'json' in some form to use 'response_format' of type 'json_object'.`）。
  - `src/jet/llm/resume_profile.py`：提示词改为要求大模型只输出 JSON（`{"direction": "适合的岗位方向，一句话", "highlights": ["亮点1", "亮点2", "亮点3"]}`，3～5 条），明确包含 "JSON" 字样与格式规范；解析支持去掉 ``` 包裹；校验 direction 非空、highlights 为 3～5 条非空字符串（多于 5 条取前 5 条，少于 3 条或解析失败抛 ParseError）；在服务端拼成画像文本：适合方向 + 亮点（序号 1/2/3），总长超过 300 字时从后往前删亮点（至少保留 3 条），仍超则截断至 300 字。不改 `call_once`。
  - `tests/conftest.py`：`FakeLlmHelper` 的 handler 模拟 DeepSeek 限制（请求体带 `response_format.type == "json_object"` 而所有 messages 内容都不含 "json"（不区分大小写）时，返回 HTTP 400 和同样错误信息），并支持画像生成分支。
  - 测试：画像相关测试更新为 JSON 响应，补齐解析成功拼接格式、少于 3 条报错、多于 5 条截取、超长删减、代码块包裹及发出的请求里含 "json" 断言。
- [x] 11. PDF 提取文本 CJK 部首补充区（U+2E80–U+2EFF）映射为常用汉字：
  - 原因：Unicode NFKC 规范化不处理"CJK 部首补充"区（U+2E80–U+2EFF），pypdf 可能把"马、黄、龙、齐、韦"等常用字（也是常见姓氏）提取成这些部首字符，导致姓名脱敏匹配失败。
  - `src/jet/domain/resume.py`：在 NFKC 之后用 `str.translate` 结合显式常量表 `CJK_RADICAL_MAP` 将 22 个部首字符（⻅→见 ⻆→角 ⻉→贝 ⻋→车 ⻓→长 ⻔→门 ⻙→韦 ⻚→页 ⻛→风 ⻜→飞 ⻠→食 ⻢→马 ⻥→鱼 ⻦→鸟 ⻧→卤 ⻨→麦 ⻩→黄 ⻪→黾 ⻬→齐 ⻮→齿 ⻰→龙 ⻳→龟，码位 U+2EC5..U+2EF3）映射为对应常用汉字，并附中文注释说明原因。
  - 测试（`tests/unit/test_resume_pdf.py`）：使用转义写法对 22 个映射逐一断言；断言 `self_name="黄丽"` 而文字里是 "⻩丽"（`\u2ee9丽`）、`self_name="马明"` 而文字里是 "⻢ 明"（`\u2ee2 明`）时规范化+脱敏后不再出现该姓名；断言 "增⻓"（`增\u2ed3`）规范化为 "增长"。
