import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const labelFormPath = path.resolve(__dirname, "../src/label-form.js");
const contentPath = path.resolve(__dirname, "../src/content.js");

function extractSyncBlock(code, filePath) {
  const startMarker = "// BEGIN SYNC hr-note-autosave (label-form.js)";
  const endMarker = "// END SYNC hr-note-autosave";
  const startIdx = code.indexOf(startMarker);
  const endIdx = code.indexOf(endMarker);
  assert.ok(startIdx !== -1, `${filePath} 缺少开始标记 ${startMarker}`);
  assert.ok(endIdx !== -1, `${filePath} 缺少结束标记 ${endMarker}`);
  assert.ok(startIdx < endIdx, `${filePath} 标记顺序错误`);
  return code.slice(startIdx + startMarker.length, endIdx).trim();
}

test("hr-note-autosave-sync: (i) label-form.js 与 content.js 标记块源码去掉 export 后完全相同", () => {
  const labelFormCode = fs.readFileSync(labelFormPath, "utf-8");
  const contentCode = fs.readFileSync(contentPath, "utf-8");

  const labelSnippet = extractSyncBlock(labelFormCode, "label-form.js");
  const contentSnippet = extractSyncBlock(contentCode, "content.js");

  const normalizedLabelSnippet = labelSnippet.replace(/^export\s+/gm, "");
  assert.equal(normalizedLabelSnippet, contentSnippet);
});

function loadContentFunctions() {
  const contentCode = fs.readFileSync(contentPath, "utf-8");
  const contentSnippet = extractSyncBlock(contentCode, "content.js");

  const sandbox = {
    setTimeout,
    clearTimeout,
    Promise,
    String,
    Boolean,
    Array,
    Object,
  };
  vm.createContext(sandbox);
  vm.runInContext(contentSnippet, sandbox);
  return {
    decideHrNoteAutoSave: sandbox.decideHrNoteAutoSave,
    createAutoSaveCoordinator: sandbox.createAutoSaveCoordinator,
  };
}

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 空不保存", () => {
  const { decideHrNoteAutoSave } = loadContentFunctions();
  const res1 = decideHrNoteAutoSave("", null);
  assert.equal(res1.shouldSave, false);
  assert.equal(res1.reason, "empty");
  assert.equal(res1.note, null);

  const res2 = decideHrNoteAutoSave("   ", "原有记录");
  assert.equal(res2.shouldSave, false);
  assert.equal(res2.reason, "empty");
  assert.equal(res2.note, null);

  const res3 = decideHrNoteAutoSave(null, "原有记录");
  assert.equal(res3.shouldSave, false);
  assert.equal(res3.reason, "empty");
  assert.equal(res3.note, null);
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 未变化不保存", () => {
  const { decideHrNoteAutoSave } = loadContentFunctions();
  const res1 = decideHrNoteAutoSave("双休", "双休");
  assert.equal(res1.shouldSave, false);
  assert.equal(res1.reason, "unchanged");
  assert.equal(res1.note, "双休");

  const res2 = decideHrNoteAutoSave("  双休  ", "双休");
  assert.equal(res2.shouldSave, false);
  assert.equal(res2.reason, "unchanged");
  assert.equal(res2.note, "双休");
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 1 秒防抖", async () => {
  const { createAutoSaveCoordinator } = loadContentFunctions();
  const calls = [];
  const coordinator = createAutoSaveCoordinator({
    delayMs: 30,
    save: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
  });

  coordinator.onInput("输入1");
  coordinator.onInput("输入2");
  coordinator.onInput("输入3");
  assert.equal(calls.length, 0);

  await new Promise((r) => setTimeout(r, 60));
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "输入3");
  assert.equal(coordinator.getLastSaved(), "输入3");
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 失焦立即保存", async () => {
  const { createAutoSaveCoordinator } = loadContentFunctions();
  const calls = [];
  const coordinator = createAutoSaveCoordinator({
    delayMs: 500,
    save: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
  });

  await coordinator.onBlur("失焦保存");
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "失焦保存");
  assert.equal(coordinator.getLastSaved(), "失焦保存");
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 在途时再触发只排队最新一份", async () => {
  const { createAutoSaveCoordinator } = loadContentFunctions();
  const calls = [];
  let finishFirst;
  const firstPromise = new Promise((resolve) => {
    finishFirst = resolve;
  });

  const coordinator = createAutoSaveCoordinator({
    delayMs: 20,
    save: async (note) => {
      calls.push(note);
      if (calls.length === 1) {
        await firstPromise;
      }
      return { ok: true, hr_note: note };
    },
  });

  const p1 = coordinator.onBlur("请求1");
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);

  coordinator.onInput("请求2");
  coordinator.onInput("请求3");
  assert.equal(coordinator.hasPending(), true);

  finishFirst();
  await p1;

  await new Promise((r) => setTimeout(r, 60));
  assert.equal(calls.length, 2);
  assert.equal(calls[0], "请求1");
  assert.equal(calls[1], "请求3");
  assert.equal(coordinator.getLastSaved(), "请求3");
  assert.equal(coordinator.isInFlight(), false);
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - flush 在在途期间调用不会并发第二个请求", async () => {
  const { createAutoSaveCoordinator } = loadContentFunctions();
  const calls = [];
  let concurrent = 0;
  let maxConcurrent = 0;
  let finishFirst;
  const firstPromise = new Promise((r) => {
    finishFirst = r;
  });

  const coordinator = createAutoSaveCoordinator({
    save: async (note) => {
      concurrent++;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      calls.push(note);
      if (calls.length === 1) {
        await firstPromise;
      }
      concurrent--;
      return { ok: true, hr_note: note };
    },
  });

  coordinator.onBlur("保存1");
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);
  assert.equal(concurrent, 1);

  const flushP = coordinator.flush("保存2");
  assert.equal(maxConcurrent, 1);
  assert.equal(calls.length, 1);

  finishFirst();
  const res = await flushP;
  assert.equal(res.ok, true);
  assert.equal(res.saved, true);
  assert.equal(maxConcurrent, 1);
  assert.equal(calls.length, 2);
  assert.equal(calls[0], "保存1");
  assert.equal(calls[1], "保存2");
  assert.equal(coordinator.isInFlight(), false);
});

test("hr-note-autosave-sync: (ii) vm 提取代码关键用例 - 自动保存（onBlur）失败后内容不变再调用 flush 会重试保存且成功", async () => {
  const { createAutoSaveCoordinator } = loadContentFunctions();
  const calls = [];
  let attempt = 0;

  const coordinator = createAutoSaveCoordinator({
    initialSaved: "原本记录",
    save: async (note) => {
      attempt++;
      calls.push(note);
      if (attempt === 1) {
        return { ok: false, error: "网络超时", message: "网络超时" };
      }
      return { ok: true, hr_note: note };
    },
  });

  const blurRes = await coordinator.onBlur("新内容");
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "新内容");
  assert.equal(blurRes.ok, false);
  assert.equal(coordinator.getLastSaved(), "原本记录");

  const flushRes = await coordinator.flush("新内容");
  assert.equal(calls.length, 2);
  assert.equal(calls[1], "新内容");
  assert.equal(flushRes.ok, true);
  assert.equal(flushRes.saved, true);
  assert.equal(flushRes.note, "新内容");
  assert.equal(coordinator.getLastSaved(), "新内容");
  assert.equal(coordinator.isInFlight(), false);
});
