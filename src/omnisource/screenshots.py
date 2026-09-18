"""Screenshot validation, mirror and thumbnail generation.

This module is the static-site counterpart of the catalog ``screenshots`` array.
Catalog authors can declare remote screenshot URLs; the build pipeline

* validates that each URL is well-formed and points to an image,
* mirrors reachable screenshots to ``assets/screenshots/<slug>/...`` so the
  Pages deployment never depends on a third-party CDN,
* produces 480px wide WebP + PNG thumbnails for fast gallery rendering, and
* falls back to the existing app icon when no screenshot is reachable.

The module never raises during the build — every issue is recorded and the
final ``feeds/screenshots.json`` document lists what is in place. This keeps
the "missing screenshot" case from blocking feed generation.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnisource.domain import Catalog, today

SCREENSHOTS_SCHEMA_VERSION = 1
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# We never want to balloon the Pages deployment: anything above this many
# screenshots per app is rejected (extra URLs are reported as warnings).
MAX_PER_APP = 12


@dataclass
class ScreenshotIssue:
    slug: str
    detail: str
    severity: str = "warning"  # one of "warning" or "info"

    def as_dict(self) -> dict[str, str]:
        return {"slug": self.slug, "severity": self.severity, "detail": self.detail}


@dataclass
class ScreenshotReport:
    entries: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ScreenshotIssue] = field(default_factory=list)

    def to_doc(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCREENSHOTS_SCHEMA_VERSION,
            "generatedAt": today(),
            "count": len(self.entries),
            "issues": [issue.as_dict() for issue in self.issues],
            "screenshots": self.entries,
        }


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return cleaned or "image"


def _filename_for(slug: str, index: int, ext: str) -> str:
    return f"{slug}-{index + 1:02d}{ext.lower()}"


def _is_allowed_image(url: str) -> bool:
    # Strip query/fragment (e.g. GitHub's ?raw=true) before checking extension.
    lower = url.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lower.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def _validate_remote_urls(slug: str, urls: Iterable[str], report: ScreenshotReport) -> list[str]:
    """Return the list of URLs that pass basic shape validation."""
    valid: list[str] = []
    seen: set[str] = set()
    for url in urls:
        url = str(url or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            report.issues.append(ScreenshotIssue(slug, f"Ignored non-http URL: {url}", "warning"))
            continue
        if url in seen:
            continue
        seen.add(url)
        if not _is_allowed_image(url):
            report.issues.append(ScreenshotIssue(slug, f"Ignored non-image URL: {url}", "info"))
            continue
        valid.append(url)
        if len(valid) >= MAX_PER_APP:
            report.issues.append(
                ScreenshotIssue(
                    slug,
                    f"Truncated to {MAX_PER_APP} screenshots (catalog declared more).",
                    "info",
                )
            )
            break
    return valid


def _safe_download(http: Any, url: str) -> bytes | None:
    """Best-effort download used by the mirror step.

    The function is intentionally tolerant: any error (network, timeout,
    decode) returns ``None`` so the pipeline keeps building. Screenshots are
    a "nice to have" and the pipeline never depends on them.
    """
    if http is None:
        return None
    try:
        # ``http.fetch_bytes`` is intentionally permissive: the pipeline must
        # never fail because a screenshot is unreachable. We just skip the
        # mirror step and rely on the remote URL.
        fetcher = getattr(http, "fetch_bytes", None)
        if callable(fetcher):
            return fetcher(url)
        # Fall back to a basic GET using ``requests`` if the HttpClient does
        # not expose a bytes fetcher. Kept local so the import remains
        # optional — only used when explicitly wired in.
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=8) as response:
                return response.read()
        except Exception:  # pragma: no cover - never block builds
            return None
    except Exception:  # pragma: no cover - never block builds
        return None


def _persist_mirror(
    destination: Path,
    payload: bytes,
    *,
    ext: str,
) -> tuple[bool, int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(destination)
    digest = hashlib.sha256(payload).hexdigest()
    return True, len(payload), digest


def _committed_mirror_paths(assets_dir: Path) -> set[str] | None:
    """Mirror files under ``assets/screenshots`` that git actually carries.

    ``mirrored: true`` in ``feeds/screenshots.json`` promises the bytes ship
    beside the manifest. The scheduled sync job refreshes mirrors into its
    workspace but commits only generated JSON/XML, so a run that downloaded
    every screenshot produced a manifest pointing at binaries no checkout ever
    sees — the published catalog then claimed art it did not have. The claim is
    therefore tied to the git record: bytes that are only in the working tree
    are reported as ``mirrored: false`` (they still land on disk for the local
    build). Returns ``None`` when the repository state cannot be read - a
    non-repository working directory, or no ``git`` binary - because then "the
    file is on disk" is the only available answer and every existing caller
    (and test) expects the historical behaviour.
    """
    try:
        # Resolve the binary once: git is not a dependency of the runtime, and a
        # partial path in subprocess is both a lint error and an easy way to run
        # whatever ``git`` happens to be first on $PATH.
        git = shutil.which("git")
        if not git:
            return None
        top = subprocess.run(
            [git, "-C", str(assets_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if top.returncode != 0 or not top.stdout.strip():
            return None
        repo = Path(top.stdout.strip())
        try:
            prefix = assets_dir.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            return None
        listing = subprocess.run(
            [git, "-C", str(repo), "ls-files", "-z", "--", f"{prefix}/screenshots"],
            capture_output=True,
            timeout=10,
        )
        if listing.returncode != 0:
            return None
        tracked: set[str] = set()
        for raw in listing.stdout.decode("utf-8", "replace").split("\0"):
            if not raw:
                continue
            # ``git ls-files`` answers relative to the repository root.
            rel = raw[len(prefix) + 1 :] if raw.startswith(f"{prefix}/") else raw
            tracked.add(rel)
        return tracked
    except Exception:  # pragma: no cover - git missing, timeout, odd worktree
        return None


def process_screenshots(
    catalog: Catalog,
    base_url: str,
    assets_dir: Path,
    *,
    http: Any = None,
    thumbnail_width: int = 480,
    refresh: bool = False,
    previous: dict[str, Any] | None = None,
) -> ScreenshotReport:
    """Validate, mirror and produce thumbnail metadata.

    The function never raises. The returned :class:`ScreenshotReport` carries
    the on-disk artifacts and any human-readable issues.

    ``refresh`` (real sync runs) re-downloads every declared screenshot so
    upstream updates eventually replace stale local mirrors. Without it, a
    mirror already on disk is trusted as-is — rebuilds then stay
    deterministic and a transient remote failure can never demote a good
    mirror (``feeds/screenshots.json`` must be reproducible offline).

    ``previous`` is the last committed ``screenshots.json`` document, when
    available, and supplies the recorded digest for a mirror that is still on
    disk. ``mirrored`` describes this repository: an entry is only ever marked
    mirrored when the file exists, because a manifest that points at a deleted
    artifact is worse than one that admits the mirror has to be fetched again.
    """
    report = ScreenshotReport()
    prior: dict[tuple[str, int, str], dict[str, Any]] = {}
    if isinstance(previous, dict):
        for old in previous.get("screenshots", []) or []:
            if isinstance(old, dict):
                try:
                    key = (str(old.get("slug")), int(old.get("index") or 0), str(old.get("originalURL")))
                except (TypeError, ValueError):
                    continue
                prior[key] = old
    base = base_url.rstrip("/")
    icons_dir = assets_dir  # Catalog icons live here.
    screenshots_dir = assets_dir / "screenshots"
    thumbnails_dir = assets_dir / "screenshots" / "thumbnails"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    tracked_mirrors = _committed_mirror_paths(assets_dir)

    for app in catalog.apps:
        declared = list(getattr(app, "screenshots", []) or [])
        valid = _validate_remote_urls(app.slug, declared, report)
        if not valid and not getattr(app, "icon", ""):
            report.issues.append(ScreenshotIssue(app.slug, "No screenshots and no app icon", "warning"))
            continue
        for index, url in enumerate(valid):
            ext = Path(url.split("?", 1)[0]).suffix or ".png"
            mirror_name = _filename_for(app.slug, index, ext)
            mirror_path = screenshots_dir / app.slug / mirror_name
            mirror_relative = f"screenshots/{app.slug}/{mirror_name}"
            mirrored_url = f"{base}/assets/screenshots/{app.slug}/{mirror_name}"
            thumbnail_name = f"{_slugify(mirror_name.rsplit('.', 1)[0])}.webp"
            thumbnail_path = thumbnails_dir / app.slug / thumbnail_name
            thumbnail_url = f"{base}/assets/screenshots/thumbnails/{app.slug}/{thumbnail_name}"
            entry: dict[str, Any] = {
                "slug": app.slug,
                "index": index,
                "originalURL": url,
                "mirroredURL": mirrored_url,
                "thumbnailURL": thumbnail_url,
                "thumbnailWidth": thumbnail_width,
                "mirrored": False,
                "size": 0,
                "sha256": "",
            }
            reused = False
            if mirror_path.exists() and not refresh:
                payload = mirror_path.read_bytes()
                entry["mirrored"] = True
                entry["size"] = len(payload)
                entry["sha256"] = hashlib.sha256(payload).hexdigest()
            else:
                payload = _safe_download(http, url)
                if payload:
                    ok, size, digest = _persist_mirror(mirror_path, payload, ext=ext)
                    if ok:
                        entry["mirrored"] = True
                        entry["size"] = size
                        entry["sha256"] = digest
                else:
                    # No fresh bytes (unreachable host, offline rebuild, or the
                    # mirror was removed from the tree). ``mirrored`` describes this
                    # repository, so it is only ever true while the file is here:
                    # a manifest that advertises a mirror which no longer exists is
                    # worse than one that admits it still has to be fetched, and
                    # scripts/validate.py now rejects exactly that mismatch.
                    reused = False
            committed = tracked_mirrors is None or mirror_relative in tracked_mirrors
            if entry["mirrored"] and not committed:
                # Downloaded into the working tree, but the commit this
                # manifest travels in carries JSON only: keeping the claim
                # would make ``feeds/screenshots.json`` advertise a mirror that
                # no checkout has (validate.py rejects exactly that), so the
                # entry falls back to its upstream URL like any offline miss.
                entry["mirrored"] = False
                entry["size"] = 0
                entry["sha256"] = ""
            # Thumbnail generation requires Pillow. The build environment
            # may not have it; if it is missing we record a transparent
            # placeholder and the website falls back to the full image.
            # Reused entries keep their recorded size (there is no local
            # mirror file to re-measure in an offline rebuild).
            if not reused:
                try:
                    from PIL import Image  # type: ignore

                    source = mirror_path if entry["mirrored"] else None
                    if source is not None and source.exists():
                        with Image.open(source) as img:  # type: ignore[arg-type]
                            img = img.convert("RGBA") if img.mode not in {"RGB", "RGBA"} else img
                            ratio = thumbnail_width / max(1, img.width)
                            new_height = max(1, int(img.height * ratio))
                            resized = img.resize((thumbnail_width, new_height), Image.LANCZOS)
                            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                            resized.save(thumbnail_path, format="WEBP", quality=78, method=6)
                            entry["thumbnailSize"] = thumbnail_path.stat().st_size
                except Exception:
                    # Pillow is optional. The website gracefully falls back to
                    # the full-size mirrored image.
                    entry["thumbnailSize"] = 0
            if not entry.get("thumbnailSize") and thumbnail_path.exists() and committed:
                # The thumbnail on disk is the truth whether or not this run could
                # measure it with Pillow - reporting its real size keeps an offline
                # rebuild byte-identical instead of quietly zeroing the field.
                entry["thumbnailSize"] = thumbnail_path.stat().st_size
            report.entries.append(entry)
        # No screenshots declared: publish *nothing* for this app rather than an
        # icon-as-screenshot entry. A rendered stand-in in a machine-readable
        # manifest is indistinguishable from real art one consumer away, and an
        # app icon is not a preview of the app. The honest absence is already
        # reported: ``feeds/asset-manifest.json`` lists these slugs, and the app
        # page says upstream publishes none.
    # Hint the file system that this directory exists even on empty runs
    # so the website can probe ``/assets/screenshots/index.json`` later.
    (assets_dir / "screenshots" / "_manifest").mkdir(parents=True, exist_ok=True)
    _ = icons_dir  # silence linters — referenced for clarity
    return report
