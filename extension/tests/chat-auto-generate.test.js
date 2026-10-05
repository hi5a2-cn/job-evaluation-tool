import test from "node:test";
import assert from "node:assert/strict";
import {
  CHAT_AUTO_ACTIONS,
  computeMessageFingerprint,
  computeChatCacheKey,
  getLocalDateString,
  isQuotaExhaustedToday,
  decideChatAutoAction,
  formatChatError,
} from "../src/chat-view.js";

test("T070: 缓存键计算 - 组合岗位ID与最后一条消息指纹 (FR-066, FR-036)", () => {
  const msg1 = {
    sender: "HR",
    is_self: false,
    body_type: 1,
    text: "您好，方便发一份简历吗？",
    time: 1727337600000,
  };
  const fp1 = computeMessageFingerprint(msg1);
  assert.equal(fp1, "HR|false|1|1727337600000|您好，方便发一份简历吗？");

  // 键 = 岗位 ID + 最后一条消息的指纹
  const key1 = computeChatCacheKey("job_abc", [msg1]);
  assert.equal(key1, "job_abc|HR|false|1|1727337600000|您好，方便发一份简历吗？");

  // 直接传入单条消息对象
  const key1Direct = computeChatCacheKey("job_abc", msg1);
  assert.equal(key1Direct, key1);

  // 消息为空或 null 时指纹与键计算
  assert.equal(computeMessageFingerprint(null), "no_message");
  assert.equal(computeMessageFingerprint(undefined), "no_message");
  assert.equal(computeChatCacheKey("job_abc", []), "job_abc|no_message");
  assert.equal(computeChatCacheKey("job_abc", null), "job_abc|no_message");

  // 字段大小写兼容 (isSelf, bodyType)
  const msgCamel = {
    sender: "我",
    isSelf: true,
    bodyType: 1,
    text: "好的，这就发您",
    time: 1727337610000,
  };
  const fpCamel = computeMessageFingerprint(msgCamel);
  assert.equal(fpCamel, "我|true|1|1727337610000|好的，这就发您");
});

test("T070: 同一聊天无新消息 → 显示缓存且不生成 (FR-066, SC-023)", () => {
  const jobId = "job_1001";
  const messages = [
    { sender: "我", is_self: true, body_type: 1, text: "您好", time: 100 },
    { sender: "HR", is_self: false, body_type: 1, text: "请问什么时候能到岗？", time: 200 },
  ];

  const cacheKey = computeChatCacheKey(jobId, messages);

  // 模拟侧边栏内存缓存 Map
  const chatResultsCache = new Map();
  const cachedGenerationResult = {
    mode: "reply",
    suggestions: [{ version: 1, text: "随时可以到岗" }],
    questions: ["请问有试用期吗？"],
  };
  chatResultsCache.set(cacheKey, cachedGenerationResult);

  // 再次切回该聊天，消息未变化
  const currentKey = computeChatCacheKey(jobId, messages);
  assert.equal(currentKey, cacheKey);

  const hasCachedResult = chatResultsCache.has(currentKey);
  assert.equal(hasCachedResult, true);

  const decision = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: true,
    hasCachedResult: true,
    quotaExhaustedToday: false,
  });

  assert.equal(decision.action, CHAT_AUTO_ACTIONS.SHOW_CACHE);
  assert.equal(decision.shouldGenerate, false);
  assert.deepEqual(chatResultsCache.get(currentKey), cachedGenerationResult);
});

test("T070: 有新消息（最后一条不同）→ 自动生成 (FR-065, SC-021)", () => {
  const jobId = "job_1001";
  const messagesBefore = [
    { sender: "我", is_self: true, body_type: 1, text: "您好", time: 100 },
    { sender: "HR", is_self: false, body_type: 1, text: "请问什么时候能到岗？", time: 200 },
  ];

  const oldKey = computeChatCacheKey(jobId, messagesBefore);

  const chatResultsCache = new Map();
  chatResultsCache.set(oldKey, { suggestions: ["旧建议"] });

  // 产生新消息（最后一条发生变化）
  const messagesAfter = [
    ...messagesBefore,
    { sender: "我", is_self: true, body_type: 1, text: "下周一可以到岗", time: 300 },
  ];

  const newKey = computeChatCacheKey(jobId, messagesAfter);
  assert.notEqual(newKey, oldKey);

  const hasCachedResult = chatResultsCache.has(newKey);
  assert.equal(hasCachedResult, false);

  const decision = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: true,
    hasCachedResult: false,
    quotaExhaustedToday: false,
  });

  assert.equal(decision.action, CHAT_AUTO_ACTIONS.AUTO_GENERATE);
  assert.equal(decision.shouldGenerate, true);
});

test("T070: autoGenerate=false → 等待手动 (FR-020, FR-067, SC-024)", () => {
  // 当设置开关 autoGenerate 为 false 时，切换聊天等待手动点击生成
  const decision = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: false,
    hasCachedResult: false,
    quotaExhaustedToday: false,
  });

  assert.equal(decision.action, CHAT_AUTO_ACTIONS.WAIT_MANUAL);
  assert.equal(decision.shouldGenerate, false);
  assert.equal(decision.message, "点'生成'获取建议");

  // 即便有缓存，只要 autoGenerate 为 false 也不自动生成
  const decisionWithCache = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: false,
    hasCachedResult: true,
  });
  assert.equal(decisionWithCache.action, CHAT_AUTO_ACTIONS.WAIT_MANUAL);
  assert.equal(decisionWithCache.shouldGenerate, false);
});

test("T070: 当天已用完 → 不自动 (FR-040, SC-008, R2)", () => {
  const today = getLocalDateString();
  assert.equal(isQuotaExhaustedToday(today), true);
  assert.equal(isQuotaExhaustedToday("2020-01-01"), false);
  assert.equal(isQuotaExhaustedToday(null), false);
  assert.equal(isQuotaExhaustedToday(undefined), false);

  // 当天次数已用完且未命中缓存时拦截自动请求
  const decision = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: true,
    hasCachedResult: false,
    quotaExhaustedToday: true,
  });

  assert.equal(decision.action, CHAT_AUTO_ACTIONS.QUOTA_EXHAUSTED);
  assert.equal(decision.shouldGenerate, false);
  assert.equal(decision.message, "今日生成次数已用完");
  assert.equal(formatChatError("quota_exhausted"), "今日生成次数已用完");

  // 跨天后恢复自动生成
  const yesterday = "2026-09-25";
  const quotaExhaustedYesterday = isQuotaExhaustedToday(yesterday, "2026-09-26");
  assert.equal(quotaExhaustedYesterday, false);

  const nextDayDecision = decideChatAutoAction({
    isChatPage: true,
    autoGenerate: true,
    hasCachedResult: false,
    quotaExhaustedToday: quotaExhaustedYesterday,
  });
  assert.equal(nextDayDecision.action, CHAT_AUTO_ACTIONS.AUTO_GENERATE);
  assert.equal(nextDayDecision.shouldGenerate, true);
});

test("T070: 不同岗位 ID 的缓存互不串用 (SC-007)", () => {
  const messages = [
    { sender: "HR", is_self: false, body_type: 1, text: "您好，方便发简历吗？", time: 100 },
  ];

  const keyJobA = computeChatCacheKey("job_AAA", messages);
  const keyJobB = computeChatCacheKey("job_BBB", messages);

  assert.notEqual(keyJobA, keyJobB);

  const chatResultsCache = new Map();
  chatResultsCache.set(keyJobA, {
    bound_job_id: "job_AAA",
    company_name: "公司A",
    suggestions: [{ text: "建议A" }],
  });

  // 查询岗位 B 的缓存不命中，绝不显示岗位 A 的结果
  assert.equal(chatResultsCache.has(keyJobB), false);
  assert.equal(chatResultsCache.get(keyJobB), undefined);

  // 岗位 A 的缓存正常读取
  assert.equal(chatResultsCache.has(keyJobA), true);
  assert.equal(chatResultsCache.get(keyJobA).company_name, "公司A");
});

test("T070: 手动重新生成覆盖当前键缓存 (FR-013, FR-039)", () => {
  const jobId = "job_switch_mode";
  const messages = [
    { sender: "HR", is_self: false, body_type: 1, text: "您好", time: 100 },
  ];
  const cacheKey = computeChatCacheKey(jobId, messages);

  const chatResultsCache = new Map();
  // 首次生成结果（开场白）
  const result1 = {
    mode: "opening",
    mode_label: "开场白",
    suggestions: [{ version: 1, text: "开场白版本1" }],
  };
  chatResultsCache.set(cacheKey, result1);
  assert.equal(chatResultsCache.get(cacheKey).mode, "opening");

  // 手动切换为建议回复并重新生成，结果覆盖当前键
  const result2 = {
    mode: "reply",
    mode_label: "建议回复",
    suggestions: [{ version: 1, text: "建议回复版本1" }],
  };
  chatResultsCache.set(cacheKey, result2);
  assert.equal(chatResultsCache.get(cacheKey).mode, "reply");
  assert.equal(chatResultsCache.get(cacheKey).suggestions[0].text, "建议回复版本1");
});
