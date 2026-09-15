"""Curated collections (Phase 10).

A small set of hand-curated app groups — currently *YouTube*, *Music*,
*Emulators*, *Utilities*, *Productivity* and *Tweaks* — rendered as
``feeds/collections.json`` and turned into static collection pages
(``collections/<slug>/index.html``) by the site builder.

Membership is curated data (``COLLECTIONS`` below), not a runtime heuristic:
each entry lists the app slugs it contains, so the curation is visible in one
place and reviewed like code. Collections only ever reference apps that exist
in ``catalog.json``; the validator cross-checks that and fails CI otherwise.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from omnisource.domain import Catalog, today
from omnisource.io import atomic_write_text

COLLECTIONS_SCHEMA_VERSION = 1

COLLECTIONS: tuple[dict[str, Any], ...] = (
    {
        "slug": "youtube",
        "title": "YouTube",
        "subtitle": "Ad-free YouTube, YouTube Music and Music clients",
        "description": (
            "Every enhanced YouTube client in the catalog: background playback, ad blocking, "
            "downloads and sponsor-block for the main app and YouTube Music."
        ),
        "appSlugs": ("uyouenhanced", "ytlite", "youpro", "ytkp", "ytkace", "youmod", "maxtube", "ytmusic", "maxmusic"),
    },
    {
        "slug": "music",
        "title": "Music",
        "subtitle": "Music streaming and playback",
        "description": (
            "Tweaked and alternative music clients: lossless Spotify playback, ad-free YouTube Music and a library "
            "importer that skips the desktop sync entirely."
        ),
        "appSlugs": ("spotiflac", "ytmusic", "maxmusic", "cosmos", "byetunes"),
    },
    {
        "slug": "emulators",
        "title": "Emulators",
        "subtitle": "Game emulation and virtual machines on iOS",
        "description": "Run classic consoles and full virtual machines on a sideloaded iOS device.",
        "appSlugs": ("provenance", "utm"),
    },
    {
        "slug": "utilities",
        "title": "Utilities",
        "subtitle": "Installation and everyday utilities",
        "description": "Sideloading tools, a BitTorrent client and debugging helpers for everyday use.",
        "appSlugs": ("feather", "sidestore", "livecontainer", "itorrent"),
    },
    {
        "slug": "productivity",
        "title": "Productivity",
        "subtitle": "Tools for creators and developers",
        "description": "Developer tooling and client apps that keep your workflow moving.",
        "appSlugs": ("stikdebug", "bhtwitter", "aidoku", "tablehabit"),
    },
    {
        # The tweak family was scattered across YouTube/Music/Social and only
        # reachable through search, even though it is the single biggest group in
        # the catalog and the one people ask for. Membership is the *tweak's own*
        # published build: OmniSource never patches an IPA itself, so anything not
        # released installable by its own project is not here (see
        # docs/SOURCING-REPORT.md for the skipped .deb-only tweaks).
        "slug": "tweaks",
        "title": "Tweaks",
        "subtitle": "Tweaked apps and on-device tweak injectors",
        "description": (
            "Tweaked builds of YouTube, music, X, Instagram, Messenger, Discord and Twitch apps, plus the tools "
            "that inject tweaks into an app on the device. Every entry is published installable by the tweak "
            "project itself — OmniSource tracks and re-verifies that release, it does not patch binaries."
        ),
        "appSlugs": (
            "trollfools",
            "bootstrap",
            "lara",
            "purekfd",
            "uyouenhanced",
            "ytlite",
            "youpro",
            "ytkp",
            "ytkace",
            "youmod",
            "maxtube",
            "ytmusic",
            "maxmusic",
            "bhtwitter",
            "neofreebird",
            "redditfilter",
            "inkillerplus",
            "ryukgram",
            "ttkillerplus",
            "snmessenger",
            "twitchadblock",
            "raintweak",
            "infuseplus",
        ),
    },
)


def build_collections_doc(catalog: Catalog, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render ``feeds/collections.json`` from the curated definitions."""
    state = state or {}
    by_slug = {app.slug: app for app in catalog.apps}

    collections = []
    for definition in COLLECTIONS:
        slugs: list[str] = []
        apps: list[dict[str, Any]] = []
        for slug in definition["appSlugs"]:
            app = by_slug.get(slug)
            if app is None:
                continue  # validator catches unknown slugs; keep the doc buildable
            slugs.append(slug)
            versions = state.get(slug, {}).get("versions") if isinstance(state.get(slug), dict) else None
            newest = versions[0] if isinstance(versions, list) and versions else {}
            apps.append(
                {
                    "slug": app.slug,
                    "name": app.name,
                    "developer": app.developer,
                    "icon": app.icon,
                    "iconURL": f"{catalog.base_url}/assets/{app.icon}" if app.icon else "",
                    "shortDescription": str(app.raw.get("shortDescription") or app.raw.get("subtitle") or "")[:160],
                    "latestVersion": str(newest.get("version") or ""),
                    "downloadURL": str(newest.get("downloadURL") or ""),
                    "category": str(app.raw.get("category") or ""),
                }
            )
        collections.append(
            {
                "slug": definition["slug"],
                "title": definition["title"],
                "subtitle": definition["subtitle"],
                "description": definition["description"],
                "appCount": len(apps),
                "appSlugs": slugs,
                "apps": apps,
            }
        )

    return {
        "schemaVersion": COLLECTIONS_SCHEMA_VERSION,
        "generatedAt": today(),
        "count": len(collections),
        "collections": collections,
    }


def collection_slugs() -> tuple[str, ...]:
    return tuple(definition["slug"] for definition in COLLECTIONS)


def _app_card(app: dict[str, Any]) -> str:
    name = html.escape(app["name"])
    version = html.escape(app.get("latestVersion") or "")
    link = f"../../apps/{html.escape(app['slug'])}/"
    icon = html.escape(app.get("iconURL") or "../../assets/OmniSource.png")
    desc = html.escape((app.get("shortDescription") or "")[:120])
    return (
        f'<a class="col-app" href="{link}">\n'
        f'  <img src="{icon}" alt="{name} icon" width="64" height="64" loading="lazy">\n'
        f'  <span class="col-app-body"><span class="col-app-name">{name}</span>\n'
        f'  <span class="col-app-meta">{version}</span>\n'
        f'  <span class="col-app-desc">{desc}</span></span>\n'
        "</a>"
    )


def render_collection_page(catalog: Catalog, collection: dict[str, Any]) -> str:
    """Render one ``collections/<slug>/index.html`` page (Phase 10)."""
    slug = str(collection["slug"])
    title = str(collection["title"])
    page_url = f"{catalog.base_url}/collections/{slug}/"
    cards = "".join(_app_card(app) for app in collection.get("apps", []))
    meta_desc = str(collection.get("subtitle") or collection.get("description") or "")
    return f"""<!doctype html>
<html lang="en" data-theme="auto">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#e8eef8" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#07070f" media="(prefers-color-scheme: dark)">
  <meta name="theme-color" content="#07070f" id="themeColor">
  <title>{html.escape(title)} — OmniSource</title>
  <meta name="description" content="{html.escape(meta_desc)}">
  <link rel="canonical" href="{html.escape(page_url)}">
  <meta property="og:site_name" content="OmniSource">
  <meta property="og:title" content="{html.escape(title)} — OmniSource">
  <meta property="og:description" content="{html.escape(meta_desc)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{html.escape(page_url)}">
  <meta property="og:image" content="{html.escape(catalog.base_url)}/assets/OmniSource.png">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{html.escape(title)} — OmniSource">
  <meta name="twitter:description" content="{html.escape(meta_desc)}">
  <meta name="twitter:image" content="{html.escape(catalog.base_url)}/assets/OmniSource.png">
  <link rel="icon" type="image/png" href="../../assets/OmniSource.png">
  <link rel="apple-touch-icon" href="../../assets/OmniSource.png">
  <link rel="preload" href="../../assets/design-system/tokens.css" as="style">
  <link rel="preload" href="../../assets/design-system/components.css" as="style">
  <link rel="stylesheet" href="../../assets/design-system/tokens.css">
  <link rel="stylesheet" href="../../assets/design-system/utilities.css">
  <link rel="stylesheet" href="../../assets/design-system/animations.css">
  <link rel="stylesheet" href="../../assets/design-system/components.css">
  <style>
    .col-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem;margin-top:1rem}}
    .col-app{{display:flex;gap:.9rem;padding:1rem;border-radius:14px;text-decoration:none;color:inherit;
      background:var(--surface-2,rgba(128,128,150,.07))}}
    .col-app img{{width:64px;height:64px;border-radius:14px;object-fit:cover;flex-shrink:0}}
    .col-app-body{{display:flex;flex-direction:column;gap:.15rem;min-width:0}}
    .col-app-name{{font-weight:700}}
    .col-app-meta{{font-size:.8rem;opacity:.65}}
    .col-app-desc{{font-size:.85rem;opacity:.8;display:-webkit-box;-webkit-line-clamp:3;
      -webkit-box-orient:vertical;overflow:hidden}}
  </style>
  <script>
    (function () {{
      try {{
        var t = localStorage.getItem('omnisource-theme');
        if (t !== 'light' && t !== 'dark') t = 'auto';
        document.documentElement.dataset.theme = t;
      }} catch (e) {{}}
    }})();
  </script>
</head>
<body data-page="collections">
  <main class="shell" id="main" style="padding-top:1.5rem;padding-bottom:3rem">
    <p><a href="../../collections/">← All collections</a> · <a href="../../">Home</a></p>
    <h1 style="font-size:1.7rem">{html.escape(title)}</h1>
    <p style="opacity:.8">{html.escape(str(collection.get("subtitle") or ""))}</p>
    <p style="opacity:.7">{html.escape(str(collection.get("description") or ""))}</p>
    <div class="col-grid">
      {cards}
    </div>
  </main>
</body>
</html>
"""


def build_collection_pages(
    catalog: Catalog,
    state: dict[str, Any] | None,
    pages_dir: Path,
) -> list[Path]:
    """Write one ``collections/<slug>/index.html`` per curated collection."""
    doc = build_collections_doc(catalog, state or {})
    changed: list[Path] = []
    for collection in doc["collections"]:
        target = pages_dir / str(collection["slug"]) / "index.html"
        if atomic_write_text(target, render_collection_page(catalog, collection)):
            changed.append(target)
    return changed
