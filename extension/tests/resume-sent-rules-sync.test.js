// 体检第 75 条：插件判断「简历已发送」的规则与服务端规则文件一致，并且每条都真的生效
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { RESUME_SENT_RULES, isResumeSentMessage, hasHrResumeRequest } from "../src/chat-view.js";

const serverRules = JSON.parse(
  fs.readFileSync(new URL("../../src/jet/llm/prompts/action_card_rules.json", import.meta.url), "utf8")
)
  .filter((r) => r.marker === "[已发送附件简历]" || r.marker === "[HR 已查看附件简历]")
  .map((r) => ({
    body_type: typeof r.body_type === "number" ? r.body_type : null,
    text_contains: Array.isArray(r.text_contains) ? r.text_contains : [r.text_contains],
  }));

test("插件的 RESUME_SENT_RULES 与服务端规则文件逐条一致", () => {
  assert.ok(serverRules.length > 0);
  assert.deepEqual(RESUME_SENT_RULES, serverRules);
});

const hrRequest = { body_type: 7, is_self: false, text: "我想要一份您的附件简历，您是否同意" };

for (const rule of serverRules) {
  const sample = { body_type: rule.body_type ?? 1, is_self: true, text: `前缀${rule.text_contains.join("·")}后缀` };
  test(`服务端规则 ${JSON.stringify(rule)} 对应的消息算简历已发送`, () => {
    assert.equal(isResumeSentMessage(sample), true);
    assert.equal(hasHrResumeRequest([hrRequest, sample]), false);
  });
}

test("只有文件名的简历卡片（bodyType 12）也算已发送", () => {
  const card = { bodyType: 12, isSelf: true, text: "张三-数据分析.pdf" };
  assert.equal(hasHrResumeRequest([hrRequest, card]), false);
});

test("不相干的消息不算已发送", () => {
  assert.equal(isResumeSentMessage({ body_type: 1, text: "好的，我稍后发您" }), false);
  assert.equal(isResumeSentMessage({ body_type: 1, text: "附件.pdf" }), false); // 普通文字里的 .pdf 不算文件卡片
  assert.equal(isResumeSentMessage(null), false);
  assert.equal(hasHrResumeRequest([hrRequest, { body_type: 1, text: "收到" }]), true);
});
