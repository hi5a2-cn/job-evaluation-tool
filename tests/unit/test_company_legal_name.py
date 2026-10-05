from pathlib import Path
import sqlite3
from fastapi.testclient import TestClient

from jet.api.routes import ObservationJob
from jet.db.store import open_db, utc_now
from jet.domain.jobs import ingest
from jet.domain.judgements import staleness, to_api
from jet.domain.profiles import save_profile


def test_observation_job_model_company_legal_name_validation_and_truncation() -> None:
    """测试 ObservationJob 模型中 company_legal_name 字段的校验与超长截断（可选、最长 100 字，超长截断）。"""
    # 1. 允许 None 或缺省
    job1 = ObservationJob(platform_job_id="legal_job_01", title="Python开发", city="深圳")
    assert job1.company_legal_name is None

    # 2. 正常字符串 <= 100 字符
    normal_name = "北京某某网络技术研发中心（有限合伙）"
    job2 = ObservationJob(
        platform_job_id="legal_job_02",
        title="Python开发",
        city="深圳",
        company_legal_name=normal_name,
    )
    assert job2.company_legal_name == normal_name

    # 3. 恰好 100 字符
    name_100 = "公" * 100
    job3 = ObservationJob(
        platform_job_id="legal_job_03",
        title="Python开发",
        city="深圳",
        company_legal_name=name_100,
    )
    assert job3.company_legal_name == name_100

    # 4. 超过 100 字符自动截断为 100 字符（不抛出 ValidationError）
    name_120 = "公" * 120
    job4 = ObservationJob(
        platform_job_id="legal_job_04",
        title="Python开发",
        city="深圳",
        company_legal_name=name_120,
    )
    assert len(job4.company_legal_name) == 100
    assert job4.company_legal_name == "公" * 100


def test_new_job_writes_company_legal_name(data_dir: Path) -> None:
    """
    新岗位写入工商名（FR-014）：
    新岗位在入库时，若 company_legal_name 有值且 company_name 为空，
    直接作为 company_name 写入 jobs 表。
    """
    conn = open_db(data_dir)
    now_str = utc_now()
    pid = "new_job_with_legal_name"

    # 新岗位入库（独立职位页，无 company_name，有 company_legal_name）
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "后端架构师",
                "salary_raw": "30-50K",
                "city": "深圳",
                "description": "负责高并发分布式架构设计。",
                "company_name": None,
                "company_legal_name": "深圳市腾讯计算机系统有限公司",
            }
        ],
        observed_at=now_str,
    )

    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row is not None
    assert job_row["company_name"] == "深圳市腾讯计算机系统有限公司"

    # 验证 ingest 层对超长 company_legal_name 的截断
    pid_long = "new_job_with_oversized_legal_name"
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid_long,
                "title": "算法工程师",
                "salary_raw": "25-35K",
                "city": "北京",
                "description": "负责大模型训练与推理优化。",
                "company_name": None,
                "company_legal_name": "长" * 150,
            }
        ],
        observed_at=now_str,
    )

    job_long_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid_long,)).fetchone()
    assert job_long_row is not None
    assert len(job_long_row["company_name"]) == 100
    assert job_long_row["company_name"] == "长" * 100

    conn.close()


def test_existing_job_brand_name_not_overwritten(data_dir: Path) -> None:
    """
    已有品牌名不被覆盖（FR-014，research R7）：
    若岗位已有 company_name（如列表页入库的品牌简称），打开独立职位页读取到 company_legal_name 时，
    company_name 不被工商登记全称覆盖。
    """
    conn = open_db(data_dir)
    now_str = utc_now()
    pid = "job_brand_name_preserve"

    # 1. 列表页入库，公司名为品牌简称
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "Python工程师",
                "salary_raw": "15-25K",
                "city": "深圳",
                "company_name": "腾讯",
                "company_industry": "互联网",
            }
        ],
        observed_at=now_str,
    )

    job_row_1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_1["company_name"] == "腾讯"
    assert job_row_1["company_industry"] == "互联网"

    # 2. 独立职位页读取入库：带工商登记全称，不传 company_name
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "Python工程师",
                "salary_raw": "15-25K",
                "city": "深圳",
                "description": "负责核心业务后台系统研发。",
                "company_name": None,
                "company_legal_name": "深圳市腾讯计算机系统有限公司",
                "company_industry": None,
            }
        ],
        observed_at=now_str,
    )

    # 3. 验证公司名未被覆盖，仍为品牌简称「腾讯」；行业沿用库里已有值
    job_row_2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_2["company_name"] == "腾讯"
    assert job_row_2["company_industry"] == "互联网"

    conn.close()


def test_existing_job_without_company_name_written(data_dir: Path) -> None:
    """
    已有岗位无公司名时写入（FR-014）：
    若库里岗位原本没有公司名（company_name 为空），打开独立职位页时记为页面上的工商登记全称；
    写入后若再次读取另一工商名，不重复覆盖。
    """
    conn = open_db(data_dir)
    now_str = utc_now()
    pid = "job_no_initial_company_name"

    # 1. 首次入库无公司名
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "测试开发工程师",
                "salary_raw": "12-18K",
                "city": "杭州",
                "company_name": None,
            }
        ],
        observed_at=now_str,
    )

    job_row_1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_1["company_name"] is None

    # 2. 第二次从独立职位页入库：带有 company_legal_name
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "测试开发工程师",
                "salary_raw": "12-18K",
                "city": "杭州",
                "description": "负责自动化测试框架与平台开发。",
                "company_name": None,
                "company_legal_name": "杭州某某网络科技有限公司",
            }
        ],
        observed_at=now_str,
    )

    job_row_2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_2["company_name"] == "杭州某某网络科技有限公司"

    # 3. 第三次入库：传入另一个 company_legal_name，由于此时 company_name 已非空，不予覆盖
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid,
                "title": "测试开发工程师",
                "salary_raw": "12-18K",
                "city": "杭州",
                "description": "负责自动化测试框架与平台开发。",
                "company_name": None,
                "company_legal_name": "杭州另一家科技有限公司",
            }
        ],
        observed_at=now_str,
    )

    job_row_3 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_3["company_name"] == "杭州某某网络科技有限公司"

    conn.close()


def test_company_name_and_legal_name_coexistence(data_dir: Path) -> None:
    """
    与 company_name 同时出现时的行为：
    1. 新岗位同时传入两者：company_name 优先作为品牌名写入；
    2. 已有无公司名岗位同时传入两者：company_name 优先写入；
    3. 已有公司名岗位传入新 company_name 与 legal_name：现有"非空即覆盖"规则生效，更新为新 company_name；
    4. 岗位 company_name 原先由 legal_name 填入，后续收到非空 company_name（品牌名）时，更新为品牌名。
    """
    conn = open_db(data_dir)
    now_str = utc_now()

    # 1. 新岗位同时传入两者
    pid1 = "coexist_job_new"
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid1,
                "title": "前端工程师",
                "salary_raw": "15-20K",
                "city": "上海",
                "description": "Vue3/React 开发。",
                "company_name": "字节跳动",
                "company_legal_name": "北京抖音信息服务有限公司",
            }
        ],
        observed_at=now_str,
    )
    job1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid1,)).fetchone()
    assert job1["company_name"] == "字节跳动"

    # 2. 已有无公司名岗位，同时传入两者
    pid2 = "coexist_job_existing_empty"
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[{"platform_job_id": pid2, "title": "运维工程师", "city": "北京", "company_name": None}],
        observed_at=now_str,
    )
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid2,
                "title": "运维工程师",
                "salary_raw": "10-15K",
                "city": "北京",
                "description": "K8s 运维部署。",
                "company_name": "美团",
                "company_legal_name": "北京三快在线科技有限公司",
            }
        ],
        observed_at=now_str,
    )
    job2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid2,)).fetchone()
    assert job2["company_name"] == "美团"

    # 3. 已有公司名岗位传入新的 company_name 与 legal_name：company_name 非空即覆盖
    pid3 = "coexist_job_overwrite_brand"
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[{"platform_job_id": pid3, "title": "产品经理", "city": "深圳", "company_name": "旧品牌"}],
        observed_at=now_str,
    )
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[
            {
                "platform_job_id": pid3,
                "title": "产品经理",
                "city": "深圳",
                "company_name": "新品牌",
                "company_legal_name": "新品牌工商全称有限公司",
            }
        ],
        observed_at=now_str,
    )
    job3 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid3,)).fetchone()
    assert job3["company_name"] == "新品牌"

    # 4. 原先由 legal_name 写入 company_name 的岗位，之后收到列表页的品牌名更新
    pid4 = "coexist_job_legal_then_brand"
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[
            {
                "platform_job_id": pid4,
                "title": "Go开发",
                "salary_raw": "20-30K",
                "city": "广州",
                "description": "微服务后台开发。",
                "company_name": None,
                "company_legal_name": "广州网易计算机系统有限公司",
            }
        ],
        observed_at=now_str,
    )
    job4_step1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid4,)).fetchone()
    assert job4_step1["company_name"] == "广州网易计算机系统有限公司"

    # 列表页观测到品牌名「网易」
    ingest(
        conn,
        user_id="me",
        page_type="list",
        jobs=[{"platform_job_id": pid4, "title": "Go开发", "salary_raw": "20-30K", "city": "广州", "company_name": "网易"}],
        observed_at=now_str,
    )
    job4_step2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid4,)).fetchone()
    assert job4_step2["company_name"] == "网易"

    conn.close()


def test_company_legal_name_does_not_bump_version_or_change_hash(data_dir: Path) -> None:
    """
    岗位版本号与内容指纹不因 company_legal_name 发生变化：
    company_legal_name 只写入 jobs 表的 company_name，
    不属于 job_versions 字段，不参与 content_hash 计算，
    不增加 job_versions 行数，已有判断的 job_changed 保持 False。
    """
    conn = open_db(data_dir)
    now_str = utc_now()

    # 创建用户画像与初始判断
    save_profile(conn, "me", {"directions": ["Python"], "cities": ["深圳"]})
    prof = conn.execute("SELECT id FROM profiles WHERE user_id = 'me'").fetchone()

    pid = "version_hash_stability_job"
    base_payload = {
        "platform_job_id": pid,
        "title": "Python全栈工程师",
        "salary_raw": "20-30K",
        "city": "深圳",
        "district": "南山区",
        "description": "负责系统研发与维护。",
    }

    # 1. 首次入库：无 company_name 与 company_legal_name
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[dict(base_payload, company_name=None, company_legal_name=None)],
        observed_at=now_str,
    )

    job_row_1 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    job_id = job_row_1["id"]
    version_id_1 = job_row_1["current_version_id"]
    assert job_row_1["company_name"] is None

    v_row_1 = conn.execute("SELECT * FROM job_versions WHERE id = ?", (version_id_1,)).fetchone()
    assert v_row_1["version_no"] == 1
    content_hash_1 = v_row_1["content_hash"]

    # 对该版本打一个判断
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, verdict, source, "
        "reasons, prompt_version, engine, rule_result, created_at, finished_at) "
        "VALUES (9001, 'me', ?, ?, ?, 'done', 'apply', 'llm', '[\"匹配\"]', 'v5', 'deepseek', '{}', ?, ?)",
        (job_id, version_id_1, prof["id"], now_str, now_str),
    )
    j_row_1 = conn.execute("SELECT * FROM judgements WHERE id = 9001").fetchone()
    stale_1 = staleness(conn, j_row_1)
    assert stale_1["job_changed"] is False

    # 2. 独立职位页再次入库：带有 company_legal_name，职位正文完全一致
    ingest(
        conn,
        user_id="me",
        page_type="detail",
        jobs=[dict(base_payload, company_name=None, company_legal_name="深圳市前海微众银行股份有限公司")],
        observed_at=now_str,
    )

    job_row_2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert job_row_2["company_name"] == "深圳市前海微众银行股份有限公司"
    # 版本依然是原来的版本 ID
    assert job_row_2["current_version_id"] == version_id_1

    # job_versions 表依然只有一条记录
    versions = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_id,)).fetchall()
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1
    assert versions[0]["content_hash"] == content_hash_1

    # 已有判断不会因为写入工商名而变成 job_changed
    stale_2 = staleness(conn, j_row_1)
    assert stale_2["job_changed"] is False
    assert to_api(conn, j_row_1)["stale"]["job_changed"] is False

    conn.close()


def test_observations_api_company_legal_name_integration(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """测试通过 POST /v1/observations 接口提交 company_legal_name 的端到端流程与截断。"""
    client, headers = llm_client
    conn: sqlite3.Connection = client.app.state.conn

    # 1. 独立职位页观测：仅传 company_legal_name
    pid = "api_legal_name_job_001"
    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-29T12:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "Golang研发专家",
                "salary_raw": "35-50K",
                "city": "深圳",
                "description": "基础架构与存储引擎研发。",
                "company_legal_name": "深圳市腾讯计算机系统有限公司",
            }
        ],
    }

    resp = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp.status_code == 200
    job_entry = resp.json()["jobs"][pid]
    assert job_entry["company_name"] == "深圳市腾讯计算机系统有限公司"

    db_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    assert db_row["company_name"] == "深圳市腾讯计算机系统有限公司"

    # 2. 超长 120 字工商登记名：接口应正常接收 200 并截断至 100 字
    pid_overflow = "api_legal_name_job_overflow"
    overflow_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-29T12:05:00Z",
        "jobs": [
            {
                "platform_job_id": pid_overflow,
                "title": "架构师",
                "salary_raw": "40-60K",
                "city": "北京",
                "description": "负责云原生基础设施开发。",
                "company_legal_name": "超长公司名" * 30,  # 150 字
            }
        ],
    }
    resp_overflow = client.post("/v1/observations", json=overflow_payload, headers=headers)
    assert resp_overflow.status_code == 200
    overflow_entry = resp_overflow.json()["jobs"][pid_overflow]
    assert len(overflow_entry["company_name"]) == 100

    # 3. 已有品牌名岗位接收独立职位页观测：company_name 不被覆盖
    pid_brand = "api_legal_name_brand_job"
    # 先列表页观测入库品牌名「美团」
    client.post(
        "/v1/observations",
        json={
            "page_type": "list",
            "observed_at": "2026-09-29T12:10:00Z",
            "jobs": [
                {
                    "platform_job_id": pid_brand,
                    "title": "前端专家",
                    "salary_raw": "25-35K",
                    "city": "北京",
                    "company_name": "美团",
                }
            ],
        },
        headers=headers,
    )
    # 再独立职位页观测：带有工商名
    resp_detail_brand = client.post(
        "/v1/observations",
        json={
            "page_type": "detail",
            "observed_at": "2026-09-29T12:15:00Z",
            "jobs": [
                {
                    "platform_job_id": pid_brand,
                    "title": "前端专家",
                    "salary_raw": "25-35K",
                    "city": "北京",
                    "description": "美团外卖核心前端架构演进。",
                    "company_legal_name": "北京三快在线科技有限公司",
                }
            ],
        },
        headers=headers,
    )
    assert resp_detail_brand.status_code == 200
    assert resp_detail_brand.json()["jobs"][pid_brand]["company_name"] == "美团"
