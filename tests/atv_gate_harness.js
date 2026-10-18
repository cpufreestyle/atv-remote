#!/usr/bin/env node
/* Apple TV 能力门禁（I24）纯函数 harness：从 static/app.js 抽出 atv-gate 段原样执行并跑用例。
   Python 单测 tests/test_atv_gate.py 通过 subprocess 调它，让「available 三态下的显隐翻转」
   有真行为证据，而非源码字符串断言。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== atv-gate:begin";
const END = "/* ===== atv-gate:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: atv-gate 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
function stripComments(x) {
  return x.replace(/\/[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
const code = stripComments(seg);
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (code.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["atvGateState"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("可用（Mac 服务端 / pyatv 就绪）：扫描入口与列表显示，门禁提示隐藏", () => {
  const g = H.atvGateState({ appletv: { available: true, devices: [] } });
  ok(g.supported === true, "supported");
  ok(g.showScan === true && g.showList === true, "扫描入口与列表要在");
  ok(g.showNote === false, "提示不要");
});

t("不可用（-PnoAppletv 构建）：全部翻转，一个死按钮都不留", () => {
  const g = H.atvGateState({ appletv: { available: false, devices: [] } });
  ok(g.supported === false, "supported");
  ok(g.showScan === false && g.showList === false, "死入口要隐藏");
  ok(g.showNote === true, "解释要显示");
});

t("available 缺失按不可用：旧服务端 / 字段漂移不得放行死按钮", () => {
  for (const bad of [undefined, null, {}, { appletv: {} },
                     { appletv: { devices: [{ id: "x" }] } }]) {
    const g = H.atvGateState(bad);
    ok(g.supported === false, "缺 available 一律不可用: " + JSON.stringify(bad));
    ok(g.showNote === true && g.showScan === false, "隐藏死入口: " + JSON.stringify(bad));
  }
});

t("available 只认 true：truthy 字符串 / 1 / 对象不得当可用", () => {
  for (const v of ["true", 1, "yes", {}]) {
    const g = H.atvGateState({ appletv: { available: v } });
    ok(g.supported === false, "非布尔 true 不当可用: " + JSON.stringify(v));
  }
});

t("devices 非空但 available=false 仍不可用：判据单一，不拿设备列表反推", () => {
  const g = H.atvGateState({ appletv: { available: false, devices: [{ id: "a" }, { id: "b" }] } });
  ok(g.supported === false, "设备列表不是判据");
  ok(g.showScan === false, "仍不给扫描入口");
});

t("status 整个缺失（请求失败时的兜底）：按不可用，不抛", () => {
  for (const bad of [undefined, null, 0, "", "x", 42]) {
    const g = H.atvGateState(bad);
    ok(g.supported === false, "坏 status 不当可用: " + JSON.stringify(bad));
    ok(g.showNote === true, "给解释: " + JSON.stringify(bad));
  }
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("NOT OK - " + name + " :: " + err.message); }
}
console.log(failed === 0 ? "ALL_ATV_GATE_CASES_PASSED" : "ATV_GATE_FAILURES=" + failed);
process.exit(failed === 0 ? 0 : 1);
