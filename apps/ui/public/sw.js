/*
 * DMS offline shell (IPHONE-PWA-01).
 *
 * Caches the app shell and its hashed build assets, nothing else. An answer is
 * never cached: every /api, /v1 and /health request goes to the network
 * untouched, so an offline phone shows the "DMS API unreachable" banner instead
 * of a stale figure. Bump SHELL to drop everything cached by an older build.
 */
const SHELL = "dms-shell-v1";

/*
 * Paths that are never intercepted: /api, /v1 and /health, anything under them, with or without a
 * trailing slash or query. Compared on the decoded, lower-cased path so /API/health and /v1%2Fspaces
 * cannot slip past a prefix test. /health also covers /healthz, as it always has. A path that does not
 * decode is left alone too: when in doubt, the network answers, never the cache.
 */
const NEVER = /^\/(?:(?:api|v1)(?:\/|$)|health)/;
function isNever(pathname) {
  try {
    let path = pathname;
    for (let i = 0; i < 3 && /%/.test(path); i++) path = decodeURIComponent(path);
    return NEVER.test(path.toLowerCase());
  } catch {
    return true;
  }
}

// Only these may be cached under an /assets/ URL. A gateway's HTML, or an answer with no or a
// generic type, must not become the script the app loads from cache first.
const ASSET_TYPE =
  /^(?:(?:text|application)\/(?:javascript|x-javascript|ecmascript|css|json|wasm)|font\/|application\/(?:font-|x-font-|vnd\.ms-fontobject)|image\/)/;
const isAsset = (res) => ASSET_TYPE.test((res.headers.get("content-type") || "").trim().toLowerCase());

// Hashed assets are keyed by path alone, so ?v=1, ?v=2 ... never make one entry each.
const assetKey = (url) => new Request(url.origin + url.pathname);

const isHtml = (res) => (res.headers.get("content-type") || "").includes("text/html");
const storable = (res) => res.ok && !res.redirected && isHtml(res);

/*
 * The app's own page carries the dms-shell marker (index.html) and names its built entry: a
 * module script under /assets/. A Wi-Fi sign-in or SSO page answered with 200 has neither, and
 * the entry alone is not enough: any Vite-built page has one. It must never become the shell
 * (the home-screen app would open to it on the next offline launch). This guards against a
 * gateway in the way, not against a page forged to look like the app.
 */
const isAppShell = (html) =>
  (html.match(/<meta\b[^>]*>/gi) || []).some(
    (tag) => /\bname=["']dms-shell["']/.test(tag) && /\bcontent=["']1["']/.test(tag),
  ) &&
  (html.match(/<script\b[^>]*>/gi) || []).some(
    (tag) => /\btype="module"/.test(tag) && /\bsrc="\/assets\/[^"]+\.js"/.test(tag),
  );

/*
 * Store the newest shell, then drop every cached asset it no longer references,
 * so the cache holds one build instead of growing with each deploy. A chunk the
 * shell does not name is simply fetched again when it is needed. A page that is
 * not the app changes nothing: the last good shell and its assets stay.
 */
async function refreshShell(res) {
  const html = await res.clone().text();
  if (!isAppShell(html)) return;
  const cache = await caches.open(SHELL);
  await cache.put("/", res);
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith("/assets/") && !html.includes(path)) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  // A failed precache must not discard the worker: the first navigation fills the shell.
  event.waitUntil(
    fetch("/")
      .then((res) => (storable(res) ? refreshShell(res) : undefined))
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
  if (isNever(url.pathname)) return;

  if (req.mode === "navigate") {
    // A path with an extension is a file (a download), not an app route: leave it to the network.
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return;
    // Network first, so a new deploy is picked up; the cached shell is the offline fallback.
    event.respondWith(
      fetch(req)
        .then((res) => {
          // A redirect (a login page, say) must never become the shell.
          if (storable(res)) event.waitUntil(refreshShell(res.clone()));
          return res;
        })
        .catch(() => caches.match("/").then((hit) => hit || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith("/assets/")) {
    // Vite content-hashes these names, so a cached copy is never stale.
    event.respondWith(
      caches.match(assetKey(url), { ignoreVary: true }).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            // The server answers a missing file with the app shell and 200, and a gateway
            // with its own page. Caching either under a script URL would break the app on
            // every later load, so only a response of an asset type is stored.
            if (res.ok && !res.redirected && isAsset(res)) {
              const copy = res.clone();
              event.waitUntil(caches.open(SHELL).then((cache) => cache.put(assetKey(url), copy)));
            }
            return res;
          }),
      ),
    );
  }
});
