import test from "node:test";
import assert from "node:assert/strict";
import {
  tabStateMap,
  sendListObservations,
  jetClient,
  getPrejudgeQuotaExhaustedDate,
  setPrejudgeQuotaExhaustedDate,
} from "../src/background.js";
import { localDateString } from "../src/scheduler.js";

const origChrome = globalThis.chrome;
const origFetch = globalThis.fetch;
const origCall = jetClient.call;

const activeTabIds = new Set();

function setupMockChrome({ onTabsSendMessage } = {}) {
  globalThis.chrome = {
    storage: {
      local: {
        get: async () => ({ marksEnabled: true }),
      },
    },
    tabs: {
      sendMessage: async (targetTabId, message) => {
        if (onTabsSendMessage) {
          onTabsSendMessage(targetTabId, message);
        }
      },
    },
    runtime: {
      sendMessage: async () => {},
    },
  };
}

function cleanupTabState(tabId) {
  const state = tabStateMap.get(tabId);
  if (state) {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.retryTimer) {
      clearTimeout(state.retryTimer);
      state.retryTimer = null;
    }
    if (state.chatSwitchRetryTimer) {
      clearTimeout(state.chatSwitchRetryTimer);
      state.chatSwitchRetryTimer = null;
    }
    if (state.listSent) state.listSent.clear();
    if (state.listJudgements) state.listJudgements.clear();
    if (state.listPrejudge) state.listPrejudge.clear();
    state.listJobs = [];
    tabStateMap.delete(tabId);
  }
  activeTabIds.delete(tabId);
  setPrejudgeQuotaExhaustedDate(null);
}

function restoreGlobals() {
  for (const tabId of Array.from(activeTabIds)) {
    cleanupTabState(tabId);
  }
  activeTabIds.clear();

  if (origChrome === undefined) {
    delete globalThis.chrome;
  } else {
    globalThis.chrome = origChrome;
  }

  if (origFetch === undefined) {
    delete globalThis.fetch;
  } else {
    globalThis.fetch = origFetch;
  }

  jetClient.call = origCall;
  setPrejudgeQuotaExhaustedDate(null);
}

test.afterEach(() => {
  restoreGlobals();
});

// -----------------------------------------------------------------------------
// 1. 列表观测成功后：先发出一次 render_state（含正式判断/提示标记），预判挂起；
//    预判返回后再发出一次 render_state，list_marks 里出现 type "prejudge" 的标记。
// -----------------------------------------------------------------------------
test("sendListObservations: 1. 列表观测成功后先发出 render_state（含正式判断/提示，预判仍挂起），预判返回后再发出含 type 'prejudge' 的标记", async () => {
  const tabId = 901;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  const renderMessages = [];
  setupMockChrome({
    onTabsSendMessage(targetTabId, message) {
      if (message.type === "render_state") {
        renderMessages.push(JSON.parse(JSON.stringify(message)));
      }
    },
  });

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs: [
        {
          platform_job_id: "job_done",
          title: "后端开发",
          company_name: "已判断公司",
          city: "深圳",
        },
        {
          platform_job_id: "job_hint",
          title: "测试开发",
          company_name: "提示公司",
          city: "深圳",
        },
        {
          platform_job_id: "job_prejudge",
          title: "前端开发",
          company_name: "预判公司",
          city: "深圳",
        },
      ],
    },
  };

  let resolvePrejudge;
  const pendingPrejudge = new Promise((resolve) => {
    resolvePrejudge = resolve;
  });

  jetClient.call = async (method, path, body) => {
    if (path === "/v1/observations") {
      return {
        ok: true,
        data: {
          jobs: {
            job_done: {
              judgement: { status: "done", verdict: "apply", judged_at: "2026-09-30T00:00:00Z" },
              screen_hints: [],
            },
            job_hint: {
              judgement: null,
              screen_hints: [{ type: "salary", text: "低于底线" }],
            },
            job_prejudge: {
              judgement: null,
              screen_hints: [],
            },
          },
        },
      };
    }
    if (path === "/v1/prejudge") {
      return pendingPrejudge;
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    const flowPromise = sendListObservations(tabState, readResult, tabId);

    // 等待微任务队列执行，使 observations 返回并触发第一次 render_state，随后挂起在预判请求
    await new Promise((resolve) => setTimeout(resolve, 20));

    // 1. 此时预判仍在挂起状态，已先发出一次 render_state
    assert.equal(renderMessages.length, 1, "预判挂起期间应先发出第一次 render_state");
    const firstMarks = renderMessages[0].list_marks;
    assert.ok(firstMarks.job_done, "第一次 render_state 应含正式判断标记");
    assert.equal(firstMarks.job_done.verdict_label, "适合投递");
    assert.equal(firstMarks.job_done.verdict_tone, "green");
    assert.equal(firstMarks.job_done.is_prejudge, undefined);

    assert.ok(firstMarks.job_hint, "第一次 render_state 应含粗筛提示标记");
    assert.equal(firstMarks.job_hint.is_hint, true);
    assert.equal(firstMarks.job_hint.verdict_label, "粗筛：低于底线");
    assert.equal(firstMarks.job_hint.verdict_tone, "slate");

    assert.equal(firstMarks.job_prejudge, undefined, "预判未返回前不应出现预判标记");

    // 2. 模拟预判请求返回
    resolvePrejudge({
      ok: true,
      data: {
        status: "ok",
        prejudgements: {
          job_prejudge: {
            level: "open",
            reason: "技术栈高匹配度",
          },
        },
      },
    });

    await flowPromise;

    // 3. 预判返回后再发出一次 render_state，list_marks 出现 type: "prejudge" 标记
    assert.equal(renderMessages.length, 2, "预判返回后应再发出一次 render_state");
    const secondMarks = renderMessages[1].list_marks;
    assert.ok(secondMarks.job_prejudge, "第二次 render_state 应包含 job_prejudge");
    assert.equal(secondMarks.job_prejudge.type, "prejudge", "预判标记 type 必须为 prejudge");
    assert.equal(secondMarks.job_prejudge.is_prejudge, true);
    assert.equal(secondMarks.job_prejudge.verdict_label, "预判·值得点开");
    assert.equal(secondMarks.job_prejudge.verdict_tone, "open");
    assert.equal(secondMarks.job_prejudge.reason, "技术栈高匹配度");

    // 正式判断与粗筛提示在第二次渲染中保留
    assert.equal(secondMarks.job_done.verdict_label, "适合投递");
    assert.equal(secondMarks.job_hint.verdict_label, "粗筛：低于底线");
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 2. 45 个新岗位 → 恰好两次 POST /v1/prejudge，分别 40 个和 5 个
// -----------------------------------------------------------------------------
test("sendListObservations: 2. 45 个新岗位恰好触发两次 POST /v1/prejudge，分别 40 个和 5 个", async () => {
  const tabId = 902;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  setupMockChrome();

  const jobs = Array.from({ length: 45 }, (_, i) => ({
    platform_job_id: `job_batch_${String(i + 1).padStart(2, "0")}`,
    title: `工程师 ${i + 1}`,
    company_name: `科技公司 ${i + 1}`,
    city: "北京",
  }));

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs,
    },
  };

  const prejudgeCalls = [];
  jetClient.call = async (method, path, body) => {
    if (path === "/v1/observations") {
      return { ok: true, data: { jobs: {} } };
    }
    if (path === "/v1/prejudge") {
      prejudgeCalls.push({ method, path, body: JSON.parse(JSON.stringify(body)) });
      return {
        ok: true,
        data: {
          status: "ok",
          prejudgements: {},
        },
      };
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    await sendListObservations(tabState, readResult, tabId);

    assert.equal(prejudgeCalls.length, 2, "45 个新岗位应恰好拆分为两次预判请求");
    assert.equal(prejudgeCalls[0].method, "POST");
    assert.equal(prejudgeCalls[0].path, "/v1/prejudge");
    assert.equal(prejudgeCalls[0].body.jobs.length, 40, "第一批恰好 40 个岗位");

    assert.equal(prejudgeCalls[1].method, "POST");
    assert.equal(prejudgeCalls[1].path, "/v1/prejudge");
    assert.equal(prejudgeCalls[1].body.jobs.length, 5, "第二批恰好 5 个岗位");

    assert.deepEqual(
      prejudgeCalls[0].body.jobs.map((j) => j.platform_job_id),
      jobs.slice(0, 40).map((j) => j.platform_job_id),
      "第一批岗位 ID 应对应前 40 个"
    );
    assert.deepEqual(
      prejudgeCalls[1].body.jobs.map((j) => j.platform_job_id),
      jobs.slice(40, 45).map((j) => j.platform_job_id),
      "第二批岗位 ID 应对应后 5 个"
    );
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 3. 第一批返回 status "quota_exhausted" → 不再发第二批；同一天后续列表也不再发预判
// -----------------------------------------------------------------------------
test("sendListObservations: 3. 第一批返回 status 'quota_exhausted' 时不发第二批，且同一天后续列表不再发预判", async () => {
  const tabId = 903;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  setupMockChrome();

  const jobs = Array.from({ length: 45 }, (_, i) => ({
    platform_job_id: `job_quota_${String(i + 1).padStart(2, "0")}`,
    title: `岗位 ${i + 1}`,
    company_name: `公司 ${i + 1}`,
    city: "上海",
  }));

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs,
    },
  };

  const prejudgeCalls = [];
  const observationCalls = [];
  jetClient.call = async (method, path, body) => {
    if (path === "/v1/observations") {
      observationCalls.push({ method, path, body });
      return { ok: true, data: { jobs: {} } };
    }
    if (path === "/v1/prejudge") {
      prejudgeCalls.push({ method, path, body });
      return {
        ok: true,
        data: {
          status: "quota_exhausted",
          prejudgements: {},
        },
      };
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    await sendListObservations(tabState, readResult, tabId);

    // 1. 第一批返回 quota_exhausted，不发第二批（5 个）
    assert.equal(prejudgeCalls.length, 1, "第一批返回 quota_exhausted 时不应发第二批");
    assert.equal(prejudgeCalls[0].body.jobs.length, 40);
    assert.equal(
      getPrejudgeQuotaExhaustedDate(),
      localDateString(),
      "应记录 prejudgeQuotaExhaustedDate 为今日日期"
    );

    // 2. 同一天后续列表读取
    const subsequentJobs = [
      { platform_job_id: "job_subseq_1", title: "后续岗位1", city: "广州" },
      { platform_job_id: "job_subseq_2", title: "后续岗位2", city: "深圳" },
    ];
    const subsequentReadResult = {
      page_kind: "search_list",
      list: { ok: true, jobs: subsequentJobs },
    };

    await sendListObservations(tabState, subsequentReadResult, tabId);

    assert.equal(observationCalls.length, 2, "后续列表观测正常发送");
    assert.equal(prejudgeCalls.length, 1, "同一天后续列表观测不再发送预判请求");
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 4. 已有正式判断（done）岗位不进入预判；已在 listPrejudge 中的岗位不重复发送
// -----------------------------------------------------------------------------
test("sendListObservations: 4. 已有正式判断（done）岗位不进入预判；已在 listPrejudge 中的岗位不重复发送", async () => {
  const tabId = 904;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map([
      ["job_already_prejudged", { level: "open", reason: "已有预判缓存" }],
    ]),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  setupMockChrome();

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs: [
        { platform_job_id: "job_done", title: "岗位1", city: "深圳" },
        { platform_job_id: "job_already_prejudged", title: "岗位2", city: "深圳" },
        { platform_job_id: "job_running", title: "岗位3", city: "深圳" },
        { platform_job_id: "job_fresh", title: "岗位4", city: "深圳" },
      ],
    },
  };

  const prejudgePayloads = [];
  jetClient.call = async (method, path, body) => {
    if (path === "/v1/observations") {
      return {
        ok: true,
        data: {
          jobs: {
            job_done: {
              judgement: { status: "done", verdict: "apply" },
            },
            job_already_prejudged: {
              judgement: null,
            },
            job_running: {
              judgement: { status: "running" },
            },
            job_fresh: {
              judgement: null,
            },
          },
        },
      };
    }
    if (path === "/v1/prejudge") {
      prejudgePayloads.push(body);
      return {
        ok: true,
        data: {
          status: "ok",
          prejudgements: {},
        },
      };
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    await sendListObservations(tabState, readResult, tabId);

    assert.equal(prejudgePayloads.length, 1);
    const sentIds = prejudgePayloads[0].jobs.map((j) => j.platform_job_id);

    assert.equal(sentIds.includes("job_done"), false, "已有正式判断（done）岗位不应出现在预判请求里");
    assert.equal(sentIds.includes("job_already_prejudged"), false, "已在 listPrejudge 中的岗位不应重复发送");
    assert.equal(sentIds.includes("job_running"), true, "尚未完成正式判断（running）的岗位应进入预判请求");
    assert.equal(sentIds.includes("job_fresh"), true, "全新未判断岗位应进入预判请求");
    assert.deepEqual(sentIds.sort(), ["job_fresh", "job_running"].sort());
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 5. 预判请求体每个岗位只包含指定的 11 个键：
//    platform_job_id, title, company_name, company_industry, salary_raw,
//    city, district, experience, degree, job_labels, skills
// -----------------------------------------------------------------------------
test("sendListObservations: 5. 预判请求体每个岗位只包含指定的 11 个键", async () => {
  const tabId = 905;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  setupMockChrome();

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs: [
        {
          platform_job_id: "job_rich",
          title: "资深架构师",
          company_name: "某科技集团",
          company_industry: "云计算",
          salary_raw: "40-60K",
          city: "杭州",
          district: "余杭区",
          experience: "5-10年",
          degree: "硕士",
          job_labels: ["架构", "分布式"],
          skills: ["Go", "Kubernetes"],
          // 页面读取带来的冗余键（禁止进入预判 payload）
          securityId: "sec_999",
          encryptJobId: "enc_888",
          bossName: "李总",
          bossTitle: "技术合伙人",
          description: "详细岗位描述内容...",
          extraField: { a: 1 },
        },
        {
          platform_job_id: "job_sparse",
          title: "初级软件工程师",
          // 所有可选字段均缺省
          unknownProp: "test",
        },
      ],
    },
  };

  const prejudgePayloads = [];
  jetClient.call = async (method, path, body) => {
    if (path === "/v1/observations") {
      return { ok: true, data: { jobs: {} } };
    }
    if (path === "/v1/prejudge") {
      prejudgePayloads.push(body);
      return {
        ok: true,
        data: { status: "ok", prejudgements: {} },
      };
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    await sendListObservations(tabState, readResult, tabId);

    assert.equal(prejudgePayloads.length, 1);
    const sentJobs = prejudgePayloads[0].jobs;
    assert.equal(sentJobs.length, 2);

    const EXPECTED_KEYS = [
      "platform_job_id",
      "title",
      "company_name",
      "company_industry",
      "salary_raw",
      "city",
      "district",
      "experience",
      "degree",
      "job_labels",
      "skills",
    ].sort();

    // 1. 富信息岗位：键集合严格一致，多余键被丢弃，已有字段保留
    const j1 = sentJobs[0];
    assert.deepEqual(Object.keys(j1).sort(), EXPECTED_KEYS, "富信息岗位对象键必须严格等于指定的 11 个键");
    assert.equal(j1.platform_job_id, "job_rich");
    assert.equal(j1.title, "资深架构师");
    assert.equal(j1.company_name, "某科技集团");
    assert.equal(j1.company_industry, "云计算");
    assert.equal(j1.salary_raw, "40-60K");
    assert.equal(j1.city, "杭州");
    assert.equal(j1.district, "余杭区");
    assert.equal(j1.experience, "5-10年");
    assert.equal(j1.degree, "硕士");
    assert.deepEqual(j1.job_labels, ["架构", "分布式"]);
    assert.deepEqual(j1.skills, ["Go", "Kubernetes"]);
    assert.equal("securityId" in j1, false);
    assert.equal("encryptJobId" in j1, false);
    assert.equal("description" in j1, false);

    // 2. 稀疏信息岗位：缺省字段按协议回退为 null / "" / []，键集合依然严格为 11 个
    const j2 = sentJobs[1];
    assert.deepEqual(Object.keys(j2).sort(), EXPECTED_KEYS, "稀疏岗位对象键必须严格等于指定的 11 个键");
    assert.equal(j2.platform_job_id, "job_sparse");
    assert.equal(j2.title, "初级软件工程师");
    assert.equal(j2.company_name, null);
    assert.equal(j2.company_industry, null);
    assert.equal(j2.salary_raw, null);
    assert.equal(j2.city, "");
    assert.equal(j2.district, null);
    assert.equal(j2.experience, null);
    assert.equal(j2.degree, null);
    assert.deepEqual(j2.job_labels, []);
    assert.deepEqual(j2.skills, []);
    assert.equal("unknownProp" in j2, false);
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 6. 所有请求都走 jetClient（断言没有其他 fetch）
// -----------------------------------------------------------------------------
test("sendListObservations: 6. 所有请求都走 jetClient（断言没有其他 fetch）", async () => {
  const tabId = 906;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  setupMockChrome();

  let directFetchCount = 0;
  globalThis.fetch = () => {
    directFetchCount++;
    throw new Error("Unexpected direct global fetch call");
  };

  const jetClientCalls = [];
  jetClient.call = async (method, path, body) => {
    jetClientCalls.push({ method, path });
    if (path === "/v1/observations") {
      return { ok: true, data: { jobs: {} } };
    }
    if (path === "/v1/prejudge") {
      return { ok: true, data: { status: "ok", prejudgements: {} } };
    }
    return { ok: true, data: {} };
  };

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs: [
        { platform_job_id: "job_f1", title: "职位1", city: "北京" },
      ],
    },
  };

  try {
    await sendListObservations(tabState, readResult, tabId);

    // 断言没有发生任何 global fetch 调用
    assert.equal(directFetchCount, 0, "断言没有发生任何 global fetch 调用");

    // 断言所有网络调用均走 jetClient.call
    assert.equal(jetClientCalls.length, 2, "应恰好发出 2 次 jetClient 调用（观测和预判）");
    assert.equal(jetClientCalls[0].method, "POST");
    assert.equal(jetClientCalls[0].path, "/v1/observations");
    assert.equal(jetClientCalls[1].method, "POST");
    assert.equal(jetClientCalls[1].path, "/v1/prejudge");
  } finally {
    cleanupTabState(tabId);
  }
});

// -----------------------------------------------------------------------------
// 7. 预判等待期间用户切换到另一个岗位：预判返回后的重绘带的是最新详情，不回退到旧岗位
// -----------------------------------------------------------------------------
test("sendListObservations: 7. 预判挂起期间切换岗位，预判返回后的 render_state.detail 是切换后的岗位", async () => {
  const tabId = 907;
  activeTabIds.add(tabId);
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listPrejudge: new Map(),
    listJobs: [],
    pageStatus: null,
    lastJobRender: { detail: { platform_job_id: "job_A", title: "岗位A" } },
    lastRender: null,
  };
  tabStateMap.set(tabId, tabState);

  const renderMessages = [];
  setupMockChrome({
    onTabsSendMessage(targetTabId, message) {
      if (message.type === "render_state") {
        renderMessages.push(JSON.parse(JSON.stringify(message)));
      }
    },
  });

  let resolvePrejudge;
  const pendingPrejudge = new Promise((resolve) => {
    resolvePrejudge = resolve;
  });

  jetClient.call = async (method, path) => {
    if (path === "/v1/observations") {
      return { ok: true, data: { jobs: { job_p: { judgement: null, screen_hints: [] } } } };
    }
    if (path === "/v1/prejudge") {
      return pendingPrejudge;
    }
    throw new Error(`Unexpected Jet call: ${method} ${path}`);
  };

  try {
    const flowPromise = sendListObservations(
      tabState,
      { page_kind: "search_list", list: { ok: true, jobs: [{ platform_job_id: "job_p", title: "预判岗位", city: "深圳" }] } },
      tabId,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(renderMessages.length, 1);
    assert.equal(renderMessages[0].detail.platform_job_id, "job_A");

    // 等待预判期间，用户点开了岗位 B（详情渲染更新了 lastJobRender）
    tabState.lastJobRender = { detail: { platform_job_id: "job_B", title: "岗位B" } };

    resolvePrejudge({
      ok: true,
      data: { status: "ok", prejudgements: { job_p: { level: "skip", reason: "城市不符" } } },
    });
    await flowPromise;

    assert.equal(renderMessages.length, 2);
    assert.equal(renderMessages[1].detail.platform_job_id, "job_B", "预判返回后的重绘不得回退到旧岗位详情");
    assert.equal(renderMessages[1].list_marks.job_p.type, "prejudge");
  } finally {
    cleanupTabState(tabId);
  }
});
