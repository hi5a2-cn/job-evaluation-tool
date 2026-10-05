import test from "node:test";
import assert from "node:assert/strict";
import {
  LABEL_OPTIONS,
  toLabelPayload,
  toHrNotePayload,
  fromLabel,
  toggleOption,
  toggleSecondary,
} from "../src/label-form.js";
import {
  CATEGORIES,
  WORK_INTENSITY,
  VERDICTS,
  VERDICT_LABELS,
  SALES_LEVEL,
  EXPERIENCE_FIT,
} from "../src/taxonomy.js";

test("LABEL_OPTIONS structure and values", () => {
  assert.deepEqual(Object.keys(LABEL_OPTIONS.categories), [
    "数据与技术",
    "运营",
    "产品与项目",
    "内容与设计",
    "市场与销售",
    "科研与专业",
    "职能",
    "其他",
  ]);
  assert.deepEqual(LABEL_OPTIONS.categories["其他"], []);
  assert.deepEqual(LABEL_OPTIONS.categories["数据与技术"], [
    "数据分析",
    "数据处理与标注",
    "技术支持与实施",
    "开发与测试",
    "AI 相关",
  ]);
  assert.deepEqual(LABEL_OPTIONS.work_intensity, [
    "高强度",
    "单休",
    "大小周",
    "双休",
    "未提及",
  ]);
  assert.deepEqual(LABEL_OPTIONS.sales_level, ["高", "中", "低"]);
  assert.deepEqual(LABEL_OPTIONS.experience_fit, [
    "满足",
    "差一点",
    "不满足",
    "无法判断",
  ]);
  assert.deepEqual(LABEL_OPTIONS.overall, [
    { value: "apply", label: "适合投递" },
    { value: "try", label: "可以一试" },
    { value: "check", label: "需要确认" },
    { value: "skip", label: "不建议投" },
  ]);
});

test("toLabelPayload empty and invalid handling", () => {
  // 1. null / undefined / 非对象
  assert.deepEqual(toLabelPayload(null), { ok: false, error: "请至少选择一项" });
  assert.deepEqual(toLabelPayload(undefined), { ok: false, error: "请至少选择一项" });
  assert.deepEqual(toLabelPayload("invalid"), { ok: false, error: "请至少选择一项" });

  // 2. 空对象或所有字段为 null / 空字符串 / 空数组
  assert.deepEqual(toLabelPayload({}), { ok: false, error: "请至少选择一项" });
  assert.deepEqual(
    toLabelPayload({
      work_type: null,
      work_subtype: null,
      secondary_work_types: [],
      sales_level: null,
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: null,
    }),
    { ok: false, error: "请至少选择一项" },
  );

  // 3. note 仅空白符视为 null
  assert.deepEqual(toLabelPayload({ note: "   \n\t  " }), {
    ok: false,
    error: "请至少选择一项",
  });

  // 4. 不在选项内的值被过滤为 null，若无其他有效项则报错
  assert.deepEqual(
    toLabelPayload({
      work_type: "未知类型",
      work_subtype: "未知细分",
      secondary_work_types: [{ category: "未知次要", subtype: null }],
      sales_level: "超高",
      work_intensity: "连轴转",
      overall: "bad_verdict",
    }),
    {
      ok: false,
      error: "请至少选择一项",
    },
  );
});

test("toLabelPayload valid single and multi-field selections", () => {
  // 1. 单选工作类型（大类与细分）
  const res1 = toLabelPayload({
    work_type: "数据与技术",
    work_subtype: "数据分析",
  });
  assert.equal(res1.ok, true);
  assert.deepEqual(res1.payload, {
    work_type: "数据与技术",
    work_subtype: "数据分析",
    secondary_work_types: null,
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: null,
  });

  // 1b. 细分不属于已选大类时，细分被规整为 null
  const resMismatch = toLabelPayload({
    work_type: "运营",
    work_subtype: "数据分析", // 属于数据与技术，不属于运营
  });
  assert.equal(resMismatch.ok, true);
  assert.equal(resMismatch.payload.work_type, "运营");
  assert.equal(resMismatch.payload.work_subtype, null);

  // 2. 总体结论和销售成分与工作强度
  const res2 = toLabelPayload({
    sales_level: "高",
    work_intensity: "高强度",
    overall: "skip",
  });
  assert.equal(res2.ok, true);
  assert.deepEqual(res2.payload, {
    work_type: null,
    work_subtype: null,
    secondary_work_types: null,
    sales_level: "高",
    experience_fit: null,
    work_intensity: "高强度",
    overall: "skip",
    note: null,
  });

  // 3. 仅填写 note
  const res3 = toLabelPayload({ note: "  需要对接外部客户  " });
  assert.equal(res3.ok, true);
  assert.equal(res3.payload.note, "需要对接外部客户");
  assert.equal(res3.payload.work_type, null);
  assert.equal(res3.payload.work_subtype, null);
  assert.equal(res3.payload.secondary_work_types, null);

  // 4. note 超过 100 字截断
  const longNote = "A".repeat(120);
  const res4 = toLabelPayload({ note: longNote });
  assert.equal(res4.ok, true);
  assert.equal(res4.payload.note.length, 100);
  assert.equal(res4.payload.note, "A".repeat(100));

  // 5. 全部字段有效填写（含次要类型）
  const fullSelection = {
    work_type: "市场与销售",
    work_subtype: "销售与商务拓展",
    secondary_work_types: [
      { category: "运营", subtype: "活动运营" },
      { category: "其他", subtype: null },
    ],
    sales_level: "高",
    experience_fit: "差一点",
    work_intensity: "单休",
    overall: "skip",
    note: "本质是商务拓客",
  };
  const res5 = toLabelPayload(fullSelection);
  assert.equal(res5.ok, true);
  assert.deepEqual(res5.payload, fullSelection);

  // 6. 只填了次要类型也算"至少一项"
  const res6 = toLabelPayload({
    secondary_work_types: [
      { category: "运营", subtype: "活动运营" },
      { category: "职能", subtype: "行政" },
    ],
  });
  assert.equal(res6.ok, true);
  assert.deepEqual(res6.payload, {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [
      { category: "运营", subtype: "活动运营" },
      { category: "职能", subtype: "行政" },
    ],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: null,
  });

  // 7. secondary_work_types 规则：空数组转为 null、过滤与主要类型相同的项、超3个截断、去重
  const res7 = toLabelPayload({
    work_type: "运营",
    work_subtype: "活动运营",
    secondary_work_types: [
      { category: "运营", subtype: "活动运营" }, // 与主要类型相同，应被过滤
      { category: "运营", subtype: "活动运营" }, // 重复项
      { category: "数据与技术", subtype: "数据分析" },
      { category: "产品与项目", subtype: "项目管理" },
      { category: "职能", subtype: "财务" },
      { category: "其他", subtype: null }, // 超出 3 项截断
    ],
  });
  assert.equal(res7.ok, true);
  assert.deepEqual(res7.payload.secondary_work_types, [
    { category: "数据与技术", subtype: "数据分析" },
    { category: "产品与项目", subtype: "项目管理" },
    { category: "职能", subtype: "财务" },
  ]);
});

test("fromLabel converts Judgement.label to form initial selection", () => {
  // 1. null 或 undefined 初始为空表单
  assert.deepEqual(fromLabel(null), {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  });
  assert.deepEqual(fromLabel(undefined), {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  });

  // 2. 部分字段已标注（回填 secondary_work_types 为 null 时转为 []）
  const partial = {
    work_type: "市场与销售",
    work_subtype: "客服与客户成功",
    secondary_work_types: null,
    sales_level: "低",
    note: "纯在线客服",
    corrected: true,
  };
  assert.deepEqual(fromLabel(partial), {
    work_type: "市场与销售",
    work_subtype: "客服与客户成功",
    secondary_work_types: [],
    sales_level: "低",
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "纯在线客服",
  });

  // 3. 非法选项重置为 null / 过滤，忽略旧字段 overtime
  assert.deepEqual(
    fromLabel({
      work_type: "非法值",
      work_subtype: "非法细分",
      secondary_work_types: [{ category: "非法次要", subtype: null }],
      overtime: "有", // 旧字段应被忽略
      overall: "bad_verdict",
    }),
    {
      work_type: null,
      work_subtype: null,
      secondary_work_types: [],
      sales_level: null,
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: "",
    },
  );

  // 4. 包含合法 secondary_work_types 且排除同名主要类型
  const withSec = {
    work_type: "运营",
    work_subtype: "用户运营",
    secondary_work_types: [
      { category: "运营", subtype: "用户运营" },
      { category: "运营", subtype: "内容运营" },
      { category: "数据与技术", subtype: "数据分析" },
    ],
  };
  assert.deepEqual(fromLabel(withSec), {
    work_type: "运营",
    work_subtype: "用户运营",
    secondary_work_types: [
      { category: "运营", subtype: "内容运营" },
      { category: "数据与技术", subtype: "数据分析" },
    ],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  });

  // 5. 完整往返验证
  const original = {
    work_type: "数据与技术",
    work_subtype: "技术支持与实施",
    secondary_work_types: [{ category: "市场与销售", subtype: "客服与客户成功" }],
    sales_level: "中",
    experience_fit: "满足",
    work_intensity: "双休",
    overall: "apply",
    note: "适合当前背景",
  };
  const payloadRes = toLabelPayload(original);
  assert.equal(payloadRes.ok, true);
  const formSel = fromLabel(payloadRes.payload);
  assert.deepEqual(formSel, original);
});

test("toggleOption handles select, unselect on re-click, switch value, and subtype", () => {
  // 1. null selection 选中主要类型大类
  const s1 = toggleOption(null, "work_type", "数据与技术");
  assert.equal(s1.work_type, "数据与技术");
  assert.equal(s1.work_subtype, null);
  assert.deepEqual(s1.secondary_work_types, []);
  assert.equal(s1.sales_level, null);
  assert.equal(s1.note, "");

  // 2. 再次点击取消 (置为 null，同时清空细分)
  const s2 = toggleOption(s1, "work_type", "数据与技术");
  assert.equal(s2.work_type, null);
  assert.equal(s2.work_subtype, null);

  // 3. 选中大类后再选细分
  const sSub = toggleOption(s1, "work_subtype", "数据分析");
  assert.equal(sSub.work_type, "数据与技术");
  assert.equal(sSub.work_subtype, "数据分析");

  // 4. 再次点击细分取消
  const sSubCancel = toggleOption(sSub, "work_subtype", "数据分析");
  assert.equal(sSubCancel.work_type, "数据与技术");
  assert.equal(sSubCancel.work_subtype, null);

  // 5. 细分不属于当前大类时被忽略
  const sSubInvalid = toggleOption(s1, "work_subtype", "用户运营");
  assert.equal(sSubInvalid.work_subtype, null);

  // 6. 切换大类时清空已选细分
  const s3 = toggleOption(sSub, "work_type", "运营");
  assert.equal(s3.work_type, "运营");
  assert.equal(s3.work_subtype, null);

  // 7. 总体结论 (overall) 切换与取消
  const o1 = toggleOption(null, "overall", "apply");
  assert.equal(o1.overall, "apply");
  const o2 = toggleOption(o1, "overall", "apply");
  assert.equal(o2.overall, null);
  const o3 = toggleOption(o1, "overall", "skip");
  assert.equal(o3.overall, "skip");

  // 8. 不可变性：不修改原对象
  const orig = {
    work_type: "数据与技术",
    work_subtype: "数据分析",
    secondary_work_types: [],
    sales_level: "高",
    work_intensity: "双休",
    overall: "apply",
    note: "hello",
  };
  const next = toggleOption(orig, "work_type", "运营");
  assert.equal(orig.work_type, "数据与技术");
  assert.equal(next.work_type, "运营");
  assert.notEqual(orig, next);

  // 9. 选择主要类型时，若同大类同细分已在次要类型中，自动从次要类型移除
  const selWithSec = {
    work_type: "运营",
    work_subtype: null,
    secondary_work_types: [
      { category: "数据与技术", subtype: "数据分析" },
      { category: "运营", subtype: "活动运营" },
    ],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };
  // 切换主要类型为数据与技术/数据分析
  const selAfterCat = toggleOption(selWithSec, "work_type", "数据与技术");
  const selAfterSub = toggleOption(selAfterCat, "work_subtype", "数据分析");
  assert.equal(selAfterSub.work_type, "数据与技术");
  assert.equal(selAfterSub.work_subtype, "数据分析");
  // 相同项 { category: "数据与技术", subtype: "数据分析" } 被移除
  assert.deepEqual(selAfterSub.secondary_work_types, [
    { category: "运营", subtype: "活动运营" },
  ]);
});

test("toggleSecondary handles multi-select, deselect, limits, primary conflict, and invalid", () => {
  // 1. 从 null selection 开始多选添加
  const r1 = toggleSecondary(null, { category: "运营", subtype: "活动运营" });
  assert.equal(r1.error, null);
  assert.deepEqual(r1.selection.secondary_work_types, [
    { category: "运营", subtype: "活动运营" },
  ]);

  // 2. 再次点击取消已选中的项
  const r2 = toggleSecondary(r1.selection, { category: "运营", subtype: "活动运营" });
  assert.equal(r2.error, null);
  assert.deepEqual(r2.selection.secondary_work_types, []);

  // 3. 连续添加最多 3 个
  let sel = fromLabel(null);
  const add1 = toggleSecondary(sel, { category: "运营", subtype: "活动运营" });
  assert.equal(add1.error, null);

  const add2 = toggleSecondary(add1.selection, { category: "市场与销售", subtype: "销售与商务拓展" });
  assert.equal(add2.error, null);

  const add3 = toggleSecondary(add2.selection, { category: "数据与技术", subtype: "开发与测试" });
  assert.equal(add3.error, null);
  assert.equal(add3.selection.secondary_work_types.length, 3);

  // 4. 已选 3 个时再加第 4 个：返回原 selection 与 error: "最多选 3 项"
  const add4 = toggleSecondary(add3.selection, { category: "其他", subtype: null });
  assert.equal(add4.error, "最多选 3 项");
  assert.equal(add4.selection, add3.selection);
  assert.equal(add4.selection.secondary_work_types.length, 3);

  // 5. 取消其中一个后，可以再添加新的
  const unsel = toggleSecondary(add3.selection, { category: "市场与销售", subtype: "销售与商务拓展" });
  assert.equal(unsel.error, null);
  assert.equal(unsel.selection.secondary_work_types.length, 2);

  const addAgain = toggleSecondary(unsel.selection, { category: "其他", subtype: null });
  assert.equal(addAgain.error, null);
  assert.equal(addAgain.selection.secondary_work_types.length, 3);
  assert.deepEqual(addAgain.selection.secondary_work_types, [
    { category: "运营", subtype: "活动运营" },
    { category: "数据与技术", subtype: "开发与测试" },
    { category: "其他", subtype: null },
  ]);

  // 6. 与当前主要类型相同（同大类同细分）：不变，error 为 null
  const selWithPrimary = {
    ...fromLabel(null),
    work_type: "数据与技术",
    work_subtype: "开发与测试",
    secondary_work_types: [{ category: "运营", subtype: "活动运营" }],
  };
  const rConflict = toggleSecondary(selWithPrimary, {
    category: "数据与技术",
    subtype: "开发与测试",
  });
  assert.equal(rConflict.error, null);
  assert.equal(rConflict.selection, selWithPrimary);
  assert.deepEqual(rConflict.selection.secondary_work_types, [
    { category: "运营", subtype: "活动运营" },
  ]);

  // 7. 非法大类或细分不属于该大类：不变，error 为 null
  const rInvalidCat = toggleSecondary(selWithPrimary, {
    category: "无效大类",
    subtype: null,
  });
  assert.equal(rInvalidCat.error, null);
  assert.equal(rInvalidCat.selection, selWithPrimary);

  const rInvalidSub = toggleSecondary(selWithPrimary, {
    category: "运营",
    subtype: "开发与测试", // 属于数据与技术，不属于运营
  });
  assert.equal(rInvalidSub.error, null);
  assert.equal(rInvalidSub.selection, selWithPrimary);

  // 8. 不可变性：不修改原对象
  const orig = {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [{ category: "运营", subtype: "活动运营" }],
  };
  const resNew = toggleSecondary(orig, { category: "其他", subtype: null });
  assert.notEqual(orig, resNew.selection);
  assert.equal(orig.secondary_work_types.length, 1);
  assert.equal(resNew.selection.secondary_work_types.length, 2);
});

test("toHrNotePayload normalizes input, handles empty, and truncates to 200 chars", () => {
  // 1. null / undefined / 非字符串 -> { note: null }
  assert.deepEqual(toHrNotePayload(null), { note: null });
  assert.deepEqual(toHrNotePayload(undefined), { note: null });
  assert.deepEqual(toHrNotePayload(123), { note: null });
  assert.deepEqual(toHrNotePayload({}), { note: null });

  // 2. 空字符串或仅包含空白 -> { note: null }
  assert.deepEqual(toHrNotePayload(""), { note: null });
  assert.deepEqual(toHrNotePayload("   \n\t  "), { note: null });

  // 3. 正常文本去除首尾空白
  assert.deepEqual(toHrNotePayload("  HR 说实际双休，不背业绩  "), {
    note: "HR 说实际双休，不背业绩",
  });

  // 4. 刚好 200 字
  const exact200 = "字".repeat(200);
  assert.deepEqual(toHrNotePayload(exact200), { note: exact200 });

  // 5. 超过 200 字截断
  const longText = "A".repeat(250);
  const resLong = toHrNotePayload(longText);
  assert.equal(resLong.note.length, 200);
  assert.equal(resLong.note, "A".repeat(200));

  // 6. 首尾空白不计入 200 字限制（先 trim 再截断）
  const spacedLong = "   " + "B".repeat(220) + "   ";
  const resSpaced = toHrNotePayload(spacedLong);
  assert.equal(resSpaced.note.length, 200);
  assert.equal(resSpaced.note, "B".repeat(200));
});
