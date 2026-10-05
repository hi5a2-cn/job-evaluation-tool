from pathlib import Path
import pytest

from jet.db.store import open_db
from jet.domain.profiles import get_current_profile, save_profile


def test_profile_empty_directions_rejected(data_dir: Path):
    conn = open_db(data_dir)
    try:
        # Empty directions
        with pytest.raises(ValueError, match="directions"):
            save_profile(
                conn,
                "me",
                {
                    "directions": [],
                    "cities": ["深圳"],
                },
            )

        # Whitespace-only directions
        with pytest.raises(ValueError, match="directions"):
            save_profile(
                conn,
                "me",
                {
                    "directions": ["   "],
                    "cities": ["深圳"],
                },
            )
    finally:
        conn.close()


def test_profile_empty_cities_allowed(data_dir: Path):
    conn = open_db(data_dir)
    try:
        # Empty preferred_cities allowed
        v1, changed1 = save_profile(
            conn,
            "me",
            {
                "directions": ["后端开发"],
                "preferred_cities": [],
            },
        )
        assert v1 == 1
        assert changed1 is True

        prof = get_current_profile(conn, "me")
        assert prof is not None
        assert prof["preferred_cities"] == []
        assert prof["cities"] == []

        # Whitespace-only cities stripped to empty, still allowed（另一个用户需先存在：profiles.user_id 外键）
        conn.execute("INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES ('me2', 'me2', '2026-09-25T00:00:00Z')")
        v2, changed2 = save_profile(
            conn,
            "me2",
            {
                "directions": ["后端开发"],
                "cities": ["  "],
            },
        )
        assert v2 == 1
        assert changed2 is True
        prof2 = get_current_profile(conn, "me2")
        assert prof2 is not None
        assert prof2["preferred_cities"] == []
        assert prof2["cities"] == []
    finally:
        conn.close()


def test_profile_legacy_cities_field_saved(data_dir: Path):
    conn = open_db(data_dir)
    try:
        v, changed = save_profile(
            conn,
            "me",
            {
                "directions": ["后端开发"],
                "cities": ["深圳", "广州"],
            },
        )
        assert v == 1
        assert changed is True

        prof = get_current_profile(conn, "me")
        assert prof is not None
        assert prof["preferred_cities"] == ["深圳", "广州"]
        assert prof["cities"] == ["深圳", "广州"]
    finally:
        conn.close()


def test_profile_new_fields_read_write_and_change_detection(data_dir: Path):
    conn = open_db(data_dir)
    try:
        data = {
            "directions": ["Python开发"],
            "preferred_cities": ["深圳"],
            "excluded_cities": ["北京", "上海", "北京 "],
            "min_monthly_k": 15.0,
            "nonpref_min_monthly_k": 20.0,
        }
        v1, changed1 = save_profile(conn, "me", data)
        assert v1 == 1
        assert changed1 is True

        prof = get_current_profile(conn, "me")
        assert prof is not None
        assert prof["preferred_cities"] == ["深圳"]
        assert prof["cities"] == ["深圳"]
        assert prof["excluded_cities"] == ["北京", "上海"]
        assert prof["min_monthly_k"] == 15.0
        assert prof["nonpref_min_monthly_k"] == 20.0

        # Saving identical content -> unchanged
        v1_again, changed_same = save_profile(conn, "me", data)
        assert v1_again == 1
        assert changed_same is False

        # Changing preferred_cities triggers change
        d_pref = dict(data, preferred_cities=["深圳", "杭州"])
        v2, changed_pref = save_profile(conn, "me", d_pref)
        assert v2 == 2
        assert changed_pref is True

        # Changing excluded_cities triggers change
        d_ex = dict(d_pref, excluded_cities=["北京"])
        v3, changed_ex = save_profile(conn, "me", d_ex)
        assert v3 == 3
        assert changed_ex is True

        # Changing nonpref_min_monthly_k triggers change
        d_nonpref = dict(d_ex, nonpref_min_monthly_k=25.0)
        v4, changed_np = save_profile(conn, "me", d_nonpref)
        assert v4 == 4
        assert changed_np is True

        prof4 = get_current_profile(conn, "me")
        assert prof4["nonpref_min_monthly_k"] == 25.0

        # nonpref_min_monthly_k negative check
        with pytest.raises(ValueError, match="nonpref_min_monthly_k"):
            save_profile(conn, "me", dict(d_nonpref, nonpref_min_monthly_k=-1))
    finally:
        conn.close()


def test_profile_deduplication_and_stripping(data_dir: Path):
    conn = open_db(data_dir)
    try:
        data = {
            "directions": ["  Python后端  ", "数据开发", "Python后端"],
            "keywords": [" FastAPI ", "Django", "FastAPI", "  "],
            "cities": [" 深圳 ", "广州", "深圳 "],
            "min_monthly_k": 20,
            "exclude_keywords": [" 外包 ", "驻场", "外包"],
        }
        v_no, changed = save_profile(conn, "me", data)
        assert v_no == 1
        assert changed is True

        prof = get_current_profile(conn, "me")
        assert prof is not None
        assert prof["version_no"] == 1
        assert prof["directions"] == ["Python后端", "数据开发"]
        assert prof["keywords"] == ["FastAPI", "Django"]
        assert prof["cities"] == ["深圳", "广州"]
        assert prof["min_monthly_k"] == 20.0
        assert prof["exclude_keywords"] == ["外包", "驻场"]
    finally:
        conn.close()


def test_profile_version_increment_and_idempotency(data_dir: Path):
    conn = open_db(data_dir)
    try:
        data = {
            "directions": ["Python后端"],
            "keywords": ["FastAPI"],
            "cities": ["深圳"],
            "min_monthly_k": None,
            "exclude_keywords": [],
        }
        v1, changed1 = save_profile(conn, "me", data)
        assert v1 == 1
        assert changed1 is True

        # Saving identical content -> version unchanged, changed=False
        v2, changed2 = save_profile(conn, "me", data)
        assert v2 == 1
        assert changed2 is False

        # Saving modified content -> version incremented
        data_mod = dict(data, min_monthly_k=25.0)
        v3, changed3 = save_profile(conn, "me", data_mod)
        assert v3 == 2
        assert changed3 is True

        curr = get_current_profile(conn, "me")
        assert curr is not None
        assert curr["version_no"] == 2
        assert curr["min_monthly_k"] == 25.0
    finally:
        conn.close()


def test_profile_min_monthly_k_none(data_dir: Path):
    conn = open_db(data_dir)
    try:
        data = {
            "directions": ["前端开发"],
            "cities": ["北京"],
            "min_monthly_k": None,
        }
        v, changed = save_profile(conn, "me", data)
        assert v == 1
        assert changed is True

        prof = get_current_profile(conn, "me")
        assert prof is not None
        assert prof["min_monthly_k"] is None
    finally:
        conn.close()
