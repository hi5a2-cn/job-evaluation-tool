import { contentKey } from "./scheduler.js";

/**
 * 计算详情岗位去重键：platform_job_id + "|" + contentKey(job)。
 * 与现有详情去重键一致。
 *
 * @param {object|null} job
 * @returns {string}
 */
export function detailKey(job) {
  if (!job) return "";
  const id = job.platform_job_id != null ? String(job.platform_job_id) : "";
  return `${id}|${contentKey(job)}`;
}

/**
 * 过滤列表岗位，仅返回未发送过的新岗位及其去重键。
 * 纯函数，不修改外部传入的 sentSet。
 *
 * 规则：
 * 1. 每个岗位的键为 platform_job_id + "|" + contentKey(job)；
 * 2. 只返回 sentSet 里没有的岗位；
 * 3. keys 为这些新岗位的键（由调用方在发送成功后加入 sentSet）；
 * 4. 同一批里重复的 ID 只保留一个；
 * 5. 最多 200 个。
 *
 * @param {Array<object>} jobs 列表页读取到的岗位数组
 * @param {Set<string>|null|undefined} sentSet 该标签页已发送岗位的键集合
 * @returns {{ toSend: Array<object>, keys: Array<string> }}
 */
export function newListItems(jobs, sentSet) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    return { toSend: [], keys: [] };
  }

  const toSend = [];
  const keys = [];
  const seenBatchIds = new Set();
  const set = sentSet instanceof Set ? sentSet : new Set(sentSet || []);

  for (const job of jobs) {
    if (!job || job.platform_job_id == null) continue;
    const id = String(job.platform_job_id).trim();
    if (!id) continue;

    // 同一批里重复的 ID 只保留一个
    if (seenBatchIds.has(id)) continue;
    seenBatchIds.add(id);

    const key = detailKey(job);
    // 只返回 sentSet 里没有的岗位
    if (set.has(key)) continue;

    toSend.push(job);
    keys.push(key);

    if (toSend.length >= 200) {
      break;
    }
  }

  return { toSend, keys };
}

/**
 * 依据 POST 结果决定是否更新详情去重键。
 * 成功（resOk === true）返回该岗位的 detailKey；失败返回原 lastKey（不更新，以便后续重试）。
 *
 * @param {string|null} currentKey 原有的 lastKey
 * @param {object|null} job 岗位对象
 * @param {boolean} resOk POST /v1/observations 是否成功
 * @returns {string|null}
 */
export function nextDetailKey(currentKey, job, resOk) {
  if (!resOk) {
    return currentKey ?? null;
  }
  return detailKey(job);
}
