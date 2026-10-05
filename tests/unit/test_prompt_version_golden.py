"""黄金样本测试：固定 v1–v9 每个提示词版本的「发出内容」与「解析结果」样本 (014-prompt-version-table SC-001)。

在任何版本表重构之前生成并固化。当环境变量 JET_UPDATE_PROMPT_GOLDEN=1 时重写样本文件，默认逐字比对。
"""
import dataclasses
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import httpx
import pytest

from jet.config import Settings
from jet.db.store import connect, open_db
from jet.domain.industry import save_user_strict_industries
from jet.domain.resume import save_resumes
from jet.llm.client import run_llm_judgement
from jet.worker import JudgementWorker

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "prompt_golden"
UPDATE_PROMPT_GOLDEN = os.environ.get("JET_UPDATE_PROMPT_GOLDEN") == "1"

FIXED_USER_ID = "me"

# 1. 固定的假画像数据（全部虚构）
FIXED_PROFILE: dict[str, Any] = {
    "id": 1,
    "user_id": FIXED_USER_ID,
    "version_no": 1,
    "directions": ["后端开发", "Python工程师"],
    "keywords": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "cities": ["深圳", "广州"],
    "preferred_cities": ["深圳", "广州"],
    "excluded_cities": ["北京", "上海"],
    "min_monthly_k": 20.0,
    "nonpref_min_monthly_k": 25.0,
    "exclude_keywords": ["销售", "外包", "电话客服"],
    "work_preference": "偏好自研核心业务系统研发，技术驱动，不接受纯维护或大量售前支持",
    "background": "计算机专业统招本科，5年Python后端研发经验，熟悉高并发与微服务架构",
}

# 2. 固定的假岗位数据（与 _job_version_with_company 产出字段一致）
FIXED_JOB_VERSION: dict[str, Any] = {
    "id": 10,
    "job_id": 1,
    "version_no": 1,
    "source": "detail",
    "title": "Python资深开发工程师",
    "salary_raw": "25-35K",
    "salary_visible": 1,
    "salary_min_k": 25.0,
    "salary_max_k": 35.0,
    "salary_months": 12,
    "salary_parse_ok": 1,
    "city": "深圳",
    "district": "南山区",
    "description": "负责核心业务系统的架构设计与后端微服务开发；解决系统性能瓶颈与高并发技术难题；与团队协作完成高质量代码交付。要求本科及以上学历，熟练掌握 Python 及主流框架。",
    "content_hash": "hash_test_job_1",
    "company_name": "某科技发展有限公司",
    "company_industry": "互联网金融",
    "experience_req": "3-5年",
    "degree_req": "本科",
}

# 3. 固定的非空已知事实
FIXED_KNOWN_FACTS: dict[str, Any] = {
    "work_type": "数据与技术",
    "sales_level": "低",
}

# 4. 当前用户勾选的 2 个真实存在的从严行业
FIXED_STRICT_INDUSTRIES: list[str] = ["金融", "房地产"]

# 5. 用户保存的 2 份简历画像（slot 1, 2）
FIXED_RESUMES: list[dict[str, Any]] = [
    {
        "slot": 1,
        "name": "后端开发简历",
        "profile": "5年后端开发经验，精通 Python、FastAPI、分布式系统架构与高并发性能优化",
    },
    {
        "slot": 2,
        "name": "全栈开发简历",
        "profile": "全栈开发方向，熟练掌握 Python 后端与 Vue/React 前端技术栈，具备独立全流程交付能力",
    },
]

# 固定的 Token 用量，确保费用计算绝对确定
FIXED_USAGE: dict[str, Any] = {
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "prompt_cache_hit_tokens": 100,
}


def _setup_database(conn: sqlite3.Connection) -> None:
    now_str = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT OR REPLACE INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, work_preference, background, excluded_cities, nonpref_min_monthly_k, current_city, created_at) "
        "VALUES (1, ?, 1, ?, ?, ?, 20.0, ?, ?, ?, ?, 25.0, '深圳', ?)",
        (
            FIXED_USER_ID,
            json.dumps(FIXED_PROFILE["directions"], ensure_ascii=False),
            json.dumps(FIXED_PROFILE["keywords"], ensure_ascii=False),
            json.dumps(FIXED_PROFILE["cities"], ensure_ascii=False),
            json.dumps(FIXED_PROFILE["exclude_keywords"], ensure_ascii=False),
            FIXED_PROFILE["work_preference"],
            FIXED_PROFILE["background"],
            json.dumps(FIXED_PROFILE["excluded_cities"], ensure_ascii=False),
            now_str,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) VALUES (?, 200, 100)",
        (FIXED_USER_ID,),
    )
    save_user_strict_industries(conn, FIXED_USER_ID, FIXED_STRICT_INDUSTRIES)
    save_resumes(conn, FIXED_USER_ID, FIXED_RESUMES)

    conn.execute(
        "INSERT OR REPLACE INTO jobs (id, platform, platform_job_id, completeness, "
        "company_name, company_industry, experience_req, degree_req, first_seen_at, last_seen_at) "
        "VALUES (1, 'boss', 'job_golden_1', 'full', ?, ?, ?, ?, ?, ?)",
        (
            FIXED_JOB_VERSION["company_name"],
            FIXED_JOB_VERSION["company_industry"],
            FIXED_JOB_VERSION["experience_req"],
            FIXED_JOB_VERSION["degree_req"],
            now_str,
            now_str,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, district, description, content_hash, created_at) "
        "VALUES (10, 1, 1, 'detail', ?, '25-35K', 1, 25.0, 35.0, 12, 1, '深圳', '南山区', ?, 'hash_test_job_1', ?)",
        (
            FIXED_JOB_VERSION["title"],
            FIXED_JOB_VERSION["description"],
            now_str,
        ),
    )
    conn.execute("UPDATE jobs SET current_version_id = 10 WHERE id = 1")
    conn.execute(
        "INSERT OR REPLACE INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (1, ?, 1, 10, 1, 'queued', '{}', ?)",
        (FIXED_USER_ID, now_str),
    )


def get_mock_llm_response(prompt_version: str, review: bool = False) -> str:
    """按各版本的 schema 返回固定的合法 JSON 字符串。"""
    if review:
        resp: dict[str, Any] = {
            "facts": {
                "summary": {
                    "text": "负责核心业务系统的架构设计与后端开发工作",
                    "quotes": ["负责核心业务系统的架构设计与后端微服务开发"],
                },
                "work_type": {
                    "value": "数据与技术",
                    "subtype": "开发与测试",
                    "secondary": [],
                    "quotes": ["后端微服务开发"],
                },
                "sales_level": {
                    "value": "低",
                    "signals": [],
                },
                "experience": {
                    "requirement": "3-5年",
                    "requirement_type": "硬性",
                    "value": "满足",
                    "gap": "",
                },
                "work_intensity": {
                    "value": "双休",
                    "quotes": [],
                },
                "risk_signals": [],
            },
            "verdict": "check",
            "derivation": [
                "金融相关业务合规要求较高",
                "系统可能涉及较多历史遗留维护工作",
            ],
            "verdict_reason": "业务偏合规与维护，需进一步核实",
            "hr_questions": [
                "实际日常工作中新系统研发与合规运维的时间比例大概是多少？",
            ],
        }
        if prompt_version in ("v6", "v7", "v8", "v9", "v10"):
            resp["resume_suggestion"] = {
                "slot": 1,
                "reason": "5年后端架构经验更契合核心系统微服务开发",
            }
        return json.dumps(resp, ensure_ascii=False)

    if prompt_version == "v1":
        return json.dumps(
            {
                "verdict": "fit",
                "reasons": [
                    "技术栈与架构要求高度匹配",
                    "薪资与工作地点符合预期",
                ],
            },
            ensure_ascii=False,
        )
    elif prompt_version == "v2":
        return json.dumps(
            {
                "facts": {
                    "summary": {
                        "text": "负责核心业务系统的架构设计与后端开发工作",
                        "quotes": ["负责核心业务系统的架构设计与后端微服务开发"],
                    },
                    "work_type": {
                        "value": "数据",
                        "quotes": ["后端微服务开发"],
                    },
                    "sales_level": {
                        "value": "低",
                        "signals": [],
                    },
                    "experience": {
                        "requirement": "3-5年",
                        "value": "满足",
                        "gap": "",
                    },
                    "overtime": {
                        "value": "明确双休或不加班",
                        "quotes": [],
                    },
                },
                "verdict": "fit",
                "derivation": [
                    "核心技术方向匹配画像要求",
                    "经验要求与候选人背景相符",
                ],
            },
            ensure_ascii=False,
        )
    elif prompt_version == "v3":
        return json.dumps(
            {
                "facts": {
                    "summary": {
                        "text": "负责核心业务系统的架构设计与后端开发工作",
                        "quotes": ["负责核心业务系统的架构设计与后端微服务开发"],
                    },
                    "work_type": {
                        "value": "数据",
                        "secondary": [],
                        "quotes": ["后端微服务开发"],
                    },
                    "sales_level": {
                        "value": "低",
                        "signals": [],
                    },
                    "experience": {
                        "requirement": "3-5年",
                        "requirement_type": "硬性",
                        "value": "满足",
                        "gap": "",
                    },
                    "overtime": {
                        "value": "明确双休或不加班",
                        "quotes": [],
                    },
                },
                "verdict": "fit",
                "derivation": [
                    "核心技术方向匹配画像要求",
                    "经验要求与候选人背景相符",
                ],
                "verdict_reason": "技术栈与业务方向高度吻合",
            },
            ensure_ascii=False,
        )
    else:
        # v4, v5, v6, v7, v8, v9
        resp = {
            "facts": {
                "summary": {
                    "text": "负责核心业务系统的架构设计与后端开发工作",
                    "quotes": ["负责核心业务系统的架构设计与后端微服务开发"],
                },
                "work_type": {
                    "value": "数据与技术",
                    "subtype": "开发与测试",
                    "secondary": [],
                    "quotes": ["后端微服务开发"],
                },
                "sales_level": {
                    "value": "低",
                    "signals": [],
                },
                "experience": {
                    "requirement": "3-5年",
                    "requirement_type": "硬性",
                    "value": "满足",
                    "gap": "",
                },
                "work_intensity": {
                    "value": "双休",
                    "quotes": [],
                },
                "risk_signals": [],
            },
            "verdict": "try",
            "derivation": [
                "核心职责对口且薪资符合底线",
                "建议进一步了解具体业务方向与架构细节",
            ],
            "verdict_reason": "方向相符但需进一步确认业务细节",
            "hr_questions": [
                "团队目前微服务技术栈的具体选型和部署环境是怎样的？",
                "该岗位主要承接新系统研发还是现有老系统重构？",
            ],
        }
        if prompt_version in ("v6", "v7", "v8", "v9", "v10"):
            resp["resume_suggestion"] = {
                "slot": 1,
                "reason": "5年后端架构经验更契合核心系统微服务开发",
            }
        return json.dumps(resp, ensure_ascii=False)


def generate_version_golden_data(
    prompt_version: str, data_dir: Path, settings: Settings
) -> dict[str, Any]:
    """生成单个版本的客户端层请求/结果与后台判断层请求/结果。"""
    conn = open_db(data_dir)
    _setup_database(conn)

    fixed_settings = dataclasses.replace(
        settings,
        llm_api_key="test-api-key",
        price_input_per_m=2.0,
        price_cached_per_m=0.04,
        price_output_per_m=8.0,
        prompt_version=prompt_version,
        judge_concurrency=1,
    )

    # 1. 样本 A（客户端层）：关思考
    req_no_think: dict[str, Any] = {}

    def handler_no_think(request: httpx.Request) -> httpx.Response:
        nonlocal req_no_think
        req_no_think = json.loads(request.content.decode("utf-8"))
        content = get_mock_llm_response(prompt_version, review=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": FIXED_USAGE,
            },
        )

    transport_no_think = httpx.MockTransport(handler_no_think)
    outcome_no_think = run_llm_judgement(
        conn,
        fixed_settings,
        user_id=FIXED_USER_ID,
        profile=FIXED_PROFILE,
        job_version=FIXED_JOB_VERSION,
        prompt_version=prompt_version,
        engine=fixed_settings.judge_engine,
        known_facts=FIXED_KNOWN_FACTS,
        transport=transport_no_think,
    )
    client_outcome_no_think = dataclasses.asdict(outcome_no_think)

    # 2. 样本 A（客户端层）：开思考（录制请求正文覆盖 max_tokens 分支）
    req_think: dict[str, Any] = {}

    def handler_think(request: httpx.Request) -> httpx.Response:
        nonlocal req_think
        req_think = json.loads(request.content.decode("utf-8"))
        content = get_mock_llm_response(
            prompt_version,
            review=True if prompt_version in ("v4", "v5", "v6", "v7", "v8", "v9", "v10") else False,
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": FIXED_USAGE,
            },
        )

    transport_think = httpx.MockTransport(handler_think)
    run_llm_judgement(
        conn,
        fixed_settings,
        user_id=FIXED_USER_ID,
        profile=FIXED_PROFILE,
        job_version=FIXED_JOB_VERSION,
        prompt_version=prompt_version,
        engine=fixed_settings.review_engine,
        known_facts=FIXED_KNOWN_FACTS,
        transport=transport_think,
    )

    conn.close()

    # 3. 样本 B（后台判断层）：通过 JudgementWorker 执行完整判断
    worker_requests: dict[str, Any] = {"initial": None, "review": None}

    def handler_worker(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        is_thinking = body.get("thinking", {}).get("type") == "enabled"
        if not is_thinking:
            worker_requests["initial"] = body
            content = get_mock_llm_response(prompt_version, review=False)
        else:
            worker_requests["review"] = body
            content = get_mock_llm_response(prompt_version, review=True)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": FIXED_USAGE,
            },
        )

    worker_transport = httpx.MockTransport(handler_worker)
    worker = JudgementWorker(fixed_settings, transport=worker_transport)
    worker.start()
    try:
        assert worker.submit(1)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    conn_read = connect(data_dir)
    j_row = conn_read.execute("SELECT * FROM judgements WHERE id = 1").fetchone()
    conn_read.close()

    assert j_row is not None, "Worker failed to save judgement row"
    judgement_dict = dict(j_row)

    # 剔除自增主键与时间戳字段
    for key in ("id", "created_at", "finished_at"):
        judgement_dict.pop(key, None)

    # 将 JSON 文本列解析为 Python 对象以便比对
    for col in ("reasons", "facts", "derivation", "hr_questions", "review", "rule_result"):
        val = judgement_dict.get(col)
        if isinstance(val, str):
            try:
                judgement_dict[col] = json.loads(val)
            except Exception:
                pass

    return {
        "client_outcome_no_think": client_outcome_no_think,
        "client_request_no_think": req_no_think,
        "client_request_think": req_think,
        "worker_judgement_row": judgement_dict,
        "worker_request_initial": worker_requests["initial"],
        "worker_request_review": worker_requests["review"],
    }


@pytest.mark.parametrize("prompt_version", [f"v{i}" for i in range(1, 11)])
def test_prompt_version_golden(prompt_version: str, data_dir: Path, settings: Settings) -> None:
    actual_data = generate_version_golden_data(prompt_version, data_dir, settings)
    actual_json = json.dumps(actual_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    fixture_path = FIXTURES_DIR / f"{prompt_version}.json"

    if UPDATE_PROMPT_GOLDEN:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(actual_json, encoding="utf-8")
        return

    assert fixture_path.exists(), f"样本文件不存在: {fixture_path}，请先使用 JET_UPDATE_PROMPT_GOLDEN=1 生成样本"
    expected_json = fixture_path.read_text(encoding="utf-8")
    assert actual_json == expected_json
