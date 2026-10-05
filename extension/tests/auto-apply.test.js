import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { kaMatchesJobId, decideAutoApply, parseJobDetailUrlId } from "../src/auto-apply.js";

test("kaMatchesJobId: 规则核对", () => {
  // 1. 无 ka（null / undefined / ""）时照常返回 true
  assert.equal(kaMatchesJobId(null, "job_abc123"), true);
  assert.equal(kaMatchesJobId(undefined, "job_abc123"), true);
  assert.equal(kaMatchesJobId("", "job_abc123"), true);

  // 2. ka 存在且包含岗位 ID 返回 true
  assert.equal(
    kaMatchesJobId("cpc_job_list_chat_1234567890123456789012345678", "1234567890123456789012345678"),
    true
  );
  assert.equal(kaMatchesJobId("cpc_job_list_chat_abc", "abc"), true);

  // 3. ka 存在但不包含岗位 ID 返回 false
  assert.equal(
    kaMatchesJobId("cpc_job_list_chat_1234567890123456789012345678", "different_job_id"),
    false
  );

  // 4. ka 存在但 jobId 无效时返回 false
  assert.equal(kaMatchesJobId("cpc_job_list_chat_123", null), false);
  assert.equal(kaMatchesJobId("cpc_job_list_chat_123", undefined), false);
  assert.equal(kaMatchesJobId("cpc_job_list_chat_123", ""), false);

  // 5. ka 非字符串返回 false
  assert.equal(kaMatchesJobId(12345, "12345"), false);
});

test("decideAutoApply: 非列表页 skip", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  // 聊天页
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: "/web/geek/chat" }),
    { action: "skip", reason: "not_search_list" }
  );

  // 详情页（005 允许 /job_detail/ 路径，未传 urlJobId 时按地址 ID 缺失 skip）
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: "/job_detail/abc123.html" }),
    { action: "skip", reason: "no_url_job_id" }
  );

  // 首页或其他路径
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: "/" }),
    { action: "skip", reason: "not_search_list" }
  );

  // pathname 缺失或非字符串
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: null }),
    { action: "skip", reason: "not_search_list" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: undefined }),
    { action: "skip", reason: "not_search_list" }
  );

  // 搜索列表页的合法路径变体
  assert.equal(
    decideAutoApply({ ...baseValid, pathname: "/web/geek/jobs" }).action,
    "mark"
  );
  assert.equal(
    decideAutoApply({ ...baseValid, pathname: "/web/geek/job/recommend" }).action,
    "mark"
  );
});

test("decideAutoApply: 按钮文字「继续沟通」及其他一律 skip", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  // 「继续沟通」
  assert.deepEqual(
    decideAutoApply({ ...baseValid, buttonText: "继续沟通" }),
    { action: "skip", reason: "not_chat_button" }
  );

  // 带空格的「继续沟通」
  assert.deepEqual(
    decideAutoApply({ ...baseValid, buttonText: "  继续沟通  " }),
    { action: "skip", reason: "not_chat_button" }
  );

  // 其他文字
  assert.deepEqual(
    decideAutoApply({ ...baseValid, buttonText: "发简历" }),
    { action: "skip", reason: "not_chat_button" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, buttonText: "" }),
    { action: "skip", reason: "not_chat_button" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, buttonText: null }),
    { action: "skip", reason: "not_chat_button" }
  );
});

test("decideAutoApply: 「立即沟通」+ ka 匹配 mark", () => {
  const result = decideAutoApply({
    pathname: "/web/geek/jobs",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_28charslongjobid12345678",
    currentJobId: "28charslongjobid12345678",
    currentStatus: null,
  });
  assert.deepEqual(result, { action: "mark", reason: "ok" });

  // 带有首尾空格的「立即沟通」同样识别为 mark
  const resultWithSpaces = decideAutoApply({
    pathname: "/web/geek/job",
    buttonText: "  立即沟通\n",
    ka: "cpc_job_list_chat_job999",
    currentJobId: "job999",
    currentStatus: null,
  });
  assert.deepEqual(resultWithSpaces, { action: "mark", reason: "ok" });
});

test("decideAutoApply: ka 不匹配 skip", () => {
  const result = decideAutoApply({
    pathname: "/web/geek/jobs",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_new_switched_job_id",
    currentJobId: "old_cached_job_id",
    currentStatus: null,
  });
  assert.deepEqual(result, { action: "skip", reason: "ka_mismatch" });
});

test("decideAutoApply: 无 ka mark", () => {
  // ka 为 null
  assert.deepEqual(
    decideAutoApply({
      pathname: "/web/geek/job",
      buttonText: "立即沟通",
      ka: null,
      currentJobId: "job_no_ka_1",
      currentStatus: null,
    }),
    { action: "mark", reason: "ok" }
  );

  // ka 为 undefined
  assert.deepEqual(
    decideAutoApply({
      pathname: "/web/geek/job",
      buttonText: "立即沟通",
      ka: undefined,
      currentJobId: "job_no_ka_2",
      currentStatus: null,
    }),
    { action: "mark", reason: "ok" }
  );

  // ka 为空字符串
  assert.deepEqual(
    decideAutoApply({
      pathname: "/web/geek/job",
      buttonText: "立即沟通",
      ka: "",
      currentJobId: "job_no_ka_3",
      currentStatus: null,
    }),
    { action: "mark", reason: "ok" }
  );
});

test("decideAutoApply: 无岗位 ID skip", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentStatus: null,
  };

  // currentJobId 为 null
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentJobId: null }),
    { action: "skip", reason: "no_current_job_id" }
  );

  // currentJobId 为 undefined
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentJobId: undefined }),
    { action: "skip", reason: "no_current_job_id" }
  );

  // currentJobId 为空字符串或纯空白
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentJobId: "" }),
    { action: "skip", reason: "no_current_job_id" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentJobId: "   " }),
    { action: "skip", reason: "no_current_job_id" }
  );
});

test("decideAutoApply: 已是 applied skip", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentJobId: "job123",
  };

  // 字符串形态 "applied"
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: "applied" }),
    { action: "skip", reason: "already_applied" }
  );

  // 对象形态 { status: "applied" }
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: { status: "applied" } }),
    { action: "skip", reason: "already_applied" }
  );
});

test("decideAutoApply: 原状态为 saved / skipped / null 时 mark", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentJobId: "job123",
  };

  // saved
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: "saved" }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: { status: "saved" } }),
    { action: "mark", reason: "ok" }
  );

  // skipped
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: "skipped" }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: { status: "skipped" } }),
    { action: "mark", reason: "ok" }
  );

  // null
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: null }),
    { action: "mark", reason: "ok" }
  );

  // undefined
  assert.deepEqual(
    decideAutoApply({ ...baseValid, currentStatus: undefined }),
    { action: "mark", reason: "ok" }
  );
});

test("parseJobDetailUrlId: 纯函数解析", () => {
  // 1. 标准地址
  assert.equal(
    parseJobDetailUrlId("/job_detail/4bfb2ad04ba5980a1HF-39y8FlRU.html"),
    "4bfb2ad04ba5980a1HF-39y8FlRU"
  );
  assert.equal(
    parseJobDetailUrlId("/job_detail/123456789.html"),
    "123456789"
  );

  // 2. 带域名与参数 / 哈希
  assert.equal(
    parseJobDetailUrlId("https://www.zhipin.com/job_detail/abc123def456.html?ka=search_list"),
    "abc123def456"
  );
  assert.equal(
    parseJobDetailUrlId("https://www.zhipin.com/job_detail/abc123def456.html#section-comment"),
    "abc123def456"
  );

  // 3. 非独立职位页地址或格式不匹配返回 null
  assert.equal(parseJobDetailUrlId("/web/geek/job"), null);
  assert.equal(parseJobDetailUrlId("/web/geek/chat"), null);
  assert.equal(parseJobDetailUrlId("/job_detail/"), null);
  assert.equal(parseJobDetailUrlId("/job_detail/.html"), null);
  assert.equal(parseJobDetailUrlId("/job_detail/abc123"), null);
  assert.equal(parseJobDetailUrlId("/other/abc123.html"), null);

  // 4. 非字符串或空值
  assert.equal(parseJobDetailUrlId(null), null);
  assert.equal(parseJobDetailUrlId(undefined), null);
  assert.equal(parseJobDetailUrlId(""), null);
  assert.equal(parseJobDetailUrlId(12345), null);
});

test("decideAutoApply: 独立职位页路径通过 mark", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/4bfb2ad04ba5980a1HF-39y8FlRU.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_4bfb2ad04ba5980a1HF-39y8FlRU",
    urlJobId: "4bfb2ad04ba5980a1HF-39y8FlRU",
    currentJobId: "4bfb2ad04ba5980a1HF-39y8FlRU",
    currentStatus: null,
  };

  // 标准通过
  assert.deepEqual(decideAutoApply(baseJobDetailValid), { action: "mark", reason: "ok" });

  // 按钮文字带首尾空格
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "  立即沟通\n" }),
    { action: "mark", reason: "ok" }
  );

  // ka 为空 / null / undefined 照常 mark
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, ka: null }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, ka: undefined }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, ka: "" }),
    { action: "mark", reason: "ok" }
  );

  // 原状态为 saved / skipped / null 时 mark
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentStatus: "saved" }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentStatus: "skipped" }),
    { action: "mark", reason: "ok" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentStatus: { status: "saved" } }),
    { action: "mark", reason: "ok" }
  );
});

test("decideAutoApply: 独立职位页地址 ID 不一致 skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job_from_url_123.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_job_from_url_123",
    urlJobId: "job_from_url_123",
    currentJobId: "job_from_reader_456",
    currentStatus: null,
  };

  assert.deepEqual(decideAutoApply(baseJobDetailValid), {
    action: "skip",
    reason: "url_job_id_mismatch",
  });
});

test("decideAutoApply: 独立职位页地址 ID 缺失 skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job123.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  // urlJobId 为 null
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, urlJobId: null }),
    { action: "skip", reason: "no_url_job_id" }
  );

  // urlJobId 为 undefined
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, urlJobId: undefined }),
    { action: "skip", reason: "no_url_job_id" }
  );

  // urlJobId 为空字符串或纯空白
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, urlJobId: "" }),
    { action: "skip", reason: "no_url_job_id" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, urlJobId: "   " }),
    { action: "skip", reason: "no_url_job_id" }
  );
});

test("decideAutoApply: 独立职位页读取失败（无岗位 ID）skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job123.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_job123",
    urlJobId: "job123",
    currentStatus: null,
  };

  // currentJobId 为 null
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentJobId: null }),
    { action: "skip", reason: "no_current_job_id" }
  );

  // currentJobId 为 undefined
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentJobId: undefined }),
    { action: "skip", reason: "no_current_job_id" }
  );

  // currentJobId 为空字符串或空白
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentJobId: "" }),
    { action: "skip", reason: "no_current_job_id" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentJobId: "   " }),
    { action: "skip", reason: "no_current_job_id" }
  );
});

test("decideAutoApply: 独立职位页「继续沟通」及其他按钮文字 skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job123.html",
    ka: "cpc_job_detail_chat_job123",
    urlJobId: "job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  // 「继续沟通」
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "继续沟通" }),
    { action: "skip", reason: "not_chat_button" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "  继续沟通  " }),
    { action: "skip", reason: "not_chat_button" }
  );

  // 其他文字
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "立即投递" }),
    { action: "skip", reason: "not_chat_button" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "发简历" }),
    { action: "skip", reason: "not_chat_button" }
  );
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, buttonText: "" }),
    { action: "skip", reason: "not_chat_button" }
  );
});

test("decideAutoApply: 独立职位页 ka 不含 ID skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job123.html",
    buttonText: "立即沟通",
    urlJobId: "job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  assert.deepEqual(
    decideAutoApply({
      ...baseJobDetailValid,
      ka: "cpc_job_detail_chat_different_job_id_999",
    }),
    { action: "skip", reason: "ka_mismatch" }
  );
});

test("decideAutoApply: 独立职位页已是 applied skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/job123.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_job123",
    urlJobId: "job123",
    currentJobId: "job123",
  };

  // 字符串形态 "applied"
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentStatus: "applied" }),
    { action: "skip", reason: "already_applied" }
  );

  // 对象形态 { status: "applied" }
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, currentStatus: { status: "applied" } }),
    { action: "skip", reason: "already_applied" }
  );
});

test("decideAutoApply: 独立职位页处于其他岗位区域 skip", () => {
  const baseJobDetailValid = {
    pathname: "/job_detail/4bfb2ad04ba5980a1HF-39y8FlRU.html",
    buttonText: "立即沟通",
    ka: "cpc_job_detail_chat_4bfb2ad04ba5980a1HF-39y8FlRU",
    urlJobId: "4bfb2ad04ba5980a1HF-39y8FlRU",
    currentJobId: "4bfb2ad04ba5980a1HF-39y8FlRU",
    currentStatus: null,
  };

  // inOtherJobArea 为 true 时返回 other_job_area skip
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, inOtherJobArea: true }),
    { action: "skip", reason: "other_job_area" }
  );

  // 无 ka 且在其他岗位区域时同样 skip（防止误标当前岗位已投递）
  assert.deepEqual(
    decideAutoApply({ ...baseJobDetailValid, ka: null, inOtherJobArea: true }),
    { action: "skip", reason: "other_job_area" }
  );
});

test("decideAutoApply: 搜索列表页忽略 inOtherJobArea 照常 mark", () => {
  const baseValid = {
    pathname: "/web/geek/job",
    buttonText: "立即沟通",
    ka: "cpc_job_list_chat_job123",
    currentJobId: "job123",
    currentStatus: null,
  };

  // 搜索列表页传 true 也照常 mark
  assert.deepEqual(
    decideAutoApply({ ...baseValid, inOtherJobArea: true }),
    { action: "mark", reason: "ok" }
  );

  // 搜索列表页变体路径传 true 也照常 mark
  assert.deepEqual(
    decideAutoApply({ ...baseValid, pathname: "/web/geek/jobs", inOtherJobArea: true }),
    { action: "mark", reason: "ok" }
  );
});

test("auto-apply: content.js 中复制的函数与 auto-apply.js 源码严格一致", () => {
  const autoApplyPath = new URL("../src/auto-apply.js", import.meta.url);
  const contentPath = new URL("../src/content.js", import.meta.url);

  const autoApplySrc = fs.readFileSync(autoApplyPath, "utf8").replace(/\r\n/g, "\n");
  const contentSrc = fs.readFileSync(contentPath, "utf8").replace(/\r\n/g, "\n");

  // 1. 验证 content.js 包含同步提示注释
  assert.ok(
    contentSrc.includes("// 注意：以下纯函数与 extension/src/auto-apply.js 保持一致（content.js 为经典脚本，无法 import）"),
    "content.js 应包含同步声明注释"
  );

  // 2. 从 content.js 提取 BEGIN SYNC 到 END SYNC 块
  const syncMatch = contentSrc.match(
    /\/\/\s*BEGIN SYNC auto-apply\n([\s\S]*?)\n\s*\/\/\s*END SYNC auto-apply/
  );
  assert.ok(syncMatch, "content.js 中应存在 // BEGIN SYNC auto-apply 标记块");

  // content.js 里的函数每行有 2 个空格缩进，规范化去除这 2 个空格
  const contentBlock = syncMatch[1]
    .split("\n")
    .map((line) => (line.startsWith("  ") ? line.slice(2) : line))
    .join("\n")
    .trim();

  // 3. 从 auto-apply.js 提取两个函数源码（去除 export 前缀或 export 语句）
  // 匹配从 kaMatchesJobId 的 JSDoc 到 decideAutoApply 结束
  const autoApplyFuncsMatch = autoApplySrc.match(
    /(\/\*\*[\r\n\s]+\* 校验按钮 ka 属性[\s\S]*?function kaMatchesJobId[\s\S]*?function decideAutoApply[\s\S]*?return \{ action: "mark", reason: "ok" \};\n\})/
  );
  assert.ok(autoApplyFuncsMatch, "auto-apply.js 中应包含两个纯函数的定义");
  const autoApplyBlock = autoApplyFuncsMatch[1].trim();

  assert.equal(
    contentBlock,
    autoApplyBlock,
    "content.js 中同步的 auto-apply 函数源码应与 auto-apply.js 完全一致"
  );
});
