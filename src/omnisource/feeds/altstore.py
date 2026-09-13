"""AltStore Source v2 renderer.

Output is byte-compatible with the historical ``scripts/omnisource.py``
renderer: same keys, same ordering, same `omnisource` extension block.
Unknown keys are ignored by every AltStore-family client.
"""

from __future__ import annotations

from typing import Any

from omnisource.domain import App, Catalog, today


def render_altstore_app(
    catalog: Catalog,
    app: App,
    versions: list[dict[str, Any]],
    health: dict[str, Any],
) -> dict[str, Any]:
    base = catalog.base_url
    # Shallow-copy so fallback injection never mutates pipeline state.
    versions = [dict(version) for version in versions]
    newest = versions[0]
    raw = app.raw

    manual = app.manual_release
    fallbacks = manual.get("fallbackDownloadURLs") if manual else None
    if fallbacks is None:
        fallbacks = raw.get("fallbackDownloadURLs") or []
    fallbacks = [url for url in fallbacks if isinstance(url, str) and url]

    entry: dict[str, Any] = {
        "name": app.name,
        "bundleIdentifier": raw["bundleIdentifier"],
        "developerName": raw["developerName"],
        "subtitle": raw.get("subtitle", ""),
        "localizedDescription": raw.get("localizedDescription", ""),
        "iconURL": f"{base}/assets/{app.icon}",
        "tintColor": str(raw.get("tintColor", "FF0000")).lstrip("#"),
        "category": raw.get("category", "utilities"),
        "version": newest["version"],
        "versionDate": newest["date"],
        "versionDescription": newest["localizedDescription"],
        "downloadURL": newest["downloadURL"],
        "size": newest["size"],
        "versions": versions,
        "screenshotURLs": raw.get("screenshots", []),
    }
    if raw.get("appPermissions"):
        entry["appPermissions"] = raw["appPermissions"]
    if raw.get("permissions"):
        entry["permissions"] = raw["permissions"]
    if fallbacks:
        entry["fallbackDownloadURLs"] = list(fallbacks)
        newest["fallbackDownloadURLs"] = list(fallbacks)

    entry["omnisource"] = {
        "slug": app.slug,
        "status": app.status,
        "featured": app.featured,
        "upstreamURL": raw.get("upstreamURL", ""),
        "sourceURL": app.source_url,
        "verification": raw.get("verification", {}),
        "compatibility": raw.get("compatibility", {}),
        "health": {
            "downloadReachable": bool(health.get("reachable", True)),
            "detail": health.get("detail", "not probed"),
            "statusSince": health.get("since", newest["date"]),
            "lastUpdatedAt": newest["date"],
        },
    }
    return entry


def feed_envelope(
    catalog: Catalog,
    *,
    name: str,
    identifier: str,
    subtitle: str,
    description: str,
) -> dict[str, Any]:
    base = catalog.base_url
    source = catalog.source
    return {
        "name": name,
        "identifier": identifier,
        "apiVersion": "v2",
        "subtitle": subtitle,
        "description": description,
        "iconURL": f"{base}/assets/{source.get('icon', 'OmniSource.png')}",
        "bannerURL": f"{base}/assets/{source.get('banner', 'OmniSource.png')}",
        "tintColor": str(source.get("tintColor", "5B5BD6")).lstrip("#"),
        "website": f"{base}/",
        "sourceURL": f"{base}/apps.json",
    }


def render_health_doc(rendered: list[tuple[App, dict[str, Any]]], *, generated_at: str | None = None) -> dict[str, Any]:
    """Render the flat ``feeds/health.json`` document.

    ``generatedAt`` is when this document was built, not the newest date found in
    the data. The health document is a product of a run — the monitoring
    staleness check (``SYNC_STALE_DAYS`` in health-check.yml) and the README's
    "Last sync" line both read it as "when was this recomputed", and a
    data-derived stamp made a healthy source look stale during any quiet week
    (and could even move *backwards* when the newest app was removed).
    ``lastEventAt`` keeps the value that used to be here for readers that want
    the newest release or status change across the apps.
    """
    reachable = sum(1 for _, entry in rendered if entry["omnisource"]["health"]["downloadReachable"])
    events = [
        stamp
        for stamp in (
            [str(entry["omnisource"]["health"].get("statusSince") or "") for _, entry in rendered]
            + [str(entry.get("versionDate") or "") for _, entry in rendered]
        )
        if stamp
    ]
    return {
        "generatedAt": generated_at or today(),
        "lastEventAt": max(events, default=""),
        "totals": {
            "apps": len(rendered),
            "reachable": reachable,
            "unreachable": len(rendered) - reachable,
            "featured": sum(1 for _, entry in rendered if entry["omnisource"]["featured"]),
        },
        "apps": [
            {
                "slug": app.slug,
                "name": app.name,
                "status": app.status,
                "version": entry["version"],
                "updatedAt": entry["versionDate"],
                "sizeBytes": entry["size"],
                "downloadReachable": entry["omnisource"]["health"]["downloadReachable"],
                "detail": entry["omnisource"]["health"]["detail"],
                "statusSince": entry["omnisource"]["health"]["statusSince"],
            }
            for app, entry in rendered
        ],
    }


def render_news_items(
    catalog: Catalog,
    state: dict[str, Any],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Build AltStore Source v2 news cards from recent release history."""
    base = catalog.base_url.rstrip("/")
    app_by_slug = {app.slug: app for app in catalog.apps}
    app_by_bundle = {app.bundle_id: app for app in catalog.apps}
    news: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. From recorded update history
    history = state.get("updateHistory", [])
    if isinstance(history, list):
        for event in history:
            if not isinstance(event, dict):
                continue
            app_id = str(event.get("appId") or "")
            app = app_by_slug.get(app_id) or app_by_bundle.get(app_id)
            if not app:
                continue
            version = str(event.get("version") or "")
            identifier = f"news-{app.slug}-{version}".replace(".", "-").replace("_", "-")
            if identifier in seen:
                continue
            seen.add(identifier)
            date_val = str(event.get("releaseDate") or event.get("date") or "2026-09-07")[:10]
            news.append(
                {
                    "title": f"{app.name} v{version}",
                    "identifier": identifier,
                    "caption": app.short_description or f"{app.name} has been updated to version {version}.",
                    "date": date_val,
                    "appID": app.bundle_id,
                    "imageURL": f"{base}/assets/{app.icon}",
                    "notify": True,
                }
            )
            if len(news) >= limit:
                return news

    # 2. Seed with newest versions from state if history is sparse
    for app in catalog.apps:
        versions = state.get(app.slug, {}).get("versions")
        if not isinstance(versions, list) or not versions:
            continue
        newest = versions[0]
        version = str(newest.get("version") or "")
        identifier = f"news-{app.slug}-{version}".replace(".", "-").replace("_", "-")
        if identifier in seen:
            continue
        seen.add(identifier)
        news.append(
            {
                "title": f"{app.name} v{version}",
                "identifier": identifier,
                "caption": app.short_description or f"{app.name} is available in version {version}.",
                "date": str(newest.get("date") or "2026-09-07")[:10],
                "appID": app.bundle_id,
                "imageURL": f"{base}/assets/{app.icon}",
                "notify": False,
            }
        )
        if len(news) >= limit:
            break

    news.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return news[:limit]


def render_badge_docs(
    rendered: list[tuple[App, dict[str, Any]]],
    health_doc: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return dynamic Shields.io JSON endpoint documents."""
    total_apps = len(rendered)
    reachable = health_doc["totals"]["reachable"]
    health_color = "2ea043" if reachable == total_apps else ("d29922" if reachable > 0 else "e5534b")
    return {
        "badge-apps.json": {
            "schemaVersion": 1,
            "label": "apps",
            "message": f"{total_apps}",
            "color": "5b5bd6",
        },
        "badge-health.json": {
            "schemaVersion": 1,
            "label": "downloads",
            "message": f"{reachable}/{total_apps} healthy",
            "color": health_color,
        },
        "badge-version.json": {
            "schemaVersion": 1,
            "label": "source",
            "message": "AltStore v2",
            "color": "108548",
        },
    }
