import test from "node:test";
import assert from "node:assert/strict";
import { contentKey, decideDetailAction } from "../src/scheduler.js";
import { detailKey, nextDetailKey, newListItems } from "../src/dedupe.js";

test("detailKey matches platform_job_id + '|' + contentKey(job)", () => {
  const job = {
    platform_job_id: "job_001",
    title: "Python架构师",
    salary_raw: "30-50K",
    city: "深圳",
    description: "分布式架构",
  };
  const expected = `job_001|${contentKey(job)}`;
  assert.equal(detailKey(job), expected);
  assert.equal(detailKey(null), "");
  assert.equal(detailKey({}), `|${contentKey({})}`);
});

test("newListItems: 首次全部返回", () => {
  const jobs = [
    { platform_job_id: "job_1", title: "岗位1", salary_raw: "10-20K", city: "北京" },
    { platform_job_id: "job_2", title: "岗位2", salary_raw: "15-25K", city: "上海" },
    { platform_job_id: "job_3", title: "岗位3", salary_raw: "20-30K", city: "深圳" },
  ];
  const sentSet = new Set();
  const { toSend, keys } = newListItems(jobs, sentSet);

  assert.equal(toSend.length, 3);
  assert.equal(keys.length, 3);
  assert.deepEqual(toSend, jobs);
  assert.equal(keys[0], detailKey(jobs[0]));
  assert.equal(keys[1], detailKey(jobs[1]));
  assert.equal(keys[2], detailKey(jobs[2]));
  // 确认不修改原 sentSet
  assert.equal(sentSet.size, 0);
});

test("newListItems: 再次调用只返回新 ID", () => {
  const job1 = { platform_job_id: "job_1", title: "岗位1", salary_raw: "10-20K", city: "北京" };
  const job2 = { platform_job_id: "job_2", title: "岗位2", salary_raw: "15-25K", city: "上海" };
  const newJob3 = { platform_job_id: "job_3", title: "岗位3", salary_raw: "20-30K", city: "深圳" };

  const sentSet = new Set([detailKey(job1), detailKey(job2)]);

  const { toSend, keys } = newListItems([job1, job2, newJob3], sentSet);

  assert.equal(toSend.length, 1);
  assert.equal(keys.length, 1);
  assert.equal(toSend[0].platform_job_id, "job_3");
  assert.equal(keys[0], detailKey(newJob3));
});

test("newListItems: 内容变化（薪资改变）的同 ID 视为新", () => {
  const oldJob = { platform_job_id: "job_1", title: "Python开发", salary_raw: "10-15K", city: "深圳" };
  const sentSet = new Set([detailKey(oldJob)]);

  const changedJob = { platform_job_id: "job_1", title: "Python开发", salary_raw: "15-25K", city: "深圳" };
  const { toSend, keys } = newListItems([changedJob], sentSet);

  assert.equal(toSend.length, 1);
  assert.equal(toSend[0].platform_job_id, "job_1");
  assert.equal(toSend[0].salary_raw, "15-25K");
  assert.equal(keys[0], detailKey(changedJob));
  assert.notEqual(keys[0], detailKey(oldJob));
});

test("newListItems: 同批重复去重（同一批里重复的 ID 只保留一个）", () => {
  const job1a = { platform_job_id: "dup_id", title: "职位A", salary_raw: "10-15K", city: "深圳" };
  const job1b = { platform_job_id: "dup_id", title: "职位B", salary_raw: "12-18K", city: "深圳" };
  const job2 = { platform_job_id: "unique_id", title: "职位C", salary_raw: "20-30K", city: "广州" };

  const { toSend, keys } = newListItems([job1a, job1b, job2], new Set());

  assert.equal(toSend.length, 2);
  assert.equal(keys.length, 2);
  assert.equal(toSend[0].platform_job_id, "dup_id");
  assert.equal(toSend[0].title, "职位A"); // 保留同批出现的首个
  assert.equal(toSend[1].platform_job_id, "unique_id");
});

test("newListItems: 超过 200 截断", () => {
  const jobs = [];
  for (let i = 0; i < 250; i++) {
    jobs.push({
      platform_job_id: `job_${i}`,
      title: `工程师_${i}`,
      salary_raw: "10-20K",
      city: "深圳",
    });
  }

  const { toSend, keys } = newListItems(jobs, new Set());

  assert.equal(toSend.length, 200);
  assert.equal(keys.length, 200);
  assert.equal(toSend[0].platform_job_id, "job_0");
  assert.equal(toSend[199].platform_job_id, "job_199");
});

test("newListItems: 空 sentSet（模拟刷新）全部重发", () => {
  const jobs = [
    { platform_job_id: "job_1", title: "岗位1", salary_raw: "10-20K", city: "北京" },
    { platform_job_id: "job_2", title: "岗位2", salary_raw: "15-25K", city: "上海" },
  ];

  // 1. 传入 null
  const resNull = newListItems(jobs, null);
  assert.equal(resNull.toSend.length, 2);
  assert.equal(resNull.keys.length, 2);

  // 2. 传入 undefined
  const resUndef = newListItems(jobs, undefined);
  assert.equal(resUndef.toSend.length, 2);

  // 3. 传入空的 Set
  const resEmpty = newListItems(jobs, new Set());
  assert.equal(resEmpty.toSend.length, 2);
});

test("newListItems: 处理无效参数与边界情况", () => {
  assert.deepEqual(newListItems(null, new Set()), { toSend: [], keys: [] });
  assert.deepEqual(newListItems([], new Set()), { toSend: [], keys: [] });
  assert.deepEqual(newListItems([null, undefined, {}], new Set()), { toSend: [], keys: [] });
});

test("Jet 未运行（POST 失败）时不把详情键记为已发送 (T096)", () => {
  const job = {
    platform_job_id: "job_t096",
    title: "前端工程师",
    salary_raw: "25-40K",
    city: "上海",
    description: "React架构",
  };
  const expectedKey = detailKey(job);

  // 1. POST 失败时 nextDetailKey 不记录新键，保持原有 lastKey（如 null）
  const keyAfterFail = nextDetailKey(null, job, false);
  assert.equal(keyAfterFail, null, "POST 失败时不得记录详情键");

  // 若已有旧 key，POST 失败也不更新为新 job 的 key
  const oldKey = "old_job|12345";
  assert.equal(nextDetailKey(oldKey, job, false), oldKey);

  // 2. 当 lastKey 未被记录时，decideDetailAction 针对相同岗位依然返回 'send'，允许重试
  const readResult = {
    page_kind: "search_list",
    detail: { ok: true, job },
    problems: [],
  };
  const retryDecision = decideDetailAction(readResult, keyAfterFail);
  assert.equal(retryDecision.action, "send", "详情键未记录时，调度动作必须保持 send");

  // 3. POST 成功时才记录详情键
  const keyAfterSuccess = nextDetailKey(null, job, true);
  assert.equal(keyAfterSuccess, expectedKey);

  // 成功记录后，再次读取相同岗位调度动作为 rerender
  const rerenderDecision = decideDetailAction(readResult, keyAfterSuccess);
  assert.equal(rerenderDecision.action, "rerender");

  // 4. 列表观察发送：POST 失败时 sentSet 不记录，下次依然包含在 toSend 中
  const listJobs = [job];
  const sentSet = new Set();
  const { toSend, keys } = newListItems(listJobs, sentSet);
  assert.equal(toSend.length, 1);
  assert.equal(keys.length, 1);
  // 模拟 POST 失败，keys 不加入 sentSet
  assert.equal(sentSet.size, 0);
  // 再次调用仍然能被发送
  const retryList = newListItems(listJobs, sentSet);
  assert.equal(retryList.toSend.length, 1);
});
