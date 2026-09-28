#!/usr/bin/env node
/* 手机安装引导段 harness：把 static/app.js 的「手机安装引导 → 启动」原样搬进 node，
   用假 DOM + 假 api() 跑真行为——验证渲染出来的地址是服务端给的局域网 IP，
   而不是 location.origin（在 Mac 上打开页面时它是 127.0.0.1，复制给手机一定连不上）。
   每个场景一套独立假 DOM，避免两个场景互相写脏。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ---------------- 手机安装引导 ---------------- */";
const END = "/* ---------------- 启动 ---------------- */";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) { console.error("harness: 安装段标记缺失"); process.exit(2); }
const seg = src.slice(s, e);

const ATV_TOKEN = "5Tq4RdHzNGQo";
const TOKEN_Q = "?token=" + ATV_TOKEN;
const TOKEN_AMP = "&token=" + ATV_TOKEN;
const ORIGIN = "http://127.0.0.1:8300";   // Mac 上打开页面时的 location.origin
const LAN = "http://192.168.1.109:8300";  // 服务端 /api/setup 给的局域网地址

function mkEl(tag) {
  return {
    tagName: tag, value: "", textContent: "", hidden: false, src: "", href: "",
    className: "", _children: [], _sel: "",
    addEventListener() {}, select() {},
    insertBefore(el) { this._children.push(el); },
    querySelector(sel) {
      for (const c of this._children) if (c._sel === sel) return c;
      return null;
    }
  };
}

function makeEnv() {
  const els = {};
 for (const id of ["installCmd", "qrImg", "lanUrl", "lanHint", "lanTokenQ", "setupQrBox",
                   "setupQrImg", "phoneInstall", "apkLink", "copyCmd", "copyDebugBtn",
                    "termuxDebugCmd", "pyatvFixCmd", "copyPyatvFixBtn"]) {
    const el = mkEl("div"); el._sel = "#" + id; els[id] = el;
  }
  const olEl = mkEl("ol"); olEl._sel = "ol";
  els.phoneInstall._children.push(olEl);
  const qrboxEl = mkEl("div"); qrboxEl._sel = ".qrbox";
  const doc = {
    createElement: (t) => mkEl(t),
    querySelector: (sel) => (sel === ".qrbox" ? qrboxEl : els[sel.replace("#", "")] || null),
    addEventListener() {}
  };
  return { doc: doc, els: els, $: (sel) => doc.querySelector(sel) };
}

function val(env, sel, key) {
  const el = env.doc.querySelector(sel);
  return el ? el[key] : undefined;
}
// 令牌提示是 renderInstall() 里 createElement 出来的 <p class="hint">，插在 ol 前面
function tipText(env) {
  for (const c of env.els.phoneInstall._children) {
    if (c.className === "hint") return c.textContent;
  }
  return "";
}

function run(apiImpl) {
  const env = makeEnv();
  const factory = new Function("$", "api", "toast", "ATV_TOKEN", "TOKEN_Q", "TOKEN_AMP",
    "location", "document", "navigator", "window", seg);
  factory(env.$, apiImpl, () => {}, ATV_TOKEN, TOKEN_Q, TOKEN_AMP,
    { origin: ORIGIN }, env.doc, {}, { addEventListener() {} });
  // 段内的 api().then() 是异步的，等一个宏任务再读，读到的是局域网 IP 生效后的真值
  return new Promise((r) => setTimeout(r, 0)).then(() => ({
    installCmd: val(env, "#installCmd", "value"),
    lanUrl: val(env, "#lanUrl", "textContent"),
    qrSrc: val(env, "#qrImg", "src"),
    setupSrc: val(env, "#setupQrImg", "src"),
    hintHidden: val(env, "#lanHint", "hidden"),
    tokenQ: val(env, "#lanTokenQ", "textContent"),
    tip: tipText(env)
  }));
}

let failed = 0;
function ok(cond, msg) {
  if (!cond) { console.error("FAIL " + msg); failed++; } else { console.log("ok   " + msg); }
}
function eq(a, b, msg) { ok(a === b, msg + " => " + JSON.stringify(a)); }

const LAN_CMD = "curl -sL " + LAN + "/install" + TOKEN_Q + " | bash";
const ORIGIN_CMD = "curl -sL " + ORIGIN + "/install" + TOKEN_Q + " | bash";

Promise.all([
  run(() => Promise.resolve({ url: LAN, token: ATV_TOKEN })),
  run(() => Promise.reject(new Error("nope")))
]).then((r) => {
  const H = r[0];
  eq(H.installCmd, LAN_CMD, "安装命令用局域网 IP（不是 location.origin）");
  ok(H.installCmd.indexOf(ORIGIN) < 0, "安装命令里不含回环地址");
  eq(H.lanUrl, LAN + "/", "明文「本机地址」= 局域网地址");
  eq(H.qrSrc, "/api/qr.svg?text=" + encodeURIComponent(LAN_CMD) + TOKEN_AMP,
     "安装命令二维码指向同一条命令");
  eq(H.setupSrc, "/api/qr.svg?text=" + encodeURIComponent(LAN + "?token=" + ATV_TOKEN) + TOKEN_AMP,
     "接入二维码仍带令牌");
  eq(H.hintHidden, false, "明文地址提示已显示");
  eq(H.tokenQ, TOKEN_Q, "明文地址后跟令牌");
  ok(H.tip.indexOf(LAN) >= 0 && H.tip.indexOf(ORIGIN) < 0, "令牌提示用局域网地址");
  eq(r[1].installCmd, ORIGIN_CMD, "api 失败时退回 location.origin，不渲染空地址");
  if (failed) { console.error(failed + " case(s) failed"); process.exit(1); }
  console.log("install harness: all passed");
});
