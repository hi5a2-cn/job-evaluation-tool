import test from "node:test";
import assert from "node:assert/strict";
import {
  buildListMarks,
  buildPageSummary,
  resolveJobStatus,
  STATUS_LABELS,
} from "../src/page-summary.js";

test("buildListMarks: handles 4-tier verdicts and legacy verdict mapping", () => {
  const judgements = new Map([
    [
      "job_apply",
      {
        judgement: {
          status: "done",
          verdict: "apply",
          judged_at: "2026-09-25T10:00:00Z",
        },
      },
    ],
    [
      "job_try",
      {
        judgement: {
          status: "done",
          verdict: "try",
          judged_at: "2026-09-25T10:01:00Z",
        },
      },
    ],
    [
      "job_check",
      {
        judgement: {
          status: "done",
          verdict: "check",
          judged_at: "2026-09-25T10:02:00Z",
        },
      },
    ],
    [
      "job_skip",
      {
        judgement: {
          status: "done",
          verdict: "skip",
          judged_at: "2026-09-25T10:03:00Z",
        },
      },
    ],
    // Legacy verdicts
    [
      "job_fit",
      {
        judgement: {
          status: "done",
          verdict: "fit",
          judged_at: "2026-09-25T10:04:00Z",
        },
      },
    ],
    [
      "job_unsure",
      {
        judgement: {
          status: "done",
          verdict: "unsure",
          judged_at: "2026-09-25T10:05:00Z",
        },
      },
    ],
    [
      "job_unfit",
      {
        judgement: {
          status: "done",
          verdict: "unfit",
          judged_at: "2026-09-25T10:06:00Z",
        },
      },
    ],
  ]);

  const marks = buildListMarks(judgements);

  assert.deepEqual(marks["job_apply"], {
    verdict_label: "适合投递",
    verdict_tone: "green",
    stale: false,
    judged_at: "2026-09-25T10:00:00Z",
  });

  assert.deepEqual(marks["job_try"], {
    verdict_label: "可以一试",
    verdict_tone: "blue",
    stale: false,
    judged_at: "2026-09-25T10:01:00Z",
  });

  assert.deepEqual(marks["job_check"], {
    verdict_label: "需要确认",
    verdict_tone: "yellow",
    stale: false,
    judged_at: "2026-09-25T10:02:00Z",
  });

  assert.deepEqual(marks["job_skip"], {
    verdict_label: "不建议投",
    verdict_tone: "red",
    stale: false,
    judged_at: "2026-09-25T10:03:00Z",
  });

  // Legacy mappings
  assert.deepEqual(marks["job_fit"], {
    verdict_label: "适合投递",
    verdict_tone: "green",
    stale: false,
    judged_at: "2026-09-25T10:04:00Z",
  });

  assert.deepEqual(marks["job_unsure"], {
    verdict_label: "需要确认",
    verdict_tone: "yellow",
    stale: false,
    judged_at: "2026-09-25T10:05:00Z",
  });

  assert.deepEqual(marks["job_unfit"], {
    verdict_label: "不建议投",
    verdict_tone: "red",
    stale: false,
    judged_at: "2026-09-25T10:06:00Z",
  });
});

test("buildListMarks: correctly evaluates stale flag", () => {
  const judgements = new Map([
    [
      "job_job_changed",
      {
        judgement: {
          status: "done",
          verdict: "apply",
          stale: { job_changed: true },
        },
      },
    ],
    [
      "job_profile_changed",
      {
        judgement: {
          status: "done",
          verdict: "try",
          stale: { profile_changed: true },
        },
      },
    ],
    [
      "job_method_changed",
      {
        judgement: {
          status: "done",
          verdict: "check",
          stale: { method_changed: true },
        },
      },
    ],
    [
      "job_boolean_stale",
      {
        judgement: {
          status: "done",
          verdict: "skip",
          stale: true,
        },
      },
    ],
    [
      "job_not_stale",
      {
        judgement: {
          status: "done",
          verdict: "apply",
          stale: { job_changed: false, profile_changed: false, method_changed: false },
        },
      },
    ],
  ]);

  const marks = buildListMarks(judgements);
  assert.equal(marks["job_job_changed"].stale, true);
  assert.equal(marks["job_profile_changed"].stale, true);
  assert.equal(marks["job_method_changed"].stale, true);
  assert.equal(marks["job_boolean_stale"].stale, true);
  assert.equal(marks["job_not_stale"].stale, false);
});

test("buildListMarks: filters out non-done or missing judgements", () => {
  const judgements = new Map([
    ["job_running", { judgement: { status: "running", verdict: null } }],
    ["job_queued", { judgement: { status: "queued", verdict: null } }],
    ["job_failed", { judgement: { status: "failed", verdict: "skip" } }],
    ["job_quota", { judgement: { status: "quota_exhausted", verdict: "apply" } }],
    ["job_interrupted", { judgement: { status: "interrupted", verdict: "try" } }],
    ["job_no_judgement", { judgement: null }],
    ["job_empty_entry", {}],
    ["job_invalid_verdict", { judgement: { status: "done", verdict: "unknown_verdict" } }],
    ["job_valid", { judgement: { status: "done", verdict: "apply" } }],
  ]);

  const marks = buildListMarks(judgements);
  assert.deepEqual(Object.keys(marks), ["job_valid"]);
  assert.equal(marks["job_valid"].verdict_label, "适合投递");

  // Invalid / null map input
  assert.deepEqual(buildListMarks(null), {});
  assert.deepEqual(buildListMarks(undefined), {});
  assert.deepEqual(buildListMarks({}), {});
});

test("buildPageSummary: order matches listJobs and non-done jobs are excluded", () => {
  const listJobs = [
    { platform_job_id: "job_3", title: "职位 3" },
    { platform_job_id: "job_1", title: "职位 1" },
    { platform_job_id: "job_2", title: "职位 2" },
    { platform_job_id: "job_4", title: "职位 4（未判断）" },
    { platform_job_id: "job_5", title: "职位 5（失败）" },
  ];

  const judgements = new Map([
    ["job_1", { judgement: { status: "done", verdict: "apply", judged_at: "2026-09-25T11:00:00Z" } }],
    ["job_2", { judgement: { status: "done", verdict: "try", stale: { job_changed: true } } }],
    ["job_3", { judgement: { status: "done", verdict: "skip" } }],
    ["job_4", { judgement: { status: "running" } }],
    ["job_5", { judgement: { status: "failed" } }],
    ["job_other", { judgement: { status: "done", verdict: "check" } }], // 不在本页列表中
  ]);

  const summary = buildPageSummary(listJobs, judgements);

  // count 仅数有 done 判断记录的本页岗位
  assert.equal(summary.count, 3);
  assert.equal(summary.empty_text, null);

  // 严格保持 listJobs 出现顺序：job_3 -> job_1 -> job_2
  assert.equal(summary.items.length, 3);
  assert.equal(summary.items[0].platform_job_id, "job_3");
  assert.equal(summary.items[0].title, "职位 3");
  assert.equal(summary.items[0].verdict_label, "不建议投");
  assert.equal(summary.items[0].verdict_tone, "red");
  assert.equal(summary.items[0].stale, false);

  assert.equal(summary.items[1].platform_job_id, "job_1");
  assert.equal(summary.items[1].title, "职位 1");
  assert.equal(summary.items[1].verdict_label, "适合投递");
  assert.equal(summary.items[1].verdict_tone, "green");
  assert.equal(summary.items[1].judged_at, "2026-09-25T11:00:00Z");

  assert.equal(summary.items[2].platform_job_id, "job_2");
  assert.equal(summary.items[2].title, "职位 2");
  assert.equal(summary.items[2].verdict_label, "可以一试");
  assert.equal(summary.items[2].verdict_tone, "blue");
  assert.equal(summary.items[2].stale, true);
});

test("buildPageSummary: empty state copy when no judged jobs exist", () => {
  // 1. listJobs 为空
  const emptySummary1 = buildPageSummary([], new Map());
  assert.equal(emptySummary1.count, 0);
  assert.deepEqual(emptySummary1.items, []);
  assert.equal(emptySummary1.empty_text, "本页没有已判断过的岗位");

  // 2. listJobs 有岗位但都未判断或非 done
  const listJobs = [
    { platform_job_id: "j1", title: "前端开发" },
    { platform_job_id: "j2", title: "后端开发" },
  ];
  const judgements = new Map([
    ["j1", { judgement: { status: "running" } }],
    ["j2", { judgement: null }],
  ]);

  const emptySummary2 = buildPageSummary(listJobs, judgements);
  assert.equal(emptySummary2.count, 0);
  assert.deepEqual(emptySummary2.items, []);
  assert.equal(emptySummary2.empty_text, "本页没有已判断过的岗位");

  // 3. null / undefined 输入容错
  const nullSummary = buildPageSummary(null, null);
  assert.equal(nullSummary.count, 0);
  assert.deepEqual(nullSummary.items, []);
  assert.equal(nullSummary.empty_text, "本页没有已判断过的岗位");
});

test("buildPageSummary: handles legacy verdicts and title fallbacks", () => {
  const listJobs = [
    { platform_job_id: "legacy_1", title: "职位 A" },
    { platform_job_id: "legacy_2", title: "" }, // title 空，从 entry 提取
  ];

  const judgements = new Map([
    [
      "legacy_1",
      {
        judgement: { status: "done", verdict: "fit" },
      },
    ],
    [
      "legacy_2",
      {
        title: "职位 B (from entry)",
        judgement: { status: "done", verdict: "unsure" },
      },
    ],
  ]);

  const summary = buildPageSummary(listJobs, judgements);
  assert.equal(summary.count, 2);
  assert.equal(summary.items[0].verdict_label, "适合投递");
  assert.equal(summary.items[0].verdict_tone, "green");
  assert.equal(summary.items[0].title, "职位 A");

  assert.equal(summary.items[1].verdict_label, "需要确认");
  assert.equal(summary.items[1].verdict_tone, "yellow");
  assert.equal(summary.items[1].title, "职位 B (from entry)");
});

test("resolveJobStatus: maps valid statuses and handles null/empty/invalid input", () => {
  // 1. 字符串输入映射
  assert.deepEqual(resolveJobStatus("saved"), {
    status_key: "saved",
    status_label: "收藏",
    status_tone: "saved",
  });
  assert.deepEqual(resolveJobStatus("applied"), {
    status_key: "applied",
    status_label: "已投递",
    status_tone: "applied",
  });
  assert.deepEqual(resolveJobStatus("skipped"), {
    status_key: "skipped",
    status_label: "不考虑",
    status_tone: "skipped",
  });

  // 2. 对象结构输入映射 { status: "..." }
  assert.deepEqual(resolveJobStatus({ status: "saved" }), {
    status_key: "saved",
    status_label: "收藏",
    status_tone: "saved",
  });
  assert.deepEqual(resolveJobStatus({ status: "applied" }), {
    status_key: "applied",
    status_label: "已投递",
    status_tone: "applied",
  });
  assert.deepEqual(resolveJobStatus({ status: "skipped" }), {
    status_key: "skipped",
    status_label: "不考虑",
    status_tone: "skipped",
  });

  // 3. 空值与未知状态输入
  assert.deepEqual(resolveJobStatus(null), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
  assert.deepEqual(resolveJobStatus(undefined), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
  assert.deepEqual(resolveJobStatus(""), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
  assert.deepEqual(resolveJobStatus("unknown_status"), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
  assert.deepEqual(resolveJobStatus({ status: null }), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
  assert.deepEqual(resolveJobStatus({}), {
    status_key: null,
    status_label: null,
    status_tone: null,
  });
});

test("buildListMarks: generates status fields for cards with verdict, screen hints, or status-only", () => {
  const judgements = new Map([
    // 1. 已判断岗位 + 投递状态
    [
      "job_verdict_and_status",
      {
        judgement: {
          status: "done",
          verdict: "apply",
          judged_at: "2026-09-29T10:00:00Z",
        },
        my_status: "saved",
      },
    ],
    // 2. 粗筛提示岗位 + 投递状态 (对象形态)
    [
      "job_hint_and_status",
      {
        screen_hints: [{ text: "学历不符" }],
        my_status: { status: "applied" },
      },
    ],
    // 3. 仅有投递状态（未判断、无粗筛提示）
    [
      "job_status_only",
      {
        my_status: "skipped",
      },
    ],
    // 4. 无状态、无结论、无提示（不应生成标记）
    [
      "job_empty",
      {},
    ],
    // 5. 空状态、无结论、无提示（不应生成标记）
    [
      "job_null_status",
      {
        my_status: null,
      },
    ],
    // 6. 已判断岗位 + 空状态（带有 my_status: null 键）
    [
      "job_verdict_null_status",
      {
        judgement: {
          status: "done",
          verdict: "skip",
          judged_at: "2026-09-29T10:05:00Z",
        },
        my_status: null,
      },
    ],
  ]);

  const marks = buildListMarks(judgements);

  // 验证 1：结论 + 状态共存
  assert.ok(marks["job_verdict_and_status"]);
  assert.equal(marks["job_verdict_and_status"].verdict_label, "适合投递");
  assert.equal(marks["job_verdict_and_status"].verdict_tone, "green");
  assert.equal(marks["job_verdict_and_status"].status_key, "saved");
  assert.equal(marks["job_verdict_and_status"].status_label, "收藏");
  assert.equal(marks["job_verdict_and_status"].status_tone, "saved");

  // 验证 2：粗筛提示 + 状态共存
  assert.ok(marks["job_hint_and_status"]);
  assert.equal(marks["job_hint_and_status"].is_hint, true);
  assert.equal(marks["job_hint_and_status"].verdict_label, "粗筛：学历不符");
  assert.equal(marks["job_hint_and_status"].status_key, "applied");
  assert.equal(marks["job_hint_and_status"].status_label, "已投递");
  assert.equal(marks["job_hint_and_status"].status_tone, "applied");

  // 验证 3：仅有状态的卡片（verdict_label 为 null）
  assert.ok(marks["job_status_only"]);
  assert.equal(marks["job_status_only"].verdict_label, null);
  assert.equal(marks["job_status_only"].verdict_tone, null);
  assert.equal(marks["job_status_only"].status_key, "skipped");
  assert.equal(marks["job_status_only"].status_label, "不考虑");
  assert.equal(marks["job_status_only"].status_tone, "skipped");

  // 验证 4 & 5：无状态且无结论的岗位不生成标记
  assert.equal(marks["job_empty"], undefined);
  assert.equal(marks["job_null_status"], undefined);

  // 验证 6：已判断但状态为空
  assert.ok(marks["job_verdict_null_status"]);
  assert.equal(marks["job_verdict_null_status"].verdict_label, "不建议投");
  assert.equal(marks["job_verdict_null_status"].status_key, null);
  assert.equal(marks["job_verdict_null_status"].status_label, null);
});

test("buildListMarks: 4-tier priority (done > prejudge > hint > status) and prejudge marks (FR-012, FR-013)", () => {
  const judgements = new Map([
    // 1. 已有正式 done 结论 + 预判 + 状态：正式结论覆盖预判
    [
      "job_tier1_over_prejudge",
      {
        judgement: { status: "done", verdict: "apply", judged_at: "2026-09-30T10:00:00Z" },
        my_status: "saved",
      },
    ],
    // 2. 无正式判断 + 有预判 + 有粗筛提示 + 有状态：预判覆盖粗筛提示
    [
      "job_tier2_prejudge_over_hint",
      {
        screen_hints: [{ type: "salary", text: "薪资偏低" }],
        my_status: "applied",
      },
    ],
    // 3. 无正式判断 + 无预判 + 有粗筛提示 + 有状态：粗筛提示覆盖仅状态
    [
      "job_tier3_hint_over_status",
      {
        screen_hints: [{ type: "degree", text: "学历不符" }],
        my_status: "skipped",
      },
    ],
    // 4. 仅有状态
    [
      "job_tier4_status_only",
      {
        my_status: "saved",
      },
    ],
  ]);

  const listPrejudge = new Map([
    [
      "job_tier1_over_prejudge",
      { level: "skip", reason: "列表看似不符但详情页正式判断为适合" },
    ],
    [
      "job_tier2_prejudge_over_hint",
      { level: "open", reason: "方向高度匹配，值得深入看" },
    ],
    [
      "job_prejudge_neutral",
      { level: "neutral", reason: "条件一般" },
    ],
  ]);

  const marks = buildListMarks(judgements, listPrejudge);

  // Tier 1: 正式判断覆盖预判
  const m1 = marks["job_tier1_over_prejudge"];
  assert.ok(m1);
  assert.equal(m1.is_prejudge, undefined);
  assert.equal(m1.verdict_label, "适合投递");
  assert.equal(m1.verdict_tone, "green");
  assert.equal(m1.status_key, "saved");

  // Tier 2: 预判覆盖粗筛提示，并保留状态
  const m2 = marks["job_tier2_prejudge_over_hint"];
  assert.ok(m2);
  assert.equal(m2.is_prejudge, true);
  assert.equal(m2.type, "prejudge");
  assert.equal(m2.verdict_label, "预判·值得点开");
  assert.equal(m2.verdict_tone, "open");
  assert.equal(m2.reason, "方向高度匹配，值得深入看");
  assert.equal(m2.status_key, "applied");
  assert.equal(m2.status_label, "已投递");

  // Prejudge neutral
  const mNeutral = marks["job_prejudge_neutral"];
  assert.ok(mNeutral);
  assert.equal(mNeutral.is_prejudge, true);
  assert.equal(mNeutral.verdict_label, "预判·一般");
  assert.equal(mNeutral.verdict_tone, "neutral");
  assert.equal(mNeutral.reason, "条件一般");

  // Tier 3: 粗筛提示覆盖仅状态
  const m3 = marks["job_tier3_hint_over_status"];
  assert.ok(m3);
  assert.equal(m3.is_hint, true);
  assert.equal(m3.verdict_label, "粗筛：学历不符");
  assert.equal(m3.status_key, "skipped");

  // Tier 4: 仅状态
  const m4 = marks["job_tier4_status_only"];
  assert.ok(m4);
  assert.equal(m4.verdict_label, null);
  assert.equal(m4.status_key, "saved");
  assert.equal(m4.status_label, "收藏");
});
