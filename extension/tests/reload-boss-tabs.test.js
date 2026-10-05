import test from "node:test";
import assert from "node:assert/strict";

import { reloadOpenBossTabs } from "../src/background.js";

function makeChrome(tabs, { failReloadIds = [], failQuery = false } = {}) {
  const calls = { query: [], reload: [] };
  return {
    calls,
    tabs: {
      async query(info) {
        calls.query.push(info);
        if (failQuery) throw new Error("query failed");
        return tabs;
      },
      async reload(tabId) {
        if (failReloadIds.includes(tabId)) throw new Error("reload failed");
        calls.reload.push(tabId);
      },
    },
  };
}

test("reloadOpenBossTabs: 插件重新加载（update）后按 zhipin.com 网址查询并逐个刷新", async () => {
  const chromeApi = makeChrome([{ id: 11 }, { id: 12 }]);
  const count = await reloadOpenBossTabs({ reason: "update" }, chromeApi);
  assert.deepEqual(chromeApi.calls.query, [{ url: "https://*.zhipin.com/*" }]);
  assert.deepEqual(chromeApi.calls.reload, [11, 12]);
  assert.equal(count, 2);
});

test("reloadOpenBossTabs: 首次安装（install）同样刷新", async () => {
  const chromeApi = makeChrome([{ id: 5 }]);
  assert.equal(await reloadOpenBossTabs({ reason: "install" }, chromeApi), 1);
  assert.deepEqual(chromeApi.calls.reload, [5]);
});

test("reloadOpenBossTabs: Chrome 自身升级等其他原因不查询、不刷新", async () => {
  for (const reason of ["chrome_update", "shared_module_update", undefined]) {
    const chromeApi = makeChrome([{ id: 1 }]);
    assert.equal(await reloadOpenBossTabs({ reason }, chromeApi), 0);
    assert.deepEqual(chromeApi.calls.query, []);
    assert.deepEqual(chromeApi.calls.reload, []);
  }
});

test("reloadOpenBossTabs: 单个页面刷新失败不影响其他页面，查询失败不抛错", async () => {
  const chromeApi = makeChrome([{ id: 1 }, { id: 2 }, { id: 3 }], { failReloadIds: [2] });
  assert.equal(await reloadOpenBossTabs({ reason: "update" }, chromeApi), 2);
  assert.deepEqual(chromeApi.calls.reload, [1, 3]);

  const failing = makeChrome([], { failQuery: true });
  await assert.doesNotReject(async () => {
    assert.equal(await reloadOpenBossTabs({ reason: "update" }, failing), 0);
  });
});
