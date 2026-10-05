import json
import sqlite3
from typing import Any, Mapping
from fastapi import HTTPException

from jet.db.store import utc_now
from jet.domain.taxonomy import (
    CATEGORIES,
    WORK_INTENSITY,
    VERDICTS,
    LEGACY_VERDICT_MAP,
    is_valid_pair,
    normalize_secondary,
)

WORK_TYPES = tuple(CATEGORIES.keys())
SALES_LEVELS = ("高", "中", "低")
EXPERIENCE_FITS = ("满足", "差一点", "不满足", "无法判断")
WORK_INTENSITIES = tuple(WORK_INTENSITY)
OVERALLS = tuple(VERDICTS)


def label_for(conn: sqlite3.Connection, user_id: str, job_version_id: int) -> dict[str, Any] | None:
    """Retrieve the label row for given user and job version, if any."""
    row = conn.execute(
        "SELECT * FROM labels WHERE user_id = ? AND job_version_id = ?",
        (user_id, job_version_id),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    sec = d.get("secondary_work_types")
    if isinstance(sec, str) and sec:
        try:
            d["secondary_work_types"] = json.loads(sec)
        except Exception:
            d["secondary_work_types"] = None
    elif sec is None:
        d["secondary_work_types"] = None
    return d


def is_corrected(label_row: Mapping[str, Any] | None, judgement_row: Mapping[str, Any] | None) -> bool:
    """
    Determine if the label constitutes a correction to the judgement.

    A correction is true if any filled fact in label disagrees with judgement.facts value,
    or if filled overall disagrees with judgement.verdict.
    次要类型 (secondary_work_types) 和细分 (work_subtype) 只记录、展示，不参与纠正判定（FR-035）。
    When judgement has no facts (e.g. rule or v1), only compares overall.
    If judgement is null or not done, returns False.
    """
    if label_row is None or judgement_row is None:
        return False
    if judgement_row.get("status") != "done":
        return False

    facts_raw = judgement_row.get("facts")
    facts: dict[str, Any] | None = None
    if isinstance(facts_raw, str) and facts_raw:
        try:
            facts = json.loads(facts_raw)
        except Exception:
            facts = None
    elif isinstance(facts_raw, dict):
        facts = facts_raw

    if facts:
        # Check work_type (只比较主要大类，细分和次要类型不参与纠正判定)
        if label_row.get("work_type") is not None:
            wt_val = facts.get("work_type", {}).get("value")
            if label_row["work_type"] != wt_val:
                return True
        # Check sales_level
        if label_row.get("sales_level") is not None:
            sl_val = facts.get("sales_level", {}).get("value")
            if label_row["sales_level"] != sl_val:
                return True
        # Check experience_fit
        if label_row.get("experience_fit") is not None:
            ef_val = facts.get("experience", {}).get("value")
            if label_row["experience_fit"] != ef_val:
                return True
        # Check work_intensity (判断没有 work_intensity 时跳过)
        if label_row.get("work_intensity") is not None:
            if "work_intensity" in facts:
                wi_val = facts.get("work_intensity", {}).get("value")
                if label_row["work_intensity"] != wi_val:
                    return True
        # Backwards compatibility for overtime in older facts / labels
        elif label_row.get("overtime") is not None:
            if "overtime" in facts:
                ot_val = facts.get("overtime", {}).get("value")
                if label_row["overtime"] != ot_val:
                    return True
        # Check overall
        if label_row.get("overall") is not None:
            j_verdict = judgement_row.get("verdict")
            if j_verdict in LEGACY_VERDICT_MAP:
                j_verdict = LEGACY_VERDICT_MAP[j_verdict]
            lbl_overall = label_row["overall"]
            if lbl_overall in LEGACY_VERDICT_MAP:
                lbl_overall = LEGACY_VERDICT_MAP[lbl_overall]
            if lbl_overall != j_verdict:
                return True
    else:
        # Without facts, only compare overall with verdict
        if label_row.get("overall") is not None:
            j_verdict = judgement_row.get("verdict")
            if j_verdict in LEGACY_VERDICT_MAP:
                j_verdict = LEGACY_VERDICT_MAP[j_verdict]
            lbl_overall = label_row["overall"]
            if lbl_overall in LEGACY_VERDICT_MAP:
                lbl_overall = LEGACY_VERDICT_MAP[lbl_overall]
            if lbl_overall != j_verdict:
                return True

    return False


def save_label(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    platform_job_id: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Validate and save a label for the current job version.

    Upserts into labels table, associating with the active judgement on the version.
    """
    job = conn.execute(
        "SELECT * FROM jobs WHERE platform = 'boss' AND platform_job_id = ?",
        (platform_job_id,),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "岗位不存在"})

    if job["completeness"] == "list_only":
        raise HTTPException(status_code=409, detail={"error": "list_only", "message": "仅有列表信息的岗位无法标注"})

    work_type = data.get("work_type")
    work_subtype = data.get("work_subtype")
    raw_secondary = data.get("secondary_work_types")
    sales_level = data.get("sales_level")
    experience_fit = data.get("experience_fit")
    work_intensity = data.get("work_intensity")
    if work_intensity is None and "overtime" in data:
        ot = data.get("overtime")
        if ot in WORK_INTENSITIES:
            work_intensity = ot
        elif ot == "明确双休或不加班":
            work_intensity = "双休"
        elif ot == "未提及":
            work_intensity = "未提及"
        elif ot is not None:
            work_intensity = ot

    overall = data.get("overall")
    note = data.get("note")

    # 1. work_type validation
    if work_type is not None and work_type not in WORK_TYPES:
        raise ValueError(f"work_type '{work_type}' 不在合法选项内: {WORK_TYPES}")

    # 2. work_subtype validation
    if work_subtype is not None:
        if not work_type:
            raise ValueError("work_subtype 不为空时 work_type 不能为空")
        if not is_valid_pair(work_type, work_subtype):
            raise ValueError(f"work_subtype '{work_subtype}' 不属于工作类型 '{work_type}'")

    # 3. secondary_work_types validation
    secondary_work_types: list[dict[str, str | None]] | None = None
    if raw_secondary is not None:
        if not isinstance(raw_secondary, list):
            raise ValueError("secondary_work_types 必须是列表或 null")
        if len(raw_secondary) == 0:
            secondary_work_types = None
        else:
            norm_sec = normalize_secondary(raw_secondary, primary_category=work_type, primary_subtype=work_subtype)
            secondary_work_types = norm_sec if norm_sec else None

    # 4. At least one fact or overall must be non-null
    if all(x is None for x in [work_type, work_subtype, secondary_work_types, sales_level, experience_fit, work_intensity, overall]):
        raise ValueError("至少填写一项事实或总体结论")

    if sales_level is not None and sales_level not in SALES_LEVELS:
        raise ValueError(f"sales_level '{sales_level}' 不在合法选项内: {SALES_LEVELS}")

    if experience_fit is not None and experience_fit not in EXPERIENCE_FITS:
        raise ValueError(f"experience_fit '{experience_fit}' 不在合法选项内: {EXPERIENCE_FITS}")

    if work_intensity is not None and work_intensity not in WORK_INTENSITIES:
        raise ValueError(f"work_intensity '{work_intensity}' 不在合法选项内: {WORK_INTENSITIES}")

    if overall is not None and overall not in OVERALLS:
        raise ValueError(f"overall '{overall}' 不在合法选项内: {OVERALLS}")

    cleaned_note: str | None = None
    if note is not None:
        cleaned_note = str(note).strip()
        if len(cleaned_note) > 100:
            raise ValueError("note 不能超过 100 字")

    job_version_id = job["current_version_id"]
    active_j = conn.execute(
        "SELECT id FROM judgements WHERE user_id = ? AND job_version_id = ? AND superseded_by IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (user_id, job_version_id),
    ).fetchone()
    judgement_id = active_j["id"] if active_j else None

    sec_json = json.dumps(secondary_work_types, ensure_ascii=False) if secondary_work_types is not None else None
    now_str = utc_now()
    conn.execute(
        """
        INSERT INTO labels (
            user_id, job_version_id, judgement_id, work_type, work_subtype, secondary_work_types,
            sales_level, experience_fit, work_intensity, overall, note, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, job_version_id) DO UPDATE SET
            judgement_id = excluded.judgement_id,
            work_type = excluded.work_type,
            work_subtype = excluded.work_subtype,
            secondary_work_types = excluded.secondary_work_types,
            sales_level = excluded.sales_level,
            experience_fit = excluded.experience_fit,
            work_intensity = excluded.work_intensity,
            overall = excluded.overall,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            job_version_id,
            judgement_id,
            work_type,
            work_subtype,
            sec_json,
            sales_level,
            experience_fit,
            work_intensity,
            overall,
            cleaned_note,
            now_str,
            now_str,
        ),
    )

    return label_for(conn, user_id, job_version_id)  # type: ignore[return-value]


def label_counts(conn: sqlite3.Connection, user_id: str) -> dict[str, int]:
    """Return total labels and count of labels with overall verdict."""
    total_row = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    with_overall_row = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE user_id = ? AND overall IS NOT NULL",
        (user_id,),
    ).fetchone()
    return {
        "total": total_row[0] if total_row else 0,
        "with_overall": with_overall_row[0] if with_overall_row else 0,
    }
