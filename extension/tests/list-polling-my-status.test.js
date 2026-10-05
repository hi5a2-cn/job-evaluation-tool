// 体检第 37 条：列表轮询按字段比较「我的状态」，同样的状态不再每一轮都当成变化而重绘
import test from "node:test";
import assert from "node:assert/strict";
import { hasEntryChanged } from "../src/scheduler.js";

const judgement = { status: "done", verdict: "apply" };
const entry = (my_status) => ({ company_name: "公司A", judgement: { ...judgement }, my_status });

test("同样的状态（每次请求得到的新对象）不算变化", () => {
  const prev = entry({ status: "applied", updated_at: "2026-10-05T10:00:00Z" });
  const next = entry({ status: "applied", updated_at: "2026-10-05T10:00:00Z" });
  assert.notEqual(prev.my_status, next.my_status);
  assert.equal(hasEntryChanged(prev, next), false);
});

test("状态值或更新时间不同算变化", () => {
  const prev = entry({ status: "applied", updated_at: "2026-10-05T10:00:00Z" });
  assert.equal(hasEntryChanged(prev, entry({ status: "rejected", updated_at: "2026-10-05T10:00:00Z" })), true);
  assert.equal(hasEntryChanged(prev, entry({ status: "applied", updated_at: "2026-10-05T11:00:00Z" })), true);
});

test("设置或清除状态算变化，前后都没有状态不算", () => {
  const withStatus = entry({ status: "applied", updated_at: "2026-10-05T10:00:00Z" });
  assert.equal(hasEntryChanged(entry(null), withStatus), true);
  assert.equal(hasEntryChanged(withStatus, entry(null)), true);
  assert.equal(hasEntryChanged(entry(null), entry(null)), false);
});

test("新结果没带 my_status 时不按状态判断", () => {
  const prev = entry({ status: "applied", updated_at: "2026-10-05T10:00:00Z" });
  const next = { company_name: "公司A", judgement: { ...judgement } };
  assert.equal(hasEntryChanged(prev, next), false);
});
