/**
 * 列表页岗位标记布局纯函数与辅助模块
 * 与 content.js 保持一致
 */

/**
 * 从 /job_detail/<id>.html 提取岗位 ID，其他格式返回 null
 */
export function jobIdFromHref(href) {
  if (typeof href !== "string") {
    return null;
  }
  const match = href.match(/\/job_detail\/([^/?#.]+)\.html/);
  return match ? match[1] : null;
}

/**
 * 按岗位 ID 去重：同 id 保留面积最大者
 * cards = [{ id, rect }]
 */
export function dedupeCards(cards) {
  if (!Array.isArray(cards)) {
    return [];
  }
  const byId = new Map();
  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    if (!card || !card.id || !card.rect || typeof card.rect !== "object") continue;

    const rWidth =
      typeof card.rect.width === "number"
        ? card.rect.width
        : typeof card.rect.right === "number" && typeof card.rect.left === "number"
        ? card.rect.right - card.rect.left
        : 0;
    const rHeight =
      typeof card.rect.height === "number"
        ? card.rect.height
        : typeof card.rect.bottom === "number" && typeof card.rect.top === "number"
        ? card.rect.bottom - card.rect.top
        : 0;
    const area = Math.max(0, rWidth) * Math.max(0, rHeight);

    const existing = byId.get(card.id);
    if (!existing || area > existing.area) {
      byId.set(card.id, { card, area, index: existing ? existing.index : i });
    }
  }

  const result = Array.from(byId.values());
  result.sort((a, b) => a.index - b.index);
  return result.map((item) => item.card);
}

/**
 * 给定取点函数与卡片，求可见范围
 * isCardAt(y) -> bool
 * 从 max(rect.top, 0) + 1 起每 4px 向下用 isCardAt(y) 取点，第一个为真的 y 即 visibleTop；
 * 找不到（整张被盖或不在视口）返回 null。
 * 返回 { top, bottom, height, visibleTop, visibleBottom }
 */
export function visibleRange(rect, viewportHeight, isCardAt) {
  if (!rect || typeof rect !== "object" || typeof isCardAt !== "function") {
    return null;
  }
  const rTop = typeof rect.top === "number" ? rect.top : 0;
  const rHeight = typeof rect.height === "number" ? rect.height : 0;
  const rBottom =
    typeof rect.bottom === "number" ? rect.bottom : rTop + rHeight;
  const vpHeight =
    typeof viewportHeight === "number" ? viewportHeight : Infinity;

  const startY = Math.max(rTop, 0) + 1;
  const bottomLimit = Math.min(rBottom, vpHeight);
  if (startY >= bottomLimit) {
    return null;
  }

  let visibleTop = null;
  for (let y = startY; y < bottomLimit; y += 4) {
    if (isCardAt(y)) {
      visibleTop = y;
      break;
    }
  }

  if (visibleTop === null) {
    return null;
  }

  const height = bottomLimit - visibleTop;
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

/**
 * 估算标签文字宽度（含左右各 4px 内边距与 1px 边框，共 10px）
 * 汉字与全角标点按 1em（fontSize）计算，ASCII 与西文标点按 0.5em 计算
 */
export function estimateLabelWidth(text, fontSize = 11) {
  if (!text || typeof text !== "string") return 0;
  const fs = typeof fontSize === "number" ? fontSize : 11;
  let w = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code <= 255) {
      w += fs * 0.5;
    } else {
      w += fs;
    }
  }
  return Math.ceil(w) + 10;
}

/**
 * 规划要在页面覆盖层上渲染的标记列表（左侧外沿色条与卡片边框内左上角留白标签）
 * cards = [{ id, rect, titleTop, visibleRange?, labelAllowed? }]
 * listMarks = { [id]: { verdict_label, verdict_tone, stale, judged_at, status_key?, status_label?, status_tone? } }
 * viewport = { width, height }
 * 返回 [{ id, text, tone, stale, strip, label, status_badge }]
 * marksEnabled 为 false 返回空数组
 */
export function planMarks(cards, listMarks, marksEnabled, viewport = null) {
  if (marksEnabled === false || !marksEnabled) {
    return [];
  }
  if (!Array.isArray(cards) || !listMarks || typeof listMarks !== "object") {
    return [];
  }

  const dedupedCards = dedupeCards(cards);

  const validCards = [];
  for (let i = 0; i < dedupedCards.length; i++) {
    const card = dedupedCards[i];
    if (!card || !card.id || !card.rect || typeof card.rect !== "object") continue;

    const mark = listMarks[card.id];
    if (!mark) continue;

    const rect = card.rect;
    const rLeft = typeof rect.left === "number" ? rect.left : 0;
    const rTop = typeof rect.top === "number" ? rect.top : 0;
    const rRight =
      typeof rect.right === "number"
        ? rect.right
        : rLeft + (typeof rect.width === "number" ? rect.width : 0);
    const rBottom =
      typeof rect.bottom === "number"
        ? rect.bottom
        : rTop + (typeof rect.height === "number" ? rect.height : 0);
    const rWidth = typeof rect.width === "number" ? rect.width : (rRight - rLeft);
    const rHeight = typeof rect.height === "number" ? rect.height : (rBottom - rTop);

    if (viewport && typeof viewport === "object") {
      const vpWidth = typeof viewport.width === "number" ? viewport.width : Infinity;
      const vpHeight = typeof viewport.height === "number" ? viewport.height : Infinity;
      const isOutside =
        rBottom <= 0 || rTop >= vpHeight || rRight <= 0 || rLeft >= vpWidth;
      if (isOutside) {
        continue;
      }
    }

    validCards.push({
      id: card.id,
      mark,
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

  validCards.sort((a, b) => a.rect.top - b.rect.top);

  const planned = [];
  for (let i = 0; i < validCards.length; i++) {
    const item = validCards[i];
    const rect = item.rect;
    const mark = item.mark;
    const stale = Boolean(mark.stale);
    const rawLabel = mark.verdict_label || mark.label || "";
    let text = rawLabel;
    if (stale) {
      text = rawLabel ? `${rawLabel} · 过时` : "过时";
    }
    const tone = mark.verdict_tone || mark.tone || "neutral";

    // 标签高度、字号与留白计算（004 规则完全不变）
    let labelHeight = 14;
    let labelFontSize = 11;
    let labelTop = rect.top;
    let labelVisible = false;

    if (item.titleTop !== null && typeof item.titleTop === "number") {
      const space = item.titleTop - rect.top;
      if (space >= 14) {
        labelHeight = 14;
        labelFontSize = 11;
        labelTop = rect.top + Math.floor((space - 14) / 2);
        labelVisible = true;
      } else if (space >= 12) {
        labelHeight = 12;
        labelFontSize = 10;
        labelTop = rect.top + Math.floor((space - 12) / 2);
        labelVisible = true;
      } else {
        // space < 12：不显示标签
        labelHeight = 12;
        labelFontSize = 10;
        labelTop = rect.top;
        labelVisible = false;
      }
    }

    // 标签只在卡片顶部可见时显示：visibleTop 与 cardRect.top 相差 ≤ 2px 才显示；
    // 卡片顶部被导航栏等盖住时不显示（随卡片一起被盖住）。
    if (item.visibleRange && Math.abs(item.visibleRange.top - rect.top) > 2) {
      labelVisible = false;
    }
    if (item.labelAllowed === false) {
      labelVisible = false;
    }

    // ① 左侧外沿色条：宽 4px、与卡片同高（或可见范围同高），left = rect.left - 6（若 rect.left - 6 < 0 则 left = 0、宽 2px）
    // 仅有状态的卡片不新增左侧色条（FR-005, data-model 列表标记部分）
    const isPrejudge = Boolean(mark.is_prejudge);
    const reason = mark.reason || "";
    const tooltipText = isPrejudge && reason ? reason : text;

    const hasStrip = Boolean(rawLabel || mark.is_hint || isPrejudge || mark.judged_at);
    let strip = null;
    if (hasStrip) {
      let stripLeft = rect.left - 6;
      let stripWidth = 4;
      if (stripLeft < 0) {
        stripLeft = 0;
        stripWidth = 2;
      }

      const stripTop = item.visibleRange ? item.visibleRange.top : rect.top;
      const stripHeight = item.visibleRange ? item.visibleRange.height : rect.height;

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
    const labelLeft = rect.left + 8;
    const labelMaxWidth = rect.width - 16;

    const label = {
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
    const statusKey = mark.status_key || null;
    const statusLabel = mark.status_label || null;
    const statusTone = mark.status_tone || statusKey;
    let statusBadge = null;

    if (statusKey && statusLabel) {
      let badgeLeft = rect.left + 8;
      let badgeMaxWidth = Math.max(0, rect.width - 16);

      if (rawLabel) {
        const verdictWidth = Math.min(estimateLabelWidth(text, labelFontSize), labelMaxWidth);
        badgeLeft = labelLeft + verdictWidth + 4;
        const maxRight = rect.left + rect.width - 8;
        badgeMaxWidth = Math.max(0, maxRight - badgeLeft);
      }

      const badgeVisible = Boolean(labelVisible && badgeMaxWidth > 0);

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
      text,
      tone,
      stale,
      is_prejudge: isPrejudge,
      reason,
      strip,
      label,
      status_badge: statusBadge,
    });
  }

  return planned;
}

/**
 * 根据实际标签渲染宽度计算状态标识的布局（left、maxWidth、visible）
 *
 * 当同一卡片既有可见结论/粗筛标签又有状态标识时：
 * - 状态标识 left = label.left + actualWidth + 4；
 * - 卡片可用右边界 = (label.left - 8) + (label.maxWidth + 16) - 8；
 * - maxWidth = 卡片可用右边界 - left；
 * - 可用宽度 <= 0 时不显示状态标识。
 *
 * @param {{ left: number, maxWidth: number }} label 已有标签对象（{ left, maxWidth }）
 * @param {number} actualWidth 实际渲染宽度
 * @returns {{ left: number, maxWidth: number, visible: boolean }}
 */
export function computeStatusBadgeLayout(label, actualWidth) {
  const labelLeft = label && typeof label.left === "number" ? label.left : 0;
  const labelMaxWidth = label && typeof label.maxWidth === "number" ? label.maxWidth : 0;
  const actWidth = typeof actualWidth === "number" ? actualWidth : 0;

  const left = labelLeft + actWidth + 4;
  const maxRight = (labelLeft - 8) + (labelMaxWidth + 16) - 8;
  const availableWidth = maxRight - left;
  const visible = availableWidth > 0;
  const maxWidth = Math.max(0, availableWidth);

  return {
    left: left,
    maxWidth: maxWidth,
    visible: visible,
  };
}

/**
 * 清理所有 [data-jet] 元素，且不创建新元素（SC-006）
 */
export function removeAllJetElements(root) {
  const doc = root || (typeof document !== "undefined" ? document : null);
  if (!doc || typeof doc.querySelectorAll !== "function") {
    return [];
  }
  const elements = doc.querySelectorAll("[data-jet]");
  const removed = [];
  for (let i = 0; i < elements.length; i++) {
    const el = elements[i];
    if (el && typeof el.remove === "function") {
      el.remove();
      removed.push(el);
    }
  }
  return removed;
}
