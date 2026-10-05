import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { JSDOM } from "jsdom";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sidepanelHtmlPath = path.resolve(__dirname, "../../src/sidepanel.html");
const sidepanelJsPath = path.resolve(__dirname, "../../src/sidepanel.js");

let moduleInstanceCounter = 0;

/**
 * 等待若干个微任务与 setImmediate 轮次，避免固定 sleep。
 *
 * @param {number} [turns=5] 轮次
 */
export async function flushAsync(turns = 5) {
  for (let i = 0; i < turns; i++) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

/**
 * 等待特定条件达成或超时
 */
export async function waitFor(predicate, { timeout = 2000, maxTurns = 100 } = {}) {
  const start = Date.now();
  let turns = 0;
  while (true) {
    await flushAsync(1);
    turns++;
    if (predicate()) {
      return true;
    }
    if (Date.now() - start > timeout || turns > maxTurns) {
      throw new Error(`Timed out waiting for condition after ${turns} turns (${Date.now() - start}ms)`);
    }
  }
}

/**
 * 创建并初始化侧边栏测试环境。
 *
 * @param {object} [options={}]
 * @param {object} [options.handlers] 自定义 chrome.runtime.sendMessage 处理器表
 * @param {object} [options.storage] 初始 chrome.storage.local 数据
 * @param {object} [options.activeTab] 当前活跃标签页对象
 * @returns {Promise<object>}
 */
export async function setupSidepanelEnv(options = {}) {
  const globalKeys = [
    "window",
    "document",
    "navigator",
    "chrome",
    "HTMLElement",
    "Element",
    "Node",
    "Event",
    "CustomEvent",
    "setInterval",
    "clearInterval",
  ];

  const savedDescriptors = new Map();
  for (const key of globalKeys) {
    savedDescriptors.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
  }

  function setGlobal(key, val) {
    Object.defineProperty(globalThis, key, {
      value: val,
      configurable: true,
      writable: true,
      enumerable: true,
    });
  }

  const activeIntervals = new Set();
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;

  setGlobal("setInterval", function (fn, ms, ...args) {
    const timer = origSetInterval(fn, ms, ...args);
    if (timer && typeof timer.unref === "function") {
      timer.unref();
    }
    activeIntervals.add(timer);
    return timer;
  });

  setGlobal("clearInterval", function (timer) {
    activeIntervals.delete(timer);
    return origClearInterval(timer);
  });

  // 读取真实的 sidepanel.html
  const rawHtml = fs.readFileSync(sidepanelHtmlPath, "utf-8");
  const dom = new JSDOM(rawHtml, {
    url: "chrome-extension://mock-sidepanel-id/src/sidepanel.html",
  });

  const win = dom.window;
  const doc = win.document;

  // 剪贴板调用记录与模拟
  const clipboardCalls = [];
  let clipboardError = null;
  const mockClipboard = {
    writeText: async (text) => {
      if (clipboardError) {
        throw clipboardError;
      }
      clipboardCalls.push(text);
    },
  };

  Object.defineProperty(win.navigator, "clipboard", {
    value: mockClipboard,
    configurable: true,
    writable: true,
  });

  // 兜底支持 textarea.select 与 execCommand
  if (!doc.execCommand) {
    doc.execCommand = () => true;
  }
  if (!win.HTMLTextAreaElement.prototype.select) {
    win.HTMLTextAreaElement.prototype.select = () => {};
  }

  // 假 chrome API
  const sentMessages = [];
  const messageListeners = [];
  const tabActivatedListeners = [];
  const storageChangedListeners = [];
  const tabCreateCalls = [];

  const defaultStorage = {
    autoGenerate: true,
    ...(options.storage || {}),
  };
  const localStorageData = { ...defaultStorage };

  const activeTab = options.activeTab || {
    id: 101,
    url: "https://www.zhipin.com/web/geek/chat",
  };

  const getEffectiveJobId = (explicitJobId) => {
    if (explicitJobId) return explicitJobId;
    const rcp = handlers.read_chat_page;
    if (typeof rcp === "function") {
      try {
        const res = rcp();
        if (res?.data?.encrypt_job_id) return res.data.encrypt_job_id;
      } catch {
        // ignore
      }
    } else if (rcp?.data?.encrypt_job_id) {
      return rcp.data.encrypt_job_id;
    }
    return "mock_job_default";
  };

  const defaultHandlers = {
    get_status: () => ({
      ok: true,
      state: "paired",
      status: {
        quota: { limit: 100, remaining: 100, used: 0 },
        assist_quota: { limit: 50, remaining: 50, used: 0 },
        llm_key_configured: true,
      },
    }),
    is_chat_page: () => ({
      ok: true,
      is_chat_page: true,
    }),
    get_page_summary: () => ({
      ok: true,
      marks_enabled: true,
      summary: null,
      timings: null,
      updated_at: null,
      chat_job_status: null,
    }),
    get_chat_job_status: (msg) => {
      const jobId = getEffectiveJobId(msg.jobId);
      return {
        ok: true,
        jobId,
        jobStatus: {
          platform_job_id: jobId,
          in_library: true,
          title: "高级前端开发",
          company_name: "测试科技有限公司",
        },
      };
    },
    read_chat_page: () => ({
      ok: true,
      data: {
        encrypt_job_id: "mock_job_default",
        company_name: "测试科技有限公司",
        job_title: "高级前端开发",
        messages: [
          { sender: "HR", is_self: false, text: "您好，方便发一份简历吗？", time: 1000 },
        ],
      },
    }),
    read_chat_job_id: () => ({
      ok: true,
      encrypt_job_id: getEffectiveJobId(),
    }),
    chat_preview: () => ({
      ok: true,
      data: {
        has_consent: true,
        prompt_hash: "mock_hash_1",
        sanitized_prompt: "脱敏预览",
        mode: "reply",
        mode_label: "建议回复",
        mode_basis: "HR刚才打了个招呼",
        has_jet_judgement: true,
      },
    }),
    chat_consent: () => ({
      ok: true,
    }),
    chat_generate: () => ({
      ok: true,
      data: {
        mode: "reply",
        mode_label: "建议回复",
        suggestions: [
          { version: 1, text: "您好，已通过附件发送，请查收。" },
        ],
        questions: ["请问团队目前的技术栈是什么？"],
        quota_remaining: 49,
      },
    }),
  };

  const handlers = {
    ...defaultHandlers,
    ...(options.handlers || {}),
  };

  const fakeChrome = {
    runtime: {
      lastError: null,
      sendMessage: (message, callback) => {
        sentMessages.push(message);
        const handler = handlers[message?.type];
        setImmediate(async () => {
          let response;
          try {
            if (typeof handler === "function") {
              response = handler(message);
              if (response && typeof response.then === "function") {
                response = await response;
              }
            } else if (handler !== undefined) {
              response = handler;
            } else {
              response = { ok: false, error: `Unhandled message: ${message?.type}` };
            }
          } catch (err) {
            fakeChrome.runtime.lastError = err;
            response = { ok: false, error: err.message };
          }
          if (typeof callback === "function") {
            callback(response);
          }
        });
      },
      onMessage: {
        addListener: (fn) => {
          messageListeners.push(fn);
        },
      },
      getURL: (pathStr) => `chrome-extension://mock-sidepanel-id/${pathStr}`,
    },
    tabs: {
      query: async (queryInfo) => {
        if (typeof options.queryTabs === "function") {
          return options.queryTabs(queryInfo);
        }
        return [activeTab];
      },
      create: (createProperties) => {
        tabCreateCalls.push(createProperties);
        return Promise.resolve({ id: 999, ...createProperties });
      },
      onActivated: {
        addListener: (fn) => {
          tabActivatedListeners.push(fn);
        },
      },
    },
    storage: {
      local: {
        get: async (key, callback) => {
          if (typeof options.storageGet === "function") {
            try {
              const res = await options.storageGet(key);
              if (typeof callback === "function") callback(res);
              return res;
            } catch (err) {
              fakeChrome.runtime.lastError = err;
              if (typeof callback === "function") callback({});
              throw err;
            }
          }
          let res;
          if (typeof key === "string") {
            res = { [key]: localStorageData[key] };
          } else if (Array.isArray(key)) {
            res = {};
            for (const k of key) res[k] = localStorageData[k];
          } else if (key && typeof key === "object") {
            res = { ...key };
            for (const k of Object.keys(key)) {
              if (k in localStorageData) res[k] = localStorageData[k];
            }
          } else {
            res = { ...localStorageData };
          }
          if (typeof callback === "function") {
            setImmediate(() => callback(res));
          }
          return res;
        },
        set: async (items, callback) => {
          Object.assign(localStorageData, items);
          if (typeof callback === "function") {
            setImmediate(() => callback());
          }
        },
      },
      onChanged: {
        addListener: (fn) => {
          storageChangedListeners.push(fn);
        },
      },
    },
  };

  // 挂载到 globalThis
  setGlobal("window", win);
  setGlobal("document", doc);
  setGlobal("navigator", win.navigator);
  setGlobal("chrome", fakeChrome);
  setGlobal("HTMLElement", win.HTMLElement);
  setGlobal("Element", win.Element);
  setGlobal("Node", win.Node);
  setGlobal("Event", win.Event);
  setGlobal("CustomEvent", win.CustomEvent);

  /**
   * 动态 import sidepanel.js 并派发 DOMContentLoaded
   */
  async function loadSidepanel() {
    const uniqueUrl = `${pathToFileURL(sidepanelJsPath).href}?t=${Date.now()}_${++moduleInstanceCounter}`;
    const sidepanelModule = await import(uniqueUrl);
    doc.dispatchEvent(new win.Event("DOMContentLoaded"));
    await flushAsync(10);
    return sidepanelModule;
  }

  function cleanup() {
    for (const timer of activeIntervals) {
      origClearInterval(timer);
    }
    activeIntervals.clear();

    for (const [key, desc] of savedDescriptors) {
      if (desc) {
        Object.defineProperty(globalThis, key, desc);
      } else {
        delete globalThis[key];
      }
    }
  }

  return {
    dom,
    window: win,
    document: doc,
    navigator: win.navigator,
    fakeChrome,
    sentMessages,
    messageListeners,
    tabActivatedListeners,
    storageChangedListeners,
    tabCreateCalls,
    clipboardCalls,
    localStorageData,
    setClipboardError: (err) => {
      clipboardError = err;
    },
    setHandler: (type, fn) => {
      handlers[type] = fn;
    },
    clearMessages: () => {
      sentMessages.length = 0;
    },
    triggerMessage: (msg, sender = { tab: activeTab }) => {
      for (const listener of messageListeners) {
        listener(msg, sender);
      }
    },
    triggerActivated: (activeInfo = { tabId: activeTab.id }) => {
      for (const listener of tabActivatedListeners) {
        listener(activeInfo);
      }
    },
    triggerStorageChanged: (changes, area = "local") => {
      for (const listener of storageChangedListeners) {
        listener(changes, area);
      }
    },
    flushAsync,
    waitFor,
    loadSidepanel,
    cleanup,
  };
}
