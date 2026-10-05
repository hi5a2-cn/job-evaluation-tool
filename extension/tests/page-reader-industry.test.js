import test from "node:test";
import assert from "node:assert/strict";
import { readBossPage } from "../src/page-reader.js";

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

test("readBossPage: list company_industry extracts brandIndustry (trimmed, empty/whitespace/missing -> null)", () => {
  const jobs = [
    { encryptJobId: "j1", jobName: "岗位1", brandName: "腾讯", brandIndustry: "互联网", industry: 100020 },
    { encryptJobId: "j2", jobName: "岗位2", brandName: "阿里", brandIndustry: "  电子商务  ", industry: 100001 },
    { encryptJobId: "j3", jobName: "岗位3", brandName: "美团", brandIndustry: "", industry: 100002 },
    { encryptJobId: "j4", jobName: "岗位4", brandName: "字节", brandIndustry: "   " },
    { encryptJobId: "j5", jobName: "岗位5", brandName: "百度", brandIndustry: null },
    { encryptJobId: "j6", jobName: "岗位6", brandName: "华为" }, // 缺失 brandIndustry
    { encryptJobId: "j7", jobName: "岗位7", brandName: "某司", industry: 9999 }, // 仅有数字 industry，无 brandIndustry
  ];
  const vue = {
    hasMore: false,
    jobList: jobs,
    jobDetail: null,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, true);
  assert.equal(result.list.jobs.length, 7);

  // 1. 正常字符串
  assert.equal(result.list.jobs[0].company_industry, "互联网");
  // 2. 去除首尾空格
  assert.equal(result.list.jobs[1].company_industry, "电子商务");
  // 3. 空字符串 -> null
  assert.equal(result.list.jobs[2].company_industry, null);
  // 4. 纯空白字符串 -> null
  assert.equal(result.list.jobs[3].company_industry, null);
  // 5. null -> null
  assert.equal(result.list.jobs[4].company_industry, null);
  // 6. 缺失 -> null
  assert.equal(result.list.jobs[5].company_industry, null);
  // 7. 绝不读取数字 industry -> null
  assert.equal(result.list.jobs[6].company_industry, null);

  restoreGlobals();
});

test("readBossPage: detail company_industry resolves from matching listItem.brandIndustry only", () => {
  // Case A: 找到 jobList 同 ID 且有 brandIndustry
  const vueA = {
    jobList: [
      { encryptJobId: "d1", jobName: "开发1", brandName: "理想汽车", brandIndustry: "新能源汽车", industry: 100801 },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d1",
        jobName: "开发1",
        postDescription: "职责描述1",
        salaryDesc: "30-50K",
      },
      brandComInfo: {
        brandName: "理想汽车",
        brandIndustry: "汽车研发/制造", // 即使 brandComInfo 有，绝不能读取
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueA });
  const resA = readBossPage();
  assert.equal(resA.detail.ok, true);
  assert.equal(resA.detail.job.company_name, "理想汽车");
  assert.equal(resA.detail.job.company_industry, "新能源汽车");

  // Case B: jobList 中该 ID 存在但 brandIndustry 为空，且 brandComInfo 有值 -> 仍为 null（不读 brandComInfo）
  const vueB = {
    jobList: [
      { encryptJobId: "d2", jobName: "开发2", brandName: "宝洁", brandIndustry: "   ", industry: 100101 },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d2",
        jobName: "开发2",
        postDescription: "职责描述2",
      },
      brandComInfo: {
        brandName: "宝洁",
        brandIndustry: "日化", // 绝不能读取 brandComInfo
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueB });
  const resB = readBossPage();
  assert.equal(resB.detail.ok, true);
  assert.equal(resB.detail.job.company_name, "宝洁");
  assert.equal(resB.detail.job.company_industry, null);

  // Case C: jobList 找不到该 ID，即使 brandComInfo 有 brandIndustry -> 仍为 null
  const vueC = {
    jobList: [
      { encryptJobId: "other_id", jobName: "其他", brandName: "联合利华", brandIndustry: "快速消费品" },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d3",
        jobName: "开发3",
        postDescription: "职责描述3",
      },
      brandComInfo: {
        brandName: "联合利华",
        brandIndustry: "快速消费品",
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueC });
  const resC = readBossPage();
  assert.equal(resC.detail.ok, true);
  // company_name 会回退到 brandComInfo
  assert.equal(resC.detail.job.company_name, "联合利华");
  // company_industry 绝不回退到 brandComInfo，必须为 null
  assert.equal(resC.detail.job.company_industry, null);

  // Case D: jobList 中 listItem 只有数字 industry，没有 brandIndustry -> null
  const vueD = {
    jobList: [
      { encryptJobId: "d4", jobName: "开发4", brandName: "腾讯", industry: 100020 },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d4",
        jobName: "开发4",
        postDescription: "职责描述4",
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueD });
  const resD = readBossPage();
  assert.equal(resD.detail.ok, true);
  assert.equal(resD.detail.job.company_industry, null);

  restoreGlobals();
});
