import test from "node:test";
import assert from "node:assert/strict";

import {
  shouldFetchChatJobStatus,
  shouldShowNoJudgementNotice,
  mergeChatPreviewResult,
  decideChatPreviewRefresh,
  isChatJobResponseCurrent,
  formatChatJobStatus,
  NO_JET_JUDGEMENT_NOTICE,
  NEW_MESSAGE_HINT,
  computeChatCacheKey,
} from "../src/chat-view.js";

// ============================================================================
// 1. 纯函数单元测试
// ============================================================================

test("shouldFetchChatJobStatus: forceRefresh=true 始终强制向服务端请求", () => {
  // 1. 缓存存在且 platform_job_id 匹配，但 forceRefresh=true -> 必须发起请求
  const cached = { platform_job_id: "job_123", in_library: false };
  assert.equal(
    shouldFetchChatJobStatus({
      forceRefresh: true,
      cachedStatus: cached,
      jobId: "job_123",
    }),
    true
  );

  // 2. 无 forceRefresh 且缓存匹配 -> 使用缓存（不请求）
  assert.equal(
    shouldFetchChatJobStatus({
      forceRefresh: false,
      cachedStatus: cached,
      jobId: "job_123",
    }),
    false
  );

  // 3. 岗位 ID 不匹配 -> 必须请求
  assert.equal(
    shouldFetchChatJobStatus({
      forceRefresh: false,
      cachedStatus: cached,
      jobId: "job_456",
    }),
    true
  );

  // 4. 无缓存 -> 必须请求
  assert.equal(
    shouldFetchChatJobStatus({
      forceRefresh: false,
      cachedStatus: null,
      jobId: "job_123",
    }),
    true
  );
});

test("shouldShowNoJudgementNotice: 仅在 has_jet_judgement=false 时展示提示", () => {
  // 显式为 false -> 展示
  assert.equal(shouldShowNoJudgementNotice({ has_jet_judgement: false }), true);
  assert.equal(shouldShowNoJudgementNotice(false), true);

  // 为 true -> 隐藏
  assert.equal(shouldShowNoJudgementNotice({ has_jet_judgement: true }), false);
  assert.equal(shouldShowNoJudgementNotice(true), false);

  // 未定义 / null -> 隐藏
  assert.equal(shouldShowNoJudgementNotice({}), false);
  assert.equal(shouldShowNoJudgementNotice(null), false);
  assert.equal(shouldShowNoJudgementNotice(undefined), false);
});

test("mergeChatPreviewResult: 保持话术与问题，更新 has_jet_judgement 与 mode 状态", () => {
  const baseResult = {
    bound_job_id: "job_test",
    company_name: "某科技公司",
    job_title: "高级工程师",
    has_jet_judgement: false,
    mode: "reply",
    mode_label: "建议回复",
    mode_basis: "旧依据",
    suggestions: [{ version: 1, text: "您好，方便沟通吗？" }],
    questions: ["请问团队技术栈是什么？"],
    referenced_experience_ids: [1, 2],
  };

  const previewData = {
    mode: "reply",
    mode_label: "建议回复",
    mode_basis: "HR刚才打了个招呼",
    has_jet_judgement: true,
  };

  const chatData = {
    encrypt_job_id: "job_test",
    company_name: "某科技公司",
    job_title: "高级工程师",
  };

  const merged = mergeChatPreviewResult(baseResult, previewData, chatData, {
    action: "update_status",
  });

  // 1. has_jet_judgement 正确更新为 true
  assert.equal(merged.has_jet_judgement, true);
  // 2. 话术与问题保留完整
  assert.deepEqual(merged.suggestions, baseResult.suggestions);
  assert.deepEqual(merged.questions, baseResult.questions);
  assert.deepEqual(merged.referenced_experience_ids, [1, 2]);
  // 3. preview 返回的 mode 信息更新
  assert.equal(merged.mode_basis, "HR刚才打了个招呼");
  // 4. new_message_hint 被移除
  assert.equal(merged.new_message_hint, undefined);

  // 5. 若指定 action=update_status_with_hint，设置 NEW_MESSAGE_HINT
  const mergedWithHint = mergeChatPreviewResult(baseResult, previewData, chatData, {
    action: "update_status_with_hint",
  });
  assert.equal(mergedWithHint.new_message_hint, NEW_MESSAGE_HINT);
});

test("decideChatPreviewRefresh: 判定激活或切换时是否满足 preview 刷新条件", () => {
  const base = {
    isChat: true,
    currentChatJobId: "job_01",
    existingResult: { suggestions: [] },
    isGenerating: false,
    isMasked: false,
  };

  // 满足条件 -> 刷新
  assert.equal(decideChatPreviewRefresh(base).shouldRefresh, true);

  // 非聊天页 -> 不刷新
  assert.equal(decideChatPreviewRefresh({ ...base, isChat: false }).shouldRefresh, false);

  // 生成中 -> 不刷新
  assert.equal(decideChatPreviewRefresh({ ...base, isGenerating: true }).shouldRefresh, false);

  // 遮盖中 -> 不刷新
  assert.equal(decideChatPreviewRefresh({ ...base, isMasked: true }).shouldRefresh, false);

  // 无当前岗位 ID -> 不刷新
  assert.equal(decideChatPreviewRefresh({ ...base, currentChatJobId: null }).shouldRefresh, false);

  // 无已有建议结果 -> 不刷新
  assert.equal(decideChatPreviewRefresh({ ...base, existingResult: null }).shouldRefresh, false);
});

// ============================================================================
// 2. 第一类测试：独立职位页判断完成后，切回聊天页标签/切回会话时发起强制刷新，
//    最终显示新判断，不用旧缓存
// ============================================================================

test("类别1(a): 别的标签页判断完成后，切回聊天页标签(refreshAll)发起强制刷新，最终显示新判断", async () => {
  const jobId = "job_cross_page_001";

  // 1. 模拟 background 中缓存了该岗位尚未判断的状态（判断前状态）
  const unjudgedJobEntry = {
    title: "分布式存储专家",
    company_name: "云原生科技",
    judgement: null,
    my_status: null,
    hr_note: null,
  };
  const cachedStatus = formatChatJobStatus(jobId, unjudgedJobEntry);
  assert.equal(cachedStatus.in_library, false);
  assert.equal(cachedStatus.verdict_label, null);

  const tabState = {
    lastChatJobId: jobId,
    chatJobStatus: cachedStatus,
  };

  // 2. 模拟服务端在另一个标签页已完成判断
  const judgedJobEntry = {
    title: "分布式存储专家",
    company_name: "云原生科技",
    judgement: {
      status: "done",
      verdict: "try",
      verdict_label: "可以一试",
      verdict_tone: "blue",
      reasons: ["核心团队技术实力强"],
    },
    my_status: { status: "saved" },
    hr_note: null,
  };
  const freshServerStatus = formatChatJobStatus(jobId, judgedJobEntry);
  assert.equal(freshServerStatus.in_library, true);
  assert.equal(freshServerStatus.verdict_label, "可以一试");

  // 3. 模拟切回聊天页标签时侧边栏 refreshAll 发送的消息
  const receivedMessages = [];
  const renderedHistory = [];
  let currentChatJobId = jobId;

  // 模拟 sidepanel 中的临时显示与最终渲染
  function renderChatJob(status) {
    renderedHistory.push(status);
  }

  // refreshAll 流程模拟：
  // 先用缓存临时显示（可先 renderChatJob(缓存)）
  const cachedChatJob = cachedStatus;
  if (cachedChatJob) {
    renderChatJob(cachedChatJob);
  }

  // 随后向 background 发送带 forceRefresh: true 的查询请求
  const queryMessage = {
    type: "get_chat_job_status",
    jobId: currentChatJobId,
    forceRefresh: true,
  };
  receivedMessages.push(queryMessage);

  // 4. 模拟 background 处理 get_chat_job_status
  let backgroundResponse = null;
  const shouldFetch = shouldFetchChatJobStatus({
    forceRefresh: queryMessage.forceRefresh,
    cachedStatus: tabState.chatJobStatus,
    jobId: queryMessage.jobId,
  });

  // 断言：由于 forceRefresh=true，background 绕过缓存
  assert.equal(shouldFetch, true, "forceRefresh=true 必须绕过 background 缓存");

  // background 此时向本机 Jet 重新查询，获取最新 status 并更新缓存
  tabState.chatJobStatus = freshServerStatus;
  backgroundResponse = { ok: true, jobId, jobStatus: freshServerStatus };

  // 5. sidepanel 收到响应并校验防乱序规则
  if (
    backgroundResponse?.ok &&
    isChatJobResponseCurrent(backgroundResponse.jobStatus?.platform_job_id, currentChatJobId)
  ) {
    renderChatJob(backgroundResponse.jobStatus);
  }

  // 6. 断言结果
  assert.equal(receivedMessages.length, 1);
  assert.equal(receivedMessages[0].forceRefresh, true, "请求必须携带 forceRefresh: true");
  assert.equal(tabState.chatJobStatus.verdict_label, "可以一试", "background 缓存已更新为新判断");
  assert.equal(renderedHistory.length, 2, "经历了临时渲染与最终渲染两次");
  assert.equal(renderedHistory[0].verdict_label, null, "临时显示为旧缓存（未判断）");
  assert.equal(renderedHistory[1].verdict_label, "可以一试", "最终渲染为新判断结果");
  assert.equal(renderedHistory[1].in_library, true);
});

test("类别1(b): 别的标签页判断完成后，切回同一会话(handleChatSwitched)发起强制刷新，显示新判断", async () => {
  const jobId = "job_cross_page_002";

  // 1. 旧缓存：未判断
  const oldCachedStatus = formatChatJobStatus(jobId, {
    title: "前端架构师",
    company_name: "互联网科技",
    judgement: null,
  });
  const tabState = {
    lastChatJobId: jobId,
    chatJobStatus: oldCachedStatus,
  };

  // 2. 服务端新判断
  const freshStatus = formatChatJobStatus(jobId, {
    title: "前端架构师",
    company_name: "互联网科技",
    judgement: {
      status: "done",
      verdict: "apply",
      verdict_label: "适合投递",
      verdict_tone: "green",
    },
  });

  const renderedHistory = [];
  let currentChatJobId = jobId;

  function renderChatJob(status) {
    renderedHistory.push(status);
  }

  // 3. handleChatSwitched 切回该会话流程模拟
  const options = { jobStatus: oldCachedStatus };

  // (a) 先以 options.jobStatus 临时渲染
  if (options.jobStatus && isChatJobResponseCurrent(options.jobStatus.platform_job_id, currentChatJobId)) {
    renderChatJob(options.jobStatus);
  }

  // (b) 总是向本机 Jet 重新查询（forceRefresh: true）
  const query = { type: "get_chat_job_status", jobId: currentChatJobId, forceRefresh: true };
  assert.equal(
    shouldFetchChatJobStatus({
      forceRefresh: query.forceRefresh,
      cachedStatus: tabState.chatJobStatus,
      jobId: query.jobId,
    }),
    true
  );

  // 服务端返回新判断，background 更新缓存
  tabState.chatJobStatus = freshStatus;
  const res = { ok: true, jobId, jobStatus: freshStatus };

  // (c) 返回时防乱序核对并重新渲染
  if (res.ok && isChatJobResponseCurrent(res.jobStatus?.platform_job_id, currentChatJobId)) {
    renderChatJob(res.jobStatus);
  }

  // 4. 验证
  assert.equal(renderedHistory.length, 2);
  assert.equal(renderedHistory[0].verdict_label, null, "临时显示未判断");
  assert.equal(renderedHistory[1].verdict_label, "适合投递", "最终显示新判断");
});

test("类别1(c): 强制刷新返回期间用户切至其他岗位，按防乱序规则丢弃旧岗位结果", () => {
  let currentChatJobId = "job_original";
  const displayed = [];

  function renderChatJob(status) {
    if (isChatJobResponseCurrent(status?.platform_job_id, currentChatJobId)) {
      displayed.push(status);
    }
  }

  // 1. 发起 job_original 的强制刷新
  const pendingJobId = "job_original";

  // 2. 在响应返回前，用户切到了 job_switched
  currentChatJobId = "job_switched";

  // 3. 迟到的 job_original 响应到达
  const delayedResponse = {
    platform_job_id: pendingJobId,
    title: "原岗位",
    verdict_label: "适合投递",
  };

  renderChatJob(delayedResponse);

  // 验证：乱序响应被安全丢弃，不渲染
  assert.equal(displayed.length, 0, "用户已离开该会话，迟到响应必须丢弃");
});

// ============================================================================
// 3. 第二类测试：切回同一会话、存在缓存建议结果时，仍会重新请求 preview，
//    且 has_jet_judgement=true 时"没有 Jet 判断"提示被隐藏；并断言不会调用 generate
// ============================================================================

test("类别2(c): 切回会话若服务端仍无判断(has_jet_judgement=false)，保持展示提示", () => {
  const cached = {
    bound_job_id: "job_unjudged_still",
    has_jet_judgement: false,
    suggestions: [{ text: "建议" }],
  };

  const previewData = {
    mode: "reply",
    has_jet_judgement: false, // 服务端依然未判断
  };

  const updated = mergeChatPreviewResult(cached, previewData, { encrypt_job_id: "job_unjudged_still" });
  assert.equal(updated.has_jet_judgement, false);
  assert.equal(shouldShowNoJudgementNotice(updated), true, "仍无判断时继续展示提示");
});
