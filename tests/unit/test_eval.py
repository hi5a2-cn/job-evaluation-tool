import json
from pathlib import Path
import httpx
import pytest

from jet.config import Settings
from jet.db.store import connect, open_db, utc_now
from jet.domain.hr_notes import save_hr_note
from jet.domain.labels import save_label
from jet.domain.profiles import save_profile
from jet.eval.runner import export_jobs, import_reference, report_eval, run_eval
from jet.llm.quota import remaining_today


@pytest.fixture
def eval_setup(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    now = utc_now()

    # 1. Profile
    save_profile(
        conn,
        "me",
        {
            "directions": ["Python开发"],
            "cities": ["深圳"],
            "work_preference": "做后端研发，不愿背销售业绩",
            "background": "计算机本科，3年后端经验",
        },
    )

    # 2. Insert 3 jobs with details
    jobs_info = [
        (
            1,
            "job_eval_1",
            "Python高级开发",
            "深圳",
            "负责后端接口研发与数据库优化，双休不加班，要求3年经验",
        ),
        (
            2,
            "job_eval_2",
            "运营拓展专员",
            "深圳",
            "负责市场营销与渠道对接，需要配合拜访客户完成获客转化指标，大小周加班",
        ),
        (
            3,
            "job_eval_3",
            "技术支持专家",
            "深圳",
            "负责核心客户的技术故障排障与系统部署，不涉及销售，正常工时",
        ),
    ]

    for jid, pid, title, city, desc in jobs_info:
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
            "VALUES (?, 'boss', ?, 'full', ?, ?)",
            (jid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, "
            "city, description, content_hash, created_at) "
            "VALUES (?, ?, 1, 'detail', ?, 1, 1, ?, ?, ?, ?)",
            (jid * 10, jid, title, city, desc, f"hash_{jid}", now),
        )
        conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (jid * 10, jid))

    # 3. Insert 3 labels
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_1",
        data={
            "work_type": "数据与技术",
            "work_subtype": "技术支持与实施",
            "sales_level": "低",
            "experience_fit": "满足",
            "work_intensity": "双休",
            "overall": "apply",
        },
    )
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_2",
        data={
            "work_type": "市场与销售",
            "work_subtype": "销售与商务拓展",
            "sales_level": "高",
            "experience_fit": "不满足",
            "work_intensity": "高强度",
            "overall": "skip",
        },
    )
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_3",
        data={
            "work_type": "数据与技术",
            "work_subtype": "技术支持与实施",
            "sales_level": "低",
            "experience_fit": "满足",
            "work_intensity": "未提及",
            "overall": "apply",
        },
    )

    conn.close()


def test_eval_run_and_report_lifecycle(eval_setup, data_dir: Path, settings: Settings):
    active_settings = Settings(
        data_dir=data_dir,
        llm_api_key="mock-key",
    )

    def mock_deepseek_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        max_tokens = body.get("max_tokens")

        if max_tokens == 400:  # Variant A (v1)
            content = json.dumps({"verdict": "fit", "reasons": ["v1理由匹配"]})
        else:  # Variant B, C (current prompt version)
            content = json.dumps({
                "facts": {
                    "summary": {"text": "开发工作", "quotes": ["负责后端接口研发"]},
                    "work_type": {
                        "value": "数据与技术",
                        "subtype": "技术支持与实施",
                        "secondary": [],
                        "quotes": [],
                    },
                    "sales_level": {"value": "低", "signals": []},
                    "experience": {
                        "requirement": "要求3年经验",
                        "requirement_type": "硬性",
                        "value": "满足",
                        "gap": "",
                    },
                    "work_intensity": {"value": "双休", "quotes": ["双休不加班"]},
                },
                "verdict": "apply",
                "derivation": ["事实与画像偏好一致"],
                "verdict_reason": "事实与画像偏好一致",
            })

        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    transport = httpx.MockTransport(mock_deepseek_handler)

    # 1. Run eval across all 3 variants
    run_data, table_str = run_eval(
        active_settings,
        variants=["A", "B", "C"],
        llm_transport=transport,
        backoff_delays=(0, 0),
    )

    assert "组合" in table_str
    assert "工作类型" in table_str
    assert "工作强度" in table_str
    assert "误判为可投" in table_str
    assert "A" in run_data["summary"]
    assert "B" in run_data["summary"]
    assert "C" in run_data["summary"]
    assert set(run_data["prompt_versions"]) == {"A", "B", "C"}

    # 2. Check result JSON file written to data_dir/evals/
    evals_dir = data_dir / "evals"
    assert evals_dir.is_dir()
    files = list(evals_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].name == f"{run_data['run_id']}.json"

    # 3. Check calls logged in database: all have purpose='eval' and judgement_id is NULL
    conn = connect(data_dir)
    try:
        calls = conn.execute("SELECT purpose, judgement_id FROM llm_calls").fetchall()
        assert len(calls) > 0
        for c in calls:
            assert c["purpose"] == "eval"
            assert c["judgement_id"] is None

        # Daily judge quota is untouched!
        assert remaining_today(conn, "me") == 150
    finally:
        conn.close()

    # 4. Offline report recomputes the same summary from the saved file
    rep_data, rep_table = report_eval(
        active_settings,
        run_id=run_data["run_id"],
    )
    assert rep_data["summary"] == run_data["summary"]


def test_eval_run_stops_when_max_calls_reached(eval_setup, data_dir: Path, settings: Settings):
    active_settings = Settings(
        data_dir=data_dir,
        llm_api_key="mock-key",
    )

    def mock_deepseek(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"verdict": "fit", "reasons": ["理由"]})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    transport = httpx.MockTransport(mock_deepseek)

    # Max calls set to 1 -> 1 call executes, then stops
    run_data, table_str = run_eval(
        active_settings,
        variants=["A", "B"],
        max_calls=1,
        llm_transport=transport,
        backoff_delays=(0, 0),
    )

    # Check that remaining variants marked with quota_exhausted
    second_item_b = run_data["items"][1]["results"].get("B")
    assert second_item_b is not None
    assert second_item_b["status"] == "quota_exhausted"
    assert "已达评测上限" in second_item_b["error"]


def test_export_jobs_structure_and_hr_notes(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    save_hr_note(conn, "me", "job_eval_1", "HR说不需要对客，纯内部系统")
    conn.close()

    out_file = data_dir / "exported_jobs.json"
    data = export_jobs(settings, out_file, limit=10)
    assert out_file.is_file()

    assert "exported_at" in data
    assert "profile" in data
    assert data["profile"]["background"] == "计算机本科，3年后端经验"
    assert data["profile"]["version_no"] >= 1

    assert "taxonomy" in data
    assert "categories" in data["taxonomy"]
    assert "work_intensity" in data["taxonomy"]
    assert "verdicts" in data["taxonomy"]

    assert "instructions" in data
    assert "hr_note" in data["instructions"]

    assert len(data["jobs"]) == 3
    # Check job_eval_1 has hr_note
    j1 = next(j for j in data["jobs"] if j["platform_job_id"] == "job_eval_1")
    assert j1["hr_note"] == "HR说不需要对客，纯内部系统"
    assert j1["title"] == "Python高级开发"
    assert j1["description"]

    # Check job_eval_2 has no hr_note
    j2 = next(j for j in data["jobs"] if j["platform_job_id"] == "job_eval_2")
    assert j2["hr_note"] is None


def test_import_reference_valid_and_invalid(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    save_hr_note(conn, "me", "job_eval_1", "HR说内部维护")
    conn.close()

    ref_payload = {
        "source": "model:gemini-3.8-flash-high",
        "labels": [
            # 1. Valid item
            {
                "job_version_id": 10,
                "work_type": "数据与技术",
                "work_subtype": "开发与测试",
                "secondary_work_types": [],
                "sales_level": "低",
                "experience_fit": "满足",
                "work_intensity": "双休",
                "overall": "apply",
                "rationale": "符合画像",
            },
            # 2. Invalid category
            {
                "job_version_id": 20,
                "work_type": "不存在的大类",
                "overall": "skip",
            },
            # 3. Invalid subtype
            {
                "job_version_id": 20,
                "work_type": "运营",
                "work_subtype": "技术支持与实施",  # 属于数据与技术
                "overall": "skip",
            },
            # 4. Non-existent job_version_id
            {
                "job_version_id": 99999,
                "work_type": "其他",
                "overall": "skip",
            },
            # 5. All fields null
            {
                "job_version_id": 30,
            },
        ],
    }

    ref_file = data_dir / "ref_labels.json"
    ref_file.write_text(json.dumps(ref_payload, ensure_ascii=False), encoding="utf-8")

    imported, skipped = import_reference(settings, ref_file)
    assert imported == 1
    assert skipped == 4

    conn = connect(data_dir)
    try:
        row = conn.execute("SELECT * FROM reference_labels WHERE job_version_id = 10").fetchone()
        assert row is not None
        assert row["source"] == "model:gemini-3.8-flash-high"
        assert row["used_hr_note"] == 1
        assert row["work_type"] == "数据与技术"
        assert row["work_subtype"] == "开发与测试"
    finally:
        conn.close()


def test_eval_field_merging_user_priority_and_source_composition(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    conn.execute("DELETE FROM labels")
    conn.execute("DELETE FROM reference_labels")

    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_1",
        data={
            "work_type": "数据与技术",
            "work_subtype": "开发与测试",
            "sales_level": "低",
            "overall": "apply",
        },
    )

    now = utc_now()
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, work_subtype, sales_level, experience_fit, work_intensity, overall, created_at
        ) VALUES ('me', 10, 'model:gemini-3.8-flash-high', 1, 0, '运营', '用户运营', '高', '满足', '双休', 'skip', ?)
        """,
        (now,),
    )
    conn.close()

    def mock_deepseek(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "开发", "quotes": ["负责后端接口研发"]},
                "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "硬性", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["完全匹配"],
            "verdict_reason": "完全匹配",
            "hr_questions": [],
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    active_settings = Settings(data_dir=data_dir, llm_api_key="mock-key")
    run_data, table_str = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=httpx.MockTransport(mock_deepseek),
    )

    item = run_data["items"][0]
    assert item["label"]["work_type"] == "数据与技术"  # user took priority
    assert item["label"]["sales_level"] == "低"        # user took priority
    assert item["label"]["overall"] == "apply"        # user took priority
    assert item["label"]["experience_fit"] == "满足"   # from reference
    assert item["label"]["work_intensity"] == "双休"   # from reference
    assert item["sources"]["work_type"] == "user"
    assert item["sources"]["experience_fit"] == "model:gemini-3.8-flash-high"

    # Source composition: user=4 (work_type, work_subtype, sales_level, overall), model=2 (experience_fit, work_intensity)
    comp = run_data["source_composition"]
    assert comp["user"] == 4
    assert comp["model"] == 2
    assert "参考答案来源：用户标注 4 项，模型参考标注 2 项（模型参考标注，非人工）" in table_str


def test_eval_with_only_reference_labels(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    conn.execute("DELETE FROM labels")
    conn.execute("DELETE FROM reference_labels")

    now = utc_now()
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, work_subtype, sales_level, experience_fit, work_intensity, overall, created_at
        ) VALUES ('me', 10, 'model:gemini-3.8-flash-high', 1, 0, '数据与技术', '开发与测试', '低', '满足', '双休', 'apply', ?)
        """,
        (now,),
    )
    conn.close()

    def mock_deepseek(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "开发", "quotes": ["负责后端接口研发"]},
                "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "硬性", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["完全匹配"],
            "verdict_reason": "完全匹配",
            "hr_questions": [],
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    active_settings = Settings(data_dir=data_dir, llm_api_key="mock-key")
    run_data, table_str = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=httpx.MockTransport(mock_deepseek),
    )
    assert len(run_data["items"]) == 1
    assert run_data["source_composition"]["user"] == 0
    assert run_data["source_composition"]["model"] > 0
    assert "模型参考标注" in table_str


def test_eval_without_any_labels_raises_helpful_error(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    conn.execute("DELETE FROM labels")
    conn.execute("DELETE FROM reference_labels")
    conn.close()

    active_settings = Settings(data_dir=data_dir)
    with pytest.raises(RuntimeError, match="请先导入参考标注"):
        run_eval(active_settings)


def test_export_jobs_sends_only_consented_profile_fields(data_dir):
    """导出给外部模型的文件只含用户明确同意的画像字段，不含配对、令牌、Key 等。"""
    import json as _json

    from jet.config import load_settings
    from jet.db.store import connect, init_db, utc_now
    from jet.domain.profiles import save_profile
    from jet.eval.runner import export_jobs

    init_db(data_dir)
    conn = connect(data_dir)
    save_profile(
        conn,
        "me",
        {
            "directions": ["数据分析"],
            "keywords": ["SQL"],
            "cities": ["深圳"],
            "min_monthly_k": 12,
            "exclude_keywords": ["销售"],
            "work_preference": "不想做销售",
            "background": "统计学本科",
        },
    )
    now = utc_now()
    conn.execute(
        "INSERT INTO pairings (user_id, extension_origin, token_hash, created_at) VALUES ('me', 'chrome-extension://x', 'secret-hash-value', ?)",
        (now,),
    )
    conn.close()

    settings = load_settings(data_dir)
    out = data_dir / "export.json"
    export_jobs(settings, out_path=out)
    text = out.read_text(encoding="utf-8")
    data = _json.loads(text)

    assert set(data["profile"]) == {
        "background",
        "work_preference",
        "preferred_cities",
        "excluded_cities",
        "min_monthly_k",
        "nonpref_min_monthly_k",
        "exclude_keywords",
        "version_no",
    }
    assert "cities" not in data["profile"]
    assert "directions" not in data["profile"] and "keywords" not in data["profile"]
    assert "secret-hash-value" not in text
    assert "chrome-extension://" not in text
    assert "token" not in text.lower() and "api_key" not in text.lower()


def test_eval_reference_disagreements(eval_setup, data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    conn.execute("DELETE FROM labels")
    conn.execute("DELETE FROM reference_labels")

    # 岗位 1 (job_eval_1, jv_id=10): 同时有用户标注与参考标注
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_1",
        data={
            "work_type": "市场与销售",
            "sales_level": "中",
            "overall": "skip",
        },
    )

    now = utc_now()
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, sales_level, overall, experience_fit, created_at
        ) VALUES ('me', 10, 'model:gemini-3.8-flash-high', 1, 0, '运营', '低', 'apply', '差一点', ?)
        """,
        (now,),
    )

    # 岗位 2 (job_eval_2, jv_id=20): 只有用户标注，没有参考标注 -> 不产生条目
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_2",
        data={
            "work_type": "市场与销售",
            "sales_level": "高",
            "overall": "skip",
        },
    )
    conn.close()

    def mock_deepseek(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "测试岗位", "quotes": []},
                "work_type": {"value": "市场与销售", "subtype": None, "secondary": [], "quotes": []},
                "sales_level": {"value": "中", "signals": []},
                "experience": {"requirement": None, "requirement_type": "硬性", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "skip",
            "derivation": ["不匹配"],
            "verdict_reason": "不匹配",
            "hr_questions": [],
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    active_settings = Settings(data_dir=data_dir, llm_api_key="mock-key")
    run_data, table_str = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=httpx.MockTransport(mock_deepseek),
    )

    disagreements = run_data.get("reference_disagreements")
    assert disagreements is not None
    assert len(disagreements) == 3

    fields = [d["field"] for d in disagreements]
    assert fields == ["work_type", "sales_level", "overall"]
    assert "experience_fit" not in fields

    # 没有参考标注的岗位不产生条目 (job_eval_2 没有条目)
    assert all(d["job_version_id"] == 10 for d in disagreements)
    assert all(d["platform_job_id"] == "job_eval_1" for d in disagreements)

    # 校验条目具体字段
    wt_item = next(d for d in disagreements if d["field"] == "work_type")
    assert wt_item["user_value"] == "市场与销售"
    assert wt_item["reference_value"] == "运营"
    assert wt_item["reference_source"] == "model:gemini-3.8-flash-high"

    sl_item = next(d for d in disagreements if d["field"] == "sales_level")
    assert sl_item["user_value"] == "中"
    assert sl_item["reference_value"] == "低"

    ov_item = next(d for d in disagreements if d["field"] == "overall")
    assert ov_item["user_value"] == "skip"
    assert ov_item["reference_value"] == "apply"

    # 打印内容断言
    assert "参考标注与人工标注不一致（以人工标注为准，共 3 处）：" in table_str
    assert "人工=不建议投，参考=适合投递" in table_str
    assert "工作类型（大类）：人工=市场与销售，参考=运营" in table_str
    assert "销售成分：人工=中，参考=低" in table_str

    # report_eval 读取并展示不一致
    rep_data, rep_table = report_eval(active_settings, run_id=run_data["run_id"])
    assert rep_data["reference_disagreements"] == disagreements
    assert "人工=不建议投，参考=适合投递" in rep_table

    # 旧结果文件若没有 reference_disagreements 字段则跳过，不报错
    old_run_data = dict(run_data)
    del old_run_data["reference_disagreements"]
    old_file = data_dir / "evals" / "old_run.json"
    old_file.write_text(json.dumps(old_run_data, ensure_ascii=False), encoding="utf-8")

    old_rep_data, old_rep_table = report_eval(active_settings, run_id="old_run")
    assert "reference_disagreements" not in old_rep_data
    assert "参考标注与人工标注不一致" not in old_rep_table
    assert "参考标注与人工标注没有不一致" not in old_rep_table

    # 没有不一致时打印"参考标注与人工标注没有不一致"
    conn = open_db(data_dir)
    conn.execute("DELETE FROM reference_labels")
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, sales_level, overall, created_at
        ) VALUES ('me', 10, 'model:gemini-3.8-flash-high', 1, 0, '市场与销售', '中', 'skip', ?)
        """,
        (now,),
    )
    conn.close()

    agree_data, agree_table = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=httpx.MockTransport(mock_deepseek),
    )
    assert agree_data["reference_disagreements"] == []
    assert "参考标注与人工标注没有不一致" in agree_table


def test_eval_overall_void_reference_labels(eval_setup, data_dir: Path, settings: Settings):
    """作废的参考总体结论不参与'总体结论一致'与'误判为可投'，也不出现在不一致条目里；打印作废提示。"""
    conn = open_db(data_dir)
    conn.execute("DELETE FROM labels")
    conn.execute("DELETE FROM reference_labels")

    now = utc_now()
    # 岗位 1 (jv_id=10): 只有参考标注，且 overall_void = 1
    # 模型的预测如果是 apply，由于参考标注 overall 已作废，不应被算入 overall_agreement 也不应被算为 false_positive
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, sales_level, overall, overall_void, created_at
        ) VALUES ('me', 10, 'model:gemini-3.8-flash-high', 1, 0, '数据与技术', '低', 'skip', 1, ?)
        """,
        (now,),
    )

    # 岗位 2 (jv_id=20): 有人工标注 overall='apply'，参考标注 overall='skip' 但 overall_void=1
    # 两者总体结论不同，但由于参考 overall 已作废，不产生 reference_disagreements
    save_label(
        conn,
        user_id="me",
        platform_job_id="job_eval_2",
        data={
            "work_type": "市场与销售",
            "sales_level": "高",
            "overall": "apply",
        },
    )
    conn.execute(
        """
        INSERT INTO reference_labels (
            user_id, job_version_id, source, profile_version_no, used_hr_note,
            work_type, sales_level, overall, overall_void, created_at
        ) VALUES ('me', 20, 'model:gemini-3.8-flash-high', 1, 0, '市场与销售', '高', 'skip', 1, ?)
        """,
        (now,),
    )
    conn.close()

    def mock_deepseek(request: httpx.Request) -> httpx.Response:
        content = json.dumps({
            "facts": {
                "summary": {"text": "测试岗位", "quotes": []},
                "work_type": {"value": "数据与技术", "subtype": None, "secondary": [], "quotes": []},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "硬性", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": "apply",
            "derivation": ["匹配"],
            "verdict_reason": "匹配",
            "hr_questions": [],
        })
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    active_settings = Settings(data_dir=data_dir, llm_api_key="mock-key")
    run_data, table_str = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=httpx.MockTransport(mock_deepseek),
    )

    # 1. 岗位 1 的 label 中 overall 为 None（被置空）
    item1 = next(it for it in run_data["items"] if it["job_version_id"] == 10)
    assert item1["label"].get("overall") is None
    # 岗位 1 的 work_type 照常提供
    assert item1["label"].get("work_type") == "数据与技术"

    # 2. 岗位 2 的 label 中 overall 来自人工标注（'apply'）
    item2 = next(it for it in run_data["items"] if it["job_version_id"] == 20)
    assert item2["label"].get("overall") == "apply"

    # 3. 总体结论一致与误判为可投：
    # 只有岗位 2 参与总体结论统计（denom=1），模型输出 'apply' 与人工 'apply' 一致（acc=1.0）
    # 岗位 1 参考标注的 'skip' 已作废，不会导致 false_positive
    b_summary = run_data["summary"]["B"]
    assert b_summary["overall_agreement"] == 1.0
    assert b_summary["false_positive"] == 0

    # 4. 不出现在不一致条目中
    assert run_data["reference_disagreements"] == []
    assert "参考标注与人工标注没有不一致" in table_str

    # 5. 打印作废说明
    assert "参考标注的总体结论已作废（城市含义变化），总体结论只用人工标注" in table_str


def test_report_ignores_reference_label_files_in_evals_dir(data_dir):
    """evals/ 里同时放着参考标注的导出 / 导入文件时，report 仍读取最新的评测结果文件。"""
    import json as _json

    from jet.config import load_settings
    from jet.eval.runner import report_eval

    evals = data_dir / "evals"
    evals.mkdir(parents=True)
    run = {
        "run_id": "20260924T201849Z",
        "created_at": "2026-09-24T20:18:49Z",
        "prompt_versions": {"B": "v4"},
        "threshold": 0.6,
        "max_calls": 300,
        "profile_version": 1,
        "items": [],
        "summary": {},
    }
    (evals / "20260924T201849Z.json").write_text(_json.dumps(run), encoding="utf-8")
    (evals / "jobs_for_label.json").write_text(_json.dumps({"jobs": []}), encoding="utf-8")
    (evals / "reference_labels.json").write_text(_json.dumps({"labels": []}), encoding="utf-8")

    data, _ = report_eval(load_settings(data_dir))
    assert data["run_id"] == "20260924T201849Z"


def test_report_reads_old_run_file_with_variant_d(data_dir):
    """体检第 82 条删掉了组合 D；9/24 留下的评测文件里还有 D 的结果，report 照常按 facts 计算，不报错。"""
    from jet.config import load_settings

    evals = data_dir / "evals"
    evals.mkdir(parents=True)
    item = {
        "label": {"sales_level": "低"},
        "results": {
            "B": {"facts": {"sales_level": {"value": "低"}}},
            "D": {
                "facts": {"sales_level": {"value": "中"}},
                "jev_probabilities": {"sales_level": {"低": 0.55, "中": 0.45}},
            },
        },
    }
    run = {
        "run_id": "20260924T201849Z",
        "prompt_versions": {"B": "v4", "D": "v4"},
        "threshold": 0.6,
        "items": [item],
        "summary": {},
    }
    (evals / "20260924T201849Z.json").write_text(json.dumps(run), encoding="utf-8")

    data, table = report_eval(load_settings(data_dir))
    assert data["summary"]["B"]["sales_level_acc"] == 1.0
    assert data["summary"]["D"]["sales_level_acc"] == 0.0


def test_cli_eval_run_rejects_removed_variant_d(data_dir, capsys):
    """体检第 82 条：组合 D 已删除，jet eval run --variants 带 D 时直接提示可选组合，不调用任何模型。"""
    from jet.cli import main
    from jet.db.store import init_db

    init_db(data_dir)
    ret = main(["eval", "run", "--variants", "B,D", "--data-dir", str(data_dir)])
    assert ret == 1
    out = capsys.readouterr().out
    assert "没有这个评测组合：D" in out
    assert "A、B、C" in out
    assert not (data_dir / "evals").exists()
