// 体检第 88 条：后台只认设置页 / 侧边栏实际发出的消息名和字段；
// 逐个按真实发送方的消息形状发给后台，核对它向 Jet 发出的请求；删掉的别名不再有响应。
import test from "node:test";
import assert from "node:assert/strict";

let messageListener = null;
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => {} },
    session: { get: async () => ({}), set: async () => {}, remove: async () => {} },
    onChanged: { addListener: () => {} },
  },
  tabs: { sendMessage: async () => {}, onRemoved: { addListener: () => {} } },
  runtime: {
    sendMessage: async () => {},
    onMessage: { addListener: (fn) => { messageListener = fn; } },
  },
  sidePanel: { setPanelBehavior: async () => {} },
};

const { jetClient } = await import("../src/background.js");

async function send(message) {
  const calls = [];
  const origCall = jetClient.call;
  jetClient.call = async (method, path, body) => {
    calls.push({ method, path, body });
    return { ok: true, data: {} };
  };
  try {
    let response;
    const handled = await new Promise((resolve) => {
      const ret = messageListener(message, {}, (res) => {
        response = res;
        resolve(true);
      });
      if (ret !== true) resolve(false);
    });
    return { handled, response, calls };
  } finally {
    jetClient.call = origCall;
  }
}

const CASES = [
  // [发送方, 消息, 期望请求]
  ["sidepanel 预览", { type: "chat_preview", payload: { job_title: "运营" } }, ["POST", "/v1/chat/preview", { job_title: "运营" }]],
  ["sidepanel 生成", { type: "chat_generate", payload: { prompt_hash: "h" } }, ["POST", "/v1/chat/generate", { prompt_hash: "h" }]],
  ["sidepanel 同意", { type: "chat_consent" }, ["POST", "/v1/chat/consent", {}]],
  ["options 撤回同意", { type: "chat_revoke_consent" }, ["POST", "/v1/chat/revoke-consent", {}]],
  ["options 同意状态", { type: "chat_consent_status" }, ["GET", "/v1/chat/consent-status", undefined]],
  [
    "options 经历素材",
    { type: "put_experience", items: [{ item_no: 1, content: "经历" }] },
    ["PUT", "/v1/experience", [{ item_no: 1, content: "经历" }]],
  ],
  [
    "options 简历名称",
    { type: "put_resumes", items: [{ slot: 1, name: "简历A" }] },
    ["PUT", "/v1/resumes", { items: [{ slot: 1, name: "简历A" }] }],
  ],
  [
    "options 从严行业",
    { type: "save_strict_industries", selected: ["保险"] },
    ["PUT", "/v1/strict-industries", { selected: ["保险"] }],
  ],
  [
    "options 重新生成画像",
    { type: "regenerate_resume", slot: 2, self_name: "李明" },
    ["POST", "/v1/resumes/2/regenerate", { self_name: "李明" }],
  ],
];

for (const [label, message, [method, path, body]] of CASES) {
  test(`后台处理 ${message.type}（${label}）`, async () => {
    const { handled, response, calls } = await send(message);
    assert.equal(handled, true);
    assert.deepEqual(response, { ok: true, data: {} });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, method);
    assert.equal(calls[0].path, path);
    assert.deepEqual(calls[0].body, body);
  });
}

test("删掉的别名消息不再被处理，也不会发出任何请求", async () => {
  for (const type of [
    "check_chat_page",
    "preview_chat",
    "generate_chat",
    "record_consent",
    "revoke_consent",
    "get_consent_status",
    "save_experience",
    "save_resumes",
    "put_strict_industries",
  ]) {
    const { handled, calls } = await send({ type, payload: {}, items: [], selected: [] });
    assert.equal(handled, false, type);
    assert.equal(calls.length, 0, type);
  }
});
