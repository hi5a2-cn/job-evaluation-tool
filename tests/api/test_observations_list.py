import sqlite3
from fastapi.testclient import TestClient
import pytest

from tests.conftest import FakeLlmHelper
from tests.fixtures.boss.observations import to_list_observation


def test_observations_list_ingest_and_lifecycle(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """
    T080 / T084: 验证列表页岗位入库全生命周期：
    - 首次入库 15 个岗位、15 条 views (page_type='list')、全 list_only、0 条判断、0 次大模型调用；
    - 列表条目包含 company_name（由 search_list.json 中的 brandName 提供）；
    - 再发一次仍 15 个岗位无重复，views 变为 30 条；
    - 某个 list_only 岗位收到 detail 后升级为 full 并进入判断（大模型调用 1 次）；
    - full 岗位再从列表读到且职位名 / 薪资 / 城市未变时，不降级、不新增版本；
    - 列表响应中已判断岗位返回现行判断（含 stale），未判断岗位返回 null；
    - page_type=list 时路由绝不调用判断及大模型。
    """
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    # 1. 预先设置用户画像，确保后续 detail 能够触发判断，并验证即使有画像，list 请求也不会触发判断
    prof_resp = client.put(
        "/v1/profile",
        json={
            "directions": ["Python后端开发"],
            "keywords": ["Python", "FastAPI"],
            "cities": ["深圳", "广州", "北京", "上海", "杭州", "成都", "武汉", "南京"],
            "preferred_cities": ["深圳"],
            "min_monthly_k": 10.0,
        },
        headers=headers,
    )
    assert prof_resp.status_code == 200

    # 2. 首次发送列表观测（由 search_list.json 转换而得）
    list_payload = to_list_observation(observed_at="2026-09-24T12:00:00Z")
    assert len(list_payload["jobs"]) == 15

    resp1 = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    jobs1 = data1["jobs"]
    assert len(jobs1) == 15

    # 验证响应中每个岗位字段与状态
    for job in list_payload["jobs"]:
        pid = job["platform_job_id"]
        assert pid in jobs1
        entry = jobs1[pid]
        assert entry["completeness"] == "list_only"
        assert entry["judgement"] is None
        assert entry["company_name"] == job["company_name"]
        assert entry["company_name"] is not None

    # 数据库核对：15 个 jobs、全部 list_only、15 条 views、0 条判断、0 次大模型调用
    job_rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    assert len(job_rows) == 15
    for r in job_rows:
        assert r["completeness"] == "list_only"

    view_rows = conn.execute("SELECT * FROM views WHERE page_type = 'list'").fetchall()
    assert len(view_rows) == 15

    assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 0
    assert fake_llm.call_count == 0

    # 3. 再发一次仍 15 个岗位无重复（views 变 30 条）
    list_payload_2 = to_list_observation(observed_at="2026-09-24T12:01:00Z")
    resp2 = client.post("/v1/observations", json=list_payload_2, headers=headers)
    assert resp2.status_code == 200

    job_rows_2 = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    assert len(job_rows_2) == 15

    view_rows_2 = conn.execute("SELECT * FROM views WHERE page_type = 'list'").fetchall()
    assert len(view_rows_2) == 30
    assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 0
    assert fake_llm.call_count == 0

    # 4. 某个 list_only 岗位收到 detail → 升级为 full 并进入判断
    target_pid = "mock_job_001"
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:05:00Z",
        "jobs": [
            {
                "platform_job_id": target_pid,
                "title": "Python后端开发工程师",
                "company_name": "虚构科技A",
                "salary_raw": "15-25K·14薪",
                "city": "深圳",
                "district": "南山区",
                "description": "负责后台核心业务研发，精通 Python 和常用 Web 框架，有微服务及高并发经验。",
            }
        ],
    }
    fake_llm.set_fit(reasons=["技术栈匹配", "城市薪资符合画像要求"])
    resp_detail = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp_detail.status_code == 200
    assert resp_detail.json()["jobs"][target_pid]["completeness"] == "full"

    # 等待后台判断完成
    worker = getattr(app.state, "worker", None)
    if worker:
        assert worker.wait_idle(timeout=5.0)

    assert fake_llm.call_count == 1
    target_job_row = conn.execute(
        "SELECT * FROM jobs WHERE platform_job_id = ?", (target_pid,)
    ).fetchone()
    assert target_job_row["completeness"] == "full"

    target_versions = conn.execute(
        "SELECT * FROM job_versions WHERE job_id = ? ORDER BY version_no",
        (target_job_row["id"],),
    ).fetchall()
    assert len(target_versions) == 2
    assert target_versions[0]["source"] == "list"
    assert target_versions[1]["source"] == "detail"

    # 5. full 岗位再从列表读到且职位名 / 薪资 / 城市未变 → 不降级、不新增版本
    list_payload_3 = to_list_observation(observed_at="2026-09-24T12:10:00Z")
    resp3 = client.post("/v1/observations", json=list_payload_3, headers=headers)
    assert resp3.status_code == 200
    jobs3 = resp3.json()["jobs"]
    assert len(jobs3) == 15

    # 检查 target_pid: 依然为 full，版本号与版本记录数未变
    target_job_after = conn.execute(
        "SELECT * FROM jobs WHERE platform_job_id = ?", (target_pid,)
    ).fetchone()
    assert target_job_after["completeness"] == "full"
    assert target_job_after["current_version_id"] == target_job_row["current_version_id"]

    version_count_after = conn.execute(
        "SELECT COUNT(*) FROM job_versions WHERE job_id = ?",
        (target_job_row["id"],),
    ).fetchone()[0]
    assert version_count_after == 2

    # 列表响应中已判断岗位返回其现行判断（含 stale 结构），未判断岗位返回 null
    target_entry = jobs3[target_pid]
    assert target_entry["completeness"] == "full"
    assert target_entry["company_name"] == "虚构科技A"
    assert target_entry["judgement"] is not None
    assert target_entry["judgement"]["verdict"] in ("apply", "fit")
    assert "stale" in target_entry["judgement"]
    assert isinstance(target_entry["judgement"]["stale"], dict)
    assert target_entry["judgement"]["stale"]["job_changed"] is False
    assert target_entry["judgement"]["stale"]["profile_changed"] is False
    assert target_entry["judgement"]["stale"]["method_changed"] is False

    for pid, entry in jobs3.items():
        if pid != target_pid:
            assert entry["completeness"] == "list_only"
            assert entry["judgement"] is None
            assert entry["company_name"] is not None

    # views 变为 45 条 (15 + 15 + 15)，LLM 仍保持为 1 次调用
    total_views = conn.execute("SELECT COUNT(*) FROM views WHERE page_type = 'list'").fetchone()[0]
    assert total_views == 45
    assert fake_llm.call_count == 1


def test_observations_list_never_triggers_judgement(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """
    T084: 显式核对 page_type = list 时不调用 request_judgement，不产生任何排队或判断。
    """
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    # 即使画像和配额完备
    prof_resp = client.put(
        "/v1/profile",
        json={"directions": ["Python后端开发"], "cities": ["深圳"]},
        headers=headers,
    )
    assert prof_resp.status_code == 200

    payload = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": "test_list_job_999",
                "title": "Python高级开发",
                "company_name": "某知名科技",
                "salary_raw": "25-40K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }

    resp = client.post("/v1/observations", json=payload, headers=headers)
    assert resp.status_code == 200

    # 确认没有创建任何判断记录，且未调用 LLM
    assert conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0] == 0
    assert fake_llm.call_count == 0


def test_list_after_detail_title_change_keeps_detail_version(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证已是 full 的岗位在列表页职位名改变时不新建版本、保留详情版本（Issue #6）。"""
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    pid = "job_test_t1"
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python后端开发工程师",
                "company_name": "测试科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
                "description": "精通 Python 与 FastAPI，熟悉高并发架构设计。",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp1.status_code == 200

    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["completeness"] == "full"
    orig_version_id = job_row["current_version_id"]

    second_observed_at = "2026-09-24T12:10:00Z"
    list_payload = {
        "page_type": "list",
        "observed_at": second_observed_at,
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python后端开发工程师-五险一金",
                "company_name": "测试科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp2.status_code == 200

    job_after = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    versions_after = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row["id"],)).fetchall()

    # 断言 job_versions 数量不变、jobs.current_version_id 不变、当前版本 source=='detail'、jobs.last_seen_at 更新为第二次的 observed_at
    assert len(versions_after) == 1
    assert job_after["current_version_id"] == orig_version_id
    current_v = conn.execute("SELECT * FROM job_versions WHERE id = ?", (job_after["current_version_id"],)).fetchone()
    assert current_v["source"] == "detail"
    assert job_after["last_seen_at"] == second_observed_at


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("salary_raw", "25-35K"),
        ("city", "广州"),
    ],
)
def test_list_after_detail_salary_or_city_change_keeps_detail_version(
    llm_client: tuple[TestClient, dict[str, str]],
    changed_field: str,
    changed_value: str,
) -> None:
    """验证已是 full 的岗位在列表页薪资或城市改变时不新建版本、保留详情版本（Issue #6）。"""
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    pid = f"job_test_t2_{changed_field}"
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python后端开发工程师",
                "company_name": "测试科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
                "description": "精通 Python 与 FastAPI，熟悉高并发架构设计。",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp1.status_code == 200

    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["completeness"] == "full"
    orig_version_id = job_row["current_version_id"]

    second_observed_at = "2026-09-24T12:15:00Z"
    list_job = {
        "platform_job_id": pid,
        "title": "Python后端开发工程师",
        "company_name": "测试科技",
        "salary_raw": "20-30K",
        "city": "深圳",
        "district": "南山区",
    }
    list_job[changed_field] = changed_value
    list_payload = {
        "page_type": "list",
        "observed_at": second_observed_at,
        "jobs": [list_job],
    }
    resp2 = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp2.status_code == 200

    job_after = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    versions_after = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row["id"],)).fetchall()

    # 断言 job_versions 数量不变、jobs.current_version_id 不变、当前版本 source=='detail'、jobs.last_seen_at 更新为第二次的 observed_at
    assert len(versions_after) == 1
    assert job_after["current_version_id"] == orig_version_id
    current_v = conn.execute("SELECT * FROM job_versions WHERE id = ?", (job_after["current_version_id"],)).fetchone()
    assert current_v["source"] == "detail"
    assert job_after["last_seen_at"] == second_observed_at


def test_list_only_job_list_change_still_creates_version(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证仅 list 入库的岗位在列表页职位名改变时仍新建 list 版本并更新当前版本（Issue #6）。"""
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    pid = "job_test_t3_list_only"
    obs1 = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "初级Python开发",
                "company_name": "初创科技",
                "salary_raw": "10-15K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp1 = client.post("/v1/observations", json=obs1, headers=headers)
    assert resp1.status_code == 200

    job1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job1["completeness"] == "list_only"
    v1_id = job1["current_version_id"]
    versions1 = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job1["id"],)).fetchall()
    assert len(versions1) == 1
    assert versions1[0]["source"] == "list"

    obs2 = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:10:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "中级Python开发",
                "company_name": "初创科技",
                "salary_raw": "10-15K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=obs2, headers=headers)
    assert resp2.status_code == 200

    job2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    versions2 = conn.execute("SELECT * FROM job_versions WHERE job_id = ? ORDER BY version_no", (job1["id"],)).fetchall()

    # 断言新建 source=='list' 的版本、current_version_id 指向新版本（保留原行为）
    assert len(versions2) == 2
    assert job2["current_version_id"] != v1_id
    assert job2["current_version_id"] == versions2[1]["id"]
    assert versions2[1]["source"] == "list"
    assert versions2[1]["title"] == "中级Python开发"
    assert job2["last_seen_at"] == "2026-09-24T12:10:00Z"


def test_list_after_detail_does_not_make_judgement_stale(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """验证岗位详情入库并生成判断后，列表页职位名改变不会使已有判断标为过时（Issue #6）。"""
    client, headers = llm_client
    app = client.app

    # 1. 预设用户画像以支持判断生成
    prof_resp = client.put(
        "/v1/profile",
        json={"directions": ["Python后端开发"], "cities": ["深圳"], "min_monthly_k": 10.0},
        headers=headers,
    )
    assert prof_resp.status_code == 200

    # 2. 岗位 detail 入库并触发判断
    pid = "job_test_t4"
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python后端开发工程师",
                "company_name": "测试科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
                "description": "负责后台核心业务研发，精通 Python 和 FastAPI。",
            }
        ],
    }
    fake_llm.set_fit(reasons=["技术栈匹配"])
    resp1 = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp1.status_code == 200

    worker = getattr(app.state, "worker", None)
    if worker:
        assert worker.wait_idle(timeout=5.0)

    # 确认初始判断生成且非 stale
    resp_j1 = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    assert resp_j1.status_code == 200
    j1 = resp_j1.json()["jobs"][pid]["judgement"]
    assert j1 is not None
    assert j1["stale"]["job_changed"] is False

    # 3. 之后 list 入库职位名不同
    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-24T12:10:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Python后端开发工程师-五险一金",
                "company_name": "测试科技",
                "salary_raw": "20-30K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp2.status_code == 200

    # 4. 断言 staleness 的 job_changed 为 False
    list_entry = resp2.json()["jobs"][pid]
    assert list_entry["judgement"] is not None
    assert list_entry["judgement"]["stale"]["job_changed"] is False

    resp_j2 = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    j2 = resp_j2.json()["jobs"][pid]["judgement"]
    assert j2 is not None
    assert j2["stale"]["job_changed"] is False


def test_detail_list_detail_same_content_no_new_version_no_rejudge(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """验证详情→列表改名→相同详情入库时版本数始终为 1、判断记录数不增加、大模型 0 增量调用（Issue #6）。"""
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    # 预设用户画像
    client.put(
        "/v1/profile",
        json={"directions": ["Python后端开发"], "cities": ["深圳"], "min_monthly_k": 10.0},
        headers=headers,
    )

    pid = "job_test_t5"
    content_x = {
        "platform_job_id": pid,
        "title": "Python高级开发工程师",
        "company_name": "创新科技",
        "salary_raw": "25-35K",
        "city": "深圳",
        "district": "南山区",
        "description": "精通 Python，熟练掌握 FastAPI 与分布式系统，有高并发项目经验。",
    }

    # 1. detail 入库（内容 X）
    fake_llm.set_fit(reasons=["技术栈匹配"])
    resp1 = client.post(
        "/v1/observations",
        json={"page_type": "detail", "observed_at": "2026-09-24T12:00:00Z", "jobs": [content_x]},
        headers=headers,
    )
    assert resp1.status_code == 200

    worker = getattr(app.state, "worker", None)
    if worker:
        assert worker.wait_idle(timeout=5.0)

    job_row = conn.execute("SELECT id FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    job_id = job_row["id"]

    version_count_1 = conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()[0]
    judgement_count_1 = conn.execute("SELECT COUNT(*) FROM judgements WHERE job_id = ?", (job_id,)).fetchone()[0]
    llm_calls_1 = fake_llm.call_count

    assert version_count_1 == 1
    assert judgement_count_1 == 1
    assert llm_calls_1 == 1

    # 2. list 入库（职位名不同）
    content_list = {
        "platform_job_id": pid,
        "title": "Python高级开发工程师-包吃住",
        "company_name": "创新科技",
        "salary_raw": "25-35K",
        "city": "深圳",
        "district": "南山区",
    }
    resp2 = client.post(
        "/v1/observations",
        json={"page_type": "list", "observed_at": "2026-09-24T12:05:00Z", "jobs": [content_list]},
        headers=headers,
    )
    assert resp2.status_code == 200
    if worker:
        assert worker.wait_idle(timeout=5.0)

    version_count_2 = conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert version_count_2 == 1

    # 3. 再次 detail 入库（内容与 X 完全相同）
    resp3 = client.post(
        "/v1/observations",
        json={"page_type": "detail", "observed_at": "2026-09-24T12:10:00Z", "jobs": [content_x]},
        headers=headers,
    )
    assert resp3.status_code == 200
    if worker:
        assert worker.wait_idle(timeout=5.0)

    # 4. 断言：版本数始终为 1、判断记录数不增加、大模型调用次数为 0 增量
    version_count_final = conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()[0]
    judgement_count_final = conn.execute("SELECT COUNT(*) FROM judgements WHERE job_id = ?", (job_id,)).fetchone()[0]
    llm_calls_incremental = fake_llm.call_count - llm_calls_1

    assert version_count_final == 1
    assert judgement_count_final == judgement_count_1
    assert llm_calls_incremental == 0


def test_observations_list_backfills_version_for_versionless_job(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """
    T003: 覆盖先有无版本岗位行（current_version_id 为 NULL）→ 列表观测补录第 1 版。
    - 数据库中预置无版本的 jobs 行（completeness='list_only', current_version_id=NULL）；
    - 发送列表观测请求；
    - 验证创建 version_no=1、source='list' 的 job_versions 记录，回填 jobs.current_version_id；
    - jobs.completeness 保持 'list_only'；
    - 不触发判断，响应中 judgement 为 None；
    - 后续再收到详情观测时，升级为 version_no=2、source='detail'、completeness='full'。
    """
    client, headers = llm_client
    app = client.app
    conn: sqlite3.Connection = app.state.conn

    # 1. 预置无版本的岗位行（模拟聊天入库的岗位）
    pid = "chat_orig_list_001"
    now_str = "2026-09-24T10:00:00Z"
    with conn:
        conn.execute(
            "INSERT INTO jobs (platform, platform_job_id, completeness, current_version_id, company_name, first_seen_at, last_seen_at) "
            "VALUES ('boss', ?, 'list_only', NULL, '某某科技', ?, ?)",
            (pid, now_str, now_str),
        )
        job_id = conn.execute("SELECT id FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO job_chat_seen (user_id, job_id, chat_title, first_seen_at, last_seen_at) "
            "VALUES ('me', ?, '数据分析师', ?, ?)",
            (job_id, now_str, now_str),
        )

    # 2. 发送包含该岗位的列表观测
    obs_time = "2026-09-24T12:00:00Z"
    list_payload = {
        "page_type": "list",
        "observed_at": obs_time,
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "数据分析师",
                "company_name": "某某科技",
                "salary_raw": "15-25K",
                "city": "深圳",
                "district": "南山区",
            }
        ],
    }
    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert pid in data["jobs"]
    entry = data["jobs"][pid]
    assert entry["completeness"] == "list_only"
    assert entry["judgement"] is None

    # 3. 验证数据库状态：首个版本补录成功
    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row["completeness"] == "list_only"
    assert job_row["current_version_id"] is not None
    assert job_row["last_seen_at"] == obs_time

    version_rows = conn.execute("SELECT * FROM job_versions WHERE job_id = ? ORDER BY version_no", (job_id,)).fetchall()
    assert len(version_rows) == 1
    v1 = version_rows[0]
    assert v1["id"] == job_row["current_version_id"]
    assert v1["version_no"] == 1
    assert v1["source"] == "list"
    assert v1["title"] == "数据分析师"
    assert v1["salary_raw"] == "15-25K"
    assert v1["city"] == "深圳"
    assert v1["district"] == "南山区"
    assert v1["description"] is None

    # 4. 验证 views 记录与大模型调用为 0
    views = conn.execute("SELECT * FROM views WHERE job_id = ?", (job_id,)).fetchall()
    assert len(views) == 1
    assert views[0]["page_type"] == "list"
    assert fake_llm.call_count == 0
    assert conn.execute("SELECT COUNT(*) FROM judgements WHERE job_id = ?", (job_id,)).fetchone()[0] == 0

    # 5. 后续该岗位收到详情观测 → 正常升级为 version_no=2、completeness='full'
    client.put(
        "/v1/profile",
        json={
            "directions": ["数据分析"],
            "keywords": ["SQL", "Python"],
            "cities": ["深圳"],
            "min_monthly_k": 10.0,
        },
        headers=headers,
    )
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-24T12:05:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "数据分析师",
                "company_name": "某某科技",
                "salary_raw": "15-25K",
                "city": "深圳",
                "district": "南山区",
                "description": "负责业务指标监控与用户行为分析，熟练掌握 SQL 与 Python。",
            }
        ],
    }
    resp_detail = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp_detail.status_code == 200
    entry_detail = resp_detail.json()["jobs"][pid]
    assert entry_detail["completeness"] == "full"
    assert entry_detail["judgement"] is not None

    job_row_after = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_after["completeness"] == "full"

    version_rows_after = conn.execute("SELECT * FROM job_versions WHERE job_id = ? ORDER BY version_no", (job_id,)).fetchall()
    assert len(version_rows_after) == 2
    v2 = version_rows_after[1]
    assert v2["id"] == job_row_after["current_version_id"]
    assert v2["version_no"] == 2
    assert v2["source"] == "detail"
    assert v2["description"] == "负责业务指标监控与用户行为分析，熟练掌握 SQL 与 Python。"
