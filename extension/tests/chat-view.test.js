import test from "node:test";
import assert from "node:assert/strict";
import {
  ERROR_MESSAGES,
  formatChatError,
  MODE_LABELS,
  formatModeDecision,
  formatAttribution,
  formatLoadedMessagesNotice,
  formatSourceNote,
  getCopyableText,
  verifyCopyJobId,
  NO_JET_JUDGEMENT_NOTICE,
  getDisplaySuggestions,
  getNextForceMode,
  getSwitchModeButtonText,
  getDisplayNotices,
  getDisplayDroppedSummary,
  formatUnansweredFactNotices,
} from "../src/chat-view.js";

test("T027: error code to R2 Chinese error message mapping", () => {
  // 还没同意发送 / 已撤回同意
  assert.equal(formatChatError("consent_required"), "尚未同意发送脱敏数据");
  assert.equal(formatChatError({ error: "consent_required" }), "尚未同意发送脱敏数据");

  // 读不到姓名
  assert.equal(formatChatError("self_name_unavailable"), "读不到你的姓名，暂时不能生成");
  assert.equal(formatChatError({ error: "self_name_unavailable" }), "读不到你的姓名，暂时不能生成");

  // 哈希不一致
  assert.equal(formatChatError("hash_mismatch"), "发送内容校验不一致，请重新预览后再试");
  assert.equal(formatChatError({ error: "hash_mismatch" }), "发送内容校验不一致，请重新预览后再试");

  // 今日生成次数已用完
  assert.equal(formatChatError("quota_exhausted"), "今日生成次数已用完");
  assert.equal(formatChatError({ error: "quota_exhausted" }), "今日生成次数已用完");

  // 大模型调用失败（带原因与不带原因）
  assert.equal(
    formatChatError("llm_failed", "服务响应超时"),
    "生成失败：服务响应超时，可以再点一次生成"
  );
  assert.equal(
    formatChatError({ error: "llm_failed", reason: "网络连接断开" }),
    "生成失败：网络连接断开，可以再点一次生成"
  );
  assert.equal(
    formatChatError("llm_failed"),
    "生成失败，可以再点一次生成"
  );

  // 返回的话术引用了不存在的经历条目全部被丢弃
  assert.equal(
    formatChatError("all_suggestions_dropped"),
    "这次没有可用的建议，可以再点一次生成"
  );

  // Jet 未配对与 Jet 没有运行
  assert.equal(formatChatError("unpaired"), "未配对");
  assert.equal(formatChatError("jet_down"), "Jet 没有运行，请先启动本机 Jet");
  assert.equal(formatChatError({ viewState: "jet_down" }), "Jet 没有运行，请先启动本机 Jet");

  // 读不到当前聊天
  assert.equal(
    formatChatError("read_chat_failed"),
    "读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试"
  );
  assert.equal(
    formatChatError("chat_unavailable"),
    "读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试"
  );

  // 切换到新聊天
  assert.equal(formatChatError("chat_switched"), "已切换到新的聊天，请重新生成");

  // 检查 ERROR_MESSAGES 常量字典覆盖
  assert.ok(ERROR_MESSAGES.consent_required);
  assert.ok(ERROR_MESSAGES.self_name_unavailable);
  assert.ok(ERROR_MESSAGES.hash_mismatch);
  assert.ok(ERROR_MESSAGES.quota_exhausted);
  assert.ok(ERROR_MESSAGES.all_suggestions_dropped);
  assert.ok(ERROR_MESSAGES.unpaired);
  assert.ok(ERROR_MESSAGES.jet_down);
  assert.ok(ERROR_MESSAGES.read_chat_failed);
  assert.ok(ERROR_MESSAGES.chat_switched);
});

test("T027: communication mode Chinese labels and decision display", () => {
  // 常量与标签对应
  assert.equal(MODE_LABELS.opening, "开场白");
  assert.equal(MODE_LABELS.reply, "建议回复");
  assert.equal(MODE_LABELS.waiting_hr, "正在等 HR 回复");

  // FR-013 判定类型展示
  assert.equal(formatModeDecision("opening"), "判断为：开场白");
  assert.equal(formatModeDecision("reply"), "判断为：建议回复");
  assert.equal(formatModeDecision("waiting_hr"), "正在等 HR 回复");
});

test("T027: attribution notice format (FR-016)", () => {
  assert.equal(
    formatAttribution("某新能源科技公司", "储能海外销售（驻尼日利亚等）"),
    "这是给 某新能源科技公司 · 储能海外销售（驻尼日利亚等） 的建议"
  );

  assert.equal(
    formatAttribution("华为技术有限公司", "软件开发工程师"),
    "这是给 华为技术有限公司 · 软件开发工程师 的建议"
  );

  // 边界容错
  assert.equal(
    formatAttribution("", ""),
    "这是给 该公司 · 该职位 的建议"
  );
  assert.equal(
    formatAttribution(null, undefined),
    "这是给 该公司 · 该职位 的建议"
  );
});

test("T027: loaded message count notice format (FR-017)", () => {
  assert.equal(formatLoadedMessagesNotice(3), "根据已加载的 3 条消息生成");
  assert.equal(formatLoadedMessagesNotice(30), "根据已加载的 30 条消息生成");
  assert.equal(formatLoadedMessagesNotice(0), "根据已加载的 0 条消息生成");
  assert.equal(formatLoadedMessagesNotice("5"), "根据已加载的 5 条消息生成");
  assert.equal(formatLoadedMessagesNotice(null), "根据已加载的 0 条消息生成");
});

test("T027: source note formatting and clean copy text (FR-030, FR-032)", () => {
  // 单条经历引用
  assert.equal(formatSourceNote([1]), "来源：经历条目 1");

  // 多条经历引用
  assert.equal(formatSourceNote([1, 2]), "来源：经历条目 1、2");
  assert.equal(formatSourceNote([3, 5, 8]), "来源：经历条目 3、5、8");

  // 无经历或空数组
  assert.equal(formatSourceNote([]), "没有可引用的经历");
  assert.equal(formatSourceNote(null), "没有可引用的经历");
  assert.equal(formatSourceNote(undefined), "没有可引用的经历");

  // 复制文本保证不夹带来源说明编号或标注
  const suggestion = {
    version: 1,
    tone_desc: "自然直接",
    text: "您好，看到贵公司的海外拓展方向与我的经历很吻合，想了解一下该岗位主要负责哪些区域？",
    referenced_experience_ids: [1, 2],
  };
  const copyText = getCopyableText(suggestion);
  assert.equal(copyText, suggestion.text);
  assert.ok(!copyText.includes("经历条目"));
  assert.ok(!copyText.includes("[1]"));
});

test("T027: no jet judgement notice (FR-011)", () => {
  assert.equal(NO_JET_JUDGEMENT_NOTICE, "这个岗位没有 Jet 判断");
});

test("T025/T027: verifyCopyJobId - allows copy when job IDs match, rejects on mismatch or empty (FR-005, V8)", () => {
  // ID 相同 -> 允许
  assert.deepEqual(verifyCopyJobId("job_123", "job_123"), { ok: true });
  assert.deepEqual(
    verifyCopyJobId("0000aaaa1111bbbbX1-fakeJobId1", "0000aaaa1111bbbbX1-fakeJobId1"),
    { ok: true }
  );

  // ID 不同 -> 拒绝
  assert.deepEqual(verifyCopyJobId("job_123", "job_456"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId("other_job_id_9999", "0000aaaa1111bbbbX1-fakeJobId1"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });

  // 当前 ID 为空 (null / undefined / '') -> 拒绝
  assert.deepEqual(verifyCopyJobId(null, "job_123"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId(null, "0000aaaa1111bbbbX1-fakeJobId1"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId(undefined, "job_123"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId("", "job_123"), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });

  // 绑定 ID 为空 (null / undefined / '') -> 拒绝
  assert.deepEqual(verifyCopyJobId("job_123", null), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId("job_123", undefined), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
  assert.deepEqual(verifyCopyJobId("job_123", ""), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });

  // 双方均为空 -> 拒绝
  assert.deepEqual(verifyCopyJobId(null, null), {
    ok: false,
    error: "已切换到新的聊天，请重新生成",
  });
});

test("T024b: getCopyableText - copies only suggestion text without source note or experience numbers (FR-030)", () => {
  const itemWithExperiences = {
    version: 1,
    tone_desc: "自然直接",
    text: "您好，我之前在新能源外贸领域负责西非市场拓展，对贵公司岗位很感兴趣。",
    referenced_experience_ids: [1, 2, 3],
  };
  const copyText = getCopyableText(itemWithExperiences);
  assert.equal(copyText, itemWithExperiences.text);
  assert.ok(!copyText.includes("来源"));
  assert.ok(!copyText.includes("经历条目"));
  assert.ok(!copyText.includes("1"));
  assert.ok(!copyText.includes("2"));
  assert.ok(!copyText.includes("3"));

  // 问题字符串直接返回
  assert.equal(getCopyableText("请问该岗位是否有海外出差要求？"), "请问该岗位是否有海外出差要求？");

  // 空值容错
  assert.equal(getCopyableText(null), "");
  assert.equal(getCopyableText(undefined), "");
  assert.equal(getCopyableText({}), "");
});

test("T024b: getDisplaySuggestions - returns empty suggestion list when mode is waiting_hr (FR-012, FR-014)", () => {
  const waitingResult = {
    mode: "waiting_hr",
    suggestions: [
      { version: 1, text: "正在等回复时不展示建议", referenced_experience_ids: [1] },
    ],
    questions: ["请问目前处于招聘流程的哪个阶段？"],
  };

  // mode 为 waiting_hr 时话术列表为空
  assert.deepEqual(getDisplaySuggestions(waitingResult), []);
  assert.deepEqual(getDisplaySuggestions("waiting_hr", waitingResult.suggestions), []);

  // opening 与 reply 模式正常返回话术列表
  const openingResult = {
    mode: "opening",
    suggestions: [{ version: 1, text: "您好！", referenced_experience_ids: [] }],
  };
  assert.deepEqual(getDisplaySuggestions(openingResult), openingResult.suggestions);

  const replyResult = {
    mode: "reply",
    suggestions: [{ version: 1, text: "好的，了解。", referenced_experience_ids: [1] }],
  };
  assert.deepEqual(getDisplaySuggestions(replyResult), replyResult.suggestions);

  // 空值或非数组容错
  assert.deepEqual(getDisplaySuggestions(null), []);
  assert.deepEqual(getDisplaySuggestions({ mode: "reply", suggestions: null }), []);
});

test("T024b: getNextForceMode and getSwitchModeButtonText - mode switching helpers (FR-013)", () => {
  // opening -> reply
  assert.equal(getNextForceMode("opening"), "reply");
  assert.equal(getSwitchModeButtonText("opening"), "切换为建议回复");

  // reply -> opening
  assert.equal(getNextForceMode("reply"), "opening");
  assert.equal(getSwitchModeButtonText("reply"), "切换为开场白");

  // waiting_hr 不支持切换
  assert.equal(getNextForceMode("waiting_hr"), null);
  assert.equal(getSwitchModeButtonText("waiting_hr"), "");

  // 未知模式
  assert.equal(getNextForceMode("other"), null);
  assert.equal(getSwitchModeButtonText("other"), "");
});

test("FR-054/FR-055: getDisplayNotices - both request_notice and unanswered_facts present", () => {
  const data = {
    request_notice: "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
    unanswered_facts: [
      {
        name: "所在城市",
        notice: "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
      },
    ],
  };

  const notices = getDisplayNotices(data);
  assert.equal(notices.length, 2);
  assert.deepEqual(notices[0], {
    type: "request",
    text: "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
    notice: "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
  });
  assert.deepEqual(notices[1], {
    type: "unanswered_fact",
    name: "所在城市",
    text: "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
    notice: "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
  });

  // 分别传参验证
  const separateNotices = getDisplayNotices(data.unanswered_facts, data.request_notice);
  assert.equal(separateNotices.length, 2);
  assert.equal(separateNotices[0].type, "request");
  assert.equal(separateNotices[1].type, "unanswered_fact");
});

test("FR-054/FR-055: getDisplayNotices - only one field present", () => {
  // 1. 只有 request_notice
  const onlyRequestData = {
    request_notice: "HR 发了请求（卡片文字），需要你在 BOSS 里点同意或拒绝",
    unanswered_facts: [],
  };
  const requestOnlyList = getDisplayNotices(onlyRequestData);
  assert.equal(requestOnlyList.length, 1);
  assert.deepEqual(requestOnlyList[0], {
    type: "request",
    text: "HR 发了请求（卡片文字），需要你在 BOSS 里点同意或拒绝",
    notice: "HR 发了请求（卡片文字），需要你在 BOSS 里点同意或拒绝",
  });

  // 单独参数（unanswered_facts 为空）
  const separateReqOnly = getDisplayNotices(null, "HR 发了请求（卡片文字），需要你在 BOSS 里点同意或拒绝");
  assert.equal(separateReqOnly.length, 1);
  assert.equal(separateReqOnly[0].type, "request");

  // 2. 只有 unanswered_facts
  const onlyFactsData = {
    request_notice: null,
    unanswered_facts: [
      {
        name: "期望薪资",
        notice: "HR 问了期望薪资，你的资料里没有，这部分请你自己回答",
      },
    ],
  };
  const factsOnlyList = getDisplayNotices(onlyFactsData);
  assert.equal(factsOnlyList.length, 1);
  assert.deepEqual(factsOnlyList[0], {
    type: "unanswered_fact",
    name: "期望薪资",
    text: "HR 问了期望薪资，你的资料里没有，这部分请你自己回答",
    notice: "HR 问了期望薪资，你的资料里没有，这部分请你自己回答",
  });

  // 单独参数（request_notice 为 null）
  const separateFactsOnly = getDisplayNotices(onlyFactsData.unanswered_facts, null);
  assert.equal(separateFactsOnly.length, 1);
  assert.equal(separateFactsOnly[0].type, "unanswered_fact");
});

test("FR-054/FR-055: getDisplayNotices - neither field present (null / empty array)", () => {
  // 两者都为空或 null
  assert.deepEqual(getDisplayNotices({ request_notice: null, unanswered_facts: [] }), []);
  assert.deepEqual(getDisplayNotices({ request_notice: "", unanswered_facts: null }), []);
  assert.deepEqual(getDisplayNotices({ request_notice: "   ", unanswered_facts: [] }), []);
  assert.deepEqual(getDisplayNotices(null), []);
  assert.deepEqual(getDisplayNotices({}), []);
  assert.deepEqual(getDisplayNotices([], null), []);
  assert.deepEqual(getDisplayNotices(null, null), []);
  assert.deepEqual(getDisplayNotices([], ""), []);
  assert.deepEqual(getDisplayNotices(undefined, undefined), []);
});

test("FR-054/FR-055: notices do not appear in any copyable text", () => {
  const reqNoticeText = "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝";
  const factNoticeText = "HR 问了所在城市，你的资料里没有，这部分请你自己回答";

  const resultData = {
    suggestions: [
      {
        version: 1,
        tone_desc: "自然直接",
        text: "您好，我之前在新能源外贸领域负责西非市场拓展，对贵公司岗位很感兴趣。",
        referenced_experience_ids: [1],
      },
    ],
    questions: ["请问该岗位在尼日利亚有常驻人员还是定期出差？"],
    request_notice: reqNoticeText,
    unanswered_facts: [
      {
        name: "所在城市",
        notice: factNoticeText,
      },
    ],
  };

  // 1. 复制话术时：内容纯净，绝不包含 request_notice 或 unanswered_facts
  const copySuggestion = getCopyableText(resultData.suggestions[0]);
  assert.equal(copySuggestion, resultData.suggestions[0].text);
  assert.ok(!copySuggestion.includes(reqNoticeText));
  assert.ok(!copySuggestion.includes(factNoticeText));
  assert.ok(!copySuggestion.includes("HR 发了请求"));
  assert.ok(!copySuggestion.includes("你的资料里没有"));

  // 2. 复制建议问题时：内容纯净，绝不包含提示
  const copyQuestion = getCopyableText(resultData.questions[0]);
  assert.equal(copyQuestion, resultData.questions[0]);
  assert.ok(!copyQuestion.includes(reqNoticeText));
  assert.ok(!copyQuestion.includes(factNoticeText));

  // 3. 提示项本身不属于可复制话术文本
  const notices = getDisplayNotices(resultData);
  for (const n of notices) {
    assert.equal(getCopyableText(n), "");
  }
});

test("FR-054/SC-014: suggestions 为空但有问题和两条提示时的待显示内容（含去掉原因，不含'没有可用的建议'）", () => {
  const resultData = {
    company_name: "某新能源科技公司",
    job_title: "储能海外销售（驻尼日利亚等）",
    mode: "reply",
    mode_label: "建议回复",
    mode_basis: "HR：方便发份简历吗？",
    has_jet_judgement: true,
    message_count: 3,
    suggestions: [],
    experience_note: null,
    questions: [
      "尼日利亚那边的业务目前是刚起步还是已经有成熟渠道？",
      "想确认一下平时主要对接的是海外代理商还是终端客户？",
    ],
    request_notice: "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
    unanswered_facts: [
      {
        name: "所在城市",
        notice: "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
      },
    ],
    dropped_summary: [
      {
        reason: "unsupported_fact",
        category: "所在城市",
        count: 2,
        text: "2 个版本因提到你资料里没有的'所在城市'被去掉",
      },
    ],
  };

  // 1. suggestions 为空时，getDisplaySuggestions 返回空数组
  const displaySuggestions = getDisplaySuggestions(resultData);
  assert.deepEqual(displaySuggestions, []);

  // 2. 照常显示两条提示（request_notice 与 unanswered_facts）
  const notices = getDisplayNotices(resultData);
  assert.equal(notices.length, 2);
  assert.equal(notices[0].type, "request");
  assert.equal(notices[0].text, resultData.request_notice);
  assert.equal(notices[1].type, "unanswered_fact");
  assert.equal(notices[1].text, resultData.unanswered_facts[0].notice);

  // 3. 显示 dropped_summary 每项的 text（含去掉原因）
  const droppedList = getDisplayDroppedSummary(resultData);
  assert.equal(droppedList.length, 1);
  assert.deepEqual(droppedList[0], {
    type: "dropped_summary",
    reason: "unsupported_fact",
    category: "所在城市",
    count: 2,
    text: "2 个版本因提到你资料里没有的'所在城市'被去掉",
    notice: "2 个版本因提到你资料里没有的'所在城市'被去掉",
  });
  assert.equal(droppedList[0].text, "2 个版本因提到你资料里没有的'所在城市'被去掉");

  // 4. 照常显示问题
  assert.equal(resultData.questions.length, 2);
  assert.equal(resultData.questions[0], "尼日利亚那边的业务目前是刚起步还是已经有成熟渠道？");

  // 5. 不包含"没有可用的建议"（或"这次没有可用的建议"）
  const allTexts = [
    ...notices.map((n) => n.text),
    ...droppedList.map((d) => d.text),
    ...displaySuggestions.map((s) => s.text),
    ...resultData.questions,
  ];
  for (const text of allTexts) {
    assert.ok(!text.includes("没有可用的建议"));
  }
  // 正常生成成功响应不会被格式化为 all_suggestions_dropped 错误
  assert.notEqual(formatChatError(resultData), "这次没有可用的建议，可以再点一次生成");
  // 只有真正收到 all_suggestions_dropped 错误时才返回该提示
  assert.equal(
    formatChatError("all_suggestions_dropped"),
    "这次没有可用的建议，可以再点一次生成"
  );
  assert.equal(
    formatChatError({ error: "all_suggestions_dropped" }),
    "这次没有可用的建议，可以再点一次生成"
  );
});

test("FR-054: getDisplayDroppedSummary - dropped_summary 为空或缺失时不显示说明", () => {
  // 1. dropped_summary 为空数组
  assert.deepEqual(getDisplayDroppedSummary({ dropped_summary: [] }), []);
  assert.deepEqual(getDisplayDroppedSummary([]), []);

  // 2. dropped_summary 为 null 或 undefined
  assert.deepEqual(getDisplayDroppedSummary({ dropped_summary: null }), []);
  assert.deepEqual(getDisplayDroppedSummary({ dropped_summary: undefined }), []);

  // 3. dropped_summary 字段缺失
  assert.deepEqual(getDisplayDroppedSummary({}), []);
  assert.deepEqual(getDisplayDroppedSummary(null), []);
  assert.deepEqual(getDisplayDroppedSummary(undefined), []);

  // 4. 驼峰 droppedSummary 兼容
  assert.deepEqual(getDisplayDroppedSummary({ droppedSummary: [] }), []);
  assert.deepEqual(getDisplayDroppedSummary({ droppedSummary: null }), []);

  // 5. 内部空元素或无效元素过滤
  assert.deepEqual(getDisplayDroppedSummary([null, undefined, "", {}]), []);
});

test("FR-054: dropped_summary 说明不出现在任何复制文本中", () => {
  const droppedText = "2 个版本因提到你资料里没有的'所在城市'被去掉";
  const resultData = {
    suggestions: [],
    questions: [
      "想确认一下平时主要对接的是海外代理商还是终端客户？",
      "尼日利亚那边的业务目前是刚起步还是已经有成熟渠道？",
    ],
    request_notice: "HR 发了请求（我想要一份您的附件简历，您是否同意），需要你在 BOSS 里点同意或拒绝",
    unanswered_facts: [
      {
        name: "所在城市",
        notice: "HR 问了所在城市，你的资料里没有，这部分请你自己回答",
      },
    ],
    dropped_summary: [
      {
        reason: "unsupported_fact",
        category: "所在城市",
        count: 2,
        text: droppedText,
      },
    ],
  };

  // 1. dropped_summary 说明项本身调用 getCopyableText 返回空字符串
  const droppedList = getDisplayDroppedSummary(resultData);
  assert.equal(droppedList.length, 1);
  assert.equal(getCopyableText(droppedList[0]), "");

  // 2. 复制问题时，复制内容纯净，绝不含 dropped_summary 说明文字
  for (const q of resultData.questions) {
    const copyText = getCopyableText(q);
    assert.equal(copyText, q);
    assert.ok(!copyText.includes(droppedText));
    assert.ok(!copyText.includes("被去掉"));
    assert.ok(!copyText.includes("所在城市"));
  }

  // 3. 当有正常话术时，复制话术也不含 dropped_summary 说明文字
  const normalSuggestion = {
    version: 1,
    tone_desc: "自然直接",
    text: "您好，我之前在新能源外贸领域负责西非市场拓展，对贵公司岗位很感兴趣。",
    referenced_experience_ids: [1],
  };
  const suggestionCopy = getCopyableText(normalSuggestion);
  assert.equal(suggestionCopy, normalSuggestion.text);
  assert.ok(!suggestionCopy.includes(droppedText));
  assert.ok(!suggestionCopy.includes("被去掉"));

  // 4. 各种格式的 dropped 描述对象传给 getCopyableText 均返回空字符串
  assert.equal(getCopyableText({ type: "dropped_summary", text: droppedText }), "");
  assert.equal(getCopyableText({ reason: "unsupported_fact", count: 2, text: droppedText }), "");
  assert.equal(getCopyableText({ reason: "too_long", text: "1 个版本因超过 50 字被去掉" }), "");
  assert.equal(getCopyableText({ category: "所在城市", text: droppedText }), "");
});

test("formatUnansweredFactNotices: 提示用户把话术里的【填写：…】替换成实际情况", () => {
  const notices = formatUnansweredFactNotices([
    { name: "所在城市" },
    { name: "期望薪资", notice: "旧提示" },
  ]);
  assert.equal(notices.length, 2);
  assert.equal(notices[0], "HR 问了所在城市，你的资料里没有，请把话术里的【填写：所在城市】替换成实际情况");
  assert.equal(notices[1], "HR 问了期望薪资，你的资料里没有，请把话术里的【填写：期望薪资】替换成实际情况");
});
