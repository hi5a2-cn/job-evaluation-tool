import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertJetUrl, createJetClient, DEFAULT_PORT, makeBaseUrl } from "../src/jet-client.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test("makeBaseUrl and assertJetUrl constraints", () => {
  assert.equal(makeBaseUrl(47615), "http://127.0.0.1:47615");
  assert.equal(makeBaseUrl("47615"), "http://127.0.0.1:47615");

  assert.throws(() => makeBaseUrl(0), /Invalid port/);
  assert.throws(() => makeBaseUrl(70000), /Invalid port/);
  assert.throws(() => makeBaseUrl("not_a_port"), /Invalid port/);

  // assertJetUrl
  assert.doesNotThrow(() => assertJetUrl("http://127.0.0.1:47615/v1/health"));
  assert.throws(() => assertJetUrl("http://localhost:47615/v1/health"), /Forbidden Jet URL/);
  assert.throws(() => assertJetUrl("https://127.0.0.1:47615/v1/health"), /Forbidden Jet URL/);
  assert.throws(() => assertJetUrl("https://api.zhipin.com/v1"), /Forbidden Jet URL/);
});

test("createJetClient error mapping", async () => {
  // 1. Connection failure -> jet_down
  const failingFetch = async () => {
    throw new Error("Connection refused");
  };
  const client1 = createJetClient({ fetchImpl: failingFetch });
  const res1 = await client1.call("GET", "/v1/health");
  assert.equal(res1.ok, false);
  assert.equal(res1.viewState, "jet_down");

  // 2. 401 Unauthorized -> unpaired
  const mock401 = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ error: "unpaired", message: "未配对" }),
  });
  const client2 = createJetClient({ fetchImpl: mock401 });
  const res2 = await client2.call("GET", "/v1/status");
  assert.equal(res2.ok, false);
  assert.equal(res2.status, 401);
  assert.equal(res2.viewState, "unpaired");

  // 3. 409 Conflict or 404 Not Found with no_profile -> no_profile
  const mock409 = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ error: "no_profile", message: "请先设置画像" }),
  });
  const client3 = createJetClient({ fetchImpl: mock409 });
  const res3 = await client3.call("GET", "/v1/profile");
  assert.equal(res3.ok, false);
  assert.equal(res3.viewState, "no_profile");

  const mock404 = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ error: "no_profile", message: "未找到画像" }),
  });
  const client3b = createJetClient({ fetchImpl: mock404 });
  const res3b = await client3b.call("GET", "/v1/profile");
  assert.equal(res3b.ok, false);
  assert.equal(res3b.viewState, "no_profile");

  // 4. 422 Unprocessable Entity -> unrecognized
  const mock422 = async () => ({
    ok: false,
    status: 422,
    json: async () => ({ error: "invalid_payload", message: "数据错误" }),
  });
  const client4 = createJetClient({ fetchImpl: mock422 });
  const res4 = await client4.call("POST", "/v1/observations", {});
  assert.equal(res4.ok, false);
  assert.equal(res4.viewState, "unrecognized");

  // 5. 500 Server Error -> failed
  const mock500 = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: "server_error" }),
  });
  const client5 = createJetClient({ fetchImpl: mock500 });
  const res5 = await client5.call("GET", "/v1/health");
  assert.equal(res5.ok, false);
  assert.equal(res5.viewState, "failed");

  // 6. 200 Success with Bearer token
  let capturedUrl = "";
  let capturedHeaders = {};
  const mock200 = async (url, options) => {
    capturedUrl = url;
    capturedHeaders = options.headers;
    return {
      ok: true,
      status: 200,
      json: async () => ({ service: "jet" }),
    };
  };
  const client6 = createJetClient({
    getToken: async () => "secret_token_123",
    getPort: async () => 47615,
    fetchImpl: mock200,
  });
  const res6 = await client6.call("GET", "/v1/health");
  assert.equal(res6.ok, true);
  assert.equal(capturedUrl, "http://127.0.0.1:47615/v1/health");
  assert.equal(capturedHeaders["Authorization"], "Bearer secret_token_123");
});

test("source code scan: fetch() only allowed in jet-client.js", () => {
  const srcDir = path.resolve(__dirname, "../src");

  function getJsFiles(dir) {
    let results = [];
    const list = fs.readdirSync(dir);
    for (const file of list) {
      const filePath = path.join(dir, file);
      const stat = fs.statSync(filePath);
      if (stat.isDirectory()) {
        results = results.concat(getJsFiles(filePath));
      } else if (file.endsWith(".js")) {
        results.push(filePath);
      }
    }
    return results;
  }

  const jsFiles = getJsFiles(srcDir);
  assert.ok(jsFiles.length > 0, "No JS files found in extension/src");

  for (const file of jsFiles) {
    const filename = path.basename(file);
    const content = fs.readFileSync(file, "utf-8");

    // Match fetch( as a function call, ignoring comments if needed or strictly checking
    const hasFetchCall = /\bfetch\s*\(/.test(content);
    if (filename === "jet-client.js") {
      // jet-client.js wraps globalThis.fetch, so it can reference fetch
      continue;
    }

    assert.ok(
      !hasFetchCall,
      `Forbidden fetch() call found in ${file}. All network requests must go through jet-client.js.`,
    );
  }
});

test("createJetClient new chat and experience methods", async () => {
  const calls = [];
  const mockFetch = async (url, options) => {
    calls.push({
      url,
      method: options.method,
      headers: options.headers,
      body: options.body ? JSON.parse(options.body) : undefined,
    });
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    };
  };

  const client = createJetClient({
    getToken: async () => "token_abc",
    getPort: async () => 47615,
    fetchImpl: mockFetch,
  });

  // preview
  await client.preview({ encrypt_job_id: "job_1" });
  assert.equal(calls[0].url, "http://127.0.0.1:47615/v1/chat/preview");
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(calls[0].body, { encrypt_job_id: "job_1" });
  assert.equal(calls[0].headers["Authorization"], "Bearer token_abc");

  // generate
  await client.generate({ encrypt_job_id: "job_1", prompt_hash: "hash_1" });
  assert.equal(calls[1].url, "http://127.0.0.1:47615/v1/chat/generate");
  assert.equal(calls[1].method, "POST");
  assert.deepEqual(calls[1].body, { encrypt_job_id: "job_1", prompt_hash: "hash_1" });

  // consent
  await client.consent();
  assert.equal(calls[2].url, "http://127.0.0.1:47615/v1/chat/consent");
  assert.equal(calls[2].method, "POST");
  // 字段版本由服务端决定，请求不带版本号
  assert.deepEqual(calls[2].body, {});

  // revokeConsent
  await client.revokeConsent();
  assert.equal(calls[3].url, "http://127.0.0.1:47615/v1/chat/revoke-consent");
  assert.equal(calls[3].method, "POST");
  assert.deepEqual(calls[3].body, {});

  // consentStatus
  await client.consentStatus();
  assert.equal(calls[4].url, "http://127.0.0.1:47615/v1/chat/consent-status");
  assert.equal(calls[4].method, "GET");

  // getExperience
  await client.getExperience();
  assert.equal(calls[5].url, "http://127.0.0.1:47615/v1/experience");
  assert.equal(calls[5].method, "GET");

  // putExperience
  const expItems = [{ item_no: 1, content: "负责海外销售" }];
  await client.putExperience(expItems);
  assert.equal(calls[6].url, "http://127.0.0.1:47615/v1/experience");
  assert.equal(calls[6].method, "PUT");
  assert.deepEqual(calls[6].body, expItems);
});

test("createJetClient new error viewState mappings", async () => {
  // 403 consent_required
  const c403 = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 403,
      json: async () => ({ error: "consent_required" }),
    }),
  });
  const r403 = await c403.call("POST", "/v1/chat/generate", {});
  assert.equal(r403.viewState, "consent_required");

  // 429 quota_exhausted
  const c429 = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 429,
      json: async () => ({ error: "quota_exhausted" }),
    }),
  });
  const r429 = await c429.call("POST", "/v1/chat/generate", {});
  assert.equal(r429.viewState, "quota_exhausted");

  // 400 hash_mismatch
  const c400 = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "hash_mismatch" }),
    }),
  });
  const r400 = await c400.call("POST", "/v1/chat/generate", {});
  assert.equal(r400.viewState, "hash_mismatch");

  // 422 self_name_unavailable
  const c422a = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 422,
      json: async () => ({ error: "self_name_unavailable" }),
    }),
  });
  const r422a = await c422a.call("POST", "/v1/chat/generate", {});
  assert.equal(r422a.viewState, "self_name_unavailable");

  // 422 all_suggestions_dropped
  const c422b = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 422,
      json: async () => ({ error: "all_suggestions_dropped" }),
    }),
  });
  const r422b = await c422b.call("POST", "/v1/chat/generate", {});
  assert.equal(r422b.viewState, "all_suggestions_dropped");

  // 502 llm_failed
  const c502 = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 502,
      json: async () => ({ error: "llm_failed" }),
    }),
  });
  const r502 = await c502.call("POST", "/v1/chat/generate", {});
  assert.equal(r502.viewState, "llm_failed");

  // 409 no_llm_key
  const c409NoKey = createJetClient({
    fetchImpl: async () => ({
      ok: false,
      status: 409,
      json: async () => ({ error: "no_llm_key", message: "请先在设置页填写 DeepSeek API Key" }),
    }),
  });
  const r409NoKey = await c409NoKey.call("POST", "/v1/chat/generate", {});
  assert.equal(r409NoKey.viewState, "no_llm_key");
});

test("createJetClient llm-key helper methods", async () => {
  const calls = [];
  const client = createJetClient({
    fetchImpl: async (url, opts) => {
      calls.push({ url, method: opts?.method, body: opts?.body });
      if (url.endsWith("/v1/llm-key") && (!opts || opts.method === "GET")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ configured: true, source: "settings_page", masked: "••••abcd" }),
        };
      }
      if (url.endsWith("/v1/llm-key") && opts?.method === "PUT") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ configured: true, source: "settings_page", masked: "••••1234" }),
        };
      }
      if (url.endsWith("/v1/llm-key") && opts?.method === "DELETE") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ configured: false, source: null, masked: null }),
        };
      }
      if (url.endsWith("/v1/llm-key/test") && opts?.method === "POST") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ok: true, reason: "ok" }),
        };
      }
      return { ok: false, status: 404, json: async () => ({ error: "not_found" }) };
    },
  });

  const getRes = await client.getLlmKey();
  assert.equal(getRes.ok, true);
  assert.equal(getRes.data.masked, "••••abcd");
  assert.equal(calls[0].method, "GET");

  const putRes = await client.putLlmKey("sk-test-key-1234");
  assert.equal(putRes.ok, true);
  assert.equal(putRes.data.masked, "••••1234");
  assert.equal(calls[1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[1].body), { api_key: "sk-test-key-1234" });

  const delRes = await client.deleteLlmKey();
  assert.equal(delRes.ok, true);
  assert.equal(delRes.data.configured, false);
  assert.equal(calls[2].method, "DELETE");

  const testRes = await client.testLlmKey("sk-test-key");
  assert.equal(testRes.ok, true);
  assert.equal(testRes.data.ok, true);
  assert.equal(calls[3].method, "POST");
  assert.deepEqual(JSON.parse(calls[3].body), { api_key: "sk-test-key" });

  const testResNull = await client.testLlmKey();
  assert.equal(testResNull.ok, true);
  assert.equal(calls[4].method, "POST");
  assert.deepEqual(JSON.parse(calls[4].body), {});
});

test("prejudgeJobs, getPrejudgeSettings, putPrejudgeSettings (FR-005, FR-008)", async () => {
  const calls = [];
  const client = createJetClient({
    fetchImpl: async (url, opts) => {
      calls.push({ url, method: opts?.method, body: opts?.body });
      if (url.endsWith("/v1/prejudge") && opts?.method === "POST") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "ok",
            prejudgements: { j1: { level: "open", reason: "契合", created_at: "2026-10-06T00:00:00Z" } },
            usage: { used: 1, limit: 20, remaining: 19 },
          }),
        };
      }
      if (url.endsWith("/v1/prejudge/settings") && (!opts || opts.method === "GET")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            daily_prejudge_limit: 20,
            used_today: 1,
            remaining_today: 19,
          }),
        };
      }
      if (url.endsWith("/v1/prejudge/settings") && opts?.method === "PUT") {
        const body = JSON.parse(opts.body);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            daily_prejudge_limit: body.daily_prejudge_limit,
            used_today: 1,
            remaining_today: body.daily_prejudge_limit - 1,
          }),
        };
      }
      return { ok: false, status: 404, json: async () => ({ error: "not_found" }) };
    },
  });

  // 1. prejudgeJobs
  const pjRes = await client.prejudgeJobs([{ platform_job_id: "j1", title: "Dev" }]);
  assert.equal(pjRes.ok, true);
  assert.equal(pjRes.data.status, "ok");
  assert.equal(pjRes.data.prejudgements.j1.level, "open");
  assert.equal(pjRes.data.usage.remaining, 19);
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].url.endsWith("/v1/prejudge"), true);
  assert.deepEqual(JSON.parse(calls[0].body), { jobs: [{ platform_job_id: "j1", title: "Dev" }] });

  // 2. getPrejudgeSettings
  const getRes = await client.getPrejudgeSettings();
  assert.equal(getRes.ok, true);
  assert.equal(getRes.data.daily_prejudge_limit, 20);
  assert.equal(calls[1].method, "GET");

  // 3. putPrejudgeSettings
  const putRes = await client.putPrejudgeSettings(50);
  assert.equal(putRes.ok, true);
  assert.equal(putRes.data.daily_prejudge_limit, 50);
  assert.equal(calls[2].method, "PUT");
  assert.deepEqual(JSON.parse(calls[2].body), { daily_prejudge_limit: 50 });
});
