"""体检第 48 条：启动时的「无主数据」自检覆盖所有带 user_id 的个人数据表。"""

import re
from pathlib import Path

from jet.db.store import OWNED_TABLES, count_ownerless_rows, open_db, utc_now

SCHEMA = Path(__file__).resolve().parents[2] / "src" / "jet" / "db" / "schema.sql"


def _tables_with_user_id() -> set[str]:
    sql = SCHEMA.read_text(encoding="utf-8")
    tables = set()
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        if re.search(r"^\s*user_id\b", m.group(2), re.M):
            tables.add(m.group(1))
    return tables


def test_owned_tables_match_schema():
    # 新增带 user_id 的表却没加进自检名单时，这里会失败
    assert set(OWNED_TABLES) == _tables_with_user_id()
    assert len(OWNED_TABLES) == len(set(OWNED_TABLES))


def test_ownerless_rows_in_resume_and_strict_industry_tables_are_counted(data_dir: Path):
    conn = open_db(data_dir)
    try:
        assert count_ownerless_rows(conn) == 0
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO resume_slots (user_id, slot, name, resume_text, profile, updated_at) "
            "VALUES ('ghost', 1, '简历', '正文', '画像', ?)",
            (utc_now(),),
        )
        assert count_ownerless_rows(conn) == 1
        conn.execute("INSERT INTO strict_industry_selection (user_id, industry) VALUES ('ghost', '金融')")
        assert count_ownerless_rows(conn) == 2
        conn.execute("DELETE FROM resume_slots WHERE user_id = 'ghost'")
        conn.execute("DELETE FROM strict_industry_selection WHERE user_id = 'ghost'")
        assert count_ownerless_rows(conn) == 0
        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()
