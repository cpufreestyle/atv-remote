#!/usr/bin/env node
/* app.js 顶层整文件可执行性冒烟（strict-mode 中断类缺陷的兜底）。

   动机：2026-10-18 小米 K90 真机「无法点到 Apple TV」，根因是 app.js:2116
   VOLSET_MAX_FALLBACK = 15 漏了 const——文件第 2 行就是 "use strict"，
   顶层裸赋值抛 ReferenceError，整个脚本当场中断，3304 行的页签 click 绑定
   及其后所有初始化从未执行。此前 16 天没人发现：现有 harness 全是「按
   atv-xxx:begin/end 标记抽段执行」，整文件顶层路径没有任何行为证据，
   node --check 也只查语法不执行；SW 陈旧缓存又一直在发旧版 app.js 掩盖症状。

   本 harness 用万能 DOM stub 完整执行 app.js 顶层（任何属性/调用都安全，
   唯独不掩盖 ReferenceError），并断言两个 .devtab 都注册了 click 监听——
   「顶层跑得完 + 页签绑得上」正是本轮真机故障的两半。 */

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");

function makeEl(name) {
  const listeners = {};
  const target = function () {};
  return new Proxy(target, {
    get(t, k) {
      if (k === "addEventListener") return (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
      if (k === "removeEventListener") return () => {};
      if (k === "__listeners") return listeners;
      if (k === "classList") return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      if (k === "dataset") return {};
      if (k === "style") return new Proxy({}, { get: () => "", set: () => true });
      if (k === "value" || k === "textContent" || k === "innerHTML") return "";
      if (k === "length") return 0;
      if (k === Symbol.toPrimitive) return () => 0;
      return makeEl(name + "." + String(k));
    },
    set() { return true; },
    apply() { return makeEl(name + "()"); },
  });
}

const devtabs = ["android", "appletv"].map((dev) => {
  const el = makeEl("devtab");
  Object.defineProperty(el, "dataset", { value: { dev } });
  return el;
});

const doc = {
  querySelector: (sel) => (String(sel).indexOf("devtab") >= 0 ? devtabs[0] : makeEl("qs:" + sel)),
  querySelectorAll: (sel) => (String(sel).indexOf("devtab") >= 0 ? devtabs.slice() : [makeEl("qsa:" + sel)]),
  createElement: () => makeEl("createElement"),
  createTextNode: () => makeEl("textNode"),
  addEventListener: () => {},
  removeEventListener: () => {},
  getElementById: () => null,
  documentElement: makeEl("documentElement"),
  body: makeEl("body"),
  hidden: false,
  visibilityState: "visible",
};

const pending = () => new Promise(() => {});
const sandbox = {
  document: doc,
  window: null,
  navigator: { userAgent: "node-harness", mediaSession: null },
  location: { href: "http://127.0.0.1:8300/", hostname: "127.0.0.1", protocol: "http:", search: "" },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: () => pending(),
  XMLHttpRequest: function () {},
  setInterval: () => 0,
  setTimeout: () => 0,
  clearInterval: () => {},
  clearTimeout: () => {},
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  alert: () => {}, confirm: () => false, prompt: () => null,
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  matchMedia: () => ({ matches: false, addEventListener: () => {}, addListener: () => {} }),
  CustomEvent: function () { return { detail: null }; },
  Event: function () { return {}; },
  Notification: function () {},
  WebSocket: function () {},
  addEventListener: () => {},
  removeEventListener: () => {},
  parent: null,
  top: null,
  self: null,
  scrollTo: () => {},
  console,
};
sandbox.window = new Proxy(sandbox, {
  get(t, k) { return k in t ? t[k] : makeEl("window." + String(k)); },
  set(t, k, v) { t[k] = v; return true; },
});

/* vm context：src 里未声明变量读写法与浏览器一致（走 global 查找），
   严格模式下的裸赋值照样抛 ReferenceError——正是要兜的那类中断。 */
const ctx = vm.createContext(sandbox);
let execError = null;
try {
  vm.runInContext(src, ctx, { filename: "app.js" });
} catch (e) {
  execError = e;
}

let failed = 0;
function ok(cond, msg) {
  if (!cond) { failed++; console.error("NOT OK - " + msg); }
  else { console.log("ok - " + msg); }
}

ok(!execError, "app.js 顶层整文件执行不抛异常（严格模式下裸赋值/未声明引用会在这里炸）" +
   (execError ? " :: " + execError.name + ": " + execError.message : ""));
ok(devtabs[0].__listeners.click && devtabs[0].__listeners.click.length >= 1, "Android 页签注册了 click 监听");
ok(devtabs[1].__listeners.click && devtabs[1].__listeners.click.length >= 1, "Apple TV 页签注册了 click 监听");

console.log(failed === 0 ? "ALL_TOP_LEVEL_CASES_PASSED" : "TOP_LEVEL_FAILURES=" + failed);
process.exit(failed === 0 ? 0 : 1);
