"""Tests for the recorded sourcing verdicts (:mod:`omnisource.source_policy`).

Everything here runs offline against fixture policies plus the shipped
``data/source_policy.json`` and ``catalog.json``, because the point of the
module is that a decision already made stays made: a blocked host must not be
promotable by discovery, must not be referenceable from the catalog, and must not
be usable as an input to the tweak patcher.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource import autodiscovery
from omnisource.constants import Paths
from omnisource.remote_validation import assert_publishable
from omnisource.source_policy import (
    POLICY_RELATIVE_PATH,
    catalog_violations,
    decide,
    load_policy,
    parse_policy,
    partition_by_policy,
    validate_policy,
)
from omnisource.validation import validate_source_policy

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = {
    "version": 1,
    "rules": [
        {
            "id": "account-gated-decrypted-store",
            "description": "Login-walled decrypted app stores.",
            "reason": "no upstream, no digest, nothing to verify",
            "hosts": ["armconverter.com/store", "armconverter.com/decryptedappstore", "example-store.net"],
            "nameKeywords": ["cracked"],
            "references": ["https://example.com/review"],
            "decidedAt": "2026-09-15",
        },
        {
            "id": "tweaked-app-aggregator",
            "description": "Repositories that only re-host other people's IPAs.",
            "reason": "an aggregator is not the developer's release channel",
            "repos": ["someone/ipa-library"],
            "references": ["https://example.com/review2"],
            "decidedAt": "2026-09-15",
        },
    ],
}


def _policy(document=FIXTURE):
    policy = parse_policy(document)
    assert not policy.error, policy.error
    return policy


class ParsePolicyTests(unittest.TestCase):
    def test_accepts_a_well_formed_document(self) -> None:
        policy = _policy()
        self.assertEqual(
            [rule.id for rule in policy.rules], ["account-gated-decrypted-store", "tweaked-app-aggregator"]
        )
        self.assertEqual(policy.rules[0].hosts[0], "armconverter.com/store")

    def test_missing_file_is_an_empty_policy_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = load_policy(Path(tmp))
        self.assertEqual(policy.rules, [])
        self.assertEqual(policy.error, "")

    def test_rejects_documents_that_would_match_everything(self) -> None:
        cases = {
            "no matchers": {"rules": [{"id": "x", "reason": "r"}]},
            "duplicate id": {
                "rules": [
                    {"id": "x", "reason": "r", "hosts": ["a.com"]},
                    {"id": "x", "reason": "r", "hosts": ["b.com"]},
                ]
            },
            "no reason": {"rules": [{"id": "x", "hosts": ["a.com"]}]},
            "schemeful host": {"rules": [{"id": "x", "reason": "r", "hosts": ["https://a.com"]}]},
            "wrong version": {"version": 2, "rules": []},
            "rules not a list": {"rules": {}},
        }
        for label, document in cases.items():
            with self.subTest(label):
                self.assertTrue(parse_policy(document).error)

    def test_shipped_data_file_stays_inside_the_declared_schema(self) -> None:
        # No jsonschema dependency in this repo, so the published schema and the
        # data file are checked against each other: every key the file uses must be
        # one the schema declares, since the schema closes unknown properties.
        schema = json.loads((ROOT / "schemas" / "source-policy.schema.json").read_text(encoding="utf-8"))
        document = json.loads((ROOT / POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertTrue(schema["additionalProperties"] is False)
        rule_schema = schema["definitions"]["rule"]
        self.assertTrue(rule_schema["additionalProperties"] is False)
        allowed_top = set(schema["properties"])
        allowed_rule = set(rule_schema["properties"])
        self.assertEqual(sorted(key for key in document if key not in allowed_top), [])
        for rule in document["rules"]:
            extra = sorted(key for key in rule if key not in allowed_rule)
            self.assertEqual(extra, [], f"rule {rule.get('id')} uses keys the schema does not declare: {extra}")
            self.assertTrue(set(rule_schema["required"]) <= set(rule), f"rule {rule.get('id')} is missing keys")


class DecideTests(unittest.TestCase):
    def test_bare_host_blocks_subdomains_and_every_path(self) -> None:
        policy = _policy()
        for url in ("https://example-store.net/", "https://feed.example-store.net/apps.json"):
            self.assertTrue(decide(url, policy=policy).blocked, url)

    def test_host_with_a_prefix_blocks_only_that_subtree(self) -> None:
        policy = _policy()
        blocked = decide("https://armconverter.com/store/us", policy=policy)
        self.assertTrue(blocked.blocked)
        self.assertEqual(blocked.rule_id, "account-gated-decrypted-store")
        self.assertIn("nothing to verify", blocked.reason)
        # The rest of the domain is unrelated to the verdict and stays reviewable.
        self.assertFalse(decide("https://armconverter.com/", policy=policy).blocked)
        self.assertTrue(decide("https://armconverter.com/decryptedappstore/Bitlife", policy=policy).blocked)

    def test_repository_rules_cover_github_and_raw_urls(self) -> None:
        policy = _policy()
        for url in (
            "https://github.com/someone/ipa-library/releases/latest",
            "https://raw.githubusercontent.com/someone/ipa-library/main/apps.json",
        ):
            decision = decide(url, policy=policy)
            self.assertTrue(decision.blocked, url)
            self.assertEqual(decision.rule_id, "tweaked-app-aggregator")
        self.assertFalse(decide("https://github.com/SomeOther/Project", policy=policy).blocked)

    def test_name_keywords_catch_renamed_rehosts_of_a_blocked_store(self) -> None:
        policy = _policy()
        decision = decide("https://elsewhere.example/feed.json", name="Some Cracked IPA Library", policy=policy)
        self.assertTrue(decision.blocked)
        self.assertIn("name ", decision.matched_on)

    def test_empty_policy_blocks_nothing(self) -> None:
        self.assertFalse(decide("https://armconverter.com/store/us", policy=parse_policy(None)).blocked)
        self.assertFalse(decide("https://armconverter.com/store/us", policy=None).blocked)

    def test_urls_without_a_host_are_ignored(self) -> None:
        policy = _policy()
        for value in ("", "not a url", "file:///tmp/apps.json"):
            self.assertFalse(decide(value, policy=policy).blocked, value)


class PartitionTests(unittest.TestCase):
    def test_splits_records_and_keeps_order(self) -> None:
        policy = _policy()
        records = [
            {"url": "https://armconverter.com/store/us/apps.json", "name": "ARM Store"},
            {"url": "https://github.com/someone/ipa-library", "name": "Library"},
            {"url": "https://good.example/apps.json", "name": "Good"},
        ]
        kept, excluded = partition_by_policy(records, policy)
        self.assertEqual([item["name"] for item in kept], ["Good"])
        self.assertEqual([item["name"] for item in excluded], ["ARM Store", "Library"])

    def test_non_dict_records_fall_back_to_the_url_string(self) -> None:
        policy = _policy()
        kept, excluded = partition_by_policy(["https://example-store.net/apps.json", "https://good.example/a"], policy)
        self.assertEqual(kept, ["https://good.example/a"])
        self.assertEqual(excluded, ["https://example-store.net/apps.json"])

    def test_shipped_store_holds_no_excluded_records(self) -> None:
        # Regression guard for the prune: discovery re-adds these hosts every run,
        # so the committed store must stay free of anything the policy rejects.
        policy = load_policy(ROOT)
        records = json.loads((ROOT / "data" / "discovered_sources.json").read_text(encoding="utf-8"))["sources"]
        _kept, excluded = partition_by_policy(records, policy)
        self.assertEqual(excluded, [])


class CatalogGateTests(unittest.TestCase):
    def test_entry_resolving_from_a_blocked_host_is_an_error(self) -> None:
        policy = _policy()
        catalog = {
            "apps": [
                {
                    "slug": "demo",
                    "upstream": {"provider": "altstore", "feedURL": "https://example-store.net/repo.json"},
                },
                {"slug": "mirror", "manualRelease": {"downloadURL": "https://armconverter.com/store/us/demo.ipa"}},
                {"slug": "ok", "upstream": {"provider": "altstore", "feedURL": "https://good.example/apps.json"}},
            ]
        }
        errors = catalog_violations(catalog, policy)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("demo" in error or "mirror" in error for error in errors), errors)
        self.assertTrue(all("sourcing policy" in error for error in errors), errors)

    def test_fallback_mirrors_are_gated_too(self) -> None:
        policy = _policy()
        catalog = {"apps": [{"slug": "demo", "fallbackDownloadURLs": ["https://example-store.net/mirror/demo.ipa"]}]}
        self.assertIn("fallbackDownloadURLs[0]", catalog_violations(catalog, policy)[0])

    def test_shipped_catalog_resolves_only_from_unblocked_sources(self) -> None:
        policy = load_policy(ROOT)
        self.assertTrue(policy.rules, "the shipped policy must not be empty")
        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog_violations(catalog, policy), [])


class ValidatorTests(unittest.TestCase):
    def test_shipped_policy_file_passes_its_own_rules(self) -> None:
        document = json.loads((ROOT / POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(validate_policy(document), [])
        for rule in document["rules"]:
            self.assertTrue(rule["references"], "a verdict without evidence is not reviewable")
            self.assertTrue(rule["hosts"] or rule["repos"] or rule["nameKeywords"])

    def test_validator_reports_a_blocked_catalog_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text(json.dumps(FIXTURE), encoding="utf-8")
            catalog = {"apps": [{"slug": "demo", "upstreamURL": "https://example-store.net/"}]}
            report = validate_source_policy(Paths.from_root(root), catalog)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("demo.upstreamURL", report.errors[0])

    def test_validator_warns_when_the_policy_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = validate_source_policy(Paths.from_root(Path(tmp)), {"apps": []})
        self.assertEqual(report.errors, [])
        self.assertTrue(any("missing" in warning for warning in report.warnings), report.warnings)

    def test_validator_fails_closed_on_an_unparseable_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text("{oops", encoding="utf-8")
            report = validate_source_policy(Paths.from_root(root), {"apps": []})
        self.assertTrue(report.errors)


class PublicationGateTests(unittest.TestCase):
    def _record(self, url: str, name: str = "Candidate") -> dict:
        return {
            "source_id": "candidate-example-a1b2c3",
            "name": name,
            "url": url,
            "type": "altstore",
            "discovered_at": "2026-09-15T00:00:00Z",
            "last_checked": "2026-09-15T00:00:00Z",
            "health": "online",
            "reputation": 60,
        }

    def test_blocked_url_is_never_publishable_even_with_high_reputation(self) -> None:
        ok, errors, _warnings = assert_publishable(
            self._record("https://armconverter.com/store/us/apps.json", "ARM Store"), policy=_policy()
        )
        self.assertFalse(ok)
        self.assertTrue(any("account-gated-decrypted-store" in error for error in errors), errors)

    def test_unblocked_url_stays_publishable(self) -> None:
        ok, errors, _warnings = assert_publishable(
            self._record("https://github.com/Good/Project/releases"), policy=_policy()
        )
        self.assertTrue(ok, errors)

    def test_unusable_policy_file_blocks_the_run(self) -> None:
        # A policy that cannot be parsed must not silently become "no rules":
        # that would let the blocked hosts back in through discovery.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text('{"rules": "nope"}', encoding="utf-8")
            ok, errors, _warnings = assert_publishable(self._record("https://good.example/apps.json"), root=root)
        self.assertFalse(ok)
        self.assertTrue(any("sourcing policy is unusable" in error for error in errors), errors)

    def test_store_cannot_be_written_with_a_blocked_record(self) -> None:
        # Every discovery path funnels through save_store, so the enforcement has
        # to live there: a caller that forgets to filter must not be able to
        # re-propose a rejected source on the next run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text(json.dumps(FIXTURE), encoding="utf-8")
            store = root / "data" / "discovered_sources.json"
            wrote = autodiscovery.save_store(
                store,
                [
                    {"url": "https://github.com/Good/Project", "name": "Good"},
                    {"url": "https://armconverter.com/store/us/apps.json", "name": "ARM Store"},
                ],
                root=root,
            )
            stored = autodiscovery.load_store(store)["sources"]
        self.assertTrue(wrote)
        self.assertEqual([record["url"] for record in stored], ["https://github.com/Good/Project"])

    def test_a_stored_record_is_pruned_when_the_policy_blocks_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text(json.dumps(FIXTURE), encoding="utf-8")
            store = root / "data" / "discovered_sources.json"
            autodiscovery.save_store(store, [{"url": "https://armconverter.com/store/us/apps.json"}], root=root)
            reloaded = autodiscovery.load_store(store)["sources"]
            # An empty rewrite is still a rewrite; what matters is that the
            # blocked record cannot survive it.
            self.assertEqual(reloaded, [])

    def test_an_unusable_policy_blocks_the_write(self) -> None:
        # Fail-closed: a policy file that does not parse must not degrade into
        # "no rules" for a writer that could otherwise publish a rejected source.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_policy.json").write_text('{"rules": 3}', encoding="utf-8")
            store = root / "data" / "discovered_sources.json"
            wrote = autodiscovery.save_store(store, [{"url": "https://github.com/A/B"}], root=root)
        self.assertFalse(wrote)
        self.assertFalse(store.exists())

    def test_every_store_writer_enforces_the_policy(self) -> None:
        # Structural guard: the store has many writers (GitHub search, the
        # GitLab/Codeberg/Forgejo sweep, feed scraping, web-catalog crawling,
        # revalidation). A new one that skips root= would silently undo the gate.
        writers = sorted((ROOT / "scripts").glob("*/discover*.py")) + sorted(
            (ROOT / "scripts").glob("*/validate_source.py")
        )
        self.assertGreaterEqual(len(writers), 7, writers)
        for path in writers:
            with self.subTest(script=str(path.relative_to(ROOT))):
                text = path.read_text(encoding="utf-8")
                if "save_store(" not in text:
                    continue
                self.assertIn("root=ROOT", text)

    def test_shipped_policy_rejects_the_store_it_recorded(self) -> None:
        shipped = load_policy(ROOT)
        for url in (
            "https://armconverter.com/store/us",
            "https://armconverter.com/store/eu",
            "https://armconverter.com/decryptedappstore/AnyApp",
            "https://ipa.cypwn.xyz/cypwn.json",
            "https://github.com/Neoncat-OG/TrollStore-IPAs/releases",
            "https://rejail.ru/apt",
        ):
            self.assertTrue(decide(url, policy=shipped).blocked, url)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
