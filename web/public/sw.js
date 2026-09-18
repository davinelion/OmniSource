/* OmniSource web service worker.
 *
 * Strategy (three rings, like the static site's worker):
 *   shell        – navigations are network-first and fall back to the cached
 *                  copy, then to the precached shell. A deploy is therefore
 *                  picked up on the next load instead of an old HTML document
 *                  being pinned in the cache forever.
 *   data         – /api/ and /data/ use network-first (fresh metadata when
 *                  online, cached metadata when not).
 *   static       – /_next/static/ files are content-hashed, so they are
 *                  cache-first for a year; everything else same-origin is
 *                  cache-first with a background refresh.
 *
 * Updates are announced, never forced: this worker does not call
 * `skipWaiting()` on install, so a new version waits until the page asks for it
 * — the page shows an Update / Later toast (see components/PwaRegister.tsx) and
 * posts `omnisource-skip-waiting` only when the reader chooses Update. Changing
 * VERSION retires the previous caches on activate.
 */
const VERSION = "omnisource-web-v1";
const CACHE = VERSION;
const PRECACHE = ["/", "/manifest.webmanifest", "/data/v3/apps.json", "/data/v3/search-index.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // One missing entry must not abort the whole shell.
      .then((cache) => cache.addAll(PRECACHE).catch(() => undefined)),
  );
});

/** Which tabs were already running under an older worker? Asked before claim. */
function upgradingClients() {
  return self.clients
    .matchAll({ includeUncontrolled: false, type: "window" })
    .then((clients) => clients.filter((client) => Boolean(client.controller)).map((client) => client.id))
    .catch(() => []);
}

function announceUpdate(ids) {
  if (!Array.isArray(ids) || !ids.length) return;
  self.clients
    .matchAll({ includeUncontrolled: false, type: "window" })
    .then((clients) => {
      for (const client of clients) {
        if (!ids.includes(client.id)) continue;
        client.postMessage({ type: "omnisource-sw-updated", version: VERSION });
      }
    })
    .catch(() => undefined);
}

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => upgradingClients())
      .then((ids) => self.clients.claim().then(() => announceUpdate(ids))),
  );
});

function cacheable(request, response) {
  if (!response || !response.ok || response.status !== 200) return false;
  if (request.method !== "GET") return false;
  if (response.type === "opaque" || response.type === "opaqueredirect") return false;
  // Range/partial bodies must never be stored as if they were the full file.
  if (response.status === 206) return false;
  return true;
}

async function put(request, response) {
  try {
    const cache = await caches.open(CACHE);
    await cache.put(request, response);
  } catch {
    /* quota or unsupported body: caching is best-effort */
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (cacheable(request, response)) void put(request, response.clone());
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await caches.match("/");
      if (shell) return shell;
    }
    return new Response(
      request.mode === "navigate" ? "<!doctype html><title>Offline</title><p>You are offline." : "Offline",
      {
        status: 504,
        statusText: "Offline",
        headers: request.mode === "navigate" ? { "Content-Type": "text/html; charset=utf-8" } : {},
      },
    );
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    // Refresh in the background; the response the page gets is never blocked.
    fetch(request)
      .then((response) => {
        if (cacheable(request, response)) return put(request, response.clone());
        return undefined;
      })
      .catch(() => undefined);
    return cached;
  }
  try {
    const response = await fetch(request);
    if (cacheable(request, response)) void put(request, response.clone());
    return response;
  } catch {
    const shell = request.mode === "navigate" ? await caches.match("/") : null;
    if (shell) return shell;
    return new Response("Offline", { status: 504, statusText: "Offline" });
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Next's client-side RSC traffic carries cache-control of its own; leave it
  // out of the shell ring so a stale payload can never be replayed.
  if (url.searchParams.has("_rsc")) return;

  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/data/")) {
    event.respondWith(networkFirst(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});

self.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "omnisource-skip-waiting") self.skipWaiting();
});
