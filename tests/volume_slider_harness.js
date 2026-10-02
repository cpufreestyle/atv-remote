#!/usr/bin/env node
/* volset 段纯函数 harness：从 static/app.js 抽出 volset 段原样执行并跑用例。
   Python 单测 tests/test_volume_set.py 通过 subprocess 调它。
   段内禁 DOM / 持久化 / 网络 / 定时器：口径必须是可搬进 node 的纯函数，服务端
   server.py 的 clamp_volume_level 要能和这里的 volSetClamp 逐值对齐。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
function slice(b, e) {
  const s = src.indexOf(b), en = src.indexOf(e);
  if (s < 0 || en < 0 || en < s) throw new Error("段标记缺失或顺序错误: " + b);
  return src.slice(s, en);
}
const seg = slice("/* ===== volset:begin", "/* ===== volset:end");
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (stripComments(seg).indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const EXPORTS = ["VOLSET_MAX_FALLBACK", "volSetClamp", "volSetPct", "volSetPresets",
  "volSetReconcile", "volSetUsable"].join(", ");
const H = new Function(seg + String.fromCharCode(10) + "return { " + EXPORTS + " };\n")();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

t("volSetClamp：先 round 后夹（8.5→9、7.5→8，半点向上，与服务端 int(raw+0.5) 同口径）", () => {
  ok(eq(H.volSetClamp(7.5, 15), [8, 15]), "7.5 -> 8");
  ok(eq(H.volSetClamp(8.5, 15), [9, 15]), "8.5 -> 9");
  ok(eq(H.volSetClamp(7.6, 15), [8, 15]), "7.6 -> 8（只夹不舍会卡成 7）");
  ok(eq(H.volSetClamp(0.4, 15), [0, 15]), "0.4 -> 0");
});

t("volSetClamp：越界夹住，绝不放 16 / -1 出去", () => {
  ok(eq(H.volSetClamp(99, 15), [15, 15]), "99 -> 15");
  ok(eq(H.volSetClamp(-3, 15), [0, 15]), "-3 -> 0");
  ok(eq(H.volSetClamp(-0.5, 15), [0, 15]), "-0.5 -> 0（Math.round(-0.5) 是 -0，别漏出去）");
});

t("volSetClamp：max 不可信时兜底 15（读通道挂过、回读值是旧值）", () => {
  ok(eq(H.volSetClamp(5, 0), [5, 15]), "max=0 -> 15");
  ok(eq(H.volSetClamp(5, ""), [5, 15]), "max=空串 -> 15");
  ok(eq(H.volSetClamp(5, undefined), [5, 15]), "max=undefined -> 15");
  ok(eq(H.volSetClamp(5, null), [5, 15]), "max=null -> 15");
  ok(eq(H.volSetClamp(5, "abc"), [5, 15]), "max 非数字 -> 15");
  ok(eq(H.volSetClamp(5, -4), [5, 15]), "max 为负 -> 15");
  ok(eq(H.volSetClamp(99, 25), [25, 25]), "25 格设备照样夹到 25");
  ok(H.VOLSET_MAX_FALLBACK === 15, "兜底值必须是 15（与服务端 VOLUME_MAX_FALLBACK 对齐）");
});

t("volSetClamp：level 不可信按 0 处理，不抛异常", () => {
  ok(eq(H.volSetClamp("abc", 15), [0, 15]), "非数字 level");
  ok(eq(H.volSetClamp(undefined, 15), [0, 15]), "undefined level");
  ok(eq(H.volSetClamp(null, 15), [0, 15]), "null level");
  ok(eq(H.volSetClamp(NaN, 15), [0, 15]), "NaN level");
});

t("volSetPct：滑条填充比例（夹取后算，别自己再除）", () => {
  ok(H.volSetPct(0, 15) === 0, "0 -> 0%");
  ok(H.volSetPct(15, 15) === 100, "满格 -> 100%");
  ok(H.volSetPct(8, 15) === 53, "8/15 -> 53%");
  ok(H.volSetPct(99, 15) === 100, "越界夹到 100%");
  ok(H.volSetPct(-5, 15) === 0, "负数 -> 0%");
  ok(H.volSetPct(3, 0) === 20, "max 坏值时也存在确定性（3/15）");
});

t("volSetPresets：0 / 25% / 50% / 75% / 满格，去重且升序", () => {
  ok(eq(H.volSetPresets(15), [0, 4, 8, 11, 15]), "15 格");
  ok(eq(H.volSetPresets(25), [0, 6, 13, 19, 25]), "25 格（老 ROM 常见）");
  ok(eq(H.volSetPresets(10), [0, 3, 5, 8, 10]), "10 格（Fire TV 常见）");
  ok(eq(H.volSetPresets(1), [0, 1]), "1 格去重后只剩两端");
  ok(eq(H.volSetPresets(4), [0, 1, 2, 3, 4]), "4 格");
  ok(eq(H.volSetPresets(0), [0, 4, 8, 11, 15]), "max 坏值退到 15 的预设");
});

t("volSetReconcile：拖动中冻结 level，只采纳 max / muted", () => {
  const cur = { level: 7, max: 15, muted: false };
  const out = H.volSetReconcile(cur, { level: 2, max: 25, muted: true }, true);
  ok(out.level === 7, "拖动时 level 以手指位置为准");
  ok(out.max === 25, "max 照常采纳");
  ok(out.muted === true, "muted 照常采纳");
  ok(out !== cur, "必须返回新对象");
  ok(cur.level === 7 && cur.max === 15 && cur.muted === false, "入参必须原样");
});

t("volSetReconcile：非拖动时采纳真值，坏值一律保留本地", () => {
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: 12, max: 25, muted: false }, false).level === 12, "采纳真值");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: -1, max: 25, muted: false }, false).level === 2, "level=-1（读不到）保留本地");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: 12, max: 0, muted: false }, false).max === 15, "max=0 是坏值，保留旧 max");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: 12, max: -1, muted: null }, false).max === 15, "max 为负保留旧值");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: 12, max: 25, muted: null }, false).muted === false, "muted=null（不知道）保留本地");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, { level: 12, max: 25, muted: "nope" }, false).muted === false, "muted 非布尔保留本地");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, null, false).level === 2, "回读失败保留本地");
  ok(H.volSetReconcile({ level: 2, max: 15, muted: false }, undefined, true).level === 2, "回读失败且拖动中保留本地");
});

t("volSetUsable：只有「已连接 + 支持 + 读得到格数」三项全真才可用", () => {
  ok(H.volSetUsable({ connected: true, supported: true, level: 8 }) === true, "全真");
  ok(H.volSetUsable({ connected: false, supported: true, level: 8 }) === false, "没连电视");
  ok(H.volSetUsable({ connected: true, supported: false, level: 8 }) === false, "设备不支持（Apple TV）");
  ok(H.volSetUsable({ connected: true, supported: true, level: -1 }) === false, "读不到格数（老 ROM）");
  ok(H.volSetUsable({ connected: true, supported: true, level: 0 }) === true, "0 格是合法值，别当 falsy");
  ok(H.volSetUsable(null) === false, "空 payload");
});

t("组合场景：25 格设备拖到 19 再回读 12，滑条应停在 12 且填充 48%", () => {
  let st = { level: -1, max: 15, muted: false };
  st = H.volSetReconcile(st, { level: 0, max: 25, muted: false }, false);
  ok(st.max === 25, "先采纳设备的 25 格");
  st.level = 19;                       // 手指拖到 19（input 事件只写本地）
  st = H.volSetReconcile(st, { level: 0, max: 25, muted: false }, true);
  ok(st.level === 19, "拖动中：回读的旧值不许把滑条弹回去");
  st = H.volSetReconcile(st, { level: 12, max: 25, muted: false }, false);
  ok(st.level === 12 && H.volSetPct(st.level, st.max) === 48, "松手后被真值纠正（12/25=48%）");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log("cases=" + cases.length + " failed=" + failed);
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_VOLUME_CASES_PASSED");
