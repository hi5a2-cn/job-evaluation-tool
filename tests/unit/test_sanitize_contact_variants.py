import pytest

from jet.llm.sanitize import PLACEHOLDER, sanitize_resume_content, sanitize_text


@pytest.mark.parametrize(
    ("raw_text", "expected_text"),
    [
        # 1. 全角数字手机号
        ("手机号：１３８００１３８０００", f"手机号：{PLACEHOLDER}"),
        # 2. 点号分隔或中间有两个空格的手机号
        ("电话：138.0013.8000", f"电话：{PLACEHOLDER}"),
        ("电话：138  0013  8000", f"电话：{PLACEHOLDER}"),
        # 3. 微信的其他写法
        ("加微：lm_12345", f"加微：{PLACEHOLDER}"),
        ("V信 lm_12345", f"V信 {PLACEHOLDER}"),
        ("v信：lm_12345", f"v信：{PLACEHOLDER}"),
        ("加v lm_12345", f"加v {PLACEHOLDER}"),
        ("+v: lm_12345", f"+v: {PLACEHOLDER}"),
        ("微: lm_12345", f"微: {PLACEHOLDER}"),
        ("WeChat: lm_12345", f"WeChat: {PLACEHOLDER}"),
        ("wechat：lm_12345", f"wechat：{PLACEHOLDER}"),
        # 4. 带点号的微信号
        ("微信：li.ming2024", f"微信：{PLACEHOLDER}"),
        # 5. 全角 ＠ 的邮箱
        ("邮箱：liming＠example.com", f"邮箱：{PLACEHOLDER}"),
    ],
)
def test_contact_variants_masked_in_sanitize_text(raw_text: str, expected_text: str):
    """测试聊天消息脱敏 (sanitize_text) 兼容 1-5 各类联系方式变体，联系方式被隐藏且关键词保留。"""
    assert sanitize_text(raw_text, []) == expected_text


@pytest.mark.parametrize(
    ("raw_text", "expected_text"),
    [
        # 1. 全角数字手机号
        ("手机号：１３８００１３８０００", f"手机号：{PLACEHOLDER}"),
        # 2. 点号分隔或中间有两个空格的手机号
        ("电话：138.0013.8000", f"电话：{PLACEHOLDER}"),
        ("电话：138  0013  8000", f"电话：{PLACEHOLDER}"),
        # 3. 微信的其他写法
        ("加微：lm_12345", f"加微：{PLACEHOLDER}"),
        ("V信 lm_12345", f"V信 {PLACEHOLDER}"),
        ("v信：lm_12345", f"v信：{PLACEHOLDER}"),
        ("加v lm_12345", f"加v {PLACEHOLDER}"),
        ("+v: lm_12345", f"+v: {PLACEHOLDER}"),
        ("微: lm_12345", f"微: {PLACEHOLDER}"),
        ("WeChat: lm_12345", f"WeChat: {PLACEHOLDER}"),
        ("wechat：lm_12345", f"wechat：{PLACEHOLDER}"),
        # 4. 带点号的微信号
        ("微信：li.ming2024", f"微信：{PLACEHOLDER}"),
        # 5. 全角 ＠ 的邮箱
        ("邮箱：liming＠example.com", f"邮箱：{PLACEHOLDER}"),
    ],
)
def test_contact_variants_masked_in_sanitize_resume(raw_text: str, expected_text: str):
    """测试简历正文脱敏 (sanitize_resume_content) 兼容 1-5 各类联系方式变体，联系方式被隐藏且关键词保留。"""
    assert sanitize_resume_content(raw_text, []) == expected_text


@pytest.mark.parametrize(
    "normal_text",
    [
        "期望薪资：15-25K",
        "更新日期：2026.10.05",
        "2026年毕业生",
        "1998年出生",
        "岗位编号：JOB-12345",
        "负责 v8 引擎优化与性能调优",
        "熟练使用 Vue、React 进行前端开发",
        "具备 microservice 微服务架构设计经验",
        "参与 event driver 驱动研发，have rich experience",
    ],
)
def test_no_false_positive_on_normal_content(normal_text: str):
    """确保脱敏规则不会误伤薪资、日期、年份、岗位编号、版本号以及普通英文单词中的 v。"""
    assert sanitize_text(normal_text, []) == normal_text
    assert sanitize_resume_content(normal_text, []) == normal_text
