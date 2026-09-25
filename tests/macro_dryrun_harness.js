#!/usr/bin/env node
/* 宏预演（dry-run）纯函数 harness：从 static/app.js 抽出 macro-dry-run 段原样执行并跑用例。
   Python 单测 tests/test_macro_dryrun.py 通过 subprocess 调它——前端逻辑因此有真行为证据，
   不只是源码字符串断言。改动 app.js 里段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== macro-dry-run:begin";
const END = "/* ===== macro-dry-run:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: macro-dry-run 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
if (/document\.|localStorage|\$\(/.test(seg)) {
  console.error("harness: 纯函数段里出现 DOM / 全局态引用");
  process.exit(2);
}
const factory = new Function(seg + "\nreturn { macroDryRun, macroDryDelay, MACRO_APP_ID_RE_FE, macroDryFmtSec };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
/* 顶层行（i=0）可有可无（缺 name / 步数不合法时才出现），按步号定位最稳 */
const rowOf = (r, i) => r.rows.find((x) => x.i === i);

const KNOWN = ["com.google.android.youtube.tv", "com.netflix.ninja"];
const envOK = { imeCurrent: true, curType: "android", knownPkgs: KNOWN };
const envNoIme = { imeCurrent: false, curType: "android", knownPkgs: KNOWN };

t("合法宏：ok/errs=0/步数/预计耗时=1200+2500+200", () => {
  const r = H.macroDryRun({ name: "观影", steps: [
    { type: "app", pkg: "com.netflix.ninja" }, { delay: 2500 },
    { type: "key", codes: [25, 25] }] }, envOK);
  ok(r.ok === true); ok(r.errs === 0); ok(r.warns === 0); ok(r.steps === 3);
  ok(r.totalMs === 1200 + 2500 + 200, "totalMs=" + r.totalMs);
  ok(r.rows.length === 3); ok(r.rows[0].text.indexOf("com.netflix.ninja") >= 0);
});

t("缺 name 是 warn 不是 err", () => {
  const r = H.macroDryRun({ steps: [{ type: "key", code: 85 }] }, envOK);
  ok(r.ok === true); ok(r.warns === 1); ok(r.rows[0].text.indexOf("name") >= 0);
});

t("steps 空数组：err", () => {
  const r = H.macroDryRun({ name: "x", steps: [] }, envOK);
  ok(r.ok === false); ok(r.errs >= 1); ok(r.rows[0].text.indexOf("1~20") >= 0);
});

t("21 步超上限：err 且仍给逐条结果", () => {
  const steps = Array.from({ length: 21 }, () => ({ type: "key", code: 85 }));
  const r = H.macroDryRun({ name: "x", steps: steps }, envOK);
  ok(r.ok === false, "ok 应为 false");
  ok(r.errs >= 1, "errs=" + r.errs);
  ok(r.rows.length === 22, "rows=" + r.rows.length);
  ok(r.rows[0].i === 0 && r.rows[0].text.indexOf("21") >= 0, "顶层行不对");
  ok(rowOf(r, 21).level === "ok");
});

t("未知类型：err", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "swipe" }] }, envOK);
  ok(r.ok === false); ok(rowOf(r, 1).level === "err"); ok(rowOf(r, 1).text.indexOf("swipe") >= 0);
});

t("键码非数字：err", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "key", code: "abc" }] }, envOK);
  ok(r.ok === false); ok(rowOf(r, 1).note.indexOf("数字") >= 0);
});

t("33 个键码超上限：err", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "key", codes: Array.from({ length: 33 }, () => 85) }] }, envOK);
  ok(r.ok === false); ok(rowOf(r, 1).note.indexOf("1~32") >= 0);
});

t("负键码：warn（服务端放行但设备会失败）", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "key", code: -25 }] }, envOK);
  ok(r.ok === true); ok(rowOf(r, 1).level === "warn"); ok(rowOf(r, 1).note.indexOf("负键码") >= 0);
});

t("空文本：err；超长文本：err", () => {
  const r1 = H.macroDryRun({ name: "x", steps: [{ type: "text", text: "   " }] }, envOK);
  ok(r1.ok === false);
  const r2 = H.macroDryRun({ name: "x", steps: [{ type: "text", text: "a".repeat(5001) }] }, envOK);
  ok(r2.ok === false); ok(rowOf(r2, 1).note.indexOf("5001 > 5000") >= 0);
});

t("中文文本：无 ADBKeyboard 时 warn，有则 ok；Apple TV 不 warn", () => {
  const st = [{ type: "text", text: "你好" }];
  const a = H.macroDryRun({ name: "x", steps: st }, envNoIme);
  ok(a.ok === true); ok(rowOf(a, 1).level === "warn"); ok(rowOf(a, 1).note.indexOf("ADBKeyboard") >= 0);
  const b = H.macroDryRun({ name: "x", steps: st }, envOK);
  ok(rowOf(b, 1).level === "ok");
  const c = H.macroDryRun({ name: "x", steps: st }, { imeCurrent: false, curType: "appletv", knownPkgs: KNOWN });
  ok(rowOf(c, 1).level === "ok");
});

t("文本带换行：warn", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "text", text: "a\nb" }] }, envOK);
  ok(rowOf(r, 1).level === "warn"); ok(rowOf(r, 1).note.indexOf("换行") >= 0);
});

t("包名不合法：err", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "app", pkg: "1com.foo" }] }, envOK);
  ok(r.ok === false); ok(rowOf(r, 1).note.indexOf("pkg/pkgs") >= 0);
});

t("未知包名 warn，已知包名 ok", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "app", pkg: "com.unknown.app" }] }, envOK);
  ok(rowOf(r, 1).level === "warn"); ok(rowOf(r, 1).note.indexOf("没装") >= 0);
  const r2 = H.macroDryRun({ name: "x", steps: [{ type: "app", pkg: "com.netflix.ninja" }] }, envOK);
  ok(rowOf(r2, 1).level === "ok");
});

t("delay 超上限钳到 10s 并 warn", () => {
  const r = H.macroDryRun({ name: "x", steps: [{ type: "key", code: 85, delay: 25000 }] }, envOK);
  ok(r.ok === true); ok(rowOf(r, 1).level === "warn"); ok(rowOf(r, 1).note.indexOf("钳到 10s") >= 0);
  ok(r.totalMs === 10000 + 200, "totalMs=" + r.totalMs);
  // 备注只此一条时不能留尾部分隔符（以前拼成 dl.warn + "；" 会多一个「；」）
  const n = rowOf(r, 1).note;
  ok(n === "延时 25s 超过上限，会被钳到 10s", "note=" + JSON.stringify(n));
});

t("delay 负数/非数字按 0 处理并 warn", () => {
  const a = H.macroDryRun({ name: "x", steps: [{ delay: -5 }] }, envOK);
  ok(rowOf(a, 1).note.indexOf("负延时") >= 0);
  const b = H.macroDryRun({ name: "x", steps: [{ delay: "abc" }] }, envOK);
  ok(rowOf(b, 1).note.indexOf("不是数字") >= 0);
  const c = H.macroDryDelay({ delay: 1500 });
  ok(c.ms === 1500 && c.warn === "");
});

t("steps 不是数组 / 顶层不是对象：err", () => {
  const a = H.macroDryRun({ name: "x", steps: "nope" }, envOK);
  ok(a.ok === false); ok(a.rows[0].text.indexOf("不是数组") >= 0);
  const b = H.macroDryRun([1, 2], envOK);
  ok(b.ok === false); ok(b.rows[0].text.indexOf("顶层") >= 0);
});

t("APP_ID_RE 与服务端口径一致", () => {
  ok(H.MACRO_APP_ID_RE_FE.test("com.foo.bar-baz_1"));
  ok(!H.MACRO_APP_ID_RE_FE.test("1com.foo"));
  ok(!H.MACRO_APP_ID_RE_FE.test("com foo"));
  ok(!H.MACRO_APP_ID_RE_FE.test(""));
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_DRYRUN_CASES_PASSED");
