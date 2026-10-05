"""体检第 76 条：同一个岗位，岗位卡片（to_api）和岗位库列表（list_my_jobs）显示的结论、是否被纠正、是否过时一致。"""

import json
from pathlib import Path

from jet.db.store import open_db, utc_now
from jet.domain.job_status import list_my_jobs
from jet.domain.judgements import latest_judgement, staleness, to_api
from jet.llm.versions import STALE_METHOD_BASELINE


def _profile(conn, version_no: int) -> int:
    return conn.execute(
        "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, excluded_cities, exclude_keywords, created_at) "
        "VALUES ('me', ?, '[\"Python\"]', '[]', '[]', '[]', '[]', ?)",
        (version_no, utc_now()),
    ).lastrowid


def _job(conn, pid: str) -> tuple[int, int]:
    now = utc_now()
    job_id = conn.execute(
        "INSERT INTO jobs (platform, platform_job_id, company_name, completeness, first_seen_at, last_seen_at) "
        "VALUES ('boss', ?, '公司', 'full', ?, ?)",
        (pid, now, now),
    ).lastrowid
    ver = _version(conn, job_id, 1)
    return job_id, ver


def _version(conn, job_id: int, n: int) -> int:
    ver = conn.execute(
        "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, salary_min_k, "
        "salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (?, ?, 'detail', 'Python工程师', '15-25K', 1, 15, 25, 12, 1, '深圳', '后端', ?, ?)",
        (job_id, n, f"h{job_id}-{n}", utc_now()),
    ).lastrowid
    conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (ver, job_id))
    return ver


def _judge(conn, job_id, ver, prof, *, status="done", verdict="apply", pv=None) -> int:
    now = utc_now()
    return conn.execute(
        "INSERT INTO judgements (user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, "
        "prompt_version, rule_result, created_at, finished_at) VALUES ('me', ?, ?, ?, ?, ?, 'llm', '[]', ?, '{}', ?, ?)",
        (job_id, ver, prof, status, verdict if status == "done" else None, pv or STALE_METHOD_BASELINE, now, now),
    ).lastrowid


def _label(conn, ver, overall) -> None:
    now = utc_now()
    conn.execute(
        "INSERT INTO labels (user_id, job_version_id, overall, created_at, updated_at) VALUES ('me', ?, ?, ?, ?)",
        (ver, overall, now, now),
    )


def test_card_and_library_agree(data_dir: Path):
    conn = open_db(data_dir)
    try:
        p1 = _profile(conn, 1)
        cases = {}
        j, v = _job(conn, "legacy_fit"); _judge(conn, j, v, p1, verdict="fit"); cases["legacy_fit"] = j
        j, v = _job(conn, "labelled"); _judge(conn, j, v, p1, verdict="try"); _label(conn, v, "skip"); cases["labelled"] = j
        j, v = _job(conn, "label_no_overall"); _judge(conn, j, v, p1, verdict="check"); _label(conn, v, None); cases["label_no_overall"] = j
        j, v = _job(conn, "job_changed"); _judge(conn, j, v, p1, verdict="apply"); _version(conn, j, 2); cases["job_changed"] = j
        j, v = _job(conn, "method_old"); _judge(conn, j, v, p1, verdict="apply", pv="v5"); cases["method_old"] = j
        j, v = _job(conn, "queued"); _judge(conn, j, v, p1, status="queued"); _label(conn, v, "apply"); cases["queued"] = j
        p2_jobs = {}
        # 画像变了：之前的判断都算过时
        j, v = _job(conn, "profile_changed"); _judge(conn, j, v, p1, verdict="apply"); p2_jobs["profile_changed"] = j
        for pid in cases.keys() | p2_jobs.keys():
            conn.execute(
                "INSERT INTO job_status (user_id, job_id, status, updated_at) "
                "SELECT 'me', id, 'saved', ? FROM jobs WHERE platform_job_id = ?",
                (utc_now(), pid),
            )
        _profile(conn, 2)
        cases.update(p2_jobs)

        items = {it["platform_job_id"]: it for it in list_my_jobs(conn, user_id="me", filter="all")["items"]}
        assert set(items) == set(cases)
        for pid, job_id in cases.items():
            latest = latest_judgement(conn, "me", job_id)
            card = to_api(conn, latest)
            item = items[pid]
            assert (item["verdict"], item["model_verdict"], item["verdict_overridden"]) == (
                card["verdict"], card["model_verdict"], card["verdict_overridden"]
            ), pid
            assert item["stale"] == any(staleness(conn, latest).values()), pid
        # 抽查几个关键值，防止两边一起错
        assert items["legacy_fit"]["verdict"] == "apply"
        assert (items["labelled"]["verdict"], items["labelled"]["model_verdict"], items["labelled"]["verdict_overridden"]) == ("skip", "try", True)
        assert items["label_no_overall"]["verdict_overridden"] is False
        assert items["queued"]["verdict"] is None and items["queued"]["verdict_overridden"] is False
        assert items["profile_changed"]["stale"] is True
        assert items["job_changed"]["stale"] is True
        assert items["method_old"]["stale"] is True
    finally:
        conn.close()
