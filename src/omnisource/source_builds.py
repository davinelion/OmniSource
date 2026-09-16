"""Source builds: the lane for iOS projects that publish source but no binary.

The catalogue answers one question — *where is the developer's own signed build?*
Some worthwhile projects never answer it: a tweak that ships as a ``.deb``, an
installer whose releases are only Linux AppImages, a VNC server whose releases
are a GitHub Actions artifact. Those are not publishable through a feed, and
they must not be smuggled in as hand-typed metadata.

``data/source_builds.json`` is the *separate* lane for them. Each entry is a
reviewed recipe: the exact revision to build, the SHA-256 of the source archive
at that revision, the build commands, and what to do with the result. A recipe
never becomes a feed entry, and OmniSource never signs or hosts the output —
that is the whole point of keeping it in its own file.

What makes a recipe trustworthy instead of aspirational:

* the revision is pinned by full commit SHA, not a branch name;
* the source archive digest was computed by downloading that exact archive, so
  ``fetch``/``verify`` can prove the tree you are about to build is the tree that
  was reviewed (this is verifiable even where upstream release binaries are not);
* the build commands come from the project's own Makefile/README/workflow, and
  ``source.upstreamWorkflow`` names the file that proves it;
* the policy in :mod:`omnisource.source_policy` applies here too — a recipe may
  not fetch from or point at a rejected source.

Public API: :func:`load_builds`, :func:`validate_builds`, :func:`plan`,
:func:`verify_archive`, and the ``python3 -m omnisource.source_builds`` CLI
(:func:`main`), which ``scripts/validate.py`` and CI use.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BUILDS_VERSION = 1
BUILDS_RELATIVE_PATH = Path("data/source_builds.json")
SCHEMA_RELATIVE_PATH = Path("schemas/source-builds.schema.json")

KINDS = ("app", "tweak", "toolchain", "signer")
# iOS artifacts a recipe is allowed to claim it produces; a recipe for a desktop
# tool is fine as a *signer*/*toolchain*, but must not pretend to ship an app.
IOS_ARTIFACT_SUFFIXES = (".ipa", ".tipa", ".deb")
FORGES = ("github", "gitlab", "codeberg", "forgejo", "gitea")
PIN_TYPES = ("tag", "branch", "commit")
SIGNING_MODELS = ("on-device", "self-hosted", "own-certificate", "unsigned", "none")

_SHA1_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_SLUG_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{1,31}\Z")
_HEX_DIGITS = 16


@dataclass(frozen=True)
class Pin:
    """The revision a recipe was reviewed at."""

    ref: str = ""
    type: str = ""
    commit: str = ""
    committed_at: str = ""

    @property
    def label(self) -> str:
        short = self.commit[:10]
        if not self.ref or self.ref == self.commit:
            return f"commit {short}"
        return f"{self.type} {self.ref}@{short}"


@dataclass(frozen=True)
class Source:
    """The archive whose digest was verified."""

    archive_url: str = ""
    sha256: str = ""
    bytes: int = 0
    upstream_workflow: str = ""


@dataclass(frozen=True)
class Build:
    """How to turn the pinned source into an artifact."""

    system: str = ""
    requirements: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    docs: str = ""


@dataclass(frozen=True)
class Recipe:
    """One reviewed source-build recipe."""

    slug: str
    name: str
    kind: str
    summary: str
    forge: str
    repo: str
    url: str
    license: str = ""
    app: str = ""
    pin: Pin = field(default_factory=Pin)
    source: Source = field(default_factory=Source)
    build: Build = field(default_factory=Build)
    signing: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def is_ios(self) -> bool:
        return self.kind in {"app", "tweak"}


@dataclass
class BuildsFile:
    """The loaded recipe set plus the review metadata around it."""

    recipes: list[Recipe] = field(default_factory=list)
    contract: dict[str, str] = field(default_factory=dict)
    updated: str = ""
    path: Path | None = None
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.recipes)

    def get(self, slug: str) -> Recipe | None:
        for recipe in self.recipes:
            if recipe.slug == slug:
                return recipe
        return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def default_builds_path(root: Path) -> Path:
    return root / BUILDS_RELATIVE_PATH


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_builds(document: Any, *, path: Path | None = None) -> BuildsFile:
    """Parse a recipe document; the first structural problem becomes ``error``.

    Parsing is strict on purpose: a recipe whose shape is unclear cannot be
    audited, and an unaudited "just build it" instruction is how unverified
    binaries get into a project that promises the opposite.
    """
    if not isinstance(document, dict):
        return BuildsFile(path=path, error="expected a JSON object at the top level")
    version = document.get("version")
    if version not in (None, BUILDS_VERSION):
        return BuildsFile(path=path, error=f"unsupported version {version!r} (expected {BUILDS_VERSION})")
    entries = document.get("builds")
    if entries is None:
        return BuildsFile(path=path, error="'builds' is missing")
    if not isinstance(entries, list):
        return BuildsFile(path=path, error="'builds' must be a list")

    contract = document.get("contract")
    recipes: list[Recipe] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            return BuildsFile(path=path, error=f"builds[{index}] is not an object")
        slug = str(item.get("slug") or "").strip()
        if not slug:
            return BuildsFile(path=path, error=f"builds[{index}] has no slug")
        pin_raw, source_raw, build_raw = item.get("pin"), item.get("source"), item.get("build")
        if not isinstance(pin_raw, dict):
            return BuildsFile(path=path, error=f"{slug}: 'pin' must be an object")
        if not isinstance(source_raw, dict):
            return BuildsFile(path=path, error=f"{slug}: 'source' must be an object")
        if not isinstance(build_raw, dict):
            return BuildsFile(path=path, error=f"{slug}: 'build' must be an object")
        recipes.append(
            Recipe(
                slug=slug,
                name=str(item.get("name") or "").strip(),
                kind=str(item.get("kind") or "").strip(),
                summary=str(item.get("summary") or "").strip(),
                forge=str(item.get("forge") or "").strip(),
                repo=str(item.get("repo") or "").strip(),
                url=str(item.get("url") or "").strip(),
                license=str(item.get("license") or "").strip(),
                app=str(item.get("app") or "").strip(),
                pin=Pin(
                    ref=str(pin_raw.get("ref") or "").strip(),
                    type=str(pin_raw.get("type") or "").strip(),
                    commit=str(pin_raw.get("commit") or "").strip().casefold(),
                    committed_at=str(pin_raw.get("committedAt") or "").strip(),
                ),
                source=Source(
                    archive_url=str(source_raw.get("archiveURL") or "").strip(),
                    sha256=str(source_raw.get("sha256") or "").strip().casefold(),
                    bytes=_as_int(source_raw.get("bytes")),
                    upstream_workflow=str(source_raw.get("upstreamWorkflow") or "").strip(),
                ),
                build=Build(
                    system=str(build_raw.get("system") or "").strip(),
                    requirements=_as_tuple(build_raw.get("requirements")),
                    commands=_as_tuple(build_raw.get("commands")),
                    artifacts=_as_tuple(build_raw.get("artifacts")),
                    docs=str(build_raw.get("docs") or "").strip(),
                ),
                signing=dict(item.get("signing") or {}) if isinstance(item.get("signing"), dict) else {},
                verification=(
                    dict(item.get("verification") or {}) if isinstance(item.get("verification"), dict) else {}
                ),
                notes=str(item.get("notes") or "").strip(),
            )
        )
    return BuildsFile(
        recipes=recipes,
        contract={str(k): str(v) for k, v in (contract or {}).items()} if isinstance(contract, dict) else {},
        updated=str(document.get("updated") or "").strip(),
        path=path,
    )


def load_builds(root: Path) -> BuildsFile:
    """Load ``data/source_builds.json``; a missing file is an empty recipe set."""
    path = default_builds_path(Path(root))
    if not path.is_file():
        return BuildsFile(path=path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return BuildsFile(path=path, error=f"{path.name}: invalid JSON ({error})")
    return parse_builds(document, path=path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _check_https(label: str, url: str, errors: list[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        errors.append(f"{label}: must be an https:// URL with a host, got {url!r}")


def validate_builds(document: Any) -> list[str]:
    """Schema-ish checks for the recipe file itself (used by the validator).

    Deliberately hand-rolled: the project ships no JSON-Schema dependency, so the
    schema file documents the shape and this function enforces it.
    """
    parsed = parse_builds(document)
    errors: list[str] = []
    if parsed.error:
        errors.append(f"{BUILDS_RELATIVE_PATH}: {parsed.error}")
        return errors
    if not _DATE_RE.fullmatch(parsed.updated or ""):
        errors.append(f"{BUILDS_RELATIVE_PATH}: 'updated' must be an ISO date")
    if not parsed.contract:
        errors.append(f"{BUILDS_RELATIVE_PATH}: 'contract' must state what this lane is for")

    seen: set[str] = set()
    for recipe in parsed.recipes:
        label = f"{BUILDS_RELATIVE_PATH}: {recipe.slug}"
        if recipe.slug in seen:
            errors.append(f"{label}: duplicate slug")
        seen.add(recipe.slug)
        if not _SLUG_RE.fullmatch(recipe.slug):
            errors.append(f"{label}: slug must match {_SLUG_RE.pattern}")
        for attribute in ("name", "summary", "repo", "license"):
            if not getattr(recipe, attribute):
                errors.append(f"{label}: '{attribute}' is required")
        if recipe.kind not in KINDS:
            errors.append(f"{label}: kind must be one of {', '.join(KINDS)}")
        if recipe.forge not in FORGES:
            errors.append(f"{label}: forge must be one of {', '.join(FORGES)}")
        if recipe.forge == "github" and not re.fullmatch(r"[^/]+/[^/]+", recipe.repo):
            errors.append(f"{label}: repo must be 'owner/name'")
        for url in (recipe.url, recipe.source.archive_url):
            _check_https(f"{label}: url", url, errors)
        if recipe.pin.type not in PIN_TYPES:
            errors.append(f"{label}: pin.type must be one of {', '.join(PIN_TYPES)}")
        if not _SHA1_RE.fullmatch(recipe.pin.commit or ""):
            errors.append(f"{label}: pin.commit must be a full 40-character commit SHA (a tag name is not a pin)")
        if not _DATE_RE.fullmatch(recipe.pin.committed_at or ""):
            errors.append(f"{label}: pin.committedAt must be the ISO date of that commit")
        if recipe.pin.type == "branch":
            errors.append(f"{label}: pin.type 'branch' is not reviewable — pin a tag or a commit")
        if not _SHA256_RE.fullmatch(recipe.source.sha256 or ""):
            errors.append(f"{label}: source.sha256 must be the 64-hex digest of the archive at that revision")
        if recipe.source.bytes <= 0:
            errors.append(f"{label}: source.bytes must record the archive size that was hashed")
        if not recipe.build.system:
            errors.append(f"{label}: build.system is required")
        if not recipe.build.commands:
            errors.append(f"{label}: build.commands is required")
        if not recipe.build.artifacts:
            errors.append(f"{label}: build.artifacts is required")
        elif recipe.is_ios and not any(artifact.endswith(IOS_ARTIFACT_SUFFIXES) for artifact in recipe.build.artifacts):
            errors.append(
                f"{label}: a '{recipe.kind}' recipe must name an iOS artifact ({', '.join(IOS_ARTIFACT_SUFFIXES)})"
            )
        if recipe.build.docs:
            _check_https(f"{label}: build.docs", recipe.build.docs, errors)
        if not recipe.build.requirements:
            errors.append(f"{label}: build.requirements is required (what must be installed first)")
        model = str(recipe.signing.get("model") or "")
        if model not in SIGNING_MODELS:
            errors.append(f"{label}: signing.model must be one of {', '.join(SIGNING_MODELS)}")
        if recipe.kind == "app" and model == "none":
            errors.append(f"{label}: an installable app needs a signing model, not 'none'")
        method = str(recipe.verification.get("method") or "")
        if not method:
            errors.append(f"{label}: verification.method is required")
        elif method != "source-archive-sha256":
            errors.append(f"{label}: verification.method must be 'source-archive-sha256' for this lane")
        verified_at = str(recipe.verification.get("verifiedAt") or "")
        if not _DATE_RE.fullmatch(verified_at):
            errors.append(f"{label}: verification.verifiedAt must be an ISO date")
        if not _as_tuple(recipe.verification.get("evidence")):
            errors.append(f"{label}: verification.evidence must list what was checked")
    return errors


def shipped_data_matches_schema(root: Path) -> list[str]:
    """Report keys in the shipped data file that the schema does not declare.

    The schema is documentation unless something compares it to the data, and
    this keeps the two from drifting apart without adding a dependency.
    """
    errors: list[str] = []
    data_path = Path(root) / BUILDS_RELATIVE_PATH
    schema_path = Path(root) / SCHEMA_RELATIVE_PATH
    if not data_path.is_file() or not schema_path.is_file():
        return errors
    try:
        document = json.loads(data_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:  # pragma: no cover - malformed repo file
        return [f"{schema_path.name}: cannot compare with data ({error})"]

    def declared(node: Any, seen: int = 0) -> set[str]:
        """Keys a node allows, following local ``$ref``s so definitions count."""
        if not isinstance(node, dict) or seen > 4:
            return set()
        reference = str(node.get("$ref") or "")
        if reference.startswith("#/definitions/"):
            node = ((schema.get("definitions") or {}).get(reference.rsplit("/", 1)[-1])) or {}
        found = set(node.get("properties") or {})
        for branch in ("anyOf", "oneOf", "allOf"):
            for option in node.get(branch) or []:
                found |= declared(option, seen + 1)
        return found

    if not isinstance(document, dict):
        return [f"{BUILDS_RELATIVE_PATH}: top level is not an object"]
    top = declared(schema)
    for key in document:
        if key not in top:
            errors.append(f"{BUILDS_RELATIVE_PATH}: '{key}' is not declared in {SCHEMA_RELATIVE_PATH.name}")
    recipe_keys = declared(((schema.get("properties") or {}).get("builds") or {}).get("items"))
    if not recipe_keys:
        errors.append(f"{SCHEMA_RELATIVE_PATH.name}: 'builds.items' declares no properties")
    for entry in document.get("builds") or []:
        if not isinstance(entry, dict):
            continue
        for key in entry:
            if key not in recipe_keys:
                errors.append(
                    f"{BUILDS_RELATIVE_PATH}: {entry.get('slug', '?')}: '{key}' is not declared in the schema"
                )
    return errors


def recipe_violations(builds: BuildsFile, *, policy: Any = None, catalog: Any = None) -> tuple[list[str], list[str]]:
    """Cross-check recipes against the sourcing policy and the catalogue.

    Returns ``(errors, warnings)``. Errors are the hard rules: a recipe may not
    fetch source from a rejected host, and it may not reference an app that does
    not exist. The warning is the soft one: a recipe for a project the catalogue
    already publishes is usually stale rather than wrong, so it is reported
    instead of blocking.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not builds or builds.error:
        return ([f"{BUILDS_RELATIVE_PATH}: {builds.error}"] if builds.error else []), warnings

    from omnisource.source_policy import decide  # local import: keeps this module usable standalone

    catalog_repo_by_slug: dict[str, str] = {}
    catalog_repos: dict[str, str] = {}
    if isinstance(catalog, dict):
        for app in catalog.get("apps") or []:
            if not isinstance(app, dict):
                continue
            repo = str((app.get("upstream") or {}).get("repo") or "").strip().casefold()
            if app.get("slug"):
                catalog_repo_by_slug[str(app["slug"])] = repo
            if repo:
                catalog_repos[repo] = str(app.get("slug"))

    for recipe in builds.recipes:
        for url in (recipe.url, recipe.source.archive_url, recipe.build.docs):
            if not url:
                continue
            decision = decide(url, policy=policy)
            if decision.blocked:
                errors.append(f"{recipe.slug}: {url} — {decision.detail}")
        if not recipe.url.startswith("https://"):
            errors.append(f"{recipe.slug}: url must be HTTPS")
        if recipe.app and recipe.app not in catalog_repo_by_slug:
            errors.append(f"{recipe.slug}: 'app' references '{recipe.app}', which is not in catalog.json")
        if recipe.kind in {"app", "tweak"} and recipe.repo.casefold() in catalog_repos:
            warnings.append(
                f"{recipe.slug}: catalog.json already publishes {recipe.repo} as "
                f"'{catalog_repos[recipe.repo.casefold()]}' — keep the recipe only if the "
                "source build is still the way to get this artifact"
            )
    return errors, warnings


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
def digest_file(path: Path) -> str:
    """SHA-256 of a file, streamed (source archives are tens of MB)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_HEX_DIGITS * 4096), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_archive(recipe: Recipe, destination: Path, *, timeout: int = 180) -> Path:
    """Download a recipe's pinned source archive into ``destination``.

    Only the archive named by the recipe is fetched, from the recipe's own URL:
    a recipe is a review record, not a general-purpose downloader.
    """
    if not recipe.source.archive_url.startswith("https://"):
        raise ValueError(f"{recipe.slug}: refusing to fetch a non-HTTPS archive URL")
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    name = recipe.source.archive_url.rstrip("/").split("/")[-1] or recipe.slug
    path = target / f"{recipe.slug}-{name}"
    # Only the recipe's own HTTPS archive is fetched, and it is checked against the
    # recorded digest by the caller, so no arbitrary URL can reach the network here.
    request = urllib.request.Request(
        recipe.source.archive_url,
        headers={"User-Agent": "omnisource-source-builds (verification)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, path.open("wb") as handle:
            handle.write(response.read())
    except (urllib.error.URLError, OSError) as error:
        raise RuntimeError(f"{recipe.slug}: could not fetch {recipe.source.archive_url}: {error}") from error
    return path


def verify_archive(recipe: Recipe, path: Path) -> tuple[bool, str, str]:
    """Compare a downloaded archive with the digest recorded in the recipe."""
    actual = digest_file(Path(path))
    if not _SHA256_RE.fullmatch(recipe.source.sha256 or ""):
        return False, actual, f"{recipe.slug}: recipe records no usable sha256"
    if actual != recipe.source.sha256:
        return (
            False,
            actual,
            (
                f"{recipe.slug}: digest mismatch — the archive is not the reviewed tree "
                f"(expected {recipe.source.sha256[:16]}…, got {actual[:16]}…)"
            ),
        )
    size = Path(path).stat().st_size
    if recipe.source.bytes and size != recipe.source.bytes:
        return True, actual, f"{recipe.slug}: digest matches but size differs ({size} != {recipe.source.bytes})"
    return True, actual, f"{recipe.slug}: source archive matches the reviewed digest"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def plan(recipe: Recipe, *, workdir: str = ".build/source") -> list[str]:
    """Reproducible shell transcript for one recipe, integrity check included.

    The digest is verified *before* anything is built, so a mirrored or silently
    updated archive cannot be compiled by accident. The final step is deliberately
    the signing instruction rather than a signing command: this project never
    signs, and the recipe says who should.
    """
    short = recipe.pin.commit[:10]
    lines = [
        f"# {recipe.name} — {recipe.summary}",
        f"# {recipe.forge}: {recipe.repo} @ {recipe.pin.label}",
        "set -euo pipefail",
        f'mkdir -p "{workdir}" && cd "{workdir}"',
        f'curl -fsSL -o {recipe.slug}.tar.gz "{recipe.source.archive_url}"',
        f"echo '{recipe.source.sha256}  {recipe.slug}.tar.gz' | sha256sum -c -",
        f"tar -xzf {recipe.slug}.tar.gz",
        f'cd "{recipe.repo.split("/")[-1]}-{recipe.pin.ref or short}" 2>/dev/null || cd */',
    ]
    lines += list(recipe.build.commands)
    lines.append(f'echo "artifacts (expect): {", ".join(recipe.build.artifacts)}"')
    tools = _as_tuple(recipe.signing.get("tools"))
    model = str(recipe.signing.get("model") or "")
    if model == "none":
        lines.append(f"# no signing step: {recipe.signing.get('notes') or 'this recipe produces no installable app'}")
    else:
        lines.append(
            f"# signing ({model}): {'/'.join(tools) or 'your own certificate'} — OmniSource does not sign or host "
            "this output"
        )
    return lines


def summary(builds: BuildsFile) -> dict[str, Any]:
    """Machine-readable overview, for ``--json`` and the docs."""
    return {
        "updated": builds.updated,
        "count": len(builds.recipes),
        "byKind": {
            kind: sum(1 for recipe in builds.recipes if recipe.kind == kind)
            for kind in KINDS
            if any(recipe.kind == kind for recipe in builds.recipes)
        },
        "byForge": {
            forge: sum(1 for recipe in builds.recipes if recipe.forge == forge)
            for forge in FORGES
            if any(recipe.forge == forge for recipe in builds.recipes)
        },
        "recipes": [
            {
                "slug": recipe.slug,
                "name": recipe.name,
                "kind": recipe.kind,
                "forge": recipe.forge,
                "repo": recipe.repo,
                "pin": recipe.pin.label,
                "license": recipe.license,
                "system": recipe.build.system,
                "artifacts": list(recipe.build.artifacts),
                "signingModel": recipe.signing.get("model", ""),
                "app": recipe.app,
            }
            for recipe in builds.recipes
        ],
    }


# ---------------------------------------------------------------------------
# Candidate discovery (source-only projects worth a recipe)
# ---------------------------------------------------------------------------
# Repositories are worth a recipe when the *only* thing upstream publishes is
# source. These suffixes are what a feed could publish; finding one in a release
# means the project belongs in catalog.json instead, so the probe reports it that
# way rather than as a recipe candidate.
IOS_ASSET_SUFFIXES = (".ipa", ".tipa", ".deb")

# Per-forge API shapes, kept as data so a new forge is one entry, not a rewrite.
# ``archive`` builds the URL whose digest a recipe would record.
FORGE_APIS: dict[str, dict[str, str]] = {
    "github": {
        "search": "https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page={limit}",
        "repo": "https://api.github.com/repos/{repo}",
        "releases": "https://api.github.com/repos/{repo}/releases/latest",
        "file": "https://api.github.com/repos/{repo}/contents/{path}?ref={ref}",
        "archive": "https://codeload.github.com/{repo}/tar.gz/refs/tags/{ref}",
        "archive_branch": "https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}",
        "web": "https://github.com/{repo}",
    },
    "codeberg": {
        "search": "https://codeberg.org/api/v1/repos/search?q={query}&limit={limit}&sort=stars",
        "repo": "https://codeberg.org/api/v1/repos/{repo}",
        "releases": "https://codeberg.org/api/v1/repos/{repo}/releases/latest",
        "file": "https://codeberg.org/api/v1/repos/{repo}/raw/commit/{ref}/{path}",
        "archive": "https://codeberg.org/{repo}/archive/{ref}.tar.gz",
        "web": "https://codeberg.org/{repo}",
    },
    "gitlab": {
        "search": "https://gitlab.com/api/v4/projects?search={query}&simple=true&order_by=star_count&per_page={limit}",
        "repo": "https://gitlab.com/api/v4/projects/{repo_id}",
        "releases": "https://gitlab.com/api/v4/projects/{repo_id}/releases?per_page=1",
        "file": "https://gitlab.com/api/v4/projects/{repo_id}/repository/files/{path}/raw?ref={ref}",
        "archive": "https://gitlab.com/api/v4/projects/{repo_id}/repository/archive.tar.gz?sha={ref}",
        "web": "https://gitlab.com/{repo}",
    },
}
FORGE_APIS["forgejo"] = dict(FORGE_APIS["codeberg"])

# Terms that historically turn up iOS projects publishing source only. Tweak
# families are searched by topic, not by app name, because the point is the build.
DEFAULT_TERMS = ("theos", "ios-tweak", "trollstore", "sideload", "altstore")
# Order matters: the specific ecosystem files win, and a bare Makefile is only
# ever "make" — a Makefile that includes theos' makefiles is detected above, so a
# project that happens to ship a Makefile is never mislabelled as a tweak build.
_BUILD_MARKERS = {
    "dub.json": "dub",
    "dub.sdl": "dub",
    "Package.swift": "swift",
    "go.mod": "go",
    "Cargo.toml": "cargo",
    "meson.build": "meson",
    "Makefile": "make",
}


def classify_build_system(paths: Any, makefile_text: str = "") -> str:
    """Best guess at the build system, from the files a repo actually has.

    A ``Makefile`` only means *theos* when it includes theos' makefiles — that
    distinction is the whole reason tweak repos are worth pinning individually.
    """
    names = {str(path).rsplit("/", 1)[-1] for path in paths}
    makefile = makefile_text or ""
    if "Makefile" in names and "THEOS" in makefile:
        return "theos"
    for marker, system in _BUILD_MARKERS.items():
        if marker in names:
            return system
    # Xcode projects are directories, so the marker can sit anywhere in the path.
    if any(".xcodeproj" in str(path) or ".xcworkspace" in str(path) for path in paths):
        return "xcodebuild"
    return ""


def has_ios_release(release: Any) -> tuple[bool, tuple[str, ...]]:
    """Does this release publish something a feed could distribute? (bool, names)"""
    assets: list[str] = []
    if isinstance(release, dict):
        for asset in release.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name"):
                assets.append(str(asset["name"]))
    published = tuple(name for name in assets if name.lower().endswith(IOS_ASSET_SUFFIXES))
    return bool(published), published


def recipe_skeleton(
    *,
    forge: str,
    repo: str,
    ref: str,
    commit: str,
    committed_at: str,
    name: str = "",
    description: str = "",
    license: str = "",
    system: str = "",
    archive_url: str = "",
    sha256: str = "",
    size: int = 0,
    ref_type: str = "",
) -> dict[str, Any]:
    """A reviewed-shaped recipe with every unverified field left for a human.

    ``ref_type`` is the caller's evidence that ``ref`` is a tag rather than a
    moving branch — a draft must not quietly claim a tag it never resolved.
    Otherwise the mechanical parts are filled in: ``summary``, ``commands``,
    ``signing`` and ``evidence`` stay empty on purpose, because guessing them is
    how a recipe becomes a rumour.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", repo.rsplit("/", 1)[-1].casefold()).strip("-")[:32]
    recipe: dict[str, Any] = {
        "slug": slug or "new-recipe",
        "name": name or repo,
        "kind": "tweak" if system == "theos" else "app",
        "summary": "",
        "forge": forge,
        "repo": repo,
        "url": str(FORGE_APIS.get(forge, {}).get("web", "")).format(repo=repo),
        "license": license,
        "pin": {
            "ref": ref,
            "type": ref_type or ("tag" if ref and not _SHA1_RE.fullmatch(ref) else "commit"),
            "commit": commit,
            "committedAt": committed_at,
        },
        "source": {"archiveURL": archive_url, "sha256": sha256, "bytes": size},
        "build": {"system": system, "requirements": [], "commands": [], "artifacts": []},
        "signing": {"model": "none", "tools": [], "notes": ""},
        "verification": {"method": "source-archive-sha256", "verifiedAt": "", "evidence": []},
        "notes": description,
    }
    return recipe


def candidate_findings(builds: BuildsFile) -> dict[str, Any]:
    """Coverage summary: which kinds/forges the lane currently proves, and gaps."""
    by_forge = dict.fromkeys(FORGES, 0)
    for recipe in builds.recipes:
        by_forge[recipe.forge] = by_forge.get(recipe.forge, 0) + 1
    return {
        "count": len(builds.recipes),
        "byKind": {kind: [r.kind for r in builds.recipes].count(kind) for kind in KINDS},
        "byForge": by_forge,
        "uncoveredForges": [forge for forge in FORGES if not by_forge.get(forge)],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """``python3 -m omnisource.source_builds`` — inspect, plan and verify recipes.

    ``check`` is the offline gate (CI, ``scripts/validate.py``); ``fetch`` and
    ``verify`` are the on-demand integrity check a reviewer runs before building.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m omnisource.source_builds",
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="command", required=True)

    lister = sub.add_parser("list", help="one line per recipe")
    lister.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="print a full recipe")
    show.add_argument("slug")
    checker = sub.add_parser("check", help="validate the file, the policy and the catalogue cross-links")
    checker.add_argument("--catalog", type=Path)
    plan_cmd = sub.add_parser("plan", help="print the build transcript for one recipe")
    plan_cmd.add_argument("slug")
    plan_cmd.add_argument("--workdir", default=".build/source")
    fetch = sub.add_parser("fetch", help="download the pinned source archive")
    fetch.add_argument("slug")
    fetch.add_argument("--dest", type=Path, default=Path(".build/source"))
    verify = sub.add_parser("verify", help="compare a downloaded archive with the recorded digest")
    verify.add_argument("slug")
    verify.add_argument("--archive", type=Path, required=True)
    hasher = sub.add_parser("hash", help="digest any file (used when authoring a recipe)")
    hasher.add_argument("path", type=Path)
    coverage = sub.add_parser("coverage", help="which forges/kinds the lane proves, and what is missing")
    coverage.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "hash":
        print(digest_file(args.path))
        return 0

    builds = load_builds(root)
    if builds.error:
        print(f"::error::source-build recipes are unusable: {builds.error}", file=sys.stderr)
        return 2

    if args.command == "coverage":
        info = candidate_findings(builds)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"{info['count']} recipe(s): " + ", ".join(f"{k}={v}" for k, v in info["byKind"].items()))
            print("per forge: " + ", ".join(f"{k}={v}" for k, v in info["byForge"].items()))
            if info["uncoveredForges"]:
                print("no recipe yet for: " + ", ".join(info["uncoveredForges"]))
        return 0

    if args.command in {"list", "show"}:
        if args.command == "list":
            if args.json:
                print(json.dumps(summary(builds), indent=2, ensure_ascii=False))
                return 0
            for recipe in builds.recipes:
                print(
                    f"{recipe.slug:<16} {recipe.kind:<9} {recipe.forge:<8} {recipe.repo:<28} "
                    f"{recipe.pin.label:<28} {recipe.build.system:<8} {recipe.license}"
                )
            print(f"{len(builds.recipes)} recipe(s); none of these are published as OmniSource builds")
            return 0
        recipe = builds.get(args.slug)
        if recipe is None:
            print(f"::error::no recipe '{args.slug}'", file=sys.stderr)
            return 1
        print(json.dumps(_raw_recipe(root, args.slug), indent=2, ensure_ascii=False))
        return 0

    if args.command == "check":
        errors = validate_builds(json.loads((root / BUILDS_RELATIVE_PATH).read_text(encoding="utf-8")))
        errors += shipped_data_matches_schema(root)
        catalog_path = args.catalog or (root / "catalog.json")
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else None
        from omnisource.source_policy import load_policy

        policy_errors, policy_warnings = recipe_violations(builds, policy=load_policy(root), catalog=catalog)
        errors += policy_errors
        for error in errors:
            print(f"::error::{error}", file=sys.stderr)
        for warning in policy_warnings:
            print(f"::warning::{warning}", file=sys.stderr)
        if errors:
            print(f"FAILED: {len(errors)} recipe problem(s)")
            return 1
        print(f"OK: {len(builds.recipes)} source-build recipe(s) valid, policy-clean, cross-linked")
        return 0

    recipe = builds.get(args.slug)
    if recipe is None:
        print(f"::error::no recipe '{args.slug}'", file=sys.stderr)
        return 1
    if args.command == "plan":
        for line in plan(recipe, workdir=args.workdir):
            print(line)
        return 0
    if args.command == "fetch":
        path = fetch_archive(recipe, args.dest)
        ok, actual, message = verify_archive(recipe, path)
        print(f"{path} ({path.stat().st_size} bytes, sha256 {actual})")
        print(message)
        return 0 if ok else 1
    ok, actual, message = verify_archive(recipe, args.archive)
    print(f"{args.archive}: sha256 {actual}")
    print(message)
    return 0 if ok else 1


def _raw_recipe(root: Path, slug: str) -> Any:
    """The untouched recipe object, so ``show`` prints what was reviewed."""
    document = json.loads((root / BUILDS_RELATIVE_PATH).read_text(encoding="utf-8"))
    for entry in document.get("builds") or []:
        if isinstance(entry, dict) and str(entry.get("slug")) == slug:
            return entry
    return {"slug": slug, "error": "not found"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
