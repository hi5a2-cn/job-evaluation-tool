export function formatLlmKeySource(source, configured) {
  if (!configured) {
    return "未填写";
  }
  if (source === "settings_page") {
    return "来自设置页";
  }
  if (source === "env") {
    return "来自 .env";
  }
  return "未填写";
}

export function formatLlmKeyStatusText(info) {
  if (!info || !info.configured || !info.masked) {
    return "未填写";
  }
  if (info.source === "env") {
    return `正在使用 .env 中的 Key：${info.masked}`;
  }
  return `已保存：${info.masked}`;
}

export function formatTestLlmKeyResult(res) {
  if (!res) {
    return { ok: false, message: "无法连接" };
  }
  if (res.ok) {
    return { ok: true, message: "连接成功" };
  }
  if (res.reason === "invalid_key") {
    return { ok: false, message: "Key 无效" };
  }
  if (res.reason === "no_key") {
    return { ok: false, message: "尚未填写 Key" };
  }
  if (res.reason === "unreachable") {
    return { ok: false, message: "无法连接" };
  }
  return { ok: false, message: res.message || "无法连接" };
}

export function canClearLlmKey(info) {
  return Boolean(info && info.configured && info.source === "settings_page");
}

export function parseAutoGenerateSetting(storageResult) {
  if (!storageResult || typeof storageResult !== "object") {
    return true;
  }
  return storageResult.autoGenerate !== false;
}

export function toAutoGenerateStoragePayload(checked) {
  return { autoGenerate: Boolean(checked) };
}

/**
 * 分配下一个最小空闲简历编号 (1–3) (FR-001, FR-004)
 * 从 1 到 3 中寻找尚未被占用的最小整数编号；若 1–3 均已被占用则返回 null。
 *
 * @param {Array<number|string>|Set<number|string>} usedSlots 已被占用的简历编号列表或集合
 * @returns {number|null} 最小可用编号（1、2 或 3），若已满 3 份则返回 null
 */
export function allocateNextResumeSlot(usedSlots) {
  const occupied = new Set();
  if (Array.isArray(usedSlots) || usedSlots instanceof Set) {
    for (const val of usedSlots) {
      const n = typeof val === "number" ? val : parseInt(val, 10);
      if (!isNaN(n)) {
        occupied.add(n);
      }
    }
  }
  for (let slot = 1; slot <= 3; slot++) {
    if (!occupied.has(slot)) {
      return slot;
    }
  }
  return null;
}


/**
 * 处理简历删除点击流程 (FR-014, 审查修正)
 * 先弹 confirm("确定删除简历 N？提取的文字和画像会一起删除，不能恢复。")；
 * 确认后发送 delete_resume 并等待结果；
 * 成功才移除这一项并提示"简历 N 已删除"；
 * 失败保留这一项并显示错误（沿用 formatResumeErrorMessage）；
 * 取消则什么都不做。
 *
 * @param {object} params
 * @param {number} params.slot 简历编号
 * @param {HTMLElement} [params.itemEl] 简历 DOM 元素
 * @param {HTMLElement} [params.delBtn] 删除按钮
 * @param {HTMLElement} [params.msgEl] 选项页通用消息提示元素
 * @param {Function} [params.updateSlots] 更新槽位状态回调
 * @param {Function} [params.confirmFn] confirm 实现（默认 window.confirm 或 globalThis.confirm）
 * @param {Function} [params.sendMessageFn] chrome.runtime.sendMessage 实现
 */
export function handleDeleteResume({
  slot,
  itemEl,
  delBtn,
  msgEl,
  updateSlots,
  confirmFn,
  sendMessageFn,
} = {}) {
  const defaultConfirm =
    (typeof window !== "undefined" && typeof window.confirm === "function")
      ? window.confirm.bind(window)
      : (typeof globalThis !== "undefined" && typeof globalThis.confirm === "function")
        ? globalThis.confirm.bind(globalThis)
        : (typeof confirm === "function" ? confirm : () => true);

  const confirmCall = confirmFn || defaultConfirm;
  const confirmed = confirmCall(`确定删除简历 ${slot}？提取的文字和画像会一起删除，不能恢复。`);
  if (!confirmed) {
    return;
  }

  if (delBtn) {
    delBtn.disabled = true;
  }
  if (msgEl) {
    msgEl.textContent = "";
    msgEl.className = "msg";
  }

  const defaultSendMessage =
    typeof chrome !== "undefined" && chrome.runtime?.sendMessage
      ? chrome.runtime.sendMessage.bind(chrome.runtime)
      : null;

  const sendCall = sendMessageFn || defaultSendMessage;
  if (!sendCall) {
    if (delBtn) delBtn.disabled = false;
    return;
  }

  sendCall({ type: "delete_resume", slot }, (res) => {
    if (delBtn) {
      delBtn.disabled = false;
    }
    if (!res) {
      if (msgEl) {
        msgEl.className = "msg error";
        msgEl.textContent = "Jet 未运行";
      }
      return;
    }

    if (res.ok) {
      if (itemEl && typeof itemEl.remove === "function") {
        itemEl.remove();
      }
      if (typeof updateSlots === "function") {
        updateSlots();
      }
      if (msgEl) {
        msgEl.className = "msg success";
        msgEl.textContent = `简历 ${slot} 已删除`;
      }
    } else {
      const errMsg = formatResumeErrorMessage(res.error, res.data?.message);
      if (msgEl) {
        msgEl.className = "msg error";
        msgEl.textContent = errMsg;
      }
    }
  });
}

export const MAX_PDF_FILE_SIZE = 5 * 1024 * 1024; // 5MB

/**
 * 截取文件名去掉 .pdf 后缀
 *
 * @param {string} filename
 * @returns {string}
 */
export function stripPdfExtension(filename) {
  if (!filename || typeof filename !== "string") return "";
  if (filename.toLowerCase().endsWith(".pdf")) {
    return filename.slice(0, -4);
  }
  return filename;
}

/**
 * 校验 PDF 文件大小（<= 5MB）
 *
 * @param {number} size 字节数
 * @returns {{ valid: boolean, error: string|null }}
 */
export function validatePdfFileSize(size) {
  if (typeof size !== "number" || isNaN(size) || size <= 0) {
    return { valid: false, error: "文件无效" };
  }
  if (size > MAX_PDF_FILE_SIZE) {
    return { valid: false, error: "文件大小不能超过 5MB" };
  }
  return { valid: true, error: null };
}

/**
 * 格式化画像字数统计
 *
 * @param {number} len 当前字数
 * @param {number} max 最大字数（默认 300）
 * @returns {{ text: string, isOverLimit: boolean }}
 */
export function formatProfileCharCount(len, max = 300) {
  const current = typeof len === "number" ? len : 0;
  return {
    text: `已输入 ${current} / ${max}`,
    isOverLimit: current > max,
  };
}

/**
 * 校验当前所在城市（<= 20 字）
 *
 * @param {string} city
 * @returns {{ valid: boolean, value?: string, error?: string }}
 */
export function validateCurrentCity(city) {
  const trimmed = typeof city === "string" ? city.trim() : "";
  if (trimmed.length > 20) {
    return { valid: false, error: "目前所在城市不能超过 20 字" };
  }
  return { valid: true, value: trimmed };
}

/**
 * 格式化简历操作错误提示
 *
 * @param {string} error 服务端错误码
 * @param {string} [message] 服务端给的中文说明
 * @returns {string}
 */
export function formatResumeErrorMessage(error, message) {
  if (!error) return "操作失败";
  if (error === "no_text") return "无法读取文字，请上传文字版 PDF";
  if (error === "self_name_required") return "无法从简历中识别姓名，请在上方填写姓名后再试";
  if (error === "file_too_large") return "文件大小不能超过 5MB";
  if (error === "pdf_invalid") return "PDF 损坏或加密，无法解析";
  if (error === "quota_exhausted") return "今日简历画像生成额度已用完（每天最多 10 份），明天 0 点恢复；简历文字已保存";
  if (error === "no_llm_key") return "未配置 API Key，简历文字已保存，配置 Key 后可点击重新生成画像";
  if (error === "too_many_resumes") return "简历最多 3 份";
  if (error === "resume_slot_invalid" || error === "slot_invalid") return "简历编号无效";
  if (error === "resume_invalid") return "简历名称或画像格式不符合要求";
  if (error === "llm_failed") return message || "画像生成失败，简历文字已保存，可以稍后点重新生成画像";
  // 没有对应中文的错误码：优先显示服务端给的说明，不显示英文错误码
  return message || error;
}

export function validateUploadSelfName(name) {
  if (!name || typeof name !== "string" || !name.trim()) {
    return { valid: false, error: "请填写你的姓名（只在本机用于去掉姓名，不发送）", value: "" };
  }
  return { valid: true, error: null, value: name.trim() };
}

/**
 * 删除旧版本存在浏览器里的姓名（chrome.storage.local 的 resumeSelfName）。
 * 姓名只随上传 / 重新生成请求发给本机 Jet 用来去掉姓名，插件和本机 Jet 都不保存。
 *
 * @param {object|null} [storage] chrome.storage.local 实现
 */
export function clearLegacyResumeSelfName(storage) {
  const store =
    storage !== undefined
      ? storage
      : typeof chrome !== "undefined"
        ? chrome.storage?.local || null
        : null;
  if (!store?.remove) return;
  try {
    store.remove("resumeSelfName");
  } catch {
    // 删不掉下次打开设置页再删
  }
}


/**
 * 根据简历列表加载结果决定是否允许保存、提示文字及消息类型 (FR-001, FR-004)
 * 成功加载前禁用保存按钮，避免空表单覆盖已有简历；失败时给出与设置页统一的错误提示。
 *
 * @param {any} res 后端 get_resumes 响应对象
 * @returns {{ ok: boolean, canSave: boolean, message: string, msgType: string }}
 */
export function decideResumeLoadState(res) {
  if (!res) {
    return { ok: false, canSave: false, message: "Jet 未运行", msgType: "error" };
  }

  if (res.viewState === "unpaired" || res.status === 401) {
    return { ok: false, canSave: false, message: "未配对", msgType: "error" };
  }

  if (res.viewState === "jet_down" || res.status === 0) {
    return { ok: false, canSave: false, message: "Jet 未运行", msgType: "error" };
  }

  if (!res.ok || !Array.isArray(res.data)) {
    return {
      ok: false,
      canSave: false,
      message: `加载失败（${res.error || "未知错误"}）`,
      msgType: "error",
    };
  }

  return { ok: true, canSave: true, message: "", msgType: "" };
}

/**
 * 根据从严行业列表加载结果决定是否允许保存、提示文字及消息类型 (FR-010)
 *
 * @param {any} res 后端 get_strict_industries 响应对象
 * @returns {{ ok: boolean, canSave: boolean, message: string, msgType: string }}
 */
export function decideStrictIndustriesLoadState(res) {
  if (!res) {
    return { ok: false, canSave: false, message: "Jet 未运行", msgType: "error" };
  }

  if (res.viewState === "unpaired" || res.status === 401) {
    return { ok: false, canSave: false, message: "未配对", msgType: "error" };
  }

  if (res.viewState === "jet_down" || res.status === 0) {
    return { ok: false, canSave: false, message: "Jet 未运行", msgType: "error" };
  }

  if (!res.ok || !res.data || !Array.isArray(res.data.available)) {
    return {
      ok: false,
      canSave: false,
      message: `加载失败（${res.error || "未知错误"}）`,
      msgType: "error",
    };
  }

  return { ok: true, canSave: true, message: "", msgType: "" };
}

/**
 * 构造从严行业保存载荷
 *
 * @param {Array<string>} selected 勾选的行业名称数组
 * @returns {{ selected: Array<string> }}
 */
export function buildStrictIndustriesSavePayload(selected) {
  if (!Array.isArray(selected)) return { selected: [] };
  const seen = new Set();
  const clean = [];
  for (const item of selected) {
    if (typeof item === "string") {
      const s = item.trim();
      if (s && !seen.has(s)) {
        seen.add(s);
        clean.push(s);
      }
    }
  }
  return { selected: clean };
}

/**
 * 校验列表预判每日上限值 (FR-002, FR-008)
 * 仅允许 0–200 范围内的整数（0 代表关闭）。
 *
 * @param {*} val 输入值（数字或数字字符串）
 * @returns {{ valid: boolean, value: number|null, error: string|null }}
 */
export function validatePrejudgeLimit(val) {
  if (val === null || val === undefined || val === "") {
    return { valid: false, value: null, error: "每日上限须为 0 至 200 之间的整数" };
  }
  const str = String(val).trim();
  if (str === "" || !/^\d+$/.test(str)) {
    return { valid: false, value: null, error: "每日上限须为 0 至 200 之间的整数" };
  }
  const num = Number(str);
  if (!Number.isInteger(num) || num < 0 || num > 200) {
    return { valid: false, value: null, error: "每日上限须为 0 至 200 之间的整数" };
  }
  return { valid: true, value: num, error: null };
}


/**
 * 格式化列表预判额度信息展示文本 (FR-008, FR-015)
 *
 * @param {number|*} used 今日已用页数
 * @param {number|*} remaining 今日剩余页数
 * @param {number|*} limit 每日上限页数
 * @returns {string}
 */
export function formatPrejudgeQuotaText(used, remaining, limit) {
  const u = typeof used === "number" ? used : (parseInt(used, 10) || 0);
  const r = typeof remaining === "number" ? remaining : (parseInt(remaining, 10) || 0);
  const l = typeof limit === "number" ? limit : (parseInt(limit, 10) || 0);
  if (l === 0) {
    return `今日已用：${u} 页 / 剩余：0 页（上限：0 页，已关闭）`;
  }
  return `今日已用：${u} 页 / 剩余：${r} 页（上限：${l} 页）`;
}

export function initOptions() {
  // 先于其他初始化执行，后面出错也不影响删除旧版本存在浏览器里的姓名
  clearLegacyResumeSelfName();

  const statusEl = document.getElementById("connection-status");
  const quotaRow = document.getElementById("quota-row");
  const quotaInfo = document.getElementById("quota-info");
  const labelsFooter = document.getElementById("labels-footer");
  const labelsInfo = document.getElementById("labels-info");
  const codeInput = document.getElementById("pair-code");
  const pairBtn = document.getElementById("pair-btn");
  const msgEl = document.getElementById("pair-msg");

  const llmKeyInput = document.getElementById("llm-key-input");
  const llmKeySaveBtn = document.getElementById("llm-key-save-btn");
  const llmKeyTestBtn = document.getElementById("llm-key-test-btn");
  const llmKeyClearBtn = document.getElementById("llm-key-clear-btn");
  const llmKeyStatusEl = document.getElementById("llm-key-status");
  const llmKeySourceEl = document.getElementById("llm-key-source");
  const llmKeyMsgEl = document.getElementById("llm-key-msg");

  const directionsInput = document.getElementById("profile-directions");
  const keywordsInput = document.getElementById("profile-keywords");
  const preferredCitiesInput = document.getElementById("profile-preferred-cities");
  const excludedCitiesInput = document.getElementById("profile-excluded-cities");
  const currentCityInput = document.getElementById("profile-current-city");
  const citiesInput = preferredCitiesInput;
  const excludeInput = document.getElementById("profile-exclude");
  const minKInput = document.getElementById("profile-min-k");
  const nonprefMinKInput = document.getElementById("profile-nonpref-min-k");
  const workPreferenceInput = document.getElementById("profile-work-preference");
  const workPreferenceCount = document.getElementById("profile-work-preference-count");
  const backgroundInput = document.getElementById("profile-background");
  const backgroundCount = document.getElementById("profile-background-count");
  const profileSaveBtn = document.getElementById("profile-save-btn");
  const profileMsgEl = document.getElementById("profile-msg");

  const resumeListEl = document.getElementById("resume-list");
  const resumeEmptyEl = document.getElementById("resume-empty");
  const resumeMinHintEl = document.getElementById("resume-min-hint");
  const resumeAddBtn = document.getElementById("resume-add-btn");
  const resumeSaveBtn = document.getElementById("resume-save-btn");
  if (resumeSaveBtn) {
    resumeSaveBtn.disabled = true;
  }
  const resumeMsgEl = document.getElementById("resume-msg");

  const strictIndustryListEl = document.getElementById("strict-industry-list");
  const strictIndustryEmptyEl = document.getElementById("strict-industry-empty");
  const strictIndustrySaveBtn = document.getElementById("strict-industry-save-btn");
  if (strictIndustrySaveBtn) {
    strictIndustrySaveBtn.disabled = true;
  }
  const strictIndustryMsgEl = document.getElementById("strict-industry-msg");

  const experienceListEl = document.getElementById("experience-list");
  const experienceEmptyEl = document.getElementById("experience-empty");
  const experienceAddBtn = document.getElementById("experience-add-btn");
  const experienceSaveBtn = document.getElementById("experience-save-btn");
  if (experienceSaveBtn) {
    experienceSaveBtn.disabled = true;
  }
  let experienceLoaded = false;
  const experienceMsgEl = document.getElementById("experience-msg");

  const consentStatusEl = document.getElementById("consent-status");
  const consentTimeRow = document.getElementById("consent-time-row");
  const consentTimeEl = document.getElementById("consent-time");
  const consentHintEl = document.getElementById("consent-hint");
  const consentRevokeBtn = document.getElementById("consent-revoke-btn");
  const consentMsgEl = document.getElementById("consent-msg");

  const prejudgeDailyLimitInput = document.getElementById("prejudge-daily-limit");
  const prejudgeQuotaInfoEl = document.getElementById("prejudge-quota-info");
  const prejudgeSaveBtn = document.getElementById("prejudge-save-btn");
  const prejudgeMsgEl = document.getElementById("prejudge-msg");

  function formatConsentTime(isoStr) {
    if (!isoStr) return "-";
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleString("zh-CN", { hour12: false });
    } catch {
      return isoStr;
    }
  }

  function updateCharCount(input, countEl) {
    if (input && countEl) {
      countEl.textContent = `已输入 ${input.value.length} / 500`;
    }
  }

  if (workPreferenceInput) {
    workPreferenceInput.addEventListener("input", () => {
      updateCharCount(workPreferenceInput, workPreferenceCount);
    });
  }

  if (backgroundInput) {
    backgroundInput.addEventListener("input", () => {
      updateCharCount(backgroundInput, backgroundCount);
    });
  }

  function parseItems(text) {
    if (!text) return [];
    return text
      .split(/[\n,，]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }

  function refreshStatus() {
    if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      statusEl.textContent = "环境不可用";
      return;
    }

    chrome.runtime.sendMessage({ type: "get_status" }, (res) => {
      if (!res) {
        statusEl.className = "status-badge jet_down";
        statusEl.textContent = "Jet 未运行";
        quotaRow.style.display = "none";
        if (labelsFooter) labelsFooter.style.display = "none";
        return;
      }

      if (res.state === "paired") {
        statusEl.className = "status-badge paired";
        statusEl.textContent = "已配对";
        if (res.status?.quota) {
          quotaRow.style.display = "block";
          quotaInfo.textContent = `${res.status.quota.used} / ${res.status.quota.limit}`;
        }
        if (
          res.status?.labels &&
          typeof res.status.labels.total === "number"
        ) {
          if (labelsFooter) labelsFooter.style.display = "block";
          if (labelsInfo) labelsInfo.textContent = `已标注 ${res.status.labels.total} 条（可选）`;
        } else if (labelsFooter) {
          labelsFooter.style.display = "none";
        }
      } else if (res.state === "unpaired") {
        statusEl.className = "status-badge unpaired";
        statusEl.textContent = "未配对";
        quotaRow.style.display = "none";
        if (labelsFooter) labelsFooter.style.display = "none";
      } else if (res.state === "failed") {
        statusEl.className = "status-badge jet_down";
        statusEl.textContent = `连接异常（${res.error || "未知错误"}）`;
        quotaRow.style.display = "none";
        if (labelsFooter) labelsFooter.style.display = "none";
      } else {
        statusEl.className = "status-badge jet_down";
        statusEl.textContent = "Jet 未运行";
        quotaRow.style.display = "none";
        if (labelsFooter) labelsFooter.style.display = "none";
      }
    });
  }

  let currentLlmKeyInfo = null;

  function renderLlmKeyInfo(info) {
    currentLlmKeyInfo = info || null;
    if (llmKeyStatusEl) {
      llmKeyStatusEl.textContent = formatLlmKeyStatusText(currentLlmKeyInfo);
    }
    if (llmKeySourceEl) {
      llmKeySourceEl.textContent = formatLlmKeySource(
        currentLlmKeyInfo?.source,
        currentLlmKeyInfo?.configured
      );
    }
    if (llmKeyClearBtn) {
      llmKeyClearBtn.disabled = !canClearLlmKey(currentLlmKeyInfo);
    }
  }

  function loadLlmKey(callback) {
    if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      if (typeof callback === "function") callback(null);
      return;
    }
    chrome.runtime.sendMessage({ type: "get_llm_key" }, (res) => {
      if (res && res.ok && res.data) {
        renderLlmKeyInfo(res.data);
      } else {
        renderLlmKeyInfo(null);
      }
      if (typeof callback === "function") callback(res);
    });
  }

  if (llmKeySaveBtn) {
    llmKeySaveBtn.addEventListener("click", () => {
      if (llmKeyMsgEl) {
        llmKeyMsgEl.textContent = "";
        llmKeyMsgEl.className = "msg";
        llmKeyMsgEl.style.display = "none";
      }
      const val = llmKeyInput ? llmKeyInput.value.trim() : "";
      if (!val) {
        if (llmKeyMsgEl) {
          llmKeyMsgEl.className = "msg error";
          llmKeyMsgEl.textContent = "请先输入 API Key";
          llmKeyMsgEl.style.display = "block";
        }
        return;
      }

      llmKeySaveBtn.disabled = true;
      chrome.runtime.sendMessage({ type: "put_llm_key", api_key: val }, (res) => {
        llmKeySaveBtn.disabled = false;
        if (!res) {
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg error";
            llmKeyMsgEl.textContent = "Jet 未运行";
            llmKeyMsgEl.style.display = "block";
          }
          return;
        }
        if (res.ok && res.data) {
          if (llmKeyInput) {
            llmKeyInput.value = "";
          }
          renderLlmKeyInfo(res.data);
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg success";
            llmKeyMsgEl.textContent = "已保存：" + (res.data.masked || "");
            llmKeyMsgEl.style.display = "block";
          }
          refreshStatus();
        } else {
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg error";
            if (res.viewState === "unpaired" || res.status === 401) {
              llmKeyMsgEl.textContent = "未配对";
            } else if (res.viewState === "jet_down" || res.status === 0) {
              llmKeyMsgEl.textContent = "Jet 未运行";
            } else if (res.status === 422 || res.error === "invalid_payload") {
              llmKeyMsgEl.textContent = "API Key 不能为空";
            } else {
              llmKeyMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
            }
            llmKeyMsgEl.style.display = "block";
          }
        }
      });
    });
  }

  if (llmKeyClearBtn) {
    llmKeyClearBtn.addEventListener("click", () => {
      if (llmKeyMsgEl) {
        llmKeyMsgEl.textContent = "";
        llmKeyMsgEl.className = "msg";
        llmKeyMsgEl.style.display = "none";
      }
      const confirmed =
        typeof window !== "undefined" && typeof window.confirm === "function"
          ? window.confirm("确定要清除设置页保存的 DeepSeek API Key 吗？")
          : true;
      if (!confirmed) {
        return;
      }

      llmKeyClearBtn.disabled = true;
      chrome.runtime.sendMessage({ type: "delete_llm_key" }, (res) => {
        llmKeyClearBtn.disabled = false;
        if (!res) {
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg error";
            llmKeyMsgEl.textContent = "Jet 未运行";
            llmKeyMsgEl.style.display = "block";
          }
          return;
        }
        if (res.ok && res.data) {
          renderLlmKeyInfo(res.data);
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg success";
            if (res.data.configured && res.data.source === "env") {
              llmKeyMsgEl.textContent = `已清除设置页 Key，已回退到 .env（${res.data.masked || ""}）`;
            } else {
              llmKeyMsgEl.textContent = "已清除";
            }
            llmKeyMsgEl.style.display = "block";
          }
          refreshStatus();
        } else {
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = "msg error";
            if (res.viewState === "unpaired" || res.status === 401) {
              llmKeyMsgEl.textContent = "未配对";
            } else if (res.viewState === "jet_down" || res.status === 0) {
              llmKeyMsgEl.textContent = "Jet 未运行";
            } else {
              llmKeyMsgEl.textContent = `清除失败（${res.error || "未知错误"}）`;
            }
            llmKeyMsgEl.style.display = "block";
          }
        }
      });
    });
  }

  if (llmKeyTestBtn) {
    llmKeyTestBtn.addEventListener("click", () => {
      if (llmKeyMsgEl) {
        llmKeyMsgEl.textContent = "";
        llmKeyMsgEl.className = "msg";
        llmKeyMsgEl.style.display = "none";
      }

      const inputVal = llmKeyInput ? llmKeyInput.value.trim() : "";
      if (!inputVal && (!currentLlmKeyInfo || !currentLlmKeyInfo.configured)) {
        if (llmKeyMsgEl) {
          llmKeyMsgEl.className = "msg error";
          llmKeyMsgEl.textContent = "尚未填写 Key";
          llmKeyMsgEl.style.display = "block";
        }
        return;
      }

      llmKeyTestBtn.disabled = true;
      const prevBtnText = llmKeyTestBtn.textContent;
      llmKeyTestBtn.textContent = "正在测试...";

      chrome.runtime.sendMessage(
        {
          type: "test_llm_key",
          api_key: inputVal ? inputVal : null,
        },
        (res) => {
          llmKeyTestBtn.disabled = false;
          llmKeyTestBtn.textContent = prevBtnText;

          if (!res) {
            if (llmKeyMsgEl) {
              llmKeyMsgEl.className = "msg error";
              llmKeyMsgEl.textContent = "无法连接";
              llmKeyMsgEl.style.display = "block";
            }
            return;
          }

          if (res.viewState === "unpaired" || res.status === 401) {
            if (llmKeyMsgEl) {
              llmKeyMsgEl.className = "msg error";
              llmKeyMsgEl.textContent = "未配对";
              llmKeyMsgEl.style.display = "block";
            }
            return;
          }

          if (res.viewState === "jet_down" || res.status === 0) {
            if (llmKeyMsgEl) {
              llmKeyMsgEl.className = "msg error";
              llmKeyMsgEl.textContent = "无法连接";
              llmKeyMsgEl.style.display = "block";
            }
            return;
          }

          const formatted = formatTestLlmKeyResult(res.data);
          if (llmKeyMsgEl) {
            llmKeyMsgEl.className = formatted.ok ? "msg success" : "msg error";
            llmKeyMsgEl.textContent = formatted.message;
            llmKeyMsgEl.style.display = "block";
          }
        }
      );
    });
  }

  function loadProfile() {
    if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      return;
    }

    chrome.runtime.sendMessage({ type: "get_profile" }, (res) => {
      if (!res) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "Jet 未运行";
        return;
      }

      if (res.viewState === "no_profile" || res.status === 404) {
        profileMsgEl.className = "msg info";
        profileMsgEl.textContent = "请先设置画像";
        return;
      }

      if (res.viewState === "unpaired" || res.status === 401) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "未配对";
        return;
      }

      if (res.viewState === "jet_down" || res.status === 0) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "Jet 未运行";
        return;
      }

      if (res.ok && res.data) {
        profileMsgEl.textContent = "";
        directionsInput.value = (res.data.directions || []).join("\n");
        keywordsInput.value = (res.data.keywords || []).join("\n");

        const prefCities =
          res.data.preferred_cities != null
            ? res.data.preferred_cities
            : (res.data.cities || []);
        if (preferredCitiesInput) {
          preferredCitiesInput.value = prefCities.join("\n");
        }
        if (excludedCitiesInput) {
          excludedCitiesInput.value = (res.data.excluded_cities || []).join("\n");
        }
        if (currentCityInput) {
          currentCityInput.value = res.data.current_city || "";
        }

        excludeInput.value = (res.data.exclude_keywords || []).join("\n");
        minKInput.value = res.data.min_monthly_k != null ? res.data.min_monthly_k : "";
        if (nonprefMinKInput) {
          nonprefMinKInput.value =
            res.data.nonpref_min_monthly_k != null ? res.data.nonpref_min_monthly_k : "";
        }
        if (workPreferenceInput) {
          workPreferenceInput.value = res.data.work_preference || "";
          updateCharCount(workPreferenceInput, workPreferenceCount);
        }
        if (backgroundInput) {
          backgroundInput.value = res.data.background || "";
          updateCharCount(backgroundInput, backgroundCount);
        }
      }
    });
  }

  pairBtn.addEventListener("click", () => {
    const code = codeInput.value.trim();
    msgEl.textContent = "";
    msgEl.className = "msg";

    if (!code || code.length !== 6) {
      msgEl.className = "msg error";
      msgEl.textContent = "请输入 6 位数字配对码";
      return;
    }

    chrome.runtime.sendMessage({ type: "pair", code }, (res) => {
      if (res?.ok) {
        msgEl.className = "msg success";
        msgEl.textContent = "配对成功！";
        codeInput.value = "";
        refreshStatus();
        loadLlmKey();
        loadProfile();
        loadResumes();
        loadExperience();
        loadConsentStatus();
        loadPrejudgeSettings();
        loadStrictIndustries();
      } else {
        msgEl.className = "msg error";
        if (res?.error === "bad_code") {
          msgEl.textContent = "配对码错误或已过期";
        } else if (res?.error === "too_many_attempts") {
          msgEl.textContent = "尝试次数过多，请重新运行 jet pair";
        } else {
          msgEl.textContent = "配对失败，请重试";
        }
        refreshStatus();
      }
    });
  });

  profileSaveBtn.addEventListener("click", () => {
    profileMsgEl.textContent = "";
    profileMsgEl.className = "msg";

    const directions = parseItems(directionsInput.value);
    const keywords = parseItems(keywordsInput.value);
    const preferred_cities = preferredCitiesInput ? parseItems(preferredCitiesInput.value) : [];
    const excluded_cities = excludedCitiesInput ? parseItems(excludedCitiesInput.value) : [];
    const exclude_keywords = parseItems(excludeInput.value);

    const minKVal = minKInput ? minKInput.value.trim() : "";
    let min_monthly_k = null;
    if (minKVal !== "") {
      const num = Number(minKVal);
      if (Number.isNaN(num) || num < 0) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "数值不能为负";
        return;
      }
      min_monthly_k = num;
    }

    const nonprefMinKVal = nonprefMinKInput ? nonprefMinKInput.value.trim() : "";
    let nonpref_min_monthly_k = null;
    if (nonprefMinKVal !== "") {
      const num = Number(nonprefMinKVal);
      if (Number.isNaN(num) || num < 0) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "数值不能为负";
        return;
      }
      nonpref_min_monthly_k = num;
    }

    // 前端先校验必填
    if (directions.length === 0) {
      profileMsgEl.className = "msg error";
      profileMsgEl.textContent = "方向至少填一项";
      return;
    }

    const work_preference = workPreferenceInput ? workPreferenceInput.value.trim() : "";
    const background = backgroundInput ? backgroundInput.value.trim() : "";

    if (work_preference.length > 500 || background.length > 500) {
      profileMsgEl.className = "msg error";
      profileMsgEl.textContent = "工作内容偏好和我的背景各不能超过 500 字";
      return;
    }

    const cityValidation = validateCurrentCity(currentCityInput ? currentCityInput.value : "");
    if (!cityValidation.valid) {
      profileMsgEl.className = "msg error";
      profileMsgEl.textContent = cityValidation.error;
      return;
    }
    const current_city = cityValidation.value;

    const payload = {
      directions,
      keywords,
      preferred_cities,
      excluded_cities,
      exclude_keywords,
      min_monthly_k,
      nonpref_min_monthly_k,
      work_preference,
      background,
      current_city,
    };

    chrome.runtime.sendMessage({ type: "save_profile", profile: payload }, (res) => {
      if (!res) {
        profileMsgEl.className = "msg error";
        profileMsgEl.textContent = "Jet 未运行";
        return;
      }

      if (res.ok) {
        profileMsgEl.className = "msg success";
        if (res.data?.changed === false) {
          profileMsgEl.textContent = "内容未变化";
        } else {
          profileMsgEl.textContent = `已保存（版本 ${res.data?.version_no}）`;
        }
        refreshStatus();
      } else {
        profileMsgEl.className = "msg error";
        if (res.viewState === "unpaired" || res.status === 401) {
          profileMsgEl.textContent = "未配对";
        } else if (res.viewState === "jet_down" || res.status === 0) {
          profileMsgEl.textContent = "Jet 未运行";
        } else if (res.status === 422 || res.error === "invalid_payload") {
          profileMsgEl.textContent = "方向至少填一项，数值不能为负";
        } else {
          profileMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
        }
      }
    });
  });

  const marksCheckbox = document.getElementById("marks-enabled");

  function loadMarksSetting() {
    if (!marksCheckbox || typeof chrome === "undefined" || !chrome.storage?.local) {
      return;
    }
    chrome.storage.local.get("marksEnabled", (result) => {
      marksCheckbox.checked = result?.marksEnabled !== false;
    });
  }

  if (marksCheckbox) {
    marksCheckbox.addEventListener("change", () => {
      if (typeof chrome !== "undefined" && chrome.storage?.local) {
        chrome.storage.local.set({ marksEnabled: marksCheckbox.checked });
      }
    });
  }

  const autoGenerateCheckbox = document.getElementById("auto-generate");

  function loadAutoGenerateSetting() {
    if (!autoGenerateCheckbox) {
      return;
    }
    if (typeof chrome === "undefined" || !chrome.storage?.local) {
      autoGenerateCheckbox.checked = true;
      return;
    }
    try {
      chrome.storage.local.get("autoGenerate", (result) => {
        if (chrome.runtime?.lastError) {
          autoGenerateCheckbox.checked = true;
          return;
        }
        autoGenerateCheckbox.checked = parseAutoGenerateSetting(result);
      });
    } catch {
      autoGenerateCheckbox.checked = true;
    }
  }

  if (autoGenerateCheckbox) {
    autoGenerateCheckbox.addEventListener("change", () => {
      if (typeof chrome !== "undefined" && chrome.storage?.local) {
        try {
          chrome.storage.local.set(toAutoGenerateStoragePayload(autoGenerateCheckbox.checked));
        } catch {
          // 读写容错
        }
      }
    });
  }

  // --- 我的简历 ---
  function updateResumeSlots() {
    if (!resumeListEl) return;
    const items = resumeListEl.querySelectorAll(".resume-item");
    const count = items.length;
    if (resumeAddBtn) {
      resumeAddBtn.disabled = count >= 3;
      if (count >= 3) {
        resumeAddBtn.title = "最多录入 3 份简历";
      } else {
        resumeAddBtn.removeAttribute("title");
      }
    }
    if (resumeEmptyEl) {
      resumeEmptyEl.style.display = count === 0 ? "block" : "none";
    }
    if (resumeMinHintEl) {
      resumeMinHintEl.style.display = count === 1 ? "block" : "none";
    }
  }

  function createResumeItemElement(slot = 1, name = "", profile = "", hasText = false) {
    const itemEl = document.createElement("div");
    itemEl.className = "resume-item";
    itemEl.dataset.slot = String(slot);

    // 1. Header
    const headerEl = document.createElement("div");
    headerEl.className = "resume-header";

    const titleEl = document.createElement("strong");
    titleEl.className = "slot-number";
    titleEl.textContent = `简历 ${slot}`;

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-danger resume-delete-btn";
    delBtn.textContent = "删除";
    delBtn.style.padding = "4px 8px";
    delBtn.style.fontSize = "12px";

    headerEl.appendChild(titleEl);
    headerEl.appendChild(delBtn);

    // 2. 文件选择区
    const fileGroup = document.createElement("div");
    fileGroup.className = "form-group";
    fileGroup.style.marginBottom = "8px";

    const fileLabel = document.createElement("label");
    fileLabel.style.fontSize = "12px";
    fileLabel.textContent = "选择简历 PDF：";

    const fileRow = document.createElement("div");
    fileRow.style.display = "flex";
    fileRow.style.gap = "8px";
    fileRow.style.alignItems = "center";

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "application/pdf";
    fileInput.className = "resume-file-input";
    fileInput.style.fontSize = "12px";

    const uploadBtn = document.createElement("button");
    uploadBtn.type = "button";
    uploadBtn.className = "resume-upload-btn btn-secondary";
    uploadBtn.style.fontSize = "12px";
    uploadBtn.style.whiteSpace = "nowrap";
    uploadBtn.textContent = "上传并生成画像";
    uploadBtn.disabled = true;

    fileRow.appendChild(fileInput);
    fileRow.appendChild(uploadBtn);

    const fileTip = document.createElement("div");
    fileTip.className = "resume-file-tip";
    fileTip.style.fontSize = "11px";
    fileTip.style.color = "#888";
    fileTip.style.marginTop = "2px";
    fileTip.textContent = "仅支持文字版 PDF，大小 ≤ 5MB；原文件仅在内存处理不存盘";

    fileGroup.appendChild(fileLabel);
    fileGroup.appendChild(fileRow);
    fileGroup.appendChild(fileTip);

    // 3. 简历名称
    const nameGroup = document.createElement("div");
    nameGroup.className = "form-group";
    nameGroup.style.marginBottom = "8px";

    const nameLabel = document.createElement("label");
    nameLabel.textContent = "简历名称（用于在插件中区分，不会发给模型）：";
    nameLabel.style.fontSize = "12px";

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.maxLength = 100;
    nameInput.placeholder = "选择 PDF 后自动生成，例如：后端开发-通用版";
    nameInput.value = name;
    nameInput.className = "resume-name-input";

    nameGroup.appendChild(nameLabel);
    nameGroup.appendChild(nameInput);

    // 4. 简历画像
    const profileGroup = document.createElement("div");
    profileGroup.className = "form-group";
    profileGroup.style.marginBottom = "4px";

    const profileLabel = document.createElement("label");
    profileLabel.textContent = "简历画像（适合方向与亮点，会发给模型，最多 300 字）：";
    profileLabel.style.fontSize = "12px";

    const profileTextarea = document.createElement("textarea");
    profileTextarea.rows = 4;
    profileTextarea.maxLength = 300;
    profileTextarea.placeholder = "上传 PDF 后由 AI 自动生成，也可以手动输入或修改...";
    profileTextarea.value = profile;
    profileTextarea.className = "resume-profile-input";

    const countEl = document.createElement("div");
    countEl.className = "char-count resume-profile-count";

    function updateCount() {
      const len = profileTextarea.value.length;
      const countInfo = formatProfileCharCount(len, 300);
      countEl.textContent = countInfo.text;
      if (countInfo.isOverLimit) {
        countEl.classList.add("over-limit");
      } else {
        countEl.classList.remove("over-limit");
      }
    }
    updateCount();
    profileTextarea.addEventListener("input", updateCount);

    profileGroup.appendChild(profileLabel);
    profileGroup.appendChild(profileTextarea);
    profileGroup.appendChild(countEl);

    // 5. 重新生成按钮
    const actionsRow = document.createElement("div");
    actionsRow.className = "resume-actions-row";
    actionsRow.style.marginTop = "4px";
    actionsRow.style.display = "flex";
    actionsRow.style.gap = "8px";

    const regenBtn = document.createElement("button");
    regenBtn.type = "button";
    regenBtn.className = "resume-regen-btn btn-link";
    regenBtn.style.fontSize = "12px";
    regenBtn.textContent = "重新生成画像";
    regenBtn.style.display = hasText ? "inline-block" : "none";

    actionsRow.appendChild(regenBtn);

    // 交互逻辑绑定：
    // A. 选择文件
    fileInput.addEventListener("change", () => {
      if (resumeMsgEl) {
        resumeMsgEl.textContent = "";
        resumeMsgEl.className = "msg";
      }
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        uploadBtn.disabled = true;
        return;
      }
      const sizeValidation = validatePdfFileSize(file.size);
      if (!sizeValidation.valid) {
        fileInput.value = "";
        uploadBtn.disabled = true;
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = sizeValidation.error || "文件大小不能超过 5MB";
        }
        return;
      }
      const stripped = stripPdfExtension(file.name);
      if (stripped) {
        nameInput.value = stripped;
      }
      uploadBtn.disabled = false;
    });

    // B. 上传并生成画像
    uploadBtn.addEventListener("click", () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;

      const selfNameInput = document.getElementById("resume-self-name");
      const selfNameRaw = selfNameInput ? selfNameInput.value : "";
      const nameValidation = validateUploadSelfName(selfNameRaw);
      if (!nameValidation.valid) {
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = nameValidation.error;
        }
        if (selfNameInput) {
          selfNameInput.focus();
        }
        return;
      }
      const selfName = nameValidation.value;

      const sizeValidation = validatePdfFileSize(file.size);
      if (!sizeValidation.valid) {
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = sizeValidation.error || "文件大小不能超过 5MB";
        }
        return;
      }

      uploadBtn.disabled = true;
      uploadBtn.textContent = "正在解析与生成...";
      if (resumeMsgEl) {
        resumeMsgEl.textContent = "";
        resumeMsgEl.className = "msg";
      }

      const reader = new FileReader();
      reader.onload = () => {
        try {
          const resultStr = String(reader.result || "");
          const base64Part = resultStr.includes(",") ? resultStr.split(",")[1] : resultStr;
          const nameVal = nameInput.value.trim() || stripPdfExtension(file.name) || `简历${slot}`;

          chrome.runtime.sendMessage(
            {
              type: "upload_resume",
              slot,
              name: nameVal,
              pdf_base64: base64Part,
              self_name: selfName,
            },
            (res) => {
              uploadBtn.disabled = false;
              uploadBtn.textContent = "上传并生成画像";

              if (!res) {
                if (resumeMsgEl) {
                  resumeMsgEl.className = "msg error";
                  resumeMsgEl.textContent = "Jet 未运行";
                }
                return;
              }

              if (res.ok) {
                profileTextarea.value = res.data?.profile || "";
                updateCount();
                regenBtn.style.display = "inline-block";
                if (resumeMsgEl) {
                  resumeMsgEl.className = "msg success";
                  resumeMsgEl.textContent = `简历 ${slot} 上传成功，已生成画像`;
                }
              } else {
                // 失败时服务端返回这个槽位现在的画像（换了简历时为空），框里不再留着旧画像
                if (typeof res.data?.profile === "string") {
                  profileTextarea.value = res.data.profile;
                  updateCount();
                }
                if (res.data?.has_text) {
                  regenBtn.style.display = "inline-block";
                }
                const errMsg = formatResumeErrorMessage(res.error, res.data?.message);
                if (resumeMsgEl) {
                  resumeMsgEl.className = "msg error";
                  resumeMsgEl.textContent = errMsg;
                }
              }
            }
          );
        } catch (err) {
          uploadBtn.disabled = false;
          uploadBtn.textContent = "上传并生成画像";
          if (resumeMsgEl) {
            resumeMsgEl.className = "msg error";
            resumeMsgEl.textContent = err?.message || String(err);
          }
        }
      };
      reader.onerror = () => {
        uploadBtn.disabled = false;
        uploadBtn.textContent = "上传并生成画像";
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = "读取文件失败";
        }
      };
      reader.readAsDataURL(file);
    });

    // C. 重新生成画像
    regenBtn.addEventListener("click", () => {
      regenBtn.disabled = true;
      regenBtn.textContent = "正在重新生成...";
      if (resumeMsgEl) {
        resumeMsgEl.textContent = "";
        resumeMsgEl.className = "msg";
      }

      // 姓名不保存，重新生成也要先填（本机 Jet 去掉姓名时需要确认过的姓名）
      const selfNameInput = document.getElementById("resume-self-name");
      const nameValidation = validateUploadSelfName(selfNameInput ? selfNameInput.value : "");
      if (!nameValidation.valid) {
        regenBtn.disabled = false;
        regenBtn.textContent = "重新生成画像";
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = nameValidation.error;
        }
        if (selfNameInput) {
          selfNameInput.focus();
        }
        return;
      }

      chrome.runtime.sendMessage(
        {
          type: "regenerate_resume",
          slot,
          self_name: nameValidation.value,
        },
        (res) => {
          regenBtn.disabled = false;
          regenBtn.textContent = "重新生成画像";

          if (!res) {
            if (resumeMsgEl) {
              resumeMsgEl.className = "msg error";
              resumeMsgEl.textContent = "Jet 未运行";
            }
            return;
          }

          if (res.ok) {
            profileTextarea.value = res.data?.profile || "";
            updateCount();
            if (resumeMsgEl) {
              resumeMsgEl.className = "msg success";
              resumeMsgEl.textContent = `简历 ${slot} 画像重新生成成功`;
            }
          } else {
            const errMsg = formatResumeErrorMessage(res.error, res.data?.message);
            if (resumeMsgEl) {
              resumeMsgEl.className = "msg error";
              resumeMsgEl.textContent = errMsg;
            }
          }
        }
      );
    });

    // D. 删除简历
    delBtn.addEventListener("click", () => {
      handleDeleteResume({
        slot,
        itemEl,
        delBtn,
        msgEl: resumeMsgEl,
        updateSlots: updateResumeSlots,
      });
    });

    itemEl.appendChild(headerEl);
    itemEl.appendChild(fileGroup);
    itemEl.appendChild(nameGroup);
    itemEl.appendChild(profileGroup);
    itemEl.appendChild(actionsRow);

    return itemEl;
  }

  function loadResumes() {
    if (resumeSaveBtn) {
      resumeSaveBtn.disabled = true;
    }
    if (!resumeListEl || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      return;
    }

    chrome.runtime.sendMessage({ type: "get_resumes" }, (res) => {
      const state = decideResumeLoadState(res);

      if (resumeSaveBtn) {
        resumeSaveBtn.disabled = !state.canSave;
      }

      if (resumeMsgEl) {
        resumeMsgEl.className = state.msgType ? `msg ${state.msgType}` : "msg";
        resumeMsgEl.textContent = state.message;
      }

      if (!state.ok) {
        return;
      }

      resumeListEl.innerHTML = "";
      for (const item of res.data) {
        const el = createResumeItemElement(
          item.slot,
          item.name || "",
          item.profile || item.job_types || "",
          Boolean(item.has_text)
        );
        resumeListEl.appendChild(el);
      }
      updateResumeSlots();
    });
  }

  if (resumeAddBtn) {
    resumeAddBtn.addEventListener("click", () => {
      if (!resumeListEl) return;
      const currentItems = Array.from(resumeListEl.querySelectorAll(".resume-item"));
      if (currentItems.length >= 3) {
        return;
      }
      if (resumeMsgEl) {
        resumeMsgEl.textContent = "";
        resumeMsgEl.className = "msg";
      }
      const usedSlots = currentItems
        .map((el) => parseInt(el.dataset.slot, 10))
        .filter((n) => !isNaN(n));
      const nextSlot = allocateNextResumeSlot(usedSlots);
      if (nextSlot === null) {
        return;
      }
      const newItem = createResumeItemElement(nextSlot, "", "", false);
      const insertBeforeEl = currentItems.find(
        (el) => parseInt(el.dataset.slot, 10) > nextSlot
      );
      if (insertBeforeEl) {
        resumeListEl.insertBefore(newItem, insertBeforeEl);
      } else {
        resumeListEl.appendChild(newItem);
      }
      updateResumeSlots();
      const nameInp = newItem.querySelector(".resume-name-input");
      if (nameInp) nameInp.focus();
    });
  }

  if (resumeSaveBtn) {
    resumeSaveBtn.addEventListener("click", () => {
      if (resumeMsgEl) {
        resumeMsgEl.textContent = "";
        resumeMsgEl.className = "msg";
      }

      if (!resumeListEl) return;
      const itemEls = Array.from(resumeListEl.querySelectorAll(".resume-item"));

      if (itemEls.length > 3) {
        if (resumeMsgEl) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = "简历最多 3 份";
        }
        return;
      }

      const items = [];
      for (let i = 0; i < itemEls.length; i++) {
        const itemEl = itemEls[i];
        const slot = parseInt(itemEl.dataset.slot, 10) || (i + 1);
        const nameInput = itemEl.querySelector(".resume-name-input");
        const profileTextarea = itemEl.querySelector(".resume-profile-input");
        const rawName = nameInput ? nameInput.value : "";
        const strippedName = rawName.trim();
        const rawProfile = profileTextarea ? profileTextarea.value : "";
        const strippedProfile = rawProfile.trim();

        if (strippedName.length === 0) {
          if (resumeMsgEl) {
            resumeMsgEl.className = "msg error";
            resumeMsgEl.textContent = itemEls.length > 1
              ? `第 ${slot} 份简历名称不能为空`
              : "简历名称不能为空";
          }
          return;
        }

        if (strippedName.length > 100) {
          if (resumeMsgEl) {
            resumeMsgEl.className = "msg error";
            resumeMsgEl.textContent = itemEls.length > 1
              ? `第 ${slot} 份简历名称不能超过 100 字`
              : "简历名称不能超过 100 字";
          }
          return;
        }

        if (strippedProfile.length === 0) {
          if (resumeMsgEl) {
            resumeMsgEl.className = "msg error";
            resumeMsgEl.textContent = itemEls.length > 1
              ? `第 ${slot} 份简历画像不能为空`
              : "简历画像不能为空";
          }
          return;
        }

        if (strippedProfile.length > 300) {
          if (resumeMsgEl) {
            resumeMsgEl.className = "msg error";
            resumeMsgEl.textContent = itemEls.length > 1
              ? `第 ${slot} 份简历画像不能超过 300 字`
              : "简历画像不能超过 300 字";
          }
          return;
        }

        items.push({
          slot,
          name: strippedName,
          profile: strippedProfile,
        });
      }

      resumeSaveBtn.disabled = true;
      chrome.runtime.sendMessage({ type: "put_resumes", items }, (res) => {
        resumeSaveBtn.disabled = false;
        if (!resumeMsgEl) return;

        if (!res) {
          resumeMsgEl.className = "msg error";
          resumeMsgEl.textContent = "Jet 未运行";
          return;
        }

        if (res.ok) {
          resumeMsgEl.className = "msg success";
          resumeMsgEl.textContent = "已保存";
          updateResumeSlots();
        } else {
          resumeMsgEl.className = "msg error";
          if (res.viewState === "unpaired" || res.status === 401) {
            resumeMsgEl.textContent = "未配对";
          } else if (res.viewState === "jet_down" || res.status === 0) {
            resumeMsgEl.textContent = "Jet 未运行";
          } else if (res.error === "too_many_resumes") {
            resumeMsgEl.textContent = "简历最多 3 份";
          } else if (res.error === "resume_invalid") {
            resumeMsgEl.textContent = "简历名称或画像格式不符合要求";
          } else if (res.error === "slot_invalid") {
            resumeMsgEl.textContent = "简历编号无效";
          } else {
            resumeMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
          }
        }
      });
    });
  }

  // --- 从严行业 ---
  function renderStrictIndustries(available = [], selected = []) {
    if (!strictIndustryListEl) return;
    strictIndustryListEl.innerHTML = "";
    const selectedSet = new Set(Array.isArray(selected) ? selected : []);

    if (!Array.isArray(available) || available.length === 0) {
      if (strictIndustryEmptyEl) strictIndustryEmptyEl.style.display = "block";
      return;
    }
    if (strictIndustryEmptyEl) strictIndustryEmptyEl.style.display = "none";

    for (const ind of available) {
      const label = document.createElement("label");
      label.style.display = "inline-flex";
      label.style.alignItems = "center";
      label.style.marginRight = "16px";
      label.style.marginBottom = "8px";
      label.style.cursor = "pointer";
      label.style.fontWeight = "normal";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = ind;
      cb.checked = selectedSet.has(ind);
      cb.style.marginRight = "6px";

      label.appendChild(cb);
      label.appendChild(document.createTextNode(ind));
      strictIndustryListEl.appendChild(label);
    }
  }

  function loadStrictIndustries() {
    if (!strictIndustryListEl || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      return;
    }

    chrome.runtime.sendMessage({ type: "get_strict_industries" }, (res) => {
      const state = decideStrictIndustriesLoadState(res);

      if (strictIndustrySaveBtn) {
        strictIndustrySaveBtn.disabled = !state.canSave;
      }

      if (strictIndustryMsgEl) {
        strictIndustryMsgEl.className = state.msgType ? `msg ${state.msgType}` : "msg";
        strictIndustryMsgEl.textContent = state.message;
      }

      if (!state.ok) {
        return;
      }

      renderStrictIndustries(res.data?.available || [], res.data?.selected || []);
    });
  }

  if (strictIndustrySaveBtn) {
    strictIndustrySaveBtn.addEventListener("click", () => {
      if (strictIndustryMsgEl) {
        strictIndustryMsgEl.textContent = "";
        strictIndustryMsgEl.className = "msg";
      }

      if (!strictIndustryListEl) return;
      const checkedBoxes = Array.from(strictIndustryListEl.querySelectorAll('input[type="checkbox"]:checked'));
      const selected = checkedBoxes.map((cb) => cb.value);

      strictIndustrySaveBtn.disabled = true;
      chrome.runtime.sendMessage(
        { type: "save_strict_industries", selected: buildStrictIndustriesSavePayload(selected).selected },
        (res) => {
        strictIndustrySaveBtn.disabled = false;
        if (!strictIndustryMsgEl) return;

        if (!res) {
          strictIndustryMsgEl.className = "msg error";
          strictIndustryMsgEl.textContent = "Jet 未运行";
          return;
        }

        if (res.ok) {
          strictIndustryMsgEl.className = "msg success";
          const count = res.data?.selected?.length ?? selected.length;
          strictIndustryMsgEl.textContent = `已保存（已勾选 ${count} 个行业）`;
        } else {
          strictIndustryMsgEl.className = "msg error";
          if (res.viewState === "unpaired" || res.status === 401) {
            strictIndustryMsgEl.textContent = "未配对";
          } else if (res.viewState === "jet_down" || res.status === 0) {
            strictIndustryMsgEl.textContent = "Jet 未运行";
          } else {
            strictIndustryMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
          }
        }
      });
    });
  }

  // --- 我的经历素材 ---
  function updateItemNumbers() {
    if (!experienceListEl) return;
    const items = experienceListEl.querySelectorAll(".experience-item");
    items.forEach((itemEl, idx) => {
      const numLabel = itemEl.querySelector(".item-number");
      if (numLabel) {
        numLabel.textContent = `条目 ${idx + 1}`;
      }
    });
    const count = items.length;
    if (experienceAddBtn) {
      experienceAddBtn.disabled = count >= 10;
      if (count >= 10) {
        experienceAddBtn.title = "最多录入 10 条经历素材";
      } else {
        experienceAddBtn.removeAttribute("title");
      }
    }
    if (experienceEmptyEl) {
      experienceEmptyEl.style.display = count === 0 ? "block" : "none";
    }
  }

  function createExperienceItemElement(initialContent = "") {
    const itemEl = document.createElement("div");
    itemEl.className = "experience-item";

    const headerEl = document.createElement("div");
    headerEl.className = "experience-header";

    const titleEl = document.createElement("strong");
    titleEl.className = "item-number";
    titleEl.textContent = "条目";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-danger";
    delBtn.textContent = "删除";
    delBtn.style.padding = "4px 8px";
    delBtn.style.fontSize = "12px";

    headerEl.appendChild(titleEl);
    headerEl.appendChild(delBtn);

    const textarea = document.createElement("textarea");
    textarea.rows = 3;
    textarea.placeholder = "例如：在新能源外贸公司负责西非市场的逆变器渠道拓展，主导拜访了 10 余家本地分销商，实现季度销售额翻倍。";
    textarea.value = initialContent;

    const countEl = document.createElement("div");
    countEl.className = "char-count";

    function updateCount() {
      const len = textarea.value.length;
      countEl.textContent = `已输入 ${len} / 200`;
      if (len > 200) {
        countEl.classList.add("over-limit");
      } else {
        countEl.classList.remove("over-limit");
      }
    }
    updateCount();

    textarea.addEventListener("input", updateCount);

    delBtn.addEventListener("click", () => {
      itemEl.remove();
      updateItemNumbers();
      if (experienceMsgEl) {
        experienceMsgEl.textContent = "";
        experienceMsgEl.className = "msg";
      }
    });

    itemEl.appendChild(headerEl);
    itemEl.appendChild(textarea);
    itemEl.appendChild(countEl);

    return itemEl;
  }

  function loadExperience() {
    if (experienceSaveBtn) {
      experienceSaveBtn.disabled = true;
    }
    if (!experienceListEl || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      return;
    }

    chrome.runtime.sendMessage({ type: "get_experience" }, (res) => {
      if (!res) {
        experienceLoaded = false;
        if (experienceSaveBtn) {
          experienceSaveBtn.disabled = true;
        }
        if (experienceMsgEl) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = "Jet 未运行";
        }
        return;
      }

      if (res.viewState === "unpaired" || res.status === 401) {
        experienceLoaded = false;
        if (experienceSaveBtn) {
          experienceSaveBtn.disabled = true;
        }
        if (experienceMsgEl) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = "未配对";
        }
        return;
      }

      if (res.viewState === "jet_down" || res.status === 0) {
        experienceLoaded = false;
        if (experienceSaveBtn) {
          experienceSaveBtn.disabled = true;
        }
        if (experienceMsgEl) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = "Jet 未运行";
        }
        return;
      }

      if (res.ok && Array.isArray(res.data)) {
        experienceLoaded = true;
        if (experienceSaveBtn) {
          experienceSaveBtn.disabled = false;
        }
        if (experienceMsgEl) {
          experienceMsgEl.textContent = "";
        }
        experienceListEl.innerHTML = "";
        for (const item of res.data) {
          const el = createExperienceItemElement(item.content || "");
          experienceListEl.appendChild(el);
        }
        updateItemNumbers();
      } else {
        experienceLoaded = false;
        if (experienceSaveBtn) {
          experienceSaveBtn.disabled = true;
        }
        if (experienceMsgEl) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = `加载失败（${res.error || "未知错误"}）`;
        }
      }
    });
  }

  if (experienceAddBtn) {
    experienceAddBtn.addEventListener("click", () => {
      if (!experienceListEl) return;
      const currentCount = experienceListEl.querySelectorAll(".experience-item").length;
      if (currentCount >= 10) {
        return;
      }
      if (experienceMsgEl) {
        experienceMsgEl.textContent = "";
        experienceMsgEl.className = "msg";
      }
      const newItem = createExperienceItemElement("");
      experienceListEl.appendChild(newItem);
      updateItemNumbers();
      const ta = newItem.querySelector("textarea");
      if (ta) ta.focus();
    });
  }

  if (experienceSaveBtn) {
    experienceSaveBtn.addEventListener("click", () => {
      if (experienceMsgEl) {
        experienceMsgEl.textContent = "";
        experienceMsgEl.className = "msg";
      }

      if (!experienceListEl) return;
      const itemEls = Array.from(experienceListEl.querySelectorAll(".experience-item"));

      if (itemEls.length > 10) {
        if (experienceMsgEl) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = "经历素材最多 10 条";
        }
        return;
      }

      const items = [];
      for (let i = 0; i < itemEls.length; i++) {
        const textarea = itemEls[i].querySelector("textarea");
        const raw = textarea ? textarea.value : "";
        const stripped = raw.trim();

        if (stripped.length === 0) {
          if (experienceMsgEl) {
            experienceMsgEl.className = "msg error";
            experienceMsgEl.textContent = itemEls.length > 1
              ? `第 ${i + 1} 条经历内容不能为空`
              : "单条经历内容不能为空";
          }
          return;
        }

        if (raw.length > 200 || stripped.length > 200) {
          if (experienceMsgEl) {
            experienceMsgEl.className = "msg error";
            experienceMsgEl.textContent = itemEls.length > 1
              ? `第 ${i + 1} 条经历内容不能超过 200 字`
              : "单条经历内容不能超过 200 字";
          }
          return;
        }

        items.push({
          item_no: i + 1,
          content: stripped,
        });
      }

      experienceSaveBtn.disabled = true;
      chrome.runtime.sendMessage({ type: "put_experience", items }, (res) => {
        if (experienceLoaded) {
          experienceSaveBtn.disabled = false;
        }
        if (!experienceMsgEl) return;

        if (!res) {
          experienceMsgEl.className = "msg error";
          experienceMsgEl.textContent = "Jet 未运行";
          return;
        }

        if (res.ok) {
          experienceMsgEl.className = "msg success";
          experienceMsgEl.textContent = "已保存";
          updateItemNumbers();
        } else {
          experienceMsgEl.className = "msg error";
          if (res.viewState === "unpaired" || res.status === 401) {
            experienceMsgEl.textContent = "未配对";
          } else if (res.viewState === "jet_down" || res.status === 0) {
            experienceMsgEl.textContent = "Jet 未运行";
          } else if (res.error === "too_many_items") {
            experienceMsgEl.textContent = "经历素材最多 10 条";
          } else if (res.error === "content_invalid") {
            experienceMsgEl.textContent = "经历内容不能为空且不能超过 200 字";
          } else {
            experienceMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
          }
        }
      });
    });
  }

  // --- 数据发送同意 ---
  function loadConsentStatus(callback) {
    if (!consentStatusEl || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      if (consentStatusEl) consentStatusEl.textContent = "环境不可用";
      if (typeof callback === "function") callback();
      return;
    }

    chrome.runtime.sendMessage({ type: "chat_consent_status" }, (res) => {
      if (!res) {
        if (consentStatusEl) {
          consentStatusEl.className = "status-badge jet_down";
          consentStatusEl.textContent = "Jet 未运行";
        }
        if (consentTimeRow) consentTimeRow.style.display = "none";
        if (consentHintEl) consentHintEl.style.display = "none";
        if (consentRevokeBtn) consentRevokeBtn.style.display = "none";
        if (typeof callback === "function") callback(res);
        return;
      }

      if (res.viewState === "unpaired" || res.status === 401) {
        if (consentStatusEl) {
          consentStatusEl.className = "status-badge unpaired";
          consentStatusEl.textContent = "未配对";
        }
        if (consentTimeRow) consentTimeRow.style.display = "none";
        if (consentHintEl) consentHintEl.style.display = "none";
        if (consentRevokeBtn) consentRevokeBtn.style.display = "none";
      } else if (res.viewState === "jet_down" || res.status === 0) {
        if (consentStatusEl) {
          consentStatusEl.className = "status-badge jet_down";
          consentStatusEl.textContent = "Jet 未运行";
        }
        if (consentTimeRow) consentTimeRow.style.display = "none";
        if (consentHintEl) consentHintEl.style.display = "none";
        if (consentRevokeBtn) consentRevokeBtn.style.display = "none";
      } else if (res.ok && res.data) {
        const hasConsent = Boolean(res.data.has_consent);
        if (hasConsent) {
          if (consentStatusEl) {
            consentStatusEl.className = "status-badge paired";
            consentStatusEl.textContent = "已同意";
          }
          if (consentTimeRow) {
            consentTimeRow.style.display = "block";
          }
          if (consentTimeEl) {
            consentTimeEl.textContent = formatConsentTime(res.data.consented_at);
          }
          if (consentHintEl) {
            consentHintEl.style.display = "none";
          }
          if (consentRevokeBtn) {
            consentRevokeBtn.style.display = "inline-block";
          }
        } else {
          if (consentStatusEl) {
            consentStatusEl.className = "status-badge unpaired";
            consentStatusEl.textContent = "未同意";
          }
          if (consentTimeRow) {
            consentTimeRow.style.display = "none";
          }
          if (consentHintEl) {
            consentHintEl.style.display = "block";
            consentHintEl.textContent = "首次生成时会在侧边栏请你确认（当前同意覆盖'打开聊天自动发送'，旧的同意需要重新确认）";
          }
          if (consentRevokeBtn) {
            consentRevokeBtn.style.display = "none";
          }
        }
      }

      if (typeof callback === "function") callback(res);
    });
  }

  if (consentRevokeBtn) {
    consentRevokeBtn.addEventListener("click", () => {
      if (consentMsgEl) {
        consentMsgEl.textContent = "";
        consentMsgEl.className = "msg";
      }
      consentRevokeBtn.disabled = true;

      chrome.runtime.sendMessage({ type: "chat_revoke_consent" }, (res) => {
        consentRevokeBtn.disabled = false;
        if (!consentMsgEl) return;

        if (!res) {
          consentMsgEl.className = "msg error";
          consentMsgEl.textContent = "Jet 未运行";
          return;
        }

        if (res.ok) {
          loadConsentStatus(() => {
            consentMsgEl.className = "msg success";
            consentMsgEl.textContent = "已撤回，下次生成前会重新请你确认";
          });
        } else {
          consentMsgEl.className = "msg error";
          if (res.viewState === "unpaired" || res.status === 401) {
            consentMsgEl.textContent = "未配对";
          } else if (res.viewState === "jet_down" || res.status === 0) {
            consentMsgEl.textContent = "Jet 未运行";
          } else {
            consentMsgEl.textContent = `撤回失败（${res.error || "未知错误"}）`;
          }
        }
      });
    });
  }

  // --- 列表预判设置 ---
  function loadPrejudgeSettings(callback) {
    if (!prejudgeDailyLimitInput || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      if (typeof callback === "function") callback();
      return;
    }

    chrome.runtime.sendMessage({ type: "get_prejudge_settings" }, (res) => {
      if (!res) {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.className = "msg error";
          prejudgeMsgEl.textContent = "Jet 未运行";
        }
        if (typeof callback === "function") callback(res);
        return;
      }

      if (res.viewState === "unpaired" || res.status === 401) {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.className = "msg error";
          prejudgeMsgEl.textContent = "未配对";
        }
        if (typeof callback === "function") callback(res);
        return;
      }

      if (res.viewState === "jet_down" || res.status === 0) {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.className = "msg error";
          prejudgeMsgEl.textContent = "Jet 未运行";
        }
        if (typeof callback === "function") callback(res);
        return;
      }

      if (res.ok && res.data) {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.textContent = "";
          prejudgeMsgEl.className = "msg";
        }
        const data = res.data;
        prejudgeDailyLimitInput.value = data.daily_prejudge_limit != null ? data.daily_prejudge_limit : 20;
        if (prejudgeQuotaInfoEl) {
          prejudgeQuotaInfoEl.textContent = formatPrejudgeQuotaText(
            data.used_today,
            data.remaining_today,
            data.daily_prejudge_limit
          );
        }
      } else {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.className = "msg error";
          prejudgeMsgEl.textContent = `加载失败（${res.error || "未知错误"}）`;
        }
      }

      if (typeof callback === "function") callback(res);
    });
  }

  if (prejudgeSaveBtn) {
    prejudgeSaveBtn.addEventListener("click", () => {
      if (prejudgeMsgEl) {
        prejudgeMsgEl.textContent = "";
        prejudgeMsgEl.className = "msg";
      }

      const rawVal = prejudgeDailyLimitInput ? prejudgeDailyLimitInput.value : "";
      const validation = validatePrejudgeLimit(rawVal);
      if (!validation.valid) {
        if (prejudgeMsgEl) {
          prejudgeMsgEl.className = "msg error";
          prejudgeMsgEl.textContent = validation.error;
        }
        return;
      }

      prejudgeSaveBtn.disabled = true;
      chrome.runtime.sendMessage(
        {
          type: "put_prejudge_settings",
          daily_prejudge_limit: validation.value,
        },
        (res) => {
          prejudgeSaveBtn.disabled = false;
          if (!prejudgeMsgEl) return;

          if (!res) {
            prejudgeMsgEl.className = "msg error";
            prejudgeMsgEl.textContent = "Jet 未运行";
            return;
          }

          if (res.ok && res.data) {
            prejudgeMsgEl.className = "msg success";
            prejudgeMsgEl.textContent = "已保存";
            const data = res.data;
            prejudgeDailyLimitInput.value = data.daily_prejudge_limit;
            if (prejudgeQuotaInfoEl) {
              prejudgeQuotaInfoEl.textContent = formatPrejudgeQuotaText(
                data.used_today,
                data.remaining_today,
                data.daily_prejudge_limit
              );
            }
          } else {
            prejudgeMsgEl.className = "msg error";
            if (res.viewState === "unpaired" || res.status === 401) {
              prejudgeMsgEl.textContent = "未配对";
            } else if (res.viewState === "jet_down" || res.status === 0) {
              prejudgeMsgEl.textContent = "Jet 未运行";
            } else if (res.status === 422 || res.error === "invalid_limit") {
              prejudgeMsgEl.textContent = "每日上限须为 0 至 200 之间的整数";
            } else {
              prejudgeMsgEl.textContent = `保存失败（${res.error || "未知错误"}）`;
            }
          }
        }
      );
    });
  }

  refreshStatus();
  loadLlmKey();
  loadProfile();
  loadResumes();
  loadStrictIndustries();
  loadMarksSetting();
  loadAutoGenerateSetting();
  loadExperience();
  loadConsentStatus();
  loadPrejudgeSettings();
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initOptions);
}
