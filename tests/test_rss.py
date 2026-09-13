"""Tests for RSS/Atom feed generator."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource.domain import Catalog
from omnisource.feeds.rss import _newest_date, _rfc822_date, render_app_rss_feed, render_rss_feed


class TestRssFeed(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_catalog = {
            "source": {
                "name": "OmniSource",
                "identifier": "com.iamsmmh.omnisource",
                "subtitle": "Curated iOS Apps",
                "description": "AltStore feed aggregator",
                "baseURL": "https://iamsmmh.github.io/OmniSource",
                "tintColor": "5B5BD6",
                "icon": "OmniSource.png",
                "banner": "OmniSource.png",
            },
            "apps": [
                {
                    "slug": "spotiflac",
                    "name": "SpotiFLAC Mobile",
                    "bundleIdentifier": "com.zarz.spotiflacAndroid",
                    "developerName": "zarzet",
                    "icon": "SpotiFLAC.png",
                    "status": "stable",
                }
            ],
        }
        self.catalog = Catalog.from_dict(self.raw_catalog)

    def test_feed_bytes_are_a_function_of_the_inputs(self) -> None:
        # lastBuildDate used to be the renderer's own clock, so a rebuild with no
        # data change rewrote all 95 feeds and broke the reproducibility gate.
        state = {
            "spotiflac": {
                "versions": [
                    {"version": "4.9.6", "date": "2026-09-07", "downloadURL": "https://example.com/a.ipa", "size": 1}
                ]
            },
            "updateHistory": [{"appId": "spotiflac", "version": "4.9.5", "releaseDate": "2026-08-01"}],
        }
        first = render_rss_feed(self.catalog, state)
        second = render_rss_feed(self.catalog, state)
        self.assertEqual(first, second)
        # The channel date is the newest item, not today.
        self.assertIn("<lastBuildDate>Mon, 07 Sep 2026 00:00:00 +0000</lastBuildDate>", first)
        self.assertNotIn(datetime.now(UTC).strftime("%a, %d %b %Y"), first)

    def test_undated_items_and_empty_feeds_omit_dates(self) -> None:
        # 2026-09-07 is a Monday; the point of _newest_date is that a lexicographic
        # max over RFC 822 strings would rank "Fri, ..." above "Mon, ...".
        items = [
            {"date": _rfc822_date("2026-08-01")},
            {"date": _rfc822_date("2026-09-07")},
            {"date": _rfc822_date("")},
        ]
        self.assertEqual(items[2]["date"], "")
        self.assertEqual(_newest_date(items), _rfc822_date("2026-09-07"))
        self.assertEqual(_newest_date([{"date": ""}]), "")
        self.assertEqual(_rfc822_date("not a date"), "")
        self.assertEqual(_rfc822_date(""), "")
        # An item without a date renders no <pubDate>; an empty feed no lastBuild.
        state = {"spotiflac": {"versions": [{"version": "1.0", "downloadURL": "https://example.com/a.ipa"}]}}
        xml = render_rss_feed(self.catalog, state)
        self.assertNotIn("<pubDate>", xml)
        self.assertNotIn("<lastBuildDate>", xml)

    def test_render_rss_feed(self) -> None:
        state = {
            "spotiflac": {
                "versions": [
                    {
                        "version": "4.9.6",
                        "date": "2026-09-07",
                        "downloadURL": "https://example.com/SpotiFLAC.ipa",
                        "size": 34171700,
                        "localizedDescription": "SpotiFLAC 4.9.6 update with new lossless downloader.",
                    }
                ]
            },
            "updateHistory": [
                {
                    "appId": "spotiflac",
                    "version": "4.9.6",
                    "releaseDate": "2026-09-07",
                    "downloadUrl": "https://example.com/SpotiFLAC.ipa",
                    "changelog": "SpotiFLAC 4.9.6 update with new lossless downloader.",
                }
            ],
        }
        rss_xml = render_rss_feed(self.catalog, state)
        self.assertIn('<rss version="2.0"', rss_xml)
        self.assertIn("<title>OmniSource Updates</title>", rss_xml)
        self.assertIn("SpotiFLAC Mobile v4.9.6", rss_xml)
        self.assertIn("https://example.com/SpotiFLAC.ipa", rss_xml)
        self.assertIn("enclosure", rss_xml)


class TestAppRssFeed(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_catalog = {
            "source": {
                "name": "OmniSource",
                "identifier": "com.iamsmmh.omnisource",
                "subtitle": "Curated iOS Apps",
                "description": "AltStore feed aggregator",
                "baseURL": "https://iamsmmh.github.io/OmniSource",
                "tintColor": "5B5BD6",
                "icon": "OmniSource.png",
                "banner": "OmniSource.png",
            },
            "apps": [
                {
                    "slug": "spotiflac",
                    "name": "SpotiFLAC Mobile",
                    "subtitle": "Lossless streaming",
                    "bundleIdentifier": "com.zarz.spotiflacAndroid",
                    "developerName": "zarzet",
                    "icon": "SpotiFLAC.png",
                    "status": "stable",
                },
                {
                    "slug": "feather",
                    "name": "Feather",
                    "bundleIdentifier": "thewonderofyou.Feather",
                    "developerName": "Feather Team",
                    "icon": "Feather.png",
                    "status": "stable",
                },
            ],
        }
        self.catalog = Catalog.from_dict(self.raw_catalog)
        self.state = {
            "spotiflac": {
                "versions": [
                    {
                        "version": "4.9.6",
                        "date": "2026-09-07",
                        "downloadURL": "https://example.com/SpotiFLAC.ipa",
                        "size": 34171700,
                        "localizedDescription": "Lossless downloader update.",
                    }
                ]
            },
            "updateHistory": [
                {
                    "appId": "feather",
                    "version": "2.9.0",
                    "releaseDate": "2026-09-01",
                    "downloadUrl": "https://example.com/Feather.ipa",
                    "changelog": "Feather 2.9.0 release.",
                }
            ],
        }

    def test_render_app_rss_feed_only_lists_matching_app(self) -> None:
        app_xml = render_app_rss_feed(self.catalog, self.state, "spotiflac")
        self.assertIn("<title>SpotiFLAC Mobile — Releases</title>", app_xml)
        self.assertIn('href="https://iamsmmh.github.io/OmniSource/feeds/spotiflac.xml"', app_xml)
        self.assertIn("SpotiFLAC Mobile v4.9.6", app_xml)
        # History from other apps must not leak into this feed.
        self.assertNotIn("Feather", app_xml)

    def test_render_app_rss_feed_uses_history(self) -> None:
        app_xml = render_app_rss_feed(self.catalog, self.state, "feather")
        self.assertIn("Feather v2.9.0", app_xml)
        self.assertNotIn("SpotiFLAC", app_xml)

    def test_render_app_rss_feed_unknown_slug_is_empty(self) -> None:
        self.assertEqual(render_app_rss_feed(self.catalog, self.state, "nope"), "")


if __name__ == "__main__":
    unittest.main()
