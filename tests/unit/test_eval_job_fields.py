"""Tests for complete job fields passed to LLM during evaluation and 无法判断 matching."""

import json
from pathlib import Path
import httpx

from jet.config import Settings
from jet.db.store import open_db, utc_now
from jet.domain.labels import save_label
from jet.domain.profiles import save_profile
from jet.eval.runner import run_eval


def test_eval_job_fields_and_unclear_fact_match(data_dir: Path):
    """评测发给大模型的请求包含真实公司名和行业，且参考答案与模型同为'无法判断'时计为答对。"""
    conn = open_db(data_dir)
    now = utc_now()

    # 1. 准备画像
    save_profile(
        conn,
        "me",
        {
            "directions": ["AI算法"],
            "cities": ["深圳"],
            "work_preference": "从事大模型算法研发",
            "background": "计算机硕士，熟练掌握PyTorch与LLM微调",
        },
    )

    # 2. 插入岗位数据（包含特定 company_name 与 company_industry）
    test_company = "极星智能科技有限公司"
    test_industry = "人工智能/大数据"
    test_title = "AI算法工程师"
    test_desc = "负责LLM评测与优化研发工作，日常工作弹性双休，要求扎实算法功底。"

    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, company_industry, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'eval_job_fields_1', 'full', ?, ?, ?, ?)",
        (test_company, test_industry, now, now),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, salary_parse_ok, salary_max_k, city, description, content_hash, created_at) "
        "VALUES (10, 1, 1, 'detail', ?, '30-50K', 1, 1, 50.0, '深圳', ?, 'hash_test', ?)",
        (test_title, test_desc, now),
    )
    conn.execute("UPDATE jobs SET current_version_id = 10 WHERE id = 1")

    # 3. 插入参考标注：experience_fit 设为'无法判断'
    save_label(
        conn,
        user_id="me",
        platform_job_id="eval_job_fields_1",
        data={
            "work_type": "数据与技术",
            "work_subtype": "AI 相关",
            "sales_level": "低",
            "experience_fit": "无法判断",
            "work_intensity": "双休",
            "overall": "apply",
        },
    )
    conn.close()

    # 4. MockTransport 截获评测发给大模型的请求
    captured_requests: list[httpx.Request] = []

    def mock_deepseek_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        content = json.dumps({
            "facts": {
                "summary": {"text": "AI算法研发", "quotes": ["负责LLM评测与优化研发工作"]},
                "work_type": {
                    "value": "数据与技术",
                    "subtype": "AI 相关",
                    "secondary": [],
                    "quotes": [],
                },
                "sales_level": {"value": "低", "signals": []},
                "experience": {
                    "requirement": None,
                    "requirement_type": "未提及",
                    "value": "无法判断",
                    "gap": "",
                },
                "work_intensity": {"value": "双休", "quotes": ["日常工作弹性双休"]},
            },
            "verdict": "apply",
            "derivation": ["各项要求与画像一致"],
            "verdict_reason": "各项要求与画像一致",
        }, ensure_ascii=False)

        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    transport = httpx.MockTransport(mock_deepseek_handler)
    active_settings = Settings(
        data_dir=data_dir,
        llm_api_key="mock-key",
    )

    # 5. 执行评测 Variant B
    run_data, _ = run_eval(
        active_settings,
        variants=["B"],
        llm_transport=transport,
        backoff_delays=(0, 0),
    )

    # 6. 断言用户消息中出现了测试数据中的公司名和公司行业
    assert len(captured_requests) > 0, "评测必须向大模型发送请求"
    user_contents = []
    for req in captured_requests:
        body = json.loads(req.content.decode("utf-8"))
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                user_contents.append(msg.get("content", ""))

    combined_user_text = "\n".join(user_contents)
    assert test_company in combined_user_text, f"大模型用户消息中必须包含公司名 '{test_company}'"
    assert test_industry in combined_user_text, f"大模型用户消息中必须包含公司行业 '{test_industry}'"

    # 7. 断言参考答案和模型都是'无法判断'时该事实项计为答对（准确率为 1.0）
    summary_b = run_data["summary"]["B"]
    assert summary_b["experience_fit_acc"] == 1.0, (
        f"参考答案与模型同为'无法判断'时应计为答对，期望 1.0，实际得到 {summary_b['experience_fit_acc']}"
    )
