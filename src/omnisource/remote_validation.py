"""Validation engine for autonomous discoveries.

:mod:`omnisource.validation` guards the hand-maintained ``catalog.json`` and
the generated AltStore feeds. This module guards everything the discovery
engine finds *before* it may enter the pipeline:

* :func:`validate_source_record` — the ``discovered_sources.json`` schema.
* :func:`validate_remote_feed` — a fetched feed envelope (any client).
* :func:`validate_remote_app` — one app entry (metadata + URLs).
* :func:`validate_remote_release` — one upstream release object.
* :func:`check_url_reachable` — optional live HEAD/GET reachability probe.

Invalid feeds must never be published: :func:`assert_publishable` aggregates
every rule and returns ``(ok, errors, warnings)`` so callers can quarantine
a source instead of merging it.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from omnisource.autodiscovery import KNOWN_TYPES
from omnisource.constants import USER_AGENT
from omnisource.http import is_http_url
from omnisource.validation import BUNDLE_RE, DATE_RE, SHA_RE, SOURCE_ID_RE, TINT_RE

ALLOWED_TYPES = frozenset(KNOWN_TYPES)
ALLOWED_HEALTH = frozenset({"unknown", "online", "degraded", "offline"})
INSTALLABLE_SUFFIXES = (".ipa", ".tipa")

REQUIRED_RECORD_FIELDS = (
    "source_id",
    "name",
    "url",
    "type",
    "discovered_at",
    "last_checked",
    "health",
    "reputation",
)
REQUIRED_FEED_FIELDS = ("name", "identifier", "apps")
REQUIRED_APP_FIELDS = (
    "name",
    "bundleIdentifier",
    "developerName",
    "version",
    "versionDate",
    "downloadURL",
    "localizedDescription",
)
REQUIRED_RELEASE_FIELDS = ("tag", "assets")


def _is_https(url: Any) -> bool:
    return isinstance(url, str) and url.startswith("https://") and is_http_url(url)


def validate_source_record(record: Any) -> list[str]:
    """Validate one ``discovered_sources.json`` entry."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object"]
    aliases = {
        "source_id": ("source_id", "sourceId"),
        "name": ("name", "source_name", "sourceName"),
        "url": ("url", "source_url", "sourceUrl"),
        "type": ("type", "source_type", "sourceType"),
        "discovered_at": ("discovered_at", "first_seen", "firstSeen"),
        "last_checked": ("last_checked", "last_seen", "lastSeen"),
        "health": ("health", "health_status", "healthStatus"),
        "reputation": ("reputation", "reputation_score", "reputationScore"),
    }
    values = {
        field: next((record.get(alias) for alias in names if record.get(alias) is not None), None)
        for field, names in aliases.items()
    }
    for field in REQUIRED_RECORD_FIELDS:
        if values.get(field) in (None, "") and values.get(field) != 0:
            errors.append(f"missing required field '{field}'")
    url = values.get("url", "")
    if url and not _is_https(url):
        errors.append("url must be an https URL")
    if values.get("type") not in ALLOWED_TYPES:
        errors.append(f"type must be one of {sorted(ALLOWED_TYPES)}")
    if values.get("health") not in ALLOWED_HEALTH:
        errors.append(f"health must be one of {sorted(ALLOWED_HEALTH)}")
    reputation = values.get("reputation")
    if not isinstance(reputation, int) or isinstance(reputation, bool) or not 0 <= reputation <= 100:
        errors.append("reputation must be an integer 0..100")
    source_id = values.get("source_id", "")
    if source_id and not SOURCE_ID_RE.match(str(source_id)):
        errors.append("source_id must be a lowercase kebab-case id of 2-80 characters")
    return errors


def validate_remote_feed(payload: Any, *, url: str = "") -> list[str]:
    """Validate a fetched feed envelope without trusting its origin."""
    errors: list[str] = []
    label = url or "feed"
    if not isinstance(payload, dict):
        return [f"{label}: feed must be a JSON object"]
    for field in REQUIRED_FEED_FIELDS:
        if field not in payload:
            errors.append(f"{label}: missing required field '{field}'")
    apps = payload.get("apps")
    if apps is not None and not isinstance(apps, list):
        errors.append(f"{label}: 'apps' must be an array")
        return errors
    if isinstance(apps, list):
        if not apps:
            errors.append(f"{label}: 'apps' must not be empty")
        seen_bundles: set[str] = set()
        for index, app in enumerate(apps):
            where = f"{label}#/apps/{index}"
            for error in validate_remote_app(app):
                errors.append(f"{where}: {error}")
            if isinstance(app, dict):
                bundle = str(app.get("bundleIdentifier", ""))
                if bundle:
                    if bundle in seen_bundles:
                        errors.append(f"{where}: duplicate bundleIdentifier '{bundle}'")
                    seen_bundles.add(bundle)
    tint = payload.get("tintColor")
    if tint is not None and not TINT_RE.match(str(tint).lstrip("#")):
        errors.append(f"{label}: tintColor must be a 6-digit hex string")
    return errors


def validate_remote_app(app: Any) -> list[str]:
    """Validate one app entry from a third-party feed."""
    errors: list[str] = []
    if not isinstance(app, dict):
        return ["app must be an object"]
    for field in REQUIRED_APP_FIELDS:
        if not app.get(field):
            errors.append(f"missing required field '{field}'")
    bundle = str(app.get("bundleIdentifier", ""))
    if bundle and not BUNDLE_RE.match(bundle):
        errors.append(f"bundleIdentifier '{bundle}' contains invalid characters")
    version = str(app.get("version", ""))
    if version and len(version) > 32:
        errors.append("version is suspiciously long (>32 chars)")
    date = str(app.get("versionDate", ""))
    if date and not DATE_RE.match(date):
        errors.append(f"versionDate '{date}' is not an ISO date")
    download = app.get("downloadURL", "")
    if download and not _is_https(download):
        errors.append("downloadURL must be an https URL")
    size = app.get("size")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
        errors.append("size must be a positive integer byte count")
    for field in ("iconURL", "website"):
        value = app.get(field)
        if value and not is_http_url(value):
            errors.append(f"{field} must be an http(s) URL")
    screenshots = app.get("screenshotURLs")
    if screenshots is not None:
        if not isinstance(screenshots, list):
            errors.append("screenshotURLs must be an array")
        else:
            for shot in screenshots:
                if not is_http_url(shot):
                    errors.append("screenshotURLs entries must be http(s) URLs")
                    break
    return errors


def validate_remote_release(release: Any) -> list[str]:
    """Validate one upstream release object (GitHub/GitLab/forge shape)."""
    errors: list[str] = []
    if not isinstance(release, dict):
        return ["release must be an object"]
    tag = release.get("tag") or release.get("tag_name") or release.get("name")
    if not tag:
        errors.append("release needs a tag/tag_name")
    assets = release.get("assets")
    if assets is not None and not isinstance(assets, list):
        errors.append("release 'assets' must be an array")
    elif isinstance(assets, list):
        installables = [
            asset
            for asset in assets
            if isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith(INSTALLABLE_SUFFIXES)
        ]
        if assets and not installables:
            errors.append("release has assets but no installable .ipa/.tipa")
        for asset in installables:
            url = asset.get("browser_download_url") or asset.get("url") or ""
            if url and not _is_https(url):
                errors.append(f"asset '{asset.get('name')}' URL must be https")
    digest = release.get("sha256") or release.get("digest") or ""
    if digest and not SHA_RE.match(str(digest).removeprefix("sha256:").strip()):
        errors.append("sha256 digest is malformed")
    return errors


def check_url_reachable(url: str, *, timeout: int = 12) -> tuple[bool, str]:
    """Return ``(reachable, detail)`` for an http(s) URL (HEAD, then GET)."""
    if not is_http_url(url):
        return False, "not an http(s) URL"
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 405) and method == "HEAD":
                continue  # HEAD blocked; retry with GET below.
            return False, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return False, str(exc) or "unreachable"
        if 200 <= code < 400:
            return True, f"HTTP {code}"
        return False, f"HTTP {code}"
    return False, "unreachable"


def assert_publishable(
    record: dict[str, Any],
    *,
    feed_payload: Any = None,
    check_downloads: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Decide whether a discovered source may enter the pipeline.

    Returns ``(ok, errors, warnings)``. ``ok`` is False whenever ``errors``
    is non-empty; warnings never block publication.
    """
    errors = validate_source_record(record)
    warnings: list[str] = []
    if feed_payload is not None:
        errors.extend(validate_remote_feed(feed_payload, url=str(record.get("url", ""))))
    if check_downloads and isinstance(feed_payload, dict):
        apps = feed_payload.get("apps") or []
        for app in apps[:25]:
            if not isinstance(app, dict):
                continue
            ok, detail = check_url_reachable(str(app.get("downloadURL", "")))
            if not ok:
                warnings.append(f"{app.get('name', '?')}: download unreachable ({detail})")
    # A non-integer reputation is already an error above; comparing it here
    # would raise on untrusted input (a third-party feed can put any JSON
    # value in the field) and take the whole validation gate down with it.
    reputation = record.get("reputation", 0)
    if isinstance(reputation, int) and not isinstance(reputation, bool) and reputation < 25:
        warnings.append("reputation below 25: quarantine recommended")
    return (not errors, errors, warnings)
