"""Findability: tab bars, anchor clearance and scroll stability.

The home page is long — hero, four rails, statistics, source health, the
install guide, the catalog and the release feed. Reaching an app meant
scrolling the whole way down, and the scroll itself was unreliable: deferred
feeds un-hid sections *above* the reader (iOS Safari has no scroll anchoring,
so the page jumped), and ``[data-reveal]`` markup injected after boot was
never observed, which left the release feed rendered at ``opacity: 0`` — a
heading with nothing under it.

These invariants cover the two tab bars that replaced the scroll (a sticky
section row and the category tablist), the affordances around them, and the
three scroll fixes. They are source-level like the rest of the shell suite:
the deployed site is static, so what ships is what runs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOME = (ROOT / "index.html").read_text(encoding="utf-8")
CORE = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
SITE = (ROOT / "js" / "site.js").read_text(encoding="utf-8")
CSS = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
TOKENS = (ROOT / "assets" / "design-system" / "tokens.css").read_text(encoding="utf-8")


class TestSectionTabs(unittest.TestCase):
    """The sticky row that replaces "scroll down and hope"."""

    def test_bar_ships_on_the_home_page(self) -> None:
        self.assertIn('id="sectionTabs"', HOME)
        self.assertRegex(HOME, r'<nav class="section-tabs" id="sectionTabs"[^>]*aria-label=')
        self.assertIn('data-i18n-aria-label="tabs.label"', HOME)

    def test_tabs_are_real_anchors_to_real_sections(self) -> None:
        # A tab is a link, not a button: it has to survive JS being off, open
        # in a new tab, and be announced as a destination.
        tabs = re.findall(r'<a class="section-tab" href="#([\w-]+)" data-target="([\w-]+)"', HOME)
        self.assertGreaterEqual(len(tabs), 4, "expected a tab per major section")
        for href, target in tabs:
            self.assertEqual(href, target, "href and data-target must agree")
            self.assertRegex(HOME, rf'id="{re.escape(target)}"', f"#{target} does not exist on the page")

    def test_bar_lives_inside_main_so_top_reaches_the_real_top(self) -> None:
        # #top is <main>. With the bar in flow *before* main, the "Overview"
        # and header "Home" anchors stopped one bar-height short of the top of
        # the page. Inside main, main's border box is where it always was.
        main_at = HOME.index('<main id="top">')
        bar_at = HOME.index('id="sectionTabs"')
        header_at = HOME.index("</header>")
        self.assertGreater(bar_at, main_at, "the tab bar must be the first child of <main>")
        self.assertLess(header_at, main_at)
        self.assertLess(
            bar_at - main_at,
            1200,
            "the tab bar must come before the hero, not somewhere further down",
        )

    def test_bar_is_styled_by_the_design_system(self) -> None:
        for selector in (".section-tabs", ".section-tabs-inner", ".section-tab"):
            self.assertIn(selector, CSS, selector + " is not styled")
        self.assertRegex(CSS, r"\.section-tabs \{[^}]*position: sticky;")
        self.assertRegex(CSS, r"\.section-tabs \{[^}]*top: var\(--sticky-offset\);")
        # Layered under the nav backdrop (55) and the header capsule (60), so
        # the bar dims with the page when the phone drawer opens instead of
        # floating above it, still clickable.
        self.assertRegex(CSS, r"\.section-tabs \{[^}]*z-index: 54;")
        self.assertRegex(CSS, r"\.to-top \{[^}]*z-index: 54;")
        self.assertRegex(CSS, r'\.section-tab\[aria-current="true"\]')

    def test_sticky_offsets_track_the_floating_header(self) -> None:
        # --sticky-offset is the header's bottom edge: top:12px + a 56px
        # capsule + 6px of padding. If either changes, the bar overlaps the
        # capsule or floats away from it.
        self.assertIn("--sticky-offset: 74px;", TOKENS)
        self.assertIn("--tabbar-h: 46px;", TOKENS)
        self.assertRegex(CSS, r"@media \(max-width: 760px\) \{[^}]*--sticky-offset: 66px;")
        self.assertRegex(CSS, r"\.section-tabs \{[^}]*margin-top: 12px;")

    def test_scroll_spy_marks_the_current_tab(self) -> None:
        self.assertIn("function setupSectionTabs()", CORE)
        self.assertIn("setupSectionTabs();", CORE, "the scroll-spy must run at boot")
        self.assertIn("aria-current", CORE)
        # Tabs whose section never arrives are dropped, not left dead.
        self.assertIn("OS.refreshSectionTabs", CORE)
        self.assertIn("OS.refreshSectionTabs()", SITE, "site.js must refresh the tabs as feeds land")

    def test_top_tab_scrolls_to_the_true_top(self) -> None:
        # Left to the browser, #top stops one scroll-padding short.
        self.assertRegex(CORE, r"tab\.dataset\.target !== 'top'")
        self.assertRegex(CORE, r"window\.scrollTo\(\{ top: 0, left: 0, behavior: 'smooth' \}\)")


class TestCategoryTabs(unittest.TestCase):
    """The catalog's category row, promoted to a real tablist."""

    def test_row_is_a_tablist_controlling_a_panel(self) -> None:
        self.assertRegex(HOME, r'<div class="filter-row" id="categoryFilters" role="tablist"')
        self.assertRegex(HOME, r'<div class="catalog-panel" id="catalogPanel" role="tabpanel">')
        # The grid and its empty state both belong to the panel: an empty
        # result is an answer, not the absence of one.
        panel = HOME[HOME.index('id="catalogPanel"') :]
        panel = panel[: panel.index("</section>")]
        self.assertIn('id="appsGrid"', panel)
        self.assertIn('id="emptyState"', panel)

    def test_tabs_carry_the_aria_contract(self) -> None:
        self.assertIn('role="tab"', SITE)
        self.assertIn("aria-selected=", SITE)
        self.assertIn('aria-controls="catalogPanel"', SITE)
        # Roving tabindex: the whole row is one tab stop.
        self.assertRegex(SITE, r"tabindex=\"' \+ \(selected \? '0' : '-1'\)")
        # The panel is named after whichever tab is selected.
        self.assertIn("panel.setAttribute('aria-labelledby', activeTab.id)", SITE)

    def test_tab_ids_are_reduced_not_escaped(self) -> None:
        # A tab id is catalog data interpolated into an id attribute and then
        # read back through aria-labelledby; an id cannot hold a space or a
        # quote, so tabIdFor() strips to a safe set instead of escaping.
        self.assertIn("function tabIdFor(categoryId)", SITE)
        self.assertRegex(SITE, r"replace\(/\[\^A-Za-z0-9_-\]/g, ''\)")

    def test_arrow_keys_walk_the_tablist(self) -> None:
        self.assertIn("ArrowRight", SITE)
        self.assertIn("ArrowLeft", SITE)
        self.assertRegex(SITE, r"current\.closest\('#categoryFilters'\)")
        # Arabic ships, so the arrows follow the writing direction.
        self.assertRegex(SITE, r"document\.documentElement\.dir === 'rtl'")

    def test_bar_is_pinned_for_the_height_of_the_grid(self) -> None:
        self.assertRegex(CSS, r"\.catalog-tabs \{[^}]*position: sticky;")
        # Stacks under the section tabs: one bar height, no magic number.
        self.assertRegex(
            CSS,
            r"\.catalog-tabs \{[^}]*top: calc\(var\(--sticky-offset\) \+ var\(--tabbar-h\)\);",
        )
        self.assertIn(".catalog-tabs { top: var(--sticky-offset); }", CSS)

    def test_category_row_moved_out_of_the_filter_groups(self) -> None:
        # It has to be a direct child of the catalog's .shell to pin for the
        # whole grid; inside .filter-groups it would unstick at the row's own
        # bottom edge. The click handler has to know about both homes.
        self.assertNotRegex(
            HOME,
            r'<div class="filter-groups">\s*(?:<div[^>]*>\s*)*<div class="filter-row" id="categoryFilters"',
        )
        self.assertIn("chip.closest('.catalog-tabs')", SITE)
        self.assertIn("chip.closest('.filter-groups')", SITE)

    def test_shelf_is_shareable_through_the_query_string(self) -> None:
        # The hash already deep-links to an app dialog on the home page, so
        # the shelf lives in ?category= and the two compose.
        self.assertIn("function readCategoryParam()", SITE)
        self.assertIn("function writeCategoryParam()", SITE)
        self.assertIn("function applyCategoryDeepLink()", SITE)
        self.assertIn("applyCategoryDeepLink();", SITE)
        # Read at parse time: the deferred feeds rewrite the URL before boot's
        # promise chain resolves, so reading later finds our own normalisation.
        self.assertRegex(SITE, r"var categoryDeepLink = readCategoryParam\(\);")
        self.assertIn("if (!categoryDeepLinkApplied) return;", SITE)

    def test_a_stale_shelf_cannot_desync_tabs_and_grid(self) -> None:
        self.assertRegex(SITE, r"if \(selectedId !== state\.category\) state\.category = selectedId;")


class TestHeroShelves(unittest.TestCase):
    def test_hero_offers_the_top_categories(self) -> None:
        self.assertRegex(HOME, r'<div class="hero-cats" id="heroCategories"[^>]*hidden>')
        self.assertIn("renderHeroCategories", SITE)
        self.assertIn("'renderHeroCategories',", SITE, "the renderer must run with the rest of the hero")
        self.assertIn("[data-hero-category]", SITE)
        self.assertIn(".hero-cats", CSS)

    def test_a_shelf_tap_lands_on_the_grid_it_filtered(self) -> None:
        self.assertIn("catalogSection.scrollIntoView", SITE)

    def test_pills_mirror_the_selected_tab(self) -> None:
        self.assertIn("syncHeroCategories", SITE)

    def test_rebuild_is_skipped_when_nothing_changed(self) -> None:
        # Every deferred feed calls the home renderers; rebuilding the row each
        # time would drop focus and replay the hero animation for nothing.
        self.assertIn("row.dataset.signature === signature", SITE)

    def test_focus_restore_stays_inside_the_row_that_was_used(self) -> None:
        # The hero offers the same categories now, so a document-wide scan
        # would hand focus to the top of the page instead of back to the row
        # the reader is working in.
        self.assertIn("focused.closest('.filter-row')", SITE)
        self.assertIn("focusScope.querySelectorAll('[data-kind][data-id]')", SITE)
        # …without reintroducing the CodeQL finding this block was rewritten
        # for (tests/test_website_shell.py::test_focus_restore_*).
        self.assertIn("chips[ci].dataset.kind === focusKind", SITE)


class TestScrollStability(unittest.TestCase):
    def test_re_renders_hold_the_readers_place(self) -> None:
        self.assertIn("OS.stableScroll = function (work)", CORE)
        self.assertIn("OS.stableScroll(render)", SITE, "refreshPage() must re-render through it")
        # A correction must never animate: html { scroll-behavior: smooth }
        # would turn it into a second, slower jump.
        self.assertRegex(CORE, r"behavior: 'auto'")

    def test_the_landmark_is_not_sticky_chrome(self) -> None:
        # The header and both tab bars are sticky: they do not move with the
        # document, so they cannot report how far the content shifted.
        self.assertRegex(CORE, r"position === 'fixed' \|\| position === 'sticky'")
        self.assertIn("function scrollLandmark()", CORE)

    def test_nothing_is_corrected_at_the_top_of_the_page(self) -> None:
        self.assertRegex(CORE, r"if \(y < 24\) \{ work\(\); return; \}")

    def test_sections_clear_the_sticky_bars_on_anchor_jumps(self) -> None:
        # scroll-padding (html) clears the floating capsule; the home page
        # needs one more bar for the sticky tabs.
        self.assertIn("scroll-padding-top: calc(var(--header-h) + 20px);", TOKENS)
        self.assertRegex(
            CSS,
            r'body\[data-page="home"\] main > section\[id\] \{\s*scroll-margin-top: calc\(var\(--tabbar-h\) \+ 6px\);',
        )
        # A phone pins only the category bar, so the extra clearance goes.
        self.assertIn('body[data-page="home"] main > section[id] { scroll-margin-top: 0; }', CSS)


class TestRevealSelfHeals(unittest.TestCase):
    """Injected [data-reveal] used to stay at opacity:0 forever."""

    def test_the_observer_is_armed_even_with_no_nodes_at_boot(self) -> None:
        # /collections/ renders nothing but injected [data-reveal] markup, so
        # the old `if (!nodes.length) return;` left the whole grid invisible.
        self.assertNotRegex(CORE, r"var nodes = \$\$\('\[data-reveal\]'\);\s*if \(!nodes\.length\) return;")
        self.assertIn("function setupReveal()", CORE)
        self.assertIn("watchRevealAdditions(observe)", CORE)
        self.assertIn("watchRevealAdditions(show)", CORE, "reduced motion must self-heal too")

    def test_late_arrivals_are_handed_to_the_observer(self) -> None:
        self.assertIn("function watchRevealAdditions(handler)", CORE)
        self.assertRegex(CORE, r"new MutationObserver\(function \(records\)")
        self.assertRegex(CORE, r"observer\.observe\(document\.body, \{ childList: true, subtree: true \}\)")

    def test_there_is_a_failsafe_for_a_missed_callback(self) -> None:
        self.assertRegex(CORE, r"\$\$\('\[data-reveal\]:not\(\.is-revealed\)'\)")

    def test_the_release_feed_is_not_left_invisible(self) -> None:
        # The concrete symptom: renderTimeline() injects [data-reveal] list
        # items and, unlike the metrics/source renderers, never revealed them.
        self.assertIn('class="timeline-item" data-reveal', SITE)


class TestScrollAffordances(unittest.TestCase):
    def test_progress_line_and_back_to_top_are_injected(self) -> None:
        # Injected (like the mobile nav drawer) so the generated app pages and
        # every hand-written section page get them without touching markup.
        self.assertIn("function setupScrollAffordances()", CORE)
        self.assertIn("setupScrollAffordances();", CORE, "must run at boot")
        self.assertIn("scroll-progress", CORE)
        self.assertIn("backToTop", CORE)
        self.assertIn(".scroll-progress", CSS)
        self.assertIn(".to-top", CSS)
        self.assertIn("is-visible", CSS)

    def test_the_bead_stays_out_of_the_way_until_it_is_useful(self) -> None:
        self.assertRegex(CSS, r"\.to-top \{[^}]*pointer-events: none;")
        self.assertRegex(CSS, r"\.to-top\.is-visible \{[^}]*pointer-events: auto;")
        self.assertRegex(CORE, r"Math\.max\(700,")

    def test_progress_cannot_shift_layout(self) -> None:
        self.assertRegex(CSS, r"\.scroll-progress \{[^}]*position: fixed;")
        self.assertRegex(CSS, r"\.scroll-progress \{[^}]*pointer-events: none;")

    def test_document_height_is_not_read_every_frame(self) -> None:
        # scrollHeight forces a layout flush; reading it per frame right after
        # writing the bar's transform is the thrash that makes a long page feel
        # sticky on a phone.
        self.assertIn("function measureDocument(now)", CORE)
        self.assertRegex(CORE, r"now - docHeightStamp > 400")
        self.assertIn("docHeight = 0;", CORE, "a resize must invalidate the cache")

    def test_the_sticky_offset_is_read_from_css_and_cached(self) -> None:
        self.assertIn("function stickyOffset()", CORE)
        self.assertIn("stickyOffsetCache = null;", CORE, "resize/orientation must re-read it")
        self.assertIn("getPropertyValue('--sticky-offset')", CORE)

    def test_overflowing_tab_rows_advertise_more_content(self) -> None:
        self.assertIn("function setupScrollHints()", CORE)
        self.assertIn("setupScrollHints();", CORE)
        self.assertIn("can-scroll-end", CSS)
        self.assertIn("data-scroll-hint-host", HOME)


class TestScrollPaintCost(unittest.TestCase):
    def test_touch_devices_drop_the_fixed_blend_layers(self) -> None:
        # body::after is a fixed, full-viewport `mix-blend-mode: soft-light`
        # layer: the engine reads back and blends the whole page under it on
        # every scrolled pixel. body { background-attachment: fixed } is
        # re-rastered rather than pinned on iOS Safari. Both go on touch.
        coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", TOKENS, re.DOTALL)
        self.assertIsNotNone(coarse, "no coarse-pointer block in tokens.css")
        self.assertIn("background-attachment: scroll;", coarse.group(1))
        self.assertIn("mix-blend-mode: normal;", coarse.group(1))

    def test_the_ambient_drift_pauses_while_scrolling(self) -> None:
        self.assertRegex(TOKENS, r"body\.is-scrolling::before \{ animation-play-state: paused; \}")
        self.assertIn("is-scrolling", CORE)

    def test_reduced_motion_still_parks_the_ambient_layer(self) -> None:
        self.assertRegex(TOKENS, r"@media \(prefers-reduced-motion: reduce\) \{\s*body::before \{ animation: none; \}")


if __name__ == "__main__":
    unittest.main()
