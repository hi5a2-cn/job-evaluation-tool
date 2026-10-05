"""Chat message sanitization and hash computation module.

Spec: specs/003-hr-assistant/spec.md (R8, FR-003, FR-021, FR-022, SC-002, SC-003)
Tasks: specs/003-hr-assistant/tasks.md (T007)
Plan: specs/003-hr-assistant/plan.md (Decisions 2, 7)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

DEFAULT_ACTION_CARD_RULES_PATH: Path = (
    Path(__file__).resolve().parent / "prompts" / "action_card_rules.json"
)


def read_action_card_rules(rules_path: Path | str | None = None) -> list[dict[str, Any]]:
    """从独立规则文件中实时动态读取动作类卡片判定规则，确保修改即时生效 (SC-013)。"""
    path = Path(rules_path) if rules_path else DEFAULT_ACTION_CARD_RULES_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


PLACEHOLDER: str = "[已隐藏]"

COMPOUND_SURNAMES: tuple[str, ...] = (
    "欧阳", "上官", "诸葛", "司马", "东方",
    "皇甫", "令狐", "慕容", "尉迟", "公孙",
    "长孙", "宇文", "夏侯", "轩辕", "端木",
    "独孤", "南宫", "西门", "百里", "呼延",
    "万俟", "闻人", "澹台", "公冶", "太史",
    "申屠", "钟离", "濮阳", "赫连", "司徒",
)

HONORIFICS: tuple[str, ...] = ("女士", "先生", "经理", "老师", "总")

_D: str = r"[0-9０-９]"
_SEP: str = r"[\s.．－\-]{0,2}"

EMAIL_PATTERN: re.Pattern[str] = re.compile(
    r"[a-zA-Z0-9_.+-]+[@＠][a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+"
)

PHONE_PATTERN: re.Pattern[str] = re.compile(
    rf"(?<!{_D})(?:(?:\+|＋)?(?:86|８６){_SEP}|[（\(](?:\+|＋)?(?:86|８６)[）\)]{_SEP})?"
    rf"[1１][3-9３-９]{_D}(?:{_SEP}{_D}{{4}}{_SEP}{_D}{{4}}|{_SEP}{_D}{{3}}{_SEP}{_D}{{5}})"
    rf"(?!{_D})"
)

WXID_PATTERN: re.Pattern[str] = re.compile(
    r"(?<![a-zA-Z0-9_])wxid_[a-zA-Z0-9_-]+"
)

WECHAT_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(?<![a-zA-Z0-9])("
    r"加(?:我)?微(?:信)?|"
    r"微信号?|"
    r"微(?=[:：])|"
    r"wechat|"
    r"[vw]x|"
    r"v信|"
    r"加v|"
    r"[+＋]v"
    r")"
    r"([:：\s=]*(?:是|为)?[:：\s=]*)"
    r"([a-zA-Z0-9_.-]{5,30}(?<!\.))"
)

ID_CARD_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)


class SelfNameUnavailable(Exception):
    """Raised when user's own name is missing, empty, or whitespace-only."""


def extract_surname(name: str) -> str:
    """Extract surname from full name.

    If name starts with one of the 30 common compound surnames, returns it;
    otherwise returns the first character. Returns empty string if name is empty.
    """
    clean_name = name.strip() if name else ""
    if not clean_name:
        return ""
    for compound in COMPOUND_SURNAMES:
        if clean_name.startswith(compound):
            return compound
    return clean_name[0]


def get_sensitive_name_terms(hr_name: str | None, user_name: str | None) -> list[str]:
    """Build list of full names and 'surname + honorific' terms, sorted by length descending."""
    terms: set[str] = set()

    clean_user = user_name.strip() if user_name else ""
    if clean_user:
        terms.add(clean_user)
        user_surname = extract_surname(clean_user)
        if user_surname:
            for honorific in HONORIFICS:
                terms.add(f"{user_surname}{honorific}")

    clean_hr = hr_name.strip() if hr_name else ""
    if clean_hr:
        terms.add(clean_hr)
        hr_surname = extract_surname(clean_hr)
        if hr_surname:
            for honorific in HONORIFICS:
                terms.add(f"{hr_surname}{honorific}")

    return sorted(terms, key=len, reverse=True)


def _sanitize_contacts(text: str) -> str:
    """脱敏邮箱、手机号、身份证号与微信号。"""
    if not text:
        return ""

    # 1. Emails
    sanitized = EMAIL_PATTERN.sub(PLACEHOLDER, text)

    # 2. Phone numbers (with or without dashes, +86 prefixes)
    sanitized = PHONE_PATTERN.sub(PLACEHOLDER, sanitized)

    # 3. ID Card numbers (18-digit Chinese resident identity card)
    sanitized = ID_CARD_PATTERN.sub(PLACEHOLDER, sanitized)

    # 4. WeChat IDs (standalone wxid_ and keyword-preceded accounts)
    sanitized = WXID_PATTERN.sub(PLACEHOLDER, sanitized)
    sanitized = WECHAT_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{PLACEHOLDER}",
        sanitized,
    )

    return sanitized


def sanitize_text(text: str, sensitive_terms: Sequence[str]) -> str:
    """Pure function to sanitize a single string by masking sensitive patterns."""
    if not text:
        return ""

    sanitized = _sanitize_contacts(text)

    # Full names and surname + honorifics (matched longest first)
    if sensitive_terms:
        pattern = re.compile("|".join(re.escape(term) for term in sensitive_terms))
        sanitized = pattern.sub(PLACEHOLDER, sanitized)

    return sanitized


def _build_name_term_pattern(term: str) -> str:
    """为姓名敏感词构造容忍空白的正则表达式。
    全汉字词在字与字之间允许任意空白（\\s*）；
    英文名/多词在单词之间允许任意空白（\\s+）。
    """
    clean = term.strip()
    if not clean:
        return ""
    no_space = "".join(clean.split())
    if re.fullmatch(r"[\u4e00-\u9fa5]+", no_space):
        return r"\s*".join(re.escape(c) for c in no_space)
    parts = clean.split()
    if len(parts) > 1:
        return r"\s+".join(re.escape(p) for p in parts)
    return re.escape(clean)


def sanitize_resume_content(text: str, sensitive_terms: Sequence[str]) -> str:
    """专用于简历正文的强脱敏函数。
    在基础脱敏（邮箱、手机号、身份证、微信号）的基础上，
    允许每个姓名敏感词字符之间（或单词之间）有任意空白（例如 '李明' 匹配 '李 明'、'李\n明'）。
    绝不改变聊天脱敏 sanitize_text 的现有行为。
    """
    if not text:
        return ""

    sanitized = _sanitize_contacts(text)

    # 5. Full names and surname + honorifics (with whitespace tolerance)
    if sensitive_terms:
        # 按无空白字符数从长到短排序
        sorted_terms = sorted(sensitive_terms, key=lambda t: len("".join(t.split())), reverse=True)
        patterns = [_build_name_term_pattern(t) for t in sorted_terms if t.strip()]
        if patterns:
            name_pattern = re.compile("|".join(patterns))
            sanitized = name_pattern.sub(PLACEHOLDER, sanitized)

    return sanitized


def sanitize_chat_messages(
    messages: Sequence[Mapping[str, Any]],
    hr_name: str | None,
    user_name: str | None,
    rules_path: Path | str | None = None,
    limit: int | None = 30,
) -> list[dict[str, Any]]:
    """Pure function to filter and sanitize chat messages according to R8, FR-073 & SC-002/SC-003/SC-013.

    - Rejects if user_name is missing or whitespace-only (raises SelfNameUnavailable).
    - Checks action card rules before R8 filter (converts hit cards to {sender: "我", text: marker, is_request_card: False}).
    - Excludes body_type=16 and unhandled is_system=True.
    - Retains body_type=1 and body_type=7 messages.
    - Attributes sender to '我' (is_self=True) or 'HR' (is_self=False).
    - Sanitizes text content without mutating inputs.
    - Truncates to the most recent `limit` messages (default 30).
    - Resolves quote_context for retained messages having non-empty quote_id against mid of the same batch.
    - Omits quote_context if quote_id is missing/empty, and never includes mid/quote_id in output.
    """
    if user_name is None or not user_name.strip():
        raise SelfNameUnavailable("User name is required for chat sanitization")

    action_card_rules = read_action_card_rules(rules_path=rules_path)
    sensitive_terms = get_sensitive_name_terms(hr_name=hr_name, user_name=user_name)
    retained_candidates: list[dict[str, Any]] = []

    for msg in messages:
        raw_body_type = msg.get("body_type") if "body_type" in msg else msg.get("bodyType")
        raw_text = str(msg.get("text") or "")
        raw_mid = msg.get("mid")
        raw_quote_id = msg.get("quote_id") if "quote_id" in msg else msg.get("quoteId")

        # 优先匹配动作类卡片规则（R8 过滤前，包括 is_system=True 的卡片）
        matched_rule = None
        for rule in action_card_rules:
            rule_bt = rule.get("body_type")
            if rule_bt is not None:
                if raw_body_type is None:
                    continue
                try:
                    if int(raw_body_type) != int(rule_bt):
                        continue
                except (ValueError, TypeError):
                    continue

            text_contains = rule.get("text_contains")
            if text_contains:
                if isinstance(text_contains, list):
                    if not all(tc in raw_text for tc in text_contains):
                        continue
                elif isinstance(text_contains, str):
                    if text_contains not in raw_text:
                        continue

            matched_rule = rule
            break

        if matched_rule is not None:
            retained_candidates.append({
                "sender": matched_rule.get("sender", "我"),
                "text": matched_rule.get("marker", ""),
                "is_request_card": False,
                "_mid": raw_mid,
                "_quote_id": raw_quote_id,
            })
            continue

        # Check system message status
        is_system = bool(
            msg.get("is_system")
            if "is_system" in msg
            else msg.get("isSystem", False)
        )
        if is_system:
            continue

        # Check body type (retain body_type=1 and body_type=7)
        body_type = (
            msg.get("body_type")
            if "body_type" in msg
            else msg.get("bodyType")
        )
        if body_type not in (1, 7):
            continue

        # Sender attribution
        is_self = bool(
            msg.get("is_self")
            if "is_self" in msg
            else msg.get("isSelf", False)
        )

        sender = "我" if is_self else "HR"

        # Sanitize text
        clean_text = sanitize_text(raw_text, sensitive_terms)

        is_request_card = body_type == 7
        card_kind = None
        if is_request_card and sender == "HR" and "工作地点" in raw_text:
            card_kind = "location_confirm"

        candidate = {
            "sender": sender,
            "text": clean_text,
            "is_request_card": is_request_card,
            "_mid": raw_mid,
            "_quote_id": raw_quote_id,
        }
        if card_kind:
            candidate["card_kind"] = card_kind
        retained_candidates.append(candidate)

    # 截取最近 N 条保留消息（以现有代码处理顺序为准）
    if limit is not None and len(retained_candidates) > limit:
        retained = list(retained_candidates[-limit:])
    else:
        retained = list(retained_candidates)

    sanitized_messages: list[dict[str, Any]] = []

    for item in retained:
        raw_quote_id = item.get("_quote_id")
        has_quote = (
            raw_quote_id is not None
            and not isinstance(raw_quote_id, bool)
            and raw_quote_id != ""
        )

        msg_dict: dict[str, Any] = {
            "sender": item["sender"],
            "text": item["text"],
            "is_request_card": item["is_request_card"],
        }
        if "card_kind" in item:
            msg_dict["card_kind"] = item["card_kind"]

        if has_quote:
            target_msg = None
            for other in retained:
                o_mid = other.get("_mid")
                if o_mid is not None and not isinstance(o_mid, bool) and o_mid != "":
                    if o_mid == raw_quote_id or (
                        isinstance(o_mid, (int, str))
                        and isinstance(raw_quote_id, (int, str))
                        and str(o_mid) == str(raw_quote_id)
                    ):
                        target_msg = other
                        break

            if target_msg is not None:
                q_from = target_msg["sender"]
                q_text = target_msg["text"]
                if len(q_text) > 40:
                    q_text = q_text[:40] + "……"
                msg_dict["quote_context"] = {
                    "from": q_from,
                    "text": q_text,
                }
            else:
                msg_dict["quote_context"] = {
                    "from": None,
                    "text": None,
                }

        sanitized_messages.append(msg_dict)

    return sanitized_messages


def compute_prompt_hash(text: str) -> str:
    """Compute SHA-256 hex digest of the given prompt text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
