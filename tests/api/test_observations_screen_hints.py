import sqlite3
from fastapi.testclient import TestClient
import pytest

from jet.llm.quota import usage_today
from tests.conftest import FakeLlmHelper


@pytest.fixture(autouse=True)
def setup_strict_industries(llm_client: tuple[TestClient, dict[str, str]]) -> None:
    client, headers = llm_client
    client.put(
        "/v1/strict-industries",
        json={"selected": ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"]},
        headers=headers,
    )


def test_observations_list_screen_hints_all_types_and_order(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """
    测试 POST /v1/observations 在 page_type=list 时返回 screen_hints：
    覆盖四类规则命中、未命中、固定顺序、技能标签与岗位标签匹配。
    """
    client, headers = llm_client

    # 1. 设置画像：最低月薪 12K、不去的城市 北京、不接受条件 销售
    prof_resp = client.put(
        "/v1/profile",
        json={
            "directions": ["Python"],
            "cities": ["深圳", "广州"],
            "preferred_cities": ["深圳"],
            "excluded_cities": ["北京"],
            "min_monthly_k": 12.0,
            "exclude_keywords": ["销售"],
        },
        headers=headers,
    )
    assert prof_resp.status_code == 200

    # 2. 发送列表观测，包含命中不同规则的多个岗位
    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T10:00:00Z",
        "jobs": [
            # Job 1: 仅命中重点排查行业（餐饮）
            {
                "platform_job_id": "hint_job_001",
                "title": "后端开发",
                "company_name": "美食餐饮管理",
                "company_industry": "餐饮",
                "salary_raw": "15-20K",
                "city": "深圳",
            },
            # Job 2: 仅命中薪资底线（8-10K < 12K）
            {
                "platform_job_id": "hint_job_002",
                "title": "Python工程师",
                "company_name": "普普通通科技",
                "company_industry": "互联网",
                "salary_raw": "8-10K",
                "city": "深圳",
            },
            # Job 3: 仅命中不接受条件（职位名无销售，但 skills 含销售）
            {
                "platform_job_id": "hint_job_003",
                "title": "售前技术专家",
                "company_name": "软件服务商",
                "company_industry": "企业服务",
                "salary_raw": "15-25K",
                "city": "深圳",
                "job_labels": ["售前", "技术"],
                "skills": ["解决方案", "电销与销售谈判"],
            },
            # Job 4: 仅命中不去的城市（北京市）
            {
                "platform_job_id": "hint_job_004",
                "title": "Python开发",
                "company_name": "北方云科",
                "company_industry": "计算机软件",
                "salary_raw": "18-25K",
                "city": "北京市",
            },
            # Job 5: 全部四类命中，验证顺序 strict_industry → salary_floor → exclude_keyword → excluded_city
            {
                "platform_job_id": "hint_job_005",
                "title": "餐饮门店电话销售",
                "company_name": "快餐连锁",
                "company_industry": "餐饮",
                "salary_raw": "5-8K",
                "city": "北京",
                "job_labels": ["快消", "拓店"],
                "skills": ["销售", "沟通"],
            },
            # Job 6: 完全合规，未命中任何规则 -> screen_hints 为 []
            {
                "platform_job_id": "hint_job_006",
                "title": "Python高级架构师",
                "company_name": "深圳前海科技",
                "company_industry": "互联网",
                "salary_raw": "25-35K",
                "city": "深圳",
                "job_labels": ["架构", "微服务"],
                "skills": ["FastAPI", "PostgreSQL"],
            },
        ],
    }

    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]

    # Job 1: strict_industry
    assert jobs["hint_job_001"]["screen_hints"] == [
        {"type": "strict_industry", "text": "高风险行业（餐饮）"}
    ]

    # Job 2: salary_floor
    assert jobs["hint_job_002"]["screen_hints"] == [
        {"type": "salary_floor", "text": "低于薪资底线"}
    ]

    # Job 3: exclude_keyword (from skills)
    assert jobs["hint_job_003"]["screen_hints"] == [
        {"type": "exclude_keyword", "text": "命中不接受条件：销售"}
    ]

    # Job 4: excluded_city
    assert jobs["hint_job_004"]["screen_hints"] == [
        {"type": "excluded_city", "text": "不去的城市"}
    ]

    # Job 5: all 4 rules hit in strict order
    assert jobs["hint_job_005"]["screen_hints"] == [
        {"type": "strict_industry", "text": "高风险行业（餐饮）"},
        {"type": "salary_floor", "text": "低于薪资底线"},
        {"type": "exclude_keyword", "text": "命中不接受条件：销售"},
        {"type": "excluded_city", "text": "不去的城市"},
    ]

    # Job 6: clean job -> empty hints
    assert jobs["hint_job_006"]["screen_hints"] == []


def test_observations_list_does_not_affect_db_judgements_llm_or_quota(
    llm_client: tuple[TestClient, dict[str, str]],
    fake_llm: FakeLlmHelper,
) -> None:
    """
    验证列表请求粗筛不写 judgements 表、不调大模型、不写 llm_calls 表、不占额度 (FR-005, SC-002)。
    """
    client, headers = llm_client
    conn: sqlite3.Connection = client.app.state.conn

    # 预设画像
    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 20.0, "exclude_keywords": ["外包"]},
        headers=headers,
    )

    # 记录请求前状态
    judgements_before = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    llm_calls_before = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    llm_mock_before = fake_llm.call_count
    quota_before = usage_today(conn, "me", purpose="judge")["used"]

    # 发送列表观测（3 个岗位，分别命中行业、薪资、关键词）
    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T10:30:00Z",
        "jobs": [
            {
                "platform_job_id": "quota_test_01",
                "title": "外包开发",
                "city": "深圳",
                "salary_raw": "10-15K",
                "company_industry": "餐饮",
            },
            {
                "platform_job_id": "quota_test_02",
                "title": "后端开发",
                "city": "深圳",
                "salary_raw": "8-12K",
            },
        ],
    }
    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]["quota_test_01"]["screen_hints"]) > 0

    # 核对请求后状态：judgements 行数、llm_calls 行数、大模型调用次数、额度使用量均保持不变
    judgements_after = conn.execute("SELECT COUNT(*) FROM judgements").fetchone()[0]
    llm_calls_after = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    llm_mock_after = fake_llm.call_count
    quota_after = usage_today(conn, "me", purpose="judge")["used"]

    assert judgements_after == judgements_before
    assert llm_calls_after == llm_calls_before
    assert llm_mock_after == llm_mock_before
    assert quota_after == quota_before


def test_observations_labels_truncation_and_storage_isolation(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """
    验证 job_labels / skills 超长截断（≤ 20 项，每项 ≤ 30 字）、
    未写入任何表、不影响岗位版本号与内容指纹。
    """
    client, headers = llm_client
    conn: sqlite3.Connection = client.app.state.conn

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "exclude_keywords": ["超长关键词测试"]},
        headers=headers,
    )

    # 构造超过 20 项且每项超过 30 字的标签
    oversized_labels = [f"标签_{i:02d}_" + "字" * 50 for i in range(25)]
    oversized_skills = [f"技能_{i:02d}_" + "字" * 50 for i in range(25)]

    pid = "label_truncate_job"
    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T11:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "算法工程师",
                "city": "深圳",
                "salary_raw": "25-35K",
                "job_labels": oversized_labels,
                "skills": oversized_skills,
            }
        ],
    }

    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200

    # 1. 验证 jobs 与 job_versions 表结构中无 job_labels 与 skills 列
    jobs_cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert "job_labels" not in jobs_cols
    assert "skills" not in jobs_cols

    versions_cols = [r[1] for r in conn.execute("PRAGMA table_info(job_versions)").fetchall()]
    assert "job_labels" not in versions_cols
    assert "skills" not in versions_cols

    # 2. 检查版本信息
    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    v1 = conn.execute("SELECT * FROM job_versions WHERE id = ?", (job_row["current_version_id"],)).fetchone()
    assert v1["version_no"] == 1
    content_hash_1 = v1["content_hash"]

    # 3. 再次发送相同岗位，但 job_labels 和 skills 完全改变
    list_payload_2 = {
        "page_type": "list",
        "observed_at": "2026-09-29T11:05:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "算法工程师",
                "city": "深圳",
                "salary_raw": "25-35K",
                "job_labels": ["全新标签A", "全新标签B"],
                "skills": ["全新技能A"],
            }
        ],
    }
    resp2 = client.post("/v1/observations", json=list_payload_2, headers=headers)
    assert resp2.status_code == 200

    # 4. 岗位版本号不新增、指纹不变
    job_row_2 = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    v2 = conn.execute("SELECT * FROM job_versions WHERE id = ?", (job_row_2["current_version_id"],)).fetchone()
    assert v2["version_no"] == 1
    assert v2["content_hash"] == content_hash_1
    all_versions = conn.execute("SELECT * FROM job_versions WHERE job_id = ?", (job_row_2["id"],)).fetchall()
    assert len(all_versions) == 1


def test_observations_detail_response_has_no_screen_hints(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证详情页观测（page_type=detail）响应中没有 screen_hints 字段。"""
    client, headers = llm_client

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 10.0},
        headers=headers,
    )

    detail_payload = {
        "page_type": "detail",
        "observed_at": "2026-09-29T11:10:00Z",
        "jobs": [
            {
                "platform_job_id": "detail_job_001",
                "title": "Python全栈工程师",
                "company_name": "某知名公司",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "负责服务端架构与业务开发。",
            }
        ],
    }

    resp = client.post("/v1/observations", json=detail_payload, headers=headers)
    assert resp.status_code == 200
    job_entry = resp.json()["jobs"]["detail_job_001"]
    assert "screen_hints" not in job_entry


def test_observations_list_no_profile_only_industry_hints(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证未设置画像时，列表响应只返回重点排查行业提示，不返回薪资、城市或关键词提示。"""
    client, headers = llm_client

    # 此时尚未设置任何 profile
    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T11:15:00Z",
        "jobs": [
            # 行业命中，薪资很低，城市为北京，标题有销售
            {
                "platform_job_id": "noprof_job_01",
                "title": "电话销售专员",
                "company_name": "老字号酒家",
                "company_industry": "餐饮",
                "salary_raw": "3-4K",
                "city": "北京",
            },
            # 行业未命中
            {
                "platform_job_id": "noprof_job_02",
                "title": "电话销售专员",
                "company_name": "科技创新公司",
                "company_industry": "计算机软件",
                "salary_raw": "3-4K",
                "city": "北京",
            },
        ],
    }

    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]

    assert jobs["noprof_job_01"]["screen_hints"] == [
        {"type": "strict_industry", "text": "高风险行业（餐饮）"}
    ]
    assert jobs["noprof_job_02"]["screen_hints"] == []


def test_observations_list_salary_negotiable_or_invisible(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证薪资不可见、面议、无法解析在列表页不产生低于薪资底线提示 (FR-007)。"""
    client, headers = llm_client

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 20.0},
        headers=headers,
    )

    list_payload = {
        "page_type": "list",
        "observed_at": "2026-09-29T11:20:00Z",
        "jobs": [
            {
                "platform_job_id": "salary_neg_01",
                "title": "Python研发",
                "salary_raw": "面议",
                "city": "深圳",
            },
            {
                "platform_job_id": "salary_neg_02",
                "title": "Python研发",
                "salary_raw": None,
                "city": "深圳",
            },
            {
                "platform_job_id": "salary_neg_03",
                "title": "Python研发",
                "salary_raw": "薪资不详",
                "city": "深圳",
            },
        ],
    }

    resp = client.post("/v1/observations", json=list_payload, headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert jobs["salary_neg_01"]["screen_hints"] == []
    assert jobs["salary_neg_02"]["screen_hints"] == []
    assert jobs["salary_neg_03"]["screen_hints"] == []


def test_observations_list_already_judged_job_returns_screen_hints(
    llm_client: tuple[TestClient, dict[str, str]],
) -> None:
    """验证已判断过的岗位在列表页响应中同样返回 screen_hints，由插件端决定是否显示 (FR-004)。"""
    client, headers = llm_client
    conn: sqlite3.Connection = client.app.state.conn

    client.put(
        "/v1/profile",
        json={"directions": ["Python"], "cities": ["深圳"], "min_monthly_k": 10.0, "exclude_keywords": ["外包"]},
        headers=headers,
    )

    pid = "already_judged_job_001"
    # 先通过 detail 观测入库并产生判断（规则排除为 skip）
    client.post(
        "/v1/observations",
        json={
            "page_type": "detail",
            "observed_at": "2026-09-29T11:30:00Z",
            "jobs": [
                {
                    "platform_job_id": pid,
                    "title": "外包开发工程师",
                    "company_name": "外包科技",
                    "company_industry": "餐饮",
                    "salary_raw": "15-20K",
                    "city": "深圳",
                    "description": "外包岗位驻场开发。",
                }
            ],
        },
        headers=headers,
    )

    job_row = conn.execute("SELECT * FROM jobs WHERE platform_job_id = ?", (pid,)).fetchone()
    judgement = conn.execute("SELECT * FROM judgements WHERE job_id = ?", (job_row["id"],)).fetchone()
    assert judgement is not None

    # 然后列表页再次观测该岗位
    list_resp = client.post(
        "/v1/observations",
        json={
            "page_type": "list",
            "observed_at": "2026-09-29T11:35:00Z",
            "jobs": [
                {
                    "platform_job_id": pid,
                    "title": "外包开发工程师",
                    "company_name": "外包科技",
                    "company_industry": "餐饮",
                    "salary_raw": "15-20K",
                    "city": "深圳",
                }
            ],
        },
        headers=headers,
    )
    assert list_resp.status_code == 200
    entry = list_resp.json()["jobs"][pid]
    # 既包含 judgement，又包含 screen_hints
    assert entry["judgement"] is not None
    assert entry["screen_hints"] == [
        {"type": "strict_industry", "text": "高风险行业（餐饮）"},
        {"type": "exclude_keyword", "text": "命中不接受条件：外包"},
    ]
