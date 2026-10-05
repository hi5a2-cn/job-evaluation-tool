import test from "node:test";
import assert from "node:assert/strict";
import {
  toRow,
  validateHrNoteEdit,
  getStatusRemovalNotice,
  updateCountsOnStatusChange,
  formatTotalMatchesNotice,
} from "../src/myjobs-view.js";

test("T041: HR 实际情况展示、截断标记与展开全文渲染", () => {
  const rNote = toRow({ hr_note: "前半年在深圳培训，之后常驻海外办事处。" });
  assert.equal(rNote.hr_note, "前半年在深圳培训，之后常驻海外办事处。");
  assert.equal(rNote.hr_note_content, "前半年在深圳培训，之后常驻海外办事处。");
  assert.equal(rNote.hr_note_recorded, true);
  assert.equal(rNote.can_expand_hr_note, true);

  const rEmpty = toRow({ hr_note: null });
  assert.equal(rEmpty.hr_note, null);
  assert.equal(rEmpty.hr_note_content, "未记录");
  assert.equal(rEmpty.hr_note_recorded, false);
  assert.equal(rEmpty.can_expand_hr_note, false);

  const rMissing = toRow({});
  assert.equal(rMissing.hr_note, null);
  assert.equal(rMissing.hr_note_content, "未记录");
  assert.equal(rMissing.hr_note_recorded, false);
  assert.equal(rMissing.can_expand_hr_note, false);
});

test("T041: HR 实际情况修改：纯空白禁用保存并提示清空请到岗位卡片操作", () => {
  const rEmpty = validateHrNoteEdit("");
  assert.equal(rEmpty.canSave, false);
  assert.equal(rEmpty.error, "清空请到岗位卡片操作");

  const rSpaces = validateHrNoteEdit("   \n\t  ");
  assert.equal(rSpaces.canSave, false);
  assert.equal(rSpaces.error, "清空请到岗位卡片操作");

  const rNull = validateHrNoteEdit(null);
  assert.equal(rNull.canSave, false);
  assert.equal(rNull.error, "清空请到岗位卡片操作");

  const rValid = validateHrNoteEdit("已明确无销售指标与加班要求");
  assert.equal(rValid.canSave, true);
  assert.equal(rValid.error, null);
  assert.equal(rValid.note, "已明确无销售指标与加班要求");

  const rOver = validateHrNoteEdit("a".repeat(201));
  assert.equal(rOver.canSave, false);
  assert.ok(rOver.error.includes("200"));
});

test("T041: 状态机：修改状态后生成移出标注与各标签规则", () => {
  assert.equal(
    getStatusRemovalNotice("saved", "applied", "saved"),
    "已改为 已投递，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("saved", "skipped", "saved"),
    "已改为 不考虑，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("applied", "saved", "applied"),
    "已改为 收藏，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("skipped", "saved", "skipped"),
    "已改为 收藏，切换标签或刷新后移出本列表"
  );
});

test("T041: 状态机：改回原状态移出标注消失", () => {
  assert.equal(getStatusRemovalNotice("saved", "saved", "saved"), null);
  assert.equal(getStatusRemovalNotice("applied", "applied", "applied"), null);
  assert.equal(getStatusRemovalNotice("skipped", "skipped", "skipped"), null);
});

test("T041: 状态机：取消状态生成移出标注", () => {
  assert.equal(
    getStatusRemovalNotice("saved", null, "saved"),
    "已取消状态，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("applied", null, "applied"),
    "已取消状态，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("skipped", null, "skipped"),
    "已取消状态，切换标签或刷新后移出本列表"
  );
});

test("T041: 状态机：「已标记」标签内互换状态不标注，取消状态标注移出", () => {
  assert.equal(getStatusRemovalNotice("saved", "applied", "all"), null);
  assert.equal(getStatusRemovalNotice("saved", "skipped", "all"), null);
  assert.equal(getStatusRemovalNotice("applied", "saved", "all"), null);
  assert.equal(
    getStatusRemovalNotice("saved", null, "all"),
    "已取消状态，切换标签或刷新后移出本列表"
  );
  assert.equal(
    getStatusRemovalNotice("applied", null, "all"),
    "已取消状态，切换标签或刷新后移出本列表"
  );
});

test("T041: 状态机：「全部岗位」与「最近看过」标签下改状态不移出、不标注", () => {
  assert.equal(getStatusRemovalNotice("saved", "applied", "all_jobs"), null);
  assert.equal(getStatusRemovalNotice("saved", null, "all_jobs"), null);
  assert.equal(getStatusRemovalNotice("saved", "applied", "recent"), null);
  assert.equal(getStatusRemovalNotice("saved", null, "recent"), null);
});

test("T041: 状态机：行内改状态各标签 counts 同步更新纯函数", () => {
  const initialCounts = {
    all_jobs: 10,
    all: 5,
    saved: 3,
    applied: 2,
    skipped: 0,
    recent: 8,
  };

  const updated = updateCountsOnStatusChange(initialCounts, "saved", "applied");
  assert.equal(updated.saved, 2);
  assert.equal(updated.applied, 3);
  assert.equal(updated.all, 5);
  assert.equal(updated.all_jobs, 10);

  const cancelled = updateCountsOnStatusChange(initialCounts, "saved", null);
  assert.equal(cancelled.saved, 2);
  assert.equal(cancelled.all, 4);
  assert.equal(cancelled.all_jobs, 10);

  const added = updateCountsOnStatusChange(initialCounts, null, "skipped");
  assert.equal(added.skipped, 1);
  assert.equal(added.all, 6);
  assert.equal(added.all_jobs, 10);
});

test("T041: 搜索超量提示：total_matches > 200 提示只显示了前 200 条", () => {
  assert.equal(
    formatTotalMatchesNotice(245, 200),
    "只显示了前 200 条，共 245 条"
  );
  assert.equal(formatTotalMatchesNotice(200, 200), null);
  assert.equal(formatTotalMatchesNotice(15, 200), null);
  assert.equal(formatTotalMatchesNotice(null, 200), null);
});

test("T042-T045: myjobs.html 结构与组件标记合规检查", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const { fileURLToPath } = await import("node:url");

  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  const htmlPath = path.resolve(__dirname, "../src/myjobs.html");
  const html = fs.readFileSync(htmlPath, "utf-8");

  // T043: 标签栏最前面增加「全部岗位」标签按钮，悬停显示"有状态、判断过或记过 HR 说的话的岗位"
  assert.ok(html.includes('data-filter="all_jobs"'));
  assert.ok(html.includes('title="有状态、判断过或记过 HR 说的话的岗位"'));
  assert.ok(html.includes('id="count-all_jobs"'));

  // T043: 新增「只看有 HR 记录」复选框
  assert.ok(html.includes('id="hr-only-checkbox"'));
  assert.ok(html.includes("只看有 HR 记录"));

  // T044: 顶部搜索框与 200 条超量提示卡片
  assert.ok(html.includes('id="search-input"'));
  assert.ok(html.includes('id="total-matches-notice"'));

  // T042: CSS 两行截断样式
  assert.ok(html.includes("-webkit-line-clamp: 2"));
  assert.ok(html.includes("-webkit-box-orient: vertical"));

  // 默认选中「已标记」
  assert.ok(html.includes('class="filter-btn active" data-filter="all"'));
});
