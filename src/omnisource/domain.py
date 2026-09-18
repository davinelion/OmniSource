"""Type-safe domain model for the OmniSource feed pipeline.

These dataclasses are the single in-memory representation of catalog entries
and remote releases. Providers produce :class:`RemoteRelease` values; the
pipeline turns them into the version entries of the AltStore feeds.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from omnisource.errors import ConfigurationError, SyncError


class SourceType(StrEnum):
    """Supported upstream kinds. Values are the catalog ``upstream.provider`` ids."""

    GITHUB_RELEASES = "github"
    GITHUB_TAGS = "github-tags"
    GITLAB_RELEASES = "gitlab"
    CODEBERG_RELEASES = "codeberg"
    FORGEJO_RELEASES = "forgejo"
    JSON_FEED = "json-feed"
    ALTSTORE = "altstore"
    FEATHER = "feather"
    DIRECT = "direct"
    ARCHIVE = "archive"
    MIRROR = "mirror"
    MANUAL = "manual"

    @classmethod
    def parse(cls, value: str | None) -> SourceType:
        if not value:
            return cls.GITHUB_RELEASES
        try:
            return cls(value)
        except ValueError as error:
            known = ", ".join(item.value for item in cls)
            raise ConfigurationError(f"unknown upstream.provider '{value}' (expected one of {known})") from error

    @property
    def is_url_source(self) -> bool:
        """Sources addressed by a single URL rather than a forge ``repo``."""
        return self in {
            SourceType.DIRECT,
            SourceType.ARCHIVE,
            SourceType.MIRROR,
            SourceType.JSON_FEED,
            SourceType.ALTSTORE,
            SourceType.FEATHER,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


_TODAY_OVERRIDE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today() -> str:
    """The build date, or the ``OMNISOURCE_TODAY`` pin when set.

    Everything date-derived in the generated set (recency scores, trending
    windows, freshness decay, ``generatedAt``) flows through this clock, so a
    rebuild can only reproduce committed bytes for the day they were built.
    ``scripts/check_reproducible.py`` sets ``OMNISOURCE_TODAY`` to the date
    committed in the feeds and rebuilds *as of that day* — the offline build
    is then genuinely a no-op instead of silently shifting every score at
    UTC midnight. Anything that is not a strict ``YYYY-MM-DD`` is ignored so
    a malformed value can never masquerade as a clock.
    """
    override = os.environ.get("OMNISOURCE_TODAY", "")
    if _TODAY_OVERRIDE_RE.match(override):
        return override
    return utc_now().strftime("%Y-%m-%d")


def today_date() -> date:
    """:func:`today` as a ``date`` — the single pinnable clock for arithmetic."""
    return date.fromisoformat(today())


@dataclass(frozen=True)
class RemoteAsset:
    """A downloadable file attached to a remote release, source-agnostic.

    Providers may not know every attribute without downloading the package;
    unknown values remain empty instead of being guessed.
    """

    name: str
    download_url: str
    size: int = 0
    sha256: str | None = None
    content_type: str = ""
    platform: str = "unknown"
    architecture: str | None = None
    file_type: str = "other"
    installable: bool = False
    download_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        from omnisource.utils.assets import detect_asset_metadata

        detected = detect_asset_metadata(self.name, self.download_url, mime_type=self.content_type)
        file_type = self.file_type if self.file_type != "other" else str(detected["fileType"])
        platform = self.platform if self.platform != "unknown" else str(detected["platform"])
        architecture = self.architecture or detected["architecture"]
        installable = self.installable or bool(detected["installable"])
        return {
            "filename": self.name,
            "downloadUrl": self.download_url,
            "platform": platform,
            "architecture": architecture,
            "fileType": file_type,
            "size": self.size,
            "sha256": self.sha256,
            "downloads": self.download_count,
            "mimeType": self.content_type or None,
            "installable": installable,
        }


@dataclass(frozen=True)
class RemoteRelease:
    """A published release from any forge or feed, source-agnostic."""

    tag: str
    name: str
    body: str
    published_at: str
    assets: tuple[RemoteAsset, ...] = ()
    prerelease: bool = False
    draft: bool = False
    build_number: str | None = None
    release_url: str | None = None
    source: str = ""

    @property
    def is_published(self) -> bool:
        """Whether the release is published rather than a draft.

        Pre-releases are published records too. The configured provider policy
        decides whether they are eligible for the current application feed.
        """
        return not self.draft


@dataclass(frozen=True)
class DiscoveredApp:
    """Lightweight identity returned by ``discover_apps()``."""

    app_id: str
    name: str
    repository_url: str
    source_type: SourceType
    developer: str = ""
    description: str = ""


@dataclass(frozen=True)
class AppMetadata:
    """Source-level metadata returned by ``fetch_metadata()``."""

    name: str
    developer: str
    description: str
    icon: str = ""
    homepage: str = ""
    license: str = ""
    stars: int | None = None
    topics: tuple[str, ...] = ()
    default_branch: str = ""
    archived: bool = False


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of ``validate_repository()``."""

    ok: bool
    detail: str
    repository_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepositoryRef:
    """How to reach an upstream. Built from a catalog ``upstream`` block."""

    provider: SourceType
    repo: str = ""
    host: str = ""
    feed_url: str = ""
    # Feed providers publish many apps in one document; this selects one of
    # them by id, bundle identifier or name.
    app_id: str = ""
    # For single-asset sources (direct/mirror/archive) there is no release
    # API to discover a version; the catalog supplies it explicitly.
    version: str = ""
    version_date: str = ""
    # Failover legs: additional upstream blocks (same shape as ``upstream``)
    # tried in order when the primary yields nothing or fails.
    mirrors: tuple[dict[str, Any], ...] = ()
    tag_prefix: str = ""
    exclude_tag_prefixes: tuple[str, ...] = ()
    asset_suffixes: tuple[str, ...] = (".ipa",)
    asset_name_pattern: str = ""
    version_pattern: str = ""
    max_pages: int = 3
    keep_versions: int = 1
    sort_by_tag_number: bool = False
    version_from_tag: bool = False
    include_prereleases: bool = False
    include_drafts: bool = False
    description_template: str = "{name} {version} | {label}"
    min_os_version: str = "16.0"
    min_os_by_tag_number: dict[str, str] = field(default_factory=dict)
    iso_dates: bool = False
    request_timeout: float = 30.0

    @property
    def cache_key(self) -> tuple[str, str, str, int]:
        return (self.provider.value, self.host, self.repo or self.feed_url, self.max_pages)

    @property
    def is_feed(self) -> bool:
        return self.provider in {
            SourceType.JSON_FEED,
            SourceType.ALTSTORE,
            SourceType.FEATHER,
            SourceType.DIRECT,
            SourceType.ARCHIVE,
            SourceType.MIRROR,
        }

    @property
    def is_url_source(self) -> bool:
        return self.provider.is_url_source

    @property
    def identity(self) -> str:
        """Stable identifier of the upstream *source* (not of one app in it)."""
        return self.feed_url if self.is_feed else (self.repo or self.feed_url)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> RepositoryRef:
        provider = SourceType.parse(raw.get("provider"))
        repo = str(raw.get("repo") or "")
        app_id = str(raw.get("appId") or "")
        # ``url`` is the single-asset alias for direct/mirror/archive sources;
        # ``feedURL`` remains the canonical key for document feeds.
        feed_url = str(raw.get("feedURL") or raw.get("feedUrl") or raw.get("url") or "")
        if provider.is_url_source:
            if not feed_url:
                raise ConfigurationError(f"{provider.value} providers require upstream.url (or feedURL)")
        elif not repo:
            raise ConfigurationError("forge providers require upstream.repo")
        mirrors_raw = raw.get("mirrors")
        mirrors = tuple(item for item in mirrors_raw if isinstance(item, dict)) if isinstance(mirrors_raw, list) else ()
        return cls(
            provider=provider,
            repo=repo,
            app_id=app_id,
            host=str(raw.get("host") or "").rstrip("/"),
            feed_url=feed_url,
            version=str(raw.get("version") or ""),
            version_date=str(raw.get("versionDate") or ""),
            mirrors=mirrors,
            tag_prefix=raw.get("tagPrefix", ""),
            exclude_tag_prefixes=tuple(raw.get("excludeTagPrefixes", ())),
            asset_suffixes=tuple(raw.get("assetSuffixes", (".ipa",))),
            asset_name_pattern=raw.get("assetNamePattern", ""),
            version_pattern=raw.get("versionPattern", ""),
            max_pages=int(raw.get("maxPages", 3)),
            keep_versions=int(raw.get("keepVersions", 1)),
            sort_by_tag_number=bool(raw.get("sortByTagNumber", False)),
            version_from_tag=bool(raw.get("versionFromTag", False)),
            include_prereleases=bool(raw.get("includePrereleases", False)),
            include_drafts=bool(raw.get("includeDrafts", False)),
            description_template=raw.get("descriptionTemplate", "{name} {version} | {label}"),
            min_os_version=raw.get("minOSVersion", "16.0"),
            min_os_by_tag_number=dict(raw.get("minOSVersionByTagNumber", {})),
            iso_dates=bool(raw.get("isoDates", False)),
            request_timeout=float(raw.get("timeout", 30.0)),
        )


@dataclass
class App:
    """One catalog entry. ``raw`` is the original JSON object."""

    slug: str
    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw["name"])

    @property
    def icon(self) -> str:
        return str(self.raw.get("icon", "OmniSource.png"))

    @property
    def short_description(self) -> str:
        return str(self.raw.get("shortDescription") or self.raw.get("subtitle") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "stable"))

    @property
    def lifecycle_status(self) -> str:
        """Return the canonical lifecycle state without conflating trust."""
        value = self.raw.get("lifecycleStatus") or self.raw.get("lifecycle_status")
        if value:
            return str(value)
        return {
            "stable": "active",
            "beta": "active",
            "manual": "maintenance",
            "unmaintained": "inactive",
            "deprecated": "archived",
        }.get(self.status, "unknown")

    @property
    def featured(self) -> bool:
        return bool(self.raw.get("featured", False))

    @property
    def category(self) -> str:
        return str(self.raw.get("category", "other"))

    @property
    def categories(self) -> tuple[str, ...]:
        raw = self.raw.get("categories")
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
            if values:
                return tuple(dict.fromkeys(values))
        return (self.category,) if self.category else ()

    @property
    def platforms(self) -> tuple[str, ...]:
        raw = self.raw.get("platforms")
        if isinstance(raw, list):
            return tuple(dict.fromkeys(str(item).lower() for item in raw if str(item).strip()))
        compatibility = self.raw.get("compatibility")
        clients = compatibility.get("clients") if isinstance(compatibility, dict) else []
        # Existing entries are iOS sideloading entries. This is a source of
        # observable metadata, not a claim that every client supports Android.
        return ("ios",) if clients or self.bundle_id else ()

    @property
    def homepage(self) -> str | None:
        value = self.raw.get("homepage") or self.raw.get("homepageURL")
        return str(value) if value else self.repository_url or None

    @property
    def documentation(self) -> str | None:
        value = self.raw.get("documentation") or self.raw.get("documentationURL")
        return str(value) if value else None

    @property
    def license(self) -> str | None:
        value = self.raw.get("license")
        return str(value) if value else None

    @property
    def package_name(self) -> str | None:
        value = self.raw.get("packageName") or self.raw.get("package_name")
        return str(value) if value else None

    @property
    def minimum_ios_version(self) -> str | None:
        compatibility = self.raw.get("compatibility")
        value = self.raw.get("minimumIOSVersion")
        if value is None and isinstance(compatibility, dict):
            value = compatibility.get("minOSVersion")
        return str(value) if value else None

    @property
    def minimum_android_version(self) -> str | None:
        value = self.raw.get("minimumAndroidVersion") or self.raw.get("minAndroidVersion")
        return str(value) if value else None

    @property
    def aliases(self) -> tuple[str, ...]:
        raw = self.raw.get("aliases")
        return tuple(str(item) for item in raw if str(item).strip()) if isinstance(raw, list) else ()

    @property
    def verified(self) -> bool:
        return bool(self.raw.get("verified", False))

    @property
    def developer(self) -> str:
        return str(self.raw.get("developerName", ""))

    @property
    def bundle_id(self) -> str:
        return str(self.raw.get("bundleIdentifier", ""))

    @property
    def description(self) -> str:
        return str(self.raw.get("localizedDescription", ""))

    @property
    def screenshots(self) -> list[str]:
        raw = self.raw.get("screenshots") or []
        return [url for url in raw if isinstance(url, str)]

    @property
    def tags(self) -> tuple[str, ...]:
        raw = self.raw.get("tags")
        if isinstance(raw, list):
            return tuple(str(tag) for tag in raw if tag)
        return (self.category,) if self.category else ()

    @property
    def upstream(self) -> RepositoryRef | None:
        raw = self.raw.get("upstream")
        return RepositoryRef.parse(raw) if isinstance(raw, dict) else None

    @property
    def manual_release(self) -> dict[str, Any] | None:
        raw = self.raw.get("manualRelease")
        return dict(raw) if isinstance(raw, dict) else None

    @property
    def source_type(self) -> SourceType:
        if self.upstream is None:
            return SourceType.MANUAL
        return self.upstream.provider

    @property
    def repository_url(self) -> str:
        explicit = self.raw.get("upstreamURL")
        if isinstance(explicit, str) and explicit:
            return explicit
        up = self.upstream
        if up is None:
            return ""
        if up.feed_url:
            return up.feed_url
        host = up.host or _default_host(up.provider)
        if up.repo and host:
            return f"{host}/{up.repo}"
        return ""

    @property
    def source_url(self) -> str:
        """Link for the source that actually publishes the sideload IPA.

        ``upstreamURL``/``repository_url`` names the official project page
        (which often ships only a .deb tweak or release notes). Apps served as
        ready-to-sideload IPAs by a different project - a build fork such as
        ``mrdrvt99/YouProEXTRA``, a mirror repository, or an AltStore/JSON
        feed - must link to that *source* instead. An explicit ``sourceURL``
        in the catalog wins; otherwise the URL is derived from the upstream
        configuration (feed origin or forge host + repo), falling back to the
        official project page when the source is the official project.
        """
        explicit = self.raw.get("sourceURL")
        if isinstance(explicit, str) and explicit:
            return explicit
        up = self.upstream
        if up is None:
            return self.repository_url
        if up.feed_url:
            try:
                parts = urlsplit(up.feed_url)
            except ValueError:
                return up.feed_url
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
            return up.feed_url
        host = up.host or _default_host(up.provider)
        if up.repo and host:
            return f"{host}/{up.repo}"
        return self.repository_url


def _default_host(provider: SourceType) -> str:
    return {
        SourceType.GITHUB_RELEASES: "https://github.com",
        SourceType.GITHUB_TAGS: "https://github.com",
        SourceType.GITLAB_RELEASES: "https://gitlab.com",
        SourceType.CODEBERG_RELEASES: "https://codeberg.org",
        SourceType.FORGEJO_RELEASES: "",
    }.get(provider, "")


@dataclass
class Catalog:
    source: dict[str, Any]
    clients: list[dict[str, Any]]
    apps: list[App]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Catalog:
        apps = []
        for entry in raw.get("apps", []):
            app = App(slug=entry["slug"], raw=entry)
            _ = app.upstream  # parse/validate now so a bad catalog fails at load
            apps.append(app)
        if not apps:
            raise SyncError("catalog.json declares no apps")
        return cls(source=raw.get("source", {}), clients=raw.get("clients", []), apps=apps)

    @property
    def base_url(self) -> str:
        return str(self.source.get("baseURL", "")).rstrip("/")

    def app_by_slug(self, slug: str) -> App | None:
        return next((app for app in self.apps if app.slug == slug), None)


@dataclass(frozen=True)
class StandardizedApp:
    """Unified metadata record, independent of the AltStore schema.

    Field names mirror the catalog keys so the record can be serialised into
    machine feeds without a second mapping layer.
    """

    app_id: str
    name: str
    developer: str
    description: str
    icon: str
    screenshots: tuple[str, ...]
    category: str
    version: str
    build_number: str | None
    release_date: str
    bundle_id: str
    minimum_os_version: str
    source_type: str
    repository_url: str
    changelog: str
    download_url: str
    sha256: str | None
    size: int = 0
    status: str = "stable"
    featured: bool = False
    tags: tuple[str, ...] = ()
    fallback_download_urls: tuple[str, ...] = ()
    short_description: str = ""
    # Canonical fields. Defaults keep the constructor source-compatible for
    # downstream feed tooling.
    app_categories: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    homepage: str | None = None
    documentation: str | None = None
    license: str | None = None
    package_name: str | None = None
    minimum_android_version: str | None = None
    lifecycle_status: str = "unknown"
    verified: bool = False
    last_updated: str = ""
    health: dict[str, Any] = field(default_factory=dict)
    integrity: dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    releases: tuple[dict[str, Any], ...] = ()
    download_assets: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Serialize both the historical snapshot keys and the canonical keys."""
        primary_assets = list(self.download_assets)
        if not primary_assets and self.download_url:
            primary_assets = [
                {
                    "filename": "",
                    "downloadUrl": self.download_url,
                    "platform": (self.platforms[0] if self.platforms else "unknown"),
                    "architecture": None,
                    "fileType": "IPA" if self.download_url.lower().split("?", 1)[0].endswith(".ipa") else "other",
                    "size": self.size,
                    "sha256": self.sha256,
                    "installable": True,
                }
            ]
        categories = list(self.app_categories or ((self.category,) if self.category else ()))
        releases = list(self.releases)
        if not releases:
            releases = [
                {
                    "version": self.version,
                    "build": self.build_number,
                    "releaseDate": self.release_date,
                    "releaseNotes": self.changelog,
                    "releaseUrl": self.repository_url or None,
                    "assets": primary_assets,
                    "source": self.source_type,
                    "isPrerelease": False,
                    "isDraft": False,
                }
            ]
        return {
            # Canonical identity and metadata.
            "id": self.app_id,
            "appId": self.app_id,
            "name": self.name,
            "developer": self.developer,
            "description": self.description,
            "shortDescription": self.short_description,
            "icon": self.icon,
            "screenshots": list(self.screenshots),
            "category": self.category,
            "categories": categories,
            "tags": list(self.tags),
            "repository": self.repository_url or None,
            "repositoryUrl": self.repository_url,
            "sourceType": self.source_type,
            "homepage": self.homepage,
            "documentation": self.documentation,
            "license": self.license,
            "platforms": list(self.platforms),
            "bundleId": self.bundle_id,
            "bundleIdentifier": self.bundle_id,
            "packageName": self.package_name,
            "minimumIOSVersion": self.minimum_os_version,
            "minimumAndroidVersion": self.minimum_android_version,
            # Historical aliases kept for compatibility with earlier feed tooling.
            "minimumOSVersion": self.minimum_os_version,
            "version": self.version,
            "buildNumber": self.build_number,
            "releaseDate": self.release_date,
            "changelog": self.changelog,
            "downloadUrl": self.download_url,
            "sha256": self.sha256,
            "size": self.size,
            "status": self.lifecycle_status,
            "lifecycleStatus": self.lifecycle_status,
            "legacyStatus": self.status,
            "featured": self.featured,
            "verified": self.verified,
            "lastUpdated": self.last_updated or self.release_date,
            "latestVersion": self.version,
            "latestBuild": self.build_number,
            "latestReleaseDate": self.release_date,
            "latestReleaseNotes": self.changelog,
            "downloadAssets": primary_assets,
            "versions": releases,
            "fallbackDownloadUrls": list(self.fallback_download_urls),
            "health": dict(self.health),
            "integrity": dict(self.integrity),
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class HealthSnapshot:
    reachable: bool
    detail: str
    since: str


@dataclass
class UpdateEvent:
    app_id: str
    name: str
    version: str
    previous_version: str | None
    release_date: str
    download_url: str
    changelog: str
    kind: str  # "new" | "updated" | "unchanged"

    def to_json(self) -> dict[str, Any]:
        return {
            "appId": self.app_id,
            "name": self.name,
            "version": self.version,
            "previousVersion": self.previous_version,
            "releaseDate": self.release_date,
            "downloadUrl": self.download_url,
            "changelog": self.changelog,
            "kind": self.kind,
        }


@dataclass
class SyncReport:
    """Structured summary of one pipeline run, written to the job summary."""

    apps_total: int = 0
    apps_synced: int = 0
    apps_incremental_hit: int = 0
    apps_failed: int = 0
    apps_updated: int = 0
    api_requests: int = 0
    files_changed: int = 0
    updates: list[UpdateEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    repositories_checked: int = 0
    repositories_succeeded: int = 0
    repositories_failed: int = 0
    new_apps: int = 0
    broken_assets: int = 0
    started_at: str = ""
    finished_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "repositoriesChecked": self.repositories_checked,
            "successful": self.repositories_succeeded,
            "failed": self.repositories_failed,
            "appsUpdated": self.apps_updated,
            "newApps": self.new_apps,
            "brokenAssets": self.broken_assets,
            "appsTotal": self.apps_total,
            "appsSynced": self.apps_synced,
            "appsFailed": self.apps_failed,
            "httpRequests": self.api_requests,
            "filesChanged": self.files_changed,
            "errors": list(self.errors),
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }
