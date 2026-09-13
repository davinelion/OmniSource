"""RSS 2.0 and Atom feed generator for release updates.

Renders the combined ``feed.xml``/``rss.xml`` update feed as well as one
per-app feed per catalog entry (``feeds/<slug>.xml``), so users can follow a
single app in any RSS reader.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from omnisource.domain import Catalog


def _rfc822_date(date_str: str) -> str:
    """Format an ISO date string (YYYY-MM-DD or full timestamp) as RFC 822.

    Returns ``""`` for a missing or unparsable date rather than substituting the
    current time. An RSS ``<pubDate>`` is a claim about when the *item* was
    published; stamping an unknown date with "now" both states something the
    builder does not know and makes the rendered file depend on the wall clock.
    Both elements are optional in RSS 2.0, so "no date" renders as no element.
    """
    if not date_str:
        return ""
    try:
        if "T" in date_str or " " in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _newest_date(items: list[dict[str, Any]]) -> str:
    """Newest ``date`` among ``items``, or ``""`` when none carry one.

    Compared as datetimes because RFC 822 leads with the weekday: a lexicographic
    ``max`` would rank ``Fri,`` above ``Mon,`` regardless of the year and pick an
    arbitrary item as the channel's build date.
    """
    newest: tuple[datetime, str] | None = None
    for item in items:
        raw = str(item.get("date") or "")
        if not raw:
            continue
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if newest is None or when > newest[0]:
            newest = (when, raw)
    return newest[1] if newest else ""


def _collect_items(
    catalog: Catalog,
    state: dict[str, Any],
    *,
    limit: int,
    slug: str | None = None,
) -> list[dict[str, Any]]:
    """Collect recent release items for the whole catalog or a single app.

    Update-history events are listed first (they carry changelogs); the newest
    known version of every app (or of the selected app) seeds the list when
    history is sparse, mirroring the AltStore news card behaviour.
    """
    base = catalog.base_url.rstrip("/")
    app_by_slug = {app.slug: app for app in catalog.apps}
    app_by_bundle = {app.bundle_id: app for app in catalog.apps}

    def matches(app: Any) -> bool:
        return slug is None or app.slug == slug

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. From recorded update history
    history = state.get("updateHistory", [])
    if isinstance(history, list):
        for event in history:
            if not isinstance(event, dict):
                continue
            app_id = str(event.get("appId") or "")
            app = app_by_slug.get(app_id) or app_by_bundle.get(app_id)
            if not app or not matches(app):
                continue
            version = str(event.get("version") or "")
            guid = f"{app.slug}-{version}"
            if guid in seen:
                continue
            seen.add(guid)
            items.append(
                {
                    "title": f"{app.name} v{version}",
                    "link": f"{base}/feeds/{app.slug}.json",
                    "guid": guid,
                    "date": _rfc822_date(str(event.get("releaseDate") or "")),
                    "description": str(event.get("changelog") or app.short_description or ""),
                    "download_url": str(event.get("downloadUrl") or ""),
                }
            )
            if len(items) >= limit:
                return items

    # 2. Seed with newest app versions if history is sparse
    if len(items) < limit:
        for app in catalog.apps:
            if not matches(app):
                continue
            versions = state.get(app.slug, {}).get("versions")
            if not isinstance(versions, list) or not versions:
                continue
            newest = versions[0]
            version = str(newest.get("version") or "")
            guid = f"{app.slug}-{version}"
            if guid in seen:
                continue
            seen.add(guid)
            items.append(
                {
                    "title": f"{app.name} v{version}",
                    "link": f"{base}/feeds/{app.slug}.json",
                    "guid": guid,
                    "date": _rfc822_date(str(newest.get("date") or "")),
                    "description": str(newest.get("localizedDescription") or app.short_description or ""),
                    "download_url": str(newest.get("downloadURL") or ""),
                    "size": newest.get("size", 0),
                }
            )
            if len(items) >= limit:
                break
    return items


def _render_channel(
    *,
    title: str,
    link: str,
    description: str,
    self_link: str,
    items: list[dict[str, Any]],
) -> str:
    """Render an RSS 2.0 channel with a CDATA description per item.

    Pure by design: the same catalog and state always render the same bytes. The
    channel's ``<lastBuildDate>`` is therefore the newest item date, not the
    moment the renderer ran — the previous ``_now_rfc822()`` stamp made every
    feed churn on a rebuild that changed nothing (the docs bot committed 95
    re-dated ``feeds/<slug>.xml`` files on a day with a single app update), and
    it broke the build's own reproducibility guarantee.
    """
    item_xml_lines: list[str] = []
    for item in items:
        esc_title = html.escape(item["title"])
        esc_link = html.escape(item["link"])
        esc_guid = html.escape(item["guid"])
        esc_desc = f"<![CDATA[{item['description']}]]>"
        enclosure = ""
        if item.get("download_url"):
            esc_dl = html.escape(item["download_url"])
            size = item.get("size", 0)
            enclosure = f'\n      <enclosure url="{esc_dl}" length="{size}" type="application/octet-stream" />'

        pub_date = f"\n      <pubDate>{item['date']}</pubDate>" if item.get("date") else ""
        item_xml_lines.append(
            f"""    <item>
      <title>{esc_title}</title>
      <link>{esc_link}</link>
      <guid isPermaLink="false">{esc_guid}</guid>{pub_date}
      <description>{esc_desc}</description>{enclosure}
    </item>"""
        )

    newest = _newest_date(items)
    last_build_line = f"    <lastBuildDate>{newest}</lastBuildDate>\n" if newest else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{html.escape(title)}</title>
    <link>{html.escape(link)}</link>
    <description>{html.escape(description)}</description>
    <language>en-us</language>
{last_build_line}    <atom:link href="{html.escape(self_link)}" rel="self" type="application/rss+xml"/>
{chr(10).join(item_xml_lines)}
  </channel>
</rss>
"""


def render_rss_feed(
    catalog: Catalog,
    state: dict[str, Any],
    limit: int = 25,
) -> str:
    """Generate a clean RSS 2.0 XML feed of recent updates."""
    base = catalog.base_url.rstrip("/")
    source_name = str(catalog.source.get("name", "OmniSource"))
    source_desc = str(catalog.source.get("description", "OmniSource curated iOS app updates"))
    items = _collect_items(catalog, state, limit=limit)
    return _render_channel(
        title=f"{source_name} Updates",
        link=f"{base}/",
        description=source_desc,
        self_link=f"{base}/feeds/feed.xml",
        items=items,
    )


def render_app_rss_feed(
    catalog: Catalog,
    state: dict[str, Any],
    slug: str,
    limit: int = 15,
) -> str:
    """Generate the per-app RSS 2.0 feed for one catalog slug."""
    base = catalog.base_url.rstrip("/")
    app = next((item for item in catalog.apps if item.slug == slug), None)
    if app is None:
        return ""
    source_name = str(catalog.source.get("name", "OmniSource"))
    items = _collect_items(catalog, state, limit=limit, slug=slug)
    return _render_channel(
        title=f"{app.name} — Releases",
        link=f"{base}/feeds/{app.slug}.json",
        description=(f"Release feed for {app.name} within {source_name}, curated from official upstream sources."),
        self_link=f"{base}/feeds/{app.slug}.xml",
        items=items,
    )
