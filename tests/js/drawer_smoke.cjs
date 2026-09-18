/* Mobile-drawer smoke test for the static site.
 *
 * `tests/test_website_shell.py` checks the markup and the CSS breakpoint that
 * the drawer hangs off, but nothing ever ran the JavaScript. This script loads
 * the real homepage markup and the real `js/core.js` in jsdom and drives the
 * drawer the way a reader does, so the behaviours that broke in production
 * before — a lock that un-sticks the header, a drawer that will not scroll, a
 * page that scrolls behind the backdrop, focus left on the button the reader
 * abandoned — fail here instead of on somebody's phone.
 *
 * jsdom has no layout engine, so anything that depends on measured boxes
 * (getClientRects, IntersectionObserver) is stubbed or skipped; the drawer lock
 * and the gesture question ("did anything preventDefault a scroll?") do not
 * depend on layout and are asserted exactly.
 *
 * Run directly (`node tests/js/drawer_smoke.cjs`) or through
 * `tests/test_js_runtime.py`, which skips when node or jsdom is unavailable.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const core = fs.readFileSync(path.join(ROOT, "js", "core.js"), "utf8");

const results = [];
function check(name, fn) {
  try {
    const value = fn();
    // Convention: true/undefined = pass, a string = failure detail, false = fail.
    const ok = value !== false && typeof value !== "string";
    results.push({ name, ok, detail: typeof value === "string" ? value : undefined });
  } catch (error) {
    results.push({ name, ok: false, detail: String((error && error.message) || error) });
  }
}

const virtualConsole = new VirtualConsole();
const dom = new JSDOM(html, {
  url: "https://drawer.test.invalid/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
const { document } = window;

// --- feature detection core.js performs that jsdom cannot answer ------------
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
window.scrollTo = function () {};
window.scroll = function () {};
// jsdom reports an empty client rect for every element, which core.js uses to
// skip hidden nodes when it collects the drawer's focusable items — without a
// rect the Tab trap has nothing to cycle through.
window.Element.prototype.getClientRects = function () {
  const rect = { top: 0, left: 0, right: 10, bottom: 10, width: 10, height: 10, x: 0, y: 0 };
  return Object.assign([rect], { item: (i) => (i === 0 ? rect : null) });
};

// Record every document-level gesture listener core.js installs, so a future
// "just swallow the scroll" lock shows up as a failure instead of a comment.
const gestureListeners = [];
const originalAdd = document.addEventListener.bind(document);
document.addEventListener = function (type, listener, options) {
  if (type === "wheel" || type === "touchmove") gestureListeners.push(type);
  return originalAdd(type, listener, options);
};

function fire(node, type, init) {
  node.dispatchEvent(new window.MouseEvent(type, Object.assign({ bubbles: true, cancelable: true }, init || {})));
}

function scenario() {
  const body = document.body;
  const root = document.documentElement;

  check("core.js injects the toggle and the backdrop", function () {
    return Boolean(document.querySelector("#navToggle")) && Boolean(document.querySelector(".nav-backdrop"));
  });

  const btn = document.querySelector("#navToggle");
  const backdrop = document.querySelector(".nav-backdrop");
  const links = document.querySelector(".nav-links");

  check("the toggle is a labelled disclosure control", function () {
    if (btn.getAttribute("aria-expanded") !== "false") return "aria-expanded should start at false";
    if (!btn.getAttribute("aria-controls")) return "aria-controls is missing";
    return true;
  });

  fire(btn, "click");

  check("opening pins the page and marks the drawer open", function () {
    if (!body.classList.contains("nav-open") || !root.classList.contains("nav-open")) return "nav-open missing";
    if (!body.classList.contains("nav-lock") || !root.classList.contains("nav-lock")) return "the scroll lock is missing";
    if (btn.getAttribute("aria-expanded") !== "true") return "aria-expanded was not updated";
    if (body.style.top === "") return "the body pin has no offset";
    return true;
  });

  check("opening twice does not stack a second lock", function () {
    const top = body.style.top;
    fire(btn, "click"); // close
    fire(btn, "click"); // open again
    return body.style.top === top;
  });

  check("a scroll gesture is never cancelled", function () {
    const wheel = new window.WheelEvent("wheel", { bubbles: true, cancelable: true });
    document.dispatchEvent(wheel);
    const touch = new window.Event("touchmove", { bubbles: true, cancelable: true });
    document.dispatchEvent(touch);
    if (wheel.defaultPrevented) return "a wheel event was preventDefault()ed";
    if (touch.defaultPrevented) return "a touchmove event was preventDefault()ed";
    return true;
  });

  check("no document-level wheel/touchmove listener is installed", function () {
    return gestureListeners.length === 0 ? true : `installed: ${gestureListeners.join(", ")}`;
  });

  check("Escape closes the drawer and restores focus", function () {
    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    if (body.classList.contains("nav-open")) return "the drawer stayed open";
    if (body.classList.contains("nav-lock")) return "the scroll lock stayed on";
    if (body.style.top !== "") return "the body pin was not released";
    if (document.activeElement !== btn) return "focus did not return to the toggle";
    return true;
  });

  check("the backdrop closes the drawer", function () {
    fire(btn, "click");
    fire(backdrop, "click");
    return !body.classList.contains("nav-open");
  });

  check("following a link closes the drawer", function () {
    fire(btn, "click");
    const link = links && links.querySelector("a[href]");
    if (!link) return "the drawer has no links to follow";
    // Keep jsdom from attempting a navigation it cannot perform.
    link.addEventListener("click", function (event) { event.preventDefault(); }, { once: true });
    fire(link, "click");
    return !body.classList.contains("nav-open") && !body.classList.contains("nav-lock");
  });

  check("Tab is trapped inside the open drawer", function () {
    fire(btn, "click");
    const items = Array.prototype.slice.call(
      links.querySelectorAll('a[href], button, summary, [tabindex]:not([tabindex="-1"])'),
    );
    if (items.length < 2) return "the drawer has too few focusable items to test the trap";
    items[items.length - 1].focus();
    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    const wrapped = document.activeElement === items[0];
    document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return wrapped ? true : "Tab did not wrap to the first item";
  });

  check("the drawer is closed again at the end", function () {
    return !body.classList.contains("nav-open");
  });
}

function main() {
  window.eval(core);
  scenario();
  const failed = results.filter((r) => !r.ok);
  process.stdout.write(JSON.stringify({ checks: results.length, failed: failed.length, results }, null, 1) + "\n");
  process.exit(failed.length ? 1 : 0);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main, { once: true });
} else {
  main();
}
