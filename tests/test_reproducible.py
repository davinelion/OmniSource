"""Tests for the generated-artifact reproducibility checker."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
SPEC = importlib.util.spec_from_file_location("check_reproducible", Path(_SCRIPTS) / "check_reproducible.py")
assert SPEC and SPEC.loader, "cannot load scripts/check_reproducible.py"
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["check_reproducible"] = MODULE
SPEC.loader.exec_module(MODULE)


class TestReproducible(unittest.TestCase):
    def test_normalizes_dates(self) -> None:
        payload = (
            b'{"generatedAt": "2026-09-09", "lastSync": "2026-09-09", "history": [{"date": "2026-09-09", "apps": 3}]}'
        )
        normalized = MODULE._norm(payload)
        self.assertNotIn(b"2026-09-09", normalized)
        self.assertIn(b'"generatedAt": "<DATE>"', normalized)

    def test_normalizes_screenshot_mirror_state(self) -> None:
        def doc(mirrored: bool, size: int, sha: str) -> bytes:
            import json

            return json.dumps(
                {
                    "schemaVersion": 1,
                    "generatedAt": "2026-09-09",
                    "screenshots": [
                        {
                            "slug": "demo",
                            "index": 0,
                            "originalURL": "https://example.com/shot.png",
                            "mirroredURL": "https://example.invalid/assets/screenshots/demo/demo-01.png",
                            "mirrored": mirrored,
                            "size": size,
                            "sha256": sha,
                            "thumbnailSize": size,
                        }
                    ],
                }
            ).encode("utf-8")

        online = MODULE._norm_screenshots(doc(True, 1234, "abc"))
        offline = MODULE._norm_screenshots(doc(False, 0, ""))
        # Network-dependent fields are environment state, not content.
        self.assertEqual(online, offline)

    def test_norm_screenshots_still_checks_urls(self) -> None:
        def doc(url: str) -> bytes:
            import json

            return json.dumps({"screenshots": [{"slug": "demo", "originalURL": url, "mirrored": False}]}).encode(
                "utf-8"
            )

        self.assertNotEqual(
            MODULE._norm_screenshots(doc("https://a.example/1.png")),
            MODULE._norm_screenshots(doc("https://a.example/2.png")),
        )

    def test_matches_patterns(self) -> None:
        self.assertTrue(MODULE._matches("feeds/discovery.json", "feeds/*.json"))
        self.assertTrue(MODULE._matches("apps/alpha/index.html", "apps/*/index.html"))
        self.assertFalse(MODULE._matches("apps/alpha/assets/x.css", "apps/*/index.html"))
        self.assertTrue(MODULE._matches("README.md", "README.md"))
        self.assertFalse(MODULE._matches("docs/API.md", "README.md"))

    def test_screenshot_normalization_covers_the_published_gz_twin(self) -> None:
        """``api/screenshots.json.gz`` is the same document as the feed."""
        self.assertTrue(MODULE._is_screenshots_doc("screenshots.json"))
        self.assertTrue(MODULE._is_screenshots_doc("screenshots.json.gz"))
        self.assertFalse(MODULE._is_screenshots_doc("discovery.json"))

    def test_the_committed_build_date_is_the_dominant_stamp(self) -> None:
        """The rebuild must run as of the committed date, never the wall clock."""
        date = MODULE._committed_build_date()
        self.assertIsNotNone(date, "the shipped feeds carry generatedAt dates")
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$")
        # The most common stamp wins — one feed refreshed hours later (an
        # update that straddled UTC midnight) must not drag the pin onto the
        # wrong day, which would shift every recency-derived score.
        import json

        feeds = Path(MODULE.ROOT) / "feeds"
        dates = []
        for path in feeds.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get("generatedAt", "")
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
            if len(value) == 10 and value[4] == "-":
                dates.append(value)
        if dates:
            counts = {}
            for value in dates:
                counts[value] = counts.get(value, 0) + 1
            self.assertEqual(date, max(counts, key=lambda value: (counts[value], value)))

    def test_the_date_pin_stays_out_of_the_checker_environment(self) -> None:
        # The pin is passed to the rebuild subprocess only, never set
        # process-wide: snapshot hashing itself must stay date-independent.
        import os

        self.assertNotIn("OMNISOURCE_TODAY", os.environ)


if __name__ == "__main__":
    unittest.main()
