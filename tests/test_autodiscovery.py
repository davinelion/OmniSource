"""Tests for the autonomous discovery engine (offline)."""

from __future__ import annotations

from unittest import TestCase

from omnisource import autodiscovery
from omnisource.validation import SOURCE_ID_RE


def _record(url: str = "https://example.com/apps.json", **overrides) -> dict:
    record = autodiscovery.new_record(url=url, name="Example", feed_type="altstore")
    record.update(overrides)
    return record


class DiscoveryRecordTests(TestCase):
    def test_new_record_schema(self) -> None:
        record = _record()
        for field in ("source_id", "name", "url", "type", "discovered_at", "last_checked", "health", "reputation"):
            self.assertIn(field, record)
        self.assertEqual(record["health"], "unknown")
        self.assertEqual(record["reputation"], 0)

    def test_source_id_is_stable(self) -> None:
        self.assertEqual(
            autodiscovery.source_id_for_url("https://example.com/apps.json"),
            autodiscovery.source_id_for_url("https://example.com/apps.json"),
        )
        self.assertNotEqual(
            autodiscovery.source_id_for_url("https://a.example.com/apps.json"),
            autodiscovery.source_id_for_url("https://b.example.com/apps.json"),
        )

    def test_source_id_always_matches_the_discovery_schema(self) -> None:
        # The id is generated from arbitrary discovered URLs, so it must satisfy
        # SOURCE_ID_RE (schemas/discovery.schema.json) by construction: an
        # exotic host or an over-long path used to produce an id the validation
        # gate rejected, quarantining a candidate for its own identifier.
        urls = (
            "https://example.com/apps.json",
            "https://github.com/SomeOwner/A-Very-Long-Repository-Name-Here/releases",
            "https://a-very-long-subdomain-name.example.co.uk/sources/altstore/apps.json",
            "https://例え.example/source.json",
            "https://example.com/" + "p" * 200,
        )
        for url in urls:
            with self.subTest(url=url):
                source_id = autodiscovery.source_id_for_url(url)
                self.assertRegex(source_id, SOURCE_ID_RE)
                self.assertLessEqual(len(source_id), 80)

    def test_reputation_is_clamped(self) -> None:
        high = autodiscovery.new_record(url="https://example.com/a.json", reputation=500)
        low = autodiscovery.new_record(url="https://example.com/b.json", reputation=-3)
        self.assertEqual(high["reputation"], 100)
        self.assertEqual(low["reputation"], 0)

    def test_unknown_type_falls_back(self) -> None:
        self.assertEqual(autodiscovery.new_record(url="https://x.test/f.json", feed_type="nope")["type"], "unknown")


class ClassifyFeedTests(TestCase):
    def test_altstore_envelope(self) -> None:
        feed_type, count = autodiscovery.classify_feed({"identifier": "com.x", "apps": [{}, {}]})
        self.assertEqual((feed_type, count), ("altstore", 2))

    def test_client_markers(self) -> None:
        for marker, expected in (("feather", "feather"), ("esign", "esign"), ("livecontainer", "livecontainer")):
            feed_type, _ = autodiscovery.classify_feed({"identifier": marker, "apps": [{}]})
            self.assertEqual(feed_type, expected)

    def test_rejects_non_feeds(self) -> None:
        self.assertEqual(autodiscovery.classify_feed({"nope": True}), ("unknown", 0))
        self.assertEqual(autodiscovery.classify_feed([1, 2]), ("unknown", 0))

    def test_client_for_url(self) -> None:
        self.assertEqual(autodiscovery.client_for_url("https://feather.example.com/s.json"), "feather")
        self.assertEqual(autodiscovery.client_for_url("https://example.com/apps.json"), "unknown")


class MergeRecordsTests(TestCase):
    def test_merge_refreshes_last_checked(self) -> None:
        old = [_record(discovered_at="2020-01-01T00:00:00Z", last_checked="2020-01-01T00:00:00Z")]
        merged = autodiscovery.merge_records(old, [_record(health="online")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["discovered_at"], "2020-01-01T00:00:00Z")
        self.assertNotEqual(merged[0]["last_checked"], "2020-01-01T00:00:00Z")
        self.assertEqual(merged[0]["health"], "online")

    def test_merge_keeps_both_urls(self) -> None:
        merged = autodiscovery.merge_records([_record()], [_record(url="https://other.test/s.json")])
        self.assertEqual(len(merged), 2)


class CandidateUrlTests(TestCase):
    def test_json_passthrough(self) -> None:
        self.assertEqual(
            autodiscovery.candidate_feed_urls("https://example.com/apps.json"),
            ["https://example.com/apps.json"],
        )

    def test_expands_bare_hosts(self) -> None:
        urls = autodiscovery.candidate_feed_urls("https://example.com")
        self.assertIn("https://example.com/apps.json", urls)
        self.assertIn("https://example.com/source.json", urls)

    def test_extract_feed_links(self) -> None:
        html = '<a href="/apps.json">a</a><a href="https://cdn.test/x.json?v=1">b</a><a href="/page.html">c</a>'
        links = autodiscovery.extract_feed_links(html, "https://example.com/dir/")
        self.assertIn("https://example.com/apps.json", links)
        self.assertIn("https://cdn.test/x.json?v=1", links)
        self.assertEqual(len(links), 2)

    def test_pages_url_for_repo(self) -> None:
        self.assertEqual(
            autodiscovery.pages_url_for_repo("octo/source", "apps.json"),
            "https://octo.github.io/source/apps.json",
        )
        self.assertEqual(
            autodiscovery.pages_url_for_repo("octo/octo.github.io", "apps.json"),
            "https://octo.github.io/apps.json",
        )

    def test_has_ipa_assets(self) -> None:
        release = {"assets": [{"name": "app.ipa"}, {"name": "notes.txt"}]}
        self.assertTrue(autodiscovery.has_ipa_assets(release))
        self.assertFalse(autodiscovery.has_ipa_assets({"assets": [{"name": "notes.txt"}]}))


if __name__ == "__main__":
    import unittest

    unittest.main()
