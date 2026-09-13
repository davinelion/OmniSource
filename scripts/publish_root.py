#!/usr/bin/env python3
"""Publish the generated public URLs that must live at the repository root.

The canonical home of every generated feed is ``feeds/`` (and the machine API
surface under ``api/``); the repository root stays clean. The only feed
mirrored at the root is ``/apps.json`` — the URL installers register — which
GitHub Pages serves from this branch:

    /apps.json            (byte-identical to feeds/apps.json)
    /api/*.json + .gz     /api/<route>   /api/index.json
    /sitemap.xml          /robots.txt    /.nojekyll

Every copy is byte-identical to its ``feeds/`` original (``check_reproducible.py``
fails the build if one drifts), and files that are no longer generated are
removed from the mirror. The full historical flat URL family (``/<slug>.json``,
``/<slug>.xml``, ``/feed.xml``, …) is still assembled into the ``_site/``
artifact by ``scripts/build_site.py`` for the GitHub Actions deployment.

The sync pipeline already calls this (see :func:`omnisource.pipeline.run`), so
scheduled builds keep the root in step. Run it directly after a manual edit to
``feeds/`` — e.g. the ``merge.yml`` safety net — or to repair the mirror:

    python3 scripts/publish_root.py
    python3 scripts/publish_root.py --check   # fail if the mirror is stale
"""

from __future__ import annotations

import argparse
import filecmp
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS in sys.path:
    sys.path.remove(_SCRIPTS)
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from omnisource.site import (
    API_DOCUMENTS,
    API_ROUTES,
    API_V2_ALIASES,
    API_V2_APPS_ROUTE,
    API_V2_CONTRACT,
    API_V2_STANDALONE,
    ROOT_EXTERNALLY_PUBLISHED,
    ROOT_GENERATED_FILES,
    ROOT_HAND_MAINTAINED,
    publish_repo_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def find_stale_mirror_files() -> list[str]:
    """Root/API mirror files that are missing, stale or no longer generated."""
    feeds = REPO_ROOT / "feeds"
    stale: list[str] = []

    def differs(mirror: Path, source: Path) -> bool:
        return not mirror.exists() or not filecmp.cmp(mirror, source, shallow=False)

    # /apps.json is the only feed mirrored at the repository root (the
    # installable source URL); everything else lives under feeds/ and api/.
    if differs(REPO_ROOT / "apps.json", feeds / "apps.json"):
        stale.append("apps.json")

    # A root JSON/XML the publisher no longer owns is stale: the publisher
    # prunes it on the next run. The ownership sets come from site.py itself so
    # this report and the prune can never disagree again — that drift is what
    # made a build delete the root security report (a machine-readable
    # publication artifact the security workflow maintains alongside
    # data/security.json) and sync.yml commit the deletion.
    owned = {*ROOT_GENERATED_FILES, *ROOT_HAND_MAINTAINED, *ROOT_EXTERNALLY_PUBLISHED}
    for path in sorted(REPO_ROOT.glob("*")):
        if path.is_file() and path.suffix.lower() in {".json", ".xml"} and path.name not in owned:
            stale.append(f"{path.name} (stale root copy)")

    # API mirror: every documented endpoint must exist and be byte-identical to
    # the canonical feed it mirrors (api/catalog.json + .min point at discovery).
    api_expected = {name: name for name in API_DOCUMENTS}
    api_expected.update({"catalog.json": "discovery.json", "catalog.min.json": "discovery.json"})
    api_expected.update(dict(API_ROUTES))
    api_expected["index.json"] = ""
    for name, source_name in sorted(api_expected.items()):
        target = REPO_ROOT / "api" / name
        if not source_name:
            if not target.exists():
                stale.append(f"api/{name}")
            continue
        source = feeds / source_name
        if name == "catalog.min.json":
            if not target.exists():
                stale.append("api/catalog.min.json")
            continue
        if source.exists() and differs(target, source):
            stale.append(f"api/{name}")

    # V2 API mirror: aliases + standalone docs + the generated OmniStore Pro
    # contract (feeds/api/v2/*) plus one per-app record per catalog slug.
    v2_dir = REPO_ROOT / "api" / "v2"
    for v2_name, feeds_name in sorted(API_V2_ALIASES.items()):
        source = feeds / feeds_name
        if source.exists() and differs(v2_dir / v2_name, source):
            stale.append(f"api/v2/{v2_name}")
    for name in sorted(API_V2_STANDALONE):
        if name == "index.json":
            if not (v2_dir / name).exists():
                stale.append("api/v2/index.json")
            continue
        if not (v2_dir / name).exists():
            stale.append(f"api/v2/{name}")
    contract_src = feeds / "api" / "v2"
    for name in sorted(API_V2_CONTRACT):
        source = contract_src / name
        if source.exists() and differs(v2_dir / name, source):
            stale.append(f"api/v2/{name}")
    try:
        import json as _json

        catalog = _json.loads((REPO_ROOT / "catalog.json").read_text(encoding="utf-8"))
        slugs = [str(item.get("slug")) for item in catalog.get("apps", []) if item.get("slug")]
    except (OSError, ValueError):
        slugs = []
    for slug in sorted(slugs):
        source = contract_src / API_V2_APPS_ROUTE / f"{slug}.json"
        if source.exists() and differs(v2_dir / API_V2_APPS_ROUTE / f"{slug}.json", source):
            stale.append(f"api/v2/{API_V2_APPS_ROUTE}/{slug}.json")

    for name in ("sitemap.xml", "robots.txt", ".nojekyll"):
        if not (REPO_ROOT / name).exists():
            stale.append(name)
    return sorted(set(stale))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift instead of writing")
    args = parser.parse_args(argv)

    if args.check:
        stale = find_stale_mirror_files()
        if stale:
            print(f"publish_root: {len(stale)} root artifact(s) missing or stale:", file=sys.stderr)
            for name in stale:
                print(f"  {name}", file=sys.stderr)
            print("  run: python3 scripts/publish_root.py", file=sys.stderr)
            return 1
        print("publish_root: repository root mirrors feeds/ byte-for-byte")
        return 0

    summary = publish_repo_artifacts(REPO_ROOT)
    print(
        f"publish_root: /apps.json ({summary['flat_files']} flat feed URL), "
        f"{summary['api_documents']} API document(s) (+{summary['api_gz_files']} gz), "
        f"sitemap + robots + .nojekyll; {len(summary['written'])} file(s) refreshed, "
        f"{len(summary['removed'])} stale file(s) removed"
    )
    for name in summary["removed"]:
        print(f"  removed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
