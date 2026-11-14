import { expect, test, type Page } from "@playwright/test";

/**
 * public/sw.js against the production build. Every assertion is on what a phone
 * user gets: which URLs end up in the cache, and what an offline phone is served.
 * The preview server answers any unknown path with the HTML shell and 200, like
 * the real deployment, which is the condition behind the original cache bug.
 */
test.use({ viewport: { width: 390, height: 844 }, serviceWorkers: "allow" });

const SHELL = "dms-shell-v1";

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
  await page.goto("about:blank").catch(() => undefined);
  const viaFetch = await page.evaluate(() => fetch("/manifest.json").then(() => "served", () => "network-error"));
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
  await page.evaluate(async () => {
    const cache = await caches.open("dms-shell-v1");
    await cache.put("/assets/old-abc.js", new Response("//old", { headers: { "content-type": "text/javascript" } }));
  });
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
    page.evaluate(async (s) => {
      const cache = await caches.open("dms-shell-v1");
      await cache.put("/", new Response(s, { headers: { "content-type": "text/html" } }));
    }, SENTINEL);
  const shellBody = () =>
    page.evaluate(async () => (await (await caches.open("dms-shell-v1")).match("/"))?.text() ?? null);
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
    const old = await caches.open("dms-shell-v0");
    await old.put("/assets/ancient.js", new Response("//ancient", { headers: { "content-type": "text/javascript" } }));
    await (await navigator.serviceWorker.getRegistration())?.unregister();
  });
  expect(await page.evaluate(() => caches.keys())).toContain("dms-shell-v0");
  // A fresh registration installs and activates again, as after a deploy that bumps SHELL.
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => caches.keys())).toEqual([SHELL]);
  expect(await paths(page)).not.toContain("/assets/ancient.js");
});
