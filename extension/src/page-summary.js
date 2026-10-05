import {
  VERDICT_LABELS,
  VERDICT_TONES,
  LEGACY_VERDICT_MAP,
} from "./taxonomy.js";

/**
 * 判断 judgement 是否已过期/可能过时
 */
function isJudgementStale(judgement) {
  if (!judgement) return false;
  if (judgement.stale === true) return true;
  if (judgement.stale && typeof judgement.stale === "object") {
    return Boolean(
      judgement.stale.job_changed ||
      judgement.stale.profile_changed ||
      judgement.stale.method_changed
    );
  }
  return false;
}

/**
 * 规整结论（支持旧版 fit / unsure / unfit 映射）
 */
function normalizeVerdict(rawVerdict) {
  if (!rawVerdict) return null;
  return LEGACY_VERDICT_MAP[rawVerdict] || rawVerdict;
}

/**
 * 格式化粗筛提示小字标签文案（FR-004 / T010 选项 A）
 * 格式："粗筛：提示1 · 提示2"
 *
 * @param {Array<object|string>} screenHints
 * @returns {string}
 */
export function formatHintLabel(screenHints) {
  if (!Array.isArray(screenHints) || screenHints.length === 0) {
    return "";
  }
  const texts = screenHints
    .map((h) => (typeof h === "string" ? h : h?.text))
    .filter((t) => typeof t === "string" && t.trim().length > 0)
    .map((t) => t.trim());

  if (texts.length === 0) {
    return "";
  }
  return "粗筛：" + texts.join(" · ");
}

/**
 * 列表岗位投递状态文案映射表 (FR-005, US2)
 */
export const STATUS_LABELS = {
  saved: "收藏",
  applied: "已投递",
  skipped: "不考虑",
};

/**
 * 规整岗位投递状态纯函数 (FR-005, T010)
 * 从 my_status 生成 status_key 与 status_label；无状态为 null；不触发判断或粗筛
 *
 * @param {object|string|null|undefined} myStatus
 * @returns {{
 *   status_key: "saved" | "applied" | "skipped" | null,
 *   status_label: "收藏" | "已投递" | "不考虑" | null,
 *   status_tone: "saved" | "applied" | "skipped" | null,
 * }}
 */
export function resolveJobStatus(myStatus) {
  let key = null;
  if (myStatus && typeof myStatus === "object" && typeof myStatus.status === "string") {
    key = myStatus.status.trim();
  } else if (typeof myStatus === "string") {
    key = myStatus.trim();
  }
  if (key && STATUS_LABELS[key]) {
    return {
      status_key: key,
      status_label: STATUS_LABELS[key],
      status_tone: key,
    };
  }
  return {
    status_key: null,
    status_label: null,
    status_tone: null,
  };
}

export const PREJUDGE_VERDICT_LABELS = {
  open: "预判·值得点开",
  neutral: "预判·一般",
  skip: "预判·可跳过",
};

/**
 * buildListMarks(listJudgements, listPrejudge)
 * 四级优先级判定链条（T016, contracts/plugin-protocol.md §3）：
 * 1. 正式判断 (done 且有结论)
 * 2. 预判 (prejudge)
 * 3. 粗筛提示 (screen_hints)
 * 4. 仅投递状态 (status)
 */
export function buildListMarks(listJudgements, listPrejudge = null) {
  const marks = {};

  if (listJudgements && listJudgements instanceof Map) {
    for (const [platformJobId, entry] of listJudgements.entries()) {
      if (!platformJobId) continue;
      const judgement = entry?.judgement;
      const statusInfo = resolveJobStatus(entry?.my_status);
      const hasStatus = Boolean(statusInfo.status_key);

      // 1. 已判断完成（status == "done" 且有结论）的岗位照旧优先生成判断标记
      if (judgement && judgement.status === "done") {
        const verdict = normalizeVerdict(judgement.verdict);
        if (verdict && VERDICT_LABELS[verdict]) {
          const mark = {
            verdict_label: VERDICT_LABELS[verdict],
            verdict_tone: VERDICT_TONES[verdict],
            stale: isJudgementStale(judgement),
            judged_at: judgement.judged_at || null,
          };
          if (hasStatus) {
            mark.status_key = statusInfo.status_key;
            mark.status_label = statusInfo.status_label;
            mark.status_tone = statusInfo.status_tone;
          } else if (entry && "my_status" in entry) {
            mark.status_key = null;
            mark.status_label = null;
            mark.status_tone = null;
          }
          marks[platformJobId] = mark;
          continue;
        }
      }

      // 2. 预判标记 (type: "prejudge")
      let prejudgeItem = null;
      if (listPrejudge) {
        prejudgeItem = listPrejudge instanceof Map
          ? listPrejudge.get(platformJobId)
          : listPrejudge[platformJobId];
      }
      if (prejudgeItem && prejudgeItem.level && PREJUDGE_VERDICT_LABELS[prejudgeItem.level]) {
        const mark = {
          type: "prejudge",
          is_prejudge: true,
          verdict_label: PREJUDGE_VERDICT_LABELS[prejudgeItem.level],
          verdict_tone: prejudgeItem.level,
          reason: prejudgeItem.reason || "",
          stale: false,
          judged_at: null,
        };
        if (hasStatus) {
          mark.status_key = statusInfo.status_key;
          mark.status_label = statusInfo.status_label;
          mark.status_tone = statusInfo.status_tone;
        } else if (entry && "my_status" in entry) {
          mark.status_key = null;
          mark.status_label = null;
          mark.status_tone = null;
        }
        marks[platformJobId] = mark;
        continue;
      }

      // 3. 其余岗位若 screen_hints 非空，生成"粗筛提示"标记
      const screenHints = Array.isArray(entry?.screen_hints) ? entry.screen_hints : [];
      if (screenHints.length > 0) {
        const hintLabel = formatHintLabel(screenHints);
        if (hintLabel) {
          const hintTexts = screenHints
            .map((h) => (typeof h === "string" ? h : h?.text))
            .filter((t) => typeof t === "string" && t.trim().length > 0)
            .map((t) => t.trim());

          const mark = {
            type: "hint",
            is_hint: true,
            verdict_label: hintLabel,
            verdict_tone: "slate",
            stale: false,
            judged_at: null,
            hints: hintTexts,
            screen_hints: screenHints,
          };
          if (hasStatus) {
            mark.status_key = statusInfo.status_key;
            mark.status_label = statusInfo.status_label;
            mark.status_tone = statusInfo.status_tone;
          } else if (entry && "my_status" in entry) {
            mark.status_key = null;
            mark.status_label = null;
            mark.status_tone = null;
          }
          marks[platformJobId] = mark;
          continue;
        }
      }

      // 4. 仅有投递状态的岗位（未判断、无预判、无粗筛提示）
      if (hasStatus) {
        marks[platformJobId] = {
          verdict_label: null,
          verdict_tone: null,
          stale: false,
          judged_at: null,
          status_key: statusInfo.status_key,
          status_label: statusInfo.status_label,
          status_tone: statusInfo.status_tone,
        };
      }
    }
  }

  // 针对仅在 listPrejudge 中存在的额外条目（若有）
  if (listPrejudge) {
    const pKeys = listPrejudge instanceof Map ? listPrejudge.keys() : Object.keys(listPrejudge);
    for (const pid of pKeys) {
      if (!marks[pid] && (!listJudgements || !(listJudgements instanceof Map) || !listJudgements.has(pid))) {
        const pItem = listPrejudge instanceof Map ? listPrejudge.get(pid) : listPrejudge[pid];
        if (pItem && pItem.level && PREJUDGE_VERDICT_LABELS[pItem.level]) {
          marks[pid] = {
            type: "prejudge",
            is_prejudge: true,
            verdict_label: PREJUDGE_VERDICT_LABELS[pItem.level],
            verdict_tone: pItem.level,
            reason: pItem.reason || "",
            stale: false,
            judged_at: null,
            status_key: null,
            status_label: null,
            status_tone: null,
          };
        }
      }
    }
  }

  return marks;
}

/**
 * buildPageSummary(listJobs, listJudgements)
 * listJobs 为本页列表岗位（有 platform_job_id、title）
 * listJudgements 为 Map<platform_job_id, {judgement, title?, my_status?, company_name?}>
 * 输出: { count, items: [{platform_job_id, title, verdict_label, verdict_tone, judged_at, stale}], empty_text }
 * 按列表顺序；count 只数有判断记录（done）的岗位；为空时 empty_text = "本页没有已判断过的岗位"
 */
export function buildPageSummary(listJobs, listJudgements) {
  const items = [];
  const seen = new Set();
  const safeListJobs = Array.isArray(listJobs) ? listJobs : [];
  const safeJudgements = listJudgements instanceof Map ? listJudgements : new Map();

  for (const job of safeListJobs) {
    if (!job || !job.platform_job_id) continue;
    const pid = job.platform_job_id;
    if (seen.has(pid)) continue;
    seen.add(pid);

    const entry = safeJudgements.get(pid);
    const judgement = entry?.judgement;
    if (!judgement || judgement.status !== "done") {
      continue;
    }

    const verdict = normalizeVerdict(judgement.verdict);
    if (!verdict || !VERDICT_LABELS[verdict]) {
      continue;
    }

    items.push({
      platform_job_id: pid,
      title: job.title || entry.title || "",
      verdict_label: VERDICT_LABELS[verdict],
      verdict_tone: VERDICT_TONES[verdict],
      judged_at: judgement.judged_at || null,
      stale: isJudgementStale(judgement),
    });
  }

  const count = items.length;
  const empty_text = count === 0 ? "本页没有已判断过的岗位" : null;

  return {
    count,
    items,
    empty_text,
  };
}
