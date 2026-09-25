#!/usr/bin/env node
/* 调用时间线（perf waterfall）纯函数 harness：从 static/app.js 抽出 perf-waterfall 段原样执行并跑用例。
   Python 单测 tests/test_perf.py 通过 subprocess 调它——瀑布的几何与汇总因此有真行为证据，
   不只是源码字符串断言。改动 app.js 里段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== perf-waterfall:begin";
const END = "/* ===== perf-waterfall:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: perf-waterfall 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
if (seg.indexOf("document.") >= 0 || seg.indexOf("localStorage") >= 0 || seg.indexOf("$(") >= 0) {
  console.error("harness: 纯函数段里出现 DOM / 全局态引用");
  process.exit(2);
}
const NL = String.fromCharCode(10);
const factory = new Function(seg + NL + "return { PERF_SLOW_FE, PERF_KIND_NAMES_FE, PERF_MS_FMT_FE, PERF_MAX_BARS_FE, perfLevel, perfPct, perfSummary, perfWindowMs, perfBar, perfBars };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("ago 几何：右缘贴 now-ago，长度=耗时，left+width=right", () => {
  const b = H.perfBar({ kind: "adb", label: "devices", ms: 100, ago: 1500 }, 2000);
  ok(b.width === 100 / 2000, "width=" + b.width);
  ok(b.left === (2000 - 1500) / 2000 - b.width, "left=" + b.left);
  ok(b.level === "ok" && b.kind === "adb" && b.label === "devices");
});

t("ago + ms 超过窗口：clamp 到 [0,1]，不出血", () => {
  const b = H.perfBar({ kind: "adb", ms: 3000, ago: 2500 }, 2000);
  ok(b.left >= 0 && b.width <= 1 && b.left + b.width <= 1, "left=" + b.left + " width=" + b.width);
});

t("负数 ago/ms：钳到 0（零宽条贴在「现在」）", () => {
  const b = H.perfBar({ kind: "shell", ms: -50, ago: -10 }, 2000);
  ok(b.ms === 0 && b.width === 0, JSON.stringify(b));
  ok(b.left === 1, "ago=0 时右缘就是现在，left=" + b.left);
});

t("level：err 优先于一切，cache 次之", () => {
  ok(H.perfLevel({ err: "boom" }) === "err");
  ok(H.perfLevel({ err: "boom", cache: true, ms: 99999 }) === "err");
  ok(H.perfLevel({ cache: true }) === "cache");
  ok(H.perfLevel({}) === "ok");
});

t("level：慢阈值按 kind 分开（adb 150 / shell 250 / devices 永不告警）", () => {
  ok(H.perfLevel({ kind: "adb", ms: 149 }) === "ok");
  ok(H.perfLevel({ kind: "adb", ms: 150 }) === "warn");
  ok(H.perfLevel({ kind: "shell", ms: 200 }) === "ok");
  ok(H.perfLevel({ kind: "shell", ms: 250 }) === "warn");
  ok(H.perfLevel({ kind: "pyatv", ms: 250 }) === "warn");
  ok(H.perfLevel({ kind: "devices", ms: 100000 }) === "ok");
  ok(H.perfLevel({ kind: "no-such-kind", ms: 100000 }) === "ok");
});

t("window：默认 2000，盖住最老一条 +1s 余量", () => {
  ok(H.perfWindowMs([]) === 2000);
  ok(H.perfWindowMs([{ ago: 0 }]) === 2000);
  ok(H.perfWindowMs([{ ago: 500 }, { ago: 3000 }]) === 4000);
});

t("summary：p50/p95/max/errs/cache/cacheRate", () => {
  const evs = [
    { kind: "adb", ms: 10 }, { kind: "shell", ms: 20 }, { kind: "shell", ms: 30 },
    { kind: "shell", ms: 40, cache: true }, { kind: "pyatv", ms: 50, err: "x" },
  ];
  const s = H.perfSummary(evs);
  ok(s.n === 5 && s.max === 50, JSON.stringify(s));
  ok(s.p50 === 30, "p50=" + s.p50);
  ok(s.p95 === 48, "p95=" + s.p95);
  ok(s.errs === 1 && s.cache === 1 && s.cacheRate === 0.2, JSON.stringify(s));
  ok(H.perfSummary([]).cacheRate === 0);
});

t("pct 单样本直接给值；线性插值口径", () => {
  ok(H.perfPct([42], 0.5) === 42);
  ok(H.perfPct([10, 20], 0.5) === 15);
  ok(H.perfPct([10, 20, 30], 0.5) === 20);
  ok(H.perfPct([], 0.95) === 0);
});

t("ms 格式化：<1000 用 ms，>=1000 用 s 且不带 .0", () => {
  ok(H.PERF_MS_FMT_FE(0) === "0ms");
  ok(H.PERF_MS_FMT_FE(999) === "999ms");
  ok(H.PERF_MS_FMT_FE(1000) === "1s");
  ok(H.PERF_MS_FMT_FE(1234) === "1.2s");
  ok(H.PERF_MS_FMT_FE(-5) === "0ms");
});

t("onlyProblems：只留 warn/err，掉 cache 与 ok，最新在上", () => {
  const evs = [
    { kind: "adb", ms: 10, ago: 0 },
    { kind: "adb", ms: 10, ago: 100, cache: true },
    { kind: "shell", ms: 300, ago: 200 },
    { kind: "pyatv", ms: 10, ago: 300, err: "boom" },
  ];
  const bars = H.perfBars(evs, { onlyProblems: true, windowMs: 2000 });
  ok(bars.length === 2, "rows=" + bars.length);
  ok(bars[0].level === "err" && bars[1].level === "warn", "顺序: " + bars.map((b) => b.level).join(","));
  ok(bars[0].note === "boom", "note=" + bars[0].note);
});

t("bars：最新在上 + 超过 40 条取最新 40 + 行模型不带 ago", () => {
  const evs = Array.from({ length: 45 }, (_, i) => ({ kind: "adb", ms: 5, ago: 45 - i }));
  const bars = H.perfBars(evs, { windowMs: 2000 });
  ok(bars.length === 40, "rows=" + bars.length);
  ok(bars[0].ago === undefined, "位置应已烘进 left/width");
  ok(H.PERF_MAX_BARS_FE === 40);
});

t("kind 徽标翻成前端说法", () => {
  ok(H.PERF_KIND_NAMES_FE.pyatv === "AppleTV");
  ok(H.PERF_KIND_NAMES_FE.devices === "缓存");
  ok(H.PERF_KIND_NAMES_FE.adb === "adb");
});

t("缺字段/空数组不炸", () => {
  ok(H.perfBars(null).length === 0);
  ok(H.perfBars(undefined, {}).length === 0);
  const b = H.perfBar({}, 0);
  ok(b.kind === "adb" && b.ms === 0 && b.label === "");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_PERF_CASES_PASSED");
