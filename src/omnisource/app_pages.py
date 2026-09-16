"""Static app detail page generator.

Every catalog app gets a fully static, search-engine friendly page at
``apps/<slug>/index.html``. Pages are generated from the same data as the
feeds — catalog metadata, pipeline state (version history), health and
verification documents — and are never hand-edited. The Pages builder copies
the ``apps/`` directory into the deployed site, so each app has a permanent
URL: ``https://iamsmmh.github.io/OmniSource/apps/<slug>/``.

Pages are self-contained (shared design-system CSS, ``js/core.js`` for
theme / clipboard / search palette / QR dialog / service worker,
``src/js/i18n.js`` + ``js/features.js`` for localization and favorites, and a
Content-Security-Policy meta tag matching the landing page) and link back to the
landing page, per-app feed, RSS and direct download, so they work with or
without JavaScript. The visual language is App Store-style: a tinted glass
hero, a capsule "Get" button, numbered sections and trust checklist.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from omnisource.discovery import newest_version, source_label
from omnisource.domain import Catalog
from omnisource.duplicates import group_for_app
from omnisource.install import install_url
from omnisource.io import atomic_write_text

# Content-Security-Policy served on every app page via <meta>. Keep in sync
# with the landing page and section pages (index.html, */index.html):
# same-origin scripts/styles (inline allowed for the theme bootstrap),
# images/data from self + https, API calls to self + https.
CSP_DIRECTIVES = (
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "img-src 'self' data: https:",
    "media-src 'self' data:",
    "connect-src 'self' https:",
    "font-src 'self' data: https://fonts.gstatic.com",
    "form-action 'self'",
)


def _fmt_bytes(size: int) -> str:
    if not size or size < 0:
        return "Unknown"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    text = f"{value:.1f}" if value >= 10 or unit in ("MB", "GB") else f"{value:.0f}"
    return f"{text} {unit}"


def _fmt_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return parsed.strftime("%b %d, %Y")
    except ValueError:
        return str(value or "Unknown")


def _badge(cls: str, label: str) -> str:
    return f'<span class="badge {html.escape(cls)}">{html.escape(label)}</span>'


# Same trust styling as the website (js/site.js verificationBadgeClass).
_VERIFICATION_BADGES = {
    "VERIFIED": ("verified", "✓ Verified"),
    "COMMUNITY VERIFIED": ("community", "Community verified"),
    "UNVERIFIED": ("unverified", "Unverified"),
}


def _badges(status: str, health_ok: bool, verification_level: str, stale: bool, provenance: str = "official") -> str:
    provenance_badge = (
        '<span class="badge verified" title="IPA published by the project&#39;s '
        'own upstream release channel">Official build</span>'
        if provenance == "official"
        else '<span class="badge community" title="Repackaged or mirrored by a '
        'community builder — see Trust &amp; provenance">Community build</span>'
    )
    parts = [
        _badge("stable" if status == "stable" else status, status.title()),
        provenance_badge,
        (
            '<span class="badge ok"><span class="dot"></span>Online</span>'
            if health_ok
            else '<span class="badge bad"><span class="dot"></span>Offline</span>'
        ),
        _badge(*_VERIFICATION_BADGES.get(verification_level, ("unverified", verification_level))),
    ]
    if stale and status != "unmaintained":
        parts.append(_badge("warn", "Stale release"))
    return "".join(parts)


def _client_buttons(catalog: Catalog, feed_url: str) -> str:
    buttons: list[str] = []
    for client in catalog.clients:
        client_id = str(client.get("id") or "")
        name = str(client.get("name") or client_id.title())
        icon = str(client.get("icon") or "")
        if icon:
            image = f'<img src="../../assets/{html.escape(icon)}" alt="" width="20" height="20" loading="lazy">'
        else:
            image = f'<span class="cli-fallback">{html.escape(name[:1].upper())}</span>'
        url = install_url(client_id, feed_url)
        if url:
            buttons.append(
                f'<a class="button client-button" href="{html.escape(url)}" '
                f'title="Add to {html.escape(name)}">{image}{html.escape(name)}</a>'
            )
        else:
            buttons.append(
                f'<button class="button client-button" type="button" '
                f'data-copy="{html.escape(feed_url)}" data-copy-msg="URL copied — paste it in {html.escape(name)}" '
                f'title="Open {html.escape(name)} and paste the source URL">{image}{html.escape(name)}</button>'
            )
    return "".join(buttons)


def _version_rows(versions: list[dict[str, Any]]) -> str:
    if not versions:
        return '<p class="ap-desc">No version history is published yet.</p>'
    rows: list[str] = []
    for index, version in enumerate(versions[:12]):
        notes = str(version.get("localizedDescription") or "")
        current = '<span class="tag">Current</span>' if index == 0 else ""
        sha = str(version.get("sha256") or "")
        sha_html = (
            f'<code title="SHA-256">{html.escape(sha[:16])}…</code>' if sha else ("<span>No checksum published</span>")
        )
        download = str(version.get("downloadURL") or "")
        if download:
            download_link = (
                f'<a class="button small" href="{html.escape(download)}" target="_blank" rel="noopener">Download</a>'
            )
        else:
            download_link = ""
        number = html.escape(str(version.get("version") or "—"))
        published = html.escape(_fmt_date(str(version.get("date") or "")))
        size = html.escape(_fmt_bytes(int(version.get("size") or 0)))
        if notes:
            notes_html = (
                f'<details class="ap-changelog"><summary>Release notes</summary>'
                f'<pre class="ap-notes">{html.escape(notes[:4000])}</pre></details>'
            )
        else:
            notes_html = ""
        rows.append(
            f"""<article class="ap-version">
  <div class="ap-version-head"><span class="v">v{number}</span>{current}
    <span class="d">{published}</span></div>
  <div class="meta"><span>{size}</span>{sha_html}{download_link}</div>
  {notes_html}
</article>"""
        )
    return f'<div class="ap-version-list">{"".join(rows)}</div>'


def _checks_html(checks: dict[str, bool]) -> str:
    if not checks:
        return '<p class="ap-desc">No individual checks are published for this app.</p>'
    cells = []
    for key, ok in checks.items():
        state = "pass" if ok else "fail"
        icon = '<path d="m5 12 4 4L19 6"/>' if ok else '<path d="m9 9 6 6m0-6-6 6"/>'
        cells.append(
            f'<li class="ap-check {state}"><span class="check-ico">'
            f'<svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg></span>'
            f"<span>{html.escape(key)}</span><small>{state}</small></li>"
        )
    return f'<ul class="ap-checks">{"".join(cells)}</ul>'


def _install_cards_html(install_doc: dict[str, Any] | None, slug: str) -> str:
    """Render the per-client install card row for a given app slug."""
    if not install_doc:
        return ""
    app_entry = next(
        (item for item in install_doc.get("apps", []) if item.get("slug") == slug),
        None,
    )
    if not app_entry:
        return ""
    cards = app_entry.get("cards", [])
    if not cards:
        return ""
    items: list[str] = []
    for card in cards:
        name = html.escape(str(card.get("name") or ""))
        url = html.escape(str(card.get("url") or ""))
        icon = html.escape(str(card.get("icon") or ""))
        recommended = bool(card.get("recommended"))
        manual = bool(card.get("manualSetup"))
        if recommended:
            flag = '<span class="card-flag flag-recommended">Recommended</span>'
        elif manual:
            flag = '<span class="card-flag flag-manual">Manual setup</span>'
        else:
            flag = '<span class="card-flag flag-compatible">Compatible</span>'
        icon_html = f'<img src="../../assets/{icon}" alt="" width="30" height="30" loading="lazy">' if icon else ""
        body = (
            f'<div style="min-width:0"><b>{name}</b>{flag}'
            f"<small>{html.escape(str(card.get('instructions') or ''))}</small></div>"
        )
        if url:
            items.append(f'<a class="ap-install-card" href="{url}" title="Open in {name}">{icon_html}{body}</a>')
        else:
            items.append(
                f'<button class="ap-install-card" type="button" '
                f'data-copy="{html.escape(str(card.get("feedURL") or ""))}" '
                f'title="Open {name} and paste the URL">{icon_html}{body}</button>'
            )
    return '<div class="ap-install-grid">' + "".join(items) + "</div>"


def _related_html(related_doc: dict[str, Any] | None, slug: str) -> str:
    """Render the related-apps strip for a given app slug."""
    if not related_doc:
        return ""
    related = related_doc.get("related", {}).get(slug) or []
    if not related:
        return ""
    items: list[str] = []
    for entry in related[:5]:
        target_slug = html.escape(str(entry.get("slug") or ""))
        name = html.escape(str(entry.get("name") or ""))
        reason = html.escape(", ".join(entry.get("reasons") or []))
        score = entry.get("score") or 0
        items.append(
            f'<a class="ap-related-card" href="../{target_slug}/" title="{reason}">'
            f'<span class="score-pill">{round(float(score) * 100)}</span>'
            f'<div class="ap-related-name">{name}</div>'
            f'<div class="ap-related-reason">{reason}</div>'
            f"</a>"
        )
    return '<div class="ap-related-grid">' + "".join(items) + "</div>"


def _detail_cells(
    app: Any,
    newest: dict[str, Any],
    health: dict[str, Any],
    verification_level: str,
    version_count: int,
    provenance: str = "official",
) -> str:
    version = html.escape(str(newest.get("version") or "—"))
    updated_at = html.escape(_fmt_date(str(newest.get("date") or "")))
    size = html.escape(_fmt_bytes(int(newest.get("size") or 0)))
    required_os = html.escape(app.minimum_ios_version or "Not listed")
    category = html.escape(str(app.category or "other").title())
    developer = html.escape(app.developer or "—")
    bundle = html.escape(app.bundle_id or "—")
    checksum = html.escape(str(newest.get("sha256") or "Not published")[:20])
    health_text = html.escape("Online" if health.get("downloadReachable") else "Unavailable")
    verification = html.escape(verification_level)
    source = html.escape(source_label(app))
    source_url = html.escape((app.source_url or "").strip())
    source_value = (
        f'<a class="ap-source-link" href="{source_url}" target="_blank" rel="noopener">{source}</a>'
        if source_url
        else source
    )
    cells = [
        ("Version", f"v{version}"),
        ("Updated", updated_at),
        ("Size", size),
        ("Requires iOS", required_os),
        ("Category", category),
        ("Developer", developer),
        ("Bundle ID", f"<code>{bundle}</code>"),
        ("Verification", verification),
        ("Build", "Official build" if provenance == "official" else "Community build"),
        ("Checksum", f"<code>{checksum}</code>"),
        ("Health", health_text),
        ("Version history", str(version_count)),
        ("Source", source_value),
    ]
    return "".join(
        f'<div class="ap-cell"><span>{html.escape(label)}</span><strong>{value}</strong></div>'
        for label, value in cells
    )


def _duplicate_banner(app: Any, duplicate: dict[str, Any] | None, feed_url: str = "") -> str:
    if not duplicate:
        return ""
    recommended = duplicate.get("recommended") if isinstance(duplicate.get("recommended"), dict) else {}
    target = recommended.get("app") or ""
    reason = html.escape(str(duplicate.get("reason") or ""))
    recommendation = (
        f"Recommended: <b>{html.escape(str(recommended.get('name') or target))}</b> — "
        f"{html.escape(str(recommended.get('reason') or 'Newest version available'))}"
    )
    if target and target != app.slug:
        recommendation += f' <a href="../{html.escape(target)}/">Open {html.escape(target)} →</a>'
    # Bundle-ID collisions: clients like SideStore cannot keep two apps that
    # share a bundle ID from the master feed. The per-app feed adds this one
    # app on its own, so offer a one-click copy of that source URL.
    bundle_collision = str(duplicate.get("type") or "").startswith("bundle-id")
    collision_hint = (
        "<p>Clients that identify apps by bundle ID (e.g. SideStore) cannot install these "
        "side by side — adding the new one replaces the old. To add <b>only this app</b> "
        "manually, use its single-app source: "
        f'<button class="button small" type="button" data-copy="{html.escape(feed_url)}" '
        'data-copy-msg="Single-app source copied — add it manually in your '
        'client">Copy single-app source link</button></p>'
        if bundle_collision and feed_url
        else ""
    )
    return f"""<div class="ap-alert" role="note">
  <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none"
    stroke="currentColor" stroke-width="2"><path d="M12 3 2.8 20h18.4L12 3Zm0 6v5m0 3.2v.1"/></svg>
  <div>
    <b>Similar apps detected in the catalog.</b>
    <p>{reason} · {recommendation}</p>
    {collision_hint}
  </div>
</div>"""


def _head(
    title: str,
    sub: str,
    icon_url: str,
    page_url: str,
    rss_url: str,
    app: Any,
    newest: dict[str, Any],
    download_url: str,
    publisher: str,
    screenshots: tuple[str, ...] = (),
) -> str:
    version_text = html.escape(str(newest.get("version") or ""))
    # schema.org wants a *screenshot* here. The app icon is not one, and emitting it
    # as if it were puts a rendered stand-in into every search index that reads this
    # page - so the field is present only when upstream actually publishes art.
    shots = ", ".join(json_quote(url) for url in screenshots)
    screenshot_field = f'"screenshot": [{shots}],' + "\n      " if screenshots else ""
    return f"""<!doctype html>
<html lang="en" data-theme="auto">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#e8eef8" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#07070f" media="(prefers-color-scheme: dark)">
  <meta name="theme-color" content="#07070f" id="themeColor">
  <title>{title} — OmniSource</title>
  <meta name="description" content="{sub}">
  <link rel="canonical" href="{html.escape(page_url)}">
  <meta property="og:site_name" content="OmniSource">
  <meta property="og:title" content="{title} — OmniSource">
  <meta property="og:description" content="{sub}">
  <meta property="og:image" content="{html.escape(icon_url)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{html.escape(page_url)}">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{title} — OmniSource">
  <meta name="twitter:description" content="{sub}">
  <meta name="twitter:image" content="{html.escape(icon_url)}">
  <link rel="icon" type="image/png" href="../../assets/OmniSource.png">
  <link rel="apple-touch-icon" href="../../assets/OmniSource.png">
  <link rel="alternate" type="application/rss+xml" title="{title} releases" href="{html.escape(rss_url)}">
  <!-- Content Security Policy: same-origin scripts/styles (inline allowed for the
       theme bootstrap), images/data from self + https, API calls to self + https.
       All dynamic markup is escaped via OS.esc. -->
  <meta http-equiv=\"Content-Security-Policy\" content=\"{"; ".join(CSP_DIRECTIVES)}\">
  <link rel="preload" href="../../assets/design-system/tokens.css" as="style">
  <link rel="preload" href="../../assets/design-system/components.css" as="style">
  <link rel="stylesheet" href="../../assets/design-system/tokens.css">
  <link rel="stylesheet" href="../../assets/design-system/utilities.css">
  <link rel="stylesheet" href="../../assets/design-system/animations.css">
  <link rel="stylesheet" href="../../assets/design-system/components.css">
  <script>
    (function () {{
      try {{
        var t = localStorage.getItem('omnisource-theme');
        if (t !== 'light' && t !== 'dark') t = 'auto';
        document.documentElement.dataset.theme = t;
      }} catch (e) {{}}
    }})();
  </script>
  <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      "name": {json_quote(app.name)},
      "applicationCategory": "UtilitiesApplication",
      "operatingSystem": "iOS",
      "softwareVersion": {json_quote(version_text)},
      "url": {json_quote(page_url)},
      "downloadUrl": {json_quote(download_url)},
      {screenshot_field}      "author": {{ "@type": "Organization", "name": {json_quote(publisher)} }}
    }}
  </script>
</head>
"""


def json_quote(value: str) -> str:
    """JSON string literal (double-quoted, escaped) for inline JSON-LD."""
    return json.dumps(str(value), ensure_ascii=False)


def render_app_page(
    catalog: Catalog,
    app: Any,
    state: dict[str, Any],
    health_doc: dict[str, Any],
    verification_doc: dict[str, Any],
    duplicates_doc: dict[str, Any],
    related_doc: dict[str, Any] | None = None,
    install_doc: dict[str, Any] | None = None,
) -> str:
    """Render one app detail page as an HTML string."""
    base = catalog.base_url.rstrip("/")
    repo_url = str(catalog.source.get("repository") or "https://github.com/iamsmmh/OmniSource")
    app_state = state.get(app.slug) if isinstance(state.get(app.slug), dict) else {}
    versions = app_state.get("versions") if isinstance(app_state.get("versions"), list) else []
    newest = newest_version(state, app.slug)
    health_item = next(
        (item for item in health_doc.get("apps", []) if item.get("slug") == app.slug),
        {},
    )
    verification_item = next(
        (item for item in verification_doc.get("apps", []) if item.get("app") == app.slug),
        {"status": "UNVERIFIED", "checks": {}, "reasons": []},
    )
    verification_level = str(verification_item.get("status") or "UNVERIFIED")
    provenance = str(verification_item.get("provenance") or "official")
    duplicate = group_for_app(duplicates_doc, app.slug)

    icon_url = f"{base}/assets/{app.icon}"
    feed_url = f"{base}/feeds/{app.slug}.json"
    rss_url = f"{base}/feeds/{app.slug}.xml"
    page_url = f"{base}/apps/{app.slug}/"
    download_url = str(newest.get("downloadURL") or app.raw.get("downloadURL") or "")
    screenshots = [url for url in app.screenshots if str(url).startswith(("http://", "https://"))]
    publisher = str(app.raw.get("verification", {}).get("publisher") or app.developer)
    publisher_html = html.escape(publisher)
    source_url = (app.source_url or "").strip()
    # "Published by" describes who publishes the sideload IPA - link it to the
    # source when the source is not the same page as Upstream.
    if source_url and source_url != (app.repository_url or ""):
        publisher_html = (
            f'<a class="ap-source-link" href="{html.escape(source_url)}" target="_blank" rel="noopener">'
            f"{publisher_html}</a>"
        )
    compatibility = app.raw.get("compatibility")
    source_notes = str(compatibility.get("notes") or "") if isinstance(compatibility, dict) else ""
    fallbacks = newest.get("fallbackDownloadURLs") or app.raw.get("fallbackDownloadURLs") or []
    fallbacks = [url for url in fallbacks if isinstance(url, str) and url.startswith(("http://", "https://"))]
    checks = verification_item.get("checks") if isinstance(verification_item.get("checks"), dict) else {}
    reasons = verification_item.get("reasons") if isinstance(verification_item.get("reasons"), list) else []

    title = html.escape(app.name)
    sub = html.escape(app.short_description or app.description or f"{app.name} on OmniSource")
    description = html.escape(app.description or "No description provided.")
    category_title = str(app.category or "other").title()
    extra_tags = [tag for tag in app.tags if tag != app.category]
    tagline = f"{category_title} · {' · '.join(extra_tags)}" if extra_tags else category_title
    version_text = html.escape(str(newest.get("version") or "—"))
    size_text = html.escape(_fmt_bytes(int(newest.get("size") or 0)))
    tint = str(app.raw.get("tintColor") or "")
    tint_style = f' style="--tint:#{html.escape(tint)}"' if tint else ""
    screenshots_html = "".join(
        f'<img src="{html.escape(url)}" alt="{title} screenshot {index}" loading="lazy">'
        for index, url in enumerate(screenshots, start=1)
    )
    if screenshots:
        screenshots_block = f'<div class="ap-screenshots">{screenshots_html}</div>\n'
    else:
        # An honest gap beats a stand-in: these apps publish no screenshots upstream,
        # and a mockup rendered from the icon is not information about the app.
        screenshots_block = (
            '<p class="ap-no-screenshots">Upstream publishes no screenshots for this '
            "app, and OmniSource does not invent them.</p>\n"
        )
    fallback_html = "".join(
        f'<a class="button" href="{html.escape(url)}" target="_blank" rel="noopener">Mirror {index}</a>'
        for index, url in enumerate(fallbacks, start=1)
    )
    reasons_html = "".join(f"<li>{html.escape(reason)}</li>" for reason in reasons)
    method_text = html.escape(str(app.raw.get("verification", {}).get("method") or "upstream source").replace("-", " "))
    notes_html = html.escape(source_notes)
    upstream_url = html.escape(app.repository_url or "")
    source_url = html.escape(app.source_url or "")
    # When the sideload IPA is published by a different repo/feed than the
    # official project page, surface that source link right next to Upstream.
    source_button = (
        f'        <a class="button" href="{source_url}" target="_blank" rel="noopener" '
        'title="The repo/feed that publishes this sideload IPA">Source</a>\n'
        if source_url and source_url != upstream_url
        else ""
    )

    head = _head(title, sub, icon_url, page_url, rss_url, app, newest, download_url, publisher, screenshots)

    hero = [
        '<body class="app-page">\n',
        '  <a class="skip-link" href="#main">Skip to app details</a>\n',
        '  <header class="ap-header">\n',
        '    <nav class="ap-header-inner" aria-label="App navigation">\n',
        '      <a class="brand" href="../../" aria-label="OmniSource home">\n',
        '        <img src="../../assets/OmniSource.png" alt="" width="32" height="32">\n',
        "        <span>OmniSource</span>\n",
        "      </a>\n",
        '      <div class="nav-links">\n',
        '        <a href="../../" data-i18n="nav.home">Home</a>\n',
        '        <a href="../../#catalog" data-i18n="nav.apps">Apps</a>\n',
        '        <a href="../../collections/" data-i18n="nav.collections">Collections</a>\n',
        '        <a href="../../sources/" data-i18n="nav.sources">Sources</a>\n',
        '        <a href="../../status/" data-i18n="nav.health">Status</a>\n',
        '        <a href="../../docs/" data-i18n="nav.docs">Docs</a>\n',
        '        <details class="nav-more">\n',
        '          <summary aria-haspopup="menu" aria-label="More pages"><span data-i18n="nav.more">More</span>\n',
        '            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>\n',
        "          </summary>\n",
        '          <div class="nav-menu" role="menu">\n',
        '            <a href="../../#trending" role="menuitem" data-i18n="nav.trending">Trending</a>\n',
        '            <a href="../../analytics/" role="menuitem" data-i18n="nav.analytics">Analytics</a>\n',
        '            <a href="../../compare/" role="menuitem" data-i18n="nav.compare">Compare</a>\n',
        '            <a href="../../favorites/" role="menuitem" data-i18n="nav.favorites">Favorites</a>\n',
        '            <a href="../../discover/" role="menuitem" data-i18n="nav.discover">Discover</a>\n',
        '            <a href="../../api/community.json" role="menuitem" data-i18n="nav.community">Community</a>\n',
        '            <a href="../../search/" role="menuitem" data-i18n="nav.search">Search</a>\n',
        '            <a href="'
        + f"{html.escape(rss_url)}"
        + '" target="_blank" rel="noopener" role="menuitem">RSS</a>\n',
        '            <a href="'
        + f"{html.escape(feed_url)}"
        + '" target="_blank" rel="noopener" role="menuitem">'
        + "This app&rsquo;s feed</a>\n",
        '            <a href="'
        + f"{html.escape(repo_url)}"
        + '" target="_blank" rel="noopener" class="nav-gh" '
        + 'role="menuitem" data-i18n="nav.github">GitHub ↗</a>\n',
        "          </div>\n",
        "        </details>\n",
        "      </div>\n",
        '      <button class="icon-button theme-toggle" id="themeButton" type="button" '
        'aria-label="Change color theme" title="Theme: system" '
        'data-i18n-aria-label="a11y.theme" data-i18n-title="a11y.theme">\n',
        '        <svg class="icon-sun" aria-hidden="true" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="4.2"/>'
        '<path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.4 5.4l1.4 1.4M17.2 17.2l1.4 1.4'
        'M18.6 5.4l-1.4 1.4M6.8 17.2l1.4 1.4"/></svg>\n',
        '        <svg class="icon-moon" aria-hidden="true" viewBox="0 0 24 24">'
        '<path d="M20.4 14.2A8.6 8.6 0 0 1 9.8 3.6a8.6 8.6 0 1 0 10.6 10.6Z"/></svg>\n',
        "      </button>\n",
        "    </nav>\n",
        "  </header>\n\n",
        '  <main class="ap-main" id="main">\n',
        '    <nav class="ap-breadcrumb" aria-label="Breadcrumb">\n',
        '      <a href="../../">Home</a> <span aria-hidden="true">/</span> <span>' + title + "</span>\n",
        "    </nav>\n",
        '    <section class="ap-hero"' + tint_style + ">\n",
        '      <div class="ap-hero-tint" aria-hidden="true"></div>\n',
        f'      <img class="ap-icon" src="{html.escape(icon_url)}" alt="{title} icon" '
        'width="112" height="112" fetchpriority="high">\n',
        "      <div>\n",
        f'        <span class="ap-kicker">{html.escape(tagline)}</span>\n',
        f'        <h1 class="ap-title">{title}</h1>\n',
        f'        <p class="ap-sub">{sub}</p>\n',
        f'        <p class="ap-dev">by {html.escape(app.developer or "Unknown developer")}</p>\n',
        '        <div class="ap-badges">\n',
        _badges(
            app.status,
            bool(health_item.get("downloadReachable", True)),
            verification_level,
            bool(health_item.get("stale")),
            provenance,
        ),
        "</div>\n",
        "      </div>\n",
        _duplicate_banner(app, duplicate, feed_url),
        '      <div class="ap-install">\n',
        f'        <a class="get-capsule" href="{html.escape(download_url)}" target="_blank" rel="noopener">'
        f'Get · v{version_text}<span class="capsule-size">{size_text}</span></a>\n',
        _client_buttons(catalog, feed_url),
        '        <button class="button ap-copy" type="button" '
        f'data-copy="{html.escape(feed_url)}" data-copy-msg="Source URL copied">Copy source URL</button>\n',
        f'        <button class="button square" type="button" id="qrButton" '
        f'data-qr-feed="{html.escape(feed_url)}" '
        'title="Show QR code" aria-label="Show QR code for the source URL">\n',
        '          <svg aria-hidden="true" viewBox="0 0 24 24">'
        '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4z"/>'
        '<path d="M15 14h2v2h-2zM19 14h1.4v3.4H19zM14 19h3.4v1.4H14zM20 19.6V20h-1.4"/></svg>\n',
        "        </button>\n",
        "      </div>\n",
        "    </section>\n",
    ]

    sections = [
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">01</span> About</h2>\n',
        f'      <p class="ap-desc">{description}</p>\n',
        f"      {screenshots_block}",
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">02</span> Install with</h2>\n',
        '      <p class="ap-desc">Pick your client. Links are generated from the '
        "catalog on every build — never hard-coded.</p>\n",
        _install_cards_html(install_doc, app.slug),
        '      <p class="mt-3"><a class="button small" href="../../install/">Open the installation center →</a></p>\n',
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">03</span> Release notes</h2>\n',
        f"      {_version_rows(versions)}\n",
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">04</span> Details</h2>\n',
        '      <div class="ap-detail-grid">\n',
        _detail_cells(app, newest, health_item, verification_level, len(versions), provenance),
        "</div>\n",
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">05</span> Trust &amp; provenance</h2>\n',
        f"      {_checks_html(checks)}\n",
        (
            '      <p class="ap-desc mt-3"><b>Official build.</b> The IPA is published by the '
            "project&rsquo;s own upstream release channel.</p>\n"
            if provenance == "official"
            else '      <p class="ap-desc mt-3"><b>Community build.</b> The IPA is repackaged or '
            "mirrored by a community builder; the tweak or app itself is developed upstream.</p>\n"
        ),
        '      <p class="ap-desc">Published by ' + f"{publisher_html} · {method_text}.</p>\n",
        f"      <ul>{reasons_html}</ul>\n",
        (
            f'      <div class="detail-note mt-3"><b>Compatibility notes:</b> {notes_html}</div>\n'
            if source_notes
            else ""
        ),
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">06</span> Related apps</h2>\n',
        f'      <p class="ap-desc">Apps that share a bundle, category or developer with {html.escape(app.name)}.</p>\n',
        _related_html(related_doc, app.slug),
        "    </section>\n\n",
        '    <section class="ap-section" data-reveal>\n',
        '      <h2><span class="num">07</span> Downloads</h2>\n',
        '      <div class="ap-links">\n',
        f'        <a class="button primary" href="{html.escape(download_url)}" '
        'target="_blank" rel="noopener">Primary IPA</a>\n',
        f"        {fallback_html}\n",
        f'        <a class="button" href="{html.escape(feed_url)}" target="_blank" rel="noopener">App feed</a>\n',
        f'        <a class="button" href="{html.escape(rss_url)}" target="_blank" rel="noopener">App RSS</a>\n',
        source_button,
        f'        <a class="button" href="{upstream_url}" target="_blank" rel="noopener">Upstream</a>\n',
        f'        <a class="button" href="{html.escape(base)}/feeds/discovery.json" '
        'target="_blank" rel="noopener">Discovery catalog</a>\n',
        f'        <a class="button" href="{html.escape(base)}/compare/?left={html.escape(app.slug)}" '
        'target="_blank" rel="noopener">Compare with another app</a>\n',
        "      </div>\n",
        "    </section>\n",
        "  </main>\n\n",
        '  <footer class="ap-footer">\n',
        '    <div class="ap-footer-inner">\n',
        f"      <span>v{version_text} of {title} · refreshed automatically.</span>\n",
        '      <a href="../../#catalog">← Back to catalog</a>\n',
        "      <span>Independent community project. Apps and trademarks belong to their respective owners.</span>\n",
        "    </div>\n",
        "  </footer>\n\n",
        '  <dialog id="qrDialog" class="qr-dialog os-dialog" aria-labelledby="qrTitle">\n',
        '    <button class="dialog-close" type="button" data-close aria-label="Close" '
        'data-i18n-aria-label="common.close">'
        '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button>\n',
        '    <span class="kicker" data-i18n="dialog.scanToAdd">SCAN TO ADD</span>\n',
        f'    <h2 id="qrTitle">{title} — source feed</h2>\n',
        '    <div class="qr-image"><img id="qrImage" width="240" height="240" alt="QR code for the app feed" '
        'loading="lazy" decoding="async" data-i18n-alt="dialog.qrAlt"></div>\n',
        f"    <code>{html.escape(feed_url)}</code>\n",
        '    <button class="button primary full" type="button" data-i18n="dialog.copyUrl" data-copy="'
        + html.escape(feed_url)
        + '">Copy URL</button>\n',
        "  </dialog>\n\n",
        '  <div class="toast" id="toast" role="status" aria-live="polite">\n',
        '    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg><span></span>\n',
        "  </div>\n\n",
        '  <script src="../../js/core.js" defer></script>\n',
        '  <script src="../../src/js/i18n.js" defer></script>\n',
        '  <script src="../../js/features.js" defer></script>\n',
        '  <script src="../../website/assets/AssetManager.js" defer></script>\n',
        "</body>\n",
        "</html>\n",
    ]
    return head + "".join(hero + sections)


def build_app_pages(
    catalog: Catalog,
    state: dict[str, Any],
    health_doc: dict[str, Any],
    verification_doc: dict[str, Any],
    duplicates_doc: dict[str, Any],
    *,
    pages_dir: Path,
    related_doc: dict[str, Any] | None = None,
    install_doc: dict[str, Any] | None = None,
) -> list[Path]:
    """Render every app page and remove pages for apps that left the catalog."""
    changed: list[Path] = []
    for app in catalog.apps:
        page = pages_dir / app.slug / "index.html"
        content = render_app_page(
            catalog,
            app,
            state,
            health_doc,
            verification_doc,
            duplicates_doc,
            related_doc=related_doc,
            install_doc=install_doc,
        )
        if atomic_write_text(page, content):
            changed.append(page)

    # Clean up stale pages so removed apps never stay published.
    valid = {app.slug for app in catalog.apps}
    if pages_dir.is_dir():
        for child in sorted(pages_dir.iterdir()):
            if child.is_dir() and child.name not in valid:
                shutil.rmtree(child, ignore_errors=True)
                changed.append(child)
    return changed
