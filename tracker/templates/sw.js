// Minimal, deliberately conservative service worker: only ever intercepts
// GET requests for /static/ assets (cache-first, revalidated in the
// background) so a repeat visit still has icons/CSS/JS if the network
// hiccups. Everything else - every page render, every HTMX partial swap,
// every POST - falls straight through untouched (this `return` with no
// event.respondWith() call is what tells the browser "handle this one
// yourself, normal network request"). Spool's data changes constantly and
// is served entirely server-rendered, so caching page/API responses here
// would risk showing stale watch history/lists instead of speeding
// anything up WhiteNoise's own immutable, hash-named asset URLs don't
// already cover client-side.
const CACHE_NAME = "spool-static-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith("/static/")) {
    return;
  }
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(event.request).then((cached) => {
        const network = fetch(event.request)
          .then((response) => {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    )
  );
});
