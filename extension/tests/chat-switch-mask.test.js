import test from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_SWITCH_RETRY_DELAYS,
  CHAT_MASK_TEXTS,
  CHAT_MASK_STATUS,
  createInitialMaskState,
  decideChatMaskOnChanging,
  decideChatMaskOnSwitched,
  decideChatMaskOnUnchanged,
  decideChatMaskOnFailed,
  decideChatJobStatusUpdate,
  reduceChatMaskState,
  decideChatSwitchRetry,
  recordMaskButtonStates,
  decideUnmaskedButtonStates,
} from "../src/chat-view.js";

// 1. 收到 changing 立即遮盖且复制不可用
test("E2-01: 收到 changing 立即遮盖且复制不可用", () => {
  const initialState = createInitialMaskState({
    currentJobId: "job_old",
    currentBoundJobId: "job_old",
    currentResult: {
      mode: "opening",
      suggestions: [{ text: "话术内容", version: 1 }],
      attribution: "这是给 某农业科技公司 · 技术助理 的建议",
    },
  });

  assert.equal(initialState.isMasked, false);
  assert.equal(initialState.copyDisabled, false);

  // 纯函数判定
  const changingState = decideChatMaskOnChanging(initialState);
  assert.equal(changingState.status, CHAT_MASK_STATUS.CHANGING);
  assert.equal(changingState.isMasked, true, "changing 状态必须立即遮盖");
  assert.equal(changingState.copyDisabled, true, "changing 状态复制按钮必须不可用");
  assert.equal(changingState.maskText, CHAT_MASK_TEXTS.IDENTIFYING);
  assert.equal(changingState.maskText, "正在识别当前聊天…");
  assert.equal(changingState.jobCardState, "identifying");
  assert.equal(changingState.jobCardText, "正在识别当前聊天…");

  // 原结果被妥善暂存
  assert.deepEqual(changingState.preservedResult, initialState.currentResult);
  assert.equal(changingState.preservedJobId, "job_old");
  assert.equal(changingState.preservedBoundJobId, "job_old");

  // reducer 纯函数处理
  const reducedState = reduceChatMaskState(initialState, { type: "chat_top_changing" });
  assert.equal(reducedState.isMasked, true);
  assert.equal(reducedState.copyDisabled, true);
  assert.equal(reducedState.maskText, "正在识别当前聊天…");
});

// 2. 确认新 ID 后解除遮盖
test("E2-02: 确认新 ID 后解除遮盖", () => {
  const changingState = decideChatMaskOnChanging(
    createInitialMaskState({
      currentJobId: "job_old",
      currentBoundJobId: "job_old",
      currentResult: { suggestions: [{ text: "旧话术" }] },
    })
  );
  assert.equal(changingState.isMasked, true);
  assert.equal(changingState.copyDisabled, true);

  // 纯函数判定：确认新 ID 后解除
  const switchedState = decideChatMaskOnSwitched(changingState, { jobId: "job_new" });
  assert.equal(switchedState.status, CHAT_MASK_STATUS.SWITCHED);
  assert.equal(switchedState.isMasked, false, "确认新 ID 后必须解除遮盖");
  assert.equal(switchedState.copyDisabled, false, "解除遮盖后复制按钮恢复可用");
  assert.equal(switchedState.maskText, null);
  assert.equal(switchedState.currentJobId, "job_new");
  assert.equal(switchedState.currentBoundJobId, null);
  assert.equal(switchedState.currentResult, null);
  assert.equal(switchedState.preservedResult, null);
  assert.equal(switchedState.jobCardState, "querying");
  assert.equal(switchedState.jobCardText, CHAT_MASK_TEXTS.QUERYING_JOB);
  assert.equal(switchedState.jobCardText, "正在查询岗位库…");

  // reducer 纯函数处理
  const reducedSwitched = reduceChatMaskState(changingState, {
    type: "chat_switched",
    jobId: "job_new",
  });
  assert.equal(reducedSwitched.isMasked, false);
  assert.equal(reducedSwitched.copyDisabled, false);
  assert.equal(reducedSwitched.currentJobId, "job_new");
  assert.equal(reducedSwitched.jobCardText, "正在查询岗位库…");
});

// 3. unchanged 恢复原结果
test("E2-03: unchanged 恢复原结果（不重新生成、不丢失原结果）", () => {
  const originalResult = {
    mode: "reply",
    suggestions: [{ text: "保留的原话术", version: 1 }],
    attribution: "这是给 某农业科技公司 · 技术助理 的建议",
  };
  const initialState = createInitialMaskState({
    currentJobId: "job_original",
    currentBoundJobId: "job_original",
    currentResult: originalResult,
  });

  const changingState = decideChatMaskOnChanging(initialState);
  assert.equal(changingState.isMasked, true);

  // 纯函数判定：unchanged 误报恢复
  const unchangedState = decideChatMaskOnUnchanged(changingState);
  assert.equal(unchangedState.status, CHAT_MASK_STATUS.UNCHANGED);
  assert.equal(unchangedState.isMasked, false, "误报恢复时解除遮盖");
  assert.equal(unchangedState.copyDisabled, false, "恢复可用");
  assert.equal(unchangedState.shouldRestoreResult, true);
  assert.deepEqual(unchangedState.currentResult, originalResult, "不丢失原结果");
  assert.equal(unchangedState.currentJobId, "job_original");
  assert.equal(unchangedState.currentBoundJobId, "job_original");
  assert.equal(unchangedState.preservedResult, null);

  // reducer 纯函数处理
  const reducedUnchanged = reduceChatMaskState(changingState, {
    type: "chat_switch_unchanged",
    jobId: "job_original",
  });
  assert.equal(reducedUnchanged.isMasked, false);
  assert.equal(reducedUnchanged.copyDisabled, false);
  assert.deepEqual(reducedUnchanged.currentResult, originalResult);
});

// 4. 读不到 ID 保持遮盖并显示失败文案
test("E2-04: 读不到 ID 保持遮盖并显示失败文案（不得恢复旧聊天内容）", () => {
  const changingState = decideChatMaskOnChanging(
    createInitialMaskState({
      currentJobId: "job_old",
      currentBoundJobId: "job_old",
      currentResult: { suggestions: [{ text: "不得泄露的旧话术" }] },
    })
  );

  // 纯函数判定：failed 保持遮盖
  const failedState = decideChatMaskOnFailed(changingState);
  assert.equal(failedState.status, CHAT_MASK_STATUS.FAILED);
  assert.equal(failedState.isMasked, true, "始终读不到 ID 时保持遮盖");
  assert.equal(failedState.copyDisabled, true, "复制按钮保持不可用");
  assert.equal(failedState.maskText, CHAT_MASK_TEXTS.FAILED);
  assert.equal(failedState.maskText, "未能识别当前聊天的岗位，请稍后再试");
  assert.equal(failedState.currentResult, null, "失败状态下绝不得恢复旧聊天内容");
  assert.equal(failedState.currentBoundJobId, null);
  assert.equal(failedState.preservedResult, null);
  assert.equal(failedState.jobCardState, "failed");
  assert.equal(failedState.jobCardText, "未能识别当前聊天的岗位，请稍后再试");

  // reducer 纯函数处理
  const reducedFailed = reduceChatMaskState(changingState, {
    type: "chat_switch_failed",
  });
  assert.equal(reducedFailed.isMasked, true);
  assert.equal(reducedFailed.copyDisabled, true);
  assert.equal(reducedFailed.maskText, "未能识别当前聊天的岗位，请稍后再试");
  assert.equal(reducedFailed.currentResult, null);
});

// 5. 过期的 chat_job_status_updated（jobId 不一致）被忽略
test("E2-05: 过期的 chat_job_status_updated（jobId 不一致）被忽略", () => {
  // 1. jobId 一致：应用更新
  const matchRes = decideChatJobStatusUpdate({
    currentChatJobId: "job_active_123",
    updateJobId: "job_active_123",
    jobStatus: { platform_job_id: "job_active_123", in_library: true, title: "技术助理" },
  });
  assert.equal(matchRes.shouldApply, true);
  assert.equal(matchRes.reason, null);
  assert.equal(matchRes.jobStatus.platform_job_id, "job_active_123");

  // 2. jobId 不一致（旧聊天返回的延迟数据）：被忽略
  const mismatchRes = decideChatJobStatusUpdate({
    currentChatJobId: "job_current_456",
    updateJobId: "job_old_123",
    jobStatus: { platform_job_id: "job_old_123", in_library: true, title: "旧岗位" },
  });
  assert.equal(mismatchRes.shouldApply, false, "jobId 不一致时必须忽略");
  assert.equal(mismatchRes.reason, "job_id_mismatch");
  assert.equal(mismatchRes.jobStatus, null);

  // 3. updateJobId 为空或 null：被忽略
  const nullUpdateRes = decideChatJobStatusUpdate({
    currentChatJobId: "job_current_456",
    updateJobId: null,
    jobStatus: { platform_job_id: "job_unknown" },
  });
  assert.equal(nullUpdateRes.shouldApply, false);

  // 4. currentChatJobId 为空或 null：被忽略
  const nullCurrentRes = decideChatJobStatusUpdate({
    currentChatJobId: null,
    updateJobId: "job_old_123",
    jobStatus: { platform_job_id: "job_old_123" },
  });
  assert.equal(nullCurrentRes.shouldApply, false);
});

// 6. 新重试间隔下累计时长上限不小于 5.6 秒且每次仍要求不同的有效 ID
test("E2-06: 新重试间隔下累计时长上限不小于 5.6 秒且每次仍要求不同的有效 ID", () => {
  // 1. 数组长度与单次间隔
  assert.equal(CHAT_SWITCH_RETRY_DELAYS.length, 28);
  for (let i = 0; i < CHAT_SWITCH_RETRY_DELAYS.length; i++) {
    assert.equal(
      CHAT_SWITCH_RETRY_DELAYS[i],
      200,
      `第 ${i} 次重试间隔必须严格为 200ms`
    );
  }

  // 2. 累计时长上限不小于 5.6 秒
  const totalDuration = CHAT_SWITCH_RETRY_DELAYS.reduce((sum, delay) => sum + delay, 0);
  assert.ok(
    totalDuration >= 5600,
    `累计等待时长 (${totalDuration}ms) 必须不小于 5.6 秒 (5600ms)`
  );

  // 3. 每次仍必须读到"与上次不同的有效 ID"才广播
  for (let attempt = 0; attempt < 28; attempt++) {
    // (a) 读到相同 ID：严禁广播，必须继续重试
    const sameRes = decideChatSwitchRetry("job_same", "job_same", attempt);
    assert.equal(sameRes.shouldBroadcast, false, `Attempt ${attempt}: 相同 ID 绝不广播`);
    assert.equal(sameRes.shouldRetry, true, `Attempt ${attempt}: 相同 ID 继续重试`);
    assert.equal(sameRes.nextDelayMs, 200);

    // (b) 读到 null 或空 ID：严禁广播，必须继续重试
    const nullRes = decideChatSwitchRetry(null, "job_same", attempt);
    assert.equal(nullRes.shouldBroadcast, false, `Attempt ${attempt}: null 绝不广播`);
    assert.equal(nullRes.shouldRetry, true);
    assert.equal(nullRes.nextDelayMs, 200);

    const emptyRes = decideChatSwitchRetry("   ", "job_same", attempt);
    assert.equal(emptyRes.shouldBroadcast, false, `Attempt ${attempt}: 空白 ID 绝不广播`);
    assert.equal(emptyRes.shouldRetry, true);
    assert.equal(emptyRes.nextDelayMs, 200);

    // (c) 读到不同的有效 ID：立即广播，停止重试
    const diffRes = decideChatSwitchRetry("job_new_confirmed", "job_same", attempt);
    assert.equal(diffRes.shouldBroadcast, true, `Attempt ${attempt}: 新有效 ID 必须立即广播`);
    assert.equal(diffRes.jobId, "job_new_confirmed");
    assert.equal(diffRes.shouldRetry, false);
    assert.equal(diffRes.nextDelayMs, null);
  }

  // 4. 重试用尽时的判定
  const exhaustedSame = decideChatSwitchRetry("job_same", "job_same", 28);
  assert.equal(exhaustedSame.shouldBroadcast, false);
  assert.equal(exhaustedSame.shouldRetry, false);
  assert.equal(exhaustedSame.isUnchanged, true);

  const exhaustedNull = decideChatSwitchRetry(null, "job_same", 28);
  assert.equal(exhaustedNull.shouldBroadcast, false);
  assert.equal(exhaustedNull.shouldRetry, false);
  assert.equal(exhaustedNull.isReadFailed, true);
});

// 7. 生成中遇误报解除 → 生成按钮仍禁用
test("E2-07: 生成中遇误报解除 → 生成按钮仍禁用", () => {
  // 遮盖前处于生成中状态（生成按钮被禁用）
  const originalStates = {
    generateDisabled: true,
    switchModeDisabled: true,
    copyDisabled: false,
  };

  // 生成仍在进行中（isGenerating = true），误报（chat_switch_unchanged）解除遮盖
  const unmaskedStates = decideUnmaskedButtonStates(originalStates, true);

  // 断言：生成仍在进行，生成按钮必须保持禁用，防重复点击与额度重复占用
  assert.equal(
    unmaskedStates.generateDisabled,
    true,
    "生成中遇误报解除遮盖，生成按钮必须保持禁用"
  );
  assert.equal(
    unmaskedStates.switchModeDisabled,
    true,
    "生成中遇误报解除遮盖，切换模式按钮亦必须保持禁用"
  );

  // options 对象传参形式同样保证结果一致
  const unmaskedWithOptions = decideUnmaskedButtonStates({
    originalStates,
    isGenerating: true,
  });
  assert.equal(unmaskedWithOptions.generateDisabled, true);
});

// 8. 遮盖期间生成结束 → 解除后可用
test("E2-08: 遮盖期间生成结束 → 解除后可用", () => {
  // 遮盖前处于生成中状态（生成按钮因生成中被禁用）
  const originalStates = {
    generateDisabled: true,
    switchModeDisabled: true,
    copyDisabled: false,
  };

  // 遮盖期间生成请求结束（isGenerating = false），解除遮盖
  const unmaskedStates = decideUnmaskedButtonStates(originalStates, false);

  // 断言：以当时生成是否仍在进行为准，生成已结束则按钮恢复可用
  assert.equal(
    unmaskedStates.generateDisabled,
    false,
    "遮盖期间生成已结束，解除遮盖后生成按钮必须恢复可用"
  );
  assert.equal(
    unmaskedStates.switchModeDisabled,
    false,
    "遮盖期间生成已结束，解除遮盖后切换模式按钮必须恢复可用"
  );

  // options 对象传参形式同样保证可用
  const unmaskedWithOptions = decideUnmaskedButtonStates({
    originalStates,
    isGenerating: false,
  });
  assert.equal(unmaskedWithOptions.generateDisabled, false);
  assert.equal(unmaskedWithOptions.switchModeDisabled, false);
});

// 9. 遮盖前本就禁用的复制按钮解除后仍禁用
test("E2-09: 遮盖前本就禁用的复制按钮解除后仍禁用", () => {
  // 遮盖前已有多个复制按钮，其中第 0 个可用，第 1、2 个已被禁用
  const originalStates = {
    generateDisabled: false,
    switchModeDisabled: false,
    copyDisabled: true,
    copyDisabledList: [false, true, true],
  };

  // 解除遮盖（生成不在进行中）
  const unmaskedStates = decideUnmaskedButtonStates(originalStates, false);

  // 断言：复制按钮恢复遮盖前的状态，本就禁用的复制按钮解除后仍禁用，原可用的恢复可用
  assert.deepEqual(
    unmaskedStates.copyDisabledList,
    [false, true, true],
    "复制按钮解除后必须严格恢复遮盖前的各个状态"
  );
  assert.equal(unmaskedStates.copyDisabledList[0], false, "原本可用的复制按钮恢复可用");
  assert.equal(unmaskedStates.copyDisabledList[1], true, "原本禁用的复制按钮解除后仍保持禁用");
  assert.equal(unmaskedStates.copyDisabledList[2], true, "原本禁用的复制按钮解除后仍保持禁用");

  // 单个复制按钮判定测试
  const singleDisabled = decideUnmaskedButtonStates({
    copyDisabled: true,
    isGenerating: false,
  });
  assert.equal(singleDisabled.copyDisabled, true, "单个禁用的复制按钮解除后仍禁用");

  const singleEnabled = decideUnmaskedButtonStates({
    copyDisabled: false,
    isGenerating: false,
  });
  assert.equal(singleEnabled.copyDisabled, false, "单个可用的复制按钮解除后可用");
});

// 10. 重复收到 changing（已在遮盖中）不覆盖第一次记录的原状态
test("E2-10: 重复收到 changing（已在遮盖中）不覆盖第一次记录的原状态", () => {
  // 第一次进入遮盖前（从未遮盖进入遮盖的那一刻）：记录原始状态
  // 比如：生成中（generateDisabled = true），第 0 个复制按钮可用，第 1 个禁用
  const firstOriginalStates = {
    generateDisabled: true,
    switchModeDisabled: true,
    copyDisabled: true,
    copyDisabledList: [false, true],
  };

  const recordedFirst = recordMaskButtonStates(null, firstOriginalStates, false);
  assert.equal(recordedFirst.generateDisabled, true);
  assert.equal(recordedFirst.switchModeDisabled, true);
  assert.deepEqual(recordedFirst.copyDisabledList, [false, true]);

  // 进入遮盖后，DOM 中所有按钮均被遮盖逻辑设置为 disabled = true
  const domStatesWhileMasked = {
    generateDisabled: true,
    switchModeDisabled: true,
    copyDisabled: true,
    copyDisabledList: [true, true],
  };

  // 重复收到 changing 事件（当前已在遮盖中，isAlreadyMasked = true）
  const recordedSecond = recordMaskButtonStates(
    recordedFirst,
    domStatesWhileMasked,
    true
  );

  // 断言：严禁覆盖第一次记录的原状态
  assert.deepEqual(
    recordedSecond,
    recordedFirst,
    "重复收到 changing 绝不覆盖第一次记录的原状态"
  );
  assert.equal(
    recordedSecond.copyDisabledList[0],
    false,
    "第一次记录的 copy button 0 原状态为可用(false)，重复 changing 时不被覆盖为 true"
  );
  assert.equal(recordedSecond.copyDisabledList[1], true);

  // 第三次收到 changing（即使传入全 false 的异常 DOM 状态）仍不被覆盖
  const recordedThird = recordMaskButtonStates(
    recordedSecond,
    { generateDisabled: false, switchModeDisabled: false, copyDisabledList: [false, false] },
    true
  );
  assert.deepEqual(
    recordedThird,
    recordedFirst,
    "多次重复收到 changing 均保持第一次记录的原状态"
  );
  assert.equal(recordedThird.generateDisabled, true);
});
