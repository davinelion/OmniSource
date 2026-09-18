#!/usr/bin/env python3
"""Verify that generated artifacts are reproducible from committed state.

The offline build (``python3 scripts/omnisource.py --no-sync --no-health``)
must not change anything that is committed. This script snapshots the
generated outputs (feeds JSON/XML, app pages, README blocks, ``/apps.json``
and the API mirror), runs the offline build, snapshots again and compares.

Volatile values that are correct to refresh on every build are normalized
before comparing — the build/sync date (``generatedAt``, ``lastSync``), the
README ``last sync`` line, the rolling analytics history (which legitimately
gains a new day's entry), the screenshot-mirror state
(``mirrored``/``size``/``sha256``/``thumbnailSize``), which depends on
whether *this* machine could reach the remote screenshot hosts. Any *other*
difference means a hand-edit or a bug in the generators and fails the check.
gzip twins are compared by the document they decode to, so the check does not
depend on the local zlib. Untracked generated files (under ``feeds/``,
``apps/``, ``api/`` or ``apps.json``) are also reported, because an artifact
that is not committed would silently diverge after deploy.

Because the generated set embeds date-derived *values* (recency scores,
trending/freshness windows — the build's clock, not just its stamps), the
offline rebuild is run with ``OMNISOURCE_TODAY`` pinned to the build date
recorded in the committed feeds: the rebuild then reproduces the committed
state as of its own build date, so a run after UTC midnight no longer scores
as drift. Every generator must read the date through
``omnisource.domain.today``/``today_date`` for this to hold — a direct
``date.today()`` anywhere shows up here as drift.


Usage
-----
    python3 scripts/check_reproducible.py          # exit 0 = reproducible
    python3 scripts/check_reproducible.py --diff   # print differing files
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The offline build may legitimately rewrite these; nothing else is checked.
# Two families of generated output are committed: the canonical files under
# feeds/ (plus app/compare/collection pages and README blocks) and the
# published root surface — /apps.json (the installable source URL) and the
# api/ mirror — which the repository root carries because GitHub Pages can
# serve this branch directly.
TRACKED_PATTERNS = (
    "feeds/*.json",
    "feeds/*.gz",
    "feeds/*.xml",
    # OmniStore Pro contract (generated into feeds/api/v2/ by the pipeline).
    "feeds/api/v2/*.json",
    "feeds/api/v2/apps/*.json",
    "apps/*/index.html",
    "compare/*/index.html",
    "collections/*/index.html",
    # Monitoring ledger: latest.json is deterministic (only generatedAt is
    # volatile, normalized below); history.json only gains a row when the
    # build state actually changes, and the "history" normalizer covers
    # that like the analytics history.
    "reports/*.json",
    "README.md",
    # Published root surface: /apps.json (the installable source URL),
    # the api/ mirror (including v2), sitemap, robots and the home page stats.
    "apps.json",
    "api/*.json",
    "api/*.gz",
    "api/v2/*.json",
    "api/v2/*.gz",
    "api/v2/apps/*.json",
    "api/v2/apps/*.gz",
    "api/*",
    "api/v2/*",
    "api/v2/apps/*",
    "robots.txt",
    "sitemap.xml",
    "index.html",
)
# state.json is runtime state (syncedAt, health history) and changes whenever
# the scheduler runs a real sync, so it is intentionally not compared.

# (pattern, replacement): values that are date-derived and therefore differ
# between two builds on different days without indicating drift.
NORMALIZERS = (
    (re.compile(rb'"generatedAt":\s*"[^"]*"'), b'"generatedAt": "<DATE>"'),
    (re.compile(rb'"lastSync":\s*"[^"]*"'), b'"lastSync": "<DATE>"'),
    (re.compile(rb"last sync \*\*[\d-]+\*\*"), b"last sync **<DATE>**"),
    (re.compile(rb"last sync \d{4}-\d{2}-\d{2}"), b"last sync <DATE>"),
    (re.compile(rb"<lastBuildDate>[^<]*</lastBuildDate>"), b"<lastBuildDate>DATE</lastBuildDate>"),
    (re.compile(rb"<lastmod>[^<]*</lastmod>"), b"<lastmod>DATE</lastmod>"),
    # Analytics history gains an entry per day; its dates are derived from the
    # snapshot date, not from content.
    (re.compile(rb'"history":\s*\[.*?\]', re.DOTALL), b'"history": [...]'),
)


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/*.json") or pattern.endswith("/*.gz") or pattern.endswith("/*.xml"):
        # e.g. feeds/*.json, api/v2/*.gz — direct children matching an extension.
        suffix = pattern[pattern.rfind("*.") :]
        ext = suffix[1:]  # ".json" / ".gz" / ".xml"
        prefix = pattern[: pattern.rfind("/")]
        return path.startswith(prefix + "/") and path.count("/") == prefix.count("/") + 1 and path.endswith(ext)
    if pattern.endswith("/*/index.html"):
        parts = path.split("/")
        return len(parts) == 3 and parts[0] in {"apps", "compare", "collections"} and parts[2] == "index.html"
    if pattern.endswith("/*"):
        # Direct children only (e.g. the api/ mirror including its gz twins).
        prefix = pattern[:-2]
        return path.startswith(prefix + "/") and path.count("/") == prefix.count("/") + 1
    if "*" in pattern and "/" not in pattern:
        # Root-level glob (e.g. the flat feed copies at the repository root).
        return "/" not in path and fnmatch.fnmatch(path, pattern)
    return path == pattern


def _norm(data: bytes) -> bytes:
    for pattern, replacement in NORMALIZERS:
        data = pattern.sub(replacement, data)
    return data


def _norm_screenshots(data: bytes) -> bytes:
    """Blank the network-dependent mirror state in ``feeds/screenshots.json``.

    Whether a screenshot could be mirrored depends on the build machine's
    reachability to third-party hosts (a fresh CI runner downloads, an
    offline or sandboxed build cannot), so ``mirrored``/``size``/``sha256``
    /``thumbnailSize`` are environment state, not generated content. The
    URLs and thumbnail metadata stay fully checked.
    """
    try:
        doc = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return data
    if not isinstance(doc, dict):
        return data
    for entry in doc.get("screenshots", []):
        if isinstance(entry, dict):
            for key in ("mirrored", "size", "sha256", "thumbnailSize"):
                entry.pop(key, None)
    return json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")


def _is_screenshots_doc(name: str) -> bool:
    """True for ``feeds/screenshots.json`` and its published ``.gz`` twin.

    Both carry the same document, so both need the mirror-state normalizer;
    only normalizing the canonical file would let the environment-dependent
    ``mirrored``/``size``/``sha256``/``thumbnailSize`` values leak into the
    comparison through ``api/screenshots.json.gz``.
    """
    return name.startswith("screenshots.json")


def _snapshot() -> dict[str, bytes]:
    """Map path -> normalized hash for every generated file on disk."""
    snapshot: dict[str, bytes] = {}
    for pattern in TRACKED_PATTERNS:
        star = pattern.find("*")
        prefix = pattern[:star] if star >= 0 else ""
        glob = pattern[star:] if star >= 0 else pattern
        for path in (ROOT / prefix).glob(glob):
            if path.is_file() and path.name != "state.json":
                data = _norm(path.read_bytes())
                if path.suffix == ".gz":
                    # gzip twins: compare the document they decode to, so the
                    # check does not depend on the local zlib's deflate output.
                    with contextlib.suppress(OSError):
                        data = _norm(gzip.decompress(data))
                if _is_screenshots_doc(path.name):
                    data = _norm_screenshots(data)
                snapshot[str(path.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
    return snapshot


def _untracked_generated() -> list[str]:
    listed = (
        subprocess.run(
            [shutil.which("git") or "git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
        .stdout.decode("utf-8")
        .splitlines()
    )
    return [rel for rel in listed if any(_matches(rel, p) for p in TRACKED_PATTERNS)]


_COMMITTED_DATE_RE = re.compile(r'"generatedAt":\s*"(?P<date>\d{4}-\d{2}-\d{2})"')


def _committed_build_date() -> str | None:
    """The build date the committed feeds were generated for.

    The generated set embeds date-derived *values* (recency scores, trending
    and freshness windows), not just date stamps, so a rebuild on a later day
    can never byte-match — that is clock movement, not drift. The offline
    rebuild is therefore pinned to the build date via ``OMNISOURCE_TODAY``,
    reproducing the committed state as of its own build date.

    The date is the *most common* ``generatedAt`` across the feeds, not the
    newest: a build that straddles UTC midnight (or a single feed refreshed
    hours later) leaves one straggler stamp that must not drag the whole
    rebuild onto the wrong day. Ties resolve to the later date.
    """
    dates: list[str] = []
    feeds = ROOT / "feeds"
    if feeds.is_dir():
        for path in feeds.glob("*.json"):
            with contextlib.suppress(OSError, UnicodeDecodeError):
                dates.extend(_COMMITTED_DATE_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    if not dates:
        return None
    counts: dict[str, int] = {}
    for date in dates:
        counts[date] = counts.get(date, 0) + 1
    return max(counts, key=lambda date: (counts[date], date))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--diff", action="store_true", help="print paths that differ")
    args = parser.parse_args(argv)

    before = _snapshot()
    if not before:
        print("check_reproducible: no generated files found", file=sys.stderr)
        return 2

    env = dict(os.environ)
    pinned = _committed_build_date()
    if pinned:
        # Rebuild as of the committed build date (see _committed_build_date).
        env["OMNISOURCE_TODAY"] = pinned
        print(f"check_reproducible: rebuilding with the committed build date ({pinned})")

    result = subprocess.run(
        [shutil.which("python3") or "python3", "scripts/omnisource.py", "--no-sync", "--no-health"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        print("check_reproducible: offline build failed", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return 3

    after = _snapshot()
    differing = sorted(rel for rel in before if before[rel] != after.get(rel))
    untracked = _untracked_generated()

    if differing or untracked:
        if differing:
            print(f"check_reproducible: {len(differing)} generated file(s) changed on rebuild:")
            for rel in differing:
                print(f"  {rel}")
        if untracked:
            print(f"check_reproducible: {len(untracked)} untracked generated file(s):")
            for rel in untracked:
                print(f"  {rel}")
            print("  (commit them or remove them; generated files must not be hand-edited)")
        if args.diff:
            subprocess.run(
                [shutil.which("git") or "git", "-C", str(ROOT), "diff", "--stat", "--", *differing],
                check=False,
            )
        return 1

    print(f"check_reproducible: {len(before)} generated file(s) stable after offline rebuild")
    return 0


if __name__ == "__main__":
    sys.exit(main())
