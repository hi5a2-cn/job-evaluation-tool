import { createJetClient, DEFAULT_PORT } from "./jet-client.js";
import { fromJudgement, fromObservation, fromJetError, describe } from "./view-state.js";
import { readBossPage, readBossChatPage, readBossChatJobId } from "./page-reader.js";
import {
  decideDetailAction,
  shouldRetryRead,
  resetTabStateOnLoad,
  pickRerenderPayload,
  serializeTabState,
  deserializeTabState,
  shouldKeepPolling,
  isBossPageSender,
  isDetailResponseCurrent,
  shouldResumePolling,
  localDateString,
  getPendingJobIds,
  hasEntryChanged,
  buildListPollUrl,
  runListPollIteration,
} from "./scheduler.js";
import { detailKey, nextDetailKey, newListItems } from "./dedupe.js";
import { buildListMarks, buildPageSummary } from "./page-summary.js";
import {
  decideChatSwitchRetry,
  CHAT_SWITCH_RETRY_DELAYS,
  formatChatJobStatus,
  isChatJobResponseCurrent,
  syncChatJobStatusInTabStates,
  isBossUrl,
  formatChatJobErrorStatus,
  shouldIngestChatJob,
  shouldFetchChatJobStatus,
} from "./chat-view.js";
import {
  buildMyJobsPath,
  buildHrNotePayload,
  formatHrNoteErrorResponse,
} from "./myjobs-view.js";

async function getToken() {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return null;
  }
  const result = await chrome.storage.local.get("jetToken");
  return result?.jetToken || null;
}

async function getPort() {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return DEFAULT_PORT;
  }
  const result = await chrome.storage.local.get("jetPort");
  return result?.jetPort || DEFAULT_PORT;
}

async function getMarksEnabled() {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return true;
  }
  const result = await chrome.storage.local.get("marksEnabled");
  return result?.marksEnabled !== false;
}

const jetClient = createJetClient({ getToken, getPort });

// 按标签页维护的状态 Map (tabId -> { tabId, lastKey, lastJobRender, lastRender, pageStatus, pollTimer, pollGaveUpJobId, retryTimer, loadAt, readAttempt, lastTimings, currentJobId, currentJobTitle, listSent, listJudgements, listPrejudge, listJobs, updated_at })
const tabStateMap = new Map();
const persistTimers = new Map();
let prejudgeQuotaExhaustedDate = null;

function getOrCreateTabState(tabId) {
  let state = tabStateMap.get(tabId);
  if (!state) {
    state = {
      tabId,
      lastKey: null,
      lastJobRender: null,
      lastRender: null,
      pageStatus: null,
      pollTimer: null,
      pollGaveUpJobId: null,
      listPollTimer: null,
      listPollStartedAt: null,
      retryTimer: null,
      loadAt: null,
      readAttempt: 0,
      lastTimings: null,
      currentJobId: null,
      currentJobTitle: null,
      listSent: new Set(),
      listJudgements: new Map(),
      listPrejudge: new Map(),
      listJobs: [],
      updated_at: null,
      lastChatJobId: null,
      chatJobStatus: null,
      chatSwitchRetryTimer: null,
      chatSwitchRetryToken: 0,
    };
    tabStateMap.set(tabId, state);
  }
  return state;
}

/**
 * 依据当前聊天岗位 ID 查询本机岗位库已有数据（只读查询，不改状态、不触发判断、不写任何表）(T060)
 * 采用现有只读接口 GET /v1/judgements?ids=...
 *
 * @param {string} jobId
 * @param {object} [client=jetClient]
 * @returns {Promise<object>}
 */
async function fetchChatJobStatus(jobId, client = jetClient) {
  if (!jobId) return formatChatJobStatus(jobId, null);
  try {
    const res = await client.call("GET", `/v1/judgements?ids=${encodeURIComponent(jobId)}`);
    if (!res?.ok) {
      const viewState = fromJetError(res);
      return formatChatJobErrorStatus(jobId, viewState);
    }
    const jobEntry = res.data?.jobs?.[jobId] || null;
    return formatChatJobStatus(jobId, jobEntry);
  } catch {
    return formatChatJobErrorStatus(jobId, "jet_down");
  }
}

async function restoreTabStateIfNeeded(tabId) {
  if (!tabId) return null;
  let state = tabStateMap.get(tabId);
  if (!state) {
    if (typeof chrome !== "undefined" && chrome.storage?.session) {
      try {
        const key = `tab:${tabId}`;
        const stored = await chrome.storage.session.get(key);
        if (stored?.[key]) {
          state = deserializeTabState(stored[key]);
          if (state) {
            if (state.lastChatJobId === undefined) state.lastChatJobId = null;
            state.chatSwitchRetryTimer = null;
            state.chatSwitchRetryToken = 0;
            tabStateMap.set(tabId, state);
          }
        }
      } catch {
        // ignore
      }
    }
  }
  if (!state) {
    state = getOrCreateTabState(tabId);
  }
  return state;
}

function schedulePersistTabState(tabId) {
  if (!tabId) return;
  const state = tabStateMap.get(tabId);
  if (!state) return;
  if (typeof chrome === "undefined" || !chrome.storage?.session) {
    return;
  }

  if (persistTimers.has(tabId)) {
    clearTimeout(persistTimers.get(tabId));
  }

  const timer = setTimeout(async () => {
    persistTimers.delete(tabId);
    try {
      const currentState = tabStateMap.get(tabId);
      if (currentState) {
        const serialized = serializeTabState(currentState);
        await chrome.storage.session.set({ [`tab:${tabId}`]: serialized });
      }
    } catch {
      // ignore
    }
  }, 200);

  persistTimers.set(tabId, timer);
}

function clearPollTimer(tabState) {
  if (tabState && tabState.pollTimer) {
    clearInterval(tabState.pollTimer);
    tabState.pollTimer = null;
  }
}

function clearListPollTimer(tabState) {
  if (tabState && tabState.listPollTimer) {
    clearInterval(tabState.listPollTimer);
    tabState.listPollTimer = null;
    tabState.listPollStartedAt = null;
  }
}

function ensureListPolling(tabId, client = jetClient) {
  const tabState = tabStateMap.get(tabId);
  if (!tabState) return;

  if (tabState.listPollTimer) return;

  const pending = getPendingJobIds(tabState.listJudgements, 30);
  if (pending.length === 0) return;

  tabState.listPollStartedAt = Date.now();
  const interval = 3000;

  tabState.listPollTimer = setInterval(async () => {
    const currentTabState = tabStateMap.get(tabId);
    if (!currentTabState) {
      clearListPollTimer(tabState);
      return;
    }

    const marksEnabled = await getMarksEnabled();
    await runListPollIteration({
      tabId,
      tabState: currentTabState,
      client,
      marksEnabled,
      sendRender: (tId, tState, marks, detail) => sendRenderState(tId, tState, marks, detail),
      onStop: () => clearListPollTimer(currentTabState),
      updateJudgement: (tState, pid, j, extra) => updateTabJudgement(tState, pid, j, extra),
    });
  }, interval);
}

function clearRetryTimer(tabState) {
  if (tabState && tabState.retryTimer) {
    clearTimeout(tabState.retryTimer);
    tabState.retryTimer = null;
  }
}

// 当前应显示的内容：渲染岗位卡片时会清空 pageStatus，所以 pageStatus 非空时它一定是最新的，优先显示
function sendRenderState(tabId, tabState, marksEnabled, detailPayload = null) {
  const listMarks = buildListMarks(tabState?.listJudgements, tabState?.listPrejudge);
  if (tabState) {
    const renderObj = {
      detail: detailPayload,
      marks_enabled: marksEnabled,
      list_marks: listMarks,
    };
    tabState.lastRender = renderObj;
    if (detailPayload && detailPayload.platform_job_id) {
      tabState.lastJobRender = renderObj;
      tabState.pageStatus = null; // 渲染岗位卡片时清空 pageStatus
    } else {
      tabState.pageStatus = detailPayload;
    }
    tabState.updated_at = Date.now();
  }

  if (tabId && typeof chrome !== "undefined" && chrome.tabs?.sendMessage) {
    chrome.tabs.sendMessage(tabId, {
      type: "render_state",
      detail: detailPayload,
      marks_enabled: marksEnabled,
      list_marks: listMarks,
    }).catch(() => {});
  }

  if (tabId && typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
    try {
      chrome.runtime.sendMessage({ type: "tab_state_changed", tabId }).catch(() => {});
    } catch {
      // 没有接收方时忽略
    }
  }

  if (tabId) {
    schedulePersistTabState(tabId);
  }
}

function updateTabJudgement(tabState, platformJobId, judgement, extra = {}) {
  if (!tabState || !platformJobId) return;
  const inList =
    (Array.isArray(tabState.listJobs) &&
      tabState.listJobs.some((j) => j.platform_job_id === platformJobId)) ||
    tabState.listJudgements?.has(platformJobId);
  if (inList) {
    const existing = tabState.listJudgements.get(platformJobId) || {};
    tabState.listJudgements.set(platformJobId, {
      ...existing,
      ...(judgement ? { judgement } : {}),
      ...(extra.my_status !== undefined ? { my_status: extra.my_status } : {}),
      ...(extra.company_name !== undefined ? { company_name: extra.company_name } : {}),
    });
    tabState.updated_at = Date.now();
    schedulePersistTabState(tabState.tabId);
  }
}

function startPolling(tabId, platformJobId, marksEnabled, client = jetClient) {
  const tabState = tabStateMap.get(tabId);
  if (!tabState) return;

  clearPollTimer(tabState);
  ensureListPolling(tabId, client);

  const startTime = Date.now();
  // 含复核（开启思考，最长约 60 秒）：初判 + 复核可能超过 60 秒
  const maxDuration = 150 * 1000;
  const interval = 1500;

  tabState.pollTimer = setInterval(async () => {
    const currentTabState = tabStateMap.get(tabId);
    if (!currentTabState || currentTabState.currentJobId !== platformJobId) {
      clearPollTimer(tabState);
      return;
    }

    if (Date.now() - startTime >= maxDuration) {
      clearPollTimer(currentTabState);
      currentTabState.pollGaveUpJobId = platformJobId;
      const desc = describe(null, "judging");
      desc.label = "判断仍在进行，稍后再看";
      const title = currentTabState.currentJobTitle || currentTabState.lastJobRender?.detail?.title || currentTabState.lastRender?.detail?.title || null;
      const renderPayload = {
        platform_job_id: platformJobId,
        title: title,
        ...desc,
        hr_note: currentTabState.lastJobRender?.detail?.hr_note ?? currentTabState.lastRender?.detail?.hr_note ?? null,
        my_status: currentTabState.lastJobRender?.detail?.my_status ?? currentTabState.lastRender?.detail?.my_status ?? null,
      };
      sendRenderState(tabId, currentTabState, marksEnabled, renderPayload);
      return;
    }

    const res = await client.call(
      "GET",
      `/v1/judgements?ids=${encodeURIComponent(platformJobId)}`,
    );

    // 若请求在途期间已切换到其他岗位，直接返回避免覆盖新岗位渲染或误清定时器
    if (!isDetailResponseCurrent(currentTabState, platformJobId)) {
      return;
    }

    if (!res.ok) {
      if (res.status === 401 || res.status === 0) {
        clearPollTimer(currentTabState);
        const viewState = fromJetError(res);
        const desc = describe(null, viewState);
        const title = currentTabState.currentJobTitle || currentTabState.lastJobRender?.detail?.title || currentTabState.lastRender?.detail?.title || null;
        const renderPayload = {
          platform_job_id: platformJobId,
          title: title,
          ...desc,
          hr_note: currentTabState.lastJobRender?.detail?.hr_note ?? currentTabState.lastRender?.detail?.hr_note ?? null,
          my_status: currentTabState.lastJobRender?.detail?.my_status ?? currentTabState.lastRender?.detail?.my_status ?? null,
        };
        sendRenderState(tabId, currentTabState, marksEnabled, renderPayload);
      }
      return;
    }

    const jobEntry = res.data?.jobs?.[platformJobId];
    if (jobEntry && jobEntry.judgement) {
      const keep = shouldKeepPolling(jobEntry.judgement);
      const viewState = fromJudgement(jobEntry.judgement);
      updateTabJudgement(currentTabState, platformJobId, jobEntry.judgement, {
        my_status: jobEntry.my_status,
        company_name: jobEntry.company_name,
      });
      const hrNote = jobEntry.hr_note ?? null;
      const myStatus = jobEntry.my_status ?? null;
      const desc = describe(jobEntry.judgement, viewState);
      const title = jobEntry.title || currentTabState.currentJobTitle || currentTabState.lastJobRender?.detail?.title || currentTabState.lastRender?.detail?.title || null;
      if (jobEntry.title) {
        currentTabState.currentJobTitle = jobEntry.title;
      }
      const renderPayload = {
        platform_job_id: platformJobId,
        title: title,
        ...desc,
        hr_note: hrNote,
        my_status: myStatus,
      };
      sendRenderState(tabId, currentTabState, marksEnabled, renderPayload);

      if (!keep) {
        clearPollTimer(currentTabState);
      }
    }
  }, interval);
}

if (typeof chrome !== "undefined" && chrome.tabs?.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    if (persistTimers.has(tabId)) {
      clearTimeout(persistTimers.get(tabId));
      persistTimers.delete(tabId);
    }
    const state = tabStateMap.get(tabId);
    if (state) {
      clearPollTimer(state);
      clearListPollTimer(state);
      clearRetryTimer(state);
      if (state.chatSwitchRetryTimer) {
        clearTimeout(state.chatSwitchRetryTimer);
        state.chatSwitchRetryTimer = null;
      }
      if (state.listSent) {
        state.listSent.clear();
      }
      if (state.listJudgements) {
        state.listJudgements.clear();
      }
      if (state.listPrejudge) {
        state.listPrejudge.clear();
      }
      if (state.listJobs) {
        state.listJobs = [];
      }
      tabStateMap.delete(tabId);
    }
    if (typeof chrome !== "undefined" && chrome.storage?.session) {
      chrome.storage.session.remove(`tab:${tabId}`).catch(() => {});
    }
  });
}

async function sendListObservations(tabState, readResult, tabId = null, client = jetClient) {
  if (
    readResult?.page_kind !== "search_list" ||
    !readResult.list?.ok ||
    !Array.isArray(readResult.list?.jobs) ||
    readResult.list.jobs.length === 0
  ) {
    return;
  }

  // 每次读取都更新 listJobs，不受去重影响
  tabState.listJobs = readResult.list.jobs.map((j) => ({
    platform_job_id: j.platform_job_id,
    title: j.title || "",
  }));

  const { toSend, keys } = newListItems(readResult.list.jobs, tabState.listSent);
  if (toSend.length === 0) {
    return;
  }

  const payloadJobs = toSend.map((j) => ({
    platform_job_id: j.platform_job_id,
    title: j.title,
    company_name: j.company_name ?? null,
    company_industry: j.company_industry ?? null,
    salary_raw: j.salary_raw ?? null,
    city: j.city,
    district: j.district ?? null,
    experience: j.experience ?? null,
    degree: j.degree ?? null,
    job_labels: Array.isArray(j.job_labels) ? j.job_labels : [],
    skills: Array.isArray(j.skills) ? j.skills : [],
  }));

  const payload = {
    page_type: "list",
    observed_at: new Date().toISOString(),
    jobs: payloadJobs,
  };

  // 发请求前就记为已上报：上报还没返回时页面又被读取一次，不会把同一批岗位再发一遍、再预判一次；
  // 上报失败再撤掉，下次重试
  const sentSet = tabState.listSent;
  for (const k of keys) {
    sentSet.add(k);
  }
  const unmarkSent = () => {
    for (const k of keys) {
      sentSet.delete(k);
    }
  };

  let uploaded = false;
  try {
    const res = await client.call("POST", "/v1/observations", payload);
    if (!res.ok) {
      unmarkSent();
    }
    if (res.ok) {
      uploaded = true;
      const returnedJobs = res.data?.jobs || {};
      for (const [pid, entry] of Object.entries(returnedJobs)) {
        tabState.listJudgements.set(pid, {
          judgement: entry?.judgement ?? null,
          my_status: entry?.my_status ?? null,
          company_name: entry?.company_name ?? null,
          screen_hints: Array.isArray(entry?.screen_hints) ? entry.screen_hints : [],
        });
      }

      const marksEnabled = await getMarksEnabled();
      const targetTabId = tabId || tabState?.tabId;
      const detail = tabState.pageStatus ?? tabState.lastJobRender?.detail ?? tabState.lastRender?.detail ?? null;
      // 先照原样用 listJudgements 渲染一次列表标记
      sendRenderState(targetTabId, tabState, marksEnabled, detail);
      // "判断中"卡片的自动更新先启动，不等预判的大模型调用
      ensureListPolling(targetTabId, client);

      // 从 toSend 中筛选未有正式判断（status === 'done'）且未预判的岗位打包预判
      if (tabState.listPrejudge) {
        const candidates = toSend.filter((j) => {
          const entry = tabState.listJudgements.get(j.platform_job_id);
          const isDone = entry?.judgement && entry.judgement.status === "done";
          return !isDone && !tabState.listPrejudge.has(j.platform_job_id);
        });

        const todayStr = localDateString();
        if (candidates.length > 0 && prejudgeQuotaExhaustedDate !== todayStr) {
          const BATCH_SIZE = 40;
          for (let i = 0; i < candidates.length; i += BATCH_SIZE) {
            if (prejudgeQuotaExhaustedDate === todayStr) {
              break;
            }
            const batch = candidates.slice(i, i + BATCH_SIZE);
            const prejudgePayload = batch.map((j) => ({
              platform_job_id: j.platform_job_id,
              title: j.title,
              company_name: j.company_name ?? null,
              company_industry: j.company_industry ?? null,
              salary_raw: j.salary_raw ?? null,
              city: j.city || "",
              district: j.district ?? null,
              experience: j.experience ?? null,
              degree: j.degree ?? null,
              job_labels: Array.isArray(j.job_labels) ? j.job_labels : [],
              skills: Array.isArray(j.skills) ? j.skills : [],
            }));

            try {
              const pRes = await client.prejudgeJobs(prejudgePayload);
              if (pRes?.ok && pRes.data) {
                if (pRes.data.status === "quota_exhausted") {
                  prejudgeQuotaExhaustedDate = todayStr;
                }
                if (pRes.data.prejudgements) {
                  for (const [pid, pItem] of Object.entries(pRes.data.prejudgements)) {
                    tabState.listPrejudge.set(pid, pItem);
                  }
                }
                // 预判返回后再渲染一次；详情在渲染时重新取，等待期间用户可能已切换岗位
                const latestDetail = tabState.pageStatus ?? tabState.lastJobRender?.detail ?? tabState.lastRender?.detail ?? null;
                sendRenderState(targetTabId, tabState, marksEnabled, latestDetail);
                if (pRes.data.status === "quota_exhausted") {
                  break;
                }
              }
            } catch {
              // 静默降级
              break;
            }
          }
        }
      }
    }
  } catch {
    // 列表发送失败不显示任何卡片提示，撤掉已上报标记，下次重试；上报成功后的渲染出错不撤
    if (!uploaded) {
      unmarkSent();
    }
  }
}

async function handleSendDetail(tabId, tabState, readResult, marksEnabled, waitMs, readMs, client = jetClient) {
  const job = readResult?.detail?.job;
  if (!job) return;
  tabState.currentJobId = job.platform_job_id;
  tabState.currentJobTitle = job.title || null;
  tabState.pollGaveUpJobId = null;
  clearPollTimer(tabState);

  // 缓存先显示：若该岗位在 listJudgements 里已有判断（或 lastJobRender.detail 就是该岗位），在发 POST 之前先用它立刻渲染卡片
  const cachedEntry = tabState.listJudgements?.get(job.platform_job_id);
  const cachedJudgement = cachedEntry?.judgement || null;
  const prevDetail = tabState.lastJobRender?.detail ?? tabState.lastRender?.detail ?? null;
  if (cachedJudgement) {
    const cachedViewState = fromJudgement(cachedJudgement);
    if (cachedViewState) {
      const cachedDesc = describe(cachedJudgement, cachedViewState);
      const hrNote = cachedEntry.hr_note ?? (prevDetail?.platform_job_id === job.platform_job_id ? prevDetail.hr_note : null);
      const myStatus = cachedEntry.my_status ?? (prevDetail?.platform_job_id === job.platform_job_id ? prevDetail.my_status : null);
      const cachedRenderPayload = {
        platform_job_id: job.platform_job_id,
        title: job.title || tabState.currentJobTitle || null,
        ...cachedDesc,
        hr_note: hrNote,
        my_status: myStatus,
      };
      sendRenderState(tabId, tabState, marksEnabled, cachedRenderPayload);
    }
  } else if (prevDetail?.platform_job_id === job.platform_job_id) {
    sendRenderState(tabId, tabState, marksEnabled, prevDetail);
  }

  const isJobDetailPage = readResult?.page_kind === "job_detail_page";

  const detailJobPayload = isJobDetailPage
    ? {
        platform_job_id: job.platform_job_id,
        title: job.title,
        company_legal_name: job.company_legal_name ?? null,
        company_industry: null,
        salary_raw: job.salary_raw,
        city: job.city,
        district: null,
        description: job.description,
      }
    : {
        platform_job_id: job.platform_job_id,
        title: job.title,
        company_name: job.company_name ?? null,
        company_industry: job.company_industry ?? null,
        salary_raw: job.salary_raw,
        city: job.city,
        district: job.district,
        description: job.description,
        experience: job.experience ?? null,
        degree: job.degree ?? null,
      };

  const payload = {
    page_type: "detail",
    observed_at: new Date().toISOString(),
    jobs: [detailJobPayload],
  };

  const tJetStart = Date.now();
  const res = await client.call("POST", "/v1/observations", payload);
  const jetMs = Date.now() - tJetStart;

  tabState.lastTimings = {
    wait_ms: waitMs,
    read_ms: readMs,
    jet_ms: jetMs,
    at: Date.now(),
  };

  if (!isDetailResponseCurrent(tabState, job.platform_job_id)) {
    if (res.ok) {
      const jobEntry = res.data?.jobs?.[job.platform_job_id];
      const judgement = jobEntry?.judgement || null;
      const myStatus = jobEntry?.my_status ?? null;
      updateTabJudgement(tabState, job.platform_job_id, judgement, {
        my_status: myStatus,
        company_name: job.company_name || jobEntry?.company_name,
      });
      ensureListPolling(tabId, client);
      if (isJobDetailPage && jobEntry) {
        const newStatus = syncChatJobStatusInTabStates(tabStateMap, job.platform_job_id, jobEntry);
        try {
          const p = chrome.runtime.sendMessage({
            type: "chat_job_status_updated",
            jobId: job.platform_job_id,
            jobStatus: newStatus,
          });
          if (p && typeof p.catch === "function") p.catch(() => {});
        } catch {}
      }
    }
    return;
  }

  if (res.ok) {
    tabState.lastKey = nextDetailKey(tabState.lastKey, job, true);
    const viewState = fromObservation(res.data, job.platform_job_id);
    const jobEntry = res.data?.jobs?.[job.platform_job_id];
    const judgement = jobEntry?.judgement || null;
    const hrNote = jobEntry?.hr_note ?? null;
    const myStatus = jobEntry?.my_status ?? null;
    updateTabJudgement(tabState, job.platform_job_id, judgement, {
      my_status: myStatus,
      company_name: job.company_name || jobEntry?.company_name,
    });
    ensureListPolling(tabId, client);
    const desc = describe(judgement, viewState, { notice: res.data?.notice });
    const renderPayload = {
      platform_job_id: job.platform_job_id,
      title: job.title || tabState.currentJobTitle || null,
      ...desc,
      hr_note: hrNote,
      my_status: myStatus,
    };
    sendRenderState(tabId, tabState, marksEnabled, renderPayload);

    // 独立职位页入库成功后同步各标签页中同一岗位的聊天状态缓存并广播（T018 / research R8）
    if (isJobDetailPage && jobEntry) {
      const newStatus = syncChatJobStatusInTabStates(tabStateMap, job.platform_job_id, jobEntry);
      try {
        const p = chrome.runtime.sendMessage({
          type: "chat_job_status_updated",
          jobId: job.platform_job_id,
          jobStatus: newStatus,
        });
        if (p && typeof p.catch === "function") p.catch(() => {});
      } catch {}
    }

    if (shouldKeepPolling(judgement)) {
      startPolling(tabId, job.platform_job_id, marksEnabled, client);
    }
  } else {
    tabState.lastKey = nextDetailKey(tabState.lastKey, job, false);
    const viewState = fromJetError(res);
    const desc = describe(null, viewState, { notice: res.data?.notice });
    const renderPayload = {
      platform_job_id: job.platform_job_id,
      title: job.title || tabState.currentJobTitle || null,
      ...desc,
      hr_note: null,
      my_status: null,
    };
    sendRenderState(tabId, tabState, marksEnabled, renderPayload);
  }
}

async function processPageRead(tabId, message = {}) {
  if (!tabId || typeof chrome.scripting?.executeScript !== "function") {
    return;
  }

  let tabState = tabStateMap.get(tabId);
  if (!tabState) {
    tabState = await restoreTabStateIfNeeded(tabId);
  }
  const reason = message.reason;

  if (reason === "load") {
    resetTabStateOnLoad(tabState);
    tabState.loadAt = Date.now();
    tabState.readAttempt = 0;
    schedulePersistTabState(tabId);
  }

  const waitMs = (reason === "load" || reason === "retry")
    ? 0
    : (typeof message.waited_ms === "number" ? message.waited_ms : 0);

  let readResult = null;
  const tReadStart = Date.now();
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: readBossPage,
    });
    readResult = results?.[0]?.result || null;
  } catch {
    return;
  }
  const readMs = Date.now() - tReadStart;

  if (
    readResult?.page_kind === "search_list" &&
    readResult.list?.ok &&
    Array.isArray(readResult.list?.jobs)
  ) {
    tabState.listJobs = readResult.list.jobs.map((j) => ({
      platform_job_id: j.platform_job_id,
      title: j.title || "",
    }));
  }

  const marksEnabled = await getMarksEnabled();
  const decision = decideDetailAction(readResult, tabState.lastKey, tabState.readAttempt);

  const msSinceLoad = typeof tabState.loadAt === "number"
    ? Math.max(0, Date.now() - tabState.loadAt)
    : Infinity;

  const retryInfo = shouldRetryRead(readResult, tabState.readAttempt, msSinceLoad);

  if (retryInfo.retry) {
    if (!tabState.retryTimer) {
      tabState.retryTimer = setTimeout(() => {
        tabState.retryTimer = null;
        tabState.readAttempt += 1;
        processPageRead(tabId, { reason: "retry" });
      }, retryInfo.delayMs);
    }
    if (retryInfo.showReading) {
      const desc = describe(null, "reading");
      const renderPayload = {
        platform_job_id: null,
        title: null,
        ...desc,
        my_status: null,
      };
      sendRenderState(tabId, tabState, marksEnabled, renderPayload);
    }
  }

  if (decision.action === "unsupported") {
    clearRetryTimer(tabState);
    const desc = describe(null, "unsupported_page");
    const renderPayload = {
      platform_job_id: null,
      title: null,
      ...desc,
      my_status: null,
    };
    sendRenderState(tabId, tabState, marksEnabled, renderPayload);
  } else if (decision.action === "unrecognized") {
    if (!retryInfo.retry) {
      const desc = describe(null, "unrecognized");
      const renderPayload = {
        platform_job_id: null,
        title: null,
        ...desc,
        my_status: null,
      };
      sendRenderState(tabId, tabState, marksEnabled, renderPayload);
    }
  } else if (decision.action === "rerender") {
    clearRetryTimer(tabState);
    const rerenderPayload = pickRerenderPayload(tabState);
    if (rerenderPayload) {
      sendRenderState(tabId, tabState, marksEnabled, rerenderPayload);
      const cachedJudgement = tabState.listJudgements?.get(rerenderPayload.platform_job_id)?.judgement;
      if (shouldKeepPolling(cachedJudgement)) {
        startPolling(tabId, rerenderPayload.platform_job_id, marksEnabled);
      }
      const resumeJobId = shouldResumePolling(tabState);
      if (resumeJobId) {
        startPolling(tabId, resumeJobId, marksEnabled);
      }
      // 后台被浏览器挂起后定时器会丢失：与详情轮询一样，在这里恢复"判断中"卡片的列表轮询（没有待完成岗位时不启动）
      ensureListPolling(tabId);
    } else {
      await handleSendDetail(tabId, tabState, readResult, marksEnabled, waitMs, readMs);
    }
  } else if (decision.action === "send") {
    clearRetryTimer(tabState);
    await handleSendDetail(tabId, tabState, readResult, marksEnabled, waitMs, readMs);
  }

  // 列表发送放在后面独立进行（不 await 阻塞详情渲染；可用单独的 async 函数并 catch 错误）
  sendListObservations(tabState, readResult, tabId).catch(() => {});
}

async function resolveTabId(message, sender) {
  let tabId = message?.tabId || sender?.tab?.id;
  if (!tabId && typeof chrome !== "undefined" && typeof chrome.tabs?.query === "function") {
    try {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      tabId = activeTab?.id;
    } catch {}
  }
  return tabId || null;
}

if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // 1. 获取连接状态
    if (message?.type === "get_status") {
      (async () => {
        try {
          const healthRes = await jetClient.call("GET", "/v1/health");
          if (!healthRes.ok) {
            sendResponse({ state: "jet_down" });
            return;
          }

          const token = await getToken();
          if (!token) {
            sendResponse({ state: "unpaired" });
            return;
          }

          const statusRes = await jetClient.call("GET", "/v1/status");
          if (statusRes.ok) {
            sendResponse({ state: "paired", status: statusRes.data });
          } else if (statusRes.status === 401) {
            sendResponse({ state: "unpaired" });
          } else {
            sendResponse({ state: "failed", error: statusRes.error });
          }
        } catch (err) {
          sendResponse({ state: "jet_down", error: err?.message || String(err) });
        }
      })();
      return true;
    }

    // 2. 配对
    if (message?.type === "pair") {
      (async () => {
        try {
          const res = await jetClient.call("POST", "/v1/pair", { code: message.code });
          if (res.ok && res.data?.token) {
            await chrome.storage.local.set({
              jetToken: res.data.token,
              jetUserId: res.data.user_id || "me",
            });
            sendResponse({ ok: true });
          } else {
            sendResponse({ ok: false, error: res.error || "failed" });
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || "failed" });
        }
      })();
      return true;
    }

    // 3. 获取画像
    if (message?.type === "get_profile") {
      (async () => {
        try {
          const res = await jetClient.call("GET", "/v1/profile");
          sendResponse({
            ok: res.ok,
            status: res.status,
            data: res.data,
            viewState: res.viewState,
            error: res.error,
          });
        } catch (err) {
          sendResponse({
            ok: false,
            status: 0,
            viewState: "jet_down",
            error: err?.message || String(err),
          });
        }
      })();
      return true;
    }

    // 4. 保存画像
    if (message?.type === "save_profile") {
      (async () => {
        try {
          const res = await jetClient.call("PUT", "/v1/profile", message.profile);
          sendResponse({
            ok: res.ok,
            status: res.status,
            data: res.data,
            viewState: res.viewState,
            error: res.error,
          });
          if (res.ok) {
            handleProfileSaved().catch(() => {});
          }
        } catch (err) {
          sendResponse({
            ok: false,
            status: 0,
            viewState: "jet_down",
            error: err?.message || String(err),
          });
        }
      })();
      return true;
    }

    // 5. 重新判断 / 重试
    if (message?.type === "rejudge") {
      (async () => {
        const platformJobId = message.platform_job_id;
        const tabId = sender.tab?.id;
        if (!platformJobId || !tabId) {
          sendResponse({ ok: false, error: "missing_params" });
          return;
        }

        let tabState = tabStateMap.get(tabId);
        if (!tabState) {
          tabState = await restoreTabStateIfNeeded(tabId);
        }
        tabState.currentJobId = platformJobId;
        tabState.pollGaveUpJobId = null;
        clearPollTimer(tabState);

        const marksEnabled = await getMarksEnabled();

        const reqBody = message.force ? { force: true } : {};

        const res = await jetClient.call(
          "POST",
          `/v1/jobs/${encodeURIComponent(platformJobId)}/judge`,
          reqBody,
        );

        if (res.ok) {
          const viewState = fromObservation(res.data, platformJobId);
          const jobEntry = res.data?.jobs?.[platformJobId];
          const judgement = jobEntry?.judgement || null;
          const hrNote = jobEntry?.hr_note ?? null;
          const myStatus = jobEntry?.my_status ?? null;
          updateTabJudgement(tabState, platformJobId, judgement, {
            my_status: myStatus,
            company_name: jobEntry?.company_name,
          });
          ensureListPolling(tabId);

          if (!isDetailResponseCurrent(tabState, platformJobId)) {
            sendResponse({ ok: true });
            return;
          }

          const title = jobEntry?.title || tabState.currentJobTitle || tabState.lastJobRender?.detail?.title || tabState.lastRender?.detail?.title || null;
          if (jobEntry?.title) {
            tabState.currentJobTitle = jobEntry.title;
          }
          const desc = describe(judgement, viewState);
          const renderPayload = {
            platform_job_id: platformJobId,
            title: title,
            ...desc,
            hr_note: hrNote,
            my_status: myStatus,
          };
          sendRenderState(tabId, tabState, marksEnabled, renderPayload);

          if (shouldKeepPolling(judgement)) {
            startPolling(tabId, platformJobId, marksEnabled);
          }
          sendResponse({ ok: true });
        } else {
          if (!isDetailResponseCurrent(tabState, platformJobId)) {
            sendResponse({ ok: false, error: res.error });
            return;
          }

          const viewState = fromJetError(res);
          const desc = describe(null, viewState);
          const title = tabState.currentJobTitle || tabState.lastJobRender?.detail?.title || tabState.lastRender?.detail?.title || null;
          const renderPayload = {
            platform_job_id: platformJobId,
            title: title,
            ...desc,
            hr_note: null,
            my_status: null,
          };
          sendRenderState(tabId, tabState, marksEnabled, renderPayload);
          sendResponse({ ok: false, error: res.error });
        }
      })();
      return true;
    }

    // 6. 保存标注 (save_label)
    if (message?.type === "save_label") {
      (async () => {
        const platformJobId = message.platform_job_id;
        const labelPayload = message.label;
        const tabId = sender.tab?.id;

        if (!platformJobId) {
          sendResponse({ ok: false, error: "missing_params", viewState: "failed" });
          return;
        }

        const res = await jetClient.call(
          "PUT",
          `/v1/jobs/${encodeURIComponent(platformJobId)}/label`,
          labelPayload,
        );

        if (res.ok) {
          let tabState = tabId ? tabStateMap.get(tabId) : null;
          if (!tabState && tabId) {
            tabState = await restoreTabStateIfNeeded(tabId);
          }
          const marksEnabled = await getMarksEnabled();

          const viewState = fromObservation(res.data, platformJobId);
          const jobEntry = res.data?.jobs?.[platformJobId];
          const judgement = jobEntry?.judgement || null;
          const hrNote = jobEntry?.hr_note ?? null;
          const myStatus = jobEntry?.my_status ?? null;

          if (tabState) {
            updateTabJudgement(tabState, platformJobId, judgement, {
              my_status: myStatus,
              company_name: jobEntry?.company_name,
            });
          }

          const title = jobEntry?.title || tabState?.currentJobTitle || tabState?.lastJobRender?.detail?.title || tabState?.lastRender?.detail?.title || null;
          if (tabState && jobEntry?.title) {
            tabState.currentJobTitle = jobEntry.title;
          }

          const desc = describe(judgement, viewState);
          const renderPayload = {
            platform_job_id: platformJobId,
            title: title,
            ...desc,
            hr_note: hrNote,
            my_status: myStatus,
          };

          if (tabState) {
            sendRenderState(tabId, tabState, marksEnabled, renderPayload);
          }

          sendResponse({ ok: true, labels: res.data?.labels });
        } else {
          const viewState = fromJetError(res);
          sendResponse({ ok: false, error: res.error, viewState });
        }
      })();
      return true;
    }

    // 7. 保存 HR 备注 (save_hr_note)
    if (message?.type === "save_hr_note") {
      (async () => {
        const platformJobId = message.platform_job_id;
        const rawNote = message.note;
        const tabId = sender.tab?.id;

        if (!platformJobId) {
          sendResponse({ ok: false, error: "missing_params", viewState: "failed" });
          return;
        }

        const payload = buildHrNotePayload(rawNote, message.source);

        const res = await jetClient.call(
          "PUT",
          `/v1/jobs/${encodeURIComponent(platformJobId)}/hr-note`,
          payload,
        );

        if (res.ok) {
          if (isBossPageSender(sender)) {
            let tabState = tabId ? tabStateMap.get(tabId) : null;
            if (!tabState && tabId) {
              tabState = await restoreTabStateIfNeeded(tabId);
            }
            const marksEnabled = await getMarksEnabled();

            const viewState = fromObservation(res.data, platformJobId);
            const jobEntry = res.data?.jobs?.[platformJobId];
            const judgement = jobEntry?.judgement || null;
            const hrNote = jobEntry?.hr_note ?? null;
            const myStatus = jobEntry?.my_status ?? null;

            if (tabState) {
              updateTabJudgement(tabState, platformJobId, judgement, {
                my_status: myStatus,
                company_name: jobEntry?.company_name,
              });
            }

            const title = jobEntry?.title || tabState?.currentJobTitle || tabState?.lastJobRender?.detail?.title || tabState?.lastRender?.detail?.title || null;
            if (tabState && jobEntry?.title) {
              tabState.currentJobTitle = jobEntry.title;
            }

            const desc = describe(judgement, viewState);
            const renderPayload = {
              platform_job_id: platformJobId,
              title: title,
              ...desc,
              hr_note: hrNote,
              my_status: myStatus,
            };

            if (tabState) {
              sendRenderState(tabId, tabState, marksEnabled, renderPayload);
            }
          }

          const jobEntry = res.data?.jobs?.[platformJobId];
          const hrNote = jobEntry?.hr_note ?? null;
          syncChatJobStatusInTabStates(tabStateMap, platformJobId, jobEntry);
          sendResponse({ ok: true, hr_note: hrNote });
        } else {
          const viewState = fromJetError(res);
          sendResponse(formatHrNoteErrorResponse(res, viewState));
        }
      })();
      return true;
    }

    // 7.5 保存岗位状态 (set_job_status)
    if (message?.type === "set_job_status") {
      (async () => {
        const platformJobId = message.platform_job_id;
        const status = message.status ?? null;
        let tabId = message.tabId || sender.tab?.id;
        if (!tabId) {
          tabId = await resolveTabId(message, sender);
        }

        if (!platformJobId) {
          sendResponse({ ok: false, error: "missing_params", viewState: "failed" });
          return;
        }

        const res = await jetClient.call(
          "PUT",
          `/v1/jobs/${encodeURIComponent(platformJobId)}/status`,
          { status },
        );

        if (res.ok) {
          if (isBossPageSender(sender)) {
            let tabState = tabId ? tabStateMap.get(tabId) : null;
            if (!tabState && tabId) {
              tabState = await restoreTabStateIfNeeded(tabId);
            }
            const marksEnabled = await getMarksEnabled();

            const viewState = fromObservation(res.data, platformJobId);
            const jobEntry = res.data?.jobs?.[platformJobId];
            const judgement = jobEntry?.judgement || null;
            const hrNote = jobEntry?.hr_note ?? null;
            const myStatus = jobEntry?.my_status ?? null;

            if (tabState) {
              updateTabJudgement(tabState, platformJobId, judgement, {
                my_status: myStatus ?? status,
                company_name: jobEntry?.company_name,
              });
            }

            const title = jobEntry?.title || tabState?.currentJobTitle || tabState?.lastJobRender?.detail?.title || tabState?.lastRender?.detail?.title || null;
            if (tabState && jobEntry?.title) {
              tabState.currentJobTitle = jobEntry.title;
            }

            const desc = describe(judgement, viewState);
            const renderPayload = {
              platform_job_id: platformJobId,
              title: title,
              ...desc,
              hr_note: hrNote,
              my_status: myStatus,
            };

            if (tabState) {
              sendRenderState(tabId, tabState, marksEnabled, renderPayload);
            }
          }

          const jobEntry = res.data?.jobs?.[platformJobId];
          const newChatStatus = syncChatJobStatusInTabStates(tabStateMap, platformJobId, jobEntry);

          if (tabId) {
            let tabState = tabStateMap.get(tabId);
            if (!tabState) {
              tabState = await restoreTabStateIfNeeded(tabId);
            }
            if (tabState) {
              if (tabState.chatJobStatus && tabState.chatJobStatus.platform_job_id === platformJobId) {
                tabState.chatJobStatus = newChatStatus;
              } else if (tabState.lastChatJobId === platformJobId) {
                tabState.chatJobStatus = newChatStatus;
              }
              schedulePersistTabState(tabId);
            }
          }

          for (const [tId, tState] of tabStateMap.entries()) {
            if (tState?.chatJobStatus?.platform_job_id === platformJobId) {
              schedulePersistTabState(tId);
            }
          }

          try {
            const p = chrome.runtime.sendMessage({
              type: "chat_job_status_updated",
              tabId,
              jobId: platformJobId,
              jobStatus: newChatStatus,
            });
            if (p && typeof p.catch === "function") p.catch(() => {});
          } catch {}

          sendResponse({ ok: true, my_status: jobEntry?.my_status ?? (status ? { status } : null) });
        } else {
          const viewState = fromJetError(res);
          sendResponse({ ok: false, error: res.error, viewState });
        }
      })();
      return true;
    }

    // 8. 页面变动通知：调度读取
    if (message?.type === "page_changed") {
      const tabId = sender.tab?.id;
      if (tabId) {
        processPageRead(tabId, message).catch(() => {});
      }
      return false; // 不回复内容脚本
    }

    // 8.5 获取页面摘要 (get_page_summary，来自侧边栏)
    if (message?.type === "get_page_summary") {
      (async () => {
        const targetTabId = message.tabId || sender.tab?.id;
        const marksEnabled = await getMarksEnabled();
        if (targetTabId && !tabStateMap.has(targetTabId)) {
          await restoreTabStateIfNeeded(targetTabId);
        }
        if (!targetTabId || !tabStateMap.has(targetTabId)) {
          sendResponse({
            ok: true,
            summary: buildPageSummary([], new Map()),
            detail: null,
            marks_enabled: marksEnabled,
            timings: null,
            updated_at: null,
            chat_job_status: null,
          });
          return;
        }
        const tabState = tabStateMap.get(targetTabId);
        const resumeJobId = shouldResumePolling(tabState);
        if (resumeJobId) {
          startPolling(targetTabId, resumeJobId, marksEnabled);
        }
        ensureListPolling(targetTabId);
        sendResponse({
          ok: true,
          summary: buildPageSummary(tabState.listJobs, tabState.listJudgements),
          detail: tabState.pageStatus ?? tabState.lastJobRender?.detail ?? null,
          marks_enabled: marksEnabled,
          timings: tabState.lastTimings || null,
          updated_at: tabState.updated_at || null,
          chat_job_status: tabState.chatJobStatus || null,
        });
      })();
      return true;
    }

    // 9. 获取我的岗位库 (get_my_jobs)
    if (message?.type === "get_my_jobs") {
      (async () => {
        try {
          const path = buildMyJobsPath({
            filter: message.filter,
            hr_only: message.hr_only,
            q: message.q,
          });
          const res = await jetClient.call("GET", path);
          sendResponse({
            ok: res.ok,
            status: res.status,
            data: res.data,
            viewState: res.viewState,
            error: res.error,
          });
        } catch (err) {
          sendResponse({
            ok: false,
            status: 0,
            viewState: "jet_down",
            error: err?.message || String(err),
          });
        }
      })();
      return true;
    }

    // 9.8 聊天顶部即将变动通知 (chat_top_changing)
    if (message?.type === "chat_top_changing") {
      (async () => {
        const tabId = await resolveTabId(message, sender);
        if (!tabId) return;

        let tabState = tabStateMap.get(tabId);
        if (!tabState) {
          tabState = await restoreTabStateIfNeeded(tabId);
        }
        if (!tabState) {
          tabState = getOrCreateTabState(tabId);
        }

        if (tabState.chatSwitchRetryTimer) {
          clearTimeout(tabState.chatSwitchRetryTimer);
          tabState.chatSwitchRetryTimer = null;
        }
        tabState.chatSwitchRetryToken = (tabState.chatSwitchRetryToken || 0) + 1;

        try {
          const p = chrome.runtime.sendMessage({
            type: "chat_top_changing",
            tabId,
          });
          if (p && typeof p.catch === "function") p.catch(() => {});
        } catch {}
      })();
      return false;
    }

    // 10. 聊天顶部区域变动通知 (chat_top_changed)
    if (message?.type === "chat_top_changed") {
      (async () => {
        const tabId = await resolveTabId(message, sender);
        if (!tabId || typeof chrome === "undefined" || typeof chrome.scripting?.executeScript !== "function") return;

        let tabState = tabStateMap.get(tabId);
        if (!tabState) {
          tabState = await restoreTabStateIfNeeded(tabId);
        }
        if (!tabState) {
          tabState = getOrCreateTabState(tabId);
        }

        // 新的 chat_top_changed 到来时取消旧的重试 (T074)
        if (tabState.chatSwitchRetryTimer) {
          clearTimeout(tabState.chatSwitchRetryTimer);
          tabState.chatSwitchRetryTimer = null;
        }
        const currentToken = (tabState.chatSwitchRetryToken || 0) + 1;
        tabState.chatSwitchRetryToken = currentToken;

        const checkJobId = async (attemptIndex) => {
          if (tabState.chatSwitchRetryToken !== currentToken) return;

          let readJobId = null;
          let readJobTitle = null;
          let readCompanyName = null;
          try {
            const results = await chrome.scripting.executeScript({
              target: { tabId },
              world: "MAIN",
              func: readBossChatJobId,
            });
            const res = results?.[0]?.result;
            if (res && res.ok && res.encrypt_job_id) {
              readJobId = res.encrypt_job_id;
              readJobTitle = res.job_title || null;
              readCompanyName = res.company_name || null;
            }
          } catch {}

          if (tabState.chatSwitchRetryToken !== currentToken) return;

          const decision = decideChatSwitchRetry(
            readJobId,
            tabState.lastChatJobId,
            attemptIndex,
            CHAT_SWITCH_RETRY_DELAYS
          );

          if (decision.shouldBroadcast && decision.jobId) {
            const currentBroadcastJobId = decision.jobId;
            tabState.lastChatJobId = currentBroadcastJobId;
            if (tabState.chatJobStatus?.platform_job_id !== currentBroadcastJobId) {
              tabState.chatJobStatus = null;
            }
            schedulePersistTabState(tabId);

            // 立即发 chat_switched（先不等岗位库查询，解除侧边栏遮盖）(E2 优化)
            try {
              const p = chrome.runtime.sendMessage({
                type: "chat_switched",
                tabId,
                jobId: currentBroadcastJobId,
              });
              if (p && typeof p.catch === "function") p.catch(() => {});
            } catch {}

            // 异步入库与查询本机公共岗位库已有数据 (T023 / FR-011–FR-017)
            (async () => {
              const token = await getToken();
              const shouldIngest = shouldIngestChatJob({
                isPaired: Boolean(token),
                jobId: currentBroadcastJobId,
                title: readJobTitle,
              });

              if (shouldIngest) {
                try {
                  await jetClient.postChatJob({
                    platform_job_id: currentBroadcastJobId,
                    title: String(readJobTitle).trim(),
                    company_name: (readCompanyName && String(readCompanyName).trim()) || null,
                  });
                } catch {
                  // 未配对或服务不可用时不入库、不重试
                }
              }

              const jobStatus = await fetchChatJobStatus(currentBroadcastJobId);

              // 乱序检查：如果在这期间又发生了切换，只采用当前聊天岗位 ID 的结果
              if (!isChatJobResponseCurrent(currentBroadcastJobId, tabState.lastChatJobId)) {
                return;
              }

              if (!jobStatus.error) {
                tabState.chatJobStatus = jobStatus;
                schedulePersistTabState(tabId);
              }

              try {
                const p = chrome.runtime.sendMessage({
                  type: "chat_job_status_updated",
                  tabId,
                  jobId: currentBroadcastJobId,
                  jobStatus,
                });
                if (p && typeof p.catch === "function") p.catch(() => {});
              } catch {}
            })();
            return;
          }

          if (decision.shouldRetry && decision.nextDelayMs != null) {
            tabState.chatSwitchRetryTimer = setTimeout(() => {
              tabState.chatSwitchRetryTimer = null;
              checkJobId(attemptIndex + 1);
            }, decision.nextDelayMs);
            return;
          }

          // 误报恢复：若重试结束时岗位 ID 仍与上次相同（顶部变化是误报）(E2 优化)
          if (decision.isUnchanged) {
            try {
              const p = chrome.runtime.sendMessage({
                type: "chat_switch_unchanged",
                tabId,
                jobId: decision.jobId || tabState.lastChatJobId,
              });
              if (p && typeof p.catch === "function") p.catch(() => {});
            } catch {}
            return;
          }

          // 若始终读不到岗位 ID，保持遮盖并显示"未能识别当前聊天的岗位，请稍后再试"，不得恢复旧聊天内容 (E2 优化)
          if (decision.isReadFailed) {
            try {
              const p = chrome.runtime.sendMessage({
                type: "chat_switch_failed",
                tabId,
                error: "job_id_unavailable",
              });
              if (p && typeof p.catch === "function") p.catch(() => {});
            } catch {}
            return;
          }
        };

        await checkJobId(0);
      })();
      return false;
    }

    // 10.5 查询当前聊天岗位库状态 (get_chat_job_status)
    if (message?.type === "get_chat_job_status") {
      (async () => {
        const tabId = await resolveTabId(message, sender);
        if (!tabId) {
          sendResponse({ ok: false, error: "no_tab" });
          return;
        }

        let tabUrl = null;
        if (typeof chrome !== "undefined" && typeof chrome.tabs?.get === "function") {
          try {
            const tab = await chrome.tabs.get(tabId);
            tabUrl = tab?.url || null;
          } catch {}
        }
        if (!isBossUrl(tabUrl)) {
          sendResponse({ ok: false, error: "not_boss_page" });
          return;
        }

        let tabState = tabStateMap.get(tabId);
        if (!tabState) {
          tabState = await restoreTabStateIfNeeded(tabId);
        }
        if (!tabState) {
          tabState = getOrCreateTabState(tabId);
        }

        let jobId = message.jobId || tabState.lastChatJobId;
        if (!jobId && typeof chrome !== "undefined" && typeof chrome.scripting?.executeScript === "function") {
          try {
            const results = await chrome.scripting.executeScript({
              target: { tabId },
              world: "MAIN",
              func: readBossChatJobId,
            });
            const res = results?.[0]?.result;
            if (res && res.ok && res.encrypt_job_id) {
              jobId = res.encrypt_job_id;
              tabState.lastChatJobId = jobId;
              schedulePersistTabState(tabId);
              const token = await getToken();
              if (shouldIngestChatJob({ isPaired: Boolean(token), jobId, title: res.job_title })) {
                try {
                  await jetClient.postChatJob({
                    platform_job_id: jobId,
                    title: String(res.job_title).trim(),
                    company_name: (res.company_name && String(res.company_name).trim()) || null,
                  });
                } catch {}
              }
            }
          } catch {}
        }

        if (!jobId) {
          sendResponse({ ok: false, error: "no_job_id" });
          return;
        }

        if (!tabState.lastChatJobId && jobId) {
          tabState.lastChatJobId = jobId;
        }

        if (
          !shouldFetchChatJobStatus({
            forceRefresh: message.forceRefresh,
            cachedStatus: tabState.chatJobStatus,
            jobId,
          })
        ) {
          sendResponse({ ok: true, jobId, jobStatus: tabState.chatJobStatus });
          return;
        }

        const jobStatus = await fetchChatJobStatus(jobId);
        if (!jobStatus.error && isChatJobResponseCurrent(jobId, tabState.lastChatJobId)) {
          tabState.chatJobStatus = jobStatus;
        }
        sendResponse({ ok: true, jobId, jobStatus });
      })();
      return true;
    }

    // 11. 判断是否聊天页 (is_chat_page)
    if (message?.type === "is_chat_page") {
      (async () => {
        try {
          const tabId = await resolveTabId(message, sender);
          let url = message?.url;
          if (!url && tabId && typeof chrome !== "undefined" && typeof chrome.tabs?.get === "function") {
            try {
              const tab = await chrome.tabs.get(tabId);
              url = tab?.url || "";
            } catch {}
          }
          if (!url && typeof chrome !== "undefined" && typeof chrome.tabs?.query === "function") {
            try {
              const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
              url = activeTab?.url || "";
            } catch {}
          }
          const isChat = typeof url === "string" && (
            url.startsWith("https://www.zhipin.com/web/geek/chat") ||
            url.includes("/web/geek/chat")
          );
          sendResponse({ ok: true, is_chat_page: Boolean(isChat) });
        } catch (err) {
          sendResponse({ ok: false, is_chat_page: false, error: err?.message || String(err) });
        }
      })();
      return true;
    }

    // 12. 深度读取聊天页 (read_chat_page，仅在点"生成"时调用)
    if (message?.type === "read_chat_page") {
      (async () => {
        const tabId = await resolveTabId(message, sender);
        if (!tabId || typeof chrome === "undefined" || typeof chrome.scripting?.executeScript !== "function") {
          sendResponse({ ok: false, error: "no_tab" });
          return;
        }
        try {
          const results = await chrome.scripting.executeScript({
            target: { tabId },
            world: "MAIN",
            func: readBossChatPage,
          });
          const readResult = results?.[0]?.result || null;
          if (readResult && readResult.ok) {
            sendResponse({ ok: true, data: readResult });
          } else {
            sendResponse({
              ok: false,
              error: readResult?.problems?.[0] || "read_failed",
              data: readResult,
            });
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err) });
        }
      })();
      return true;
    }

    // 13. 只读当前聊天岗位 ID (read_chat_job_id，供复制前核对)
    if (message?.type === "read_chat_job_id") {
      (async () => {
        const tabId = await resolveTabId(message, sender);
        if (!tabId || typeof chrome === "undefined" || typeof chrome.scripting?.executeScript !== "function") {
          sendResponse({ ok: false, error: "no_tab" });
          return;
        }
        try {
          const results = await chrome.scripting.executeScript({
            target: { tabId },
            world: "MAIN",
            func: readBossChatJobId,
          });
          const readResult = results?.[0]?.result || null;
          if (readResult && readResult.ok && readResult.encrypt_job_id) {
            let tabState = tabStateMap.get(tabId);
            if (tabState) {
              tabState.lastChatJobId = readResult.encrypt_job_id;
              schedulePersistTabState(tabId);
            }
            sendResponse({ ok: true, encrypt_job_id: readResult.encrypt_job_id });
          } else {
            sendResponse({
              ok: false,
              error: readResult?.problems?.[0] || "read_failed",
              encrypt_job_id: null,
            });
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err) });
        }
      })();
      return true;
    }

    // 14. 聊天生成脱敏预览 (chat_preview)
    if (message?.type === "chat_preview") {
      (async () => {
        try {
          const payload = message.payload;
          const res = await jetClient.preview(payload);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 15. 聊天话术生成 (chat_generate)
    if (message?.type === "chat_generate") {
      (async () => {
        try {
          const payload = message.payload;
          const res = await jetClient.generate(payload);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 16. 记录数据发送同意 (chat_consent)
    if (message?.type === "chat_consent") {
      (async () => {
        try {
          const res = await jetClient.consent();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 17. 撤回数据发送同意 (chat_revoke_consent)
    if (message?.type === "chat_revoke_consent") {
      (async () => {
        try {
          const res = await jetClient.revokeConsent();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 18. 查询数据发送同意状态 (chat_consent_status)
    if (message?.type === "chat_consent_status") {
      (async () => {
        try {
          const res = await jetClient.consentStatus();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 19. 获取我的经历素材 (get_experience)
    if (message?.type === "get_experience") {
      (async () => {
        try {
          const res = await jetClient.getExperience();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 20. 更新我的经历素材 (put_experience)
    if (message?.type === "put_experience") {
      (async () => {
        try {
          const items = message.items;
          const res = await jetClient.putExperience(items);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 21. 获取我的简历 (get_resumes)
    if (message?.type === "get_resumes") {
      (async () => {
        try {
          const res = await jetClient.getResumes();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 21.1 上传简历 PDF 并生成画像 (upload_resume)
    if (message?.type === "upload_resume") {
      (async () => {
        try {
          const payload = {
            slot: message.slot,
            name: message.name,
            pdf_base64: message.pdf_base64,
            self_name: message.self_name,
          };
          const res = await jetClient.uploadResume(payload);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 21.2 重新生成简历画像 (regenerate_resume)
    if (message?.type === "regenerate_resume") {
      (async () => {
        try {
          const slot = message.slot;
          const payload = { self_name: message.self_name };
          const res = await jetClient.regenerateResume(slot, payload);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 21.3 删除单份简历 (delete_resume)
    if (message?.type === "delete_resume") {
      (async () => {
        try {
          const slot = message.slot;
          const res = await jetClient.deleteResume(slot);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 22. 保存我的简历 (put_resumes)
    if (message?.type === "put_resumes") {
      (async () => {
        try {
          const items = message.items;
          const res = await jetClient.putResumes(items);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 23. 获取从严行业 (get_strict_industries)
    if (message?.type === "get_strict_industries") {
      (async () => {
        try {
          const res = await jetClient.getStrictIndustries();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 24. 保存从严行业 (save_strict_industries)
    if (message?.type === "save_strict_industries") {
      (async () => {
        try {
          const selected = message.selected ?? [];
          const res = await jetClient.putStrictIndustries(selected);
          sendResponse(res);
          if (res.ok) {
            handleProfileSaved().catch(() => {});
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 25. 获取 DeepSeek API Key 状态 (get_llm_key)
    if (message?.type === "get_llm_key") {
      (async () => {
        try {
          const res = await jetClient.getLlmKey();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 26. 保存 DeepSeek API Key (put_llm_key)
    if (message?.type === "put_llm_key") {
      (async () => {
        try {
          const apiKey = typeof message.api_key === "string" ? message.api_key.trim() : "";
          const res = await jetClient.putLlmKey(apiKey);
          sendResponse(res);
          if (res.ok) {
            handleProfileSaved().catch(() => {});
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 27. 清除 DeepSeek API Key (delete_llm_key)
    if (message?.type === "delete_llm_key") {
      (async () => {
        try {
          const res = await jetClient.deleteLlmKey();
          sendResponse(res);
          if (res.ok) {
            handleProfileSaved().catch(() => {});
          }
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 28. 测试 DeepSeek API Key 连接 (test_llm_key)
    if (message?.type === "test_llm_key") {
      (async () => {
        try {
          const rawKey = typeof message.api_key === "string" ? message.api_key.trim() : "";
          const res = await jetClient.testLlmKey(rawKey || null);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 29. 获取预判设置 (get_prejudge_settings)
    if (message?.type === "get_prejudge_settings") {
      (async () => {
        try {
          const res = await jetClient.getPrejudgeSettings();
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }

    // 30. 更新预判设置 (put_prejudge_settings)
    if (message?.type === "put_prejudge_settings") {
      (async () => {
        try {
          prejudgeQuotaExhaustedDate = null;
          const res = await jetClient.putPrejudgeSettings(message.daily_prejudge_limit);
          sendResponse(res);
        } catch (err) {
          sendResponse({ ok: false, error: err?.message || String(err), viewState: "jet_down" });
        }
      })();
      return true;
    }
  });
}

async function setupSidePanelBehavior(chromeApi = (typeof chrome !== "undefined" ? chrome : undefined)) {
  if (chromeApi && chromeApi.sidePanel?.setPanelBehavior) {
    try {
      await chromeApi.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
    } catch {
      // 避免不支持或调用失败时抛出未捕获错误
    }
  }
}

// 插件安装或重新加载（reason 为 install / update）后，已打开的 BOSS 页面里还是旧的内容脚本，
// 与插件断开；逐个刷新这些页面。查询按网址过滤依赖 https://*.zhipin.com/* 站点权限，reload 不需要权限。
// Chrome 自身升级（chrome_update）等其他原因不刷新。
async function reloadOpenBossTabs(details, chromeApi = (typeof chrome !== "undefined" ? chrome : undefined)) {
  const reason = details?.reason;
  if (reason !== "install" && reason !== "update") return 0;
  if (!chromeApi?.tabs?.query || !chromeApi.tabs.reload) return 0;
  let tabs = [];
  try {
    tabs = await chromeApi.tabs.query({ url: "https://*.zhipin.com/*" });
  } catch {
    return 0;
  }
  let reloaded = 0;
  for (const tab of tabs || []) {
    if (typeof tab?.id !== "number") continue;
    try {
      await chromeApi.tabs.reload(tab.id);
      reloaded++;
    } catch {
      // 单个页面刷新失败不影响其他页面
    }
  }
  return reloaded;
}

/**
 * 画像变化后的刷新（T008 选 A / FR-006）：
 * 保存画像成功后清空所有 BOSS 标签页的 listSent，
 * 并对当前为搜索列表页的标签页各调用一次现有读取流程（只读页面已加载的数据，不导航、不刷新页面）。
 *
 * @param {object} [chromeApi=chrome]
 * @returns {Promise<number>} 发起重新读取的搜索列表标签页数量
 */
async function handleProfileSaved(chromeApi = (typeof chrome !== "undefined" ? chrome : undefined)) {
  prejudgeQuotaExhaustedDate = null;
  // 1. 清空内存中已维护的所有 tabState 的 listSent 与 listPrejudge
  for (const state of tabStateMap.values()) {
    if (state?.listSent) {
      state.listSent.clear();
    }
    if (state?.listPrejudge) {
      state.listPrejudge.clear();
    }
  }

  if (!chromeApi?.tabs?.query) {
    return 0;
  }

  let tabs = [];
  try {
    tabs = await chromeApi.tabs.query({ url: "https://*.zhipin.com/*" });
  } catch {
    return 0;
  }

  let refreshedCount = 0;
  for (const tab of tabs || []) {
    if (typeof tab?.id !== "number") continue;
    let state = tabStateMap.get(tab.id);
    if (!state) {
      state = await restoreTabStateIfNeeded(tab.id);
    }
    if (state?.listSent) {
      state.listSent.clear();
    }
    if (state?.listPrejudge) {
      state.listPrejudge.clear();
    }

    // 2. 判断当前是否为搜索列表页（/web/geek/job）
    const url = tab.url || "";
    let isSearchList = false;
    try {
      if (url) {
        const parsed = new URL(url);
        isSearchList = parsed.pathname.startsWith("/web/geek/job");
      }
    } catch {
      isSearchList = url.includes("/web/geek/job");
    }

    if (isSearchList) {
      try {
        await processPageRead(tab.id, { reason: "profile_updated" });
        refreshedCount++;
      } catch {
        // 单个标签读取失败不影响其他标签
      }
    }
  }
  return refreshedCount;
}

// Service worker 顶层调用
setupSidePanelBehavior().catch(() => {});

// 生命周期事件监听
if (typeof chrome !== "undefined" && chrome.runtime?.onInstalled) {
  chrome.runtime.onInstalled.addListener((details) => {
    // 旧版本把上传简历时填的姓名存在浏览器里，安装或更新插件时删掉（体检第 20 条）
    chrome.storage?.local?.remove("resumeSelfName").catch(() => {});
    setupSidePanelBehavior().catch(() => {});
    reloadOpenBossTabs(details).catch(() => {});
  });
}

if (typeof chrome !== "undefined" && chrome.runtime?.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    setupSidePanelBehavior().catch(() => {});
  });
}

if (typeof chrome !== "undefined" && chrome.storage?.onChanged) {
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if ((!areaName || areaName === "local") && changes.marksEnabled) {
      const newMarksEnabled = changes.marksEnabled.newValue !== false;
      for (const [tabId, state] of tabStateMap.entries()) {
        const detail = state.pageStatus ?? state.lastJobRender?.detail ?? state.lastRender?.detail ?? null;
        sendRenderState(tabId, state, newMarksEnabled, detail);
      }
    }
  });
}

function getPrejudgeQuotaExhaustedDate() {
  return prejudgeQuotaExhaustedDate;
}

function setPrejudgeQuotaExhaustedDate(d) {
  prejudgeQuotaExhaustedDate = d;
}

export {
  tabStateMap,
  sendListObservations,
  getMarksEnabled,
  processPageRead,
  setupSidePanelBehavior,
  fetchChatJobStatus,
  reloadOpenBossTabs,
  handleProfileSaved,
  jetClient,
  getPrejudgeQuotaExhaustedDate,
  setPrejudgeQuotaExhaustedDate,
  ensureListPolling,
  clearListPollTimer,
  updateTabJudgement,
  sendRenderState,
  handleSendDetail,
  startPolling,
  getOrCreateTabState,
};
