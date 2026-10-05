import copy
import re
from typing import Any

from jet.domain.normalize import normalize_text


def check_quote(text: str | None, description: str | None) -> bool:
    """
    Check if a quote string is found in the job description.

    Returns False if text or description is empty or whitespace.
    Normalizes both with normalize_text (NFKC, full-width/half-width, extra spaces).
    If not matched, strips all whitespace from both and checks substring again.
    """
    if not text or not description:
        return False

    t = text.strip()
    d = description.strip()
    if not t or not d:
        return False

    norm_t = normalize_text(t)
    norm_d = normalize_text(d)

    if norm_t in norm_d:
        return True

    # Strip all whitespace from both
    no_space_t = re.sub(r"\s+", "", norm_t)
    no_space_d = re.sub(r"\s+", "", norm_d)
    if no_space_t and no_space_t in no_space_d:
        return True

    return False


def annotate_facts(facts: dict[str, Any], description: str | None) -> dict[str, Any]:
    """
    Annotate all quotes in facts structure with found: bool.

    Converts each quote string in:
    - summary.quotes
    - work_type.quotes
    - sales_level.signals[].quote
    - experience.requirement
    - overtime.quotes
    into {"text": original_str, "found": bool}.
    """
    res = copy.deepcopy(facts) if facts else {}

    # 1. summary
    if "summary" in res and isinstance(res["summary"], dict):
        quotes = res["summary"].get("quotes") or []
        res["summary"]["quotes"] = [
            {
                "text": q["text"] if isinstance(q, dict) else str(q),
                "found": check_quote(q["text"] if isinstance(q, dict) else str(q), description),
            }
            for q in quotes
        ]

    # 2. work_type
    if "work_type" in res and isinstance(res["work_type"], dict):
        quotes = res["work_type"].get("quotes") or []
        res["work_type"]["quotes"] = [
            {
                "text": q["text"] if isinstance(q, dict) else str(q),
                "found": check_quote(q["text"] if isinstance(q, dict) else str(q), description),
            }
            for q in quotes
        ]

    # 3. sales_level
    if "sales_level" in res and isinstance(res["sales_level"], dict):
        signals = res["sales_level"].get("signals") or []
        annotated_signals = []
        for s in signals:
            s_dict = dict(s)
            q = s_dict.get("quote")
            q_text = q["text"] if isinstance(q, dict) else str(q or "")
            s_dict["quote"] = {
                "text": q_text,
                "found": check_quote(q_text, description),
            }
            annotated_signals.append(s_dict)
        res["sales_level"]["signals"] = annotated_signals

    # 4. experience
    if "experience" in res and isinstance(res["experience"], dict):
        req = res["experience"].get("requirement")
        if req is not None:
            req_text = req["text"] if isinstance(req, dict) else str(req)
            res["experience"]["requirement"] = {
                "text": req_text,
                "found": check_quote(req_text, description),
            }
        else:
            res["experience"]["requirement"] = None

    # 5. overtime
    if "overtime" in res and isinstance(res["overtime"], dict):
        quotes = res["overtime"].get("quotes") or []
        res["overtime"]["quotes"] = [
            {
                "text": q["text"] if isinstance(q, dict) else str(q),
                "found": check_quote(q["text"] if isinstance(q, dict) else str(q), description),
            }
            for q in quotes
        ]

    # 6. work_intensity
    if "work_intensity" in res and isinstance(res["work_intensity"], dict):
        quotes = res["work_intensity"].get("quotes") or []
        res["work_intensity"]["quotes"] = [
            {
                "text": q["text"] if isinstance(q, dict) else str(q),
                "found": check_quote(q["text"] if isinstance(q, dict) else str(q), description),
            }
            for q in quotes
        ]

    # 7. risk_signals
    if "risk_signals" in res and isinstance(res["risk_signals"], list):
        annotated_risks = []
        for r in res["risk_signals"]:
            if isinstance(r, dict):
                r_dict = dict(r)
                q = r_dict.get("quote")
                q_text = q["text"] if isinstance(q, dict) else str(q or "")
                r_dict["quote"] = {
                    "text": q_text,
                    "found": check_quote(q_text, description),
                }
                annotated_risks.append(r_dict)
            else:
                annotated_risks.append(r)
        res["risk_signals"] = annotated_risks

    return res


def count_quotes(facts: dict[str, Any]) -> tuple[int, int]:
    """
    Count total quotes and missing quotes across the facts structure.

    Returns (total, missing).
    """
    if not facts:
        return 0, 0

    total = 0
    missing = 0

    def _check_q(q: Any) -> None:
        nonlocal total, missing
        if isinstance(q, dict):
            total += 1
            if not q.get("found"):
                missing += 1

    # summary
    for q in facts.get("summary", {}).get("quotes", []):
        _check_q(q)

    # work_type
    for q in facts.get("work_type", {}).get("quotes", []):
        _check_q(q)

    # sales_level
    for s in facts.get("sales_level", {}).get("signals", []):
        if isinstance(s, dict):
            _check_q(s.get("quote"))

    # experience
    req = facts.get("experience", {}).get("requirement")
    if req is not None:
        _check_q(req)

    # overtime
    for q in facts.get("overtime", {}).get("quotes", []):
        _check_q(q)

    # work_intensity
    for q in facts.get("work_intensity", {}).get("quotes", []):
        _check_q(q)

    # risk_signals
    for r in facts.get("risk_signals", []):
        if isinstance(r, dict):
            _check_q(r.get("quote"))

    return total, missing
