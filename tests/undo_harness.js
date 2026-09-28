#!/usr/bin/env node
/* 撤销（第二十七轮）纯函数 harness：从 static/app.js 抽出 undo 段原样执行并跑用例。
   Python 单测 tests/test_undo.py 通过 subprocess 调它，让「过期判定 / 同 id 去重 /
   二次撤销 / 上限截断 / label 不参与匹配」有真行为证据，而不是源码字符串断言。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== undo:begin";
const END = "/* ===== undo:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: undo 段标记缺失或顺序错误");
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
const EXPORTS = ["UNDO_MS", "UNDO_STACK_MAX", "undoLive", "undoPush", "undoTake"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
function eq(a, b, msg) { if (a !== b) throw new Error((msg || "eq") + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); }

const T0 = 1000000;
const mk = (id, at, label) => ({ id: id, label: label || ("label" + id), at: at, undo: function () {} });
const ids = (list) => list.map((x) => x.id).join(",");

/* 1. 过期判定：5999ms 留着，恰好 6000ms 与 6001ms 都算过期 */
t("undoLive window: 5999ms lives, 6000ms is the boundary", () => {
  const now = T0 + 100000;
  const list = [mk(1, now - 5999), mk(2, now - 6000), mk(3, now - 6001), mk(4, now)];
  eq(ids(H.undoLive(list, now)), "1,4");
});

/* 2. 脏输入不炸：非数组当空栈，缺 at / 非对象元素一律视作过期 */
t("undoLive tolerates junk", () => {
  eq(H.undoLive(null, T0).length, 0, "null 当空栈");
  eq(H.undoLive(undefined, T0).length, 0, "undefined 当空栈");
  eq(H.undoLive("nope", T0).length, 0, "字符串当空栈");
  eq(H.undoLive([{ id: 9 }, null, 42, { id: 8, at: T0 }], T0).length, 1, "缺 at 或非对象一律过期");
});

/* 3. 入栈：新的排最前、顺手 prune 过期、同 id 先去重再插；旧数组引用不动 */
t("undoPush ordering, dedupe, prune; input untouched", () => {
  const now = T0;
  const old = [mk(1, now - 100), mk(2, now - 99999)];
  const one = H.undoPush(old, mk(5, now), now);
  eq(ids(one), "5,1", "过期的 2 被 prune");
  eq(ids(old), "1,2", "旧数组不被修改");
  eq(ids(H.undoPush(one, mk(1, now), now)), "1,5", "同 id 先去重再插到最前");
});

/* 4. 上限：连续压 7 条只留最新的 4 条，被挤掉的是最旧的 */
t("undoPush caps at UNDO_STACK_MAX dropping oldest", () => {
  const now = T0;
  let stack = [];
  for (let i = 1; i <= H.UNDO_STACK_MAX + 3; i++) stack = H.undoPush(stack, mk(i, now), now);
  eq(stack.length, H.UNDO_STACK_MAX, "长度封顶");
  eq(ids(stack), [7, 6, 5, 4].join(","), "留下最新 4 条");
});

/* 5. 命中：返回 { entry, rest }，rest 已摘掉该条，原数组不动 */
t("undoTake hit returns entry plus rest without it", () => {
  const now = T0 + 5000;
  const list = [mk(7, now - 100), mk(8, now - 200)];
  const hit = H.undoTake(list, 7, now);
  ok(hit && hit.entry, "必须命中");
  eq(hit.entry.id, 7);
  eq(ids(hit.rest), "8");
  eq(ids(list), "7,8", "原数组不被修改");
});

/* 6. 不命中：过期、未知 id、以及撤过一次之后的第二次 */
t("undoTake null for expired, unknown, second take", () => {
  const now = T0 + 5000;
  const list = [mk(7, now - 100), mk(6, now - 200), mk(5, now - 60000)];
  eq(H.undoTake(list, 5, now), null, "过期条目不命中");
  eq(H.undoTake(list, 999, now), null, "未知 id 不命中");
  const first = H.undoTake(list, 7, now);
  eq(ids(first.rest), "6", "命中后 rest 去掉该条");
  eq(H.undoTake(first.rest, 7, now), null, "同一次撤销不能点两次");
});

/* 7. label 只是文案：两条同 label 的条目靠 id 区分 */
t("label is inert for matching", () => {
  const now = T0 + 10;
  const list = [mk(1, now, "same"), mk(2, now, "same")];
  const hit = H.undoTake(list, 2, now);
  eq(hit.entry.id, 2, "同 label 也按 id 命中");
  eq(hit.entry.label, "same", "label 原样带回只作展示");
  eq(ids(H.undoPush(list, mk(3, now, "same"), now)), "3,1,2", "同 label 不影响入栈");
});

/* 8. 常量有语义：反悔窗口不能短，栈上限要盖住同屏 3 条通知 */
t("constants are sane", () => {
  ok(H.UNDO_MS >= 3000, "反悔窗口不能短于 3s");
  ok(H.UNDO_STACK_MAX >= 3, "栈上限至少要盖住同屏通知数");
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
console.log(failed === 0 ? "ALL_UNDO_CASES_PASSED" : ("UNDO_CASES_FAILED=" + failed));
process.exit(failed === 0 ? 0 : 1);

