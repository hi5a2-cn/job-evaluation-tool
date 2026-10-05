# 来源：jet-demo d13aa43 core/salary_parser.py，已修正
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ParsedSalary:
    """Structured representation of parsed salary with amounts normalized to k CNY/month."""
    raw: str
    min_k: float | None
    max_k: float | None
    months: int | None
    period: str  # "month", "year", "day", "hour"
    parse_ok: bool


def parse_salary(text: str | None) -> ParsedSalary:
    """
    Parse unstructured salary strings into normalized monthly k CNY values.

    Returns ParsedSalary with parse_ok=False if unknown, negotiable, or invalid.
    """
    raw = (text or "").strip()
    if not raw or any(kw in raw for kw in ["面议", "未公开", "不限", "详见", "Negotiable"]):
        return ParsedSalary(raw=raw, min_k=None, max_k=None, months=None, period="month", parse_ok=False)

    # 外币薪资无法折算为人民币月薪：记为无法解析，而不是当成人民币数值（原则 IV）
    if re.search(r"[$€£]|USD|EUR|GBP|美元|欧元|英镑", raw, re.IGNORECASE):
        return ParsedSalary(raw=raw, min_k=None, max_k=None, months=None, period="month", parse_ok=False)

    # Detect months count (e.g. 15-25K·14薪, 14薪)
    months = 12
    month_match = re.search(r"[·*×\s](\d{1,2})\s*薪", raw)
    if month_match:
        try:
            months = int(month_match.group(1))
        except ValueError:
            months = 12

    # Detect explicit pay period
    explicit_period: str | None = None
    if any(kw in raw for kw in ["/年", "·年", "年薪", "每年", "/yr", "per year", "annum", "万/年", "W/年", "w/年"]):
        explicit_period = "year"
    elif any(kw in raw for kw in ["/天", "·天", "日薪", "每天", "/day", "per day", "元/天"]):
        explicit_period = "day"
    elif any(kw in raw for kw in ["/时", "·时", "/小时", "时薪", "每小时", "/hr", "per hour", "元/时", "元/小时"]):
        explicit_period = "hour"
    elif any(kw in raw for kw in ["/月", "·月", "月薪", "每月", "/mo", "per month", "万/月", "元/月"]):
        explicit_period = "month"

    cleaned = raw.replace(",", "").replace("，", "")

    # 1. Wan format: e.g. 1-1.5万, 1万-2万, 1.5万-2万, 20-30万/年, 5-8万
    wan_range = re.search(r"(\d+(?:\.\d+)?)\s*[万wW]?\s*[-~至到/]\s*(\d+(?:\.\d+)?)\s*[万wW]", cleaned)
    if wan_range:
        v1 = float(wan_range.group(1))
        v2 = float(wan_range.group(2))

        if explicit_period is not None:
            period = explicit_period
        else:
            # Without explicit marker:
            # Upper bound <= 5 万 -> monthly salary (1-1.5万 = 10-15K/month)
            # Lower bound >= 10 万 -> annual salary
            # Between 5 and 10 万 -> ambiguous, parse_ok = False
            if v2 <= 5.0:
                period = "month"
            elif v1 >= 10.0:
                period = "year"
            else:
                return ParsedSalary(raw=raw, min_k=None, max_k=None, months=None, period="month", parse_ok=False)

        if period == "month":
            min_k = round(v1 * 10.0, 4)
            max_k = round(v2 * 10.0, 4)
        elif period == "year":
            divisor = max(months, 12)
            min_k = round((v1 * 10.0) / divisor, 4)
            max_k = round((v2 * 10.0) / divisor, 4)
        elif period == "day":
            min_k = round((v1 * 10000.0 * 21.75) / 1000.0, 4)
            max_k = round((v2 * 10000.0 * 21.75) / 1000.0, 4)
        else:  # hour
            min_k = round((v1 * 10000.0 * 8 * 21.75) / 1000.0, 4)
            max_k = round((v2 * 10000.0 * 8 * 21.75) / 1000.0, 4)

        return ParsedSalary(raw=raw, min_k=min_k, max_k=max_k, months=months, period=period, parse_ok=True)

    # Single Wan format: e.g. 1.5万, 2万/月, 20万/年
    single_wan = re.search(r"(\d+(?:\.\d+)?)\s*[万wW]", cleaned)
    if single_wan and not re.search(r"\d+\s*[-~至到/]", cleaned):
        v = float(single_wan.group(1))
        if explicit_period is not None:
            period = explicit_period
        else:
            if v <= 5.0:
                period = "month"
            elif v >= 10.0:
                period = "year"
            else:
                return ParsedSalary(raw=raw, min_k=None, max_k=None, months=None, period="month", parse_ok=False)

        if period == "month":
            val_k = round(v * 10.0, 4)
        else:
            divisor = max(months, 12)
            val_k = round((v * 10.0) / divisor, 4)
        return ParsedSalary(raw=raw, min_k=val_k, max_k=val_k, months=months, period=period, parse_ok=True)

    # 2. K format: e.g. 6-8K, 15-20k·13薪, 15K-20K
    k_range = re.search(r"(\d+(?:\.\d+)?)\s*[kK]?\s*[-~至到/]\s*(\d+(?:\.\d+)?)\s*[kK]", cleaned)
    if k_range:
        v1 = float(k_range.group(1))
        v2 = float(k_range.group(2))
        period = explicit_period or "month"
        if period == "month":
            min_k = round(v1, 4)
            max_k = round(v2, 4)
        else:  # year
            divisor = max(months, 12)
            min_k = round(v1 / divisor, 4)
            max_k = round(v2 / divisor, 4)
        return ParsedSalary(raw=raw, min_k=min_k, max_k=max_k, months=months, period=period, parse_ok=True)

    # Single K: e.g. 15K, 20k
    single_k = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", cleaned)
    if single_k and not re.search(r"\d+\s*[-~至到/]", cleaned):
        v = float(single_k.group(1))
        period = explicit_period or "month"
        if period == "month":
            val_k = round(v, 4)
        else:
            divisor = max(months, 12)
            val_k = round(v / divisor, 4)
        return ParsedSalary(raw=raw, min_k=val_k, max_k=val_k, months=months, period=period, parse_ok=True)

    # 3. Raw integer range: e.g. 6000-8000, 200-300元/天, 30-50元/时, $4000-$6000
    int_range = re.search(r"(\d+(?:\.\d+)?)\s*[-~至到/]\s*(\d+(?:\.\d+)?)", cleaned)
    if int_range:
        v1 = float(int_range.group(1))
        v2 = float(int_range.group(2))

        has_currency_or_unit = bool(re.search(r"[$￥¥€£元]", cleaned))
        # Pure integers only accepted if containing currency/元 or values >= 1000
        if has_currency_or_unit or (v1 >= 1000 and v2 >= 1000):
            period = explicit_period or "month"
            if period == "day":
                min_k = round((v1 * 21.75) / 1000.0, 4)
                max_k = round((v2 * 21.75) / 1000.0, 4)
            elif period == "hour":
                min_k = round((v1 * 8 * 21.75) / 1000.0, 4)
                max_k = round((v2 * 8 * 21.75) / 1000.0, 4)
            elif period == "year":
                divisor = max(months, 12)
                min_k = round((v1 / 1000.0) / divisor, 4)
                max_k = round((v2 / 1000.0) / divisor, 4)
            else:  # month
                # 这里的数字是元（6000-8000、800-1200元/月），一律换算成 K
                min_k = round(v1 / 1000.0, 4)
                max_k = round(v2 / 1000.0, 4)
            return ParsedSalary(raw=raw, min_k=min_k, max_k=max_k, months=months, period=period, parse_ok=True)

    # 4. Single number with currency/元 or value >= 1000
    single_num = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if single_num and not re.search(r"\d+\s*[-~至到/]", cleaned):
        v = float(single_num.group(1))
        has_currency_or_unit = bool(re.search(r"[$￥¥€£元]", cleaned))
        if has_currency_or_unit or v >= 1000:
            period = explicit_period or "month"
            if period == "day":
                val_k = round((v * 21.75) / 1000.0, 4)
            elif period == "hour":
                val_k = round((v * 8 * 21.75) / 1000.0, 4)
            elif period == "year":
                divisor = max(months, 12)
                val_k = round((v / 1000.0) / divisor, 4)
            else:
                val_k = round(v / 1000.0, 4)
            return ParsedSalary(raw=raw, min_k=val_k, max_k=val_k, months=months, period=period, parse_ok=True)

    # Could not parse or rejected fallback
    return ParsedSalary(raw=raw, min_k=None, max_k=None, months=None, period="month", parse_ok=False)
