import test from "node:test";
import assert from "node:assert/strict";

import {
  isJobInLibrary,
  formatChatJobStatus,
  IN_LIBRARY_NO_JUDGEMENT_NOTICE,
  shouldIngestChatJob,
} from "../src/chat-view.js";
import { readBossChatJobId } from "../src/page-reader.js";
import { MockElement } from "./helpers/mock-element.js";

// ============================================================================
// 1. shouldIngestChatJob 纯函数测试 (T027 / US4)
// ============================================================================

test("shouldIngestChatJob: 已配对且读到岗位 ID 与非空职位名时返回 true", () => {
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "job_12345",
      title: "大模型算法工程师",
    }),
    true
  );

  // 职位名两端带空白时 trim 后非空 -> true
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "job_12345",
      title: "   数据分析师   ",
    }),
    true
  );
});

test("shouldIngestChatJob: 未配对时不入库 (返回 false)", () => {
  assert.equal(
    shouldIngestChatJob({
      isPaired: false,
      jobId: "job_12345",
      title: "大模型算法工程师",
    }),
    false
  );
});

test("shouldIngestChatJob: 缺少岗位 ID 或职位名时不入库 (返回 false)", () => {
  // 缺少 jobId
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "",
      title: "大模型算法工程师",
    }),
    false
  );
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: null,
      title: "大模型算法工程师",
    }),
    false
  );

  // 缺少 title 或 title 纯空白
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "job_12345",
      title: "",
    }),
    false
  );
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "job_12345",
      title: "    \t \n  ",
    }),
    false
  );
  assert.equal(
    shouldIngestChatJob({
      isPaired: true,
      jobId: "job_12345",
      title: null,
    }),
    false
  );
});

// ============================================================================
// 2. isJobInLibrary 的 seen_in_chat 判定 (T027 / FR-014, US4)
// ============================================================================

test("isJobInLibrary: seen_in_chat 为 true 时判定为在库", () => {
  const chatSeenOnlyJob = {
    completeness: "list_only",
    title: "聊天中遇到岗位",
    company_name: "某科技公司",
    judgement: null,
    my_status: null,
    hr_note: null,
    seen_in_chat: true,
  };

  assert.equal(isJobInLibrary(chatSeenOnlyJob), true);
});

test("isJobInLibrary: seen_in_chat 为 false 且无状态、无判断、无备注时判定为不在库", () => {
  const notSeenJob = {
    completeness: "list_only",
    title: "搜索列表中偶遇岗位",
    company_name: "某科技公司",
    judgement: null,
    my_status: null,
    hr_note: null,
    seen_in_chat: false,
  };

  assert.equal(isJobInLibrary(notSeenJob), false);
});

// ============================================================================
// 3. 在库但无判断时的 formatChatJobStatus 提示原文 (T027 / FR-015, US4)
// ============================================================================

test("formatChatJobStatus: 在库但无判断时 notice 为原文「点「查看职位」获取详情并判断」", () => {
  const chatSeenJob = {
    title: "高级测试开发专家",
    company_name: "深蓝动力科技",
    judgement: null,
    my_status: null,
    hr_note: null,
    seen_in_chat: true,
  };

  const status = formatChatJobStatus("job_chat_unjudged", chatSeenJob);

  assert.equal(status.platform_job_id, "job_chat_unjudged");
  assert.equal(status.in_library, true);
  assert.equal(status.title, "高级测试开发专家");
  assert.equal(status.company_name, "深蓝动力科技");
  assert.equal(status.verdict_label, null);
  assert.equal(status.notice, IN_LIBRARY_NO_JUDGEMENT_NOTICE);
  assert.equal(status.notice, "点「查看职位」获取详情并判断");
});

test("formatChatJobStatus: 在库且有判断时 notice 为 null", () => {
  const judgedJob = {
    title: "资深产品专家",
    company_name: "未来数智",
    judgement: {
      status: "done",
      verdict: "apply",
      verdict_label: "适合投递",
    },
    my_status: null,
    hr_note: null,
    seen_in_chat: true,
  };

  const status = formatChatJobStatus("job_chat_judged", judgedJob);

  assert.equal(status.in_library, true);
  assert.equal(status.verdict_label, "适合投递");
  assert.equal(status.notice, null);
});

// ============================================================================
// 4. 轻量读取 readBossChatJobId 返回职位名与公司名 (MockElement) (T022, T027)
// ============================================================================

const origLocation = globalThis.location;
const origDocument = globalThis.document;
const origGetComputedStyle = globalThis.getComputedStyle;

function restoreGlobals() {
  if (origLocation === undefined) delete globalThis.location;
  else globalThis.location = origLocation;

  if (origDocument === undefined) delete globalThis.document;
  else globalThis.document = origDocument;

  if (origGetComputedStyle === undefined) delete globalThis.getComputedStyle;
  else globalThis.getComputedStyle = origGetComputedStyle;
}

test.afterEach(() => {
  restoreGlobals();
});


function setupMockChatEnvironment({
  pathname = "/web/geek/chat",
  boss = null,
  domJobTitle = null,
} = {}) {
  globalThis.location = { pathname };
  globalThis.getComputedStyle = (el) => el?.style || { visibility: "visible", display: "block" };

  let messageListComp = null;
  if (boss) {
    messageListComp = {
      $options: { name: "message-list" },
      $parent: null,
      $data: {
        boss: {
          encryptJobId: boss.encryptJobId,
          jobName: boss.jobName,
          brandName: boss.brandName,
        },
      },
    };
  }

  const container = new MockElement({
    tagName: "div",
    className: "chat-conversation",
    vue: messageListComp,
  });

  const msgItem = new MockElement({
    tagName: "li",
    className: "message-item",
    vue: messageListComp,
  });
  container.appendChild(msgItem);

  let topTitleEl = null;
  if (domJobTitle) {
    topTitleEl = new MockElement({
      tagName: "span",
      className: "position-name",
      textContent: domJobTitle,
    });
  }

  globalThis.document = {
    querySelector: (selector) => {
      if (domJobTitle && selector === ".chat-position-content .position-name") {
        return topTitleEl;
      }
      return null;
    },
    querySelectorAll: (selector) => {
      if (selector.includes("message-item")) {
        return [msgItem];
      }
      if (selector.includes("chat-conversation") || selector.includes("chat-message")) {
        return [container];
      }
      return [];
    },
  };
}

test("readBossChatJobId: 成功提取岗位 ID、职位名与公司名（优先取 boss.jobName）", () => {
  setupMockChatEnvironment({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "chat_encrypt_job_001",
      jobName: "数据开发工程师",
      brandName: "前沿智能科技有限公司",
    },
    domJobTitle: null,
  });

  const result = readBossChatJobId();

  assert.equal(result.ok, true);
  assert.equal(result.encrypt_job_id, "chat_encrypt_job_001");
  assert.equal(result.job_title, "数据开发工程师");
  assert.equal(result.company_name, "前沿智能科技有限公司");
  assert.equal(result.problems.length, 0);
});

test("readBossChatJobId: boss.jobName 为空时回退为顶部栏文字", () => {
  setupMockChatEnvironment({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "chat_encrypt_job_002",
      jobName: "",
      brandName: "极客网络",
    },
    domJobTitle: "顶部完整职位名（深度学习方向）",
  });

  const result = readBossChatJobId();

  assert.equal(result.ok, true);
  assert.equal(result.encrypt_job_id, "chat_encrypt_job_002");
  assert.equal(result.job_title, "顶部完整职位名（深度学习方向）");
  assert.equal(result.company_name, "极客网络");
});

test("readBossChatJobId: boss.jobName 与顶部栏文字不同时取 boss.jobName", () => {
  setupMockChatEnvironment({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "chat_encrypt_job_003",
      jobName: "当前会话真实职位名",
      brandName: "前沿智能科技有限公司",
    },
    domJobTitle: "上一会话残留顶部职位名",
  });

  const result = readBossChatJobId();

  assert.equal(result.ok, true);
  assert.equal(result.encrypt_job_id, "chat_encrypt_job_003");
  assert.equal(result.job_title, "当前会话真实职位名");
  assert.equal(result.company_name, "前沿智能科技有限公司");
});
