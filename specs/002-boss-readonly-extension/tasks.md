# Tasks: BOSS 岗位只读插件 + 本机判断（第一阶段）

**Input**: Design documents from `/specs/002-boss-readonly-extension/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/local-api.md、contracts/page-reader.md、quickstart.md

**Tests**: 包含测试任务。quickstart.md §1 明确列出了"必须覆盖并通过的场景"，constitution 原则 VIII 要求默认测试不联网、不需 Key、不开浏览器。

**Organization**: 按用户故事分阶段，P1 优先；每个阶段结束都有"单独验证"方法（Checkpoint）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、不依赖未完成任务）
- **[Story]**: 所属用户故事（US1–US7）
- 路径：Python 在 `src/jet/`，Python 测试在 `tests/`，插件在 `extension/`，插件测试在 `extension/tests/`

## 全局约束（每个任务都适用）

- 不向 zhipin.com 发任何请求；插件只 fetch `http://127.0.0.1:<port>`（原则 IV）。
- 代码中不写死真实数据路径；数据目录由 `JET_DATA_DIR` 或参数注入（原则 VIII）。
- 从 jet-demo 搬的代码：复制 + 附测试，文件头注明来源（jet-demo `d13aa43` + 原路径），不 import jet-demo（原则 XI）。
- "不知道"不能显示成"没有 / 不适合"（原则 IV、FR-026）。

---

## Phase 1: Setup（项目骨架）

**Purpose**: 建立 Python 包、插件目录和测试基础设施，让 `uv run pytest` 与 `node --test extension/tests/*.test.js` 都能在干净环境跑通。

- [x] T001 创建 `pyproject.toml`：uv 项目，`requires-python = ">=3.12"`，依赖 `fastapi`、`uvicorn`、`httpx`，dev 依赖 `pytest`；src 布局（`[tool.hatch.build.targets.wheel] packages = ["src/jet"]` 或等价配置）；入口脚本 `jet = "jet.cli:main"`；`[tool.pytest.ini_options]` 设 `testpaths = ["tests"]`，注册 `live` marker
- [x] T002 创建 Python 包骨架：`src/jet/__init__.py`（`__version__ = "0.1.0"`）、`src/jet/db/__init__.py`、`src/jet/domain/__init__.py`、`src/jet/llm/__init__.py`、`src/jet/api/__init__.py`；`src/jet/cli.py` 先只实现 `jet --version`（argparse，子命令 `serve`/`pair`/`stats` 占位，调用时打印"尚未实现"并以非零码退出）
- [x] T003 [P] 创建测试目录与隔离基础设施：`tests/unit/`、`tests/api/`、`tests/live/`、`tests/fixtures/boss/`（各含 `.gitkeep` 或 `__init__.py`）；`tests/conftest.py` 提供 `data_dir` fixture（`tmp_path` 下的目录，并用 `monkeypatch.setenv("JET_DATA_DIR", ...)` 注入），`autouse` 的会话级检查：测试开始前记录真实默认数据目录 `~/Library/Application Support/Jet/` 的文件清单（不存在则记为空），测试结束后若出现新文件或改动则让会话失败（原则 VIII）；`tests/live/conftest.py` 在未设置 `JET_LIVE=1` 时跳过该目录全部测试
- [x] T004 [P] 创建 `tests/unit/test_setup.py`：断言 `jet.__version__ == "0.1.0"`、`jet --version` 输出版本号（用 `subprocess` 调 `sys.executable -m jet.cli --version` 或直接调 `main(["--version"])`）、`data_dir` fixture 指向临时目录且 `JET_DATA_DIR` 已注入
- [x] T005 [P] 创建插件骨架：`extension/manifest.json`（MV3，`name` "Jet"，`version` "0.1.0"，`permissions`: `scripting`、`storage`、`sidePanel`；`host_permissions` 只有 `https://*.zhipin.com/*` 和 `http://127.0.0.1/*`；`background.service_worker` = `src/background.js`，`type: module`；`options_page` = `src/options.html`；`side_panel.default_path` = `src/sidepanel.html`；`minimum_chrome_version` "114"）；`extension/src/background.js`、`extension/src/options.html`、`extension/src/sidepanel.html` 先放最小占位内容（页面只显示"Jet"，SW 不做任何事）；`extension/package.json` 只含 `{"type": "module", "private": true}`，无依赖
- [x] T006 [P] 创建 `extension/tests/manifest.test.js`（`node --test`）：断言 `host_permissions` 恰好是上述两项、没有 `<all_urls>`、没有 `webRequest`/`debugger`/`cookies` 权限（原则 IV、V）
- [x] T007 [P] 创建 `.env.example`（`JET_LLM_BASE_URL=https://api.deepseek.com`、`JET_LLM_MODEL=deepseek-flash`、`JET_LLM_API_KEY=`、`JET_PORT=47615`、`JET_DAILY_LLM_LIMIT=50`，注释说明真实 `.env` 放在数据目录内而不是仓库）；确认 `.gitignore` 已忽略 `.env`、`*.db`、`data/`，并补上 `node_modules/`

**Checkpoint（单独验证）**: `uv sync && uv run pytest` 全部通过；`node --test extension/tests/*.test.js` 通过；`uv run jet --version` 打印 `0.1.0`；Chrome `chrome://extensions` 加载 `extension/` 无报错；真实数据目录无新文件。

---

## Phase 2: Foundational（所有故事的前提）

**Purpose**: 配置、数据库全部表、字符规范化、日志脱敏、本机接口骨架（只绑 127.0.0.1）、配对与认证、插件与 Jet 的连通。

**⚠️ CRITICAL**: 本阶段完成前不开始任何用户故事。

### Tests for Foundational

- [x] T008 [P] `tests/unit/test_config.py`：数据目录优先级（参数 > `JET_DATA_DIR` > 默认 `~/Library/Application Support/Jet/`）；从数据目录内 `.env` 读取；默认端口 47615、每日上限 50、模型 `deepseek-flash`；测试中从不触碰真实目录
- [x] T009 [P] `tests/unit/test_store.py`：建库后所有表存在、WAL 模式、外键开启；个人表 `user_id` 为 NOT NULL；首次启动创建用户 `me`；"无主人记录数 = 0" 自检函数（SC-008）；启动时把遗留 `judgements.status = 'running'` 改为 `interrupted`
- [x] T010 [P] `tests/unit/test_normalize.py`：NFKC 把"银⾏"（U+2F8F）规范成"银行"；全角字母数字转半角；去多余空白；城市规范化"深圳市" == "深圳"；规范化不修改传入原文（返回新字符串）
- [x] T011 [P] `tests/unit/test_logging.py`：`mask_sensitive_data` 遮蔽 `Bearer xxx`、`sk-...`、`api_key=...`（随 jet-demo 原测试一起搬）
- [x] T012 [P] `tests/api/test_auth.py`：`GET /v1/health` 无需认证并返回 `{"service":"jet","api":1,"version":"0.1.0"}`；配对码正确 → 200 返回 43 位令牌、库里只存 SHA-256；错码 → 400 `bad_code`；第 6 次尝试 → 429 `too_many_attempts`；5 分钟过期；受保护接口无令牌 / 令牌错 / Origin 与配对不一致 → 401 `unpaired`；`Origin: https://evil.example` → 403 `forbidden_origin` 且响应不含 `Access-Control-Allow-Origin`
- [x] T013 [P] `extension/tests/jet-client.test.js`：`jet-client.js` 只接受 `http://127.0.0.1:` 开头的地址，其他地址抛错；连接失败映射为 `jet_down`，401 映射为 `unpaired`；另写一个源码扫描测试：`extension/src/**/*.js` 中所有 `fetch(` 只出现在 `jet-client.js` 内

### Implementation for Foundational

- [x] T014 实现 `src/jet/config.py`：`Settings` dataclass（`data_dir`、`port`、`llm_base_url`、`llm_model`、`llm_api_key`、`daily_llm_limit`、`llm_timeout_s=20`、单价配置），解析顺序同 T008；API Key 只从数据目录 `.env` 或环境变量读取
- [x] T015 实现 `src/jet/db/schema.sql`：按 data-model.md 建 `users`、`jobs`、`job_versions`、`profiles`、`views`、`judgements`、`llm_calls`、`user_settings`、`pairings` 全部表与约束，包括 `UNIQUE(platform, platform_job_id)`、`UNIQUE(job_id, version_no)`、`UNIQUE(user_id, version_no)`、部分唯一索引 `UNIQUE(user_id, job_version_id, profile_id) WHERE superseded_by IS NULL`、`completeness IN ('list_only','full')`、`status IN ('queued','running','done','failed','quota_exhausted','interrupted')`、`user_settings.daily_llm_limit` 默认 50 且 `CHECK BETWEEN 0 AND 500`
- [x] T016 实现 `src/jet/db/store.py`：`open_db(data_dir)`（WAL、`foreign_keys=ON`、执行 schema）、事务辅助（普通事务与 `BEGIN IMMEDIATE`）、首次启动创建用户 `me` 和默认 `user_settings`、`reset_running_to_interrupted()`、`count_ownerless_rows()`（依赖 T015）
- [x] T017 [P] 实现 `src/jet/domain/normalize.py`：`normalize_text()`（NFKC + 全半角 + 压缩空白，只用于比较）、`normalize_city()`（再去掉末尾"市"）
- [x] T018 [P] 实现 `src/jet/logging.py`：从 jet-demo `core/security_utils.py::mask_sensitive_data` 复制并注明来源；提供带脱敏过滤器的 logger 工厂
- [x] T019 实现 `src/jet/api/auth.py`：内存中的一次性配对码（6 位数字、5 分钟、最多 5 次）；`POST /v1/pair` 逻辑（记录 `Origin`、生成 32 字节令牌、存 SHA-256、明文只返回一次）；FastAPI 依赖 `require_paired`（核对 Bearer + Origin，更新 `last_used_at`）；拒绝非 `chrome-extension://` Origin 的中间件（403，不加 CORS 头）；只对已配对扩展 Origin 返回 CORS 允许头
- [x] T020 实现 `src/jet/api/app.py` 与 `src/jet/api/routes.py`：`create_app(settings)` 工厂（启动时 `open_db` + `reset_running_to_interrupted` + 无主人自检，自检不为 0 则拒绝启动）；`GET /v1/health`；`POST /v1/pair`；`GET /v1/status`（本阶段 `profile.set` 恒为已有画像时 true / 否则 false，`quota` 从 `llm_calls` 与 `user_settings` 计算，`rule_excluded_today` 按本机时区当天统计；口径见 spec 指标定义）；统一错误格式 `{"error","message"}`
- [x] T021 实现 `src/jet/cli.py` 的 `serve` 与 `pair`：`jet serve` 用 uvicorn 只绑定 `127.0.0.1`（不接受其他 host 参数），启动时打印 `Jet listening on http://127.0.0.1:47615`、数据目录、今日额度；`jet pair` 通过本机接口的一个仅限回环 + 进程内密钥保护的内部端点（或共享的数据目录内一次性文件，二选一并在代码注释说明）让运行中的 Jet 生成配对码并在终端打印
- [x] T022 [P] 实现 `extension/src/jet-client.js`：`JET_BASE = "http://127.0.0.1:47615"`（可在设置里改端口，但主机名固定）；`callJet(method, path, body)` 自动带 Bearer 令牌（从 `chrome.storage.local` 读），把连接失败 / 401 / 409 / 422 / 其他错误映射为 `{ok:false, viewState}`
- [x] T023 [P] 实现 `extension/src/view-state.js` 骨架：`VIEW_STATES` 常量（contracts/page-reader.md §3 全部 13 种）和每种状态的固定中文文案；导出纯函数 `fromJetError()`（本阶段覆盖 `jet_down`、`unpaired`）
- [x] T024 实现设置页配对：`extension/src/options.html` / `options.js` 增加"连接状态"区（调用 SW `get_status`，显示"Jet 未运行 / 未配对 / 已配对"）和"配对码"输入框；`extension/src/background.js` 处理 `pair`、`get_status` 消息并转发到 `jet-client.js`，配对成功把令牌存入 `chrome.storage.local`

**Checkpoint（单独验证）**: `uv run pytest` 与 `node --test extension/tests/*.test.js` 通过；`uv run jet serve` 后 `curl -s http://127.0.0.1:47615/v1/health` 返回 JSON；`uv run jet pair` 显示配对码，插件设置页输入后显示"已配对"；停掉 Jet 后设置页显示"Jet 未运行"。

---

## Phase 3: User Story 1 - 设置简单画像 (Priority: P1) 🎯 MVP

**Goal**: 在插件设置页编辑并保存画像，画像按版本存在 Jet 里。

**Independent Test**: 打开设置页，填写方向、城市、底线并保存；关闭后再打开，内容还在；未保存过画像时 `/v1/status` 的 `profile.set = false`，设置页提示"请先设置画像"。

### Tests for User Story 1

- [x] T025 [P] [US1] `tests/unit/test_profiles.py`：`directions`、`cities` 至少 1 项，否则拒绝；关键词去首尾空白、去重；保存内容与当前版本相同 → `version_no` 不变、`changed = false`；不同 → `version_no + 1`；当前画像 = 最大版本号；`min_monthly_k` 可为 NULL
- [x] T026 [P] [US1] `tests/api/test_profile_api.py`：未设置时 `GET /v1/profile` → 404 `no_profile`；`PUT` 后 `GET` 返回同样内容；`PUT` 响应 `{"version_no","changed"}`；`/v1/status` 的 `profile.set`、`profile.version_no` 随之变化；缺少 `directions` → 422

### Implementation for User Story 1

- [x] T027 [US1] 实现 `src/jet/domain/profiles.py`：`save_profile(conn, user_id, data)`（校验、规范化列表、与当前版本比较、写新版本）、`get_current_profile(conn, user_id)`
- [x] T028 [US1] 在 `src/jet/api/routes.py` 增加 `GET /v1/profile`、`PUT /v1/profile`（pydantic 模型：`directions: list[str]`、`keywords: list[str]`、`cities: list[str]`、`min_monthly_k: float | None`、`exclude_keywords: list[str]`），接到 `/v1/status`
- [x] T029 [US1] 设置页画像表单：`extension/src/options.html` / `options.js` 增加方向、关键词、城市、不接受关键词（逗号或换行分隔，可多个）和最低月薪（千元/月）输入；打开时经 SW `get_profile` 读取回填，保存时经 SW `save_profile` 提交；无画像时显示"请先设置画像"；保存后显示"已保存（版本 N）"或"内容未变化"
- [x] T030 [US1] `extension/src/background.js` 处理 `get_profile`、`save_profile` 消息；`extension/src/view-state.js` 增加 `no_profile` 映射（409 `no_profile`），并在 `extension/tests/view-state.test.js` 覆盖

**Checkpoint（单独验证）**: 按上面 Independent Test 操作。US1 验收场景 1（点开岗位提示"请先设置画像"且照常入库）和场景 3（改画像后旧判断"可能过时"）依赖 US2/US3，在那两个阶段的 Checkpoint 中验证。

---

## Phase 4: User Story 2 - 点开岗位就自动判断 (Priority: P1)

**Goal**: 在搜索列表页点开岗位 → 插件读详情 → Jet 入库、规则粗筛、后台调用大模型 → 详情旁显示结论。

**Independent Test**: 画像已设、Jet 在运行，点开 3 个不同岗位，各自显示"判断中"后出现"适合 / 不适合 + 理由 + 判断时间"；`jet stats` 显示新增 3 个岗位、3 条判断。

### Tests for User Story 2

- [x] T031 [P] [US2] 准备脱敏样本 `tests/fixtures/boss/search_list.json`（15 条岗位，只保留 contracts/page-reader.md 映射表中的字段 + 若干无关字段，去掉 `securityId`、`lid`、招聘者姓名）、`tests/fixtures/boss/detail.json`（1 条详情，描述中含"银⾏"康熙部首字符）、`tests/fixtures/boss/broken.json`（`jobList` 不是数组）；插件测试共用同一批样本
- [x] T032 [P] [US2] `extension/tests/page-reader.test.js`：用 T031 样本构造假的 `document`/Vue 实例对象，断言 `readBossPage` 输出 contracts/page-reader.md §2 结构；空薪资 → `null`；`district` 空 → `null`；缺 `platform_job_id`/`title` 或 `jobList` 非数组 → `ok = false` 且 `problems` 非空；路径 `/job_detail/` → `page_kind = job_detail_page`；超过 200 条截断并写入 `problems`；函数不写入任何属性（用冻结对象 / Proxy 断言无写操作）
- [x] T033 [P] [US2] `tests/unit/test_salary.py`：从 jet-demo 复制原 8 个测试，并新增 research.md §3 的 3 处缺陷用例（"招5人"不能解析成薪资；"1万-2万"→ 10–20K；"5-8万"年薪判定正确）以及"无法解析 → `parse_ok = False`（不是面议）"、`·14薪` → `months = 14`、日薪 / 时薪换算
- [x] T034 [P] [US2] `tests/unit/test_rules.py`：城市不在画像城市 → 排除并写明规则；"深圳市"匹配"深圳"；月薪**上限** < `min_monthly_k` → 排除；薪资不可见或解析失败 → 跳过薪资规则；职位名或描述命中不接受关键词（规范化后子串匹配）→ 排除并写出命中词；全部通过 → `passed = True`
- [x] T035 [P] [US2] `tests/unit/test_quota.py`：上限 2 时并发 5 次预占（多线程 + 同一数据库文件）→ 恰好 2 条 `reserved/billed=1`；`not_sent` 退还额度；`timeout`/`http_error`/`parse_error` 保持 `billed = 1`；"今日"按本机时区；上限 0 → 不预占
- [x] T036 [P] [US2] `tests/unit/test_llm_client.py`（用 `httpx.MockTransport` 假模型，不联网）：正常返回 `{"verdict":"fit","reasons":[...]}` → 解析成功，理由截到 ≤ 3 条；非 JSON / 缺字段 / verdict 不在两值内 → `parse_error`；超时 → `timeout`；429/5xx 最多重试 2 次且每次重试都先预占额度；连接被拒 → `not_sent`
- [x] T037 [P] [US2] `tests/api/test_observations_detail.py`（假模型注入 app）：有画像时 `POST /v1/observations`（detail）立即返回 `queued` 判断，后台完成后 `GET /v1/judgements?ids=` 返回 `done`；同一岗位同内容连发 5 次 → 1 个岗位、1 个版本、大模型最多 1 次（SC-002）；规则排除 → `done/unfit/rule` 且 0 次调用；额度 = 2 时 5 个通过粗筛的新岗位 → 恰好 2 次调用、3 个 `quota_exhausted`；假模型超时 → `failed`、计入额度；无画像 → 岗位入库、`judgement = null`，响应带 `no_profile` 提示（US1 场景 1）；`detail` 缺 `description` 或多于 1 个 job → 422
- [x] T038 [P] [US2] `extension/tests/view-state.test.js` 扩展：`queued`/`running` → 判断中；`done` + `fit`/`unfit` → `done_fit`/`done_unfit`；`failed`/`quota_exhausted`/`interrupted` 各自映射；断言没有任何错误或非 `done` 状态映射成 `done_unfit`（FR-026、SC-004）

### Implementation for User Story 2

- [x] T039 [P] [US2] 实现 `extension/src/page-reader.js`：从 `experiments/boss-probe-ext/reader.js` 复制读取逻辑并按 contracts/page-reader.md §2 改写为自包含函数 `readBossPage()`（能被 `chrome.scripting.executeScript({world:"MAIN", func})` 序列化执行，不引用外部变量）；只读属性、复制成普通对象；不写属性、不留全局变量、不注册事件、不发请求；文件头注明来源
- [x] T040 [P] [US2] 实现 `src/jet/domain/salary.py`：从 jet-demo `core/salary_parser.py` 复制，修正 3 处缺陷，去掉 pydantic 改 dataclass（`min_k`、`max_k`、`months`、`parse_ok`），文件头注明来源
- [x] T041 [US2] 实现 `src/jet/domain/jobs.py`：`ingest(conn, user_id, page_type, jobs, observed_at)`，按 data-model.md"变化判定"：规范化后比较实质字段（列表来源只比较职位名、薪资、城市，缺失描述不算变化）；相同只更新 `last_seen_at`；不同新增版本；`list_only` 首次读到详情 → 新增 `source = detail` 版本并升级为 `full`；`full` 不降级；空薪资 → `salary_raw = NULL, salary_visible = 0`；调用 `salary.py` 填 `salary_min_k`/`salary_max_k`/`salary_months`/`salary_parse_ok`；`district` 空 → NULL；每个岗位写一条 `views`（依赖 T040）
- [x] T042 [P] [US2] 实现 `src/jet/domain/rules.py`：`screen(job_version, profile) -> RuleResult(passed, hits)`，规则见 research.md §5（城市去后缀后精确比较、不做子串包含；月薪上限 < 最低月薪；不接受关键词在规范化后的职位名 + 描述里子串匹配）
- [x] T043 [P] [US2] 实现 `src/jet/llm/prompt.py`：固定系统提示词在前、画像其次、岗位最后；要求只返回 `{"verdict":"fit|unfit","reasons":[...]}`、理由 ≤ 3 条每条 ≤ 40 字；`parse_verdict(text)` 严格校验，失败抛出解析错误
- [x] T044 [US2] 实现 `src/jet/llm/quota.py`：`reserve(conn, user_id, judgement_id, provider, model)` 在 `BEGIN IMMEDIATE` 中数今日 `billed = 1` 行数，小于上限才插入 `outcome = 'reserved', billed = 1`；`finish(call_id, outcome, usage)`，`not_sent` 置 `billed = 0`；按配置单价估算 `cost_cny`
- [x] T045 [US2] 实现 `src/jet/llm/client.py`：OpenAI 兼容 `/chat/completions`（`base_url` + `model` + `api_key`），关闭思考模式、JSON 输出、温度 0、超时 20 秒；网络错误 / 429 / 5xx 指数退避最多重试 2 次，每次尝试前调用 `quota.reserve`，额度不足则停止；参考 jet-demo `job_analysis/providers/deepseek.py` 的 HTTP / 重试骨架重写（注明参考来源）；可注入 `httpx` transport 以便测试
- [x] T046 [US2] 实现 `src/jet/domain/judgements.py`：`request_judgement(conn, user_id, job)`——`list_only` 或无画像不创建；已有现行判断（同用户 × 岗位版本 × 画像版本、`superseded_by IS NULL`）直接返回；否则规则粗筛：排除 → `done/unfit/rule`（`reasons` 写命中规则，`rule_result` 保存）；通过且今日有额度 → `queued`；无额度 → `quota_exhausted`（仍保存 `rule_result`）；`to_api(judgement)` 输出 local-api.md 的 Judgement 对象（本阶段 `stale` 先输出两个 false，US3 再补计算）
- [x] T047 [US2] 实现 `src/jet/worker.py`：进程内 asyncio 队列 + 1 个工作协程；内存 in-flight 集合按 `(user_id, job_id)` 合并；取出后置 `running`，调用 `client.py`，成功 → `done/llm`，失败 → `failed` 并记录 `error`；FastAPI lifespan 中启动 / 停止；可注入假客户端
- [x] T048 [US2] 在 `src/jet/api/routes.py` 增加 `POST /v1/observations`（pydantic 校验：`page_type` 为 `list`/`detail`，`detail` 时恰好 1 个 job 且 `description` 必填，否则 422 `invalid_payload`；一个事务内 `ingest` + 对 detail 调 `request_judgement`；`queued` 交给 worker；立即返回 `jobs` 结构）和 `GET /v1/judgements?ids=`
- [x] T049 [US2] 实现 `extension/src/content.js`（ISOLATED world，`manifest.json` 注册到 `https://*.zhipin.com/*`）：`MutationObserver` 只观察列表容器和详情面板的子节点变化，防抖 400 ms 后发 `page_changed`；页面加载完成时也发一次；只观察不改 DOM
- [x] T050 [US2] 在 `extension/src/background.js` 实现读取调度：收到 `page_changed` → `chrome.scripting.executeScript({target:{tabId}, world:"MAIN", func: readBossPage})`；按 contracts/page-reader.md §5 用 `lastSent.detail`（岗位 ID + 内容摘要）去重；详情变化时 `POST /v1/observations`（detail）；`queued`/`running` 时每 1.5 秒轮询 `/v1/judgements`，最多 60 秒后显示"判断仍在进行，稍后再看"（后来改为最多 150 秒，见 `extension/src/background.js` 的 `maxDuration`）；把结果经 `view-state.js` 映射后发 `render_state` 给内容脚本
- [x] T051 [US2] 在 `extension/src/content.js` 实现详情旁结论卡片：只在自建的 Shadow DOM 宿主元素（带 `data-jet` 属性）里渲染"判断中 / 适合 / 不适合 + 理由 + 判断时间"及其他状态文案；不修改 BOSS 原有元素的内容、样式和事件
- [x] T052 [US2] 在 `src/jet/cli.py` 实现 `jet stats`：读数据库打印岗位总数（`list_only` / `full`）、今日大模型调用次数 / 上限 / 剩余、今日规则排除数、判断按状态计数（口径同 spec 指标定义）

**Checkpoint（单独验证）**: 自动测试全绿；真实页面按 quickstart R1、R2 操作（R1 同时验证 US1 场景 1）。真实大模型调用需要在数据目录 `.env` 放 API Key。

---

## Phase 5: User Story 3 - 已判断过的岗位直接显示结果 (Priority: P1)

**Goal**: 再次点开已判断岗位直接显示原结论；岗位或画像变了标"可能过时"，用户手动"重新判断"。

**Independent Test**: 再点开一个已判断岗位，1 秒内显示原结论，`jet stats` 调用次数不变；修改画像后再点开，显示"可能过时（画像已变）"和"重新判断"按钮，点击后按新画像重新判断。

### Tests for User Story 3

- [x] T053 [P] [US3] `tests/unit/test_judgements_stale.py`：`job_version_id ≠ jobs.current_version_id` → `stale.job_changed = true`；`profile_id ≠` 当前画像 → `stale.profile_changed = true`；两者可同时为真；"可能过时"是计算结果、不存库
- [x] T054 [P] [US3] `tests/api/test_rejudge.py`：薪资变化后再次 observe → 新版本、旧判断 `stale.job_changed = true`、没有自动重判；修改画像 → 旧判断 `stale.profile_changed = true`；`POST /v1/jobs/{id}/judge` 新建判断、旧行 `superseded_by` 指向新行；`failed`/`interrupted` 重试在原行改回 `queued`；`list_only` 岗位 → 409 `list_only`；额度不足 → 200 且 `status = quota_exhausted`；已有 `queued/running` → 返回同一条
- [x] T055 [P] [US3] `extension/tests/view-state.test.js` 扩展：`done` 且任一 `stale` 为真 → `stale`，文案注明"岗位已变 / 画像已变"，并附带原结论

### Implementation for User Story 3

- [x] T056 [US3] 在 `src/jet/domain/judgements.py` 实现：查找"该用户该岗位最近的现行判断"（即使版本或画像已变也返回它，并计算 `stale`），observe 时若当前版本 × 画像没有现行判断但存在旧判断 → 返回旧判断 + `stale`，不自动新建；`rejudge(conn, user_id, platform_job_id)` 按 local-api.md `POST /v1/jobs/{id}/judge` 规则处理（新建 + `superseded_by`，或重试改回 `queued`）
- [x] T057 [US3] 在 `src/jet/api/routes.py` 增加 `POST /v1/jobs/{platform_job_id}/judge`，`queued` 交给 worker
- [x] T058 [US3] 插件：结论卡片在 `stale`/`failed`/`interrupted`/`quota_exhausted` 时显示"重新判断"或"重试"按钮（在 Shadow DOM 内）；点击 → 内容脚本发消息 → SW 调 `POST /v1/jobs/{id}/judge` 并进入轮询；修改 `extension/src/content.js`、`extension/src/background.js`

**Checkpoint（单独验证）**: quickstart R3；再改画像后点开同一岗位看到"可能过时（画像已变）"并能重新判断（US1 场景 3）。

---

## Phase 6: User Story 7 - 判断先讲事实、再对照偏好；标注与评测 (Priority: P1)

> 2026-09-24 新增（真实页面试用反馈）。放在 US4–US6 之前。

**Goal**: 卡片先回答事实问题（每项引用原句），再给出适合 / 存疑 / 不适合与推导；可在卡片上标注事实形成评测集；`jet eval run` 比较提示词与模型组合。

**Independent Test**: 启动后自动迁移并备份旧库；点开"银行合作产品运营助理"这类岗位，卡片工作类型不是"运营"、销售成分为中或高且列出原句；标注后设置页评测集数量增加；`jet eval run` 输出各组合指标且今日判断额度不变。

### Tests for User Story 7

- [x] T059 [P] [US7] `tests/unit/test_migrations.py`：用初始结构（user_version 0，含 `verdict` 只允许 fit/unfit 的旧 `judgements`、`judgement_id NOT NULL` 的旧 `llm_calls`）建一个临时库并写入岗位、画像、判断（含 `superseded_by` 链）、调用记录、配对；执行迁移后：同目录出现 `jet.db.bak-*` 且可打开、内容为旧数据；各表行数不变；旧判断 `prompt_version` 为 `rule`/`v1`；可插入 `verdict='unsure'` 与 `judgement_id=NULL, purpose='eval'` 的调用；`PRAGMA foreign_key_check` 为空；`user_version = 2`；再次启动不重复迁移、不再备份；迁移中途出错时回滚且 `user_version` 不变
- [x] T060 [P] [US7] `tests/unit/test_prompt_v2.py`：`build_messages` 含画像全部字段（包括工作内容偏好、我的背景）与变相销售信号清单、"依据职责而非职位名"要求；`parse_facts` 严格校验 data-model.md"事实结构"：枚举值必须在选项内（`work_type` 数据/运营/营销/销售/客服/技术支持/其他；`sales_level` 高/中/低；`experience.value` 满足/差一点/不满足/无法判断；`overtime` 有/未提及/明确双休或不加班；`verdict` fit/unsure/unfit），`derivation` 1–5 条，缺字段或越界 → ParseError
- [x] T061 [P] [US7] `tests/unit/test_quotes.py`：原句在职位描述中（含康熙部首"银⾏"、全半角、空白差异）→ `found=true`；改写过的句子 → `found=false`；空引用 → `found=false`；统计函数返回 total / missing
- [x] T062 [P] [US7] `tests/unit/test_labels.py`：同一用户 × 岗位版本只保留最新一条（覆盖）；全为 null → 拒绝；取值不在选项内 → 拒绝；note > 100 字 → 拒绝；`corrected` 计算（某项与判断事实或结论不一致为 true，只比较已填项）；计数 total / with_overall；`list_only` 岗位拒绝
- [x] T063 [P] [US7] `tests/unit/test_eval.py`（假 DeepSeek 用 `httpx.MockTransport`，假 Jev 用注入的假客户端）：4 个组合对 3 条标注跑通，指标计算正确（事实准确率只算已标注项，"存疑/无法判断"算不一致；误判为适合；引用未找到率；费用）；评测调用 `purpose='eval'` 且不改变 `/v1/status` 的今日已用次数；达到 `--max-calls` 即停止并在报告中标明未完成部分；`jet eval report --threshold` 用保存的 Jev 概率离线重算、不发任何请求；结果文件写在临时数据目录 `evals/` 下
- [x] T064 [P] [US7] `tests/api/test_us7_api.py`：画像新字段读写与 ≤500 字校验；v2 判断完成后 Judgement 含 `facts`（引用带 found）、`derivation`、`prompt_version='v2'`、`engine`；`verdict='unsure'` 正常返回；把配置的提示词版本调高后旧判断 `stale.method_changed=true` 且不自动重判；`PUT /v1/jobs/{id}/label` 各分支（404、409 list_only、422、成功后 Judgement.label.corrected 与 `/v1/status.labels` 计数）
- [x] T065 [P] [US7] 插件测试：`extension/tests/view-state.test.js` 增加 `done_unsure`（存疑，黄色，不得映射为 `done_unfit`）、`describe` 输出事实与推导、`stale` 带"判断方式已更新"；新建 `extension/tests/label-form.test.js` 覆盖把表单选择转成接口请求体的纯函数（空选项 → null、全空 → 不允许提交）

### Implementation for User Story 7

- [x] T066 [US7] 实现 `src/jet/db/migrations.py` 并更新 `src/jet/db/schema.sql` 到结构版本 2（新库直接建新结构并设 `user_version=2`）；`init_db` 调用 `migrate(data_dir)`：版本 < 2 时先 `wal_checkpoint(TRUNCATE)` 再复制备份 `jet.db.bak-<UTC 时间戳>-v<旧版本>`，然后**在事务外**关闭外键（`PRAGMA foreign_keys=OFF`，SQLite 不允许在事务内切换），事务内按 data-model.md"结构迁移"重建表并回填，校验行数与 `PRAGMA foreign_key_check`，提交后写 `user_version=2`、重新打开外键；失败回滚并抛出清晰的中文错误（`jet serve` 打印并退出，备份保留）；`jet serve` 启动时打印"已从结构版本 N 迁移到 2，备份：<文件名>"
- [x] T067 [US7] 画像新字段：`src/jet/domain/profiles.py` 与 `src/jet/api/routes.py` 的 `GET/PUT /v1/profile` 支持 `work_preference`、`background`（默认空字符串，各 ≤ 500 字，参与"内容是否变化"比较）
- [x] T068 [P] [US7] 实现 `src/jet/domain/quotes.py`：`check_quote(text, description) -> bool`（两边都 `normalize_text` 后子串匹配，并去掉所有空白再比较一次）、`annotate_facts(facts, description) -> facts`（给每段引用加 `found`）、`count_quotes(facts) -> (total, missing)`
- [x] T069 [P] [US7] 实现 `src/jet/llm/prompt_v2.py`：`PROMPT_VERSION = "v2"`；固定系统提示词（先事实后建议；依据职责而非职位名；变相销售信号清单：对接客户或渠道、商务拓展、业绩指标、营销推广或活动策划、获客转化、维护客户关系、陪同拜访；先摘原句再定销售成分；职位名好听但职责以营销推广、对接客户、完成业绩为主的按销售处理；原句必须逐字摘自职位描述；推导写明事实与画像哪一条一致或冲突；三档建议的含义；只输出 JSON）；`build_messages(profile, job_version, known_facts=None)`（`known_facts` 为组合 D 中 Jev 已判定的事实，要求模型沿用并为其找原句）；`parse_facts(text)` 按 data-model.md"事实结构"严格解析，返回 `(facts, verdict, derivation)`
- [x] T070 [US7] 改造 `src/jet/llm/client.py` 与 `src/jet/worker.py`：`call_once` 支持 `thinking: bool`（开启时 `thinking={"type":"enabled"}` 并用 `JET_LLM_THINKING_TIMEOUT_S`，默认 60 秒）与 `max_tokens`；`run_llm_judgement` 按配置 `JET_PROMPT_VERSION`（默认 v2）与 `JET_JUDGE_ENGINE`（默认 `deepseek-flash:no-think`）选择提示词与组合；v2 结果经 `quotes.annotate_facts` 后写入 `judgements.facts`、`derivation`、`verdict`、`prompt_version`、`engine`；规则排除写 `prompt_version='rule'`；额度预占只数 `purpose='judge'`
- [x] T071 [US7] 更新 `src/jet/domain/judgements.py`：`to_api` 输出 `prompt_version`、`engine`、`facts`、`derivation`、`stale.method_changed`（大模型判断且版本低于当前配置版本）、`label`（含 `corrected`）；`rejudge` 把"判断方式已更新"视为过时，按当前版本新建判断并 supersede 旧判断；`verdict='unsure'` 全链路可用
- [x] T072 [P] [US7] 实现 `src/jet/llm/jev.py`：用 `typesafe_sdk.AsyncTypeSafeClient.system_one`（`model` 默认 `jev-latest`，Key 取 `JET_TYPESAFE_API_KEY`）回答事实选择题：工作类型、销售成分、经验是否满足（对照"我的背景"）、加班信号；每题 `Choice(instructions=中文问题, criteria={选项: 中文描述})`；返回每题选项与概率，最高概率 < 阈值 → `存疑`；同步包装函数供评测调用；可注入假客户端；参考 jet-demo `evaluators/jev_judge/client.py` 的调用方式，不复制代码；Key 缺失时报清晰错误；`pyproject.toml` 增加 `typesafe-sdk` 依赖
- [x] T073 [US7] 实现 `src/jet/domain/labels.py` 与路由 `PUT /v1/jobs/{platform_job_id}/label`（契约见 contracts/local-api.md）；`/v1/status` 增加 `labels.total`、`labels.with_overall`
- [x] T074 [US7] 实现 `src/jet/eval/runner.py` 与 CLI `jet eval run [--variants A,B,C,D] [--max-calls 300] [--threshold 0.6]`、`jet eval report [<运行 ID>] [--threshold X]`：评测集 = 当前用户全部标注对应的岗位版本 + 当前画像；组合 A/B/C/D 按 spec FR-037；每次调用预占 `purpose='eval'`、`eval_run_id` 的额度（上限 `--max-calls`，默认取 `JET_EVAL_MAX_CALLS`=300）；逐条结果（含 Jev 概率、事实、引用核对、费用）写 `<数据目录>/evals/<运行 ID>.json`；终端打印汇总表：每组合 × 每项事实准确率、误判为适合次数（只算填了总体结论的标注）、引用未找到率、调用次数与费用；评测集为空时提示"请先在卡片上标注岗位"
- [x] T075 [P] [US7] 配置：`src/jet/config.py` 增加 `typesafe_api_key`、`prompt_version`（`JET_PROMPT_VERSION`，默认 v2）、`judge_engine`（`JET_JUDGE_ENGINE`）、`llm_thinking_timeout_s`、`eval_max_calls`（300）、`jev_threshold`（0.6）；`.env.example` 增加对应行（`JET_TYPESAFE_API_KEY=` 留空）
- [x] T076 [P] [US7] 设置页：`extension/src/options.html` / `options.js` 增加"工作内容偏好""我的背景"两个多行文本框（字数计数，≤ 500）与"评测集：已标注 N 条（含总体结论 M 条）"
- [x] T077 [US7] 卡片事实与建议：`extension/src/view-state.js` 增加 `done_unsure` 与事实/推导的 `describe` 输出；`extension/src/content.js` 卡片先显示事实（每项附原句，`found=false` 的原句旁标"未在原文找到"），再显示建议（适合绿 / 存疑黄 / 不适合红）与推导；卡片可折叠、可滚动，高度不超过视口 70%
- [x] T078 [US7] 卡片标注区：`extension/src/content.js` 增加默认收起的"标注"区（工作类型、销售成分、经验是否满足、加班信号四组选择按钮 + 总体结论选填 + 备注输入框），已有标注时回填并显示"已纠正"；输入框内的键盘事件不冒泡到页面；新建纯函数模块 `extension/src/label-form.js`；`extension/src/background.js` 处理 `save_label` 并重新渲染卡片
- [ ] T079 [US7] 文档（README 部分已完成；research.md §7 的评测数字待真实评测后补）：更新 `README.md`（`jet eval run` 用法、`.env` 中 `JET_TYPESAFE_API_KEY` 格式）；评测跑完后把各组合汇总数字与默认组合决定写入 research.md §7（由 Claude 在人工评测后完成，不在代码委托中）（2026-10-06：暂不做，需要真实评测数据）

### 真实页面反馈修复（2026-09-25，R13 后）

- [x] T079a [US7] 标注区点选项时卡片与 BOSS 页面滚动位置保持不动：选项点击只切换按钮样式，不重建卡片；必须重建时保存并恢复卡片 `scrollTop` 与页面 `scrollX/Y`；卡片宿主拦截 click / mousedown / mouseup / pointerdown / pointerup 冒泡到页面（`extension/src/content.js`）
- [x] T079b [US7] 卡片避开页面上方操作按钮（如"立即沟通"）：卡片最高 `min(50vh, 480px)`；可切换左下 / 右下，记在 `chrome.storage.local.cardSide`（`extension/src/content.js`）
- [x] T079c [US7] 卡片最上方结论摘要行（结论 + 一句话理由；无 `verdict_reason` 时用推导第一条）；事实区可折叠，默认折叠，展开状态记在 `chrome.storage.local.factsExpanded`（`extension/src/content.js`、`extension/src/view-state.js`）
- [x] T079d [US7] 提示词 v3：经验要求区分硬性 / 优先 / 未提及，"优先"项不满足最多"差一点"（解析后代码强制降级）；输出一句话理由 `verdict_reason`；`judgements.verdict_reason` 可空列；默认 `JET_PROMPT_VERSION=v3`，v2 判断标"判断方式已更新"；评测组合 B/C/D 使用当前版本（`src/jet/llm/prompt_v3.py` 等）

- [x] T079e [US7] 工作类型改为主要类型（单选）+ 次要类型（多选，0–3 个，选填）：提示词 v3 输出 `facts.work_type.secondary`；`labels.secondary_work_types` 可空列与 `PUT label` 校验；纠正判定与评测准确率只看主要类型，次要类型只记录、展示；卡片事实区显示"主要 / 次要"，标注区增加次要类型多选（`src/jet/llm/prompt_v3.py`、`src/jet/domain/labels.py`、`src/jet/eval/runner.py`、`extension/src/content.js`、`extension/src/label-form.js`）

### 分类与结论调整（2026-09-25，标注前）

- [x] T079f [US7] 结构版本 3 迁移（备份、重建 judgements 与 labels、旧标注按 data-model.md 转换并打印被清空的字段）；共享分类常量 `src/jet/domain/taxonomy.py`（大类、细分、工作强度、四档结论及中文名）
- [x] T079g [US7] 提示词 v4：两层工作类型（主要 + 次要）、工作强度五档、四档结论及含义；默认 `JET_PROMPT_VERSION=v4`；规则排除为 `skip`；Jev 问题改为 8 个大类与工作强度五档；标注校验、纠正判定（按大类）、评测指标（按大类、四档一致率、误判为可投）同步更新
- [x] T079h [US7] 插件：四档结论与颜色；事实区显示"大类 / 细分"；标注区主要类型（大类 + 细分）、次要类型（多选，大类或细分）、工作强度五档、总体结论四档；Jet 的答案只在选项旁显示"Jet"小标记，不预选

### 不依赖用户标注（2026-09-25 修订）

- [x] T079i [US7] 提示词 v4 增加 `hr_questions`（结论为 try / check 时 2–3 个，针对该职位描述的模糊或可能有坑之处；其他结论为空），同一次调用输出；`judgements.hr_questions` 可空列；卡片在摘要下方显示"建议问 HR"
- [x] T079j [US7] HR 说的实际情况：`hr_notes` 表、`PUT /v1/jobs/{id}/hr-note`、各接口岗位条目带 `hr_note`；卡片上可填写 / 修改 / 清空（一句话，≤ 200 字）
- [x] T079k [US7] 标注改为可选：卡片标注区标题"标注（可选）"；设置页评测集数量改为底部灰色小字
- [x] T079l [US7] 模型参考标注：`reference_labels` 表；`jet eval export-jobs`、`jet eval import-reference`；评测参考答案按字段取"用户标注 > 模型参考标注"，报告注明来源构成与"模型参考标注，非人工"
- [x] T079m [US7] 由 Antigravity（Gemini 3.8 Flash (High)）对导出的岗位做参考标注，结果导入；Claude 抽样核对格式与明显错误，用户抽查（2026-10-06 补勾：本机数据库 reference_labels 已有 17 条参考标注；用户抽查情况未记录）

### 规则误判与风险信号（2026-09-25 真实页面）

- [x] T079n [US7] 不接受关键词只匹配职位名称，不再匹配职位描述正文（`src/jet/domain/rules.py`）；测试覆盖"不涉及电商、销售、客服"这类否定句不被排除；岗位标签的字段名待真实页面确认后再加入匹配（NOT VERIFIED）
- [x] T079o [US7] 提示词 v4 增加风险信号检查（见 FR-043），命中时结论强制为 skip（代码兜底）；`judgements.facts.risk_signals`；卡片最上方醒目警告；以 `tests/fixtures/boss/risk_finance_jd.txt`（"互联网金融"岗位职位描述）为测试样例：假模型返回风险信号时结论为 skip，规则不再因正文"销售"排除它

- [x] T079p [US7] 规则版本：规则排除判断记 `engine='rules:r2'`（NULL 视为 r1），版本低于当前时 `stale.method_changed=true`；`POST /v1/jobs/{id}/judge` 支持 `{"force": true}`；卡片对所有已完成判断显示"重新判断"（过时 / 失败 / 中断醒目，其余次要样式）；以"互联网金融"旧规则判断为回归测试

**Checkpoint（单独验证）**: 自动测试全绿；quickstart R11–R14；参考标注导入后 R15。

---

### 评测修复与画像城市调整（2026-09-25，US8 之前）

- [x] T079u [US7] 评测修复：开启思考时 `max_tokens` 提高到 8192；解析失败时也记录用量与 `finish_reason`（如"输出被截断"）；`jet eval report` 离线重算（含 `--threshold`）输出空表的 bug；之后只重跑组合 C
- [x] T079v [US7] 画像城市与薪资（FR-018、FR-048、FR-049）：偏好城市 / 不去的城市 / 非偏好城市的最低月薪；规则 r3（只排除不去的城市、最低月薪、职位名关键词）；提示词 v5 与代码兜底（非偏好城市且薪资上限低于该值 → 最多 check）；设置页表单；`export-jobs` 导出新字段（属已同意的"城市和底线"范围）
- [x] T079w [US7] 参考标注总体结论作废（FR-050）：`reference_labels.overall_void`，启动时对已有行置 1（只执行一次）；评测不用作废的总体结论并在报告注明

- [x] T079x [US7] 复核（FR-051）：B 给出 apply / try 时用 C（开启思考）复核，只降档；`judgements.review`；复核计入每日额度；卡片"复核中"；轮询上限 150 秒（Claude 直接实现，用户同意）

### 过时判断自动更新与标记位置（2026-09-25，US4 验证后；constitution 3.0.0）

- [x] T079y [US7] Python：结构版本 4 迁移（备份 + `judgements.origin`）；打开详情时自动更新过时判断（FR-052：状态 skipped / applied 不更新；规则判断先按新规则重算；额度不足不新建、返回 notice）；Judgement 增加 `origin`、`replacing`；测试覆盖各分支
- [x] T079z [US4/US7] 插件：卡片"更新中"（旧结论淡色）/"更新失败"（保留旧结论 + 重试）/ 额度不足提示；列表标记改为左侧色条 + 上方间隙标签，放不下只显示色条、悬停显示文字，不压卡片内容（FR-054）；过时标记浅色虚线 + "过时"，我的岗位库与侧边栏同样区分（FR-053）

- [x] T079aa [US4/US8] 真实页面修复：标记不画在固定导航栏上（按卡片可见部分裁剪）；同一岗位多个链接只算一张卡片、空隙按所有卡片计算；"最近看过"只按打开详情的查看时间；"全部"改名"已标记"；侧边栏"当前岗位"显示职位名

## Phase 6b: User Story 8 - 我的岗位库 (Priority: P2)

> 2026-09-25 新增，排在 US7 收尾之后、US5/US4/US6 之前。

**Goal**: 卡片标记收藏 / 已投递 / 不考虑；插件"我的岗位库"页面按状态和"最近看过"找回岗位，点击打开 BOSS 原岗位。

**Independent Test**: 见 spec US8。

- [x] T079q [US8] 存储与接口：`job_status`、`job_status_events` 表与 `jobs.company_name` 列（启动时补建）；`PUT /v1/jobs/{id}/status`（变化必追加事件）；`GET /v1/my-jobs`；岗位条目带 `my_status`、`company_name`；测试覆盖互斥、取消、事件追加、各筛选数量与排序、`recent` 口径
- [x] T079r [US8] 公司名读取：`readBossPage` 列表读 `brandName`、详情读候选字段（均 NOT VERIFIED，读不到为 null）；observations 传 `company_name`，入库时有值才更新；测试覆盖有 / 无公司名
- [x] T079s [US8] 卡片状态按钮：收藏 / 已投递 / 不考虑（互斥、可取消），点按钮只更新这一小块，不重建卡片
- [x] T079t [US8] 插件"我的岗位库"页面 `extension/src/myjobs.html` / `myjobs.js`：五个筛选与数量、列表字段、四色结论、"公司未读取"、点击行 `chrome.tabs.create` 打开 BOSS 原岗位（仅用户点击时）；入口：设置页顶部链接、工具栏图标（`action` 打开该页面）；纯函数测试（列表行渲染数据、链接构造只允许 `https://www.zhipin.com/job_detail/`）

**Checkpoint（单独验证）**: 真实页面标记 3 个岗位并取消 1 个；我的岗位库各筛选数量正确；点击一行打开原岗位；链接格式与公司字段结果写入 research.md（FACT / NOT VERIFIED）。

---

## Phase 7: User Story 5 - 浏览列表时岗位自动入库 (Priority: P2)

> 先于 US4 实现：US4 的列表标记依赖列表页 observations 返回的判断数据。

**Goal**: 列表页读到的岗位即使没点开也入库，标记"仅列表信息"，不判断、不花钱。

**Independent Test**: 打开一个 15 条的列表页、不点开任何岗位；`jet stats` 显示这 15 个岗位存在且都是 `list_only`，今日调用 0。

### Tests for User Story 5

- [x] T080 [P] [US5] `tests/api/test_observations_list.py`：用 `search_list.json` 发 `page_type = list` → 15 个岗位、15 条 `views(page_type='list')`、全部 `list_only`、0 条判断、0 次调用（SC-005）；再发一次 → 仍 15 个岗位无重复；`list_only` 岗位再收到 detail → 升级为 `full` 并进入判断；`full` 岗位再从列表读到且三项未变 → 不降级、不新增版本
- [x] T081 [P] [US5] `extension/tests/dedupe.test.js`：把去重逻辑抽成纯函数（`extension/src/dedupe.js`），断言列表只返回本标签页没发过的"ID + 内容摘要"，翻页 / 加载更多只发新 ID；刷新（清空状态）后全部重发

### Implementation for User Story 5

- [x] T082 [US5] 实现 `extension/src/dedupe.js`（按标签页的 `lastSent.list` / `lastSent.detail`），并让 `extension/src/background.js` 的详情去重也改用它
- [x] T083 [US5] 在 `extension/src/background.js` 增加列表读取：`readBossPage().list.ok` 时把新岗位 `POST /v1/observations`（list），保存响应中每个岗位的 `judgement` 供 US4 使用；标签页关闭 / 导航时清理该标签页状态
- [x] T084 [US5] 核对 `src/jet/api/routes.py` 与 `src/jet/domain/jobs.py` 在 `page_type = list` 时不调用 `request_judgement`（FR-017），列表响应中已判断岗位返回其现行判断（含 `stale`），未判断返回 `null`

**Checkpoint（单独验证）**: quickstart R4；如页面有"加载更多"，顺带记录 R5 结果（NOT VERIFIED → 实测）。

---

## Phase 8: User Story 4 - 列表页提醒已判断的岗位 (Priority: P2)

**Goal**: 列表卡片上显示已判断岗位的结论标记；开关关闭时页面零改动，改在侧边栏列出。

**Independent Test**: 在有判断数据的列表页，已判断岗位卡片带标记；关掉开关后刷新，页面 `[data-jet]` 元素为 0，侧边栏列出同样的岗位；本页无已判断岗位时侧边栏显示"本页没有已判断过的岗位"。

### Tests for User Story 4

- [x] T085 [P] [US4] `extension/tests/page-summary.test.js`：把"本页已判断岗位列表"抽成纯函数（`extension/src/page-summary.js`），输入列表岗位 + 判断，输出职位名、结论、判断时间、是否可能过时；数量等于"本页已判断岗位数"指标口径（含可能过时）；空列表 → 空态文案
- [x] T086 [P] [US4] `extension/tests/marks.test.js`：用简易 DOM 替身（不引入第三方库）断言：开关关闭时渲染函数不创建任何元素、并移除所有 `[data-jet]` 元素（SC-006）；开关打开时只给已判断岗位的卡片挂宿主元素

### Implementation for User Story 4

- [x] T087 [US4] 实现 `extension/src/page-summary.js`；SW 处理 `get_page_summary`（按当前标签页返回列表），并在 `render_state` 中带上 `list_marks` 和 `marks_enabled`
- [x] T088 [US4] 在 `extension/src/content.js` 实现列表卡片标记：按 `platform_job_id` 找到卡片，在卡片旁（不在卡片内部修改原有元素）挂 Shadow DOM 宿主（`data-jet`），显示"适合 / 不适合 / 可能过时"小标记；开关关闭时立即移除全部 `[data-jet]` 并停止添加（中途切换也生效）
- [x] T089 [US4] 设置页增加"页面标记"开关（`chrome.storage.local.marksEnabled`，默认 `true`），切换后通知当前 BOSS 标签页；修改 `extension/src/options.html`、`extension/src/options.js`
- [x] T090 [US4] 实现侧边栏 `extension/src/sidepanel.html` / `sidepanel.js`：列出"本页已判断过的岗位"（职位名、结论、判断时间、可能过时）、空态文案、今日剩余次数 / 今日调用次数 / 每日上限（FR-029，数据来自 `/v1/status`）；打开侧边栏只读状态，不触发判断（原则 VI）

**Checkpoint（单独验证）**: quickstart R7；US4 三条验收场景逐条核对。

---

## Phase 9: User Story 6 - 读不到、判断不了时明确告诉我 (Priority: P2)

**Goal**: 各种异常都有明确提示，没有一种显示成"不适合"或"没有岗位"。

**Independent Test**: 分别制造"Jet 未运行""独立详情页""今日额度用完""页面数据结构变化""薪资为空"，每种都看到对应提示。

### Tests for User Story 6

- [x] T091 [P] [US6] `extension/tests/view-state.test.js` 补全：`jet_down`、`unpaired`、`no_profile`、`list_only`、`unrecognized`、`unsupported_page`、`quota_exhausted`、`failed`（带原因）、`interrupted` 的文案和映射全部覆盖；遍历所有非 `done` 输入断言结果从不为 `done_unfit`（SC-004）；`salary_visible = false` 时卡片附加"薪资不可见"
- [x] T092 [P] [US6] `extension/tests/scheduler.test.js`：把"读取结果 → 动作"的决策抽成纯函数（`extension/src/scheduler.js`）：`job_detail_page` → 显示"此页暂不支持读取"、不发 Jet；`ok = false` → "页面无法识别"、不发 Jet、不读页面文字；`captcha_or_blank` → 什么都不做、不显示
- [x] T093 [P] [US6] `tests/api/test_edge_cases.py`：薪资为空入库 → `salary_visible = 0`、判断时不按薪资排除、Judgement 对象 `salary_visible = false`；区域为空 → NULL；Jet 重启（重新 `create_app`）后遗留 `running` → `interrupted`，再次 observe 或 judge 可重新排队；日额度用完时规则结果仍保存

### Implementation for User Story 6

- [x] T094 [US6] 实现 `extension/src/scheduler.js` 并让 `extension/src/background.js` 使用它；独立详情页和"页面无法识别"在页面内（开关打开时）或侧边栏显示对应文案；验证码 / 空白页不做任何事
- [x] T095 [US6] 补全 `extension/src/view-state.js` 全部状态文案与 `salary_visible` 提示，`extension/src/content.js` 与 `extension/src/sidepanel.js` 统一使用
- [x] T096 [US6] Jet 未运行时：SW 连接失败显示"Jet 未运行"，不显示任何结论；不在失败时缓存"已发送"，以便 Jet 启动后再次点开能正常处理（修改 `extension/src/background.js`、`extension/src/dedupe.js`）

**Checkpoint（单独验证）**: quickstart R8、R9、R10；R6（未登录薪资）实测并记录。

---

### US6 追加（2026-09-25 真实页面）

- [x] T096a [US6] 页面刚加载读不到时自动重读（FR-055）：重试 4 次、"正在读取页面…"、4 次失败才"页面无法识别"；纯函数 `shouldRetryRead`；侧边栏同步显示状态
- [x] T096b [US6] 卡片出现速度（FR-056）：变化后最多 0.5 秒读取（150ms 防抖 + 500ms 上限）；已判断岗位先用缓存结论显示；侧边栏三段耗时；Jet 终端请求耗时日志
- [x] T096c [US4] 标签移到卡片边框内左上角的职位名上方留白（FR-054 修订）；删除按卡片间空隙计算的位置逻辑；留白不足按安全规则处理

- [x] T096d [US6] 真实页面修复：刷新后卡在"正在读取页面…"（去重键刷新时未清空 + 重发了读取占位）；侧边栏主动接收状态推送、标签页状态存入会话存储、显示"状态更新于"；复核期间先显示普通判断结论（"复核中，结论可能下调"），Jet 重启时未完成的复核记为失败

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T097 [P] `tests/live/test_eval_smoke.py`（仅 `JET_LIVE=1`）：用 1 条虚构岗位对组合 B 与 D 各真实调用一次，确认 DeepSeek 与 TypeSafe 接口可用、返回可解析（不做质量断言）。（原"对比 deepseek-flash 与 qwen3.8-flash"已被 US7 的 `jet eval run` 取代；用户 2026-09-24 决定不接千问）（2026-10-06：暂不做——需要真实 API Key 并产生费用；pyproject 里的 `live` 标记留给它用）
- [x] T098 [P] `tests/unit/test_ownership.py`：跑一遍完整流程（配对、画像、列表、详情、判断、重新判断）后，所有个人表无主人记录数 = 0（SC-008）（2026-10-06 完成：`tests/unit/test_ownership.py` 走配对、画像、列表、详情、判断、重新判断、状态、HR 实际情况、标注、同意、聊天入库、从严行业、经历素材后，断言 17 张个人数据表没有无主行、user_id 都是配对用户；另有一条反向用例确认能发现无主行；体检第 63 条）
- [x] T099 [P] 更新 `README.md`：安装（`uv sync`）、启动（`uv run jet serve`）、配对、加载插件、跑测试的命令；数据目录与 `.env` 位置说明（2026-10-06 补勾：README 已写明安装、启动、配对、加载插件、跑测试的命令与数据目录 / .env 位置）
- [ ] T100 按 quickstart.md §2–§3 做一次完整真实验证（R1–R10），结果写入 `specs/002-boss-readonly-extension/research.md` §6，标 FACT / NOT VERIFIED **NOT VERIFIED**（截至 2026-10-06 没有真实页面验证记录；体检第 63 条补记）
- [x] T101 确认仓库中没有 `.env`、数据库、令牌、未脱敏页面样本（`git status` + 检查 `tests/fixtures/boss/` 无 `securityId`、`lid`、招聘者姓名）（2026-10-06 补勾：git ls-files 里没有 .env、数据库、令牌；tests/fixtures/boss/ 里没有 securityId、lid、encryptUserId）
- [x] T102 修复 Issue #6：已是"完整信息"的岗位从列表页读到时不再比较、不新建版本，只更新最近读到时间（`src/jet/domain/jobs.py`）；新增测试 T1–T5（`tests/api/test_observations_list.py`）；FR-011 与 data-model"变化判定"同步修改。
- [x] T103 FR-057 重点排查行业：新增规则文件 `src/jet/llm/prompts/strict_industry_rules.json`；`prompt_v5` 读取它并在系统提示词末尾追加从严规则、岗位信息加公司名；判断与复核都把 `jobs.company_name` 传给提示词；不改提示词版本（`src/jet/llm/prompt_v5.py`、`src/jet/worker.py`、`tests/unit/`）〔执行：Antigravity〕
- [x] T104 FR-057 修订：列表与详情读取 `brandIndustry` 存为 `jobs.company_industry`（迁移到 user_version 6，不参与内容指纹）；判断提示词加"公司行业"并优先据此判断重点排查行业；规则文件加 `aliases`（`extension/src/page-reader.js`、`extension/src/background.js`、`src/jet/`、`tests/`）〔执行：Antigravity〕
- [x] T105 FR-058 列表页详情区点「立即沟通」自动标"已投递"（只监听用户点击、ka 核对、可撤销提示）（`extension/src/content.js`、`extension/src/auto-apply.js`、`extension/tests/auto-apply.test.js`）〔执行：Antigravity〕
- [x] T106 FR-059 判断队列初判优先（初判后进先出、复核延后先进先出）；复核推导超过 5 条截断保留（`src/jet/worker.py`、`src/jet/llm/client.py`、`src/jet/llm/prompt_v4.py`、`tests/`）〔执行：Antigravity〕

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup** → **Phase 2 Foundational**（阻塞所有故事）→ 用户故事
- **US1（Phase 3）**：只依赖 Foundational
- **US2（Phase 4）**：依赖 Foundational；判断需要画像，端到端验证需 US1 已完成（单元 / 接口测试可直接写入画像，不依赖 US1 界面）
- **US3（Phase 5）**：依赖 US2（需要已有判断）
- **US7（Phase 6）**：依赖 US2、US3（判断、重新判断、卡片）；迁移任务 T066 先于其他 US7 实现任务
- **US5（Phase 7）**：依赖 Foundational 与 US2 的 `ingest` / `page-reader`
- **US4（Phase 8）**：依赖 US5（列表响应带判断）、US3（`stale`）与 US7（存疑标记）
- **US6（Phase 9）**：依赖 US2、US5 的读取调度；状态文案可与其他故事并行
- **Polish（Phase 10）**：所有故事完成后

### Within Each Phase

- 先写测试并确认失败，再实现
- 数据层（schema / store）→ 领域（domain）→ 接口（routes）→ 插件
- 每个 Checkpoint 通过后再进入下一阶段

### Parallel Opportunities

- Phase 1：T003–T007 可并行（T001、T002 先完成）
- Phase 2：所有测试 T008–T013 可并行；实现中 T017、T018、T022、T023 可并行
- Phase 4：T031–T038 测试可并行；T039、T040、T042、T043 实现可并行
- Phase 6（US7）：T059–T065 测试可并行；T068、T069、T072、T075、T076 实现可并行；Python 与插件两部分可分开委托
- 各故事内标 [P] 的测试任务都可并行

## Parallel Example: User Story 2

```bash
# 测试并行
T032 extension/tests/page-reader.test.js
T033 tests/unit/test_salary.py
T034 tests/unit/test_rules.py
T035 tests/unit/test_quota.py
T036 tests/unit/test_llm_client.py

# 互不依赖的实现并行
T039 extension/src/page-reader.js
T040 src/jet/domain/salary.py
T042 src/jet/domain/rules.py
T043 src/jet/llm/prompt.py
```

## Implementation Strategy

### MVP First（P1：US1 + US2 + US3）

1. Phase 1 Setup → Phase 2 Foundational（能启动、能配对）
2. Phase 3 US1 → 验证画像保存
3. Phase 4 US2 → 验证点开岗位出结论（主线跑通）
4. Phase 5 US3 → 验证不重复花钱、可能过时
5. **停下验证**：按 quickstart R1–R3 真实使用

### Incremental Delivery

6. Phase 6 US7 → 判断质量：事实优先、三档建议、标注与评测（真实页面试用后新增，P1）
7. Phase 7 US5 → 列表入库
8. Phase 8 US4 → 列表标记 / 侧边栏
9. Phase 9 US6 → 全部异常提示
10. Phase 10 → 接口冒烟、完整真实验证

每个阶段单独提交，不破坏前一阶段已验证的行为。
