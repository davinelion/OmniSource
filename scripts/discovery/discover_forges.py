#!/usr/bin/env python3
"""Discover iOS release projects on GitLab, Codeberg and Forgejo.

This complements GitHub discovery by finding projects whose release APIs expose
IPA/TIPA assets. Results are candidates only: validation, quarantine and the
normal catalog review gate still run before anything is published.

Examples:
    python3 scripts/discovery/discover_forges.py
    python3 scripts/discovery/discover_forges.py --provider gitlab --dry-run
    FORGEJO_HOSTS=https://forge.example python3 scripts/discovery/discover_forges.py
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
TERMS = ("altstore", "sidestore", "feather ipa", "ios ipa", "sideload ipa")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("all", "gitlab", "codeberg", "forgejo"), default="all")
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--store", default=str(STORE))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _get(url: str, *, token: str | None = None) -> Any:
    try:
        return autodiscovery.fetch_json(url, token=token, max_bytes=4_000_000)
    except Exception:
        return None


def _has_installable(releases: Any) -> tuple[bool, str]:
    if not isinstance(releases, list):
        return False, ""
    for release in releases:
        if not isinstance(release, dict):
            continue
        assets = release.get("assets")
        links = assets.get("links", []) if isinstance(assets, dict) else assets
        for asset in links or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if name.lower().endswith((".ipa", ".tipa")):
                return True, str(release.get("tag_name") or release.get("tag_name") or "")
    return False, ""


def _record(*, provider: str, project: dict[str, Any], host: str, tag: str, term: str) -> dict[str, Any] | None:
    url = str(project.get("web_url") or project.get("html_url") or "")
    name = str(project.get("name") or project.get("path") or "")
    repo = str(project.get("path_with_namespace") or project.get("full_name") or "")
    if not url or not name or not repo:
        return None
    return autodiscovery.new_record(
        url=url,
        name=name,
        feed_type="unknown",
        health=autodiscovery.HEALTH_ONLINE,
        extra={
            "via": f"{provider}-release-discovery",
            "provider": provider,
            "host": host,
            "repository": repo,
            "term": term,
            "latest_tag": tag,
            "ipa_release": True,
            "project_description": str(project.get("description") or ""),
        },
    )


def _gitlab(host: str, terms: tuple[str, ...], pages: int, token: str | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for term in terms:
        for page in range(1, max(1, pages) + 1):
            query = urllib.parse.urlencode({"search": term, "simple": "false", "per_page": 50, "page": page})
            projects = _get(f"{host.rstrip('/')}/api/v4/projects?{query}", token=token)
            if not isinstance(projects, list) or not projects:
                break
            for project in projects:
                if not isinstance(project, dict) or project.get("archived"):
                    continue
                project_id = project.get("id")
                if project_id is None:
                    continue
                project_path = urllib.parse.quote(str(project_id), safe="")
                releases = _get(
                    f"{host.rstrip('/')}/api/v4/projects/{project_path}/releases?per_page=20",
                    token=token,
                )
                ok, tag = _has_installable(releases)
                if ok:
                    item = _record(provider="gitlab", project=project, host=host, tag=tag, term=term)
                    if item:
                        found.append(item)
            if len(projects) < 50:
                break
    return found


def _gitea(host: str, terms: tuple[str, ...], pages: int, provider: str, token: str | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for term in terms:
        for page in range(1, max(1, pages) + 1):
            query = urllib.parse.urlencode({"q": term, "limit": 50, "page": page})
            payload = _get(f"{host.rstrip('/')}/api/v1/repos/search?{query}", token=token)
            projects = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(projects, list) or not projects:
                break
            for project in projects:
                if not isinstance(project, dict) or project.get("archived"):
                    continue
                full_name = str(project.get("full_name") or "")
                if not full_name:
                    continue
                releases = _get(f"{host.rstrip('/')}/api/v1/repos/{full_name}/releases?limit=20", token=token)
                ok, tag = _has_installable(releases)
                if ok:
                    item = _record(provider=provider, project=project, host=host, tag=tag, term=term)
                    if item:
                        found.append(item)
            if len(projects) < 50:
                break
    return found


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    terms = tuple(dict.fromkeys((*TERMS, *args.term)))
    found: list[dict[str, Any]] = []
    if args.provider in ("all", "gitlab"):
        found.extend(_gitlab("https://gitlab.com", terms, args.max_pages, token))
    if args.provider in ("all", "codeberg"):
        found.extend(_gitea("https://codeberg.org", terms, args.max_pages, "codeberg", token))
    if args.provider in ("all", "forgejo"):
        hosts = [item.strip().rstrip("/") for item in os.environ.get("FORGEJO_HOSTS", "").split(",") if item.strip()]
        for host in hosts:
            found.extend(_gitea(host, terms, args.max_pages, "forgejo", token))

    unique = {str(item.get("url")): item for item in found if item.get("url")}
    quarantine = QuarantineStore(Path(args.store).parent / "quarantine")
    result = validate_candidates(list(unique.values()), validate_source_record, quarantine)
    if args.dry_run:
        print(f"forge discovery: {len(result.accepted)} accepted, {len(result.quarantined)} quarantined (dry run)")
        return 0
    existing = read_json(Path(args.store))
    old = existing.get("sources", []) if isinstance(existing, dict) else []
    merged = autodiscovery.merge_records([item for item in old if isinstance(item, dict)], result.accepted)
    autodiscovery.save_store(Path(args.store), merged, root=ROOT)
    print(
        f"forge discovery: {len(result.accepted)} accepted, {len(result.quarantined)} quarantined, {len(merged)} total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
