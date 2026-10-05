import {
  formatChatError,
  formatModeDecision,
  formatAttribution,
  formatLoadedMessagesNotice,
  formatSourceNote,
  getCopyableText,
  verifyCopyJobId,
  getDisplaySuggestions,
  getNextForceMode,
  getSwitchModeButtonText,
  NO_JET_JUDGEMENT_NOTICE,
  getDisplayNotices,
  getDisplayDroppedSummary,
  CHAT_AUTO_ACTIONS,
  computeChatCacheKey,
  getLocalDateString,
  isQuotaExhaustedToday,
  decideChatAutoAction,
  openMyJobsTab,
  runChatFallbackCheck,
  COPY_REJECTED_NOTICE,
  formatChatJobStatus,
  isChatJobResponseCurrent,
  switchChatJobWithEditorFlush,
  NOT_IN_LIBRARY_NOTICE,
  pickCachedChatJobStatus,
  formatResumeHighlightText,
  formatUnansweredFactNotices,
  resolveAutoGenerateSetting,
  CONSENT_MODAL_DESC,
  CHAT_MASK_TEXTS,
  CHAT_MASK_STATUS,
  createInitialMaskState,
  decideChatMaskOnChanging,
  decideChatMaskOnSwitched,
  decideChatMaskOnUnchanged,
  decideChatMaskOnFailed,
  decideChatJobStatusUpdate,
  reduceChatMaskState,
  recordMaskButtonStates,
  decideUnmaskedButtonStates,
  formatJudgeQuotaText,
  formatAssistQuotaText,
  computeAssistQuotaAfterGenerate,
  isLastMessageFromHr,
  decideNewMessageAction,
  NEW_MESSAGE_HINT,
  getLatestChatCacheKey,
  setLatestChatCacheKey,
  computeNextJobStatus,
  getChatJobStatusButtons,
  shouldShowNoJudgementNotice,
  mergeChatPreviewResult,
  decideChatPreviewRefresh,
  hasHrResumeRequest,
  decideResumeHighlightState,
  resetResumePromptOnSwitch,
  decideChatResumePromptState,
  decideQuotaBanner,
  decideJobCardBadge,
  computeLlmKeyConfigured,
  buildChatPreviewPayload,
} from "./chat-view.js";
import { createAutoSaveCoordinator } from "./label-form.js";

function formatJudgedAt(rawDate) {
  if (!rawDate) return "";
  try {
    const d = new Date(rawDate);
    if (Number.isNaN(d.getTime())) return String(rawDate);
    const pad = (n) => String(n).padStart(2, "0");
    const month = pad(d.getMonth() + 1);
    const day = pad(d.getDate());
    const hours = pad(d.getHours());
    const minutes = pad(d.getMinutes());
    return `${month}-${day} ${hours}:${minutes}`;
  } catch {
    return String(rawDate);
  }
}

async function getActiveTabId() {
  if (typeof chrome === "undefined" || !chrome.tabs?.query) {
    return null;
  }
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab?.id ?? null;
  } catch {
    return null;
  }
}

let currentAssistLimit = 50;
let currentAssistRemaining = null;

function updateAssistQuotaAfterGenerate(quotaRemaining, limit = currentAssistLimit) {
  const quota = computeAssistQuotaAfterGenerate(quotaRemaining, limit);
  currentAssistRemaining = quota.remaining;
  const chatQuotaEl = document.getElementById("chat-quota-text");
  if (chatQuotaEl) {
    chatQuotaEl.textContent = formatAssistQuotaText(quota);
  }
  return quota;
}

let currentLlmKeyConfigured = true;
let currentDetail = null;

function renderQuota(statusRes) {
  const bannerEl = document.getElementById("quota-banner");
  const textEl = document.getElementById("quota-text");
  const chatQuotaEl = document.getElementById("chat-quota-text");
  if (!bannerEl || !textEl) return;

  const nextLlmKeyConfigured = computeLlmKeyConfigured(statusRes);
  const keyConfigChanged = nextLlmKeyConfigured !== currentLlmKeyConfigured;
  currentLlmKeyConfigured = nextLlmKeyConfigured;

  const decision = decideQuotaBanner(statusRes);
  bannerEl.className = decision.className;
  textEl.textContent = decision.text;

  if (keyConfigChanged) {
    const assistantCard = document.getElementById("hr-assistant-card");
    const isChat = Boolean(assistantCard && assistantCard.style.display !== "none");
    if (isChat) {
      renderChatJob(currentChatJobStatus);
    } else {
      renderCurrentJob(currentDetail);
    }
  }

  if (decision.isNoKey) {
    if (chatQuotaEl) chatQuotaEl.textContent = "";
    return;
  }

  if (!statusRes || statusRes.state !== "paired") {
    if (chatQuotaEl) chatQuotaEl.textContent = "";
    return;
  }

  if (chatQuotaEl) {
    if (statusRes.status?.assist_quota) {
      currentAssistLimit = statusRes.status.assist_quota.limit ?? 50;
      currentAssistRemaining = statusRes.status.assist_quota.remaining;
      chatQuotaEl.textContent = formatAssistQuotaText(statusRes.status.assist_quota);
    } else {
      chatQuotaEl.textContent = "";
    }
  }
}

function renderMarksNotice(marksEnabled) {
  const noticeEl = document.getElementById("marks-disabled-notice");
  if (!noticeEl) return;
  if (marksEnabled === false) {
    noticeEl.style.display = "block";
    noticeEl.textContent = "页面标记已关闭，结论只在这里显示";
  } else {
    noticeEl.style.display = "none";
  }
}

function renderCurrentJob(detail) {
  currentDetail = detail;
  const emptyEl = document.getElementById("current-job-empty");
  const detailEl = document.getElementById("current-job-detail");
  if (!emptyEl || !detailEl) return;

  if (!detail || !detail.platform_job_id) {
    let statusText =
      (typeof detail?.label === "string" && detail.label.trim()) ||
      (typeof detail?.status_label === "string" && detail.status_label.trim()) ||
      null;

    if (!currentLlmKeyConfigured || detail?.view_state === "no_llm_key") {
      statusText = "请先在设置页填写 DeepSeek API Key";
    }

    emptyEl.style.display = "block";
    emptyEl.textContent = statusText || "未打开岗位详情";
    detailEl.style.display = "none";
    detailEl.replaceChildren();
    return;
  }

  emptyEl.style.display = "none";
  detailEl.style.display = "block";
  detailEl.replaceChildren();

  const headerRow = document.createElement("div");
  headerRow.className = "job-header-row";

  const titleEl = document.createElement("div");
  titleEl.className = "job-title";
  titleEl.textContent = detail.title || detail.platform_job_id;
  headerRow.appendChild(titleEl);

  let badge = null;
  const badgeInfo = decideJobCardBadge(detail, currentLlmKeyConfigured);
  if (badgeInfo && badgeInfo.text) {
    badge = document.createElement("span");
    badge.className = `badge badge-${badgeInfo.tone || "gray"}`;
    badge.textContent = badgeInfo.text;
    headerRow.appendChild(badge);
  }

  const isStale = Boolean(
    detail.view_state === "stale" ||
    detail.stale ||
    (Array.isArray(detail.stale_reasons) && detail.stale_reasons.length > 0)
  );
  if (isStale) {
    if (badge) {
      badge.classList.add("stale");
    }
    const staleBadge = document.createElement("span");
    staleBadge.className = "badge badge-stale";
    staleBadge.textContent = "可能过时";
    headerRow.appendChild(staleBadge);
  }

  detailEl.appendChild(headerRow);

  if (detail.summary_reason) {
    const reasonEl = document.createElement("div");
    reasonEl.className = "job-reason";
    reasonEl.textContent = detail.summary_reason;
    detailEl.appendChild(reasonEl);
  }

  if (detail.judged_at) {
    const timeEl = document.createElement("div");
    timeEl.className = "job-time";
    timeEl.textContent = `判断时间：${formatJudgedAt(detail.judged_at)}`;
    detailEl.appendChild(timeEl);
  }
}

let activeChatJobEditor = null;
let currentChatJobStatus = null;
let currentHasHrResumeRequest = false;
let currentResumeRequestJobId = null;

function applyResumeHighlightToCurrentJob() {
  const detailEl = document.getElementById("current-job-detail");
  if (!detailEl || detailEl.style.display === "none") {
    return;
  }
  if (!currentChatJobStatus) {
    return;
  }

  // 若处于编辑中，避免整卡重刷破坏编辑输入，只局部更新顶部提示
  if (activeChatJobEditor) {
    const targetJobId = currentChatJobId || currentChatJobStatus.platform_job_id;
    const isRequestCurrent = isChatJobResponseCurrent(currentResumeRequestJobId, targetJobId);
    const promptDecision = decideChatResumePromptState({
      currentChatJobId: targetJobId,
      requestJobId: isRequestCurrent ? currentResumeRequestJobId : null,
      hasHrResumeRequest: isRequestCurrent ? currentHasHrResumeRequest : false,
      jobStatus: currentChatJobStatus,
    });
    const existing = detailEl.querySelector("#current-job-resume-highlight");
    if (existing) {
      existing.remove();
    }
    if (promptDecision.state === "highlight") {
      const banner = document.createElement("div");
      banner.id = "current-job-resume-highlight";
      banner.className = "job-resume-highlight";
      const bannerTitle = document.createElement("div");
      bannerTitle.className = "job-resume-highlight-title";
      bannerTitle.textContent = formatResumeHighlightText(currentChatJobStatus);
      banner.appendChild(bannerTitle);
      const reason = currentChatJobStatus.resume_suggestion?.reason ? currentChatJobStatus.resume_suggestion.reason.trim() : "";
      if (reason) {
        const reasonEl = document.createElement("div");
        reasonEl.className = "job-resume-highlight-reason";
        reasonEl.textContent = reason;
        banner.appendChild(reasonEl);
      }
      detailEl.prepend(banner);
    } else if (promptDecision.state === "hint") {
      const banner = document.createElement("div");
      banner.id = "current-job-resume-highlight";
      banner.className = "job-resume-hint";
      banner.textContent = "点重新判断可获得简历建议";
      detailEl.prepend(banner);
    }
    return;
  }

  renderChatJob(currentChatJobStatus);
}

function updateResumePromptWithChatData(chatData) {
  if (!chatData || !chatData.encrypt_job_id) {
    return;
  }
  const jobId = chatData.encrypt_job_id;
  const targetJobId = currentChatJobId || currentChatJobStatus?.platform_job_id;
  if (!isChatJobResponseCurrent(jobId, targetJobId)) {
    return;
  }

  const hasRequest = hasHrResumeRequest(chatData.messages);
  currentHasHrResumeRequest = hasRequest;
  currentResumeRequestJobId = jobId;

  applyResumeHighlightToCurrentJob();
}

function refreshResumePromptForActiveTab(tabId) {
  if (!tabId || currentMaskState?.isMasked) return;
  chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, (readRes) => {
    if (readRes?.ok && readRes.data) {
      updateResumePromptWithChatData(readRes.data);
    }
  });
}

function flushAndClearActiveChatJobEditor() {
  if (activeChatJobEditor) {
    if (activeChatJobEditor.coordinator && typeof activeChatJobEditor.coordinator.flush === "function") {
      const text = typeof activeChatJobEditor.getText === "function" ? activeChatJobEditor.getText() : "";
      try {
        activeChatJobEditor.coordinator.flush(text);
      } catch {}
    }
    activeChatJobEditor = null;
  }
}

function renderChatJob(status) {
  const emptyEl = document.getElementById("current-job-empty");
  const detailEl = document.getElementById("current-job-detail");
  if (!emptyEl || !detailEl) return;

  currentChatJobStatus = status;

  if (!status || !status.platform_job_id) {
    emptyEl.style.display = "block";
    emptyEl.textContent = "未打开岗位详情";
    detailEl.style.display = "none";
    detailEl.replaceChildren();
    return;
  }

  if (status.error || status.in_library === null) {
    emptyEl.style.display = "block";
    let msg = "Jet 未运行";
    if (status.view_state === "unpaired") {
      msg = "未配对";
    }
    emptyEl.textContent = msg;
    detailEl.style.display = "none";
    detailEl.replaceChildren();
    return;
  }

  if (!status.in_library) {
    emptyEl.style.display = "block";
    emptyEl.textContent = status.notice || NOT_IN_LIBRARY_NOTICE;
    detailEl.style.display = "none";
    detailEl.replaceChildren();
    return;
  }

  emptyEl.style.display = "none";
  detailEl.style.display = "block";
  detailEl.replaceChildren();

  const currentJobId = status.platform_job_id;

  // 0.5 检查并渲染 HR 要简历的突出提示 (006 US3, T018)
  const isRequestCurrent = isChatJobResponseCurrent(currentResumeRequestJobId, currentJobId);
  const promptDecision = decideChatResumePromptState({
    currentChatJobId: currentJobId,
    requestJobId: isRequestCurrent ? currentResumeRequestJobId : null,
    hasHrResumeRequest: isRequestCurrent ? currentHasHrResumeRequest : false,
    jobStatus: status,
  });
  const highlightState = promptDecision.state;

  if (highlightState === "highlight") {
    const banner = document.createElement("div");
    banner.id = "current-job-resume-highlight";
    banner.className = "job-resume-highlight";

    const bannerTitle = document.createElement("div");
    bannerTitle.className = "job-resume-highlight-title";
    bannerTitle.textContent = formatResumeHighlightText(status);
    banner.appendChild(bannerTitle);

    const reason = status.resume_suggestion?.reason ? status.resume_suggestion.reason.trim() : "";
    if (reason) {
      const reasonEl = document.createElement("div");
      reasonEl.className = "job-resume-highlight-reason";
      reasonEl.textContent = reason;
      banner.appendChild(reasonEl);
    }
    detailEl.appendChild(banner);
  } else if (highlightState === "hint") {
    const banner = document.createElement("div");
    banner.id = "current-job-resume-highlight";
    banner.className = "job-resume-hint";
    banner.textContent = "点重新判断可获得简历建议";
    detailEl.appendChild(banner);
  }

  // 1. 标题与 Jet 结论行
  const headerRow = document.createElement("div");
  headerRow.className = "job-header-row";

  const titleEl = document.createElement("div");
  titleEl.className = "job-title";
  titleEl.textContent = status.title || currentJobId;
  headerRow.appendChild(titleEl);

  const badgeDecision = decideJobCardBadge(status, currentLlmKeyConfigured);
  let badge = null;
  if (badgeDecision && badgeDecision.isNoKey) {
    badge = document.createElement("span");
    badge.className = "badge badge-gray";
    badge.textContent = badgeDecision.text;
    headerRow.appendChild(badge);
  } else if (status.verdict_label) {
    badge = document.createElement("span");
    badge.className = `badge badge-${status.verdict_tone || "gray"}`;
    badge.textContent = status.verdict_label;
    headerRow.appendChild(badge);
  } else {
    badge = document.createElement("span");
    badge.className = "badge badge-gray";
    badge.textContent = "未判断";
    headerRow.appendChild(badge);
  }

  if (status.stale) {
    if (badge) {
      badge.classList.add("stale");
    }
    const staleBadge = document.createElement("span");
    staleBadge.className = "badge badge-stale";
    staleBadge.textContent = "可能过时";
    headerRow.appendChild(staleBadge);
  }

  detailEl.appendChild(headerRow);

  // 1.5 投递状态按钮组（收藏 / 已投递 / 不考虑）(T006 / FR-001–FR-004)
  const statusRow = document.createElement("div");
  statusRow.className = "jet-status-actions";

  const initialButtonsData = getChatJobStatusButtons(status.my_status);
  let activeStatus = initialButtonsData.activeStatus;

  const tipEl = document.createElement("span");
  tipEl.className = "jet-status-btn-tip";
  let tipTimer = null;

  const statusButtons = {};

  function refreshStatusButtons(currentStatusVal, options = {}) {
    const opts = typeof options === "boolean" ? { disabled: options } : options;
    const { activeStatus: nextActive, buttons } = getChatJobStatusButtons(currentStatusVal, opts);
    activeStatus = nextActive;
    for (const b of buttons) {
      const btnEl = statusButtons[b.key];
      if (btnEl) {
        if (b.active) {
          btnEl.classList.add("active");
        } else {
          btnEl.classList.remove("active");
        }
        btnEl.disabled = b.disabled;
      }
    }
  }

  for (const btnData of initialButtonsData.buttons) {
    const sBtn = document.createElement("button");
    sBtn.type = "button";
    sBtn.className = "jet-status-btn" + (btnData.active ? " active" : "");
    sBtn.disabled = btnData.disabled;
    sBtn.textContent = btnData.label;
    statusButtons[btnData.key] = sBtn;

    sBtn.addEventListener("click", async () => {
      const prevStatus = activeStatus;
      const nextStatus = computeNextJobStatus(activeStatus, btnData.key);

      // 保存中禁用按钮防重复提交并乐观更新选中态 (FR-003)
      refreshStatusButtons(nextStatus, { saving: true });

      if (tipTimer) {
        clearTimeout(tipTimer);
        tipTimer = null;
      }
      tipEl.textContent = "";

      const currentTabId = await getActiveTabId();

      try {
        chrome.runtime.sendMessage(
          {
            type: "set_job_status",
            platform_job_id: currentJobId,
            status: nextStatus,
            tabId: currentTabId,
          },
          (res) => {
            // 防乱序：响应到达时若当前聊天岗位 ID 已变则丢弃 (FR-004)
            const activeJobId = currentChatJobId || currentChatJobStatus?.platform_job_id;
            if (!isChatJobResponseCurrent(currentJobId, activeJobId)) {
              return;
            }

            if (res && res.ok) {
              status.my_status = nextStatus ? { status: nextStatus } : null;
              refreshStatusButtons(nextStatus, { disabled: false });
            } else {
              refreshStatusButtons(prevStatus, { disabled: false });
              tipEl.textContent = "保存失败";
              tipTimer = setTimeout(() => {
                tipEl.textContent = "";
              }, 2000);
            }
          }
        );
      } catch {
        const activeJobId = currentChatJobId || currentChatJobStatus?.platform_job_id;
        if (isChatJobResponseCurrent(currentJobId, activeJobId)) {
          refreshStatusButtons(prevStatus, { disabled: false });
          tipEl.textContent = "保存失败";
          tipTimer = setTimeout(() => {
            tipEl.textContent = "";
          }, 2000);
        }
      }
    });

    statusRow.appendChild(sBtn);
  }

  statusRow.appendChild(tipEl);
  detailEl.appendChild(statusRow);

  // 2. 公司名
  if (status.company_name) {
    const companyEl = document.createElement("div");
    companyEl.className = "job-company";
    companyEl.textContent = status.company_name;
    detailEl.appendChild(companyEl);
  }

  // 3. 结论原因（如有）
  if (status.summary_reason) {
    const reasonEl = document.createElement("div");
    reasonEl.className = "job-reason";
    reasonEl.textContent = status.summary_reason;
    detailEl.appendChild(reasonEl);
  }

  // 3.4 简历建议 (006 FR-007, US1)
  // 当已有顶部突出高亮时不在底部重复显示 (006 US3, D7)
  if (
    highlightState !== "highlight" &&
    status.resume_suggestion &&
    status.resume_suggestion.name &&
    status.verdict_label !== "不建议投"
  ) {
    const resumeEl = document.createElement("div");
    resumeEl.className = "job-resume-suggestion";

    const tagEl = document.createElement("div");
    tagEl.className = "job-resume-tag";
    tagEl.textContent = `建议投：${status.resume_suggestion.name}`;
    tagEl.title = status.resume_suggestion.reason || "";

    const reasonEl = document.createElement("div");
    reasonEl.className = "job-resume-reason";
    reasonEl.textContent = status.resume_suggestion.reason || "";
    reasonEl.style.display = "none";

    let isExpanded = false;
    tagEl.addEventListener("click", () => {
      isExpanded = !isExpanded;
      reasonEl.style.display = isExpanded ? "block" : "none";
    });

    resumeEl.appendChild(tagEl);
    resumeEl.appendChild(reasonEl);
    detailEl.appendChild(resumeEl);
  }

  // 3.5 提示语（如在库但未判断："点「查看职位」获取详情并判断"）(FR-015, T024)
  if (status.notice && status.notice !== "no_llm_key") {
    const noticeEl = document.createElement("div");
    noticeEl.className = "job-notice";
    noticeEl.textContent = status.notice;
    detailEl.appendChild(noticeEl);
  }

  // 4. 判断时间（如有）
  if (status.judged_at) {
    const timeEl = document.createElement("div");
    timeEl.className = "job-time";
    timeEl.textContent = `判断时间：${formatJudgedAt(status.judged_at)}`;
    detailEl.appendChild(timeEl);
  }

  // 5. HR 实际情况区块 (FR-058, FR-070, T061)
  const hrBlock = document.createElement("div");
  hrBlock.className = "job-hr-block";

  const hrHeader = document.createElement("div");
  hrHeader.className = "job-hr-header";

  const hrLabel = document.createElement("span");
  hrLabel.className = "job-hr-label";
  hrLabel.textContent = "HR 实际情况";
  hrHeader.appendChild(hrLabel);

  const hrActions = document.createElement("div");
  hrActions.className = "job-hr-actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "hr-btn-link";
  editBtn.textContent = "修改";
  hrActions.appendChild(editBtn);

  hrHeader.appendChild(hrActions);
  hrBlock.appendChild(hrHeader);

  const hrContentEl = document.createElement("div");
  hrContentEl.className = "job-hr-content";
  const hasHrNote = Boolean(typeof status.hr_note === "string" && status.hr_note.trim().length > 0);
  hrContentEl.textContent = hasHrNote ? status.hr_note : "未记录";
  if (!hasHrNote) {
    hrContentEl.classList.add("empty");
  }
  hrBlock.appendChild(hrContentEl);

  // 编辑区域（默认隐藏）
  const editArea = document.createElement("div");
  editArea.className = "job-hr-edit-area";
  editArea.style.display = "none";

  const textarea = document.createElement("textarea");
  textarea.className = "job-hr-textarea";
  textarea.value = status.hr_note || "";
  textarea.placeholder = "记录 HR 透露的实际情况（≤ 200 字，清空请到岗位卡片）";
  textarea.maxLength = 200;
  editArea.appendChild(textarea);

  const editFooter = document.createElement("div");
  editFooter.className = "job-hr-edit-footer";

  const editError = document.createElement("span");
  editError.className = "job-hr-edit-error";
  editFooter.appendChild(editError);

  const editBtns = document.createElement("div");
  editBtns.className = "job-hr-edit-btns";

  const doneBtn = document.createElement("button");
  doneBtn.type = "button";
  doneBtn.className = "btn-sm primary";
  doneBtn.textContent = "完成";
  editBtns.appendChild(doneBtn);

  editFooter.appendChild(editBtns);
  editArea.appendChild(editFooter);
  hrBlock.appendChild(editArea);

  detailEl.appendChild(hrBlock);

  // 自动保存协调器 (FR-070, T061)
  const coordinator = createAutoSaveCoordinator({
    delayMs: 1000,
    initialSaved: status.hr_note || null,
    emptyMessage: "清空请到岗位卡片操作",
    save: async (note) => {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage(
          {
            type: "save_hr_note",
            platform_job_id: currentJobId,
            note: note,
            source: "chat_sidebar",
          },
          (res) => {
            resolve(res || { ok: false, error: "empty_response" });
          }
        );
      });
    },
    onStatusChange: ({ status: sStatus, message }) => {
      if (sStatus === "empty") {
        editError.textContent = message;
      } else if (sStatus === "saving") {
        editError.textContent = "保存中...";
      } else if (sStatus === "saved") {
        editError.textContent = "已自动保存";
      } else if (sStatus === "error") {
        editError.textContent = message || "保存失败";
      }
    },
    onSaveSuccess: (savedNote) => {
      status.hr_note = savedNote;
      hrContentEl.textContent = savedNote;
      hrContentEl.classList.remove("empty");
    },
  });

  textarea.addEventListener("input", () => {
    if (textarea.value.trim().length > 0 && editError.textContent === "清空请到岗位卡片操作") {
      editError.textContent = "";
    }
    coordinator.onInput(textarea.value);
  });

  textarea.addEventListener("blur", () => {
    coordinator.onBlur(textarea.value);
  });

  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const currentSaved = status.hr_note ? status.hr_note.trim() : "";
    coordinator.setLastSaved(currentSaved);
    textarea.value = currentSaved;
    editError.textContent = "";
    doneBtn.disabled = false;
    hrContentEl.style.display = "none";
    editBtn.style.display = "none";
    editArea.style.display = "";
    textarea.focus();

    activeChatJobEditor = {
      jobId: currentJobId,
      coordinator,
      getText: () => textarea.value,
    };
  });

  doneBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    doneBtn.disabled = true;

    try {
      const res = await coordinator.flush(textarea.value);
      if (res.ok) {
        if (res.note) {
          status.hr_note = res.note;
          hrContentEl.textContent = res.note;
          hrContentEl.classList.remove("empty");
        }
        editArea.style.display = "none";
        hrContentEl.style.display = "";
        editBtn.style.display = "";
        editError.textContent = "";
        doneBtn.disabled = false;
        activeChatJobEditor = null;
      } else {
        doneBtn.disabled = false;
        editError.textContent = res.message || res.error || "保存失败";
      }
    } catch (err) {
      doneBtn.disabled = false;
      editError.textContent = err?.message || "网络异常";
    }
  });
}

function renderPageSummary(summary) {
  const titleEl = document.getElementById("list-jobs-title");
  const emptyEl = document.getElementById("list-jobs-empty");
  const itemsEl = document.getElementById("list-jobs-items");
  if (!titleEl || !emptyEl || !itemsEl) return;

  const count = summary?.count ?? 0;
  titleEl.textContent = `本页已判断过的岗位（${count}）`;

  if (count === 0 || !Array.isArray(summary?.items) || summary.items.length === 0) {
    emptyEl.style.display = "block";
    emptyEl.textContent = summary?.empty_text || "本页没有已判断过的岗位";
    itemsEl.style.display = "none";
    itemsEl.replaceChildren();
    return;
  }

  emptyEl.style.display = "none";
  itemsEl.style.display = "block";
  itemsEl.replaceChildren();

  for (const item of summary.items) {
    const li = document.createElement("li");
    li.className = "job-item";

    const headerRow = document.createElement("div");
    headerRow.className = "job-header-row";

    const titleEl = document.createElement("div");
    titleEl.className = "job-title";
    titleEl.textContent = item.title || item.platform_job_id;
    headerRow.appendChild(titleEl);

    let badge = null;
    if (item.verdict_label) {
      badge = document.createElement("span");
      badge.className = `badge badge-${item.verdict_tone || "gray"}`;
      badge.textContent = item.verdict_label;
      headerRow.appendChild(badge);
    }

    if (item.stale) {
      if (badge) {
        badge.classList.add("stale");
      }
      const staleBadge = document.createElement("span");
      staleBadge.className = "badge badge-stale";
      staleBadge.textContent = "可能过时";
      headerRow.appendChild(staleBadge);
    }

    li.appendChild(headerRow);

    if (item.judged_at) {
      const timeEl = document.createElement("div");
      timeEl.className = "job-time";
      timeEl.textContent = `判断时间：${formatJudgedAt(item.judged_at)}`;
      li.appendChild(timeEl);
    }

    itemsEl.appendChild(li);
  }
}

function renderTimings(timings) {
  const timingEl = document.getElementById("timing-footer");
  if (!timingEl) return;

  if (
    !timings ||
    typeof timings.wait_ms !== "number" ||
    typeof timings.read_ms !== "number" ||
    typeof timings.jet_ms !== "number"
  ) {
    timingEl.style.display = "none";
    timingEl.textContent = "";
    return;
  }

  timingEl.style.display = "block";
  timingEl.textContent = `最近一次：等页面稳定 ${timings.wait_ms} ms · 读取页面 ${timings.read_ms} ms · Jet 查询 ${timings.jet_ms} ms`;
}

function formatUpdateTime(timestamp) {
  if (!timestamp) return "";
  try {
    const d = new Date(timestamp);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    const hours = pad(d.getHours());
    const minutes = pad(d.getMinutes());
    const seconds = pad(d.getSeconds());
    return `${hours}:${minutes}:${seconds}`;
  } catch {
    return "";
  }
}

function renderUpdatedAt(updatedAt) {
  const el = document.getElementById("status-updated-at");
  if (!el) return;
  const timeStr = formatUpdateTime(updatedAt);
  if (!timeStr) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "block";
  el.textContent = `状态更新于 ${timeStr}`;
}

let pendingChatData = null;
let currentBoundJobId = null;
let currentChatResult = null;
let currentChatJobId = null;
let currentDisplayedCacheKey = null;
const chatResultsCache = new Map();
const latestCacheKeyByJobId = new Map();
const inFlightGenerations = new Set();
let quotaExhaustedDate = null;
let hasTriggeredOpenAutoGenerate = false;
let currentMaskState = createInitialMaskState();
let isGeneratingChat = false;
let pendingNewMessageCheck = false;
let maskOriginalButtonStates = null;
let maskOriginalCopyDisabledMap = new WeakMap();

function applyMaskUI(maskState) {
  const hrMaskEl = document.getElementById("hr-assistant-mask");
  const jobMaskEl = document.getElementById("current-job-mask");

  if (maskState && maskState.isMasked) {
    const text = maskState.maskText || CHAT_MASK_TEXTS.IDENTIFYING;
    if (hrMaskEl) {
      hrMaskEl.textContent = text;
      hrMaskEl.style.display = "flex";
    }
    if (jobMaskEl) {
      jobMaskEl.textContent = text;
      jobMaskEl.style.display = "flex";
    }

    const copyBtns = document.querySelectorAll(".btn-copy-suggestion, .btn-copy-question");
    const genBtn = document.getElementById("btn-generate-chat");
    const switchBtn = document.getElementById("btn-switch-mode");

    // 遮盖开始时（从未遮盖进入遮盖的那一刻）记录这些按钮各自原来的 disabled 状态 (E2 优化)
    // 重复收到 changing（已在遮盖中）不覆盖第一次记录的原状态
    if (!maskOriginalButtonStates) {
      const copyDisabledList = [];
      for (const btn of copyBtns) {
        maskOriginalCopyDisabledMap.set(btn, btn.disabled);
        copyDisabledList.push(btn.disabled);
      }
      maskOriginalButtonStates = recordMaskButtonStates(null, {
        generateDisabled: genBtn ? genBtn.disabled : false,
        switchModeDisabled: switchBtn ? switchBtn.disabled : false,
        copyDisabled: copyDisabledList.some(Boolean),
        copyDisabledList,
      });
    }

    // 遮盖期间禁用复制按钮与生成按钮 (FR-005, E2 优化)
    for (const btn of copyBtns) {
      btn.disabled = true;
    }
    if (genBtn) {
      genBtn.disabled = true;
    }
    if (switchBtn) {
      switchBtn.disabled = true;
    }
  } else {
    if (hrMaskEl) {
      hrMaskEl.style.display = "none";
    }
    if (jobMaskEl) {
      jobMaskEl.style.display = "none";
    }

    // 解除遮盖时调用纯函数判定各按钮应恢复的状态 (E2 优化)
    const unmaskedStates = decideUnmaskedButtonStates(
      maskOriginalButtonStates || {
        generateDisabled: false,
        switchModeDisabled: false,
        copyDisabled: false,
      },
      isGeneratingChat
    );

    const copyBtns = document.querySelectorAll(".btn-copy-suggestion, .btn-copy-question");
    copyBtns.forEach((btn, index) => {
      if (maskOriginalCopyDisabledMap.has(btn)) {
        btn.disabled = maskOriginalCopyDisabledMap.get(btn);
      } else if (
        Array.isArray(unmaskedStates.copyDisabledList) &&
        index < unmaskedStates.copyDisabledList.length
      ) {
        btn.disabled = unmaskedStates.copyDisabledList[index];
      } else {
        btn.disabled = unmaskedStates.copyDisabled;
      }
    });

    const genBtn = document.getElementById("btn-generate-chat");
    if (genBtn) {
      genBtn.disabled = unmaskedStates.generateDisabled;
      if (!unmaskedStates.generateDisabled && genBtn.textContent === "生成中..." && !isGeneratingChat) {
        genBtn.textContent = "生成沟通建议";
      }
    }
    const switchBtn = document.getElementById("btn-switch-mode");
    if (switchBtn) {
      switchBtn.disabled = unmaskedStates.switchModeDisabled;
    }

    // 解除遮盖后清空暂存的原始状态
    maskOriginalButtonStates = null;
    maskOriginalCopyDisabledMap = new WeakMap();
  }
}

async function handleChatTopChanging() {
  // 正在编辑 HR 实际情况时，先对原岗位 flush（复用 switchChatJobWithEditorFlush 的做法），再盖住 (E2 优化)
  await switchChatJobWithEditorFlush({
    activeEditor: activeChatJobEditor,
    newJobId: null,
    onSwitch: () => {
      activeChatJobEditor = null;
    },
  });

  currentMaskState = decideChatMaskOnChanging({
    ...currentMaskState,
    currentJobId: currentChatJobId,
    currentBoundJobId: currentBoundJobId,
    currentResult: currentChatResult,
    currentDisplayedCacheKey: currentDisplayedCacheKey,
  });

  const assistantCard = document.getElementById("hr-assistant-card");
  if (assistantCard) {
    assistantCard.style.display = "block";
  }

  applyMaskUI(currentMaskState);
}

function handleChatJobStatusUpdated(jobId, jobStatus) {
  const decision = decideChatJobStatusUpdate({
    currentChatJobId,
    updateJobId: jobId,
    jobStatus,
  });

  if (!decision.shouldApply) {
    // 过期的岗位更新被忽略 (FR-005, E2 优化)
    return;
  }

  const jobMaskEl = document.getElementById("current-job-mask");
  if (jobMaskEl && !currentMaskState.isMasked) {
    jobMaskEl.style.display = "none";
  }
  renderChatJob(decision.jobStatus);
}

function handleChatSwitchUnchanged(jobId) {
  currentMaskState = decideChatMaskOnUnchanged(currentMaskState);
  applyMaskUI(currentMaskState);

  // 误报恢复：解除遮盖、恢复原来的显示（不重新生成、不丢失原结果）(E2 优化)
  if (currentMaskState.shouldRestoreResult) {
    if (currentMaskState.currentResult) {
      currentChatResult = currentMaskState.currentResult;
      currentBoundJobId = currentMaskState.currentBoundJobId;
      currentChatJobId = currentMaskState.currentJobId;
      currentDisplayedCacheKey = currentMaskState.currentDisplayedCacheKey || null;
      const resultContainer = document.getElementById("chat-result-container");
      if (!resultContainer || resultContainer.children.length === 0) {
        renderChatResult(currentChatResult);
      }
    }
    if (currentChatJobStatus) {
      renderChatJob(currentChatJobStatus);
    }
  }
}

function handleChatSwitchFailed() {
  isGeneratingChat = false;
  currentMaskState = decideChatMaskOnFailed(currentMaskState);
  applyMaskUI(currentMaskState);

  // 若始终读不到岗位 ID，保持遮盖并显示"未能识别当前聊天的岗位，请稍后再试"，不得恢复旧聊天内容 (E2 优化)
  currentBoundJobId = null;
  currentChatResult = null;
  currentChatJobId = null;
  currentDisplayedCacheKey = null;
  currentChatJobStatus = null;
  currentHasHrResumeRequest = false;
  currentResumeRequestJobId = null;
  pendingChatData = null;
  pendingNewMessageCheck = false;

  const resultContainer = document.getElementById("chat-result-container");
  if (resultContainer) {
    resultContainer.style.display = "none";
    resultContainer.replaceChildren();
  }
  const attributionEl = document.getElementById("chat-attribution");
  if (attributionEl) {
    attributionEl.style.display = "none";
    attributionEl.textContent = "";
  }
  const emptyEl = document.getElementById("current-job-empty");
  const detailEl = document.getElementById("current-job-detail");
  if (emptyEl && detailEl) {
    emptyEl.style.display = "block";
    emptyEl.textContent = CHAT_MASK_TEXTS.FAILED;
    detailEl.style.display = "none";
    detailEl.replaceChildren();
  }
}

async function getAutoGenerateSetting() {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return true;
  }
  try {
    const res = await chrome.storage.local.get("autoGenerate");
    return resolveAutoGenerateSetting(res?.autoGenerate);
  } catch {
    return true;
  }
}

function showChatError(err, detail) {
  const msgEl = document.getElementById("chat-status-message");
  if (!msgEl) return;
  const text = formatChatError(err, detail);
  msgEl.className = "chat-status-msg error";
  msgEl.textContent = text;
  msgEl.style.display = "block";
}

function showChatInfo(text) {
  const msgEl = document.getElementById("chat-status-message");
  if (!msgEl) return;
  msgEl.className = "chat-status-msg info";
  msgEl.textContent = text;
  msgEl.style.display = "block";
}

function clearChatError() {
  const msgEl = document.getElementById("chat-status-message");
  if (!msgEl) return;
  msgEl.className = "chat-status-msg";
  msgEl.textContent = "";
  msgEl.style.display = "none";
}

function showConsentModal(previewData) {
  const modal = document.getElementById("chat-consent-modal");
  const previewBox = document.getElementById("consent-prompt-preview");
  const fieldsList = document.getElementById("consent-fields-list");
  const autoSendNotice = document.getElementById("consent-auto-send-notice");
  if (!modal) return;

  if (autoSendNotice) {
    autoSendNotice.textContent = CONSENT_MODAL_DESC;
  }

  if (previewBox) {
    previewBox.value = previewData?.sanitized_prompt || "";
  }

  if (fieldsList && Array.isArray(previewData?.fields) && previewData.fields.length > 0) {
    fieldsList.replaceChildren();
    for (const field of previewData.fields) {
      const li = document.createElement("li");
      li.textContent = field;
      fieldsList.appendChild(li);
    }
  }

  modal.style.display = "block";
}

function hideConsentModal() {
  const modal = document.getElementById("chat-consent-modal");
  if (modal) {
    modal.style.display = "none";
  }
}

function handleConsentCancel() {
  hideConsentModal();
  pendingChatData = null;
  const btn = document.getElementById("btn-generate-chat");
  if (btn) {
    btn.disabled = false;
    btn.textContent = "生成沟通建议";
  }
  const switchBtn = document.getElementById("btn-switch-mode");
  if (switchBtn) {
    switchBtn.disabled = false;
  }
  // "取消"不发请求 (T023 / FR-024)
  checkAndRunPendingNewMessage();
}

function handleConsentAgree() {
  const agreeBtn = document.getElementById("btn-consent-agree");
  const cancelBtn = document.getElementById("btn-consent-cancel");
  if (agreeBtn) agreeBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;

  chrome.runtime.sendMessage(
    { type: "chat_consent" },
    (consentRes) => {
      if (agreeBtn) agreeBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = false;

      hideConsentModal();

      if (!consentRes || !consentRes.ok) {
        showChatError(consentRes || "consent_failed");
        return;
      }

      // 同意后继续生成 (T023)
      if (pendingChatData) {
        const { chatData, previewData, forceMode, cacheKey } = pendingChatData;
        isGeneratingChat = true;
        const btn = document.getElementById("btn-generate-chat");
        if (btn) {
          btn.disabled = true;
          btn.textContent = "生成中...";
        }
        const switchBtn = document.getElementById("btn-switch-mode");
        if (switchBtn) {
          switchBtn.disabled = true;
        }
        executeGenerate(chatData, previewData.prompt_hash, forceMode || null, cacheKey);
      }
    }
  );
}

function executeGenerate(chatData, promptHash, forceMode = null, cacheKey = null) {
  isGeneratingChat = true;
  const effectiveCacheKey =
    cacheKey || computeChatCacheKey(chatData.encrypt_job_id, chatData.messages);
  inFlightGenerations.add(effectiveCacheKey);

  const jobId = chatData.encrypt_job_id;
  const btn = document.getElementById("btn-generate-chat");
  const switchBtn = document.getElementById("btn-switch-mode");
  clearChatError();

  const payload = {
    encrypt_job_id: chatData.encrypt_job_id,
    job_title: chatData.job_title,
    company_name: chatData.company_name,
    location_name: chatData.location_name,
    hr_name: chatData.hr_name,
    user_name: chatData.user_name,
    messages: chatData.messages,
    prompt_hash: promptHash,
    force_mode: forceMode,
  };

  chrome.runtime.sendMessage({ type: "chat_generate", payload }, (generateRes) => {
    isGeneratingChat = false;
    inFlightGenerations.delete(effectiveCacheKey);

    const isCurrent = currentChatJobId === jobId;
    if (isCurrent) {
      if (btn) {
        btn.textContent = "生成沟通建议";
        if (!currentMaskState?.isMasked) {
          btn.disabled = false;
        }
      }
      const currentSwitch = document.getElementById("btn-switch-mode");
      if (currentSwitch) {
        if (!currentMaskState?.isMasked) {
          currentSwitch.disabled = false;
        }
      }
    }

    if (!generateRes || !generateRes.ok || !generateRes.data) {
      if (
        generateRes?.error === "quota_exhausted" ||
        generateRes?.viewState === "quota_exhausted"
      ) {
        quotaExhaustedDate = getLocalDateString();
      }
      if (isCurrent) {
        const resultContainer = document.getElementById("chat-result-container");
        if (resultContainer) {
          resultContainer.style.display = "none";
          resultContainer.replaceChildren();
        }
        showChatError(generateRes || "generate_failed");
      }
      checkAndRunPendingNewMessage();
      return;
    }

    const resultData = {
      ...generateRes.data,
      bound_job_id: jobId,
      company_name: generateRes.data.company_name || chatData.company_name,
      job_title: generateRes.data.job_title || chatData.job_title,
    };

    // 内存缓存：写入或覆盖该键 (FR-066, FR-036)；同步维护岗位 -> 最新缓存键映射 (FR-074)
    chatResultsCache.set(effectiveCacheKey, resultData);
    setLatestChatCacheKey(latestCacheKeyByJobId, jobId, effectiveCacheKey);

    // 仅当与其岗位 ID 一致时渲染到当前界面 (FR-005, SC-007)
    if (isCurrent) {
      currentBoundJobId = jobId;
      currentChatResult = resultData;
      currentDisplayedCacheKey = effectiveCacheKey;
      renderChatResult(currentChatResult);
    }

    // 每次生成成功后立即按生成响应中的 quota_remaining 更新（已用 = 上限 - 剩余）
    if (typeof generateRes.data?.quota_remaining === "number") {
      updateAssistQuotaAfterGenerate(generateRes.data.quota_remaining);
    }

    // 刷新额度显示
    chrome.runtime.sendMessage({ type: "get_status" }, (statusRes) => {
      renderQuota(statusRes);
    });

    checkAndRunPendingNewMessage();
  });
}

async function handleCopyText(textToCopy, copyBtn) {
  if (!textToCopy) return;

  // 遮盖期间复制按钮不可用 (FR-005, E2 优化)
  if (currentMaskState?.copyDisabled || currentMaskState?.isMasked) {
    return;
  }

  if (!currentBoundJobId) {
    handleChatSwitched(null, { isCopyRejected: true });
    return;
  }

  const originalText = copyBtn ? copyBtn.textContent : "复制";
  if (copyBtn) {
    copyBtn.disabled = true;
  }

  try {
    const tabId = await getActiveTabId();
    chrome.runtime.sendMessage({ type: "read_chat_job_id", tabId }, async (res) => {
      if (chrome.runtime?.lastError || !res || !res.ok || !res.encrypt_job_id) {
        if (copyBtn) copyBtn.disabled = false;
        handleChatSwitched(null, { isCopyRejected: true });
        return;
      }

      const verifyResult = verifyCopyJobId(res.encrypt_job_id, currentBoundJobId);
      if (!verifyResult.ok) {
        if (copyBtn) copyBtn.disabled = false;
        handleChatSwitched(res.encrypt_job_id, { isCopyRejected: true });
        return;
      }

      // 验证一致才写入剪贴板 (FR-005, V8)
      let copySuccess = false;
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(textToCopy);
          copySuccess = true;
        }
      } catch {
        copySuccess = false;
      }

      if (!copySuccess) {
        try {
          const textarea = document.createElement("textarea");
          textarea.value = textToCopy;
          textarea.style.position = "fixed";
          textarea.style.opacity = "0";
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand("copy");
          document.body.removeChild(textarea);
          copySuccess = true;
        } catch {
          copySuccess = false;
        }
      }

      if (copyBtn) {
        copyBtn.disabled = false;
        if (copySuccess) {
          copyBtn.textContent = "已复制";
          setTimeout(() => {
            copyBtn.textContent = originalText;
          }, 1500);
        } else {
          showChatError("复制失败，请手动选择复制");
        }
      }
    });
  } catch {
    if (copyBtn) copyBtn.disabled = false;
    handleChatSwitched();
  }
}

function renderChatResult(data) {
  clearChatError();
  const resultContainer = document.getElementById("chat-result-container");
  const attributionEl = document.getElementById("chat-attribution");
  const unjudgedNoticeEl = document.getElementById("chat-no-judgement-notice");

  // FR-016: 这是给 [公司] · [职位] 的建议
  if (attributionEl) {
    attributionEl.textContent = formatAttribution(data.company_name, data.job_title);
    attributionEl.style.display = "block";
  }

  // FR-011: 这个岗位没有 Jet 判断 醒目标注
  if (unjudgedNoticeEl) {
    if (shouldShowNoJudgementNotice(data)) {
      unjudgedNoticeEl.textContent = NO_JET_JUDGEMENT_NOTICE;
      unjudgedNoticeEl.style.display = "block";
    } else {
      unjudgedNoticeEl.style.display = "none";
    }
  }

  if (!resultContainer) return;
  resultContainer.style.display = "block";
  resultContainer.replaceChildren();

  const notices = getDisplayNotices(data);

  // 0. request_notice: 不为空时在结果区最上方醒目显示 (FR-055)
  const reqNotice = notices.find((n) => n.type === "request");
  if (reqNotice) {
    const reqNoticeEl = document.createElement("div");
    reqNoticeEl.id = "chat-request-notice";
    reqNoticeEl.className = "chat-request-notice";
    reqNoticeEl.textContent = reqNotice.text;
    resultContainer.appendChild(reqNoticeEl);
  }

  // 1. 顶部判定类型与手动切换按钮 (FR-013, FR-012)
  const modeRow = document.createElement("div");
  modeRow.className = "chat-mode-row";

  const modeTitle = document.createElement("div");
  modeTitle.className = "chat-mode-title";
  modeTitle.textContent = formatModeDecision(data.mode);
  modeRow.appendChild(modeTitle);

  const nextMode = getNextForceMode(data.mode);
  const switchBtnText = getSwitchModeButtonText(data.mode);
  if (nextMode && switchBtnText) {
    const switchBtn = document.createElement("button");
    switchBtn.type = "button";
    switchBtn.className = "btn btn-secondary btn-sm";
    switchBtn.id = "btn-switch-mode";
    switchBtn.textContent = switchBtnText;
    switchBtn.addEventListener("click", () => {
      handleGenerateClick(nextMode);
    });
    modeRow.appendChild(switchBtn);
  }

  resultContainer.appendChild(modeRow);

  // 2. 依据的消息
  if (data.mode_basis) {
    const modeBasis = document.createElement("div");
    modeBasis.className = "chat-mode-basis";
    modeBasis.textContent = `依据：${data.mode_basis}`;
    resultContainer.appendChild(modeBasis);
  }

  // NEW_MESSAGE_HINT (FR-074)
  if (data.new_message_hint) {
    const hintEl = document.createElement("div");
    hintEl.id = "chat-new-message-hint";
    hintEl.className = "chat-new-message-hint chat-status-msg info";
    hintEl.style.display = "block";
    hintEl.style.marginTop = "6px";
    hintEl.textContent = data.new_message_hint;
    resultContainer.appendChild(hintEl);
  }

  // 3. experience_note（如"没有可引用的经历"）(FR-032)
  if (data.experience_note) {
    const expNoteEl = document.createElement("div");
    expNoteEl.className = "chat-experience-note";
    expNoteEl.textContent = data.experience_note;
    resultContainer.appendChild(expNoteEl);
  }

  // 4. unanswered_facts: 每一项显示其 notice 文字，放在话术上方 (FR-054)
  const factNotices = formatUnansweredFactNotices(data.unanswered_facts);
  if (factNotices.length > 0) {
    const factsContainer = document.createElement("div");
    factsContainer.id = "chat-unanswered-facts";
    factsContainer.className = "chat-unanswered-facts-container";
    for (const text of factNotices) {
      const factEl = document.createElement("div");
      factEl.className = "chat-unanswered-fact-notice";
      factEl.textContent = text;
      factsContainer.appendChild(factEl);
    }
    resultContainer.appendChild(factsContainer);
  }

  // 4.5. dropped_summary: 每一项显示其 text 文字 (FR-054)
  const droppedList = getDisplayDroppedSummary(data);
  if (droppedList.length > 0) {
    const droppedContainer = document.createElement("div");
    droppedContainer.id = "chat-dropped-summary";
    droppedContainer.className = "chat-dropped-summary-container";
    for (const item of droppedList) {
      const itemEl = document.createElement("div");
      itemEl.className = "chat-dropped-summary-item";
      itemEl.textContent = item.text;
      droppedContainer.appendChild(itemEl);
    }
    resultContainer.appendChild(droppedContainer);
  }

  // 5. 话术列表（waiting_hr 模式下为空，只显示问题）(FR-012, FR-014, FR-030)
  const displaySuggestions = getDisplaySuggestions(data);
  if (displaySuggestions.length > 0) {
    for (let i = 0; i < displaySuggestions.length; i++) {
      const item = displaySuggestions[i];
      const itemEl = document.createElement("div");
      itemEl.className = "chat-suggestion-item";

      const headerEl = document.createElement("div");
      headerEl.className = "chat-suggestion-header";

      const toneEl = document.createElement("span");
      toneEl.className = "chat-suggestion-tone";
      const versionNum = item.version ?? (i + 1);
      const toneText = item.tone_desc ? `（${item.tone_desc}）` : "";
      toneEl.textContent = `版本 ${versionNum}${toneText}`;
      headerEl.appendChild(toneEl);

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn btn-secondary btn-sm btn-copy-suggestion";
      copyBtn.textContent = "复制";
      copyBtn.addEventListener("click", () => {
        handleCopyText(getCopyableText(item), copyBtn);
      });
      headerEl.appendChild(copyBtn);

      itemEl.appendChild(headerEl);

      const textEl = document.createElement("div");
      textEl.className = "chat-suggestion-text";
      textEl.textContent = item.text;
      itemEl.appendChild(textEl);

      const sourceNote = formatSourceNote(item.referenced_experience_ids);
      const sourceEl = document.createElement("div");
      sourceEl.className = "chat-source-note";
      sourceEl.textContent = sourceNote;
      itemEl.appendChild(sourceEl);

      resultContainer.appendChild(itemEl);
    }
  }

  // 5. 最多 3 个问题（每个也可复制）(FR-014)
  if (Array.isArray(data.questions) && data.questions.length > 0) {
    const qSection = document.createElement("div");
    qSection.className = "chat-questions-section";

    const qTitle = document.createElement("div");
    qTitle.className = "chat-questions-title";
    qTitle.textContent = "建议问 HR 的问题：";
    qSection.appendChild(qTitle);

    const qList = document.createElement("div");
    qList.className = "chat-questions-list";

    const displayQuestions = data.questions.slice(0, 3);
    for (const q of displayQuestions) {
      const qItem = document.createElement("div");
      qItem.className = "chat-question-item";

      const qText = document.createElement("div");
      qText.className = "chat-question-text";
      qText.textContent = q;
      qItem.appendChild(qText);

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn btn-secondary btn-sm btn-copy-question";
      copyBtn.textContent = "复制";
      copyBtn.addEventListener("click", () => {
        handleCopyText(getCopyableText(q), copyBtn);
      });
      qItem.appendChild(copyBtn);

      qList.appendChild(qItem);
    }
    qSection.appendChild(qList);
    resultContainer.appendChild(qSection);
  }

  // 6. "根据已加载的 N 条消息生成" (FR-017)
  if (data.message_count !== undefined) {
    const countEl = document.createElement("div");
    countEl.className = "chat-msg-count-notice";
    countEl.textContent = formatLoadedMessagesNotice(data.message_count);
    resultContainer.appendChild(countEl);
  }
}

async function handleGenerateClick(forceMode = null) {
  const effectiveForceMode =
    typeof forceMode === "string" && (forceMode === "opening" || forceMode === "reply")
      ? forceMode
      : null;

  const btn = document.getElementById("btn-generate-chat");
  const switchBtn = document.getElementById("btn-switch-mode");
  if (isGeneratingChat) return;
  if (btn && btn.disabled) return;
  if (switchBtn && switchBtn.disabled) return;

  isGeneratingChat = true;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "生成中...";
  }
  if (switchBtn) {
    switchBtn.disabled = true;
  }
  clearChatError();

  const resetButtons = () => {
    isGeneratingChat = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "生成沟通建议";
    }
    const currentSwitch = document.getElementById("btn-switch-mode");
    if (currentSwitch) {
      currentSwitch.disabled = false;
    }
  };

  const tabId = await getActiveTabId();
  if (!tabId) {
    showChatError("read_chat_failed");
    resetButtons();
    return;
  }

  // 1. read_chat_page
  chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, (readRes) => {
    if (!readRes || !readRes.ok || !readRes.data) {
      showChatError(readRes?.error || "read_chat_failed");
      resetButtons();
      return;
    }

    const chatData = readRes.data;
    const jobId = chatData.encrypt_job_id;
    currentChatJobId = jobId;
    updateResumePromptWithChatData(chatData);
    const cacheKey = computeChatCacheKey(jobId, chatData.messages);

    // 2. chat_preview
    chrome.runtime.sendMessage(
      { type: "chat_preview", payload: buildChatPreviewPayload(chatData, effectiveForceMode) },
      (previewRes) => {
      if (!previewRes || !previewRes.ok) {
        if (
          previewRes?.error === "quota_exhausted" ||
          previewRes?.viewState === "quota_exhausted"
        ) {
          quotaExhaustedDate = getLocalDateString();
        }
        showChatError(previewRes || "preview_failed");
        resetButtons();
        return;
      }

      const previewData = previewRes.data;
      pendingChatData = {
        chatData,
        previewData,
        cacheKey,
        forceMode: effectiveForceMode,
      };

      // 3. 未同意则进入知情同意界面 (T023)
      if (!previewData.has_consent) {
        resetButtons();
        showConsentModal(previewData);
        return;
      }

      // 4. 已同意直接生成
      executeGenerate(chatData, previewData.prompt_hash, effectiveForceMode, cacheKey);
    });
  });
}

function checkAndRunPendingNewMessage() {
  if (pendingNewMessageCheck && !isGeneratingChat && inFlightGenerations.size === 0) {
    pendingNewMessageCheck = false;
    getActiveTabId().then((tabId) => {
      if (tabId && !currentMaskState?.isMasked) {
        handleChatMessagesChanged(tabId);
      }
    });
  }
}

function startAutoGenerateWithChatData(chatData, cacheKey) {
  const jobId = chatData.encrypt_job_id;
  if (inFlightGenerations.has(cacheKey) || isGeneratingChat) {
    return;
  }
  isGeneratingChat = true;
  inFlightGenerations.add(cacheKey);

  const btn = document.getElementById("btn-generate-chat");
  const switchBtn = document.getElementById("btn-switch-mode");
  if (currentChatJobId === jobId) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "生成中...";
    }
    if (switchBtn) {
      switchBtn.disabled = true;
    }
  }

  chrome.runtime.sendMessage(
    { type: "chat_preview", payload: buildChatPreviewPayload(chatData, null) },
    (previewRes) => {
    if (!previewRes || !previewRes.ok) {
      inFlightGenerations.delete(cacheKey);
      isGeneratingChat = false;
      if (
        previewRes?.error === "quota_exhausted" ||
        previewRes?.viewState === "quota_exhausted"
      ) {
        quotaExhaustedDate = getLocalDateString();
      }
      if (currentChatJobId === jobId) {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "生成沟通建议";
        }
        if (switchBtn) {
          switchBtn.disabled = false;
        }
        showChatError(previewRes || "preview_failed");
      }
      checkAndRunPendingNewMessage();
      return;
    }

    const previewData = previewRes.data;

    // 同意无效则显示同意页，等用户点"同意"，不自动发送 (FR-023, SC-001)
    if (!previewData.has_consent) {
      inFlightGenerations.delete(cacheKey);
      isGeneratingChat = false;
      if (currentChatJobId === jobId) {
        if (btn) {
          btn.disabled = false;
          btn.textContent = "生成沟通建议";
        }
        if (switchBtn) {
          switchBtn.disabled = false;
        }
        pendingChatData = {
          chatData,
          previewData,
          cacheKey,
          forceMode: null,
        };
        showConsentModal(previewData);
      }
      checkAndRunPendingNewMessage();
      return;
    }

    // 已同意则调用 chat_generate
    executeGenerate(chatData, previewData.prompt_hash, null, cacheKey);
  });
}

function updateStatusWithPreview({ chatData, cacheKey, baseResult = null, action = "update_status" }) {
  const jobId = chatData.encrypt_job_id;
  chrome.runtime.sendMessage(
    { type: "chat_preview", payload: buildChatPreviewPayload(chatData, null) },
    (previewRes) => {
    if (!previewRes || !previewRes.ok || !previewRes.data) {
      if (
        previewRes?.error === "quota_exhausted" ||
        previewRes?.viewState === "quota_exhausted"
      ) {
        quotaExhaustedDate = getLocalDateString();
      }
      // preview 失败（如 422 读不到姓名、服务未连接）时保持现有显示不变，不报错
      return;
    }

    const previewData = previewRes.data;
    const updatedResult = mergeChatPreviewResult(baseResult, previewData, chatData, { action });

    chatResultsCache.set(cacheKey, updatedResult);
    setLatestChatCacheKey(latestCacheKeyByJobId, jobId, cacheKey);

    if (currentChatJobId === jobId) {
      currentBoundJobId = jobId;
      currentChatResult = updatedResult;
      currentDisplayedCacheKey = cacheKey;
      renderChatResult(updatedResult);

      const btn = document.getElementById("btn-generate-chat");
      if (btn && !currentMaskState?.isMasked) {
        btn.disabled = false;
        btn.textContent = "生成沟通建议";
      }

      if (action === "quota_exhausted") {
        showChatError("quota_exhausted");
      }
    }
  });
}

async function handleChatMessagesChanged(tabId) {
  if (!tabId) return;

  // 1. 若正在生成，只记待复查标记并返回
  if (isGeneratingChat || inFlightGenerations.size > 0) {
    pendingNewMessageCheck = true;
    return;
  }

  // 2. read_chat_page
  chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, async (readRes) => {
    if (!readRes || !readRes.ok || !readRes.data) {
      return;
    }

    const chatData = readRes.data;
    const jobId = chatData.encrypt_job_id;
    // 若读到的 encrypt_job_id 不等于 currentChatJobId 则返回（交给切换流程）
    if (!jobId || jobId !== currentChatJobId) {
      return;
    }

    updateResumePromptWithChatData(chatData);

    // 计算新缓存键，若等于当前显示结果的缓存键则返回（最后一条没变）
    const newCacheKey = computeChatCacheKey(jobId, chatData.messages);
    if (newCacheKey === currentDisplayedCacheKey) {
      return;
    }

    const autoGenerate = await getAutoGenerateSetting();
    const lastIsHr = isLastMessageFromHr(chatData.messages);
    const quotaExhaustedToday = isQuotaExhaustedToday(quotaExhaustedDate);

    const action = decideNewMessageAction({
      autoGenerate,
      lastIsHr,
      quotaExhaustedToday,
    });

    if (action === "generate") {
      startAutoGenerateWithChatData(chatData, newCacheKey);
      return;
    }

    if (action === "quota_exhausted") {
      updateStatusWithPreview({
        chatData,
        cacheKey: newCacheKey,
        baseResult: currentChatResult,
        action: "quota_exhausted",
      });
      return;
    }

    if (action === "update_status") {
      updateStatusWithPreview({
        chatData,
        cacheKey: newCacheKey,
        baseResult: currentChatResult,
        action: "update_status",
      });
      return;
    }

    if (action === "update_status_with_hint") {
      updateStatusWithPreview({
        chatData,
        cacheKey: newCacheKey,
        baseResult: currentChatResult,
        action: "update_status_with_hint",
      });
      return;
    }
  });
}

function triggerAutoGenerateFlow(tabId, expectedJobId = null) {
  if (!tabId) return;

  chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, (readRes) => {
    if (!readRes || !readRes.ok || !readRes.data) {
      showChatError(readRes?.error || "read_chat_failed");
      return;
    }

    const chatData = readRes.data;
    const jobId = chatData.encrypt_job_id;
    currentChatJobId = jobId;
    updateResumePromptWithChatData(chatData);

    const cacheKey = computeChatCacheKey(jobId, chatData.messages);
    const cachedResult = chatResultsCache.get(cacheKey);
    const hasCachedResult = Boolean(cachedResult);
    const quotaExhaustedToday = isQuotaExhaustedToday(quotaExhaustedDate);

    // 1. 精确缓存命中 → 先显示缓存，再通过 preview 刷新判断状态
    if (hasCachedResult) {
      if (currentChatJobId === jobId) {
        currentBoundJobId = jobId;
        currentChatResult = cachedResult;
        currentDisplayedCacheKey = cacheKey;
        renderChatResult(cachedResult);
      }
      updateStatusWithPreview({
        chatData,
        cacheKey,
        baseResult: cachedResult,
        action: "update_status",
      });
      return;
    }

    // 2. 没有精确命中但该岗位有更早的结果 → 按 decideNewMessageAction 处理 (FR-074)
    const latestKey = getLatestChatCacheKey(latestCacheKeyByJobId, jobId);
    const earlierResult = latestKey ? chatResultsCache.get(latestKey) : null;

    if (earlierResult) {
      const action = decideNewMessageAction({
        autoGenerate: true,
        lastIsHr: isLastMessageFromHr(chatData.messages),
        quotaExhaustedToday,
      });

      if (action === "generate") {
        startAutoGenerateWithChatData(chatData, cacheKey);
        return;
      }

      if (action === "quota_exhausted") {
        updateStatusWithPreview({
          chatData,
          cacheKey,
          baseResult: earlierResult,
          action: "quota_exhausted",
        });
        return;
      }

      if (action === "update_status") {
        updateStatusWithPreview({
          chatData,
          cacheKey,
          baseResult: earlierResult,
          action: "update_status",
        });
        return;
      }

      if (action === "update_status_with_hint") {
        updateStatusWithPreview({
          chatData,
          cacheKey,
          baseResult: earlierResult,
          action: "update_status_with_hint",
        });
        return;
      }
    }

    // 3. 该岗位没有任何结果 → 照旧自动生成 (FR-065)
    if (quotaExhaustedToday) {
      if (currentChatJobId === jobId) {
        showChatError("quota_exhausted");
      }
      return;
    }

    startAutoGenerateWithChatData(chatData, cacheKey);
  });
}

async function handleChatSwitched(newJobId = null, options = {}) {
  // 编辑中切换聊天时：先 flush 当前内容到原岗位，再切换显示，不得把内容存到新岗位 (FR-058, T061)
  await switchChatJobWithEditorFlush({
    activeEditor: activeChatJobEditor,
    newJobId: newJobId,
    onSwitch: () => {
      activeChatJobEditor = null;
    },
  });

  // 确认新 ID 后解除遮盖 (FR-005, E2 优化)
  isGeneratingChat = false;
  currentMaskState = decideChatMaskOnSwitched(currentMaskState, { jobId: newJobId });
  applyMaskUI(currentMaskState);

  // 在岗位库状态返回之前，当前岗位卡片显示"正在查询岗位库…" (E2 优化)
  const emptyEl = document.getElementById("current-job-empty");
  const detailEl = document.getElementById("current-job-detail");
  if (emptyEl && detailEl) {
    emptyEl.style.display = "block";
    emptyEl.textContent = CHAT_MASK_TEXTS.QUERYING_JOB;
    detailEl.style.display = "none";
    detailEl.replaceChildren();
  }
  currentChatJobStatus = null;
  const resetPromptState = resetResumePromptOnSwitch({
    hasHrResumeRequest: currentHasHrResumeRequest,
    resumeRequestJobId: currentResumeRequestJobId,
  });
  currentHasHrResumeRequest = resetPromptState.hasHrResumeRequest;
  currentResumeRequestJobId = resetPromptState.resumeRequestJobId;

  const isCopyRejected = Boolean(options && (options.isCopyRejected || options.reason === "copy_rejected"));
  if (newJobId) {
    currentChatJobId = newJobId;
  }
  currentBoundJobId = null;
  pendingChatData = null;
  currentChatResult = null;
  currentDisplayedCacheKey = null;
  pendingNewMessageCheck = false;

  const resultContainer = document.getElementById("chat-result-container");
  if (resultContainer) {
    resultContainer.style.display = "none";
    resultContainer.replaceChildren();
  }
  const attributionEl = document.getElementById("chat-attribution");
  if (attributionEl) {
    attributionEl.style.display = "none";
    attributionEl.textContent = "";
  }
  const unjudgedNoticeEl = document.getElementById("chat-no-judgement-notice");
  if (unjudgedNoticeEl) {
    unjudgedNoticeEl.style.display = "none";
  }
  hideConsentModal();
  clearChatError();

  const tabId = await getActiveTabId();
  if (!tabId) return;

  chrome.runtime.sendMessage({ type: "is_chat_page", tabId }, async (chatRes) => {
    const isChat = Boolean(chatRes?.ok && chatRes?.is_chat_page);
    const assistantCard = document.getElementById("hr-assistant-card");
    if (assistantCard) {
      assistantCard.style.display = isChat ? "block" : "none";
    }
    if (!isChat) {
      return;
    }

    // 更新当前岗位区块 (T061 / 强制向本机 Jet 重新查询)
    if (options.jobStatus && isChatJobResponseCurrent(options.jobStatus.platform_job_id, currentChatJobId)) {
      renderChatJob(options.jobStatus);
    }
    if (currentChatJobId) {
      const targetJobId = currentChatJobId;
      chrome.runtime.sendMessage(
        { type: "get_chat_job_status", tabId, jobId: targetJobId, forceRefresh: true },
        (res) => {
          if (res?.ok && isChatJobResponseCurrent(res?.jobStatus?.platform_job_id, currentChatJobId)) {
            renderChatJob(res.jobStatus);
          }
        }
      );
    } else {
      chrome.runtime.sendMessage({ type: "get_chat_job_status", tabId, forceRefresh: true }, (res) => {
        if (res?.ok && res.jobStatus) {
          currentChatJobId = res.jobId;
          renderChatJob(res.jobStatus);
        } else {
          renderChatJob(null);
        }
      });
    }

    const autoGenerate = await getAutoGenerateSetting();
    if (isCopyRejected) {
      showChatInfo(COPY_REJECTED_NOTICE);
      if (autoGenerate) {
        triggerAutoGenerateFlow(tabId, newJobId);
      }
      return;
    }

    if (!autoGenerate) {
      const latestKey = currentChatJobId
        ? getLatestChatCacheKey(latestCacheKeyByJobId, currentChatJobId)
        : null;
      const hasAnyResult = Boolean(latestKey && chatResultsCache.has(latestKey));

      if (!hasAnyResult) {
        showChatInfo("点'生成'获取建议");
        refreshResumePromptForActiveTab(tabId);
        return;
      }

      // 该岗位有任何结果时才 read_chat_page (FR-074 第 4 条)
      chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, (readRes) => {
        if (!readRes || !readRes.ok || !readRes.data) {
          showChatInfo("点'生成'获取建议");
          return;
        }

        const chatData = readRes.data;
        updateResumePromptWithChatData(chatData);
        const jobId = chatData.encrypt_job_id;
        if (!jobId || jobId !== currentChatJobId) {
          return;
        }

        const cacheKey = computeChatCacheKey(jobId, chatData.messages);
        const exactResult = chatResultsCache.get(cacheKey);

        if (exactResult) {
          // 精确命中 → 先显示缓存，再通过 preview 刷新判断状态
          if (currentChatJobId === jobId) {
            currentBoundJobId = jobId;
            currentChatResult = exactResult;
            currentDisplayedCacheKey = cacheKey;
            renderChatResult(exactResult);
          }
          updateStatusWithPreview({
            chatData,
            cacheKey,
            baseResult: exactResult,
            action: "update_status",
          });
          return;
        }

        // 更早的结果 → 只更新判定类型与依据并显示 NEW_MESSAGE_HINT
        const earlierResult = chatResultsCache.get(latestKey);
        updateStatusWithPreview({
          chatData,
          cacheKey,
          baseResult: earlierResult,
          action: "update_status_with_hint",
        });
      });
      return;
    }

    triggerAutoGenerateFlow(tabId, newJobId);
  });
}

async function initAutoGenerateOnOpen() {
  if (hasTriggeredOpenAutoGenerate) return;
  hasTriggeredOpenAutoGenerate = true;
  await handleChatSwitched();
}

function refreshChatPreviewForActiveTab(tabId) {
  if (!tabId) return;
  const jobId = currentChatJobId || currentBoundJobId;
  const latestKey = jobId ? getLatestChatCacheKey(latestCacheKeyByJobId, jobId) : null;
  const existingResult = currentChatResult || (latestKey ? chatResultsCache.get(latestKey) : null);

  const decision = decideChatPreviewRefresh({
    isChat: true,
    currentChatJobId: jobId,
    existingResult,
    isGenerating: isGeneratingChat,
    isMasked: Boolean(currentMaskState?.isMasked),
  });

  if (!decision.shouldRefresh) {
    return;
  }

  chrome.runtime.sendMessage({ type: "read_chat_page", tabId }, (readRes) => {
    if (!readRes || !readRes.ok || !readRes.data) {
      return;
    }
    const chatData = readRes.data;
    updateResumePromptWithChatData(chatData);
    if (!chatData.encrypt_job_id || chatData.encrypt_job_id !== currentChatJobId) {
      return;
    }
    const cacheKey = computeChatCacheKey(chatData.encrypt_job_id, chatData.messages);
    const baseResult = chatResultsCache.get(cacheKey) || existingResult;
    updateStatusWithPreview({
      chatData,
      cacheKey,
      baseResult,
      action: "update_status",
    });
  });
}

async function refreshAll() {
  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
    return;
  }

  // 1. 获取额度与连接状态
  chrome.runtime.sendMessage({ type: "get_status" }, (statusRes) => {
    renderQuota(statusRes);
  });

  // 2. 取当前激活标签页并获取页面状态
  const tabId = await getActiveTabId();
  if (tabId) {
    // 检查是否为聊天页并控制 HR 沟通助手卡片显隐 (FR-006)
    chrome.runtime.sendMessage({ type: "is_chat_page", tabId }, (chatRes) => {
      const isChat = Boolean(chatRes?.ok && chatRes?.is_chat_page);
      const assistantCard = document.getElementById("hr-assistant-card");
      if (assistantCard) {
        assistantCard.style.display = isChat ? "block" : "none";
      }
      if (!isChat) {
        hideConsentModal();
        flushAndClearActiveChatJobEditor();
        currentHasHrResumeRequest = false;
        currentResumeRequestJobId = null;
      }

      // 遮盖状态保护：正处于遮盖状态（changing 或 failed）时，不让定时轮询覆盖遮盖层或旧岗位 (E2 优化)
      if (currentMaskState?.isMasked) {
        chrome.runtime.sendMessage({ type: "get_page_summary", tabId }, (res) => {
          if (res?.ok) {
            renderMarksNotice(res.marks_enabled);
            renderPageSummary(res.summary);
            renderTimings(res.timings);
            renderUpdatedAt(res.updated_at);
          }
        });
        return;
      }

      // 兜底检查：若当前是聊天页且界面上正显示某个岗位的结果（有 currentBoundJobId）(T074)
      runChatFallbackCheck({
        isChat,
        displayedJobId: currentBoundJobId,
        readCurrentJobId: () =>
          new Promise((resolve) => {
            chrome.runtime.sendMessage({ type: "read_chat_job_id", tabId }, (jobRes) => {
              const activeJobId =
                jobRes?.ok && jobRes?.encrypt_job_id ? jobRes.encrypt_job_id : null;
              resolve(activeJobId);
            });
          }),
        onSwitch: (newJobId) => {
          handleChatSwitched(newJobId);
        },
      });

      chrome.runtime.sendMessage({ type: "get_page_summary", tabId }, (res) => {
        if (res?.ok) {
          renderMarksNotice(res.marks_enabled);
          if (isChat) {
            // 聊天页：使用现有"当前岗位"卡片显示当前聊天的岗位 (T061)
            const cachedChatJob = pickCachedChatJobStatus(res.chat_job_status, currentChatJobId);
            if (cachedChatJob) {
              if (!activeChatJobEditor) {
                renderChatJob(cachedChatJob);
              }
            }
            if (currentChatJobId) {
              if (!activeChatJobEditor) {
                chrome.runtime.sendMessage(
                  { type: "get_chat_job_status", tabId, jobId: currentChatJobId, forceRefresh: true },
                  (cRes) => {
                    if (
                      cRes?.ok &&
                      isChatJobResponseCurrent(cRes?.jobStatus?.platform_job_id, currentChatJobId)
                    ) {
                      if (!activeChatJobEditor) {
                        renderChatJob(cRes.jobStatus);
                      }
                    }
                  }
                );
              }
            } else {
              chrome.runtime.sendMessage({ type: "get_chat_job_status", tabId, forceRefresh: true }, (cRes) => {
                if (cRes?.ok && cRes.jobStatus) {
                  currentChatJobId = cRes.jobId;
                  if (!activeChatJobEditor) {
                    renderChatJob(cRes.jobStatus);
                  }
                } else if (!activeChatJobEditor) {
                  renderChatJob(null);
                }
              });
            }

            // 重新请求 preview 更新 HR 助手判断状态 (Requirement 2)
            refreshChatPreviewForActiveTab(tabId);
            // 刷新当前聊天 HR 要简历提示 (006 US3, T018)
            refreshResumePromptForActiveTab(tabId);
          } else {
            // 非聊天页："当前岗位"卡片行为完全不变 (T061)
            renderCurrentJob(res.detail);
          }
          renderPageSummary(res.summary);
          renderTimings(res.timings);
          renderUpdatedAt(res.updated_at);
        } else {
          renderMarksNotice(true);
          if (isChat) {
            if (!activeChatJobEditor) {
              renderChatJob(null);
            }
          } else {
            renderCurrentJob(null);
          }
          renderPageSummary(null);
          renderTimings(null);
          renderUpdatedAt(null);
        }
      });
    });
  } else {
    const assistantCard = document.getElementById("hr-assistant-card");
    if (assistantCard) {
      assistantCard.style.display = "none";
    }
    hideConsentModal();
    flushAndClearActiveChatJobEditor();
    currentHasHrResumeRequest = false;
    currentResumeRequestJobId = null;
    renderMarksNotice(true);
    renderCurrentJob(null);
    renderPageSummary(null);
    renderTimings(null);
    renderUpdatedAt(null);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // 绑定打开岗位库按钮 (T054 / FR-062)
  const myJobsBtn = document.getElementById("btn-open-myjobs");
  if (myJobsBtn) {
    myJobsBtn.addEventListener("click", () => openMyJobsTab());
  }

  // 绑定生成与知情同意按钮事件
  const generateBtn = document.getElementById("btn-generate-chat");
  if (generateBtn) {
    generateBtn.addEventListener("click", () => handleGenerateClick(null));
  }

  const consentCancelBtn = document.getElementById("btn-consent-cancel");
  if (consentCancelBtn) {
    consentCancelBtn.addEventListener("click", handleConsentCancel);
  }

  const consentAgreeBtn = document.getElementById("btn-consent-agree");
  if (consentAgreeBtn) {
    consentAgreeBtn.addEventListener("click", handleConsentAgree);
  }

  refreshAll();
  initAutoGenerateOnOpen();

  // 每 5 秒轮询刷新一次（只读，不触发判断）
  setInterval(refreshAll, 5000);

  // 标签页切换时刷新
  if (typeof chrome !== "undefined" && chrome.tabs?.onActivated) {
    chrome.tabs.onActivated.addListener(() => {
      refreshAll();
    });
  }

  // 监听来自 background.js 的标签页状态更新推送与 content.js 的新消息通知
  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, sender) => {
      if (message?.type === "tab_state_changed") {
        getActiveTabId().then((activeTabId) => {
          if (activeTabId && activeTabId === message.tabId) {
            refreshAll();
          }
        });
      }
      if (message?.type === "chat_messages_changed") {
        getActiveTabId().then((activeTabId) => {
          const senderTabId = sender?.tab?.id ?? message?.tabId;
          if (!activeTabId || !senderTabId || senderTabId !== activeTabId) {
            return;
          }
          if (currentMaskState?.isMasked) {
            return;
          }
          chrome.runtime.sendMessage({ type: "is_chat_page", tabId: activeTabId }, (chatRes) => {
            if (!chatRes?.ok || !chatRes?.is_chat_page) {
              return;
            }
            if (currentMaskState?.isMasked) {
              return;
            }
            handleChatMessagesChanged(activeTabId);
          });
        });
      }
      if (message?.type === "chat_top_changing") {
        getActiveTabId().then((activeTabId) => {
          if (!message.tabId || (activeTabId && activeTabId === message.tabId)) {
            handleChatTopChanging();
          }
        });
      }
      if (message?.type === "chat_switched") {
        getActiveTabId().then((activeTabId) => {
          if (!message.tabId || (activeTabId && activeTabId === message.tabId)) {
            handleChatSwitched(message.jobId, { jobStatus: message.jobStatus });
          }
        });
      }
      if (message?.type === "chat_job_status_updated") {
        getActiveTabId().then((activeTabId) => {
          if (!message.tabId || (activeTabId && activeTabId === message.tabId)) {
            handleChatJobStatusUpdated(message.jobId, message.jobStatus);
          }
        });
      }
      if (message?.type === "chat_switch_unchanged") {
        getActiveTabId().then((activeTabId) => {
          if (!message.tabId || (activeTabId && activeTabId === message.tabId)) {
            handleChatSwitchUnchanged(message.jobId);
          }
        });
      }
      if (message?.type === "chat_switch_failed") {
        getActiveTabId().then((activeTabId) => {
          if (!message.tabId || (activeTabId && activeTabId === message.tabId)) {
            handleChatSwitchFailed();
          }
        });
      }
    });
  }

  // 设置项变化时刷新
  if (typeof chrome !== "undefined" && chrome.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.marksEnabled) {
        refreshAll();
      }
      if (changes.autoGenerate) {
        handleChatSwitched();
      }
    });
  }
});

export {
  renderCurrentJob,
  renderChatJob,
  handleChatSwitched,
  refreshAll,
  handleChatTopChanging,
  handleChatJobStatusUpdated,
  handleChatSwitchUnchanged,
  handleChatSwitchFailed,
  applyMaskUI,
  handleChatMessagesChanged,
  updateResumePromptWithChatData,
  refreshResumePromptForActiveTab,
  applyResumeHighlightToCurrentJob,
};
