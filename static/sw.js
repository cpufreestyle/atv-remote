/* ATV Remote Service Worker —— 离线壳 + 静态资源缓存
   策略学习源：Workbox 7 的 stale-while-revalidate 与 app-shell 预缓存
   （npm 镜像取 workbox-strategies@7.4.1 源码核对过语义）。落到本项目：
   - install：预缓存 app shell（导航页 + 静态资源），装完即激活
   - 导航请求：network-first —— 尽快拿到新版本；断网才回落缓存的壳
   - 同源其他 GET：stale-while-revalidate —— 缓存秒开 + 后台更新，
     下次加载即为新版本；activate 时清旧缓存，不会「新 HTML + 旧 JS」幽灵
   - 只认 origin 相同的 GET；/api/* 一律放行（实时状态进缓存就是 bug）
   - 升级前端若要强制刷新离线副本：递增下面的 CACHE 版本号 */
// v5：修「点了下载无法下载」——下载类请求（*.apk 等）不再进 SW 缓存分支，
// 且缓存写入失败不再吞掉网络响应。版本号必须递增，否则老客户端还跑旧逻辑。
// v6：布局紧凑化（style.css 大面积调间距）+ Apple TV 页签即时渲染；
// 不改版本号的话，装过桌面的老客户端会一直用 stale-while-revalidate 的旧外壳。
// v7：图标重做（屏内加品牌蓝信号弧）——icon.svg 与四张 PNG 都在预缓存清单里，
// 不递增的话老客户端的 favicon / 桌面图标还是旧图。
const CACHE = "atv-shell-v7";
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
  // 大文件（APK 包）不要经手：Cache Storage 写 20MB 会触发配额失败，
  // 而下面那条 .catch(() => hit) 会把「缓存写失败」和「网络失败」混为一谈 ——
  // 网络明明是好的，却因为没缓存命中而 respondWith(undefined)，浏览器就报「无法下载」。
  // 下载类请求直接放行给网络，别进 SW 的缓存逻辑。
  if (req.mode === "no-cors" || /\.(apk|tgz|zip)$/i.test(url.pathname)) return;

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
    // 先起网络请求；缓存写入是「顺手做的事」，绝不能因为写缓存失败而丢掉响应
    const net = fetch(req).then((res) => {
      if (res && res.ok && res.type !== "opaque") {
        // 不 await、也不让它影响返回值：写缓存失败（配额、大文件）只丢缓存，
        // 不能丢响应
        cache.put(req, res.clone()).catch(() => {});
      }
      return res;
    });
    if (hit) {
      // stale-while-revalidate：有缓存先返缓存，网络请求继续跑（失败也无所谓，
      // 上面已经 catch 掉，不会变成 unhandled rejection）
      net.catch(() => {});
      return hit;
    }
    return net;
  })());
});
