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
let palStatus = null; // 最近一次 /api/status 快照：命令面板的设备/宏来源
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
/* 语音意图识别：说的话先尝试理解成命令，听不懂才原样发给电视。
   默认开（原来语音只能当字面文本，说「声音小一点」会打进搜索框）；
   误识别烦人可关，关掉后行为与加这个功能之前完全一致。偏好存 localStorage。 */
const INTENT_KEY = "atv.intent";
function intentEnabled() { return localStorage.getItem(INTENT_KEY) !== "0"; }
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

/* ===== notif-queue:begin =====
   通知队列纯函数段：禁 DOM / localStorage / fetch（后面有测试盯着，node harness 直接抽这段执行）。
   学 macOS 通知中心 / VS Code 通知：瞬时浮层只回答「刚刚发生了什么」，持久历史回答「刚才那条说啥」；
   同屏条数有上限，超出的折成未读数，而不是一条盖一条地把上一条悄悄吃掉（旧 toast 就是这么丢消息的）。
   三条口径：
   1) 同屏最多 3 条，最新的永远可见；被挤下去的并未消失——进了历史，🔔 上有未读数。
   2) 停留时长随级别拉长（成功短、错误久），读得慢的人由通知中心兜底，不靠加长倒时计。
   3) 连续同一消息 1.2s 内合并不重复入账，别把历史刷成复读机。 */
const NOTIF_MAX_VISIBLE = 3;
const NOTIF_DUR = { ok: 2600, info: 3400, err: 7000 };
const NOTIF_LEVELS = ["ok", "info", "err"];
const NOTIF_HISTORY_KEY = "atv.notif.history";
const NOTIF_SEEN_KEY = "atv.notif.seen";
const NOTIF_HISTORY_MAX = 50;
const NOTIF_COALESCE_MS = 1200;
const NOTIF_MSG_MAX = 200;

function notifNormLevel(level) {
  return NOTIF_LEVELS.indexOf(level) >= 0 ? level : "err";
}

// 级别从文案推：调用点几十处，不该为了配色去逐个传第三个参数。
// isInfo 沿用旧 toast 的「这是条好消息」语义；其余按内容判成功/失败。
function notifLevel(msg, isInfo) {
  if (isInfo === true) return "info";
  const s = String(msg === null || msg === undefined ? "" : msg);
  if (s.indexOf("✓") === 0 || s.indexOf("✅") === 0 || s.indexOf("成功") >= 0) return "ok";
  if (s.indexOf("失败") >= 0 || s.indexOf("错误") >= 0 || s.indexOf("⚠") >= 0 || s.indexOf("✕") >= 0) return "err";
  return s.indexOf("已") === 0 ? "ok" : "err";
}

function notifDur(level) {
  const d = NOTIF_DUR[notifNormLevel(level)];
  return typeof d === "number" ? d : NOTIF_DUR.err;
}

// items 为到达顺序（旧 → 新）。visible 取最后 max 条并翻成「新 → 旧」方便渲染，
// hidden 是被挤出屏幕、只剩历史可查的条数。
function notifPlan(items, maxVisible) {
  const arr = Array.isArray(items) ? items.slice() : [];
  const max = typeof maxVisible === "number" && maxVisible > 0 ? Math.floor(maxVisible) : NOTIF_MAX_VISIBLE;
  const start = Math.max(0, arr.length - max);
  return { visible: arr.slice(start).reverse(), hidden: Math.max(0, arr.length - max) };
}

function notifCoalesce(a, b) {
  if (!a || !b) return false;
  if (a.msg !== b.msg || notifNormLevel(a.level) !== notifNormLevel(b.level)) return false;
  return Math.abs((b.ts || 0) - (a.ts || 0)) <= NOTIF_COALESCE_MS;
}

// 纯函数：返回新数组（旧引用不变），最新在前、超量截断。合并时刷新时间戳而不是插一条。
function notifPushHistory(list, item, max) {
  const cap = typeof max === "number" && max > 0 ? Math.floor(max) : NOTIF_HISTORY_MAX;
  const arr = (Array.isArray(list) ? list : []).slice();
  if (arr.length && notifCoalesce(arr[0], item)) { arr[0] = item; return arr.slice(0, cap); }
  arr.unshift(item);
  return arr.slice(0, cap);
}

function notifUnread(list, lastSeenTs) {
  const seen = typeof lastSeenTs === "number" && lastSeenTs > 0 ? lastSeenTs : 0;
  return (Array.isArray(list) ? list : []).filter((it) => it && (it.ts || 0) > seen).length;
}

function notifFmtTime(ts) {
  const d = new Date(typeof ts === "number" && ts > 0 ? ts : Date.now());
  const p = (n) => (n < 10 ? "0" : "") + n;
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

// localStorage 里的历史是不可信输入：字段缺失/类型错/超长都要能兜住，否则一条坏数据
// 会让整个通知中心打不开（JSON.parse 抛在加载路径上）。
function notifSanitizeHistory(list, max) {
  const cap = typeof max === "number" && max > 0 ? Math.floor(max) : NOTIF_HISTORY_MAX;
  return (Array.isArray(list) ? list : [])
    .filter((it) => it && typeof it.msg === "string" && it.msg.trim())
    .map((it) => ({
      msg: it.msg.slice(0, NOTIF_MSG_MAX),
      level: notifNormLevel(it.level),
      ts: typeof it.ts === "number" && it.ts > 0 ? it.ts : 0,
    }))
    .slice(0, cap);
}
/* ===== notif-queue:end ===== */

/* ===== wake-on-lan:begin =====
   Wake-on-LAN 纯函数段：禁 DOM / localStorage / fetch（tests/wol_harness.js 直接抽这段执行）。
   动机是遥控器的唯一真盲区：电视关机后 adb 掉线，设备列表里连这台都没有，方向键 / 截屏 /
   宏全都无从谈起。所以「远程开机」不是又一个按钮，而是三件事的组合：从本机 ARP 表反查
   MAC（省得用户去路由器后台手抄）、按惯例双端口发包、发包后守着状态等它自己上线并重连。
   三条口径：
   1) MAC 宽容解析与后端同口径——用户抄回来的格式五花八门，解析不了要说清而不是硬发；
   2) 等待必须有上限（24 次 × 2.5s = 60s）且可中止，否则一次误唤醒会永久占着轮询；
   3) IP / MAC 一律交给 textContent——局域网广播可伪造，渲染路径上出现 innerHTML 即违约。 */
const WOL_WATCH_MS = 2500;
const WOL_WATCH_MAX = 24;      // 24 × 2.5s = 60s 上限
const WOL_IP_MAX = 8;          // 一次最多问这么多 IP（后端上限 20，这里留余量）
const WOL_ASK_MIN_MS = 15000;  // 自动发现的最小间隔：ARP 表秒级不变，别跟着 8s 轮询刷

// 与 server.py 的 wol_norm_mac 同一口径：全收冒号 / 连字符 / 点分 / 无分隔，但不猜位数
function wolNormMac(v) {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase().replace(/-/g, ":").replace(/[.]/g, ":");
  const mac = s.split(":").filter((p) => p).join("");
  if (!/^[0-9a-f]{12}$/.test(mac)) return null;
  const g = [];
  for (let i = 0; i < 12; i += 2) g.push(mac.slice(i, i + 2));
  return g.join(":");
}

// adb 无线调试目标是 host:port（192.168.0.52:5555），ARP 表里只有 IP
function wolIpOf(t) {
  return String(t === null || t === undefined ? "" : t).split(":")[0].trim().toLowerCase();
}

// 从一次 /api/status 快照里取「值得问一遍」的 IP：当前设备 + 最近连过，去重、限量。
// appletv 的 current 是 uuid，没有点号，天然被过滤掉——Apple TV 不走 adb，也就没有 ARP 条目。
function wolIpsFromStatus(s) {
  const st = s || {};
  const out = [];
  const add = (v) => {
    const ip = wolIpOf(v);
    if (ip && ip.indexOf(".") >= 0 && out.indexOf(ip) < 0 && out.length < WOL_IP_MAX) out.push(ip);
  };
  add(st.current);
  (Array.isArray(st.recent) ? st.recent : []).forEach(add);
  return out;
}

// 每次探测的结果怎么解释：上线了就停并收尾；attempt 用满还没上就停并报超时。
// online 优先于 attempt——别为了跑满次数把已经上线的设备再晾 30 秒。
function wolWatchPlan(attempt, online) {
  if (online === true) return { stop: true, why: "online" };
  if (attempt >= WOL_WATCH_MAX) return { stop: true, why: "timeout" };
  return { stop: false, why: "waiting" };
}

// 问了却没拿到 MAC 的 IP：要么关机后从没跟本机通信过（ARP 表里就没有它），
// 要么它不在同一个二层网络里。这两种要分开说，用户才知道该去开机还是去查网络。
function wolMissing(asked, found) {
  const has = [];
  (Array.isArray(found) ? found : []).forEach((t) => { if (t && t.ip) has.push(t.ip); });
  return (Array.isArray(asked) ? asked : []).filter((ip) => has.indexOf(ip) < 0);
}

// MAC 只承担「确认是这台」的辅助信息：缩成 aa:bb:cc…ee:ff，别整条糊在按钮上
function wolMacShort(mac) {
  const g = String(mac === null || mac === undefined ? "" : mac).split(":").filter((p) => p);
  return g.length === 6 ? g.slice(0, 3).join(":") + "…" + g.slice(4).join(":") : String(mac || "");
}

// 徽标一个数字说不清三件事：发现几台、其中几台真有 MAC、还缺几台
function wolSummary(targets) {
  const arr = Array.isArray(targets) ? targets : [];
  const withMac = arr.filter((t) => t && t.mac).length;
  return { total: arr.length, withMac: withMac, missing: arr.length - withMac };
}
/* ===== wake-on-lan:end ===== */

/* ===== favorites:begin =====
   收藏夹 / 快捷启动纯函数段：禁 DOM / localStorage / fetch / innerHTML / $(
   （tests/fav_harness.js 直接抽这段执行）。
   学的是三家的同一个观察：浏览器 Speed Dial 把天天进的站钉在新标签页，VS Code
   Favorites 让常用文件不靠回忆路径，Plex 的「Continue Watching」把没看完的顶到第一排。
   遥控器上的对应物就是那三五个 App——功能越加越多，用户一天里真正点的还是它们。
   所以收藏夹不是新能力，而是把既有的启动通道（launchApp → /api/cmd 的 type=app）
   缩短到一键：固定常用、顺序即优先级、点到即播。零新增后端。
   三条口径：
   1) 持久化只落浏览器 localStorage（键 atv.favApps）——state.json 会被打进
      bundle.tgz 分发给局域网任何设备，可重放内容不该进去；
   2) 数量必须设上限：收藏夹超过一屏就退化成「全部应用」，失去「省得找」的意义；
      满员时拒绝并让调用方提示，绝不静默丢弃；
   3) 名字解析逐级兜底（pin 自带 → 预设表 → 最近使用 → 包名末段）——坏数据不炸，
      也不把 com.google.android.youtube.tv 这种长串糊在按钮上。 */
const FAV_MAX = 8;                 // 上限：超过一屏的收藏夹没有「省得找」的意义
function favNorm(list) {
  const out = [];
  if (!Array.isArray(list)) return out;
  const seen = {};
  for (let i = 0; i < list.length && out.length < FAV_MAX; i++) {
    const it = list[i];
    if (!it || typeof it !== "object") continue;
    const pkg = typeof it.pkg === "string" ? it.pkg.trim() : "";
    if (!pkg || seen[pkg]) continue;
    seen[pkg] = 1;
    const name = typeof it.name === "string" ? it.name.trim() : "";
    out.push({ name: name, pkg: pkg });
  }
  return out;
}
function favIsPinned(list, pkg) {
  return favNorm(list).some((a) => a.pkg === pkg);
}
function favToggle(list, pkg, name) {
  const cur = favNorm(list);
  if (cur.some((a) => a.pkg === pkg)) return cur.filter((a) => a.pkg !== pkg);
  if (cur.length >= FAV_MAX) return null;   // 满员拒绝：调用方 toast，别静默丢
  cur.unshift({ name: typeof name === "string" ? name.trim() : "", pkg: pkg });
  return cur;
}
function favResolve(list, apps, recent) {
  const lookup = {};
  const feed = (arr) => (Array.isArray(arr) ? arr : []).forEach((a) => {
    if (a && typeof a.pkg === "string" && a.pkg && !lookup[a.pkg])
      lookup[a.pkg] = typeof a.name === "string" ? a.name : "";
  });
  feed(apps);
  feed(recent);
  return favNorm(list).map((p) => ({
    pkg: p.pkg,
    name: p.name || lookup[p.pkg] || p.pkg.split(".").slice(-1)[0],
  }));
}
/* ===== favorites:end ===== */

/* ===== padsens:begin ===== */
/* 触摸板灵敏度：学各遥控 App 的指针/滚动增益与游戏灵敏度滑条。双指手势每滑过
   「基础步长 / 增益」px 发一次音量 / seek；单指长按按「基础间隔 / 增益」ms 发一次。
   增益越大越跟手。纯函数可搬进 node 跑 harness；基础值与触摸板手势段常量一一对应。 */
const PADSENS_KEY = "atv.padSens";
const PADSENS_MIN = 0.5, PADSENS_MAX = 2, PADSENS_DEFAULT = 1;
const PADSENS_BASE_STEP = 26;   // = GESTURE_STEP
const PADSENS_BASE_RATE = 120;  // = PAD_HOLD_RATE
const PADSENS_STEP_MIN = 8, PADSENS_STEP_MAX = 64;
const PADSENS_RATE_MIN = 45, PADSENS_RATE_MAX = 400;
function padSensClamp(gain) {
  const n = (typeof gain === "number" && isFinite(gain)) ? gain : PADSENS_DEFAULT;
  return Math.min(PADSENS_MAX, Math.max(PADSENS_MIN, Math.round(n * 20) / 20));
}
function padStepFor(gain) {
  return Math.min(PADSENS_STEP_MAX, Math.max(PADSENS_STEP_MIN, Math.round(PADSENS_BASE_STEP / padSensClamp(gain))));
}
function padHoldRateFor(gain) {
  return Math.min(PADSENS_RATE_MAX, Math.max(PADSENS_RATE_MIN, Math.round(PADSENS_BASE_RATE / padSensClamp(gain))));
}
function padSensLabel(gain) { return (Math.round(padSensClamp(gain) * 100) / 100).toString() + "×"; }
/* ===== padsens:end ===== */

/* ---- 通知队列 DOM 胶水：规则在上面纯函数段，这里只管渲染 / 计时 / 持久化 ---- */
const notifQueue = [];
let notifHistory = [];
let notifLastSeen = 0;
let notifSeq = 0;

function notifStore(key, val) {
  try { localStorage.setItem(key, val); }
  catch (e) { /* 隐私模式 / 配额满：通知不是关键路径，静默降级为「仅本次会话可见」 */ }
}
function notifLoad(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function notifLoadHistory() {
  try { return notifSanitizeHistory(JSON.parse(notifLoad(NOTIF_HISTORY_KEY) || "[]"), NOTIF_HISTORY_MAX); }
  catch (e) { return []; }
}
function notifSaveHistory() { notifStore(NOTIF_HISTORY_KEY, JSON.stringify(notifHistory)); }
function notifLoadSeen() { const v = Number(notifLoad(NOTIF_SEEN_KEY) || "0"); return v > 0 ? v : 0; }
function notifSaveSeen() { notifStore(NOTIF_SEEN_KEY, String(notifLastSeen)); }

function notifMount(n) {
  const box = $("#notifStack");
  if (!box) return;
  const item = document.createElement("div");
  item.className = "ntoast " + n.level;
  const txt = document.createElement("span");
  txt.className = "ntext";
  txt.textContent = n.msg;          // 通知文本可能带设备名 / IP，一律 textContent
  const x = document.createElement("button");
  x.className = "ntof";
  x.type = "button";
  x.textContent = "×";
  x.title = "关闭这条通知";
  x.setAttribute("aria-label", "关闭通知：" + n.msg);
  x.addEventListener("click", (e) => { e.stopPropagation(); notifDismiss(n, true); });
  // 可撤销动作按钮（撤销 / 重试…）：插在文本与关闭键之间，只有带 act 的通知才有它
  const kids = [txt];
  if (n.act && n.act.text) {
    const a = document.createElement("button");
    a.className = "nact";
    a.type = "button";
    a.textContent = n.act.text;      // 文案来自我们自己的常量，永远不是局域网来的数据
    a.addEventListener("click", (e) => {
      e.stopPropagation();
      const act = n.act;
      notifDismiss(n, true);         // 先收通知再跑回调，回调抛异常也不留一条死 toast
      try { if (act && typeof act.onAction === "function") act.onAction(); }
      catch (err) { /* 回调自己负责提示失败，这里只保证不冒泡成未捕获错误 */ }
    });
    kids.push(a);
  }
  kids.push(x);
  kids.forEach((k) => item.append(k));
  n.el = item;
  box.prepend(item);               // 最新的在最上面
}

function notifUnmount(n) {
  if (n && n.el && n.el.parentNode) n.el.parentNode.removeChild(n.el);
  if (n) n.el = null;
}

function notifRender() {
  const plan = notifPlan(notifQueue, NOTIF_MAX_VISIBLE);
  const keep = plan.visible.map((n) => n.seq);
  notifQueue.forEach((n) => { if (keep.indexOf(n.seq) < 0) notifUnmount(n); });
  // 反过来 prepend 后，先插的（旧的）被推到下面 → 视觉顺序 = 新在上
  plan.visible.slice().reverse().forEach((n) => { if (!n.el) notifMount(n); });
  const more = $("#notifMore");
  if (more) {
    if (plan.hidden > 0) {
      more.textContent = "还有 " + plan.hidden + " 条在通知中心 ›";
      more.classList.remove("hidden");
    } else {
      more.classList.add("hidden");
    }
  }
}

// 只上屏、不入账：通知中心里点历史条目重新弹出时用
// act = { text, onAction }：给了就多渲染一个动作按钮（撤销 Snackbar）；
// 停留时长与 UNDO_MS 取 max，保证按钮永远先于反悔窗口本身消失。
function notifShow(msg, level, act) {
  const box = $("#notifStack");
  if (!box) return null;
  const lv = notifNormLevel(level);
  const n = {
    seq: ++notifSeq,
    msg: String(msg === null || msg === undefined ? "" : msg),
    level: lv, el: null, timer: 0,
    act: act && act.text && typeof act.onAction === "function"
      ? { text: String(act.text), onAction: act.onAction } : null,
  };
  notifQueue.push(n);
  notifRender();
  n.timer = setTimeout(() => notifDismiss(n), n.act ? Math.max(notifDur(lv), UNDO_MS) : notifDur(lv));
  return n;
}

function notifDismiss(n, manual) {
  if (!n) return;
  if (n.timer) clearTimeout(n.timer);
  const i = notifQueue.indexOf(n);
  if (i >= 0) notifQueue.splice(i, 1);
  if (manual && n.el) {
    n.el.classList.add("out");
    setTimeout(() => notifUnmount(n), 240);
  } else {
    notifUnmount(n);
  }
  notifRender();
}

function notifBadge() {
  const b = $("#notifBadge");
  if (!b) return;
  const unread = notifUnread(notifHistory, notifLastSeen);
  if (unread > 0) {
    b.textContent = unread > 99 ? "99+" : String(unread);
    b.classList.remove("hidden");
  } else {
    b.textContent = "";
    b.classList.add("hidden");
  }
}

function notifPanelOpen() {
  const p = $("#notifPanel");
  return !!p && !p.classList.contains("hidden");
}

// 弹窗是「开 / 关」两态：读屏用户只能从按钮上的状态位知道现在是不是展开着
function notifSyncBtn() {
  const btn = $("#notifBtn");
  if (!btn) return;
  const on = notifPanelOpen();
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.setAttribute("aria-expanded", on ? "true" : "false");
}

// 统一入口：入账（历史 + 未读数）+ 上屏。旧调用点 toast(msg, isInfo) 一个都不用改。
function notifPush(msg, isInfo) {
  const level = notifLevel(msg, isInfo);
  const item = {
    msg: String(msg === null || msg === undefined ? "" : msg).slice(0, NOTIF_MSG_MAX),
    level: level,
    ts: Date.now(),
  };
  notifHistory = notifPushHistory(notifHistory, item, NOTIF_HISTORY_MAX);
  notifSaveHistory();
  if (notifPanelOpen()) {          // 面板开着 = 用户正在看，来了新的直接标已读
    notifLastSeen = item.ts;
    notifSaveSeen();
    notifRenderPanel();
  }
  notifBadge();
  notifShow(item.msg, level);
  return level;
}

function toast(msg, isInfo = false) { notifPush(msg, isInfo); }

function notifRenderPanel() {
  const list = $("#notifList");
  if (!list) return;
  const cnt = $("#notifCount");
  if (cnt) cnt.textContent = notifHistory.length ? notifHistory.length + " 条" : "";
  list.textContent = "";
  if (!notifHistory.length) {
    const li = document.createElement("li");
    li.className = "nempty";
    li.textContent = "还没有通知。出错、成功、配对结果都会记在这儿。";
    list.append(li);
    return;
  }
  notifHistory.forEach((it) => {
    const li = document.createElement("li");
    li.className = "nrow " + it.level;
    li.title = "点一下重新弹出这条通知";
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    const dot = document.createElement("span");
    dot.className = "ndot";
    dot.setAttribute("aria-hidden", "true");
    const time = document.createElement("span");
    time.className = "ntime";
    time.textContent = notifFmtTime(it.ts);
    const msg = document.createElement("span");
    msg.className = "nmsg";
    msg.textContent = it.msg;
    li.append(dot, time, msg);
    const re = () => {
      notifShow(it.msg, it.level);
      notifLastSeen = Date.now();
      notifSaveSeen();
      notifBadge();
    };
    li.addEventListener("click", re);
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); re(); }
    });
    list.append(li);
  });
}

function notifOpen(btn) {
  if (notifPanelOpen()) return;   // openModal 自身防风重入，这里顺带避免重复标已读
  notifLastSeen = Date.now();
  notifSaveSeen();
  notifBadge();
  notifRenderPanel();
  openModal("#notifPanel", btn || $("#notifBtn"));   // 复用弹窗统一行为：Esc / 焦点归还
  notifSyncBtn();
}

// 快捷键与命令面板共用：开着就关、关着就开（状态位一起翻）
function notifToggle(btn) {
  if (notifPanelOpen()) closeModal("#notifPanel");
  else notifOpen(btn);
}

function notifClearAll() {
  notifHistory = [];
  notifSaveHistory();
  notifLastSeen = Date.now();
  notifSaveSeen();
  notifBadge();
  notifRenderPanel();
}

function notifInit() {
  notifHistory = notifLoadHistory();
  notifLastSeen = notifLoadSeen();
  const btn = $("#notifBtn");
  if (btn) btn.addEventListener("click", () => notifOpen(btn));
  const panel = $("#notifPanel");
  if (panel) panel.addEventListener("modalclosed", notifSyncBtn);
  const clr = $("#notifClearBtn");
  if (clr) clr.addEventListener("click", notifClearAll);
  const more = $("#notifMore");
  if (more) more.addEventListener("click", () => notifOpen());
  notifSyncBtn();
  notifBadge();
}/* ---------------- 弹窗统一行为 ----------------
   对标 Radix Dialog 的最小子集：语义由 HTML 上 role=dialog/aria-modal 提供，这里管行为
   ——Esc 关闭、打开时焦点移入弹窗、关闭后还给触发按钮、Tab 在弹窗内循环、背景锁滚动。
   两个 modal（设置/截图）共用，新增弹窗只要调 openModal/closeModal。
   关键冲突：全局键盘遥控里 Esc=「返回」电视，弹窗开着时必须优先关弹窗，所以在
   capture 阶段拦下导航键并 stopPropagation，dpad 处理器收不到；Enter/Space 放行
   ——那是激活弹窗内按钮的键盘通道，不能误伤。 */
const openModals = [];
let lastModalTrigger = null;

function openModal(sel, trigger) {
  const el = $(sel);
  if (!el || openModals.includes(el)) return;
  if (!openModals.length) document.body.style.overflow = "hidden";
  lastModalTrigger = trigger || document.activeElement;
  el.classList.remove("hidden");
  openModals.push(el);
  const auto = el.querySelector("[data-autofocus]") ||
    el.querySelector("button:not([disabled]), [href], input, select, textarea");
  if (auto) auto.focus();
}

function closeModal(sel) {
  const el = typeof sel === "string" ? $(sel) : sel;
  const i = openModals.indexOf(el);
  if (i < 0) return;
  openModals.splice(i, 1);
  el.classList.add("hidden");
  if (!openModals.length) document.body.style.overflow = "";
  // 焦点还给打开弹窗的那个按钮（读屏用户不会「弹窗一关就丢了位置」）
  if (lastModalTrigger && lastModalTrigger.focus) lastModalTrigger.focus();
  el.dispatchEvent(new CustomEvent("modalclosed"));   // 给有「关闭即持久化」需求的弹窗用
}

// capture 阶段拦截：弹窗开着时电视导航键不许穿透
document.addEventListener("keydown", (e) => {
  if (!openModals.length) return;
  const top = openModals[openModals.length - 1];
  if (e.key === "Escape") {
    e.preventDefault(); e.stopPropagation();
    closeModal(top);
    return;
  }
  if (e.key === "Tab") {   // 焦点循环在弹窗内，不逃到背景
    const f = [...top.querySelectorAll("button:not([disabled]), [href], input, select, textarea")]
      .filter((el) => el.offsetParent !== null);
    if (f.length < 2) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    return;
  }
  // 其余键：Enter/Space 是激活弹窗内按钮的键盘通道——按键的「默认动作」会触发
  // click，所以只 stopPropagation 切断向电视的穿透，不能 preventDefault（那会把
  // 按钮的键盘激活一起禁掉，键盘用户就关不掉弹窗了）；其他键连同默认动作一起拦。
  // 输入框内打字除外（当前弹窗没有输入框，留给以后的弹窗）。
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select";
  if (typing) return;
  if (e.key === "Enter" || e.key === " ") e.stopPropagation();
  else { e.preventDefault(); e.stopPropagation(); }
}, true);

/* ---------------- 首次使用引导（coach mark） ----------------
   对标 Material 的 feature discovery：暗场里只留目标元素一块光圈 + 吸附说明卡。
   双指手势、长按滚动这类隐形能力不引导根本发现不了，所以按连接状态分两段自动走一遍：
   连接前只教「怎么连」，连接后才教「怎么用」，看完/跳过/Esc 关掉各自记一刀
   （localStorage），之后随时能从设置里整段重看。 */

// 拆两段学的是 shepherd.js 的 stage queue + beforeShowPromise：步骤能不能出场由前置条件
// 决定，而不是「进页面 600ms 一定把 4 步全糊上来」。没连上电视时长按连发、触摸板手势、键盘
// 遥控全都无从体验，光圈还会打到被 .disc 压暗的按键上；已经连着的人则不需要再被教一遍怎么
// 连接。目标当前不可见时直接跳步，抄的是 driver.js 找不到目标元素就退到 0×0 dummy element
// 的做法——宁可少一步，也不把光圈空打在页面上。
const COACH_KEY_PRE = "atv.coached.pre";
const COACH_KEY_POST = "atv.coached.post";
const COACH_HELP = {
  pre: [
    { tab: "dpad", sel: "#targetInput", title: "连接电视",
      desc: "输入电视 IP 点「连接」；连过的地址会留在下方芯片里，点一下就能重连。" },
  ],
  post: [
    { tab: "dpad", sel: ".dpad", title: "方向键可以长按连发",
      desc: "方向、音量、seek 键按住 450ms 后会连续发送——滚列表、调音量不用点到手酸。" },
    { tab: "pad", sel: "#touchpad", title: "触摸板的四种手势",
      desc: "轻点 = 点击、拖动 = 滑动；✌️ 双指上下滑 = 音量、左右滑 = 快进，双轻点 = 播放/暂停；单指长按 = 连续滚动。" },
    { tab: "pad", sel: "#textInput", title: "直接用键盘遥控",
      desc: "点一下页面空白处：方向键移动、回车 = OK、Esc = 返回、退格 = 删除、媒体键控制播放；在输入框里打字则作为文本发送。" },
    // 没东西在播时 npCard 整张收起，这一步会被自动跳过去（见 showCoachStep 的可见性判断）
    { tab: "pad", sel: "#npSeek", title: "⏪15 / ⏩15 快进快退",
      desc: "播放中才亮，直播或拿不到时长时自动禁用；点一下跳 15 秒。" },
  ],
};
let coachStage = "pre";   // "pre" 连接前 / "post" 连接后 / "all" 设置里手动整段重看
let coachSteps = COACH_HELP.pre;
let coachStep = 0;
const coachKeyOf = (stage) => (stage === "post" ? COACH_KEY_POST : COACH_KEY_PRE);
function coachSeen(stage) { return localStorage.getItem(coachKeyOf(stage)) === "1"; }
function coachMarkSeen(stage) { return localStorage.setItem(coachKeyOf(stage), "1"); }
function coachOpen() { return !$("#coachMark").classList.contains("hidden"); }
let postCoachPending = false;
// 连接后才引导：connect() 成功后那次 renderStatus、以及开着页面就连着时的 boot 首帧都会
// 走到这。shepherd.js 的 beforeShowPromise 是同一招——前置条件不成立就不出场。
function maybePostCoach() {
  if (postCoachPending || coachOpen() || coachSeen("post") || !status.connected) return;
  postCoachPending = true;
  // 等本轮 renderStatus 把剩余 DOM 收尾（输入法查询、设备 chips）再量尺寸，不然光圈会对到
  // 还没稳定的布局上；首屏那一下顺带等一次绘制
  setTimeout(() => {
    postCoachPending = false;
    if (!coachOpen() && status.connected) startCoach("post");
  }, 400);
}

function showCoachStep(i) {
  coachStep = i;
  const st = coachSteps[i];
  // 目标可能在另一个 tab 的面板里，先切过去再量尺寸（class 切换后同步读 rect 会触发重排，拿到的是新值）
  document.querySelector(`.tab[data-tab="${st.tab}"]`)?.click();
  const el = $(st.sel);
  // 目标此刻不可见（没开始播、Apple TV 没有这张卡）：学 driver.js 找不到目标就退到 0×0
  // dummy element——跳过这一步，别让光圈空打在页面上；后面没步了就顺势收尾
  const r = el && el.offsetParent !== null ? el.getBoundingClientRect() : null;
  if (!r || r.width < 1 || r.height < 1) {
    return i + 1 < coachSteps.length ? showCoachStep(i + 1) : finishCoach();
  }
  const pad = 8;
  const spot = $("#coachSpot");
  spot.style.left = (r.left - pad) + "px";
  spot.style.top = (r.top - pad) + "px";
  spot.style.width = (r.width + pad * 2) + "px";
  spot.style.height = (r.height + pad * 2) + "px";
  $("#coachTitle").textContent = st.title;
  $("#coachDesc").textContent = st.desc;
  $("#coachIdx").textContent = i + 1;
  $("#coachPrevBtn").classList.toggle("hidden", i === 0);
  $("#coachTotal").textContent = coachSteps.length;
  $("#coachNextBtn").textContent = i === coachSteps.length - 1 ? "完成" : "下一步";
  // 说明卡优先吸附目标下方，下方放不下（矮屏/目标在底部）就翻到上方
  const card = $("#coachCard");
  const ch = card.offsetHeight || 180;
  card.style.top = (r.bottom + pad + 12 + ch < innerHeight
    ? r.bottom + pad + 12
    : Math.max(12, r.top - pad - 12 - ch)) + "px";
}
// stage: "pre" 连接前 / "post" 连接后 / "all" 设置里手动整段重看
function startCoach(stage) {
  coachStage = stage || (status.connected ? "post" : "pre");
  coachSteps = coachStage === "all" ? COACH_HELP.pre.concat(COACH_HELP.post) : COACH_HELP[coachStage];
  coachStep = 0;
  openModal("#coachMark");
  showCoachStep(0);
}
function markCoachStage() {
  if (coachStage === "all") { coachMarkSeen("pre"); coachMarkSeen("post"); }
  else coachMarkSeen(coachStage);
}
function finishCoach() {
  markCoachStage();   // 看完/跳过/Esc 都算数，别反复骚扰
  const chained = coachStage === "pre" && status.connected && !coachSeen("post");
  closeModal("#coachMark");
  // 刚教完「怎么连」就已经连上了：顺势接上「怎么用」，不用等刷新或下一次 8s 轮询
  if (chained) setTimeout(maybePostCoach, 400);
}
$("#coachNextBtn").addEventListener("click", () => {
  buzz();
  if (coachStep >= coachSteps.length - 1) return finishCoach();
  showCoachStep(coachStep + 1);
});
$("#coachPrevBtn").addEventListener("click", () => { buzz(); showCoachStep(coachStep - 1); });
$("#coachSkipBtn").addEventListener("click", finishCoach);
// Esc/通用路径关掉也算看过——监听 closeModal 广播的 modalclosed
$("#coachMark").addEventListener("modalclosed", markCoachStage);
// 引导开着时窗口尺寸变化/内部滚动要重新对光
const repositionCoach = () => {
  if (!$("#coachMark").classList.contains("hidden")) showCoachStep(coachStep);
};
addEventListener("resize", repositionCoach);
addEventListener("scroll", repositionCoach, true);

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
  if (VOL_KEYS.has(code)) volBump(code);   // 音量 OSD：本地先反馈，不等 adb 回包
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
    pushHistory(text);
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
  renderHist(); // 隐私模式下整行隐藏，避免旁人瞥到此前的输入记录
});

/* ---------------- 输入历史（最近发送过的内容） ---------------- */
// 学习源：@algolia/autocomplete-plugin-recent-searches@1.19.11——submit 时 onAdd、
// getAll 截 limit、有查询时大小写不敏感子串过滤、localStorage 先试写探测可用性
// （Safari 隐私模式 setItem 会抛）。补上它的缺口：trim + 去重（它的去重靠服务端
// Query Suggestions）；隐私模式不记录。
const HIST_KEY = "atv.kbhist";
const HIST_MAX = 10;   // 与常用短语同級行宽，再多屏幕也摆不下
const HIST_LEN = 200;  // 单条截断，防长文把 localStorage 撑爆

let kbHist = (() => {
  try {
    const v = JSON.parse(localStorage.getItem(HIST_KEY));
    if (Array.isArray(v)) return v.filter((s) => typeof s === "string").slice(0, HIST_MAX);
  } catch { /* 损坏就回落空历史 */ }
  return [];
})();

function histSave() {
  try { localStorage.setItem(HIST_KEY, JSON.stringify(kbHist)); }
  catch { /* 存不进去（隐私模式 / 超额）就只是本次会话没有历史 */ }
}

function pushHistory(text) {
  if (privacy.on) return; // 隐私模式不记录
  const t = String(text).trim().replace(/\s+/g, " ").slice(0, HIST_LEN);
  if (!t) return;
  kbHist = [t, ...kbHist.filter((h) => h !== t)].slice(0, HIST_MAX);
  histSave();
  renderHist();
}

// filter = 输入框当前内容：像 Algolia 那样按大小写不敏感子串收窄，打完一半就能从
// 历史里点回全句；空输入显示全部。隐私模式下整行隐藏。
function renderHist(filter) {
  const row = $("#histRow");
  row.innerHTML = "";
  if (privacy.on) { row.classList.add("hidden"); return; }
  const q = String(filter || "").trim().toLowerCase();
  const items = q ? kbHist.filter((h) => h.toLowerCase().includes(q)) : kbHist;
  if (!items.length) { row.classList.add("hidden"); return; }
  row.classList.remove("hidden");

  const label = document.createElement("span");
  label.className = "histlabel";
  label.textContent = "最近";
  row.appendChild(label);

  items.forEach((h) => {
    const wrap = document.createElement("span");
    wrap.className = "hist-item";
    const c = document.createElement("button");
    c.className = "btn tiny hist-chip";
    const t = document.createElement("span");
    t.className = "hist-t";
    t.textContent = h; // 历史内容一律 textContent，与 phrase chips 一致
    c.appendChild(t);
    c.title = h;
    c.onclick = () => { const inp = $("#textInput"); inp.value = h; inp.focus(); };
    const del = document.createElement("button");
    del.className = "btn tiny hist-del";
    del.textContent = "✕";
    del.title = "删除这条记录";
    del.setAttribute("aria-label", "删除这条输入记录");
    del.onclick = () => {
      kbHist = kbHist.filter((x) => x !== h);
      histSave();
      renderHist();
    };
    wrap.append(c, del);
    row.appendChild(wrap);
  });

  const clear = document.createElement("button");
  clear.className = "btn tiny hist-clear";
  clear.textContent = "🗑 清空";
  clear.title = "清空全部输入历史";
  clear.onclick = () => { kbHist = []; histSave(); renderHist(); };
  row.appendChild(clear);
}

$("#textInput").addEventListener("input", (e) => renderHist(e.target.value));

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
    if (!text) return toast("没听清，再说一次");
    intentSpeak(text);
  };
  r.onerror = (e) => { toast("语音识别失败：" + (e.error || "unknown")); micListening(false); micRec = null; };
  r.onend = () => { micListening(false); micRec = null; };

  micRec = r;
  micListening(true);
  log("🎤 请说话（说完自动发送）…");
  r.start();
});
/* ---- 说话即遥控的胶水层：规则在 intent 段，这里只负责执行与反馈 ----
   两个刻意的选择：
   1. 听不懂就原样 sendText——搜索词（「周杰伦」）绝不能被误判成命令；
   2. 执行前先 log + toast 说出「听懂成了什么」，误识别时用户立刻知道按了什么，
      而不是看着电视莫名其妙换台。 */
function intentSpeak(text) {
  if (!intentEnabled()) return sendText(text, false);   // 用户在设置里关掉了意图层
  let hit = null;
  try { hit = intentParse(text, palCommands()); } catch (err) { hit = null; }
  if (!hit) return sendText(text, false);
  log("🎤 听懂为：" + hit.cmd.label + "（" + hit.via + "）");
  toast("🎤 " + hit.cmd.label);
  palRemember(hit.cmd.id);
  try { hit.cmd.run(); } catch (err) { toast("⚠ " + err.message); }
}

/* ---------------- 睡眠定时 ---------------- */
// until 用 epoch 秒由服务端给，倒计时本地走秒：不靠 8s 轮询刷新，按钮秒级响应
let sleepUntil = 0;
let sleepTick = null;

/* ---------------- Android 掉线自动重连进度 ---------------- */
// 服务端退避是 6->12->24s（server.py reconnect_delay）。next_in 只在 8s 轮询到达时刷新，
// 若只显示它，「3s 后重试」会原地停 8 秒；所以终点由服务端校准、中间按本地秒走。
let arEl = null;       // #tvInfo，由 renderStatus 传进来（别在这里再 $() 找一遍）
let arBase = "";       // 不带重连后缀的设备状态文案
let arActive = false;
let arStopped = false;
let arNextIn = 0;
let arTick = null;

function arPaint() {
  if (!arEl) return;
  let suffix = "";
  if (arStopped) suffix = " · 重连失败，点连接重试";
  else if (arActive && arNextIn > 0) suffix = " · 自动重连中（" + arNextIn + "s 后重试）";
  else if (arActive) suffix = " · 自动重连中";
  arEl.textContent = arBase + suffix;
}

function renderAutoReconn(el, base, ar) {
  arEl = el || null;
  arBase = base || "";
  arActive = !!(ar && ar.active);
  arStopped = !!(ar && ar.stopped);
  arNextIn = ar && ar.next_in > 0 ? ar.next_in : 0;   // 服务端值是校准点，不是唯一真相
  clearInterval(arTick);
  arTick = null;
  arPaint();
  if (arActive && !arStopped && arNextIn > 0) {
    arTick = setInterval(function () {
      arNextIn -= 1;
      if (arNextIn <= 0) { clearInterval(arTick); arTick = null; }
      arPaint();
    }, 1000);
  }
}

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
  /* 和进度条共用同一套步骤描述：纯延时步以前在这里渲染成 "undefined" */
  b.title = (m.steps || []).map(macroStepText).join(" → ");
  b.addEventListener("click", () => runMacro(m));
  if (custom) {
    const del = document.createElement("button");
    del.className = "btn tiny danger";
    del.textContent = "✕";
    del.title = "删除这个自定义宏";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      const all = customMacros();
      if (all.findIndex((x) => x.name === m.name) < 0) return;   // 列表刚刷新过，这一下已经没有目标了
      const prevRaw = localStorage.getItem(MACRO_LS);            // 撤销 = 把整份旧值原样写回
      undoable("已删除宏「" + m.name + "」", () => {
        localStorage.setItem(MACRO_LS, JSON.stringify(all.filter((x) => x.name !== m.name)));
        loadMacros();
      }, () => {
        if (prevRaw === null) localStorage.removeItem(MACRO_LS);
        else localStorage.setItem(MACRO_LS, prevRaw);
        loadMacros();
      });
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
    Macro.presets = j.presets || [];   // 命令面板读这份缓存，不重复请求
    ([...(j.presets || []), ...customMacros()]).forEach((m, i) => {
      row.append(macroButton(m, i >= (j.presets || []).length));
    });
    renderMacroState(j);
  } catch (e) {
    row.textContent = "";
    $("#macroState").textContent = "⚠ 宏列表加载失败：" + e.message;
  }
}

/* ---------------- 宏执行进度与分步 trace ----------------
   学 Home Assistant 的 script 实体（homeassistant/components/script）与它的 automation
   trace：后台任务把「第几步 / 完成几步 / 失败几步 / 是否被取消」当一等状态暴露（服务端
   macro_state()），前端只做投影、不推断跑到哪；每步的耗时与失败原因同样来自服务端。
   正因为「哪一步失败了」是可知的，才有「重跑失败步骤」：步号取自 trace，步骤本体取自
   本地 Macro.steps。run 是递增运行代号：区分「新的一次刚结束」与「从没跑过」，也保证汇总
   只记一条日志。自定义宏的步骤只存在浏览器 localStorage，所以本地留一份 Macro.steps
   供步骤描述与重跑用；刷新页面后丢了就只报步号——总步数服务端仍然给得出。 */
const Macro = { run: 0, name: "", steps: [], primed: false, lastTrace: [] };

function macroStepText(step) {
  if (!step) return "";
  const { icon, text } = macroStepLabel(step);
  return `${icon} ${text}`;
}

/* 一次运行结束：汇总留在进度条原位，直到下一次运行把它顶掉。
   失败计数终于对用户可见——以前它只进服务端 stderr，手机上没人知道哪一步挂了。 */
function macroShowResult(j) {
  const prog = $("#macroProg");
  if (!prog) return;
  const total = j.total || 0, failed = j.failed || 0;
  prog.classList.remove("hidden");
  prog.className = "mprog " + (j.cancelled ? "bad" : failed ? "warn" : "ok");
  $("#macroProgFill").style.width = "100%";
  const what = j.cancelled ? "已取消" : failed ? "完成，但有失败" : "完成";
  $("#macroProgStep").textContent =
    `「${j.name || "宏"}」${what} · ${j.done || 0}/${total} 步`;
  $("#macroProgFail").textContent = failed ? `${failed} 步失败` : "";
  log(`${j.cancelled ? "⏹" : failed ? "⚠" : "✅"} 宏「${j.name || "宏"}」${what}：` +
      `${j.done || 0}/${total} 步${failed ? `，${failed} 步失败` : ""}`);
  /* 状态行收尾：进度条说「第几步 / 完成到哪」，状态行说「这次跑成了什么」。
     少了这句，宏结束后状态行还停在「⏳ 执行中」，和进度条自相矛盾。 */
  const state = $("#macroState");
  if (state) {
    const name = j.name || "宏";
    state.textContent = j.cancelled
      ? `⏹ 宏「${name}」已取消 · 执行了 ${j.done || 0}/${total} 步`
      : failed ? `⚠ 宏「${name}」完成，${total} 步里有 ${failed} 步失败`
      : `✅ 宏「${name}」完成 · ${total} 步`;
  }
}

/* 分步 trace：一行一步——做了什么、花了多久、为什么失败。
   步号由服务端给，步骤描述在本地 Macro.steps 里查（自定义宏的步骤不该进 state.json）。
   耗时同样是信息：纯延时步的 2.5s 和「卡住 2.5s」在进度条上长得一模一样。 */
function fmtMs(ms) {
  if (!isFinite(ms)) return "";
  return ms >= 1000 ? fmtSec(ms) : Math.round(ms) + "ms";
}

function macroTraceRows(j) {
  const ul = $("#macroTrace");
  const rerun = $("#macroRerunBtn");
  if (!ul) return;
  const list = (j && Array.isArray(j.trace)) ? j.trace : [];
  Macro.lastTrace = list;
  ul.textContent = "";
  list.forEach((t) => {
    if (!t || typeof t.i !== "number") return;   // 字段缺失就跳过，别阻断轮询
    const li = document.createElement("li");
    li.className = t.ok ? "ok" : t.cancel ? "cancel" : "bad";
    const badge = document.createElement("span");
    badge.className = "mtbadge";
    badge.textContent = t.ok ? "✅" : t.cancel ? "⏹" : "❌";
    const body = document.createElement("span");
    body.className = "mtbody";
    const what = document.createElement("span");
    what.className = "mtwhat";
    what.textContent = t.i + ". " + (macroStepText(Macro.steps[t.i - 1]) || ("第 " + t.i + " 步"));
    body.append(what);
    if (t.err) {
      const err = document.createElement("span");
      err.className = "mterr";
      err.textContent = t.err;
      body.append(err);
    }
    const ms = document.createElement("span");
    ms.className = "mtms";
    ms.textContent = fmtMs(t.ms);
    li.append(badge, body, ms);
    ul.append(li);
  });
  ul.classList.toggle("hidden", !list.length);
  const bad = list.filter((t) => t && !t.ok && !t.cancel);
  if (rerun) rerun.classList.toggle("hidden", !bad.length);
}

function renderMacroState(j) {
  clearInterval(macroTick);
  macroTick = null;
  macroTraceRows(j);                       // 分步 trace：执行中一行行长出来，结束后留下
  Macro.name = (j && j.name) || Macro.name;   // 重跑失败步骤时拼名字用
  const cancelBtn = $("#macroCancelBtn");
  const stateEl = $("#macroState");
  const prog = $("#macroProg");
  const running = !!(j && j.running);
  if (cancelBtn) cancelBtn.classList.toggle("hidden", !running);
  if (!running) {
    if (j && j.run && j.run !== Macro.run) {
      Macro.run = j.run;
      if (Macro.primed) { macroShowResult(j); return; }
      Macro.primed = true;   // 首帧只吸收历史结果，不当成「刚跑完的一条」来报
    }
    if (Macro.run) return;   // 汇总已在展示，留到下一次运行顶掉
    if (stateEl) stateEl.textContent = "宏会按顺序执行多步操作（应用包名会依次尝试 Android / tvOS）。";
    if (prog) prog.classList.add("hidden");
    return;
  }
  Macro.primed = true;
  if (stateEl) stateEl.textContent = "⏳ 宏执行中……（可取消；每步结果见下方日志）";
  if (prog) {
    const total = Math.max(1, j.total || 0);
    const idx = Math.min(total, Math.max(0, j.index || 0));
    prog.classList.remove("hidden");
    prog.className = "mprog running";
    $("#macroProgFill").style.width = (idx / total * 100) + "%";
    const desc = macroStepText(Macro.steps[idx - 1]);
    $("#macroProgStep").textContent = `第 ${idx} / ${total} 步` + (desc ? ` · ${desc}` : "");
    $("#macroProgFail").textContent = j.failed ? `${j.failed} 步失败` : "";
  }
  macroTick = setInterval(async () => {
    try {
      const again = await api("/api/macros");
      renderMacroState(again);
    } catch (e) { /* 轮询失败不打扰：宏还在服务端跑 */ }
  }, 1500);
}

async function runMacro(m) {
  try {
    Macro.steps = Array.isArray(m.steps) ? m.steps.slice() : [];   // 只用于步骤描述
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

/* 重跑失败步骤：步号来自 trace（服务端），步骤本体来自本地 Macro.steps。
   服务端不存自定义宏（state.json 不放可编辑内容），所以步骤不在这个浏览器里就只能提示。 */
$("#macroRerunBtn")?.addEventListener("click", () => {
  const bad = Macro.lastTrace.filter((t) => t && !t.ok && !t.cancel);
  const steps = bad.map((t) => Macro.steps[t.i - 1]).filter(Boolean);
  if (!steps.length) {
    toast("⚠ 这条宏的步骤不在这个浏览器里（自定义宏存在本地），重跑不了");
    return;
  }
  runMacro({ name: (Macro.name || "宏") + "（重跑失败步）", steps });
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

/* ---------------- 自定义宏可视化编辑器 ----------------
   textarea 仍是唯一事实源（兼容已存的本机宏、也方便直接粘贴 JSON），步骤列表只是它的
   实时投影：每一步渲染成一行人话，可删可排序；快捷添加把参数收齐后写回 JSON，
   用户不再需要手写 JSON。解析失败时列表保留最后一版好状态，只把错误显示出来。 */
const MACRO_KEY_NAMES = { 3: "主页", 4: "返回", 19: "上", 20: "下", 21: "左", 22: "右", 23: "OK",
  24: "音量+", 25: "音量-", 66: "回车", 67: "退格", 85: "播放/暂停", 86: "停止",
  87: "下一曲", 88: "上一曲", 89: "快退", 90: "快进", 164: "静音" };
const MACRO_KEY_OPTIONS = [[24, "音量+"], [25, "音量-"], [164, "静音"], [3, "主页"], [4, "返回"],
  [23, "OK"], [19, "上"], [20, "下"], [21, "左"], [22, "右"], [85, "播放/暂停"], [86, "停止"],
  [89, "快退"], [90, "快进"]];
const MACRO_PKG_NAMES = {};   // pkg → 中文名（预设应用 + Apple TV 列表动态补充）
APPS.forEach((a) => { MACRO_PKG_NAMES[a.pkg] = a.name; });
const pkgName = (p) => MACRO_PKG_NAMES[p] || p;

function macroParse() {
  const raw = $("#macroText").value.trim();
  if (!raw) return { name: "", steps: [] };
  const m = JSON.parse(raw);
  if (typeof m !== "object" || m === null || Array.isArray(m)) throw new Error("顶层必须是对象（含 name 和 steps）");
  if (!Array.isArray(m.steps)) m.steps = [];
  return m;
}
function macroWrite(m) {
  $("#macroText").value = JSON.stringify(m, null, 2);
  renderMacroSteps();
}
function macroStepLabel(st) {
  // 纯延时步和「带延时的动作步」是两种渲染
  if (st.type === undefined && st.delay !== undefined) return { icon: "⏱", text: `等 ${fmtSec(st.delay)}` };
  if (st.type === "app") {
    const pkgs = (st.pkgs || []).length ? st.pkgs : [st.pkg];
    const more = pkgs.length > 1 ? `（${pkgs.length} 个候选包）` : "";
    return { icon: "🎬", text: `启动 ${pkgName(pkgs[0])}${more}` };
  }
  if (st.type === "key") {
    const codes = st.codes || [st.code];
    const names = codes.map((c) => MACRO_KEY_NAMES[c] || `键码 ${c}`);
    return { icon: "⌨", text: names.join("、") + (codes.length > 1 ? ` ×${codes.length}` : "") };
  }
  if (st.type === "text") return { icon: "🔤", text: `输入「${String(st.text).slice(0, 30)}」` };
  return { icon: "•", text: "未知步骤" };
}
const fmtSec = (ms) => (Math.round(ms / 100) / 10).toString().replace(/\.0$/, "") + "s";

function renderMacroSteps() {
  let m;
  try {
    m = macroParse();
    $("#macroErr").textContent = "";
  } catch (e) {
    // 解析失败只报错、不清空——用户改 JSON 的半途列表不闪没，改完自动回来
    $("#macroErr").textContent = "⚠ JSON 解析失败：" + e.message + "（列表为最后一版好状态）";
    return;
  }
  const box = $("#macroSteps");
  box.textContent = "";
  if (!m.steps.length) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "还没有步骤——用下面的按钮添加，或直接粘贴 JSON";
    box.appendChild(d);
    return;
  }
  m.steps.forEach((st, i) => {
    const { icon, text } = macroStepLabel(st);
    const row = document.createElement("div");
    row.className = "mstep";
    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = i + 1;
    const ic = document.createElement("span");
    ic.textContent = icon;
    const tx = document.createElement("span");
    tx.className = "stext";
    tx.textContent = text;
    tx.title = text;
    row.append(idx, ic, tx);
    if (st.type !== undefined && st.delay !== undefined) {
      const b = document.createElement("span");
      b.className = "delaybadge";
      b.textContent = "等 " + fmtSec(st.delay);
      row.appendChild(b);
    }
    const ops = document.createElement("span");
    ops.className = "ops";
    const mk = (label, cls, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.className = cls;
      b.title = label === "✕" ? "删除这一步" : label === "↑" ? "上移" : "下移";
      b.addEventListener("click", fn);
      ops.appendChild(b);
    };
    mk("↑", "", () => { const mm = macroParse(); if (i > 0) { mm.steps.splice(i - 1, 0, mm.steps.splice(i, 1)[0]); macroWrite(mm); } });
    mk("↓", "", () => { const mm = macroParse(); if (i < mm.steps.length - 1) { mm.steps.splice(i + 1, 0, mm.steps.splice(i, 1)[0]); macroWrite(mm); } });
    mk("✕", "del", () => { const mm = macroParse(); mm.steps.splice(i, 1); macroWrite(mm); });
    row.appendChild(ops);
    box.appendChild(row);
  });
}

/* 快捷添加：点类型 → 内联参数表单 → 确定后 append 回 JSON */
const MACRO_ADDER = {
  app: { label: "包名", html: '<input id="mfPkg" list="appPkgOptions" placeholder="如 com.google.android.youtube.tv" autocomplete="off"><button class="btn tiny primary" id="mfOk">添加</button>' },
  key: { label: "按键", html: '<select id="mfKey">' + MACRO_KEY_OPTIONS.map(([c, n]) => `<option value="${c}">${n}</option>`).join("") + '</select><button class="btn tiny primary" id="mfOk">添加</button>' },
  delay: { label: "等待秒数", html: '<input id="mfDelay" type="number" min="0.1" max="10" step="0.1" value="2"><button class="btn tiny primary" id="mfOk">添加</button>' },
  text: { label: "文本", html: '<input id="mfText" placeholder="要输入到电视的文字" autocomplete="off"><button class="btn tiny primary" id="mfOk">添加</button>' },
};
function macroAddStep(kind) {
  const form = $("#macroForm");
  form.textContent = "";
  form.innerHTML = MACRO_ADDER[kind].html;
  form.classList.remove("hidden");
  const first = form.querySelector("input, select");
  first?.focus();
  $("#mfOk").addEventListener("click", () => {
    let st;
    if (kind === "app") {
      const pkg = $("#mfPkg").value.trim();
      if (!pkg) return toast("⚠ 包名不能为空");
      st = { type: "app", pkg };
    } else if (kind === "key") {
      st = { type: "key", code: Number($("#mfKey").value) };
    } else if (kind === "delay") {
      const sec = Number($("#mfDelay").value);
      if (!(sec > 0)) return toast("⚠ 等待秒数不合法");
      st = { delay: Math.round(Math.min(10, sec) * 1000) };
    } else {
      const t = $("#mfText").value;
      if (!t.trim()) return toast("⚠ 文本不能为空");
      st = { type: "text", text: t };
    }
    const m = macroParse();
    if (!m.name) m.name = "我的宏";
    m.steps.push(st);
    macroWrite(m);
    form.classList.add("hidden");
    buzz(10);
  });
  form.querySelector("input")?.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#mfOk").click(); });
}
$$("#macroCustom [data-add]").forEach((b) => b.addEventListener("click", () => macroAddStep(b.dataset.add)));

// textarea 是事实源：边打字边投影（轻量防抖，别每个 keystroke 都全量重渲染）
let macroRenderTimer = null;
$("#macroText").addEventListener("input", () => {
  clearTimeout(macroRenderTimer);
  macroRenderTimer = setTimeout(renderMacroSteps, 250);
});

// 启动应用候选（datalist）：选名字即可，不用记包名
const appPkgList = $("#appPkgOptions");
APPS.forEach((a) => {
  const o = document.createElement("option");
  o.value = a.pkg;
  o.label = a.name;
  appPkgList.appendChild(o);
});
renderMacroSteps();

/* ---------------- 宏预演（dry-run） ----------------
   学 Ansible --check / Terraform plan：点「预演」不发请求、不碰设备，只在本地把这条宏
   按服务端 validate_macro_steps（server.py）的同一套规则过一遍，告知每步会怎样、
   整条要跑多久、哪里会被拒。计划与执行同源才不会骗人——下面常量与 server.py 一一
   对应，tests/test_macro_dryrun.py 逐项比对，改一边忘另一边测试就红。 */
/* ===== macro-dry-run:begin（纯函数段，node 单测 harness 原样抽取执行；禁引用 DOM / 全局态） ===== */
const MACRO_DRY_KEY_NAMES = { 3: "主页", 4: "返回", 19: "上", 20: "下", 21: "左", 22: "右",
  23: "OK", 24: "音量+", 25: "音量-", 66: "回车", 67: "退格", 85: "播放/暂停", 86: "停止",
  87: "下一曲", 88: "上一曲", 89: "快退", 90: "快进", 164: "静音" };
const MACRO_MAX_STEPS_FE = 20;                                 // = server.py MACRO_MAX_STEPS
const MACRO_MAX_DELAY_FE = 10000;                              // = server.py MACRO_MAX_DELAY
const MAX_KEYCODES_FE = 32;                                    // = server.py MAX_KEYCODES
const MAX_TEXT_LEN_FE = 5000;                                  // = server.py MAX_TEXT_LEN
const MACRO_STEP_TYPES_FE = ["key", "text", "app"];            // = server.py MACRO_STEP_TYPES
const MACRO_APP_ID_RE_FE = /^[A-Za-z][A-Za-z0-9_.\-]+$/;       // = server.py APP_ID_RE
const MACRO_STEP_EST_FE = { key: 200, text: 600, app: 1200 };  // 单步耗时估值 ms（非服务端行为）
const macroDryFmtSec = (ms) => (Math.round(ms / 100) / 10).toString().replace(/\.0$/, "") + "s";

/* 与服务端 _macro_step_delay 同规则：非数字按 0、钳到 [0, MACRO_MAX_DELAY]。
   返回 { ms, warn }——warn 是「服务端不拒绝、但和你想要的不一样」的那种事。 */
function macroDryDelay(st) {
  const d = st.delay;
  if (typeof d !== "number" || !Number.isFinite(d)) return { ms: 0, warn: "延时不是数字，按 0 处理" };
  if (d < 0) return { ms: 0, warn: "负延时按 0 处理" };
  if (d > MACRO_MAX_DELAY_FE) {
    return { ms: MACRO_MAX_DELAY_FE, warn: `延时 ${macroDryFmtSec(d)} 超过上限，会被钳到 ${macroDryFmtSec(MACRO_MAX_DELAY_FE)}` };
  }
  return { ms: Math.round(d), warn: "" };
}

/* 纯函数：m = 解析后的宏对象，env = { imeCurrent, curType, knownPkgs }。
   返回 { rows: [{ i, level, text, note }], errs, warns, steps, totalMs, ok }。 */
function macroDryRun(m, env) {
  env = env || {};
  const imeCurrent = !!env.imeCurrent;
  const curType = env.curType || "";
  const knownPkgs = env.knownPkgs || [];
  const rows = [];
  let errs = 0, warns = 0, totalMs = 0;
  const add = (i, level, text, note) => {
    rows.push({ i, level, text, note: note || "" });
    if (level === "err") errs++;
    else if (level === "warn") warns++;
  };
  if (typeof m !== "object" || m === null || Array.isArray(m)) {
    add(0, "err", "顶层必须是对象（含 name 和 steps）", "服务端会整条拒绝");
    return { rows, errs, warns, steps: 0, totalMs: 0, ok: false };
  }
  const steps = Array.isArray(m.steps) ? m.steps : null;
  if (!steps || steps.length < 1 || steps.length > MACRO_MAX_STEPS_FE) {
    add(0, "err", `宏步骤须为 1~${MACRO_MAX_STEPS_FE} 项的数组（当前 ${steps ? steps.length : "不是数组"}）`,
      "服务端会整条拒绝，下面的逐条结果仅供参考");
  }
  if (!m.name) add(0, "warn", "这条宏没有 name", "运行记录里不好认；存为本机宏时也会被拒");
  (steps || []).forEach((st, idx) => {
    const i = idx + 1;
    if (typeof st !== "object" || st === null || Array.isArray(st)) {
      add(i, "err", "步骤必须是对象", "服务端会拒绝");
      return;
    }
    const t = st.type || "delay";   // 只带 delay 的步骤 = 纯等待（同服务端口径）
    const dl = "delay" in st ? macroDryDelay(st) : { ms: 0, warn: "" };
    totalMs += dl.ms;
    if (MACRO_STEP_TYPES_FE.indexOf(t) < 0 && t !== "delay") {
      add(i, "err", `类型 ${t} 不支持（可用 ${MACRO_STEP_TYPES_FE.join(" / ")}）`,
        (dl.warn ? dl.warn + "；" : "") + "服务端会拒绝");
      return;
    }
    if (t === "key") {
      const raw = "codes" in st ? st.codes : st.code;
      const codes = Array.isArray(raw) ? raw : [raw];
      const text = `按键 ${codes.map((c) => MACRO_DRY_KEY_NAMES[c] || `键码 ${c}`).join("、")}${codes.length > 1 ? ` ×${codes.length}` : ""}`;
      totalMs += MACRO_STEP_EST_FE.key;
      if (!codes.length || codes.length > MAX_KEYCODES_FE) {
        add(i, "err", text, (dl.warn ? dl.warn + "；" : "") + `键码数量须为 1~${MAX_KEYCODES_FE} 个`);
      } else if (!codes.every((c) => /^[0-9]+$/.test(String(c).replace(/^-+/, "")))) {
        add(i, "err", text, (dl.warn ? dl.warn + "；" : "") + "键码必须是数字");
      } else {
        const neg = codes.some((c) => Number(c) < 0);
        const warn = neg || !!dl.warn;   // 延时告警也要把整行降级，别让「会被钳到 10s」看起来一切正常
        const knotes = [];
        if (dl.warn) knotes.push(dl.warn);
        if (neg) knotes.push("负键码在设备上会执行失败");
        add(i, warn ? "warn" : "ok", text, knotes.join("；"));
      }
      return;
    }
    if (t === "text") {
      const text = typeof st.text === "string" ? st.text : "";
      totalMs += MACRO_STEP_EST_FE.text;
      if (!text.trim()) {
        add(i, "err", "输入空文本", (dl.warn ? dl.warn + "；" : "") + "服务端会拒绝");
        return;
      }
      if (text.length > MAX_TEXT_LEN_FE) {
        add(i, "err", `输入「${text.slice(0, 12)}…」`, (dl.warn ? dl.warn + "；" : "") + `文本超长（${text.length} > ${MAX_TEXT_LEN_FE}）`);
        return;
      }
      const notes = [];
      if (dl.warn) notes.push(dl.warn);
      if (/[^\x20-\x7E]/.test(text) && !imeCurrent && curType !== "appletv") {
        notes.push("含非 ASCII 且当前输入法不是 ADBKeyboard，中文/Emoji 会静默丢失");
      }
      if (/[\r\n]/.test(text)) notes.push("换行会被替换成空格再发送");
      add(i, notes.length ? "warn" : "ok", `输入「${text.slice(0, 16)}${text.length > 16 ? "…" : ""}」`, notes.join("；"));
      return;
    }
    if (t === "app") {
      const pkgs = (st.pkg ? [st.pkg] : []).concat(Array.isArray(st.pkgs) ? st.pkgs : []);
      const good = pkgs.filter((p) => typeof p === "string" && MACRO_APP_ID_RE_FE.test(p));
      totalMs += MACRO_STEP_EST_FE.app;
      if (!good.length) {
        add(i, "err", "启动应用：包名不合法", (dl.warn ? dl.warn + "；" : "") + "pkg/pkgs 至少要有一个形如 com.foo.bar 的包名");
        return;
      }
      const more = good.length > 1 ? `（${good.length} 个候选包，成功一个就停）` : "";
      const unknown = good.filter((p) => knownPkgs.indexOf(p) < 0);
      const notes = [];
      if (dl.warn) notes.push(dl.warn);
      if (unknown.length) {
        notes.push(`${unknown.join("、")} 不在常见列表，设备上可能没装（失败不中断整条宏）`);
      }
      add(i, notes.length ? "warn" : "ok", `启动 ${good[0]}${more}`, notes.join("；"));
      return;
    }
    add(i, dl.warn ? "warn" : "ok", `等 ${macroDryFmtSec(dl.ms)}`, dl.warn);
  });
  return { rows, errs, warns, steps: steps ? steps.length : 0, totalMs, ok: errs === 0 };
}
/* ===== macro-dry-run:end ===== */
/* ===== perf-waterfall:begin（纯函数段，node 单测 harness 原样抽取执行；禁引用 DOM / 全局态） ===== */
/* 阈值：超过这条线算「慢」（ms）。按 kind 分开定：shell 在常驻连接上跑，一条命令本就该快；
   adb 每次都要 fork 进程，天然慢一截；devices 命中缓存恒 0，永不告警。 */
const PERF_SLOW_FE = { adb: 150, shell: 250, pyatv: 250, devices: 0 };
/* 徽标文字：把 kind 翻成前端说法（AppleTV / 缓存） */
const PERF_KIND_NAMES_FE = { adb: "adb", shell: "shell", pyatv: "AppleTV", devices: "缓存" };
const PERF_MAX_BARS_FE = 40;          // 最多画多少行：环形缓冲 60 条，全画太密

const perfClamp01 = (v) => Math.min(1, Math.max(0, v));
const PERF_MS_FMT_FE = (ms) => {
  const v = Math.max(0, Number(ms) || 0);
  if (v < 1000) return Math.round(v) + "ms";
  const s = Math.round(v / 100) / 10;
  return (Number.isInteger(s) ? String(s) : s.toFixed(1)) + "s";
};

/* 行的严重级别：err（有失败原因）> cache（命中缓存，本就快）> warn（超过 kind 阈值）> ok。 */
function perfLevel(e) {
  e = e || {};
  if (e.err) return "err";
  if (e.cache) return "cache";
  const slow = PERF_SLOW_FE[e.kind] || 0;
  return slow > 0 && (Number(e.ms) || 0) >= slow ? "warn" : "ok";
}

/* 汇总 chips 的统计：与服务端 perf_snapshot().stats 同口径（p50/p95 线性插值）。 */
function perfPct(sorted, p) {
  if (!sorted.length) return 0;
  if (sorted.length === 1) return sorted[0];
  const k = (sorted.length - 1) * p;
  const lo = Math.floor(k), hi = Math.min(lo + 1, sorted.length - 1);
  return Math.round((sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo)) * 10) / 10;
}
function perfSummary(evs) {
  const list = Array.isArray(evs) ? evs : [];
  const mss = list.map((e) => Math.max(0, Number(e.ms) || 0)).sort((a, b) => a - b);
  const n = list.length;
  const hits = list.filter((e) => e.cache).length;
  const errs = list.filter((e) => e.err).length;
  return { n, p50: perfPct(mss, 0.5), p95: perfPct(mss, 0.95),
    max: mss.length ? mss[mss.length - 1] : 0, errs, cache: hits,
    cacheRate: n ? Math.round((hits / n) * 1000) / 1000 : 0 };
}

/* 瀑布窗口（ms）：盖住最老一条并留 1s 余量；没有调用时给默认宽度。
   与服务端 window_ms 同规则——前端只认 ago，不依赖时钟对齐。 */
function perfWindowMs(evs) {
  const list = Array.isArray(evs) ? evs : [];
  const oldest = list.reduce((m, e) => Math.max(m, Math.max(0, Number(e.ago) || 0)), 0);
  return Math.max(2000, oldest + 1000);
}

/* 一条事件 → 行模型。几何：右缘 = win-ago（右端永远是「现在」），长度 = 耗时；
   两个值都 clamp 到 [0,1]——ago 超过 win 的旧事件服务端已滤掉，这里双保险。
   可见性交给 CSS min-width，这里不造假宽度。 */
function perfBar(e, win) {
  e = e || {};
  const w = win > 0 ? win : 1;
  const ago = Math.max(0, Number(e.ago) || 0);
  const ms = Math.max(0, Number(e.ms) || 0);
  const right = perfClamp01(1 - ago / w);
  const width = perfClamp01(ms / w);
  return { kind: PERF_KIND_NAMES_FE[e.kind] || e.kind || "adb",
    label: String(e.label == null ? "" : e.label), ms, level: perfLevel(e),
    note: e.err ? String(e.err) : (e.cache ? "缓存命中" : ""),
    left: perfClamp01(right - width), width };
}

/* 事件数组 → 行模型数组：onlyProblems 只留 warn/err；超过 PERF_MAX_BARS_FE 条取最新；
   返回最新在上。 */
function perfBars(evs, opts) {
  opts = opts || {};
  const win = opts.windowMs > 0 ? opts.windowMs : 2000;
  let list = (Array.isArray(evs) ? evs : []).slice();
  if (opts.onlyProblems) {
    list = list.filter((e) => { const lv = perfLevel(e); return lv === "warn" || lv === "err"; });
  }
  if (list.length > PERF_MAX_BARS_FE) list = list.slice(list.length - PERF_MAX_BARS_FE);
  return list.map((e) => perfBar(e, win)).reverse();
}
/* ===== perf-waterfall:end ===== */

/* perf 的 DOM 侧：诊断卡片，轮询与 8s 状态轮询解耦；面板滚出视口或页面隐藏就停。
   行内容来自服务端快照（label 可能是 adb 命令行），一律 textContent。 */
let perfOnlyProblems = false;
let perfLastData = null;
const PERF_POLL_FE = 2000;

const perfMk = (tag, cls, txt) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (txt != null) el.textContent = txt;
  return el;
};

function perfRender(data) {
  perfLastData = data;
  const evs = (data && data.events) || [];
  const st = (data && data.stats) || perfSummary([]);
  const win = (data && data.window_ms) || perfWindowMs(evs);
  const sum = $("#perfSum");
  sum.textContent = "";
  const chips = [st.n + " 次调用", "p50 " + PERF_MS_FMT_FE(st.p50), "p95 " + PERF_MS_FMT_FE(st.p95),
    "最长 " + PERF_MS_FMT_FE(st.max), "缓存命中 " + st.cache + "/" + st.n];
  if (st.errs) chips.push("失败 " + st.errs);
  chips.forEach((c) => sum.append(perfMk("span", "pchip", c)));
  sum.classList.toggle("err", !!st.errs);
  const rows = $("#perfRows");
  rows.textContent = "";
  perfBars(evs, { onlyProblems: perfOnlyProblems, windowMs: win }).forEach((b) => {
    const row = perfMk("div", "prow " + b.level);
    row.append(perfMk("span", "pkind", b.kind));
    row.append(perfMk("span", "plabel", b.label));
    const track = perfMk("div", "ptrack");
    const fill = perfMk("div", "pfill");
    fill.style.left = (b.left * 100).toFixed(2) + "%";
    fill.style.width = (b.width * 100).toFixed(3) + "%";
    track.append(fill);
    row.append(track);
    row.append(perfMk("span", "pms", PERF_MS_FMT_FE(b.ms)));
    if (b.note) row.append(perfMk("span", "pnote", b.note));
    rows.append(row);
  });
  const meta = $("#perfMeta");
  if (meta) {
    let m = "";
    const info = data && data.adb;
    if (info) {
      m = "adb " + (info.version || "?") + " · shell " + (info.shell ? "常驻" : "未建立")
        + " · devices 缓存 " + info.ttl + "s";
    }
    if (st.hidden) m += (m ? " · " : "") + st.hidden + " 条超出窗口未显示";
    meta.textContent = m;
  }
}

async function perfFetch() {
  try { perfRender(await api("/api/perf")); }
  catch (e) { /* 诊断卡片静默失败：下个轮询周期再试，不打断正常遥控 */ }
}
const perfCardInViewport = () => {
  const el = $("#perfCard");
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < window.innerHeight;
};
setInterval(() => { if (pageVisible && perfCardInViewport()) perfFetch(); }, PERF_POLL_FE);
$("#perfRefreshBtn").addEventListener("click", perfFetch);
$("#perfOnlyBtn").addEventListener("click", () => {
  perfOnlyProblems = !perfOnlyProblems;
  $("#perfOnlyBtn").setAttribute("aria-pressed", perfOnlyProblems ? "true" : "false");
  if (perfLastData) perfRender(perfLastData);   // 本地重画，不再发请求
});


/* 预演的 DOM 侧：环境信息（输入法 / 设备类型 / 常见包名）只在调用时取，
   上面的纯函数段因此可以整段搬进 node 单测。行内容全部来自用户 JSON，一律 textContent。 */
const KNOWN_PKGS_FOR_DRY = () => Array.from(new Set(APPS.map((a) => a.pkg).concat(Object.keys(NP_APPS))));

function macroShowDryRun() {
  const box = $("#macroDry");
  let m;
  try {
    m = macroParse();
  } catch (e) {
    box.classList.remove("hidden");
    box.textContent = "";
    const s = document.createElement("p");
    s.className = "mdrysum err";
    s.textContent = "⚠ JSON 解析失败：" + e.message;
    box.appendChild(s);
    return;
  }
 const r = macroDryRun(m, {
   imeCurrent: imeState.current,
   curType: status.curType,
   knownPkgs: KNOWN_PKGS_FOR_DRY(),
 });
  // 忘了这行 = 内容渲染好了但面板仍带 hidden，用户点了「预演」什么都没发生
  box.classList.remove("hidden");
  box.textContent = "";
  const sum = document.createElement("p");
  sum.className = "mdrysum " + (r.errs ? "err" : r.warns ? "warn" : "ok");
  const parts = [`${r.steps} 步`, `预计约 ${macroDryFmtSec(r.totalMs)}`];
  if (r.errs) parts.push(`${r.errs} 处会被服务端拒绝`);
  if (r.warns) parts.push(`${r.warns} 处提醒`);
  sum.textContent = (r.errs ? "✕ " : r.warns ? "⚠ " : "✓ ") + parts.join(" · ")
    + (r.errs ? "——先修再跑" : r.warns ? "——可以跑，提醒看着办" : "——可以放心跑");
  box.appendChild(sum);
  r.rows.forEach((row) => {
    const el = document.createElement("div");
    el.className = "drow " + row.level;
    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = row.i ? String(row.i) : "·";
    const lvl = document.createElement("span");
    lvl.className = "lvl";
    lvl.textContent = row.level === "err" ? "✕" : row.level === "warn" ? "⚠" : "✓";
    const tx = document.createElement("span");
    tx.className = "dtext";
    tx.textContent = row.text;
    tx.title = row.text + (row.note ? " — " + row.note : "");
    const note = document.createElement("span");
    note.className = "dnote";
    note.textContent = row.note;
    el.append(idx, lvl, tx, note);
    box.appendChild(el);
  });
  buzz(r.errs ? 30 : 12);
}

$("#macroDryBtn")?.addEventListener("click", macroShowDryRun);
// Ctrl/Cmd + Enter 预演：与「运行」分开——预演是零副作用的
$("#macroText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    macroShowDryRun();
  }
});

/* ---------------- 正在播放（Now Playing） ----------------
   学 Google TV 官方遥控 / Kodi Remote 的常驻信息条。数据源 /api/nowplaying
   （服务端 dumpsys media_session，3.5s 缓存 + 休眠不查）；轮询蹭 8s 状态轮询的
   车，页面隐藏时随之暂停（pageVisible）。进度条在两次轮询之间本地插值——
   否则 8s 一跳像卡带。 */
const NP = { playing: false, title: "", artist: "", app: "", duration: 0, position: 0, at: 0, shown: false };
// 常见 App 包名 → 中文名；未知包名退化为末段（com.foo.bar → bar）
const NP_APPS = {
  "com.google.android.youtube.tv": "YouTube", "com.netflix.ninja": "Netflix",
  "com.amazon.amazonvideo.livingroom": "Prime Video", "com.disney.disneyplus": "Disney+",
  "com.spotify.tv.android": "Spotify", "com.plexapp.android": "Plex",
  "org.xbmc.kodi": "Kodi", "tv.twitch.android.app": "Twitch",
  "com.bilibili.bilithings": "哔哩哔哩",
};

function npAppName(pkg) {
  if (!pkg) return "";
  return NP_APPS[pkg] || pkg.split(".").slice(-1)[0];
}

function npFmt(ms) {
  if (!ms || ms < 0) return "0:00";
  const s = Math.round(ms / 1000);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

function npPosition() {
  if (!NP.playing) return NP.position;
  return Math.min(NP.duration || Infinity, NP.position + (Date.now() - NP.at));
}

function npPaint() {
  const pos = npPosition(), dur = NP.duration;
  $("#npFill").style.width = (dur ? Math.min(100, pos / dur * 100) : 0) + "%";
  $("#npTime").textContent = dur ? `${npFmt(pos)} / ${npFmt(dur)}` : "";
}

setInterval(() => { if (NP.shown) npPaint(); }, 1000);

function renderNowPlaying(np) {
  const card = $("#npCard");
  // 没连上 / 查询失败 / 没有任何会话 → 收起卡片，别留一个假的进度条
  const show = !!(np && np.connected && !np.error && (np.title || np.playing));
  if (!show) {
    NP.shown = false;
    card.classList.add("hidden");
    npSeekSync();   // 收起卡片 / 直播无时长：快退快进一起禁用，别给点了没反应的按钮
    return;
  }
  NP.shown = true;
  Object.assign(NP, np, { at: Date.now() });
  card.classList.remove("hidden");
  // 片名 / 艺人来自电视侧元数据，可被伪造或乱码 → textContent，禁 innerHTML
  $("#npIcon").textContent = "🎬";   // 字形只用在渲染充分的 emoji 上；⏸/⏹ 在部分字体缺字形会变豆腐块
  $("#npTitle").textContent = np.title || npAppName(np.app) + " 正在播放";
  const sub = [np.artist, np.playing ? "播放中" : "已暂停", npAppName(np.app)].filter(Boolean);
  $("#npSub").textContent = sub.join(" · ");
  $("#npToggle").textContent = np.playing ? "暂停" : "播放";
  npPaint();
  npSeekSync();
}

async function refreshNowPlaying() {
  try {
    renderNowPlaying(await api("/api/nowplaying"));
  } catch (e) {
    renderNowPlaying(null);
  }
}

$("#npToggle").addEventListener("click", async () => {
  try {
    await api("/api/cmd", { type: "key", code: 85 });   // 85 = 播放/暂停
    setTimeout(refreshNowPlaying, 500);   // 媒体状态切换有延迟，别读到旧缓存
  } catch (e) { toast(e.message); }
});

/* ---------------- Now Playing 快退 / 快进（±15s） ----------------
   学 Plyr（MIT 开源播放器 src/js/controls.js）的三个决定：
   1. 固定偏移 seek（seekTime，默认 10s）不做任意 scrub——电视端 adb 只能发系统
      快退/快进键（89/90），幅度由当前 App 决定（YouTube ±10s，多数播放器 10–15s）。
      所以按钮标签照 Plyr "Rewind 10s" 把秒数写在明处；与触摸板双指横滑（连发 89/90
      按次数计）互补：那个隐式，这个显式一步。
   2. Plyr 对直播/无时长（hls.js/dash.js 用 2**32 哨兵值）隐藏进度、不给 seek 控件。
      本地同理：NP.duration 缺失时整行禁用变暗——直播里快退没有意义。
   3. seek 后回读一次：media_session 更新有延迟，800ms 后拿真值校准本地进度，
      不靠本地推算（seek 多远本地不知道）。 */
function npSeekSync() {
  const seekable = !!(NP.shown && NP.duration);
  $("#npSeek").classList.toggle("noseek", !seekable);
  $("#npSeekBack").disabled = !seekable;
  $("#npSeekFwd").disabled = !seekable;
}

$("#npSeekBack").addEventListener("click", () => {
  sendKey(89);
  setTimeout(refreshNowPlaying, 800);   // seek 让本地缓存作废，回读真值
});
$("#npSeekFwd").addEventListener("click", () => {
  sendKey(90);
  setTimeout(refreshNowPlaying, 800);
});

/* ---------------- 音量 OSD（本地反馈） ----------------
   学 Google TV 官方遥控：按音量键手机端先出声，不盯着电视也知键已送达。电视自己
   也会弹系统音量条，但 adb 发键是「发完就走」——本地一片安静，用户不知道送到没有。
   所有音量入口（按键区 / 物理键盘 / 触摸板双指 / 长按连发）最后都汇到 sendKey，
   所以挂钩只写这一处。乐观步进 + 后端校正：查询要走 adb 有延迟，OSD 先动、220ms
   后拿真值纠偏；真值拿不到（老 ROM 两个命令都不认）就退成图标模式，不出格数。 */
const VOL_KEYS = new Set([24, 25, 164]);
const VOL_HIDE_MS = 1600, VOL_SYNC_DEBOUNCE = 220;
const Vol = { level: -1, max: 15, muted: false, dead: false, hideTimer: null, syncTimer: null };
let volTargetKey = "";

function volIcon() {
  if (Vol.muted) return "🔇";
  if (Vol.level < 0) return "🔊";
  const p = Vol.level / Vol.max;
  return p >= 0.6 ? "🔊" : p > 0 ? "🔉" : "🔈";
}

function volPaint() {
  const osd = $("#volOsd");
  osd.classList.toggle("ismuted", Vol.muted);
  osd.classList.toggle("unknown", Vol.level < 0);
  $("#volIcon").textContent = volIcon();
  $("#volFill").style.width = Vol.level < 0 ? "100%" :
    Math.round(Vol.level / Vol.max * 100) + "%";
  $("#volNum").textContent = Vol.level < 0 ? "" : Vol.muted ? "静音" : Vol.level;
}

function volHide() {
  clearTimeout(Vol.hideTimer);
  Vol.hideTimer = null;
  $("#volOsd").classList.add("hidden");
}

async function volSync() {
  if (Vol.dead) return;
  try {
    const v = await api("/api/volume");
    if (!v) return;
    if (!v.connected || !v.supported) {
      // 没连电视 / 电视不报音量：本会话不再查，OSD 退化成图标模式（照样有送达反馈）
      Vol.dead = true;
      Vol.level = -1;
      volPaint();
      return;
    }
    Vol.dead = false;
    Vol.max = v.max || 15;
    Vol.level = v.level;
    // muted 只有后端真读到时（dumpsys audio）才是布尔值；media volume 通道读不到
    // 静音位，muted=null 表示「不知道」——保留本地乐观值，别把静音反馈抹掉
    if (typeof v.muted === "boolean") Vol.muted = v.muted;
    volPaint();
  } catch (e) { /* 网络抖动：保留乐观值，下一次按音量再纠 */ }
}

function volRetarget(key) {
  if (volTargetKey === key) return;
  volTargetKey = key;
  // 换设备（Apple↔Android、换电视）必须重新探：级数差得远（10 格 vs 25 格），
  // 拿 A 电视的级数显示 B 电视的音量会越调越错
  Vol.level = -1; Vol.dead = false; Vol.muted = false;
  volPaint();
}

// 按音量：本地先动（等 adb 回包就晚了），再防抖打一次真值纠正乐观值的漂移
function volBump(code) {
  if (!status.connected) return;          // 没连上按啥都报错，别弹多余的 OSD
  if (code === 24) { Vol.muted = false; if (Vol.level >= 0) Vol.level = Math.min(Vol.max, Vol.level + 1); }
  else if (code === 25) { Vol.muted = false; if (Vol.level >= 0) Vol.level = Math.max(0, Vol.level - 1); }
  else if (code === 164) { Vol.muted = !Vol.muted; }
  else return;                            // 非音量键，与 OSD 无关
  volPaint();
  $("#volOsd").classList.remove("hidden");
  clearTimeout(Vol.hideTimer);
  Vol.hideTimer = setTimeout(volHide, VOL_HIDE_MS);
  if (!Vol.dead) {
    clearTimeout(Vol.syncTimer);
    Vol.syncTimer = setTimeout(volSync, Vol.level < 0 ? 60 : VOL_SYNC_DEBOUNCE);
  }
}
/* ===== volset:begin
   音量精确设置（滑条 + 预设档）的纯规则段。第三十八轮，学 androidtv 0.0.75
   （MIT，Copyright (c) 2020 Jeff Irion，commit 343b74e）的 set_volume_level()：
   把音量设到「绝对格数」，而不是连按音量键去凑。此前本项目只有按键 + 只读 OSD，
   能看不能设——从 3 格调到 12 格要按 9 次，还容易按过头。

   三条口径与服务端 server.volume_set() 逐字对齐（tests/test_volume_set.py 钉住）：
   1) 先 round 后夹：basetv_async.py:830 的 int(min(max(round(x), 0.0), max_volume))。
      只夹不舍会把 7.6 卡成 7、只舍不夹会放出 16 / -1，电视侧命令直接报错。
      JS 的 Math.round 是半点向上（8.5→9），Python3 内建 round 是银行家舍入
      （8.5→8），服务端因此写成 int(raw + 0.5)；本段直接用 Math.round，天然同口径。
   2) max 一律按不可信输入处理：它来自上一次回读，可能是旧值；<=0 / 空 / 非数字
      退到 VOLSET_MAX_FALLBACK（15，STREAM_MUSIC 的常见上限）。老设备报 25 格。
   3) level >= 0 才叫「知道格数」。读通道全废（两个查询命令都不认）时 level 是 -1，
      此时整行停用、退回音量键 24/25——任何 ROM 都认按键，这才是降级方向。

   发出的命令有两条候选（学 constants.py:145/148，服务端 volume_set_cmd() 有序降级链）：
     media volume --show --stream 3 --set N      —— 读通道同款，先试；
     cmd media_session volume --show --stream 3 --set N —— Android 11+ 兜底。 */
VOLSET_MAX_FALLBACK = 15;

function volSetClamp(level, max) {
  let top = Number(max);
  if (!isFinite(top) || top <= 0) top = VOLSET_MAX_FALLBACK;
  top = Math.floor(top);
  const raw = Number(level);
  const lvl = isFinite(raw) ? Math.round(raw) : 0;
  return [Math.max(0, Math.min(lvl, top)), top];
}

function volSetPct(level, max) {
  const c = volSetClamp(level, max);
  return Math.round(c[0] / c[1] * 100);
}

function volSetPresets(max) {
  const c = volSetClamp(0, max);
  const top = c[1];
  const raw = [0, Math.round(top * 0.25), Math.round(top * 0.5), Math.round(top * 0.75), top];
  const out = [];
  for (const n of raw) {
    const v = Math.max(0, Math.min(n, top));
    if (out.indexOf(v) < 0) out.push(v);
  }
  return out;
}

function volSetReconcile(cur, fresh, dragging) {
  const out = { level: cur.level, max: cur.max, muted: cur.muted };
  if (!fresh || typeof fresh !== "object") return out;
  if (Number(fresh.max) > 0) out.max = Math.floor(Number(fresh.max));
  if (typeof fresh.muted === "boolean") out.muted = fresh.muted;
  if (!dragging && Number(fresh.level) >= 0) out.level = Math.floor(Number(fresh.level));
  return out;
}

function volSetUsable(v) {
  return !!(v && v.connected && v.supported && Number(v.level) >= 0);
}
/* ===== volset:end */

/* 上面是纯规则段；下面是 DOM 胶水。分工的理由见 tests/volume_slider_harness.js 首注释：
   规则要能搬进 node 跑，胶水才允许摸 document / fetch / setTimeout。 */
const VolSet = { level: -1, max: 15, muted: false, dragging: false, dead: false };

function volSetPaint() {
  const row = $("#volSetRow"), slider = $("#volSlider");
  if (!row || !slider) return;
  const usable = !VolSet.dead && VolSet.level >= 0;
  row.classList.toggle("dead", !usable);
  slider.disabled = !usable;
  slider.max = String(Math.max(1, VolSet.max));
  if (!VolSet.dragging) slider.value = String(usable ? VolSet.level : 0);
  $("#volSliderFill").style.width = usable ? volSetPct(VolSet.level, VolSet.max) + "%" : "0%";
  $("#volSetNum").textContent = usable ? (VolSet.muted ? "静音" : VolSet.level + "/" + VolSet.max) : "—";
  const mute = $("#volMuteBtn");
  if (mute) {
    mute.disabled = !usable;
    mute.textContent = VolSet.muted ? "🔇 已静音" : "🔈 静音";
  }
  document.querySelectorAll("#volPresets .btn").forEach((b) => {
    b.disabled = !usable;
    b.classList.toggle("on", usable && Number(b.dataset.level) === VolSet.level);
  });
}

function volSetRenderPresets() {
  const box = $("#volPresets");
  if (!box) return;
  box.textContent = "";
  volSetPresets(VolSet.max).forEach((n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.dataset.level = String(n);
    b.textContent = n === 0 ? "0" : String(n);
    b.title = "把音量设到第 " + n + " 格";
    b.setAttribute("aria-label", b.title);
    b.addEventListener("click", () => {
      if (!status.connected) { toast("先连上电视"); return; }
      volSetCommit(n);
    });
    box.appendChild(b);
  });
}

async function volSetSync() {
  try {
    const v = await api("/api/volume");
    if (!v) return;
    VolSet.dead = !v.connected;
    if (VolSet.dead) { volSetPaint(); return; }
    const m = volSetReconcile(VolSet, v, VolSet.dragging);
    VolSet.level = m.level; VolSet.max = m.max; VolSet.muted = m.muted;
    if (!v.supported && !VolSet.dragging) VolSet.level = -1;   // 读通道不报格数 → 整行停用
    volSetPaint();
  } catch (e) { /* 网络抖动：保留现值，下一次轮询再纠 */ }
}

async function volSetCommit(level) {
  const c = volSetClamp(level, VolSet.max);
  VolSet.level = c[0]; VolSet.max = c[1];
  volSetPaint();
  try {
    const r = await api("/api/volume", { level: c[0] });
    if (r && r.ok) {
      VolSet.level = Number(r.level);
      if (Number(r.max) > 0) VolSet.max = Math.floor(Number(r.max));
      volSetPaint();
    }
  } catch (e) {
    toast(e.message || "设置音量失败");
    volSetSync();      // 回读真值，别让滑条停在一个假格数上
  }
}

function volSetRetarget() {
  // 换设备必须重置：Apple TV 侧没有这条链路，Android 电视之间级数也差得远
  // （10 格 vs 25 格），拿 A 的级数显示 B 的音量会越调越错
  VolSet.level = -1; VolSet.max = 15; VolSet.muted = false;
  VolSet.dragging = false; VolSet.dead = false;
  volSetRenderPresets();
  volSetPaint();
}

/* ---------------- 状态与连接 ---------------- */
let statusBusy = null;  // 上一次 /api/status 没回来就不叠加下一次（慢响应会排在按键锁后面）

async function refreshStatus() {
  if (statusBusy) return statusBusy;
  statusBusy = (async () => {
    try {
      const s = await api("/api/status");
      renderStatus(s);
      // 正在播放蹭状态轮询的车（Android 且已连接才查；页面隐藏时整个轮询本来就停着）
      if (status.curType === "android" && status.connected) refreshNowPlaying();
      else renderNowPlaying(null);
      // 音量 OSD 的种子按「当前设备」走：换了设备要重新探级数（不主动查，
      // 下一次按音量时 volBump → volSync 顺手取真值）
      volRetarget((s.cur_type || "") + "|" + (s.current || ""));
      // 滑条同一时机换目标；Android 已连接才顺手回读一次格数（和服务端 0.8s TTL
      // 缓存合用，重复查询不花钱；换设备后第一次进页面就有值，不用先按音量键）
      volSetRetarget();
      if (status.curType === "android" && status.connected) volSetSync();
    } catch (e) {
      // 原来是静默 ignore：服务端挂了状态栏却还留着上一次的「已连接」，
      // 用户对着一个已经死掉的遥控器按半天。
      $("#dot").className = "dot off";
      $("#tvInfo").textContent = "连不上服务端：" + e.message;
      renderNowPlaying(null);
    } finally {
      statusBusy = null;
    }
  })();
  return statusBusy;
}

function renderStatus(s) {
  palStatus = s;
  window.__atvLastStatus = s;   // chip 菜单要用（Apple TV 行的 connected 判断）
  // 服务跑在手机 App 的 Chaquopy 引擎里时，「把遥控器装到手机」那张卡片没有意义：
  // 手机上都装好了，而且 APK 文件不在包里——点下载只会拿到 404「APK 不存在」。
  // 只看服务端标记、不看 UA：手机浏览器访问 Mac 的服务时卡片要保留。
  if (s.embedded) {
    const card = $("#phoneInstall");
    const box = card && card.closest("section.card");
    if (box) box.remove();
  }
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
  // Apple TV 没有这套 adb 重连，ar 传 null 让后缀为空（arBase 复位）
  renderAutoReconn(info, isApple ? "" : info.textContent, isApple ? null : s.auto_reconnect);

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
  $("#padHint").textContent = padHintText(isApple);
  $("#kbdHint").innerHTML = isApple
    ? '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>PageUp/Down</b> 快退/快进 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送（支持中文）。'
    : '点一下页面空白处，然后直接用键盘遥控：<b>方向键</b> 移动 · <b>回车</b>=OK · <b>Esc</b>=返回 · <b>退格</b>=删除 · <b>PageUp/Down</b> 翻页 · <b>媒体键</b> 播放控制。在输入框里打字则作为文本发送。';

  // 设备列表每 8 秒轮询一次，数据没变就别重建 DOM（会打断 hover / 触发无谓重排）
  const chipSig = JSON.stringify([
    s.cur_type, s.current, s.current_state, s.recent, s.devices,
    (s.appletv.devices || []).map((d) => d.id), s.adb_found,
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
    c.title = "点击连接 · 长按/右键打开菜单";
    c.onclick = () => connect(t);
    sheetBindChip(c, sheetDevOfRecent(s, t, wolTargets));
    rc.appendChild(c);
  });

  // Android 在线设备
  const dc = $("#deviceChips");
  dc.innerHTML = "";
  (s.devices || []).filter((d) => !(s.cur_type === "android" && d.serial === s.current)).forEach((d) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = `${d.serial}（${d.state === "device" ? "在线" : d.state}）`;
    c.title = "点击切换 · 长按/右键打开菜单";
    c.onclick = () => api("/api/switch", { target: d.serial }).then(refreshStatus).catch((e) => toast(e.message));
    sheetBindChip(c, sheetDevOfDevice(s, d));
    dc.appendChild(c);
  });

  renderEstate(s);   // chips 全空时给空状态：一句解释 + 一个下一步（规则见 empty 段）

  // 已配对的 Apple TV（未连接当前页也展示）
  if (isApple || !s.current) renderAtvKnown(s);

  // 连接上了就补「连接后」那一段引导（connect() 回来的这次渲染走这儿最合适）
  maybePostCoach();
  wolMaybeDiscover(s);
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
    refreshNowPlaying();   // 信息条不用等下一个 8s 轮询
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  } finally {
    $("#connectBtn").disabled = false;
  }
}

/* ---------------- 远程开机（Wake-on-LAN） ----------------
   规则在同名纯函数段里，这里只管渲染 / 发包 / 等待。
   等待是必需的：魔法包等效于「按一下电源键」，主板还要几十秒才起来并重新注册进 adb；
   不盯着状态，用户发完包只会对着一块黑屏发呆，不知道是没生效还是生效得慢。 */
let wolIpsCache = [];
let wolBusy = false;
let wolTimer = null;
let wolWaitLeft = 0;
let wolWaitIp = "";
let wolAskSig = "";
let wolAskAt = 0;
let wolLastStatus = null;
let wolTargets = [];   // 最近一次 WOL 发现结果，供 chip 菜单判断能否远程开机

function wolSetWatch(text) {
  $("#wolWatch").classList.toggle("hidden", !text);
  if (text) $("#wolWatchText").textContent = text;
}

async function wolDiscover(ips) {
  const ask = (Array.isArray(ips) && ips.length ? ips : wolIpsCache).slice(0, WOL_IP_MAX);
  if (!ask.length) {
    $("#wolCount").textContent = "—";
    // 说清「为什么是空的」：ARP 表只记得跟本机通信过的对端，这是 WoL 的前置条件
    $("#wolHint").textContent = "还没有可问的地址。先连过一次电视——本机 ARP 表里只会有"
      + "跟它通信过的条目，而 MAC 正是从那里来的。";
    return [];
  }
  wolIpsCache = ask;
  wolAskAt = Date.now();
  wolBusy = true;
  $("#wolRefreshBtn").disabled = true;
  try {
    const r = await api("/api/wol?action=discover&ips=" + encodeURIComponent(ask.join(",")));
    wolTargets = r.targets || [];
    wolRender(r.targets || [], r.asked || ask);
    return r.targets || [];
  } catch (e) {
    $("#wolHint").textContent = "⚠ 读取本机 ARP 表失败：" + e.message;
    return [];
  } finally {
    wolBusy = false;
    $("#wolRefreshBtn").disabled = false;
  }
}

function wolRender(targets, asked) {
  const box = $("#wolList");
  box.textContent = "";
  (Array.isArray(targets) ? targets : []).forEach((t) => {
    const row = document.createElement("div");
    row.className = "atvrow wolrow";
    const name = document.createElement("span");
    name.className = "atvname";
    name.textContent = t.ip;         // IP 一律 textContent：局域网广播可伪造
    const mac = document.createElement("span");
    mac.className = "wolmac";
    mac.textContent = wolMacShort(t.mac);
    const btn = document.createElement("button");
    btn.className = "btn tiny";
    btn.type = "button";
    btn.textContent = "⚡ 开机";
    btn.onclick = () => wolSend(t, btn);
    row.append(name, mac, btn);
    box.appendChild(row);
  });
  const n = (targets || []).length;
  $("#wolCount").textContent = n ? String(n) : "—";
  const miss = wolMissing(asked, targets);
  $("#wolHint").textContent = miss.length
    ? "未发现 MAC：" + miss.join("、") + "（开机后通信过一次就会进本机 ARP 表；不在同一二层网络则永远发现不到）"
    : (n ? "电视关机后网卡仍在低功耗监听广播。多数电视要在「网络设置」里打开 WoL / 远程唤醒。"
         : "本机 ARP 表里暂时没有这些地址的条目。");
  $("#wolRefreshBtn").disabled = false;
}

async function wolSend(t, btn) {
  const ip = (t && t.ip) || "";
  if (!t || !t.mac) return toast("⚠ 没有 " + (ip || "该设备") + " 的 MAC 地址，无法远程开机");
  const label = btn ? btn.textContent : "";
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/wol", { action: "send", mac: t.mac });
    const ports = (r.sent || []).join("/");
    toast("✓ 开机包已发往 " + ip + "（端口 " + ports + "）");
    log("⚡ 魔法包 → " + ip + " :" + ports);
    wolWaitStart(ip);
  } catch (e) {
    toast("⚠ 开机包发送失败：" + e.message);
    log("⚠ 魔法包发送失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

function wolWaitStart(ip) {
  wolWaitStop();
  wolWaitIp = ip;
  wolWaitLeft = WOL_WATCH_MAX;
  wolSetWatch("等待 " + ip + " 上线 · 最多 1 分钟");
  wolTimer = setTimeout(wolWaitTick, WOL_WATCH_MS);
}

function wolWaitStop() {
  if (wolTimer) { clearTimeout(wolTimer); wolTimer = null; }
  wolWaitLeft = 0;
  wolSetWatch("");
}

// 一次探测的结果只做一件事：决定继续等、还是收尾。online 判定必须同时看 IP 和 state
// （adb 会把 unauthorized / offline 也列在 devices 里），否则会以为早就上线了。
async function wolWaitTick() {
  if (!wolWaitLeft) return;          // 已被 wolWaitStop 抢先一步
  const ip = wolWaitIp;
  let online = false;
  try {
    const s = await api("/api/status");
    online = (s.devices || []).some((d) =>
      d && String(d.serial || "").split(":")[0] === ip && d.state === "device");
  } catch (e) {
    log("⚠ 等待上线期间状态查询失败：" + e.message);
  }
  wolWaitLeft -= 1;
  const plan = wolWatchPlan(WOL_WATCH_MAX - wolWaitLeft, online);
  if (plan.stop) {
    wolWaitStop();
    if (plan.why === "online") {
      toast("✓ " + ip + " 已开机上线，正在重连");
      log("✓ " + ip + " 已上线");
      wolMarkOnline(ip);
      connect(ip);
    } else {
      toast("⚠ 等了 1 分钟 " + ip + " 仍未上线：确认电视开了 WoL，且没被路由器 AP 隔离挡住");
    }
    return;
  }
  wolSetWatch("等待 " + ip + " 上线 · 剩余 " + wolWaitLeft + " 次探测");
  wolTimer = setTimeout(wolWaitTick, WOL_WATCH_MS);
}

function wolMarkOnline(ip) {
  const box = $("#wolList");
  Array.prototype.forEach.call(box.children, (row) => {
    const nm = row.querySelector(".atvname");
    row.classList.toggle("online", !!(nm && nm.textContent === ip));
  });
}

// 挂在设备 chips 渲染之后：ARP 表秒级不变，所以按 IP 签名 + 最短间隔节流，
// 别跟着 8s 状态轮询把本机 ARP 表反复读一遍。
function wolMaybeDiscover(s) {
  const ips = wolIpsFromStatus(s);
  const sig = ips.join(",");
  if (!sig) return;
  if (sig === wolAskSig && Date.now() - wolAskAt < WOL_ASK_MIN_MS) return;
  wolAskSig = sig;
  wolDiscover(ips);
}

$("#wolRefreshBtn").addEventListener("click", () => {
  if (wolBusy) return;
  wolDiscover(wolIpsCache.length ? wolIpsCache : wolIpsFromStatus(window.__atvLastStatus || {}));
});

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

/* ---- 空状态 DOM 胶水：规则在上方 empty 纯函数段，这里只管建 DOM / 映射动作 ---- */
let estateSig = "";
/* 快照只取 Android 连接面板视角：Apple TV 的 current 是另一页的事，不能算「有设备」 */
function estateSnap(s) {
  return {
    recent: s.recent || [],
    devices: s.devices || [],
    current: s.cur_type === "android" ? s.current : "",
    adb_found: !!s.adb_found,
  };
}
/* 图标：一台睡着的显示器（电源符号）。stroke 走 currentColor 跟随双主题；
   手搓 createElementNS，不引图标库、不碰 innerHTML。 */
function estateIcon() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 64 48");
  svg.setAttribute("width", "64");
  svg.setAttribute("height", "48");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("aria-hidden", "true");
  const el = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach((k) => n.setAttribute(k, attrs[k]));
    return n;
  };
  svg.append(
    el("rect", { x: 8, y: 6, width: 40, height: 28, rx: 4 }),   // 屏幕
    el("path", { d: "M24 34h12" }),                             // 底座颈
    el("path", { d: "M17 41h26" }),                             // 底座
    el("path", { d: "M28 14v8" }),                              // 电源竖线
    el("path", { d: "M22.5 18.5a7.5 7.5 0 0 1 11 0" }),         // 电源弧
  );
  return svg;
}
function estateBtn(label, cls, fn, title) {
  const b = document.createElement("button");
  b.className = "btn " + cls;
  b.textContent = label;
  if (title) b.title = title;
  b.onclick = fn;
  return b;
}
/* 动作键 -> 真行为：reconnect 顺手把 IP 填进输入框，让「连的哪台」可见 */
function estateAct(act, ip) {
  if (act === "scan") return adbScan;
  if (act === "focus") return () => $("#targetInput").focus();
  if (act === "reconnect") return () => { $("#targetInput").value = ip; connect(ip); };
  return null;
}
function renderEstate(s) {
  const box = $("#estateAndroid");
  if (!box) return;
  const snap = estateSnap(s);
  const kind = emptyKind(snap);
  if (kind === EMPTY_NONE) {
    if (!box.classList.contains("hidden")) {
      box.classList.add("hidden");
      box.textContent = "";
      estateSig = "";
    }
    return;
  }
  const copy = emptyCopy(kind, snap.recent);
  const sig = emptySig(kind, snap.recent.length);
  if (sig === estateSig) return;
  estateSig = sig;
  box.textContent = "";
  const ic = estateIcon();
  ic.setAttribute("class", "eicon");   // SVG 的 className 是只读的 SVGAnimatedString，赋值会抛
  const title = document.createElement("div");
  title.className = "etitle";
  title.textContent = copy.title;
  const desc = document.createElement("div");
  desc.className = "edesc";
  desc.textContent = copy.desc;
  box.append(ic, title, desc);
  const row = document.createElement("div");
  row.className = "ecta";
  const acts = [[copy.cta, copy.act, "primary"], [copy.cta2, copy.act2, ""]];
  acts.forEach((a) => {
    const fn = estateAct(a[1], snap.recent[0]);
    if (a[0] && fn) row.appendChild(estateBtn(a[0], a[2], fn, copy.title));
  });
  if (row.childElementCount) box.appendChild(row);
  box.classList.remove("hidden");
}

/* ---- 底部快捷菜单 DOM 胶水：规则在上方 sheet 纯函数段，这里只管手势 / 渲染 / 动作分发 ----
   长按语义与触摸板同源：pointerdown 起 450ms 定时，位移超过 10px 视为要滚动列表、
   取消长按；松手时若长按已触发，接下来那一次 click 是「长按松手」产生的，必须吞掉，
   否则又把设备连了一遍（chip 原有 onclick 绑定在前，只能包一层）。
   桌面端右键（contextmenu）直达菜单。复制走 clipboard API、失败回落 execCommand——
   局域网是明文 http，navigator.clipboard 在非安全上下文里根本不存在。 */
const SHEET_HOLD_MS = 450;      // 长按判定：与触摸板长按连发同一量级
const SHEET_MOVE_PX = 10;       // 位移超过它就当用户想滚动，取消长按
let sheetHoldTimer = null;
let sheetHoldCtx = null;        // 进行中的长按：{ dev, el, x0, y0 }
let sheetHoldConsumed = false;  // 长按已触发：接下来那次 click 要吞掉

function sheetCancelHold() {
  if (sheetHoldTimer) { clearTimeout(sheetHoldTimer); sheetHoldTimer = null; }
  sheetHoldCtx = null;
}

// Apple TV 行里自带的按钮（连接 / 配对 / ✕）有各自的点击语义，长按与右键都不该被菜单劫走
function sheetOnControl(e) {
  const t = e.target;
  return !!(t && t.closest && t.closest("button, a, input, select, textarea"));
}

// el：chip / 设备行；dev：纯函数段构造的设备快照
function sheetBindChip(el, dev) {
  if (!el || !dev) return;
  el.addEventListener("pointerdown", (e) => {
    if (sheetOnControl(e)) return;                            // 控件上的手势归控件自己
    if (e.pointerType === "mouse" && e.button !== 0) return;  // 鼠标右键走 contextmenu
    sheetHoldConsumed = false;
    sheetCancelHold();
    const c = { dev, el, x0: e.clientX, y0: e.clientY };
    sheetHoldCtx = c;
    sheetHoldTimer = setTimeout(() => {
      sheetHoldTimer = null;
      sheetHoldCtx = null;
      sheetHoldConsumed = true;
      buzz(15);                 // 触觉确认：长按命中了（设置里可关）
      sheetOpen(c.dev, c.el);
    }, SHEET_HOLD_MS);
  });
  el.addEventListener("pointermove", (e) => {
    if (sheetHoldCtx && sheetHoldCtx.el === el &&
        Math.hypot(e.clientX - sheetHoldCtx.x0, e.clientY - sheetHoldCtx.y0) > SHEET_MOVE_PX) {
      sheetCancelHold();
    }
  });
  const holdUp = () => sheetCancelHold();
  el.addEventListener("pointerup", holdUp);
  el.addEventListener("pointercancel", holdUp);
  // 包一层既有 onclick：长按松手会补一次 click，直接放行就又把设备连/切了一遍
  const prevClick = el.onclick;
  el.onclick = (e) => {
    if (sheetHoldConsumed) { sheetHoldConsumed = false; e.preventDefault(); e.stopPropagation(); return; }
    if (prevClick) prevClick.call(el, e);
  };
  el.addEventListener("contextmenu", (e) => {
    if (sheetOnControl(e)) return;   // 按钮上让浏览器出原生菜单
    e.preventDefault();
    sheetOpen(dev, el);
  });
}

function sheetOpen(dev, trigger) {
  const box = $("#sheet");
  if (!box || !dev) return;
  $("#sheetTitle").textContent = dev.label || dev.target;
  $("#sheetSub").textContent = sheetStateText(dev);
  const list = $("#sheetActs");
  list.textContent = "";
  sheetItems(dev).forEach((it) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sheetact" + (it.danger ? " danger" : "");
    b.setAttribute("role", "menuitem");
    b.textContent = it.label;   // 文案一律 textContent：设备名 / IP 来自局域网广播，可伪造
    b.onclick = () => { closeModal("#sheet"); sheetAct(it.id, dev); };
    list.appendChild(b);
  });
  openModal("#sheet", trigger || document.activeElement);
}

/* ===== diagnostics:begin =====
   一键体检报告（第三十四轮）：把 /api/diagnostics 的白名单 JSON 变成人能读的文本，交给
   既有的 sheetCopy（clipboard API + execCommand 兜底——局域网是明文 http、属非安全上下文，
   剪贴板 API 不一定存在）贴进群 / issue。
   纯函数段：不碰 DOM / 网络 / 本地存储，由 tests/diagnostics_harness.js 原样抽走执行。 */
function diagYes(v) { return v ? "是" : "否"; }
function diagImeLine(ime) {
  if (!ime) return "中文输入: 未查询（当前不是 Android TV 或 adb 不可用）";
  return "中文输入: 已装 " + diagYes(ime.installed) + " / 已启用 " + diagYes(ime.enabled)
    + " / 当前 " + diagYes(ime.current) + "（" + (ime.default_ime || "未知") + "）";
}
function diagReportText(d) {
  d = d || {};
  const pf = d.platform || {}, adb = d.adb || {}, cur = d.current || {};
  const atv = d.appletv || {}, auth = d.auth || {}, mdns = d.mdns || {};
  const devs = adb.devices || [];
  const lines = [];
  // filter(Boolean)：release / machine 缺失时不留下连续空格（空 payload 也要能看）
  const plat = [pf.system || "?", pf.release, pf.machine].filter(Boolean).join(" ");
  lines.push("ATV Remote 诊断报告 v" + (d.version || "未知") + " · " + plat + " · Python "
    + (pf.python || "?") + " · 内嵌引擎 " + diagYes(d.embedded));
  lines.push("adb: " + (adb.found ? "已找到 " + (adb.path || "") : "未找到")
    + " · " + (adb.version || "版本未知") + " · 常驻 shell "
    + (adb.shell_alive ? "存活" : "无") + " · 设备缓存 " + (adb.devices_cache_ttl || 0) + "s");
  lines.push("设备: " + devs.length + " 台" + (devs.length ? "" : "（一台都没扫到）"));
  devs.forEach((dv) => lines.push("  - " + dv.serial + " [" + dv.state + "]"));
  lines.push("当前: " + (cur.target ? (cur.type === "appletv" ? "Apple TV " : "Android TV ")
    + cur.target + " [" + (cur.state || "未知") + "]" : "未连接")
    + " · 最近 Android 设备 " + (cur.recent_android_count || 0) + " 台");
  lines.push(diagImeLine(d.ime));
  lines.push("Apple TV: pyatv " + (atv.pyatv_available ? "可用" : "不可用")
    + " · 已配对 " + (atv.paired_count || 0) + " 台 · 已连接 " + diagYes(atv.connected));
  lines.push("局域网令牌: " + (auth.token_enabled ? "已开启（" + auth.mode + "）" : "未开启"));
  lines.push("mDNS: dns-sd " + (mdns.dns_sd_available ? "可用" : "不可用"));
  return lines.join("\n");
}
/* ===== diagnostics:end ===== */
// 明文 http 的局域网上 navigator.clipboard 不存在（非安全上下文），必须留 execCommand 兜底
function sheetCopy(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(() => true, () => sheetCopyFallback(text));
    }
  } catch (e) { /* 老 Safari 访问 navigator.clipboard 也可能抛，落兜底 */ }
  return Promise.resolve(sheetCopyFallback(text));
}
function sheetCopyFallback(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return !!ok;
  } catch (e) { return false; }
}

// 动作分发：只把 id 映射到既有入口，状态判断都在 sheetItems 里做完了
async function sheetAct(id, dev) {
  const t = dev.target;
  const ip = wolIpOf(t);
  try {
    if (id === "copy") {
      const ok = await sheetCopy(t);
      toast(ok ? "已复制 " + t : "复制失败，请长按手动选择");
      return;
    }
    if (id === "connect") { connect(t); return; }
    if (id === "switch") { await api("/api/switch", { target: t }); refreshStatus(); return; }
    if (id === "disconnect") {
      if (dev.kind === "appletv") await api("/api/atv/disconnect", { id: t });
      else await api("/api/disconnect", {});
      refreshStatus();
      return;
    }
    if (id === "wake") {
      let mac = (wolTargets.find((w) => w.ip === ip) || {}).mac;
      if (!mac) {   // 缓存没有（WOL 卡片还没自动发现过）就为这一台现查一次 ARP
        const r = await api("/api/wol?action=discover&ips=" + encodeURIComponent(ip));
        mac = ((r.targets || []).find((w) => w.ip === ip) || {}).mac;
      }
      if (!mac) {
        toast("⚠ 没查到 " + ip + " 的 MAC：它可能从没和本机通信过（不在同一二层网络也查不到）");
        return;
      }
      await api("/api/wol", { action: "send", mac });
      toast("✓ 开机包已发往 " + ip);
      wolWaitStart(ip);
      return;
    }
    if (id === "forget") {
      if (dev.kind === "appletv") await api("/api/atv/forget", { id: t });
      else await api("/api/forget", { target: t });
      toast("已移除 " + (dev.kind === "appletv" ? dev.label : t));
      refreshStatus();
      return;
    }
  } catch (e) {
    toast("⚠ " + e.message);
  }
}
// 遮罩点击关闭：触屏没有 Esc，必须能点外面关掉（与 shotModal/keymapModal 同一模式）
$("#sheet").addEventListener("click", (e) => { if (e.target === $("#sheet")) closeModal("#sheet"); });

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

function atvRow(dev, s) {
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
  sheetBindChip(row, sheetDevOfAtv(s || window.__atvLastStatus || {}, dev));
  return row;
}

function renderAtvFound(devs, s) {
  const box = $("#atvList");
  box.innerHTML = "";
  if (!devs.length) {
    box.innerHTML = '<p class="hint">未发现 Apple TV：确认电视与本机同网段、已唤醒（Apple TV 3 及更早型号不支持）。</p>';
    return;
  }
  devs.forEach((d) => box.appendChild(atvRow(d, s)));
}

function renderAtvKnown(s) {
  const box = $("#atvList");
  if (!box.dataset.scanned) return; // 扫描结果优先展示，未扫描时展示已配对
  renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })), s);
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
    box.classList.add("rows");   // Apple 应用列表同样切行：每行带星标可固定
    (r.apps || []).forEach((a) => {
      const row = document.createElement("div");
      row.className = "atvrow favrow pinrow";
      const b = document.createElement("button");
      b.className = "btn";
      b.textContent = a.name || a.id;
      MACRO_PKG_NAMES[a.id] = a.name || a.id;   // 让宏步骤列表能显示中文名
      b.onclick = () => launchApp(a.name || a.id, a.id);
      row.append(b, favStarBtn(a.name || a.id, a.id));   // 星标 → 固定到收藏夹
      box.appendChild(row);
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
  let hold = null, rep = null, edit = null;
  const stop = () => {
    clearTimeout(hold); clearInterval(rep); clearTimeout(edit);
    hold = rep = edit = null;
    btn.classList.remove("pressed");
    if (activeHoldStop === stop) activeHoldStop = null;
  };
  const tick = () => kmSend(code);
  const bound = () => kmMap[String(code)];

  btn.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    stopActiveHold();                 // 换键重按：先停掉上一个
    buzz();                           // 先震后发：延迟由网络决定，反馈不能等
    kmSend(code);                     // 统一出口：没改绑就发自己（历史行为）
    btn.classList.add("pressed");     // 按压态全程保持，stop() 里摘掉
    if (kmHoldable(bound())) {         // 连发按绑定目标推导：启动器/宏不连发
      hold = setTimeout(() => { rep = setInterval(tick, HOLD_RATE); }, HOLD_DELAY);
      activeHoldStop = stop;
    }
    // 长按进改键弹窗：触发时先停连发，别让用户白白多发几个键
    edit = setTimeout(() => { stop(); kmOpen(code, btn); }, KM_EDIT_MS);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) =>
    btn.addEventListener(ev, stop));
  // click 只服务键盘可达性（Enter/Space，detail===0）；指针点击已在 pointerdown 发过。
  // 用 detail 而不是时间戳判重：慢按（按住 200ms 再松）会越过任何时间窗，双重发送。
  btn.addEventListener("click", (e) => {
    if (e.detail !== 0) return;
    buzz();
    kmSend(code);
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

/* 音量精确设置（滑条 + 预设档）。与音量 OSD 的分工：OSD 只管按键的本地反馈，
   这条链路负责「一步调到第 N 格」。滑条 input 只画本地、change（松手）才发命令 ——
   拖动过程每帧一次 adb 会把输入锁堵死；静音仍走 sendKey(164)，因为静音位只在
   dumpsys audio 里读得到，不值得为它单开一条命令。 */
{
  const slider = document.querySelector("#volSlider"), mute = document.querySelector("#volMuteBtn");
  if (slider) {
    slider.addEventListener("input", () => {
      VolSet.dragging = true;
      VolSet.level = Number(slider.value);
      volSetPaint();               // 只画本地：回读有 adb 延迟，会把滑条弹回旧格数
    });
    slider.addEventListener("change", () => {
      VolSet.dragging = false;
      volSetCommit(Number(slider.value));
    });
    slider.addEventListener("pointerup", () => { VolSet.dragging = false; });
  }
  if (mute) {
    mute.addEventListener("click", () => {
      if (!status.connected) { toast("先连上电视"); return; }
      VolSet.muted = !VolSet.muted;
      volSetPaint();
      sendKey(164);
      setTimeout(volSetSync, 400);   // 静音位只在 dumpsys 里读得到，回读一次校准
    });
  }
}

/* Apple TV 控件 */
$("#scanBtn")?.addEventListener("click", atvScan);
$("#pairFinishBtn")?.addEventListener("click", atvPairFinish);
$("#pairCancelBtn")?.addEventListener("click", atvPairCancel);
$("#pinInput")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") atvPairFinish();
  if (e.key === "Escape") atvPairCancel();
});
$("#loadAppsBtn")?.addEventListener("click", loadAtvApps);

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
        const cached = window.__atvLastStatus;
        if (cached && (cached.appletv.devices || []).length) {
          box.dataset.scanned = "1";
          renderAtvKnown(cached);
        }
        api("/api/status").then((s) => {
          if ((s.appletv.devices || []).length) {
            box.dataset.scanned = "1";
            renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })), s);
          } else if (box.dataset.scanned === "1") {
            renderAtvKnown(s);
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

/* Android 应用预设 + 最近使用的应用（localStorage，最多 5 条，打开过的置顶）。
   日常开关电视其实就固定那几个 App，置顶省得在一排预设里找。 */
const RECENT_KEY = "atv.recentApps";
const RECENT_MAX = 5;
const recentApps = () => {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; }
};
function recordRecentApp(name, pkg) {
  const left = recentApps().filter((a) => a.pkg !== pkg);
  left.unshift({ name, pkg });
  localStorage.setItem(RECENT_KEY, JSON.stringify(left.slice(0, RECENT_MAX)));
  renderRecentApps();
}
function renderRecentApps() {
  const wrap = $("#recentApps");
  wrap.textContent = "";
  const list = recentApps();
  $("#recentLabel").classList.toggle("hidden", !list.length);
  wrap.classList.toggle("hidden", !list.length);
  list.forEach((a) => {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = "🕘 " + a.name;
    b.onclick = () => launchApp(a.name, a.pkg);
    wrap.appendChild(b);
  });
}
async function launchApp(name, pkg) {
  recordRecentApp(name, pkg);   // 先记录后发送：用户意图已发生，电视那头失败也不该丢
  try { await api("/api/cmd", { type: "app", pkg }); log(`→ 启动 ${name}`); }
  catch (e) { log("⚠ " + e.message); toast(e.message); }
}
/* ---- 收藏夹 DOM 胶水：规则在上面 favorites 纯函数段，这里只管 localStorage / 渲染 / 星标 ---- */
const FAV_KEY = "atv.favApps";      // 只活在浏览器里：state.json 会被打进 bundle.tgz，可重放内容不进敏感文件
const favLoad = () => {
  try { return favNorm(JSON.parse(localStorage.getItem(FAV_KEY)) || []); }
  catch (e) { return []; }
};
const favSave = (list) => {
  try { localStorage.setItem(FAV_KEY, JSON.stringify(favNorm(list))); }
  catch (e) { /* 隐私模式写不进 localStorage：这轮会话照常能用，重启不记得 */ }
  favRender();
};
function favTogglePin(name, pkg) {
  const next = favToggle(favLoad(), pkg, name);
  if (next === null) { toast("收藏夹最多 " + FAV_MAX + " 个，先取消一个再固定"); return false; }
  favSave(next);
  const on = favIsPinned(next, pkg);
  toast(on ? "★ 已收藏 " + name : "已取消收藏 " + name);
  return on;
}

/* ---- 收藏夹调序的 DOM 胶水：移动规则在 favorder 段 ----
   走 undoable 链路（与删除宏 / 清短语同一套）：移错了能一步退回，
   而不是逼用户凭记忆把顺序按回去。 */
function favOrderBtn(a, dir) {
  const b = document.createElement("button");
  b.className = "btn tiny favmove";
  b.type = "button";
  b.dataset.dir = dir;
  b.dataset.pkg = a.pkg;
  b.textContent = dir === "up" ? "↑" : "↓";
  b.title = dir === "up" ? "上移一位" : "下移一位";
  b.setAttribute("aria-label", b.title);
  b.onclick = () => {
    const pins = favLoad();
    const from = favIndexOf(pins, a.pkg);
    if (from < 0 || !favCanMove(pins, from, dir)) return;
    const next = favMove(pins, from, from + (dir === "up" ? -1 : 1));
    const prev = pins.slice();
    undoable((dir === "up" ? "上移" : "下移") + "「" + a.name + "」", () => {
      favSave(next);
      favRender();
    }, () => {
      favSave(prev);
      favRender();
    });
  };
  return b;
}
function favRender() {
  const box = $("#favList");
  const pins = favLoad();
  const list = favResolve(pins, APPS, recentApps());
  box.textContent = "";
  $("#favEmpty").classList.toggle("hidden", !!list.length);
  $("#favCount").textContent = list.length ? String(list.length) : "—";
  list.forEach((a, idx) => {
    const row = document.createElement("div");
    row.className = "atvrow favrow";
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = "★ " + a.name;
    b.onclick = () => launchApp(a.name, a.pkg);
    const x = document.createElement("button");
    x.className = "btn tiny";
    x.type = "button";
    x.title = "取消收藏";
    x.textContent = "✕";
    x.onclick = () => favTogglePin(a.name, a.pkg);
    const up = favOrderBtn(a, "up");
    const dn = favOrderBtn(a, "down");
    row.append(b, up, dn, x);
    // 首行不能上移、末行不能下移：置灰但不消失，位置跳动比置灰更难用
    up.disabled = !favCanMove(list, idx, "up");
    dn.disabled = !favCanMove(list, idx, "down");
    box.appendChild(row);
  });
  // 预设区 / Apple TV 列表里的星标都是收藏夹的视图：一个数据源，这里统一刷新
  $$(".favstar").forEach((s) => {
    const on = favIsPinned(pins, s.dataset.pkg);
    s.textContent = on ? "★" : "☆";
    s.setAttribute("aria-pressed", on ? "true" : "false");
  });
}
function favStarBtn(name, pkg) {
  const s = document.createElement("button");
  s.className = "btn tiny favstar";
  s.type = "button";
  s.title = "收藏 / 取消收藏";
  s.dataset.pkg = pkg;
  s.setAttribute("aria-pressed", "false");
  s.textContent = "☆";
  s.onclick = () => favTogglePin(name, pkg);
  return s;
}
/* Android 预设一行一枚：名字按钮（点击即启动）+ 星标（固定到收藏夹）。
   横排芯片留给「最近使用」——那是流水，收藏是钉子，形态要一眼分出来（样式见 .rows）。 */
function renderAppPresets() {
  const box = $("#apps");
  box.textContent = "";
  box.classList.add("rows");
  APPS.forEach((a) => {
    const row = document.createElement("div");
    row.className = "atvrow favrow pinrow";
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = a.name;
    b.onclick = () => launchApp(a.name, a.pkg);
    row.append(b, favStarBtn(a.name, a.pkg));
    box.appendChild(row);
  });
}
renderAppPresets();
renderRecentApps();
favRender();
$("#pkgBtn").addEventListener("click", async () => {
  const pkg = $("#pkgInput").value.trim();
  if (!pkg) return;
  launchApp(pkg, pkg);
});
$("#pkgInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#pkgBtn").click();
});

/* ===== panemotion:begin ===== */
/* 窗格转场与触控目标：学 Material 3 motion（emphasized 类短进场 200-300ms、缓出为主）
   与 WCAG 2.2 目标尺寸（>=24x24 CSS px，本项目按 36px 从严，拇指点击）。
   paneIdFor：data-tab 到面板 id 的纯映射；paneAnimMs：系统减少动效偏好下取 0。
   CSS 侧 --pane-dur / --touch-min 消费同一组数值，tests/test_panemotion.py 正则
   比对两处防漂移；DOM 胶水（matchMedia / style / classList）在下方 tabs 绑定处。 */
const PANE_ANIM_MS = 240;     // = CSS --pane-dur
const TOUCH_MIN_PX = 36;      // = CSS --touch-min（.btn.tiny min-height / .btn.tiny.gear min-width）
function paneIdFor(name) { return "pane-" + String(name == null ? "" : name); }
function paneAnimMs(reduced) { return reduced ? 0 : PANE_ANIM_MS; }

/* ===== panemotion:end ===== */
/* ===== collapse:begin ===== */
/* 卡片折叠：学 WAI-ARIA Disclosure 模式与 Radix Collapsible / Accordion 的「状态即属性」
   方法论——视觉与可访问性只认 data-state 这一个属性，CSS 不读 JS 变量、JS 不直接改样式；
   aria-expanded 跟属性同步翻，折叠后 CSS 用 visibility 把它从 Tab 顺序里摘掉。
   折叠态存浏览器 localStorage（键 atv.collapsed.v1）：state.json 会被打进 bundle.tgz
   分发给局域网任何设备，可编辑偏好一律不放它。
   纯函数段：禁 DOM / localStorage / fetch / innerHTML / $( （tests/collapse_harness.js
   直接抽这段执行），DOM 胶水在下方。localStorage 是不可信输入：JSON 坏掉、类型不对、
   混进不存在的 id 都要能兜住，否则一条坏数据能让整页卡片集体消失。 */
const COLLAPSE_MS = 180;               // = CSS --collapse-dur
const COLLAPSE_KEY = "atv.collapsed.v1";
const COLLAPSE_OPEN = "open", COLLAPSE_CLOSED = "closed";
function collapseAnimMs(reduced) { return reduced ? 0 : COLLAPSE_MS; }
function collapseStateOf(current, forced) {
  if (forced === COLLAPSE_OPEN || forced === COLLAPSE_CLOSED) return forced;
  return current === COLLAPSE_CLOSED ? COLLAPSE_OPEN : COLLAPSE_CLOSED;
}
function parseCollapsed(raw, known) {
  const allow = new Set(known || []);
  let list = null;
  if (typeof raw === "string" && raw) { try { list = JSON.parse(raw); } catch (e) { return []; } }
  if (!Array.isArray(list)) return [];
  const out = new Set();
  for (const it of list) if (typeof it === "string" && allow.has(it)) out.add(it);
  return Array.from(out).sort();
}
function serializeCollapsed(ids) {
  const out = new Set();
  for (const it of (ids || [])) if (typeof it === "string") out.add(it);
  return JSON.stringify(Array.from(out).sort());
}
function collapseLabel(state) { return state === COLLAPSE_CLOSED ? "展开" : "折叠"; }

/* ===== collapse:end ===== */

/* ===== undo:begin ===== */
/* 撤销（学 Gmail「已删除·撤销」/ Material Snackbar action / Radix Toast action）纯规则段。
   破坏性操作（清短语 / 清宏 / 恢复默认按键 / 删单个宏）先执行、再给 6s 反悔窗口；
   恢复语义 = 回到 doFn 执行前那一刻的快照，快照由调用方闭包持有，栈本身不落盘——
   可编辑偏好不进 state.json，撤销机会同理不该跨重启存活。 */
const UNDO_MS = 6000;          // 反悔窗口；通知停留时长与它取 max，按钮永远先于窗口消失
const UNDO_STACK_MAX = 4;      // 同屏最多 4 条可撤销，防连点把内存堆满

// 只留「没过期」的条目。at 缺失 / 非数字一律算已过期：没有时间戳就无从判断新旧。
// now 由调用方注入（Date.now()），本段不自己读时钟，才能整段搬进 node 跑用例。
function undoLive(entries, now) {
  const arr = Array.isArray(entries) ? entries : [];
  const t = typeof now === "number" ? now : 0;
  return arr.filter((e) => !!e && typeof e.at === "number" && e.at >= 0 && e.at + UNDO_MS > t);
}

// 新条目排最前，顺手 prune 过期、去掉同 id 重复、截到上限。返回新数组（旧引用不变）。
function undoPush(entries, entry, now) {
  const kept = undoLive(entries, now).filter((e) => e.id !== entry.id);
  const out = [entry].concat(kept);
  return out.length > UNDO_STACK_MAX ? out.slice(0, UNDO_STACK_MAX) : out;
}

// 命中且未过期 → { entry, rest }；rest 已摘掉该条，同一次撤销不会被点两次。
// 未命中（id 不认识 / 已过期）→ null，调用方据此提示「反悔时间已过」。
function undoTake(entries, id, now) {
  const live = undoLive(entries, now);
  const i = live.findIndex((e) => e.id === id);
  if (i < 0) return null;
  return { entry: live[i], rest: live.slice(0, i).concat(live.slice(i + 1)) };
}
/* ===== undo:end ===== */
 
/* ===== gamepad:begin ===== */
/* 手柄当 D-pad（第三十五轮）规则段：把 Gamepad API 的两根模拟轴翻成 Android TV 的离散
   D-pad 按键。浏览器 Gamepad.axes 给的是 [-1, 1]，而三个参照项目全都在 ±32767 的原始轴上
   干活，所以第一件事是换基：默认死区 0.15 ≈ xorg 手柄驱动的 5000/32768 = 0.1526
   （qjoypad 默认更小，3000/32767 = 0.0916）。每条规则都能 grep 到出处，
   见 docs/opensource-references.md 第 10 节：
     · 死区与重标度 (|v|-dz)/(1-dz)：xf86-input-joystick src/jstk_axis.c:83 与 :411-416
     · 死区判据是「小于才算死区」，等于阈值算已经顶出：src/backend_joystick.c:172、
       qjoypad src/axis.cpp:281（abs(value) < dZone 才在死区内）
     · 只在方向真的变了时才发键，其余直接 return 不重发：qjoypad src/axis.cpp:205-233
     · 轴跳变不经过死区时，先抬旧键再按新键：xorg src/jstk_axis.c:494-505
       （原注释就叫 PWM Axis %d jumped over. Forcing keys_low up.，qjoypad 在这里会卡住不松键）
     · 占空比两相 50ms 下限与 600ms 截止：xorg src/jstk_axis.c:518-551
   纯函数：时钟与 DOM 一律不碰，可整段搬进 node 跑用例。
   段末另有一帧 -> 该发哪些键的五个函数（gpNormPad / gpBtnDir / gpDirKey /
   gpFrame / gpNextFireMs），口径抄 xboxdrv 0.8.8，出处见同一份台账第 11 节。 */
const GP_DEADZONE_DEFAULT = 0.15;
const GP_DEADZONE_MIN = 0.02, GP_DEADZONE_MAX = 0.5;
const GP_REPEAT_MIN_MS = 60;    /* 自己定：再快就是连发，列表会飞过去 */
const GP_REPEAT_MAX_MS = 600;   /* = GP_PWM_HOLD_MS：驱动里一相超过 600ms 就判不用重复 */
const GP_REPEAT_FIRST_MS = 300; /* 首按与首次重复之间的延时，自己的取值 */
const GP_ARBIT_MARGIN = 1.25;   /* 换轴迟滞：新轴残差要超过当前轴这么多倍才抢得到 */
const GP_PWM_MIN_PHASE_MS = 50; /* jstk_axis.c:527-531 —— 较小的那一相永远缩放到 50ms */
const GP_PWM_HOLD_MS = 600;     /* jstk_axis.c:537/:547 —— 超过它就当一直按着/松着 */

function gpNum(v) {
  const n = (typeof v === 'number') ? v : 0;
  return isFinite(n) ? n : 0;
}
/* 死区只收调皮皮和 NaN，不收 0：关成 0 会让摇杆的静止噪声一直触发方向键。 */
function gpClampDeadzone(dz) {
  const n = gpNum(dz);
  if (!(n > 0)) return GP_DEADZONE_DEFAULT;
  return Math.min(GP_DEADZONE_MAX, Math.max(GP_DEADZONE_MIN, Math.round(n * 100) / 100));
}
/* 死区内归零；死区外按 (|v|-dz)/(1-dz) 重标度回 0..1。少了重标度这一步，死区一放大，
   半推和满推就分不开了。 */
function gpResidual(v, dz) {
  const a = Math.abs(gpNum(v));
  const d = gpClampDeadzone(dz);
  if (a < d) return 0;
  return Math.min(1, (a - d) / Math.max(1e-6, 1 - d));
}
/* 这根轴现在算哪个方向：死区内 0，死区外取符号。阈值上算顶出——驱动判死区用小于号。 */
function gpDirOf(v, dz) {
  const x = gpNum(v), d = gpClampDeadzone(dz);
  if (x >= d) return 1;
  if (x <= -d) return -1;
  return 0;
}
/* 单步：给 DOM 胶水用的最小接口，changed 为真才值得发一次键。 */
function gpStep(v, dz, held) {
  const cur = gpDirOf(v, dz);
  const prev = (held === 1 || held === -1) ? held : 0;
  return { dir: cur, prev: prev, changed: cur !== prev };
}
/* 一串采样翻成按键事件：方向没变就一行都不出。
   qjoypad 的 jsevent 留了个坑——轴从 +0.5 直接跳到 -0.5（不经过死区）时正向键会一直
   按着不松；这里照 xorg 驱动 jstk_axis.c:494-505 的口径先抬旧的再按新的。 */
function gpAxisEvents(samples, dz) {
  const out = [];
  const d = gpClampDeadzone(dz);
  const seq = Array.isArray(samples) ? samples : [];
  let held = 0;
  for (let i = 0; i < seq.length; i++) {
    const cur = gpDirOf(seq[i], d);
    if (cur === held) continue;
    if (held !== 0) out.push({ i: i, dir: held, press: false });
    if (cur !== 0) out.push({ i: i, dir: cur, press: true });
    held = cur;
  }
  return out;
}
/* 两根轴抢一个 D-pad：Android 的 D-pad 没有斜向，同一时刻只能有一个方向。
   抢法带迟滞——已在按的轴，对手残差要高出 GP_ARBIT_MARGIN 倍才抢得走，否则推到头
   再往回带那一下会在两个方向之间抖。当前轴回中了就主动让位，不要求余量，否则键卡住。 */
function gpArbitrate(x, y, dz, held) {
  const d = gpClampDeadzone(dz);
  const rx = gpResidual(x, d), ry = gpResidual(y, d);
  const cur = (held === 'x' || held === 'y') ? held : null;
  const pick = function (axis) {
    const v = (axis === 'x') ? gpNum(x) : gpNum(y);
    return { axis: axis, dir: gpDirOf(v, d) };
  };
  if (cur === null) {
    if (rx <= 0 && ry <= 0) return { axis: null, dir: 0 };
    return pick((rx >= ry) ? 'x' : 'y');
  }
  const rr = (cur === 'x') ? rx : ry;
  const or = (cur === 'x') ? ry : rx;
  if (rr <= 0) {
    if (or <= 0) return { axis: null, dir: 0 };
    return pick((cur === 'x') ? 'y' : 'x');
  }
  if (or > rr * GP_ARBIT_MARGIN) return pick((cur === 'x') ? 'y' : 'x');
  return pick(cur);
}
/* 占空比周期，逐字照 xorg 驱动 src/jstk_axis.c:518-551：残差即通电占比，两相各 +0.01
   防除零，较小那相缩放到 50ms，超过 600ms 就当一直按着 / 一直松着。
   注意这是给「指针移动」设计的速度律，敷到离散 D-pad 上呈 U 形（中间最快、两头最慢），
   所以产品侧另有一份单调的 gpRepeatMs；这里留着，是为了让那 50ms 与 600ms 有据可查。 */
function gpPwmCycle(v, dz) {
  const u = gpResidual(v, dz);
  const on = u + 0.01, off = (1 - u) + 0.01;
  const scale = GP_PWM_MIN_PHASE_MS / Math.min(on, off);
  const onMs = on * scale, offMs = off * scale;
  let hold = null;
  if (offMs > GP_PWM_HOLD_MS) hold = 'up';
  else if (onMs > GP_PWM_HOLD_MS) hold = 'down';
  return { onMs: onMs, offMs: offMs, hold: hold };
}
/* 产品侧的重复间隔：残差越大按得越密（轻推慢步、重推快滚）。上界取驱动的 600ms 截止，
   下界 60ms 是自己的取值——驱动那边是全 tilt 交给 X 自动重复，我们没有这层。 */
function gpRepeatMs(v, dz) {
  const u = gpResidual(v, dz);
  if (u <= 0) return null;
  const t = Math.min(1, u);
  return Math.round(GP_REPEAT_MAX_MS - (GP_REPEAT_MAX_MS - GP_REPEAT_MIN_MS) * t);
}
/* 一帧 -> 该发哪些键。轮询到的快照原样传进来，时钟与 DOM 仍然一概不碰。
   三条口径抄 xboxdrv 0.8.8（Ubuntu pool 源码包，GPL-3.0+），行号见台账第 11 节：
     · 一份输入只留一个出口：uinput_options.cpp:189-198 的 dpad_as_button() 把 DPAD
       四向 bind 成按键的同时，把同两根轴 bind 成 AxisEvent::invalid()。同一个偏转
       从轴和按键两条路各出一份，用户看到的就是双倍速度。这里的两个输入源（物理十字键
       与左摇杆）抢同一个 D-pad，同样只许一个赢。
     · 斜按裁掉一根轴：modifier/four_way_restrictor_modifier.cpp:44-59，abs 大的那根
       留下，相等时清零 X——平手判 Y 胜，且完全没有迟滞。模拟轴那条路的换轴迟滞
       另见 gpArbitrate（有意分歧，写在第 11 节里，不是漏抄）。
     · 连发先发一拍再按速率重发：buttonfilter/autofire_button_filter.cpp:82-97，
       默认 rate=50 / delay=0（from_string 的初值，:27-28）；
       delay 用尽之后是 m_counter > m_delay 才置位 m_autofire，再每 m_rate 一拍。
       另有一条克制：controller_slot_config.cpp:207-212 只有 autofire_map 非空才挂
       这条滤波链——默认什么都不挂。本项目默认就开连发（列表里移动光标离不开它），
       所以死区内必须一个键都不发，否则摇杆静止噪声会变成自动翻页。 */
const GP_DIR_KEYS = { up: 19, down: 20, left: 21, right: 22 };  /* Android keyevent */
/* 归一：标准映射 0 = A、1 = B、12..15 = 十字键上下左右。缺轴 / 缺键 / NaN 一律兜底成
   0 与 false——部分手柄在浏览器把手柄交出来之前那几帧，前两根轴就是 NaN，
   直接喂给 gpResidual 会把死区判成「已经顶出」，方向键自己飞起来。 */
function gpNormPad(pad) {
  const src = pad || {};
  const ax = Array.isArray(src.axes) ? src.axes : [];
  const bt = Array.isArray(src.buttons) ? src.buttons : [];
  const b = function (i) { return !!(bt[i] && bt[i].pressed); };
  return {
    x: gpNum(ax[0]), y: gpNum(ax[1]),
    up: b(12), down: b(13), left: b(14), right: b(15),
    a: b(0), back: b(1)
  };
}
/* 十字键 -> 单个方向。只要按着任一竖直方向就判竖直胜：这正是 four_way_restrictor 的
   else 分支（相等时清零 X）搬到布尔输入上的样子，斜按不会同时出两个方向。 */
function gpBtnDir(up, down, left, right) {
  const u = !!up, d = !!down, l = !!left, r = !!right;
  if (!u && !d && !l && !r) return null;
  if (u || d) return u ? "up" : "down";
  return l ? "left" : "right";
}
/* 方向 -> 键码的唯一一张对照表，别处不要再写 19 / 20 / 21 / 22。 */
function gpDirKey(dir) {
  const k = GP_DIR_KEYS[dir];
  return (typeof k === "number") ? k : 0;
}
/* 物理十字键按着时摇杆整帧让位（单一出口）；否则走带迟滞的 gpArbitrate。
   val 是胜出那一轴的原始值，连发速率要用它算推得多重。 */
function gpFrame(snap, dz, held) {
  const s = snap || {};
  const btn = gpBtnDir(s.up, s.down, s.left, s.right);
  if (btn !== null) return { dir: btn, axis: null, val: 0 };
  const a = gpArbitrate(s.x, s.y, dz, held);
  if (a.axis === null || a.dir === 0) return { dir: null, axis: null, val: 0 };
  const vert = (a.axis === "y");
  return {
    dir: vert ? (a.dir > 0 ? "down" : "up") : (a.dir > 0 ? "right" : "left"),
    axis: a.axis,
    val: vert ? gpNum(s.y) : gpNum(s.x)
  };
}
/* 连发节拍：返回「距下一拍还有多少 ms」。首发那一拍在按下时就发出、不计入延时——
   xboxdrv 的 filter(true) 首拍直接返回 true；delay 用尽才置位 m_autofire，再每 rate
   一拍。本项目把 delay 换成 GP_REPEAT_FIRST_MS、rate 换成 gpRepeatMs（残差越大越密）。
   xboxdrv 判 delay 用严格大于，差不到一个轮询周期，不值得为它歪一份实现。 */
function gpNextFireMs(elapsed, rate) {
  let r = gpNum(rate);
  if (!(r > 0)) r = GP_REPEAT_MIN_MS;
  const e = Math.max(0, gpNum(elapsed));
  if (e < GP_REPEAT_FIRST_MS) return GP_REPEAT_FIRST_MS - e;
  return r;
}
/* ===== gamepad:end ===== */
/* ---- 手柄 DOM 胶水（第三十六轮接线）：规则在上面纯函数段，这里只管轮询 / 发键 / 持久化 ---- */
const GP_KEY = "atv.gamepad";        /* 开关，默认关——不开就不占 80ms 轮询 */
const GP_DZ_KEY = "atv.gamepadDz";   /* 摇杆死区，可编辑内容只进 localStorage */
const GP_TICK_MS = 80;               /* 轮询周期：8s 的状态轮询太慢，连发会一顿一顿 */
/* 标准映射：0 = A（确定）、1 = B（返回）、12..15 = 十字键上下左右 */
const gp = { id: "", dir: null, axis: null, rate: 0, since: 0, timer: 0, face: { a: false, back: false } };
let gpOn = false, gpDz = GP_DEADZONE_DEFAULT;
try {
  gpOn = localStorage.getItem(GP_KEY) === "1";
  const dz = JSON.parse(localStorage.getItem(GP_DZ_KEY));
  if (dz !== null && dz !== undefined) gpDz = gpClampDeadzone(dz);
} catch (e) { /* 存储被禁（隐私模式 / 无痕）：用默认值，功能照常 */ }
function gpDzSave() { try { localStorage.setItem(GP_DZ_KEY, String(gpDz)); } catch (e) {} }
function gpShortId(id) { const s = String(id || ""); return s.length > 30 ? s.slice(0, 30) + "…" : s; }
/* 状态一律 textContent：手柄 id 是设备自述，和局域网广播一样不可信 */
function gpSetState(kind, id) {
  const el = $("#gpState");
  if (!el) return;
  let t = "已关闭";
  if (kind === "on") t = "已连接：" + gpShortId(id);
  else if (kind === "wait") t = "未检测到手柄";
  else if (kind === "lost") t = "手柄已断开";
  el.textContent = t;
  el.className = "gpstate" + (kind === "on" ? " on" : "");
}
function gpEnableApply() {
  const b = $("#gpEnableBtn");
  if (b) b.setAttribute("aria-checked", gpOn ? "true" : "false");
}
function gpDzApply() {
  const el = $("#gpDz");
  if (el) el.value = String(gpDz);
  const v = $("#gpDzVal");
  if (v) v.textContent = (Math.round(gpDz * 100) / 100).toFixed(2);
}
function gpStop() { if (gp.timer) { clearTimeout(gp.timer); gp.timer = 0; } }
function gpFire(code) { buzz(8); sendKey(code); }
function gpArm(ms) { gpStop(); gp.timer = setTimeout(gpRepeat, ms); }
function gpRepeat() {
  gp.timer = 0;
  if (!gpOn || !gp.dir) return;
  gpFire(gpDirKey(gp.dir));
  /* 注意 sendKey 自带 90ms 同键节流，所以 GP_REPEAT_MIN_MS = 60 那一档实际发成 ~90ms：
     下限是规则段的口径，真正的速率地板在共用的发键函数里，这里不另起一套。 */
  gpArm(gpNextFireMs(performance.now() - gp.since, gp.rate));
}
function gpPress(f) {
  gpStop();   /* 松开即清零：xboxdrv 的 filter(false) 把 m_counter 与 m_autofire 一起归零 */
  gp.dir = f.dir;
  gp.axis = f.axis;
  if (!f.dir) return;   /* 回中：keyevent 是一次性的，没有 keyup 可发，停发就是松键 */
  gp.rate = gpRepeatMs(f.val, gpDz) || GP_REPEAT_MIN_MS;
  gp.since = performance.now();
  gpFire(gpDirKey(f.dir));
  gpArm(gpNextFireMs(0, gp.rate));
}
function gpFindPad() {
  if (!navigator.getGamepads) return null;
  const list = navigator.getGamepads();
  for (let i = 0; i < list.length; i++) {
    const p = list[i];
    if (p && p.connected && p.axes && p.axes.length >= 2) return p;
  }
  return null;
}
function gpTick() {
  if (!gpOn || !pageVisible) return;
  const pad = gpFindPad();
  if (!pad) {
    /* 拔了手柄：先把连发掐掉。方向键是点发，没有 keyup 会卡住，但连发定时器会。 */
    if (gp.id || gp.dir) { gpStop(); gp.dir = null; gp.axis = null; gp.id = ""; }
    gpSetState("wait");
    return;
  }
  if (gp.id !== pad.id) { gpStop(); gp.dir = null; gp.axis = null; gp.id = pad.id; }
  gpSetState("on", pad.id);
  const snap = gpNormPad(pad);
  /* 功能键边沿：A = 确定、B = 返回。点一下发一下，不连发——连发只属于方向键。 */
  const face = { a: snap.a, back: snap.back };
  if (face.a && !gp.face.a) gpFire(23);
  if (face.back && !gp.face.back) gpFire(4);
  gp.face = face;
  const f = gpFrame(snap, gpDz, gp.axis);
  if (f.dir !== gp.dir) gpPress(f);
}
function gpBind() {
  const en = $("#gpEnableBtn");
  if (en) en.addEventListener("click", () => {
    gpOn = !gpOn;
    try { localStorage.setItem(GP_KEY, gpOn ? "1" : "0"); } catch (e) {}
    gpEnableApply();
    if (gpOn) {
      gpSetState("wait");
      log("→ 手柄遥控已开：先在手柄上按任意键，浏览器这才把手柄交出来");
    } else {
      gpStop(); gp.dir = null; gp.axis = null; gp.id = "";
      gp.face = { a: false, back: false };
      gpSetState("off");
    }
  });
  const dz = $("#gpDz");
  if (dz) dz.addEventListener("input", () => { gpDz = gpClampDeadzone(Number(dz.value)); gpDzApply(); gpDzSave(); });
  const r = $("#gpDzReset");
  if (r) r.addEventListener("click", () => {
    gpDz = GP_DEADZONE_DEFAULT; gpDzApply(); gpDzSave();
    toast("摇杆死区已重置为 0.15");
  });
  /* 插拔即时反映，不等下一拍轮询 */
  window.addEventListener("gamepadconnected", (e) => { if (gpOn) gpSetState("on", e && e.gamepad && e.gamepad.id); });
  window.addEventListener("gamepaddisconnected", () => {
    if (!gpOn) return;
    gpStop(); gp.dir = null; gp.axis = null; gp.id = "";
    gp.face = { a: false, back: false };
    gpSetState("lost");
  });
}
gpEnableApply();
gpDzApply();
gpSetState(gpOn ? "wait" : "off");
gpBind();

/* ===== empty:begin ===== */
/* 空状态（第二十八轮）规则段：学 Shopify Polaris Empty State / Material empty state
   的契约——列表空着不是无话可说，而该给「一句解释 + 一个下一步动作」。
   判空只看一份 Android 连接面板视角的快照，文案按 kind 查表；全部纯函数、
   时钟与 DOM 一律不碰，可整段搬进 node 跑用例。 */
const EMPTY_NONE = null;               // 有东西可看：不渲染空状态
/* kind：intro = 从没连过（首次引导）；offline = 连过但现在不在线；noadb = 本机没 adb */
function emptyKind(snap) {
  const recent = Array.isArray(snap.recent) ? snap.recent.length : 0;
  const online = Array.isArray(snap.devices) ? snap.devices.length : 0;
  if (snap.current || online) return EMPTY_NONE;    // 正连着 / 有在线设备：chips 不空
  if (!snap.adb_found) return "noadb";              // 没 adb 说什么都白费，先讲这个
  return recent ? "offline" : "intro";
}
/* 文案查表：offline 要把「几台」说进去——空状态的可信度全靠具体数字。
   cta / cta2 是按钮文案，act / act2 是动作键（glue 负责映射到真行为），
   act 为 null 表示这个 kind 没有可执行的下一步（noadb 只能去装 adb）。 */
function emptyCopy(kind, recent) {
  const n = Array.isArray(recent) ? recent.length : 0;
  if (kind === "intro") return {
    title: "还没有连接过电视",
    desc: "输入电视 IP 点「连接」，或点「扫描」找同一网段开着网络调试的设备。"
      + "第一次用，需要先在电视「设置 → 开发者选项」里打开网络调试。",
    cta: "🔍 扫描局域网", act: "scan",
    cta2: "手输 IP", act2: "focus",
  };
  if (kind === "offline") return {
    title: n + " 台设备当前离线",
    desc: "连过的设备现在都不在：电视可能睡了。开机后点「重连」，或再扫描一次局域网。",
    cta: "重连最近一台", act: "reconnect",
    cta2: "🔍 扫描", act2: "scan",
  };
  if (kind === "noadb") return {
    title: "本机没有找到 adb",
    desc: "Android TV 控制走 adb。装好 platform-tools 并加进 PATH 后重启本应用即可。",
    cta: null, act: null, cta2: null, act2: null,
  };
  return null;
}
/* 节流签名：输入没变就别重建（8s 轮询会反复进这里，重建会打断 hover / 焦点） */
function emptySig(kind, n) { return kind + "|" + n; }
/* ===== empty:end ===== */

/* ===== sheet:begin ===== */
/* 底部快捷菜单（第二十九轮）规则段：学 Material 3 Bottom Sheet / iOS Context Menu /
   Home Assistant more-info 的行为契约——「属于这个对象的所有动作，收进一个单指可达的
   弹层」。手机没有右键：最近连接的「右键移除」在触屏上根本不可达，在线设备 chip 也只剩
   「点一下切换」。菜单长什么样子由纯函数按设备状态推算，时钟与 DOM 一律不碰，
   可整段搬进 node 跑用例。 */
const SHEET_NONE = null;   // 设备快照解析失败：不开菜单
/* 标题旁那行小字。current 压过 online——「已连接」比「在线」信息量大。 */
function sheetStateText(dev) {
  if (!dev) return "";
  if (dev.current) return "已连接 · 当前设备";
  if (dev.online) return "在线";
  return dev.kind === "appletv" ? "已配对 · 未连接" : "离线";
}
/* Android 最近连接 chip 的快照。online 要按 IP 匹配：adb 的 serial 带端口（host:5555），
   而 ARP 表与最近列表只认 IP，先 wolIpOf 归一再比。wakes 是 WOL 发现结果（[{ip, mac}]），
   只有「当前离线且查得到 MAC」才给唤醒——在线设备没有唤醒的必要，正连着的更不行。 */
function sheetDevOfRecent(s, ip, wakes) {
  const st = s || {};
  const target = String(ip === null || ip === undefined ? "" : ip);
  if (!target) return SHEET_NONE;
  const online = (Array.isArray(st.devices) ? st.devices : [])
    .some((d) => !!d && wolIpOf(d.serial) === target);
  const current = st.cur_type === "android" && st.current === target;
  const wake = (Array.isArray(wakes) ? wakes : [])
    .some((w) => !!w && w.ip === target && !!w.mac);
  return { target, kind: "android", label: target, online, current,
           removable: true, canWake: !online && !current && wake };
}
/* Android 在线设备 chip（s.devices 里当前设备之外的那些）：只有「切换」和「复制」。
   不给「移除」：它不在用户的最近连接历史里，forget 对它没有意义。 */
function sheetDevOfDevice(s, dev) {
  const st = s || {};
  if (!dev || !dev.serial) return SHEET_NONE;
  const target = String(dev.serial);
  const current = st.cur_type === "android" && st.current === target;
  return { target, kind: "android", label: target, online: true, current,
           removable: false, canWake: false };
}
/* Apple TV 设备行：stored 的给「取消配对」；Apple TV 不走 adb/ARP，永远没有 WOL。 */
function sheetDevOfAtv(s, dev) {
  const st = s || {};
  if (!dev || !dev.id) return SHEET_NONE;
  const target = String(dev.id);
  const current = !!(st.appletv && st.appletv.connected && st.current === target);
  return { target, kind: "appletv", label: String(dev.name || "Apple TV"),
           online: current, current, removable: !!dev.stored, canWake: false };
}
/* 动作表。三条硬规则（harness 有对应用例）：
   1) 「复制」永远在——菜单至少要有一个无害动作，长按一下不至于白按；
   2) 破坏性动作（移除 / 取消配对）永远排最后且 danger=true，胶水只照表执行；
   3) 唤醒只出现在「当前离线且查得到 MAC」，重连/切换不打扰正连着的设备。 */
function sheetItems(dev) {
  if (!dev) return [];
  const out = [];
  if (dev.kind === "appletv") {
    out.push(dev.current
      ? { id: "disconnect", label: "⏏ 断开连接", danger: false }
      : { id: "connect", label: "🔌 连接", danger: false });
    out.push({ id: "copy", label: "📋 复制设备 ID", danger: false });
    if (dev.removable) out.push({ id: "forget", label: "🗑 取消配对", danger: true });
    return out;
  }
  if (dev.current) out.push({ id: "disconnect", label: "⏏ 断开连接", danger: false });
  else if (dev.online) out.push({ id: "switch", label: "🔀 切换到此设备", danger: false });
  else {
    out.push({ id: "connect", label: "🔌 重新连接", danger: false });
    if (dev.canWake) out.push({ id: "wake", label: "⚡ 远程开机（WOL）", danger: false });
  }
  out.push({ id: "copy", label: "📋 复制 IP 地址", danger: false });
  if (dev.removable) out.push({ id: "forget", label: "🗑 从最近列表移除", danger: true });
  return out;
}
/* ===== sheet:end ===== */

/* ---- 折叠卡片 DOM 胶水：规则在上方 collapse 纯函数段，这里只管 localStorage / data-state / aria ----
   单一决策点：collapseApply() 只翻属性（data-state + aria-expanded + aria-label + title），
   样式全部由 CSS 按 data-state 自己算；collapseSave() 是唯一落盘处，键与格式由段里定。 */
function collapseCards() { return Array.prototype.slice.call($$("[data-collapsible]")); }
function collapseApply(el, state) {
  el.dataset.state = state;
  const btn = el.querySelector(".ctog");
  if (btn) {
    const act = collapseLabel(state);
    btn.setAttribute("aria-expanded", state === COLLAPSE_CLOSED ? "false" : "true");
    btn.setAttribute("aria-label", act + "「" + (el.dataset.collapseName || el.id) + "」");
    btn.title = act + "「" + (el.dataset.collapseName || el.id) + "」";
  }
}
function collapseSave() {
  const closed = collapseCards().filter((el) => el.dataset.state === COLLAPSE_CLOSED).map((el) => el.id);
  try { localStorage.setItem(COLLAPSE_KEY, serializeCollapsed(closed)); }
  catch (e) { /* 隐私模式写不进 localStorage：这轮会话照常能用，重启不记得 */ }
}
function collapseRestore() {
  const known = collapseCards().map((el) => el.id);
  let hit = new Set();
  try { hit = new Set(parseCollapsed(localStorage.getItem(COLLAPSE_KEY), known)); }
  catch (e) { hit = new Set(); }
  collapseCards().forEach((el) => collapseApply(el, hit.has(el.id) ? COLLAPSE_CLOSED : COLLAPSE_OPEN));
}
function collapseAll(want) {
  collapseCards().forEach((el) => collapseApply(el, want));
  collapseSave();
}
function collapseBind() {
  collapseCards().forEach((el) => {
    const btn = el.querySelector(".ctog");
    if (btn) btn.addEventListener("click", () => {
      collapseApply(el, collapseStateOf(el.dataset.state));
      collapseSave();
    });
  });
  const seg = $("#collapseSeg");
  if (seg) Array.prototype.slice.call(seg.querySelectorAll("[data-collapse-all]")).forEach((b) => {
    b.addEventListener("click", () => {
      const want = b.dataset.collapseAll === "closed" ? COLLAPSE_CLOSED : COLLAPSE_OPEN;
      collapseAll(want);
      toast(want === COLLAPSE_CLOSED ? "已折叠全部次级卡片" : "已展开全部次级卡片");
    });
  });
}
// 初值先于首帧落地，再绑事件：restore 放 Bind 之后的话，第一次点击会按 DOM 初值反转
collapseRestore();
collapseBind();

/* ---- 撤销 DOM 胶水：规则在上方 undo 纯函数段，这里只管执行 / 计时 / 回调 ---- */
let undoStack = [];
let undoSeq = 0;

// 破坏性操作唯一入口：先立刻执行 doFn，再把「怎么恢复」压栈并弹带「撤销」按钮的通知。
// 恢复语义 = 回到 doFn 执行前那一刻，所以快照必须在调 undoable 之前抓，undoFn 只负责写回。
// label 同时决定通知级别（「已…」开头算成功）并进通知历史，写用户看得懂的话。
function undoable(label, doFn, undoFn) {
  const now = Date.now();
  const entry = { id: ++undoSeq, label: String(label === undefined || label === null ? "" : label), at: now, undo: undoFn };
  doFn();
  undoStack = undoPush(undoStack, entry, now);
  notifShow(entry.label, notifLevel(entry.label), { text: "撤销", onAction: () => undoRun(entry.id) });
  return entry.id;
}

// 点「撤销」：命中未过期就跑回调，否则提示反悔时间已过。返回是否真的撤成了。
function undoRun(id) {
  const hit = undoTake(undoStack, id, Date.now());
  if (!hit) { toast("反悔时间已过，恢复不了啦"); return false; }
  undoStack = hit.rest;
  try {
    hit.entry.undo();
    toast("已撤销");
    return true;
  } catch (e) {
    toast("撤销失败，请手动重试");
    return false;
  }
}

/* tabs */
$$(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.remove("on"));
    t.classList.add("on");
    $$(".tabpane").forEach((p) => p.classList.remove("on"));
    const pane = $("#" + paneIdFor(t.dataset.tab));   // paneIdFor 给裸 id，$ 吃完整选择器
    // 系统「减少动效」偏好：时长压到 0（CSS 全局兜底之外，UI 线程同步再拦一道）
    pane.style.setProperty("--pane-dur",
      paneAnimMs(window.matchMedia("(prefers-reduced-motion: reduce)").matches) + "ms");
    pane.classList.add("on");
  });
});

/* ---------------- 触摸板 ----------------
   单指：轻点=点击、拖动=滑动（映射整块屏幕）。
   双指（参照桌面触控板/Google TV 遥控的手势惯例）：上下滑=音量、左右滑=快进快退，
   每 GESTURE_STEP px 发一次键（sendKey 自带 90ms 节流，天然限速）；双指轻点=播放/暂停。
   手势期间作废单指滑动——两根手指都抬起才结算，避免误触发。 */
/* 触摸板灵敏度：默认 1×（出厂手感），存 localStorage（键 atv.padSens，可编辑内容
   不进 state.json）。只影响触摸板的双指手势与单指长按连发；方向键按钮区固定节奏不动。 */
let padGain = (() => {
  try { return padSensClamp(JSON.parse(localStorage.getItem(PADSENS_KEY))); }
  catch (e) { return PADSENS_DEFAULT; }
})();
function padSensSave() { try { localStorage.setItem(PADSENS_KEY, String(padGain)); } catch (e) {} }
function padSensApply() {
  const el = $("#padSens"); if (el) el.value = String(padGain);
  const v = $("#padSensVal"); if (v) v.textContent = padSensLabel(padGain);   // textContent，禁 innerHTML
}
function padSensBind() {
  const el = $("#padSens");
  if (el) el.addEventListener("input", () => { padGain = padSensClamp(Number(el.value)); padSensApply(); padSensSave(); });
  const r = $("#padSensReset");
  if (r) r.addEventListener("click", () => { padGain = PADSENS_DEFAULT; padSensApply(); padSensSave(); toast("触摸板灵敏度已重置为 1×"); });
}
// 初值同步 DOM 与 localStorage：必须在 let padGain 初始化之后调用（TDZ）
padSensApply();
padSensBind();

const pad = $("#touchpad");
const padHint = $("#padHint");
/* 触摸板提示语的单一来源：renderStatus 每 8s 轮询也会写这个元素，
   两处必须同文，否则手势说明会被轮询悄悄覆盖回旧文案。 */
function padHintText(isApple) {
  return "轻点 = 点击 · 拖动 = 滑动（" +
    (isApple ? "Apple TV 触控" : "映射整块电视屏幕") +
    "）· ✌️ 双指上下滑=音量、左右滑=快进，双轻点=播放/暂停 · 单指长按=连续滚动";
}
const GESTURE_STEP = 26;         // 双指每滑过 26px 发一次音量/seek
let ptr = null;                  // 单指滑动状态
const padPtrs = new Map();       // 按在触摸板上的指针（含单指）
let gesture = null;              // 双指手势状态

/* 单指长按=连续滚动：按住不动 450ms 起、之后每 120ms 发一次方向键（复用按键区同款节奏）。
   静止不动=向下滚（滚列表/片单最高频），之后手指往哪边偏就往哪边滚；松手即停，
   且这次按压不再触发 tap/swipe——长按和滑动是两种意图，不能都发。 */
const PAD_HOLD_DELAY = 450, PAD_HOLD_RATE = 120, PAD_HOLD_DIR_PX = 24;
const PAD_HOLD_LABEL = { 19: "⏫ 连续滚动（上）", 20: "⏬ 连续滚动（下）", 21: "⏪ 连续滚动（左）", 22: "⏩ 连续滚动（右）" };
let holdTimer = null, holdTick = null, holding = false;

function stopPadHold() {
  clearTimeout(holdTimer);
  clearInterval(holdTick);
  holdTimer = holdTick = null;
  if (holding) {
    holding = false;
    pad.classList.remove("holding");
    padHint.textContent = padHintText(status.curType === "appletv");
  }
}
function padHoldDir() {
  if (!ptr) return 20;
  const dx = ptr.x1 - ptr.x0, dy = ptr.y1 - ptr.y0;
  if (Math.abs(dx) < PAD_HOLD_DIR_PX && Math.abs(dy) < PAD_HOLD_DIR_PX) return 20;  // 没偏=向下
  return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 22 : 21) : (dy > 0 ? 20 : 19);
}
function padHoldTick() {
  const code = padHoldDir();
  sendKey(code);   // sendKey 自带 90ms 节流，120ms 间隔天然不会超发；顺带点亮屏幕方向键
  padHint.textContent = PAD_HOLD_LABEL[code];
}

pad.addEventListener("pointerdown", (e) => {
  pad.setPointerCapture(e.pointerId);
  const r = pad.getBoundingClientRect();
  padPtrs.set(e.pointerId, { x: e.clientX - r.left, y: e.clientY - r.top });
  if (padPtrs.size === 2) {
    // 第二根手指落下：进入双指手势，作废进行中的单指滑动与长按
    stopPadHold();
    ptr = null;
    const [a, b] = [...padPtrs.values()];
    gesture = { t0: performance.now(), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2,
                rx: 0, ry: 0, vol: 0, seek: 0, moved: false };
    pad.classList.add("gesturing");
    padHint.textContent = "✌️ 上下滑=音量 · 左右滑=快进";
    buzz();
  } else if (padPtrs.size === 1) {
    ptr = { x0: e.clientX - r.left, y0: e.clientY - r.top, x1: e.clientX - r.left, y1: e.clientY - r.top, t0: performance.now() };
    pad.classList.add("dragging");
    // 长按定时器：450ms 内明显移动（要滑动）就撤掉，静止才进入连续滚动
    holdTimer = setTimeout(() => {
      holdTimer = null;
      holding = true;
      pad.classList.add("holding");
      padHoldTick();
      holdTick = setInterval(padHoldTick, padHoldRateFor(padGain));   // 增益越高间隔越短、越跟手
    }, PAD_HOLD_DELAY);
  }
});
pad.addEventListener("pointermove", (e) => {
  const r = pad.getBoundingClientRect();
  if (padPtrs.has(e.pointerId)) padPtrs.set(e.pointerId, { x: e.clientX - r.left, y: e.clientY - r.top });
  if (ptr) { ptr.x1 = e.clientX - r.left; ptr.y1 = e.clientY - r.top; }
  if (holdTimer && Math.hypot(ptr.x1 - ptr.x0, ptr.y1 - ptr.y0) > 12) stopPadHold();
  if (!gesture || padPtrs.size < 2) return;
  const sp = padStepFor(padGain);   // 灵敏度增益 → 双指每滑过 sp px 发一次键
  const [a, b] = [...padPtrs.values()];
  const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
  gesture.rx += cx - gesture.cx;
  gesture.ry += cy - gesture.cy;
  gesture.cx = cx; gesture.cy = cy;
  if (Math.hypot(gesture.rx, gesture.ry) > 12) gesture.moved = true;
  // 垂直：上滑(负)=音量增，下滑(正)=音量减
  while (Math.abs(gesture.ry) >= sp) {
    const step = Math.sign(gesture.ry);
    gesture.ry -= step * sp;
    gesture.vol += step;
    sendKey(step < 0 ? 24 : 25);
  }
  // 水平：右滑(正)=快进，左滑(负)=快退
  while (Math.abs(gesture.rx) >= sp) {
    const step = Math.sign(gesture.rx);
    gesture.rx -= step * sp;
    gesture.seek += step;
    sendKey(step > 0 ? 90 : 89);
  }
  padHint.textContent = gesture.vol
    ? `${gesture.vol < 0 ? "🔊 音量 +" : "🔉 音量 -"}${Math.abs(gesture.vol)}`
    : gesture.seek
      ? `${gesture.seek > 0 ? "⏩ 快进 ×" : "⏪ 快退 ×"}${Math.abs(gesture.seek)}`
      : "✌️ 上下滑=音量 · 左右滑=快进";
});
function endPadGesture() {
  if (!gesture) return;
  // 双指轻点（位移小、时间短）= 播放/暂停，比去点媒体键快一步
  if (!gesture.moved && performance.now() - gesture.t0 < 350) {
    sendKey(85);
    padHint.textContent = "⏯ 播放/暂停";
  }
  gesture = null;
  pad.classList.remove("gesturing");
  setTimeout(() => { if (!gesture) padHint.textContent = padHintText(status.curType === "appletv"); }, 600);
}
pad.addEventListener("pointerup", async (e) => {
  if (gesture) {                  // 手势中抬起任一指即结算，剩下那根手指不接续单指滑动
    padPtrs.delete(e.pointerId);
    endPadGesture();
    return;
  }
  padPtrs.delete(e.pointerId);
  if (holding) { stopPadHold(); return; }   // 长按结束=停滚，这次按压不再发 tap/swipe
  if (!ptr) return;               // 单指滑动只在「一根手指按下又抬起」时结算
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
pad.addEventListener("pointercancel", (e) => {
  padPtrs.delete(e.pointerId);
  if (gesture) endPadGesture();
  stopPadHold();
  ptr = null;
  pad.classList.remove("dragging");
});

/* ---------------- 截屏 / 画面（第十七轮：截屏工作台） ----------------
   学 Home Assistant 的 camera snapshot 查看（可缩放）、Google Photos 的近期项目条、
   macOS 预览的 ⌘+ / ⌘0 / 双击 1:1。旧链路「截一张看一张、看完就丢」只改最小部分：
   抓 blob → 入归档（新→旧，上限 SHOT_MAX），当前显示的是指针 shotUrl——
   只有被挤出归档的那张才 revoke blob URL（旧代码每张都 revoke，归档会互相踩）。 */
const SHOT_MAX = 8;                    // 归档上限：blob URL 常驻内存，8 张 ≈ 几十 MB 量级
const SHOT_ZOOM_MAX = 6;
const shotShots = [];                  // [{url, ts, dev, w, h}]，新 → 旧
let shotUrl = null;                    // 当前显示的 blob URL（指针，不独占 revoke 权）
let shotView = { s: 1, x: 0, y: 0 };   // 缩放倍率 + 平移量（stage 坐标系）
const shotPtrs = new Map();            // 活跃指针：单指拖拽 + 双指 pinch
let shotPinch = null;                  // pinch 基准 {d, cx, cy}（上一帧）

function shotFmt(ts) {                 // 文件名用：20260926-013215
  const d = new Date(ts), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
function shotClock(ts) {               // 角标用：01:32:15
  const d = new Date(ts), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function shotApply() {
  const img = $("#shotImg"), st = $("#shotStage");
  img.style.transform = `translate(${shotView.x}px, ${shotView.y}px) scale(${shotView.s})`;
  $("#shotZoomLabel").textContent = Math.round(shotView.s * 100) + "%";
  st.classList.toggle("zoomed", shotView.s > 1);
  st.classList.toggle("grabbing", shotView.s > 1 && shotPtrs.size > 0);
}

function shotClamp() {
  // 别把画面整个拖出口：左/上沿不越过 0，右/下沿不露底色
  const img = $("#shotImg"), st = $("#shotStage");
  const w = img.clientWidth * shotView.s, h = img.clientHeight * shotView.s;
  if (w <= st.clientWidth) shotView.x = 0;
  else shotView.x = Math.min(0, Math.max(st.clientWidth - w, shotView.x));
  if (h <= st.clientHeight) shotView.y = 0;
  else shotView.y = Math.min(0, Math.max(st.clientHeight - h, shotView.y));
}

function shotFit() { shotView = { s: 1, x: 0, y: 0 }; shotApply(); }

// 以 stage 内的 (cx, cy) 为锚点缩放：锚点对着的内容不动，其余跟着胀缩
function shotZoomAt(f, cx, cy) {
  const st = $("#shotStage");
  if (cx == null) cx = st.clientWidth / 2;
  if (cy == null) cy = st.clientHeight / 2;
  const s = Math.min(SHOT_ZOOM_MAX, Math.max(1, shotView.s * f));
  shotView.x = cx - (cx - shotView.x) * (s / shotView.s);
  shotView.y = cy - (cy - shotView.y) * (s / shotView.s);
  shotView.s = s;
  shotClamp();
  shotApply();
}

function shotRenderStrip() {
  const box = $("#shotStrip");
  box.textContent = "";            // textContent 清空即销毁子节点，后续全程 DOM API 构建
  shotShots.forEach((shot, i) => {
    const item = document.createElement("div");
    item.className = "shotthumb";
    item.setAttribute("role", "listitem");
    if (shot.url === shotUrl) item.setAttribute("aria-current", "true");
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.i = String(i);
    b.title = shotFmt(shot.ts);
    b.setAttribute("aria-label", `第 ${i + 1} 张 ${shotClock(shot.ts)}`);
    const im = document.createElement("img");
    im.src = shot.url;
    im.alt = "";
    im.draggable = false;
    b.appendChild(im);
    item.appendChild(b);
    box.appendChild(item);
  });
}

function shotCapText(shot) {
  return `${shot.w || "?"}×${shot.h || "?"} · ${shotClock(shot.ts)}${shot.dev ? " · " + shot.dev : ""}`;
}

function shotShow(i) {
  const shot = shotShots[i];
  if (!shot) return;
  shotUrl = shot.url;
  $("#shotImg").src = shot.url;
  $("#shotSave").href = shot.url;
  $("#shotSave").download = `tv-${shotFmt(shot.ts)}.png`;
  $("#shotCap").textContent = shotCapText(shot);
  shotFit();
  shotRenderStrip();
}

function shotPush(url) {
  // 设备名从状态栏取（局域网广播可伪造 → 只读 textContent、只写 textContent）
  const dev = ($("#tvInfo").textContent || "")
    .split("·")[0].trim().replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]\s*/u, "");
  shotShots.unshift({ url, ts: Date.now(), dev, w: 0, h: 0 });
  // 只 revoke 被挤出去的那张：在册的每一张都可能被再次点开。调用方紧接着
  // shotShow(0)，即使挤出的正是当前显示项，也在同一 tick 换掉 src。
  while (shotShots.length > SHOT_MAX) URL.revokeObjectURL(shotShots.pop().url);
}

// 图加载完才知道真实像素：回填角标（缩略图条不显示尺寸，保持安静）
$("#shotImg").addEventListener("load", () => {
  const shot = shotShots.find((s) => s.url === shotUrl);
  if (!shot || !$("#shotImg").naturalWidth) return;
  shot.w = $("#shotImg").naturalWidth;
  shot.h = $("#shotImg").naturalHeight;
  $("#shotCap").textContent = shotCapText(shot);
});

$("#shotBtn").addEventListener("click", async () => {
  log(status.curType === "appletv" ? "正在获取画面…" : "正在截屏…");
  try {
    const r = await fetch("/api/screenshot", { headers: { ...TOKEN_HDR } });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || "获取画面失败");
    }
    shotPush(URL.createObjectURL(await r.blob()));
    openModal("#shotModal", $("#shotBtn"));
    shotShow(0);
    log("完成");
  } catch (e) {
    log("⚠ " + e.message);
    toast(e.message);
  }
});
$("#shotClose").addEventListener("click", () => closeModal("#shotModal"));
$("#shotModal").addEventListener("click", (e) => {
  if (e.target === $("#shotModal")) closeModal("#shotModal");
});
// 关窗即复位：缩放/平移/手势状态不留到下一张（焦点交还由 openModal/closeModal 管）
$("#shotModal").addEventListener("modalclosed", () => {
  shotPtrs.clear();
  shotPinch = null;
  shotFit();
});
$("#shotStrip").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (b) shotShow(Number(b.dataset.i));
});

$("#shotZoomIn").addEventListener("click", () => shotZoomAt(1.4));
$("#shotZoomOut").addEventListener("click", () => shotZoomAt(1 / 1.4));
$("#shotZoomFit").addEventListener("click", () => shotFit());
$("#shotStage").addEventListener("dblclick", () => {
  if (shotView.s > 1) shotFit();          // 已放大 → 回适应态
  else shotZoomAt(1.5);                   // 适应态 → 150%
});
$("#shotStage").addEventListener("wheel", (e) => {
  e.preventDefault();                     //  passive:false，别把弹窗里的滚轮滚到页面
  const r = e.currentTarget.getBoundingClientRect();
  shotZoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
}, { passive: false });

function shotLocal(e) {
  const r = $("#shotStage").getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}
$("#shotStage").addEventListener("pointerdown", (e) => {
  shotPtrs.set(e.pointerId, shotLocal(e));
  try { e.currentTarget.setPointerCapture(e.pointerId); }
  catch (err) { /* 合成指针没有活动 id，放弃捕获照样能拖 */ }
  if (shotPtrs.size === 2) shotPinch = null;   // 第二根落下：重置基准，防止跳变
  if (shotView.s > 1) e.preventDefault();      // 放大态阻止原生拖拽/选区
  shotApply();
});
$("#shotStage").addEventListener("pointermove", (e) => {
  if (!shotPtrs.has(e.pointerId)) return;
  const p = shotLocal(e), prev = shotPtrs.get(e.pointerId);
  shotPtrs.set(e.pointerId, p);
  if (shotPtrs.size >= 2) {                     // 双指 pinch：以中指距为倍率
    const pts = [...shotPtrs.values()];
    const d = Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
    const cx = (pts[0][0] + pts[1][0]) / 2, cy = (pts[0][1] + pts[1][1]) / 2;
    if (shotPinch && shotPinch.d > 0) shotZoomAt(d / shotPinch.d, cx, cy);
    shotPinch = { d, cx, cy };
    return;
  }
  if (shotView.s <= 1) return;                  // 适应态整体可见，拖动没有意义
  shotView.x += p[0] - prev[0];
  shotView.y += p[1] - prev[1];
  shotClamp();
  shotApply();
});
function shotPtrEnd(e) {
  shotPtrs.delete(e.pointerId);
  if (shotPtrs.size < 2) shotPinch = null;
  shotApply();
}
$("#shotStage").addEventListener("pointerup", shotPtrEnd);
$("#shotStage").addEventListener("pointercancel", shotPtrEnd);

// 快捷键学 macOS 预览：+ / − / 0。注册在 capture 阶段，与命令面板热键同款——
// 弹窗开着时通用兜底已 stopPropagation（键值到不了电视），同节点后注册的监听照常执行。
// 只认「截图弹窗是顶层弹窗」：命令面板开着时这些键属于输入框。
document.addEventListener("keydown", (e) => {
  if (openModals[openModals.length - 1] !== $("#shotModal")) return;
  const tag = (e.target && e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (e.key === "+" || e.key === "=") { e.preventDefault(); e.stopPropagation(); shotZoomAt(1.4); }
  else if (e.key === "-" || e.key === "_") { e.preventDefault(); e.stopPropagation(); shotZoomAt(1 / 1.4); }
  else if (e.key === "0") { e.preventDefault(); e.stopPropagation(); shotFit(); }
}, true);

loadMacros();

/* ---------------- 按键自定义（第十八轮；学 Kodi keymap 分层覆盖 / macOS 修饰键重映射 / HA remote command 目录） ----------------
   物理键 -> 动作。绑定表存 localStorage（与自定义宏同规矩：state.json 敏感，不放可编辑
   内容），默认行为是「发这个键自己的 keyevent」——没改绑的键走 sendKey(code)，与历史
   版本行为一致。动作目录全部复用既有出口（sendKey / launchApp / runMacro），零新增后端
   路由。连发性跟随绑定目标推导：方向/音量/seek 类 keyevent 可连发；启动器与宏不连发
   （连发启动器 = 连环重启 App）。长按键 550ms 进改键弹窗：pointerdown 即发送保持旧
   手感，长按只是附加入口，触发时先停连发，不让用户白白多发几个键。 */
const KM_LS = "atv.keymap_v1";
const KM_EDIT_MS = 550;
// 可绑定的 keyevent 目录（与命令面板 PAL_KEYS 同源，补回车：文本框场景常用）
const KM_KEY_ACTIONS = [[19, "上"], [20, "下"], [21, "左"], [22, "右"], [23, "确定"], [4, "返回"],
  [3, "主页"], [82, "菜单"], [26, "电源"], [224, "唤醒"], [24, "音量+"], [25, "音量-"], [164, "静音"],
  [85, "播放 / 暂停"], [87, "下一集"], [88, "上一集"], [86, "停止"], [66, "回车"]];
let kmMap = loadKeymap();          // {"19": "app:com…"}；空对象 = 全部默认
let kmEditCode = null;             // 正在改绑的物理键（null = 引导态）

function loadKeymap() {
  try {
    const v = JSON.parse(localStorage.getItem(KM_LS));
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch (e) { return {}; }
}
function saveKeymap() { localStorage.setItem(KM_LS, JSON.stringify(kmMap)); }

function kmKeyName(code) {
  const hit = KM_KEY_ACTIONS.find((k) => k[0] === code);
  return hit ? hit[1] : "键码 " + code;
}

// 动作 id -> {label, hint, run}；null = 绑定已失效（应用被删 / 宏被清）
function kmFind(action) {
  if (!action) return null;          // 没改绑：默认行为，不是「绑定失效」
  if (action === "none") return { label: "无操作", hint: "按下什么都不做", run: () => {} };
  if (action.startsWith("key:")) {
    const code = +action.slice(4);
    return { label: "按键 " + kmKeyName(code), hint: "keyevent " + code, run: () => sendKey(code) };
  }
  if (action.startsWith("app:")) {
    const pkg = action.slice(4);
    const a = APPS.find((x) => x.pkg === pkg) || recentApps().find((x) => x.pkg === pkg);
    if (!a) return null;
    return { label: "打开 " + a.name, hint: a.pkg, run: () => launchApp(a.name, a.pkg) };
  }
  if (action.startsWith("macro:")) {
    const id = action.slice(6);
    const m = [...(Macro.presets || []), ...customMacros()].find((x) => String(x.id || x.name) === id);
    if (!m) return null;
    return { label: "运行宏「" + m.name + "」", hint: (m.steps || []).length + " 步", run: () => runMacro(m) };
  }
  return null;
}

// 统一发送入口：没改绑就发自己（历史行为），改了绑按绑定分发
async function kmSend(code) {
  const action = kmMap[String(code)];
  if (!action) return sendKey(code);
  const f = kmFind(action);
  if (!f) { toast("该键的绑定已失效，重新设置一下"); return; }
  buzz();
  return f.run();
}

// 长按是否连发：只认 keyevent，且目标键本身在 HOLD_KEYS 里
function kmHoldable(action) {
  if (!action || action === "none") return false;
  return action.startsWith("key:") && HOLD_KEYS.has(+action.slice(4));
}

function kmActions() {
  const groups = [{ name: "按键", items: KM_KEY_ACTIONS.map(([code, name]) =>
    ({ action: "key:" + code, label: "按键 " + name, hint: "keyevent " + code })) }];
  const apps = [...APPS, ...recentApps().filter((r) => !APPS.some((a) => a.pkg === r.pkg))];
  if (apps.length) groups.push({ name: "应用", items: apps.map((a) =>
    ({ action: "app:" + a.pkg, label: "打开 " + a.name, hint: a.pkg })) });
  const macros = [...(Macro.presets || []), ...customMacros()];
  if (macros.length) groups.push({ name: "宏", items: macros.map((m) =>
    ({ action: "macro:" + (m.id || m.name), label: "运行宏「" + m.name + "」",
      hint: (m.steps || []).length + " 步" })) });
  return groups;
}

function kmRenderList() {
  const box = $("#kmList");
  box.textContent = "";            // 全程 DOM API：动作名来自应用目录/宏名，用户可编辑
  const cur = kmEditCode == null ? null : kmMap[String(kmEditCode)];
  kmActions().forEach((g) => {
    const head = document.createElement("div");
    head.className = "kmgroup";
    head.textContent = g.name;
    box.appendChild(head);
    g.items.forEach((it) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "kmrow";
      row.setAttribute("role", "listitem");
      row.dataset.a = it.action;
      row.title = it.hint;
      const lab = document.createElement("span");
      lab.className = "kmlabel";
      lab.textContent = it.label;
      const h = document.createElement("span");
      h.className = "kmhint";
      h.textContent = it.hint;
      row.appendChild(lab);
      row.appendChild(h);
      if (it.action === cur) row.setAttribute("aria-current", "true");
      box.appendChild(row);
    });
  });
}

function kmOpen(code, trigger) {
  kmEditCode = code == null ? null : code;
  const guide = code == null;
  $("#kmEmpty").classList.toggle("hidden", !guide);
  $("#kmList").classList.toggle("hidden", guide);
  if (guide) {
    $("#kmKeyLabel").textContent = "按键自定义";
    $("#kmCur").textContent = "长按任意键 " + KM_EDIT_MS + "ms";
  } else {
    const f = kmFind(kmMap[String(code)]);
    $("#kmKeyLabel").textContent = kmKeyName(code);
    $("#kmCur").textContent = f ? "当前：" + f.label : "当前：默认";
  }
  kmRenderList();
  openModal("#keymapModal", trigger);
}

// 绑回自己 = 删条目（不是写 "key:19"）：默认就该是「没有记录」
function kmBind(code, action) {
  if (action === "key:" + code) delete kmMap[String(code)];
  else kmMap[String(code)] = action;
  saveKeymap();
  kmRefreshMarks();
  const f = action === "key:" + code ? null : kmFind(action);
  log(action === "key:" + code ? `⌨ ${kmKeyName(code)} 恢复默认`
    : `⌨ ${kmKeyName(code)} → ${f ? f.label : "无操作"}`);
  closeModal("#keymapModal");
}

function kmResetAll() {
  const n = Object.keys(kmMap).length;
  if (!n) { log("⌨ 没有改过的键"); toast("没有改过的键"); return; }   // 没得可撤，别弹撤销按钮
  const prev = {};
  Object.keys(kmMap).forEach((k) => { prev[k] = kmMap[k]; });
  undoable("已恢复 " + n + " 个默认按键", () => {
    kmMap = {};
    saveKeymap();
    kmRefreshMarks();
    log("⌨ 已恢复 " + n + " 个默认按键");
    if (openModals.includes($("#keymapModal"))) closeModal("#keymapModal");
  }, () => {
    kmMap = prev;
    saveKeymap();
    kmRefreshMarks();
  });
}

// 改过的键带 • 标记 + title 提示当前绑定：一眼看出「这个键被我动过」
function kmRefreshMarks() {
  $$("[data-key]").forEach((btn) => {
    const f = kmFind(kmMap[String(+btn.dataset.key)]);
    btn.classList.toggle("remapped", !!kmMap[String(+btn.dataset.key)]);
    if (f) btn.title = "按下：" + f.label;
    else btn.removeAttribute("title");
  });
}

$("#kmClose").addEventListener("click", () => closeModal("#keymapModal"));
$("#keymapModal").addEventListener("click", (e) => {
  if (e.target === $("#keymapModal")) closeModal("#keymapModal");
});
$("#kmList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-a]");
  if (row && kmEditCode != null) kmBind(kmEditCode, row.dataset.a);
});
$("#kmUnbind").addEventListener("click", () => { if (kmEditCode != null) kmBind(kmEditCode, "none"); });
$("#kmResetAll").addEventListener("click", kmResetAll);
$("#kmResetAllBtn").addEventListener("click", kmResetAll);
kmRefreshMarks();

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
//   命令、二维码、提示里的地址一律用服务端给出的局域网 IP，不能用 location.origin：
//   在 Mac 上打开页面时 origin 是 127.0.0.1，复制给手机的就是回环地址，一定连不上
//   （访问不了页面 / ERR_ADDRESS_UNREACHABLE，往往就是照着错地址访问的结果）。
//   服务端取不到局域网 IP 时才退回 origin（至少是当前页面能用的地址）。
let installBase = location.origin;
const installTip = document.createElement("p");
installTip.className = "hint";
function renderInstall() {
  const installCmd = `curl -sL ${installBase}/install${TOKEN_Q} | bash`;
  $("#installCmd").value = installCmd;
  $("#qrImg").src = "/api/qr.svg?text=" + encodeURIComponent(installCmd) + TOKEN_AMP;
  if (ATV_TOKEN) {
    installTip.textContent = "🔒 本机已启用访问令牌，手机浏览器请打开：" +
    installBase + "/?token=" + ATV_TOKEN + "（本机 127.0.0.1 访问免令牌）";
  }
  const lanUrl = $("#lanUrl");
  if (lanUrl) {
    lanUrl.textContent = installBase + "/";
    $("#lanHint").hidden = false;
    const lanTokenQ = $("#lanTokenQ");
    if (lanTokenQ) lanTokenQ.textContent = ATV_TOKEN ? "?token=" + ATV_TOKEN : "";
  }
}
renderInstall();
$("#qrImg").onerror = () => { document.querySelector(".qrbox").style.display = "none"; }; // 无 qrcode 库时隐藏
// 未启用令牌时 setupQrBox 保持 hidden；启用后这里换成「带令牌的页面地址」二维码，
// 手机扫一次即完成首次接入（服务端会种 cookie，之后不再需要令牌）
api("/api/setup").then((j) => {
  if (!j || !j.url) return;
  installBase = j.url;
  renderInstall();
  if (!ATV_TOKEN || !j.token) return;
  $("#setupQrBox").hidden = false;
  $("#setupQrImg").src = "/api/qr.svg?text=" + encodeURIComponent(j.url + "?token=" + j.token) + TOKEN_AMP;
}).catch(() => {});

if (ATV_TOKEN && $("#phoneInstall")) {
  $("#phoneInstall").insertBefore(installTip, $("#phoneInstall").querySelector("ol"));
}

// 启用令牌后，APK 直链与手机访问地址都得带上它
const apkLink = $("#apkLink");
if (apkLink) apkLink.href = "/app.apk" + TOKEN_Q;
$("#copyDebugBtn").addEventListener("click", async () => {
  const i = $("#termuxDebugCmd");
  const ok = await sheetCopy(i.value);
  if (!ok) {
    i.select();
    toast("复制失败，请长按手动选择", false);
    return;
  }
  toast("已复制！打开 Termux 粘贴回车即可", true);
});
$("#copyPyatvFixBtn").addEventListener("click", async () => {
  const i = $("#pyatvFixCmd");
  const ok = await sheetCopy(i.value);
  if (!ok) {
    i.select();
    toast("复制失败，请长按手动选择", false);
    return;
  }
  toast("已复制！在 Termux 里粘贴回车，装完就能遥控 Apple TV", true);
});
$("#copyCmd").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#installCmd").value);
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
  // switch 的视觉完全由 aria-checked 驱动（CSS [aria-checked="true"]），不写文字状态
  $("#hapticBtn").setAttribute("aria-checked", hapticEnabled() ? "true" : "false");
}
function renderIntentBtn() {
  $("#intentBtn").setAttribute("aria-checked", intentEnabled() ? "true" : "false");
}
$("#intentBtn").addEventListener("click", () => {
  localStorage.setItem(INTENT_KEY, intentEnabled() ? "0" : "1");
  renderIntentBtn();
  toast(intentEnabled() ? "语音意图识别已开" : "语音意图识别已关：说的话将原样发给电视");
});
// 一键体检（第三十四轮）：连不上时把整份报告递给帮忙的人，省去来回问版本 / adb / IME
$("#diagCopyBtn").addEventListener("click", async () => {
  let d;
  try {
    d = await api("/api/diagnostics");
  } catch (e) {
    toast("获取诊断信息失败：" + (e && e.message ? e.message : e), false);
    return;
  }
  const ok = await sheetCopy(diagReportText(d));
  toast(ok ? "诊断报告已复制，直接贴给帮忙的人" : "复制失败，请长按手动选择", !!ok);
});
$("#hapticBtn").addEventListener("click", () => {
  localStorage.setItem(HAPTIC_KEY, hapticEnabled() ? "0" : "1");
  renderHapticBtn();
  if (hapticEnabled()) buzz(20);   // 打开时立刻震一下，让用户知道效果
});
$("#clearPhrasesBtn").addEventListener("click", () => {
  const prevPhrases = phrases.slice();
  const prevRaw = localStorage.getItem(PHRASE_KEY);   // null = 用户从没存过：撤销时删键而不是写 "null"
  undoable("已重置常用短语为默认", () => {
    localStorage.removeItem(PHRASE_KEY);
    phrases = DEFAULT_PHRASES.slice();
    renderPhrases();
    $("#phraseCount").textContent = phrases.length;
  }, () => {
    if (prevRaw === null) localStorage.removeItem(PHRASE_KEY);
    else localStorage.setItem(PHRASE_KEY, prevRaw);
    phrases = prevPhrases.slice();
    renderPhrases();
    $("#phraseCount").textContent = phrases.length;
  });
});
$("#clearMacrosBtn").addEventListener("click", () => {
  if (!customMacros().length) { toast("没有自定义宏可清空"); return; }   // 空列表再清一次只会换来一条废撤销
  const prevRaw = localStorage.getItem(MACRO_LS);
  undoable("已清空自定义宏", () => {
    localStorage.removeItem(MACRO_LS);
    loadMacros();
  }, () => {
    if (prevRaw === null) localStorage.removeItem(MACRO_LS);
    else localStorage.setItem(MACRO_LS, prevRaw);
    loadMacros();
  });
});
$("#coachBtn").addEventListener("click", () => startCoach("all"));   // 随时能整段重看

/* ---------------- 主题 ----------------
   默认跟随系统（prefers-color-scheme），但看电视常在暗房间、手机却是浅色模式——
   所以给显式选择。写 html[data-theme]，CSS 里属性选择器特异性压过媒体查询；
   head 内联脚本负责首帧不闪，这里负责同步 UI 与持久化。 */
const THEME_KEY = "atv.theme";
function applyTheme(v) {
  if (v) document.documentElement.dataset.theme = v;
  else delete document.documentElement.dataset.theme;
  $$("#themeSeg button").forEach((b) => {
    const on = b.dataset.themeVal === (v || "");
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
}
$$("#themeSeg button").forEach((b) => b.addEventListener("click", () => {
  const v = b.dataset.themeVal;
  if (v) localStorage.setItem(THEME_KEY, v);
  else localStorage.removeItem(THEME_KEY);
  applyTheme(v);
  buzz(12);
}));
applyTheme(localStorage.getItem(THEME_KEY));
$("#appSettingsBtn").addEventListener("click", () => {
  $("#phraseCount").textContent = phrases.length;
  renderHapticBtn();
  renderIntentBtn();
  openModal("#settingsModal");
});
$("#settingsCloseBtn").addEventListener("click", () => closeModal("#settingsModal"));
$("#settingsModal").addEventListener("click", (e) => {
  if (e.target === $("#settingsModal")) closeModal("#settingsModal");
});

/* ---------------- 命令面板 ----------------
   学习源：VS Code Command Palette（Ctrl/⌘+K 唤起、模糊匹配、最近使用置顶）与
   Home Assistant 的 Quick Bar / header search。落到本项目：页面能力越来越多
   （连接、应用、按键、宏、工具），手机一屏摆不下、熟手只能滚动找按钮——面板把
   它们汇成一个键盘优先的入口，敲几个字母就能到达。
   边界与取舍：
   - 命令是「已有交互的快捷方式」，执行体全部复用现有函数/按钮点击，零后端新增；
   - 设备名 / IP 来自局域网广播、可伪造，渲染一律 textContent（AGENTS.md 禁令）；
   - 全局热键用 capture 注册：别的弹窗的按键兜底会 stopPropagation，但同节点同阶段
     的后续监听仍会执行，所以 Cmd+K 在任何界面都能唤起/收起，且 K 绝不下发到电视。 */
const PAL_KEY = "atv.palRecent";
/* ===== cfgxfer:begin =====
   配置搬家（换机 / 换浏览器时把本机设置一次带走）纯函数段：禁 DOM / localStorage /
   fetch / innerHTML / $(（tests/backup_harness.js 直接抽这段执行），DOM 胶水在段外。
   学习源 chezmoi v2.73.0（MIT，Copyright (c) 2018 Tom Payne，proxy.golang.org 源码包
   逐行核对），三条口径：
   1) 坏档在写任何东西之前就失败 —— internal/cmd/config.go:998-1035：
      decodeConfigFile 先 ReadFile 再 decodeConfigContents，最后才做 1028-1033 的
      语义互斥校验（git.commitMessageTemplate 与 git.commitMessageTemplateFile
      不能同时给）。三步任一失败都直接 return，configFile 一个字都没被写。
      落到本项目：导入先 cfgParseBackup 完整解析校验，坏 JSON 只报错，
      现有 localStorage 一个字节都不动。
   2) 写入只认「完整的一份」—— internal/chezmoi/realsystem_unix.go:68-104：
      WriteFile 在 safe 模式下经 renameio 写同目录临时文件（89 tempDir =
      renameio.TempDir(dir.String())），Chmod 后 Write，96 行 defer
      chezmoierrors.CombineFunc(&err, t.Cleanup) 保证失败也清理，103 行 return
      t.CloseAtomicallyReplace() 让读者只见到旧或新的完整文件。
      落到本项目：导出是一次性生成的完整 JSON；导入是「全部校验通过后才批量
      落盘」，绝不做「能写几个写几个」的半截状态（半份配置比导入失败更难查）。
   3) 账目化 —— internal/chezmoi/sourcestate.go:917-926 把每个 ignore 命中的路径记进
      s.ignoredRelPaths，ignoredcmd.go:18/37 的 ignored 命令能原样打印出来。
      落到本项目：导出要说清「导了哪些键、因敏感排了哪些键」，导入要说清
      「写了哪些、跳了哪些、坏在哪」，全程可见，不做黑箱。
   敏感纪律：白名单只收非敏感键，另有敏感词副闸。state.json（配对凭据 + 访问令牌）
   后端从来不下发给前端，前端想导也导不出来；但白名单之外万一有别的工具往
   localStorage 塞了 token 类键，副闸一律排掉（AGENTS.md：凭据禁出本机）。 */
const CFG_MAGIC = "atv-remote-backup";   /* 导入时认这个暗号，防别的 JSON 乱认亲 */
const CFG_VER = 1;
/* 白名单 = 可安全搬家的全部配置。不含通知历史 / 键盘历史 / 引导状态：那些是推导数据
   或一次性状态，换机重放没有意义，还可能夹带隐私文本。 */
const CFG_EXPORTABLE = [
  "atv.theme", "atv.haptics", "atv.intent", "atv.gamepad", "atv.gamepadDz",
  "atv.padSens", "atv.favApps", "atv.phrases", "atv.collapsed.v1",
  "atv.palRecent", "atv.recentApps", "atv.keymap_v1", "atv_macros_v1",
];
const CFG_LABELS = {
  "atv.theme": "主题", "atv.haptics": "按键震动", "atv.intent": "语音意图",
  "atv.gamepad": "手柄开关", "atv.gamepadDz": "摇杆死区", "atv.padSens": "触摸板灵敏度",
  "atv.favApps": "收藏夹", "atv.phrases": "常用短语", "atv.collapsed.v1": "卡片折叠态",
  "atv.palRecent": "命令面板最近", "atv.recentApps": "最近应用",
  "atv.keymap_v1": "键位图", "atv_macros_v1": "自定义宏",
};
/* 敏感词副闸：白名单是主闸，这是防「白名单里误加键」与「别的工具塞键」的第二道。
   键名命中即排，值再像配置也不导、不写。 */
const CFG_SENSITIVE_RE = /token|cred|pairing|secret|password|cookie/i;

function cfgLabelOf(key) { return CFG_LABELS[key] || key; }
function cfgIsExportable(key) {
  return CFG_EXPORTABLE.indexOf(key) >= 0 && !CFG_SENSITIVE_RE.test(key);
}
/* 把一串键分成「导」与「因敏感排除」两堆（chezmoi ignored 的账目化口径）。 */
function cfgPlanExport(keys) {
  const include = [], excluded = [];
  for (let i = 0; i < keys.length; i++) {
    if (cfgIsExportable(keys[i])) include.push(keys[i]);
    else if (CFG_SENSITIVE_RE.test(keys[i])) excluded.push(keys[i]);
  }
  return { include: include, excluded: excluded };
}
/* entries: [{key, value}]（胶水刚从 localStorage 读的原始值）→ 完整备份对象。
   任何一个值不是字符串都整体失败：半份备份比没有备份更坏。 */
function cfgBuildBackup(entries) {
  if (!Array.isArray(entries)) return { ok: false, error: "entries 不是数组" };
  const plan = cfgPlanExport(entries.map((e) => (e && typeof e.key === "string" ? e.key : "")));
  const keys = {};
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (!e || typeof e !== "object" || typeof e.key !== "string") continue;
    if (plan.include.indexOf(e.key) < 0) continue;
    if (typeof e.value !== "string") return { ok: false, error: "键 " + e.key + " 的值不是字符串" };
    keys[e.key] = e.value;
  }
  if (Object.keys(keys).length === 0) return { ok: false, error: "没有可导出的配置" };
  return { ok: true, backup: { magic: CFG_MAGIC, version: CFG_VER, keys: keys } };
}
/* 导入第一关：完整解析校验。坏 JSON / magic 不符 / 版本不符 / keys 不对 → ok:false
   带人话 error；调用方据此报错，且不许动现有配置（chezmoi 口径 1）。 */
function cfgParseBackup(text) {
  if (typeof text !== "string") return { ok: false, error: "导入内容不是文本" };
  let obj;
  try { obj = JSON.parse(text); } catch (e) { return { ok: false, error: "不是合法的 JSON 文件" }; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return { ok: false, error: "备份顶层不是对象" };
  if (obj.magic !== CFG_MAGIC) return { ok: false, error: "不是本工具导出的备份（magic 不符）" };
  if (obj.version !== CFG_VER) return { ok: false, error: "备份版本不支持（需 v" + CFG_VER + "，收到 v" + obj.version + "）" };
  if (!obj.keys || typeof obj.keys !== "object" || Array.isArray(obj.keys)) return { ok: false, error: "备份缺少 keys 字段" };
  const names = Object.keys(obj.keys);
  if (names.length === 0) return { ok: false, error: "备份里没有任何配置" };
  const plan = cfgPlanExport(names);
  for (let i = 0; i < plan.include.length; i++) {
    if (typeof obj.keys[plan.include[i]] !== "string") {
      return { ok: false, error: "键 " + plan.include[i] + " 的值不是字符串" };
    }
  }
  if (plan.include.length === 0) return { ok: false, error: "备份里没有可识别的配置键" };
  return { ok: true, backup: obj };
}
/* 导入第二关（cfgParseBackup 已过）：逐键决定写不写。apply=可写、skipped=白名单外或
   敏感、invalid=白名单内但值不对。调用方只准写 apply 的，且必须全部写（口径 2）。 */
function cfgApplyBackup(backup) {
  const apply = [], skipped = [], invalid = [];
  const names = Object.keys((backup && backup.keys) || {});
  for (let i = 0; i < names.length; i++) {
    const k = names[i];
    if (CFG_SENSITIVE_RE.test(k)) { skipped.push({ key: k, reason: "敏感" }); continue; }
    if (CFG_EXPORTABLE.indexOf(k) < 0) { skipped.push({ key: k, reason: "不是已知配置键" }); continue; }
    const v = backup.keys[k];
    if (typeof v !== "string") { invalid.push({ key: k, reason: "值不是字符串" }); continue; }
    apply.push({ key: k, value: v });
  }
  return { apply: apply, skipped: skipped, invalid: invalid };
}
/* ===== cfgxfer:end ===== */
/* ---- 配置搬家 DOM 胶水：规则在上方 cfgxfer 纯函数段，这里只管 localStorage / 文件 / UI ----
   导出只读白名单键（再过敏感词副闸），绝不读 state.json（后端不下发，前端拿不到）。
   导入先 cfgParseBackup 校验，成功了才批量写；失败只 toast，现有配置一个字节不动。 */
function cfgStatus(msg) {
  const el = $("#cfgxferStatus");
  if (el) el.textContent = msg;   /* 一律 textContent，禁 innerHTML */
}
function cfgExport() {
  let allKeys = CFG_EXPORTABLE.slice();
  try { allKeys = Object.keys(localStorage); }   /* 顺便让副闸看见别的工具塞的键 */
  catch (e) { /* 存储被禁（隐私模式）：退回白名单 */ }
  const plan = cfgPlanExport(allKeys);
  const live = plan.include.filter((k) => localStorage.getItem(k) !== null);
  const built = cfgBuildBackup(live.map((k) => ({ key: k, value: localStorage.getItem(k) })));
  if (!built.ok) { cfgStatus("导出失败：" + built.error); toast("导出失败：" + built.error); return; }
  const blob = new Blob([JSON.stringify(built.backup, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "atv-remote-config.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  cfgStatus("已导出 " + live.length + " 项（" + live.map(cfgLabelOf).join("、") + "）" +
    (plan.excluded.length ? "；已排除 " + plan.excluded.length + " 项敏感配置" : ""));
  toast("配置已导出（" + live.length + " 项，不含配对凭据与令牌）");
}
/* 导入后同步：主题 / 灵敏度 / 折叠 / 手柄是即时手感，必须当场刷新；短语、收藏、
   最近、宏、键位在各自面板打开时也会重读，这里一并刷新让用户立刻看到结果。 */
function cfgReloadUI() {
  applyTheme(localStorage.getItem(THEME_KEY));
  try { padGain = padSensClamp(JSON.parse(localStorage.getItem(PADSENS_KEY))); } catch (e) {}
  padSensApply();
  try {
    const v = JSON.parse(localStorage.getItem(PHRASE_KEY));
    phrases = Array.isArray(v) ? v.filter((s) => typeof s === "string").slice(0, 12) : DEFAULT_PHRASES.slice();
  } catch (e) {}
  renderPhrases();
  try { gpOn = localStorage.getItem(GP_KEY) === "1"; } catch (e) {}
  try {
    const dz = JSON.parse(localStorage.getItem(GP_DZ_KEY));
    if (dz !== null && dz !== undefined) gpDz = gpClampDeadzone(dz);
  } catch (e) {}
  gpEnableApply();
  gpDzApply();
  collapseRestore();
  favRender();
  renderRecentApps();
  kmMap = loadKeymap();
  loadMacros();
}
function cfgImportText(text) {
  const parsed = cfgParseBackup(text);
  if (!parsed.ok) {
    cfgStatus("导入失败：" + parsed.error + "（现有配置未改动）");
    toast("导入失败：" + parsed.error);
    return;
  }
  const res = cfgApplyBackup(parsed.backup);
  let wrote = 0;
  for (let i = 0; i < res.apply.length; i++) {
    try { localStorage.setItem(res.apply[i].key, res.apply[i].value); wrote++; } catch (e) {}
  }
  if (wrote !== res.apply.length) {
    cfgStatus("导入未完整：写成功 " + wrote + " / " + res.apply.length + " 项（存储空间不足或被禁）");
    toast("导入未完整，详见设置页状态行");
    return;
  }
  cfgReloadUI();
  cfgStatus("已导入 " + wrote + " 项（" + res.apply.map((x) => cfgLabelOf(x.key)).join("、") +
    "）；跳过 " + res.skipped.length + " 项" +
    (res.invalid.length ? "，无效 " + res.invalid.length + " 项" : ""));
  toast("配置已导入（" + wrote + " 项）");
}
$("#cfgExportBtn").addEventListener("click", () => { cfgExport(); buzz(12); });
$("#cfgImportBtn").addEventListener("click", () => { const f = $("#cfgFileInput"); if (f) f.click(); });
$("#cfgFileInput").addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => { cfgImportText(String(rd.result || "")); };
  rd.onerror = () => { cfgStatus("导入失败：读不了这个文件（现有配置未改动）"); };
  rd.readAsText(f);
  e.target.value = "";   /* 清掉选择，同一个文件才能再导入一次 */
});
/* ===== intent:begin ===== */
/* 说话即遥控 / 命令容错：说的话和敲的命令都先过一层「意图理解」，命中才执行，
   不命中一律原样下发。修的是两个真问题：
   1. 语音识别结果原来直接 sendText——说「声音小一点」会把这五个字打进电视搜索框；
   2. palScore 只有「词首 > 子串 > 别名词首 > 子序列」四档，打错一个字母就 0 命中。

   学习源（都读过真实源码，口径照抄不凭印象）：
   - Fuse.js 7.1.0（Apache-2.0，npm fuse.js，dist/fuse.mjs）：Bitap 模糊匹配。
     可复用的口径是 computeScore()：accuracy = errors / pattern.length，
     proximity = |expectedLocation - currentLocation|，score = accuracy + proximity/distance，
     默认 location=0 / distance=100 / threshold=0.6；查法是先 indexOf 精确快路径，
     再从 0 到 patternLen-1 逐级放宽允许的错误数。本项目取两条：
     (a) intentSubstrErrors() 是「近似子串匹配」的 DP——第 0 行初始化为 0，
         表示文本前缀可免费跳过，取最后一行最小值得 errors，这就是 Bitap 里
         proximity 的来源（匹配可以不在位置 0），比整串 Levenshtein 更贴面板场景；
     (b) 允许的错误数 = floor(长度 * 阈值)。
   - rapidfuzz 3.14.3（MIT，版权 Max Bachmann，PyPI rapidfuzz）：fuzz.ratio /
     partial_ratio / token_sort_ratio / token_set_ratio 组合成 process.extract 的打分，
     后两者按 token 拆句再比，天生吃词序差异（「开一下 Netflix」约等于「Netflix 打开」）。
     intentSim() 照这个组合思路取多视角最大值。
   - Home Assistant conversation（概念参照，本项目前几轮已多次引用）：同义词/别名
     优先于模糊匹配，未命中绝不改写用户输入。

   取舍（都是被测试逼出来的，不是想当然）：
   - **别名只认「整句相等」或「后缀命中」**，不认任意子串。曾经用任意子串，
     结果「打开电视机顶盒」里的「打开电视」触发开机键——用户说的是装什么，不是开电视。
   - **容错只在句尾窗口里找**：说错字的多半在结尾（「说错字的音量城」），
     且句尾比对不会把前缀短语误判成命令。
   - **不命中必须原样当文本**——「周杰伦」这类搜索词不能被误判成命令。
   - 段内禁 DOM / localStorage / fetch / setTimeout，纯函数可整段搬进 node 跑 harness。 */

/* 归一化：语音识别常把英文词转成全角，中文里也混着标点和空格，先拉平再比。
   空格也去掉——中文没有词间空格，去掉后「打开 Netflix」与「打开Netflix」同形。 */
const INTENT_FILLERS = ["请", "帮我", "帮忙", "给我", "把", "一下子", "一下", "快点", "赶紧", "现在"];
function intentNorm(s) {
  let t = String(s == null ? "" : s).toLowerCase().trim();
  t = t.replace(/[\uff01-\uff5e]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0));
  t = t.replace(/[\u3000\s]+/g, "");
  t = t.replace(/[\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f\u2026\u2014\uff08\uff09()<>[\]{}"'\`~!@#$%^&*_+\-=|\\/;,.:?]+/g, "");
  for (const f of INTENT_FILLERS) t = t.split(f).join("");
  return t;
}

/* 同义词别名表：[归一化后的说法, 命令 id]。id 与 palCommands() 的 push id 一致，
   命中后直接复用面板那套执行体，零新增后端。
   表里只放「自足的命令」——不放「打开 XX」这类动宾短语（那是 object 阶段的活）。 */
const INTENT_ALIASES = [
  ["声音小一点", "key:25"], ["声音调小一点", "key:25"], ["小声", "key:25"], ["声音小", "key:25"],
  ["音量减", "key:25"], ["音量低", "key:25"], ["轻一点", "key:25"], ["太吵了", "key:25"],
  ["吵死了", "key:25"],
  ["声音大一点", "key:24"], ["声音调大一点", "key:24"], ["大声", "key:24"], ["声音大", "key:24"],
  ["音量加", "key:24"], ["音量增", "key:24"], ["响一点", "key:24"], ["听不清", "key:24"],
  ["大声一点", "key:24"], ["小声一点", "key:25"],
  ["静音", "key:164"], ["消音", "key:164"], ["别吵", "key:164"], ["别吵了", "key:164"], ["关闭声音", "key:164"],
  ["暂停", "key:85"], ["停一下", "key:85"], ["先停", "key:85"], ["别放了", "key:85"],
  ["放着别动", "key:85"],
  ["继续", "key:85"], ["接着放", "key:85"], ["继续放", "key:85"], ["播放", "key:85"],
  ["下一集", "key:87"], ["跳过", "key:87"], ["换一集", "key:87"], ["看下一集", "key:87"],
  ["上一集", "key:88"], ["往回看", "key:88"], ["前一集", "key:88"],
  ["返回", "key:4"], ["回去", "key:4"], ["退出", "key:4"], ["关掉这个", "key:4"],
  ["主页", "key:3"], ["首页", "key:3"], ["桌面", "key:3"], ["回主页", "key:3"],
  ["菜单", "key:82"],
  ["关机", "key:26"], ["关电视", "key:26"], ["睡觉", "key:26"], ["休息了", "key:26"],
  ["唤醒", "key:224"], ["开机", "key:224"], ["打开电视", "key:224"],
  ["截图", "shot"], ["截个屏", "shot"], ["电视截屏", "shot"],
];

/* 动词表：长动词排前面，避免「打开」先把「开」吃掉剩下「电视」。 */
const INTENT_VERBS = ["我想看", "我要看", "请打开", "帮我打开", "打开", "开启", "开一下",
  "启动", "运行", "切换", "跳到", "去看", "播放", "开", "看"];
/* 取对象：动词在后面也认（「Netflix 打开」），此时取动词前面的部分。 */
function intentObject(norm) {
  for (const v of INTENT_VERBS) {
    const at = norm.indexOf(v);
    if (at < 0) continue;
    const rest = norm.slice(at + v.length);
    if (rest) return rest;
    return norm.slice(0, at);
  }
  return "";
}

/* 有界 Levenshtein：整行下界都超预算就提前退。返回实际距离；超预算返回 maxErrors + 1。 */
function intentEditErrors(a, b, maxErrors) {
  const m = a.length, n = b.length;
  if (m === 0) return 0;
  if (maxErrors < 0) return 1;
  let prev = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    const cur = new Array(n + 1);
    cur[0] = i;
    let rowMin = i;
    for (let j = 1; j <= n; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > maxErrors) return maxErrors + 1;
    prev = cur;
  }
  return prev[n];
}

/* 近似子串匹配（Bitap 的实质）：第 0 行全 0 表示「文本前缀可免费跳过」，
   最后一行取最小值 = 把 text 的某一段改成 pat 所需的最少编辑数。
   Fuse.js 里对应「匹配可以不在 location=0」——proximity 项就是干这个的。 */
function intentSubstrErrors(pat, text, maxErrors) {
  const m = pat.length, n = text.length;
  if (m === 0) return 0;
  if (maxErrors < 0) return 1;
  let prev = new Array(n + 1).fill(0);
  for (let i = 1; i <= m; i++) {
    const cur = new Array(n + 1);
    cur[0] = i;
    let rowMin = i;
    for (let j = 1; j <= n; j++) {
      const cost = pat.charCodeAt(i - 1) === text.charCodeAt(j - 1) ? 0 : 1;
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > maxErrors) return maxErrors + 1;
    prev = cur;
  }
  let best = prev[0];
  for (let j = 1; j <= n; j++) if (prev[j] < best) best = prev[j];
  return best;
}

/* Fuse.js 口径：允许的错误数 = floor(长度 * 阈值)。阈值取 0.34 而不是 Fuse 默认的
   0.6：两字符查询若容忍 1 错，一半的短标签都会命中，噪音太大。
   但中文两字词（静音 / 菜单 / 返回）错一个字仍是同一个词，且中文标签本身就只有两三个字，
   不容错等于「中文命令永远不能容错」。所以按是否含 ASCII 分流：纯中文给 1 的底线，
   拉丁查询不给——两字母容一个错等于什么都匹配（yt 会命中 youtube）。 */
const INTENT_ERR_RATE = 0.34;
function intentMaxErrors(s) {
  const len = String(s == null ? "" : s).length;
  if (len < 2) return 0;
  const floor = /[a-z0-9]/.test(String(s)) ? 0 : 1;
  return Math.max(floor, Math.floor(len * INTENT_ERR_RATE));
}

function intentRatio(q, s) {
  if (!q || !s) return 0;
  const max = Math.max(q.length, s.length);
  if (max === 0) return 1;
  return 1 - intentEditErrors(q, s, max) / max;
}

/* partial_ratio（rapidfuzz 同名）：短串在长串里任意位置对齐取最优。
   应用名埋在 terms 里（"打开 启动 app open launch netflix com.netflix..."），
   必须靠它才能把「打开 netflx」里的错字吃进去。 */
function intentPartialRatio(q, s) {
  if (!q || !s) return 0;
  const short = q.length <= s.length ? q : s;
  const long = q.length <= s.length ? s : q;
  if (short.length === 0) return 0;
  let best = 0;
  for (let i = 0; i + short.length <= long.length; i++) {
    const r = intentRatio(short, long.slice(i, i + short.length));
    if (r > best) { best = r; if (best >= 0.999) break; }
  }
  return best;
}

/* token_sort/token_set 的合并退化形态：按空白切 token 排序后整串比。
   intentNorm 已把空格删掉，这里的中文输入恒定退化成败于 intentRatio；
   保留它是为了中英混排（"netflix打开" vs terms 里的 "netflix"）时词序不影响命中。 */
function intentTokenRatio(q, s) {
  const tok = (x) => x.split(/\s+/).filter(Boolean).sort();
  return Math.max(intentRatio(tok(q).join(" "), tok(s).join(" ")), intentRatio(q, s));
}

function intentSim(q, s) {
  const a = intentRatio(q, s);
  if (a >= 0.999) return a;
  return Math.max(a, intentPartialRatio(q, s), intentTokenRatio(q, s));
}

/* 别名命中：整句相等，或别名是句子的后缀（「太吵了能不能静音」）。
   刻意不认「任意位置子串」——「打开电视机顶盒」里含「打开电视」，按子串匹配会触发
   开机键，而用户说的是装什么。后缀规则把这类前缀延伸全挡住。 */
function intentAliasHit(norm) {
  if (!norm) return "";
  for (const [phrase] of INTENT_ALIASES) if (norm === phrase) return phrase;
  let best = "";
  for (const [phrase] of INTENT_ALIASES) {
    if (norm.length > phrase.length && norm.endsWith(phrase)) {
      if (!best || phrase.length > best.length) best = phrase;   // 取最长后缀
    }
  }
  return best;
}

/* 别名的容错命中：说错一两个字也认（「音量城」约等于「音量减」）。
   只在句尾窗口里找：说错字的多半在结尾，且句尾比对不会误伤前缀短语。
   多个同样近、却指向不同命令的候选一律不猜；指向同一命令不算歧义。 */
function intentFuzzyAlias(norm) {
  if (!norm || norm.length < 2) return "";
  const cmdOf = (phrase) => {
    for (const [p, id] of INTENT_ALIASES) if (p === phrase) return id;
    return "";
  };
  let best = "", bestErr = 99, bestLen = 0, tie = false;
  for (const [phrase] of INTENT_ALIASES) {
    const budget = intentMaxErrors(phrase);
    if (budget < 1) continue;
    /* 三条收紧规则，都是被误判逼出来的： */
    // 1) 只跟「句尾等长那一小段」整串比，不做子串搜索。说错字的多半在结尾；
    //    子串搜索会让「打开电视」这种前缀短语在长句里被捞到。
    if (norm.length < phrase.length) continue;
    const tail = norm.slice(norm.length - phrase.length);
    // 2) 首字必须相同：「看电视」与「关电视」只差一个字，放开首字就会把看电视判成关机，
    //    「开机」与「关机」同理——中文命令的区分信息主要在首字。
    if (phrase.charAt(0) !== tail.charAt(0)) continue;
    const err = intentEditErrors(phrase, tail, budget);
    if (err > budget) continue;
    if (err < bestErr || (err === bestErr && phrase.length > bestLen)) {
      if (err === bestErr && phrase !== best && cmdOf(phrase) !== cmdOf(best)) tie = true;
      bestErr = err; best = phrase; bestLen = phrase.length; tie = false;
    } else if (err === bestErr && phrase !== best && cmdOf(phrase) !== cmdOf(best)) tie = true;
  }
  return tie ? "" : best;
}

/* 一条查询对一串候选（rapidfuzz process.extract 的最小形态）：返回最优命令与分数。
   只比 terms（应用名 / IP / 按键名都在这儿），不比 label——label 带「打开 / 按键」
   这类前缀，会把「电视」误判成「电视截屏」。 */
function intentPick(q, cmds, cutoff) {
  if (!q || !Array.isArray(cmds) || !cmds.length) return null;
  let best = null;
  for (const c of cmds) {
    const terms = String((c && c.terms) || "").toLowerCase();
    if (!terms) continue;
    const score = intentSim(q, terms);
    if (score < cutoff) continue;
    if (!best || score > best.score) best = { cmd: c, score: score };
  }
  return best;
}

/* 对象停用词：动词后面的通用名词不参与路由。没有这一条，「看电视」的对象
   「电视」会命中 conn 命令 terms 里的「连接电视」，把「我想看电视」变成连设备。 */
const INTENT_OBJ_STOP = ["电视", "电视机", "电视盒子", "机顶盒", "设备", "应用",
  "东西", "什么", "一下", "页面", "设置", "遥控器"];
const INTENT_OBJ_CUTOFF = 0.82;
const INTENT_WHOLE_CUTOFF = 0.9;

/* 对外入口：文本转命令。返回 null 表示「这不是命令，原样当文本下发」。
   via 记录命中路径，日志里能看出是别名还是模糊匹配，方便排查误判。
   顺序即优先级：整句/后缀别名 > 容错别名 > 动词+对象 > 整句直中。 */
function intentParse(text, cmds) {
  const norm = intentNorm(text);
  if (!norm) return null;
  const find = (id) => { for (const c of cmds || []) if (c.id === id) return c; return null; };
  const cmdOfAlias = (phrase) => {
    for (const [p, id] of INTENT_ALIASES) if (p === phrase) return find(id);
    return null;
  };

  const exact = intentAliasHit(norm);
  if (exact) {
    const c = cmdOfAlias(exact);
    if (c) return { cmd: c, via: "alias", said: exact };
  }
  const fuzzy = intentFuzzyAlias(norm);
  if (fuzzy) {
    const c = cmdOfAlias(fuzzy);
    if (c) return { cmd: c, via: "alias~", said: fuzzy };
  }

  const obj = intentObject(norm);
  /* 有动词就只走对象分支，不再做整句直中：「看电视」若允许整句匹配，会命中 conn 命令
     terms 里的「连接电视」，把一句闲聊变成连设备。没有动词的短句（「截图」）才直中。 */
  if (obj) {
    if (obj.length >= 2 && INTENT_OBJ_STOP.indexOf(obj) < 0) {
      const hit = intentPick(obj, cmds, INTENT_OBJ_CUTOFF);
      if (hit) return { cmd: hit.cmd, via: "object", score: Math.round(hit.score * 100) / 100 };
    }
    return null;
  }

  if (norm.length >= 2) {
    const hit = intentPick(norm, cmds, INTENT_WHOLE_CUTOFF);
    if (hit) return { cmd: hit.cmd, via: "whole", score: Math.round(hit.score * 100) / 100 };
  }
  return null;
}

/* 命令面板的第 5 档计分：前四档（词首 / 标签子串 / 别名词首 / 子序列）全落空时，
   按 Fuse.js 的「允许 errors 个错 + 匹配可不在开头」兜底。压在最末档，
   绝不抢精确命中的优先序——加了容错也不该打乱既有排序。 */
function palIntentScore(q, c) {
  if (q.length < 2) return 0;
  const label = String((c && c.label) || "").toLowerCase();
  const terms = String((c && c.terms) || "").toLowerCase();
  const budget = intentMaxErrors(q);
  if (budget < 1) return 0;
  const err = intentSubstrErrors(q, label, budget);
  if (err <= budget) return 100 - err * 10 - Math.min(50, label.length);
  const errT = intentSubstrErrors(q, terms, budget);
  if (errT <= budget) return 80 - errT * 10;
  return 0;
}
/* ===== intent:end ===== */
/* ===== palmark:begin ===== */
/* 命令面板：高亮「到底哪一段命中了」。
   学习源：rapidfuzz 3.14.3（MIT，© Max Bachmann）的 rapidfuzz.distance 模块。
   核实到的事实：它的相似度接口之外还有一组 *_alignment 接口，返回 ScoreAlignment——
   字段为 score / src_start / src_end / dest_start / dest_end，也就是**把最佳对齐区间
   在两条串上的位置一起返回**，而不是只给一个分数。这正是模糊匹配界面缺的东西：
   有了 tier 5 容错之后，「打开 YouTube」能匹配查询「yutube」，但用户满眼看去
   找不到 yutube 在哪——不知道这一行为什么冒出来。
   本段把同一思想落到 palScore 的五档上：**每一档都回答「命中在标签的哪一段」**。
   边界（刻意保守）：区间对不上就返回空数组——错的高亮比没有高亮更糟，
   用户会以为「高亮的地方就是我要找的」，反而更慢。 */
function palMarkRanges(label, q) {
  const s = String(label == null ? "" : label);
  const query = String(q == null ? "" : q).toLowerCase();
  if (!query || !s) return [];
  const low = s.toLowerCase();
  /* 第 1、2 档：词首 / 子串，整段高亮 */
  const at = low.indexOf(query);
  if (at >= 0) return [[at, at + query.length]];
  /* 两种候选都算出来再挑，挑法就一条：**覆盖更宽者胜；一样宽时取段数更少的**
     （一整段永远比零碎几段好认）。
     例子：netflx → Netflix，窗口 [0,7) 与子序列跨度都是 7，窗口只有一段，选窗口；
     ybe → YouTube，子序列跨 y…e 共 7 个字符而窗口只有 ube 3 个，选子序列——
     高亮 ube 会把没打过的 u 也算进去，反而是错的。
     预算为 0（拉丁短查询，如 "yt"）时没有窗口，直接走逐字高亮。 */
  const marks = [];
  let i = 0;
  for (let k = 0; k < s.length && i < query.length; k++) {
    if (low.charAt(k) === query.charAt(i)) { marks.push([k, k + 1]); i++; }
  }
  const seqRanges = i >= query.length ? palMarkMerge(marks) : [];
  let win = null;
  const budget = intentMaxErrors(query);
  if (budget >= 1) win = palMarkWindow(query, low, budget);
  if (!win) return seqRanges;
  if (!seqRanges.length) return [[win.start, win.end]];
  const seqSpan = seqRanges[seqRanges.length - 1][1] - seqRanges[0][0];
  const winSpan = win.end - win.start;
  if (winSpan > seqSpan) return [[win.start, win.end]];
  if (winSpan < seqSpan) return seqRanges;
  return seqRanges.length > 1 ? [[win.start, win.end]] : seqRanges;
}

/* 相邻/重叠的字元区间合并：子序列逐字高亮会产出一堆 [k,k+1]，合起来才像一句 */
function palMarkMerge(ranges) {
  const out = [];
  for (const r of ranges) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

/* 在 text 里找 pat 的近似窗口：窗口长度从 pat.length 放到 pat.length+budget，
   取错误最少、又最短的那个。窗口长度有上界， crossword 式乱撞不会发生。 */
function palMarkWindow(pat, text, budget) {
  const m = pat.length, n = text.length;
  if (!m || !n || budget < 1) return null;
  let best = null;
  for (let len = m; len <= m + budget && len <= n; len++) {
    for (let i = 0; i + len <= n; i++) {
      const err = intentEditErrors(pat, text.slice(i, i + len), budget);
      if (err > budget) continue;
      if (!best || err < best.err ||
          (err === best.err && (i < best.start ||
           (i === best.start && len > best.end - best.start)))) {
        best = { start: i, end: i + len, err: err };
      }
    }
  }
  return best;
}
/* ===== palmark:end ===== */

/* ===== favorder:begin ===== */
/* 收藏夹可调序：上移 / 下移。
   学习源：SortableJS 1.15.6（MIT）。核实到的事实：它的拖拽结束事件携带 oldIndex 与
   newIndex，库负责重排 DOM，**消费者的责任是把自己的数据重排成和 DOM 一致**。
   本项目取其契约（「移动 = 从 oldIndex 取出、插到 newIndex」），不取其交互形态：
   SortableJS 只提供拖拽，没有键盘通道；而这个项目的界面同时跑在手机触屏和
   Mac App（键盘可达）上，纯拖拽会把键盘用户挡在外面。所以这里是显式的 ↑/↓ 按钮，
   数组移动的语义照抄。 */
/* 把 list 里 from 位置的一项移到 to。返回新数组，**不入参原地改**——调用方要能拿到
   旧值做撤销（项目里所有可编辑内容都走 undoable 链路）。to 越界时钳到端点。 */
function favMove(list, from, to) {
  if (!Array.isArray(list)) return [];
  const n = list.length;
  if (from < 0 || from >= n) return list.slice();
  const dest = Math.max(0, Math.min(n - 1, to));
  if (dest === from) return list.slice();
  const out = list.slice();
  const item = out.splice(from, 1)[0];
  out.splice(dest, 0, item);
  return out;
}

/* 首行不能上移、末行不能下移——按钮要置灰，但不能消失（位置跳动比置灰更难用）。 */
function favCanMove(list, i, dir) {
  if (!Array.isArray(list) || i < 0 || i >= list.length) return false;
  if (dir === "up") return i > 0;
  if (dir === "down") return i < list.length - 1;
  return false;
}

/* 收藏夹的数据形态是 [{name, pkg}]，按 pkg 找下标——视图里只有 pkg 是稳定标识。 */
function favIndexOf(list, pkg) {
  if (!Array.isArray(list)) return -1;
  for (let i = 0; i < list.length; i++) if (list[i] && list[i].pkg === pkg) return i;
  return -1;
}
/* ===== favorder:end ===== */


let palRows = [], palSel = 0;   // palRows 含分组头；选中、执行都以行下标为准

const palRecent = () => {
  try { return JSON.parse(localStorage.getItem(PAL_KEY)) || []; } catch (e) { return []; }
};
const palRemember = (id) => {
  const left = palRecent().filter((x) => x !== id);
  left.unshift(id);
  localStorage.setItem(PAL_KEY, JSON.stringify(left.slice(0, 8)));
};

/* 匹配计分：前四档是 VS Code Command Palette 的行为约定（标签词首 > 标签内子串 >
   别名词首 > 散乱子序列，命中越靠前、标签越短分越高）；第五档是容错兜底，口径来自
   Fuse.js 的「允许 errors 个错」（见 intent 段），压在最末——精确匹配永远赢过模糊
   匹配，不会因为加了容错就打乱既有排序。返回 0 = 不匹配。 */
function palScore(q, c) {
  const label = c.label.toLowerCase();
  const terms = (c.terms || "").toLowerCase();
  const at = label.indexOf(q);
  if (at === 0) return 1000 - label.length;
  if (at > 0) return 800 - at * 10 - label.length;
  const ta = terms.indexOf(q);
  if (ta >= 0) return 600 - ta - Math.min(400, label.length);
  let i = 0;                                   // 子序列兜底："yt" → YouTube
  for (const ch of label) { if (ch === q[i]) i++; if (i >= q.length) break; }
  if (i >= q.length) return 200;
  return palIntentScore(q, c);
}

function palCommands() {
  const cmds = [];
  const push = (id, group, icon, label, terms, run, hint) =>
    cmds.push({ id, group, icon, label, terms, run, hint });
  // —— 设备 ——（来源是 palStatus 快照；还没状态时 gracefully 少几项）
  (palStatus?.recent || []).forEach((t) =>
    push("conn:" + t, "设备", "📺", `连接 ${t}`, "连接 连接电视 connect ip " + t, () => connect(t), t));
  (palStatus?.devices || []).forEach((d) =>
    push("sw:" + d.serial, "设备", "🔌", `切换设备 ${d.serial}`, "切换 switch device " + d.serial,
      async () => { await api("/api/switch", { target: d.serial }); refreshStatus(); }, d.serial));
  (palStatus?.appletv?.devices || []).forEach((d) =>
    push("atv:" + (d.id || d.name), "设备", "🍎", `连接 ${d.name || d.id}`,
      "连接 苹果 apple tv " + (d.name || d.id), () => atvConnect(d), "Apple TV"));
  push("scan:adb", "设备", "🔍", "扫描无线调试设备", "扫描 scan adb wireless", adbScan);
  push("scan:atv", "设备", "🔍", "扫描局域网 Apple TV", "扫描 scan apple tv", atvScan);
  push("tab:android", "设备", "🤖", "切到 Android TV 页签", "页签 tab android",
    () => $('.devtab[data-dev="android"]').click());
  push("tab:appletv", "设备", "🍎", "切到 Apple TV 页签", "页签 tab apple",
    () => $('.devtab[data-dev="appletv"]').click());
  if (palStatus?.current)
    push("disc", "设备", "⏏", "断开当前设备", "断开 disconnect", () => $("#disconnectBtn").click());
  // —— 应用 ——（预设 APPS + 最近使用；重开时最近置顶不去重，图标有区分）
  APPS.forEach((a) => push("app:" + a.pkg, "应用", "▶", `打开 ${a.name}`,
    "打开 启动 app open launch " + a.name + " " + a.pkg, () => launchApp(a.name, a.pkg), a.pkg));
  recentApps().forEach((a) => push("rapp:" + a.pkg, "应用", "🕘", `打开 ${a.name}（最近使用）`,
    "最近 recent " + a.name + " " + a.pkg, () => launchApp(a.name, a.pkg), "最近"));
  favResolve(favLoad(), APPS, recentApps()).forEach((a) => push("fav:" + a.pkg, "收藏", "★",
    `打开 ${a.name}（收藏）`, "收藏 固定 常用 favorite pin quick " + a.name + " " + a.pkg,
    () => launchApp(a.name, a.pkg), "收藏夹"));
  // —— 按键 ——（与页面上的物理键同一出口 sendKey，长按连发/音量 OSD 都带着）
  const PAL_KEYS = [[19, "上"], [20, "下"], [21, "左"], [22, "右"], [23, "确定"], [4, "返回"],
    [3, "主页"], [82, "菜单"], [26, "电源"], [224, "唤醒"], [24, "音量+"], [25, "音量-"], [164, "静音"],
    [85, "播放 / 暂停"], [87, "下一集"], [88, "上一集"], [86, "停止"]];
  PAL_KEYS.forEach(([code, name]) =>
    push("key:" + code, "按键", "⌨", `按键 ${name}`, "按键 key " + name + " " + code,
      () => sendKey(code), "keyevent " + code));
  // —— 宏 ——（预置缓存读 Macro.presets，自定义宏每次打开读 localStorage）
  [...(Macro.presets || []), ...customMacros()].forEach((m) =>
    push("macro:" + (m.id || m.name), "宏", "⚡", `运行宏「${m.name}」`,
      "宏 运行 一键 macro run " + m.name, () => runMacro(m), (m.steps || []).length + " 步"));
  // —— 工具 ——
  push("shot", "工具", "📸", "电视截屏", "截屏 截图 screenshot", () => $("#shotBtn").click());
  push("sleep:30", "工具", "🌙", "30 分钟后休眠电视", "睡眠 定时 休眠 sleep", () => $('[data-sleep="30"]').click());
  push("sleep:60", "工具", "🌙", "60 分钟后休眠电视", "睡眠 定时 休眠 sleep", () => $('[data-sleep="60"]').click());
  push("sleep:90", "工具", "🌙", "90 分钟后休眠电视", "睡眠 定时 休眠 sleep", () => $('[data-sleep="90"]').click());
  push("sleep:0", "工具", "⏹", "取消睡眠定时", "取消 睡眠 定时 cancel", () => $("#sleepCancelBtn").click());
  push("theme:dark", "工具", "🌙", "切换到深色主题", "主题 深色 暗色 dark theme", () => $('#themeSeg button[data-theme-val="dark"]').click());
  push("theme:light", "工具", "☀", "切换到浅色主题", "主题 浅色 亮色 light theme", () => $('#themeSeg button[data-theme-val="light"]').click());
  push("theme:auto", "工具", "🌓", "主题跟随系统", "主题 跟随 系统 auto", () => $('#themeSeg button[data-theme-val=""]').click());
  push("privacy", "工具", "👁", "切换隐私模式", "隐私 密码 privacy", () => $("#privacyBtn").click());
  push("km", "工具", "⌨", "按键自定义（长按任意键）", "按键 改键 绑定 自定义 keymap 长按",
    () => kmOpen(null), "长按物理键 " + KM_EDIT_MS + "ms");
  push("km:reset", "工具", "♻", "恢复所有默认按键", "恢复 默认 改键 重置 keymap reset",
    kmResetAll, Object.keys(kmMap).length + " 个已改");
 push("settings", "工具", "⚙", "打开设置", "设置 settings 偏好", () => $("#appSettingsBtn").click());
 push("notif", "工具", "🔔", "打开通知中心", "翻最近的出错 / 成功提示，可整段重看", () => notifToggle($("#notifBtn")));
  push("wol", "工具", "⚡", "远程开机（Wake-on-LAN）", "开机 唤醒 冷启动 wake wol 魔法包", () => {
    $("#wolCard").scrollIntoView({ behavior: "smooth", block: "center" });
    wolDiscover(wolIpsCache.length ? wolIpsCache : wolIpsFromStatus(wolLastStatus || {}));
  });
  push("coach", "工具", "🎓", "重看使用指引", "引导 教程 coach help", () => $("#coachBtn").click());
  return cmds;
}


/* ---- 命中高亮的 DOM 胶水：区间规则在 palmark 段，这里只负责拼元素 ----
   全程 createElement + textContent，不碰 innerHTML（AGENTS.md 的设备名/IP 禁令）。
   没查询或区间对不上时整行就是一个文本节点，与加上高亮之前完全一致。 */
let palQuery = "";
function palMarkSpan(label, query) {
  const span = document.createElement("span");
  span.className = "pallabel";
  const text = String(label == null ? "" : label);
  const ranges = query ? palMarkRanges(text, query) : [];
  if (!ranges.length) { span.textContent = text; return span; }
  let at = 0;
  for (const [s, e] of ranges) {
    if (s > at) span.appendChild(document.createTextNode(text.slice(at, s)));
    const mark = document.createElement("mark");
    mark.className = "palmark";
    mark.textContent = text.slice(s, e);
    span.appendChild(mark);
    at = e;
  }
  if (at < text.length) span.appendChild(document.createTextNode(text.slice(at)));
  return span;
}

function palRender(q) {
  const list = $("#palList");
  list.textContent = "";
  const query = (q || "").trim().toLowerCase();
  palQuery = query;   // palMarkSpan 要用：高亮跟着当前查询走
  let rows = [];
  if (query) {
    rows = palCommands()
      .map((c) => [c, palScore(query, c)])
      .filter(([, s]) => s > 0)
      .sort((a, b) => b[1] - a[1] || a[0].label.length - b[0].label.length)
      .slice(0, 80)
      .map(([c]) => ({ kind: "cmd", c }));
  } else {
    const all = palCommands();
    const rec = [], seen = new Set();
    for (const id of palRecent()) {
      const c = all.find((x) => x.id === id);
      if (c && !seen.has(c.id)) { seen.add(c.id); rec.push(c); }
    }
    if (rec.length) rows.push({ kind: "head", label: "最近使用" }, ...rec.map((c) => ({ kind: "cmd", c })));
    rows.push({ kind: "head", label: "全部命令" });
    for (const c of all) if (!seen.has(c.id)) rows.push({ kind: "cmd", c });
  }
  if (!rows.some((r) => r.kind === "cmd")) {
    const li = document.createElement("li");
    li.className = "palrow palempty";
    li.textContent = query ? `没有匹配「${query}」的命令` : "暂无可用的命令";
    list.appendChild(li);
    palRows = [];
    palSel = -1;
    $("#palTip").textContent = "0 条命令";
    $("#palInput").setAttribute("aria-activedescendant", "");
    return;
  }
  palRows = rows;
  palSel = rows.findIndex((r) => r.kind === "cmd");
  rows.forEach((r, i) => {
    const li = document.createElement("li");
    li.id = "palrow-" + i;
    if (r.kind === "head") {
      li.className = "palhead";
      li.setAttribute("role", "presentation");
      li.textContent = r.label;
    } else {
      const c = r.c;
      li.className = "palrow" + (i === palSel ? " on" : "");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === palSel ? "true" : "false");
      const ic = document.createElement("span");
      ic.className = "palico";
      ic.textContent = c.icon || "•";
      li.append(ic, palMarkSpan(c.label, palQuery));
      if (c.hint || c.group) {
        const hn = document.createElement("span");
        hn.className = "palhint";
        hn.textContent = c.hint || c.group;
        li.appendChild(hn);
      }
      li.addEventListener("mousemove", () => palSelect(i));
      li.addEventListener("click", () => palExec(i));
    }
    list.appendChild(li);
  });
  $("#palTip").textContent = rows.filter((r) => r.kind === "cmd").length + " 条命令";
  palSyncActive();
}

function palSelect(i) {
  if (i < 0 || i >= palRows.length || palRows[i].kind !== "cmd") return;
  palSel = i;
  palSyncActive();
}

function palSyncActive() {
  const rows = $("#palList").children;
  for (let i = 0; i < rows.length; i++) {
    const on = i === palSel;
    rows[i].classList.toggle("on", on);
    if (rows[i].classList.contains("palrow"))
      rows[i].setAttribute("aria-selected", on ? "true" : "false");
  }
  const cur = rows[palSel];
  $("#palInput").setAttribute("aria-activedescendant", cur ? cur.id : "");
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
}

function palMove(d) {   // 遇到分组头自动跳过
  if (!palRows.length) return;
  let i = palSel;
  for (let n = 0; n < palRows.length; n++) {
    i = (i + d + palRows.length) % palRows.length;
    if (palRows[i].kind === "cmd") { palSelect(i); return; }
  }
}

async function palExec(i) {
  const r = palRows[i];
  if (!r || r.kind !== "cmd") return;
  palRemember(r.c.id);
  palClose();      // 先收起再执行：命令可能又打开别的弹窗（设置/截屏），不能叠
  buzz();
  try { await r.c.run(); }
  catch (e) { log("⚠ " + e.message); }
}

function palOpen() {
  const inp = $("#palInput");
  inp.value = "";
  openModal("#cmdpal");
  refreshStatus();        // 打开时顺手刷新，设备/宏列表最多 8s 陈旧
  palRender("");
}
function palClose() { closeModal("#cmdpal"); }

$("#palBtn").addEventListener("click", palOpen);
$("#cmdpal").addEventListener("click", (e) => { if (e.target === $("#cmdpal")) palClose(); });
$("#palInput").addEventListener("input", () => palRender($("#palInput").value));
$("#palInput").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); palMove(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); palMove(-1); }
  else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); palExec(palSel); }
});
// 全局热键（capture：别的弹窗按键兜底 stopPropagation 后，同节点监听仍会执行）
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || (e.key !== "k" && e.key !== "K")) return;
  e.preventDefault();
  e.stopPropagation();
  if ($("#cmdpal").classList.contains("hidden")) palOpen();
  else palClose();
}, true);

// Ctrl/⌘+N 开关通知中心（与 Ctrl+K 命令面板同一套 capture 兜底写法）
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || (e.key !== "n" && e.key !== "N")) return;
  e.preventDefault();
  e.stopPropagation();
  notifToggle();
}, true);

/* ---------------- PWA：离线壳（Service Worker） ----------------
   策略细节见 static/sw.js 头部注释。边界：SW 只在安全上下文（https / localhost）
   注册——原生 App 的 WebView 与 https 访问可用；纯 http 局域网访问注册失败，
   静默跳过，遥控功能不受影响（渐进增强）。 */
(function registerSW() {
  if (!("serviceWorker" in navigator)) return;
  addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js", { scope: "/" })
      .then(() => {
        document.documentElement.dataset.sw = "on";
        // controller 为空 = 首次装上；后续刷新不重复播报
        if (!navigator.serviceWorker.controller) log("✅ 离线可用：遥控器界面已缓存，弱网/断网也能打开");
      })
      .catch(() => { document.documentElement.dataset.sw = "fail"; });
  });
})();

applyPrivacy();
renderPhrases();
renderHist();
notifInit();   // 通知队列：读历史 / 挂未读数角标（曾误放进隐私按钮回调：不点就不初始化，点了还重复挂监听）
refreshStatus();
// 首次使用：等首屏渲染稳定后自动开始引导（跳过/看完都会记住，不再自动弹）
// 按当前连接状态选段落：没连上教「怎么连」，已经连着直接教「怎么用」
setTimeout(() => {
  if (status.connected) maybePostCoach();
  else if (!coachSeen("pre")) startCoach("pre");
}, 600);
setInterval(() => { if (pageVisible) refreshStatus(); }, 8000);
// 手柄轮询（第三十六轮）：80ms 一拍，与 8s 状态轮询分开——连发节奏经不起 8s。
// 回调里只读不写，放在文件末尾保证执行到时 pageVisible 已初始化。
setInterval(gpTick, GP_TICK_MS);
