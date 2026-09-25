/* ATV Remote 前端逻辑（Android TV + Apple TV） */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// 服务端启用 --token 时会注入到这里；未启用为空串，下面所有拼接都退化成原路径，行为不变
const ATV_TOKEN = (document.querySelector('meta[name="atv-token"]') || {}).content || "";
const TOKEN_Q = ATV_TOKEN ? "?token=" + encodeURIComponent(ATV_TOKEN) : "";
const TOKEN_AMP = ATV_TOKEN ? "&token=" + encodeURIComponent(ATV_TOKEN) : "";

const APPS = [
  { name: "YouTube", pkg: "com.google.android.youtube.tv" },
  { name: "Netflix", pkg: "com.netflix.ninja" },
  { name: "Prime Video", pkg: "com.amazon.amazonvideo.livingroom" },
  { name: "Disney+", pkg: "com.disney.disneyplus" },
  { name: "Spotify", pkg: "com.spotify.tv.android" },
  { name: "Plex", pkg: "com.plexapp.android" },
  { name: "Kodi", pkg: "org.xbmc.kodi" },
];

// 浏览器按键 → Android 风格键码（Apple TV 由服务端再映射）
const KEYMAP = {
  ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22,
  Enter: 23, Escape: 4, Backspace: 67, Delete: 112,
  PageUp: 92, PageDown: 93, Home: 3,
  MediaPlayPause: 85, MediaStop: 86, MediaTrackNext: 87, MediaTrackPrevious: 88,
  MediaFastForward: 90, MediaRewind: 89,
  AudioVolumeUp: 24, AudioVolumeDown: 25, AudioVolumeMute: 164,
};

const status = { curType: null, connected: false, screen: { w: 1920, h: 1080 } };
const lastSent = {}; // 同键节流（自动重复）
let pairingDev = null; // 正在配对的 Apple TV
let lastChipSig = ""; // 设备列表签名：无变化则跳过重建
let lastImeTarget = null; // 上次查过输入法的设备，避免 8s 轮询反复查
// ADBKeyboard 中文键盘状态（仅 Android TV 需要，按需查询，不进 8s 轮询）
const imeState = { installed: false, enabled: false, current: false, default_ime: "", checked: false };

/* ---------------- 基础 ---------------- */
// 走请求头而不是 ?token= 查询串：令牌会留在浏览器历史、Referer 和任何中间代理日志里。
// 只有 curl / <img> / 下载链接这类发不出自定义头的场景才需要拼进 URL。
const TOKEN_HDR = ATV_TOKEN ? { "X-ATV-Token": ATV_TOKEN } : {};

/* 触觉反馈（A5）：按键有 50–200ms 网络延迟，「按没按上」不确定，按下瞬间先震一下。
   只在按下时给（不等服务器回包，否则失去意义）；平台不支持 navigator.vibrate 时静默 no-op。
   默认开，设置弹窗里可关（偏好存 localStorage）。 */
const HAPTIC_KEY = "atv.haptics";
function hapticEnabled() { return localStorage.getItem(HAPTIC_KEY) !== "0"; }
function buzz(ms = 8) {
  if (!hapticEnabled()) return;
  try { navigator.vibrate && navigator.vibrate(ms); } catch (e) { /* 桌面/无硬件忽略 */ }
}

async function api(path, body) {
  const init = { headers: { ...TOKEN_HDR } };
  if (body) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

// 日志滚动带：连按/轮询会瞬间刷屏，所以按前缀分级着色 + 只留最近 60 行 + 自动滚到底。
// 颜色由 CSS 类承担（--ok/--warn/--danger 令牌），不要在这里写死 hex。
const LOG_MAX = 60;
function log(msg) {
  const el = $("#log");
  const line = document.createElement("div");
  // 一律 textContent：日志内容可能拼进设备名 / IP，不用 innerHTML
  line.textContent = msg;
  if (msg.startsWith("⚠") || msg.startsWith("❌") || msg.includes("失败")) line.className = "err";
  else if (msg.startsWith("✅") || msg.startsWith("已")) line.className = "ok";
  else if (msg.startsWith("→")) line.className = "go";
  else if (msg.startsWith("⏳")) line.className = "warn";
  el.append(line);
  while (el.childElementCount > LOG_MAX) el.firstElementChild.remove();
  el.scrollTop = el.scrollHeight;
}

// 骨架屏：扫描 / 加载要等几秒，先给结构占位，别让用户以为点了没反应
function skeletonRows(box, n = 3) {
  box.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const sk = document.createElement("div");
    sk.className = "skel row";
    box.appendChild(sk);
  }
}

let toastTimer = null;
function toast(msg, isInfo = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("info", isInfo);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3000);
}

function flashKey(code) {
  const btn = document.querySelector(`[data-key="${code}"]`);
  if (!btn) return;
  btn.classList.add("pressed");
  setTimeout(() => btn.classList.remove("pressed"), 110);
}

/* ---------------- 命令发送 ---------------- */
async function sendKey(code) {
  const now = performance.now();
  if (lastSent[code] && now - lastSent[code] < 90) return; // 按住自动重复时节流
  lastSent[code] = now;
  flashKey(code);
  try {
    await api("/api/cmd", { type: "key", code });
    log(`→ keyevent ${code}`);
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
}

async function sendText(text, withEnter) {
  text = text.replace(/[\r\n]+/g, " ");
  if (!text.trim()) {
    if (withEnter) return sendKey(66);
    return;
  }
  if (status.curType !== "appletv" && /[^\x20-\x7E]/.test(text)) {
    if (!imeState.current) {
      toast("⚠ 中文输入要把电视输入法切到 ADBKeyboard，点下方「中文键盘 → 启用」");
      refreshIme(); // 状态可能已变（比如刚在电视上手动切过），顺手刷新一次
      return;
    }
  }
  try {
    await api("/api/cmd", { type: "text", text, enter: !!withEnter });
    const echo = privacy.on
      ? "•".repeat(Math.min(8, text.length))
      : `"${text.slice(0, 30)}"${text.length > 30 ? "…" : ""}`;
    log(`→ text ${echo}${withEnter ? " + Enter" : ""}`);
    $("#textInput").value = "";
    $("#textInput").blur(); // 发送后回到全局键盘遥控状态
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
}

/* ---------------- 隐私模式 ---------------- */
// 在电视上输密码一类场景：发送内容不回显进 #log（会一直留在手机屏幕上），输入框同时掩码
const privacy = { on: localStorage.getItem("atv.privacy") === "1" };

function applyPrivacy() {
  $("#privacyBtn").classList.toggle("on", privacy.on);
  $("#privacyBtn").setAttribute("aria-pressed", privacy.on ? "true" : "false");
  $("#textInput").type = privacy.on ? "password" : "text";
  $("#privacyHint").hidden = !privacy.on;
}
$("#privacyBtn").addEventListener("click", () => {
  privacy.on = !privacy.on;
  localStorage.setItem("atv.privacy", privacy.on ? "1" : "0");
  applyPrivacy();
});

/* ---------------- 剪贴板 / 常用短语 / 语音 ---------------- */
// 剪贴板与语音识别都要 secure context：http://127.0.0.1 / https / Mac App 里可用，
// http://局域网IP 打开时浏览器直接不给用 —— 点击时给出可操作的提示，不静默失败
$("#pasteBtn").addEventListener("click", async () => {
  if (!navigator.clipboard || !navigator.clipboard.readText) {
    return toast("此浏览器不支持读取剪贴板，可长按输入框手动粘贴");
  }
  try {
    const text = await navigator.clipboard.readText();
    if (!text.trim()) return toast("剪贴板是空的");
    await sendText(text, false);
  } catch (e) {
    toast("读取剪贴板失败：http 局域网打开会被浏览器禁掉，用 Mac App 或本机打开");
  }
});

const PHRASE_KEY = "atv.phrases";
const DEFAULT_PHRASES = ["第一集", "下一集", "原画", "全集"];
let phrases = (() => {
  try {
    const v = JSON.parse(localStorage.getItem(PHRASE_KEY));
    if (Array.isArray(v)) return v.filter((s) => typeof s === "string").slice(0, 12);
  } catch { /* 损坏就回落默认 */ }
  return DEFAULT_PHRASES.slice();
})();

function renderPhrases() {
  const row = $("#phraseRow");
  row.innerHTML = "";
  phrases.forEach((p) => {
    const c = document.createElement("button");
    c.className = "btn tiny";
    c.textContent = p;
    c.title = "点击发送到电视";
    c.onclick = () => sendText(p, false);
    row.appendChild(c);
  });
  const edit = document.createElement("button");
  edit.className = "btn tiny";
  edit.textContent = "✏️";
  edit.title = "编辑常用短语（逗号分隔）";
  edit.onclick = () => {
    const v = prompt("常用短语（用逗号分隔，点 chips 即发送）：", phrases.join("，"));
    if (v === null) return;
    phrases = v.split(/[，,]/).map((s) => s.trim()).filter(Boolean).slice(0, 12);
    localStorage.setItem(PHRASE_KEY, JSON.stringify(phrases));
    renderPhrases();
  };
  row.appendChild(edit);
}

let micRec = null;
function micListening(on) {
  $("#micBtn").classList.toggle("on", on);
  $("#micBtn").setAttribute("aria-pressed", on ? "true" : "false");
}
$("#micBtn").addEventListener("click", () => {
  if (micRec) {           // 再点一次 = 停止
    micRec.stop();
    micRec = null;
    micListening(false);
    return;
  }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return toast("此浏览器不支持语音识别（Chrome / Edge / Safari 可以）");
  if (!window.isSecureContext) {
    return toast("语音要安全上下文：用 Mac App、本机 localhost 或 https 打开");
  }
  const r = new SR();
  r.lang = "zh-CN";
  r.interimResults = false;
  r.maxAlternatives = 1;
  r.onresult = (e) => {
    const text = e.results[0][0] ? e.results[0][0].transcript : "";
    if (text) sendText(text, false);
    else toast("没听清，再说一次");
  };
  r.onerror = (e) => { toast("语音识别失败：" + (e.error || "unknown")); micListening(false); micRec = null; };
  r.onend = () => { micListening(false); micRec = null; };
  micRec = r;
  micListening(true);
  log("🎤 请说话（说完自动发送）…");
  r.start();
});

/* ---------------- 睡眠定时 ---------------- */
// until 用 epoch 秒由服务端给，倒计时本地走秒：不靠 8s 轮询刷新，按钮秒级响应
let sleepUntil = 0;
let sleepTick = null;

function fmtLeft(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function renderSleepTimer(st) {
  sleepUntil = st && st.active && st.until ? st.until : 0;
  $("#sleepCancelBtn").classList.toggle("hidden", !sleepUntil);
  clearInterval(sleepTick);
  sleepTick = null;
  if (!sleepUntil) {
    $("#sleepState").textContent = "到点自动让电视休眠 / 关闭，取消随时有效。";
    return;
  }
  const tick = () => {
    const left = Math.max(0, Math.round(sleepUntil - Date.now() / 1000));
    $("#sleepState").textContent = left > 0 ? `⏳ ${fmtLeft(left)} 后自动休眠 / 关闭电视` : "正在执行…";
    if (left <= 0) { clearInterval(sleepTick); sleepTick = null; }
  };
  tick();
  sleepTick = setInterval(tick, 1000);
}

$$("[data-sleep]").forEach((b) => b.addEventListener("click", async () => {
  try {
    const r = await api("/api/cmd", { type: "timer", action: "set", minutes: +b.dataset.sleep });
    renderSleepTimer(r.sleep_timer);
    toast(`已定时 ${b.dataset.sleep} 分钟后休眠`);
  } catch (e) {
    toast("⚠ " + e.message);
  }
}));
$("#sleepCancelBtn").addEventListener("click", async () => {
  try {
    const r = await api("/api/cmd", { type: "timer", action: "cancel" });
    renderSleepTimer(r.sleep_timer);
    toast("已取消睡眠定时", true);
  } catch (e) {
    toast("⚠ " + e.message);
  }
});

/* ---------------- 一键宏 ---------------- */
// 预置宏来自 /api/macros；自定义宏只存浏览器 localStorage（state.json 不放可编辑内容）。
// 执行在服务端线程里串行跑，这里只负责发起、显示状态和取消。
const MACRO_LS = "atv_macros_v1";
let macroTick = null;

const customMacros = () => {
  try { return JSON.parse(localStorage.getItem(MACRO_LS)) || []; } catch (e) { return []; }
};

function macroButton(m, custom) {
  const b = document.createElement("button");
  b.className = "btn";
  b.dataset.macro = m.id || m.name;
  b.textContent = (custom ? "⭐ " : "") + m.name;
  b.title = (m.steps || []).map((st) => st.type + (st.delay ? `+${st.delay}ms` : "")).join(" → ");
  b.addEventListener("click", () => runMacro(m));
  if (custom) {
    const del = document.createElement("button");
    del.className = "btn tiny danger";
    del.textContent = "✕";
    del.title = "删除这个自定义宏";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      const left = customMacros().filter((x) => x.name !== m.name);
      localStorage.setItem(MACRO_LS, JSON.stringify(left));
      loadMacros();
      toast("已删除", true);
    });
    const wrap = document.createElement("span");
    wrap.className = "withdel";
    wrap.append(b, del);
    return wrap;
  }
  return b;
}

async function loadMacros() {
  const row = $("#macroRow");
  if (!row) return;
  row.textContent = "";
  try {
    const j = await api("/api/macros");
    ([...(j.presets || []), ...customMacros()]).forEach((m, i) => {
      row.append(macroButton(m, i >= (j.presets || []).length));
    });
    renderMacroState(j);
  } catch (e) {
    row.textContent = "";
    $("#macroState").textContent = "⚠ 宏列表加载失败：" + e.message;
  }
}

function renderMacroState(j) {
  clearInterval(macroTick);
  macroTick = null;
  const cancelBtn = $("#macroCancelBtn");
  const stateEl = $("#macroState");
  const running = !!(j && j.running);
  if (cancelBtn) cancelBtn.classList.toggle("hidden", !running);
  if (!running) {
    stateEl.textContent = "宏会按顺序执行多步操作（应用包名会依次尝试 Android / tvOS）。";
    return;
  }
  stateEl.textContent = "⏳ 宏执行中……（可取消；每步结果见下方日志）";
  macroTick = setInterval(async () => {
    try {
      const again = await api("/api/macros");
      renderMacroState(again);
    } catch (e) { /* 轮询失败不打扰：宏还在服务端跑 */ }
  }, 1500);
}

async function runMacro(m) {
  try {
    log(`⚡ 执行宏「${m.name}」…`);
    const r = await api("/api/cmd", { type: "macro", name: m.name, steps: m.steps });
    renderMacroState(r);
    toast(`宏「${m.name}」开始执行`);
  } catch (e) {
    toast("⚠ " + e.message);
  }
}

$("#macroCancelBtn")?.addEventListener("click", async () => {
  try {
    const r = await api("/api/cmd", { type: "macro", action: "cancel" });
    renderMacroState(r);
    toast("已取消宏", true);
  } catch (e) {
    toast("⚠ " + e.message);
  }
});

$("#macroRunCustomBtn")?.addEventListener("click", async () => {
  try {
    const m = JSON.parse($("#macroText").value);
    if (!Array.isArray(m.steps) || !m.steps.length) throw new Error("steps 不能为空");
    await runMacro(m);
  } catch (e) {
    toast("⚠ 宏 JSON 解析失败：" + e.message);
  }
});

$("#macroSaveCustomBtn")?.addEventListener("click", () => {
  try {
    const m = JSON.parse($("#macroText").value);
    if (!Array.isArray(m.steps) || !m.steps.length) throw new Error("steps 不能为空");
    if (!m.name) throw new Error("缺少 name");
    const list = customMacros().filter((x) => x.name !== m.name);
    list.push({ name: String(m.name).slice(0, 24), steps: m.steps });
    localStorage.setItem(MACRO_LS, JSON.stringify(list));
    loadMacros();
    toast(`已保存「${m.name}」`, true);
  } catch (e) {
    toast("⚠ 宏 JSON 解析失败：" + e.message);
  }
});

/* ---------------- 状态与连接 ---------------- */
let statusBusy = null;  // 上一次 /api/status 没回来就不叠加下一次（慢响应会排在按键锁后面）

async function refreshStatus() {
  if (statusBusy) return statusBusy;
  statusBusy = (async () => {
    try {
      renderStatus(await api("/api/status"));
    } catch (e) {
      // 原来是静默 ignore：服务端挂了状态栏却还留着上一次的「已连接」，
      // 用户对着一个已经死掉的遥控器按半天。
      $("#dot").className = "dot off";
      $("#tvInfo").textContent = "连不上服务端：" + e.message;
    } finally {
      statusBusy = null;
    }
  })();
  return statusBusy;
}

function renderStatus(s) {
  status.curType = s.cur_type;
  const dot = $("#dot"), info = $("#tvInfo");
  const isApple = s.cur_type === "appletv";
  if (!isApple && s.info && s.info.w) status.screen = { w: s.info.w, h: s.info.h };

  if (isApple) {
    status.connected = !!s.appletv.connected;
    const cur = (s.appletv.devices || []).find((d) => d.id === s.current);
    info.textContent = status.connected
      ? `🍎 ${cur ? cur.name : "Apple TV"}${s.appletv.kb_focus === "Focused" ? " · 输入框已聚焦" : ""}`
      : (s.current ? `🍎 ${cur ? cur.name : s.current} · 离线` : "未连接");
  } else {
    status.connected = s.current_state === "device";
    if (status.connected) {
      const i = s.info || {};
      const name = [i.brand, i.model].filter(Boolean).join(" ").trim() || s.current;
      const extra = [i.android ? "Android " + i.android : "", i.w ? `${i.w}×${i.h}` : ""].filter(Boolean).join(" · ");
      info.textContent = `🤖 ${name}${extra ? " · " + extra : ""}`;
    } else if (s.current) {
      info.textContent = `🤖 ${s.current} · ${s.current_state === "unauthorized" ? "未授权（请在电视上点允许）" : s.current_state || "离线"}`;
    } else {
      info.textContent = s.adb_found ? "未连接" : "未连接（本机未安装 adb）";
    }
  }
  dot.className = "dot " + (status.connected ? "on" : s.current ? "warn" : "off");
  // 没连上电视时把遥控区压暗：按钮此时发了也没用，先让「连接」动作 visually 突出
  document.body.classList.toggle("disc", !status.connected);

  // Android 掉线自动重连的进度（服务后台线程在试，失败 3 次会停并提示手动）
  const ar = s.auto_reconnect;
  if (!isApple && ar && ar.active) info.textContent += " · 自动重连中";
  else if (!isApple && ar && ar.stopped) info.textContent += " · 重连失败，点连接重试";

  // Apple TV 输入框聚焦徽标
  const badge = $("#kbFocus");
  if (isApple && s.appletv.kb_focus === "Focused") badge.classList.remove("hidden");
  else badge.classList.add("hidden");

  // 按设备类型调整 UI
  $("#settingsBtn").classList.toggle("hidden", isApple);
  $("#appsAndroid").classList.toggle("hidden", isApple);
  $("#appsApple").classList.toggle("hidden", !isApple);
  $("#shotBtn").textContent = isApple ? "🖼 正在播放画面" : "📸 电视截屏";
  // Android 的 placeholder 由 renderIme() 按 ADBKeyboard 状态设置（会随能力变化）
  if (isApple) $("#textInput").placeholder = "在此打字，回车发送到电视（Apple TV 支持中文）";
  // 清空 / 搜索键走 ADBKeyboard 广播，Apple TV 没有这回事
  $("#kbTools").classList.toggle("hidden", isApple);

  if (s.version) $("#verHint").textContent = "ATV Remote v" + s.version + " · 数据只存在本机浏览器";
  // 睡眠定时状态（倒计时本地走秒，这里只负责发现 set/cancel 的变化）
  renderSleepTimer(s.sleep_timer);
  renderMacroState(s.macro || { running: false });

  // 连接的设备变了才去查输入法（每次查询要 3 条 shell，不能跟着 8s 轮询跑）
  const imeTarget = (isApple ? "atv:" : "adb:") + (s.current || "");
  if (imeTarget !== lastImeTarget) {
    lastImeTarget = imeTarget;
    refreshIme();
  }
  $("#padHint").textContent = isApple
    ? "轻点 = 点击 · 按住拖动 = 滑动（Apple TV 触控）"
    : "轻点 = 点击 · 按住拖动 = 滑动（映射整块电视屏幕）";
  $("#kbdHint").innerHTML = isApple
    ? '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>PageUp/Down</b> 快退/快进 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送（支持中文）。'
    : '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>退格</b>=删除 · <b>PageUp/Down</b> 翻页 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送。';

  // 设备列表每 8 秒轮询一次，数据没变就别重建 DOM（会打断 hover / 触发无谓重排）
  const chipSig = JSON.stringify([
    s.cur_type, s.current, s.current_state, s.recent, s.devices,
    (s.appletv.devices || []).map((d) => d.id),
  ]);
  if (chipSig === lastChipSig) return;
  lastChipSig = chipSig;

  // Android 最近连接
  const rc = $("#recentChips");
  rc.innerHTML = "";
  (s.recent || []).forEach((t) => {
    const c = document.createElement("span");
    c.className = "chip" + (s.cur_type === "android" && t === s.current ? " active" : "");
    c.textContent = t;
    c.title = "点击连接 · 右键移除";
    c.onclick = () => connect(t);
    c.oncontextmenu = (e) => { e.preventDefault(); api("/api/forget", { target: t }).then(refreshStatus); };
    rc.appendChild(c);
  });

  // Android 在线设备
  const dc = $("#deviceChips");
  dc.innerHTML = "";
  (s.devices || []).filter((d) => !(s.cur_type === "android" && d.serial === s.current)).forEach((d) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = `${d.serial}（${d.state === "device" ? "在线" : d.state}）`;
    c.onclick = () => api("/api/switch", { target: d.serial }).then(refreshStatus).catch((e) => toast(e.message));
    dc.appendChild(c);
  });

  // 已配对的 Apple TV（未连接当前页也展示）
  if (isApple || !s.current) renderAtvKnown(s);
}

async function connect(target) {
  target = (target || $("#targetInput").value).trim();
  if (!target) return toast("请输入电视 IP");
  log(`正在连接 ${target} …`);
  $("#connectBtn").disabled = true;
  try {
    const r = await api("/api/connect", { target });
    log(`已连接 ${r.target}`);
    if (r.warning) toast(r.warning, true);
    await refreshStatus();
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  } finally {
    $("#connectBtn").disabled = false;
  }
}

/* ---------------- Android 无线调试扫描 ---------------- */
// 无线调试设备（Android 11+）会广播配对 / 连接端口；老电视只开 5555 端口时不广播
async function adbScan() {
  const box = $("#adbScanList");
  skeletonRows(box);
  try {
    const r = await api("/api/android/scan", {});
    renderAdbScan(r.hosts || []);
  } catch (e) {
    box.innerHTML = "";
    toast(e.message);
  }
}

function adbScanRow(h) {
  const row = document.createElement("div");
  row.className = "atvrow";
  const left = document.createElement("div");
  left.className = "atvname";
  // 广播内容可被伪造 → textContent，禁止 innerHTML
  left.appendChild(document.createTextNode("📺 " + h.host + " "));
  const ip = document.createElement("span");
  ip.className = "atvip";
  ip.textContent = "无线调试" + (h.pairing ? "（未配对）" : "");
  left.appendChild(ip);
  const btns = document.createElement("div");
  btns.className = "atvbtns";

  const conn = document.createElement("button");
  conn.className = "btn primary tiny";
  conn.textContent = "连接";
  conn.onclick = () => connect(h.host + ":" + (h.connect || 5555));
  btns.appendChild(conn);

  if (h.pairing) {
    const pair = document.createElement("button");
    pair.className = "btn tiny";
    pair.textContent = "配对";
    pair.title = "Android 11+ 无线调试首次使用要先配对（码在电视设置里）";
    pair.onclick = async () => {
      const code = prompt("在电视「设置 → 网络调试」里查看 6 位配对码：", "");
      if (!code) return;
      pair.disabled = true;
      try {
        await api("/api/android/pair", { host: h.host, port: h.pairing, code });
        log("已配对 " + h.host);
        toast("配对成功，点「连接」", true);
      } catch (e) {
        log("⚠ " + e.message);
        toast(e.message);
      } finally {
        pair.disabled = false;
      }
    };
    btns.appendChild(pair);
  }
  row.appendChild(left);
  row.appendChild(btns);
  return row;
}

function renderAdbScan(hosts) {
  const box = $("#adbScanList");
  box.innerHTML = "";
  if (!hosts.length) {
    box.innerHTML = '<p class="hint">未发现无线调试设备：确认电视「设置 → 网络调试」已打开；老电视只开 5555 端口时不广播 mDNS，请直接输 IP。</p>';
    return;
  }
  hosts.forEach((h) => box.appendChild(adbScanRow(h)));
}

$("#scanAdbBtn").addEventListener("click", adbScan);

/* ---------------- ADBKeyboard 中文键盘 ---------------- */
function renderIme(st) {
  Object.assign(imeState, st || {}, { checked: true });
  const el = $("#imeState");
  if (status.curType === "appletv") {
    $("#imeRow").classList.add("hidden");
    $("#imeHint").classList.add("hidden");
    $("#imeInstall").classList.add("hidden");
    return;
  }
  $("#imeRow").classList.remove("hidden");
  $("#imeHint").classList.remove("hidden");

  let cls = "imestate", label = "未检测";
  if (imeState.current) { cls += " ok"; label = "已启用 · 可输中文"; }
  else if (imeState.installed) { cls += " warn"; label = "已安装 · 未切换"; }
  else { cls += " off"; label = "电视上未安装"; }
  el.className = cls;
  el.textContent = label;
  el.title = imeState.default_ime ? "当前输入法：" + imeState.default_ime : "";

  $("#imeEnableBtn").classList.toggle("hidden", !!imeState.current);
  $("#imeResetBtn").classList.toggle("hidden", !imeState.current);
  $("#imeInstall").classList.toggle("hidden", !!imeState.installed);

  // 输入框提示随能力变化，避免用户打完中文才发现发不出去
  $("#textInput").placeholder = imeState.current
    ? "在此打字，回车发送到电视（支持中文 / Emoji）"
    : "在此打字，回车发送到电视（中文需先启用 ADBKeyboard）";
}

async function refreshIme() {
  if (status.curType === "appletv") return renderIme(null);
  try {
    renderIme(await api("/api/ime", { action: "status" }));
  } catch (e) {
    imeState.current = false;
    $("#imeState").className = "imestate off";
    $("#imeState").textContent = "未连接";
  }
}

async function imeEnable() {
  log("正在切换电视输入法…");
  $("#imeEnableBtn").disabled = true;
  try {
    const st = await api("/api/ime", { action: "enable" });
    renderIme(st);
    if (st.ok) {
      toast("已切到 ADBKeyboard，可以输中文了", true);
      log("输入法已切换");
    } else {
      const msg = st.hint || "切换未生效，请看电视屏幕确认";
      toast(msg, true);
      log("⚠ " + msg);
    }
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  } finally {
    $("#imeEnableBtn").disabled = false;
  }
}

/* ---------------- Apple TV ---------------- */
async function atvScan() {
  const box = $("#atvList");
  skeletonRows(box);
  try {
    const r = await api("/api/atv/scan", {});
    renderAtvFound(r.devices || []);
  } catch (e) {
    box.innerHTML = "";
    toast(e.message);
  }
}

function atvRow(dev) {
  const row = document.createElement("div");
  row.className = "atvrow";
  const left = document.createElement("div");
  left.className = "atvname";
  // 设备名 / IP 来自局域网广播，可被伪造 → 一律走 textContent，禁止拼 innerHTML
  left.appendChild(document.createTextNode(`🍎 ${dev.name || "Apple TV"} `));
  const ip = document.createElement("span");
  ip.className = "atvip";
  ip.textContent = dev.ip || "";
  left.appendChild(ip);
  const btns = document.createElement("div");
  btns.className = "atvbtns";
  if (dev.paired !== false) {
    const b = document.createElement("button");
    b.className = "btn primary";
    b.textContent = "连接";
    b.onclick = () => atvConnect(dev);
    btns.appendChild(b);
  } else {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = "配对";
    b.onclick = () => atvPairBegin(dev);
    btns.appendChild(b);
  }
  if (dev.stored) {
    const x = document.createElement("button");
    x.className = "btn";
    x.textContent = "✕";
    x.title = "删除已配对设备";
    x.onclick = async () => {
      try {
        await api("/api/atv/forget", { id: dev.id });
        refreshStatus();
      } catch (e) {
        toast(e.message);   // 没有 catch 的话失败会变成静默的 unhandled rejection
      }
    };
    btns.appendChild(x);
  }
  row.appendChild(left);
  row.appendChild(btns);
  return row;
}

function renderAtvFound(devs) {
  const box = $("#atvList");
  box.innerHTML = "";
  if (!devs.length) {
    box.innerHTML = '<p class="hint">未发现 Apple TV：确认电视与本机同网段、已唤醒（Apple TV 3 及更早型号不支持）。</p>';
    return;
  }
  devs.forEach((d) => box.appendChild(atvRow(d)));
}

function renderAtvKnown(s) {
  const box = $("#atvList");
  if (!box.dataset.scanned) return; // 扫描结果优先展示，未扫描时展示已配对
  renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })));
}

async function atvConnect(dev) {
  log(`正在连接 Apple TV ${dev.name || dev.ip} …`);
  try {
    await api("/api/atv/connect", { id: dev.id, ip: dev.ip, name: dev.name });
    log(`已连接 🍎 ${dev.name || dev.ip}`);
    await refreshStatus();
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
}

async function atvPairBegin(dev) {
  pairingDev = dev;
  log(`正在向 ${dev.name || dev.ip} 发起配对 …`);
  try {
    const r = await api("/api/atv/pair", { action: "begin", ...dev });
    $("#pairName").textContent = `${dev.name || dev.ip}`;
    $("#pinInput").value = "";
    $("#pairBox").classList.remove("hidden");
    $("#pinInput").focus();
    toast(r.hint || "请在电视屏幕上查看 PIN 码", true);
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
}

async function atvPairFinish() {
  const pin = $("#pinInput").value.trim();
  if (!pin) return toast("请输入电视屏幕上显示的 PIN 码");
  try {
    await api("/api/atv/pair", { action: "finish", ...pairingDev, pin });
    $("#pairBox").classList.add("hidden");
    log(`配对成功：${pairingDev.name || pairingDev.ip}`);
    toast("配对成功，正在连接…", true);
    await api("/api/atv/connect", { id: pairingDev.id, ip: pairingDev.ip, name: pairingDev.name });
    await refreshStatus();
    await atvScan();
  } catch (e) {
    toast(e.message);
  }
}

async function atvPairCancel() {
  $("#pairBox").classList.add("hidden");
  try { await api("/api/atv/pair", { action: "stop" }); } catch { /* ignore */ }
}

async function loadAtvApps() {
  const box = $("#atvApps");
  skeletonRows(box, 4);
  try {
    const r = await api("/api/atv/apps", {});
    box.innerHTML = "";
    (r.apps || []).forEach((a) => {
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = a.name || a.id;
      b.onclick = async () => {
        try { await api("/api/cmd", { type: "app", pkg: a.id }); log(`→ 启动 ${b.textContent}`); }
        catch (e) { toast(e.message); }
      };
      box.appendChild(b);
    });
    if (!box.children.length) box.innerHTML = '<span class="hint">未获取到应用列表</span>';
  } catch (e) {
    box.innerHTML = "";
    toast(e.message);
  }
}

/* ---------------- 键盘全局遥控 ---------------- */
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return; // 输入框内正常打字
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const code = KEYMAP[e.key];
  if (!code) return;
  e.preventDefault();
  if (status.curType === "appletv" && (code === 67 || code === 112)) {
    return; // Apple TV 无删除键，避免误报（PageUp/Down = 快退/快进）
  }
  sendKey(code);
});

/* ---------------- 控件绑定 ---------------- */
// 长按连发：滚列表、调音量是遥控最高频动作，单发要点到手酸。
// 450ms 起发、之后每 120ms 一次；指针抬起/离开/页面隐藏/失焦立即停。
// 只有方向、音量、seek 允许连发（电源/静音/主页连发会翻转状态，见 HOLD_KEYS）。
// 键盘 Enter/Space 触发 click 时没有 pointerdown，靠 lastHoldSend 时间戳防双重发送。
const HOLD_DELAY = 450, HOLD_RATE = 120;
const HOLD_KEYS = new Set([19, 20, 21, 22, 23, 24, 25, 87, 88, 89, 90]);

// 当前按住的键的停止函数。pointerup 必须「在窗口任何位置收到都停」——手指滑出按钮、
// 页面滚动、系统吞事件时，只靠按钮自己的 pointerup/leave 会漏，interval 就变孤儿
// （实测漏过一次：松手后按键还在连发，只能刷新页面）。
let activeHoldStop = null;
const stopActiveHold = () => { if (activeHoldStop) { activeHoldStop(); activeHoldStop = null; } };
["pointerup", "pointercancel", "blur"].forEach((ev) => window.addEventListener(ev, stopActiveHold));
document.addEventListener("visibilitychange", () => { if (document.hidden) stopActiveHold(); });

$$("[data-key]").forEach((btn) => {
  const code = +btn.dataset.key;
  let hold = null, rep = null;
  const stop = () => {
    clearTimeout(hold); clearInterval(rep); hold = rep = null;
    btn.classList.remove("pressed");
    if (activeHoldStop === stop) activeHoldStop = null;
  };
  const tick = () => sendKey(code);

  btn.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    stopActiveHold();                 // 换键重按：先停掉上一个
    buzz();                           // 先震后发：延迟由网络决定，反馈不能等
    sendKey(code);
    btn.classList.add("pressed");     // 按压态全程保持，stop() 里摘掉
    if (!HOLD_KEYS.has(code)) return;
    hold = setTimeout(() => { rep = setInterval(tick, HOLD_RATE); }, HOLD_DELAY);
    activeHoldStop = stop;
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) =>
    btn.addEventListener(ev, stop));
  // click 只服务键盘可达性（Enter/Space，detail===0）；指针点击已在 pointerdown 发过。
  // 用 detail 而不是时间戳判重：慢按（按住 200ms 再松）会越过任何时间窗，双重发送。
  btn.addEventListener("click", (e) => {
    if (e.detail !== 0) return;
    buzz();
    sendKey(code);
  });
});

$("#settingsBtn").addEventListener("click", async () => {
  try { await api("/api/cmd", { type: "settings" }); log("→ 打开电视设置"); }
  catch (e) { toast(e.message); }
});

$("#connectBtn").addEventListener("click", () => connect());
$("#targetInput").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("#disconnectBtn").addEventListener("click", async () => {
  try {
    if (status.curType === "appletv") await api("/api/atv/disconnect", {});
    else await api("/api/disconnect", {});
    log("已断开");
  } catch (e) { toast(e.message); }
  refreshStatus();
});

/* Apple TV 控件 */
$("#scanBtn").addEventListener("click", atvScan);
$("#pairFinishBtn").addEventListener("click", atvPairFinish);
$("#pairCancelBtn").addEventListener("click", atvPairCancel);
$("#pinInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") atvPairFinish();
  if (e.key === "Escape") atvPairCancel();
});
$("#loadAppsBtn").addEventListener("click", loadAtvApps);

/* 设备类型页签 */
$$(".devtab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".devtab").forEach((x) => x.classList.remove("on"));
    t.classList.add("on");
    $$(".devpane").forEach((p) => p.classList.remove("on"));
    $("#pane-" + t.dataset.dev).classList.add("on");
    refreshIme(); // 页签切换会改变键盘区的显隐，重新按当前设备类型渲染
    if (t.dataset.dev === "appletv") {
      const box = $("#atvList");
      if (!box.dataset.scanned) {
        api("/api/status").then((s) => {
          if ((s.appletv.devices || []).length) {
            box.dataset.scanned = "1";
            renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })));
          }
        });
      }
    }
  });
});

/* 键盘输入区 */
$("#sendBtn").addEventListener("click", () => sendText($("#textInput").value, false));
$("#sendEnterBtn").addEventListener("click", () => sendText($("#textInput").value, true));
$("#textInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); sendText($("#textInput").value, true); }
  if (e.key === "Escape") e.target.blur();
});

/* 中文键盘（ADBKeyboard）控件 */
$("#imeEnableBtn").addEventListener("click", imeEnable);
$("#imeResetBtn").addEventListener("click", async () => {
  try {
    renderIme(await api("/api/ime", { action: "reset" }));
    toast("已切回电视系统输入法", true);
    log("输入法已还原");
  } catch (e) { toast(e.message); }
});
$("#clearBtn").addEventListener("click", async () => {
  try { await api("/api/cmd", { type: "clear" }); log("→ 清空电视输入框"); }
  catch (e) { toast(e.message); }
});
$("#searchBtn").addEventListener("click", async () => {
  try { await api("/api/cmd", { type: "editor", code: 3 }); log("→ 触发电视搜索（IME_ACTION_SEARCH）"); }
  catch (e) { toast(e.message); }
});

/* Android 应用预设 */
const appsEl = $("#apps");
APPS.forEach((a) => {
  const b = document.createElement("button");
  b.className = "btn";
  b.textContent = a.name;
  b.onclick = async () => {
    try { await api("/api/cmd", { type: "app", pkg: a.pkg }); log(`→ 启动 ${a.name}`); }
    catch (e) { log("⚠ " + e.message); toast(e.message); }
  };
  appsEl.appendChild(b);
});
$("#pkgBtn").addEventListener("click", async () => {
  const pkg = $("#pkgInput").value.trim();
  if (!pkg) return;
  try { await api("/api/cmd", { type: "app", pkg }); log(`→ 启动 ${pkg}`); }
  catch (e) { toast(e.message); }
});
$("#pkgInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#pkgBtn").click();
});

/* tabs */
$$(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.remove("on"));
    t.classList.add("on");
    $$(".tabpane").forEach((p) => p.classList.remove("on"));
    $("#pane-" + t.dataset.tab).classList.add("on");
  });
});

/* ---------------- 触摸板 ---------------- */
const pad = $("#touchpad");
let ptr = null;
pad.addEventListener("pointerdown", (e) => {
  pad.setPointerCapture(e.pointerId);
  const r = pad.getBoundingClientRect();
  ptr = { x0: e.clientX - r.left, y0: e.clientY - r.top, x1: e.clientX - r.left, y1: e.clientY - r.top, t0: performance.now() };
  pad.classList.add("dragging");
});
pad.addEventListener("pointermove", (e) => {
  if (!ptr) return;
  const r = pad.getBoundingClientRect();
  ptr.x1 = e.clientX - r.left;
  ptr.y1 = e.clientY - r.top;
});
pad.addEventListener("pointerup", async () => {
  if (!ptr) return;
  pad.classList.remove("dragging");
  const { x0, y0, x1, y1, t0 } = ptr;
  ptr = null;
  const r = pad.getBoundingClientRect();
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const nx = (p) => Math.min(1, Math.max(0, p / r.width));
  const ny = (p) => Math.min(1, Math.max(0, p / r.height));
  try {
    if (dist < 12) {
      await api("/api/cmd", status.curType === "appletv"
        ? { type: "tap" }
        : { type: "tap", x: Math.round(nx(x0) * (status.screen.w - 1)), y: Math.round(ny(y0) * (status.screen.h - 1)) });
      log(`→ tap ${Math.round(nx(x0) * 100)}%,${Math.round(ny(y0) * 100)}%`);
    } else {
      const dur = Math.min(1500, Math.max(120, performance.now() - t0));
      const body = status.curType === "appletv"
        ? { type: "swipe", x1: nx(x0), y1: ny(y0), x2: nx(x1), y2: ny(y1), duration: dur }
        : { type: "swipe", x1: Math.round(nx(x0) * (status.screen.w - 1)), y1: Math.round(ny(y0) * (status.screen.h - 1)),
            x2: Math.round(nx(x1) * (status.screen.w - 1)), y2: Math.round(ny(y1) * (status.screen.h - 1)), duration: dur };
      await api("/api/cmd", body);
      log(`→ swipe (${Math.round(x0)},${Math.round(y0)})→(${Math.round(x1)},${Math.round(y1)})`);
    }
  } catch (e) {
    toast(e.message);
  }
});
pad.addEventListener("pointercancel", () => { ptr = null; pad.classList.remove("dragging"); });

/* ---------------- 截屏 / 画面 ---------------- */
let shotUrl = null; // 上一次截屏的 blob URL，必须显式释放否则每次截屏都泄漏一张 PNG

function setShot(url) {
  if (shotUrl) URL.revokeObjectURL(shotUrl); // 释放上一张，避免内存泄漏
  shotUrl = url;
  $("#shotImg").src = url;
  $("#shotSave").href = url;
}

$("#shotBtn").addEventListener("click", async () => {
  log(status.curType === "appletv" ? "正在获取画面…" : "正在截屏…");
  try {
    const r = await fetch("/api/screenshot", { headers: { ...TOKEN_HDR } });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || "获取画面失败");
    }
    setShot(URL.createObjectURL(await r.blob()));
    $("#shotModal").classList.remove("hidden");
    log("完成");
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
});
$("#shotClose").addEventListener("click", () => $("#shotModal").classList.add("hidden"));
$("#shotModal").addEventListener("click", (e) => {
  if (e.target === $("#shotModal")) $("#shotModal").classList.add("hidden");
});

loadMacros();

/* ---------------- 屏幕常亮（Wake Lock） ----------------
   遥控器打开着就是在看电视，中途熄屏要解锁很烦。Screen Wake Lock 在
   Chrome/Edge/Android WebView 可用；iOS Safari 尚不支持 → 静默跳过，
   不影响任何其他功能。需要一次用户手势才能申请，所以在首个 pointerdown 时发起。 */
let wakeLock = null;
async function keepAwake() {
  try {
    if (!("wakeLock" in navigator)) return;
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch (e) { /* 被拒绝或不可用：遥控本身不受影响 */ }
}
document.addEventListener("pointerdown", () => { if (!wakeLock) keepAwake(); }, { once: true });
// 切回前台时锁可能已被释放，重新申请（同样要求手势栈里有交互，实测可直接调）
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !wakeLock) keepAwake();
});

/* ---------------- 手机安装引导 ---------------- */
const installCmd = `curl -sL ${location.origin}/install${TOKEN_Q} | bash`;
$("#installCmd").value = installCmd;
$("#qrImg").src = "/api/qr.svg?text=" + encodeURIComponent(installCmd) + TOKEN_AMP;
$("#qrImg").onerror = () => { document.querySelector(".qrbox").style.display = "none"; }; // 无 qrcode 库时隐藏
// 未启用令牌时 setupQrBox 保持 hidden；启用后这里换成「带令牌的页面地址」二维码，
// 手机扫一次即完成首次接入（服务端会种 cookie，之后不再需要令牌）
if (ATV_TOKEN) {
  api("/api/setup").then((j) => {
    if (!j.token) return;
    $("#setupQrBox").hidden = false;
    $("#setupQrImg").src = "/api/qr.svg?text=" + encodeURIComponent(j.url + "?token=" + j.token) + TOKEN_AMP;
  }).catch(() => {});
}

// 启用令牌后，APK 直链与手机访问地址都得带上它
const apkLink = $("#apkLink");
if (apkLink) apkLink.href = "/app.apk" + TOKEN_Q;
if (ATV_TOKEN) {
  const tip = document.createElement("p");
  tip.className = "hint";
  tip.textContent = "🔒 本机已启用访问令牌，手机浏览器请打开：" +
    location.origin + "/?token=" + ATV_TOKEN + "（本机 127.0.0.1 访问免令牌）";
  const box = $("#phoneInstall");
  box.insertBefore(tip, box.querySelector("ol"));
}
$("#copyCmd").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(installCmd);
  } catch { // http 非安全上下文回退
    const i = $("#installCmd");
    i.select();
    document.execCommand("copy");
  }
  toast("已复制！打开 Termux 粘贴回车即可", true);
});

window.addEventListener("pagehide", () => {
  if (shotUrl) URL.revokeObjectURL(shotUrl);
});

/* ---------------- 启动 ---------------- */
let pageVisible = true;
document.addEventListener("visibilitychange", () => {
  pageVisible = !document.hidden;
  if (pageVisible) refreshStatus();
});

/* ---------------- App 设置 ----------------
// 偏好全部存 localStorage：这些是「这一侧浏览器」的设置，与电视无关，不进 state.json。
// 第一版只放震动开关与清空本地数据；以后要加偏好都往这个弹窗里挂。 */
function renderHapticBtn() {
  const b = $("#hapticBtn");
  const on = hapticEnabled();
  b.textContent = on ? "开" : "关";
  b.setAttribute("aria-pressed", on ? "true" : "false");
  b.classList.toggle("on", on);
}
$("#hapticBtn").addEventListener("click", () => {
  localStorage.setItem(HAPTIC_KEY, hapticEnabled() ? "0" : "1");
  renderHapticBtn();
  if (hapticEnabled()) buzz(20);   // 打开时立刻震一下，让用户知道效果
});
$("#clearPhrasesBtn").addEventListener("click", () => {
  localStorage.removeItem(PHRASE_KEY);
  phrases = DEFAULT_PHRASES.slice();
  renderPhrases();
  $("#phraseCount").textContent = phrases.length;
  toast("常用短语已重置为默认", true);
});
$("#clearMacrosBtn").addEventListener("click", () => {
  localStorage.removeItem(MACRO_LS);
  loadMacros();
  toast("自定义宏已清空", true);
});
$("#appSettingsBtn").addEventListener("click", () => {
  $("#phraseCount").textContent = phrases.length;
  renderHapticBtn();
  $("#settingsModal").classList.remove("hidden");
});
$("#settingsCloseBtn").addEventListener("click", () => $("#settingsModal").classList.add("hidden"));
$("#settingsModal").addEventListener("click", (e) => {
  if (e.target === $("#settingsModal")) $("#settingsModal").classList.add("hidden");
});

applyPrivacy();
renderPhrases();
refreshStatus();
setInterval(() => { if (pageVisible) refreshStatus(); }, 8000);
