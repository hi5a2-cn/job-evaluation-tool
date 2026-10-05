/**
 * 计算岗位实质内容摘要哈希（FNV-1a 32位）。
 * 对 title, salary_raw, city, description 变化敏感；对相同内容稳定。
 */
export function contentKey(job) {
  if (!job) return "";
  const parts = [
    job.title || "",
    job.salary_raw || "",
    job.city || "",
    job.description || "",
  ].join("|");

  let hash = 2166136261;
  for (let i = 0; i < parts.length; i++) {
    hash ^= parts.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

/**
 * 决定针对当前页面读取结果对详情卡片执行何种调度动作。
 *
 * @param {object|null} readResult readBossPage 返回的对象
 * @param {string|null} lastKey 该标签页上次成功发送或渲染的岗位键
 * @returns {{ action: "none"|"unsupported"|"unrecognized"|"send"|"rerender", key: string|null }}
 */
export function decideDetailAction(readResult, lastKey, attempt) {
  if (!readResult || !readResult.page_kind) {
    return { action: "none", key: null };
  }

  const pageKind = readResult.page_kind;

  if (pageKind === "captcha_or_blank" || pageKind === "other") {
    return { action: "none", key: null };
  }

  if (pageKind === "job_detail_page") {
    if (readResult.detail?.ok && readResult.detail?.job) {
      const job = readResult.detail.job;
      const key = `${job.platform_job_id}|${contentKey(job)}`;

      if (lastKey && lastKey === key) {
        return { action: "rerender", key };
      }

      return { action: "send", key };
    }

    // 独立职位页读不到必需字段：
    // 若读取结果包含 detail 且处于自动重试阶段（attempt < 4），则为重读；
    // 重读用完（attempt >= 4）或未提供 detail 读取结果时，最终显示"此页暂不支持读取"（沿用 unsupported_page 文案）
    if (readResult.detail && typeof attempt === "number" && attempt >= 0 && attempt < 4) {
      return { action: "retry", key: null };
    }

    return { action: "unsupported", key: null };
  }

  if (pageKind === "search_list") {
    const problems = Array.isArray(readResult.problems) ? readResult.problems : [];

    if (!readResult.detail?.ok || !readResult.detail?.job) {
      // 页面整体读不出（找不到 Vue、列表结构不对）或详情缺字段 → 页面无法识别。
      // 仅"列表被截断"这类提示、或只是没点开岗位，不算无法识别。
      const listBroken = readResult.list?.ok === false && problems.length > 0;
      const detailBroken = problems.some((p) => String(p).startsWith("详情"));
      if (listBroken || detailBroken) {
        return { action: "unrecognized", key: null };
      }
      return { action: "none", key: null };
    }

    const job = readResult.detail.job;
    const key = `${job.platform_job_id}|${contentKey(job)}`;

    if (lastKey && lastKey === key) {
      return { action: "rerender", key };
    }

    return { action: "send", key };
  }

  return { action: "none", key: null };
}

/**
 * 决定读取结果是否需要在刚加载时自动重读（FR-055）。
 *
 * @param {object|null} readResult readBossPage 返回的对象
 * @param {number} attempt 已重试次数 (0, 1, 2, 3...)
 * @param {number} msSinceLoad 页面加载至今的毫秒数
 * @returns {{ retry: boolean, delayMs: number, showReading: boolean }}
 */
export function shouldRetryRead(readResult, attempt = 0, msSinceLoad = Infinity) {
  const RETRY_DELAYS = [500, 1500, 3000, 6000];
  if (typeof attempt !== "number" || attempt < 0 || attempt >= 4) {
    return { retry: false, delayMs: 0, showReading: false };
  }

  const decision = decideDetailAction(readResult, null, attempt);
  if (decision.action === "unrecognized" || decision.action === "retry") {
    return {
      retry: true,
      delayMs: RETRY_DELAYS[attempt],
      showReading: true,
    };
  }

  if (
    readResult?.page_kind === "search_list" &&
    readResult.list?.ok === true &&
    (!readResult.detail?.ok || !readResult.detail?.job) &&
    typeof msSinceLoad === "number" &&
    msSinceLoad < 8000
  ) {
    return {
      retry: true,
      delayMs: RETRY_DELAYS[attempt],
      showReading: false,
    };
  }

  return { retry: false, delayMs: 0, showReading: false };
}

/**
 * 收到 reason: "load" 时重置标签页状态。
 * 清空 lastKey、lastJobRender、lastRender、pageStatus、currentJobId、currentJobTitle、
 * listSent、listJobs、listJudgements，取消轮询与重试定时器。
 * 保留 loadAt 与 readAttempt（由外部单独更新）。
 *
 * @param {object|null} state
 * @returns {object|null}
 */
export function resetTabStateOnLoad(state) {
  if (!state || typeof state !== "object") return null;

  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  if (state.listPollTimer) {
    clearInterval(state.listPollTimer);
    state.listPollTimer = null;
  }
  state.listPollStartedAt = null;
  if (state.retryTimer) {
    clearTimeout(state.retryTimer);
    state.retryTimer = null;
  }

  state.lastKey = null;
  state.lastJobRender = null;
  state.lastRender = null;
  state.pageStatus = null;
  state.currentJobId = null;
  state.currentJobTitle = null;
  state.listSent = new Set();
  state.listJudgements = new Map();
  state.listPrejudge = new Map();
  state.listJobs = [];
  state.updated_at = null;

  return state;
}

/**
 * 从标签页状态中提取用于 rerender 的岗位卡片 payload。
 * 只返回岗位卡片（有 platform_job_id），从不返回页面状态（reading / unrecognized 等）。
 *
 * @param {object|null} state
 * @returns {object|null}
 */
export function pickRerenderPayload(state) {
  if (!state || typeof state !== "object") return null;
  const jobRender = state.lastJobRender || (state.lastRender?.detail?.platform_job_id ? state.lastRender : null);
  const detail = jobRender?.detail !== undefined ? jobRender.detail : jobRender;
  if (detail && detail.platform_job_id) {
    return detail;
  }
  return null;
}

/**
 * 将标签页状态序列化为适合写入 chrome.storage.session 的纯 JSON 对象。
 * Map 转为数组，定时器丢弃。
 *
 * @param {object|null} state
 * @returns {object|null}
 */
export function serializeTabState(state) {
  if (!state || typeof state !== "object") return null;

  let listJudgementsArray = [];
  if (state.listJudgements instanceof Map) {
    listJudgementsArray = Array.from(state.listJudgements.entries());
  } else if (Array.isArray(state.listJudgements)) {
    listJudgementsArray = state.listJudgements;
  }

  let listPrejudgeArray = [];
  if (state.listPrejudge instanceof Map) {
    listPrejudgeArray = Array.from(state.listPrejudge.entries());
  } else if (Array.isArray(state.listPrejudge)) {
    listPrejudgeArray = state.listPrejudge;
  }

  return {
    tabId: state.tabId,
    lastKey: state.lastKey ?? null,
    lastJobRender: state.lastJobRender ?? null,
    pageStatus: state.pageStatus ?? null,
    currentJobId: state.currentJobId ?? null,
    currentJobTitle: state.currentJobTitle ?? null,
    listJobs: Array.isArray(state.listJobs) ? state.listJobs : [],
    listJudgements: listJudgementsArray,
    listPrejudge: listPrejudgeArray,
    lastTimings: state.lastTimings ?? null,
    updated_at: state.updated_at ?? null,
  };
}

/**
 * 从 chrome.storage.session 恢复标签页状态。
 * 数组转为 Map，定时器初始化为 null。
 *
 * @param {object|null} data
 * @returns {object|null}
 */
export function deserializeTabState(data) {
  if (!data || typeof data !== "object") return null;

  const listJudgements = new Map();
  if (Array.isArray(data.listJudgements)) {
    for (const item of data.listJudgements) {
      if (Array.isArray(item) && item.length >= 2) {
        listJudgements.set(item[0], item[1]);
      } else if (item && item.platform_job_id) {
        listJudgements.set(item.platform_job_id, item);
      }
    }
  }

  const listPrejudge = new Map();
  if (Array.isArray(data.listPrejudge)) {
    for (const item of data.listPrejudge) {
      if (Array.isArray(item) && item.length >= 2) {
        listPrejudge.set(item[0], item[1]);
      } else if (item && item.platform_job_id) {
        listPrejudge.set(item.platform_job_id, item);
      }
    }
  }

  return {
    tabId: data.tabId,
    lastKey: data.lastKey ?? null,
    lastJobRender: data.lastJobRender ?? null,
    lastRender: data.lastJobRender ?? null,
    pageStatus: data.pageStatus ?? null,
    pollTimer: null,
    pollGaveUpJobId: null,
    listPollTimer: null,
    listPollStartedAt: null,
    retryTimer: null,
    loadAt: null,
    readAttempt: 0,
    lastTimings: data.lastTimings ?? null,
    currentJobId: data.currentJobId ?? null,
    currentJobTitle: data.currentJobTitle ?? null,
    listSent: new Set(),
    listJudgements,
    listPrejudge,
    listJobs: Array.isArray(data.listJobs) ? data.listJobs : [],
    updated_at: data.updated_at ?? null,
  };
}

/**
 * 判断是否应当继续轮询判断结果。
 * queued / running / done+pending → 继续；
 * done+kept / downgraded / failed / null → 停止。
 *
 * @param {object|null} judgement
 * @returns {boolean}
 */
export function shouldKeepPolling(judgement) {
  if (!judgement || typeof judgement !== "object") {
    return false;
  }
  const status = judgement.status;
  if (status === "queued" || status === "running") {
    return true;
  }
  if (status === "done") {
    return judgement.review?.outcome === "pending";
  }
  return false;
}

/**
 * 判断消息发送者是否为 BOSS 直聘页面。
 * 只有 sender.tab 存在且 sender.tab.url（缺失时退回 sender.url）以 "https://www.zhipin.com/" 开头时返回 true；
 * 插件自身页面（chrome-extension://）、无 tab、URL 缺失均返回 false。
 *
 * @param {object|null} sender chrome.runtime.MessageSender
 * @returns {boolean}
 */
export function isBossPageSender(sender) {
  if (!sender || !sender.tab) {
    return false;
  }
  const url = sender.tab.url || sender.url;
  if (typeof url !== "string") {
    return false;
  }
  return url.startsWith("https://www.zhipin.com/");
}

/**
 * 判断异步详情响应是否对应当前激活的岗位。
 * tabState 存在且 tabState.currentJobId === platformJobId 时返回 true，否则 false。
 *
 * @param {object|null} tabState
 * @param {string|null} platformJobId
 * @returns {boolean}
 */
export function isDetailResponseCurrent(tabState, platformJobId) {
  if (!tabState || typeof tabState !== "object") {
    return false;
  }
  if (!platformJobId) {
    return false;
  }
  return tabState.currentJobId === platformJobId;
}

/**
 * 决定是否应当自愈恢复判断结果轮询。
 * 满足以下全部条件时返回 tabState.currentJobId，否则返回 null：
 * 1. tabState 存在
 * 2. tabState.pollTimer 为空
 * 3. tabState.currentJobId 非空
 * 4. 当前显示的岗位卡片（tabState.lastJobRender?.detail）的 platform_job_id 等于 currentJobId 且 view_state 为 "judging"
 * 5. 该岗位未因轮询超时放弃（pollGaveUpJobId !== currentJobId）
 *
 * @param {object|null} tabState
 * @returns {string|null}
 */
export function shouldResumePolling(tabState) {
  if (!tabState || typeof tabState !== "object") {
    return null;
  }
  if (tabState.pollTimer) {
    return null;
  }
  if (!tabState.currentJobId) {
    return null;
  }
  if (tabState.pollGaveUpJobId === tabState.currentJobId) {
    return null;
  }
  const detail = tabState.lastJobRender?.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  if (detail.platform_job_id !== tabState.currentJobId) {
    return null;
  }
  if (detail.view_state !== "judging") {
    return null;
  }
  return tabState.currentJobId;
}

/**
 * 从 listJudgements 中找出所有仍在进行中（queued / running / 复核 pending）的岗位 ID 列表。
 *
 * @param {Map<string, object>|Array|null} listJudgements
 * @param {number} [maxCount=30] 最多返回的数量
 * @returns {string[]}
 */
export function getPendingJobIds(listJudgements, maxCount = 30) {
  if (!listJudgements) return [];
  const entries = listJudgements instanceof Map
    ? listJudgements.entries()
    : (Array.isArray(listJudgements) ? listJudgements : []);

  const pending = [];
  for (const item of entries) {
    const [id, entry] = Array.isArray(item) ? item : [item?.platform_job_id, item];
    if (!id) continue;
    const judgement = entry?.judgement;
    if (shouldKeepPolling(judgement)) {
      pending.push(id);
      if (pending.length >= maxCount) break;
    }
  }
  return pending;
}

/**
 * 比较两次查询得到的「我的状态」。服务端每次返回新的 { status, updated_at } 对象，
 * 按字段比较，避免同样的状态每一轮轮询都被当成变化而重绘。
 *
 * @param {object|null|undefined} a
 * @param {object|null|undefined} b
 * @returns {boolean}
 */
function sameMyStatus(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  return a.status === b.status && a.updated_at === b.updated_at;
}

/**
 * 判断新查询到的岗位信息较已有条目是否有任何变化。
 *
 * @param {object|null} prevEntry
 * @param {object|null} newJobEntry
 * @returns {boolean}
 */
export function hasEntryChanged(prevEntry, newJobEntry) {
  if (!prevEntry && newJobEntry) return true;
  if (!newJobEntry) return false;
  if (newJobEntry.my_status !== undefined && !sameMyStatus(prevEntry?.my_status, newJobEntry.my_status)) return true;
  if (newJobEntry.company_name !== undefined && prevEntry?.company_name !== newJobEntry.company_name) return true;

  const prevJ = prevEntry?.judgement;
  const newJ = newJobEntry.judgement;
  if (!prevJ && !newJ) return false;
  if (!prevJ || !newJ) return true;

  if (prevJ.status !== newJ.status) return true;
  if (prevJ.verdict !== newJ.verdict) return true;
  if (prevJ.review?.outcome !== newJ.review?.outcome) return true;
  if (prevJ.error !== newJ.error) return true;
  if (prevJ.finished_at !== newJ.finished_at) return true;

  return JSON.stringify(prevJ) !== JSON.stringify(newJ);
}

/**
 * 构造查询待完成岗位判断的 URL。
 *
 * @param {string[]} pendingIds
 * @returns {string}
 */
export function buildListPollUrl(pendingIds) {
  const query = (pendingIds || []).map(encodeURIComponent).join(",");
  return `/v1/judgements?ids=${query}`;
}

/**
 * 执行一次列表待完成轮询迭代。
 *
 * @param {object} params
 * @param {number|string} params.tabId
 * @param {object} params.tabState
 * @param {object} params.client 本机 jetClient
 * @param {boolean} [params.marksEnabled=true]
 * @param {Function} [params.sendRender]
 * @param {Function} [params.onStop]
 * @param {Function} [params.updateJudgement]
 * @param {number} [params.now]
 * @param {number} [params.maxDuration=300000]
 * @returns {Promise<{ stopped: boolean, reason?: string, changed?: boolean }>}
 */
export async function runListPollIteration({
  tabId,
  tabState,
  client,
  marksEnabled = true,
  sendRender,
  onStop,
  updateJudgement,
  now = Date.now(),
  maxDuration = 5 * 60 * 1000,
}) {
  if (!tabState) {
    if (onStop) onStop();
    return { stopped: true, reason: "no_tab_state" };
  }

  // 1. 单次轮询总时长上限 5 分钟
  if (tabState.listPollStartedAt && now - tabState.listPollStartedAt >= maxDuration) {
    if (onStop) onStop();
    return { stopped: true, reason: "timeout" };
  }

  // 2. 检查是否有待完成岗位
  const pendingIds = getPendingJobIds(tabState.listJudgements, 30);
  if (pendingIds.length === 0) {
    if (onStop) onStop();
    return { stopped: true, reason: "no_pending" };
  }

  // 3. 一次查询所有待完成的岗位（最多 30 个）
  const queryUrl = buildListPollUrl(pendingIds);
  let res;
  try {
    res = await client.call("GET", queryUrl);
  } catch {
    // 请求异常（如 Jet 未运行），停止轮询
    if (onStop) onStop();
    return { stopped: true, reason: "client_error" };
  }

  if (!res || !res.ok) {
    // 请求失败（含 401 / 500 等），停止轮询
    if (onStop) onStop();
    return { stopped: true, reason: "response_not_ok" };
  }

  // 4. 对每个岗位更新 listJudgements
  const returnedJobs = res.data?.jobs || {};
  let changed = false;

  for (const [pid, jobEntry] of Object.entries(returnedJobs)) {
    if (!jobEntry) continue;
    const prevEntry = tabState.listJudgements?.get(pid);
    if (hasEntryChanged(prevEntry, jobEntry)) {
      changed = true;
    }
    if (updateJudgement) {
      updateJudgement(tabState, pid, jobEntry.judgement, {
        my_status: jobEntry.my_status,
        company_name: jobEntry.company_name,
      });
    } else {
      const existing = tabState.listJudgements?.get(pid) || {};
      tabState.listJudgements?.set(pid, {
        ...existing,
        ...(jobEntry.judgement ? { judgement: jobEntry.judgement } : {}),
        ...(jobEntry.my_status !== undefined ? { my_status: jobEntry.my_status } : {}),
        ...(jobEntry.company_name !== undefined ? { company_name: jobEntry.company_name } : {}),
      });
      tabState.updated_at = Date.now();
    }
  }

  // 5. 若有变化，重绘标记；detail 必须保持当前详情不变
  if (changed && sendRender) {
    const detail = tabState.pageStatus ?? tabState.lastJobRender?.detail ?? tabState.lastRender?.detail ?? null;
    sendRender(tabId, tabState, marksEnabled, detail);
  }

  // 6. 检查更新后是否还有待完成岗位
  const remainingPending = getPendingJobIds(tabState.listJudgements, 30);
  if (remainingPending.length === 0) {
    if (onStop) onStop();
    return { stopped: true, reason: "completed", changed };
  }

  return { stopped: false, changed };
}

/**
 * 返回本地日期的 YYYY-MM-DD 字符串。
 * 按 getFullYear / getMonth / getDate 拼接，避免 toISOString 的 UTC 跨日问题。
 *
 * @param {Date} [date=new Date()]
 * @returns {string}
 */
export function localDateString(date = new Date()) {
  const d = date instanceof Date ? date : new Date(date);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
