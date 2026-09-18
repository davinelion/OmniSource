"""Strict, network-free metadata validation and normalization.

This validator is intentionally separate from feed-shape validation.  A feed
can be valid JSON and still be unusable because an app has an invalid bundle
identifier, an empty description, a broken icon, or an ill-formed release
version.  Invalid records are errors; optional enrichment can fill absent
non-critical fields but never invents a download URL or publisher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlparse

BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{1,254}$")
VERSION_RE = re.compile(r"^(?=.*\d)[^\s]{1,128}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
INSTALLABLE_SUFFIXES = (".ipa", ".tipa", ".deb")
CLIENT_TYPES = {
    "altstore",
    "sidestore",
    "flarestore",
    "feather",
    "esign",
    "ksign",
    "livecontainer",
}


@dataclass
class MetadataReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _url(value: Any, *, https_only: bool = True) -> bool:
    if not isinstance(value, str) or len(value) > 4096:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == "https" if https_only else parsed.scheme in {"http", "https"}


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        date.fromisoformat(value[:10])
    except ValueError:
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}(?:[Tt ].*)?$", value))


def _versions(app: dict[str, Any]) -> list[dict[str, Any]]:
    values = app.get("versions")
    if isinstance(values, list) and values:
        return [item for item in values if isinstance(item, dict)]
    return [app]


def normalize_metadata(app: dict[str, Any], *, source_url: str = "") -> dict[str, Any]:
    """Normalize AltStore and OmniSource aliases without contacting a provider."""
    value = dict(app)
    value["name"] = str(value.get("name") or value.get("title") or "").strip()
    value["developerName"] = str(value.get("developerName") or value.get("developer") or "").strip()
    value["localizedDescription"] = str(
        value.get("localizedDescription") or value.get("description") or value.get("subtitle") or ""
    ).strip()
    value["bundleIdentifier"] = str(value.get("bundleIdentifier") or value.get("bundleId") or "").strip()
    value["version"] = str(value.get("version") or value.get("versionName") or "").strip()
    value["versionDate"] = str(value.get("versionDate") or value.get("date") or "").strip()
    value["iconURL"] = str(value.get("iconURL") or value.get("icon") or "").strip()
    value["screenshotURLs"] = list(value.get("screenshotURLs") or value.get("screenshots") or [])
    value["downloadURL"] = str(value.get("downloadURL") or value.get("downloadUrl") or "").strip()
    value["website"] = str(value.get("website") or value.get("homepage") or source_url or "").strip()
    value["category"] = str(value.get("category") or "other").strip().casefold()
    tags = value.get("tags")
    value["tags"] = (
        sorted({str(tag).strip().casefold() for tag in tags if str(tag).strip()}) if isinstance(tags, list) else []
    )
    value["releaseNotes"] = str(value.get("releaseNotes") or value.get("localizedDescription") or "").strip()
    value["changelog"] = str(value.get("changelog") or value.get("versionDescription") or "").strip()
    return value


def validate_release_metadata(version: Any, *, prefix: str = "release") -> MetadataReport:
    report = MetadataReport()
    if not isinstance(version, dict):
        report.error(f"{prefix}: must be an object")
        return report
    normalized = normalize_metadata(version)
    report.normalized = normalized
    if not normalized["version"] or not VERSION_RE.fullmatch(normalized["version"]):
        report.error(f"{prefix}: version must contain a digit and no whitespace")
    if not _iso_date(normalized["versionDate"]):
        report.error(f"{prefix}: versionDate must be ISO YYYY-MM-DD or ISO timestamp")
    if not normalized["downloadURL"] or not _url(normalized["downloadURL"]):
        report.error(f"{prefix}: downloadURL must be HTTPS")
    elif not normalized["downloadURL"].split("?", 1)[0].casefold().endswith(INSTALLABLE_SUFFIXES):
        report.error(f"{prefix}: downloadURL must end in .ipa, .tipa or .deb")
    size = normalized.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        report.error(f"{prefix}: size must be a positive integer")
    digest = normalized.get("sha256") or normalized.get("checksum")
    if digest and not HEX_RE.fullmatch(str(digest)):
        report.error(f"{prefix}: sha256/checksum must be 64 hexadecimal characters")
    return report


def validate_metadata(app: Any, *, prefix: str = "app", strict: bool = False) -> MetadataReport:
    """Validate one normalized/AltStore app and all retained releases."""
    report = MetadataReport()
    if not isinstance(app, dict):
        report.error(f"{prefix}: must be an object")
        return report
    normalized = normalize_metadata(app)
    report.normalized = normalized
    required_fields = ("name", "developerName", "localizedDescription", "bundleIdentifier", "version", "versionDate")
    for required_field in required_fields:
        if not normalized[required_field]:
            report.error(f"{prefix}: missing {required_field}")
    if normalized["bundleIdentifier"] and not BUNDLE_RE.fullmatch(normalized["bundleIdentifier"]):
        report.error(f"{prefix}: invalid bundleIdentifier")
    if normalized["iconURL"] and not _url(normalized["iconURL"]):
        report.error(f"{prefix}: iconURL must be HTTPS")
    elif not normalized["iconURL"]:
        report.error(f"{prefix}: missing iconURL")
    for index, screenshot in enumerate(normalized["screenshotURLs"]):
        if not _url(screenshot):
            report.error(f"{prefix}: screenshotURLs[{index}] must be HTTPS")
    if not normalized["localizedDescription"] or len(normalized["localizedDescription"]) < 8:
        report.warning(f"{prefix}: description is missing or too short")
    if not normalized["releaseNotes"] and not normalized["changelog"]:
        report.warning(f"{prefix}: release notes/changelog unavailable")
    if normalized["website"] and not _url(normalized["website"]):
        report.error(f"{prefix}: website must be HTTPS")
    category = normalized["category"]
    if not category or len(category) > 64:
        report.error(f"{prefix}: category is invalid")
    clients = app.get("clients")
    if clients is not None and (not isinstance(clients, list) or not set(clients).issubset(CLIENT_TYPES)):
        report.error(f"{prefix}: clients contains an unsupported client")
    for index, version in enumerate(_versions(app)):
        release = validate_release_metadata(version, prefix=f"{prefix}.versions[{index}]")
        report.errors.extend(release.errors)
        report.warnings.extend(release.warnings)
    if strict and report.warnings:
        report.errors.extend(f"strict: {warning}" for warning in report.warnings)
    return report


def validate_feed_metadata(feed: Any, *, strict: bool = False) -> MetadataReport:
    """Validate every app in an AltStore-family feed."""
    report = MetadataReport()
    if not isinstance(feed, dict):
        report.error("feed: must be an object")
        return report
    apps = feed.get("apps")
    if not isinstance(apps, list):
        report.error("feed: apps must be an array")
        return report
    seen_bundles: set[str] = set()
    for index, app in enumerate(apps):
        item = validate_metadata(app, prefix=f"apps[{index}]", strict=strict)
        report.errors.extend(item.errors)
        report.warnings.extend(item.warnings)
        if isinstance(app, dict):
            bundle = str(app.get("bundleIdentifier") or "")
            if bundle in seen_bundles:
                report.warning(
                    f"apps[{index}]: duplicate bundleIdentifier {bundle!r} requires an explicit alternative group"
                )
            if bundle:
                seen_bundles.add(bundle)
    return report


__all__ = [
    "MetadataReport",
    "normalize_metadata",
    "validate_feed_metadata",
    "validate_metadata",
    "validate_release_metadata",
]
