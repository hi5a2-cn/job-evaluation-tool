import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const srcDir = path.resolve(__dirname, "../src");

const files = fs
  .readdirSync(srcDir)
  .filter((f) => f.endsWith(".js"))
  .sort();

assert.ok(files.length > 0, "No .js files found in extension/src");

for (const file of files) {
  test(`syntax check: ${file}`, () => {
    const fullPath = path.join(srcDir, file);
    const result = spawnSync(process.execPath, ["--check", fullPath], {
      encoding: "utf-8",
    });
    const errorDetails = (result.stderr || result.stdout || "").trim();
    assert.strictEqual(
      result.status,
      0,
      `Syntax check failed for ${file} (exit code ${result.status}):\n${errorDetails}`
    );
  });
}

test("content_scripts classic script syntax check", () => {
  const manifestPath = path.resolve(__dirname, "../manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  const contentScriptFiles = [];
  if (Array.isArray(manifest.content_scripts)) {
    for (const entry of manifest.content_scripts) {
      if (Array.isArray(entry.js)) {
        for (const file of entry.js) {
          if (!contentScriptFiles.includes(file)) {
            contentScriptFiles.push(file);
          }
        }
      }
    }
  }
  assert.ok(contentScriptFiles.length > 0, "No content_scripts found in manifest.json");

  for (const filename of contentScriptFiles) {
    const fullPath = path.resolve(__dirname, "..", filename);
    const source = fs.readFileSync(fullPath, "utf-8");
    try {
      new vm.Script(source, { filename });
    } catch (err) {
      assert.fail(`Classic script check failed for ${filename}: ${err.message}`);
    }
  }
});

test("self-check: vm.Script rejects ESM import and unmatched brackets", () => {
  assert.throws(
    () => new vm.Script('import x from "./y.js";', { filename: "test-import.js" }),
    SyntaxError
  );
  assert.throws(
    () => new vm.Script("(() => { if (true) { })();", { filename: "test-unmatched.js" }),
    SyntaxError
  );
});
