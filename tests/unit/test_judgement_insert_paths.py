"""体检第 73 条：新建判断记录的 9 条路径，逐字段固定下来（重构前后必须一致）。

覆盖 request_judgement 的首次判断（规则排除 / 排队 / 额度用完）、自动更新（规则 / 大模型），
以及 rejudge 的手动重新判断（规则 / 大模型 / 额度用完 / 之前没有判断）。
"""

import json
from pathlib import Path

import pytest

from jet.db.store import open_db, transaction, utc_now
from jet.domain.judgements import request_judgement, rejudge
from jet.domain.rules import RULES_VERSION

PV = "v10"
FIELDS = ("status", "verdict", "source", "reasons", "prompt_version", "engine", "origin",
          "job_version_id", "profile_id", "superseded_by", "error")


def _add_profile(conn, version_no: int, excluded_city: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO profiles (user_id, version_no, directions, keywords, cities, excluded_cities, "
        "exclude_keywords, created_at) VALUES ('me', ?, '[\"Python\"]', '[]', '[]', ?, '[]', ?)",
        (version_no, json.dumps([excluded_city] if excluded_city else [], ensure_ascii=False), utc_now()),
    )
    return cur.lastrowid


def _add_job(conn, pid: str = "job_ins") -> tuple[int, int]:
    now = utc_now()
    job_id = conn.execute(
        "INSERT INTO jobs (platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES ('boss', ?, 'full', ?, ?)",
        (pid, now, now),
    ).lastrowid
    ver_id = conn.execute(
        "INSERT INTO job_versions (job_id, version_no, source, title, salary_raw, salary_visible, salary_min_k, "
        "salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (?, 1, 'detail', 'Python工程师', '15-25K', 1, 15.0, 25.0, 12, 1, '深圳', '后端', 'h', ?)",
        (job_id, now),
    ).lastrowid
    conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (ver_id, job_id))
    return job_id, ver_id


def _job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _row(conn, jid: int) -> dict:
    r = conn.execute("SELECT * FROM judgements WHERE id = ?", (jid,)).fetchone()
    d = {k: r[k] for k in FIELDS}
    d["rule_result_set"] = bool(r["rule_result"])
    d["created_at_set"] = r["created_at"] is not None
    d["finished_at_set"] = r["finished_at"] is not None
    return d


def _rule_row(origin: str, ver_id: int, prof_id: int) -> dict:
    return {"status": "done", "verdict": "skip", "source": "rule",
            "reasons": json.dumps(["不去的城市：深圳"], ensure_ascii=False), "prompt_version": "rule",
            "engine": f"rules:{RULES_VERSION}", "origin": origin, "job_version_id": ver_id, "profile_id": prof_id,
            "superseded_by": None, "error": None, "rule_result_set": True, "created_at_set": True,
            "finished_at_set": True}


def _llm_row(status: str, origin: str, ver_id: int, prof_id: int) -> dict:
    return {"status": status, "verdict": None, "source": None, "reasons": None, "prompt_version": PV,
            "engine": None, "origin": origin, "job_version_id": ver_id, "profile_id": prof_id,
            "superseded_by": None, "error": None, "rule_result_set": True, "created_at_set": True,
            "finished_at_set": False}


@pytest.fixture
def conn(data_dir: Path):
    c = open_db(data_dir)
    yield c
    c.close()


def _set_judge_limit(conn, n: int) -> None:
    conn.execute("UPDATE user_settings SET daily_llm_limit = ? WHERE user_id = 'me'", (n,))


def _reasons(conn, jid):
    return conn.execute("SELECT reasons FROM judgements WHERE id = ?", (jid,)).fetchone()["reasons"]


# ---------- request_judgement：首次判断 ----------

def test_initial_rule(conn):
    p = _add_profile(conn, 1, "深圳")
    job_id, ver = _add_job(conn)
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (queued, notice) == (False, None)
    expected = _rule_row("initial", ver, p) | {"reasons": _reasons(conn, row["id"])}
    assert _row(conn, row["id"]) == expected
    assert json.loads(expected["reasons"])  # 规则命中原因非空


def test_initial_llm_queued(conn):
    p = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (queued, notice) == (True, None)
    assert _row(conn, row["id"]) == _llm_row("queued", "initial", ver, p)


def test_initial_llm_quota_exhausted(conn):
    p = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    _set_judge_limit(conn, 0)
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (queued, notice) == (False, None)
    assert _row(conn, row["id"]) == _llm_row("quota_exhausted", "initial", ver, p)


# ---------- request_judgement：自动更新（画像变了，判断过时） ----------

def _old_done(conn, p_old, job_id, ver) -> int:
    first, _, _ = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    conn.execute("UPDATE judgements SET status = 'done', verdict = 'apply', source = 'llm' WHERE id = ?", (first["id"],))
    return first["id"]


def test_auto_refresh_rule(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    p_new = _add_profile(conn, 2, "深圳")
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (queued, notice) == (False, None)
    assert row["id"] != old_id
    assert _row(conn, row["id"]) == _rule_row("auto_refresh", ver, p_new) | {"reasons": _reasons(conn, row["id"])}
    assert _row(conn, old_id)["superseded_by"] == row["id"]


def test_auto_refresh_llm(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    p_new = _add_profile(conn, 2, None)
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (queued, notice) == (True, None)
    assert _row(conn, row["id"]) == _llm_row("queued", "auto_refresh", ver, p_new)
    assert _row(conn, old_id)["superseded_by"] == row["id"]
    assert row["id"] == old_id + 1


# ---------- rejudge：手动重新判断 ----------

def _rejudge(conn, **kw):
    with transaction(conn, immediate=True):
        return rejudge(conn, user_id="me", platform_job_id="job_ins", prompt_version=PV, **kw)


def test_rejudge_rule(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    p_new = _add_profile(conn, 2, "深圳")
    row, queued = _rejudge(conn)
    assert queued is False
    assert _row(conn, row["id"]) == _rule_row("manual", ver, p_new) | {"reasons": _reasons(conn, row["id"])}
    assert _row(conn, old_id)["superseded_by"] == row["id"]


def test_rejudge_llm_force(conn):
    p = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p, job_id, ver)
    row, queued = _rejudge(conn, force=True)  # 判断没过时，强制重判：同岗位版本、同画像，靠预定新 id 避开唯一索引
    assert queued is True
    assert _row(conn, row["id"]) == _llm_row("queued", "manual", ver, p)
    assert _row(conn, old_id)["superseded_by"] == row["id"]
    assert row["id"] == old_id + 1


def test_rejudge_llm_quota_exhausted(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    p_new = _add_profile(conn, 2, None)
    _set_judge_limit(conn, 0)
    row, queued = _rejudge(conn)
    assert queued is False
    assert _row(conn, row["id"]) == _llm_row("quota_exhausted", "manual", ver, p_new)
    assert _row(conn, old_id)["superseded_by"] == row["id"]


def test_rejudge_without_previous_judgement_reserves_max_plus_one(conn):
    p = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    # 两条别的岗位的判断，再删掉编号最大的那条：此时 MAX(id)+1 小于数据库自动编号的下一个值，
    # 能区分「预定 MAX(id)+1」和「交给数据库自动编号」
    other_job, _ = _add_job(conn, "job_other")
    other, _, _ = request_judgement(conn, user_id="me", job_row=_job(conn, other_job), prompt_version=PV)
    third_job, _ = _add_job(conn, "job_third")
    third, _, _ = request_judgement(conn, user_id="me", job_row=_job(conn, third_job), prompt_version=PV)
    conn.execute("DELETE FROM judgements WHERE id = ?", (third["id"],))
    row, queued = _rejudge(conn)
    assert queued is True
    assert row["id"] == other["id"] + 1 == third["id"]
    assert _row(conn, row["id"]) == _llm_row("queued", "manual", ver, p)


# ---------- request_judgement：自动更新时不新建记录的两种情况 ----------

def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]


def test_auto_refresh_quota_exhausted_keeps_old_judgement(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    _add_profile(conn, 2, None)
    _set_judge_limit(conn, 0)
    before = _count(conn)
    row, queued, notice = request_judgement(conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV)
    assert (row["id"], queued, notice) == (old_id, False, "auto_refresh_quota")
    assert _count(conn) == before
    assert _row(conn, old_id)["superseded_by"] is None


def test_auto_refresh_without_key_keeps_old_judgement(conn):
    p_old = _add_profile(conn, 1, None)
    job_id, ver = _add_job(conn)
    old_id = _old_done(conn, p_old, job_id, ver)
    _add_profile(conn, 2, None)
    before = _count(conn)
    row, queued, notice = request_judgement(
        conn, user_id="me", job_row=_job(conn, job_id), prompt_version=PV, has_llm_key=False
    )
    assert (row["id"], queued, notice) == (old_id, False, "no_llm_key")
    assert _count(conn) == before
    assert _row(conn, old_id)["superseded_by"] is None
