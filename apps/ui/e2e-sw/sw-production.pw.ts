import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

/**
 * public/sw.js against the production build. Every assertion is on what a phone
 * user gets: which URLs end up in the cache, and what an offline phone is served.
 * The preview server answers any unknown path with the HTML shell and 200, like
 * the real deployment, which is the condition behind the original cache bug.
 */
test.use({ viewport: { width: 390, height: 844 }, serviceWorkers: "allow" });

// The cache name comes from the worker itself, so bumping SHELL (as sw.js tells you to) breaks nothing here.
const SW_SRC = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");
const SHELL = /const SHELL = "([^"]+)"/.exec(SW_SRC)![1];

type Entry = { cache: string; path: string };

async function entries(page: Page): Promise<Entry[]> {
  return page.evaluate(async () => {
    const out: { cache: string; path: string }[] = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const req of await cache.keys()) out.push({ cache: name, path: new URL(req.url).pathname });
    }
    return out;
  });
}

const paths = async (page: Page) => (await entries(page)).map((e) => e.path);

/** Load the app and wait until a worker is active, controlling the page, and the shell is stored. */
async function boot(page: Page) {
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL ?? null))
    .not.toBeNull();
  await expect.poll(() => paths(page)).toContain("/");
}

/** Reload under control so the SW sees and stores the hashed JS and CSS. */
async function bootWithAssets(page: Page) {
  await boot(page);
  await page.reload();
  await expect
    .poll(async () => {
      const p = await paths(page);
      return [p.some((x) => /^\/assets\/.+\.js$/.test(x)), p.some((x) => /^\/assets\/.+\.css$/.test(x))];
    })
    .toEqual([true, true]);
}

test("1 registration: page is controlled by /sw.js and the shell is cached", async ({ page }) => {
  await boot(page);
  const script = await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL);
  expect(script).toMatch(/\/sw\.js$/);
  const all = await entries(page);
  expect(all.filter((e) => e.path === "/").map((e) => e.cache)).toEqual([SHELL]);
  expect(await page.evaluate(() => caches.keys())).toEqual([SHELL]);
});

test("2 a missing asset (server answers 200 HTML) is never cached", async ({ page, context }) => {
  await boot(page);
  let hits = 0;
  await context.route("**/assets/does-not-exist-123.js", (route) => {
    hits += 1;
    return route.continue();
  });
  const fetchIt = () =>
    page.evaluate(async () => {
      const r = await fetch("/assets/does-not-exist-123.js");
      return { status: r.status, type: r.headers.get("content-type") ?? "" };
    });
  const first = await fetchIt();
  expect(first.status).toBe(200);
  expect(first.type).toContain("text/html");
  await expect.poll(() => hits).toBe(1);
  // Give a wrongly started cache write time to land, then look.
  await page.waitForTimeout(300);
  expect(await paths(page)).not.toContain("/assets/does-not-exist-123.js");
  await fetchIt();
  await expect.poll(() => hits).toBe(2);
});

test("3 offline reload renders the app shell from cached shell, JS and CSS", async ({ page, context }) => {
  await bootWithAssets(page);
  const cached = await paths(page);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("tab-bar")).toBeVisible();
  // The stylesheet applied: the body is not the browser default white-on-serif page.
  const loaded = await page.evaluate(() => ({
    sheets: document.styleSheets.length,
    js: [...document.scripts].map((s) => new URL(s.src, location.href).pathname).filter((p) => p.startsWith("/assets/")),
  }));
  expect(loaded.sheets).toBeGreaterThan(0);
  expect(loaded.js.length).toBeGreaterThan(0);
  for (const p of loaded.js) expect(cached).toContain(p);
});

test("4 offline, a file URL that was never cached is a network error, not the shell", async ({ page, context }) => {
  await bootWithAssets(page);
  expect(await paths(page)).not.toContain("/manifest.json");
  expect(await paths(page)).not.toContain("/icon-192.png");
  await context.setOffline(true);
  for (const file of ["/manifest.json", "/icon-192.png"]) {
    await expect(page.goto(file)).rejects.toThrow(/ERR_INTERNET_DISCONNECTED|net::ERR/);
  }
  // Offline, "/" is served by the worker from the cached shell: a stable http page to run the fetch in
  // (the failed file navigation above can leave Chromium committing an error page late).
  await expect(async () => {
    await page.goto("/");
    await expect(page.getByTestId("tab-bar")).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 15_000 });
  const viaFetch = await page.evaluate(() =>
    fetch("/manifest.json").then(
      () => "served",
      (e) => (e instanceof TypeError ? "network-error" : String(e)),
    ),
  );
  expect(viaFetch).toBe("network-error");
});

test("5 no API response is cached and offline API navigations are network errors", async ({ page, context }) => {
  await bootWithAssets(page);
  // The preview server proxies these paths to an API that is not running here, and
  // answers 500, which makes a navigation throw. Answer them ourselves so the test
  // needs no stack: the worker must still leave the response alone and never cache it.
  const apiPaths = /\/(api|v1)\/|\/health$/;
  await context.route(apiPaths, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
  );
  await page.evaluate(async () => {
    await fetch("/api/health").catch(() => undefined);
    await fetch("/health").catch(() => undefined);
    await fetch("/v1/chat/ask", { method: "POST", body: "{}" }).catch(() => undefined);
    await fetch("/v1/spaces").catch(() => undefined);
  });
  // Online navigations to these paths too (network first, then the shell is refreshed).
  for (const p of ["/api/health", "/health", "/v1/spaces"]) await page.goto(p);
  await page.goto("/");
  await page.waitForTimeout(300);
  const bad = (await paths(page)).filter((p) => p.startsWith("/api/") || p.startsWith("/v1/") || p === "/health");
  expect(bad).toEqual([]);
  // Offline, the worker must not stand in for an answer with the cached shell.
  // Remove the stub first: a route handler would answer even with the network off.
  await context.unroute(apiPaths);
  await context.setOffline(true);
  for (const p of ["/api/health", "/health", "/v1/spaces"]) {
    await expect(page.goto(p), p).rejects.toThrow(/net::ERR/);
  }
});

test("6 a stale /assets entry is pruned when a fresh shell is stored; current assets stay", async ({ page }) => {
  await bootWithAssets(page);
  const current = (await paths(page)).filter((p) => p.startsWith("/assets/"));
  expect(current.length).toBeGreaterThanOrEqual(2);
  await page.evaluate(async (shell) => {
    const cache = await caches.open(shell);
    await cache.put("/assets/old-abc.js", new Response("//old", { headers: { "content-type": "text/javascript" } }));
  }, SHELL);
  expect(await paths(page)).toContain("/assets/old-abc.js");
  await page.goto("/");
  await expect.poll(() => paths(page)).not.toContain("/assets/old-abc.js");
  const after = await paths(page);
  for (const p of current) expect(after).toContain(p);
  expect(after).toContain("/");
});

test("7 a non-ok, non-HTML or redirected response is never stored", async ({ page, context }) => {
  await bootWithAssets(page);
  const SENTINEL = "SENTINEL-SHELL";
  const seed = () =>
    page.evaluate(
      async ([shell, s]) => {
        const cache = await caches.open(shell);
        await cache.put("/", new Response(s, { headers: { "content-type": "text/html" } }));
      },
      [SHELL, SENTINEL],
    );
  const shellBody = () =>
    page.evaluate(async (shell) => (await (await caches.open(shell)).match("/"))?.text() ?? null, SHELL);
  const html = "<!doctype html><html><body>other page /assets/index.js</body></html>";

  await context.route("**/nav-404", (r) => r.fulfill({ status: 404, contentType: "text/html", body: html }));
  await context.route("**/nav-json", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: '{"ok":true}' }),
  );

  await seed();
  await page.goto("/nav-404");
  await page.waitForTimeout(400);
  expect(await shellBody(), "404 HTML stored as the shell").toBe(SENTINEL);

  await page.goto("/nav-json");
  await page.waitForTimeout(400);
  expect(await shellBody(), "JSON stored as the shell").toBe(SENTINEL);

  // A redirected asset response (followed by fetch) must not be stored either.
  const real = (await paths(page)).find((p) => /^\/assets\/.+\.js$/.test(p))!;
  await context.route("**/assets/redirected-1.js", (r) =>
    r.fulfill({ status: 302, headers: { location: real.replace(/^\//, "/") + "?r=1" } }),
  );
  const status = await page.evaluate(() => fetch("/assets/redirected-1.js").then((r) => r.status));
  expect(status).toBe(200);
  await page.waitForTimeout(400);
  expect(await paths(page)).not.toContain("/assets/redirected-1.js");

  // A non-ok asset response is not stored.
  await context.route("**/assets/gone-1.js", (r) =>
    r.fulfill({ status: 404, contentType: "text/javascript", body: "//nope" }),
  );
  await page.evaluate(() => fetch("/assets/gone-1.js"));
  await page.waitForTimeout(400);
  expect(await paths(page)).not.toContain("/assets/gone-1.js");
});

test("8 a cache left by an older build is dropped when a worker activates", async ({ page }) => {
  await boot(page);
  await page.evaluate(async () => {
    const old = await caches.open("dms-legacy-cache");
    await old.put("/assets/ancient.js", new Response("//ancient", { headers: { "content-type": "text/javascript" } }));
    await (await navigator.serviceWorker.getRegistration())?.unregister();
  });
  expect(await page.evaluate(() => caches.keys())).toContain("dms-legacy-cache");
  // A fresh registration installs and activates again, as after a deploy that bumps SHELL.
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => caches.keys())).toEqual([SHELL]);
  expect(await paths(page)).not.toContain("/assets/ancient.js");
});

/*
 * IPHONE-SHELL-01: only the app may become the offline shell. Hotel Wi-Fi and SSO gateways answer
 * any URL with their own page and 200 (no redirect a fetch can see). Stored as the shell, that page
 * is what the home-screen app opens to the next time the phone is offline.
 */
const PORTAL_TITLE = "Sign in to Hotel Wi-Fi";
const portal = (extra = "") =>
  `<!doctype html><html><head><title>Wi-Fi</title>${extra}</head><body><h1>${PORTAL_TITLE}</h1><form><input name="room" /></form></body></html>`;
const shellBody = (page: Page) =>
  page.evaluate(async (shell) => (await (await caches.open(shell)).match("/"))?.text() ?? null, SHELL);
const assetPaths = async (page: Page) => (await paths(page)).filter((p) => p.startsWith("/assets/")).sort();

/** Decoys: pages that mention /assets/ without being the app (its entry is a module script under /assets/). */
const PORTALS: Record<string, string> = {
  "a plain sign-in page": portal(),
  "a sign-in page with its own /assets/ image, stylesheet and classic script": portal(
    '<link rel="stylesheet" href="/assets/portal.css"><script src="/assets/portal.js"></script><script type="module" src="/portal/app.js"></script>',
  ).replace("<h1>", '<img src="/assets/logo.png" alt=""><p>see /assets/index.js</p><h1>'),
};

for (const [name, body] of Object.entries(PORTALS)) {
  for (const route of ["/", "/library"]) {
    test(`9 ${name} answered with 200 at ${route} never becomes the offline shell`, async ({ page, context }) => {
      await bootWithAssets(page);
      const goodShell = await shellBody(page);
      expect(goodShell).toContain('src="/assets/');
      const goodAssets = await assetPaths(page);

      const isRoute = (url: URL) => url.pathname === route;
      await context.route(isRoute, (r) => r.fulfill({ status: 200, contentType: "text/html", body }));
      await page.goto(route);
      // Online, the person sees the gateway's page: the worker does not hide it.
      await expect(page.getByRole("heading", { name: PORTAL_TITLE })).toBeVisible();
      // Give a wrongly started cache write time to land.
      await page.waitForTimeout(400);

      // The next offline launch opens the app, not the sign-in page.
      await context.unroute(isRoute);
      await context.setOffline(true);
      await page.goto(route);
      await expect(page.getByText(PORTAL_TITLE), "the offline launch shows the sign-in page").toHaveCount(0);
      await expect(page.locator('input[name="room"]')).toHaveCount(0);
      await expect(page.getByTestId("tab-bar")).toBeVisible();
      expect(await shellBody(page), "the stored shell changed").toBe(goodShell);
      expect(await assetPaths(page), "the app's assets were pruned").toEqual(goodAssets);
    });
  }
}

test("10 a new deploy of the app still replaces the shell (the guard does not freeze updates)", async ({
  page,
  context,
}) => {
  await bootWithAssets(page);
  const old = (await shellBody(page))!;
  const next = old.replace("</body>", "<!--deploy-2--></body>");
  expect(next).not.toBe(old);
  await context.route(
    (url) => url.pathname === "/",
    (r) => r.fulfill({ status: 200, contentType: "text/html", body: next }),
  );
  await page.goto("/");
  await expect.poll(() => shellBody(page)).toBe(next);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("tab-bar")).toBeVisible();
});

test("11 a sign-in page answered to the worker's own install fetch is not precached as the shell", async ({
  page,
  context,
}) => {
  let fromWorker = 0;
  const isRoot = (url: URL) => url.pathname === "/";
  await context.route(isRoot, (r) => {
    if (!r.request().serviceWorker()) return r.continue();
    fromWorker += 1;
    return r.fulfill({ status: 200, contentType: "text/html", body: portal() });
  });
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL ?? null))
    .not.toBeNull();
  // The install fetch really was answered with the sign-in page (otherwise this test proves nothing).
  await expect.poll(() => fromWorker).toBeGreaterThanOrEqual(1);
  await page.waitForTimeout(400);
  expect((await shellBody(page)) ?? "", "the sign-in page was precached").not.toContain(PORTAL_TITLE);

  // The first real navigation fills the shell, and the app then opens offline.
  await context.unroute(isRoot);
  await page.goto("/");
  await expect.poll(async () => (await shellBody(page)) ?? "").toContain('src="/assets/');
  await page.reload();
  await expect.poll(async () => (await assetPaths(page)).some((p) => p.endsWith(".js"))).toBe(true);
  await expect.poll(async () => (await assetPaths(page)).some((p) => p.endsWith(".css"))).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("tab-bar")).toBeVisible();
  await expect(page.getByText(PORTAL_TITLE)).toHaveCount(0);
});

/*
 * IPHONE-SHELL-02: hardening found by an independent attack round. Each test states what the person
 * sees or gets, not how the worker decides it.
 */
const MARKER = '<meta name="dms-shell" content="1" />';
const goodShell = (page: Page) => page.evaluate(async (s) => (await (await caches.open(s)).match("/"))?.text() ?? null, SHELL);

/** Serve `body` at "/" online, then go offline: the next launch must still open the app, with its assets. */
async function expectPageNeverBecomesShell(page: Page, context: import("@playwright/test").BrowserContext, body: string) {
  await bootWithAssets(page);
  const good = await goodShell(page);
  const goodAssets = await assetPaths(page);
  expect(goodAssets.length).toBeGreaterThanOrEqual(2);
  const isRoot = (url: URL) => url.pathname === "/";
  await context.route(isRoot, (r) => r.fulfill({ status: 200, contentType: "text/html", body }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: PORTAL_TITLE })).toBeVisible();
  await page.waitForTimeout(400);
  await context.unroute(isRoot);
  await context.setOffline(true);
  await page.goto("/");
  await expect(page.getByTestId("tab-bar")).toBeVisible();
  await expect(page.getByText(PORTAL_TITLE)).toHaveCount(0);
  expect(await goodShell(page), "the stored shell changed").toBe(good);
  expect(await assetPaths(page), "the app's assets were pruned").toEqual(goodAssets);
}

test("12 a Vite-built sign-in page (module script under /assets/, no app marker) never becomes the shell", async ({
  page,
  context,
}) => {
  const body = `<!doctype html><html><head><script type="module" crossorigin src="/assets/index-Portal9.js"></script></head><body><h1>${PORTAL_TITLE}</h1></body></html>`;
  await expectPageNeverBecomesShell(page, context, body);
});

for (const [name, src] of [
  ["a stylesheet", "/assets/x.css"],
  ["a file without an extension", "/assets/x"],
] as const) {
  test(`13 a marked page whose module script is ${name} is not the app and never becomes the shell`, async ({
    page,
    context,
  }) => {
    const body = `<!doctype html><html><head>${MARKER}<script type="module" src="${src}"></script></head><body><h1>${PORTAL_TITLE}</h1></body></html>`;
    await expectPageNeverBecomesShell(page, context, body);
  });
}

const HTML_POISON = "<html><h1>Sign in to Hotel Wi-Fi</h1></html>";

for (const [name, init] of [
  ["200 with no content-type", { status: 200, headers: {}, body: HTML_POISON }],
  ["200 text/plain", { status: 200, contentType: "text/plain", body: HTML_POISON }],
  ["200 text/html", { status: 200, contentType: "text/html", body: HTML_POISON }],
] as const) {
  test(`14 an asset answered ${name} by a gateway is not served from cache on the next offline load`, async ({
    page,
    context,
  }) => {
    await bootWithAssets(page);
    const poisoned = (r: import("@playwright/test").Route) => r.fulfill({ ...init, headers: { ...init.headers } });
    await context.route("**/assets/poison-1.js", poisoned);
    const seen = await page.evaluate(() => fetch("/assets/poison-1.js").then((r) => r.text()));
    expect(seen, "the page sees the gateway's answer once, online").toContain("Hotel Wi-Fi");
    await page.waitForTimeout(400);
    await context.unroute("**/assets/poison-1.js", poisoned);
    await context.setOffline(true);
    const next = await page.evaluate(() => fetch("/assets/poison-1.js").then((r) => r.text(), () => "network-error"));
    expect(next, "the gateway's page was served from cache").toBe("network-error");
  });
}

test("15 assets of the types a build emits (script, style, font, image, JSON, wasm) are still cached", async ({
  page,
  context,
}) => {
  await bootWithAssets(page);
  const types: [string, string][] = [
    ["a.js", "text/javascript"],
    ["b.js", "application/javascript; charset=utf-8"],
    ["c.css", "text/css"],
    ["d.woff2", "font/woff2"],
    ["e.woff", "application/font-woff"],
    ["f.png", "image/png"],
    ["g.svg", "image/svg+xml"],
    ["h.json", "application/json"],
    ["i.wasm", "application/wasm"],
  ];
  await context.route("**/assets/legit-*", (r) => {
    const name = new URL(r.request().url()).pathname.split("legit-")[1];
    const hit = types.find(([n]) => n === name)!;
    return r.fulfill({ status: 200, contentType: hit[1], body: "x" });
  });
  await page.evaluate(async (names) => {
    for (const n of names) await fetch(`/assets/legit-${n}`);
  }, types.map(([n]) => n));
  await expect.poll(() => paths(page)).toEqual(expect.arrayContaining(types.map(([n]) => `/assets/legit-${n}`)));
});

test("16 offline, /api, /v1 and /health in any spelling are network errors, never the shell", async ({
  page,
  context,
}) => {
  await bootWithAssets(page);
  await context.setOffline(true);
  const urls = [
    "/api",
    "/api/",
    "/api/health",
    "/v1",
    "/v1?x=1",
    "/v1/spaces",
    "/API/health",
    "/V1/Spaces",
    "/v1%2Fspaces",
    "/%61pi/health",
    "/health",
    "/health/",
    "/health?x=1",
    "/healthz",
  ];
  for (const u of urls) await expect(page.goto(u), u).rejects.toThrow(/net::ERR/);
});

test("17 one hashed asset requested under many query strings is cached once", async ({ page }) => {
  await bootWithAssets(page);
  const js = (await paths(page)).find((p) => /^\/assets\/.+\.js$/.test(p))!;
  expect((await paths(page)).filter((p) => p === js)).toHaveLength(1);
  await page.evaluate(async (js) => {
    for (let i = 0; i < 5; i++) await fetch(`${js}?q=${i}`);
  }, js);
  await page.waitForTimeout(400);
  expect((await paths(page)).filter((p) => p === js), "one cache entry per query string").toHaveLength(1);
});

test("18 the app's own page answered 503 is not stored as the shell", async ({ page, context }) => {
  await bootWithAssets(page);
  const good = (await goodShell(page))!;
  const goodAssets = await assetPaths(page);
  const maintenance = good.replace("</body>", "<!--maintenance-503--></body>");
  expect(maintenance).not.toBe(good);
  await context.route(
    (url) => url.pathname === "/",
    (r) => r.fulfill({ status: 503, contentType: "text/html", body: maintenance }),
  );
  await page.goto("/");
  await page.waitForTimeout(400);
  expect(await goodShell(page), "a 503 page became the shell").toBe(good);
  expect(await assetPaths(page)).toEqual(goodAssets);
});

test("19 the app's own page answered 503 to the worker's install fetch is not precached", async ({
  page,
  context,
  request,
}) => {
  const real = await (await request.get("/")).text();
  expect(real).toContain('src="/assets/');
  let fromWorker = 0;
  const isRoot = (url: URL) => url.pathname === "/";
  await context.route(isRoot, (r) => {
    if (!r.request().serviceWorker()) return r.continue();
    fromWorker += 1;
    return r.fulfill({ status: 503, contentType: "text/html", body: real });
  });
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL ?? null))
    .not.toBeNull();
  await expect.poll(() => fromWorker).toBeGreaterThanOrEqual(1);
  await page.waitForTimeout(400);
  expect((await goodShell(page)) ?? "", "a 503 page was precached as the shell").not.toContain('src="/assets/');
});

test("20 a newly deployed worker takes over without waiting, and the phone opens the new shell offline", async ({
  page,
  context,
}) => {
  await bootWithAssets(page);
  const next = `${SHELL}-next`;
  // The worker that is actually served, with only its cache name changed: the next deploy.
  const served = await page.evaluate(() => fetch("/sw.js").then((r) => r.text()));
  const nextSw = served.replace(`const SHELL = "${SHELL}"`, `const SHELL = "${next}"`);
  expect(nextSw).not.toBe(served);
  const deployed = (await goodShell(page))!.replace("</body>", '<p data-testid="deploy-2">Deploy 2</p></body>');
  const isRoot = (url: URL) => url.pathname === "/";
  // Chromium's own update check of /sw.js is not visible to Playwright's routing, but a registration
  // of another script URL at the same scope is, and installs a new worker exactly as a deploy does.
  const isNextSw = (url: URL) => url.pathname === "/sw-next.js";
  await context.route(isNextSw, (r) => r.fulfill({ status: 200, contentType: "text/javascript", body: nextSw }));
  // Only the new worker is shown the deployed page; the old worker and the browser see the live one.
  await context.route(isRoot, (r) =>
    r.request().serviceWorker()?.url().endsWith("/sw-next.js")
      ? r.fulfill({ status: 200, contentType: "text/html", body: deployed })
      : r.continue(),
  );
  await page.evaluate(() => navigator.serviceWorker.register("/sw-next.js", { scope: "/" }).then(() => undefined));
  // The new worker has installed (its shell is stored) ...
  await expect.poll(() => page.evaluate(() => caches.keys())).toContain(next);
  await expect
    .poll(() =>
      page.evaluate(() => navigator.serviceWorker.getRegistration().then((reg) => !reg?.installing)),
    )
    .toBe(true);
  await page.waitForTimeout(500);
  await context.unroute(isNextSw);
  await context.unroute(isRoot);
  // ... and, although the old page is still open, the next offline launch shows the new shell. (A bumped
  // SHELL drops the old build's cached assets, so the app's script is not there; the shell's own markup is
  // what tells the two builds apart.)
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("deploy-2"), "the old worker still controls the page").toBeVisible();
});
