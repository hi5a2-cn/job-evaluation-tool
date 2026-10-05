"""HR Communication Assistant orchestration, prompt assembly, and response verification.

Specs:
- specs/003-hr-assistant/spec.md (FR-011-FR-015, FR-018, FR-031, FR-032, FR-034, FR-035, SC-005, R1, R2)
- specs/003-hr-assistant/contracts/local-api.md (Section 1 & 5)
- specs/003-hr-assistant/tasks.md (T015, T016)
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import unicodedata

import httpx

from jet.config import Settings
from jet.llm.client import ParseError, call_once
from jet.llm.sanitize import (
    SelfNameUnavailable,
    compute_prompt_hash,
    get_sensitive_name_terms,
    sanitize_chat_messages,
    sanitize_text,
)

DEFAULT_TONE_RULES_PATH = Path(__file__).resolve().parent / "prompts" / "tone_rules.txt"
DEFAULT_SELF_FACT_RULES_PATH = Path(__file__).resolve().parent / "prompts" / "self_fact_rules.json"
DEFAULT_COMPLETED_ACTION_RULES_PATH = Path(__file__).resolve().parent / "prompts" / "completed_action_rules.json"
DEFAULT_TONE_FIX_RULES_PATH = Path(__file__).resolve().parent / "prompts" / "tone_fix_rules.json"
DEFAULT_RESUME_SENT_RULES_PATH = Path(__file__).resolve().parent / "prompts" / "resume_sent_rules.json"


def read_resume_sent_rules(rules_path: Path | str | None = None) -> dict[str, Any]:
    """从独立规则文件中实时动态读取简历已发送后的话术判定规则，确保修改即时生效 (FR-064)。"""
    path = Path(rules_path) if rules_path else DEFAULT_RESUME_SENT_RULES_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_tone_fix_rules(rules_path: Path | str | None = None) -> dict[str, Any]:
    """从独立规则文件中实时动态读取口语词后语气修正规则，确保修改即时生效 (SC-013)。"""
    path = Path(rules_path) if rules_path else DEFAULT_TONE_FIX_RULES_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def apply_tone_fix(
    text: str,
    rules: Mapping[str, Any] | None = None,
    rules_path: Path | str | None = None,
) -> str:
    """口语词后接提问的语气兜底改写：若以口语词开头紧跟逗号后直接是提问且中间没有承接，插入承接词 (SC-013)。"""
    clean_text = text.strip() if text else ""
    if not clean_text:
        return clean_text

    if rules is None:
        rules = read_tone_fix_rules(rules_path=rules_path)
    if not rules:
        return clean_text

    colloquial_words = rules.get("colloquial_words") or []
    bridge_words = rules.get("bridge_words") or []
    question_features = rules.get("question_features") or []
    question_endings = rules.get("question_endings") or []
    insert_bridge = rules.get("insert_bridge", "了解了。那")

    # 1. 检查是否以口语词开头紧跟逗号（优先匹配最长口语词）
    matched_cw: str | None = None
    comma: str = "，"
    rest: str | None = None

    for cw in sorted(colloquial_words, key=len, reverse=True):
        if clean_text.startswith(cw + "，"):
            matched_cw = cw
            comma = "，"
            rest = clean_text[len(cw) + 1:].lstrip()
            break
        elif clean_text.startswith(cw + ","):
            matched_cw = cw
            comma = "，"
            rest = clean_text[len(cw) + 1:].lstrip()
            break

    if not matched_cw or rest is None:
        return clean_text

    # 若开头匹配到的口语词本身就在承接词中，不进行改写，直接原样返回
    if matched_cw in bridge_words:
        return clean_text

    # 2. 检查中间是否已有承接词
    has_bridge = any(bw in rest for bw in bridge_words)
    if has_bridge:
        return clean_text

    # 3. 检查紧跟逗号后是否直接是提问（句首直接发问，而非先陈述其它内容）
    first_clause = re.split(r"[。！!]", rest)[0].strip()
    is_question = (
        any(first_clause.startswith(feat) for feat in question_features)
        or any(first_clause.endswith(end) for end in question_endings)
    )
    if not is_question:
        return clean_text

    rewritten = f"{matched_cw}{comma}{insert_bridge}{rest}".strip()
    if len(rewritten) > 50:
        return clean_text
    return rewritten


def read_completed_action_rules(rules_path: Path | str | None = None) -> list[dict[str, Any]]:
    """从独立规则文件中实时动态读取完成声称判定规则，确保修改即时生效 (FR-064)。"""
    path = Path(rules_path) if rules_path else DEFAULT_COMPLETED_ACTION_RULES_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []



def read_self_fact_rules(rules_path: Path | str | None = None) -> list[dict[str, Any]]:
    """从独立规则文件中实时动态读取关于'我'的事实判定规则，确保修改即时生效 (FR-054)。"""
    path = Path(rules_path) if rules_path else DEFAULT_SELF_FACT_RULES_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def is_fact_specified_in_experiences(
    rule: Mapping[str, Any],
    experiences: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """检查经历素材是否已写明该类事实。若经历素材为空或未包含任何指定关键词，返回 False。"""
    if not experiences:
        return False
    exp_texts: list[str] = []
    for exp in experiences:
        if isinstance(exp, Mapping):
            exp_texts.append(str(exp.get("content") or ""))
        else:
            exp_texts.append(str(exp))
    full_text = " ".join(exp_texts)
    experience_keywords = rule.get("experience_keywords", [])
    return any(kw in full_text for kw in experience_keywords)


def is_fact_known(
    rule: Mapping[str, Any],
    experiences: Sequence[Mapping[str, Any]] | None,
    current_city: str = "",
) -> bool:
    """检查事实是否已知：所在城市在画像 current_city 非空时视为已知，其余类别仍按经历素材判断。"""
    if rule.get("name") == "所在城市":
        if current_city and current_city.strip():
            return True
    return is_fact_specified_in_experiences(rule, experiences)


_NON_CITY_KEYWORDS = {"常住", "现居", "居住在", "人在", "坐标", "所在地", "目前在"}


def rule_city_names(
    rule: Mapping[str, Any],
    location_name: str = "",
    current_city: str = "",
) -> set[str]:
    """所在城市规则用到的城市名（去掉'市'/'区'后缀）：规则词表里的城市 + 本岗位城市 + 用户目前所在城市。"""
    cities: set[str] = set()
    candidates = [c for c in rule.get("experience_keywords", []) if c not in _NON_CITY_KEYWORDS]
    for c in [*candidates, location_name, current_city]:
        c_clean = str(c or "").strip().removesuffix("市").removesuffix("区")
        if len(c_clean) >= 2:
            cities.add(c_clean)
    return cities


def city_regex(cities: set[str]) -> str:
    """把城市名拼成正则选择分支，长的在前，避免短名先匹配。"""
    return "|".join(re.escape(c) for c in sorted(cities, key=len, reverse=True))


def expand_self_claim_patterns(rule: Mapping[str, Any], location_name: str = "") -> list[str]:
    """把陈述句式里的 {cities} 占位换成和识别 HR 提问相同的城市名单。"""
    patterns = [str(p) for p in rule.get("self_claim_patterns", [])]
    if not any("{cities}" in p for p in patterns):
        return patterns
    cities = rule_city_names(rule, location_name=location_name)
    if not cities:
        return [p for p in patterns if "{cities}" not in p]
    part = city_regex(cities)
    return [p.replace("{cities}", part) for p in patterns]


def is_city_question(
    text: str,
    rule: Mapping[str, Any],
    location_name: str = "",
    current_city: str = "",
) -> bool:
    """识别 HR 消息是否在询问所在城市，用正则覆盖常见问法，避免宽泛规则误伤。"""
    if not text:
        return False

    # 1. 直接命中 rule 中配置的问题关键词
    for kw in rule.get("hr_question_keywords", []):
        if kw in text:
            return True

    # 2. 候选城市：规则列表 + 本岗位城市 + 用户目前所在城市
    cities = rule_city_names(rule, location_name=location_name, current_city=current_city)

    if cities:
        city_regex_part = city_regex(cities)
        # 针对具体城市的提问，如 "你在成都吗"、"现在在成都么"、"人在成都不"、"在成都吗"
        pattern_specific_city = (
            rf"(?:你|您)?(?:目前|现在)?(?:是|人在|住在|居住在|定居在|在)\s*(?:{city_regex_part})(?:市|区)?(?:吗|么|嘛|不|呢|\?|？)"
        )
        if re.search(pattern_specific_city, text):
            return True

        pattern_person_in_city = rf"人在\s*(?:{city_regex_part})(?:市|区)?(?:不|吗|么|嘛)"
        if re.search(pattern_person_in_city, text):
            return True

    # 3. 通用地点/城市询问，如 "你现在人在哪"、"人在哪里"、"在哪个城市"、"在本地吗"、"在当地吗"、"人在这边吗"、"是本地人吗"
    general_patterns = [
        r"(?:你|您|人)(?:目前|现在)?(?:人)?(?:在|住)(?:(?:哪个城市|什么城市|哪座城市)(?:呢|\?|？)?|(?:哪儿|哪里|哪)(?=[呢？\?。！!，\s]|$))",
        r"(?:哪个城市|什么城市|哪座城市)",
        r"(?:你|您)?(?:目前|现在)?(?:人在|住在|在)\s*(?:本地|当地|这边)(?:吗|么|嘛|不|呢|\?|？)",
        r"(?:你|您)?(?:是|属于)?(?:本地人|当地人)(?:吗|么|嘛|不|呢|\?|？)",
        r"人在这边吗",
    ]
    for pat in general_patterns:
        if re.search(pat, text):
            return True

    return False


def is_resume_already_sent(sanitized_messages: Sequence[Mapping[str, Any]]) -> bool:
    """检查清洗后的消息历史中是否包含已发送或已查看附件简历标记。"""
    for msg in sanitized_messages:
        text = str(msg.get("text") or "")
        if "[已发送附件简历]" in text or "[HR 已查看附件简历]" in text:
            return True
    return False


def get_unhandled_request_cards(
    sanitized_messages: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """获取我最后一条消息之后出现的未处理请求卡片，简历已发送时简历卡片视为已处理。"""
    last_self_idx = -1
    for idx, msg in enumerate(sanitized_messages):
        if msg.get("sender") == "我":
            last_self_idx = idx

    unhandled: list[Mapping[str, Any]] = []
    for idx, msg in enumerate(sanitized_messages):
        if idx > last_self_idx and msg.get("is_request_card"):
            card_text = str(msg.get("text") or "").strip()
            # 若为要简历卡片，且后续出现了简历已发送/已查看标记，视为已处理
            if "简历" in card_text:
                if any(
                    "[已发送附件简历]" in str(sub.get("text") or "")
                    or "[HR 已查看附件简历]" in str(sub.get("text") or "")
                    for sub in sanitized_messages[idx + 1 :]
                ):
                    continue
            unhandled.append(msg)
    return unhandled


def matches_self_claim(text: str, patterns: Sequence[str]) -> bool:
    """判断话术文本中是否包含该类事实的第一人称陈述句式。"""
    for pat in patterns:
        try:
            if re.search(pat, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def determine_unanswered_facts(
    sanitized_messages: Sequence[Mapping[str, Any]],
    experiences: Sequence[Mapping[str, Any]] | None,
    rules: Sequence[Mapping[str, Any]] | None = None,
    rules_path: Path | str | None = None,
    current_city: str = "",
    location_name: str = "",
) -> list[dict[str, str]]:
    """根据最近的 HR 消息是否命中某类提问关键词且事实未知，生成 unanswered_facts (FR-054)。"""
    if rules is None:
        rules = read_self_fact_rules(rules_path=rules_path)

    # 提取最近的 HR 消息文本（取自用户最后一条消息之后的所有 HR 消息，若无用户消息则取所有 HR 消息）
    last_self_idx = -1
    for idx, msg in enumerate(sanitized_messages):
        if msg.get("sender") == "我":
            last_self_idx = idx

    recent_hr_messages = [
        msg for idx, msg in enumerate(sanitized_messages)
        if idx > last_self_idx and msg.get("sender") == "HR"
    ]
    if not recent_hr_messages:
        return []

    recent_hr_text = " ".join(str(m.get("text") or "") for m in recent_hr_messages)

    unanswered: list[dict[str, str]] = []
    for rule in rules:
        name = rule.get("name", "")
        # 1. 检查最近 HR 消息是否命中提问关键词 / 正则
        if name == "所在城市":
            if not is_city_question(recent_hr_text, rule, location_name=location_name, current_city=current_city):
                continue
        else:
            hr_keywords = rule.get("hr_question_keywords", [])
            if not any(kw in recent_hr_text for kw in hr_keywords):
                continue

        # 2. 检查事实是否已知（所在城市在 current_city 非空时视为已知）
        if is_fact_known(rule, experiences, current_city=current_city):
            continue

        unanswered.append({
            "name": name,
            "notice": f"HR 问了{name}，你的资料里没有，这部分请你自己回答",
        })

    return unanswered


def determine_request_notice(
    sanitized_messages: Sequence[Mapping[str, Any]],
) -> str | None:
    """根据消息顺序判断 HR 请求卡片是否出现在我最后一条消息之后，且未被处理 (FR-055)。"""
    unhandled = get_unhandled_request_cards(sanitized_messages)
    if not unhandled:
        return None

    last_card = unhandled[-1]
    card_text = str(last_card.get("text") or "").strip()
    card_kind = last_card.get("card_kind")
    if card_kind == "location_confirm" or "工作地点" in card_text:
        return f'HR 发了地点确认（{card_text}），需要你在 BOSS 里点"可以接受"或"暂不考虑"。话术按可以接受来写，不接受的话请自己回复'

    return f"HR 发了请求（{card_text}），需要你在 BOSS 里点同意或拒绝。话术按你同意来写，不同意的话请自己回复"


MODE_LABELS: dict[str, str] = {
    "opening": "开场白",
    "reply": "建议回复",
    "waiting_hr": "正在等 HR 回复",
}

VERDICT_LABELS: dict[str, str] = {
    "apply": "适合",
    "try": "可以一试",
    "check": "需要核实",
    "skip": "不适合",
    "fit": "适合",
    "unsure": "需要核实",
    "unfit": "不适合",
}

ASSIST_PREVIEW_FIELDS: list[str] = [
    "职位名、公司、城市",
    "Jet 判断结论与风险信号（如有）",
    "HR 实际情况（如有，已脱敏）",
    "最近已加载的非系统聊天消息（已脱敏）",
    "我的经历素材（已录入条目）",
    "语气规则（8 条）",
]


class AssistError(Exception):
    """Base exception for assist module."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


class AllSuggestionsDroppedError(AssistError):
    """Raised when all suggestions, questions, and notices are empty."""

    def __init__(
        self,
        message: str = "这次没有可用的建议，可以再点一次生成",
        usage: Mapping[str, Any] | None = None,
        dropped_summary: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__("all_suggestions_dropped", message)
        self.usage = usage
        self.dropped_summary = list(dropped_summary or [])


class HashMismatchError(AssistError):
    """Raised when client prompt hash does not match server computed hash."""

    def __init__(self, message: str = "发送内容哈希不一致，已被安全拦截") -> None:
        super().__init__("hash_mismatch", message)


def normalize_question(q: str | None) -> str:
    """Normalize question text for deduplication: NFKC, collapse whitespace, strip punctuation."""
    if not q:
        return ""
    normalized = unicodedata.normalize("NFKC", str(q))
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return re.sub(r"[?？.。!！\s]+$", "", collapsed).strip()


def determine_communication_stage(
    sanitized_messages: Sequence[Mapping[str, Any]],
    force_mode: str | None = None,
) -> tuple[str, str, str]:
    """依据沟通历史自动判定沟通阶段（开场白/建议回复/等待HR回复）并返回阶段标识、中文标签与依据消息，支持手动覆盖。"""
    hr_messages = [m for m in sanitized_messages if m.get("sender") == "HR"]
    non_system = [m for m in sanitized_messages if m.get("sender") in ("HR", "我")]

    if not hr_messages:
        # 没有 HR 消息 → opening
        auto_mode = "opening"
        if non_system:
            last = non_system[-1]
            basis = f"{last.get('sender', '我')}：{last.get('text', '')}"
        elif sanitized_messages:
            last = sanitized_messages[-1]
            basis = f"{last.get('sender', '我')}：{last.get('text', '')}"
        else:
            basis = "HR尚未回复"
    else:
        last = non_system[-1] if non_system else sanitized_messages[-1]
        if last.get("sender") == "HR":
            # 最后一条是 HR → reply
            auto_mode = "reply"
            basis = f"HR：{last.get('text', '')}"
        else:
            # 最后一条是我且之前 HR 说过话 → waiting_hr
            auto_mode = "waiting_hr"
            basis = f"我：{last.get('text', '')}"

    if force_mode in ("opening", "reply"):
        effective_mode = force_mode
    else:
        effective_mode = auto_mode

    label = MODE_LABELS.get(effective_mode, effective_mode)
    return effective_mode, label, basis


def read_tone_rules(rules_path: Path | str | None = None) -> str:
    """从独立规则文件中实时动态读取语气规则文本，确保修改即时生效。"""
    path = Path(rules_path) if rules_path else DEFAULT_TONE_RULES_PATH
    if not path.exists():
        raise FileNotFoundError(f"语气规则文件不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


def format_chat_message_line(msg: Mapping[str, Any], location_name: str = "") -> str:
    """格式化单条聊天记录行，支持引用回复上下文显示与地点确认卡片 (FR-073)。"""
    sender = msg.get("sender", "我")
    text = msg.get("text", "")
    quote_ctx = msg.get("quote_context")

    if sender == "系统":
        return f"系统：{text}"

    if msg.get("is_request_card"):
        card_kind = msg.get("card_kind")
        if card_kind == "location_confirm" or "工作地点" in str(text):
            loc = location_name.strip() or "该城市"
            return f"【HR 地点确认卡片】HR：{text}（岗位城市：{loc}；BOSS 按钮：暂不考虑 / 可以接受）"
        prefix = "【HR 请求卡片】"
    else:
        prefix = ""

    if quote_ctx is not None and isinstance(quote_ctx, Mapping):
        q_from = quote_ctx.get("from")
        q_text = quote_ctx.get("text")
        if q_from is not None and q_text is not None:
            if q_from == sender:
                target = "自己"
            elif q_from == "我":
                target = "我"
            elif q_from == "HR":
                target = "HR"
            else:
                target = str(q_from)
            sender_part = f"{sender}（回复{target}：'{q_text}'）"
        else:
            sender_part = f"{sender}（回复了一条较早的消息）"
    else:
        sender_part = sender

    return f"{prefix}{sender_part}：{text}"


def build_assist_prompt(
    job_title: str,
    company_name: str,
    location_name: str,
    sanitized_messages: Sequence[Mapping[str, Any]],
    experiences: Sequence[Mapping[str, Any]],
    tone_rules: str,
    judgement: Mapping[str, Any] | None = None,
    hr_note: str | None = None,
    mode: str = "reply",
    mode_basis: str = "",
    unanswered_facts: Sequence[Mapping[str, str]] | None = None,
    rules_path: Path | str | None = None,
    current_city: str = "",
) -> str:
    """组装包含岗位基本信息、Jet判断结论、HR实际情况、脱敏消息、我的资料、编号经历素材与语气规则的完整提示词文本。"""
    if unanswered_facts is None:
        unanswered_facts = determine_unanswered_facts(
            sanitized_messages=sanitized_messages,
            experiences=experiences,
            rules_path=rules_path,
            current_city=current_city,
            location_name=location_name,
        )

    lines: list[str] = [
        "【岗位信息】",
        f"职位：{job_title}",
        f"公司：{company_name}",
        f"城市：{location_name}",
        "岗位以【岗位信息】为准；聊天中出现的 HR 本人职务（如部门、经理、主管等）是 HR 的职务，不是这个岗位。",
        "",
    ]

    # Jet 判断 (R1)
    if judgement is not None:
        lines.append("【Jet 判断结论与风险】")
        raw_verdict = judgement.get("verdict") or ""
        verdict_str = VERDICT_LABELS.get(raw_verdict, raw_verdict)
        lines.append(f"结论：{verdict_str}")

        facts = judgement.get("facts")
        if isinstance(facts, str):
            try:
                facts = json.loads(facts)
            except Exception:
                facts = {}
        elif not isinstance(facts, dict):
            facts = {}

        summary = judgement.get("summary") or facts.get("summary")
        if isinstance(summary, dict):
            summary_text = summary.get("text", "")
        else:
            summary_text = str(summary or "")
        if summary_text:
            lines.append(f"白话职责概括：{summary_text}")

        sales = judgement.get("sales_level") or facts.get("sales_level")
        if isinstance(sales, dict):
            sales_val = sales.get("value", "")
        else:
            sales_val = str(sales or "")
        if sales_val:
            lines.append(f"销售与客户对接成分：{sales_val}")

        raw_risks = judgement.get("risk_signals") or facts.get("risk_signals") or []
        risk_strs: list[str] = []
        for r in raw_risks:
            if isinstance(r, dict):
                desc = r.get("description") or r.get("signal") or ""
                rtype = r.get("type", "")
                risk_strs.append(f"{rtype}（{desc}）" if rtype and desc else (desc or rtype))
            else:
                risk_strs.append(str(r))
        if risk_strs:
            lines.append(f"命中的风险信号：{'；'.join(risk_strs)}")
        else:
            lines.append("命中的风险信号：无")

        raw_hr_q = judgement.get("hr_questions")
        if isinstance(raw_hr_q, str):
            try:
                raw_hr_q = json.loads(raw_hr_q)
            except Exception:
                raw_hr_q = []
        elif not isinstance(raw_hr_q, list):
            raw_hr_q = []
        if raw_hr_q:
            lines.append(f"建议问 HR 的问题：{'；'.join(str(q) for q in raw_hr_q)}")
        else:
            lines.append("建议问 HR 的问题：无")
        lines.append("")
    else:
        # FR-011: 无 Jet 判断时标注并要求提问确认岗位性质
        lines.extend([
            "【Jet 判断】",
            "无 Jet 判断（该岗位未经过适合度判断）。如果聊天中还没弄清岗位的主要职责或是否含销售成分，请在问题中带上一个确认岗位性质的问题；聊天或 HR 实际情况中已经说明过，或岗位明显不是销售类的，不要再问。",
            "",
        ])

    # HR 实际情况
    if hr_note and hr_note.strip():
        lines.extend([
            "【HR 实际情况】",
            hr_note.strip(),
            "",
        ])

    # 聊天记录
    lines.append("【聊天记录】")
    if sanitized_messages:
        for msg in sanitized_messages:
            lines.append(format_chat_message_line(msg, location_name=location_name))
    else:
        lines.append("（暂无历史消息）")
    lines.append("")

    # 我的资料
    if current_city and current_city.strip():
        lines.extend([
            "【我的资料】",
            f"目前所在城市：{current_city.strip()}",
            "",
        ])

    # 经历素材
    lines.append("【经历素材】")
    if experiences:
        for exp in experiences:
            lines.append(f"[{exp.get('item_no')}] {exp.get('content')}")
    else:
        lines.append("无（没有录入经历素材。请勿虚构任何经历，生成 2 个都不提及具体经历的版本，并将 referenced_experience_ids 设为 []）")
    lines.append("")

    # 语气规则
    lines.extend([
        "【语气规则】",
        tone_rules.strip(),
        "",
    ])

    # 生成要求
    mode_label = MODE_LABELS.get(mode, mode)
    lines.extend([
        "【生成要求】",
        f"沟通阶段：{mode_label}（{mode}）",
    ])
    if mode_basis:
        lines.append(f"依据消息：{mode_basis}")

    if mode == "waiting_hr":
        lines.append("- 当前状态为我方最后发言且正在等 HR 回复。请勿生成建议回复话术（suggestions 设为 []），仅生成最多 3 个针对当前岗位和沟通进展建议问 HR 的问题。")
    else:
        lines.extend([
            "- 每条话术字数必须在 50 字以内（严格限制 ≤ 50 字）。",
            "- 请生成 2 个不同语气版本的话术（version 1: 自然直接，version 2: 沉稳专业）。",
            "- 严格遵循上述语气规则（称呼用'您'、公司用'贵公司'、每条最多 1 问、敏感问题先铺垫、严禁反问句质疑、不使用客套长句、不夸大个人背景）。",
            "- 严格遵循真实性原则（FR-054）：话术中关于'我'的事实（包括但不限于所在城市、期望薪资、到岗时间、出差或外派等）必须严格来自【我的资料】或【经历素材】；若未写明，必须使用【填写：类别名】占位，严禁编造作答。",
            "- HR 问到\"我\"的事实（所在城市、期望薪资、到岗时间、出差或外派等）时，话术必须正面回应，不得绕开：【我的资料】或【经历素材】里有的直接用该值回答；没有的，在话术里放醒目占位，格式严格为【填写：类别名】，例如【填写：所在城市】【填写：到岗时间】【填写：期望薪资】【填写：出差或外派】，由用户自己替换。",
        ])

        if unanswered_facts:
            names = "、".join(str(f.get("name") or "") for f in unanswered_facts if f.get("name"))
            if names:
                lines.append(
                    f"- 本次 HR 问到你资料里没有的事实（{names}），话术必须正面回应，用【填写：类别名】占位，不得绕开。"
                )

        resume_sent = is_resume_already_sent(sanitized_messages)
        if resume_sent:
            lines.append(
                "- 附件简历已经发给 HR（HR 已查看时也一样），话术不得再说要发简历、不得再问是否需要简历。"
            )

        unhandled_cards = get_unhandled_request_cards(sanitized_messages)
        if unhandled_cards:
            last_card = unhandled_cards[-1]
            card_text = str(last_card.get("text") or "").strip()
            card_kind = last_card.get("card_kind")
            if card_kind == "location_confirm" or "工作地点" in card_text:
                loc = location_name.strip() or "该城市"
                lines.append(
                    f'- 严格回应HR地点确认卡片：话术必须明确回应能否接受在{loc}工作，按"可以接受"来写，例如"工作地点在{loc}我可以接受"。'
                )
            elif "简历" in card_text:
                if not resume_sent:
                    lines.append(
                        '- 严格回应HR请求卡片：按同意来写，只说将要发简历，例如"好的，我这就把简历发您"，不得声称已发送。'
                    )
            elif "微信" in card_text or "电话" in card_text or "手机" in card_text:
                lines.append(
                    '- 严格回应HR交换联系方式请求：按同意来写，例如"好的，可以加微信沟通"/"好的，方便电话沟通"，不得编造微信号或电话号码，不得声称已添加。'
                )
            else:
                lines.append(
                    '- 严格回应HR请求卡片：只写将要做的动作，按同意来写，不得声称已完成。'
                )
        else:
            lines.append(
                "- 聊天里没有需要回应的请求，话术不要主动提出发简历、加微信、留电话这类动作。"
            )

        lines.extend([
            "- 仅能引用上述【经历素材】中已有的条目，并在 referenced_experience_ids 中列出引用的编号（如 [1]）；严禁捏造素材中未提及的经历。若无合适经历可引用，生成不提具体经历的版本，referenced_experience_ids 设为 []。",
            "- 先列出 HR 在聊天中已经明确的信息（known_facts，每条 ≤ 20 字，最多 8 条），再写话术和问题；已列入的内容不得再问，也不得换个说法再问。",
            "- 提出最多 3 个建议问 HR 的问题。提问前逐条核对聊天：HR 已经回答过的内容都视为已回答，不得再问，也不得换个说法再问，话术里同样不要再问。已回答包括：HR 用\"都有的\"\"是的\"\"可以\"\"对\"等简短词回应我的提问（表示我列出的选项都成立或得到肯定）；HR 主动说明过的内容（如职责、转正、晋升、薪资构成）。",
        ])
    if judgement is None:
        lines.append("- 该岗位没有 Jet 适合度判断：只有聊天中尚未说明岗位主要职责或销售成分时，才加入一个确认岗位性质的问题；已说明过的不要再问。")
    if not experiences:
        lines.append("- 当前没有经历素材，生成 2 个不提具体经历的版本，referenced_experience_ids 设为 []，并在 experience_note 中注明'没有可引用的经历'。")

    lines.extend([
        "- 请输出合法 JSON 对象，格式如下：",
        "```json",
        "{",
        '  "known_facts": [',
        '    "HR 在聊天中已经明确的信息（每条 ≤ 20 字）"',
        "  ],",
        '  "suggestions": [',
        "    {",
        '      "version": 1,',
        '      "tone_desc": "自然直接",',
        '      "text": "话术文本（≤ 50 字，标点也算）",',
        '      "referenced_experience_ids": [1]',
        "    },",
        "    {",
        '      "version": 2,',
        '      "tone_desc": "沉稳专业",',
        '      "text": "话术文本（≤ 50 字，标点也算）",',
        '      "referenced_experience_ids": [1]',
        "    }",
        "  ],",
        '  "questions": [',
        '    "建议问 HR 的问题 1"',
        "  ],",
        '  "experience_note": null',
        "}",
        "```",
    ])

    return "\n".join(lines)


def verify_and_filter_assist_response(
    raw_response: Mapping[str, Any] | str,
    valid_experience_ids: set[int],
    sanitized_messages: Sequence[Mapping[str, Any]],
    hr_note: str | None = None,
    mode: str = "reply",
    experiences: Sequence[Mapping[str, Any]] | None = None,
    rules_path: Path | str | None = None,
    completed_action_rules_path: Path | str | None = None,
    tone_fix_rules_path: Path | str | None = None,
    resume_sent_rules_path: Path | str | None = None,
    current_city: str = "",
    location_name: str = "",
) -> dict[str, Any]:
    """核验并过滤大模型返回结果，丢弃非法经历引用与超长话术，去重聊天已有问题，处理无经历及等待回复等特殊状态。"""
    if isinstance(raw_response, str):
        try:
            data = json.loads(raw_response)
        except Exception as e:
            raise ParseError(f"大模型响应解析失败: {e}") from e
    elif isinstance(raw_response, Mapping):
        data = dict(raw_response)
    else:
        raise ParseError("大模型响应必须为 JSON 对象或字典")

    self_fact_rules = read_self_fact_rules(rules_path=rules_path)
    completed_action_rules = read_completed_action_rules(rules_path=completed_action_rules_path)
    tone_fix_rules = read_tone_fix_rules(rules_path=tone_fix_rules_path)
    resume_sent_rules = read_resume_sent_rules(rules_path=resume_sent_rules_path)

    resume_sent = is_resume_already_sent(sanitized_messages)
    dropped_counts: dict[tuple[str, str | None], int] = {}

    if mode == "waiting_hr":
        filtered_suggestions: list[dict[str, Any]] = []
    else:
        raw_suggestions = data.get("suggestions") or []
        filtered_suggestions = []
        for s in raw_suggestions:
            if not isinstance(s, dict):
                continue
            raw_text = str(s.get("text") or "").strip()
            if not raw_text:
                continue

            # SC-005 / FR-069: 只有原文本身超过 50 字才去掉，并计入 dropped_summary
            if len(raw_text) > 50:
                key = ("too_long", None)
                dropped_counts[key] = dropped_counts.get(key, 0) + 1
                continue

            # SC-013 / FR-069: 口语词后紧跟提问时插入承接词；若改写后超过 50 字，apply_tone_fix 不改写保留原文
            text = apply_tone_fix(raw_text, rules=tone_fix_rules)

            ref_ids = s.get("referenced_experience_ids")
            if ref_ids is None:
                ref_ids = []
            elif not isinstance(ref_ids, list):
                ref_ids = [ref_ids]

            # FR-031: 引用不存在经历编号的话术被丢弃
            if not all(isinstance(rid, int) and rid in valid_experience_ids for rid in ref_ids):
                key = ("invalid_experience", None)
                dropped_counts[key] = dropped_counts.get(key, 0) + 1
                continue

            # FR-064: 含有声称已完成某操作的第一人称话术直接丢弃 (claims_done)
            claims_done_category: str | None = None
            for rule in completed_action_rules:
                if resume_sent and rule.get("name") == "简历已发送":
                    continue
                pats = rule.get("patterns") or rule.get("self_claim_patterns") or []
                if matches_self_claim(text, pats):
                    claims_done_category = rule.get("name", "简历已发送")
                    break
            if claims_done_category is not None:
                key = ("claims_done", claims_done_category)
                dropped_counts[key] = dropped_counts.get(key, 0) + 1
                continue

            # 简历已发送时，丢弃表示将要发简历的话术 (resume_already_sent)
            if resume_sent:
                done_statement_patterns = resume_sent_rules.get("done_statement_patterns") or []
                is_done_statement = any(
                    re.search(pat, text, re.IGNORECASE)
                    for pat in done_statement_patterns
                )
                if not is_done_statement:
                    resume_intent_patterns = resume_sent_rules.get("intent_patterns") or []
                    if any(re.search(pat, text, re.IGNORECASE) for pat in resume_intent_patterns):
                        key = ("resume_already_sent", "简历已发送")
                        dropped_counts[key] = dropped_counts.get(key, 0) + 1
                        continue

            # FR-054: 对规则中列出的事实类别，若经历素材/资料中未写明，含有该类第一人称陈述的话术被丢弃
            # 匹配前先把【填写：...】占位整体去掉，确保带占位的话术不会因"提到资料里没有的事实"被丢弃
            text_without_placeholders = re.sub(r"【填写：[^】]+】", "", text)
            unsupported_category: str | None = None
            for rule in self_fact_rules:
                if not is_fact_known(rule, experiences, current_city=current_city):
                    if matches_self_claim(text_without_placeholders, expand_self_claim_patterns(rule, location_name)):
                        unsupported_category = rule.get("name")
                        break
            if unsupported_category is not None:
                key = ("unsupported_fact", unsupported_category)
                dropped_counts[key] = dropped_counts.get(key, 0) + 1
                continue

            filtered_suggestions.append({
                "version": s.get("version", len(filtered_suggestions) + 1),
                "tone_desc": s.get("tone_desc", ""),
                "text": text,
                "referenced_experience_ids": ref_ids,
            })

    dropped_summary: list[dict[str, Any]] = []
    for (reason, category), count in dropped_counts.items():
        if reason == "unsupported_fact":
            text = f"{count} 个版本因提到你资料里没有的'{category}'被去掉"
        elif reason == "claims_done":
            text = f"{count} 个版本因声称'{category}'这类还没做的操作被去掉"
        elif reason == "resume_already_sent":
            text = f"{count} 个版本因简历已发送，去掉了要再发简历的话术" if count > 1 else "简历已发送，去掉了要再发简历的话术"
        elif reason == "invalid_experience":
            text = f"{count} 个版本因引用了不存在的经历条目被去掉"
        elif reason == "too_long":
            text = f"{count} 个版本因原文超过 50 字被去掉"
        else:
            text = f"{count} 个版本被去掉"
        dropped_summary.append({
            "reason": reason,
            "category": category,
            "count": count,
            "text": text,
        })

    # 问题核验与去重 (FR-014, FR-015)
    asked_normalized: set[str] = set()
    for m in sanitized_messages:
        t = str(m.get("text") or "").strip()
        if t:
            asked_normalized.add(normalize_question(t))
    if hr_note:
        asked_normalized.add(normalize_question(hr_note))

    raw_questions = data.get("questions") or []
    filtered_questions: list[str] = []
    seen_q_normalized: set[str] = set()

    for q in raw_questions:
        if not isinstance(q, str):
            continue
        q_clean = q.strip()
        if not q_clean:
            continue
        q_norm = normalize_question(q_clean)
        # 排除在聊天中已问过或 HR 实际情况已有答案的问题
        if q_norm in asked_normalized:
            continue
        if q_norm in seen_q_normalized:
            continue
        seen_q_normalized.add(q_norm)
        filtered_questions.append(q_clean)
        if len(filtered_questions) == 3:
            break

    # experience_note
    exp_note = data.get("experience_note")
    if not valid_experience_ids and not exp_note:
        exp_note = "没有可引用的经历"

    # FR-054 & FR-055: 生成 unanswered_facts 与 request_notice（不依赖模型）
    unanswered_facts = determine_unanswered_facts(
        sanitized_messages=sanitized_messages,
        experiences=experiences,
        rules=self_fact_rules,
        rules_path=rules_path,
        current_city=current_city,
        location_name=location_name,
    )
    request_notice = determine_request_notice(
        sanitized_messages=sanitized_messages,
    )

    if not filtered_suggestions and not filtered_questions and not unanswered_facts and not request_notice:
        raise AllSuggestionsDroppedError(
            "这次没有可用的建议，可以再点一次生成",
            dropped_summary=dropped_summary,
        )

    return {
        "suggestions": filtered_suggestions,
        "questions": filtered_questions,
        "experience_note": exp_note,
        "unanswered_facts": unanswered_facts,
        "request_notice": request_notice,
        "dropped_summary": dropped_summary,
    }


def assemble_assist_request(
    job_title: str,
    company_name: str,
    location_name: str,
    messages: Sequence[Mapping[str, Any]] | None = None,
    experiences: Sequence[Mapping[str, Any]] | None = None,
    *,
    sanitized_messages: Sequence[Mapping[str, Any]] | None = None,
    hr_name: str | None = None,
    user_name: str | None = None,
    judgement: Mapping[str, Any] | None = None,
    hr_note: str | None = None,
    force_mode: str | None = None,
    rules_path: Path | str | None = None,
    current_city: str = "",
) -> dict[str, Any]:
    """负责从请求数据得到截取最近 30 条 + 脱敏后的消息、阶段判定、读取语气规则、组装提示词、计算指纹的全部结果。"""
    if sanitized_messages is not None:
        effective_messages = list(sanitized_messages)
    elif messages is not None:
        if messages and all(
            isinstance(m, Mapping)
            and "sender" in m
            and "text" in m
            and "body_type" not in m
            and "bodyType" not in m
            for m in messages
        ):
            effective_messages = list(messages)
        else:
            effective_messages = sanitize_chat_messages(
                messages,
                hr_name=hr_name,
                user_name=user_name,
            )
    else:
        effective_messages = []

    if len(effective_messages) > 30:
        effective_messages = effective_messages[-30:]

    mode, mode_label, mode_basis = determine_communication_stage(
        effective_messages, force_mode=force_mode
    )
    tone_rules = read_tone_rules(rules_path=rules_path)
    unanswered_facts = determine_unanswered_facts(
        sanitized_messages=effective_messages,
        experiences=experiences,
        rules_path=rules_path,
        current_city=current_city,
        location_name=location_name,
    )
    sanitized_hr_note: str | None = None
    if hr_note is not None:
        sensitive_terms = get_sensitive_name_terms(hr_name=hr_name, user_name=user_name)
        sanitized_hr_note = sanitize_text(hr_note, sensitive_terms)

    prompt = build_assist_prompt(
        job_title=job_title,
        company_name=company_name,
        location_name=location_name,
        sanitized_messages=effective_messages,
        experiences=experiences or [],
        tone_rules=tone_rules,
        judgement=judgement,
        hr_note=sanitized_hr_note,
        mode=mode,
        mode_basis=mode_basis,
        unanswered_facts=unanswered_facts,
        rules_path=rules_path,
        current_city=current_city,
    )
    prompt_hash = compute_prompt_hash(prompt)

    fields = list(ASSIST_PREVIEW_FIELDS)
    if current_city and current_city.strip():
        fields.append("我的资料：目前所在城市")

    return {
        "fields": fields,
        "job_title": job_title,
        "company_name": company_name,
        "location_name": location_name,
        "sanitized_messages": effective_messages,
        "mode": mode,
        "mode_label": mode_label,
        "mode_basis": mode_basis,
        "tone_rules": tone_rules,
        "prompt_text": prompt,
        "prompt_hash": prompt_hash,
        "message_count": len(effective_messages),
        "has_jet_judgement": judgement is not None,
        "experiences": experiences or [],
        "judgement": judgement,
        "hr_note": sanitized_hr_note,
        "unanswered_facts": unanswered_facts,
        "current_city": current_city,
    }


def generate_assist_suggestions(
    settings: Settings,
    job_title: str,
    company_name: str,
    location_name: str,
    sanitized_messages: Sequence[Mapping[str, Any]],
    experiences: Sequence[Mapping[str, Any]],
    expected_hash: str,
    judgement: Mapping[str, Any] | None = None,
    hr_note: str | None = None,
    force_mode: str | None = None,
    transport: httpx.BaseTransport | None = None,
    rules_path: Path | str | None = None,
    completed_action_rules_path: Path | str | None = None,
    tone_fix_rules_path: Path | str | None = None,
    resume_sent_rules_path: Path | str | None = None,
    *,
    messages: Sequence[Mapping[str, Any]] | None = None,
    hr_name: str | None = None,
    user_name: str | None = None,
    current_city: str = "",
    assembled: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """单次调用大模型（关闭思考模式）并核验生成回复话术与提问建议，返回符合 API 契约的结构化结果。

    assembled：调用方已经用 assemble_assist_request 组装好的请求（接口层比对指纹时组装过一次），
    传入时不再重复组装；不传时在这里组装。两种情况都会再核对一次指纹。
    """
    if not expected_hash or not str(expected_hash).strip():
        raise HashMismatchError("发送内容哈希不一致，已被安全拦截")

    if assembled is None:
        assembled = assemble_assist_request(
            job_title=job_title,
            company_name=company_name,
            location_name=location_name,
            messages=messages,
            experiences=experiences,
            sanitized_messages=sanitized_messages,
            hr_name=hr_name,
            user_name=user_name,
            judgement=judgement,
            hr_note=hr_note,
            force_mode=force_mode,
            rules_path=rules_path,
            current_city=current_city,
        )

    if expected_hash != assembled["prompt_hash"]:
        raise HashMismatchError("发送内容哈希不一致，已被安全拦截")

    messages_payload = [{"role": "user", "content": assembled["prompt_text"]}]
    content, usage = call_once(
        settings,
        messages_payload,
        transport=transport,
        thinking=False,
        max_tokens=800,
    )

    valid_ids = {
        exp["item_no"]
        for exp in (experiences or [])
        if isinstance(exp, Mapping) and "item_no" in exp and isinstance(exp["item_no"], int)
    }

    try:
        verified = verify_and_filter_assist_response(
            raw_response=content,
            valid_experience_ids=valid_ids,
            sanitized_messages=assembled["sanitized_messages"],
            hr_note=hr_note,
            mode=assembled["mode"],
            experiences=experiences,
            rules_path=rules_path,
            completed_action_rules_path=completed_action_rules_path,
            tone_fix_rules_path=tone_fix_rules_path,
            resume_sent_rules_path=resume_sent_rules_path,
            current_city=current_city,
            location_name=location_name,
        )
    except AllSuggestionsDroppedError as err:
        err.usage = usage
        raise

    return {
        "company_name": company_name,
        "job_title": job_title,
        "mode": assembled["mode"],
        "mode_label": assembled["mode_label"],
        "mode_basis": assembled["mode_basis"],
        "has_jet_judgement": assembled["has_jet_judgement"],
        "message_count": assembled["message_count"],
        "suggestions": verified["suggestions"],
        "experience_note": verified["experience_note"],
        "questions": verified["questions"],
        "unanswered_facts": verified["unanswered_facts"],
        "request_notice": verified["request_notice"],
        "dropped_summary": verified["dropped_summary"],
        "prompt_text": assembled["prompt_text"],
        "prompt_hash": assembled["prompt_hash"],
        "usage": usage,
    }


def build_assist_preview(
    job_title: str,
    company_name: str,
    location_name: str,
    sanitized_messages: Sequence[Mapping[str, Any]] | None = None,
    experiences: Sequence[Mapping[str, Any]] | None = None,
    judgement: Mapping[str, Any] | None = None,
    hr_note: str | None = None,
    force_mode: str | None = None,
    rules_path: Path | str | None = None,
    *,
    messages: Sequence[Mapping[str, Any]] | None = None,
    hr_name: str | None = None,
    user_name: str | None = None,
    current_city: str = "",
) -> dict[str, Any]:
    """组装脱敏文本预览及 SHA-256 指纹，供侧边栏在发送前展示发送字段清单与完整脱敏报文。"""
    assembled = assemble_assist_request(
        job_title=job_title,
        company_name=company_name,
        location_name=location_name,
        messages=messages,
        experiences=experiences,
        sanitized_messages=sanitized_messages,
        hr_name=hr_name,
        user_name=user_name,
        judgement=judgement,
        hr_note=hr_note,
        force_mode=force_mode,
        rules_path=rules_path,
        current_city=current_city,
    )
    return assist_preview_fields(assembled)


def assist_preview_fields(assembled: Mapping[str, Any]) -> dict[str, Any]:
    """组装结果里给侧边栏预览的字段；预览接口与 build_assist_preview 共用（体检第 83 条）。"""
    return {
        "fields": assembled["fields"],
        "sanitized_prompt": assembled["prompt_text"],
        "prompt_hash": assembled["prompt_hash"],
        "mode": assembled["mode"],
        "mode_label": assembled["mode_label"],
        "mode_basis": assembled["mode_basis"],
        "message_count": assembled["message_count"],
        "has_jet_judgement": assembled["has_jet_judgement"],
    }
