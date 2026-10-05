// 体检第 20 条：上传简历时填的姓名不在浏览器里保存（插件和本机 Jet 都不保存）
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { clearLegacyResumeSelfName } from "../src/options.js";

const optionsSrc = fs.readFileSync(new URL("../src/options.js", import.meta.url), "utf8");

test("clearLegacyResumeSelfName 删除旧版本存的 resumeSelfName", () => {
  const removed = [];
  clearLegacyResumeSelfName({ remove: (key) => removed.push(key) });
  assert.deepEqual(removed, ["resumeSelfName"]);
});

test("clearLegacyResumeSelfName 没有 storage 或删除出错时不抛异常", () => {
  assert.doesNotThrow(() => clearLegacyResumeSelfName(null));
  assert.doesNotThrow(() => clearLegacyResumeSelfName({}));
  assert.doesNotThrow(() =>
    clearLegacyResumeSelfName({
      remove: () => {
        throw new Error("Extension context invalidated");
      },
    })
  );
});

test("options.js 不再读写浏览器里的姓名，打开设置页时清掉旧值", () => {
  assert.ok(!optionsSrc.includes("resumeSelfName:"), "不能再 set resumeSelfName");
  assert.ok(!/\.get\(\s*\[?\s*["']resumeSelfName/.test(optionsSrc), "不能再读 resumeSelfName 回填");
  const initIdx = optionsSrc.indexOf("export function initOptions(");
  assert.ok(initIdx !== -1);
  // 放在设置页初始化的最前面，后面的初始化出错也不影响删除
  const firstStatement = optionsSrc
    .slice(optionsSrc.indexOf("{", initIdx) + 1)
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("//"));
  assert.equal(firstStatement, "clearLegacyResumeSelfName();");
});

test("安装或更新插件时删除旧版本存的姓名", () => {
  const bgSrc = fs.readFileSync(new URL("../src/background.js", import.meta.url), "utf8");
  const idx = bgSrc.indexOf("chrome.runtime.onInstalled.addListener(");
  assert.ok(idx !== -1);
  const end = bgSrc.indexOf("\n  });", idx);
  assert.ok(bgSrc.slice(idx, end).includes('chrome.storage?.local?.remove("resumeSelfName")'));
});

test("重新生成画像前先检查姓名，没填就不发请求", () => {
  const start = optionsSrc.indexOf("// C. 重新生成画像");
  const send = optionsSrc.indexOf('type: "regenerate_resume"', start);
  assert.ok(start !== -1 && send !== -1, "找不到重新生成画像的处理代码");
  const before = optionsSrc.slice(start, send);
  const check = before.indexOf("validateUploadSelfName(");
  assert.ok(check !== -1, "发请求前要调用 validateUploadSelfName");
  assert.ok(before.slice(check).includes("return;"), "姓名无效时要在发请求前返回");
  assert.ok(optionsSrc.slice(send, send + 200).includes("self_name: nameValidation.value"));
});
