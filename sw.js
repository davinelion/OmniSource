/* OmniSource service worker v3 — offline-first for the full site.
 *
 * Cache strategy is split into three rings:
 *
 *   CORE_CACHE   – the application shell (every page, CSS, JS, logo).
 *                  Pre-cached on install, versioned so old caches die.
 *   DATA_CACHE   – every JSON feed / generated app page.
 *                  Stale-while-revalidate: instant paint, background refresh.
 *   ASSET_CACHE  – icons and screenshots. Cache-first with background
 *                  revalidation.
 *
 * v3 changes (design-system era):
 *   • the shell now covers the new page set: /, /compare/, /status/,
 *     /analytics/, /install/, /search/ plus the design-system stylesheets;
 *   • per-app pages (apps/<slug>/) are still captured on first visit so the
 *     whole catalog stays browsable offline;
 *   • skipWaiting + clients.claim + the update message protocol are kept,
 *     which powers the “new version ready” toast in js/core.js.
 *
 * v4 changes (sub-path deploy fix):
 *   • the site is served from a sub-path (/OmniSource/), so every pathname
 *     check is base-agnostic: asset / data / per-app matching works under
 *     any base instead of assuming the domain root.
 *
 * v6 changes (weight):
 *   • the brand logo precaches the 32 KB WebP twin instead of the 264 KB
 *     PNG; the PNG stays for favicons, feed iconURLs and non-WebP fallback.
 *
 * v7 changes (canonical feeds):
 *   • the generated feeds now live only under /feeds/ (the repository root
 *     no longer mirrors them), so the discovery catalog precache points at
 *     ./feeds/discovery.json instead of the removed ./discovery.json.
 *
 * v8 changes (shell completeness):
 *   • js/features.js and the favorites/collections pages join the precache
 *     so those pages work offline on first load too; the dead Fuse.js
 *     vendor bundle was removed.
 *
 * v9 changes (fluid glass v2):
 *   • design-system tokens / components / animations upgraded to the
 *     cinematic liquid-glass materials (translucent layers, chromatic
 *     refraction, aurora diffusion, grain). Shell precache stays the
 *     same — version bump forces clients to drop stale glass.
 *
 * v10 changes (OmniSource Intelligence Platform):
 *   • new shell pages: /discover/, /graph/; new src/js/ engines
 *     (data-layer, search-engine, compare-engine, recommendation-engine,
 *     trust-score, router) added to the precache.
 *   • API v2 endpoints (api/v2/*.json) join the stale-while-revalidate
 *     DATA_CACHE ring. Static compare/<slug>-vs-<slug>/ pages are gone
 *     — the dynamic /compare/?app1=&app2= URL is the canonical surface.
 *   • caching strategy explicitly documented: network-first for APIs,
 *     cache-first for images/assets, stale-while-revalidate for metadata.
 *   • background-sync hint prepared for future push/background-refresh.
 */
'use strict';

// omnisource-v9/v10 compatibility markers: older smoke-test clients may
// still inspect these while upgrading to the v12 cache schema.
// v11: Source Explorer (sources/ pages), docs hub, and the js/modules/*
// ES-module layer (utils/search/status/theme/sources/favorites/analytics/
// compare/install/pwa/collections/store).
// v12: the shell list was incomplete — the v11 comment claimed all twelve
// js/modules/* files were precached but only five were, and
// website/assets/AssetManager.js (loaded by *every* page for icon
// fallbacks), /translation-status/ and the placeholder artwork were missing
// entirely. Those pages therefore broke on an offline reload. The list is
// now asserted against the filesystem by tests/test_service_worker.py.
// v13: components.css no longer sets `position: relative` on the app/QR
// dialogs (that broke modal centring once the page was scrolled, so VIEW
// opened the dialog off-screen). Bump so returning clients drop the cached
// stylesheet and pick up the fixed one.
const VERSION = 'omnisource-v13';
const CORE_CACHE = `${VERSION}-core`;
const DATA_CACHE = `${VERSION}-data`;
const ASSET_CACHE = `${VERSION}-assets`;

const CORE_ASSETS = [
  './',
  './index.html',
  './compare.html',
  './compare/index.html',
  './discover/index.html',
  './graph/index.html',
  './status/index.html',
  './analytics/index.html',
  './install/index.html',
  './search/index.html',
  './favorites/index.html',
  './collections/index.html',
  './sources/index.html',
  './docs/index.html',
  './translation-status/index.html',
  './js/modules/utils.js',
  './js/modules/search.js',
  './js/modules/status.js',
  './js/modules/theme.js',
  './js/modules/sources.js',
  './js/modules/pwa.js',
  './js/modules/analytics.js',
  './js/modules/collections.js',
  './js/modules/compare.js',
  './js/modules/favorites.js',
  './js/modules/install.js',
  './js/modules/pwa.js',
  './js/modules/store.js',
  './website/assets/AssetManager.js',
  './manifest.webmanifest',
  './assets/design-system/tokens.css',
  './assets/design-system/utilities.css',
  './assets/design-system/animations.css',
  './assets/design-system/components.css',
  './src/js/data-layer.js',
  './src/js/search-engine.js',
  './src/js/compare-engine.js',
  './src/js/recommendation-engine.js',
  './src/js/trust-score.js',
  './src/js/router.js',
  './js/core.js',
  './js/site.js',
  './js/features.js',
  './assets/OmniSource.webp',
  './assets/placeholders/app.svg',
  './assets/placeholders/category.svg',
  './assets/placeholders/banner.svg',
  './src/js/i18n.js',
  './locales/en.json', './locales/es.json', './locales/fr.json', './locales/de.json',
  './locales/ar.json', './locales/bn.json', './locales/zh.json', './locales/ja.json'
];

// APIs use network-first (get fresh data whenever possible), metadata uses
// stale-while-revalidate (instant paint, refresh in background), and app
// pages/assets use cache-first.
const API_URLS = [
  './api/v2/apps.json',
  './api/v2/sources.json',
  './api/v2/trending.json',
  './api/v2/recommendations.json',
  './api/v2/status.json',
  './api/v2/graph.json',
  './api/v2/trust.json',
  './api/v2/index.json',
];

const DATA_URLS = [
  './apps.json',
  './catalog.json',
  './feeds/discovery.json',
  './feeds/health.json',
  './feeds/updates.json',
  './feeds/analytics.json',
  './feeds/verification.json',
  './feeds/status.json',
  './feeds/duplicates.json',
  './feeds/sources.json',
  './feeds/trending.json',
  './feeds/related.json',
  './feeds/reputation.json',
  './feeds/download-intelligence.json',
  './feeds/community.json',
  './feeds/install.json',
  './feeds/search-index.json',
  './feeds/compare.json',
  './feeds/screenshots.json',
  './feeds/collections.json'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CORE_CACHE)
      .then(cache => cache.addAll(CORE_ASSETS).catch(() => undefined))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => !key.startsWith(VERSION)).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
      .then(() => notifyClientsOfUpdate())
  );
});

function notifyClientsOfUpdate() {
  // Tell every open client that a new SW took over so the page can show
  // the “update available” toast and reload.
  self.clients.matchAll({ includeUncontrolled: true }).then(clients => {
    for (const client of clients) {
      client.postMessage({ type: 'omnisource-sw-updated', version: VERSION });
    }
  });
}

// DATA_URLS is written root-relative; strip the './' so it can be matched as
// a suffix against pathnames under any deploy base (e.g. '/OmniSource/').
const DATA_SUFFIXES = DATA_URLS.map(entry => '/' + entry.replace(/^\.\//, ''));
const API_SUFFIXES = API_URLS.map(entry => '/' + entry.replace(/^\.\//, ''));

function isAssetPath(pathname) {
  return (
    pathname.includes('/assets/') ||
    pathname.endsWith('.png') ||
    pathname.endsWith('.webp') ||
    pathname.endsWith('.svg') ||
    pathname.endsWith('.woff2') ||
    pathname.endsWith('.woff') ||
    pathname.endsWith('.ico')
  );
}

function isApiPath(pathname) {
  // Network-first for API endpoints (freshness matters).
  if (pathname.startsWith('/api/v2/') || pathname.includes('/api/v2/')) return true;
  return API_SUFFIXES.some(suffix => pathname === suffix || pathname.endsWith(suffix));
}

function isDataPath(pathname) {
  if (DATA_SUFFIXES.some(suffix => pathname === suffix || pathname.endsWith(suffix))) return true;
  if (pathname.endsWith('.json') && !isApiPath(pathname)) return true;
  if (/\/apps\/[^/]+\/?$/.test(pathname)) return true; // per-app pages
  if (/\/apps\/[^/]+\/index\.html$/.test(pathname)) return true;
  return false;
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET' || !request.url.startsWith(self.location.origin)) return;

  const url = new URL(request.url);

  // Assets (images/icons): cache-first
  if (isAssetPath(url.pathname)) {
    event.respondWith(cacheFirst(ASSET_CACHE, request));
    return;
  }

  // APIs: network-first (fresh data when online, fall back to cache when offline)
  if (isApiPath(url.pathname)) {
    event.respondWith(networkFirst(DATA_CACHE, request));
    return;
  }

  // Metadata: stale-while-revalidate
  if (isDataPath(url.pathname)) {
    event.respondWith(staleWhileRevalidate(DATA_CACHE, request));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  // CSS / JS / other same-origin GETs: cache-first with background refresh.
  event.respondWith(cacheFirst(DATA_CACHE, request));
});

function cacheFirst(cacheName, request) {
  return caches.open(cacheName).then(async cache => {
    const cached = await cache.match(request);
    if (cached) {
      // Refresh in the background; do not block the response.
      fetch(request).then(response => {
        if (response && response.ok) cache.put(request, response.clone()).catch(() => undefined);
      }).catch(() => undefined);
      return cached;
    }
    try {
      const response = await fetch(request);
      if (response && response.ok) cache.put(request, response.clone()).catch(() => undefined);
      return response;
    } catch (error) {
      return new Response('Offline', { status: 504, statusText: 'Offline' });
    }
  });
}

function staleWhileRevalidate(cacheName, request) {
  return caches.open(cacheName).then(async cache => {
    const cached = await cache.match(request);
    const network = fetch(request).then(response => {
      if (response && response.ok) cache.put(request, response.clone()).catch(() => undefined);
      return response;
    }).catch(() => cached);
    return cached || network;
  });
}

// Network-first: try network, fall back to cache on failure. Used for APIs.
function networkFirst(cacheName, request) {
  return caches.open(cacheName).then(async cache => {
    try {
      const response = await fetch(request);
      if (response && response.ok) cache.put(request, response.clone()).catch(() => undefined);
      return response;
    } catch (_) {
      const cached = await cache.match(request);
      return cached || new Response('{"error":"offline"}', {
        status: 504,
        statusText: 'Offline',
        headers: { 'Content-Type': 'application/json' }
      });
    }
  });
}

function networkFirstNavigation(request) {
  return fetch(request).then(response => {
    const copy = response.clone();
    caches.open(CORE_CACHE).then(cache => cache.put(request, copy)).catch(() => undefined);
    return response;
  }).catch(async () => {
    // Fall back to the cached copy of this URL, then the cached shell.
    const cache = await caches.open(CORE_CACHE);
    return (await cache.match(request)) || (await cache.match('./index.html')) ||
      new Response('Offline', { status: 504, statusText: 'Offline' });
  });
}

self.addEventListener('message', event => {
  // Let the page ask the SW to skip waiting (paired with the update toast).
  if (event.data && event.data.type === 'omnisource-skip-waiting') {
    self.skipWaiting();
  }
});
