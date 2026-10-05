import test from "node:test";
import assert from "node:assert/strict";
import {
  STATUS_LABELS,
  isSafeBossJobUrl,
  formatTime,
  toRow,
} from "../src/myjobs-view.js";

test("toRow handles company_name (empty and present)", () => {
  // null
  const r1 = toRow({ company_name: null });
  assert.equal(r1.company, "公司未读取");
  assert.equal(r1.company_is_empty, true);

  // undefined
  const r2 = toRow({});
  assert.equal(r2.company, "公司未读取");
  assert.equal(r2.company_is_empty, true);

  // empty string
  const r3 = toRow({ company_name: "   " });
  assert.equal(r3.company, "公司未读取");
  assert.equal(r3.company_is_empty, true);

  // valid company name
  const r4 = toRow({ company_name: "腾讯科技" });
  assert.equal(r4.company, "腾讯科技");
  assert.equal(r4.company_is_empty, false);
});

test("toRow handles salary_raw (empty and present)", () => {
  // null
  const r1 = toRow({ salary_raw: null });
  assert.equal(r1.salary, "薪资不可见");
  assert.equal(r1.salary_is_empty, true);

  // undefined
  const r2 = toRow({});
  assert.equal(r2.salary, "薪资不可见");
  assert.equal(r2.salary_is_empty, true);

  // empty string
  const r3 = toRow({ salary_raw: "" });
  assert.equal(r3.salary, "薪资不可见");
  assert.equal(r3.salary_is_empty, true);

  // valid salary
  const r4 = toRow({ salary_raw: "25-45K" });
  assert.equal(r4.salary, "25-45K");
  assert.equal(r4.salary_is_empty, false);
});

test("toRow handles 4 verdict tones and stale modifier", () => {
  // apply -> green
  const rApply = toRow({ verdict: "apply" });
  assert.equal(rApply.verdict_tone, "green");
  assert.equal(rApply.verdict_text, "适合投递");
  assert.equal(rApply.stale, false);
  assert.equal(rApply.stale_style, false);

  // try -> blue
  const rTry = toRow({ verdict: "try" });
  assert.equal(rTry.verdict_tone, "blue");
  assert.equal(rTry.verdict_text, "可以一试");
  assert.equal(rTry.stale, false);
  assert.equal(rTry.stale_style, false);

  // check -> yellow
  const rCheck = toRow({ verdict: "check" });
  assert.equal(rCheck.verdict_tone, "yellow");
  assert.equal(rCheck.verdict_text, "需要确认");
  assert.equal(rCheck.stale, false);
  assert.equal(rCheck.stale_style, false);

  // skip -> red
  const rSkip = toRow({ verdict: "skip" });
  assert.equal(rSkip.verdict_tone, "red");
  assert.equal(rSkip.verdict_text, "不建议投");
  assert.equal(rSkip.stale, false);
  assert.equal(rSkip.stale_style, false);

  // stale = true: adds "（可能过时）" and stale_style = true
  const rStale = toRow({ verdict: "apply", stale: true });
  assert.equal(rStale.verdict_tone, "green");
  assert.equal(rStale.verdict_text, "适合投递（可能过时）");
  assert.equal(rStale.stale, true);
  assert.equal(rStale.stale_style, true);

  // stale as object
  const rStaleObj = toRow({ verdict: "try", stale: { job_changed: true } });
  assert.equal(rStaleObj.stale, true);
  assert.equal(rStaleObj.stale_style, true);
  assert.equal(rStaleObj.verdict_text, "可以一试（可能过时）");

  const rNotStaleObj = toRow({ verdict: "check", stale: { job_changed: false } });
  assert.equal(rNotStaleObj.stale, false);
  assert.equal(rNotStaleObj.stale_style, false);
  assert.equal(rNotStaleObj.verdict_text, "需要确认");

  // legacy verdict fit -> apply -> green
  const rFit = toRow({ verdict: "fit" });
  assert.equal(rFit.verdict_tone, "green");
  assert.equal(rFit.verdict_text, "适合投递");
  assert.equal(rFit.stale, false);
  assert.equal(rFit.stale_style, false);
});

test("toRow handles unjudged items", () => {
  const r = toRow({ verdict: null, verdict_label: null });
  assert.equal(r.verdict_text, "未判断");
  assert.equal(r.verdict_tone, "neutral");
});

test("toRow handles hr_note_recorded", () => {
  const rRecorded = toRow({ hr_note_recorded: true });
  assert.equal(rRecorded.hr_note_text, "HR 实际情况：已记录");
  assert.equal(rRecorded.hr_note_recorded, true);

  const rNotRecorded = toRow({ hr_note_recorded: false });
  assert.equal(rNotRecorded.hr_note_text, "HR 实际情况：未记录");
  assert.equal(rNotRecorded.hr_note_recorded, false);

  const rMissing = toRow({});
  assert.equal(rMissing.hr_note_text, "HR 实际情况：未记录");
  assert.equal(rMissing.hr_note_recorded, false);
});

test("toRow handles my_status Chinese and updated time", () => {
  assert.equal(STATUS_LABELS.saved, "收藏");
  assert.equal(STATUS_LABELS.applied, "已投递");
  assert.equal(STATUS_LABELS.skipped, "不考虑");

  // string status with status_updated_at
  const rSaved = toRow({
    my_status: "saved",
    status_updated_at: "2026-09-25T10:00:00Z",
  });
  assert.equal(rSaved.status, "saved");
  assert.equal(rSaved.status_label, "收藏");
  assert.ok(rSaved.status_text.includes("收藏"));

  // object status
  const rApplied = toRow({
    my_status: {
      status: "applied",
      updated_at: "2026-09-25T11:00:00Z",
    },
  });
  assert.equal(rApplied.status, "applied");
  assert.equal(rApplied.status_label, "已投递");
  assert.ok(rApplied.status_text.includes("已投递"));

  // skipped
  const rSkipped = toRow({ my_status: "skipped" });
  assert.equal(rSkipped.status, "skipped");
  assert.equal(rSkipped.status_label, "不考虑");
  assert.equal(rSkipped.status_text, "不考虑");

  // null
  const rNull = toRow({ my_status: null });
  assert.equal(rNull.status, null);
  assert.equal(rNull.status_label, null);
  assert.equal(rNull.status_text, "");
});

test("toRow handles recent filter and last_seen_at", () => {
  const timeStr = "2026-09-25T10:30:00Z";

  // filter === 'recent'
  const rRecent = toRow({ last_seen_at: timeStr }, "recent");
  assert.equal(rRecent.show_last_seen, true);
  assert.ok(rRecent.last_seen_label.startsWith("最后查看："));

  // filter === 'all'
  const rAll = toRow({ last_seen_at: timeStr }, "all");
  assert.equal(rAll.show_last_seen, false);
  assert.ok(rAll.last_seen_label.startsWith("最后查看："));
});

test("isSafeBossJobUrl security constraints", () => {
  // 接受合法 BOSS 详情页链接
  assert.equal(
    isSafeBossJobUrl("https://www.zhipin.com/job_detail/abc.html"),
    true,
  );
  assert.equal(
    isSafeBossJobUrl("https://www.zhipin.com/job_detail/12345.html?ka=search_1"),
    true,
  );

  // 拒绝 http 协议
  assert.equal(
    isSafeBossJobUrl("http://www.zhipin.com/job_detail/abc.html"),
    false,
  );

  // 拒绝其他域名
  assert.equal(
    isSafeBossJobUrl("https://evil.com/job_detail/abc.html"),
    false,
  );
  assert.equal(
    isSafeBossJobUrl("https://zhipin.com/job_detail/abc.html"),
    false,
  );

  // 拒绝 javascript:
  assert.equal(isSafeBossJobUrl("javascript:alert(1)"), false);

  // 拒绝 BOSS 非 job_detail 页面
  assert.equal(isSafeBossJobUrl("https://www.zhipin.com/other"), false);
  assert.equal(isSafeBossJobUrl("https://www.zhipin.com/web/geek/job"), false);

  // 拒绝空或非字符串
  assert.equal(isSafeBossJobUrl(null), false);
  assert.equal(isSafeBossJobUrl(undefined), false);
  assert.equal(isSafeBossJobUrl(""), false);
});
