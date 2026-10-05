import test from "node:test";
import assert from "node:assert/strict";
import { readBossChatPage, readBossChatJobId } from "../src/page-reader.js";
import { MockElement } from "./helpers/mock-element.js";

const origLocation = globalThis.location;
const origDocument = globalThis.document;
const origGetComputedStyle = globalThis.getComputedStyle;

function restoreGlobals() {
  if (origLocation === undefined) {
    delete globalThis.location;
  } else {
    globalThis.location = origLocation;
  }

  if (origDocument === undefined) {
    delete globalThis.document;
  } else {
    globalThis.document = origDocument;
  }

  if (origGetComputedStyle === undefined) {
    delete globalThis.getComputedStyle;
  } else {
    globalThis.getComputedStyle = origGetComputedStyle;
  }
}

test.afterEach(() => {
  restoreGlobals();
});


function setupChatMockEnv({
  pathname = "/web/geek/chat",
  boss = null,
  userName = "测试求职者",
  topJobTitle = "测试高级岗位（海外业务）",
  messages = [],
  includeTopEl = true,
} = {}) {
  globalThis.location = { pathname };
  globalThis.getComputedStyle = (el) => el?.style || { visibility: "visible", display: "block" };

  // Create root Vue instance with $store.state.userInfo.name
  const rootVue = {
    $options: { name: "App" },
    $store: userName !== null ? {
      state: {
        userInfo: {
          name: userName,
        },
      },
    } : null,
  };

  // Create message-list Vue component
  let messageListComp = null;
  if (boss) {
    messageListComp = {
      $options: { name: "message-list" },
      $parent: rootVue,
      $root: rootVue,
      $data: {
        boss: {
          encryptJobId: boss.encryptJobId,
          jobName: boss.jobName,
          brandName: boss.brandName,
          locationName: boss.locationName,
          name: boss.name,
        },
      },
      $store: rootVue.$store,
    };
  }

  // Build DOM elements
  const allElements = [];
  const messageElements = [];

  const body = new MockElement({ tagName: "body" });
  body.__vue__ = rootVue;
  allElements.push(body);

  let posEl = null;
  if (includeTopEl && topJobTitle !== null) {
    posEl = new MockElement({
      tagName: "div",
      className: "position-name",
      textContent: topJobTitle,
    });
    body.appendChild(posEl);
    allElements.push(posEl);
  }

  const chatContainer = new MockElement({
    tagName: "div",
    className: "chat-conversation",
  });
  body.appendChild(chatContainer);
  allElements.push(chatContainer);

  const messageListEl = new MockElement({
    tagName: "ul",
    className: "chat-message",
    vue: messageListComp,
  });
  chatContainer.appendChild(messageListEl);
  allElements.push(messageListEl);

  messages.forEach((msgSpec) => {
    const isVisible = msgSpec.visible !== false;
    const msgEl = new MockElement({
      tagName: "li",
      className: msgSpec.className || "message-item",
      textContent: msgSpec.text || "",
      visible: isVisible,
    });

    const chatMessageComp = {
      $options: { name: "ChatMessage" },
      $parent: messageListComp,
      $root: rootVue,
      $props: {
        message: {
          isSelf: msgSpec.isSelf,
          type: msgSpec.type,
          bodyType: msgSpec.bodyType,
          text: msgSpec.text,
          time: msgSpec.time || 1727000000000,
          mid: msgSpec.mid,
          quoteId: msgSpec.quoteId,
        },
      },
    };
    msgEl.__vue__ = chatMessageComp;

    messageListEl.appendChild(msgEl);
    messageElements.push(msgEl);
    allElements.push(msgEl);
  });

  globalThis.document = {
    body,
    querySelector(selector) {
      if (selector === ".position-name" || selector.includes(".position-name")) {
        return posEl;
      }
      if (selector === "#app" || selector === "body") {
        return body;
      }
      if (selector.includes("chat-message") || selector.includes("message-list")) {
        return messageListEl;
      }
      return null;
    },
    querySelectorAll(selector) {
      if (
        selector === ".chat-message .message-item, li.message-item" ||
        selector.includes("message-item")
      ) {
        return messageElements;
      }
      if (
        selector.includes("chat-message") ||
        selector.includes("message-list") ||
        selector.includes("chat-conversation") ||
        selector.includes("chat-content")
      ) {
        return [messageListEl, chatContainer];
      }
      return [];
    },
  };
}

test("readBossChatPage: identifies non-chat page and returns clear problem", () => {
  setupChatMockEnv({ pathname: "/web/geek/job" });
  const result = readBossChatPage();

  assert.equal(result.ok, false);
  assert.equal(result.page_kind, "other");
  assert.ok(result.problems.length > 0);
  assert.match(result.problems[0], /不是 BOSS 聊天页/);
  assert.equal(result.encrypt_job_id, null);
  assert.deepEqual(result.messages, []);
});

test("readBossChatPage: returns clear problem when active session data is missing", () => {
  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: null,
  });
  const result = readBossChatPage();

  assert.equal(result.ok, false);
  assert.equal(result.page_kind, "chat_page");
  assert.ok(result.problems.length > 0);
  assert.match(result.problems[0], /未找到活跃聊天会话数据/);
  assert.equal(result.encrypt_job_id, null);
  assert.deepEqual(result.messages, []);
});

test("readBossChatPage: extracts full session data and messages with correct priority", () => {
  const sampleMessages = [
    {
      className: "message-item item-myself",
      isSelf: true,
      type: 3,
      bodyType: 1,
      text: "您好，请问该职位还在招聘吗？",
      time: 1727100000000,
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 1,
      text: "在招的，方便发一份简历吗？",
      time: 1727100050000,
    },
    {
      className: "message-item item-system",
      isSelf: false,
      type: 1,
      bodyType: 12,
      text: "附件简历已发送",
      time: 1727100100000,
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 7,
      text: "我想要一份您的附件简历，您是否同意",
      time: 1727100150000,
    },
  ];

  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_777",
      jobName: "备用职位名",
      brandName: "测试科技有限公司",
      locationName: "北京",
      name: "王主管",
    },
    topJobTitle: "资深后端架构师（云原生方向）",
    userName: "张候选人",
    messages: sampleMessages,
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.page_kind, "chat_page");
  assert.equal(result.reader_version, 1);
  assert.equal(result.encrypt_job_id, "mock_job_777");
  // 页面顶部完整职位名优先于 boss.jobName
  assert.equal(result.job_title, "资深后端架构师（云原生方向）");
  assert.equal(result.company_name, "测试科技有限公司");
  assert.equal(result.location_name, "北京");
  assert.equal(result.hr_name, "王主管");
  assert.equal(result.user_name, "张候选人");
  assert.equal(result.problems.length, 0);

  // 消息按页面顺序输出
  assert.equal(result.messages.length, 4);

  // 自动招呼语 (type=3, bodyType=1, is_self=true)
  assert.equal(result.messages[0].is_self, true);
  assert.equal(result.messages[0].type, 3);
  assert.equal(result.messages[0].body_type, 1);
  assert.equal(result.messages[0].is_system, false);
  assert.equal(result.messages[0].text, "您好，请问该职位还在招聘吗？");

  // HR 消息
  assert.equal(result.messages[1].is_self, false);
  assert.equal(result.messages[1].type, 1);
  assert.equal(result.messages[1].body_type, 1);
  assert.equal(result.messages[1].is_system, false);

  // 系统消息 (class 含 item-system)
  assert.equal(result.messages[2].is_self, false);
  assert.equal(result.messages[2].body_type, 12);
  assert.equal(result.messages[2].is_system, true);

  // 请求卡片 (bodyType=7)
  assert.equal(result.messages[3].is_self, false);
  assert.equal(result.messages[3].body_type, 7);
  assert.equal(result.messages[3].is_system, false);
  assert.equal(result.messages[3].text, "我想要一份您的附件简历，您是否同意");
});

test("readBossChatPage: falls back to boss.jobName when top position-name is missing", () => {
  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_888",
      jobName: "前端开发工程师",
      brandName: "某某互联网公司",
      locationName: "上海",
      name: "李经理",
    },
    topJobTitle: null,
    includeTopEl: false,
    userName: "李候选人",
    messages: [],
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.job_title, "前端开发工程师");
  assert.equal(result.encrypt_job_id, "mock_job_888");
});

test("readBossChatPage: user_name is null when not found in $store.state.userInfo", () => {
  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_999",
      jobName: "测试经理",
      brandName: "创新科技",
      locationName: "杭州",
      name: "赵总",
    },
    topJobTitle: "测试经理",
    userName: null, // 读不到用户名
    messages: [],
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.user_name, null);
  assert.equal(result.encrypt_job_id, "mock_job_999");
});

test("readBossChatPage: ignores invisible message items from hidden chat sessions", () => {
  const sampleMessages = [
    {
      className: "message-item item-myself",
      isSelf: true,
      type: 1,
      bodyType: 1,
      text: "这是可见会话消息 1",
      visible: true,
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 1,
      text: "这是已被隐藏的旧会话消息",
      visible: false, // 模拟切走后藏起来的会话
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 1,
      text: "这是可见会话消息 2",
      visible: true,
    },
  ];

  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_visible",
      jobName: "产品总监",
      brandName: "未来科技",
      locationName: "广州",
      name: "陈女士",
    },
    messages: sampleMessages,
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.messages.length, 2);
  assert.equal(result.messages[0].text, "这是可见会话消息 1");
  assert.equal(result.messages[1].text, "这是可见会话消息 2");
});

test("readBossChatPage: returns all loaded visible messages in order without plugin-side truncation", () => {
  // 构造 35 条可见消息，验证插件端不截断，原样交给服务端做过滤与截取
  const thirtyFiveMessages = [];
  for (let i = 1; i <= 35; i++) {
    thirtyFiveMessages.push({
      className: i % 2 === 0 ? "message-item item-friend" : "message-item item-myself",
      isSelf: i % 2 !== 0,
      type: 1,
      bodyType: 1,
      text: `测试消息序号 ${i}`,
      visible: true,
    });
  }

  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_35",
      jobName: "数据分析师",
      brandName: "数智科技",
      locationName: "深圳",
      name: "孙主管",
    },
    messages: thirtyFiveMessages,
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.messages.length, 35);
  assert.equal(result.messages[0].text, "测试消息序号 1");
  assert.equal(result.messages[34].text, "测试消息序号 35");
});

test("readBossChatJobId: lightweight extraction of encryptJobId without message processing", () => {
  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_lightweight_123",
      jobName: "安全工程师",
      brandName: "安天网络",
      locationName: "武汉",
      name: "钱主管",
    },
    messages: [
      {
        className: "message-item item-myself",
        isSelf: true,
        type: 1,
        bodyType: 1,
        text: "消息不会被处理",
      },
    ],
  });

  const result = readBossChatJobId();

  assert.equal(result.ok, true);
  assert.equal(result.encrypt_job_id, "mock_job_lightweight_123");
  assert.equal(result.problems.length, 0);
});

test("readBossChatJobId: returns ok=false when session data is missing", () => {
  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: null,
  });

  const result = readBossChatJobId();

  assert.equal(result.ok, false);
  assert.equal(result.encrypt_job_id, null);
  assert.ok(result.problems.length > 0);
});

test("readBossChatPage: extracts mid and quote_id when present as numbers, defaults to null", () => {
  const sampleMessages = [
    {
      className: "message-item item-myself",
      isSelf: true,
      type: 1,
      bodyType: 1,
      text: "请问贵公司还在招聘吗？",
      mid: 1001,
      quoteId: undefined,
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 1,
      text: "在招的，这是引用回复",
      mid: 1002,
      quoteId: 1001,
    },
    {
      className: "message-item item-friend",
      isSelf: false,
      type: 1,
      bodyType: 1,
      text: "没有 mid 与 quoteId 的消息",
      mid: "not-a-number",
      quoteId: "invalid",
    },
  ];

  setupChatMockEnv({
    pathname: "/web/geek/chat",
    boss: {
      encryptJobId: "mock_job_quote",
      jobName: "测试岗位",
      brandName: "测试公司",
      locationName: "北京",
      name: "王主管",
    },
    messages: sampleMessages,
  });

  const result = readBossChatPage();

  assert.equal(result.ok, true);
  assert.equal(result.messages.length, 3);
  assert.equal(result.messages[0].mid, 1001);
  assert.equal(result.messages[0].quote_id, null);
  assert.equal(result.messages[1].mid, 1002);
  assert.equal(result.messages[1].quote_id, 1001);
  assert.equal(result.messages[2].mid, null);
  assert.equal(result.messages[2].quote_id, null);
});
