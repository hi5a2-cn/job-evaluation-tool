import test from "node:test";
import assert from "node:assert/strict";
import {
  formatJudgeQuotaText,
  formatAssistQuotaText,
  computeAssistQuotaAfterGenerate,
} from "../src/chat-view.js";

test("formatJudgeQuotaText: 纯函数格式化判断配额文字", () => {
  // 对象传参
  assert.equal(
    formatJudgeQuotaText({ used: 3, limit: 150, remaining: 147 }),
    "判断：今日已用 3 / 上限 150，剩余 147"
  );
  // 多参数传参
  assert.equal(
    formatJudgeQuotaText(3, 150, 147),
    "判断：今日已用 3 / 上限 150，剩余 147"
  );
  // 缺省 remaining 自动根据 limit - used 计算
  assert.equal(
    formatJudgeQuotaText({ used: 5, limit: 100 }),
    "判断：今日已用 5 / 上限 100，剩余 95"
  );
  // 零值处理
  assert.equal(
    formatJudgeQuotaText({ used: 0, limit: 50, remaining: 50 }),
    "判断：今日已用 0 / 上限 50，剩余 50"
  );
});

test("formatAssistQuotaText: 纯函数格式化沟通建议配额文字", () => {
  // 对象传参（标准形态）
  assert.equal(
    formatAssistQuotaText({ used: 2, limit: 50, remaining: 48 }),
    "沟通建议：今日已用 2 / 上限 50，剩余 48"
  );
  // 多参数传参
  assert.equal(
    formatAssistQuotaText(2, 50, 48),
    "沟通建议：今日已用 2 / 上限 50，剩余 48"
  );
  // 仅传入 limit 和 remaining（如生成成功后的响应推导）
  assert.equal(
    formatAssistQuotaText({ limit: 50, remaining: 45 }),
    "沟通建议：今日已用 5 / 上限 50，剩余 45"
  );
  // 默认上限为 50
  assert.equal(
    formatAssistQuotaText({ used: 10, remaining: 40 }),
    "沟通建议：今日已用 10 / 上限 50，剩余 40"
  );
});

test("computeAssistQuotaAfterGenerate: 纯函数按 quota_remaining 计算新的已用与剩余", () => {
  // 1. 已配对状态下的格式化文字断言
  assert.equal(
    formatJudgeQuotaText({ used: 12, limit: 150, remaining: 138 }),
    "判断：今日已用 12 / 上限 150，剩余 138"
  );
  assert.equal(
    formatAssistQuotaText({ date: "2026-09-28", limit: 50, used: 3, remaining: 47 }),
    "沟通建议：今日已用 3 / 上限 50，剩余 47"
  );

  // 2. 生成成功后按 quota_remaining 立即更新计算
  const res = computeAssistQuotaAfterGenerate(42, 50);
  assert.deepEqual(res, { used: 8, limit: 50, remaining: 42 });
  assert.equal(
    formatAssistQuotaText(res),
    "沟通建议：今日已用 8 / 上限 50，剩余 42"
  );
});
