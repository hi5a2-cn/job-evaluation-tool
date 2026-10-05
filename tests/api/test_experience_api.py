"""API tests for experience endpoints GET /v1/experience and PUT /v1/experience (T011).

Specs:
- specs/003-hr-assistant/spec.md (FR-028-FR-033)
- specs/003-hr-assistant/contracts/local-api.md (Section 6: GET/PUT /v1/experience)
- specs/003-hr-assistant/tasks.md (T011, T012)
"""

import hashlib
from pathlib import Path
from fastapi.testclient import TestClient

from jet.db.store import open_db


def test_experience_api_unpaired_rejected(client: TestClient):
    """未配对 401 约定。"""
    # 1. GET /v1/experience
    resp1 = client.get("/v1/experience")
    assert resp1.status_code == 401
    assert resp1.json()["error"] == "unpaired"

    # 2. PUT /v1/experience
    resp2 = client.put("/v1/experience", json=[])
    assert resp2.status_code == 401
    assert resp2.json()["error"] == "unpaired"


def test_get_experience_empty(paired_client: tuple[TestClient, dict[str, str]]):
    """已配对新用户查询经历素材返回空列表。"""
    client, headers = paired_client
    resp = client.get("/v1/experience", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_put_and_get_experience_lifecycle(paired_client: tuple[TestClient, dict[str, str]]):
    """测试完整读写流程：写入 2 条 -> 查询 -> 覆盖写入 1 条 -> 清空。"""
    client, headers = paired_client

    # 1. 写入 2 条
    payload = [
        {
            "item_no": 1,
            "content": "在新能源外贸公司负责西非市场的逆变器渠道拓展，主导拜访了 10 余家本地分销商，实现季度销售额翻倍。",
        },
        {
            "item_no": 2,
            "content": "独立带过 3 人的海外客户成功小团队，负责售后技术工单跟进，客户满意度提升 15%。",
        },
    ]
    put_resp = client.put("/v1/experience", json=payload, headers=headers)
    assert put_resp.status_code == 200
    assert put_resp.json() == {"ok": True, "count": 2}

    # 2. 查询并验证字段与顺序
    get_resp = client.get("/v1/experience", headers=headers)
    assert get_resp.status_code == 200
    items = get_resp.json()
    assert len(items) == 2
    assert items[0]["item_no"] == 1
    assert items[0]["content"] == payload[0]["content"]
    assert "updated_at" in items[0]
    assert items[1]["item_no"] == 2
    assert items[1]["content"] == payload[1]["content"]
    assert "updated_at" in items[1]

    # 3. 覆盖写入 1 条（全量替换）
    new_payload = [
        {
            "item_no": 5,
            "content": "负责跨境供应链系统搭建与物流清关管理。",
        }
    ]
    put_resp2 = client.put("/v1/experience", json=new_payload, headers=headers)
    assert put_resp2.status_code == 200
    assert put_resp2.json() == {"ok": True, "count": 1}

    # 验证原条目 1、2 被物理替换，仅剩条目 5
    get_resp2 = client.get("/v1/experience", headers=headers)
    assert get_resp2.status_code == 200
    items2 = get_resp2.json()
    assert len(items2) == 1
    assert items2[0]["item_no"] == 5
    assert items2[0]["content"] == new_payload[0]["content"]

    # 4. 全量清空
    put_resp3 = client.put("/v1/experience", json=[], headers=headers)
    assert put_resp3.status_code == 200
    assert put_resp3.json() == {"ok": True, "count": 0}

    get_resp3 = client.get("/v1/experience", headers=headers)
    assert get_resp3.status_code == 200
    assert get_resp3.json() == []


def test_put_experience_validation_errors(paired_client: tuple[TestClient, dict[str, str]]):
    """测试 PUT /v1/experience 各种非法 payload 的 422 校验拦截。"""
    client, headers = paired_client

    # 1. 超过 10 条 -> too_many_items
    too_many = [{"item_no": i, "content": f"经历描述 {i}"} for i in range(1, 12)]
    resp = client.put("/v1/experience", json=too_many, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "too_many_items"

    # 2. 单条内容为空 -> content_invalid
    resp2 = client.put("/v1/experience", json=[{"item_no": 1, "content": ""}], headers=headers)
    assert resp2.status_code == 422
    assert resp2.json()["error"] == "content_invalid"

    # 3. 单条内容纯空白 -> content_invalid
    resp3 = client.put("/v1/experience", json=[{"item_no": 1, "content": "   \n\t  "}], headers=headers)
    assert resp3.status_code == 422
    assert resp3.json()["error"] == "content_invalid"

    # 4. 单条内容超过 200 字 -> content_invalid
    resp4 = client.put("/v1/experience", json=[{"item_no": 1, "content": "字" * 201}], headers=headers)
    assert resp4.status_code == 422
    assert resp4.json()["error"] == "content_invalid"

    # 5. item_no < 1 -> item_no_invalid
    resp5 = client.put("/v1/experience", json=[{"item_no": 0, "content": "正常经历"}], headers=headers)
    assert resp5.status_code == 422
    assert resp5.json()["error"] == "item_no_invalid"

    # 6. item_no > 10 -> item_no_invalid
    resp6 = client.put("/v1/experience", json=[{"item_no": 11, "content": "正常经历"}], headers=headers)
    assert resp6.status_code == 422
    assert resp6.json()["error"] == "item_no_invalid"

    # 7. item_no 重复 -> item_no_invalid
    duplicate_items = [
        {"item_no": 1, "content": "经历一"},
        {"item_no": 1, "content": "经历二"},
    ]
    resp7 = client.put("/v1/experience", json=duplicate_items, headers=headers)
    assert resp7.status_code == 422
    assert resp7.json()["error"] == "item_no_invalid"

    # 8. 请求体不是列表 -> invalid_payload
    resp8 = client.put("/v1/experience", json={"item_no": 1, "content": "非数组"}, headers=headers)
    assert resp8.status_code == 422
    assert resp8.json()["error"] == "invalid_payload"


def test_experience_api_user_isolation(paired_client: tuple[TestClient, dict[str, str]], data_dir: Path):
    """测试不同配对用户的经历素材完全隔离。"""
    client, headers_me = paired_client

    # 给 user2 设置配对凭证
    token_u2 = "test-token-user2"
    token_hash_u2 = hashlib.sha256(token_u2.encode("utf-8")).hexdigest()
    origin = "chrome-extension://testextid"

    conn = open_db(data_dir)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES ('user2', 'user2', '2026-09-25T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO pairings (user_id, extension_origin, token_hash, created_at, last_used_at, revoked_at) "
            "VALUES ('user2', ?, ?, '2026-09-25T00:00:00Z', NULL, NULL)",
            (origin, token_hash_u2),
        )
        conn.commit()
    finally:
        conn.close()

    headers_u2 = {
        "Authorization": f"Bearer {token_u2}",
        "Origin": origin,
    }

    # me 写入 2 条
    client.put(
        "/v1/experience",
        json=[
            {"item_no": 1, "content": "me 的第 1 条经历"},
            {"item_no": 2, "content": "me 的第 2 条经历"},
        ],
        headers=headers_me,
    )

    # user2 初始查询应为空
    resp_u2_empty = client.get("/v1/experience", headers=headers_u2)
    assert resp_u2_empty.status_code == 200
    assert resp_u2_empty.json() == []

    # user2 写入 1 条
    client.put(
        "/v1/experience",
        json=[
            {"item_no": 1, "content": "user2 的独有经历"},
        ],
        headers=headers_u2,
    )

    # me 查询依然为自己的 2 条
    me_items = client.get("/v1/experience", headers=headers_me).json()
    assert len(me_items) == 2
    assert me_items[0]["content"] == "me 的第 1 条经历"

    # user2 查询为自己的 1 条
    u2_items = client.get("/v1/experience", headers=headers_u2).json()
    assert len(u2_items) == 1
    assert u2_items[0]["content"] == "user2 的独有经历"
