"""Monitoring reports: deterministic snapshots + a trimmed rolling history."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from omnisource.reports import HISTORY_LIMIT, append_history, build_report, history_row, write_reports


def make_health():
    return {
        "generatedAt": "2026-09-11",
        "totals": {"apps": 3, "reachable": 2, "unreachable": 1},
        "apps": [
            {"slug": "ok-one", "name": "OK One", "downloadReachable": True},
            {"slug": "ok-two", "name": "OK Two", "downloadReachable": True},
            {"slug": "down-app", "name": "Down App", "downloadReachable": False},
        ],
    }


def make_analytics():
    return {"totals": {"verifiedApps": 2}}


def make_sync_report():
    return SimpleNamespace(
        apps_synced=3,
        apps_incremental_hit=1,
        apps_updated=1,
        apps_failed=0,
        api_requests=12,
        updates=[
            SimpleNamespace(
                app_id="ok-one",
                name="OK One",
                version="2.0",
                previous_version="1.0",
                kind="updated",
            )
        ],
        errors=["boom"],
    )


def make_idle_sync_report():
    """The report of a build that never contacted an upstream (--no-sync)."""
    return SimpleNamespace(
        apps_synced=0,
        apps_incremental_hit=0,
        apps_updated=0,
        apps_failed=0,
        api_requests=0,
        updates=[],
        errors=[],
    )


class TestBuildReport(unittest.TestCase):
    def test_snapshot_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            feeds = Path(tmp)
            (feeds / "api" / "v2").mkdir(parents=True)
            (feeds / "api" / "v2" / "manifest.json").write_text(json.dumps({"feedVersion": "abc123"}), encoding="utf-8")
            (feeds / "translation-status.json").write_text(json.dumps({"en": 100, "es": 100}), encoding="utf-8")
            report = build_report(
                health_doc=make_health(),
                analytics_doc=make_analytics(),
                sync_report=make_sync_report(),
                feeds_dir=feeds,
            )
        self.assertEqual(report["generatedAt"], "2026-09-11")
        self.assertEqual(report["feedVersion"], "abc123")
        self.assertEqual(
            report["apps"],
            {"total": 3, "reachable": 2, "unreachable": 1, "verified": 2},
        )
        self.assertEqual(
            report["sync"],
            {"synced": 3, "incrementalHits": 1, "updated": 1, "failed": 0, "apiRequests": 12},
        )
        self.assertEqual(report["unreachable"], ["down-app"])
        self.assertEqual(
            report["updates"],
            [
                {
                    "slug": "ok-one",
                    "name": "OK One",
                    "previousVersion": "1.0",
                    "version": "2.0",
                    "kind": "updated",
                }
            ],
        )
        self.assertEqual(report["errors"], ["boom"])
        self.assertEqual(report["translations"], {"en": 100, "es": 100})

    def test_missing_sidecar_docs_degrade_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_report(
                health_doc=make_health(),
                analytics_doc=make_analytics(),
                sync_report=make_sync_report(),
                feeds_dir=Path(tmp),
            )
        self.assertIsNone(report["feedVersion"])
        self.assertEqual(report["translations"], {})

    def test_history_row_is_a_stable_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_report(
                health_doc=make_health(),
                analytics_doc=make_analytics(),
                sync_report=make_sync_report(),
                feeds_dir=Path(tmp),
            )
        self.assertEqual(
            history_row(report),
            {
                "generatedAt": "2026-09-11",
                "apps": 3,
                "reachable": 2,
                "verified": 2,
                "updated": 1,
                "failed": 0,
                "errors": 1,
            },
        )


class TestHistoryLedger(unittest.TestCase):
    def test_append_trims_to_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            rows = [{"generatedAt": f"day-{i}"} for i in range(HISTORY_LIMIT + 5)]
            path.write_text(json.dumps({"history": rows}), encoding="utf-8")
            with tempfile.TemporaryDirectory() as feeds:
                report = build_report(
                    health_doc=make_health(),
                    analytics_doc=make_analytics(),
                    sync_report=make_sync_report(),
                    feeds_dir=Path(feeds),
                )
            doc = append_history(path, report)
        self.assertEqual(len(doc["history"]), HISTORY_LIMIT)
        self.assertEqual(doc["history"][-1]["generatedAt"], "2026-09-11")

    def test_corrupt_history_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text("not json{", encoding="utf-8")
            with tempfile.TemporaryDirectory() as feeds:
                report = build_report(
                    health_doc=make_health(),
                    analytics_doc=make_analytics(),
                    sync_report=make_sync_report(),
                    feeds_dir=Path(feeds),
                )
            doc = append_history(path, report)
        self.assertEqual(len(doc["history"]), 1)

    def test_identical_rebuild_appends_no_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            with tempfile.TemporaryDirectory() as feeds:
                report = build_report(
                    health_doc=make_health(),
                    analytics_doc=make_analytics(),
                    sync_report=make_sync_report(),
                    feeds_dir=Path(feeds),
                )
            first = append_history(path, report)
            path.write_text(json.dumps(first), encoding="utf-8")
            second = append_history(path, report)
        self.assertEqual(first, second)


class TestWriteReports(unittest.TestCase):
    def test_writes_both_files_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feeds = root / "feeds"
            (feeds / "api" / "v2").mkdir(parents=True)
            (feeds / "api" / "v2" / "manifest.json").write_text(json.dumps({"feedVersion": "abc123"}), encoding="utf-8")
            kwargs = {
                "root": root,
                "feeds_dir": feeds,
                "health_doc": make_health(),
                "analytics_doc": make_analytics(),
                "sync_report": make_sync_report(),
            }
            first = write_reports(**kwargs)
            self.assertEqual(len(first), 2)
            self.assertTrue((root / "reports" / "latest.json").exists())
            # A rebuild from identical inputs is a byte-level no-op.
            before = {name: (root / "reports" / name).read_bytes() for name in ("latest.json", "history.json")}
            self.assertEqual(write_reports(**kwargs), [])
            after = {name: (root / "reports" / name).read_bytes() for name in ("latest.json", "history.json")}
            self.assertEqual(before, after)

    def _prepared_root(self, tmp: str) -> tuple[Path, Path, dict]:
        root = Path(tmp)
        feeds = root / "feeds"
        (feeds / "api" / "v2").mkdir(parents=True)
        (feeds / "api" / "v2" / "manifest.json").write_text(json.dumps({"feedVersion": "abc123"}), encoding="utf-8")
        base = {"root": root, "feeds_dir": feeds, "health_doc": make_health(), "analytics_doc": make_analytics()}
        return root, feeds, base

    def test_offline_rebuild_carries_the_last_sync_forward(self):
        # A rebuild (--no-sync) has no telemetry of its own. Zeroing the
        # run-scoped blocks is what made scripts/check_reproducible.py fail
        # after every scheduled sync — and erased what that sync had done.
        with tempfile.TemporaryDirectory() as tmp:
            root, _feeds, base = self._prepared_root(tmp)
            write_reports(sync_report=make_sync_report(), **base)
            after_sync = {name: (root / "reports" / name).read_bytes() for name in ("latest.json", "history.json")}

            changed = write_reports(sync_report=make_idle_sync_report(), sync_ran=False, **base)

            self.assertEqual(changed, [], "an offline rebuild must not touch the monitoring ledger")
            after_rebuild = {name: (root / "reports" / name).read_bytes() for name in ("latest.json", "history.json")}
            self.assertEqual(after_sync, after_rebuild)
            report = json.loads((root / "reports" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report["sync"]["apiRequests"], 12)
            self.assertEqual(report["errors"], ["boom"])
            self.assertEqual(len(report["updates"]), 1)
            history = json.loads((root / "reports" / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history["history"]), 1, "a rebuild must not append a fake zero row")

    def test_a_real_sync_still_reports_its_own_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _feeds, base = self._prepared_root(tmp)
            write_reports(sync_report=make_sync_report(), **base)
            write_reports(sync_report=make_idle_sync_report(), **base)
            report = json.loads((root / "reports" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["sync"]["apiRequests"], 0)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["updates"], [])

    def test_carry_forward_without_a_previous_report_degrades_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _feeds, base = self._prepared_root(tmp)
            write_reports(sync_report=make_idle_sync_report(), sync_ran=False, **base)
            report = json.loads((root / "reports" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["sync"]["synced"], 0)
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()
