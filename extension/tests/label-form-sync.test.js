import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

import {
  LABEL_OPTIONS,
  toLabelPayload,
  toggleOption as labelFormToggleOption,
  toggleSecondary as labelFormToggleSecondary,
} from "../src/label-form.js";

import {
  CATEGORIES,
  WORK_INTENSITY,
  VERDICTS,
  VERDICT_LABELS,
  VERDICT_TONES,
  SALES_LEVEL,
  EXPERIENCE_FIT,
  LEGACY_VERDICT_MAP,
} from "../src/taxonomy.js";

/**
 * 辅助函数：跨 realm 对象深比较前先经由 JSON 序列化规范化为当前 realm 的普通对象
 */
function normalizeRealm(obj) {
  if (obj === undefined) return undefined;
  return JSON.parse(JSON.stringify(obj));
}

/**
 * 从 JS 源码中按变量名稳定提取声明语句（var / let / const <varName> = ...;）
 * 基于括号、方括号、花括号配对与分号终结符，不依赖固定的行号或缩进。
 */
function extractVariable(source, varName) {
  const pattern = new RegExp(`\\b(?:var|let|const)\\s+${varName}\\s*=`);
  const match = pattern.exec(source);
  if (!match) {
    assert.fail(`未能在 content.js 中定位到变量声明: var/let/const ${varName} = ...`);
  }

  const startIndex = match.index;
  const equalsIndex = source.indexOf("=", startIndex);

  let depthBrace = 0;
  let depthBracket = 0;
  let depthParen = 0;
  let inString = null;
  let inComment = null;
  let endIndex = -1;

  for (let i = equalsIndex + 1; i < source.length; i++) {
    const char = source[i];
    const next = source[i + 1];

    if (inComment === "line") {
      if (char === "\n") inComment = null;
      continue;
    }
    if (inComment === "block") {
      if (char === "*" && next === "/") {
        inComment = null;
        i++;
      }
      continue;
    }
    if (inString) {
      if (char === "\\") {
        i++;
      } else if (char === inString) {
        inString = null;
      }
      continue;
    }

    if (char === "/" && next === "/") {
      inComment = "line";
      i++;
      continue;
    }
    if (char === "/" && next === "*") {
      inComment = "block";
      i++;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      inString = char;
      continue;
    }

    if (char === "{") depthBrace++;
    else if (char === "}") depthBrace--;
    else if (char === "[") depthBracket++;
    else if (char === "]") depthBracket--;
    else if (char === "(") depthParen++;
    else if (char === ")") depthParen--;
    else if (
      char === ";" &&
      depthBrace === 0 &&
      depthBracket === 0 &&
      depthParen === 0
    ) {
      endIndex = i + 1;
      break;
    }
  }

  if (endIndex === -1) {
    assert.fail(`未能在 content.js 中找到变量 ${varName} 的结束分号`);
  }

  return source.slice(startIndex, endIndex);
}

/**
 * 从 JS 源码中按函数声明开头（function <funcName>(...）找到起点，
 * 再按大括号配对找到终点，提取完整函数声明。不依赖固定的行号或缩进。
 */
function extractFunction(source, funcName) {
  const pattern = new RegExp(`\\bfunction\\s+${funcName}\\s*\\(`);
  const match = pattern.exec(source);
  if (!match) {
    assert.fail(`未能在 content.js 中定位到函数声明: function ${funcName}(...`);
  }

  const startIndex = match.index;
  const openBraceIndex = source.indexOf("{", startIndex);
  if (openBraceIndex === -1) {
    assert.fail(`未能在 content.js 中找到函数 ${funcName} 的起始大括号 '{'`);
  }

  let depth = 0;
  let inString = null;
  let inComment = null;
  let endIndex = -1;

  for (let i = openBraceIndex; i < source.length; i++) {
    const char = source[i];
    const next = source[i + 1];

    if (inComment === "line") {
      if (char === "\n") inComment = null;
      continue;
    }
    if (inComment === "block") {
      if (char === "*" && next === "/") {
        inComment = null;
        i++;
      }
      continue;
    }
    if (inString) {
      if (char === "\\") {
        i++;
      } else if (char === inString) {
        inString = null;
      }
      continue;
    }

    if (char === "/" && next === "/") {
      inComment = "line";
      i++;
      continue;
    }
    if (char === "/" && next === "*") {
      inComment = "block";
      i++;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      inString = char;
      continue;
    }

    if (char === "{") {
      depth++;
    } else if (char === "}") {
      depth--;
      if (depth === 0) {
        endIndex = i + 1;
        break;
      }
    }
  }

  if (endIndex === -1 || depth !== 0) {
    assert.fail(`未能在 content.js 中闭合函数 ${funcName} 的大括号`);
  }

  return source.slice(startIndex, endIndex);
}

/**
 * 简易伪随机数生成器 (mulberry32)
 */
function mulberry32(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// -----------------------------------------------------------------------------
// 沙箱初始化与依赖提取
// -----------------------------------------------------------------------------
const contentPath = new URL("../src/content.js", import.meta.url);
const contentSrc = fs.readFileSync(contentPath, "utf8").replace(/\r\n/g, "\n");

const sandbox = {};
vm.createContext(sandbox);

const DEPENDENT_VARS = [
  "CATEGORIES",
  "WORK_INTENSITY",
  "VERDICTS",
  "VERDICT_LABELS",
  "VERDICT_TONES",
  "LEGACY_VERDICT_MAP",
  "SALES_LEVEL",
  "EXPERIENCE_FIT",
  "LABEL_OPTIONS",
];

const DEPENDENT_FUNCS = [
  "assembleLabelPayload",
  "toggleOption",
  "toggleSecondary",
];

for (const varName of DEPENDENT_VARS) {
  const code = extractVariable(contentSrc, varName);
  vm.runInContext(code, sandbox);
}

for (const fnName of DEPENDENT_FUNCS) {
  const code = extractFunction(contentSrc, fnName);
  vm.runInContext(code, sandbox);
}

// 沙箱执行包装函数（返回经 normalizeRealm 处理的对象）
const contentToggleOption = (selection, field, value) => {
  return normalizeRealm(sandbox.toggleOption(selection, field, value));
};
const contentToggleSecondary = (selection, item) => {
  return normalizeRealm(sandbox.toggleSecondary(selection, item));
};
const contentAssembleLabelPayload = (selection) => {
  return normalizeRealm(sandbox.assembleLabelPayload(selection));
};

const labelFormToggleOptionWrapped = (selection, field, value) => {
  return normalizeRealm(labelFormToggleOption(selection, field, value));
};
const labelFormToggleSecondaryWrapped = (selection, item) => {
  return normalizeRealm(labelFormToggleSecondary(selection, item));
};
const labelFormToLabelPayloadWrapped = (selection) => {
  return normalizeRealm(toLabelPayload(selection));
};

// -----------------------------------------------------------------------------
// 测试 1：从 content.js 提取的常量与原版一致性校验
// -----------------------------------------------------------------------------
test("label-form-sync: content.js 提取的常量与 taxonomy.js / label-form.js 完全一致", () => {
  for (const varName of DEPENDENT_VARS) {
    assert.ok(sandbox[varName] !== undefined, `沙箱中应存在常量 ${varName}`);
  }
  for (const fnName of DEPENDENT_FUNCS) {
    assert.equal(
      typeof sandbox[fnName],
      "function",
      `沙箱中应存在函数 ${fnName}`
    );
  }

  assert.deepStrictEqual(normalizeRealm(sandbox.CATEGORIES), CATEGORIES);
  assert.deepStrictEqual(normalizeRealm(sandbox.WORK_INTENSITY), WORK_INTENSITY);
  assert.deepStrictEqual(normalizeRealm(sandbox.VERDICTS), VERDICTS);
  assert.deepStrictEqual(normalizeRealm(sandbox.VERDICT_LABELS), VERDICT_LABELS);
  assert.deepStrictEqual(normalizeRealm(sandbox.VERDICT_TONES), VERDICT_TONES);
  assert.deepStrictEqual(normalizeRealm(sandbox.LEGACY_VERDICT_MAP), LEGACY_VERDICT_MAP);
  assert.deepStrictEqual(normalizeRealm(sandbox.SALES_LEVEL), SALES_LEVEL);
  assert.deepStrictEqual(normalizeRealm(sandbox.EXPERIENCE_FIT), EXPERIENCE_FIT);
  assert.deepStrictEqual(normalizeRealm(sandbox.LABEL_OPTIONS), LABEL_OPTIONS);
});

// -----------------------------------------------------------------------------
// 测试 2：对比 a - 手写边界用例对比
// -----------------------------------------------------------------------------
test("label-form-sync: 对比 a - 手写边界用例输出完全一致", () => {
  // 1. 空选择 / 非对象 / 初始状态输入
  const invalidStates = [null, undefined, 0, 42, "invalid", true, false, {}];
  for (const s of invalidStates) {
    // toggleOption
    assert.deepStrictEqual(
      contentToggleOption(s, "work_type", "数据与技术"),
      labelFormToggleOptionWrapped(s, "work_type", "数据与技术")
    );
    assert.deepStrictEqual(
      contentToggleOption(s, "sales_level", "高"),
      labelFormToggleOptionWrapped(s, "sales_level", "高")
    );

    // toggleSecondary
    assert.deepStrictEqual(
      contentToggleSecondary(s, "运营"),
      labelFormToggleSecondaryWrapped(s, "运营")
    );
    assert.deepStrictEqual(
      contentToggleSecondary(s, { category: "职能", subtype: "行政" }),
      labelFormToggleSecondaryWrapped(s, { category: "职能", subtype: "行政" })
    );

    // assembleLabelPayload vs toLabelPayload
    assert.deepStrictEqual(
      contentAssembleLabelPayload(s),
      labelFormToLabelPayloadWrapped(s)
    );
  }

  // 2. 重复点击同一项取消 (toggle-off)
  let sC = contentToggleOption(null, "work_type", "数据与技术");
  let sL = labelFormToggleOptionWrapped(null, "work_type", "数据与技术");
  assert.deepStrictEqual(sC, sL);

  // 再次点击相同 work_type 取消
  sC = contentToggleOption(sC, "work_type", "数据与技术");
  sL = labelFormToggleOptionWrapped(sL, "work_type", "数据与技术");
  assert.deepStrictEqual(sC, sL);
  assert.equal(sC.work_type, null);

  // 点击单选字段后再次点击取消
  for (const [field, val] of [
    ["sales_level", "高"],
    ["experience_fit", "满足"],
    ["work_intensity", "双休"],
    ["overall", "apply"],
  ]) {
    sC = contentToggleOption(sC, field, val);
    sL = labelFormToggleOptionWrapped(sL, field, val);
    assert.deepStrictEqual(sC, sL);
    assert.equal(sC[field], val);

    sC = contentToggleOption(sC, field, val);
    sL = labelFormToggleOptionWrapped(sL, field, val);
    assert.deepStrictEqual(sC, sL);
    assert.equal(sC[field], null);
  }

  // 次要类型点击取消 (对象形式和字符串形式)
  let secResC = contentToggleSecondary(null, { category: "运营", subtype: "用户运营" });
  let secResL = labelFormToggleSecondaryWrapped(null, { category: "运营", subtype: "用户运营" });
  assert.deepStrictEqual(secResC, secResL);

  secResC = contentToggleSecondary(secResC.selection, { category: "运营", subtype: "用户运营" });
  secResL = labelFormToggleSecondaryWrapped(secResL.selection, { category: "运营", subtype: "用户运营" });
  assert.deepStrictEqual(secResC, secResL);
  assert.deepStrictEqual(secResC.selection.secondary_work_types, []);

  secResC = contentToggleSecondary(null, "其他");
  secResL = labelFormToggleSecondaryWrapped(null, "其他");
  assert.deepStrictEqual(secResC, secResL);

  secResC = contentToggleSecondary(secResC.selection, "其他");
  secResL = labelFormToggleSecondaryWrapped(secResL.selection, "其他");
  assert.deepStrictEqual(secResC, secResL);
  assert.deepStrictEqual(secResC.selection.secondary_work_types, []);

  // 3. 互斥与依赖规则：
  // 3a. 切换大类清空细分，并从 secondary 移除与新主要类型相同的大类项
  const stateWithSecondary = {
    work_type: "运营",
    work_subtype: "用户运营",
    secondary_work_types: [
      { category: "数据与技术", subtype: null },
      { category: "数据与技术", subtype: "开发与测试" },
      { category: "职能", subtype: "行政" },
    ],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };

  sC = contentToggleOption(stateWithSecondary, "work_type", "数据与技术");
  sL = labelFormToggleOptionWrapped(stateWithSecondary, "work_type", "数据与技术");
  assert.deepStrictEqual(sC, sL);
  assert.equal(sC.work_type, "数据与技术");
  assert.equal(sC.work_subtype, null);
  // secondary 中与新 work_type 相同且 subtype 为 null 的项被移除，但 subtype 非空的保留
  assert.deepStrictEqual(sC.secondary_work_types, [
    { category: "数据与技术", subtype: "开发与测试" },
    { category: "职能", subtype: "行政" },
  ]);

  // 3b. 细分选择时从 secondary 移除相同项
  sC = contentToggleOption(sC, "work_subtype", "开发与测试");
  sL = labelFormToggleOptionWrapped(sL, "work_subtype", "开发与测试");
  assert.deepStrictEqual(sC, sL);
  assert.equal(sC.work_subtype, "开发与测试");
  assert.deepStrictEqual(sC.secondary_work_types, [
    { category: "职能", subtype: "行政" },
  ]);

  // 4. 次要工作类型达到上限（最多 3 项）
  let currentSelection = {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };

  const itemsToAdd = [
    { category: "运营", subtype: "用户运营" },
    { category: "产品与项目", subtype: "项目管理" },
    { category: "内容与设计", subtype: "设计与视频" },
  ];

  for (const item of itemsToAdd) {
    const resC = contentToggleSecondary(currentSelection, item);
    const resL = labelFormToggleSecondaryWrapped(currentSelection, item);
    assert.deepStrictEqual(resC, resL);
    assert.equal(resC.error, null);
    currentSelection = resC.selection;
  }
  assert.equal(currentSelection.secondary_work_types.length, 3);

  // 尝试添加第 4 项（对象与字符串形式），应提示"最多选 3 项"且状态不变
  const fourthObj = { category: "职能", subtype: "财务" };
  const resOverObjC = contentToggleSecondary(currentSelection, fourthObj);
  const resOverObjL = labelFormToggleSecondaryWrapped(currentSelection, fourthObj);
  assert.deepStrictEqual(resOverObjC, resOverObjL);
  assert.equal(resOverObjC.error, "最多选 3 项");
  assert.deepStrictEqual(resOverObjC.selection, currentSelection);

  const resOverStrC = contentToggleSecondary(currentSelection, "其他");
  const resOverStrL = labelFormToggleSecondaryWrapped(currentSelection, "其他");
  assert.deepStrictEqual(resOverStrC, resOverStrL);
  assert.equal(resOverStrC.error, "最多选 3 项");
  assert.deepStrictEqual(resOverStrC.selection, currentSelection);

  // 5. 与主类型相同的次要类型过滤
  const stateWithPrimary = {
    work_type: "数据与技术",
    work_subtype: "AI 相关",
    secondary_work_types: [],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };

  // 添加与主要类型相同项（category 和 subtype 完全一致）-> 忽略，error 为 null
  const resSameC = contentToggleSecondary(stateWithPrimary, {
    category: "数据与技术",
    subtype: "AI 相关",
  });
  const resSameL = labelFormToggleSecondaryWrapped(stateWithPrimary, {
    category: "数据与技术",
    subtype: "AI 相关",
  });
  assert.deepStrictEqual(resSameC, resSameL);
  assert.equal(resSameC.error, null);
  assert.deepStrictEqual(resSameC.selection.secondary_work_types, []);

  // 主要类型的细分为 null 时，添加相同大类且 subtype 为 null 的次要类型 -> 忽略
  const stateWithPrimaryNoSub = { ...stateWithPrimary, work_subtype: null };
  const resSameCatOnlyC = contentToggleSecondary(stateWithPrimaryNoSub, "数据与技术");
  const resSameCatOnlyL = labelFormToggleSecondaryWrapped(stateWithPrimaryNoSub, "数据与技术");
  assert.deepStrictEqual(resSameCatOnlyC, resSameCatOnlyL);
  assert.equal(resSameCatOnlyC.error, null);
  assert.deepStrictEqual(resSameCatOnlyC.selection.secondary_work_types, []);

  // 同大类不同细分 -> 允许添加
  const resDiffSubC = contentToggleSecondary(stateWithPrimary, {
    category: "数据与技术",
    subtype: "开发与测试",
  });
  const resDiffSubL = labelFormToggleSecondaryWrapped(stateWithPrimary, {
    category: "数据与技术",
    subtype: "开发与测试",
  });
  assert.deepStrictEqual(resDiffSubC, resDiffSubL);
  assert.equal(resDiffSubC.error, null);
  assert.equal(resDiffSubC.selection.secondary_work_types.length, 1);

  // 6. 无效值处理
  // 不合法大类
  assert.deepStrictEqual(
    contentToggleOption(currentSelection, "work_type", "非法大类"),
    labelFormToggleOptionWrapped(currentSelection, "work_type", "非法大类")
  );
  // 不合法细分（当前没有选中大类）
  assert.deepStrictEqual(
    contentToggleOption({ ...currentSelection, work_type: null }, "work_subtype", "开发与测试"),
    labelFormToggleOptionWrapped({ ...currentSelection, work_type: null }, "work_subtype", "开发与测试")
  );
  // 细分不属于当前大类
  assert.deepStrictEqual(
    contentToggleOption({ ...currentSelection, work_type: "运营" }, "work_subtype", "开发与测试"),
    labelFormToggleOptionWrapped({ ...currentSelection, work_type: "运营" }, "work_subtype", "开发与测试")
  );
  // toggleSecondary 传入无效项
  for (const badItem of [
    null,
    undefined,
    "",
    123,
    {},
    "不存在的分类",
    { category: "不存在的分类", subtype: null },
    { category: "运营", subtype: "不存在的细分" },
    { category: "运营", subtype: "开发与测试" }, // 跨分类细分
  ]) {
    assert.deepStrictEqual(
      contentToggleSecondary(currentSelection, badItem),
      labelFormToggleSecondaryWrapped(currentSelection, badItem)
    );
  }

  // 7. assembleLabelPayload vs toLabelPayload 请求体装配边界
  // 7a. 全空 / 纯空白备注 -> { ok: false, error: "请至少选择一项" }
  assert.deepStrictEqual(
    contentAssembleLabelPayload({
      work_type: null,
      work_subtype: null,
      secondary_work_types: [],
      sales_level: null,
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: "   \t \n ",
    }),
    labelFormToLabelPayloadWrapped({
      work_type: null,
      work_subtype: null,
      secondary_work_types: [],
      sales_level: null,
      experience_fit: null,
      work_intensity: null,
      overall: null,
      note: "   \t \n ",
    })
  );

  // 7b. note 超长截断为 100 字
  const longNoteState = {
    overall: "apply",
    note: "测试备注".repeat(30), // 120 字
  };
  const notePayloadC = contentAssembleLabelPayload(longNoteState);
  const notePayloadL = labelFormToLabelPayloadWrapped(longNoteState);
  assert.deepStrictEqual(notePayloadC, notePayloadL);
  assert.equal(notePayloadC.ok, true);
  assert.equal(notePayloadC.payload.note.length, 100);

  // 7c. 旧版 verdict 映射 (fit->apply, unsure->check, unfit->skip)
  for (const [legacy, expected] of [
    ["fit", "apply"],
    ["unsure", "check"],
    ["unfit", "skip"],
  ]) {
    const legacyState = { overall: legacy };
    const pC = contentAssembleLabelPayload(legacyState);
    const pL = labelFormToLabelPayloadWrapped(legacyState);
    assert.deepStrictEqual(pC, pL);
    assert.equal(pC.payload.overall, expected);
  }

  // 7d. 仅填写了次要类型也视为有效
  const onlySecondaryState = {
    work_type: null,
    work_subtype: null,
    secondary_work_types: [{ category: "其他", subtype: null }],
    sales_level: null,
    experience_fit: null,
    work_intensity: null,
    overall: null,
    note: "",
  };
  assert.deepStrictEqual(
    contentAssembleLabelPayload(onlySecondaryState),
    labelFormToLabelPayloadWrapped(onlySecondaryState)
  );

  // 7e. 请求体装配中次要类型的去重、过滤无效项、过滤与主类型重合项
  const complexSecondaryState = {
    work_type: "数据与技术",
    work_subtype: "AI 相关",
    secondary_work_types: [
      { category: "数据与技术", subtype: "AI 相关" }, // 与主类型相同 -> 过滤
      { category: "非法类别", subtype: null }, // 非法大类 -> 过滤
      { category: "数据与技术", subtype: "非法细分" }, // 非法细分 -> 过滤
      { category: "运营", subtype: "用户运营" }, // 合法 1
      { category: "运营", subtype: "用户运营" }, // 重复 -> 去重
      { category: "产品与项目", subtype: "项目管理" }, // 合法 2
      { category: "职能", subtype: "财务" }, // 合法 3
      { category: "其他", subtype: null }, // 达到 3 项上限 -> 截断
    ],
  };
  assert.deepStrictEqual(
    contentAssembleLabelPayload(complexSecondaryState),
    labelFormToLabelPayloadWrapped(complexSecondaryState)
  );
});

// -----------------------------------------------------------------------------
// 测试 3：对比 b - 固定种子伪随机数生成器 (mulberry32) 随机操作序列对比 (>= 3000 步)
// -----------------------------------------------------------------------------
test("label-form-sync: 对比 b - mulberry32 伪随机序列 (种子 20261005, 3500 步) 状态与装配严格一致", () => {
  const SEED = 20261005;
  const TOTAL_STEPS = 3500;
  const ASSEMBLE_INTERVAL = 10;

  const rng = mulberry32(SEED);

  const randInt = (max) => Math.floor(rng() * max);
  const randChoice = (arr) => arr[randInt(arr.length)];

  const categoryList = Object.keys(CATEGORIES);
  const allSubtypes = [];
  const allSecondaryCandidates = [];

  for (const cat of categoryList) {
    allSecondaryCandidates.push({ category: cat, subtype: null });
    allSecondaryCandidates.push(cat); // 字符串形态
    const subs = CATEGORIES[cat] || [];
    for (const sub of subs) {
      allSubtypes.push({ category: cat, subtype: sub });
      allSecondaryCandidates.push({ category: cat, subtype: sub });
    }
  }

  // 少量无效值候选集
  const invalidFields = ["invalid_field", "unknown_key", "foo_bar"];
  const invalidCategories = ["未知行业", "全栈研发", "", 12345, null];
  const invalidSubtypes = ["非标准细分", "架构师", "", null];
  const invalidSecondaryItems = [
    null,
    undefined,
    42,
    "",
    "未知分类",
    { category: "未知分类", subtype: null },
    { category: "运营", subtype: "开发与测试" }, // 跨分类无效细分
    {},
  ];
  const invalidEnumValues = ["超高", "996", "fit_perfect", null, "unknown_enum"];

  let stateContent = null;
  let stateLabelForm = null;

  // 说明：assembleLabelPayload (content.js) 与 toLabelPayload (label-form.js)
  // 两者具有完全相同的入参签名 (selection) 和完全相同的返回结构（包括 ok、error、payload 及其 8 个字段名），
  // 语义及数据结构完全一致，无需额外字段映射转换。
  let payloadCheckCount = 0;

  for (let step = 0; step < TOTAL_STEPS; step++) {
    const actionType = rng();

    if (actionType < 0.5) {
      // 操作类型 1：toggleOption (约 50% 概率)
      const fieldRoll = rng();
      let field;
      let value;

      if (fieldRoll < 0.22) {
        field = "work_type";
        if (rng() < 0.85) {
          value = randChoice(categoryList);
        } else if (rng() < 0.5) {
          value = null;
        } else {
          value = randChoice(invalidCategories);
        }
      } else if (fieldRoll < 0.44) {
        field = "work_subtype";
        const curCat = stateContent && stateContent.work_type;
        const validSubsForCur = curCat && CATEGORIES[curCat] ? CATEGORIES[curCat] : null;

        if (validSubsForCur && validSubsForCur.length > 0 && rng() < 0.6) {
          value = randChoice(validSubsForCur);
        } else if (rng() < 0.75) {
          value = randChoice(allSubtypes).subtype;
        } else if (rng() < 0.5) {
          value = null;
        } else {
          value = randChoice(invalidSubtypes);
        }
      } else if (fieldRoll < 0.58) {
        field = "sales_level";
        value = rng() < 0.8 ? randChoice(SALES_LEVEL) : (rng() < 0.5 ? null : randChoice(invalidEnumValues));
      } else if (fieldRoll < 0.72) {
        field = "experience_fit";
        value = rng() < 0.8 ? randChoice(EXPERIENCE_FIT) : (rng() < 0.5 ? null : randChoice(invalidEnumValues));
      } else if (fieldRoll < 0.86) {
        field = "work_intensity";
        value = rng() < 0.8 ? randChoice(WORK_INTENSITY) : (rng() < 0.5 ? null : randChoice(invalidEnumValues));
      } else if (fieldRoll < 0.96) {
        field = "overall";
        if (rng() < 0.6) {
          value = randChoice(VERDICTS);
        } else if (rng() < 0.8) {
          value = randChoice(Object.keys(LEGACY_VERDICT_MAP)); // 混入 legacy verdict
        } else {
          value = rng() < 0.5 ? null : randChoice(invalidEnumValues);
        }
      } else {
        field = randChoice(invalidFields);
        value = "arbitrary_val";
      }

      stateContent = contentToggleOption(stateContent, field, value);
      stateLabelForm = labelFormToggleOptionWrapped(stateLabelForm, field, value);

      assert.deepStrictEqual(
        stateContent,
        stateLabelForm,
        `第 ${step} 步 toggleOption("${field}", ${JSON.stringify(value)}) 状态不一致`
      );
    } else if (actionType < 0.9) {
      // 操作类型 2：toggleSecondary (约 40% 概率)
      let item;
      const secRoll = rng();

      if (secRoll < 0.7) {
        // 选用合法次要类型候选项
        item = randChoice(allSecondaryCandidates);
      } else if (secRoll < 0.85) {
        // 尝试选用与当前主要类型一致的项（测试同类型过滤）
        if (stateContent && stateContent.work_type) {
          item = {
            category: stateContent.work_type,
            subtype: stateContent.work_subtype || null,
          };
        } else {
          item = randChoice(allSecondaryCandidates);
        }
      } else {
        // 选用无效次要类型项
        item = randChoice(invalidSecondaryItems);
      }

      const resC = contentToggleSecondary(stateContent, item);
      const resL = labelFormToggleSecondaryWrapped(stateLabelForm, item);

      assert.deepStrictEqual(
        resC,
        resL,
        `第 ${step} 步 toggleSecondary(${JSON.stringify(item)}) 返回结果不一致`
      );

      stateContent = resC.selection;
      stateLabelForm = resL.selection;
    } else {
      // 操作类型 3：备注修改 (约 10% 概率)
      const noteRoll = rng();
      let noteVal;
      if (noteRoll < 0.2) {
        noteVal = "";
      } else if (noteRoll < 0.4) {
        noteVal = "   \t\n  ";
      } else if (noteRoll < 0.7) {
        noteVal = `随机备注_${step}_测试`;
      } else if (noteRoll < 0.9) {
        noteVal = `超长备注_`.repeat(20); // > 100 字
      } else {
        noteVal = null;
      }

      if (stateContent) stateContent.note = noteVal;
      if (stateLabelForm) stateLabelForm.note = noteVal;

      assert.deepStrictEqual(
        stateContent,
        stateLabelForm,
        `第 ${step} 步直接设置 note 不一致`
      );
    }

    // 每隔 ASSEMBLE_INTERVAL 步及初始、结尾步验证请求体装配一致性
    if (step % ASSEMBLE_INTERVAL === 0 || step === TOTAL_STEPS - 1) {
      const payloadC = contentAssembleLabelPayload(stateContent);
      const payloadL = labelFormToLabelPayloadWrapped(stateLabelForm);

      assert.deepStrictEqual(
        payloadC,
        payloadL,
        `第 ${step} 步 assembleLabelPayload 与 toLabelPayload 请求体装配结果不一致`
      );
      payloadCheckCount++;
    }
  }

  assert.equal(payloadCheckCount, TOTAL_STEPS / ASSEMBLE_INTERVAL + 1);
});

// -----------------------------------------------------------------------------
// 测试 4：直接构造含各类无效值的标注内容（非点选生成场景）手写用例装配严格一致对比
// -----------------------------------------------------------------------------
test("label-form-sync: 对比 c - 直接构造含无效值的标注内容（覆盖非点选生成场景）手写用例装配对比", () => {
  // 用例说明：这些用例是为了覆盖「标注内容不是由点选生成」的情况。
  // 页面上的标注内容除了由前端点选生成，还可能来自服务端返回的历史标注（见 label-form.js 的 fromLabel 与 content.js 的回填逻辑），
  // 或者受到异常/脏数据影响。点选逻辑（toggleOption/toggleSecondary）本身会阻断非法输入，
  // 但两个整理函数（content.js 的 assembleLabelPayload 与 label-form.js 的 toLabelPayload）必须在直接接收到
  // 非法/无效输入时具备完全一致的过滤、防御与清洗行为。

  // 1. work_type 不在分类里
  const invalidWorkTypes = [
    "不存在分类",
    "全栈开发",
    "架构师",
    "",
    12345,
    true,
    false,
    {},
    [],
  ];
  for (const badType of invalidWorkTypes) {
    // 搭配有效单选字段，确保整体有输入
    const stateWithValid = {
      work_type: badType,
      sales_level: "高",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateWithValid),
      labelFormToLabelPayloadWrapped(stateWithValid),
      `work_type 为非法值 ${JSON.stringify(badType)} 时装配结果不一致`
    );

    // 仅有非法 work_type，预期返回 { ok: false, error: "请至少选择一项" }
    const stateAlone = { work_type: badType };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateAlone),
      labelFormToLabelPayloadWrapped(stateAlone),
      `只有非法 work_type ${JSON.stringify(badType)} 时装配结果不一致`
    );
  }

  // 2. work_subtype 不属于所选 work_type
  const mismatchSubtypes = [
    // 跨分类细分：属于其他大类，但不属于当前大类
    { type: "运营", subtype: "开发与测试" },
    { type: "职能", subtype: "用户运营" },
    { type: "产品与项目", subtype: "行政" },
    // "其他" 分类没有细分，任何细分都是非法的
    { type: "其他", subtype: "行政" },
    { type: "其他", subtype: "数据分析" },
    // 完全不存在的细分名
    { type: "数据与技术", subtype: "完全不存在的细分" },
    { type: "运营", subtype: "架构师" },
    // 错误类型 / 空值
    { type: "数据与技术", subtype: "" },
    { type: "数据与技术", subtype: 999 },
    { type: "数据与技术", subtype: true },
    { type: "数据与技术", subtype: ["开发与测试"] },
    { type: "数据与技术", subtype: { name: "开发与测试" } },
  ];
  for (const item of mismatchSubtypes) {
    const state = {
      work_type: item.type,
      work_subtype: item.subtype,
      sales_level: "中",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `work_type=${item.type}, work_subtype=${JSON.stringify(item.subtype)} 细分不属于大类时装配结果不一致`
    );
  }

  // 3. 有 work_subtype 但 work_type 为空
  const emptyWorkTypes = [null, undefined, "", 0, false, "不存在分类"];
  for (const emptyType of emptyWorkTypes) {
    const stateWithValid = {
      work_type: emptyType,
      work_subtype: "用户运营",
      experience_fit: "满足",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateWithValid),
      labelFormToLabelPayloadWrapped(stateWithValid),
      `work_type 为空(${JSON.stringify(emptyType)})且有 work_subtype 时装配结果不一致`
    );

    // 只有 work_subtype，缺少 work_type 属性
    const stateNoProp = {
      work_subtype: "数据分析",
      work_intensity: "双休",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateNoProp),
      labelFormToLabelPayloadWrapped(stateNoProp),
      "缺少 work_type 属性且有 work_subtype 时装配结果不一致"
    );

    // 只有非法 work_type + work_subtype，无其他字段
    const stateAlone = {
      work_type: emptyType,
      work_subtype: "用户运营",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateAlone),
      labelFormToLabelPayloadWrapped(stateAlone),
      `work_type 为空(${JSON.stringify(emptyType)})且只有 work_subtype 时装配结果不一致`
    );
  }

  // 4. sales_level / experience_fit / work_intensity / overall 取不在选项表里的值
  const invalidEnumTests = [
    {
      field: "sales_level",
      invalidValues: ["超高", "极高", "无", "中等", "", 123, true, false, ["高"], {}],
    },
    {
      field: "experience_fit",
      invalidValues: ["完全符合", "不匹配", "差很多", "很好", "", 456, true, false, ["满足"], {}],
    },
    {
      field: "work_intensity",
      invalidValues: ["996", "007", "弹性工作", "不加班", "", 789, true, false, ["双休"], {}],
    },
    {
      field: "overall",
      invalidValues: ["recommend", "pass", "bad", "hire", "good", "", 1, true, false, ["apply"], {}],
    },
  ];

  for (const { field, invalidValues } of invalidEnumTests) {
    for (const val of invalidValues) {
      // 搭配有效字段
      const stateWithValid = {
        [field]: val,
        work_type: "运营",
      };
      assert.deepStrictEqual(
        contentAssembleLabelPayload(stateWithValid),
        labelFormToLabelPayloadWrapped(stateWithValid),
        `字段 ${field} 取非法值 ${JSON.stringify(val)} 时装配结果不一致`
      );

      // 仅有此非法字段 -> 预期 ok: false, error: "请至少选择一项"
      const stateAlone = { [field]: val };
      assert.deepStrictEqual(
        contentAssembleLabelPayload(stateAlone),
        labelFormToLabelPayloadWrapped(stateAlone),
        `仅有非法字段 ${field}=${JSON.stringify(val)} 时装配结果不一致`
      );
    }
  }

  // 5. secondary_work_types
  // 5a. 不是数组
  const nonArraySecondary = [
    "运营",
    12345,
    true,
    false,
    { category: "运营", subtype: "用户运营" },
    null,
    undefined,
  ];
  for (const badSec of nonArraySecondary) {
    const state = {
      secondary_work_types: badSec,
      sales_level: "高",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `secondary_work_types 为非数组 ${JSON.stringify(badSec)} 时装配结果不一致`
    );

    const stateAlone = { secondary_work_types: badSec };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateAlone),
      labelFormToLabelPayloadWrapped(stateAlone),
      `仅有非数组 secondary_work_types ${JSON.stringify(badSec)} 时装配结果不一致`
    );
  }

  // 5b. 含非对象项
  const arrayWithNonObjects = [
    [null, undefined, 42, "运营", true, []],
    [null, { category: "运营", subtype: "用户运营" }, 123, "字符串项"],
    ["运营", "数据与技术"], // 字符串形态在整理函数中属于非对象，应被忽略
  ];
  for (const badArr of arrayWithNonObjects) {
    const state = {
      secondary_work_types: badArr,
      sales_level: "低",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `secondary_work_types 含非对象项时装配结果不一致: ${JSON.stringify(badArr)}`
    );
  }

  // 5c. 含不存在的分类或细分
  const invalidCategorySubtypes = [
    // 不存在的大类
    [{ category: "不存在分类", subtype: null }],
    [{ category: "不存在分类", subtype: "用户运营" }],
    [{ category: "", subtype: null }],
    [{ category: 123, subtype: null }],
    [{ category: true, subtype: null }],
    // 存在的大类，细分不存在
    [{ category: "运营", subtype: "不存在细分" }],
    [{ category: "其他", subtype: "行政" }], // "其他" 无细分
    // 存在的大类，细分属于其他大类（跨分类）
    [{ category: "运营", subtype: "开发与测试" }],
    [{ category: "职能", subtype: "数据分析" }],
    // subtype 错误类型
    [{ category: "运营", subtype: 999 }],
    [{ category: "运营", subtype: true }],
    [{ category: "运营", subtype: {} }],
    [{ category: "运营", subtype: [] }],
    // category / subtype 缺失
    [{}],
    [{ subtype: "用户运营" }],
  ];
  for (const badArr of invalidCategorySubtypes) {
    const state = {
      secondary_work_types: badArr,
      experience_fit: "满足",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `secondary_work_types 包含非法分类/细分时装配结果不一致: ${JSON.stringify(badArr)}`
    );
  }

  // 5d. 含与主类型相同的项
  const sameAsPrimaryCases = [
    // 主类型带细分：相同大类相同细分 -> 过滤
    {
      primary: { work_type: "数据与技术", work_subtype: "AI 相关" },
      secondary: [
        { category: "数据与技术", subtype: "AI 相关" },
        { category: "运营", subtype: "用户运营" },
      ],
    },
    // 主类型带细分：同大类不同细分 -> 保留
    {
      primary: { work_type: "数据与技术", work_subtype: "AI 相关" },
      secondary: [
        { category: "数据与技术", subtype: "开发与测试" },
      ],
    },
    // 主类型带细分：同大类但 subtype 为 null -> 保留（因为主类型的 subtype 非 null）
    {
      primary: { work_type: "数据与技术", work_subtype: "AI 相关" },
      secondary: [
        { category: "数据与技术", subtype: null },
      ],
    },
    // 主类型无细分：相同大类且 subtype 为 null -> 过滤
    {
      primary: { work_type: "其他", work_subtype: null },
      secondary: [
        { category: "其他", subtype: null },
        { category: "职能", subtype: "行政" },
      ],
    },
    // 主类型为 null：同大类次要类型不受影响，正常保留
    {
      primary: { work_type: null, work_subtype: null },
      secondary: [
        { category: "运营", subtype: "用户运营" },
      ],
    },
    // 主类型为非法值：次要类型不因非法主类型误过滤
    {
      primary: { work_type: "非法类别", work_subtype: null },
      secondary: [
        { category: "运营", subtype: "用户运营" },
      ],
    },
  ];
  for (const c of sameAsPrimaryCases) {
    const state = {
      ...c.primary,
      secondary_work_types: c.secondary,
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `次要类型含与主类型相同时装配结果不一致: ${JSON.stringify(c)}`
    );
  }

  // 5e. 超过上限的项数（最多 3 项有效项，多余项截断）
  const overLimitCases = [
    // 4 个有效项
    [
      { category: "运营", subtype: "用户运营" },
      { category: "产品与项目", subtype: "项目管理" },
      { category: "职能", subtype: "行政" },
      { category: "其他", subtype: null },
    ],
    // 5 个有效项
    [
      { category: "数据与技术", subtype: "数据分析" },
      { category: "运营", subtype: "用户运营" },
      { category: "产品与项目", subtype: "项目管理" },
      { category: "职能", subtype: "行政" },
      { category: "其他", subtype: null },
    ],
    // 混合非法项、重复项及超过 3 个有效项
    [
      { category: "运营", subtype: "用户运营" },
      { category: "非法类别", subtype: null },
      { category: "运营", subtype: "用户运营" }, // 重复
      null,
      "字符串项",
      { category: "产品与项目", subtype: "项目管理" },
      { category: "职能", subtype: "行政" },
      { category: "科研与专业", subtype: "质检" }, // 第 4 个有效项，应截断
      { category: "其他", subtype: null }, // 第 5 个有效项，应截断
    ],
  ];
  for (const arr of overLimitCases) {
    const state = {
      secondary_work_types: arr,
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(state),
      labelFormToLabelPayloadWrapped(state),
      `secondary_work_types 超过上限截断装配结果不一致: ${JSON.stringify(arr)}`
    );
  }

  // 6. note 不是字符串
  const nonStringNotes = [
    12345,
    0,
    true,
    false,
    {},
    [],
    ["备注"],
    { text: "备注" },
  ];
  for (const badNote of nonStringNotes) {
    const stateWithValid = {
      note: badNote,
      sales_level: "高",
    };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateWithValid),
      labelFormToLabelPayloadWrapped(stateWithValid),
      `note 为非字符串 ${JSON.stringify(String(badNote))} 时装配结果不一致`
    );

    const stateAlone = { note: badNote };
    assert.deepStrictEqual(
      contentAssembleLabelPayload(stateAlone),
      labelFormToLabelPayloadWrapped(stateAlone),
      `仅有非字符串 note ${JSON.stringify(String(badNote))} 时装配结果不一致`
    );
  }

  // 7. 全部字段均为无效值 / 混合非法值组合
  const allInvalidState = {
    work_type: "非法大类",
    work_subtype: "非法细分",
    secondary_work_types: "非数组",
    sales_level: "超高",
    experience_fit: "完全符合",
    work_intensity: "996",
    overall: "unknown",
    note: 12345,
  };
  assert.deepStrictEqual(
    contentAssembleLabelPayload(allInvalidState),
    labelFormToLabelPayloadWrapped(allInvalidState),
    "全字段为非法值时装配结果不一致"
  );
  assert.equal(contentAssembleLabelPayload(allInvalidState).ok, false);

  const mixedState = {
    work_type: "不存在分类",
    work_subtype: "用户运营",
    secondary_work_types: [
      { category: "不存在分类", subtype: null },
      { category: "运营", subtype: "开发与测试" },
      { category: "职能", subtype: "行政" },
      42,
    ],
    sales_level: 999,
    experience_fit: "不满足",
    work_intensity: "弹性",
    overall: "bad_choice",
    note: ["非字符串备注"],
  };
  assert.deepStrictEqual(
    contentAssembleLabelPayload(mixedState),
    labelFormToLabelPayloadWrapped(mixedState),
    "混合非法与合法值时装配结果不一致"
  );
  assert.equal(contentAssembleLabelPayload(mixedState).ok, true);
});

// -----------------------------------------------------------------------------
// 测试 5：mulberry32 伪随机生成至少 2000 个标注内容对象（非点选生成场景）装配严格一致对比
// -----------------------------------------------------------------------------
test("label-form-sync: 对比 d - mulberry32 伪随机生成 2500 个含非法/混合类型的标注对象（覆盖非点选生成场景）装配严格一致", () => {
  // 用例说明：这些用例是为了覆盖「标注内容不是由点选生成」的情况。
  // 使用固定种子的伪随机数生成器，直接随机生成至少 2000 个（本测试生成 2500 个）标注内容对象，
  // 每个字段独立从「合法值 / 不在选项表里的值 / null / 错误类型」里随机取值，
  // secondary_work_types 随机长度（0~6）并混入无效项、跨分类项、与主类型重合项及非对象项，
  // 逐个喂给 assembleLabelPayload 与 toLabelPayload 进行严格比对。

  const SEED = 20261006;
  const TOTAL_OBJECTS = 2500;
  const rng = mulberry32(SEED);

  const randInt = (max) => Math.floor(rng() * max);
  const randChoice = (arr) => arr[randInt(arr.length)];

  const categoryKeys = Object.keys(CATEGORIES);
  const allSubtypes = [];
  for (const cat of categoryKeys) {
    for (const sub of (CATEGORIES[cat] || [])) {
      allSubtypes.push({ category: cat, subtype: sub });
    }
  }

  const invalidCategoryStrings = [
    "非法大类", "架构师", "全栈开发", "AI算法专家", "未知分类", "", "   ", "ops",
  ];
  const invalidSubtypeStrings = [
    "非法细分", "技术总监", "未知细分", "", "   ", "lead",
  ];
  const invalidSalesStrings = ["超高", "极高", "无", "中等", "", "high", "normal"];
  const invalidExpStrings = ["完全符合", "不匹配", "差得远", "很好", "", "match", "none"];
  const invalidIntensityStrings = ["996", "007", "弹性工作", "不加班", "", "heavy", "free"];
  const invalidOverallStrings = ["recommend", "pass", "bad", "hire", "good", "", "apply_now", "skip_it"];
  const wrongTypes = [123, 0, -1, true, false, {}, [], [1, 2], { foo: "bar" }];

  for (let i = 0; i < TOTAL_OBJECTS; i++) {
    // 少量测试非对象/原始类型作为顶层输入
    if (rng() < 0.04) {
      const topInvalidCandidates = [null, undefined, 0, 42, "invalid", true, false, [], {}];
      const badInput = randChoice(topInvalidCandidates);
      const payloadC = contentAssembleLabelPayload(badInput);
      const payloadL = labelFormToLabelPayloadWrapped(badInput);
      assert.deepStrictEqual(
        payloadC,
        payloadL,
        `第 ${i} 个测试输入为顶层原始值 ${JSON.stringify(badInput)} 时装配不一致`
      );
      continue;
    }

    const candidate = {};

    // 1. work_type
    const wtRoll = rng();
    if (wtRoll < 0.40) {
      candidate.work_type = randChoice(categoryKeys);
    } else if (wtRoll < 0.65) {
      candidate.work_type = randChoice(invalidCategoryStrings);
    } else if (wtRoll < 0.85) {
      candidate.work_type = rng() < 0.5 ? null : undefined;
    } else {
      candidate.work_type = randChoice(wrongTypes);
    }

    // 2. work_subtype
    const subRoll = rng();
    const curType = candidate.work_type;
    const curTypeSubs = curType && CATEGORIES[curType] ? CATEGORIES[curType] : null;

    if (subRoll < 0.30) {
      // 合法细分：优先从当前 work_type 选取，若无细分则从全局选一个合法细分
      if (curTypeSubs && curTypeSubs.length > 0) {
        candidate.work_subtype = randChoice(curTypeSubs);
      } else {
        candidate.work_subtype = randChoice(allSubtypes).subtype;
      }
    } else if (subRoll < 0.50) {
      // 跨分类细分：从全局细分中选（很可能不属于当前 work_type）
      candidate.work_subtype = randChoice(allSubtypes).subtype;
    } else if (subRoll < 0.70) {
      // 不在分类表里的字符串
      candidate.work_subtype = randChoice(invalidSubtypeStrings);
    } else if (subRoll < 0.85) {
      candidate.work_subtype = rng() < 0.5 ? null : undefined;
    } else {
      candidate.work_subtype = randChoice(wrongTypes);
    }

    // 3. secondary_work_types
    const secRoll = rng();
    if (secRoll < 0.12) {
      // 非数组类型
      candidate.secondary_work_types = randChoice([
        null,
        undefined,
        "运营",
        123,
        true,
        false,
        { category: "运营", subtype: "用户运营" },
      ]);
    } else {
      // 数组类型，长度 0~6
      const secLen = randInt(7);
      const secArr = [];
      for (let s = 0; s < secLen; s++) {
        const itemRoll = rng();
        if (itemRoll < 0.35) {
          // 合法项
          const chosenCat = randChoice(categoryKeys);
          const chosenSubs = CATEGORIES[chosenCat] || [];
          const chosenSub = chosenSubs.length > 0 && rng() < 0.7 ? randChoice(chosenSubs) : null;
          secArr.push({ category: chosenCat, subtype: chosenSub });
        } else if (itemRoll < 0.50) {
          // 制造与当前 work_type / work_subtype 相同的项（测试主类型过滤）
          secArr.push({
            category: candidate.work_type,
            subtype: candidate.work_subtype || null,
          });
        } else if (itemRoll < 0.65) {
          // 非法分类或跨分类细分
          if (rng() < 0.5) {
            secArr.push({ category: randChoice(invalidCategoryStrings), subtype: null });
          } else {
            secArr.push({ category: randChoice(categoryKeys), subtype: randChoice(invalidSubtypeStrings) });
          }
        } else if (itemRoll < 0.80) {
          // 非对象项 / 畸形对象
          secArr.push(randChoice([
            null,
            undefined,
            42,
            "运营",
            true,
            {},
            { category: 123, subtype: "用户运营" },
            { category: "运营", subtype: 999 },
            [1, 2],
          ]));
        } else {
          // 重复已有项（测试去重）
          if (secArr.length > 0) {
            secArr.push(randChoice(secArr));
          } else {
            secArr.push({ category: "职能", subtype: "行政" });
          }
        }
      }
      candidate.secondary_work_types = secArr;
    }

    // 4. sales_level
    const slRoll = rng();
    if (slRoll < 0.40) {
      candidate.sales_level = randChoice(SALES_LEVEL);
    } else if (slRoll < 0.65) {
      candidate.sales_level = randChoice(invalidSalesStrings);
    } else if (slRoll < 0.85) {
      candidate.sales_level = rng() < 0.5 ? null : undefined;
    } else {
      candidate.sales_level = randChoice(wrongTypes);
    }

    // 5. experience_fit
    const efRoll = rng();
    if (efRoll < 0.40) {
      candidate.experience_fit = randChoice(EXPERIENCE_FIT);
    } else if (efRoll < 0.65) {
      candidate.experience_fit = randChoice(invalidExpStrings);
    } else if (efRoll < 0.85) {
      candidate.experience_fit = rng() < 0.5 ? null : undefined;
    } else {
      candidate.experience_fit = randChoice(wrongTypes);
    }

    // 6. work_intensity
    const wiRoll = rng();
    if (wiRoll < 0.40) {
      candidate.work_intensity = randChoice(WORK_INTENSITY);
    } else if (wiRoll < 0.65) {
      candidate.work_intensity = randChoice(invalidIntensityStrings);
    } else if (wiRoll < 0.85) {
      candidate.work_intensity = rng() < 0.5 ? null : undefined;
    } else {
      candidate.work_intensity = randChoice(wrongTypes);
    }

    // 7. overall
    const ovRoll = rng();
    if (ovRoll < 0.35) {
      candidate.overall = randChoice(VERDICTS);
    } else if (ovRoll < 0.50) {
      candidate.overall = randChoice(Object.keys(LEGACY_VERDICT_MAP));
    } else if (ovRoll < 0.70) {
      candidate.overall = randChoice(invalidOverallStrings);
    } else if (ovRoll < 0.85) {
      candidate.overall = rng() < 0.5 ? null : undefined;
    } else {
      candidate.overall = randChoice(wrongTypes);
    }

    // 8. note
    const nRoll = rng();
    if (nRoll < 0.25) {
      candidate.note = `随机备注_${i}_测试`;
    } else if (nRoll < 0.40) {
      candidate.note = "超长备注内容".repeat(15); // > 100 字
    } else if (nRoll < 0.55) {
      candidate.note = "   \t \n  ";
    } else if (nRoll < 0.65) {
      candidate.note = "";
    } else if (nRoll < 0.85) {
      candidate.note = rng() < 0.5 ? null : undefined;
    } else {
      candidate.note = randChoice(wrongTypes);
    }

    // 随机混入额外字段或删除某个字段
    if (rng() < 0.20) {
      candidate.extra_prop_test = "some_random_value";
    }
    if (rng() < 0.10) {
      delete candidate.work_type;
    }

    const payloadC = contentAssembleLabelPayload(candidate);
    const payloadL = labelFormToLabelPayloadWrapped(candidate);

    assert.deepStrictEqual(
      payloadC,
      payloadL,
      `第 ${i} 个随机对象装配结果不一致: ${JSON.stringify(candidate)}`
    );
  }
});
