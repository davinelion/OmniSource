"""Invariants for the modern (Next.js) site's shell behaviour.

The React surface is built and type-checked by ``.github/workflows/website.yml``,
but two classes of regression have already shipped here and are invisible to
both type-checking and linting:

* a *refresh* that looks like a reload — the language switch used to write the
  cookie and call ``router.refresh()``, and the service-worker prompt answered a
  new deploy by reloading the page under the reader;
* a *missing translation key*, which renders a raw key instead of a word.

These tests read the sources and assert the behaviour that matters. They are
deliberately structural (like ``tests/test_website_shell.py`` for the static
site) so they run in the same suite CI already gates on, with no browser and no
JavaScript toolchain.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SRC = WEB / "src"
APP = SRC / "app"
COMPONENTS = SRC / "components"
SW = WEB / "public" / "sw.js"

DICT_USAGE = re.compile(r"dict\.([a-z]+)\.([A-Za-z0-9_]+)")
LOCALE_KEY = re.compile(r'^\s{4}([A-Za-z0-9_]+):\s*"', re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Drop comments so prose *about* a reload cannot satisfy an assertion."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"(?m)^\s*//.*$", "", source)
    return re.sub(r"(?m)^\s*\{/\*.*?\*/\}\s*$", "", source, flags=re.S)


class DictionaryCoverageTests(unittest.TestCase):
    """Every ``dict.<section>.<key>`` the UI asks for must exist in English."""

    @classmethod
    def setUpClass(cls) -> None:
        english = _read(SRC / "i18n" / "locales" / "en.ts")
        cls.keys: dict[str, set[str]] = {}
        section: str | None = None
        for line in english.splitlines():
            start = re.match(r"^\s{2}([A-Za-z0-9_]+):\s*\{\s*$", line)
            if start:
                section = start.group(1)
                cls.keys.setdefault(section, set())
                continue
            if section and re.match(r"^\s{2}\},?\s*$", line):
                section = None
                continue
            if section:
                match = LOCALE_KEY.match(line)
                if match:
                    cls.keys[section].add(match.group(1))

    def test_every_lookup_has_a_translation(self) -> None:
        missing: list[str] = []
        for path in sorted(SRC.rglob("*")):
            if path.suffix not in {".ts", ".tsx"} or "i18n/locales" in path.as_posix():
                continue
            for section, key in DICT_USAGE.findall(_read(path)):
                if key not in self.keys.get(section, set()):
                    missing.append(f"{path.relative_to(WEB)}: dict.{section}.{key}")
        self.assertEqual([], sorted(set(missing)), "the UI asks for keys that are not translated")


class ReloadBehaviourTests(unittest.TestCase):
    """Nothing in the web app reloads the document except a chosen update."""

    def test_language_switch_never_refreshes_the_route(self) -> None:
        for path in sorted(SRC.rglob("*.ts*")):
            self.assertNotIn(
                "router.refresh",
                _strip_comments(_read(path)),
                f"{path.relative_to(WEB)} refreshes the route instead of re-rendering it",
            )

    def test_language_switch_goes_through_the_server_action(self) -> None:
        switcher = _read(COMPONENTS / "LanguageSwitcher.tsx")
        self.assertIn("setLocale", switcher)
        self.assertIn("useTransition", switcher, "no pending state: a double switch can queue two renders")
        actions = _read(APP / "actions.ts")
        self.assertIn("revalidatePath", actions)
        self.assertIn("cookies()", actions)
        # The pathname/query are never touched: a language change is not a nav.
        self.assertNotIn("redirect(", actions)

    def test_the_only_reload_is_the_readers_update_choice(self) -> None:
        reloads: list[str] = []
        for path in sorted(SRC.rglob("*.ts*")):
            body = _strip_comments(_read(path))
            if "location.reload()" in body:
                reloads.append(path.relative_to(WEB).as_posix())
        self.assertEqual(["src/components/PwaRegister.tsx"], reloads)

        register = _strip_comments(_read(COMPONENTS / "PwaRegister.tsx"))
        self.assertEqual(1, register.count("location.reload()"))
        update = register.split("const applyUpdate", 1)[1].split("const offer", 1)[0]
        self.assertIn("location.reload()", update)
        # ...and it is reached from a toast action, not from a timer or an event.
        offer = register.split("const offer", 1)[1].split("useEffect", 1)[0]
        self.assertIn("onSelect", offer)
        self.assertIn("persistent: true", offer)

    def test_an_update_is_never_offered_on_a_first_install(self) -> None:
        register = _strip_comments(_read(COMPONENTS / "PwaRegister.tsx"))
        message = register.split('addEventListener("message"', 1)[1].split("}, [lang, offer])", 1)[0]
        self.assertIn("navigator.serviceWorker.controller", message)
        self.assertIn("hadController", register)
        for flag in ("prompted", "later", "reloaded"):
            self.assertIn(f"omnisource-web-sw-{flag}:", register, f"the {flag} flag is not persisted")

    def test_registration_is_production_only_and_ids_are_checked(self) -> None:
        register = _read(COMPONENTS / "PwaRegister.tsx")
        self.assertIn('process.env.NODE_ENV !== "production"', register)
        self.assertIn('register("/sw.js"', register)
        # No unguarded navigations out of the app.
        for path in sorted(SRC.rglob("*.ts*")):
            self.assertNotIn("location.href =", _strip_comments(_read(path)), path.as_posix())


class ServiceWorkerTests(unittest.TestCase):
    def test_the_worker_never_claims_the_page_before_announcing(self) -> None:
        worker = _strip_comments(_read(SW))
        activate = worker.split('addEventListener("activate"', 1)[1].split("});", 1)[0]
        self.assertLess(
            activate.index("upgradingClients()"),
            activate.index("clients.claim()"),
            "a first visit would be told it was updated",
        )
        message = worker.split('addEventListener("message"', 1)[1]
        self.assertIn("omnisource-skip-waiting", message)
        # skipWaiting() must not run during install, or a deploy yanks the page.
        install = worker.split('addEventListener("install"', 1)[1].split('addEventListener("activate"', 1)[0]
        self.assertNotIn("skipWaiting()", install)

    def test_caches_are_versioned_and_pruned(self) -> None:
        worker = _strip_comments(_read(SW))
        self.assertRegex(worker, r'const VERSION = "omnisource-web-v\d+"')
        self.assertIn("caches.delete", worker)
        self.assertIn("_rsc", worker, "Next RSC payloads must stay out of the shell cache")


class ShellMarkupTests(unittest.TestCase):
    def test_the_drawer_is_a_labelled_dialog(self) -> None:
        nav = _read(COMPONENTS / "Nav.tsx")
        for needle in ('role="dialog"', "aria-modal", "Escape", "nav-lock", "pagehide"):
            self.assertIn(needle, nav, f"Nav.tsx lost its {needle} handling")
        self.assertIn("focus", nav.lower())

    def test_the_toast_is_a_polite_status_region(self) -> None:
        toast = _read(COMPONENTS / "Toast.tsx")
        self.assertIn('role="status"', toast)
        self.assertIn('aria-live="polite"', toast)
        self.assertIn("fixed", toast, "a toast that takes part in layout can shift the page")

    def test_the_icon_falls_back_instead_of_breaking(self) -> None:
        icon = _read(COMPONENTS / "AppIcon.tsx")
        self.assertIn("safeExternalUrl", icon)
        self.assertIn("onError", icon, "a 404 icon would render the browser's broken-image glyph")
        self.assertIn("loading=", icon)

    def test_external_urls_are_validated_before_becoming_links(self) -> None:
        guard = _read(SRC / "lib" / "url.ts")
        self.assertIn("http:", guard)
        self.assertIn("https:", guard)
        detail = _read(APP / "apps" / "[id]" / "page.tsx")
        self.assertIn("safeExternalUrl", detail, "a feed-supplied download URL becomes an href unchecked")
        source = _read(APP / "sources" / "[id]" / "page.tsx")
        self.assertIn("safeExternalUrl", source, "a feed-supplied homepage URL becomes an href unchecked")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
