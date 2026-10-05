// 自动标记「已投递」纯函数模块
// 与 content.js 保持一致

/**
 * 校验按钮 ka 属性是否与当前岗位 ID 匹配
 * ka 存在且不包含当前岗位 ID 时返回 false；ka 不存在时照常返回 true
 *
 * @param {string|null|undefined} ka 按钮的 ka 属性
 * @param {string|null|undefined} jobId 当前岗位 ID
 * @returns {boolean} 是否匹配
 */
function kaMatchesJobId(ka, jobId) {
  if (ka === null || ka === undefined || ka === "") {
    return true;
  }
  if (typeof ka !== "string" || typeof jobId !== "string" || jobId.length === 0) {
    return false;
  }
  return ka.indexOf(jobId) !== -1;
}

/**
 * 从 /job_detail/<id>.html 解析岗位 ID 纯函数
 *
 * @param {string|null|undefined} url 页面路径或完整 URL
 * @returns {string|null} 岗位 ID，解析失败返回 null
 */
function parseJobDetailUrlId(url) {
  if (typeof url !== "string") {
    return null;
  }
  const match = url.match(/\/job_detail\/([^/?#.]+)\.html/);
  return match && match[1] ? match[1].trim() : null;
}

/**
 * 决定点击时是否需要自动标记为「已投递」纯函数
 *
 * @param {object} params
 * @param {string} [params.pathname] 页面 location.pathname
 * @param {string} [params.buttonText] 按钮的文本内容
 * @param {string|null} [params.ka] 按钮的 ka 属性
 * @param {string|null} [params.currentJobId] 当前详情对应的岗位 ID
 * @param {string|null} [params.urlJobId] 独立职位页从地址解析出的岗位 ID
 * @param {string|object|null} [params.currentStatus] 当前岗位的状态
 * @param {boolean} [params.inOtherJobArea] 独立职位页是否处于其他岗位区域（如相似岗位、推荐岗位）
 * @returns {{ action: "mark" | "skip", reason: string }}
 */
function decideAutoApply(params) {
  if (!params || typeof params !== "object") {
    return { action: "skip", reason: "missing_params" };
  }
  const pathname = params.pathname;
  const buttonText = params.buttonText;
  const ka = params.ka;
  const currentJobId = params.currentJobId;
  const urlJobId = params.urlJobId;
  const currentStatus = params.currentStatus;
  const inOtherJobArea = params.inOtherJobArea;

  // 1. 仅在搜索列表页（/web/geek/job 开头）与独立职位页（/job_detail/ 开头）生效
  if (
    typeof pathname !== "string" ||
    (!pathname.startsWith("/web/geek/job") && !pathname.startsWith("/job_detail/"))
  ) {
    return { action: "skip", reason: "not_search_list" };
  }

  // 2. 按钮文字必须恰好是「立即沟通」
  const text = typeof buttonText === "string" ? buttonText.trim() : "";
  if (text !== "立即沟通") {
    return { action: "skip", reason: "not_chat_button" };
  }

  // 3. 读取到的当前岗位 ID 必须存在
  const jobId = typeof currentJobId === "string" ? currentJobId.trim() : "";
  if (!jobId) {
    return { action: "skip", reason: "no_current_job_id" };
  }

  // 4. 独立职位页需核对地址中的岗位 ID，与读取到的当前岗位 ID 一致才标记
  if (pathname.startsWith("/job_detail/")) {
    if (Boolean(inOtherJobArea)) {
      return { action: "skip", reason: "other_job_area" };
    }
    const urlId = typeof urlJobId === "string" ? urlJobId.trim() : "";
    if (!urlId) {
      return { action: "skip", reason: "no_url_job_id" };
    }
    if (urlId !== jobId) {
      return { action: "skip", reason: "url_job_id_mismatch" };
    }
  }

  // 5. 核对 ka 属性：存在且不包含当前岗位 ID 时不标记
  if (!kaMatchesJobId(ka, jobId)) {
    return { action: "skip", reason: "ka_mismatch" };
  }

  // 6. 当前状态已经是「已投递」（applied）时不标记
  let statusKey = currentStatus;
  if (currentStatus && typeof currentStatus === "object" && typeof currentStatus.status === "string") {
    statusKey = currentStatus.status;
  }
  if (statusKey === "applied") {
    return { action: "skip", reason: "already_applied" };
  }

  return { action: "mark", reason: "ok" };
}

export { kaMatchesJobId, parseJobDetailUrlId, decideAutoApply };
