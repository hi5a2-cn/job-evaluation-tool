from jet.domain.salary import parse_salary


def test_k_range():
    res = parse_salary("6-8K")
    assert res.parse_ok is True
    assert res.min_k == 6.0
    assert res.max_k == 8.0
    assert res.period == "month"
    assert res.months == 12

    res_lower = parse_salary("10-15k")
    assert res_lower.parse_ok is True
    assert res_lower.min_k == 10.0
    assert res_lower.max_k == 15.0
    assert res_lower.months == 12


def test_months_count():
    res1 = parse_salary("15-20K·13薪")
    assert res1.parse_ok is True
    assert res1.min_k == 15.0
    assert res1.max_k == 20.0
    assert res1.months == 13

    res2 = parse_salary("20-30K*14薪")
    assert res2.parse_ok is True
    assert res2.min_k == 20.0
    assert res2.max_k == 30.0
    assert res2.months == 14

    res3 = parse_salary("15-25K·14薪")
    assert res3.parse_ok is True
    assert res3.min_k == 15.0
    assert res3.max_k == 25.0
    assert res3.months == 14


def test_wan_monthly_and_annual():
    # Upper bound <= 5万 -> monthly
    res1 = parse_salary("1-1.5万")
    assert res1.parse_ok is True
    assert res1.min_k == 10.0
    assert res1.max_k == 15.0
    assert res1.period == "month"

    res_wan1 = parse_salary("1万-2万")
    assert res_wan1.parse_ok is True
    assert res_wan1.min_k == 10.0
    assert res_wan1.max_k == 20.0
    assert res_wan1.period == "month"

    res_wan2 = parse_salary("1.5万-2万")
    assert res_wan2.parse_ok is True
    assert res_wan2.min_k == 15.0
    assert res_wan2.max_k == 20.0
    assert res_wan2.period == "month"

    # Explicit annual
    res_annual = parse_salary("24-36万/年")
    assert res_annual.parse_ok is True
    assert res_annual.min_k == 20.0
    assert res_annual.max_k == 30.0
    assert res_annual.period == "year"

    # Ambiguous without year/month marker: 5-8万 -> parse_ok=False
    res_ambiguous = parse_salary("5-8万")
    assert res_ambiguous.parse_ok is False
    assert res_ambiguous.min_k is None
    assert res_ambiguous.max_k is None

    # Lower bound >= 10万 without marker -> annual
    res_high_annual = parse_salary("12-15万")
    assert res_high_annual.parse_ok is True
    assert res_high_annual.period == "year"
    assert res_high_annual.min_k == 10.0
    assert res_high_annual.max_k == 12.5


def test_daily_and_hourly():
    # 200-300元/天 -> 200*21.75/1000 = 4.35, 300*21.75/1000 = 6.525
    res_day = parse_salary("200-300元/天")
    assert res_day.parse_ok is True
    assert res_day.min_k == 4.35
    assert res_day.max_k == 6.525
    assert res_day.period == "day"

    res_day2 = parse_salary("100-150元/天")
    assert res_day2.parse_ok is True
    assert res_day2.min_k == 2.175
    assert res_day2.max_k == 3.2625
    assert res_day2.period == "day"

    # 30-50元/时 -> 30*8*21.75/1000 = 5.22, 50*8*21.75/1000 = 8.7
    res_hour = parse_salary("30-50元/时")
    assert res_hour.parse_ok is True
    assert res_hour.min_k == 5.22
    assert res_hour.max_k == 8.7
    assert res_hour.period == "hour"

    res_hour2 = parse_salary("25-35元/小时")
    assert res_hour2.parse_ok is True
    assert res_hour2.min_k == 4.35
    assert res_hour2.max_k == 6.09
    assert res_hour2.period == "hour"


def test_pure_integers():
    res = parse_salary("6000-8000")
    assert res.parse_ok is True
    assert res.min_k == 6.0
    assert res.max_k == 8.0

    # 外币不折算为人民币：无法解析
    res_currency = parse_salary("$4000-$6000/month")
    assert res_currency.parse_ok is False
    assert res_currency.min_k is None


def test_non_salary_fallbacks_rejected():
    # Single numbers or non-salary text should NOT be parsed as salary
    assert parse_salary("招5人").parse_ok is False
    assert parse_salary("5人").parse_ok is False
    assert parse_salary("3-5人").parse_ok is False
    assert parse_salary("纯文字测试").parse_ok is False
    assert parse_salary("经验不限").parse_ok is False


def test_negotiable_and_empty():
    assert parse_salary("面议").parse_ok is False
    assert parse_salary("薪资面议").parse_ok is False
    assert parse_salary("").parse_ok is False
    assert parse_salary("   ").parse_ok is False
    assert parse_salary(None).parse_ok is False
