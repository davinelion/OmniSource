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

Everything is idempotent: a build whose deb *and* base are unchanged and whose
release already exists is skipped, so the weekly schedule costs nothing when
nothing moved, and an empty matrix claims no macOS runner at all.

Commands:
    plan      resolve newest deb+base per build, print the matrix + a summary
    publish   validate one finished build and write a state fragment
    merge     merge state fragments into data/tweak-builds-state.json
    notes     render the release notes for a build (stdout)
    build     operator flow: base IPA (local or link) + tweak pick + custom
              options, then optional base publish / run dispatch / registry
              persistence (docs/TWEAK-FACTORY.md)
    status    show the committed state
    validate  check the registry only

Examples:
    python3 scripts/tweak_factory.py plan --matrix-out /tmp/matrix.json
    python3 scripts/tweak_factory.py publish --slug ytlite --tag tweak-build/ytlite/v5.2.2 ...
    python3 scripts/tweak_factory.py merge fragments/*.json --out data/tweak-builds-state.json
    python3 scripts/tweak_factory.py build --base ~/Downloads/YouTube.ipa --tweaks youmod,ytlite
    python3 scripts/tweak_factory.py build --base https://…/YouTube.ipa --tweaks youmod \\
        --custom-deb Gonerino=https://…/Gonerino.deb --app-name uProVid --save-registry --run
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
_BUILD_ARG_FLAGS = (
    "slug",
    "tag",
    "deb-url",
    "deb-version",
    "deb-sha256",
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


@dataclass(frozen=True)
class TweakSpec:
    """One selectable tweak in the catalog of known tweaks (``tweaks``).

    ``repo``/``asset_regex`` resolve the newest official .deb from GitHub
    releases; a spec with neither is an error (custom tweaks enter a build
    through ``extraDebs`` URL entries, not through the catalog).
    """

    id: str
    name: str
    repo: str = ""
    asset_regex: re.Pattern[str] | None = None
    arch_preference: tuple[str, ...] = ()
    description: str = ""


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
    extra_debs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class Registry:
    base_apps: dict[str, dict[str, Any]]
    builds: tuple[Build, ...]
    tweaks: tuple[TweakSpec, ...] = ()

    def tweak(self, tweak_id: str) -> TweakSpec:
        for spec in self.tweaks:
            if spec.id == tweak_id:
                return spec
        raise FactoryError(
            f"tweak id {tweak_id!r} is not in the registry tweaks catalog "
            f"(known: {', '.join(s.id for s in self.tweaks) or 'none'})"
        )


# ---------------------------------------------------------------------------
# Registry loading / validation
# ---------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FactoryError(message)


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

    tweaks_raw = raw.get("tweaks") or []
    _require(isinstance(tweaks_raw, list), "registry tweaks must be an array")
    tweaks: list[TweakSpec] = []
    seen_tweaks: set[str] = set()
    for item in tweaks_raw:
        _require(isinstance(item, dict), "every tweak must be an object")
        tweak_id = str(item.get("id", ""))
        _require(SLUG_RE.match(tweak_id) is not None, f"tweak id {tweak_id!r} is not a slug")
        _require(tweak_id not in seen_tweaks, f"tweak id {tweak_id!r} is registered twice")
        seen_tweaks.add(tweak_id)
        name = str(item.get("name", ""))
        _require(NAME_RE.match(name) is not None, f"tweak {tweak_id}: name must be printable (got {name!r})")
        repo = str(item.get("repo", ""))
        _require(_is_repo(repo), f"tweak {tweak_id}: repo must be owner/name")
        try:
            asset_regex = re.compile(str(item.get("assetRegex", "")))
        except re.error as error:
            raise FactoryError(f"tweak {tweak_id}: assetRegex does not compile: {error}") from error
        arches = tuple(item.get("archPreference") or ())
        _require(all(arch in ARCHES for arch in arches), f"tweak {tweak_id}: archPreference may only use {ARCHES}")
        tweaks.append(
            TweakSpec(
                id=tweak_id,
                name=name,
                repo=repo,
                asset_regex=asset_regex,
                arch_preference=arches or ARCHES,
                description=str(item.get("description") or ""),
            )
        )

    builds_raw = raw.get("builds")
    _require(isinstance(builds_raw, list), "registry builds must be an array")
    builds: list[Build] = []
    seen: set[str] = set()
    for item in builds_raw:
        _require(isinstance(item, dict), "every build must be an object")
        slug = str(item.get("slug", ""))
        _require(SLUG_RE.match(slug) is not None, f"build slug {slug!r} is not a slug")
        _require(slug not in seen, f"build slug {slug!r} is registered twice")
        seen.add(slug)
        name = str(item.get("name", ""))
        _require(NAME_RE.match(name) is not None, f"build {slug}: name must be printable (got {name!r})")
        deb = item.get("deb")
        _require(isinstance(deb, dict), f"build {slug}: deb must be an object")
        _require(deb.get("source") == "github-release", f"build {slug}: only deb source github-release is supported")
        _require(_is_repo(deb.get("repo")), f"build {slug}: deb.repo must be owner/name")
        try:
            deb_regex = re.compile(str(deb.get("assetRegex", "")))
        except re.error as error:
            raise FactoryError(f"build {slug}: deb.assetRegex does not compile: {error}") from error
        arches = tuple(deb.get("archPreference") or ())
        _require(all(arch in ARCHES for arch in arches), f"build {slug}: archPreference may only use {ARCHES}")
        base_app = str(item.get("base", ""))
        _require(base_app in base_apps, f"build {slug}: base {base_app!r} is not defined in baseApps")
        publish = item.get("publish") or {}
        tag_prefix = str(publish.get("tagPrefix") or f"tweak-build/{slug}")
        _require(TAG_RE.match(tag_prefix) is not None, f"build {slug}: publish.tagPrefix has invalid characters")
        extra_debs = _parse_extra_debs(slug, item.get("extraDebs") or (), tweaks)
        builds.append(
            Build(
                slug=slug,
                name=name,
                enabled=bool(item.get("enabled", True)),
                catalog_app=str(item.get("catalogApp") or slug),
                deb_repo=str(deb["repo"]),
                deb_regex=deb_regex,
                arch_preference=arches or ARCHES,
                base_app=base_app,
                bundle_id=str(item.get("bundleId") or ""),
                app_name=str(item.get("appName") or ""),
                tag_prefix=tag_prefix,
                prerelease=bool(publish.get("prerelease", True)),
                extra_debs=extra_debs,
            )
        )
    return Registry(base_apps=dict(base_apps), builds=tuple(builds), tweaks=tuple(tweaks))


def _parse_extra_debs(slug: str, raw: Any, tweaks: list[TweakSpec]) -> tuple[dict[str, str], ...]:
    """Parse ``builds[].extraDebs``: ``"id"`` refs or ``{"name", "url"}`` customs.

    Entries are normalised to flat dicts so state fragments and the workflow
    matrix can carry them without nested structures. A ref form may also carry
    an optional ``version`` pin (checked against the resolved version).
    """
    _require(isinstance(raw, (list, tuple)), f"build {slug}: extraDebs must be an array")
    out: list[dict[str, str]] = []
    for index, entry in enumerate(raw):
        if isinstance(entry, str):
            entry = {"id": entry}
        _require(isinstance(entry, dict), f"build {slug}: extraDebs[{index}] must be an id or an object")
        keys = set(entry)
        if keys <= {"id", "version"}:
            tweak_id = str(entry.get("id", ""))
            spec = next((s for s in tweaks if s.id == tweak_id), None)
            _require(spec is not None, f"build {slug}: extraDebs[{index}] id {tweak_id!r} is not in tweaks")
            out.append({"id": tweak_id, "name": spec.name, "version": str(entry.get("version") or "")})
        elif {"name", "url"} <= keys:
            custom_name = str(entry.get("name", ""))
            custom_url = str(entry.get("url", ""))
            _require(NAME_RE.match(custom_name) is not None, f"build {slug}: extraDebs[{index}] name is invalid")
            _require(
                URL_RE.match(custom_url) is not None,
                f"build {slug}: extraDebs[{index}] url must be an https URL",
            )
            out.append(
                {"id": "", "name": custom_name, "url": custom_url, "version": str(entry.get("version") or "custom")}
            )
        else:
            _require(False, f"build {slug}: extraDebs[{index}] must be an id string or an object with name+url")
    return tuple(out)


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
    """Newest official .deb for the build's primary tweak."""
    return resolve_tweak(
        TweakSpec(
            id=build.slug,
            name=build.name,
            repo=build.deb_repo,
            asset_regex=build.deb_regex,
            arch_preference=build.arch_preference,
        ),
        token=token,
    )


def resolve_tweak(spec: TweakSpec, *, token: str | None) -> Resolved:
    """Newest official .deb for a catalog tweak from its own GitHub releases."""
    releases = _get_json(RELEASES_URL.format(repo=spec.repo), token=token)
    _require(isinstance(releases, list), f"{spec.repo}: unexpected releases payload")
    candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        asset = _pick_asset(release, spec.asset_regex, spec.arch_preference) if spec.asset_regex else None
        if asset is not None:
            candidates.append((release, asset, str(release.get("tag_name", ""))))
    _require(candidates, f"{spec.repo}: no release asset matches the registered pattern")
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
    return Resolved(version=str(release.get("tag_name", "")) or tag, url=url, name=str(asset.get("name", "")), tag=tag)


def resolve_extra_deb(registry: Registry, entry: dict[str, str], *, token: str | None) -> Resolved:
    """Resolve one ``extraDebs`` entry: a catalog id or an operator-supplied URL.

    Custom URL entries are operator-controlled (the operator is the one who
    typed them), but they still pass the sourcing policy at plan time exactly
    like every other URL.
    """
    if entry.get("id"):
        resolved = resolve_tweak(registry.tweak(entry["id"]), token=token)
        pin = entry.get("version") or ""
        if pin and _bare_version(resolved.version) != _bare_version(pin):
            raise FactoryError(
                f"tweak {entry['id']}: pinned version {pin} does not match the newest release "
                f"{resolved.version} (remove the pin or publish it)"
            )
        return resolved
    url = str(entry["url"])
    name = str(Path(url.split("?", 1)[0]).split("/")[-1] or entry.get("name", "custom.deb"))
    return Resolved(version=entry.get("version") or "custom", url=url, name=name)


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


def build_tag(build: Build, version: str) -> str:
    _require(VERSION_RE.match(version) is not None, f"build {build.slug}: version {version!r} has invalid characters")
    return f"{build.tag_prefix}/v{_bare_version(version)}"


def asset_name_for(build: Build, version: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "", build.name.replace(" ", ""))
    bare = re.sub(r"[^A-Za-z0-9._-]", "", _bare_version(version))
    return f"{stem}-{bare}.ipa"


def _extras_key(pairs: Any) -> tuple[tuple[str, str, str], ...]:
    """Order-sensitive (name, version, url) identity of a build's extra tweaks.

    The state file stores the same triples plus digests, the plan only knows
    the triples - comparing the projection keeps both sides comparable while
    any change (add, drop, swap, re-pin, new URL) still forces a rebuild.
    """
    if not isinstance(pairs, list):
        return ()
    return tuple(
        (str(item.get("name", "")), str(item.get("version", "")), str(item.get("url", "")))
        for item in pairs
        if isinstance(item, dict)
    )


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
    skipped: list[dict[str, str]] = []
    disabled: list[dict[str, str]] = []
    target = repository_slug()
    for build in registry.builds:
        if not build.enabled:
            disabled.append({"slug": build.slug, "reason": "disabled in the registry"})
            continue
        try:
            deb = resolve_deb(build, token=token)
            verdict = decide(deb.url, policy=policy)
            _require(not verdict.blocked, f"deb URL blocked by sourcing policy ({verdict.rule_id}): {deb.url}")
            extras = [resolve_extra_deb(registry, entry, token=token) for entry in build.extra_debs]
            for extra in extras:
                verdict = decide(extra.url, policy=policy)
                if verdict.blocked:
                    raise FactoryError(f"extra deb URL blocked by sourcing policy ({verdict.rule_id}): {extra.url}")
            base_cfg = registry.base_apps[build.base_app]
            base = resolve_base(build.base_app, base_cfg, token=token)
            if base_cfg.get("source") != "variable":
                verdict = decide(base.url, policy=policy)
                _require(not verdict.blocked, f"base URL blocked by sourcing policy ({verdict.rule_id}): {base.url}")
            tag = build_tag(build, deb.version)
            record = (state.get("builds") or {}).get(build.slug) or {}
            same = (
                record.get("tweakVersion") == deb.version
                and record.get("debURL") == deb.url
                and record.get("baseVersion") == base.version
                and record.get("baseURL") == base.url
                and _extras_key(record.get("extraDebs"))
                == _extras_key(
                    [
                        {"name": entry["name"], "version": extra.version, "url": extra.url}
                        for entry, extra in zip(build.extra_debs, extras, strict=True)
                    ]
                )
            )
            if not force and same and release_exists(target, tag, token=token):
                uptodate.append({"slug": build.slug, "reason": f"{deb.version} already published as {tag}"})
                continue
            include.append(
                {
                    "slug": build.slug,
                    "name": build.name,
                    "catalog_app": build.catalog_app,
                    "deb_url": deb.url,
                    "deb_version": deb.version,
                    "deb_name": deb.name,
                    "base_app": build.base_app,
                    "base_url": base.url,
                    "base_version": base.version,
                    "bundle_id": build.bundle_id,
                    "app_name": build.app_name,
                    "tag": tag,
                    "asset_name": asset_name_for(build, deb.version),
                    "prerelease": "true" if build.prerelease else "false",
                    "extra_deb_urls": "\n".join(extra.url for extra in extras),
                    "extra_deb_labels": "\n".join(
                        f"{entry['name']} {extra.version}"
                        for entry, extra in zip(build.extra_debs, extras, strict=True)
                    ),
                    "extra_debs_json": json.dumps(
                        [
                            {"name": entry["name"], "version": extra.version, "url": extra.url, "sha256": ""}
                            for entry, extra in zip(build.extra_debs, extras, strict=True)
                        ],
                        sort_keys=True,
                    ),
                }
            )
        except FactoryError as error:
            skipped.append({"slug": build.slug, "reason": str(error)})
    return {"include": include, "uptodate": uptodate, "skipped": skipped, "disabled": disabled}


def summary_markdown(result: dict[str, Any]) -> str:
    lines = ["## Tweak Factory plan", ""]
    if result["include"]:
        lines.append("| Build | deb | extra tweaks | base | release tag |")
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in result["include"]:
            labels = [line.split(" ", 1)[0] for line in str(entry.get("extra_deb_labels") or "").splitlines() if line]
            row = (
                f"| {entry['slug']} | {entry['deb_version']} "
                f"| {', '.join(labels) if labels else '-'} "
                f"| {entry['base_app']} {entry['base_version']} | `{entry['tag']}` |"
            )
            lines.append(row)
    else:
        lines.append("No builds to run.")
    sections = (
        ("Up to date", result["uptodate"]),
        ("Skipped", result["skipped"]),
        ("Disabled", result["disabled"]),
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


def _validated_publish_args(args: argparse.Namespace) -> None:
    _require(SLUG_RE.match(args.slug) is not None, "slug has invalid characters")
    _require(TAG_RE.match(args.tag) is not None, "tag has invalid characters")
    _require(VERSION_RE.match(args.deb_version) is not None, "deb version has invalid characters")
    _require(VERSION_RE.match(args.base_version) is not None, "base version has invalid characters")
    _require(URL_RE.match(args.deb_url) is not None, "deb url must be https")
    _require(URL_RE.match(args.base_url) is not None, "base url must be https")
    _require(SHA_RE.match(args.deb_sha256) is not None, "deb sha256 must be 64 hex characters")
    _require(Path(args.ipa).is_file(), f"{args.ipa} is not a file")
    _require(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ipa$", args.asset_name) is not None, "asset name is invalid")
    _parse_extra_debs_json(str(getattr(args, "extra_debs_json", "") or ""))


def _parse_extra_debs_json(raw: str) -> list[dict[str, str]]:
    """Validate the ``--extra-debs-json`` payload (CI fills in the digests)."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FactoryError(f"extra-debs-json is not valid JSON: {error}") from error
    _require(isinstance(payload, list), "extra-debs-json must be an array")
    out: list[dict[str, str]] = []
    for index, entry in enumerate(payload):
        _require(isinstance(entry, dict), f"extra-debs-json[{index}] must be an object")
        name = str(entry.get("name", ""))
        version = str(entry.get("version", ""))
        url = str(entry.get("url", ""))
        sha = str(entry.get("sha256", ""))
        _require(NAME_RE.match(name) is not None, f"extra-debs-json[{index}].name is invalid")
        _require(VERSION_RE.match(version) is not None, f"extra-debs-json[{index}].version is invalid")
        _require(URL_RE.match(url) is not None, f"extra-debs-json[{index}].url must be https")
        _require(SHA_RE.match(sha) is not None, f"extra-debs-json[{index}].sha256 must be 64 hex characters")
        out.append({"name": name, "version": version, "url": url, "sha256": sha})
    return out


def publish_fragment(args: argparse.Namespace) -> dict[str, Any]:
    """Validate a finished build and emit its state fragment (single slug)."""
    _validated_publish_args(args)
    registry = load_registry()
    candidates = [b for b in registry.builds if b.slug == args.slug]
    _require(bool(candidates), f"slug {args.slug} is not in the registry")
    build = candidates[0]
    expected = build_tag(build, args.deb_version)
    _require(args.tag == expected, f"tag {args.tag} does not match {expected}")
    ipa = Path(args.ipa)
    payload = ipa.read_bytes()
    record = {
        "tweakVersion": args.deb_version,
        "debURL": args.deb_url,
        "debSha256": args.deb_sha256,
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
    extras = _parse_extra_debs_json(str(getattr(args, "extra_debs_json", "") or ""))
    if extras:
        record["extraDebs"] = extras
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
    _validated_publish_args(args)
    registry = load_registry()
    candidates = [b for b in registry.builds if b.slug == args.slug]
    _require(bool(candidates), f"slug {args.slug} is not in the registry")
    build = candidates[0]
    ipa = Path(args.ipa)
    sha = hashlib.sha256(ipa.read_bytes()).hexdigest()
    rows = [
        ("Tweak", f"{build.name} {args.deb_version}"),
        ("Tweak .deb", f"[{args.deb_name or 'download'}]({args.deb_url})"),
        ("deb SHA-256", f"`{args.deb_sha256}`"),
    ]
    for extra in _parse_extra_debs_json(str(getattr(args, "extra_debs_json", "") or "")):
        rows.append(
            (
                f"Extra tweak: {extra['name']} {extra['version']}",
                f"[download]({extra['url']}) — SHA-256 `{extra['sha256']}`",
            )
        )
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
        "The tweak `.deb` was collected from the tweak's **official upstream release**; the base app was supplied",
        "by the repository operator and is **not** redistributed beyond this injection. Sideload with SideStore,",
        "AltStore, Feather, LiveContainer or E-Sign. Verify the digest above before installing.",
    ]
    return "\n".join(lines) + "\n"


def status(state_path: Path = STATE_PATH) -> str:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"state {state_path} is unreadable: {error}") from error
    lines = [f"builds recorded in {state_path.name} (generatedAt {state.get('generatedAt')}):", ""]
    for slug, record in sorted((state.get("builds") or {}).items()):
        line = (
            f"  {slug:<16} tweak {record.get('tweakVersion'):<12} "
            f"base {record.get('baseVersion'):<20} tag {record.get('tag')}"
        )
        lines.append(line)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Operator build helper (`build` subcommand)
#
# The human-facing side of the factory: supply a base IPA (a local .ipa or an
# https link), pick the tweaks to inject (official upstream releases from the
# registry's `tweaks` catalog, plus custom .deb URLs), set the custom options
# (app name, bundle ID, tag prefix), then optionally publish the local base to
# the operator's dumps repo and dispatch a Build & Inject Tweak run. It can
# also persist the selection into data/tweak-builds.json so the scheduled
# Tweak Factory keeps the build fresh (and publishes the catalog releases).
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def read_local_ipa(path: Path) -> dict[str, Any]:
    """Validate a local .ipa (a zip holding Payload/<App>.app/Info.plist)."""
    _require(path.is_file(), f"{path} is not a file")
    _require(path.suffix.lower() in (".ipa", ".zip"), f"{path.name}: expected a .ipa (zip) file")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise FactoryError(f"{path.name} is not a valid zip/IPA archive: {error}") from error
    with archive:
        plist_names = [name for name in archive.namelist() if re.match(r"^Payload/[^/]+\.app/Info\.plist$", name)]
        _require(plist_names, f"{path.name}: no Payload/<App>.app/Info.plist inside - not an iOS IPA")
        try:
            info = plistlib.loads(archive.read(plist_names[0]))
        except (OSError, ValueError) as error:
            raise FactoryError(f"{path.name}: cannot parse {plist_names[0]}: {error}") from error
        bundle_id = str(info.get("CFBundleIdentifier") or "")
        _require(bundle_id, f"{path.name}: Info.plist has no CFBundleIdentifier")
    return {
        "kind": "local",
        "path": str(path),
        "name": path.name,
        "bundle_id": bundle_id,
        "version": str(info.get("CFBundleShortVersionString") or ""),
        "build": str(info.get("CFBundleVersion") or ""),
        "display_name": str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or ""),
        "sha256": _sha256_file(path),
        "size": path.stat().st_size,
    }


def probe_remote_ipa(url: str, *, root: Path = ROOT) -> dict[str, Any]:
    """Policy-check and probe an https base-IPA URL (final URL, size)."""
    _require(URL_RE.match(url) is not None, "base URL must be an https URL")
    try:
        policy = load_policy(root)
    except Exception:  # an unusable policy must not stop an operator from planning
        policy = None
    verdict = decide(url, policy=policy)
    _require(not verdict.blocked, f"base URL blocked by sourcing policy ({verdict.rule_id}): {url}")
    headers = {"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    try:
        head = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(head, timeout=30) as response:
            size = _int_or_none(response.headers.get("Content-Length"))
            final_url = response.geturl()
    except urllib.error.HTTPError as error:
        if error.code in (405, 501):  # HEAD refused - a ranged GET still reports the size
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                content_range = response.headers.get("Content-Range") or ""
                size = _int_or_none(content_range.rsplit("/", 1)[-1]) if content_range else None
                final_url = response.geturl()
        else:
            raise FactoryError(f"base URL unreachable (HTTP {error.code}): {url}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FactoryError(f"base URL unreachable ({error}): {url}") from error
    name = Path(urllib.parse.urlsplit(final_url).path).name or "base.ipa"
    return {
        "kind": "url",
        "url": final_url,
        "name": name,
        "size": size,
        "sha256": "",
        "bundle_id": "",
        "version": "",
        "build": "",
        "display_name": "",
    }


def resolve_selection(registry: Registry, selection: dict[str, str], *, token: str | None) -> Resolved:
    """Resolve one selected tweak (official id or operator-supplied URL)."""
    if selection.get("id"):
        return resolve_tweak(registry.tweak(selection["id"]), token=token)
    url = selection["url"]
    name = Path(urllib.parse.urlsplit(url).path).name or "custom.deb"
    return Resolved(version="custom", url=url, name=name)


def selections_from_args(registry: Registry, tweaks_arg: str | None, customs: list[str]) -> list[dict[str, str]]:
    """Parse ``--tweaks`` (ids / all / none, comma-separated) + custom URLs."""
    selected: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_official(tweak_id: str) -> None:
        if tweak_id in seen:
            return
        spec = registry.tweak(tweak_id)
        seen.add(tweak_id)
        selected.append({"id": spec.id, "name": spec.name, "repo": spec.repo, "url": ""})

    for token in re.split(r"[,;]+", tweaks_arg or ""):
        token = token.strip()
        if not token:
            continue
        if token.lower() == "none":
            selected.clear()
            seen.clear()
        elif token.lower() == "all":
            for spec in registry.tweaks:
                add_official(spec.id)
        else:
            add_official(token)
    for custom in customs:
        name, _, url = custom.partition("=")
        name, url = name.strip(), url.strip()
        _require(bool(name), "--custom-deb must be NAME=URL")
        _require(URL_RE.match(url) is not None, f"custom tweak {name}: URL must be https")
        if any(item["name"].casefold() == name.casefold() for item in selected):
            continue
        selected.append({"id": "", "name": name, "repo": "", "url": url})
    return selected


def pick_tweaks_interactively(registry: Registry) -> list[dict[str, str]]:
    """TTY picker: numbered official tweaks plus 'c' for custom .deb URLs."""
    tweaks = list(registry.tweaks)
    if not tweaks:
        raise FactoryError(
            "the registry tweaks catalog is empty - add entries under 'tweaks' in data/tweak-builds.json"
        )
    print("\nAvailable tweaks (collected from their official upstream releases):")
    for index, spec in enumerate(tweaks, 1):
        note = f" - {spec.description}" if spec.description else ""
        print(f"  [{index}] {spec.name} ({spec.repo}){note}")
    print(
        "Pick tweaks: comma-separated numbers, 'all', 'none', 'c' for a custom .deb URL, "
        "Enter to finish, 'q' to quit.\n"
    )
    selected: list[dict[str, str]] = []
    while True:
        prompt = f"  {len(selected)} selected ({', '.join(item['name'] for item in selected) or 'none'}): "
        try:
            line = input(prompt).strip()
        except EOFError:
            break
        if line.lower() == "q":
            raise FactoryError("aborted")
        if not line:
            break
        if line.lower() == "c":
            name = input("  custom tweak name: ").strip()
            url = input("  custom .deb URL (https): ").strip()
            _require(NAME_RE.match(name) is not None, "custom tweak name is invalid")
            _require(URL_RE.match(url) is not None, "custom tweak URL must be https")
            if not any(item["name"].casefold() == name.casefold() for item in selected):
                selected.append({"id": "", "name": name, "repo": "", "url": url})
            continue
        if line.lower() == "none":
            selected = []
            continue
        if line.lower() == "all":
            selected = [{"id": spec.id, "name": spec.name, "repo": spec.repo, "url": ""} for spec in tweaks]
            continue
        for token in re.split(r"[,\s]+", line):
            if not token.isdigit():
                print(f"  ignoring {token!r} (numbers, 'all', 'none', 'c')")
                continue
            index = int(token) - 1
            if not 0 <= index < len(tweaks):
                print(f"  {index + 1} is out of range")
                continue
            spec = tweaks[index]
            if not any(item["id"] == spec.id for item in selected):
                selected.append({"id": spec.id, "name": spec.name, "repo": spec.repo, "url": ""})
    if not selected:
        raise FactoryError("no tweaks selected")
    return selected


def _gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise FactoryError("the `gh` CLI was not found on PATH (install it from https://cli.github.com)")
    result = subprocess.run([gh_bin, *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise FactoryError(f"gh {' '.join(args[:2])} failed: {detail[0] if detail else 'unknown error'}")
    return result


def publish_local_base(
    *,
    base_repo: str,
    base_cfg: dict[str, Any],
    base: dict[str, Any],
    title: str,
) -> tuple[str, str]:
    """Publish a local base IPA as a release in the operator's dumps repo.

    Mirrors the layout baseApps release resolution expects (tagPrefix +
    version, one .ipa asset). Returns (release tag, download URL).
    """
    if shutil.which("gh") is None:
        raise FactoryError("--publish-base needs the `gh` CLI on PATH, authenticated for the base repo")
    version = base.get("version") or "unknown"
    tag_prefix = str(base_cfg.get("tagPrefix") or "")
    tag = f"{tag_prefix}{version}" if tag_prefix else f"base-{version}"
    asset_name = re.sub(r"[^A-Za-z0-9._-]", "_", base["name"])
    if not asset_name.lower().endswith(".ipa"):
        asset_name += ".ipa"
    work = Path(f".{asset_name}")
    shutil.copyfile(base["path"], work)
    try:
        if _gh(["release", "view", tag, "--repo", base_repo], check=False).returncode == 0:
            _gh(["release", "delete", tag, "--repo", base_repo, "--yes"], check=False)
        _gh(["release", "create", tag, str(work), "--repo", base_repo, "--title", title])
    finally:
        work.unlink(missing_ok=True)
    url = f"https://github.com/{base_repo}/releases/download/{tag}/{asset_name}"
    return tag, url


def dispatch_inject_run(
    *,
    repo: str,
    base_url: str,
    selections: list[dict[str, str]],
    resolved: list[Resolved],
    app_name: str,
    bundle_id: str,
) -> str:
    """Dispatch Build & Inject Tweak for one base + every selected tweak."""
    if shutil.which("gh") is None:
        raise FactoryError("--run needs the `gh` CLI on PATH")
    _require(len(resolved) == len(selections) and resolved, "nothing resolved to inject")
    command = [
        "workflow",
        "run",
        "build-tweak.yml",
        "--repo",
        repo,
        "-f",
        f"base_ipa_url={base_url}",
        "-f",
        f"tweak_deb_url={resolved[0].url}",
    ]
    extra_urls = "\n".join(item.url for item in resolved[1:])
    if extra_urls:
        command += ["-f", f"extra_deb_urls={extra_urls}"]
    command += ["-f", f"tweak_name={selections[0]['name']}"]
    if app_name:
        command += ["-f", f"app_name={app_name}"]
    if bundle_id:
        command += ["-f", f"bundle_id={bundle_id}"]
    return _gh(command).stdout.strip()


def save_selection(
    registry: Registry,
    path: Path,
    *,
    slug: str,
    selections: list[dict[str, str]],
    app_name: str,
    bundle_id: str,
    tag_prefix: str,
    base_key: str,
    base_url: str | None,
    base_repo: str | None,
) -> None:
    """Upsert the selection as a registry build (validated before writing)."""
    _require(base_key in registry.base_apps, f"base app {base_key!r} is not in baseApps")
    primary = next((item for item in selections if item.get("id")), None)
    _require(
        primary is not None,
        "a registry build needs at least one official tweak (one with a repo) as its primary deb - "
        "reorder the selection or register the tweak under 'tweaks'",
    )
    spec = registry.tweak(primary["id"])
    entry: dict[str, Any] = {
        "slug": slug,
        "name": app_name or slug.replace("-", " ").title(),
        "enabled": True,
        "catalogApp": slug,
        "deb": {
            "source": "github-release",
            "repo": spec.repo,
            "assetRegex": spec.asset_regex.pattern,
            "archPreference": list(spec.arch_preference or ARCHES),
        },
        "base": base_key,
    }
    if bundle_id:
        entry["bundleId"] = bundle_id
    if app_name:
        entry["appName"] = app_name
    extra_debs: list[dict[str, Any]] = []
    for item in selections:
        if item is primary:
            continue
        if item.get("id"):
            extra_debs.append({"id": item["id"]})
        else:
            extra_debs.append({"name": item["name"], "url": item["url"]})
    if extra_debs:
        entry["extraDebs"] = extra_debs
    entry["publish"] = {"tagPrefix": tag_prefix, "prerelease": True}

    raw = json.loads(path.read_text(encoding="utf-8"))
    builds = raw.setdefault("builds", [])
    replaced = False
    for index, existing in enumerate(builds):
        if isinstance(existing, dict) and existing.get("slug") == slug:
            builds[index] = entry
            replaced = True
            break
    if not replaced:
        builds.append(entry)
    if base_url:
        raw.setdefault("baseApps", {})[base_key] = {
            "description": f"Operator base app for {slug} (set by `tweak_factory.py build` from a link)",
            "source": "url",
            "url": base_url,
        }
    elif base_repo:
        apps_block = raw.setdefault("baseApps", {})
        current = apps_block.get(base_key)
        current = current if isinstance(current, dict) else {}
        apps_block[base_key] = {
            "description": (
                f"Decrypted base app for {slug} releases (managed by `tweak_factory.py build --publish-base`)"
            ),
            "source": "release",
            "repo": base_repo,
            "tagPrefix": str(current.get("tagPrefix") or "youtube-"),
            "assetGlob": str(current.get("assetGlob") or "*.ipa"),
        }

    original = path.read_text(encoding="utf-8")
    atomic_write_text(path, json.dumps(raw, indent=2, sort_keys=True) + "\n")
    try:
        load_registry(path)
    except FactoryError:
        atomic_write_text(path, original)
        raise
    action = "updated" if replaced else "added"
    print(f"tweak-factory: {action} build {slug!r} in {path}")


def run_build(args: argparse.Namespace) -> int:
    """The operator flow: base (local or link) + tweaks + options -> actions."""
    registry = load_registry()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    base_arg = args.base
    if not base_arg and interactive:
        base_arg = input("Base IPA (local .ipa path or https URL): ").strip()
    _require(base_arg, "no base IPA given (pass --base, or run interactively)")
    if base_arg.startswith("http://"):
        raise FactoryError("base URL must be https (sourcing policy is https-only)")
    base = probe_remote_ipa(base_arg) if base_arg.startswith("https://") else read_local_ipa(Path(base_arg))

    customs = list(args.custom_deb or [])
    if interactive and not args.tweaks and not customs:
        selections = pick_tweaks_interactively(registry)
    else:
        selections = selections_from_args(registry, args.tweaks, customs)
    _require(selections, "no tweaks selected (see data/tweak-builds.json 'tweaks' or --custom-deb)")
    resolved = [resolve_selection(registry, item, token=_token()) for item in selections]

    default_name = "uProVid" if args.slug == "uprovid" else args.slug.replace("-", " ").title()
    app_name = args.app_name
    if app_name is None and interactive:
        app_name = input(f"App display name [{default_name}]: ").strip() or default_name
    app_name = app_name or default_name
    bundle_id = args.bundle_id or base.get("bundle_id") or ""
    if interactive:
        bundle_id = input(f"Bundle ID [{bundle_id or 'keep original'}]: ").strip() or bundle_id
    tag_prefix = args.tag_prefix or f"tweak-build/{args.slug}"

    size = base.get("size")
    size_text = f"{size / 1048576:.1f} MB" if isinstance(size, int) else "size unknown"
    print()
    print(f"{app_name} build plan")
    print(
        f"  base      {base['name']} ({base.get('bundle_id') or 'bundle id n/a'} "
        f"{base.get('version') or 'version n/a'}) - {size_text}"
    )
    if base.get("sha256"):
        print(f"            sha256 {base['sha256']}")
    print("  tweaks:")
    for item, resolved_item in zip(selections, resolved, strict=True):
        print(f"    - {item['name']} {resolved_item.version}  {resolved_item.url}")
    print(f"  app name  {app_name}")
    print(f"  bundle id {bundle_id or '(keep original)'}")
    print(f"  tag prefix {tag_prefix}")
    if args.notes:
        print(f"  notes     {args.notes}")

    if args.matrix_out:
        matrix = {
            "slug": args.slug,
            "app_name": app_name,
            "bundle_id": bundle_id,
            "tag_prefix": tag_prefix,
            "base": base,
            "tweaks": [
                {
                    "name": item["name"],
                    "version": resolved_item.version,
                    "url": resolved_item.url,
                    "name_file": resolved_item.name,
                }
                for item, resolved_item in zip(selections, resolved, strict=True)
            ],
        }
        atomic_write_text(Path(args.matrix_out), json.dumps(matrix, indent=2, sort_keys=True) + "\n")
        print(f"  plan      {args.matrix_out}")

    base_url_for_run: str | None = base["url"] if base["kind"] == "url" else None
    published_repo: str | None = None
    if args.publish_base:
        if base["kind"] != "local":
            raise FactoryError("--publish-base only applies to a local base (a link needs no upload)")
        base_cfg = registry.base_apps[args.base_app]
        base_repo = args.base_repo or (base_cfg.get("repo") if isinstance(base_cfg.get("repo"), str) else "")
        _require(
            _is_repo(base_repo),
            f"no base repo for --publish-base (set --base-repo owner/name or baseApps.{args.base_app}.repo)",
        )
        title = (
            f"{base.get('display_name') or base['name']} {base.get('version') or ''} "
            "(decrypted base for OmniSource Tweak Factory)"
        ).strip()
        tag, base_url_for_run = publish_local_base(base_repo=base_repo, base_cfg=base_cfg, base=base, title=title)
        published_repo = base_repo
        print(f"  base      published as release {tag} in {base_repo}")
        print(f"            {base_url_for_run}")

    if args.run:
        if base_url_for_run is None:
            raise FactoryError(
                "local base: the macOS runner can only download a URL - pass a --base https link or "
                "use --publish-base to upload it to the base repo first"
            )
        run_url = dispatch_inject_run(
            repo=repository_slug(),
            base_url=base_url_for_run,
            selections=selections,
            resolved=resolved,
            app_name=app_name,
            bundle_id=bundle_id,
        )
        print(f"  run       {run_url or 'dispatched (run URL not echoed)'}")
        print("            the patched IPA arrives as the workflow artifact (retention 14 days)")

    if args.save_registry or args.factory:
        save_selection(
            registry,
            REGISTRY_PATH,
            slug=args.slug,
            selections=selections,
            app_name=app_name,
            bundle_id=bundle_id,
            tag_prefix=tag_prefix,
            base_key=args.base_app,
            base_url=base_url_for_run if not args.publish_base else None,
            base_repo=published_repo,
        )
        print("            commit + push data/tweak-builds.json before the scheduled factory uses it")

    if args.factory:
        if shutil.which("gh") is not None:
            out = _gh(
                ["workflow", "run", "tweak-factory.yml", "--repo", repository_slug(), "-f", "force=true"]
            ).stdout.strip()
            print(f"  factory   {out or 'dispatched'}")
        else:
            print("  factory   gh CLI not found - run: gh workflow run tweak-factory.yml -f force=true")

    print(
        "  next      the Tweak Factory publishes releases under "
        f"{tag_prefix}/*; the {args.slug} catalog entry picks them up on the next sync"
    )
    return 0


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

    publish.add_argument("--deb-name", default="")
    publish.add_argument("--ipa", required=True)
    publish.add_argument("--fragment", required=True)
    publish.add_argument("--run-url", default="")
    publish.add_argument(
        "--extra-debs-json",
        default="",
        help='JSON array of extra tweaks injected alongside: [{"name","version","url","sha256"}]',
    )

    merge = sub.add_parser("merge", help="merge state fragments into the committed state file")
    merge.add_argument("fragments", nargs="+", type=Path)
    merge.add_argument("--out", type=Path, default=STATE_PATH)

    notes = sub.add_parser("notes", help="render release notes for one build (stdout)")
    for flag in _BUILD_ARG_FLAGS:
        notes.add_argument(f"--{flag}", required=True)
    notes.add_argument("--deb-name", default="")
    notes.add_argument("--ipa", required=True)
    notes.add_argument("--run-url", default="")
    notes.add_argument("--extra-debs-json", default="", help="same shape as publish --extra-debs-json")

    build = sub.add_parser(
        "build",
        help="operator flow: base IPA (local or link) + tweak pick + custom options",
        description=(
            "Supply a base IPA (a local .ipa or an https link), pick the tweaks to inject "
            "(official upstream releases listed in the registry's 'tweaks' catalog, plus custom "
            ".deb URLs), set the app name / bundle ID / tag prefix, then optionally publish a "
            "local base to the dumps repo (--publish-base), dispatch a Build & Inject Tweak run "
            "(--run) and persist the selection for the scheduled Tweak Factory (--save-registry / "
            "--factory). Without --run/--factory this command only resolves and prints the plan."
        ),
    )
    build.add_argument("--base", default="", help="local .ipa path or https URL (prompted when interactive)")
    build.add_argument("--tweaks", default="", help="comma-separated tweak ids from the registry, 'all' or 'none'")
    build.add_argument(
        "--custom-deb",
        action="append",
        default=None,
        metavar="NAME=URL",
        help="extra tweak from a direct https .deb URL (repeatable)",
    )
    build.add_argument("--app-name", default=None, help="CFBundleDisplayName (default: the app name for --slug)")
    build.add_argument("--bundle-id", default="", help="CFBundleIdentifier (default: the base IPA's)")
    build.add_argument("--tag-prefix", default="", help="release tag prefix (default: tweak-build/<slug>)")
    build.add_argument("--notes", default="", help="free-form notes for the plan (shown in the output)")
    build.add_argument("--slug", default="uprovid", help="catalog app / registry build slug (default: uprovid)")
    build.add_argument("--base-app", default="youtube", help="baseApps key the base belongs to (default: youtube)")
    build.add_argument(
        "--base-repo", default="", help="dumps repo for --publish-base (default: baseApps.<base-app>.repo)"
    )
    build.add_argument("--save-registry", action="store_true", help="persist the selection into data/tweak-builds.json")
    build.add_argument("--publish-base", action="store_true", help="publish a local base as a release (needs gh)")
    build.add_argument("--run", action="store_true", help="dispatch Build & Inject Tweak via gh")
    build.add_argument(
        "--factory", action="store_true", help="persist + dispatch the scheduled Tweak Factory (release lane)"
    )
    build.add_argument("--matrix-out", type=Path, default=None, help="write the resolved plan JSON here")

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
        elif args.command == "build":
            run_build(args)
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
