import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { setupSidePanelBehavior } from "../src/background.js";
import { openMyJobsTab } from "../src/chat-view.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const backgroundJsPath = path.resolve(__dirname, "../src/background.js");
const sidepanelHtmlPath = path.resolve(__dirname, "../src/sidepanel.html");
const sidepanelJsPath = path.resolve(__dirname, "../src/sidepanel.js");
const chatViewJsPath = path.resolve(__dirname, "../src/chat-view.js");

test("T052 (a): background 启动及生命周期调用 chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }) 且异常时不抛出未捕获错误 (FR-061)", async () => {
  // 1. 正常调用：传入 openPanelOnActionClick: true
  let calledOptions = null;
  const mockChrome = {
    sidePanel: {
      async setPanelBehavior(options) {
        calledOptions = options;
      },
    },
  };
  await setupSidePanelBehavior(mockChrome);
  assert.deepEqual(calledOptions, { openPanelOnActionClick: true });

  // 2. 异常捕获：setPanelBehavior 抛错或 reject 时不抛出未捕获异常
  const failingChrome = {
    sidePanel: {
      async setPanelBehavior() {
        throw new Error("setPanelBehavior failed");
      },
    },
  };
  await assert.doesNotReject(async () => {
    await setupSidePanelBehavior(failingChrome);
  });

  // 3. 边界情况：chrome 未定义或 sidePanel / setPanelBehavior 不存在时安全退出
  await assert.doesNotReject(async () => {
    await setupSidePanelBehavior(null);
    await setupSidePanelBehavior(undefined);
    await setupSidePanelBehavior({});
    await setupSidePanelBehavior({ sidePanel: {} });
  });
});

// 体检第 87 条：原来这里检查 background.js 源码里有没有某些字样，改为真正加载 background.js 看它做了什么
test("T052 (a2)/(b): background 加载、安装、启动时都设置侧边栏行为；不注册点图标打开岗位库的监听 (FR-061, FR-063)", async () => {
  const panelCalls = [];
  const listeners = { installed: [], startup: [] };
  const actionClickListeners = [];
  const createdTabs = [];
  const origChrome = globalThis.chrome;
  globalThis.chrome = {
    sidePanel: { setPanelBehavior: async (options) => { panelCalls.push(options); } },
    action: { onClicked: { addListener: (fn) => { actionClickListeners.push(fn); } } },
    runtime: {
      sendMessage: async () => {},
      onMessage: { addListener: () => {} },
      onInstalled: { addListener: (fn) => { listeners.installed.push(fn); } },
      onStartup: { addListener: (fn) => { listeners.startup.push(fn); } },
    },
    storage: {
      local: { get: async () => ({}), set: async () => {}, remove: async () => {} },
      session: { get: async () => ({}), set: async () => {}, remove: async () => {} },
      onChanged: { addListener: () => {} },
    },
    tabs: {
      query: async () => [],
      create: async (opts) => { createdTabs.push(opts); },
      sendMessage: async () => {},
      onRemoved: { addListener: () => {} },
    },
  };
  try {
    // 带查询串重新加载一份 background.js，让顶层代码在上面的假 chrome 下执行
    await import("../src/background.js?toolbar-lifecycle");
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(panelCalls, [{ openPanelOnActionClick: true }], "加载时设置一次");

    assert.equal(listeners.installed.length, 1);
    assert.equal(listeners.startup.length, 1);
    listeners.installed[0]({ reason: "update" });
    listeners.startup[0]();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(panelCalls.length, 3, "安装 / 更新和浏览器启动时各再设置一次");
    assert.ok(panelCalls.every((o) => o.openPanelOnActionClick === true));

    assert.equal(actionClickListeners.length, 0, "不得注册 chrome.action.onClicked（点图标由侧边栏接管）");
    assert.equal(createdTabs.length, 0, "加载和生命周期事件都不得自动打开岗位库");
  } finally {
    globalThis.chrome = origChrome;
  }
});

test("T052 (c): 侧边栏操作区增加「我的岗位库」按钮且点击时调用 chrome.tabs.create (FR-062, SC-020)", () => {
  // 1. sidepanel.html 必须包含「我的岗位库」按钮与对应 id
  const sidepanelHtml = fs.readFileSync(sidepanelHtmlPath, "utf-8");
  assert.ok(
    sidepanelHtml.includes("我的岗位库"),
    "sidepanel.html 界面中必须包含「我的岗位库」按钮文本"
  );
  assert.ok(
    sidepanelHtml.includes('id="btn-open-myjobs"'),
    "sidepanel.html 必须包含 id='btn-open-myjobs' 元素"
  );

  // 2. 点击函数纯逻辑调用：传入 mock chrome 对象，正确调用 tabs.create
  let createdUrl = null;
  const mockChrome = {
    runtime: {
      getURL(pathStr) {
        return `chrome-extension://mock-id/${pathStr}`;
      },
    },
    tabs: {
      create(opts) {
        createdUrl = opts?.url;
      },
    },
  };

  openMyJobsTab(mockChrome);
  assert.equal(createdUrl, "chrome-extension://mock-id/src/myjobs.html");

  // 3. sidepanel.js 必须将 btn-open-myjobs 点击事件绑定到 openMyJobsTab
  const sidepanelJs = fs.readFileSync(sidepanelJsPath, "utf-8");
  const chatViewJs = fs.readFileSync(chatViewJsPath, "utf-8");
  assert.ok(
    sidepanelJs.includes("btn-open-myjobs"),
    "sidepanel.js 必须获取并绑定 btn-open-myjobs 按钮"
  );
  assert.ok(
    sidepanelJs.includes("openMyJobsTab"),
    "sidepanel.js 必须绑定 openMyJobsTab"
  );
  assert.ok(
    chatViewJs.includes("src/myjobs.html"),
    "chat-view.js 中 openMyJobsTab 打开目标必须为 src/myjobs.html"
  );
});
