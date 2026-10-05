import {
  VERDICT_LABELS,
  VERDICT_TONES,
  LEGACY_VERDICT_MAP,
} from "./taxonomy.js";
import { toHrNotePayload } from "./label-form.js";

export const STATUS_LABELS = {
  saved: "收藏",
  applied: "已投递",
  skipped: "不考虑",
};

/**
 * 校验 url 是否为合法的 BOSS 直聘原岗位链接。
 * 打开前校验 url 必须以 https://www.zhipin.com/job_detail/ 开头，否则不打开。
 *
 * @param {string} url
 * @returns {boolean}
 */
export function isSafeBossJobUrl(url) {
  if (typeof url !== "string") {
    return false;
  }
  return url.startsWith("https://www.zhipin.com/job_detail/");
}

/**
 * 格式化 ISO 时间字符串为本地时间字符串。
 *
 * @param {string|null} isoString
 * @returns {string}
 */
export function formatTime(isoString) {
  if (!isoString) return "";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return String(isoString);
    return d.toLocaleString();
  } catch {
    return String(isoString);
  }
}

/**
 * 将岗位条目转为视图行数据。
 *
 * @param {object} item
 * @param {string} filter 'all' | 'saved' | 'applied' | 'skipped' | 'recent'
 * @returns {object}
 */
export function toRow(item, filter = "all") {
  const it = item || {};

  const title = it.title || "";

  const companyRaw = it.company_name;
  const companyIsEmpty =
    companyRaw === null ||
    companyRaw === undefined ||
    String(companyRaw).trim() === "";
  const company = companyIsEmpty ? "公司未读取" : String(companyRaw).trim();

  const salaryRaw = it.salary_raw;
  const salaryIsEmpty =
    salaryRaw === null ||
    salaryRaw === undefined ||
    String(salaryRaw).trim() === "";
  const salary = salaryIsEmpty ? "薪资不可见" : String(salaryRaw).trim();

  const city = it.city || "";

  let vKey = it.verdict || null;
  if (vKey && LEGACY_VERDICT_MAP[vKey]) {
    vKey = LEGACY_VERDICT_MAP[vKey];
  }

  const rawLabel =
    it.verdict_label || (vKey ? VERDICT_LABELS[vKey] : null) || null;
  let verdictTone = (vKey && VERDICT_TONES[vKey]) || null;
  let verdictText = "未判断";
  const isStale = Boolean(
    it.stale &&
      (typeof it.stale === "object"
        ? (it.stale.job_changed || it.stale.profile_changed || it.stale.method_changed)
        : it.stale)
  );

  if (rawLabel) {
    verdictText = rawLabel;
    if (isStale) {
      verdictText += "（可能过时）";
    }
  } else {
    verdictTone = "neutral";
  }

  const hrNoteRaw = it.hr_note ?? null;
  const hrNote =
    typeof hrNoteRaw === "string" && hrNoteRaw.trim().length > 0
      ? hrNoteRaw.trim()
      : null;
  const hrNoteRecorded = Boolean(it.hr_note_recorded) || hrNote !== null;
  const hrNoteContent = hrNote || (hrNoteRecorded ? "已记录" : "未记录");
  const hrNoteText = hrNote
    ? `HR 实际情况：${hrNote}`
    : (hrNoteRecorded ? "HR 实际情况：已记录" : "HR 实际情况：未记录");
  const canExpandHrNote = Boolean(hrNote && hrNote.length > 0);

  const statusKey =
    typeof it.my_status === "object" ? it.my_status?.status : it.my_status;
  const statusLabel = (statusKey && STATUS_LABELS[statusKey]) || null;
  const statusUpdatedAtRaw =
    it.status_updated_at ||
    (typeof it.my_status === "object" ? it.my_status?.updated_at : null) ||
    null;
  const statusUpdatedAtText = statusUpdatedAtRaw
    ? formatTime(statusUpdatedAtRaw)
    : "";

  let statusText = "";
  if (statusLabel) {
    statusText = statusUpdatedAtText
      ? `${statusLabel}（${statusUpdatedAtText}）`
      : statusLabel;
  }

  const lastSeenAtRaw = it.last_seen_at || null;
  const lastSeenAtText = lastSeenAtRaw ? formatTime(lastSeenAtRaw) : "";
  const lastSeenLabel = lastSeenAtRaw ? `最后查看：${lastSeenAtText}` : "";

  const url = it.url || "";
  const safeUrl = isSafeBossJobUrl(url);

  return {
    platform_job_id: it.platform_job_id || "",
    title,
    company,
    company_name: company,
    company_is_empty: companyIsEmpty,
    salary,
    salary_raw: salaryRaw,
    salary_is_empty: salaryIsEmpty,
    city,
    verdict: vKey,
    verdict_label: rawLabel || "未判断",
    verdict_text: verdictText,
    verdict_tone: verdictTone,
    stale: isStale,
    stale_style: isStale,
    hr_note: hrNote,
    hr_note_content: hrNoteContent,
    hr_note_recorded: hrNoteRecorded,
    hr_note_text: hrNoteText,
    can_expand_hr_note: canExpandHrNote,
    status: statusKey || null,
    status_label: statusLabel,
    status_updated_at: statusUpdatedAtRaw,
    status_updated_at_text: statusUpdatedAtText,
    status_text: statusText,
    last_seen_at: lastSeenAtRaw,
    last_seen_at_text: lastSeenAtText,
    last_seen_label: lastSeenLabel,
    show_last_seen: filter === "recent",
    url,
    is_safe_url: safeUrl,
  };
}

/**
 * 校验 HR 实际情况编辑输入。
 * 纯空白禁用保存按钮并提示"清空请到岗位卡片操作"。
 * 内容最大不超过 200 字。
 *
 * @param {string|null|undefined} text
 * @returns {{ canSave: boolean, error: string|null, note: string|null }}
 */
export function validateHrNoteEdit(text) {
  if (typeof text !== "string") {
    return {
      canSave: false,
      error: "清空请到岗位卡片操作",
      note: null,
    };
  }
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return {
      canSave: false,
      error: "清空请到岗位卡片操作",
      note: null,
    };
  }
  if (trimmed.length > 200) {
    return {
      canSave: false,
      error: "字数不能超过 200 字",
      note: trimmed.slice(0, 200),
    };
  }
  return {
    canSave: true,
    error: null,
    note: trimmed,
  };
}

/**
 * 计算就地修改状态后的移出标注（纯函数状态机）。
 * 规则：
 * - 改回原状态：标注消失（null）
 * - 「全部岗位」标签（all_jobs）与「最近看过」标签（recent）：改状态不移出、不标注（null）
 * - 「已标记」标签（all）：互换状态不标注（null），取消状态（null）标注"已取消状态，切换标签或刷新后移出本列表"
 * - 具体状态标签（saved / applied / skipped）：
 *   - 改为其他状态：标注"已改为 X，切换标签或刷新后移出本列表"
 *   - 取消状态（null）：标注"已取消状态，切换标签或刷新后移出本列表"
 *   - 改为与当前标签一致：null
 *
 * @param {string|null} initialStatus 原状态 ('saved' | 'applied' | 'skipped' | null)
 * @param {string|null} currentStatus 当前新状态 ('saved' | 'applied' | 'skipped' | null)
 * @param {string} activeFilter 当前选中标签 ('all_jobs' | 'all' | 'saved' | 'applied' | 'skipped' | 'recent')
 * @returns {string|null} 标注文本或 null
 */
export function getStatusRemovalNotice(initialStatus, currentStatus, activeFilter) {
  if (currentStatus === initialStatus) {
    return null;
  }
  if (activeFilter === "all_jobs" || activeFilter === "recent") {
    return null;
  }
  if (activeFilter === "all") {
    if (currentStatus === null) {
      return "已取消状态，切换标签或刷新后移出本列表";
    }
    return null;
  }
  if (currentStatus === activeFilter) {
    return null;
  }
  if (currentStatus === null) {
    return "已取消状态，切换标签或刷新后移出本列表";
  }
  const label = STATUS_LABELS[currentStatus] || currentStatus;
  return `已改为 ${label}，切换标签或刷新后移出本列表`;
}

/**
 * 当行内就地修改状态时，同步更新各标签数字纯函数。
 *
 * @param {object} counts 当前 counts 对象 { all_jobs, all, saved, applied, skipped, recent }
 * @param {string|null} oldStatus 变化前状态
 * @param {string|null} newStatus 变化后状态
 * @returns {object} 新 counts 对象
 */
export function updateCountsOnStatusChange(counts, oldStatus, newStatus) {
  if (!counts || oldStatus === newStatus) {
    return counts ? { ...counts } : {};
  }
  const next = { ...counts };

  if (oldStatus && next[oldStatus] !== undefined) {
    next[oldStatus] = Math.max(0, next[oldStatus] - 1);
  }
  if (newStatus && next[newStatus] !== undefined) {
    next[newStatus] = (next[newStatus] || 0) + 1;
  }

  // 更新 all (已标记)
  if (oldStatus === null && newStatus !== null) {
    next.all = (next.all || 0) + 1;
  } else if (oldStatus !== null && newStatus === null) {
    next.all = Math.max(0, (next.all || 0) - 1);
  }

  return next;
}

/**
 * 格式化搜索结果总数超量提示。
 * 当 totalMatches > limit 时返回提示语，否则返回 null。
 *
 * @param {number|null|undefined} totalMatches
 * @param {number} [limit=200]
 * @returns {string|null}
 */
export function formatTotalMatchesNotice(totalMatches, limit = 200) {
  if (typeof totalMatches === "number" && totalMatches > limit) {
    return `只显示了前 ${limit} 条，共 ${totalMatches} 条`;
  }
  return null;
}

/**
 * 构造 get_my_jobs API 请求路径纯函数。
 * 规则：
 * - 基础路径：/v1/my-jobs?filter=<filter>&limit=200
 * - filter 默认 "all"
 * - hr_only 为 true 时拼接 &hr_only=true
 * - q 为非空字符串时，经 trim 与 encodeURIComponent 后拼接 &q=<q>
 * - 不带时与原地址完全一致
 *
 * @param {object} [params]
 * @param {string} [params.filter="all"]
 * @param {boolean} [params.hr_only=false]
 * @param {string} [params.q=""]
 * @returns {string}
 */
export function buildMyJobsPath({ filter = "all", hr_only = false, q = "" } = {}) {
  const f = filter || "all";
  let path = `/v1/my-jobs?filter=${encodeURIComponent(f)}&limit=200`;
  if (hr_only === true) {
    path += "&hr_only=true";
  }
  const trimmedQ = typeof q === "string" ? q.trim() : "";
  if (trimmedQ) {
    path += `&q=${encodeURIComponent(trimmedQ)}`;
  }
  return path;
}

/**
 * 构造 save_hr_note 请求体纯函数。
 * 规则：
 * - 基础 note 规整（空或非字符串转为 null，截断 200 字）
 * - source 为 "myjobs" 或 "card" 时放进请求体，否则不加该字段
 *
 * @param {string|null|undefined} rawNote
 * @param {string|null|undefined} source
 * @returns {{ note: string|null, source?: string }}
 */
export function buildHrNotePayload(rawNote, source) {
  const payload = toHrNotePayload(rawNote);
  if (source === "myjobs" || source === "card" || source === "chat_sidebar") {
    payload.source = source;
  }
  return payload;
}

/**
 * 格式化 save_hr_note 失败响应纯函数。
 * 保留 ok, error, viewState，额外透传服务端 message (res.data?.message)。
 *
 * @param {object} res 来自 jetClient.call 的响应
 * @param {string} viewState 从 fromJetError 得到的状态
 * @returns {{ ok: false, error: any, viewState: string, message: string|undefined }}
 */
export function formatHrNoteErrorResponse(res, viewState) {
  return {
    ok: false,
    error: res?.error,
    viewState,
    message: res?.data?.message,
  };
}

/**
 * 创建请求序号跟踪器。
 * 用于解决搜索/勾选/切标签的请求乱序返回问题。
 */
export function createRequestTracker() {
  let latestId = 0;
  return {
    next() {
      return ++latestId;
    },
    isLatest(id) {
      return id === latestId;
    },
    get current() {
      return latestId;
    },
  };
}

export const STATUS_SAVE_FAILED_NOTICE = "状态保存失败";

/**
 * 状态保存失败时的回滚纯函数。
 * 恢复先前的状态与 counts 对象，并生成统一的失败提示文案。
 *
 * @param {object} params
 * @param {string|null} params.previousStatus 修改前的状态
 * @param {object} params.previousCounts 修改前的 counts 对象
 * @returns {{ status: string|null, counts: object, notice: string }}
 */
export function rollbackStatusChange({ previousStatus, previousCounts }) {
  return {
    status: previousStatus,
    counts: previousCounts ? { ...previousCounts } : {},
    notice: STATUS_SAVE_FAILED_NOTICE,
  };
}

/**
 * 判断 HR 实际情况在两行截断状态下是否实际溢出。
 * 规则：两行截断状态下 scrollHeight > clientHeight + 1 即为溢出。
 *
 * @param {number|{ scrollHeight: number, clientHeight: number }} scrollHeightOrEl
 * @param {number} [clientHeight]
 * @returns {boolean}
 */
export function isHrNoteOverflowing(scrollHeightOrEl, clientHeight) {
  if (scrollHeightOrEl && typeof scrollHeightOrEl === "object") {
    const sh = Number(scrollHeightOrEl.scrollHeight) || 0;
    const ch = Number(scrollHeightOrEl.clientHeight) || 0;
    return sh > ch + 1;
  }
  const sh = Number(scrollHeightOrEl) || 0;
  const ch = Number(clientHeight) || 0;
  return sh > ch + 1;
}
