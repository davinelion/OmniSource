"""Dead-app detection (Phase 8).

Automatically classifies every app from pipeline state:

* no update for **90** days    → ``warning``
* no update for **180** days   → ``stale``
* no update for **365** days   → ``archived``
* a release *newer* than the one still published is gone → ``critical``
  (the sync stage records candidate removals in ``state[slug].removedReleases``;
  see :func:`_unexplained_removals` for why most of them are not takedowns)

Rendered as ``feeds/dead_apps.json``. The classification is a *recommendation
board*, not an automatic delisting: apps keep their feeds unless a maintainer
marks them deprecated in ``catalog.json``.
"""

from __future__ import annotations

from typing import Any

from omnisource.domain import Catalog, today
from omnisource.utils.dates import days_since
from omnisource.utils.versioning import compare_versions

DEAD_APPS_SCHEMA_VERSION = 1

THRESHOLDS = {"warning": 90, "stale": 180, "archived": 365}
CLASSIFICATIONS = ("healthy", "warning", "stale", "archived", "critical")


def classify_age(days: int, *, warning: int = 90, stale: int = 180, archived: int = 365) -> str:
    if days >= archived:
        return "archived"
    if days >= stale:
        return "stale"
    if days >= warning:
        return "warning"
    return "healthy"


def _removed_releases(app_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = app_state.get("removedReleases")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _unexplained_removals(
    removed: list[dict[str, Any]],
    current_version: str,
) -> list[dict[str, Any]]:
    """Removals that a takedown explains, and those that nothing explains.

    ``upstream.keepVersions`` is 1 for almost every catalog entry, so the
    upstream stops offering the *previous* newest release as soon as it ships a
    newer one. Recording that as a removal produced a ``critical`` verdict for
    apps that had simply updated — nine of them at one point, all with a live
    download. A same-version entry with a different URL is the other false
    positive: that is the resolver failing over between provider legs, not a
    deleted release.

    So only a *regression* counts: the release that disappeared is newer than
    the one still published (or nothing is published at all). Entries the old
    logic left behind in ``feeds/state.json`` self-heal here as well as in the
    sync stage, so the published board is right without a state rewrite.
    """
    if not removed:
        return []
    if not current_version:
        return list(removed)
    kept: list[dict[str, Any]] = []
    for item in removed:
        version = str(item.get("version") or "")
        if version and compare_versions(version, current_version) > 0:
            kept.append(item)
    return kept


def classify_app(
    app: Any,
    app_state: dict[str, Any],
    *,
    today_iso: str | None = None,
) -> dict[str, Any]:
    """Classification record for one app."""
    newest_entry: dict[str, Any] = {}
    versions = app_state.get("versions")
    if isinstance(versions, list) and versions and isinstance(versions[0], dict):
        newest_entry = versions[0]
    last_update = str(newest_entry.get("date") or "")
    current_version = str(newest_entry.get("version") or "")
    removed = _unexplained_removals(_removed_releases(app_state), current_version)
    superseded = len(_removed_releases(app_state)) - len(removed)

    days = days_since(last_update, today_iso=today_iso or today()) if last_update else 3650

    reasons: list[str] = []
    classification = "healthy"
    if removed:
        classification = "critical"
        versions_removed = ", ".join(str(item.get("version") or "?") for item in removed[:3])
        published = current_version or "no release"
        reasons.append(f"removed upstream release(s): {versions_removed} (newer than {published})")
    age = classify_age(days)
    if age != "healthy":
        if classification == "healthy":
            classification = age
        reasons.append(f"no update for {days} days (threshold {THRESHOLDS.get(age, 365)})")

    return {
        "slug": app.slug,
        "name": app.name,
        "lastUpdate": last_update,
        "daysSinceUpdate": days,
        "classification": classification,
        "removedReleases": len(removed),
        # Superseded removals are counted but never escalated: they are the
        # ordinary "the upstream shipped something newer" case.
        "supersededReleases": superseded,
        "reasons": reasons,
    }


def build_dead_apps_doc(catalog: Catalog, state: dict[str, Any], *, today_iso: str | None = None) -> dict[str, Any]:
    """Render ``feeds/dead_apps.json``."""
    entries = []
    for app in catalog.apps:
        app_state = state.get(app.slug) if isinstance(state.get(app.slug), dict) else {}
        entries.append(classify_app(app, app_state, today_iso=today_iso))

    summary = dict.fromkeys(("warning", "stale", "archived", "critical"), 0)
    for entry in entries:
        if entry["classification"] in summary:
            summary[entry["classification"]] += 1

    severity_order = {"critical": 0, "archived": 1, "stale": 2, "warning": 3}
    dead = [entry for entry in entries if entry["classification"] != "healthy"]
    dead.sort(key=lambda entry: (severity_order[entry["classification"]], entry["slug"]))
    return {
        "schemaVersion": DEAD_APPS_SCHEMA_VERSION,
        "generatedAt": today_iso or today(),
        "thresholds": dict(THRESHOLDS),
        "count": len(dead),
        "summary": summary,
        "deadApps": dead,
        "apps": entries,
    }
