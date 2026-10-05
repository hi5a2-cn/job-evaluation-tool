import test from "node:test";
import assert from "node:assert/strict";
import { isLastMessageFromHr, decideNewMessageAction } from "../src/chat-view.js";

test("isLastMessageFromHr: 最后一条是系统提示 (sender: '系统') 返回 false 且不触发 generate", () => {
  const messages = [
    { sender: "我", text: "您好，请问岗位还招人吗？", is_self: true },
    { sender: "HR", text: "在招的，发份简历看看。", is_self: false },
    { sender: "系统", text: "对方已查看了您的附件简历" },
  ];

  const lastIsHr = isLastMessageFromHr(messages);
  assert.equal(lastIsHr, false, "sender 为系统的消息不得归为 HR");

  const action = decideNewMessageAction({
    autoGenerate: true,
    lastIsHr,
    quotaExhaustedToday: false,
  });
  assert.notEqual(action, "generate", "系统提示到达时不应返回 generate");
  assert.equal(action, "update_status");
});

test("isLastMessageFromHr: 最后一条是系统提示 (is_system: true) 返回 false 且不触发 generate", () => {
  const messages = [
    { sender: "我", text: "您好！", is_self: true },
    { sender: "HR", text: "您好，方便发份简历吗？", is_self: false },
    { text: "对方已查看了您的附件简历", is_system: true },
  ];

  const lastIsHr = isLastMessageFromHr(messages);
  assert.equal(lastIsHr, false, "is_system 为真的消息不得归为 HR");

  const action = decideNewMessageAction({
    autoGenerate: true,
    lastIsHr,
    quotaExhaustedToday: false,
  });
  assert.notEqual(action, "generate", "系统提示到达时不应返回 generate");
  assert.equal(action, "update_status");

  // 同时支持驼峰命名 isSystem: true
  const messagesCamel = [
    { sender: "HR", text: "您好", is_self: false },
    { text: "系统通知", isSystem: true },
  ];
  assert.equal(isLastMessageFromHr(messagesCamel), false);
});

test("isLastMessageFromHr: 最后一条是 HR 消息时仍返回 true 且触发 generate", () => {
  const messagesWithSender = [
    { sender: "我", text: "您好！", is_self: true },
    { sender: "HR", text: "请问目前在深圳吗？", is_self: false },
  ];
  const lastIsHr1 = isLastMessageFromHr(messagesWithSender);
  assert.equal(lastIsHr1, true, "sender 为 HR 的消息应返回 true");

  const action1 = decideNewMessageAction({
    autoGenerate: true,
    lastIsHr: lastIsHr1,
    quotaExhaustedToday: false,
  });
  assert.equal(action1, "generate", "HR 消息且开启自动生成时应返回 generate");

  const messagesWithoutSender = [
    { text: "您好！", is_self: true },
    { text: "请问目前在深圳吗？", is_self: false },
  ];
  const lastIsHr2 = isLastMessageFromHr(messagesWithoutSender);
  assert.equal(lastIsHr2, true, "非我且非系统的消息应默认归为 HR");

  const action2 = decideNewMessageAction({
    autoGenerate: true,
    lastIsHr: lastIsHr2,
    quotaExhaustedToday: false,
  });
  assert.equal(action2, "generate");
});
