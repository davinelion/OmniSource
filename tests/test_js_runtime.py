"""Run the JavaScript smoke tests that need a DOM.

``tests/test_website_shell.py`` asserts what the shipped markup and CSS say;
this module runs the shipped JavaScript. The drawer test
(``tests/js/drawer_smoke.cjs``) loads the real ``index.html`` and the real
``js/core.js`` in jsdom and drives the drawer as a reader would.

jsdom is not a dependency of the repository - it comes from ``web/node_modules``
(the modern site's devDependencies), so the test skips rather than fails when
the web toolchain has not been installed. That keeps ``python3 -m unittest
discover -s tests`` green on a bare checkout (CI's validate job) while giving
real coverage anywhere ``cd web && npm ci`` has run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "drawer_smoke.cjs"
JSDOM = ROOT / "web" / "node_modules" / "jsdom"


class DrawerRuntimeTests(unittest.TestCase):
    def test_the_mobile_drawer_behaves(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        if not JSDOM.is_dir():
            self.skipTest("jsdom is not installed (run `cd web && npm ci`)")

        env = dict(os.environ)
        env["NODE_PATH"] = str(ROOT / "web" / "node_modules")
        result = subprocess.run(
            [node, str(SCRIPT)],
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
            self.fail(f"the drawer smoke test produced no report:\n{result.stdout}\n{result.stderr}")

        failures = [entry for entry in report.get("results", []) if not entry.get("ok")]
        self.assertEqual(
            [],
            failures,
            "the drawer smoke test failed:\n"
            + "\n".join(f"  - {entry['name']}: {entry.get('detail', '')}" for entry in failures),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertGreaterEqual(report.get("checks", 0), 8, "the smoke test stopped running its checks")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
