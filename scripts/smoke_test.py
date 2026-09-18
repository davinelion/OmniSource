#!/usr/bin/env python3
"""End-to-end smoke test for the assembled static site.

Builds the deployable site into a temporary directory (exactly what
``sync.yml`` publishes), serves it over local HTTP, then verifies the
contract the website and feed clients depend on:

* every page and feed URL returns HTTP 200;
* the live catalog data is reachable at all three URL families
  (flat ``/apps.json``, organized ``/feeds/apps.json``, API ``/api/apps.json``);
* each page carries the markers and ``#id`` elements its renderer needs;
* the client scripts — external files *and* the inline ``<script>``
  blocks the section pages carry — are syntactically valid JavaScript
  (when ``node`` is available);

``--root`` serves the repository tree instead of the ``_site/`` artifact —
that is what GitHub Pages serves while it is configured for a *branch*
deployment, and it is how the installable source URL
(https://iamsmmh.github.io/OmniSource/apps.json) is reached. The two modes
share the same page/API expectations; the flat URL family is fully published
in ``_site/`` and reduced to ``/apps.json`` at the repository root.

Usage
-----
    python3 scripts/smoke_test.py             # build _site/ + serve + verify
    python3 scripts/smoke_test.py --no-build  # verify an existing _site/
    python3 scripts/smoke_test.py --root      # verify the repository root
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SITE = ROOT / "_site"

# (path, substring that must be present in the 200 response)
PAGES: list[tuple[str, str]] = [
    ("/", 'id="appsGrid"'),
    ("/compare/", 'id="compareForm"'),
    ("/status/", 'id="stTable"'),
    ("/analytics/", 'id="anKpis"'),
    ("/install/", 'id="installClients"'),
    ("/search/", 'id="searchPageInput"'),
    ("/collections/", 'id="collections-content"'),
    ("/favorites/", 'id="favorites-content"'),
    ("/apps/ytlite/", 'class="app-page"'),
    ("/manifest.webmanifest", '"name": "OmniSource'),
    ("/locales/en.json", '"nav"'),
    ("/website/assets/AssetManager.js", "AssetManager"),
    ("/sw.js", "omnisource-v9"),
    ("/sitemap.xml", "<urlset"),
    ("/robots.txt", "User-agent"),
]

# Feed / API URLs that must resolve (they exist at all three families).
FEEDS: list[str] = [
    "apps.json",
    "discovery.json",
    "sources.json",
    "health.json",
    "updates.json",
    "analytics.json",
    "verification.json",
    "status.json",
    "trending.json",
    "related.json",
    "reputation.json",
    "download-intelligence.json",
    "community.json",
    "install.json",
    "search-index.json",
    "compare.json",
    "asset-manifest.json",
    "feed.xml",
    "rss.xml",
]

# OmniStore Pro contract URLs (organized + API families; no flat twins).
V2_URLS: list[str] = [
    "/feeds/api/v2/manifest.json",
    "/feeds/api/v2/featured.json",
    "/feeds/api/v2/categories.json",
    "/feeds/api/v2/updates.json",
    "/feeds/api/v2/apps/ytlite.json",
    "/api/v2/manifest.json",
    "/api/v2/featured.json",
    "/api/v2/categories.json",
    "/api/v2/updates.json",
    "/api/v2/apps/ytlite.json",
    "/api/v2/index.json",
]

# The subset of #ids each page's renderer actually uses (site.js loads on
# every page but boot() only calls the renderer for the current data-page,
# so a page only needs the ids that renderer touches).
PAGE_IDS: dict[str, list[str]] = {
    "sources/index.html": [
        "srcGrid",
        "srcFilter",
        "srcStatus",
        "srcSort",
        "srcChips",
        "srcEmpty",
        "srcReset",
        "srcStatic",
        "srcStats",
        "srcStatTotal",
    ],
    "index.html": [
        "appsGrid",
        "searchInput",
        "sortSelect",
        "osSelect",
        "categoryFilters",
        "statusFilters",
        "clearFilters",
        "resultCount",
        "emptyState",
        "collisionSummary",
        "updatesList",
        "updatesNote",
        "footerClients",
        "guideClients",
        "installGuide",
        "clientButtons",
        "sourceUrl",
        "statApps",
        "statSources",
        "statOnline",
        "statVerified",
        "statSyncLabel",
        "healthLabel",
        "appDialog",
        "dialogContent",
        "qrDialog",
        "qrTitle",
        "qrText",
        "qrImage",
        "qrCopy",
        "sourceQr",
    ],
    "compare/index.html": [
        "compareForm",
        "leftSelect",
        "rightSelect",
        "pairList",
        "pairs",
        "result",
        "cmpEmpty",
        "resultGrid",
        "cmpMetaRow",
    ],
    "status/index.html": [
        "stOverview",
        "stTableWrap",
        "stSyncGrid",
        "stTable",
        "stMetaRow",
        "statusContent",
    ],
    "analytics/index.html": [
        "anKpis",
        "trendChart",
        "updateBars",
        "categoryBars",
        "verificationDonut",
        "weekLists",
        "anMetaRow",
        "analyticsContent",
    ],
    "install/index.html": [
        "installClients",
        "installAppSelect",
        "installAppCards",
        "installAppFeed",
        "installQr",
    ],
    "search/index.html": ["searchPageInput", "searchResults", "searchCount"],
}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    directory = _SITE

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(self.directory), **kwargs)

    def log_message(self, *args: object) -> None:
        pass


class _Server:
    def __init__(self, directory: Path = _SITE) -> None:
        handler = type("_Handler", (_QuietHandler,), {"directory": directory})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def ids_in(html: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))


INLINE_SCRIPT_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script[^>]*>", re.DOTALL | re.IGNORECASE)


def check_js_syntax(site_root: Path = _SITE) -> list[str]:
    node = shutil.which("node")
    if not node:
        return []
    errors = []
    scripts = ["js/core.js", "js/site.js", "js/features.js"]
    scripts += sorted(str(path.relative_to(ROOT)) for path in (ROOT / "src" / "js").glob("*.js"))
    scripts += sorted(str(path.relative_to(ROOT)) for path in (ROOT / "website").rglob("*.js"))
    for script in scripts:
        result = subprocess.run(
            [node, "--check", str(ROOT / script)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"{script}: {result.stderr.strip()}")

    # Inline <script> blocks too. The hand-maintained section pages carry
    # their whole page logic inline, and an over-escaped string literal in
    # collections/index.html silently killed that page while this check only
    # looked at external files.
    if site_root.is_dir():
        with tempfile.TemporaryDirectory() as tmp:
            for page in sorted(site_root.rglob("*.html")):
                parts = set(page.relative_to(site_root).parts)
                if parts & {"_site", "node_modules"} or any(p.startswith(".") for p in parts):
                    continue  # build output / vendored code, checked separately
                html = page.read_text(encoding="utf-8", errors="replace")
                for index, match in enumerate(INLINE_SCRIPT_RE.finditer(html)):
                    attrs = match.group("attrs") or ""
                    body = match.group("body") or ""
                    if "src=" in attrs or "application/ld+json" in attrs or not body.strip():
                        continue
                    scratch = Path(tmp) / f"inline-{index}.js"
                    scratch.write_text(body, encoding="utf-8")
                    result = subprocess.run(
                        [node, "--check", str(scratch)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode != 0:
                        line = html[: match.start()].count("\n") + 1
                        detail = next(
                            (ln for ln in result.stderr.splitlines() if "SyntaxError" in ln),
                            result.stderr.strip(),
                        )
                        rel = page.relative_to(site_root).as_posix()
                        errors.append(f"{rel} inline script #{index} (line {line}): {detail.strip()}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true", help="verify an existing _site/ without rebuilding")
    parser.add_argument("--root", action="store_true", help="verify the repository root (branch deployment view)")
    args = parser.parse_args(argv)

    failures: list[str] = []
    site_root = ROOT if args.root else _SITE

    if not args.no_build and not args.root:
        sys.path.insert(0, str(ROOT / "src"))
        from omnisource.site import build_site

        tmp = Path(tempfile.mkdtemp(prefix="omnisource-smoke-", dir=ROOT))
        try:
            build_site(tmp)
            shutil.rmtree(_SITE, ignore_errors=True)
            shutil.copytree(tmp, _SITE)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if not (site_root / "index.html").is_file():
        print(f"smoke_test: {site_root}/index.html missing — build the site first", file=sys.stderr)
        return 2

    with _Server(site_root) as server:
        print(f"smoke_test: serving {site_root} at {server.base}")
        checked = 0
        for path, marker in PAGES:
            status, body = fetch(server.base + path)
            checked += 1
            if status != 200:
                failures.append(f"{path}: HTTP {status}")
                continue
            text = body.decode("utf-8", "replace")
            if marker not in text:
                failures.append(f"{path}: missing marker {marker!r}")

        for name in FEEDS:
            # The /api/ mirror is JSON-only; XML feeds live at the organized
            # family but not under /api/. The flat family is only guaranteed
            # for /apps.json at the repository root (the assembled _site/
            # artifact publishes every flat URL), so the other flat URLs are
            # checked in build mode only.
            families = [f"/feeds/{name}"]
            if name.endswith(".json"):
                families.append(f"/api/{name}")
            if name == "apps.json" or not args.root:
                families.append(f"/{name}")
            for path in families:
                status, body = fetch(server.base + path)
                checked += 1
                if status != 200:
                    failures.append(f"{path}: HTTP {status}")

        for path in V2_URLS:
            status, body = fetch(server.base + path)
            checked += 1
            if status != 200:
                failures.append(f"{path}: HTTP {status}")
                continue
            try:
                doc = json.loads(body.decode("utf-8"))
            except ValueError:
                failures.append(f"{path}: invalid JSON")
                continue
            if path.endswith("/manifest.json") and doc.get("schemaVersion") != 2:
                failures.append(f"{path}: schemaVersion must be 2")
            if path.endswith("/apps/ytlite.json") and (doc.get("app") or {}).get("id") != "ytlite":
                failures.append(f"{path}: app record id mismatch")

        if args.root:
            # The root carries only /apps.json plus the API mirror, sitemap
            # and robots — this is the exact surface installers and clients
            # hit when the branch itself is served.
            published = [
                "apps.json",
                "sitemap.xml",
                "robots.txt",
                ".nojekyll",
                "api/index.json",
                *(f"api/{path.name}" for path in sorted((ROOT / "api").iterdir())),
            ]
            for path in published:
                status, _body = fetch(f"{server.base}/{path}")
                checked += 1
                if status != 200:
                    failures.append(f"/{path}: HTTP {status} (branch deployment would 404)")

            # The gzip twin must decode to the same document as its source.
            import gzip

            status, body = fetch(f"{server.base}/api/apps.json.gz")
            if status == 200:
                expected = (ROOT / "feeds" / "apps.json").read_bytes()
                if gzip.decompress(body) != expected:
                    failures.append("api/apps.json.gz does not decode to feeds/apps.json")

        # Each page's renderer must find every #id it touches; catches broken
        # selectors after edits.
        for page, wanted in PAGE_IDS.items():
            page_html = (site_root / page).read_text(encoding="utf-8")
            present = ids_in(page_html)
            for ref in sorted(set(wanted) - present):
                failures.append(f"{page}: missing id #{ref} used by its renderer")

        # The sources.json repositories index must be human-readable now.
        sources = json.loads((site_root / "feeds" / "sources.json").read_text(encoding="utf-8"))
        raw = (site_root / "feeds" / "sources.json").read_text(encoding="utf-8")
        if "\n  " not in raw:
            failures.append("sources.json is not indented (still one long line)")
        if sources.get("count") != len(sources.get("sources", [])):
            failures.append("sources.json: count does not match sources[] length")

        # Phase 4: every source in the index must have a static detail page,
        # and every detail page must carry the explorer contract fields.
        for entry in sources.get("sources", [])[:5]:
            slug = entry.get("slug") or ""
            detail = site_root / "sources" / slug / "index.html"
            if not slug or not detail.is_file():
                failures.append(f"sources.json: no static page for source {entry.get('id')!r}")
                continue
            page_html = detail.read_text(encoding="utf-8")
            for needle in ('data-page="source-detail"', "og:title", 'rel="canonical"', "Maintainer", "Reputation"):
                if needle not in page_html:
                    failures.append(f"sources/{slug}/index.html: missing {needle!r}")

    for error in check_js_syntax(site_root):
        failures.append(error)

    print(f"smoke_test: checked {checked} URL(s), {len(PAGES)} page(s), {len(FEEDS)} feed(s) x 3 families")
    if failures:
        print(f"smoke_test: {len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("smoke_test: all checks passed ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
