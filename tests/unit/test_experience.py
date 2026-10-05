"""Unit tests for experience domain logic (T011).

Specs:
- specs/003-hr-assistant/spec.md (FR-028-FR-033, FR-031)
- specs/003-hr-assistant/data-model.md (experience_items)
- specs/003-hr-assistant/contracts/local-api.md (Section 6: GET/PUT /v1/experience)
- specs/003-hr-assistant/tasks.md (T011, T012)
"""

from pathlib import Path
import pytest

from jet.db.store import open_db
from jet.domain.experience import (
    ContentInvalidError,
    ExperienceError,
    ItemNoInvalidError,
    TooManyItemsError,
    list_experiences,
    replace_experiences,
)


def test_list_experiences_empty(data_dir: Path):
    """新用户经历素材初始为空。"""
    conn = open_db(data_dir)
    try:
        items = list_experiences(conn, "me")
        assert items == []
    finally:
        conn.close()


def test_replace_experiences_success(data_dir: Path):
    """正常保存多条经历素材，并按 item_no 升序读取。"""
    conn = open_db(data_dir)
    try:
        raw_items = [
            {
                "item_no": 2,
                "content": "独立带过 3 人的海外客户成功小团队，负责售后技术工单跟进，客户满意度提升 15%。",
            },
            {
                "item_no": 1,
                "content": "在新能源外贸公司负责西非市场的逆变器渠道拓展，主导拜访了 10 余家本地分销商，实现季度销售额翻倍。",
            },
        ]
        count = replace_experiences(conn, "me", raw_items)
        assert count == 2

        items = list_experiences(conn, "me")
        assert len(items) == 2
        # 严格按 item_no 升序排列
        assert items[0]["item_no"] == 1
        assert items[0]["content"] == raw_items[1]["content"]
        assert isinstance(items[0]["updated_at"], str)

        assert items[1]["item_no"] == 2
        assert items[1]["content"] == raw_items[0]["content"]
        assert isinstance(items[1]["updated_at"], str)

        # 校验编号集合
        ids = {item["item_no"] for item in list_experiences(conn, "me")}
        assert ids == {1, 2}
    finally:
        conn.close()


def test_replace_experiences_atomic_overwrite(data_dir: Path):
    """全量替换保存：旧数据全部清除，新数据生效。"""
    conn = open_db(data_dir)
    try:
        # 初始写入 2 条 (item 1, 2)
        replace_experiences(
            conn,
            "me",
            [
                {"item_no": 1, "content": "旧经历 1"},
                {"item_no": 2, "content": "旧经历 2"},
            ],
        )
        assert len(list_experiences(conn, "me")) == 2

        # 全量替换为 1 条 (item 3)
        count = replace_experiences(
            conn,
            "me",
            [
                {"item_no": 3, "content": "新经历 3"},
            ],
        )
        assert count == 1

        items = list_experiences(conn, "me")
        assert len(items) == 1
        assert items[0]["item_no"] == 3
        assert items[0]["content"] == "新经历 3"
        assert {item["item_no"] for item in list_experiences(conn, "me")} == {3}
    finally:
        conn.close()


def test_replace_experiences_clear_all(data_dir: Path):
    """传入空列表全量清空经历素材。"""
    conn = open_db(data_dir)
    try:
        replace_experiences(
            conn,
            "me",
            [
                {"item_no": 1, "content": "经历素材 1"},
            ],
        )
        assert len(list_experiences(conn, "me")) == 1

        count = replace_experiences(conn, "me", [])
        assert count == 0

        assert list_experiences(conn, "me") == []
        assert {item["item_no"] for item in list_experiences(conn, "me")} == set()
    finally:
        conn.close()


def test_replace_experiences_max_10_items(data_dir: Path):
    """最多 10 条经历素材，超过 10 条抛出 TooManyItemsError。"""
    conn = open_db(data_dir)
    try:
        # 10 条正常保存
        items_10 = [{"item_no": i, "content": f"工作经历第 {i} 条描述内容"} for i in range(1, 11)]
        count = replace_experiences(conn, "me", items_10)
        assert count == 10
        assert len(list_experiences(conn, "me")) == 10

        # 11 条抛出异常并保证原有数据不被损坏
        items_11 = [{"item_no": i, "content": f"工作经历第 {i} 条描述内容"} for i in range(1, 12)]
        with pytest.raises(TooManyItemsError) as exc_info:
            replace_experiences(conn, "me", items_11)
        assert exc_info.value.error == "too_many_items"

        # 原有 10 条依然完整
        assert len(list_experiences(conn, "me")) == 10
    finally:
        conn.close()


def test_replace_experiences_content_validation(data_dir: Path):
    """单条经历内容校验：空内容、纯空白拦截，超 200 字拦截，≤ 200 字正常。"""
    conn = open_db(data_dir)
    try:
        # 空字符串
        with pytest.raises(ContentInvalidError) as exc_info:
            replace_experiences(conn, "me", [{"item_no": 1, "content": ""}])
        assert exc_info.value.error == "content_invalid"

        # 纯空白字符（空格、换行、制表符）
        with pytest.raises(ContentInvalidError) as exc_info:
            replace_experiences(conn, "me", [{"item_no": 1, "content": "   \n\t  "}])
        assert exc_info.value.error == "content_invalid"

        # None 或非字符串
        with pytest.raises(ContentInvalidError):
            replace_experiences(conn, "me", [{"item_no": 1, "content": None}])  # type: ignore

        # 恰好 200 字 -> 成功
        content_200 = "字" * 200
        count = replace_experiences(conn, "me", [{"item_no": 1, "content": content_200}])
        assert count == 1
        items = list_experiences(conn, "me")
        assert items[0]["content"] == content_200

        # 201 字 -> 失败
        content_201 = "字" * 201
        with pytest.raises(ContentInvalidError) as exc_info:
            replace_experiences(conn, "me", [{"item_no": 1, "content": content_201}])
        assert exc_info.value.error == "content_invalid"

        # strip 之后保存
        count = replace_experiences(conn, "me", [{"item_no": 1, "content": "  前有空格后有空格  "}])
        assert count == 1
        items = list_experiences(conn, "me")
        assert items[0]["content"] == "前有空格后有空格"
    finally:
        conn.close()


def test_replace_experiences_item_no_validation(data_dir: Path):
    """item_no 必须为 1 到 10 的整数且不重复。"""
    conn = open_db(data_dir)
    try:
        # item_no < 1
        with pytest.raises(ItemNoInvalidError) as exc_info:
            replace_experiences(conn, "me", [{"item_no": 0, "content": "正常经历"}])
        assert exc_info.value.error == "item_no_invalid"

        # item_no > 10
        with pytest.raises(ItemNoInvalidError) as exc_info:
            replace_experiences(conn, "me", [{"item_no": 11, "content": "正常经历"}])
        assert exc_info.value.error == "item_no_invalid"

        # item_no 负数
        with pytest.raises(ItemNoInvalidError):
            replace_experiences(conn, "me", [{"item_no": -1, "content": "正常经历"}])

        # item_no 重复
        with pytest.raises(ItemNoInvalidError) as exc_info:
            replace_experiences(
                conn,
                "me",
                [
                    {"item_no": 1, "content": "经历 A"},
                    {"item_no": 1, "content": "经历 B"},
                ],
            )
        assert exc_info.value.error == "item_no_invalid"

        # item_no 类型错误（字符串或布尔值）
        with pytest.raises(ItemNoInvalidError):
            replace_experiences(conn, "me", [{"item_no": "1", "content": "正常经历"}])  # type: ignore
        with pytest.raises(ItemNoInvalidError):
            replace_experiences(conn, "me", [{"item_no": True, "content": "正常经历"}])  # type: ignore
    finally:
        conn.close()


def test_replace_experiences_payload_type(data_dir: Path):
    """非列表 payload 或非字典元素拒绝。"""
    conn = open_db(data_dir)
    try:
        with pytest.raises(ExperienceError):
            replace_experiences(conn, "me", "not a list")  # type: ignore

        with pytest.raises(ExperienceError):
            replace_experiences(conn, "me", [123])  # type: ignore
    finally:
        conn.close()


def test_replace_experiences_user_isolation(data_dir: Path):
    """经历素材按用户完全隔离。"""
    conn = open_db(data_dir)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES ('user2', 'user2', '2026-09-25T00:00:00Z')"
        )

        replace_experiences(conn, "me", [{"item_no": 1, "content": "用户 me 的经历"}])
        replace_experiences(conn, "user2", [{"item_no": 1, "content": "用户 user2 的经历"}])

        me_items = list_experiences(conn, "me")
        u2_items = list_experiences(conn, "user2")

        assert len(me_items) == 1
        assert me_items[0]["content"] == "用户 me 的经历"

        assert len(u2_items) == 1
        assert u2_items[0]["content"] == "用户 user2 的经历"

        # me 清空不影响 user2
        replace_experiences(conn, "me", [])
        assert list_experiences(conn, "me") == []
        assert len(list_experiences(conn, "user2")) == 1
    finally:
        conn.close()
