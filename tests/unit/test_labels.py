import json
from pathlib import Path
from fastapi import HTTPException
import pytest

from jet.db.store import open_db, utc_now
from jet.domain.job_status import list_my_jobs
from jet.domain.judgements import to_api
from jet.domain.labels import (
    is_corrected,
    label_counts,
    label_for,
    save_label,
)


@pytest.fixture
def label_test_db(data_dir: Path):
    conn = open_db(data_dir)
    now = utc_now()

    # Full job
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_full', 'full', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, "
        "city, description, content_hash, created_at) "
        "VALUES (10, 1, 1, 'detail', 'Python开发', 1, 1, '深圳', '后端开发职责', 'h1', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 10 WHERE id = 1")

    # List only job
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (2, 'boss', 'job_list', 'list_only', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, "
        "city, description, content_hash, created_at) "
        "VALUES (20, 2, 1, 'list', 'Python仅列表', 0, 0, '深圳', NULL, 'h2', ?)",
        (now,),
    )
    conn.execute("UPDATE jobs SET current_version_id = 20 WHERE id = 2")

    # Profile
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (1, 'me', 1, '[\"Python\"]', '[]', '[\"深圳\"]', '[]', ?)",
        (now,),
    )

    # Done Judgement on Job 1 Version 10
    sample_facts = {
        "summary": {"text": "开发工作", "quotes": []},
        "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": [], "quotes": []},
        "sales_level": {"value": "低", "signals": []},
        "experience": {"requirement": None, "value": "满足", "gap": ""},
        "work_intensity": {"value": "双休", "quotes": []},
    }
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, "
        "source, prompt_version, facts, rule_result, created_at, finished_at) "
        "VALUES (100, 'me', 1, 10, 1, 'done', 'apply', 'llm', 'v4', ?, '{}', ?, ?)",
        (json.dumps(sample_facts, ensure_ascii=False), now, now),
    )

    yield conn
    conn.close()


def test_save_label_list_only_rejected(label_test_db):
    conn = label_test_db
    with pytest.raises(HTTPException) as exc_info:
        save_label(conn, user_id="me", platform_job_id="job_list", data={"work_type": "运营"})
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "list_only"


def test_save_label_validation_errors(label_test_db):
    conn = label_test_db

    # 1. All null -> ValueError
    with pytest.raises(ValueError, match="至少填写一项"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={})

    with pytest.raises(ValueError, match="至少填写一项"):
        save_label(
            conn,
            user_id="me",
            platform_job_id="job_full",
            data={"work_type": None, "sales_level": None, "note": "仅有备注"},
        )

    # 2. Invalid work_type enum
    with pytest.raises(ValueError, match="work_type"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"work_type": "非法分类"})

    # 3. work_subtype without work_type
    with pytest.raises(ValueError, match="work_subtype"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"work_subtype": "技术支持与实施"})

    # 4. work_subtype not belonging to work_type
    with pytest.raises(ValueError, match="work_subtype"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"work_type": "运营", "work_subtype": "技术支持与实施"})

    # 5. Invalid sales_level enum
    with pytest.raises(ValueError, match="sales_level"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"sales_level": "极高"})

    # 6. Invalid experience_fit enum
    with pytest.raises(ValueError, match="experience_fit"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"experience_fit": "优秀"})

    # 7. Invalid work_intensity enum
    with pytest.raises(ValueError, match="work_intensity"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"work_intensity": "偶尔加班"})

    # 8. Invalid overall enum
    with pytest.raises(ValueError, match="overall"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"overall": "unknown"})

    # 9. Note > 100 characters
    long_note = "a" * 101
    with pytest.raises(ValueError, match="note"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"work_type": "数据与技术", "note": long_note})

    # 10. Invalid secondary_work_types option
    with pytest.raises(ValueError, match="未知的工作类型"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"secondary_work_types": ["打杂"]})

    # 11. Empty secondary_work_types alone counts as all null
    with pytest.raises(ValueError, match="至少填写一项"):
        save_label(conn, user_id="me", platform_job_id="job_full", data={"secondary_work_types": []})


def test_save_label_success_and_overwrite(label_test_db):
    conn = label_test_db

    # 1. First save with work_subtype and secondary_work_types
    row1 = save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={
            "work_type": "运营",
            "work_subtype": "用户运营",
            "secondary_work_types": [{"category": "市场与销售", "subtype": "市场营销与品牌"}],
            "sales_level": "中",
            "experience_fit": "差一点",
            "work_intensity": "单休",
            "overall": "try",
            "note": "职责偏重用户运营",
        },
    )
    assert row1["work_type"] == "运营"
    assert row1["work_subtype"] == "用户运营"
    assert row1["secondary_work_types"] == [{"category": "市场与销售", "subtype": "市场营销与品牌"}]
    assert row1["sales_level"] == "中"
    assert row1["work_intensity"] == "单休"
    assert row1["overall"] == "try"
    assert row1["judgement_id"] == 100
    assert row1["note"] == "职责偏重用户运营"

    # Read back via label_for
    r1 = label_for(conn, "me", 10)
    assert r1 is not None
    assert r1["work_type"] == "运营"
    assert r1["work_subtype"] == "用户运营"
    assert r1["secondary_work_types"] == [{"category": "市场与销售", "subtype": "市场营销与品牌"}]
    assert r1["work_intensity"] == "单休"

    # Total labels is 1
    cnt1 = label_counts(conn, "me")
    assert cnt1 == {"total": 1, "with_overall": 1}

    # 2. Overwrite on the same job version (clearing secondary_work_types by passing empty list or None)
    row2 = save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={
            "work_type": "市场与销售",
            "work_subtype": "销售与商务拓展",
            "secondary_work_types": [],
            "sales_level": "高",
            "experience_fit": "不满足",
            "work_intensity": "高强度",
            "overall": "skip",
            "note": "确定是变相销售",
        },
    )
    # Replaces the row (same ID, updated fields)
    assert row2["id"] == row1["id"]
    assert row2["work_type"] == "市场与销售"
    assert row2["work_subtype"] == "销售与商务拓展"
    assert row2["secondary_work_types"] is None
    assert row2["sales_level"] == "高"
    assert row2["work_intensity"] == "高强度"
    assert row2["overall"] == "skip"
    assert row2["note"] == "确定是变相销售"

    # Still total 1
    cnt2 = label_counts(conn, "me")
    assert cnt2 == {"total": 1, "with_overall": 1}


def test_save_label_secondary_only_counts_as_at_least_one(label_test_db):
    conn = label_test_db
    # Only secondary_work_types filled, main work_type is None -> valid!
    row = save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={"secondary_work_types": [{"category": "运营", "subtype": None}]},
    )
    assert row["work_type"] is None
    assert row["secondary_work_types"] == [{"category": "运营", "subtype": None}]

    # Read back via label_for
    r = label_for(conn, "me", 10)
    assert r is not None
    assert r["work_type"] is None
    assert r["secondary_work_types"] == [{"category": "运营", "subtype": None}]


def test_is_corrected_logic():
    judgement_facts = {
        "work_type": {"value": "数据与技术", "subtype": "技术支持与实施", "secondary": []},
        "sales_level": {"value": "低"},
        "experience": {"value": "满足"},
        "work_intensity": {"value": "双休"},
    }
    j_done = {
        "status": "done",
        "verdict": "apply",
        "facts": json.dumps(judgement_facts),
    }

    # 1. Same facts and overall -> not corrected
    label_same = {
        "work_type": "数据与技术",
        "work_subtype": "技术支持与实施",
        "sales_level": "低",
        "experience_fit": "满足",
        "work_intensity": "双休",
        "overall": "apply",
    }
    assert is_corrected(label_same, j_done) is False

    # 2. Subtype difference does NOT count as correction (纠正只比大类)
    label_diff_subtype = dict(label_same, work_subtype="大数据与数据分析")
    assert is_corrected(label_diff_subtype, j_done) is False

    # 3. Partial filled matching facts -> not corrected
    label_partial = {
        "work_type": "数据与技术",
        "sales_level": None,
        "experience_fit": None,
        "work_intensity": None,
        "overall": None,
    }
    assert is_corrected(label_partial, j_done) is False

    # 4. Disagreement in work_type (primary category) -> is corrected
    label_diff_wt = dict(label_same, work_type="市场与销售")
    assert is_corrected(label_diff_wt, j_done) is True

    # 5. Disagreement in sales_level -> is corrected
    label_diff_sl = dict(label_same, sales_level="高")
    assert is_corrected(label_diff_sl, j_done) is True

    # 6. Disagreement in experience_fit -> is corrected
    label_diff_ef = dict(label_same, experience_fit="不满足")
    assert is_corrected(label_diff_ef, j_done) is True

    # 7. Disagreement in work_intensity -> is corrected
    label_diff_wi = dict(label_same, work_intensity="高强度")
    assert is_corrected(label_diff_wi, j_done) is True

    # 8. Disagreement in overall verdict -> is corrected
    label_diff_overall = dict(label_same, overall="skip")
    assert is_corrected(label_diff_overall, j_done) is True

    # 9. Judgement not done or null -> not corrected
    j_running = {"status": "running", "verdict": None, "facts": None}
    assert is_corrected(label_diff_wt, j_running) is False
    assert is_corrected(label_diff_wt, None) is False

    # 10. Judgement has no facts (e.g. v1): only compares overall with LEGACY_VERDICT_MAP mapping
    j_v1 = {"status": "done", "verdict": "fit", "facts": None}
    # fit maps to apply, so overall='apply' matches
    assert is_corrected({"work_type": "运营", "overall": "apply"}, j_v1) is False
    assert is_corrected({"work_type": "运营", "overall": "fit"}, j_v1) is False
    assert is_corrected({"work_type": "运营", "overall": "skip"}, j_v1) is True

    # 11. Judgement has legacy verdict in v2/v3
    j_v2 = {
        "status": "done",
        "verdict": "unfit",
        "facts": json.dumps({
            "work_type": {"value": "数据与技术"},
            "sales_level": {"value": "低"},
            "experience": {"value": "满足"},
            # Notice: no work_intensity in older facts
            "overtime": {"value": "明确双休或不加班"},
        }),
    }
    # overall 'skip' matches 'unfit' via LEGACY_VERDICT_MAP, work_intensity is skipped because not in facts
    label_v2 = {
        "work_type": "数据与技术",
        "sales_level": "低",
        "experience_fit": "满足",
        "work_intensity": "双休",
        "overall": "skip",
    }
    assert is_corrected(label_v2, j_v2) is False

    # 12. Secondary work types difference does NOT count as correction
    label_diff_sec = dict(label_same, secondary_work_types=[{"category": "运营", "subtype": None}])
    assert is_corrected(label_diff_sec, j_done) is False


def test_label_counts_without_overall(label_test_db):
    conn = label_test_db
    # Save a label without overall
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={"work_type": "数据与技术", "overall": None},
    )
    counts = label_counts(conn, "me")
    assert counts["total"] == 1
    assert counts["with_overall"] == 0


def test_verdict_override_to_api_and_job_status(label_test_db):
    """测试 to_api 和 job_status 的覆盖与不覆盖逻辑。
    1. 无标注：不覆盖，model_verdict 等于模型结论，verdict_overridden 为 False。
    2. 标注 overall 为空：不覆盖，verdict 保持模型结论。
    3. 标注 overall 非空且判断已完成：覆盖，verdict/verdict_label 改为标注结论，model_verdict 为原结论，verdict_overridden 为 True。
    4. 判断未完成：不覆盖，verdict/model_verdict 均为 None，verdict_overridden 为 False。
    """
    conn = label_test_db

    # 1. 无标注：不覆盖
    j_row = conn.execute("SELECT * FROM judgements WHERE id = 100").fetchone()
    api_res = to_api(conn, j_row)
    assert api_res["verdict"] == "apply"
    assert api_res["model_verdict"] == "apply"
    assert api_res["verdict_overridden"] is False

    jobs_res = list_my_jobs(conn, user_id="me", filter="all_jobs")
    assert len(jobs_res["items"]) == 1
    item = jobs_res["items"][0]
    assert item["verdict"] == "apply"
    assert item["verdict_label"] == "适合投递"
    assert item["model_verdict"] == "apply"
    assert item["verdict_overridden"] is False

    # 2. 标注 overall 为空：不覆盖
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={"work_type": "运营", "overall": None},
    )
    api_res = to_api(conn, j_row)
    assert api_res["verdict"] == "apply"
    assert api_res["model_verdict"] == "apply"
    assert api_res["verdict_overridden"] is False

    jobs_res = list_my_jobs(conn, user_id="me", filter="all_jobs")
    item = jobs_res["items"][0]
    assert item["verdict"] == "apply"
    assert item["verdict_label"] == "适合投递"
    assert item["model_verdict"] == "apply"
    assert item["verdict_overridden"] is False

    # 3. 标注 overall 非空（skip）且判断已完成：覆盖
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_full",
        data={"work_type": "运营", "overall": "skip"},
    )
    api_res = to_api(conn, j_row)
    assert api_res["verdict"] == "skip"
    assert api_res["model_verdict"] == "apply"
    assert api_res["verdict_overridden"] is True

    jobs_res = list_my_jobs(conn, user_id="me", filter="all_jobs")
    item = jobs_res["items"][0]
    assert item["verdict"] == "skip"
    assert item["verdict_label"] == "不建议投"
    assert item["model_verdict"] == "apply"
    assert item["verdict_overridden"] is True

    # 4. 判断未完成：即使有标注也不覆盖
    conn.execute("UPDATE judgements SET status = 'running', verdict = NULL WHERE id = 100")
    j_running = conn.execute("SELECT * FROM judgements WHERE id = 100").fetchone()
    api_res = to_api(conn, j_running)
    assert api_res["verdict"] is None
    assert api_res["model_verdict"] is None
    assert api_res["verdict_overridden"] is False

    jobs_res = list_my_jobs(conn, user_id="me", filter="all_jobs")
    item = jobs_res["items"][0]
    assert item["verdict"] is None
    assert item["verdict_label"] is None
    assert item["model_verdict"] is None
    assert item["verdict_overridden"] is False
