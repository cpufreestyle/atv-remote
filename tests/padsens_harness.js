#!/usr/bin/env node
/* 触摸板灵敏度纯函数 harness：从 static/app.js 抽出 padsens 段原样执行并跑用例。
   Python 单测 tests/test_padsens.py 通过 subprocess 调它，让「钳位 / 步长缩放 /
   连发间隔缩放 / 读数」有真行为证据，而不只是源码字符串断言。改动段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== padsens:begin";
const END = "/* ===== padsens:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: padsens 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
// 段内禁 DOM / 持久化 / 网络：规则必须是可搬进 node 的纯函数。
// 先剥注释再查——段首注释里列举了这些禁用词，是给人看的说明，不是违规引用。
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
const EXPORTS = ["PADSENS_MIN", "PADSENS_MAX", "PADSENS_DEFAULT", "PADSENS_STEP_MIN",
  "PADSENS_STEP_MAX", "PADSENS_RATE_MIN", "PADSENS_RATE_MAX", "padSensClamp",
  "padStepFor", "padHoldRateFor", "padSensLabel"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("常量：范围 0.5x-2.0x、默认 1x（出厂手感）", () => {
  ok(H.PADSENS_MIN === 0.5, "PADSENS_MIN=" + H.PADSENS_MIN);
  ok(H.PADSENS_MAX === 2, "PADSENS_MAX=" + H.PADSENS_MAX);
  ok(H.PADSENS_DEFAULT === 1, "PADSENS_DEFAULT=" + H.PADSENS_DEFAULT);
});

t("常量：步长与间隔的钳位区间（防增益极值时一次滑动刷屏或被吞）", () => {
  ok(H.PADSENS_STEP_MIN === 8 && H.PADSENS_STEP_MAX === 64, "step 钳位区间");
  ok(H.PADSENS_RATE_MIN === 45 && H.PADSENS_RATE_MAX === 400, "rate 钳位区间");
});

t("padSensClamp：钳进 [0.5,2] 并按 0.05 吸附；坏入参回默认 1", () => {
  ok(H.padSensClamp(0.1) === 0.5, "低于下限钳到 0.5");
  ok(H.padSensClamp(3) === 2, "高于上限钳到 2");
  ok(H.padSensClamp(1.03) === 1.05, "吸附到 0.05 网格");
  ok(H.padSensClamp("x") === 1, "非数字回默认");
  ok(H.padSensClamp(null) === 1, "null 回默认");
  ok(H.padSensClamp(NaN) === 1, "NaN 回默认");
  ok(H.padSensClamp(undefined) === 1, "undefined 回默认");
});

t("padStepFor：增益越大步长越短——双指手势越跟手（1x=26 / 2x=13 / 0.5x=52）", () => {
  ok(H.padStepFor(1) === 26, "1x -> 26");
  ok(H.padStepFor(2) === 13, "2x -> 13");
  ok(H.padStepFor(0.5) === 52, "0.5x -> 52");
  ok(H.padStepFor(1.6) === 16, "1.6x -> 16（26/1.6=16.25 取整）");
  ok(H.padStepFor(3) === 13, "越界增益先钳 2x 再算，步长不得越过钳位下限");
  ok(H.padStepFor(0.01) === 52, "极低增益同样先钳 0.5x");
});

t("padHoldRateFor：增益越大间隔越短——长按连发越跟手（1x=120 / 2x=60 / 0.5x=240）", () => {
  ok(H.padHoldRateFor(1) === 120, "1x -> 120");
  ok(H.padHoldRateFor(2) === 60, "2x -> 60");
  ok(H.padHoldRateFor(0.5) === 240, "0.5x -> 240");
  ok(H.padHoldRateFor(3) === 60, "越界增益先钳 2x");
  ok(H.padHoldRateFor(0.01) === 240, "极低增益先钳 0.5x");
});

t("padSensLabel：读数 1x / 0.5x / 1.25x（两位小数内，不带尾零）", () => {
  ok(H.padSensLabel(1) === "1×", "默认读 1x");
  ok(H.padSensLabel(0.5) === "0.5×", "下限读数");
  ok(H.padSensLabel(1.25) === "1.25×", "中点读数");
  ok(H.padSensLabel("x") === "1×", "坏入参读数回默认");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_PADSENS_CASES_PASSED");
