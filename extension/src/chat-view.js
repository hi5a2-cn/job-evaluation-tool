/**
 * chat-view.js
 * HR 沟通助手纯函数视图与状态映射模块（ES module，纯函数，不访问 DOM、不发网络请求）
 * 对应 spec R1, R2, FR-005, FR-011, FR-012, FR-013, FR-016, FR-017, FR-030, FR-032
 */

import { fromJudgement, describe, shouldShowResumeSuggestion } from "./view-state.js";
import { LEGACY_VERDICT_MAP } from "./taxonomy.js";

export const ERROR_MESSAGES = {
  consent_required: "尚未同意发送脱敏数据",
  self_name_unavailable: "读不到你的姓名，暂时不能生成",
  hash_mismatch: "发送内容校验不一致，请重新预览后再试",
  quota_exhausted: "今日生成次数已用完",
  all_suggestions_dropped: "这次没有可用的建议，可以再点一次生成",
  unpaired: "未配对",
  jet_down: "Jet 没有运行，请先启动本机 Jet",
  read_chat_failed: "读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试",
  chat_unavailable: "读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试",
  chat_switched: "已切换到新的聊天，请重新生成",
  no_llm_key: "请先在设置页填写 DeepSeek API Key",
};

/**
 * 将错误码或错误响应对象映射为 R2 规范的中文提示
 */
export function formatChatError(err, detail) {
  if (!err) {
    return "生成失败，请稍后重试";
  }

  const isObj = typeof err === "object" && err !== null;
  const code = isObj
    ? (err.viewState === "jet_down" ? "jet_down" : (err.error || err.viewState || err.code))
    : err;

  const reason =
    (typeof detail === "string" && detail) ||
    (isObj && (err?.reason || err?.message)) ||
    "";

  if (code === "no_llm_key") {
    return "请先在设置页填写 DeepSeek API Key";
  }

  if (code === "llm_failed") {
    if (isObj && typeof err.data?.message === "string" && err.data.message.trim().length > 0) {
      return err.data.message;
    }
    return reason ? `生成失败：${reason}，可以再点一次生成` : "生成失败，可以再点一次生成";
  }

  if (
    code === "read_chat_failed" ||
    code === "chat_unavailable" ||
    code === "no_chat" ||
    code === "read_failed"
  ) {
    return "读不到当前聊天，请在 BOSS 里重新点开这个聊天后再试";
  }

  if (code === "jet_down" || code === "connection_failed") {
    return "Jet 没有运行，请先启动本机 Jet";
  }

  if (typeof code === "string" && ERROR_MESSAGES[code]) {
    return ERROR_MESSAGES[code];
  }

  return reason || "生成失败，请稍后重试";
}

/**
 * 由 status 响应计算 Key 配置状态纯函数 (007-friend-install)
 * 只有在 status 请求成功且为已配对状态、并且响应明确给出 llm_key_configured === false 时才设为 false；
 * status 请求失败、未配对、服务不可用或字段缺失时重置为 true（未知状态不显示"请先填写 Key"，由现有的连接/配对提示负责）。
 *
 * @param {object|null|undefined} statusRes
 * @returns {boolean}
 */
export function computeLlmKeyConfigured(statusRes) {
  if (
    statusRes &&
    statusRes.state === "paired" &&
    !statusRes.error &&
    statusRes.status?.llm_key_configured === false
  ) {
    return false;
  }
  return true;
}

/**
 * 决策侧边栏顶部额度横幅展示状态与文本纯函数 (007-friend-install)
 *
 * @param {object|null|undefined} statusRes
 * @returns {{
 *   className: string,
 *   text: string,
 *   isNoKey: boolean,
 * }}
 */
export function decideQuotaBanner(statusRes) {
  if (!statusRes) {
    return { className: "top-banner status-error", text: "Jet 未运行", isNoKey: false };
  }
  if (statusRes.state === "unpaired") {
    return { className: "top-banner status-warning", text: "未配对", isNoKey: false };
  }
  if (statusRes.state === "failed") {
    return {
      className: "top-banner status-error",
      text: `连接异常（${statusRes.error || "未知错误"}）`,
      isNoKey: false,
    };
  }
  if (statusRes.state === "paired") {
    if (statusRes.status?.llm_key_configured === false) {
      return {
        className: "top-banner status-warning",
        text: "请先在设置页填写 DeepSeek API Key",
        isNoKey: true,
      };
    }
    const text = statusRes.status?.quota
      ? formatJudgeQuotaText(statusRes.status.quota)
      : "已配对";
    return { className: "top-banner status-paired", text, isNoKey: false };
  }
  return { className: "top-banner status-error", text: "Jet 未运行", isNoKey: false };
}

/**
 * 决策侧边栏与卡片状态徽标文案与颜色纯函数 (007-friend-install)
 * 当 llm_key_configured 为 false 时显示"请先在设置页填写 DeepSeek API Key"，绝不显示"判断失败"
 * 无论是否有徽标均返回对象（无徽标时 text 为 null），保证调用处可安全解构与访问 isNoKey
 *
 * @param {object|null|undefined} detail
 * @param {boolean} [llmKeyConfigured=true]
 * @returns {{ text: string|null, tone?: string, isNoKey: boolean }}
 */
export function decideJobCardBadge(detail, llmKeyConfigured = true) {
  if (!detail) return { isNoKey: false, text: null };
  if (llmKeyConfigured === false || detail.view_state === "no_llm_key") {
    return {
      text: "请先在设置页填写 DeepSeek API Key",
      tone: "gray",
      isNoKey: true,
    };
  }
  if (detail.verdict_label) {
    return {
      text: detail.verdict_label,
      tone: detail.verdict_tone || "gray",
      isNoKey: false,
    };
  }
  if (detail.label) {
    return {
      text: detail.label,
      tone: "gray",
      isNoKey: false,
    };
  }
  return { isNoKey: false, text: null };
}

/**
 * 沟通阶段判定类型标签映射 (FR-012)
 */
export const MODE_LABELS = {
  opening: "开场白",
  reply: "建议回复",
  waiting_hr: "正在等 HR 回复",
};


/**
 * 格式化侧边栏结果顶部判定类型展示标题 (FR-013)
 */
export function formatModeDecision(mode) {
  if (mode === "waiting_hr") {
    return "正在等 HR 回复";
  }
  const label = MODE_LABELS[mode];
  return label ? `判断为：${label}` : "";
}

/**
 * 格式化归属提示："这是给 [公司] · [职位] 的建议" (FR-016)
 */
export function formatAttribution(companyName, jobTitle) {
  const company =
    typeof companyName === "string" && companyName.trim()
      ? companyName.trim()
      : "该公司";
  const title =
    typeof jobTitle === "string" && jobTitle.trim()
      ? jobTitle.trim()
      : "该职位";
  return `这是给 ${company} · ${title} 的建议`;
}

/**
 * 格式化已加载非系统消息条数提示："根据已加载的 N 条消息生成" (FR-017)
 */
export function formatLoadedMessagesNotice(count) {
  const n =
    typeof count === "number"
      ? Math.max(0, Math.floor(count))
      : parseInt(count, 10) || 0;
  return `根据已加载的 ${n} 条消息生成`;
}

/**
 * 格式化来源说明：只列经历条目编号，无经历显示"没有可引用的经历" (FR-030, FR-032)
 */
export function formatSourceNote(referencedIds) {
  if (!Array.isArray(referencedIds) || referencedIds.length === 0) {
    return "没有可引用的经历";
  }
  const validIds = referencedIds
    .map((id) => Number(id))
    .filter((id) => Number.isInteger(id) && id > 0);

  if (validIds.length === 0) {
    return "没有可引用的经历";
  }
  return `来源：经历条目 ${validIds.join("、")}`;
}

/**
 * 获取可复制的话术纯文本（保证正文不夹带经历编号与标注） (FR-030)
 */
export function getCopyableText(suggestion) {
  if (!suggestion) {
    return "";
  }
  if (
    typeof suggestion === "object" &&
    (suggestion.type === "request" ||
      suggestion.type === "unanswered_fact" ||
      suggestion.type === "dropped_summary" ||
      suggestion.type === "dropped" ||
      suggestion.reason !== undefined ||
      suggestion.category !== undefined)
  ) {
    return "";
  }
  if (typeof suggestion === "string") {
    return suggestion;
  }
  if (typeof suggestion.text === "string") {
    return suggestion.text;
  }
  return "";
}

/**
 * 复制前核对当前活跃聊天的岗位 ID 与建议绑定的岗位 ID (FR-005)
 */
export function verifyCopyJobId(currentJobId, boundJobId) {
  if (!currentJobId || !boundJobId || currentJobId !== boundJobId) {
    return {
      ok: false,
      error: "已切换到新的聊天，请重新生成",
    };
  }
  return { ok: true };
}

/**
 * 根据沟通模式获取展示的话术列表 (FR-012, FR-014)
 * 当 mode 为 waiting_hr 时返回空数组，只展示问题；其它模式返回 suggestions 数组
 */
export function getDisplaySuggestions(dataOrMode, maybeSuggestions) {
  let mode;
  let suggestions;
  if (typeof dataOrMode === "object" && dataOrMode !== null) {
    mode = dataOrMode.mode;
    suggestions = dataOrMode.suggestions;
  } else {
    mode = dataOrMode;
    suggestions = maybeSuggestions;
  }
  if (mode === "waiting_hr") {
    return [];
  }
  return Array.isArray(suggestions) ? suggestions : [];
}

/**
 * 获取手动切换模式的目标模式 (FR-013)
 * opening <-> reply；waiting_hr 不支持切换（返回 null）
 */
export function getNextForceMode(currentMode) {
  if (currentMode === "opening") return "reply";
  if (currentMode === "reply") return "opening";
  return null;
}

/**
 * 获取手动切换模式按钮的展示文案 (FR-013)
 */
export function getSwitchModeButtonText(currentMode) {
  if (currentMode === "opening") return "切换为建议回复";
  if (currentMode === "reply") return "切换为开场白";
  return "";
}

/**
 * 无 Jet 判断时的显眼标注提示 (FR-011)
 */
export const NO_JET_JUDGEMENT_NOTICE = "这个岗位没有 Jet 判断";

/**
 * 将 request_notice 与 unanswered_facts 整理成待显示的提示列表纯函数 (FR-054, FR-055)
 * 支持传入包含字段的响应对象或独立参数，返回规整后的提示项列表
 */
export function getDisplayNotices(firstArg, secondArg) {
  let unansweredFacts = null;
  let requestNotice = null;

  if (firstArg && typeof firstArg === "object" && !Array.isArray(firstArg)) {
    const source =
      firstArg.data && typeof firstArg.data === "object" ? firstArg.data : firstArg;
    unansweredFacts = source.unanswered_facts ?? source.unansweredFacts;
    requestNotice = source.request_notice ?? source.requestNotice ?? secondArg;
  } else if (Array.isArray(firstArg)) {
    unansweredFacts = firstArg;
    requestNotice = secondArg;
  } else if (typeof firstArg === "string") {
    requestNotice = firstArg;
    unansweredFacts = secondArg;
  } else {
    if (Array.isArray(secondArg)) {
      unansweredFacts = secondArg;
    } else if (typeof secondArg === "string") {
      requestNotice = secondArg;
    } else if (secondArg && typeof secondArg === "object") {
      unansweredFacts = secondArg.unanswered_facts ?? secondArg.unansweredFacts;
      requestNotice = secondArg.request_notice ?? secondArg.requestNotice;
    }
  }

  const list = [];

  if (typeof requestNotice === "string" && requestNotice.trim()) {
    const text = requestNotice.trim();
    list.push({
      type: "request",
      text,
      notice: text,
    });
  }

  if (Array.isArray(unansweredFacts)) {
    for (const item of unansweredFacts) {
      if (!item) continue;
      if (typeof item === "string" && item.trim()) {
        const text = item.trim();
        list.push({
          type: "unanswered_fact",
          name: "",
          text,
          notice: text,
        });
      } else if (typeof item === "object") {
        const text = (item.notice || item.text || "").trim();
        const name = (item.name || "").trim();
        if (text) {
          list.push({
            type: "unanswered_fact",
            name,
            text,
            notice: text,
          });
        }
      }
    }
  }

  return list;
}


export function formatUnansweredFactNotices(unansweredFacts) {
  if (!Array.isArray(unansweredFacts)) {
    return [];
  }
  const result = [];
  for (const item of unansweredFacts) {
    if (!item) continue;
    if (typeof item === "string" && item.trim()) {
      const name = item.trim();
      result.push(`HR 问了${name}，你的资料里没有，请把话术里的【填写：${name}】替换成实际情况`);
    } else if (typeof item === "object") {
      const name = (item.name || "").trim();
      if (name) {
        result.push(`HR 问了${name}，你的资料里没有，请把话术里的【填写：${name}】替换成实际情况`);
      } else {
        const text = (item.notice || item.text || "").trim();
        if (text) {
          result.push(text);
        }
      }
    }
  }
  return result;
}

/**
 * 将 dropped_summary 整理成待显示的说明列表纯函数 (FR-054)
 * 支持传入包含字段的响应对象或独立数组，返回规整后的说明列表
 */
export function getDisplayDroppedSummary(firstArg) {
  let droppedSummary = null;

  if (firstArg && typeof firstArg === "object" && !Array.isArray(firstArg)) {
    const source =
      firstArg.data && typeof firstArg.data === "object" ? firstArg.data : firstArg;
    droppedSummary = source.dropped_summary ?? source.droppedSummary;
  } else if (Array.isArray(firstArg)) {
    droppedSummary = firstArg;
  }

  if (!Array.isArray(droppedSummary) || droppedSummary.length === 0) {
    return [];
  }

  const list = [];
  for (const item of droppedSummary) {
    if (!item) continue;
    if (typeof item === "string" && item.trim()) {
      const text = item.trim();
      list.push({
        type: "dropped_summary",
        reason: "",
        category: "",
        count: 0,
        text,
        notice: text,
      });
    } else if (typeof item === "object") {
      const count =
        typeof item.count === "number"
          ? item.count
          : parseInt(item.count, 10) || 0;
      const reason = item.reason || "";
      const category = item.category || "";
      let text = (item.text || item.notice || "").trim();
      if (!text) {
        if (reason === "unsupported_fact") {
          text = `${count} 个版本因提到你资料里没有的'${category}'被去掉`;
        } else if (reason === "claims_done") {
          text = `${count} 个版本因声称'${category}'这类还没做的操作被去掉`;
        } else if (reason === "invalid_experience") {
          text = `${count} 个版本因引用了不存在的经历条目被去掉`;
        } else if (reason === "too_long") {
          text = `${count} 个版本因原文超过 50 字被去掉`;
        } else if (count > 0) {
          text = `${count} 个版本被去掉`;
        }
      }
      if (text) {
        list.push({
          type: "dropped_summary",
          reason,
          category,
          count,
          text,
          notice: text,
        });
      }
    }
  }

  return list;
}


/**
 * 自动生成相关的操作枚举 (FR-065, FR-066, FR-040)
 */
export const CHAT_AUTO_ACTIONS = {
  SHOW_CACHE: "show_cache",
  AUTO_GENERATE: "auto_generate",
  WAIT_MANUAL: "wait_manual",
  QUOTA_EXHAUSTED: "quota_exhausted",
  NOT_CHAT_PAGE: "not_chat_page",
};

/**
 * 计算单条消息的指纹纯函数 (FR-066, FR-036)
 * 组合 sender / is_self, body_type, text, time
 */
export function computeMessageFingerprint(message) {
  if (!message || typeof message !== "object") {
    return "no_message";
  }
  const isSelf = Boolean(message.is_self ?? message.isSelf);
  const sender = String(message.sender ?? (isSelf ? "我" : "HR")).trim();
  const bodyType = message.body_type ?? message.bodyType ?? "";
  const text = message.text != null ? String(message.text).trim() : "";
  const time = message.time != null ? String(message.time) : "";
  return `${sender}|${isSelf}|${bodyType}|${time}|${text}`;
}

/**
 * 计算聊天结果内存缓存键纯函数 (FR-066, FR-036)
 * 键 = 岗位 ID + 最后一条消息的指纹
 * 支持传入消息数组或单条消息对象
 */
export function computeChatCacheKey(encryptJobId, messagesOrLastMessage) {
  const jobId =
    typeof encryptJobId === "string"
      ? encryptJobId.trim()
      : encryptJobId
        ? String(encryptJobId).trim()
        : "";
  let lastMessage = null;
  if (Array.isArray(messagesOrLastMessage)) {
    lastMessage =
      messagesOrLastMessage.length > 0
        ? messagesOrLastMessage[messagesOrLastMessage.length - 1]
        : null;
  } else if (messagesOrLastMessage && typeof messagesOrLastMessage === "object") {
    lastMessage = messagesOrLastMessage;
  }
  const fp = computeMessageFingerprint(lastMessage);
  return `${jobId}|${fp}`;
}

/**
 * 获取本地日期字符串 YYYY-MM-DD (FR-040)
 */
export function getLocalDateString(date = new Date()) {
  const d = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(d.getTime())) {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * 判断指定日期是否为今天 (FR-040)
 */
export function isQuotaExhaustedToday(exhaustedDate, today = getLocalDateString()) {
  if (!exhaustedDate) return false;
  return String(exhaustedDate) === String(today);
}

/**
 * 切换聊天或打开侧边栏时的纯函数决策 (FR-065, FR-066, FR-040)
 * 决定：显示缓存 / 自动生成 / 等待手动 / 次数已用完不自动 / 非聊天页
 */
export function decideChatAutoAction(firstArg, maybeHasCached, maybeQuotaExhausted) {
  let opts = {};
  if (typeof firstArg === "object" && firstArg !== null) {
    opts = firstArg;
  } else {
    opts = {
      autoGenerate: firstArg !== false,
      hasCachedResult: Boolean(maybeHasCached),
      quotaExhaustedToday: Boolean(maybeQuotaExhausted),
    };
  }
  const isChatPage = opts.isChatPage !== false;
  const autoGenerate = opts.autoGenerate !== false;
  const hasCachedResult = Boolean(opts.hasCachedResult);
  const quotaExhaustedToday = Boolean(opts.quotaExhaustedToday);

  if (!isChatPage) {
    return {
      action: CHAT_AUTO_ACTIONS.NOT_CHAT_PAGE,
      message: "",
      shouldGenerate: false,
    };
  }
  if (!autoGenerate) {
    return {
      action: CHAT_AUTO_ACTIONS.WAIT_MANUAL,
      message: "点'生成'获取建议",
      shouldGenerate: false,
    };
  }
  if (hasCachedResult) {
    return {
      action: CHAT_AUTO_ACTIONS.SHOW_CACHE,
      message: "",
      shouldGenerate: false,
    };
  }
  if (quotaExhaustedToday) {
    return {
      action: CHAT_AUTO_ACTIONS.QUOTA_EXHAUSTED,
      message: "今日生成次数已用完",
      shouldGenerate: false,
    };
  }
  return {
    action: CHAT_AUTO_ACTIONS.AUTO_GENERATE,
    message: "",
    shouldGenerate: true,
  };
}

/**
 * 同意页说明文案常量 (FR-023, T072, T073)
 */
export const CONSENT_MODAL_DESC =
  "开启自动生成时，打开聊天就会自动发送（可以在设置页关闭自动生成）。";

/**
 * 校验 autoGenerate 设置值纯函数，缺省为 true (FR-020, FR-067)
 */
export function resolveAutoGenerateSetting(val) {
  return val !== false;
}

/**
 * 聊天切换重试间隔紧密序列（毫秒）(FR-005, E2 优化)
 * 每 200ms 重试一次，共 28 次，总等待上限 5600ms（5.6 秒）
 */
export const CHAT_SWITCH_RETRY_DELAYS = Array(28).fill(200);

/**
 * 决定聊天顶部变动后的重试与广播行为纯函数 (FR-005, T074, E2 优化)
 * @param {string|null|object} currentJobId 当前读到的岗位 ID，或包含参数的 options 对象
 * @param {string|null} [lastJobId] 上次绑定的岗位 ID
 * @param {number} [attemptIndex=0] 当前重试序号（0 开始）
 * @param {number[]} [retryDelays=CHAT_SWITCH_RETRY_DELAYS] 重试递增间隔配置
 * @returns {{
 *   shouldBroadcast: boolean,
 *   shouldRetry: boolean,
 *   nextDelayMs: number | null,
 *   jobId: string | null,
 *   isUnchanged: boolean,
 *   isReadFailed: boolean,
 *   exhausted?: boolean,
 * }}
 */
export function decideChatSwitchRetry(
  currentJobId,
  lastJobId,
  attemptIndex = 0,
  retryDelays = CHAT_SWITCH_RETRY_DELAYS
) {
  let current = currentJobId;
  let last = lastJobId;
  let attempt = attemptIndex;
  let delays = retryDelays;

  if (typeof currentJobId === "object" && currentJobId !== null) {
    current = currentJobId.currentJobId;
    last = currentJobId.lastJobId;
    attempt = currentJobId.attemptIndex ?? 0;
    delays = currentJobId.retryDelays ?? CHAT_SWITCH_RETRY_DELAYS;
  }

  const newId = typeof current === "string" && current.trim() ? current.trim() : null;
  const oldId = typeof last === "string" && last.trim() ? last.trim() : null;

  // 1. 读到不同的有效新 ID：立即广播，停止重试
  if (newId && newId !== oldId) {
    return {
      shouldBroadcast: true,
      shouldRetry: false,
      nextDelayMs: null,
      jobId: newId,
      isUnchanged: false,
      isReadFailed: false,
    };
  }

  // 2. 读到相同的 ID 或未读到 ID：按紧密间隔重试
  const list = Array.isArray(delays) ? delays : CHAT_SWITCH_RETRY_DELAYS;
  if (attempt < list.length) {
    return {
      shouldBroadcast: false,
      shouldRetry: true,
      nextDelayMs: list[attempt],
      jobId: null,
      isUnchanged: false,
      isReadFailed: false,
    };
  }

  // 3. 重试用尽：停止重试，不广播
  const isSame = Boolean(newId && oldId && newId === oldId);
  return {
    shouldBroadcast: false,
    shouldRetry: false,
    nextDelayMs: null,
    jobId: null,
    exhausted: true,
    isUnchanged: isSame,
    isReadFailed: !newId,
  };
}

/**
 * 侧边栏 5 秒轮询兜底比较纯函数 (FR-005, T074)
 * 显示中的岗位 ID 与当前读取到的岗位 ID 不同 → 需要切换，相同 → 不动
 * @param {string|null} displayedJobId 界面上正显示结果的岗位 ID (currentBoundJobId)
 * @param {string|null} currentJobId 当前聊天读取到的岗位 ID
 * @returns {{
 *   shouldSwitch: boolean,
 *   newJobId: string | null
 * }}
 */
export function decideChatFallbackSwitch(displayedJobId, currentJobId) {
  const displayed =
    typeof displayedJobId === "string" && displayedJobId.trim()
      ? displayedJobId.trim()
      : null;
  const current =
    typeof currentJobId === "string" && currentJobId.trim()
      ? currentJobId.trim()
      : null;

  if (displayed && current && displayed !== current) {
    return {
      shouldSwitch: true,
      newJobId: current,
    };
  }
  return {
    shouldSwitch: false,
    newJobId: null,
  };
}


/**
 * 复制被拒绝时的提示常量 (FR-005)
 */
export const COPY_REJECTED_NOTICE = "已切换到新的聊天，请重新生成";


/**
 * 打开我的岗位库标签页 (FR-062)
 * @param {object} [chromeApi] chrome API 对象（支持依赖注入）
 */
export function openMyJobsTab(chromeApi = (typeof chrome !== "undefined" ? chrome : undefined)) {
  if (chromeApi?.tabs?.create && chromeApi?.runtime?.getURL) {
    chromeApi.tabs.create({ url: chromeApi.runtime.getURL("src/myjobs.html") });
  }
}

/**
 * 侧边栏刷新时的聊天切换兜底检查纯逻辑/流程函数 (FR-005, T074)
 * 仅在当前处于聊天页且侧边栏正在显示某个岗位结果时执行检查；
 * 若显示中岗位 ID 与当前读到岗位 ID 不同，触发切换回调。
 *
 * @param {object} options
 * @param {boolean} options.isChat 是否为聊天页
 * @param {string|null} options.displayedJobId 当前展示中的岗位 ID（若无展示结果则为空）
 * @param {() => Promise<string|null>} options.readCurrentJobId 获取当前聊天岗位 ID 的异步函数
 * @param {(newJobId: string) => void|Promise<void>} options.onSwitch 发现切换时的回调
 * @returns {Promise<{ shouldSwitch: boolean, newJobId: string|null }>}
 */
export async function runChatFallbackCheck({
  isChat = false,
  displayedJobId = null,
  readCurrentJobId = null,
  onSwitch = null,
} = {}) {
  if (!isChat || !displayedJobId) {
    return { shouldSwitch: false, newJobId: null };
  }
  if (typeof readCurrentJobId !== "function") {
    return { shouldSwitch: false, newJobId: null };
  }

  try {
    const currentJobId = await readCurrentJobId();
    const fallback = decideChatFallbackSwitch(displayedJobId, currentJobId);
    if (fallback?.shouldSwitch && fallback?.newJobId) {
      if (typeof onSwitch === "function") {
        await onSwitch(fallback.newJobId);
      }
      return { shouldSwitch: true, newJobId: fallback.newJobId };
    }
  } catch {
    // 读取失败或抛错时不触发切换
  }

  return { shouldSwitch: false, newJobId: null };
}

/**
 * 岗位不在库提示原文常量 (FR-059, SC-018)
 */
export const NOT_IN_LIBRARY_NOTICE =
  "岗位库里没有这个岗位。在 BOSS 搜索列表里看到它时，Jet 会记录并判断";

/**
 * 岗位在库但尚未判断提示原文常量 (FR-015, US4)
 */
export const IN_LIBRARY_NO_JUDGEMENT_NOTICE = "点「查看职位」获取详情并判断";

/**
 * 判断岗位是否在库（与岗位库「全部岗位」定义一致：有状态 ∪ 判断过 ∪ 有非空 HR 记录 ∪ 在聊天中出现过）(FR-014, FR-056, FR-057)
 * 若仅存在于 jobs 表但不满足上述任一条件，按不在库处理。
 *
 * @param {object|null|undefined} jobEntry 来自 Jet 服务端 /v1/judgements 的岗位条目
 * @returns {boolean}
 */
export function isJobInLibrary(jobEntry) {
  if (!jobEntry || typeof jobEntry !== "object") return false;
  const hasStatus = Boolean(jobEntry.my_status && jobEntry.my_status.status);
  const hasJudgement = Boolean(jobEntry.judgement);
  const hasHrNote = typeof jobEntry.hr_note === "string" && jobEntry.hr_note.trim().length > 0;
  const seenInChat = Boolean(jobEntry.seen_in_chat);
  return hasStatus || hasJudgement || hasHrNote || seenInChat;
}

/**
 * 决定是否向本机 Jet 发起聊天岗位入库的纯函数 (FR-011, FR-017, T023, US4)
 * 条件：已配对且存在非空岗位 ID 与非空职位名。
 *
 * @param {object} [options={}]
 * @param {boolean} [options.isPaired=false] 是否已配对
 * @param {string|null|undefined} [options.jobId] 读取到的岗位 ID
 * @param {string|null|undefined} [options.title] 读取到的职位名称
 * @returns {boolean}
 */
export function shouldIngestChatJob({ isPaired = false, jobId = null, title = null } = {}) {
  if (!isPaired) return false;
  const trimmedJobId = typeof jobId === "string" ? jobId.trim() : "";
  const trimmedTitle = typeof title === "string" ? title.trim() : "";
  return Boolean(trimmedJobId && trimmedTitle);
}

/**
 * 格式化聊天页当前岗位展示数据纯函数 (FR-015, FR-056–FR-060, SC-016–SC-019)
 *
 * @param {string} jobId 当前聊天岗位 ID
 * @param {object|null|undefined} jobEntry 来自 Jet 服务端 /v1/judgements 的岗位条目
 * @returns {{
 *   platform_job_id: string,
 *   in_library: boolean,
 *   title: string,
 *   company_name: string,
 *   verdict_label: string|null,
 *   verdict_tone: string,
 *   summary_reason: string|null,
 *   hr_note: string|null,
 *   my_status: object|null,
 *   stale: boolean,
 *   judged_at: string|null,
 *   notice: string|null,
 *   resume_suggestion: object|null,
 * }}
 */
export function formatChatJobStatus(jobId, jobEntry) {
  const pid = typeof jobId === "string" ? jobId.trim() : "";
  const inLib = isJobInLibrary(jobEntry);

  if (!inLib) {
    return {
      platform_job_id: pid,
      in_library: false,
      title: (jobEntry && jobEntry.title) || pid,
      company_name: (jobEntry && jobEntry.company_name) || "",
      verdict_label: null,
      verdict_tone: "gray",
      summary_reason: null,
      hr_note: null,
      my_status: null,
      stale: false,
      judged_at: null,
      notice: NOT_IN_LIBRARY_NOTICE,
      resume_suggestion: null,
    };
  }

  const j = jobEntry.judgement || null;
  let vLabel = null;
  let vTone = "gray";
  let sReason = null;
  let isStale = false;
  let judgedAt = null;
  let notice = null;
  let resumeSuggestion = null;

  if (j) {
    const viewState = fromJudgement(j);
    const desc = describe(j, viewState);
    vLabel = desc.verdict_label || j.verdict_label || null;
    vTone = desc.verdict_tone || j.verdict_tone || "gray";
    sReason = desc.summary_reason || j.verdict_reason || j.summary_reason || null;
    isStale =
      viewState === "stale" ||
      Boolean(j.stale === true) ||
      Boolean(
        j.stale &&
          typeof j.stale === "object" &&
          (j.stale.job_changed || j.stale.profile_changed || j.stale.method_changed)
      );
    judgedAt = desc.judged_at || j.judged_at || j.created_at || null;
    if (shouldShowResumeSuggestion(j)) {
      resumeSuggestion = j.resume_suggestion;
    }
  } else {
    notice = IN_LIBRARY_NO_JUDGEMENT_NOTICE;
  }

  return {
    platform_job_id: pid,
    in_library: true,
    title: jobEntry.title || pid,
    company_name: jobEntry.company_name || "",
    verdict_label: vLabel,
    verdict_tone: vTone,
    summary_reason: sReason,
    hr_note: typeof jobEntry.hr_note === "string" ? jobEntry.hr_note : null,
    my_status: jobEntry.my_status || null,
    stale: isStale,
    judged_at: judgedAt,
    notice: notice,
    resume_suggestion: resumeSuggestion,
  };
}

/**
 * 聊天页侧边栏投递状态按钮选项 (FR-001, US1)
 */
export const CHAT_JOB_STATUS_ACTIONS = [
  { key: "saved", label: "收藏" },
  { key: "applied", label: "已投递" },
  { key: "skipped", label: "不考虑" },
];

/**
 * 提取岗位投递状态键名（"saved" | "applied" | "skipped" | null）
 *
 * @param {object|string|null|undefined} statusObjOrString
 * @returns {string|null}
 */
export function extractJobStatusKey(statusObjOrString) {
  if (!statusObjOrString) return null;
  if (typeof statusObjOrString === "object") {
    return statusObjOrString.status || null;
  }
  if (typeof statusObjOrString === "string") {
    const trimmed = statusObjOrString.trim();
    return trimmed || null;
  }
  return null;
}

/**
 * 计算下一投递状态纯函数 (FR-002, T005, US1)
 * 点击已选中的按钮取消状态（返回 null）；点击其他状态切换至该状态。
 *
 * @param {object|string|null|undefined} currentStatus 当前岗位状态（对象、字符串或 null）
 * @param {string|object|null|undefined} clickedKey 用户点击的目标状态
 * @returns {string|null}
 */
export function computeNextJobStatus(currentStatus, clickedKey) {
  const currentKey = extractJobStatusKey(currentStatus);
  const targetKey = extractJobStatusKey(clickedKey);
  if (!targetKey) return currentKey;
  return currentKey === targetKey ? null : targetKey;
}

/**
 * 计算侧边栏状态按钮组展示数据纯函数 (FR-001, T005, US1)
 *
 * @param {object|string|null|undefined} currentStatus 当前岗位状态
 * @param {object} [options={}]
 * @param {boolean} [options.disabled=false] 是否整体禁用（如保存中防重复提交）
 * @param {boolean} [options.saving=false] 是否正在保存中
 * @returns {{
 *   activeStatus: string|null,
 *   buttons: Array<{ key: string, label: string, active: boolean, disabled: boolean }>
 * }}
 */
export function getChatJobStatusButtons(currentStatus, options = {}) {
  const activeKey = extractJobStatusKey(currentStatus);
  const disabled = Boolean(options?.disabled || options?.saving);
  const buttons = CHAT_JOB_STATUS_ACTIONS.map((action) => {
    const isActive = action.key === activeKey;
    return {
      key: action.key,
      label: action.label,
      active: isActive,
      disabled,
    };
  });
  return {
    activeStatus: activeKey,
    buttons,
  };
}


/**
 * 判断异步响应是否对应当前激活的聊天岗位 ID（防乱序纯函数）
 * 仅当响应所属的 responseJobId 与当前正在查看的 currentJobId 相同时才采纳；否则判定为乱序/过期丢弃。
 *
 * @param {string|null|undefined} responseJobId 响应所属的岗位 ID
 * @param {string|null|undefined} currentJobId 当前处于激活状态的聊天岗位 ID
 * @returns {boolean}
 */
export function isChatJobResponseCurrent(responseJobId, currentJobId) {
  if (!responseJobId || !currentJobId) return false;
  return String(responseJobId).trim() === String(currentJobId).trim();
}

/**
 * 「简历已发送」的消息规则，与服务端 src/jet/llm/prompts/action_card_rules.json 里
 * marker 为 [已发送附件简历] / [HR 已查看附件简历] 的规则一一对应（只取消息类型和文字两项；
 * extension/tests/resume-sent-rules-sync.test.js 检查两边一致，改规则文件时要同步改这里）。
 * body_type 为 null 表示不限消息类型；text_contains 里的每一项都要出现（区分大小写）。
 */
export const RESUME_SENT_RULES = [
  { body_type: null, text_contains: ["已发送给Boss", "简历"] },
  { body_type: null, text_contains: ["对方已查看了您的附件简历"] },
  { body_type: 12, text_contains: ["简历"] },
  { body_type: 12, text_contains: [".pdf"] },
  { body_type: 12, text_contains: [".doc"] },
  { body_type: 12, text_contains: [".docx"] },
  { body_type: 12, text_contains: [".PDF"] },
  { body_type: 12, text_contains: [".DOC"] },
  { body_type: 12, text_contains: [".DOCX"] },
];

/**
 * 这条消息是否表示简历已经发出或已被 HR 查看（规则见 RESUME_SENT_RULES）。
 *
 * @param {object|null|undefined} message
 * @returns {boolean}
 */
export function isResumeSentMessage(message) {
  if (!message || typeof message !== "object") return false;
  const text = typeof message.text === "string" ? message.text : "";
  const bType = typeof message.body_type === "number" ? message.body_type : message.bodyType;
  return RESUME_SENT_RULES.some(
    (rule) =>
      (rule.body_type === null || rule.body_type === bType) &&
      rule.text_contains.every((part) => text.includes(part))
  );
}

/**
 * 判定聊天消息列表中是否存在 HR 发送的索要简历卡片 (FR-008, US3, T017)
 *
 * 判定规则：
 * 1. 寻找最后一张 HR 要简历卡片（body_type 或 bodyType 为 7、非本人、text 包含"简历"）；若不存在则返回 false；
 * 2. 在最后一张 HR 要简历卡片之后，若出现符合 RESUME_SENT_RULES 的消息（附件简历已发送、HR 已查看、
 *    自己发出的简历文件卡片），则视为已处理/已发送，返回 false；
 * 3. 否则返回 true。
 *
 * 交换微信/电话请求卡片、自己发的请求卡片、普通文字（bodyType 1）含"简历"一律返回 false。
 *
 * @param {Array|null|undefined} messages
 * @returns {boolean}
 */
export function hasHrResumeRequest(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return false;
  }
  let lastCardIdx = -1;
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || typeof m !== "object") continue;
    const bType = typeof m.body_type === "number" ? m.body_type : m.bodyType;
    if (bType !== 7) continue;
    const isSelf = Boolean(m.is_self || m.isSelf);
    if (isSelf) continue;
    const text = typeof m.text === "string" ? m.text : "";
    if (text.includes("简历")) {
      lastCardIdx = i;
    }
  }

  if (lastCardIdx === -1) {
    return false;
  }

  for (let i = lastCardIdx + 1; i < messages.length; i++) {
    if (isResumeSentMessage(messages[i])) {
      return false;
    }
  }

  return true;
}

/**
 * 决定侧边栏当前岗位卡片的简历突出提示状态纯函数 (FR-008, US3, T017, T019)
 *
 * 输入：（是否有 HR 简历请求、当前岗位的格式化状态）
 * 输出：
 * - "highlight"：有请求且当前岗位 resume_suggestion 非空（且包含有效简历名称），显示"HR 在要简历，建议投：<name>"及理由；
 * - "hint"：有请求、岗位已有判断但无建议（结论为"不建议投"除外，如 v5 旧判断、规则判断或暂无建议的判断），显示"点重新判断可获得简历建议"；
 * - "none"：无请求、未在岗位库、未判断、结论为"不建议投"等其他情况。
 *
 * @param {boolean} hasRequest 是否存在 HR 索要简历请求
 * @param {object|null|undefined} jobStatus 当前岗位格式化状态 (formatChatJobStatus 输出)
 * @returns {"highlight" | "hint" | "none"}
 */
export function decideResumeHighlightState(hasRequest, jobStatus) {
  if (!hasRequest || !jobStatus || typeof jobStatus !== "object") {
    return "none";
  }

  // 1. 有请求且当前岗位 resume_suggestion 非空（且包含有效简历名称）
  const suggestion = jobStatus.resume_suggestion;
  const hasSuggestion = Boolean(
    suggestion &&
      typeof suggestion === "object" &&
      typeof suggestion.name === "string" &&
      suggestion.name.trim().length > 0
  );
  if (hasSuggestion) {
    return "highlight";
  }

  // 2. 有请求、岗位已有判断但无建议（如 v5 旧判断等已有判断但无建议的情况）
  const hasJudgement = Boolean(
    jobStatus.in_library &&
      !jobStatus.notice &&
      ((jobStatus.verdict_label && jobStatus.verdict_label !== "未判断") || jobStatus.judged_at)
  );
  if (hasJudgement) {
    // 岗位结论为"不建议投"时，简历建议本就不显示，重新判断也不会改变，返回 "none"，不提示重新判断
    const isSkipVerdict =
      jobStatus.verdict_label === "不建议投" ||
      jobStatus.verdict === "skip" ||
      jobStatus.verdict === "unfit" ||
      jobStatus.view_state === "done_skip";
    if (isSkipVerdict) {
      return "none";
    }
    return "hint";
  }

  // 3. 其余情况（如未在岗位库、在库但未判断等）
  return "none";
}

/**
 * 会话切换时重置简历提示状态纯函数 (FR-008, US3, T018)
 *
 * 保证切换会话时旧提示被完全清除，绝不残留到新会话。
 *
 * @param {object} [prevState={}]
 * @returns {{ hasHrResumeRequest: boolean, resumeRequestJobId: null, highlightState: "none" }}
 */
export function resetResumePromptOnSwitch(prevState = {}) {
  return {
    ...prevState,
    hasHrResumeRequest: false,
    resumeRequestJobId: null,
    highlightState: "none",
  };
}

/**
 * 结合岗位一致性校验与突出状态决策纯函数 (FR-008, US3, T018)
 *
 * 规则：
 * - 只有核对当前会话岗位 ID 一致时才显示提示；
 * - 岗位 ID 不一致或切换会话时，清除提示（返回 none），不残留上一会话的提示。
 *
 * @param {object} params
 * @param {string|null|undefined} params.currentChatJobId 当前激活的会话岗位 ID
 * @param {string|null|undefined} params.requestJobId 读取到消息时的岗位 ID
 * @param {boolean} [params.hasHrResumeRequest=false] 是否存在 HR 要简历请求
 * @param {object|null|undefined} params.jobStatus 当前岗位格式化状态
 * @returns {{ state: "highlight" | "hint" | "none", hasHrResumeRequest: boolean, isCurrent: boolean }}
 */
export function decideChatResumePromptState({
  currentChatJobId,
  requestJobId,
  hasHrResumeRequest: hasRequest = false,
  jobStatus = null,
} = {}) {
  if (!isChatJobResponseCurrent(requestJobId, currentChatJobId)) {
    return {
      state: "none",
      hasHrResumeRequest: false,
      isCurrent: false,
    };
  }

  const state = decideResumeHighlightState(hasRequest, jobStatus);
  return {
    state,
    hasHrResumeRequest: Boolean(hasRequest),
    isCurrent: true,
  };
}

/**
 * 由岗位状态生成 HR 要简历的突出提示文字纯函数 (FR-008, US3)
 *
 * @param {object|null|undefined} jobStatus 当前岗位格式化状态 (formatChatJobStatus 输出)
 * @returns {string} 突出提示文字，显示"HR 在要简历，建议投：<name>"
 */
export function formatResumeHighlightText(jobStatus) {
  const resumeName = jobStatus?.resume_suggestion?.name || "";
  return `HR 在要简历，建议投：${resumeName}`;
}


/**
 * 侧边栏编辑中切换聊天时的安全切换调度器 (FR-058, T061)
 * 当用户正在编辑原岗位的 HR 实际情况且发生聊天切换时：
 * 必须先 flush 当前输入内容保存到原岗位（保存目标绝对是原岗位，带 source: 'chat_sidebar'），
 * flush 完成后才切换界面至新岗位，严禁将原岗位内容存入新岗位。
 *
 * @param {object} options
 * @param {object|null} [options.activeEditor] 当前正在编辑的上下文对象 { jobId, coordinator, getText }
 * @param {string|null} [options.newJobId] 即将切换到的新岗位 ID
 * @param {(newJobId: string|null) => Promise<void>|void} [options.onSwitch] 切换展示的回调
 * @returns {Promise<{ flushed: boolean, flushedJobId: string|null, switchedToJobId: string|null }>}
 */
export async function switchChatJobWithEditorFlush({
  activeEditor = null,
  newJobId = null,
  onSwitch = null,
} = {}) {
  let flushed = false;
  let flushedJobId = null;

  if (activeEditor && activeEditor.jobId && activeEditor.jobId !== newJobId) {
    flushedJobId = activeEditor.jobId;
    if (activeEditor.coordinator && typeof activeEditor.coordinator.flush === "function") {
      const text = typeof activeEditor.getText === "function" ? activeEditor.getText() : "";
      try {
        await activeEditor.coordinator.flush(text);
        flushed = true;
      } catch {
        // flush 失败亦允许继续切换，但不将内容带入新岗位
      }
    }
  }

  if (typeof onSwitch === "function") {
    await onSwitch(newJobId);
  }

  return {
    flushed,
    flushedJobId,
    switchedToJobId: newJobId,
  };
}

/**
 * 格式化查询失败时的岗位状态对象 (FR-059, SC-018)
 *
 * @param {string} jobId
 * @param {string} [viewState="jet_down"]
 * @returns {{
 *   platform_job_id: string,
 *   in_library: null,
 *   error: true,
 *   view_state: string,
 * }}
 */
export function formatChatJobErrorStatus(jobId, viewState = "jet_down") {
  return {
    platform_job_id: typeof jobId === "string" ? jobId.trim() : "",
    in_library: null,
    error: true,
    view_state: viewState || "jet_down",
  };
}

/**
 * 遍历已存在的 tabState，凡 chatJobStatus.platform_job_id 等于本次岗位 ID 的，
 * 用本次响应 jobEntry 重新格式化并替换 chatJobStatus（不得创建、不得调用 restoreTabStateIfNeeded / getOrCreateTabState）。
 *
 * @param {Map|Iterable<object>} tabStateMap
 * @param {string} platformJobId
 * @param {object|null|undefined} jobEntry
 * @returns {object|null} 格式化后的新 chatJobStatus
 */
export function syncChatJobStatusInTabStates(tabStateMap, platformJobId, jobEntry) {
  if (!tabStateMap || !platformJobId) return null;
  const newStatus = formatChatJobStatus(platformJobId, jobEntry);
  const states = typeof tabStateMap.values === "function" ? tabStateMap.values() : tabStateMap;
  for (const tabState of states) {
    if (
      tabState &&
      tabState.chatJobStatus &&
      tabState.chatJobStatus.platform_job_id === platformJobId
    ) {
      tabState.chatJobStatus = newStatus;
    }
  }
  return newStatus;
}

/**
 * 判断 URL 是否属于 BOSS 直聘页面（必须以 https://www.zhipin.com/ 开头）
 * @param {string|null|undefined} url
 * @returns {boolean}
 */
export function isBossUrl(url) {
  return typeof url === "string" && url.startsWith("https://www.zhipin.com/");
}

/**
 * 校验并提取可用的缓存聊天岗位状态纯函数 (阶段 E2 返工)
 * 只有当缓存存在且其 platform_job_id 与 currentJobId 一致时才返回缓存；
 * ID 不同返回 null、相同返回缓存、缓存为空返回 null。
 *
 * @param {object|null|undefined} cached 缓存的岗位状态对象
 * @param {string|null|undefined} currentJobId 当前聊天顶部绑定的岗位 ID
 * @returns {object|null}
 */
export function pickCachedChatJobStatus(cached, currentJobId) {
  if (!cached || !cached.platform_job_id) return null;
  if (!isChatJobResponseCurrent(cached.platform_job_id, currentJobId)) {
    return null;
  }
  return cached;
}

/**
 * 遮盖文案常量 (E2 优化)
 */
export const CHAT_MASK_TEXTS = {
  IDENTIFYING: "正在识别当前聊天…",
  QUERYING_JOB: "正在查询岗位库…",
  FAILED: "未能识别当前聊天的岗位，请稍后再试",
};

/**
 * 遮盖状态机状态常量 (E2 优化)
 */
export const CHAT_MASK_STATUS = {
  IDLE: "idle",
  CHANGING: "changing",
  SWITCHED: "switched",
  UNCHANGED: "unchanged",
  FAILED: "failed",
};

/**
 * 创建遮盖状态初始值纯函数
 */
export function createInitialMaskState(options = {}) {
  return {
    status: CHAT_MASK_STATUS.IDLE,
    isMasked: false,
    maskText: null,
    copyDisabled: false,
    currentJobId: options.currentJobId || null,
    currentBoundJobId: options.currentBoundJobId || null,
    currentResult: options.currentResult || null,
    preservedResult: null,
    preservedJobId: null,
    preservedBoundJobId: null,
    jobCardState: "idle",
    jobCardText: null,
    shouldRestoreResult: false,
  };
}

/**
 * 收到 changing 事件：立即遮盖且复制不可用纯函数 (FR-005, E2 优化)
 */
export function decideChatMaskOnChanging(currentState = {}) {
  const current = currentState || {};
  return {
    ...current,
    status: CHAT_MASK_STATUS.CHANGING,
    isMasked: true,
    maskText: CHAT_MASK_TEXTS.IDENTIFYING,
    copyDisabled: true,
    preservedResult: current.currentResult !== undefined ? current.currentResult : null,
    preservedJobId: current.currentJobId || null,
    preservedBoundJobId: current.currentBoundJobId || null,
    jobCardState: "identifying",
    jobCardText: CHAT_MASK_TEXTS.IDENTIFYING,
    shouldRestoreResult: false,
  };
}

/**
 * 收到 switched 事件：确认新 ID 后解除遮盖纯函数 (FR-005, E2 优化)
 */
export function decideChatMaskOnSwitched(currentState = {}, options = {}) {
  const current = currentState || {};
  const newJobId =
    typeof options === "object" && options !== null
      ? options.newJobId || options.jobId
      : options;
  return {
    ...current,
    status: CHAT_MASK_STATUS.SWITCHED,
    isMasked: false,
    maskText: null,
    copyDisabled: false,
    currentJobId: typeof newJobId === "string" && newJobId.trim() ? newJobId.trim() : null,
    currentBoundJobId: null,
    currentResult: null,
    preservedResult: null,
    preservedJobId: null,
    preservedBoundJobId: null,
    jobCardState: "querying",
    jobCardText: CHAT_MASK_TEXTS.QUERYING_JOB,
    shouldRestoreResult: false,
  };
}

/**
 * 收到 unchanged 事件：误报恢复原结果纯函数 (FR-005, E2 优化)
 */
export function decideChatMaskOnUnchanged(currentState = {}) {
  const current = currentState || {};
  const restoredResult =
    current.preservedResult !== undefined && current.preservedResult !== null
      ? current.preservedResult
      : current.currentResult || null;
  const restoredJobId = current.preservedJobId || current.currentJobId || null;
  const restoredBoundJobId = current.preservedBoundJobId || current.currentBoundJobId || null;

  return {
    ...current,
    status: CHAT_MASK_STATUS.UNCHANGED,
    isMasked: false,
    maskText: null,
    copyDisabled: false,
    currentResult: restoredResult,
    currentJobId: restoredJobId,
    currentBoundJobId: restoredBoundJobId,
    preservedResult: null,
    preservedJobId: null,
    preservedBoundJobId: null,
    jobCardState: "ready",
    jobCardText: null,
    shouldRestoreResult: true,
  };
}

/**
 * 收到 failed 事件（始终读不到 ID）：保持遮盖并显示失败文案纯函数 (FR-005, E2 优化)
 */
export function decideChatMaskOnFailed(currentState = {}) {
  const current = currentState || {};
  return {
    ...current,
    status: CHAT_MASK_STATUS.FAILED,
    isMasked: true,
    maskText: CHAT_MASK_TEXTS.FAILED,
    copyDisabled: true,
    currentResult: null, // 不得恢复旧聊天内容
    currentBoundJobId: null,
    preservedResult: null,
    preservedJobId: null,
    preservedBoundJobId: null,
    jobCardState: "failed",
    jobCardText: CHAT_MASK_TEXTS.FAILED,
    shouldRestoreResult: false,
  };
}

/**
 * 岗位库更新判定纯函数（过期的 chat_job_status_updated 被忽略）(FR-005, E2 优化)
 */
export function decideChatJobStatusUpdate({ currentChatJobId, updateJobId, jobStatus } = {}) {
  const current = typeof currentChatJobId === "string" ? currentChatJobId.trim() : null;
  const update = typeof updateJobId === "string" ? updateJobId.trim() : null;

  if (!current || !update || current !== update) {
    return {
      shouldApply: false,
      reason: "job_id_mismatch",
      jobStatus: null,
    };
  }

  return {
    shouldApply: true,
    reason: null,
    jobStatus: jobStatus || null,
  };
}

/**
 * 统一的遮盖状态 reducer 纯函数 (E2 优化)
 */
export function reduceChatMaskState(state, event) {
  const current = state || createInitialMaskState();
  const type = typeof event === "string" ? event : event?.type;
  switch (type) {
    case "changing":
    case "chat_top_changing":
      return decideChatMaskOnChanging(current);
    case "switched":
    case "chat_switched":
      return decideChatMaskOnSwitched(current, event);
    case "unchanged":
    case "chat_switch_unchanged":
      return decideChatMaskOnUnchanged(current);
    case "failed":
    case "chat_switch_failed":
      return decideChatMaskOnFailed(current);
    default:
      return current;
  }
}

/**
 * 遮盖开始时记录各按钮原始 disabled 状态的纯函数 (阶段 E2 优化)
 * 若当前已处于遮盖中（isAlreadyMasked=true 或 existingSaved 已存在），则保持第一次记录的原状态，严禁覆盖。
 *
 * @param {object|null} existingSaved 已记录的原始状态（若尚未记录则为 null/undefined）
 * @param {object} [currentStates={}] 当前各按钮的 disabled 状态
 * @param {boolean} [isAlreadyMasked=false] 是否已处于遮盖中
 * @returns {object} 应保存的原始状态对象
 */
export function recordMaskButtonStates(existingSaved, currentStates = {}, isAlreadyMasked = false) {
  let existing = null;
  let current = {};
  let masked = false;

  if (
    existingSaved &&
    typeof existingSaved === "object" &&
    ("existingStates" in existingSaved || "existingSaved" in existingSaved)
  ) {
    existing = existingSaved.existingStates ?? existingSaved.existingSaved ?? null;
    current = existingSaved.currentStates || {};
    masked = Boolean(existingSaved.isAlreadyMasked);
  } else {
    existing = existingSaved;
    current = currentStates || {};
    masked = Boolean(isAlreadyMasked);
  }

  if (existing) {
    return existing;
  }

  const result = {
    generateDisabled: Boolean(current.generateDisabled),
    switchModeDisabled: Boolean(current.switchModeDisabled),
    copyDisabled: Boolean(current.copyDisabled),
  };

  if (Array.isArray(current.copyDisabledList)) {
    result.copyDisabledList = [...current.copyDisabledList];
  } else if (Array.isArray(current.copyDisabled)) {
    result.copyDisabledList = [...current.copyDisabled];
  }

  return result;
}


/**
 * 决定解除遮盖后各按钮 disabled 状态的纯函数 (阶段 E2 优化)
 *
 * 规则：
 * 1. 生成按钮：以当时生成是否仍在进行为准：生成已结束则可用（false），仍在进行则保持禁用（true）
 * 2. 切换模式按钮：生成仍在进行则保持禁用（true），生成已结束则恢复为原状态或可用（false）
 * 3. 复制按钮：恢复为遮盖前记录的原始状态（原为 true 则保持 true，原为 false 则恢复 false）
 *
 * @param {object} [firstArg={}] 原始状态对象或包含 originalStates 与 isGenerating 的选项对象
 * @param {boolean} [maybeIsGenerating] 是否正在生成
 * @returns {{
 *   generateDisabled: boolean,
 *   switchModeDisabled: boolean,
 *   copyDisabled: boolean,
 *   copyDisabledList?: boolean[],
 * }}
 */
export function decideUnmaskedButtonStates(firstArg = {}, maybeIsGenerating) {
  let original = {};
  let isGenerating = false;

  if (typeof maybeIsGenerating === "boolean") {
    original = firstArg || {};
    isGenerating = maybeIsGenerating;
  } else if (typeof firstArg === "object" && firstArg !== null) {
    if ("originalStates" in firstArg) {
      original = firstArg.originalStates || {};
      isGenerating = Boolean(firstArg.isGenerating);
    } else {
      original = firstArg;
      isGenerating = Boolean(firstArg.isGenerating);
    }
  }

  const generateDisabled = Boolean(isGenerating);
  const switchModeDisabled = Boolean(isGenerating);

  let copyDisabled = false;
  if (typeof original.copyDisabled === "boolean") {
    copyDisabled = original.copyDisabled;
  } else if (Array.isArray(original.copyDisabledList) && original.copyDisabledList.length > 0) {
    copyDisabled = original.copyDisabledList.some(Boolean);
  } else if (Array.isArray(original.copyDisabled) && original.copyDisabled.length > 0) {
    copyDisabled = original.copyDisabled.some(Boolean);
  }

  const result = {
    generateDisabled,
    switchModeDisabled,
    copyDisabled,
  };

  if (Array.isArray(original.copyDisabledList)) {
    result.copyDisabledList = original.copyDisabledList.map(Boolean);
  } else if (Array.isArray(original.copyDisabled)) {
    result.copyDisabledList = original.copyDisabled.map(Boolean);
  }

  return result;
}


/**
 * 纯函数格式化判断配额文字
 * @param {object|number} quotaOrUsed 配额对象 { used, limit, remaining } 或已用次数
 * @param {number} [limit=0] 上限次数
 * @param {number} [remaining] 剩余次数
 * @returns {string}
 */
export function formatJudgeQuotaText(quotaOrUsed, limit, remaining) {
  let u, l, r;
  if (typeof quotaOrUsed === "object" && quotaOrUsed !== null) {
    u = quotaOrUsed.used ?? 0;
    l = quotaOrUsed.limit ?? 0;
    r = quotaOrUsed.remaining ?? Math.max(0, l - u);
  } else {
    u = quotaOrUsed ?? 0;
    l = limit ?? 0;
    r = remaining ?? Math.max(0, l - u);
  }
  return `判断：今日已用 ${u} / 上限 ${l}，剩余 ${r}`;
}

/**
 * 纯函数格式化沟通建议配额文字
 * @param {object|number} quotaOrUsed 配额对象 { used, limit, remaining } 或已用次数
 * @param {number} [limit=50] 上限次数
 * @param {number} [remaining] 剩余次数
 * @returns {string}
 */
export function formatAssistQuotaText(quotaOrUsed, limit, remaining) {
  let u, l, r;
  if (typeof quotaOrUsed === "object" && quotaOrUsed !== null) {
    l = quotaOrUsed.limit ?? 50;
    r = quotaOrUsed.remaining ?? (typeof quotaOrUsed.used === "number" ? Math.max(0, l - quotaOrUsed.used) : l);
    u = quotaOrUsed.used ?? Math.max(0, l - r);
  } else {
    l = limit ?? 50;
    r = remaining ?? (typeof quotaOrUsed === "number" ? Math.max(0, l - quotaOrUsed) : l);
    u = quotaOrUsed ?? Math.max(0, l - r);
  }
  return `沟通建议：今日已用 ${u} / 上限 ${l}，剩余 ${r}`;
}

/**
 * 生成后按 quota_remaining 计算新的已用与剩余纯函数
 * @param {number} quotaRemaining 生成成功响应返回的剩余额度
 * @param {number} [limit=50] 当前沟通建议上限
 * @returns {{ used: number, limit: number, remaining: number }}
 */
export function computeAssistQuotaAfterGenerate(quotaRemaining, limit = 50) {
  const lim = limit || 50;
  const rem = quotaRemaining;
  const used = Math.max(0, lim - rem);
  return { used, limit: lim, remaining: rem };
}


/**
 * 判断消息列表中最后一条消息是否由 HR 发送纯函数 (FR-074)
 * 与 computeMessageFingerprint 的发送方判定保持一致：
 * is_self/isSelf 为真即"我"；也兼容 sender 字段；空数组返回 false。
 *
 * @param {Array<object>|null|undefined} messages 消息列表
 * @returns {boolean}
 */
export function isLastMessageFromHr(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return false;
  }
  const lastMsg = messages[messages.length - 1];
  if (!lastMsg || typeof lastMsg !== "object") {
    return false;
  }
  const isSystem = Boolean(lastMsg.is_system ?? lastMsg.isSystem);
  if (isSystem) {
    return false;
  }
  const isSelf = Boolean(lastMsg.is_self ?? lastMsg.isSelf);
  if (isSelf) {
    return false;
  }
  const sender = lastMsg.sender != null ? String(lastMsg.sender).trim() : null;
  if (sender === "我" || sender === "系统") {
    return false;
  }
  if (sender === "HR") {
    return true;
  }
  // 未指定 sender 且 isSelf 为 false 时，按 computeMessageFingerprint 默认归为 HR
  return true;
}

/**
 * 决定新消息到达后的处理动作纯函数 (FR-074)
 *
 * @param {object} [options={}]
 * @param {boolean} [options.autoGenerate=true] 打开聊天自动生成开关状态
 * @param {boolean} [options.lastIsHr=false] 最新一条消息是否由 HR 发送
 * @param {boolean} [options.quotaExhaustedToday=false] 今日生成额度是否已用完
 * @returns {"generate" | "quota_exhausted" | "update_status" | "update_status_with_hint"}
 */
export function decideNewMessageAction(options = {}) {
  const autoGenerate = options.autoGenerate !== false;
  const lastIsHr = Boolean(options.lastIsHr);
  const quotaExhaustedToday = Boolean(options.quotaExhaustedToday);

  if (!autoGenerate) {
    return "update_status_with_hint";
  }

  if (lastIsHr) {
    if (quotaExhaustedToday) {
      return "quota_exhausted";
    }
    return "generate";
  }

  return "update_status";
}

/**
 * 自动生成开关关闭时新消息提示文案常量 (FR-074)
 */
export const NEW_MESSAGE_HINT = "有新消息 · 点「生成沟通建议」重新生成";

/**
 * 在"岗位 ID -> 该岗位最新一次结果的缓存键"映射上记录或更新缓存键纯函数 (FR-074)
 * 注意：对 Map 中已存在的键进行 set 不会改变键的顺序，因此通过 delete + set 保证顺序最新。
 *
 * @param {Map|object} mapping 岗位 ID 到缓存键的映射
 * @param {string} jobId 岗位 ID
 * @param {string} cacheKey 该岗位最新结果的缓存键
 */
export function recordLatestChatCacheKey(mapping, jobId, cacheKey) {
  if (!mapping || !jobId || !cacheKey) return;
  const normalizedJobId = String(jobId).trim();
  const normalizedCacheKey = String(cacheKey).trim();
  if (mapping instanceof Map) {
    mapping.delete(normalizedJobId);
    mapping.set(normalizedJobId, normalizedCacheKey);
  } else if (typeof mapping === "object") {
    mapping[normalizedJobId] = normalizedCacheKey;
  }
}

export const setLatestChatCacheKey = recordLatestChatCacheKey;

/**
 * 在"岗位 ID -> 该岗位最新一次结果的缓存键"映射上查询该岗位最新缓存键纯函数 (FR-074)
 * 保证拿到的是最新写入的那一条。
 *
 * @param {Map|object} mapping 岗位 ID 到缓存键的映射
 * @param {string} jobId 岗位 ID
 * @returns {string|null}
 */
export function getLatestChatCacheKey(mapping, jobId) {
  if (!mapping || !jobId) return null;
  const normalizedJobId = String(jobId).trim();
  if (mapping instanceof Map) {
    const val = mapping.get(normalizedJobId);
    if (Array.isArray(val)) {
      return val.length > 0 ? val[val.length - 1] : null;
    }
    return typeof val === "string" ? val : (val || null);
  }
  if (typeof mapping === "object") {
    const val = mapping[normalizedJobId];
    if (Array.isArray(val)) {
      return val.length > 0 ? val[val.length - 1] : null;
    }
    return typeof val === "string" ? val : (val || null);
  }
  return null;
}


/**
 * 决定查询聊天岗位状态时是否向服务端发起请求纯函数
 * 若指定 forceRefresh=true，则总是绕过缓存发起强制刷新；
 * 否则若缓存存在且 platform_job_id 与目标 jobId 一致，则使用缓存。
 *
 * @param {object} [options={}]
 * @param {boolean} [options.forceRefresh=false] 是否强制刷新
 * @param {object|null} [options.cachedStatus=null] 缓存的岗位状态对象
 * @param {string|null} [options.jobId=null] 目标岗位 ID
 * @returns {boolean} true: 发起请求; false: 使用缓存
 */
export function shouldFetchChatJobStatus(options = {}) {
  const forceRefresh = Boolean(options.forceRefresh);
  if (forceRefresh) return true;
  const cachedStatus = options.cachedStatus;
  const jobId = options.jobId;
  if (!cachedStatus || !cachedStatus.platform_job_id || !jobId) return true;
  return String(cachedStatus.platform_job_id).trim() !== String(jobId).trim();
}

/**
 * 判断是否应展示"这个岗位没有 Jet 判断"提示纯函数 (FR-011)
 * 仅当 has_jet_judgement 明确为 false 时展示；为 true 或未定义时隐藏。
 *
 * @param {object|boolean|null|undefined} dataOrFlag 结果对象或布尔值
 * @returns {boolean}
 */
export function shouldShowNoJudgementNotice(dataOrFlag) {
  if (dataOrFlag === false) return true;
  if (dataOrFlag && typeof dataOrFlag === "object") {
    return dataOrFlag.has_jet_judgement === false;
  }
  return false;
}

/**
 * 合并 preview 响应数据到已有建议结果对象的纯函数 (FR-011, FR-012, FR-074)
 * 保留原结果中的建议话术、问题、经历引用等字段，用 preview 最新结果更新
 * mode、mode_label、mode_basis 以及 has_jet_judgement 状态，并更新提示。
 *
 * @param {object|null} baseResult 原建议结果
 * @param {object|null} previewData preview 接口返回数据
 * @param {object} [chatData={}] 聊天数据
 * @param {object} [options={}]
 * @param {string} [options.action="update_status"] 操作类型 ("update_status" | "update_status_with_hint" | "quota_exhausted")
 * @returns {object}
 */
export function mergeChatPreviewResult(baseResult, previewData, chatData = {}, options = {}) {
  const jobId = (chatData && chatData.encrypt_job_id) || (baseResult && baseResult.bound_job_id) || null;
  const updatedResult = {
    ...(baseResult || {}),
    bound_job_id: jobId,
    company_name: (baseResult && baseResult.company_name) || (chatData && chatData.company_name) || "",
    job_title: (baseResult && baseResult.job_title) || (chatData && chatData.job_title) || "",
  };

  if (previewData) {
    if (previewData.mode !== undefined) {
      updatedResult.mode = previewData.mode;
    }
    if (previewData.mode_label !== undefined) {
      updatedResult.mode_label = previewData.mode_label;
    }
    if (previewData.mode_basis !== undefined) {
      updatedResult.mode_basis = previewData.mode_basis;
    }
    if (previewData.has_jet_judgement !== undefined) {
      updatedResult.has_jet_judgement = Boolean(previewData.has_jet_judgement);
    }
  }

  const action = options.action || "update_status";
  if (action === "update_status_with_hint") {
    updatedResult.new_message_hint = NEW_MESSAGE_HINT;
  } else {
    delete updatedResult.new_message_hint;
  }

  return updatedResult;
}

/**
 * 决定激活或切换聊天标签页时是否应刷新 preview 纯函数
 *
 * @param {object} [options={}]
 * @param {boolean} [options.isChat=false] 是否处于聊天页
 * @param {string|null} [options.currentChatJobId=null] 当前聊天岗位 ID
 * @param {object|null} [options.existingResult=null] 当前展示或已缓存的建议结果
 * @param {boolean} [options.isGenerating=false] 是否正在生成中
 * @param {boolean} [options.isMasked=false] 界面是否处于遮盖中
 * @returns {{ shouldRefresh: boolean, reason?: string }}
 */
export function decideChatPreviewRefresh(options = {}) {
  if (!options.isChat) {
    return { shouldRefresh: false, reason: "not_chat_page" };
  }
  if (options.isGenerating) {
    return { shouldRefresh: false, reason: "generating" };
  }
  if (options.isMasked) {
    return { shouldRefresh: false, reason: "masked" };
  }
  if (!options.currentChatJobId) {
    return { shouldRefresh: false, reason: "no_job_id" };
  }
  if (!options.existingResult) {
    return { shouldRefresh: false, reason: "no_existing_result" };
  }
  return { shouldRefresh: true };
}

/**
 * 构造用于 chat_preview 的载荷纯函数
 * forceMode 为 "opening" 或 "reply" 时返回 {...chatData, force_mode: forceMode}，
 * 否则原样返回 chatData（不加 force_mode 键）。
 *
 * @param {object} chatData 聊天数据
 * @param {string|null|undefined} forceMode 强制模式 ("opening" | "reply" | null)
 * @returns {object}
 */
export function buildChatPreviewPayload(chatData, forceMode) {
  if (forceMode === "opening" || forceMode === "reply") {
    return {
      ...chatData,
      force_mode: forceMode,
    };
  }
  return chatData;
}
