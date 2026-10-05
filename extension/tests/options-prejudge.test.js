import test from "node:test";
import assert from "node:assert/strict";
import {
  formatPrejudgeQuotaText,
  validatePrejudgeLimit,
} from "../src/options.js";

test("validatePrejudgeLimit: validates 0-200 integers", () => {
  // Valid integers
  assert.deepEqual(validatePrejudgeLimit(0), { valid: true, value: 0, error: null });
  assert.deepEqual(validatePrejudgeLimit(20), { valid: true, value: 20, error: null });
  assert.deepEqual(validatePrejudgeLimit(200), { valid: true, value: 200, error: null });
  assert.deepEqual(validatePrejudgeLimit("50"), { valid: true, value: 50, error: null });
  assert.deepEqual(validatePrejudgeLimit("0"), { valid: true, value: 0, error: null });
  assert.deepEqual(validatePrejudgeLimit("200"), { valid: true, value: 200, error: null });

  // Invalid integers (<0, >200, non-integer, empty)
  assert.equal(validatePrejudgeLimit(-1).valid, false);
  assert.equal(validatePrejudgeLimit(201).valid, false);
  assert.equal(validatePrejudgeLimit(10.5).valid, false);
  assert.equal(validatePrejudgeLimit("10.5").valid, false);
  assert.equal(validatePrejudgeLimit("abc").valid, false);
  assert.equal(validatePrejudgeLimit("").valid, false);
  assert.equal(validatePrejudgeLimit(null).valid, false);
  assert.equal(validatePrejudgeLimit(undefined).valid, false);
});


test("formatPrejudgeQuotaText: formats used, remaining, limit info", () => {
  assert.equal(
    formatPrejudgeQuotaText(3, 17, 20),
    "今日已用：3 页 / 剩余：17 页（上限：20 页）"
  );
  assert.equal(
    formatPrejudgeQuotaText(0, 0, 0),
    "今日已用：0 页 / 剩余：0 页（上限：0 页，已关闭）"
  );
});
