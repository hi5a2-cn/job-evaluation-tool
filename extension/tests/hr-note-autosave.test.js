import test from "node:test";
import assert from "node:assert/strict";
import {
  decideHrNoteAutoSave,
  createAutoSaveCoordinator,
} from "../src/label-form.js";
import { isHrNoteOverflowing } from "../src/myjobs-view.js";

test("decideHrNoteAutoSave: 空内容绝不保存", () => {
  assert.deepEqual(decideHrNoteAutoSave("", null), {
    shouldSave: false,
    reason: "empty",
    note: null,
  });
  assert.deepEqual(decideHrNoteAutoSave("   ", "原有内容"), {
    shouldSave: false,
    reason: "empty",
    note: null,
  });
  assert.deepEqual(decideHrNoteAutoSave(null, "原有内容"), {
    shouldSave: false,
    reason: "empty",
    note: null,
  });
  assert.deepEqual(decideHrNoteAutoSave(undefined, null), {
    shouldSave: false,
    reason: "empty",
    note: null,
  });
});

test("decideHrNoteAutoSave: 与最近已保存内容相同时不保存", () => {
  assert.deepEqual(decideHrNoteAutoSave("双休不加班", "双休不加班"), {
    shouldSave: false,
    reason: "unchanged",
    note: "双休不加班",
  });
  assert.deepEqual(decideHrNoteAutoSave("  双休不加班  ", "双休不加班"), {
    shouldSave: false,
    reason: "unchanged",
    note: "双休不加班",
  });
  assert.deepEqual(decideHrNoteAutoSave("双休不加班", "  双休不加班  "), {
    shouldSave: false,
    reason: "unchanged",
    note: "双休不加班",
  });
});

test("decideHrNoteAutoSave: trim 后非空且内容变化时需要保存并截断至 200 字", () => {
  const res1 = decideHrNoteAutoSave("双休不加班", null);
  assert.equal(res1.shouldSave, true);
  assert.equal(res1.note, "双休不加班");

  const res2 = decideHrNoteAutoSave("偶尔加班，有加班费", "双休不加班");
  assert.equal(res2.shouldSave, true);
  assert.equal(res2.note, "偶尔加班，有加班费");

  // 超过 200 字截断
  const longText = "a".repeat(250);
  const res3 = decideHrNoteAutoSave(longText, null);
  assert.equal(res3.shouldSave, true);
  assert.equal(res3.note, "a".repeat(200));

  // 截断后与已保存内容一致时不保存
  const res4 = decideHrNoteAutoSave(longText, "a".repeat(200));
  assert.equal(res4.shouldSave, false);
  assert.equal(res4.reason, "unchanged");
});

test("createAutoSaveCoordinator: 防抖延迟约 1 秒后触发自动保存", async () => {
  const calls = [];
  const statusEvents = [];

  const coordinator = createAutoSaveCoordinator({
    delayMs: 40,
    initialSaved: null,
    saveFn: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
    onStatusChange: (evt) => statusEvents.push(evt),
  });

  // 连续快速输入多次，只触发一次最终保存
  coordinator.handleInput("第一版");
  coordinator.handleInput("第二版");
  coordinator.handleInput("最终版");

  assert.equal(calls.length, 0);

  await new Promise((r) => setTimeout(r, 70));

  assert.equal(calls.length, 1);
  assert.equal(calls[0], "最终版");
  assert.equal(coordinator.getLastSaved(), "最终版");
  assert.ok(statusEvents.some((e) => e.status === "saving"));
  assert.ok(statusEvents.some((e) => e.status === "saved" && e.message === "已自动保存"));
});

test("createAutoSaveCoordinator: 失焦立即自动保存且空内容给出提示不保存", async () => {
  const calls = [];
  const statusEvents = [];

  const coordinator = createAutoSaveCoordinator({
    delayMs: 200,
    initialSaved: "旧记录",
    emptyMessage: "内容为空不会自动保存；要清空请点「清空」",
    saveFn: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
    onStatusChange: (evt) => statusEvents.push(evt),
  });

  // 输入为空时失焦：不调用 saveFn，报 empty
  await coordinator.handleBlur("   ");
  assert.equal(calls.length, 0);
  assert.ok(statusEvents.some((e) => e.status === "empty" && e.message.includes("不会自动保存")));

  // 输入新内容时失焦：立即保存，无需等待 200ms
  await coordinator.handleBlur("新记录");
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "新记录");
  assert.equal(coordinator.getLastSaved(), "新记录");
});

test("createAutoSaveCoordinator: 同一时刻仅一个保存请求在途，排队并更新最新内容", async () => {
  const calls = [];
  let finishFirst;
  const firstPromise = new Promise((resolve) => {
    finishFirst = resolve;
  });

  const coordinator = createAutoSaveCoordinator({
    delayMs: 20,
    initialSaved: null,
    saveFn: async (note) => {
      calls.push(note);
      if (calls.length === 1) {
        await firstPromise;
      }
      return { ok: true, hr_note: note };
    },
  });

  // 触发第 1 次保存
  const p1 = coordinator.handleBlur("请求 1");
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);

  // 在途期间再次输入 "请求 2"，再输入 "请求 3"
  coordinator.handleInput("请求 2");
  coordinator.handleInput("请求 3");
  assert.equal(coordinator.hasPending(), true);

  // 放行第 1 次请求
  finishFirst();
  await p1;

  // 等待排队的第 2 次保存完成
  await new Promise((r) => setTimeout(r, 60));

  assert.equal(calls.length, 2);
  assert.equal(calls[0], "请求 1");
  assert.equal(calls[1], "请求 3"); // 保证是最新的请求 3，而非旧的请求 2
  assert.equal(coordinator.getLastSaved(), "请求 3");
  assert.equal(coordinator.isInFlight(), false);
});

test("createAutoSaveCoordinator: handleComplete 保存未保存变动或保留原内容", async () => {
  const calls = [];
  const coordinator = createAutoSaveCoordinator({
    initialSaved: "已保存内容",
    saveFn: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
  });

  // 内容为空点完成：不调用 saveFn
  const resEmpty = await coordinator.handleComplete("");
  assert.equal(resEmpty.saved, false);
  assert.equal(resEmpty.note, "已保存内容");
  assert.equal(calls.length, 0);

  // 有未保存变动点完成：先调用 saveFn 保存再完成
  const resChanged = await coordinator.handleComplete("有新变动");
  assert.equal(resChanged.saved, true);
  assert.equal(resChanged.note, "有新变动");
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "有新变动");
});

test("isHrNoteOverflowing: 测量两行截断状态下实际溢出 (scrollHeight > clientHeight + 1)", () => {
  // 单行或未溢出：scrollHeight <= clientHeight + 1
  assert.equal(isHrNoteOverflowing(36, 36), false);
  assert.equal(isHrNoteOverflowing(37, 36), false); // 37 > 37 为 false (防 1px 抖动)
  assert.equal(isHrNoteOverflowing({ scrollHeight: 36, clientHeight: 36 }), false);

  // 超过两行溢出：scrollHeight > clientHeight + 1
  assert.equal(isHrNoteOverflowing(38, 36), true); // 38 > 37 为 true
  assert.equal(isHrNoteOverflowing(60, 36), true);
  assert.equal(isHrNoteOverflowing({ scrollHeight: 54, clientHeight: 36 }), true);
});

function createFakeTimer() {
  let timerId = 0;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => {
      const id = ++timerId;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimeout: (id) => {
      timers.delete(id);
    },
    advance: async (ms) => {
      const toRun = [];
      for (const [id, t] of Array.from(timers.entries())) {
        if (t.ms <= ms) {
          timers.delete(id);
          toRun.push(t.fn);
        }
      }
      for (const fn of toRun) {
        await fn();
      }
    },
    hasPending: () => timers.size > 0,
  };
}

test("createAutoSaveCoordinator: flush 取消防抖并立即保存变动（使用假定时器与假 save）", async () => {
  const fakeTimer = createFakeTimer();
  const calls = [];
  const statusEvents = [];

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: "初始内容",
    save: async (note) => {
      calls.push(note);
      return { ok: true, hr_note: note };
    },
    setTimeout: fakeTimer.setTimeout,
    clearTimeout: fakeTimer.clearTimeout,
    onStatusChange: (evt) => statusEvents.push(evt),
  });

  // 1. 输入触发防抖
  coordinator.onInput("输入未等防抖");
  assert.equal(calls.length, 0);
  assert.equal(fakeTimer.hasPending(), true);

  // 2. 立即 flush 取消防抖并执行保存
  const res = await coordinator.flush("输入未等防抖");
  assert.deepEqual(res, { ok: true, saved: true, note: "输入未等防抖" });
  assert.equal(fakeTimer.hasPending(), false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "输入未等防抖");
  assert.equal(coordinator.getLastSaved(), "输入未等防抖");

  // 3. 再次 flush 相同内容不重复保存
  const resSame = await coordinator.flush("输入未等防抖");
  assert.deepEqual(resSame, { ok: true, saved: false, note: "输入未等防抖" });
  assert.equal(calls.length, 1);

  // 4. flush 空内容不保存
  const resEmpty = await coordinator.flush("");
  assert.deepEqual(resEmpty, { ok: true, saved: false, note: "输入未等防抖" });
  assert.equal(calls.length, 1);
});

test("createAutoSaveCoordinator: 在途期间 onBlur + flush 只产生一个并发请求（内容不同排队最新一份）", async () => {
  const fakeTimer = createFakeTimer();
  const calls = [];
  let concurrent = 0;
  let maxConcurrent = 0;
  let finishFirst;
  const firstPromise = new Promise((r) => {
    finishFirst = r;
  });
  let finishSecond;
  const secondPromise = new Promise((r) => {
    finishSecond = r;
  });

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: null,
    save: async (note) => {
      concurrent++;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      calls.push(note);
      if (calls.length === 1) {
        await firstPromise;
      } else if (calls.length === 2) {
        await secondPromise;
      }
      concurrent--;
      return { ok: true, hr_note: note };
    },
    setTimeout: fakeTimer.setTimeout,
    clearTimeout: fakeTimer.clearTimeout,
  });

  // 请求 1 由失焦触发，进入在途
  const p1 = coordinator.onBlur("内容1");
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);
  assert.equal(concurrent, 1);
  assert.equal(maxConcurrent, 1);

  // 在途期间：再次 onBlur("内容2-过渡")，紧接着点击完成触发 flush("内容2-最终")
  coordinator.onBlur("内容2-过渡");
  const flushP = coordinator.flush("内容2-最终");

  // 验证：此时仍仅有 1 个请求在途，没有并发第 2 个请求
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);
  assert.equal(concurrent, 1);
  assert.equal(maxConcurrent, 1);

  // 放行请求 1
  finishFirst();
  for (let i = 0; i < 20 && calls.length < 2; i++) {
    await Promise.resolve();
  }

  // 请求 1 结束后，排队的最新一份（"内容2-最终"）开始执行
  assert.equal(calls.length, 2);
  assert.equal(calls[0], "内容1");
  assert.equal(calls[1], "内容2-最终");
  assert.equal(concurrent, 1);
  assert.equal(maxConcurrent, 1);

  // 放行请求 2
  finishSecond();
  const res = await flushP;
  await p1;
  assert.deepEqual(res, { ok: true, saved: true, note: "内容2-最终" });
  assert.equal(coordinator.getLastSaved(), "内容2-最终");
  assert.equal(coordinator.isInFlight(), false);
  assert.equal(maxConcurrent, 1);
});

test("createAutoSaveCoordinator: 在途期间 onBlur + flush 只产生一个并发请求（内容相同不发多余请求）", async () => {
  const fakeTimer = createFakeTimer();
  const calls = [];
  let concurrent = 0;
  let maxConcurrent = 0;
  let finishFirst;
  const firstPromise = new Promise((r) => {
    finishFirst = r;
  });

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: null,
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
    setTimeout: fakeTimer.setTimeout,
    clearTimeout: fakeTimer.clearTimeout,
  });

  // 失焦发起保存
  coordinator.onBlur("相同内容");
  assert.equal(coordinator.isInFlight(), true);
  assert.equal(calls.length, 1);
  assert.equal(concurrent, 1);

  // 用户点击"完成"触发 mousedown (blur) + click (flush)
  coordinator.onBlur("相同内容");
  const flushP = coordinator.flush("相同内容");

  // 并发数始终为 1，请求次数为 1
  assert.equal(calls.length, 1);
  assert.equal(maxConcurrent, 1);

  finishFirst();
  const res = await flushP;
  assert.deepEqual(res, { ok: true, saved: true, note: "相同内容" });
  assert.equal(calls.length, 1);
  assert.equal(maxConcurrent, 1);
  assert.equal(coordinator.getLastSaved(), "相同内容");
  assert.equal(coordinator.isInFlight(), false);
});

test("createAutoSaveCoordinator: flush 保存失败时返回错误结果且不自动重复重试", async () => {
  const fakeTimer = createFakeTimer();
  const calls = [];

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: "原本记录",
    save: async (note) => {
      calls.push(note);
      return { ok: false, error: "网络超时", message: "网络超时" };
    },
    setTimeout: fakeTimer.setTimeout,
    clearTimeout: fakeTimer.clearTimeout,
  });

  const res = await coordinator.flush("失败变动");
  assert.deepEqual(res, {
    ok: false,
    saved: false,
    note: "原本记录",
    error: "网络超时",
    message: "网络超时",
  });
  assert.equal(calls.length, 1);
  assert.equal(coordinator.getLastSaved(), "原本记录");
  assert.equal(coordinator.isInFlight(), false);
});

test("createAutoSaveCoordinator: 自动保存（onBlur）失败后内容不变再调用 flush 会重试保存且成功返回 saved: true", async () => {
  const fakeTimer = createFakeTimer();
  const calls = [];
  let attempt = 0;

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: "原本记录",
    save: async (note) => {
      attempt++;
      calls.push(note);
      if (attempt === 1) {
        return { ok: false, error: "网络超时", message: "网络超时" };
      }
      return { ok: true, hr_note: note };
    },
    setTimeout: fakeTimer.setTimeout,
    clearTimeout: fakeTimer.clearTimeout,
  });

  const blurRes = await coordinator.onBlur("新内容");
  assert.equal(calls.length, 1);
  assert.equal(calls[0], "新内容");
  assert.equal(blurRes.ok, false);
  assert.equal(coordinator.getLastSaved(), "原本记录");

  const flushRes = await coordinator.flush("新内容");
  assert.equal(calls.length, 2);
  assert.equal(calls[1], "新内容");
  assert.deepEqual(flushRes, {
    ok: true,
    saved: true,
    note: "新内容",
  });
  assert.equal(coordinator.getLastSaved(), "新内容");
  assert.equal(coordinator.isInFlight(), false);
});
