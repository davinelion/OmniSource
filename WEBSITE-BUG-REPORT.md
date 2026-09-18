# OmniSource website — bug report

_Audit of the static site built from this repo (`make site` → `_site`, 207 HTML pages:
the home catalog, 16 hub pages, 85 source pages, 101 app pages, plus feeds/API)._

**How it was tested:** the real build served over HTTP and driven in headless Chromium
(Blink, the desktop/mobile engine used by Chrome/Edge/Android) at **320, 390, 834 and
1440 px**, in both light and dark themes, with smooth scrolling disabled so anchor
measurements are exact. Coverage: 212 hub page×viewport combinations, 202 app-page
combinations, plus targeted flows (search, filters, dialogs, drawer, favorites,
deep links, keyboard, i18n, storage persistence) and a static crawl of all 207 pages
(internal links, assets, anchors, ids, meta). Screenshots referenced below are in
`/home/user/shots2/`.

**Overall verdict:** the site is in good shape structurally — no broken links, no missing
assets, no duplicate ids, correct SEO basics, and the main catalog flows work. There are
**three high-impact bugs** (mobile drawer, favorites persistence, deep-link anchoring) and
a handful of narrower layout/media issues. Details and root causes below.

---

## Severity summary

| # | Area | Severity | One-line symptom |
|---|---|---|---|
| 1 | Mobile drawer | **High** | Drawer scrolls away with the page while it stays "open" — screen ends up blurred with no menu |
| 2 | Favorites | **High** | "Saved" is always empty after a reload (badge 0, hearts off, filter empty) despite data in localStorage |
| 3 | Deep links | **High** | `/#catalog` (and every "Apps" / "Back to catalog" link on app pages) lands ~5,600 px short of the catalog |
| 4 | Source pages | Medium | Hero heading/description clipped off-screen at ≤340 px viewports |
| 5 | `/sources/` | Medium | Source cards overflow the viewport by 15 px at 320 px |
| 6 | Performance | Medium | Home loads/parses ~2.4 MB of JSON, 101 cards, ~1.5 s of main-thread blocking |
| 7 | Media | Medium | Screenshots + QR codes are hot-linked from third-party hosts (no local mirror) |
| 8 | Search palette | Low | 1-character queries return nothing (only the static "POPULAR" chips) |
| 9 | SEO/IA | Low | `/translation-status/` is an orphan page and missing from `sitemap.xml` |
| 10 | SEO | Low | Two source pages share an identical `<title>`/meta description |
| 11 | Legibility | Low | Badge/meta text at 9.5–10.5 px below 4.5:1 contrast (3.27–3.82:1) |
| 12 | A11y polish | Low | Search inputs are 23.7 px tall (< 24 px target size); screenshot `<img>`s lack width/aspect-ratio |

---

## Status — re-checked 2026-09-18 (branch `arena/01a0b463-omnisource`)

This report predates the current checkout, and several items in it are fixed.
The table below is a code-level re-check, not a re-measurement: there is no
browser in this environment, so nothing here is a rendered-page result. Items
whose only evidence is a pixel or contrast measurement are marked **not
re-checkable here** rather than guessed at.

| # | Item | Status | Evidence in the current tree |
| --- | --- | --- | --- |
| 1 | Mobile drawer | **Fixed** | `assets/design-system/components.css:3691` locks scroll on `html.nav-open` (the `position: fixed` trick this report recommended); the comment at line 3646 records that `body.nav-open { overflow: hidden }` was the ineffective approach. `tests/js/drawer_smoke.cjs` drives the real drawer in jsdom and passes |
| 2 | Saved apps never survive a page load | **Fixed** | `js/site.js:39-40` declare `FAVORITES_KEY` / `FAVORITES_LEGACY_KEY` *above* `var state` (line 43), with a comment explaining the `var`-hoisting bug; `state.favorites` initialises at line 61 |
| 3 | Deep links land short | **Fixed** | `js/site.js` re-pins the target after every feed-driven render (`armHashRepin` / `repinHash`), and stops the moment the reader scrolls. Covered by `tests/js/scroll_pin.cjs` |
| 8 | Search palette ignores 1-character queries | **Fixed** | `js/core.js:357` `SEARCH_MIN_CHARS = 1`, with a dedicated `SEARCH_SHORT_THRESHOLD = 0.12` for single characters and a comment citing this finding |
| 9 | Orphan page missing from sitemap | **Fixed** | `sitemap.xml:76` lists `/translation-status/` |
| 10 | Duplicate source-page metadata | **Fixed** | 114 generated source pages, 114 distinct `<title>` values |
| 4, 5, 11, 12 | Hero clipping ≤ 340 px, `/sources/` card overflow at 320 px, small-text contrast, target size / `<img>` sizing | **Not re-checkable here** | All four are layout/contrast measurements; they need a rendering engine |
| 6 | Home page weight and main-thread cost | **Open** | Measurable offline, not yet measured or budgeted |
| 7 | Third-party hot-linked media | **Open** | A mirroring decision, not a code fix |

The scroll-pin fix came with a regression: the re-pin used to be disarmed only
by `wheel`, `touchmove` and the scroll keys, so a reader who scrolled by
**dragging the scrollbar** stayed "armed" and the next deferred feed to land
yanked the catalog back to its own top — which reads exactly as the page
reloading and jumping back to the first section. `js/site.js` now ends the
re-pin on any scroll it did not cause, and a new hash re-arms it. Both
directions are pinned by `tests/js/scroll_pin.cjs`.

**Updated 2026-09-18 (branch `arena/01a0b522-omnisource`).** Item **#6** is now
*budgeted*: `TestHomeWeightBudget` (`tests/test_website_shell.py`) pins the
home page's first-paint JSON — the catalog, `apps.json` and every feed
declared `firstPaint: true` in `js/site.js` — to a 2 MiB ceiling (measured
baseline this day: ~1.47 MiB; the report's 2.4 MB included idle/lazy feeds).
The report's laziness suggestions (one home document, defer
`related`/`reputation`/`verification`) remain follow-ups. Item **#7** is
now *reviewed rather than open*: screenshots and icons must come from
developer-owned hosting, enforced by the provenance rule in
`src/omnisource/validation.py` plus the dated allowlist
`data/screenshot_hosts.json` (the 15 standing warnings are resolved,
`validate.py` reports 0 warnings).

## 1. Mobile drawer scrolls out of view (High)

**Symptom.** On a phone (< 1180 px), open the ☰ menu, then scroll (trackpad, wheel, or a
swipe on the blurred backdrop — `body { overflow: hidden }` does not stop touch scrolling
on iOS). The drawer slides off the top of the screen while the page still believes it is
open: the full-screen backdrop stays up, the whole header is gone, and there is nothing
clickable anywhere — no menu, no close button, no links.

**Measured (390 × 844, mobile emulation):**

| | closed | opened at top | same drawer open, page scrolled 600 px |
|---|---|---|---|
| drawer box | — | top **79**, bottom **599** | top **−521**, bottom **−1** (off-screen) |
| `header.site-header` | top 8 | top 8 | top **−592** (scrolled away, despite `position: sticky`) |
| `body.nav-open` | false | true | **still true** + backdrop still up |

Screenshots: `shots2/drawer-open-top.png` (correct), `shots2/drawer-after-scroll.png`
(broken — blurred page, no menu), `shots2/viewport-scrolled-drawer-open.png`.

**Root cause.** Two interacting things in `assets/design-system/components.css`:

1. In drawer mode the menu is `position: fixed; top: 78px` (line ~3861, ≤1180 px; `top: 70px`
   at ≤760 px) — but it lives inside `.site-header`, which has `backdrop-filter` (line 106).
   A `backdrop-filter` makes the header a **containing block for fixed descendants**, so the
   drawer is positioned relative to the *header*, not the viewport. Measured: the gap
   `drawerTop − headerTop` stays exactly 71 px before and after scrolling.
2. `body.nav-open { overflow: hidden }` (line 3586) is also what makes the sticky header stop
   sticking — the header itself scrolls off (top 8 → −592), taking the drawer with it.

**Suggested fix.** Move the drawer markup (or the `position: fixed` element) out of the
`.site-header` subtree so the viewport is its containing block, and lock the scroll with
`html.nav-open { overflow: hidden; position: fixed; width: 100% }` (the `position: fixed`
trick is the only thing that reliably stops iOS body scrolling). Removing `overflow: hidden`
from `body.nav-open` also restores the sticky header.

---

## 2. Saved apps never survive a page load (High)

**Symptom.** Tap a heart on the home catalog → the chip reads **"❤️ Saved 1"** and the card
shows as saved. Reload the page → chip reads **"❤️ Saved 0"**, the heart is inactive
(`aria-pressed="false"`), and the Saved filter shows 0 cards plus the empty state —
while `localStorage` still contains the entry. The standalone `/favorites/` page *does* show
the app, so only the home catalog loses it.

**Measured.** After save: `omnisource-favorites = ["ytmusic"]`, chip `Saved 1`.
After reload (checked at 2 s, 6 s, 12 s, 20 s — never recovers):
`omnisource-favorites = ["ytmusic"]`, `os:favorites = ["ytmusic"]`, chip `Saved 0`,
`document.querySelectorAll('.favorite.active').length = 0`, Saved filter → 0 cards + empty state.

**Root cause (proven).** `js/site.js`:

```js
45:    favorites: new Set(loadFavorites()),   // runs first …
…
65:    var FAVORITES_KEY = 'omnisource-favorites';      // … but these are `var`s
66:    var FAVORITES_LEGACY_KEY = 'os:favorites';       //     assigned ~20 lines later
```

`var` hoisting means both keys are `undefined` when `loadFavorites()` runs at line 45, so it
reads `localStorage.getItem(undefined)` → `null` → returns `[]`. Every page load therefore
starts with an empty favorites set. Two confirmations:

* in-page, `localStorage.getItem(undefined)` → `null`, while `getItem('omnisource-favorites')`
  → `["ytmusic"]`;
* dispatching the `os:favorites-changed` event after boot (which re-reads the keys, now
  defined) immediately turns the heart on and the chip to `Saved 1`.

**Fix.** Declare the two key constants above `var state = {…}` (or initialise
`favorites: new Set()` in the literal and re-assign `state.favorites = new Set(loadFavorites())`
inside `boot()`).

---

## 3. Deep links to the catalog land thousands of pixels short (High)

**Symptom.** `/...#catalog` and every "Apps" / "Back to catalog" link on the generated app and
source pages (`../../#catalog`) put you in the middle of the page — around the *Trending*
section — not at the catalog. The first thing a visitor clicks from an app page ("Apps")
misdirects them.

**Measured (1440 × 900):**

| Entry point | Result |
|---|---|
| load `/#catalog` directly | scrollY **2,044**, `#catalog` is **5,693 px below** the viewport (needed ≈ 7,700) — still wrong after 15 s |
| `/apps/delta/` → click "Apps" (`../../#catalog`) | scrollY 2,159, `#catalog` 5,578 px below |
| home page → click the "All Apps" quick tab (already-loaded page) | scrollY 7,589, `#catalog` top **148 px** ✓ correct |
| load `/#trending` directly | `#trending` lands 494 px low (should be ≈148) |

**Root cause.** The fragment scroll happens on load, while the sections above the catalog are
still empty. The feeds then arrive and those sections grow by several thousand pixels
(`body[data-page="home"]` renders trending/recent/featured/whats-new/statistics/health on top of
the catalog), so whatever offset the engine scrolled to is stale and nothing corrects it
(`js/site.js` boot only special-cases `#slug` for the app dialog, line ~2627). Fully loaded, the
same anchors land correctly — which is why in-page tab clicks work.

**Fix.** After the first render completes, if `location.hash` names an element, re-run the
scroll (e.g. `element.scrollIntoView()` after `loadData()` resolves, or a `MutationObserver`
correction as `js/core.js` already does for deferred anchors at line ~1317).

---

## 4. Source-page hero text is clipped at ≤ ~340 px (Medium)

**Symptom.** On narrow phones the `SOURCE EXPLORER` heading and its description run off the
right edge and are cut mid-word (e.g. "BlueWallet/BlueWalle…"), with no way to scroll to them
because `html`/`body` are `overflow-x: hidden`.

**Measured** (`/sources/bluewallet-bluewallet/`, 88 px icon + 22 px gap + text):

| viewport | hero text right edge | clipped? |
|---|---|---|
| 320 px | **353** (33 px past the edge) | **yes** |
| 360 px | 353 | no (just fits) |
| 390 px+ | ≤ viewport | no |

28 of the 85 source pages overflow at 320 px; pages with longer names overflow further
(`Bartuzen/qBitController` needs 360.7 px, so it clips even at 360 px).

**Root cause.** `src/omnisource/source_pages.py` `_PAGE_STYLE` (lines 179–181):

```css
.src-hero{display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap}
```

The text `<div>` gets no `min-width: 0`, and flex items default to `min-width: auto`
(min-content), so the 338 px-wide text column can never shrink — the same class of bug the
catalog-tools comment in `components.css` (~line 3230) says was fixed for the home filters.

**Fix.** Add `.src-hero > div{min-width:0}` and `overflow-wrap:anywhere` on the `h1`/`p`.

---

## 5. `/sources/` cards overflow the viewport at 320 px (Medium)

**Symptom.** At 320 px each source card is 320 px wide inside a 290 px container: 15 px of
every card (padding + right-edge text) is clipped, with no horizontal scrolling to reach it.

**Measured** (`_site/sources/index.html`, first card):

| viewport | grid width | computed track | card right edge | clipped |
|---|---|---|---|---|
| 320 px | 290 | `320px` | **335** | **15 px** |
| 360 px+ | ≥330 | fits | ≤ viewport | no |

**Root cause.** `.src-app-grid{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}`
(`src/omnisource/source_pages.py:184`) plus the card's own min-content width — `auto-fill`
with a hard 300 px floor cannot shrink below the available 290 px.

**Fix.** `minmax(min(300px,100%),1fr)` and `min-width: 0` on the grid items.

---

## 6. Home page weight and main-thread cost (Medium)

The home page fetches **65 same-origin resources** and parses **~2.4 MB of JSON** before the
catalog is complete:

| document | decoded size |
|---|---|
| `/apps.json` | 850 KB |
| `/feeds/install.json` | 264 KB |
| `/catalog.json` | 231 KB |
| `/feeds/updates.json` | 211 KB |
| `/feeds/related.json` | 201 KB |
| `/discovery.json` | 175 KB |
| `/feeds/search-index.json` | 120 KB |
| + reputation, verification, health, trending, asset-manifest, … | ~360 KB |

It then renders 101 cards (4,884 DOM nodes — `content-visibility: auto` on `.apps-grid` helps
paint, not parse). Measured on a 2-core sandbox VM: 5–6 `longtask` entries during boot, the
largest **957 ms**, ~1.5 s blocked in total. (This VM is slower than a phone, so treat the
absolute numbers as an upper bound — but 850 KB for the feed plus a separate 231 KB catalog
plus 7 more JSON documents *are* real parse costs on a mid-range device.) `apps.json` and
`catalog.json` also carry overlapping data.

**Suggestions.** Serve the home catalog from one document (`catalog.min.json` already exists,
236 KB→minified), load `related`/`reputation`/`verification` lazily on interaction, and defer
`install`/`updates` until the sections that use them are visible.

---

## 7. Third-party hot-linked media (Medium)

Screenshots and QR codes are loaded straight from other people's hosts, with no local mirror
and no on-page fallback:

| host | references |
|---|---|
| `raw.githubusercontent.com` | 79 |
| `pub-dd800571175348b6a2745b3396360028.r2.dev` | 39 |
| `repo.ikghd.me` | 16 |
| `source.ryuksign.com` | 14 |
| `aidoku.app` | 3 |
| `api.qrserver.com` (QR images) | every QR dialog |

If an upstream repo, Pages branch or R2 bucket disappears, those galleries break silently
(`img` alt text only). It is also a third-party request per visitor. Note: the pipeline's
verification covers app artifacts, not these images.

**Suggestion.** Mirror screenshots into `assets/` during the build (the size-check step in
`scripts/audit_site_integrity.py` is a natural place to add a reachability check), and render
QR codes locally instead of via `api.qrserver.com`.

---

## 8. Search palette ignores 1-character queries (Low)

Typing `a`, `d`, or nothing shows only the non-filtered "POPULAR" chip list — `rows = 0`, so
arrow-key selection does nothing; from 2 characters on it works (`de` → 8 results, `delta` → 1,
Enter opens the app). `js/core.js:1691` (`live.length < 2`) is the gate. Fine as a deliberate
minimum, but the empty state gives no hint that a second character is needed — consider a
"keep typing…" message or allowing exact prefix matches for single characters.

## 9. Orphan page + sitemap (Low)

`/translation-status/` has **no inbound links** from any page (only a self-canonical) and is
**absent from `sitemap.xml`** (204 URLs, all valid). Either link it from the footer/docs and add
it to the sitemap, or drop it.

## 10. Duplicate source page metadata (Low)

`/sources/https-source-ryuksign-com-ig410/` and `/sources/https-source-ryuksign-com-duplicate/`
produce the same generated name, so both ship the identical `<title>`
("Ryuk / RyukSign (source.ryuksign.com) — OmniSource") and meta description. (They are two
different apps — RyukGram (IG 410) and RyukGram Side by Side — so this is a naming collision,
not duplicate content.)

## 11. Small-text contrast (Low)

Measured against blended backgrounds:

| element | theme | ratio | size |
|---|---|---|---|
| `.badge.cyan` ("Deep link") | light | **3.27:1** | 9.5 px |
| `.badge.community`, `.badge.stable`, `.badge.ok` | light | **3.82:1** | 9.5 px |
| `.badge.warn`, `.score-pill` | light | 3.87–4.2:1 | 9.5–10.5 px |
| `.tab-count` | dark | **3.63:1** | 10.5 px |

All below the 4.5:1 AA threshold for small text. Bumping these to a darker tint (or ≥ 12 px /
600 weight) fixes it. (An earlier "invisible text on `/status/` in dark mode" hit was a false
positive of the audit harness — the screenshot `shots2/status-sync-dark.png` shows it is
legible; the probe resolved the colour against a gradient background.)

## 12. Target size and image sizing (Low)

* `#searchInput`, `#dSearch`, `#graphSearch`, `#searchPageInput` are **23.7 px** tall — just
  under the WCAG 2.5.8 (AA) 24 px minimum.
* 50 of the screenshot `<img>`s in app-page galleries (`.ap-screenshots img`) have no
  `width`/`height` or `aspect-ratio` (only a CSS `height: 380px`), so the strip reflows while
  images load. Adding `aspect-ratio` per screenshot (the build knows the dimensions) removes it.
* In-page targets `#qrTitle`, `#installAutoAdd`, `#pairs` land under the sticky header when
  scrolled to programmatically (no user-facing link points at them today, so this is latent).
* **Tab-stop count**: the home page exposes **725 tabbable elements** — 101 `<article class="app-card" tabindex="0">`
  plus their title links, hearts and "View details" links (540 stops inside `#catalog` alone).
  Enter on a focused card does open the dialog, and the skip link jumps past the chrome, but
  traversing the catalog by keyboard is hundreds of presses. A roving-tabindex/listbox pattern
  (one stop per row, arrows to move) would fix it, as the category chip row already does.

---

## What checked out (no action needed)

* **Structure**: 207 pages; static crawl found **0 broken internal links, 0 missing assets,
  0 links to non-existent anchors, 0 duplicate ids, 0 `<img>` without `alt`, 0 pages without a
  meta description**, and no 404s on any same-origin resource. `sitemap.xml` has 204 URLs, all
  resolving; `manifest.webmanifest` icons exist; `/compare.html` redirects to `/compare/` cleanly.
* **Layout/scroll**: no horizontal page scroll at any width 320–1440 px (everything that
  overflows is clipped by design *except* items 4 and 5); no nested scroll traps; footer/bottom
  content is never covered by the mobile tab bar; `.bottom-nav` items are 52×52 px and inside
  the viewport; sticky bars don't cover real anchor targets that users click (the home quick
  tabs land the section 148 px from the top, below the 62 px header + 73 px tab rail).
* **Catalog**: search, category chips, provenance sub-tabs, device filter, sort, "reset
  filters", and the empty state all work; 101 cards render and a focused card opens with
  Enter/Space (`tabindex="0"` is present, the delegated handler fires).
* **Dialogs**: the app dialog opens from a card tap, is a real modal (`:modal`), fits the
  viewport, moves focus inside, traps Tab, switches About/Versions/Details, and Esc closes it
  and restores the previous scroll position. `#slug` deep links open the right app.
* **State**: theme cycle auto→light→dark persists; compact view persists; heart toggling works
  in-session; the standalone `/favorites/`, `/collections/`, `/status/`, `/analytics/`,
  `/discover/`, `/graph/`, `/docs/`, `/compare/?left=…&right=…`, `/search/?q=…` pages all render
  and function.
* **i18n**: 9 locales switch instantly, persist across pages, and leave no raw translation keys.
* **A11y basics**: skip link is the first tab stop and visible on focus, tab order is sane
  (no zero-size focusables), reduced-motion is honoured, `aria-pressed`/`aria-selected`/
  `aria-modal` are maintained.

## Method & caveats

* Build: `python3 scripts/build_site.py` (107 pages reported, 207 HTML files including
  per-app/source pages); served with `python3 -m http.server` from `_site`.
* Browser: Chromium 153 headless (Blink). **WebKit/Safari was not available in this sandbox**, so
  the drawer bug's iOS-touch reachability is inferred from `body { overflow: hidden }` semantics
  rather than measured on an iPhone; everything else was measured directly.
* Third-party hosts are blocked by the sandbox network, so external screenshots/QR codes could
  not be verified as reachable — they were therefore counted as a fragility risk (item 7), not
  reported as broken.
* Smooth scrolling was disabled for anchor measurements (`html{scroll-behavior:auto}`); with it
  enabled, readings taken mid-animation look like jumps and were deliberately discarded.
