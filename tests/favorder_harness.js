#!/usr/bin/env node
/* favorder 段纯函数 harness：从 static/app.js 抽出 favorder 段原样执行并跑用例。
   Python 单测 tests/test_favorder.py 通过 subprocess 调它。
   段内禁 DOM / 持久化 / 网络：规则必须是可搬进 node 的纯函数。 */
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
const seg = slice("/* ===== favorder:begin", "/* ===== favorder:end");
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (stripComments(seg).indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const EXPORTS = ["favMove", "favCanMove", "favIndexOf"].join(", ");
const H = new Function(seg + String.fromCharCode(10) + "return { " + EXPORTS + " };")();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const L = (n) => [1, 2, 3, 4, 5].slice(0, n);

t("favMove：移动语义 = 从 from 取出、插到 to（与 SortableJS 的 oldIndex/newIndex 同构）", () => {
  ok(eq(H.favMove(L(5), 0, 1), [2, 1, 3, 4, 5]), "0 -> 1");
  ok(eq(H.favMove(L(5), 4, 3), [1, 2, 3, 5, 4]), "4 -> 3（取出 5 插到下标 3）");
  ok(eq(H.favMove(L(5), 2, 0), [3, 1, 2, 4, 5]), "2 -> 0 跨过两项");
  ok(eq(H.favMove(L(5), 1, 4), [1, 3, 4, 5, 2]), "1 -> 4");
});

t("favMove：不动入参——调用方要拿旧值做撤销", () => {
  const src = L(5);
  const out = H.favMove(src, 0, 2);
  ok(eq(src, L(5)), "入参必须原样");
  ok(out !== src, "必须返回新数组");
});

t("favMove：越界与坏入参全部安全退化", () => {
  ok(eq(H.favMove(L(3), -1, 0), L(3)), "from 为负不动");
  ok(eq(H.favMove(L(3), 9, 0), L(3)), "from 越界不动");
  ok(eq(H.favMove(L(3), 1, -5), [2, 1, 3]), "to 低于 0 钳到首位");
  ok(eq(H.favMove(L(3), 0, -5), L(3)), "from 已在首位且 to 钳到首位：不动");
  ok(eq(H.favMove(L(3), 2, 99), [1, 2, 3]), "to 越界钳到末位");
  ok(eq(H.favMove(L(3), 1, 1), L(3)), "原地移动返回等价新数组");
  ok(eq(H.favMove([], 0, 1), []), "空数组");
  ok(eq(H.favMove(null, 0, 1), []), "null 入参");
  ok(eq(H.favMove("abc", 0, 1), []), "非数组入参");
});

t("favMove：可逆——上移一步再下移一步回到原状", () => {
  const a = L(5);
  const b = H.favMove(a, 3, 2);
  const c = H.favMove(b, 2, 3);
  ok(eq(c, a), "一次上移加一次下移应还原");
});

t("favCanMove：首行不上移、末行不下移，其余放行", () => {
  ok(H.favCanMove(L(3), 0, "up") === false, "首行不上移");
  ok(H.favCanMove(L(3), 0, "down") === true, "首行可下移");
  ok(H.favCanMove(L(3), 2, "down") === false, "末行不下移");
  ok(H.favCanMove(L(3), 2, "up") === true, "末行可上移");
  ok(H.favCanMove(L(1), 0, "up") === false, "单项列表上移锁");
  ok(H.favCanMove(L(1), 0, "down") === false, "单项列表下移锁");
  ok(H.favCanMove(L(3), 1, "left") === false, "未知方向拒绝");
  ok(H.favCanMove(L(3), -1, "up") === false, "负下标拒绝");
  ok(H.favCanMove(L(3), 9, "down") === false, "越界下标拒绝");
  ok(H.favCanMove(null, 0, "up") === false, "坏入参拒绝");
});

t("favIndexOf：按 pkg 定位，找不到给 -1", () => {
  const pins = [
    { name: "Netflix", pkg: "com.netflix.ninja" },
    { name: "YouTube", pkg: "com.google.android.youtube.tv" },
  ];
  ok(H.favIndexOf(pins, "com.google.android.youtube.tv") === 1, "第二项");
  ok(H.favIndexOf(pins, "com.netflix.ninja") === 0, "第一项");
  ok(H.favIndexOf(pins, "nope") === -1, "不存在");
  ok(H.favIndexOf([], "x") === -1, "空数组");
  ok(H.favIndexOf(null, "x") === -1, "null 入参");
});

t("组合场景：连续上移把末项顶到首位（收藏夹最常见的调序诉求）", () => {
  let list = L(4);
  for (let i = 3; i > 0; i--) list = H.favMove(list, i, i - 1);
  ok(eq(list, [4, 1, 2, 3]), "got " + JSON.stringify(list));
});

t("组合场景：下移把首项沉到底部", () => {
  let list = L(4);
  for (let i = 0; i < 3; i++) list = H.favMove(list, i, i + 1);
  ok(eq(list, [2, 3, 4, 1]), "got " + JSON.stringify(list));
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_FAVORDER_CASES_PASSED");

