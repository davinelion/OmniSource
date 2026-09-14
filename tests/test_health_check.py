"""Tests for the download-link health check (scripts/health_check.py).

The checker is the only job that talks to a GitHub Issue, and it used to be
one-directional: it opened or appended to a broken-link tracker but never
closed one, so a link that upstream fixed left the repository's issue open
forever. These tests pin both directions of that lifecycle with ``gh`` stubbed
out, because the workflow runs unattended.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"
_SRC = ROOT / "src"
for _path in (str(_SCRIPTS), str(_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_SPEC = importlib.util.spec_from_file_location("health_check", _SCRIPTS / "health_check.py")
assert _SPEC and _SPEC.loader
HEALTH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(HEALTH)


class _Completed:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def _run_stub(number: str = ""):
    """Return a ``subprocess.run`` replacement and the list of calls it records."""
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if "list" in args and "issue" in args:
            return _Completed(number)
        return _Completed("")

    return fake_run, calls


class RenderReportTests(unittest.TestCase):
    def test_healthy_report_says_so(self) -> None:
        report = HEALTH.render_report([{"app": "A", "kind": "primary", "url": "https://x.test/a.ipa"}], [])
        self.assertIn("All download URLs are reachable", report)
        self.assertIn("**Broken:** 0", report)

    def test_broken_report_lists_every_failure(self) -> None:
        broken = [{"app": "A", "kind": "fallback", "url": "https://x.test/a.ipa", "detail": "HTTP 404"}]
        report = HEALTH.render_report([], broken)
        self.assertIn("HTTP 404", report)
        self.assertIn("fallbackDownloadURLs", report)


class IssueLifecycleTests(unittest.TestCase):
    def _env(self):
        return mock.patch.dict("os.environ", {"GH_TOKEN": "token"}, clear=False)

    def test_healthy_run_closes_the_open_tracker(self) -> None:
        fake_run, calls = _run_stub(number="33")
        with (
            self._env(),
            mock.patch.object(HEALTH.shutil, "which", return_value="/usr/bin/gh"),
            mock.patch.object(HEALTH.subprocess, "run", side_effect=fake_run),
        ):
            HEALTH.resolve_issue("## Download health check", repo="owner/repo", label="broken-link")

        comments = [call for call in calls if call[:3] == ["/usr/bin/gh", "issue", "comment"]]
        closes = [call for call in calls if call[:3] == ["/usr/bin/gh", "issue", "close"]]
        self.assertEqual(len(comments), 1, calls)
        self.assertEqual(len(closes), 1, calls)
        self.assertIn("33", comments[0])
        self.assertIn("33", closes[0])
        self.assertIn("completed", closes[0])

    def test_no_open_issue_is_a_no_op(self) -> None:
        fake_run, calls = _run_stub(number="")
        with (
            self._env(),
            mock.patch.object(HEALTH.shutil, "which", return_value="/usr/bin/gh"),
            mock.patch.object(HEALTH.subprocess, "run", side_effect=fake_run),
        ):
            HEALTH.resolve_issue("## Download health check", repo="owner/repo", label="broken-link")

        self.assertFalse([call for call in calls if "close" in call], calls)

    def test_broken_run_still_files_or_appends(self) -> None:
        fake_run, calls = _run_stub(number="")
        with (
            self._env(),
            mock.patch.object(HEALTH.shutil, "which", return_value="/usr/bin/gh"),
            mock.patch.object(HEALTH.subprocess, "run", side_effect=fake_run),
        ):
            HEALTH.report_issue("## Download health check", repo="owner/repo", label="broken-link", title="t")

        self.assertTrue([call for call in calls if call[:3] == ["/usr/bin/gh", "issue", "create"]], calls)
        self.assertFalse([call for call in calls if "close" in call], calls)

    def test_non_numeric_issue_output_is_ignored(self) -> None:
        fake_run, _calls = _run_stub(number="not-a-number")
        with (
            self._env(),
            mock.patch.object(HEALTH.shutil, "which", return_value="/usr/bin/gh"),
            mock.patch.object(HEALTH.subprocess, "run", side_effect=fake_run),
        ):
            self.assertEqual(HEALTH.open_issue_number("/usr/bin/gh", {"GH_TOKEN": "t"}, repo="o/r", label="l"), "")


class MainExitCodeTests(unittest.TestCase):
    def test_all_links_reachable_exits_zero_and_closes(self) -> None:
        targets = [{"app": "A", "kind": "primary", "url": "https://x.test/a.ipa"}]
        with (
            mock.patch.object(HEALTH, "iter_targets", return_value=targets),
            mock.patch.object(HEALTH, "probe_url", return_value=(True, "HTTP 200")),
            mock.patch.object(HEALTH, "resolve_issue") as resolve,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            code = HEALTH.main(["--report-issue", "--repo", "owner/repo"])

        self.assertEqual(code, 0)
        resolve.assert_called_once()

    def test_broken_links_without_report_issue_exit_one(self) -> None:
        targets = [{"app": "A", "kind": "primary", "url": "https://x.test/a.ipa"}]
        with (
            mock.patch.object(HEALTH, "iter_targets", return_value=targets),
            mock.patch.object(HEALTH, "probe_url", return_value=(False, "HTTP 404")),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(HEALTH.main([]), 1)


if __name__ == "__main__":
    unittest.main()
