"""单元测试：判断队列优先级调度（初判优先且后进先出，复核先进先出）与复核推导截断保留。"""
import dataclasses
import json
from pathlib import Path
import threading
import time
import httpx
import pytest

from jet.config import Settings
from jet.db.store import connect, init_db, open_db, utc_now
from jet.llm import prompt_v4
from jet.llm.client import run_llm_judgement
from jet.llm.prompt import ParseError
from jet.worker import JudgementWorker


def _make_v5_json(verdict: str, derivation: list[str] | None = None) -> str:
    der = derivation if derivation is not None else ["推导1", "推导2"]
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


def _setup_base_data(conn: open_db, user_id: str = "me"):
    now_str = utc_now()
    conn.execute(
        "INSERT OR REPLACE INTO profiles (id, user_id, version_no, directions, keywords, cities, min_monthly_k, "
        "exclude_keywords, created_at) "
        "VALUES (1, ?, 1, '[\"Python\"]', '[]', '[\"深圳\"]', 15.0, '[]', ?)",
        (user_id, now_str),
    )
    conn.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, daily_llm_limit, daily_assist_limit) "
        "VALUES (?, 100, 100)",
        (user_id,),
    )


def _insert_job_and_judgement(conn, jid: int, platform_job_id: str, title: str | None = None) -> int:
    now_str = utc_now()
    job_title = title if title is not None else platform_job_id
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, company_name, first_seen_at, last_seen_at) "
        "VALUES (?, 'boss', ?, 'full', '测试公司', ?, ?)",
        (jid, platform_job_id, now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_raw, salary_visible, "
        "salary_min_k, salary_max_k, salary_months, salary_parse_ok, city, description, content_hash, created_at) "
        "VALUES (?, ?, 1, 'detail', ?, '20-30K', 1, 20.0, 30.0, 12, 1, '深圳', '描述', ?, ?)",
        (jid * 10, jid, job_title, f"hash_{jid}", now_str),
    )
    conn.execute("UPDATE jobs SET current_version_id = ? WHERE id = ?", (jid * 10, jid))
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (?, 'me', ?, ?, 1, 'queued', '{}', ?)",
        (jid, jid, jid * 10, now_str),
    )
    return jid


def test_queue_priority_and_lifo_fifo_order(data_dir: Path, settings: Settings):
    """
    测试队列调度顺序：
    1. 初判任务一律先于复核任务执行；
    2. 初判之间最新提交的先执行（后进先出 LIFO）；
    3. 复核之间先进先出（FIFO）。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 1, "job_1")
    _insert_job_and_judgement(conn, 2, "job_2")
    _insert_job_and_judgement(conn, 3, "job_3")
    _insert_job_and_judgement(conn, 4, "job_4")
    conn.close()

    events_log: list[str] = []
    job1_entered = threading.Event()
    job1_proceed = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        user_msg = next(m["content"] for m in body["messages"] if m["role"] == "user")

        # 识别是哪个岗位的请求
        job_tag = "unknown"
        for i in range(1, 5):
            if f"hash_{i}" in user_msg or f"job_{i}" in user_msg or f"jid={i}" in user_msg:
                job_tag = f"job_{i}"
                break
        if job_tag == "unknown":
            # 根据职位描述/哈希识别
            for i in range(1, 5):
                if f"hash_{i}" in request.content.decode("utf-8"):
                    job_tag = f"job_{i}"
                    break

        kind = "review" if thinking else "initial"
        tag = f"{kind}:{job_tag}"
        events_log.append(tag)

        if kind == "initial" and job_tag == "job_1":
            job1_entered.set()
            job1_proceed.wait(timeout=5.0)

        # 初判返回 try（需复核）；复核返回 check（降档）
        content = _make_v5_json("check" if thinking else "try")
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
        review_enabled=True,
        judge_concurrency=1,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        # 1. 提交 job_1，worker 开始执行 job_1 初判并卡在 job1_proceed
        assert worker.submit(1)
        assert job1_entered.wait(timeout=5.0)

        # 2. 在 job_1 初判还在处理时，依次提交 job_2, job_3
        assert worker.submit(2)
        assert worker.submit(3)

        # 此时初始队列里有 [2, 3]，job_1 还在运行
        # 3. 放行 job_1 初判
        job1_proceed.set()

        # 等待所有任务完成
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    # 验证执行顺序：
    # 1. initial:job_1 运行；
    # 2. job_1 初判完成后进入 review 队列（复核队列=[job_1]）；
    # 3. 此时初始栈有 [job_2, job_3]，初判优先于复核，且初判后进先出（LIFO）：
    #    最新提交的 job_3 先执行 initial！
    # 4. initial:job_3 完成，进入 review 队列（复核队列=[job_1, job_3]）；
    # 5. 初始栈还有 [job_2]，初判仍优先于复核，执行 initial:job_2；
    # 6. initial:job_2 完成，进入 review 队列（复核队列=[job_1, job_3, job_2]）；
    # 7. 初判全部完成，开始按 FIFO 执行复核：review:job_1 -> review:job_3 -> review:job_2！
    expected_order = [
        "initial:job_1",
        "initial:job_3",
        "initial:job_2",
        "review:job_1",
        "review:job_3",
        "review:job_2",
    ]
    assert events_log == expected_order


def test_delayed_review_results_downgrade_kept_failed(data_dir: Path, settings: Settings):
    """
    测试复核延后执行后，结果与原先一致（降档、保留、失败各一例）。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 10, "job_downgrade")
    _insert_job_and_judgement(conn, 20, "job_kept")
    _insert_job_and_judgement(conn, 30, "job_failed")
    conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        text = request.content.decode("utf-8")

        if "job_downgrade" in text:
            # 降档：初判 try，复核 check
            verdict = "check" if thinking else "try"
            content = _make_v5_json(verdict, ["降档理由1", "降档理由2"] if thinking else ["初判理由"])
        elif "job_kept" in text:
            # 保留：初判 try，复核 try
            verdict = "try"
            content = _make_v5_json(verdict, ["保留理由1"] if thinking else ["初判理由"])
        elif "job_failed" in text:
            if thinking:
                # 失败：复核返回不合规推导（0条，无法解析）
                content = _make_v5_json("check", [])
            else:
                content = _make_v5_json("try", ["初判理由"])
        else:
            content = _make_v5_json("check")

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
        review_enabled=True,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        worker.submit(10)
        worker.submit(20)
        worker.submit(30)
        assert worker.wait_idle(10.0)
    finally:
        worker.stop()

    conn = open_db(data_dir)
    # 1. 降档案例
    j10 = conn.execute("SELECT * FROM judgements WHERE id = 10").fetchone()
    assert j10["status"] == "done"
    assert j10["verdict"] == "check"
    r10 = json.loads(j10["review"])
    assert r10["outcome"] == "downgraded"
    assert r10["first_verdict"] == "try"
    assert r10["review_verdict"] == "check"
    der10 = json.loads(j10["derivation"])
    assert "降为「需要确认」" in der10[0]

    # 2. 保留案例
    j20 = conn.execute("SELECT * FROM judgements WHERE id = 20").fetchone()
    assert j20["status"] == "done"
    assert j20["verdict"] == "try"
    r20 = json.loads(j20["review"])
    assert r20["outcome"] == "kept"
    assert r20["first_verdict"] == "try"
    assert r20["review_verdict"] == "try"

    # 3. 失败案例
    j30 = conn.execute("SELECT * FROM judgements WHERE id = 30").fetchone()
    assert j30["status"] == "done"
    assert j30["verdict"] == "try"  # 保留 B 的结论
    r30 = json.loads(j30["review"])
    assert r30["outcome"] == "failed"
    assert r30["first_verdict"] == "try"
    assert "复核失败" in r30["error"] or "大模型返回无法解析" in r30["error"]
    conn.close()


def test_delayed_review_skipped_when_superseded(data_dir: Path, settings: Settings):
    """测试复核执行前若该判断行已被替换（superseded_by 非空），跳过复核。"""
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 100, "job_superseded")
    conn.close()

    blocker_entered = threading.Event()
    blocker_proceed = threading.Event()
    job100_initial_done = threading.Event()
    job101_submitted = threading.Event()
    review_called_for_100 = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        text = request.content.decode("utf-8")
        if thinking:
            if "job_superseded" in text:
                review_called_for_100.set()
            content = _make_v5_json("check")
        else:
            if "job_blocker" in text:
                blocker_entered.set()
                blocker_proceed.wait(timeout=5.0)
                content = _make_v5_json("check")
            else:
                content = _make_v5_json("try")
                if "job_superseded" in text:
                    job100_initial_done.set()
                    # 等测试把 101 提交进去再返回：100 的初判处理完时 101 已在排队，
                    # 复核一定让路给 101，不依赖测试线程醒得够快
                    job101_submitted.wait(timeout=5.0)
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
        review_enabled=True,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))

    worker.start()
    try:
        conn = open_db(data_dir)
        _insert_job_and_judgement(conn, 101, "job_blocker")
        conn.close()

        # 提交 100（初判返回 try，需要复核）
        worker.submit(100)
        assert job100_initial_done.wait(timeout=5.0)

        # 提交 101（初判任务，会抢在 100 复核前执行），再放行 100 的初判返回
        worker.submit(101)
        job101_submitted.set()
        assert blocker_entered.wait(timeout=5.0)

        # 此时 101 正在执行初判，100 在复核队列排队未执行！将 100 标为 superseded
        conn = open_db(data_dir)
        conn.execute("UPDATE judgements SET superseded_by = 101 WHERE id = 100")
        conn.commit()
        conn.close()

        # 放行 101 初判
        blocker_proceed.set()
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    # 复核应当被跳过，不会对 100 发起 thinking 复核请求
    assert not review_called_for_100.is_set()

    conn = open_db(data_dir)
    j100 = conn.execute("SELECT * FROM judgements WHERE id = 100").fetchone()
    rev = json.loads(j100["review"])
    assert rev["outcome"] == "pending"  # 保持跳过状态
    assert j100["superseded_by"] == 101
    conn.close()


def test_wait_idle_waits_for_review_completion(data_dir: Path, settings: Settings):
    """测试 wait_idle 必须等待复核任务也完成才返回。"""
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 200, "job_wait_idle")
    conn.close()

    review_in_progress = threading.Event()
    review_finish = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        if thinking:
            review_in_progress.set()
            review_finish.wait(timeout=5.0)
            content = _make_v5_json("check")
        else:
            content = _make_v5_json("try")
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
        review_enabled=True,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        worker.submit(200)
        assert review_in_progress.wait(timeout=5.0)

        # 此时初判已结束，复核正在运行，wait_idle(0.05) 应该返回 False！
        assert worker.wait_idle(timeout=0.05) is False

        # 放行复核
        review_finish.set()

        # 现在 wait_idle 应该等到复核完成并返回 True
        assert worker.wait_idle(timeout=5.0) is True
    finally:
        worker.stop()

    conn = open_db(data_dir)
    j200 = conn.execute("SELECT * FROM judgements WHERE id = 200").fetchone()
    assert j200["status"] == "done"
    rev = json.loads(j200["review"])
    assert rev["outcome"] == "downgraded"
    conn.close()


def test_review_truncates_7_derivations_and_initial_fails_7_derivations(data_dir: Path, settings: Settings):
    """
    测试推导截断：
    1. 复核返回 7 条 derivation 时截断为 5 条并正常完成；
    2. 初判返回 7 条 derivation 时仍按原规则失败。
    """
    conn = open_db(data_dir)
    _setup_base_data(conn)
    _insert_job_and_judgement(conn, 301, "job_review_7")
    _insert_job_and_judgement(conn, 302, "job_initial_7")
    conn.close()

    seven_reasons = [f"推导理由_{i}" for i in range(1, 8)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        thinking = body.get("thinking", {}).get("type") == "enabled"
        text = request.content.decode("utf-8")

        if "job_review_7" in text:
            if thinking:
                # 复核返回 7 条 derivation
                content = _make_v5_json("check", seven_reasons)
            else:
                content = _make_v5_json("try", ["初判理由1"])
        else:
            # 初判返回 7 条 derivation
            content = _make_v5_json("apply", seven_reasons)

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
        review_enabled=True,
    )
    worker = JudgementWorker(worker_settings, transport=httpx.MockTransport(handler))
    worker.start()
    try:
        worker.submit(301)
        assert worker.wait_idle(5.0)

        worker.submit(302)
        assert worker.wait_idle(5.0)
    finally:
        worker.stop()

    conn = open_db(data_dir)
    # 1. 验证 301（复核返回 7 条）：截断为 5 条并正常完成
    j301 = conn.execute("SELECT * FROM judgements WHERE id = 301").fetchone()
    assert j301["status"] == "done"
    assert j301["verdict"] == "check"
    r301 = json.loads(j301["review"])
    assert r301["outcome"] == "downgraded"
    der301 = json.loads(j301["derivation"])
    assert len(der301) == 5
    assert "降为「需要确认」" in der301[0]
    # 保留了复核推导的前几条
    assert der301[1] == "推导理由_1"
    assert der301[4] == "推导理由_4"

    # 2. 验证 302（初判返回 7 条）：失败报错
    j302 = conn.execute("SELECT * FROM judgements WHERE id = 302").fetchone()
    assert j302["status"] == "failed"
    assert "derivation 必须是包含 1 到 5 条字符串的列表" in j302["error"]
    conn.close()


def test_parse_facts_derivation_truncation_unit():
    """直接测试 parse_facts 的 truncate_derivation 参数行为。"""
    seven_reasons = [f"理由{i}" for i in range(7)]
    text_7 = _make_v5_json("check", seven_reasons)

    # 1. 默认 truncate_derivation=False 时，7 条推导抛出 ParseError
    with pytest.raises(ParseError, match="derivation 必须是包含 1 到 5 条字符串的列表"):
        prompt_v4.parse_facts(text_7, truncate_derivation=False)

    # 2. truncate_derivation=True 时，7 条推导截断保留前 5 条
    facts, verdict, derivation, verdict_reason, hr_questions = prompt_v4.parse_facts(
        text_7, truncate_derivation=True
    )
    assert verdict == "check"
    assert len(derivation) == 5
    assert derivation == [f"理由{i}" for i in range(5)]

    # 3. 少于 1 条（空列表）：无论 truncate 开关如何都报错
    text_0 = _make_v5_json("check", [])
    with pytest.raises(ParseError, match="derivation 必须是包含 1 到 5 条字符串的列表"):
        prompt_v4.parse_facts(text_0, truncate_derivation=True)
    with pytest.raises(ParseError, match="derivation 必须是包含 1 到 5 条字符串的列表"):
        prompt_v4.parse_facts(text_0, truncate_derivation=False)

    # 4. 不是字符串列表：报错
    text_invalid = _make_v5_json("check", ["合法理由", 123])  # type: ignore
    with pytest.raises(ParseError, match="derivation 项必须是非空字符串"):
        prompt_v4.parse_facts(text_invalid, truncate_derivation=True)
