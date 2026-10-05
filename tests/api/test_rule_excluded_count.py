"""指标"规则排除数（今日）"：每个岗位只算一次、只看现在的判断（2026-09-25 修订）。"""
from fastapi.testclient import TestClient


def _detail(client: TestClient, headers: dict[str, str], pid: str, title: str, city: str = "深圳") -> dict:
    obs = {
        "page_type": "detail",
        "observed_at": "2026-09-25T10:00:00Z",
        "jobs": [{"platform_job_id": pid, "title": title, "salary_raw": "10-15K", "city": city, "description": f"{title} 职责"}],
    }
    resp = client.post("/v1/observations", json=obs, headers=headers)
    assert resp.status_code == 200
    return resp.json()["jobs"][pid]["judgement"]


def _rule_excluded(client: TestClient, headers: dict[str, str]) -> int:
    return client.get("/v1/status", headers=headers).json()["rule_excluded_today"]


def test_rule_excluded_counts_each_job_once_and_only_current(llm_client: tuple[TestClient, dict[str, str]]):
    client, headers = llm_client
    profile = {
        "directions": ["数据分析"],
        "preferred_cities": ["深圳"],
        "excluded_cities": ["哈尔滨"],
        "exclude_keywords": ["销售"],
    }
    assert client.put("/v1/profile", json=profile, headers=headers).status_code == 200

    # 职位名命中不接受关键词 → 规则排除
    j = _detail(client, headers, "r1", "电话销售")
    assert (j["source"], j["verdict"]) == ("rule", "skip")
    assert _rule_excluded(client, headers) == 1

    # 同一岗位强制重新判断：又产生一条规则判断，取代旧的 —— 仍只算 1
    resp = client.post("/v1/jobs/r1/judge", json={"force": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["jobs"]["r1"]["judgement"]["source"] == "rule"
    assert _rule_excluded(client, headers) == 1

    # 另一个岗位因不去的城市被排除 → 2
    j = _detail(client, headers, "r2", "数据分析师", city="哈尔滨")
    assert (j["source"], j["verdict"]) == ("rule", "skip")
    assert _rule_excluded(client, headers) == 2

    # 第三个岗位先被排除 → 3
    _detail(client, headers, "r3", "销售数据助理")
    assert _rule_excluded(client, headers) == 3

    # 画像去掉"销售"后再打开 r3：自动更新，不再被规则排除（没有 Key，大模型判断失败），现在的判断不是规则排除 → 不再计入
    profile["exclude_keywords"] = []
    assert client.put("/v1/profile", json=profile, headers=headers).status_code == 200
    j = _detail(client, headers, "r3", "销售数据助理")
    assert j["source"] != "rule"
    # r1（电话销售）现在的判断仍是规则排除（没有重新打开，不会自动更新）；r2 仍因城市排除
    assert _rule_excluded(client, headers) == 2
