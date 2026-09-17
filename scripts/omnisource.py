#!/usr/bin/env python3
"""OmniSource feed builder (CLI wrapper).

Thin entry point over :mod:`omnisource.pipeline`. The package lives in
``src/omnisource``; this script puts ``src/`` on ``sys.path`` so a bare
``python3 scripts/omnisource.py`` keeps working without an install step.

Usage
-----
    python3 scripts/omnisource.py                 # full pipeline
    python3 scripts/omnisource.py --no-sync       # rebuild feeds from state.json
    python3 scripts/omnisource.py --no-health     # skip network link probing
    python3 scripts/omnisource.py --only ytlite   # restrict sync to one app
    python3 scripts/omnisource.py --incremental   # skip unchanged remotes
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS in sys.path:
    sys.path.remove(_SCRIPTS)
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from omnisource.cli import main


def _build_ipa_downloader() -> int:
    """Regenerate the direct-download index after a successful feed build."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_ipa_downloader import main as downloader_main

    try:
        return downloader_main([])
    finally:
        sys.path.remove(str(Path(__file__).resolve().parent))


if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0:
        downloader_code = _build_ipa_downloader()
        sys.exit(max(exit_code, downloader_code))
    sys.exit(exit_code)
