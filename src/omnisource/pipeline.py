"""Pipeline orchestrator.

Stages
------
1. ``sync``       Resolve upstream releases through the provider registry.
2. ``health``     Probe every download URL concurrently.
3. ``build``      Render the AltStore Source v2 feeds (per-app + ``apps.json``).
4. ``readme``     Refresh the generated catalog block inside README.md.

``feeds/`` is the single source of truth: the published flat URLs
(``/<file>.json``, ``/<file>.xml``, ``/api/*``) are byte-identical copies that
:func:`omnisource.site.publish_repo_artifacts` refreshes at the end of every
run, because GitHub Pages serves this repository from the branch itself.

A failing upstream degrades to "keep serving the last good build"; an entry
that has no cached build yet (a brand-new catalog row) falls back to its
``manualRelease`` snapshot instead of disappearing. The pipeline is idempotent:
unchanged payloads are not written.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from omnisource.analytics import build_analytics_doc, remember_analytics_snapshot
from omnisource.api_v2 import build_api_v2_documents
from omnisource.app_pages import build_app_pages
from omnisource.app_schema import build_app_records, validate_app_records
from omnisource.assets import DirectoryCache, build_asset_manifest_doc, inspect_catalog
from omnisource.collections import build_collection_pages, build_collections_doc
from omnisource.community import build_community_doc
from omnisource.compare import build_compare_doc
from omnisource.constants import README_MARKERS, README_STATS_MARKERS
from omnisource.dead_apps import build_dead_apps_doc
from omnisource.di import Container, build_container
from omnisource.discovery import build_discovery_doc
from omnisource.docs_index import build_docs_index
from omnisource.domain import App, Catalog, SyncReport, UpdateEvent, today
from omnisource.download_intel import build_download_intel_doc
from omnisource.duplicates import build_duplicates_doc
from omnisource.errors import ConfigurationError, ProviderError, SyncError
from omnisource.feeds.altstore import (
    feed_envelope,
    render_altstore_app,
    render_badge_docs,
    render_health_doc,
    render_news_items,
)
from omnisource.feeds.rss import render_app_rss_feed, render_rss_feed
from omnisource.feeds.updates import render_updates_doc
from omnisource.health_score import annotate_health_doc, build_health_scores
from omnisource.http import ProbeResult
from omnisource.install import build_install_doc
from omnisource.integrity import build_integrity_doc, verify_downloads
from omnisource.io import atomic_write_many, atomic_write_text, read_json, write_json
from omnisource.logutil import Group, log
from omnisource.monitor import build_status_doc, remember_probe
from omnisource.providers.failover import FailoverChain
from omnisource.related import build_related_doc
from omnisource.reports import write_reports
from omnisource.reputation import build_reputation_doc
from omnisource.screenshots import process_screenshots
from omnisource.search_index import build_search_index
from omnisource.site import publish_repo_artifacts
from omnisource.source_pages import build_source_pages, build_sources_doc
from omnisource.tracking import compile_version_pattern, detect_update, select_versions
from omnisource.translations import build_translation_status_doc
from omnisource.trending import build_trending_doc
from omnisource.utils.versioning import compare_versions
from omnisource.verification import build_verification_doc


def _reuse_or(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    existing = read_json(path)
    return existing if isinstance(existing, dict) else fallback


def load_catalog(container: Container) -> Catalog:
    raw = read_json(container.paths.catalog)
    if not isinstance(raw, dict):
        raise SyncError(f"{container.paths.catalog.name} is missing or not a JSON object")
    return Catalog.from_dict(raw)


def load_state(container: Container) -> dict[str, Any]:
    raw = read_json(container.paths.feeds / "state.json")
    return raw if isinstance(raw, dict) else {}


def _compile_pattern(app: App) -> re.Pattern[str] | None:
    up = app.upstream
    if up is None:
        return None
    if up.version_pattern:
        # Fail fast on the app, not deep inside version rendering.
        try:
            compile_version_pattern(up.version_pattern)
        except ConfigurationError as error:
            raise SyncError(f"{app.slug}: {error}") from error
    if not up.asset_name_pattern:
        return None
    try:
        return re.compile(up.asset_name_pattern)
    except re.error as error:
        raise SyncError(f"{app.slug}: upstream.assetNamePattern does not compile: {error}") from error


def _manual_versions(app: App, reason: str) -> list[dict[str, Any]] | None:
    manual = app.manual_release
    if manual:
        log.info("%-14s %s - using manualRelease v%s from catalog.json", app.slug, reason, manual.get("version"))
        return [manual]
    log.warning("%-14s %s and no manualRelease fallback exists", app.slug, reason)
    return None


def sync_app(
    container: Container,
    app: App,
    *,
    incremental: bool,
    previous: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return ``(versions, resolved_source)`` for ``app``.

    ``versions is None`` means "keep previous state". ``resolved_source``
    names the failover leg that actually supplied the versions ("" for
    manual/cached fallbacks), recorded in state for the status board.
    """
    up = app.upstream
    previous_versions = (previous or {}).get("versions") if isinstance(previous, dict) else None
    previous_url = None
    if isinstance(previous_versions, list) and previous_versions:
        previous_url = previous_versions[0].get("downloadURL")

    if up is None:
        return _manual_versions(app, "no upstream configured"), "manual"

    chain = FailoverChain.from_ref(container.providers, up)
    try:
        result = chain.fetch_releases(previous_latest_url=previous_url, incremental=incremental)
    except (ProviderError, ConfigurationError) as error:
        # Every failover leg errored. ``feeds/state.json`` is the usual last
        # leg, but a brand-new entry has no cached snapshot yet: fall back to
        # the catalog's manualRelease so the app is not dropped from the build
        # (and so a fresh clone still publishes it) while the upstream is
        # unreachable. Entries that do have cached state keep it untouched.
        if not previous_versions:
            manual = _manual_versions(app, f"upstream unreachable ({error})")
            if manual:
                return manual, "manual"
        raise SyncError(str(error)) from error
    releases = result.releases

    if incremental and previous_versions and not releases:
        log.info("%-14s incremental hit - keeping v%s", app.slug, previous_versions[0].get("version"))
        return None, None

    if not releases:
        manual = _manual_versions(app, f"no published release with a matching asset in {up.repo or up.feed_url}")
        return manual, "manual" if manual else ""

    versions = select_versions(app_name=app.name, ref=up, releases=releases, pattern=_compile_pattern(app))
    if not versions:
        manual = _manual_versions(app, "upstream produced no usable version entries")
        return manual, "manual" if manual else ""

    log.info(
        "%-14s %-28s -> v%s (%d version(s))%s",
        app.slug,
        up.repo or up.feed_url,
        versions[0]["version"],
        len(versions),
        f" [failover: {result.attempts[-1]}]" if result.used_failover else "",
    )
    return versions, result.source


def _sync_result(
    container: Container,
    app: App,
    *,
    incremental: bool,
    previous: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]] | None, str | None, str | None]:
    """Worker boundary: one provider failure becomes one isolated result."""
    try:
        versions, source = sync_app(container, app, incremental=incremental, previous=previous)
        return app.slug, versions, None, source
    except Exception as error:  # provider failures must not stop sibling apps
        return app.slug, None, str(error), None


def _remember_update(state: dict[str, Any], event: UpdateEvent, *, limit: int) -> None:
    history = state.setdefault("updateHistory", [])
    if not isinstance(history, list):
        history = []
        state["updateHistory"] = history
    key = (event.app_id, event.version, event.kind)
    material = [
        item
        for item in history
        if isinstance(item, dict) and (item.get("appId"), item.get("version"), item.get("kind")) != key
    ]
    material.append(event.to_json())
    material.sort(key=lambda item: (str(item.get("releaseDate") or ""), str(item.get("appId") or "")), reverse=True)
    state["updateHistory"] = material[: max(1, limit)]


def stage_sync(
    container: Container,
    catalog: Catalog,
    state: dict[str, Any],
    *,
    only: set[str] | None,
    incremental: bool,
    report: SyncReport,
    workers: int | None = None,
) -> dict[str, Any]:
    """Synchronize apps independently and apply results in catalog order."""
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        log.warning("No GH_TOKEN/GITHUB_TOKEN set - using unauthenticated API limits (60 req/h)")

    selected = [app for app in catalog.apps if not only or app.slug in only]
    report.repositories_checked = len(selected)
    max_workers = max(1, workers or container.settings.sync_workers)
    previous_by_slug = {app.slug: state.get(app.slug) for app in selected}
    results: dict[str, tuple[list[dict[str, Any]] | None, str | None, str | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(selected)))) as pool:
        futures = {
            pool.submit(
                _sync_result,
                container,
                app,
                incremental=incremental,
                previous=previous_by_slug[app.slug],
            ): app
            for app in selected
        }
        for future in concurrent.futures.as_completed(futures):
            app = futures[future]
            try:
                slug, versions, error, source = future.result()
            except Exception as error:  # executor boundary; preserve all known state
                slug, versions, error, source = app.slug, None, str(error), None
            results[slug] = (versions, error, source)

    for app in selected:
        previous = previous_by_slug[app.slug]
        versions, error, source = results.get(app.slug, (None, "worker returned no result", None))
        if error is not None:
            log.error("%s: upstream sync failed (%s) - keeping last known state", app.slug, error)
            report.apps_failed += 1
            report.errors.append(f"{app.slug}: {error}")
            entry = state.setdefault(app.slug, {})
            entry["lastError"] = error
            entry["retryCount"] = int(entry.get("retryCount") or 0) + 1
            continue

        report.apps_synced += 1
        entry = state.setdefault(app.slug, {})
        entry.pop("lastError", None)
        entry["retryCount"] = 0
        if source:
            entry["lastResolvedSource"] = source
        previous_versions = (previous or {}).get("versions") if isinstance(previous, dict) else None
        if versions is None:
            report.apps_incremental_hit += 1
            continue

        kind = detect_update(previous_versions if isinstance(previous_versions, list) else None, versions)
        if entry.get("versions") != versions:
            previous_version = None
            if isinstance(previous_versions, list) and previous_versions:
                previous_version = str(previous_versions[0].get("version") or "") or None
            # Phase 8 signal: the upstream no longer offers the previously
            # newest version. Only a *regression* is a takedown — see
            # dead_apps._unexplained_removals, which is the other half of the
            # same rule and keeps already-recorded entries from escalating.
            if isinstance(previous_versions, list) and previous_versions and isinstance(versions, list):
                previous_latest = previous_versions[0] if isinstance(previous_versions[0], dict) else {}
                current_keys = {
                    (str(v.get("version") or ""), str(v.get("downloadURL") or ""))
                    for v in versions
                    if isinstance(v, dict)
                }
                previous_key = (
                    str(previous_latest.get("version") or ""),
                    str(previous_latest.get("downloadURL") or ""),
                )
                # keepVersions is 1 for nearly every app, so the previous latest
                # disappears from the set on every ordinary bump; a same-version
                # entry with a new URL is a failover between provider legs.
                # Neither means the release was pulled, so neither is recorded.
                still_newer = next((str(v.get("version") or "") for v in versions if isinstance(v, dict)), "")
                explained = bool(still_newer) and compare_versions(previous_key[0], still_newer) <= 0
                if explained and previous_key[0]:
                    entry.pop("removedReleases", None)
                elif previous_key not in current_keys:
                    removed = entry.setdefault("removedReleases", [])
                    if not isinstance(removed, list):
                        removed = []
                        entry["removedReleases"] = removed
                    removed.append(
                        {
                            "version": str(previous_latest.get("version") or ""),
                            "downloadURL": str(previous_latest.get("downloadURL") or ""),
                            "at": today(),
                        }
                    )
                    del removed[:-10]
                    log.warning("%s: previous latest %s no longer published upstream", app.slug, previous_version)
                else:
                    entry.pop("removedReleases", None)
            entry["versions"] = versions
            entry["syncedAt"] = today()
            if kind != "unchanged":
                report.apps_updated += 1
                event = UpdateEvent(
                    app_id=app.slug,
                    name=app.name,
                    version=str(versions[0].get("version") or ""),
                    previous_version=previous_version,
                    release_date=str(versions[0].get("date") or ""),
                    download_url=str(versions[0].get("downloadURL") or ""),
                    changelog=str(versions[0].get("localizedDescription") or ""),
                    kind=kind,
                )
                report.updates.append(event)
                _remember_update(state, event, limit=container.settings.max_update_history)
        elif incremental:
            report.apps_incremental_hit += 1

    report.api_requests = container.http.requests
    log.info("Sync complete using %d HTTP request(s)", container.http.requests)
    return state


def stage_health(
    container: Container,
    catalog: Catalog,
    state: dict[str, Any],
    *,
    enabled: bool,
    workers: int,
) -> None:
    targets = {
        app.slug: state[app.slug]["versions"][0]["downloadURL"]
        for app in catalog.apps
        if state.get(app.slug, {}).get("versions")
    }
    if not enabled:
        log.info("Link health probing disabled (--no-health); reusing stored results")
        return

    started = time.monotonic()

    def probe(url: str):
        probe_started = time.monotonic()
        result = container.http.probe(url, timeout=container.settings.health_timeout)
        elapsed_ms = (time.monotonic() - probe_started) * 1000.0
        return result, elapsed_ms

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, url): slug for slug, url in targets.items()}
        for future in concurrent.futures.as_completed(futures):
            slug = futures[future]
            try:
                result, latency_ms = future.result()
            except Exception as error:  # a probe must not cancel the other apps
                result = ProbeResult(False, f"probe failed: {error}", targets[slug])
                latency_ms = 0.0

            previous = state.get(slug, {}).get("health", {}) if isinstance(state.get(slug), dict) else {}
            if not isinstance(previous, dict):
                previous = {}
            since = previous.get("since", today()) if previous.get("reachable") == result.reachable else today()
            remember_probe(state, slug, reachable=result.reachable, detail=result.detail, latency_ms=latency_ms)
            state.setdefault(slug, {})["health"]["since"] = since
            (log.info if result.reachable else log.warning)(
                "%-14s %s (%s) in %dms",
                slug,
                "reachable" if result.reachable else "UNREACHABLE",
                result.detail,
                int(latency_ms),
            )
    log.info("Probed %d download URL(s) in %.1fs", len(targets), time.monotonic() - started)


def _days_since(iso_date: str, *, today_iso: str) -> int:
    """Whole days between an ISO date and today (0 when either is unparseable)."""
    try:
        start = date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return 0
    try:
        end = date.fromisoformat(str(today_iso)[:10])
    except ValueError:
        return 0
    return max(0, (end - start).days)


def _annotate_staleness(health_doc: dict[str, Any], *, stale_after_days: int) -> None:
    """Attach ``updatedDaysAgo`` and ``stale`` to every health entry."""
    today_iso = today()
    for entry in health_doc.get("apps", []):
        updated = str(entry.get("updatedAt") or "")
        days = _days_since(updated, today_iso=today_iso) if updated else 0
        entry["updatedDaysAgo"] = days
        entry["stale"] = bool(days > stale_after_days and entry.get("status") not in {"unmaintained", "deprecated"})


def stage_build(
    container: Container,
    catalog: Catalog,
    state: dict[str, Any],
    report: SyncReport,
    *,
    refresh_mirrors: bool = False,
) -> tuple[list[Path], dict[str, Any]]:
    rendered: list[tuple[App, dict[str, Any]]] = []

    for app in catalog.apps:
        versions = state.get(app.slug, {}).get("versions")
        if not isinstance(versions, list) or not versions:
            log.error("%s has no known versions - excluded from this build", app.slug)
            continue
        rendered.append((app, render_altstore_app(catalog, app, versions, state[app.slug].get("health", {}))))

    if not rendered:
        raise SyncError("No app produced a valid feed entry")

    feeds_dir = container.paths.feeds
    base = catalog.base_url
    documents: dict[Path, Any] = {}
    for app, entry in rendered:
        feed = feed_envelope(
            catalog,
            name=f"OmniSource - {app.name}",
            identifier=f"{catalog.source.get('identifier', 'com.omnisource')}.{app.slug}",
            subtitle=app.raw.get("subtitle", app.name),
            description=f"Standalone distribution feed for {app.name}, curated within OmniSource.",
        )
        feed["sourceURL"] = f"{base}/{app.slug}.json"
        feed["apps"] = [entry]
        feed["news"] = []
        documents[feeds_dir / f"{app.slug}.json"] = feed

    master = feed_envelope(
        catalog,
        name=str(catalog.source.get("name", "OmniSource")),
        identifier=str(catalog.source.get("identifier", "com.omnisource")),
        subtitle=str(catalog.source.get("subtitle", "")),
        description=str(catalog.source.get("description", "")),
    )
    master["apps"] = [entry for _, entry in sorted(rendered, key=lambda item: item[0].name.casefold())]
    master["news"] = render_news_items(catalog, state, limit=10)
    documents[feeds_dir / "apps.json"] = master

    health_doc = render_health_doc(rendered)
    _annotate_staleness(health_doc, stale_after_days=container.settings.stale_after_days)
    documents[feeds_dir / "health.json"] = health_doc

    # Hard gate: the normalized client records must satisfy the centralized
    # app schema before anything is published. A failing record means a
    # broken upstream or a catalog bug — publishing it would ship a
    # half-empty app to the website and OmniStore Pro clients.
    app_records = build_app_records(catalog, state, health_doc)
    rendered_slugs = {app.slug for app, _ in rendered}
    schema_report = validate_app_records(
        [record for record in app_records if record.get("id") in rendered_slugs],
        catalog,
        prefix="apps",
    )
    for warning in schema_report.warnings:
        log.warning("app-schema: %s", warning)
    if schema_report.errors:
        details = "; ".join(schema_report.errors[:8])
        raise SyncError(f"{len(schema_report.errors)} app record(s) violate schemas/app.schema.json: {details}")

    # Website updates timeline (sanitized history + newest versions).
    updates_doc = render_updates_doc(catalog, state, limit=max(10, container.settings.max_update_history))
    documents[feeds_dir / "updates.json"] = updates_doc

    # Dynamic badge documents
    badges = render_badge_docs(rendered, health_doc)
    for badge_name, badge_doc in badges.items():
        documents[feeds_dir / badge_name] = badge_doc

    # Derived intelligence documents: discovery catalog, source index,
    # verification levels, health status board, duplicate groups, analytics.
    # All of them are generated from the same in-memory dataset; none is
    # hand-edited. See docs/API.md for the contracts.
    discovery_doc = build_discovery_doc(catalog, state, health_doc)
    verification_doc = build_verification_doc(catalog, state, health_doc)
    # The source index (feeds/sources.json) is the Source Explorer's contract.
    # Enrichment fields (slug/page/status/score/health/…) are additive; the
    # base grouping stays owned by omnisource.discovery.
    sources_doc = build_sources_doc(catalog, state, health_doc=health_doc, verification_doc=verification_doc)
    status_doc = build_status_doc(catalog, state, health_doc)
    duplicates_doc = build_duplicates_doc(catalog, state)
    analytics_doc = build_analytics_doc(catalog, state, health_doc, verification_doc)
    # Rolling analytics snapshot lives in pipeline state (no external DB).
    # Record it before publishing so the document carries today's entry too.
    remember_analytics_snapshot(state, analytics_doc)
    history = state.get("analyticsHistory")
    if isinstance(history, list):
        analytics_doc["history"] = history[-30:]
    for name, doc in (
        ("discovery.json", discovery_doc),
        ("sources.json", sources_doc),
        ("verification.json", verification_doc),
        ("status.json", status_doc),
        ("duplicates.json", duplicates_doc),
        ("analytics.json", analytics_doc),
    ):
        documents[feeds_dir / name] = doc

    # Phase 1-7 intelligence documents. Each of them is derived from the
    # already-built health / verification / analytics documents and shares
    # the same atomic-write contract. Order matters: trending depends on
    # health+verification, related depends on state, reputation depends on
    # health+state, download-intel depends on health+state, community
    # depends on state, search index depends on health+verification, install
    # depends only on catalog, compare depends on health+verification.
    trending_doc = build_trending_doc(catalog, state, health_doc, verification_doc)
    related_doc = build_related_doc(catalog, state)
    reputation_doc = build_reputation_doc(catalog, state, health_doc)
    # sources.json carries the reputation summary per source so the /sources/
    # pages and the explorer need no extra join (reputation exists only from
    # here on; this is the one documented second pass of the additive build).
    sources_doc = build_sources_doc(
        catalog,
        state,
        reputation_doc=reputation_doc,
        health_doc=health_doc,
        verification_doc=verification_doc,
    )
    # (documents["feeds/sources.json"] is replaced with this enriched copy
    # in the second intelligence loop below — one dict, one writer.)
    download_intel_doc = build_download_intel_doc(catalog, state, health_doc)
    community_doc = build_community_doc(catalog, state)
    search_index_doc = build_search_index(catalog, state, health_doc, verification_doc)
    install_doc = build_install_doc(catalog)
    compare_doc = build_compare_doc(catalog, state, health_doc, verification_doc)
    # Phase 6/7/8 intelligence: per-app health score (30/25/20/15/10 formula),
    # trust score (inside verification_doc above) and dead-app classification.
    health_scores = build_health_scores(catalog, state, health_doc, reputation_doc)
    annotate_health_doc(health_doc, health_scores)
    # sources.json carries the reputation summary per source so the /sources/
    # pages and the explorer need no extra join (reputation/annotation exist
    # only from here on; the second pass replaces the pre-annotation copy in
    # the documents dict below — one dict, one writer).
    sources_doc = build_sources_doc(
        catalog,
        state,
        reputation_doc=reputation_doc,
        health_doc=health_doc,
        verification_doc=verification_doc,
    )
    integrity_doc = build_integrity_doc(catalog, state, health_doc)
    dead_apps_doc = build_dead_apps_doc(catalog, state)
    # Phase 10 curated collections (YouTube, Music, Emulators, Utilities,
    # Productivity) — static JSON plus generated pages in the site builder.
    collections_doc = build_collections_doc(catalog, state)
    # Asset manifest: icon health + placeholder fallback map for the website
    # AssetManager and the missing-assets report.
    asset_manifest_doc = build_asset_manifest_doc(catalog, assets_dir=container.paths.assets, base_url=catalog.base_url)
    # Translation coverage against the canonical en locale. A pure function
    # of the committed locale files (locales/*.json); CI only cross-checks
    # the committed document (scripts/validate-translations.js) and never
    # writes it. Checkouts without the website sources skip the document so
    # a feeds-only consumer stays buildable.
    locales_dir = container.paths.root / "locales"
    translation_doc: dict[str, int] | None
    try:
        translation_doc = build_translation_status_doc(locales_dir)
    except ValueError as exc:
        translation_doc = None
        if locales_dir.is_dir():
            log.warning("Skipping translation-status.json: %s", exc)
    # Screenshot pipeline (validation + mirror + thumbnail). The function
    # itself never raises; issues are recorded inside the resulting doc.
    # The previous document seeds keep-last-good: offline rebuilds reuse
    # mirror metadata they cannot re-download.
    previous_screenshots = read_json(feeds_dir / "screenshots.json")
    screenshot_report = process_screenshots(
        catalog,
        base_url=catalog.base_url,
        assets_dir=container.paths.assets,
        http=container.http,
        refresh=refresh_mirrors,
        previous=previous_screenshots if isinstance(previous_screenshots, dict) else None,
    )
    screenshot_doc = screenshot_report.to_doc()
    for name, doc in (
        ("trending.json", trending_doc),
        ("related.json", related_doc),
        ("reputation.json", reputation_doc),
        ("download-intelligence.json", download_intel_doc),
        ("community.json", community_doc),
        ("search-index.json", search_index_doc),
        ("install.json", install_doc),
        ("compare.json", compare_doc),
        ("screenshots.json", screenshot_doc),
        ("integrity_report.json", integrity_doc),
        ("dead_apps.json", dead_apps_doc),
        ("collections.json", collections_doc),
        ("asset-manifest.json", asset_manifest_doc),
        ("sources.json", sources_doc),
    ):
        documents[feeds_dir / name] = doc
    if translation_doc is not None:
        documents[feeds_dir / "translation-status.json"] = translation_doc

    # OmniStore Pro API contract (feeds/api/v2/): versioned endpoints +
    # feed manifest with per-document checksums for incremental sync.
    for rel_path, doc in build_api_v2_documents(
        catalog,
        state,
        health_doc=health_doc,
        verification_doc=verification_doc,
        updates_doc=updates_doc,
    ).items():
        documents[feeds_dir / rel_path] = doc

    # Additional Shields.io-compatible badges for the README.
    documents[feeds_dir / "badge-sync.json"] = {
        "schemaVersion": 1,
        "label": "last sync",
        "message": analytics_doc.get("lastSync") or "pending",
        "color": "5b5bd6",
    }
    verified = int(analytics_doc["totals"]["verifiedApps"])
    documents[feeds_dir / "badge-verified.json"] = {
        "schemaVersion": 1,
        "label": "verified",
        "message": f"{verified}/{len(catalog.apps)}",
        "color": "2ea043" if verified == len(catalog.apps) else "d29922",
    }

    # Keep every generated JSON feed vertical/readable. The canonical files
    # live in feeds/; publish_repo_artifacts() (end of run) mirrors them to the
    # flat/API URL families at the repository root and build_site() assembles
    # them into _site/, so this stage only ever writes feeds/.
    changed = atomic_write_many(documents, pretty=lambda path: True)

    # RSS 2.0 / Atom XML feeds: one combined feed plus a per-app feed.
    rss_content = render_rss_feed(catalog, state, limit=25)
    for rss_name in ("feed.xml", "rss.xml"):
        rss_path = feeds_dir / rss_name
        if atomic_write_text(rss_path, rss_content):
            changed.append(rss_path)
    for app, _entry in rendered:
        app_rss_path = feeds_dir / f"{app.slug}.xml"
        app_rss_content = render_app_rss_feed(catalog, state, app.slug, limit=15)
        if app_rss_content and atomic_write_text(app_rss_path, app_rss_content):
            changed.append(app_rss_path)

    # Static app detail pages (apps/<slug>/index.html).
    page_dir = container.paths.root / "apps"
    changed.extend(
        build_app_pages(
            catalog,
            state,
            health_doc,
            verification_doc,
            duplicates_doc,
            pages_dir=page_dir,
            related_doc=related_doc,
            install_doc=install_doc,
        )
    )

    # Static per-pair compare pages (compare/<a>-vs-<b>/) removed in v2:
    # the /compare/ page renders every pair client-side from feeds/compare.json
    # via the compare engine (src/js/compare-engine.js). This eliminates
    # ~5,800 generated HTML files and ~35 MB from the repository. Legacy
    # /compare/<slug>-vs-<slug>/ URLs still resolve through the dynamic
    # engine thanks to the client-side router and a catch-all redirect page
    # placed by the site builder.

    # Static collection pages: one /collections/<slug>/ per curated collection
    # (Phase 10).
    changed.extend(build_collection_pages(catalog, state, pages_dir=container.paths.root / "collections"))

    # Static Source Explorer pages (sources/<slug>/index.html, Phase 4),
    # rendered from the enriched source index built above.
    changed.extend(build_source_pages(catalog, sources_doc, pages_dir=container.paths.root / "sources"))

    log.info(
        "Built %d AltStore feed(s) + apps.json + health.json + updates.json + badges + RSS + "
        "discovery/verification/status/duplicates/analytics + "
        "trending/related/reputation/download-intel/community/search-index/install/compare/screenshots + "
        "asset-manifest/api-v2-contract + "
        "%d app page(s) (%d file(s) changed)",
        len(rendered),
        len(rendered),
        len(changed),
    )
    return changed, health_doc, analytics_doc


def _refresh_block(text: str, markers: tuple[str, str], block: str) -> tuple[str, bool]:
    start, end = markers
    if start not in text or end not in text:
        return text, False
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
    updated = pattern.sub(lambda _: block, text)
    return updated, updated != text


def stage_readme(
    container: Container,
    catalog: Catalog,
    health_doc: dict[str, Any],
    analytics_doc: dict[str, Any] | None = None,
) -> bool:
    readme = container.paths.readme
    if not readme.exists():
        return False
    start, end = README_MARKERS
    text = readme.read_text(encoding="utf-8")
    if start not in text or end not in text:
        log.debug("README has no generated catalog block - skipping")
        return False

    totals = health_doc["totals"]
    block = "\n".join(
        [
            start,
            "",
            f"Browse the full **{totals['apps']}-app catalog** on the [website]({catalog.base_url}/#catalog) — "
            f"all installable with one tap.",
            "",
            "- Combined feeds: [`feeds/apps.json`](./feeds/apps.json), "
            "[`feeds/feed.xml`](./feeds/feed.xml), [`Catalog.json`](./Catalog.json)",
            "- Substrate docs: [`feeds/sources.json`](./feeds/sources.json), "
            "[`feeds/discovery.json`](./feeds/discovery.json), [`feeds/health.json`](./feeds/health.json), "
            "[`feeds/updates.json`](./feeds/updates.json), [`feeds/verification.json`](./feeds/verification.json)",
            "- Per-app feeds at `feeds/<slug>.json` and `feeds/<slug>.xml` — e.g. "
            "[`feeds/esign.json`](./feeds/esign.json)",
            "",
            f"_Last sync {health_doc['generatedAt']} · {totals['reachable']}/{totals['apps']} downloads reachable._",
            "",
            end,
        ]
    )
    updated, _ = _refresh_block(text, README_MARKERS, block)

    # Live statistics block, refreshed from the analytics document.
    analytics_doc = analytics_doc or build_analytics_doc(catalog, load_state(container), health_doc)
    analytics_totals = analytics_doc["totals"]
    stats_block = "\n".join(
        [
            README_STATS_MARKERS[0],
            "",
            f"**{analytics_totals['apps']}** apps · **{analytics_totals['sources']}** upstream sources · "
            f"**{analytics_totals['verifiedApps']}** verified · "
            f"**{analytics_totals['communityVerifiedApps']}** community verified · "
            f"**{analytics_totals['downloadsReachable']}/{analytics_totals['apps']}** downloads online · "
            f"last sync **{analytics_doc.get('lastSync') or 'pending'}**.",
            "",
            README_STATS_MARKERS[1],
        ]
    )
    updated, _ = _refresh_block(updated, README_STATS_MARKERS, stats_block)

    if updated == text:
        return False
    changed = atomic_write_text(readme, updated)
    if changed:
        log.info("README catalog + stats block refreshed")
    return changed


def stage_assets(container: Container, catalog: Catalog) -> None:
    report = inspect_catalog(catalog, assets_dir=container.paths.assets)
    for issue in report.issues:
        if issue.kind in {"missing"}:
            log.error("asset: %s %s", issue.slug or issue.path, issue.detail)
        elif issue.kind in {"oversized", "screenshot", "unused", "icon"}:
            log.warning("asset: %s %s", issue.slug or issue.path, issue.detail)
    # Touch the cache directory so operators know where it will live.
    DirectoryCache(container.paths.cache)


def write_summary(health_doc: dict[str, Any], changed: list[Path], report: SyncReport) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    totals = health_doc["totals"]
    lines = [
        "## OmniSource build summary",
        "",
        f"- **Apps:** {totals['apps']}",
        f"- **Downloads reachable:** {totals['reachable']}/{totals['apps']}",
        (
            f"- **Synced:** {report.apps_synced} · incremental hits: "
            f"{report.apps_incremental_hit} · failed: {report.apps_failed}"
        ),
        f"- **Version changes:** {report.apps_updated}",
        f"- **HTTP requests:** {report.api_requests}",
        f"- **Files changed:** {len(changed)}",
        "",
        "### Catalog",
        "",
        "| App | Version | Updated | Download |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {item['name']} | `{item['version']}` | {item['updatedAt']} | "
        f"{'✅' if item['downloadReachable'] else '⚠️ ' + item['detail']} |"
        for item in health_doc["apps"]
    ]
    if report.updates:
        lines += ["", "### Release report", "", "| App | From | To | Kind |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {event.name} | `{event.previous_version or '—'} ` | `{event.version}` | {event.kind} |"
            for event in report.updates
        ]
    if report.errors:
        lines += ["", "### Errors", ""]
        lines += [f"- `{error}`" for error in report.errors]
    with Path(summary).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(
    *,
    container: Container | None = None,
    no_sync: bool = False,
    no_health: bool = False,
    verify: bool = False,
    only: set[str] | None = None,
    incremental: bool = False,
    workers: int = 8,
) -> tuple[int, SyncReport]:
    """Execute the pipeline. Returns ``(exit_code, report)``.

    ``verify`` enables the Phase 3 full-integrity pass (stream download +
    SHA-256 + size check of every app's newest asset) and makes the run fail
    when any download is rejected. Used by the weekly ``verify.yml`` job; the
    regular sync only performs the metadata-level checks.
    """
    container = container or build_container()
    report = SyncReport(started_at=today())

    catalog = load_catalog(container)
    report.apps_total = len(catalog.apps)
    loaded_state = load_state(container)
    known = {app.slug for app in catalog.apps}
    state = {slug: value for slug, value in loaded_state.items() if slug in known and isinstance(value, dict)}
    # State metadata is deliberately kept outside the app slug namespace.
    for key in ("updateHistory", "schemaVersion"):
        if key in loaded_state:
            state[key] = loaded_state[key]

    if not no_sync:
        with Group("Sync upstream releases"):
            state = stage_sync(
                container,
                catalog,
                state,
                only=only,
                incremental=incremental,
                report=report,
                workers=workers,
            )

    with Group("Check download health"):
        stage_health(container, catalog, state, enabled=not no_health, workers=max(1, workers))

    verify_failed: list[dict[str, Any]] = []
    if verify:
        with Group("Verify downloads (full integrity)"):
            records = verify_downloads(container, catalog, state, only=only, workers=max(1, min(workers, 4)))
            verify_failed = [record for record in records if not record.get("ok")]
            report.broken_assets += len(verify_failed)
            for record in verify_failed:
                report.errors.append(f"integrity: {record['slug']} rejected ({record.get('detail')})")

    with Group("Validate local assets"):
        stage_assets(container, catalog)

    with Group("Build feeds"):
        changed, health_doc, analytics_doc = stage_build(container, catalog, state, report, refresh_mirrors=not no_sync)

    # Persist state only after the complete generated dataset passed validation;
    # a failed build therefore leaves both data and memory at last-known-good.
    if write_json(container.paths.feeds / "state.json", dict(sorted(state.items()))):
        changed.append(container.paths.feeds / "state.json")

    if stage_readme(container, catalog, health_doc, analytics_doc):
        changed.append(container.paths.readme)

    # Monitoring ledger: per-build snapshot + rolling history. Written from
    # the same deterministic inputs as the feeds, so a rebuild from
    # committed state is a byte-level no-op (consecutive identical history
    # rows are skipped). A run that did not sync has no telemetry of its own,
    # so it carries the last sync's counters/updates/errors forward instead of
    # resetting them — otherwise the reproducibility gate in sync.yml fails on
    # every scheduled run and erases what the sync actually did.
    with Group("Write monitoring reports"):
        changed.extend(
            write_reports(
                root=container.paths.root,
                feeds_dir=container.paths.feeds,
                health_doc=health_doc,
                analytics_doc=analytics_doc,
                sync_report=report,
                sync_ran=not no_sync,
            )
        )

    # Publish the generated public URLs into the repository root. GitHub Pages
    # serves this repository in *branch* mode, so the live site is the tree
    # itself: without this mirror /apps.json — the URL installers add as a
    # source — and every other flat/API URL would 404. scripts/build_site.py
    # additionally assembles the same URLs into the _site/ artifact that
    # sync.yml uploads for the GitHub Actions deployment path.
    with Group("Publish repository-root URLs"):
        # Regenerate the docs hub from the Markdown on disk, then publish.
        build_docs_index(container.paths.root / "docs", base_url=catalog.base_url.rstrip("/"))
        published = publish_repo_artifacts(container.paths.root, health_doc=health_doc, analytics_doc=analytics_doc)
        log.info(
            "%d flat URL(s) + %d API document(s) published (%d refreshed, %d stale removed)",
            published["flat_files"],
            published["api_documents"],
            len(published["written"]),
            len(published["removed"]),
        )
        for name in published["removed"]:
            log.info("removed stale root artifact: %s", name)
    report.finished_at = today()
    report.files_changed = len(changed)
    write_summary(health_doc, changed, report)

    # Dispatch webhooks to Discord and Telegram if configured and updates occurred
    if report.updates:
        from omnisource.notify import dispatch_configured_notifications

        dispatch_configured_notifications(
            report.updates,
            source_name=str(catalog.source.get("name", "OmniSource")),
            base_url=catalog.base_url,
        )

    unreachable = health_doc["totals"]["unreachable"]
    if unreachable:
        log.warning("%d app(s) currently have an unreachable download URL", unreachable)
    if verify_failed:
        log.warning("%d app(s) FAILED full integrity verification", len(verify_failed))
    log.info("Done. %d file(s) changed.", len(changed))
    # A failing full-integrity verification is a hard gate (weekly verify job),
    # but a normal sync never enables it, so this only affects explicit runs.
    return (1 if verify_failed else 0), report
