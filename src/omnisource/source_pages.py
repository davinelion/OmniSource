"""Source Explorer: enriched ``feeds/sources.json`` + static ``sources/`` pages.

The first-class surface for "a source" in OmniSource is the /sources/
section (modernization Phase 4):

* ``feeds/sources.json`` — the machine index of every upstream source. The
  pipeline already built it from the discovery grouping; the modernization
  pass enriches each entry with ``slug``/``page`` (stable URL identity),
  ``status``, ``score``/``level`` (reputation), ``healthScore``,
  ``appCount``/``verifiedApps``, ``updateFrequencyDays``, ``lastUpdate`` and
  ``feedURL`` so the static pages and the interactive explorer need no extra
  round-trips.
* ``sources/<slug>/index.html`` — one static, self-contained page per source
  (name, maintainer, source URL, app count, update frequency, health score,
  verification status, last update date, reputation badge + app cards).
  Rendered server-side like the app detail pages so crawlers and no-JS
  readers see the full document.

Slug rule: lowercase alphanumeric folding of the source identity (the same
identity ``reputation.py`` groups by), uniquified with numeric suffixes. The
final mapping ships inside ``feeds/sources.json`` so the sitemap, the website
and the pages can never disagree.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from omnisource.discovery import build_sources_doc as _build_base_sources_doc
from omnisource.io import atomic_write_text
from omnisource.utils.dates import version_dates

_NON_SLUG = re.compile(r"[^a-z0-9]+")

#: Design-system CSS class per reputation status (see components.css `.badge`).
STATUS_BADGE_CLASS = {
    "Verified": "verified",
    "Community Verified": "community",
    "Maintained": "blue",
    "Warning": "warn",
    "Inactive": "neutral",
    "Deprecated": "deprecated",
}


def source_slug(identity: str) -> str:
    """Filesystem/URL-safe slug for a source identity."""
    slug = _NON_SLUG.sub("-", str(identity).casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:48].strip("-") or "source"


def unique_slugs(identities: list[str]) -> dict[str, str]:
    """Map identity -> unique slug (collisions gain ``-2``, ``-3``… suffixes)."""
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for identity in identities:
        base = source_slug(identity)
        slug = base
        counter = 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        mapping[identity] = slug
    return mapping


def _average(values: list[float]) -> float | None:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 1)


def _fmt_days(value: Any) -> str:
    if value is None:
        return "—"
    try:
        days = float(value)
    except (TypeError, ValueError):
        return "—"
    if days < 1:
        return "<1 day"
    if days < 45:
        return f"{days:.0f} days"
    return f"{days / 30.44:.1f} months"


def _fmt_score(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.0f}/100"
    except (TypeError, ValueError):
        return str(value)


def build_sources_doc(
    catalog: Any,
    state: dict[str, Any],
    *,
    reputation_doc: dict[str, Any] | None = None,
    health_doc: dict[str, Any] | None = None,
    verification_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``feeds/sources.json`` on top of the discovery source index.

    Never removes a pre-existing field: every consumer built against v1 keeps
    working (additive schema change, ``schemaVersion`` bumped to 2).
    """
    doc = _build_base_sources_doc(catalog, state)
    reputation_by_id = {item.get("id"): item for item in (reputation_doc or {}).get("sources", [])}
    reputation_by_source = {item.get("source"): item for item in (reputation_doc or {}).get("sources", [])}
    health_by_slug = {item.get("slug"): item for item in (health_doc or {}).get("apps", [])}
    verification_by_slug = {item.get("app"): item for item in (verification_doc or {}).get("apps", [])}

    identities = [str(entry.get("id") or entry.get("source") or "") for entry in doc.get("sources", [])]
    slugs = unique_slugs(identities)

    for entry in doc.get("sources", []):
        identity = str(entry.get("id") or entry.get("source") or "")
        rep = reputation_by_id.get(identity) or reputation_by_source.get(str(entry.get("source"))) or {}
        entry["slug"] = slugs[identity]
        entry["page"] = f"/sources/{entry['slug']}/"
        slugs_in: list[str] = []
        healths: list[float] = []
        cadences: list[float] = []
        verified = 0
        last_update = ""
        for app in entry.get("apps", []):
            slug = str(app.get("slug") or "")
            slugs_in.append(slug)
            entry_health = health_by_slug.get(slug) or {}
            raw_health = entry_health.get("healthScore")
            if raw_health is None:
                raw_health = (entry_health.get("scoreBreakdown") or {}).get("total") or entry_health.get("score")
            healths.append(float(raw_health or 0))
            if (verification_by_slug.get(slug) or {}).get("status") == "VERIFIED":
                verified += 1
            dates = version_dates(state, slug)
            if dates:
                last_update = max(last_update, max(dates).isoformat())
            gap = (rep.get("metrics") or {}).get("averageUpdateGapDays")
            if isinstance(gap, (int, float)):
                cadences.append(float(gap))
        entry["appSlugs"] = slugs_in
        entry["appCount"] = len(slugs_in)
        entry["verifiedApps"] = verified
        entry["healthScore"] = _average(healths)
        entry["score"] = rep.get("score")
        entry["status"] = rep.get("status") or "Maintained"
        entry["level"] = rep.get("level")
        entry["updateFrequencyDays"] = _average(cadences)
        entry["lastUpdate"] = last_update or None
        # AltStore feed URL when one source maps to exactly one app feed.
        if len(slugs_in) == 1:
            entry["feedURL"] = f"{catalog.base_url.rstrip('/')}/feeds/{slugs_in[0]}.json"

    doc["schemaVersion"] = 2
    counts: dict[str, int] = {}
    for entry in doc.get("sources", []):
        key = str(entry.get("status"))
        counts[key] = counts.get(key, 0) + 1
    doc["statuses"] = counts
    return doc


# ---------------------------------------------------------------------------
# Static page rendering (mirrors the app-page chrome at ../../ depth)
# ---------------------------------------------------------------------------

_PAGE_STYLE = """
    .src-hero{display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap}
    .src-hero img{width:88px;height:88px;border-radius:22px;box-shadow:var(--shadow-md)}
    /* A flex item's automatic minimum size is its content's min-content size,
       so a long source name ("https://source.ryuksign.com/ig410") kept the text
       block wider than the phone viewport and the heading was clipped off the
       right edge (measured 353px wide at 320px). min-width:0 lets the block
       shrink and overflow-wrap lets an unbreakable URL break. */
    .src-hero > div{min-width:0;flex:1 1 220px}
    .src-hero h1,.src-hero p{overflow-wrap:anywhere}
    .src-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:26px 0}
    .src-meta .panel{padding:16px 18px;display:flex;flex-direction:column;gap:4px}
    .src-meta small{color:var(--muted);font:600 10.5px var(--font-mono);
      letter-spacing:.12em;text-transform:uppercase}
    .src-meta b{font-size:17px;letter-spacing:-.01em}
    /* min() so the track can never demand more than the container: at 320px a
       bare 300px minimum overflowed the padded main column. */
    .src-app-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr));gap:12px}
    .src-app{display:flex;gap:12px;padding:14px;border-radius:var(--radius-md);
      text-decoration:none;color:inherit;background:var(--surface);border:1px solid var(--line);
      transition:transform var(--dur-fast) var(--ease)}
    .src-app:hover,.src-app:focus-visible{transform:translateY(-2px)}
    .src-app img{width:52px;height:52px;border-radius:13px;object-fit:cover;flex:none}
    .src-app-name{display:block;font-weight:700}
    .src-app-meta{font-size:12px;color:var(--muted)}
"""

_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: https:; media-src 'self' data:; "
    "connect-src 'self' https:; font-src 'self' data: https://fonts.gstatic.com; "
    "form-action 'self'"
)


def _status_badge(status: str) -> str:
    cls = STATUS_BADGE_CLASS.get(status, "neutral")
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _head(*, base: str, title: str, description: str, page_url: str, json_ld: str) -> str:
    icon = f"{base}/assets/OmniSource.png"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en" data-theme="auto">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
            '  <meta name="color-scheme" content="light dark">',
            '  <meta name="theme-color" content="#e8eef8" media="(prefers-color-scheme: light)">',
            '  <meta name="theme-color" content="#07070f" media="(prefers-color-scheme: dark)">',
            '  <meta name="theme-color" content="#07070f" id="themeColor">',
            f"  <title>{html.escape(title)} — OmniSource</title>",
            f'  <meta name="description" content="{html.escape(description)}">',
            f'  <link rel="canonical" href="{html.escape(page_url)}">',
            '  <meta property="og:site_name" content="OmniSource">',
            f'  <meta property="og:title" content="{html.escape(title)} — OmniSource">',
            f'  <meta property="og:description" content="{html.escape(description)}">',
            '  <meta property="og:type" content="website">',
            f'  <meta property="og:url" content="{html.escape(page_url)}">',
            f'  <meta property="og:image" content="{html.escape(icon)}">',
            '  <meta name="twitter:card" content="summary">',
            f'  <meta name="twitter:title" content="{html.escape(title)} — OmniSource">',
            f'  <meta name="twitter:description" content="{html.escape(description)}">',
            f'  <meta name="twitter:image" content="{html.escape(icon)}">',
            '  <link rel="icon" type="image/png" href="../../assets/OmniSource.png">',
            '  <link rel="apple-touch-icon" href="../../assets/OmniSource.png">',
            '  <link rel="manifest" href="../../manifest.webmanifest">',
            f'  <meta http-equiv="Content-Security-Policy" content="{_CSP}">',
            '  <link rel="preload" href="../../assets/design-system/tokens.css" as="style">',
            '  <link rel="preload" href="../../assets/design-system/components.css" as="style">',
            '  <link rel="stylesheet" href="../../assets/design-system/tokens.css">',
            '  <link rel="stylesheet" href="../../assets/design-system/utilities.css">',
            '  <link rel="stylesheet" href="../../assets/design-system/animations.css">',
            '  <link rel="stylesheet" href="../../assets/design-system/components.css">',
            "  <style>" + _PAGE_STYLE + "  </style>",
            "  <script>",
            "    (function () {",
            "      try {",
            "        var t = localStorage.getItem('omnisource-theme');",
            "        if (t !== 'light' && t !== 'dark') t = 'auto';",
            "        document.documentElement.dataset.theme = t;",
            "      } catch (e) {}",
            "    })();",
            "  </script>",
            '  <script src="../../js/core.js" defer></script>',
            '  <script src="../../src/js/i18n.js" defer></script>',
            "  " + json_ld,
            "</head>",
        ]
    )


def _nav() -> str:
    """Simplified IA (Phase 2) rendered at ../../ depth like the app pages."""
    links = [
        ("../../", "nav.home", "Home", False),
        ("../../#catalog", "nav.apps", "Apps", False),
        ("../../collections/", "nav.collections", "Collections", False),
        ("../../sources/", "nav.sources", "Sources", True),
        ("../../status/", "nav.health", "Status", False),
        ("../../docs/", "nav.docs", "Docs", False),
    ]
    more = [
        ("../../analytics/", "nav.analytics", "Analytics"),
        ("../../compare/", "nav.compare", "Compare"),
        ("../../favorites/", "nav.favorites", "Favorites"),
        ("../../discover/", "nav.discover", "Discover"),
        ("../../api/community.json", "nav.community", "Community"),
    ]
    primary = "\n".join(
        f'        <a href="{href}"' + (' aria-current="page"' if current else "") + f' data-i18n="{key}">{label}</a>'
        for href, key, label, current in links
    )
    secondary = "\n".join(
        f'            <a href="{href}" role="menuitem" data-i18n="{key}">{label}</a>' for href, key, label in more
    )
    return "\n".join(
        [
            '  <a class="skip-link" href="#main">Skip to source details</a>',
            '  <header class="ap-header">',
            '    <nav class="ap-header-inner" aria-label="Source navigation">',
            '      <a class="brand" href="../../" aria-label="OmniSource home">',
            '        <img src="../../assets/OmniSource.png" alt="" width="32" height="32">',
            "        <span>OmniSource</span>",
            "      </a>",
            '      <div class="nav-links">',
            primary,
            '        <details class="nav-more">',
            '          <summary aria-haspopup="menu" aria-label="More pages"><span data-i18n="nav.more">More</span>',
            '            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
            "          </summary>",
            '          <div class="nav-menu" role="menu">',
            secondary,
            '            <a href="https://github.com/iamsmmh/OmniSource" target="_blank" rel="noopener" '
            'class="nav-gh" role="menuitem" data-i18n="nav.github">GitHub ↗</a>',
            "          </div>",
            "        </details>",
            "      </div>",
            '      <button class="icon-button theme-toggle" id="themeButton" type="button" '
            'aria-label="Change color theme" title="Theme: system" '
            'data-i18n-aria-label="a11y.theme" data-i18n-title="a11y.theme">',
            '        <svg class="icon-sun" aria-hidden="true" viewBox="0 0 24 24">'
            '<circle cx="12" cy="12" r="4.2"/>'
            '<path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.4 5.4l1.4 1.4M17.2 17.2l1.4 1.4'
            'M18.6 5.4l-1.4 1.4M6.8 17.2l1.4 1.4"/></svg>',
            '        <svg class="icon-moon" aria-hidden="true" viewBox="0 0 24 24">'
            '<path d="M20.4 14.2A8.6 8.6 0 0 1 9.8 3.6a8.6 8.6 0 1 0 10.6 10.6Z"/></svg>',
            "      </button>",
            "    </nav>",
            "  </header>",
        ]
    )


_I18N_MARKER = (
    "  <!-- i18n-keys: nav.home, nav.apps, nav.collections, nav.sources, nav.health, nav.docs, "
    "nav.more, nav.analytics, nav.compare, nav.favorites, nav.discover, nav.community, nav.github, "
    "a11y.theme, sources.page, sources.pageDescription, sources.maintainer, sources.feed, "
    "sources.frequency, sources.lastUpdate, sources.verification, sources.open -->"
)


def render_source_page(
    catalog: Any,
    source: dict[str, Any],
    apps_by_slug: dict[str, Any],
) -> str:
    """Render ``sources/<slug>/index.html`` for one source (Phase 4)."""
    name = str(source.get("source") or source.get("id") or "Source")
    slug = str(source.get("slug"))
    base = catalog.base_url.rstrip("/")
    page_url = f"{base}/sources/{slug}/"
    publisher = str(source.get("publisher") or name)
    source_url = str(source.get("sourceURL") or source.get("homepage") or "")
    status = str(source.get("status") or "Maintained")
    app_count = int(source.get("appCount") or len(source.get("apps") or []))
    verified = int(source.get("verifiedApps") or 0)
    health = source.get("healthScore")
    score = source.get("score")
    cadence = source.get("updateFrequencyDays")
    last_update = str(source.get("lastUpdate") or "")
    plural = "s" if app_count != 1 else ""
    # A host can publish several feeds under the same display name — both
    # source.ryuksign.com/duplicate and /ig410 call themselves "Ryuk / RyukSign
    # (source.ryuksign.com)" — which gave two different pages byte-identical
    # <title> and meta description. The feed's own path is the only thing that
    # tells them apart, so it is appended when the name does not already carry
    # it; sources on a bare host (AltStore) are untouched.
    label = name
    feed_path = urlsplit(source_url).path.strip("/")
    if feed_path:
        segment = feed_path.rsplit("/", 1)[-1]
        if segment and segment.lower() not in name.lower():
            label = f"{name} — {segment}"
    description = (
        f"{label} on OmniSource — {app_count} app{plural}, status {status}, "
        f"health {_fmt_score(health)}, reputation {_fmt_score(score)}."
    )
    json_ld = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": f"{label} — OmniSource source",
                "description": description,
                "url": page_url,
                "provider": {"@type": "Organization", "name": publisher},
            },
            ensure_ascii=False,
        )
        + "</script>"
    )

    rows = []
    for app in source.get("apps", []):
        app_slug = str(app.get("slug"))
        record = apps_by_slug.get(app_slug)
        icon = f"{base}/assets/{record.icon}" if record is not None else f"{base}/assets/OmniSource.png"
        version = str(app.get("version") or "")
        app_name = str(app.get("name") or app_slug)
        version_part = f"v{html.escape(version)} · " if version else ""
        rows.append(
            f'        <a class="src-app" href="../../apps/{html.escape(app_slug)}/">\n'
            f'          <img src="{html.escape(icon)}" alt="" width="52" height="52" loading="lazy" decoding="async">\n'
            "          <span>\n"
            f'            <span class="src-app-name">{html.escape(app_name)}</span>\n'
            f'            <span class="src-app-meta">{version_part}{html.escape(app_slug)}</span>\n'
            "          </span>\n"
            "        </a>"
        )
    apps_html = "\n".join(rows) or '        <p class="text-muted">No apps currently resolve from this source.</p>'
    if source_url:
        source_link = (
            f'<a href="{html.escape(source_url)}" target="_blank" rel="noopener">'
            f"<code>{html.escape(source_url)}</code></a>"
        )
    else:
        source_link = "—"

    parts = [
        _head(
            base=base,
            title=label,
            description=description,
            page_url=page_url,
            json_ld=json_ld,
        ),
        '<body data-page="source-detail">',
        _nav(),
        "",
        '  <main class="shell page-main" id="main" style="padding-top:40px">',
        '    <nav class="ap-breadcrumb" aria-label="Breadcrumb">',
        '      <a href="../../">Home</a> <span aria-hidden="true">/</span> '
        '<a href="../../sources/">Sources</a> <span aria-hidden="true">/</span> '
        f"<span>{html.escape(name)}</span>",
        "    </nav>",
        '    <section class="src-hero">',
        f'      <img src="{html.escape(base + "/assets/OmniSource.png")}" alt="" width="88" height="88">',
        "      <div>",
        '        <span class="kicker">SOURCE EXPLORER</span>',
        f'        <h1 style="margin:6px 0 8px;letter-spacing:var(--track-tighter)">{html.escape(label)}</h1>',
        f'        <p class="text-muted">{html.escape(description)}</p>',
        f'        <p style="margin-top:10px">{_status_badge(status)}</p>',
        "      </div>",
        "    </section>",
        '    <section class="src-meta" aria-label="Source metrics">',
        f'      <div class="panel"><small>Source</small><b>{html.escape(name)}</b></div>',
        f'      <div class="panel"><small>Maintainer</small><b>{html.escape(publisher)}</b></div>',
        f'      <div class="panel"><small>Source URL</small><b style="font-size:13px;'
        f'overflow-wrap:anywhere">{source_link}</b></div>',
        f'      <div class="panel"><small>Apps</small><b>{app_count}</b></div>',
        f'      <div class="panel"><small>Update frequency</small><b>{_fmt_days(cadence)}</b></div>',
        f'      <div class="panel"><small>Health score</small><b>{_fmt_score(health)}</b></div>',
        f'      <div class="panel"><small>Verification</small><b>{verified}/{app_count} verified</b></div>',
        f'      <div class="panel"><small>Reputation</small><b>{_fmt_score(score)}</b></div>',
        f'      <div class="panel"><small>Last update</small><b>{html.escape(last_update) or "—"}</b></div>',
        "    </section>",
        '    <h2 style="margin:34px 0 12px">Apps from this source</h2>',
        f'    <div class="src-app-grid">\n{apps_html}\n    </div>',
        '    <p class="mt-4"><a class="button small" href="../../status/">Open the health center →</a> '
        '<a class="button small" href="../../sources/">All sources →</a></p>',
        "  </main>",
        _I18N_MARKER,
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def render_sources_table(catalog: Any, sources_doc: dict[str, Any]) -> str:
    """Server-rendered fallback table for /sources/ (no-JS + crawler content).

    The committed ``sources/index.html`` wraps it in a ``<noscript>`` block and
    in a static ``#src-static`` panel so crawlers index every source even
    though the interactive explorer replaces it client-side.
    """
    rows = []
    for source in sources_doc.get("sources", []):
        name = str(source.get("source") or source.get("id"))
        slug = str(source.get("slug"))
        status = str(source.get("status") or "Maintained")
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(slug)}/">{html.escape(name)}</a></td>'
            f"<td>{int(source.get('appCount') or 0)}</td>"
            f"<td>{_fmt_score(source.get('score'))}</td>"
            f"<td>{_status_badge(status)}</td>"
            f"<td>{html.escape(str(source.get('lastUpdate') or '—'))}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="text-muted">Sources are being indexed.</p>'
    return "\n".join(
        [
            '<table class="table">',
            "  <thead><tr><th>Source</th><th>Apps</th><th>Reputation</th><th>Status</th>"
            "<th>Last update</th></tr></thead>",
            "  <tbody>",
            "    " + "\n    ".join(rows),
            "  </tbody>",
            "</table>",
        ]
    )


def build_source_pages(
    catalog: Any,
    sources_doc: dict[str, Any],
    *,
    pages_dir: Path,
) -> list[Path]:
    """Write one ``sources/<slug>/index.html`` per source (Phase 4)."""
    apps_by_slug = {app.slug: app for app in catalog.apps}
    changed: list[Path] = []
    expected: set[str] = set()
    for source in sources_doc.get("sources", []):
        slug = str(source.get("slug"))
        expected.add(slug)
        target = pages_dir / slug / "index.html"
        if atomic_write_text(target, render_source_page(catalog, source, apps_by_slug)):
            changed.append(target)
    # Prune pages of sources that left the catalog (no orphaned routes).
    if pages_dir.is_dir():
        for child in sorted(pages_dir.iterdir()):
            if child.is_dir() and child.name not in expected and (child / "index.html").is_file():
                shutil.rmtree(child, ignore_errors=True)
                changed.append(child)
    return changed
