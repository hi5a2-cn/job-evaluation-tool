import test from "node:test";
import assert from "node:assert/strict";
import { readBossPage } from "../src/page-reader.js";
import { decideDetailAction, shouldRetryRead, contentKey } from "../src/scheduler.js";

const origLocation = globalThis.location;
const origDocument = globalThis.document;

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
}

test.afterEach(() => {
  restoreGlobals();
});

function matchesToken(el, token) {
  if (!el || !token) return false;
  let tag = "";
  const classes = [];

  const parts = token.split(".");
  if (parts[0] !== "") {
    tag = parts[0].toUpperCase();
  }
  for (let i = 1; i < parts.length; i++) {
    if (parts[i]) classes.push(parts[i]);
  }

  if (tag && el.tagName !== tag) {
    return false;
  }

  const elClasses = (el.className || "").trim().split(/\s+/).filter(Boolean);
  for (const c of classes) {
    if (!elClasses.includes(c)) {
      return false;
    }
  }

  return true;
}

function parseSelector(selector) {
  const rawParts = selector.trim().split(/\s+/);
  const segments = [];
  let nextCombinator = " ";
  for (let i = 0; i < rawParts.length; i++) {
    const part = rawParts[i];
    if (part === ">") {
      nextCombinator = ">";
    } else {
      segments.push({ token: part, combinator: nextCombinator });
      nextCombinator = " ";
    }
  }
  return segments;
}

function matchesSelector(node, segments) {
  if (!node || segments.length === 0) return false;

  const lastSegment = segments[segments.length - 1];
  if (!matchesToken(node, lastSegment.token)) {
    return false;
  }

  let cur = node;
  for (let i = segments.length - 1; i > 0; i--) {
    const prevSegment = segments[i - 1];
    const combinator = segments[i].combinator;

    if (combinator === ">") {
      cur = cur.parentElement;
      if (!cur || !matchesToken(cur, prevSegment.token)) {
        return false;
      }
    } else {
      let found = false;
      cur = cur.parentElement;
      while (cur) {
        if (matchesToken(cur, prevSegment.token)) {
          found = true;
          break;
        }
        cur = cur.parentElement;
      }
      if (!found) return false;
    }
  }

  return true;
}

function collectAllDescendants(root) {
  const result = [];
  function traverse(node) {
    for (const child of node.children) {
      result.push(child);
      traverse(child);
    }
  }
  traverse(root);
  return result;
}

class MockElement {
  constructor({
    tagName = "div",
    className = "",
    textContent = "",
    innerText,
  } = {}) {
    this.tagName = tagName.toUpperCase();
    this.className = className;
    this.textContent = textContent;
    if (innerText !== undefined) {
      this.innerText = innerText;
    }
    this.parentElement = null;
    this.children = [];
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  closest(selector) {
    const token = selector.trim();
    let cur = this;
    while (cur) {
      if (matchesToken(cur, token)) {
        return cur;
      }
      cur = cur.parentElement;
    }
    return null;
  }

  querySelector(selector) {
    const segments = parseSelector(selector);
    const all = collectAllDescendants(this);
    for (const node of all) {
      if (matchesSelector(node, segments)) {
        return node;
      }
    }
    return null;
  }

  querySelectorAll(selector) {
    const segments = parseSelector(selector);
    const all = collectAllDescendants(this);
    const matched = [];
    for (const node of all) {
      if (matchesSelector(node, segments)) {
        matched.push(node);
      }
    }
    return matched;
  }
}

function createJobDetailDOM({
  title = "测试职位名称",
  titleInnerText,
  salary = "20-35K",
  salaryInnerText,
  city = "北京",
  cityInnerText,
  description = "这是测试职位描述正文内容，负责核心模块设计与研发。",
  descriptionInnerText,
  salaryInfoText = "这是薪资说明：年终奖根据绩效发放，包含五险一金补充医疗。",
  companyIntroText = "这是公司介绍：专注于前沿技术研发与企业级服务。",
  companyLegalName = "公司名称：北京测试科技有限公司",
  companyLegalNameInnerText,
  includeBossInfo = false,
  includeCompanyUser = false,
  includeCompanyAddress = false,
  includeSimilarJobs = false,
} = {}) {
  const root = new MockElement({ tagName: "body" });

  // 1. 顶部职位信息区 .job-banner
  const banner = root.appendChild(new MockElement({ className: "job-banner" }));
  const infoPrimary = banner.appendChild(new MockElement({ className: "info-primary" }));
  const nameBox = infoPrimary.appendChild(new MockElement({ className: "name" }));

  if (title !== null) {
    nameBox.appendChild(
      new MockElement({
        tagName: "h1",
        textContent: title,
        innerText: titleInnerText,
      })
    );
  }
  if (salary !== null) {
    nameBox.appendChild(
      new MockElement({
        tagName: "span",
        className: "salary",
        textContent: salary,
        innerText: salaryInnerText,
      })
    );
  }
  if (city !== null) {
    infoPrimary.appendChild(
      new MockElement({
        tagName: "a",
        className: "text-desc text-city",
        textContent: city,
        innerText: cityInnerText,
      })
    );
  }

  // 2. 岗位详情区 .job-detail-section
  const detailSection = root.appendChild(new MockElement({ className: "job-detail-section" }));
  if (description !== null) {
    detailSection.appendChild(
      new MockElement({
        className: "job-sec-text",
        textContent: description,
        innerText: descriptionInnerText,
      })
    );
  }

  // 2.1 薪资说明（在 .salary-info 内，不能被误识别为职位描述）
  const salaryInfoItem = root.appendChild(new MockElement({ className: "detail-section-item salary-info" }));
  salaryInfoItem.appendChild(new MockElement({ className: "job-sec-text", textContent: salaryInfoText }));

  // 3. 公司区 .job-detail-company
  const companySection = root.appendChild(new MockElement({ className: "job-detail-company" }));

  // 3.1 公司介绍（在 .job-detail-company 内，不能被误识别为职位描述）
  companySection.appendChild(new MockElement({ className: "job-sec-text fold-text", textContent: companyIntroText }));

  // 3.2 工商信息 .business-info-box
  if (companyLegalName !== null) {
    const bizBox = companySection.appendChild(new MockElement({ className: "business-info-box" }));
    bizBox.appendChild(
      new MockElement({
        tagName: "li",
        className: "company-name",
        textContent: companyLegalName,
        innerText: companyLegalNameInnerText,
      })
    );
  }

  // 4. 敏感信息与无关推荐（绝不读取）
  if (includeBossInfo) {
    const bossInfo = root.appendChild(new MockElement({ className: "job-boss-info" }));
    bossInfo.appendChild(new MockElement({ textContent: "敏感招聘者信息-张某某-招聘专家" }));
  }
  if (includeCompanyUser) {
    const compUser = companySection.appendChild(new MockElement({ className: "business-info-box" }));
    compUser.appendChild(new MockElement({ tagName: "li", className: "company-user", textContent: "敏感法定代表人-王某某" }));
  }
  if (includeCompanyAddress) {
    root.appendChild(new MockElement({ className: "company-address", textContent: "敏感公司具体门牌地址-XX路XX号" }));
  }
  if (includeSimilarJobs) {
    const similarJobs = root.appendChild(new MockElement({ className: "similar-job-list" }));
    const simItem = similarJobs.appendChild(new MockElement({ className: "similar-job-item" }));
    simItem.appendChild(new MockElement({ tagName: "h1", textContent: "其他相似岗位职位名" }));
    simItem.appendChild(new MockElement({ tagName: "span", className: "salary", textContent: "50-70K" }));
    root.appendChild(new MockElement({ className: "look-job-list", textContent: "推荐岗位列表" }));
  }

  return root;
}

function setupJobDetailMockEnv({
  pathname = "/job_detail/test_job_001.html",
  search = "",
  rootElement = null,
  trackedQueries = [],
} = {}) {
  globalThis.location = { pathname, search };

  const root = rootElement || createJobDetailDOM();

  globalThis.document = {
    body: root,
    querySelector: (selector) => {
      trackedQueries.push(selector);
      return root.querySelector(selector);
    },
    querySelectorAll: (selector) => {
      trackedQueries.push(selector);
      return root.querySelectorAll(selector);
    },
  };
}

test("readBossPage: 独立职位页成功读取各字段 (T016 / FR-011)", () => {
  const root = createJobDetailDOM({
    title: "高级服务端开发工程师",
    salary: "25-40K",
    city: "深圳",
    description: "负责平台核心服务架构研发与性能调优。",
    companyLegalName: "企业名称：深圳测试网络技术有限公司",
  });
  setupJobDetailMockEnv({
    pathname: "/job_detail/job_abc123.html",
    search: "?securityId=xyz987",
    rootElement: root,
  });

  const res = readBossPage();

  assert.equal(res.reader_version, 1);
  assert.equal(res.page_kind, "job_detail_page");
  assert.equal(res.list.ok, false);
  assert.deepEqual(res.list.jobs, []);
  assert.equal(res.problems.length, 0);

  assert.equal(res.detail.ok, true);
  assert.ok(res.detail.job);
  assert.equal(res.detail.job.platform_job_id, "job_abc123");
  assert.equal(res.detail.job.title, "高级服务端开发工程师");
  assert.equal(res.detail.job.salary_raw, "25-40K");
  assert.equal(res.detail.job.city, "深圳");
  assert.equal(res.detail.job.district, null, "district 必须固定为 null");
  assert.equal(res.detail.job.description, "负责平台核心服务架构研发与性能调优。");
  assert.equal(res.detail.job.company_legal_name, "深圳测试网络技术有限公司", "前缀企业名称：已正确剔除");
});

test("readBossPage: 职位描述不会取到薪资说明或公司介绍 (T016 / FR-011)", () => {
  const root = createJobDetailDOM({
    description: "岗位核心职责描述正文。",
    salaryInfoText: "薪资说明文字：十三薪加绩效年终。",
    companyIntroText: "公司介绍文字：全国领先的软件服务商。",
  });
  setupJobDetailMockEnv({
    pathname: "/job_detail/job_def456.html",
    rootElement: root,
  });

  const res = readBossPage();

  assert.equal(res.detail.ok, true);
  assert.equal(res.detail.job.description, "岗位核心职责描述正文。");
  assert.ok(!res.detail.job.description.includes("薪资说明文字"));
  assert.ok(!res.detail.job.description.includes("公司介绍文字"));
});

test("readBossPage: 缺职位名 / 缺职位描述 / 地址无 ID 时 detail.ok = false (T016 / FR-011 / FR-012)", () => {
  // 1. 缺职位名
  const rootNoTitle = createJobDetailDOM({ title: null });
  setupJobDetailMockEnv({ pathname: "/job_detail/job_001.html", rootElement: rootNoTitle });
  const resNoTitle = readBossPage();
  assert.equal(resNoTitle.detail.ok, false);
  assert.equal(resNoTitle.detail.job, null);
  assert.ok(resNoTitle.problems.some((p) => p.includes("缺少职位名")));

  // 2. 缺职位描述
  const rootNoDesc = createJobDetailDOM({ description: null });
  setupJobDetailMockEnv({ pathname: "/job_detail/job_002.html", rootElement: rootNoDesc });
  const resNoDesc = readBossPage();
  assert.equal(resNoDesc.detail.ok, false);
  assert.equal(resNoDesc.detail.job, null);
  assert.ok(resNoDesc.problems.some((p) => p.includes("缺少职位描述")));

  // 3. 地址无 ID
  const rootValid = createJobDetailDOM();
  setupJobDetailMockEnv({ pathname: "/job_detail/.html", rootElement: rootValid });
  const resNoId = readBossPage();
  assert.equal(resNoId.detail.ok, false);
  assert.equal(resNoId.detail.job, null);
  assert.ok(resNoId.problems.some((p) => p.includes("未从地址识别到岗位 ID")));
});

test("readBossPage: 不读取招聘者、法定代表人、地址、相似岗位 (T016 / FR-011 / FR-016)", () => {
  const trackedQueries = [];
  const root = createJobDetailDOM({
    includeBossInfo: true,
    includeCompanyUser: true,
    includeCompanyAddress: true,
    includeSimilarJobs: true,
  });
  setupJobDetailMockEnv({
    pathname: "/job_detail/job_safe_check.html",
    rootElement: root,
    trackedQueries,
  });

  const res = readBossPage();

  assert.equal(res.detail.ok, true);
  const jsonStr = JSON.stringify(res.detail.job);

  assert.ok(!jsonStr.includes("张某某"), "不得包含招聘者姓名");
  assert.ok(!jsonStr.includes("王某某"), "不得包含法定代表人");
  assert.ok(!jsonStr.includes("XX路XX号"), "不得包含公司地址");
  assert.ok(!jsonStr.includes("其他相似岗位"), "不得包含相似岗位名称");
  assert.ok(!jsonStr.includes("50-70K"), "不得包含相似岗位薪资");

  // 严禁查询禁止选择器
  const forbiddenSelectors = [
    ".job-boss-info",
    "li.company-user",
    ".company-address",
    "similar-job",
    ".look-job-list",
  ];
  for (const query of trackedQueries) {
    for (const forbidden of forbiddenSelectors) {
      assert.ok(
        !query.includes(forbidden),
        `readBossPage 绝不能查询禁止选择器: ${forbidden}，实际查询: ${query}`
      );
    }
  }
});

test("decideDetailAction: 独立职位页的 send / 重读 / 最终「此页暂不支持读取」(T017 / T019)", () => {
  const validJob = {
    platform_job_id: "job_success_01",
    title: "前端工程师",
    salary_raw: "20-30K",
    city: "上海",
    district: null,
    description: "开发高质量Web前端界面。",
    company_legal_name: "上海某某科技有限公司",
  };

  const readSuccess = {
    page_kind: "job_detail_page",
    detail: { ok: true, job: validJob },
    problems: [],
  };

  const readFailed = {
    page_kind: "job_detail_page",
    detail: { ok: false, job: null },
    problems: ["缺少职位名"],
  };

  const expectedKey = `job_success_01|${contentKey(validJob)}`;

  // 1. 读到必需字段 -> send
  const decisionSend = decideDetailAction(readSuccess, null);
  assert.deepEqual(decisionSend, { action: "send", key: expectedKey });

  // 1.1 相同 key 再次读取 -> rerender
  const decisionRerender = decideDetailAction(readSuccess, expectedKey);
  assert.deepEqual(decisionRerender, { action: "rerender", key: expectedKey });

  // 2. 读不到必需字段，处于自动重试阶段 (attempt < 4) -> retry (重读)
  const decisionRetry0 = decideDetailAction(readFailed, null, 0);
  assert.deepEqual(decisionRetry0, { action: "retry", key: null });
  assert.deepEqual(shouldRetryRead(readFailed, 0, 100), {
    retry: true,
    delayMs: 500,
    showReading: true,
  });

  const decisionRetry1 = decideDetailAction(readFailed, null, 1);
  assert.deepEqual(decisionRetry1, { action: "retry", key: null });
  assert.deepEqual(shouldRetryRead(readFailed, 1, 1000), {
    retry: true,
    delayMs: 1500,
    showReading: true,
  });

  const decisionRetry3 = decideDetailAction(readFailed, null, 3);
  assert.deepEqual(decisionRetry3, { action: "retry", key: null });
  assert.deepEqual(shouldRetryRead(readFailed, 3, 5000), {
    retry: true,
    delayMs: 6000,
    showReading: true,
  });

  // 3. 重读用完仍失败 (attempt >= 4) -> 最终 "此页暂不支持读取" (unsupported)
  const decisionExhausted = decideDetailAction(readFailed, null, 4);
  assert.deepEqual(decisionExhausted, { action: "unsupported", key: null });
  assert.deepEqual(shouldRetryRead(readFailed, 4, 11000), {
    retry: false,
    delayMs: 0,
    showReading: false,
  });

  // 未指定 attempt 静态判定时，缺字段直接得出最终 unsupported
  const decisionStatic = decideDetailAction(readFailed, null);
  assert.deepEqual(decisionStatic, { action: "unsupported", key: null });
});

test("syncChatJobStatusInTabStates: 独立职位页入库后同步各标签页中的聊天岗位状态缓存 (T018 / research R8)", async () => {
  const { syncChatJobStatusInTabStates } = await import("../src/chat-view.js");
  const tabStateMap = new Map();
  const targetPid = "job_detail_sync_01";

  // Tab 1: 当前是聊天页，正在看该岗位（未入库前）
  tabStateMap.set(101, {
    tabId: 101,
    chatJobStatus: {
      platform_job_id: targetPid,
      in_library: false,
      title: "原标题",
      company_name: "原公司名",
      verdict_label: null,
    },
  });

  // Tab 2: 当前是另一个聊天页，看的是其他岗位
  tabStateMap.set(102, {
    tabId: 102,
    chatJobStatus: {
      platform_job_id: "other_job_999",
      in_library: true,
      title: "其他职位",
    },
  });

  // 独立职位页入库成功，返回了已在库的 jobEntry
  const jobEntry = {
    platform_job_id: targetPid,
    title: "独立职位页读取的标题",
    company_name: "独立职位页工商公司名",
    judgement: {
      status: "done",
      verdict: "apply",
      summary_reason: "匹配度高",
      judged_at: "2026-09-29T10:00:00Z",
    },
    my_status: { status: "interested" },
    hr_note: "已联系",
  };

  const newStatus = syncChatJobStatusInTabStates(tabStateMap, targetPid, jobEntry);

  // Tab 1 的 chatJobStatus 被更新为在库状态
  const tab1Status = tabStateMap.get(101).chatJobStatus;
  assert.equal(tab1Status.platform_job_id, targetPid);
  assert.equal(tab1Status.in_library, true);
  assert.equal(tab1Status.title, "独立职位页读取的标题");
  assert.equal(tab1Status.company_name, "独立职位页工商公司名");
  assert.equal(tab1Status.verdict_label, "适合投递");

  // Tab 2 不受影响
  assert.equal(tabStateMap.get(102).chatJobStatus.platform_job_id, "other_job_999");
});

test("background.js: handleSendDetail 对独立职位页带 company_legal_name 且不带 company_name (T018)", async () => {
  const fs = await import("node:fs");
  const bgCode = fs.readFileSync(new URL("../src/background.js", import.meta.url), "utf-8");

  // 源码中明确区分了 job_detail_page，并在 payload 中使用 company_legal_name 而非 company_name
  assert.ok(
    bgCode.includes('readResult?.page_kind === "job_detail_page"'),
    "background.js 必须区分 job_detail_page"
  );
  assert.ok(
    bgCode.includes("company_legal_name: job.company_legal_name"),
    "background.js 必须在独立职位页详情 payload 中带 company_legal_name"
  );
  assert.ok(
    bgCode.includes("syncChatJobStatusInTabStates(tabStateMap"),
    "background.js 必须在入库后调用 syncChatJobStatusInTabStates"
  );
  assert.ok(
    bgCode.includes('"chat_job_status_updated"'),
    "background.js 必须发出 chat_job_status_updated 广播"
  );
});

test("readBossPage: 公司工商登记名去掉公司名称/企业名称前缀与去首尾空格 (T016)", () => {
  const cases = [
    { input: "公司名称：北京测试甲网络科技有限公司", expected: "北京测试甲网络科技有限公司" },
    { input: "公司名称:  上海测试乙信息技术有限公司  ", expected: "上海测试乙信息技术有限公司" },
    { input: "企业名称： 深圳测试丙智能制造有限公司", expected: "深圳测试丙智能制造有限公司" },
    { input: "企业名称:广州测试丁进出口有限公司", expected: "广州测试丁进出口有限公司" },
    { input: "  杭州测试戊电子商务有限公司  ", expected: "杭州测试戊电子商务有限公司" },
    { input: "公司名称", expected: null },
  ];

  for (const c of cases) {
    const root = createJobDetailDOM({ companyLegalName: c.input });
    setupJobDetailMockEnv({ rootElement: root });
    const res = readBossPage();
    assert.equal(res.detail.ok, true);
    assert.equal(res.detail.job.company_legal_name, c.expected);
  }
});

test("readBossPage: MockElement 同时提供 innerText（含 \\n）与 textContent（无换行）时，读到的是 innerText", () => {
  const root = createJobDetailDOM({
    title: "旧职位名文本",
    titleInnerText: "高级服务端开发工程师",
    salary: "15-20K",
    salaryInnerText: "25-40K",
    city: "广州",
    cityInnerText: "深圳",
    description: "负责平台核心服务架构研发与性能调优。参与高并发微服务设计。保障线上服务稳定性。",
    descriptionInnerText: "  负责平台核心服务架构研发与性能调优。\n参与高并发微服务设计。\n保障线上服务稳定性。  ",
    companyLegalName: "企业名称：旧公司网络科技有限公司",
    companyLegalNameInnerText: "  企业名称：深圳测试网络技术有限公司  ",
  });
  setupJobDetailMockEnv({
    pathname: "/job_detail/job_inner_text_priority.html",
    rootElement: root,
  });

  const res = readBossPage();

  assert.equal(res.detail.ok, true);
  assert.equal(res.detail.job.title, "高级服务端开发工程师");
  assert.equal(res.detail.job.salary_raw, "25-40K");
  assert.equal(res.detail.job.city, "深圳");
  assert.equal(
    res.detail.job.description,
    "负责平台核心服务架构研发与性能调优。\n参与高并发微服务设计。\n保障线上服务稳定性。"
  );
  assert.ok(
    res.detail.job.description.includes("\n"),
    "职位描述必须保留 innerText 中的换行 \\n"
  );
  assert.equal(res.detail.job.company_legal_name, "深圳测试网络技术有限公司");
});

test("readBossPage: MockElement 只有 textContent 时仍能读到各字段", () => {
  const root = createJobDetailDOM({
    title: "后端开发工程师",
    salary: "30-50K",
    city: "北京",
    description: "负责分布式系统核心架构设计与开发。",
    companyLegalName: "公司名称：北京创新未来科技有限公司",
  });

  // 验证所有元素确实只有 textContent，无 innerText 属性
  const h1 = root.querySelector(".job-banner .info-primary .name h1");
  const salarySpan = root.querySelector(".job-banner .info-primary .name span.salary");
  const cityLink = root.querySelector(".job-banner .info-primary a.text-desc.text-city");
  const descEl = root.querySelector(".job-detail-section > .job-sec-text");
  const compEl = root.querySelector(".job-detail-company .business-info-box li.company-name");

  assert.equal(h1.innerText, undefined);
  assert.equal(salarySpan.innerText, undefined);
  assert.equal(cityLink.innerText, undefined);
  assert.equal(descEl.innerText, undefined);
  assert.equal(compEl.innerText, undefined);

  setupJobDetailMockEnv({
    pathname: "/job_detail/job_text_content_fallback.html",
    rootElement: root,
  });

  const res = readBossPage();

  assert.equal(res.detail.ok, true);
  assert.equal(res.detail.job.title, "后端开发工程师");
  assert.equal(res.detail.job.salary_raw, "30-50K");
  assert.equal(res.detail.job.city, "北京");
  assert.equal(res.detail.job.description, "负责分布式系统核心架构设计与开发。");
  assert.equal(res.detail.job.company_legal_name, "北京创新未来科技有限公司");
});
