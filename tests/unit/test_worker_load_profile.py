"""体检第 72 条：初判和复核共用 _load_profile，读出的画像字段固定。"""

from pathlib import Path

from jet.db.store import open_db, utc_now
from jet.worker import _load_profile


def test_load_profile_fields_and_missing(data_dir: Path):
    conn = open_db(data_dir)
    try:
        pid = conn.execute(
            "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, excluded_cities, "
            "exclude_keywords, min_monthly_k, nonpref_min_monthly_k, work_preference, background, created_at) "
            "VALUES ('me', 3, '[\"数据分析\"]', '[\"SQL\"]', '[\"深圳\"]', '[\"北京\"]', '[\"销售\"]', 12, 15, '', '统计学本科', ?)",
            (utc_now(),),
        ).lastrowid
        assert _load_profile(conn, pid) == {
            "id": pid, "user_id": "me", "version_no": 3,
            "directions": ["数据分析"], "keywords": ["SQL"],
            "cities": ["深圳"], "preferred_cities": ["深圳"], "excluded_cities": ["北京"],
            "min_monthly_k": 12, "nonpref_min_monthly_k": 15, "exclude_keywords": ["销售"],
            "work_preference": "", "background": "统计学本科",
        }
        assert _load_profile(conn, pid + 999) is None
    finally:
        conn.close()
