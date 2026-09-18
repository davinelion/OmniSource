"""Screenshot provenance: originals from the app's own upstream, or nothing at all.

A screenshot is the catalog field that can be invented without anyone noticing - a
rendered mockup looks exactly like the app it claims to show - so these tests hold
the two rules that keep the gallery honest:

* the declared URL belongs to the project, not to us or to someone else's copy;
* ``feeds/screenshots.json`` only claims what is actually in the tree, and never
  passes an app icon off as a preview.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource.constants import Paths
from omnisource.validation import (
    Report,
    _validate_screenshot_manifest,
    validate_catalog,
    validate_generated_docs,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_SOURCE = {
    "baseURL": "https://iamsmmh.github.io/OmniSource",
    "repository": "https://github.com/iamsmmh/OmniSource",
}
UPSTREAM = "https://github.com/SomeDev/SomeApp"


def _app(**overrides):
    app = {
        "slug": "someapp",
        "name": "SomeApp",
        "subtitle": "A real app",
        "bundleIdentifier": "dev.some.someapp",
        "developerName": "SomeDev",
        "category": "utilities",
        "tintColor": "111111",
        "icon": "SomeApp.png",
        "localizedDescription": {"en": "A real app"},
        "screenshots": ["https://raw.githubusercontent.com/SomeDev/SomeApp/main/docs/shot1.png"],
        "featured": False,
        "status": "stable",
        "upstreamURL": UPSTREAM,
        "verification": {"method": "github-release", "publisher": "SomeDev/SomeApp"},
        "compatibility": {"minOSVersion": "16.0"},
        "upstream": {"repo": "SomeDev/SomeApp"},
    }
    app.update(overrides)
    return app


def _catalog(*apps):
    return {"source": dict(CATALOG_SOURCE), "apps": list(apps)}


class DeclaredProvenanceTests(unittest.TestCase):
    def test_upstream_repo_screenshots_are_accepted(self) -> None:
        report = validate_catalog(_catalog(_app()), assets_dir=ROOT / "assets")
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)

    def test_a_screenshot_hosted_by_omnisource_itself_is_rejected(self) -> None:
        # The self-loop: declaring our own mirror as the source records no origin,
        # which is exactly how fabricated mockups survived review before.
        app = _app(
            screenshots=["https://raw.githubusercontent.com/iamsmmh/OmniSource/main/assets/screenshots/x/x-1.png"]
        )
        report = validate_catalog(_catalog(app), assets_dir=ROOT / "assets")
        self.assertTrue(any("points back at OmniSource" in e for e in report.errors), report.errors)

    def test_a_copy_in_someone_elses_repository_is_rejected(self) -> None:
        app = _app(screenshots=["https://github.com/SomeoneElse/AltSource/raw/master/assets/screenshots/x.png"])
        report = validate_catalog(_catalog(app), assets_dir=ROOT / "assets")
        self.assertTrue(any("someone else's repository" in e for e in report.errors), report.errors)

    def test_a_non_forge_host_only_warns(self) -> None:
        # The developer's own site is a legitimate place to publish art, so the
        # build asks a human to confirm it - it does not fail the pipeline.
        app = _app(screenshots=["https://some.app/shot1.png"])
        report = validate_catalog(_catalog(app), assets_dir=ROOT / "assets")
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)
        self.assertTrue(any("developer's own hosting" in w for w in report.warnings), report.warnings)

    def test_no_screenshots_is_a_valid_state(self) -> None:
        report = validate_catalog(_catalog(_app(screenshots=[])), assets_dir=ROOT / "assets")
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)

    def test_malformed_entries_are_rejected(self) -> None:
        for bad in ([{"url": "ftp://x/y.png"}], ["not a url"], "https://x/y.png"):
            with self.subTest(bad=str(bad)[:24]):
                report = validate_catalog(_catalog(_app(screenshots=bad)), assets_dir=ROOT / "assets")
                self.assertTrue(any("screenshots" in e for e in report.errors), report.errors)

    def test_archive_org_source_urls_do_not_become_fake_owners(self) -> None:
        # A mirror on archive.org is a *host*, not a repository owner; treating the
        # path segment as one would invent an upstream the project never claimed.
        app = _app(
            sourceURL="https://archive.org/details/someapp",
            screenshots=["https://raw.githubusercontent.com/SomeDev/SomeApp/main/docs/shot1.png"],
        )
        report = validate_catalog(_catalog(app), assets_dir=ROOT / "assets")
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)


class ManifestTruthTests(unittest.TestCase):
    def _paths(self, root: Path) -> Paths:
        return Paths.from_root(root)

    def test_a_claimed_mirror_must_exist(self) -> None:
        doc = {
            "screenshots": [
                {
                    "slug": "someapp",
                    "mirrored": True,
                    "mirroredURL": "https://iamsmmh.github.io/OmniSource/assets/screenshots/someapp/x.png",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = Report()
            _validate_screenshot_manifest(doc, self._paths(Path(tmp)), report)
            self.assertTrue(any("not in the tree" in e for e in report.errors), report.errors)

            (Path(tmp) / "assets" / "screenshots" / "someapp").mkdir(parents=True)
            (Path(tmp) / "assets" / "screenshots" / "someapp" / "x.png").write_bytes(b"x")
            report2 = Report()
            _validate_screenshot_manifest(doc, self._paths(Path(tmp)), report2)
            self.assertEqual(report2.errors, [])

    def test_an_icon_never_shows_up_as_a_screenshot(self) -> None:
        doc = {"screenshots": [{"slug": "someapp", "iconFallback": True, "mirroredURL": "…/assets/SomeApp.png"}]}
        report = Report()
        with tempfile.TemporaryDirectory() as tmp:
            _validate_screenshot_manifest(doc, self._paths(Path(tmp)), report)
        self.assertTrue(any("app icon as a screenshot" in e for e in report.errors), report.errors)


class ShippedTreeTests(unittest.TestCase):
    def test_the_shipped_catalog_declares_only_upstream_art(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        report = validate_catalog(catalog, assets_dir=ROOT / "assets")
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)

    def test_no_app_page_claims_a_screenshot_it_does_not_have(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        report = validate_generated_docs(catalog, Paths.from_root(ROOT))
        self.assertFalse([e for e in report.errors if "screenshot" in e], report.errors)

    def test_the_fabricated_mockups_are_gone(self) -> None:
        # These ten directories held 390x844 renders (fake status bar, app icon,
        # skeleton rows) published as screenshots. They must stay deleted *as
        # fabricated art* - which is not the same as "the directory must never
        # exist": a real sync mirrors the art an app legitimately declares from
        # its own upstream into exactly those paths (refresh=True in
        # omnisource.screenshots), and the sync job runs this suite on that
        # workspace. So the durable invariants are instead:
        #   * an app whose upstream publishes no art must declare none and must
        #     have no mirror directory in the tree, and
        #   * every mirror file in the tree must be a pipeline artifact
        #     recorded in the current feeds/screenshots.json - digest-checked
        #     whenever the manifest claims the mirror, so a re-added render
        #     (or a hand-dropped file) can never ship.
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        declared = {app["slug"]: (app.get("screenshots") or []) for app in catalog["apps"]}
        for slug in ("youpro", "youmod", "itorrent", "winston", "raintweak", "uyouenhanced"):
            with self.subTest(slug=slug):
                self.assertEqual(
                    declared.get(slug, []),
                    [],
                    f"{slug} publishes no upstream art and must keep declaring none",
                )
                self.assertFalse(
                    (ROOT / "assets" / "screenshots" / slug).exists(),
                    f"{slug} declares no screenshots, so no mirror directory may exist",
                )

        manifest = {
            (entry["slug"], str(entry["mirroredURL"]).rsplit("/", 1)[-1]): entry
            for entry in json.loads((ROOT / "feeds" / "screenshots.json").read_text(encoding="utf-8")).get(
                "screenshots", []
            )
            if isinstance(entry, dict) and entry.get("mirroredURL")
        }
        screenshots_dir = ROOT / "assets" / "screenshots"
        if not screenshots_dir.is_dir():
            # Fresh checkout: mirrors are build artifacts and never committed,
            # so a clean tree legitimately ships none of them.
            return
        for path in sorted(screenshots_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(screenshots_dir)
            # Only <slug>/<file> mirror pairs are guarded; thumbnails/<slug>/
            # holds derived WebP thumbnails and _manifest/ is a marker.
            if len(rel.parts) != 2 or rel.parts[0] in ("_manifest", "thumbnails"):
                continue
            slug, filename = rel.parts
            with self.subTest(mirror=str(rel)):
                self.assertTrue(
                    declared.get(slug),
                    f"{slug} ships mirror files but declares no screenshots",
                )
                entry = manifest.get((slug, filename))
                self.assertIsNotNone(
                    entry,
                    f"{slug}/{filename} is not recorded in feeds/screenshots.json - "
                    "a screenshot file without manifest provenance is a fabricated mockup",
                )
                if entry is not None and entry.get("mirrored"):
                    self.assertEqual(
                        entry.get("sha256"),
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        f"{slug}/{filename} does not match the digest recorded in feeds/screenshots.json",
                    )

    def test_apps_with_screenshots_point_at_their_own_project(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        for app in catalog["apps"]:
            shots = app.get("screenshots") or []
            if not shots:
                continue
            with self.subTest(slug=app["slug"]):
                self.assertNotIn("iamsmmh/OmniSource", " ".join(shots))
                self.assertTrue(all(str(u).startswith("https://") for u in shots), shots)


class ScreenshotHostAllowlistTests(unittest.TestCase):
    """data/screenshot_hosts.json is the reviewed exception to the own-upstream-host rule."""

    def test_the_shipped_allowlist_covers_only_confirmed_developer_hosts(self) -> None:
        from omnisource.validation import _screenshot_host_allowlist

        allowlist = _screenshot_host_allowlist(ROOT)
        self.assertEqual(allowlist, frozenset({"repo.ikghd.me", "apps.sidestore.io", "aidoku.app"}))

    def test_the_shipped_catalog_no_longer_warns_on_confirmed_hosts(self) -> None:
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        report = validate_catalog(catalog, assets_dir=ROOT / "assets")
        self.assertEqual(
            [warning for warning in report.warnings if "screenshot host" in warning],
            [],
            "every catalog screenshot host must be the app's upstream host or reviewed in data/screenshot_hosts.json",
        )

    def test_a_foreign_host_not_on_the_allowlist_still_warns(self) -> None:
        report = validate_catalog(
            _catalog(_app(screenshots=["https://screenshots.example.net/app/shot.png"])),
            assets_dir=ROOT / "assets",
        )
        self.assertTrue(any("screenshot host" in warning for warning in report.warnings), report.warnings)

    def test_an_allowlisted_foreign_host_does_not_warn(self) -> None:
        report = validate_catalog(
            _catalog(_app(screenshots=["https://aidoku.app/screenshots/shot.png"])),
            assets_dir=ROOT / "assets",
        )
        self.assertFalse(any("screenshot host" in warning for warning in report.warnings), report.warnings)

    def test_a_missing_allowlist_file_falls_back_to_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "data").mkdir()  # deliberately no screenshot_hosts.json
            report = validate_catalog(
                _catalog(_app(screenshots=["https://aidoku.app/screenshots/shot.png"])),
                assets_dir=root / "assets",
            )
            self.assertTrue(any("screenshot host" in warning for warning in report.warnings), report.warnings)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
