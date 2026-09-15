#!/usr/bin/env python3
"""Orchestrate every discovery pass and merge results into the store.

Runs the GitHub code-search, feed-probing, release-scan and web-catalog
passes (each skippable), validates every candidate with the validation
engine, and merges the survivors into ``data/discovered_sources.json``.

Scheduled every 12 hours by ``.github/workflows/discovery.yml``.

Usage
-----
    python3 scripts/discovery/discover_sources.py
    python3 scripts/discovery/discover_sources.py --skip-github --dry-run
    python3 scripts/discovery/discover_sources.py --feeds feeds.txt --pages pages.txt --repos repos.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource import autodiscovery
from omnisource.quarantine import QuarantineStore, validate_candidates
from omnisource.remote_validation import validate_source_record
from omnisource.source_policy import decide, load_policy, partition_by_policy

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "discovered_sources.json"


def _read_list(path: str | None) -> list[str]:
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default=str(STORE), help="discovery store path")
    parser.add_argument("--feeds", default="", help="file with candidate feed URLs (one per line)")
    parser.add_argument("--pages", default="", help="file with catalog page URLs (one per line)")
    parser.add_argument("--repos", default="", help="file with owner/repo names (one per line)")
    parser.add_argument("--skip-github", action="store_true", help="skip the GitHub code-search pass")
    parser.add_argument("--max-pages", type=int, default=2, help="code-search pages per term")
    parser.add_argument("--dry-run", action="store_true", help="print what would be stored, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    found: list[dict] = []
    if not args.skip_github:
        found.extend(autodiscovery.discover_from_github(token=token, max_pages=max(1, args.max_pages)))
    feed_urls = _read_list(args.feeds)
    for base in feed_urls:
        for candidate in autodiscovery.candidate_feed_urls(base):
            if candidate not in feed_urls:
                feed_urls.append(candidate)
    if feed_urls:
        found.extend(autodiscovery.discover_feeds(feed_urls))
    repos = _read_list(args.repos)
    if repos:
        found.extend(autodiscovery.discover_releases(repos, token=token))
    pages = _read_list(args.pages)
    if pages:
        found.extend(autodiscovery.discover_web_catalogs(pages))

    # Sourcing verdicts first: a host that has already been reviewed and
    # rejected (data/source_policy.json) is dropped instead of being validated,
    # re-validated and re-dropped on every run. They are *not* quarantined —
    # quarantine means "malformed, look at this", which is a different signal.
    policy = load_policy(ROOT)
    candidates, blocked = partition_by_policy(found, policy)
    for record in blocked:
        decision = decide(str(record.get("url") or ""), name=str(record.get("name") or ""), policy=policy)
        print(f"discovery: excluded {record.get('url')} — {decision.rule_id}")

    quarantine = QuarantineStore(ROOT / "data" / "quarantine")
    result = validate_candidates(candidates, validate_source_record, quarantine)
    valid, quarantined = result.accepted, result.quarantined

    store_path = Path(args.store)
    if args.dry_run:
        print(
            f"discovery: {len(valid)} validated, {len(quarantined)} quarantined, {len(blocked)} excluded by "
            "policy (dry run, stores untouched)"
        )
        return 0
    store = autodiscovery.load_store(store_path)
    # Only structurally valid candidates enter the discovery registry. Invalid
    # payloads are isolated in data/quarantine/sources.json and cannot reach
    # feed generation through an accidental merge.
    stored, stale = partition_by_policy(store.get("sources", []), policy)
    for record in stale:
        print(f"discovery: pruned {record.get('url')} from the store — excluded by policy")
    merged = autodiscovery.merge_records(stored, valid)
    autodiscovery.save_store(store_path, merged, root=ROOT)
    print(
        f"discovery: +{len(valid)} validated, +{len(quarantined)} quarantined, {len(blocked)} excluded by policy, "
        f"{len(stale)} pruned from store, {len(merged)} candidates in {store_path}; quarantine={quarantine.path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
