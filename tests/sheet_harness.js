#!/usr/bin/env node
/* 底部快捷菜单（第二十九轮）纯函数 harness：从 static/app.js 抽出 sheet 段原样执行并跑用例。
   Python 单测 tests/test_sheet.py 通过 subprocess 调它，让「快照解析 / 动作表矩阵 /
   破坏性动作排尾」有真行为证据，而不是源码字符串断言。
   段里引用了段外的 wolIpOf（IP 归一化），所以先把真函数源码拼进去再建工厂。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== sheet:begin";
const END = "/* ===== sheet:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: sheet 段标记缺失或顺序错误");
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
// wolIpOf 是真函数（IP 归一到 host），段内按同名引用，拼进去才能跑
const w = src.indexOf("function wolIpOf(t) {");
const wEnd = src.indexOf("\n}", w) + 2;
if (w < 0 || wEnd < 2) {
  console.error("harness: wolIpOf 定义缺失");
  process.exit(2);
}
const wolFn = src.slice(w, wEnd);
const NL = String.fromCharCode(10);
const EXPORTS = ["SHEET_NONE", "sheetStateText", "sheetDevOfRecent", "sheetDevOfDevice",
  "sheetDevOfAtv", "sheetItems"].join(", ");
const factory = new Function(wolFn + NL + seg + NL + "return { " + EXPORTS + " };");
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }
function eq(a, b, msg) { if (a !== b) throw new Error((msg || "eq") + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); }
function ids(dev) { return H.sheetItems(dev).map((it) => it.id).join(","); }

/* 1. 最近连接：当前离线、也没查到 MAC → 只有重连 + 复制 + 移除 */
t("recent offline without wake has connect/copy/forget", () => {
  const d = H.sheetDevOfRecent({ devices: [], recent: [] }, "192.168.1.50", []);
  eq(d.online, false); eq(d.current, false); eq(d.canWake, false); eq(d.removable, true);
  eq(ids(d), "connect,copy,forget");
});

/* 2. online 要按 IP 比：adb serial 带端口，ARP / 最近列表只认 IP */
t("recent online matches serial by stripped ip", () => {
  const d = H.sheetDevOfRecent({ devices: [{ serial: "192.168.1.50:5555" }] }, "192.168.1.50", []);
  eq(d.online, true, "host:5555 要能匹配上裸 IP");
  eq(ids(d), "switch,copy,forget", "在线且非当前只给切换");
});

/* 3. 正连着的：断开替代切换，且绝不给唤醒 */
t("recent current gets disconnect and never wake", () => {
  const d = H.sheetDevOfRecent({ cur_type: "android", current: "192.168.1.50", devices: [] },
    "192.168.1.50", [{ ip: "192.168.1.50", mac: "aa:bb" }]);
  eq(d.current, true); eq(d.canWake, false, "正连着的设备不能被唤醒");
  eq(ids(d), "disconnect,copy,forget");
});

/* 4. 唤醒只在「当前离线 + 查得到 MAC」 */
t("recent wake only when offline and mac known", () => {
  const wakes = [{ ip: "192.168.1.50", mac: "aa:bb:cc:dd:ee:ff" }];
  const d = H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", wakes);
  eq(d.canWake, true);
  eq(ids(d), "connect,wake,copy,forget");
  eq(H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", [{ ip: "192.168.1.50" }]).canWake, false,
     "发现结果里没有 MAC 不算能唤醒");
  eq(H.sheetDevOfRecent({ devices: [{ serial: "192.168.1.50:5555" }] }, "192.168.1.50", wakes).canWake,
     false, "在线设备没有唤醒的必要");
});

/* 5. 空 / 脏 IP：解析失败就不开菜单 */
t("recent junk ip yields SHEET_NONE", () => {
  eq(H.sheetDevOfRecent({}, "", []), H.SHEET_NONE);
  eq(H.sheetDevOfRecent({}, null, []), H.SHEET_NONE);
  eq(H.sheetDevOfRecent({}, undefined, []), H.SHEET_NONE);
});

/* 6. 脏状态对象不炸：devices / wakes 缺省当空 */
t("recent tolerates junk status", () => {
  const d = H.sheetDevOfRecent(null, "10.0.0.2", null);
  eq(d.online, false); eq(d.canWake, false); eq(d.removable, true);
});

/* 7. 在线设备 chip：只有切换 + 复制，不给移除（不在最近历史里） */
t("device chip has switch and copy only", () => {
  const d = H.sheetDevOfDevice({}, { serial: "192.168.1.77:5555", state: "device" });
  eq(ids(d), "switch,copy");
  eq(d.removable, false, "forget 对在线设备没有意义");
  eq(d.canWake, false);
  eq(H.sheetDevOfDevice({ cur_type: "android", current: "192.168.1.77:5555" },
    { serial: "192.168.1.77:5555" }).current, true);
});
t("device chip junk yields SHEET_NONE", () => {
  eq(H.sheetDevOfDevice({}, null), H.SHEET_NONE);
  eq(H.sheetDevOfDevice({}, {}), H.SHEET_NONE);
});

/* 8. Apple TV：connected 才算在线；永远没有 WOL、没有 IP 复制 */
t("apple tv row has no wake and no ip copy", () => {
  const st = { appletv: { connected: true }, current: "atv-1" };
  const d = H.sheetDevOfAtv(st, { id: "atv-1", name: "主卧", stored: true });
  eq(d.current, true); eq(d.online, true); eq(d.kind, "appletv");
  eq(d.canWake, false); eq(d.removable, true);
  eq(ids(d), "disconnect,copy,forget");
  eq(H.sheetItems(d).some((it) => it.label.indexOf("IP") >= 0), false, "Apple TV 复制的是设备 ID");
  const d2 = H.sheetDevOfAtv({ appletv: { connected: false } }, { id: "atv-2", name: "客厅" });
  eq(d2.current, false); eq(d2.online, false); eq(d2.removable, false);
  eq(ids(d2), "connect,copy", "未配对的 Apple TV 不给取消配对");
});
t("apple tv junk yields SHEET_NONE", () => {
  eq(H.sheetDevOfAtv({}, null), H.SHEET_NONE);
  eq(H.sheetDevOfAtv({}, {}), H.SHEET_NONE);
});

/* 9. null 快照 → 空动作表，调用方据此不开菜单 */
t("sheetItems null is empty", () => { eq(H.sheetItems(null).length, 0); });
t("SHEET_NONE is null", () => { eq(H.SHEET_NONE, null); });

/* 10. 复制永远在：菜单至少要有一个无害动作 */
t("copy action always present", () => {
  const devs = [
    H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", []),
    H.sheetDevOfRecent({ devices: [{ serial: "192.168.1.50:5555" }] }, "192.168.1.50", []),
    H.sheetDevOfRecent({ cur_type: "android", current: "192.168.1.50" }, "192.168.1.50", []),
    H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", [{ ip: "192.168.1.50", mac: "aa" }]),
    H.sheetDevOfDevice({}, { serial: "192.168.1.77:5555" }),
    H.sheetDevOfAtv({ appletv: { connected: true }, current: "a" }, { id: "a", name: "n", stored: true }),
    H.sheetDevOfAtv({}, { id: "b", name: "n" }),
  ];
  for (const d of devs) ok(ids(d).split(",").indexOf("copy") >= 0, "复制必须在：" + ids(d));
});

/* 11. 破坏性动作恒排最后且 danger */
t("forget is last and danger", () => {
  const devs = [
    H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", []),
    H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", [{ ip: "192.168.1.50", mac: "aa" }]),
    H.sheetDevOfAtv({ appletv: { connected: true }, current: "a" }, { id: "a", name: "n", stored: true }),
  ];
  for (const d of devs) {
    const items = H.sheetItems(d);
    const last = items[items.length - 1];
    eq(last.id, "forget"); eq(last.danger, true, "破坏性动作必须标 danger");
    for (const it of items.slice(0, -1)) eq(it.danger, false, "非破坏性动作不许标 danger");
  }
});

/* 12. 状态小字四态：current 压过 online */
t("sheetStateText four states with current winning", () => {
  eq(H.sheetStateText({ current: true, online: true }), "已连接 · 当前设备");
  eq(H.sheetStateText({ current: false, online: true, kind: "android" }), "在线");
  eq(H.sheetStateText({ kind: "appletv" }), "已配对 · 未连接");
  eq(H.sheetStateText({ kind: "android" }), "离线");
  eq(H.sheetStateText(null), "");
});

/* 13. 文案一律不含 Markup：最终走 textContent，设备名来自局域网广播可伪造 */
t("no label contains markup", () => {
  const devs = [
    H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", [{ ip: "192.168.1.50", mac: "aa" }]),
    H.sheetDevOfDevice({}, { serial: "192.168.1.77:5555" }),
    H.sheetDevOfAtv({ appletv: { connected: true }, current: "a" }, { id: "a", name: "<b>x</b>", stored: true }),
  ];
  for (const d of devs) for (const it of H.sheetItems(d)) {
    ok(it.label.indexOf("<") < 0, "动作文案不许含 <");
    ok(!!it.id, "每个动作都要有 id");
  }
});

/* 14. 唤醒不重不漏：三种 Android 状态各一条路径 */
t("wake appears exactly once when allowed", () => {
  const d = H.sheetDevOfRecent({ devices: [] }, "192.168.1.50", [{ ip: "192.168.1.50", mac: "aa" }]);
  eq(H.sheetItems(d).filter((it) => it.id === "wake").length, 1);
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
console.log(failed === 0 ? "ALL_SHEET_CASES_PASSED" : ("SHEET_CASES_FAILED=" + failed));
process.exit(failed === 0 ? 0 : 1);
