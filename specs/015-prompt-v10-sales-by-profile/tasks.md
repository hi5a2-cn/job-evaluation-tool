# Tasks: 提示词 v10：销售从严按画像生效 (015-prompt-v10-sales-by-profile)

**Input**: `/specs/015-prompt-v10-sales-by-profile/spec.md`，按 `/specs/014-prompt-version-table/spec.md` 的「加版本的步骤」实施。

**Tests**: 遵循宪法原则 VIII。Antigravity 只运行指定的测试文件；整套测试由 Claude 运行，并排除 `tests/unit/test_config.py::test_data_dir_priority`。

`〔执行：Antigravity〕` 编写代码与测试（Gemini 3.8 Flash (High)，不执行任何 git 命令）；`〔执行：Claude Code〕` 审核 diff、运行测试、Codex 审查、提交。

- [x] T001 新建 `src/jet/llm/prompt_v10.py`：判断「不接受销售」的函数；组装消息（规则 15 按画像取完整版或识别版，简历建议段编号 17）；解析（按画像决定是否执行销售兜底，执行时去掉「改为需要确认」的说明）〔执行：Antigravity〕
- [x] T002 版本表加入 v10（声明解析需要画像信息），默认版本改为 v10，过时基线保持 v9；`client.py` 的通用路径按版本表能力把画像信息传给解析〔执行：Antigravity〕
- [x] T003 测试：v10 提示词文字、两种画像下与 v9 的逐字差别（spec SC-002）、解析兜底（SC-003）、版本表登记、`test_prompt_version_golden.py` 补上 v10 样本〔执行：Antigravity〕
- [x] T004 设置页「不接受关键词」下方的说明（spec FR-005）〔执行：Antigravity〕
- [x] T005 检查点：全量测试；Codex；提交〔执行：Claude Code〕
- [x] T006 文档：`GLOSSARY.md` 补「销售从严」「不接受销售的用户」；README 的判断说明补一句〔执行：Claude Code〕
- [ ] T007 上线前把发给 DeepSeek 的文字变化列给用户确认；合并与重启 jet serve 等用户确认〔执行：Claude Code〕
