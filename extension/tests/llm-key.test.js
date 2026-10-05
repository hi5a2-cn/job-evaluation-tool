import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  formatLlmKeySource,
  formatLlmKeyStatusText,
  formatTestLlmKeyResult,
  canClearLlmKey,
} from "../src/options.js";

import {
  decideQuotaBanner,
  decideJobCardBadge,
  formatChatError,
  computeLlmKeyConfigured,
} from "../src/chat-view.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test("formatLlmKeySource: maps sources to user-facing labels", () => {
  assert.equal(formatLlmKeySource("settings_page", true), "来自设置页");
  assert.equal(formatLlmKeySource("env", true), "来自 .env");
  assert.equal(formatLlmKeySource("settings_page", false), "未填写");
  assert.equal(formatLlmKeySource("env", false), "未填写");
  assert.equal(formatLlmKeySource(null, false), "未填写");
  assert.equal(formatLlmKeySource(undefined, false), "未填写");
  assert.equal(formatLlmKeySource("other", true), "未填写");
});

test("formatLlmKeyStatusText: displays masked key and origin correctly", () => {
  assert.equal(
    formatLlmKeyStatusText({ configured: true, source: "settings_page", masked: "••••abcd" }),
    "已保存：••••abcd",
  );
  assert.equal(
    formatLlmKeyStatusText({ configured: true, source: "env", masked: "••••1234" }),
    "正在使用 .env 中的 Key：••••1234",
  );
  assert.equal(
    formatLlmKeyStatusText({ configured: false, source: null, masked: null }),
    "未填写",
  );
  assert.equal(formatLlmKeyStatusText(null), "未填写");
  assert.equal(formatLlmKeyStatusText(undefined), "未填写");
  assert.equal(
    formatLlmKeyStatusText({ configured: true, source: "settings_page", masked: null }),
    "未填写",
  );
});

test("formatTestLlmKeyResult: maps test connection outcomes to exact standard messages", () => {
  // 1. Success
  const okRes = formatTestLlmKeyResult({ ok: true, reason: "ok" });
  assert.equal(okRes.ok, true);
  assert.equal(okRes.message, "连接成功");

  // 2. Key 无效
  const invalidRes = formatTestLlmKeyResult({ ok: false, reason: "invalid_key" });
  assert.equal(invalidRes.ok, false);
  assert.equal(invalidRes.message, "Key 无效");

  // 3. 无法连接 (unreachable / timeout)
  const unreachableRes = formatTestLlmKeyResult({ ok: false, reason: "unreachable" });
  assert.equal(unreachableRes.ok, false);
  assert.equal(unreachableRes.message, "无法连接");

  // 4. 尚未填写 Key
  const noKeyRes = formatTestLlmKeyResult({ ok: false, reason: "no_key" });
  assert.equal(noKeyRes.ok, false);
  assert.equal(noKeyRes.message, "尚未填写 Key");

  // 5. null / unexpected
  const nullRes = formatTestLlmKeyResult(null);
  assert.equal(nullRes.ok, false);
  assert.equal(nullRes.message, "无法连接");

  const customErrorRes = formatTestLlmKeyResult({ ok: false, message: "自定义错误" });
  assert.equal(customErrorRes.ok, false);
  assert.equal(customErrorRes.message, "自定义错误");
});

test("canClearLlmKey: allows clear only when key is saved via settings_page", () => {
  assert.equal(
    canClearLlmKey({ configured: true, source: "settings_page", masked: "••••abcd" }),
    true,
  );
  assert.equal(
    canClearLlmKey({ configured: true, source: "env", masked: "••••1234" }),
    false,
  );
  assert.equal(
    canClearLlmKey({ configured: false, source: null, masked: null }),
    false,
  );
  assert.equal(canClearLlmKey(null), false);
  assert.equal(canClearLlmKey(undefined), false);
});

test("decideQuotaBanner: displays banner when llm_key_configured is false", () => {
  const unconfiguredStatus = {
    state: "paired",
    status: {
      llm_key_configured: false,
      quota: { used: 0, limit: 50 },
    },
  };
  const decision1 = decideQuotaBanner(unconfiguredStatus);
  assert.equal(decision1.isNoKey, true);
  assert.equal(decision1.text, "请先在设置页填写 DeepSeek API Key");

  const configuredStatus = {
    state: "paired",
    status: {
      llm_key_configured: true,
      quota: { used: 0, limit: 50 },
    },
  };
  const decision2 = decideQuotaBanner(configuredStatus);
  assert.equal(decision2.isNoKey, false);

  const emptyStatus = {};
  const decision3 = decideQuotaBanner(emptyStatus);
  assert.equal(decision3.isNoKey, false);
});

test("computeLlmKeyConfigured: only returns false when paired and llm_key_configured is explicitly false", () => {
  // 1. 已配对且明确给出 llm_key_configured === false 时设为 false
  assert.equal(
    computeLlmKeyConfigured({
      state: "paired",
      status: { llm_key_configured: false },
    }),
    false,
  );

  // 2. 已配对且明确给出 llm_key_configured === true 时设为 true
  assert.equal(
    computeLlmKeyConfigured({
      state: "paired",
      status: { llm_key_configured: true },
    }),
    true,
  );

  // 3. 字段缺失时重置为 true（未知状态不显示"请先填写 Key"，由现有连接/配对提示负责）
  assert.equal(computeLlmKeyConfigured({ state: "paired" }), true);
  assert.equal(computeLlmKeyConfigured({ state: "paired", status: {} }), true);
  assert.equal(
    computeLlmKeyConfigured({ state: "paired", status: { llm_key_configured: undefined } }),
    true,
  );
  assert.equal(
    computeLlmKeyConfigured({ state: "paired", status: { llm_key_configured: null } }),
    true,
  );

  // 4. 未配对状态时重置为 true（即使响应体中含有 llm_key_configured: false）
  assert.equal(computeLlmKeyConfigured({ state: "unpaired" }), true);
  assert.equal(
    computeLlmKeyConfigured({
      state: "unpaired",
      status: { llm_key_configured: false },
    }),
    true,
  );

  // 5. status 请求失败、服务不可用时重置为 true
  assert.equal(
    computeLlmKeyConfigured({
      state: "failed",
      error: "network_error",
      status: { llm_key_configured: false },
    }),
    true,
  );
  assert.equal(computeLlmKeyConfigured({ state: "jet_down" }), true);

  // 6. 空值、未定义与非法对象安全重置为 true
  assert.equal(computeLlmKeyConfigured(null), true);
  assert.equal(computeLlmKeyConfigured(undefined), true);
  assert.equal(computeLlmKeyConfigured({}), true);
});

test("decideJobCardBadge: suppresses 判断失败 and displays no key message when unconfigured", () => {
  // When llmKeyConfigured is false
  const badge1 = decideJobCardBadge({ verdict_label: "适合投递", verdict_tone: "green" }, false);
  assert.equal(badge1.isNoKey, true);
  assert.equal(badge1.text, "请先在设置页填写 DeepSeek API Key");

  // When detail indicates failed or no_llm_key
  const badge2 = decideJobCardBadge(
    { view_state: "no_llm_key", label: "判断失败", status_label: "判断失败" },
    true,
  );
  assert.equal(badge2.isNoKey, true);
  assert.equal(badge2.text, "请先在设置页填写 DeepSeek API Key");

  // When detail is normal and llmKeyConfigured is true
  const badge3 = decideJobCardBadge(
    { verdict_label: "适合投递", verdict_tone: "green", view_state: "done_apply" },
    true,
  );
  assert.equal(badge3.isNoKey, false);
  assert.equal(badge3.text, "适合投递");
  assert.equal(badge3.tone, "green");

  // When detail is null
  const badge4 = decideJobCardBadge(null, true);
  assert.deepEqual(badge4, { isNoKey: false, text: null });
});

test("decideJobCardBadge: 未判断岗位在已配置 Key 时返回对象且 isNoKey 为 false，未配置 Key 时 isNoKey 为 true", () => {
  const unjudgedJob = {
    platform_job_id: "job_unjudged_test",
    title: "Python 工程师",
    in_library: true,
  };

  // 未判断岗位 + 已配置 Key 时返回对象且 isNoKey 为 false
  const resConfigured = decideJobCardBadge(unjudgedJob, true);
  assert.ok(typeof resConfigured === "object" && resConfigured !== null);
  assert.equal(resConfigured.isNoKey, false);
  assert.equal(resConfigured.text, null);

  // 未配置 Key 时 isNoKey 为 true
  const resUnconfigured = decideJobCardBadge(unjudgedJob, false);
  assert.ok(typeof resUnconfigured === "object" && resUnconfigured !== null);
  assert.equal(resUnconfigured.isNoKey, true);
  assert.equal(resUnconfigured.text, "请先在设置页填写 DeepSeek API Key");
});

test("formatChatError: translates no_llm_key to standard guide prompt", () => {
  assert.equal(
    formatChatError({ error: "no_llm_key" }),
    "请先在设置页填写 DeepSeek API Key",
  );
  assert.equal(
    formatChatError({ error: "quota_exhausted" }),
    "今日生成次数已用完",
  );
});

test("options.html DOM structure: contains llm-key card at top with password input and action buttons", () => {
  const htmlPath = path.resolve(__dirname, "../src/options.html");
  const html = fs.readFileSync(htmlPath, "utf-8");

  assert.ok(html.includes('id="llm-key-section"'), "Missing #llm-key-section");
  assert.ok(html.includes('id="llm-key-input"'), "Missing #llm-key-input");
  assert.ok(html.includes('type="password"'), "Input must be type=password");
  assert.ok(html.includes('id="llm-key-save-btn"'), "Missing #llm-key-save-btn");
  assert.ok(html.includes('id="llm-key-test-btn"'), "Missing #llm-key-test-btn");
  assert.ok(html.includes('id="llm-key-clear-btn"'), "Missing #llm-key-clear-btn");
  assert.ok(html.includes('id="llm-key-status"'), "Missing #llm-key-status");
  assert.ok(html.includes('id="llm-key-source"'), "Missing #llm-key-source");
  assert.ok(html.includes('id="llm-key-msg"'), "Missing #llm-key-msg");

  const keySecIdx = html.indexOf('id="llm-key-section"');
  const marksSecIdx = html.indexOf('id="marks-section"');
  assert.ok(
    keySecIdx < marksSecIdx,
    "#llm-key-section must be placed above #marks-section (at top of settings cards)",
  );
});
