import test from "node:test";
import assert from "node:assert/strict";
import { buildChatPreviewPayload } from "../src/chat-view.js";

test("buildChatPreviewPayload: forceMode 为 opening 时携带 force_mode 键", () => {
  const chatData = {
    encrypt_job_id: "job-opening-1",
    job_title: "前端工程师",
    company_name: "某科技公司",
  };
  const result = buildChatPreviewPayload(chatData, "opening");
  assert.deepEqual(result, {
    encrypt_job_id: "job-opening-1",
    job_title: "前端工程师",
    company_name: "某科技公司",
    force_mode: "opening",
  });
  assert.equal(result.force_mode, "opening");
  // 确保原对象未被修改
  assert.equal("force_mode" in chatData, false);
});

test("buildChatPreviewPayload: forceMode 为 reply 时携带 force_mode 键", () => {
  const chatData = {
    encrypt_job_id: "job-reply-1",
    job_title: "后端工程师",
    company_name: "某软件公司",
  };
  const result = buildChatPreviewPayload(chatData, "reply");
  assert.deepEqual(result, {
    encrypt_job_id: "job-reply-1",
    job_title: "后端工程师",
    company_name: "某软件公司",
    force_mode: "reply",
  });
  assert.equal(result.force_mode, "reply");
  // 确保原对象未被修改
  assert.equal("force_mode" in chatData, false);
});

test("buildChatPreviewPayload: forceMode 为 null 或其他值时原样返回 chatData（不加 force_mode 键）", () => {
  const chatData = {
    encrypt_job_id: "job-null-1",
    job_title: "产品经理",
    company_name: "某互联网公司",
  };

  const resNull = buildChatPreviewPayload(chatData, null);
  assert.equal(resNull, chatData);
  assert.equal("force_mode" in resNull, false);

  const resUndefined = buildChatPreviewPayload(chatData, undefined);
  assert.equal(resUndefined, chatData);
  assert.equal("force_mode" in resUndefined, false);

  const resOther = buildChatPreviewPayload(chatData, "custom_mode");
  assert.equal(resOther, chatData);
  assert.equal("force_mode" in resOther, false);
});
