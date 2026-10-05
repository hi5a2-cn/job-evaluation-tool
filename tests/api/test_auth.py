import hashlib
from fastapi.testclient import TestClient

from jet.db.store import local_day_bounds_utc, utc_now


def test_health_endpoint(client: TestClient):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "service": "jet",
        "api": 1,
        "version": "0.1.0",
    }


def test_origin_protection_middleware(client: TestClient):
    # Any request with an origin that does NOT start with chrome-extension:// must be blocked with 403
    resp = client.get("/v1/health", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"] == "forbidden_origin"
    # No CORS headers returned
    assert "access-control-allow-origin" not in resp.headers


def test_internal_pair_code_endpoint(client: TestClient):
    # 1. Missing admin secret header -> 403
    resp = client.post("/internal/pair-code")
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"

    # 2. Wrong admin secret header -> 403
    resp = client.post("/internal/pair-code", headers={"X-Jet-Admin": "wrong-secret"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"

    # 3. Origin header present -> 403
    resp = client.post(
        "/internal/pair-code",
        headers={"X-Jet-Admin": "test-admin", "Origin": "chrome-extension://any"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"

    # 4. Correct admin secret -> 200
    resp = client.post("/internal/pair-code", headers={"X-Jet-Admin": "test-admin"})
    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert len(data["code"]) == 6
    assert data["code"].isdigit()
    assert data["expires_in"] == 300


def test_pair_and_status_flow(client: TestClient):
    # 1. Get code from internal endpoint
    res_code = client.post("/internal/pair-code", headers={"X-Jet-Admin": "test-admin"})
    code = res_code.json()["code"]

    origin = "chrome-extension://myextensionid"

    # 2. Pair without Origin header -> 400 bad_origin
    bad_origin_res = client.post("/v1/pair", json={"code": code})
    assert bad_origin_res.status_code == 400
    assert bad_origin_res.json()["error"] == "bad_origin"

    # 3. Pair with wrong code -> 400 bad_code
    bad_code_res = client.post("/v1/pair", json={"code": "000000"}, headers={"Origin": origin})
    assert bad_code_res.status_code == 400
    assert bad_code_res.json()["error"] == "bad_code"

    # 4. Pair with correct code -> 200
    pair_res = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
    assert pair_res.status_code == 200
    pair_data = pair_res.json()
    token = pair_data["token"]
    assert len(token) == 43
    assert pair_data["user_id"] == "me"

    # Check database: only SHA-256 hash stored, not plaintext token
    conn = client.app.state.conn
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute("SELECT * FROM pairings WHERE token_hash = ?", (token_hash,)).fetchone()
    assert row is not None
    assert row["extension_origin"] == origin

    # Code cannot be reused
    reuse_res = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
    assert reuse_res.status_code == 400
    assert reuse_res.json()["error"] == "bad_code"

    # 5. Access /v1/status with valid auth
    headers = {
        "Authorization": f"Bearer {token}",
        "Origin": origin,
    }
    status_res = client.get("/v1/status", headers=headers)
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["user_id"] == "me"
    assert status_data["profile"] == {"set": False, "version_no": None}
    assert status_data["quota"]["limit"] == 150
    assert status_data["quota"]["used"] == 0
    assert status_data["quota"]["remaining"] == 150
    assert status_data["rule_excluded_today"] == 0

    # 6. Insert 2 billed=1 calls for today
    start_utc, _, _ = local_day_bounds_utc()
    now_str = utc_now()
    conn.execute(
        "INSERT INTO jobs (id, platform, platform_job_id, completeness, first_seen_at, last_seen_at) "
        "VALUES (100, 'boss', 'job_status_test', 'full', ?, ?)",
        (now_str, now_str),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (100, 100, 1, 'detail', 'Test Job', 1, 1, '深圳', 'hash_test', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO profiles (id, user_id, version_no, directions, keywords, cities, exclude_keywords, created_at) "
        "VALUES (100, 'me', 1, '[]', '[]', '[]', '[]', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (101, 'me', 100, 100, 100, 'done', '{}', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO job_versions (id, job_id, version_no, source, title, salary_visible, salary_parse_ok, city, content_hash, created_at) "
        "VALUES (101, 100, 2, 'detail', 'Test Job', 1, 1, '深圳', 'hash_test2', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO judgements (id, user_id, job_id, job_version_id, profile_id, status, rule_result, created_at) "
        "VALUES (102, 'me', 100, 101, 100, 'done', '{}', ?)",
        (now_str,),
    )
    conn.execute(
        "INSERT INTO llm_calls (user_id, judgement_id, provider, model, started_at, outcome, billed) "
        "VALUES ('me', 101, 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
        (start_utc,),
    )
    conn.execute(
        "INSERT INTO llm_calls (user_id, judgement_id, provider, model, started_at, outcome, billed) "
        "VALUES ('me', 102, 'deepseek', 'deepseek-flash', ?, 'ok', 1)",
        (start_utc,),
    )

    status_res2 = client.get("/v1/status", headers=headers)
    assert status_res2.status_code == 200
    status_data2 = status_res2.json()
    assert status_data2["quota"]["used"] == 2
    assert status_data2["quota"]["remaining"] == 148


def test_auth_rejections(client: TestClient):
    origin = "chrome-extension://myextensionid"
    headers = {"Origin": origin}

    # 1. No Authorization header -> 401
    resp1 = client.get("/v1/status", headers=headers)
    assert resp1.status_code == 401
    assert resp1.json()["error"] == "unpaired"

    # 2. Invalid Bearer token -> 401
    resp2 = client.get(
        "/v1/status",
        headers={"Authorization": "Bearer badtoken123", "Origin": origin},
    )
    assert resp2.status_code == 401
    assert resp2.json()["error"] == "unpaired"

    # 3. Valid token with wrong Origin -> 401
    # Pair to get a token
    code = client.app.state.pairing_codes.issue()
    pair_res = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
    token = pair_res.json()["token"]

    resp3 = client.get(
        "/v1/status",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "chrome-extension://different-ext-id",
        },
    )
    assert resp3.status_code == 401
    assert resp3.json()["error"] == "unpaired"

    # 4. Revoked token -> 401
    conn = client.app.state.conn
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute("UPDATE pairings SET revoked_at = ? WHERE token_hash = ?", (utc_now(), token_hash))

    resp4 = client.get(
        "/v1/status",
        headers={"Authorization": f"Bearer {token}", "Origin": origin},
    )
    assert resp4.status_code == 401
    assert resp4.json()["error"] == "unpaired"


def test_pairing_code_attempts_and_expiration(client: TestClient):
    # Time injection via custom clock
    current_time = 1000.0
    client.app.state.pairing_codes.clock = lambda: current_time

    code = client.app.state.pairing_codes.issue()
    origin = "chrome-extension://myextensionid"

    # 5 failed attempts -> 400 bad_code
    for _ in range(5):
        resp = client.post("/v1/pair", json={"code": "000000"}, headers={"Origin": origin})
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_code"

    # 6th attempt onwards -> 429 too_many_attempts (even with correct code)
    resp6 = client.post("/v1/pair", json={"code": code}, headers={"Origin": origin})
    assert resp6.status_code == 429
    assert resp6.json()["error"] == "too_many_attempts"

    # Re-issue code resets attempts
    code2 = client.app.state.pairing_codes.issue()
    # Expire after 301 seconds
    current_time += 301.0
    resp_exp = client.post("/v1/pair", json={"code": code2}, headers={"Origin": origin})
    assert resp_exp.status_code == 400
    assert resp_exp.json()["error"] == "bad_code"


def test_validation_error_format(client: TestClient):
    # Sending invalid payload to /v1/pair (code missing or wrong type)
    origin = "chrome-extension://myextensionid"
    resp = client.post("/v1/pair", json={"code": 12345}, headers={"Origin": origin})
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "invalid_payload"
    assert "message" in data


def test_paired_client_fixture(paired_client: tuple[TestClient, dict[str, str]]):
    client, headers = paired_client
    resp = client.get("/v1/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "me"


def test_internal_pair_code_rejects_non_loopback(settings):
    from jet.api.app import create_app

    app = create_app(settings, admin_secret="test-admin")
    with TestClient(app, client=("192.168.1.20", 50000)) as c:
        resp = c.post("/internal/pair-code", headers={"X-Jet-Admin": "test-admin"})
    assert resp.status_code == 403


def test_unknown_route_uses_error_format(client: TestClient):
    resp = client.get("/v1/nope")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_pairing_survives_restart_and_get_without_origin(settings):
    """真实 Chrome 场景：配对（POST，带 Origin）之后，插件后台的 GET 请求可能不带 Origin。

    配对后 status/profile 必须能通过；重启 Jet（同一数据目录重新创建 app）后令牌仍然有效；
    带了别的插件 Origin 仍然拒绝；普通网页 Origin 仍然 403。
    """
    from jet.api.app import create_app

    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"

    app1 = create_app(settings, admin_secret="test-admin")
    with TestClient(app1, client=("127.0.0.1", 50000)) as c1:
        code = c1.post("/internal/pair-code", headers={"X-Jet-Admin": "test-admin"}).json()["code"]
        token = c1.post("/v1/pair", json={"code": code}, headers={"Origin": origin}).json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        # GET 不带 Origin（Chrome 插件后台实际情况）
        assert c1.get("/v1/status", headers=auth).status_code == 200
        assert c1.get("/v1/profile", headers=auth).status_code == 404  # 已配对，只是还没有画像
        # PUT 带 Origin
        put = c1.put(
            "/v1/profile",
            json={"directions": ["数据分析"], "cities": ["深圳"]},
            headers={**auth, "Origin": origin},
        )
        assert put.status_code == 200

    # 重启：同一数据目录新建 app（新的 admin secret、新的内存配对码），令牌仍有效
    app2 = create_app(settings, admin_secret="another-admin")
    with TestClient(app2, client=("127.0.0.1", 50000)) as c2:
        status = c2.get("/v1/status", headers=auth)
        assert status.status_code == 200
        assert status.json()["profile"]["set"] is True
        assert c2.get("/v1/status", headers={**auth, "Origin": origin}).status_code == 200

        # 别的插件拿着这个令牌：拒绝
        other = c2.get("/v1/status", headers={**auth, "Origin": "chrome-extension://otherextension"})
        assert other.status_code == 401
        # 普通网页：403
        web = c2.get("/v1/status", headers={**auth, "Origin": "https://www.zhipin.com"})
        assert web.status_code == 403
        # 没有令牌：拒绝
        assert c2.get("/v1/status").status_code == 401


def test_daily_limit_synced_from_settings_on_startup(settings):
    import dataclasses

    from jet.api.app import create_app
    from jet.db.store import connect

    s = dataclasses.replace(settings, daily_llm_limit=77)
    app = create_app(s, admin_secret="test-admin")
    with TestClient(app, client=("127.0.0.1", 50000)):
        conn = connect(s.data_dir)
        limit = conn.execute("SELECT daily_llm_limit FROM user_settings WHERE user_id = 'me'").fetchone()[0]
        conn.close()
    assert limit == 77
