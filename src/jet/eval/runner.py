from collections.abc import Sequence
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
import httpx

from jet.config import Settings
from jet.db.store import connect, utc_now
from jet.domain import taxonomy
from jet.domain.hr_notes import hr_note_for
from jet.domain.profiles import get_current_profile
from jet.domain.quotes import count_quotes
from jet.domain.taxonomy import (
    CATEGORIES,
    VERDICT_LABELS,
    LEGACY_VERDICT_MAP,
    VERDICTS,
    WORK_INTENSITY,
)
from jet.llm.client import run_llm_judgement
from jet.llm.versions import EVAL_BASELINE_PROMPT_VERSION
from jet.worker import _job_version_with_company

FIELD_LABELS: dict[str, str] = {
    "work_type": "工作类型（大类）",
    "work_subtype": "工作类型（细分）",
    "sales_level": "销售成分",
    "experience_fit": "经验是否满足",
    "work_intensity": "工作强度",
    "overall": "总体结论",
}


def calculate_metrics(
    items: list[dict[str, Any]],
    variants: Sequence[str],
) -> dict[str, Any]:
    """Calculate evaluation metrics for each variant over items."""
    summary: dict[str, Any] = {}

    for var in variants:
        fact_keys = ["work_type", "sales_level", "experience_fit", "work_intensity"]
        fact_acc: dict[str, float | None] = {}

        if var == "A":
            for fk in fact_keys:
                fact_acc[fk] = None
        else:
            for fk in fact_keys:
                denom = 0
                match = 0
                for it in items:
                    label_val = it["label"].get(fk)
                    if label_val is None and fk == "work_intensity":
                        label_val = it["label"].get("overtime")
                        if label_val == "明确双休或不加班":
                            label_val = "双休"
                    if label_val is not None:
                        denom += 1
                        res = it["results"].get(var, {})

                        facts = res.get("facts") or {}
                        if fk == "work_type":
                            jet_val = facts.get("work_type", {}).get("value")
                        elif fk == "sales_level":
                            jet_val = facts.get("sales_level", {}).get("value")
                        elif fk == "experience_fit":
                            jet_val = facts.get("experience", {}).get("value")
                        elif fk == "work_intensity":
                            jet_val = facts.get("work_intensity", {}).get("value")
                            if jet_val is None and "overtime" in facts:
                                ot = facts.get("overtime", {}).get("value")
                                jet_val = "双休" if ot == "明确双休或不加班" else ot
                        else:
                            jet_val = None

                        if jet_val is not None and jet_val != "存疑" and jet_val == label_val:
                            match += 1

                fact_acc[fk] = round(match / denom, 4) if denom > 0 else None

        # Overall agreement & false positive
        overall_denom = 0
        overall_match = 0
        false_positive = 0
        for it in items:
            label_overall = it["label"].get("overall")
            res = it["results"].get(var, {})
            verdict = res.get("verdict")
            if verdict in LEGACY_VERDICT_MAP:
                verdict = LEGACY_VERDICT_MAP[verdict]
            if label_overall in LEGACY_VERDICT_MAP:
                label_overall = LEGACY_VERDICT_MAP[label_overall]

            if label_overall is not None:
                overall_denom += 1
                if verdict == label_overall:
                    overall_match += 1
                if label_overall == "skip" and verdict in ("apply", "try"):
                    false_positive += 1

        overall_acc = round(overall_match / overall_denom, 4) if overall_denom > 0 else None

        # Missing quote rate
        if var == "A":
            quote_rate = None
        else:
            total_q = 0
            missing_q = 0
            for it in items:
                res = it["results"].get(var, {})
                facts = res.get("facts") or {}
                t, m = count_quotes(facts)
                total_q += t
                missing_q += m
            quote_rate = round(missing_q / total_q, 4) if total_q > 0 else 0.0

        # Total calls and cost
        total_calls = 0
        total_cost = 0.0
        for it in items:
            res = it["results"].get(var, {})
            total_calls += res.get("calls", 0)
            total_cost += res.get("cost_cny") or 0.0

        summary[var] = {
            "work_type_acc": fact_acc["work_type"],
            "sales_level_acc": fact_acc["sales_level"],
            "experience_fit_acc": fact_acc["experience_fit"],
            "work_intensity_acc": fact_acc["work_intensity"],
            "overtime_acc": fact_acc["work_intensity"],
            "overall_agreement": overall_acc,
            "false_positive": false_positive,
            "false_fit": false_positive,
            "missing_quote_rate": quote_rate,
            "calls": total_calls,
            "cost_cny": round(total_cost, 4),
        }

    return summary


EXPORTABLE_PROFILE_FIELDS = (
    "background",
    "work_preference",
    "preferred_cities",
    "excluded_cities",
    "min_monthly_k",
    "nonpref_min_monthly_k",
    "exclude_keywords",
)


def format_summary_table(
    summary: dict[str, Any],
    source_composition: dict[str, int] | None = None,
    reference_disagreements: list[dict[str, Any]] | None = None,
    void_overall_used: bool = False,
) -> str:
    """Format evaluation summary into an aligned terminal table."""
    headers = [
        "组合",
        "工作类型",
        "销售成分",
        "经验门槛",
        "工作强度",
        "总体一致",
        "误判为可投",
        "引用缺失",
        "调用数",
        "费用(元)",
    ]

    def _fmt_pct(val: float | None) -> str:
        if val is None:
            return "-"
        return f"{val * 100:.1f}%"

    rows = []
    for var, stats in summary.items():
        wi_acc = stats.get("work_intensity_acc")
        if wi_acc is None:
            wi_acc = stats.get("overtime_acc")
        fp_val = stats.get("false_positive")
        if fp_val is None:
            fp_val = stats.get("false_fit", 0)
        row = [
            var,
            _fmt_pct(stats.get("work_type_acc")),
            _fmt_pct(stats.get("sales_level_acc")),
            _fmt_pct(stats.get("experience_fit_acc")),
            _fmt_pct(wi_acc),
            _fmt_pct(stats.get("overall_agreement")),
            str(fp_val),
            _fmt_pct(stats.get("missing_quote_rate")),
            str(stats.get("calls", 0)),
            f"{stats.get('cost_cny', 0.0):.4f}",
        ]
        rows.append(row)

    col_widths = [len(h) for h in headers]
    for r in rows:
        for i, val in enumerate(r):
            col_widths[i] = max(col_widths[i], len(val))

    def _join_row(vals: list[str]) -> str:
        return " | ".join(val.ljust(col_widths[i]) for i, val in enumerate(vals))

    header_line = _join_row(headers)
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    data_lines = [_join_row(r) for r in rows]

    table_text = "\n".join([header_line, sep_line] + data_lines)
    if void_overall_used:
        table_text += "\n\n参考标注的总体结论已作废（城市含义变化），总体结论只用人工标注"

    if source_composition is not None:
        user_cnt = source_composition.get("user", 0)
        model_cnt = source_composition.get("model", 0)
        table_text += f"\n参考答案来源：用户标注 {user_cnt} 项，模型参考标注 {model_cnt} 项（模型参考标注，非人工）"

    if reference_disagreements is not None:
        if not reference_disagreements:
            table_text += "\n\n参考标注与人工标注没有不一致"
        else:
            table_text += f"\n\n参考标注与人工标注不一致（以人工标注为准，共 {len(reference_disagreements)} 处）："
            for d in reference_disagreements:
                title = d.get("title", "")
                field = d.get("field", "")
                field_cn = FIELD_LABELS.get(field, field)
                user_val = d.get("user_value")
                ref_val = d.get("reference_value")
                if field == "overall":
                    user_disp = VERDICT_LABELS.get(
                        LEGACY_VERDICT_MAP.get(user_val, user_val),
                        str(user_val) if user_val is not None else "无",
                    )
                    ref_disp = VERDICT_LABELS.get(
                        LEGACY_VERDICT_MAP.get(ref_val, ref_val),
                        str(ref_val) if ref_val is not None else "无",
                    )
                else:
                    user_disp = str(user_val) if user_val is not None else "无"
                    ref_disp = str(ref_val) if ref_val is not None else "无"
                src = d.get("reference_source") or ""
                table_text += f"\n- {title} · {field_cn}：人工={user_disp}，参考={ref_disp}（{src}）"

    return table_text


def run_eval(
    settings: Settings,
    *,
    variants: Sequence[str] = ("A", "B", "C"),
    max_calls: int | None = None,
    llm_transport: httpx.BaseTransport | None = None,
    backoff_delays: Sequence[float] = (0.0, 0.0),
) -> tuple[dict[str, Any], str]:
    """
    Run evaluation across variants on the current user's labelled jobs.

    Returns (run_data_dict, formatted_table_string).
    """
    conn = connect(settings.data_dir)
    try:
        user_id = "me"
        profile = get_current_profile(conn, user_id)
        if profile is None:
            raise RuntimeError("请先设置画像")

        candidate_rows = conn.execute(
            """
            SELECT DISTINCT jv.*, j.platform_job_id
            FROM job_versions jv
            JOIN jobs j ON jv.job_id = j.id
            WHERE jv.id IN (
                SELECT job_version_id FROM labels WHERE user_id = ?
                UNION
                SELECT job_version_id FROM reference_labels WHERE user_id = ?
            )
            ORDER BY jv.id ASC
            """,
            (user_id, user_id),
        ).fetchall()

        valid_items = [r for r in candidate_rows if r["description"] and str(r["description"]).strip()]
        if not valid_items:
            raise RuntimeError("请先导入参考标注（jet eval export-jobs → 参考标注 → jet eval import-reference）")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        eval_limit = max_calls if max_calls is not None else settings.eval_max_calls

        items_data = []
        limit_reached = False
        reference_disagreements: list[dict[str, Any]] = []
        void_overall_used = False

        for row in valid_items:
            jv_row = _job_version_with_company(conn, row)
            jv_id = jv_row["id"]

            user_lbl = conn.execute(
                "SELECT * FROM labels WHERE user_id = ? AND job_version_id = ?",
                (user_id, jv_id),
            ).fetchone()

            ref_lbl = conn.execute(
                "SELECT * FROM reference_labels WHERE user_id = ? AND job_version_id = ? ORDER BY id DESC LIMIT 1",
                (user_id, jv_id),
            ).fetchone()

            ref_overall_void = False
            if ref_lbl:
                try:
                    ref_overall_void = bool(ref_lbl["overall_void"])
                except (IndexError, KeyError):
                    ref_overall_void = False
                if ref_overall_void:
                    void_overall_used = True

            if user_lbl and ref_lbl:
                u_dict = dict(user_lbl)
                r_dict = dict(ref_lbl)
                cmp_fields = [
                    "work_type",
                    "work_subtype",
                    "sales_level",
                    "experience_fit",
                    "work_intensity",
                    "overall",
                ]
                for f in cmp_fields:
                    if f == "overall" and ref_overall_void:
                        continue
                    u_v = u_dict.get(f)
                    r_v = r_dict.get(f)
                    if f == "work_intensity":
                        if u_v is None and "overtime" in u_dict:
                            ot = u_dict.get("overtime")
                            u_v = "双休" if ot == "明确双休或不加班" else ot
                        if r_v is None and "overtime" in r_dict:
                            ot = r_dict.get("overtime")
                            r_v = "双休" if ot == "明确双休或不加班" else ot
                    elif f == "overall":
                        if u_v in LEGACY_VERDICT_MAP:
                            u_v = LEGACY_VERDICT_MAP[u_v]
                        if r_v in LEGACY_VERDICT_MAP:
                            r_v = LEGACY_VERDICT_MAP[r_v]

                    if u_v is not None and (not isinstance(u_v, str) or u_v.strip() != ""):
                        if u_v != r_v:
                            reference_disagreements.append({
                                "job_version_id": jv_id,
                                "platform_job_id": row["platform_job_id"],
                                "title": row["title"],
                                "field": f,
                                "user_value": u_v,
                                "reference_value": r_v,
                                "reference_source": r_dict.get("source"),
                                "reference_rationale": r_dict.get("rationale"),
                            })

            label_data: dict[str, Any] = {}
            item_sources: dict[str, str] = {}

            fields = [
                "work_type",
                "work_subtype",
                "secondary_work_types",
                "sales_level",
                "experience_fit",
                "work_intensity",
                "overall",
            ]
            for f in fields:
                val = None
                src = None
                if user_lbl and user_lbl[f] is not None:
                    val = user_lbl[f]
                    src = "user"
                elif ref_lbl and ref_lbl[f] is not None:
                    if f == "overall" and ref_overall_void:
                        val = None
                        src = None
                    else:
                        val = ref_lbl[f]
                        src = ref_lbl["source"]

                if f == "work_intensity" and val is None:
                    if user_lbl:
                        try:
                            ot = user_lbl["overtime"]
                            if ot is not None:
                                val = "双休" if ot == "明确双休或不加班" else ot
                                src = "user"
                        except (IndexError, KeyError):
                            pass
                    if val is None and ref_lbl:
                        try:
                            ot = ref_lbl["overtime"]
                            if ot is not None:
                                val = "双休" if ot == "明确双休或不加班" else ot
                                src = ref_lbl["source"]
                        except (IndexError, KeyError):
                            pass

                if f == "secondary_work_types":
                    if isinstance(val, str) and val:
                        try:
                            val = json.loads(val)
                        except Exception:
                            val = None
                    elif not isinstance(val, list):
                        val = None

                label_data[f] = val
                if val is not None and (f != "secondary_work_types" or val != []):
                    item_sources[f] = src

            note_val = None
            if user_lbl:
                try:
                    note_val = user_lbl["note"]
                except (IndexError, KeyError):
                    pass
            if not note_val and ref_lbl:
                try:
                    note_val = ref_lbl["rationale"]
                except (IndexError, KeyError):
                    pass
            label_data["note"] = note_val

            results: dict[str, Any] = {}

            for var in variants:
                if limit_reached:
                    results[var] = {
                        "status": "quota_exhausted",
                        "error": "已达评测上限，以下未完成",
                        "calls": 0,
                    }
                    continue

                if var == "A":
                    outcome = run_llm_judgement(
                        conn,
                        settings,
                        user_id=user_id,
                        judgement_id=None,
                        profile=profile,
                        job_version=jv_row,
                        transport=llm_transport,
                        backoff_delays=backoff_delays,
                        prompt_version=EVAL_BASELINE_PROMPT_VERSION,
                        engine="deepseek-flash:no-think",
                        purpose="eval",
                        eval_run_id=run_id,
                        eval_limit=eval_limit,
                    )
                    if outcome.status == "quota_exhausted":
                        limit_reached = True
                        results["A"] = {
                            "status": "quota_exhausted",
                            "error": "已达评测上限，以下未完成",
                            "calls": 0,
                        }
                    else:
                        a_verdict = outcome.verdict
                        if a_verdict in LEGACY_VERDICT_MAP:
                            a_verdict = LEGACY_VERDICT_MAP[a_verdict]
                        results["A"] = {
                            "status": outcome.status,
                            "verdict": a_verdict,
                            "reasons": outcome.reasons,
                            "work_subtype": None,
                            "secondary_work_types": None,
                            "error": outcome.error,
                            "calls": 1 if outcome.status in ("done", "failed") else 0,
                            "cost_cny": outcome.cost_cny,
                            "usage": outcome.usage,
                        }

                elif var == "B":
                    outcome = run_llm_judgement(
                        conn,
                        settings,
                        user_id=user_id,
                        judgement_id=None,
                        profile=profile,
                        job_version=jv_row,
                        transport=llm_transport,
                        backoff_delays=backoff_delays,
                        prompt_version=settings.prompt_version,
                        engine="deepseek-flash:no-think",
                        purpose="eval",
                        eval_run_id=run_id,
                        eval_limit=eval_limit,
                    )
                    if outcome.status == "quota_exhausted":
                        limit_reached = True
                        results["B"] = {
                            "status": "quota_exhausted",
                            "error": "已达评测上限，以下未完成",
                            "calls": 0,
                        }
                    else:
                        sec_b = outcome.facts.get("work_type", {}).get("secondary") if outcome.facts else None
                        sub_b = outcome.facts.get("work_type", {}).get("subtype") if outcome.facts else None
                        results["B"] = {
                            "status": outcome.status,
                            "verdict": outcome.verdict,
                            "facts": outcome.facts,
                            "work_subtype": sub_b,
                            "secondary_work_types": sec_b,
                            "derivation": outcome.derivation,
                            "error": outcome.error,
                            "calls": 1 if outcome.status in ("done", "failed") else 0,
                            "cost_cny": outcome.cost_cny,
                            "usage": outcome.usage,
                        }

                elif var == "C":
                    outcome = run_llm_judgement(
                        conn,
                        settings,
                        user_id=user_id,
                        judgement_id=None,
                        profile=profile,
                        job_version=jv_row,
                        transport=llm_transport,
                        backoff_delays=backoff_delays,
                        prompt_version=settings.prompt_version,
                        engine="deepseek-flash:think",
                        purpose="eval",
                        eval_run_id=run_id,
                        eval_limit=eval_limit,
                    )
                    if outcome.status == "quota_exhausted":
                        limit_reached = True
                        results["C"] = {
                            "status": "quota_exhausted",
                            "error": "已达评测上限，以下未完成",
                            "calls": 0,
                        }
                    else:
                        sec_c = outcome.facts.get("work_type", {}).get("secondary") if outcome.facts else None
                        sub_c = outcome.facts.get("work_type", {}).get("subtype") if outcome.facts else None
                        results["C"] = {
                            "status": outcome.status,
                            "verdict": outcome.verdict,
                            "facts": outcome.facts,
                            "work_subtype": sub_c,
                            "secondary_work_types": sec_c,
                            "derivation": outcome.derivation,
                            "error": outcome.error,
                            "calls": 1 if outcome.status in ("done", "failed") else 0,
                            "cost_cny": outcome.cost_cny,
                            "usage": outcome.usage,
                        }

            items_data.append({
                "job_version_id": jv_id,
                "platform_job_id": row["platform_job_id"],
                "title": row["title"],
                "label": label_data,
                "sources": item_sources,
                "label_sources": item_sources,
                "results": results,
            })

        summary = calculate_metrics(items_data, list(variants))

        user_items_count = 0
        model_items_count = 0
        for it in items_data:
            lbl = it.get("label", {})
            for f, s in it.get("sources", {}).items():
                if lbl.get(f) is not None and lbl.get(f) != []:
                    if s == "user":
                        user_items_count += 1
                    elif s:
                        model_items_count += 1

        source_composition = {
            "user": user_items_count,
            "model": model_items_count,
        }

        run_data = {
            "run_id": run_id,
            "created_at": utc_now(),
            "prompt_versions": {v: (EVAL_BASELINE_PROMPT_VERSION if v == "A" else settings.prompt_version) for v in variants},
            "max_calls": eval_limit,
            "profile_version": profile["version_no"],
            "source_composition": source_composition,
            "reference_disagreements": reference_disagreements,
            "void_overall_used": void_overall_used,
            "items": items_data,
            "summary": summary,
        }

        evals_dir = settings.data_dir / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)
        out_file = evals_dir / f"{run_id}.json"
        out_file.write_text(json.dumps(run_data, ensure_ascii=False, indent=2), encoding="utf-8")

        table_str = format_summary_table(
            summary,
            source_composition=source_composition,
            reference_disagreements=reference_disagreements,
            void_overall_used=void_overall_used,
        )
        return run_data, table_str
    finally:
        conn.close()


def report_eval(
    settings: Settings,
    run_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Load an evaluation run file and recalculate its metrics.

    Returns (run_data_dict, formatted_table_string).
    """
    evals_dir = settings.data_dir / "evals"
    if not evals_dir.is_dir():
        raise FileNotFoundError("未找到评测结果文件")

    if run_id:
        target_file = evals_dir / f"{run_id}.json"
        if not target_file.is_file():
            raise FileNotFoundError(f"未找到运行 ID 为 {run_id} 的评测文件")
    else:
        # 只认评测结果文件（运行 ID 形如 20260924T201849Z.json）；evals/ 里还放着参考标注的导出 / 导入文件
        files = sorted(f for f in evals_dir.glob("*.json") if re.fullmatch(r"\d{8}T\d{6}Z\.json", f.name))
        if not files:
            raise FileNotFoundError("未找到任何评测结果文件")
        target_file = files[-1]

    data = json.loads(target_file.read_text(encoding="utf-8"))
    items = data.get("items", [])
    variants = list(data.get("prompt_versions", {}).keys())

    summary = calculate_metrics(items, variants)
    data["summary"] = summary

    source_composition = data.get("source_composition")
    if source_composition is None:
        user_cnt = 0
        model_cnt = 0
        for it in items:
            lbl = it.get("label", {})
            for f, s in it.get("sources", {}).items():
                if lbl.get(f) is not None and lbl.get(f) != []:
                    if s == "user":
                        user_cnt += 1
                    elif s:
                        model_cnt += 1
        if user_cnt > 0 or model_cnt > 0:
            source_composition = {"user": user_cnt, "model": model_cnt}

    reference_disagreements = data.get("reference_disagreements")
    void_overall_used = data.get("void_overall_used", False)
    table_str = format_summary_table(
        summary,
        source_composition=source_composition,
        reference_disagreements=reference_disagreements,
        void_overall_used=void_overall_used,
    )
    return data, table_str


INSTRUCTIONS = """
你是一个职位评估与标注模型。请根据提供的 profile（求职者画像）和 taxonomy（分类体系），对 jobs 列表中的每个岗位进行客观参考标注。

标注要求：
1. 逐个岗位按 taxonomy 给出以下字段：
   - work_type: 主要工作类型（必须是 taxonomy.categories 的 8 个大类之一）
   - work_subtype: 主要工作类型细分（必须属于对应 work_type 的细分列表，无细分则为 null）
   - secondary_work_types: 次要工作类型（数组，0-3 项，每项 {"category": 大类, "subtype": 细分|null}，不与主要类型重复）
   - sales_level: 销售成分（"高" / "中" / "低"）
   - experience_fit: 经验门槛匹配度（对照 profile.background，"满足" / "差一点" / "不满足" / "无法判断"）
   - work_intensity: 工作强度（"高强度" / "单休" / "大小周" / "双休" / "未提及"）
   - overall: 总体结论四档（对照 profile 中提供的背景、工作内容偏好、偏好城市 / 不去的城市 / 非偏好城市最低月薪与底线，"apply" 适合投递 / "try" 可以一试 / "check" 需要确认 / "skip" 不建议投）
   - rationale: 标注依据简述（≤ 200 字）
2. 特别注意：若岗位存在 hr_note（HR 说的实际情况），以 hr_note 为准，其效力优先于职位描述正文。
3. 输出格式必须为纯 JSON，不要包含任何 markdown 代码块或解释文字。

输出文件格式示例：
{
  "source": "model:gemini-3.8-flash-high",
  "labels": [
    {
      "job_version_id": 1,
      "work_type": "数据与技术",
      "work_subtype": "开发与测试",
      "secondary_work_types": [],
      "sales_level": "低",
      "experience_fit": "满足",
      "work_intensity": "双休",
      "overall": "apply",
      "rationale": "工作职责匹配Python开发，双休且无销售要求"
    }
  ]
}
""".strip()


def export_jobs(
    settings: Settings,
    out_path: Path,
    limit: int = 100,
    user_id: str = "me",
) -> dict[str, Any]:
    """Export full jobs with profile, taxonomy, instructions and hr_notes for reference labeling."""
    conn = connect(settings.data_dir)
    try:
        profile = get_current_profile(conn, user_id)
        if profile is None:
            raise RuntimeError("请先设置画像")

        rows = conn.execute(
            """
            SELECT jv.id as job_version_id, j.id as job_id, j.platform_job_id,
                   jv.title, jv.salary_raw, jv.city, jv.description,
                   COALESCE(MAX(v.seen_at), j.last_seen_at) as last_viewed
            FROM jobs j
            JOIN job_versions jv ON j.current_version_id = jv.id
            LEFT JOIN views v ON v.job_id = j.id AND v.user_id = ?
            WHERE j.completeness = 'full'
            GROUP BY j.id, jv.id
            ORDER BY last_viewed DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

        jobs_list = []
        for r in rows:
            note = hr_note_for(conn, user_id, r["job_id"])
            jobs_list.append({
                "job_version_id": r["job_version_id"],
                "platform_job_id": r["platform_job_id"],
                "title": r["title"],
                "salary_raw": r["salary_raw"],
                "city": r["city"],
                "description": r["description"],
                "hr_note": note,
            })

        # 只导出用户明确同意发给外部模型的画像字段（2026-09-25，spec FR-042）：
        # 我的背景、工作内容偏好、偏好城市 / 不去的城市 / 非偏好城市最低月薪、最低月薪、不接受关键词。方向、关键词等其他字段不导出。
        profile_data = {field: profile[field] for field in EXPORTABLE_PROFILE_FIELDS}
        profile_data["version_no"] = profile["version_no"]

        export_data = {
            "exported_at": utc_now(),
            "profile": profile_data,
            "taxonomy": {
                "categories": CATEGORIES,
                "work_intensity": WORK_INTENSITY,
                "verdicts": VERDICT_LABELS,
                "sales_level": ["高", "中", "低"],
                "experience_fit": ["满足", "差一点", "不满足", "无法判断"],
                "大类到细分": CATEGORIES,
                "工作强度": WORK_INTENSITY,
                "四档结论": VERDICT_LABELS,
                "销售成分": ["高", "中", "低"],
                "经验选项": ["满足", "差一点", "不满足", "无法判断"],
            },
            "instructions": INSTRUCTIONS,
            "jobs": jobs_list,
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return export_data
    finally:
        conn.close()


def import_reference(
    settings: Settings,
    file_path: Path,
    source_override: str | None = None,
    user_id: str = "me",
) -> tuple[int, int]:
    """
    Import reference labels from a JSON file into reference_labels table.

    Validates each entry with the same rules as user labels.
    Returns (imported_count, skipped_count).
    """
    conn = connect(settings.data_dir)
    try:
        profile = get_current_profile(conn, user_id)
        if profile is None:
            raise RuntimeError("请先设置画像")
        profile_version_no = profile["version_no"]

        try:
            file_data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"无法读取或解析 JSON 文件: {e}")

        source = (source_override or file_data.get("source") or "").strip()
        if not source:
            raise ValueError("未指定 source，且输入文件中无 source 字段")

        raw_labels = file_data.get("labels")
        if not isinstance(raw_labels, list):
            raise ValueError("输入文件中的 labels 必须是列表")

        imported_count = 0
        skipped_count = 0

        for idx, item in enumerate(raw_labels, start=1):
            if not isinstance(item, dict):
                print(f"第 {idx} 条：格式错误（不是字典）")
                skipped_count += 1
                continue

            jv_id = item.get("job_version_id")
            if jv_id is None:
                print(f"第 {idx} 条：缺少 job_version_id")
                skipped_count += 1
                continue

            jv_row = conn.execute(
                "SELECT jv.id, jv.job_id FROM job_versions jv WHERE jv.id = ?",
                (jv_id,),
            ).fetchone()
            if jv_row is None:
                print(f"第 {idx} 条：岗位版本不存在 (job_version_id={jv_id})")
                skipped_count += 1
                continue

            hr_note = hr_note_for(conn, user_id, jv_row["job_id"])
            used_hr_note = 1 if (hr_note and hr_note.strip()) else 0

            work_type = item.get("work_type")
            work_subtype = item.get("work_subtype")
            raw_secondary = item.get("secondary_work_types")
            sales_level = item.get("sales_level")
            experience_fit = item.get("experience_fit")
            work_intensity = item.get("work_intensity")
            overall = item.get("overall")
            rationale = item.get("rationale")

            if all(x is None for x in [work_type, work_subtype, raw_secondary, sales_level, experience_fit, work_intensity, overall]):
                print(f"第 {idx} 条：至少需要填写一项事实或总体结论")
                skipped_count += 1
                continue

            if work_type is not None and work_type not in taxonomy.CATEGORIES:
                print(f"第 {idx} 条：work_type '{work_type}' 不在合法大类内")
                skipped_count += 1
                continue

            if work_subtype is not None:
                if not work_type:
                    print(f"第 {idx} 条：work_subtype 不为空时 work_type 不能为空")
                    skipped_count += 1
                    continue
                if not taxonomy.is_valid_pair(work_type, work_subtype):
                    print(f"第 {idx} 条：work_subtype '{work_subtype}' 不属于工作类型 '{work_type}'")
                    skipped_count += 1
                    continue

            secondary_json = None
            if raw_secondary is not None:
                try:
                    norm_sec = taxonomy.normalize_secondary(
                        raw_secondary, primary_category=work_type, primary_subtype=work_subtype
                    )
                    secondary_json = json.dumps(norm_sec, ensure_ascii=False) if norm_sec else None
                except Exception as e:
                    print(f"第 {idx} 条：secondary_work_types 非法: {e}")
                    skipped_count += 1
                    continue

            if sales_level is not None and sales_level not in ("高", "中", "低"):
                print(f"第 {idx} 条：sales_level '{sales_level}' 不在 ('高', '中', '低') 内")
                skipped_count += 1
                continue

            if experience_fit is not None and experience_fit not in ("满足", "差一点", "不满足", "无法判断"):
                print(f"第 {idx} 条：experience_fit '{experience_fit}' 不在合法选项内")
                skipped_count += 1
                continue

            if work_intensity is not None and work_intensity not in taxonomy.WORK_INTENSITY:
                print(f"第 {idx} 条：work_intensity '{work_intensity}' 不在合法选项内")
                skipped_count += 1
                continue

            if overall is not None:
                if overall in taxonomy.LEGACY_VERDICT_MAP:
                    overall = taxonomy.LEGACY_VERDICT_MAP[overall]
                if overall not in taxonomy.VERDICTS:
                    print(f"第 {idx} 条：overall '{overall}' 不在合法选项内")
                    skipped_count += 1
                    continue

            if rationale is not None and len(str(rationale).strip()) > 200:
                print(f"第 {idx} 条：rationale 超过 200 字")
                skipped_count += 1
                continue

            now = utc_now()
            with conn:
                conn.execute(
                    """
                    INSERT INTO reference_labels (
                        user_id, job_version_id, source, profile_version_no, used_hr_note,
                        work_type, work_subtype, secondary_work_types, sales_level,
                        experience_fit, work_intensity, overall, overall_void, rationale, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(user_id, job_version_id, source) DO UPDATE SET
                        profile_version_no = excluded.profile_version_no,
                        used_hr_note = excluded.used_hr_note,
                        work_type = excluded.work_type,
                        work_subtype = excluded.work_subtype,
                        secondary_work_types = excluded.secondary_work_types,
                        sales_level = excluded.sales_level,
                        experience_fit = excluded.experience_fit,
                        work_intensity = excluded.work_intensity,
                        overall = excluded.overall,
                        overall_void = 0,
                        rationale = excluded.rationale,
                        created_at = excluded.created_at
                    """,
                    (
                        user_id,
                        jv_id,
                        source,
                        profile_version_no,
                        used_hr_note,
                        work_type,
                        work_subtype,
                        secondary_json,
                        sales_level,
                        experience_fit,
                        work_intensity,
                        overall,
                        str(rationale).strip() if rationale else None,
                        now,
                    ),
                )
            imported_count += 1

        print(f"导入 {imported_count} 条，跳过 {skipped_count} 条")
        return imported_count, skipped_count
    finally:
        conn.close()
