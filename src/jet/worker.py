# 有意偏离说明：初判最多 N 个并发（由 Settings.judge_concurrency 控制，默认为 3）+ 复核最多 1 个且排在所有初判之后。
# 每个线程拥有独立的 SQLite 连接，写事务使用 BEGIN IMMEDIATE。
# 同一 (user_id, job_id) 在同一时间只允许一个任务在执行（满足原则 VI）。
import collections
import json
import sqlite3
import threading
import time
from typing import Any, Mapping
import httpx

from jet.config import Settings
from jet.db.store import connect, transaction, utc_now
from jet.llm.client import run_llm_judgement
from jet.llm.quota import remaining_today
from jet.llm.versions import EARLIEST_PROMPT_VERSION, get_prompt_version


def _job_version_with_company(
    conn: sqlite3.Connection,
    v_row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """将 job_versions 行转换为包含 company_name、company_industry、experience_req 与 degree_req 的字典（按 job_id 查 jobs 表，查不到为 None）。"""
    jv = dict(v_row)
    job_id = jv.get("job_id")
    company_name = None
    company_industry = None
    experience_req = None
    degree_req = None
    if job_id is not None:
        row = conn.execute("SELECT company_name, company_industry, experience_req, degree_req FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row:
            company_name = row["company_name"]
            company_industry = row["company_industry"]
            experience_req = row["experience_req"]
            degree_req = row["degree_req"]
    if company_name is None:
        company_name = jv.get("company_name")
    if company_industry is None:
        company_industry = jv.get("company_industry")
    if experience_req is None:
        experience_req = jv.get("experience_req")
    if degree_req is None:
        degree_req = jv.get("degree_req")
    jv["company_name"] = company_name
    jv["company_industry"] = company_industry
    jv["experience_req"] = experience_req
    jv["degree_req"] = degree_req
    return jv


def _load_profile(conn: sqlite3.Connection, profile_id: int) -> dict[str, Any] | None:
    """读取判断用的画像（初判和复核共用，保证两者看到的画像一致）；画像不存在时返回 None。"""
    p_row = conn.execute(
        "SELECT id, user_id, version_no, directions, keywords, cities, min_monthly_k, exclude_keywords, "
        "work_preference, background, excluded_cities, nonpref_min_monthly_k "
        "FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if not p_row:
        return None
    preferred_cities = json.loads(p_row["cities"]) if p_row["cities"] else []
    excluded_cities = json.loads(p_row["excluded_cities"]) if p_row["excluded_cities"] else []
    return {
        "id": p_row["id"],
        "user_id": p_row["user_id"],
        "version_no": p_row["version_no"],
        "directions": json.loads(p_row["directions"]),
        "keywords": json.loads(p_row["keywords"]),
        "cities": preferred_cities,
        "preferred_cities": preferred_cities,
        "excluded_cities": excluded_cities,
        "min_monthly_k": p_row["min_monthly_k"],
        "nonpref_min_monthly_k": p_row["nonpref_min_monthly_k"],
        "exclude_keywords": json.loads(p_row["exclude_keywords"]),
        "work_preference": p_row["work_preference"] or "",
        "background": p_row["background"] or "",
    }


class JudgementWorker:
    """
    Background worker processing LLM judgements concurrently for initial judgements
    (up to Settings.judge_concurrency, default 3) and sequentially for reviews (at most 1,
    scheduled after all initial judgements).

    Each worker thread maintains its own SQLite connection.
    Write transactions use BEGIN IMMEDIATE to avoid busy locking.
    Deduplicates active requests for the same (user_id, job_id).
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._initial_stack: list[int] = []
        self._review_queue: collections.deque[int] = collections.deque()
        self._review_set: set[int] = set()
        self._in_flight: set[int] = set()
        self._judgement_jobs: dict[int, tuple[str, int]] = {}  # judgement_id -> (user_id, job_id)
        self._active_jobs: set[tuple[str, int]] = set()        # active (user_id, job_id)
        self._active_initial: int = 0
        self._active_review: int = 0
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._threads: list[threading.Thread] = []
        self._stopped = False

    def _lookup_user_job_with_conn(self, conn: sqlite3.Connection, judgement_id: int) -> tuple[str, int] | None:
        try:
            row = conn.execute(
                "SELECT user_id, job_id FROM judgements WHERE id = ?",
                (judgement_id,),
            ).fetchone()
            if row:
                return (row["user_id"], row["job_id"])
        except Exception:
            pass
        return None

    def submit(self, judgement_id: int) -> bool:
        """
        Submit a judgement ID to the queue.

        The same judgement already queued or running is merged (ignored). Different judgements
        for the same (user_id, job_id) will not run concurrently (principle VI).
        Returns True if enqueued, False if merged.
        """
        with self._lock:
            if judgement_id in self._in_flight:
                return False
            self._in_flight.add(judgement_id)
            self._initial_stack.append(judgement_id)
            self._cv.notify_all()
            return True

    def start(self) -> None:
        """Start worker threads (N initial + 1 review)."""
        with self._lock:
            if self._threads and any(t.is_alive() for t in self._threads):
                return
            self._stopped = False
            self._threads = []

            concurrency = max(1, min(5, self.settings.judge_concurrency))
            for i in range(1, concurrency + 1):
                t = threading.Thread(
                    target=self._run_initial,
                    daemon=True,
                    name=f"JudgementWorker-initial-{i}",
                )
                self._threads.append(t)

            r_thread = threading.Thread(
                target=self._run_review,
                daemon=True,
                name="JudgementWorker-review-1",
            )
            self._threads.append(r_thread)

            for t in self._threads:
                t.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop all worker threads gracefully."""
        with self._lock:
            self._stopped = True
            self._cv.notify_all()
        for t in list(self._threads):
            t.join(timeout=timeout)
        self._threads.clear()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until all initial and review queues are empty and no task is currently processing (for tests)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if (
                    not self._initial_stack
                    and not self._review_queue
                    and self._active_initial == 0
                    and self._active_review == 0
                    and len(self._in_flight) == 0
                ):
                    return True
            time.sleep(0.01)

        with self._lock:
            return (
                not self._initial_stack
                and not self._review_queue
                and self._active_initial == 0
                and self._active_review == 0
                and len(self._in_flight) == 0
            )

    def _pick_initial_task(self, conn: sqlite3.Connection) -> int | None:
        chosen_idx = -1
        for idx in range(len(self._initial_stack) - 1, -1, -1):
            jid = self._initial_stack[idx]
            if jid not in self._judgement_jobs:
                uj = self._lookup_user_job_with_conn(conn, jid)
                if uj is not None:
                    self._judgement_jobs[jid] = uj
            uj = self._judgement_jobs.get(jid)
            if uj is None or uj not in self._active_jobs:
                chosen_idx = idx
                break

        if chosen_idx == -1:
            return None

        judgement_id = self._initial_stack.pop(chosen_idx)
        uj = self._judgement_jobs.get(judgement_id)
        if uj is not None:
            self._active_jobs.add(uj)
        self._active_initial += 1
        return judgement_id

    def _pick_review_task(self, conn: sqlite3.Connection) -> int | None:
        if self._initial_stack or self._active_initial > 0:
            return None

        chosen_idx = -1
        for idx in range(len(self._review_queue)):
            jid = self._review_queue[idx]
            if jid not in self._judgement_jobs:
                uj = self._lookup_user_job_with_conn(conn, jid)
                if uj is not None:
                    self._judgement_jobs[jid] = uj
            uj = self._judgement_jobs.get(jid)
            if uj is None or uj not in self._active_jobs:
                chosen_idx = idx
                break

        if chosen_idx == -1:
            return None

        judgement_id = self._review_queue[chosen_idx]
        del self._review_queue[chosen_idx]
        self._review_set.discard(judgement_id)
        uj = self._judgement_jobs.get(judgement_id)
        if uj is not None:
            self._active_jobs.add(uj)
        self._active_review += 1
        return judgement_id

    def _run_initial(self) -> None:
        conn = connect(self.settings.data_dir)
        try:
            while not self._stopped:
                with self._lock:
                    while not self._stopped:
                        judgement_id = self._pick_initial_task(conn)
                        if judgement_id is not None:
                            break
                        self._cv.wait()
                    if self._stopped:
                        break

                try:
                    j_row = conn.execute(
                        "SELECT id, user_id, job_id, job_version_id, profile_id, status, prompt_version FROM judgements WHERE id = ?",
                        (judgement_id,),
                    ).fetchone()

                    if j_row:
                        with transaction(conn, immediate=True):
                            cur = conn.execute(
                                "UPDATE judgements SET status = 'running' WHERE id = ? AND status = 'queued'",
                                (judgement_id,),
                            )
                            should_run = cur.rowcount > 0

                        if should_run:
                            self._process_judgement(conn, judgement_id, j_row)
                except Exception as e:
                    now_str = utc_now()
                    try:
                        with transaction(conn, immediate=True):
                            conn.execute(
                                "UPDATE judgements SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                (f"后台执行异常: {e}", now_str, judgement_id),
                            )
                    except Exception:
                        pass
                finally:
                    with self._lock:
                        uj = self._judgement_jobs.get(judgement_id)
                        if judgement_id not in self._review_set:
                            self._in_flight.discard(judgement_id)
                            self._judgement_jobs.pop(judgement_id, None)
                        if uj is not None:
                            self._active_jobs.discard(uj)
                        self._active_initial -= 1
                        self._cv.notify_all()
        finally:
            conn.close()

    def _run_review(self) -> None:
        conn = connect(self.settings.data_dir)
        try:
            while not self._stopped:
                with self._lock:
                    while not self._stopped:
                        judgement_id = self._pick_review_task(conn)
                        if judgement_id is not None:
                            break
                        self._cv.wait()
                    if self._stopped:
                        break

                try:
                    self._process_review(conn, judgement_id)
                except Exception as e:
                    try:
                        info = {
                            "outcome": "failed",
                            "error": f"复核执行异常: {e}"[:200],
                        }
                        with transaction(conn, immediate=True):
                            conn.execute(
                                "UPDATE judgements SET review = ? WHERE id = ?",
                                (json.dumps(info, ensure_ascii=False), judgement_id),
                            )
                    except Exception:
                        pass
                finally:
                    with self._lock:
                        uj = self._judgement_jobs.get(judgement_id)
                        self._in_flight.discard(judgement_id)
                        self._judgement_jobs.pop(judgement_id, None)
                        if uj is not None:
                            self._active_jobs.discard(uj)
                        self._active_review -= 1
                        self._cv.notify_all()
        finally:
            conn.close()

    def _process_judgement(self, conn: sqlite3.Connection, judgement_id: int, j_row: sqlite3.Row) -> None:
        user_id = j_row["user_id"]
        profile = _load_profile(conn, j_row["profile_id"])

        if profile is None:
            now_str = utc_now()
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET status = 'failed', error = '画像不存在', finished_at = ? WHERE id = ?",
                    (now_str, judgement_id),
                )
            return

        v_row = conn.execute(
            "SELECT * FROM job_versions WHERE id = ?",
            (j_row["job_version_id"],),
        ).fetchone()

        if not v_row:
            now_str = utc_now()
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET status = 'failed', error = '岗位版本不存在', finished_at = ? WHERE id = ?",
                    (now_str, judgement_id),
                )
            return

        if not self.settings.llm_api_key:
            now_str = utc_now()
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET status = 'interrupted', error = '未配置 API Key（请在设置页填写）', finished_at = ? WHERE id = ?",
                    (now_str, judgement_id),
                )
            return

        delays = (0.0, 0.0) if self.transport is not None else (0.5, 1.0)
        p_ver = self.settings.prompt_version
        job_version_dict = _job_version_with_company(conn, v_row)
        outcome = run_llm_judgement(
            conn,
            self.settings,
            user_id=user_id,
            judgement_id=judgement_id,
            profile=profile,
            job_version=job_version_dict,
            transport=self.transport,
            backoff_delays=delays,
            prompt_version=p_ver,
            engine=self.settings.judge_engine,
        )

        if outcome.status == "quota_exhausted":
            now_str = utc_now()
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET status = 'quota_exhausted', finished_at = ?, error = ? WHERE id = ?",
                    (now_str, outcome.error, judgement_id),
                )
            return

        if outcome.status != "done":
            now_str = utc_now()
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                    (outcome.error or "大模型调用失败", now_str, judgement_id),
                )
            return

        settings = self.settings
        main_engine = outcome.engine or settings.judge_engine or ""
        main_is_thinking = "think" in main_engine and "no-think" not in main_engine
        outcome_ver = get_prompt_version(outcome.prompt_version) if outcome.prompt_version else None
        needs_review = (
            outcome.verdict in ("apply", "try")
            and settings.review_enabled
            and not main_is_thinking
            and bool(outcome_ver and outcome_ver.supports_review)
        )

        r_dir = None
        r_reason = None
        if outcome.resume_suggestion and isinstance(outcome.resume_suggestion, dict):
            slot_val = outcome.resume_suggestion.get("slot")
            clean_reason = str(outcome.resume_suggestion.get("reason") or "").strip()
            if slot_val is not None and clean_reason:
                r_dir = str(slot_val)
                r_reason = clean_reason[:40]

        now_str = utc_now()
        if not needs_review:
            with transaction(conn, immediate=True):
                if outcome_ver and outcome_ver.has_facts_and_derivation:
                    hr_q_json = (
                        json.dumps(outcome.hr_questions, ensure_ascii=False)
                        if (outcome_ver.has_hr_questions and outcome.hr_questions)
                        else None
                    )
                    conn.execute(
                        "UPDATE judgements SET status = 'done', source = 'llm', verdict = ?, "
                        "prompt_version = ?, engine = ?, facts = ?, derivation = ?, verdict_reason = ?, "
                        "hr_questions = ?, review = NULL, finished_at = ?, error = NULL, "
                        "resume_direction = ?, resume_reason = ? WHERE id = ?",
                        (
                            outcome.verdict,
                            outcome.prompt_version,
                            outcome.engine,
                            json.dumps(outcome.facts, ensure_ascii=False) if outcome.facts else None,
                            json.dumps(outcome.derivation, ensure_ascii=False) if outcome.derivation else None,
                            outcome.verdict_reason if outcome_ver.has_verdict_reason else None,
                            hr_q_json,
                            now_str,
                            r_dir,
                            r_reason,
                            judgement_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE judgements SET status = 'done', source = 'llm', verdict = ?, reasons = ?, "
                        "prompt_version = ?, engine = ?, review = NULL, finished_at = ?, error = NULL WHERE id = ?",
                        (
                            outcome.verdict,
                            json.dumps(outcome.reasons, ensure_ascii=False) if outcome.reasons else None,
                            outcome.prompt_version or EARLIEST_PROMPT_VERSION,
                            outcome.engine,
                            now_str,
                            judgement_id,
                        ),
                    )
            return

        # B 完成且需要复核时，先把 B 的完整结果写入判断行（status='done', review.outcome='pending'），
        # 然后把复核作为单独任务放进复核队列
        review_engine = settings.review_engine
        pending_review = {
            "outcome": "pending",
            "engine": review_engine,
            "first_verdict": outcome.verdict,
        }
        hr_q_json = (
            json.dumps(outcome.hr_questions, ensure_ascii=False)
            if outcome.hr_questions
            else None
        )
        with transaction(conn, immediate=True):
            conn.execute(
                "UPDATE judgements SET status = 'done', source = 'llm', verdict = ?, "
                "prompt_version = ?, engine = ?, facts = ?, derivation = ?, verdict_reason = ?, "
                "hr_questions = ?, review = ?, finished_at = ?, error = NULL, "
                "resume_direction = ?, resume_reason = ? WHERE id = ?",
                (
                    outcome.verdict,
                    outcome.prompt_version,
                    outcome.engine,
                    json.dumps(outcome.facts, ensure_ascii=False) if outcome.facts else None,
                    json.dumps(outcome.derivation, ensure_ascii=False) if outcome.derivation else None,
                    outcome.verdict_reason,
                    hr_q_json,
                    json.dumps(pending_review, ensure_ascii=False),
                    now_str,
                    r_dir,
                    r_reason,
                    judgement_id,
                ),
            )

        with self._lock:
            self._review_queue.append(judgement_id)
            self._review_set.add(judgement_id)
            self._cv.notify_all()

    # 复核（2026-09-25 用户决定）：B 给出 apply / try 时，用开启思考的 C 再判一次。
    # 1. B 完成且需要复核时，先把 B 的完整结果写入判断行（status='done', review.outcome='pending'），
    #    作为单独任务放入复核队列，只有没有待执行的初判时才执行复核；
    # 2. C 更低 → 用 C 的结果覆盖 verdict/facts/derivation 等，review.outcome='downgraded'，finished_at 更新；
    # 3. C 相同或更高、失败、不合规 → 只把 review 改为 kept / failed（含 error），B 的结论不动；
    # 4. 额度不足 → 不调用 C，review.outcome='quota_exhausted'（B 的结论已经是 done）。
    # 执行前若该判断行已被替换（superseded_by 非空）或 review.outcome 已不是 'pending'，跳过。
    _VERDICT_RANK = {"apply": 0, "try": 1, "check": 2, "skip": 3}
    _VERDICT_CN = {"apply": "适合投递", "try": "可以一试", "check": "需要确认", "skip": "不建议投"}

    def _process_review(self, conn: sqlite3.Connection, judgement_id: int) -> None:
        """
        执行复核任务：
        从数据库重新读取所需数据（判断行、画像、岗位版本、B 的结论等）。
        执行前若该判断行已被替换（superseded_by 非空）或 review.outcome 已不是 'pending'，跳过。
        """
        j_row = conn.execute(
            "SELECT id, user_id, job_id, job_version_id, profile_id, status, verdict, "
            "prompt_version, engine, facts, derivation, verdict_reason, hr_questions, "
            "review, superseded_by FROM judgements WHERE id = ?",
            (judgement_id,),
        ).fetchone()

        if not j_row:
            return

        # 执行前若该判断行已被替换（superseded_by 非空），跳过
        if j_row["superseded_by"] is not None:
            return

        # 执行前若 review.outcome 已不是 'pending'，跳过
        raw_review = j_row["review"]
        if not raw_review:
            return
        try:
            pending_info = json.loads(raw_review)
        except Exception:
            return
        if not isinstance(pending_info, dict) or pending_info.get("outcome") != "pending":
            return

        user_id = j_row["user_id"]
        review_engine = pending_info.get("engine") or self.settings.review_engine
        first_verdict = pending_info.get("first_verdict") or j_row["verdict"]

        profile = _load_profile(conn, j_row["profile_id"])

        if profile is None:
            info = {
                "engine": review_engine,
                "first_verdict": first_verdict,
                "review_verdict": None,
                "outcome": "failed",
                "error": "画像不存在",
            }
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        v_row = conn.execute(
            "SELECT * FROM job_versions WHERE id = ?",
            (j_row["job_version_id"],),
        ).fetchone()

        if not v_row:
            info = {
                "engine": review_engine,
                "first_verdict": first_verdict,
                "review_verdict": None,
                "outcome": "failed",
                "error": "岗位版本不存在",
            }
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        delays = (0.0, 0.0) if self.transport is not None else (0.5, 1.0)
        p_ver = self.settings.prompt_version
        main_engine = j_row["engine"] or self.settings.judge_engine or ""

        # 检查今日额度
        if remaining_today(conn, user_id) <= 0:
            info = {
                "engine": review_engine,
                "first_verdict": first_verdict,
                "review_verdict": None,
                "outcome": "quota_exhausted",
                "error": "今日判断额度已用完（明天 0 点恢复），未复核",
            }
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        try:
            job_version_dict = _job_version_with_company(conn, v_row)
            review = run_llm_judgement(
                conn,
                self.settings,
                user_id=user_id,
                judgement_id=judgement_id,
                profile=profile,
                job_version=job_version_dict,
                transport=self.transport,
                backoff_delays=delays,
                prompt_version=p_ver,
                engine=review_engine,
                truncate_derivation=True,
            )
            if review.status != "done" and review.error and "finish_reason=length" in review.error:
                review = run_llm_judgement(
                    conn,
                    self.settings,
                    user_id=user_id,
                    judgement_id=judgement_id,
                    profile=profile,
                    job_version=job_version_dict,
                    transport=self.transport,
                    backoff_delays=delays,
                    prompt_version=p_ver,
                    engine=review_engine,
                    truncate_derivation=True,
                )
        except Exception as e:
            info = {
                "engine": review_engine,
                "first_verdict": first_verdict,
                "review_verdict": None,
                "outcome": "failed",
                "error": f"复核执行异常: {e}"[:200],
            }
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        info: dict[str, Any] = {
            "engine": review_engine,
            "first_verdict": first_verdict,
            "review_verdict": review.verdict if review.status == "done" else None,
        }

        if review.status == "quota_exhausted":
            info.update(outcome="quota_exhausted", error="今日判断额度已用完（明天 0 点恢复），未复核")
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        if review.status != "done" or review.verdict not in self._VERDICT_RANK:
            info.update(outcome="failed", error=(review.error or "复核失败")[:200])
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET review = ? WHERE id = ?",
                    (json.dumps(info, ensure_ascii=False), judgement_id),
                )
            return

        if self._VERDICT_RANK[review.verdict] > self._VERDICT_RANK[first_verdict]:
            note = (
                f"复核（开启思考）认为应降档：由「{self._VERDICT_CN[first_verdict]}」"
                f"降为「{self._VERDICT_CN[review.verdict]}」"
            )
            derivation = [note] + list(review.derivation or [])
            derivation = derivation[:5]
            engine = f"{main_engine}+review:{review_engine}"
            info.update(outcome="downgraded")
            now_str = utc_now()
            review_ver = get_prompt_version(review.prompt_version) if review.prompt_version else None
            hr_q_json = (
                json.dumps(review.hr_questions, ensure_ascii=False)
                if (review_ver and review_ver.has_hr_questions and review.hr_questions)
                else None
            )
            with transaction(conn, immediate=True):
                conn.execute(
                    "UPDATE judgements SET verdict = ?, prompt_version = ?, engine = ?, "
                    "facts = ?, derivation = ?, verdict_reason = ?, hr_questions = ?, "
                    "review = ?, finished_at = ? WHERE id = ?",
                    (
                        review.verdict,
                        review.prompt_version,
                        engine,
                        json.dumps(review.facts, ensure_ascii=False) if review.facts else None,
                        json.dumps(derivation, ensure_ascii=False),
                        review.verdict_reason if (review_ver and review_ver.has_verdict_reason) else None,
                        hr_q_json,
                        json.dumps(info, ensure_ascii=False),
                        now_str,
                        judgement_id,
                    ),
                )
            return

        info.update(outcome="kept")
        with transaction(conn, immediate=True):
            conn.execute(
                "UPDATE judgements SET review = ? WHERE id = ?",
                (json.dumps(info, ensure_ascii=False), judgement_id),
            )
