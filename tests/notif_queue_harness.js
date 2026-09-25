#!/usr/bin/env node
/* 通知队列纯函数 harness：从 static/app.js 抽出 notif-queue 段原样执行并跑用例。
   Python 单测 tests/test_notif_queue.py 通过 subprocess 调它，级别判定 / 同屏条数 / 历史合并与
   截断 / 未读数因此有真行为证据，而不只是源码字符串断言。改动段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== notif-queue:begin";
const END = "/* ===== notif-queue:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: notif-queue 段标记缺失或顺序错误");
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
const EXPORTS = [
  "NOTIF_MAX_VISIBLE", "NOTIF_DUR", "NOTIF_LEVELS", "NOTIF_HISTORY_KEY", "NOTIF_SEEN_KEY",
  "NOTIF_HISTORY_MAX", "NOTIF_COALESCE_MS", "NOTIF_MSG_MAX",
  "notifNormLevel", "notifLevel", "notifDur", "notifPlan", "notifCoalesce",
  "notifPushHistory", "notifUnread", "notifFmtTime", "notifSanitizeHistory",
].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("常量：同屏 3 条 / 三态级别 / key 命名 / 合并窗口 / 历史上限", () => {
  ok(H.NOTIF_MAX_VISIBLE === 3, "VISIBLE=" + H.NOTIF_MAX_VISIBLE);
  ok(H.NOTIF_HISTORY_MAX === 50, "HISTORY_MAX=" + H.NOTIF_HISTORY_MAX);
  ok(H.NOTIF_COALESCE_MS === 1200, "COALESCE_MS=" + H.NOTIF_COALESCE_MS);
  ok(H.NOTIF_MSG_MAX === 200, "MSG_MAX=" + H.NOTIF_MSG_MAX);
  ok(H.NOTIF_HISTORY_KEY === "atv.notif.history", "HISTORY_KEY=" + H.NOTIF_HISTORY_KEY);
  ok(H.NOTIF_SEEN_KEY === "atv.notif.seen", "SEEN_KEY=" + H.NOTIF_SEEN_KEY);
  ok(JSON.stringify(H.NOTIF_LEVELS) === JSON.stringify(["ok", "info", "err"]),
     "LEVELS=" + JSON.stringify(H.NOTIF_LEVELS));
  ok(JSON.stringify(H.NOTIF_DUR) === JSON.stringify({ ok: 2600, info: 3400, err: 7000 }),
     "DUR=" + JSON.stringify(H.NOTIF_DUR));
});

// 级别由文案推导：40+ 个调用点不该为了配色逐个传第三个参数。
t("notifLevel：isInfo 优先判 info，其余按内容判成功 / 失败", () => {
  ok(H.notifLevel("随便什么", true) === "info");
  ok(H.notifLevel("✓ 成功", true) === "info", "isInfo=true 时不再看内容");
  ok(H.notifLevel("连接失败", false) === "err");
  ok(H.notifLevel("配对失败", false) === "err");
  ok(H.notifLevel("命令错误", false) === "err");
  ok(H.notifLevel("⚠ 设备离线", false) === "err");
  ok(H.notifLevel("✕ 发送失败", false) === "err");
  ok(H.notifLevel("✓ 已连接 Apple TV", false) === "ok");
  ok(H.notifLevel("✅ 截图已保存", false) === "ok");
  ok(H.notifLevel("打印成功", false) === "ok");
  ok(H.notifLevel("已切换到客厅电视", false) === "ok");
  ok(H.notifLevel("音量 20%", false) === "err", "无关键词默认 err，宁可红一点都不漏报");
  ok(H.notifLevel(null, false) === "err", "null 不炸");
});

t("notifNormLevel：未知级别归 err，合法级别原样", () => {
  ok(H.notifNormLevel("ok") === "ok");
  ok(H.notifNormLevel("info") === "info");
  ok(H.notifNormLevel("err") === "err");
  ok(H.notifNormLevel("warn") === "err");
  ok(H.notifNormLevel(undefined) === "err");
  ok(H.notifNormLevel(42) === "err");
});

t("notifDur：级别映射停留时长，坏输入落 7000", () => {
  ok(H.notifDur("ok") === 2600);
  ok(H.notifDur("info") === 3400);
  ok(H.notifDur("err") === 7000);
  ok(H.notifDur("warn") === 7000, "未知级别按 err 停留，别让重要提示一闪而过");
  ok(H.notifDur(undefined) === 7000);
});

t("notifPlan：6 条取最新 3 条（新→旧），hidden=3", () => {
  const items = [1, 2, 3, 4, 5, 6].map((n) => ({ seq: n }));
  const p = H.notifPlan(items, H.NOTIF_MAX_VISIBLE);
  ok(p.visible.length === 3, "visible=" + p.visible.length);
  ok(p.visible[0].seq === 6 && p.visible[1].seq === 5 && p.visible[2].seq === 4,
     JSON.stringify(p.visible));
  ok(p.hidden === 3, "hidden=" + p.hidden);
});

t("notifPlan：空数组 / 不足上限 / 非法 max / 非数组", () => {
  const empty = H.notifPlan([], 3);
  ok(empty.visible.length === 0 && empty.hidden === 0, JSON.stringify(empty));
  const few = H.notifPlan([{ seq: 1 }, { seq: 2 }], 3);
  ok(few.visible.length === 2 && few.hidden === 0, JSON.stringify(few));
  ok(few.visible[0].seq === 2, "不足上限也按新→旧");
  const dflt = H.notifPlan([1, 2, 3, 4].map((n) => ({ seq: n })), "bad");
  ok(dflt.visible.length === 3 && dflt.hidden === 1, "非法 max 回落到常量 3");
  ok(H.notifPlan(null, 3).visible.length === 0, "null 不炸");
  ok(H.notifPlan(undefined).hidden === 0, "undefined 不炸");
});

t("notifPlan：不改入参（slice 语义）", () => {
  const items = [{ seq: 1 }, { seq: 2 }, { seq: 3 }, { seq: 4 }];
  const before = JSON.stringify(items);
  H.notifPlan(items, 2);
  ok(JSON.stringify(items) === before, "入参被就地翻转 / 改动");
});

t("notifCoalesce：同消息同级别且 1.2s 内才合并", () => {
  const a = { msg: "hello", level: "ok", ts: 1000 };
  ok(H.notifCoalesce(a, { msg: "hello", level: "ok", ts: 2200 }) === true, "1.2s 边界含在内");
  ok(H.notifCoalesce(a, { msg: "hello", level: "ok", ts: 2201 }) === false, "超出窗口不合并");
  ok(H.notifCoalesce(a, { msg: "other", level: "ok", ts: 1100 }) === false, "消息不同");
  ok(H.notifCoalesce(a, { msg: "hello", level: "err", ts: 1100 }) === false, "级别不同");
  ok(H.notifCoalesce(null, a) === false && H.notifCoalesce(a, null) === false, "缺项不合并");
});

t("notifPushHistory：最新在前，旧引用不被就地改", () => {
  const base = [{ msg: "old", level: "ok", ts: 100 }];
  const next = H.notifPushHistory(base, { msg: "new", level: "err", ts: 99999 });
  ok(base.length === 1 && base[0].msg === "old", "入参被改");
  ok(next.length === 2 && next[0].msg === "new", "最新应在最前");
  ok(next[1].msg === "old", "旧条目在后");
});

t("notifPushHistory：合并时刷新为较新那条，不插重复行", () => {
  const a = { msg: "same", level: "ok", ts: 1000 };
  const b = { msg: "same", level: "ok", ts: 1500 };
  const h = H.notifPushHistory([a], b);
  ok(h.length === 1, "重复消息被合并，len=" + h.length);
  ok(h[0] === b, "合并后应是较新那条（时间戳刷新）");
  ok(a.ts === 1000, "旧对象未被改");
});

t("notifPushHistory：超过 50 条截断且保留最新", () => {
  let list = [];
  for (let i = 0; i < 60; i++) {
    list = H.notifPushHistory(list, { msg: "m" + i, level: "ok", ts: i * 10000 });
  }
  ok(list.length === 50, "len=" + list.length);
  ok(list[0].msg === "m59", "最新在前，最旧被挤掉");
  ok(list[49].msg === "m10", "m0..m9 被挤掉，最老保留 " + list[49].msg);
  ok(list[0].ts > list[49].ts, "顺序确为新→旧");
});

t("notifPushHistory：非数组入参兜底为空历史", () => {
  ok(H.notifPushHistory(null, { msg: "x", level: "ok", ts: 1 }).length === 1);
  ok(H.notifPushHistory(undefined, { msg: "x", level: "ok", ts: 1 })[0].msg === "x");
});

t("notifUnread：只数比 lastSeen 新的", () => {
  const list = [{ ts: 100 }, { ts: 200 }, { ts: 300 }];
  ok(H.notifUnread(list, 250) === 1, JSON.stringify(list));
  ok(H.notifUnread(list, 0) === 3);
  ok(H.notifUnread(list, 300) === 0, "等于 seen 不算未读");
  ok(H.notifUnread(list, -5) === 3, "坏 seen 归 0");
  ok(H.notifUnread(list, "nope") === 3, "非数字 seen 归 0");
  ok(H.notifUnread(null, 0) === 0, "null 不炸");
  ok(H.notifUnread([null, { ts: 200 }], 100) === 1, "坏条目被过滤");
  ok(H.notifUnread([{ ts: 0 }], 0) === 0, "ts=0 不算未读");
});

t("notifFmtTime：hh:mm:ss 补零，坏输入回落 now", () => {
  const ts = new Date(2026, 8, 26, 9, 5, 3).getTime();
  ok(H.notifFmtTime(ts) === "09:05:03", H.notifFmtTime(ts));
  ok(H.notifFmtTime(0).length === 8, "ts=0 走 now");
  ok(H.notifFmtTime(null).length === 8, "null 走 now");
  ok(H.notifFmtTime(undefined).indexOf(":") > 0, "undefined 走 now");
});

// localStorage 里的历史是不可信输入：一条坏数据不该让整个通知中心打不开。
t("notifSanitizeHistory：过滤坏数据，未知 level 归 err，坏 ts 归 0", () => {
  const raw = [
    { msg: "good", level: "ok", ts: 100 },
    null,
    "not-an-object",
    { msg: "", level: "ok", ts: 100 },
    { msg: "   ", level: "ok", ts: 100 },
    { level: "err", ts: 100 },
    { msg: 42, level: "ok", ts: 100 },
    { msg: "unknown-level", level: "verbose", ts: 100 },
    { msg: "bad-ts", level: "err" },
  ];
  const cl = H.notifSanitizeHistory(raw, 50);
  ok(cl.length === 3, "len=" + cl.length);
  ok(cl[0].msg === "good" && cl[0].level === "ok" && cl[0].ts === 100, JSON.stringify(cl[0]));
  ok(cl[1].level === "err", "未知 level 归一为 err: " + cl[1].level);
  ok(cl[2].ts === 0, "缺失 ts 归 0: " + cl[2].ts);
  ok(cl.every((x) => typeof x.msg === "string" && typeof x.level === "string" && typeof x.ts === "number"),
     "字段类型必须收敛，否则渲染时埋雷");
});

t("notifSanitizeHistory：超长消息截 200、超量截 50、非数组兜底", () => {
  const long = H.notifSanitizeHistory([{ msg: "x".repeat(500), level: "ok", ts: 1 }], 50);
  ok(long.length === 1 && long[0].msg.length === 200, "msg len=" + long[0].msg.length);
  const many = H.notifSanitizeHistory(
    Array.from({ length: 60 }, (_, i) => ({ msg: "m" + i, level: "ok", ts: i + 1 })), 50);
  ok(many.length === 50, "many=" + many.length);
  ok(H.notifSanitizeHistory(null, 50).length === 0, "null 不炸");
  ok(H.notifSanitizeHistory(undefined).length === 0, "undefined 不炸");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_NOTIF_CASES_PASSED")