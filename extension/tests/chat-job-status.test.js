import test from "node:test";
import assert from "node:assert/strict";

import {
  isJobInLibrary,
  formatChatJobStatus,
  formatChatJobErrorStatus,
  syncChatJobStatusInTabStates,
  isBossUrl,
  isChatJobResponseCurrent,
  switchChatJobWithEditorFlush,
  NOT_IN_LIBRARY_NOTICE,
  pickCachedChatJobStatus,
  CHAT_JOB_STATUS_ACTIONS,
  computeNextJobStatus,
  extractJobStatusKey,
  getChatJobStatusButtons,
} from "../src/chat-view.js";
import { buildHrNotePayload } from "../src/myjobs-view.js";
import { createAutoSaveCoordinator } from "../src/label-form.js";

// ============================================================================
// 1. 在库/不在库判定与展示数据的纯函数 (T059, FR-056–FR-060, SC-016–SC-019)
// ============================================================================

test("isJobInLibrary: 有状态、判断过或有非空 HR 记录的岗位判定为在库", () => {
  // a. 有判断结论 -> 在库
  assert.equal(
    isJobInLibrary({
      judgement: { verdict: "try", verdict_label: "可以一试" },
    }),
    true
  );

  // b. 有状态 (saved/applied/skipped) -> 在库
  assert.equal(
    isJobInLibrary({
      my_status: { status: "saved" },
    }),
    true
  );
  assert.equal(
    isJobInLibrary({
      my_status: { status: "applied" },
    }),
    true
  );
  assert.equal(
    isJobInLibrary({
      my_status: { status: "skipped" },
    }),
    true
  );

  // c. 有非空 HR 记录 -> 在库
  assert.equal(
    isJobInLibrary({
      hr_note: "HR告知提供双休与餐补",
    }),
    true
  );
});

test("isJobInLibrary: 不在库判定（无状态、无判断且无 HR 记录；或仅在 jobs 表中未满足条件）", () => {
  // a. 空对象、null、undefined
  assert.equal(isJobInLibrary(null), false);
  assert.equal(isJobInLibrary(undefined), false);
  assert.equal(isJobInLibrary({}), false);

  // b. 仅存在于 jobs 表中（如搜索列表读到），但无状态、未判断过且 HR 记录为空 -> 不在库
  assert.equal(
    isJobInLibrary({
      completeness: "list",
      title: "初级开发工程师",
      company_name: "某科技公司",
      judgement: null,
      my_status: null,
      hr_note: null,
    }),
    false
  );

  // c. my_status.status 为 null 且 hr_note 为纯空白
  assert.equal(
    isJobInLibrary({
      my_status: { status: null },
      judgement: null,
      hr_note: "   \n\t  ",
    }),
    false
  );
});

test("formatChatJobStatus: 不在库时返回原文提示与不在库标记 (FR-059, SC-018)", () => {
  const result = formatChatJobStatus("job_not_in_lib", null);

  assert.equal(result.platform_job_id, "job_not_in_lib");
  assert.equal(result.in_library, false);
  assert.equal(
    result.notice,
    "岗位库里没有这个岗位。在 BOSS 搜索列表里看到它时，Jet 会记录并判断"
  );
  assert.equal(result.verdict_label, null);
  assert.equal(result.hr_note, null);
});

test("formatChatJobStatus: 在库时返回完整展示数据与 Jet 结论与 HR 实际情况 (FR-057, SC-016)", () => {
  const jobEntry = {
    title: "储能海外销售（驻尼日利亚等）",
    company_name: "某新能源科技公司",
    judgement: {
      status: "done",
      verdict: "try",
      source: "llm",
      reasons: ["行业前景良好，薪资待遇匹配"],
      judged_at: "2026-09-26T04:20:00Z",
      stale: { job_changed: false, profile_changed: false, method_changed: false },
      error: null,
      salary_visible: true,
      prompt_version: "v5",
      verdict_reason: "行业前景良好，薪资待遇匹配",
      hr_questions: [],
      review: null,
      engine: "deepseek-flash:no-think",
      facts: null,
      derivation: ["行业前景良好，薪资待遇匹配"],
      label: null,
      origin: "initial",
      replacing: null,
    },
    hr_note: "HR告知前半年在深圳培训，之后常驻西非办事处",
    my_status: {
      status: "saved",
      updated_at: "2026-09-26T04:20:00Z",
    },
  };

  const result = formatChatJobStatus("0000aaaa1111bbbbX1-fakeJobId1", jobEntry);

  assert.equal(result.platform_job_id, "0000aaaa1111bbbbX1-fakeJobId1");
  assert.equal(result.in_library, true);
  assert.equal(result.title, "储能海外销售（驻尼日利亚等）");
  assert.equal(result.company_name, "某新能源科技公司");
  assert.equal(result.verdict_label, "可以一试");
  assert.equal(result.verdict_tone, "blue");
  assert.equal(result.summary_reason, "行业前景良好，薪资待遇匹配");
  assert.equal(result.hr_note, "HR告知前半年在深圳培训，之后常驻西非办事处");
  assert.equal(result.stale, false);
  assert.equal(result.judged_at, "2026-09-26T04:20:00Z");
  assert.equal(result.notice, null);
  // 断言理由、颜色、判断时间都有值
  assert.ok(result.summary_reason);
  assert.ok(result.verdict_tone);
  assert.ok(result.judged_at);
});

// ============================================================================
// 2. 切换聊天与乱序响应只采用当前 ID (T059, FR-060, SC-019)
// ============================================================================

test("isChatJobResponseCurrent: 响应所属 ID 与当前聊天 ID 一致时采纳，否则丢弃", () => {
  // 一致时采纳
  assert.equal(isChatJobResponseCurrent("job_001", "job_001"), true);
  assert.equal(isChatJobResponseCurrent(" job_001 ", "job_001"), true);

  // 乱序/不一致时丢弃
  assert.equal(isChatJobResponseCurrent("job_001", "job_002"), false);
  assert.equal(isChatJobResponseCurrent(null, "job_001"), false);
  assert.equal(isChatJobResponseCurrent("job_001", null), false);
  assert.equal(isChatJobResponseCurrent("", ""), false);
});

test("乱序响应模拟测试: 快速切换聊天时只采用当前正在查看的岗位响应", () => {
  let activeJobId = "job_A";
  const displayedJobs = [];

  function onResponseReceived(responseJobId, data) {
    if (isChatJobResponseCurrent(responseJobId, activeJobId)) {
      displayedJobs.push(data.title);
    }
  }

  // 1. 用户先点击 Job A，发起请求 reqA
  const reqAJobId = "job_A";

  // 2. 在 reqA 返回前，用户快速切换到 Job B，发起请求 reqB
  activeJobId = "job_B";
  const reqBJobId = "job_B";

  // 3. reqB 先返回 (耗时短) -> 采纳 Job B
  onResponseReceived(reqBJobId, { title: "岗位 B" });

  // 4. reqA 迟到返回 -> 被安全拦截丢弃，不覆盖岗位 B
  onResponseReceived(reqAJobId, { title: "岗位 A" });

  assert.deepEqual(displayedJobs, ["岗位 B"]);
});

// ============================================================================
// 3. save_hr_note 的 chat_sidebar 放行 (T059, FR-058)
// ============================================================================

test("buildHrNotePayload: 放行 source='chat_sidebar'、'myjobs'、'card'，过滤未知来源", () => {
  // a. chat_sidebar 放行
  const p1 = buildHrNotePayload("HR说下周二初试", "chat_sidebar");
  assert.deepEqual(p1, {
    note: "HR说下周二初试",
    source: "chat_sidebar",
  });

  // b. myjobs 与 card 同样放行
  const p2 = buildHrNotePayload("HR说可以远程", "myjobs");
  assert.deepEqual(p2, {
    note: "HR说可以远程",
    source: "myjobs",
  });

  const p3 = buildHrNotePayload("卡片备注", "card");
  assert.deepEqual(p3, {
    note: "卡片备注",
    source: "card",
  });

  // c. 未知来源不带 source 属性
  const p4 = buildHrNotePayload("普通备注", "unknown_source");
  assert.deepEqual(p4, {
    note: "普通备注",
  });

  // d. 空白字符在 chat_sidebar 下 note 规范为 null
  const p5 = buildHrNotePayload("   \n  ", "chat_sidebar");
  assert.deepEqual(p5, {
    note: null,
    source: "chat_sidebar",
  });
});

// ============================================================================
// 4. 侧边栏编辑中切换聊天时保存目标是原岗位 (T059, FR-058, T061)
// ============================================================================

test("switchChatJobWithEditorFlush: 编辑中切换聊天时先 flush 当前内容到原岗位，不得存到新岗位", async () => {
  const saveCalls = [];

  // 模拟为原岗位 job_old 初始化的自动保存协调器
  const oldJobId = "job_old";
  const newJobId = "job_new";

  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: "原初记录",
    emptyMessage: "清空请到岗位卡片操作",
    save: async (note) => {
      // 生产代码中 coordinator 绑定的 save 闭包天然持有原岗位 ID
      saveCalls.push({
        platform_job_id: oldJobId,
        note,
        source: "chat_sidebar",
      });
      return { ok: true, hr_note: note };
    },
  });

  // 正在编辑原岗位的内容
  let currentEditingText = "HR在离开前补充说了有季度奖金";
  const activeEditor = {
    jobId: oldJobId,
    coordinator,
    getText: () => currentEditingText,
  };

  let switchedToId = null;

  // 执行切换
  const switchResult = await switchChatJobWithEditorFlush({
    activeEditor,
    newJobId,
    onSwitch: (id) => {
      switchedToId = id;
    },
  });

  // 验证保存目标：必须且只能保存到原岗位 job_old，绝不是新岗位 job_new
  assert.equal(saveCalls.length, 1);
  assert.deepEqual(saveCalls[0], {
    platform_job_id: "job_old",
    note: "HR在离开前补充说了有季度奖金",
    source: "chat_sidebar",
  });

  // 验证切换结果
  assert.equal(switchResult.flushed, true);
  assert.equal(switchResult.flushedJobId, "job_old");
  assert.equal(switchResult.switchedToJobId, "job_new");
  assert.equal(switchedToId, "job_new");
});

test("switchChatJobWithEditorFlush: 未处于编辑状态时切换聊天不触发任何保存", async () => {
  let switchedToId = null;
  const switchResult = await switchChatJobWithEditorFlush({
    activeEditor: null,
    newJobId: "job_new",
    onSwitch: (id) => {
      switchedToId = id;
    },
  });

  assert.equal(switchResult.flushed, false);
  assert.equal(switchResult.flushedJobId, null);
  assert.equal(switchResult.switchedToJobId, "job_new");
  assert.equal(switchedToId, "job_new");
});

test("switchChatJobWithEditorFlush: 切换到相同岗位 ID 时不重复 flush", async () => {
  let saveCount = 0;
  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: null,
    save: async () => {
      saveCount++;
      return { ok: true };
    },
  });

  const activeEditor = {
    jobId: "job_same",
    coordinator,
    getText: () => "新文字",
  };

  const switchResult = await switchChatJobWithEditorFlush({
    activeEditor,
    newJobId: "job_same",
  });

  assert.equal(switchResult.flushed, false);
  assert.equal(saveCount, 0);
});

test("switchChatJobWithEditorFlush: 编辑内容为空时协调器拒绝保存，不发送空请求到原岗位", async () => {
  const saveCalls = [];
  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: "已有内容",
    emptyMessage: "清空请到岗位卡片操作",
    save: async (note) => {
      saveCalls.push(note);
      return { ok: true };
    },
  });

  const activeEditor = {
    jobId: "job_old",
    coordinator,
    getText: () => "   ", // 纯空格清空
  };

  let switchedTo = null;
  const result = await switchChatJobWithEditorFlush({
    activeEditor,
    newJobId: "job_new",
    onSwitch: (id) => {
      switchedTo = id;
    },
  });

  // 空内容不保存
  assert.equal(saveCalls.length, 0);
  assert.equal(switchedTo, "job_new");
});

// ============================================================================
// 5. 缓存同步纯函数与失败处理及 URL 判定 (阶段 E2 返工)
// ============================================================================

test("syncChatJobStatusInTabStates: 模拟侧边栏保存成功后遍历更新已存在标签页的 chatJobStatus 缓存", () => {
  const jobId = "job_test_001";
  const tabStateMap = new Map();

  // 模拟两个标签页：tab 1 正在查看 job_test_001，tab 2 正在查看 job_test_002
  tabStateMap.set(1, {
    tabId: 1,
    chatJobStatus: formatChatJobStatus(jobId, {
      title: "岗位A",
      hr_note: "旧的HR记录",
      my_status: { status: "saved" },
    }),
  });
  tabStateMap.set(2, {
    tabId: 2,
    chatJobStatus: formatChatJobStatus("job_test_002", {
      title: "岗位B",
      hr_note: "岗位B记录",
      my_status: { status: "applied" },
    }),
  });

  // 模拟侧边栏保存成功返回的最新 jobEntry
  const updatedJobEntry = {
    title: "岗位A",
    hr_note: "HR告知提供双休餐补与班车（侧边栏最新保存）",
    my_status: { status: "saved" },
  };

  // 纯函数同步更新
  syncChatJobStatusInTabStates(tabStateMap, jobId, updatedJobEntry);

  // 模拟下一次 get_page_summary 读取 tab 1 的 chatJobStatus
  const tab1State = tabStateMap.get(1);
  assert.equal(
    tab1State.chatJobStatus.hr_note,
    "HR告知提供双休餐补与班车（侧边栏最新保存）"
  );
  assert.equal(tab1State.chatJobStatus.platform_job_id, jobId);

  // 验证不影响其它岗位且未创建多余 tabState
  const tab2State = tabStateMap.get(2);
  assert.equal(tab2State.chatJobStatus.hr_note, "岗位B记录");
  assert.equal(tabStateMap.size, 2);
});

test("formatChatJobErrorStatus: 查询失败返回独立错误状态且不在库提示为空", () => {
  const errStatus1 = formatChatJobErrorStatus("job_err_1", "jet_down");
  assert.equal(errStatus1.platform_job_id, "job_err_1");
  assert.equal(errStatus1.in_library, null);
  assert.equal(errStatus1.error, true);
  assert.equal(errStatus1.view_state, "jet_down");
  assert.equal(errStatus1.notice, undefined);

  const errStatus2 = formatChatJobErrorStatus("job_err_2", "unpaired");
  assert.equal(errStatus2.in_library, null);
  assert.equal(errStatus2.error, true);
  assert.equal(errStatus2.view_state, "unpaired");

  // 验证失败状态不应被写入缓存（依据 !status.error 判断）
  assert.equal(Boolean(!errStatus1.error), false);
});

test("isBossUrl: 校验是否仅放行以 https://www.zhipin.com/ 开头的 BOSS 直聘页面", () => {
  // 放行 BOSS 直聘页面
  assert.equal(isBossUrl("https://www.zhipin.com/web/geek/chat?id=123"), true);
  assert.equal(isBossUrl("https://www.zhipin.com/job_detail/xxx.html"), true);
  assert.equal(isBossUrl("https://www.zhipin.com/"), true);

  // 拦截非 BOSS 页面、未受信任协议及非法输入
  assert.equal(isBossUrl("http://www.zhipin.com/web/geek/chat"), false);
  assert.equal(isBossUrl("https://other.com/page"), false);
  assert.equal(isBossUrl("chrome://extensions"), false);
  assert.equal(isBossUrl(""), false);
  assert.equal(isBossUrl(null), false);
  assert.equal(isBossUrl(undefined), false);
});

test("pickCachedChatJobStatus: ID 不同返回 null、相同返回缓存、缓存为空返回 null", () => {
  const cached = {
    platform_job_id: "job_001",
    title: "前端工程师",
    in_library: true,
  };

  // 1. 相同返回缓存
  assert.equal(pickCachedChatJobStatus(cached, "job_001"), cached);
  assert.equal(pickCachedChatJobStatus(cached, " job_001 "), cached);

  // 2. ID 不同返回 null
  assert.equal(pickCachedChatJobStatus(cached, "job_002"), null);
  assert.equal(pickCachedChatJobStatus(cached, null), null);
  assert.equal(pickCachedChatJobStatus(cached, undefined), null);
  assert.equal(pickCachedChatJobStatus(cached, ""), null);

  // 3. 缓存为空返回 null
  assert.equal(pickCachedChatJobStatus(null, "job_001"), null);
  assert.equal(pickCachedChatJobStatus(undefined, "job_001"), null);
  assert.equal(pickCachedChatJobStatus({}, "job_001"), null);
  assert.equal(pickCachedChatJobStatus({ platform_job_id: null }, "job_001"), null);
  assert.equal(pickCachedChatJobStatus(null, null), null);
});

test("pickCachedChatJobStatus: 切换聊天至新岗位且查询失败时拦截旧岗位缓存", () => {
  const cachedJobA = {
    platform_job_id: "job_A",
    title: "在库岗位 A",
    in_library: true,
  };
  const currentChatJobId = "job_B";

  // 侧边栏 refreshAll 从 page_summary 拿到旧缓存 cachedJobA，但当前聊天已是 job_B
  const usableCache = pickCachedChatJobStatus(cachedJobA, currentChatJobId);
  assert.equal(usableCache, null);
});

// ============================================================================
// 6. 聊天页投递状态计算与按钮组展示纯函数 (T005, T008, US1, FR-001–FR-004)
// ============================================================================

test("extractJobStatusKey: 从对象或字符串提取状态键名", () => {
  // 对象格式 { status: "saved" }
  assert.equal(extractJobStatusKey({ status: "saved" }), "saved");
  assert.equal(extractJobStatusKey({ status: "applied" }), "applied");
  assert.equal(extractJobStatusKey({ status: "skipped" }), "skipped");

  // 纯字符串格式
  assert.equal(extractJobStatusKey("saved"), "saved");
  assert.equal(extractJobStatusKey("applied"), "applied");
  assert.equal(extractJobStatusKey("  skipped  "), "skipped");

  // 空值或无效值返回 null
  assert.equal(extractJobStatusKey(null), null);
  assert.equal(extractJobStatusKey(undefined), null);
  assert.equal(extractJobStatusKey(""), null);
  assert.equal(extractJobStatusKey("   "), null);
  assert.equal(extractJobStatusKey({ status: null }), null);
  assert.equal(extractJobStatusKey({}), null);
});

test("computeNextJobStatus: 设置、切换与取消（再点已选中的即取消为 null）(FR-002, T005)", () => {
  // 1. 设置：当前无状态（null/undefined），点击状态设为该状态
  assert.equal(computeNextJobStatus(null, "saved"), "saved");
  assert.equal(computeNextJobStatus(null, "applied"), "applied");
  assert.equal(computeNextJobStatus(null, "skipped"), "skipped");
  assert.equal(computeNextJobStatus(undefined, "saved"), "saved");

  // 2. 切换：当前有状态，点击其他状态切换为新状态
  assert.equal(computeNextJobStatus("saved", "applied"), "applied");
  assert.equal(computeNextJobStatus("saved", "skipped"), "skipped");
  assert.equal(computeNextJobStatus("applied", "saved"), "saved");
  assert.equal(computeNextJobStatus("skipped", "applied"), "applied");

  // 对象格式状态切换
  assert.equal(computeNextJobStatus({ status: "saved" }, "applied"), "applied");
  assert.equal(computeNextJobStatus({ status: "applied" }, "skipped"), "skipped");

  // 3. 取消：再次点击已选中的状态取消为 null
  assert.equal(computeNextJobStatus("saved", "saved"), null);
  assert.equal(computeNextJobStatus("applied", "applied"), null);
  assert.equal(computeNextJobStatus("skipped", "skipped"), null);
  assert.equal(computeNextJobStatus({ status: "saved" }, "saved"), null);
  assert.equal(computeNextJobStatus({ status: "applied" }, "applied"), null);
  assert.equal(computeNextJobStatus({ status: "skipped" }, "skipped"), null);

  // 4. 异常输入
  assert.equal(computeNextJobStatus("saved", "saved"), null);
  assert.equal(computeNextJobStatus("saved", "applied"), "applied");
  assert.equal(computeNextJobStatus("saved", null), "saved");
  assert.equal(computeNextJobStatus("saved", ""), "saved");
});

test("getChatJobStatusButtons: 按钮组展示数据与选中态/禁用态 (FR-001, FR-003, T005)", () => {
  // 1. 结构与常量：恰好 3 个按钮，对应收藏、已投递、不考虑
  assert.equal(CHAT_JOB_STATUS_ACTIONS.length, 3);
  assert.deepEqual(CHAT_JOB_STATUS_ACTIONS, [
    { key: "saved", label: "收藏" },
    { key: "applied", label: "已投递" },
    { key: "skipped", label: "不考虑" },
  ]);

  // 2. 未设置状态时：activeStatus 为 null，3 个按钮均未选中
  const unselectedData = getChatJobStatusButtons(null);
  assert.equal(unselectedData.activeStatus, null);
  assert.equal(unselectedData.buttons.length, 3);
  assert.deepEqual(
    unselectedData.buttons.map((b) => ({ key: b.key, active: b.active, disabled: b.disabled })),
    [
      { key: "saved", active: false, disabled: false },
      { key: "applied", active: false, disabled: false },
      { key: "skipped", active: false, disabled: false },
    ]
  );

  // 3. 状态为 applied 时：仅已投递按钮为 active
  const appliedData = getChatJobStatusButtons({ status: "applied" });
  assert.equal(appliedData.activeStatus, "applied");
  const appliedBtn = appliedData.buttons.find((b) => b.key === "applied");
  const savedBtn = appliedData.buttons.find((b) => b.key === "saved");
  const skippedBtn = appliedData.buttons.find((b) => b.key === "skipped");
  assert.equal(appliedBtn.active, true);
  assert.equal(appliedBtn.label, "已投递");
  assert.equal(savedBtn.active, false);
  assert.equal(skippedBtn.active, false);

  // 4. 保存中禁用防重复：options.saving=true 或 options.disabled=true
  const savingData = getChatJobStatusButtons("saved", { saving: true });
  assert.equal(savingData.buttons.every((b) => b.disabled === true), true);

  const disabledData = getChatJobStatusButtons("skipped", { disabled: true });
  assert.equal(disabledData.buttons.every((b) => b.disabled === true), true);
});

test("formatChatJobStatus: 完整保留与返回 my_status 状态 (T005)", () => {
  // 在库岗位带 my_status 对象
  const inLibEntry = {
    title: "前端工程师",
    company_name: "某公司",
    judgement: { verdict: "apply", verdict_label: "适合投递" },
    my_status: { status: "applied", updated_at: "2026-09-29T10:00:00Z" },
  };
  const inLibStatus = formatChatJobStatus("job_status_01", inLibEntry);
  assert.equal(inLibStatus.in_library, true);
  assert.deepEqual(inLibStatus.my_status, { status: "applied", updated_at: "2026-09-29T10:00:00Z" });

  // 不在库岗位 my_status 保持为 null
  const notInLibStatus = formatChatJobStatus("job_status_02", null);
  assert.equal(notInLibStatus.in_library, false);
  assert.equal(notInLibStatus.my_status, null);
});

test("状态保存乱序响应丢弃测试: 切换聊天后旧岗位在途响应不得写回当前岗位 (FR-004, T006, T008)", () => {
  let currentActiveJobId = "job_A";
  let displayedStatus = {
    platform_job_id: "job_A",
    my_status: null,
  };

  function onStatusSaveResponse(targetJobId, newStatus) {
    // 侧边栏回调中的防乱序检查
    if (!isChatJobResponseCurrent(targetJobId, currentActiveJobId)) {
      // 判定为乱序/过期丢弃
      return false;
    }
    displayedStatus = {
      platform_job_id: targetJobId,
      my_status: newStatus ? { status: newStatus } : null,
    };
    return true;
  }

  // 1. 用户在岗位 A 点击"已投递"，发起请求 reqA (target: job_A)
  const reqAJobId = "job_A";

  // 2. 在 reqA 返回前，用户快速切换聊天到岗位 B
  currentActiveJobId = "job_B";
  displayedStatus = {
    platform_job_id: "job_B",
    my_status: { status: "saved" },
  };

  // 3. reqA 返回结果 -> 检查 targetJobId ("job_A") 与 currentActiveJobId ("job_B")
  const appliedA = onStatusSaveResponse(reqAJobId, "applied");

  // 4. 验证：旧岗位 A 的响应被丢弃，不覆盖岗位 B 的状态
  assert.equal(appliedA, false);
  assert.equal(displayedStatus.platform_job_id, "job_B");
  assert.deepEqual(displayedStatus.my_status, { status: "saved" });

  // 5. 若用户未切换（留在岗位 B），岗位 B 的请求正常生效
  const appliedB = onStatusSaveResponse("job_B", "skipped");
  assert.equal(appliedB, true);
  assert.equal(displayedStatus.platform_job_id, "job_B");
  assert.deepEqual(displayedStatus.my_status, { status: "skipped" });
});

test("syncChatJobStatusInTabStates: 状态变更后更新标签页中 chatJobStatus 的 my_status (T007)", () => {
  const jobId = "job_switch_001";
  const tabStateMap = new Map();

  tabStateMap.set(10, {
    tabId: 10,
    chatJobStatus: formatChatJobStatus(jobId, {
      title: "资深产品经理",
      judgement: { verdict: "apply", verdict_label: "适合投递" },
      my_status: { status: "saved" },
    }),
  });

  // 初始状态为 saved
  assert.deepEqual(tabStateMap.get(10).chatJobStatus.my_status, { status: "saved" });

  // 模拟保存状态为 applied 后返回的最新 jobEntry
  const updatedEntry = {
    title: "资深产品经理",
    judgement: { verdict: "apply", verdict_label: "适合投递" },
    my_status: { status: "applied", updated_at: "2026-09-29T11:00:00Z" },
  };

  syncChatJobStatusInTabStates(tabStateMap, jobId, updatedEntry);

  // 标签页 10 的 chatJobStatus.my_status 已同步更新为 applied
  assert.deepEqual(tabStateMap.get(10).chatJobStatus.my_status, {
    status: "applied",
    updated_at: "2026-09-29T11:00:00Z",
  });

  // 模拟再次点击取消为 null
  const clearedEntry = {
    title: "资深产品经理",
    judgement: { verdict: "apply", verdict_label: "适合投递" },
    my_status: null,
  };

  syncChatJobStatusInTabStates(tabStateMap, jobId, clearedEntry);
  assert.equal(tabStateMap.get(10).chatJobStatus.my_status, null);
});
