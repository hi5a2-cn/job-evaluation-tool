import test from "node:test";
import assert from "node:assert/strict";
import { formatChatError } from "../src/chat-view.js";

test("formatChatError handles jet_down viewState priority over error message", () => {
  const err = {
    ok: false,
    status: 0,
    error: "Failed to fetch",
    viewState: "jet_down",
  };
  assert.equal(formatChatError(err), "Jet 没有运行，请先启动本机 Jet");
});

test("formatChatError returns err.data.message directly when code is llm_failed", () => {
  const err = {
    ok: false,
    status: 502,
    error: "llm_failed",
    viewState: "error",
    data: {
      error: "llm_failed",
      message: "生成失败：服务响应超时，可以再点一次生成",
    },
  };
  assert.equal(
    formatChatError(err),
    "生成失败：服务响应超时，可以再点一次生成",
  );
});

test("formatChatError preserves existing string error codes", () => {
  assert.equal(formatChatError("jet_down"), "Jet 没有运行，请先启动本机 Jet");
  assert.equal(formatChatError("quota_exhausted"), "今日生成次数已用完");
  assert.equal(
    formatChatError("no_llm_key"),
    "请先在设置页填写 DeepSeek API Key",
  );
  assert.equal(formatChatError("consent_required"), "尚未同意发送脱敏数据");
  assert.equal(
    formatChatError("self_name_unavailable"),
    "读不到你的姓名，暂时不能生成",
  );
});
