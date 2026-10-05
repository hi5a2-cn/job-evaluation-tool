"""我的简历 (resume_slots) 领域模块 (009-resume-pdf)。

管理用户在设置页上传的简历 PDF 与 AI 提炼的简历画像。
PDF 字节仅在内存流式处理，零磁盘写入、零数据库 BLOB、零日志打印。
保存在本机数据库 resume_slots 表，按用户隔离。
"""

import io
import re
import sqlite3
from typing import Any
import unicodedata
import pypdf
import pypdf.errors

from jet.db.store import transaction, utc_now
from jet.llm.sanitize import get_sensitive_name_terms, sanitize_resume_content, sanitize_text
from jet.logging import get_logger

logger = get_logger(__name__)

MAX_PDF_SIZE_BYTES: int = 5 * 1024 * 1024  # 5MB


class ResumeError(Exception):
    """简历设置异常基类。"""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


class TooManyResumesError(ResumeError):
    def __init__(self, message: str = "简历最多 3 份") -> None:
        super().__init__("too_many_resumes", message)


class ResumeInvalidError(ResumeError):
    def __init__(self, message: str = "简历名称与画像不能为空") -> None:
        super().__init__("resume_invalid", message)


class ResumeSlotInvalidError(ResumeError):
    def __init__(self, message: str = "简历编号必须为 1 到 3 的整数且不重复") -> None:
        super().__init__("slot_invalid", message)


class NoTextError(ResumeError):
    def __init__(self, message: str = "无法读取文字，请上传文字版 PDF") -> None:
        super().__init__("no_text", message)


class PdfInvalidError(ResumeError):
    def __init__(self, message: str = "PDF 文件已加密或已损坏，无法解析") -> None:
        super().__init__("pdf_invalid", message)


class SelfNameRequiredError(ResumeError):
    def __init__(self, message: str = "没有识别出你的姓名，请在上传区填写姓名后重试（只在本机用于去掉姓名）") -> None:
        super().__init__("self_name_required", message)


class FileTooLargeError(ResumeError):
    def __init__(self, message: str = "PDF 文件大小不能超过 5MB") -> None:
        super().__init__("file_too_large", message)


class ResumeNotFoundError(ResumeError):
    def __init__(self, message: str = "该简历槽位未上传过文字版简历，请先上传 PDF") -> None:
        super().__init__("resume_not_found", message)


# CJK 部首补充区（U+2E80–U+2EFF）到常用汉字的显式映射表。
# 原因：Unicode NFKC 规范化不处理"CJK 部首补充"区（U+2E80–U+2EFF），
# pypdf 从部分 PDF 提取中文时可能把"马、黄、龙、齐、韦"等常用字（也是常见姓氏）提取成这些部首字符，
# 导致姓名脱敏匹配与文本分析失败。在此定义显式常量表，在 NFKC 规范化之后将其映射为对应常用汉字。
CJK_RADICAL_MAP: dict[str, str] = {
    "\u2ec5": "见",  # ⻅ (U+2EC5) -> 见
    "\u2ec6": "角",  # ⻆ (U+2EC6) -> 角
    "\u2ec9": "贝",  # ⻉ (U+2EC9) -> 贝
    "\u2ecb": "车",  # ⻋ (U+2ECB) -> 车
    "\u2ed3": "长",  # ⻓ (U+2ED3) -> 长
    "\u2ed4": "门",  # ⻔ (U+2ED4) -> 门
    "\u2ed9": "韦",  # ⻙ (U+2ED9) -> 韦
    "\u2eda": "页",  # ⻚ (U+2EDA) -> 页
    "\u2edb": "风",  # ⻛ (U+2EDB) -> 风
    "\u2edc": "飞",  # ⻜ (U+2EDC) -> 飞
    "\u2ee0": "食",  # ⻠ (U+2EE0) -> 食
    "\u2ee2": "马",  # ⻢ (U+2EE2) -> 马
    "\u2ee5": "鱼",  # ⻥ (U+2EE5) -> 鱼
    "\u2ee6": "鸟",  # ⻦ (U+2EE6) -> 鸟
    "\u2ee7": "卤",  # ⻧ (U+2EE7) -> 卤
    "\u2ee8": "麦",  # ⻨ (U+2EE8) -> 麦
    "\u2ee9": "黄",  # ⻩ (U+2EE9) -> 黄
    "\u2eea": "黾",  # ⻪ (U+2EEA) -> 黾
    "\u2eec": "齐",  # ⻬ (U+2EEC) -> 齐
    "\u2eee": "齿",  # ⻮ (U+2EEE) -> 齿
    "\u2ef0": "龙",  # ⻰ (U+2EF0) -> 龙
    "\u2ef3": "龟",  # ⻳ (U+2EF3) -> 龟
}

CJK_RADICAL_TRANSLATE_TABLE = str.maketrans(CJK_RADICAL_MAP)


def normalize_extracted_text(text: str) -> str:
    """对从 PDF 提取出的文字进行 Unicode NFKC 规范化并映射 CJK 部首补充字符 (FR-005)。

    1. NFKC 修复 pypdf 从部分 PDF 提取中文时出现的康熙部首兼容字符（例如 '⼈' U+2F08 代替 '人'、'⽤' U+2F64 代替 '用'）。
    2. NFKC 不处理"CJK 部首补充"区（U+2E80–U+2EFF），在 NFKC 之后通过 str.translate 将常见部首字符
       （例如 '⻢' 代替 '马'、'⻩' 代替 '黄'）映射为对应常用汉字，避免姓名脱敏与关键词匹配失效。
    """
    nfkc_text = unicodedata.normalize("NFKC", text)
    return nfkc_text.translate(CJK_RADICAL_TRANSLATE_TABLE)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """纯内存中解析 PDF 二进制流并提取全部页面文字 (FR-005, FR-006, T006)。
    零磁盘写入、零数据库 BLOB。
    """
    if len(pdf_bytes) > MAX_PDF_SIZE_BYTES:
        raise FileTooLargeError("PDF 文件大小不能超过 5MB")

    try:
        stream = io.BytesIO(pdf_bytes)
        reader = pypdf.PdfReader(stream)
        if reader.is_encrypted:
            try:
                decrypt_result = reader.decrypt("")
                if decrypt_result == pypdf.constants.PasswordType.NOT_DECRYPTED:
                    raise PdfInvalidError("PDF 文件已加密或已损坏，无法解析")
            except Exception as e:
                raise PdfInvalidError("PDF 文件已加密或已损坏，无法解析") from e

        pages_text: list[str] = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            pages_text.append(txt)
        full_text = "\n".join(pages_text).strip()
    except (pypdf.errors.PdfReadError, Exception) as e:
        if isinstance(e, ResumeError):
            raise
        raise PdfInvalidError("PDF 文件已加密或已损坏，无法解析") from e

    full_text = normalize_extracted_text(full_text)

    # 校验去除空白后的有效文字数 >= 50
    non_space_chars = "".join(full_text.split())
    if len(non_space_chars) < 50:
        raise NoTextError("无法读取文字，请上传文字版 PDF")

    return full_text


EXCLUDED_TITLES: frozenset[str] = frozenset(
    {
        "个人简历",
        "简历",
        "求职简历",
        "个人信息",
        "基本信息",
        "求职意向",
        "联系方式",
        "RESUME",
        "CV",
        "CURRICULUMVITAE",
        "PERSONALRESUME",
    }
)


def _is_excluded_title(text: str) -> bool:
    no_space = "".join(text.split()).upper()
    return no_space in EXCLUDED_TITLES


NAME_LABEL_PATTERN = re.compile(
    r"姓[ \t]*名[ \t]*[:：][ \t]*([\u4e00-\u9fa5](?:[ \t]*[\u4e00-\u9fa5]){1,3})(?![ \t]*[\u4e00-\u9fa5])"
)
ENGLISH_NAME_LINE_PATTERN = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}$")


def extract_candidate_names(
    text: str,
    self_name: str | None = None,
    display_name: str | None = None,
) -> list[str]:
    r"""从三个来源提取求职者姓名候选词并去重 (FR-007, T007)。

    来源 A: users.display_name（若非空且不是默认占位 'me'）
    来源 B: self_name（上传接口传入的用户可选姓名）
    来源 C: PDF 正文规则提取补充（'姓\s*名\s*[:：]' 正则，及非空首行 2–4 汉字/大写英文词）
    """
    candidates: set[str] = set()

    # 来源 A: display_name
    if display_name:
        clean_disp = display_name.strip()
        if clean_disp and clean_disp.lower() != "me":
            candidates.add(clean_disp)

    # 来源 B: self_name
    if self_name:
        clean_self = self_name.strip()
        if clean_self:
            candidates.add(clean_self)

    # 来源 C: PDF 文本规则补充
    # 规则 1: 姓名标签 (姓\s*名\s*[:：]\s* 后的 2–4 个汉字，中间可有空白)
    for m in NAME_LABEL_PATTERN.finditer(text):
        cand = "".join(m.group(1).split())
        if len(cand) in (2, 3, 4) and not _is_excluded_title(cand):
            candidates.add(cand)

    # 规则 2: 非空首行检查 (取第一个分隔符前的片段，若为 2–4 个汉字且非排除标题才作为候选)
    for line in text.splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        if _is_excluded_title(clean_line):
            break

        # 首行检查：先看首部 2-4 汉字片段（在分隔符空白、|、｜、/、·、,、，或行尾之前）
        m_head = re.match(
            r"^([\u4e00-\u9fa5](?:[ \t]*[\u4e00-\u9fa5]){1,3})(?:[ \t|｜/·,，]|$)",
            clean_line,
        )
        if m_head:
            cand = "".join(m_head.group(1).split())
            if len(cand) in (2, 3, 4) and not _is_excluded_title(cand):
                candidates.add(cand)
        elif ENGLISH_NAME_LINE_PATTERN.match(clean_line) and not _is_excluded_title(clean_line):
            candidates.add(clean_line)
        break

    return sorted(candidates, key=len, reverse=True)


def sanitize_resume_text(
    text: str,
    self_name: str | None = None,
    display_name: str | None = None,
) -> str:
    """执行姓名三来源融合与强脱敏调度 (FR-007, FR-008, T007)。

    确认来源 = self_name 或 users.display_name（非空且不是 'me'）。
    两者都没有 → 抛 SelfNameRequiredError，坚决阻断外发，不调用大模型。
    PDF 里识别出的姓名只作为额外脱敏词（补充），不能单独让检查通过。
    """
    confirmed_names: set[str] = set()
    if self_name:
        clean_self = self_name.strip()
        if clean_self:
            confirmed_names.add(clean_self)
    if display_name:
        clean_disp = display_name.strip()
        if clean_disp and clean_disp.lower() != "me":
            confirmed_names.add(clean_disp)

    if not confirmed_names:
        raise SelfNameRequiredError("没有识别出你的姓名，请在上传区填写姓名后重试（只在本机用于去掉姓名）")

    candidate_names = extract_candidate_names(text, self_name=self_name, display_name=display_name)

    sensitive_terms: set[str] = set()
    for name in candidate_names:
        terms = get_sensitive_name_terms(None, name)
        sensitive_terms.update(terms)

    sorted_terms = sorted(sensitive_terms, key=len, reverse=True)
    return sanitize_resume_content(text, sorted_terms)


def list_resumes(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    """按用户读取所有简历，按 slot 升序返回 (FR-012, FR-016)。
    包含 slot, name, profile, has_text, text_chars, updated_at。
    绝不返回 resume_text 全文。
    """
    rows = conn.execute(
        """
        SELECT slot, name, resume_text, profile, updated_at
        FROM resume_slots
        WHERE user_id = ?
        ORDER BY slot ASC
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "slot": row["slot"],
            "name": row["name"],
            "profile": row["profile"],
            "has_text": bool(row["resume_text"]),
            "text_chars": len(row["resume_text"]) if row["resume_text"] else 0,
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_resume_slot(conn: sqlite3.Connection, user_id: str, slot: int) -> dict[str, Any] | None:
    """获取指定 slot 简历的完整记录（包含 resume_text），仅供内部提炼与重生成使用。"""
    row = conn.execute(
        "SELECT slot, name, resume_text, profile, updated_at FROM resume_slots WHERE user_id = ? AND slot = ?",
        (user_id, slot),
    ).fetchone()
    if not row:
        return None
    return {
        "slot": row["slot"],
        "name": row["name"],
        "resume_text": row["resume_text"],
        "profile": row["profile"],
        "updated_at": row["updated_at"],
    }


def save_resume_upload(
    conn: sqlite3.Connection,
    user_id: str,
    slot: int,
    name: str,
    resume_text: str,
    profile: str,
) -> None:
    """保存上传或重新生成的简历槽位 (FR-010, FR-011)。"""
    now_str = utc_now()
    with transaction(conn, immediate=True):
        conn.execute(
            """
            INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, slot) DO UPDATE SET
                name = excluded.name,
                resume_text = excluded.resume_text,
                profile = excluded.profile,
                updated_at = excluded.updated_at
            """,
            (user_id, slot, name, resume_text, profile, now_str),
        )


def save_resumes(conn: sqlite3.Connection, user_id: str, items: Any) -> int:
    """原子更新用户的简历设置 (最多 3 份) (FR-013)。
    支持保存用户对名称和画像的手动修改（profile ≤ 300 字且非空），保持底层已保存的 resume_text 不变。
    """
    if isinstance(items, dict):
        if "items" in items:
            items = items["items"]
        elif "resumes" in items:
            items = items["resumes"]

    if not isinstance(items, list):
        raise ResumeError("invalid_payload", "简历数据提交格式必须为数组")

    if len(items) > 3:
        raise TooManyResumesError("简历最多 3 份")

    validated: list[tuple[int, str, str]] = []
    seen_slots: set[int] = set()

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ResumeError("invalid_payload", f"第 {idx + 1} 份简历格式错误")

        raw_slot = item.get("slot")
        if raw_slot is None:
            slot = idx + 1
        else:
            if not isinstance(raw_slot, int) or isinstance(raw_slot, bool):
                raise ResumeSlotInvalidError("简历编号必须为整数")
            slot = raw_slot

        if slot not in (1, 2, 3):
            raise ResumeSlotInvalidError("简历编号必须为 1、2 或 3")

        if slot in seen_slots:
            raise ResumeSlotInvalidError(f"简历编号 {slot} 重复")
        seen_slots.add(slot)

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ResumeInvalidError(f"第 {slot} 份简历名称不能为空")
        clean_name = name.strip()
        if len(clean_name) > 100:
            raise ResumeInvalidError("简历名称不能超过 100 字")

        profile = item.get("profile")
        if profile is None:
            profile = item.get("job_types")
        if not isinstance(profile, str) or not profile.strip():
            raise ResumeInvalidError(f"第 {slot} 份简历画像不能为空")
        clean_profile = profile.strip()
        if len(clean_profile) > 300:
            raise ResumeInvalidError(f"第 {slot} 份简历画像不能超过 300 字")

        validated.append((slot, clean_name, clean_profile))

    validated.sort(key=lambda x: x[0])
    now_str = utc_now()

    def _execute():
        existing = conn.execute(
            "SELECT slot, resume_text FROM resume_slots WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        saved_texts = {r["slot"]: r["resume_text"] for r in existing}

        submitted_slots = [s for s, _, _ in validated]
        if submitted_slots:
            placeholders = ",".join("?" for _ in submitted_slots)
            conn.execute(
                f"DELETE FROM resume_slots WHERE user_id = ? AND slot NOT IN ({placeholders})",
                [user_id, *submitted_slots],
            )
        else:
            conn.execute("DELETE FROM resume_slots WHERE user_id = ?", (user_id,))

        for slot, name, profile in validated:
            r_text = saved_texts.get(slot)
            if slot in saved_texts:
                conn.execute(
                    """
                    UPDATE resume_slots
                    SET name = ?, profile = ?, updated_at = ?
                    WHERE user_id = ? AND slot = ?
                    """,
                    (name, profile, now_str, user_id, slot),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, slot, name, r_text, profile, now_str),
                )

    if conn.in_transaction:
        _execute()
    else:
        with transaction(conn, immediate=True):
            _execute()

    return len(validated)


def delete_resume_slot(conn: sqlite3.Connection, user_id: str, slot: int) -> bool:
    """彻底物理删除指定 slot 的记录（名称、提取文字、画像均清除）(FR-014)。"""
    if slot not in (1, 2, 3):
        raise ResumeSlotInvalidError("简历编号必须为 1、2 或 3")

    def _execute():
        conn.execute("DELETE FROM resume_slots WHERE user_id = ? AND slot = ?", (user_id, slot))

    if conn.in_transaction:
        _execute()
    else:
        with transaction(conn, immediate=True):
            _execute()

    return True


def resolve_resume_suggestion(
    conn: sqlite3.Connection | None,
    user_id: str | None,
    slot: Any,
    reason: str | None,
) -> dict[str, Any] | None:
    """供 API 装配简历建议 (FR-004)。
    解析判断中的 slot，按当前用户的 resume_slots 解析；
    若 slot 无效、理由为空或编号无对应简历，则返回 None。
    """
    if slot is None or reason is None or not str(reason).strip():
        return None
    if not conn or not user_id:
        return None

    slot_str = str(slot).strip()
    if slot_str.startswith("简历"):
        slot_str = slot_str[2:].strip()
    try:
        slot_num = int(slot_str)
    except ValueError:
        return None

    if slot_num not in (1, 2, 3):
        return None

    row = conn.execute(
        "SELECT slot, name FROM resume_slots WHERE user_id = ? AND slot = ?",
        (user_id, slot_num),
    ).fetchone()
    if not row:
        return None

    clean_reason = str(reason).strip()
    if len(clean_reason) > 40:
        clean_reason = clean_reason[:40]

    return {
        "slot": slot_num,
        "name": row["name"],
        "reason": clean_reason,
    }
