import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { isBossPageSender } from "../src/scheduler.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const backgroundJsPath = path.resolve(__dirname, "../src/background.js");

test("isBossPageSender: 无 sender 或无 tab 均返回 false", () => {
  assert.equal(isBossPageSender(null), false);
  assert.equal(isBossPageSender(undefined), false);
  assert.equal(isBossPageSender(123), false);
  assert.equal(isBossPageSender("sender"), false);
  assert.equal(isBossPageSender({}), false);
  assert.equal(isBossPageSender({ tab: null }), false);
  assert.equal(isBossPageSender({ tab: undefined }), false);
  assert.equal(isBossPageSender({ id: "ext" }), false);
});

test("isBossPageSender: URL 缺失或非字符串均返回 false", () => {
  assert.equal(isBossPageSender({ tab: {} }), false);
  assert.equal(isBossPageSender({ tab: { id: 1 } }), false);
  assert.equal(isBossPageSender({ tab: { id: 1, url: null } }), false);
  assert.equal(isBossPageSender({ tab: { id: 1, url: "" } }), false);
  assert.equal(isBossPageSender({ tab: { id: 1 }, url: null }), false);
  assert.equal(isBossPageSender({ tab: { id: 1 }, url: "" }), false);
  assert.equal(isBossPageSender({ tab: { id: 1, url: 123 } }), false);
});

test("isBossPageSender: 插件自身页面返回 false", () => {
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "chrome-extension://abcdefg/src/myjobs.html" },
    }),
    false
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "chrome-extension://abcdefg/src/sidepanel.html" },
    }),
    false
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "chrome-extension://abcdefg/src/options.html" },
    }),
    false
  );
  // sender.tab.url 缺失退回 sender.url 为插件页
  assert.equal(
    isBossPageSender({
      tab: { id: 1 },
      url: "chrome-extension://abcdefg/src/myjobs.html",
    }),
    false
  );
});

test("isBossPageSender: 非 BOSS 域名或非 https 协议均返回 false", () => {
  // HTTP
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "http://www.zhipin.com/web/geek/job" },
    }),
    false
  );
  // 其他网站包含该 URL
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "https://www.google.com/?q=https://www.zhipin.com/" },
    }),
    false
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "https://zhipin.com/web/geek/job" },
    }),
    false
  );
  // sender.tab.url 为非 BOSS 页，即使 sender.url 为 BOSS 也以 tab.url 为准
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "chrome-extension://mock/src/myjobs.html" },
      url: "https://www.zhipin.com/web/geek/job",
    }),
    false
  );
});

test("isBossPageSender: 合法 BOSS 直聘页面返回 true", () => {
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "https://www.zhipin.com/" },
    }),
    true
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 1, url: "https://www.zhipin.com/web/geek/job" },
    }),
    true
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 2, url: "https://www.zhipin.com/web/geek/chat" },
    }),
    true
  );
  assert.equal(
    isBossPageSender({
      tab: { id: 3, url: "https://www.zhipin.com/job_detail/123456.html" },
    }),
    true
  );
  // tab.url 缺失时退回 sender.url
  assert.equal(
    isBossPageSender({
      tab: { id: 4 },
      url: "https://www.zhipin.com/web/geek/job",
    }),
    true
  );
});

test("background.js 源码检查：save_hr_note 与 set_job_status 使用 isBossPageSender 保护标签页状态", () => {
  const bgCode = fs.readFileSync(backgroundJsPath, "utf-8");

  // 1. 必须从 scheduler.js 导入 isBossPageSender
  assert.ok(
    bgCode.includes("isBossPageSender"),
    "background.js 必须引入并在代码中使用 isBossPageSender"
  );
  assert.match(
    bgCode,
    /import\s*\{[^}]*isBossPageSender[^}]*\}\s*from\s*["']\.\/scheduler\.js["']/,
    "background.js 必须从 ./scheduler.js 导入 isBossPageSender"
  );

  // 2. save_hr_note 处理中必须有 isBossPageSender(sender) 条件分支保护标签页状态
  const hrNoteBlockMatch = bgCode.match(
    /message\?\.type\s*===\s*["']save_hr_note["'][\s\S]*?(?=\/\/\s*\d+|$)/
  );
  assert.ok(hrNoteBlockMatch, "background.js 必须包含 save_hr_note 处理块");
  const hrNoteBlock = hrNoteBlockMatch[0];
  assert.ok(
    hrNoteBlock.includes("isBossPageSender(sender)"),
    "save_hr_note 处理中必须检查 isBossPageSender(sender)"
  );

  // 3. set_job_status 处理中必须有 isBossPageSender(sender) 条件分支保护标签页状态
  const statusBlockMatch = bgCode.match(
    /message\?\.type\s*===\s*["']set_job_status["'][\s\S]*?(?=\/\/\s*\d+|$)/
  );
  assert.ok(statusBlockMatch, "background.js 必须包含 set_job_status 处理块");
  const statusBlock = statusBlockMatch[0];
  assert.ok(
    statusBlock.includes("isBossPageSender(sender)"),
    "set_job_status 处理中必须检查 isBossPageSender(sender)"
  );
});
