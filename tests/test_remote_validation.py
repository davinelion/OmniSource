"""Tests for the discovery validation engine (offline)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

from omnisource import autodiscovery
from omnisource.remote_validation import (
    assert_publishable,
    check_url_reachable,
    validate_remote_app,
    validate_remote_feed,
    validate_remote_release,
    validate_source_record,
)
from omnisource.validation import SOURCE_ID_RE


def _app(**overrides) -> dict:
    app = {
        "name": "Demo",
        "bundleIdentifier": "com.example.demo",
        "developerName": "Example",
        "version": "1.0",
        "versionDate": "2026-01-01",
        "downloadURL": "https://example.com/demo.ipa",
        "localizedDescription": "A demo app.",
    }
    app.update(overrides)
    return app


class SourceRecordTests(TestCase):
    def test_valid_record(self) -> None:
        record = autodiscovery.new_record(url="https://example.com/apps.json", feed_type="altstore")
        self.assertEqual(validate_source_record(record), [])

    def test_rejects_bad_url_and_type(self) -> None:
        record = autodiscovery.new_record(url="http://example.com/apps.json", feed_type="altstore")
        record["type"] = "nope"
        record["reputation"] = 101
        errors = validate_source_record(record)
        self.assertTrue(any("https" in error for error in errors))
        self.assertTrue(any("type" in error for error in errors))
        self.assertTrue(any("reputation" in error for error in errors))

    def test_rejects_non_object(self) -> None:
        self.assertTrue(validate_source_record([]))

    def test_accepts_url_derived_source_ids(self) -> None:
        # Discovery ids are host + path stem + digest, bounded at 80 characters
        # by schemas/discovery.schema.json. Validating them with the 32-char
        # catalog app-slug rule (SLUG_RE) made every discovered candidate an
        # error, so discovery.yml failed as soon as a pass found anything.
        record = autodiscovery.new_record(
            url="https://github.com/some-owner/a-long-repository-name/apps.json",
            feed_type="altstore",
        )
        self.assertGreater(len(record["source_id"]), 32)
        self.assertRegex(record["source_id"], SOURCE_ID_RE)
        self.assertEqual(validate_source_record(record), [])

    def test_rejects_source_id_outside_the_schema(self) -> None:
        record = autodiscovery.new_record(url="https://example.com/apps.json", feed_type="altstore")
        record["source_id"] = "a" * 81
        errors = validate_source_record(record)
        self.assertTrue(any("source_id" in error for error in errors))


class RemoteFeedTests(TestCase):
    def test_valid_feed(self) -> None:
        feed = {"name": "X", "identifier": "com.x", "apps": [_app()]}
        self.assertEqual(validate_remote_feed(feed), [])

    def test_duplicate_bundles_rejected(self) -> None:
        feed = {"name": "X", "identifier": "com.x", "apps": [_app(), _app()]}
        errors = validate_remote_feed(feed, url="https://x.test/f.json")
        self.assertTrue(any("duplicate bundleIdentifier" in error for error in errors))

    def test_empty_apps_rejected(self) -> None:
        self.assertTrue(validate_remote_feed({"name": "X", "identifier": "com.x", "apps": []}))


class RemoteAppTests(TestCase):
    def test_valid_app(self) -> None:
        self.assertEqual(validate_remote_app(_app()), [])

    def test_missing_fields_and_bad_values(self) -> None:
        errors = validate_remote_app(_app(bundleIdentifier="bad id!", downloadURL="ftp://x/y", versionDate="now"))
        self.assertTrue(any("bundleIdentifier" in error for error in errors))
        self.assertTrue(any("downloadURL" in error for error in errors))
        self.assertTrue(any("versionDate" in error for error in errors))

    def test_bad_size_and_screenshots(self) -> None:
        errors = validate_remote_app(_app(size=-1, screenshotURLs=["not-a-url"]))
        self.assertTrue(any("size" in error for error in errors))
        self.assertTrue(any("screenshotURLs" in error for error in errors))


class RemoteReleaseTests(TestCase):
    def test_valid_release(self) -> None:
        release = {
            "tag_name": "v1.0",
            "assets": [{"name": "app.ipa", "browser_download_url": "https://example.com/app.ipa"}],
        }
        self.assertEqual(validate_remote_release(release), [])

    def test_rejects_missing_ipa(self) -> None:
        release = {"tag_name": "v1.0", "assets": [{"name": "notes.txt"}]}
        self.assertTrue(any("ipa" in error for error in validate_remote_release(release)))

    def test_rejects_malformed_digest(self) -> None:
        release = {"tag_name": "v1.0", "assets": [], "sha256": "zzz"}
        self.assertTrue(any("sha256" in error for error in validate_remote_release(release)))


class PublishableTests(TestCase):
    def test_publishable_feed(self) -> None:
        record = autodiscovery.new_record(url="https://example.com/apps.json", feed_type="altstore")
        feed = {"name": "X", "identifier": "com.x", "apps": [_app()]}
        ok, errors, _warnings = assert_publishable(record, feed_payload=feed)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_invalid_never_publishes(self) -> None:
        record = {"nope": True}
        ok, errors, _warnings = assert_publishable(record, feed_payload={"nope": True})
        self.assertFalse(ok)
        self.assertTrue(errors)

    def test_non_integer_reputation_is_an_error_not_a_crash(self) -> None:
        # A discovered record is untrusted input: a third-party feed can put any
        # JSON value in "reputation". Comparing it with `<` used to raise
        # TypeError, which took the whole fail-closed gate down with a traceback
        # instead of reporting the record as invalid.
        record = autodiscovery.new_record(url="https://example.com/apps.json", feed_type="altstore")
        record["reputation"] = "high"
        ok, errors, warnings = assert_publishable(record)
        self.assertFalse(ok)
        self.assertTrue(any("reputation" in error for error in errors))
        self.assertEqual(warnings, [])

    def test_low_reputation_warns_without_blocking(self) -> None:
        record = autodiscovery.new_record(url="https://example.com/apps.json", feed_type="altstore", reputation=10)
        ok, errors, warnings = assert_publishable(record)
        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertTrue(any("reputation below 25" in warning for warning in warnings))

    def test_non_url_unreachable(self) -> None:
        ok, _detail = check_url_reachable("not-a-url")
        self.assertFalse(ok)


class ValidateFeedCliTests(TestCase):
    """The validate_feed.py CLI must treat local files as payloads, not URLs."""

    def _run_cli(self, feed: dict, *extra: str) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(feed, handle)
            path = handle.name
        try:
            return subprocess.run(
                [sys.executable, str(root / "scripts" / "validation" / "validate_feed.py"), path, *extra],
                capture_output=True,
                text=True,
                cwd=root,
                check=False,
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def test_local_file_skips_record_url_check(self) -> None:
        feed = {"name": "X", "identifier": "com.x", "apps": [_app()]}
        completed = self._run_cli(feed)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("publishable=True", completed.stdout)

    def test_duplicate_bundles_strict_by_default(self) -> None:
        feed = {"name": "X", "identifier": "com.x", "apps": [_app(), _app()]}
        completed = self._run_cli(feed)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("duplicate bundleIdentifier", completed.stdout)

    def test_allow_duplicates_for_merged_feeds(self) -> None:
        feed = {"name": "X", "identifier": "com.x", "apps": [_app(), _app()]}
        completed = self._run_cli(feed, "--allow-duplicates")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("publishable=True", completed.stdout)


if __name__ == "__main__":
    import unittest

    unittest.main()
