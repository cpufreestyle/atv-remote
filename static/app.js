/* ATV Remote 前端逻辑（Android TV + Apple TV） */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

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

/* ---------------- 基础 ---------------- */
async function api(path, body) {
  const init = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

function log(msg) {
  const el = $("#log");
  el.textContent = msg;
  el.style.color = msg.startsWith("⚠") ? "#ff9a9a" : "";
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
    toast("⚠ Android TV 的 adb 输入不支持中文等非 ASCII 字符（Apple TV 支持中文）");
    return;
  }
  try {
    await api("/api/cmd", { type: "text", text, enter: !!withEnter });
    log(`→ text "${text.slice(0, 30)}"${text.length > 30 ? "…" : ""}${withEnter ? " + Enter" : ""}`);
    $("#textInput").value = "";
    $("#textInput").blur(); // 发送后回到全局键盘遥控状态
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
}

/* ---------------- 状态与连接 ---------------- */
async function refreshStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch { /* ignore */ }
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

  // Apple TV 输入框聚焦徽标
  const badge = $("#kbFocus");
  if (isApple && s.appletv.kb_focus === "Focused") badge.classList.remove("hidden");
  else badge.classList.add("hidden");

  // 按设备类型调整 UI
  $("#settingsBtn").classList.toggle("hidden", isApple);
  $("#appsAndroid").classList.toggle("hidden", isApple);
  $("#appsApple").classList.toggle("hidden", !isApple);
  $("#shotBtn").textContent = isApple ? "🖼 正在播放画面" : "📸 电视截屏";
  $("#textInput").placeholder = isApple
    ? "在此打字，回车发送到电视（Apple TV 支持中文）"
    : "在此打字，回车发送到电视（英文/数字/符号）";
  $("#padHint").textContent = isApple
    ? "轻点 = 点击 · 按住拖动 = 滑动（Apple TV 触控）"
    : "轻点 = 点击 · 按住拖动 = 滑动（映射整块电视屏幕）";
  $("#kbdHint").innerHTML = isApple
    ? '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>PageUp/Down</b> 快退/快进 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送（支持中文）。'
    : '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>退格</b>=删除 · <b>PageUp/Down</b> 翻页 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送。';

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

/* ---------------- Apple TV ---------------- */
async function atvScan() {
  const box = $("#atvList");
  box.innerHTML = '<p class="hint">正在扫描（约 5 秒）…</p>';
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
  left.innerHTML = `🍎 ${dev.name || "Apple TV"} <span class="atvip">${dev.ip || ""}</span>`;
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
    x.onclick = () => api("/api/atv/forget", { id: dev.id }).then(() => { renderAtvKnown(); refreshStatus(); });
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
  box.innerHTML = '<span class="hint">加载中…</span>';
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
$$("[data-key]").forEach((btn) => {
  btn.addEventListener("click", () => sendKey(+btn.dataset.key));
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
    if (t.dataset.dev === "appletv" && !$("#atvList").dataset.scanned) {
      api("/api/status").then((s) => {
        if ((s.appletv.devices || []).length) renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })));
      });
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
$("#shotBtn").addEventListener("click", async () => {
  log(status.curType === "appletv" ? "正在获取画面…" : "正在截屏…");
  try {
    const r = await fetch("/api/screenshot");
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || "获取画面失败");
    }
    const url = URL.createObjectURL(await r.blob());
    $("#shotImg").src = url;
    $("#shotSave").href = url;
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

/* ---------------- 手机安装引导 ---------------- */
const installCmd = `curl -sL ${location.origin}/install | bash`;
$("#installCmd").value = installCmd;
$("#qrImg").src = "/api/qr.svg?text=" + encodeURIComponent(installCmd);
$("#qrImg").onerror = () => { document.querySelector(".qrbox").style.display = "none"; }; // 无 qrcode 库时隐藏
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

/* ---------------- 启动 ---------------- */
refreshStatus();
setInterval(refreshStatus, 8000);
