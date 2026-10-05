import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

import {
  formatChatJobStatus,
  hasHrResumeRequest,
  decideResumeHighlightState,
  resetResumePromptOnSwitch,
  decideChatResumePromptState,
  formatResumeHighlightText,
} from "../src/chat-view.js";
import {
  describe,
  shouldShowResumeSuggestion,
} from "../src/view-state.js";

// ============================================================================
// 1. shouldShowResumeSuggestion 显示条件纯函数测试 (FR-006, FR-007, US1, T015)
// ============================================================================

test("shouldShowResumeSuggestion: 有有效建议且结论非 skip 时返回 true", () => {
  const validSuggestion = {
    slot: 1,
    name: "简历A",
    reason: "岗位工作以大模型提示词设计和用户运营为主，高度匹配该简历方向",
  };

  // v6 大模型判断：适合投递 (apply)
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    true
  );

  // 可以一试 (try)
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "try",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    true
  );

  // 需要确认 (check)
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "check",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    true
  );

  // 旧版结论映射 (fit -> apply, unsure -> check)
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "fit",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    true
  );
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "unsure",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    true
  );
});

test("shouldShowResumeSuggestion: 结论为不建议投（skip / unfit）时不显示，返回 false", () => {
  const validSuggestion = {
    slot: 1,
    name: "简历A",
    reason: "工作职责重合",
  };

  // verdict === 'skip'
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "skip",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    false
  );

  // verdict === 'unfit' (旧版 unfit 映射为 skip)
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "unfit",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    false
  );

  // view_state === 'done_skip'
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      view_state: "done_skip",
      source: "llm",
      resume_suggestion: validSuggestion,
    }),
    false
  );

  // verdict_label === '不建议投'
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict_label: "不建议投",
      resume_suggestion: validSuggestion,
    }),
    false
  );
});

test("shouldShowResumeSuggestion: 规则判断（source === 'rule'）不显示，返回 false", () => {
  const validSuggestion = {
    slot: 1,
    name: "简历A",
    reason: "工作职责重合",
  };

  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "rule",
      resume_suggestion: validSuggestion,
    }),
    false
  );

  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "skip",
      source: "rule",
      resume_suggestion: null,
    }),
    false
  );
});

test("shouldShowResumeSuggestion: 旧判断或无建议时返回 false", () => {
  // resume_suggestion 为 null
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: null,
    }),
    false
  );

  // resume_suggestion 缺失
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
    }),
    false
  );
});

test("shouldShowResumeSuggestion: name 或 reason 为空/纯空格时返回 false", () => {
  // name 为空字符串
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { name: "", reason: "职责匹配" },
    }),
    false
  );

  // name 纯空格
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { name: "   \t", reason: "职责匹配" },
    }),
    false
  );

  // reason 为空字符串
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { name: "简历A", reason: "" },
    }),
    false
  );

  // reason 纯空格
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { name: "简历A", reason: "   \n  " },
    }),
    false
  );

  // 缺少字段
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { name: "简历A" },
    }),
    false
  );
  assert.equal(
    shouldShowResumeSuggestion({
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: { reason: "职责匹配" },
    }),
    false
  );
});

test("shouldShowResumeSuggestion: 空值与非对象入参安全返回 false", () => {
  assert.equal(shouldShowResumeSuggestion(null), false);
  assert.equal(shouldShowResumeSuggestion(undefined), false);
  assert.equal(shouldShowResumeSuggestion(""), false);
  assert.equal(shouldShowResumeSuggestion(123), false);
  assert.equal(shouldShowResumeSuggestion({}), false);
});

test("shouldShowResumeSuggestion: status 为 queued/running/failed 等非 done 状态时不显示，返回 false", () => {
  const validSuggestion = {
    slot: 1,
    name: "简历A",
    reason: "工作职责重合",
  };

  const nonDoneStatuses = ["queued", "running", "failed", "quota_exhausted", "interrupted"];
  for (const status of nonDoneStatuses) {
    assert.equal(
      shouldShowResumeSuggestion({
        status,
        verdict: "apply",
        source: "llm",
        resume_suggestion: validSuggestion,
      }),
      false,
      `status 为 ${status} 时不应显示简历建议`
    );
  }
});

test("content.js 与 view-state.js 的 shouldShowResumeSuggestion 纯函数一致性", () => {
  const contentPath = new URL("../src/content.js", import.meta.url);
  const contentSrc = fs.readFileSync(contentPath, "utf8");

  assert.ok(
    contentSrc.includes("BEGIN SYNC resume-suggestion"),
    "content.js 应包含 BEGIN SYNC resume-suggestion 标记"
  );
  assert.ok(
    contentSrc.includes("END SYNC resume-suggestion"),
    "content.js 应包含 END SYNC resume-suggestion 标记"
  );

  const fnMatch = contentSrc.match(
    /function shouldShowResumeSuggestion\(judgement\) \{[\s\S]*?\n  \}/
  );
  assert.ok(fnMatch, "应在 content.js 中提取出 shouldShowResumeSuggestion");

  const sandbox = {
    LEGACY_VERDICT_MAP: {
      fit: "apply",
      unsure: "check",
      unfit: "skip",
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(fnMatch[0], sandbox);

  const testCases = [
    null,
    undefined,
    {},
    { status: "queued", verdict: "apply", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "running", verdict: "apply", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "failed", verdict: "apply", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "done", verdict: "apply", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "done", verdict: "skip", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "done", verdict: "unfit", source: "llm", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "done", verdict: "apply", source: "rule", resume_suggestion: { name: "简历A", reason: "b" } },
    { status: "done", verdict: "apply", source: "llm", resume_suggestion: null },
    { status: "done", verdict: "apply", source: "llm", resume_suggestion: { name: "", reason: "b" } },
    { status: "done", verdict: "apply", source: "llm", resume_suggestion: { name: "简历A", reason: "   " } },
    { status: "done", verdict_label: "不建议投", resume_suggestion: { name: "简历A", reason: "b" } },
    { verdict_label: "不建议投", resume_suggestion: { name: "简历A", reason: "b" } },
  ];

  for (const tc of testCases) {
    const fromModule = shouldShowResumeSuggestion(tc);
    const fromContent = sandbox.shouldShowResumeSuggestion(tc);
    assert.equal(
      fromContent,
      fromModule,
      `用例 ${JSON.stringify(tc)} 在 content.js 与 view-state.js 中的判定结果必须一致`
    );
  }
});

// ============================================================================
// 2. formatChatJobStatus 纯函数输出测试 (FR-007, US1, T015)
// ============================================================================

test("formatChatJobStatus: 有建议且结论适合投递时正确输出 resume_suggestion", () => {
  const suggestion = {
    slot: 1,
    name: "简历A",
    reason: "核心职责为提示词调试与内容运营，匹配画像方向",
  };

  const jobEntry = {
    title: "AI产品运营专家",
    company_name: "某智能科技有限公司",
    judgement: {
      status: "done",
      verdict: "apply",
      source: "llm",
      prompt_version: "v6",
      verdict_reason: "核心职责为提示词调试与内容运营，匹配画像方向",
      resume_suggestion: suggestion,
      hr_questions: [],
      stale: { job_changed: false, profile_changed: false, method_changed: false },
      judged_at: "2026-09-29T10:00:00Z",
    },
    hr_note: null,
    my_status: null,
  };

  const result = formatChatJobStatus("job_123", jobEntry);

  assert.equal(result.platform_job_id, "job_123");
  assert.equal(result.in_library, true);
  assert.equal(result.verdict_label, "适合投递");
  assert.deepEqual(result.resume_suggestion, suggestion);
  assert.equal(result.resume_suggestion.name, "简历A");
  assert.equal(result.resume_suggestion.reason, "核心职责为提示词调试与内容运营，匹配画像方向");
});

test("formatChatJobStatus: 结论为不建议投（skip）时 resume_suggestion 为 null", () => {
  const jobEntry = {
    title: "变相销售经理",
    company_name: "某销售公司",
    judgement: {
      status: "done",
      verdict: "skip",
      source: "llm",
      prompt_version: "v6",
      verdict_reason: "涉及变相销售",
      resume_suggestion: {
        slot: 1,
        name: "简历A",
        reason: "虽然职责有部分匹配但被排除",
      },
    },
  };

  const result = formatChatJobStatus("job_skip", jobEntry);

  assert.equal(result.in_library, true);
  assert.equal(result.verdict_label, "不建议投");
  assert.equal(result.resume_suggestion, null);
});

test("formatChatJobStatus: 规则判断时 resume_suggestion 为 null", () => {
  const jobEntry = {
    title: "房地产渠道主管",
    company_name: "某地产公司",
    judgement: {
      status: "done",
      verdict: "skip",
      source: "rule",
      prompt_version: "rule",
      verdict_reason: "重点排查行业",
      resume_suggestion: null,
    },
  };

  const result = formatChatJobStatus("job_rule", jobEntry);

  assert.equal(result.in_library, true);
  assert.equal(result.resume_suggestion, null);
});

test("formatChatJobStatus: 旧版本判断（无 resume_suggestion）输出 null", () => {
  const jobEntry = {
    title: "数据分析师",
    company_name: "某数据公司",
    judgement: {
      status: "done",
      verdict: "try",
      source: "llm",
      prompt_version: "v5",
      verdict_reason: "职责基本匹配",
      resume_suggestion: null,
    },
  };

  const result = formatChatJobStatus("job_v5", jobEntry);

  assert.equal(result.in_library, true);
  assert.equal(result.verdict_label, "可以一试");
  assert.equal(result.resume_suggestion, null);
});

test("formatChatJobStatus: 岗位不在库或未判断时 resume_suggestion 为 null", () => {
  // 不在库
  const notInLibResult = formatChatJobStatus("job_not_in_lib", null);
  assert.equal(notInLibResult.in_library, false);
  assert.equal(notInLibResult.resume_suggestion, null);

  // 在库但未判断
  const inLibNoJudge = formatChatJobStatus("job_no_judge", {
    title: "开发岗",
    seen_in_chat: true,
    judgement: null,
  });
  assert.equal(inLibNoJudge.in_library, true);
  assert.equal(inLibNoJudge.resume_suggestion, null);
});

// ============================================================================
// 3. describe 纯函数中的 resume_suggestion 支持测试
// ============================================================================

test("describe: 包含合规简历建议时输出 resume_suggestion，skip / 规则判断输出 null", () => {
  const validSuggestion = {
    slot: 2,
    name: "简历B",
    reason: "岗位以数据分析与临床数据管理为主",
  };

  // 1. 合规输出
  const descApply = describe(
    {
      status: "done",
      verdict: "apply",
      source: "llm",
      resume_suggestion: validSuggestion,
    },
    "done_apply"
  );
  assert.deepEqual(descApply.resume_suggestion, validSuggestion);
  assert.equal(descApply.source, "llm");

  // 2. 结论为 skip 时输出 null
  const descSkip = describe(
    {
      status: "done",
      verdict: "skip",
      source: "llm",
      resume_suggestion: validSuggestion,
    },
    "done_skip"
  );
  assert.equal(descSkip.resume_suggestion, null);

  // 3. 规则判断时输出 null
  const descRule = describe(
    {
      status: "done",
      verdict: "apply",
      source: "rule",
      resume_suggestion: validSuggestion,
    },
    "done_apply"
  );
  assert.equal(descRule.resume_suggestion, null);

  // 4. 无建议时输出 null
  const descNone = describe(
    {
      status: "done",
      verdict: "try",
      source: "llm",
      resume_suggestion: null,
    },
    "done_try"
  );
  assert.equal(descNone.resume_suggestion, null);
});

// ============================================================================
// 4. hasHrResumeRequest 纯函数测试 (FR-008, US3, T017, T019)
// ============================================================================

test("hasHrResumeRequest: HR 发送的索要简历卡片（body_type 7 且含'简历'）返回 true", () => {
  // snake_case 字段
  const msg1 = [
    {
      sender: "HR",
      is_self: false,
      type: 1,
      body_type: 7,
      text: "我想要一份您的附件简历，您是否同意？",
    },
  ];
  assert.equal(hasHrResumeRequest(msg1), true);

  // camelCase 字段（兼容原始消息结构）
  const msg2 = [
    {
      sender: "HR",
      isSelf: false,
      bodyType: 7,
      text: "HR 向您索取简历",
    },
  ];
  assert.equal(hasHrResumeRequest(msg2), true);
});

test("hasHrResumeRequest: 交换微信或电话卡片（body_type 7 但不含'简历'）返回 false", () => {
  // 交换微信卡片
  const wechatMsg = [
    {
      sender: "HR",
      is_self: false,
      body_type: 7,
      text: "我想与您交换微信，您是否同意？",
    },
  ];
  assert.equal(hasHrResumeRequest(wechatMsg), false);

  // 交换电话号码卡片
  const phoneMsg = [
    {
      sender: "HR",
      is_self: false,
      body_type: 7,
      text: "我想与您交换电话号码，方便后续联系",
    },
  ];
  assert.equal(hasHrResumeRequest(phoneMsg), false);
});

test("hasHrResumeRequest: 自己发送的请求卡片（is_self / isSelf 为 true）返回 false", () => {
  // 自己发的 body_type 7
  const selfMsg1 = [
    {
      sender: "我",
      is_self: true,
      body_type: 7,
      text: "我想要一份您的附件简历，您是否同意？",
    },
  ];
  assert.equal(hasHrResumeRequest(selfMsg1), false);

  // 自己发的 bodyType 7 (camelCase)
  const selfMsg2 = [
    {
      sender: "我",
      isSelf: true,
      bodyType: 7,
      text: "我想要一份您的附件简历",
    },
  ];
  assert.equal(hasHrResumeRequest(selfMsg2), false);
});

test("hasHrResumeRequest: 普通文本消息含'简历'但 body_type 为 1 返回 false", () => {
  const textMsg = [
    {
      sender: "HR",
      is_self: false,
      body_type: 1,
      text: "你好，请把你的个人简历发我看看",
    },
  ];
  assert.equal(hasHrResumeRequest(textMsg), false);

  const textMsgCamel = [
    {
      sender: "HR",
      isSelf: false,
      bodyType: 1,
      text: "看简历觉得不错",
    },
  ];
  assert.equal(hasHrResumeRequest(textMsgCamel), false);
});

test("hasHrResumeRequest: 混合多条消息的匹配与排除", () => {
  // 包含招呼、普通文字、换微信卡片、以及 HR 索要简历卡片 -> 应返回 true
  const mixedWithResume = [
    { sender: "HR", is_self: false, body_type: 1, text: "你好！" },
    { sender: "我", is_self: true, body_type: 1, text: "您好，已投递该职位" },
    { sender: "HR", is_self: false, body_type: 7, text: "我想与您交换微信，您是否同意？" },
    { sender: "HR", is_self: false, body_type: 7, text: "我想要一份您的附件简历，您是否同意？" },
  ];
  assert.equal(hasHrResumeRequest(mixedWithResume), true);

  // 包含多种消息但不含索要简历卡片 -> 应返回 false
  const mixedWithoutResume = [
    { sender: "HR", is_self: false, body_type: 1, text: "请发简历到邮箱" },
    { sender: "我", is_self: true, body_type: 7, text: "我想要一份您的附件简历" },
    { sender: "HR", is_self: false, body_type: 7, text: "我想与您交换电话号码" },
  ];
  assert.equal(hasHrResumeRequest(mixedWithoutResume), false);
});

test("hasHrResumeRequest: 空值、空数组与畸形入参安全返回 false", () => {
  assert.equal(hasHrResumeRequest(null), false);
  assert.equal(hasHrResumeRequest(undefined), false);
  assert.equal(hasHrResumeRequest([]), false);
  assert.equal(hasHrResumeRequest("not an array"), false);
  assert.equal(hasHrResumeRequest(123), false);
  assert.equal(hasHrResumeRequest([null, undefined, {}, { text: "简历" }]), false);
});

test("hasHrResumeRequest: 卡片后出现已发送或已查看返回 false，之后再有新卡片返回 true", () => {
  // 1. 卡片后有'已发送给Boss'（含简历）-> false
  const msgsSent = [
    { sender: "HR", is_self: false, body_type: 7, text: "我想要一份您的附件简历，您是否同意" },
    { sender: "系统", is_self: false, is_system: true, text: "您的附件简历 李明_简历 已发送给Boss点击查看附件" },
  ];
  assert.equal(hasHrResumeRequest(msgsSent), false);

  // 2. 卡片后有'对方已查看了您的附件简历' -> false
  const msgsViewed = [
    { sender: "HR", is_self: false, body_type: 7, text: "我想要一份您的附件简历，您是否同意" },
    { sender: "系统", is_self: false, is_system: true, text: "对方已查看了您的附件简历" },
  ];
  assert.equal(hasHrResumeRequest(msgsViewed), false);

  // 3. 只有卡片 -> true
  const msgsOnlyCard = [
    { sender: "HR", is_self: false, body_type: 7, text: "我想要一份您的附件简历，您是否同意" },
  ];
  assert.equal(hasHrResumeRequest(msgsOnlyCard), true);

  // 4. 已发送之后 HR 又发新的要简历卡片 -> true
  const msgsReCard = [
    { sender: "HR", is_self: false, body_type: 7, text: "我想要一份您的附件简历，您是否同意" },
    { sender: "系统", is_self: false, is_system: true, text: "您的附件简历 李明_简历 已发送给Boss点击查看附件" },
    { sender: "HR", is_self: false, body_type: 7, text: "更新版简历发一份？我想要一份您的附件简历，您是否同意" },
  ];
  assert.equal(hasHrResumeRequest(msgsReCard), true);
});

// ============================================================================
// 5. decideResumeHighlightState 突出状态决策纯函数测试 (FR-008, US3, T017, T019)
// ============================================================================

test("decideResumeHighlightState: 有 HR 请求且岗位有简历建议时输出 highlight", () => {
  const suggestion = {
    slot: 1,
    name: "简历A",
    reason: "岗位职责契合",
  };

  // 适合投递岗位
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: suggestion,
    }),
    "highlight"
  );

  // 可以一试岗位
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "可以一试",
      resume_suggestion: suggestion,
    }),
    "highlight"
  );

  // 需要确认岗位
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "需要确认",
      resume_suggestion: suggestion,
    }),
    "highlight"
  );
});

test("decideResumeHighlightState: 有 HR 请求、岗位已有判断但无建议时输出 hint", () => {
  // v5 旧判断（已有判断，verdict_label 适合投递，但 resume_suggestion 为 null）
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: null,
    }),
    "hint"
  );

  // resume_suggestion 缺失
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "可以一试",
    }),
    "hint"
  );

  // resume_suggestion 名称为空串（无效建议视为无建议）
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "需要确认",
      resume_suggestion: { name: "   ", reason: "有些理由" },
    }),
    "hint"
  );

  // 岗位有 judged_at
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      judged_at: "2026-09-29T12:00:00Z",
      resume_suggestion: null,
    }),
    "hint"
  );

});

test("decideResumeHighlightState: 有 HR 请求、结论为不建议投且无建议时输出 none", () => {
  // verdict_label 为 "不建议投" 且无建议时返回 none
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "不建议投",
      resume_suggestion: null,
    }),
    "none"
  );

  // formatChatJobStatus 输出中表示 skip 结论的岗位对象
  const skipJobStatus = formatChatJobStatus("job_skip", {
    title: "变相销售经理",
    company_name: "某销售公司",
    judgement: {
      status: "done",
      verdict: "skip",
      source: "llm",
      verdict_reason: "涉及变相销售",
      resume_suggestion: null,
    },
  });
  assert.equal(decideResumeHighlightState(true, skipJobStatus), "none");
});

test("decideResumeHighlightState: 有 HR 请求但岗位未判断或不在库时输出 none", () => {
  // 岗位在库但未判断（notice 存在且无 verdict_label）
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: null,
      notice: "点「查看职位」获取详情并判断",
      resume_suggestion: null,
    }),
    "none"
  );

  // 岗位不在库中
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: false,
      verdict_label: null,
      notice: "未在岗位库中",
      resume_suggestion: null,
    }),
    "none"
  );

  // verdict_label 为 "未判断"
  assert.equal(
    decideResumeHighlightState(true, {
      in_library: true,
      verdict_label: "未判断",
      resume_suggestion: null,
    }),
    "none"
  );
});

test("decideResumeHighlightState: 无 HR 请求时一律输出 none", () => {
  const suggestion = {
    slot: 1,
    name: "简历A",
    reason: "职责匹配",
  };

  // 即使岗位有建议，若无 HR 请求也不突出提示
  assert.equal(
    decideResumeHighlightState(false, {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: suggestion,
    }),
    "none"
  );

  // 无 HR 请求、岗位已有判断无建议
  assert.equal(
    decideResumeHighlightState(false, {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: null,
    }),
    "none"
  );

  // 无 HR 请求、岗位未判断
  assert.equal(
    decideResumeHighlightState(false, {
      in_library: true,
      verdict_label: null,
      notice: "点「查看职位」获取详情并判断",
    }),
    "none"
  );
});

test("decideResumeHighlightState: 空值与非对象入参安全输出 none", () => {
  assert.equal(decideResumeHighlightState(true, null), "none");
  assert.equal(decideResumeHighlightState(true, undefined), "none");
  assert.equal(decideResumeHighlightState(true, {}), "none");
  assert.equal(decideResumeHighlightState(false, null), "none");
  assert.equal(decideResumeHighlightState(null, null), "none");
});

// ============================================================================
// 6. 会话切换与岗位一致性纯函数测试 (FR-008, US3, T018, T019)
// ============================================================================

test("resetResumePromptOnSwitch: 清除旧提示并返回重置后的状态对象", () => {
  const oldState = {
    hasHrResumeRequest: true,
    resumeRequestJobId: "job_old",
    highlightState: "highlight",
    customExtra: 123,
  };

  const resetState = resetResumePromptOnSwitch(oldState);
  assert.equal(resetState.hasHrResumeRequest, false);
  assert.equal(resetState.resumeRequestJobId, null);
  assert.equal(resetState.highlightState, "none");
  // 保持原有额外属性不丢失
  assert.equal(resetState.customExtra, 123);

  // 默认空对象传参
  const defaultReset = resetResumePromptOnSwitch();
  assert.equal(defaultReset.hasHrResumeRequest, false);
  assert.equal(defaultReset.resumeRequestJobId, null);
  assert.equal(defaultReset.highlightState, "none");
});

test("decideChatResumePromptState: 岗位 ID 一致时正常输出决策", () => {
  const suggestion = {
    slot: 1,
    name: "简历A",
    reason: "职责匹配",
  };

  const res = decideChatResumePromptState({
    currentChatJobId: "job_100",
    requestJobId: "job_100",
    hasHrResumeRequest: true,
    jobStatus: {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: suggestion,
    },
  });

  assert.equal(res.state, "highlight");
  assert.equal(res.hasHrResumeRequest, true);
  assert.equal(res.isCurrent, true);
});

test("decideChatResumePromptState: 岗位 ID 不一致或切换后旧会话不残留突出提示", () => {
  const suggestion = {
    slot: 1,
    name: "简历A",
    reason: "职责匹配",
  };

  // requestJobId 为上一会话岗位 job_old，而当前会话岗位已变为 job_new
  const mismatchRes = decideChatResumePromptState({
    currentChatJobId: "job_new",
    requestJobId: "job_old",
    hasHrResumeRequest: true,
    jobStatus: {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: suggestion,
    },
  });

  // 岗位 ID 不一致时，绝不展示上一会话的 highlight，返回 none
  assert.equal(mismatchRes.state, "none");
  assert.equal(mismatchRes.hasHrResumeRequest, false);
  assert.equal(mismatchRes.isCurrent, false);

  // requestJobId 为 null（切换会话清空后）
  const nullRequestRes = decideChatResumePromptState({
    currentChatJobId: "job_new",
    requestJobId: null,
    hasHrResumeRequest: true,
    jobStatus: {
      in_library: true,
      verdict_label: "适合投递",
      resume_suggestion: suggestion,
    },
  });

  assert.equal(nullRequestRes.state, "none");
  assert.equal(nullRequestRes.isCurrent, false);
});

// ============================================================================
// 7. formatResumeHighlightText 突出提示纯函数测试 (FR-008, US3)
// ============================================================================

test("formatResumeHighlightText: 突出提示使用 name 字段并展示「HR 在要简历，建议投：<name>」", () => {
  const suggestion = {
    slot: 1,
    name: "后端开发简历",
    reason: "岗位技术栈与经历高度重合",
  };

  const jobStatus = {
    platform_job_id: "job_123",
    in_library: true,
    resume_suggestion: suggestion,
  };

  // 1. 验证纯函数由状态生成突出提示文字时读取 name 字段并生成规范文案
  assert.equal(
    formatResumeHighlightText(jobStatus),
    "HR 在要简历，建议投：后端开发简历"
  );

  // 2. 异常与边界入参安全回退
  assert.equal(formatResumeHighlightText(null), "HR 在要简历，建议投：");
  assert.equal(formatResumeHighlightText({}), "HR 在要简历，建议投：");
  assert.equal(
    formatResumeHighlightText({ resume_suggestion: null }),
    "HR 在要简历，建议投："
  );
  assert.equal(
    formatResumeHighlightText({ resume_suggestion: { name: "" } }),
    "HR 在要简历，建议投："
  );

  // 3. sidepanel.js 源码检查：调用 formatResumeHighlightText 展示文案
  const sidepanelPath = new URL("../src/sidepanel.js", import.meta.url);
  const sidepanelSrc = fs.readFileSync(sidepanelPath, "utf8");
  assert.ok(
    sidepanelSrc.includes("bannerTitle.textContent = formatResumeHighlightText(currentChatJobStatus);"),
    "sidepanel.js 应调用 formatResumeHighlightText(currentChatJobStatus)"
  );
});
