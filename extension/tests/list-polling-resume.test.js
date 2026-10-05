import test from "node:test";
import assert from "node:assert/strict";

// 后台被浏览器挂起后定时器丢失：页面再次读取（内容未变、走"重绘"分支）时，
// 与详情轮询一样恢复"判断中"卡片的列表轮询
const job = { platform_job_id: "job_cur", title: "当前岗位", salary_raw: "20-30K", city: "深圳", description: "职责" };

globalThis.chrome = {
  storage: {
    local: { get: async () => ({ marksEnabled: true }), set: async () => {} },
    session: { get: async () => ({}), set: async () => {}, remove: async () => {} },
    onChanged: { addListener: () => {} },
  },
  tabs: { sendMessage: async () => {}, onRemoved: { addListener: () => {} } },
  runtime: { sendMessage: async () => {}, onMessage: { addListener: () => {} } },
  scripting: {
    executeScript: async () => [{ result: { page_kind: "job_detail_page", detail: { ok: true, job } } }],
  },
  sidePanel: { setPanelBehavior: async () => {} },
};

const { tabStateMap, getOrCreateTabState, processPageRead, clearListPollTimer, jetClient } = await import("../src/background.js");
const { contentKey } = await import("../src/scheduler.js");

test("processPageRead 重绘分支：有'判断中'的列表岗位时恢复列表轮询；没有时不启动", async () => {
  const origCall = jetClient.call;
  jetClient.call = async () => ({ ok: true, data: { jobs: {} } });
  const tabId = 951;
  try {
    const tabState = getOrCreateTabState(tabId);
    tabState.lastKey = `${job.platform_job_id}|${contentKey(job)}`;
    tabState.lastJobRender = { detail: { platform_job_id: "job_cur", title: "当前岗位" } };
    tabState.listJobs = [{ platform_job_id: "job_other", title: "别的岗位" }];
    tabState.listJudgements.set("job_cur", { judgement: { status: "done", verdict: "apply" } });
    tabState.listJudgements.set("job_other", { judgement: { status: "running" } });
    assert.equal(tabState.listPollTimer, null);

    await processPageRead(tabId, { reason: "mutation" });
    assert.ok(tabState.listPollTimer, "有判断中的岗位时应恢复列表轮询");
    clearListPollTimer(tabState);

    tabState.listJudgements.set("job_other", { judgement: { status: "done", verdict: "try" } });
    await processPageRead(tabId, { reason: "mutation" });
    assert.equal(tabState.listPollTimer, null, "没有判断中的岗位时不启动");
  } finally {
    const s = tabStateMap.get(tabId);
    if (s) {
      clearListPollTimer(s);
      if (s.pollTimer) clearInterval(s.pollTimer);
      if (s.retryTimer) clearTimeout(s.retryTimer);
    }
    tabStateMap.delete(tabId);
    jetClient.call = origCall;
  }
});
