import {
  VERDICT_LABELS,
  VERDICT_TONES,
  LEGACY_VERDICT_MAP,
} from "./taxonomy.js";
export { shouldKeepPolling } from "./scheduler.js";

/**
 * contracts/page-reader.md §3: VIEW_STATES 16 种状态（新增 reading）
 */
export const VIEW_STATES = [
  "jet_down",
  "unpaired",
  "no_profile",
  "no_llm_key",
  "list_only",
  "reading",
  "judging",
  "done_apply",
  "done_try",
  "done_check",
  "done_skip",
  "stale",
  "failed",
  "quota_exhausted",
  "interrupted",
  "unrecognized",
  "unsupported_page",
];

export const LABELS = {
  jet_down: "Jet 未运行",
  unpaired: "未配对",
  no_profile: "请先设置画像",
  no_llm_key: "请先在设置页填写 DeepSeek API Key",
  list_only: "仅列表信息",
  reading: "正在读取页面…",
  judging: "判断中",
  done_apply: "适合投递",
  done_try: "可以一试",
  done_check: "需要确认",
  done_skip: "不建议投",
  stale: "可能过时",
  failed: "判断失败",
  quota_exhausted: "今日判断额度已用完，明天 0 点恢复",
  interrupted: "判断中断",
  unrecognized: "页面无法识别",
  unsupported_page: "此页暂不支持读取",
};

/**
 * 将 Jet 客户端调用结果或错误对象映射为 VIEW_STATES 之一。
 * 保证非预期值一律返回 "failed"，绝不返回 "done_skip"。
 */
export function fromJetError(result) {
  if (result && result.viewState && VIEW_STATES.includes(result.viewState)) {
    return result.viewState;
  }
  return "failed";
}

/**
 * 将 Judgement 对象映射为 view_state 字符串。
 * 任何非 done 状态、任何错误都绝不返回 done_skip。
 */
export function fromJudgement(judgement) {
  if (!judgement) {
    return null;
  }
  const status = judgement.status;
  if (status === "queued" || status === "running") {
    return "judging";
  }
  if (status === "done") {
    const isStale = Boolean(
      judgement.stale &&
        (judgement.stale.job_changed ||
          judgement.stale.profile_changed ||
          judgement.stale.method_changed)
    );
    if (isStale) {
      return "stale";
    }

    let verdict = judgement.verdict;
    if (LEGACY_VERDICT_MAP[verdict]) {
      verdict = LEGACY_VERDICT_MAP[verdict];
    }
    if (verdict === "apply") {
      return "done_apply";
    }
    if (verdict === "try") {
      return "done_try";
    }
    if (verdict === "check") {
      return "done_check";
    }
    if (verdict === "skip") {
      return "done_skip";
    }
    return "failed";
  }
  if (status === "failed") {
    if (judgement.error && judgement.error.includes("未配置 API Key")) {
      return "no_llm_key";
    }
    return "failed";
  }
  if (status === "quota_exhausted") {
    return "quota_exhausted";
  }
  if (status === "interrupted") {
    if (judgement.error && judgement.error.includes("未配置 API Key")) {
      return "no_llm_key";
    }
    return "interrupted";
  }
  return "failed";
}

/**
 * 从 observations / judgements 响应和 platformJobId 解析 view_state。
 */
export function fromObservation(response, platformJobId) {
  if (!response) {
    return "failed";
  }
  if (response.notice === "no_profile") {
    return "no_profile";
  }
  if (response.notice === "no_llm_key") {
    return "no_llm_key";
  }
  const jobEntry = response.jobs?.[platformJobId];
  if (!jobEntry) {
    return "failed";
  }
  if (jobEntry.completeness === "list_only") {
    return "list_only";
  }
  if (jobEntry.judgement) {
    const state = fromJudgement(jobEntry.judgement);
    return state || "failed";
  }
  return "failed";
}

/**
 * 判定岗位卡片或侧边栏是否显示简历建议的纯函数 (FR-006, FR-007, US1)
 *
 * 显示条件：
 * 1. judgement 存在且为对象；
 * 2. 状态必须为已完成（judgement.status === "done"）；
 * 3. 结论不是"不建议投"（verdict !== "skip" 且 !== "unfit"；verdict_label !== "不建议投"；view_state !== "done_skip"）；
 * 4. 不是规则判断（source !== "rule"）；
 * 5. judgement.resume_suggestion 含非空 name 与 reason。
 * 其他情况（无建议、规则判断、旧判断、字段为空等）一律返回 false。
 *
 * @param {object|null|undefined} judgement
 * @returns {boolean}
 */
export function shouldShowResumeSuggestion(judgement) {
  if (!judgement || typeof judgement !== "object") {
    return false;
  }
  if (judgement.status !== "done") {
    return false;
  }
  if (judgement.source === "rule") {
    return false;
  }
  let verdict = judgement.verdict;
  if (verdict && LEGACY_VERDICT_MAP[verdict]) {
    verdict = LEGACY_VERDICT_MAP[verdict];
  }
  if (verdict === "skip" || verdict === "unfit") {
    return false;
  }
  if (judgement.view_state === "done_skip") {
    return false;
  }
  if (judgement.verdict_label === "不建议投") {
    return false;
  }
  const suggestion = judgement.resume_suggestion;
  if (!suggestion || typeof suggestion !== "object") {
    return false;
  }
  const name =
    typeof suggestion.name === "string" ? suggestion.name.trim() : "";
  const reason =
    typeof suggestion.reason === "string" ? suggestion.reason.trim() : "";
  if (!name || !reason) {
    return false;
  }
  return true;
}

/**
 * 给内容脚本显示用的纯数据描述对象。
 */
export function describe(judgement, viewState, options = {}) {
  // 复核（B 给出适合投递 / 可以一试后，用开启思考的模型再判一次）进行中：
  // 兼容旧数据：running 且 review.outcome === "pending" 显示"复核中"；
  // 否则判断中且 origin === "auto_refresh" → label "更新中"。
  // 状态 failed 且 replacing 非空 → label "更新失败"（仍是 failed 的动作 retry）。
  let statusLabel = LABELS[viewState] || "";
  if (viewState === "judging") {
    if (judgement?.status === "running" && judgement?.review?.outcome === "pending") {
      statusLabel = "复核中";
    } else if (judgement?.origin === "auto_refresh") {
      statusLabel = "更新中";
    }
  } else if (viewState === "failed" && judgement?.replacing) {
    statusLabel = "更新失败";
  }
  // label 是状态文字；用户标注放在 user_label（两者不能共用一个字段）
  const label = statusLabel;
  const user_label = judgement?.label && typeof judgement.label === "object" ? judgement.label : null;

  const isDone =
    viewState === "done_apply" ||
    viewState === "done_try" ||
    viewState === "done_check" ||
    viewState === "done_skip" ||
    viewState === "stale";

  let verdict_label = null;
  let verdict_tone = null;
  let verdict = null;
  let model_verdict = null;
  let verdict_overridden = false;
  if (isDone) {
    let rawVerdict = judgement?.verdict;
    if (rawVerdict) {
      verdict = LEGACY_VERDICT_MAP[rawVerdict] || rawVerdict;
    }
    if (judgement?.model_verdict !== undefined && judgement?.model_verdict !== null) {
      let rawMv = judgement.model_verdict;
      model_verdict = LEGACY_VERDICT_MAP[rawMv] || rawMv;
    } else {
      model_verdict = verdict;
    }
    verdict_overridden = Boolean(judgement?.verdict_overridden);
    if (verdict && VERDICT_LABELS[verdict]) {
      verdict_label = VERDICT_LABELS[verdict];
      verdict_tone = VERDICT_TONES[verdict];
    }
  }

  const isReviewPending = Boolean(
    judgement?.review && judgement.review.outcome === "pending"
  );
  const review_pending = isReviewPending;
  const review_note = isReviewPending ? "复核中，结论可能下调" : null;

  const reasons = Array.isArray(judgement?.reasons) ? [...judgement.reasons] : [];
  const facts = judgement?.facts || null;
  const derivation = Array.isArray(judgement?.derivation) ? [...judgement.derivation] : [];
  const prompt_version = judgement?.prompt_version || null;
  const judged_at = judgement?.judged_at || null;

  const verdict_reason = judgement?.verdict_reason || null;

  let summary_reason = null;
  if (isDone) {
    if (typeof judgement?.verdict_reason === "string" && judgement.verdict_reason.trim().length > 0) {
      summary_reason = judgement.verdict_reason.trim();
    } else if (derivation.length > 0 && typeof derivation[0] === "string" && derivation[0].trim().length > 0) {
      summary_reason = derivation[0].trim();
    } else if (reasons.length > 0 && typeof reasons[0] === "string" && reasons[0].trim().length > 0) {
      summary_reason = reasons[0].trim();
    }
  }

  const stale_reasons = [];
  if (judgement?.stale?.job_changed) {
    stale_reasons.push("岗位已变");
  }
  if (judgement?.stale?.profile_changed) {
    stale_reasons.push("画像已变");
  }
  if (judgement?.stale?.method_changed) {
    stale_reasons.push("判断方式已更新");
  }

  const error = judgement?.error || null;
  const salary_note = judgement?.salary_visible === false ? "薪资不可见" : null;

  const risk_signals = Array.isArray(judgement?.facts?.risk_signals)
    ? [...judgement.facts.risk_signals]
    : [];
  const has_risk = risk_signals.length > 0;

  const hr_questions = Array.isArray(judgement?.hr_questions)
    ? [...judgement.hr_questions]
    : [];

  let action = null;
  if (viewState === "stale") {
    action = "rejudge";
  } else if (
    viewState === "done_apply" ||
    viewState === "done_try" ||
    viewState === "done_check" ||
    viewState === "done_skip"
  ) {
    action = "force_rejudge";
  } else if (
    viewState === "failed" ||
    viewState === "interrupted" ||
    viewState === "quota_exhausted"
  ) {
    action = "retry";
  }

  let replacing = null;
  if (judgement?.replacing && typeof judgement.replacing === "object") {
    let rVerdict = judgement.replacing.verdict;
    if (LEGACY_VERDICT_MAP[rVerdict]) {
      rVerdict = LEGACY_VERDICT_MAP[rVerdict];
    }
    const rTone = (rVerdict && VERDICT_TONES[rVerdict]) || null;
    const rLabel =
      judgement.replacing.verdict_label ||
      (rVerdict && VERDICT_LABELS[rVerdict]) ||
      null;
    replacing = {
      ...judgement.replacing,
      ...(rVerdict ? { verdict: rVerdict } : {}),
      ...(rLabel ? { verdict_label: rLabel } : {}),
      verdict_tone: rTone,
    };
  }

  const notice_text =
    options?.notice === "auto_refresh_quota"
      ? "额度已用完，未自动更新（明天 0 点恢复）"
      : null;

  const source = judgement?.source || null;

  let resume_suggestion = null;
  if (shouldShowResumeSuggestion(judgement)) {
    resume_suggestion = judgement.resume_suggestion;
  }

  return {
    view_state: viewState,
    status: judgement?.status || null,
    verdict,
    model_verdict,
    verdict_overridden,
    label,
    status_label: statusLabel,
    user_label,
    verdict_label,
    verdict_tone,
    verdict_reason,
    summary_reason,
    facts,
    derivation,
    prompt_version,
    reasons,
    judged_at,
    stale_reasons,
    error,
    salary_note,
    risk_signals,
    has_risk,
    hr_questions,
    action,
    replacing,
    notice_text,
    review_pending,
    review_note,
    source,
    resume_suggestion,
  };
}
