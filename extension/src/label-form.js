import {
  CATEGORIES,
  WORK_INTENSITY,
  VERDICTS,
  VERDICT_LABELS,
  SALES_LEVEL,
  EXPERIENCE_FIT,
  LEGACY_VERDICT_MAP,
} from "./taxonomy.js";

export const LABEL_OPTIONS = {
  categories: CATEGORIES,
  work_intensity: WORK_INTENSITY,
  sales_level: SALES_LEVEL,
  experience_fit: EXPERIENCE_FIT,
  overall: VERDICTS.map((v) => ({ value: v, label: VERDICT_LABELS[v] })),
};

/**
 * 把表单选择转成 API 请求体。
 * 规则：
 * - work_type: 必须在大类内，否则 null
 * - work_subtype: 必须属于已选大类细分，否则 null
 * - secondary_work_types: 0–3 项对象 [{category, subtype}]，排除与主要类型相同的项；空数组转为 null
 * - sales_level / experience_fit / work_intensity / overall: 不在合法选项中 → null
 * - note: 去首尾空白、空串 → null、超过 100 字截断
 * - 全为 null 时返回 { ok: false, error: "请至少选择一项" }
 * - 只填了次要类型也算"至少一项"
 * - 否则返回 { ok: true, payload }
 *
 * @param {object|null} selection
 * @returns {{ ok: true, payload: object } | { ok: false, error: string }}
 */
export function toLabelPayload(selection) {
  if (!selection || typeof selection !== "object") {
    return { ok: false, error: "请至少选择一项" };
  }

  const work_type =
    typeof selection.work_type === "string" &&
    Object.prototype.hasOwnProperty.call(CATEGORIES, selection.work_type)
      ? selection.work_type
      : null;

  let work_subtype = null;
  if (
    work_type !== null &&
    typeof selection.work_subtype === "string" &&
    Array.isArray(CATEGORIES[work_type]) &&
    CATEGORIES[work_type].includes(selection.work_subtype)
  ) {
    work_subtype = selection.work_subtype;
  }

  let secondary_work_types = null;
  if (Array.isArray(selection.secondary_work_types)) {
    const valid = [];
    for (const item of selection.secondary_work_types) {
      if (item && typeof item === "object") {
        const cat = item.category;
        const sub = item.subtype || null;
        if (
          typeof cat === "string" &&
          Object.prototype.hasOwnProperty.call(CATEGORIES, cat)
        ) {
          const subValid =
            sub === null ||
            (Array.isArray(CATEGORIES[cat]) && CATEGORIES[cat].includes(sub));
          if (subValid) {
            const isSameAsPrimary =
              work_type !== null &&
              cat === work_type &&
              (work_subtype || null) === sub;
            if (!isSameAsPrimary) {
              const alreadyExists = valid.some(
                (v) => v.category === cat && (v.subtype || null) === sub
              );
              if (!alreadyExists) {
                valid.push({ category: cat, subtype: sub });
                if (valid.length === 3) break;
              }
            }
          }
        }
      }
    }
    if (valid.length > 0) {
      secondary_work_types = valid;
    }
  }

  const sales_level =
    typeof selection.sales_level === "string" &&
    SALES_LEVEL.includes(selection.sales_level)
      ? selection.sales_level
      : null;

  const experience_fit =
    typeof selection.experience_fit === "string" &&
    EXPERIENCE_FIT.includes(selection.experience_fit)
      ? selection.experience_fit
      : null;

  const work_intensity =
    typeof selection.work_intensity === "string" &&
    WORK_INTENSITY.includes(selection.work_intensity)
      ? selection.work_intensity
      : null;

  let overall = null;
  if (typeof selection.overall === "string") {
    let o = selection.overall;
    if (LEGACY_VERDICT_MAP[o]) {
      o = LEGACY_VERDICT_MAP[o];
    }
    if (VERDICTS.includes(o)) {
      overall = o;
    }
  }

  let note = null;
  if (typeof selection.note === "string") {
    const trimmed = selection.note.trim();
    if (trimmed.length > 0) {
      note = trimmed.length > 100 ? trimmed.slice(0, 100) : trimmed;
    }
  }

  if (
    work_type === null &&
    work_subtype === null &&
    secondary_work_types === null &&
    sales_level === null &&
    experience_fit === null &&
    work_intensity === null &&
    overall === null &&
    note === null
  ) {
    return { ok: false, error: "请至少选择一项" };
  }

  return {
    ok: true,
    payload: {
      work_type,
      work_subtype,
      secondary_work_types,
      sales_level,
      experience_fit,
      work_intensity,
      overall,
      note,
    },
  };
}

/**
 * 把 Judgement.label 转成表单初始选择。
 *
 * @param {object|null} label Judgement 对象中的 label 字段
 * @returns {object} 表单选择对象
 */
export function fromLabel(label) {
  if (!label || typeof label !== "object") {
    return {
      work_type: null,
      work_subtype: null,
      secondary_work_types: [],
      sales_level: null,
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: "",
    };
  }

  const work_type =
    typeof label.work_type === "string" &&
    Object.prototype.hasOwnProperty.call(CATEGORIES, label.work_type)
      ? label.work_type
      : null;

  let work_subtype = null;
  if (
    work_type !== null &&
    typeof label.work_subtype === "string" &&
    Array.isArray(CATEGORIES[work_type]) &&
    CATEGORIES[work_type].includes(label.work_subtype)
  ) {
    work_subtype = label.work_subtype;
  }

  let secondary_work_types = [];
  if (Array.isArray(label.secondary_work_types)) {
    const valid = [];
    for (const item of label.secondary_work_types) {
      if (item && typeof item === "object") {
        const cat = item.category;
        const sub = item.subtype || null;
        if (
          typeof cat === "string" &&
          Object.prototype.hasOwnProperty.call(CATEGORIES, cat)
        ) {
          const subValid =
            sub === null ||
            (Array.isArray(CATEGORIES[cat]) && CATEGORIES[cat].includes(sub));
          if (subValid) {
            const isSameAsPrimary =
              work_type !== null &&
              cat === work_type &&
              (work_subtype || null) === sub;
            if (!isSameAsPrimary) {
              const alreadyExists = valid.some(
                (v) => v.category === cat && (v.subtype || null) === sub
              );
              if (!alreadyExists) {
                valid.push({ category: cat, subtype: sub });
                if (valid.length === 3) break;
              }
            }
          }
        }
      }
    }
    secondary_work_types = valid;
  }

  let overall = null;
  if (typeof label.overall === "string") {
    let o = label.overall;
    if (LEGACY_VERDICT_MAP[o]) {
      o = LEGACY_VERDICT_MAP[o];
    }
    if (VERDICTS.includes(o)) {
      overall = o;
    }
  }

  return {
    work_type,
    work_subtype,
    secondary_work_types,
    sales_level:
      typeof label.sales_level === "string" &&
      SALES_LEVEL.includes(label.sales_level)
        ? label.sales_level
        : null,
    experience_fit:
      typeof label.experience_fit === "string" &&
      EXPERIENCE_FIT.includes(label.experience_fit)
        ? label.experience_fit
        : null,
    work_intensity:
      typeof label.work_intensity === "string" &&
      WORK_INTENSITY.includes(label.work_intensity)
        ? label.work_intensity
        : null,
    overall,
    note: typeof label.note === "string" ? label.note : "",
  };
}

/**
 * 切换表单单选项纯函数。
 * 规则：
 * - work_type: 点击同值取消；切换大类清空 work_subtype 并从 secondary 移除与新主要类型相同的项
 * - work_subtype: 必须属于当前大类，否则忽略；点击同值取消；选择时从 secondary 移除相同项
 * - 其他单选字段：点击同值取消，否则设为新值
 * - 返回新对象，不修改原对象
 *
 * @param {object|null} selection 当前选择对象
 * @param {string} field 字段名
 * @param {string|null} value 选中的值
 * @returns {object} 更新后的 selection 对象
 */
export function toggleOption(selection, field, value) {
  const current =
    selection && typeof selection === "object" ? selection : fromLabel(null);

  if (field === "work_type") {
    const currentVal = current.work_type || null;
    const nextVal = currentVal === value ? null : value;
    if (nextVal !== null && !Object.prototype.hasOwnProperty.call(CATEGORIES, nextVal)) {
      return current;
    }
    const updated = {
      ...current,
      work_type: nextVal,
      work_subtype: null,
    };
    if (nextVal !== null && Array.isArray(updated.secondary_work_types)) {
      updated.secondary_work_types = updated.secondary_work_types.filter(
        (item) => !(item.category === nextVal && (item.subtype || null) === null)
      );
    }
    return updated;
  }

  if (field === "work_subtype") {
    const currentType = current.work_type;
    if (
      !currentType ||
      !Array.isArray(CATEGORIES[currentType]) ||
      !CATEGORIES[currentType].includes(value)
    ) {
      return current;
    }
    const currentSub = current.work_subtype || null;
    const nextSub = currentSub === value ? null : value;
    const updated = {
      ...current,
      work_subtype: nextSub,
    };
    if (Array.isArray(updated.secondary_work_types)) {
      updated.secondary_work_types = updated.secondary_work_types.filter(
        (item) => !(item.category === currentType && (item.subtype || null) === (nextSub || null))
      );
    }
    return updated;
  }

  const currentVal = current[field] !== undefined ? current[field] : null;
  const nextVal = currentVal === value ? null : value;
  return {
    ...current,
    [field]: nextVal,
  };
}

/**
 * 切换次要类型多选项纯函数。
 * 规则：
 * - 输入为 {category, subtype}
 * - 多选切换；已选则取消（移除）
 * - 已选 3 个时再加 → 返回原 selection 与 error: "最多选 3 项"
 * - 与主要类型相同 → 不变，error 为 null
 * - 非合法选项 → 不变，error 为 null
 * - 成功添加或移除 → 返回更新后的 selection，error 为 null
 *
 * @param {object|null} selection 当前选择对象
 * @param {{category: string, subtype?: string|null}|string} item 次要类型对象或大类字符串
 * @returns {{ selection: object, error: string|null }}
 */
export function toggleSecondary(selection, item) {
  const current =
    selection && typeof selection === "object" ? selection : fromLabel(null);

  const targetItem =
    typeof item === "string" ? { category: item, subtype: null } : item;

  if (!targetItem || typeof targetItem !== "object") {
    return { selection: current, error: null };
  }

  const category = targetItem.category;
  const subtype = targetItem.subtype || null;

  if (
    typeof category !== "string" ||
    !Object.prototype.hasOwnProperty.call(CATEGORIES, category)
  ) {
    return { selection: current, error: null };
  }

  if (
    subtype !== null &&
    (!Array.isArray(CATEGORIES[category]) || !CATEGORIES[category].includes(subtype))
  ) {
    return { selection: current, error: null };
  }

  const isSameAsPrimary =
    current.work_type !== null &&
    current.work_type === category &&
    (current.work_subtype || null) === subtype;

  if (isSameAsPrimary) {
    return { selection: current, error: null };
  }

  const currentSecondary = Array.isArray(current.secondary_work_types)
    ? [...current.secondary_work_types]
    : [];

  const existingIdx = currentSecondary.findIndex(
    (s) => s.category === category && (s.subtype || null) === subtype
  );

  if (existingIdx !== -1) {
    const nextSecondary = currentSecondary.filter((_, idx) => idx !== existingIdx);
    return {
      selection: {
        ...current,
        secondary_work_types: nextSecondary,
      },
      error: null,
    };
  }

  if (currentSecondary.length >= 3) {
    return { selection: current, error: "最多选 3 项" };
  }

  return {
    selection: {
      ...current,
      secondary_work_types: [
        ...currentSecondary,
        { category, subtype },
      ],
    },
    error: null,
  };
}

/**
 * 把用户输入的 HR 备注规整为接口请求体。
 * 规则：
 * - text 去首尾空白
 * - 空或非字符串 -> { note: null }
 * - 超过 200 字 -> 截断为 200 字
 * - 否则 -> { note: text }
 *
 * @param {string|null|undefined} text
 * @returns {{ note: string | null }}
 */
export function toHrNotePayload(text) {
  if (typeof text !== "string") {
    return { note: null };
  }
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { note: null };
  }
  return {
    note: trimmed.length > 200 ? trimmed.slice(0, 200) : trimmed,
  };
}

// BEGIN SYNC hr-note-autosave (label-form.js)
/**
 * 判断 HR 实际情况输入是否需要自动保存纯函数。
 * 规则：
 * - 只有 trim 后非空、且与最近一次成功保存的内容不同时才保存；
 * - 内容为空绝不自动保存；
 * - 超过 200 字截断为 200 字；
 * - 截断后与最近一次保存内容相同时不保存。
 *
 * @param {string|null|undefined} text
 * @param {string|null|undefined} lastSaved
 * @returns {{ shouldSave: boolean, reason?: string, note: string|null }}
 */
export function decideHrNoteAutoSave(text, lastSaved) {
  if (typeof text !== "string") {
    return { shouldSave: false, reason: "empty", note: null };
  }
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { shouldSave: false, reason: "empty", note: null };
  }
  const note = trimmed.length > 200 ? trimmed.slice(0, 200) : trimmed;
  const normLastSaved =
    typeof lastSaved === "string"
      ? (lastSaved.trim().length > 200 ? lastSaved.trim().slice(0, 200) : lastSaved.trim())
      : "";
  if (note === normLastSaved) {
    return { shouldSave: false, reason: "unchanged", note };
  }
  return { shouldSave: true, note };
}

/**
 * 创建 HR 实际情况自动保存调度器。
 * 纯调度控制逻辑：负责防抖等待、失焦保存、在途排队不乱序、状态回调。
 *
 * @param {object} options
 * @param {number} [options.delayMs=1000] 防抖等待时间（毫秒）
 * @param {string|null} [options.initialSaved=null] 初始已保存内容
 * @param {string} [options.emptyMessage="内容为空不会自动保存"] 为空时的提示文案
 * @param {function(string): Promise<{ ok: boolean, hr_note?: string, error?: string, message?: string }>} [options.save] 执行保存的异步函数
 * @param {function(string): Promise<{ ok: boolean, hr_note?: string, error?: string, message?: string }>} [options.saveFn] 执行保存的异步函数（兼容别名）
 * @param {function({ status: string, message: string, note?: string|null }): void} [options.onStatusChange] 状态改变回调
 * @param {function(string): void} [options.onSaveSuccess] 保存成功回调
 * @param {function(function, number): any} [options.setTimeout] 定时器函数注入
 * @param {function(any): void} [options.clearTimeout] 清除定时器函数注入
 * @returns {object} coordinator 实例
 */
export function createAutoSaveCoordinator(options = {}) {
  const delayMs = options.delayMs !== undefined ? options.delayMs : 1000;
  let lastSaved = typeof options.initialSaved === "string" ? options.initialSaved.trim() : null;
  const emptyMessage = options.emptyMessage || "内容为空不会自动保存";
  const save = options.save || options.saveFn;
  const onStatusChange = options.onStatusChange || (() => {});
  const onSaveSuccess = options.onSaveSuccess || (() => {});
  const setTimer = options.setTimeout || ((fn, ms) => setTimeout(fn, ms));
  const clearTimerFn = options.clearTimeout || ((id) => clearTimeout(id));

  let timer = null;
  let inFlight = false;
  let inFlightPromise = null;
  let pendingText = null;
  let lastResult = null;

  function clearTimer() {
    if (timer !== null) {
      clearTimerFn(timer);
      timer = null;
    }
  }

  async function executeSingleSave(noteToSave) {
    inFlight = true;
    onStatusChange({ status: "saving", message: "保存中...", note: noteToSave });

    try {
      const res = await save(noteToSave);
      if (res && res.ok) {
        const savedVal = res.hr_note !== undefined && res.hr_note !== null ? res.hr_note : noteToSave;
        lastSaved = savedVal;
        lastResult = { ok: true, note: savedVal, attemptedNote: noteToSave };
        onSaveSuccess(savedVal);
        onStatusChange({ status: "saved", message: "已自动保存", note: savedVal });
      } else {
        const errMsg = res?.message || res?.error || "保存失败";
        lastResult = { ok: false, error: errMsg, message: errMsg, attemptedNote: noteToSave };
        onStatusChange({ status: "error", message: errMsg, note: noteToSave });
      }
    } catch (err) {
      const errMsg = err?.message || String(err) || "保存失败";
      lastResult = { ok: false, error: errMsg, message: errMsg, attemptedNote: noteToSave };
      onStatusChange({ status: "error", message: errMsg, note: noteToSave });
    } finally {
      inFlight = false;
    }
  }

  async function runSaveLoop(initialNote) {
    let currentNote = initialNote;
    while (currentNote !== null) {
      await executeSingleSave(currentNote);
      currentNote = null;

      if (pendingText !== null) {
        const nextText = pendingText;
        pendingText = null;
        const decision = decideHrNoteAutoSave(nextText, lastSaved);
        if (decision.shouldSave) {
          if (lastResult && !lastResult.ok && lastResult.attemptedNote === decision.note) {
            break;
          }
          currentNote = decision.note;
        } else if (decision.reason === "empty") {
          onStatusChange({ status: "empty", message: emptyMessage, note: null });
        }
      }
    }
    return lastResult || { ok: true, saved: false, note: lastSaved };
  }

  function triggerSave(note) {
    inFlightPromise = runSaveLoop(note).finally(() => {
      inFlightPromise = null;
    });
    return inFlightPromise;
  }

  function onInput(text) {
    clearTimer();
    const decision = decideHrNoteAutoSave(text, lastSaved);
    if (!decision.shouldSave) {
      if (decision.reason === "empty") {
        onStatusChange({ status: "empty", message: emptyMessage, note: null });
      }
      return;
    }
    timer = setTimer(() => {
      timer = null;
      if (inFlight || inFlightPromise) {
        pendingText = text;
      } else {
        const nextDecision = decideHrNoteAutoSave(text, lastSaved);
        if (nextDecision.shouldSave) {
          triggerSave(nextDecision.note);
        }
      }
    }, delayMs);
  }

  function onBlur(text) {
    clearTimer();
    if (inFlight || inFlightPromise) {
      pendingText = text;
      return inFlightPromise;
    }
    const decision = decideHrNoteAutoSave(text, lastSaved);
    if (!decision.shouldSave) {
      if (decision.reason === "empty") {
        onStatusChange({ status: "empty", message: emptyMessage, note: null });
      }
      return Promise.resolve(lastResult || { ok: true, saved: false, note: lastSaved });
    }
    return triggerSave(decision.note);
  }

  async function flush(text) {
    clearTimer();
    const prevSaved = lastSaved;

    if (inFlightPromise) {
      pendingText = text;
      await inFlightPromise;
    }

    const decision = decideHrNoteAutoSave(text, lastSaved);
    if (!decision.shouldSave) {
      if (decision.reason === "empty") {
        onStatusChange({ status: "empty", message: emptyMessage, note: null });
      }
      const normInput = decideHrNoteAutoSave(text, null).note;
      if (lastResult && !lastResult.ok && lastResult.attemptedNote === normInput) {
        return {
          ok: false,
          saved: false,
          note: lastSaved,
          error: lastResult.error,
          message: lastResult.message,
        };
      }
      const wasSaved = lastSaved !== prevSaved && lastSaved === normInput;
      return { ok: true, saved: wasSaved, note: lastSaved };
    }

    await triggerSave(decision.note);

    if (lastResult && !lastResult.ok) {
      return {
        ok: false,
        saved: false,
        note: lastSaved,
        error: lastResult.error,
        message: lastResult.message,
      };
    }

    return {
      ok: true,
      saved: true,
      note: lastSaved,
    };
  }

  function cancel() {
    clearTimer();
    pendingText = null;
  }

  function getLastSaved() {
    return lastSaved;
  }

  function setLastSaved(val) {
    lastSaved = typeof val === "string" ? val.trim() : null;
  }

  function isInFlight() {
    return inFlight || inFlightPromise !== null;
  }

  function hasPending() {
    return pendingText !== null || timer !== null;
  }

  return {
    onInput,
    handleInput: onInput,
    onBlur,
    handleBlur: onBlur,
    flush,
    handleComplete: flush,
    cancel,
    getLastSaved,
    setLastSaved,
    isInFlight,
    hasPending,
  };
}
// END SYNC hr-note-autosave
