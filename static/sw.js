/* ATV Remote Service Worker —— 离线壳 + 静态资源缓存
   策略学习源：Workbox 7 的 stale-while-revalidate 与 app-shell 预缓存
   （npm 镜像取 workbox-strategies@7.4.1 源码核对过语义）。落到本项目：
   - install：预缓存 app shell（导航页 + 静态资源），装完即激活
   - 导航请求：network-first —— 尽快拿到新版本；断网才回落缓存的壳
   - 同源其他 GET：stale-while-revalidate —— 缓存秒开 + 后台更新，
     下次加载即为新版本；activate 时清旧缓存，不会「新 HTML + 旧 JS」幽灵
   - 只认 origin 相同的 GET；/api/* 一律放行（实时状态进缓存就是 bug）
   - 升级前端若要强制刷新离线副本：递增下面的 CACHE 版本号 */
const CACHE = "atv-shell-v1";
const SHELL = [
  "/",
  "/static/app.js",
  "/static/style.css",
  "/static/manifest.webmanifest",
  "/static/icon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-512.png",
  "/static/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // 逐条容忍失败：单个图标 404 不该让整个壳装不上
    await Promise.all(SHELL.map((url) =>
      cache.add(new Request(url, { cache: "reload" })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok) {
          const cache = await caches.open(CACHE);
          cache.put("/", fresh.clone());
        }
        return fresh;
      } catch (err) {
        const cache = await caches.open(CACHE);
        const shell = (await cache.match("/")) || (await cache.match("/index.html"));
        return shell || new Response("离线且无缓存", {
          status: 504,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req);
    const net = fetch(req)
      .then((res) => {
        if (res && res.ok && res.type !== "opaque") cache.put(req, res.clone());
        return res;
      })
      .catch(() => hit);
    return hit || net;
  })());
});
