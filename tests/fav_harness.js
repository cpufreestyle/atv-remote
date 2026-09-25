#!/usr/bin/env node
/* 收藏夹 / 快捷启动纯函数 harness：从 static/app.js 抽出 favorites 段原样执行并跑用例。
   Python 单测 tests/test_favorites.py 通过 subprocess 调它，让上限 / 去重 / 置顶 /
   名字兜底有真行为证据，而不只是源码字符串断言。改动段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== favorites:begin";
const END = "/* ===== favorites:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: favorites 段标记缺失或顺序错误");
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
const EXPORTS = ["FAV_MAX", "favNorm", "favIsPinned", "favToggle", "favResolve"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

const YT = "com.google.android.youtube.tv";
const PRESETS = [
  { name: "YouTube", pkg: YT },
  { name: "Netflix", pkg: "com.netflix.ninja" },
];

t("常量：收藏夹上限 8（超过一屏就失去「省得找」的意义）", () => {
  ok(H.FAV_MAX === 8, "FAV_MAX=" + H.FAV_MAX);
});

t("favNorm：坏项全丢、按 pkg 去重保留首现、限量", () => {
  ok(JSON.stringify(H.favNorm(null)) === "[]", "null 不炸");
  ok(JSON.stringify(H.favNorm(undefined)) === "[]", "undefined 不炸");
  ok(JSON.stringify(H.favNorm("x")) === "[]", "非数组");
  const r1 = H.favNorm([null, {}, { name: "A", pkg: "a" }, { name: "B", pkg: "a" }, { pkg: "b" }]);
  ok(r1.length === 2 && r1[0].pkg === "a" && r1[1].pkg === "b", JSON.stringify(r1));
  ok(r1[0].name === "A", "去重保留首现的名字");
  ok(r1[1].name === "", "缺名不丢，交给 favResolve 兜底");
  const many = [];
  for (let i = 0; i < 20; i++) many.push({ name: "N" + i, pkg: "p" + i });
  ok(H.favNorm(many).length === 8, "限量到 FAV_MAX");
});

t("favIsPinned：在 / 不在 / 坏入参", () => {
  const l = [{ name: "A", pkg: "a" }];
  ok(H.favIsPinned(l, "a") === true);
  ok(H.favIsPinned(l, "b") === false);
  ok(H.favIsPinned(null, "a") === false, "null 不炸");
  ok(H.favIsPinned(l, null) === false, "null pkg 不炸");
});

t("favToggle：未固定→置顶；已固定→移除；满员拒绝（null）不静默丢", () => {
  let l = H.favToggle([], YT, "YouTube");
  ok(l.length === 1 && l[0].pkg === YT && l[0].name === "YouTube", "首个置顶");
  l = H.favToggle(l, "com.netflix.ninja", "Netflix");
  ok(l.length === 2 && l[0].pkg === "com.netflix.ninja", "新固定的排最前");
  l = H.favToggle(l, YT, "YouTube");
  ok(l.length === 1 && l[0].pkg === "com.netflix.ninja", "再点一次 = 取消");
  const full = [];
  for (let i = 0; i < 8; i++) full.push({ name: "N" + i, pkg: "p" + i });
 ok(H.favToggle(full, "p8", "N8") === null, "满员返回 null 让调用方提示");
  ok(H.favToggle(full, "p0", "x").length === 7, "满员时取消已有的仍可用，并不卡死");
});

t("favResolve：pin 名优先，其次预设/最近，最后包名末段", () => {
  const pins = [{ name: "自定义名", pkg: YT }, { name: "", pkg: "org.xbmc.kodi" },
                { name: "", pkg: "com.some.thing" }];
  const recents = [{ name: "最近里的", pkg: "org.xbmc.kodi" }];
  const r = H.favResolve(pins, PRESETS, recents);
  ok(r.length === 3, "一条不多一条不少");
  ok(r[0].name === "自定义名", "pin 自带名字优先");
  ok(r[1].name === "最近里的", "预设没有 → 查最近");
  ok(r[2].name === "thing", "都没有 → 包名末段，不长串糊脸");
  ok(JSON.stringify(H.favResolve(null, PRESETS, recents)) === "[]", "null 不炸");
  ok(H.favResolve([{ pkg: "a.b" }], null, null).length === 1, "数据源缺失也解析");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_FAV_CASES_PASSED");
