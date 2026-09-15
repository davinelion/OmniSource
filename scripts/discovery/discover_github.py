#!/usr/bin/env python3
"""GitHub code-search discovery pass.

Searches github.com for feed filenames and ecosystem keywords
(``source.json``, ``apps.json``, ``altsource``, ``altstore source``,
``sidestore source``, ``feather source``) and merges the hits into
``data/discovered_sources.json``.

Usage
-----
    GITHUB_TOKEN=... python3 scripts/discovery/discover_github.py
    python3 scripts/discovery/discover_github.py --term "feather source" --dry-run
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
    parser.add_argument("--term", action="append", default=[], help="extra search term (repeatable)")
    parser.add_argument("--max-pages", type=int, default=2, help="result pages per term")
    parser.add_argument("--store", default=str(STORE), help="discovery store path")
    parser.add_argument("--dry-run", action="store_true", help="print hits, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    terms = tuple(dict.fromkeys((*autodiscovery.SEARCH_TERMS, *args.term)))
    found = autodiscovery.discover_from_github(token=token, terms=terms, max_pages=max(1, args.max_pages))
    if args.dry_run:
        for record in found:
            print(f"{record['url']}  [{record['meta'].get('term', '')}]")
        print(f"{len(found)} candidate(s) (dry run)")
        return 0
    store = autodiscovery.load_store(Path(args.store))
    merged = autodiscovery.merge_records(store.get("sources", []), found)
    autodiscovery.save_store(Path(args.store), merged, root=ROOT)
    print(f"github discovery: +{len(found)}, {len(merged)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
