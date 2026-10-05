import test from "node:test";
import assert from "node:assert/strict";

// 全局模拟 chrome API（在导入 background.js 之前注入）
let fetchCalled = false;
globalThis.fetch = async (...args) => {
  fetchCalled = true;
  throw new Error(`Unexpected fetch call: ${JSON.stringify(args)}`);
};

const chromeStorage = {
  local: {
    get: async () => ({ marksEnabled: true }),
    set: async () => {},
  },
  session: {
    get: async () => ({}),
    set: async () => {},
    remove: async () => {},
  },
  onChanged: {
    addListener: () => {},
  },
};

const sentTabMessages = [];
const chromeTabs = {
  sendMessage: async (tabId, msg) => {
    sentTabMessages.push({ tabId, msg });
  },
  onRemoved: {
    addListener: () => {},
  },
};

const chromeRuntime = {
  sendMessage: async () => {},
  onMessage: {
    addListener: () => {},
  },
};

globalThis.chrome = {
  storage: chromeStorage,
  tabs: chromeTabs,
  runtime: chromeRuntime,
  sidePanel: {
    setPanelBehavior: async () => {},
  },
};

// 导入要测试的模块
import {
  tabStateMap,
  getOrCreateTabState,
  clearListPollTimer,
  ensureListPolling,
  updateTabJudgement,
  sendRenderState,
  handleSendDetail,
} from "../src/background.js";
import {
  getPendingJobIds,
  hasEntryChanged,
  buildListPollUrl,
  runListPollIteration,
  shouldKeepPolling,
} from "../src/scheduler.js";
import { buildListMarks } from "../src/page-summary.js";

function createMockClient() {
  const calls = [];
  const db = {
    jobs: {},
  };

  const client = {
    calls,
    db,
    async call(method, path, body) {
      calls.push({ method, path, body });
      if (path === "/v1/observations") {
        const job = body?.jobs?.[0];
        const pid = job?.platform_job_id;
        const entry = db.jobs[pid] || {
          platform_job_id: pid,
          title: job?.title || "",
          judgement: { status: "running" },
          my_status: null,
          company_name: job?.company_name || null,
        };
        return {
          ok: true,
          status: 200,
          data: {
            jobs: { [pid]: entry },
          },
        };
      }
      if (path.startsWith("/v1/judgements?ids=")) {
        const queryStr = path.slice("/v1/judgements?ids=".length);
        const ids = queryStr.split(",").map((s) => decodeURIComponent(s.trim()));
        const returned = {};
        for (const id of ids) {
          if (db.jobs[id]) {
            returned[id] = db.jobs[id];
          }
        }
        return {
          ok: true,
          status: 200,
          data: { jobs: returned },
        };
      }
      return { ok: false, status: 404, error: "not_found" };
    },
  };
  return client;
}

test("连点多个岗位：岗位 1、2、3 依次进入详情，随后轮询得到 done，列表生成结论标记且当前详情仍是岗位 3", async () => {
  const tabId = 201;
  const tabState = getOrCreateTabState(tabId);
  tabState.listJobs = [
    { platform_job_id: "job_1", title: "岗位1" },
    { platform_job_id: "job_2", title: "岗位2" },
    { platform_job_id: "job_3", title: "岗位3" },
  ];

  const client = createMockClient();
  client.db.jobs["job_1"] = {
    platform_job_id: "job_1",
    title: "岗位1",
    judgement: { status: "running" },
    my_status: null,
    company_name: "公司A",
  };
  client.db.jobs["job_2"] = {
    platform_job_id: "job_2",
    title: "岗位2",
    judgement: { status: "running" },
    my_status: null,
    company_name: "公司B",
  };
  client.db.jobs["job_3"] = {
    platform_job_id: "job_3",
    title: "岗位3",
    judgement: { status: "running" },
    my_status: null,
    company_name: "公司C",
  };

  // 1. 依次进入详情 1 -> 2 -> 3
  await handleSendDetail(tabId, tabState, {
    page_kind: "search_list",
    detail: { ok: true, job: { platform_job_id: "job_1", title: "岗位1" } },
  }, true, 0, 0, client);

  await handleSendDetail(tabId, tabState, {
    page_kind: "search_list",
    detail: { ok: true, job: { platform_job_id: "job_2", title: "岗位2" } },
  }, true, 0, 0, client);

  await handleSendDetail(tabId, tabState, {
    page_kind: "search_list",
    detail: { ok: true, job: { platform_job_id: "job_3", title: "岗位3" } },
  }, true, 0, 0, client);

  assert.equal(tabState.currentJobId, "job_3");
  assert.equal(tabState.listJudgements.get("job_1").judgement.status, "running");
  assert.equal(tabState.listJudgements.get("job_2").judgement.status, "running");
  assert.equal(tabState.listJudgements.get("job_3").judgement.status, "running");

  // 2. 假接口返回 1、2、3 都完成
  client.db.jobs["job_1"].judgement = {
    status: "done",
    verdict: "apply",
    judged_at: "2026-09-30T00:00:01Z",
  };
  client.db.jobs["job_2"].judgement = {
    status: "done",
    verdict: "try",
    judged_at: "2026-09-30T00:00:02Z",
  };
  client.db.jobs["job_3"].judgement = {
    status: "done",
    verdict: "check",
    judged_at: "2026-09-30T00:00:03Z",
  };

  sentTabMessages.length = 0; // 清空历史消息

  // 3. 运行列表轮询迭代
  const res = await runListPollIteration({
    tabId,
    tabState,
    client,
    marksEnabled: true,
    sendRender: (tId, tState, marks, detail) => sendRenderState(tId, tState, marks, detail),
    onStop: () => clearListPollTimer(tabState),
    updateJudgement: (tState, pid, j, extra) => updateTabJudgement(tState, pid, j, extra),
  });

  assert.equal(res.changed, true);

  // 4. 不需要点回去，listJudgements 里三者都变成 done
  assert.equal(tabState.listJudgements.get("job_1").judgement.status, "done");
  assert.equal(tabState.listJudgements.get("job_2").judgement.status, "done");
  assert.equal(tabState.listJudgements.get("job_3").judgement.status, "done");

  // 5. buildListMarks 为三者都生成结论标记（色条/标签）
  const marks = buildListMarks(tabState.listJudgements);
  assert.equal(marks["job_1"].verdict_label, "适合投递");
  assert.equal(marks["job_1"].verdict_tone, "green");
  assert.equal(marks["job_2"].verdict_label, "可以一试");
  assert.equal(marks["job_2"].verdict_tone, "blue");
  assert.equal(marks["job_3"].verdict_label, "需要确认");
  assert.equal(marks["job_3"].verdict_tone, "yellow");

  // 6. 当前详情仍是岗位 3
  assert.equal(tabState.currentJobId, "job_3");
  const lastMsg = sentTabMessages[sentTabMessages.length - 1];
  assert.ok(lastMsg);
  assert.equal(lastMsg.msg.detail.platform_job_id, "job_3");

  clearListPollTimer(tabState);
});

test("列表轮询不影响详情：岗位 1 完成时发给 content script 的 render_state.detail 仍是当前岗位", async () => {
  const tabId = 202;
  const tabState = getOrCreateTabState(tabId);
  tabState.currentJobId = "job_2";
  tabState.currentJobTitle = "岗位2详情标题";
  tabState.lastJobRender = {
    detail: {
      platform_job_id: "job_2",
      title: "岗位2详情标题",
      label: "可以一试",
    },
  };
  tabState.listJobs = [
    { platform_job_id: "job_1", title: "岗位1" },
    { platform_job_id: "job_2", title: "岗位2" },
  ];
  tabState.listJudgements.set("job_1", {
    judgement: { status: "running" },
    my_status: null,
    company_name: "公司1",
  });
  tabState.listJudgements.set("job_2", {
    judgement: { status: "done", verdict: "try" },
    my_status: null,
    company_name: "公司2",
  });

  const client = createMockClient();
  // 岗位 1 完成
  client.db.jobs["job_1"] = {
    platform_job_id: "job_1",
    title: "岗位1",
    judgement: { status: "done", verdict: "apply", judged_at: "2026-09-30T00:00:05Z" },
    my_status: null,
    company_name: "公司1",
  };

  sentTabMessages.length = 0;

  const result = await runListPollIteration({
    tabId,
    tabState,
    client,
    marksEnabled: true,
    sendRender: (tId, tState, marks, detail) => sendRenderState(tId, tState, marks, detail),
    onStop: () => clearListPollTimer(tabState),
    updateJudgement: (tState, pid, j, extra) => updateTabJudgement(tState, pid, j, extra),
  });

  assert.equal(result.changed, true);
  assert.equal(tabState.listJudgements.get("job_1").judgement.status, "done");

  const lastMsg = sentTabMessages[sentTabMessages.length - 1];
  assert.ok(lastMsg);
  // detail 仍是岗位 2，绝不能把岗位 1 渲染到岗位 2 的详情上
  assert.equal(lastMsg.msg.detail.platform_job_id, "job_2");
  assert.equal(lastMsg.msg.detail.title, "岗位2详情标题");
  // 但 list_marks 里岗位 1 已有结论
  assert.equal(lastMsg.msg.list_marks["job_1"].verdict_label, "适合投递");

  clearListPollTimer(tabState);
});

test("没有待完成岗位时定时器停止；请求失败（含401/异常）时停止；超时停止", async () => {
  const tabId = 203;
  const tabState = getOrCreateTabState(tabId);
  tabState.listJudgements.clear();

  let stopCalled = false;
  const onStop = () => {
    stopCalled = true;
    clearListPollTimer(tabState);
  };

  // 1. 没有待完成岗位，停止
  stopCalled = false;
  const r1 = await runListPollIteration({
    tabId,
    tabState,
    client: createMockClient(),
    onStop,
  });
  assert.equal(r1.stopped, true);
  assert.equal(r1.reason, "no_pending");
  assert.equal(stopCalled, true);

  // 2. 有待完成岗位，但请求返回 401 失败，停止
  tabState.listJudgements.set("job_err", {
    judgement: { status: "queued" },
  });
  const failClient = {
    call: async () => ({ ok: false, status: 401, error: "unauthorized" }),
  };
  stopCalled = false;
  const r2 = await runListPollIteration({
    tabId,
    tabState,
    client: failClient,
    onStop,
  });
  assert.equal(r2.stopped, true);
  assert.equal(r2.reason, "response_not_ok");
  assert.equal(stopCalled, true);

  // 3. 网络异常 / Jet 未运行抛出异常，停止
  const downClient = {
    call: async () => {
      throw new Error("Connection refused");
    },
  };
  stopCalled = false;
  const r3 = await runListPollIteration({
    tabId,
    tabState,
    client: downClient,
    onStop,
  });
  assert.equal(r3.stopped, true);
  assert.equal(r3.reason, "client_error");
  assert.equal(stopCalled, true);

  // 4. 超时 5 分钟，停止
  tabState.listPollStartedAt = 1000;
  stopCalled = false;
  const r4 = await runListPollIteration({
    tabId,
    tabState,
    client: createMockClient(),
    onStop,
    now: 1000 + 5 * 60 * 1000 + 1,
  });
  assert.equal(r4.stopped, true);
  assert.equal(r4.reason, "timeout");
  assert.equal(stopCalled, true);
});

test("所有 GET 请求都只发往 jetClient（断言没有其他 fetch）", async () => {
  // 确认在整个测试流程中全局 fetch 从未被调用
  assert.equal(fetchCalled, false, "fetch should never be called directly; only jetClient");
});
