import test from "node:test";
import assert from "node:assert/strict";
import { planMarks } from "../src/marks-layout.js";

test("planMarks: handles prejudge marks with reason tooltip (FR-014)", () => {
  const cards = [
    {
      id: "job_pj_1",
      rect: { left: 100, top: 100, width: 300, height: 120 },
      titleTop: 130, // space = 30 >= 14, label visible
    },
    {
      id: "job_pj_hidden_label",
      rect: { left: 100, top: 300, width: 300, height: 120 },
      titleTop: 305, // space = 5 < 12, label hidden
    },
  ];

  const listMarks = {
    job_pj_1: {
      type: "prejudge",
      is_prejudge: true,
      verdict_label: "预判·值得点开",
      verdict_tone: "open",
      reason: "岗位职责契合，薪资符合预期",
      stale: false,
    },
    job_pj_hidden_label: {
      type: "prejudge",
      is_prejudge: true,
      verdict_label: "预判·可跳过",
      verdict_tone: "skip",
      reason: "行业限制且经验不符",
      stale: false,
    },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  // 1. 卡片 1：标签可见，title tooltip 为 reason
  const p1 = planned[0];
  assert.equal(p1.id, "job_pj_1");
  assert.equal(p1.is_prejudge, true);
  assert.equal(p1.reason, "岗位职责契合，薪资符合预期");
  assert.equal(p1.text, "预判·值得点开");
  assert.equal(p1.tone, "open");
  assert.ok(p1.strip);
  assert.equal(p1.strip.left, 94); // 100 - 6
  assert.equal(p1.strip.width, 4);
  assert.equal(p1.strip.title, ""); // label visible -> strip title empty
  assert.ok(p1.label);
  assert.equal(p1.label.visible, true);
  assert.equal(p1.label.title, "岗位职责契合，薪资符合预期"); // tooltip 为预判理由

  // 2. 卡片 2：标签不可见，色条 title tooltip 为 reason
  const p2 = planned[1];
  assert.equal(p2.id, "job_pj_hidden_label");
  assert.equal(p2.is_prejudge, true);
  assert.equal(p2.reason, "行业限制且经验不符");
  assert.ok(p2.label);
  assert.equal(p2.label.visible, false);
  assert.ok(p2.strip);
  assert.equal(p2.strip.pointerEvents, "auto");
  assert.equal(p2.strip.title, "行业限制且经验不符"); // 标签不可见时色条展示预判理由
});
