"""Tests for asset detection, classification, and inspection."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tempfile
import unittest
from pathlib import Path

from omnisource.assets import DirectoryCache, NullCache, _icon_magic_ok, inspect_catalog
from omnisource.constants import PNG_MAGIC
from omnisource.domain import Catalog
from omnisource.utils.assets import (
    detect_architecture,
    detect_asset_metadata,
    detect_file_type,
    detect_platform,
)


class TestAssetUtils(unittest.TestCase):
    def test_detect_file_type(self) -> None:
        self.assertEqual(detect_file_type("app.ipa"), "IPA")
        self.assertEqual(detect_file_type("https://example.com/app.apk?raw=true"), "APK")
        self.assertEqual(detect_file_type("archive.zip"), "ZIP")
        self.assertEqual(detect_file_type("package.deb"), "DEB")
        # .tipa is a TrollStore-named .ipa and stays an installable IPA.
        self.assertEqual(detect_file_type("Bootstrap.tipa"), "IPA")
        self.assertEqual(detect_file_type("unknown.bin"), "other")

    def test_detect_platform(self) -> None:
        self.assertEqual(detect_platform("app.ipa"), "ios")
        self.assertEqual(detect_platform("Bootstrap.tipa"), "ios")
        self.assertEqual(detect_platform("app.apk"), "android")
        self.assertEqual(detect_platform("setup.exe"), "windows")
        self.assertEqual(detect_platform("tool.dmg"), "macos")
        self.assertEqual(detect_platform("installer.deb"), "linux")
        self.assertEqual(detect_platform("app-ios-unsigned.ipa"), "ios")
        self.assertEqual(detect_platform("unknown.tar.gz"), "unknown")

    def test_detect_architecture(self) -> None:
        self.assertEqual(detect_architecture("app-arm64.ipa"), "arm64")
        self.assertEqual(detect_architecture("app-aarch64.deb"), "arm64")
        self.assertEqual(detect_architecture("app-x86_64.zip"), "x86_64")
        self.assertEqual(detect_architecture("app-universal.ipa"), "universal")
        self.assertEqual(detect_architecture("app.ipa"), None)

    def test_detect_asset_metadata(self) -> None:
        meta = detect_asset_metadata("SpotiFLAC-v4.9.6-arm64.apk", "https://example.com/SpotiFLAC.apk")
        self.assertEqual(meta["fileType"], "APK")
        self.assertEqual(meta["platform"], "android")
        self.assertEqual(meta["architecture"], "arm64")
        self.assertTrue(meta["installable"])


class TestAssetManagement(unittest.TestCase):
    def test_directory_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DirectoryCache(Path(tmpdir))
            key = "test-key-1"
            data = b"hello-world-payload"

            self.assertFalse(cache.has(key))
            self.assertIsNone(cache.get(key))

            cache.put(key, data)
            self.assertTrue(cache.has(key))
            self.assertEqual(cache.get(key), data)

    def test_null_cache(self) -> None:
        cache = NullCache()
        self.assertFalse(cache.has("key"))
        self.assertIsNone(cache.get("key"))
        cache.put("key", b"data")
        self.assertFalse(cache.has("key"))

    def test_icon_magic_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            valid_png = tmp / "icon.png"
            valid_png.write_bytes(PNG_MAGIC + b"\x00\x00\x00\rIHDR...")
            self.assertIsNone(_icon_magic_ok(valid_png))

            empty_file = tmp / "empty.png"
            empty_file.write_bytes(b"")
            self.assertIsNotNone(_icon_magic_ok(empty_file))

    def test_inspect_catalog_basic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir)
            icon = assets_dir / "TestIcon.png"
            icon.write_bytes(PNG_MAGIC + b"content")

            raw_catalog = {
                "source": {
                    "name": "Test",
                    "identifier": "com.test",
                    "baseURL": "https://test.local",
                    "icon": "TestIcon.png",
                },
                "clients": [],
                "apps": [
                    {
                        "slug": "test-app",
                        "name": "Test App",
                        "bundleIdentifier": "com.test.app",
                        "developerName": "Tester",
                        "icon": "TestIcon.png",
                        "status": "stable",
                        "screenshots": ["https://example.com/screen.png"],
                        "compatibility": {"minOSVersion": "16.0", "clients": ["altstore"]},
                    }
                ],
            }
            catalog = Catalog.from_dict(raw_catalog)
            report = inspect_catalog(catalog, assets_dir=assets_dir)
            self.assertEqual(len(report.errors), 0)
            self.assertEqual(report.icons_ok, 1)
            self.assertEqual(report.screenshots_ok, 1)


class TestAssetManifest(unittest.TestCase):
    def _manifest(self, screenshots: list[str]) -> dict:
        from omnisource.assets import build_asset_manifest_doc

        with tempfile.TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir)
            (assets_dir / "TestIcon.png").write_bytes(PNG_MAGIC + b"content")
            raw_catalog = {
                "source": {"name": "Test", "identifier": "com.test", "baseURL": "https://test.local"},
                "clients": [],
                "apps": [
                    {
                        "slug": "test-app",
                        "name": "Test App",
                        "bundleIdentifier": "com.test.app",
                        "developerName": "Tester",
                        "icon": "TestIcon.png",
                        "status": "stable",
                        "screenshots": screenshots,
                        "compatibility": {"minOSVersion": "16.0", "clients": ["altstore"]},
                    }
                ],
            }
            return build_asset_manifest_doc(
                Catalog.from_dict(raw_catalog),
                assets_dir=assets_dir,
                base_url="https://test.local",
            )

    def test_missing_screenshots_are_published_not_hidden(self) -> None:
        """The per-app build warning was demoted; the manifest carries the list.

        ``stage_assets`` used to log one ``::warning::`` annotation per app
        without screenshots — over 50 of ~94 on every sync — which buried the
        annotations that matter. The signal stays actionable here.
        """
        doc = self._manifest([])
        self.assertEqual(doc["screenshotsMissing"], ["test-app"])
        self.assertEqual(doc["totals"]["screenshotsMissing"], 1)
        self.assertEqual(doc["totals"]["screenshotsDeclared"], 0)

    def test_declared_screenshots_are_counted(self) -> None:
        doc = self._manifest(["https://example.com/screen.png"])
        self.assertEqual(doc["screenshotsMissing"], [])
        self.assertEqual(doc["totals"]["screenshotsMissing"], 0)
        self.assertEqual(doc["totals"]["screenshotsDeclared"], 1)


if __name__ == "__main__":
    unittest.main()
