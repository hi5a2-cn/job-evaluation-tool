# Data Model: 列表页规则粗筛 + 独立职位页被动读取

**Feature**: `004-list-screen-job-detail` | **Date**: 2026-09-29

**数据库结构不变**（仍为 user_version 6，无迁移）。

## 粗筛提示（ScreenHint，不入库）

只在 `POST /v1/observations` 列表分支的响应里出现，插件保存在标签页状态（`listJudgements` 的对应条目）中用于显示；每次列表发送时按当前画像重新计算。

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | 字符串 | `strict_industry` / `salary_floor` / `exclude_keyword` / `excluded_city` |
| `text` | 字符串 | 显示文字：`高风险行业（快消）`、`低于薪资底线`、`命中不接受条件：销售`、`不去的城市` |

- 顺序固定：`strict_industry` → `salary_floor` → `exclude_keyword` → `excluded_city`；同一类可有多条（如命中多个不接受条件词）。
- `strict_industry` 只按用户在设置页勾选的从严行业计算（006 起），一个都没勾就不产生；没有画像时只计算这一项（2026-10-06 修订）。
- 薪资不可见、"面议"、无法解析 → 不产生 `salary_floor`；公司行业为空 → 不产生 `strict_industry`（FR-007）。
- 不写入 `judgements`、不写入任何表、不计入任何额度（FR-005）。

## 列表岗位的临时字段（请求中出现，不入库）

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_labels` | 字符串数组（≤ 20 项，每项 ≤ 30 字） | 列表 `jobLabels`，只用于"不接受条件"匹配 |
| `skills` | 字符串数组（≤ 20 项，每项 ≤ 30 字） | 列表 `skills`，只用于"不接受条件"匹配 |

不参与内容指纹、不写入 `job_versions`（R2）。

## 岗位（jobs，沿用）

| 字段 | 本功能的变化 |
|---|---|
| `company_name` | 新来源：独立职位页的 `company_legal_name`，**只在该岗位没有公司名时写入**（FR-014）；列表页与列表页详情区的"非空即覆盖"不变 |
| `company_industry` | 不变；独立职位页不提供（FR-015） |
| `completeness` | 独立职位页读取成功按 `full` 入库，合并与版本规则与列表页详情区相同 |

## 重点排查行业规则（沿用规则文件）

文件仍为 `src/jet/llm/prompts/strict_industry_rules.json`（`industries` + `aliases`）；读取与匹配函数移至 `src/jet/domain/industry.py`，粗筛与判断提示词共用（R3）。

匹配规则：公司行业（去首尾空格）与某行业名或其同义名**相同**，或**包含**该行业名或同义名 → 命中该行业；多个行业命中时取规则文件中靠前的一个。公司行业未命中或为空时，按行业顺序检查规则文件 `keywords` 中该行业的关键词是否出现在职位名或公司名中，出现即命中（2026-09-29 修订）。
