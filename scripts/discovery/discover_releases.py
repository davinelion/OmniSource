#!/usr/bin/env python3
"""Release-repository discovery pass.

Inspects GitHub repositories for releases shipping ``.ipa``/``.tipa``
assets and merges the positive hits into ``data/discovered_sources.json``.

Usage
-----
    python3 scripts/discovery/discover_releases.py owner/repo [owner/repo ...]
    python3 scripts/discovery/discover_releases.py --file repos.txt --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource import autodiscovery

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "discovered_sources.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repos", nargs="*", help="owner/repo names")
    parser.add_argument("--file", default="", help="file with one owner/repo per line")
    parser.add_argument("--store", default=str(STORE), help="discovery store path")
    parser.add_argument("--dry-run", action="store_true", help="print hits, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repos = list(args.repos)
    if args.file:
        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        repos.extend(line.strip() for line in lines if line.strip() and not line.strip().startswith("#"))
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    found = autodiscovery.discover_releases(repos, token=token)
    if args.dry_run:
        for record in found:
            print(f"{record['url']}  tag={record['meta'].get('tag', '?')}")
        print(f"{len(found)} repo(s) with IPA releases (dry run)")
        return 0
    store = autodiscovery.load_store(Path(args.store))
    merged = autodiscovery.merge_records(store.get("sources", []), found)
    autodiscovery.save_store(Path(args.store), merged, root=ROOT)
    print(f"release discovery: +{len(found)}, {len(merged)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
