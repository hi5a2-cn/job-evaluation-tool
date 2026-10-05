// 读取逻辑源自 experiments/boss-probe-ext/reader.js（实验插件 v0.0.2），按 contracts/page-reader.md 改写

/**
 * 完全自包含的页面读取函数。
 * 只能在页面的 MAIN world 中执行，通过读取 Vue 实例只读地获取当前页面已加载的岗位数据。
 * 不写任何属性、不留全局变量、不注册事件、不发网络请求、不点击/滚动/导航。
 */
export function readBossPage() {
  try {
    var problems = [];

    var path = "";
    if (typeof location !== "undefined" && location && location.pathname) {
      path = String(location.pathname);
    }

    var isBlank = Boolean(
      typeof document !== "undefined" &&
      document &&
      document.body &&
      document.body.children &&
      document.body.children.length === 0
    );

    var isVerify = path.indexOf("verify") !== -1 || path.indexOf("security") !== -1;

    var page_kind = "other";
    if (isVerify || isBlank) {
      page_kind = "captcha_or_blank";
    } else if (path.startsWith("/job_detail/")) {
      page_kind = "job_detail_page";
    } else if (path.startsWith("/web/geek/job")) {
      page_kind = "search_list";
    } else if (path.startsWith("/web/geek/chat")) {
      page_kind = "chat_page";
    } else {
      page_kind = "other";
    }

    // 独立职位页被动读取（004 US2）：在页面主环境只读 DOM，不碰 Vue 实例，不导航、不点击、不改页面
    if (page_kind === "job_detail_page") {
      var jobIdMatch = path.match(/\/job_detail\/([^\/\.]+)(?:\.html)?/);
      var platformJobId = jobIdMatch && jobIdMatch[1] ? jobIdMatch[1].trim() : null;
      if (!platformJobId) {
        problems.push("未从地址识别到岗位 ID");
      }

      // 优先读 innerText（保留 <br> 换行为 \n），否则再用 textContent
      function getElementText(el) {
        if (!el) return "";
        if (typeof el.innerText === "string") {
          return el.innerText;
        }
        if (el.textContent != null) {
          return String(el.textContent);
        }
        return "";
      }

      var title = null;
      if (typeof document !== "undefined" && document && document.querySelector) {
        var titleEl = document.querySelector(".job-banner .info-primary .name h1");
        if (titleEl) {
          var t = getElementText(titleEl).trim();
          if (t.length > 0) {
            title = t;
          }
        }
      }
      if (!title) {
        problems.push("缺少职位名");
      }

      var salaryRaw = null;
      if (typeof document !== "undefined" && document && document.querySelector) {
        var salaryEl = document.querySelector(".job-banner .info-primary .name span.salary");
        if (salaryEl) {
          var s = getElementText(salaryEl).trim();
          if (s.length > 0) {
            salaryRaw = s;
          }
        }
      }

      var city = "";
      if (typeof document !== "undefined" && document && document.querySelector) {
        var cityEl = document.querySelector(".job-banner .info-primary a.text-desc.text-city");
        if (cityEl) {
          city = getElementText(cityEl).trim();
        }
      }

      var description = null;
      if (typeof document !== "undefined" && document && document.querySelectorAll) {
        var secEls = document.querySelectorAll(".job-detail-section > .job-sec-text");
        for (var sIdx = 0; sIdx < secEls.length; sIdx++) {
          var el = secEls[sIdx];
          var inSalary = false;
          var inCompany = false;
          if (typeof el.closest === "function") {
            inSalary = Boolean(el.closest(".salary-info"));
            inCompany = Boolean(el.closest(".job-detail-company"));
          } else {
            var p = el.parentElement;
            while (p) {
              var cls = p.className || "";
              if (typeof cls === "string") {
                if (cls.indexOf("salary-info") !== -1) inSalary = true;
                if (cls.indexOf("job-detail-company") !== -1) inCompany = true;
              }
              p = p.parentElement;
            }
          }
          if (!inSalary && !inCompany) {
            var dText = getElementText(el).trim();
            if (dText.length > 0) {
              description = dText;
              break;
            }
          }
        }
      }
      if (!description) {
        problems.push("缺少职位描述");
      }

      var companyLegalName = null;
      if (typeof document !== "undefined" && document && document.querySelector) {
        var compEl = document.querySelector(".job-detail-company .business-info-box li.company-name");
        if (compEl) {
          var rawCompName = getElementText(compEl).trim();
          var cleanedCompName = rawCompName.replace(/^(?:公司名称|企业名称)[:：\s]*/, "").trim();
          if (cleanedCompName.length > 0) {
            companyLegalName = cleanedCompName;
          }
        }
      }

      var isDetailOk = Boolean(platformJobId && title && description);
      return {
        reader_version: 1,
        page_kind: "job_detail_page",
        list: {
          ok: false,
          has_more: null,
          jobs: [],
        },
        detail: {
          ok: isDetailOk,
          job: isDetailOk
            ? {
                platform_job_id: platformJobId,
                title: title,
                salary_raw: salaryRaw,
                city: city,
                district: null,
                description: description,
                company_legal_name: companyLegalName,
              }
            : null,
        },
        problems: problems,
      };
    }

    // search_list 以外的其他页面（验证码、空白、聊天页、未知页）直接返回空结果
    if (page_kind !== "search_list") {
      return {
        reader_version: 1,
        page_kind: page_kind,
        list: {
          ok: false,
          has_more: null,
          jobs: [],
        },
        detail: {
          ok: false,
          job: null,
        },
        problems: problems,
      };
    }

    // 寻找 Vue 实例
    function findVue2Instance() {
      var selectors = [
        "#wrap .page-job-wrapper",
        ".job-recommend-main",
        ".page-jobs-main",
        "#wrap",
      ];
      for (var i = 0; i < selectors.length; i++) {
        var el = document.querySelector(selectors[i]);
        if (el && el.__vue__) {
          return el.__vue__;
        }
      }
      return null;
    }

    var vueInst = findVue2Instance();
    if (!vueInst) {
      problems.push("未找到 Vue 实例");
      return {
        reader_version: 1,
        page_kind: "search_list",
        list: {
          ok: false,
          has_more: null,
          jobs: [],
        },
        detail: {
          ok: false,
          job: null,
        },
        problems: problems,
      };
    }

    // has_more 状态
    var has_more = null;
    if ("hasMore" in vueInst) {
      has_more = Boolean(vueInst.hasMore);
    }

    // 提取列表数据
    var listResult = {
      ok: false,
      has_more: has_more,
      jobs: [],
    };

    var rawList = vueInst.jobList;
    if (!Array.isArray(rawList)) {
      problems.push("jobList 不是数组");
    } else {
      var listItems = rawList;
      if (rawList.length > 200) {
        problems.push("岗位超过 200 条，已截断");
        listItems = rawList.slice(0, 200);
      }

      function cleanStringArray(arr) {
        if (!Array.isArray(arr)) {
          return [];
        }
        var res = [];
        for (var i = 0; i < arr.length; i++) {
          if (typeof arr[i] === "string") {
            var trimmed = arr[i].trim();
            if (trimmed.length > 0) {
              res.push(trimmed);
            }
          }
        }
        return res;
      }

      var listValid = true;
      var mappedJobs = [];
      for (var j = 0; j < listItems.length; j++) {
        var item = listItems[j];
        if (
          !item ||
          item.encryptJobId == null ||
          String(item.encryptJobId).trim() === "" ||
          item.jobName == null ||
          String(item.jobName).trim() === ""
        ) {
          listValid = false;
          break;
        }

        var salaryRaw =
          item.salaryDesc != null && String(item.salaryDesc).trim() !== ""
            ? String(item.salaryDesc)
            : null;

        var districtRaw =
          item.areaDistrict != null && String(item.areaDistrict).trim() !== ""
            ? String(item.areaDistrict)
            : null;

        var companyName =
          item.brandName != null && String(item.brandName).trim() !== ""
            ? String(item.brandName).trim()
            : null;

        var companyIndustry =
          item.brandIndustry != null && String(item.brandIndustry).trim() !== ""
            ? String(item.brandIndustry).trim()
            : null;

        var experience =
          item.jobExperience != null && String(item.jobExperience).trim() !== ""
            ? String(item.jobExperience).trim()
            : null;

        var degree =
          item.jobDegree != null && String(item.jobDegree).trim() !== ""
            ? String(item.jobDegree).trim()
            : null;

        mappedJobs.push({
          platform_job_id: String(item.encryptJobId),
          title: String(item.jobName),
          company_name: companyName,
          company_industry: companyIndustry,
          salary_raw: salaryRaw,
          city: item.cityName != null ? String(item.cityName) : "",
          district: districtRaw,
          experience: experience,
          degree: degree,
          job_labels: cleanStringArray(item.jobLabels),
          skills: cleanStringArray(item.skills),
        });
      }

      if (!listValid) {
        problems.push("岗位缺少 encryptJobId 或 jobName");
      } else {
        listResult = {
          ok: true,
          has_more: has_more,
          jobs: mappedJobs,
        };
      }
    }

    // 提取详情数据
    var detailResult = {
      ok: false,
      job: null,
    };

    var jobInfo = null;
    if (vueInst.jobDetail && vueInst.jobDetail.jobInfo) {
      jobInfo = vueInst.jobDetail.jobInfo;
    } else if (vueInst.jobInfo) {
      jobInfo = vueInst.jobInfo;
    }

    if (!jobInfo) {
      // 列表页未点开岗位时很正常，不写 problems
      detailResult = {
        ok: false,
        job: null,
      };
    } else {
      if (
        jobInfo.encryptId == null ||
        String(jobInfo.encryptId).trim() === "" ||
        jobInfo.jobName == null ||
        String(jobInfo.jobName).trim() === "" ||
        jobInfo.postDescription == null ||
        typeof jobInfo.postDescription !== "string"
      ) {
        problems.push("详情缺少必要字段(encryptId/jobName/postDescription)");
        detailResult = {
          ok: false,
          job: null,
        };
      } else {
        var detailSalary =
          jobInfo.salaryDesc != null && String(jobInfo.salaryDesc).trim() !== ""
            ? String(jobInfo.salaryDesc)
            : null;

        var detailCompanyName = null;
        var detailCompanyIndustry = null;
        if (Array.isArray(vueInst.jobList)) {
          for (var k = 0; k < vueInst.jobList.length; k++) {
            var listItem = vueInst.jobList[k];
            if (
              listItem &&
              String(listItem.encryptJobId) === String(jobInfo.encryptId)
            ) {
              if (
                listItem.brandName != null &&
                String(listItem.brandName).trim() !== ""
              ) {
                detailCompanyName = String(listItem.brandName).trim();
              }
              if (
                listItem.brandIndustry != null &&
                String(listItem.brandIndustry).trim() !== ""
              ) {
                detailCompanyIndustry = String(listItem.brandIndustry).trim();
              }
              break;
            }
          }
        }
        if (detailCompanyName == null) {
          if (
            vueInst.jobDetail &&
            vueInst.jobDetail.brandComInfo &&
            vueInst.jobDetail.brandComInfo.brandName != null &&
            String(vueInst.jobDetail.brandComInfo.brandName).trim() !== ""
          ) {
            detailCompanyName = String(vueInst.jobDetail.brandComInfo.brandName).trim();
          }
        }

        var detailExperience =
          jobInfo.experienceName != null && String(jobInfo.experienceName).trim() !== ""
            ? String(jobInfo.experienceName).trim()
            : null;
        var detailDegree =
          jobInfo.degreeName != null && String(jobInfo.degreeName).trim() !== ""
            ? String(jobInfo.degreeName).trim()
            : null;

        detailResult = {
          ok: true,
          job: {
            platform_job_id: String(jobInfo.encryptId),
            title: String(jobInfo.jobName),
            company_name: detailCompanyName,
            company_industry: detailCompanyIndustry,
            salary_raw: detailSalary,
            experience: detailExperience,
            degree: detailDegree,
            city: jobInfo.locationName != null ? String(jobInfo.locationName) : "",
            district: null,
            description: String(jobInfo.postDescription),
          },
        };
      }
    }

    return {
      reader_version: 1,
      page_kind: page_kind,
      list: listResult,
      detail: detailResult,
      problems: problems,
    };
  } catch (err) {
    return {
      reader_version: 1,
      page_kind: "search_list",
      list: {
        ok: false,
        has_more: null,
        jobs: [],
      },
      detail: {
        ok: false,
        job: null,
      },
      problems: ["读取异常: " + (err && err.message ? err.message : String(err))],
    };
  }
}

/**
 * 自包含的聊天页深度读取函数。
 * 只能在页面的 MAIN world 中执行，通过读取 Vue 2 实例只读地获取当前活跃会话的岗位、HR 及已加载聊天消息。
 * 不写任何属性、不改 DOM、不留全局变量、不发网络请求、不向上滚动加载。
 */
export function readBossChatPage() {
  try {
    var problems = [];

    var path = "";
    if (typeof location !== "undefined" && location && location.pathname) {
      path = String(location.pathname);
    }

    if (!path.startsWith("/web/geek/chat")) {
      return {
        ok: false,
        reader_version: 1,
        page_kind: "other",
        encrypt_job_id: null,
        job_title: null,
        company_name: null,
        location_name: null,
        hr_name: null,
        user_name: null,
        messages: [],
        problems: ["当前页面不是 BOSS 聊天页 (/web/geek/chat)"],
      };
    }

    function isVisible(el) {
      if (!el) return false;
      try {
        if (typeof getComputedStyle === "function") {
          var cs = getComputedStyle(el);
          if (cs && (cs.display === "none" || cs.visibility === "hidden")) return false;
        }
        if (el.style && (el.style.display === "none" || el.style.visibility === "hidden")) return false;
        if (typeof el.getClientRects === "function") {
          var rects = el.getClientRects();
          if (rects && rects.length > 0) return true;
          if (rects && rects.length === 0) return false;
        }
      } catch (e) {}
      return true;
    }

    function vueUp(el, name) {
      var node = el;
      while (node && !node.__vue__) node = node.parentElement;
      var comp = node && node.__vue__;
      for (var u = 0; comp && u < 10; u++) {
        var nm = comp.$options && (comp.$options.name || comp.$options._componentTag);
        if (nm === name) return comp;
        comp = comp.$parent;
      }
      return null;
    }

    function findRootUserName(comp) {
      try {
        if (comp) {
          if (comp.$store && comp.$store.state && comp.$store.state.userInfo && comp.$store.state.userInfo.name) {
            var n1 = String(comp.$store.state.userInfo.name).trim();
            if (n1) return n1;
          }
          if (comp.$root && comp.$root.$store && comp.$root.$store.state && comp.$root.$store.state.userInfo && comp.$root.$store.state.userInfo.name) {
            var n2 = String(comp.$root.$store.state.userInfo.name).trim();
            if (n2) return n2;
          }
        }
        if (typeof document !== "undefined" && document) {
          var selectors = ["#app", "#wrap", ".page-chat-wrapper", ".chat-wrap", "body"];
          for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            var vm = el && el.__vue__;
            if (vm) {
              var store = vm.$store || (vm.$root && vm.$root.$store);
              if (store && store.state && store.state.userInfo && store.state.userInfo.name) {
                var n3 = String(store.state.userInfo.name).trim();
                if (n3) return n3;
              }
            }
          }
        }
      } catch (e) {}
      return null;
    }

    var msgEls = [];
    if (typeof document !== "undefined" && document && document.querySelectorAll) {
      msgEls = document.querySelectorAll(".chat-message .message-item, li.message-item");
    }

    var activeMsgEls = [];
    for (var i = 0; i < msgEls.length; i++) {
      if (isVisible(msgEls[i])) {
        activeMsgEls.push(msgEls[i]);
      }
    }

    var activeListComp = null;
    for (var j = 0; j < activeMsgEls.length; j++) {
      var ml = vueUp(activeMsgEls[j], "message-list");
      if (ml && ml.$data && ml.$data.boss) {
        activeListComp = ml;
        break;
      }
    }

    if (!activeListComp && typeof document !== "undefined" && document) {
      var containers = document.querySelectorAll(".chat-message, .message-list, .chat-conversation, .chat-content");
      for (var c = 0; c < containers.length; c++) {
        if (isVisible(containers[c])) {
          var comp = vueUp(containers[c], "message-list");
          if (comp && comp.$data && comp.$data.boss) {
            activeListComp = comp;
            break;
          }
        }
      }
    }

    if (!activeListComp || !activeListComp.$data || !activeListComp.$data.boss) {
      return {
        ok: false,
        reader_version: 1,
        page_kind: "chat_page",
        encrypt_job_id: null,
        job_title: null,
        company_name: null,
        location_name: null,
        hr_name: null,
        user_name: null,
        messages: [],
        problems: ["未找到活跃聊天会话数据 (message-list.boss)"],
      };
    }

    var boss = activeListComp.$data.boss;
    var encryptJobId =
      boss.encryptJobId != null && String(boss.encryptJobId).trim() !== ""
        ? String(boss.encryptJobId).trim()
        : null;

    if (!encryptJobId) {
      return {
        ok: false,
        reader_version: 1,
        page_kind: "chat_page",
        encrypt_job_id: null,
        job_title: null,
        company_name: null,
        location_name: null,
        hr_name: null,
        user_name: null,
        messages: [],
        problems: ["当前会话缺少岗位 ID (encryptJobId)"],
      };
    }

    // 页面顶部完整职位名（.position-name 优先，读不到时用 boss.jobName 备用）
    var posEl = null;
    if (typeof document !== "undefined" && document) {
      posEl =
        document.querySelector(".position-name") ||
        document.querySelector(".chat-position-content .position-name") ||
        document.querySelector(".chat-position-content");
    }
    var topJobTitle = posEl && posEl.textContent ? posEl.textContent.trim() : "";
    var jobTitle = topJobTitle || (boss.jobName != null ? String(boss.jobName).trim() : "");

    var companyName = boss.brandName != null ? String(boss.brandName).trim() : "";
    var locationName = boss.locationName != null ? String(boss.locationName).trim() : "";
    var hrName = boss.name != null ? String(boss.name).trim() : "";

    // 我自己的姓名（根 Vue 实例的 $store.state.userInfo.name，读不到时为 null）
    var userName = findRootUserName(activeListComp);

    // 提取所有已加载可见消息（按页面顺序，插件不截取，交给服务端统一过滤与截取）
    var messages = [];
    for (var mIdx = 0; mIdx < activeMsgEls.length; mIdx++) {
      var el = activeMsgEls[mIdx];
      var isMyself = el.className ? /(^|\s)item-myself(\s|$)/.test(el.className) : false;
      var isSystem = el.className ? /(^|\s)item-system(\s|$)/.test(el.className) : false;

      var cm = vueUp(el.firstElementChild || el, "ChatMessage") || vueUp(el, "ChatMessage");
      var m = cm && cm.$props && cm.$props.message ? cm.$props.message : null;

      var msgIsSelf = isMyself;
      var msgType = 1;
      var msgBodyType = 1;
      var msgText = el.textContent ? el.textContent.trim() : "";
      var msgTime = null;
      var msgMid = null;
      var msgQuoteId = null;

      if (m) {
        if ("isSelf" in m) {
          msgIsSelf = Boolean(m.isSelf);
        }
        if (typeof m.type === "number") {
          msgType = m.type;
        }
        if (typeof m.bodyType === "number") {
          msgBodyType = m.bodyType;
        }
        if (typeof m.text === "string") {
          msgText = m.text;
        }
        if (typeof m.time === "number") {
          msgTime = m.time;
        }
        if (typeof m.mid === "number") {
          msgMid = m.mid;
        }
        if (typeof m.quoteId === "number") {
          msgQuoteId = m.quoteId;
        }
        if ("isSystem" in m && m.isSystem) {
          isSystem = true;
        }
      }

      var sender = msgIsSelf ? "我" : isSystem ? "系统" : "HR";

      messages.push({
        sender: sender,
        is_self: msgIsSelf,
        type: msgType,
        body_type: msgBodyType,
        is_system: isSystem,
        text: msgText,
        time: msgTime,
        mid: msgMid,
        quote_id: msgQuoteId,
      });
    }

    return {
      ok: true,
      reader_version: 1,
      page_kind: "chat_page",
      encrypt_job_id: encryptJobId,
      job_title: jobTitle,
      company_name: companyName,
      location_name: locationName,
      hr_name: hrName,
      user_name: userName,
      messages: messages,
      problems: [],
    };
  } catch (err) {
    return {
      ok: false,
      reader_version: 1,
      page_kind: "chat_page",
      encrypt_job_id: null,
      job_title: null,
      company_name: null,
      location_name: null,
      hr_name: null,
      user_name: null,
      messages: [],
      problems: ["读取异常: " + (err && err.message ? err.message : String(err))],
    };
  }
}

/**
 * 轻量自包含函数：仅读取当前活跃聊天的岗位 ID。
 * 供切换发现（T022）与复制防错核对（T025）使用。
 * 不读消息区、不改 DOM、不发网络请求。
 */
export function readBossChatJobId() {
  try {
    var path = "";
    if (typeof location !== "undefined" && location && location.pathname) {
      path = String(location.pathname);
    }
    if (!path.startsWith("/web/geek/chat")) {
      return {
        ok: false,
        encrypt_job_id: null,
        job_title: null,
        company_name: null,
        problems: ["当前页面不是 BOSS 聊天页 (/web/geek/chat)"],
      };
    }

    function isVisible(el) {
      if (!el) return false;
      try {
        if (typeof getComputedStyle === "function") {
          var cs = getComputedStyle(el);
          if (cs && (cs.display === "none" || cs.visibility === "hidden")) return false;
        }
        if (el.style && (el.style.display === "none" || el.style.visibility === "hidden")) return false;
        if (typeof el.getClientRects === "function") {
          var rects = el.getClientRects();
          if (rects && rects.length > 0) return true;
          if (rects && rects.length === 0) return false;
        }
      } catch (e) {}
      return true;
    }

    function vueUp(el, name) {
      var node = el;
      while (node && !node.__vue__) node = node.parentElement;
      var comp = node && node.__vue__;
      for (var u = 0; comp && u < 10; u++) {
        var nm = comp.$options && (comp.$options.name || comp.$options._componentTag);
        if (nm === name) return comp;
        comp = comp.$parent;
      }
      return null;
    }

    var activeListComp = null;
    if (typeof document !== "undefined" && document) {
      var msgEls = document.querySelectorAll(".chat-message .message-item, li.message-item");
      for (var i = 0; i < msgEls.length; i++) {
        if (isVisible(msgEls[i])) {
          var ml = vueUp(msgEls[i], "message-list");
          if (ml && ml.$data && ml.$data.boss && ml.$data.boss.encryptJobId) {
            activeListComp = ml;
            break;
          }
        }
      }

      if (!activeListComp) {
        var containers = document.querySelectorAll(".chat-message, .message-list, .chat-conversation, .chat-content");
        for (var c = 0; c < containers.length; c++) {
          if (isVisible(containers[c])) {
            var comp = vueUp(containers[c], "message-list");
            if (comp && comp.$data && comp.$data.boss && comp.$data.boss.encryptJobId) {
              activeListComp = comp;
              break;
            }
          }
        }
      }
    }

    if (!activeListComp || !activeListComp.$data || !activeListComp.$data.boss || !activeListComp.$data.boss.encryptJobId) {
      return {
        ok: false,
        encrypt_job_id: null,
        job_title: null,
        company_name: null,
        problems: ["未找到活跃聊天会话数据 (message-list.boss.encryptJobId)"],
      };
    }

    var boss = activeListComp.$data.boss;
    var encryptJobId = String(boss.encryptJobId).trim();

    // 职位名优先与 encryptJobId 取自同一 boss 对象，为空时回退为顶部栏文字
    var bossJobTitle = boss.jobName != null ? String(boss.jobName).trim() : "";
    var jobTitle = bossJobTitle;
    if (!jobTitle && typeof document !== "undefined" && document && document.querySelector) {
      var posEl = document.querySelector(".chat-position-content .position-name");
      if (posEl && posEl.textContent) {
        jobTitle = posEl.textContent.trim();
      }
    }
    var companyName = boss.brandName != null ? String(boss.brandName).trim() : "";

    return {
      ok: true,
      encrypt_job_id: encryptJobId,
      job_title: jobTitle || null,
      company_name: companyName || null,
      problems: [],
    };
  } catch (err) {
    return {
      ok: false,
      encrypt_job_id: null,
      job_title: null,
      company_name: null,
      problems: ["读取异常: " + (err && err.message ? err.message : String(err))],
    };
  }
}
