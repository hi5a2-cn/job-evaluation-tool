"""API tests for HR note endpoint with source parameter (T036).

Specs:
- specs/003-hr-assistant/spec.md (FR-043, SC-011)
- specs/003-hr-assistant/contracts/local-api.md (Section 8: PUT /v1/jobs/{platform_job_id}/hr-note)
- specs/003-hr-assistant/tasks.md (T036, T038)
"""

import pytest
from fastapi.testclient import TestClient


def _setup_job(client: TestClient, headers: dict[str, str], pid: str = "job_note_01") -> None:
    # 确保画像与岗位存在
    client.put(
        "/v1/profile",
        json={"directions": ["后端开发"], "cities": ["深圳"]},
        headers=headers,
    )
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-26T01:00:00Z",
        "jobs": [
            {
                "platform_job_id": pid,
                "title": "后端工程师",
                "company_name": "测试网络",
                "salary_raw": "20-30K",
                "city": "深圳",
                "description": "服务端架构设计",
            }
        ],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200


def test_put_hr_note_card_empty_allowed(paired_client: tuple[TestClient, dict[str, str]]):
    """source='card' 为空时照旧清空记录（保持 002 行为）。"""
    client, headers = paired_client
    pid = "job_card_clear"
    _setup_job(client, headers, pid=pid)

    # 1. 初始写入
    client.put(f"/v1/jobs/{pid}/hr-note", json={"note": "需要清空的内容", "source": "card"}, headers=headers)

    # 2. source='card' 提交空内容清空
    res_clear = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": "", "source": "card"}, headers=headers)
    assert res_clear.status_code == 200
    assert res_clear.json()["jobs"][pid]["hr_note"] is None


def test_put_hr_note_default_source_is_card(paired_client: tuple[TestClient, dict[str, str]]):
    """不传 source 时缺省按 'card' 处理，保持 002 兼容性。"""
    client, headers = paired_client
    pid = "job_default_source"
    _setup_job(client, headers, pid=pid)

    # 写入
    res_set = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": "原有卡片说明"}, headers=headers)
    assert res_set.status_code == 200
    assert res_set.json()["jobs"][pid]["hr_note"] == "原有卡片说明"

    # 不传 source 提交空 -> 清空成功
    res_clear = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": ""}, headers=headers)
    assert res_clear.status_code == 200
    assert res_clear.json()["jobs"][pid]["hr_note"] is None


# myjobs（岗位库列表）和 chat_sidebar（聊天页侧边栏）两个来源走服务端同一条判断（routes.py hr-note），
# 用同一组断言参数化测试（体检第 77 条：原来是逐行相同的两对函数）。
@pytest.mark.parametrize(
    "source, initial_note, spec",
    [
        ("myjobs", "HR说西非办事处有补贴", "SC-011"),
        ("chat_sidebar", "HR告知下周一安排初试", "T057, SC-017"),
    ],
)
def test_put_hr_note_empty_rejected_for_list_and_sidebar(
    paired_client: tuple[TestClient, dict[str, str]], source: str, initial_note: str, spec: str
):
    """source 为 myjobs / chat_sidebar 且内容经 strip() 后为空时返回 422 empty_not_allowed，数据库记录不变。"""
    client, headers = paired_client
    pid = f"job_{source}_empty"
    _setup_job(client, headers, pid=pid)

    # 1. 先保存一条已有记录
    res1 = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": initial_note, "source": source}, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["jobs"][pid]["hr_note"] == initial_note

    # 2–4. 空字符串、纯空白、None 都返回 422
    for empty in ("", "   \n\t  ", None):
        res = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": empty, "source": source}, headers=headers)
        assert res.status_code == 422, (spec, repr(empty))
        assert res.json() == {
            "error": "empty_not_allowed",
            "message": "清空请到岗位卡片操作",
        }

    # 5. 校验数据库中的原有记录绝对未被修改或删除
    get_res = client.get(f"/v1/judgements?ids={pid}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["jobs"][pid]["hr_note"] == initial_note


@pytest.mark.parametrize(
    "source, note",
    [
        ("myjobs", "HR告知主要负责海外代理商对接"),
        ("chat_sidebar", "HR告知主要负责海外客户维护"),
    ],
)
def test_put_hr_note_valid_saved_for_list_and_sidebar(
    paired_client: tuple[TestClient, dict[str, str]], source: str, note: str
):
    """source 为 myjobs / chat_sidebar 时提交非空内容正常保存。"""
    client, headers = paired_client
    pid = f"job_{source}_valid"
    _setup_job(client, headers, pid=pid)

    res = client.put(f"/v1/jobs/{pid}/hr-note", json={"note": note, "source": source}, headers=headers)
    assert res.status_code == 200
    assert res.json()["jobs"][pid]["hr_note"] == note
