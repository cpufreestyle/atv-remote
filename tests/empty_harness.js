#!/usr/bin/env node
/* 空状态（第二十八轮）纯函数 harness：从 static/app.js 抽出 empty 段原样执行并跑用例。
   Python 单测 tests/test_empty.py 通过 subprocess 调它，让「判空优先级 / 文案查表 /
   动作键 / 节流签名」有真行为证据，而不是源码字符串断言。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== empty:begin";
const END = "/* ===== empty:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: empty 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
function stripComments(x) {
  return x.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
const code = stripComments(seg);
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$(", "Date.now", "new Date"]) {
  if (code.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["EMPTY_NONE", "emptyKind", "emptyCopy", "emptySig"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
function eq(a, b, msg) { if (a !== b) throw new Error((msg || "eq") + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); }

/* 1. 正连着：chips 不空，不该拿空状态挡眼 */
t("emptyKind: connected current hides empty state", () => {
  eq(H.emptyKind({ recent: [], devices: [], current: "192.168.1.50", adb_found: true }), null,
     "连着就不该空态");
});

/* 2. 有在线设备也算不空（观看视角没 current 时） */
t("emptyKind: online device hides empty state", () => {
  eq(H.emptyKind({ recent: ["a"], devices: [{ ip: "a" }], current: "", adb_found: true }), null);
});

/* 3. 连过但现在不在线 = offline */
t("emptyKind: recent but all offline is offline kind", () => {
  eq(H.emptyKind({ recent: ["192.168.1.50"], devices: [], current: "", adb_found: true }),
     "offline", "连过的设备全掉线要说 offline");
});

/* 4. 从没连过 + 有 adb = 首次引导 */
t("emptyKind: empty history with adb is intro", () => {
  eq(H.emptyKind({ recent: [], devices: [], current: "", adb_found: true }), "intro");
});

/* 5. 没 adb 压过 offline：先讲怎么才能遥控，再说重连 */
t("emptyKind: missing adb beats offline", () => {
  eq(H.emptyKind({ recent: ["192.168.1.50"], devices: [], current: "", adb_found: false }),
     "noadb", "没 adb 时报 noadb");
  eq(H.emptyKind({ recent: [], devices: [], current: "", adb_found: false }), "noadb");
});

/* 6. 脏输入不炸：recent / devices 缺省当空数组，adb_found 缺省当没有 */
t("emptyKind: missing fields tolerated", () => {
  eq(H.emptyKind({ adb_found: true }), "intro", "缺省 recent/devices 视作从没连过");
  eq(H.emptyKind({ adb_found: false }), "noadb", "没 adb 优先");
  eq(H.emptyKind({}), "noadb", "全缺省走 noadb（adb_found 假）");
});

/* 7. EMPTY_NONE 就是 null：调用方 === 判断 */
t("EMPTY_NONE is null", () => { eq(H.EMPTY_NONE, null); });

/* 8. intro 文案：一句解释 + 主动作扫描 + 次动作聚焦输入框 */
t("emptyCopy intro has valid CTA pair", () => {
  const c = H.emptyCopy("intro", []);
  ok(c && c.title && c.desc, "intro 要有标题和解释");
  eq(c.act, "scan", "主动作是扫描");
  eq(c.act2, "focus", "次动作是手输 IP");
  ok(c.cta && c.cta2, "intro 两个按钮都要有文案");
});

/* 9. offline 文案：台数写进标题——空状态的可信度靠具体数字 */
t("emptyCopy offline counts devices in title", () => {
  eq(H.emptyCopy("offline", ["a"]).title.indexOf("1"), 0, "台数在标题开头");
  ok(H.emptyCopy("offline", ["a", "b", "c"]).title.indexOf("3") >= 0, "3 台要说 3");
  eq(H.emptyCopy("offline", ["a"]).act, "reconnect", "主动作是重连");
  eq(H.emptyCopy("offline", ["a"]).act2, "scan", "次动作是扫描");
});

/* 10. noadb 没有可执行下一步：只能去装 adb */
t("emptyCopy noadb has no CTA", () => {
  const c = H.emptyCopy("noadb", []);
  ok(c && c.title && c.desc, "noadb 要有解释");
  eq(c.cta, null); eq(c.act, null); eq(c.cta2, null); eq(c.act2, null);
});

/* 11. 未知 kind 返回 null，调用方据此不渲染 */
t("emptyCopy unknown kind returns null", () => {
  eq(H.emptyCopy("bogus", ["a"]), null);
  eq(H.emptyCopy(null, []), null);
});

/* 12. 三态文案都不含 Markup：最终走 textContent，源头也别埋 < */
t("no copy contains markup", () => {
  for (const kind of ["intro", "offline", "noadb"]) {
    for (const rec of [[], ["a", "b"]]) {
      const c = H.emptyCopy(kind, rec);
      const all = c.title + c.desc + (c.cta || "") + (c.cta2 || "");
      ok(all.indexOf("<") < 0, kind + " 文案不许含 <");
    }
  }
});

/* 13. 节流签名：kind 变必变 */
t("emptySig changes with kind", () => {
  ok(H.emptySig("intro", 0) !== H.emptySig("offline", 0), "kind 不同签名必须不同");
});

/* 14. 台数变必变：offline 标题带数字，不重建会显示旧台数 */
t("emptySig changes with count", () => {
  ok(H.emptySig("offline", 2) !== H.emptySig("offline", 3), "台数不同签名必须不同");
});

/* 15. 同输入同签名：8s 轮询反复进来不能重建打断 hover / 焦点 */
t("emptySig stable for same input", () => {
  eq(H.emptySig("offline", 3), H.emptySig("offline", 3));
});

/* 16. emptyCopy 对脏 recent 也稳：非数组当 0 台 */
t("emptyCopy tolerates junk recent", () => {
  ok(H.emptyCopy("offline", null).title.indexOf("0") >= 0, "非数组当 0 台");
  ok(H.emptyCopy("intro", "nope").title, "字符串 recent 不炸");
});

for (const [name, fn] of cases) {
  try {
    fn();
    console.log("ok   - " + name);
  } catch (err) {
    failed++;
    console.log("FAIL - " + name + "\n       " + err.message);
  }
}
console.log(failed === 0 ? "ALL_EMPTY_CASES_PASSED" : ("EMPTY_CASES_FAILED=" + failed));
process.exit(failed === 0 ? 0 : 1);
