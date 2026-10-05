# Tasks: 提示词版本表 (014-prompt-version-table)

**Input**: `/specs/014-prompt-version-table/spec.md`

**Tests**: 遵循宪法原则 VIII（不联网、不需真实 Key、不写真实数据目录、使用 MockTransport）。生产代码不得包含针对测试的特殊处理；已有断言不删不放宽，确需改动的逐条说明。Antigravity 只运行指定的测试文件，整套测试由 Claude 运行，并排除会读取真实数据目录的 `tests/unit/test_config.py::test_data_dir_priority`（体检报告第 21 条）。

`〔执行：Antigravity〕` 编写代码与测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）；`〔执行：Claude Code〕` 审核 diff、运行测试、Codex 审查、提交。

---

## 阶段 1：先固定「发出内容」的样本（在任何重构之前）

- [x] T001 新建 `tests/unit/test_prompt_version_golden.py` 与样本目录 `tests/fixtures/prompt_golden/`：用一组固定的假数据（画像含不接受条件与城市偏好、岗位含公司名 / 公司行业 / BOSS 经验与学历标签 / 薪资、2 份以上简历画像、勾选 2 个从严行业），对 v1–v9 每个版本：(a) 直接调用 `run_llm_judgement`，MockTransport 返回该版本格式的固定响应，记录请求正文（messages、max_tokens、thinking 设置）与返回的 `LlmOutcome` 各字段；(b) 通过 `JudgementWorker` 跑一次完整判断（含一次需要复核的情况），记录保存后的判断行（去掉时间戳和自增 ID）。样本由当前代码生成后写入样本目录；测试运行时重新生成并逐字比对。另加一个仅在显式环境变量下才重写样本的入口，默认关闭〔执行：Antigravity〕
- [x] T002 检查点：审核样本内容覆盖了 v1–v9 与复核分支；全量测试；Codex；提交〔执行：Claude Code〕

## 阶段 2：版本表与单一路径

- [x] T003 新建 `src/jet/llm/versions.py`：版本表（spec FR-001 列出的每项能力）、`DEFAULT_PROMPT_VERSION`、`STALE_METHOD_BASELINE`、评测组合 A 用的具名常量、查询函数（未知版本抛出明确的异常）〔执行：Antigravity〕
- [x] T004 `src/jet/llm/client.py`：`run_llm_judgement` 的组装与解析改为由版本表驱动的单一路径；`max_tokens` 从版本表取。T001 样本测试必须逐字通过〔执行：Antigravity〕
- [x] T005 `src/jet/worker.py`：6 处版本名单改为查询版本表的能力（是否复核、是否保存事实与推导、是否保存 hr_questions、是否保存一句话理由）〔执行：Antigravity〕
- [x] T006 `src/jet/config.py`：默认版本引用版本表；`load_settings` 遇到不在版本表里的版本抛出 `ValueError` 并列出可用版本；新增对应测试〔执行：Antigravity〕
- [x] T007 检查点：样本测试逐字通过；全量测试；搜索 `client.py`、`worker.py` 里的版本号字面量为 0；Codex；提交〔执行：Claude Code〕

## 阶段 3：去掉不起作用的版本参数与分散的默认值

- [x] T008 `src/jet/domain/judgements.py`：过时基线引用版本表；去掉 `is_method_changed`、`staleness`、`to_api` 等函数里不参与判断的 `current_prompt_version` 参数〔执行：Antigravity〕
- [x] T009 `src/jet/api/routes.py`、`src/jet/domain/job_status.py`：去掉 10 处取版本与一路传递的参数，以及 `_job_entry` / `list_my_jobs` 的默认值 `"v5"`〔执行：Antigravity〕
- [x] T010 `src/jet/eval/runner.py`：组合 A 的 `"v1"` 改为引用版本表常量〔执行：Antigravity〕
- [x] T011 测试整理：调用处去掉 `current_prompt_version` 实参；「默认版本是多少」的断言改为引用版本表；改名或补全名字与内容不符的测试（spec SC-004）。每处改动在报告里逐条列出〔执行：Antigravity〕
- [x] T012 检查点：样本测试逐字通过；全量测试；SC-003 搜索为 0；Codex；提交〔执行：Claude Code〕

## 阶段 4：文档与收尾

- [x] T013 `GLOSSARY.md`：新建，收录提示词版本、默认版本、过时基线、版本表四个术语〔执行：Claude Code〕
- [x] T014 README 的「配置说明」：`JET_PROMPT_VERSION` 写错时会报错并列出可用版本；示例里不再写死版本号（与体检第 11 条一起处理）〔执行：Antigravity〕
- [x] T015 检查点：全量测试；把本文件的任务勾上；提交；合并到 main 与重启 jet serve 前等用户确认〔执行：Claude Code〕
