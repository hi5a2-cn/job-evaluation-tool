import test from "node:test";
import assert from "node:assert/strict";
import {
  contentKey,
  decideDetailAction,
  shouldRetryRead,
  resetTabStateOnLoad,
  pickRerenderPayload,
  serializeTabState,
  deserializeTabState,
  shouldKeepPolling,
  isDetailResponseCurrent,
  shouldResumePolling,
  localDateString,
} from "../src/scheduler.js";

test("contentKey is sensitive to content changes and stable for identical input", () => {
  const baseJob = {
    platform_job_id: "job_1",
    title: "后端架构师",
    salary_raw: "30-50K",
    city: "深圳",
    description: "负责分布式微服务架构开发",
  };

  const key1 = contentKey(baseJob);
  const key2 = contentKey({ ...baseJob });
  assert.equal(key1, key2, "Same content must produce identical key");

  // Title changed
  const keyTitle = contentKey({ ...baseJob, title: "前端架构师" });
  assert.notEqual(key1, keyTitle);

  // Salary changed
  const keySalary = contentKey({ ...baseJob, salary_raw: "35-55K" });
  assert.notEqual(key1, keySalary);

  // City changed
  const keyCity = contentKey({ ...baseJob, city: "广州" });
  assert.notEqual(key1, keyCity);

  // Description changed
  const keyDesc = contentKey({ ...baseJob, description: "负责基础平台架构开发" });
  assert.notEqual(key1, keyDesc);

  // Handles null / empty
  assert.equal(typeof contentKey(null), "string");
  assert.equal(typeof contentKey({}), "string");
});

test("decideDetailAction handles all page kinds and details", () => {
  // 1. null / empty result
  assert.deepEqual(decideDetailAction(null, null), { action: "none", key: null });
  assert.deepEqual(decideDetailAction({}, null), { action: "none", key: null });

  // 2. captcha_or_blank & other -> none
  assert.deepEqual(
    decideDetailAction({ page_kind: "captcha_or_blank" }, null),
    { action: "none", key: null },
  );
  assert.deepEqual(
    decideDetailAction({ page_kind: "other" }, null),
    { action: "none", key: null },
  );

  // 3. job_detail_page -> unsupported
  assert.deepEqual(
    decideDetailAction({ page_kind: "job_detail_page" }, null),
    { action: "unsupported", key: null },
  );

  // 4. search_list with detail failure and non-empty problems -> unrecognized
  const unrecognizedResult = {
    page_kind: "search_list",
    detail: { ok: false, job: null },
    problems: ["详情缺少必要字段"],
  };
  assert.deepEqual(
    decideDetailAction(unrecognizedResult, null),
    { action: "unrecognized", key: null },
  );

  // 5. search_list with detail not opened yet (ok:false, problems:[]) -> none
  const listOnlyResult = {
    page_kind: "search_list",
    detail: { ok: false, job: null },
    problems: [],
  };
  assert.deepEqual(
    decideDetailAction(listOnlyResult, null),
    { action: "none", key: null },
  );

  // 6. search_list with valid detail job -> send when no lastKey or different key
  const validJob = {
    platform_job_id: "abc_123",
    title: "AI 工程师",
    salary_raw: "30-50K",
    city: "北京",
    description: "大模型应用研发",
  };
  const detailResult = {
    page_kind: "search_list",
    detail: { ok: true, job: validJob },
    problems: [],
  };

  const expectedKey = `abc_123|${contentKey(validJob)}`;

  const firstDecision = decideDetailAction(detailResult, null);
  assert.equal(firstDecision.action, "send");
  assert.equal(firstDecision.key, expectedKey);

  // 7. search_list with same key -> rerender
  const secondDecision = decideDetailAction(detailResult, expectedKey);
  assert.equal(secondDecision.action, "rerender");
  assert.equal(secondDecision.key, expectedKey);

  // 8. search_list with different job -> send
  const anotherJob = { ...validJob, platform_job_id: "def_456" };
  const anotherResult = {
    page_kind: "search_list",
    detail: { ok: true, job: anotherJob },
    problems: [],
  };
  const thirdDecision = decideDetailAction(anotherResult, expectedKey);
  assert.equal(thirdDecision.action, "send");
  assert.notEqual(thirdDecision.key, expectedKey);
});

test("truncated list without an opened job is not unrecognized", () => {
  const r = {
    page_kind: "search_list",
    list: { ok: true, has_more: true, jobs: [] },
    detail: { ok: false, job: null },
    problems: ["岗位超过 200 条，已截断"],
  };
  assert.equal(decideDetailAction(r, null).action, "none");
});

test("missing Vue instance is unrecognized", () => {
  const r = {
    page_kind: "search_list",
    list: { ok: false, has_more: null, jobs: [] },
    detail: { ok: false, job: null },
    problems: ["未找到 Vue 实例"],
  };
  assert.equal(decideDetailAction(r, null).action, "unrecognized");
});

test("shouldRetryRead branches and limits (FR-055 / T092)", () => {
  const unrecognizedResult = {
    page_kind: "search_list",
    detail: { ok: false, job: null },
    problems: ["详情缺少必要字段"],
  };

  // 1. unrecognized 重试 4 次后停止、延迟序列依次 500 / 1500 / 3000 / 6000ms，showReading = true
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 0, 100), {
    retry: true,
    delayMs: 500,
    showReading: true,
  });
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 1, 1000), {
    retry: true,
    delayMs: 1500,
    showReading: true,
  });
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 2, 2000), {
    retry: true,
    delayMs: 3000,
    showReading: true,
  });
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 3, 5000), {
    retry: true,
    delayMs: 6000,
    showReading: true,
  });
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 4, 11000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(unrecognizedResult, 5, 20000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });

  // 2. search_list 列表有但无详情岗位且在 8 秒内重试，showReading = false
  const searchListNoDetail = {
    page_kind: "search_list",
    list: { ok: true, jobs: [{ platform_job_id: "job_1" }] },
    detail: { ok: false, job: null },
    problems: [],
  };
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 0, 0), {
    retry: true,
    delayMs: 500,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 1, 3000), {
    retry: true,
    delayMs: 1500,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 2, 7999), {
    retry: true,
    delayMs: 3000,
    showReading: false,
  });

  // 3. 超过 8 秒不重试
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 0, 8000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 1, 10000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(searchListNoDetail, 4, 2000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });

  // 4. 正常读到详情不重试
  const validDetail = {
    page_kind: "search_list",
    list: { ok: true, jobs: [{ platform_job_id: "job_1" }] },
    detail: {
      ok: true,
      job: {
        platform_job_id: "job_1",
        title: "后端开发",
        salary_raw: "20-30K",
        city: "北京",
        description: "研发",
      },
    },
    problems: [],
  };
  assert.deepEqual(shouldRetryRead(validDetail, 0, 500), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });

  // 5. 其他情况不重试 (job_detail_page, other, captcha_or_blank, null)
  assert.deepEqual(shouldRetryRead({ page_kind: "job_detail_page" }, 0, 500), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead({ page_kind: "other" }, 0, 500), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead({ page_kind: "captcha_or_blank" }, 0, 500), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
  assert.deepEqual(shouldRetryRead(null, 0, 500), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });
});

test("resetTabStateOnLoad resets tab state and clears timers while preserving loadAt/readAttempt", () => {
  let pollCleared = false;
  let retryCleared = false;

  const dummyPollTimer = setInterval(() => {}, 1000);
  const dummyRetryTimer = setTimeout(() => {}, 1000);

  const state = {
    tabId: 42,
    lastKey: "job_1|hash123",
    lastJobRender: { detail: { platform_job_id: "job_1" } },
    lastRender: { detail: { platform_job_id: "job_1" } },
    pageStatus: { label: "正在读取页面…" },
    currentJobId: "job_1",
    currentJobTitle: "工程师",
    listSent: new Set(["key1", "key2"]),
    listJudgements: new Map([["job_1", { judgement: {} }]]),
    listJobs: [{ platform_job_id: "job_1" }],
    pollTimer: dummyPollTimer,
    retryTimer: dummyRetryTimer,
    loadAt: 1234567,
    readAttempt: 2,
    updated_at: 1234567,
  };

  const res = resetTabStateOnLoad(state);
  assert.equal(res, state);
  assert.equal(state.lastKey, null);
  assert.equal(state.lastJobRender, null);
  assert.equal(state.lastRender, null);
  assert.equal(state.pageStatus, null);
  assert.equal(state.currentJobId, null);
  assert.equal(state.currentJobTitle, null);
  assert.equal(state.listSent.size, 0);
  assert.equal(state.listJudgements.size, 0);
  assert.equal(state.listJobs.length, 0);
  assert.equal(state.pollTimer, null);
  assert.equal(state.retryTimer, null);
  assert.equal(state.updated_at, null);
  assert.equal(state.loadAt, 1234567);
  assert.equal(state.readAttempt, 2);

  assert.equal(resetTabStateOnLoad(null), null);
  assert.equal(resetTabStateOnLoad(undefined), null);
});

test("pickRerenderPayload only returns job render and never returns page status", () => {
  // 1. With lastJobRender containing platform_job_id -> returns detail
  const jobDetail = { platform_job_id: "job_123", title: "数据分析师" };
  const stateWithJob = {
    lastJobRender: { detail: jobDetail, marks_enabled: true },
    pageStatus: null,
  };
  assert.deepEqual(pickRerenderPayload(stateWithJob), jobDetail);

  // 2. With pageStatus only (no platform_job_id) -> returns null
  const stateWithPageStatus = {
    lastJobRender: null,
    pageStatus: { platform_job_id: null, label: "正在读取页面…" },
  };
  assert.equal(pickRerenderPayload(stateWithPageStatus), null);

  // 3. With both lastJobRender and pageStatus -> returns lastJobRender detail
  const stateWithBoth = {
    lastJobRender: { detail: jobDetail },
    pageStatus: { platform_job_id: null, label: "正在读取页面…" },
  };
  assert.deepEqual(pickRerenderPayload(stateWithBoth), jobDetail);

  // 4. Empty / null state -> returns null
  assert.equal(pickRerenderPayload(null), null);
  assert.equal(pickRerenderPayload({}), null);
  assert.equal(pickRerenderPayload({ lastJobRender: null, pageStatus: null }), null);
});

test("刷新后读到同一岗位时不会重发 reading 占位", () => {
  const job = {
    platform_job_id: "job_same",
    title: "前端专家",
    salary_raw: "30-50K",
    city: "北京",
    description: "负责前端架构",
  };
  const readResult = {
    page_kind: "search_list",
    detail: { ok: true, job },
    problems: [],
  };

  // 1. 刷新前已有该岗位的渲染状态和 key
  const tabState = {
    tabId: 1,
    lastKey: `${job.platform_job_id}|${contentKey(job)}`,
    lastJobRender: { detail: { platform_job_id: job.platform_job_id, title: job.title } },
    lastRender: { detail: { platform_job_id: job.platform_job_id, title: job.title } },
    pageStatus: null,
    currentJobId: job.platform_job_id,
    currentJobTitle: job.title,
    listSent: new Set([`${job.platform_job_id}|${contentKey(job)}`]),
    listJudgements: new Map([[job.platform_job_id, { judgement: { status: "done", verdict: "apply" } }]]),
    listJobs: [{ platform_job_id: job.platform_job_id, title: job.title }],
  };

  // 2. 页面刷新 (reason: "load") 调用 resetTabStateOnLoad
  resetTabStateOnLoad(tabState);
  assert.equal(tabState.lastKey, null);
  assert.equal(tabState.lastJobRender, null);
  assert.equal(tabState.lastRender, null);

  // 3. 刷新后在重试过程中展示了 "reading" 占位并写入 pageStatus，不覆盖 lastJobRender
  const readingPayload = { platform_job_id: null, title: null, label: "正在读取页面…" };
  tabState.pageStatus = readingPayload;
  tabState.lastRender = { detail: readingPayload };

  // 4. 数据读到后，由于 lastKey 已在 load 时被清空，decideDetailAction 返回 "send" 而非 "rerender"
  const decision = decideDetailAction(readResult, tabState.lastKey);
  assert.equal(decision.action, "send");

  // 5. 即使若某种情况下 decision.action 为 rerender，pickRerenderPayload 绝不会返回 reading 占位
  const rerenderPayload = pickRerenderPayload(tabState);
  assert.equal(rerenderPayload, null);
  assert.notEqual(rerenderPayload, tabState.pageStatus);
  assert.notEqual(rerenderPayload, tabState.lastRender.detail);
});

test("serializeTabState and deserializeTabState: Map <-> 数组转换并丢弃定时器", () => {
  const state = {
    tabId: 101,
    lastKey: "job_1|hash123",
    lastJobRender: { detail: { platform_job_id: "job_1", title: "Go工程师" } },
    pageStatus: null,
    pollTimer: 12345,
    retryTimer: 67890,
    loadAt: 1000,
    readAttempt: 2,
    lastTimings: { wait_ms: 10, read_ms: 20, jet_ms: 30 },
    currentJobId: "job_1",
    currentJobTitle: "Go工程师",
    listJobs: [{ platform_job_id: "job_1", title: "Go工程师" }],
    listJudgements: new Map([
      ["job_1", { judgement: { status: "done", verdict: "apply" }, my_status: "saved" }],
    ]),
    updated_at: 1700000000000,
  };

  const serialized = serializeTabState(state);
  assert.equal(serialized.tabId, 101);
  assert.equal(serialized.lastKey, "job_1|hash123");
  assert.equal(serialized.pollTimer, undefined);
  assert.equal(serialized.retryTimer, undefined);
  assert.ok(Array.isArray(serialized.listJudgements));
  assert.equal(serialized.listJudgements.length, 1);
  assert.deepEqual(serialized.listJudgements[0], [
    "job_1",
    { judgement: { status: "done", verdict: "apply" }, my_status: "saved" },
  ]);

  const restored = deserializeTabState(serialized);
  assert.equal(restored.tabId, 101);
  assert.equal(restored.lastKey, "job_1|hash123");
  assert.equal(restored.pollTimer, null);
  assert.equal(restored.retryTimer, null);
  assert.ok(restored.listJudgements instanceof Map);
  assert.equal(restored.listJudgements.size, 1);
  assert.deepEqual(restored.listJudgements.get("job_1"), {
    judgement: { status: "done", verdict: "apply" },
    my_status: "saved",
  });
  assert.equal(restored.updated_at, 1700000000000);

  // null / invalid handling
  assert.equal(serializeTabState(null), null);
  assert.equal(deserializeTabState(null), null);
});

test("shouldKeepPolling handles all judgement states", () => {
  assert.equal(shouldKeepPolling(null), false);
  assert.equal(shouldKeepPolling(undefined), false);
  assert.equal(shouldKeepPolling({ status: "queued" }), true);
  assert.equal(shouldKeepPolling({ status: "running" }), true);
  assert.equal(
    shouldKeepPolling({ status: "done", review: { outcome: "pending" } }),
    true,
  );
  assert.equal(
    shouldKeepPolling({ status: "done", review: { outcome: "kept" } }),
    false,
  );
  assert.equal(
    shouldKeepPolling({ status: "done", review: { outcome: "downgraded" } }),
    false,
  );
  assert.equal(
    shouldKeepPolling({ status: "done", review: { outcome: "failed" } }),
    false,
  );
  assert.equal(
    shouldKeepPolling({ status: "done", review: { outcome: "quota_exhausted" } }),
    false,
  );
  assert.equal(shouldKeepPolling({ status: "done" }), false);
  assert.equal(shouldKeepPolling({ status: "failed" }), false);
  assert.equal(shouldKeepPolling({ status: "quota_exhausted" }), false);
  assert.equal(shouldKeepPolling({ status: "interrupted" }), false);
});

test("isDetailResponseCurrent: 岗位一致/不一致/tabState 为空", () => {
  // 1. tabState 为空 (null / undefined / 非对象)
  assert.equal(isDetailResponseCurrent(null, "job_1"), false);
  assert.equal(isDetailResponseCurrent(undefined, "job_1"), false);
  assert.equal(isDetailResponseCurrent("invalid", "job_1"), false);

  // 2. 岗位一致
  const matchingState = { currentJobId: "job_1" };
  assert.equal(isDetailResponseCurrent(matchingState, "job_1"), true);

  // 3. 岗位不一致
  const mismatchState = { currentJobId: "job_2" };
  assert.equal(isDetailResponseCurrent(mismatchState, "job_1"), false);

  // 4. platformJobId 为空
  assert.equal(isDetailResponseCurrent(matchingState, null), false);
  assert.equal(isDetailResponseCurrent(matchingState, undefined), false);
  assert.equal(isDetailResponseCurrent({ currentJobId: null }, null), false);
});

test("shouldResumePolling: 覆盖定时器/状态/岗位一致性/放弃标记等各分支", () => {
  const baseValidState = {
    tabId: 1,
    pollTimer: null,
    currentJobId: "job_100",
    pollGaveUpJobId: null,
    lastJobRender: {
      detail: {
        platform_job_id: "job_100",
        view_state: "judging",
      },
    },
  };

  // 1. 正常应恢复
  assert.equal(shouldResumePolling(baseValidState), "job_100");

  // 2. tabState 为空
  assert.equal(shouldResumePolling(null), null);
  assert.equal(shouldResumePolling(undefined), null);

  // 3. 有定时器不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      pollTimer: 12345,
    }),
    null,
  );

  // 4. 卡片不是判断中不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      lastJobRender: {
        detail: {
          platform_job_id: "job_100",
          view_state: "done_apply",
        },
      },
    }),
    null,
  );
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      lastJobRender: {
        detail: {
          platform_job_id: "job_100",
          view_state: "failed",
        },
      },
    }),
    null,
  );

  // 5. 卡片岗位与 currentJobId 不一致不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      lastJobRender: {
        detail: {
          platform_job_id: "job_200",
          view_state: "judging",
        },
      },
    }),
    null,
  );

  // 6. pollGaveUpJobId 等于当前岗位不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      pollGaveUpJobId: "job_100",
    }),
    null,
  );
  // pollGaveUpJobId 为其他岗位时允许恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      pollGaveUpJobId: "job_other",
    }),
    "job_100",
  );

  // 7. currentJobId 为空不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      currentJobId: null,
    }),
    null,
  );

  // 8. lastJobRender 或 detail 缺失不恢复
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      lastJobRender: null,
    }),
    null,
  );
  assert.equal(
    shouldResumePolling({
      ...baseValidState,
      lastJobRender: {},
    }),
    null,
  );
});

test("localDateString: 按本地年月日拼接 YYYY-MM-DD，避免 toISOString 的 UTC 跨日问题", () => {
  // 1. 明确的年月日
  const d1 = new Date(2026, 8, 30); // 2026-09-30 (月从 0 开始)
  assert.equal(localDateString(d1), "2026-09-30");

  // 2. 单数月与日补零
  const d2 = new Date(2026, 0, 5); // 2026-01-05
  assert.equal(localDateString(d2), "2026-01-05");

  // 3. 年末
  const d3 = new Date(2026, 11, 31); // 2026-12-31
  assert.equal(localDateString(d3), "2026-12-31");

  // 4. 本地凌晨（东八区此时 UTC 仍是前一天）：按本地日期返回
  const earlyMorning = new Date(2026, 8, 30, 0, 30); // 本地 2026-09-30 00:30
  assert.equal(localDateString(earlyMorning), "2026-09-30");

  // 5. 默认无参返回当天本地日期字符串
  const today = localDateString();
  assert.match(today, /^\d{4}-\d{2}-\d{2}$/);
});
