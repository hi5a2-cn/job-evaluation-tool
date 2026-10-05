import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  stripPdfExtension,
  validatePdfFileSize,
  formatProfileCharCount,
  formatResumeErrorMessage,
  validateUploadSelfName,
  handleDeleteResume,
  MAX_PDF_FILE_SIZE,
} from "../src/options.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test("stripPdfExtension: 截取文件名并切除 .pdf 后缀", () => {
  assert.equal(stripPdfExtension("李四-Java架构师.pdf"), "李四-Java架构师");
  assert.equal(stripPdfExtension("张三_算法工程师.PDF"), "张三_算法工程师");
  assert.equal(stripPdfExtension("简历.pdf.pdf"), "简历.pdf");
  assert.equal(stripPdfExtension("无后缀文件"), "无后缀文件");
  assert.equal(stripPdfExtension("doc_file.docx"), "doc_file.docx");
  assert.equal(stripPdfExtension(""), "");
  assert.equal(stripPdfExtension(null), "");
  assert.equal(stripPdfExtension(undefined), "");
});

test("validatePdfFileSize: 5MB 边界与超限拦截", () => {
  // 5MB 恰好满足
  assert.deepEqual(validatePdfFileSize(5 * 1024 * 1024), { valid: true, error: null });
  assert.deepEqual(validatePdfFileSize(1024), { valid: true, error: null });

  // 超过 5MB 拦截
  const overSizeRes = validatePdfFileSize(5 * 1024 * 1024 + 1);
  assert.equal(overSizeRes.valid, false);
  assert.equal(overSizeRes.error, "文件大小不能超过 5MB");

  // 非法或负数字节数拦截
  assert.equal(validatePdfFileSize(0).valid, false);
  assert.equal(validatePdfFileSize(-100).valid, false);
  assert.equal(validatePdfFileSize("invalid").valid, false);
});

test("formatProfileCharCount: 实时字数统计与 300 字上限警示", () => {
  assert.deepEqual(formatProfileCharCount(0, 300), {
    text: "已输入 0 / 300",
    isOverLimit: false,
  });
  assert.deepEqual(formatProfileCharCount(150, 300), {
    text: "已输入 150 / 300",
    isOverLimit: false,
  });
  assert.deepEqual(formatProfileCharCount(300, 300), {
    text: "已输入 300 / 300",
    isOverLimit: false,
  });
  assert.deepEqual(formatProfileCharCount(301, 300), {
    text: "已输入 301 / 300",
    isOverLimit: true,
  });
});

test("formatResumeErrorMessage: 统一服务端错误文案", () => {
  assert.equal(formatResumeErrorMessage("no_text"), "无法读取文字，请上传文字版 PDF");
  assert.equal(formatResumeErrorMessage("self_name_required"), "无法从简历中识别姓名，请在上方填写姓名后再试");
  assert.equal(formatResumeErrorMessage("file_too_large"), "文件大小不能超过 5MB");
  assert.equal(formatResumeErrorMessage("pdf_invalid"), "PDF 损坏或加密，无法解析");
  assert.match(formatResumeErrorMessage("quota_exhausted"), /今日简历画像生成额度已用完/);
  assert.match(formatResumeErrorMessage("no_llm_key"), /未配置 API Key/);
  assert.equal(formatResumeErrorMessage("too_many_resumes"), "简历最多 3 份");
});


test("options.html 双处说明文字严格原样替换与旧文案清除", () => {
  const htmlPath = path.resolve(__dirname, "../src/options.html");
  const content = fs.readFileSync(htmlPath, "utf-8");

  const expectedHint = "上传时，简历文字会在本机去掉姓名、手机号、邮箱等联系方式后发送给 DeepSeek 生成简历画像；之后岗位判断时只发送画像（编号为简历1/2/3，不发送简历名称）。本机只保存提取出的文字和画像，不保存 PDF 文件。";

  // 断言新文案正好出现 2 次（简历卡片与从严行业各一处）
  const matches = content.split(expectedHint).length - 1;
  assert.equal(matches, 2, "新说明文案应恰好在 options.html 中出现 2 次");

  // 断言旧文案已彻底移除
  assert.equal(
    content.includes("简历的'适合的岗位类型'和勾选的从严行业会随岗位判断发给 DeepSeek；简历名称不会发送。"),
    false,
    "旧说明文案必须彻底从 options.html 中移除"
  );

  // 断言脱敏姓名输入框存在且必填，文案更新
  assert.equal(content.includes('id="resume-self-name"'), true, "脱敏姓名输入框必须存在");
  assert.equal(
    content.includes("你的姓名（只在本机用于去掉姓名，不发送）："),
    true,
    "姓名输入框 label 必须包含必填提示文字"
  );
  assert.equal(
    content.includes("选填，只在本机用于去掉姓名"),
    false,
    "必须彻底移除选填字样"
  );
});

test("validateUploadSelfName: 上传前姓名必填校验", () => {
  assert.equal(validateUploadSelfName("").valid, false);
  assert.equal(validateUploadSelfName("   ").valid, false);
  assert.equal(validateUploadSelfName(null).valid, false);
  assert.equal(validateUploadSelfName(undefined).valid, false);
  assert.equal(
    validateUploadSelfName("").error,
    "请填写你的姓名（只在本机用于去掉姓名，不发送）"
  );

  const res = validateUploadSelfName("  李明  ");
  assert.equal(res.valid, true);
  assert.equal(res.error, null);
  assert.equal(res.value, "李明");
});

test("handleDeleteResume: 取消删除先弹 confirm 确认，取消时什么都不做", () => {
  let confirmPrompt = null;
  const mockConfirm = (msg) => {
    confirmPrompt = msg;
    return false; // 用户点击取消
  };

  let sendCalled = false;
  const mockSendMessage = () => {
    sendCalled = true;
  };

  let removed = false;
  const mockItemEl = {
    remove: () => {
      removed = true;
    },
  };

  const mockBtn = { disabled: false };
  const mockMsgEl = { textContent: "初始消息", className: "msg" };
  let updateSlotsCalled = false;

  handleDeleteResume({
    slot: 2,
    itemEl: mockItemEl,
    delBtn: mockBtn,
    msgEl: mockMsgEl,
    updateSlots: () => {
      updateSlotsCalled = true;
    },
    confirmFn: mockConfirm,
    sendMessageFn: mockSendMessage,
  });

  // 1. 断言 confirm 提示文案与 slot 格式
  assert.equal(
    confirmPrompt,
    "确定删除简历 2？提取的文字和画像会一起删除，不能恢复。"
  );
  // 2. 取消时什么都不做
  assert.equal(sendCalled, false, "取消后不得发送 delete_resume 消息");
  assert.equal(removed, false, "取消后不得移除 DOM 元素");
  assert.equal(updateSlotsCalled, false, "取消后不得更新槽位");
  assert.equal(mockMsgEl.textContent, "初始消息", "取消后不得改动消息");
  assert.equal(mockBtn.disabled, false);
});

test("handleDeleteResume: 确认后发送 delete_resume 成功，移除元素并提示'简历 N 已删除'", () => {
  let confirmPrompt = null;
  const mockConfirm = (msg) => {
    confirmPrompt = msg;
    return true; // 用户点击确认
  };

  let sentPayload = null;
  const mockSendMessage = (msg, callback) => {
    sentPayload = msg;
    callback({ ok: true, data: { ok: true, slot: 1 } });
  };

  let removed = false;
  const mockItemEl = {
    remove: () => {
      removed = true;
    },
  };

  const mockBtn = { disabled: false };
  const mockMsgEl = { textContent: "", className: "msg" };
  let updateSlotsCalled = false;

  handleDeleteResume({
    slot: 1,
    itemEl: mockItemEl,
    delBtn: mockBtn,
    msgEl: mockMsgEl,
    updateSlots: () => {
      updateSlotsCalled = true;
    },
    confirmFn: mockConfirm,
    sendMessageFn: mockSendMessage,
  });

  assert.equal(
    confirmPrompt,
    "确定删除简历 1？提取的文字和画像会一起删除，不能恢复。"
  );
  assert.deepEqual(sentPayload, { type: "delete_resume", slot: 1 });
  assert.equal(removed, true, "成功时必须移除 DOM 元素");
  assert.equal(updateSlotsCalled, true, "成功时必须更新槽位");
  assert.equal(mockMsgEl.textContent, "简历 1 已删除");
  assert.equal(mockMsgEl.className, "msg success");
  assert.equal(mockBtn.disabled, false);
});

test("handleDeleteResume: 确认后删除失败，保留该项并展示错误提示", () => {
  const mockConfirm = () => true;

  let sentPayload = null;
  const mockSendMessage = (msg, callback) => {
    sentPayload = msg;
    callback({ ok: false, error: "slot_invalid" });
  };

  let removed = false;
  const mockItemEl = {
    remove: () => {
      removed = true;
    },
  };

  const mockBtn = { disabled: false };
  const mockMsgEl = { textContent: "", className: "msg" };
  let updateSlotsCalled = false;

  handleDeleteResume({
    slot: 3,
    itemEl: mockItemEl,
    delBtn: mockBtn,
    msgEl: mockMsgEl,
    updateSlots: () => {
      updateSlotsCalled = true;
    },
    confirmFn: mockConfirm,
    sendMessageFn: mockSendMessage,
  });

  assert.deepEqual(sentPayload, { type: "delete_resume", slot: 3 });
  assert.equal(removed, false, "失败时必须保留该项（不得移除）");
  assert.equal(updateSlotsCalled, false, "失败时不得更新槽位");
  assert.equal(mockMsgEl.textContent, "简历编号无效");
  assert.equal(mockMsgEl.className, "msg error");
  assert.equal(mockBtn.disabled, false);
});

test("handleDeleteResume: 确认后服务无响应（res 为 null），保留该项并提示 Jet 未运行", () => {
  const mockConfirm = () => true;

  const mockSendMessage = (msg, callback) => {
    callback(null);
  };

  let removed = false;
  const mockItemEl = {
    remove: () => {
      removed = true;
    },
  };

  const mockBtn = { disabled: false };
  const mockMsgEl = { textContent: "", className: "msg" };

  handleDeleteResume({
    slot: 2,
    itemEl: mockItemEl,
    delBtn: mockBtn,
    msgEl: mockMsgEl,
    confirmFn: mockConfirm,
    sendMessageFn: mockSendMessage,
  });

  assert.equal(removed, false, "服务异常时必须保留该项");
  assert.equal(mockMsgEl.textContent, "Jet 未运行");
  assert.equal(mockMsgEl.className, "msg error");
  assert.equal(mockBtn.disabled, false);
});
