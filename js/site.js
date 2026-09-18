/* ============================================================================
   OmniSource — site.js
   ----------------------------------------------------------------------------
   Page-level rendering for the dynamic pages:

     body[data-page="home"]       landing page (hero, rails, catalog, timeline)
     body[data-page="compare"]    /compare/   side-by-side app comparison
     body[data-page="status"]     /status/    source health center
     body[data-page="analytics"]  /analytics/ metrics dashboard
     body[data-page="install"]    /install/   installation center
     body[data-page="search"]     /search/    full search results

   Everything renders from generated JSON (see docs/API.md) — no backend, no
   build step. Shared helpers, the search engine, theme and PWA plumbing come
   from js/core.js (window.OS).
   ========================================================================== */
(function () {
  'use strict';

  var OS = window.OS;
  if (!OS) return; // core.js must load first

  var $ = OS.$;
  var $$ = OS.$$;

  /* Favorites are shared with js/features.js (favorites/collections pages).
     'omnisource-favorites' is canonical (raw JSON array); 'os:favorites' is
     the prefixed legacy key features.js used on its own. Both are kept in
     sync and merged on read so hearts agree on every page.

     These two constants have to be declared before `state` below: `var`
     hoists the *binding*, not the assignment, so when they were declared
     after the literal every page booted with both keys `undefined` and
     `loadFavorites()` read `localStorage.getItem(undefined)`. The set was
     therefore always empty on load - hearts showed hollow, the "Saved" chip
     said 0 and the Saved shelf was empty - even though both storage keys held
     the list, which is why /favorites/ (features.js, which reads the literal
     key names) still showed the apps. */
  var FAVORITES_KEY = 'omnisource-favorites';
  var FAVORITES_LEGACY_KEY = 'os:favorites';

  /* ---------------------------------------------------------------- state */
  var state = {
    apps: [],
    catalog: null,
    clients: [],
    health: null,
    updates: null,
    analytics: null,
    verification: new Map(),
    discovery: new Map(),
    trending: null,
    related: null,
    reputation: null,
    downloadIntel: null,
    community: null,
    install: null,
    status: null,
    compare: null,
    collisions: new Map(),
    favorites: new Set(loadFavorites()),
    query: '',
    category: 'all',
    statusFilter: 'all',
    provenance: 'all',
    os: 'any',
    sort: 'featured',
    activeApp: null,
    activeTab: 'about',
    /* `loaded` flips once the first-paint feeds and the catalog metadata are
       in, which lets openApp() tell "this app is not in the catalog" from "the
       catalog has not arrived yet" (see state.pendingOpen). */
    loaded: false,
    pendingOpen: null
  };

  /* Favorites are shared with js/features.js (favorites/collections pages) —
     see the key declarations above for why they come first. */
  function readFavoriteList(key) {
    try {
      var list = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function loadFavorites() {
    var canonical = readFavoriteList(FAVORITES_KEY);
    var legacy = readFavoriteList(FAVORITES_LEGACY_KEY);
    var merged = Array.from(new Set(canonical.concat(legacy)));
    if (merged.length !== canonical.length || legacy.length !== merged.length) {
      try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(merged)); } catch (e) { /* ignore */ }
      try { localStorage.setItem(FAVORITES_LEGACY_KEY, JSON.stringify(merged)); } catch (e) { /* ignore */ }
    }
    return merged;
  }
  function saveFavorites() {
    var value = JSON.stringify(Array.from(state.favorites));
    try { localStorage.setItem(FAVORITES_KEY, value); } catch (e) { /* ignore */ }
    try { localStorage.setItem(FAVORITES_LEGACY_KEY, value); } catch (e) { /* ignore */ }
    try { window.dispatchEvent(new CustomEvent('os:favorites-changed')); } catch (e) { /* ignore */ }
  }

  /* --------------------------------------------------------------- labels */
  var CATEGORY_LABELS = {
    'photo-video': 'Photo & Video', music: 'Music', social: 'Social', news: 'News',
    utilities: 'Utilities', networking: 'Networking', games: 'Games',
    'developer-tools': 'Developer Tools', entertainment: 'Entertainment', other: 'Other'
  };
  var STATUS_LABELS = { stable: 'Stable', beta: 'Beta', manual: 'Manual', unmaintained: 'Unmaintained', deprecated: 'Deprecated' };
  var KIND_LABELS = { new: 'New app', updated: 'Updated', available: 'Live', unmaintained: 'Flagged' };
  var VERIFICATION_LABELS = { 'VERIFIED': '✓ Verified', 'COMMUNITY VERIFIED': 'Community verified', 'UNVERIFIED': 'Unverified' };
  var categoryLabel = function (c) { return CATEGORY_LABELS[c] || (c ? c.charAt(0).toUpperCase() + c.slice(1) : 'Other'); };
  var statusLabel = function (s) { return STATUS_LABELS[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown'); };

  /* -------------------------------------------------------------- lookups */
  /* Icons come from data written in three URL shapes; OS.icon resolves each
     against the site root (see the helper in core.js). */
  var iconSrc = function (value, fallback) { return OS.icon(value, fallback); };
  var slugFor = function (app) { return (app.omnisource && app.omnisource.slug) || (app.bundleIdentifier ? app.bundleIdentifier.split('.').pop().toLowerCase() : 'app'); };
  var feedFor = function (app) { return OS.url('feeds/' + slugFor(app) + '.json'); };
  var rssFor = function (app) { return OS.url('feeds/' + slugFor(app) + '.xml'); };
  var healthFor = function (app) { return (state.health && state.health.apps || []).find(function (item) { return item.slug === slugFor(app); }) || {}; };
  var verificationFor = function (app) { return state.verification.get(slugFor(app)) || null; };
  /* Official = the IPA is published by the project's own upstream release
     feed/repository. Community = repackaged/mirrored by someone else (the
     compatibility notes disclose who). Mirrors verification.py's rule. */
  var COMMUNITY_METHODS = { 'manual-mirror': 1, 'self-built': 1 };
  function provenanceFor(app) {
    var meta = app.omnisource || app || {};
    var verification = meta.verification || (app.verification) || {};
    var method = String(verification.method || '').toLowerCase();
    var publisher = String(verification.publisher || '').toLowerCase();
    var upstream = String(meta.upstreamURL || app.upstreamURL || '').toLowerCase();
    if ((meta.status || app.status) === 'manual') return 'community';
    if (COMMUNITY_METHODS[method]) return 'community';
    if (/vault|mirror|community/.test(publisher)) return 'community';
    if (method === 'github-release') {
      var p = publisher.replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, '');
      var m = upstream.match(/github\.com\/([^/]+\/[^/]+?)(?:\.git)?\/?$/);
      if (p && m && m[1] !== p) return 'community';
    }
    return 'official';
  }
  var discoveryFor = function (app) { return state.discovery.get(slugFor(app)) || null; };
  var minOSMajor = function (app) {
    var value = app.omnisource && app.omnisource.compatibility ? app.omnisource.compatibility.minOSVersion : null;
    if (!value) return null;
    var m = String(value).match(/^(\d+)/);
    return m ? Number(m[1]) : null;
  };
  var appForSlug = function (slug) {
    return state.apps.find(function (a) { return slugFor(a) === slug; }) || null;
  };

  /* DOM id for a category tab. The category id is catalog data, so anything
     outside the safe set is dropped rather than escaped — an id with a space
     or a quote in it would not survive aria-labelledby. */
  function tabIdFor(categoryId) {
    return 'catTab-' + String(categoryId || 'all').replace(/[^A-Za-z0-9_-]/g, '');
  }

  function readCategoryParam() {
    try {
      return new URLSearchParams(location.search).get('category') || '';
    } catch (e) { return ''; }
  }

  /* The selected shelf lives in the query string (?category=games) so a tab
     can be shared or bookmarked. The hash is deliberately left alone: on the
     home page it already deep-links to an app dialog (#slug).

     Read once, at parse time. The deferred feeds repaint the catalog — and
     therefore rewrite the query string — before boot()'s promise chain
     resolves, so reading the parameter any later finds the URL this module
     has already normalised and the shared link silently does nothing. */
  var categoryDeepLink = readCategoryParam();
  var categoryDeepLinkApplied = false;

  function writeCategoryParam() {
    // Never normalise away a deep link that has not been honoured yet.
    if (!categoryDeepLinkApplied) return;
    try {
      var params = new URLSearchParams(location.search);
      if (state.category && state.category !== 'all') params.set('category', state.category);
      else params.delete('category');
      var query = params.toString();
      history.replaceState(null, '', location.pathname + (query ? '?' + query : '') + location.hash);
    } catch (e) { /* sandboxed frame: the URL is a nicety, not the point */ }
  }

  /* ------------------------------------------------------------ deep links */
  /* The browser resolves `/#catalog` (and `/#trending`, `/#whats-new`, …) the
     moment the document parses — while every section above the target is still
     collapsed, waiting for its feed. Each feed that lands afterwards un-hides
     thousands of pixels over the reader's head, so the reader who asked for the
     catalog used to stop just past the hero: measured 5693 px short for
     `/#catalog` and 494 px short for `/#trending`. The same applies when a
     section is entered from another page (`../../#catalog`, 5578 px short).

     Re-pin the target after every render that can still move it, and stop as
     soon as the reader scrolls on their own — at that point they are where
     they want to be, and yanking them back would be worse than the drift. */
  var repinTarget = null;
  var repinArmed = false;
  /* Set while our own scrollIntoView is in flight, so the scroll event it
     raises is not mistaken for the reader scrolling (see armHashRepin). */
  var repinOwnScroll = false;

  function sectionTargetFor(id) {
    var node = document.getElementById(id);
    if (!node || node.hidden) return null;
    /* `#<slug>` app deep links open the in-page dialog instead of scrolling;
       js/core.js owns those and they never name a section id. */
    return node;
  }

  function armHashRepin() {
    if (repinArmed) return;
    repinArmed = true;
    /* Two lifetimes, deliberately different:
       - watching the reader's own movement ends the *current* re-pin, and
       - a new hash is a new request, so it stays listenable for the whole
         window and re-arms the movement watch. Tearing both down together is
         what made a deep link clicked after the reader had scrolled once
         silently unprotected. */
    var watching = false;
    var stopWatching = function () {
      watching = false;
      window.removeEventListener('wheel', onMove);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll);
    };
    function watch() {
      if (watching) return;
      watching = true;
      window.addEventListener('wheel', onMove, { passive: true });
      window.addEventListener('touchmove', onMove, { passive: true });
      window.addEventListener('keydown', onKey);
      window.addEventListener('scroll', onScroll, { passive: true });
    }
    function disarm() {
      repinTarget = null;
      stopWatching();
    }
    function onMove() {
      disarm();
    }
    function onKey(event) {
      /* Only keys that actually scroll; Cmd/ctrl-K and friends are not a
         statement about where the reader wants to be. */
      if (/^(Arrow|Page|Home|End|Spacebar| )/.test(event.key)) disarm();
    }
    function onHash() {
      /* A new hash is a new request: retarget (or drop the re-pin for an app
         slug, which js/core.js opens as a dialog instead). */
      var raw = (location.hash || '').slice(1);
      var id = raw ? decodeURIComponent(raw) : '';
      repinTarget = id && sectionTargetFor(id) ? id : null;
      if (repinTarget) {
        watch();
        repinHash();
      }
    }
    function onScroll() {
      /* The reader moved. `wheel`, `touchmove` and the scroll keys cover a
         trackpad, a finger and a keyboard, but *not* a scrollbar drag or
         middle-click autoscroll — neither raises one of those events. A reader
         who scrolled that way was still "armed", so the next deferred feed to
         land (they arrive for several seconds) re-pinned the page to a deep
         link the reader had already scrolled away from: the catalog snapped
         back to its own top mid-read, which reads exactly as the page
         reloading. Any scroll we did not cause ends the re-pin. */
      if (repinOwnScroll) {
        repinOwnScroll = false;
        return;
      }
      disarm();
    }
    watch();
    window.addEventListener('hashchange', onHash);
    /* Feeds give up after their timeouts (6 s) and the idle tier lands within
       ~2.5 s; after that nothing else moves the page. */
    setTimeout(function () {
      disarm();
      window.removeEventListener('hashchange', onHash);
    }, 25000);
  }

  function repinHash() {
    if (!repinTarget) return;
    var node = document.getElementById(repinTarget);
    if (!node || node.hidden) return;
    repinOwnScroll = true;
    try {
      node.scrollIntoView({ behavior: 'auto', block: 'start' });
    } catch (error) {
      try { node.scrollIntoView(); } catch (inner) { /* nothing to scroll */ }
    }
    /* scrollIntoView announces itself with one scroll event, dispatched in the
       same frame's scroll steps — before rAF callbacks — so the flag is
       consumed there. When the node is already in position no event fires at
       all, and leaving the flag set would swallow the reader's *next* scroll,
       so drop it on the next frame as well. */
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () { repinOwnScroll = false; });
    } else {
      repinOwnScroll = false;
    }
  }

  /* ?category=<id> lands the reader on a shelf — applied once, before the
     first full render, and validated against the catalog so a stale or
     invented value cannot leave the grid and the tabs disagreeing. */
  function applyCategoryDeepLink() {
    if (categoryDeepLinkApplied) return;
    categoryDeepLinkApplied = true;
    if (!categoryDeepLink) return;
    var known = categoryDeepLink === 'all' || categoryDeepLink === 'favorites' ||
      state.apps.some(function (app) { return (app.category || 'other') === categoryDeepLink; });
    if (known) state.category = categoryDeepLink;
  }

  /* ------------------------------------------------------------ data load */
  // /apps.json lives at the repository root (installable source URL) and in
  // the deployed site; /discovery.json is only assembled into the deployed
  // site by the build step. Fall back to the canonical organized location
  // under feeds/ so the catalog keeps loading either way.
  function fetchFeed(primary, fallback, timeoutMs) {
    return OS.fetchJSON(primary, timeoutMs).then(function (doc) {
      return doc != null ? doc : OS.fetchJSON(fallback, timeoutMs);
    });
  }

  /* Feed registry. Each entry declares the document it loads, the state slot
     it fills, the pages that actually render it, and whether it belongs to the
     first paint.

     This replaces a single Promise.all over all 16 documents: every page used
     to download the whole 2.09 MB bundle and draw nothing until the slowest
     file landed, so on a phone the catalog and every rail sat as skeletons for
     seconds and the nav's #trending anchor pointed at a still-hidden section.
     Now the catalog plus the four feeds behind the hero stats and the rails
     paint first (~825 KB), the long tail streams in behind a debounced
     re-render, and the other pages fetch only what they render. */
  var FEEDS = [
    {
      key: 'health', path: 'feeds/health.json', firstPaint: true,
      pages: ['home'],
      set: function (doc) { state.health = doc; }
    },
    {
      key: 'analytics', path: 'feeds/analytics.json', firstPaint: true,
      pages: ['home', 'status', 'analytics'],
      set: function (doc) { state.analytics = doc; }
    },
    {
      key: 'verification', path: 'feeds/verification.json', firstPaint: true,
      pages: ['home', 'search'],
      set: function (doc) {
        if (doc && Array.isArray(doc.apps)) {
          doc.apps.forEach(function (entry) { state.verification.set(entry.app, entry); });
        }
      }
    },
    {
      key: 'trending', path: 'feeds/trending.json', firstPaint: true,
      pages: ['home'],
      set: function (doc) { state.trending = doc; }
    },
    {
      key: 'updates', path: 'feeds/updates.json', idle: true,
      pages: ['home'],
      set: function (doc) { state.updates = doc; }
    },
    {
      key: 'discovery', primary: 'discovery.json', fallback: 'feeds/discovery.json',
      pages: ['home'],
      set: function (doc) {
        if (doc && Array.isArray(doc.apps)) {
          window.OS_CATALOG = doc.apps;
          doc.apps.forEach(function (entry) { state.discovery.set(entry.id || entry.slug, entry); });
        }
      }
    },
    {
      key: 'reputation', path: 'feeds/reputation.json',
      pages: ['home', 'status'],
      set: function (doc) { state.reputation = doc; }
    },
    {
      key: 'downloadIntel', path: 'feeds/download-intelligence.json',
      pages: ['home'],
      set: function (doc) { state.downloadIntel = doc; }
    },
    {
      key: 'install', path: 'feeds/install.json', idle: true,
      pages: ['home', 'install'],
      set: function (doc) { state.install = doc; }
    },
    {
      key: 'status', path: 'feeds/status.json',
      pages: ['status'],
      set: function (doc) { state.status = doc; }
    },
    {
      key: 'compare', path: 'feeds/compare.json',
      pages: ['compare'],
      set: function (doc) { state.compare = doc; }
    },
    {
      /* The palette's index: 120 KB that only matters once the search UI is
         opened. js/core.js fetches it on demand (Search.load) and now reuses
         whatever is already in OS.Search.docs, so the id-timed fetch here is
         purely a head start for the first Cmd/ctrl-K. */
      key: 'searchIndex', path: 'feeds/search-index.json', idle: true,
      pages: ['home', 'search'],
      set: function (doc) {
        if (doc && Array.isArray(doc.documents) && !OS.Search.docs.length) {
          OS.Search.docs = doc.documents;
        }
      }
    }
  ];

  function feedsForPage(page) {
    return FEEDS.filter(function (feed) { return feed.pages.indexOf(page) !== -1; });
  }

  function loadFeed(feed) {
    var request = feed.fallback
      ? fetchFeed(feed.primary, feed.fallback, 6000)
      : OS.fetchJSON(feed.path, 6000);
    return request.then(function (doc) {
      if (doc) feed.set(doc);
      return doc;
    });
  }

  /* Re-render after background feeds land. Debounced so a burst of arrivals
     causes one pass instead of fourteen. The hero is deliberately excluded —
     its numbers come from the first-paint feeds and re-running it would replay
     the count-up animation a second time. */
  var refreshTimer = null;
  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      refreshTimer = null;
      refreshPage();
    }, 120);
  }

  function refreshPage() {
    var page = (document.body && document.body.dataset.page) || '';
    /* Every deferred feed un-hides a section, and on the home page most of
       them sit *above* the catalog. Re-rendering from here therefore inserts
       thousands of pixels over the reader's head — fine on Chromium, which
       anchors the scroll for us, and a violent jump on iOS Safari, which does
       not. OS.stableScroll re-pins the viewport around the work. */
    var render = function () {
      if (page === 'home') {
        Home.renderRails();
        Home.renderMetrics();
        Home.renderSourceHealth();
        Home.renderInstallGuide();
        Home.renderTimeline();
        Home.filterAndRender();
      } else if (page === 'status') {
        StatusPage.render();
      } else if (page === 'analytics') {
        Analytics.render();
      } else if (page === 'install') {
        Install.render();
      } else if (page === 'search') {
        SearchPage.render();
      } else if (page === 'compare') {
        Compare.load();
      }
    };
    try {
      if (OS.stableScroll) OS.stableScroll(render);
      else render();
    } catch (error) {
      /* renderer not ready on this page */
    }
    // A section that just arrived (or just gave up) changes which tabs exist.
    if (OS.refreshSectionTabs) {
      try { OS.refreshSectionTabs(); } catch (error) { /* no tab bar here */ }
    }
    // …and it also moved every section below it, including the one a deep link
    // asked for. Put the reader back on their target (see armHashRepin).
    repinHash();
  }

  function loadData() {
    var page = (document.body && document.body.dataset.page) || 'home';
    var wanted = feedsForPage(page);
    // Only the home page has enough feeds to be worth splitting: the other
    // pages need one or two documents, and their renderers bind listeners, so
    // they render exactly once with everything present.
    var split = page === 'home';
    var primary = split ? wanted.filter(function (feed) { return feed.firstPaint; }) : wanted;
    var deferred = split ? wanted.filter(function (feed) { return !feed.firstPaint && !feed.idle; }) : [];
    /* Idle-tier feeds (`idle: true`) are the ones nothing on the first screen
       reads: the install cards, the release timeline and the palette index.
       Together they were ~600 KB of JSON parsed while the catalog was still
       painting. They now arrive on the browser's idle callback (with a
       timeout, and a plain timer where requestIdleCallback is missing), and
       render through the same refresh path as the deferred feeds. */
    var idle = split ? wanted.filter(function (feed) { return feed.idle; }) : [];

    var catalog = fetchFeed('apps.json', 'feeds/apps.json').then(function (feedDoc) {
      if (!feedDoc || !Array.isArray(feedDoc.apps)) throw new Error('Feed unavailable');
      state.apps = feedDoc.apps;
    });

    // Background feeds never block the first paint; each one re-renders the
    // widgets that need it as it arrives.
    deferred.forEach(function (feed) {
      loadFeed(feed).then(scheduleRefresh);
    });

    if (idle.length) {
      scheduleIdle(function () {
        idle.forEach(function (feed) {
          loadFeed(feed).then(scheduleRefresh);
        });
      });
    }

    return Promise.all([catalog].concat(primary.map(loadFeed)));
  }

  /* Run `work` when the main thread is free. requestIdleCallback is missing on
     Safari before 15.4 and in older WebViews, so it falls back to a timer that
     still lands after the first paint. */
  function scheduleIdle(work) {
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(work, { timeout: 2500 });
      return;
    }
    setTimeout(work, 1200);
  }

  function loadCatalogMeta() {
    return OS.fetchJSON('catalog.json', 6000).then(function (meta) {
      if (!meta) {
        state.clients = [
          { id: 'altstore', name: 'AltStore', icon: 'AltStore.webp' },
          { id: 'sidestore', name: 'SideStore', icon: 'SideStore.webp' },
          { id: 'flarestore', name: 'FlareStore', icon: 'FlareStore.webp' },
          { id: 'feather', name: 'Feather', icon: 'Feather.webp' },
          { id: 'esign', name: 'ESign', icon: 'E-Sign.webp' },
          { id: 'ksign', name: 'Ksign', icon: 'Ksign.webp' },
          { id: 'livecontainer', name: 'LiveContainer', icon: 'LiveContainer.webp' }
        ];
        return;
      }
      state.catalog = meta;
      state.clients = meta.clients || [];
    });
  }

  function buildCollisions() {
    var bundles = new Map();
    state.apps.forEach(function (app) {
      var key = app.bundleIdentifier;
      if (!key) return;
      if (!bundles.has(key)) bundles.set(key, []);
      bundles.get(key).push(slugFor(app));
    });
    state.collisions = new Map();
    bundles.forEach(function (slugs, bundle) {
      if (slugs.length > 1) state.collisions.set(bundle, slugs);
    });
  }

  /* ------------------------------------------------------------- shared UI */
  function collisionInfo(app) {
    var slugs = state.collisions.get(app.bundleIdentifier) || [];
    if (slugs.length < 2) return null;
    var others = slugs.filter(function (s) { return s !== slugFor(app); });
    return {
      count: others.length + 1,
      names: others.map(function (s) {
        var other = appForSlug(s);
        return other ? other.name : s;
      }).join(', ')
    };
  }

  /* Percent-encode only what would break the link or the attribute it lives
     in: a quote or angle bracket ends the href, `#` / `&` / whitespace end or
     split the query, and backslash, caret, grave accent, braces, bar, dollar
     and apostrophe are excluded from URLs by RFC 3986 §2 — so no legitimate
     feed URL contains them, escaping them costs nothing, and a crafted value
     cannot smuggle structure into the scheme template. `:` and `/` stay
     intact so every deep link the browser renders is byte-identical to the
     ones the build generates in feeds/install.json and apps/<slug>/ from
     src/omnisource/install.py — several clients parse the query naively and
     reject a fully encoded `https%3A%2F%2F…`, which is why ESign and
     LiveContainer used to behave differently from AltStore and SideStore. */
  var FEED_PARAM_UNSAFE = /["'<>#&\s\\^`{|}$]/g;

  function encodeFeedParam(value) {
    return String(value == null ? '' : value).replace(FEED_PARAM_UNSAFE, function (ch) {
      return encodeURIComponent(ch);
    });
  }

  /* Feather's bare host+path, matching install.py's `netloc + path`: the
     scheme AND any query/fragment are dropped, not just the scheme. */
  function hostPath(value) {
    var raw = String(value == null ? '' : value);
    try {
      var parsed = new URL(raw);
      return (parsed.host + parsed.pathname) || raw.split('://').pop();
    } catch (err) {
      var noScheme = raw.indexOf('://') > -1 ? raw.slice(raw.indexOf('://') + 3) : raw;
      return noScheme.split(/[?#]/)[0];
    }
  }

  /* One table, mirroring src/omnisource/install.py::CLIENT_PROFILES. All five
     clients ship a one-tap "add source" scheme; Feather is the odd one out
     and takes a bare host+path instead of a query string. */
  var CLIENT_SCHEMES = {
    altstore: 'altstore://source?url={url}',
    sidestore: 'sidestore://source?url={url}',
    flarestore: 'flarestore://source?url={url}',
    feather: 'feather://source/{hostpath}',
    esign: 'esign://addsource?url={url}',
    ksign: 'ksign://addsource?url={url}',
    livecontainer: 'livecontainer://sources?url={url}'
  };

  function installUrlFor(clientId, feedUrl) {
    var scheme = CLIENT_SCHEMES[String(clientId || '').toLowerCase()];
    if (!scheme || !feedUrl) return '';
    /* Function replacers, never strings: `$` is not escaped by
       encodeURIComponent, so a `$&` / `$'` / `$1` sequence in a value would
       otherwise be interpreted as a replacement pattern by String.replace. */
    return scheme
      .replace('{url}', function () { return encodeFeedParam(feedUrl); })
      .replace('{hostpath}', function () { return hostPath(feedUrl); });
  }

  function clientButton(client, feedUrl) {
    var icon = client.icon
      ? '<img src="' + OS.esc(OS.url('assets/' + client.icon)) + '" alt="" loading="lazy">'
      : '<span class="cli-fallback">' + OS.esc(String(client.name || '?').slice(0, 1).toUpperCase()) + '</span>';
    var urlValue = installUrlFor(client.id, feedUrl);
    var common = 'class="button client-button os-press"';
    if (urlValue) {
      return '<a ' + common + ' href="' + OS.esc(urlValue) + '" title="Add to ' + OS.esc(client.name) + '" aria-label="Add to ' + OS.esc(client.name) + '">' + icon + OS.esc(client.name) + '</a>';
    }
    return '<button ' + common + ' type="button" data-copy="' + OS.esc(feedUrl) + '" data-copy-msg="URL copied — paste it in ' + OS.esc(client.name) + '" title="Copy the source URL for ' + OS.esc(client.name) + '">' + icon + OS.esc(client.name) + '</button>';
  }

  function verificationBadgeClass(level) {
    if (level === 'VERIFIED') return 'verified';
    if (level === 'COMMUNITY VERIFIED') return 'community';
    return 'unverified';
  }

  /* Source cell: the human-readable source name, hyperlinked to the repo/feed
     that actually publishes the sideload IPA when one is known. */
  function sourceCell(app) {
    var text = app.source || '—';
    var url = app.sourceURL ? OS.cleanUrl(app.sourceURL) : '';
    if (!url || url === '#') return OS.esc(text);
    return '<a class="source-link" href="' + OS.esc(url) + '" target="_blank" rel="noopener">' + OS.esc(text) + '</a>';
  }

  function tintFor(slug) {
    var app = appForSlug(slug);
    if (app && app.tintColor) return '#' + String(app.tintColor).replace(/^#/, '');
    var disc = state.discovery.get(slug);
    if (disc && disc.tint) return '#' + String(disc.tint).replace(/^#/, '');
    return null;
  }

  function railCard(slug, extra) {
    var app = appForSlug(slug);
    if (!app) return '';
    var meta = app.omnisource || {};
    var verification = verificationFor(app);
    var disc = state.discovery.get(slug);
    var icon = iconSrc(app.iconURL || app.icon);
    var tags = (disc && disc.tags ? disc.tags : []).slice(0, 3);
    var scorePill = extra && extra.score != null
      ? '<span class="score-pill" title="Trending score: recency + availability + featured + verification">' + (extra.score * 100).toFixed(0) + '</span>'
      : '';
    return '<a class="rail-card os-lift" href="' + OS.esc(OS.url('apps/' + slug + '/')) + '" aria-label="View ' + OS.esc(app.name) + '">' +
      scorePill +
      '<div class="row">' +
        '<img class="os-icon" src="' + OS.esc(icon) + '" alt="" width="48" height="48" loading="lazy" onerror="this.onerror=null;this.src=\'' + OS.esc(OS.url('assets/OmniSource.png')) + '\'">' +
        '<div style="min-width:0;flex:1"><h3>' + OS.esc(app.name) + '</h3>' +
        '<div class="dev">' + OS.esc(app.developerName || '') + '</div></div>' +
      '</div>' +
      '<p class="desc">' + OS.esc((app.subtitle || app.localizedDescription || '').slice(0, 150)) + '</p>' +
      '<div class="meta"><b>v' + OS.esc(app.version || '—') + '</b>' +
        (meta.status ? '<span>' + OS.esc(statusLabel(meta.status)) + '</span>' : '') +
        (verification ? '<span>' + OS.esc(VERIFICATION_LABELS[verification.status] || verification.status) + '</span>' : '') +
        (tags.length ? '<span>' + OS.esc(tags.join(' · ')) + '</span>' : '') +
      '</div></a>';
  }

  function featuredCard(slug, rank) {
    var app = appForSlug(slug);
    if (!app) return '';
    var meta = app.omnisource || {};
    var icon = iconSrc(app.iconURL || app.icon);
    var disc = state.discovery.get(slug);
    var tags = (disc && disc.tags ? disc.tags : []).slice(0, 3);
    var tint = tintFor(slug);
    var styleAttr = tint ? ' style="--tint:' + OS.esc(tint) + '"' : '';
    return '<a class="featured-card os-lift" href="' + OS.esc(OS.url('apps/' + slug + '/')) + '"' + styleAttr + ' aria-label="View ' + OS.esc(app.name) + '">' +
      '<div class="featured-bg" aria-hidden="true"></div>' +
      '<span class="rank" aria-hidden="true">0' + rank + '</span>' +
      '<div class="fc-top">' +
        '<img class="os-icon" src="' + OS.esc(icon) + '" alt="" width="64" height="64" loading="lazy">' +
        '<div><h3>' + OS.esc(app.name) + '</h3><div class="fc-dev">by ' + OS.esc(app.developerName || 'independent developer') + '</div></div>' +
      '</div>' +
      '<p class="fc-desc">' + OS.esc(app.localizedDescription || app.subtitle || '') + '</p>' +
      '<div class="fc-meta"><b>v' + OS.esc(app.version || '—') + '</b>' +
        (meta.status ? '<span>' + OS.esc(statusLabel(meta.status)) + '</span>' : '') +
        (tags.length ? '<span>' + OS.esc(tags.join(' · ')) + '</span>' : '') +
      '</div>' +
      '<span class="fc-cta" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg></span>' +
      '</a>';
  }

  function appCard(app) {
    var meta = app.omnisource || {};
    var slug = slugFor(app);
    var online = healthFor(app).downloadReachable !== false;
    var status = meta.status || 'stable';
    var stale = Boolean(healthFor(app).stale);
    var conflict = collisionInfo(app);
    var collisionBadge = conflict
      ? '<span class="badge warn" title="Bundle ID ' + OS.esc(app.bundleIdentifier) + ' is shared by ' + conflict.count + ' apps: ' + OS.esc(conflict.names) + '. Installing one replaces the others on device.">⚠ Shared bundle ×' + conflict.count + '</span>'
      : '';
    var statusBadge = '<span class="badge ' + (status === 'stable' ? 'stable' : status) + '">' + OS.esc(statusLabel(status)) + '</span>';
    var healthBadge = online
      ? '<span class="badge ok"><span class="dot"></span>Online</span>'
      : '<span class="badge bad"><span class="dot"></span>Offline</span>';
    var staleBadge = stale ? '<span class="badge warn">Stale</span>' : '';
    var verification = verificationFor(app);
    var provenance = provenanceFor(app);
    var provenanceBadge = provenance === 'official'
      ? '<span class="badge verified" title="IPA published by the project&#39;s own upstream release channel">Official build</span>'
      : '<span class="badge community" title="Repackaged or mirrored by a community builder — see the Details tab for provenance">Community build</span>';
    var verificationBadge = verification
      ? '<span class="badge ' + verificationBadgeClass(verification.status) + '" title="' + OS.esc((verification.checks && verification.checks.fileAvailable ? 'File available · ' : '') + (verification.hash_verified ? 'Checksum verified' : 'No published checksum')) + '">' + OS.esc(VERIFICATION_LABELS[verification.status] || verification.status) + '</span>'
      : '';
    var osMajor = minOSMajor(app);
    var icon = iconSrc(app.iconURL || app.icon);
    return '<article class="app-card os-lift os-icon-hover' + (conflict ? ' has-collision' : '') + '" data-slug="' + OS.esc(slug) + '" tabindex="0" role="button" aria-label="View ' + OS.esc(app.name) + ' details">' +
      '<div class="card-top">' +
        '<div class="icon-wrap">' +
          '<img class="app-icon os-icon" src="' + OS.esc(icon) + '" alt="" width="58" height="58" loading="lazy" onerror="this.onerror=null;this.src=\'' + OS.esc(OS.url('assets/OmniSource.png')) + '\'">' +
          '<span class="health-dot' + (online ? '' : ' down') + '" title="' + (online ? 'Download online' : 'Download currently unavailable') + '"></span>' +
        '</div>' +
        '<div class="card-identity">' +
          '<h3><a href="' + OS.esc(OS.url('apps/' + slug + '/')) + '" title="Open ' + OS.esc(app.name) + ' detail page">' + OS.esc(app.name) + '</a></h3>' +
          '<p class="card-dev">' + OS.esc(app.developerName || app.subtitle || 'Independent developer') + '</p>' +
        '</div>' +
        '<button class="favorite' + (state.favorites.has(slug) ? ' active' : '') + '" type="button" data-favorite="' + OS.esc(slug) + '" aria-label="' + (state.favorites.has(slug) ? 'Remove from' : 'Add to') + ' saved apps" title="Save app" aria-pressed="' + state.favorites.has(slug) + '">' +
          '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 20.5S4.5 16.1 4.5 10A4.5 4.5 0 0 1 12 7.2a4.5 4.5 0 0 1 7.5 2.8c0 6.1-7.5 10.5-7.5 10.5Z"/></svg>' +
        '</button>' +
      '</div>' +
      '<div class="card-badges">' + statusBadge + provenanceBadge + healthBadge + verificationBadge + collisionBadge + staleBadge + '</div>' +
      '<p class="app-description">' + OS.esc(app.subtitle || app.localizedDescription || 'View app details and installation options.') + '</p>' +
      '<div class="card-meta">' +
        '<span class="meta-item"><b>v' + OS.esc(app.version || '—') + '</b></span>' +
        (osMajor !== null ? '<span class="meta-item">iOS ' + osMajor + '+</span>' : '') +
        '<span class="meta-item">' + OS.esc(OS.fmtBytes(app.size)) + '</span>' +
        '<span class="meta-item" title="' + OS.esc(OS.fmtDate(app.versionDate)) + '">' + OS.esc(OS.timeAgo(app.versionDate)) + '</span>' +
      '</div>' +
      '<div class="card-bottom">' +
        '<div class="card-mini"><b>' + OS.esc(categoryLabel(app.category)) + '</b></div>' +
        '<div class="card-actions">' +
          '<a class="page-link" href="' + OS.esc(OS.url('apps/' + slug + '/')) + '" title="Open the static detail page">PAGE ↗</a>' +
          '<button class="get-button" type="button" data-open="' + OS.esc(slug) + '">VIEW <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6"/></svg></button>' +
        '</div>' +
      '</div></article>';
  }

  /* ------------------------------------------------------ catalog bindings
     Delegated interaction handlers for the catalog: attached to `document`
     (which always exists) instead of to the grid, and attached on DOM-ready
     instead of at the end of the data pipeline.

     They used to live inside Home.renderCatalog(), which only runs once
     apps.json, every first-paint feed and catalog.json have resolved. The
     deferred feeds already repaint the catalog through refreshPage() by then,
     so the cards were on screen with no listener attached at all: tapping VIEW
     did nothing until the rest of the boot chain finished - a second or two on
     a fast connection, the full fetch timeout on a slow one, and for the rest
     of the session if any earlier renderer threw (the boot catch clears the
     grid, a deferred refresh then repaints it unbound). Delegating on document
     and binding before the first request removes the ordering dependency
     entirely: a painted card is an interactive card. */
  function bindCatalog() {
    var root = document.documentElement;
    if (root.dataset.osCatalogBound) return;
    root.dataset.osCatalogBound = '1';

    // New discovery tabs — category + provenance quick filters
    function syncDiscoveryTabs() {
      var cat = state.category || 'all';
      var prov = state.provenance || 'all';
      // Compute counts for category tabs
      var counts = {};
      state.apps.forEach(function (app) {
        var c = app.category || 'other';
        counts[c] = (counts[c] || 0) + 1;
      });
      counts['all'] = state.apps.length;
      counts['favorites'] = state.favorites.size;
      document.querySelectorAll('.catalog-tab[data-tab-category]').forEach(function (btn) {
        var active = btn.dataset.tabCategory === cat;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
        var count = counts[btn.dataset.tabCategory];
        if (count != null) {
          var existing = btn.querySelector('.tab-count');
          if (existing) {
            existing.textContent = String(count);
          } else if (btn.dataset.tabCategory !== 'all') {
            // Add count badge if not present for non-all tabs
            var span = document.createElement('span');
            span.className = 'tab-count';
            span.textContent = String(count);
            btn.appendChild(span);
          }
        }
      });
      document.querySelectorAll('.subtab[data-tab-provenance]').forEach(function (btn) {
        var active = btn.dataset.tabProvenance === prov;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', String(active));
      });
      var countAll = document.getElementById('tabCountAll');
      if (countAll) countAll.textContent = String(state.apps.length);
    }
    // Expose for other renderers
    Home.syncDiscoveryTabs = syncDiscoveryTabs;

    // Quick section tabs — highlight based on scroll
    function setupQuickNavScrollSpy() {
      var quickNav = document.getElementById('quickNav');
      if (!quickNav) return;
      var sectionIds = ['trending', 'recent', 'featured', 'catalog', 'whats-new'];
      var quickTabs = quickNav.querySelectorAll('.quick-tab[data-section]');
      if (!('IntersectionObserver' in window) || !quickTabs.length) return;
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var id = entry.target.id;
            quickTabs.forEach(function (tab) {
              var isActive = tab.dataset.section === id;
              tab.classList.toggle('is-active', isActive);
              if (isActive) tab.setAttribute('aria-current', 'true');
              else tab.removeAttribute('aria-current');
            });
          }
        });
      }, { rootMargin: '-40% 0px -50% 0px', threshold: 0 });
      sectionIds.forEach(function (sid) {
        var sec = document.getElementById(sid);
        if (sec) observer.observe(sec);
      });
    }
    // Run once after DOM ready
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', setupQuickNavScrollSpy);
    } else {
      setupQuickNavScrollSpy();
    }

    document.addEventListener('click', function (event) {
      var quickTab = event.target.closest ? event.target.closest('.quick-tab[data-section]') : null;
      if (quickTab) {
        var secId = quickTab.dataset.section;
        var sec = document.getElementById(secId);
        if (sec) {
          if (sec.hidden) {
            // Section not yet loaded — scroll to catalog as fallback and show hint
            // The deferred anchor watcher in core.js will handle the queued jump when it appears
            return; // let default anchor + deferred watcher handle it
          }
          // Smooth scroll with header offset
          event.preventDefault();
          try {
            sec.scrollIntoView({ behavior: OS.reducedMotion ? 'auto' : 'smooth', block: 'start' });
          } catch (e) {
            sec.scrollIntoView();
          }
          document.querySelectorAll('.quick-tab[data-section]').forEach(function (t) {
            t.classList.toggle('is-active', t === quickTab);
          });
          return;
        }
      }
      var catTab = event.target.closest ? event.target.closest('[data-tab-category]') : null;
      if (catTab) {
        var newCat = catTab.dataset.tabCategory;
        if (newCat === 'favorites') {
          state.category = 'favorites';
        } else {
          state.category = newCat;
        }
        Home.renderFilters();
        syncDiscoveryTabs();
        Home.filterAndRender();
        // Scroll to catalog if not already visible
        var catalog = document.getElementById('catalog');
        if (catalog) {
          var rect = catalog.getBoundingClientRect();
          if (rect.top > window.innerHeight * 0.6 || rect.top < -200) {
            catalog.scrollIntoView({ behavior: OS.reducedMotion ? 'auto' : 'smooth', block: 'start' });
          }
        }
        return;
      }
      var provTab = event.target.closest ? event.target.closest('[data-tab-provenance]') : null;
      if (provTab) {
        state.provenance = provTab.dataset.tabProvenance;
        Home.renderFilters();
        syncDiscoveryTabs();
        Home.filterAndRender();
        return;
      }
    });


    var searchInput = $('#searchInput');
    if (searchInput && !searchInput._osBound) {
      // Debounced: filtering 94 cards is cheap, rebuilding the DOM on every
      // keystroke still causes layout churn and drops hover states.
      searchInput._osBound = true;
      var searchTimer = null;
      searchInput.addEventListener('input', function () {
        var value = this.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          state.query = value;
          Home.filterAndRender();
        }, 110);
      });
      searchInput.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && state.query) {
          clearTimeout(searchTimer);
          state.query = '';
          searchInput.value = '';
          Home.filterAndRender();
        }
      });
    }

    document.addEventListener('change', function (event) {
      var select = event.target;
      if (!select || !select.matches) return;
      if (select.matches('#sortSelect')) {
        state.sort = select.value;
        Home.filterAndRender();
        return;
      }
      if (select.matches('#osSelect')) {
        state.os = select.value === 'any' ? 'any' : Number(select.value);
        Home.filterAndRender();
      }
    });

    document.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.closest) return;

      if (target.closest('#clearFilters') || target.closest('#emptyClear')) {
        Home.clearFilters();
        return;
      }

      /* Hero shelf pills carry the same category state as the catalog tabs,
         but they are a shortcut from the top of the page: picking one also
         takes the reader down to the grid it just filtered. */
      var shelf = target.closest('[data-hero-category]');
      if (shelf) {
        state.category = shelf.dataset.id || 'all';
        Home.renderFilters();
        Home.filterAndRender();
        var catalogSection = $('#catalog');
        if (catalogSection && catalogSection.scrollIntoView) {
          catalogSection.scrollIntoView({ behavior: OS.reducedMotion ? 'auto' : 'smooth', block: 'start' });
        }
        return;
      }

      var chip = target.closest('[data-kind]');
      // The category row moved out of .filter-groups when it became the
      // sticky tablist, so both homes count.
      if (chip && (chip.closest('.filter-groups') || chip.closest('.catalog-tabs'))) {
        if (chip.dataset.kind === 'category') state.category = state.category === chip.dataset.id ? 'all' : chip.dataset.id;
        if (chip.dataset.kind === 'status') state.statusFilter = state.statusFilter === chip.dataset.id ? 'all' : chip.dataset.id;
        if (chip.dataset.kind === 'provenance') state.provenance = state.provenance === chip.dataset.id ? 'all' : chip.dataset.id;
        Home.renderFilters();
        Home.filterAndRender();
        return;
      }

      var grid = $('#appsGrid');
      if (!grid || !grid.contains(target)) return;

      var favorite = target.closest('[data-favorite]');
      if (favorite) {
        event.preventDefault();
        event.stopPropagation();
        var saved = favorite.dataset.favorite;
        if (state.favorites.has(saved)) state.favorites.delete(saved); else state.favorites.add(saved);
        saveFavorites();
        Home.renderFilters(); // refresh the "Saved" chip count
        Home.filterAndRender();
        OS.toast(state.favorites.has(saved) ? 'Saved for later' : 'Removed from saved apps');
        return;
      }

      var slug = catalogTargetSlug(target);
      if (slug) Home.openApp(slug);
    });

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      var target = event.target;
      if (!target || !target.matches || !target.matches('.app-card')) return;
      event.preventDefault();
      Home.openApp(target.dataset.slug);
    });

    /* Roving tabindex for the catalog grid.
       Every card carries a title link, a save button, a detail link and a
       download button, so a plain tab order through the catalog was 505 stops
       (101 cards x 5) on the home page alone — the report measured 725 tabbable
       elements overall. The grid now behaves like the category tablist above
       it: one tab stop for the whole grid, arrow keys (or Home/End) to move
       between cards, and the card you are on brings its own controls along. */
    var gridCursor = 0;

    function gridCards() {
      var grid = $('#appsGrid');
      return grid ? $$('.app-card', grid) : [];
    }

    function paintGridRoving(moveFocus) {
      var cards = gridCards();
      if (!cards.length) { gridCursor = 0; return; }
      if (gridCursor < 0 || gridCursor >= cards.length) gridCursor = 0;
      cards.forEach(function (card, index) {
        var current = index === gridCursor;
        card.tabIndex = current ? 0 : -1;
        // Inner controls ride along with their card: a heart or download button
        // on a card the reader has not reached is not a tab stop either.
        $$('a, button, input, select', card).forEach(function (control) {
          if (current) control.removeAttribute('tabindex');
          else control.setAttribute('tabindex', '-1');
        });
      });
      if (moveFocus) {
        try { cards[gridCursor].focus(); } catch (error) { /* detached frame */ }
      }
    }

    // Exposed so every render path (filters, sort, search, favourites,
    // deferred feeds, language change) can restore the single tab stop.
    OS.syncGridRoving = paintGridRoving;

    document.addEventListener('focusin', function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      var card = target.closest('#appsGrid .app-card');
      if (!card) return;
      var index = gridCards().indexOf(card);
      if (index !== -1 && index !== gridCursor) {
        gridCursor = index;
        paintGridRoving(false);
      }
    });

    document.addEventListener('keydown', function (event) {
      var key = event.key;
      if (['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Home', 'End'].indexOf(key) === -1) return;
      var current = event.target;
      if (!current || !current.closest) return;
      var card = current.closest('#appsGrid .app-card');
      if (!card) return;
      var cards = gridCards();
      var index = cards.indexOf(card);
      if (index === -1 || cards.length < 2) return;
      var next = null;
      if (key === 'Home') next = cards[0];
      else if (key === 'End') next = cards[cards.length - 1];
      else if (key === 'ArrowRight' || key === 'ArrowLeft') {
        var rtl = document.documentElement.dir === 'rtl';
        var forward = rtl ? key === 'ArrowLeft' : key === 'ArrowRight';
        next = cards[index + (forward ? 1 : -1)] || null;
      } else {
        // Vertical movement goes to the *nearest card in the row above/below*,
        // so a filtered grid (2 cards in the last row) still behaves.
        var step = key === 'ArrowDown' ? 1 : -1;
        var rowTop = card.offsetTop;
        for (var i = index + step; i >= 0 && i < cards.length; i += step) {
          if (Math.abs(cards[i].offsetTop - rowTop) > 4) { next = cards[i]; break; }
        }
      }
      if (!next) return;
      event.preventDefault();
      gridCursor = cards.indexOf(next);
      paintGridRoving(true);
    });

    /* Arrow-key navigation for the category tablist. The chips are real
       buttons, so Enter/Space already activate them, but a tablist also owes
       its keyboard users Home/End and left/right — and the roving tabindex
       renderFilters() paints means the whole row is a single tab stop, so
       without this the only way across it would be to activate every shelf. */
    document.addEventListener('keydown', function (event) {
      if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].indexOf(event.key) === -1) return;
      var current = event.target;
      if (!current || !current.closest) return;
      var list = current.closest('#categoryFilters');
      if (!list) return;
      var tabs = $$('[role="tab"]', list);
      var index = tabs.indexOf(current);
      if (tabs.length < 2 || index === -1) return;
      event.preventDefault();
      var next;
      if (event.key === 'Home') next = tabs[0];
      else if (event.key === 'End') next = tabs[tabs.length - 1];
      else {
        // Arrows follow the reading direction: the site ships Arabic, where
        // "right" is the previous tab.
        var forward = (event.key === 'ArrowRight') !== (document.documentElement.dir === 'rtl');
        next = tabs[(index + (forward ? 1 : -1) + tabs.length) % tabs.length];
      }
      if (next && next.focus) next.focus();
    });
  }

  /* Resolve what a click inside the catalog should open: the explicit VIEW
     button first (data-open), then the card around it (data-slug). Links
     inside the card - the app name, PAGE - keep navigating. */
  function catalogTargetSlug(target) {
    var trigger = target.closest('[data-open]');
    if (trigger && trigger.dataset.open) return trigger.dataset.open;
    if (target.closest('a')) return '';
    var card = target.closest('[data-slug]');
    return card && card.dataset.slug ? card.dataset.slug : '';
  }

  /* ------------------------------------------------------------- home page */
  var Home = {
    render: function () {
      // Each section is isolated on purpose: a hero that used to throw took
      // the catalog down with it, and the deferred feeds then repainted a grid
      // nobody had finished wiring.
      ['renderHero', 'renderHeroCategories', 'renderRails', 'renderMetrics', 'renderSourceHealth', 'renderInstallGuide',
        'renderCatalog', 'renderTimeline', 'renderFooterClients'].forEach(function (name) {
        try {
          this[name]();
        } catch (error) {
          console.error('OmniSource: ' + name + ' failed', error);
        }
      }, this);
    },

    renderHero: function () {
      var total = state.apps.length;
      var totals = state.analytics ? state.analytics.totals || {} : {};
      var healthy = state.health && state.health.totals ? state.health.totals.reachable : state.apps.filter(function (a) { return healthFor(a).downloadReachable !== false; }).length;
      var verified = totals.verifiedApps != null ? totals.verifiedApps : state.apps.filter(function (a) { return verificationFor(a) && verificationFor(a).status === 'VERIFIED'; }).length;
      var sources = totals.sources != null ? totals.sources : new Set(state.apps.map(function (a) {
        return (a.omnisource && (a.omnisource.upstreamURL || (a.omnisource.verification && a.omnisource.verification.publisher))) || a.developerName;
      })).size;
      var lastSync = (totals.lastSync !== undefined ? totals.lastSync : (state.analytics ? state.analytics.lastSync : null)) || null;

      function setStat(id, value, title) {
        var node = $('#' + id);
        if (!node) return;
        node.dataset.countValue = value;
        node.title = title || '';
        // The count-up observer may have already fired on the static "0";
        // force a fresh animation toward the real value.
        OS.animateCount(node, true);
      }
      setStat('statApps', total, 'Apps in the catalog');
      setStat('statSources', sources, sources + ' upstream sources');
      setStat('statOnline', healthy + '/' + total, healthy + ' of ' + total + ' downloads reachable');
      setStat('statVerified', verified + '/' + total, verified + ' of ' + total + ' apps verified');

      var syncLabel = $('#statSyncLabel');
      if (syncLabel && lastSync) syncLabel.textContent = 'last sync ' + OS.timeAgo(lastSync);

      var label = $('#healthLabel');
      if (label) {
        if (total === 0) {
          label.textContent = 'Source status unavailable';
        } else if (healthy === total) {
          label.textContent = 'All ' + total + ' downloads verified online';
        } else {
          label.textContent = healthy + ' of ' + total + ' downloads online';
          var pill = $('#healthPill');
          if (pill) pill.classList.add('is-error');
          var dot = $('#healthDot');
          if (dot) dot.classList.add('is-down');
        }
      }

      var sourceUrl = $('#sourceUrl');
      var sourceHref = OS.ROOT.replace(/\/$/, '') + '/apps.json';
      if (sourceUrl) sourceUrl.textContent = sourceHref;
      var sourceUrlLink = $('#sourceUrlLink');
      if (sourceUrlLink) sourceUrlLink.href = sourceHref;

      // Client chips in the hero.
      var row = $('#clientButtons');
      if (row) {
        row.innerHTML = '<span class="client-hint">' + OS.esc(OS.t('hero.sourceHint', null, 'Add the source in your client')) + '</span>' +
          state.clients.map(function (client) {
            return clientButton(client, OS.ROOT.replace(/\/$/, '') + '/apps.json');
          }).join('');
      }
    },

    /* Quick shelves in the hero: the six biggest categories, one tap from the
       top of the page. A first-time visitor should not have to scroll past
       four rails to find out what the catalog holds, and tapping a shelf
       lands on the matching category tab further down, so the two never
       disagree. */
    renderHeroCategories: function () {
      var row = $('#heroCategories');
      if (!row || !state.apps.length) return;
      var counts = new Map();
      state.apps.forEach(function (app) {
        var cat = app.category || 'other';
        counts.set(cat, (counts.get(cat) || 0) + 1);
      });
      var top = Array.from(counts.entries())
        .sort(function (a, b) { return b[1] - a[1] || categoryLabel(a[0]).localeCompare(categoryLabel(b[0])); })
        .slice(0, 6);
      // Rebuilding on every deferred feed would drop focus and replay the hero
      // animation for nothing: repaint only when the shelves (or their labels)
      // actually change.
      var lang = (window.OmniI18n && window.OmniI18n.language) || 'en';
      var signature = lang + '|' + top.map(function (pair) { return pair[0] + ':' + pair[1]; }).join('|');
      if (row.dataset.signature === signature) { this.syncHeroCategories(); return; }
      row.dataset.signature = signature;
      row.innerHTML = '<span class="hero-cats-label">' +
          '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/></svg>' +
          '<span data-i18n="tabs.browse">' + OS.esc(OS.t('tabs.browse', null, 'Browse')) + '</span>' +
        '</span>' +
        top.map(function (pair) {
          return '<button type="button" class="chip" data-hero-category data-kind="category" data-id="' + OS.esc(pair[0]) + '">' +
            '<span>' + OS.esc(categoryLabel(pair[0])) + '</span><span class="count">' + pair[1] + '</span></button>';
        }).join('');
      row.hidden = false;
      this.syncHeroCategories();
    },

    syncHeroCategories: function () {
      var row = $('#heroCategories');
      if (!row) return;
      $$('[data-hero-category]', row).forEach(function (pill) {
        pill.classList.toggle('active', pill.dataset.id === state.category);
      });
    },

    renderRails: function () {
      function fill(railId, sectionId, html) {
        var rail = $('#' + railId);
        var section = $('#' + sectionId);
        if (!rail) return;
        if (!html) { if (section) section.hidden = true; return; }
        rail.innerHTML = html;
        if (section) section.hidden = false;
      }

      // Trending — ranked by the pipeline's score.
      var trendingHtml = '';
      if (state.trending && state.trending.trending) {
        trendingHtml = state.trending.trending.slice(0, 10).map(function (item) {
          return railCard(item.slug, { score: item.score });
        }).join('');
      }
      fill('trendingRail', 'trending', trendingHtml);

      // Recently updated.
      var recentHtml = '';
      if (state.trending && state.trending.recentlyUpdated) {
        recentHtml = state.trending.recentlyUpdated.slice(0, 10).map(function (item) {
          return railCard(item.slug);
        }).join('');
      }
      fill('recentRail', 'recent', recentHtml);

      // Verified.
      var verifiedHtml = state.apps
        .filter(function (app) {
          var v = verificationFor(app);
          return v && v.status === 'VERIFIED';
        })
        .slice(0, 10)
        .map(function (app) { return railCard(slugFor(app)); })
        .join('');
      fill('verifiedRail', 'verifiedApps', verifiedHtml);

      // Featured — editorial cards.
      var featured = state.apps.filter(function (app) { return app.omnisource && app.omnisource.featured; }).slice(0, 8);
      var featuredHtml = featured.map(function (app, i) {
        return featuredCard(slugFor(app), i + 1);
      }).join('');
      fill('featuredRail', 'featured', featuredHtml);
    },

    renderMetrics: function () {
      var grid = $('#metricsGrid');
      if (!grid) return;
      var section = $('#statistics');
      var totals = state.analytics ? state.analytics.totals || {} : {};
      var intel = state.downloadIntel || {};
      var summary = intel.summary || {};
      var verified = totals.verifiedApps != null ? totals.verifiedApps : 0;
      var total = state.apps.length;
      var metrics = [
        { hint: 'Live ranking', value: (state.trending && state.trending.trending ? state.trending.trending.length : 0), label: 'Trending apps' },
        { hint: 'New this month', value: (state.trending && state.trending.rising ? state.trending.rising.length : 0), label: 'Rising apps' },
        { hint: 'Past 60 days', value: (state.trending && state.trending.recentlyUpdated ? state.trending.recentlyUpdated.length : 0), label: 'Recently updated' },
        { hint: (total ? Math.round(verified / total * 100) : 0) + '% of catalog', value: verified, label: 'Verified apps' },
        { hint: (summary.probes || 0) + ' probes / 30d', value: (summary.averageAvailability != null ? summary.averageAvailability + '%' : '—'), label: 'Avg. availability' },
        { hint: 'Across all upstreams', value: (summary.averageResponseTimeMs != null ? summary.averageResponseTimeMs + ' ms' : '—'), label: 'Avg. response time' },
        { hint: 'Primary + fallbacks', value: summary.mirrorCount != null ? summary.mirrorCount : '—', label: 'Download mirrors' },
        { hint: 'Score ≥ 85', value: (state.reputation && state.reputation.sources ? state.reputation.sources.filter(function (s) { return s.level === 'TRUSTED'; }).length : 0), label: 'Trusted sources' }
      ];
      grid.innerHTML = metrics.map(function (m, i) {
        return '<div class="metric-card" data-reveal style="--reveal-delay:' + (i * 45) + 'ms">' +
          '<small>' + OS.esc(m.hint) + '</small><strong>' + OS.esc(String(m.value)) + '</strong><span>' + OS.esc(m.label) + '</span></div>';
      }).join('');
      if (section) section.hidden = false;
      // Re-observe newly revealed nodes.
      $$('#metricsGrid [data-reveal]').forEach(function (n) { n.classList.add('is-revealed'); });
    },

    renderSourceHealth: function () {
      var section = $('#sourceHealth');
      var grid = $('#sourceGrid');
      if (!section || !grid) return;
      var sources = state.reputation && state.reputation.sources ? state.reputation.sources : [];
      if (!sources.length) { section.hidden = true; return; }
      grid.innerHTML = sources.slice(0, 9).map(function (source, i) {
        var level = source.level || 'EXPERIMENTAL';
        var srcUrl = source.sourceURL ? OS.cleanUrl(source.sourceURL) : '';
        var nameHtml = (srcUrl && srcUrl !== '#')
          ? '<a class="source-link" href="' + OS.esc(srcUrl) + '" target="_blank" rel="noopener" title="Open the source repo/feed">' + OS.esc(source.source) + '</a>'
          : OS.esc(source.source);
        return '<article class="source-card os-lift" data-reveal style="--reveal-delay:' + (i * 45) + 'ms">' +
          '<span class="rep-badge ' + OS.esc(level.toLowerCase()) + '">' + OS.esc(level) + '</span>' +
          '<div class="name">' + nameHtml + '</div>' +
          '<div class="metric"><span>Score</span><b>' + (source.score != null ? source.score.toFixed(1) : '—') + ' / 100</b></div>' +
          '<div class="metric"><span>Uptime</span><b>' + (source.metrics && source.metrics.uptime != null ? source.metrics.uptime : 0) + '%</b></div>' +
          '<div class="metric"><span>Avg latency</span><b>' + (source.metrics && source.metrics.averageLatencyMs != null ? source.metrics.averageLatencyMs + ' ms' : '—') + '</b></div>' +
          '<div class="metric"><span>Update gap</span><b>' + (source.metrics && source.metrics.averageUpdateGapDays != null ? source.metrics.averageUpdateGapDays + ' d' : '—') + '</b></div>' +
          '<div class="apps">' + source.apps.length + ' app' + (source.apps.length === 1 ? '' : 's') + '</div>' +
          '</article>';
      }).join('');
      section.hidden = false;
      $('#sourceHealthMore') ? ($('#sourceHealthMore').hidden = false) : null;
      $$('#sourceHealth [data-reveal]').forEach(function (n) { n.classList.add('is-revealed'); });
    },

    renderInstallGuide: function () {
      var section = $('#installGuide');
      if (!section) return;
      var chips = $('#guideClients');
      if (chips) {
        chips.innerHTML = state.clients.map(function (client) {
          return clientButton(client, OS.ROOT.replace(/\/$/, '') + '/apps.json');
        }).join('');
      }
      section.hidden = false;
    },

    /* ---- catalog --------------------------------------------------------- */
    renderCatalog: function () {
      bindCatalog();
      this.renderFilters();
      this.filterAndRender();
    },

    renderFilters: function () {
      /* Every row below is rebuilt from scratch, which throws away the chip
         the user just activated. Mouse users never notice, but a keyboard
         user tabbing across the filter chips was dropped back to the top of
         the document on every single press. Remember which chip had focus and
         hand it back once the new nodes exist. */
      var focused = document.activeElement;
      var focusKind = focused && focused.dataset && focused.dataset.kind ? focused.dataset.kind : null;
      var focusId = focusKind ? focused.dataset.id : null;
      /* Scope the re-focus to the row the chip came from. The hero now offers
         the same categories as the catalog tabs, and a document-wide scan
         would hand focus to the top of the page instead of back to the row
         the reader is actually working in. */
      var focusScope = (focused && focused.closest && focused.closest('.filter-row')) || document;

      var categoryCounts = new Map();
      var statusCounts = new Map();
      var provenanceCounts = { official: 0, community: 0 };
      state.apps.forEach(function (app) {
        var cat = app.category || 'other';
        categoryCounts.set(cat, (categoryCounts.get(cat) || 0) + 1);
        var status = (app.omnisource && app.omnisource.status) || 'stable';
        statusCounts.set(status, (statusCounts.get(status) || 0) + 1);
        provenanceCounts[provenanceFor(app)]++;
      });

      var provenanceRow = $('#provenanceFilters');
      if (provenanceRow) {
        var provenanceFilters = [
          { id: 'all', label: 'All builds', count: state.apps.length },
          { id: 'official', label: 'Official', count: provenanceCounts.official },
          { id: 'community', label: 'Community', count: provenanceCounts.community }
        ];
        provenanceRow.innerHTML = provenanceFilters.map(function (filter) {
          return '<button type="button" class="chip' + (state.provenance === filter.id ? ' active' : '') + '" data-kind="provenance" data-id="' + OS.esc(filter.id) + '">' +
            '<span>' + OS.esc(filter.label) + '</span><span class="count">' + filter.count + '</span></button>';
        }).join('');
      }

      var categories = Array.from(categoryCounts.keys()).sort(function (a, b) {
        return categoryLabel(a).localeCompare(categoryLabel(b));
      });
      var catFilters = [
        { id: 'all', label: 'All apps', count: state.apps.length },
        { id: 'favorites', label: 'Saved', count: state.favorites.size }
      ].concat(categories.map(function (id) {
        return { id: id, label: categoryLabel(id), count: categoryCounts.get(id) };
      }));
      var catRow = $('#categoryFilters');
      if (catRow) {
        /* A tab id is built from catalog data, so it is reduced to a safe
           character set rather than escaped — an id cannot contain a space or
           a quote and still round-trip through aria-labelledby. */
        var selectedId = catFilters.some(function (filter) { return filter.id === state.category; })
          ? state.category
          : 'all';
        // A stale ?category= (or a shelf that emptied out) must not leave the
        // tablist with no selected tab: the grid and the tabs would disagree
        // and the row would have no tab stop at all.
        if (selectedId !== state.category) state.category = selectedId;
        catRow.innerHTML = catFilters.map(function (filter) {
          var selected = filter.id === selectedId;
          return '<button type="button" role="tab" id="' + tabIdFor(filter.id) + '"' +
            ' class="chip' + (selected ? ' active' : '') + '"' +
            ' aria-selected="' + (selected ? 'true' : 'false') + '"' +
            ' aria-controls="catalogPanel"' +
            ' tabindex="' + (selected ? '0' : '-1') + '"' +
            ' data-kind="category" data-id="' + OS.esc(filter.id) + '">' +
            '<span>' + OS.esc(filter.label) + '</span><span class="count">' + filter.count + '</span></button>';
        }).join('');
      }

      var statuses = Array.from(statusCounts.keys()).sort();
      var statusFilters = [{ id: 'all', label: 'Any status', count: state.apps.length }].concat(statuses.map(function (id) {
        return { id: id, label: statusLabel(id), count: statusCounts.get(id) };
      }));
      var statusRow = $('#statusFilters');
      if (statusRow) {
        statusRow.innerHTML = statusFilters.map(function (filter) {
          return '<button type="button" class="chip' + (state.statusFilter === filter.id ? ' active' : '') + '" data-kind="status" data-id="' + OS.esc(filter.id) + '">' +
            '<span>' + OS.esc(filter.label) + '</span><span class="count">' + filter.count + '</span></button>';
        }).join('');
      }

      var osLevels = Array.from(new Set(state.apps.map(minOSMajor).filter(function (v) { return Number.isFinite(v); }))).sort(function (a, b) { return a - b; });
      var osSelect = $('#osSelect');
      if (osSelect && osLevels.length) {
        var current = osSelect.value;
        osSelect.innerHTML = '<option value="any">' + OS.esc(OS.t('catalog.anyOS', null, 'Any iOS')) + '</option>' +
          osLevels.map(function (level) {
            return '<option value="' + level + '">' + OS.esc(OS.t('catalog.worksOnIOS', { level: level }, 'Works on iOS ${level}+')) + '</option>';
          }).join('');
        osSelect.value = osLevels.indexOf(Number(current)) !== -1 ? String(current) : 'any';
      }

      if (focusKind) {
        /* Match on the dataset properties rather than building an attribute
           selector out of them. A chip id is catalog data, so interpolating it
           into a selector string would need escaping that is easy to get
           subtly wrong (escaping `"` but not `\` leaves the string breakable)
           — comparing values has no such surface at all. */
        var chips = focusScope.querySelectorAll('[data-kind][data-id]');
        for (var ci = 0; ci < chips.length; ci += 1) {
          if (chips[ci].dataset.kind === focusKind && chips[ci].dataset.id === focusId) {
            if (chips[ci].focus) chips[ci].focus();
            break;
          }
        }
      }

      // Keep new discovery tabs in sync with chip state
      try {
        if (Home.syncDiscoveryTabs) Home.syncDiscoveryTabs();
      } catch (e) { /* ignore */ }
    },

    filteredApps: function () {
      // The search box supports a few operators (documented in the ⌘K panel):
      // status:beta, category:music, source:vault, provenance:community,
      // version:1.2, updated:>30d / updated:<7d. Anything else is free text.
      var raw = state.query.toLowerCase().trim();
      var operators = {};
      var freeParts = [];
      raw.split(/\s+/).forEach(function (token) {
        if (!token) return;
        var m = token.match(/^([a-z]+):(.+)$/);
        if (m && ['status', 'category', 'source', 'provenance', 'version', 'updated'].indexOf(m[1]) !== -1) {
          (operators[m[1]] = operators[m[1]] || []).push(m[2]);
        } else {
          freeParts.push(token);
        }
      });
      var freeQuery = freeParts.join(' ');
      var now = Date.now();
      var result = state.apps.filter(function (app) {
        var slug = slugFor(app);
        var meta = app.omnisource || {};
        var disc = discoveryFor(app);
        var verification = verificationFor(app);
        var searchText = [
          app.name, app.subtitle, app.localizedDescription, app.developerName,
          app.bundleIdentifier, categoryLabel(app.category),
          meta.verification ? meta.verification.publisher : '',
          meta.status, slug,
          (disc && disc.tags ? disc.tags : []),
          verification ? verification.status : ''
        ].join(' ').toLowerCase();
        var categoryMatch = state.category === 'all' ||
          (state.category === 'favorites' ? state.favorites.has(slug) : app.category === state.category);
        var statusMatch = state.statusFilter === 'all' || ((meta.status || 'stable') === state.statusFilter);
        var provenanceMatch = state.provenance === 'all' || provenanceFor(app) === state.provenance;
        var osMajor = minOSMajor(app);
        var osMatch = state.os === 'any' || state.os === '' || osMajor === null || osMajor <= Number(state.os);
        if (!categoryMatch || !statusMatch || !provenanceMatch || !osMatch) return false;
        if (freeQuery && searchText.indexOf(freeQuery) === -1) return false;
        var matchAll = function (values, test) { return values.every(test); };
        if (operators.status && !matchAll(operators.status, function (v) { return (meta.status || 'stable') === v; })) return false;
        if (operators.provenance && !matchAll(operators.provenance, function (v) { return provenanceFor(app) === v; })) return false;
        if (operators.version && !matchAll(operators.version, function (v) { return String(app.version || '').toLowerCase().indexOf(v) !== -1; })) return false;
        if (operators.category && !matchAll(operators.category, function (v) {
          return app.category === v || categoryLabel(app.category).toLowerCase().indexOf(v) !== -1;
        })) return false;
        if (operators.source && !matchAll(operators.source, function (v) {
          var hay = String(meta.verification && meta.verification.publisher || '') + ' ' + String(app.developerName || '');
          return hay.toLowerCase().indexOf(v) !== -1;
        })) return false;
        if (operators.updated && !matchAll(operators.updated, function (v) {
          var days = (now - new Date(app.versionDate).getTime()) / 86400000;
          var mm = v.match(/^(>=?|<=?)\s*(\d+)\s*d?$/);
          if (!mm) return true;
          var n = Number(mm[2]);
          return mm[1].charAt(0) === '>' ? days > n : days < n;
        })) return false;
        return true;
      });
      result.sort(function (a, b) {
        if (state.sort === 'name') return a.name.localeCompare(b.name);
        if (state.sort === 'version') return String(b.version).localeCompare(String(a.version), undefined, { numeric: true });
        if (state.sort === 'updated') return String(b.versionDate).localeCompare(String(a.versionDate));
        if (state.sort === 'size') return (a.size || Infinity) - (b.size || Infinity);
        if (state.sort === 'downloads') {
          var da = (discoveryFor(a) || {}).downloads || 0;
          var db = (discoveryFor(b) || {}).downloads || 0;
          return (db - da) || String(b.versionDate).localeCompare(String(a.versionDate));
        }
        return Number(Boolean(b.omnisource && b.omnisource.featured)) - Number(Boolean(a.omnisource && a.omnisource.featured)) ||
          String(b.versionDate).localeCompare(String(a.versionDate));
      });
      return result;
    },

    filterAndRender: function () {
      var apps = this.filteredApps();
      var grid = $('#appsGrid');
      if (grid) {
        grid.setAttribute('aria-busy', 'false');
        grid.innerHTML = apps.map(appCard).join('');
        grid.hidden = apps.length === 0;
        // A fresh grid has tabindex="0" on every card; collapse it back to the
        // single tab stop the roving pattern promises (see bindCatalog).
        if (OS.syncGridRoving) OS.syncGridRoving(false);
      }
      var count = $('#resultCount');
      if (count) {
        // i18n-keys: catalog.results, catalog.resultsOne, catalog.resultsCompact, catalog.resultsOneCompact
        var compact = document.documentElement.dataset.view === 'compact';
        var key = apps.length === 1
          ? (compact ? 'catalog.resultsOneCompact' : 'catalog.resultsOne')
          : (compact ? 'catalog.resultsCompact' : 'catalog.results');
        var fallback = apps.length === 1
          ? (compact ? '1 app listed' : '1 app shown')
          : '${count} apps ' + (compact ? 'listed' : 'shown');
        count.textContent = OS.t(key, { count: apps.length }, fallback);
      }
      var filtered = state.category !== 'all' || state.statusFilter !== 'all' || state.provenance !== 'all' || state.os !== 'any' || Boolean(state.query);
      var clearButton = $('#clearFilters');
      if (clearButton) clearButton.hidden = !filtered;
      var empty = $('#emptyState');
      if (empty) empty.hidden = apps.length > 0;

      var groups = Array.from(state.collisions.values());
      var summary = $('#collisionSummary');
      if (summary) {
        if (groups.length) {
          var duplicated = new Set(groups.flat());
          summary.hidden = false;
          summary.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/></svg>' +
            '<span title="' + OS.esc(Array.from(state.collisions.keys()).join('\n')) + '">' + groups.length + ' bundle ID' + (groups.length === 1 ? '' : 's') + ' shared by ' + duplicated.size + ' apps</span>';
        } else {
          summary.hidden = true;
        }
      }
      // Filter chips are static per catalog; rebuilding them on every
      // keystroke discards nothing but wastes DOM work, so they render once
      // on load and explicitly when favorites change.
      var panel = $('#catalogPanel');
      if (panel) {
        // The grid is the panel the category tablist controls: name it after
        // the selected tab so a screen reader announces which shelf it is in.
        var activeTab = $('#categoryFilters [aria-selected="true"]');
        if (activeTab && activeTab.id) panel.setAttribute('aria-labelledby', activeTab.id);
        else panel.removeAttribute('aria-labelledby');
      }
      Home.syncHeroCategories();
      writeCategoryParam();
    },

    clearFilters: function () {
      state.query = '';
      state.category = 'all';
      state.statusFilter = 'all';
      state.provenance = 'all';
      state.os = 'any';
      var searchInput = $('#searchInput');
      if (searchInput) searchInput.value = '';
      var osSelect = $('#osSelect');
      if (osSelect) osSelect.value = 'any';
      this.renderFilters();
      try { if (this.syncDiscoveryTabs) this.syncDiscoveryTabs(); } catch (e) {}
      this.filterAndRender();
    },

    /* ---- timeline ---------------------------------------------------------- */
    renderTimeline: function () {
      var list = $('#updatesList');
      if (!list) return;
      var updates = (state.updates && state.updates.updates || []).slice(0, 8);
      var note = $('#updatesNote');
      if (!updates.length) {
        list.innerHTML = '';
        if (note) {
          note.hidden = false;
          note.textContent = 'No releases recorded yet — check back after the next sync.';
        }
        return;
      }
      list.innerHTML = updates.map(function (item, i) {
        var icon = iconSrc(item.iconURL || item.icon);
        var kind = KIND_LABELS[item.kind] || item.kind || 'Updated';
        var preview = (item.changelog || item.shortDescription || '').trim();
        var snippet = preview ? preview.slice(0, 220) : item.name + ' version ' + item.version + ' is available.';
        return '<li class="timeline-item" data-reveal style="--reveal-delay:' + (i * 50) + 'ms">' +
          '<img class="tl-icon" src="' + OS.esc(icon) + '" alt="" loading="lazy">' +
          '<div class="tl-card">' +
            '<div class="tl-head">' +
              '<button type="button" class="tl-name" data-open-app="' + OS.esc(item.slug) + '" title="Open ' + OS.esc(item.name) + '">' + OS.esc(item.name) + '</button>' +
              '<span class="badge ' + (item.kind === 'new' ? 'ok' : item.kind === 'updated' ? 'neutral' : 'manual') + '">' + OS.esc(kind) + '</span>' +
              '<span class="tl-date" title="' + OS.esc(OS.fmtDate(item.date)) + '">' + OS.esc(OS.timeAgo(item.date)) + '</span>' +
            '</div>' +
            '<p class="tl-preview">' + OS.esc(snippet) + (preview.length > 220 ? '…' : '') + '</p>' +
            '<div class="tl-foot">' +
              '<button type="button" class="version-chip" data-open-app="' + OS.esc(item.slug) + '">v' + OS.esc(item.version) + '</button>' +
              '<div class="tl-actions">' +
                '<button type="button" data-open-app="' + OS.esc(item.slug) + '">Details</button>' +
                '<a href="' + OS.esc(item.rssURL || '#') + '" title="Per-app RSS feed">RSS</a>' +
                (item.downloadURL ? '<a href="' + OS.esc(OS.cleanUrl(item.downloadURL)) + '" target="_blank" rel="noopener" title="Direct download">IPA</a>' : '') +
              '</div>' +
            '</div>' +
          '</div></li>';
      }).join('');
      if (note) note.hidden = true;
      // Delegate clicks so every "Details" / version chip keeps working
      // (a { once: true } listener would detach after the first click and
      // silently stop opening apps).
      if (!list.dataset.boundTimeline) {
        list.dataset.boundTimeline = '1';
        list.addEventListener('click', function (event) {
          var trigger = event.target.closest ? event.target.closest('[data-open-app]') : null;
          if (trigger) Home.openApp(trigger.dataset.openApp);
        });
      }
    },

    renderFooterClients: function () {
      var row = $('#footerClients');
      if (!row) return;
      row.innerHTML = state.clients.map(function (client) {
        var img = client.icon ? '<img src="' + OS.esc(OS.url('assets/' + client.icon)) + '" alt="">' : '';
        var deep = installUrlFor(client.id, OS.ROOT.replace(/\/$/, '') + '/apps.json');
        var inner = img + OS.esc(client.name);
        return deep
          ? '<a class="client-link" href="' + OS.esc(deep) + '">' + inner + '</a>'
          : '<button class="client-link" type="button" data-copy="' + OS.esc(OS.ROOT.replace(/\/$/, '') + '/apps.json') + '">' + inner + '</button>';
      }).join('');
    }
  };

  /* ------------------------------------------------------------ app dialog */
  function dialogChips(app) {
    var meta = app.omnisource || {};
    var status = meta.status || 'stable';
    var chips = ['<span class="badge ' + (status === 'stable' ? 'stable' : status) + '" title="Release status"><span class="dot"></span>' + OS.esc(statusLabel(status)) + '</span>'];
    var provenance = provenanceFor(app);
    chips.push(provenance === 'official'
      ? '<span class="badge verified" title="This IPA is published by the project&#39;s own upstream release channel">Official build</span>'
      : '<span class="badge community" title="This IPA is repackaged or mirrored by a community builder — provenance is listed under the Details tab">Community build</span>');
    var verification = verificationFor(app);
    if (verification) {
      chips.push('<span class="badge ' + verificationBadgeClass(verification.status) + '">' + OS.esc(VERIFICATION_LABELS[verification.status] || verification.status) + '</span>');
    }
    var conflict = collisionInfo(app);
    if (conflict) {
      chips.push('<span class="badge warn" title="Installing this app replaces the others on device.">⚠ Same bundle ID ×' + conflict.count + '</span>');
    }
    if (healthFor(app).stale && status !== 'unmaintained') chips.push('<span class="badge warn">Stale release</span>');
    return chips.join('');
  }

  function aboutPanel(app) {
    var screenshots = (app.screenshotURLs || []).filter(function (u) { return OS.cleanUrl(u) !== '#'; });
    return '<p class="about-text">' + OS.esc(app.localizedDescription || app.subtitle || 'No description provided.') + '</p>' +
      (screenshots.length
        ? '<div class="detail-section"><h3>Preview</h3><div class="screenshots">' +
          screenshots.map(function (u, i) {
            return '<img src="' + OS.esc(OS.cleanUrl(u)) + '" alt="' + OS.esc(app.name) + ' screenshot ' + (i + 1) + '" loading="lazy">';
          }).join('') + '</div></div>'
        : '');
  }

  function versionsPanel(app) {
    var versions = app.versions || [];
    if (!versions.length) return '<p class="detail-section"><span class="text-muted">No version history is published yet.</span></p>';
    return '<div>' + versions.map(function (version, index) {
      var current = index === 0;
      var notes = version.localizedDescription || '';
      var changelog = notes
        ? '<details class="v-changelog"><summary>Release notes</summary><div class="cl-body">' + OS.esc(notes) + '</div></details>'
        : '';
      return '<div class="version-row">' +
        '<div class="v-main">' +
          '<div class="v-head"><span class="v-ver">v' + OS.esc(version.version) + '</span>' +
            (current ? '<span class="v-tag">Current</span>' : '') +
            '<span class="v-date">' + OS.esc(OS.fmtDate(version.date)) + '</span></div>' +
          '<div class="v-meta"><span>' + OS.esc(OS.fmtBytes(version.size)) + '</span>' +
            (version.sha256 ? '<span class="sha" title="SHA-256 of the downloaded IPA"><code>' + OS.esc(version.sha256.slice(0, 12)) + '…</code><button type="button" data-copy="' + OS.esc(version.sha256) + '" aria-label="Copy SHA-256 checksum"><svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg></button></span>' : '') +
          '</div>' + changelog +
        '</div>' +
        '<div class="v-side"><a class="button primary" href="' + OS.esc(OS.cleanUrl(version.downloadURL)) + '" target="_blank" rel="noopener">Download</a></div>' +
      '</div>';
    }).join('') + '</div>';
  }

  function infoPanel(app) {
    var meta = app.omnisource || {};
    var compatibility = meta.compatibility || {};
    var verification = meta.verification || {};
    var health = healthFor(app);
    var version = (app.versions || [{}])[0];
    var osMajor = minOSMajor(app);
    // Values are escaped where they are built. The two cells that carry markup
    // of their own (the copy buttons) opt out through `raw`, which keeps the
    // rule at the render site simple: nothing reaches innerHTML unescaped by
    // accident. Feed-supplied fields (developer name, category, health detail)
    // used to be the exception - they are catalog data today, but they arrive
    // over the network, so they are escaped like everything else.
    var cells = [
      ['Version', 'v' + OS.esc(app.version || '—')],
      ['Updated', OS.esc(OS.fmtDate(app.versionDate))],
      ['Size', OS.esc(OS.fmtBytes(app.size))],
      ['Requires iOS', osMajor !== null ? OS.esc(osMajor + '+') : 'Not listed'],
      ['Category', OS.esc(categoryLabel(app.category))],
      ['Developer', OS.esc(app.developerName || '—')],
      {
        label: 'Bundle ID',
        raw: '<button type="button" data-copy="' + OS.esc(app.bundleIdentifier || '') + '">' + OS.esc(app.bundleIdentifier || '—') + ' <svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg></button>'
      },
      {
        label: 'Checksum',
        raw: version.sha256
          ? '<button type="button" data-copy="' + OS.esc(version.sha256) + '">' + OS.esc(version.sha256.slice(0, 16)) + '…</button>'
          : 'Not published'
      },
      ['Health', OS.esc((health.downloadReachable ? 'Online' : 'Unavailable') + (health.detail ? ' · ' + health.detail : ''))],
      ['Last release', OS.esc(OS.timeAgo(app.versionDate) + (health.updatedDaysAgo ? ' (' + health.updatedDaysAgo + 'd)' : ''))]
    ];
    var sourceNotes = compatibility.notes;
    var upstreamUrl = meta.upstreamURL ? OS.cleanUrl(meta.upstreamURL) : '#';
    // The sideload IPA source when it differs from the official project page.
    var sourceUrl = meta.sourceURL ? OS.cleanUrl(meta.sourceURL) : '';
    if (!sourceUrl || sourceUrl === '#' || sourceUrl === upstreamUrl) sourceUrl = '';
    var fallbacks = (app.fallbackDownloadURLs || []).filter(function (u) { return OS.cleanUrl(u) !== '#'; });
    var permissions = app.appPermissions || app.permissions || null;
    var entitlements = permissions ? permissions.entitlements : null;
    var privacy = permissions ? permissions.privacy : null;
    var privacyEntries = privacy ? Object.entries(privacy) : [];
    var legacyPerms = Array.isArray(app.permissions) ? app.permissions : [];
    return '<div class="info-grid">' + cells.map(function (cell) {
      if (cell.raw !== undefined) return '<div class="info-cell"><span>' + OS.esc(cell.label) + '</span><strong>' + cell.raw + '</strong></div>';
      return '<div class="info-cell"><span>' + OS.esc(cell[0]) + '</span><strong>' + cell[1] + '</strong></div>';
    }).join('') + '</div>' +
      '<div class="detail-section"><h3>Build provenance</h3>' +
      '<p class="body">Published by ' + OS.esc(verification.publisher || app.developerName || 'the upstream developer') + ' · ' +
      OS.esc(String(verification.method || 'upstream source').replace(/-/g, ' ')) + '.' +
      (verification.checksumPublished ? ' An official checksum is published.' : ' No upstream checksum is published.') +
      (meta.status === 'manual' ? '\n\nCommunity-built IPA of an official project that publishes no IPA itself — see compatibility notes.' : '') + '</p></div>' +
      (sourceNotes ? '<div class="detail-section"><h3>Compatibility &amp; source notes</h3><div class="detail-note">' + OS.esc(sourceNotes) + '</div></div>' : '') +
      ((entitlements || privacyEntries.length || legacyPerms.length)
        ? '<div class="detail-section"><h3>Permissions</h3><div class="perm-grid">' +
          (entitlements ? '<div class="perm"><b>Entitlements</b><span>' + OS.esc(entitlements.join(', ')) + '</span></div>' : '') +
          privacyEntries.map(function (entry) { return '<div class="perm"><b>' + OS.esc(entry[0]) + '</b><span>' + OS.esc(entry[1]) + '</span></div>'; }).join('') +
          legacyPerms.map(function (perm) { return '<div class="perm"><b>' + OS.esc(perm.type) + '</b><span>' + OS.esc(perm.usageDescription) + '</span></div>'; }).join('') +
        '</div></div>'
        : '') +
      (fallbacks.length
        ? '<div class="detail-section"><h3>Mirror download links</h3><p class="body">' + fallbacks.map(function (u) {
            return '<a href="' + OS.esc(OS.cleanUrl(u)) + '" target="_blank" rel="noopener">' + OS.esc(u) + '</a><br>';
          }).join('') + '</p></div>'
        : '') +
      '<div class="detail-section"><h3>Links</h3><div class="link-row">' +
        '<a href="' + OS.esc(OS.url('apps/' + slugFor(app) + '/')) + '" rel="noopener">Detail page ↗</a>' +
        '<a href="' + OS.esc(OS.cleanUrl(app.downloadURL)) + '" target="_blank" rel="noopener">Direct IPA ↗</a>' +
        '<a href="' + OS.esc(feedFor(app)) + '" target="_blank" rel="noopener">App feed ↗</a>' +
        '<a href="' + OS.esc(rssFor(app)) + '" target="_blank" rel="noopener">App RSS ↗</a>' +
        (sourceUrl ? '<a href="' + OS.esc(sourceUrl) + '" target="_blank" rel="noopener">Source ↗</a>' : '') +
        (upstreamUrl !== '#' ? '<a href="' + OS.esc(upstreamUrl) + '" target="_blank" rel="noopener">Upstream ↗</a>' : '') +
        '<button type="button" data-app-qr>QR code</button>' +
        '<button type="button" data-share>Share</button>' +
      '</div></div>';
  }

  function detailMarkup(app) {
    var sourceFeed = feedFor(app);
    var panels = { about: aboutPanel(app), versions: versionsPanel(app), info: infoPanel(app) };
    var visible = panels[state.activeTab] ? state.activeTab : 'about';
    var versionCount = (app.versions || []).length;
    var conflict = collisionInfo(app);
    return '<button class="dialog-close" type="button" data-close aria-label="Close details"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button>' +
      '<div class="dialog-hero">' +
        '<img class="dialog-icon" src="' + OS.esc(iconSrc(app.iconURL || app.icon)) + '" alt="" width="84" height="84">' +
        '<div class="dialog-titles">' +
          '<h2 id="dialogTitle">' + OS.esc(app.name) + '</h2>' +
          '<p class="dialog-sub">' + OS.esc(app.subtitle || 'By ' + (app.developerName || 'independent developer')) + '</p>' +
          '<div class="dialog-chips">' + dialogChips(app) + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="dialog-body">' +
        '<div class="install-grid">' +
          '<a class="button primary download" href="' + OS.esc(OS.cleanUrl(app.downloadURL)) + '" target="_blank" rel="noopener">⬇ Download IPA · ' + OS.esc(OS.fmtBytes(app.size)) + '</a>' +
          state.clients.map(function (client) { return clientButton(client, sourceFeed); }).join('') +
        '</div>' +
        (conflict
          ? '<div class="collision-note"><svg viewBox="0 0 24 24"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/></svg><span><b>Shared bundle ID.</b> ' + OS.esc(conflict.names) + ' also use <code>' + OS.esc(app.bundleIdentifier) + '</code> — installing one replaces the others on the same device. SideStore cannot add two of them from the master feed; add this app on its own with its single-app source instead.<br><button type="button" class="copy-feed-btn" data-copy="' + OS.esc(sourceFeed) + '" data-copy-msg="Single-app source copied — add it manually in your client">⧉ Copy single-app source link</button> <a class="copy-feed-link" href="' + OS.esc(sourceFeed) + '" target="_blank" rel="noopener">open feed ↗</a></span></div>'
          : '') +
        '<div class="tabs" role="tablist" aria-label="App details">' +
          [['about', 'About'], ['versions', 'Versions' + (versionCount > 1 ? ' <span class="count">' + versionCount + '</span>' : '')], ['info', 'Details']].map(function (tab) {
            return '<button type="button" class="tab' + (state.activeTab === tab[0] ? ' active' : '') + '" data-tab="' + tab[0] + '" role="tab" aria-selected="' + (state.activeTab === tab[0]) + '">' + tab[1] + '</button>';
          }).join('') +
        '</div>' +
        '<div id="panel-about"' + (visible === 'about' ? '' : ' hidden') + '>' + panels.about + '</div>' +
        '<div id="panel-versions"' + (visible === 'versions' ? '' : ' hidden') + '>' + panels.versions + '</div>' +
        '<div id="panel-info"' + (visible === 'info' ? '' : ' hidden') + '>' + panels.info + '</div>' +
      '</div>';
  }

  /* showModal()/close() are missing on older engines (Safari/iOS < 15), where
     calling them threw - which is one way a tap on VIEW could end with nothing
     on screen. The [open] attribute renders the dialog the same way, just
     non-modally, so every call site goes through these two. */
  function showModal(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function hideModal(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  /* Opening an app must never be a silent no-op. Three paths used to swallow
     the tap: the app not being in state.apps yet (the lookup just returned), a
     page without the dialog markup, and an engine without showModal(). Each one
     now falls through to the app's generated static detail page, which exists
     for every catalog entry and carries the same information. */
  Home.openApp = function (slug, tab) {
    if (!slug) return;
    var app = appForSlug(slug);
    if (!app) {
      if (!state.loaded) {
        // The feeds are still in flight: remember the tap and replay it once
        // they land (flushPendingOpen) instead of dropping it on the floor.
        state.pendingOpen = { slug: slug, tab: tab };
        return;
      }
      Home.openStaticPage(slug);
      return;
    }
    state.activeApp = app;
    state.activeTab = tab || 'about';
    var content = $('#dialogContent');
    var dialog = $('#appDialog');
    if (!content || !dialog) {
      Home.openStaticPage(slug);
      return;
    }
    try {
      content.innerHTML = detailMarkup(app);
    } catch (error) {
      console.error('OmniSource: detail render failed', error);
      Home.openStaticPage(slug);
      return;
    }
    showModal(dialog);
    try {
      history.replaceState(null, '', '#' + encodeURIComponent(slug));
    } catch (error) {
      /* Sandboxed frame: the hash is a nicety, not the point. */
    }
  };

  /* The static page generated for one app - the fallback target whenever the
     dialog cannot be built (no data yet, no dialog on this page, bad render). */
  Home.openStaticPage = function (slug) {
    if (!slug) return;
    location.href = OS.url('apps/' + encodeURIComponent(slug) + '/');
  };

  function flushPendingOpen() {
    var pending = state.pendingOpen;
    if (!pending) return;
    state.pendingOpen = null;
    Home.openApp(pending.slug, pending.tab);
  }

  function closeApp() {
    var dialog = $('#appDialog');
    if (dialog && dialog.open) hideModal(dialog);
    state.activeApp = null;
    if (location.hash && location.hash.charAt(0) === '#') {
      history.replaceState(null, '', location.pathname + location.search);
    }
  }

  function switchTab(tab) {
    if (!state.activeApp) return;
    state.activeTab = tab;
    var content = $('#dialogContent');
    if (!content) return;
    $$('.tab', content).forEach(function (button) {
      var active = button.dataset.tab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    ['about', 'versions', 'info'].forEach(function (key) {
      var panel = $('#panel-' + key);
      if (panel) panel.hidden = key !== tab;
    });
  }

  function openQr(title, text) {
    var dialog = $('#qrDialog');
    if (!dialog) return;
    $('#qrTitle').textContent = title;
    $('#qrText').textContent = text;
    $('#qrImage').src = 'https://api.qrserver.com/v1/create-qr-code/?size=460x460&margin=0&data=' + encodeURIComponent(text);
    showModal(dialog);
  }

  function shareApp(app) {
    var data = {
      title: app.name + ' on OmniSource',
      text: app.name + ' — ' + (app.subtitle || 'Available on OmniSource'),
      url: feedFor(app)
    };
    return (navigator.share ? navigator.share(data) : Promise.resolve())
      .catch(function (error) {
        if (error && error.name === 'AbortError') return;
        OS.copy(data.url, 'App feed copied');
      });
  }

  function bindQr() {
    var qrDialog = $('#qrDialog');
    if (!qrDialog || qrDialog.dataset.osBound) return;
    qrDialog.dataset.osBound = '1';
    qrDialog.addEventListener('click', function (event) {
      if (event.target === qrDialog || (event.target.closest && event.target.closest('[data-close]'))) hideModal(qrDialog);
    });
    var qrCopy = $('#qrCopy');
    if (qrCopy) qrCopy.addEventListener('click', function () {
      OS.copy($('#qrText').textContent, 'URL copied');
    });
    var sourceQr = $('#sourceQr') || $('#installQr');
    if (sourceQr) sourceQr.addEventListener('click', function () {
      openQr('OmniSource source', OS.ROOT.replace(/\/$/, '') + '/apps.json');
    });
  }

  /* Guarded so calling it twice (boot, then a later refresh) cannot double the
     close/tab/share handlers on the same dialog. */
  function bindDialogs() {
    bindQr();
    var appDialog = $('#appDialog');
    if (appDialog && !appDialog.dataset.osBound) {
      appDialog.dataset.osBound = '1';
      appDialog.addEventListener('click', function (event) {
        if (event.target === appDialog || (event.target.closest && event.target.closest('[data-close]'))) { closeApp(); return; }
        var tab = event.target.closest ? event.target.closest('.tab') : null;
        if (tab) { switchTab(tab.dataset.tab); return; }
        var qr = event.target.closest ? event.target.closest('[data-app-qr]') : null;
        if (qr && state.activeApp) {
          var app = state.activeApp;
          closeApp();
          openQr(app.name, feedFor(app));
          return;
        }
        var share = event.target.closest ? event.target.closest('[data-share]') : null;
        if (share && state.activeApp) shareApp(state.activeApp);
      });
      appDialog.addEventListener('cancel', function (event) {
        event.preventDefault();
        closeApp();
      });
    }
  }

  /* ============================================================== compare */
  var Compare = {
    left: null,
    right: null,
    bySlug: new Map(),

    load: function () {
      var doc = state.compare;
      if (!doc || !Array.isArray(doc.pairs) || !doc.pairs.length) {
        this.fallbackApps();
      } else {
        doc.pairs.forEach(function (pair) {
          if (pair.left && pair.left.slug) this.bySlug.set(pair.left.slug, pair.left);
          if (pair.right && pair.right.slug) this.bySlug.set(pair.right.slug, pair.right);
        }, this);
      }
      // Deep link support: ?left=a&right=b or #a-b.
      var params = new URLSearchParams(location.search);
      if (params.get('left')) this.left = params.get('left');
      if (params.get('right')) this.right = params.get('right');
      if (!this.left && location.hash.length > 1) {
        var parts = location.hash.slice(1).split('-');
        if (parts.length === 2) { this.left = parts[0]; this.right = parts[1]; }
      }
      this.renderSelects();
      this.renderPairs();
      this.renderResult();

      var metaRow = $('#cmpMetaRow');
      if (metaRow) {
        var pairCount = (state.compare && state.compare.pairs ? state.compare.pairs.length : 0);
        metaRow.innerHTML = '<span>' + this.bySlug.size + ' apps</span>' +
          '<span>' + pairCount + ' pairs precomputed</span>' +
          '<span>data: compare.json</span>';
      }

      var form = $('#compareForm');
      if (form) {
        form.addEventListener('submit', function (event) {
          event.preventDefault();
          Compare.left = $('#leftSelect').value;
          Compare.right = $('#rightSelect').value;
          Compare.renderResult();
        });
      }
    },

    fallbackApps: function () {
      // compare.json missing: build a minimal pair list from the catalog.
      state.apps.forEach(function (app) {
        this.bySlug.set(slugFor(app), {
          slug: slugFor(app), name: app.name, icon: OS.url('assets/' + (app.icon ? app.icon.replace(/^assets\//, '') : 'OmniSource.png')),
          category: app.category, version: app.version, releaseDate: app.versionDate,
          source: app.omnisource ? ((app.omnisource.verification && app.omnisource.verification.publisher) || app.omnisource.upstreamURL || '') : '',
          sourceURL: app.omnisource ? (app.omnisource.sourceURL || '') : '', verificationLevel: '',
          updateFrequencyDays: null, downloadReachable: true,
          compatibility: (app.omnisource && app.omnisource.compatibility) || {}
        });
      }, this);
    },

    renderSelects: function () {
      var left = $('#leftSelect');
      var right = $('#rightSelect');
      if (!left || !right) return;
      var apps = Array.from(this.bySlug.values()).sort(function (a, b) { return a.name.localeCompare(b.name); });
      var options = apps.map(function (app) {
        return '<option value="' + OS.esc(app.slug) + '">' + OS.esc(app.name) + '</option>';
      }).join('');
      left.innerHTML = options;
      right.innerHTML = options;
      if (!this.left && apps.length > 1) {
        this.left = apps[0].slug;
        this.right = apps[1].slug;
      }
      if (this.bySlug.has(this.left)) left.value = this.left;
      if (this.bySlug.has(this.right)) right.value = this.right;
    },

    renderPairs: function () {
      var list = $('#pairList');
      var section = $('#pairs');
      if (!list || !section) return;
      var pairs = state.compare ? state.compare.pairs : [];
      var featured = pairs.filter(function (p) { return p.shareBundle; }).slice(0, 12);
      if (!featured.length) { section.hidden = true; return; }
      list.innerHTML = featured.map(function (p) {
        return '<li><button type="button" data-left="' + OS.esc(p.left.slug) + '" data-right="' + OS.esc(p.right.slug) + '" class="os-lift">' +
          '<img src="' + OS.esc(iconSrc(p.left.icon)) + '" alt="" width="34" height="34" loading="lazy">' +
          '<span class="pair-vs">' + OS.esc(p.left.name) + ' <em>vs</em> ' + OS.esc(p.right.name) + '</span>' +
          '<span class="pair-meta">' + (p.shareBundle ? 'Same bundle' : p.shareCategory ? 'Same category' : '') + '</span>' +
          '<span class="pair-arrow" aria-hidden="true">→</span>' +
        '</button></li>';
      }).join('');
      section.hidden = false;
      list.addEventListener('click', function (event) {
        var btn = event.target.closest ? event.target.closest('button') : null;
        if (!btn) return;
        Compare.left = btn.dataset.left;
        Compare.right = btn.dataset.right;
        var left = $('#leftSelect'); if (left) left.value = Compare.left;
        var right = $('#rightSelect'); if (right) right.value = Compare.right;
        Compare.renderResult();
      });
    },

    screenStrip: function (app) {
      var catalogApp = appForSlug(app.slug);
      var shots = catalogApp && catalogApp.screenshotURLs
        ? catalogApp.screenshotURLs.filter(function (u) { return OS.cleanUrl(u) !== '#'; })
        : [];
      if (!shots.length) {
        return '<img class="icon-fallback" src="' + OS.esc(iconSrc(app.icon)) + '" alt="' + OS.esc(app.name) + ' icon" loading="lazy">';
      }
      return shots.slice(0, 4).map(function (u, i) {
        return '<img src="' + OS.esc(OS.cleanUrl(u)) + '" alt="' + OS.esc(app.name) + ' screenshot ' + (i + 1) + '" loading="lazy">';
      }).join('');
    },

    side: function (app, winnerSlug) {
      var winner = winnerSlug === app.slug;
      var verification = app.verificationLevel || 'UNVERIFIED';
      return '<article class="cmp-side' + (winner ? ' is-winner' : '') + '" data-reveal>' +
        '<header>' +
          '<img src="' + OS.esc(iconSrc(app.icon)) + '" alt="" width="68" height="68" loading="lazy">' +
          '<div style="min-width:0;flex:1"><h2><a href="' + OS.esc(OS.url('apps/' + app.slug + '/')) + '">' + OS.esc(app.name) + '</a></h2>' +
            '<p>' + OS.esc(categoryLabel(app.category)) + ' · ' + OS.esc(app.developer || '') + '</p>' +
            '<div class="chips">' +
              '<span class="badge ' + verificationBadgeClass(verification) + '">' + OS.esc(VERIFICATION_LABELS[verification] || verification) + '</span>' +
              (app.downloadReachable ? '<span class="badge ok"><span class="dot"></span>Online</span>' : '<span class="badge bad"><span class="dot"></span>Offline</span>') +
            '</div></div>' +
          (winner ? '<span class="winner-tag">Recommended</span>' : '') +
        '</header>' +
        '<table class="cmp-table"><tbody>' +
          '<tr><th scope="row">Version</th><td>v' + OS.esc(app.version || '—') + '</td></tr>' +
          '<tr><th scope="row">Released</th><td>' + OS.esc(OS.fmtDate(app.releaseDate)) + '</td></tr>' +
          '<tr><th scope="row">Source</th><td>' + sourceCell(app) + '</td></tr>' +
          '<tr><th scope="row">Update gap</th><td>' + (app.updateFrequencyDays != null ? OS.esc(app.updateFrequencyDays + ' days') : '—') + '</td></tr>' +
          '<tr><th scope="row">Min iOS</th><td>' + OS.esc((app.compatibility && app.compatibility.minOSVersion) || '—') + '</td></tr>' +
          '<tr><th scope="row">Devices</th><td>' + OS.esc(((app.compatibility && app.compatibility.devices) || []).join(', ') || '—') + '</td></tr>' +
        '</tbody></table>' +
        '<div class="cmp-screens">' + this.screenStrip(app) + '</div>' +
        '<a class="button cmp-cta" href="' + OS.esc(OS.url('apps/' + app.slug + '/')) + '">Open detail page →</a>' +
      '</article>';
    },

    renderResult: function () {
      var section = $('#result');
      var empty = $('#cmpEmpty');
      if (!section) return;
      if (!this.left || !this.right || this.left === this.right || !this.bySlug.has(this.left) || !this.bySlug.has(this.right)) {
        section.hidden = true;
        if (empty) empty.hidden = false;
        return;
      }
      var left = this.bySlug.get(this.left);
      var right = this.bySlug.get(this.right);
      var winner = null;
      var pairs = state.compare ? state.compare.pairs : [];
      var pair = pairs.find(function (p) {
        return (p.left.slug === left.slug && p.right.slug === right.slug) || (p.left.slug === right.slug && p.right.slug === left.slug);
      });
      if (pair) winner = pair.winner;

      var grid = $('#resultGrid');
      if (grid) {
        grid.innerHTML = this.side(left, winner) + this.side(right, winner);
      }
      section.hidden = false;
      if (empty) empty.hidden = true;
      $$('#result [data-reveal]').forEach(function (n) { n.classList.add('is-revealed'); });
      if (section.scrollIntoView) section.scrollIntoView({ behavior: OS.reducedMotion ? 'auto' : 'smooth', block: 'start' });
      history.replaceState(null, '', '?left=' + encodeURIComponent(this.left) + '&right=' + encodeURIComponent(this.right));
    }
  };

  /* ============================================================== status */
  var StatusPage = {
    render: function () {
      var doc = state.status;
      var analytics = state.analytics;
      var overview = $('#stOverview');
      var tableWrap = $('#stTableWrap');
      var syncGrid = $('#stSyncGrid');
      if (!doc || !Array.isArray(doc.sources)) {
        if (tableWrap) tableWrap.innerHTML = '<div class="chart-empty">Status data unavailable — it is refreshed on every sync.</div>';
        return;
      }
      var totals = doc.totals || {};
      if (overview) {
        overview.innerHTML = [
          { cls: 'ok', icon: '<path d="m5 12 4 4L19 6"/>', value: totals.healthy != null ? totals.healthy : 0, label: 'Healthy', sub: 'reachable & valid' },
          { cls: 'warn', icon: '<path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/>', value: totals.degraded != null ? totals.degraded : 0, label: 'Degraded', sub: 'slow or stale' },
          { cls: 'bad', icon: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>', value: totals.unavailable != null ? totals.unavailable : 0, label: 'Unavailable', sub: 'download unreachable' },
          { cls: 'info', icon: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4m0 3.2v.1"/>', value: totals.unknown != null ? totals.unknown : 0, label: 'Unknown', sub: 'not checked yet' }
        ].map(function (tile, i) {
          return '<div class="status-tile panel ' + tile.cls + '" data-reveal style="--reveal-delay:' + (i * 60) + 'ms">' +
            '<span class="tile-icon"><svg viewBox="0 0 24 24" aria-hidden="true">' + tile.icon + '</svg></span>' +
            '<strong>' + tile.value + '</strong><span>' + tile.label + '</span><span class="tile-sub">' + tile.sub + '</span></div>';
        }).join('');
      }

      var rep = {};
      if (state.reputation && state.reputation.sources) {
        state.reputation.sources.forEach(function (s) { rep[s.source] = s; });
      }
      var rows = doc.sources.map(function (source, i) {
        var level = rep[source.source] ? rep[source.source].level : '';
        var spark = sparkline(source.history);
        var srcUrl = source.sourceURL ? OS.cleanUrl(source.sourceURL) : '';
        var srcText = source.source || source.id || '';
        var srcHtml = (srcUrl && srcUrl !== '#')
          ? '<a class="source-link" href="' + OS.esc(srcUrl) + '" target="_blank" rel="noopener">' + OS.esc(srcText) + '</a>'
          : OS.esc(srcText);
        return '<tr data-reveal style="--reveal-delay:' + Math.min(i * 25, 400) + 'ms">' +
          '<td class="source-name">' + OS.esc(source.name) + '<small>' + srcHtml + (level ? ' · ' + OS.esc(level) : '') + '</small></td>' +
          '<td><span class="st-status ' + OS.esc(source.status || 'unknown') + '">' + OS.esc(source.status || 'unknown') + '</span></td>' +
          '<td class="num">' + (source.latencyMs != null ? source.latencyMs + ' ms' : '—') + '</td>' +
          '<td class="num">' + spark + '</td>' +
          '<td class="num">' + OS.esc(OS.timeAgo(source.checkedAt)) + '</td>' +
          '<td class="num">' + OS.esc(OS.timeAgo(source.lastUpdate)) + '</td>' +
          '<td class="num">' + (source.valid === false ? 'invalid' : 'valid') + '</td>' +
          '<td><a class="page-link" href="' + OS.esc(OS.url('apps/' + source.app + '/')) + '">' + OS.esc(source.app) + ' ↗</a></td>' +
        '</tr>';
      }).join('');
      var table = $('#stTable');
      if (table) table.innerHTML = rows;
      if (tableWrap) tableWrap.hidden = false;

      if (syncGrid) {
        var lastSync = (analytics && analytics.lastSync) || (analytics && analytics.totals && analytics.totals.lastSync) || '';
        syncGrid.innerHTML = [
          ['Last sync', lastSync ? OS.fmtDate(lastSync) + ' · ' + OS.timeAgo(lastSync) : 'pending'],
          ['Generated', OS.fmtDate(doc.generatedAt)],
          ['Sources checked', String(doc.sources.length)],
          ['Apps in catalog', String(state.apps.length)],
          ['Sync cadence', 'every 6 hours (GitHub Actions)'],
          ['Pipeline', '<code>scripts/omnisource.py</code>', 'raw']
        ].map(function (cell) {
          // The one cell that carries markup is marked; everything else is
          // escaped, so a feed value can never become live HTML here.
          if (cell[2] === 'raw') return '<div class="st-sync-cell"><span>' + OS.esc(cell[0]) + '</span><strong>' + cell[1] + '</strong></div>';
          return '<div class="st-sync-cell"><span>' + OS.esc(cell[0]) + '</span><strong>' + OS.esc(cell[1]) + '</strong></div>';
        }).join('');
      }

      var metaRow = $('#stMetaRow');
      if (metaRow) {
        metaRow.innerHTML = '<span>generated ' + OS.esc(OS.fmtDate(doc.generatedAt)) + '</span>' +
          '<span>' + doc.sources.length + ' sources</span>' +
          '<span>refreshed every 6 h</span>';
      }
      $$('[data-reveal]', $('#statusContent')).forEach(function (n) { n.classList.add('is-revealed'); });
    }
  };

  function sparkline(history) {
    if (!Array.isArray(history) || history.length < 2) return '<span class="text-faint">—</span>';
    var values = history.slice(-10).map(function (h) { return Number(h.latencyMs) || 0; });
    var max = Math.max.apply(null, values.concat([1]));
    var w = 84, h = 22, gap = 2;
    var barW = (w - gap * (values.length - 1)) / values.length;
    var bars = values.map(function (v, i) {
      var bh = Math.max(2, Math.round((v / max) * h));
      var down = v === 0;
      return '<rect x="' + (i * (barW + gap)).toFixed(1) + '" y="' + (h - bh) + '" width="' + barW.toFixed(1) + '" height="' + bh + '" rx="1.5" fill="' + (down ? 'var(--red)' : 'var(--accent)') + '" opacity="' + (0.35 + 0.65 * (i / Math.max(1, values.length - 1))).toFixed(2) + '"></rect>';
    }).join('');
    var last = values[values.length - 1];
    return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Latency trend, latest ' + last + ' ms">' + bars + '</svg>';
  }

  /* ============================================================== analytics */
  var Analytics = {
    render: function () {
      var doc = state.analytics;
      var kpis = $('#anKpis');
      if (!doc || !doc.totals) {
        if (kpis) kpis.innerHTML = '<div class="chart-empty">Analytics data unavailable.</div>';
        return;
      }
      var t = doc.totals;

      function kpi(label, value, delta) {
        var deltaHtml = delta ? '<span class="kpi-delta ' + delta.cls + '">' + (delta.arrow || '') + ' ' + OS.esc(delta.text) + '</span>' : '';
        return '<div class="kpi-card panel" data-reveal><small>' + OS.esc(label) + '</small><strong>' + OS.esc(String(value)) + '</strong>' + deltaHtml + '</div>';
      }

      var history = Array.isArray(doc.history) ? doc.history : [];
      var first = history[0] || null;
      function deltaFrom(field) {
        if (!first || history.length < 2) return null;
        var nowVal = Number(history[history.length - 1][field] || 0);
        var thenVal = Number(first[field] || 0);
        var diff = nowVal - thenVal;
        if (!diff) return { cls: 'flat', text: 'no change in 30d', arrow: '→' };
        return diff > 0
          ? { cls: 'up', text: '+' + diff + ' in 30d', arrow: '↑' }
          : { cls: 'down', text: String(diff) + ' in 30d', arrow: '↓' };
      }

      if (kpis) {
        kpis.innerHTML =
          kpi('Apps', t.apps, deltaFrom('apps')) +
          kpi('Sources', t.sources, deltaFrom('sources')) +
          kpi('Verified', t.verifiedApps + '/' + t.apps, deltaFrom('verified')) +
          kpi('Updated this week', t.updatedAppsThisWeek != null ? t.updatedAppsThisWeek : 0) +
          kpi('New this week', t.newAppsThisWeek != null ? t.newAppsThisWeek : 0) +
          kpi('Dead links', t.deadLinks, t.deadLinks ? { cls: 'down', text: 'action needed', arrow: '↓' } : { cls: 'up', text: 'all clear', arrow: '✓' });
      }

      // Trend chart (area + lines over the rolling 30-day history).
      this.renderTrendChart($('#trendChart'), history);
      this.renderUpdateBars($('#updateBars'), history);
      this.renderCategoryBars($('#categoryBars'), doc.topCategories);
      this.renderVerificationDonut($('#verificationDonut'), doc.verification);
      this.renderWeekLists($('#weekLists'), doc);

      var metaRow = $('#anMetaRow');
      if (metaRow) {
        metaRow.innerHTML = '<span>generated ' + OS.esc(OS.fmtDate(doc.generatedAt)) + '</span>' +
          '<span>' + history.length + '-day rolling history</span>' +
          '<span>no external services</span>';
      }
      $$('[data-reveal]', $('#analyticsContent')).forEach(function (n) { n.classList.add('is-revealed'); });
    },

    renderTrendChart: function (node, history) {
      if (!node) return;
      if (!history.length) {
        node.innerHTML = '<div class="chart-empty">Trends appear after the first few syncs (history is a rolling 30-day window).</div>';
        return;
      }
      var W = 720, H = 240, PAD = { l: 34, r: 14, t: 16, b: 26 };
      var series = [
        { key: 'apps', label: 'Apps', color: 'var(--accent)' },
        { key: 'verified', label: 'Verified', color: 'var(--green)' },
        { key: 'sources', label: 'Sources', color: 'var(--cyan)' }
      ];
      var n = history.length;
      var allValues = [];
      series.forEach(function (s) { history.forEach(function (h) { allValues.push(Number(h[s.key]) || 0); }); });
      var maxV = Math.max.apply(null, allValues.concat([1])) * 1.15;
      var x = function (i) { return PAD.l + (n === 1 ? (W - PAD.l - PAD.r) / 2 : (i / (n - 1)) * (W - PAD.l - PAD.r)); };
      var y = function (v) { return H - PAD.b - (v / maxV) * (H - PAD.t - PAD.b); };

      // Grid lines + labels.
      var grid = '';
      for (var g = 0; g <= 4; g++) {
        var gv = (maxV / 4) * g;
        var gy = y(gv);
        grid += '<line x1="' + PAD.l + '" y1="' + gy + '" x2="' + (W - PAD.r) + '" y2="' + gy + '" stroke="var(--line)" stroke-width="1"/>' +
          '<text x="' + (PAD.l - 8) + '" y="' + (gy + 3.5) + '" text-anchor="end" font-size="9.5" fill="var(--faint)" font-family="var(--font-mono)">' + Math.round(gv) + '</text>';
      }
      // X labels: first, middle, last.
      function xLabel(i) {
        if (i < 0 || i >= n) return '';
        var d = history[i].date ? String(history[i].date).slice(5) : '';
        var anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
        var tx = i === 0 ? PAD.l : (i === n - 1 ? W - PAD.r : x(i));
        return '<text x="' + tx + '" y="' + (H - 8) + '" text-anchor="' + anchor + '" font-size="9.5" fill="var(--faint)" font-family="var(--font-mono)">' + OS.esc(d) + '</text>';
      }

      var paths = '';
      var area = '';
      series.forEach(function (s) {
        var points = history.map(function (h, i) { return x(i).toFixed(1) + ',' + y(Number(h[s.key]) || 0).toFixed(1); });
        if (n >= 2) {
          paths += '<polyline class="os-chart-line" points="' + points.join(' ') + '" fill="none" stroke="' + s.color + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
        } else {
          paths += '<circle cx="' + points[0].split(',')[0] + '" cy="' + points[0].split(',')[1] + '" r="4" fill="' + s.color + '"/>';
        }
        points.forEach(function (p, i) {
          paths += '<circle cx="' + p.split(',')[0] + '" cy="' + p.split(',')[1] + '" r="3" fill="var(--surface-solid)" stroke="' + s.color + '" stroke-width="2"><title>' + s.label + ': ' + Math.round(Number(history[i][s.key]) || 0) + '</title></circle>';
        });
        if (s.key === 'apps' && n >= 2) {
          area = '<path class="os-chart-area" d="M' + points[0].split(',')[0] + ',' + (H - PAD.b) + ' L' + points.join(' L') + ' L' + points[points.length - 1].split(',')[0] + ',' + (H - PAD.b) + ' Z" fill="var(--accent-soft)"/>';
        }
      });

      var legend = series.map(function (s) {
        return '<span class="legend-item"><span class="swatch" style="background:' + s.color + '"></span>' + s.label + '</span>';
      }).join('');

      node.innerHTML =
        '<div class="chart-body"><svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Catalog size and verification over time">' +
        grid + area + paths + xLabel(0) + xLabel(n - 1) +
        '</svg></div>' +
        '<div class="donut-legend" style="margin-top:14px">' + legend + '</div>';
    },

    renderUpdateBars: function (node, history) {
      if (!node) return;
      if (!history.length) {
        node.innerHTML = '<div class="chart-empty">Update counts appear after the first sync.</div>';
        return;
      }
      var W = 340, H = 240, PAD = { l: 30, r: 10, t: 16, b: 26 };
      var n = history.length;
      var values = history.map(function (h) { return Number(h.updatedThisWeek) || 0; });
      var maxV = Math.max.apply(null, values.concat([1])) * 1.2;
      var barW = Math.min(26, ((W - PAD.l - PAD.r) / n) * 0.62);
      var x = function (i) { return PAD.l + (n === 1 ? (W - PAD.l - PAD.r) / 2 : (i + 0.5) / n * (W - PAD.l - PAD.r)); };
      var y = function (v) { return H - PAD.b - (v / maxV) * (H - PAD.t - PAD.b); };
      var bars = history.map(function (h, i) {
        var v = Number(h.updatedThisWeek) || 0;
        var by = y(v);
        return '<rect class="os-bar" x="' + (x(i) - barW / 2).toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + Math.max(2, (H - PAD.b - by)).toFixed(1) + '" rx="4" fill="var(--accent)" style="--bar-delay:' + (i * 40) + 'ms"><title>' + OS.esc(h.date) + ': ' + v + ' updates this week</title></rect>';
      }).join('');
      var grid = '';
      for (var g = 0; g <= 3; g++) {
        var gv = (maxV / 3) * g;
        var gy = y(gv);
        grid += '<line x1="' + PAD.l + '" y1="' + gy + '" x2="' + (W - PAD.r) + '" y2="' + gy + '" stroke="var(--line)"/><text x="' + (PAD.l - 6) + '" y="' + (gy + 3.5) + '" text-anchor="end" font-size="9.5" fill="var(--faint)" font-family="var(--font-mono)">' + Math.round(gv) + '</text>';
      }
      node.innerHTML = '<div class="chart-body"><svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="App updates per week">' + grid + bars +
        '<text x="' + (W - PAD.r) + '" y="' + (H - 8) + '" text-anchor="end" font-size="9.5" fill="var(--faint)" font-family="var(--font-mono)">' + OS.esc(String(history[n - 1].date).slice(5)) + '</text>' +
        '</svg></div>';
    },

    renderCategoryBars: function (node, topCategories) {
      if (!node) return;
      if (!topCategories || !topCategories.length) {
        node.innerHTML = '<div class="chart-empty">No category data.</div>';
        return;
      }
      var max = Math.max.apply(null, topCategories.map(function (c) { return c.count; }));
      node.innerHTML = '<div class="bar-list">' + topCategories.map(function (c, i) {
        return '<div class="bar-row">' +
          '<span class="bar-label">' + OS.esc(categoryLabel(c.category)) + '</span>' +
          '<span class="bar-track"><span class="bar-fill" style="width:' + Math.round((c.count / max) * 100) + '%;--bar-delay:' + (i * 60) + 'ms"></span></span>' +
          '<span class="bar-count">' + c.count + '</span>' +
        '</div>';
      }).join('') + '</div>';
    },

    renderVerificationDonut: function (node, verification) {
      if (!node || !verification) return;
      var entries = [
        { label: 'Verified', value: verification['VERIFIED'] || 0, color: 'var(--green)' },
        { label: 'Community verified', value: verification['COMMUNITY VERIFIED'] || 0, color: 'var(--cyan)' },
        { label: 'Unverified', value: verification['UNVERIFIED'] || 0, color: 'var(--amber)' }
      ];
      var total = entries.reduce(function (sum, e) { return sum + e.value; }, 0);
      if (!total) {
        node.innerHTML = '<div class="chart-empty">No verification data.</div>';
        return;
      }
      var R = 62, C = 2 * Math.PI * R;
      var segments = '';
      var offset = 0;
      entries.forEach(function (entry, i) {
        var fraction = entry.value / total;
        var dash = fraction * C;
        segments += '<circle class="os-donut-seg" cx="80" cy="80" r="' + R + '" fill="none" stroke="' + entry.color + '" stroke-width="20" ' +
          'stroke-dasharray="' + Math.max(0, dash - 2).toFixed(1) + ' ' + (C - dash + 2).toFixed(1) + '" ' +
          'stroke-dashoffset="' + (-offset).toFixed(1) + '" transform="rotate(-90 80 80)" style="--seg-delay:' + (i * 120) + 'ms">' +
          '<title>' + entry.label + ': ' + entry.value + '</title></circle>';
        offset += dash;
      });
      node.innerHTML = '<div class="donut-wrap">' +
        '<svg width="160" height="160" viewBox="0 0 160 160" role="img" aria-label="Verification breakdown">' +
        segments +
        '<text x="80" y="76" text-anchor="middle" font-size="26" font-weight="800" fill="var(--text)" font-family="var(--font-display)">' + total + '</text>' +
        '<text x="80" y="96" text-anchor="middle" font-size="10" fill="var(--muted)" font-family="var(--font-mono)">apps</text>' +
        '</svg>' +
        '<div class="donut-legend">' + entries.map(function (entry) {
          return '<div class="legend-item"><span class="swatch" style="background:' + entry.color + '"></span>' + entry.label +
            '<span class="legend-val">' + entry.value + ' · ' + Math.round((entry.value / total) * 100) + '%</span></div>';
        }).join('') + '</div></div>';
    },

    renderWeekLists: function (node, doc) {
      if (!node) return;
      function list(items, empty) {
        if (!items || !items.length) return '<p class="text-muted">' + empty + '</p>';
        return '<div class="stack-sm">' + items.slice(0, 5).map(function (item) {
          return '<a class="rail-card os-lift" style="padding:11px 13px" href="' + OS.esc(OS.url('apps/' + item.slug + '/')) + '">' +
            '<div class="row"><b style="font-size:13px">' + OS.esc(item.name) + '</b>' +
            '<span class="text-mono text-xs text-muted" style="margin-left:auto">v' + OS.esc(item.version) + (item.previousVersion ? ' (from ' + OS.esc(item.previousVersion) + ')' : '') + '</span></div>' +
            '<div class="dev">' + OS.esc(OS.timeAgo(item.date)) + '</div></a>';
        }).join('') + '</div>';
      }
      node.innerHTML =
        // The two-column template lives in CSS (`.an-charts.is-pair`), never in
        // a style attribute: an inline `grid-template-columns` outranks the
        // responsive rule, so on a phone this row stayed two columns wide and
        // the second panel hung off the right edge of the screen.
        '<div class="an-charts is-pair">' +
        '<div class="chart-panel panel" data-reveal><h3>Updated this week</h3><p class="chart-sub">Releases that shipped in the last 7 days.</p>' + list(doc.updatedThisWeek, 'No updates recorded this week.') + '</div>' +
        '<div class="chart-panel panel" data-reveal><h3>New this week</h3><p class="chart-sub">Apps that joined the catalog in the last 7 days.</p>' + list(doc.newThisWeek, 'No new apps this week.') + '</div>' +
        '</div>';
    }
  };

  /* ============================================================== install */
  var Install = {
    render: function () {
      this.renderClientCards();
      this.renderAppPicker();
      this.handleAutoAdd();
    },

    /* One-tap hand-off used by README badges, QR codes and external links:
       /install/?add=altstore|sidestore|flarestore|feather|esign|ksign|livecontainer
       optionally with &app=<slug> for a single-app feed. GitHub (and most
       chat apps) strip non-http schemes from links, so those badges point
       here and this page performs the scheme navigation itself. The attempt
       fires once per page load; if the client is not installed iOS shows its
       own alert and the banner below stays on screen with a manual retry and
       the feed URL, so the flow can never dead-end. */
    handleAutoAdd: function () {
      var banner = $('#installAutoAdd');
      var params;
      try {
        params = new URLSearchParams(location.search);
      } catch (err) {
        return;
      }
      var wanted = String(params.get('add') || '').trim().toLowerCase();
      if (!wanted || !banner) return;
      var client = state.clients.filter(function (c) { return c.id === wanted; })[0] || null;
      if (!client || !installUrlFor(wanted, 'https://example.com/apps.json')) {
        banner.hidden = false;
        banner.className = 'panel in-autoadd is-error';
        banner.innerHTML = '<div class="iaa-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/></svg></div>' +
          '<div class="iaa-body"><h2>Unknown client</h2><p>' + OS.esc(wanted) + ' is not a supported sideloading client. Pick one of the clients below instead.</p></div>';
        return;
      }

      var appSlug = String(params.get('app') || '').trim().toLowerCase().replace(/[^a-z0-9._-]/g, '');
      var app = appSlug ? appForSlug(appSlug) : null;
      if (appSlug && !app) {
        banner.hidden = false;
        banner.className = 'panel in-autoadd is-error';
        banner.innerHTML = '<div class="iaa-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/></svg></div>' +
          '<div class="iaa-body"><h2>App not found</h2><p>There is no single-app feed for <code>' + OS.esc(appSlug) + '</code>. Add the master feed instead, or pick the app from the install center.</p></div>';
        return;
      }

      var feed = appSlug
        ? OS.ROOT.replace(/\/$/, '') + '/feeds/' + appSlug + '.json'
        : OS.ROOT.replace(/\/$/, '') + '/apps.json';

      // Prefer the URL the Python builder published in feeds/install.json
      // (byte-identical to the static app pages); installUrlFor() is the
      // fallback if that feed has not loaded yet.
      var deep = '';
      var doc = state.install;
      if (doc) {
        var set = appSlug ? (doc.apps || []).filter(function (a) { return a.slug === appSlug; })[0] : doc.master;
        var card = set && (set.cards || []).filter(function (c) { return c.client === wanted; })[0];
        if (card && card.url) deep = card.url;
      }
      if (!deep) deep = installUrlFor(wanted, feed);
      if (!deep) return;

      var icon = client.icon
        ? '<img src="' + OS.esc(OS.url('assets/' + client.icon)) + '" alt="" width="40" height="40">'
        : '<span class="iaa-dot">' + OS.esc(String(client.name || '?').slice(0, 1)) + '</span>';
      var target = app ? '<code>' + OS.esc(app.name) + '</code>' : 'the <b>master feed</b> (all ' + state.apps.length + ' apps)';
      banner.hidden = false;
      banner.className = 'panel in-autoadd';
      banner.innerHTML =
        '<div class="iaa-icon">' + icon + '</div>' +
        '<div class="iaa-body">' +
          '<span class="kicker">OPENING CLIENT</span>' +
          '<h2>Add OmniSource to ' + OS.esc(client.name) + '</h2>' +
          '<p>Adding ' + target + '. If ' + OS.esc(client.name) + ' does not open, it is not installed on this device — install it first, or paste the source URL manually.</p>' +
          '<div class="iaa-actions">' +
            '<a class="button primary" id="iaaContinue" href="' + OS.esc(deep) + '">Open ' + OS.esc(client.name) + ' &amp; add source</a>' +
            '<button class="button" type="button" data-copy="' + OS.esc(feed) + '" data-copy-msg="Source URL copied — paste it in ' + OS.esc(client.name) + '">Copy source URL</button>' +
            (app ? '' : '<a class="text-button" href="' + OS.esc(feed) + '" target="_blank" rel="noopener">View feed ↗</a>') +
          '</div>' +
        '</div>';

      // Highlight the matching client card in the grid below.
      var grid = $('#installClients');
      if (grid) {
        Array.prototype.forEach.call(grid.querySelectorAll('a[href]'), function (link) {
          if (link.getAttribute('href') === deep) {
            var card = link.closest('.client-card');
            if (card) card.classList.add('is-autoadd-target');
          }
        });
      }

      // One programmatic hand-off per load; further attempts are explicit
      // taps on the banner button.
      if (!this._autoAddFired) {
        this._autoAddFired = true;
        setTimeout(function () {
          try { window.location.href = deep; } catch (err) { /* banner remains */ }
        }, 450);
      }
    },

    renderClientCards: function () {
      var grid = $('#installClients');
      if (!grid) return;
      var doc = state.install;
      var clients = (doc && doc.clients) || state.clients || [];
      var sourceUrl = OS.ROOT.replace(/\/$/, '') + '/apps.json';
      var cards = clients.map(function (client, i) {
        var card = (doc && doc.master && doc.master.cards || []).find(function (c) { return c.client === client.id; });
        var deep = card && card.url ? card.url : installUrlFor(client.id, sourceUrl);
        var manual = card ? card.manualSetup : !deep;
        var recommended = card ? card.recommended : false;
        var steps = clientSteps(client, sourceUrl);
        var about = client.description || '';
        var needs = client.requirements || '';
        return '<article class="client-card panel os-lift" data-reveal style="--reveal-delay:' + (i * 60) + 'ms">' +
          '<div class="client-head">' +
            (client.icon ? '<img src="' + OS.esc(OS.url('assets/' + client.icon)) + '" alt="" width="48" height="48" loading="lazy">' : '') +
            '<div><h3>' + OS.esc(client.name) + '</h3>' +
              (about ? '<p class="client-about">' + OS.esc(about) + '</p>' : '') +
            '</div>' +
            (recommended ? '<span class="badge ok client-badge">Recommended</span>' : manual ? '<span class="badge warn client-badge">Manual setup</span>' : '<span class="badge cyan client-badge">Deep link</span>') +
          '</div>' +
          (needs ? '<p class="client-needs"><b>Needs</b> ' + OS.esc(needs) + '</p>' : '') +
          '<ol class="client-steps">' + steps.map(function (step) { return '<li>' + OS.esc(step) + '</li>'; }).join('') + '</ol>' +
          (deep
            ? '<a class="button primary" href="' + OS.esc(deep) + '">Add source in ' + OS.esc(client.name) + '</a>'
            : '<button class="button" type="button" data-copy="' + OS.esc(sourceUrl) + '" data-copy-msg="Source URL copied — paste it in ' + OS.esc(client.name) + '">Copy source URL</button>') +
          (client.url ? '<a class="text-button" href="' + OS.esc(client.url) + '" target="_blank" rel="noopener">Get ' + OS.esc(client.name) + ' ↗</a>' : '') +
        '</article>';
      }).join('');
      grid.innerHTML = cards;
      $$('#installClients [data-reveal]').forEach(function (n) { n.classList.add('is-revealed'); });
    },

    renderAppPicker: function () {
      var select = $('#installAppSelect');
      var cards = $('#installAppCards');
      var feedLabel = $('#installAppFeed');
      if (!select || !cards) return;
      var apps = state.apps.slice().sort(function (a, b) { return a.name.localeCompare(b.name); });
      select.innerHTML = '<option value="">Master feed (all apps)</option>' + apps.map(function (app) {
        return '<option value="' + OS.esc(slugFor(app)) + '">' + OS.esc(app.name) + '</option>';
      }).join('');

      function draw(slug) {
        var doc = state.install;
        var entry = null;
        if (doc && doc.apps) {
          entry = doc.apps.find(function (a) { return a.slug === (slug || ''); });
        }
        var sourceFeed = slug ? OS.ROOT.replace(/\/$/, '') + '/feeds/' + slug + '.json' : OS.ROOT.replace(/\/$/, '') + '/apps.json';
        // One-click copy of the single-app feed — the manual workaround when a
        // client (e.g. SideStore) cannot co-install apps sharing a bundle ID.
        if (feedLabel) {
          feedLabel.innerHTML = '<span class="ap-feed-url">' + OS.esc(sourceFeed) + '</span> <button type="button" class="ap-feed-copy" data-copy="' + OS.esc(sourceFeed) + '" data-copy-msg="Single-app source copied — add it manually in your client">Copy source link</button>';
        }
        var appCards = (entry && entry.cards) || (doc && doc.master ? doc.master.cards : []);
        if (!appCards.length) {
          cards.innerHTML = '<div class="chart-empty">Install cards are generated on every build.</div>';
          return;
        }
        cards.innerHTML = appCards.map(function (card) {
          var flag = card.recommended ? '<span class="card-flag flag-recommended">Recommended</span>'
            : card.manualSetup ? '<span class="card-flag flag-manual">Manual setup</span>'
              : '<span class="card-flag flag-compatible">Compatible</span>';
          var icon = card.icon ? '<img src="' + OS.esc(OS.url('assets/' + card.icon)) + '" alt="" width="30" height="30" loading="lazy">' : '';
          var body = '<div style="min-width:0"><b>' + OS.esc(card.name) + '</b>' + flag +
            '<small>' + OS.esc(card.instructions || '') + '</small></div>';
          return card.url
            ? '<a class="ap-install-card os-lift" href="' + OS.esc(card.url) + '" title="Open in ' + OS.esc(card.name) + '">' + icon + body + '</a>'
            : '<button class="ap-install-card os-lift" type="button" data-copy="' + OS.esc(card.feedURL || sourceFeed) + '" title="Open ' + OS.esc(card.name) + ' and paste the URL">' + icon + body + '</button>';
        }).join('');
      }

      select.addEventListener('change', function () { draw(this.value); });
      draw('');
    }
  };

  /* Steps for one client card. feeds/install.json carries the per-client copy
     (generated from src/omnisource/install.py::CLIENT_PROFILES), so the page
     and the API document cannot drift apart. installSteps() below is only the
     fallback for a client the builder does not know about. */
  function clientSteps(client, sourceUrl) {
    var fromDoc = Array.isArray(client.steps) ? client.steps.filter(function (step) { return String(step).trim(); }) : null;
    if (fromDoc && fromDoc.length) return fromDoc;
    return installSteps(client, sourceUrl);
  }

  function installSteps(client, sourceUrl) {
    var name = client.name || 'the client';
    // "It stays as your sideloading host" is true of AltStore and SideStore and
    // wrong for ESign (a signer, refreshed by hand) and LiveContainer (a
    // container installed through another client), so it is not a constant.
    var host = client.id === 'altstore' || client.id === 'sidestore';
    return [
      'Install ' + name + (host ? ' on your iPhone (it stays as your sideloading host).' : '.'),
      'Add the OmniSource feed as a source — the link below opens it on the right screen; if it does not, copy this URL and paste it into the client.',
      'Browse the catalog and install any app. New versions show up when the source next checks for updates.'
    ];
  }

  /* ============================================================== search */
  var SearchPage = {
    render: function () {
      var input = $('#searchPageInput');
      var results = $('#searchResults');
      if (!input || !results) return;
      var params = new URLSearchParams(location.search);
      var q = params.get('q') || '';
      input.value = q;

      function draw(query) {
        OS.Search.load().then(function () {
          var items = OS.Search.search(query);
          var count = $('#searchCount');
          if (count) count.textContent = query.trim()
            ? items.length + ' result' + (items.length === 1 ? '' : 's') + ' for “' + query.trim() + '”'
            : state.apps.length + ' apps in the catalog';
          if (!query.trim()) {
            results.innerHTML = '<div class="chart-empty" style="border:1px dashed var(--line-strong)">Type to search by app name, bundle ID, developer, source, category or tag.<br>Tip: press <kbd>⌘</kbd><kbd>K</kbd> anywhere for instant results.</div>';
            return;
          }
          if (!items.length) {
            results.innerHTML = '<div class="chart-empty">No matches for “' + OS.esc(query) + '”. Try a different keyword.</div>';
            return;
          }
          results.innerHTML = items.map(function (item) {
            var doc = item.doc;
            var icon = OS.asset(doc.icon || 'OmniSource.png');
            var verification = (state.verification.get(doc.slug || doc.id) || {}).status || '';
            return '<a class="result-row panel os-lift" href="' + OS.esc(OS.url('apps/' + (doc.slug || doc.id) + '/')) + '">' +
              '<img src="' + OS.esc(icon) + '" alt="" width="52" height="52" loading="lazy">' +
              '<div class="info"><h3>' + OS.Search.highlight(doc.name || '', query) + '</h3>' +
              '<p>' + OS.Search.highlight((doc.developer || '') + (doc.developer ? ' · ' : '') + (doc.subtitle || doc.category || ''), query) + '</p></div>' +
              '<div class="actions">' +
                (verification ? '<span class="badge ' + verificationBadgeClass(verification) + '">' + OS.esc(VERIFICATION_LABELS[verification] || verification) + '</span>' : '') +
                '<span class="meta-item text-mono">' + OS.esc(String(doc.bundleId || '').slice(0, 26)) + '</span>' +
              '</div></a>';
          }).join('');
        });
      }

      input.addEventListener('input', function () {
        var query = this.value;
        var url = new URL(location.href);
        if (query) url.searchParams.set('q', query); else url.searchParams.delete('q');
        history.replaceState(null, '', url);
        draw(query);
      });
      draw(q);
    }
  };

  /* Keep hearts in sync when favorites change on another page/tab
     (features.js on /favorites/ and /collections/). */
  function syncFavoritesFromStorage() {
    var next = new Set(loadFavorites());
    var same = next.size === state.favorites.size &&
      Array.prototype.every.call(next, function (slug) { return state.favorites.has(slug); });
    if (same) return;
    state.favorites = next;
    $$('.favorite[data-favorite]').forEach(function (btn) {
      var active = state.favorites.has(btn.dataset.favorite);
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
      btn.setAttribute('aria-label', (active ? 'Remove from' : 'Add to') + ' saved apps');
    });
    if (document.body.dataset.page === 'home') {
      Home.renderFilters();
      if (state.category === 'favorites') Home.filterAndRender();
    }
  }
  window.addEventListener('os:favorites-changed', syncFavoritesFromStorage);
  window.addEventListener('storage', function (event) {
    if (event.key === FAVORITES_KEY || event.key === FAVORITES_LEGACY_KEY) {
      syncFavoritesFromStorage();
    }
  });

  /* Re-render locale-dependent chrome when the language changes after boot
     (static [data-i18n] markup is handled by OmniI18n itself). */
  window.addEventListener('i18n:changed', function () {
    try {
      if (document.body.dataset.page !== 'home' || !state.apps.length) return;
      // Translated copy changes heights, so hold the reader's place while the
      // chrome is rebuilt (same reason refreshPage() goes through this).
      var render = function () {
        Home.renderHero();
        Home.renderHeroCategories();
        Home.renderFilters();
        Home.filterAndRender();
      };
      if (OS.stableScroll) OS.stableScroll(render); else render();
    } catch (e) { /* home renderers not ready on this page */ }
  });

  /* ------------------------------------------------------------------ boot */
  function boot() {
    var page = document.body.dataset.page;
    if (!page) return;
    /* Wire the catalog and the dialogs before requesting anything: the handlers
       are delegated on `document`, so they survive every re-render, and a
       deferred feed can paint the first cards at any moment - they have to be
       clickable the instant they exist, not after the last feed of the boot
       chain resolves (which is what used to make VIEW a no-op for a second or
       two, or forever on a slow connection). */
    bindCatalog();
    bindDialogs();
    loadData().then(loadCatalogMeta).then(function () {
      buildCollisions();
      state.loaded = true;
      // A VIEW tap that arrived before the feeds did is replayed here.
      flushPendingOpen();
      if (page === 'home') {
        applyCategoryDeepLink();
        Home.render();
        // Sections that arrived with the first-paint feeds change which tabs
        // the sticky bar should offer.
        if (OS.refreshSectionTabs) {
          try { OS.refreshSectionTabs(); } catch (error) { /* tab bar not ready */ }
        }
        // Deep link: #slug opens the dialog, #section is re-pinned to the
        // place the section ended up once the feeds above it had rendered.
        var hash = location.hash.slice(1);
        if (hash) {
          var slug = decodeURIComponent(hash);
          if (appForSlug(slug)) Home.openApp(slug);
          else if (sectionTargetFor(slug)) {
            repinTarget = slug;
            repinHash();
          }
        }
        armHashRepin();
      } else if (page === 'compare') {
        Compare.load();
      } else if (page === 'status') {
        StatusPage.render();
      } else if (page === 'analytics') {
        Analytics.render();
      } else if (page === 'install') {
        Install.render();
        bindQr();
      } else if (page === 'search') {
        SearchPage.render();
      }
    }).catch(function (error) {
      // The catalog could not be loaded, so a tap can no longer be answered
      // from memory: release anything queued (it falls through to the app's
      // static page) before showing the error state.
      state.loaded = true;
      try {
        flushPendingOpen();
      } catch (pendingError) {
        console.error('OmniSource: queued app open failed', pendingError);
      }
      try {
        console.error('OmniSource: data load failed', error);
        var grid = $('#appsGrid');
        if (grid) {
          grid.innerHTML = '';
          grid.hidden = true;
        }
        var empty = $('#emptyState');
        if (empty) {
          empty.hidden = false;
          var h = empty.querySelector('h3'); if (h) h.textContent = 'Catalog unavailable';
          var p = empty.querySelector('p'); if (p) p.textContent = 'The live feed could not be loaded. Please try again shortly.';
        }
        var label = $('#healthLabel');
        if (label) label.textContent = 'Source status unavailable';
        var pill = $('#healthPill');
        if (pill) pill.classList.add('is-error');
      } catch (renderError) {
        console.error('OmniSource: error state failed to render', renderError);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
