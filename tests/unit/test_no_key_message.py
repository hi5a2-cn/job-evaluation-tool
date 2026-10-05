"""体检第 50 条：没配 API Key 时，判断（含复核、评测走的同一入口）的报错指向设置页，不再让用户去改 .env。"""

import dataclasses
from pathlib import Path

import httpx

from jet.config import Settings
from jet.db.store import open_db
from jet.llm.client import run_llm_judgement


def test_no_key_error_points_to_settings_page(data_dir: Path, settings: Settings):
    conn = open_db(data_dir)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    try:
        outcome = run_llm_judgement(
            conn,
            dataclasses.replace(settings, llm_api_key=None),
            user_id="me",
            profile={"id": 1, "directions": [], "keywords": [], "cities": [], "exclude_keywords": []},
            job_version={"id": 1, "title": "Python工程师", "description": "后端研发"},
            transport=httpx.MockTransport(handler),
            backoff_delays=(0, 0),
        )
    finally:
        conn.close()

    assert outcome.status == "failed"
    assert outcome.error == "未配置 API Key（请在设置页填写）"
    assert ".env" not in outcome.error
    assert calls == []
