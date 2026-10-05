import test from "node:test";
import assert from "node:assert/strict";
import { setupSidepanelEnv } from "./helpers/sidepanel-env.js";
import { CONSENT_MODAL_DESC, NO_JET_JUDGEMENT_NOTICE } from "../src/chat-view.js";

test("流程一：切换模式 - 点击「切换为开场白」依次发送 chat_preview 与 chat_generate 且 force_mode 为 opening，默认生成不带 force_mode", async () => {
  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_mode_test_001",
          company_name: "未来科技",
          job_title: "全栈工程师",
          messages: [{ sender: "HR", is_self: false, text: "您好，在招的", time: 100 }],
        },
      }),
      chat_preview: (msg) => ({
        ok: true,
        data: {
          has_consent: true,
          prompt_hash: "hash_reply_mode",
          mode: "reply",
          mode_label: "建议回复",
          mode_basis: "HR刚才打了个招呼",
          has_jet_judgement: true,
        },
      }),
      chat_generate: (msg) => ({
        ok: true,
        data: {
          mode: msg.payload?.force_mode || "reply",
          mode_label: msg.payload?.force_mode === "opening" ? "开场白" : "建议回复",
          suggestions: [{ version: 1, text: "您好，方便进一步沟通吗？" }],
          questions: [],
          quota_remaining: 48,
        },
      }),
    },
  });

  try {
    await env.loadSidepanel();

    // 1. 默认点击「生成沟通建议」
    const generateBtn = env.document.getElementById("btn-generate-chat");
    assert.ok(generateBtn, "界面应存在生成按钮");

    env.clearMessages();
    generateBtn.click();
    await env.flushAsync(10);

    const defaultPreview = env.sentMessages.find((m) => m.type === "chat_preview");
    const defaultGenerate = env.sentMessages.find((m) => m.type === "chat_generate");

    assert.ok(defaultPreview, "默认点击应发送 chat_preview");
    assert.ok(defaultGenerate, "默认点击应发送 chat_generate");
    assert.equal(defaultPreview.payload?.force_mode, undefined, "默认 chat_preview 不带 force_mode");
    assert.equal(defaultGenerate.payload?.force_mode, null, "默认 chat_generate 不带 force_mode");

    // 2. 检查生成的 DOM 中是否出现「切换为开场白」按钮
    const switchBtn = env.document.getElementById("btn-switch-mode");
    assert.ok(switchBtn, "生成建议回复后应出现切换模式按钮");
    assert.equal(switchBtn.textContent.trim(), "切换为开场白");

    // 3. 点击「切换为开场白」
    env.clearMessages();
    switchBtn.click();
    await env.flushAsync(10);

    const switchPreview = env.sentMessages.find((m) => m.type === "chat_preview");
    const switchGenerate = env.sentMessages.find((m) => m.type === "chat_generate");

    assert.ok(switchPreview, "切换模式应发送 chat_preview");
    assert.ok(switchGenerate, "切换模式应发送 chat_generate");

    const pIdx = env.sentMessages.indexOf(switchPreview);
    const gIdx = env.sentMessages.indexOf(switchGenerate);
    assert.ok(pIdx < gIdx, "chat_preview 必须在 chat_generate 之前依次发送");

    assert.equal(switchPreview.payload?.force_mode, "opening", "chat_preview 的 force_mode 应为 opening");
    assert.equal(switchGenerate.payload?.force_mode, "opening", "chat_generate 的 force_mode 应为 opening");
    assert.equal(
      switchPreview.payload?.force_mode,
      switchGenerate.payload?.force_mode,
      "两者的 force_mode 必须相同且均为 opening"
    );
  } finally {
    env.cleanup();
  }
});

test("流程二：新消息 - 最后一条为 HR 消息时触发 chat_generate，系统消息或自己发送则不触发", async () => {
  const env = await setupSidepanelEnv({
    storage: { autoGenerate: true },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_msg_flow_002",
          company_name: "创新科技",
          job_title: "前端专家",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 }],
        },
      }),
    },
  });

  try {
    const sidepanel = await env.loadSidepanel();
    // 首次打开侧边栏已就绪并完成初次生成
    await env.flushAsync(10);

    // Case A: 最新一条是 HR 发送的消息且开启自动生成 -> handleChatMessagesChanged 触发 chat_generate
    env.clearMessages();
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_msg_flow_002",
        company_name: "创新科技",
        job_title: "前端专家",
        messages: [
          { sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 },
          { sender: "HR", is_self: false, text: "请问方便发送简历吗？", time: 200, body_type: 1 },
        ],
      },
    }));

    await sidepanel.handleChatMessagesChanged(101);
    await env.flushAsync(10);

    const hrGenerate = env.sentMessages.find((m) => m.type === "chat_generate");
    assert.ok(hrGenerate, "HR 发来新消息时应触发 chat_generate");

    // Case B: 最新一条是系统提示 (sender 为 '系统' 或 is_system 为真) -> 不触发 chat_generate
    env.clearMessages();
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_msg_flow_002",
        company_name: "创新科技",
        job_title: "前端专家",
        messages: [
          { sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 },
          { sender: "HR", is_self: false, text: "请问方便发送简历吗？", time: 200, body_type: 1 },
          { sender: "系统", is_system: true, text: "对方开启了隐私保护", time: 300, body_type: 1 },
        ],
      },
    }));

    await sidepanel.handleChatMessagesChanged(101);
    await env.flushAsync(10);

    const sysGenerate = env.sentMessages.find((m) => m.type === "chat_generate");
    assert.equal(sysGenerate, undefined, "系统消息到达时不得触发 chat_generate");

    // Case C: 最新一条是自己发送的 (sender 为 '我' 或 is_self 为真) -> 不触发 chat_generate
    env.clearMessages();
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_msg_flow_002",
        company_name: "创新科技",
        job_title: "前端专家",
        messages: [
          { sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 },
          { sender: "HR", is_self: false, text: "请问方便发送简历吗？", time: 200, body_type: 1 },
          { sender: "我", is_self: true, text: "好的，这就发您", time: 400, body_type: 1 },
        ],
      },
    }));

    await sidepanel.handleChatMessagesChanged(101);
    await env.flushAsync(10);

    const selfGenerate = env.sentMessages.find((m) => m.type === "chat_generate");
    assert.equal(selfGenerate, undefined, "自己发送的消息到达时不得触发 chat_generate");
  } finally {
    env.cleanup();
  }
});

test("流程三：知情同意 - preview 返回 has_consent=false 时弹出同意框且不生成，点击同意后依次发送 chat_consent 和 chat_generate", async () => {
  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_consent_flow_003",
          company_name: "数据智能",
          job_title: "算法工程师",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100 }],
        },
      }),
      chat_preview: () => ({
        ok: true,
        data: {
          has_consent: false,
          prompt_hash: "hash_consent_needed_999",
          sanitized_prompt: "脱敏提示词预览文本",
          mode: "reply",
          mode_label: "建议回复",
          fields: ["职位名、公司、城市（不发岗位 ID）"],
        },
      }),
      chat_consent: () => ({
        ok: true,
      }),
      chat_generate: (msg) => ({
        ok: true,
        data: {
          mode: "reply",
          mode_label: "建议回复",
          suggestions: [{ version: 1, text: "算法岗位回复建议" }],
          questions: [],
          quota_remaining: 47,
        },
      }),
    },
  });

  try {
    await env.loadSidepanel();

    // 1. 手动点击生成
    const generateBtn = env.document.getElementById("btn-generate-chat");
    env.clearMessages();
    generateBtn.click();
    await env.flushAsync(10);

    // 2. 断言弹窗显示且未发 chat_generate
    const consentModal = env.document.getElementById("chat-consent-modal");
    assert.ok(consentModal, "页面应包含知情同意弹窗元素");
    assert.equal(consentModal.style.display, "block", "未同意时弹窗应处于显示状态");

    const genCalls = env.sentMessages.filter((m) => m.type === "chat_generate");
    assert.equal(genCalls.length, 0, "用户未同意前严禁发送 chat_generate");

    // 3. 断言提示文案与 CONSENT_MODAL_DESC 一致
    const noticeEl = env.document.getElementById("consent-auto-send-notice");
    assert.ok(noticeEl, "弹窗内应有自动发送提示元素");
    assert.equal(
      noticeEl.textContent.trim(),
      CONSENT_MODAL_DESC.trim(),
      "弹窗内的自动发送说明文案必须等于 CONSENT_MODAL_DESC"
    );

    // 4. 点击「同意并继续生成」
    const agreeBtn = env.document.getElementById("btn-consent-agree");
    assert.ok(agreeBtn, "页面应存在同意按钮");

    env.clearMessages();
    agreeBtn.click();
    await env.flushAsync(10);

    // 5. 断言依次发送 chat_consent 与 chat_generate
    const consentMsg = env.sentMessages.find((m) => m.type === "chat_consent");
    const generateMsg = env.sentMessages.find((m) => m.type === "chat_generate");

    assert.ok(consentMsg, "点击同意后必须发送 chat_consent");
    assert.ok(generateMsg, "同意后必须继续发送 chat_generate");

    const cIdx = env.sentMessages.indexOf(consentMsg);
    const gIdx = env.sentMessages.indexOf(generateMsg);
    assert.ok(cIdx < gIdx, "chat_consent 必须在 chat_generate 之前发送");
    assert.equal(generateMsg.payload?.prompt_hash, "hash_consent_needed_999", "应携带 preview 返回的 prompt_hash");
    assert.equal(consentModal.style.display, "none", "同意后弹窗应隐藏");
  } finally {
    env.cleanup();
  }
});

test("流程四：复制核对 - 岗位 ID 不一致时拒绝复制并显示提示，岗位 ID 一致时正常调用 navigator.clipboard.writeText", async () => {
  let currentActiveChatJobId = "job_bound_original";

  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_bound_original",
          company_name: "安全科技",
          job_title: "安全研究员",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100 }],
        },
      }),
      read_chat_job_id: () => ({
        ok: true,
        encrypt_job_id: currentActiveChatJobId,
      }),
      chat_preview: () => ({
        ok: true,
        data: {
          has_consent: true,
          prompt_hash: "hash_copy_test",
          mode: "reply",
          mode_label: "建议回复",
          has_jet_judgement: true,
        },
      }),
      chat_generate: () => ({
        ok: true,
        data: {
          mode: "reply",
          mode_label: "建议回复",
          suggestions: [{ version: 1, text: "您好，我擅长漏洞挖掘与逆向分析。" }],
          questions: [],
          quota_remaining: 46,
        },
      }),
    },
  });

  try {
    await env.loadSidepanel();

    // 1. 生成话术建议并渲染
    env.document.getElementById("btn-generate-chat").click();
    await env.flushAsync(10);

    const copyBtn = env.document.querySelector(".btn-copy-suggestion");
    assert.ok(copyBtn, "生成成功后应渲染复制按钮");

    // 2. 模拟用户在 BOSS 页面切换了聊天，当前读到的岗位 ID 变为另一个
    currentActiveChatJobId = "job_switched_different";

    // 3. 点击复制按钮
    copyBtn.click();
    await env.flushAsync(10);

    // 4. 断言剪贴板未被调用，并提示「已切换到新的聊天，请重新生成」
    assert.equal(env.clipboardCalls.length, 0, "岗位 ID 不一致时严禁调用 clipboard.writeText");

    const statusMsg = env.document.getElementById("chat-status-message");
    assert.ok(statusMsg, "应存在状态提示元素");
    assert.equal(statusMsg.style.display, "block", "提示元素应展示");
    assert.ok(
      statusMsg.textContent.includes("已切换到新的聊天，请重新生成"),
      `提示文字应包含「已切换到新的聊天，请重新生成」，实际为: ${statusMsg.textContent}`
    );

    // 5. 模拟岗位 ID 一致时的正常复制
    currentActiveChatJobId = "job_consistent_target";
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_consistent_target",
        company_name: "安全科技",
        job_title: "安全研究员",
        messages: [{ sender: "HR", is_self: false, text: "您好", time: 100 }],
      },
    }));

    // 重新生成绑定到新岗位
    env.document.getElementById("btn-generate-chat").click();
    await env.flushAsync(10);

    const newCopyBtn = env.document.querySelector(".btn-copy-suggestion");
    assert.ok(newCopyBtn, "新岗位建议复制按钮应渲染");

    newCopyBtn.click();
    await env.flushAsync(10);

    assert.equal(env.clipboardCalls.length, 1, "岗位 ID 一致时应成功调用 clipboard.writeText 一次");
    assert.equal(env.clipboardCalls[0], "您好，我擅长漏洞挖掘与逆向分析。");
    assert.equal(newCopyBtn.textContent, "已复制", "复制成功后按钮文字变为「已复制」");
  } finally {
    env.cleanup();
  }
});

test("额外流程：未回答事实 - 生成结果带 unanswered_facts 时，提示文字包含「请把话术里的【填写：所在城市】替换成实际情况」", async () => {
  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_unanswered_fact_005",
          company_name: "地理信息科技",
          job_title: "GIS 工程师",
          messages: [{ sender: "HR", is_self: false, text: "请问目前在哪个城市？", time: 100 }],
        },
      }),
      chat_generate: () => ({
        ok: true,
        data: {
          mode: "reply",
          mode_label: "建议回复",
          suggestions: [{ version: 1, text: "您好，我目前在【填写：所在城市】，随时可面试。" }],
          questions: [],
          unanswered_facts: ["所在城市"],
          quota_remaining: 45,
        },
      }),
    },
  });

  try {
    await env.loadSidepanel();

    env.document.getElementById("btn-generate-chat").click();
    await env.flushAsync(10);

    const factContainer = env.document.getElementById("chat-unanswered-facts");
    assert.ok(factContainer, "结果区应渲染未回答事实容器");

    const factNotice = factContainer.querySelector(".chat-unanswered-fact-notice");
    assert.ok(factNotice, "容器内应有未回答事实提示项");
    assert.ok(
      factNotice.textContent.includes("请把话术里的【填写：所在城市】替换成实际情况"),
      `提示文字应包含「请把话术里的【填写：所在城市】替换成实际情况」，实际为: ${factNotice.textContent}`
    );
  } finally {
    env.cleanup();
  }
});

// ============================================================================
// 任务三改写测试：替换原先使用假变量/假计数器的测试，使用真实侧边栏环境与函数验证
// ============================================================================

test("T070 改写 (FR-036): 侧边栏内存缓存生命周期与隔离 - 内存缓存仅存在于当前侧边栏实例，重新打开侧边栏时缓存清空不串用", async () => {
  // 实例 1：生成并缓存建议
  const env1 = await setupSidepanelEnv({
    storage: { autoGenerate: true },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_cache_isolate_001",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 }],
        },
      }),
    },
  });

  try {
    const sidepanel1 = await env1.loadSidepanel();
    await env1.flushAsync(10);

    // 实例 1 中初次生成
    const genCount1 = env1.sentMessages.filter((m) => m.type === "chat_generate").length;
    assert.equal(genCount1, 1, "实例1首次应触发生成");

    // 再次切回该聊天（消息无变化） -> 命中内存缓存，不再调用 chat_generate
    env1.clearMessages();
    await sidepanel1.handleChatSwitched("job_cache_isolate_001");
    await env1.flushAsync(10);

    const genCallsAfter = env1.sentMessages.filter((m) => m.type === "chat_generate").length;
    assert.equal(genCallsAfter, 0, "同一实例内切回命中内存缓存，严禁重新调用 chat_generate");
  } finally {
    env1.cleanup();
  }

  // 实例 2：模拟关闭后重新打开侧边栏（全新的侧边栏模块实例与环境）
  const env2 = await setupSidepanelEnv({
    storage: { autoGenerate: true },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_cache_isolate_001",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100, body_type: 1 }],
        },
      }),
    },
  });

  try {
    await env2.loadSidepanel();
    await env2.flushAsync(10);

    // 重新打开的实例中内存缓存自然清空，必须触发全新生成
    const genCount2 = env2.sentMessages.filter((m) => m.type === "chat_generate").length;
    assert.equal(genCount2, 1, "重新打开侧边栏后内存缓存已自然清空，应触发全新生成");
  } finally {
    env2.cleanup();
  }
});

test("T070 改写 (并发防重与快速连续切换): 生成中防重复发起，切换岗位后迟到响应不渲染到 UI", async () => {
  let resolveGenerate = null;

  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_concurrency_A",
          messages: [{ sender: "HR", is_self: false, text: "您好", time: 100 }],
        },
      }),
      chat_generate: () =>
        new Promise((resolve) => {
          resolveGenerate = resolve;
        }),
    },
  });

  try {
    const sidepanel = await env.loadSidepanel();

    // 1. 发起岗位 A 的生成
    env.clearMessages();
    env.document.getElementById("btn-generate-chat").click();
    await env.flushAsync(5);

    assert.equal(env.sentMessages.filter((m) => m.type === "chat_generate").length, 1);

    // 2. 并发防重：请求尚未返回前再次点击生成或触发新消息，不重复发起 chat_generate
    env.document.getElementById("btn-generate-chat").click();
    await sidepanel.handleChatMessagesChanged(101);
    await env.flushAsync(5);

    assert.equal(env.sentMessages.filter((m) => m.type === "chat_generate").length, 1, "生成中应拦截重复请求");

    // 3. 快速连续切换：用户在岗位 A 响应返回前切到了岗位 B
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_concurrency_B",
        messages: [{ sender: "HR", is_self: false, text: "你好B", time: 200 }],
      },
    }));

    await sidepanel.handleChatSwitched("job_concurrency_B");
    await env.flushAsync(5);

    // 4. 岗位 A 的迟到响应到达
    if (resolveGenerate) {
      resolveGenerate({
        ok: true,
        data: {
          mode: "reply",
          suggestions: [{ version: 1, text: "岗位A的话术" }],
          questions: [],
          quota_remaining: 40,
        },
      });
    }
    await env.flushAsync(10);

    // 5. 校验：岗位 A 的结果绝不渲染到当前（岗位 B）的 UI 上
    const resultContainer = env.document.getElementById("chat-result-container");
    const renderedText = resultContainer?.textContent || "";
    assert.ok(!renderedText.includes("岗位A的话术"), "已离开岗位 A，迟到响应绝不得渲染到界面");
  } finally {
    env.cleanup();
  }
});

test("类别2(a)/2(b) 改写: 存在缓存建议时切回会话，只请求 preview 刷新并隐藏无判断提示，不调用 chat_generate", async () => {
  let serverJudged = false;

  const env = await setupSidepanelEnv({
    storage: { autoGenerate: true },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_rejudge_refresh_001",
          company_name: "创新科技",
          job_title: "AI 架构师",
          messages: [{ sender: "HR", is_self: false, text: "您好，在招", time: 100 }],
        },
      }),
      chat_preview: () => ({
        ok: true,
        data: {
          has_consent: true,
          prompt_hash: "hash_rejudge_001",
          mode: "reply",
          mode_label: "建议回复",
          has_jet_judgement: serverJudged, // 服务端是否已完成判断
        },
      }),
      chat_generate: () => ({
        ok: true,
        data: {
          mode: "reply",
          mode_label: "建议回复",
          has_jet_judgement: serverJudged,
          suggestions: [{ version: 1, text: "初次生成的建议话术" }],
          questions: ["团队规模多大？"],
          quota_remaining: 45,
        },
      }),
    },
  });

  try {
    const sidepanel = await env.loadSidepanel();
    await env.flushAsync(10);

    // 初始状态下服务端未判断，提示「这个岗位没有 Jet 判断」应显示
    const noticeEl = env.document.getElementById("chat-no-judgement-notice");
    assert.ok(noticeEl);
    assert.equal(noticeEl.style.display, "block", "初始未判断时应展示提示");
    assert.equal(noticeEl.textContent.trim(), NO_JET_JUDGEMENT_NOTICE);

    // 随后服务端完成判断：has_jet_judgement 变为 true
    serverJudged = true;

    // 用户切回同一会话
    env.clearMessages();
    await sidepanel.handleChatSwitched("job_rejudge_refresh_001");
    await env.flushAsync(10);

    // 断言：重新发起了 preview，但绝不调用 chat_generate
    const previewCall = env.sentMessages.find((m) => m.type === "chat_preview");
    const generateCall = env.sentMessages.find((m) => m.type === "chat_generate");

    assert.ok(previewCall, "切回存在缓存会话必须重新发送 chat_preview 刷新状态");
    assert.equal(generateCall, undefined, "命中缓存时严禁调用 chat_generate 重新生成");

    // 断言：无判断提示被隐藏，话术完整保留
    assert.equal(noticeEl.style.display, "none", "服务端有判断后提示必须隐藏");
    const resultText = env.document.getElementById("chat-result-container").textContent;
    assert.ok(resultText.includes("初次生成的建议话术"), "原建议话术必须完整保留");
  } finally {
    env.cleanup();
  }
});

test("auto-generate-settings 改写: storage.local.get 读取异常时安全降级默认开启自动生成", async () => {
  const env = await setupSidepanelEnv({
    storageGet: () => {
      throw new Error("chrome.storage.local disk failure");
    },
    handlers: {
      read_chat_page: () => ({
        ok: true,
        data: {
          encrypt_job_id: "job_storage_err_001",
          messages: [{ sender: "HR", is_self: false, text: "有新机会", time: 100, body_type: 1 }],
        },
      }),
    },
  });

  try {
    const sidepanel = await env.loadSidepanel();
    await env.flushAsync(10);

    // 模拟新消息到达
    env.clearMessages();
    env.setHandler("read_chat_page", () => ({
      ok: true,
      data: {
        encrypt_job_id: "job_storage_err_001",
        messages: [
          { sender: "HR", is_self: false, text: "有新机会", time: 100, body_type: 1 },
          { sender: "HR", is_self: false, text: "方便发简历吗？", time: 200, body_type: 1 },
        ],
      },
    }));

    await sidepanel.handleChatMessagesChanged(101);
    await env.flushAsync(10);

    // 虽然 storage 抛错，但 getAutoGenerateSetting 安全捕获并回退 true，依然正常生成
    const genCall = env.sentMessages.find((m) => m.type === "chat_generate");
    assert.ok(genCall, "storage 读取异常时应安全降级为默认开启自动生成 (true)");
  } finally {
    env.cleanup();
  }
});

test("T052 (c) 改写 (FR-062): 岗位库只在用户点击时打开，侧边栏初始化时不自动打开", async () => {
  const env = await setupSidepanelEnv({
    storage: { autoGenerate: false },
  });

  try {
    await env.loadSidepanel();

    // 1. 初始化完成后，tabs.create 不得被自动调用
    assert.equal(env.tabCreateCalls.length, 0, "未经用户点击，不得自动调用 tabs.create 打开岗位库");

    // 2. 用户点击「我的岗位库」按钮
    const myJobsBtn = env.document.getElementById("btn-open-myjobs");
    assert.ok(myJobsBtn, "页面应存在「我的岗位库」按钮");

    myJobsBtn.click();
    await env.flushAsync(5);

    // 3. 点击后 tabs.create 被调用一次，且目标 URL 为 src/myjobs.html
    assert.equal(env.tabCreateCalls.length, 1, "用户点击后应调用 tabs.create 一次");
    assert.equal(
      env.tabCreateCalls[0]?.url,
      "chrome-extension://mock-sidepanel-id/src/myjobs.html",
      "打开的 URL 应为 src/myjobs.html"
    );
  } finally {
    env.cleanup();
  }
});
