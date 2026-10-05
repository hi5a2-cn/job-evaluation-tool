from pathlib import Path

from jet.db.store import open_db, utc_now
from jet.domain.judgements import to_api
from jet.domain.profiles import save_profile


def test_stale_calculation_scenarios(data_dir: Path):
    conn = open_db(data_dir)
    now_str = utc_now()

    # 1. Create Profile V1
    p1_id, _ = save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})
    prof1 = conn.execute("SELECT id FROM profiles WHERE user_id = 'me' AND version_no = 1").fetchone()
    p1_row_id = prof1["id"]

    # 2. Create Job with Version 1 and Version 2
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (10, 'boss', 'job_stale_test', 'full', NULL, ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (100, 10, 1, 'detail', 'Python Dev', 1, 1, '深圳', 'hash_v1', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (101, 10, 2, 'detail', 'Senior Python Dev', 1, 1, '深圳', 'hash_v2', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 101 WHERE id = 10")

    # Insert a judgement evaluated on Version 1 (id=100) and Profile V1 (id=p1_row_id)
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, rule_result, created_at, finished_at) "
        "VALUES (1000, 'me', 10, 100, ?, 'done', 'fit', 'llm', '[\"不错\"]', '{}', ?, ?)",
        (p1_row_id, now_str, now_str),
    )
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 1000").fetchone()

    # Case A: Current job version is 101, but judgement was on 100 -> job_changed = True
    # Current profile is still P1 -> profile_changed = False
    api_res_a = to_api(conn, j_row)
    assert api_res_a["stale"]["job_changed"] is True
    assert api_res_a["stale"]["profile_changed"] is False

    # Case B: Update profile to V2
    save_profile(conn, "me", {"directions": ["Python", "Go"], "cities": ["深圳"]})
    # Now both job and profile are changed!
    api_res_b = to_api(conn, j_row)
    assert api_res_b["stale"]["job_changed"] is True
    assert api_res_b["stale"]["profile_changed"] is True

    # Case C: Revert jobs.current_version_id back to 100
    conn.execute("UPDATE jobs SET current_version_id = 100 WHERE id = 10")
    api_res_c = to_api(conn, j_row)
    assert api_res_c["stale"]["job_changed"] is False
    assert api_res_c["stale"]["profile_changed"] is True

    # Case D: Both match
    prof2 = conn.execute("SELECT id FROM profiles WHERE user_id = 'me' AND version_no = 2").fetchone()
    conn.execute("UPDATE judgements SET profile_id = ? WHERE id = 1000", (prof2["id"],))
    j_row_updated = conn.execute("SELECT * FROM judgements WHERE id = 1000").fetchone()
    api_res_d = to_api(conn, j_row_updated)
    assert api_res_d["stale"]["job_changed"] is False
    assert api_res_d["stale"]["profile_changed"] is False

    conn.close()


def test_rule_judgement_stale_method_changed(data_dir: Path):
    conn = open_db(data_dir)
    now_str = utc_now()

    p_id, _ = save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})
    prof = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (20, 'boss', 'job_rule_stale_test', 'full', NULL, ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (200, 20, 1, 'detail', 'Python Dev', 1, 1, '深圳', 'hash_r', ?)",
        (now_str,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 200 WHERE id = 20")

    # 1. Old rule judgement: engine is NULL -> stale.method_changed = True
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (2001, 'me', 20, 200, ?, 'done', 'skip', 'rule', '[\"命中不接受关键词：销售\"]', 'rule', NULL, '{}', ?, ?)",
        (prof["id"], now_str, now_str),
    )
    j_null = conn.execute("SELECT * FROM judgements WHERE id = 2001").fetchone()
    res_null = to_api(conn, j_null)
    assert res_null["stale"]["method_changed"] is True

    # 2. Old rule judgement: engine='rules:r1' -> stale.method_changed = True
    # 同一岗位版本 × 画像只能有一条现行判断：先删掉上一条测试判断
    conn.execute("DELETE FROM judgements WHERE id = 2001")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (2002, 'me', 20, 200, ?, 'done', 'skip', 'rule', '[\"命中不接受关键词：销售\"]', 'rule', 'rules:r1', '{}', ?, ?)",
        (prof["id"], now_str, now_str),
    )
    j_r1 = conn.execute("SELECT * FROM judgements WHERE id = 2002").fetchone()
    res_r1 = to_api(conn, j_r1)
    assert res_r1["stale"]["method_changed"] is True

    # 3. New rule judgement: engine='rules:r3' -> stale.method_changed = False
    # 同一岗位版本 × 画像只能有一条现行判断：先删掉上一条测试判断
    conn.execute("DELETE FROM judgements WHERE id = 2002")
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (2003, 'me', 20, 200, ?, 'done', 'skip', 'rule', '[\"城市不符\"]', 'rule', 'rules:r3', '{}', ?, ?)",
        (prof["id"], now_str, now_str),
    )
    j_r2 = conn.execute("SELECT * FROM judgements WHERE id = 2003").fetchone()
    res_r2 = to_api(conn, j_r2)
    assert res_r2["stale"]["method_changed"] is False

    conn.close()
