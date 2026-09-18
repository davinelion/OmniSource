#!/usr/bin/env python3
"""Build the static API v3 surface (``api/v3/*.json``).

Pre-renders every v3 endpoint from the pipeline feed documents; dynamic
semantics (query-string pagination / filtering / ETag) live in the
``web/`` API routes, which reuse :mod:`omnisource.api_v3`. Content-stable:
unchanged documents are not rewritten.

Usage
-----
    python3 scripts/build_api_v3.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS in sys.path:
    sys.path.remove(_SCRIPTS)
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from omnisource.api_v3 import build_static_documents
from omnisource.io import read_json, write_json_stable

ROOT = Path(__file__).resolve().parents[1]


def _doc(name: str) -> dict:
    data = read_json(ROOT / "feeds" / name)
    return data if isinstance(data, dict) else {}


def _releases_timeline(state: dict) -> list[dict]:
    timeline: list[dict] = []
    for slug, node in state.items():
        if not isinstance(node, dict) or not isinstance(node.get("versions"), list):
            continue
        for version in node["versions"][:5]:
            if not isinstance(version, dict):
                continue
            timeline.append(
                {
                    "app": slug,
                    "version": version.get("version", ""),
                    "date": version.get("date", ""),
                    "downloadURL": version.get("downloadURL", ""),
                    "size": version.get("size", 0),
                }
            )
    timeline.sort(key=lambda item: (item["date"], item["app"]), reverse=True)
    return timeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=str(ROOT / "api" / "v3"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    apps_doc = _doc("apps.json")
    sources_doc = _doc("sources.json")
    discovery_doc = _doc("discovery.json")
    state = read_json(ROOT / "feeds" / "state.json")
    security = read_json(ROOT / "data" / "security.json")
    bundle = {
        "apps": apps_doc.get("apps", []),
        "sources": sources_doc.get("sources", []),
        "trending": _doc("trending.json"),
        "search_index": _doc("search-index.json"),
        "status": _doc("status.json"),
        "security": security if isinstance(security, dict) else {},
        "analytics": _doc("analytics.json"),
        "releases": _releases_timeline(state if isinstance(state, dict) else {}),
        "generated_at": discovery_doc.get("generatedAt", ""),
    }
    documents = build_static_documents(bundle)
    out_dir = Path(args.out_dir)
    written, unchanged = 0, 0
    for name, doc in documents.items():
        if write_json_stable(out_dir / name, doc):
            written += 1
        else:
            unchanged += 1
    # Prune records of removed apps/sources so deletions propagate.
    # Recursive on purpose: before `safe_doc_id` flattened source ids, ids like
    # `Aidoku/Aidoku` wrote `sources/Aidoku/Aidoku.json`, so a depth-1 glob left
    # every stale source document behind and its directory with it.
    for subdir in ("apps", "sources"):
        target_dir = out_dir / subdir
        if not target_dir.is_dir():
            continue
        keep = {name for name in documents if name.startswith(f"{subdir}/")}
        for path in sorted(target_dir.rglob("*.json")):
            if path.relative_to(out_dir).as_posix() not in keep:
                path.unlink()
                written += 1
        for path in sorted((p for p in target_dir.rglob("*") if p.is_dir()), reverse=True):
            with contextlib.suppress(OSError):  # only succeeds once emptied above
                path.rmdir()
    print(f"api_v3: {len(documents)} document(s), {written} written, {unchanged} unchanged -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
