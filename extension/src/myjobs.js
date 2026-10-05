// 这是用户点击触发的打开链接，不属于自动化，不违反原则 IV。
// 页面从不在没有用户点击时打开任何页面。

import {
  toRow,
  isSafeBossJobUrl,
  getStatusRemovalNotice,
  updateCountsOnStatusChange,
  formatTotalMatchesNotice,
  createRequestTracker,
  rollbackStatusChange,
  isHrNoteOverflowing,
} from "./myjobs-view.js";
import { decideHrNoteAutoSave, createAutoSaveCoordinator } from "./label-form.js";

function sendMessageAsync(message) {
  return new Promise((resolve) => {
    if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      resolve({
        ok: false,
        status: 0,
        viewState: "jet_down",
        error: "chrome.runtime unavailable",
      });
      return;
    }
    try {
      chrome.runtime.sendMessage(message, (res) => {
        if (chrome.runtime?.lastError) {
          resolve({
            ok: false,
            status: 0,
            viewState: "jet_down",
            error: chrome.runtime.lastError.message || "Jet 未运行",
          });
          return;
        }
        resolve(res || { ok: false, error: "empty_response" });
      });
    } catch (err) {
      resolve({
        ok: false,
        status: 0,
        viewState: "jet_down",
        error: err?.message || String(err),
      });
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const noticeContainer = document.getElementById("notice-container");
  const totalMatchesNotice = document.getElementById("total-matches-notice");
  const filtersBar = document.getElementById("filters-bar");
  const jobsContainer = document.getElementById("jobs-container");
  const searchInput = document.getElementById("search-input");
  const hrOnlyCheckbox = document.getElementById("hr-only-checkbox");

  const countEls = {
    all_jobs: document.getElementById("count-all_jobs"),
    all: document.getElementById("count-all"),
    saved: document.getElementById("count-saved"),
    applied: document.getElementById("count-applied"),
    skipped: document.getElementById("count-skipped"),
    recent: document.getElementById("count-recent"),
  };

  const filterButtons = document.querySelectorAll(".filter-btn");

  let currentFilter = "all";
  let isInitialLoad = true;
  let currentCounts = {
    all_jobs: 0,
    all: 0,
    saved: 0,
    applied: 0,
    skipped: 0,
    recent: 0,
  };
  let searchTimer = null;

  function clearNotice() {
    while (noticeContainer.firstChild) {
      noticeContainer.removeChild(noticeContainer.firstChild);
    }
  }

  function showNotice(text, type = "error", actionText = null, onAction = null) {
    clearNotice();
    const card = document.createElement("div");
    card.className = `notice-card ${type}`;

    const textSpan = document.createElement("span");
    textSpan.textContent = text;
    card.appendChild(textSpan);

    if (actionText && typeof onAction === "function") {
      const actionLink = document.createElement("a");
      actionLink.className = "notice-link";
      actionLink.textContent = actionText;
      actionLink.addEventListener("click", onAction);
      card.appendChild(actionLink);
    }

    noticeContainer.appendChild(card);
  }

  function updateCounts(counts) {
    if (!counts) return;
    for (const key of Object.keys(countEls)) {
      if (countEls[key] && counts[key] !== undefined) {
        countEls[key].textContent = `(${counts[key]})`;
      }
    }
  }

  function setActiveFilter(filter) {
    currentFilter = filter;
    for (const btn of filterButtons) {
      if (btn.getAttribute("data-filter") === filter) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    }
  }

  function renderList(items, filter) {
    while (jobsContainer.firstChild) {
      jobsContainer.removeChild(jobsContainer.firstChild);
    }

    if (!Array.isArray(items) || items.length === 0) {
      const emptyDiv = document.createElement("div");
      emptyDiv.className = "empty-state";
      emptyDiv.textContent = "这里还没有岗位";
      jobsContainer.appendChild(emptyDiv);
      return;
    }

    for (const item of items) {
      const card = renderJobCard(item, filter);
      jobsContainer.appendChild(card);
    }
  }

  function renderJobCard(item, filter) {
    const row = toRow(item, filter);

    const card = document.createElement("div");
    card.className = "job-card";
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");

    // 顶部行：职位名称与结论徽标
    const header = document.createElement("div");
    header.className = "job-card-header";

    const titleEl = document.createElement("div");
    titleEl.className = "job-title";
    titleEl.textContent = row.title || "（无职位名）";
    header.appendChild(titleEl);

    const badgeEl = document.createElement("span");
    badgeEl.className = `job-badge ${row.verdict_tone || "neutral"}${row.stale_style ? " stale" : ""}`;
    if (row.stale_style) {
      badgeEl.style.opacity = "0.7";
      badgeEl.style.border = "1px dashed currentColor";
    }
    badgeEl.textContent = row.verdict_text;
    header.appendChild(badgeEl);

    card.appendChild(header);

    // 元信息行：公司、薪资、城市、最近查看
    const meta = document.createElement("div");
    meta.className = "job-meta-row";

    const companyEl = document.createElement("span");
    companyEl.className = `job-company ${row.company_is_empty ? "muted" : ""}`;
    companyEl.textContent = row.company;
    meta.appendChild(companyEl);

    const salaryEl = document.createElement("span");
    salaryEl.className = `job-salary ${row.salary_is_empty ? "muted" : ""}`;
    salaryEl.textContent = row.salary;
    meta.appendChild(salaryEl);

    if (row.city) {
      const cityEl = document.createElement("span");
      cityEl.className = "job-city";
      cityEl.textContent = row.city;
      meta.appendChild(cityEl);
    }

    if (row.show_last_seen && row.last_seen_label) {
      const lastSeenEl = document.createElement("span");
      lastSeenEl.className = "job-last-seen";
      lastSeenEl.textContent = row.last_seen_label;
      meta.appendChild(lastSeenEl);
    }

    card.appendChild(meta);

    // --- HR 实际情况区块 (T042) ---
    const hrBlock = document.createElement("div");
    hrBlock.className = "job-hr-block";

    const hrHeader = document.createElement("div");
    hrHeader.className = "job-hr-header";

    const hrLabel = document.createElement("span");
    hrLabel.className = "job-hr-label";
    hrLabel.textContent = "HR 实际情况：";
    hrHeader.appendChild(hrLabel);

    const hrActions = document.createElement("div");
    hrActions.className = "job-hr-actions";

    // 展开/收起 按钮
    const expandBtn = document.createElement("button");
    expandBtn.type = "button";
    expandBtn.className = "hr-btn-link";
    expandBtn.textContent = "展开";
    expandBtn.style.display = "none";
    hrActions.appendChild(expandBtn);

    // 修改 按钮
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "hr-btn-link";
    editBtn.textContent = "修改";
    hrActions.appendChild(editBtn);

    hrHeader.appendChild(hrActions);
    hrBlock.appendChild(hrHeader);

    // 展示内容（最多两行 CSS 截断）
    const hrContentEl = document.createElement("div");
    hrContentEl.className = `job-hr-content ${row.hr_note ? "" : "empty"}`;
    hrContentEl.textContent = row.hr_note_content;
    hrBlock.appendChild(hrContentEl);

    // 行内编辑区域
    const editArea = document.createElement("div");
    editArea.className = "job-hr-edit-area";
    editArea.style.display = "none";

    const textarea = document.createElement("textarea");
    textarea.className = "job-hr-textarea";
    textarea.value = row.hr_note || "";
    textarea.placeholder = "记录 HR 透露的实际情况（≤ 200 字，清空请到岗位卡片）";
    textarea.maxLength = 200;
    textarea.addEventListener("click", (e) => e.stopPropagation());
    textarea.addEventListener("keydown", (e) => e.stopPropagation());
    editArea.appendChild(textarea);

    const editFooter = document.createElement("div");
    editFooter.className = "job-hr-edit-footer";

    const editError = document.createElement("span");
    editError.className = "job-hr-edit-error";
    editFooter.appendChild(editError);

    const editBtns = document.createElement("div");
    editBtns.className = "job-hr-edit-btns";

    const doneBtn = document.createElement("button");
    doneBtn.type = "button";
    doneBtn.className = "btn-sm primary";
    doneBtn.textContent = "完成";
    editBtns.appendChild(doneBtn);

    editFooter.appendChild(editBtns);
    editArea.appendChild(editFooter);
    hrBlock.appendChild(editArea);

    card.appendChild(hrBlock);

    // 测量内容元素两行截断下是否溢出，决定是否展示"展开"
    function updateExpandBtnVisibility() {
      if (editArea.style.display !== "none") {
        expandBtn.style.display = "none";
        return;
      }
      if (!row.hr_note || row.hr_note.trim().length === 0) {
        expandBtn.style.display = "none";
        return;
      }
      const wasExpanded = hrContentEl.classList.contains("expanded");
      if (wasExpanded) {
        hrContentEl.classList.remove("expanded");
      }
      const overflowing = isHrNoteOverflowing(hrContentEl.scrollHeight, hrContentEl.clientHeight);
      if (wasExpanded) {
        hrContentEl.classList.add("expanded");
      }
      if (overflowing) {
        expandBtn.style.display = "";
        expandBtn.textContent = wasExpanded ? "收起" : "展开";
      } else {
        expandBtn.style.display = "none";
        if (wasExpanded) {
          hrContentEl.classList.remove("expanded");
        }
      }
    }

    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(() => {
        updateExpandBtnVisibility();
      });
      ro.observe(hrContentEl);
    }
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(updateExpandBtnVisibility);
    } else {
      setTimeout(updateExpandBtnVisibility, 0);
    }

    // 展开/收起 点击
    expandBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isExpanded = hrContentEl.classList.toggle("expanded");
      expandBtn.textContent = isExpanded ? "收起" : "展开";
    });

    let coordinator = createAutoSaveCoordinator({
      delayMs: 1000,
      initialSaved: row.hr_note || null,
      emptyMessage: "清空请到岗位卡片操作",
      save: async (note) => {
        return sendMessageAsync({
          type: "save_hr_note",
          platform_job_id: row.platform_job_id,
          note: note,
          source: "myjobs",
        });
      },
      onStatusChange: ({ status, message }) => {
        if (status === "empty") {
          editError.textContent = message;
        } else if (status === "saving") {
          editError.textContent = "保存中...";
        } else if (status === "saved") {
          editError.textContent = "已自动保存";
        } else if (status === "error") {
          editError.textContent = message || "保存失败";
        }
      },
      onSaveSuccess: (savedNote) => {
        row.hr_note = savedNote;
        row.hr_note_content = savedNote;
        row.hr_note_recorded = true;
        hrContentEl.textContent = savedNote;
        hrContentEl.classList.remove("empty");
        updateExpandBtnVisibility();
      },
    });

    textarea.addEventListener("input", () => {
      if (textarea.value.trim().length > 0 && editError.textContent === "清空请到岗位卡片操作") {
        editError.textContent = "";
      }
      coordinator.onInput(textarea.value);
    });

    textarea.addEventListener("blur", () => {
      coordinator.onBlur(textarea.value);
    });

    // 进入编辑
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const currentSaved = row.hr_note ? row.hr_note.trim() : "";
      coordinator.setLastSaved(currentSaved);
      textarea.value = currentSaved;
      editError.textContent = "";
      doneBtn.disabled = false;
      hrContentEl.style.display = "none";
      expandBtn.style.display = "none";
      editBtn.style.display = "none";
      editArea.style.display = "";
      textarea.focus();
    });

    // 完成编辑（有未保存变动先保存再关闭，输入为空不保存直接关闭并显示最近一次内容）
    doneBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      doneBtn.disabled = true;

      try {
        const res = await coordinator.flush(textarea.value);
        if (res.ok) {
          editArea.style.display = "none";
          hrContentEl.style.display = "";
          editBtn.style.display = "";
          editError.textContent = "";
          doneBtn.disabled = false;
          updateExpandBtnVisibility();
        } else {
          doneBtn.disabled = false;
          editError.textContent = res.message || res.error || "保存失败";
        }
      } catch (err) {
        doneBtn.disabled = false;
        editError.textContent = err?.message || "网络异常";
      }
    });

    // --- 底部行：状态操作按钮与移出标注 (T045) ---
    const footer = document.createElement("div");
    footer.className = "job-footer-row";

    const statusActions = document.createElement("div");
    statusActions.className = "job-status-actions";

    const initialStatus = row.status;
    let currentStatus = row.status;

    const statusBtnConfigs = [
      { key: "saved", label: "收藏" },
      { key: "applied", label: "已投递" },
      { key: "skipped", label: "不考虑" },
      { key: null, label: "取消状态", isCancel: true },
    ];

    const statusBtns = [];
    const statusNoticeEl = document.createElement("span");
    statusNoticeEl.className = "job-status-notice";
    statusNoticeEl.style.display = "none";

    function updateStatusUI() {
      for (const { btn, key, isCancel } of statusBtns) {
        if (isCancel) {
          btn.style.display = currentStatus ? "" : "none";
        } else {
          if (key === currentStatus) {
            btn.classList.add("active");
          } else {
            btn.classList.remove("active");
          }
        }
      }

      const notice = getStatusRemovalNotice(initialStatus, currentStatus, currentFilter);
      if (notice) {
        statusNoticeEl.textContent = notice;
        statusNoticeEl.style.display = "";
      } else {
        statusNoticeEl.textContent = "";
        statusNoticeEl.style.display = "none";
      }
    }

    for (const cfg of statusBtnConfigs) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `status-btn ${cfg.isCancel ? "cancel-btn" : ""}`;
      btn.textContent = cfg.label;

      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (cfg.key === currentStatus) return;

        const oldStatus = currentStatus;
        const oldCounts = { ...currentCounts };
        const newStatus = cfg.key;

        // 行保留在原位不跳动，立即更新按钮与标注
        currentStatus = newStatus;
        updateStatusUI();

        // 标签数字同步更新
        currentCounts = updateCountsOnStatusChange(oldCounts, oldStatus, newStatus);
        updateCounts(currentCounts);

        function handleFailure() {
          const rolledBack = rollbackStatusChange({
            previousStatus: oldStatus,
            previousCounts: oldCounts,
          });
          currentStatus = rolledBack.status;
          updateStatusUI();
          statusNoticeEl.textContent = rolledBack.notice;
          statusNoticeEl.style.display = "";
          currentCounts = rolledBack.counts;
          updateCounts(currentCounts);
        }

        try {
          const res = await sendMessageAsync({
            type: "set_job_status",
            platform_job_id: row.platform_job_id,
            status: newStatus,
          });

          if (!res.ok) {
            handleFailure();
            return;
          }

          row.status = newStatus;
        } catch {
          handleFailure();
        }
      });

      statusBtns.push({ btn, key: cfg.key, isCancel: cfg.isCancel });
      statusActions.appendChild(btn);
    }

    statusActions.appendChild(statusNoticeEl);
    footer.appendChild(statusActions);

    card.appendChild(footer);

    updateStatusUI();

    // 用户显式点击才打开链接，不属于自动化
    card.addEventListener("click", () => {
      if (row.url && isSafeBossJobUrl(row.url)) {
        if (typeof chrome !== "undefined" && chrome.tabs?.create) {
          chrome.tabs.create({ url: row.url });
        }
      }
    });

    card.addEventListener("keydown", (e) => {
      if (e.target !== card) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (row.url && isSafeBossJobUrl(row.url)) {
          if (typeof chrome !== "undefined" && chrome.tabs?.create) {
            chrome.tabs.create({ url: row.url });
          }
        }
      }
    });

    return card;
  }

  const loadJobsTracker = createRequestTracker();

  async function loadJobs(filterToLoad) {
    const requestId = loadJobsTracker.next();
    const hrOnly = Boolean(hrOnlyCheckbox?.checked);
    const q = searchInput?.value.trim() || "";

    try {
      const res = await sendMessageAsync({
        type: "get_my_jobs",
        filter: filterToLoad,
        hr_only: hrOnly,
        q,
      });

      if (!loadJobsTracker.isLatest(requestId)) {
        return;
      }

      if (!res.ok) {
        if (res.viewState === "jet_down" || res.status === 0) {
          showNotice("Jet 未运行，请先启动 Jet 后端服务。");
        } else if (res.viewState === "unpaired" || res.status === 401) {
          showNotice("未配对，请在设置中完成配对。", "warning", "前往设置页", () => {
            if (typeof chrome !== "undefined" && chrome.runtime?.openOptionsPage) {
              chrome.runtime.openOptionsPage();
            } else if (typeof chrome !== "undefined" && chrome.tabs?.create) {
              chrome.tabs.create({ url: "options.html" });
            }
          });
        } else {
          showNotice("加载岗位库失败" + (res.error ? `（${res.error}）` : "") + "。");
        }
        return;
      }

      clearNotice();
      const counts = res.data?.counts || {
        all_jobs: 0,
        all: 0,
        saved: 0,
        applied: 0,
        skipped: 0,
        recent: 0,
      };
      currentCounts = counts;
      updateCounts(counts);

      // total_matches > 200 提示
      const totalMatches = res.data?.total_matches;
      const totalMatchesNoticeText = formatTotalMatchesNotice(totalMatches, 200);
      if (totalMatchesNotice && totalMatchesNoticeText) {
        totalMatchesNotice.textContent = totalMatchesNoticeText;
        totalMatchesNotice.style.display = "";
      } else if (totalMatchesNotice) {
        totalMatchesNotice.textContent = "";
        totalMatchesNotice.style.display = "none";
      }

      // 默认"已标记"，若 counts.all 为 0 则默认"最近看过"
      if (isInitialLoad) {
        isInitialLoad = false;
        if (counts.all === 0) {
          setActiveFilter("recent");
          if (counts.recent > 0) {
            await loadJobs("recent");
            return;
          } else {
            renderList([], "recent");
            return;
          }
        }
      }

      renderList(res.data?.items || [], filterToLoad);
    } catch (err) {
      if (!loadJobsTracker.isLatest(requestId)) {
        return;
      }
      showNotice("加载岗位库异常：" + (err?.message || String(err)));
    }
  }

  // 绑定筛选按钮事件
  if (filtersBar) {
    filtersBar.addEventListener("click", (e) => {
      const btn = e.target.closest(".filter-btn");
      if (!btn) return;
      const targetFilter = btn.getAttribute("data-filter");
      if (!targetFilter || targetFilter === currentFilter) return;

      setActiveFilter(targetFilter);
      loadJobs(targetFilter);
    });
  }

  // 绑定 HR 记录复选框
  if (hrOnlyCheckbox) {
    hrOnlyCheckbox.addEventListener("change", () => {
      loadJobs(currentFilter);
    });
  }

  // 绑定搜索输入框（300ms 防抖）
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        loadJobs(currentFilter);
      }, 300);
    });
  }

  // 初始加载
  loadJobs("all");
});
