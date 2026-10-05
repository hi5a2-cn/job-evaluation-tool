"""Unit tests for my-jobs search, filters, counts, and sorting (T035).

Specs:
- specs/003-hr-assistant/spec.md (FR-041-FR-051, SC-009, SC-010, R7)
- specs/003-hr-assistant/data-model.md (all_jobs union, recent_updated_at, escape_like)
- specs/003-hr-assistant/contracts/local-api.md (Section 7: GET /v1/my-jobs)
- specs/003-hr-assistant/tasks.md (T035, T037)
"""

from pathlib import Path
import pytest

from jet.db.store import open_db, utc_now
from jet.domain.job_status import escape_like, list_my_jobs


def _insert_job(conn, jid: int, pid: str, company: str, seen_at: str) -> None:
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, current_version_id, first_seen_at, last_seen_at) "
        "VALUES (?, 'boss', ?, 'full', ?, NULL, ?, ?)",
        (jid, pid, company, seen_at, seen_at),
    )


def _insert_version(conn, vid: int, jid: int, vno: int, title: str, source: str, created_at: str, is_current: bool = False) -> None:
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, '20-30K', 1, 1, '深圳', ?, ?)",
        (vid, jid, vno, source, title, f"hash_{vid}", created_at),
    )
    if is_current:
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (vid, jid))


def test_escape_like():
    """测试 LIKE 特殊字符 %、_、/ 转义。"""
    assert escape_like("abc") == "abc"
    assert escape_like("100%") == "100/%"
    assert escape_like("a_b") == "a/_b"
    assert escape_like("a/b") == "a//b"
    assert escape_like("a%b_c/d") == "a/%b/_c//d"


def test_all_jobs_union_and_deduplication(data_dir: Path):
    """测试 filter=all_jobs 为有状态 ∪ 判断过 ∪ 有 HR 记录并集去重总数（SC-009）。"""
    conn = open_db(data_dir)
    try:
        now = "2026-09-26T10:00:00Z"
        # 建立画像
        conn.execute(
            "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
            "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', ?)",
            (now,),
        )

        # 8 个岗位
        # job1: 只有状态 (saved)
        # job2: 只有判断
        # job3: 只有 HR 记录
        # job4: 既有状态，又有判断，又有 HR 记录 (重合)
        # job5: 既有状态 (skipped)，又有判断
        # job6: 其他用户的记录 (不应包含)
        # job7: 未标记、未判断、无 HR 记录
        # job8: HR 记录为空白 (不应计入)
        for i in range(1, 9):
            _insert_job(conn, i, f"job_{i}", f"公司_{i}", now)
            _insert_version(conn, 100 + i, i, 1, f"职位_{i}", "detail", now, is_current=True)

        # job1: status
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', ?)", (now,))

        # job2: judgement
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (201, 'me', 2, 102, 1, 'done', 'apply', '{}', ?, ?)",
            (now, now),
        )

        # job3: hr_note
        conn.execute(
            "INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 3, 'HR说双休', ?, ?)",
            (now, now),
        )

        # job4: status + judgement + hr_note
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 4, 'applied', ?)", (now,))
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (202, 'me', 4, 104, 1, 'done', 'try', '{}', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 4, 'HR说有补贴', ?, ?)",
            (now, now),
        )

        # job5: status (skipped) + judgement
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 5, 'skipped', ?)", (now,))
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (203, 'me', 5, 105, 1, 'done', 'skip', '{}', ?, ?)",
            (now, now),
        )

        # job6: other user
        conn.execute("INSERT INTO users (id, created_at) VALUES ('other', ?)", (now,))
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('other', 6, 'saved', ?)", (now,))

        # job8: empty hr_note
        conn.execute(
            "INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 8, '   ', ?, ?)",
            (now, now),
        )

        res = list_my_jobs(conn, user_id="me", filter="all_jobs")
        assert res["counts"]["all_jobs"] == 5
        assert res["total_matches"] == 5
        pids = {i["platform_job_id"] for i in res["items"]}
        assert pids == {"job_1", "job_2", "job_3", "job_4", "job_5"}
    finally:
        conn.close()


def test_hr_only_filtering_and_counts(data_dir: Path):
    """测试 hr_only=true 仅返回有非空 HR 记录岗位且 counts 联动更新。"""
    conn = open_db(data_dir)
    try:
        now = "2026-09-26T10:00:00Z"
        conn.execute(
            "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
            "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', ?)",
            (now,),
        )

        # job1: saved + no hr_note
        _insert_job(conn, 1, "job_1", "公司1", now)
        _insert_version(conn, 101, 1, 1, "职位1", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', ?)", (now,))

        # job2: applied + has hr_note
        _insert_job(conn, 2, "job_2", "公司2", now)
        _insert_version(conn, 102, 2, 1, "职位2", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 2, 'applied', ?)", (now,))
        conn.execute("INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 2, 'HR说有年终奖', ?, ?)", (now, now))

        # job3: judged (recent) + has hr_note
        _insert_job(conn, 3, "job_3", "公司3", now)
        _insert_version(conn, 103, 3, 1, "职位3", "detail", now, is_current=True)
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (201, 'me', 3, 103, 1, 'done', 'apply', '{}', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 3, 'HR已约面', ?, ?)", (now, now))

        # 未勾选 hr_only 时
        res_normal = list_my_jobs(conn, user_id="me", filter="all_jobs", hr_only=False)
        assert res_normal["counts"]["all_jobs"] == 3
        assert res_normal["counts"]["all"] == 2
        assert res_normal["counts"]["saved"] == 1
        assert res_normal["counts"]["applied"] == 1
        assert res_normal["counts"]["recent"] == 1
        assert res_normal["total_matches"] == 3

        # 勾选 hr_only=True 时
        res_hr = list_my_jobs(conn, user_id="me", filter="all_jobs", hr_only=True)
        assert res_hr["total_matches"] == 2
        pids = {i["platform_job_id"] for i in res_hr["items"]}
        assert pids == {"job_2", "job_3"}

        # counts 联动更新为"该分类下有 HR 记录的岗位数"
        assert res_hr["counts"]["all_jobs"] == 2
        assert res_hr["counts"]["all"] == 1  # 只有 job_2
        assert res_hr["counts"]["saved"] == 0
        assert res_hr["counts"]["applied"] == 1
        assert res_hr["counts"]["skipped"] == 0
        assert res_hr["counts"]["recent"] == 1  # 只有 job_3
    finally:
        conn.close()


def test_q_search_matching_historical_versions_company_hr_note(data_dir: Path):
    """测试 q 搜索匹配职位名所有历史版本、公司名、HR 实际情况。"""
    conn = open_db(data_dir)
    try:
        now = "2026-09-26T10:00:00Z"

        # job1: 历史版本包含"出差智利"，当前版本为"储能海外销售"
        _insert_job(conn, 1, "job_chile", "某新能源科技公司", now)
        _insert_version(conn, 101, 1, 1, "储能海外销售（出差智利等）", "list", "2026-09-26T08:00:00Z", is_current=False)
        _insert_version(conn, 102, 1, 2, "储能海外销售", "detail", "2026-09-26T09:00:00Z", is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', ?)", (now,))

        # job2: 公司名含"某云端科技"
        _insert_job(conn, 2, "job_cloud", "某云端科技", now)
        _insert_version(conn, 103, 2, 1, "后端开发", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 2, 'saved', ?)", (now,))

        # job3: HR 记录包含"有绩效考核"
        _insert_job(conn, 3, "job_kpi", "某数字跳动公司", now)
        _insert_version(conn, 104, 3, 1, "前端开发", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 3, 'saved', ?)", (now,))
        conn.execute("INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 3, 'HR说有绩效考核指标', ?, ?)", (now, now))

        # 1. 匹配旧版本职位名
        res_chile = list_my_jobs(conn, user_id="me", filter="all_jobs", q="智利")
        assert res_chile["total_matches"] == 1
        assert res_chile["items"][0]["platform_job_id"] == "job_chile"
        # 返回的 title 必须是当前版本
        assert res_chile["items"][0]["title"] == "储能海外销售"

        # 2. 匹配公司名 (大小写不敏感)
        res_comp = list_my_jobs(conn, user_id="me", filter="all_jobs", q="云端")
        assert res_comp["total_matches"] == 1
        assert res_comp["items"][0]["platform_job_id"] == "job_cloud"

        # 3. 匹配 HR 记录
        res_note = list_my_jobs(conn, user_id="me", filter="all_jobs", q="绩效考核")
        assert res_note["total_matches"] == 1
        assert res_note["items"][0]["platform_job_id"] == "job_kpi"
        assert res_note["items"][0]["hr_note"] == "HR说有绩效考核指标"
    finally:
        conn.close()


def test_like_escape_percent_and_underscore(data_dir: Path):
    """测试 % 与 _ 的 LIKE ESCAPE 转义。"""
    conn = open_db(data_dir)
    try:
        now = "2026-09-26T10:00:00Z"

        # job1: 公司名含 100%
        _insert_job(conn, 1, "job_pct", "100%纯果汁公司", now)
        _insert_version(conn, 101, 1, 1, "销售经理", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', ?)", (now,))

        # job2: 公司名含 1000
        _insert_job(conn, 2, "job_num", "1000纯果汁公司", now)
        _insert_version(conn, 102, 2, 1, "销售总监", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 2, 'saved', ?)", (now,))

        # job3: 职位名含 A_B
        _insert_job(conn, 3, "job_und", "测试公司", now)
        _insert_version(conn, 103, 3, 1, "A_B测试专员", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 3, 'saved', ?)", (now,))

        # job4: 职位名含 AXB
        _insert_job(conn, 4, "job_norm", "测试公司2", now)
        _insert_version(conn, 104, 4, 1, "AXB测试专员", "detail", now, is_current=True)
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 4, 'saved', ?)", (now,))

        # 搜索 100%：应该只命中 job_pct，绝不能命中 job_num
        res_pct = list_my_jobs(conn, user_id="me", filter="all_jobs", q="100%")
        assert res_pct["total_matches"] == 1
        assert res_pct["items"][0]["platform_job_id"] == "job_pct"

        # 搜索 A_B：应该只命中 job_und，绝不能命中 job_norm
        res_und = list_my_jobs(conn, user_id="me", filter="all_jobs", q="A_B")
        assert res_und["total_matches"] == 1
        assert res_und["items"][0]["platform_job_id"] == "job_und"
    finally:
        conn.close()


def test_limit_and_total_matches(data_dir: Path):
    """测试单次最多 limit（默认 200）条与 total_matches。"""
    conn = open_db(data_dir)
    try:
        now = "2026-09-26T10:00:00Z"
        for i in range(1, 11):
            _insert_job(conn, i, f"job_{i}", f"公司_{i}", now)
            _insert_version(conn, 100 + i, i, 1, f"职位_{i}", "detail", now, is_current=True)
            conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', ?, 'saved', ?)", (i, now))

        # limit = 3
        res = list_my_jobs(conn, user_id="me", filter="all_jobs", limit=3)
        assert res["total_matches"] == 10
        assert len(res["items"]) == 3
    finally:
        conn.close()


def test_recent_updated_at_sorting(data_dir: Path):
    """测试按 recent_updated_at 倒序排列（取状态、HR记录、详情查看、判断创建时间最大值）。"""
    conn = open_db(data_dir)
    try:
        conn.execute(
            "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
            "VALUES (1, 'me', 1, '[]', '[]', '[]', '[]', '2026-09-26T00:00:00Z')"
        )

        for i in range(1, 5):
            _insert_job(conn, i, f"job_{i}", f"公司_{i}", "2026-09-26T01:00:00Z")
            _insert_version(conn, 100 + i, i, 1, f"职位_{i}", "detail", "2026-09-26T01:00:00Z", is_current=True)

        # job1: status_updated_at = 10:00
        conn.execute("INSERT INTO job_status (user_id, job_id, status, updated_at) VALUES ('me', 1, 'saved', '2026-09-26T10:00:00Z')")

        # job2: hr_note_updated_at = 12:00 (最晚)
        conn.execute("INSERT INTO hr_notes (user_id, job_id, note, created_at, updated_at) VALUES ('me', 2, 'Note2', '2026-09-26T12:00:00Z', '2026-09-26T12:00:00Z')")

        # job3: detail view = 11:00, list view = 13:00 (列表页不计入)
        conn.execute("INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('me', 3, 'detail', '2026-09-26T11:00:00Z')")
        conn.execute("INSERT INTO views (user_id, job_id, page_type, seen_at) VALUES ('me', 3, 'list', '2026-09-26T13:00:00Z')")
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (201, 'me', 3, 103, 1, 'done', 'apply', '{}', '2026-09-26T05:00:00Z', '2026-09-26T05:00:00Z')"
        )

        # job4: judgement created_at = 09:00
        conn.execute(
            "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, rule_result, created_at, finished_at) "
            "VALUES (202, 'me', 4, 104, 1, 'done', 'apply', '{}', '2026-09-26T09:00:00Z', '2026-09-26T09:00:00Z')"
        )

        res = list_my_jobs(conn, user_id="me", filter="all_jobs")
        pids = [i["platform_job_id"] for i in res["items"]]
        # 顺序应为 job2 (12:00) -> job3 (11:00) -> job1 (10:00) -> job4 (09:00)
        assert pids == ["job_2", "job_3", "job_1", "job_4"]

        # 检查 recent_updated_at 字段
        item_by_id = {i["platform_job_id"]: i for i in res["items"]}
        assert item_by_id["job_2"]["recent_updated_at"] == "2026-09-26T12:00:00Z"
        assert item_by_id["job_3"]["recent_updated_at"] == "2026-09-26T11:00:00Z"
        assert item_by_id["job_1"]["recent_updated_at"] == "2026-09-26T10:00:00Z"
        assert item_by_id["job_4"]["recent_updated_at"] == "2026-09-26T09:00:00Z"
    finally:
        conn.close()
