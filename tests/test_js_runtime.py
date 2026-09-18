"""Run the JavaScript smoke tests that need a DOM.

``tests/test_website_shell.py`` asserts what the shipped markup and CSS say;
this module runs the shipped JavaScript. Each harness loads the real
``index.html`` and the real scripts in jsdom and drives them the way a reader
would:

* ``tests/js/drawer_smoke.cjs`` — the mobile drawer (``js/core.js``).
* ``tests/js/scroll_pin.cjs`` — the home page's scroll position while deferred
  feeds land (``js/core.js`` + ``js/site.js``).

jsdom is not a dependency of the repository - it comes from ``web/node_modules``
(the modern site's devDependencies), so the tests skip rather than fail when
the web toolchain has not been installed. That keeps ``python3 -m unittest
discover -s tests`` green on a bare checkout while giving real coverage
anywhere ``cd web && npm ci`` has run. Note that CI's validate job does install
it, so a skip there means the install step broke rather than that the behaviour
is untested.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSDOM = ROOT / "web" / "node_modules" / "jsdom"


class JsHarnessTests(unittest.TestCase):
    """Shared driver: run one ``tests/js/*.cjs`` harness and grade its report."""

    def run_harness(self, script: str, min_checks: int) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        if not JSDOM.is_dir():
            self.skipTest("jsdom is not installed (run `cd web && npm ci`)")

        env = dict(os.environ)
        env["NODE_PATH"] = str(ROOT / "web" / "node_modules")
        result = subprocess.run(
            [node, str(ROOT / "tests" / "js" / script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        try:
            # The script reports one JSON document (pretty printed for humans);
            # ``raw_decode`` finds it wherever jsdom's own noise ends.
            report, _ = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{") :])
        except (ValueError, json.JSONDecodeError):  # pragma: no cover - only on a crash
            self.fail(f"{script} produced no report:\n{result.stdout}\n{result.stderr}")

        failures = [entry for entry in report.get("results", []) if not entry.get("ok")]
        self.assertEqual(
            [],
            failures,
            f"{script} failed:\n" + "\n".join(f"  - {entry['name']}: {entry.get('detail', '')}" for entry in failures),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertGreaterEqual(
            report.get("checks", 0),
            min_checks,
            f"{script} stopped running its checks",
        )


class DrawerRuntimeTests(JsHarnessTests):
    def test_the_mobile_drawer_behaves(self) -> None:
        self.run_harness("drawer_smoke.cjs", 8)


class ScrollPinRuntimeTests(JsHarnessTests):
    def test_the_home_page_holds_its_scroll_position(self) -> None:
        self.run_harness("scroll_pin.cjs", 9)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
