#!/usr/bin/env python3
"""Feed-probing discovery pass.

Fetches candidate feed URLs (expanding bare site/repo URLs into likely
feed filenames), classifies the live ones by client family, and merges
them into ``data/discovered_sources.json``.

Usage
-----
    python3 scripts/discovery/discover_feeds.py https://example.com/apps.json
    python3 scripts/discovery/discover_feeds.py --file candidates.txt --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource import autodiscovery

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "discovered_sources.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="candidate feed or site URLs")
    parser.add_argument("--file", default="", help="file with one URL per line")
    parser.add_argument("--store", default=str(STORE), help="discovery store path")
    parser.add_argument("--timeout", type=int, default=20, help="per-request timeout in seconds")
    parser.add_argument("--dry-run", action="store_true", help="print live feeds, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    seeds = list(args.urls)
    if args.file:
        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        seeds.extend(line.strip() for line in lines if line.strip() and not line.strip().startswith("#"))
    candidates: list[str] = []
    for seed in seeds:
        for url in autodiscovery.candidate_feed_urls(seed):
            if url not in candidates:
                candidates.append(url)
    found = autodiscovery.discover_feeds(candidates, timeout=max(5, args.timeout))
    if args.dry_run:
        for record in found:
            print(f"{record['url']}  [{record['type']}] {record['meta'].get('apps', 0)} apps")
        print(f"{len(found)} live feed(s) (dry run)")
        return 0
    store = autodiscovery.load_store(Path(args.store))
    merged = autodiscovery.merge_records(store.get("sources", []), found)
    autodiscovery.save_store(Path(args.store), merged, root=ROOT)
    print(f"feed discovery: +{len(found)}, {len(merged)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
