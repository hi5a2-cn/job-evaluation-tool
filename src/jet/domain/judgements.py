import json
import sqlite3
from typing import Any, Mapping
from fastapi import HTTPException

from jet.db.store import local_day_bounds_utc, transaction, utc_now
from jet.domain import labels
from jet.domain.profiles import get_current_profile
from jet.domain.resume import resolve_resume_suggestion
from jet.domain.rules import RULES_VERSION, screen
from jet.domain.taxonomy import LEGACY_VERDICT_MAP, VERDICT_LABELS
from jet.llm.quota import remaining_today
from jet.llm.versions import STALE_METHOD_BASELINE


def is_method_changed(judgement_row: sqlite3.Row) -> bool:
    """Check if LLM judgement's prompt_version or rule judgement's version is earlier than configured."""
    try:
        source = judgement_row["source"]
    except (KeyError, IndexError):
        source = None

    if source == "llm":
        try:
            p_ver = judgement_row["prompt_version"]
        except (KeyError, IndexError):
            p_ver = None
        if not p_ver or not str(p_ver).startswith("v"):
            return False
        try:
            old_v = int(str(p_ver)[1:])
            baseline_v = int(str(STALE_METHOD_BASELINE)[1:])
            return old_v < baseline_v
        except ValueError:
            return False

    if source == "rule":
        try:
            engine = judgement_row["engine"]
        except (KeyError, IndexError):
            engine = None
        if not engine:
            engine = "rules:r1"
        engine_str = str(engine)
        rule_ver = engine_str.split(":", 1)[1] if ":" in engine_str else engine_str
        try:
            old_v = int(rule_ver.lstrip("r"))
            cur_v = int(RULES_VERSION.lstrip("r"))
            return old_v < cur_v
        except (ValueError, TypeError):
            return False

    return False


def staleness(
    conn: sqlite3.Connection,
    judgement_row: sqlite3.Row,
) -> dict[str, bool]:
    """Check if the judgement is stale due to job, profile, or method changes."""
    job_row = conn.execute(
        "SELECT current_version_id FROM jobs WHERE id = ?",
        (judgement_row["job_id"],),
    ).fetchone()
    job_changed = bool(job_row and judgement_row["job_version_id"] != job_row["current_version_id"])

    curr_profile = get_current_profile(conn, judgement_row["user_id"])
    profile_changed = bool(curr_profile is None or judgement_row["profile_id"] != curr_profile["id"])

    method_changed = is_method_changed(judgement_row)

    return {
        "job_changed": job_changed,
        "profile_changed": profile_changed,
        "method_changed": method_changed,
    }


def latest_judgement(conn: sqlite3.Connection, user_id: str, job_id: int) -> sqlite3.Row | None:
    """Retrieve the latest active (superseded_by IS NULL) judgement for the job and user."""
    return conn.execute(
        "SELECT * FROM judgements WHERE user_id = ? AND job_id = ? AND superseded_by IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (user_id, job_id),
    ).fetchone()


def _insert_judgement(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    job_id: int,
    job_version_id: int,
    profile_id: int,
    rule_res: Any,
    now_str: str,
    origin: str,
    prompt_version: str,
    llm_status: str = "queued",
    reserve_id: bool = False,
    supersede_id: int | None = None,
) -> sqlite3.Row:
    """新建一条判断记录并返回它。

    规则粗筛没通过 → 规则判断（done / skip / rule，立即完成）；通过 → 大模型判断，状态为 llm_status。
    reserve_id：先预定新 id（MAX(id)+1）再插入；supersede_id 给出时把那条旧判断指向这个新 id。
    新判断可能与旧判断同一岗位版本、同一画像（只有提示词版本变了），会撞上"每组只有一条现行判断"的
    部分唯一索引，所以要先把旧判断指向新 id 再插入；superseded_by 外键是 DEFERRABLE INITIALLY DEFERRED。
    不预定时由数据库自动编号（首次判断）。调用方负责事务和唯一索引冲突的处理。
    """
    new_id = None
    if reserve_id or supersede_id is not None:
        new_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM judgements").fetchone()[0]
        if supersede_id is not None:
            conn.execute("UPDATE judgements SET superseded_by = ? WHERE id = ?", (new_id, supersede_id))
    if not rule_res.passed:
        values = ("done", "skip", "rule", json.dumps(rule_res.hits[:3], ensure_ascii=False), "rule",
                  f"rules:{RULES_VERSION}", now_str)
    else:
        values = (llm_status, None, None, None, prompt_version, None, None)
    status, verdict, source, reasons, pv, engine, finished_at = values
    cur = conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, reasons, prompt_version, engine, rule_result, created_at, finished_at, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id, user_id, job_id, job_version_id, profile_id, status, verdict, source, reasons, pv, engine,
         rule_res.to_json(), now_str, finished_at, origin),
    )
    return conn.execute("SELECT * FROM judgements WHERE id = ?", (new_id or cur.lastrowid,)).fetchone()


def request_judgement(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    job_row: sqlite3.Row,
    has_llm_key: bool = True,
    prompt_version: str,
) -> tuple[sqlite3.Row | None, bool, str | None]:
    """
    Create or retrieve judgement when a job detail page is observed.

    Returns (judgement_row, is_newly_queued, notice).
    """
    if job_row["completeness"] == "list_only":
        return None, False, None

    profile = get_current_profile(conn, user_id)
    if profile is None:
        return None, False, None

    latest = latest_judgement(conn, user_id, job_row["id"])
    if latest is not None:
        if (
            latest["status"] == "interrupted"
            and latest["job_version_id"] == job_row["current_version_id"]
            and latest["profile_id"] == profile["id"]
        ):
            if not has_llm_key:
                return latest, False, "no_llm_key"
            conn.execute(
                "UPDATE judgements SET status = 'queued', error = NULL WHERE id = ?",
                (latest["id"],),
            )
            updated = conn.execute("SELECT * FROM judgements WHERE id = ?", (latest["id"],)).fetchone()
            return updated, True, None

        # If already queued or running, return as is (FR-052)
        if latest["status"] in ("queued", "running"):
            return latest, False, None

        # Check if the existing judgement is stale
        stale_info = staleness(conn, latest)
        if any(stale_info.values()):
            from jet.domain.job_status import status_for
            u_status = status_for(conn, user_id, job_row["id"])
            current_status = u_status["status"] if u_status else None

            # If user status is not skipped or applied (saved or None), auto-refresh
            if current_status not in ("skipped", "applied"):
                version_row = conn.execute(
                    "SELECT * FROM job_versions WHERE id = ?",
                    (job_row["current_version_id"],),
                ).fetchone()
                rule_res = screen(version_row, profile)
                now_str = utc_now()

                common = dict(
                    user_id=user_id, job_id=job_row["id"], job_version_id=job_row["current_version_id"],
                    profile_id=profile["id"], rule_res=rule_res, now_str=now_str, origin="auto_refresh",
                    prompt_version=prompt_version, supersede_id=latest["id"],
                )
                if rule_res.passed:
                    if not has_llm_key:
                        return latest, False, "no_llm_key"
                    rem = remaining_today(conn, user_id)
                    if rem <= 0:
                        return latest, False, "auto_refresh_quota"

                if conn.in_transaction:
                    new_j = _insert_judgement(conn, **common)
                else:
                    with transaction(conn, immediate=True):
                        new_j = _insert_judgement(conn, **common)
                return new_j, rule_res.passed, None

        return latest, False, None

    # No active judgement exists: run screening rules
    version_row = conn.execute(
        "SELECT * FROM job_versions WHERE id = ?",
        (job_row["current_version_id"],),
    ).fetchone()
    rule_res = screen(version_row, profile)
    now_str = utc_now()

    llm_status = "queued"
    if rule_res.passed:
        if not has_llm_key:
            return None, False, "no_llm_key"
        if remaining_today(conn, user_id) <= 0:
            llm_status = "quota_exhausted"
    try:
        inserted = _insert_judgement(
            conn, user_id=user_id, job_id=job_row["id"], job_version_id=job_row["current_version_id"],
            profile_id=profile["id"], rule_res=rule_res, now_str=now_str, origin="initial",
            prompt_version=prompt_version, llm_status=llm_status,
        )
        return inserted, inserted["status"] == "queued", None
    except sqlite3.IntegrityError:
        return latest_judgement(conn, user_id, job_row["id"]), False, None


def rejudge(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    platform_job_id: str,
    force: bool = False,
    has_llm_key: bool = True,
    prompt_version: str,
) -> tuple[sqlite3.Row, bool]:
    """
    Trigger manual re-judgement for a job.

    Returns (judgement_row, is_queued).
    """
    job = conn.execute(
        "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (platform_job_id,),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "岗位不存在"})

    if job["completeness"] == "list_only":
        raise HTTPException(status_code=409, detail={"error": "list_only", "message": "仅有列表信息的岗位无法判断"})

    profile = get_current_profile(conn, user_id)
    if profile is None:
        raise HTTPException(status_code=409, detail={"error": "no_profile", "message": "请先设置画像"})

    if not has_llm_key:
        raise HTTPException(
            status_code=409,
            detail={"error": "no_llm_key", "message": "请先在设置页填写 DeepSeek API Key"},
        )

    latest = latest_judgement(conn, user_id, job["id"])

    # If already queued or running, return as is (FR-024)
    if latest and latest["status"] in ("queued", "running"):
        return latest, False

    is_stale = latest is None or any(staleness(conn, latest).values())

    if not is_stale and latest is not None:
        if latest["status"] in ("failed", "interrupted"):
            conn.execute(
                "UPDATE judgements SET status = 'queued', error = NULL, origin = 'manual' WHERE id = ?",
                (latest["id"],),
            )
            updated = conn.execute("SELECT * FROM judgements WHERE id = ?", (latest["id"],)).fetchone()
            return updated, True
        if latest["status"] == "quota_exhausted":
            rem = remaining_today(conn, user_id)
            if rem > 0:
                conn.execute(
                    "UPDATE judgements SET status = 'queued', error = NULL, origin = 'manual' WHERE id = ?",
                    (latest["id"],),
                )
                updated = conn.execute("SELECT * FROM judgements WHERE id = ?", (latest["id"],)).fetchone()
                return updated, True
            else:
                return latest, False
        if latest["status"] == "done":
            if not force:
                return latest, False

    # Is stale or no judgement exists: create new judgement
    version_row = conn.execute(
        "SELECT * FROM job_versions WHERE id = ?",
        (job["current_version_id"],),
    ).fetchone()
    rule_res = screen(version_row, profile)
    now_str = utc_now()

    def _execute():
        llm_status = "queued"
        if rule_res.passed and remaining_today(conn, user_id) <= 0:
            llm_status = "quota_exhausted"
        row = _insert_judgement(
            conn, user_id=user_id, job_id=job["id"], job_version_id=job["current_version_id"],
            profile_id=profile["id"], rule_res=rule_res, now_str=now_str, origin="manual",
            prompt_version=prompt_version, llm_status=llm_status, reserve_id=True,
            supersede_id=latest["id"] if latest else None,
        )
        return row, row["status"] == "queued"

    if conn.in_transaction:
        return _execute()
    else:
        with transaction(conn, immediate=True):
            return _execute()


def effective_verdict(
    judgement_row: Mapping[str, Any], label_row: Mapping[str, Any] | None
) -> tuple[str | None, str | None, bool]:
    """岗位卡片和岗位库列表共用的「生效结论」：返回 (verdict, model_verdict, verdict_overridden)。

    只有已完成的判断有结论；旧三档结论按 LEGACY_VERDICT_MAP 换成四档；用户标注了总体结论时以用户的为准。
    """
    if judgement_row["status"] != "done":
        return None, None, False
    raw = judgement_row["verdict"]
    model_verdict = LEGACY_VERDICT_MAP.get(raw, raw) if raw is not None else None
    if label_row and label_row.get("overall"):
        overall = label_row["overall"]
        return LEGACY_VERDICT_MAP.get(overall, overall), model_verdict, True
    return model_verdict, model_verdict, False


def to_api(
    conn: sqlite3.Connection,
    judgement_row: sqlite3.Row,
) -> dict[str, Any]:
    """Convert a database judgement row to the API contract Judgement object."""
    status = judgement_row["status"]
    source = judgement_row["source"]
    reasons_raw = judgement_row["reasons"]
    reasons = json.loads(reasons_raw) if reasons_raw else []
    judged_at = judgement_row["finished_at"]
    error = judgement_row["error"]
    prompt_version = judgement_row["prompt_version"]
    engine = judgement_row["engine"]

    facts_raw = judgement_row["facts"]
    facts = json.loads(facts_raw) if facts_raw else None

    derivation_raw = judgement_row["derivation"]
    derivation = json.loads(derivation_raw) if derivation_raw else None

    verdict_reason = None
    try:
        verdict_reason = judgement_row["verdict_reason"]
    except (IndexError, KeyError):
        verdict_reason = None

    resume_suggestion = None
    try:
        r_dir = judgement_row["resume_direction"]
        r_reason = judgement_row["resume_reason"]
        u_id = judgement_row.get("user_id") if hasattr(judgement_row, "get") else judgement_row["user_id"]
        resume_suggestion = resolve_resume_suggestion(conn, u_id, r_dir, r_reason)
    except (IndexError, KeyError):
        resume_suggestion = None

    hr_questions = None
    try:
        hr_q_raw = judgement_row["hr_questions"]
        if hr_q_raw:
            hr_questions = json.loads(hr_q_raw)
    except (IndexError, KeyError):
        hr_questions = None

    review = None
    try:
        review_raw = judgement_row["review"]
        if review_raw:
            review = json.loads(review_raw) if isinstance(review_raw, str) else review_raw
    except (IndexError, KeyError, ValueError):
        review = None

    v_row = conn.execute(
        "SELECT salary_visible FROM job_versions WHERE id = ?",
        (judgement_row["job_version_id"],),
    ).fetchone()
    salary_visible = bool(v_row["salary_visible"]) if v_row else True

    stale_dict = staleness(conn, judgement_row)

    # replacing lookup
    replacing = None
    if status in ("queued", "running", "failed", "interrupted", "quota_exhausted"):
        old_row = conn.execute(
            "SELECT verdict, finished_at FROM judgements WHERE superseded_by = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
            (judgement_row["id"],),
        ).fetchone()
        if old_row and old_row["verdict"]:
            v_raw = old_row["verdict"]
            v_mapped = LEGACY_VERDICT_MAP.get(v_raw, v_raw)
            v_label = VERDICT_LABELS.get(v_mapped)
            replacing = {
                "verdict": v_mapped,
                "verdict_label": v_label,
                "judged_at": old_row["finished_at"],
            }

    origin = None
    try:
        origin = judgement_row["origin"]
    except (IndexError, KeyError):
        origin = None

    # Label lookup
    label_row = labels.label_for(conn, judgement_row["user_id"], judgement_row["job_version_id"])
    verdict, model_verdict, verdict_overridden = effective_verdict(judgement_row, label_row)
    label_data: dict[str, Any] | None = None
    if label_row:
        corrected = labels.is_corrected(label_row, judgement_row)
        sec_wt = label_row.get("secondary_work_types")
        if isinstance(sec_wt, str) and sec_wt:
            try:
                sec_wt = json.loads(sec_wt)
            except Exception:
                sec_wt = None
        label_data = {
            "work_type": label_row.get("work_type"),
            "work_subtype": label_row.get("work_subtype"),
            "secondary_work_types": sec_wt,
            "sales_level": label_row.get("sales_level"),
            "experience_fit": label_row.get("experience_fit"),
            "work_intensity": label_row.get("work_intensity"),
            "overall": label_row.get("overall"),
            "note": label_row.get("note"),
            "corrected": corrected,
        }

    return {
        "status": status,
        "verdict": verdict,
        "model_verdict": model_verdict,
        "verdict_overridden": verdict_overridden,
        "source": source,
        "reasons": reasons,
        "judged_at": judged_at,
        "stale": stale_dict,
        "error": error,
        "salary_visible": salary_visible,
        "prompt_version": prompt_version,
        "verdict_reason": verdict_reason,
        "resume_suggestion": resume_suggestion,
        "hr_questions": hr_questions,
        "review": review,
        "engine": engine,
        "facts": facts,
        "derivation": derivation,
        "label": label_data,
        "origin": origin,
        "replacing": replacing,
    }


def rule_excluded_today(conn: sqlite3.Connection, user_id: str) -> int:
    """指标"规则排除数（今日）"：今日被规则直接排除的岗位数。

    每个岗位只算一次、只看现在的判断（superseded_by IS NULL）：被重新判断取代的旧规则判断不重复计数
    （2026-09-25 冒烟测试发现强制重新判断会把同一岗位数成 2 次）。"今日"按本机时区。
    """
    start_utc, end_utc, _ = local_day_bounds_utc()
    row = conn.execute(
        "SELECT COUNT(DISTINCT job_id) FROM judgements "
        "WHERE user_id = ? AND superseded_by IS NULL AND status = 'done' AND source = 'rule' "
        "AND verdict IN ('unfit', 'skip') AND finished_at >= ? AND finished_at < ?",
        (user_id, start_utc, end_utc),
    ).fetchone()
    return int(row[0]) if row else 0
