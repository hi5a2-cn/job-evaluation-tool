from jet.domain.rules import RuleResult, screen, screen_hints
from jet.domain.salary import parse_salary


def test_salary_yuan_under_1000_single():
    """'800元/月' -> min_k=max_k=0.8"""
    s = parse_salary("800元/月")
    assert s.parse_ok is True
    assert s.min_k == 0.8
    assert s.max_k == 0.8
    assert s.period == "month"


def test_salary_yuan_under_1000_range():
    """'500-800元/月' -> min_k=0.5, max_k=0.8"""
    s = parse_salary("500-800元/月")
    assert s.parse_ok is True
    assert s.min_k == 0.5
    assert s.max_k == 0.8
    assert s.period == "month"


def test_salary_yuan_under_1000_span_1000():
    """'800-1200元/月' -> min_k=0.8, max_k=1.2（下限不大于上限）"""
    s = parse_salary("800-1200元/月")
    assert s.parse_ok is True
    assert s.min_k == 0.8
    assert s.max_k == 1.2
    assert s.min_k <= s.max_k
    assert s.period == "month"


def test_salary_yuan_under_1000_currency_symbol():
    """'¥500-800/月' -> 0.5/0.8（货币符号也按元换算）；本机真实数据里的 '500-6000元/月' -> 0.5/6.0"""
    s = parse_salary("¥500-800/月")
    assert s.parse_ok is True
    assert s.min_k == 0.5
    assert s.max_k == 0.8
    assert s.period == "month"

    s2 = parse_salary("500-6000元/月")
    assert s2.parse_ok is True
    assert s2.min_k == 0.5
    assert s2.max_k == 6.0


def test_salary_existing_behaviors_preserved():
    """原有的薪资解析逻辑保持不变。"""
    # '3000-5000元/月' -> 3.0/5.0
    r1 = parse_salary("3000-5000元/月")
    assert r1.parse_ok is True
    assert r1.min_k == 3.0
    assert r1.max_k == 5.0
    assert r1.period == "month"

    # '6000-8000' -> 6.0/8.0
    r2 = parse_salary("6000-8000")
    assert r2.parse_ok is True
    assert r2.min_k == 6.0
    assert r2.max_k == 8.0
    assert r2.period == "month"

    # '15-20K' -> 15/20
    r3 = parse_salary("15-20K")
    assert r3.parse_ok is True
    assert r3.min_k == 15.0
    assert r3.max_k == 20.0
    assert r3.period == "month"

    # '200-300元/天' 按原有日薪换算 (21.75 天折算)
    r4 = parse_salary("200-300元/天")
    assert r4.parse_ok is True
    assert r4.period == "day"
    assert r4.min_k == round((200 * 21.75) / 1000.0, 4)  # 4.35
    assert r4.max_k == round((300 * 21.75) / 1000.0, 4)  # 6.525

    # '8000' -> 8.0
    r5 = parse_salary("8000")
    assert r5.parse_ok is True
    assert r5.min_k == 8.0
    assert r5.max_k == 8.0
    assert r5.period == "month"


def test_salary_floor_screening_hits_yuan_under_1000():
    """画像最低月薪 3K 时，'800元/月' 的岗位会被最低月薪粗筛命中（低于薪资底线）。"""
    parsed = parse_salary("800元/月")
    assert parsed.parse_ok is True
    assert parsed.max_k == 0.8

    job_version = {
        "title": "兼职保洁",
        "salary_raw": parsed.raw,
        "salary_visible": 1,
        "salary_parse_ok": int(parsed.parse_ok),
        "salary_min_k": parsed.min_k,
        "salary_max_k": parsed.max_k,
    }
    profile = {
        "min_monthly_k": 3.0,
    }

    # 公开函数 1: rules.screen()
    res: RuleResult = screen(job_version, profile)
    assert res.passed is False
    assert any("薪资上限 0.8K 低于底线 3K" in hit for hit in res.hits)

    # 公开函数 2: rules.screen_hints()
    hints = screen_hints(job_version, profile)
    assert any(h["type"] == "salary_floor" and h["text"] == "低于薪资底线" for h in hints)
