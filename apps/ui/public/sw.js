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

const isHtml = (res) => (res.headers.get("content-type") || "").includes("text/html");

/*
 * Store the newest shell, then drop every cached asset it no longer references,
 * so the cache holds one build instead of growing with each deploy. A chunk the
 * shell does not name is simply fetched again when it is needed.
 */
async function refreshShell(res) {
  const cache = await caches.open(SHELL);
  const html = await res.clone().text();
  await cache.put("/", res);
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith("/assets/") && !html.includes(path)) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  // A failed precache must not discard the worker: the first navigation fills the shell.
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.add("/"))
      .catch(() => undefined),
  );
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
    // A path with an extension is a file (a download), not an app route: leave it to the network.
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return;
    // Network first, so a new deploy is picked up; the cached shell is the offline fallback.
    event.respondWith(
      fetch(req)
        .then((res) => {
          // A redirect (a login page, say) must never become the shell.
          if (res.ok && !res.redirected && isHtml(res)) event.waitUntil(refreshShell(res.clone()));
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
            // The server answers a missing file with the app shell and 200. Caching
            // that HTML under a script URL would break the app on every later load.
            if (res.ok && !res.redirected && !isHtml(res)) {
              const copy = res.clone();
              event.waitUntil(caches.open(SHELL).then((cache) => cache.put(req, copy)));
            }
            return res;
          }),
      ),
    );
  }
});
