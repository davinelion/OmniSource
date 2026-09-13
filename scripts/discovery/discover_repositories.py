#!/usr/bin/env python3
"""Discover GitHub repositories that may publish sideloadable releases.

This pass complements code-search discovery: it searches repository metadata
for the ecosystem terms, inspects release metadata, and writes candidates to
the discovery store only.  Candidates still require validation and
quarantine; no repository is published from this command.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource import autodiscovery
from omnisource.io import read_json
from omnisource.quarantine import QuarantineStore, validate_candidates
from omnisource.remote_validation import validate_source_record

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "discovered_sources.json"
SEARCH_TERMS = (
    "altstore",
    "sidestore",
    "feather ipa",
    "esign ipa",
    "livecontainer ipa",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repos", nargs="*", help="explicit owner/repo values")
    parser.add_argument("--term", action="append", default=[], help="extra GitHub repository search term")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--store", default=str(STORE))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _search_repositories(term: str, *, token: str | None, max_pages: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in range(1, max(1, max_pages) + 1):
        query = urllib.parse.urlencode({"q": term, "per_page": 30, "page": page, "sort": "updated"})
        try:
            payload = autodiscovery.fetch_json(
                f"{autodiscovery.GITHUB_API}/search/repositories?{query}", token=token, max_bytes=2_000_000
            )
        except Exception:
            break
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name") or "")
            if not full_name:
                continue
            url = str(item.get("html_url") or f"https://github.com/{full_name}")
            records.append(
                autodiscovery.new_record(
                    url=url,
                    name=str(item.get("name") or full_name),
                    feed_type=autodiscovery.client_for_url(url),
                    extra={
                        "via": "github-repository-search",
                        "term": term,
                        "repository": full_name,
                        "default_branch": item.get("default_branch", "main"),
                        "archived": bool(item.get("archived")),
                        "stars": int(item.get("stargazers_count", 0) or 0),
                    },
                )
            )
        if len(items) < 30:
            break
    return records


def _explicit_records(repos: list[str]) -> list[dict[str, Any]]:
    records = []
    for repo in repos:
        if "/" not in repo or repo.startswith(("http://", "https://")):
            continue
        records.append(
            autodiscovery.new_record(
                url=f"https://github.com/{repo}",
                name=repo,
                feed_type=autodiscovery.client_for_url(repo),
                extra={"via": "explicit-repository", "repository": repo},
            )
        )
    return records


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    terms = tuple(dict.fromkeys((*SEARCH_TERMS, *args.term)))
    found = _explicit_records(args.repos)
    for term in terms:
        found.extend(_search_repositories(term, token=token, max_pages=args.max_pages))
    unique = {str(item.get("url")): item for item in found if item.get("url")}
    candidates = list(unique.values())
    store = QuarantineStore(Path(args.store).parent / "quarantine")
    result = validate_candidates(candidates, autodiscovery_validate, store)
    if args.dry_run:
        print(f"repository discovery: {len(result.accepted)} accepted, {len(result.quarantined)} quarantined (dry run)")
        return 0
    existing = read_json(Path(args.store))
    old = existing.get("sources", []) if isinstance(existing, dict) else []
    merged = autodiscovery.merge_records([item for item in old if isinstance(item, dict)], result.accepted)
    autodiscovery.save_store(Path(args.store), merged)
    print(
        f"repository discovery: {len(result.accepted)} accepted, "
        f"{len(result.quarantined)} quarantined, {len(merged)} total -> {args.store}"
    )
    return 0


def autodiscovery_validate(record: dict[str, Any]) -> list[str]:
    """Validate repository candidates without treating a repo page as a feed.

    The discovery store's publication gate (``scripts/validation/validate_source.py``)
    applies the full record schema, so a candidate accepted here has to satisfy
    it as well: this pass used to check only the URL, the name and the archived
    flag, which let it write records the very next workflow step rejected — and
    a rejected record failed ``discovery.yml`` before it could be committed or
    quarantined. The repository-specific rules stay on top of the shared schema.
    """
    errors = list(validate_source_record(record))
    url = str(record.get("url") or "")
    if not url.startswith("https://github.com/"):
        errors.append("repository URL must be an HTTPS GitHub URL")
    if not record.get("name"):
        errors.append("repository name is missing")
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    if meta.get("archived"):
        errors.append("repository is archived")
    return errors


if __name__ == "__main__":
    sys.exit(main())
