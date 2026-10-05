import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {
  jobIdFromHref,
  dedupeCards,
  visibleRange,
  estimateLabelWidth,
  planMarks,
  removeAllJetElements,
  computeStatusBadgeLayout,
} from "../src/marks-layout.js";

test("jobIdFromHref extracts id from valid /job_detail/<id>.html hrefs", () => {
  // 1. 相对路径
  assert.equal(jobIdFromHref("/job_detail/4a8c9e2b1f.html"), "4a8c9e2b1f");
  assert.equal(jobIdFromHref("/job_detail/12345.html"), "12345");

  // 2. 绝对完整 URL（含查询参数）
  assert.equal(
    jobIdFromHref(
      "https://www.zhipin.com/job_detail/9f2b8a7c1d3e.html?lid=123&securityId=456",
    ),
    "9f2b8a7c1d3e",
  );

  // 3. 包含 hash 锚点
  assert.equal(
    jobIdFromHref(
      "https://www.zhipin.com/job_detail/job_abc-123.html#overview",
    ),
    "job_abc-123",
  );

  // 4. 其他合法字符构成的 id
  assert.equal(jobIdFromHref("/job_detail/job_99_XYZ.html"), "job_99_XYZ");
});

test("jobIdFromHref returns null for invalid or non-detail hrefs", () => {
  // 缺少 id
  assert.equal(jobIdFromHref("/job_detail/"), null);
  assert.equal(jobIdFromHref("/job_detail/.html"), null);

  // 多层路径或非法后缀
  assert.equal(jobIdFromHref("/job_detail/sub/123.html"), null);
  assert.equal(jobIdFromHref("/job_detail/123.htm"), null);
  assert.equal(jobIdFromHref("/job_detail/123.json"), null);

  // 其他页面路由
  assert.equal(jobIdFromHref("/web/geek/job?id=123"), null);
  assert.equal(jobIdFromHref("/about.html"), null);
  assert.equal(jobIdFromHref("https://www.zhipin.com/"), null);

  // 空与非字符串
  assert.equal(jobIdFromHref(""), null);
  assert.equal(jobIdFromHref(null), null);
  assert.equal(jobIdFromHref(undefined), null);
  assert.equal(jobIdFromHref(12345), null);
  assert.equal(jobIdFromHref({}), null);
});

test("planMarks: strip position and left boundary clamping", () => {
  const cards = [
    {
      id: "job_normal",
      rect: { left: 100, top: 50, right: 300, bottom: 150, height: 100, width: 200 },
    },
    {
      id: "job_near_left",
      rect: { left: 4, top: 200, right: 204, bottom: 300, height: 100, width: 200 },
    },
    {
      id: "job_at_zero",
      rect: { left: 0, top: 350, right: 200, bottom: 450, height: 100, width: 200 },
    },
  ];
  const listMarks = {
    job_normal: { verdict_label: "适合投递", verdict_tone: "green" },
    job_near_left: { verdict_label: "可以一试", verdict_tone: "blue" },
    job_at_zero: { verdict_label: "需要确认", verdict_tone: "yellow" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 3);

  // 普通卡片：left = rect.left - 6 = 94, width = 4, top = 50, height = 100
  assert.equal(planned[0].id, "job_normal");
  assert.equal(planned[0].strip.left, 94);
  assert.equal(planned[0].strip.top, 50);
  assert.equal(planned[0].strip.width, 4);
  assert.equal(planned[0].strip.height, 100);

  // 靠左卡片 (4 - 6 = -2 < 0)：左边界夹紧为 left = 0, width = 2
  assert.equal(planned[1].id, "job_near_left");
  assert.equal(planned[1].strip.left, 0);
  assert.equal(planned[1].strip.top, 200);
  assert.equal(planned[1].strip.width, 2);
  assert.equal(planned[1].strip.height, 100);

  // 贴零卡片 (0 - 6 = -6 < 0)：左边界夹紧为 left = 0, width = 2
  assert.equal(planned[2].id, "job_at_zero");
  assert.equal(planned[2].strip.left, 0);
  assert.equal(planned[2].strip.top, 350);
  assert.equal(planned[2].strip.width, 2);
});

test("planMarks: space >= 14 has height 14, fontSize 11, vertically centered and not below title top", () => {
  // 1. space = 20 (偶数差值)
  // cardRect.top = 100, titleTop = 120 -> space = 20 >= 14
  // top = 100 + floor((20 - 14) / 2) = 100 + 3 = 103
  const cards = [
    {
      id: "card_space_20",
      rect: { left: 50, top: 100, right: 350, bottom: 200, width: 300, height: 100 },
      titleTop: 120,
    },
    {
      id: "card_space_14",
      rect: { left: 50, top: 250, right: 350, bottom: 350, width: 300, height: 100 },
      titleTop: 264, // space = 14
    },
  ];
  const listMarks = {
    card_space_20: { verdict_label: "适合投递", verdict_tone: "green" },
    card_space_14: { verdict_label: "可以一试", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  // space = 20
  assert.equal(planned[0].id, "card_space_20");
  assert.equal(planned[0].label.visible, true);
  assert.equal(planned[0].label.height, 14);
  assert.equal(planned[0].label.fontSize, 11);
  assert.equal(planned[0].label.top, 103);
  // 在留白内垂直居中：上方留白 103 - 100 = 3, 下方留白 120 - (103 + 14) = 3
  const spaceAbove = planned[0].label.top - 100;
  const spaceBelow = 120 - (planned[0].label.top + planned[0].label.height);
  assert.equal(spaceAbove, spaceBelow);
  // 不低于职位名顶部 (标签底边 <= 职位名顶部)
  assert.ok(planned[0].label.top + planned[0].label.height <= 120);

  // space = 14 (临界值)
  assert.equal(planned[1].id, "card_space_14");
  assert.equal(planned[1].label.visible, true);
  assert.equal(planned[1].label.height, 14);
  assert.equal(planned[1].label.fontSize, 11);
  assert.equal(planned[1].label.top, 250);
  assert.ok(planned[1].label.top + planned[1].label.height <= 264);
});

test("planMarks: 12 <= space < 14 has height 12 and fontSize 10", () => {
  const cards = [
    {
      id: "card_space_13",
      rect: { left: 50, top: 100, width: 300, height: 100 },
      titleTop: 113, // space = 13
    },
    {
      id: "card_space_12",
      rect: { left: 50, top: 250, width: 300, height: 100 },
      titleTop: 262, // space = 12
    },
  ];
  const listMarks = {
    card_space_13: { verdict_label: "适合投递", verdict_tone: "green" },
    card_space_12: { verdict_label: "可以一试", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  // space = 13: top = 100 + floor((13 - 12) / 2) = 100
  assert.equal(planned[0].label.visible, true);
  assert.equal(planned[0].label.height, 12);
  assert.equal(planned[0].label.fontSize, 10);
  assert.equal(planned[0].label.top, 100);
  assert.ok(planned[0].label.top + planned[0].label.height <= 113);

  // space = 12: top = 250 + floor(0 / 2) = 250
  assert.equal(planned[1].label.visible, true);
  assert.equal(planned[1].label.height, 12);
  assert.equal(planned[1].label.fontSize, 10);
  assert.equal(planned[1].label.top, 250);
  assert.ok(planned[1].label.top + planned[1].label.height <= 262);
});

test("planMarks: space < 12 hides label, strip is hoverable with text", () => {
  const cards = [
    {
      id: "card_space_11",
      rect: { left: 50, top: 100, width: 300, height: 100 },
      titleTop: 111, // space = 11 < 12
    },
    {
      id: "card_space_0",
      rect: { left: 50, top: 250, width: 300, height: 100 },
      titleTop: 250, // space = 0 < 12
    },
  ];
  const listMarks = {
    card_space_11: { verdict_label: "不建议投", verdict_tone: "red" },
    card_space_0: { verdict_label: "适合投递", verdict_tone: "green", stale: true },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  assert.equal(planned[0].label.visible, false);
  assert.equal(planned[0].strip.pointerEvents, "auto");
  assert.equal(planned[0].strip.title, "不建议投");
  assert.equal(planned[0].text, "不建议投");

  assert.equal(planned[1].label.visible, false);
  assert.equal(planned[1].strip.pointerEvents, "auto");
  assert.equal(planned[1].strip.title, "适合投递 · 过时");
  assert.equal(planned[1].text, "适合投递 · 过时");
});

test("planMarks: titleTop is null hides label", () => {
  const cards = [
    {
      id: "card_null_title",
      rect: { left: 50, top: 100, width: 300, height: 100 },
      titleTop: null,
    },
    {
      id: "card_no_title_prop",
      rect: { left: 50, top: 250, width: 300, height: 100 },
    },
  ];
  const listMarks = {
    card_null_title: { verdict_label: "适合投递", verdict_tone: "green" },
    card_no_title_prop: { verdict_label: "可以一试", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  assert.equal(planned[0].label.visible, false);
  assert.equal(planned[0].strip.pointerEvents, "auto");
  assert.equal(planned[0].strip.title, "适合投递");

  assert.equal(planned[1].label.visible, false);
  assert.equal(planned[1].strip.pointerEvents, "auto");
  assert.equal(planned[1].strip.title, "可以一试");
});

test("planMarks: first card displays label according to space rule", () => {
  // 第一张卡片即使靠近视口顶部（例如 top = 5），只要留白足够就正常显示标签
  const cards = [
    {
      id: "card_at_top",
      rect: { left: 50, top: 5, width: 300, height: 100 },
      titleTop: 25, // space = 20 >= 14
    },
  ];
  const listMarks = {
    card_at_top: { verdict_label: "适合投递", verdict_tone: "green" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 1);
  assert.equal(planned[0].label.visible, true);
  assert.equal(planned[0].label.height, 14);
  assert.equal(planned[0].label.fontSize, 11);
  assert.equal(planned[0].label.top, 5 + Math.floor((20 - 14) / 2)); // 8
  assert.equal(planned[0].label.left, 58); // 50 + 8
  assert.equal(planned[0].strip.pointerEvents, "none");
});

test("planMarks: label maxWidth equals rect.width - 16 and left equals rect.left + 8", () => {
  const cards = [
    {
      id: "card_w200",
      rect: { left: 40, top: 100, width: 200, height: 100 },
      titleTop: 130, // space = 30
    },
    {
      id: "card_w350",
      rect: { left: 100, top: 250, right: 450, bottom: 350, width: 350, height: 100 },
      titleTop: 270, // space = 20
    },
  ];
  const listMarks = {
    card_w200: { verdict_label: "超长标签文本测试截断省略", verdict_tone: "green" },
    card_w350: { verdict_label: "适合投递", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  assert.equal(planned[0].label.left, 48); // 40 + 8
  assert.equal(planned[0].label.maxWidth, 184); // 200 - 16

  assert.equal(planned[1].label.left, 108); // 100 + 8
  assert.equal(planned[1].label.maxWidth, 334); // 350 - 16
});

test("planMarks: label hidden when card top is occluded by navbar (diff > 2px)", () => {
  const cards = [
    {
      id: "card_occluded",
      rect: { left: 50, top: 50, width: 300, height: 100 },
      titleTop: 80, // space = 30 >= 14
      visibleRange: { top: 70, height: 80, visibleTop: 70, visibleBottom: 150 }, // diff = 20 > 2
    },
    {
      id: "card_visible_top",
      rect: { left: 50, top: 200, width: 300, height: 100 },
      titleTop: 230, // space = 30 >= 14
      visibleRange: { top: 201, height: 99, visibleTop: 201, visibleBottom: 300 }, // diff = 1 <= 2
    },
  ];
  const listMarks = {
    card_occluded: { verdict_label: "适合投递", verdict_tone: "green" },
    card_visible_top: { verdict_label: "可以一试", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  // 顶部被遮挡，标签隐藏，色条从 visibleTop 开始并可悬停
  assert.equal(planned[0].label.visible, false);
  assert.equal(planned[0].strip.top, 70);
  assert.equal(planned[0].strip.height, 80);
  assert.equal(planned[0].strip.pointerEvents, "auto");
  assert.equal(planned[0].strip.title, "适合投递");

  // 顶部可见（相差 1px <= 2px），标签正常显示
  assert.equal(planned[1].label.visible, true);
  assert.equal(planned[1].strip.top, 201);
  assert.equal(planned[1].strip.height, 99);
  assert.equal(planned[1].strip.pointerEvents, "none");
});

test("planMarks: cards sorted by rect.top", () => {
  const cards = [
    {
      id: "card_second",
      rect: { left: 50, top: 120, right: 350, bottom: 200, height: 80 },
      titleTop: 140,
    },
    {
      id: "card_first",
      rect: { left: 50, top: 20, right: 350, bottom: 100, height: 80 },
      titleTop: 40,
    },
  ];
  const listMarks = {
    card_first: { verdict_label: "适合投递", verdict_tone: "green" },
    card_second: { verdict_label: "可以一试", verdict_tone: "blue" },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);
  assert.equal(planned[0].id, "card_first");
  assert.equal(planned[1].id, "card_second");
});

test("planMarks: stale text contains '过时'", () => {
  const cards = [
    {
      id: "job_fresh",
      rect: { left: 50, top: 20, right: 350, bottom: 100, height: 80 },
    },
    {
      id: "job_stale",
      rect: { left: 50, top: 120, right: 350, bottom: 200, height: 80 },
    },
  ];
  const listMarks = {
    job_fresh: { verdict_label: "适合投递", verdict_tone: "green", stale: false },
    job_stale: { verdict_label: "不建议投", verdict_tone: "red", stale: true },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  assert.equal(planned[0].id, "job_fresh");
  assert.equal(planned[0].stale, false);
  assert.equal(planned[0].text, "适合投递");
  assert.equal(planned[0].text.includes("过时"), false);

  assert.equal(planned[1].id, "job_stale");
  assert.equal(planned[1].stale, true);
  assert.ok(planned[1].text.includes("过时"));
  assert.equal(planned[1].text, "不建议投 · 过时");
});

test("planMarks: returns empty array when marksEnabled is false", () => {
  const cards = [
    { id: "job_1", rect: { left: 10, top: 20, right: 300, bottom: 100 } },
  ];
  const listMarks = {
    job_1: { verdict_label: "适合投递", verdict_tone: "green", stale: false },
  };

  assert.deepEqual(planMarks(cards, listMarks, false), []);
  assert.deepEqual(planMarks(cards, listMarks, null), []);
  assert.deepEqual(planMarks(cards, listMarks, undefined), []);
});

test("planMarks: skips cards outside viewport when viewport is provided", () => {
  const viewport = { width: 1000, height: 800 };
  const cards = [
    {
      id: "job_visible",
      rect: { left: 50, top: 50, right: 350, bottom: 150, height: 100 },
    },
    {
      id: "job_scrolled_out",
      rect: { left: 50, top: 1200, right: 350, bottom: 1300, height: 100 },
    },
  ];
  const listMarks = {
    job_visible: {
      verdict_label: "适合投递",
      verdict_tone: "green",
      stale: false,
    },
    job_scrolled_out: {
      verdict_label: "需要确认",
      verdict_tone: "yellow",
      stale: false,
    },
  };

  const planned = planMarks(cards, listMarks, true, viewport);
  assert.equal(planned.length, 1);
  assert.equal(planned[0].id, "job_visible");
});

test("removeAllJetElements removes all [data-jet] elements and creates no new elements (SC-006)", () => {
  let createdCount = 0;
  const domElements = [
    {
      tagName: "div",
      attrs: { "data-jet": "detail-card" },
      removed: false,
      remove() {
        this.removed = true;
      },
      getAttribute(k) {
        return this.attrs[k];
      },
      setAttribute(k, v) {
        this.attrs[k] = v;
      },
    },
    {
      tagName: "div",
      attrs: { "data-jet": "list-marks" },
      removed: false,
      remove() {
        this.removed = true;
      },
      getAttribute(k) {
        return this.attrs[k];
      },
      setAttribute(k, v) {
        this.attrs[k] = v;
      },
    },
    {
      tagName: "div",
      attrs: { class: "boss-job-card" },
      removed: false,
      remove() {
        this.removed = true;
      },
      getAttribute(k) {
        return this.attrs[k];
      },
      setAttribute(k, v) {
        this.attrs[k] = v;
      },
    },
  ];

  const fakeDoc = {
    querySelectorAll(selector) {
      if (selector === "[data-jet]") {
        return domElements.filter((el) => !el.removed && el.attrs["data-jet"]);
      }
      return [];
    },
    createElement(tag) {
      createdCount++;
      return {
        tagName: tag,
        attrs: {},
        remove() {},
        setAttribute(k, v) {
          this.attrs[k] = v;
        },
      };
    },
  };

  // 初始状态应有两个 [data-jet] 元素
  assert.equal(fakeDoc.querySelectorAll("[data-jet]").length, 2);

  // 执行清理函数
  const removed = removeAllJetElements(fakeDoc);

  // 断言 1：所有 [data-jet] 元素被移除
  assert.equal(removed.length, 2);
  assert.equal(fakeDoc.querySelectorAll("[data-jet]").length, 0);
  assert.equal(domElements[0].removed, true);
  assert.equal(domElements[1].removed, true);

  // 断言 2：BOSS 页面本身的普通元素（非 data-jet）不被删除
  assert.equal(domElements[2].removed, false);

  // 断言 3：清理过程中绝不创建新元素（SC-006）
  assert.equal(createdCount, 0);
});

test("dedupeCards: keeps card with maximum area for duplicate IDs", () => {
  const cards = [
    {
      id: "job_dup",
      rect: { left: 10, top: 10, right: 60, bottom: 30, width: 50, height: 20 }, // 面积 1000
    },
    {
      id: "job_dup",
      rect: { left: 10, top: 10, right: 310, bottom: 110, width: 300, height: 100 }, // 面积 30000
    },
    {
      id: "job_dup",
      rect: { left: 10, top: 10, right: 110, bottom: 60, width: 100, height: 50 }, // 面积 5000
    },
    {
      id: "job_single",
      rect: { left: 10, top: 120, right: 310, bottom: 220, width: 300, height: 100 },
    },
  ];

  const deduped = dedupeCards(cards);
  assert.equal(deduped.length, 2);
  assert.equal(deduped[0].id, "job_dup");
  assert.equal(deduped[0].rect.width, 300);
  assert.equal(deduped[1].id, "job_single");
});

test("visibleRange: calculates visible segment and occlusion", () => {
  const rect = { left: 50, top: 40, right: 350, bottom: 140, height: 100 };

  // 1. 完全可见：从 startY = 41 起命中
  const range1 = visibleRange(rect, 800, (y) => y >= 41);
  assert.ok(range1);
  assert.equal(range1.visibleTop, 41);
  assert.equal(range1.visibleBottom, 140);
  assert.equal(range1.height, 99);

  // 2. 顶部被遮挡（如顶部导航栏盖到 y=70）
  const range2 = visibleRange(rect, 800, (y) => y >= 70);
  assert.ok(range2);
  assert.equal(range2.visibleTop, 73); // 41 + 4*8 = 73
  assert.equal(range2.visibleBottom, 140);
  assert.equal(range2.height, 67);

  // 3. 整张被盖住
  const range3 = visibleRange(rect, 800, () => false);
  assert.equal(range3, null);

  // 4. 不在视口中
  const range4 = visibleRange(rect, 30, () => true);
  assert.equal(range4, null);
});

test("planMarks: status badge layout and preservation of existing verdict label position", () => {
  const cards = [
    {
      id: "job_apply_saved",
      rect: { left: 10, top: 100, width: 300, height: 80 },
      titleTop: 120, // space = 20 >= 14
    },
    {
      id: "job_hint_applied",
      rect: { left: 20, top: 200, width: 320, height: 80 },
      titleTop: 216, // space = 16 >= 14
    },
    {
      id: "job_status_only_skipped",
      rect: { left: 30, top: 300, width: 280, height: 80 },
      titleTop: 315, // space = 15 >= 14
    },
    {
      id: "job_no_status",
      rect: { left: 10, top: 400, width: 300, height: 80 },
      titleTop: 420,
    },
  ];

  const listMarks = {
    job_apply_saved: {
      verdict_label: "适合投递",
      verdict_tone: "green",
      stale: false,
      status_key: "saved",
      status_label: "收藏",
      status_tone: "saved",
    },
    job_hint_applied: {
      is_hint: true,
      verdict_label: "粗筛：经验要求 3-5 年",
      verdict_tone: "slate",
      stale: false,
      status_key: "applied",
      status_label: "已投递",
      status_tone: "applied",
    },
    job_status_only_skipped: {
      verdict_label: null,
      verdict_tone: null,
      stale: false,
      status_key: "skipped",
      status_label: "不考虑",
      status_tone: "skipped",
    },
    job_no_status: {
      verdict_label: "不考虑",
      verdict_tone: "gray",
      stale: false,
    },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 4);

  // 1. 结论标签 + 状态标识共存：
  // 必须断言：已有 Jet 结论标签的位置与宽度计算完全不变（004 已验收）
  const item1 = planned.find((p) => p.id === "job_apply_saved");
  assert.ok(item1);
  assert.equal(item1.label.left, 10 + 8); // rect.left + 8
  assert.equal(item1.label.maxWidth, 300 - 16); // rect.width - 16
  assert.equal(item1.label.visible, true);
  assert.ok(item1.strip); // 结论卡片有色条

  // 状态标识排在结论标签右侧（间距 4px）
  const verdictWidth1 = estimateLabelWidth("适合投递", item1.label.fontSize);
  assert.equal(item1.status_badge.left, item1.label.left + verdictWidth1 + 4);
  assert.equal(item1.status_badge.maxWidth, 10 + 300 - 8 - item1.status_badge.left);
  assert.equal(item1.status_badge.visible, true);
  assert.equal(item1.status_badge.text, "收藏");
  assert.equal(item1.status_badge.tone, "saved");

  // 2. 粗筛提示 + 状态标识共存：提示标签位置同样不变
  const item2 = planned.find((p) => p.id === "job_hint_applied");
  assert.ok(item2);
  assert.equal(item2.label.left, 20 + 8); // rect.left + 8
  assert.equal(item2.label.maxWidth, 320 - 16); // rect.width - 16
  assert.equal(item2.label.visible, true);
  assert.ok(item2.strip);

  const hintWidth2 = estimateLabelWidth("粗筛：经验要求 3-5 年", item2.label.fontSize);
  assert.equal(item2.status_badge.left, item2.label.left + hintWidth2 + 4);
  assert.equal(item2.status_badge.visible, true);
  assert.equal(item2.status_badge.text, "已投递");
  assert.equal(item2.status_badge.tone, "applied");

  // 3. 仅有状态的卡片：
  // 不新增左侧色条 (strip: null)
  // 状态标识放在原标签位置 (rect.left + 8)
  const item3 = planned.find((p) => p.id === "job_status_only_skipped");
  assert.ok(item3);
  assert.equal(item3.strip, null); // 仅有状态卡片不新增左侧色条
  assert.equal(item3.label.visible, false); // 无结论标签
  assert.ok(item3.status_badge);
  assert.equal(item3.status_badge.left, 30 + 8); // rect.left + 8
  assert.equal(item3.status_badge.maxWidth, 280 - 16); // rect.width - 16
  assert.equal(item3.status_badge.visible, true);
  assert.equal(item3.status_badge.text, "不考虑");
  assert.equal(item3.status_badge.tone, "skipped");

  // 4. 无状态的卡片：status_badge 为 null
  const item4 = planned.find((p) => p.id === "job_no_status");
  assert.ok(item4);
  assert.equal(item4.status_badge, null);
});

test("planMarks: handles narrow space truncation without overlapping existing label", () => {
  // 窄卡片：宽度仅 100，结论标签"适合投递"占 54px，间距 4px
  // label.left = 18, badgeLeft = 18 + 54 + 4 = 76
  // maxRight = 10 + 100 - 8 = 102
  // badgeMaxWidth = 102 - 76 = 26 (文字将被截断，但不会覆盖已有标签)
  const cardNarrow = {
    id: "job_narrow",
    rect: { left: 10, top: 100, width: 100, height: 80 },
    titleTop: 120,
  };
  const listMarksNarrow = {
    job_narrow: {
      verdict_label: "适合投递",
      verdict_tone: "green",
      status_key: "saved",
      status_label: "收藏",
    },
  };

  const planned = planMarks([cardNarrow], listMarksNarrow, true);
  assert.equal(planned.length, 1);
  const item = planned[0];

  // 已有标签计算完全不变
  assert.equal(item.label.left, 18);
  assert.equal(item.label.maxWidth, 84);
  assert.equal(item.label.visible, true);

  // 状态标识被截断，但位于标签右侧，绝不覆盖已有标签
  const verdictWidth = estimateLabelWidth("适合投递", 11);
  assert.equal(item.status_badge.left, item.label.left + verdictWidth + 4);
  assert.ok(item.status_badge.left > item.label.left);
  assert.equal(item.status_badge.maxWidth, 26);
  assert.equal(item.status_badge.visible, true);
  // 保证 badge 范围在右边界内
  assert.ok(item.status_badge.left + item.status_badge.maxWidth <= cardNarrow.rect.left + cardNarrow.rect.width - 8);

  // 极窄卡片：空间为 0 时隐藏状态标识，已有结论标签完全不受影响
  const cardZeroSpace = {
    id: "job_zero_space",
    rect: { left: 10, top: 100, width: 60, height: 80 },
    titleTop: 120,
  };
  const plannedZero = planMarks([cardZeroSpace], {
    job_zero_space: {
      verdict_label: "适合投递",
      verdict_tone: "green",
      status_key: "applied",
      status_label: "已投递",
    },
  }, true);
  assert.equal(plannedZero.length, 1);
  assert.equal(plannedZero[0].label.visible, true);
  assert.equal(plannedZero[0].status_badge.maxWidth, 0);
  assert.equal(plannedZero[0].status_badge.visible, false);
});

test("planMarks: space < 12 or occluded card hides status badge", () => {
  const cards = [
    // space < 12
    {
      id: "job_small_space",
      rect: { left: 10, top: 100, width: 300, height: 80 },
      titleTop: 110, // space = 10 < 12
    },
    // 顶部被遮挡
    {
      id: "job_occluded",
      rect: { left: 10, top: 100, width: 300, height: 80 },
      titleTop: 120,
      visibleRange: { top: 120, height: 60 }, // diff = 20 > 2
    },
  ];

  const listMarks = {
    job_small_space: {
      status_key: "saved",
      status_label: "收藏",
    },
    job_occluded: {
      verdict_label: "适合投递",
      status_key: "applied",
      status_label: "已投递",
    },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  assert.equal(planned[0].label.visible, false);
  assert.equal(planned[0].status_badge.visible, false);
  assert.equal(planned[0].strip, null); // 纯状态卡片无色条

  assert.equal(planned[1].label.visible, false);
  assert.equal(planned[1].status_badge.visible, false);
  assert.ok(planned[1].strip); // 结论卡片保留色条
});

test("content.js: updateListMarkStatus 更新内存标记并触发重绘 (T012)", () => {
  const contentPath = new URL("../src/content.js", import.meta.url);
  const contentSrc = fs.readFileSync(contentPath, "utf8");

  const fnMatch = contentSrc.match(/function updateListMarkStatus\(jobId, statusInput\) \{[\s\S]*?\n  \}/);
  assert.ok(fnMatch, "content.js 中应存在 updateListMarkStatus 函数定义");

  let updateMarksPositionsCalled = 0;
  const sandbox = {
    currentListMarks: {
      job_existing: {
        verdict_label: "适合投递",
        verdict_tone: "green",
        stale: false,
        judged_at: "2026-09-29T10:00:00Z",
      },
    },
    updateMarksPositions: () => {
      updateMarksPositionsCalled++;
    },
  };

  vm.createContext(sandbox);
  vm.runInContext(fnMatch[0], sandbox);

  // 1. 已有结论卡片设置状态（字符串）
  sandbox.updateListMarkStatus("job_existing", "saved");
  assert.equal(updateMarksPositionsCalled, 1);
  assert.equal(sandbox.currentListMarks.job_existing.verdict_label, "适合投递");
  assert.equal(sandbox.currentListMarks.job_existing.status_key, "saved");
  assert.equal(sandbox.currentListMarks.job_existing.status_label, "收藏");
  assert.equal(sandbox.currentListMarks.job_existing.status_tone, "saved");

  // 2. 无结论卡片新增状态（对象形态）
  sandbox.updateListMarkStatus("job_new", { status: "applied" });
  assert.equal(updateMarksPositionsCalled, 2);
  assert.ok(sandbox.currentListMarks.job_new);
  assert.equal(sandbox.currentListMarks.job_new.verdict_label, null);
  assert.equal(sandbox.currentListMarks.job_new.status_key, "applied");
  assert.equal(sandbox.currentListMarks.job_new.status_label, "已投递");

  // 3. 撤销/取消状态：已有结论卡片恢复为无状态（不删除该条目）
  sandbox.updateListMarkStatus("job_existing", null);
  assert.equal(updateMarksPositionsCalled, 3);
  assert.equal(sandbox.currentListMarks.job_existing.verdict_label, "适合投递");
  assert.equal(sandbox.currentListMarks.job_existing.status_key, null);
  assert.equal(sandbox.currentListMarks.job_existing.status_label, null);

  // 4. 撤销/取消状态：纯状态卡片无结论，清除该条目
  sandbox.updateListMarkStatus("job_new", null);
  assert.equal(updateMarksPositionsCalled, 4);
  assert.equal(sandbox.currentListMarks.job_new, undefined);
});

test("computeStatusBadgeLayout: 实际宽度大于估算时状态标识右移且绝不与标签重叠 (T011)", () => {
  // 卡片 rect: left = 10, width = 300；留白足够
  // planMarks 规划的 label: left = 18, maxWidth = 284
  const label = {
    left: 18,
    maxWidth: 284,
  };

  // 按字符估算宽度为 54px，planMarks 中估算的初始位置 left = 18 + 54 + 4 = 76
  const estimatedWidth = estimateLabelWidth("适合投递", 11);
  assert.equal(estimatedWidth, 54);
  const initialBadgeLeft = label.left + estimatedWidth + 4; // 76

  // 实际渲染宽度大于估算值（例如 72px）
  const actualRenderedWidth = 72;
  const layout = computeStatusBadgeLayout(label, actualRenderedWidth);

  // 断言 1：状态标识因实际宽度大于估算值而向右移动
  assert.equal(layout.left, label.left + actualRenderedWidth + 4); // 94
  assert.ok(layout.left > initialBadgeLeft, "实际宽度大于估算时状态标识必须右移");

  // 断言 2：状态标识 left 位于标签实际右边界（left + actualWidth = 90）右侧 4px 处，绝不重叠
  assert.equal(layout.left - (label.left + actualRenderedWidth), 4);
  assert.ok(layout.left >= label.left + actualRenderedWidth, "状态标识绝不得与已有结论标签重叠");

  // 断言 3：maxWidth 按卡片右内边（rect.left + rect.width - 8 = 302）自适应缩减
  // cardRightInner = (label.left - 8) + (label.maxWidth + 16) - 8 = 302
  // maxWidth = 302 - 94 = 208
  assert.equal(layout.maxWidth, 208);
  assert.equal(layout.left + layout.maxWidth, 302);
  assert.equal(layout.visible, true);
});

test("computeStatusBadgeLayout: 空间不足时（maxWidth <= 0）不显示状态标识", () => {
  // 窄卡片：rect.left = 10, width = 120
  // label.left = 18, maxWidth = 104
  // cardRightInner = (18 - 8) + (104 + 16) - 8 = 122
  const label = {
    left: 18,
    maxWidth: 104,
  };

  // 1. 实际标签极宽（例如 105px），导致可用宽度为负：122 - (18 + 105 + 4) = -5 <= 0
  const layoutOverflow = computeStatusBadgeLayout(label, 105);
  assert.equal(layoutOverflow.maxWidth, 0);
  assert.equal(layoutOverflow.visible, false);

  // 2. 刚好卡在边界：122 - (18 + 100 + 4) = 0 <= 0
  const layoutExactZero = computeStatusBadgeLayout(label, 100);
  assert.equal(layoutExactZero.maxWidth, 0);
  assert.equal(layoutExactZero.visible, false);

  // 3. 有少量可用空间：122 - (18 + 96 + 4) = 4 > 0
  const layoutTight = computeStatusBadgeLayout(label, 96);
  assert.equal(layoutTight.left, 118);
  assert.equal(layoutTight.maxWidth, 4);
  assert.equal(layoutTight.visible, true);
});

test("content.js: 包含防遮挡实际宽度排版逻辑且与 marks-layout 纯函数保持一致", () => {
  const contentPath = new URL("../src/content.js", import.meta.url);
  const contentSrc = fs.readFileSync(contentPath, "utf8");

  // 1. 源码中包含 computeStatusBadgeLayout 定义并调用 getBoundingClientRect 读取实际宽度
  assert.ok(
    contentSrc.includes("function computeStatusBadgeLayout(label, actualWidth)"),
    "content.js 中应定义 computeStatusBadgeLayout"
  );
  assert.ok(
    contentSrc.includes("labelEl.getBoundingClientRect"),
    "content.js 渲染时应调用 getBoundingClientRect 读取实际宽度"
  );

  // 2. 沙箱中验证 content.js 的 computeStatusBadgeLayout 与 marks-layout.js 行为完全一致
  const fnMatch = contentSrc.match(/function computeStatusBadgeLayout\(label, actualWidth\) \{[\s\S]*?\n  \}/);
  assert.ok(fnMatch, "应匹配到 content.js 中的 computeStatusBadgeLayout 定义");

  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(fnMatch[0], sandbox);

  const resContent1 = sandbox.computeStatusBadgeLayout({ left: 18, maxWidth: 284 }, 72);
  const resLayout1 = computeStatusBadgeLayout({ left: 18, maxWidth: 284 }, 72);
  assert.deepEqual(JSON.parse(JSON.stringify(resContent1)), JSON.parse(JSON.stringify(resLayout1)));

  const resContent2 = sandbox.computeStatusBadgeLayout({ left: 18, maxWidth: 104 }, 95);
  const resLayout2 = computeStatusBadgeLayout({ left: 18, maxWidth: 104 }, 95);
  assert.deepEqual(JSON.parse(JSON.stringify(resContent2)), JSON.parse(JSON.stringify(resLayout2)));
});
