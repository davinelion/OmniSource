"""Offline validator for the catalog and the generated AltStore feeds.

Runs with zero network access so it is safe on pull requests from forks.
"""

from __future__ import annotations

import json
import os
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from omnisource.constants import (
    ALTSTORE_NON_FEED,
    INSTALLABLE_SUFFIXES,
    KNOWN_CLIENTS,
    VALID_STATUSES,
    VALID_VERIFICATION_METHODS,
    Paths,
)
from omnisource.http import is_http_url

REQUIRED_APP_FIELDS = ("name", "bundleIdentifier", "developerName", "version", "versionDate", "downloadURL")
REQUIRED_VERSION_FIELDS = ("version", "date", "downloadURL", "size", "localizedDescription")
REQUIRED_CATALOG_FIELDS = ("slug", "name", "bundleIdentifier", "developerName", "icon", "status", "compatibility")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
# Discovery source ids are derived from a feed URL (host + path stem + digest,
# see autodiscovery.source_id_for_url) and are bounded by
# schemas/discovery.schema.json — not by the 32-char catalog app slug rule.
# Validating them with SLUG_RE rejected every URL-derived id.
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
BUNDLE_RE = re.compile(r"^[A-Za-z0-9.-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?$")
TINT_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FORGE_PROVIDERS = {"github", "github-tags", "gitlab", "codeberg", "forgejo"}
FEED_PROVIDERS = {"json-feed", "altstore", "feather"}
URL_PROVIDERS = {"direct", "mirror", "archive"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def extend(self, other: Report) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def load_json(path: Path, report: Report, *, root: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.error(f"{_rel(path, root)}: file not found")
    except (OSError, json.JSONDecodeError) as error:
        report.error(f"{_rel(path, root)}: invalid JSON ({error})")
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# catalog.json
# ---------------------------------------------------------------------------
def validate_catalog(catalog: Any, *, assets_dir: Path) -> Report:
    report = Report()
    if not isinstance(catalog, dict):
        report.error("catalog.json: root must be a JSON object")
        return report

    source = catalog.get("source")
    if not isinstance(source, dict):
        report.error("catalog.json: 'source' must be an object")
    else:
        for key in ("name", "identifier", "baseURL", "icon"):
            if not source.get(key):
                report.error(f"catalog.json: source.{key} is required")
        if source.get("baseURL") and not is_http_url(source["baseURL"]):
            report.error("catalog.json: source.baseURL must be an HTTP(S) URL")

    apps = catalog.get("apps")
    if not isinstance(apps, list) or not apps:
        report.error("catalog.json: 'apps' must be a non-empty array")
        return report

    # Bundle identifiers must be unique on-device: sideloading clients replace
    # an installed app whose bundleIdentifier matches, so sharing one across
    # catalog entries silently uninstalls the other. Sharing is only allowed
    # when it is *declared*: every app in a shared-bundle group must set
    # ``alternativeTo`` to another member of the group (Phase 5 — undeclared
    # duplicates now fail CI instead of warning).
    bundles: dict[str, list[dict[str, Any]]] = {}
    for app in apps:
        if isinstance(app, dict) and app.get("bundleIdentifier"):
            bundles.setdefault(str(app["bundleIdentifier"]), []).append(app)
    for bundle_id, members in sorted(bundles.items()):
        if len(members) < 2:
            continue
        slugs = {str(app.get("slug") or app.get("name") or "?") for app in members}
        for app in members:
            slug = str(app.get("slug") or "?")
            alt = app.get("alternativeTo")
            if not alt:
                report.error(
                    f"catalog.json: {slug} shares bundleIdentifier '{bundle_id}' with "
                    f"{', '.join(sorted(slugs - {slug}))} but declares no 'alternativeTo' - "
                    "installing one replaces the others on device"
                )
            elif str(alt) not in slugs - {slug}:
                report.error(
                    f"catalog.json: {slug} declares alternativeTo '{alt}' which is not another "
                    f"member of its bundle group ({', '.join(sorted(slugs))})"
                )

    seen_slugs: set[str] = set()
    seen_names: set[str] = set()
    for index, app in enumerate(apps):
        prefix = f"catalog.json: apps[{index}]"
        if not isinstance(app, dict):
            report.error(f"{prefix} must be an object")
            continue
        prefix = f"catalog.json: {app.get('slug', index)}"

        for key in REQUIRED_CATALOG_FIELDS:
            if not app.get(key):
                report.error(f"{prefix} is missing '{key}'")

        slug = app.get("slug", "")
        if slug and not SLUG_RE.match(slug):
            report.error(f"{prefix}: slug must be lowercase kebab-case (2-32 chars)")
        if slug in seen_slugs:
            report.error(f"{prefix}: duplicate slug")
        seen_slugs.add(slug)

        name = app.get("name", "")
        if name in seen_names:
            report.warn(f"{prefix}: duplicate app name '{name}' - clients may show them as one app")
        seen_names.add(name)

        if app.get("bundleIdentifier") and not BUNDLE_RE.match(app["bundleIdentifier"]):
            report.error(f"{prefix}: bundleIdentifier contains invalid characters")

        status = app.get("status")
        if status and status not in VALID_STATUSES:
            report.error(f"{prefix}: unknown status '{status}' (expected one of {sorted(VALID_STATUSES)})")

        icon = app.get("icon")
        if icon and not (assets_dir / icon).is_file():
            report.error(f"{prefix}: icon 'assets/{icon}' does not exist")

        tint = app.get("tintColor")
        if tint and not TINT_RE.match(str(tint).lstrip("#")):
            report.error(f"{prefix}: tintColor must be a 6-digit hex string")

        if not app.get("localizedDescription"):
            report.warn(f"{prefix}: no localizedDescription - clients will show an empty app page")

        report.extend(_validate_verification(prefix, app.get("verification")))
        report.extend(_validate_compatibility(prefix, app.get("compatibility")))
        report.extend(_validate_upstream(prefix, app))
        report.extend(_validate_screenshots(prefix, app, catalog))
        report.extend(_validate_fallback_urls(prefix, app.get("fallbackDownloadURLs")))
        manual = app.get("manualRelease")
        if isinstance(manual, dict):
            report.extend(
                _validate_fallback_urls(
                    f"{prefix}.manualRelease", manual.get("fallbackDownloadURLs"), manual.get("downloadURL")
                )
            )
        report.extend(_validate_permissions(prefix, app.get("appPermissions"), app.get("permissions")))

    return report


_FORGE_HOSTS = frozenset({"github.com", "codeberg.org", "gitlab.com", "gitea.com"})


def _screenshot_source(app: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Owners and hosts this app's own project publishes from."""
    owners: set[str] = set()
    hosts: set[str] = set()
    upstream = app.get("upstream") if isinstance(app.get("upstream"), dict) else {}
    legs = [upstream, *(upstream.get("mirrors") or [])] if isinstance(upstream.get("mirrors"), list) else [upstream]
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        repo = str(leg.get("repo") or "")
        if "/" in repo:
            owners.add(repo.split("/", 1)[0].lower())
    for key in ("upstreamURL", "sourceURL"):
        url = str(app.get(key) or "")
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host:
            continue
        hosts.add(host)
        # Only a forge path has an owner as its first segment. Treating
        # archive.org/details/… or aidoku.app/images/… as "the owner is details"
        # would invent a source the project never claimed.
        if host.split("://")[-1] in _FORGE_HOSTS or host.removeprefix("raw.") in _FORGE_HOSTS:
            path = parsed.path.strip("/")
            if path.count("/") >= 1:
                owners.add(path.split("/", 1)[0].lower())
    return owners, hosts


def _validate_screenshots(prefix: str, app: dict[str, Any], catalog: dict[str, Any]) -> Report:
    """A screenshot must come from the app's own upstream, never from us.

    Screenshots are the one catalogue field that can be invented without anyone
    noticing - a rendered mockup looks exactly like the app it describes. So the
    rule here is provenance rather than taste: the URL has to live in the project's
    own repository, or on the host that project publishes from.

    The self-loop case is an error because ``source.baseURL`` makes OmniSource's raw
    URL resolvable forever: pointing ``screenshots`` at our own ``assets/`` mirror
    records no origin at all, and that is how fabricated 390x844 "screenshots"
    (fake status bar, app icon, skeleton rows) passed review for so long. Mirrors of
    genuine upstream art are still produced - by the build, into
    ``feeds/screenshots.json``, with a digest - they are simply not the declaration.
    """
    report = Report()
    shots = app.get("screenshots")
    if shots is None:
        return report
    if not isinstance(shots, list):
        report.error(f"{prefix}: screenshots must be an array of URLs")
        return report
    owners, hosts = _screenshot_source(app)
    self_repo = ""
    source = catalog.get("source") if isinstance(catalog.get("source"), dict) else {}
    repo_url = str(source.get("repository") or "")
    parsed_repo = urlparse(repo_url)
    if parsed_repo.path.count("/") >= 2:
        self_repo = parsed_repo.path.strip("/").lower()
    for entry in shots:
        url = entry if isinstance(entry, str) else (entry.get("url") if isinstance(entry, dict) else "")
        if not isinstance(url, str) or not is_http_url(url):
            report.error(f"{prefix}: screenshots entries must be HTTP(S) URLs")
            continue
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/").lower()
        if self_repo and (path == self_repo or path.startswith(self_repo + "/")):
            report.error(
                f"{prefix}: screenshot {url!r} points back at OmniSource's own assets - declare "
                "the image in the app's upstream repository (the build mirrors it, with a digest, "
                "into feeds/screenshots.json)"
            )
            continue
        repo_host = host in {"raw.githubusercontent.com", "gist.githubusercontent.com"} or (
            host == "github.com" and path.count("/") >= 2
        )
        if repo_host:
            owner = path.split("/", 1)[0].lower()
            if owners and owner not in owners:
                report.error(
                    f"{prefix}: screenshot is hosted in '{owner}/…' but this app's upstream is "
                    f"{', '.join(sorted(owners))} - use the developer's own image, not a copy in "
                    "someone else's repository"
                )
                continue
        elif hosts and host not in hosts:
            report.warn(
                f"{prefix}: screenshot host {host!r} is not the app's upstream host "
                f"({', '.join(sorted(hosts))}) - confirm it is the developer's own hosting"
            )
    return report


def _validate_verification(prefix: str, verification: Any) -> Report:
    report = Report()
    if verification is None:
        report.warn(f"{prefix}: no verification block - the app shows as unverified")
        return report
    if not isinstance(verification, dict):
        report.error(f"{prefix}: verification must be an object")
        return report
    method = verification.get("method")
    if method not in VALID_VERIFICATION_METHODS:
        report.error(f"{prefix}: verification.method must be one of {sorted(VALID_VERIFICATION_METHODS)}")
    if not verification.get("publisher"):
        report.error(f"{prefix}: verification.publisher is required")
    return report


def _validate_compatibility(prefix: str, compatibility: Any) -> Report:
    report = Report()
    if not isinstance(compatibility, dict):
        report.error(f"{prefix}: compatibility must be an object")
        return report
    min_os = compatibility.get("minOSVersion")
    if not isinstance(min_os, str) or not re.match(r"^\d+(\.\d+)*$", min_os):
        report.error(f"{prefix}: compatibility.minOSVersion must look like '16.0'")
    max_os = compatibility.get("maxOSVersion")
    if max_os is not None and not re.match(r"^\d+(\.\d+)*$", str(max_os)):
        report.error(f"{prefix}: compatibility.maxOSVersion must be null or look like '18.0'")
    clients = compatibility.get("clients")
    if not isinstance(clients, list) or not clients:
        report.error(f"{prefix}: compatibility.clients must be a non-empty array")
    else:
        unknown = sorted(set(clients) - KNOWN_CLIENTS)
        if unknown:
            report.error(f"{prefix}: unknown client id(s) {unknown}")
    return report


def _validate_upstream(prefix: str, app: dict[str, Any]) -> Report:
    report = Report()
    upstream = app.get("upstream")
    if upstream is None:
        if not isinstance(app.get("manualRelease"), dict):
            report.error(f"{prefix}: apps without an 'upstream' block need a 'manualRelease' block")
        else:
            manual = app["manualRelease"]
            for key in REQUIRED_VERSION_FIELDS:
                if key not in manual:
                    report.error(f"{prefix}: manualRelease is missing '{key}'")
            if not is_http_url(manual.get("downloadURL")):
                report.error(f"{prefix}: manualRelease.downloadURL must be an HTTP(S) URL")
        return report

    if not isinstance(upstream, dict):
        report.error(f"{prefix}: upstream must be an object or null")
        return report

    provider = str(upstream.get("provider") or "github")
    if provider in FEED_PROVIDERS:
        if not is_http_url(upstream.get("feedURL") or upstream.get("feedUrl")):
            report.error(f"{prefix}: feed providers require a valid upstream.feedURL")
    elif provider in URL_PROVIDERS:
        if not is_http_url(upstream.get("url") or upstream.get("feedURL") or upstream.get("feedUrl")):
            report.error(f"{prefix}: {provider} providers require a valid upstream.url")
    else:
        repo = upstream.get("repo", "")
        if not re.match(r"^[\w.-]+/[\w.-]+$", str(repo)):
            report.error(f"{prefix}: upstream.repo must be 'owner/name'")
        if provider == "forgejo" and not upstream.get("host"):
            report.error(f"{prefix}: forgejo provider requires upstream.host")
        if provider not in FORGE_PROVIDERS | FEED_PROVIDERS | URL_PROVIDERS | {"manual"}:
            report.error(f"{prefix}: unknown upstream.provider '{provider}'")
    report.extend(_validate_mirrors(prefix, upstream.get("mirrors")))
    if int(upstream.get("keepVersions", 1) or 0) < 0:
        report.error(f"{prefix}: upstream.keepVersions must be >= 0 (0 means keep all)")
    if "{version}" not in str(upstream.get("descriptionTemplate", "{version}")):
        report.warn(f"{prefix}: upstream.descriptionTemplate has no {{version}} placeholder")
    pattern = upstream.get("assetNamePattern")
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            report.error(f"{prefix}: upstream.assetNamePattern must be a non-empty string")
        else:
            try:
                re.compile(pattern)
            except re.error as error:
                report.error(f"{prefix}: upstream.assetNamePattern is not a valid regex ({error})")
    version_pattern = upstream.get("versionPattern")
    if version_pattern is not None:
        if not isinstance(version_pattern, str) or not version_pattern:
            report.error(f"{prefix}: upstream.versionPattern must be a non-empty string")
        else:
            try:
                compiled = re.compile(version_pattern)
            except re.error as error:
                report.error(f"{prefix}: upstream.versionPattern is not a valid regex ({error})")
            else:
                if compiled.groups > 1:
                    report.warn(
                        f"{prefix}: upstream.versionPattern has {compiled.groups} capture groups - "
                        "only the first one is read"
                    )
    if upstream.get("versionFromTag") is not None and not isinstance(upstream.get("versionFromTag"), bool):
        report.error(f"{prefix}: upstream.versionFromTag must be a boolean")
    return report


def _validate_mirrors(prefix: str, mirrors: Any) -> Report:
    """Validate the optional ``upstream.mirrors`` failover legs."""
    report = Report()
    if mirrors is None:
        return report
    if not isinstance(mirrors, list):
        report.error(f"{prefix}.mirrors must be an array of upstream blocks")
        return report
    for index, mirror in enumerate(mirrors):
        label = f"{prefix}.mirrors[{index}]"
        if not isinstance(mirror, dict):
            report.error(f"{label} must be an object")
            continue
        provider = str(mirror.get("provider") or "")
        if provider not in FORGE_PROVIDERS | FEED_PROVIDERS | URL_PROVIDERS:
            report.error(f"{label}.provider must be one of {sorted(FORGE_PROVIDERS | FEED_PROVIDERS | URL_PROVIDERS)}")
            continue
        if provider in URL_PROVIDERS or provider in FEED_PROVIDERS:
            if not is_http_url(mirror.get("url") or mirror.get("feedURL") or mirror.get("feedUrl")):
                report.error(f"{label} requires a valid url")
        else:
            if not re.match(r"^[\w.-]+/[\w.-]+$", str(mirror.get("repo") or "")):
                report.error(f"{label}.repo must be 'owner/name'")
            if provider == "forgejo" and not mirror.get("host"):
                report.error(f"{label}: forgejo mirrors require host")
    return report


def _validate_fallback_urls(prefix: str, value: Any, primary: str | None = None) -> Report:
    report = Report()
    if value is None:
        return report
    if not isinstance(value, list):
        report.error(f"{prefix}: fallbackDownloadURLs must be an array of URLs")
        return report
    seen: set[str] = set()
    for index, url in enumerate(value):
        label = f"{prefix}.fallbackDownloadURLs[{index}]"
        if not isinstance(url, str) or not is_http_url(url):
            report.error(f"{label} must be a valid HTTP(S) URL")
            continue
        if not url.startswith("https://"):
            report.warn(f"{label} is not HTTPS - mirrors should be served over TLS")
        if url in seen:
            report.error(f"{label} duplicates an earlier mirror")
        if primary and url == primary:
            report.error(f"{label} duplicates the primary downloadURL")
        seen.add(url)
    return report


def _validate_permissions(prefix: str, app_permissions: Any, legacy_permissions: Any) -> Report:
    report = Report()
    if legacy_permissions is not None:
        if not isinstance(legacy_permissions, list):
            report.error(f"{prefix}.permissions must be an array of {{type, usageDescription}}")
        else:
            for index, perm in enumerate(legacy_permissions):
                if not isinstance(perm, dict) or not perm.get("type") or not perm.get("usageDescription"):
                    report.error(f"{prefix}.permissions[{index}] must have 'type' and 'usageDescription'")
    if app_permissions is not None:
        if not isinstance(app_permissions, dict):
            report.error(f"{prefix}.appPermissions must be an object")
            return report
        entitlements = app_permissions.get("entitlements")
        if entitlements is not None and (
            not isinstance(entitlements, list) or not all(isinstance(item, str) for item in entitlements)
        ):
            report.error(f"{prefix}.appPermissions.entitlements must be an array of strings")
        privacy = app_permissions.get("privacy")
        privacy_ok = isinstance(privacy, dict) and all(isinstance(v, str) for v in privacy.values())
        if privacy is not None and not privacy_ok:
            report.error(f"{prefix}.appPermissions.privacy must be an object of string values")
    return report


# ---------------------------------------------------------------------------
# AltStore feeds
# ---------------------------------------------------------------------------
def validate_feed(path: Path, feed: Any, *, root: Path) -> Report:
    report = Report()
    label = _rel(path, root)
    if not isinstance(feed, dict):
        report.error(f"{label}: root must be a JSON object")
        return report

    for key in ("name", "identifier", "apps"):
        if key not in feed:
            report.error(f"{label}: missing top-level '{key}'")
    for key in ("iconURL", "website"):
        if feed.get(key) and not is_http_url(feed[key]):
            report.error(f"{label}: {key} must be an HTTP(S) URL")

    apps = feed.get("apps")
    if not isinstance(apps, list) or not apps:
        report.error(f"{label}: 'apps' must be a non-empty array")
        return report

    # Phase 5: two apps in the same feed must never publish the same download
    # URL — that is unambiguously a mistake (unlike a shared bundle, which the
    # catalog may declare deliberately with 'alternativeTo').
    url_owners: dict[str, str] = {}
    for app in apps:
        if not isinstance(app, dict):
            continue
        owner = str(app.get("name") or "?")
        version_urls = [v.get("downloadURL") for v in app.get("versions", []) if isinstance(v, dict)]
        for url in (app.get("downloadURL"), *version_urls):
            if not isinstance(url, str) or not url:
                continue
            previous = url_owners.get(url)
            if previous is not None and previous != owner:
                report.error(f"{label}: download URL {url} is shared by '{previous}' and '{owner}'")
            else:
                url_owners[url] = owner

    for index, app in enumerate(apps):
        prefix = f"{label}: apps[{index}]"
        if not isinstance(app, dict):
            report.error(f"{prefix} must be an object")
            continue
        prefix = f"{label}: {app.get('name', index)}"

        for key in REQUIRED_APP_FIELDS:
            if not app.get(key):
                report.error(f"{prefix} is missing '{key}'")
        if app.get("bundleIdentifier") and not BUNDLE_RE.match(str(app["bundleIdentifier"])):
            report.error(f"{prefix}.bundleIdentifier contains invalid characters")
        if app.get("downloadURL") and not is_http_url(app["downloadURL"]):
            report.error(f"{prefix}.downloadURL must be an HTTP(S) URL")
        if app.get("versionDate") and not DATE_RE.match(str(app["versionDate"])):
            report.error(f"{prefix}.versionDate must be an ISO date (YYYY-MM-DD)")
        if app.get("iconURL") and not is_http_url(app["iconURL"]):
            report.error(f"{prefix}.iconURL must be an HTTP(S) URL")
        if app.get("tintColor") and not TINT_RE.match(str(app["tintColor"]).lstrip("#")):
            report.error(f"{prefix}.tintColor must be a 6-digit hex string")
        report.extend(_validate_fallback_urls(prefix, app.get("fallbackDownloadURLs"), app.get("downloadURL")))
        report.extend(_validate_permissions(prefix, app.get("appPermissions"), app.get("permissions")))

        versions = app.get("versions")
        if not isinstance(versions, list) or not versions:
            report.error(f"{prefix}.versions must be a non-empty array")
            continue

        seen: set[str] = set()
        for v_index, version in enumerate(versions):
            v_prefix = f"{prefix}.versions[{v_index}]"
            if not isinstance(version, dict):
                report.error(f"{v_prefix} must be an object")
                continue
            for key in REQUIRED_VERSION_FIELDS:
                if version.get(key) in (None, ""):
                    report.error(f"{v_prefix} is missing '{key}'")
            if version.get("downloadURL") and not is_http_url(version["downloadURL"]):
                report.error(f"{v_prefix}.downloadURL must be an HTTP(S) URL")
            size = version.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                report.error(f"{v_prefix}.size must be a non-negative integer")
            elif size == 0:
                report.warn(f"{v_prefix}.size is 0 - clients show a bogus download size")
            if version.get("date") and not DATE_RE.match(str(version["date"])):
                report.error(f"{v_prefix}.date must be an ISO date (YYYY-MM-DD)")
            if version.get("sha256") and not SHA_RE.match(str(version["sha256"])):
                report.error(f"{v_prefix}.sha256 must be a 64-char hex digest")
            report.extend(
                _validate_fallback_urls(v_prefix, version.get("fallbackDownloadURLs"), version.get("downloadURL"))
            )
            url = version.get("downloadURL")
            if url in seen:
                report.error(f"{v_prefix}.downloadURL is duplicated within this app")
            seen.add(url)

        newest = versions[0]
        if isinstance(newest, dict):
            for flat, nested in (
                ("version", "version"),
                ("versionDate", "date"),
                ("downloadURL", "downloadURL"),
                ("size", "size"),
            ):
                if app.get(flat) != newest.get(nested):
                    report.error(f"{prefix}.{flat} must mirror versions[0].{nested}")

        extension = app.get("omnisource")
        if extension is None:
            report.warn(f"{prefix}: no 'omnisource' metadata block")
        elif isinstance(extension, dict):
            if extension.get("status") not in VALID_STATUSES:
                report.error(f"{prefix}.omnisource.status is missing or unknown")
            health = extension.get("health", {})
            if isinstance(health, dict) and health.get("downloadReachable") is False:
                report.warn(f"{prefix}: download URL was unreachable at build time ({health.get('detail')})")

    return report


def _is_paired_asset(name: str, referenced: set[Any]) -> bool:
    """True when ``name`` is the format twin of a referenced asset.

    The catalog references WebP icons while the original PNGs stay in the
    repo as fallbacks, so a ``.png`` next to a referenced ``.webp`` (or the
    reverse) is intentional, not dead weight.
    """
    stem, dot, ext = str(name).rpartition(".")
    if not dot:
        return False
    twin_ext = ".png" if ext.lower() == "webp" else ".webp"
    return f"{stem}{twin_ext}" in referenced


def validate_assets(catalog: Any, *, assets_dir: Path) -> Report:
    report = Report()
    referenced = {app.get("icon") for app in catalog.get("apps", []) if isinstance(app, dict)}
    referenced |= {catalog.get("source", {}).get("icon"), catalog.get("source", {}).get("banner")}
    referenced |= {client.get("icon") for client in catalog.get("clients", []) if isinstance(client, dict)}
    referenced = {name for name in referenced if name}

    for name in sorted(referenced):
        if not (assets_dir / name).is_file():
            report.error(f"assets/{name}: referenced by catalog.json but missing")

    if assets_dir.is_dir():
        for asset in sorted(assets_dir.iterdir()):
            # The catalog references WebP icons; the matching PNGs are kept
            # as <picture>/legacy fallbacks, so paired twins are not unused.
            if asset.is_file() and asset.name not in referenced and not _is_paired_asset(asset.name, referenced):
                report.warn(f"assets/{asset.name}: not referenced by catalog.json")
            if asset.is_file() and asset.stat().st_size > 512_000:
                report.warn(f"assets/{asset.name}: {asset.stat().st_size // 1024} KB - consider optimising")
    return report


# ---------------------------------------------------------------------------
# Generated intelligence documents + static app pages
# ---------------------------------------------------------------------------
VERIFICATION_LEVELS = {"VERIFIED", "COMMUNITY VERIFIED", "UNVERIFIED"}
SOURCE_STATUSES = {"healthy", "degraded", "unavailable", "unknown"}
GENERATED_DOCS = (
    "discovery.json",
    "sources.json",
    "verification.json",
    "status.json",
    "duplicates.json",
    "analytics.json",
    "trending.json",
    "related.json",
    "reputation.json",
    "download-intelligence.json",
    "community.json",
    "install.json",
    "search-index.json",
    "compare.json",
    "screenshots.json",
    "integrity_report.json",
    "dead_apps.json",
    "collections.json",
    "asset-manifest.json",
)
CHECK_KEYS = ("metadata", "urls", "fileAvailable", "hashVerified")


def validate_generated_docs(catalog: Any, paths: Paths) -> Report:
    """Validate the derived documents and the static app pages."""
    report = Report()
    apps = catalog.get("apps", []) if isinstance(catalog, dict) else []
    slugs = {str(item.get("slug")) for item in apps if isinstance(item, dict) and item.get("slug")}

    for name in GENERATED_DOCS:
        path = paths.feeds / name
        doc = load_json(path, report, root=paths.root)
        if doc is None:
            continue
        if not isinstance(doc, dict):
            report.error(f"feeds/{name}: root must be a JSON object")
            continue
        if not doc.get("generatedAt"):
            report.error(f"feeds/{name}: missing generatedAt")
        validate_doc_shape(name, doc, catalog, report, root=paths.root, apps_count=len(apps))
        if name == "screenshots.json":
            _validate_screenshot_manifest(doc, paths, report)

    shots_by_slug = {
        str(item.get("slug")): [s for s in (item.get("screenshots") or []) if isinstance(s, str)]
        for item in apps
        if isinstance(item, dict) and item.get("slug")
    }
    pages_dir = paths.root / "apps"
    for slug in sorted(slugs):
        page = pages_dir / slug / "index.html"
        if not page.is_file():
            report.error(f"apps/{slug}/index.html: missing generated app page (run scripts/omnisource.py)")
            continue
        try:
            content = page.read_text(encoding="utf-8")
        except OSError as error:
            report.error(f"apps/{slug}/index.html: unreadable ({error})")
            continue
        if "og:title" not in content or "assets/design-system/tokens.css" not in content:
            report.error(f"apps/{slug}/index.html: looks incomplete (missing page shell)")
        # The page's structured data may only claim screenshots the catalog declares
        # from the app's own upstream. Claiming the icon as a screenshot is what put
        # fabricated previews into search results.
        claims = '"screenshot":' in content
        if claims and not shots_by_slug.get(slug):
            report.error(f"apps/{slug}/index.html: JSON-LD claims a screenshot the catalog does not declare")
        if not claims and shots_by_slug.get(slug):
            report.error(f"apps/{slug}/index.html: {len(shots_by_slug[slug])} screenshot(s) declared but not published")
    return report


def _validate_screenshot_manifest(doc: dict[str, Any], paths: Paths, report: Report) -> None:
    """``feeds/screenshots.json`` describes this repository, so its claims must hold.

    ``mirrored: true`` promises the bytes are checked in beside the manifest; a
    dangling mirror URL is what a reader, the site, or a future rebuild then 404
    on. And an app icon is not a screenshot: apps whose upstream publishes no art
    are simply absent from this document, which is why they are listed as a content
    gap in ``feeds/asset-manifest.json`` instead.
    """
    for entry in doc.get("screenshots") or []:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "?")
        if entry.get("iconFallback"):
            report.error(
                f"feeds/screenshots.json: {slug} publishes the app icon as a screenshot - "
                "declare the upstream image, or nothing"
            )
            continue
        if not entry.get("mirrored"):
            continue
        url = str(entry.get("mirroredURL") or "")
        if "/assets/" not in url:
            report.error(f"feeds/screenshots.json: {slug} mirrors to a URL outside the repository: {url!r}")
            continue
        relative = url.split("/assets/", 1)[1]
        if not (paths.root / "assets" / relative).is_file():
            report.error(f"feeds/screenshots.json: {slug} claims a mirror at assets/{relative} that is not in the tree")


def _catalog_slugs(catalog: Any) -> set[str] | None:
    """Known app slugs, accepting either the raw catalog dict or a Catalog."""
    if catalog is None:
        return None
    if isinstance(catalog, dict):
        apps = catalog.get("apps")
        if not isinstance(apps, list):
            return None
        return {str(item.get("slug")) for item in apps if isinstance(item, dict) and item.get("slug")}
    apps = getattr(catalog, "apps", None)
    if not isinstance(apps, (list, tuple)):
        return None
    return {app.slug for app in apps}


def validate_doc_shape(
    name: str,
    doc: dict[str, Any],
    catalog: Any,
    report: Report,
    *,
    root: Path,
    apps_count: int,
) -> None:
    """Structural checks per generated document type."""

    def items(key: str) -> list[Any]:
        value = doc.get(key)
        return value if isinstance(value, list) else []

    if name == "discovery.json":
        if doc.get("count") != len(items("apps")):
            report.error("feeds/discovery.json: count does not match apps[] length")
        if len(items("apps")) != apps_count:
            report.error(f"feeds/discovery.json: {len(items('apps'))} apps, catalog declares {apps_count}")
        for app in items("apps"):
            if not isinstance(app, dict):
                report.error("feeds/discovery.json: apps[] entries must be objects")
                continue
            for key in ("id", "name", "developer", "version", "category", "tags", "source"):
                if key not in app:
                    report.error(f"feeds/discovery.json: app {app.get('id')} is missing '{key}'")

    if name == "sources.json":
        if doc.get("count") != len(items("sources")):
            report.error("feeds/sources.json: count does not match sources[] length")
        if not items("sources"):
            report.error("feeds/sources.json: sources[] is empty")
        for source in items("sources"):
            if not isinstance(source, dict) or not source.get("id") or not source.get("source"):
                report.error("feeds/sources.json: every source needs id + source")
            if not isinstance(source.get("apps"), list):
                report.error("feeds/sources.json: every source needs an apps[] list")

    if name == "verification.json":
        if doc.get("totals", {}).get("apps") != len(items("apps")):
            report.error("feeds/verification.json: totals.apps does not match entries")
        for entry in items("apps"):
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "")
            if status not in VERIFICATION_LEVELS:
                report.error(f"feeds/verification.json: {entry.get('app')} has unknown status '{status}'")
            checks = entry.get("checks", {})
            if not isinstance(checks, dict) or sorted(checks) != sorted(CHECK_KEYS):
                report.error(f"feeds/verification.json: {entry.get('app')} checks must be {list(CHECK_KEYS)}")

    if name == "status.json":
        if doc.get("totals", {}).get("sources") != len(items("sources")):
            report.error("feeds/status.json: totals.sources does not match sources[] length")
        # Phase 15 monitoring sections.
        for section in ("pipeline", "deployment"):
            if not isinstance(doc.get(section), dict):
                report.error(f"feeds/status.json: {section} section is required")
        if not isinstance(doc.get("providers"), list):
            report.error("feeds/status.json: providers section must be a list")
        for source in items("sources"):
            if not isinstance(source, dict):
                continue
            status = str(source.get("status") or "")
            if status not in SOURCE_STATUSES:
                report.error(f"feeds/status.json: {source.get('id')} has unknown status '{status}'")
            latency = source.get("latency")
            if latency is not None and (not isinstance(latency, int) or latency < 0):
                report.error(f"feeds/status.json: {source.get('id')} latency must be a non-negative integer or null")

    if name == "duplicates.json":
        if doc.get("count") != len(items("groups")):
            report.error("feeds/duplicates.json: count does not match groups[] length")
        for group in items("groups"):
            if not isinstance(group, dict):
                continue
            if len(group.get("apps", [])) < 2:
                report.error("feeds/duplicates.json: every group needs at least 2 apps")
            if not isinstance(group.get("recommended"), dict):
                report.error("feeds/duplicates.json: every group needs a recommendation")

    if name == "analytics.json":
        totals = doc.get("totals", {})
        required = (
            "apps",
            "sources",
            "verifiedApps",
            "communityVerifiedApps",
            "unverifiedApps",
            "newAppsThisWeek",
            "updatedAppsThisWeek",
            "deadLinks",
        )
        for key in required:
            if key not in totals:
                report.error(f"feeds/analytics.json: totals.{key} is missing")
        if totals.get("apps") != apps_count:
            report.error(f"feeds/analytics.json: totals.apps ({totals.get('apps')}) != catalog apps ({apps_count})")

    if name == "trending.json":
        if doc.get("count") != len(items("all")):
            report.error("feeds/trending.json: count does not match all[] length")
        for app in items("all"):
            if not isinstance(app, dict):
                continue
            if not isinstance(app.get("score"), (int, float)):
                report.error(f"feeds/trending.json: app {app.get('slug')} score must be a number")
            if not isinstance(app.get("signals"), dict):
                report.error(f"feeds/trending.json: app {app.get('slug')} signals must be an object")

    if name == "related.json":
        related = doc.get("related")
        if not isinstance(related, dict):
            report.error("feeds/related.json: related must be an object")
        for slug, entries in (related or {}).items():
            if not isinstance(entries, list):
                report.error(f"feeds/related.json: related.{slug} must be a list")
                continue
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("slug"):
                    report.error(f"feeds/related.json: related.{slug} entries must be objects with a slug")

    if name == "reputation.json":
        if doc.get("count") != len(items("sources")):
            report.error("feeds/reputation.json: count does not match sources[] length")
        for source in items("sources"):
            if not isinstance(source, dict):
                continue
            if source.get("level") not in {"TRUSTED", "RELIABLE", "AVERAGE", "EXPERIMENTAL"}:
                report.error(f"feeds/reputation.json: {source.get('id')} has unknown level")
            score = source.get("score")
            if not isinstance(score, (int, float)) or not (0 <= score <= 100):
                report.error(f"feeds/reputation.json: {source.get('id')} score must be 0..100")

    if name == "download-intelligence.json":
        summary = doc.get("summary", {})
        for key in ("averageAvailability", "mirrorCount", "probes"):
            if key not in summary:
                report.error(f"feeds/download-intelligence.json: summary.{key} is missing")

    if name == "community.json":
        for key in ("popular", "recentlyAdded", "rising", "requested"):
            if not isinstance(doc.get(key), list):
                report.error(f"feeds/community.json: {key} must be a list")

    if name == "install.json":
        if not isinstance(doc.get("clients"), list) or not doc.get("clients"):
            report.error("feeds/install.json: clients must be a non-empty list")
        if not isinstance(doc.get("apps"), list) or not doc.get("apps"):
            report.error("feeds/install.json: apps must be a non-empty list")
        for app in items("apps"):
            if not isinstance(app.get("cards"), list) or not app.get("cards"):
                report.error(f"feeds/install.json: {app.get('slug')} must have install cards")

    if name == "search-index.json":
        documents = doc.get("documents")
        if not isinstance(documents, list) or not documents:
            report.error("feeds/search-index.json: documents must be a non-empty list")
        else:
            for entry in documents:
                if not isinstance(entry, dict) or not entry.get("id"):
                    report.error("feeds/search-index.json: every document must have an id")

    if name == "compare.json":
        # In v2, count is the number of app summaries (not the number of pairs)
        # and pairs[] only holds bundle-sharing pairs (~28) — the full matrix is
        # computed client-side by src/js/compare-engine.js to keep the payload
        # under 150 KB. Accept both v1 (count = pairs.length) and v2
        # (schemaVersion >= 2, count = apps.length).
        schema_v = doc.get("schemaVersion", 1)
        if schema_v >= 2:
            if doc.get("count") != len(items("apps")):
                report.error("feeds/compare.json: count does not match apps[] length (v2 schema)")
            if not isinstance(doc.get("bundlePairs"), list):
                report.error("feeds/compare.json: bundlePairs must be a list (v2 schema)")
        else:
            if doc.get("count") != len(items("pairs")):
                report.error("feeds/compare.json: count does not match pairs[] length")

    if name == "screenshots.json" and not isinstance(doc.get("screenshots"), list):
        report.error("feeds/screenshots.json: screenshots must be a list")

    if name == "integrity_report.json":
        totals = doc.get("totals", {})
        if not isinstance(totals, dict) or "apps" not in totals:
            report.error("feeds/integrity_report.json: totals.apps is required")
        if not isinstance(doc.get("apps"), list):
            report.error("feeds/integrity_report.json: apps must be a list")
        else:
            for entry in doc["apps"]:
                if not isinstance(entry, dict) or not entry.get("slug"):
                    report.error("feeds/integrity_report.json: every entry needs a slug")
                    continue
                asset = entry.get("asset")
                if not isinstance(asset, dict):
                    report.error(f"feeds/integrity_report.json: {entry.get('slug')} asset record is missing")
                    continue
                for key in ("sha256", "size", "releaseId", "source"):
                    if key not in asset:
                        report.error(f"feeds/integrity_report.json: {entry.get('slug')} asset is missing '{key}'")
                # Hard reject rules — a corrupted asset must fail CI, not just
                # show up in the report.
                if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
                    report.error(f"feeds/integrity_report.json: {entry.get('slug')} asset is zero bytes")
                url = str(asset.get("downloadUrl") or "")
                if url and not url.split("?", 1)[0].lower().endswith(INSTALLABLE_SUFFIXES):
                    report.error(f"feeds/integrity_report.json: {entry.get('slug')} primary asset is not an IPA")
                sha = asset.get("sha256")
                if sha is not None and not SHA_RE.match(str(sha)):
                    report.error(f"feeds/integrity_report.json: {entry.get('slug')} sha256 is malformed")

    if name == "dead_apps.json":
        if not isinstance(doc.get("summary"), dict):
            report.error("feeds/dead_apps.json: summary is required")
        if not isinstance(doc.get("apps"), list):
            report.error("feeds/dead_apps.json: apps must be a list")
        for entry in items("apps"):
            if isinstance(entry, dict) and entry.get("classification") not in {
                "healthy",
                "warning",
                "stale",
                "archived",
                "critical",
            }:
                report.error(f"feeds/dead_apps.json: {entry.get('slug')} has unknown classification")

    if name == "collections.json":
        collections_list = doc.get("collections")
        if not isinstance(collections_list, list):
            report.error("feeds/collections.json: collections must be a list")
            collections_list = []
        known = _catalog_slugs(catalog)
        slugs: set[str] = set()
        for collection in collections_list:
            if not isinstance(collection, dict) or not collection.get("slug") or not collection.get("title"):
                report.error("feeds/collections.json: every collection needs slug + title")
                continue
            slug = str(collection["slug"])
            if slug in slugs:
                report.error(f"feeds/collections.json: duplicate collection slug '{slug}'")
            slugs.add(slug)
            app_slugs = collection.get("appSlugs")
            if not isinstance(app_slugs, list) or not app_slugs:
                report.error(f"feeds/collections.json: collection '{slug}' has no apps")
                continue
            if known is not None:
                unknown = [str(s) for s in app_slugs if str(s) not in known]
                if unknown:
                    report.error(f"feeds/collections.json: collection '{slug}' references unknown apps {unknown}")

    if name == "asset-manifest.json":
        icons = doc.get("icons")
        if not isinstance(icons, dict) or not icons:
            report.error("feeds/asset-manifest.json: icons must be a non-empty object")
        else:
            for slug, entry in icons.items():
                if not isinstance(entry, dict):
                    report.error(f"feeds/asset-manifest.json: icons.{slug} must be an object")
                    continue
                if not entry.get("url"):
                    report.error(f"feeds/asset-manifest.json: icons.{slug} has no url")
                if not entry.get("fallback"):
                    report.error(f"feeds/asset-manifest.json: icons.{slug} has no fallback")
                if entry.get("exists") is False:
                    report.error(f"feeds/asset-manifest.json: icons.{slug} points at a missing file")
        placeholders = doc.get("placeholders")
        if not isinstance(placeholders, dict):
            report.error("feeds/asset-manifest.json: placeholders must be an object")
        else:
            for key in ("app.svg", "category.svg", "banner.svg"):
                entry = placeholders.get(key)
                if not isinstance(entry, dict) or entry.get("exists") is not True:
                    report.error(f"feeds/asset-manifest.json: placeholder '{key}' is missing")
        if not isinstance(doc.get("missing"), list):
            report.error("feeds/asset-manifest.json: missing must be a list")
        elif doc["missing"]:
            report.error(f"feeds/asset-manifest.json: {len(doc['missing'])} icon(s) missing: {doc['missing'][:5]}")
        totals = doc.get("totals", {})
        if not isinstance(totals, dict) or totals.get("iconsMissing"):
            report.error("feeds/asset-manifest.json: totals.iconsMissing must be 0")


# ---------------------------------------------------------------------------
# OmniStore Pro API contract (feeds/api/v2/) + normalized app records
# ---------------------------------------------------------------------------
FEED_VERSION_RE = re.compile(r"^[0-9a-f]{12}$")


def validate_api_v2(catalog: Any, paths: Paths) -> Report:
    """Validate the generated v2 contract: manifest, checksums, per-app docs."""
    import hashlib

    from omnisource.app_schema import validate_app_records

    report = Report()
    contract = paths.feeds / "api" / "v2"
    manifest_path = contract / "manifest.json"
    manifest = load_json(manifest_path, report, root=paths.root)
    if manifest is None:
        return report
    if not isinstance(manifest, dict):
        report.error("feeds/api/v2/manifest.json: root must be a JSON object")
        return report
    if manifest.get("schemaVersion") != 2:
        report.error("feeds/api/v2/manifest.json: schemaVersion must be 2")
    feed_version = str(manifest.get("feedVersion") or "")
    if not FEED_VERSION_RE.match(feed_version):
        report.error("feeds/api/v2/manifest.json: feedVersion must be 12 hex chars")
    if not manifest.get("minimumClientVersion"):
        report.error("feeds/api/v2/manifest.json: minimumClientVersion is required")
    if not manifest.get("generatedAt"):
        report.error("feeds/api/v2/manifest.json: missing generatedAt")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        report.error("feeds/api/v2/manifest.json: documents must be a non-empty list")
    else:
        for entry in documents:
            if not isinstance(entry, dict):
                report.error("feeds/api/v2/manifest.json: every documents[] entry must be an object")
                continue
            rel = str(entry.get("path") or "")
            if not rel.startswith("/api/v2/"):
                report.error(f"feeds/api/v2/manifest.json: unexpected document path '{rel}'")
                continue
            target = contract / rel[len("/api/v2/") :]
            try:
                payload = target.read_bytes()
            except OSError:
                report.error(f"feeds/api/v2/manifest.json: listed document missing: {rel}")
                continue
            digest = hashlib.sha256(payload).hexdigest()
            if entry.get("sha256") != digest:
                report.error(f"feeds/api/v2/manifest.json: checksum mismatch for {rel} - rebuild the contract")
            if entry.get("bytes") != len(payload):
                report.error(f"feeds/api/v2/manifest.json: byte size mismatch for {rel}")

    for name in ("featured.json", "categories.json", "updates.json"):
        doc = load_json(contract / name, report, root=paths.root)
        if not isinstance(doc, dict):
            continue
        if doc.get("schemaVersion") != 2:
            report.error(f"feeds/api/v2/{name}: schemaVersion must be 2")
        if doc.get("feedVersion") != feed_version:
            report.error(f"feeds/api/v2/{name}: feedVersion does not match the manifest")

    slugs = _catalog_slugs(catalog) or set()
    records: list[dict[str, Any]] = []
    for slug in sorted(slugs):
        doc = load_json(contract / "apps" / f"{slug}.json", report, root=paths.root)
        if not isinstance(doc, dict):
            continue
        if doc.get("feedVersion") != feed_version:
            report.error(f"feeds/api/v2/apps/{slug}.json: feedVersion does not match the manifest")
        record = doc.get("app")
        if not isinstance(record, dict):
            report.error(f"feeds/api/v2/apps/{slug}.json: app record is missing")
            continue
        if str(record.get("id") or "") != slug:
            report.error(f"feeds/api/v2/apps/{slug}.json: record id does not match the file name")
        records.append(record)
    if records:
        report.extend(validate_app_records(records, catalog, prefix="api/v2/apps"))
    return report


def validate_published_app_records(catalog: Any, paths: Paths) -> Report:
    """Validate the *published* master feed against the client contract."""
    from omnisource.app_schema import (
        record_from_feed_entry,
        validate_app_records,
    )

    report = Report()
    feed = load_json(paths.feeds / "apps.json", report, root=paths.root)
    if not isinstance(feed, dict):
        return report
    apps = feed.get("apps")
    if not isinstance(apps, list):
        return report
    slugs = _catalog_slugs(catalog) or set()
    records = [record_from_feed_entry(entry) for entry in apps if isinstance(entry, dict)]
    if len(records) != len(slugs):
        report.warn(f"feeds/apps.json: {len(records)} published app(s) for {len(slugs)} catalog app(s)")
    report.extend(validate_app_records(records, catalog, prefix="apps"))
    return report


# ---------------------------------------------------------------------------
# Sourcing policy (data/source_policy.json)
# ---------------------------------------------------------------------------
def validate_source_policy(paths: Paths, catalog: Any) -> Report:
    """Offline gate for the recorded sourcing verdicts.

    Two directions are checked, because both matter:

    * the policy file itself has to stay parseable and narrow (an unparseable
      rule would silently stop blocking anything); and
    * no ``catalog.json`` entry may resolve from, or mirror, a blocked source.
      Discovery can keep re-proposing a blocked feed - the catalog is the only
      thing that reaches a client, so that is where the decision is enforced.
    """
    from omnisource.source_policy import (
        POLICY_RELATIVE_PATH,
        SCHEMA_RELATIVE_PATH,
        catalog_violations,
        load_policy,
        validate_policy,
    )

    report = Report()
    policy_path = paths.root / POLICY_RELATIVE_PATH
    if not (paths.root / SCHEMA_RELATIVE_PATH).is_file():
        report.warn(
            f"{SCHEMA_RELATIVE_PATH}: missing - the sourcing policy is no longer schema-checkable, "
            "so tests/test_source_policy.py cannot prove its shape"
        )
    if not policy_path.is_file():
        report.warn(f"{_rel(policy_path, paths.root)}: missing - sourcing verdicts are not enforced this run")
        return report
    document = load_json(policy_path, report, root=paths.root)
    if document is None:
        return report
    for error in validate_policy(document):
        report.error(error)

    policy = load_policy(paths.root)
    if policy.error:
        report.error(f"{_rel(policy_path, paths.root)}: {policy.error}")
        return report
    for violation in catalog_violations(catalog, policy):
        report.error(violation)
    return report


# ---------------------------------------------------------------------------
# Source-build recipes (data/source_builds.json)
# ---------------------------------------------------------------------------
def validate_source_builds(paths: Paths, catalog: Any) -> Report:
    """Offline gate for the source-build lane.

    Recipes are the honest answer to "upstream publishes source, no binary":
    they carry a pinned revision and the digest of that revision's archive, so
    the file has to stay parseable and its claims stay checkable. Like the
    sourcing policy it reuses, a broken file is an error rather than a skip —
    otherwise a typo would quietly remove the integrity promise.
    """
    from omnisource.source_builds import (
        BUILDS_RELATIVE_PATH,
        SCHEMA_RELATIVE_PATH,
        load_builds,
        recipe_violations,
        shipped_data_matches_schema,
        validate_builds,
    )
    from omnisource.source_policy import load_policy

    report = Report()
    builds_path = paths.root / BUILDS_RELATIVE_PATH
    if not (paths.root / SCHEMA_RELATIVE_PATH).is_file():
        report.warn(
            f"{SCHEMA_RELATIVE_PATH}: missing - the recipe shape is no longer schema-checkable, so "
            "tests/test_source_builds.py cannot prove it"
        )
    if not builds_path.is_file():
        return report
    document = load_json(builds_path, report, root=paths.root)
    if document is None:
        return report
    for error in validate_builds(document):
        report.error(error)
    for error in shipped_data_matches_schema(paths.root):
        report.error(error)

    builds = load_builds(paths.root)
    if builds.error:
        report.error(f"{_rel(builds_path, paths.root)}: {builds.error}")
        return report
    errors, warnings = recipe_violations(builds, policy=load_policy(paths.root), catalog=catalog)
    for error in errors:
        report.error(error)
    for warning in warnings:
        report.warn(warning)
    return report


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def emit(report: Report, *, strict: bool) -> int:
    annotate = bool(os.environ.get("GITHUB_ACTIONS"))
    for warning in report.warnings:
        print(f"::warning::{warning}" if annotate else f"WARN  {warning}")
    for error in report.errors:
        print(f"::error::{error}" if annotate else f"ERROR {error}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(
                f"## Validation\n\n- **Errors:** {len(report.errors)}\n- **Warnings:** {len(report.warnings)}\n\n"
            )
            for error in report.errors:
                handle.write(f"- ❌ `{error}`\n")
            for warning in report.warnings[:20]:
                handle.write(f"- ⚠️ `{warning}`\n")

    if report.errors:
        print(f"\nFAILED: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
        return 1
    if strict and report.warnings:
        print(f"\nFAILED (strict): {len(report.warnings)} warning(s)")
        return 1
    print(f"\nOK: 0 errors, {len(report.warnings)} warning(s)")
    return 0


def validate_tree(paths: Paths) -> Report:
    report = Report()
    catalog = load_json(paths.catalog, report, root=paths.root)
    if catalog is None:
        return report

    report.extend(validate_catalog(catalog, assets_dir=paths.assets))
    report.extend(validate_assets(catalog, assets_dir=paths.assets))
    report.extend(validate_source_policy(paths, catalog))
    report.extend(validate_source_builds(paths, catalog))

    feed_paths = sorted(p for p in paths.feeds.glob("*.json") if p.name not in ALTSTORE_NON_FEED)
    if not feed_paths:
        report.error("feeds/: no generated feeds found - run scripts/omnisource.py")
    for path in feed_paths:
        feed = load_json(path, report, root=paths.root)
        if feed is not None:
            report.extend(validate_feed(path, feed, root=paths.root))

    report.extend(validate_generated_docs(catalog, paths))
    report.extend(validate_published_app_records(catalog, paths))
    report.extend(validate_api_v2(catalog, paths))
    print(
        f"Validated catalog.json, {len(feed_paths)} AltStore feed(s), the sourcing verdicts and source-build "
        "recipes, and the derived intelligence documents."
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description=__doc__, formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, help="specific feed files (default: catalog + every feed)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    paths = Paths.default()
    report = Report()

    if args.files:
        for raw in args.files:
            path = raw if raw.is_absolute() else paths.root / raw
            feed = load_json(path, report, root=paths.root)
            if feed is not None:
                report.extend(validate_feed(path, feed, root=paths.root))
        return emit(report, strict=args.strict)

    report.extend(validate_tree(paths))
    return emit(report, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
