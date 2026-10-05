import test from "node:test";
import assert from "node:assert/strict";
import {
  parseAutoGenerateSetting,
  toAutoGenerateStoragePayload,
  allocateNextResumeSlot,
  decideResumeLoadState,
  validateCurrentCity,
  initOptions,
} from "../src/options.js";

test("新增占用最小空闲编号", () => {
  // 1. 初始为空时占用编号 1
  assert.equal(allocateNextResumeSlot([]), 1);
  assert.equal(allocateNextResumeSlot(new Set()), 1);

  // 2. 已有编号 1 时占用编号 2
  assert.equal(allocateNextResumeSlot([1]), 2);

  // 3. 已有编号 2 时占用最小空闲编号 1
  assert.equal(allocateNextResumeSlot([2]), 1);

  // 4. 已有编号 3 时占用最小空闲编号 1
  assert.equal(allocateNextResumeSlot([3]), 1);

  // 5. 已有编号 1 和 2 时占用编号 3
  assert.equal(allocateNextResumeSlot([1, 2]), 3);

  // 6. 已有编号 1 和 3 时（删除了中间编号 2），新添加占用最小空闲编号 2
  assert.equal(allocateNextResumeSlot([1, 3]), 2);

  // 7. 已有编号 2 和 3 时占用最小空闲编号 1
  assert.equal(allocateNextResumeSlot([2, 3]), 1);

  // 8. 满 3 份（1, 2, 3）时返回 null（不能再添加）
  assert.equal(allocateNextResumeSlot([1, 2, 3]), null);
  assert.equal(allocateNextResumeSlot([3, 1, 2]), null);

  // 9. 容错字符串数字与异常输入
  assert.equal(allocateNextResumeSlot(["1", "3"]), 2);
  assert.equal(allocateNextResumeSlot(null), 1);
  assert.equal(allocateNextResumeSlot(undefined), 1);
});

test("删除中间一份后其余编号不变", () => {
  // 保持其余编号不变时（如剩余编号 1 和 3），新添加占用最小空闲编号 2
  const existingSlots = [1, 3];
  const nextSlot = allocateNextResumeSlot(existingSlots);
  assert.equal(nextSlot, 2);
});

test("parseAutoGenerateSetting and toAutoGenerateStoragePayload", () => {
  assert.equal(parseAutoGenerateSetting({ autoGenerate: true }), true);
  assert.equal(parseAutoGenerateSetting({ autoGenerate: false }), false);
  assert.equal(parseAutoGenerateSetting({}), true);
  assert.equal(parseAutoGenerateSetting(null), true);

  assert.deepEqual(toAutoGenerateStoragePayload(true), { autoGenerate: true });
  assert.deepEqual(toAutoGenerateStoragePayload(false), { autoGenerate: false });
});

test("decideResumeLoadState: 加载结果决策是否允许保存与提示信息", () => {
  // 1. 无响应或网络不可达
  assert.deepEqual(decideResumeLoadState(null), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState(undefined), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });

  // 2. 未配对
  assert.deepEqual(decideResumeLoadState({ viewState: "unpaired" }), {
    ok: false,
    canSave: false,
    message: "未配对",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState({ status: 401 }), {
    ok: false,
    canSave: false,
    message: "未配对",
    msgType: "error",
  });

  // 3. Jet 服务不可用 / 未运行
  assert.deepEqual(decideResumeLoadState({ viewState: "jet_down" }), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState({ status: 0 }), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });

  // 4. 任意 !res.ok 的失败情况（带 error 或不带 error）
  assert.deepEqual(decideResumeLoadState({ ok: false, error: "database_error" }), {
    ok: false,
    canSave: false,
    message: "加载失败（database_error）",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState({ ok: false, status: 500 }), {
    ok: false,
    canSave: false,
    message: "加载失败（未知错误）",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState({ ok: false }), {
    ok: false,
    canSave: false,
    message: "加载失败（未知错误）",
    msgType: "error",
  });

  // 5. res.ok 为 true 但 data 缺失或非数组（异常数据防覆盖）
  assert.deepEqual(decideResumeLoadState({ ok: true, data: null }), {
    ok: false,
    canSave: false,
    message: "加载失败（未知错误）",
    msgType: "error",
  });
  assert.deepEqual(decideResumeLoadState({ ok: true }), {
    ok: false,
    canSave: false,
    message: "加载失败（未知错误）",
    msgType: "error",
  });

  // 6. 成功加载（空列表与非空列表均允许保存且无错误提示）
  assert.deepEqual(decideResumeLoadState({ ok: true, data: [] }), {
    ok: true,
    canSave: true,
    message: "",
    msgType: "",
  });
  assert.deepEqual(
    decideResumeLoadState({
      ok: true,
      data: [{ slot: 1, name: "后端开发", job_types: "Go / Python" }],
    }),
    {
      ok: true,
      canSave: true,
      message: "",
      msgType: "",
    }
  );
});

test("options: current_city 读写与字数校验", () => {
  // 1. 校验字数限制：<= 20 字有效
  assert.deepEqual(validateCurrentCity("成都"), { valid: true, value: "成都" });
  assert.deepEqual(validateCurrentCity(""), { valid: true, value: "" });
  assert.deepEqual(validateCurrentCity(null), { valid: true, value: "" });
  assert.deepEqual(validateCurrentCity("  重庆  "), { valid: true, value: "重庆" });
  assert.deepEqual(validateCurrentCity("成".repeat(20)), { valid: true, value: "成".repeat(20) });

  // 2. 超过 20 字无效并返回错误提示
  assert.deepEqual(validateCurrentCity("成".repeat(21)), {
    valid: false,
    error: "目前所在城市不能超过 20 字",
  });

  // 3. 页面真实读写路径验证：
  // 加载时把 current_city 填入 #profile-current-city，保存时 payload 含去空白后的 current_city
  const origDocument = globalThis.document;
  const origChrome = globalThis.chrome;

  const elements = new Map();
  class MockElement {
    constructor(id) {
      this.id = id;
      this.value = "";
      this.textContent = "";
      this.className = "";
      this.style = {};
      this.disabled = false;
      this.checked = false;
      this.listeners = {};
      this.children = [];
    }
    addEventListener(event, fn) {
      this.listeners[event] = fn;
    }
    click() {
      if (this.listeners["click"]) {
        this.listeners["click"]({ type: "click" });
      }
    }
    querySelector() {
      return null;
    }
    querySelectorAll() {
      return [];
    }
    appendChild(child) {
      this.children.push(child);
    }
    remove() {}
  }

  function getEl(id) {
    if (!elements.has(id)) {
      elements.set(id, new MockElement(id));
    }
    return elements.get(id);
  }

  globalThis.document = {
    getElementById: (id) => getEl(id),
    addEventListener: () => {},
  };

  let lastSavedProfile = null;
  globalThis.chrome = {
    runtime: {
      sendMessage(msg, cb) {
        if (msg.type === "get_profile") {
          if (cb) {
            cb({
              ok: true,
              data: {
                directions: ["后端开发"],
                keywords: ["Python"],
                current_city: "成都",
              },
            });
          }
        } else if (msg.type === "save_profile") {
          lastSavedProfile = msg.profile;
          if (cb) cb({ ok: true });
        } else {
          if (cb) cb({ ok: true });
        }
      },
    },
    storage: {
      local: {
        get(keys, cb) {
          if (cb) cb({});
        },
        set(data, cb) {
          if (cb) cb();
        },
      },
    },
  };

  try {
    initOptions();

    // 验证加载路径：加载时把 current_city 填入 #profile-current-city
    const currentCityInput = getEl("profile-current-city");
    assert.equal(currentCityInput.value, "成都");

    // 验证保存路径：保存时 payload 含去空白后的 current_city
    currentCityInput.value = "   重庆   ";
    getEl("profile-directions").value = "后端开发";
    getEl("profile-save-btn").click();

    assert.ok(lastSavedProfile !== null);
    assert.equal(lastSavedProfile.current_city, "重庆");

    // 验证保存路径校验：超过 20 字由 validateCurrentCity 拦截并给出错误提示，不发出保存请求
    lastSavedProfile = null;
    currentCityInput.value = "成".repeat(21);
    getEl("profile-save-btn").click();

    assert.equal(lastSavedProfile, null);
    assert.equal(getEl("profile-msg").textContent, "目前所在城市不能超过 20 字");
  } finally {
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
});
