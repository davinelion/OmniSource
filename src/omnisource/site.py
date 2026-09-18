"""Static site assembler and publisher for GitHub Pages.

The site is published twice from one set of sources, because GitHub Pages
supports two deployment modes and the repository must work under either:

* ``build_site()`` assembles the complete deployable site into ``_site/``,
  the artifact ``sync.yml`` uploads with ``actions/upload-pages-artifact``
  (GitHub Actions deployment).
* ``publish_repo_artifacts()`` writes ``/apps.json``, the ``/api/`` mirror
  and the SEO files into the repository root, which is what GitHub's *legacy*
  branch deployment (``pages-build-deployment``) serves. ``/apps.json`` — the
  URL installers add as a source — must therefore be committed.

Both publishers expose every generated artifact at the same URL families
so existing subscribers and future API consumers keep working:

* organized   ``/feeds/<file>``           — canonical generated location
* flat        ``/<file>``                 — historical subscriber URLs (in ``_site/`` only)
* API         ``/api/<file>``             — machine-readable endpoints
* app pages   ``/apps/<slug>/``           — static detail pages

On top of the copy, the publishers add the pieces that make the site a
first-class web app: ``sitemap.xml`` (every page, regenerated each build),
``robots.txt``, ``.nojekyll``, the home page's live statistics (baked into
the served ``index.html`` copies), gzip copies of the JSON API documents
(``.json.gz``) for consumers that want the smallest payload, and — in
``_site/`` only — minified copies of the design-system stylesheets
(comments/blank lines stripped, no structural rewriting, so the committed
source of truth stays readable).

The repository root is kept clean: ``feeds/`` is the single source of truth
for every generated feed, and the only feed committed at the root is
``apps.json`` (the installable source URL, byte-identical to
``feeds/apps.json``). The historical flat URL family is still assembled into
``_site/`` for the GitHub Actions deployment, so existing subscribers keep
working while the repository tree no longer duplicates ``feeds/``.
``check_reproducible.py`` fails the build if ``apps.json`` ever drifts, and the
hand-maintained ``catalog.json`` is the only root-level JSON the publisher
does not own.
"""

from __future__ import annotations

import argparse
import filecmp
import gzip
import json
import re
import shutil
import sys
from html import escape
from pathlib import Path
from typing import Any

from omnisource.domain import today

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "_site"

# Hand-maintained website sources that live at the repository root. The
# repository root holds the site *sources* (pages, js/, assets/, feeds/,
# apps/); the deployable site is assembled from them into _site/ by
# build_site(), which is the only publisher (GitHub Actions deployment).
SITE_FILES = (
    "index.html",
    "compare.html",
    "manifest.webmanifest",
    "sw.js",
    "compare",
    "collections",
    "sources",
    "favorites",
    "install",
    "analytics",
    "search",
    "status",
    "discover",
    "graph",
    "translation-status",
    "js",
    "src",
    "locales",
    "website",
    "docs",
)

# Assets that must ship even though the site HTML does not reference them:
# feed iconURL values, favicons and JS fallbacks all point at the PNG.
ASSET_ALWAYS_PNG = ("OmniSource.png",)

# Files published under both the flat root and /api/ (the machine API surface).
# NOTE: the hand-maintained catalog.json is published only at /catalog.json;
# /api/catalog.json is the auto-generated discovery index (see docs/API.md).
API_DOCUMENTS = {
    "apps.json": "The unified AltStore Source v2 feed (the installable source URL).",
    "discovery.json": "Searchable discovery catalog, generated every build. Also exposed as api/catalog.json.",
    "sources.json": "Source index: upstreams, publisher, clients, feed metadata and "
    "the Source Explorer roll-up (slug, status, reputation, health, last update).",
    "verification.json": "Trust levels and checks per app.",
    "status.json": "Source health board: reachability, latency, update age.",
    "duplicates.json": "Duplicate groups and recommended source per group.",
    "analytics.json": "Repository-derived metrics: totals, weekly changes, trends.",
    "updates.json": "Release timeline for the website.",
    "health.json": "Per-app download health and staleness annotations.",
    "trending.json": "Trending apps, rising apps, recently updated.",
    "related.json": "Per-app relationship graph (bundle, category, developer, tags).",
    "reputation.json": "Source reputation (TRUSTED / RELIABLE / AVERAGE / EXPERIMENTAL).",
    "download-intelligence.json": "Per-app historical availability, latency, mirror count.",
    "community.json": "Popular, recently added, rising and requested apps.",
    "install.json": "Install cards for every app and the master feed.",
    "search-index.json": "Fuse.js-compatible search index.",
    "compare.json": "Side-by-side comparison matrix (app summaries only; pairs computed client-side).",
    "screenshots.json": "Screenshot catalog + mirror URLs + WebP thumbnails.",
    "integrity_report.json": "Per-asset integrity: sha256, size, release id, source and reject checks.",
    "dead_apps.json": "Dead/stale/approaching-dead classification (90/180/365 day thresholds).",
    "collections.json": "Curated collections (YouTube, Music, Emulators, Utilities, Productivity).",
    "translation-status.json": "Per-locale translation coverage (%) against the canonical English locale.",
    "asset-manifest.json": "Icon health, placeholder fallbacks and per-category asset maps.",
}

# V2 API mapping: canonical file in feeds/ -> published name under api/v2/.
# These are aliases that share bytes with the v1 documents where possible so
# OmniStore clients can rely on stable versioned paths without duplicating
# payloads. The graph/trust/recommendations v2 endpoints are assembled
# client-side-aware (from related.json + verification.json + trending.json).
API_V2_ALIASES = {
    "apps.json": "discovery.json",
    "sources.json": "sources.json",
    "trending.json": "trending.json",
    "status.json": "status.json",
    "recommendations.json": "related.json",
    "trust.json": "verification.json",
}

API_V2_STANDALONE = {"index.json", "graph.json"}

# OmniStore Pro contract documents generated into feeds/api/v2/ on every
# build (see omnisource.api_v2) and mirrored byte-identical to api/v2/.
# Per-app records live under api/v2/apps/<id>.json (one per catalog slug).
API_V2_CONTRACT = ("manifest.json", "featured.json", "categories.json", "updates.json")
API_V2_APPS_ROUTE = "apps"

# Extensionless API routes (Phase 16): clients that prefer clean URLs get a
# byte-identical twin of the JSON document. Mapping of route name -> document.
API_ROUTES = {
    "apps": "apps.json",
    "trending": "trending.json",
    "collections": "collections.json",
    "search": "search-index.json",
    "status": "status.json",
    "catalog": "discovery.json",
}

# Hand-maintained website sections (besides the home page), in sitemap order.
# /discover/ and /graph/ are linked from the main navigation, so they belong
# here too — they were missing, which left two indexable pages unlisted.
SITE_PAGES = (
    ("/sources/", 0.8, "weekly"),
    ("/docs/", 0.6, "monthly"),
    ("/compare/", 0.8, "weekly"),
    ("/discover/", 0.7, "weekly"),
    ("/status/", 0.6, "daily"),
    ("/analytics/", 0.5, "weekly"),
    ("/install/", 0.7, "weekly"),
    ("/search/", 0.7, "weekly"),
    ("/graph/", 0.5, "weekly"),
    ("/collections/", 0.6, "weekly"),
    ("/favorites/", 0.4, "monthly"),
    # Hand-maintained and reachable only from the home footer; without this
    # entry the page is an orphan in the sitemap as well as in the crawl.
    ("/translation-status/", 0.3, "monthly"),
)


def _api_manifest(base_url: str, generated_at: str) -> dict[str, Any]:
    endpoints = [
        {"path": f"/api/{name}", "description": description, "format": "json"}
        for name, description in sorted(API_DOCUMENTS.items())
    ]
    discovery = next(item["description"] for item in endpoints if item["path"] == "/api/discovery.json")
    endpoints.append(
        {
            "path": "/api/catalog.json",
            "description": f"Alias of /api/discovery.json. {discovery}",
            "format": "json",
        }
    )
    endpoints.append(
        {
            "path": "/api/catalog.min.json",
            "description": "Minified discovery catalog (Phase 13). Same data, compact encoding.",
            "format": "json",
        }
    )
    for route in sorted(API_ROUTES):
        endpoints.append(
            {
                "path": f"/api/{route}",
                "description": f"Extensionless alias of /api/{API_ROUTES[route]} (Phase 16).",
                "format": "json",
            }
        )
    for name in API_V2_CONTRACT:
        endpoints.append(
            {
                "path": f"/api/v2/{name}",
                "description": f"OmniStore Pro contract document (schema v2): {name}.",
                "format": "json",
            }
        )
    endpoints.append(
        {
            "path": "/api/v2/apps/{id}.json",
            "description": "OmniStore Pro per-app record (schema v2), one document per catalog id.",
            "format": "json",
        }
    )
    return {
        "name": "OmniSource API",
        "version": "1",
        "baseURL": base_url.rstrip("/"),
        "generatedAt": generated_at,
        "endpoints": sorted(endpoints, key=lambda item: item["path"]),
        "docs": "https://github.com/iamsmmh/OmniSource/blob/main/docs/API.md",
    }


def _base_url_from_catalog(root: Path) -> str:
    try:
        raw = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
        return str(raw.get("source", {}).get("baseURL", ""))
    except (OSError, ValueError):
        return "https://iamsmmh.github.io/OmniSource"


def _app_slugs(root: Path) -> list[str]:
    apps_dir = root / "apps"
    if not apps_dir.is_dir():
        return []
    return sorted(p.name for p in apps_dir.iterdir() if p.is_dir() and (p / "index.html").is_file())


def _collection_slugs(root: Path) -> list[str]:
    """Generated collection page slugs (collections/<slug>/index.html)."""
    collections_dir = root / "collections"
    if not collections_dir.is_dir():
        return []
    return sorted(p.name for p in collections_dir.iterdir() if p.is_dir() and (p / "index.html").is_file())


def _source_slugs(root: Path) -> list[str]:
    """Generated Source Explorer page slugs (sources/<slug>/index.html)."""
    sources_dir = root / "sources"
    if not sources_dir.is_dir():
        return []
    return sorted(p.name for p in sources_dir.iterdir() if p.is_dir() and (p / "index.html").is_file())


def _compare_pairs(root: Path) -> list[str]:
    """Return the list of compare-pair slugs.

    In v2 the 5,800+ static pair pages are no longer generated: the
    /compare/?app1=a&app2=b URL renders everything client-side. The sitemap
    therefore no longer lists individual pair URLs, which keeps sitemap.xml
    small and relevant.
    """
    return []


def _sitemap(
    base_url: str,
    slugs: list[str],
    today: str,
    *,
    compare_pairs: list[str] | None = None,
    collection_slugs: list[str] | None = None,
    source_slugs: list[str] | None = None,
) -> str:
    base = base_url.rstrip("/")
    urls = [(f"{base}/", "1.0", "daily")]
    for path, priority, changefreq in SITE_PAGES:
        urls.append((f"{base}{path}", str(priority), changefreq))
    for slug in slugs:
        urls.append((f"{base}/apps/{slug}/", "0.9", "weekly"))
    for slug in collection_slugs or []:
        urls.append((f"{base}/collections/{slug}/", "0.6", "weekly"))
    for slug in source_slugs or []:
        urls.append((f"{base}/sources/{slug}/", "0.7", "weekly"))
    for pair in compare_pairs or []:
        urls.append((f"{base}/compare/{pair}/", "0.5", "monthly"))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, priority, changefreq in urls:
        lines += [
            "  <url>",
            f"    <loc>{loc}</loc>",
            f"    <lastmod>{today}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _robots(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"


def _minify_json(text: str) -> str:
    """Compact a pretty-printed JSON document (byte-stable, same data)."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _minify_css(text: str) -> str:
    """Conservative CSS minification: drop comments and blank lines only.

    No token rewriting — the result stays diffable against the source and
    cannot change selector specificity or cascade order.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip() + "\n"


def _write_gzip(path: Path) -> Path | None:
    """Write a deterministic gzip copy next to ``path`` (``.gz``).

    Skipped when compression would not save bytes; a stale twin is removed
    when the source disappears, so twins never outlive their documents.
    """
    gz_path = Path(str(path) + ".gz")
    if not path.exists():
        gz_path.unlink(missing_ok=True)
        return None
    data = path.read_bytes()
    compressed = gzip.compress(data, mtime=0)
    if len(compressed) >= len(data):
        gz_path.unlink(missing_ok=True)
        return None
    if not gz_path.exists() or gz_path.read_bytes() != compressed:
        gz_path.write_bytes(compressed)
    return gz_path


def _write_brotli(path: Path) -> Path | None:
    """Write ``<path>.br`` when the brotli package is installed (optional).

    GitHub Pages serves ``.br`` automatically for Brotli-capable clients.
    The runtime stays stdlib-only: without the ``brotli`` package the twin is
    simply skipped (CI can ``pip install brotli`` to opt in).
    """
    brotli_path = Path(str(path) + ".br")
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        brotli_path.unlink(missing_ok=True)
        return None
    if not path.exists():
        brotli_path.unlink(missing_ok=True)
        return None
    data = path.read_bytes()
    try:
        compressed = brotli.compress(data, quality=11)
    except (OSError, ValueError):
        return None
    if len(compressed) >= len(data):
        brotli_path.unlink(missing_ok=True)
        return None
    brotli_path.write_bytes(compressed)
    return brotli_path


def _write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _deployable_asset(path: Path) -> bool:
    """Whether an assets/ file belongs in the deployed site.

    App icons ship as .webp (the site and feeds reference the webp twins);
    the legacy per-app .png files are design source only. OmniSource.png is
    the brand icon for favicons, feed iconURLs and JS fallbacks — it ships.
    """
    return not (path.suffix.lower() == ".png" and path.name not in ASSET_ALWAYS_PNG)


# ---------------------------------------------------------------------------
# Shared publishers (used by both deployment modes)
# ---------------------------------------------------------------------------


def _copy_if_different(source: Path, destination: Path) -> bool:
    """Copy ``source`` over ``destination``; return whether the bytes changed.

    Byte comparison (not timestamps) keeps a rebuild from touching files whose
    content is unchanged, which is what makes the publisher idempotent and its
    reports trustworthy.
    """
    if destination.exists() and filecmp.cmp(source, destination, shallow=False):
        return False
    shutil.copy2(source, destination)
    return True


def _publish_flat_feeds(root: Path, destination: Path) -> tuple[int, list[Path]]:
    """Copy every canonical feed to its flat historical URL name.

    ``destination`` is the ``_site/`` Pages artifact: the historical flat URL
    family is assembled there so every legacy subscriber URL keeps resolving,
    while the repository root itself stays clean (only ``/apps.json`` is
    mirrored there, by :func:`publish_repo_artifacts`). Copies are
    byte-identical to ``feeds/`` so the organized, flat and API families can
    never drift apart. Returns ``(published, changed)``.
    """
    published = 0
    changed: list[Path] = []
    for pattern in ("*.json", "*.xml"):
        for feed in sorted((root / "feeds").glob(pattern)):
            if feed.name == "state.json":
                continue
            if _copy_if_different(feed, destination / feed.name):
                changed.append(destination / feed.name)
            published += 1
    return published, changed


def _publish_api_mirror(root: Path, api_dir: Path, *, brotli: bool) -> dict[str, int]:
    """Write the machine API surface into ``api_dir``.

    Publishes every :data:`API_DOCUMENTS` entry, the ``catalog.json`` alias of
    the discovery index, its minified twin, the extensionless :data:`API_ROUTES`
    aliases and the ``api/index.json`` manifest — each with a ``.gz`` twin and,
    when ``brotli`` is enabled and the optional package is importable, a ``.br``
    twin. Returns the published file counts plus the names written, so the
    repository-root publisher can prune files that are no longer part of the
    API.
    """
    api_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()  # every file the mirror owns (for pruning)
    changed: list[Path] = []  # files whose content changed in this run
    documents = 0
    gz_files = 0
    br_files = 0

    def publish(destination: Path, source: Path) -> None:
        nonlocal documents, gz_files, br_files
        if _copy_if_different(source, destination):
            changed.append(destination)
        written.add(destination.name)
        documents += 1
        if _write_gzip(destination) is not None:
            written.add(destination.name + ".gz")
            gz_files += 1
        if brotli and _write_brotli(destination) is not None:
            written.add(destination.name + ".br")
            br_files += 1

    for name in API_DOCUMENTS:
        source = root / "feeds" / name
        if not source.exists():
            continue
        if name == "discovery.json":
            # The discovery index is also the API consumer's catalog: document
            # it under both names, plus a minified twin for payload-only clients.
            minified = api_dir / "catalog.min.json"
            payload = _minify_json(source.read_text(encoding="utf-8"))
            if not minified.exists() or minified.read_text(encoding="utf-8") != payload:
                changed.append(minified)
            _write_text(minified, payload)
            written.add(minified.name)
            if _write_gzip(minified) is not None:
                written.add(minified.name + ".gz")
                gz_files += 1
            if brotli and _write_brotli(minified) is not None:
                written.add(minified.name + ".br")
                br_files += 1
            publish(api_dir / "catalog.json", source)
        publish(api_dir / name, source)

    # Extensionless routes (Phase 16): byte-identical twins for clients that
    # prefer clean URLs (/api/apps, /api/search, ...).
    for route, document in API_ROUTES.items():
        source = root / "feeds" / document
        if source.exists():
            publish(api_dir / route, source)

    # V2 API (api/v2/): stable, versioned surface for OmniStore clients.
    v2_dir = api_dir / "v2"
    v2_dir.mkdir(parents=True, exist_ok=True)
    v2_written: set[str] = set()

    def publish_v2(destination: Path, source: Path) -> None:
        nonlocal documents, gz_files, br_files
        if _copy_if_different(source, destination):
            changed.append(destination)
        v2_written.add(destination.name)
        documents += 1
        if _write_gzip(destination) is not None:
            v2_written.add(destination.name + ".gz")
            gz_files += 1
        if brotli and _write_brotli(destination) is not None:
            v2_written.add(destination.name + ".br")
            br_files += 1

    for v2_name, feeds_name in API_V2_ALIASES.items():
        source = root / "feeds" / feeds_name
        if source.exists():
            publish_v2(v2_dir / v2_name, source)
    # Copy the api/v2/index.json manifest that ships in the repository.
    v2_index_src = root / "api" / "v2" / "index.json"
    if v2_index_src.exists():
        publish_v2(v2_dir / "index.json", v2_index_src)
    # graph.json is assembled on demand from discovery + related + sources.
    _publish_v2_graph(root, v2_dir, v2_written)
    # OmniStore Pro contract: generated feeds/api/v2/* mirrored verbatim.
    v2_apps_written = _publish_v2_contract(root, v2_dir, publish_v2, v2_written)

    manifest_path = api_dir / "index.json"
    manifest = _api_manifest(_base_url_from_catalog(root), today())
    manifest_payload = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != manifest_payload:
        changed.append(manifest_path)
    _write_text(manifest_path, manifest_payload)
    written.add(manifest_path.name)
    if _write_gzip(manifest_path) is not None:
        written.add(manifest_path.name + ".gz")
        gz_files += 1
    if brotli and _write_brotli(manifest_path) is not None:
        written.add(manifest_path.name + ".br")
        br_files += 1

    return {
        "documents": documents,
        "gz_files": gz_files,
        "br_files": br_files,
        "published": written,
        "v2_published": v2_written,
        "v2_apps_published": v2_apps_written,
        "changed": changed,
    }


def _publish_v2_contract(
    root: Path,
    v2_dir: Path,
    publish_v2: Any,
    v2_written: set[str],
) -> set[str]:
    """Mirror the generated OmniStore Pro contract into ``api/v2/``.

    Top-level documents land next to the existing aliases; per-app records
    land under ``apps/<id>.json``. Returns the owned names inside ``apps/``
    so the repository-root publisher can prune records of removed apps.
    """
    contract_src = root / "feeds" / "api" / "v2"
    for name in API_V2_CONTRACT:
        source = contract_src / name
        if source.exists():
            publish_v2(v2_dir / name, source)
    apps_src = contract_src / API_V2_APPS_ROUTE
    apps_dir = v2_dir / API_V2_APPS_ROUTE
    apps_written: set[str] = set()
    if apps_src.is_dir():
        apps_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(apps_src.glob("*.json")):
            target = apps_dir / source.name
            # Track per-app twins separately: v2_written holds names relative
            # to v2_dir (for pruning it), while apps_written is relative to
            # v2_dir/apps/ (for pruning that).
            seen = set(v2_written)
            publish_v2(target, source)
            for owned in v2_written - seen:
                apps_written.add(owned)
                v2_written.discard(owned)
    return apps_written


# ---------------------------------------------------------------------------
# V2 API graph document
# ---------------------------------------------------------------------------


def _publish_v2_graph(root: Path, v2_dir: Path, written_set: set[str]) -> None:
    """Assemble api/v2/graph.json from the existing intelligence documents."""
    discovery = _generated_doc(root, "discovery.json")
    related = _generated_doc(root, "related.json")
    apps_list = discovery.get("apps") if isinstance(discovery, dict) else []
    if not apps_list:
        return

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    nodes_set: set[str] = set()
    dev_map: dict[str, str] = {}
    source_map: dict[str, str] = {}

    def add_node(node_id: str, kind: str, label: str, **extra: Any) -> None:
        if node_id in nodes_set:
            return
        nodes_set.add(node_id)
        entry: dict[str, Any] = {"id": node_id, "type": kind, "label": label}
        entry.update(extra)
        nodes.append(entry)

    for app in apps_list:
        slug = app.get("slug") or app.get("id")
        if not slug:
            continue
        add_node(
            f"app:{slug}",
            "app",
            app.get("name") or slug,
            slug=slug,
            bundleId=app.get("bundleId") or app.get("bundleIdentifier") or "",
            category=app.get("category") or "other",
            version=app.get("version") or "",
        )
        dev = app.get("developerName") or app.get("developer") or "unknown"
        dev_key = dev.lower().replace(" ", "-")
        dev_id = f"dev:{dev_key}"
        if dev_key not in dev_map:
            dev_map[dev_key] = dev_id
            add_node(dev_id, "developer", dev)
        edges.append({"source": f"app:{slug}", "target": dev_id, "type": "developer"})
        src_label = app.get("source") or "unknown"
        src_key = "".join(ch for ch in src_label.lower() if ch.isalnum())[:30]
        src_id = f"source:{src_key}"
        if src_key not in source_map:
            source_map[src_key] = src_id
            add_node(src_id, "source", src_label, url=app.get("sourceURL") or "")
        edges.append({"source": f"app:{slug}", "target": src_id, "type": "source"})

    bundles: dict[str, list[str]] = {}
    for app in apps_list:
        bid = app.get("bundleId") or app.get("bundleIdentifier")
        slug = app.get("slug") or app.get("id")
        if bid and slug:
            bundles.setdefault(bid, []).append(slug)
    for bid, slugs in bundles.items():
        for i in range(len(slugs)):
            for j in range(i + 1, len(slugs)):
                edges.append(
                    {"source": f"app:{slugs[i]}", "target": f"app:{slugs[j]}", "type": "bundle", "bundleId": bid}
                )

    rel = related.get("related") if isinstance(related, dict) else None
    if isinstance(rel, dict):
        for slug, related_list in rel.items():
            if not isinstance(related_list, list):
                continue
            for target in related_list:
                tslug = target if isinstance(target, str) else target.get("slug")
                if not tslug:
                    continue
                if f"app:{slug}" in nodes_set and f"app:{tslug}" in nodes_set:
                    edges.append({"source": f"app:{slug}", "target": f"app:{tslug}", "type": "related"})

    payload = {
        "schemaVersion": 2,
        "generatedAt": today(),
        "count": len(nodes),
        "edgeCount": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
    target = v2_dir / "graph.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        _write_text(target, text)
    written_set.add(target.name)


# ---------------------------------------------------------------------------
# Homepage live statistics (no-JS/SEO values, baked into the served copy)
# ---------------------------------------------------------------------------


def _homepage_stat_values(health_doc: dict[str, Any], analytics_doc: dict[str, Any]) -> dict[str, str]:
    totals = analytics_doc.get("totals") or {}
    total = int(totals.get("apps") or 0)
    sources = int(totals.get("sources") or 0)
    verified = int(totals.get("verifiedApps") or 0)
    reachable = int((health_doc.get("totals") or {}).get("reachable") or 0)
    if not total:
        banner = "Checking source health…"
    elif reachable == total:
        banner = f"All {total} downloads verified online"
    else:
        banner = f"{reachable} of {total} downloads online"
    last_sync = str(analytics_doc.get("lastSync") or "").strip()
    return {
        "statApps": str(total),
        "statSources": str(sources),
        "statOnline": f"{reachable}/{total}",
        "statVerified": f"{verified}/{total}",
        "healthLabel": banner,
        "statSyncLabel": f"last sync {last_sync}" if last_sync else "",
        # PR #72's category row shows an "All" count. It is catalog data, so the
        # committed copy must carry the real number: no-JS readers and crawlers
        # would otherwise read whatever was typed when the row was drafted.
        "tabCountAll": str(total),
    }


def _inject_homepage_stats(path: Path, health_doc: dict[str, Any], analytics_doc: dict[str, Any]) -> bool:
    """Write the real statistics into a home page copy.

    Without this step, no-JS visitors, crawlers and any renderer whose
    requestAnimationFrame callbacks never fire (headless, throttled
    background tabs) would see stale numbers forever. JS still animates on
    top of the real values. Fails loudly if a marker disappears from the
    page. Only ever called on the deployed ``_site/index.html`` copy — the
    committed template keeps the values of the last build for readability.
    """
    if not path.is_file():
        raise ValueError(f"home page missing: {path}")
    text = path.read_text(encoding="utf-8")
    values = _homepage_stat_values(health_doc, analytics_doc)
    changed = False
    for key, value in values.items():
        if key == "healthLabel":
            pattern = re.compile(r'(<span id="healthLabel">)[^<]*(</span>)')
        elif key == "statSyncLabel":
            pattern = re.compile(r'(<p[^>]*id="statSyncLabel"[^>]*>)[^<]*(</p>)')
        elif key == "tabCountAll":
            pattern = re.compile(r'(<span class="tab-count" id="tabCountAll">)[^<]*(</span>)')
        else:
            pattern = re.compile(rf'(<strong id="{re.escape(key)}" data-count>)[^<]*(</strong>)')
        safe = escape(value)

        def _replace(match: re.Match[str], safe: str = safe) -> str:
            return match.group(1) + safe + match.group(2)

        updated, count = pattern.subn(_replace, text, count=1)
        if count != 1:
            raise ValueError(f"home page stat marker missing: {key}")
        if updated != text:
            text = updated
            changed = True
    if changed:
        _write_text(path, text)
    return changed


def _inject_sources_table(path: Path, sources_doc: dict[str, Any]) -> bool:
    """Refresh the static fallback table inside ``sources/index.html``.

    The interactive explorer replaces this block at runtime, but crawlers and
    no-JS visitors read the server-rendered rows — so the committed copy must
    not drift. Markers delimit the region; a missing marker raises so a silent
    template change cannot disable the fallback.
    """
    from omnisource.source_pages import render_sources_table

    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    start = "<!-- sources:static:start -->"
    end = "<!-- sources:static:end -->"
    if start not in text or end not in text:
        raise ValueError(f"sources page markers missing: {path}")
    block = "\n" + render_sources_table(_CatalogShell(path.parents[1]), sources_doc) + "  "
    updated, _ = re.subn(
        re.escape(start) + ".*?" + re.escape(end),
        lambda _m: start + block + end,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if updated != text:
        _write_text(path, updated)
        return True
    return False


class _CatalogShell:
    """Minimal ``base_url`` view for the table renderer (relative links)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def base_url(self) -> str:
        return _base_url_from_catalog(self.root)


# ---------------------------------------------------------------------------
# Repository-root publication (branch-backed Pages deployment)
# ---------------------------------------------------------------------------

# Root-level generated files that are not feed copies. ``apps.json`` is the
# installable source URL and the only feed mirrored at the repository root;
# the publisher owns every other root ``*.json``/``*.xml`` file it removes,
# while ``catalog.json`` is the hand-maintained source of truth and is never
# touched (a unit test guards the invariant that no other hand-maintained root
# JSON/XML exists).
ROOT_GENERATED_FILES = ("apps.json", "sitemap.xml", "robots.txt", ".nojekyll")
ROOT_HAND_MAINTAINED = ("catalog.json",)
# Root documents another workflow publishes. ``security.yml`` commits the root
# copy of its machine-readable report (``scripts/security/scan.py --alias``),
# so the feed pipeline must keep it: pruning it deleted a committed file, which
# ``sync.yml``'s ``git add -A -- '*.json'`` then published as a removal, and
# the report 404'd until the next daily scan restored it.
ROOT_EXTERNALLY_PUBLISHED = ("security-report.json",)


def _prune_mirror(directory: Path, keep: set[str]) -> list[Path]:
    """Remove generated files in ``directory`` that are no longer published."""
    removed: list[Path] = []
    if not directory.is_dir():
        return removed
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name in keep:
            continue
        if path.suffix.lower() not in {".json", ".xml", ".gz", ".br"}:
            continue
        path.unlink()
        removed.append(path)
    return removed


def publish_repo_artifacts(
    root: Path,
    *,
    health_doc: dict[str, Any] | None = None,
    analytics_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish the generated URLs that must live at the repository root.

    The canonical home of every generated feed is ``feeds/`` (and the machine
    API surface under ``api/``); the repository root stays clean. The only
    feed mirrored at the root is ``/apps.json`` — the URL installers register
    — which GitHub Pages serves from this branch. The full flat URL family
    (``/<slug>.json``, ``/<slug>.xml``, ``/feed.xml``, …) is still assembled
    into the ``_site/`` artifact by :func:`build_site`, so the GitHub Actions
    deployment keeps serving every historical subscriber URL while the
    repository tree no longer carries duplicate copies of ``feeds/``.

    Also writes ``/sitemap.xml``, ``/robots.txt``, ``.nojekyll`` and the live
    homepage statistics, and prunes any root JSON/XML the publisher no longer
    owns.

    Called by the pipeline (so a sync can never leave the mirror stale) and by
    ``scripts/publish_root.py`` for repair/one-off runs. Returns a summary with
    the files written and removed.
    """
    root = root.resolve()
    written: list[Path] = []
    removed: list[Path] = []

    def write(path: Path, data: str) -> None:
        if not path.exists() or path.read_text(encoding="utf-8") != data:
            _write_text(path, data)
            written.append(path)

    # /apps.json — the installable source URL, byte-identical to feeds/.
    flat_changed: list[Path] = []
    apps_feed = root / "feeds" / "apps.json"
    if apps_feed.exists() and _copy_if_different(apps_feed, root / "apps.json"):
        flat_changed.append(root / "apps.json")
    written.extend(flat_changed)

    api = _publish_api_mirror(root, root / "api", brotli=False)
    written.extend(api["changed"])
    removed.extend(_prune_mirror(root / "api", set(api["published"])))
    # The v2 directory keeps the hand-maintained index.json plus everything
    # the mirror just published; per-app records of removed apps are pruned.
    removed.extend(_prune_mirror(root / "api" / "v2", set(api["v2_published"]) | {"index.json"}))
    removed.extend(_prune_mirror(root / "api" / "v2" / "apps", set(api["v2_apps_published"])))
    keep = {*ROOT_GENERATED_FILES, *ROOT_HAND_MAINTAINED, *ROOT_EXTERNALLY_PUBLISHED}
    keep.update(f"{name}.gz" for name in ROOT_GENERATED_FILES if name.endswith(".json"))
    removed.extend(_prune_mirror(root, keep))

    base_url = _base_url_from_catalog(root)
    write(
        root / "sitemap.xml",
        _sitemap(
            base_url,
            _app_slugs(root),
            today(),
            compare_pairs=_compare_pairs(root),
            collection_slugs=_collection_slugs(root),
            source_slugs=_source_slugs(root),
        ),
    )
    write(root / "robots.txt", _robots(base_url))
    (root / ".nojekyll").touch(exist_ok=True)

    # Source Explorer static table (branch-deployed sources/index.html copy).
    sources_page = root / "sources" / "index.html"
    if sources_page.is_file():
        sources_doc = _generated_doc(root, "sources.json")
        if sources_doc and _inject_sources_table(sources_page, sources_doc):
            written.append(sources_page)

    # Live statistics in the served home page (no-JS/crawler values). The
    # committed copy must carry the real numbers because it *is* the page the
    # branch deployment serves. A checkout without the website sources (a feed
    # consumer consuming only feeds/) must still be publishable, so a missing
    # home page is skipped — but a page that lost a stat marker raises.
    if health_doc is None:
        health_doc = _generated_doc(root, "health.json")
    if analytics_doc is None:
        analytics_doc = _generated_doc(root, "analytics.json")
    home = root / "index.html"
    if home.is_file() and _inject_homepage_stats(home, health_doc, analytics_doc):
        written.append(home)

    return {
        "flat_files": 1,  # /apps.json — the only feed mirrored at the root
        "api_documents": api["documents"],
        "api_gz_files": api["gz_files"],
        "written": sorted({str(path.relative_to(root)) for path in written}),
        "removed": sorted(str(path.relative_to(root)) for path in removed),
    }


# ---------------------------------------------------------------------------
# _site/ assembly
# ---------------------------------------------------------------------------


def _generated_doc(root: Path, name: str) -> dict[str, Any]:
    """Read a generated intelligence document, tolerating a fresh checkout."""
    try:
        raw = json.loads((root / "feeds" / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def build_site(output: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Build a complete, deployable site without changing source files."""
    root = (root or ROOT).resolve()
    output = output.resolve()
    if output == root or root not in output.parents:
        raise ValueError("output must be a directory inside the repository")

    build_date = today()
    base_url = _base_url_from_catalog(root)
    slugs = _app_slugs(root)

    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    for name in SITE_FILES:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, output / name)
        elif source.is_file():
            shutil.copy2(source, output / name)

    # Assets: ship the webp twins + design system; skip legacy per-app PNGs.
    assets_src = root / "assets"
    assets_dst = output / "assets"
    for path in sorted(assets_src.rglob("*")):
        if path.is_file() and _deployable_asset(path):
            destination = assets_dst / path.relative_to(assets_src)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)

    # Organized feeds (canonical location).
    shutil.copytree(root / "feeds", output / "feeds", ignore=shutil.ignore_patterns("state.json"))

    # Historical flat source URLs for existing subscribers (/apps.json,
    # /<slug>.json, /<slug>.xml, badges, intelligence docs): byte-identical
    # copies of the canonical feeds/, assembled fresh on every build.
    flat_count, _ = _publish_flat_feeds(root, output)

    shutil.copy2(root / "catalog.json", output / "catalog.json")
    # Phase 13: a minified catalog twin at the flat URL (clients that only
    # want the payload skip the formatting bytes; gzip twin alongside).
    discovery = root / "feeds" / "discovery.json"
    if discovery.exists():
        minified_catalog = output / "catalog.min.json"
        _write_text(minified_catalog, _minify_json(discovery.read_text(encoding="utf-8")))
        flat_count += 1

    # Static app detail pages. (compare/ and collections/ ship via SITE_FILES.)
    if (root / "apps").is_dir():
        shutil.copytree(root / "apps", output / "apps")

    # Machine-readable API surface plus a small manifest.
    api = _publish_api_mirror(root, output / "api", brotli=True)
    gz_count = api["gz_files"]
    br_count = api["br_files"]
    if (output / "catalog.min.json").exists() and _write_gzip(output / "catalog.min.json") is not None:
        gz_count += 1

    # SEO / discoverability. The repository copy is written by
    # publish_repo_artifacts(); this one belongs to the deployable artifact.
    _write_text(
        output / "sitemap.xml",
        _sitemap(
            base_url,
            slugs,
            build_date,
            compare_pairs=_compare_pairs(root),
            collection_slugs=_collection_slugs(root),
            source_slugs=_source_slugs(root),
        ),
    )
    _write_text(output / "robots.txt", _robots(base_url))

    # Live statistics from the generated health/analytics documents, so the
    # no-JS/crawler values in both served copies are true. (Only the six stat
    # markers are rewritten — the hand-maintained markup is untouched.)
    stats_injected = _inject_homepage_stats(
        output / "index.html",
        _generated_doc(root, "health.json"),
        _generated_doc(root, "analytics.json"),
    )
    # Source Explorer static table (deployed copy).
    sources_page = output / "sources" / "index.html"
    sources_doc = _generated_doc(root, "sources.json")
    sources_injected = False
    if sources_page.is_file() and sources_doc:
        sources_injected = _inject_sources_table(sources_page, sources_doc)

    # Minify the design-system stylesheets in the deployed copy only.
    minified = 0
    css_dir = output / "assets" / "design-system"
    if css_dir.is_dir():
        for css in sorted(css_dir.glob("*.css")):
            before = css.stat().st_size
            minified_text = _minify_css(css.read_text(encoding="utf-8"))
            css.write_text(minified_text, encoding="utf-8")
            minified += before - css.stat().st_size

    (output / ".nojekyll").touch()

    compare_pairs = _compare_pairs(root)
    return {
        "output": str(output),
        "pages": 6 + len(slugs) + len(compare_pairs),
        "app_pages": len(slugs),
        "compare_pages": len(compare_pairs),
        "flat_files": flat_count,
        "gz_files": gz_count,
        "br_files": br_count,
        "css_minified_bytes": minified,
        "stats_injected": stats_injected,
        "sources_table_injected": sources_injected,
        "source_pages": len(_source_slugs(root)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="site output directory")
    args = parser.parse_args(argv)
    summary = build_site(args.output)
    print(
        f"Built site at {summary['output']}: {summary['pages']} pages "
        f"({summary['app_pages']} app pages), {summary['flat_files']} flat feed URLs, "
        f"{summary['gz_files']} gz API copies, "
        f"{summary['css_minified_bytes'] // 1024} KB CSS minified, sitemap + robots written."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
