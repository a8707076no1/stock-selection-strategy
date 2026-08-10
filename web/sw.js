// Service Worker — 離線快取最新的 index + 最近圖表
const CACHE = "stock-app-v5";
const CORE = ["/", "/manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  // 主頁 / 圖表 / summary 都 network-first（保證 iPhone Safari 拿最新）
  if (url.pathname === "/" || url.pathname.startsWith("/飆股圖表_") || url.pathname.startsWith("/summary_")) {
    e.respondWith(
      fetch(e.request, { cache: "no-store" }).then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
  }
});
