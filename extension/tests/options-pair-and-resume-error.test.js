// 体检第 39 条②、第 40 条：配对成功后重新加载从严行业；简历错误优先显示服务端的中文说明
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { formatResumeErrorMessage } from "../src/options.js";

const src = fs.readFileSync(new URL("../src/options.js", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("../src/options.html", import.meta.url), "utf8");

test("配对成功后和其他栏一起重新加载从严行业", () => {
  const ok = src.indexOf('msgEl.textContent = "配对成功！";');
  assert.ok(ok !== -1, "找不到配对成功的处理代码");
  const branch = src.slice(ok, src.indexOf("} else {", ok));
  assert.ok(branch.includes("loadPrejudgeSettings();"));
  assert.ok(branch.includes("loadStrictIndustries();"), "配对成功后要重新加载从严行业");
});

test("llm_failed 显示服务端说明，没有说明时显示中文兜底", () => {
  assert.equal(formatResumeErrorMessage("llm_failed", "生成失败：连接失败，可以稍后再试"), "生成失败：连接失败，可以稍后再试");
  const fallback = formatResumeErrorMessage("llm_failed");
  assert.notEqual(fallback, "llm_failed");
  assert.match(fallback, /重新生成画像/);
});

test("没有对应中文的错误码优先显示服务端说明；已有中文的错误码不变", () => {
  assert.equal(formatResumeErrorMessage("some_new_error", "服务端说明"), "服务端说明");
  assert.equal(formatResumeErrorMessage("some_new_error"), "some_new_error");
  assert.equal(formatResumeErrorMessage("no_text", "服务端说明"), "无法读取文字，请上传文字版 PDF");
});

test("页面上所有简历错误提示都把服务端说明传进来", () => {
  assert.ok(!src.includes("formatResumeErrorMessage(res.error)"), "不能只传错误码");
  assert.equal((src.match(/formatResumeErrorMessage\(res\.error, res\.data\?\.message\)/g) || []).length, 3);
});

test("设置页 body 里的 div 开闭成对（第 39 条①：聊天自动生成卡片补上结束标签）", () => {
  const body = html.slice(html.indexOf("<body"), html.indexOf("</body>"));
  const opens = (body.match(/<div\b/g) || []).length;
  const closes = (body.match(/<\/div>/g) || []).length;
  assert.equal(opens, closes);
  const ag = body.indexOf('id="auto-generate-section"');
  const pj = body.indexOf('id="prejudge-section"');
  const between = body.slice(ag, pj);
  // 「聊天自动生成」卡片在「列表预判」卡片开始前完整闭合
  assert.equal((between.match(/<div\b/g) || []).length, (between.match(/<\/div>/g) || []).length);
});
