# 大模型输入与输出契约 (Prompt v6)

> **2026-10-06 补记（体检第 61 条）：简历部分已被 009 取代。** 009 起简历在设置页上传 PDF：上传时把**脱敏后的简历正文（最多 8000 字）**发给 DeepSeek 生成「简历画像」，判断时发送画像（编号简历 1/2/3，不发简历名称）。本文件里「不发送任何简历内容」「只发编号与适合的岗位类型」的说法已不成立，发送内容以 `specs/009-resume-pdf/` 为准；模型只输出简历编号 `slot` 和理由，接口返回的简历建议为 `{slot, name, reason}`（`name` 由本机按编号填回），不再有方向代号和文件名；文中提到的 `resume_directions.json` 已删除。

> **2026-09-29 修订**：简历方向不再放在仓库配置文件 `resume_directions.json`，改为设置页"我的简历"（本机数据库 `resume_slots`，编号 1–3，只发编号与适合的岗位类型）；从严行业改为设置页勾选（`strict_industry_selection`，迁移 v7）。本文件中涉及 `resume_directions.json` 与方向代号（`ai_ops` 等）的设计已被 `tasks.md`"2026-09-29 修订"一节取代，以该节与 `spec.md` 为准。


**Feature**: `006-resume-suggestion` | **Date**: 2026-09-29

本契约定义提示词升级至 `v6` 后发往外部大模型（DeepSeek）的内容差异、模型期望输出的 JSON 结构规范及后端的校验容错规则。

---

## 一、发往外部模型的内容变更全量清单（D8 专项审计）

对照 `prompt_v5.py` 与 `prompt_v6.py`，本次升级中发往大模型的实际内容变动如下：

### 1. System Prompt 新增说明段

在原有的 13 条判定规则尾部追加第 14 条说明（当且仅当配置文件 `resume_directions.json` 存在且有效方向数 $\ge 2$ 时追加）：

```text
14. 简历方向建议：
候选人准备了以下几个不同侧重点的简历方向：
- ai_ops：AI / 运营（适合岗位类型：AI 产品运营、内容运营、平台和用户运营、AIGC、AI 训练师）
- data_support：数据 / 技术支持（适合岗位类型：数据分析、数据管理、临床数据、实施、技术支持、数据标注和质检）
- animal_life：动物 / 生命科学（适合岗位类型：宠物、实验动物和动物手术支持、生物医药 / CRO、农牧科技企业的技术或数据岗）

请根据岗位实际日常职责，从上述方向代号（ai_ops / data_support / animal_life）中挑选一个最适合投递的简历方向，并在 resume_suggestion 中给出代号和一句推荐理由（≤ 40 字）。即使结论不是 apply，只要该岗位有投递价值或方向大体对口，也请给出最贴近的简历方向；若方向完全不相干或岗位有严重风险，仍选一个相对最接近的方向并在理由中注明。
```

同时，在 System Prompt 尾部的 JSON 输出示例中，新增 `"resume_suggestion"` 字段说明：
```json
{
  "facts": { ... },
  "verdict": "apply|try|check|skip",
  "derivation": [ ... ],
  "verdict_reason": "一句话理由，≤ 40 字",
  "hr_questions": [ ... ],
  "resume_suggestion": {
    "direction": "ai_ops|data_support|animal_life",
    "reason": "一句话推荐理由，≤ 40 字"
  }
}
```

### 2. User Prompt 变动

**完全无任何变动**。用户画像（`【候选人画像】`）与岗位详情（`【岗位信息】`）各字段及格式与 `v5` 完全一致。

### 3. 数据安全与隐私审查（SC-004）

- **绝对不发送简历文件名**：配置文件中的 `file_name`（`简历A.pdf`、`简历B.pdf`、`简历C.pdf`）仅留存于本机后端，**绝对不拼入任何 Prompt**。
- **绝对不发送求职者姓名**：发往大模型的提示词中出现的简历方向文本仅为客观行业与职责描述（如"AI / 运营"、"数据分析"），不含用户真实姓名（"（用户姓名）"）。
- **零额外调用**：在现有判断调用的同一次输出中要求模型给出简历建议，大模型调用次数保持为 1 次，调用预算零增加。

---

## 二、大模型输出 JSON 规范 (v6)

### 1. 完整输出示例

```json
{
  "facts": {
    "summary": {
      "text": "负责AI绘画产品的用户社群运营与提示词效果测试",
      "quotes": ["负责社群日常互动与AIGC工具测试"]
    },
    "work_type": {
      "value": "运营",
      "subtype": "用户运营",
      "secondary": [
        {"category": "数据与技术", "subtype": "AI 相关"}
      ],
      "quotes": ["负责社群日常互动与AIGC工具测试"]
    },
    "sales_level": {
      "value": "低",
      "signals": []
    },
    "experience": {
      "requirement": "1-3年运营经验",
      "requirement_type": "硬性",
      "value": "满足",
      "gap": ""
    },
    "work_intensity": {
      "value": "双休",
      "quotes": ["周末双休"]
    },
    "risk_signals": []
  },
  "verdict": "apply",
  "derivation": [
    "岗位职责契合用户运营偏好，且具备AI相关工具实践",
    "双休且无销售指标压力"
  ],
  "verdict_reason": "职责符合AI用户运营方向，双休无销售压力",
  "hr_questions": [],
  "resume_suggestion": {
    "direction": "ai_ops",
    "reason": "岗位工作以大模型提示词设计和用户运营为主，高度契合该方向"
  }
}
```

### 2. `resume_suggestion` 字段详细约束

| 字段 | 类型 | 必需 | 约束与说明 |
|---|---|---|---|
| `direction` | 字符串 | 是 | 必须严格为当前配置中声明的 `key` 之一（例如 `"ai_ops"`、`"data_support"`、`"animal_life"`） |
| `reason` | 字符串 | 是 | 推荐理由，纯中文一句话说明，不可为空字符串，建议 $\le 40$ 字符 |

---

## 三、后端的校验、截断与容错规则 (`parse_facts`)

在 `src/jet/llm/prompt_v6.py` 的 `parse_facts` 中，对大模型输出进行后处理：

1. **JSON 结构完整性**：
   - 若模型未输出 `resume_suggestion`，或该字段不是 JSON 对象（dict）：
     - 不抛 `ParseError`（避免因非核心字段导致整条判断失败）；
     - 将解析出的 `resume_suggestion` 置为 `None`。
2. **方向代号（`direction`）校验**：
   - 提取 `resume_suggestion.get("direction")`，去除首尾空白；
   - 检查 `direction` 是否处于从 `read_resume_directions()` 加载的合法 `valid_keys` 集合中；
   - 若不在配置中（包括模型胡乱生成了新词、返回了空串、或给出了多个方向）：
     - 判定该建议无效，解析结果置为 `None`，不入库。
3. **推荐理由（`reason`）校验与截断**：
   - 提取 `resume_suggestion.get("reason")`，去除首尾空白；
   - 若理由为空串或非字符串：判定无效，置为 `None`；
   - 若理由长度超过 40 字：截断保留前 40 字符（`reason = reason[:40]`），与 `verdict_reason` 保持同级精炼。
4. **结论互斥处理**：
   - 若模型最终给出的 `verdict == "skip"`：
     - 后端 Worker 仍可如实将 `resume_direction` 与 `resume_reason` 写入数据库；
     - **但在 API 返回与前端展示层做强行屏蔽**：只要 `verdict == "skip"`，前端卡片与侧边栏一律不予显示，避免出现"不建议投，但建议投这份简历"的逻辑矛盾。
