#!/usr/bin/env node
/* Wake-on-LAN 纯函数 harness：从 static/app.js 抽出 wake-on-lan 段原样执行并跑用例。
   Python 单测 tests/test_wol.py 通过 subprocess 调它，让 MAC 宽容解析 / IP 提取 /
   等待计划 / 缺失归因有真行为证据，而不只是源码字符串断言。改动段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== wake-on-lan:begin";
const END = "/* ===== wake-on-lan:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: wake-on-lan 段标记缺失或顺序错误");
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
  "WOL_WATCH_MS", "WOL_WATCH_MAX", "WOL_IP_MAX", "WOL_ASK_MIN_MS",
  "wolNormMac", "wolIpOf", "wolIpsFromStatus", "wolWatchPlan",
  "wolMissing", "wolMacShort", "wolSummary",
].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("常量：2.5s 探测 / 24 次上限 / 8 个 IP / 15s 最小重问间隔", () => {
  ok(H.WOL_WATCH_MS === 2500, "WATCH_MS=" + H.WOL_WATCH_MS);
  ok(H.WOL_WATCH_MAX === 24, "WATCH_MAX=" + H.WOL_WATCH_MAX);
  ok(H.WOL_IP_MAX === 8, "IP_MAX=" + H.WOL_IP_MAX);
  ok(H.WOL_ASK_MIN_MS === 15000, "ASK_MIN_MS=" + H.WOL_ASK_MIN_MS);
  ok(H.WOL_WATCH_MS * H.WOL_WATCH_MAX === 60000, "总等待应约 60s");
});

// 用户是从路由器后台手抄 MAC 的：什么格式都该收，位数不对则绝不猜（猜错会唤醒别人家电视）
t("wolNormMac：冒号 / 连字符 / 点分 / 无分隔 / 大小写混用全收", () => {
  ok(H.wolNormMac("aa:bb:cc:dd:ee:ff") === "aa:bb:cc:dd:ee:ff");
  ok(H.wolNormMac("AA:BB:CC:DD:EE:FF") === "aa:bb:cc:dd:ee:ff");
  ok(H.wolNormMac("aa-bb-cc-dd-ee-ff") === "aa:bb:cc:dd:ee:ff");
  ok(H.wolNormMac("AABBCCDDEEFF") === "aa:bb:cc:dd:ee:ff");
  ok(H.wolNormMac("aabb.ccdd.eeff") === "aa:bb:cc:dd:ee:ff");
  ok(H.wolNormMac("  aa:bb:cc:dd:ee:ff  ") === "aa:bb:cc:dd:ee:ff", "前后空白要吃掉");
  ok(H.wolNormMac("aA:bB-cC.dD:eeFF") === "aa:bb:cc:dd:ee:ff", "分隔符混用也收");
});

t("wolNormMac：位数不对 / 非十六进制 / 非字符串一律 null", () => {
  ok(H.wolNormMac("aa:bb:cc:dd:ee") === null, "11 位");
  ok(H.wolNormMac("aa:bb:cc:dd:ee:f") === null, "分组不全不猜位");
  ok(H.wolNormMac("aabbccddeeff00") === null, "14 位");
  ok(H.wolNormMac("zz:bb:cc:dd:ee:ff") === null, "非十六进制");
  ok(H.wolNormMac("aa:bb:cc:dd:ee:ff:00") === null, "7 组");
  ok(H.wolNormMac("") === null);
  ok(H.wolNormMac("   ") === null);
  ok(H.wolNormMac(null) === null, "null 不炸");
  ok(H.wolNormMac(undefined) === null);
  ok(H.wolNormMac(123456789012) === null, "数字不入参，别悄悄 toString");
  ok(H.wolNormMac({}) === null);
});

// adb 无线调试目标是 host:port，ARP 表里只有 IP
t("wolIpOf：剥端口、剥空白、非字符串兜底", () => {
  ok(H.wolIpOf("192.168.0.52:5555") === "192.168.0.52");
  ok(H.wolIpOf("192.168.0.52") === "192.168.0.52");
  ok(H.wolIpOf("  192.168.0.52:5555  ") === "192.168.0.52");
  ok(H.wolIpOf("192.168.0.52:5555 ") === "192.168.0.52");
  ok(H.wolIpOf("") === "");
  ok(H.wolIpOf(null) === "");
  ok(H.wolIpOf(undefined) === "");
  ok(H.wolIpOf(42) === "42");
});

t("wolIpsFromStatus：当前 + 最近，去重、限量、滤掉非 IP", () => {
  const s = { current: "192.168.0.52:5555", recent: ["192.168.0.7", "192.168.0.52", "10.0.0.3"] };
  ok(JSON.stringify(H.wolIpsFromStatus(s)) === JSON.stringify(["192.168.0.52", "192.168.0.7", "10.0.0.3"]),
     JSON.stringify(H.wolIpsFromStatus(s)));
  const atv = { cur_type: "appletv", current: "9f2c3b uuid", recent: [] };
  ok(H.wolIpsFromStatus(atv).length === 0, "Apple TV 的 current 是 uuid，没有点号");
  const many = { current: "1.1.1.1", recent: Array.from({ length: 20 }, (_, i) => "10.0.0." + i) };
  ok(H.wolIpsFromStatus(many).length === H.WOL_IP_MAX, "限量 IP_MAX");
  ok(H.wolIpsFromStatus(null).length === 0, "null 不炸");
  ok(H.wolIpsFromStatus({}).length === 0, "空快照不炸");
  ok(H.wolIpsFromStatus({ current: "nope", recent: "not-an-array" }).length === 0);
});

// 等待计划：online 优先于 attempt，别为了跑满次数把已经上线的设备再晾 30 秒
t("wolWatchPlan：online 即停；attempt 用满报超时；否则继续等", () => {
  ok(JSON.stringify(H.wolWatchPlan(0, true)) === JSON.stringify({ stop: true, why: "online" }));
  ok(JSON.stringify(H.wolWatchPlan(1, true)) === JSON.stringify({ stop: true, why: "online" }));
  ok(JSON.stringify(H.wolWatchPlan(23, true)) === JSON.stringify({ stop: true, why: "online" }),
     "最后一次探测赶上上线也算 online，不是 timeout");
  ok(JSON.stringify(H.wolWatchPlan(H.WOL_WATCH_MAX, true)) === JSON.stringify({ stop: true, why: "online" }));
  ok(JSON.stringify(H.wolWatchPlan(0, false)) === JSON.stringify({ stop: false, why: "waiting" }));
  ok(H.wolWatchPlan(1, false).stop === false);
  ok(H.wolWatchPlan(H.WOL_WATCH_MAX - 1, false).stop === false, "差一次还没满");
  ok(JSON.stringify(H.wolWatchPlan(H.WOL_WATCH_MAX, false)) === JSON.stringify({ stop: true, why: "timeout" }));
  ok(H.wolWatchPlan(H.WOL_WATCH_MAX + 5, false).why === "timeout", "超限也按超时收尾");
});

// 缺失归因：问了却没 MAC，和「根本没有可问的」是两回事，文案必须能分开
t("wolMissing：列出问了却没查到 MAC 的 IP", () => {
  const asked = ["1.1.1.1", "2.2.2.2", "3.3.3.3"];
  const found = [{ ip: "2.2.2.2", mac: "aa:bb:cc:dd:ee:ff" }];
  ok(JSON.stringify(H.wolMissing(asked, found)) === JSON.stringify(["1.1.1.1", "3.3.3.3"]));
  ok(JSON.stringify(H.wolMissing(asked, [])) === JSON.stringify(asked));
  ok(H.wolMissing(asked, found).length === 2);
  ok(JSON.stringify(H.wolMissing([], found)) === "[]", "没问就没缺口");
  ok(H.wolMissing(["1.1.1.1"], []).length === 1, "空结果不是缺口消失");
  ok(H.wolMissing(null, found).length === 0, "null 不炸");
  ok(H.wolMissing(asked, null).length === 3, "结果缺失时全算没查到");
  ok(H.wolMissing(asked, [null, {}, { ip: "1.1.1.1" }]).length === 2, "坏条目被过滤");
});

t("wolMacShort：前三组 + 省略 + 后两组；坏输入原样返回", () => {
  ok(H.wolMacShort("aa:bb:cc:dd:ee:ff") === "aa:bb:cc…ee:ff", H.wolMacShort("aa:bb:cc:dd:ee:ff"));
  ok(H.wolMacShort("00:11:22:33:44:55") === "00:11:22…44:55");
  ok(H.wolMacShort("aa:bb") === "aa:bb", "组数不够不硬凑");
  ok(H.wolMacShort("") === "");
  ok(H.wolMacShort(null) === "");
  ok(H.wolMacShort(undefined) === "");
});

t("wolSummary：发现 / 有 MAC / 缺口三个数分开报", () => {
  const s1 = H.wolSummary([{ ip: "1.1.1.1", mac: "aa:bb:cc:dd:ee:ff" }, { ip: "2.2.2.2", mac: "" }]);
  ok(s1.total === 2 && s1.withMac === 1 && s1.missing === 1, JSON.stringify(s1));
  ok(H.wolSummary([]).total === 0 && H.wolSummary([]).withMac === 0);
  ok(H.wolSummary(null).total === 0, "null 不炸");
  ok(H.wolSummary(undefined).missing === 0);
  const s2 = H.wolSummary([null, {}, { ip: "3.3.3.3", mac: "aa:bb:cc:dd:ee:ff" }]);
  ok(s2.total === 3 && s2.withMac === 1 && s2.missing === 2, JSON.stringify(s2));
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_WOL_CASES_PASSED");

