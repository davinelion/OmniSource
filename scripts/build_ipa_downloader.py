#!/usr/bin/env python3
"""Build the IPA downloader index: direct download links for every app.

Reads the already-built master feed (``feeds/apps.json``) and the per-client
install cards (``feeds/install.json``) and emits two artifacts:

* ``feeds/ipa-downloader.json`` — a machine-readable index of every app with
  its newest direct download URL, size, date, icon, minimum OS and the
  per-client install deep links. This is the "direct link" surface: anything
  that can read JSON gets every IPA/.tipa/.deb download URL in one document.

* ``ipa-downloader/index.html`` — a self-contained (no dependencies, no
  external requests) static page that renders the same index as a searchable
  list of direct download buttons plus one-tap install links per client.

Both artifacts are regenerated on every pipeline run by ``scripts/omnisource.py``
(after the feed build stage) and are therefore part of the reproducibility
gate in ``scripts/check_reproducible.py`` (``generatedAt`` is the only
volatile value). Stdlib only, deterministic output (apps sorted by slug).

Usage
-----
    python3 scripts/build_ipa_downloader.py            # build both artifacts
    python3 scripts/build_ipa_downloader.py --check    # verify on-disk artifacts
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "feeds" / "apps.json"
INSTALL = ROOT / "feeds" / "install.json"
OUT_JSON = ROOT / "feeds" / "ipa-downloader.json"
OUT_HTML = ROOT / "ipa-downloader" / "index.html"

DOCUMENT = {
    "name": "OmniSource IPA Downloader",
    "description": (
        "Direct download links for every app in the OmniSource catalog, "
        "resolved from each app's official upstream and refreshed by the "
        "build pipeline. downloadURL is the newest published asset; "
        "install[] are one-tap source-add deep links per client."
    ),
}

CLIENT_NAMES = {
    "altstore": "AltStore",
    "sidestore": "SideStore",
    "feather": "Feather",
    "esign": "ESign",
    "livecontainer": "LiveContainer",
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _human_size(size) -> str:
    if not isinstance(size, int) or size <= 0:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return ""


def build_document() -> dict:
    master = json.loads(MASTER.read_text(encoding="utf-8"))
    apps = master.get("apps") or []
    install_by_slug: dict[str, dict] = {}
    if INSTALL.exists():
        install_doc = json.loads(INSTALL.read_text(encoding="utf-8"))
        base_url = install_doc.get("baseURL", "") or ""
        for entry in install_doc.get("apps") or []:
            slug = entry.get("slug")
            if slug:
                install_by_slug[slug] = entry
    else:
        base_url = ""

    rows = []
    for app in apps:
        meta = app.get("omnisource") or {}
        slug = meta.get("slug") or ""
        if not slug:
            continue
        compat = meta.get("compatibility") or {}
        card = install_by_slug.get(slug) or {}
        install = []
        for item in card.get("cards") or []:
            url = item.get("url")
            if url:
                install.append(
                    {
                        "client": item.get("client"),
                        "name": item.get("name") or CLIENT_NAMES.get(item.get("client", ""), item.get("client") or ""),
                        "url": url,
                    }
                )
        rows.append(
            {
                "slug": slug,
                "name": app.get("name"),
                "developer": app.get("developerName"),
                "bundleIdentifier": app.get("bundleIdentifier"),
                "version": app.get("version"),
                "date": app.get("versionDate"),
                "size": app.get("size"),
                "sizeHuman": _human_size(app.get("size")),
                "downloadURL": app.get("downloadURL"),
                "iconURL": app.get("iconURL"),
                "tintColor": app.get("tintColor"),
                "category": app.get("category"),
                "status": meta.get("status"),
                "minOSVersion": compat.get("minOSVersion"),
                "clients": compat.get("clients") or [],
                "feedURL": f"{base_url}/feeds/{slug}.json" if base_url else None,
                "install": install,
            }
        )

    rows.sort(key=lambda row: row["slug"])
    document = dict(DOCUMENT)
    document["generatedAt"] = _now()
    document["baseURL"] = base_url or None
    document["count"] = len(rows)
    document["apps"] = rows
    return document


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OmniSource — IPA Downloader (direct links)</title>
<meta name="description" content="Direct download links for every app in the OmniSource catalog.">
<style>
  :root {
    color-scheme: dark;
    --bg: #0b0d12; --panel: #12151d; --line: #232837;
    --text: #e8ebf2; --dim: #9aa3b5; --accent: #4f8cff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  header { padding: 28px 20px 8px; max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  p.sub { color: var(--dim); margin: 0 0 14px; }
  #q {
    width: 100%; max-width: 520px; padding: 11px 14px; border-radius: 10px;
    border: 1px solid var(--line); background: var(--panel); color: var(--text); font-size: 16px;
  }
  #count { color: var(--dim); font-size: 14px; margin: 10px 0 0; }
  main { max-width: 1080px; margin: 0 auto; padding: 16px 20px 60px; }
  .app {
    display: flex; gap: 14px; align-items: center; padding: 14px 16px;
    border: 1px solid var(--line); border-radius: 12px; background: var(--panel); margin-top: 10px;
  }
  .app img { width: 52px; height: 52px; border-radius: 11px; flex: none; background: #1a1e29; }
  .info { flex: 1; min-width: 0; }
  .info .name { font-weight: 650; font-size: 16px; }
  .info .meta {
    color: var(--dim); font-size: 13.5px; margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .actions { display: flex; gap: 8px; flex: none; flex-wrap: wrap; justify-content: flex-end; max-width: 46%; }
  a.dl {
    display: inline-block; background: var(--accent); color: #fff; text-decoration: none;
    font-weight: 650; font-size: 13.5px; padding: 9px 14px; border-radius: 9px;
  }
  a.dl:hover { filter: brightness(1.12); }
  a.open {
    display: inline-block; border: 1px solid var(--line); color: var(--text); text-decoration: none;
    font-size: 13px; padding: 9px 11px; border-radius: 9px; background: transparent;
  }
  a.open:hover { border-color: var(--accent); color: var(--accent); }
  .status {
    font-size: 11.5px; padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--line); color: var(--dim); margin-left: 8px; vertical-align: 1px;
  }
  footer { max-width: 1080px; margin: 0 auto; padding: 0 20px 40px; color: var(--dim); font-size: 13px; }
  @media (max-width: 720px) {
    .app { flex-wrap: wrap; }
    .actions { max-width: 100%; justify-content: flex-start; }
  }
</style>
</head>
<body>
<header>
  <h1>OmniSource — IPA Downloader</h1>
  <p class="sub">Direct download links for every app in the catalog. Each download resolves to the
  app's official upstream asset; install links add the OmniSource source to your sideloading client.</p>
  <input id="q" type="search" placeholder="Search apps, developers, bundle IDs…" autocomplete="off">
  <p id="count"></p>
</header>
<main id="list"></main>
<footer>Generated by the OmniSource build pipeline · __GENERATED__ · direct links refresh
  automatically on every upstream sync.</footer>
<script>var OMNISOURCE_DOWNLOADS = __DATA__;</script>
<script>
(function () {
  var data = OMNISOURCE_DOWNLOADS;
  var list = document.getElementById("list");
  var q = document.getElementById("q");
  var count = document.getElementById("count");
  function row(app) {
    var el = document.createElement("div");
    el.className = "app";
    var status = app.status ? '<span class="status">' + app.status + "</span>" : "";
    var meta = [
      app.version && "v" + app.version,
      app.date,
      app.sizeHuman,
      app.minOSVersion && "iOS " + app.minOSVersion + "+",
      app.developer,
    ].filter(Boolean).join(" · ");
    var installs = (app.install || []).slice(0, 3).map(function (c) {
      return '<a class="open" href="' + c.url + '" rel="nofollow">' + c.name + "</a>";
    }).join(" ");
    var icon = app.iconURL ? '<img loading="lazy" src="' + app.iconURL + '" alt="">' : "";
    var name = '<div class="info"><div class="name">' + app.name + status + "</div>";
    var metaHtml = '<div class="meta">' + meta + "</div></div>";
    var actions =
      '<div class="actions"><a class="dl" href="' +
      app.downloadURL + '" rel="nofollow" download>Download</a>' +
      installs + "</div>";
    el.innerHTML = icon + name + metaHtml + actions;
    return el;
  }
  function haystack(app) {
    return (app.name + " " + (app.developer || "") + " " + (app.bundleIdentifier || "") + " " + app.slug);
  }
  function render(filter) {
    list.textContent = "";
    var shown = 0;
    data.apps.forEach(function (app) {
      if (filter && haystack(app).toLowerCase().indexOf(filter) === -1) return;
      list.appendChild(row(app));
      shown++;
    });
    count.textContent = shown + " of " + data.count + " apps";
  }
  q.addEventListener("input", function () {
    render(q.value.trim().toLowerCase());
  });
  render("");
})();
</script>
</body>
</html>
"""


def render_html(document: dict) -> str:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    page = PAGE_TEMPLATE.replace("__GENERATED__", escape(document["generatedAt"]))
    page = page.replace("__DATA__", payload.replace("</", "<\\/"))
    return page


def write_all() -> tuple[Path, Path]:
    document = build_document()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_HTML.write_text(render_html(document), encoding="utf-8")
    return OUT_JSON, OUT_HTML


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if on-disk artifacts are stale")
    args = parser.parse_args(argv)
    if not MASTER.exists():
        print(f"error: {MASTER} missing - run the feed build first", file=sys.stderr)
        return 1
    if args.check:
        document = build_document()

        def _canon_doc(doc: dict) -> dict:
            doc = json.loads(json.dumps(doc))
            doc.pop("generatedAt", None)
            return doc

        if not OUT_JSON.exists() or not OUT_HTML.exists():
            print("error: downloader artifacts missing - run the pipeline", file=sys.stderr)
            return 1
        current_doc = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        if _canon_doc(current_doc) != _canon_doc(document):
            print("error: feeds/ipa-downloader.json is stale", file=sys.stderr)
            return 1
        # The HTML embeds the document plus the generatedAt footer; compare
        # with the volatile timestamp normalized out of both sides.
        expected_html = render_html(document).replace(document["generatedAt"], "<DATE>")
        current_html = OUT_HTML.read_text(encoding="utf-8").replace(current_doc.get("generatedAt", ""), "<DATE>")
        if current_html != expected_html:
            print("error: ipa-downloader/index.html is stale", file=sys.stderr)
            return 1
        print(f"ipa-downloader: {document['count']} app(s), artifacts current")
        return 0
    json_path, html_path = write_all()
    document = build_document()
    message = (
        f"ipa-downloader: wrote {json_path.relative_to(ROOT)} + "
        f"{html_path.relative_to(ROOT)} ({document['count']} app(s))"
    )
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
