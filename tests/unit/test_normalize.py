from jet.domain.normalize import normalize_city, normalize_text


def test_normalize_text_kangxi_radical():
    # U+2F8F is Kangxi radical 行 ('\u2f8f')
    kangxi = "银\u2f8f"
    res = normalize_text(kangxi)
    assert res == "银行"


def test_normalize_text_full_width():
    full_width = "１２３ＡＢＣ"
    res = normalize_text(full_width)
    assert res == "123ABC"


def test_normalize_text_whitespace():
    raw = "  \t 数据分析师 \n \t  高级  "
    res = normalize_text(raw)
    assert res == "数据分析师 高级"


def test_normalize_text_none():
    assert normalize_text(None) == ""


def test_normalize_text_immutability():
    original = " 原始 文本 "
    res = normalize_text(original)
    assert res == "原始 文本"
    assert original == " 原始 文本 "


def test_normalize_city():
    assert normalize_city("深圳市") == "深圳"
    assert normalize_city("深圳") == "深圳"
    assert normalize_city("  北京市  ") == "北京"
    assert normalize_city(None) == ""
