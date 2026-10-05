# Research & Codebase Analysis: 我的简历：上传 PDF + AI 简历画像

**Feature**: `009-resume-pdf`
**Date**: 2026-09-30
**Branch**: `009-resume-pdf`

---

## 1. 现状调研与代码事实（FACT / INFERENCE / HYPOTHESIS）

### S1: 数据库现有结构与 v9 迁移设计（D1, D2）

- **FACT [代码事实] (`src/jet/db/schema.sql:248-256`, `src/jet/db/migrations.py:1215-1236`)**：
  - 当前数据库版本为 8（`LATEST_VERSION = 8`）；
  - `resume_slots` 当前结构为：
    ```sql
    CREATE TABLE IF NOT EXISTS resume_slots (
        user_id TEXT NOT NULL,
        slot INTEGER NOT NULL CHECK(slot IN (1, 2, 3)),
        name TEXT NOT NULL,
        job_types TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, slot),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    ```
  - `llm_calls.purpose` 的 CHECK 约束当前定义为：
    `CHECK(purpose IN ('judge', 'eval', 'assist', 'prejudge'))`；
  - `user_settings` 当前定义为：
    ```sql
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT PRIMARY KEY,
        daily_llm_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_llm_limit BETWEEN 0 AND 500),
        daily_assist_limit INTEGER NOT NULL DEFAULT 50 CHECK(daily_assist_limit BETWEEN 0 AND 500),
        daily_prejudge_limit INTEGER NOT NULL DEFAULT 20 CHECK(daily_prejudge_limit BETWEEN 0 AND 200),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    ```
- **FACT [代码事实] (`src/jet/db/migrations.py:1150-1230`)**：
  SQLite 不支持通过 `ALTER TABLE` 直接重命名列或修改已有 `CHECK` 约束。在 `migrate_to_v8` 中，标准迁移步骤包含：
  1. WAL checkpoint，并在 `data/` 下备份 `jet.db.bak-<timestamp>-v8`；
  2. 新连接关闭外键检查（`PRAGMA foreign_keys=OFF`）；
  3. `BEGIN IMMEDIATE` 事务中统计迁移前各表行数；
  4. 重建修改表、数据拷贝、外键一致性校验（`PRAGMA foreign_key_check`）；
  5. 更新 `PRAGMA user_version = 9`，提交事务。
- **INFERENCE [关键推论]**：
  - 迁移至 v9（`migrate_to_v9`）的设计：
    1. **重建 `resume_slots`**：
       ```sql
       CREATE TABLE resume_slots_new (
           user_id TEXT NOT NULL,
           slot INTEGER NOT NULL CHECK(slot IN (1, 2, 3)),
           name TEXT NOT NULL CHECK(length(name) <= 100),
           resume_text TEXT,
           profile TEXT NOT NULL CHECK(length(profile) <= 300),
           updated_at TEXT NOT NULL,
           PRIMARY KEY(user_id, slot),
           FOREIGN KEY (user_id) REFERENCES users(id)
       );
       INSERT INTO resume_slots_new (user_id, slot, name, resume_text, profile, updated_at)
       SELECT user_id, slot, name, NULL, job_types, updated_at FROM resume_slots;
       DROP TABLE resume_slots;
       ALTER TABLE resume_slots_new RENAME TO resume_slots;
       ```
       原有旧数据中的 `job_types` 平滑迁移为新字段 `profile`，旧记录的 `resume_text` 设为 `NULL`，行数完全一致。
    2. **重建 `llm_calls` 表**：
       扩展 CHECK 约束为 `CHECK(purpose IN ('judge', 'eval', 'assist', 'prejudge', 'resume_profile'))`，全量拷贝历史调用数据。
    3. **扩展 `user_settings` 表**：
       执行 `ALTER TABLE user_settings ADD COLUMN daily_resume_profile_limit INTEGER NOT NULL DEFAULT 10 CHECK(daily_resume_profile_limit BETWEEN 0 AND 50);`。
    4. 同步更新 `src/jet/db/schema.sql`、`src/jet/db/store.py`（`_ensure_current_schema` 与 `init_db` 版本分支）及 `migrations.py` 的 `LATEST_VERSION = 9`。

---

### S2: 纯 Python PDF 文本提取库选型与事实（D1）

- **FACT [代码事实] (`pyproject.toml:23-28`, `uv.lock`)**：
  项目已正式引入 `pypdf = ">=6.19.0"` 依赖。
- **FACT [技术特性对比]**：
  - `pypdf`: 纯 Python 实现、无任何 C 扩展编译要求、BSD 许可、跨平台友好，朋友安装使用无需在系统安装 Poppler 或构建工具链；
  - `PyMuPDF (fitz)`: 解析性能极高，但采用 AGPL 许可证，且底层依赖 C 库，对于轻量本机助手容易引发分发与许可冲突；
  - `pdfminer.six`: 纯 Python，但 API 设计繁复、提取速度慢且维护不够活跃。
- **INFERENCE [关键推论]**：
  在服务端解析时，采用内存流操作：
  ```python
  import io
  import pypdf

  reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
  if reader.is_encrypted:
      raise PdfInvalidError("PDF 文件已加密，无法解析")
  extracted_pages = [page.extract_text() or "" for page in reader.pages]
  full_text = "\n".join(extracted_pages).strip()
  if len("".join(full_text.split())) < 50:
      raise NoTextError("无法读取文字，请上传文字版 PDF")
  ```
  纯内存完成读取与校验，异常直接向 API 抛出特定错误。

---

### S3: 脱敏机制复用与姓名三来源识别机制（D3）

- **FACT [代码事实] (`src/jet/llm/sanitize.py:45-130`)**：
  - `sanitize.py` 已提供 `sanitize_text(text, sensitive_terms)` 以及 `get_sensitive_name_terms(hr_name, user_name)`；
  - 已支持邮箱（`EMAIL_PATTERN`）、中国手机号（`PHONE_PATTERN`）、微信（`WXID_PATTERN`, `WECHAT_PATTERN`）的正则表达式掩码；
  - 目前缺少针对 18 位身份证号码的正则匹配。
- **INFERENCE [关键推论]**：
  1. **增补身份证号码正则**：
     在 `sanitize.py` 中新增 `ID_CARD_PATTERN = re.compile(r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")`，并在 `sanitize_text` 中执行占位替换；
  2. **姓名三来源抽取策略**：
     - 来源 A (`users.display_name`)：从当前用户表中读取（若存在且非空）；
     - 来源 B (`self_name`)：从上传请求体中获取用户可选填写的姓名；
     - 来源 C (PDF 规则提取)：
       - 规则 1：正则匹配 `re.search(r"姓名[:：]\s*(\S{2,4})", full_text)`；
       - 规则 2：提取非空首行内容，若为 2–4 个汉字（`^[\u4e00-\u9fa5]{2,4}$`）或 2–3 个首字母大写的英文单词（`^[A-Z][a-z]+(\s+[A-Z][a-z]+){1,2}$`），提取为姓名。
  3. **三来源合并与强脱敏安全阻断**：
     收集到的非空姓名合并去重后，统一构造敏感词集合（含姓氏+尊称组合，如"李先生"、"李经理"）。
     若三来源合并后的姓名列表为空：**坚决不调用大模型**，立即返回 422 错误 `{error: "self_name_required", message: "没有识别出你的姓名，请在上传区填写姓名后重试（只在本机用于去掉姓名）"}`。

---

### S4: 提示词工程与非思考模型画像提炼（D4, D5）

- **FACT [代码事实] (`src/jet/llm/client.py:64-95`, `src/jet/config.py`)**：
  系统支持通过 `call_once` 调用 OpenAI 兼容的 DeepSeek 接口。大模型引擎默认为非思考引擎 `settings.judge_engine`（如 `deepseek-flash:no-think`）。
- **INFERENCE [关键推论]**：
  新建模块 `src/jet/llm/resume_profile.py`：
  - 系统提示词：设定资深职业顾问角色，要求仅依据脱敏后的简历正文，提炼适合的岗位方向及 3～5 条核心亮点；
  - 格式规范：
    ```text
    适合方向：<岗位方向>
    亮点：
    1. <亮点1>
    2. <亮点2>
    3. <亮点3>
    ```
  - 约束要求：
    - 总字数严格限制在 300 汉字以内；
    - 仅输出纯文本，不包含 Markdown 大标题，不编造未提及的经历；
  - 输入边界：仅传递脱敏后的简历文本，严禁传递文件名、简历槽位名称、用户标识；
  - 保护策略：日志中严禁打印完整的 Prompt 与大模型输出文本，仅记录 Token 消耗量与字符数。

---

### S5: 岗位判断提示词集成（D5, D6）

- **FACT [代码事实] (`src/jet/llm/prompt_v6.py:25-59`, `src/jet/llm/client.py:183-193`)**：
  当前 `prompt_v6.build_resume_instruction` 生成逻辑为：
  ```python
  for r in resumes:
      slot = r.get("slot")
      jt = str(r.get("job_types") or "").strip()
      lines.append(f"- 简历{slot}：适合岗位类型：{jt}")
  ```
  在岗位判断时，仅在有效简历数 ≥ 2 份时才向模型追加该提示段落。
- **INFERENCE [关键推论]**：
  - 修改 `build_resume_instruction` 中的组装逻辑，将原"适合岗位类型"替换为"画像"：
    ```python
    for r in resumes:
        slot = r.get("slot")
        pf = str(r.get("profile") or "").strip()
        lines.append(f"- 简历{slot}：{pf}")
    ```
  - 保持 `PROMPT_VERSION = "v6"` 不变，理由：
    - 提示词段落骨架与 JSON 输出模式（`resume_suggestion` 包含 slot 与 reason）完全兼容；
    - 若升级到 v7，会导致此前已完成正式判断的所有岗位被系统标记为“已过时（stale）”，引发大量重复重判请求，违反原则 VII 成本可控要求；
  - `client.py` 及相关领域函数从 `resume_slots` 读取数据时，统一将读取字段由 `job_types` 改为 `profile`。

---

### S6: 内存流处理与零磁盘残留安全设计（D2）

- **FACT [宪法原则 VIII]**：
  “个人资料、Cookie、Token、配对密钥、数据库不进 Git。数据目录是可注入的参数，代码中不写死真实数据路径。测试写入真实数据目录即判为失败。”
- **INFERENCE [关键推论]**：
  PDF 原件的处理必须做到：
  1. 插件端通过 `FileReader.readAsDataURL` 转换为 Base64 字符串随 HTTP 传输；
  2. 服务端在 FastAPI Controller 中使用 `base64.b64decode` 解码得到 bytes；
  3. `pypdf.PdfReader` 直接基于 `io.BytesIO(pdf_bytes)` 在内存中流式解析；
  4. 提取出文本并脱敏后，`pdf_bytes` 随函数作用域退出而被垃圾回收；
  5. 数据库仅落库 `resume_text`（提取的文本）与 `profile`（生成的画像文本），严禁落库任何二进制 BLOB；
  6. 数据目录（如 `data/`）绝不创建任何临时 `.pdf` 文件。

---

### S7: 设置页交互与双处说明文字更新（D7）

- **FACT [代码事实] (`extension/src/options.html:276-308`, `extension/src/options.js:875-1120`)**：
  - `options.html` 中存在两处提示文字：
    1. `#resume-section .hint-text`；
    2. `#strict-industry-section .hint-text`；
    原文字均为："简历的'适合的岗位类型'和勾选的从严行业会随岗位判断发给 DeepSeek；简历名称不会发送。"
- **INFERENCE [关键推论]**：
  - 两处说明文字统一原样替换为：
    "上传时，简历文字会在本机去掉姓名、手机号、邮箱等联系方式后发送给 DeepSeek 生成简历画像；之后岗位判断时只发送画像（编号为简历1/2/3，不发送简历名称）。本机只保存提取出的文字和画像，不保存 PDF 文件。"
  - 界面每个 slot 改造为：
    - 文件选择控件 `<input type="file" accept="application/pdf">`；
    - 选定文件后自动截取文件名（去掉 `.pdf`）并填充至名称输入框；
    - "上传并生成画像"操作按钮；
    - 画像多行文本域，绑定实时字数统计 `<div class="char-count">已输入 x / 300</div>`；
    - "保存修改"按钮与"删除"按钮；
  - 顶部上传辅助区提供可选输入框："你的姓名（只在本机用于去掉姓名，不发送）"，保存在前端内存，上传时一并携带给本机 Jet。

---

## 2. 关键决定及理由（D1–D8）

### D1: PDF 选型确定为 pypdf，保持纯 Python 与 BSD 友好分发

- **决定**：采用 `pypdf`（版本 6.19.0）作为唯一的 PDF 文本提取引擎。
- **理由**：
  - 纯 Python 架构，无需系统级 C 编译依赖或动态库支持，保障未来将 Jet 打包给朋友使用时免除复杂的环境配置痛苦；
  - BSD 宽松开源协议，完全避免 AGPL 带来的强传染许可风险；
  - 仅用于提取纯文本，不需要渲染页面图像，`pypdf` 性能完全满足单页或多页简历的瞬时提取。

### D2: PDF 字节零落盘、零数据库持久化，仅保存提取文字与画像

- **决定**：PDF 文件的二进制内容仅在服务端的内存缓冲（`io.BytesIO`）中暂存解析，解析完成后立即丢弃；磁盘与数据库中绝对不保存 PDF 原文件。
- **理由**：
  - 恪守宪法原则 VIII（数据安全）：PDF 简历文件包含排版、版式元数据等大量非结构化信息，易产生数据留存隐患；
  - 岗位推荐决策仅需结构化的文字与画像摘要，持久化存储原始 PDF 文件会无谓膨胀本地数据库体积并带来文件管理碎片；
  - 用户删除简历时，删除数据库记录即代表该简历在本机的彻底销毁，逻辑清爽。

### D3: 姓名三来源兜底，无法脱敏姓名时坚决阻断外发

- **决定**：综合系统已有姓名、用户上传区主动输入姓名与 PDF 规则匹配提取三项来源；若合并后仍拿不到任何候选姓名，服务端直接返回 422 阻断大模型调用。
- **理由**：
  - 真实简历中求职者姓名位置多样，单一正则存在漏网风险；
  - 姓名是个人隐私中最核心的锚点，一旦姓名未经脱敏外发给外部大模型，将造成重大隐私穿透；
  - 阻断外发并提示求职者在上传区补充姓名，属于完全可控的友好人机协作，将隐私保护做到百分之百可靠。

### D4: 简历画像生成设立独立配额，不挤占岗位判断

- **决定**：在 `llm_calls` 与 `quota.py` 中为简历画像分配独立的 `purpose='resume_profile'`，由 `user_settings.daily_resume_profile_limit`（默认 10 次，0–50）单独约束。
- **理由**：
  - 严格遵守宪法原则 VII：“每一种花钱的调用都有独立的数量上限和预算配置，不与其他数量共用”；
  - 简历上传通常为单次偶发行为，若共用每日岗位判断额度，可能会因多次尝试上传而意外消耗正常的日常求职浏览预算。

### D5: 岗位判断仅发送画像，不发全文、不发简历名称，沿用编号 1/2/3

- **决定**：在岗位判断提示词中，仅发送 `- 简历{slot}：{profile}`，严格剔除简历名称，绝不附带简历原始全文。
- **理由**：
  - 保护隐私：简历名称常包含求职者真实意向或个人标签，不发名称彻底切断个人身份暴露通道；
  - 节省 Token：完整简历往往长达数千字，若每次判断都携带多份简历全文，Prompt 上下文将迅速膨胀数十倍，造成巨额 Token 浪费；300 字画像提炼了核心技术栈与亮点，信息密度极高，兼顾推荐精准度与低调用成本。

### D6: 保持 prompt_version 为 v6 不升级，保障既有判断有效性

- **决定**：保留 `PROMPT_VERSION = "v6"`，不因简历提示词内容由“适合岗位类型”升级为“画像”而自增版本号。
- **理由**：
  - Jet 的缓存体系会依据 `prompt_version` 判定历史判断是否过时（stale）；若升为 v7，用户此前浏览并判断过的所有岗位卡片都将变成过时状态，重新打开时会触发大面积重新判断，产生不必要的调用费用；
  - 006 引入 v6 的核心是支持多简历建议的 JSON 输出结构（`resume_suggestion`），本次变更仅优化了提示词中输入的简历侧重点内容，输出格式未作任何改变，完全向下兼容。

### D7: 额度耗尽或缺 Key 时允许降级保存简历文字，保留断点重试能力

- **决定**：在额度用尽（429）或未配置 API Key（409）时，服务端依然持久化保存用户上传的 `name` 与提取的 `resume_text`，保留原画像或置空，并返回特定状态码提示用户。
- **理由**：
  - 避免重复劳动：PDF 上传与文字提取是重资产操作，保存已提取的文本后，用户后续可在额度刷新或配置 Key 后一键点击“重新生成”，无需再次寻找本地 PDF 上传；
  - 保证系统健壮性，用户随时可通过手动填写画像的方式直接生效。

### D8: 虚构人物测试规范，强阻断真实个人信息进 Git

- **决定**：所有自动化测试用例、夹具、模拟响应及文档，统统采用虚构人物（如“李明”，手机号 `13800000000`，邮箱 `liming@example.com`，虚构身份证 `110101199001011234`）。
- **理由**：
  - 遵循宪法原则 VIII 及项目全局规定，防范真实个人信息泄露到 Git 历史中；
  - 保证单元测试具有确定性、幂等性与跨设备可复现性。
