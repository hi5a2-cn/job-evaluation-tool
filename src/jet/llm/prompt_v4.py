import json
import re
from typing import Any, Mapping

from jet.domain import taxonomy
from jet.domain.taxonomy import CATEGORIES, VERDICTS, WORK_INTENSITY
from jet.llm import prompt_v2
from jet.llm.prompt import ParseError
from jet.llm.prompt_v2 import VALID_EXPERIENCE_VALUES, VALID_SALES_LEVELS
from jet.llm.prompt_v3 import VALID_REQUIREMENT_TYPES

PROMPT_VERSION = "v4"

VALID_RISK_TYPES = {"非法金融", "诈骗", "传销", "收费入职", "按交易量提成", "名实不符", "其他"}

# 风险信号兜底把结论改成不建议投时，一句话理由的前缀（后接第一条风险信号的描述）
RISK_FALLBACK_REASON_PREFIX = "有风险："

SYSTEM_PROMPT = """你是一个专业的求职决策分析专家。在评估岗位与候选人的匹配度时，你必须遵循以下原则：

1. 先事实后建议：先客观提取并判定岗位的事实属性，再基于这些事实与候选人画像对照得出建议。
2. 依据职责而非职位名：招聘职位名常有夸大或修饰，必须严格以职位描述中的日常工作职责为准。
3. 专门识别变相销售信号：仔细排查以下变相销售信号清单（包括但不限于：对接客户或渠道、商务拓展、业绩指标、营销推广或活动策划、获客转化、维护客户关系、陪同拜访）。先逐条摘出命中上述信号的原句，再综合判定销售与客户对接成分以及工作类型。职位名好听但实际职责以营销推广、对接客户、完成业绩为主的，一律按销售处理。
   - 销售与客户对接成分：高 = 对接客户或渠道、商务拓展、业绩指标、获客转化中至少一项是主要职责；中 = 有这类职责但只占一部分（如"配合商务""协助对接客户"）；低 = 基本不涉及。
   - 工作类型两层分类（大类 → 细分）：
     * 数据与技术：数据分析 / 数据处理与标注 / 技术支持与实施 / 开发与测试 / AI 相关
     * 运营：用户运营 / 内容运营 / 产品运营 / 活动运营 / 电商运营 / 新媒体与社群
     * 产品与项目：产品经理或助理 / 项目管理
     * 内容与设计：编辑与文案 / 设计与视频
     * 市场与销售：市场营销与品牌 / 销售与商务拓展 / 客服与客户成功
     * 科研与专业：实验与研发 / 质检 / 动物医学相关 / 农牧生产
     * 职能：行政 / 人事 / 财务
     * 其他（无细分）
   - 主要类型与次要类型判定：
     * work_type.value 为主要类型的大类，必须为上述 8 个大类之一，代表日常花时间最多的一类。
     * work_type.subtype 为主要类型的细分，尽量填入上述对应细分，若没有合适细分填 null；大类为"其他"时细分必须为 null。
     * work_type.secondary 为次要类型（0–3 项），每项为 {"category": 大类, "subtype": 细分或 null}，不含主要类型。岗位职责很多是混合的，岗位混合时必须填次要类型；没有则填空数组 []。
     * 以策划营销活动、推广获客为主属于"市场与销售"（细分"市场营销与品牌"）；以用户、内容、活动的日常运转和数据跟进为主属于"运营"。
   - 对照画像时，"工作内容偏好"中明确不想做的方向优先于关键词匹配；不能因为职位名或关键词命中就判推荐投递。
4. 原句引用要求：所有 quote 必须逐字逐句摘自职位描述原文，严禁任何形式的改写或编造；如果在原文中找不到依据，绝对不要编造，直接留空数组 []。
5. 经验门槛要求：
   - 经验要求必须区分 requirement_type：
     * 硬性（"要求""须""必须""至少"等，没有"优先"字样）
     * 优先（"优先""加分""者佳"等）
     * 未提及
   - 对照候选人提供的"我的背景"（学历、工作年限、主要经历），客观判定候选人是否满足岗位的经验门槛（满足 / 差一点 / 不满足 / 无法判断），若不满足或差一点，必须在 gap 中明确说明差距。
   - "优先"项不满足时 value 最多"差一点"，不得判"不满足"；硬性要求才可能"不满足"。
   - 描述里没有经验要求时，requirement_type="未提及"、value="满足"、requirement=null。
6. 工作强度判定（五档之一）：
   - 高强度：996、长期加班、休息不固定等
   - 单休：做六休一
   - 大小周：一周单休一周双休循环
   - 双休：明确双休或不加班
   - 未提及：职位描述中未提及工作时间或工作强度
7. 风险信号排查（risk_signals）：仔细排查是否存在疑似诈骗、传销、非法金融（期货、荐股、虚拟币、杀猪盘类）、收费入职或培训贷、提成按客户交易量 / 手数 / 入金计算、职位名与职责严重不符、门槛极低却承诺高收入、年龄限定过窄等风险。每条包含：
   - type（风险类型，必须为以下之一：非法金融 / 诈骗 / 传销 / 收费入职 / 按交易量提成 / 名实不符 / 其他）
   - description（一句话说明风险）
   - quote（职位描述原文原句，不可编造）
   没有风险信号时必须填空数组 []。命中任一风险信号时，verdict 必须为 skip，verdict_reason 必须说明风险。
8. 建议四档及含义：
   - apply（适合投递）：方向对口、硬性条件满足、与偏好和底线无明显冲突。
   - try（可以一试）：方向大体对口，但有差距（如经验差一点、部分偏好不完全吻合）。
   - check（需要确认）：信息不足或前后矛盾，投之前需要问清楚。
   - skip（不建议投）：与偏好或底线明确冲突，或命中风险信号。
9. 建议问 HR 的问题（hr_questions）：
   - 当 verdict 为 try 或 check 时，提供 2–3 个问题（每个 ≤ 60 字），针对该职位描述中模糊或可能有坑的地方（如实际对接客户的比例、是否有业绩指标、经验要求能否放宽）。
   - 其他结论（apply 或 skip）时必须填空数组 []。
10. 推导过程与一句话理由：
   - 给出 1–5 条明确的推导说明，每条必须明确指明哪项岗位事实与候选人画像的哪一条规则一致或冲突。
   - 顶层给出一句话理由 verdict_reason（≤ 40 字），用于卡片最上方的结论摘要行，讲清最核心的一致或冲突理由。
11. 格式约束：严格只输出合法 JSON，不要包含任何 markdown 代码块或解释文字。

输出 JSON 格式规范：
{
  "facts": {
    "summary": {
      "text": "一两句白话概括该岗位每天实际做什么",
      "quotes": ["原句"]
    },
    "work_type": {
      "value": "8个大类之一",
      "subtype": "细分名或 null",
      "secondary": [
        {"category": "大类", "subtype": "细分名或 null"}
      ],
      "quotes": ["原句"]
    },
    "sales_level": {
      "value": "高|中|低",
      "signals": [
        {"signal": "命中信号名（如：对接客户或渠道）", "quote": "原句"}
      ]
    },
    "experience": {
      "requirement": "经验要求的原文原句，无则为 null",
      "requirement_type": "硬性|优先|未提及",
      "value": "满足|差一点|不满足|无法判断",
      "gap": "差距说明，满足时为空字符串 \"\""
    },
    "work_intensity": {
      "value": "高强度|单休|大小周|双休|未提及",
      "quotes": ["原句"]
    },
    "risk_signals": [
      {
        "type": "非法金融|诈骗|传销|收费入职|按交易量提成|名实不符|其他",
        "description": "一句话说明",
        "quote": "原句"
      }
    ]
  },
  "verdict": "apply|try|check|skip",
  "derivation": [
    "推导理由1",
    "推导理由2"
  ],
  "verdict_reason": "一句话理由，≤ 40 字",
  "hr_questions": [
    "建议问HR的问题1（≤ 60 字）",
    "建议问HR的问题2（≤ 60 字）"
  ]
}
"""


def build_messages(
    profile: Mapping[str, Any],
    job_version: Mapping[str, Any],
    known_facts: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build messages array with prompt v4 system prompt, profile details, and job details."""
    msgs = prompt_v2.build_messages(profile, job_version, known_facts=known_facts)
    msgs[0] = {"role": "system", "content": SYSTEM_PROMPT}
    return msgs


def parse_facts(
    text: str,
    truncate_derivation: bool = False,
) -> tuple[dict[str, Any], str, list[str], str, list[str]]:
    """
    Parse and validate LLM output into (facts, verdict, derivation, verdict_reason, hr_questions) for v4 prompt.

    Raises ParseError on any schema or enum violation.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ParseError("大模型返回内容为空")

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ParseError(f"大模型返回无法解析为 JSON: {e}") from e

    if not isinstance(data, dict):
        raise ParseError("返回内容不是 JSON 对象")

    # 1. facts validation
    facts = data.get("facts")
    if not isinstance(facts, dict):
        raise ParseError("缺失 facts 字段或 facts 不是对象")

    # 1.1 summary
    summary = facts.get("summary")
    if not isinstance(summary, dict) or "text" not in summary:
        raise ParseError("facts.summary 缺失或不是对象")
    summary_text = str(summary.get("text") or "").strip()
    raw_summary_quotes = summary.get("quotes")
    if raw_summary_quotes is None:
        raw_summary_quotes = []
    elif not isinstance(raw_summary_quotes, list):
        raise ParseError("facts.summary.quotes 必须是列表")
    summary_quotes = [str(q).strip() for q in raw_summary_quotes if str(q).strip()][:3]

    # 1.2 work_type
    work_type = facts.get("work_type")
    if not isinstance(work_type, dict):
        raise ParseError("facts.work_type 缺失或不是对象")
    wt_val = work_type.get("value")
    if wt_val not in CATEGORIES:
        raise ParseError(f"facts.work_type.value '{wt_val}' 不在合法大类枚举中: {list(CATEGORIES.keys())}")

    wt_sub = work_type.get("subtype")
    if isinstance(wt_sub, str) and not wt_sub.strip():
        # 没有细分应输出 null；模型偶尔输出空字符串，按 null 处理，不让整次判断失败
        wt_sub = None
    if wt_sub is not None:
        if not isinstance(wt_sub, str):
            raise ParseError("facts.work_type.subtype 必须是字符串或 null")
        if not taxonomy.is_valid_pair(wt_val, wt_sub):
            raise ParseError(f"facts.work_type.subtype '{wt_sub}' 不属于大类 '{wt_val}'")

    raw_sec = work_type.get("secondary")
    try:
        normalized_sec = taxonomy.normalize_secondary(raw_sec, primary_category=wt_val, primary_subtype=wt_sub)
    except ValueError as e:
        raise ParseError(f"facts.work_type.secondary 校验失败: {e}") from e

    raw_wt_quotes = work_type.get("quotes")
    if raw_wt_quotes is None:
        raw_wt_quotes = []
    elif not isinstance(raw_wt_quotes, list):
        raise ParseError("facts.work_type.quotes 必须是列表")
    wt_quotes = [str(q).strip() for q in raw_wt_quotes if str(q).strip()][:3]

    # 1.3 sales_level
    sales_level = facts.get("sales_level")
    if not isinstance(sales_level, dict):
        raise ParseError("facts.sales_level 缺失或不是对象")
    sl_val = sales_level.get("value")
    if sl_val not in VALID_SALES_LEVELS:
        raise ParseError(f"facts.sales_level.value '{sl_val}' 不在合法枚举中: {VALID_SALES_LEVELS}")
    raw_signals = sales_level.get("signals")
    if raw_signals is None:
        raw_signals = []
    elif not isinstance(raw_signals, list):
        raise ParseError("facts.sales_level.signals 必须是列表")
    signals = []
    for s in raw_signals:
        if isinstance(s, dict):
            sig_name = str(s.get("signal") or "").strip()
            quote_val = s.get("quote")
            quote_str = quote_val if isinstance(quote_val, str) else str(quote_val or "")
            signals.append({"signal": sig_name, "quote": quote_str.strip()})
    signals = signals[:6]

    # 1.4 experience
    experience = facts.get("experience")
    if not isinstance(experience, dict):
        raise ParseError("facts.experience 缺失或不是对象")
    req_type = experience.get("requirement_type")
    if req_type not in VALID_REQUIREMENT_TYPES:
        raise ParseError(f"facts.experience.requirement_type '{req_type}' 不在合法枚举中: {VALID_REQUIREMENT_TYPES}")
    exp_val = experience.get("value")
    if exp_val not in VALID_EXPERIENCE_VALUES:
        raise ParseError(f"facts.experience.value '{exp_val}' 不在合法枚举中: {VALID_EXPERIENCE_VALUES}")
    req_val = experience.get("requirement")
    exp_req = str(req_val).strip() if req_val is not None else None
    exp_gap = str(experience.get("gap") or "").strip()

    # Code enforcement for experience
    if req_type == "优先" and exp_val == "不满足":
        exp_val = "差一点"
        if not exp_gap.startswith("（优先项）"):
            exp_gap = f"（优先项）{exp_gap}"
    elif req_type == "未提及" and exp_val in ("不满足", "差一点"):
        exp_val = "满足"
        exp_gap = ""

    # 1.5 work_intensity
    work_intensity = facts.get("work_intensity")
    if not isinstance(work_intensity, dict):
        raise ParseError("facts.work_intensity 缺失或不是对象")
    wi_val = work_intensity.get("value")
    if wi_val not in WORK_INTENSITY:
        raise ParseError(f"facts.work_intensity.value '{wi_val}' 不在合法枚举中: {WORK_INTENSITY}")
    raw_wi_quotes = work_intensity.get("quotes")
    if raw_wi_quotes is None:
        raw_wi_quotes = []
    elif not isinstance(raw_wi_quotes, list):
        raise ParseError("facts.work_intensity.quotes 必须是列表")
    wi_quotes = [str(q).strip() for q in raw_wi_quotes if str(q).strip()][:3]

    # 1.6 risk_signals
    raw_risks = facts.get("risk_signals")
    if raw_risks is None:
        raw_risks = []
    elif not isinstance(raw_risks, list):
        raise ParseError("facts.risk_signals 必须是列表")

    parsed_risk_signals = []
    for r in raw_risks:
        if not isinstance(r, dict):
            raise ParseError("facts.risk_signals 项必须是对象")
        r_type = r.get("type")
        if r_type not in VALID_RISK_TYPES:
            raise ParseError(f"facts.risk_signals.type '{r_type}' 不在合法枚举中: {list(VALID_RISK_TYPES)}")
        desc = r.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise ParseError("facts.risk_signals.description 缺失或不是非空字符串")
        if "quote" not in r:
            raise ParseError("facts.risk_signals.quote 字段缺失")
        q_val = r.get("quote")
        q_str = q_val["text"] if isinstance(q_val, dict) else str(q_val or "")
        parsed_risk_signals.append({
            "type": r_type,
            "description": desc.strip(),
            "quote": q_str.strip(),
        })

    parsed_facts = {
        "summary": {"text": summary_text, "quotes": summary_quotes},
        "work_type": {
            "value": wt_val,
            "subtype": wt_sub,
            "secondary": normalized_sec,
            "quotes": wt_quotes,
        },
        "sales_level": {"value": sl_val, "signals": signals},
        "experience": {
            "requirement": exp_req,
            "requirement_type": req_type,
            "value": exp_val,
            "gap": exp_gap,
        },
        "work_intensity": {"value": wi_val, "quotes": wi_quotes},
        "risk_signals": parsed_risk_signals,
    }

    # 2. verdict
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise ParseError(f"verdict '{verdict}' 不在合法枚举中: {VERDICTS}")

    # 3. derivation
    derivation = data.get("derivation")
    if truncate_derivation:
        if not isinstance(derivation, list) or len(derivation) < 1:
            raise ParseError("derivation 必须是包含 1 到 5 条字符串的列表")
        derivation = derivation[:5]
    else:
        if not isinstance(derivation, list) or not (1 <= len(derivation) <= 5):
            raise ParseError("derivation 必须是包含 1 到 5 条字符串的列表")

    parsed_derivation = []
    for d in derivation:
        if not isinstance(d, str) or not d.strip():
            raise ParseError("derivation 项必须是非空字符串")
        parsed_derivation.append(d.strip())

    # Code fallback: risk_signals non-empty and verdict != skip -> skip
    risk_fallback = bool(parsed_risk_signals) and verdict != "skip"
    if risk_fallback:
        verdict = "skip"
        parsed_derivation.insert(0, "命中风险信号，结论改为不建议投")
        if len(parsed_derivation) > 5:
            parsed_derivation = parsed_derivation[:5]

    # 4. verdict_reason
    verdict_reason = data.get("verdict_reason")
    if not isinstance(verdict_reason, str) or not verdict_reason.strip():
        raise ParseError("缺失 verdict_reason 字段或不是非空字符串")
    verdict_reason = verdict_reason.strip()
    if risk_fallback:
        # 结论被代码改成不建议投时，模型原来的理由可能是正面的，换成第一条风险信号
        verdict_reason = f"{RISK_FALLBACK_REASON_PREFIX}{parsed_risk_signals[0]['description']}"
    if len(verdict_reason) > 40:
        verdict_reason = verdict_reason[:40]

    # 5. hr_questions
    raw_hr_questions = data.get("hr_questions")
    if not isinstance(raw_hr_questions, list):
        parsed_hr_questions = []
    else:
        parsed_hr_questions = []
        for q in raw_hr_questions:
            q_str = str(q or "").strip()
            if q_str:
                if len(q_str) > 60:
                    q_str = q_str[:60]
                parsed_hr_questions.append(q_str)
        parsed_hr_questions = parsed_hr_questions[:3]

    if verdict not in ("try", "check"):
        parsed_hr_questions = []

    return parsed_facts, verdict, parsed_derivation, verdict_reason, parsed_hr_questions
