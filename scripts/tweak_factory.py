#!/usr/bin/env python3
"""Tweak Factory — collect official tweaks, inject them, publish to OmniSource.

The factory turns the manual "Build & Inject Tweak" lane into a scheduled
pipeline with the same policy line as everything else in this repository:

1. **Collect** the newest official ``.deb`` for each registered tweak straight
   from its own upstream releases (GitHub API) — the same sources the catalog
   already trusts.
2. **Build** by injecting that deb into a *base app the operator supplies*
   (their own dumps repo / a repo variable / a fixed URL). The factory never
   downloads a base from a public decrypted-app store — those hosts are blocked
   by ``data/source_policy.json`` and every URL is re-checked here with the
   same policy module before anything is downloaded.
3. **Publish** the result as a tagged release on this repository with the
   deb/base versions, SHA-256 digests and the run link in the notes, record the
   build in ``data/tweak-builds-state.json``, commit it and dispatch
   ``sync.yml`` so the feeds pick up anything that references the new tag.

An entry injects either one ``deb`` or an ordered ``debs`` **bundle**: one base app
plus several officially published tweaks in a single deterministic build.  The
bundle's version is a digest over its members' versions, so any member moving
triggers exactly one rebuild, ``conflictGroups`` refuse a bundle whose members
hook the same part of the host app before a runner is claimed, and unsatisfied
tweak dependencies are reported instead of silently half-injected.  Tweaks that
publish only to their own apt repository (``deb.source: "apt-repository"``) are
collected from that repository's ``Packages`` index, and the SHA-256 the
developer published in the index is the digest the build is recorded with.

Everything is idempotent: a build whose deb *and* base are unchanged and whose
release already exists is skipped, so the weekly schedule costs nothing when
nothing moved, and an empty matrix claims no macOS runner at all.

Commands:
    plan      resolve newest deb+base per build, print the matrix + a summary
    publish   validate one finished build and write a state fragment
    merge     merge state fragments into data/tweak-builds-state.json
    notes     render the release notes for a build (stdout)
    status    show the committed state
    validate  check the registry only

Examples:
    python3 scripts/tweak_factory.py plan --matrix-out /tmp/matrix.json
    python3 scripts/tweak_factory.py publish --slug ytlite --tag tweak-build/ytlite/v5.2.2 ...
    python3 scripts/tweak_factory.py merge fragments/*.json --out data/tweak-builds-state.json
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnisource.apt_index import AptIndexError
from omnisource.apt_index import resolve_deb as resolve_apt_deb
from omnisource.domain import today
from omnisource.io import atomic_write_text
from omnisource.source_policy import decide, load_policy
from omnisource.utils.versioning import compare_versions

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "tweak-builds.json"
STATE_PATH = ROOT / "data" / "tweak-builds-state.json"
CATALOG_PATH = ROOT / "catalog.json"
USER_AGENT = "omnisource-tweak-factory (+https://iamsmmh.github.io/OmniSource)"
RELEASES_URL = "https://api.github.com/repos/{repo}/releases?per_page=30"
RELEASE_TAG_URL = "https://api.github.com/repos/{repo}/releases/tags/{tag}"
VARIABLE_URL = "https://api.github.com/repos/{repo}/actions/variables/{name}"
ARCHES = ("arm64", "arm64e", "arm")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]*$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./_-]*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://[^\s]+$")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9+-]*(?:\.[a-z0-9][a-z0-9+-]*)+$")
VERSION_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.~_+-]*$")
DEB_SOURCES = ("github-release", "apt-repository")
MAX_BUNDLE_MEMBERS = 8
APT_INDEX_RE = re.compile(r"^https://[^\s]+/Packages(?:\.(?:gz|bz2|xz|lzma|lz4))?$")
BUNDLE_SEP = "|"
_BUILD_ARG_FLAGS = (
    "slug",
    "tag",
    "deb-version",
    "base-app",
    "base-version",
    "base-url",
    "asset-name",
)


class FactoryError(Exception):
    """A registry, resolution or artifact problem that must skip the build."""


@dataclass(frozen=True)
class Resolved:
    """A resolved artifact: which version, where to download it, its name."""

    version: str
    url: str
    name: str = ""
    tag: str = ""
    architecture: str = ""
    sha256: str = ""
    depends: tuple[str, ...] = ()
    package_id: str = ""


@dataclass(frozen=True)
class DebSpec:
    """Where one tweak's official ``.deb`` lives, as registered by the operator."""

    label: str
    source: str
    repo: str = ""
    asset_regex: re.Pattern[str] | None = None
    arch_preference: tuple[str, ...] = ARCHES
    index_url: str = ""
    suite: str = ""
    component: str = ""
    package: str = ""
    pin_version: str = ""
    package_id: str = ""


@dataclass(frozen=True)
class ConflictGroup:
    """Package ids that must never be injected into the same app build."""

    name: str
    packages: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class Build:
    slug: str
    name: str
    enabled: bool
    catalog_app: str
    deb_repo: str
    deb_regex: re.Pattern[str]
    arch_preference: tuple[str, ...]
    base_app: str
    bundle_id: str
    app_name: str
    tag_prefix: str
    prerelease: bool
    members: tuple[DebSpec, ...] = ()
    note: str = ""

    @property
    def is_bundle(self) -> bool:
        """A bundle injects more than one tweak into the same base app."""
        return len(self.members) > 1

    @property
    def primary(self) -> DebSpec:
        return self.members[0]


@dataclass(frozen=True)
class Registry:
    base_apps: dict[str, dict[str, Any]]
    builds: tuple[Build, ...]
    conflict_groups: tuple[ConflictGroup, ...] = ()


# ---------------------------------------------------------------------------
# Registry loading / validation
# ---------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FactoryError(message)


def _parse_member(item: Any, *, slug: str, fallback_label: str = "") -> DebSpec:
    """Validate one ``deb`` / ``debs[]`` entry into a :class:`DebSpec`."""
    where = f"build {slug}"
    _require(isinstance(item, dict), f"{where}: every deb entry must be an object")
    label = str(item.get("label") or fallback_label)
    _require(LABEL_RE.match(label) is not None, f"{where}: deb label {label!r} is not a label (lowercase, digits, -)")
    source = item.get("source")
    _require(source in DEB_SOURCES, f"{where}: {label} deb source must be one of {DEB_SOURCES}")
    arches = tuple(item.get("archPreference") or ())
    _require(all(arch in ARCHES for arch in arches), f"{where}: {label} archPreference may only use {ARCHES}")
    package_id = str(item.get("packageId") or "")
    _require(
        not package_id or PACKAGE_ID_RE.match(package_id) is not None,
        f"{where}: {label} packageId must be a reverse-DNS package id (got {package_id!r})",
    )
    pin_version = str(item.get("pinVersion") or "")
    _require(
        not pin_version or VERSION_PIN_RE.match(pin_version) is not None,
        f"{where}: {label} pinVersion has invalid characters",
    )
    if source == "github-release":
        _require(_is_repo(item.get("repo")), f"{where}: {label} deb.repo must be owner/name")
        try:
            asset_regex = re.compile(str(item.get("assetRegex", "")))
        except re.error as error:
            raise FactoryError(f"{where}: {label} deb.assetRegex does not compile: {error}") from error
        return DebSpec(
            label=label,
            source=str(source),
            repo=str(item["repo"]),
            asset_regex=asset_regex,
            arch_preference=arches or ARCHES,
            pin_version=pin_version,
            package_id=package_id,
        )
    index_url, base, package = (
        str(item.get("indexUrl") or ""),
        str(item.get("repo") or ""),
        str(item.get("package") or ""),
    )
    _require(
        bool(index_url) != bool(base), f"{where}: {label} needs either indexUrl (flat repo) or repo+suite (dists repo)"
    )
    if index_url:
        _require(
            APT_INDEX_RE.match(index_url) is not None,
            f"{where}: {label} indexUrl must be an https URL ending in Packages[.gz|.bz2|.xz]",
        )
    else:
        _require(URL_RE.match(base) is not None, f"{where}: {label} repo must be an https URL")
        _require(str(item.get("suite") or ""), f"{where}: {label} needs a suite for a dists-layout repository")
    _require(PACKAGE_ID_RE.match(package) is not None, f"{where}: {label} package must be a Debian package id")
    return DebSpec(
        label=label,
        source=str(source),
        repo=base,
        # The apt index lists Debian architecture names, not the bare arm64 the
        # GitHub-release lane uses, so the preference is expanded here.
        arch_preference=tuple(f"iphoneos-{arch}" for arch in (arches or ARCHES)),
        index_url=index_url,
        suite=str(item.get("suite") or ""),
        component=str(item.get("component") or ""),
        package=package,
        pin_version=pin_version,
        package_id=package_id or package,
    )


def _parse_conflict_groups(value: Any) -> tuple[ConflictGroup, ...]:
    """Validate ``conflictGroups``: ids that must not share one injected build."""
    if value is None:
        return ()
    _require(isinstance(value, list), "registry conflictGroups must be an array")
    groups: list[ConflictGroup] = []
    names: set[str] = set()
    for group in value:
        _require(isinstance(group, dict), "every conflictGroups entry must be an object")
        name = str(group.get("name", ""))
        _require(SLUG_RE.match(name) is not None, f"conflictGroups name {name!r} is not a slug")
        _require(name not in names, f"conflictGroups name {name!r} is registered twice")
        names.add(name)
        packages = group.get("packages")
        _require(isinstance(packages, list) and len(packages) >= 2, f"conflictGroups.{name} must list 2+ packages")
        for package in packages:
            _require(
                PACKAGE_ID_RE.match(str(package)) is not None,
                f"conflictGroups.{name}: {package!r} is not a package id",
            )
        _require(str(group.get("reason") or ""), f"conflictGroups.{name} must say why these conflict")
        groups.append(ConflictGroup(name=name, packages=tuple(str(x) for x in packages), reason=str(group["reason"])))
    return tuple(groups)


def _catalog_slugs(path: Path = CATALOG_PATH) -> set[str]:
    """The slugs catalog.json publishes; a factory build may only serve one."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"catalog {path} is unreadable: {error}") from error
    apps = document.get("apps") if isinstance(document, dict) else None
    _require(isinstance(apps, list), f"catalog {path} has no apps array")
    return {str(app.get("slug", "")) for app in apps if isinstance(app, dict)}


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    """Parse and fully validate the registry. Any problem is a FactoryError."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FactoryError(f"registry {path} is missing") from error
    except json.JSONDecodeError as error:
        raise FactoryError(f"registry {path} is not valid JSON: {error}") from error
    _require(isinstance(raw, dict), "registry must be a JSON object")
    _require(raw.get("version") == 1, "registry version must be 1")

    base_apps = raw.get("baseApps")
    _require(isinstance(base_apps, dict), "registry baseApps must be an object")
    for key, cfg in base_apps.items():
        _require(SLUG_RE.match(key) is not None, f"baseApps key {key!r} is not a slug")
        _require(isinstance(cfg, dict), f"baseApps.{key} must be an object")
        source = cfg.get("source")
        _require(source in ("release", "url", "variable"), f"baseApps.{key}.source must be release|url|variable")
        if source == "release":
            _require(_is_repo(cfg.get("repo")), f"baseApps.{key}.repo must be owner/name")
            _require(isinstance(cfg.get("tagPrefix"), str) and cfg["tagPrefix"], f"baseApps.{key}.tagPrefix required")
            _require(isinstance(cfg.get("assetGlob"), str) and cfg["assetGlob"], f"baseApps.{key}.assetGlob required")
        elif source == "url":
            _require(URL_RE.match(str(cfg.get("url", ""))) is not None, f"baseApps.{key}.url must be an https URL")
        else:
            _require(
                re.match(r"^[A-Z_][A-Z0-9_]*$", str(cfg.get("envVar", ""))) is not None,
                f"baseApps.{key}.envVar must be an environment variable name",
            )

    groups = _parse_conflict_groups(raw.get("conflictGroups"))

    builds_raw = raw.get("builds")
    _require(isinstance(builds_raw, list), "registry builds must be an array")
    builds: list[Build] = []
    seen: set[str] = set()
    catalog_slugs: set[str] | None = None
    for item in builds_raw:
        _require(isinstance(item, dict), "every build must be an object")
        slug = str(item.get("slug", ""))
        _require(SLUG_RE.match(slug) is not None, f"build slug {slug!r} is not a slug")
        _require(slug not in seen, f"build slug {slug!r} is registered twice")
        seen.add(slug)
        name = str(item.get("name", ""))
        _require(NAME_RE.match(name) is not None, f"build {slug}: name must be printable (got {name!r})")
        bundle_id = str(item.get("bundleId") or "")
        _require(
            not bundle_id or re.match(r"^[A-Za-z0-9.-]+$", bundle_id) is not None,
            f"build {slug}: bundleId may only use letters, digits, dots and dashes",
        )
        _require(bool(item.get("deb")) ^ bool(item.get("debs")), f"build {slug}: register exactly one of deb or debs")
        entries = [item["deb"]] if item.get("deb") else item.get("debs")
        _require(isinstance(entries, list) and entries, f"build {slug}: debs must be a non-empty array")
        _require(
            len(entries) <= MAX_BUNDLE_MEMBERS, f"build {slug}: a bundle injects at most {MAX_BUNDLE_MEMBERS} tweaks"
        )
        members: list[DebSpec] = []
        labels: set[str] = set()
        for index, entry in enumerate(entries):
            member = _parse_member(entry, slug=slug, fallback_label=slug if index == 0 else "")
            _require(member.label not in labels, f"build {slug}: member label {member.label!r} appears twice")
            labels.add(member.label)
            members.append(member)
        base_app = str(item.get("base", ""))
        _require(base_app in base_apps, f"build {slug}: base {base_app!r} is not defined in baseApps")
        publish = item.get("publish") or {}
        tag_prefix = str(publish.get("tagPrefix") or f"tweak-build/{slug}")
        _require(TAG_RE.match(tag_prefix) is not None, f"build {slug}: publish.tagPrefix has invalid characters")
        catalog_app = str(item["catalogApp"]) if "catalogApp" in item else slug
        if catalog_app:
            # A factory build only reaches a client once a hand-curated catalog
            # entry points at its tag namespace; a slug that is not a catalog app
            # at all is a typo, and silently dropping it would hide that.
            if catalog_slugs is None:
                catalog_slugs = _catalog_slugs()
            _require(
                catalog_app in catalog_slugs,
                f"build {slug}: catalogApp {catalog_app!r} is not in catalog.json "
                "(set an empty catalogApp for an operator-only build that never reaches the feeds)",
            )
        first = members[0]
        builds.append(
            Build(
                slug=slug,
                name=name,
                enabled=bool(item.get("enabled", True)),
                catalog_app=catalog_app,
                deb_repo=first.repo,
                deb_regex=first.asset_regex or re.compile("(?!)"),
                arch_preference=first.arch_preference,
                base_app=base_app,
                bundle_id=bundle_id,
                app_name=str(item.get("appName") or ""),
                tag_prefix=tag_prefix,
                prerelease=bool(publish.get("prerelease", True)),
                members=tuple(members),
                note=str(item.get("note") or ""),
            )
        )
    return Registry(base_apps=dict(base_apps), builds=tuple(builds), conflict_groups=groups)


def _is_repo(value: Any) -> bool:
    return isinstance(value, str) and re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", value) is not None


# ---------------------------------------------------------------------------
# GitHub resolution
# ---------------------------------------------------------------------------


def _get_json(url: str, *, token: str | None) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        raise FactoryError(f"GitHub API {error.code} for {urllib.parse.urlsplit(url).path}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise FactoryError(f"GitHub API unreachable ({error})") from error


def _token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def repository_slug() -> str:
    """The repository the factory publishes into (env override, else catalog)."""
    from_env = os.environ.get("GITHUB_REPOSITORY")
    if from_env and _is_repo(from_env):
        return from_env
    try:
        source = json.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("source", {})
        parsed = urllib.parse.urlsplit(str(source.get("repository", "")))
        if parsed.netloc == "github.com" and _is_repo(parsed.path.lstrip("/")):
            return parsed.path.lstrip("/")
    except (OSError, json.JSONDecodeError):
        pass
    raise FactoryError("cannot determine the target repository (set GITHUB_REPOSITORY)")


def _pick_asset(release: dict[str, Any], pattern: re.Pattern[str], arches: tuple[str, ...]) -> dict[str, Any] | None:
    """Choose the release asset matching the pattern, preferring arch order."""
    matches = [asset for asset in release.get("assets") or [] if pattern.match(str(asset.get("name", "")))]
    if not matches:
        return None
    for arch in arches:
        for asset in matches:
            if re.search(rf"[-._]iPhoneOS[-._]{arch}\.|_iphoneos-{arch}\.", str(asset.get("name", "")), re.IGNORECASE):
                return asset
    return matches[0]


def _bare_version(version: str) -> str:
    """Normalize ``v5.2.2`` / ``5.2.2`` to ``5.2.2`` for tags, assets, compares."""
    return version[1:] if version[:1] in ("v", "V") else version


def resolve_deb(build: Build, *, token: str | None) -> Resolved:
    """Newest official deb for a single-tweak build, from its own upstream."""
    return _resolve_from_github(build.primary, token=token)


def resolve_member(build: Build, spec: DebSpec, *, token: str | None) -> Resolved:
    """Newest official deb for one registered bundle member."""
    if spec.source == "apt-repository":
        return _resolve_from_apt(spec)
    return _resolve_from_github(spec, token=token)


def _resolve_from_apt(spec: DebSpec) -> Resolved:
    """Newest official deb for the tweak, from its own apt repository index.

    The digest returned here is the SHA-256 the developer published in their
    own ``Packages`` index, which is what the build is recorded and verified
    against — not a hash of whatever bytes happened to arrive.
    """
    try:
        deb = resolve_apt_deb(
            package=spec.package,
            index_url=spec.index_url,
            repo=spec.repo,
            suite=spec.suite,
            component=spec.component,
            architectures=spec.arch_preference,
            pin_version=spec.pin_version,
        )
    except AptIndexError as error:
        raise FactoryError(f"{spec.label}: {error}") from error
    return Resolved(
        version=deb.version,
        url=deb.url,
        name=deb.name,
        tag=deb.version,
        architecture=deb.architecture,
        sha256=deb.sha256,
        depends=deb.tweak_requires,
        package_id=deb.package,
    )


def _resolve_from_github(spec: DebSpec, *, token: str | None) -> Resolved:
    """Newest official .deb for the tweak from its own GitHub releases."""
    releases = _get_json(RELEASES_URL.format(repo=spec.repo), token=token)
    _require(isinstance(releases, list), f"{spec.repo}: unexpected releases payload")
    candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if spec.pin_version and _bare_version(str(release.get("tag_name", ""))) != _bare_version(spec.pin_version):
            continue
        asset = _pick_asset(release, spec.asset_regex or re.compile("(?!)"), spec.arch_preference)
        if asset is not None:
            candidates.append((release, asset, str(release.get("tag_name", ""))))
    pattern = "the pinned version" if spec.pin_version else "the registered pattern"
    _require(candidates, f"{spec.repo}: no release asset matches {pattern}")
    # Pairwise version compare (cmp_to_key): tags are v-prefixed and not
    # always strict semver, so a key function against a constant would lie.
    candidates.sort(
        key=cmp_to_key(
            lambda left, right: compare_versions(_bare_version(left[2]) or "0", _bare_version(right[2]) or "0")
        ),
        reverse=True,
    )
    release, asset, tag = candidates[0]
    url = str(asset.get("browser_download_url", ""))
    _require(URL_RE.match(url) is not None, f"{spec.repo}: asset download URL is not https")
    asset_name = str(asset.get("name", ""))
    arch = re.search(r"[-._]iPhoneOS[-._](arm64e|arm64|armv7|arm)\.", asset_name, re.IGNORECASE)
    return Resolved(
        version=str(release.get("tag_name", "")) or tag,
        url=url,
        name=asset_name,
        tag=tag,
        architecture=f"iphoneos-{arch.group(1).lower()}" if arch else "",
        package_id=spec.package_id,
    )


def resolve_base(name: str, cfg: dict[str, Any], *, token: str | None) -> Resolved:
    """Resolve the operator-supplied decrypted base app for an injection."""
    source = cfg.get("source")
    if source == "release":
        try:
            releases = _get_json(RELEASES_URL.format(repo=cfg["repo"]), token=token)
        except FactoryError as error:
            if "404" in str(error):
                raise FactoryError(
                    f"base {name}: repo {cfg['repo']} is not reachable (404) — create it and publish your "
                    "decrypted dump as a release (see docs/TWEAK-FACTORY.md)"
                ) from error
            raise
        prefix = str(cfg["tagPrefix"])
        glob = str(cfg["assetGlob"])
        for release in releases or []:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            tag = str(release.get("tag_name", ""))
            if not tag.startswith(prefix):
                continue
            for asset in release.get("assets") or []:
                if fnmatch.fnmatch(str(asset.get("name", "")), glob):
                    url = str(asset.get("browser_download_url", ""))
                    _require(URL_RE.match(url) is not None, f"base {name}: asset URL is not https")
                    return Resolved(version=tag, url=url, name=str(asset.get("name", "")), tag=tag)
        raise FactoryError(
            f"base {name}: no release tag starting with {prefix!r} carries a {glob!r} asset in {cfg['repo']} "
            "(create one — see docs/TWEAK-FACTORY.md)"
        )
    if source == "url":
        return Resolved(version="static", url=str(cfg["url"]))
    # source == "variable": repository variables are readable through the API.
    repo = repository_slug()
    var_name = str(cfg["envVar"])
    payload = _get_json(VARIABLE_URL.format(repo=repo, name=var_name), token=token)
    value = str(payload.get("value", "")) if isinstance(payload, dict) else ""
    _require(URL_RE.match(value) is not None, f"base {name}: variable {var_name} is unset or not an https URL")
    return Resolved(version="variable", url=value)


def release_exists(repo: str, tag: str, *, token: str | None) -> bool:
    try:
        _get_json(RELEASE_TAG_URL.format(repo=repo, tag=urllib.parse.quote(tag, safe="")), token=token)
    except FactoryError:
        return False
    return True


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def bundle_manifest(pairs: list[tuple[DebSpec, Resolved]]) -> str:
    """A single digest over the exact set of tweaks a bundle injects.

    Sorting by label keeps it stable across registry edits; including the URL
    makes a repository or asset swap (not just a version bump) trigger a
    rebuild, because the bytes a client would download changed.
    """
    lines = sorted(BUNDLE_SEP.join([spec.label, resolved.version, resolved.url]) for spec, resolved in pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def manifest_from_members(members: list[dict[str, Any]]) -> str:
    """Recompute :func:`bundle_manifest` from a published member record."""
    lines = sorted(
        BUNDLE_SEP.join([str(m.get("label", "")), str(m.get("version", "")), str(m.get("url", ""))]) for m in members
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _short_version(value: str) -> str:
    """``youtube-21.36.6`` -> ``21.36.6``; anything unparseable is sanitized."""
    numbers = re.findall(r"\d+(?:\.\d+)*", str(value or ""))
    return numbers[-1] if numbers else (re.sub(r"[^A-Za-z0-9._+-]", "", str(value or "")) or "base")


def bundle_version(base: Resolved, manifest: str) -> str:
    """The version of a bundle build: the base app plus the members' digest.

    A bundle has no version of its own upstream, so its identity is its inputs.
    Bumping any tweak moves the suffix and the release tag; a quiet week keeps
    both and the plan skips the build.
    """
    return f"{_short_version(base.version)}-{manifest[:8]}"


def conflict_violations(package_ids: list[str], groups: tuple[ConflictGroup, ...]) -> list[str]:
    """Every conflict group with two or more members present in this bundle."""
    problems: list[str] = []
    for group in groups:
        hits = sorted({pid for pid in package_ids if pid in group.packages})
        if len(hits) > 1:
            problems.append(f"{group.name}: {' + '.join(hits)} — {group.reason}")
    return problems


def dependency_warnings(pairs: list[tuple[DebSpec, Resolved]]) -> list[str]:
    """Tweak-level ``Depends`` an apt index states that the bundle does not inject.

    Injection has no dependency solver (a ``.deb`` is unpacked into the app and
    that is the end of it), so a missing library tweak is reported instead of
    being silently skipped — that is the difference between "installs" and
    "works".
    """
    present: set[str] = set()
    for spec, resolved in pairs:
        for identity in (resolved.package_id, spec.package_id, spec.package):
            if identity:
                present.add(identity)
    warnings: list[str] = []
    for spec, resolved in pairs:
        for dependency in resolved.depends:
            # An alternatives group ("a | b") is satisfied by any one of its members.
            if any(name in present for name in dependency.split("|")):
                continue
            warnings.append(f"{spec.label} depends on {dependency}, which this bundle does not inject")
    return sorted(set(warnings))


def slice_warnings(pairs: list[tuple[DebSpec, Resolved]]) -> list[str]:
    """Flag a bundle whose members are packaged for different arch fields.

    ``Architecture`` is the repository's claim, not the binary's slice list, so
    this is advisory rather than a hard stop: a mixed bundle *usually* still
    loads, and it is exactly the case where "it installed" and "it works"
    diverge.  Reading it once at plan time beats debugging one crash report per
    user.
    """
    slices = {resolved.architecture for _, resolved in pairs if resolved.architecture}
    if len(slices) < 2:
        return []
    listing = ", ".join(f"{spec.label}={resolved.architecture or 'unknown'}" for spec, resolved in pairs)
    return [f"members declare different Architecture values ({listing}); confirm the dylibs match the base app"]


def build_tag(build: Build, version: str) -> str:
    _require(VERSION_RE.match(version) is not None, f"build {build.slug}: version {version!r} has invalid characters")
    return f"{build.tag_prefix}/v{_bare_version(version)}"


def asset_name_for(build: Build, version: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "", build.name.replace(" ", ""))
    bare = re.sub(r"[^A-Za-z0-9._-]", "", _bare_version(version))
    return f"{stem}-{bare}.ipa"


def plan_builds(
    registry: Registry,
    state: dict[str, Any],
    *,
    token: str | None,
    force: bool = False,
) -> dict[str, Any]:
    """Resolve every build into include / up-to-date / skipped buckets."""
    try:
        policy = load_policy(ROOT)
    except Exception:  # an unusable policy must not stop planning; decide() then allows everything
        policy = None
    include: list[dict[str, Any]] = []
    uptodate: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    disabled: list[dict[str, str]] = []
    warned: list[dict[str, Any]] = []
    target = repository_slug()
    for build in registry.builds:
        if not build.enabled:
            disabled.append({"slug": build.slug, "reason": "disabled in the registry"})
            continue
        try:
            if build.is_bundle:
                pairs = [(spec, resolve_member(build, spec, token=token)) for spec in build.members]
            else:
                pairs = [(build.primary, resolve_deb(build, token=token))]
            for spec, resolved in pairs:
                verdict = decide(resolved.url, policy=policy)
                _require(
                    not verdict.blocked,
                    f"{spec.label} deb URL blocked by sourcing policy ({verdict.rule_id}): {resolved.url}",
                )
            base_cfg = registry.base_apps[build.base_app]
            base = resolve_base(build.base_app, base_cfg, token=token)
            if base_cfg.get("source") != "variable":
                verdict = decide(base.url, policy=policy)
                _require(not verdict.blocked, f"base URL blocked by sourcing policy ({verdict.rule_id}): {base.url}")
            manifest = bundle_manifest(pairs)
            version = bundle_version(base, manifest) if build.is_bundle else pairs[0][1].version
            # Two tweaks that hook the same host-app feature are skipped rather
            # than built: the result would install and then misbehave, and the
            # registry (not a run) is where that decision belongs.
            identities = [resolved.package_id or spec.package_id for spec, resolved in pairs]
            problems = conflict_violations([pid for pid in identities if pid], registry.conflict_groups)
            _require(not problems, f"bundle {build.slug} mixes conflicting tweaks -> " + "; ".join(problems))
            notes = dependency_warnings(pairs) + slice_warnings(pairs)
            tag = build_tag(build, version)
            record = (state.get("builds") or {}).get(build.slug) or {}
            if build.is_bundle:
                same = (
                    record.get("tweakVersion") == version
                    and record.get("debSha256") == manifest
                    and record.get("baseVersion") == base.version
                    and record.get("baseURL") == base.url
                )
            else:
                same = (
                    record.get("tweakVersion") == version
                    and record.get("debURL") == pairs[0][1].url
                    and record.get("baseVersion") == base.version
                    and record.get("baseURL") == base.url
                )
            if not force and same and release_exists(target, tag, token=token):
                uptodate.append({"slug": build.slug, "reason": f"{version} already published as {tag}"})
                continue
            entries = [
                {
                    "label": spec.label,
                    "name": resolved.name,
                    "version": resolved.version,
                    "url": resolved.url,
                    "sha256": resolved.sha256,
                }
                for spec, resolved in pairs
            ]
            if notes:
                warned.append({"slug": build.slug, "reason": "; ".join(notes)})
            include.append(
                {
                    "slug": build.slug,
                    "name": build.name,
                    "catalog_app": build.catalog_app,
                    "deb_url": pairs[0][1].url,
                    "deb_version": version,
                    "deb_name": pairs[0][1].name,
                    "deb_entries": json.dumps(entries, sort_keys=True),
                    "bundle_sha256": manifest,
                    "is_bundle": "true" if build.is_bundle else "false",
                    "member_count": str(len(pairs)),
                    "base_app": build.base_app,
                    "base_url": base.url,
                    "base_version": base.version,
                    "bundle_id": build.bundle_id,
                    "app_name": build.app_name,
                    "tag": tag,
                    "asset_name": asset_name_for(build, version),
                    "prerelease": "true" if build.prerelease else "false",
                }
            )
        except FactoryError as error:
            skipped.append({"slug": build.slug, "reason": str(error)})
    return {"include": include, "uptodate": uptodate, "skipped": skipped, "disabled": disabled, "warnings": warned}


def summary_markdown(result: dict[str, Any]) -> str:
    lines = ["## Tweak Factory plan", ""]
    if result["include"]:
        lines.append("| Build | tweaks | version | base | release tag |")
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in result["include"]:
            members = "bundle x" + entry.get("member_count", "1") if entry.get("is_bundle") == "true" else "1"
            row = (
                f"| {entry['slug']} | {members} | {entry['deb_version']} "
                f"| {entry['base_app']} {entry['base_version']} | `{entry['tag']}` |"
            )
            lines.append(row)
    else:
        lines.append("No builds to run.")
    sections = (
        ("Up to date", result["uptodate"]),
        ("Skipped", result["skipped"]),
        ("Disabled", result["disabled"]),
        ("Warnings", result.get("warnings") or []),
    )
    for title, rows in sections:
        if rows:
            lines += ["", f"### {title}", ""]
            lines += [f"- **{row['slug']}** — {row['reason']}" for row in rows]
    if result["skipped"]:
        hint = "_Skipped builds need no runner; configure the reason above (docs/TWEAK-FACTORY.md) to enable them._"
        lines += ["", hint]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Publish / merge / notes / status
# ---------------------------------------------------------------------------


def _registry_build(slug: str) -> Build:
    registry = load_registry()
    candidates = [build for build in registry.builds if build.slug == slug]
    _require(bool(candidates), f"slug {slug} is not in the registry")
    return candidates[0]


def _load_members_json(value: str) -> list[dict[str, Any]]:
    """Read a bundle's member records from an inline JSON array or a file."""
    text = str(value or "").strip()
    if not text:
        return []
    try:
        payload = (
            json.loads(text) if text.startswith(("[", "{")) else json.loads(Path(text).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"members json is unreadable: {error}") from error
    _require(isinstance(payload, list) and bool(payload), "members json must be a non-empty array")
    records: list[dict[str, Any]] = []
    for item in payload:
        _require(isinstance(item, dict), "every member record must be an object")
        entry = {
            "label": str(item.get("label", "")),
            "name": str(item.get("name", "")),
            "version": str(item.get("version", "")),
            "url": str(item.get("url", "")),
            "sha256": str(item.get("sha256", "")),
        }
        _require(LABEL_RE.match(entry["label"]) is not None, f"member label {entry['label']!r} is not a label")
        _require(VERSION_RE.match(entry["version"]) is not None, f"member {entry['label']}: version is invalid")
        _require(URL_RE.match(entry["url"]) is not None, f"member {entry['label']}: url must be https")
        _require(
            not entry["sha256"] or SHA_RE.match(entry["sha256"]) is not None,
            f"member {entry['label']}: sha256 must be 64 hex characters",
        )
        records.append(entry)
    return records


def _validated_publish_args(args: argparse.Namespace, build: Build | None = None) -> list[dict[str, Any]]:
    """Validate a finished build and return its member records (empty for a single tweak)."""
    _require(SLUG_RE.match(args.slug) is not None, "slug has invalid characters")
    _require(TAG_RE.match(args.tag) is not None, "tag has invalid characters")
    _require(VERSION_RE.match(args.deb_version) is not None, "deb version has invalid characters")
    _require(VERSION_RE.match(args.base_version) is not None, "base version has invalid characters")
    _require(URL_RE.match(args.base_url) is not None, "base url must be https")
    _require(Path(args.ipa).is_file(), f"{args.ipa} is not a file")
    _require(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ipa$", args.asset_name) is not None, "asset name is invalid")
    members = _load_members_json(getattr(args, "members_json", ""))
    bundle = build.is_bundle if build is not None else bool(members)
    if bundle:
        # A bundle has one input set, not one deb: the recorded members must be
        # the registry's members, and their digest must be what the tag says.
        _require(members, "a bundle build must record every injected tweak with --members-json")
        _require(not args.deb_url and not args.deb_sha256, "a bundle records members, not a single deb url/sha256")
        if build is not None:
            _require(
                len(members) == len(build.members),
                f"bundle {build.slug} injects {len(build.members)} tweaks but recorded {len(members)}",
            )
            expected = bundle_version(
                Resolved(version=args.base_version, url=args.base_url), manifest_from_members(members)
            )
            _require(
                args.deb_version == expected,
                f"version {args.deb_version} does not match the recorded members (expected {expected})",
            )
        return members
    _require(not members, "--members-json is only valid for bundle builds")
    _require(URL_RE.match(args.deb_url) is not None, "deb url must be https")
    _require(SHA_RE.match(args.deb_sha256) is not None, "deb sha256 must be 64 hex characters")
    return members


def publish_fragment(args: argparse.Namespace) -> dict[str, Any]:
    """Validate a finished build and emit its state fragment (single slug)."""
    build = _registry_build(args.slug)
    members = _validated_publish_args(args, build)
    expected = build_tag(build, args.deb_version)
    _require(args.tag == expected, f"tag {args.tag} does not match {expected}")
    ipa = Path(args.ipa)
    payload = ipa.read_bytes()
    record: dict[str, Any] = {
        "tweakVersion": args.deb_version,
        "baseApp": args.base_app,
        "baseVersion": args.base_version,
        "baseURL": args.base_url,
        "tag": args.tag,
        "assetName": args.asset_name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "builtAt": today(),
        "runURL": args.run_url,
    }
    if members:
        manifest = manifest_from_members(members)
        record["bundle"] = True
        record["debSha256"] = manifest
        record["members"] = members
    else:
        record["debURL"] = args.deb_url
        record["debSha256"] = args.deb_sha256
    fragment = {"version": 1, "builds": {build.slug: record}}
    if args.fragment:
        atomic_write_text(Path(args.fragment), json.dumps(fragment, indent=2, sort_keys=True) + "\n")
    return fragment


def merge_fragments(fragments: list[Path], out: Path) -> bool:
    """Merge per-build fragments into the state file deterministically."""
    builds: dict[str, Any] = {}
    for path in fragments:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FactoryError(f"fragment {path} is unreadable: {error}") from error
        for slug, record in (payload.get("builds") or {}).items():
            _require(SLUG_RE.match(str(slug)) is not None, f"fragment {path}: invalid slug {slug!r}")
            existing = builds.get(slug)
            if existing is None or str(record.get("builtAt", "")) >= str(existing.get("builtAt", "")):
                builds[slug] = record
    if not builds:
        print("tweak-factory: no fragments to merge; state unchanged")
        return False
    document = {"version": 1, "generatedAt": today(), "builds": dict(sorted(builds.items()))}
    atomic_write_text(out, json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"tweak-factory: wrote {out} ({len(builds)} build(s))")
    return True


def release_notes(args: argparse.Namespace) -> str:
    """Provenance-first release notes for one build."""
    build = _registry_build(args.slug)
    members = _validated_publish_args(args, build)
    ipa = Path(args.ipa)
    sha = hashlib.sha256(ipa.read_bytes()).hexdigest()
    rows = [("Bundle" if members else "Tweak", f"{build.name} {args.deb_version}")]
    if members:
        for member in members:
            rows.append(("Tweak", f"{member['label']} {member['version']}"))
            rows.append(("", f"[{member['name'] or 'download'}]({member['url']})"))
            if member["sha256"]:
                rows.append(("", f"SHA-256 `{member['sha256']}`"))
        rows.append(("Bundle SHA-256", f"`{manifest_from_members(members)}`"))
    else:
        rows.append(("Tweak .deb", f"[{args.deb_name or 'download'}]({args.deb_url})"))
        rows.append(("deb SHA-256", f"`{args.deb_sha256}`"))
    rows += [
        ("Base app", f"{args.base_app} {args.base_version}"),
        ("Base app source", f"[download]({args.base_url})"),
        ("IPA SHA-256", f"`{sha}`"),
        ("IPA size", f"{ipa.stat().st_size} bytes"),
        ("Injection", "Cyan (`.deb` → `.ipa`), Build & Inject Tweak workflow"),
        ("Run", f"[#{args.run_url.rsplit('/', 1)[-1]}]({args.run_url})" if args.run_url else "n/a"),
    ]
    lines = [f"## {build.name} {args.deb_version} (injected)", "", "| Field | Value |", "| --- | --- |"]
    lines += [f"| {key} | {value} |" for key, value in rows]
    lines += [
        "",
        "The tweak `.deb` was collected from the tweak's **official upstream release or apt repository**; the",
        "base app was supplied by the repository operator and is **not** redistributed beyond this injection.",
        "Sideload with SideStore, AltStore, Feather, LiveContainer or E-Sign. Verify the digests above first.",
    ]
    return "\n".join(lines) + "\n"


def status(state_path: Path = STATE_PATH) -> str:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"state {state_path} is unreadable: {error}") from error
    lines = [f"builds recorded in {state_path.name} (generatedAt {state.get('generatedAt')}):", ""]
    for slug, record in sorted((state.get("builds") or {}).items()):
        members = record.get("members") or []
        size = f" ({len(members)} tweaks)" if members else ""
        line = (
            f"  {slug:<16} tweak {record.get('tweakVersion')!s:<12} "
            f"base {record.get('baseVersion')!s:<20} tag {record.get('tag')}{size}"
        )
        lines.append(line)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tweak_factory", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="resolve builds into the GitHub Actions matrix")
    plan.add_argument("--matrix-out", type=Path, default=None, help="write the matrix JSON here")
    plan.add_argument("--force", action="store_true", help="rebuild even when the release already exists")

    publish = sub.add_parser("publish", help="validate one finished build and write its state fragment")
    for flag in _BUILD_ARG_FLAGS:
        publish.add_argument(f"--{flag}", required=True)

    # Only meaningful for a single-tweak build; a bundle passes --members-json.
    publish.add_argument("--deb-url", default="")
    publish.add_argument("--deb-sha256", default="")
    publish.add_argument("--members-json", default="")
    publish.add_argument("--deb-name", default="")
    publish.add_argument("--ipa", required=True)
    publish.add_argument("--fragment", required=True)
    publish.add_argument("--run-url", default="")

    merge = sub.add_parser("merge", help="merge state fragments into the committed state file")
    merge.add_argument("fragments", nargs="+", type=Path)
    merge.add_argument("--out", type=Path, default=STATE_PATH)

    notes = sub.add_parser("notes", help="render release notes for one build (stdout)")
    for flag in _BUILD_ARG_FLAGS:
        notes.add_argument(f"--{flag}", required=True)
    notes.add_argument("--deb-url", default="")
    notes.add_argument("--deb-sha256", default="")
    notes.add_argument("--members-json", default="")
    notes.add_argument("--deb-name", default="")
    notes.add_argument("--ipa", required=True)
    notes.add_argument("--run-url", default="")

    sub.add_parser("status", help="show the committed build state")
    sub.add_parser("validate", help="validate the registry only")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            registry = load_registry()
            state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
            result = plan_builds(registry, state if isinstance(state, dict) else {}, token=_token(), force=args.force)
            matrix = json.dumps({"include": result["include"]}, sort_keys=True)
            if args.matrix_out is not None:
                atomic_write_text(args.matrix_out, matrix + "\n")
            sys.stdout.write(summary_markdown(result))
            if args.matrix_out is None:
                print(matrix)
        elif args.command == "publish":
            publish_fragment(args)
            print(f"tweak-factory: fragment written for {args.slug}")
        elif args.command == "merge":
            merge_fragments(list(args.fragments), args.out)
        elif args.command == "notes":
            sys.stdout.write(release_notes(args))
        elif args.command == "status":
            sys.stdout.write(status())
        else:
            load_registry()
            print("tweak-factory: registry OK")
    except FactoryError as error:
        prefix = "::error::tweak-factory: " if os.environ.get("GITHUB_ACTIONS") else "tweak-factory: "
        print(prefix + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
