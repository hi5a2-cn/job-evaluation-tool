from pathlib import Path
import pytest

from jet.db.store import count_ownerless_rows, open_db, utc_now
from jet.domain.jobs import ingest
from jet.domain.job_status import set_status, status_for


def _create_sample_job(conn, platform_job_id: str = "job_01") -> int:
    now_str = utc_now()
    cur = conn.execute(
        "INSERT INTO jobs (platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
        "VALUES ('boss', ?, 'full', '测试公司', ?, ?)",
        (platform_job_id, now_str, now_str),
    )
    job_id = cur.lastrowid
    v_cur = conn.execute(
        "INSERT INTO job_versions (job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (?, 1, 'detail', 'Python Dev', 1, 1, '深圳', 'hash_01', ?)",
        (job_id, now_str),
    )
    conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (v_cur.lastrowid, job_id))
    return job_id


def test_set_status_mutex_transition_and_events(data_dir: Path):
    conn = open_db(data_dir)
    try:
        job_id = _create_sample_job(conn, "job_01")

        # 1. 初始为 None
        assert status_for(conn, "me", job_id) is None

        # 2. 设置为 saved
        res_saved = set_status(conn, user_id="me", platform_job_id="job_01", status="saved")
        assert res_saved is not None
        assert res_saved["status"] == "saved"
        assert "updated_at" in res_saved

        cur_st = status_for(conn, "me", job_id)
        assert cur_st is not None
        assert cur_st["status"] == "saved"
        assert cur_st["updated_at"] == res_saved["updated_at"]

        # 验证 job_status 表与事件表
        status_rows = conn.execute("SELECT * FROM job_status WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(status_rows) == 1
        assert status_rows[0]["status"] == "saved"

        events = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ? ORDER BY id", (job_id,)).fetchall()
        assert len(events) == 1
        assert events[0]["status"] == "saved"
        assert events[0]["previous_status"] is None

        # 3. 互斥替换为 applied
        res_applied = set_status(conn, user_id="me", platform_job_id="job_01", status="applied")
        assert res_applied is not None
        assert res_applied["status"] == "applied"

        status_rows = conn.execute("SELECT * FROM job_status WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(status_rows) == 1  # 依然只有一条记录，互斥
        assert status_rows[0]["status"] == "applied"

        events = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ? ORDER BY id", (job_id,)).fetchall()
        assert len(events) == 2
        assert events[1]["status"] == "applied"
        assert events[1]["previous_status"] == "saved"

        # 4. 互斥替换为 skipped
        res_skipped = set_status(conn, user_id="me", platform_job_id="job_01", status="skipped")
        assert res_skipped is not None
        assert res_skipped["status"] == "skipped"

        status_rows = conn.execute("SELECT * FROM job_status WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(status_rows) == 1
        assert status_rows[0]["status"] == "skipped"

        events = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ? ORDER BY id", (job_id,)).fetchall()
        assert len(events) == 3
        assert events[2]["status"] == "skipped"
        assert events[2]["previous_status"] == "applied"
    finally:
        conn.close()


def test_set_status_cancel(data_dir: Path):
    conn = open_db(data_dir)
    try:
        job_id = _create_sample_job(conn, "job_cancel")

        # 设为 saved
        set_status(conn, user_id="me", platform_job_id="job_cancel", status="saved")
        assert status_for(conn, "me", job_id) is not None

        # 取消 (status=None)
        res_cancel = set_status(conn, user_id="me", platform_job_id="job_cancel", status=None)
        assert res_cancel is None
        assert status_for(conn, "me", job_id) is None

        # job_status 应为空
        status_rows = conn.execute("SELECT * FROM job_status WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(status_rows) == 0

        # job_status_events 追加了一条取消事件
        events = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ? ORDER BY id", (job_id,)).fetchall()
        assert len(events) == 2
        assert events[1]["status"] is None
        assert events[1]["previous_status"] == "saved"
    finally:
        conn.close()


def test_same_status_does_not_write_event(data_dir: Path):
    conn = open_db(data_dir)
    try:
        job_id = _create_sample_job(conn, "job_same")

        # 设为 saved -> 写入第 1 条事件
        res1 = set_status(conn, user_id="me", platform_job_id="job_same", status="saved")
        assert res1 is not None
        events1 = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(events1) == 1

        # 再次设为 saved -> 相同状态直接返回，不写事件
        res2 = set_status(conn, user_id="me", platform_job_id="job_same", status="saved")
        assert res2 is not None
        assert res2["status"] == "saved"
        assert res2["updated_at"] == res1["updated_at"]

        events2 = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ?", (job_id,)).fetchall()
        assert len(events2) == 1

        # 当前无状态时设为 None -> 不写事件
        job_id2 = _create_sample_job(conn, "job_none")
        res_none = set_status(conn, user_id="me", platform_job_id="job_none", status=None)
        assert res_none is None
        events_none = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me' AND job_id = ?", (job_id2,)).fetchall()
        assert len(events_none) == 0
    finally:
        conn.close()


def test_invalid_status_value(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _create_sample_job(conn, "job_invalid")

        with pytest.raises(ValueError, match="Invalid status"):
            set_status(conn, user_id="me", platform_job_id="job_invalid", status="unknown_status")

        with pytest.raises(ValueError, match="Invalid status"):
            set_status(conn, user_id="me", platform_job_id="job_invalid", status="")

        events = conn.execute("SELECT * FROM job_status_events WHERE user_id = 'me'").fetchall()
        assert len(events) == 0
    finally:
        conn.close()


def test_nonexistent_job_raises_lookup_error(data_dir: Path):
    conn = open_db(data_dir)
    try:
        with pytest.raises(LookupError, match="not found"):
            set_status(conn, user_id="me", platform_job_id="nonexistent_999", status="saved")
    finally:
        conn.close()


def test_ownerless_check_includes_new_tables(data_dir: Path):
    conn = open_db(data_dir)
    try:
        _create_sample_job(conn, "job_ownerless")
        job_row = conn.execute("SELECT id FROM jobs WHERE platform_job_id = 'job_ownerless'").fetchone()
        job_id = job_row["id"]

        assert count_ownerless_rows(conn) == 0

        # 关闭外键检查以插入无主记录
        conn.execute("PRAGMA foreign_keys=OFF")

        # 1. 测试 job_status 无主行
        conn.execute(
            "INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('ghost_user', ?, 'saved', ?)",
            (job_id, utc_now()),
        )
        assert count_ownerless_rows(conn) == 1

        conn.execute("DELETE FROM job_status WHERE user_id = 'ghost_user'")
        assert count_ownerless_rows(conn) == 0

        # 2. 测试 job_status_events 无主行
        conn.execute(
            "INSERT INTO job_status_events (user_id, job_id, status, previous_status, created_at) "
            "VALUES ('ghost_user', ?, 'saved', NULL, ?)",
            (job_id, utc_now()),
        )
        assert count_ownerless_rows(conn) == 1

        conn.execute("DELETE FROM job_status_events WHERE user_id = 'ghost_user'")
        assert count_ownerless_rows(conn) == 0

        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()


def test_ingest_company_name_lifecycle(data_dir: Path):
    conn = open_db(data_dir)
    try:
        now_str = utc_now()

        # 1. 新建写入 company_name
        job_data1 = {
            "platform_job_id": "job_company_test",
            "title": "后端架构师",
            "company_name": "腾讯科技",
            "salary_raw": "30-50K",
            "city": "深圳",
            "description": "系统设计与开发",
        }
        res1 = ingest(conn, user_id="me", page_type="detail", jobs=[job_data1], observed_at=now_str)
        job_row1 = res1["job_company_test"]
        assert job_row1["company_name"] == "腾讯科技"

        v_rows1 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row1["id"],)).fetchall()
        assert len(v_rows1) == 1

        # 2. 空值不覆盖已有值
        job_data2 = {
            "platform_job_id": "job_company_test",
            "title": "后端架构师",
            "company_name": None,  # 空值
            "salary_raw": "30-50K",
            "city": "深圳",
            "description": "系统设计与开发",
        }
        res2 = ingest(conn, user_id="me", page_type="detail", jobs=[job_data2], observed_at=now_str)
        job_row2 = res2["job_company_test"]
        assert job_row2["company_name"] == "腾讯科技"  # 保持原值未被覆盖

        v_rows2 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row1["id"],)).fetchall()
        assert len(v_rows2) == 1

        # 空字符串也不覆盖已有值
        job_data_empty = {
            "platform_job_id": "job_company_test",
            "title": "后端架构师",
            "company_name": "   ",
            "salary_raw": "30-50K",
            "city": "深圳",
            "description": "系统设计与开发",
        }
        res_empty = ingest(conn, user_id="me", page_type="detail", jobs=[job_data_empty], observed_at=now_str)
        assert res_empty["job_company_test"]["company_name"] == "腾讯科技"

        # 3. 公司名变化更新 jobs.company_name，但不产生新版本
        job_data3 = {
            "platform_job_id": "job_company_test",
            "title": "后端架构师",
            "company_name": "腾讯集团",  # 公司名变更
            "salary_raw": "30-50K",
            "city": "深圳",
            "description": "系统设计与开发",
        }
        res3 = ingest(conn, user_id="me", page_type="detail", jobs=[job_data3], observed_at=now_str)
        job_row3 = res3["job_company_test"]
        assert job_row3["company_name"] == "腾讯集团"

        # 版本数量依然为 1，未产生新版本
        v_rows3 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row1["id"],)).fetchall()
        assert len(v_rows3) == 1
        assert job_row3["current_version_id"] == v_rows1[0]["id"]
    finally:
        conn.close()
