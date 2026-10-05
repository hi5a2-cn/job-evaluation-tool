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

// 构造 15 个列表岗位样本：
// - 岗位 3: 空薪资（""）
// - 岗位 7: 空区域（""）
// - 岗位 8: 空区域（null）
function createSampleJobList() {
  const jobs = [];
  for (let i = 1; i <= 15; i++) {
    const id = `job_${String(i).padStart(2, "0")}`;
    let salaryDesc = "20-35K";
    let areaDistrict = "南山区";

    if (i === 3) {
      salaryDesc = ""; // 空薪资
    }
    if (i === 7) {
      areaDistrict = ""; // 空区域
    }
    if (i === 8) {
      areaDistrict = null; // 空区域
    }

    jobs.push({
      encryptJobId: id,
      jobName: `工程师岗位 ${i}`,
      salaryDesc: salaryDesc,
      cityName: "深圳",
      areaDistrict: areaDistrict,
    });
  }
  return jobs;
}

// 构造 1 个详情样本：
// 包含 "银\u2F8F"（U+2F8F 康熙部首）和多行空白
const sampleDescriptionWithU2F8F = "岗位职责：\n\n  1. 负责核心系统研发\n  2. 具备银\u2F8F系统背景者优先\n\n  3. 熟练掌握现代架构\n\n";

function createSampleDetail() {
  return {
    jobInfo: {
      encryptId: "detail_job_01",
      jobName: "高级软件架构师",
      salaryDesc: "30-50K·15薪",
      locationName: "深圳",
      postDescription: sampleDescriptionWithU2F8F,
    },
  };
}

function deepFreezeAndTrack(obj, mutations) {
  if (obj === null || typeof obj !== "object") {
    return obj;
  }
  const handler = {
    set(target, prop, value, receiver) {
      mutations.push({ type: "set", prop: String(prop) });
      return Reflect.set(target, prop, value, receiver);
    },
    defineProperty(target, prop, descriptor) {
      mutations.push({ type: "defineProperty", prop: String(prop) });
      return Reflect.defineProperty(target, prop, descriptor);
    },
    deleteProperty(target, prop) {
      mutations.push({ type: "deleteProperty", prop: String(prop) });
      return Reflect.deleteProperty(target, prop);
    },
  };

  const copy = Array.isArray(obj) ? [] : {};
  for (const k of Object.keys(obj)) {
    copy[k] = deepFreezeAndTrack(obj[k], mutations);
  }
  Object.freeze(copy);
  return new Proxy(copy, handler);
}

test.afterEach(() => {
  restoreGlobals();
});

test("readBossPage: normal search_list with 15 jobs and detail", () => {
  const jobList = createSampleJobList();
  const jobDetail = createSampleDetail();
  const vue = {
    hasMore: true,
    jobList,
    jobDetail,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();

  assert.equal(result.reader_version, 1);
  assert.equal(result.page_kind, "search_list");
  assert.equal(result.problems.length, 0);

  // List assertions
  assert.equal(result.list.ok, true);
  assert.equal(result.list.has_more, true);
  assert.equal(result.list.jobs.length, 15);

  // 空薪资 -> null
  assert.equal(result.list.jobs[2].platform_job_id, "job_03");
  assert.equal(result.list.jobs[2].salary_raw, null);

  // 空区域 -> null
  assert.equal(result.list.jobs[6].platform_job_id, "job_07");
  assert.equal(result.list.jobs[6].district, null);
  assert.equal(result.list.jobs[7].platform_job_id, "job_08");
  assert.equal(result.list.jobs[7].district, null);

  // 正常岗位
  assert.equal(result.list.jobs[0].platform_job_id, "job_01");
  assert.equal(result.list.jobs[0].title, "工程师岗位 1");
  assert.equal(result.list.jobs[0].salary_raw, "20-35K");
  assert.equal(result.list.jobs[0].city, "深圳");
  assert.equal(result.list.jobs[0].district, "南山区");

  // Detail assertions
  assert.equal(result.detail.ok, true);
  assert.ok(result.detail.job);
  assert.equal(result.detail.job.platform_job_id, "detail_job_01");
  assert.equal(result.detail.job.title, "高级软件架构师");
  assert.equal(result.detail.job.salary_raw, "30-50K·15薪");
  assert.equal(result.detail.job.city, "深圳");
  assert.equal(result.detail.job.district, null);
  // 原文保留（不压缩空白，包含 U+2F8F）
  assert.equal(result.detail.job.description, sampleDescriptionWithU2F8F);
  assert.ok(result.detail.job.description.includes("\u2F8F"));
});

test("readBossPage: jobList is not an array -> ok=false and problems noted", () => {
  const vue = {
    jobList: "invalid_not_array",
    jobDetail: null,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, false);
  assert.deepEqual(result.list.jobs, []);
  assert.ok(result.problems.some((p) => p.includes("jobList 不是数组")));
});

test("readBossPage: job missing encryptJobId or jobName -> ok=false and problems noted", () => {
  const jobs = [
    { encryptJobId: "j1", jobName: "岗位1", salaryDesc: "10K" },
    { encryptJobId: "", jobName: "岗位2" }, // 缺少 encryptJobId
  ];
  const vue = { jobList: jobs };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, false);
  assert.ok(result.problems.some((p) => p.includes("缺少 encryptJobId 或 jobName")));
});

test("readBossPage: path /job_detail/abc.html -> page_kind = job_detail_page", () => {
  setupMockEnv({ pathname: "/job_detail/abc12345.html" });

  const result = readBossPage();
  assert.equal(result.page_kind, "job_detail_page");
  assert.equal(result.list.ok, false);
  assert.equal(result.detail.ok, false);
});

test("readBossPage: verify page or blank document -> captcha_or_blank", () => {
  // 1. verify in path
  setupMockEnv({ pathname: "/web/geek/verify/slider" });
  const r1 = readBossPage();
  assert.equal(r1.page_kind, "captcha_or_blank");

  // 2. security in path
  setupMockEnv({ pathname: "/security-check.html" });
  const r2 = readBossPage();
  assert.equal(r2.page_kind, "captcha_or_blank");

  // 3. body has no children
  setupMockEnv({ pathname: "/web/geek/job", bodyChildrenCount: 0 });
  const r3 = readBossPage();
  assert.equal(r3.page_kind, "captcha_or_blank");
});

test("readBossPage: truncates list when jobs exceed 200 items", () => {
  const jobs = [];
  for (let i = 0; i < 205; i++) {
    jobs.push({
      encryptJobId: `job_${i}`,
      jobName: `Job ${i}`,
      salaryDesc: "15-20K",
    });
  }
  const vue = { jobList: jobs };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, true);
  assert.equal(result.list.jobs.length, 200);
  assert.ok(result.problems.some((p) => p.includes("超过 200 条")));
});

test("readBossPage: detail not opened yet -> detail={ok:false, job:null} without problems", () => {
  const vue = {
    jobList: [{ encryptJobId: "j1", jobName: "前端开发" }],
    jobDetail: null,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.detail.ok, false);
  assert.equal(result.detail.job, null);
  assert.equal(result.problems.length, 0);
});

test("readBossPage: detail missing required fields -> detail.ok=false with problems", () => {
  const vue = {
    jobList: [{ encryptJobId: "j1", jobName: "前端开发" }],
    jobDetail: {
      jobInfo: {
        encryptId: "j1",
        jobName: "前端开发",
        postDescription: null, // 缺描述
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.detail.ok, false);
  assert.equal(result.detail.job, null);
  assert.ok(result.problems.some((p) => p.includes("详情缺少必要字段")));
});

test("readBossPage: strictly read-only guarantee via deep freeze and Proxy", () => {
  const mutations = [];
  const rawVue = {
    hasMore: true,
    jobList: createSampleJobList(),
    jobDetail: createSampleDetail(),
  };

  const frozenVue = deepFreezeAndTrack(rawVue, mutations);
  setupMockEnv({ pathname: "/web/geek/job", vue: frozenVue });

  const keysBefore = Reflect.ownKeys(globalThis);
  const result = readBossPage();
  const keysAfter = Reflect.ownKeys(globalThis);

  // 1. 无任何写属性 / 定义属性 / 删除属性动作
  assert.deepEqual(mutations, []);

  // 2. 正常读取出数据
  assert.equal(result.list.ok, true);
  assert.equal(result.detail.ok, true);

  // 3. globalThis 上没有新增任何属性
  const addedKeys = keysAfter.filter((k) => !keysBefore.includes(k));
  assert.deepEqual(addedKeys, []);
});

test("readBossPage: source code does not contain forbidden operations", () => {
  const fnStr = readBossPage.toString();

  assert.ok(!fnStr.includes("fetch"), "readBossPage must not contain fetch");
  assert.ok(!fnStr.includes("XMLHttpRequest"), "readBossPage must not contain XMLHttpRequest");
  assert.ok(!fnStr.includes("addEventListener"), "readBossPage must not contain addEventListener");
  assert.ok(!fnStr.includes(".click("), "readBossPage must not contain .click(");
  assert.ok(!fnStr.includes("scroll"), "readBossPage must not contain scroll");
  assert.ok(!fnStr.includes("location.href ="), "readBossPage must not contain location.href =");
  assert.ok(!fnStr.includes("__jet"), "readBossPage must not contain __jet");
});

test("readBossPage: list company_name with brandName present, empty, or missing", () => {
  const jobs = [
    { encryptJobId: "j1", jobName: "前端开发", brandName: "腾讯科技" },
    { encryptJobId: "j2", jobName: "后端开发", brandName: "  " },
    { encryptJobId: "j3", jobName: "测试开发", brandName: null },
    { encryptJobId: "j4", jobName: "算法专家" }, // 缺失 brandName
  ];
  const vue = {
    hasMore: false,
    jobList: jobs,
    jobDetail: null,
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });

  const result = readBossPage();
  assert.equal(result.list.ok, true);
  assert.equal(result.list.jobs[0].company_name, "腾讯科技");
  assert.equal(result.list.jobs[1].company_name, null);
  assert.equal(result.list.jobs[2].company_name, null);
  assert.equal(result.list.jobs[3].company_name, null);
});

test("readBossPage: detail company_name resolves from jobList first, then brandComInfo, then null", () => {
  // Case A: 找到 jobList 同 ID 且有 brandName
  const vueA = {
    jobList: [
      { encryptJobId: "d1", jobName: "开发1", brandName: "阿里巴巴" },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d1",
        jobName: "开发1",
        postDescription: "职责描述",
      },
      brandComInfo: {
        brandName: "美团", // 即使 brandComInfo 也有，优先使用 jobList 的
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueA });
  const resA = readBossPage();
  assert.equal(resA.detail.ok, true);
  assert.equal(resA.detail.job.company_name, "阿里巴巴");

  // Case B: jobList 中该 ID 缺失或空 brandName，回退到 brandComInfo
  const vueB = {
    jobList: [
      { encryptJobId: "d2", jobName: "开发2", brandName: "" },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d2",
        jobName: "开发2",
        postDescription: "职责描述",
      },
      brandComInfo: {
        brandName: "美团",
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueB });
  const resB = readBossPage();
  assert.equal(resB.detail.ok, true);
  assert.equal(resB.detail.job.company_name, "美团");

  // Case C: jobList 中找不到该 ID，回退到 brandComInfo
  const vueC = {
    jobList: [
      { encryptJobId: "other_id", jobName: "其他", brandName: "百度" },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d3",
        jobName: "开发3",
        postDescription: "职责描述",
      },
      brandComInfo: {
        brandName: "字节跳动",
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueC });
  const resC = readBossPage();
  assert.equal(resC.detail.ok, true);
  assert.equal(resC.detail.job.company_name, "字节跳动");

  // Case D: 两处都没有 brandName -> null
  const vueD = {
    jobList: [
      { encryptJobId: "d4", jobName: "开发4" },
    ],
    jobDetail: {
      jobInfo: {
        encryptId: "d4",
        jobName: "开发4",
        postDescription: "职责描述",
      },
      brandComInfo: null,
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueD });
  const resD = readBossPage();
  assert.equal(resD.detail.ok, true);
  assert.equal(resD.detail.job.company_name, null);
});

test("readBossPage: extracts experience and degree in list jobs (FR-009)", () => {
  const vue = {
    jobList: [
      {
        encryptJobId: "job_exp_1",
        jobName: "后端开发",
        salaryDesc: "20-30K",
        cityName: "深圳",
        areaDistrict: "南山区",
        jobExperience: "3-5年",
        jobDegree: "本科",
      },
      {
        encryptJobId: "job_exp_2",
        jobName: "前端开发",
        salaryDesc: "15-25K",
        cityName: "广州",
        // missing jobExperience and jobDegree
      },
    ],
  };
  setupMockEnv({ pathname: "/web/geek/job", vue });
  const res = readBossPage();
  assert.equal(res.list.ok, true);
  assert.equal(res.list.jobs.length, 2);

  const j1 = res.list.jobs[0];
  assert.equal(j1.experience, "3-5年");
  assert.equal(j1.degree, "本科");

  const j2 = res.list.jobs[1];
  assert.equal(j2.experience, null);
  assert.equal(j2.degree, null);
});

test("readBossPage: extracts experience and degree in search list detail (FR-010)", () => {
  // Case A: 存在 experienceName 与 degreeName
  const vueA = {
    jobDetail: {
      jobInfo: {
        encryptId: "detail_exp_1",
        jobName: "产品运营",
        salaryDesc: "15-25K",
        locationName: "深圳",
        postDescription: "产品设计与运营职责描述",
        experienceName: "  3-5年  ",
        degreeName: " 本科 ",
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueA });
  const resA = readBossPage();
  assert.equal(resA.detail.ok, true);
  assert.equal(resA.detail.job.experience, "3-5年");
  assert.equal(resA.detail.job.degree, "本科");

  // Case B: 空白字符串与缺失字段返回 null
  const vueB = {
    jobDetail: {
      jobInfo: {
        encryptId: "detail_exp_2",
        jobName: "产品运营2",
        salaryDesc: "15-25K",
        locationName: "深圳",
        postDescription: "职责描述",
        experienceName: "   ",
        degreeName: null,
      },
    },
  };
  setupMockEnv({ pathname: "/web/geek/job", vue: vueB });
  const resB = readBossPage();
  assert.equal(resB.detail.ok, true);
  assert.equal(resB.detail.job.experience, null);
  assert.equal(resB.detail.job.degree, null);
});
