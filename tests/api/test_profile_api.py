from fastapi.testclient import TestClient


def test_profile_api_lifecycle(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client

    # 1. Unset profile -> 404 no_profile
    resp_initial = client.get("/v1/profile", headers=headers)
    assert resp_initial.status_code == 404
    assert resp_initial.json()["error"] == "no_profile"

    # Status reflects profile.set == False
    status_resp = client.get("/v1/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["profile"] == {"set": False, "version_no": None}

    # 2. Invalid PUT payload: missing directions -> 422
    invalid_resp = client.put(
        "/v1/profile",
        json={"cities": ["深圳"]},
        headers=headers,
    )
    assert invalid_resp.status_code == 422
    assert invalid_resp.json()["error"] == "invalid_payload"

    # Invalid PUT payload: empty directions list -> 422
    invalid_resp2 = client.put(
        "/v1/profile",
        json={"directions": [], "cities": ["深圳"]},
        headers=headers,
    )
    assert invalid_resp2.status_code == 422
    assert invalid_resp2.json()["error"] == "invalid_payload"

    # 3. Successful PUT
    payload = {
        "directions": ["Python后端", "数据分析"],
        "keywords": ["FastAPI", "SQL"],
        "cities": ["深圳", "广州"],
        "min_monthly_k": 20.0,
        "exclude_keywords": ["外包", "驻场"],
    }
    put_resp = client.put("/v1/profile", json=payload, headers=headers)
    assert put_resp.status_code == 200
    assert put_resp.json() == {"version_no": 1, "changed": True}

    # 4. GET returns stored profile
    get_resp = client.get("/v1/profile", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["directions"] == payload["directions"]
    assert data["keywords"] == payload["keywords"]
    assert data["cities"] == payload["cities"]
    assert data["min_monthly_k"] == 20.0
    assert data["exclude_keywords"] == payload["exclude_keywords"]

    # Status reflects profile.set == True and version_no == 1
    status_resp2 = client.get("/v1/status", headers=headers)
    assert status_resp2.status_code == 200
    assert status_resp2.json()["profile"] == {"set": True, "version_no": 1}

    # 5. Idempotent PUT -> changed == False, version_no unchanged
    put_resp2 = client.put("/v1/profile", json=payload, headers=headers)
    assert put_resp2.status_code == 200
    assert put_resp2.json() == {"version_no": 1, "changed": False}

    # 6. Updated PUT -> version_no == 2, changed == True
    payload_mod = dict(payload, min_monthly_k=25.0)
    put_resp3 = client.put("/v1/profile", json=payload_mod, headers=headers)
    assert put_resp3.status_code == 200
    assert put_resp3.json() == {"version_no": 2, "changed": True}

    status_resp3 = client.get("/v1/status", headers=headers)
    assert status_resp3.json()["profile"] == {"set": True, "version_no": 2}
