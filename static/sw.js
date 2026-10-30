// fMily Cloud service worker: lets the app install and open its shell offline.
// The network always wins when it answers, so a new version shows up on the next load.
// Nothing under /api/ (files, thumbnails, uploads) is ever cached here.
const CACHE = "family-cloud-shell-v2";
const SHELL = ["/", "/style.css", "/app.js", "/manifest.webmanifest", "/icon.svg", "/icon-180.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(event.request, copy)); }
    return res;
  }).catch(() => caches.match(event.request)));
});
