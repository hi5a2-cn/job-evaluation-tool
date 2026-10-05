import test from "node:test";
import assert from "node:assert/strict";
import {
  buildMyJobsPath,
  buildHrNotePayload,
  formatHrNoteErrorResponse,
  createRequestTracker,
  STATUS_SAVE_FAILED_NOTICE,
  rollbackStatusChange,
} from "../src/myjobs-view.js";

test("buildMyJobsPath: get_my_jobs 默认参数与缺失参数与原地址完全一致", () => {
  assert.equal(buildMyJobsPath(), "/v1/my-jobs?filter=all&limit=200");
  assert.equal(buildMyJobsPath({}), "/v1/my-jobs?filter=all&limit=200");
  assert.equal(
    buildMyJobsPath({ filter: "saved" }),
    "/v1/my-jobs?filter=saved&limit=200"
  );
  assert.equal(
    buildMyJobsPath({ filter: "all_jobs", hr_only: false, q: "" }),
    "/v1/my-jobs?filter=all_jobs&limit=200"
  );
  assert.equal(
    buildMyJobsPath({ filter: "recent", hr_only: null, q: "   " }),
    "/v1/my-jobs?filter=recent&limit=200"
  );
});

test("buildMyJobsPath: hr_only 为 true 时拼接 &hr_only=true，非 true 忽略", () => {
  assert.equal(
    buildMyJobsPath({ filter: "all", hr_only: true }),
    "/v1/my-jobs?filter=all&limit=200&hr_only=true"
  );
  assert.equal(
    buildMyJobsPath({ filter: "saved", hr_only: false }),
    "/v1/my-jobs?filter=saved&limit=200"
  );
  assert.equal(
    buildMyJobsPath({ filter: "saved", hr_only: "yes" }),
    "/v1/my-jobs?filter=saved&limit=200"
  );
});

test("buildMyJobsPath: q 非空时 encodeURIComponent 拼入 &q=，并支持与 hr_only 组合", () => {
  assert.equal(
    buildMyJobsPath({ filter: "all", q: "后端架构" }),
    "/v1/my-jobs?filter=all&limit=200&q=%E5%90%8E%E7%AB%AF%E6%9E%B6%E6%9E%84"
  );
  assert.equal(
    buildMyJobsPath({ filter: "all_jobs", hr_only: true, q: "C++ & Go" }),
    "/v1/my-jobs?filter=all_jobs&limit=200&hr_only=true&q=C%2B%2B%20%26%20Go"
  );
});

test("buildHrNotePayload: message.source 为 myjobs 或 card 时放进请求体，否则不加", () => {
  assert.deepEqual(buildHrNotePayload("面试过了", "myjobs"), {
    note: "面试过了",
    source: "myjobs",
  });
  assert.deepEqual(buildHrNotePayload("卡片备注", "card"), {
    note: "卡片备注",
    source: "card",
  });
  assert.deepEqual(buildHrNotePayload("普通备注", undefined), {
    note: "普通备注",
  });
  assert.deepEqual(buildHrNotePayload("其他来源", "other"), {
    note: "其他来源",
  });
  assert.deepEqual(buildHrNotePayload("", "myjobs"), {
    note: null,
    source: "myjobs",
  });
});

test("formatHrNoteErrorResponse: 失败时保留字段并额外透传服务端 message", () => {
  const mockRes = {
    ok: false,
    error: "empty_not_allowed",
    data: { message: "清空请到岗位卡片操作" },
  };
  const formatted = formatHrNoteErrorResponse(mockRes, "failed");
  assert.deepEqual(formatted, {
    ok: false,
    error: "empty_not_allowed",
    viewState: "failed",
    message: "清空请到岗位卡片操作",
  });

  const mockResNoMsg = {
    ok: false,
    error: "network_error",
  };
  const formattedNoMsg = formatHrNoteErrorResponse(mockResNoMsg, "jet_down");
  assert.deepEqual(formattedNoMsg, {
    ok: false,
    error: "network_error",
    viewState: "jet_down",
    message: undefined,
  });
});

test("createRequestTracker / isLatestRequest: 序号递增与乱序旧响应丢弃", () => {
  const tracker = createRequestTracker();
  assert.equal(tracker.current, 0);

  const req1 = tracker.next();
  assert.equal(req1, 1);
  assert.equal(tracker.isLatest(req1), true);

  const req2 = tracker.next();
  assert.equal(req2, 2);
  assert.equal(tracker.isLatest(req1), false);
  assert.equal(tracker.isLatest(req2), true);

  const req3 = tracker.next();
  assert.equal(req3, 3);
  assert.equal(tracker.isLatest(req1), false);
  assert.equal(tracker.isLatest(req2), false);
  assert.equal(tracker.isLatest(req3), true);
});

test("rollbackStatusChange: 状态保存失败恢复原状态与原计数并返回'状态保存失败'提示", () => {
  const prevCounts = {
    all_jobs: 12,
    all: 6,
    saved: 3,
    applied: 2,
    skipped: 1,
    recent: 5,
  };
  const res = rollbackStatusChange({
    previousStatus: "saved",
    previousCounts: prevCounts,
  });
  assert.equal(res.status, "saved");
  assert.deepEqual(res.counts, prevCounts);
  assert.equal(res.notice, STATUS_SAVE_FAILED_NOTICE);
  assert.equal(res.notice, "状态保存失败");
  // 确保返回的 counts 为独立浅拷贝，不影响原对象
  res.counts.saved = 99;
  assert.equal(prevCounts.saved, 3);
});
