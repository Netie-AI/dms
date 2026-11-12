/*
 * DMS offline shell (IPHONE-PWA-01).
 *
 * Caches the app shell and its hashed build assets, nothing else. An answer is
 * never cached: every /api, /v1 and /health request goes to the network
 * untouched, so an offline phone shows the "DMS API unreachable" banner instead
 * of a stale figure. Bump SHELL to drop everything cached by an older build.
 */
const SHELL = "dms-shell-v1";
const NEVER = ["/api/", "/v1/", "/health"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.add("/")));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (NEVER.some((prefix) => url.pathname.startsWith(prefix))) return;

  if (req.mode === "navigate") {
    // Network first, so a new deploy is picked up; the cached shell is the offline fallback.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && (res.headers.get("content-type") || "").includes("text/html")) {
            const copy = res.clone();
            caches.open(SHELL).then((cache) => cache.put("/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/").then((hit) => hit || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith("/assets/")) {
    // Vite content-hashes these names, so a cached copy is never stale.
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(SHELL).then((cache) => cache.put(req, copy));
            }
            return res;
          }),
      ),
    );
  }
});
