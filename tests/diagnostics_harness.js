#!/usr/bin/env node
/* 一键体检报告（第三十四轮）纯函数 harness：从 static/app.js 抽出 diagnostics 段原样执行并跑用例。
   Python 单测 tests/test_diagnostics.py 通过 subprocess 调它——报告文本的拼装因此有真行为证据，
   不只是源码字符串断言。改动 app.js 里段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== diagnostics:begin";
const END = "/* ===== diagnostics:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: diagnostics 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
for (const banned of ["document.", "localStorage", "fetch(", "$(", "innerHTML", "setInterval", "navigator."]) {
  if (seg.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const factory = new Function(seg + NL + "return { diagYes, diagImeLine, diagReportText };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

// 形状与 /api/diagnostics 的真实返回一一对应（键名从 server.py make_diagnostics 抄来）
const REAL = {
  version: "1.34.0",
  embedded: false,
  platform: { system: "Darwin", release: "27.0.0", machine: "arm64", python: "3.14.7" },
  adb: {
    found: true, path: "/opt/homebrew/bin/adb",
    version: "Android Debug Bridge version 1.0.41",
    shell_alive: true, devices_cache_ttl: 1.5,
    devices: [
      { serial: "192.168.1.5:5555", state: "device" },
      { serial: "emulator-5554", state: "offline" },
    ],
  },
  appletv: { pyatv_available: true, paired_count: 1, connected: false },
  current: { type: "android", target: "192.168.1.5:5555", state: "device", recent_android_count: 3 },
  ime: {
    ime: "com.android.adbkeyboard/.AdbIME",
    installed: true, enabled: true, current: true,
    default_ime: "com.android.adbkeyboard/.AdbIME",
    apk: "https://example.com/adbkeyboard.apk",
    apk_a16: "https://example.com/a16.apk",
  },
  auth: { token_enabled: true, mode: "lan", loopback_hosts: ["127.0.0.1", "::1"] },
  mdns: { dns_sd_available: true },
};

t("全字段报告：十行拼齐、顺序稳定", () => {
  const lines = H.diagReportText(REAL).split(NL);
  ok(lines.length === 10, "lines=" + lines.length);
  ok(lines[0] === "ATV Remote 诊断报告 v1.34.0 · Darwin 27.0.0 arm64 · Python 3.14.7 · 内嵌引擎 否", lines[0]);
  ok(lines[1] === "adb: 已找到 /opt/homebrew/bin/adb · Android Debug Bridge version 1.0.41 · 常驻 shell 存活 · 设备缓存 1.5s", lines[1]);
  ok(lines[2] === "设备: 2 台", lines[2]);
  ok(lines[3] === "  - 192.168.1.5:5555 [device]", lines[3]);
  ok(lines[4] === "  - emulator-5554 [offline]", lines[4]);
  ok(lines[5] === "当前: Android TV 192.168.1.5:5555 [device] · 最近 Android 设备 3 台", lines[5]);
  ok(lines[6] === "中文输入: 已装 是 / 已启用 是 / 当前 是（com.android.adbkeyboard/.AdbIME）", lines[6]);
  ok(lines[7] === "Apple TV: pyatv 可用 · 已配对 1 台 · 已连接 否", lines[7]);
  ok(lines[8] === "局域网令牌: 已开启（lan）", lines[8]);
  ok(lines[9] === "mDNS: dns-sd 可用", lines[9]);
});

t("adb 扫不到设备：明说一台都没扫到，别让读者以为漏渲染", () => {
  const d = JSON.parse(JSON.stringify(REAL));
  d.adb.found = false;
  d.adb.devices = [];
  d.adb.version = "";
  // 找不到 adb 二进制时后端根本没起过常驻 shell，前端回报的就是「无」
  d.adb.shell_alive = false;
  const lines = H.diagReportText(d).split(NL);
  ok(lines[1] === "adb: 未找到 · 版本未知 · 常驻 shell 无 · 设备缓存 1.5s", lines[1]);
  ok(lines[2] === "设备: 0 台（一台都没扫到）", lines[2]);
  ok(H.diagReportText(d).indexOf(NL + "  - ") < 0, "没有设备时不该出现设备行");
});

t("adb 在、shell 也活着，但零设备：同样明说一台都没扫到", () => {
  // 这两件事互不相干：常驻 shell 活着仍然可能一台设备都没扫到（未授权 / 授权弹窗 / 网段不同）
  const d = JSON.parse(JSON.stringify(REAL));
  d.adb.devices = [];
  const lines = H.diagReportText(d).split(NL);
  ok(lines[1].indexOf("常驻 shell 存活") >= 0 && lines[1].indexOf("已找到") >= 0, lines[1]);
  ok(lines[2] === "设备: 0 台（一台都没扫到）", lines[2]);
});

t("当前是 Apple TV：按 type 渲染前缀（state 里这叫道 id）", () => {
  const d = JSON.parse(JSON.stringify(REAL));
  d.current = { type: "appletv", target: "4F5E:ATV", state: null, recent_android_count: 0 };
  d.ime = null;
  const lines = H.diagReportText(d).split(NL);
  ok(lines[5] === "当前: Apple TV 4F5E:ATV [未知] · 最近 Android 设备 0 台", lines[5]);
  ok(lines[6] === "中文输入: 未查询（当前不是 Android TV 或 adb 不可用）", lines[6]);
});

t("IME 三态不全：把「没启用」说清楚，这是中文打不进去的第一嫌疑", () => {
  const d = JSON.parse(JSON.stringify(REAL));
  d.ime = { installed: true, enabled: false, current: false, default_ime: "com.google.android.tv/.ime" };
  const lines = H.diagReportText(d).split(NL);
  ok(lines[6] === "中文输入: 已装 是 / 已启用 否 / 当前 否（com.google.android.tv/.ime）", lines[6]);
});

t("令牌未开启 / mDNS 不可用也各有一行", () => {
  const d = JSON.parse(JSON.stringify(REAL));
  d.auth = { token_enabled: false, mode: "off", loopback_hosts: [] };
  d.mdns = { dns_sd_available: false };
  const lines = H.diagReportText(d).split(NL);
  ok(lines[8] === "局域网令牌: 未开启", lines[8]);
  ok(lines[9] === "mDNS: dns-sd 不可用", lines[9]);
});

t("空 payload / null / undefined 不炸，且明确说「未连接」", () => {
  for (const bad of [null, undefined, {}, { adb: null, current: null, appletv: null }]) {
    const txt = H.diagReportText(bad);
    ok(txt.indexOf("未连接") >= 0, "缺未连接: " + txt.slice(0, 40));
    ok(txt.indexOf("未找到") >= 0, "缺未找到");
    ok(txt.indexOf("NaN") < 0 && txt.indexOf("undefined") < 0 && txt.indexOf("null") < 0,
       "空值漏成了字面量: " + txt);
    ok(txt.indexOf("· ? ·") >= 0 || txt.indexOf("?") >= 0, "平台缺失要有占位");
  }
});

t("敏感键混进 payload 也漏不出去：纯文本化只读白名单键", () => {
  const d = JSON.parse(JSON.stringify(REAL));
  d.token = "SUPER_SECRET_TOKEN";
  d.credentials = "MRP_CREDS_BLOB";
  d.extra = { password: "hunter2", pairing: "PAIR_BLOB" };
  const txt = H.diagReportText(d);
  for (const secret of ["SUPER_SECRET_TOKEN", "MRP_CREDS_BLOB", "hunter2", "PAIR_BLOB"]) {
    ok(txt.indexOf(secret) < 0, "泄露了 " + secret);
  }
});

t("diagYes 真值表：1 / 非空串为是，0 / 空 / undefined 为否", () => {
  ok(H.diagYes(1) === "是" && H.diagYes("x") === "是" && H.diagYes(true) === "是");
  ok(H.diagYes(0) === "否" && H.diagYes("") === "否" && H.diagYes(undefined) === "否" && H.diagYes(null) === "否");
});

t("diagImeLine 缺失即「未查询」，不猜", () => {
  ok(H.diagImeLine(null).indexOf("未查询") >= 0);
  ok(H.diagImeLine({}).indexOf("未知") >= 0);
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_DIAG_CASES_PASSED");

