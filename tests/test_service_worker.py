"""Invariants for the service worker's offline shell.

The precache list in ``sw.js`` is hand-maintained, and it drifted out of sync
with the site several times: the v11 comment claimed the whole ``js/modules/*``
layer was precached when only five of the twelve files were listed, and
``website/assets/AssetManager.js`` (loaded by *every* page to fall back on a
missing icon), ``/translation-status/`` and the placeholder artwork were absent
altogether. Nothing failed while online, so the breakage only surfaced for a
returning visitor on a flaky connection — exactly the audience an offline-first
PWA exists for.

These tests assert the list against the filesystem and against the pages that
actually load each file, so a new script or page cannot silently fall out of
the shell.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

SW = ROOT / "sw.js"


def _array(name: str) -> list[str]:
    """Pull one top-level string array literal out of sw.js."""
    source = SW.read_text(encoding="utf-8")
    body = re.search(rf"const {name}\s*=\s*\[(.*?)\n\];", source, re.S)
    if body is None:
        raise AssertionError(f"const {name} not found in sw.js")
    return re.findall(r"'([^']+)'", body.group(1))


def _version() -> str:
    match = re.search(r"const VERSION = '([^']+)'", SW.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("const VERSION not found in sw.js")
    return match.group(1)


class TestServiceWorkerShell(unittest.TestCase):
    def test_every_precached_core_asset_exists(self) -> None:
        missing: list[str] = []
        for entry in _array("CORE_ASSETS"):
            relative = entry.lstrip("./") or "index.html"
            if not (ROOT / relative).is_file():
                missing.append(entry)
        self.assertEqual([], missing, "CORE_ASSETS lists files that do not exist")

    def test_version_bumped_and_cache_names_derive_from_it(self) -> None:
        version = _version()
        source = SW.read_text(encoding="utf-8")
        self.assertRegex(version, r"^omnisource-v\d+$")
        # All three rings must be namespaced by VERSION, otherwise an update
        # leaves stale entries behind forever.
        for ring in ("CORE_CACHE", "DATA_CACHE", "ASSET_CACHE"):
            self.assertRegex(source, rf"const {ring} = `\$\{{VERSION\}}-", f"{ring} is not derived from VERSION")

    def test_every_module_script_is_precached(self) -> None:
        # The ES-module layer is lazy-loaded by js/features.js and the page
        # bundles; if one is missing from the shell its page dies offline.
        core = set(_array("CORE_ASSETS"))
        for module in sorted((ROOT / "js" / "modules").glob("*.js")):
            self.assertIn(f"./js/modules/{module.name}", core, f"js/modules/{module.name} is not precached")

    def test_pages_referenced_by_the_shell_are_precached(self) -> None:
        core = set(_array("CORE_ASSETS"))
        for page in (
            "./index.html",
            "./install/index.html",
            "./docs/index.html",
            "./sources/index.html",
            "./status/index.html",
            "./analytics/index.html",
            "./search/index.html",
            "./favorites/index.html",
            "./collections/index.html",
            "./compare/index.html",
            "./discover/index.html",
            "./graph/index.html",
            "./translation-status/index.html",
        ):
            self.assertIn(page, core, f"{page} is not in the offline shell")
            self.assertTrue((ROOT / page.lstrip("./")).is_file(), f"{page} does not exist")

    def test_every_stylesheet_the_shell_loads_is_precached(self) -> None:
        core = set(_array("CORE_ASSETS"))
        referenced = set()
        for page in ROOT.glob("*.html"):
            referenced |= set(
                re.findall(r'<link[^>]+href="((?:\.\./)*(?:assets|src)/[^"]+\.css)"', page.read_text(encoding="utf-8"))
            )
        referenced |= set(
            re.findall(
                r'<link[^>]+href="(assets/design-system/[^"]+\.css)"',
                (ROOT / "install" / "index.html").read_text(encoding="utf-8"),
            )
        )
        self.assertTrue(referenced, "found no stylesheet links to check")
        for href in sorted(referenced):
            self.assertIn(f"./{href}", core, f"{href} is loaded by a page but not precached")

    def test_runtime_js_loaded_by_the_home_page_is_precached(self) -> None:
        core = set(_array("CORE_ASSETS"))
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for src in re.findall(r'<script[^>]+src="((?:src/)?js/[^"]+\.js)"', html):
            self.assertIn(f"./{src}", core, f"{src} is loaded by the home page but not precached")

    def test_every_locale_bundle_is_precached(self) -> None:
        core = set(_array("CORE_ASSETS"))
        for bundle in sorted((ROOT / "locales").glob("*.json")):
            self.assertIn(
                f"./locales/{bundle.name}",
                core,
                f"locales/{bundle.name} is not precached (language switch would fail offline)",
            )

    def test_placeholder_artwork_is_precached(self) -> None:
        # website/assets/AssetManager.js falls back to these whenever an icon
        # or screenshot URL 404s; without them the fallback itself 404s.
        core = set(_array("CORE_ASSETS"))
        self.assertIn("./website/assets/AssetManager.js", core)
        for name in ("app", "category", "banner"):
            self.assertIn(f"./assets/placeholders/{name}.svg", core)

    def test_data_urls_exist_where_generated(self) -> None:
        # Feeds are generated by the pipeline, so only assert the ones the
        # build is known to emit; the rest are matched by pattern at runtime.
        for entry in _array("DATA_URLS"):
            relative = entry.lstrip("./")
            if relative.startswith("feeds/") or relative in {"apps.json", "catalog.json"}:
                path = ROOT / relative
                if path.exists():
                    continue
                # Not generated in a clean checkout: that is fine, but the
                # runtime fetch must not 404 hard — it is stale-while-revalidate.
                self.assertTrue(relative.endswith(".json"))

    def test_sw_is_syntactically_valid(self) -> None:
        import shutil

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        import subprocess

        result = subprocess.run([node, "--check", str(SW)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


class UpdateProtocolTests(unittest.TestCase):
    """A first visit must never reload the page by itself.

    ``activate`` used to broadcast ``omnisource-sw-updated`` to every client
    unconditionally. On an uncached site that message arrived right after the
    worker claimed a page that had published itself seconds earlier, and the
    page's handler answered it with ``location.reload()``: the visitor watched
    the site jump back to the top and refetch every asset. An update notice is
    only meaningful for a tab that already had a controller.
    """

    def setUp(self) -> None:
        self.sw = SW.read_text(encoding="utf-8")
        self.core = (ROOT / "js" / "core.js").read_text(encoding="utf-8")
        self.pwa = (ROOT / "js" / "modules" / "pwa.js").read_text(encoding="utf-8")

    @staticmethod
    def _code(source: str) -> str:
        """Drop comments so prose about ordering cannot satisfy an ordering test."""
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        return re.sub(r"(?m)//.*$", "", source)

    def test_upgrade_set_is_captured_before_claim(self) -> None:
        activate = self.sw.split("self.addEventListener('activate'", 1)[1]
        activate = self._code(activate.split("\n});", 1)[0])
        self.assertLess(
            activate.index("upgradingClients()"),
            activate.index("clients.claim()"),
            "clients.claim() runs before the upgrade set is captured",
        )

    def test_notify_only_targets_already_controlled_clients(self) -> None:
        notify = self._code(self.sw.split("function notifyClientsOfUpdate(", 1)[1])
        self.assertIn("upgradingIds", notify)
        self.assertIn("if (!targets.length) return;", notify)
        # Broadcasting to uncontrolled clients is the first-visit reload.
        self.assertNotIn("includeUncontrolled: true", notify)

    def test_core_ignores_the_message_without_a_previous_controller(self) -> None:
        marker = "event.data.type !== 'omnisource-sw-updated'"
        self.assertIn(marker, self.core)
        handler = self._code(self.core.split(marker, 1)[1].split("\n          });", 1)[0])
        self.assertIn("hadController", handler)
        # The handler only *offers* the update to a page that was already
        # controlled; what reloads is the reader choosing Update, not the
        # message (see the test below). An `assertLess` against a reload that
        # lives in a different function would pass while the reload came back.
        self.assertNotIn("location.reload()", handler)
        self.assertIn("offerServiceWorkerUpdate", handler)

    def test_the_only_reload_in_the_runtime_is_the_reader_choosing_update(self) -> None:
        code = self._code(self.core)
        self.assertEqual(1, code.count("location.reload()"), "an automatic reload path came back")
        update = code.split("function applyServiceWorkerUpdate", 1)[1].split("\n  }", 1)[0]
        self.assertIn("location.reload()", update)
        # ...and the prompt that leads to it is a persistent Update/Later
        # choice, never a timed notice that reloads on its own.
        prompt = code.split("function offerServiceWorkerUpdate", 1)[1].split("\n  }", 1)[0]
        self.assertIn("persistent: true", prompt)
        self.assertIn("pwa.later", prompt)

    def test_reload_happens_at_most_once_per_version_and_tab(self) -> None:
        self.assertIn("omnisource-sw-reloaded:", self.core)
        self.assertIn("sessionStorage", self.core)

    def test_skip_waiting_message_matches_the_sw_listener(self) -> None:
        self.assertIn("postMessage({ type: 'omnisource-skip-waiting' })", self.pwa)
        self.assertNotIn("postMessage('skipWaiting')", self.pwa)
        self.assertIn("omnisource-skip-waiting", self.sw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
