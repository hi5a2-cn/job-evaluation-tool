import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { readBossPage } from "../src/page-reader.js";
import { buildListMarks, buildPageSummary, formatHintLabel } from "../src/page-summary.js";
import {
  jobIdFromHref,
  dedupeCards,
  visibleRange,
  planMarks,
} from "../src/marks-layout.js";
import {
  tabStateMap,
  sendListObservations,
  handleProfileSaved,
  jetClient,
  handleSendDetail,
} from "../src/background.js";
import { serializeTabState, deserializeTabState } from "../src/scheduler.js";

const origLocation = globalThis.location;
const origDocument = globalThis.document;
const origChrome = globalThis.chrome;

function restoreGlobals() {
  if (origLocation === undefined) {
    delete globalThis.location;
  } else {
    globalThis.location = origLocation;
  }

  if (origDocument === undefined) {
    delete globalThis.document;
  } else {
    globalThis.document = origDocument;
  }

  if (origChrome === undefined) {
    delete globalThis.chrome;
  } else {
    globalThis.chrome = origChrome;
  }
}

function setupMockEnv({ pathname = "/web/geek/job", vue = null, bodyChildrenCount = 1 } = {}) {
  globalThis.location = { pathname };
  globalThis.document = {
    body: {
      children: {
        length: bodyChildrenCount,
      },
    },
    querySelector(selector) {
      if (vue && (selector === "#wrap .page-job-wrapper" || selector === "#wrap")) {
        return { __vue__: vue };
      }
      return null;
    },
  };
}

test.afterEach(() => {
  restoreGlobals();
});

// -----------------------------------------------------------------------------
// 1. 列表读取带出 job_labels、skills（含读不到为 []，不读取其他新字段 FR-009）
// -----------------------------------------------------------------------------
test("readBossPage: 列表读取正确带出 job_labels 与 skills（非空去空格，读不到为 []）", () => {
  const jobList = [
    {
      encryptJobId: "job_01",
      jobName: "后端架构师",
      brandName: "测试公司",
      brandIndustry: "互联网",
      salaryDesc: "30-50K",
      cityName: "深圳",
      areaDistrict: "南山区",
      jobLabels: [" 架构设计 ", "微服务", "", "   ", 123, null],
      skills: [" Go ", "  K8s", "\n", undefined, {}],
    },
    {
      encryptJobId: "job_02",
      jobName: "前端开发",
      cityName: "广州",
      // jobLabels 与 skills 均缺失
    },
    {
      encryptJobId: "job_03",
      jobName: "测试工程师",
      cityName: "北京",
      jobLabels: null,
      skills: "not_an_array",
    },
  ];

  const vue = {
    hasMore: false,
    jobList,
    jobDetail: null,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, true);
  assert.equal(result.list.jobs.length, 3);

  // job_01: 字符串去空格，过滤非字符串与空串
  assert.deepEqual(result.list.jobs[0].job_labels, ["架构设计", "微服务"]);
  assert.deepEqual(result.list.jobs[0].skills, ["Go", "K8s"]);

  // job_02: 缺失时返回 []
  assert.deepEqual(result.list.jobs[1].job_labels, []);
  assert.deepEqual(result.list.jobs[1].skills, []);

  // job_03: 非数组 / null 时返回 []
  assert.deepEqual(result.list.jobs[2].job_labels, []);
  assert.deepEqual(result.list.jobs[2].skills, []);

  // 严格核对字段列表：不读取招聘者、各类令牌、行业编号等任何其他新字段（FR-009 增补 experience 与 degree）
  const expectedKeys = [
    "platform_job_id",
    "title",
    "company_name",
    "company_industry",
    "salary_raw",
    "city",
    "district",
    "experience",
    "degree",
    "job_labels",
    "skills",
  ].sort();
  assert.deepEqual(Object.keys(result.list.jobs[0]).sort(), expectedKeys);
});

// -----------------------------------------------------------------------------
// 2. formatHintLabel: 格式化小字标签文案（FR-004 / T010 选项 A）
// -----------------------------------------------------------------------------
test("formatHintLabel: 格式化小字标签为「粗筛：提示1 · 提示2」", () => {
  // 单条提示
  assert.equal(
    formatHintLabel([{ type: "strict_industry", text: "高风险行业（快消）" }]),
    "粗筛：高风险行业（快消）"
  );

  // 多条提示按顺序拼接
  assert.equal(
    formatHintLabel([
      { type: "strict_industry", text: "高风险行业（快消）" },
      { type: "salary_floor", text: "低于薪资底线" },
      { type: "exclude_keyword", text: "命中不接受条件：销售" },
      { type: "excluded_city", text: "不去的城市" },
    ]),
    "粗筛：高风险行业（快消） · 低于薪资底线 · 命中不接受条件：销售 · 不去的城市"
  );

  // 兼容纯字符串数组
  assert.equal(
    formatHintLabel(["提示A", "提示B"]),
    "粗筛：提示A · 提示B"
  );

  // 空数组或无效输入返回空字符串
  assert.equal(formatHintLabel([]), "");
  assert.equal(formatHintLabel(null), "");
  assert.equal(formatHintLabel(undefined), "");
  assert.equal(formatHintLabel([{ text: "   " }]), "");
});

// -----------------------------------------------------------------------------
// 3. buildListMarks: 已判断只出判断标记、未判断有提示出提示标记、无提示不出标记
// -----------------------------------------------------------------------------
test("buildListMarks: 对已判断岗位只出判断标记，其余岗位有 screen_hints 出提示标记，无提示不出标记", () => {
  const judgements = new Map([
    // 岗位 1: 已完成判断（done 且有结论），同时有 screen_hints -> 照旧只出判断标记（FR-004）
    [
      "job_judged_done",
      {
        judgement: {
          status: "done",
          verdict: "apply",
          judged_at: "2026-09-29T10:00:00Z",
        },
        screen_hints: [
          { type: "salary_floor", text: "低于薪资底线" },
        ],
      },
    ],
    // 岗位 2: 未完成判断（running）且有 screen_hints -> 生成提示标记
    [
      "job_running_with_hints",
      {
        judgement: { status: "running" },
        screen_hints: [
          { type: "strict_industry", text: "高风险行业（快消）" },
          { type: "exclude_keyword", text: "命中不接受条件：销售" },
        ],
      },
    ],
    // 岗位 3: 尚未点开/判断（judgement 为 null）且有 screen_hints -> 生成提示标记
    [
      "job_unjudged_with_hints",
      {
        judgement: null,
        screen_hints: [
          { type: "excluded_city", text: "不去的城市" },
        ],
      },
    ],
    // 岗位 4: 未完成判断且无 screen_hints（空数组）-> 不出标记
    [
      "job_unjudged_no_hints",
      {
        judgement: null,
        screen_hints: [],
      },
    ],
    // 岗位 5: 未完成判断且无 screen_hints 字段 -> 不出标记
    [
      "job_unjudged_missing_hints",
      {
        judgement: null,
      },
    ],
  ]);

  const marks = buildListMarks(judgements);

  // 只有 job_judged_done, job_running_with_hints, job_unjudged_with_hints 生成标记
  assert.deepEqual(
    Object.keys(marks).sort(),
    ["job_judged_done", "job_running_with_hints", "job_unjudged_with_hints"].sort()
  );

  // 1. 已判断岗位：只有判断标记，无提示标记内容
  assert.equal(marks["job_judged_done"].verdict_label, "适合投递");
  assert.equal(marks["job_judged_done"].verdict_tone, "green");
  assert.equal(marks["job_judged_done"].is_hint, undefined);

  // 2. 未判断有提示：生成提示标记，按服务端顺序，色条为中性灰蓝色 (slate)
  assert.equal(marks["job_running_with_hints"].is_hint, true);
  assert.equal(marks["job_running_with_hints"].verdict_tone, "slate");
  assert.equal(
    marks["job_running_with_hints"].verdict_label,
    "粗筛：高风险行业（快消） · 命中不接受条件：销售"
  );
  assert.deepEqual(marks["job_running_with_hints"].hints, [
    "高风险行业（快消）",
    "命中不接受条件：销售",
  ]);

  // 3. 另一未判断提示岗位
  assert.equal(marks["job_unjudged_with_hints"].is_hint, true);
  assert.equal(marks["job_unjudged_with_hints"].verdict_tone, "slate");
  assert.equal(marks["job_unjudged_with_hints"].verdict_label, "粗筛：不去的城市");

  // 4. 侧边栏摘要不受提示标记影响（FR-004）
  const listJobs = [
    { platform_job_id: "job_judged_done", title: "职位 1" },
    { platform_job_id: "job_running_with_hints", title: "职位 2" },
    { platform_job_id: "job_unjudged_with_hints", title: "职位 3" },
    { platform_job_id: "job_unjudged_no_hints", title: "职位 4" },
  ];
  const summary = buildPageSummary(listJobs, judgements);
  assert.equal(summary.count, 1);
  assert.equal(summary.items.length, 1);
  assert.equal(summary.items[0].platform_job_id, "job_judged_done");
});

// -----------------------------------------------------------------------------
// 4. planMarks: 提示标记的截断文字与悬停全文（T010 选项 A）
// -----------------------------------------------------------------------------
test("planMarks: 提示标记包含悬停全文 title，maxWidth 截断，留白不足时色条保留悬停全文", () => {
  const cards = [
    {
      id: "card_hint_normal",
      rect: { left: 40, top: 100, width: 220, height: 100 },
      titleTop: 125, // space = 25 >= 14
    },
    {
      id: "card_hint_no_space",
      rect: { left: 40, top: 250, width: 220, height: 100 },
      titleTop: 258, // space = 8 < 12
    },
  ];

  const fullText = "粗筛：高风险行业（快消） · 低于薪资底线 · 命中不接受条件：销售";
  const listMarks = {
    card_hint_normal: {
      is_hint: true,
      verdict_label: fullText,
      verdict_tone: "slate",
    },
    card_hint_no_space: {
      is_hint: true,
      verdict_label: fullText,
      verdict_tone: "slate",
    },
  };

  const planned = planMarks(cards, listMarks, true);
  assert.equal(planned.length, 2);

  // 1. 留白充足的卡片：标签可见，maxWidth 为 rect.width - 16 = 204，title 包含悬停全文
  const p1 = planned[0];
  assert.equal(p1.id, "card_hint_normal");
  assert.equal(p1.tone, "slate");
  assert.equal(p1.text, fullText);
  assert.equal(p1.label.visible, true);
  assert.equal(p1.label.maxWidth, 204);
  assert.equal(p1.label.title, fullText); // 悬停全文

  // 2. 留白不足（space < 12）的卡片：标签隐藏，左侧色条承载 hover title 悬停全文
  const p2 = planned[1];
  assert.equal(p2.id, "card_hint_no_space");
  assert.equal(p2.label.visible, false);
  assert.equal(p2.strip.pointerEvents, "auto");
  assert.equal(p2.strip.title, fullText); // 色条悬停全文
});

// -----------------------------------------------------------------------------
// 5. handleProfileSaved: 画像变化后的刷新（T008 选 A / FR-006）
// -----------------------------------------------------------------------------
test("handleProfileSaved: 保存画像后清空所有 BOSS 标签页的 listSent，并对搜索列表页调用读取流程", async () => {
  // 模拟 tabStateMap
  tabStateMap.clear();
  const state1 = {
    tabId: 101,
    listSent: new Set(["job_1", "job_2"]),
    listJudgements: new Map(),
    listJobs: [],
  };
  const state2 = {
    tabId: 102,
    listSent: new Set(["job_3"]),
    listJudgements: new Map(),
    listJobs: [],
  };
  tabStateMap.set(101, state1);
  tabStateMap.set(102, state2);

  const reloadedTabIds = [];
  const fakeChromeApi = {
    tabs: {
      async query(queryInfo) {
        assert.equal(queryInfo.url, "https://*.zhipin.com/*");
        return [
          { id: 101, url: "https://www.zhipin.com/web/geek/job?query=前端" }, // 搜索列表页
          { id: 102, url: "https://www.zhipin.com/web/geek/chat?id=999" },     // 聊天页，非搜索列表
        ];
      },
      async reload(tabId) {
        reloadedTabIds.push(tabId);
      },
    },
    scripting: {
      async executeScript({ target }) {
        return [{ result: { page_kind: "search_list", list: { ok: true, jobs: [] } } }];
      },
    },
  };

  globalThis.chrome = fakeChromeApi;
  const refreshedCount = await handleProfileSaved(fakeChromeApi);

  // 1. 所有 BOSS 标签页的 listSent 被清空
  assert.equal(state1.listSent.size, 0);
  assert.equal(state2.listSent.size, 0);

  // 2. 只有搜索列表页（101）调用了读取，刷新计数为 1
  assert.equal(refreshedCount, 1);

  // 3. 绝不导航、绝不刷新页面（chrome.tabs.reload 未被调用）
  assert.equal(reloadedTabIds.length, 0);
});

// -----------------------------------------------------------------------------
// 6. serializeTabState & deserializeTabState 随标签页状态保存 / 恢复 screen_hints
// -----------------------------------------------------------------------------
test("tabState: screen_hints 随标签页状态序列化与恢复保存完整", () => {
  const state = {
    tabId: 201,
    lastKey: "key1",
    lastJobRender: null,
    pageStatus: null,
    currentJobId: null,
    currentJobTitle: null,
    listJobs: [{ platform_job_id: "job_01", title: "测试" }],
    listJudgements: new Map([
      [
        "job_01",
        {
          judgement: null,
          my_status: null,
          company_name: "某公司",
          screen_hints: [{ type: "salary_floor", text: "低于薪资底线" }],
        },
      ],
    ]),
    lastTimings: null,
    updated_at: 1700000000000,
  };

  const serialized = serializeTabState(state);
  const deserialized = deserializeTabState(serialized);

  assert.ok(deserialized.listJudgements instanceof Map);
  const restoredEntry = deserialized.listJudgements.get("job_01");
  assert.ok(restoredEntry);
  assert.equal(restoredEntry.company_name, "某公司");
  assert.deepEqual(restoredEntry.screen_hints, [
    { type: "salary_floor", text: "低于薪资底线" },
  ]);
});

// -----------------------------------------------------------------------------
// 7. content.js 与 marks-layout.js 的对应函数一致性检查（参照已有一致性检查）
// -----------------------------------------------------------------------------
test("marks-layout: content.js 与 marks-layout.js 对应纯函数一致性检查", () => {
  const marksLayoutPath = new URL("../src/marks-layout.js", import.meta.url);
  const contentPath = new URL("../src/content.js", import.meta.url);

  const marksLayoutSrc = fs.readFileSync(marksLayoutPath, "utf8").replace(/\r\n/g, "\n");
  const contentSrc = fs.readFileSync(contentPath, "utf8").replace(/\r\n/g, "\n");

  // 从 content.js 提取 planMarks 函数并在隔离沙箱中执行对比
  const planMarksMatch = contentSrc.match(/function planMarks\(cards, listMarks, marksEnabled, viewport\) \{[\s\S]*?\n  \}/);
  assert.ok(planMarksMatch, "content.js 中应存在 planMarks 函数定义");

  const dedupeCardsMatch = contentSrc.match(/function dedupeCards\(cards\) \{[\s\S]*?\n  \}/);
  assert.ok(dedupeCardsMatch, "content.js 中应存在 dedupeCards 函数定义");

  // 在沙箱中执行 content.js 的 planMarks 和 dedupeCards
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(dedupeCardsMatch[0], sandbox);
  vm.runInContext(planMarksMatch[0], sandbox);

  // 3. 验证两个模块对带 screen_hints 标记的卡片计算输出严格相同
  const testCards = [
    {
      id: "card_1",
      rect: { left: 50, top: 100, width: 300, height: 100 },
      titleTop: 125,
    },
    {
      id: "card_2",
      rect: { left: 50, top: 250, width: 300, height: 100 },
      titleTop: 258,
    },
  ];
  const testListMarks = {
    card_1: {
      is_hint: true,
      verdict_label: "粗筛：高风险行业（快消） · 命中不接受条件：销售",
      verdict_tone: "slate",
    },
    card_2: {
      verdict_label: "适合投递",
      verdict_tone: "green",
      stale: true,
    },
  };

  const plannedFromLayout = planMarks(testCards, testListMarks, true);
  const plannedFromContent = sandbox.planMarks(testCards, testListMarks, true);

  // 标准化（避免 cross-realm prototype 不一致），验证纯数据深比较一致
  assert.deepEqual(
    JSON.parse(JSON.stringify(plannedFromContent)),
    JSON.parse(JSON.stringify(plannedFromLayout)),
    "content.js 中的 planMarks 输出必须与 marks-layout.js 的 planMarks 输出完全一致"
  );
});

// -----------------------------------------------------------------------------
// 8. content.js 源码检查：标签不挂载 click 事件监听且不对页面元素转发点击
// -----------------------------------------------------------------------------
test("content.js: 标签不挂载 click 事件监听且不对页面元素转发点击", () => {
  const contentPath = new URL("../src/content.js", import.meta.url);
  const contentSrc = fs.readFileSync(contentPath, "utf8");

  // 1. 列表标签渲染处没有给标签添加 click 事件监听（源码中不出现 labelEl.addEventListener("click"）
  assert.equal(
    contentSrc.includes('labelEl.addEventListener("click"'),
    false,
    'content.js 源码中不出现 labelEl.addEventListener("click"'
  );
  assert.equal(
    /labelEl\.addEventListener\(\s*["']click["']/.test(contentSrc),
    false,
    "labelEl 上不得挂载 click 事件监听"
  );

  // 2. content.js 中没有对页面元素调用 .click() 转发点击（不出现 elBelow 相关代码）
  assert.equal(
    contentSrc.includes("elBelow"),
    false,
    "content.js 中不得包含 elBelow 相关代码"
  );
  assert.equal(
    contentSrc.includes("elBelow.click()"),
    false,
    "content.js 中不得包含 elBelow.click()"
  );
});

// -----------------------------------------------------------------------------
// 9. content.js 样式检查：判断标签为 pointer-events: none，提示标签为 pointer-events: auto
// -----------------------------------------------------------------------------
test("content.js: 判断标签为 pointer-events: none，提示标签为 pointer-events: auto", () => {
  const contentPath = new URL("../src/content.js", import.meta.url);
  const contentSrc = fs.readFileSync(contentPath, "utf8");

  // 1. 通用 .jet-mark-label 为 pointer-events: none，且不含 cursor: default
  const baseLabelMatch = contentSrc.match(/\.jet-mark-label\s*\{([^}]+)\}/);
  assert.ok(baseLabelMatch, "content.js 中应存在 .jet-mark-label 规则");
  assert.match(baseLabelMatch[1], /pointer-events:\s*none;/, "通用 .jet-mark-label 样式应为 pointer-events: none");
  assert.doesNotMatch(baseLabelMatch[1], /cursor:\s*default;/, "通用 .jet-mark-label 样式不应包含 cursor: default");

  // 2. 提示标签（.jet-mark-label.slate, .jet-mark-label.hint）为 pointer-events: auto
  const hintLabelMatch = contentSrc.match(/\.jet-mark-label\.slate,\s*\.jet-mark-label\.hint\s*\{([^}]+)\}/);
  assert.ok(hintLabelMatch, "content.js 中应存在提示标签样式规则");
  assert.match(hintLabelMatch[1], /pointer-events:\s*auto;/, "提示标签应具有 pointer-events: auto 以便鼠标悬停显示 title 全文");

  // 3. 四档判断标签保持 pointer-events: none（不单独覆盖为 auto）
  for (const cls of ["green", "blue", "yellow", "red"]) {
    const verdictRegex = new RegExp(`\\.jet-mark-label\\.${cls}[^{]*\\{([^}]+)\\}`);
    const verdictMatch = contentSrc.match(verdictRegex);
    if (verdictMatch) {
      assert.doesNotMatch(
        verdictMatch[1],
        /pointer-events:\s*auto;/,
        `判断标签 .jet-mark-label.${cls} 不应设置 pointer-events: auto`
      );
    }
  }
});

// -----------------------------------------------------------------------------
// 10. sendListObservations: 服务端返回 judgement 为 null 时覆盖旧缓存为 null
// -----------------------------------------------------------------------------
test("sendListObservations: 服务端返回 judgement 为 null 时覆盖旧缓存为 null（覆盖语义）", async () => {
  const tabId = 301;
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map([
      [
        "job_cached_done",
        {
          judgement: { status: "done", verdict: "apply", judged_at: "2026-09-29T00:00:00Z" },
          my_status: "applied",
          company_name: "旧公司名称",
          screen_hints: [{ type: "strict_industry", text: "高风险行业（快消）" }],
        },
      ],
    ]),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  const readResult = {
    page_kind: "search_list",
    list: {
      ok: true,
      jobs: [
        {
          platform_job_id: "job_cached_done",
          title: "后端开发",
          company_name: "新公司名称",
          company_industry: "互联网",
          salary_raw: "20-30K",
          city: "深圳",
          district: "南山",
          job_labels: ["后端"],
          skills: ["Go"],
        },
      ],
    },
  };

  const origCall = jetClient.call;
  try {
    jetClient.call = async (method, path, body) => {
      return {
        ok: true,
        data: {
          jobs: {
            job_cached_done: {
              judgement: null,
              my_status: null,
              company_name: "服务端新公司",
              screen_hints: [{ type: "salary_floor", text: "低于薪资底线" }],
            },
          },
        },
      };
    };

    await sendListObservations(tabState, readResult, tabId);

    const entry = tabState.listJudgements.get("job_cached_done");
    assert.ok(entry, "listJudgements 中应包含该岗位");
    // 覆盖语义验证：即使旧缓存有值，服务端返回 null 时也必须被覆盖为 null
    assert.equal(entry.judgement, null, "judgement 应被覆盖为 null，不保留旧值");
    assert.equal(entry.my_status, null, "my_status 应被覆盖为 null，不保留旧值");
    assert.equal(entry.company_name, "服务端新公司", "company_name 应取服务端新值");
    assert.deepEqual(
      entry.screen_hints,
      [{ type: "salary_floor", text: "低于薪资底线" }],
      "screen_hints 应取服务端返回值"
    );
  } finally {
    jetClient.call = origCall;
    tabStateMap.delete(tabId);
  }
});

// -----------------------------------------------------------------------------
// 11. observations payload: 列表与搜索详情携带 experience 和 degree，独立详情页保持不带
// -----------------------------------------------------------------------------
test("observations payload: 列表与搜索详情携带 experience 和 degree，独立详情页保持不带", async () => {
  let capturedPayloads = [];
  const mockClient = {
    call: async (method, path, body) => {
      capturedPayloads.push({ method, path, body });
      return { ok: true, data: { jobs: {} } };
    },
  };

  const tabId = 401;
  const tabState = {
    tabId,
    listSent: new Set(),
    listJudgements: new Map(),
    listJobs: [],
  };
  tabStateMap.set(tabId, tabState);

  try {
    // 1. 列表观察
    const listReadResult = {
      page_kind: "search_list",
      list: {
        ok: true,
        jobs: [
          {
            platform_job_id: "job_list_exp_1",
            title: "产品运营",
            company_name: "法本",
            company_industry: "计算机软件",
            salary_raw: "10-15K",
            city: "深圳",
            district: "南山",
            experience: "3-5年",
            degree: "本科",
            job_labels: ["运营"],
            skills: [],
          },
          {
            platform_job_id: "job_list_exp_2",
            title: "开发",
            city: "深圳",
            experience: null,
            degree: null,
          },
        ],
      },
    };

    const origCall = jetClient.call;
    jetClient.call = mockClient.call;
    try {
      await sendListObservations(tabState, listReadResult, tabId);
    } finally {
      jetClient.call = origCall;
    }

    assert.equal(capturedPayloads.length, 1);
    const listPayload = capturedPayloads[0].body;
    assert.equal(listPayload.page_type, "list");
    assert.equal(listPayload.jobs[0].experience, "3-5年");
    assert.equal(listPayload.jobs[0].degree, "本科");
    assert.equal(listPayload.jobs[1].experience, null);
    assert.equal(listPayload.jobs[1].degree, null);

    // 2. 搜索列表页右侧详情观察
    capturedPayloads = [];
    const searchDetailReadResult = {
      page_kind: "search_list",
      detail: {
        ok: true,
        job: {
          platform_job_id: "job_detail_exp_1",
          title: "产品运营 线上面试",
          company_name: "法本",
          company_industry: "计算机软件",
          salary_raw: "12-18K",
          city: "深圳",
          district: null,
          description: "产品运营职责描述",
          experience: "3-5年",
          degree: "本科",
        },
      },
    };

    await handleSendDetail(tabId, tabState, searchDetailReadResult, false, 0, 0, mockClient);
    assert.equal(capturedPayloads.length, 1);
    const searchDetailPayload = capturedPayloads[0].body;
    assert.equal(searchDetailPayload.page_type, "detail");
    assert.equal(searchDetailPayload.jobs[0].experience, "3-5年");
    assert.equal(searchDetailPayload.jobs[0].degree, "本科");

    // 3. 独立职位详情页观察 (job_detail_page) 保持不带
    capturedPayloads = [];
    const fullDetailPageReadResult = {
      page_kind: "job_detail_page",
      detail: {
        ok: true,
        job: {
          platform_job_id: "job_full_page_1",
          title: "产品经理",
          company_legal_name: "深圳市腾讯计算机系统有限公司",
          salary_raw: "25-35K",
          city: "深圳",
          district: null,
          description: "独立详情页描述",
        },
      },
    };

    await handleSendDetail(tabId, tabState, fullDetailPageReadResult, false, 0, 0, mockClient);
    assert.equal(capturedPayloads.length, 1);
    const fullDetailPayload = capturedPayloads[0].body;
    assert.equal(fullDetailPayload.page_type, "detail");
    assert.equal("experience" in fullDetailPayload.jobs[0], false);
    assert.equal("degree" in fullDetailPayload.jobs[0], false);
  } finally {
    tabStateMap.delete(tabId);
  }
});
