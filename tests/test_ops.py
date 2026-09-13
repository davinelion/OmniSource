"""Tests for ops modules: reputation labels, probes, enrichment, security, self-heal, mirrors, rollups."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

from omnisource import mirrors, reputation_labels, security, selfheal
from omnisource.analytics_rollup import build_rollup, normalize_history
from omnisource.backup import LABELS
from omnisource.enrichment import clean_text, enrich_app, normalize_category, normalize_url
from omnisource.probes import build_status, state_for


class ReputationLabelTests(TestCase):
    def test_bands(self) -> None:
        self.assertEqual(reputation_labels.label_for_score(90), "verified")
        self.assertEqual(reputation_labels.label_for_score(70), "trusted")
        self.assertEqual(reputation_labels.label_for_score(50), "good")
        self.assertEqual(reputation_labels.label_for_score(25), "warning")
        self.assertEqual(reputation_labels.label_for_score(10), "untrusted")
        self.assertEqual(reputation_labels.label_for_score(None), "untrusted")

    def test_build_labels(self) -> None:
        doc = reputation_labels.build_labels({"sources": [{"id": "s", "source": "S", "score": 95, "apps": []}]})
        self.assertEqual(doc["sources"][0]["status"], "verified")
        self.assertEqual(doc["quarantined"], [])

    def test_quarantine(self) -> None:
        self.assertTrue(reputation_labels.is_quarantined(10))
        self.assertFalse(reputation_labels.is_quarantined(80))


class ProbeTests(TestCase):
    def test_state_for(self) -> None:
        self.assertEqual(state_for(3, 3), "online")
        self.assertEqual(state_for(0, 3), "offline")
        self.assertEqual(state_for(1, 3), "degraded")
        self.assertEqual(state_for(0, 0), "offline")

    def test_build_status(self) -> None:
        doc = build_status(
            [{"id": "s", "url": "https://x.test", "ok": True}],
            [{"id": "a", "url": "https://x.test/a.ipa", "ok": False}],
        )
        self.assertEqual(doc["overall"], "degraded")
        self.assertEqual(doc["failing"], ["a"])
        self.assertEqual(len(doc["history"]), 1)


class EnrichmentTests(TestCase):
    def test_clean_text(self) -> None:
        self.assertEqual(clean_text("  a   b  "), "a b")

    def test_normalize_category(self) -> None:
        self.assertEqual(normalize_category("tweak"), "Utilities")
        self.assertEqual(normalize_category(""), "Utilities")

    def test_normalize_url_strips_tracking(self) -> None:
        self.assertEqual(
            normalize_url("https://example.com/x?utm_source=a&keep=1"),
            "https://example.com/x?keep=1",
        )

    def test_enrich_app_backfills(self) -> None:
        enriched = enrich_app({"name": "  Demo ", "localizedDescription": "First. Second."})
        self.assertEqual(enriched["name"], "Demo")
        self.assertEqual(enriched["subtitle"], "First")
        self.assertIn("iconURL", enriched["missingFields"])


class SecurityTests(TestCase):
    def test_verify_hashes(self) -> None:
        digest = "ab" * 32
        report = security.verify_hashes(
            [
                {"slug": "good", "versions": [{"version": "1", "sha256": digest}]},
                {"slug": "bad", "versions": [{"version": "1", "sha256": "zzz"}]},
                {"slug": "none", "versions": [{"version": "1"}]},
            ]
        )
        self.assertEqual(report["sha256"], 1)
        self.assertEqual(report["invalid"], ["bad"])
        self.assertIn("none", report["missing"])

    def test_duplicate_binaries(self) -> None:
        digest = "cd" * 32
        dupes = security.find_duplicate_binaries(
            [
                {"bundleIdentifier": "com.a", "versions": [{"sha256": digest}]},
                {"bundleIdentifier": "com.b", "versions": [{"sha256": digest}]},
            ]
        )
        self.assertEqual(len(dupes), 1)

    def test_verdict_fails_on_critical(self) -> None:
        report = security.build_security_report(
            apps=[{"slug": "bad", "versions": [{"version": "1", "sha256": "zzz"}]}],
            catalog_apps=[],
            health={},
        )
        self.assertEqual(report["verdict"], "fail")


class SelfhealTests(TestCase):
    def test_detect_broken_download(self) -> None:
        apps = [{"slug": "a", "name": "A", "downloadURL": "https://x.test/a.ipa"}]
        health = {"apps": [{"slug": "a", "status": "unavailable", "error": "HTTP 404"}]}
        issues = selfheal.detect_issues(apps=apps, health=health)
        kinds = {issue["type"] for issue in issues}
        self.assertIn("broken_download", kinds)
        self.assertIn("missing_metadata", kinds)

    def test_plan_repairs(self) -> None:
        plans = selfheal.plan_repairs(
            [
                {"type": "broken_download", "slug": "a", "transient": True, "fallbacks": []},
                {"type": "broken_download", "slug": "b", "transient": False, "fallbacks": ["https://m.test/b.ipa"]},
            ]
        )
        by_slug = {plan["slug"]: plan["action"] for plan in plans}
        self.assertEqual(by_slug, {"a": "retry", "b": "repair"})

    def test_apply_safe_fixes(self) -> None:
        apps = [{"slug": "b", "downloadURL": "https://dead.test/b.ipa"}]
        plans = [{"slug": "b", "action": "repair", "fallbacks": ["https://m.test/b.ipa"]}]
        fixed, applied = selfheal.apply_safe_fixes(apps, plans)
        self.assertEqual(applied, 1)
        self.assertEqual(fixed[0]["downloadURL"], "https://m.test/b.ipa")


class MirrorTests(TestCase):
    def test_select_mirrors_prefers_healthy(self) -> None:
        registry = {
            "mirrors": [
                {"id": "m1", "type": "cdn", "urlTemplate": "https://cdn.test/{slug}.ipa", "apps": ["*"]},
            ]
        }
        ordered = mirrors.select_mirrors(
            registry, "demo", "https://up.test/demo.ipa", health={"https://up.test/demo.ipa": False}
        )
        self.assertEqual(ordered[0]["url"], "https://cdn.test/demo.ipa")

    def test_failover(self) -> None:
        registry = {
            "mirrors": [{"id": "m1", "type": "backup", "urlTemplate": "https://b.test/{slug}.ipa", "apps": ["*"]}]
        }
        result = mirrors.failover(registry, "demo", "https://up.test/demo.ipa", "https://up.test/demo.ipa")
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://b.test/demo.ipa")

    def test_failover_without_mirrors(self) -> None:
        result = mirrors.failover(mirrors.default_registry(), "demo", "https://up.test/d.ipa", "https://up.test/d.ipa")
        self.assertFalse(result["ok"])


class RollupTests(TestCase):
    def test_rollup_windows(self) -> None:
        rows = normalize_history([{"date": f"2026-01-{day:02d}", "totals": {"apps": 90 + day}} for day in range(1, 12)])
        self.assertEqual(len(rows), 11)
        doc = build_rollup(
            {
                "generatedAt": "2026-01-12",
                "totals": {"apps": 100},
                "history": [{"date": f"2026-01-{d:02d}", "totals": {"apps": d}} for d in range(1, 12)],
            }
        )
        self.assertEqual(doc["coverageDays"], 11)
        self.assertTrue(doc["daily"])
        self.assertTrue(doc["weekly"])
        self.assertEqual(len(doc["monthly"]), 1)


class BackupLabelContractTests(TestCase):
    """``backup.yml`` must only use labels ``create_backup.py`` accepts.

    The workflow passed ``--label scheduled``, which is not one of the CLI's
    choices, so argparse exited 2 and every scheduled run failed *before*
    taking a snapshot — the repository had no recovery backups at all. Neither
    side can see that contract on its own (the CLI is right, the YAML parses),
    so it is pinned here.
    """

    root = Path(__file__).resolve().parents[1]

    def _workflow(self) -> str:
        return (self.root / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")

    def test_every_label_the_workflow_uses_is_accepted(self) -> None:
        used = set(re.findall(r"label=(\S+)", self._workflow()))
        self.assertTrue(used, "backup.yml no longer maps a run to a backup label")
        for label in sorted(used):
            self.assertIn(label, LABELS, f"create_backup.py rejects --label {label}")

    def test_every_scheduled_cron_maps_to_a_tier(self) -> None:
        text = self._workflow()
        crons = set(re.findall(r"^\s*-\s*cron:\s*[\"']([^\"']+)[\"']", text, re.MULTILINE))
        mapped = set(re.findall(r"^\s*'([^']+)'\)\s*label=", text, re.MULTILINE))
        self.assertTrue(crons, "backup.yml lost its schedule")
        self.assertEqual(crons, mapped, "a cron without a case branch snapshots as 'manual'")

    def test_the_cli_rejects_an_unknown_label(self) -> None:
        # The guard above only means something while the CLI stays strict.
        script = self.root / "scripts" / "backup" / "create_backup.py"
        result = subprocess.run(
            [sys.executable, str(script), "create", "--label", "scheduled", "--destination", "/tmp/omni-label-probe"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    import unittest

    unittest.main()
