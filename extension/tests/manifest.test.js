import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const manifestPath = path.resolve(__dirname, "../manifest.json");
const pyprojectPath = path.resolve(__dirname, "../../pyproject.toml");

test("manifest configuration and security boundaries", () => {
  const content = fs.readFileSync(manifestPath, "utf-8");
  const manifest = JSON.parse(content);

  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.name, "Jet");
  // 插件和服务端同一个版本号（发版时两处一起改）
  const pyVersion = fs.readFileSync(pyprojectPath, "utf-8").match(/^version = "([^"]+)"/m)[1];
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
  assert.equal(manifest.version, pyVersion);

  // host_permissions 恰好是这两项
  const expectedHosts = ["https://*.zhipin.com/*", "http://127.0.0.1/*"];
  assert.deepEqual(manifest.host_permissions, expectedHosts);

  // 没有 <all_urls>
  assert.ok(!manifest.host_permissions.includes("<all_urls>"));
  if (manifest.permissions) {
    assert.ok(!manifest.permissions.includes("<all_urls>"));
    // 没有 webRequest / debugger / cookies
    assert.ok(!manifest.permissions.includes("webRequest"));
    assert.ok(!manifest.permissions.includes("debugger"));
    assert.ok(!manifest.permissions.includes("cookies"));
  }

  // content_scripts 只匹配 https://www.zhipin.com/*
  assert.ok(Array.isArray(manifest.content_scripts));
  assert.equal(manifest.content_scripts.length, 1);
  assert.deepEqual(manifest.content_scripts[0].matches, ["https://www.zhipin.com/*"]);
  assert.deepEqual(manifest.content_scripts[0].js, ["src/content.js"]);
  assert.equal(manifest.content_scripts[0].run_at, "document_idle");
});
