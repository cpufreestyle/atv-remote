#!/usr/bin/env node
/* 窗格转场与触控目标（第二十五轮）纯函数 harness：从 static/app.js 抽出 panemotion 段
   原样执行并跑用例。Python 单测 tests/test_panemotion.py 通过 subprocess 调它，
   让「面板 id 映射 / 减少动效时长 / 常量」有真行为证据，而非源码字符串断言。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== panemotion:begin";
const END = "/* ===== panemotion:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: panemotion 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
const code = stripComments(seg);
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (code.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["PANE_ANIM_MS", "TOUCH_MIN_PX", "paneIdFor", "paneAnimMs"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("常量：转场 240ms / 触控 36px（与 CSS --pane-dur / --touch-min 对齐）", () => {
  ok(H.PANE_ANIM_MS === 240, "PANE_ANIM_MS=" + H.PANE_ANIM_MS);
  ok(H.TOUCH_MIN_PX === 36, "TOUCH_MIN_PX=" + H.TOUCH_MIN_PX);
});

t("paneIdFor：data-tab 到面板 id 的纯映射，空值不炸", () => {
  ok(H.paneIdFor("dpad") === "pane-dpad", "dpad");
  ok(H.paneIdFor("pad") === "pane-pad", "pad");
  ok(H.paneIdFor("") === "pane-", "空串");
  ok(H.paneIdFor(null) === "pane-", "null");
  ok(H.paneIdFor(undefined) === "pane-", "undefined");
});

t("paneAnimMs：减少动效偏好取 0，否则标准 240ms", () => {
  ok(H.paneAnimMs(false) === 240, "false -> 240");
  ok(H.paneAnimMs(true) === 0, "true -> 0");
  ok(H.paneAnimMs() === 240, "缺省 -> 240");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("NOT OK - " + name + " :: " + err.message); }
}
console.log(failed === 0 ? "ALL_PANEMOTION_CASES_PASSED" : "PANEMOTION_FAILURES=" + failed);
process.exit(failed === 0 ? 0 : 1);
