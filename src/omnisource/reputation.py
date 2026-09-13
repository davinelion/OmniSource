"""Source reputation engine.

A "source" in OmniSource is the upstream identity that publishes a given
app. Reputation is an explainable 0..100 score built from six signals:

* **JSON validity** — every app of the source has a well-formed feed entry
  (bundle id, version, download URL) in the published ``apps.json`` shape.
* **Uptime** — fraction of health probes that found the download reachable
  (30-day rolling window; current probe used when no history exists).
* **Update frequency** — average gap (in days) between consecutive releases,
  plus the age of the newest release.
* **Broken links** — releases that were later removed / re-published
  (rolled back, yanked, or replaced within 24 hours) and downloads that the
  latest health pass could not reach.
* **Release activity** — number of releases published in the past year.
* **Metadata completeness** — share of the source's apps that fill in every
  display-critical field (name, description, icon, category, subtitle).

Score bands keep the long-standing numeric ``level`` labels (consumed by
``js/site.js`` and the badges):

* **TRUSTED**      85..100
* **RELIABLE**     70..84
* **AVERAGE**      50..69
* **EXPERIMENTAL**  0..49

On top of the score, every source carries an explicit lifecycle ``status``
(Phase 5 of the modernization brief) that the website renders as a badge:

* **Verified**          score >= 85, valid JSON, healthy uptime
* **Community Verified** score >= 70, valid JSON
* **Maintained**        active but below the verified bar
* **Warning**           broken links / failing downloads / invalid metadata
* **Inactive**          no release in more than a year
* **Deprecated**        every app of the source is unmaintained/deprecated

The document is consumed by the website (badges throughout), the static
``sources/<slug>/`` pages, ``feeds/reputation.json`` and the
``api/reputation.json`` mirror.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from omnisource.discovery import source_label
from omnisource.domain import App, Catalog, today
from omnisource.utils.dates import average_update_gap_days as _update_frequency
from omnisource.utils.dates import days_since, parse_date, version_dates
from omnisource.utils.health import WINDOW_DAYS
from omnisource.utils.health import probe_window as _health_window

REPUTATION_SCHEMA_VERSION = 2
LEVELS = ("TRUSTED", "RELIABLE", "AVERAGE", "EXPERIMENTAL")
#: Lifecycle statuses (modernization Phase 5). Ordered best -> worst; the
#: numeric ``level`` above is kept for backwards compatibility.
STATUSES = ("Verified", "Community Verified", "Maintained", "Warning", "Inactive", "Deprecated")

#: Weights of the six signals — they sum to 100.
SCORE_WEIGHTS = {
    "jsonValidity": 20,
    "uptime": 25,
    "updateFrequency": 20,
    "releaseActivity": 15,
    "metadataCompleteness": 15,
    "brokenLinks": 5,
}

#: Days without a release before a source is considered inactive.
INACTIVE_AFTER_DAYS = 365
#: Sources with a fresher cadence than this can reach the Verified band.
VERIFIED_UPDATE_DAYS = 180


def _level_for(score: float) -> str:
    if score >= 85:
        return "TRUSTED"
    if score >= 70:
        return "RELIABLE"
    if score >= 50:
        return "AVERAGE"
    return "EXPERIMENTAL"


def _cadence_dates(state: dict[str, Any], slug: str) -> list:
    """Release dates used for cadence, age and window counts.

    The per-app ``versions`` list holds at most ``upstream.keepVersions`` entries
    — three, and one for most of this catalog — so scoring a source's cadence on
    it alone left 77 of 78 sources with ``updateFrequencyDays: null`` while the
    same releases sat in the shared ``updateHistory`` timeline, which keeps 100
    events across all apps. Both are merged (de-duplicated) here, so an app that
    updates often is finally measured over the series it actually has.
    """
    return version_dates(state, slug, include_history=True)


def _broken_releases(state: dict[str, Any], slug: str) -> int:
    broken = (state.get(slug) or {}).get("brokenReleases")
    if isinstance(broken, int):
        return max(0, broken)
    # Fall back to a heuristic: count the number of times a release was
    # published and then superseded within 48 hours. This is rare but
    # usually indicates a bad release.
    if len(state.get(slug, {}).get("versions") or []) < 2:
        return 0
    dates = _cadence_dates(state, slug)[::-1]
    rolled = 0
    for i in range(1, len(dates)):
        delta = (dates[i - 1] - dates[i]).days
        if 0 <= delta <= 2:
            rolled += 1
    return rolled


def _releases_in_window(state: dict[str, Any], slug: str, *, within_days: int, today_iso: str) -> int:
    """Releases published within ``within_days`` of ``today_iso``."""
    start = parse_date(today_iso)
    if start is None:
        return 0
    floor = start - timedelta(days=within_days)
    return sum(1 for when in _cadence_dates(state, slug) if floor <= when <= start)


def _app_has_valid_entry(app: App, state: dict[str, Any]) -> bool:
    """A source's entry is valid when the app carries a complete AltStore
    identity: bundle id, display name and at least one tracked version with a
    download URL (the shape the published ``apps.json`` requires)."""
    if not app.bundle_id or not app.name:
        return False
    versions = (state.get(app.slug) or {}).get("versions")
    if not isinstance(versions, list) or not versions:
        return False
    return any(
        isinstance(v, dict) and v.get("version") and str(v.get("downloadURL") or "").startswith(("http://", "https://"))
        for v in versions
    )


def _metadata_completeness(app: App) -> int:
    """0..1: share of the display-critical fields this app fills in."""
    fields = (
        bool(app.name),
        bool(str(app.raw.get("subtitle") or "").strip() or len(str(app.description or "")) >= 40),
        bool(app.icon),
        bool(app.category),
        bool(app.short_description or app.description),
        bool(app.developer),
    )
    return sum(1 for ok in fields if ok) * (100 // len(fields))


def _status_for(
    *,
    score: float,
    json_valid: float,
    broken: int,
    unreachable: int,
    last_release_age: int,
    cadence_days: float,
    unmaintained: bool,
) -> str:
    """Lifecycle status derived from the raw signals (Phase 5 rules)."""
    if unmaintained:
        return "Deprecated"
    if last_release_age > INACTIVE_AFTER_DAYS:
        return "Inactive"
    if json_valid < 0.999 or broken > 0 or unreachable > 0:
        return "Warning"
    # Cadence gate: a source qualifies as Verified on its average release gap
    # when there is enough history to compute one; with a single tracked
    # release the age of that release is used instead.
    effective_cadence = cadence_days if cadence_days > 0 else last_release_age
    if score >= 85 and effective_cadence <= VERIFIED_UPDATE_DAYS:
        return "Verified"
    if score >= 70:
        return "Community Verified"
    return "Maintained"


def _score_source(*, uptime: float, avg_delta: float, broken: int, sample_size: int) -> float:
    """Legacy 4-signal score kept for the ``metrics.legacyScore`` field."""
    # Uptime is the dominant signal (0..100 weighted at 0.5).
    score = uptime * 100 * 0.5
    # Update frequency: every 30 days is "neutral", 7 days is great, 180+ is
    # cold. We clamp the contribution to 0..25.
    if avg_delta <= 0:
        frequency_component = 0.0
    else:
        # Smooth curve: peak at 7 days, decays to 0 at 180 days.
        peak = 25.0
        frequency_component = max(0.0, peak * (1 - abs(avg_delta - 7) / 180))
        frequency_component = min(peak, frequency_component)
    score += frequency_component
    # Broken releases: each is a -3 penalty, capped at -15.
    score -= min(15.0, broken * 3.0)
    # Sample size bonus: a window of 30+ probes earns 10, 10..29 earns 5.
    if sample_size >= 30:
        score += 10
    elif sample_size >= 10:
        score += 5
    # Clamp.
    return round(max(0.0, min(100.0, score)), 2)


def _compose_score(
    *,
    json_valid_ratio: float,
    uptime: float,
    avg_delta: float,
    releases_year: int,
    completeness: float,
    broken_total: int,
) -> float:
    """Six-signal weighted score in ``SCORE_WEIGHTS`` (0..100)."""
    weight = SCORE_WEIGHTS
    score = weight["jsonValidity"] * (1.0 if json_valid_ratio >= 0.999 else json_valid_ratio)
    score += weight["uptime"] * max(0.0, min(1.0, uptime))
    if avg_delta <= 0:
        cadence = 0.35  # no release history: neutral-low, not zero
    elif avg_delta <= 7:
        cadence = 1.0
    elif avg_delta <= 30:
        cadence = 0.9
    elif avg_delta <= 90:
        cadence = 0.7
    elif avg_delta <= 180:
        cadence = 0.45
    else:
        cadence = 0.1
    score += weight["updateFrequency"] * cadence
    score += weight["releaseActivity"] * min(1.0, releases_year / 6)
    score += weight["metadataCompleteness"] * (completeness / 100)
    score += weight["brokenLinks"] * max(0.0, 1.0 - broken_total * 0.25)
    return round(max(0.0, min(100.0, score)), 1)


def build_reputation_doc(
    catalog: Catalog,
    state: dict[str, Any],
    health_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``feeds/reputation.json``."""
    health_doc = health_doc or {}
    health_by_slug = {item.get("slug"): item for item in health_doc.get("apps", []) if isinstance(item, dict)}

    grouped: dict[str, dict[str, Any]] = {}
    for app in catalog.apps:
        verification = app.raw.get("verification", {}) if isinstance(app.raw.get("verification"), dict) else {}
        upstream = app.upstream
        identity = (upstream.identity if upstream is not None else None) or f"manual:{app.slug}"
        entry = grouped.setdefault(
            identity,
            {
                "id": identity,
                "source": source_label(app),
                "publisher": verification.get("publisher", ""),
                # Link of the source itself (the repo/feed that publishes the
                # sideload IPAs) - not the official page of one of its apps.
                "homepage": app.source_url or app.repository_url or app.homepage,
                "sourceURL": app.source_url or app.repository_url or app.homepage,
                "apps": [],
            },
        )
        entry["apps"].append(app.slug)

    today_iso = today()
    sources: list[dict[str, Any]] = []
    for identity, info in sorted(grouped.items(), key=lambda item: item[1]["source"].casefold()):
        app_slugs: list[str] = info["apps"]
        apps_by_slug = {app.slug: app for app in catalog.apps if app.slug in set(app_slugs)}
        # Aggregate health across all of the source's apps.
        total = 0
        reachable = 0
        latencies: list[int] = []
        sample_size = 0
        for slug in app_slugs:
            app_state = state.get(slug) if isinstance(state.get(slug), dict) else state
            history_total, history_reachable, avg_latency = _health_window(app_state)
            sample_size += history_total
            total += history_total
            reachable += history_reachable
            if avg_latency is not None:
                latencies.append(int(avg_latency))
        # Use the most recent health document for current reachability.
        currently_reachable = 0
        unreachable = 0
        for slug in app_slugs:
            item = health_by_slug.get(slug) or {}
            if item.get("downloadReachable") is True:
                currently_reachable += 1
            elif item.get("downloadReachable") is False:
                unreachable += 1
        uptime = reachable / total if total else (currently_reachable / len(app_slugs) if app_slugs else 0.0)
        avg_latency = sum(latencies) / len(latencies) if latencies else None
        # Update frequency: average over the source's apps.
        deltas = [_update_frequency(state, slug, include_history=True) for slug in app_slugs]
        active_deltas = [d for d in deltas if d > 0]
        avg_delta = sum(active_deltas) / len(active_deltas) if active_deltas else 0.0
        # Newest release age across the source.
        ages = [days_since(max(_cadence_dates(state, slug), default=None), today_iso=today_iso) for slug in app_slugs]
        ages = [age for age in ages if age < 3650]
        last_release_age = min(ages) if ages else 3650
        broken = sum(_broken_releases(state, slug) for slug in app_slugs)
        releases_year = sum(
            _releases_in_window(state, slug, within_days=365, today_iso=today_iso) for slug in app_slugs
        )
        # JSON validity + metadata completeness over the source's apps.
        valid_apps = sum(1 for slug, app in apps_by_slug.items() if _app_has_valid_entry(app, state))
        json_valid_ratio = valid_apps / len(app_slugs) if app_slugs else 0.0
        completeness = (
            sum(_metadata_completeness(app) for app in apps_by_slug.values()) // len(apps_by_slug)
            if apps_by_slug
            else 0
        )
        # Deprecated when every app of the source is archived/inactive in the
        # catalog lifecycle sense (status unmaintained/deprecated).
        lifecycles = {apps_by_slug[slug].lifecycle_status for slug in app_slugs if slug in apps_by_slug}
        unmaintained = bool(lifecycles) and lifecycles <= {"archived", "inactive"}

        score = _compose_score(
            json_valid_ratio=json_valid_ratio,
            uptime=uptime,
            avg_delta=avg_delta,
            releases_year=releases_year,
            completeness=completeness,
            broken_total=broken + unreachable,
        )
        level = _level_for(score)
        status = _status_for(
            score=score,
            json_valid=json_valid_ratio,
            broken=broken,
            unreachable=unreachable,
            last_release_age=last_release_age,
            cadence_days=avg_delta,
            unmaintained=unmaintained,
        )
        sources.append(
            {
                "id": identity,
                "source": info["source"],
                "publisher": info["publisher"],
                "homepage": info["homepage"],
                "sourceURL": info["sourceURL"],
                "apps": info["apps"],
                "score": score,
                "level": level,
                "status": status,
                "lastUpdate": today_iso if last_release_age == 0 else _iso_minus_days(today_iso, last_release_age),
                "signals": {
                    "jsonValidity": round(json_valid_ratio * 100, 1),
                    "uptime": round(uptime * 100, 2),
                    "updateFrequencyDays": round(avg_delta, 1) if avg_delta else None,
                    "lastReleaseDaysAgo": None if last_release_age >= 3650 else last_release_age,
                    "brokenLinks": broken + unreachable,
                    "releasesPastYear": releases_year,
                    "metadataCompleteness": int(completeness),
                    "probes": total,
                },
                "metrics": {
                    "uptime": round(uptime * 100, 2),
                    "probes": total,
                    "averageLatencyMs": round(avg_latency, 1) if avg_latency is not None else None,
                    "averageUpdateGapDays": round(avg_delta, 1) if avg_delta else None,
                    "brokenReleases": broken,
                    "currentlyReachable": f"{currently_reachable}/{len(app_slugs)}",
                    "legacyScore": _score_source(
                        uptime=uptime, avg_delta=avg_delta, broken=broken, sample_size=sample_size
                    ),
                },
            }
        )
    sources.sort(key=lambda item: (item["score"], item["source"].casefold()), reverse=True)

    return {
        "schemaVersion": REPUTATION_SCHEMA_VERSION,
        "generatedAt": today_iso,
        "count": len(sources),
        "levels": list(LEVELS),
        "statuses": list(STATUSES),
        "weights": SCORE_WEIGHTS,
        "windowDays": WINDOW_DAYS,
        "sources": sources,
    }


def _iso_minus_days(today_iso: str, days: int) -> str:
    """ISO date ``days`` before ``today_iso`` (falls back to ``today_iso``)."""
    base = parse_date(today_iso)
    if base is None:
        return str(today_iso)
    return (base - timedelta(days=days)).isoformat()
