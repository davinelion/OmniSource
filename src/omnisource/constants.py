"""Shared constants and path configuration.

Paths are injectable so tests can run against a temporary tree. Production
code uses :func:`Paths.default`, which resolves the repository root from this
file's location (``src/omnisource/constants.py`` → repo root).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "OmniSource-Sync/3.0 (+https://github.com/iamsmmh/OmniSource)"
GITHUB_API_ROOT = "https://api.github.com"

VERSION_RE_PATTERN = r"(\d+\.\d+(?:\.\d+)?)"
TAG_NUMBER_RE_PATTERN = r"(\d+)\s*$"
README_MARKERS = ("<!-- omnisource:catalog:start -->", "<!-- omnisource:catalog:end -->")
README_STATS_MARKERS = ("<!-- omnisource:stats:start -->", "<!-- omnisource:stats:end -->")

# A download URL is considered reachable when the server answers with one of
# these. 206 covers ranged GET fallbacks, 3xx covers CDN redirects.
ALIVE_CODES = frozenset({200, 206, 301, 302, 303, 307, 308})
RETRYABLE_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Containers an iOS sideloading client can install. ``.tipa`` is a
# TrollStore-named IPA: byte-identical to a .ipa, only the extension
# tells TrollStore to install it without the Share sheet. ``.deb`` is the
# native jailbreak/TrollStore package format (dpkg), which is the only
# official distribution format for apt-repo apps such as Cercube.
INSTALLABLE_SUFFIXES = (".ipa", ".tipa", ".deb")

VALID_STATUSES = frozenset({"stable", "beta", "manual", "unmaintained", "deprecated"})
VALID_VERIFICATION_METHODS = frozenset(
    {
        "github-release",
        "github-tag",
        "gitlab-release",
        "codeberg-release",
        "forgejo-release",
        "json-feed",
        "altstore",
        "feather",
        "manual-mirror",
        "self-built",
    }
)
KNOWN_CLIENTS = frozenset({"altstore", "sidestore", "feather", "esign", "livecontainer"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

# Pipeline state, health snapshots, updates timeline, badges and the derived
# intelligence documents (discovery catalog, verification, status, duplicates,
# analytics, sources) are not distributable AltStore feeds.
ALTSTORE_NON_FEED = frozenset(
    {
        "state.json",
        "health.json",
        "updates.json",
        "badge-apps.json",
        "badge-health.json",
        "badge-version.json",
        "badge-sync.json",
        "badge-verified.json",
        "discovery.json",
        "verification.json",
        "status.json",
        "duplicates.json",
        "analytics.json",
        "sources.json",
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
        "ipa-downloader.json",
        "dead_apps.json",
        "collections.json",
        "translation-status.json",
        "asset-manifest.json",
    }
)


@dataclass(frozen=True)
class Paths:
    """Filesystem layout of an OmniSource checkout."""

    root: Path
    catalog: Path
    feeds: Path
    assets: Path
    readme: Path
    cache: Path

    @classmethod
    def from_root(cls, root: Path) -> Paths:
        root = root.resolve()
        feeds = root / "feeds"
        return cls(
            root=root,
            catalog=root / "catalog.json",
            feeds=feeds,
            assets=root / "assets",
            readme=root / "README.md",
            cache=root / ".cache" / "omnisource",
        )

    @classmethod
    def default(cls) -> Paths:
        # src/omnisource/constants.py → src/omnisource → src → repo
        return cls.from_root(Path(__file__).resolve().parents[2])
