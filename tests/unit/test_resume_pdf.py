import io
from pathlib import Path
import pypdf
import pytest

from jet.db.store import open_db, utc_now
from jet.domain.resume import (
    FileTooLargeError,
    NoTextError,
    PdfInvalidError,
    SelfNameRequiredError,
    extract_candidate_names,
    extract_text_from_pdf_bytes,
    normalize_extracted_text,
    sanitize_resume_text,
    save_resume_upload,
    get_resume_slot,
    CJK_RADICAL_MAP,
)


def _make_ascii_pdf(lines: list[str]) -> bytes:
    """用最小内容流在内存中生成标准 ASCII 文字 PDF 字节串。"""
    stream_content = "BT\n/F1 12 Tf\n50 750 Td\n14 TL\n"
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i == 0:
            stream_content += f"({escaped}) Tj\n"
        else:
            stream_content += f"T* ({escaped}) Tj\n"
    stream_content += "ET\n"
    stream_bytes = stream_content.encode("ascii")

    obj1 = b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n"
    obj2 = b"2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
    obj3 = b"3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R>>\nendobj\n"
    obj4 = b"4 0 obj\n<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>\nendobj\n"
    obj5 = f"5 0 obj\n<</Length {len(stream_bytes)}>>\nstream\n".encode("ascii") + stream_bytes + b"endstream\nendobj\n"

    header = b"%PDF-1.4\n"
    pos1 = len(header)
    pos2 = pos1 + len(obj1)
    pos3 = pos2 + len(obj2)
    pos4 = pos3 + len(obj3)
    pos5 = pos4 + len(obj4)
    xref_pos = pos5 + len(obj5)

    xref = (
        f"xref\n0 6\n"
        f"0000000000 65535 f \n"
        f"{pos1:010d} 00000 n \n"
        f"{pos2:010d} 00000 n \n"
        f"{pos3:010d} 00000 n \n"
        f"{pos4:010d} 00000 n \n"
        f"{pos5:010d} 00000 n \n"
    ).encode("ascii")

    trailer = f"trailer\n<</Size 6 /Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


def _make_blank_pdf() -> bytes:
    """生成完全没有文字的空白单页 PDF。"""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_text_from_pdf_success():
    """测试从纯代码生成的文字 PDF 中成功提取出文字 (FR-005, T006)。"""
    lines = [
        "Li Ming",
        "Phone: 13800000000",
        "Email: liming@example.com",
        "ID Card: 110101199001011234",
        "Software Engineer with 6 years experience in Python and FastAPI backend development",
        "Skilled in MySQL, Redis, Kafka, Distributed Systems and Cloud Computing architecture",
    ]
    pdf_bytes = _make_ascii_pdf(lines)
    extracted = extract_text_from_pdf_bytes(pdf_bytes)

    assert "Li Ming" in extracted
    assert "13800000000" in extracted
    assert "liming@example.com" in extracted
    assert "Python and FastAPI" in extracted
    assert len("".join(extracted.split())) >= 50


def test_extract_text_empty_pdf_raises_no_text():
    """测试空白页或有效字符不足 50 时抛出 NoTextError (FR-006, T006)。"""
    blank_bytes = _make_blank_pdf()
    with pytest.raises(NoTextError, match="无法读取文字，请上传文字版 PDF"):
        extract_text_from_pdf_bytes(blank_bytes)


def test_extract_text_too_short_raises_no_text():
    """测试文字少于 50 字符时判定为扫描件或无效 PDF (FR-006)。"""
    short_lines = ["Short resume text", "Only 25 chars"]
    short_pdf = _make_ascii_pdf(short_lines)
    with pytest.raises(NoTextError, match="无法读取文字"):
        extract_text_from_pdf_bytes(short_pdf)


def test_extract_text_corrupted_raises_pdf_invalid():
    """测试损坏或非法格式 PDF 抛出 PdfInvalidError (FR-005, T006)。"""
    corrupt_bytes = b"%PDF-1.4 broken content that is not a valid pdf at all"
    with pytest.raises(PdfInvalidError, match="PDF 文件已加密或已损坏，无法解析"):
        extract_text_from_pdf_bytes(corrupt_bytes)


def test_extract_text_file_too_large():
    """测试文件超过 5MB 报 FileTooLargeError (FR-010, T006)。"""
    large_bytes = b"0" * (5 * 1024 * 1024 + 1)
    with pytest.raises(FileTooLargeError, match="PDF 文件大小不能超过 5MB"):
        extract_text_from_pdf_bytes(large_bytes)


def test_name_extraction_three_sources():
    """测试姓名三来源识别与去重 (FR-007, T007)。"""
    # 1. 来源 A: display_name
    names_a = extract_candidate_names("求职意向：后端开发\n工作经验 5 年...", display_name="李明")
    assert "李明" in names_a

    # 忽略默认占位 'me'
    names_default = extract_candidate_names("工作经验 5 年...", display_name="me")
    assert "me" not in names_default

    # 2. 来源 B: self_name
    names_b = extract_candidate_names("求职意向：前端开发...", self_name="王五")
    assert "王五" in names_b

    # 3. 来源 C: PDF 正文规则补充（首行中文名、空格拆分、分隔符、标签、排除标题）
    chinese_text = "李明\n手机：13800000000\n求职意向：后端架构师"
    names_c1 = extract_candidate_names(chinese_text)
    assert "李明" in names_c1

    # 覆盖"李 明"空格拆分
    names_split = extract_candidate_names("李 明\n手机：13800000000\n求职意向：后端架构师")
    assert "李明" in names_split

    # 覆盖"李明 | 138 0000 0000 | liming@example.com"同一行
    names_same_line = extract_candidate_names("李明 | 138 0000 0000 | liming@example.com\n工作经验5年")
    assert "李明" in names_same_line

    # 规则 2: PDF 正文标签 姓 名：李明（带空白）
    label_text = "求职简历\n姓 名：李明\n联系方式：13800000000"
    names_c2 = extract_candidate_names(label_text)
    assert "李明" in names_c2

    # 首行为"个人简历"（排除标题词，不能作为候选人名）
    title_text = "个人简历\n工作经验 5 年\n无标签人名"
    names_title = extract_candidate_names(title_text)
    assert "个人简历" not in names_title

    # 规则 3: 英文名首行
    eng_text = "Li Ming\nPhone: 13800000000\nSoftware Engineer"
    names_c3 = extract_candidate_names(eng_text)
    assert "Li Ming" in names_c3


def test_sanitize_resume_text_success_and_blocking():
    """测试姓名确认来源检查与强脱敏 (FR-007, FR-008, T007)。"""
    # 1. 确认来源两者都没有（self_name 与 display_name 均缺失） -> 坚决阻断抛出 SelfNameRequiredError
    pure_tech_text = (
        "后端开发专家经验总结\n"
        "电话：13800000000\n"
        "邮箱：dev@example.com\n"
        "负责分布式微服务架构与十万级高并发交易系统优化。"
    )
    with pytest.raises(SelfNameRequiredError, match="没有识别出你的姓名"):
        sanitize_resume_text(pure_tech_text, self_name=None, display_name=None)

    # 1.1 即使正文能提取出姓名（如"姓 名：李明"），但确认来源没有（display_name 为 'me' 且 self_name 为 None），依然必须阻断！
    text_with_pdf_name = (
        "个人简历\n"
        "姓 名：李明\n"
        "电话：13800000000\n"
        "邮箱：liming@example.com\n"
        "负责分布式微服务架构与高并发交易系统优化。"
    )
    with pytest.raises(SelfNameRequiredError, match="没有识别出你的姓名"):
        sanitize_resume_text(text_with_pdf_name, self_name=None, display_name="me")

    # 2. 传入确认来源 self_name 补全后成功脱敏
    sanitized = sanitize_resume_text(pure_tech_text, self_name="李明", display_name=None)
    assert "13800000000" not in sanitized
    assert "dev@example.com" not in sanitized
    assert "[已隐藏]" in sanitized
    assert "分布式微服务架构与十万级高并发交易系统优化" in sanitized

    # 3. 简历中包含 18 位身份证号码与李先生称谓（带确认来源 self_name="李明"）
    resume_with_id = (
        "李明\n"
        "身份证：110101199001011234\n"
        "电话：13800000000\n"
        "李先生精通 Python 异步编程与 PostgreSQL 性能调优。"
    )
    sanitized_id = sanitize_resume_text(resume_with_id, self_name="李明")
    assert "李明" not in sanitized_id
    assert "李先生" not in sanitized_id
    assert "110101199001011234" not in sanitized_id
    assert "13800000000" not in sanitized_id
    assert "Python 异步编程与 PostgreSQL 性能调优" in sanitized_id


def test_resume_formatting_whitespace_and_title_exclusion():
    """测试覆盖 pypdf 常见排版：'李 明' 空格拆分、同行分隔符、'姓 名：李明'、首行'个人简历'。
    断言脱敏结果绝对不含 李明 / 李 明 / 手机号 / 邮箱。
    """
    raw_resume = (
        "个人简历\n"
        "李 明 | 138 0000 0000 | liming@example.com\n"
        "姓 名：李明\n"
        "李\n明 负责微服务核心业务开发，李 明 先生具有深厚架构经验。\n"
        "电话：138-0000-0000 邮箱：liming@example.com"
    )
    # self_name 作为确认来源
    sanitized = sanitize_resume_text(raw_resume, self_name="李明")

    # 严格断言：绝对不含 李明 / 李 明 / 李\n明 / 手机号 / 邮箱
    assert "李明" not in sanitized
    assert "李 明" not in sanitized
    assert "李\n明" not in sanitized
    assert "138 0000 0000" not in sanitized
    assert "138-0000-0000" not in sanitized
    assert "liming@example.com" not in sanitized
    assert "微服务核心业务开发" in sanitized


def test_zero_disk_and_no_db_blob_assertion(data_dir: Path):
    """测试零磁盘写入与零数据库 BLOB 安全断言 (SC-003, 原则 VIII)。"""
    conn = open_db(data_dir)
    user_id = "me"

    raw_text = "李明\n电话：13800000000\n邮箱：liming@example.com\nPython 高级研发工程师架构实战经验"
    sanitized = sanitize_resume_text(raw_text, self_name="李明")

    # 保存简历
    save_resume_upload(
        conn,
        user_id=user_id,
        slot=1,
        name="测试简历1",
        resume_text=sanitized,
        profile="适合方向：Python 后端架构。\n亮点：具备分布式系统实战经验。",
    )

    # 1. 验证数据目录下没有任何 .pdf 文件产生
    pdf_files = list(data_dir.glob("**/*.pdf"))
    assert len(pdf_files) == 0

    # 2. 验证数据库中 resume_text 为 TEXT 类型，非 BLOB
    row = conn.execute("SELECT typeof(resume_text) as t, typeof(profile) as pt FROM resume_slots WHERE user_id = ? AND slot = 1", (user_id,)).fetchone()
    assert row["t"] == "text"
    assert row["pt"] == "text"

    # 3. 验证读取出来的内容为提取后的脱敏文字
    slot_data = get_resume_slot(conn, user_id, 1)
    assert slot_data is not None
    assert "13800000000" not in slot_data["resume_text"]
    assert "liming@example.com" not in slot_data["resume_text"]
    assert "Python 高级研发工程师架构实战经验" in slot_data["resume_text"]

    conn.close()


def test_normalize_extracted_text_kangxi_radicals():
    """测试对含康熙部首兼容字符（如 '⼈' U+2F08、'⽤' U+2F64）的字符串规范化为标准汉字 ('人'、'用')。"""
    raw_str = "前端开发⼯程师，熟练使⽤各类⼯具，具备良好团队协作能⼒与个⼈素养。"
    assert "⼈" in raw_str and "\u2f08" in raw_str
    assert "⽤" in raw_str and "\u2f64" in raw_str

    normalized = normalize_extracted_text(raw_str)
    assert "人" in normalized
    assert "用" in normalized
    assert "⼈" not in normalized
    assert "⽤" not in normalized
    assert "\u2f08" not in normalized
    assert "\u2f64" not in normalized
    assert "熟练使用各类工具" in normalized
    assert "个人素养" in normalized


def test_normalize_and_sanitize_kangxi_radicals_name():
    """测试当 self_name="王大人" 而文字里是康熙部首 "王⼤⼈"（U+2F24 ⼤, U+2F08 ⼈）时，
    规范化+脱敏后不再含该姓名。
    """
    # 构造包含 U+2F24(⼤) 与 U+2F08(⼈) 的简历文字
    raw_text = (
        "王\u2f24\u2f08\n"
        "电话：13800000000\n"
        "邮箱：wangdaren@example.com\n"
        "王\u2f24\u2f08 具备 8 年大型微服务研发经验，负责核心架构演进优化。"
    )
    assert "王⼤⼈" in raw_text
    assert "王\u2f24\u2f08" in raw_text

    # 规范化 + 脱敏
    normalized = normalize_extracted_text(raw_text)
    assert "王大人" in normalized

    sanitized = sanitize_resume_text(normalized, self_name="王大人")
    assert "王大人" not in sanitized
    assert "王⼤⼈" not in sanitized
    assert "王\u2f24\u2f08" not in sanitized
    assert "13800000000" not in sanitized
    assert "wangdaren@example.com" not in sanitized
    assert "[已隐藏]" in sanitized
    assert "具备 8 年大型微服务研发经验" in sanitized


def test_normalize_extracted_text_cjk_radical_supplements():
    """测试 CJK 部首补充区（U+2E80–U+2EFF）字符逐一映射为对应常用汉字。
    测试里使用转义写法，避免编辑器混淆。
    """
    mappings = [
        ("\u2ec5", "见"),
        ("\u2ec6", "角"),
        ("\u2ec9", "贝"),
        ("\u2ecb", "车"),
        ("\u2ed3", "长"),
        ("\u2ed4", "门"),
        ("\u2ed9", "韦"),
        ("\u2eda", "页"),
        ("\u2edb", "风"),
        ("\u2edc", "飞"),
        ("\u2ee0", "食"),
        ("\u2ee2", "马"),
        ("\u2ee5", "鱼"),
        ("\u2ee6", "鸟"),
        ("\u2ee7", "卤"),
        ("\u2ee8", "麦"),
        ("\u2ee9", "黄"),
        ("\u2eea", "黾"),
        ("\u2eec", "齐"),
        ("\u2eee", "齿"),
        ("\u2ef0", "龙"),
        ("\u2ef3", "龟"),
    ]
    assert len(mappings) == 22
    for radical, expected in mappings:
        assert radical in CJK_RADICAL_MAP
        assert CJK_RADICAL_MAP[radical] == expected
        assert normalize_extracted_text(radical) == expected
        assert normalize_extracted_text(f"测试{radical}字符") == f"测试{expected}字符"

    # "增⻓" 规范化为 "增长"
    assert normalize_extracted_text("增\u2ed3") == "增长"


def test_normalize_and_sanitize_cjk_radical_names():
    """测试 CJK 部首字符在姓名中的规范化与脱敏：
    1. self_name="黄丽" 而文字里是 "⻩丽"（\u2ee9丽），规范化+脱敏后不再出现该姓名；
    2. self_name="马明" 而文字里是 "⻢ 明"（\u2ee2 明），规范化+脱敏后不再出现该姓名。
    """
    # 1. self_name="黄丽"，文字中为 "\u2ee9丽"
    raw_text_huang = (
        "\u2ee9丽\n"
        "电话：13800000000\n"
        "邮箱：huangli@example.com\n"
        "\u2ee9丽 担任高级产品经理，负责用户增长与数据分析。"
    )
    normalized_huang = normalize_extracted_text(raw_text_huang)
    assert "黄丽" in normalized_huang
    assert "\u2ee9" not in normalized_huang
    sanitized_huang = sanitize_resume_text(normalized_huang, self_name="黄丽")
    assert "黄丽" not in sanitized_huang
    assert "\u2ee9丽" not in sanitized_huang
    assert "⻩丽" not in sanitized_huang
    assert "\u2ee9" not in sanitized_huang

    # 2. self_name="马明"，文字中为 "\u2ee2 明"
    raw_text_ma = (
        "\u2ee2 明\n"
        "电话：13900000000\n"
        "邮箱：maming@example.com\n"
        "\u2ee2 明 负责全栈架构研发，推动系统架构演进。"
    )
    normalized_ma = normalize_extracted_text(raw_text_ma)
    assert "马 明" in normalized_ma
    assert "\u2ee2" not in normalized_ma
    sanitized_ma = sanitize_resume_text(normalized_ma, self_name="马明")
    assert "马明" not in sanitized_ma
    assert "马 明" not in sanitized_ma
    assert "\u2ee2 明" not in sanitized_ma
    assert "⻢ 明" not in sanitized_ma
    assert "\u2ee2" not in sanitized_ma
