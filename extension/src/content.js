// 卡片固定在右下角而不是嵌进 BOSS 详情区域：BOSS 详情面板的 DOM 选择器未经验证，嵌入会依赖不稳定的结构。
// 列表标记覆盖层：在页面中查询 a[href*="/job_detail/"]（只读），从 href 中用正则 /\/job_detail\/([^/?#.]+)\.html/ 取岗位 ID；卡片元素取该链接最近的祖先 li（没有就用链接本身）。这个 DOM 结构未经真实页面验证，取不到就不显示任何列表标记（不报错）。

(function () {
  "use strict";

  // 注意：以下常量与 extension/src/taxonomy.js 保持一致（content.js 为经典脚本，无法 import）
  var CATEGORIES = {
    "数据与技术": ["数据分析", "数据处理与标注", "技术支持与实施", "开发与测试", "AI 相关"],
    "运营": ["用户运营", "内容运营", "产品运营", "活动运营", "电商运营", "新媒体与社群"],
    "产品与项目": ["产品经理或助理", "项目管理"],
    "内容与设计": ["编辑与文案", "设计与视频"],
    "市场与销售": ["市场营销与品牌", "销售与商务拓展", "客服与客户成功"],
    "科研与专业": ["实验与研发", "质检", "动物医学相关", "农牧生产"],
    "职能": ["行政", "人事", "财务"],
    "其他": [],
  };

  var WORK_INTENSITY = ["高强度", "单休", "大小周", "双休", "未提及"];

  var VERDICTS = ["apply", "try", "check", "skip"];

  var VERDICT_LABELS = {
    apply: "适合投递",
    try: "可以一试",
    check: "需要确认",
    skip: "不建议投",
  };

  var VERDICT_TONES = {
    apply: "green",
    try: "blue",
    check: "yellow",
    skip: "red",
  };

  var LEGACY_VERDICT_MAP = {
    fit: "apply",
    unsure: "check",
    unfit: "skip",
  };

  var SALES_LEVEL = ["高", "中", "低"];

  var EXPERIENCE_FIT = ["满足", "差一点", "不满足", "无法判断"];

  var LABEL_OPTIONS = {
    categories: CATEGORIES,
    work_intensity: WORK_INTENSITY,
    sales_level: SALES_LEVEL,
    experience_fit: EXPERIENCE_FIT,
    overall: [
      { value: "apply", label: "适合投递" },
      { value: "try", label: "可以一试" },
      { value: "check", label: "需要确认" },
      { value: "skip", label: "不建议投" },
    ],
  };

  // 注意：以下纯函数与 extension/src/view-state.js 的 shouldShowResumeSuggestion 保持一致（content.js 为经典脚本，无法 import）
  // BEGIN SYNC resume-suggestion
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
  function shouldShowResumeSuggestion(judgement) {
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
  // END SYNC resume-suggestion

  // 注意：以下纯函数与 extension/src/auto-apply.js 保持一致（content.js 为经典脚本，无法 import）
  // BEGIN SYNC auto-apply
  /**
   * 校验按钮 ka 属性是否与当前岗位 ID 匹配
   * ka 存在且不包含当前岗位 ID 时返回 false；ka 不存在时照常返回 true
   *
   * @param {string|null|undefined} ka 按钮的 ka 属性
   * @param {string|null|undefined} jobId 当前岗位 ID
   * @returns {boolean} 是否匹配
   */
  function kaMatchesJobId(ka, jobId) {
    if (ka === null || ka === undefined || ka === "") {
      return true;
    }
    if (typeof ka !== "string" || typeof jobId !== "string" || jobId.length === 0) {
      return false;
    }
    return ka.indexOf(jobId) !== -1;
  }

  /**
   * 从 /job_detail/<id>.html 解析岗位 ID 纯函数
   *
   * @param {string|null|undefined} url 页面路径或完整 URL
   * @returns {string|null} 岗位 ID，解析失败返回 null
   */
  function parseJobDetailUrlId(url) {
    if (typeof url !== "string") {
      return null;
    }
    const match = url.match(/\/job_detail\/([^/?#.]+)\.html/);
    return match && match[1] ? match[1].trim() : null;
  }

  /**
   * 决定点击时是否需要自动标记为「已投递」纯函数
   *
   * @param {object} params
   * @param {string} [params.pathname] 页面 location.pathname
   * @param {string} [params.buttonText] 按钮的文本内容
   * @param {string|null} [params.ka] 按钮的 ka 属性
   * @param {string|null} [params.currentJobId] 当前详情对应的岗位 ID
   * @param {string|null} [params.urlJobId] 独立职位页从地址解析出的岗位 ID
   * @param {string|object|null} [params.currentStatus] 当前岗位的状态
   * @param {boolean} [params.inOtherJobArea] 独立职位页是否处于其他岗位区域（如相似岗位、推荐岗位）
   * @returns {{ action: "mark" | "skip", reason: string }}
   */
  function decideAutoApply(params) {
    if (!params || typeof params !== "object") {
      return { action: "skip", reason: "missing_params" };
    }
    const pathname = params.pathname;
    const buttonText = params.buttonText;
    const ka = params.ka;
    const currentJobId = params.currentJobId;
    const urlJobId = params.urlJobId;
    const currentStatus = params.currentStatus;
    const inOtherJobArea = params.inOtherJobArea;

    // 1. 仅在搜索列表页（/web/geek/job 开头）与独立职位页（/job_detail/ 开头）生效
    if (
      typeof pathname !== "string" ||
      (!pathname.startsWith("/web/geek/job") && !pathname.startsWith("/job_detail/"))
    ) {
      return { action: "skip", reason: "not_search_list" };
    }

    // 2. 按钮文字必须恰好是「立即沟通」
    const text = typeof buttonText === "string" ? buttonText.trim() : "";
    if (text !== "立即沟通") {
      return { action: "skip", reason: "not_chat_button" };
    }

    // 3. 读取到的当前岗位 ID 必须存在
    const jobId = typeof currentJobId === "string" ? currentJobId.trim() : "";
    if (!jobId) {
      return { action: "skip", reason: "no_current_job_id" };
    }

    // 4. 独立职位页需核对地址中的岗位 ID，与读取到的当前岗位 ID 一致才标记
    if (pathname.startsWith("/job_detail/")) {
      if (Boolean(inOtherJobArea)) {
        return { action: "skip", reason: "other_job_area" };
      }
      const urlId = typeof urlJobId === "string" ? urlJobId.trim() : "";
      if (!urlId) {
        return { action: "skip", reason: "no_url_job_id" };
      }
      if (urlId !== jobId) {
        return { action: "skip", reason: "url_job_id_mismatch" };
      }
    }

    // 5. 核对 ka 属性：存在且不包含当前岗位 ID 时不标记
    if (!kaMatchesJobId(ka, jobId)) {
      return { action: "skip", reason: "ka_mismatch" };
    }

    // 6. 当前状态已经是「已投递」（applied）时不标记
    let statusKey = currentStatus;
    if (currentStatus && typeof currentStatus === "object" && typeof currentStatus.status === "string") {
      statusKey = currentStatus.status;
    }
    if (statusKey === "applied") {
      return { action: "skip", reason: "already_applied" };
    }

    return { action: "mark", reason: "ok" };
  }
  // END SYNC auto-apply

  // 注意：以下纯函数与 extension/src/marks-layout.js 保持一致（content.js 为经典脚本，无法 import）
  function jobIdFromHref(href) {
    if (typeof href !== "string") {
      return null;
    }
    var match = href.match(/\/job_detail\/([^/?#.]+)\.html/);
    return match ? match[1] : null;
  }

  function dedupeCards(cards) {
    if (!Array.isArray(cards)) {
      return [];
    }
    var byId = {};
    var order = [];
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      if (!card || !card.id || !card.rect || typeof card.rect !== "object") continue;

      var rWidth =
        typeof card.rect.width === "number"
          ? card.rect.width
          : typeof card.rect.right === "number" && typeof card.rect.left === "number"
          ? card.rect.right - card.rect.left
          : 0;
      var rHeight =
        typeof card.rect.height === "number"
          ? card.rect.height
          : typeof card.rect.bottom === "number" && typeof card.rect.top === "number"
          ? card.rect.bottom - card.rect.top
          : 0;
      var area = Math.max(0, rWidth) * Math.max(0, rHeight);

      if (!byId[card.id] || area > byId[card.id].area) {
        if (!byId[card.id]) {
          order.push(card.id);
        }
        byId[card.id] = { card: card, area: area };
      }
    }

    var result = [];
    for (var j = 0; j < order.length; j++) {
      result.push(byId[order[j]].card);
    }
    return result;
  }

  function visibleRange(rect, viewportHeight, isCardAt) {
    if (!rect || typeof rect !== "object" || typeof isCardAt !== "function") {
      return null;
    }
    var rTop = typeof rect.top === "number" ? rect.top : 0;
    var rHeight = typeof rect.height === "number" ? rect.height : 0;
    var rBottom =
      typeof rect.bottom === "number" ? rect.bottom : rTop + rHeight;
    var vpHeight =
      typeof viewportHeight === "number" ? viewportHeight : Infinity;

    var startY = Math.max(rTop, 0) + 1;
    var bottomLimit = Math.min(rBottom, vpHeight);
    if (startY >= bottomLimit) {
      return null;
    }

    var visibleTop = null;
    for (var y = startY; y < bottomLimit; y += 4) {
      if (isCardAt(y)) {
        visibleTop = y;
        break;
      }
    }

    if (visibleTop === null) {
      return null;
    }

    var height = bottomLimit - visibleTop;
    if (height <= 0) {
      return null;
    }

    return {
      top: visibleTop,
      bottom: bottomLimit,
      height: height,
      visibleTop: visibleTop,
      visibleBottom: bottomLimit,
    };
  }

  function planMarks(cards, listMarks, marksEnabled, viewport) {
    if (marksEnabled === false || !marksEnabled) {
      return [];
    }
    if (!Array.isArray(cards) || !listMarks || typeof listMarks !== "object") {
      return [];
    }

    function estimateLabelWidth(text, fontSize) {
      if (!text || typeof text !== "string") return 0;
      var fs = typeof fontSize === "number" ? fontSize : 11;
      var w = 0;
      for (var k = 0; k < text.length; k++) {
        var code = text.charCodeAt(k);
        if (code <= 255) {
          w += fs * 0.5;
        } else {
          w += fs;
        }
      }
      return Math.ceil(w) + 10;
    }

    var dedupedCards = dedupeCards(cards);

    var validCards = [];
    for (var i = 0; i < dedupedCards.length; i++) {
      var card = dedupedCards[i];
      if (!card || !card.id || !card.rect || typeof card.rect !== "object") continue;

      var mark = listMarks[card.id];
      if (!mark) continue;

      var rect = card.rect;
      var rLeft = typeof rect.left === "number" ? rect.left : 0;
      var rTop = typeof rect.top === "number" ? rect.top : 0;
      var rRight =
        typeof rect.right === "number"
          ? rect.right
          : rLeft + (typeof rect.width === "number" ? rect.width : 0);
      var rBottom =
        typeof rect.bottom === "number"
          ? rect.bottom
          : rTop + (typeof rect.height === "number" ? rect.height : 0);
      var rWidth = typeof rect.width === "number" ? rect.width : (rRight - rLeft);
      var rHeight = typeof rect.height === "number" ? rect.height : (rBottom - rTop);

      if (viewport && typeof viewport === "object") {
        var vpWidth = typeof viewport.width === "number" ? viewport.width : Infinity;
        var vpHeight = typeof viewport.height === "number" ? viewport.height : Infinity;
        var isOutside =
          rBottom <= 0 || rTop >= vpHeight || rRight <= 0 || rLeft >= vpWidth;
        if (isOutside) {
          continue;
        }
      }

      validCards.push({
        id: card.id,
        mark: mark,
        rect: {
          left: rLeft,
          top: rTop,
          right: rRight,
          bottom: rBottom,
          width: rWidth,
          height: rHeight,
        },
        titleTop: card.titleTop !== undefined ? card.titleTop : null,
        visibleRange: card.visibleRange || null,
        labelAllowed: card.labelAllowed !== undefined ? card.labelAllowed : true,
      });
    }

    validCards.sort(function (a, b) {
      return a.rect.top - b.rect.top;
    });

    var planned = [];
    for (var j = 0; j < validCards.length; j++) {
      var item = validCards[j];
      var cRect = item.rect;
      var cMark = item.mark;
      var stale = Boolean(cMark.stale);
      var rawLabel = cMark.verdict_label || cMark.label || "";
      var text = rawLabel;
      if (stale) {
        text = rawLabel ? (rawLabel + " · 过时") : "过时";
      }
      var tone = cMark.verdict_tone || cMark.tone || "neutral";

      // 标签高度、字号与留白计算（004 规则完全不变）
      var labelHeight = 14;
      var labelFontSize = 11;
      var labelTop = cRect.top;
      var labelVisible = false;

      if (item.titleTop !== null && typeof item.titleTop === "number") {
        var space = item.titleTop - cRect.top;
        if (space >= 14) {
          labelHeight = 14;
          labelFontSize = 11;
          labelTop = cRect.top + Math.floor((space - 14) / 2);
          labelVisible = true;
        } else if (space >= 12) {
          labelHeight = 12;
          labelFontSize = 10;
          labelTop = cRect.top + Math.floor((space - 12) / 2);
          labelVisible = true;
        } else {
          // space < 12：不显示标签
          labelHeight = 12;
          labelFontSize = 10;
          labelTop = cRect.top;
          labelVisible = false;
        }
      }

      // 标签只在卡片顶部可见时显示：visibleTop 与 cardRect.top 相差 ≤ 2px 才显示；
      // 卡片顶部被导航栏等盖住时不显示（随卡片一起被盖住）。
      if (item.visibleRange && Math.abs(item.visibleRange.top - cRect.top) > 2) {
        labelVisible = false;
      }
      if (item.labelAllowed === false) {
        labelVisible = false;
      }

      // ① 左侧外沿色条：宽 4px、与卡片同高（或可见范围同高），left = rect.left - 6（若 rect.left - 6 < 0 则 left = 0、宽 2px）
      // 仅有状态的卡片不新增左侧色条（FR-005, data-model 列表标记部分）
      var isPrejudge = Boolean(cMark.is_prejudge);
      var reason = cMark.reason || "";
      var tooltipText = isPrejudge && reason ? reason : text;

      var hasStrip = Boolean(rawLabel || cMark.is_hint || isPrejudge || cMark.judged_at);
      var strip = null;
      if (hasStrip) {
        var stripLeft = cRect.left - 6;
        var stripWidth = 4;
        if (stripLeft < 0) {
          stripLeft = 0;
          stripWidth = 2;
        }

        var stripTop = item.visibleRange ? item.visibleRange.top : cRect.top;
        var stripHeight = item.visibleRange ? item.visibleRange.height : cRect.height;

        strip = {
          left: stripLeft,
          top: stripTop,
          height: stripHeight,
          width: stripWidth,
          pointerEvents: labelVisible ? "none" : "auto",
          title: labelVisible ? "" : tooltipText,
        };
      }

      // ② 结论/粗筛标签：放在卡片边框内的左上角、职位名上方的留白里（004 原位置与宽度不动）
      // left = cardRect.left + 8；maxWidth = cardRect.width - 16
      var labelLeft = cRect.left + 8;
      var labelMaxWidth = cRect.width - 16;

      var label = {
        left: labelLeft,
        top: labelTop,
        height: labelHeight,
        fontSize: labelFontSize,
        maxWidth: labelMaxWidth,
        visible: Boolean(rawLabel && labelVisible),
        title: tooltipText,
      };

      // ③ 投递状态标识：
      // - 已有结论标签或粗筛提示原位置不动；状态标识排在其右侧（间距 4px）；
      // - 卡片没有结论/提示标签时，状态标识放在原标签位置（left = rect.left + 8）；
      // - 空间不足时截断状态标识文字，不得覆盖已有标签。
      var statusKey = cMark.status_key || null;
      var statusLabel = cMark.status_label || null;
      var statusTone = cMark.status_tone || statusKey;
      var statusBadge = null;

      if (statusKey && statusLabel) {
        var badgeLeft = cRect.left + 8;
        var badgeMaxWidth = Math.max(0, cRect.width - 16);

        if (rawLabel) {
          var verdictWidth = Math.min(estimateLabelWidth(text, labelFontSize), labelMaxWidth);
          badgeLeft = labelLeft + verdictWidth + 4;
          var maxRight = cRect.left + cRect.width - 8;
          badgeMaxWidth = Math.max(0, maxRight - badgeLeft);
        }

        var badgeVisible = Boolean(labelVisible && badgeMaxWidth > 0);

        statusBadge = {
          left: badgeLeft,
          top: labelTop,
          height: labelHeight,
          fontSize: labelFontSize,
          maxWidth: badgeMaxWidth,
          visible: badgeVisible,
          text: statusLabel,
          tone: statusTone,
          title: statusLabel,
        };
      }

      planned.push({
        id: item.id,
        text: text,
        tone: tone,
        stale: stale,
        is_prejudge: isPrejudge,
        reason: reason,
        strip: strip,
        label: label,
        status_badge: statusBadge,
      });
    }

    return planned;
  }

  function computeStatusBadgeLayout(label, actualWidth) {
    var labelLeft = label && typeof label.left === "number" ? label.left : 0;
    var labelMaxWidth = label && typeof label.maxWidth === "number" ? label.maxWidth : 0;
    var actWidth = typeof actualWidth === "number" ? actualWidth : 0;

    var left = labelLeft + actWidth + 4;
    var maxRight = (labelLeft - 8) + (labelMaxWidth + 16) - 8;
    var availableWidth = maxRight - left;
    var visible = availableWidth > 0;
    var maxWidth = Math.max(0, availableWidth);

    return {
      left: left,
      maxWidth: maxWidth,
      visible: visible,
    };
  }

  function findCardElementForLink(link, jobId) {
    var current = link;
    var bestLi = null;
    while (current && current !== document.body && current !== document.documentElement) {
      if (current.tagName && current.tagName.toLowerCase() === "li") {
        var innerLinks = current.querySelectorAll('a[href*="/job_detail/"]');
        var onlySameJob = true;
        for (var k = 0; k < innerLinks.length; k++) {
          var innerHref = innerLinks[k].getAttribute("href") || innerLinks[k].href || "";
          var innerId = jobIdFromHref(innerHref);
          if (innerId && innerId !== jobId) {
            onlySameJob = false;
            break;
          }
        }
        if (onlySameJob) {
          bestLi = current;
        } else {
          break;
        }
      }
      current = current.parentElement;
    }
    return bestLi || link;
  }

  function findTitleTop(cardEl) {
    if (!cardEl) return null;
    try {
      if (cardEl.matches && cardEl.matches('a[href*="/job_detail/"]') && (cardEl.textContent || "").trim().length > 0) {
        var selfRect = cardEl.getBoundingClientRect();
        if (selfRect && typeof selfRect.top === "number") {
          return selfRect.top;
        }
      }
      if (typeof cardEl.querySelectorAll === "function") {
        var links = cardEl.querySelectorAll('a[href*="/job_detail/"]');
        for (var i = 0; i < links.length; i++) {
          var link = links[i];
          if (link && (link.textContent || "").trim().length > 0) {
            var r = link.getBoundingClientRect();
            if (r && typeof r.top === "number") {
              return r.top;
            }
          }
        }

        var all = cardEl.querySelectorAll("*");
        for (var j = 0; j < all.length; j++) {
          var el = all[j];
          if (el && (el.textContent || "").trim().length > 0) {
            var elRect = el.getBoundingClientRect();
            if (elRect && typeof elRect.top === "number") {
              return elRect.top;
            }
          }
        }
      }
    } catch (e) {}

    return null;
  }

  function assembleLabelPayload(selection) {
    if (!selection || typeof selection !== "object") {
      return { ok: false, error: "请至少选择一项" };
    }

    var work_type =
      typeof selection.work_type === "string" &&
      Object.prototype.hasOwnProperty.call(CATEGORIES, selection.work_type)
        ? selection.work_type
        : null;

    var work_subtype = null;
    if (
      work_type !== null &&
      typeof selection.work_subtype === "string" &&
      Array.isArray(CATEGORIES[work_type]) &&
      CATEGORIES[work_type].indexOf(selection.work_subtype) !== -1
    ) {
      work_subtype = selection.work_subtype;
    }

    var secondary_work_types = null;
    if (Array.isArray(selection.secondary_work_types)) {
      var validSec = [];
      for (var sIdx = 0; sIdx < selection.secondary_work_types.length; sIdx++) {
        var sItem = selection.secondary_work_types[sIdx];
        if (sItem && typeof sItem === "object") {
          var sCat = sItem.category;
          var sSub = sItem.subtype || null;
          if (
            typeof sCat === "string" &&
            Object.prototype.hasOwnProperty.call(CATEGORIES, sCat)
          ) {
            var subValid =
              sSub === null ||
              (Array.isArray(CATEGORIES[sCat]) && CATEGORIES[sCat].indexOf(sSub) !== -1);
            if (subValid) {
              var isSameAsPrim =
                work_type !== null &&
                sCat === work_type &&
                (work_subtype || null) === sSub;
              if (!isSameAsPrim) {
                var alreadyHas = false;
                for (var vIdx = 0; vIdx < validSec.length; vIdx++) {
                  if (
                    validSec[vIdx].category === sCat &&
                    (validSec[vIdx].subtype || null) === sSub
                  ) {
                    alreadyHas = true;
                    break;
                  }
                }
                if (!alreadyHas) {
                  validSec.push({ category: sCat, subtype: sSub });
                  if (validSec.length === 3) break;
                }
              }
            }
          }
        }
      }
      if (validSec.length > 0) {
        secondary_work_types = validSec;
      }
    }

    var sales_level =
      typeof selection.sales_level === "string" &&
      SALES_LEVEL.indexOf(selection.sales_level) !== -1
        ? selection.sales_level
        : null;

    var experience_fit =
      typeof selection.experience_fit === "string" &&
      EXPERIENCE_FIT.indexOf(selection.experience_fit) !== -1
        ? selection.experience_fit
        : null;

    var work_intensity =
      typeof selection.work_intensity === "string" &&
      WORK_INTENSITY.indexOf(selection.work_intensity) !== -1
        ? selection.work_intensity
        : null;

    var overall = null;
    if (typeof selection.overall === "string") {
      var oVal = selection.overall;
      if (LEGACY_VERDICT_MAP[oVal]) {
        oVal = LEGACY_VERDICT_MAP[oVal];
      }
      if (VERDICTS.indexOf(oVal) !== -1) {
        overall = oVal;
      }
    }

    var note = null;
    if (typeof selection.note === "string") {
      var trimmed = selection.note.trim();
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
        work_type: work_type,
        work_subtype: work_subtype,
        secondary_work_types: secondary_work_types,
        sales_level: sales_level,
        experience_fit: experience_fit,
        work_intensity: work_intensity,
        overall: overall,
        note: note,
      },
    };
  }

  function formatJudgedAt(rawDate) {
    if (!rawDate) return "";
    try {
      var d = new Date(rawDate);
      if (isNaN(d.getTime())) return String(rawDate);
      var pad = function (n) { return String(n).padStart(2, "0"); };
      var month = pad(d.getMonth() + 1);
      var day = pad(d.getDate());
      var hours = pad(d.getHours());
      var minutes = pad(d.getMinutes());
      return month + "-" + day + " " + hours + ":" + minutes;
    } catch (e) {
      return String(rawDate);
    }
  }

  // 插件被重新加载后，旧内容脚本的 chrome.runtime 会失效；此时静默停止，不影响页面。
  function notify(message) {
    try {
      var p = chrome.runtime.sendMessage(message);
      if (p && typeof p.catch === "function") p.catch(function () {});
    } catch (e) {
      // Extension context invalidated
    }
  }

  var currentHost = null;
  var currentShadow = null;
  var currentMarksHost = null;
  var currentMarksShadow = null;
  var currentListMarks = {};
  var rafPending = false;
  var debounceTimer = null;
  var maxWaitTimer = null;
  var firstMutationAt = null;
  var secTipTimer = null;

  var currentDetail = null;
  var currentMarksEnabled = true;
  var lastJobId = null;
  var savedCardScrollTop = 0;
  var isCardCollapsed = false;
  var isFactsExpanded = false;
  var currentCardSide = "right";
  var isAnnotationExpanded = false;
  var isSecondaryExpanded = false;
  var hasUnsavedAnnotation = false;
  var justSavedAnnotation = false;
  var annotationSelection = {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };
  var annotationStatusText = null;
  var isResumeReasonExpanded = false;
  var isHrNoteEditing = false;
  var hrNoteDraft = "";
  // 编辑 HR 实际情况期间，同一岗位的后台刷新先暂存，结束编辑后再应用（否则编辑框会被重建：
  // 第一次点"保存 / 清空"时后台先推送刷新、再回复结果，结果写到了已被替换的旧编辑框上，于是要点两次）
  var pendingRender = null;
  var focusHrInput = false;

  var hrNoteLastSaved = "";
  var hrNoteCoordinator = null;
  var activeCardRefreshStatus = null;
  var autoApplyToastEl = null;
  var autoApplyToastTimer = null;

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
function decideHrNoteAutoSave(text, lastSaved) {
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
function createAutoSaveCoordinator(options = {}) {
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

  // 在当前页面上的那一块重新渲染（不要用闭包里捕获的旧容器，它可能已被替换）
  function refreshHrNoteSection() {
    var live = currentShadow ? currentShadow.querySelector(".jet-hr-note-row") : null;
    if (live) {
      renderHrNoteSection(live);
    } else {
      buildCardDom();
    }
  }

  function startHrNoteEditing() {
    isHrNoteEditing = true;
    hrNoteLastSaved =
      currentDetail && typeof currentDetail.hr_note === "string" ? currentDetail.hr_note.trim() : "";
    hrNoteDraft = hrNoteLastSaved;
    focusHrInput = true;
    refreshHrNoteSection();
  }

  // 结束编辑：updateNote 为 true 时用 noteValue 更新已保存内容；有暂存的刷新则应用它
  function finishHrNoteEditing(noteValue, updateNote) {
    if (hrNoteCoordinator) {
      hrNoteCoordinator.cancel();
    }
    isHrNoteEditing = false;
    hrNoteDraft = "";
    focusHrInput = false;
    if (updateNote && currentDetail) {
      currentDetail.hr_note = noteValue;
    }
    var pending = pendingRender;
    pendingRender = null;
    if (pending && pending.detail) {
      if (updateNote && currentDetail && pending.detail.platform_job_id === currentDetail.platform_job_id) {
        pending.detail.hr_note = noteValue;
      }
      renderDetailCard(pending.detail, pending.marksEnabled);
      updateMarksPositions();
    } else {
      refreshHrNoteSection();
    }
  }

  function renderHrNoteSection(container) {
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }

    if (!isHrNoteEditing) {
      var hasNote =
        currentDetail &&
        typeof currentDetail.hr_note === "string" &&
        currentDetail.hr_note.trim().length > 0;

      if (hasNote) {
        var labelSpan = document.createElement("span");
        labelSpan.className = "jet-hr-note-label";
        labelSpan.textContent = "HR 说的实际情况：";
        container.appendChild(labelSpan);

        var noteSpan = document.createElement("span");
        noteSpan.className = "jet-hr-note-text";
        noteSpan.textContent = currentDetail.hr_note.trim();
        container.appendChild(noteSpan);

        var editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "jet-hr-note-link";
        editBtn.textContent = "修改";
        editBtn.addEventListener("click", function () {
          startHrNoteEditing();
        });
        container.appendChild(editBtn);
      } else {
        var addBtn = document.createElement("button");
        addBtn.type = "button";
        addBtn.className = "jet-hr-note-add-btn";
        addBtn.textContent = "＋ 记录 HR 说的实际情况";
        addBtn.addEventListener("click", function () {
          startHrNoteEditing();
        });
        container.appendChild(addBtn);
      }
    } else {
      var editBox = document.createElement("div");
      editBox.className = "jet-hr-note-edit-box";

      var inputEl = document.createElement("input");
      inputEl.type = "text";
      inputEl.maxLength = 200;
      inputEl.className = "jet-hr-note-input";
      inputEl.placeholder = "HR 说的实际情况（≤ 200 字）";
      inputEl.value = hrNoteDraft;

      var actionsDiv = document.createElement("div");
      actionsDiv.className = "jet-hr-note-actions";

      var clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "jet-btn-sm danger";
      clearBtn.textContent = "清空";

      var doneBtn = document.createElement("button");
      doneBtn.type = "button";
      doneBtn.className = "jet-btn-sm primary";
      doneBtn.textContent = "完成";

      var msgEl = document.createElement("div");
      msgEl.className = "jet-hr-note-msg";

      var targetJobId = currentDetail ? currentDetail.platform_job_id : null;

      hrNoteCoordinator = createAutoSaveCoordinator({
        delayMs: 1000,
        initialSaved: hrNoteLastSaved || null,
        emptyMessage: "内容为空不会自动保存；要清空请点「清空」",
        save: function (noteToSave) {
          return new Promise(function (resolve) {
            try {
              chrome.runtime.sendMessage(
                {
                  type: "save_hr_note",
                  platform_job_id: targetJobId,
                  note: noteToSave,
                  source: "card",
                },
                function (res) {
                  if (chrome.runtime?.lastError) {
                    resolve({
                      ok: false,
                      error: chrome.runtime.lastError.message || "保存异常",
                    });
                    return;
                  }
                  resolve(res || { ok: false, error: "empty_response" });
                }
              );
            } catch (e) {
              resolve({ ok: false, error: e?.message || "保存异常" });
            }
          });
        },
        onStatusChange: function (evt) {
          if (evt.status === "empty") {
            msgEl.textContent = evt.message;
          } else if (evt.status === "saving") {
            msgEl.textContent = "保存中...";
          } else if (evt.status === "saved") {
            msgEl.textContent = "已自动保存";
          } else if (evt.status === "error") {
            msgEl.textContent = "保存失败" + (evt.message && evt.message !== "保存失败" ? "（" + evt.message + "）" : "");
          }
        },
        onSaveSuccess: function (savedVal) {
          hrNoteLastSaved = savedVal;
          if (currentDetail && currentDetail.platform_job_id === targetJobId) {
            currentDetail.hr_note = savedVal;
          }
          if (pendingRender && pendingRender.detail && pendingRender.detail.platform_job_id === targetJobId) {
            pendingRender.detail.hr_note = savedVal;
          }
        },
      });

      inputEl.addEventListener("input", function () {
        hrNoteDraft = inputEl.value;
        if (inputEl.value.trim().length > 0 && msgEl.textContent === "内容为空不会自动保存；要清空请点「清空」") {
          msgEl.textContent = "";
        }
        hrNoteCoordinator.onInput(inputEl.value);
      });

      inputEl.addEventListener("blur", function () {
        hrNoteCoordinator.onBlur(inputEl.value);
      });

      clearBtn.addEventListener("click", function () {
        if (hrNoteCoordinator) {
          hrNoteCoordinator.cancel();
        }

        clearBtn.disabled = true;
        doneBtn.disabled = true;
        clearBtn.textContent = "清空中...";
        msgEl.textContent = "";

        try {
          chrome.runtime.sendMessage(
            {
              type: "save_hr_note",
              platform_job_id: currentDetail.platform_job_id,
              note: null,
              source: "card",
            },
            function (res) {
              if (res && res.ok) {
                hrNoteLastSaved = "";
                if (hrNoteCoordinator) {
                  hrNoteCoordinator.setLastSaved(null);
                }
                finishHrNoteEditing(null, true);
              } else {
                clearBtn.disabled = false;
                doneBtn.disabled = false;
                clearBtn.textContent = "清空";
                msgEl.textContent = "清空失败" + (res && res.error ? "（" + res.error + "）" : "");
              }
            }
          );
        } catch (e) {
          clearBtn.disabled = false;
          doneBtn.disabled = false;
          clearBtn.textContent = "清空";
          msgEl.textContent = "清空异常";
        }
      });

      doneBtn.addEventListener("click", function () {
        clearBtn.disabled = true;
        doneBtn.disabled = true;

        hrNoteCoordinator.flush(inputEl.value).then(function (res) {
          if (res.ok) {
            finishHrNoteEditing(res.note, res.saved);
          } else {
            clearBtn.disabled = false;
            doneBtn.disabled = false;
            msgEl.textContent = res.message || res.error || "保存失败";
          }
        }).catch(function (err) {
          clearBtn.disabled = false;
          doneBtn.disabled = false;
          msgEl.textContent = err?.message || "保存异常";
        });
      });

      if (focusHrInput) {
        focusHrInput = false;
        setTimeout(function () {
          try {
            inputEl.focus();
            var end = inputEl.value.length;
            inputEl.setSelectionRange(end, end);
          } catch (e) {
            // ignore
          }
        }, 0);
      }

      actionsDiv.appendChild(clearBtn);
      actionsDiv.appendChild(doneBtn);

      editBox.appendChild(inputEl);
      editBox.appendChild(actionsDiv);
      editBox.appendChild(msgEl);
      container.appendChild(editBox);
    }
  }

  // 与 extension/src/label-form.js 保持一致
  function toggleOption(selection, field, value) {
    var current =
      selection && typeof selection === "object"
        ? selection
        : {
            work_type: null,
            work_subtype: null,
            secondary_work_types: [],
            sales_level: null,
            experience_fit: null,
            work_intensity: null,
            overall: null,
            note: "",
          };

    if (field === "work_type") {
      var curVal = current.work_type || null;
      var nextVal = curVal === value ? null : value;
      if (nextVal !== null && !Object.prototype.hasOwnProperty.call(CATEGORIES, nextVal)) {
        return current;
      }
      var updated = {};
      for (var k in current) {
        if (Object.prototype.hasOwnProperty.call(current, k)) {
          updated[k] = current[k];
        }
      }
      updated.work_type = nextVal;
      updated.work_subtype = null;
      if (nextVal !== null && Array.isArray(updated.secondary_work_types)) {
        var filteredSec = [];
        for (var f = 0; f < updated.secondary_work_types.length; f++) {
          var item = updated.secondary_work_types[f];
          if (!(item.category === nextVal && (item.subtype || null) === null)) {
            filteredSec.push(item);
          }
        }
        updated.secondary_work_types = filteredSec;
      }
      return updated;
    }

    if (field === "work_subtype") {
      var curType = current.work_type;
      if (
        !curType ||
        !Array.isArray(CATEGORIES[curType]) ||
        CATEGORIES[curType].indexOf(value) === -1
      ) {
        return current;
      }
      var curSub = current.work_subtype || null;
      var nextSub = curSub === value ? null : value;
      var updatedSub = {};
      for (var kSub in current) {
        if (Object.prototype.hasOwnProperty.call(current, kSub)) {
          updatedSub[kSub] = current[kSub];
        }
      }
      updatedSub.work_subtype = nextSub;
      if (Array.isArray(updatedSub.secondary_work_types)) {
        var filteredSub = [];
        for (var f2 = 0; f2 < updatedSub.secondary_work_types.length; f2++) {
          var item2 = updatedSub.secondary_work_types[f2];
          if (!(item2.category === curType && (item2.subtype || null) === (nextSub || null))) {
            filteredSub.push(item2);
          }
        }
        updatedSub.secondary_work_types = filteredSub;
      }
      return updatedSub;
    }

    var currentVal = current[field] !== undefined ? current[field] : null;
    var nextV = currentVal === value ? null : value;
    var updatedOther = {};
    for (var kOther in current) {
      if (Object.prototype.hasOwnProperty.call(current, kOther)) {
        updatedOther[kOther] = current[kOther];
      }
    }
    updatedOther[field] = nextV;
    return updatedOther;
  }

  // 与 extension/src/label-form.js 保持一致
  function toggleSecondary(selection, item) {
    var current =
      selection && typeof selection === "object"
        ? selection
        : {
            work_type: null,
            work_subtype: null,
            secondary_work_types: [],
            sales_level: null,
            experience_fit: null,
            work_intensity: null,
            overall: null,
            note: "",
          };

    var targetItem =
      typeof item === "string" ? { category: item, subtype: null } : item;

    if (!targetItem || typeof targetItem !== "object") {
      return { selection: current, error: null };
    }

    var category = targetItem.category;
    var subtype = targetItem.subtype || null;

    if (
      typeof category !== "string" ||
      !Object.prototype.hasOwnProperty.call(CATEGORIES, category)
    ) {
      return { selection: current, error: null };
    }

    if (
      subtype !== null &&
      (!Array.isArray(CATEGORIES[category]) || CATEGORIES[category].indexOf(subtype) === -1)
    ) {
      return { selection: current, error: null };
    }

    var isSameAsPrimary =
      current.work_type !== null &&
      current.work_type === category &&
      (current.work_subtype || null) === subtype;

    if (isSameAsPrimary) {
      return { selection: current, error: null };
    }

    var currentSecondary = Array.isArray(current.secondary_work_types)
      ? current.secondary_work_types.slice()
      : [];

    var existingIdx = -1;
    for (var i = 0; i < currentSecondary.length; i++) {
      if (
        currentSecondary[i].category === category &&
        (currentSecondary[i].subtype || null) === subtype
      ) {
        existingIdx = i;
        break;
      }
    }

    if (existingIdx !== -1) {
      currentSecondary.splice(existingIdx, 1);
      var updated = {};
      for (var k in current) {
        if (Object.prototype.hasOwnProperty.call(current, k)) {
          updated[k] = current[k];
        }
      }
      updated.secondary_work_types = currentSecondary;
      return { selection: updated, error: null };
    }

    if (currentSecondary.length >= 3) {
      return { selection: current, error: "最多选 3 项" };
    }

    currentSecondary.push({ category: category, subtype: subtype });
    var updatedAdd = {};
    for (var k2 in current) {
      if (Object.prototype.hasOwnProperty.call(current, k2)) {
        updatedAdd[k2] = current[k2];
      }
    }
    updatedAdd.secondary_work_types = currentSecondary;
    return { selection: updatedAdd, error: null };
  }

  function applyCardPosition() {
    if (!currentHost) return;
    if (currentCardSide === "left") {
      currentHost.style.left = "16px";
      currentHost.style.right = "auto";
    } else {
      currentHost.style.right = "16px";
      currentHost.style.left = "auto";
    }
    currentHost.style.bottom = "16px";
  }

  try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(["cardSide", "factsExpanded"], function (items) {
        if (chrome.runtime && chrome.runtime.lastError) return;
        if (items) {
          if (items.cardSide === "left" || items.cardSide === "right") {
            currentCardSide = items.cardSide;
            applyCardPosition();
          }
          if (typeof items.factsExpanded === "boolean") {
            isFactsExpanded = items.factsExpanded;
          }
        }
      });
    }
  } catch (e) {}

  function isJetNode(node) {
    if (!node || node.nodeType !== 1) return false;
    return (
      node.hasAttribute("data-jet") ||
      Boolean(node.closest && node.closest("[data-jet]"))
    );
  }

  function isJetMutation(mutation) {
    if (isJetNode(mutation.target)) return true;
    for (var i = 0; i < mutation.addedNodes.length; i++) {
      if (isJetNode(mutation.addedNodes[i])) return true;
    }
    for (var j = 0; j < mutation.removedNodes.length; j++) {
      if (isJetNode(mutation.removedNodes[j])) return true;
    }
    return false;
  }

  function removeAllJetElements() {
    clearAutoApplyToast();
    activeCardRefreshStatus = null;
    if (secTipTimer) {
      clearTimeout(secTipTimer);
      secTipTimer = null;
    }
    var jetEls = document.querySelectorAll("[data-jet]");
    for (var i = 0; i < jetEls.length; i++) {
      jetEls[i].remove();
    }
    currentHost = null;
    currentShadow = null;
    currentDetail = null;
    lastJobId = null;
    savedCardScrollTop = 0;
    isResumeReasonExpanded = false;
    currentMarksHost = null;
    currentMarksShadow = null;
  }

  function createQuoteEl(quote) {
    if (!quote || typeof quote.text !== "string" || quote.text.length === 0) {
      return null;
    }
    var qDiv = document.createElement("div");
    qDiv.className = "jet-quote";
    var textSpan = document.createElement("span");
    textSpan.className = "jet-quote-text";
    textSpan.textContent = "“" + quote.text + "”";
    qDiv.appendChild(textSpan);

    if (quote.found === false) {
      var notFoundSpan = document.createElement("span");
      notFoundSpan.className = "jet-quote-not-found";
      notFoundSpan.textContent = " 未在原文找到";
      qDiv.appendChild(notFoundSpan);
    }
    return qDiv;
  }

  function appendQuotes(parent, quotes) {
    if (Array.isArray(quotes)) {
      for (var i = 0; i < quotes.length; i++) {
        var el = createQuoteEl(quotes[i]);
        if (el) parent.appendChild(el);
      }
    }
  }

  function appendJetTag(btn) {
    var tag = document.createElement("span");
    tag.className = "jet-ai-tag";
    tag.textContent = "Jet";
    btn.appendChild(tag);
  }

  function buildCardDom() {
    if (!currentShadow || !currentDetail) return;

    var pageX = window.scrollX !== undefined ? window.scrollX : (window.pageXOffset || 0);
    var pageY = window.scrollY !== undefined ? window.scrollY : (window.pageYOffset || 0);

    while (currentShadow.firstChild) {
      currentShadow.removeChild(currentShadow.firstChild);
    }

    var styleEl = document.createElement("style");
    styleEl.textContent = [
      ".jet-card {",
      "  box-sizing: border-box;",
      "  width: 360px;",
      "  max-height: min(50vh, 480px);",
      "  overflow-y: auto;",
      "  overscroll-behavior: contain;",
      "  background: #ffffff;",
      "  border-radius: 8px;",
      "  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);",
      "  border: 1px solid #e0e0e0;",
      "  padding: 14px;",
      "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "  font-size: 13px;",
      "  color: #333333;",
      "  line-height: 1.5;",
      "}",
      ".jet-header {",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: space-between;",
      "  margin-bottom: 8px;",
      "  padding-bottom: 6px;",
      "  border-bottom: 1px solid #f0f0f0;",
      "}",
      ".jet-header-left {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 8px;",
      "}",
      ".jet-header-right {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 6px;",
      "}",
      ".jet-title {",
      "  font-size: 15px;",
      "  font-weight: bold;",
      "  color: #1976d2;",
      "}",
      ".jet-corrected-tag {",
      "  font-size: 11px;",
      "  color: #e65100;",
      "  background: #fff3e0;",
      "  border: 1px solid #ffe0b2;",
      "  border-radius: 3px;",
      "  padding: 1px 5px;",
      "  font-weight: 500;",
      "}",
      ".jet-verdict-badge {",
      "  font-size: 11px;",
      "  padding: 1px 6px;",
      "  border-radius: 3px;",
      "  font-weight: bold;",
      "}",
      ".jet-verdict-badge.green, .jet-verdict-badge.apply, .jet-verdict-badge.fit { background: #e8f5e9; color: #2e7d32; }",
      ".jet-verdict-badge.blue, .jet-verdict-badge.try { background: #e3f2fd; color: #1565c0; }",
      ".jet-verdict-badge.yellow, .jet-verdict-badge.check, .jet-verdict-badge.unsure { background: #fffde7; color: #5d4037; border: 1px solid #f9a825; }",
      ".jet-verdict-badge.red, .jet-verdict-badge.skip, .jet-verdict-badge.unfit { background: #ffebee; color: #c62828; }",
      ".jet-verdict-badge.neutral { background: #f5f5f5; color: #666; }",
      ".jet-risk-badge {",
      "  font-size: 11px;",
      "  padding: 1px 6px;",
      "  border-radius: 3px;",
      "  font-weight: bold;",
      "  background: #ffebee;",
      "  color: #c62828;",
      "  border: 1px solid #ef5350;",
      "}",
      ".jet-toggle-btn {",
      "  background: none;",
      "  border: 1px solid #ddd;",
      "  border-radius: 4px;",
      "  font-size: 11px;",
      "  padding: 2px 8px;",
      "  cursor: pointer;",
      "  color: #666;",
      "}",
      ".jet-toggle-btn:hover { background: #f0f0f0; }",
      ".jet-summary-row {",
      "  display: flex;",
      "  align-items: baseline;",
      "  flex-wrap: wrap;",
      "  gap: 8px;",
      "  margin: 6px 0 10px 0;",
      "  line-height: 1.4;",
      "}",
      ".jet-review-pending-note {",
      "  font-size: 11px;",
      "  color: #e65100;",
      "  background: #fff3e0;",
      "  border: 1px solid #ffe0b2;",
      "  border-radius: 3px;",
      "  padding: 1px 6px;",
      "  font-weight: 500;",
      "  white-space: nowrap;",
      "}",
      ".jet-summary-badge {",
      "  font-size: 12px;",
      "  font-weight: bold;",
      "  padding: 2px 7px;",
      "  border-radius: 4px;",
      "  white-space: nowrap;",
      "  flex-shrink: 0;",
      "}",
      ".jet-summary-badge.green, .jet-summary-badge.apply, .jet-summary-badge.fit { background: #e8f5e9; color: #2e7d32; }",
      ".jet-summary-badge.blue, .jet-summary-badge.try { background: #e3f2fd; color: #1565c0; }",
      ".jet-summary-badge.yellow, .jet-summary-badge.check, .jet-summary-badge.unsure { background: #fffde7; color: #5d4037; border: 1px solid #f9a825; }",
      ".jet-summary-badge.red, .jet-summary-badge.skip, .jet-summary-badge.unfit { background: #ffebee; color: #c62828; }",
      ".jet-summary-reason {",
      "  font-size: 13px;",
      "  color: #333333;",
      "  font-weight: 500;",
      "  word-break: break-word;",
      "}",
      ".jet-risk-section {",
      "  background: #fff8f8;",
      "  border: 1px solid #ef5350;",
      "  border-radius: 6px;",
      "  margin-top: 6px;",
      "  margin-bottom: 10px;",
      "  overflow: hidden;",
      "}",
      ".jet-risk-header {",
      "  background: #d32f2f;",
      "  color: #ffffff;",
      "  font-weight: bold;",
      "  font-size: 12px;",
      "  padding: 4px 10px;",
      "}",
      ".jet-risk-body {",
      "  padding: 8px 10px;",
      "}",
      ".jet-risk-item {",
      "  margin-bottom: 6px;",
      "}",
      ".jet-risk-item:last-child {",
      "  margin-bottom: 0;",
      "}",
      ".jet-risk-desc {",
      "  font-size: 12px;",
      "  font-weight: 600;",
      "  color: #c62828;",
      "}",
      ".jet-hr-questions {",
      "  margin: 6px 0 10px 0;",
      "  background: #fdfaf3;",
      "  border: 1px solid #fae8c8;",
      "  border-radius: 6px;",
      "  padding: 8px 10px;",
      "}",
      ".jet-hr-questions-title {",
      "  font-size: 12px;",
      "  font-weight: bold;",
      "  color: #b78103;",
      "  margin-bottom: 4px;",
      "}",
      ".jet-hr-questions-list {",
      "  margin: 0;",
      "  padding-left: 20px;",
      "  font-size: 12px;",
      "  color: #444;",
      "}",
      ".jet-hr-questions-list li {",
      "  margin-bottom: 3px;",
      "  line-height: 1.4;",
      "}",
      ".jet-hr-questions-list li:last-child {",
      "  margin-bottom: 0;",
      "}",
      ".jet-status-actions {",
      "  display: flex;",
      "  align-items: center;",
      "  gap: 6px;",
      "  margin: 6px 0 10px 0;",
      "}",
      ".jet-status-btn {",
      "  background: #f5f5f5;",
      "  border: 1px solid #ddd;",
      "  border-radius: 3px;",
      "  padding: 3px 8px;",
      "  font-size: 11px;",
      "  color: #333;",
      "  cursor: pointer;",
      "}",
      ".jet-status-btn:hover { background: #e8e8e8; }",
      ".jet-status-btn.active {",
      "  background: #1976d2;",
      "  border-color: #1976d2;",
      "  color: #ffffff;",
      "  font-weight: 500;",
      "}",
      ".jet-status-btn-tip {",
      "  font-size: 11px;",
      "  color: #d32f2f;",
      "  margin-left: 4px;",
      "}",
      ".jet-hr-note-row {",
      "  margin: 6px 0 10px 0;",
      "  font-size: 12px;",
      "  line-height: 1.4;",
      "}",
      ".jet-hr-note-label {",
      "  font-weight: 600;",
      "  color: #555;",
      "  margin-right: 4px;",
      "}",
      ".jet-hr-note-text {",
      "  color: #222;",
      "  word-break: break-word;",
      "}",
      ".jet-hr-note-link {",
      "  color: #1976d2;",
      "  cursor: pointer;",
      "  margin-left: 6px;",
      "  background: none;",
      "  border: none;",
      "  padding: 0;",
      "  font-size: 12px;",
      "  text-decoration: underline;",
      "}",
      ".jet-hr-note-link:hover {",
      "  color: #1565c0;",
      "}",
      ".jet-hr-note-add-btn {",
      "  color: #1976d2;",
      "  cursor: pointer;",
      "  background: none;",
      "  border: none;",
      "  padding: 0;",
      "  font-size: 12px;",
      "  font-weight: 500;",
      "}",
      ".jet-hr-note-add-btn:hover {",
      "  text-decoration: underline;",
      "}",
      ".jet-hr-note-edit-box {",
      "  margin-top: 4px;",
      "}",
      ".jet-hr-note-input {",
      "  width: 100%;",
      "  box-sizing: border-box;",
      "  padding: 5px 8px;",
      "  font-size: 12px;",
      "  border: 1px solid #ccc;",
      "  border-radius: 4px;",
      "  font-family: inherit;",
      "}",
      ".jet-hr-note-actions {",
      "  display: flex;",
      "  gap: 6px;",
      "  margin-top: 6px;",
      "}",
      ".jet-btn-sm {",
      "  padding: 3px 8px;",
      "  font-size: 11px;",
      "  border-radius: 3px;",
      "  cursor: pointer;",
      "  border: 1px solid #ccc;",
      "  background: #f5f5f5;",
      "  color: #333;",
      "}",
      ".jet-btn-sm:hover {",
      "  background: #e8e8e8;",
      "}",
      ".jet-btn-sm.primary {",
      "  background: #1976d2;",
      "  color: #fff;",
      "  border-color: #1976d2;",
      "}",
      ".jet-btn-sm.primary:hover {",
      "  background: #1565c0;",
      "}",
      ".jet-btn-sm.danger {",
      "  color: #c62828;",
      "  border-color: #ef9a9a;",
      "}",
      ".jet-btn-sm.danger:hover {",
      "  background: #ffebee;",
      "}",
      ".jet-hr-note-msg {",
      "  font-size: 11px;",
      "  margin-top: 4px;",
      "  color: #d32f2f;",
      "}",
      ".jet-facts-section {",
      "  background: #f9fbfd;",
      "  border: 1px solid #e3f2fd;",
      "  border-radius: 6px;",
      "  margin-top: 10px;",
      "  margin-bottom: 10px;",
      "  overflow: hidden;",
      "}",
      ".jet-facts-toggle {",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: space-between;",
      "  cursor: pointer;",
      "  user-select: none;",
      "  padding: 8px 10px;",
      "  background: #f1f7fc;",
      "}",
      ".jet-facts-toggle-title {",
      "  font-size: 12px;",
      "  font-weight: bold;",
      "  color: #1976d2;",
      "}",
      ".jet-facts-body {",
      "  padding: 10px;",
      "}",
      ".jet-fact-item { margin-bottom: 8px; }",
      ".jet-fact-item:last-child { margin-bottom: 0; }",
      ".jet-fact-label { font-weight: 600; font-size: 12px; color: #444; }",
      ".jet-fact-value { font-size: 12px; color: #222; margin-top: 2px; }",
      ".jet-fact-text { font-size: 12px; color: #333; margin-top: 2px; }",
      ".jet-quote {",
      "  margin-top: 3px;",
      "  padding: 2px 6px;",
      "  background: #f0f4f8;",
      "  border-left: 2px solid #90caf9;",
      "  border-radius: 2px;",
      "}",
      ".jet-quote-text { font-size: 11px; color: #555; font-style: italic; }",
      ".jet-quote-not-found { font-size: 11px; color: #d32f2f; margin-left: 4px; font-style: normal; }",
      ".jet-signal-row { margin-top: 4px; font-size: 12px; }",
      ".jet-signal-name { font-weight: 500; color: #555; }",
      ".jet-status-label {",
      "  display: inline-block;",
      "  font-size: 13px;",
      "  font-weight: 500;",
      "  color: #666666;",
      "  margin-bottom: 6px;",
      "}",
      ".jet-stale-reasons { color: #e65100; font-size: 12px; margin-bottom: 6px; }",
      ".jet-salary-note { color: #f57f17; font-size: 12px; margin-bottom: 6px; }",
      ".jet-reasons { margin: 8px 0; padding-left: 18px; color: #555555; }",
      ".jet-reasons li { margin-bottom: 4px; }",
      ".jet-time { font-size: 11px; color: #888888; margin-top: 8px; display: flex; align-items: center; gap: 8px; }",
      ".jet-verdict-overridden { font-size: 11px; color: #888888; margin: -4px 0 8px 0; line-height: 1.4; }",
      ".jet-force-rejudge-btn {",
      "  background: none;",
      "  border: 1px solid #cccccc;",
      "  border-radius: 3px;",
      "  font-size: 11px;",
      "  color: #666666;",
      "  padding: 1px 6px;",
      "  cursor: pointer;",
      "}",
      ".jet-force-rejudge-btn:hover { background: #f5f5f5; color: #333333; border-color: #bbbbbb; }",
      ".jet-force-rejudge-btn:disabled { opacity: 0.5; cursor: not-allowed; }",
      ".jet-error { font-size: 12px; color: #d32f2f; margin-top: 6px; }",
      ".jet-action-btn {",
      "  margin-top: 10px;",
      "  background: #1976d2;",
      "  color: #ffffff;",
      "  border: none;",
      "  padding: 6px 14px;",
      "  border-radius: 4px;",
      "  font-size: 12px;",
      "  cursor: pointer;",
      "}",
      ".jet-action-btn:hover { background: #1565c0; }",
      ".jet-action-btn:disabled { background: #bdbdbd; cursor: not-allowed; }",
      ".jet-annotation-container {",
      "  margin-top: 12px;",
      "  border-top: 1px dashed #e0e0e0;",
      "  padding-top: 8px;",
      "}",
      ".jet-annotation-toggle {",
      "  display: flex;",
      "  align-items: center;",
      "  justify-content: space-between;",
      "  cursor: pointer;",
      "  user-select: none;",
      "  padding: 4px 0;",
      "}",
      ".jet-annotation-toggle-title { font-size: 12px; font-weight: bold; color: #1976d2; }",
      ".jet-annotation-toggle-icon { font-size: 11px; color: #888; }",
      ".jet-annotation-form { margin-top: 8px; }",
      ".jet-form-group { margin-bottom: 8px; }",
      ".jet-form-title { font-size: 11px; font-weight: 600; color: #555; margin-bottom: 4px; }",
      ".jet-btn-row { display: flex; flex-wrap: wrap; gap: 4px; }",
      ".jet-opt-btn {",
      "  background: #f5f5f5;",
      "  border: 1px solid #ddd;",
      "  border-radius: 3px;",
      "  padding: 3px 8px;",
      "  font-size: 11px;",
      "  color: #333;",
      "  cursor: pointer;",
      "  display: inline-flex;",
      "  align-items: center;",
      "}",
      ".jet-opt-btn:hover { background: #e8e8e8; }",
      ".jet-opt-btn.active {",
      "  background: #1976d2;",
      "  border-color: #1976d2;",
      "  color: #ffffff;",
      "  font-weight: 500;",
      "}",
      ".jet-opt-btn:disabled,",
      ".jet-opt-btn.disabled {",
      "  opacity: 0.45;",
      "  cursor: not-allowed;",
      "  background: #eeeeee;",
      "  border-color: #dddddd;",
      "  color: #999999;",
      "}",
      ".jet-opt-btn:disabled:hover,",
      ".jet-opt-btn.disabled:hover {",
      "  background: #eeeeee;",
      "}",
      ".jet-ai-tag {",
      "  display: inline-block;",
      "  font-size: 10px;",
      "  line-height: 1.2;",
      "  color: #1976d2;",
      "  background: #e3f2fd;",
      "  border-radius: 2px;",
      "  padding: 1px 3px;",
      "  margin-left: 4px;",
      "  font-weight: bold;",
      "  vertical-align: middle;",
      "}",
      ".jet-opt-btn.active .jet-ai-tag {",
      "  background: rgba(255, 255, 255, 0.3);",
      "  color: inherit;",
      "}",
      ".jet-subtype-row {",
      "  margin-top: 4px;",
      "  padding-left: 6px;",
      "  border-left: 2px solid #90caf9;",
      "}",
      ".jet-secondary-toggle {",
      "  font-size: 11px;",
      "  font-weight: 600;",
      "  color: #1976d2;",
      "  cursor: pointer;",
      "  user-select: none;",
      "  padding: 3px 0;",
      "  margin-top: 4px;",
      "  margin-bottom: 4px;",
      "}",
      ".jet-secondary-content {",
      "  margin-top: 4px;",
      "  padding-left: 4px;",
      "  border-left: 2px solid #e3f2fd;",
      "}",
      ".jet-secondary-group {",
      "  margin-bottom: 6px;",
      "}",
      ".jet-secondary-group-title {",
      "  font-size: 11px;",
      "  font-weight: 500;",
      "  color: #555;",
      "  margin-bottom: 3px;",
      "}",
      ".jet-opt-btn.verdict-apply { border-color: #2e7d32; }",
      ".jet-opt-btn.verdict-apply.active { background: #2e7d32; color: #fff; border-color: #2e7d32; }",
      ".jet-opt-btn.verdict-try { border-color: #1565c0; }",
      ".jet-opt-btn.verdict-try.active { background: #1565c0; color: #fff; border-color: #1565c0; }",
      ".jet-opt-btn.verdict-check { border-color: #f9a825; }",
      ".jet-opt-btn.verdict-check.active { background: #f9a825; color: #333; border-color: #f9a825; }",
      ".jet-opt-btn.verdict-skip { border-color: #c62828; }",
      ".jet-opt-btn.verdict-skip.active { background: #c62828; color: #fff; border-color: #c62828; }",
      ".jet-group-tip {",
      "  font-size: 11px;",
      "  color: #e65100;",
      "  margin-top: 3px;",
      "  min-height: 16px;",
      "  line-height: 16px;",
      "}",
      ".jet-note-input {",
      "  width: 100%;",
      "  box-sizing: border-box;",
      "  padding: 6px 8px;",
      "  font-size: 12px;",
      "  border: 1px solid #ccc;",
      "  border-radius: 4px;",
      "  font-family: inherit;",
      "  resize: vertical;",
      "}",
      ".jet-save-label-btn {",
      "  margin-top: 6px;",
      "  background: #2e7d32;",
      "  color: #ffffff;",
      "  border: none;",
      "  padding: 6px 12px;",
      "  border-radius: 4px;",
      "  font-size: 12px;",
      "  cursor: pointer;",
      "  width: 100%;",
      "}",
      ".jet-save-label-btn:hover { background: #1b5e20; }",
      ".jet-save-label-btn:disabled { background: #bdbdbd; cursor: not-allowed; }",
      ".jet-label-status-msg { font-size: 11px; margin-top: 4px; text-align: center; }",
      ".jet-label-status-msg.success { color: #2e7d32; }",
      ".jet-label-status-msg.error { color: #d32f2f; }",
      ".jet-replacing-badge {",
      "  font-size: 11px;",
      "  font-weight: 500;",
      "  padding: 1px 6px;",
      "  border-radius: 4px;",
      "  opacity: 0.5;",
      "  border: 1px dashed;",
      "  white-space: nowrap;",
      "  display: inline-block;",
      "  vertical-align: middle;",
      "}",
      ".jet-replacing-badge.green, .jet-replacing-badge.apply { background: #e8f5e9; color: #2e7d32; border-color: #2e7d32; }",
      ".jet-replacing-badge.blue, .jet-replacing-badge.try { background: #e3f2fd; color: #1565c0; border-color: #1565c0; }",
      ".jet-replacing-badge.yellow, .jet-replacing-badge.check { background: #fffde7; color: #5d4037; border-color: #f9a825; }",
      ".jet-replacing-badge.red, .jet-replacing-badge.skip { background: #ffebee; color: #c62828; border-color: #c62828; }",
      ".jet-replacing-badge.neutral { background: #f5f5f5; color: #666666; border-color: #cccccc; }",
      ".jet-notice-row {",
      "  margin: -4px 0 10px 0;",
      "  padding: 6px 10px;",
      "  background: #fff3e0;",
      "  color: #e65100;",
      "  border: 1px solid #ffe0b2;",
      "  border-radius: 4px;",
      "  font-size: 12px;",
      "  font-weight: 500;",
      "  line-height: 1.4;",
      "}",
      ".jet-resume-suggestion {",
      "  margin: 6px 0 10px 0;",
      "  font-size: 12px;",
      "  line-height: 1.4;",
      "}",
      ".jet-resume-tag {",
      "  display: inline-block;",
      "  background: #f0f4f8;",
      "  border: 1px solid #d0dbe5;",
      "  border-radius: 4px;",
      "  padding: 3px 8px;",
      "  cursor: pointer;",
      "  user-select: none;",
      "  color: #1976d2;",
      "  font-weight: 500;",
      "  max-width: 100%;",
      "  box-sizing: border-box;",
      "  word-break: break-all;",
      "}",
      ".jet-resume-tag:hover {",
      "  background: #e3edf7;",
      "}",
      ".jet-resume-reason {",
      "  margin-top: 4px;",
      "  padding: 6px 8px;",
      "  background: #fafafa;",
      "  border-left: 2px solid #1976d2;",
      "  border-radius: 2px;",
      "  color: #555555;",
      "  font-size: 12px;",
      "  line-height: 1.4;",
      "  word-break: break-word;",
      "}",
    ].join("\n");
    currentShadow.appendChild(styleEl);

    var cardEl = document.createElement("div");
    cardEl.className = "jet-card";

    // 1. 标题栏
    var headerEl = document.createElement("div");
    headerEl.className = "jet-header";

    var headerLeft = document.createElement("div");
    headerLeft.className = "jet-header-left";

    var titleEl = document.createElement("span");
    titleEl.className = "jet-title";
    titleEl.textContent = "Jet";
    headerLeft.appendChild(titleEl);

    var labelObj =
      currentDetail.user_label && typeof currentDetail.user_label === "object"
        ? currentDetail.user_label
        : null;

    if (labelObj && labelObj.corrected === true) {
      var corrTag = document.createElement("span");
      corrTag.className = "jet-corrected-tag";
      corrTag.textContent = "已纠正";
      headerLeft.appendChild(corrTag);
    }

    // 折叠状态下只显示标题栏和结论徽标
    if (isCardCollapsed) {
      if (currentDetail.has_risk) {
        var riskBadge = document.createElement("span");
        riskBadge.className = "jet-risk-badge";
        riskBadge.textContent = "⚠ 风险";
        headerLeft.appendChild(riskBadge);
      }

      var isNoKey =
        currentDetail.view_state === "no_llm_key" ||
        currentDetail.notice === "no_llm_key" ||
        currentDetail.notice_text === "no_llm_key";

      var badgeText = isNoKey
        ? "请先在设置页填写 DeepSeek API Key"
        : (currentDetail.verdict_label ||
           (typeof currentDetail.label === "string" && currentDetail.label) ||
           currentDetail.status_label ||
           "");

      if (badgeText) {
        var badgeEl = document.createElement("span");
        var bTone = isNoKey ? "neutral" : (currentDetail.verdict_tone || "neutral");
        badgeEl.className = "jet-verdict-badge " + bTone;
        badgeEl.textContent = badgeText;
        headerLeft.appendChild(badgeEl);
      }
    }

    headerEl.appendChild(headerLeft);

    var headerRight = document.createElement("div");
    headerRight.className = "jet-header-right";

    var sideBtn = document.createElement("button");
    sideBtn.type = "button";
    sideBtn.className = "jet-toggle-btn";
    sideBtn.textContent = "⇆";
    sideBtn.title = "移到另一侧";
    sideBtn.setAttribute("aria-label", "移到另一侧");
    sideBtn.addEventListener("click", function () {
      currentCardSide = currentCardSide === "left" ? "right" : "left";
      applyCardPosition();
      try {
        if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
          chrome.storage.local.set({ cardSide: currentCardSide });
        }
      } catch (e) {}
    });
    headerRight.appendChild(sideBtn);

    var toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "jet-toggle-btn";
    toggleBtn.textContent = isCardCollapsed ? "展开" : "折叠";
    toggleBtn.addEventListener("click", function () {
      var pCard = currentShadow.querySelector(".jet-card");
      if (pCard) {
        savedCardScrollTop = pCard.scrollTop;
      }
      isCardCollapsed = !isCardCollapsed;
      buildCardDom();
    });
    headerRight.appendChild(toggleBtn);
    headerEl.appendChild(headerRight);

    cardEl.appendChild(headerEl);

    if (isCardCollapsed) {
      currentShadow.appendChild(cardEl);
      return;
    }

    // 1.5 风险警告（T079o）
    if (
      currentDetail.has_risk ||
      (Array.isArray(currentDetail.risk_signals) && currentDetail.risk_signals.length > 0)
    ) {
      var signals = Array.isArray(currentDetail.risk_signals)
        ? currentDetail.risk_signals
        : [];
      if (signals.length > 0) {
        var riskSection = document.createElement("div");
        riskSection.className = "jet-risk-section";

        var riskHeader = document.createElement("div");
        riskHeader.className = "jet-risk-header";
        riskHeader.textContent = "⚠ 风险警告";
        riskSection.appendChild(riskHeader);

        var riskBody = document.createElement("div");
        riskBody.className = "jet-risk-body";

        for (var rIdx = 0; rIdx < signals.length; rIdx++) {
          var sig = signals[rIdx];
          if (!sig || typeof sig !== "object") continue;

          var itemEl = document.createElement("div");
          itemEl.className = "jet-risk-item";

          if (sig.description) {
            var descEl = document.createElement("div");
            descEl.className = "jet-risk-desc";
            descEl.textContent = sig.description;
            itemEl.appendChild(descEl);
          }

          if (sig.quote) {
            var qEl = createQuoteEl(sig.quote);
            if (qEl) itemEl.appendChild(qEl);
          }

          if (Array.isArray(sig.quotes)) {
            appendQuotes(itemEl, sig.quotes);
          }

          riskBody.appendChild(itemEl);
        }

        riskSection.appendChild(riskBody);
        cardEl.appendChild(riskSection);
      }
    }

    // 2. 结论摘要行
    var summaryRow = document.createElement("div");
    summaryRow.className = "jet-summary-row";

    if (currentDetail.verdict_label) {
      var tone = currentDetail.verdict_tone || "neutral";
      var verdictBadge = document.createElement("span");
      verdictBadge.className = "jet-summary-badge " + tone;
      verdictBadge.textContent = currentDetail.verdict_label;
      summaryRow.appendChild(verdictBadge);

      if (currentDetail.review_pending) {
        var reviewNoteEl = document.createElement("span");
        reviewNoteEl.className = "jet-review-pending-note";
        reviewNoteEl.textContent = currentDetail.review_note || "复核中，结论可能下调";
        summaryRow.appendChild(reviewNoteEl);
      }

      var oneReason = currentDetail.summary_reason || null;
      if (!oneReason) {
        if (typeof currentDetail.verdict_reason === "string" && currentDetail.verdict_reason.trim().length > 0) {
          oneReason = currentDetail.verdict_reason.trim();
        } else if (Array.isArray(currentDetail.derivation) && currentDetail.derivation.length > 0 && typeof currentDetail.derivation[0] === "string" && currentDetail.derivation[0].trim().length > 0) {
          oneReason = currentDetail.derivation[0].trim();
        } else if (Array.isArray(currentDetail.reasons) && currentDetail.reasons.length > 0 && typeof currentDetail.reasons[0] === "string" && currentDetail.reasons[0].trim().length > 0) {
          oneReason = currentDetail.reasons[0].trim();
        }
      }

      if (oneReason) {
        var reasonSpan = document.createElement("span");
        reasonSpan.className = "jet-summary-reason";
        reasonSpan.textContent = oneReason;
        summaryRow.appendChild(reasonSpan);
      }
      cardEl.appendChild(summaryRow);

      if (currentDetail.verdict_overridden === true) {
        var rawMv = currentDetail.model_verdict;
        var normMv = (rawMv && LEGACY_VERDICT_MAP[rawMv]) ? LEGACY_VERDICT_MAP[rawMv] : rawMv;
        var mvName = (normMv && VERDICT_LABELS[normMv]) ? VERDICT_LABELS[normMv] : (normMv || "");
        var ovEl = document.createElement("div");
        ovEl.className = "jet-verdict-overridden";
        ovEl.textContent = "按你的纠正显示 · Jet 原判：" + mvName;
        cardEl.appendChild(ovEl);
      }
    } else {
      var isNoKey =
        currentDetail.view_state === "no_llm_key" ||
        currentDetail.notice === "no_llm_key" ||
        currentDetail.notice_text === "no_llm_key";

      var statusText = isNoKey
        ? "请先在设置页填写 DeepSeek API Key"
        : ((typeof currentDetail.label === "string" && currentDetail.label) ||
           currentDetail.status_label ||
           "");
      if (statusText) {
        var statusEl = document.createElement("span");
        statusEl.className = "jet-status-label";
        statusEl.textContent = statusText;
        summaryRow.appendChild(statusEl);

        if (currentDetail.replacing) {
          var rep = currentDetail.replacing;
          var repBadge = document.createElement("span");
          var repTone = rep.verdict_tone || "neutral";
          repBadge.className = "jet-replacing-badge " + repTone;
          var repTimeStr = rep.judged_at ? formatJudgedAt(rep.judged_at) : "";
          var repLabel = rep.verdict_label || rep.verdict || "";
          repBadge.textContent = "旧结论：" + repLabel + (repTimeStr ? "（" + repTimeStr + "）" : "");
          summaryRow.appendChild(repBadge);
        }

        cardEl.appendChild(summaryRow);
      }
    }

    if (currentDetail.notice_text && currentDetail.notice_text !== "no_llm_key") {
      var noticeEl = document.createElement("div");
      noticeEl.className = "jet-notice-row";
      noticeEl.textContent = currentDetail.notice_text;
      cardEl.appendChild(noticeEl);
    }

    // 2.3 状态按钮行（T079s）：收藏与判断无关，只要有岗位就显示（额度用完、判断失败、判断中、未设画像等都显示）
    if (currentDetail.platform_job_id) {
      var statusRow = document.createElement("div");
      statusRow.className = "jet-status-actions";

      var STATUS_ACTIONS = [
        { key: "saved", label: "收藏" },
        { key: "applied", label: "已投递" },
        { key: "skipped", label: "不考虑" },
      ];

      var activeStatus = null;
      if (currentDetail.my_status) {
        if (typeof currentDetail.my_status === "object" && currentDetail.my_status.status) {
          activeStatus = currentDetail.my_status.status;
        } else if (typeof currentDetail.my_status === "string") {
          activeStatus = currentDetail.my_status;
        }
      }

      var tipEl = document.createElement("span");
      tipEl.className = "jet-status-btn-tip";
      var tipTimer = null;

      var statusButtons = {};

      function refreshStatusButtons(highlightKey) {
        for (var a = 0; a < STATUS_ACTIONS.length; a++) {
          var act = STATUS_ACTIONS[a];
          var btnEl = statusButtons[act.key];
          if (btnEl) {
            if (act.key === highlightKey) {
              btnEl.classList.add("active");
            } else {
              btnEl.classList.remove("active");
            }
          }
        }
      }

      activeCardRefreshStatus = function (newStatus) {
        activeStatus = newStatus;
        refreshStatusButtons(newStatus);
      };

      var targetJobId = currentDetail.platform_job_id;

      for (var sIdx = 0; sIdx < STATUS_ACTIONS.length; sIdx++) {
        (function (act) {
          var sBtn = document.createElement("button");
          sBtn.type = "button";
          sBtn.className = "jet-status-btn" + (activeStatus === act.key ? " active" : "");
          sBtn.textContent = act.label;
          statusButtons[act.key] = sBtn;

          sBtn.addEventListener("click", function () {
            var prevStatus = activeStatus;
            var nextStatus = activeStatus === act.key ? null : act.key;
            activeStatus = nextStatus;
            refreshStatusButtons(nextStatus);

            if (tipTimer) {
              clearTimeout(tipTimer);
              tipTimer = null;
            }
            tipEl.textContent = "";

            try {
              chrome.runtime.sendMessage(
                {
                  type: "set_job_status",
                  platform_job_id: targetJobId,
                  status: nextStatus,
                },
                function (res) {
                  if (res && res.ok) {
                    if (currentDetail && currentDetail.platform_job_id === targetJobId) {
                      currentDetail.my_status = nextStatus ? { status: nextStatus } : null;
                    }
                    updateListMarkStatus(targetJobId, nextStatus);
                  } else {
                    if (currentDetail && currentDetail.platform_job_id === targetJobId) {
                      activeStatus = prevStatus;
                      refreshStatusButtons(prevStatus);
                      tipEl.textContent = "保存失败";
                      tipTimer = setTimeout(function () {
                        tipEl.textContent = "";
                      }, 2000);
                    }
                  }
                }
              );
            } catch (e) {
              if (currentDetail && currentDetail.platform_job_id === targetJobId) {
                activeStatus = prevStatus;
                refreshStatusButtons(prevStatus);
                tipEl.textContent = "保存失败";
                tipTimer = setTimeout(function () {
                  tipEl.textContent = "";
                }, 2000);
              }
            }
          });

          statusRow.appendChild(sBtn);
        })(STATUS_ACTIONS[sIdx]);
      }

      statusRow.appendChild(tipEl);
      cardEl.appendChild(statusRow);
    }

    // 2.4 简历建议 (006 FR-007, US1)
    if (shouldShowResumeSuggestion(currentDetail)) {
      var resumeSection = document.createElement("div");
      resumeSection.className = "jet-resume-suggestion";

      var resumeTag = document.createElement("div");
      resumeTag.className = "jet-resume-tag";
      resumeTag.textContent = "建议投：" + currentDetail.resume_suggestion.name;
      resumeTag.title = currentDetail.resume_suggestion.reason || "";

      var resumeReason = document.createElement("div");
      resumeReason.className = "jet-resume-reason";
      resumeReason.textContent = currentDetail.resume_suggestion.reason || "";
      resumeReason.style.display = isResumeReasonExpanded ? "block" : "none";

      resumeTag.addEventListener("click", function (e) {
        e.stopPropagation();
        isResumeReasonExpanded = !isResumeReasonExpanded;
        resumeReason.style.display = isResumeReasonExpanded ? "block" : "none";
      });

      resumeSection.appendChild(resumeTag);
      resumeSection.appendChild(resumeReason);
      cardEl.appendChild(resumeSection);
    }

    // 2.5 建议问 HR（T079i）
    if (
      Array.isArray(currentDetail.hr_questions) &&
      currentDetail.hr_questions.length > 0
    ) {
      var hrqSection = document.createElement("div");
      hrqSection.className = "jet-hr-questions";

      var hrqTitle = document.createElement("div");
      hrqTitle.className = "jet-hr-questions-title";
      hrqTitle.textContent = "建议问 HR：";
      hrqSection.appendChild(hrqTitle);

      var hrqList = document.createElement("ol");
      hrqList.className = "jet-hr-questions-list";

      for (var qIdx = 0; qIdx < currentDetail.hr_questions.length; qIdx++) {
        var qText = currentDetail.hr_questions[qIdx];
        if (typeof qText === "string" && qText.length > 0) {
          var liEl = document.createElement("li");
          liEl.textContent = qText;
          hrqList.appendChild(liEl);
        }
      }

      hrqSection.appendChild(hrqList);
      cardEl.appendChild(hrqSection);
    }

    // 2.6 HR 说的实际情况（T079j）
    if (currentDetail.platform_job_id) {
      var hrNoteRow = document.createElement("div");
      hrNoteRow.className = "jet-hr-note-row";
      renderHrNoteSection(hrNoteRow);
      cardEl.appendChild(hrNoteRow);
    }

    // 3. 可能过时 / 错误 / 薪资不可见 / 判断时间与操作按钮
    if (currentDetail.stale_reasons && currentDetail.stale_reasons.length > 0) {
      var staleEl = document.createElement("div");
      staleEl.className = "jet-stale-reasons";
      staleEl.textContent =
        "可能过时（" + currentDetail.stale_reasons.join("、") + "）";
      cardEl.appendChild(staleEl);
    }

    if (currentDetail.error) {
      var errEl = document.createElement("div");
      errEl.className = "jet-error";
      errEl.textContent = "错误原因: " + currentDetail.error;
      cardEl.appendChild(errEl);
    }

    if (currentDetail.salary_note) {
      var salaryEl = document.createElement("div");
      salaryEl.className = "jet-salary-note";
      salaryEl.textContent = currentDetail.salary_note;
      cardEl.appendChild(salaryEl);
    }

    var timeEl = null;
    if (currentDetail.judged_at) {
      timeEl = document.createElement("div");
      timeEl.className = "jet-time";
      var timeSpan = document.createElement("span");
      try {
        timeSpan.textContent =
          "判断时间: " + new Date(currentDetail.judged_at).toLocaleString();
      } catch (e) {
        timeSpan.textContent = "判断时间: " + String(currentDetail.judged_at);
      }
      timeEl.appendChild(timeSpan);
      cardEl.appendChild(timeEl);
    }

    if (currentDetail.action === "force_rejudge" && currentDetail.platform_job_id) {
      var forceBtn = document.createElement("button");
      forceBtn.type = "button";
      forceBtn.className = "jet-force-rejudge-btn";
      forceBtn.textContent = "重新判断";
      var forceJobId = currentDetail.platform_job_id;
      forceBtn.addEventListener("click", function () {
        forceBtn.disabled = true;
        forceBtn.textContent = "正在请求...";
        notify({ type: "rejudge", platform_job_id: forceJobId, force: true });
      });
      if (timeEl) {
        timeEl.appendChild(forceBtn);
      } else {
        var forceRow = document.createElement("div");
        forceRow.className = "jet-time";
        forceRow.appendChild(forceBtn);
        cardEl.appendChild(forceRow);
      }
    } else if (
      (currentDetail.action === "rejudge" || currentDetail.action === "retry") &&
      currentDetail.platform_job_id
    ) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "jet-action-btn";
      if (currentDetail.action === "rejudge") {
        btn.textContent = "重新判断";
      } else if (currentDetail.action === "retry") {
        btn.textContent = "重试";
      }

      var currentJobId = currentDetail.platform_job_id;
      btn.addEventListener("click", function () {
        btn.disabled = true;
        btn.textContent = "正在请求...";
        notify({ type: "rejudge", platform_job_id: currentJobId });
      });
      cardEl.appendChild(btn);
    }

    // 4. "事实与推导"区
    var hasFacts = Boolean(currentDetail.facts && typeof currentDetail.facts === "object");
    var listItems = [];
    if (Array.isArray(currentDetail.derivation) && currentDetail.derivation.length > 0) {
      listItems = currentDetail.derivation;
    } else if (Array.isArray(currentDetail.reasons) && currentDetail.reasons.length > 0) {
      listItems = currentDetail.reasons;
    }
    var hasDerivation = listItems.length > 0;

    if (hasFacts || hasDerivation) {
      var factsSection = document.createElement("div");
      factsSection.className = "jet-facts-section";

      var factsToggle = document.createElement("div");
      factsToggle.className = "jet-facts-toggle";

      var factsToggleTitle = document.createElement("span");
      factsToggleTitle.className = "jet-facts-toggle-title";
      factsToggleTitle.textContent = isFactsExpanded ? "事实与推导 ▾" : "事实与推导 ▸";
      factsToggle.appendChild(factsToggleTitle);

      var factsBody = document.createElement("div");
      factsBody.className = "jet-facts-body";
      factsBody.style.display = isFactsExpanded ? "block" : "none";

      factsToggle.addEventListener("click", function () {
        isFactsExpanded = !isFactsExpanded;
        factsToggleTitle.textContent = isFactsExpanded ? "事实与推导 ▾" : "事实与推导 ▸";
        factsBody.style.display = isFactsExpanded ? "block" : "none";
        try {
          if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
            chrome.storage.local.set({ factsExpanded: isFactsExpanded });
          }
        } catch (e) {}
      });

      factsSection.appendChild(factsToggle);

      if (hasFacts) {
        var facts = currentDetail.facts;

        // 白话职责
        if (facts.summary) {
          var sumDiv = document.createElement("div");
          sumDiv.className = "jet-fact-item";
          var sumLabel = document.createElement("div");
          sumLabel.className = "jet-fact-label";
          sumLabel.textContent = "白话职责：";
          sumDiv.appendChild(sumLabel);

          var sumText = document.createElement("div");
          sumText.className = "jet-fact-text";
          sumText.textContent = facts.summary.text || "";
          sumDiv.appendChild(sumText);

          appendQuotes(sumDiv, facts.summary.quotes);
          factsBody.appendChild(sumDiv);
        }

        // 工作类型
        if (facts.work_type) {
          var wtDiv = document.createElement("div");
          wtDiv.className = "jet-fact-item";
          var wtLabel = document.createElement("div");
          wtLabel.className = "jet-fact-label";
          wtLabel.textContent = "工作类型：";
          wtDiv.appendChild(wtLabel);

          var wtValStr = "";
          var isV4 =
            Object.prototype.hasOwnProperty.call(facts.work_type, "subtype") ||
            (Array.isArray(facts.work_type.secondary) &&
              facts.work_type.secondary.length > 0 &&
              typeof facts.work_type.secondary[0] === "object");

          if (isV4) {
            var primaryVal = facts.work_type.value || "";
            var primarySub = facts.work_type.subtype || null;
            var primaryStr = primarySub ? (primaryVal + " / " + primarySub) : primaryVal;

            var secList = Array.isArray(facts.work_type.secondary)
              ? facts.work_type.secondary.filter(Boolean)
              : [];
            var secParts = [];
            for (var sIdx = 0; sIdx < secList.length; sIdx++) {
              var secItem = secList[sIdx];
              if (typeof secItem === "object" && secItem !== null) {
                if (secItem.subtype) {
                  secParts.push(secItem.category + "/" + secItem.subtype);
                } else if (secItem.category) {
                  secParts.push(secItem.category);
                }
              } else if (typeof secItem === "string") {
                secParts.push(secItem);
              }
            }

            if (primaryStr && secParts.length > 0) {
              wtValStr = "主要：" + primaryStr + "；次要：" + secParts.join("、");
            } else if (primaryStr) {
              wtValStr = "主要：" + primaryStr;
            } else if (secParts.length > 0) {
              wtValStr = "次要：" + secParts.join("、");
            }
          } else {
            wtValStr = facts.work_type.value || "";
            var wtSec = Array.isArray(facts.work_type.secondary)
              ? facts.work_type.secondary.filter(Boolean)
              : [];
            if (wtSec.length > 0) {
              if (wtValStr) {
                wtValStr = "主要 " + wtValStr + "；次要 " + wtSec.join("、");
              } else {
                wtValStr = "次要 " + wtSec.join("、");
              }
            }
          }

          if (
            facts.work_type.probabilities &&
            typeof facts.work_type.probabilities === "object"
          ) {
            var probs = facts.work_type.probabilities;
            var maxP = 0;
            for (var pk in probs) {
              if (Object.prototype.hasOwnProperty.call(probs, pk)) {
                var p = Number(probs[pk]);
                if (p > maxP) maxP = p;
              }
            }
            if (maxP > 0) {
              wtValStr += " (" + Math.round(maxP * 100) + "%)";
            }
          }

          var wtText = document.createElement("div");
          wtText.className = "jet-fact-value";
          wtText.textContent = wtValStr;
          wtDiv.appendChild(wtText);

          appendQuotes(wtDiv, facts.work_type.quotes);
          factsBody.appendChild(wtDiv);
        }

        // 销售与客户对接
        if (facts.sales_level) {
          var slDiv = document.createElement("div");
          slDiv.className = "jet-fact-item";
          var slLabel = document.createElement("div");
          slLabel.className = "jet-fact-label";
          slLabel.textContent = "销售与客户对接：";
          slDiv.appendChild(slLabel);

          var slText = document.createElement("div");
          slText.className = "jet-fact-value";
          slText.textContent = facts.sales_level.value || "";
          slDiv.appendChild(slText);

          if (Array.isArray(facts.sales_level.signals)) {
            for (var s = 0; s < facts.sales_level.signals.length; s++) {
              var sig = facts.sales_level.signals[s];
              var sigRow = document.createElement("div");
              sigRow.className = "jet-signal-row";
              var sigName = document.createElement("span");
              sigName.className = "jet-signal-name";
              sigName.textContent = sig.signal ? sig.signal + "：" : "";
              sigRow.appendChild(sigName);
              if (sig.quote) {
                var qSigEl = createQuoteEl(sig.quote);
                if (qSigEl) sigRow.appendChild(qSigEl);
              }
              slDiv.appendChild(sigRow);
            }
          }
          factsBody.appendChild(slDiv);
        }

        // 经验门槛
        if (facts.experience) {
          var expDiv = document.createElement("div");
          expDiv.className = "jet-fact-item";
          var expLabel = document.createElement("div");
          expLabel.className = "jet-fact-label";
          var expLabelText = "经验门槛：";
          if (facts.experience.requirement_type) {
            expLabelText = "经验门槛（" + facts.experience.requirement_type + "）：";
          }
          expLabel.textContent = expLabelText;
          expDiv.appendChild(expLabel);

          if (facts.experience.requirement) {
            var reqEl = createQuoteEl(facts.experience.requirement);
            if (reqEl) expDiv.appendChild(reqEl);
          }

          var expValStr = facts.experience.value || "";
          if (facts.experience.gap) {
            expValStr += "（差距：" + facts.experience.gap + "）";
          }
          var expVal = document.createElement("div");
          expVal.className = "jet-fact-value";
          expVal.textContent = expValStr;
          expDiv.appendChild(expVal);

          factsBody.appendChild(expDiv);
        }

        // 工作强度 / 加班
        if (facts.work_intensity) {
          var wiDiv = document.createElement("div");
          wiDiv.className = "jet-fact-item";
          var wiLabel = document.createElement("div");
          wiLabel.className = "jet-fact-label";
          wiLabel.textContent = "工作强度：";
          wiDiv.appendChild(wiLabel);

          var wiVal = document.createElement("div");
          wiVal.className = "jet-fact-value";
          wiVal.textContent = facts.work_intensity.value || "";
          wiDiv.appendChild(wiVal);

          appendQuotes(wiDiv, facts.work_intensity.quotes);
          factsBody.appendChild(wiDiv);
        } else if (facts.overtime) {
          var otDiv = document.createElement("div");
          otDiv.className = "jet-fact-item";
          var otLabel = document.createElement("div");
          otLabel.className = "jet-fact-label";
          otLabel.textContent = "加班 / 单休：";
          otDiv.appendChild(otLabel);

          var otVal = document.createElement("div");
          otVal.className = "jet-fact-value";
          otVal.textContent = (facts.overtime.value || "") + "（旧）";
          otDiv.appendChild(otVal);

          appendQuotes(otDiv, facts.overtime.quotes);
          factsBody.appendChild(otDiv);
        }
      }

      // 推导列表 / 理由列表
      if (hasDerivation) {
        var reasonsList = document.createElement("ul");
        reasonsList.className = "jet-reasons";
        for (var r = 0; r < listItems.length; r++) {
          var li = document.createElement("li");
          li.textContent = listItems[r];
          reasonsList.appendChild(li);
        }
        factsBody.appendChild(reasonsList);
      }

      factsSection.appendChild(factsBody);
      cardEl.appendChild(factsSection);
    }

    // 5. 标注区（保持默认收起）
    var canAnnotate = Boolean(
      currentDetail.platform_job_id &&
        (currentDetail.facts ||
          currentDetail.prompt_version)
    );

    if (canAnnotate) {
      var annotContainer = document.createElement("div");
      annotContainer.className = "jet-annotation-container";

      var annotToggleBar = document.createElement("div");
      annotToggleBar.className = "jet-annotation-toggle";

      var annotToggleTitle = document.createElement("span");
      annotToggleTitle.className = "jet-annotation-toggle-title";
      annotToggleTitle.textContent = "标注（可选）";
      annotToggleBar.appendChild(annotToggleTitle);

      var annotToggleIcon = document.createElement("span");
      annotToggleIcon.className = "jet-annotation-toggle-icon";
      annotToggleIcon.textContent = isAnnotationExpanded ? "收起 ▲" : "展开 ▼";
      annotToggleBar.appendChild(annotToggleIcon);

      annotToggleBar.addEventListener("click", function () {
        var pCard = currentShadow.querySelector(".jet-card");
        if (pCard) {
          savedCardScrollTop = pCard.scrollTop;
        }
        isAnnotationExpanded = !isAnnotationExpanded;
        buildCardDom();
      });
      annotContainer.appendChild(annotToggleBar);

      if (isAnnotationExpanded) {
        var formEl = document.createElement("div");
        formEl.className = "jet-annotation-form";

        // Jet 事实提取
        var jetFacts = currentDetail.facts || null;
        var jetWorkType = jetFacts?.work_type?.value || null;
        var jetWorkSubtype = jetFacts?.work_type?.subtype || null;
        var jetSecList = [];
        if (Array.isArray(jetFacts?.work_type?.secondary)) {
          for (var jsIdx = 0; jsIdx < jetFacts.work_type.secondary.length; jsIdx++) {
            var jsItem = jetFacts.work_type.secondary[jsIdx];
            if (jsItem && typeof jsItem === "object") {
              jetSecList.push({ category: jsItem.category, subtype: jsItem.subtype || null });
            } else if (typeof jsItem === "string") {
              jetSecList.push({ category: jsItem, subtype: null });
            }
          }
        }
        var jetSalesLevel = jetFacts?.sales_level?.value || null;
        var jetExpFit = jetFacts?.experience?.value || null;
        var jetWorkIntensity = jetFacts?.work_intensity?.value || null;
        var jetOverall = currentDetail.model_verdict || currentDetail.verdict || null;
        if (jetOverall && LEGACY_VERDICT_MAP[jetOverall]) {
          jetOverall = LEGACY_VERDICT_MAP[jetOverall];
        }

        var secondaryButtons = [];
        var secTipEl = document.createElement("div");
        secTipEl.className = "jet-group-tip";

        function updateSecondaryButtons() {
          var curPrimaryCat = annotationSelection.work_type;
          var curPrimarySub = annotationSelection.work_subtype || null;
          var curSec = Array.isArray(annotationSelection.secondary_work_types)
            ? annotationSelection.secondary_work_types
            : [];

          for (var j = 0; j < secondaryButtons.length; j++) {
            var btn = secondaryButtons[j];
            var btnCat = btn.getAttribute("data-category");
            var btnSub = btn.getAttribute("data-subtype") || null;

            var isSameAsPrimary =
              curPrimaryCat !== null &&
              curPrimaryCat === btnCat &&
              curPrimarySub === btnSub;

            if (isSameAsPrimary) {
              btn.disabled = true;
              btn.classList.add("disabled");
              btn.classList.remove("active");
            } else {
              btn.disabled = false;
              btn.classList.remove("disabled");

              var isSelected = false;
              for (var k = 0; k < curSec.length; k++) {
                if (curSec[k].category === btnCat && (curSec[k].subtype || null) === btnSub) {
                  isSelected = true;
                  break;
                }
              }

              if (isSelected) {
                btn.classList.add("active");
              } else {
                btn.classList.remove("active");
              }
            }
          }
        }

        // 1. 主要类型
        var primaryGrp = document.createElement("div");
        primaryGrp.className = "jet-form-group";
        var primaryTitle = document.createElement("div");
        primaryTitle.className = "jet-form-title";
        primaryTitle.textContent = "主要类型（单选）：";
        primaryGrp.appendChild(primaryTitle);

        var primaryCatRow = document.createElement("div");
        primaryCatRow.className = "jet-btn-row";

        var subtypeContainer = document.createElement("div");
        subtypeContainer.className = "jet-subtype-row";

        var categoryButtons = [];

        function renderSubtypeButtons() {
          while (subtypeContainer.firstChild) {
            subtypeContainer.removeChild(subtypeContainer.firstChild);
          }

          var selectedCat = annotationSelection.work_type;
          if (!selectedCat || !CATEGORIES[selectedCat] || CATEGORIES[selectedCat].length === 0) {
            subtypeContainer.style.display = "none";
            return;
          }

          subtypeContainer.style.display = "block";
          var subRow = document.createElement("div");
          subRow.className = "jet-btn-row";

          var subList = CATEGORIES[selectedCat];
          var subButtons = [];

          for (var s = 0; s < subList.length; s++) {
            (function (subName) {
              var sBtn = document.createElement("button");
              sBtn.type = "button";
              var isSubActive = annotationSelection.work_subtype === subName;
              sBtn.className = "jet-opt-btn" + (isSubActive ? " active" : "");
              sBtn.setAttribute("data-val", subName);

              var sLabel = document.createElement("span");
              sLabel.textContent = subName;
              sBtn.appendChild(sLabel);

              if (jetWorkSubtype === subName) {
                appendJetTag(sBtn);
              }

              sBtn.addEventListener("click", function () {
                annotationSelection = toggleOption(annotationSelection, "work_subtype", subName);
                hasUnsavedAnnotation = true;
                var nextSubVal = annotationSelection.work_subtype;

                for (var bIdx = 0; bIdx < subButtons.length; bIdx++) {
                  var bEl = subButtons[bIdx];
                  if (bEl.getAttribute("data-val") === nextSubVal) {
                    bEl.classList.add("active");
                  } else {
                    bEl.classList.remove("active");
                  }
                }
                updateSecondaryButtons();
              });

              subButtons.push(sBtn);
              subRow.appendChild(sBtn);
            })(subList[s]);
          }

          subtypeContainer.appendChild(subRow);
        }

        var catList = Object.keys(CATEGORIES);
        for (var cIdx = 0; cIdx < catList.length; cIdx++) {
          (function (catName) {
            var catBtn = document.createElement("button");
            catBtn.type = "button";
            var isCatActive = annotationSelection.work_type === catName;
            catBtn.className = "jet-opt-btn" + (isCatActive ? " active" : "");
            catBtn.setAttribute("data-val", catName);

            var catLabel = document.createElement("span");
            catLabel.textContent = catName;
            catBtn.appendChild(catLabel);

            if (jetWorkType === catName) {
              appendJetTag(catBtn);
            }

            catBtn.addEventListener("click", function () {
              annotationSelection = toggleOption(annotationSelection, "work_type", catName);
              hasUnsavedAnnotation = true;
              var nextCatVal = annotationSelection.work_type;

              for (var cb = 0; cb < categoryButtons.length; cb++) {
                var cEl = categoryButtons[cb];
                if (cEl.getAttribute("data-val") === nextCatVal) {
                  cEl.classList.add("active");
                } else {
                  cEl.classList.remove("active");
                }
              }

              renderSubtypeButtons();
              updateSecondaryButtons();
            });

            categoryButtons.push(catBtn);
            primaryCatRow.appendChild(catBtn);
          })(catList[cIdx]);
        }

        primaryGrp.appendChild(primaryCatRow);
        primaryGrp.appendChild(subtypeContainer);
        renderSubtypeButtons();
        formEl.appendChild(primaryGrp);

        // 2. 次要类型（默认收起，点"次要类型（选填）▸"展开）
        var secGrp = document.createElement("div");
        secGrp.className = "jet-form-group";

        var secToggleBar = document.createElement("div");
        secToggleBar.className = "jet-secondary-toggle";
        secToggleBar.textContent = isSecondaryExpanded
          ? "次要类型（选填） ▾"
          : "次要类型（选填） ▸";

        var secContent = document.createElement("div");
        secContent.className = "jet-secondary-content";
        secContent.style.display = isSecondaryExpanded ? "block" : "none";

        secToggleBar.addEventListener("click", function () {
          isSecondaryExpanded = !isSecondaryExpanded;
          secToggleBar.textContent = isSecondaryExpanded
            ? "次要类型（选填） ▾"
            : "次要类型（选填） ▸";
          secContent.style.display = isSecondaryExpanded ? "block" : "none";
        });

        secGrp.appendChild(secToggleBar);
        secondaryButtons = [];

        function checkJetSecondaryMatch(c, s) {
          for (var i = 0; i < jetSecList.length; i++) {
            if (jetSecList[i].category === c && (jetSecList[i].subtype || null) === (s || null)) {
              return true;
            }
          }
          return false;
        }

        for (var c2 = 0; c2 < catList.length; c2++) {
          (function (catName) {
            var groupDiv = document.createElement("div");
            groupDiv.className = "jet-secondary-group";

            var gTitle = document.createElement("div");
            gTitle.className = "jet-secondary-group-title";
            gTitle.textContent = catName + "：";
            groupDiv.appendChild(gTitle);

            var gRow = document.createElement("div");
            gRow.className = "jet-btn-row";

            // 大类按钮（只选大类）
            var mainOnlyBtn = document.createElement("button");
            mainOnlyBtn.type = "button";
            mainOnlyBtn.className = "jet-opt-btn";
            mainOnlyBtn.setAttribute("data-category", catName);
            mainOnlyBtn.setAttribute("data-subtype", "");

            var mainOnlyLabel = document.createElement("span");
            mainOnlyLabel.textContent = catName === "其他" ? "其他" : "只选大类";
            mainOnlyBtn.appendChild(mainOnlyLabel);

            if (checkJetSecondaryMatch(catName, null)) {
              appendJetTag(mainOnlyBtn);
            }

            mainOnlyBtn.addEventListener("click", function () {
              if (mainOnlyBtn.disabled) return;
              var res = toggleSecondary(annotationSelection, { category: catName, subtype: null });
              if (res.error) {
                if (secTipTimer) clearTimeout(secTipTimer);
                secTipEl.textContent = res.error;
                secTipTimer = setTimeout(function () {
                  secTipEl.textContent = "";
                  secTipTimer = null;
                }, 2000);
              } else {
                annotationSelection = res.selection;
                hasUnsavedAnnotation = true;
                updateSecondaryButtons();
              }
            });

            secondaryButtons.push(mainOnlyBtn);
            gRow.appendChild(mainOnlyBtn);

            // 细分按钮
            var subtypes = CATEGORIES[catName] || [];
            for (var st = 0; st < subtypes.length; st++) {
              (function (subName) {
                var subBtn = document.createElement("button");
                subBtn.type = "button";
                subBtn.className = "jet-opt-btn";
                subBtn.setAttribute("data-category", catName);
                subBtn.setAttribute("data-subtype", subName);

                var sLabel = document.createElement("span");
                sLabel.textContent = subName;
                subBtn.appendChild(sLabel);

                if (checkJetSecondaryMatch(catName, subName)) {
                  appendJetTag(subBtn);
                }

                subBtn.addEventListener("click", function () {
                  if (subBtn.disabled) return;
                  var res = toggleSecondary(annotationSelection, { category: catName, subtype: subName });
                  if (res.error) {
                    if (secTipTimer) clearTimeout(secTipTimer);
                    secTipEl.textContent = res.error;
                    secTipTimer = setTimeout(function () {
                      secTipEl.textContent = "";
                      secTipTimer = null;
                    }, 2000);
                  } else {
                    annotationSelection = res.selection;
                    hasUnsavedAnnotation = true;
                    updateSecondaryButtons();
                  }
                });

                secondaryButtons.push(subBtn);
                gRow.appendChild(subBtn);
              })(subtypes[st]);
            }

            groupDiv.appendChild(gRow);
            secContent.appendChild(groupDiv);
          })(catList[c2]);
        }

        secContent.appendChild(secTipEl);
        secGrp.appendChild(secContent);
        formEl.appendChild(secGrp);
        updateSecondaryButtons();

        // 通用单选按钮组生成器
        function createSimpleBtnGroup(groupTitle, options, fieldName, jetVal, extraClassPrefix) {
          var grp = document.createElement("div");
          grp.className = "jet-form-group";
          var gt = document.createElement("div");
          gt.className = "jet-form-title";
          gt.textContent = groupTitle;
          grp.appendChild(gt);

          var row = document.createElement("div");
          row.className = "jet-btn-row";
          var buttons = [];

          for (var i = 0; i < options.length; i++) {
            (function (opt) {
              var val = typeof opt === "string" ? opt : opt.value;
              var label = typeof opt === "string" ? opt : opt.label;
              var b = document.createElement("button");
              b.type = "button";
              var currentVal = annotationSelection[fieldName];
              var extraCls = extraClassPrefix ? (extraClassPrefix + "-" + val) : "";
              b.className = "jet-opt-btn" + (currentVal === val ? " active" : "") + (extraCls ? (" " + extraCls) : "");
              b.setAttribute("data-val", val);

              var span = document.createElement("span");
              span.textContent = label;
              b.appendChild(span);

              if (jetVal === val) {
                appendJetTag(b);
              }

              b.addEventListener("click", function () {
                annotationSelection = toggleOption(annotationSelection, fieldName, val);
                hasUnsavedAnnotation = true;
                var nextVal = annotationSelection[fieldName];

                for (var j = 0; j < buttons.length; j++) {
                  var btnEl = buttons[j];
                  if (btnEl.getAttribute("data-val") === nextVal) {
                    btnEl.classList.add("active");
                  } else {
                    btnEl.classList.remove("active");
                  }
                }
              });

              buttons.push(b);
              row.appendChild(b);
            })(options[i]);
          }
          grp.appendChild(row);
          return grp;
        }

        // 3. 销售成分
        formEl.appendChild(
          createSimpleBtnGroup("销售成分：", SALES_LEVEL, "sales_level", jetSalesLevel)
        );

        // 4. 经验是否满足
        formEl.appendChild(
          createSimpleBtnGroup("经验是否满足：", EXPERIENCE_FIT, "experience_fit", jetExpFit)
        );

        // 5. 工作强度
        formEl.appendChild(
          createSimpleBtnGroup("工作强度：", WORK_INTENSITY, "work_intensity", jetWorkIntensity)
        );

        // 6. 总体结论（四档，按钮带对应颜色的细边框）
        formEl.appendChild(
          createSimpleBtnGroup("总体结论（选填）：", LABEL_OPTIONS.overall, "overall", jetOverall, "verdict")
        );

        // 7. 备注
        var noteGrp = document.createElement("div");
        noteGrp.className = "jet-form-group";
        var noteTitle = document.createElement("div");
        noteTitle.className = "jet-form-title";
        noteTitle.textContent = "备注（选填，最多 100 字）：";
        noteGrp.appendChild(noteTitle);

        var noteArea = document.createElement("textarea");
        noteArea.className = "jet-note-input";
        noteArea.rows = 2;
        noteArea.maxLength = 100;
        noteArea.placeholder = "例如：本质是商务拓客";
        noteArea.value = annotationSelection.note || "";
        noteArea.addEventListener("input", function () {
          annotationSelection.note = noteArea.value;
          hasUnsavedAnnotation = true;
        });
        noteGrp.appendChild(noteArea);
        formEl.appendChild(noteGrp);

        var msgDiv = document.createElement("div");
        msgDiv.className = "jet-label-status-msg";
        if (annotationStatusText) {
          msgDiv.className = "jet-label-status-msg success";
          msgDiv.textContent = annotationStatusText;
        }

        var saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.className = "jet-save-label-btn";
        saveBtn.textContent = "保存标注";
        saveBtn.addEventListener("click", function () {
          var validated = assembleLabelPayload(annotationSelection);
          if (!validated.ok) {
            msgDiv.className = "jet-label-status-msg error";
            msgDiv.textContent = validated.error;
            return;
          }

          saveBtn.disabled = true;
          saveBtn.textContent = "保存中...";
          msgDiv.className = "jet-label-status-msg";
          msgDiv.textContent = "";

          try {
            chrome.runtime.sendMessage(
              {
                type: "save_label",
                platform_job_id: currentDetail.platform_job_id,
                label: validated.payload,
              },
              function (res) {
                saveBtn.disabled = false;
                saveBtn.textContent = "保存标注";
                if (res && res.ok) {
                  justSavedAnnotation = true;
                  hasUnsavedAnnotation = false;
                  annotationStatusText = "已保存";
                  msgDiv.className = "jet-label-status-msg success";
                  msgDiv.textContent = "已保存";
                } else {
                  msgDiv.className = "jet-label-status-msg error";
                  msgDiv.textContent =
                    "保存失败" + (res && res.error ? "（" + res.error + "）" : "");
                }
              }
            );
          } catch (err) {
            saveBtn.disabled = false;
            saveBtn.textContent = "保存标注";
            msgDiv.className = "jet-label-status-msg error";
            msgDiv.textContent = "保存异常";
          }
        });

        formEl.appendChild(saveBtn);
        formEl.appendChild(msgDiv);
        annotContainer.appendChild(formEl);
      }

      cardEl.appendChild(annotContainer);
    }

    currentShadow.appendChild(cardEl);

    // 恢复卡片 scrollTop
    if (!isCardCollapsed) {
      cardEl.scrollTop = savedCardScrollTop;
    }

    // 若页面滚动位置变了就 window.scrollTo 回原位
    var curPageX = window.scrollX !== undefined ? window.scrollX : (window.pageXOffset || 0);
    var curPageY = window.scrollY !== undefined ? window.scrollY : (window.pageYOffset || 0);
    if (curPageX !== pageX || curPageY !== pageY) {
      window.scrollTo(pageX, pageY);
    }
  }

  function renderDetailCard(detail, marksEnabled) {
    if (marksEnabled === false) {
      removeAllJetElements();
      return;
    }

    if (!detail) {
      if (currentHost) {
        currentHost.remove();
        currentHost = null;
        currentShadow = null;
        currentDetail = null;
        lastJobId = null;
        savedCardScrollTop = 0;
      }
      return;
    }

    if (!currentHost || !document.body.contains(currentHost)) {
      var existing = document.querySelector('div[data-jet="detail-card"]');
      if (existing) {
        existing.remove();
      }
      currentHost = document.createElement("div");
      currentHost.setAttribute("data-jet", "detail-card");
      currentHost.style.position = "fixed";
      applyCardPosition();
      currentHost.style.zIndex = "2147483647";

      try {
        if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
          chrome.storage.local.get(["cardSide"], function (result) {
            if (chrome.runtime && chrome.runtime.lastError) return;
            if (result && (result.cardSide === "left" || result.cardSide === "right")) {
              currentCardSide = result.cardSide;
              applyCardPosition();
            }
          });
        }
      } catch (e) {}

      // 阻止键盘事件冒泡到 BOSS 页面，防止在备注框输入时触发页面快捷键
      currentHost.addEventListener("keydown", function (e) {
        e.stopPropagation();
      });
      currentHost.addEventListener("keyup", function (e) {
        e.stopPropagation();
      });
      currentHost.addEventListener("keypress", function (e) {
        e.stopPropagation();
      });

      // 阻止鼠标/指针/滚轮事件传到 BOSS 页面，防止页面跳动或触发 BOSS 页面滚动处理
      var eventsToStop = [
        "click",
        "mousedown",
        "mouseup",
        "pointerdown",
        "pointerup",
        "wheel",
      ];
      for (var evIdx = 0; evIdx < eventsToStop.length; evIdx++) {
        currentHost.addEventListener(eventsToStop[evIdx], function (e) {
          e.stopPropagation();
        });
      }

      currentShadow = currentHost.attachShadow({ mode: "closed" });
      document.body.appendChild(currentHost);
    }

    currentDetail = detail;
    currentMarksEnabled = marksEnabled;

    var newJobId = detail.platform_job_id || null;
    if (newJobId !== lastJobId) {
      lastJobId = newJobId;
      savedCardScrollTop = 0;
      isCardCollapsed = false;
      isAnnotationExpanded = false;
      isSecondaryExpanded = false;
      annotationStatusText = null;
      hasUnsavedAnnotation = false;
      justSavedAnnotation = false;
      isHrNoteEditing = false;
      hrNoteDraft = "";
      isResumeReasonExpanded = false;
      pendingRender = null;
      focusHrInput = false;

      if (detail.user_label && typeof detail.user_label === "object") {
        annotationSelection = {
          work_type: detail.user_label.work_type || null,
          work_subtype: detail.user_label.work_subtype || null,
          secondary_work_types: Array.isArray(detail.user_label.secondary_work_types)
            ? detail.user_label.secondary_work_types.slice()
            : [],
          sales_level: detail.user_label.sales_level || null,
          experience_fit: detail.user_label.experience_fit || null,
          work_intensity: detail.user_label.work_intensity || null,
          overall: detail.user_label.overall || null,
          note: detail.user_label.note || "",
        };
      } else {
        annotationSelection = {
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
    } else {
      // 相同岗位更新
      var prevCard = currentShadow ? currentShadow.querySelector(".jet-card") : null;
      if (prevCard) {
        savedCardScrollTop = prevCard.scrollTop;
      }

      if (justSavedAnnotation) {
        justSavedAnnotation = false;
        hasUnsavedAnnotation = false;
        if (detail.user_label && typeof detail.user_label === "object") {
          annotationSelection = {
            work_type: detail.user_label.work_type || null,
            work_subtype: detail.user_label.work_subtype || null,
            secondary_work_types: Array.isArray(detail.user_label.secondary_work_types)
              ? detail.user_label.secondary_work_types.slice()
              : [],
            sales_level: detail.user_label.sales_level || null,
            experience_fit: detail.user_label.experience_fit || null,
            work_intensity: detail.user_label.work_intensity || null,
            overall: detail.user_label.overall || null,
            note: detail.user_label.note || "",
          };
        }
      } else {
        // 未保存的选择在收到同一岗位的 render_state 时保留（不要被重置）
        if (!hasUnsavedAnnotation && detail.user_label && typeof detail.user_label === "object") {
          annotationSelection = {
            work_type: detail.user_label.work_type || null,
            work_subtype: detail.user_label.work_subtype || null,
            secondary_work_types: Array.isArray(detail.user_label.secondary_work_types)
              ? detail.user_label.secondary_work_types.slice()
              : [],
            sales_level: detail.user_label.sales_level || null,
            experience_fit: detail.user_label.experience_fit || null,
            work_intensity: detail.user_label.work_intensity || null,
            overall: detail.user_label.overall || null,
            note: detail.user_label.note || "",
          };
        }
      }
    }

    buildCardDom();
  }

  function updateMarksPositions() {
    if (currentMarksEnabled === false) {
      return;
    }

    if (
      !currentListMarks ||
      typeof currentListMarks !== "object" ||
      Object.keys(currentListMarks).length === 0
    ) {
      if (currentMarksHost) {
        currentMarksHost.remove();
        currentMarksHost = null;
        currentMarksShadow = null;
      }
      return;
    }

    // 取点前临时把色条设为 pointer-events: none，避免命中旧色条
    if (currentMarksShadow) {
      var oldStrips = currentMarksShadow.querySelectorAll(".jet-mark-strip");
      for (var sIdx = 0; sIdx < oldStrips.length; sIdx++) {
        oldStrips[sIdx].style.pointerEvents = "none";
      }
    }

    function getPageElementAt(x, y) {
      if (typeof document.elementsFromPoint === "function") {
        var hits = document.elementsFromPoint(x, y);
        if (hits && hits.length > 0) {
          for (var hIdx = 0; hIdx < hits.length; hIdx++) {
            var h = hits[hIdx];
            if (!h) continue;
            if (h.hasAttribute && h.hasAttribute("data-jet")) continue;
            if (h.closest && h.closest("[data-jet]")) continue;
            return h;
          }
        }
      }
      if (typeof document.elementFromPoint === "function") {
        var hit = document.elementFromPoint(x, y);
        if (hit) {
          if (hit.hasAttribute && hit.hasAttribute("data-jet")) return null;
          if (hit.closest && hit.closest("[data-jet]")) return null;
          return hit;
        }
      }
      return null;
    }

    var viewport = {
      width:
        (typeof window !== "undefined" && window.innerWidth) ||
        (document.documentElement && document.documentElement.clientWidth) ||
        0,
      height:
        (typeof window !== "undefined" && window.innerHeight) ||
        (document.documentElement && document.documentElement.clientHeight) ||
        0,
    };

    var rawCards = [];
    try {
      var links = document.querySelectorAll('a[href*="/job_detail/"]');
      for (var lIdx = 0; lIdx < links.length; lIdx++) {
        var link = links[lIdx];
        var href = link.getAttribute("href") || link.href || "";
        var jobId = jobIdFromHref(href);
        if (!jobId) continue;

        var cardEl = findCardElementForLink(link, jobId);
        var bRect = cardEl.getBoundingClientRect();
        rawCards.push({
          id: jobId,
          el: cardEl,
          rect: {
            left: bRect.left,
            top: bRect.top,
            right: bRect.right,
            bottom: bRect.bottom,
            width: bRect.width,
            height: bRect.height,
          },
        });
      }
    } catch (e) {
      rawCards = [];
    }

    if (rawCards.length === 0) {
      if (currentMarksHost) {
        currentMarksHost.remove();
        currentMarksHost = null;
        currentMarksShadow = null;
      }
      return;
    }

    // 按岗位 ID 去重：同一 ID 只保留面积最大的那个元素
    var dedupeMap = {};
    var dedupeOrder = [];
    for (var cIdx = 0; cIdx < rawCards.length; cIdx++) {
      var cCandidate = rawCards[cIdx];
      var area = (cCandidate.rect.width || 0) * (cCandidate.rect.height || 0);
      if (!dedupeMap[cCandidate.id]) {
        dedupeMap[cCandidate.id] = { card: cCandidate, area: area };
        dedupeOrder.push(cCandidate.id);
      } else if (area > dedupeMap[cCandidate.id].area) {
        dedupeMap[cCandidate.id] = { card: cCandidate, area: area };
      }
    }

    var allCards = [];
    for (var oIdx = 0; oIdx < dedupeOrder.length; oIdx++) {
      allCards.push(dedupeMap[dedupeOrder[oIdx]].card);
    }

    var validCardsToPlan = [];
    for (var aIdx = 0; aIdx < allCards.length; aIdx++) {
      var cardItem = allCards[aIdx];
      if (!currentListMarks[cardItem.id]) {
        continue;
      }

      var rect = cardItem.rect;
      var isOutside =
        rect.bottom <= 0 ||
        rect.top >= viewport.height ||
        rect.right <= 0 ||
        rect.left >= viewport.width;
      if (isOutside) {
        continue;
      }

      var cardEl = cardItem.el;
      var midX = rect.left + rect.width / 2;
      var range = visibleRange(rect, viewport.height, function (y) {
        var hit = getPageElementAt(midX, y);
        return Boolean(hit && (cardEl.contains(hit) || (hit.hasAttribute && hit.hasAttribute("data-jet"))));
      });

      // 找不到（整张被盖或不在视口）则这张卡片不画任何标记
      if (!range) {
        continue;
      }

      var titleTop = findTitleTop(cardEl);

      validCardsToPlan.push({
        id: cardItem.id,
        rect: rect,
        titleTop: titleTop,
        visibleRange: range,
      });
    }

    var planned = planMarks(validCardsToPlan, currentListMarks, currentMarksEnabled, viewport);

    if (planned.length === 0) {
      if (currentMarksHost) {
        currentMarksHost.remove();
        currentMarksHost = null;
        currentMarksShadow = null;
      }
      return;
    }

    if (!currentMarksHost || !document.body.contains(currentMarksHost)) {
      var existing = document.querySelector('div[data-jet="list-marks"]');
      if (existing) {
        existing.remove();
      }
      currentMarksHost = document.createElement("div");
      currentMarksHost.setAttribute("data-jet", "list-marks");
      currentMarksHost.style.position = "fixed";
      currentMarksHost.style.left = "0px";
      currentMarksHost.style.top = "0px";
      currentMarksHost.style.width = "0px";
      currentMarksHost.style.height = "0px";
      currentMarksHost.style.zIndex = "2147483646";
      currentMarksHost.style.pointerEvents = "none";
      currentMarksShadow = currentMarksHost.attachShadow({ mode: "closed" });
      document.body.appendChild(currentMarksHost);
    }

    while (currentMarksShadow.firstChild) {
      currentMarksShadow.removeChild(currentMarksShadow.firstChild);
    }

    var styleEl = document.createElement("style");
    styleEl.textContent = [
      ".jet-mark-strip {",
      "  position: fixed;",
      "  box-sizing: border-box;",
      "  border-radius: 1px;",
      "}",
      ".jet-mark-strip.green, .jet-mark-strip.apply { background: #2e7d32; }",
      ".jet-mark-strip.blue, .jet-mark-strip.try { background: #1565c0; }",
      ".jet-mark-strip.yellow, .jet-mark-strip.check { background: #f9a825; }",
      ".jet-mark-strip.red, .jet-mark-strip.skip { background: #c62828; }",
      ".jet-mark-strip.neutral { background: #9e9e9e; }",
      ".jet-mark-strip.slate, .jet-mark-strip.hint { background: #607d8b; }",
      ".jet-mark-strip.stale {",
      "  background: transparent !important;",
      "  opacity: 0.55;",
      "}",
      ".jet-mark-strip.stale.green, .jet-mark-strip.stale.apply { border-left: 4px dashed #2e7d32; }",
      ".jet-mark-strip.stale.blue, .jet-mark-strip.stale.try { border-left: 4px dashed #1565c0; }",
      ".jet-mark-strip.stale.yellow, .jet-mark-strip.stale.check { border-left: 4px dashed #f9a825; }",
      ".jet-mark-strip.stale.red, .jet-mark-strip.stale.skip { border-left: 4px dashed #c62828; }",
      ".jet-mark-strip.stale.neutral { border-left: 4px dashed #9e9e9e; }",
      ".jet-mark-strip.stale.slate, .jet-mark-strip.stale.hint { border-left: 4px dashed #607d8b; }",
      ".jet-mark-strip.open { background: #10b981; }",
      ".jet-mark-strip.prejudge {",
      "  background: transparent !important;",
      "  opacity: 0.75;",
      "}",
      ".jet-mark-strip.prejudge.open { border-left: 4px dashed #10b981; }",
      ".jet-mark-strip.prejudge.neutral { border-left: 4px dashed #64748b; }",
      ".jet-mark-strip.prejudge.skip { border-left: 4px dashed #f59e0b; }",
      ".jet-mark-label {",
      "  position: fixed;",
      "  box-sizing: border-box;",
      "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "  font-size: 11px;",
      "  font-weight: bold;",
      "  height: 14px;",
      "  line-height: 12px;",
      "  padding: 0 4px;",
      "  border-radius: 2px;",
      "  pointer-events: none;",
      "  user-select: none;",
      "  white-space: nowrap;",
      "  overflow: hidden;",
      "  text-overflow: ellipsis;",
      "  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);",
      "  border: 1px solid transparent;",
      "}",
      ".jet-mark-label.green, .jet-mark-label.apply {",
      "  background: #e8f5e9;",
      "  color: #2e7d32;",
      "  border-color: #81c784;",
      "}",
      ".jet-mark-label.blue, .jet-mark-label.try {",
      "  background: #e3f2fd;",
      "  color: #1565c0;",
      "  border-color: #64b5f6;",
      "}",
      ".jet-mark-label.yellow, .jet-mark-label.check {",
      "  background: #fffde7;",
      "  color: #5d4037;",
      "  border-color: #f9a825;",
      "}",
      ".jet-mark-label.red, .jet-mark-label.skip {",
      "  background: #ffebee;",
      "  color: #c62828;",
      "  border-color: #e57373;",
      "}",
      ".jet-mark-label.neutral {",
      "  background: #f5f5f5;",
      "  color: #666666;",
      "  border-color: #cccccc;",
      "}",
      ".jet-mark-label.slate, .jet-mark-label.hint {",
      "  background: #eceff1;",
      "  color: #37474f;",
      "  border-color: #90a4ae;",
      "  pointer-events: auto;",
      "}",
      ".jet-mark-label.stale {",
      "  border-style: dashed;",
      "}",
      ".jet-mark-label.prejudge {",
      "  background: rgba(255, 255, 255, 0.95);",
      "  pointer-events: auto;",
      "  cursor: default;",
      "}",
      ".jet-mark-label.prejudge.open {",
      "  color: #10b981;",
      "  border: 1px solid #10b981;",
      "}",
      ".jet-mark-label.prejudge.neutral {",
      "  color: #64748b;",
      "  border: 1px solid #64748b;",
      "}",
      ".jet-mark-label.prejudge.skip {",
      "  color: #f59e0b;",
      "  border: 1px solid #f59e0b;",
      "}",
      ".jet-mark-status-badge {",
      "  position: fixed;",
      "  box-sizing: border-box;",
      "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "  font-size: 11px;",
      "  font-weight: bold;",
      "  height: 14px;",
      "  line-height: 12px;",
      "  padding: 0 4px;",
      "  border-radius: 2px;",
      "  pointer-events: none;",
      "  user-select: none;",
      "  white-space: nowrap;",
      "  overflow: hidden;",
      "  text-overflow: ellipsis;",
      "  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);",
      "  border: 1px solid transparent;",
      "}",
      ".jet-mark-status-badge.saved {",
      "  background: #fffde7;",
      "  color: #8d6e63;",
      "  border-color: #ffe082;",
      "}",
      ".jet-mark-status-badge.applied {",
      "  background: #e8f5e9;",
      "  color: #2e7d32;",
      "  border-color: #a5d6a7;",
      "}",
      ".jet-mark-status-badge.skipped {",
      "  background: #f5f5f5;",
      "  color: #616161;",
      "  border-color: #e0e0e0;",
      "}",
    ].join("\n");
    currentMarksShadow.appendChild(styleEl);

    var TONE_COLORS = {
      green: "#2e7d32",
      apply: "#2e7d32",
      blue: "#1565c0",
      try: "#1565c0",
      yellow: "#f9a825",
      check: "#f9a825",
      red: "#c62828",
      skip: "#c62828",
      neutral: "#9e9e9e",
      slate: "#607d8b",
      hint: "#607d8b",
      open: "#10b981",
    };

    var PREJUDGE_COLORS = {
      open: "#10b981",
      neutral: "#64748b",
      skip: "#f59e0b",
    };

    for (var mIdx = 0; mIdx < planned.length; mIdx++) {
      var item = planned[mIdx];
      var toneCls = item.tone || "neutral";
      var toneColor = item.is_prejudge
        ? (PREJUDGE_COLORS[toneCls] || "#64748b")
        : (TONE_COLORS[toneCls] || "#9e9e9e");

      if (item.strip) {
        var stripEl = document.createElement("div");
        stripEl.className = "jet-mark-strip " + toneCls + (item.stale ? " stale" : "") + (item.is_prejudge ? " prejudge" : "");
        stripEl.style.left = item.strip.left + "px";
        stripEl.style.top = item.strip.top + "px";
        stripEl.style.width = item.strip.width + "px";
        stripEl.style.height = item.strip.height + "px";

        if (item.stale) {
          stripEl.style.borderLeft = item.strip.width + "px dashed " + toneColor;
          stripEl.style.background = "transparent";
          stripEl.style.opacity = "0.55";
        } else if (item.is_prejudge) {
          stripEl.style.borderLeft = item.strip.width + "px dashed " + toneColor;
          stripEl.style.background = "transparent";
          stripEl.style.opacity = "0.75";
        } else {
          stripEl.style.background = toneColor;
        }

        if (!item.label || !item.label.visible) {
          stripEl.style.pointerEvents = "auto";
          stripEl.title = item.strip.title || item.text;
        } else {
          stripEl.style.pointerEvents = "none";
          stripEl.title = "";
        }

        currentMarksShadow.appendChild(stripEl);
      }

      var labelEl = null;
      if (item.label && item.label.visible) {
        labelEl = document.createElement("div");
        labelEl.className = "jet-mark-label " + toneCls + (item.stale ? " stale" : "") + (item.is_prejudge ? " prejudge" : "");
        labelEl.style.left = item.label.left + "px";
        labelEl.style.top = item.label.top + "px";
        labelEl.style.height = item.label.height + "px";
        labelEl.style.lineHeight = (item.label.height - 2) + "px";
        labelEl.style.fontSize = item.label.fontSize + "px";
        labelEl.style.maxWidth = item.label.maxWidth + "px";
        labelEl.textContent = item.text;
        if (item.is_prejudge) {
          labelEl.style.pointerEvents = "auto";
          labelEl.style.cursor = "default";
        }
        if (item.label && item.label.title) {
          labelEl.title = item.label.title;
        }
        currentMarksShadow.appendChild(labelEl);
      }

      if (item.status_badge && item.status_badge.visible) {
        var badgeLeft = item.status_badge.left;
        var badgeMaxWidth = item.status_badge.maxWidth;
        var badgeVisible = item.status_badge.visible;

        if (labelEl && item.label && item.label.visible) {
          var actualWidth =
            typeof labelEl.getBoundingClientRect === "function"
              ? labelEl.getBoundingClientRect().width
              : 0;
          var badgeLayout = computeStatusBadgeLayout(item.label, actualWidth);
          badgeLeft = badgeLayout.left;
          badgeMaxWidth = badgeLayout.maxWidth;
          badgeVisible = badgeLayout.visible;
        }

        if (badgeVisible && badgeMaxWidth > 0) {
          var badgeEl = document.createElement("div");
          badgeEl.className = "jet-mark-status-badge " + item.status_badge.tone;
          badgeEl.style.left = badgeLeft + "px";
          badgeEl.style.top = item.status_badge.top + "px";
          badgeEl.style.height = item.status_badge.height + "px";
          badgeEl.style.lineHeight = (item.status_badge.height - 2) + "px";
          badgeEl.style.fontSize = item.status_badge.fontSize + "px";
          badgeEl.style.maxWidth = badgeMaxWidth + "px";
          badgeEl.textContent = item.status_badge.text;
          if (item.status_badge.title) {
            badgeEl.title = item.status_badge.title;
          }
          currentMarksShadow.appendChild(badgeEl);
        }
      }
    }
  }

  function updateListMarkStatus(jobId, statusInput) {
    if (!jobId) return;
    var statusLabels = {
      saved: "收藏",
      applied: "已投递",
      skipped: "不考虑",
    };
    var rawKey = null;
    if (statusInput && typeof statusInput === "object") {
      rawKey = statusInput.status || null;
    } else if (typeof statusInput === "string") {
      rawKey = statusInput.trim();
    }
    var validKey = rawKey && statusLabels[rawKey] ? rawKey : null;

    if (!currentListMarks || typeof currentListMarks !== "object") {
      currentListMarks = {};
    }

    var existing = currentListMarks[jobId];
    if (validKey) {
      if (existing) {
        existing.status_key = validKey;
        existing.status_label = statusLabels[validKey];
        existing.status_tone = validKey;
      } else {
        currentListMarks[jobId] = {
          verdict_label: null,
          verdict_tone: null,
          stale: false,
          judged_at: null,
          status_key: validKey,
          status_label: statusLabels[validKey],
          status_tone: validKey,
        };
      }
    } else {
      if (existing) {
        existing.status_key = null;
        existing.status_label = null;
        existing.status_tone = null;
        if (!existing.verdict_label && !existing.is_hint && !existing.judged_at) {
          delete currentListMarks[jobId];
        }
      }
    }

    updateMarksPositions();
  }

  function scheduleMarksUpdate() {
    if (rafPending) return;
    rafPending = true;
    if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(function () {
        rafPending = false;
        updateMarksPositions();
      });
    } else {
      rafPending = false;
      updateMarksPositions();
    }
  }

  if (typeof window !== "undefined") {
    window.addEventListener("scroll", scheduleMarksUpdate, { capture: true, passive: true });
    window.addEventListener("resize", scheduleMarksUpdate, { passive: true });
  }

  // 监听来自 SW 的渲染消息
  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener(function (message) {
      if (message && message.type === "render_state") {
        if (message.marks_enabled === false) {
          currentMarksEnabled = false;
          removeAllJetElements();
          return;
        }

        currentMarksEnabled = true;

        if (message.list_marks && typeof message.list_marks === "object") {
          currentListMarks = message.list_marks;
        }

        // detail 可能为 null（只刷新列表标记时）
        if (message.detail !== undefined) {
          var incomingJobId = message.detail && message.detail.platform_job_id ? message.detail.platform_job_id : null;
          if (isHrNoteEditing && (incomingJobId === null || incomingJobId === lastJobId)) {
            // 正在编辑 HR 实际情况：同一岗位（或只刷新列表标记）的更新先暂存，不重建卡片，保留编辑框内容与光标
            if (message.detail) {
              pendingRender = { detail: message.detail, marksEnabled: message.marks_enabled };
            }
          } else {
            pendingRender = null;
            renderDetailCard(message.detail, message.marks_enabled);
          }
        }

        updateMarksPositions();
      }
    });
  }

  function isChatPage() {
    return (
      typeof location !== "undefined" &&
      Boolean(location.pathname && location.pathname.startsWith("/web/geek/chat"))
    );
  }

  var lastChatTopText = null;
  var chatDebounceTimer = null;
  var chatMaxWaitTimer = null;
  var isChatTopChangingSent = false;
  var lastChatMessagesSignature = null;
  var chatMessagesDebounceTimer = null;

  function computeChatMessagesSignature() {
    var rawItems = document.querySelectorAll(".chat-message .message-item, li.message-item");
    var validItems = [];
    for (var i = 0; i < rawItems.length; i++) {
      var item = rawItems[i];
      var cls = typeof item.className === "string" ? item.className : (item.getAttribute ? item.getAttribute("class") || "" : "");
      if (cls && cls.indexOf("item-system") !== -1) {
        continue;
      }
      validItems.push(item);
    }
    var count = validItems.length;
    if (count === 0) {
      return "0|false|";
    }
    var lastItem = validItems[count - 1];
    var lastCls = typeof lastItem.className === "string" ? lastItem.className : (lastItem.getAttribute ? lastItem.getAttribute("class") || "" : "");
    var isMyself = Boolean(lastCls && lastCls.indexOf("item-myself") !== -1);
    var text = (lastItem.textContent || "").trim();
    return count + "|" + isMyself + "|" + text;
  }

  function triggerChatMessagesChanged() {
    if (chatMessagesDebounceTimer) {
      clearTimeout(chatMessagesDebounceTimer);
      chatMessagesDebounceTimer = null;
    }
    notify({ type: "chat_messages_changed" });
  }

  function checkChatMessagesChanged() {
    if (!isChatPage()) {
      return;
    }
    var sig = computeChatMessagesSignature();
    if (lastChatMessagesSignature === null) {
      lastChatMessagesSignature = sig;
      return;
    }
    if (sig !== lastChatMessagesSignature) {
      lastChatMessagesSignature = sig;
      if (chatMessagesDebounceTimer) {
        clearTimeout(chatMessagesDebounceTimer);
      }
      chatMessagesDebounceTimer = setTimeout(triggerChatMessagesChanged, 800);
    }
  }

  function getChatTopText() {
    var el = document.querySelector(".chat-position-content");
    if (el) {
      var topContainer = el.closest(".chat-conversation-top, .conversation-top, .chat-top");
      if (topContainer) {
        return (topContainer.textContent || "").trim();
      }
      return (el.textContent || "").trim();
    }
    var fallback = document.querySelector(".chat-conversation-top, .conversation-top, .chat-top, .position-name");
    return fallback ? (fallback.textContent || "").trim() : "";
  }

  function triggerChatTopChanged() {
    if (chatDebounceTimer) {
      clearTimeout(chatDebounceTimer);
      chatDebounceTimer = null;
    }
    if (chatMaxWaitTimer) {
      clearTimeout(chatMaxWaitTimer);
      chatMaxWaitTimer = null;
    }
    if (chatMessagesDebounceTimer) {
      clearTimeout(chatMessagesDebounceTimer);
      chatMessagesDebounceTimer = null;
    }
    lastChatMessagesSignature = null;
    isChatTopChangingSent = false;
    notify({ type: "chat_top_changed" });
  }

  function checkChatTopChanged() {
    var text = getChatTopText();
    if (!text) {
      return;
    }
    if (text !== lastChatTopText) {
      lastChatTopText = text;
      if (chatMessagesDebounceTimer) {
        clearTimeout(chatMessagesDebounceTimer);
        chatMessagesDebounceTimer = null;
      }
      lastChatMessagesSignature = null;
      if (!isChatTopChangingSent) {
        isChatTopChangingSent = true;
        notify({ type: "chat_top_changing" });
      }
      if (chatDebounceTimer) {
        clearTimeout(chatDebounceTimer);
      }
      chatDebounceTimer = setTimeout(triggerChatTopChanged, 150);
      if (!chatMaxWaitTimer) {
        chatMaxWaitTimer = setTimeout(triggerChatTopChanged, 500);
      }
    }
  }

  // 页面加载完成，先通知一次
  notify({ type: "page_changed", reason: "load" });
  if (isChatPage()) {
    checkChatTopChanged();
    checkChatMessagesChanged();
  }

  function triggerPageChanged() {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    if (maxWaitTimer) {
      clearTimeout(maxWaitTimer);
      maxWaitTimer = null;
    }
    var waitedMs = firstMutationAt !== null ? Math.max(0, Date.now() - firstMutationAt) : 0;
    firstMutationAt = null;
    notify({ type: "page_changed", waited_ms: waitedMs });
    updateMarksPositions();
  }

  // MutationObserver 观察子节点变动：忽略 [data-jet] 内部或本身的变动，防抖触发 page_changed
  function startObserver() {
    var target =
      document.querySelector("#wrap") ||
      document.body ||
      document.documentElement;

    if (!target) {
      return;
    }

    var observer = new MutationObserver(function (mutationsList) {
      if (isChatPage()) {
        checkChatTopChanged();
        checkChatMessagesChanged();
      }

      var hasExternalMutation = false;
      for (var i = 0; i < mutationsList.length; i++) {
        var mutation = mutationsList[i];
        if (mutation.type === "childList" && !isJetMutation(mutation)) {
          hasExternalMutation = true;
          break;
        }
      }

      if (!hasExternalMutation) {
        return;
      }

      if (firstMutationAt === null) {
        firstMutationAt = Date.now();
      }

      if (debounceTimer) {
        clearTimeout(debounceTimer);
      }
      debounceTimer = setTimeout(triggerPageChanged, 150);

      if (!maxWaitTimer) {
        maxWaitTimer = setTimeout(triggerPageChanged, 500);
      }
    });

    if (isChatPage()) {
      observer.observe(target, { childList: true, subtree: true, characterData: true });
    } else {
      observer.observe(target, { childList: true, subtree: true });
    }
  }

  function clearAutoApplyToast() {
    if (autoApplyToastTimer) {
      clearTimeout(autoApplyToastTimer);
      autoApplyToastTimer = null;
    }
    if (autoApplyToastEl) {
      autoApplyToastEl.remove();
      autoApplyToastEl = null;
    }
  }

  function showAutoApplySuccessToast(prevStatus, targetJobId) {
    clearAutoApplyToast();

    var toast = document.createElement("div");
    toast.setAttribute("data-jet", "auto-apply-toast");
    toast.className = "jet-auto-apply-toast";
    toast.style.cssText = [
      "position: fixed;",
      "top: 24px;",
      "right: 24px;",
      "z-index: 2147483647;",
      "background: #323232;",
      "color: #ffffff;",
      "padding: 8px 14px;",
      "border-radius: 6px;",
      "box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);",
      "font-size: 13px;",
      "line-height: 1.4;",
      "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "display: flex;",
      "align-items: center;",
      "gap: 12px;",
      "pointer-events: auto;",
    ].join(" ");

    var textSpan = document.createElement("span");
    textSpan.textContent = "已标为「已投递」";
    toast.appendChild(textSpan);

    var undoBtn = document.createElement("button");
    undoBtn.type = "button";
    undoBtn.textContent = "撤销";
    undoBtn.style.cssText = [
      "background: none;",
      "border: 1px solid rgba(255, 255, 255, 0.5);",
      "color: #ffffff;",
      "border-radius: 4px;",
      "padding: 2px 8px;",
      "font-size: 12px;",
      "cursor: pointer;",
    ].join(" ");

    undoBtn.addEventListener("click", function () {
      if (autoApplyToastTimer) {
        clearTimeout(autoApplyToastTimer);
        autoApplyToastTimer = null;
      }
      undoBtn.disabled = true;
      undoBtn.textContent = "撤销中...";

      try {
        chrome.runtime.sendMessage(
          {
            type: "set_job_status",
            platform_job_id: targetJobId,
            status: prevStatus,
          },
          function (res) {
            if (chrome.runtime && chrome.runtime.lastError) {
              textSpan.textContent = "撤销失败";
              undoBtn.remove();
              autoApplyToastTimer = setTimeout(clearAutoApplyToast, 2000);
              return;
            }
            if (res && res.ok) {
              if (currentDetail && currentDetail.platform_job_id === targetJobId) {
                currentDetail.my_status = prevStatus ? { status: prevStatus } : null;
                if (typeof activeCardRefreshStatus === "function") {
                  activeCardRefreshStatus(prevStatus);
                }
              }
              updateListMarkStatus(targetJobId, prevStatus);
              clearAutoApplyToast();
            } else {
              textSpan.textContent = "撤销失败";
              undoBtn.remove();
              autoApplyToastTimer = setTimeout(clearAutoApplyToast, 2000);
            }
          }
        );
      } catch (e) {
        textSpan.textContent = "撤销失败";
        undoBtn.remove();
        autoApplyToastTimer = setTimeout(clearAutoApplyToast, 2000);
      }
    });

    toast.appendChild(undoBtn);
    document.body.appendChild(toast);
    autoApplyToastEl = toast;

    autoApplyToastTimer = setTimeout(clearAutoApplyToast, 8000);
  }

  function showAutoApplyFailureToast() {
    clearAutoApplyToast();

    var toast = document.createElement("div");
    toast.setAttribute("data-jet", "auto-apply-toast");
    toast.className = "jet-auto-apply-toast";
    toast.style.cssText = [
      "position: fixed;",
      "top: 24px;",
      "right: 24px;",
      "z-index: 2147483647;",
      "background: #d32f2f;",
      "color: #ffffff;",
      "padding: 8px 14px;",
      "border-radius: 6px;",
      "box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);",
      "font-size: 13px;",
      "line-height: 1.4;",
      "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "pointer-events: auto;",
    ].join(" ");

    toast.textContent = "未能自动标记为已投递";
    document.body.appendChild(toast);
    autoApplyToastEl = toast;

    autoApplyToastTimer = setTimeout(clearAutoApplyToast, 3000);
  }

  function handleAutoApplyClick(event) {
    var target = event && event.target;
    if (!target || typeof target.closest !== "function") {
      return;
    }

    var pathname =
      typeof location !== "undefined" && location && location.pathname
        ? location.pathname
        : "";

    var chatBtn = null;
    if (pathname.startsWith("/web/geek/job")) {
      chatBtn = target.closest("a.op-btn.op-btn-chat");
    } else if (pathname.startsWith("/job_detail/")) {
      chatBtn = target.closest("a, button");
    }

    if (!chatBtn) {
      return;
    }

    var buttonText = chatBtn.textContent || "";
    var ka = chatBtn.getAttribute("ka") || chatBtn.ka || null;
    var currentJobId =
      currentDetail && currentDetail.platform_job_id
        ? currentDetail.platform_job_id
        : null;

    var urlJobId = null;
    var inOtherJobArea = false;
    if (pathname.startsWith("/job_detail/")) {
      urlJobId = parseJobDetailUrlId(pathname);
      inOtherJobArea = Boolean(chatBtn.closest('[class*="similar-job"], .look-job-list'));
    }

    var activeStatus = null;
    if (currentDetail && currentDetail.my_status) {
      if (
        typeof currentDetail.my_status === "object" &&
        currentDetail.my_status.status
      ) {
        activeStatus = currentDetail.my_status.status;
      } else if (typeof currentDetail.my_status === "string") {
        activeStatus = currentDetail.my_status;
      }
    }

    var decision = decideAutoApply({
      pathname: pathname,
      buttonText: buttonText,
      ka: ka,
      currentJobId: currentJobId,
      urlJobId: urlJobId,
      currentStatus: activeStatus,
      inOtherJobArea: inOtherJobArea,
    });

    if (decision.action !== "mark") {
      return;
    }

    var prevStatus = activeStatus;
    var targetJobId = currentJobId;

    try {
      chrome.runtime.sendMessage(
        {
          type: "set_job_status",
          platform_job_id: targetJobId,
          status: "applied",
        },
        function (res) {
          if (chrome.runtime && chrome.runtime.lastError) {
            showAutoApplyFailureToast();
            return;
          }
          if (res && res.ok) {
            if (currentDetail && currentDetail.platform_job_id === targetJobId) {
              currentDetail.my_status = { status: "applied" };
              if (typeof activeCardRefreshStatus === "function") {
                activeCardRefreshStatus("applied");
              }
            }
            updateListMarkStatus(targetJobId, "applied");
            showAutoApplySuccessToast(prevStatus, targetJobId);
          } else {
            showAutoApplyFailureToast();
          }
        }
      );
    } catch (e) {
      showAutoApplyFailureToast();
    }
  }

  document.addEventListener("click", handleAutoApplyClick, { capture: true, passive: true });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      startObserver();
    });
  } else {
    startObserver();
  }
})();
