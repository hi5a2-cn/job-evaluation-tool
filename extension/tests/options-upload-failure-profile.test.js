// 体检第 29 条：上传失败时画像框显示服务端返回的当前画像（换了简历时为空），不留着旧画像
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const src = fs.readFileSync(new URL("../src/options.js", import.meta.url), "utf8");

test("上传失败分支按返回的 profile 刷新画像框，空字符串也刷新", () => {
  const start = src.indexOf('type: "upload_resume"');
  assert.ok(start !== -1, "找不到上传简历的处理代码");
  const failIdx = src.indexOf("} else {", start);
  const branch = src.slice(failIdx, src.indexOf("formatResumeErrorMessage(res.error)", failIdx));
  assert.ok(branch.includes('typeof res.data?.profile === "string"'), "失败时要按返回的 profile 刷新，包括空字符串");
  assert.ok(branch.includes("profileTextarea.value = res.data.profile"));
  assert.ok(!branch.includes("if (res.data?.profile) {"), "不能只在 profile 非空时才刷新");
});
