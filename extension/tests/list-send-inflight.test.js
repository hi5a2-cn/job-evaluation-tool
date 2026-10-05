// 体检第 38 条：同一页岗位第一次上报还没返回时页面又被读取，不再重复上报、重复预判；上报失败仍会重试
import test from "node:test";
import assert from "node:assert/strict";
import { tabStateMap, sendListObservations, jetClient, setPrejudgeQuotaExhaustedDate } from "../src/background.js";

const origChrome = globalThis.chrome;
const origCall = jetClient.call;
const TAB_ID = 9381;

function setup() {
  globalThis.chrome = {
    storage: { local: { get: async () => ({ marksEnabled: true }) } },
    tabs: { sendMessage: async () => {} },
    runtime: { sendMessage: async () => {} },
  };
  const tabState = {
    tabId: TAB_ID,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(TAB_ID, tabState);
  return tabState;
}

test.afterEach(() => {
  const state = tabStateMap.get(TAB_ID);
  if (state?.pollTimer) clearInterval(state.pollTimer);
  if (state?.listPollTimer) clearTimeout(state.listPollTimer);
  tabStateMap.delete(TAB_ID);
  if (origChrome === undefined) delete globalThis.chrome;
  else globalThis.chrome = origChrome;
  jetClient.call = origCall;
  setPrejudgeQuotaExhaustedDate(null);
});

const readResult = {
  page_kind: "search_list",
  list: {
    ok: true,
    jobs: [
      { platform_job_id: "job_a", title: "后端开发", company_name: "公司A", city: "深圳" },
      { platform_job_id: "job_b", title: "数据分析", company_name: "公司B", city: "深圳" },
    ],
  },
};

const okObservations = { ok: true, data: { jobs: { job_a: { judgement: null }, job_b: { judgement: null } } } };
const okPrejudge = {
  ok: true,
  data: { status: "ok", prejudgements: { job_a: { level: "open", reason: "方向契合" } } },
};

test("上报还没返回时再次读取同一页：只上报一次、只预判一次", async () => {
  const tabState = setup();
  const calls = [];
  let releaseObservations;
  const slowObservations = new Promise((resolve) => {
    releaseObservations = () => resolve(okObservations);
  });
  jetClient.call = async (method, path) => {
    calls.push(path);
    if (path === "/v1/observations") return slowObservations;
    if (path === "/v1/prejudge") return okPrejudge;
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  const first = sendListObservations(tabState, readResult, TAB_ID);
  // 第一次上报挂起期间，页面重读一次；两次都发出后再放行上报，旧代码会在这里多发一次而不是卡住
  await new Promise((resolve) => setTimeout(resolve, 5));
  const second = sendListObservations(tabState, readResult, TAB_ID);
  await new Promise((resolve) => setTimeout(resolve, 5));
  releaseObservations();
  await Promise.all([first, second]);

  assert.equal(calls.filter((p) => p === "/v1/observations").length, 1);
  assert.equal(calls.filter((p) => p === "/v1/prejudge").length, 1);
});

test("上报返回失败：撤掉已上报标记，下次读取会重新上报", async () => {
  const tabState = setup();
  const calls = [];
  let fail = true;
  jetClient.call = async (method, path) => {
    calls.push(path);
    if (path === "/v1/observations") return fail ? { ok: false, error: "jet_down" } : okObservations;
    if (path === "/v1/prejudge") return okPrejudge;
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(tabState.listSent.size, 0);

  fail = false;
  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(calls.filter((p) => p === "/v1/observations").length, 2);
  assert.equal(tabState.listSent.size, 2);
});

test("上报抛出异常：撤掉已上报标记，下次读取会重新上报", async () => {
  const tabState = setup();
  let throwOnce = true;
  const calls = [];
  jetClient.call = async (method, path) => {
    calls.push(path);
    if (path === "/v1/observations") {
      if (throwOnce) {
        throwOnce = false;
        throw new Error("Failed to fetch");
      }
      return okObservations;
    }
    if (path === "/v1/prejudge") return okPrejudge;
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(tabState.listSent.size, 0);
  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(calls.filter((p) => p === "/v1/observations").length, 2);
});

test("上报成功后的后续处理出错：已上报标记保留，不会重新上报", async () => {
  const tabState = setup();
  // 上报成功后读取「是否显示标记」时出错（例如插件刚被重新加载），错误落到外层 catch
  globalThis.chrome.storage.local.get = async () => {
    throw new Error("Extension context invalidated");
  };
  const calls = [];
  jetClient.call = async (method, path) => {
    calls.push(path);
    if (path === "/v1/observations") return okObservations;
    if (path === "/v1/prejudge") return okPrejudge;
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(tabState.listSent.size, 2);
  await sendListObservations(tabState, readResult, TAB_ID);
  assert.equal(calls.filter((p) => p === "/v1/observations").length, 1);
});
