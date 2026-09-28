#!/usr/bin/env node
/* 卡片折叠（第二十六轮）纯函数 harness：从 static/app.js 抽出 collapse 段原样执行并跑用例。
   Python 单测 tests/test_collapse.py 通过 subprocess 调它，让「状态机 / localStorage 反序列化 /
   序列化往返」有真行为证据，而非源码字符串断言。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== collapse:begin";
const END = "/* ===== collapse:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: collapse 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
function stripComments(x) {
  return x.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
const code = stripComments(seg);
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (code.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["COLLAPSE_MS", "COLLAPSE_KEY", "COLLAPSE_OPEN", "COLLAPSE_CLOSED",
                 "collapseAnimMs", "collapseStateOf", "parseCollapsed", "serializeCollapsed",
                 "collapseLabel"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

const KNOWN = ["kbCard", "favCard", "appsCard", "toolsCard"];

t("常量：折叠时长 180ms、可见性延迟等长（CSS --collapse-dur 同源）", () => {
  ok(H.COLLAPSE_MS === 180, "COLLAPSE_MS=" + H.COLLAPSE_MS);
  ok(H.COLLAPSE_KEY === "atv.collapsed.v1", "COLLAPSE_KEY=" + H.COLLAPSE_KEY);
  ok(H.COLLAPSE_OPEN === "open" && H.COLLAPSE_CLOSED === "closed", "状态常量");
});

t("collapseAnimMs：减少动效偏好取 0，否则 180ms", () => {
  ok(H.collapseAnimMs(true) === 0, "true -> 0");
  ok(H.collapseAnimMs(false) === 180, "false -> 180");
  ok(H.collapseAnimMs() === 180, "缺省 -> 180");
});

t("collapseStateOf：二态翻转 + forced 覆盖，非法 forced 视作未指定", () => {
  ok(H.collapseStateOf("open") === "closed", "open -> closed");
  ok(H.collapseStateOf("closed") === "open", "closed -> open");
  ok(H.collapseStateOf("open", "open") === "open", "forced 同值");
  ok(H.collapseStateOf("open", "closed") === "closed", "forced 覆盖 1");
  ok(H.collapseStateOf("closed", "open") === "open", "forced 覆盖 2");
  ok(H.collapseStateOf("closed", "closed") === "closed", "forced 覆盖 3");
  ok(H.collapseStateOf(undefined, "open") === "open", "缺省 current + forced");
  ok(H.collapseStateOf("garbage", "garbage") === "closed", "非法 forced 不得当值用");
  ok(H.collapseStateOf("garbage") === "closed", "非法 current 一律当 open 反转");
});

t("parseCollapsed：localStorage 是不可信输入，坏 JSON / 非数组 / 未知 id 都要兜住", () => {
  ok(JSON.stringify(H.parseCollapsed(null, KNOWN)) === "[]", "null");
  ok(JSON.stringify(H.parseCollapsed(undefined, KNOWN)) === "[]", "undefined");
  ok(JSON.stringify(H.parseCollapsed("", KNOWN)) === "[]", "空串");
  ok(JSON.stringify(H.parseCollapsed("not json", KNOWN)) === "[]", "坏 JSON");
  ok(JSON.stringify(H.parseCollapsed("{}", KNOWN)) === "[]", "对象不是数组");
  ok(JSON.stringify(H.parseCollapsed('"kbCard"', KNOWN)) === "[]", "裸字符串不是数组");
  ok(JSON.stringify(H.parseCollapsed('["kbCard","zzz"]', KNOWN)) === '["kbCard"]', "过滤未知 id");
  ok(JSON.stringify(H.parseCollapsed('["favCard","kbCard","favCard"]', KNOWN)) === '["favCard","kbCard"]', "去重 + 排序");
  ok(JSON.stringify(H.parseCollapsed('[1,{"a":1},null,"toolsCard"]', KNOWN)) === '["toolsCard"]', "非字符串项过滤");
  ok(JSON.stringify(H.parseCollapsed('["kbCard"]', [])) === "[]", "known 为空 = 全不认识");
});

t("serializeCollapsed：去重排序后可 JSON.parse，坏类型不入键", () => {
  ok(H.serializeCollapsed(["favCard", "kbCard", "favCard"]) === '["favCard","kbCard"]', "去重排序");
  ok(H.serializeCollapsed([]) === "[]", "空数组");
  ok(H.serializeCollapsed(null) === "[]", "null");
  ok(H.serializeCollapsed([1, null, {}, "kbCard"]) === '["kbCard"]', "过滤非字符串");
  ok(JSON.parse(H.serializeCollapsed(["kbCard"])).length === 1, "可被 JSON.parse");
});

t("往返：serialize -> parse 不掉项、不混入未知 id", () => {
  const ids = ["toolsCard", "kbCard", "kbCard", "appsCard"];
  const out = H.parseCollapsed(H.serializeCollapsed(ids), KNOWN);
  ok(JSON.stringify(out) === '["appsCard","kbCard","toolsCard"]', "round trip: " + out);
  const bad = H.parseCollapsed(H.serializeCollapsed(["kbCard", "ghost"]), KNOWN);
  ok(JSON.stringify(bad) === '["kbCard"]', "未知 id 在往返中被丢掉");
});

t("collapseLabel：按钮 aria-label 的动作词跟随当前态", () => {
  ok(H.collapseLabel("open") === "折叠", "open -> 折叠");
  ok(H.collapseLabel("closed") === "展开", "closed -> 展开");
  ok(H.collapseLabel("garbage") === "折叠", "其他值按 open 处理");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("NOT OK - " + name + " :: " + err.message); }
}
console.log(failed === 0 ? "ALL_COLLAPSE_CASES_PASSED" : "COLLAPSE_FAILURES=" + failed);
process.exit(failed === 0 ? 0 : 1);
