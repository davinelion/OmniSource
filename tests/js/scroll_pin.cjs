/* Scroll-pin smoke test for the home page.
 *
 * `tests/js/drawer_smoke.cjs` covers the drawer; this one covers the thing a
 * reader feels while reading: the home page un-hides sections as their feeds
 * land, and `js/site.js` re-pins a deep-linked section after every such render
 * (see `armHashRepin` / `repinHash`). That is correct for a reader who has not
 * moved yet and wrong the moment they have — and "have they moved" used to be
 * answered by `wheel`, `touchmove` and the scroll keys only. A scrollbar drag
 * and middle-click autoscroll raise none of those, so a reader who scrolled
 * that way was still considered parked on the deep link: the next deferred
 * feed to land (they arrive for seconds) yanked the catalog back to its own
 * top mid-read, which reads exactly as the page reloading and jumping to the
 * first section.
 *
 * jsdom has no layout engine, so `scrollIntoView` and `scrollTo` are spied
 * rather than measured: what is asserted is *whether the page decided to move
 * the viewport*, which is the whole of this bug.
 *
 * Run directly (`node tests/js/scroll_pin.cjs`) or through
 * `tests/test_js_runtime.py`, which skips when node or jsdom is unavailable.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const core = fs.readFileSync(path.join(ROOT, "js", "core.js"), "utf8");
const site = fs.readFileSync(path.join(ROOT, "js", "site.js"), "utf8");

/* The home page's feed tiers (js/site.js FEEDS). Three documents are awaited
   before the first render, so they must resolve for boot() to reach the
   deep-link step at all. */
const FIRST_PAINT = ["feeds/health.json", "feeds/analytics.json", "feeds/verification.json"];
/* The deferred home feed: not first paint and not idle, so it is requested
   right after boot and re-renders (and therefore re-pins) when it lands. */
const DEFERRED_FEEDS = ["discovery.json"];
/* Idle-tier feeds, requested from requestIdleCallback. The harness supplies
   that callback (see boot) so the checks decide when they land instead of
   racing the 1200 ms fallback timer. */
const IDLE_FEEDS = ["feeds/updates.json", "feeds/install.json", "feeds/search-index.json"];
/* Every document that can land after the first render, in landing order. */
const LATE_FEEDS = DEFERRED_FEEDS.concat(IDLE_FEEDS);

const MINIMAL_FEED = {
  apps: [
    {
      slug: "demo",
      name: "Demo",
      bundleIdentifier: "com.demo.app",
      developerName: "Demo Developer",
      category: "Utilities",
      version: "1.0",
      versionDate: "2026-01-01",
      iconURL: "",
      downloadURL: "https://example.com/d.ipa",
      size: 1024,
    },
  ],
};

const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(
      (value) => {
        const ok = value !== false && typeof value !== "string";
        results.push({ name, ok, detail: typeof value === "string" ? value : undefined });
      },
      (error) => results.push({ name, ok: false, detail: String((error && error.message) || error) }),
    );
}

const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* One boot of the real index.html + core.js + site.js, with the feeds on a
   leash: first-paint feeds resolve at once, the late feeds resolve only when
   the test says so. */
function boot(hash) {
  const virtualConsole = new VirtualConsole();
  const dom = new JSDOM(html, {
    url: "https://scrollpin.test.invalid/" + (hash || ""),
    runScripts: "outside-only",
    pretendToBeVisual: true,
    virtualConsole,
  });
  const { window } = dom;
  const { document } = window;

  window.matchMedia = function () {
    return {
      matches: false,
      media: "",
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    };
  };
  window.Element.prototype.getClientRects = function () {
    const rect = { top: 0, left: 0, right: 10, bottom: 10, width: 10, height: 10, x: 0, y: 0 };
    return Object.assign([rect], { item: (i) => (i === 0 ? rect : null) });
  };

  let scrollY = 0;
  Object.defineProperty(window, "scrollY", { get: () => scrollY, configurable: true });
  Object.defineProperty(window, "pageYOffset", { get: () => scrollY, configurable: true });

  const scrollCalls = [];
  window.scrollTo = function (options, y) {
    const next = options && typeof options === "object" ? options.top : y;
    scrollCalls.push(next);
    scrollY = next;
  };
  window.scroll = window.scrollTo;

  // Every viewport move the page asks for, in order, by target id.
  const pinned = [];
  window.Element.prototype.scrollIntoView = function () {
    pinned.push(this.id || this.tagName.toLowerCase());
  };

  /* js/site.js hands the idle-tier feed loads to requestIdleCallback. jsdom
     does not implement it, and the code's timer fallback would fire in the
     middle of a check, so capture the callbacks and run them on demand. */
  const idleWork = [];
  window.requestIdleCallback = function (work) {
    idleWork.push(work);
    return idleWork.length;
  };

  window.eval(core);

  const pending = new Map();
  window.OS.fetchJSON = function (feedPath) {
    if (feedPath === "apps.json" || feedPath === "feeds/apps.json") {
      return Promise.resolve(MINIMAL_FEED);
    }
    if (FIRST_PAINT.indexOf(feedPath) !== -1) return Promise.resolve(null);
    if (LATE_FEEDS.indexOf(feedPath) !== -1) {
      if (!pending.has(feedPath)) {
        let resolve;
        const promise = new Promise((r) => {
          resolve = r;
        });
        pending.set(feedPath, { resolve, promise });
      }
      return pending.get(feedPath).promise;
    }
    return Promise.resolve(null);
  };

  window.eval(site);

  return {
    window,
    document,
    pinned,
    scrollCalls,
    pending,
    virtualConsole,
    /* Land one late feed and let the debounced re-render (120 ms) run. */
    async land(feedPath) {
      const entry = pending.get(feedPath);
      if (!entry) throw new Error("no pending request for " + feedPath);
      entry.resolve(null);
      await tick(220);
      await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
    },
    /* Run the queued idle work (which requests the idle-tier feeds) and let
       them resolve and re-render. */
    async landIdle() {
      const queued = idleWork.splice(0, idleWork.length);
      queued.forEach((work) => work());
      await tick(220);
      await new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
    },
    setScroll(y) {
      scrollY = y;
    },
  };
}

/* Let boot()'s promise chain (loadData → loadCatalogMeta → deep link) settle. */
async function settled(page) {
  await tick(60);
  await new Promise((resolve) => page.window.requestAnimationFrame(() => resolve()));
  await tick(20);
}

async function main() {
  /* --- 1. the re-pin still works for a reader who has not moved ------------ */
  const parked = boot("#catalog");
  await settled(parked);

  await check("a deep link re-pins #catalog once the first feeds are in", function () {
    return parked.pinned.indexOf("catalog") !== -1
      ? true
      : "boot() never re-pinned the deep link (pinned: " + JSON.stringify(parked.pinned) + ")";
  });

  const beforeLanding = parked.pinned.length;
  await parked.land(LATE_FEEDS[0]);
  await check("a late feed re-pins the deep link while the reader has not moved", function () {
    return parked.pinned.length > beforeLanding
      ? true
      : "the late feed did not re-pin — this test would pass vacuously without the re-pin";
  });
  parked.window.close();

  /* --- 2. the reported bug: a scrollbar drag must end the re-pin ----------- */
  const dragged = boot("#catalog");
  await settled(dragged);
  const pinnedBeforeDrag = dragged.pinned.length;

  // A scrollbar drag (or middle-click autoscroll): the viewport moves and a
  // scroll event fires, with no wheel, touchmove or keydown anywhere.
  dragged.setScroll(4000);
  dragged.window.dispatchEvent(new dragged.window.Event("scroll"));
  await tick(30);

  await dragged.land(LATE_FEEDS[0]);
  await check("a reader who scrolled by scrollbar is not yanked back by a late feed", function () {
    const after = dragged.pinned.slice(pinnedBeforeDrag);
    return after.length === 0
      ? true
      : "the page re-pinned after the reader scrolled away: " + JSON.stringify(after);
  });

  await dragged.landIdle();
  await check("and stays put for every idle-tier feed after that", function () {
    return dragged.pinned.length === pinnedBeforeDrag
      ? true
      : "further re-pins: " + JSON.stringify(dragged.pinned.slice(pinnedBeforeDrag));
  });
  dragged.window.close();

  /* --- 3. the original disarm paths still work ----------------------------- */
  const wheeled = boot("#catalog");
  await settled(wheeled);
  const pinnedBeforeWheel = wheeled.pinned.length;
  wheeled.window.dispatchEvent(
    new wheeled.window.MouseEvent("wheel", { bubbles: true, cancelable: true, deltaY: 120 }),
  );
  await tick(30);
  await wheeled.land(LATE_FEEDS[0]);
  await check("a wheel scroll still ends the re-pin", function () {
    return wheeled.pinned.length === pinnedBeforeWheel
      ? true
      : "re-pinned after a wheel scroll: " + JSON.stringify(wheeled.pinned.slice(pinnedBeforeWheel));
  });
  wheeled.window.close();

  const touched = boot("#catalog");
  await settled(touched);
  const pinnedBeforeTouch = touched.pinned.length;
  touched.window.dispatchEvent(
    new touched.window.MouseEvent("touchmove", { bubbles: true, cancelable: true }),
  );
  await tick(30);
  await touched.land(LATE_FEEDS[0]);
  await check("a touch drag still ends the re-pin", function () {
    return touched.pinned.length === pinnedBeforeTouch
      ? true
      : "re-pinned after a touch drag: " + JSON.stringify(touched.pinned.slice(pinnedBeforeTouch));
  });
  touched.window.close();

  /* --- 4. a new hash is a new request, even after the reader moved --------- */
  const rehash = boot("#catalog");
  await settled(rehash);
  rehash.setScroll(4000);
  // Disarm by wheel, which every version of this code honours — the point of
  // this check is what happens to the *next* deep link, not the disarm itself.
  rehash.window.dispatchEvent(
    new rehash.window.MouseEvent("wheel", { bubbles: true, cancelable: true, deltaY: 120 }),
  );
  await tick(30);
  const pinnedBeforeRehash = rehash.pinned.length;
  // #installGuide starts hidden and only becomes a scroll target once its
  // feed lands — which is the situation a reader clicking "Install" is in.
  rehash.document.getElementById("installGuide").hidden = false;
  rehash.window.location.hash = "#installGuide";
  rehash.window.dispatchEvent(new rehash.window.Event("hashchange"));
  await tick(30);
  await check("a deep link requested after the reader scrolled is honoured again", function () {
    return rehash.pinned.slice(pinnedBeforeRehash).indexOf("installGuide") !== -1
      ? true
      : "the new #installGuide request was ignored after a reader scroll (pinned: " +
          JSON.stringify(rehash.pinned) +
          ")";
  });
  rehash.window.close();

  /* --- 5. OS.stableScroll: correct by the landmark, fall back to <main> ---- */
  await check("stableScroll re-pins by however much the landmark moved", function () {
    const page = boot("");
    const { window, document, scrollCalls } = page;
    /* The page's own <main>: stableScroll resolves it with $('main'), so the
       test has to use the real one (jsdom reports a zero-height rect for any
       node it did not lay out, which `usable()` reads as invisible). */
    const main = document.querySelector("main");
    const section = document.createElement("section");
    section.id = "shelf";
    const deep = document.createElement("div");
    section.appendChild(deep);
    main.appendChild(section);

    let deepTop = 100;
    let mainTop = 0;
    deep.getBoundingClientRect = () => ({ top: deepTop, bottom: deepTop + 40, height: 40 });
    section.getBoundingClientRect = () => ({ top: deepTop, bottom: deepTop + 40, height: 40 });
    main.getBoundingClientRect = () => ({ top: mainTop, bottom: mainTop + 900, height: 900 });
    document.elementsFromPoint = () => [deep];

    page.setScroll(500);
    window.OS.stableScroll(function () {
      deepTop += 800; // a rail grew above the reader
    });
    window.close();
    return scrollCalls.length === 1 && scrollCalls[0] === 1300
      ? true
      : "expected one correction to 1300, got " + JSON.stringify(scrollCalls);
  });

  await check("stableScroll falls back to <main> when the render replaces the landmark", function () {
    const page = boot("");
    const { window, document, scrollCalls } = page;
    const main = document.querySelector("main");
    const section = document.createElement("section");
    section.id = "shelf";
    const deep = document.createElement("div");
    section.appendChild(deep);
    main.appendChild(section);

    let mainTop = 0;
    deep.getBoundingClientRect = () => ({ top: 100, bottom: 140, height: 40 });
    section.getBoundingClientRect = () => ({ top: 100, bottom: 140, height: 40 });
    main.getBoundingClientRect = () => ({ top: mainTop, bottom: mainTop + 900, height: 900 });
    document.elementsFromPoint = () => [deep];

    page.setScroll(500);
    window.OS.stableScroll(function () {
      // The render threw the whole shelf away and rebuilt it: both the
      // landmark and its section are detached afterwards.
      section.remove();
      const rebuilt = document.createElement("section");
      rebuilt.id = "shelf";
      main.appendChild(rebuilt);
      mainTop += 300;
    });
    window.close();
    return scrollCalls.length === 1 && scrollCalls[0] === 800
      ? true
      : "expected one correction to 800 via <main>, got " + JSON.stringify(scrollCalls);
  });
}

main().then(
  () => {
    const failed = results.filter((r) => !r.ok);
    process.stdout.write(JSON.stringify({ checks: results.length, failed: failed.length, results }, null, 1) + "\n");
    process.exit(failed.length ? 1 : 0);
  },
  (error) => {
    process.stdout.write(
      JSON.stringify({ checks: results.length, failed: results.length + 1, results, crash: String(error) }, null, 1) +
        "\n",
    );
    process.exit(1);
  },
);
