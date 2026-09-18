"""OmniSource API v3: paginated, filterable, cache-friendly endpoints.

API v2 (see :mod:`omnisource.api_v2`) is a set of static snapshot documents.
API v3 adds the REST affordances integrators expect while staying
statically hostable:

* ``/api/v3/index.json`` — endpoint registry + global ``feedVersion``.
* ``/api/v3/apps.json`` — paginated app catalog (``page``/``per_page``).
* ``/api/v3/apps/<id>.json`` — one normalized app record.
* ``/api/v3/sources.json`` + ``/api/v3/sources/<slug>.json`` — upstreams.
  The document name is the record's ``slug`` (see :func:`safe_doc_id`), never
  its raw ``id``, which may be a URL or an ``owner/repo`` pair.
* ``/api/v3/trending.json`` — trending / rising / recently updated.
* ``/api/v3/search-index.json`` — compact client-side search corpus.
* ``/api/v3/status.json`` — health board snapshot.
* ``/api/v3/security.json`` — security posture snapshot.
* ``/api/v3/analytics.json`` — rollup snapshot.
* ``/api/v3/releases.json`` — sequenced release timeline.

Static hosting cannot evaluate query strings, so the dynamic semantics
(pagination, sorting, filtering, ETag, compression, cache control) are
implemented twice: as pure helpers here (unit-tested, reused by the
Next.js ``web/`` API routes) and as pre-rendered first pages in the
static files. Conditional refresh uses the manifest's per-document
SHA-256 checksums exactly like v2.

See ``docs/API-V3.md`` for the full contract.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

API_V3_VERSION = "3.0.0"
API_V3_SCHEMA_VERSION = 3
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200

ENDPOINTS = (
    "index.json",
    "apps.json",
    "sources.json",
    "trending.json",
    "search-index.json",
    "status.json",
    "security.json",
    "analytics.json",
    "releases.json",
)


def paginate(items: list[Any], *, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> dict[str, Any]:
    """Slice ``items`` into a page envelope with navigation metadata."""
    per_page = max(1, min(MAX_PER_PAGE, int(per_page or DEFAULT_PER_PAGE)))
    total = len(items)
    pages = max(1, -(-total // per_page))
    page = max(1, min(pages, int(page or 1)))
    start = (page - 1) * per_page
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
        "items": items[start : start + per_page],
    }


def apply_filters(items: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter app/source records by exact/substring field matches."""
    result = items
    for field, want in filters.items():
        if want in (None, "", []):
            continue
        needle = str(want).lower()
        if field in ("category", "status", "type", "level"):
            result = [item for item in result if str(item.get(field, "")).lower() == needle]
        else:
            result = [item for item in result if needle in str(item.get(field, "")).lower()]
    return result


def sort_items(items: list[dict[str, Any]], sort: str = "name") -> list[dict[str, Any]]:
    """Sort records by a ``field`` or ``-field`` (descending) key."""
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    if field in ("score", "size", "reputation"):

        def key(item: dict[str, Any]) -> Any:
            return float(item.get(field, 0) or 0)
    else:

        def key(item: dict[str, Any]) -> Any:
            return str(item.get(field, "")).lower()

    return sorted(items, key=key, reverse=descending)


def envelope(
    data: Any,
    *,
    feed_version: str = "",
    pagination: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a payload in the standard v3 envelope."""
    body: dict[str, Any] = {"apiVersion": API_V3_VERSION, "schemaVersion": API_V3_SCHEMA_VERSION}
    if feed_version:
        body["feedVersion"] = feed_version
    if pagination is not None:
        body["pagination"] = {key: pagination[key] for key in ("page", "per_page", "total", "pages")}
        body["data"] = pagination["items"]
    else:
        body["data"] = data
    return body


def etag_for(payload: str) -> str:
    """Weak ETag for a serialized document (dynamic routes)."""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f'W/"{digest[:32]}"'


def feed_version_for(documents: dict[str, str]) -> str:
    """Global feed version: 12 hex chars over every document's bytes."""
    digest = hashlib.sha256()
    for name in sorted(documents):
        digest.update(name.encode("utf-8"))
        digest.update(documents[name].encode("utf-8"))
    return digest.hexdigest()[:12]


#: Characters a document name may contain. Everything else becomes ``-``.
SAFE_DOC_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_doc_id(raw: Any) -> str:
    """Flatten an arbitrary record id into one filesystem-safe path segment.

    Document names are the last path segment of a URL *and* a filename on every
    runner, mirror and checkout this project publishes to, so they may not
    contain a separator or any character NTFS rejects. Record ids do not obey
    that: upstreams are keyed by whatever identifies them — a bare slug, a
    GitHub ``owner/repo`` pair, a full feed URL, or a namespaced
    ``manual:cercube``. Interpolated raw they produced
    ``sources/Aidoku/Aidoku.json`` (a directory per source, 97 of them) and
    ``sources/https:/repo.ikghd.me/repo.json.json`` — a colon in a filename,
    which ``actions/upload-artifact`` refuses outright, so the scheduled
    ``Backup and Recovery`` job failed on the upload step of every run.

    Empty and dot-only results collapse to ``source`` so the caller always gets
    a usable segment rather than writing to the parent directory.
    """
    text = SAFE_DOC_ID_CHARS.sub("-", str(raw or "")).strip("-._")
    return text if text not in {"", ".", ".."} else "source"


def _unique_ids(apps: list[dict[str, Any]]) -> list[str]:
    """Stable unique IDs: bare for the first use, ``-2``/``-3``… on collision."""
    seen: dict[str, int] = {}
    ids: list[str] = []
    for app in apps:
        base = safe_doc_id(app.get("slug") or app.get("id") or app.get("bundleIdentifier")) or "app"
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return ids


def slim_app(app: dict[str, Any], *, record_id: str = "") -> dict[str, Any]:
    """List-view projection of one app record."""
    return {
        "id": record_id or app.get("slug") or app.get("id") or app.get("bundleIdentifier"),
        "name": app.get("name", ""),
        "bundleIdentifier": app.get("bundleIdentifier", ""),
        "developerName": app.get("developerName", ""),
        "category": app.get("category", ""),
        "version": app.get("version", ""),
        "versionDate": app.get("versionDate", ""),
        "iconURL": app.get("iconURL", ""),
        "downloadURL": app.get("downloadURL", ""),
        "size": app.get("size", 0),
    }


def full_app(app: dict[str, Any], *, versions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Detail-view projection of one app record."""
    record = dict(app)
    record["id"] = app.get("slug") or app.get("id") or app.get("bundleIdentifier")
    if versions is not None:
        record["versions"] = versions
    return record


def build_static_documents(bundle: dict[str, Any]) -> dict[str, Any]:
    """Render every static v3 document from already-loaded feed docs.

    ``bundle`` keys: ``apps`` (AltStore app list), ``sources``,
    ``trending``, ``search_index``, ``status``, ``security``,
    ``analytics``, ``releases`` (timeline list), ``generated_at``.
    Missing keys degrade to empty documents — the endpoint still exists.
    """
    apps = [item for item in bundle.get("apps", []) if isinstance(item, dict)]
    sources = [item for item in bundle.get("sources", []) if isinstance(item, dict)]
    releases = [item for item in bundle.get("releases", []) if isinstance(item, dict)]
    generated_at = str(bundle.get("generated_at", ""))

    def version_of(doc: Any) -> str:
        return hashlib.sha256(repr(doc).encode("utf-8")).hexdigest()[:12]

    feed_version = version_of((len(apps), len(sources), len(releases), generated_at))
    ordered = sorted(apps, key=lambda a: str(a.get("name", "")).lower())
    record_ids = _unique_ids(ordered)
    first_page = paginate([slim_app(app, record_id=uid) for app, uid in zip(ordered, record_ids, strict=True)])
    documents: dict[str, Any] = {
        "apps.json": envelope(first_page["items"], feed_version=feed_version, pagination=first_page),
        "sources.json": envelope(sources, feed_version=feed_version),
        "trending.json": envelope(bundle.get("trending", {}), feed_version=feed_version),
        "search-index.json": envelope(bundle.get("search_index", {}), feed_version=feed_version),
        "status.json": envelope(bundle.get("status", {}), feed_version=feed_version),
        "security.json": envelope(bundle.get("security", {}), feed_version=feed_version),
        "analytics.json": envelope(bundle.get("analytics", {}), feed_version=feed_version),
        "releases.json": envelope(releases, feed_version=feed_version),
    }
    for app, uid in zip(ordered, record_ids, strict=True):
        detail = full_app(app)
        detail["id"] = uid
        documents[f"apps/{uid}.json"] = envelope(detail, feed_version=feed_version)
    seen_sources: dict[str, int] = {}
    for source in sources:
        # `slug` is the pre-flattened form every source record already carries;
        # `id` is the fallback for callers that only pass an id (and is what
        # `safe_doc_id` exists to make safe). Collisions keep both documents by
        # suffixing `-2`/`-3`…, the same convention `_unique_ids` uses for apps.
        base = safe_doc_id(source.get("slug") or source.get("id"))
        if base == "source" and not (source.get("slug") or source.get("id")):
            continue
        seen_sources[base] = seen_sources.get(base, 0) + 1
        doc_id = base if seen_sources[base] == 1 else f"{base}-{seen_sources[base]}"
        documents[f"sources/{doc_id}.json"] = envelope(source, feed_version=feed_version)
    checksums = {name: hashlib.sha256(repr(doc).encode("utf-8")).hexdigest() for name, doc in documents.items()}
    documents["index.json"] = {
        "apiVersion": API_V3_VERSION,
        "schemaVersion": API_V3_SCHEMA_VERSION,
        "feedVersion": feed_version,
        "generatedAt": generated_at,
        "endpoints": [f"/api/v3/{name}" for name in ENDPOINTS],
        "documents": len(documents),
        "checksums": checksums,
    }
    return documents
