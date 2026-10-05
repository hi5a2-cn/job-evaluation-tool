import test from "node:test";
import assert from "node:assert/strict";
import {
  CHAT_SWITCH_RETRY_DELAYS,
  decideChatSwitchRetry,
  decideChatFallbackSwitch,
  COPY_REJECTED_NOTICE,
  CHAT_AUTO_ACTIONS,
  runChatFallbackCheck,
} from "../src/chat-view.js";

// (a) 重试节奏与"读到新 ID 即广播、读到相同 ID 继续重试、重试用尽不广播"
test("T074(a): CHAT_SWITCH_RETRY_DELAYS 密集重试间隔与上限配置", () => {
  assert.equal(CHAT_SWITCH_RETRY_DELAYS.length, 28);
  assert.equal(CHAT_SWITCH_RETRY_DELAYS.every((d) => d === 200), true);
  const totalDelay = CHAT_SWITCH_RETRY_DELAYS.reduce((a, b) => a + b, 0);
  assert.ok(totalDelay >= 5600, "总重试等待时长不短于 5.6 秒");
});

test("T074(a): decideChatSwitchRetry - 读到新 ID 立即广播并停止重试", () => {
  // 1. 初次尝试读到新岗位 ID：立即广播，无需重试
  const res0 = decideChatSwitchRetry("job_accenture", "job_dongwan", 0);
  assert.equal(res0.shouldBroadcast, true);
  assert.equal(res0.jobId, "job_accenture");
  assert.equal(res0.shouldRetry, false);
  assert.equal(res0.nextDelayMs, null);

  // 2. 上次无记录（null）首次读到岗位：立即广播
  const resNullLast = decideChatSwitchRetry("job_dongwan", null, 0);
  assert.equal(resNullLast.shouldBroadcast, true);
  assert.equal(resNullLast.jobId, "job_dongwan");
  assert.equal(resNullLast.shouldRetry, false);

  // 3. 在后续重试阶段读到新 ID：立即广播并终止后续重试
  const resRetry = decideChatSwitchRetry("job_accenture", "job_dongwan", 2);
  assert.equal(resRetry.shouldBroadcast, true);
  assert.equal(resRetry.jobId, "job_accenture");
  assert.equal(resRetry.shouldRetry, false);
  assert.equal(resRetry.nextDelayMs, null);

  // 4. options 对象形式传参支持
  const resObj = decideChatSwitchRetry({
    currentJobId: "job_accenture",
    lastJobId: "job_dongwan",
    attemptIndex: 1,
  });
  assert.equal(resObj.shouldBroadcast, true);
  assert.equal(resObj.jobId, "job_accenture");
  assert.equal(resObj.shouldRetry, false);
});

test("T074(a): decideChatSwitchRetry - 读到相同 ID 继续按密集节奏重试", () => {
  // 读到与上次相同 ID（DOM 尚未更新）时，按 200ms 密集间隔重试
  const r0 = decideChatSwitchRetry("job_dongwan", "job_dongwan", 0);
  assert.equal(r0.shouldBroadcast, false);
  assert.equal(r0.shouldRetry, true);
  assert.equal(r0.nextDelayMs, 200);

  const r1 = decideChatSwitchRetry("job_dongwan", "job_dongwan", 1);
  assert.equal(r1.shouldBroadcast, false);
  assert.equal(r1.shouldRetry, true);
  assert.equal(r1.nextDelayMs, 200);

  const r2 = decideChatSwitchRetry("job_dongwan", "job_dongwan", 2);
  assert.equal(r2.shouldBroadcast, false);
  assert.equal(r2.shouldRetry, true);
  assert.equal(r2.nextDelayMs, 200);

  const r3 = decideChatSwitchRetry("job_dongwan", "job_dongwan", 3);
  assert.equal(r3.shouldBroadcast, false);
  assert.equal(r3.shouldRetry, true);
  assert.equal(r3.nextDelayMs, 200);

  // 未读到有效 ID（null 或空）时也继续重试
  const rNull = decideChatSwitchRetry(null, "job_dongwan", 0);
  assert.equal(rNull.shouldBroadcast, false);
  assert.equal(rNull.shouldRetry, true);
  assert.equal(rNull.nextDelayMs, 200);

  const rEmpty = decideChatSwitchRetry("   ", "job_dongwan", 1);
  assert.equal(rEmpty.shouldBroadcast, false);
  assert.equal(rEmpty.shouldRetry, true);
  assert.equal(rEmpty.nextDelayMs, 200);
});

test("T074(a): decideChatSwitchRetry - 重试用尽不广播", () => {
  // 达到最大重试次数（attemptIndex >= 28）后用尽，停止重试且不广播
  const rExhaustedSame = decideChatSwitchRetry("job_dongwan", "job_dongwan", 28);
  assert.equal(rExhaustedSame.shouldBroadcast, false);
  assert.equal(rExhaustedSame.shouldRetry, false);
  assert.equal(rExhaustedSame.nextDelayMs, null);
  assert.equal(rExhaustedSame.jobId, null);
  assert.equal(rExhaustedSame.isUnchanged, true);

  const rExhaustedBeyond = decideChatSwitchRetry("job_dongwan", "job_dongwan", 29);
  assert.equal(rExhaustedBeyond.shouldBroadcast, false);
  assert.equal(rExhaustedBeyond.shouldRetry, false);
  assert.equal(rExhaustedBeyond.nextDelayMs, null);

  const rExhaustedNull = decideChatSwitchRetry(null, "job_dongwan", 28);
  assert.equal(rExhaustedNull.shouldBroadcast, false);
  assert.equal(rExhaustedNull.shouldRetry, false);
  assert.equal(rExhaustedNull.nextDelayMs, null);
  assert.equal(rExhaustedNull.isReadFailed, true);
});

// (b) 兜底比较：显示中的岗位 ID 与当前 ID 不同 → 需要切换，相同 → 不动
test("T074(b): decideChatFallbackSwitch - 显示中岗位 ID 与当前 ID 比较", () => {
  // 1. 显示中的 ID 与当前读到的 ID 不同 → 需要切换
  const diffRes = decideChatFallbackSwitch("job_dongwan", "job_accenture");
  assert.equal(diffRes.shouldSwitch, true);
  assert.equal(diffRes.newJobId, "job_accenture");

  // 2. 显示中的 ID 与当前读到的 ID 相同 → 不动
  const sameRes = decideChatFallbackSwitch("job_dongwan", "job_dongwan");
  assert.equal(sameRes.shouldSwitch, false);
  assert.equal(sameRes.newJobId, null);

  // 3. 边界情况：未显示岗位结果（null）或读不到当前 ID（null）→ 不触发兜底切换
  assert.equal(decideChatFallbackSwitch(null, "job_accenture").shouldSwitch, false);
  assert.equal(decideChatFallbackSwitch("job_dongwan", null).shouldSwitch, false);
  assert.equal(decideChatFallbackSwitch(null, null).shouldSwitch, false);
});

// (c) 复制被拒绝时的提示文字在开、关两种情况下都是"已切换到新的聊天，请重新生成"
test("T074(c): 开关开、关两种情况下提示文字均为 FR-005 规范文案", () => {
  const expectedNotice = "已切换到新的聊天，请重新生成";
  assert.equal(COPY_REJECTED_NOTICE, expectedNotice);
});

// (d) runChatFallbackCheck 兜底检查纯函数流程测试
test("T074(d): runChatFallbackCheck - (a) displayedJobId 为空时连续调用 5 次不触发 onSwitch 且不调用 readCurrentJobId", async () => {
  let switchCount = 0;
  let readCount = 0;

  const onSwitch = () => {
    switchCount++;
  };
  const readCurrentJobId = async () => {
    readCount++;
    return "job_active";
  };

  for (let i = 0; i < 5; i++) {
    await runChatFallbackCheck({
      isChat: true,
      displayedJobId: null,
      readCurrentJobId,
      onSwitch,
    });
  }

  assert.equal(switchCount, 0, "displayedJobId 为空时 onSwitch 必须调用 0 次");
  assert.equal(readCount, 0, "displayedJobId 为空时 readCurrentJobId 可以不被调用");
});

test("T074(d): runChatFallbackCheck - (b) 侧边栏已有该聊天结果且岗位相同时连续 5 次不触发 onSwitch", async () => {
  let switchCount = 0;
  let readCount = 0;

  const onSwitch = () => {
    switchCount++;
  };
  const readCurrentJobId = async () => {
    readCount++;
    return "job_same";
  };

  for (let i = 0; i < 5; i++) {
    await runChatFallbackCheck({
      isChat: true,
      displayedJobId: "job_same",
      readCurrentJobId,
      onSwitch,
    });
  }

  assert.equal(switchCount, 0, "岗位相同时 onSwitch 必须调用 0 次");
  assert.equal(readCount, 5, "每次检查都调用 readCurrentJobId");
});

test("T074(d): runChatFallbackCheck - (c) 显示中的岗位与当前岗位不同时恰好触发 1 次 onSwitch 且参数为当前岗位 ID", async () => {
  let switchCount = 0;
  let switchedJobId = null;

  const onSwitch = (newJobId) => {
    switchCount++;
    switchedJobId = newJobId;
  };
  const readCurrentJobId = async () => {
    return "job_current";
  };

  await runChatFallbackCheck({
    isChat: true,
    displayedJobId: "job_displayed",
    readCurrentJobId,
    onSwitch,
  });

  assert.equal(switchCount, 1, "显示中的岗位与当前岗位不同时 onSwitch 恰好调用 1 次");
  assert.equal(switchedJobId, "job_current", "onSwitch 参数必须为当前岗位 ID");
});

test("T074(d): runChatFallbackCheck - (d) readCurrentJobId 返回空或抛错时 onSwitch 调用 0 次", async () => {
  let switchCount = 0;
  const onSwitch = () => {
    switchCount++;
  };

  // 1. readCurrentJobId 返回 null
  await runChatFallbackCheck({
    isChat: true,
    displayedJobId: "job_displayed",
    readCurrentJobId: async () => null,
    onSwitch,
  });
  assert.equal(switchCount, 0, "readCurrentJobId 返回 null 时不得触发切换");

  // 2. readCurrentJobId 返回空字符串
  await runChatFallbackCheck({
    isChat: true,
    displayedJobId: "job_displayed",
    readCurrentJobId: async () => "",
    onSwitch,
  });
  assert.equal(switchCount, 0, "readCurrentJobId 返回空字符串时不得触发切换");

  // 3. readCurrentJobId 抛错
  await assert.doesNotReject(async () => {
    await runChatFallbackCheck({
      isChat: true,
      displayedJobId: "job_displayed",
      readCurrentJobId: async () => {
        throw new Error("read failed");
      },
      onSwitch,
    });
  });
  assert.equal(switchCount, 0, "readCurrentJobId 抛错时不得触发切换");
});
