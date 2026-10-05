"""Experience items domain module (T012).

Manages user personal experience snippets for LLM communication assistant.
Specs:
- specs/003-hr-assistant/spec.md (FR-028-FR-033, FR-031)
- specs/003-hr-assistant/data-model.md (experience_items)
- specs/003-hr-assistant/contracts/local-api.md (Section 6: GET/PUT /v1/experience)
"""

from typing import Any
import sqlite3

from jet.db.store import transaction, utc_now


class ExperienceError(Exception):
    """Base exception for experience validation and domain errors."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


class TooManyItemsError(ExperienceError):
    """Raised when experience items exceed the maximum limit of 10."""

    def __init__(self, message: str = "经历素材最多 10 条") -> None:
        super().__init__("too_many_items", message)


class ContentInvalidError(ExperienceError):
    """Raised when experience item content is empty, whitespace only, or > 200 chars."""

    def __init__(self, message: str = "经历内容经 strip() 后不能为空且不能超过 200 字") -> None:
        super().__init__("content_invalid", message)


class ItemNoInvalidError(ExperienceError):
    """Raised when item_no is not an integer between 1 and 10 or duplicate."""

    def __init__(self, message: str = "条目编号必须为 1 到 10 的整数且不重复") -> None:
        super().__init__("item_no_invalid", message)


def list_experiences(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    """Retrieve all experience items for the user, ordered by item_no ascending."""
    rows = conn.execute(
        """
        SELECT item_no, content, updated_at
        FROM experience_items
        WHERE user_id = ?
        ORDER BY item_no ASC
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "item_no": row["item_no"],
            "content": row["content"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def replace_experiences(conn: sqlite3.Connection, user_id: str, items: Any) -> int:
    """Validate and atomically replace all experience items for the user.

    Constraints:
    - items must be a list.
    - len(items) <= 10, otherwise raises TooManyItemsError (too_many_items).
    - item_no must be int between 1 and 10, unique, otherwise raises ItemNoInvalidError (item_no_invalid).
    - content stripped must be non-empty and len <= 200, otherwise raises ContentInvalidError (content_invalid).
    """
    if not isinstance(items, list):
        raise ExperienceError("invalid_payload", "经历素材提交格式必须为数组")

    if len(items) > 10:
        raise TooManyItemsError("经历素材最多 10 条")

    validated: list[tuple[int, str]] = []
    seen_item_nos: set[int] = set()

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExperienceError("invalid_payload", f"第 {idx + 1} 条经历素材格式错误")

        if "item_no" not in item:
            raise ItemNoInvalidError(f"第 {idx + 1} 条经历素材缺少条目编号 item_no")

        item_no = item["item_no"]
        if not isinstance(item_no, int) or isinstance(item_no, bool):
            raise ItemNoInvalidError("条目编号必须为整数")

        if item_no < 1 or item_no > 10:
            raise ItemNoInvalidError("条目编号必须在 1 到 10 之间")

        if item_no in seen_item_nos:
            raise ItemNoInvalidError(f"条目编号 {item_no} 重复")
        seen_item_nos.add(item_no)

        if "content" not in item:
            raise ContentInvalidError(f"第 {idx + 1} 条经历素材缺少内容 content")

        content = item["content"]
        if not isinstance(content, str):
            raise ContentInvalidError("经历内容必须为字符串")

        cleaned = content.strip()
        if len(cleaned) == 0:
            raise ContentInvalidError("单条经历内容不能为空")

        if len(cleaned) > 200:
            raise ContentInvalidError("单条经历内容不能超过 200 字")

        validated.append((item_no, cleaned))

    now_str = utc_now()

    def _execute_replace() -> None:
        conn.execute("DELETE FROM experience_items WHERE user_id = ?", (user_id,))
        for item_no, cleaned in validated:
            conn.execute(
                """
                INSERT INTO experience_items (user_id, item_no, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, item_no, cleaned, now_str, now_str),
            )

    if conn.in_transaction:
        _execute_replace()
    else:
        with transaction(conn, immediate=True):
            _execute_replace()

    return len(validated)
