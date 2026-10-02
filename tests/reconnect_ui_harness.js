#!/usr/bin/env node
/* renderAutoReconn / arPaint 的行为 harness：从 static/app.js 按函数名原样抽出执行，
   注入假 DOM 元素与假 setInterval，验证「本地秒走 + 服务端 next_in 校准 + 停掉时清计时器」。
   Python 单测 tests/test_reconnect_ui.py 通过 subprocess 调它——有真行为证据，
   而不只是源码字符串断言。改函数名会让这里直接失败（这就是目的）。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");

// 按花括号配平切出一个 function（注释里也可能有括号，所以不能数 `{`）
function sliceFn(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) { console.error("harness: 找不到 function " + name); process.exit(2); }
  let depth = 0, started = false;
  for (let j = i; j < src.length; j++) {
    const c = src[j];
    if (c === "{") { depth++; started = true; }
    else if (c === "}") {
      depth--;
      if (started && depth === 0) return src.slice(i, j + 1);
    }
  }
  console.error("harness: function " + name + " 的花括号没配平");
  process.exit(2);
}

const seg = [
  "let arEl, arBase, arActive, arStopped, arNextIn, arTick;",
  sliceFn("arPaint"),
  sliceFn("renderAutoReconn"),
  "return { renderAutoReconn: renderAutoReconn,",
  "         peek: function () { return { arBase: arBase, arActive: arActive,",
  "                                      arStopped: arStopped, arNextIn: arNextIn,",
  "                                      live: timers.filter(Boolean).length }; } };",
].join("\n");

// 假 setInterval / clearInterval：不真等，只把回调收进来让人手动步进
const realSI = globalThis.setInterval, realCI = globalThis.clearInterval;
let timers = [];
globalThis.setInterval = function (fn) { timers.push(fn); return timers.length; };
globalThis.clearInterval = function (id) {
  if (id >= 1 && id <= timers.length) timers[id - 1] = null;
};
// 假定时器必须一直挂到用例跑完：函数体里是运行时按名字找 setInterval 的，
// 这里一还原，被测代码拿回去的就是真定时器（于是「没挂上」）。
const api = new Function("timers", seg)(timers);

let cases = 0, failed = 0;
function check(name, got, want) {
  cases++;
  if (got !== want) {
    failed++;
    console.error("FAIL " + name + "\n  got:  " + JSON.stringify(got) + "\n  want: " + JSON.stringify(want));
  }
}
function el() { return { textContent: "" }; }
function step() { const live = timers.filter(Boolean); return live.length ? live[0]() : null; }

// 1. 排队中：显示秒数，并挂上一个本地计时器
let node = el();
api.renderAutoReconn(node, "🤖 电视 · 离线", { active: true, stopped: false, next_in: 12 });
check("排队中显示秒数", node.textContent, "🤖 电视 · 离线 · 自动重连中（12s 后重试）");
check("排队中挂了一个计时器", api.peek().live, 1);

// 2. 本地按秒走，不等 8s 轮询
step();
check("走一秒", node.textContent, "🤖 电视 · 离线 · 自动重连中（11s 后重试）");

// 3. 倒数到 0：清掉计时器，回到不带秒数的「自动重连中」
for (let i = 0; i < 12; i++) step();
check("倒数归零后清计时器", api.peek().live, 0);
check("归零后不再显示秒数", node.textContent, "🤖 电视 · 离线 · 自动重连中");

// 4. 三次失败停机：只提示手动，不留计时器
node = el();
api.renderAutoReconn(node, "🤖 电视 · 离线", { active: false, stopped: true, next_in: 0 });
check("停机提示手动", node.textContent, "🤖 电视 · 离线 · 重连失败，点连接重试");
check("停机不挂计时器", api.peek().live, 0);

// 5. 空闲：后缀为空，别在设备名后面缀废话
node = el();
api.renderAutoReconn(node, "🤖 电视 · 离线", { active: false, stopped: false, next_in: 0 });
check("空闲无后缀", node.textContent, "🤖 电视 · 离线");

// 6. Apple TV：ar 传 null，arBase 必须复位成新传入的 base（不能留着上一台 Android 的后缀）
node = el();
api.renderAutoReconn(node, "🍎 Apple TV · 离线", { active: true, stopped: false, next_in: 5 });
check("切换前是 Android 文案", node.textContent, "🍎 Apple TV · 离线 · 自动重连中（5s 后重试）");
api.renderAutoReconn(node, "🍎 Apple TV · 离线", null);
check("切到 Apple TV 后缀清空", node.textContent, "🍎 Apple TV · 离线");
check("切到 Apple TV 无计时器", api.peek().live, 0);
check("切到 Apple TV 清掉 active", api.peek().arActive, false);

globalThis.setInterval = realSI;
globalThis.clearInterval = realCI;
if (failed) { console.error("harness: " + failed + "/" + cases + " 失败"); process.exit(1); }
console.log("OK " + cases + " cases");
