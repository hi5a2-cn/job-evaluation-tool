import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  decideStrictIndustriesLoadState,
  buildStrictIndustriesSavePayload,
} from "../src/options.js";
import { createJetClient } from "../src/jet-client.js";

const jetClient = createJetClient({ getPort: () => 8765 });

test("decideStrictIndustriesLoadState: 处理各种网络与接口状态", () => {
  // 1. res 为空 / undefined（Jet 未运行）
  assert.deepEqual(decideStrictIndustriesLoadState(null), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });
  assert.deepEqual(decideStrictIndustriesLoadState(undefined), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });

  // 2. 未配对（401 / viewState="unpaired"）
  assert.deepEqual(decideStrictIndustriesLoadState({ status: 401, error: "unpaired" }), {
    ok: false,
    canSave: false,
    message: "未配对",
    msgType: "error",
  });
  assert.deepEqual(decideStrictIndustriesLoadState({ viewState: "unpaired" }), {
    ok: false,
    canSave: false,
    message: "未配对",
    msgType: "error",
  });

  // 3. 服务未启动（status 0 / viewState="jet_down"）
  assert.deepEqual(decideStrictIndustriesLoadState({ status: 0 }), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });
  assert.deepEqual(decideStrictIndustriesLoadState({ viewState: "jet_down" }), {
    ok: false,
    canSave: false,
    message: "Jet 未运行",
    msgType: "error",
  });

  // 4. 接口报错
  assert.deepEqual(decideStrictIndustriesLoadState({ ok: false, error: "database_error" }), {
    ok: false,
    canSave: false,
    message: "加载失败（database_error）",
    msgType: "error",
  });

  // 5. 数据格式异常（available 缺失或非数组）
  assert.deepEqual(decideStrictIndustriesLoadState({ ok: true, data: {} }), {
    ok: false,
    canSave: false,
    message: "加载失败（未知错误）",
    msgType: "error",
  });

  // 6. 成功加载
  assert.deepEqual(
    decideStrictIndustriesLoadState({
      ok: true,
      data: {
        available: ["餐饮", "保险", "汽车", "房地产", "美妆", "快消"],
        selected: ["餐饮", "汽车"],
      },
    }),
    {
      ok: true,
      canSave: true,
      message: "",
      msgType: "",
    }
  );
});

test("buildStrictIndustriesSavePayload: 格式化、去重与非法值过滤", () => {
  // 1. 去除首尾空格并去重
  assert.deepEqual(
    buildStrictIndustriesSavePayload(["  餐饮  ", "汽车", "餐饮", "汽车  "]),
    { selected: ["餐饮", "汽车"] }
  );

  // 2. 过滤空字符串与纯空白
  assert.deepEqual(
    buildStrictIndustriesSavePayload(["餐饮", "", "   ", "美妆"]),
    { selected: ["餐饮", "美妆"] }
  );

  // 3. 非数组输入容错
  assert.deepEqual(buildStrictIndustriesSavePayload(null), { selected: [] });
  assert.deepEqual(buildStrictIndustriesSavePayload(undefined), { selected: [] });
  assert.deepEqual(buildStrictIndustriesSavePayload("餐饮"), { selected: [] });
});

test("jetClient: getStrictIndustries 与 putStrictIndustries 协议规范", async () => {
  const origFetch = globalThis.fetch;
  const calls = [];

  const mockFetch = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/v1/strict-industries") && options.method === "GET") {
      return {
        status: 200,
        ok: true,
        json: async () => ({
          available: ["餐饮", "汽车"],
          selected: ["餐饮"],
        }),
      };
    }
    if (url.endsWith("/v1/strict-industries") && options.method === "PUT") {
      return {
        status: 200,
        ok: true,
        json: async () => ({ ok: true, count: 1 }),
      };
    }
    return { status: 404, ok: false };
  };

  const client = createJetClient({ getPort: () => 8765, fetchImpl: mockFetch });

  try {
    const getRes = await client.getStrictIndustries();
    assert.equal(getRes.ok, true);
    assert.deepEqual(getRes.data.available, ["餐饮", "汽车"]);
    assert.deepEqual(getRes.data.selected, ["餐饮"]);
    assert.equal(calls[0].url, "http://127.0.0.1:8765/v1/strict-industries");
    assert.equal(calls[0].options.method, "GET");

    const putRes = await client.putStrictIndustries(["餐饮"]);
    assert.equal(putRes.ok, true);
    assert.equal(calls[1].url, "http://127.0.0.1:8765/v1/strict-industries");
    assert.equal(calls[1].options.method, "PUT");
    assert.deepEqual(JSON.parse(calls[1].options.body), { selected: ["餐饮"] });
  } finally {
    globalThis.fetch = origFetch;
  }
});

test("options.html: 存在从严行业设置卡片及相同发送说明", () => {
  const htmlPath = new URL("../src/options.html", import.meta.url);
  const htmlSrc = fs.readFileSync(htmlPath, "utf8");

  // 1. 存在卡片容器
  assert.ok(htmlSrc.includes('id="strict-industry-section"'));
  assert.ok(htmlSrc.includes('id="strict-industry-list"'));
  assert.ok(htmlSrc.includes('id="strict-industry-save-btn"'));
  assert.ok(htmlSrc.includes('id="strict-industry-msg"'));

  // 2. 存在与"我的简历"相同的发送说明文案 (009-resume-pdf / T019)
  const expectedDisclaimer =
    "上传时，简历文字会在本机去掉姓名、手机号、邮箱等联系方式后发送给 DeepSeek 生成简历画像；之后岗位判断时只发送画像（编号为简历1/2/3，不发送简历名称）。本机只保存提取出的文字和画像，不保存 PDF 文件。";
  assert.ok(
    htmlSrc.includes(expectedDisclaimer),
    "options.html 从严行业卡片应包含与我的简历一致的发送说明"
  );
});
