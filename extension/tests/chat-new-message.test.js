import test from "node:test";
import assert from "node:assert/strict";
import {
  isLastMessageFromHr,
  decideNewMessageAction,
  NEW_MESSAGE_HINT,
  recordLatestChatCacheKey,
  setLatestChatCacheKey,
  getLatestChatCacheKey,
} from "../src/chat-view.js";

test("FR-074: isLastMessageFromHr - 最后一条消息发送方判定（HR 最后 / 我最后 / 空 / 兼容字段）", () => {
  // 1. 空数组及无效输入返回 false
  assert.equal(isLastMessageFromHr([]), false);
  assert.equal(isLastMessageFromHr(null), false);
  assert.equal(isLastMessageFromHr(undefined), false);
  assert.equal(isLastMessageFromHr("not an array"), false);

  // 2. HR 最后说话（is_self: false / isSelf: false / sender: "HR" / 缺省默认）
  assert.equal(isLastMessageFromHr([{ text: "你好", is_self: false }]), true);
  assert.equal(isLastMessageFromHr([{ text: "你好", isSelf: false }]), true);
  assert.equal(isLastMessageFromHr([{ text: "你好", sender: "HR" }]), true);
  assert.equal(isLastMessageFromHr([{ text: "你好", sender: "HR", is_self: false }]), true);
  assert.equal(isLastMessageFromHr([{ text: "你好", sender: "HR", isSelf: false }]), true);
  assert.equal(isLastMessageFromHr([{ text: "方便发份简历吗？" }]), true);

  // 3. 我最后说话（is_self: true / isSelf: true / sender: "我"）
  assert.equal(isLastMessageFromHr([{ text: "您好，贵公司还在招吗？", is_self: true }]), false);
  assert.equal(isLastMessageFromHr([{ text: "您好，贵公司还在招吗？", isSelf: true }]), false);
  assert.equal(isLastMessageFromHr([{ text: "您好，贵公司还在招吗？", sender: "我" }]), false);
  assert.equal(isLastMessageFromHr([{ text: "您好", sender: "我", is_self: true }]), false);
  assert.equal(isLastMessageFromHr([{ text: "您好", sender: "我", is_self: false }]), false);
  // is_self 为真即"我"，即使 sender 标为 HR 也归为"我"
  assert.equal(isLastMessageFromHr([{ text: "您好", sender: "HR", is_self: true }]), false);
  assert.equal(isLastMessageFromHr([{ text: "您好", sender: "HR", isSelf: true }]), false);

  // 4. 多条消息顺序检查：只看最后一条
  const conversation1 = [
    { sender: "我", text: "招呼语", is_self: true },
    { sender: "HR", text: "你好，目前岗位还在招的", is_self: false },
  ];
  assert.equal(isLastMessageFromHr(conversation1), true);

  const conversation2 = [
    { sender: "我", text: "招呼语", is_self: true },
    { sender: "HR", text: "你好，目前岗位还在招的", is_self: false },
    { sender: "我", text: "请问该岗位主要负责海外哪些国家？", is_self: true },
  ];
  assert.equal(isLastMessageFromHr(conversation2), false);

  const conversation3 = [
    { isSelf: true, text: "招呼语" },
    { isSelf: false, text: "方便发一份附件简历吗？" },
  ];
  assert.equal(isLastMessageFromHr(conversation3), true);

  const conversation4 = [
    { isSelf: false, text: "方便发一份附件简历吗？" },
    { isSelf: true, text: "好的，稍后发您" },
  ];
  assert.equal(isLastMessageFromHr(conversation4), false);

  // 5. 动作类卡片标记（FR-068 / R8）：我方发送的卡片标记归为"我"，HR 请求卡片归为"HR"
  const conversationActionCard1 = [
    { sender: "HR", text: "方便发一份附件简历吗？", is_self: false },
    { sender: "我", text: "我：[已发送附件简历]", is_self: true },
  ];
  assert.equal(isLastMessageFromHr(conversationActionCard1), false);

  const conversationActionCard2 = [
    { sender: "我", text: "我：[已发送附件简历]", is_self: true },
    { sender: "HR", text: "好的，收到了", is_self: false },
  ];
  assert.equal(isLastMessageFromHr(conversationActionCard2), true);

  const conversationReqCard = [
    { sender: "我", text: "您好", is_self: true },
    { sender: "HR", text: "我想要一份您的附件简历，您是否同意", is_self: false, is_request_card: true },
  ];
  assert.equal(isLastMessageFromHr(conversationReqCard), true);
});

test("FR-074: decideNewMessageAction - 全部组合覆盖（autoGenerate × lastIsHr × quotaExhaustedToday）", () => {
  // 1. 开关开启、最新是 HR、今日未用完 -> "generate"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: true,
      lastIsHr: true,
      quotaExhaustedToday: false,
    }),
    "generate"
  );

  // 2. 开关开启、最新是 HR、今日已用完 -> "quota_exhausted"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: true,
      lastIsHr: true,
      quotaExhaustedToday: true,
    }),
    "quota_exhausted"
  );

  // 3. 开关开启、最新是我、今日未用完 -> "update_status"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: true,
      lastIsHr: false,
      quotaExhaustedToday: false,
    }),
    "update_status"
  );

  // 4. 开关开启、最新是我、今日已用完 -> "update_status"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: true,
      lastIsHr: false,
      quotaExhaustedToday: true,
    }),
    "update_status"
  );

  // 5. 开关关闭、最新是 HR、今日未用完 -> "update_status_with_hint"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: false,
      lastIsHr: true,
      quotaExhaustedToday: false,
    }),
    "update_status_with_hint"
  );

  // 6. 开关关闭、最新是 HR、今日已用完 -> "update_status_with_hint"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: false,
      lastIsHr: true,
      quotaExhaustedToday: true,
    }),
    "update_status_with_hint"
  );

  // 7. 开关关闭、最新是我、今日未用完 -> "update_status_with_hint"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: false,
      lastIsHr: false,
      quotaExhaustedToday: false,
    }),
    "update_status_with_hint"
  );

  // 8. 开关关闭、最新是我、今日已用完 -> "update_status_with_hint"
  assert.equal(
    decideNewMessageAction({
      autoGenerate: false,
      lastIsHr: false,
      quotaExhaustedToday: true,
    }),
    "update_status_with_hint"
  );

  // 9. 缺省值容错
  // 缺省 autoGenerate 默认为 true，quotaExhaustedToday 默认为 false
  assert.equal(decideNewMessageAction({ lastIsHr: true }), "generate");
  assert.equal(decideNewMessageAction({ lastIsHr: false }), "update_status");
  assert.equal(decideNewMessageAction({ autoGenerate: false }), "update_status_with_hint");
  assert.equal(decideNewMessageAction({}), "update_status");
  assert.equal(decideNewMessageAction(), "update_status");
});

test("FR-074: NEW_MESSAGE_HINT - 新消息提示文案验证", () => {
  assert.equal(NEW_MESSAGE_HINT, "有新消息 · 点「生成沟通建议」重新生成");
});

test("FR-074: B4 岗位 ID 到最新缓存键映射工具（同一岗位多次写入取最新、不同岗位互不影响）", () => {
  const cacheKeyMap = new Map();

  // 1. 同一岗位写入多次：始终返回最新写入的那一条
  recordLatestChatCacheKey(cacheKeyMap, "job_alpha", "job_alpha|fingerprint_v1");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v1");

  recordLatestChatCacheKey(cacheKeyMap, "job_alpha", "job_alpha|fingerprint_v2");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v2");

  setLatestChatCacheKey(cacheKeyMap, "job_alpha", "job_alpha|fingerprint_v3");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v3");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v3");

  // 2. 不同岗位互不影响
  recordLatestChatCacheKey(cacheKeyMap, "job_beta", "job_beta|fingerprint_b1");
  recordLatestChatCacheKey(cacheKeyMap, "job_gamma", "job_gamma|fingerprint_g1");

  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v3");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_beta"), "job_beta|fingerprint_b1");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_gamma"), "job_gamma|fingerprint_g1");

  // 更新 job_beta，不影响 job_alpha 与 job_gamma
  recordLatestChatCacheKey(cacheKeyMap, "job_beta", "job_beta|fingerprint_b2");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_alpha"), "job_alpha|fingerprint_v3");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_beta"), "job_beta|fingerprint_b2");
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "job_gamma"), "job_gamma|fingerprint_g1");

  // 3. Map set 顺序保持：重复键写入时通过 delete + set 保证该键移至 Map 最新末尾
  const orderMap = new Map();
  recordLatestChatCacheKey(orderMap, "job_1", "key_1_1");
  recordLatestChatCacheKey(orderMap, "job_2", "key_2_1");
  // 重新写入 job_1：其键应被移到最新末尾
  recordLatestChatCacheKey(orderMap, "job_1", "key_1_2");
  const keys = Array.from(orderMap.keys());
  assert.deepEqual(keys, ["job_2", "job_1"]);
  assert.equal(getLatestChatCacheKey(orderMap, "job_1"), "key_1_2");

  // 4. 边界与无效输入处理
  assert.equal(getLatestChatCacheKey(cacheKeyMap, "non_existent"), null);
  assert.equal(getLatestChatCacheKey(null, "job_alpha"), null);
  assert.equal(getLatestChatCacheKey(cacheKeyMap, null), null);
  assert.equal(getLatestChatCacheKey(cacheKeyMap, ""), null);

  // 5. 普通 Object 字典映射同样支持
  const plainObj = {};
  setLatestChatCacheKey(plainObj, "job_obj", "key_obj_1");
  assert.equal(getLatestChatCacheKey(plainObj, "job_obj"), "key_obj_1");
  setLatestChatCacheKey(plainObj, "job_obj", "key_obj_2");
  assert.equal(getLatestChatCacheKey(plainObj, "job_obj"), "key_obj_2");
});
