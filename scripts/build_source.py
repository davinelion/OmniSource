#!/usr/bin/env python3
"""Inspect, plan and integrity-check the source-build recipes.

Thin wrapper over :mod:`omnisource.source_builds`. Recipes live in
``data/source_builds.json`` and cover iOS projects whose upstream publishes
source but no artifact this project could attribute and verify.

Usage
-----
    python3 scripts/build_source.py list
    python3 scripts/build_source.py check
    python3 scripts/build_source.py plan trollvnc
    python3 scripts/build_source.py fetch trollvnc      # download + verify digest
    python3 scripts/build_source.py verify trollvnc --archive path/to.tar.gz

Exit code 0 = clean, 1 = a recipe problem or a digest mismatch, 2 = unusable file.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from omnisource.source_builds import main

if __name__ == "__main__":
    sys.exit(main())
