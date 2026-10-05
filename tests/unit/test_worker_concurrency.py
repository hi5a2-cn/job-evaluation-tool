"""单元测试：JudgementWorker 多线程并发与原则 VI 安全。"""
import dataclasses
import json
from pathlib import Path
import threading
import time
import httpx
import pytest

from jet.config import Settings, load_settings
from jet.db.store import open_db, utc_now
from jet.worker import JudgementWorker


def _make_v5_json(verdict: str, derivation: list[str] | None = None) -> str:
    der = derivation if derivation is not None else ["推导理由"]
    return json.dumps(
        {
            "facts": {
                "summary": {"text": "职责概述", "quotes": ["职责"]},
                "work_type": {"value": "数据与技术", "subtype": "开发与测试", "secondary": [], "quotes": ["职责"]},
                "sales_level": {"value": "低", "signals": []},
                "experience": {"requirement": None, "requirement_type": "未提及", "value": "满足", "gap": ""},
                "work_intensity": {"value": "双休", "quotes": []},
                "risk_signals": [],
            },
            "verdict": verdict,
            "derivation": der,
            "verdict_reason": f"{verdict}理由",
            "hr_questions": ["HR问题1"] if verdict in ("try", "check") else [],
        },
        ensure_ascii=False,
    )


def _setup_base_data(conn, user_id: str = "me", daily_limit: int = 100):
    now_str = utc_now()
    conn.execute(
        "INSERT OR REPLACE INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (1, ?, 1, '[\"Python\"]', '[]', '[\"深圳\"]', 15.0, '[]', ?)",
        (user_id, now_str),
    )
    conn.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) "
        "VALUES (?, ?, 100)",
        (user_id, daily_limit),
    )


def _insert_job_and_judgement(
    conn,
    jid: int,
    platform_job_id: str,
    title: str | None = None,
    job_id: int | None = None,
    version_no: int = 1,
) -> int:
    now_str = utc_now()
    actual_job_id = job_id if job_id is not None else jid
    job_title = title if title is not None else platform_job_id

    # 仅当 job 不存在时才插入
    existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (actual_job_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
            "VALUES (?, 'boss', ?, 'full', '测试公司', ?, ?)",
            (actual_job_id, platform_job_id, now_str, now_str),
        )

    version_id = jid * 10
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (?, ?, ?, 'detail', ?, '20-30K', 1, 20.0, 30.0, 12, 1, '深圳', '描述', ?, ?)",
        (version_id, actual_job_id, version_no, job_title, f"hash_{jid}", now_str),
    )
    conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (version_id, actual_job_id))

    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (?, 'me', ?, ?, 1, 'queued', '{}', ?)",
        (jid, actual_job_id, version_id, now_str),
    )
    return jid


def test_initial_concurrency_reaches_limit_and_all_done(data_dir: Path, settings: Settings):
    """
    连续提交 5 个岗位的判断（不同岗位），大模型响应延时 0.3s，judge_concurrency=3：
    - 全部 5 个最终 status='done'；
    - 同时在途的最大初判数 == 3（不超过 3，且确实并发到 3）。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    for i in range(1, 6):
        _insert_job_and_judgement(conn, i, f"job_{i}")
    conn.close()

    lock = threading.Lock()
    active_count = 0
    max_active = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_count, max_active
        with lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        try:
            time.sleep(0.3)
            content = _make_v5_json("skip")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                },
            )
        finally:
            with lock:
                active_count -= 1

    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        judge_concurrency=3,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        for i in range(1, 6):
            assert worker.submit(i)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    assert max_active == 3, f"Expected max concurrent initial == 3, got {max_active}"

    conn = open_db(data_dir)
    for i in range(1, 6):
        row = conn.execute("SELECT status, verdict FROM judgements WHERE id = ?", (i,)).fetchone()
        assert row["status"] == "done"
        assert row["verdict"] == "skip"
    conn.close()


def test_review_after_all_initial_and_max_one_concurrent(data_dir: Path, settings: Settings):
    """
    复核排在初判之后：有初判在排队或执行时复核不开始；复核同时最多 1 个。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 1, "job_1")
    _insert_job_and_judgement(conn, 2, "job_2")
    _insert_job_and_judgement(conn, 3, "job_3")
    conn.close()

    lock = threading.Lock()
    active_initial = 0
    active_review = 0
    max_active_review = 0
    events_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_initial, active_review, max_active_review
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        is_review = thinking
        text = request.content.decode("utf-8")

        job_tag = "unknown"
        for i in range(1, 4):
            if f"job_{i}" in text or f"hash_{i}" in text:
                job_tag = f"job_{i}"
                break

        with lock:
            if is_review:
                # 复核开始时：必须没有初判在执行
                assert active_initial == 0, f"Review started while initial was active (active_initial={active_initial})"
                active_review += 1
                if active_review > max_active_review:
                    max_active_review = active_review
            else:
                active_initial += 1
            events_log.append(f"{'review' if is_review else 'initial'}:{job_tag}")

        try:
            time.sleep(0.15)
            # 初判返回 try（需复核）；复核返回 check
            verdict = "check" if is_review else "try"
            content = _make_v5_json(verdict)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                },
            )
        finally:
            with lock:
                if is_review:
                    active_review -= 1
                else:
                    active_initial -= 1

    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        review_enabled=True,
        judge_concurrency=2,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        # 依次提交 1, 2, 3
        for i in [1, 2, 3]:
            assert worker.submit(i)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    # 复核最多同时 1 个
    assert max_active_review == 1, f"Expected max_active_review == 1, got {max_active_review}"

    # 验证所有的 initial 先于任何 review
    initial_indices = [idx for idx, ev in enumerate(events_log) if ev.startswith("initial:")]
    review_indices = [idx for idx, ev in enumerate(events_log) if ev.startswith("review:")]
    assert len(initial_indices) == 3
    assert len(review_indices) == 3
    assert max(initial_indices) < min(review_indices), (
        f"All initials must finish before reviews begin! Log: {events_log}"
    )


def test_same_user_job_never_runs_concurrently(data_dir: Path, settings: Settings):
    """
    同一用户同一岗位（user_id, job_id）同一时间只能有一个判断在跑（原则 VI）。
    两个不同判断对应同一个 job_id，不能并发执行。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    # 两个 judgement 指向同一个 actual job_id = 99，不同版本号
    _insert_job_and_judgement(conn, 10, "job_same_A", job_id=99, version_no=1)
    _insert_job_and_judgement(conn, 11, "job_same_B", job_id=99, version_no=2)
    conn.close()

    lock = threading.Lock()
    active_same_job = 0
    max_active_same_job = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_same_job, max_active_same_job
        with lock:
            active_same_job += 1
            if active_same_job > max_active_same_job:
                max_active_same_job = active_same_job
        try:
            time.sleep(0.3)
            content = _make_v5_json("skip")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                },
            )
        finally:
            with lock:
                active_same_job -= 1

    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        judge_concurrency=3,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        assert worker.submit(10)
        assert worker.submit(11)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    assert max_active_same_job == 1, f"Expected max_active_same_job == 1, got {max_active_same_job}"

    conn = open_db(data_dir)
    r10 = conn.execute("SELECT status FROM judgements WHERE id = 10").fetchone()
    r11 = conn.execute("SELECT status FROM judgements WHERE id = 11").fetchone()
    assert r10["status"] == "done"
    assert r11["status"] == "done"
    conn.close()


def test_quota_concurrency_safety(data_dir: Path, settings: Settings):
    """
    额度并发安全：daily_llm_limit=2、提交 4 个并发初判 → 恰好 2 个 billed 调用，其余 quota_exhausted，不超额。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn, daily_limit=2)
    for i in range(1, 5):
        _insert_job_and_judgement(conn, i, f"quota_job_{i}")
    conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.1)
        content = _make_v5_json("skip")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    worker_settings = dataclasses.replace(
        settings,
        llm_api_key="test-key",
        prompt_version="v5",
        judge_concurrency=4,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        for i in range(1, 5):
            assert worker.submit(i)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    conn = open_db(data_dir)
    billed_count = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE user_id = 'me' AND billed = 1 AND purpose = 'judge'"
    ).fetchone()[0]
    done_count = conn.execute(
        "SELECT COUNT(*) FROM judgements WHERE status = 'done'"
    ).fetchone()[0]
    exhausted_count = conn.execute(
        "SELECT COUNT(*) FROM judgements WHERE status = 'quota_exhausted'"
    ).fetchone()[0]
    conn.close()

    assert billed_count == 2, f"Expected billed_count == 2, got {billed_count}"
    assert done_count == 2, f"Expected done_count == 2, got {done_count}"
    assert exhausted_count == 2, f"Expected exhausted_count == 2, got {exhausted_count}"


def test_judge_concurrency_config_parsing(data_dir: Path):
    """
    JET_JUDGE_CONCURRENCY 配置解析：默认 3；非法值（0、6、abc）报错。
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. 默认值 3
    s_default = load_settings(data_dir, env={})
    assert s_default.judge_concurrency == 3

    # 2. 合法值 1 和 5
    s_1 = load_settings(data_dir, env={"JET_JUDGE_CONCURRENCY": "1"})
    assert s_1.judge_concurrency == 1

    s_5 = load_settings(data_dir, env={"JET_JUDGE_CONCURRENCY": "5"})
    assert s_5.judge_concurrency == 5

    # 3. .env 文件读取
    (data_dir / ".env").write_text("JET_JUDGE_CONCURRENCY=4\n", encoding="utf-8")
    s_file = load_settings(data_dir, env={})
    assert s_file.judge_concurrency == 4
    (data_dir / ".env").unlink()

    # 4. 非法值校验报错
    with pytest.raises(ValueError, match="1–5 的整数"):
        load_settings(data_dir, env={"JET_JUDGE_CONCURRENCY": "0"})

    with pytest.raises(ValueError, match="1–5 的整数"):
        load_settings(data_dir, env={"JET_JUDGE_CONCURRENCY": "6"})

    with pytest.raises(ValueError, match="1–5 的整数"):
        load_settings(data_dir, env={"JET_JUDGE_CONCURRENCY": "abc"})
