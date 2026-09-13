"""Tests for the Phase 3/6/7/8/9/10 intelligence modules."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unittest

from omnisource.collections import build_collection_pages, build_collections_doc
from omnisource.compare import build_compare_pages, render_compare_page, render_compare_redirect
from omnisource.dead_apps import build_dead_apps_doc, classify_age
from omnisource.domain import App, Catalog
from omnisource.health_score import annotate_health_doc, build_health_scores, compute_health_score
from omnisource.integrity import build_integrity_doc, integrity_errors, metadata_checks
from omnisource.reputation import build_reputation_doc
from omnisource.utils.dates import average_update_gap_days, version_dates
from omnisource.verification import compute_trust_score, trust_badge


def _app(slug: str = "demo", **extra) -> App:
    raw = {
        "slug": slug,
        "name": f"App {slug.title()}",
        "bundleIdentifier": f"com.example.{slug}",
        "developerName": "Dev",
        "icon": "Icon.png",
        "category": "utilities",
        "status": "stable",
        "localizedDescription": "A description.",
        "shortDescription": "Short.",
        "compatibility": {"minOSVersion": "16.0", "clients": ["altstore"]},
        "verification": {"publisher": "Dev", "verified": True},
        **extra,
    }
    return App(slug=slug, raw=raw)


def _catalog(*apps: App) -> Catalog:
    return Catalog(source={"baseURL": "https://example.com"}, clients=[], apps=list(apps))


GOOD_NEWEST = {
    "version": "1.0.0",
    "date": "2026-09-01",
    "downloadURL": "https://example.com/app.ipa",
    "size": 1000,
    "sha256": "a" * 64,
    "source": "github",
}


class IntegrityTests(unittest.TestCase):
    def test_good_asset_passes(self) -> None:
        result = metadata_checks(_app(), dict(GOOD_NEWEST), {"downloadReachable": True})
        self.assertEqual(result["status"], "pass")
        self.assertTrue(all(result["checks"].values()))

    def test_zero_bytes_fails(self) -> None:
        newest = dict(GOOD_NEWEST, size=0)
        result = metadata_checks(_app(), newest, None)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["sizePositive"])

    def test_non_ipa_fails(self) -> None:
        newest = dict(GOOD_NEWEST, downloadURL="https://example.com/app.zip")
        result = metadata_checks(_app(), newest, None)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["installable"])

    def test_bad_digest_fails(self) -> None:
        newest = dict(GOOD_NEWEST, sha256="not-a-hex")
        result = metadata_checks(_app(), newest, None)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["checks"]["digestFormat"])

    def test_missing_sha_is_warn(self) -> None:
        newest = dict(GOOD_NEWEST)
        newest.pop("sha256")
        result = metadata_checks(_app(), newest, None)
        self.assertEqual(result["status"], "warn")

    def test_build_doc_and_errors(self) -> None:
        catalog = _catalog(_app("demo"), _app("broken", **{"bundleIdentifier": "com.example.broken"}))
        state = {
            "demo": {"versions": [dict(GOOD_NEWEST)]},
            "broken": {"versions": [dict(GOOD_NEWEST, size=0)]},
        }
        doc = build_integrity_doc(catalog, state, {})
        self.assertEqual(doc["totals"]["fail"], 1)
        self.assertEqual(doc["totals"]["rejected"], 1)
        errors = integrity_errors(doc)
        self.assertEqual(len(errors), 1)
        self.assertIn("broken", errors[0])
        # Failing entries are sorted first.
        self.assertEqual(doc["apps"][0]["slug"], "broken")
        self.assertEqual(doc["apps"][0]["asset"]["releaseId"], "1.0.0")
        self.assertEqual(doc["apps"][0]["asset"]["source"], "github")


class HealthScoreTests(unittest.TestCase):
    def test_weights_and_status(self) -> None:
        app = _app()
        state = {
            "demo": {
                "versions": [
                    {"version": "1.0.0", "date": "2026-09-01"},
                    {"version": "0.9.0", "date": "2026-08-25"},
                ],
                "healthHistory": [{"date": "2026-09-05", "reachable": True, "latencyMs": 80}],
            }
        }
        result = compute_health_score(app, state, reachable_now=True)
        # All components near-perfect except metadata completeness -> high score.
        self.assertGreaterEqual(result["health_score"], 70)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["weights"]["downloadSuccess"], 0.30)
        # Weights sum to 1.0 and every breakdown component is bounded 0..1.
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0, places=6)
        for value in result["breakdown"].values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_unreachable_is_lower(self) -> None:
        app = _app()
        result = compute_health_score(app, {}, reachable_now=False)
        self.assertEqual(result["status"] in {"warning", "critical"}, True)
        self.assertLess(result["health_score"], 70)

    def test_archived_status(self) -> None:
        app = _app(status="deprecated")
        result = compute_health_score(app, {}, reachable_now=True)
        self.assertEqual(result["status"], "archived")

    def test_annotate(self) -> None:
        catalog = _catalog(_app())
        state = {"demo": {"versions": [dict(GOOD_NEWEST)]}}
        health_doc = {"apps": [{"slug": "demo", "downloadReachable": True}], "totals": {}}
        scores = build_health_scores(catalog, state, health_doc, {})
        annotate_health_doc(health_doc, scores)
        item = health_doc["apps"][0]
        self.assertIn("healthScore", item)
        self.assertIn("healthStatus", item)
        self.assertEqual(health_doc["totals"]["healthy"], 1)


class TrustScoreTests(unittest.TestCase):
    def test_badge_thresholds(self) -> None:
        self.assertEqual(trust_badge(9.5), "Verified")
        self.assertEqual(trust_badge(8.0), "Trusted")
        self.assertEqual(trust_badge(5.0), "Community")
        self.assertEqual(trust_badge(4.9), "Experimental")

    def test_max_score(self) -> None:
        score, badge = compute_trust_score(
            level="VERIFIED", release_count=3, consistency=1.0, uptime=1.0, hash_verified=True
        )
        self.assertEqual(score, 10.0)
        self.assertEqual(badge, "Verified")

    def test_min_score(self) -> None:
        score, badge = compute_trust_score(
            level="UNVERIFIED", release_count=0, consistency=0.0, uptime=0.0, hash_verified=False
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(badge, "Experimental")


class DeadAppsTests(unittest.TestCase):
    def test_age_thresholds(self) -> None:
        self.assertEqual(classify_age(30), "healthy")
        self.assertEqual(classify_age(90), "warning")
        self.assertEqual(classify_age(180), "stale")
        self.assertEqual(classify_age(365), "archived")

    def test_removed_release_newer_than_published_is_critical(self) -> None:
        app = _app()
        state = {
            "demo": {
                "versions": [{"version": "1.0", "date": "2026-09-01"}],
                "removedReleases": [{"version": "1.1", "at": "2026-09-02"}],
            }
        }
        doc = build_dead_apps_doc(_catalog(app), state, today_iso="2026-09-10")
        entry = doc["apps"][0]
        self.assertEqual(entry["classification"], "critical")
        self.assertEqual(doc["summary"]["critical"], 1)
        self.assertEqual(entry["removedReleases"], 1)

    def test_superseded_release_is_not_a_takedown(self) -> None:
        # upstream.keepVersions is 1, so the previous latest disappears from the
        # set on every ordinary bump; the same version on a new URL is a
        # failover rename. Neither may be published as a critical dead app.
        state = {
            "demo": {
                "versions": [{"version": "1.0", "date": "2026-09-01"}],
                "removedReleases": [{"version": "0.9", "at": "2026-09-02"}],
            },
            "other": {
                "versions": [{"version": "12.9.2", "date": "2026-09-01"}],
                "removedReleases": [{"version": "12.9.2", "at": "2026-09-02"}],
            },
        }
        doc = build_dead_apps_doc(_catalog(_app(), _app("other")), state, today_iso="2026-09-10")
        self.assertEqual(doc["summary"]["critical"], 0)
        by_slug = {entry["slug"]: entry for entry in doc["apps"]}
        for slug in ("demo", "other"):
            self.assertEqual(by_slug[slug]["classification"], "healthy", slug)
            self.assertEqual(by_slug[slug]["removedReleases"], 0, slug)
            self.assertEqual(by_slug[slug]["supersededReleases"], 1, slug)

    def test_fresh_app_is_healthy(self) -> None:
        app = _app()
        state = {"demo": {"versions": [{"version": "1.0", "date": "2026-09-08"}]}}
        doc = build_dead_apps_doc(_catalog(app), state, today_iso="2026-09-10")
        self.assertEqual(doc["count"], 0)
        self.assertEqual(doc["apps"][0]["classification"], "healthy")


def _cadence_state() -> dict:
    """One app whose releases live only in the shared timeline.

    ``keepVersions`` is 1 for most of this catalog, so ``versions`` alone holds a
    single dated release - enough to prove a release happened, not enough to say
    how often they happen.
    """
    return {
        "demo": {"versions": [{"version": "3.0", "date": "2026-09-01", "downloadURL": "https://example.test/d.ipa"}]},
        "updateHistory": [
            {"appId": "demo", "version": "3.0", "releaseDate": "2026-09-01"},
            {"appId": "demo", "version": "2.0", "releaseDate": "2026-08-08"},
            {"appId": "demo", "version": "1.0", "releaseDate": "2026-07-01"},
            {"appId": "other", "version": "9.9", "releaseDate": "2026-07-20"},
        ],
    }


class ReleaseCadenceTests(unittest.TestCase):
    """#4: cadence is measured on the whole timeline, not on keepVersions."""

    def test_per_app_window_alone_sees_no_interval(self) -> None:
        state = _cadence_state()
        self.assertEqual([d.isoformat() for d in version_dates(state, "demo")], ["2026-09-01"])
        self.assertEqual(average_update_gap_days(state, "demo"), 0.0)

    def test_shared_timeline_fills_the_series(self) -> None:
        state = _cadence_state()
        dates = version_dates(state, "demo", include_history=True)
        self.assertEqual([d.isoformat() for d in dates], ["2026-07-01", "2026-08-08", "2026-09-01"])
        # 38 days, then 24; the duplicate on 2026-09-01 (same release in both
        # sources) collapses instead of becoming a zero-length gap.
        self.assertAlmostEqual(average_update_gap_days(state, "demo", include_history=True), 31.0)

    def test_other_apps_do_not_leak_in(self) -> None:
        state = _cadence_state()
        self.assertEqual(version_dates(state, "missing", include_history=True), [])

    def test_reputation_publishes_the_merged_cadence(self) -> None:
        app = _app("demo", upstream={"type": "github", "repo": "example/demo", "assetPattern": ".*"})
        doc = build_reputation_doc(_catalog(app), _cadence_state())
        signals = doc["sources"][0]["signals"]
        self.assertEqual(signals["updateFrequencyDays"], 31.0)
        # The same releases are what the 365-day window counts.
        self.assertEqual(signals["releasesPastYear"], 3)


class CollectionsTests(unittest.TestCase):
    def test_build_doc_shape(self) -> None:
        catalog = _catalog(_app("ytlite"), _app("utm"))
        doc = build_collections_doc(catalog, {})
        self.assertEqual(doc["count"], 5)
        slugs = {c["slug"] for c in doc["collections"]}
        self.assertEqual(slugs, {"youtube", "music", "emulators", "utilities", "productivity"})
        # Only apps present in the catalog are referenced.
        for collection in doc["collections"]:
            for slug in collection["appSlugs"]:
                self.assertIn(slug, {"ytlite", "utm"})

    def test_pages_generated(self) -> None:
        import tempfile

        catalog = _catalog(_app("utm"))
        with tempfile.TemporaryDirectory() as tmp:
            changed = build_collection_pages(catalog, {}, pages_dir=Path(tmp))
            names = {p.parent.name for p in changed}
            self.assertEqual(len(changed), 5)
            self.assertIn("emulators", names)
            content = (Path(tmp) / "emulators" / "index.html").read_text(encoding="utf-8")
            self.assertIn("App Utm", content)


class ComparePageTests(unittest.TestCase):
    def test_both_orders_generated(self) -> None:
        import tempfile

        catalog = _catalog(_app("aaa"), _app("bbb"))
        health_doc = {"apps": [{"slug": "aaa", "downloadReachable": True}, {"slug": "bbb", "downloadReachable": True}]}
        verification_doc = {
            "apps": [
                {"app": "aaa", "status": "VERIFIED", "trustScore": 9.0, "trustBadge": "Verified"},
                {"app": "bbb", "status": "UNVERIFIED", "trustScore": 1.0, "trustBadge": "Experimental"},
            ]
        }
        state = {
            "aaa": {"versions": [dict(GOOD_NEWEST, version="1.0")]},
            "bbb": {"versions": [dict(GOOD_NEWEST, version="2.0", size=2000)]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            changed = build_compare_pages(catalog, state, health_doc, verification_doc, pages_dir=Path(tmp))
            # One unordered pair -> 1 canonical page + 1 reverse-order stub.
            self.assertEqual(len(changed), 2)
            canonical = (Path(tmp) / "aaa-vs-bbb" / "index.html").read_text(encoding="utf-8")
            self.assertIn("App Aaa vs App Bbb", canonical)
            self.assertIn("Trust score", canonical)
            stub = (Path(tmp) / "bbb-vs-aaa" / "index.html").read_text(encoding="utf-8")
            self.assertIn('http-equiv="refresh"', stub)
            self.assertIn("aaa-vs-bbb", stub)

    def test_redirect_helper(self) -> None:
        page = render_compare_redirect("../../compare/a-vs-b/")
        self.assertIn("../../compare/a-vs-b/", page)

    def test_render_page_escaping(self) -> None:
        left = {
            "slug": "a",
            "name": "A <b>",
            "icon": "../../assets/i.png",
            "category": "other",
            "developer": "d",
            "version": "1",
            "releaseDate": "",
            "sizeBytes": 0,
            "updateFrequencyDays": 0.0,
            "verificationLevel": "UNVERIFIED",
            "trustScore": 1.0,
            "trustBadge": "Experimental",
            "healthScore": 10,
            "healthStatus": "critical",
            "features": 'x"y',
            "detailURL": "../../apps/a/",
        }
        right = dict(left, slug="b", name="B")
        page = render_compare_page(catalog=_catalog(_app("a")), left=left, right=right, base_url="https://x.dev")
        self.assertIn("&lt;b&gt;", page)
        self.assertIn("Trust score", page)


if __name__ == "__main__":
    unittest.main()
