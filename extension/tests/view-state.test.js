import test from "node:test";
import { shouldKeepPolling as schedulerShouldKeepPolling } from "../src/scheduler.js";
import assert from "node:assert/strict";
import {
  VIEW_STATES,
  LABELS,
  fromJetError,
  fromJudgement,
  fromObservation,
  describe,
  shouldKeepPolling,
} from "../src/view-state.js";

test("view-state definitions and labels completeness", () => {
  // 新增 no_llm_key 后共有 17 种状态
  assert.equal(VIEW_STATES.length, 17);

  for (const state of VIEW_STATES) {
    assert.ok(LABELS[state], `Missing label for state: ${state}`);
    assert.equal(typeof LABELS[state], "string");
    assert.ok(LABELS[state].length > 0);
  }
});

test("fromJetError behavior", () => {
  assert.equal(fromJetError({ viewState: "jet_down" }), "jet_down");
  assert.equal(fromJetError({ viewState: "unpaired" }), "unpaired");
  assert.equal(fromJetError({ viewState: "no_profile" }), "no_profile");
  assert.equal(fromJetError({ viewState: "reading" }), "reading");

  assert.equal(fromJetError({ viewState: "invalid_state_xyz" }), "failed");
  assert.equal(fromJetError(null), "failed");
  assert.equal(fromJetError(undefined), "failed");
  assert.equal(fromJetError({}), "failed");

  assert.notEqual(fromJetError({ viewState: "error" }), "done_skip");
});

test("fromJudgement status mappings", () => {
  assert.equal(fromJudgement(null), null);
  assert.equal(fromJudgement(undefined), null);

  assert.equal(fromJudgement({ status: "queued" }), "judging");
  assert.equal(fromJudgement({ status: "running" }), "judging");

  // done + v4 四档结论 (apply / try / check / skip)
  assert.equal(fromJudgement({ status: "done", verdict: "apply" }), "done_apply");
  assert.equal(fromJudgement({ status: "done", verdict: "try" }), "done_try");
  assert.equal(fromJudgement({ status: "done", verdict: "check" }), "done_check");
  assert.equal(fromJudgement({ status: "done", verdict: "skip" }), "done_skip");

  // done + 旧版结论映射 (fit -> apply, unsure -> check, unfit -> skip)
  assert.equal(fromJudgement({ status: "done", verdict: "fit" }), "done_apply");
  assert.equal(fromJudgement({ status: "done", verdict: "unsure" }), "done_check");
  assert.equal(fromJudgement({ status: "done", verdict: "unfit" }), "done_skip");
  assert.equal(fromJudgement({ status: "done", verdict: null }), "failed");

  // done + stale
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "apply",
      stale: { job_changed: true, profile_changed: false },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "fit",
      stale: { job_changed: true, profile_changed: false },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "skip",
      stale: { job_changed: false, profile_changed: true },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "unfit",
      stale: { job_changed: false, profile_changed: true },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "try",
      stale: { job_changed: true, profile_changed: true },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "check",
      stale: { method_changed: true },
    }),
    "stale",
  );
  assert.equal(
    fromJudgement({
      status: "done",
      verdict: "unsure",
      stale: { method_changed: true },
    }),
    "stale",
  );

  // other statuses
  assert.equal(fromJudgement({ status: "failed" }), "failed");
  assert.equal(fromJudgement({ status: "failed", error: "未配置 API Key" }), "no_llm_key");
  assert.equal(fromJudgement({ status: "quota_exhausted" }), "quota_exhausted");
  assert.equal(fromJudgement({ status: "interrupted" }), "interrupted");
  assert.equal(fromJudgement({ status: "interrupted", error: "未配置 API Key" }), "no_llm_key");
  assert.equal(fromJudgement({ status: "unexpected_status" }), "failed");
});

test("fromJudgement hard requirement: non-done never returns done_skip", () => {
  const nonDoneStatuses = [
    "queued",
    "running",
    "failed",
    "quota_exhausted",
    "interrupted",
    "reading",
    "random_error",
    null,
    undefined,
  ];

  const verdicts = [
    "apply",
    "try",
    "check",
    "skip",
    "fit",
    "unsure",
    "unfit",
    null,
    "unknown",
  ];
  const stales = [
    null,
    { job_changed: false, profile_changed: false, method_changed: false },
    { job_changed: true, profile_changed: false, method_changed: false },
    { job_changed: false, profile_changed: true, method_changed: false },
    { job_changed: false, profile_changed: false, method_changed: true },
  ];

  for (const status of nonDoneStatuses) {
    for (const verdict of verdicts) {
      for (const stale of stales) {
        const result = fromJudgement({ status, verdict, stale });
        assert.notEqual(
          result,
          "done_skip",
          `Status ${status} with verdict ${verdict} must NEVER return done_skip`,
        );
      }
    }
  }

  // Also check done with stale does not return done_skip even if verdict is skip or unfit
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "skip",
      stale: { job_changed: true, profile_changed: false },
    }),
    "done_skip",
  );
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "unfit",
      stale: { job_changed: true, profile_changed: false },
    }),
    "done_skip",
  );

  // Also check done with check / try / apply never returns done_skip
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "check",
      stale: { job_changed: false, profile_changed: false },
    }),
    "done_skip",
  );
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "unsure",
      stale: { job_changed: false, profile_changed: false },
    }),
    "done_skip",
  );
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "try",
      stale: { job_changed: false, profile_changed: false },
    }),
    "done_skip",
  );
  assert.notEqual(
    fromJudgement({
      status: "done",
      verdict: "apply",
      stale: { job_changed: false, profile_changed: false },
    }),
    "done_skip",
  );
});

test("fromObservation mappings", () => {
  assert.equal(fromObservation(null, "job_1"), "failed");
  assert.equal(fromObservation({}, "job_1"), "failed");

  // notice === "no_profile"
  assert.equal(fromObservation({ notice: "no_profile" }, "job_1"), "no_profile");
  assert.equal(
    fromObservation({ notice: "no_profile", jobs: { job_1: { completeness: "full" } } }, "job_1"),
    "no_profile",
  );

  // notice === "no_llm_key"
  assert.equal(fromObservation({ notice: "no_llm_key" }, "job_1"), "no_llm_key");
  assert.equal(
    fromObservation({ notice: "no_llm_key", jobs: { job_1: { completeness: "full" } } }, "job_1"),
    "no_llm_key",
  );

  // completeness list_only
  assert.equal(
    fromObservation({ jobs: { job_1: { completeness: "list_only" } } }, "job_1"),
    "list_only",
  );

  // completeness full with judgement (apply / fit)
  const fullApply = {
    jobs: {
      job_1: {
        completeness: "full",
        judgement: { status: "done", verdict: "apply", stale: { job_changed: false, profile_changed: false } },
      },
    },
  };
  assert.equal(fromObservation(fullApply, "job_1"), "done_apply");

  const fullFit = {
    jobs: {
      job_1: {
        completeness: "full",
        judgement: { status: "done", verdict: "fit", stale: { job_changed: false, profile_changed: false } },
      },
    },
  };
  assert.equal(fromObservation(fullFit, "job_1"), "done_apply");

  // completeness full with null judgement and no notice -> failed
  const fullNullJudgement = {
    jobs: {
      job_1: {
        completeness: "full",
        judgement: null,
      },
    },
  };
  assert.equal(fromObservation(fullNullJudgement, "job_1"), "failed");

  // job not in response -> failed
  assert.equal(fromObservation({ jobs: { job_2: { completeness: "full" } } }, "job_1"), "failed");
});

test("describe pure data structure and branches", () => {
  // 1. done_apply
  const applyDesc = describe(
    {
      status: "done",
      verdict: "apply",
      reasons: ["方向匹配", "薪资符合"],
      judged_at: "2026-09-24T12:00:00Z",
      stale: { job_changed: false, profile_changed: false },
      salary_visible: true,
    },
    "done_apply",
  );
  assert.equal(applyDesc.view_state, "done_apply");
  assert.equal(applyDesc.label, "适合投递");
  assert.equal(applyDesc.verdict_label, "适合投递");
  assert.equal(applyDesc.verdict_tone, "green");
  assert.deepEqual(applyDesc.reasons, ["方向匹配", "薪资符合"]);
  assert.equal(applyDesc.judged_at, "2026-09-24T12:00:00Z");
  assert.deepEqual(applyDesc.stale_reasons, []);
  assert.equal(applyDesc.salary_note, null);
  assert.equal(applyDesc.action, "force_rejudge");

  // 1b. done_apply with legacy fit verdict
  const fitDesc = describe(
    {
      status: "done",
      verdict: "fit",
      reasons: ["方向匹配"],
      salary_visible: true,
    },
    "done_apply",
  );
  assert.equal(fitDesc.verdict_label, "适合投递");
  assert.equal(fitDesc.verdict_tone, "green");

  // 2. done_skip with salary not visible
  const skipDesc = describe(
    {
      status: "done",
      verdict: "skip",
      reasons: ["城市不符"],
      judged_at: "2026-09-24T12:00:00Z",
      salary_visible: false,
    },
    "done_skip",
  );
  assert.equal(skipDesc.view_state, "done_skip");
  assert.equal(skipDesc.label, "不建议投");
  assert.equal(skipDesc.verdict_label, "不建议投");
  assert.equal(skipDesc.verdict_tone, "red");
  assert.equal(skipDesc.salary_note, "薪资不可见");
  assert.equal(skipDesc.action, "force_rejudge");

  // 2b. done_skip with legacy unfit verdict
  const unfitDesc = describe(
    {
      status: "done",
      verdict: "unfit",
      reasons: ["城市不符"],
    },
    "done_skip",
  );
  assert.equal(unfitDesc.verdict_label, "不建议投");
  assert.equal(unfitDesc.verdict_tone, "red");

  // 3. done_check (v4 check & legacy unsure)
  const checkDesc = describe(
    {
      status: "done",
      verdict: "check",
      reasons: ["销售成分中等，无法完全排除"],
      judged_at: "2026-09-24T12:00:00Z",
      salary_visible: true,
    },
    "done_check",
  );
  assert.equal(checkDesc.view_state, "done_check");
  assert.equal(checkDesc.label, "需要确认");
  assert.equal(checkDesc.verdict_label, "需要确认");
  assert.equal(checkDesc.verdict_tone, "yellow");
  assert.deepEqual(checkDesc.reasons, ["销售成分中等，无法完全排除"]);

  const unsureDesc = describe(
    {
      status: "done",
      verdict: "unsure",
      reasons: ["需要进一步核对"],
    },
    "done_check",
  );
  assert.equal(unsureDesc.verdict_label, "需要确认");
  assert.equal(unsureDesc.verdict_tone, "yellow");

  // 4. done_try (v4 try)
  const tryDesc = describe(
    {
      status: "done",
      verdict: "try",
      reasons: ["方向大体对口但有差距"],
      judged_at: "2026-09-24T12:00:00Z",
      salary_visible: true,
    },
    "done_try",
  );
  assert.equal(tryDesc.view_state, "done_try");
  assert.equal(tryDesc.label, "可以一试");
  assert.equal(tryDesc.verdict_label, "可以一试");
  assert.equal(tryDesc.verdict_tone, "blue");

  // 5. stale carries original verdict_label and stale_reasons, action is rejudge
  const staleDesc = describe(
    {
      status: "done",
      verdict: "apply",
      reasons: ["技术匹配"],
      stale: { job_changed: true, profile_changed: true },
      judged_at: "2026-09-24T10:00:00Z",
    },
    "stale",
  );
  assert.equal(staleDesc.view_state, "stale");
  assert.equal(staleDesc.label, "可能过时");
  assert.equal(staleDesc.verdict_label, "适合投递");
  assert.equal(staleDesc.verdict_tone, "green");
  assert.deepEqual(staleDesc.stale_reasons, ["岗位已变", "画像已变"]);
  assert.equal(staleDesc.action, "rejudge");

  // stale with method_changed
  const methodChangedDesc = describe(
    {
      status: "done",
      verdict: "check",
      stale: { method_changed: true },
    },
    "stale",
  );
  assert.equal(methodChangedDesc.view_state, "stale");
  assert.equal(methodChangedDesc.verdict_label, "需要确认");
  assert.equal(methodChangedDesc.verdict_tone, "yellow");
  assert.deepEqual(methodChangedDesc.stale_reasons, ["判断方式已更新"]);

  // 6. failed / interrupted / quota_exhausted: action is retry, verdict_label is null
  const failedDesc = describe({ status: "failed", error: "LLM timeout", verdict: "skip" }, "failed");
  assert.equal(failedDesc.view_state, "failed");
  assert.equal(failedDesc.label, "判断失败");
  assert.equal(failedDesc.verdict_label, null); // NEVER show verdict when failed!
  assert.equal(failedDesc.verdict_tone, null);
  assert.equal(failedDesc.error, "LLM timeout");
  assert.equal(failedDesc.action, "retry");

  const interruptedDesc = describe({ status: "interrupted", verdict: "apply" }, "interrupted");
  assert.equal(interruptedDesc.verdict_label, null);
  assert.equal(interruptedDesc.verdict_tone, null);
  assert.equal(interruptedDesc.action, "retry");

  const quotaDesc = describe({ status: "quota_exhausted", verdict: "apply" }, "quota_exhausted");
  assert.equal(quotaDesc.verdict_label, null);
  assert.equal(quotaDesc.verdict_tone, null);
  assert.equal(quotaDesc.action, "retry");

  // 7. judging: action is null, verdict_label is null
  const judgingDesc = describe({ status: "running" }, "judging");
  assert.equal(judgingDesc.verdict_label, null);
  assert.equal(judgingDesc.verdict_tone, null);
  assert.equal(judgingDesc.action, null);

  // 8. null judgement (e.g. unsupported_page or jet_down or no_llm_key)
  const noKeyDesc = describe(null, "no_llm_key");
  assert.equal(noKeyDesc.view_state, "no_llm_key");
  assert.equal(noKeyDesc.label, "请先在设置页填写 DeepSeek API Key");
  assert.equal(noKeyDesc.verdict_label, null);
  assert.equal(noKeyDesc.status_label, "请先在设置页填写 DeepSeek API Key");
  assert.equal(noKeyDesc.action, null);

  const nullDesc = describe(null, "unsupported_page");
  assert.equal(nullDesc.view_state, "unsupported_page");
  assert.equal(nullDesc.label, "此页暂不支持读取");
  assert.equal(nullDesc.verdict_label, null);
  assert.equal(nullDesc.verdict_tone, null);
  assert.deepEqual(nullDesc.reasons, []);
  assert.equal(nullDesc.action, null);
});

test("describe outputs facts, derivation, prompt_version, and label", () => {
  const v4Judgement = {
    status: "done",
    verdict: "check",
    prompt_version: "v4",
    facts: {
      summary: { text: "数据分析职责", quotes: [{ text: "负责SQL开发", found: true }] },
      work_type: { value: "数据与技术", subtype: "数据分析", quotes: [] },
      sales_level: { value: "中", signals: [{ signal: "对接客户", quote: { text: "偶尔陪访", found: false } }] },
      experience: { requirement: { text: "本科2年", found: true }, value: "满足", gap: null },
      work_intensity: { value: "未提及", quotes: [] },
    },
    derivation: ["职责为数据分析 ↔ 偏好一致", "销售成分中等 ↔ 需要确认"],
    label: {
      work_type: "数据与技术",
      work_subtype: "数据分析",
      secondary_work_types: null,
      sales_level: "中",
      experience_fit: "满足",
      work_intensity: "未提及",
      overall: "check",
      note: "需要进一步核实",
      corrected: false,
    },
    stale: { method_changed: false },
  };

  const desc = describe(v4Judgement, "done_check");
  assert.equal(desc.view_state, "done_check");
  assert.equal(desc.verdict_label, "需要确认");
  assert.equal(desc.verdict_tone, "yellow");
  assert.deepEqual(desc.facts, v4Judgement.facts);
  assert.deepEqual(desc.derivation, v4Judgement.derivation);
  assert.equal(desc.prompt_version, "v4");
  assert.deepEqual(desc.user_label, v4Judgement.label);
});

test("describe keeps status text in label and the user's annotation in user_label", () => {
  const judgement = {
    status: "done",
    verdict: "skip",
    source: "llm",
    reasons: [],
    judged_at: "2026-09-24T13:30:04Z",
    stale: { job_changed: false, profile_changed: false, method_changed: false },
    error: null,
    salary_visible: true,
    prompt_version: "v4",
    facts: null,
    derivation: ["销售成分高 ↔ 偏好'不想对接客户' → 冲突"],
    label: {
      work_type: "市场与销售",
      work_subtype: "销售与商务拓展",
      secondary_work_types: null,
      sales_level: "高",
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: null,
      corrected: true,
    },
  };
  const desc = describe(judgement, fromJudgement(judgement));
  assert.equal(typeof desc.label, "string");
  assert.equal(desc.label, "不建议投");
  assert.equal(desc.user_label.corrected, true);
  assert.equal(desc.user_label.work_type, "市场与销售");

  const noLabel = describe({ ...judgement, label: null }, "done_skip");
  assert.equal(noLabel.user_label, null);
  assert.equal(noLabel.label, "不建议投");
});

test("describe summary_reason fallback order and non-done handling", () => {
  // 1. verdict_reason -> derivation[0] -> reasons[0] -> null
  const jAll = {
    status: "done",
    verdict: "apply",
    verdict_reason: "职责与画像匹配",
    derivation: ["推导第一条", "推导第二条"],
    reasons: ["理由第一条"],
  };
  const descAll = describe(jAll, "done_apply");
  assert.equal(descAll.verdict_reason, "职责与画像匹配");
  assert.equal(descAll.summary_reason, "职责与画像匹配");

  // 2. verdict_reason missing -> derivation[0]
  const jDeriv = {
    status: "done",
    verdict: "apply",
    derivation: ["白话职责为后端开发 ↔ 偏好一致", "第二条推导"],
    reasons: ["备用理由"],
  };
  const descDeriv = describe(jDeriv, "done_apply");
  assert.equal(descDeriv.verdict_reason, null);
  assert.equal(descDeriv.summary_reason, "白话职责为后端开发 ↔ 偏好一致");

  // 3. derivation missing or empty -> reasons[0]
  const jReasons = {
    status: "done",
    verdict: "skip",
    reasons: ["销售成分过高", "薪资低于预期"],
  };
  const descReasons = describe(jReasons, "done_skip");
  assert.equal(descReasons.verdict_reason, null);
  assert.equal(descReasons.summary_reason, "销售成分过高");

  // 4. all missing -> null
  const jNone = {
    status: "done",
    verdict: "check",
  };
  const descNone = describe(jNone, "done_check");
  assert.equal(descNone.verdict_reason, null);
  assert.equal(descNone.summary_reason, null);

  // 5. non-done state -> summary_reason must be null even if reasons/derivation exist
  const nonDoneStates = [
    "judging",
    "failed",
    "quota_exhausted",
    "interrupted",
    "jet_down",
    "unpaired",
    "no_profile",
    "list_only",
    "reading",
    "unrecognized",
    "unsupported_page",
  ];
  for (const ndState of nonDoneStates) {
    const ndDesc = describe(jAll, ndState);
    assert.equal(
      ndDesc.summary_reason,
      null,
      `State ${ndState} must have summary_reason as null`,
    );
  }

  // 6. stale state has summary_reason
  const staleDesc = describe(jAll, "stale");
  assert.equal(staleDesc.summary_reason, "职责与画像匹配");
});

test("describe outputs risk_signals, has_risk, and hr_questions with defaults", () => {
  // 1. Defaults when judgement is null or empty
  const nullDesc = describe(null, "jet_down");
  assert.deepEqual(nullDesc.risk_signals, []);
  assert.equal(nullDesc.has_risk, false);
  assert.deepEqual(nullDesc.hr_questions, []);

  const emptyDesc = describe({ status: "done", verdict: "apply" }, "done_apply");
  assert.deepEqual(emptyDesc.risk_signals, []);
  assert.equal(emptyDesc.has_risk, false);
  assert.deepEqual(emptyDesc.hr_questions, []);

  // 2. When risk_signals present
  const riskJudgement = {
    status: "done",
    verdict: "skip",
    facts: {
      risk_signals: [
        {
          type: "非法金融",
          description: "按交易日和每手提成，疑似非法期货配资",
          quote: { text: "按每手提成", found: true },
        },
      ],
    },
  };
  const riskDesc = describe(riskJudgement, "done_skip");
  assert.equal(riskDesc.has_risk, true);
  assert.deepEqual(riskDesc.risk_signals, riskJudgement.facts.risk_signals);

  // 3. When hr_questions present
  const hrQuestionsJudgement = {
    status: "done",
    verdict: "try",
    hr_questions: [
      "实际对接银行客户的时间大概占多少？",
      "有没有个人业绩或拉新指标？",
    ],
  };
  const hrDesc = describe(hrQuestionsJudgement, "done_try");
  assert.deepEqual(hrDesc.hr_questions, hrQuestionsJudgement.hr_questions);

  // 4. When facts.risk_signals is empty array
  const emptyRiskFactsDesc = describe(
    { status: "done", verdict: "apply", facts: { risk_signals: [] } },
    "done_apply",
  );
  assert.deepEqual(emptyRiskFactsDesc.risk_signals, []);
  assert.equal(emptyRiskFactsDesc.has_risk, false);
});

test("describe action mapping for done (not stale), stale, and retry states", () => {
  // 1. All non-stale done states -> action: force_rejudge
  for (const doneState of ["done_apply", "done_try", "done_check", "done_skip"]) {
    const desc = describe({ status: "done", verdict: "apply" }, doneState);
    assert.equal(desc.action, "force_rejudge", `State ${doneState} must have action force_rejudge`);
  }

  // 2. stale state -> action: rejudge
  const staleDesc = describe(
    { status: "done", verdict: "apply", stale: { method_changed: true } },
    "stale",
  );
  assert.equal(staleDesc.action, "rejudge");

  // 3. Retry states -> action: retry
  for (const retryState of ["failed", "interrupted", "quota_exhausted"]) {
    const desc = describe({ status: retryState }, retryState);
    assert.equal(desc.action, "retry", `State ${retryState} must have action retry`);
  }

  // 4. Other states -> action: null
  for (const otherState of [
    "judging",
    "jet_down",
    "unpaired",
    "no_profile",
    "list_only",
    "reading",
    "unrecognized",
    "unsupported_page",
  ]) {
    const desc = describe(null, otherState);
    assert.equal(desc.action, null, `State ${otherState} must have action null`);
  }
});

test("running judgement under review shows 复核中; finished review keeps normal labels", () => {
  const reviewing = {
    status: "running",
    verdict: null,
    source: null,
    reasons: [],
    judged_at: null,
    stale: { job_changed: false, profile_changed: false, method_changed: false },
    error: null,
    salary_visible: true,
    review: { outcome: "pending", engine: "deepseek-flash:think" },
  };
  const vs = fromJudgement(reviewing);
  assert.equal(vs, "judging");
  assert.equal(describe(reviewing, vs).label, "复核中");

  const plainRunning = { ...reviewing, review: null };
  assert.equal(describe(plainRunning, fromJudgement(plainRunning)).label, "判断中");

  const downgraded = {
    ...reviewing,
    status: "done",
    verdict: "check",
    source: "llm",
    derivation: ["复核（开启思考）认为应降档：由「可以一试」降为「需要确认」"],
    review: { outcome: "downgraded", first_verdict: "try", review_verdict: "check" },
  };
  const d = describe(downgraded, fromJudgement(downgraded));
  assert.equal(d.label, "需要确认");
  assert.notEqual(fromJudgement(downgraded), "done_skip");
});

test("describe handles auto_refresh origin, replacing, and review priority", () => {
  // 1. origin: auto_refresh when judging -> label "更新中"
  const autoRefreshing = {
    status: "running",
    origin: "auto_refresh",
  };
  const descAuto = describe(autoRefreshing, "judging");
  assert.equal(descAuto.label, "更新中");
  assert.equal(descAuto.status_label, "更新中");

  // 2. review.outcome: "pending" takes priority over auto_refresh -> label "复核中"
  const reviewingAuto = {
    status: "running",
    origin: "auto_refresh",
    review: { outcome: "pending", engine: "deepseek-flash:think" },
  };
  const descRev = describe(reviewingAuto, "judging");
  assert.equal(descRev.label, "复核中");
  assert.equal(descRev.status_label, "复核中");

  // 3. failed state with replacing -> label "更新失败" and action is retry
  const failedWithReplacing = {
    status: "failed",
    origin: "auto_refresh",
    replacing: {
      verdict: "skip",
      verdict_label: "不建议投",
      judged_at: "2026-09-24T10:00:00Z",
    },
  };
  const descFailedRep = describe(failedWithReplacing, "failed");
  assert.equal(descFailedRep.label, "更新失败");
  assert.equal(descFailedRep.status_label, "更新失败");
  assert.equal(descFailedRep.action, "retry");
  assert.deepEqual(descFailedRep.replacing, {
    verdict: "skip",
    verdict_label: "不建议投",
    judged_at: "2026-09-24T10:00:00Z",
    verdict_tone: "red",
  });

  // 4. failed state without replacing -> label "判断失败"
  const failedNormal = {
    status: "failed",
    replacing: null,
  };
  const descFailedNormal = describe(failedNormal, "failed");
  assert.equal(descFailedNormal.label, "判断失败");
  assert.equal(descFailedNormal.replacing, null);

  // 5. replacing with legacy verdict mapping
  const legacyReplacing = {
    status: "running",
    origin: "auto_refresh",
    replacing: {
      verdict: "fit",
      judged_at: "2026-09-24T09:00:00Z",
    },
  };
  const descLegacyRep = describe(legacyReplacing, "judging");
  assert.equal(descLegacyRep.replacing.verdict, "apply");
  assert.equal(descLegacyRep.replacing.verdict_label, "适合投递");
  assert.equal(descLegacyRep.replacing.verdict_tone, "green");

  // 6. notice_text with options.notice === "auto_refresh_quota"
  const descNotice = describe(null, "stale", { notice: "auto_refresh_quota" });
  assert.equal(descNotice.notice_text, "额度已用完，未自动更新（明天 0 点恢复）");

  // 7. notice_text when options.notice is null or different
  const descNoNotice = describe(null, "stale", { notice: "other_notice" });
  assert.equal(descNoNotice.notice_text, null);

  const descEmptyOptions = describe(null, "stale");
  assert.equal(descEmptyOptions.notice_text, null);
});

test("all 10 system/intermediate states labels and describe mapping coverage (T091)", () => {
  const expectedLabels = {
    jet_down: "Jet 未运行",
    unpaired: "未配对",
    no_profile: "请先设置画像",
    list_only: "仅列表信息",
    reading: "正在读取页面…",
    unrecognized: "页面无法识别",
    unsupported_page: "此页暂不支持读取",
    quota_exhausted: "今日判断额度已用完，明天 0 点恢复",
    failed: "判断失败",
    interrupted: "判断中断",
  };

  for (const [state, expectedLabel] of Object.entries(expectedLabels)) {
    assert.equal(LABELS[state], expectedLabel, `Label for ${state} must match`);
    const judgement = state === "failed" ? { status: "failed", error: "连接超时" } : null;
    const desc = describe(judgement, state);
    assert.equal(desc.view_state, state);
    assert.equal(desc.label, expectedLabel);
    assert.equal(desc.status_label, expectedLabel);
    assert.equal(desc.verdict_label, null);
    if (state === "failed") {
      assert.equal(desc.error, "连接超时");
    }
  }
});

test("salary_visible === false always outputs 薪资不可见 across verdicts (T091)", () => {
  const verdicts = ["apply", "try", "check", "skip", "fit", "unsure", "unfit"];
  for (const verdict of verdicts) {
    const desc = describe(
      { status: "done", verdict, salary_visible: false },
      "done_apply",
    );
    assert.equal(desc.salary_note, "薪资不可见");
  }
  const descVisible = describe(
    { status: "done", verdict: "apply", salary_visible: true },
    "done_apply",
  );
  assert.equal(descVisible.salary_note, null);
});

test("done judgement with review.outcome pending outputs verdict view state and review_note", () => {
  const donePendingTry = {
    status: "done",
    verdict: "try",
    source: "llm",
    reasons: ["方向基本匹配"],
    judged_at: "2026-09-25T12:00:00Z",
    salary_visible: true,
    review: { outcome: "pending", engine: "deepseek-flash:think" },
  };

  const vsTry = fromJudgement(donePendingTry);
  assert.equal(vsTry, "done_try");

  const descTry = describe(donePendingTry, vsTry);
  assert.equal(descTry.view_state, "done_try");
  assert.equal(descTry.label, "可以一试");
  assert.equal(descTry.verdict_label, "可以一试");
  assert.equal(descTry.verdict_tone, "blue");
  assert.equal(descTry.review_pending, true);
  assert.equal(descTry.review_note, "复核中，结论可能下调");

  // apply with pending review
  const donePendingApply = {
    status: "done",
    verdict: "apply",
    review: { outcome: "pending" },
  };
  const vsApply = fromJudgement(donePendingApply);
  assert.equal(vsApply, "done_apply");
  const descApply = describe(donePendingApply, vsApply);
  assert.equal(descApply.view_state, "done_apply");
  assert.equal(descApply.label, "适合投递");
  assert.equal(descApply.verdict_label, "适合投递");
  assert.equal(descApply.verdict_tone, "green");
  assert.equal(descApply.review_pending, true);
  assert.equal(descApply.review_note, "复核中，结论可能下调");

  // check with pending review
  const donePendingCheck = {
    status: "done",
    verdict: "check",
    review: { outcome: "pending" },
  };
  const vsCheck = fromJudgement(donePendingCheck);
  assert.equal(vsCheck, "done_check");
  const descCheck = describe(donePendingCheck, vsCheck);
  assert.equal(descCheck.view_state, "done_check");
  assert.equal(descCheck.label, "需要确认");
  assert.equal(descCheck.verdict_label, "需要确认");
  assert.equal(descCheck.review_pending, true);
  assert.equal(descCheck.review_note, "复核中，结论可能下调");

  // when review finished (kept / downgraded)
  const doneKept = {
    status: "done",
    verdict: "try",
    review: { outcome: "kept" },
  };
  const descKept = describe(doneKept, "done_try");
  assert.equal(descKept.review_pending, false);
  assert.equal(descKept.review_note, null);
});

test("view-state 转出的 shouldKeepPolling 就是 scheduler.js 的同一个函数（各状态的断言见 scheduler.test.js）", () => {
  assert.equal(shouldKeepPolling, schedulerShouldKeepPolling);
});

test("describe outputs verdict, model_verdict, and verdict_overridden", () => {
  // 1. Normal done without override
  const normalDesc = describe(
    { status: "done", verdict: "try" },
    "done_try",
  );
  assert.equal(normalDesc.verdict, "try");
  assert.equal(normalDesc.model_verdict, "try");
  assert.equal(normalDesc.verdict_overridden, false);

  // 2. Done with user override
  const overriddenDesc = describe(
    {
      status: "done",
      verdict: "skip",
      model_verdict: "try",
      verdict_overridden: true,
    },
    "done_skip",
  );
  assert.equal(overriddenDesc.verdict, "skip");
  assert.equal(overriddenDesc.model_verdict, "try");
  assert.equal(overriddenDesc.verdict_overridden, true);
  assert.equal(overriddenDesc.verdict_label, "不建议投");

  // 3. Non-done (e.g. running / failed)
  const runningDesc = describe({ status: "running" }, "judging");
  assert.equal(runningDesc.verdict, null);
  assert.equal(runningDesc.model_verdict, null);
  assert.equal(runningDesc.verdict_overridden, false);
});
