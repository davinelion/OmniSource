"""Shared date and release-cadence helpers.

Extracted from five near-identical private copies (``analytics``,
``community``, ``compare``, ``download_intel``, ``reputation``) — this is the
single implementation; the intelligence modules import from here.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from typing import Any


def parse_date(value: Any) -> date | None:
    """Parse the first 10 characters of ``value`` as an ISO date, or ``None``."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def version_dates(state: dict[str, Any], slug: str, *, include_history: bool = False) -> list[date]:
    """Sorted, de-duplicated release dates recorded for ``slug``.

    The per-app ``versions`` list is capped at three entries
    (``upstream.keepVersions``), so on its own it can only ever describe the
    two most recent gaps. ``include_history`` merges in the app's rows from the
    shared ``state["updateHistory"]`` timeline — 100 events across the whole
    catalog, kept by :func:`omnisource.pipeline._remember_update` — which is the
    only place a longer cadence series exists.

    Callers that publish a *release series* (a per-version feed, a download
    record) must leave it off: the timeline is keyed by app and its rows carry no
    version metadata of their own, so it is for interval arithmetic. The
    reputation module is the caller that wants the longer series; ``compare`` and
    ``health_score`` publish the per-app window on purpose and can opt in here if
    that definition ever changes.

    Both sources describe the same transitions and are stored in the same field
    name (``releaseDate`` / ``date`` = the upstream date of the release), so the
    union cannot invent an event. It can only fill in releases the per-app
    window has already aged out; duplicates collapse.
    """
    parsed: list[date] = []
    versions = (state.get(slug) or {}).get("versions") or []
    if isinstance(versions, list):
        for version in versions:
            if isinstance(version, dict):
                when = parse_date(version.get("date"))
                if when is not None:
                    parsed.append(when)
    if include_history:
        history = state.get("updateHistory") or []
        if isinstance(history, list):
            for event in history:
                if isinstance(event, dict) and str(event.get("appId") or "") == slug:
                    when = parse_date(event.get("releaseDate"))
                    if when is not None:
                        parsed.append(when)
    return sorted(set(parsed))


def average_update_gap_days(state: dict[str, Any], slug: str, *, include_history: bool = False) -> float:
    """Average gap (days) between consecutive releases; 0.0 without history.

    A two-sample interval measured on a list the upstream truncates is not a
    cadence, so callers that publish this as a *frequency* should pass
    ``include_history=True`` and get the longer shared timeline.
    """
    dates = version_dates(state, slug, include_history=include_history)
    if len(dates) < 2:
        return 0.0
    deltas = [(later - earlier).days for earlier, later in pairwise(dates) if (later - earlier).days > 0]
    if not deltas:
        return 0.0
    return sum(deltas) / len(deltas)


def days_since(value: Any, *, today_iso: str) -> int:
    """Days between ``value`` and ``today_iso``; a large sentinel when unparsable."""
    when = parse_date(value)
    if when is None:
        return 3650
    end = parse_date(today_iso)
    if end is None:
        return 3650
    return max(0, (end - when).days)
