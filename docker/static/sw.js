// Service worker: makes the page installable and keeps it usable when the alarm server is down.
//
// With a reverse proxy in front, "server down" does not look like a network error: the proxy
// still answers, with 502/503/504. So any 5xx is treated the same as being offline, and only
// good responses are ever cached.
//
// - page, icons, manifest: network first, cached copy when offline
// - GET /api/alarms:      network first, last good copy when offline, marked with X-Offline: 1
//                          so the page can show it read-only
// - everything else under /api/ is never cached
const CACHE = "alarm-clock-v2";             // bumped: v1 could hold a cached 502 page
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/favicon-48.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// fetch that rejects on 5xx (proxy up, server down) as well as on network errors
async function fetchUp(request) {
  const res = await fetch(request);
  if (res.status >= 500) throw new Error("server down (" + res.status + ")");
  return res;
}

async function networkFirst(request, { cacheKey = request, offlineFallback, markOffline = false } = {}) {
  try {
    const res = await fetchUp(request);
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(cacheKey, copy));
    }
    return res;
  } catch (err) {
    const cached = await caches.match(cacheKey) || (offlineFallback && await caches.match(offlineFallback));
    if (!cached) {
      return new Response(JSON.stringify({ error: "Alarm server is offline" }), {
        status: 503, headers: { "Content-Type": "application/json", "X-Offline": "1" },
      });
    }
    if (!markOffline) return cached;
    const headers = new Headers(cached.headers);
    headers.set("X-Offline", "1");
    return new Response(await cached.blob(), { status: 200, headers });
  }
}

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  if (url.pathname === "/api/alarms") {
    // the clock's If-None-Match is not used here; cache under the bare URL
    e.respondWith(networkFirst(e.request, { cacheKey: "/api/alarms", markOffline: true }));
  } else if (url.pathname.startsWith("/api/")) {
    return;                                   // status etc: always live
  } else {
    e.respondWith(networkFirst(e.request, { offlineFallback: e.request.mode === "navigate" ? "/" : undefined }));
  }
});
