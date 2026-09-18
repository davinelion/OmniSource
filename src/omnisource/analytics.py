"""Analytics system.

All metrics are derived from the repository itself — the catalog, the
pipeline state, the health document and the verification document — so no
external database (or third-party analytics service) is needed. The result is
``feeds/analytics.json``, refreshed on every build.

Weekly counters use ``state.json`` update history, which the sync engine
appends to whenever a release changes. ``newThisWeek``/``updatedThisWeek``
carry the underlying app entries so clients can render the lists without a
second request. A rolling snapshot history is kept in state (``analyticsHistory``)
so dashboards can show trends without extra storage.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from omnisource.domain import Catalog, today, today_date
from omnisource.utils.dates import parse_date as _date

ANALYTICS_SCHEMA_VERSION = 1
HISTORY_LIMIT = 30


def _week_events(state: dict[str, Any], kind: str, *, days: int = 7) -> list[dict[str, Any]]:
    history = state.get("updateHistory", [])
    if not isinstance(history, list):
        return []
    cutoff = today_date() - timedelta(days=days)
    events: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict) or item.get("kind") != kind:
            continue
        when = _date(item.get("releaseDate"))
        if when is None or when < cutoff:
            continue
        events.append(
            {
                "slug": str(item.get("appId") or ""),
                "name": str(item.get("name") or ""),
                "version": str(item.get("version") or ""),
                "previousVersion": item.get("previousVersion") or "",
                "date": str(item.get("releaseDate") or ""),
                "downloadURL": str(item.get("downloadUrl") or ""),
            }
        )
    events.sort(key=lambda item: (str(item["date"]), str(item["name"])), reverse=True)
    return events


def build_analytics_doc(
    catalog: Catalog,
    state: dict[str, Any],
    health_doc: dict[str, Any] | None = None,
    verification_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render ``analytics.json`` from repository-derived data."""
    health_doc = health_doc or {}
    verification_doc = verification_doc or {}
    health_total = health_doc.get("totals", {}) if isinstance(health_doc.get("totals"), dict) else {}
    verify_total = verification_doc.get("totals", {}) if isinstance(verification_doc.get("totals"), dict) else {}
    entries = verification_doc.get("apps", []) if isinstance(verification_doc.get("apps"), list) else []

    identities: set[str] = set()
    for app in catalog.apps:
        upstream = app.upstream
        identities.add((upstream.identity if upstream is not None else None) or f"manual:{app.slug}")

    new_this_week = _week_events(state, "new")
    updated_this_week = _week_events(state, "updated")

    category_counts: dict[str, int] = {}
    for app in catalog.apps:
        for category in app.categories or (app.category,):
            category_counts[category] = category_counts.get(category, 0) + 1
    top_categories = sorted(category_counts.items(), key=lambda item: (-item[1], str(item[0])))

    synced_at = [
        str(entry.get("syncedAt") or "")
        for entry in state.values()
        if isinstance(entry, dict) and entry.get("syncedAt")
    ]

    totals = {
        "apps": len(catalog.apps),
        "sources": len(identities),
        "verifiedApps": int(verify_total.get("verified", 0)),
        "communityVerifiedApps": int(verify_total.get("communityVerified", 0)),
        "unverifiedApps": int(verify_total.get("unverified", 0)),
        "hashVerifiedApps": int(verify_total.get("hashVerified", 0)),
        "verified": int(verify_total.get("verified", 0)),
        "community_verified": int(verify_total.get("communityVerified", 0)),
        "unverified": int(verify_total.get("unverified", 0)),
        "featuredApps": int(health_total.get("featured", 0)),
        "downloadsReachable": int(health_total.get("reachable", 0)),
        "deadLinks": int(health_total.get("unreachable", 0)),
        "newAppsThisWeek": len(new_this_week),
        "updatedAppsThisWeek": len(updated_this_week),
        "clientsSupported": len(catalog.clients),
    }

    doc: dict[str, Any] = {
        "schemaVersion": ANALYTICS_SCHEMA_VERSION,
        "generatedAt": today(),
        "lastSync": max(synced_at) if synced_at else None,
        "totals": totals,
        "newThisWeek": new_this_week,
        "updatedThisWeek": updated_this_week,
        "topCategories": [{"category": key, "count": value} for key, value in top_categories],
        "verification": {
            "VERIFIED": totals["verifiedApps"],
            "COMMUNITY VERIFIED": totals["communityVerifiedApps"],
            "UNVERIFIED": totals["unverifiedApps"],
        },
        "recentUpdates": [
            {
                "slug": str(item.get("app") or ""),
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in entries[:10]
        ],
    }
    history = state.get("analyticsHistory", [])
    if isinstance(history, list):
        doc["history"] = history[-HISTORY_LIMIT:]
    else:
        doc["history"] = []
    return doc


def remember_analytics_snapshot(state: dict[str, Any], analytics_doc: dict[str, Any]) -> None:
    """Append today's totals to the rolling history in pipeline state."""
    snapshot = {
        "date": today(),
        "apps": int(analytics_doc["totals"]["apps"]),
        "sources": int(analytics_doc["totals"]["sources"]),
        "verified": int(analytics_doc["totals"]["verifiedApps"]),
        "deadLinks": int(analytics_doc["totals"]["deadLinks"]),
        "newThisWeek": int(analytics_doc["totals"]["newAppsThisWeek"]),
        "updatedThisWeek": int(analytics_doc["totals"]["updatedAppsThisWeek"]),
    }
    history = state.setdefault("analyticsHistory", [])
    if not isinstance(history, list):
        history = []
        state["analyticsHistory"] = history
    if history and isinstance(history[-1], dict) and history[-1].get("date") == snapshot["date"]:
        history[-1] = snapshot
    else:
        history.append(snapshot)
    del history[:-HISTORY_LIMIT]
