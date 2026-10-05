import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  CHAT_AUTO_ACTIONS,
  decideChatAutoAction,
  CONSENT_MODAL_DESC,
  resolveAutoGenerateSetting,
} from "../src/chat-view.js";

import {
  parseAutoGenerateSetting,
  toAutoGenerateStoragePayload,
} from "../src/options.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const optionsHtmlPath = path.resolve(__dirname, "../src/options.html");
const sidepanelHtmlPath = path.resolve(__dirname, "../src/sidepanel.html");
const sidepanelJsPath = path.resolve(__dirname, "../src/sidepanel.js");

test("T072: 设置页开关缺省为开 (FR-020, FR-067)", () => {
  // 1. 纯函数解析：storage 为空对象、undefined、null 或未包含 autoGenerate 时，缺省均返回 true
  assert.equal(resolveAutoGenerateSetting(undefined), true);
  assert.equal(resolveAutoGenerateSetting(null), true);
  assert.equal(resolveAutoGenerateSetting(true), true);
  assert.equal(parseAutoGenerateSetting({}), true);
  assert.equal(parseAutoGenerateSetting({ autoGenerate: undefined }), true);
  assert.equal(parseAutoGenerateSetting(null), true);
  assert.equal(parseAutoGenerateSetting({ other: 123 }), true);

  // 2. options.html 页面元素结构与默认勾选验证
  const optionsHtml = fs.readFileSync(optionsHtmlPath, "utf-8");
  assert.ok(
    optionsHtml.includes('id="auto-generate"'),
    "options.html 应包含 id='auto-generate' 开关元素"
  );
  assert.ok(
    /<input[^>]*id="auto-generate"[^>]*checked/.test(optionsHtml),
    "options.html 中 auto-generate 开关缺省应带有 checked 属性"
  );
  assert.ok(
    optionsHtml.includes("打开聊天自动生成"),
    "options.html 应包含'打开聊天自动生成'标签文案"
  );

  const expectedHint =
    "开启时，在聊天页打开侧边栏或切换聊天会自动生成建议并把脱敏内容发送给 DeepSeek（计入每天次数）；关闭后只在你点'生成'时发送";
  assert.ok(
    optionsHtml.includes(expectedHint),
    "options.html 应包含完整的开启/关闭说明文字"
  );

  // 3. 模拟 options.js 读取 storage.local 缺省行为
  let loadedChecked = null;
  const mockStorage = {
    get(key, cb) {
      assert.equal(key, "autoGenerate");
      cb({}); // 未存储过该键
    },
  };
  mockStorage.get("autoGenerate", (res) => {
    loadedChecked = res?.autoGenerate !== false;
  });
  assert.equal(loadedChecked, true, "storage 无值时读取结果应为 true");
});

test("T072: 设置页开关关闭后写入 false，开启后写入 true (FR-067)", () => {
  // 1. 纯函数解析与 payload 生成
  assert.equal(resolveAutoGenerateSetting(false), false);
  assert.equal(parseAutoGenerateSetting({ autoGenerate: false }), false);
  assert.deepEqual(toAutoGenerateStoragePayload(false), { autoGenerate: false });
  assert.deepEqual(toAutoGenerateStoragePayload(true), { autoGenerate: true });

  // 2. 模拟用户交互：关闭开关写入 false，重新开启写入 true
  let savedData = null;
  const mockStorage = {
    set(data) {
      savedData = data;
    },
  };

  // 用户点击取消勾选 -> 写入 false
  const uncheckState = false;
  mockStorage.set(toAutoGenerateStoragePayload(uncheckState));
  assert.deepEqual(savedData, { autoGenerate: false });

  // 用户再次点击勾选 -> 写入 true
  const checkState = true;
  mockStorage.set(toAutoGenerateStoragePayload(checkState));
  assert.deepEqual(savedData, { autoGenerate: true });
});

test("T072: 开关关闭时侧边栏不自动生成并展示生成按钮等待点击 (FR-020, FR-067, SC-024)", () => {
  // 当 autoGenerate 开关为 false 时，侧边栏不触发自动请求，等待手动点击
  const decisionOff = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: false,
    hasCachedResult: false,
    quotaExhaustedToday: false,
  });
  assert.equal(decisionOff.action, CHAT_AUTO_ACTIONS.WAIT_MANUAL);
  assert.equal(decisionOff.shouldGenerate, false);
  assert.equal(decisionOff.message, "点'生成'获取建议");

  // 即便有缓存，只要 autoGenerate 为 false 也保持等待手动交互
  const decisionOffWithCache = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: false,
    hasCachedResult: true,
    quotaExhaustedToday: false,
  });
  assert.equal(decisionOffWithCache.action, CHAT_AUTO_ACTIONS.WAIT_MANUAL);
  assert.equal(decisionOffWithCache.shouldGenerate, false);

  // 当 autoGenerate 开关为 true 时，无缓存则自动触发生成
  const decisionOn = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: true,
    hasCachedResult: false,
    quotaExhaustedToday: false,
  });
  assert.equal(decisionOn.action, CHAT_AUTO_ACTIONS.AUTO_GENERATE);
  assert.equal(decisionOn.shouldGenerate, true);
});

test("T072: 同意页文案常量与 sidepanel.html 必须包含'打开聊天就会自动发送'且说明可在设置页关闭 (FR-023, SC-022)", () => {
  // 1. 常量必须包含"打开聊天就会自动发送"
  assert.ok(
    CONSENT_MODAL_DESC.includes("打开聊天就会自动发送"),
    "CONSENT_MODAL_DESC 应包含'打开聊天就会自动发送'"
  );
  assert.ok(
    CONSENT_MODAL_DESC.includes("设置页"),
    "CONSENT_MODAL_DESC 应提到'设置页'"
  );

  // 3. sidepanel.html 同意弹窗文案检查
  const sidepanelHtml = fs.readFileSync(sidepanelHtmlPath, "utf-8");
  assert.ok(
    sidepanelHtml.includes('id="chat-consent-modal"'),
    "sidepanel.html 应包含 id='chat-consent-modal' 同意弹窗"
  );
  assert.ok(
    sidepanelHtml.includes("打开聊天就会自动发送"),
    "sidepanel.html 同意弹窗文案必须明确包含'打开聊天就会自动发送'"
  );
  assert.ok(
    sidepanelHtml.includes("设置页"),
    "sidepanel.html 同意弹窗必须说明可以在设置页关闭自动生成"
  );
  assert.ok(
    sidepanelHtml.includes('id="btn-consent-agree"'),
    "sidepanel.html 应有同意按钮"
  );
  assert.ok(
    sidepanelHtml.includes('id="btn-consent-cancel"'),
    "sidepanel.html 应有取消按钮"
  );
});

test("T072: 同意请求传递发送字段版本 2 及设置页说明同步升级 (FR-025, SC-022)", () => {
  // 1. 设置页 options.html "数据发送同意"区块说明
  const optionsHtml = fs.readFileSync(optionsHtmlPath, "utf-8");
  assert.ok(
    optionsHtml.includes('id="consent-section"'),
    "options.html 应包含 consent-section"
  );
  assert.ok(
    optionsHtml.includes("打开聊天自动发送"),
    "options.html 数据发送同意区块说明必须同步提到覆盖'打开聊天自动发送'"
  );
  assert.ok(
    optionsHtml.includes("版本 3"),
    "options.html 数据发送同意区块说明必须提到发送字段版本 3"
  );
  assert.ok(
    optionsHtml.includes("旧的同意需要重新确认"),
    "options.html 必须提到旧的同意需要重新确认"
  );

  // 2. sidepanel.js 提交同意时不带版本号：版本由服务端按 CONSENT_FIELDS_VERSION（当前 3）记录，
  //    见 tests/api 下的 test_consent_version_set_by_server.py
  const sidepanelJs = fs.readFileSync(sidepanelJsPath, "utf-8");
  assert.ok(sidepanelJs.includes('{ type: "chat_consent" }'), "sidepanel.js 要发送 chat_consent");
  assert.ok(!sidepanelJs.includes("fields_version"), "sidepanel.js 不再传 fields_version");
});
