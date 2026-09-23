// Minimal service worker: enough to make the app installable (and therefore
// a share target), with a cache of the shell so a check still opens on a
// train.  Nothing about a verdict is cached -- those must always be fresh.
const SHELL = "radar-shell-v1";
const FILES = ["/", "/static/manifest.json", "/static/icons/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // Never serve an API answer from cache.
  if (["/check", "/stats", "/review", "/leaderboard", "/recent"].some(p => url.pathname.startsWith(p))) return;
  // The page itself: network first, cache as the fallback.
  if (url.pathname === "/" || url.pathname === "/share") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
