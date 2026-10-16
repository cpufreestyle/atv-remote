#!/usr/bin/env node
/* Apple TV 三档能力的命令面板出口 harness：把 palCommands() 真跑起来（I23）。

   动机：文本追加/整段替换、下一个按键双击、长按五项在页面上有按钮，但键盘优先的
   命令面板（Cmd/Ctrl+K）与语音意图层完全没有入口——熟手敲几个字母到不了这些新功能。
   只有「按钮存在」的证据不够：必须证明面板点下去落在同一个出口、且不破坏 Android 用户。

   这里用 with(sandbox) 造一套桩（$ 记录 click 的选择器、holdSend 记录 code），让 push
   出来的命令原样执行，断言四件事：
     1) 门禁：只有 curType === "appletv" 才有这 8 条，Android 用户面板一条都不多；
     2) 幂等：文本语义走「指定目标态」而非翻转，重复执行不产生多余 click
        （语音说「替换」时若当前已是替换态，翻转会切回追加，与用户说的正好相反）；
     3) 单一状态源：执行体是点击页面按钮 / 调 holdSend，不自己另发一套请求；
     4) label / hint 反映当前态，用户看得见自己会切到哪。

   依赖是显式列举的，漏一个就 ReferenceError——逼着后来的人显式决定「这个新依赖
   要不要也给桩」，而不是静默拿到 undefined 后测出一个假绿。

   Python 单测 tests/test_atv_palette_actions.py 通过 subprocess 调它。
*/
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const AT = src.indexOf("function palCommands()");
if (AT < 0) {
  console.error("harness: palCommands() 缺失");
  process.exit(2);
}
const seg = src.slice(AT);
const FN = seg.slice(0, seg.indexOf("\n}\n") + 3);
if (FN.indexOf("palCommands") < 0 || FN.indexOf("return cmds;") < 0) {
  console.error("harness: palCommands() 切片失败");
  process.exit(2);
}

const ATV_IDS = ["atv:append:on", "atv:append:off", "atv:dbl",
  "atv:hold:23", "atv:hold:19", "atv:hold:20", "atv:hold:21", "atv:hold:22"];
const HOLD_IDS = ATV_IDS.filter((i) => i.indexOf("atv:hold:") === 0);

function build(state) {
  const clicks = [];
  const holds = [];
  const sandbox = {
    $: (sel) => { clicks.push(sel); return { click() {} }; },
    holdSend: (code) => { holds.push(code); },
    status: { curType: state.curType },
    atvAppend: { on: state.appendOn },
    dblArmed: state.dblArmed,
    palStatus: null, APPS: [], Macro: { presets: [] }, kmMap: {},
    wolIpsCache: [], wolLastStatus: {},
    recentApps: () => [], favLoad: () => [], customMacros: () => [], favResolve: () => [],
    KM_EDIT_MS: 600, adbScan: () => {}, atvScan: () => {}, connect: () => {},
    atvConnect: () => {}, launchApp: () => {}, runMacro: () => {}, sendKey: () => {},
    kmOpen: () => {}, kmResetAll: () => {}, notifToggle: () => {}, refreshStatus: () => {},
    api: () => {}, wolDiscover: () => {}, wolIpsFromStatus: () => []
  };
  const cmds = new Function("sandbox", "with (sandbox) { " + FN + "\nreturn palCommands(); }")(sandbox);
  return { cmds: cmds, clicks: clicks, holds: holds };
}
const byId = (cmds, id) => cmds.filter((c) => c.id === id)[0] || null;
const runId = (state, id) => {
  const env = build(state);
  const c = byId(env.cmds, id);
  if (!c) return null;
  c.run();
  return { clicks: env.clicks, holds: env.holds };
};

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("门禁：Apple TV 才出现这 8 条，Android 一条都没有", () => {
  const atv = build({ curType: "appletv", appendOn: false, dblArmed: false });
  const got = atv.cmds.filter((c) => c.group === "Apple TV").map((c) => c.id);
  ok(got.length === 8, "appletv 应有 8 条，实得 " + got.length);
  ok(ATV_IDS.every((i) => got.indexOf(i) >= 0),
    "缺 " + ATV_IDS.filter((i) => got.indexOf(i) < 0).join(","));
  const an = build({ curType: "android", appendOn: false, dblArmed: false });
  ok(an.cmds.filter((c) => c.group === "Apple TV").length === 0,
    "Android 用户不该看到 Apple TV 组");
  ok(an.cmds.every((c) => ATV_IDS.indexOf(c.id) < 0), "Android 面板里混进了 atv: 命令");
});

t("幂等：已是追加态再喊「追加」不产生 click", () => {
  const noop = runId({ curType: "appletv", appendOn: true, dblArmed: false }, "atv:append:on");
  ok(noop.clicks.length === 0, "已是追加还点了按钮：" + JSON.stringify(noop.clicks));
  const r = runId({ curType: "appletv", appendOn: false, dblArmed: false }, "atv:append:on");
  ok(r.clicks.length === 1 && r.clicks[0] === "#atvAppendBtn",
    "替换态喊追加应点一次切换按钮，实得 " + JSON.stringify(r.clicks));
});

t("幂等：已是替换态再喊「替换」不产生 click", () => {
  const noop = runId({ curType: "appletv", appendOn: false, dblArmed: false }, "atv:append:off");
  ok(noop.clicks.length === 0, "已是替换还点了按钮：" + JSON.stringify(noop.clicks));
  const r = runId({ curType: "appletv", appendOn: true, dblArmed: false }, "atv:append:off");
  ok(r.clicks.length === 1 && r.clicks[0] === "#atvAppendBtn",
    "追加态喊替换应点一次切换按钮，实得 " + JSON.stringify(r.clicks));
});

t("双击命令落到页面同一个 #atvDoubleBtn", () => {
  const r = runId({ curType: "appletv", appendOn: false, dblArmed: false }, "atv:dbl");
  ok(r.clicks.length === 1 && r.clicks[0] === "#atvDoubleBtn",
    "实得 " + JSON.stringify(r.clicks));
  ok(r.holds.length === 0, "双击不该走长按出口");
});

t("长按五键各自调 holdSend(code)", () => {
  for (const id of HOLD_IDS) {
    const r = runId({ curType: "appletv", appendOn: false, dblArmed: false }, id);
    const code = Number(id.slice("atv:hold:".length));
    ok(r.holds.length === 1 && r.holds[0] === code,
      id + " 应调 holdSend(" + code + ")，实得 " + JSON.stringify(r.holds));
    ok(r.clicks.length === 0, id + " 不该走按钮 click：" + JSON.stringify(r.clicks));
  }
});

t("label / hint 反映当前态：用户看得见自己会切到哪", () => {
  const on = byId(build({ curType: "appletv", appendOn: true, dblArmed: false }).cmds, "atv:append:on");
  ok(on.label.indexOf("当前：追加") >= 0, "追加态 label 要写明当前是追加，实得 " + on.label);
  ok(on.hint.indexOf("当前已是追加") >= 0, "hint 也要说明无需再切，实得 " + on.hint);
  const off = byId(build({ curType: "appletv", appendOn: true, dblArmed: false }).cmds, "atv:append:off");
  ok(off.label.indexOf("当前：追加") >= 0 && off.hint.indexOf("切换到替换") >= 0,
    "另一条要指向替换，实得 " + off.label + " / " + off.hint);
  const dbl = byId(build({ curType: "appletv", appendOn: false, dblArmed: true }).cmds, "atv:dbl");
  ok(dbl.label.indexOf("（待命中）") >= 0, "待命中态要标注，实得 " + dbl.label);
  ok(dbl.hint.indexOf("发一次即解除") >= 0, "待命中 hint 要说清怎么解除，实得 " + dbl.hint);
  const idle = byId(build({ curType: "appletv", appendOn: false, dblArmed: false }).cmds, "atv:dbl");
  ok(idle.label.indexOf("待命中") < 0, "非待命不该标注，实得 " + idle.label);
});

t("执行体只走页面出口：最多点一次按钮，绝不另发请求", () => {
  /* 每条都跑在「它确实要动作」的状态下：追加态跑 append:off、替换态跑 append:on。
     否则幂等分支会吃掉 click，这条就退化成空断言——那正是要防的假绿。 */
  const states = {
    "atv:append:on": { curType: "appletv", appendOn: false, dblArmed: false },
    "atv:append:off": { curType: "appletv", appendOn: true, dblArmed: false },
    "atv:dbl": { curType: "appletv", appendOn: false, dblArmed: false }
  };
  for (const id of Object.keys(states)) {
    const r = runId(states[id], id);
    ok(r.clicks.length === 1, id + " 应只点一次按钮，实得 " + JSON.stringify(r.clicks));
    ok(r.holds.length === 0, id + " 不该走长按出口");
  }
});

t("面板 id 不重复（含新增 8 条）", () => {
  const cmds = build({ curType: "appletv", appendOn: false, dblArmed: true }).cmds;
  const seen = {};
  const dup = [];
  cmds.forEach((c) => { if (seen[c.id]) dup.push(c.id); seen[c.id] = true; });
  ok(dup.length === 0, "重复 id: " + dup.join(","));
});

/* 供 Python 侧复核的结构化事实——harness 自己绿了不算，单测要能独立读到这些数。 */
const facts = (() => {
  const atv = build({ curType: "appletv", appendOn: false, dblArmed: false });
  const an = build({ curType: "android", appendOn: false, dblArmed: false });
  const holds = {};
  for (const id of HOLD_IDS) holds[id] = runId({ curType: "appletv", appendOn: false, dblArmed: false }, id).holds;
  const seen = {};
  const dup = [];
  atv.cmds.forEach((c) => { if (seen[c.id]) dup.push(c.id); seen[c.id] = true; });
  return {
    atvIds: atv.cmds.filter((c) => c.group === "Apple TV").map((c) => c.id),
    androidAtvIds: an.cmds.filter((c) => c.group === "Apple TV" || c.id.indexOf("atv:") === 0)
      .map((c) => c.id),
    total: atv.cmds.length,
    appendNoopClicks: runId({ curType: "appletv", appendOn: true, dblArmed: false },
      "atv:append:on").clicks.length,
    appendSwitchClicks: runId({ curType: "appletv", appendOn: false, dblArmed: false },
      "atv:append:on").clicks,
    replaceNoopClicks: runId({ curType: "appletv", appendOn: false, dblArmed: false },
      "atv:append:off").clicks.length,
    replaceSwitchClicks: runId({ curType: "appletv", appendOn: true, dblArmed: false },
      "atv:append:off").clicks,
    dblClicks: runId({ curType: "appletv", appendOn: false, dblArmed: false }, "atv:dbl").clicks,
    holdCalls: holds,
    labels: {
      appendOn: byId(atv.cmds, "atv:append:on").label,
      appendOff: byId(atv.cmds, "atv:append:off").label,
      dbl: byId(atv.cmds, "atv:dbl").label
    },
    dupes: dup
  };
})();

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed: failed, facts: facts }));
if (failed) process.exit(1);
console.log("ALL_ATV_PALETTE_CASES_PASSED");

