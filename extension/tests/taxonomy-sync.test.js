import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import {
  CATEGORIES,
  WORK_INTENSITY,
  VERDICTS,
  VERDICT_LABELS,
  VERDICT_TONES,
  LEGACY_VERDICT_MAP,
  SALES_LEVEL,
  EXPERIENCE_FIT,
} from "../src/taxonomy.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const contentJsPath = path.resolve(__dirname, "../src/content.js");

test("content.js contains taxonomy synchronization notice and all taxonomy strings", () => {
  const content = fs.readFileSync(contentJsPath, "utf-8");

  // 注释注明与 taxonomy.js 保持一致
  assert.ok(
    content.includes("与 extension/src/taxonomy.js 保持一致"),
    "content.js must contain comment indicating sync with taxonomy.js",
  );

  // 1. 所有大类与细分字符串都出现在 content.js 中
  for (const [category, subtypes] of Object.entries(CATEGORIES)) {
    assert.ok(
      content.includes(category),
      `Category "${category}" must appear in content.js`,
    );
    for (const subtype of subtypes) {
      assert.ok(
        content.includes(subtype),
        `Subtype "${subtype}" of "${category}" must appear in content.js`,
      );
    }
  }

  // 2. 工作强度五档
  for (const intensity of WORK_INTENSITY) {
    assert.ok(
      content.includes(intensity),
      `Work intensity "${intensity}" must appear in content.js`,
    );
  }

  // 3. 四档结论键名与中文标签
  for (const verdict of VERDICTS) {
    assert.ok(
      content.includes(verdict),
      `Verdict "${verdict}" must appear in content.js`,
    );
    assert.ok(
      content.includes(VERDICT_LABELS[verdict]),
      `Verdict label "${VERDICT_LABELS[verdict]}" must appear in content.js`,
    );
  }

  // 4. 旧结论映射键名
  for (const [legacyKey, newKey] of Object.entries(LEGACY_VERDICT_MAP)) {
    assert.ok(
      content.includes(legacyKey),
      `Legacy verdict "${legacyKey}" must appear in content.js`,
    );
  }
});

test("content.js taxonomy constant block parses and matches taxonomy.js exactly", () => {
  const content = fs.readFileSync(contentJsPath, "utf-8");

  // 提取 content.js 中的分类常量块
  const match = content.match(
    /(var CATEGORIES =[\s\S]*?var EXPERIENCE_FIT = [^;]+;)/,
  );
  assert.ok(match, "Constants block must exist in content.js");

  const context = {};
  vm.createContext(context);
  vm.runInContext(match[1], context);
  // vm 沙箱里的对象原型与本进程不同，deepEqual 会误判不相等：先转成普通 JSON 再比较
  const sandbox = JSON.parse(
    JSON.stringify({
      CATEGORIES: context.CATEGORIES,
      WORK_INTENSITY: context.WORK_INTENSITY,
      VERDICTS: context.VERDICTS,
      VERDICT_LABELS: context.VERDICT_LABELS,
      VERDICT_TONES: context.VERDICT_TONES,
      LEGACY_VERDICT_MAP: context.LEGACY_VERDICT_MAP,
      SALES_LEVEL: context.SALES_LEVEL,
      EXPERIENCE_FIT: context.EXPERIENCE_FIT,
    }),
  );

  assert.deepEqual(
    sandbox.CATEGORIES,
    CATEGORIES,
    "CATEGORIES in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.WORK_INTENSITY,
    WORK_INTENSITY,
    "WORK_INTENSITY in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.VERDICTS,
    VERDICTS,
    "VERDICTS in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.VERDICT_LABELS,
    VERDICT_LABELS,
    "VERDICT_LABELS in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.VERDICT_TONES,
    VERDICT_TONES,
    "VERDICT_TONES in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.LEGACY_VERDICT_MAP,
    LEGACY_VERDICT_MAP,
    "LEGACY_VERDICT_MAP in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.SALES_LEVEL,
    SALES_LEVEL,
    "SALES_LEVEL in content.js must match taxonomy.js exactly",
  );
  assert.deepEqual(
    sandbox.EXPERIENCE_FIT,
    EXPERIENCE_FIT,
    "EXPERIENCE_FIT in content.js must match taxonomy.js exactly",
  );
});
