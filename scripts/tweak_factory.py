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


@dataclass(frozen=True)
class Registry:
    base_apps: dict[str, dict[str, Any]]
    builds: tuple[Build, ...]


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
            )
        )
    return Registry(base_apps=dict(base_apps), builds=tuple(builds))


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
    """Newest official .deb for the tweak from its own GitHub releases."""
    releases = _get_json(RELEASES_URL.format(repo=build.deb_repo), token=token)
    _require(isinstance(releases, list), f"{build.deb_repo}: unexpected releases payload")
    candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        asset = _pick_asset(release, build.deb_regex, build.arch_preference)
        if asset is not None:
            candidates.append((release, asset, str(release.get("tag_name", ""))))
    _require(candidates, f"{build.deb_repo}: no release asset matches the registered pattern")
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
    _require(URL_RE.match(url) is not None, f"{build.deb_repo}: asset download URL is not https")
    return Resolved(version=str(release.get("tag_name", "")) or tag, url=url, name=str(asset.get("name", "")), tag=tag)


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
                }
            )
        except FactoryError as error:
            skipped.append({"slug": build.slug, "reason": str(error)})
    return {"include": include, "uptodate": uptodate, "skipped": skipped, "disabled": disabled}


def summary_markdown(result: dict[str, Any]) -> str:
    lines = ["## Tweak Factory plan", ""]
    if result["include"]:
        lines.append("| Build | deb | base | release tag |")
        lines.append("| --- | --- | --- | --- |")
        for entry in result["include"]:
            row = (
                f"| {entry['slug']} | {entry['deb_version']} "
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

    merge = sub.add_parser("merge", help="merge state fragments into the committed state file")
    merge.add_argument("fragments", nargs="+", type=Path)
    merge.add_argument("--out", type=Path, default=STATE_PATH)

    notes = sub.add_parser("notes", help="render release notes for one build (stdout)")
    for flag in _BUILD_ARG_FLAGS:
        notes.add_argument(f"--{flag}", required=True)
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
