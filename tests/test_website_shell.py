"""Invariants for the static website shell (Liquid Glass + GH Pages).

The repository root holds the site *sources* (index.html, install/, js/,
feeds/, apps/, …) plus the published ``/apps.json`` installable source URL and
the machine API surface under ``api/``. ``scripts/build_site.py`` additionally
assembles the deployable site (including the full historical flat URL family)
into ``_site/`` for the GitHub Actions deployment path. These invariants
protect the pieces the builder and the deployed site depend on.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class TestWebsiteShell(unittest.TestCase):
    def test_core_resolves_root_without_current_script(self) -> None:
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("function detectRoot()", core)
        self.assertIn("document.getElementsByTagName('script')", core)
        self.assertIn("function asset(name)", core)
        self.assertIn("asset: asset", core)
        self.assertIn("setupMobileNav()", core)
        self.assertIn("OS.closeNav", core)

    def test_site_uses_asset_helper_and_compare_query(self) -> None:
        site = (ROOT / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("OS.asset(", site)
        self.assertIn("?left=", site)
        self.assertIn("&right=", site)
        self.assertNotIn("#/' + Compare.left", site)

    def test_install_page_has_title(self) -> None:
        html = (ROOT / "install" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Installation Center — OmniSource</title>", html)
        self.assertIn('content="#e8eef8"', html)

    def test_source_feed_is_a_link_and_stacks_on_small_screens(self) -> None:
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        install = (ROOT / "install" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        self.assertIn('id="sourceUrlLink" href="https://iamsmmh.github.io/OmniSource/apps.json"', home)
        self.assertIn('id="installFeedUrlLink" href="https://iamsmmh.github.io/OmniSource/apps.json"', install)
        self.assertIn("@media (max-width: 620px)", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn("overflow-wrap: anywhere", css)

    def test_service_worker_version(self) -> None:
        # The cache version is derived from sw.js instead of being pinned to a
        # literal: the shell was bumped to v13 (dialog positioning fix) while
        # this assertion still demanded "omnisource-v12", which failed the
        # suite — and therefore Sync & Publish and Validate — on every run.
        # What matters is the *floor*: the v12 schema is the first one that
        # precaches the whole shell, so anything older must not ship.
        sw = (ROOT / "sw.js").read_text(encoding="utf-8")
        match = re.search(r"const VERSION\s*=\s*'omnisource-v(\d+)'", sw)
        self.assertIsNotNone(match, "sw.js must declare const VERSION = 'omnisource-v<N>'")
        version = int(match.group(1))  # type: ignore[union-attr]
        self.assertGreaterEqual(version, 12, "the complete-shell cache schema (v12) is the floor")
        # Every cache ring is derived from VERSION, so a bump invalidates all
        # three instead of leaving stale entries behind.
        for ring in ("core", "data", "assets"):
            self.assertIn("${VERSION}-" + ring, sw)
        # The shell precaches the lightweight WebP logo; the PNG stays for
        # favicons, feed iconURLs and non-WebP fallbacks only.
        self.assertIn("'./assets/OmniSource.webp'", sw)
        self.assertNotIn("'./assets/OmniSource.png'", sw)
        # v10/v11 must precache the engines, pages and module layer.
        self.assertIn("discover/index.html", sw)
        self.assertIn("graph/index.html", sw)
        self.assertIn("src/js/data-layer.js", sw)
        self.assertIn("sources/index.html", sw)
        self.assertIn("docs/index.html", sw)
        self.assertIn("js/modules/utils.js", sw)
        self.assertIn("js/modules/sources.js", sw)
        # v12 closed the gaps: the v11 comment claimed all twelve modules were
        # precached when only five were, and AssetManager.js (loaded by every
        # page for icon fallbacks), /translation-status/ and the placeholder
        # artwork were missing entirely — those pages broke on an offline
        # reload. tests/test_service_worker.py asserts the list against disk.
        for module in ("analytics", "collections", "compare", "favorites", "install", "pwa", "store"):
            self.assertIn("js/modules/" + module + ".js", sw, module + " module is not precached")
        self.assertIn("website/assets/AssetManager.js", sw)
        self.assertIn("translation-status/index.html", sw)
        self.assertIn("assets/placeholders/app.svg", sw)

    def test_homepage_stat_markers(self) -> None:
        # The site builder (site._inject_homepage_stats) rewrites these
        # markers in the deployed _site/index.html copy and fails loudly
        # when one disappears — keep them stable.
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        for marker in (
            'id="statApps" data-count>',
            'id="statSources" data-count>',
            'id="statOnline" data-count>',
            'id="statVerified" data-count>',
            'id="healthLabel">',
            'id="statSyncLabel"',
            'id="tabCountAll">',
        ):
            self.assertIn(marker, home, f"home page stat marker missing: {marker}")
        # The committed home page carries the real values from the last build,
        # not the 0 placeholders (no-JS visitors and crawlers read these).
        self.assertNotIn('id="statApps" data-count>0<', home)

    def test_pages_deploys_from_site_artifact(self) -> None:
        # The GitHub Actions deploy path assembles _site/ and publishes it.
        # The same builder also mirrors the generated URLs into the root,
        # because the repository is (also) served by GitHub's branch build.
        # A Jekyll config must not come back: `.nojekyll` is the only Pages
        # configuration the repository carries.
        self.assertFalse(
            (ROOT / "_config.yml").exists(),
            "_config.yml must stay deleted: the generated mirror is served verbatim, not rendered by Jekyll",
        )
        self.assertTrue((ROOT / ".nojekyll").exists(), "the deployed root must disable Jekyll processing")
        sync = (ROOT / ".github" / "workflows" / "sync.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/build_site.py", sync)
        self.assertIn("actions/upload-pages-artifact", sync)
        self.assertIn("actions/deploy-pages", sync)
        # ...but only when Pages is configured to build from Actions. This
        # repository publishes from the branch, where deploy-pages fails the
        # whole run for a configuration that works as designed, so the step is
        # guarded by a runtime probe of the Pages build_type.
        self.assertRegex(
            sync,
            r"if: steps\.pages-mode\.outputs\.deploy == 'true'\s+uses: actions/deploy-pages",
        )

    def test_installable_source_url_is_published_at_the_root(self) -> None:
        # https://iamsmmh.github.io/OmniSource/apps.json is the URL installers
        # register, and GitHub Pages serves it from this branch — so the file
        # must exist in the repository, byte-identical to feeds/apps.json.
        feed = ROOT / "feeds" / "apps.json"
        source = ROOT / "apps.json"
        self.assertTrue(source.is_file(), "/apps.json is missing: the installable source URL would 404")
        self.assertEqual(source.read_bytes(), feed.read_bytes(), "/apps.json diverged from feeds/apps.json")

    def test_repo_root_publishes_apps_json_and_api_mirror(self) -> None:
        # The repository root stays clean: /apps.json (the installable source
        # URL) is the only feed mirrored at the root, and the machine API
        # surface lives under /api/. Every copy stays byte-identical to
        # feeds/. The full flat URL family is assembled into _site/ by the
        # builder instead of being committed to the tree.
        from omnisource.site import API_DOCUMENTS, API_ROUTES

        feeds = ROOT / "feeds"
        source = ROOT / "apps.json"
        self.assertTrue(source.is_file(), "/apps.json is missing: the installable source URL would 404")
        self.assertEqual(source.read_bytes(), (feeds / "apps.json").read_bytes(), "/apps.json diverged from feeds/")

        for name in sorted(API_DOCUMENTS):
            canonical = feeds / name
            if not canonical.exists():
                continue
            mirror = ROOT / "api" / name
            self.assertTrue(mirror.is_file(), f"API URL missing from the repository root: api/{name}")
            self.assertEqual(mirror.read_bytes(), canonical.read_bytes(), f"api/{name} diverged from feeds/{name}")
        for name in ("catalog.json", "catalog.min.json", "index.json", *API_ROUTES):
            self.assertTrue((ROOT / "api" / name).is_file(), f"missing API document: api/{name}")
        for name in ("sitemap.xml", "robots.txt"):
            self.assertTrue((ROOT / name).is_file(), f"missing published file: {name}")

    def test_root_carries_no_hand_maintained_json_besides_the_catalog(self) -> None:
        # The publisher owns every root-level *.json/*.xml except the
        # hand-maintained catalog.json, published apps.json, security report,
        # and sitemap; it prunes anything else, so an accidental root document
        # would be deleted at the next build.
        allowed = {"catalog.json", "apps.json", "security-report.json", "sitemap.xml"}
        for path in sorted(ROOT.glob("*")):
            if path.is_file() and path.suffix.lower() in {".json", ".xml"}:
                self.assertIn(path.name, allowed, f"unexpected hand-maintained root file: {path.name}")

    def test_no_page_loads_a_non_existent_catalog_fallback(self) -> None:
        # The catalog fallback used to be ../feeds/catalog.json, which has
        # never existed (the discovery index lives at feeds/discovery.json):
        # when the API copy 404'd the pages rendered empty forever.
        for rel in ("collections/index.html", "collections/collection.html", "favorites/index.html"):
            html = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("feeds/catalog.json", html, f"{rel}: feeds/catalog.json does not exist")
            self.assertIn("OS.loadCatalog(", html, f"{rel}: must load the catalog through OS.loadCatalog()")
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("OS.loadCatalog = function", core)
        for source in ("api/catalog.json", "feeds/discovery.json", "discovery.json"):
            self.assertIn(f"'{source}'", core, f"OS.loadCatalog lost the {source} source")

    def test_every_internal_reference_resolves(self) -> None:
        # GitHub Pages serves this repository (and the _site/ artifact) as a
        # static tree, so a local href/src/fetch() that points at a file which
        # is not published is a guaranteed 404 — including the flat feed URLs
        # the generated app pages and the installer buttons depend on.
        # Schemes with their own handling (altstore://, sidestore://, …) and
        # template interpolations are skipped.
        attribute = re.compile(r"""(?:href|src|poster|action)\s*=\s*["']([^"'<>]+)["']""", re.I)
        fetch = re.compile(r"""(?:fetch|fetchJSON)\(\s*[`'"]([^`'"$]+)[`'"]""")
        os_url = re.compile(r"""OS\.url\(\s*['"]([^'"$]+)['"]""")
        schemes = ("#", "/", "data:", "mailto:", "tel:", "javascript:", "blob:", "http://", "https://", "//")

        srcset = re.compile(r"""srcset\s*=\s*["']([^"']+)["']""")

        def local_refs(page: Path, text: str) -> list[str]:
            refs = attribute.findall(text) + fetch.findall(text) + os_url.findall(text)
            for value in srcset.findall(text):
                refs += [part.strip().split(" ")[0] for part in value.split(",")]
            return [
                ref
                for ref in refs
                if ref
                and not ref.startswith(schemes)
                and not re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I)
                and "${" not in ref
            ]

        checked = 0
        for page in sorted(ROOT.rglob("*.html")):
            if "_site" in page.parts:
                continue
            text = page.read_text(encoding="utf-8", errors="replace")
            for ref in local_refs(page, text):
                target = (page.parent / ref.split("#")[0].split("?")[0]).resolve()
                if not str(target).startswith(str(ROOT)):
                    continue
                checked += 1
                exists = target.is_file() or (target / "index.html").is_file()
                self.assertTrue(exists, f"{page.relative_to(ROOT)}: broken reference {ref!r}")

        # Client scripts resolve their URLs against the site root (OS.url).
        for script in (ROOT / "js" / "core.js", ROOT / "js" / "site.js", ROOT / "js" / "features.js", ROOT / "sw.js"):
            text = script.read_text(encoding="utf-8")
            for ref in fetch.findall(text) + os_url.findall(text):
                if not ref or "${" in ref or ref.startswith(schemes):
                    continue
                if script.name == "sw.js" and ref.startswith("./"):
                    ref = ref[2:]
                target = (ROOT / ref).resolve()
                if not str(target).startswith(str(ROOT)) or target.is_dir():
                    continue
                checked += 1
                self.assertTrue(target.is_file(), f"{script.name}: broken reference {ref!r}")

        self.assertGreater(checked, 500, "expected the reference scan to walk every generated page")

    def test_collection_cards_link_to_their_app_page(self) -> None:
        # OS.asset('/apps/<slug>/') produced '/assets/apps/<slug>/' — a 404 on
        # every card in a collection.
        html = (ROOT / "collections" / "collection.html").read_text(encoding="utf-8")
        self.assertNotIn("OS.asset(`/apps/", html)
        self.assertIn("OS.url(`apps/${app.slug}/`)", html)

    def test_site_build_publishes_flat_subscriber_urls(self) -> None:
        # /apps.json is the installable source URL for existing clients; the
        # builder must publish it (and every other flat URL) byte-identical
        # to feeds/, plus sitemap/robots and live homepage statistics.
        from omnisource.site import build_site

        tmp = Path(tempfile.mkdtemp(prefix="omnisource-site-test-", dir=ROOT))
        try:
            summary = build_site(tmp)
            self.assertGreater(summary["flat_files"], 0)
            for pattern in ("*.json", "*.xml"):
                for canonical in sorted((ROOT / "feeds").glob(pattern)):
                    if canonical.name == "state.json":
                        continue
                    flat = tmp / canonical.name
                    self.assertTrue(flat.is_file(), f"flat URL missing from the built site: {canonical.name}")
                    self.assertEqual(
                        flat.read_bytes(),
                        canonical.read_bytes(),
                        f"flat URL diverged from feeds/: {canonical.name}",
                    )
            self.assertTrue((tmp / "catalog.json").is_file())
            self.assertTrue((tmp / "sitemap.xml").is_file())
            self.assertTrue((tmp / "robots.txt").is_file())
            self.assertTrue((tmp / "feeds" / "apps.json").is_file())
            self.assertTrue((tmp / "api" / "apps.json").is_file())
            home = (tmp / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('id="statApps" data-count>0<', home)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nav_never_pushes_controls_off_page(self) -> None:
        # The header row is wider than the 1200px shell on desktops (7 links
        # + controls). The links absorb the squeeze by *relocating* into the
        # "More" menu (js/core.js setupNavFit), never by clipping their text,
        # so the controls stay on-page AND every label stays readable.
        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.nav-links \{[^}]*flex: 0 1 auto;[^}]*min-width: 0;")
        self.assertRegex(css, r"\.nav-controls \{[^}]*flex: none;")
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("@media (max-width: 430px)", css)

    def test_nav_labels_are_never_truncated(self) -> None:
        # Regression: `.nav-links a` carried `overflow: hidden` +
        # `text-overflow: ellipsis` with `min-width: 0`, so the row squeezed
        # the labels and the header read "Apps / Collec… / Sour… / Stat…".
        # Every translation is wider than the English (German "Sammlungen",
        # Spanish "Colecciones", Japanese "コレクション"), so the clipping was
        # worst immediately after a language switch.
        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        rules = re.findall(r"([^{}]*\.nav-links a[^{}]*)\{([^{}]*)\}", css)
        self.assertTrue(rules, "no .nav-links a rules found at all")
        for selector, body in rules:
            self.assertNotIn("text-overflow: ellipsis", body, selector.strip() + " still ellipsizes nav labels")
        # The two structural rules pin each link to its natural width so the
        # overflow guard — not the box model — decides what fits.
        for selector in (".nav-links a", ".ap-header-inner .nav-links a"):
            body = re.search(re.escape(selector) + r" \{([^{}]*)\}", css)
            self.assertIsNotNone(body, selector + " rule disappeared")
            self.assertRegex(body.group(1), r"flex: none;", selector + " must not shrink below its label")
        # And the guard itself must exist and run.
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("function setupNavFit()", core)
        self.assertIn("setupNavFit();", core, "the guard must run at boot")
        self.assertRegex(
            core,
            r"addEventListener\('i18n:changed', schedule\)",
            "translated labels change width, so re-measure on switch",
        )

    def test_nav_drawer_breakpoint_matches_css(self) -> None:
        # core.js decides when the drawer owns the links; components.css
        # decides when it is *styled*. They drifted apart once already
        # (1100 in JS vs 1180 in CSS), so pin them to the same number.
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        js_bp = re.search(r"var NAV_DRAWER_WIDTH = (\d+);", core)
        self.assertIsNotNone(js_bp, "NAV_DRAWER_WIDTH constant missing from core.js")
        self.assertNotRegex(core, r"window\.innerWidth [<>]=? 1100", "stale hardcoded breakpoint in core.js")
        self.assertIn("@media (max-width: " + js_bp.group(1) + "px)", css)
        self.assertIn(".nav-toggle { display: grid; }", css)

    def test_language_switcher_is_on_the_main_screen(self) -> None:
        # The switcher used to be a <select> that injected CSS hid below the
        # drawer breakpoint, so on a phone the only copy lived inside the
        # hamburger menu. It is now a globe button in `.nav-controls` that
        # renders at *every* breakpoint, plus a pill row in the home-page
        # hero, so changing language never requires opening a menu.
        features = (ROOT / "js" / "features.js").read_text(encoding="utf-8")
        self.assertIn("_buildHeaderPicker()", features)
        self.assertIn("_buildHeroPicker()", features)
        self.assertIn("syncLanguageControls()", features)
        for marker in ("lang-picker", "lang-toggle", "hero-lang", "lang-pill"):
            self.assertIn(marker, features)
        # The old drawer-only copy and its hide-rule must stay gone.
        self.assertNotIn(".nav-controls .language-selector { display: none; }", features)
        self.assertNotIn("nav-lang", features)
        # ARIA contract for the dropdown.
        self.assertIn("aria-haspopup', 'listbox'", features)
        self.assertIn("role', 'option'", features)
        self.assertIn("aria-selected", features)

        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        for selector in (".lang-picker", ".lang-toggle", ".lang-menu", ".lang-option", ".hero-lang", ".lang-pill"):
            self.assertIn(selector, css, selector + " is not styled by the design system")
        self.assertIn(".lang-menu[hidden] { display: none; }", css)
        # On narrow screens the header button drops its two-letter code but
        # must never disappear.
        self.assertRegex(css, r"@media \(max-width: 560px\) \{[^}]*\.lang-toggle \.lang-code \{ display: none; \}")
        self.assertNotIn(".lang-picker { display: none", css)

    def test_language_switcher_covers_every_supported_locale(self) -> None:
        # The picker must offer exactly the locales src/js/i18n.js can load;
        # otherwise a language appears in one list and 404s in the other.
        features = (ROOT / "js" / "features.js").read_text(encoding="utf-8")
        i18n = (ROOT / "src" / "js" / "i18n.js").read_text(encoding="utf-8")
        picker = set(re.findall(r"^\s{6}(\w\w): \{ name: '", features, re.M))
        runtime = set(re.findall(r"'(\w\w)'", re.search(r"var supported = \[(.*?)\];", i18n, re.S).group(1)))
        bundles = {path.stem for path in (ROOT / "locales").glob("*.json")}
        self.assertTrue(picker, "no languages found in the picker table")
        self.assertEqual(picker, runtime, "picker languages != i18n runtime languages")
        self.assertEqual(picker, bundles, "picker languages != locales/*.json bundles")

    def test_install_button_does_not_wait_for_beforeinstallprompt(self) -> None:
        # `beforeinstallprompt` is Chromium-only: it never fires on iOS Safari
        # or Firefox. The button was `display: none` until that event, so on
        # exactly the devices this catalog is for it never appeared at all.
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("function setupInstallPrompt()", core)
        self.assertIn("setupInstallPrompt();", core, "the button must be revealed at boot")
        self.assertIn("classList.add('is-visible')", core)
        # …and when no native prompt exists it must go somewhere useful
        # rather than being a dead button.
        self.assertIn("location.href = url('install/')", core)
        # Bound once: beforeinstallprompt can fire repeatedly and used to
        # stack a new click listener each time.
        self.assertRegex(core, r"btn\.dataset\.bound === '1'")

    def test_focus_restore_does_not_build_a_selector_from_data(self) -> None:
        # CodeQL flagged js/incomplete-sanitization here: renderFilters()
        # re-focused the activated chip by interpolating its catalog id into
        # `[data-id="…"]` with only `"` escaped, so a backslash in an id could
        # break out of the string. Matching on dataset properties instead has
        # no escaping surface at all.
        site = (ROOT / "js" / "site.js").read_text(encoding="utf-8")
        self.assertNotIn("[data-kind=\"' +", site)
        self.assertNotIn("[data-id=\"' +", site)
        self.assertNotIn(".replace(/\"/g, '\\\\\"')", site)
        self.assertIn("chips[ci].dataset.kind === focusKind", site)
        self.assertIn("chips[ci].dataset.id === focusId", site)

    def test_os_search_engine_is_not_shadowed_by_features(self) -> None:
        # js/core.js publishes the search *engine* as OS.Search and js/site.js
        # plus core.js call OS.Search.load()/search()/highlight()/.docs.
        # js/features.js runs last, so when it also assigned OS.Search it
        # replaced the engine with its operator UI and every engine call threw
        # "OS.Search.load is not a function" (the /search/ page died). The UI
        # now lives at OS.SearchUI; keep the namespaces disjoint.
        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        features = (ROOT / "js" / "features.js").read_text(encoding="utf-8")
        site = (ROOT / "js" / "site.js").read_text(encoding="utf-8")

        self.assertIn("OS.Search = Search;", core, "core.js must publish the search engine")
        self.assertNotIn("OS.Search =", features, "features.js must not reassign OS.Search")
        self.assertIn("OS.SearchUI = SearchUI;", features, "features.js must export its UI as OS.SearchUI")

        engine_members = {"load", "search", "highlight", "fetchTrending", "score", "popular", "topCategories", "docs"}
        for name in sorted(engine_members):
            self.assertRegex(core, rf"\b{name}: (function|\[)", f"core.js search engine lost {name}")

        # Every OS.Search.<member> call site must resolve on the engine.
        called = {m.group(1) for m in re.finditer(r"OS\.Search\.([A-Za-z_$][\w$]*)", core + features + site)}
        self.assertTrue(called, "expected OS.Search call sites in the client scripts")
        for name in sorted(called):
            self.assertIn(name, engine_members, f"OS.Search.{name} is not part of the core engine")

        # Every OS.SearchUI.<member> call site must resolve on the UI module.
        ui_called = {m.group(1) for m in re.finditer(r"OS\.SearchUI\.([A-Za-z_$][\w$]*)", core + features + site)}
        for name in sorted(ui_called):
            self.assertRegex(features, rf"\b{name}\(\) \{{", f"OS.SearchUI.{name} is not defined in features.js")

    def test_collections_pages_extend_os_collections_after_features_init(self) -> None:
        # features.js only assigns OS.Collections from its DOMContentLoaded
        # init, but these two pages load core.js/features.js synchronously and
        # then extend OS.Collections from an inline <script>. Running that at
        # parse time threw "Cannot set properties of undefined (setting
        # 'showCreateModal')", which aborted the rest of the block — so the
        # catalog fetch never ran and the page rendered empty. The inline
        # block must therefore defer to DOMContentLoaded.
        for rel in ("collections/index.html", "collections/collection.html"):
            html = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("OS.Collections.", html, f"{rel}: expected page-specific Collections extensions")
            body = html.split("<script>", 1)[1].split("</script>", 1)[0]
            self.assertIn("document.addEventListener('DOMContentLoaded'", body, f"{rel}: inline block is not deferred")
            # The guard must precede the first assignment, not follow it.
            self.assertLess(
                body.index("document.addEventListener('DOMContentLoaded'"),
                body.index("OS.Collections."),
                f"{rel}: OS.Collections is touched before the DOMContentLoaded guard",
            )
            self.assertIn("if (!window.OS || !OS.Collections)", body, f"{rel}: missing OS.Collections guard")

    def test_client_scripts_reference_only_shipped_assets(self) -> None:
        # Only the WebP twins plus OmniSource.png are deployed (see
        # site._deployable_asset), so a literal asset reference that resolves
        # to anything else is a guaranteed 404 in production. This catches the
        # 'assets/unknown.png' icon fallback, which never shipped, and the
        # OS.asset('../assets/...') values that the helper turned into
        # 'assets/../assets/...' because it only strips a leading 'assets/'.
        from omnisource.site import _deployable_asset

        asset_arg = re.compile(r"""OS\.asset\(\s*['"]([^'"${}]+?)['"]""")
        html_ref = re.compile(
            r"""(?:href|src|srcset)\s*=\s*["']((?:\.\./)*assets/[^"'${}]+?\.(?:png|webp|svg|jpg|ico))["']"""
        )
        checked = 0

        def resolve_asset(arg: str) -> Path:
            """Mirror js/core.js asset(): strip one leading 'assets/', re-add it."""
            return ROOT / "assets" / re.sub(r"^assets/", "", arg)

        for source in [ROOT / "js" / "core.js", ROOT / "js" / "features.js", ROOT / "js" / "site.js"]:
            for arg in asset_arg.findall(source.read_text(encoding="utf-8")):
                target = resolve_asset(arg)
                checked += 1
                where = f"{source.name}: OS.asset({arg!r})"
                self.assertFalse(arg.startswith(("../", "/")), f"{where} must not be path-prefixed")
                self.assertTrue(target.is_file(), f"{where} resolves to a missing file")
                self.assertTrue(_deployable_asset(target), f"{where} is not deployed to _site/")

        # Static markup resolves ../assets/ against the page's own directory.
        for page in sorted(ROOT.rglob("*.html")):
            if "_site" in page.parts:
                continue
            for raw in html_ref.findall(page.read_text(encoding="utf-8")):
                target = (page.parent / raw).resolve()
                checked += 1
                self.assertTrue(target.is_file(), f"{page.relative_to(ROOT)}: missing asset {raw}")
                self.assertTrue(_deployable_asset(target), f"{page.relative_to(ROOT)}: {raw} is not deployed to _site/")

        self.assertGreater(checked, 10, "expected the asset-reference scan to find the icon literals")

    def test_exported_favorites_page_links_real_stylesheets(self) -> None:
        # getFavoritesPageHTML() writes a standalone document; it used to link
        # css/site.css and css/design-system.css, which have never existed in
        # this repository, so every exported page came out unstyled.
        features = (ROOT / "js" / "features.js").read_text(encoding="utf-8")
        self.assertNotIn("css/site.css", features)
        self.assertNotIn("css/design-system.css", features)
        for sheet in ("tokens.css", "utilities.css", "animations.css", "components.css"):
            self.assertIn(f"design-system/{sheet}", features, f"exported favorites page is missing {sheet}")
            self.assertTrue((ROOT / "assets" / "design-system" / sheet).is_file(), f"{sheet} is not a real stylesheet")

    def test_liquid_glass_tokens(self) -> None:
        tokens = (ROOT / "assets" / "design-system" / "tokens.css").read_text(encoding="utf-8")
        self.assertIn("--glass-nav:", tokens)
        self.assertIn("--blur-liquid:", tokens)
        self.assertIn("--bg: #e8eef8", tokens)
        self.assertIn("--bg: #06060e", tokens)
        self.assertIn("--highlight-inset: inset 0 1px 0 rgba(255, 255, 255, 0.16)", tokens)

    def test_liquid_glass_nav(self) -> None:
        css = (ROOT / "assets" / "design-system" / "components.css").read_text(encoding="utf-8")
        self.assertIn("LIQUID GLASS layer", css)
        self.assertIn(".nav-toggle", css)
        self.assertIn("@media (max-width: 1180px)", css)
        self.assertIn("var(--glass-nav)", css)

    # -- regressions from the website audit ---------------------------------
    # Each of these failed in production while `make check` was green.

    def test_inline_scripts_are_syntactically_valid(self) -> None:
        # collections/index.html shipped an over-escaped string literal that
        # made its only <script> block a syntax error, so the page's entire
        # logic never ran and the grid stayed as skeletons forever. The smoke
        # test passed because it only syntax-checked external files.
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        block = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script[^>]*>", re.DOTALL | re.IGNORECASE)
        checked = 0
        with tempfile.TemporaryDirectory() as tmp:
            for page in sorted(ROOT.rglob("*.html")):
                if "_site" in page.parts or any(part.startswith(".") for part in page.parts):
                    continue
                text = page.read_text(encoding="utf-8", errors="replace")
                for index, match in enumerate(block.finditer(text)):
                    attrs = match.group("attrs") or ""
                    body = match.group("body") or ""
                    if "src=" in attrs or "application/ld+json" in attrs or not body.strip():
                        continue
                    scratch = Path(tmp) / f"{page.parent.name}-{page.stem}-{index}.js"
                    scratch.write_text(body, encoding="utf-8")
                    result = subprocess.run(
                        [node, "--check", str(scratch)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    checked += 1
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{page.relative_to(ROOT)} inline script #{index}: {result.stderr.strip()[:200]}",
                    )
        self.assertGreater(checked, 80, "expected every section and app page's inline script to be checked")

    def test_language_change_listener_does_not_reapply_translations(self) -> None:
        # OmniI18n.apply() dispatches i18n:changed as its last step, so a
        # listener that calls _applyTranslations() re-enters apply(): measured
        # 610 dispatches per boot, 631 per language switch, ending in an
        # uncaught "Maximum call stack size exceeded" on every page.
        features = (ROOT / "js" / "features.js").read_text(encoding="utf-8")
        listener = re.search(r"addEventListener\('i18n:changed', \(\) => \{(.*?)\n {6}\}\);", features, re.DOTALL)
        self.assertIsNotNone(listener, "the i18n:changed listener disappeared from features.js")
        self.assertNotIn("_applyTranslations", listener.group(1))
        self.assertIn("_applyTitle()", listener.group(1))

    def test_design_system_font_import_precedes_every_rule(self) -> None:
        # @import is only honoured before all other rules. At the bottom of
        # tokens.css browsers dropped it and the declared Inter/Noto stack
        # silently fell back to system fonts.
        tokens = (ROOT / "assets" / "design-system" / "tokens.css").read_text(encoding="utf-8")
        stripped = re.sub(r"/\*.*?\*/", "", tokens, flags=re.DOTALL).lstrip()
        self.assertTrue(stripped.startswith("@import"), "the webfont @import must be the first rule in tokens.css")

    def test_csp_allows_the_webfont_hosts(self) -> None:
        # Otherwise the @import above is blocked a second time over: style-src
        # governs the imported sheet, font-src the files it references.
        from omnisource.app_pages import CSP_DIRECTIVES

        style = next(directive for directive in CSP_DIRECTIVES if directive.startswith("style-src"))
        font = next(directive for directive in CSP_DIRECTIVES if directive.startswith("font-src"))
        self.assertIn("https://fonts.googleapis.com", style)
        self.assertIn("https://fonts.gstatic.com", font)

        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("style-src 'self' 'unsafe-inline' https://fonts.googleapis.com", index)
        self.assertIn("font-src 'self' data: https://fonts.gstatic.com", index)

    def test_social_preview_images_are_absolute(self) -> None:
        # og:image has to be an absolute URL for scrapers, and on the section
        # pages the relative path resolved to a 404 as well.
        meta = re.compile(r'(?:property="og:image"|name="twitter:image")\s+content="([^"]+)"')
        checked = 0
        for page in sorted(ROOT.rglob("*.html")):
            if "_site" in page.parts or any(part.startswith(".") for part in page.parts):
                continue
            for value in meta.findall(page.read_text(encoding="utf-8", errors="replace")):
                checked += 1
                self.assertTrue(
                    value.startswith("https://"),
                    f"{page.relative_to(ROOT)}: preview image is not absolute: {value!r}",
                )
        self.assertGreater(checked, 80, "expected every generated page to declare a preview image")

    def test_sitemap_lists_every_navigable_section(self) -> None:
        # /discover/ and /graph/ are linked from the main navigation but were
        # missing from the sitemap, leaving two indexable pages unlisted.
        from omnisource.site import SITE_PAGES

        sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
        for path, _priority, _changefreq in SITE_PAGES:
            self.assertIn(f"<loc>https://iamsmmh.github.io/OmniSource{path}</loc>", sitemap)

    def test_page_data_load_is_scoped_and_progressive(self) -> None:
        # Every page used to Promise.all the whole feed bundle (~2 MB) before
        # drawing anything, so the catalog and the rails sat as skeletons for
        # seconds and the nav's #trending anchor pointed at a hidden section.
        site = (ROOT / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("var FEEDS = [", site)
        self.assertIn("function feedsForPage(page)", site)
        self.assertIn("firstPaint: true", site)
        self.assertIn("function scheduleRefresh()", site)

        core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("jsonMemo", core)  # one request per feed per page load
        self.assertIn("setupDeferredAnchors()", core)  # scroll once a section is revealed

    def test_collection_templates_escape_every_interpolation(self) -> None:
        # The collection pages build their markup by assigning template literals
        # to innerHTML. collection.html had no escaping at all, so a collection
        # or app name could break out of an attribute and inject markup —
        # CodeQL reported it as soon as the pages became analysable.
        #
        # Rule: on any line that emits markup (contains "<"), any ${…} that
        # reaches user-controlled data (app/collection fields, the collection
        # id) or a URL helper must pass through OS.esc()/jsAttr() somewhere in
        # the expression. The non-HTML uses of the same values (download
        # filename, Web Share title/text, clipboard fallback) emit no markup.
        safe_prefix = ("OS.Favorites.has(",)
        user_data = ("app.", "collection.", "collectionId")

        def interpolations(line: str) -> list[str]:
            found = []
            cursor = 0
            while True:
                start = line.find("${", cursor)
                if start == -1:
                    return found
                depth = 0
                end = start + 1
                while end < len(line):
                    if line[end] == "{":
                        depth += 1
                    elif line[end] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    end += 1
                found.append(line[start + 2 : end].strip())
                cursor = end + 1

        checked = 0
        for page in (ROOT / "collections" / "index.html", ROOT / "collections" / "collection.html"):
            for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
                if "<" not in line or "${" not in line:
                    continue
                for expression in interpolations(line):
                    if not expression:
                        continue
                    checked += 1
                    if not any(token in expression for token in user_data):
                        continue  # literal, count or date — nothing to escape
                    self.assertTrue(
                        "OS.esc(" in expression or "jsAttr(" in expression or expression.startswith(safe_prefix),
                        f"{page.name}:{number}: unescaped markup interpolation ${{{expression}}}",
                    )
        self.assertGreater(checked, 20, "expected the collection templates to be scanned")

    def test_inline_script_regex_tolerates_real_html(self) -> None:
        # CodeQL flagged the extraction regex used by smoke_test: "</script>"
        # does not match "</script >", and HTML lets an end tag carry junk
        # before its ">" that parsers ignore. A page spelled that way would
        # silently skip its syntax check — the same blind spot that let the
        # broken Collections script through in the first place. Load the real
        # regex rather than restating it.
        spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "scripts" / "smoke_test.py")
        assert spec and spec.loader, "cannot load scripts/smoke_test.py"
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)

        def bodies(html: str) -> list[str]:
            return [body for _attrs, body in smoke.INLINE_SCRIPT_RE.findall(html)]

        self.assertEqual(bodies("<script >var a = 1;</script >"), ["var a = 1;"])
        self.assertEqual(bodies('<script type="module">var b = 2;</SCRIPT>'), ["var b = 2;"])
        self.assertEqual(bodies("<script>var d = 4;</script\t\n bar>"), ["var d = 4;"])

        # …and it must not treat an unrelated tag as a script element.
        self.assertEqual(bodies("<scriptx>var c = 3;</scriptx>"), [])

    # -- client deep links --------------------------------------------------
    # Three tables described the same five clients and disagreed with each
    # other, which is why ESign and LiveContainer behaved differently from
    # the AltStore/SideStore links the user compared them against.

    def _js_scheme_table(self, path: Path) -> dict[str, str]:
        source = path.read_text(encoding="utf-8")
        # install.js wraps its table in Object.freeze({...}) and site.js keeps
        # its copy inside an IIFE, so rather than guessing brace depth, pick
        # out the lines that are literally `key: 'scheme://template'`.
        self.assertIn("CLIENT_SCHEMES", source, "CLIENT_SCHEMES table missing from " + path.name)
        entries = re.findall(r"^\s+(\w+): '([a-z][a-z0-9+.-]*://[^']*)',?\s*$", source, re.M)
        self.assertTrue(entries, "no scheme entries found in " + path.name)
        return dict(entries)

    def test_js_scheme_tables_match_the_builder(self) -> None:
        from omnisource.install import CLIENT_PROFILES

        # Compare the scheme *prefix* rather than the whole template: the
        # builder spells Feather's argument `{host}{path}` while the JS uses a
        # single `{hostpath}`, and both resolve to the same URL. What matters
        # is that a tap produces the same deep link the client recognises.
        expected = {cid: profile["scheme"].split("{", 1)[0] for cid, profile in CLIENT_PROFILES.items()}
        self.assertEqual(
            {"altstore", "sidestore", "flarestore", "feather", "esign", "ksign", "livecontainer"},
            set(expected),
            "builder gained/lost a client; update this test too",
        )
        for path in (ROOT / "js" / "modules" / "install.js", ROOT / "js" / "site.js"):
            got = {cid: scheme.split("{", 1)[0] for cid, scheme in self._js_scheme_table(path).items()}
            self.assertEqual(expected, got, path.name + " disagrees with omnisource.install")

    def test_deep_link_query_is_not_fully_percent_encoded(self) -> None:
        # The generated pages keep the feed URL raw in the query; the browser
        # copies used `encodeURIComponent`, which produced
        # `esign://addsource?url=https%3A%2F%2F…`. Clients that parse the
        # query naively reject that, which is why ESign and LiveContainer
        # behaved differently from the AltStore/SideStore links next to them.
        # Both copies must keep `:` and `/` literal and use a function
        # replacer — `$` is not escaped by encodeURIComponent, so a `$&` or
        # `$'` sequence in a feed URL would otherwise be interpreted as a
        # String.replace replacement pattern.
        for path in (ROOT / "js" / "modules" / "install.js", ROOT / "js" / "site.js"):
            source = path.read_text(encoding="utf-8")
            self.assertIn("encodeFeedParam", source, path.name + " lost the narrow feed-URL encoder")
            self.assertNotIn("encodeURIComponent(url)", source)
            self.assertNotIn("encodeURIComponent(feedUrl)", source)
            self.assertNotRegex(
                source,
                r"replace\('\{url\}',\s*(?:encodeFeedParam|hostPath)\(",
                path.name + " must substitute {url}/{hostpath} with a function replacer, not a string",
            )

    def _install_urls_from_node(self, feeds: dict[str, str]) -> dict[str, dict[str, str]]:
        """Run js/modules/install.js in Node and return {case: {client: url}}."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        import json

        from omnisource.install import CLIENT_PROFILES

        harness = "\n".join(
            [
                "globalThis.window = { OS: { url: (p) => 'https://example.org/OmniSource/' + p } };",
                "const m = await import(" + json.dumps(str(ROOT / "js" / "modules" / "install.js")) + ");",
                "const feeds = " + json.dumps(feeds) + ";",
                "const ids = " + json.dumps(sorted(CLIENT_PROFILES)) + ";",
                "const out = {};",
                "for (const [name, feed] of Object.entries(feeds)) {",
                "  out[name] = {};",
                "  for (const id of ids) out[name][id] = m.installUrlFor(id, feed);",
                "}",
                "console.log(JSON.stringify(out));",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "schemes.mjs"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run([node, str(script)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_deep_link_escapes_hostile_feed_urls(self) -> None:
        # CodeQL flagged the encoder for incomplete string escaping: the
        # character class omitted backslash, so a crafted feed URL could put
        # characters into the href that the template never intended to carry.
        # Assert the behaviour rather than the shape of the regex.
        feeds = {
            "backslash": "https://ex.org/a\\b.json",
            "quote_angle": 'https://ex.org/">x.json',
            "dollar_amp": "https://ex.org/$&evil.json",
            "dollar_tick": "https://ex.org/$'tail.json",
            "space": "https://ex.org/a b.json",
            "brace_bar": "https://ex.org/{a|b}.json",
            "hash": "https://ex.org/a#b.json",
        }
        got = self._install_urls_from_node(feeds)

        # The four query-parameter clients put the feed URL after `?url=`,
        # where `&`, `#`, `$` and friends change how the value parses. Nothing
        # RFC-excluded may survive there.
        query_forbidden = ['"', "<", ">", "\\", " ", "\t", "{", "}", "|", "$", "#", "&", "`", "^"]
        for client_id in ("altstore", "sidestore", "flarestore", "esign", "ksign", "livecontainer"):
            for name, url_by_client in got.items():
                url = url_by_client[client_id]
                for char in query_forbidden:
                    self.assertNotIn(
                        char,
                        url,
                        f"{name}/{client_id} left {char!r} raw in the deep link: {url}",
                    )

        # Feather is different on purpose: its argument is a bare host+path,
        # not a query value, and `src/omnisource/install.py` builds it with
        # `netloc + path` and no escaping. `$`, `&` and `'` are legal path
        # sub-delims, so demanding they be escaped would break the
        # byte-identical contract with the builder. Only the characters that
        # would break the href attribute or are illegal in a path are checked.
        path_forbidden = ['"', "<", ">", "\\", " ", "\t", "\n"]
        for name, url_by_client in got.items():
            url = url_by_client["feather"]
            for char in path_forbidden:
                self.assertNotIn(char, url, f"{name}/feather left {char!r} raw in the deep link: {url}")
            self.assertTrue(url.startswith("feather://source/"), f"{name}/feather lost its scheme: {url}")
            self.assertNotIn("://", url[len("feather://source/") :], f"{name}/feather kept the scheme: {url}")

        # `$&` must not have expanded into the matched substring (the reason
        # substitution uses a function replacer rather than a string).
        self.assertNotIn("evil", got["dollar_amp"]["esign"].replace("%26evil", ""))

    def test_module_deep_links_execute_identically_to_the_builder(self) -> None:
        # String comparison above can pass while the code still produces
        # something else, so actually run the module and diff the output.
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        import json

        from omnisource.install import CLIENT_PROFILES, install_url

        client_ids = sorted(CLIENT_PROFILES)
        feed = "https://example.org/OmniSource/feeds/apollo.json"
        harness = (
            "globalThis.window = { OS: { url: (p) => 'https://example.org/OmniSource/' + p } };\n"
            "const m = await import(" + json.dumps(str(ROOT / "js" / "modules" / "install.js")) + ");\n"
            "const out = {};\n"
            "for (const id of " + json.dumps(client_ids) + ") "
            "out[id] = m.installUrlFor(id, " + json.dumps(feed) + ");\n"
            "console.log(JSON.stringify(out));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "schemes.mjs"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run([node, str(script)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        from_js = json.loads(result.stdout.strip().splitlines()[-1])
        for client_id in client_ids:
            self.assertEqual(
                install_url(client_id, feed),
                from_js[client_id],
                "js/modules/install.js disagrees with the builder for " + client_id,
            )

    def test_no_invented_client_schemes_survive(self) -> None:
        # `delta://` and `walle://` were invented in js/modules/install.js;
        # no such install scheme exists and those buttons dead-ended.
        for path in (ROOT / "js" / "site.js", ROOT / "js" / "modules" / "install.js"):
            source = path.read_text(encoding="utf-8")
            for bogus in ("delta://", "walle://"):
                self.assertNotIn(bogus, source, path.name + " still references " + bogus)
            for real in (
                "esign://addsource?url=",
                "livecontainer://sources?url=",
                "altstore://source?url=",
                "sidestore://source?url=",
                "flarestore://source?url=",
                "ksign://addsource?url=",
                "feather://source/",
            ):
                self.assertIn(real, source, path.name + " lost the " + real.split("://")[0] + " scheme")


if __name__ == "__main__":
    unittest.main()
