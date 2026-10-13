// Service worker : l'application s'ouvre hors ligne avec les dernières données consultées.
const CACHE = "suivi-taux-v2";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/3dt-logo.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // réseau d'abord (données et page toujours fraîches), cache en secours hors ligne
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone();
      const key = url.pathname.startsWith("/data/") ? url.pathname : e.request;
      caches.open(CACHE).then(c => c.put(key, copy));
      return r;
    }).catch(() => caches.match(url.pathname.startsWith("/data/") ? url.pathname : e.request).then(m => m || caches.match("/index.html")))
  );
});
