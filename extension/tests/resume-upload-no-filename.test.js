// 体检第 79 条：上传简历不再把原始文件名（常带真名）发给本机服务，服务端从不使用它
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

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

test("后台转发 upload_resume 时请求体不含 filename，即使消息里带了", async () => {
  assert.ok(messageListener, "background.js 应注册消息监听");
  const origCall = jetClient.call;
  let sent = null;
  jetClient.call = async (method, path, body) => {
    sent = { method, path, body };
    return { ok: true, data: {} };
  };
  try {
    const response = await new Promise((resolve) => {
      messageListener(
        {
          type: "upload_resume",
          slot: 1,
          name: "后端简历",
          filename: "李明_后端简历.pdf",
          pdf_base64: "JVBERi0=",
          self_name: "李明",
        },
        {},
        resolve
      );
    });
    assert.deepEqual(response, { ok: true, data: {} });
    assert.equal(sent.method, "POST");
    assert.equal(sent.path, "/v1/resumes/upload");
    assert.deepEqual(sent.body, { slot: 1, name: "后端简历", pdf_base64: "JVBERi0=", self_name: "李明" });
  } finally {
    jetClient.call = origCall;
  }
});

test("设置页发出的 upload_resume 消息不带文件名", () => {
  const src = fs.readFileSync(new URL("../src/options.js", import.meta.url), "utf8");
  const start = src.indexOf('type: "upload_resume"');
  assert.ok(start !== -1, "找不到上传简历的消息");
  const message = src.slice(start, src.indexOf("}", start));
  assert.ok(message.includes("pdf_base64"), "截取的应是上传消息本体");
  assert.ok(!message.includes("filename"), "上传消息不能带 filename");
});
