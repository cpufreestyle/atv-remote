#!/usr/bin/env node
/* palmark 段纯函数 harness：从 static/app.js 抽出 palmark 段原样执行并跑用例。
   intent 段的 intentEditErrors / intentMaxErrors 会被 palmark 调用，所以两个段一起载入。
   Python 单测 tests/test_palmark.py 通过 subprocess 调它。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
function slice(beginMark, endMark) {
  const s = src.indexOf(beginMark), e = src.indexOf(endMark);
  if (s < 0 || e < 0 || e < s) throw new Error("段标记缺失或顺序错误: " + beginMark);
  return src.slice(s, e);
}
const seg = slice("/* ===== palmark:begin", "/* ===== palmark:end") + "\n" +
  slice("/* ===== intent:begin", "/* ===== intent:end");
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (stripComments(seg).indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["palMarkRanges", "palMarkMerge", "palMarkWindow", "intentEditErrors",
  "intentMaxErrors", "INTENT_ERR_RATE"].join(", ");
const H = new Function(seg + NL + "return { " + EXPORTS + " };")();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

t("空查询 / 空标签：一律不高亮", () => {
  ok(eq(H.palMarkRanges("打开 YouTube", ""), []), "空查询");
  ok(eq(H.palMarkRanges("", "yt"), []), "空标签");
  ok(eq(H.palMarkRanges(null, "yt"), []), "null 标签");
  ok(eq(H.palMarkRanges("abc", null), []), "null 查询");
});

t("第 1、2 档：子串整段高亮（含大小写不敏感）", () => {
  ok(eq(H.palMarkRanges("打开 YouTube", "youtube"), [[3, 10]]), "词中命中");
  ok(eq(H.palMarkRanges("YouTube", "you"), [[0, 3]]), "词首命中");
  ok(eq(H.palMarkRanges("打开 YouTube", "zzzq"), []), "不命中给空");
});

t("散乱子序列：预算 0 时逐字高亮并合并相邻区间", () => {
  // "yt" 在 "YouTube" 里是 y(0) 与 t(2)? y-o-u-t-u-b-e: y=0, t=3
  ok(eq(H.palMarkRanges("YouTube", "yt"), [[0, 1], [3, 4]]), "got " + JSON.stringify(H.palMarkRanges("YouTube", "yt")));
  ok(eq(H.palMarkRanges("YouTube", "ybe"), [[0, 1], [5, 7]]), "y + be");
  ok(eq(H.palMarkRanges("YouTube", "youtube"), [[0, 7]]), "全串先按子串走");
});

t("第 5 档：近似窗口——容错命中也要指出是哪一段", () => {
  // "yutube" 少个 o，窗口 [0,7) 只有 1 个错
  const r = H.palMarkRanges("打开 YouTube", "yutube");
  ok(eq(r, [[3, 10]]), "应整段覆盖 YouTube，got " + JSON.stringify(r));
  const n = H.palMarkRanges("打开 Netflix", "netflx");
  ok(eq(n, [[3, 10]]), "netflx 应整段覆盖 Netflix，got " + JSON.stringify(n));
});

t("palMarkMerge：相邻/重叠区间合并，间隔保留", () => {
  ok(eq(H.palMarkMerge([[0, 1], [1, 2], [2, 3]]), [[0, 3]]), "相邻合并");
  ok(eq(H.palMarkMerge([[0, 1], [3, 4]]), [[0, 1], [3, 4]]), "有间隔不合并");
  ok(eq(H.palMarkMerge([[0, 3], [1, 2]]), [[0, 3]]), "包含式合并");
  ok(eq(H.palMarkMerge([]), []), "空入参");
});

t("palMarkWindow：错误最少；平手时取最靠前且覆盖最宽的窗口", () => {
  const w = H.palMarkWindow("yutube", "打开 youtube", H.intentMaxErrors("yutube"));
  ok(w && w.err === 1, "yutube 在 youtube 里 1 个错，got " + JSON.stringify(w));
  ok(w.start === 3 && w.end === 10, "窗口应从词首起、覆盖整个 youtube，got " + JSON.stringify(w));
  ok(H.palMarkWindow("zzzz", "youtube", 1) === null, "超预算给 null");
  const w2 = H.palMarkWindow("netflx", "打开 netflix", 2);
  ok(w2 && w2.start === 3 && w2.end === 10, "平手时要覆盖整个词而非短一截，got " + JSON.stringify(w2));
  ok(H.palMarkWindow("abc", "", 1) === null, "空文本给 null");
  ok(H.palMarkWindow("abc", "abc", 0) === null, "预算 0 不走近似窗口");
});

t("保守原则：对不上就空数组，绝不乱高亮", () => {
  ok(eq(H.palMarkRanges("打开 YouTube", "zzzzz"), []), "完全无关的查询");
  ok(eq(H.palMarkRanges("按键 音量-", "周杰伦"), []), "中文无关查询");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_PALMARK_CASES_PASSED");

