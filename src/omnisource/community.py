"""Community features aggregator.

This module derives community signals from artifacts that already exist in
the repository. It is the input for the future GitHub Issues integration —
when a backend issue tracker is added it can append entries to the same
JSON file. The point is to keep the website "popular apps" and "recently
added" lists in sync with what the community cares about *today*.

Lists produced:

* **popular** — apps ordered by historical availability + recent activity.
* **recentlyAdded** — apps whose first version was added in the last 14 days.
* **requested** — apps that are referenced in the community tracking state.
* **rising** — apps that show a strong month-over-month version change.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from omnisource.domain import Catalog, today, today_date
from omnisource.utils.dates import parse_date as _parse_date

COMMUNITY_SCHEMA_VERSION = 1


def _popularity(state: dict[str, Any], slug: str) -> float:
    """A 0..1 score blending reachability and recency of activity."""
    app_state = state.get(slug) if isinstance(state.get(slug), dict) else state
    history = app_state.get("healthHistory") or []
    if isinstance(history, list) and history:
        reachable = sum(1 for item in history if isinstance(item, dict) and item.get("reachable") is True)
        availability = reachable / len(history)
    else:
        availability = 0.0
    versions = app_state.get("versions") or []
    newest_date = None
    if isinstance(versions, list) and versions and isinstance(versions[0], dict):
        newest_date = _parse_date(versions[0].get("date"))
    recency = 0.0
    if newest_date is not None:
        days = max(0, (today_date() - newest_date).days)
        recency = max(0.0, 1.0 - (days / 60))
    return round(0.65 * availability + 0.35 * recency, 4)


def _rising_signal(state: dict[str, Any], slug: str) -> float:
    """Positive when a new version shipped this month vs last month."""
    versions = (state.get(slug) or {}).get("versions") or []
    if not isinstance(versions, list):
        return 0.0
    now = today_date()
    this_month = sum(
        1
        for v in versions
        if isinstance(v, dict)
        and (parsed := _parse_date(v.get("date"))) is not None
        and parsed >= now - timedelta(days=30)
    )
    last_month = sum(
        1
        for v in versions
        if isinstance(v, dict)
        and (parsed := _parse_date(v.get("date"))) is not None
        and now - timedelta(days=60) <= parsed < now - timedelta(days=30)
    )
    return this_month - last_month


def build_community_doc(
    catalog: Catalog,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build ``feeds/community.json``."""
    cutoff_recent = today_date() - timedelta(days=14)
    popular: list[dict[str, Any]] = []
    recently_added: list[dict[str, Any]] = []
    rising: list[dict[str, Any]] = []
    requested: list[dict[str, Any]] = []
    # Track an "in-repo" first-seen for each slug based on the first known
    # version date. This is a cheap proxy for "recently added".
    for app in catalog.apps:
        app_state = state.get(app.slug) if isinstance(state.get(app.slug), dict) else state
        versions = app_state.get("versions") or []
        first_date = None
        if isinstance(versions, list) and versions:
            for v in versions:
                if isinstance(v, dict):
                    parsed = _parse_date(v.get("date"))
                    if parsed is not None:
                        first_date = parsed if first_date is None else min(first_date, parsed)
        if first_date is not None and first_date >= cutoff_recent:
            recently_added.append(
                {
                    "slug": app.slug,
                    "name": app.name,
                    "firstSeen": first_date.isoformat(),
                    "category": app.category,
                }
            )
        score = _popularity(state, app.slug)
        popular.append(
            {
                "slug": app.slug,
                "name": app.name,
                "category": app.category,
                "score": score,
            }
        )
        rising_value = _rising_signal(state, app.slug)
        if rising_value > 0:
            rising.append(
                {
                    "slug": app.slug,
                    "name": app.name,
                    "category": app.category,
                    "newThisMonth": rising_value,
                }
            )
    # The "requested" list is populated from the optional
    # ``state.communityRequests`` block. This is a forward-compatible
    # extension point for the future GitHub Issues integration.
    requests_raw = state.get("communityRequests") or []
    if isinstance(requests_raw, list):
        for item in requests_raw:
            if not isinstance(item, dict):
                continue
            requested.append(
                {
                    "name": str(item.get("name") or "Unknown"),
                    "reason": str(item.get("reason") or ""),
                    "submittedAt": str(item.get("submittedAt") or ""),
                    "source": str(item.get("source") or "issue"),
                    "issueNumber": item.get("issueNumber"),
                }
            )
    popular.sort(key=lambda item: (item["score"], item["name"].casefold()), reverse=True)
    rising.sort(key=lambda item: (item["newThisMonth"], item["name"].casefold()), reverse=True)
    recently_added.sort(key=lambda item: item["firstSeen"], reverse=True)
    requested.sort(key=lambda item: item["submittedAt"], reverse=True)
    return {
        "schemaVersion": COMMUNITY_SCHEMA_VERSION,
        "generatedAt": today(),
        "popular": popular[:12],
        "recentlyAdded": recently_added[:12],
        "rising": rising[:12],
        "requested": requested,
        "counts": {
            "popular": len(popular),
            "recentlyAdded": len(recently_added),
            "rising": len(rising),
            "requested": len(requested),
        },
    }
