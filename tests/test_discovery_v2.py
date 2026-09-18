"""Tests for the discovery v2 modules (Phases 1-9, 13)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource.community import build_community_doc
from omnisource.compare import build_compare_doc
from omnisource.discovery import build_discovery_doc, build_sources_doc
from omnisource.domain import Catalog
from omnisource.download_intel import build_download_intel_doc
from omnisource.install import build_install_doc
from omnisource.monitor import build_status_doc
from omnisource.related import build_related_doc
from omnisource.reputation import build_reputation_doc
from omnisource.search_index import build_search_index
from omnisource.trending import build_trending_doc

CATALOG = {
    "source": {
        "name": "OmniSource",
        "identifier": "com.omnisource",
        "baseURL": "https://example.test/OmniSource",
        "icon": "OmniSource.png",
    },
    "clients": [
        {"id": "altstore", "name": "AltStore", "icon": "AltStore.png", "url": "https://altstore.io"},
        {"id": "sidestore", "name": "SideStore", "icon": "SideStore.png", "url": "https://sidestore.io"},
        {"id": "feather", "name": "Feather", "icon": "Feather.png", "url": "https://github.com/claration/Feather"},
        {"id": "esign", "name": "ESign", "icon": "E-Sign.png", "url": "https://example.com/esign"},
        {
            "id": "livecontainer",
            "name": "LiveContainer",
            "icon": "LiveContainer.png",
            "url": "https://github.com/LiveContainer/LiveContainer",
        },
    ],
    "apps": [
        {
            "slug": "spotiflac",
            "name": "SpotiFLAC",
            "bundleIdentifier": "com.zarz.spotiflacAndroid",
            "developerName": "zarzet",
            "icon": "SpotiFLAC.png",
            "status": "stable",
            "compatibility": {"minOSVersion": "16.0", "clients": ["altstore", "sidestore"]},
            "verification": {"method": "github-release", "publisher": "spotiflacapp/SpotiFLAC-Mobile"},
            "tags": ["music", "flac"],
            "featured": True,
            "upstream": {
                "provider": "github",
                "repo": "spotiflacapp/SpotiFLAC-Mobile",
                "assetSuffixes": [".apk", ".ipa"],
            },
        },
        {
            "slug": "uyouenhanced",
            "name": "uYouEnhanced",
            "bundleIdentifier": "com.google.ios.youtube",
            "developerName": "arichornlover",
            "icon": "uYouEnhanced.png",
            "status": "stable",
            "compatibility": {"minOSVersion": "15.0", "clients": ["altstore", "sidestore"]},
            "verification": {"method": "manual-mirror", "publisher": "arichornlover/uYouEnhanced"},
            "tags": ["photo-video", "youtube"],
            "featured": True,
            "upstream": {"provider": "github", "repo": "arichornlover/uYouEnhanced", "assetSuffixes": [".ipa"]},
        },
        {
            "slug": "ytlite",
            "name": "YouTubePlus",
            "bundleIdentifier": "com.google.ios.youtube",
            "developerName": "dayanch96",
            "icon": "YouTube.png",
            "status": "stable",
            "compatibility": {"minOSVersion": "15.0", "clients": ["altstore", "sidestore"]},
            "verification": {"method": "github-release", "publisher": "dayanch96/YTLite"},
            "tags": ["photo-video", "youtube"],
            "featured": True,
            "upstream": {"provider": "github", "repo": "dayanch96/YTLite", "assetSuffixes": [".ipa"]},
        },
    ],
}

STATE = {
    "spotiflac": {
        "versions": [
            {
                "version": "4.9.6",
                "date": "2026-09-07",
                "size": 1024,
                "downloadURL": "https://example.com/spotiflac.ipa",
            },
            {"version": "4.9.5", "date": "2026-08-15", "size": 1024, "downloadURL": "https://example.com/old.ipa"},
        ],
        "health": {"reachable": True, "latencyMs": 120},
    },
    "uyouenhanced": {
        "versions": [
            {"version": "21.14.4", "date": "2026-08-22", "size": 4096, "downloadURL": "https://example.com/uyou.ipa"},
        ],
        "health": {"reachable": True, "latencyMs": 200},
    },
    "ytlite": {
        "versions": [
            {"version": "21.24.3", "date": "2026-09-01", "size": 4096, "downloadURL": "https://example.com/ytlite.ipa"},
        ],
        "health": {"reachable": True, "latencyMs": 150},
    },
}

HEALTH_DOC = {
    "apps": [
        {"slug": "spotiflac", "downloadReachable": True, "status": "healthy", "updatedAt": "2026-09-07"},
        {"slug": "uyouenhanced", "downloadReachable": True, "status": "healthy", "updatedAt": "2026-08-22"},
        {"slug": "ytlite", "downloadReachable": True, "status": "healthy", "updatedAt": "2026-09-01"},
    ]
}

VERIFICATION_DOC = {
    "apps": [
        {"app": "spotiflac", "status": "VERIFIED", "checks": {}, "reasons": []},
        {"app": "uyouenhanced", "status": "COMMUNITY", "checks": {}, "reasons": []},
        {"app": "ytlite", "status": "VERIFIED", "checks": {}, "reasons": []},
    ]
}


def _catalog() -> Catalog:
    return Catalog.from_dict(CATALOG)


def _sideload_catalog() -> Catalog:
    """Fixture mirroring the catalog's sideload-source pattern: an app built
    by a fork repo and an app resolved from a developer AltStore feed."""
    base_app = {
        "bundleIdentifier": "com.example.demo",
        "developerName": "Dev",
        "icon": "Demo.png",
        "status": "stable",
        "compatibility": {"minOSVersion": "16.0", "clients": ["altstore", "sidestore"]},
    }
    return Catalog.from_dict(
        {
            "source": {
                "name": "OmniSource",
                "identifier": "com.omnisource",
                "baseURL": "https://example.test/OmniSource",
                "icon": "OmniSource.png",
            },
            "clients": [{"id": "altstore", "name": "AltStore", "icon": "AltStore.png"}],
            "apps": [
                {
                    **base_app,
                    "slug": "ytlite",
                    "name": "YouTubePlus",
                    "upstreamURL": "https://github.com/Dayanch96/YTLite",
                    "verification": {"method": "github-release", "publisher": "mrdrvt99/YouProEXTRA"},
                    "upstream": {"provider": "github", "repo": "mrdrvt99/YouProEXTRA"},
                },
                {
                    **base_app,
                    "slug": "ytkp",
                    "name": "YTKillerPlus",
                    "upstreamURL": "https://github.com/iKarwan/YTKillerPlus",
                    "verification": {"method": "altstore", "publisher": "iKarwan (repo.ikghd.me)"},
                    "upstream": {
                        "provider": "altstore",
                        "feedURL": "https://repo.ikghd.me/repo.json",
                        "appId": "com.google.ios.youtube",
                    },
                },
            ],
        }
    )


_SIDELOAD_STATE = {
    "ytlite": {
        "versions": [
            {"version": "21.24.3", "date": "2026-09-01", "size": 1024, "downloadURL": "https://example.com/ytlite.ipa"}
        ]
    },
    "ytkp": {
        "versions": [
            {"version": "21.36.6", "date": "2026-09-09", "size": 1024, "downloadURL": "https://example.com/ytkp.ipa"}
        ]
    },
}

_SIDELOAD_HEALTH = {
    "apps": [
        {"slug": "ytlite", "downloadReachable": True, "status": "healthy"},
        {"slug": "ytkp", "downloadReachable": True, "status": "healthy"},
    ]
}

_SIDELOAD_VERIFICATION = {
    "apps": [
        {"app": "ytlite", "status": "VERIFIED", "checks": {}, "reasons": []},
        {"app": "ytkp", "status": "VERIFIED", "checks": {}, "reasons": []},
    ]
}


class SourceLinkTests(unittest.TestCase):
    """Every generated document exposes the link of the sideload source."""

    def test_discovery_carries_source_url(self) -> None:
        catalog = _sideload_catalog()
        doc = build_discovery_doc(catalog, _SIDELOAD_STATE, _SIDELOAD_HEALTH)
        by_slug = {entry["slug"]: entry for entry in doc["apps"]}
        # Fork-built app: sourceURL points at the builder repo, homepage at
        # the official project page.
        self.assertEqual(by_slug["ytlite"]["sourceURL"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_slug["ytlite"]["homepage"], "https://github.com/Dayanch96/YTLite")
        # AltStore feed app: sourceURL points at the feed's site.
        self.assertEqual(by_slug["ytkp"]["sourceURL"], "https://repo.ikghd.me")
        self.assertEqual(by_slug["ytkp"]["homepage"], "https://github.com/iKarwan/YTKillerPlus")

    def test_sources_index_group_links_to_the_source(self) -> None:
        catalog = _sideload_catalog()
        doc = build_sources_doc(catalog, _SIDELOAD_STATE)
        by_id = {entry["id"]: entry for entry in doc["sources"]}
        self.assertEqual(by_id["mrdrvt99/YouProEXTRA"]["homepage"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_id["mrdrvt99/YouProEXTRA"]["sourceURL"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_id["https://repo.ikghd.me/repo.json"]["homepage"], "https://repo.ikghd.me")
        self.assertEqual(by_id["https://repo.ikghd.me/repo.json"]["sourceURL"], "https://repo.ikghd.me")

    def test_reputation_group_links_to_the_source(self) -> None:
        catalog = _sideload_catalog()
        doc = build_reputation_doc(catalog, _SIDELOAD_STATE, _SIDELOAD_HEALTH)
        by_id = {entry["id"]: entry for entry in doc["sources"]}
        self.assertEqual(by_id["mrdrvt99/YouProEXTRA"]["homepage"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_id["mrdrvt99/YouProEXTRA"]["sourceURL"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_id["https://repo.ikghd.me/repo.json"]["homepage"], "https://repo.ikghd.me")
        self.assertEqual(by_id["https://repo.ikghd.me/repo.json"]["sourceURL"], "https://repo.ikghd.me")

    def test_compare_summary_carries_source_url(self) -> None:
        catalog = _sideload_catalog()
        doc = build_compare_doc(catalog, _SIDELOAD_STATE, _SIDELOAD_HEALTH, _SIDELOAD_VERIFICATION)
        pair = doc["pairs"][0]
        sides = {side["slug"]: side for side in (pair["left"], pair["right"])}
        self.assertEqual(sides["ytlite"]["sourceURL"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(sides["ytkp"]["sourceURL"], "https://repo.ikghd.me")

    def test_status_rows_carry_source_url(self) -> None:
        catalog = _sideload_catalog()
        doc = build_status_doc(catalog, _SIDELOAD_STATE, _SIDELOAD_HEALTH)
        by_app = {entry["app"]: entry for entry in doc["sources"]}
        self.assertEqual(by_app["ytlite"]["sourceURL"], "https://github.com/mrdrvt99/YouProEXTRA")
        self.assertEqual(by_app["ytkp"]["sourceURL"], "https://repo.ikghd.me")


class TrendingTests(unittest.TestCase):
    def test_trending_doc_has_three_lists(self) -> None:
        catalog = _catalog()
        doc = build_trending_doc(catalog, STATE, HEALTH_DOC, VERIFICATION_DOC)
        self.assertIn("trending", doc)
        self.assertIn("rising", doc)
        self.assertIn("recentlyUpdated", doc)
        # All apps in our fixture end up in the trending list.
        self.assertEqual(doc["count"], 3)
        # Scores are 0..1.
        for entry in doc["all"]:
            self.assertGreaterEqual(entry["score"], 0.0)
            self.assertLessEqual(entry["score"], 1.0)
        # The signal breakdown is preserved.
        for entry in doc["all"]:
            self.assertIn("signals", entry)
            self.assertEqual(set(entry["signals"]), {"recency", "availability", "featured", "verification"})


class RelatedTests(unittest.TestCase):
    def test_related_links_same_bundle(self) -> None:
        catalog = _catalog()
        doc = build_related_doc(catalog, STATE)
        # uyouenhanced and ytlite share bundle id, so they must be related.
        uyou = doc["related"]["uyouenhanced"]
        ytlite = doc["related"]["ytlite"]
        self.assertTrue(any(r["slug"] == "ytlite" for r in uyou))
        self.assertTrue(any(r["slug"] == "uyouenhanced" for r in ytlite))
        # spotiflac shares no bundle, but shares the music category.
        self.assertTrue(any(r["slug"] == "ytlite" or r["slug"] == "uyouenhanced" for r in doc["related"]["spotiflac"]))
        # Media ecosystem contains all apps that target youtube or youtube music bundles.
        media_slugs = {m["slug"] for m in doc["media"]["apps"]}
        self.assertIn("uyouenhanced", media_slugs)
        self.assertIn("ytlite", media_slugs)


class ReputationTests(unittest.TestCase):
    def test_reputation_doc_has_sources(self) -> None:
        catalog = _catalog()
        doc = build_reputation_doc(catalog, STATE, HEALTH_DOC)
        self.assertIn("sources", doc)
        self.assertGreater(len(doc["sources"]), 0)
        for source in doc["sources"]:
            self.assertIn(source["level"], {"TRUSTED", "RELIABLE", "AVERAGE", "EXPERIMENTAL"})
            self.assertGreaterEqual(source["score"], 0.0)
            self.assertLessEqual(source["score"], 100.0)


class DownloadIntelTests(unittest.TestCase):
    def test_summary_present(self) -> None:
        catalog = _catalog()
        doc = build_download_intel_doc(catalog, STATE, HEALTH_DOC)
        self.assertIn("summary", doc)
        self.assertIn("apps", doc)
        self.assertEqual(len(doc["apps"]), 3)
        # Every app carries availability, mirrorCount, consistency.
        for app in doc["apps"]:
            self.assertIn("availability", app)
            self.assertIn("mirrorCount", app)
            self.assertIn("releaseConsistency", app)


class CommunityTests(unittest.TestCase):
    def test_community_lists_present(self) -> None:
        catalog = _catalog()
        doc = build_community_doc(catalog, STATE)
        for key in ("popular", "recentlyAdded", "rising", "requested"):
            self.assertIn(key, doc)
            self.assertIsInstance(doc[key], list)


class SearchIndexTests(unittest.TestCase):
    def test_documents_have_id_and_bundle(self) -> None:
        catalog = _catalog()
        doc = build_search_index(catalog, STATE, HEALTH_DOC, VERIFICATION_DOC)
        self.assertEqual(doc["count"], 3)
        for entry in doc["documents"]:
            self.assertIn("id", entry)
            self.assertIn("bundleId", entry)
        # Fuse config is present.
        self.assertIn("keys", doc["fuse"])


class InstallTests(unittest.TestCase):
    def test_install_cards_for_each_client(self) -> None:
        catalog = _catalog()
        doc = build_install_doc(catalog)
        self.assertGreater(len(doc["apps"]), 0)
        for app in doc["apps"]:
            cards = app["cards"]
            self.assertEqual(len(cards), len(catalog.clients))
            for card in cards:
                self.assertIn("client", card)
                self.assertIn("name", card)
                self.assertIn("compatible", card)
                self.assertIn("recommended", card)
                self.assertIn("bestFor", card)
                # Recommended clients must have a URL.
                if card["recommended"]:
                    self.assertTrue(card["url"])

    def test_exactly_one_client_is_the_default_pick(self) -> None:
        # The install center badges the default pick and every other client
        # explains who it *is* for; two defaults would make the badge noise.
        doc = build_install_doc(_catalog())
        picked = [c["id"] for c in doc["clients"] if c.get("recommended")]
        self.assertEqual(len(picked), 1, picked)
        self.assertEqual(picked[0], doc["recommender"]["default"])
        # The catalog's own client list is the source of truth for ids.
        self.assertEqual(
            [c["id"] for c in doc["clients"]],
            [c["id"] for c in _catalog().clients],
        )

    def test_recommender_questions_terminate_at_real_clients(self) -> None:
        doc = build_install_doc(_catalog())
        ids = {c["id"] for c in doc["clients"]}
        recommender = doc["recommender"]
        self.assertIn(recommender["default"], ids)
        questions = {q["id"]: q for q in recommender["questions"]}

        def walk(question_id: str, seen: tuple[str, ...] = ()) -> None:
            self.assertNotIn(question_id, seen, "recommender question cycle")
            for option in questions[question_id]["options"]:
                if "result" in option:
                    self.assertIn(option["result"], ids, option)
                else:
                    self.assertIn(option["next"], questions, option)
                    walk(option["next"], (*seen, question_id))

        walk("device")
        # Every client with a profile in src/omnisource/install.py explains
        # itself: who it is for and where to get it. (A catalog can declare a
        # client OmniSource has no profile for; that one is labelled "manual
        # setup" instead and is not part of the advice.)
        from omnisource.install import CLIENT_GUIDE

        for client in doc["clients"]:
            if client["id"] in CLIENT_GUIDE:
                self.assertTrue(client.get("bestFor"), client["id"])
                self.assertTrue(client.get("url"), client["id"])

    def test_recommender_prunes_clients_the_catalog_does_not_carry(self) -> None:
        # The fixture catalog has no FlareStore; the full tree does. Advice must
        # never point at a client the document cannot link.
        catalog = _catalog()
        doc = build_install_doc(catalog)
        ids = {c["id"] for c in doc["clients"]}
        self.assertNotIn("flarestore", ids)
        results = [
            option["result"]
            for question in doc["recommender"]["questions"]
            for option in question["options"]
            if option.get("result")
        ]
        self.assertTrue(results, "every branch was pruned")
        for result in results:
            self.assertIn(result, ids)
        self.assertIn(doc["recommender"]["default"], ids)
        self.assertIn(doc["recommender"]["default"], results)

    def test_recommender_is_omitted_when_nothing_survives(self) -> None:
        # A catalog whose only client has no profile: there is no advice to
        # give, and the install center renders the client grid without it.
        catalog = Catalog.from_dict({**CATALOG, "clients": [{"id": "some-other-signer", "name": "Other", "icon": ""}]})
        doc = build_install_doc(catalog)
        self.assertNotIn("recommender", doc)
        self.assertEqual(len(doc["clients"]), 1)


class CompareTests(unittest.TestCase):
    def test_pairs_include_same_bundle_pair(self) -> None:
        catalog = _catalog()
        doc = build_compare_doc(catalog, STATE, HEALTH_DOC, VERIFICATION_DOC)
        # 3 apps → 3 pairs.
        self.assertEqual(doc["count"], 3)
        # At least one pair should share a bundle.
        self.assertTrue(any(p["shareBundle"] for p in doc["pairs"]))
        for pair in doc["pairs"]:
            self.assertIn("left", pair)
            self.assertIn("right", pair)
            self.assertIn("winner", pair)


if __name__ == "__main__":
    unittest.main()
