"""体检第 69、70 条：llm_calls.model 各用途口径一致；jet serve 启动时打印的判断上限用配置值。"""

import httpx
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from jet.cli import main
from jet.db.store import open_db
from tests.api.test_prejudge_routes import _observe_job, _setup_active_profile
from tests.api.test_resume_upload_failure_profile import _first_upload_ok
from tests.conftest import FakeLlmHelper


def _latest_model(data_dir: Path, purpose: str) -> str:
    conn = open_db(data_dir)
    try:
        row = conn.execute(
            "SELECT model FROM llm_calls WHERE purpose = ? ORDER BY id DESC LIMIT 1", (purpose,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row["model"]


def test_prejudge_records_llm_model(
    llm_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
    fake_llm: FakeLlmHelper,
):
    client, headers = llm_client
    _setup_active_profile(data_dir)
    _observe_job(data_dir, "job_model_1")
    fake_llm.set_prejudge([{"id": "1", "level": "open", "reason": "薪资契合"}])

    resp = client.post(
        "/v1/prejudge",
        json={"jobs": [{"platform_job_id": "job_model_1", "title": "Python 后端", "company_name": "公司A"}]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    settings = client.app.state.settings
    assert _latest_model(data_dir, "prejudge") == settings.llm_model
    assert _latest_model(data_dir, "prejudge") != settings.judge_engine


def test_resume_profile_records_llm_model(
    paired_client: tuple[TestClient, dict[str, str]],
    data_dir: Path,
):
    client, headers = paired_client
    _first_upload_ok(client, headers)
    settings = client.app.state.settings
    assert _latest_model(data_dir, "resume_profile") == settings.llm_model


def test_serve_prints_configured_judge_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # 数据库里还是改配置前的旧上限
    conn = open_db(tmp_path)
    conn.execute("UPDATE user_settings SET daily_llm_limit = 50 WHERE user_id = 'me'")
    conn.close()

    monkeypatch.setenv("JET_DAILY_LLM_LIMIT", "123")
    monkeypatch.setattr(
        "httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("Connection refused")),
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)

    assert main(["serve", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "今日额度: 0/123" in out
