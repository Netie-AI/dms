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
 * trailing slash, query or ;parameter. The path is reduced to what a server would route before it is
 * tested: percent-decoded until it stops changing, backslashes read as slashes, repeated slashes
 * collapsed, "." and ".." segments resolved, lower-cased. /health also covers /healthz, as it always
 * has; /apiary does not match. A path that does not decode, or that needs 10 decodes or more to stop
 * changing, is never intercepted either: when in doubt, the network answers, never the cache.
 */
const NEVER = /^\/(?:(?:api|v1)(?![a-z0-9_-])|health)/;
function isNever(pathname) {
  try {
    let path = pathname;
    for (let round = 0; ; round++) {
      if (round >= 10) return true;
      const next = decodeURIComponent(path);
      if (next === path) break;
      path = next;
    }
    const kept = [];
    for (const part of path.replace(/\\/g, "/").split("/")) {
      if (part === "..") kept.pop();
      else if (part !== "" && part !== ".") kept.push(part);
    }
    return NEVER.test("/" + kept.join("/").toLowerCase());
  } catch {
    return true;
  }
}

// Only these may be cached under an /assets/ URL, matched exactly on the media type. A gateway's HTML,
// or an answer with no, a generic or a lookalike type, must not become the script the app loads from
// cache first. JSON is not here: an answer cached under a normalised URL would be a stale figure.
const ASSET_TYPES = new Set([
  "text/javascript",
  "application/javascript",
  "application/x-javascript",
  "text/ecmascript",
  "application/ecmascript",
  "text/css",
  "application/wasm",
  "font/woff",
  "font/woff2",
  "font/ttf",
  "font/otf",
  "font/sfnt",
  "application/font-woff",
  "application/font-woff2",
  "application/vnd.ms-fontobject",
  "application/x-font-ttf",
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "image/avif",
  "image/svg+xml",
  "image/x-icon",
  "image/vnd.microsoft.icon",
]);
function isAsset(res) {
  const raw = res.headers.get("content-type") || "";
  // A comma makes it a list of values, not a media type.
  if (raw.includes(",")) return false;
  return ASSET_TYPES.has(raw.split(";")[0].trim().toLowerCase());
}

// Hashed assets are keyed by path alone, so ?v=1, ?v=2 ... never make one entry each.
const assetKey = (url) => new Request(url.origin + url.pathname);

const isHtml = (res) => (res.headers.get("content-type") || "").includes("text/html");
const storable = (res) => res.ok && !res.redirected && isHtml(res);

/*
 * The app's own page carries the dms-shell marker (index.html) and names its built entry: a
 * module script under /assets/. A Wi-Fi sign-in or SSO page answered with 200 has neither, and
 * the entry alone is not enough: any Vite-built page has one. It must never become the shell
 * (the home-screen app would open to it on the next offline launch).
 *
 * What this defends: a gateway or captive portal that answers with its own page, which has no
 * reason to contain the marker, or to contain it only as inert text (a comment, a script string,
 * a CDATA block, a textarea or style body). The scan is one linear pass over the markup, skips
 * those, matches attribute names and values exactly (data-name is not name), and refuses an entry
 * src with ".." or "%" in it.
 *
 * What it does not defend: a page deliberately forged to look like the app. Markup this scan does
 * not model (title, noscript, template, xmp, plaintext, svg, attribute values, duplicate
 * attributes, nomodule) can still carry a marker that a browser would not treat as one. Nobody
 * between the phone and the server is assumed to be writing the app's own HTML.
 */
const MAX_SHELL_CHARS = 64 * 1024; // the real shell is about 1.3KB; anything bigger is not it
const TAG = /<(meta|script|textarea|style)(?![A-Za-z0-9_])/iy;
const CLOSER = {
  script: /<\/script\s*>/gi,
  textarea: /<\/textarea\s*>/gi,
  style: /<\/style\s*>/gi,
};

function isAppShell(page) {
  if (page.length > MAX_SHELL_CHARS) return false;
  let marker = false;
  let entry = false;
  let at = 0;
  // Every step moves forward over text it has not seen, so the cost is linear in the page.
  while ((at = page.indexOf("<", at)) !== -1) {
    if (page.startsWith("<!--", at) || page.startsWith("<![CDATA[", at)) {
      const comment = page.startsWith("<!--", at);
      const end = page.indexOf(comment ? "-->" : "]]>", at + 4);
      if (end === -1) break; // unclosed: the rest of the page is inert
      at = end + 3;
      continue;
    }
    TAG.lastIndex = at;
    const open = TAG.exec(page);
    const close = open ? page.indexOf(">", at + open[0].length) : -1;
    if (!open || close === -1) {
      if (open) break; // an unfinished tag
      at += 1;
      continue;
    }
    const tag = page.slice(at, close + 1);
    const name = open[1].toLowerCase();
    at = close + 1;
    if (name === "meta") {
      if (/[\s"']name=["']dms-shell["']/.test(tag) && /[\s"']content=["']1["']/.test(tag)) marker = true;
      continue;
    }
    if (name === "script") {
      const src = /[\s"']src=["'](\/assets\/[^"']+\.js)["']/.exec(tag);
      if (
        /[\s"']type=["']module["']/.test(tag) &&
        src !== null &&
        !src[1].includes("..") &&
        !src[1].includes("%")
      ) {
        entry = true;
      }
    }
    // The body of script, textarea and style is text, not markup.
    CLOSER[name].lastIndex = at;
    const closer = CLOSER[name].exec(page);
    if (closer === null) break; // unclosed: the rest of the page is inert
    at = closer.index + closer[0].length;
  }
  return marker && entry;
}

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
      caches.match(assetKey(url)).then(
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
