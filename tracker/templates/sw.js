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

// Web push (tracker/web_push.py sends, Settings > Notifications turns it on).
// Every push MUST end in showNotification - the subscription is made with
// userVisibleOnly, and a push that shows nothing gets the subscription
// revoked by the browser. The payload is JSON {title, body, url, icon, tag};
// url is always a same-site path (the server only sends one).
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Spool", {
      body: data.body || "",
      icon: data.icon || undefined,
      tag: data.tag || undefined,
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = (event.notification.data && event.notification.data.url) || "/";
  const target = new URL(path, self.location.origin);
  if (target.origin !== self.location.origin) return;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === target.origin && "focus" in client) {
          return client.focus().then((focused) => ("navigate" in focused ? focused.navigate(target.href) : focused));
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});
