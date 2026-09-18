"""Docs hub (``docs/index.html``): a landing page over the Markdown docs.

The docs are plain Markdown served straight off Pages; the modernization pass
adds a browsable hub so ``/docs/`` is a real navigation target instead of a
404. Titles come from each document's first heading and the one-line summary
from its first paragraph — nothing is hand-maintained, so the hub can never
drift from the files it lists.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from omnisource.io import atomic_write_text

#: Which Markdown files the hub lists, in navigation order. Files present in
#: docs/ but absent here are still linked at the bottom of the page under
#: "Archive" so nothing on the site becomes unreachable.
DOCUMENTS = (
    ("README-FEATURES.md", "Feature map", "What the platform ships: catalog, health, APIs and pages."),
    ("ARCHITECTURE.md", "Architecture", "Modules, data flow and the pipeline stages behind every feed."),
    ("API.md", "API reference", "Every endpoint, schema, gzip twin and versioning rule."),
    ("REPOSITORY.md", "Repository layout", "Which directory is canonical and which are generated mirrors."),
    ("DEPLOYMENT-GUIDE.md", "Deployment", "GitHub Pages wiring, the sync workflow and rollback notes."),
    ("localization.md", "Localization", "Locale files, coverage policy and the translation pipeline."),
    ("website.md", "Website", "Pages, design system tokens and the client-side module map."),
    ("FEATURES-P0-P3.md", "Feature tiers", "The P0-P3 breakdown of the modernization roadmap."),
    ("AUDIT.md", "Audit log", "Findings and fixes from the repository audit."),
    ("CHANGES.md", "Changelog", "Notable changes between builds."),
    ("SOURCING-REPORT.md", "Sourcing report", "How upstream sources were selected and validated."),
    (
        "SOURCING-REPORT-2026-09-15.md",
        "Sourcing verdicts (2026-09-15)",
        "FMHY iOS tools: what was catalogued, and why the decrypted-app storefronts are blocked.",
    ),
    (
        "SOURCE-BUILDS.md",
        "Source builds",
        "The separate lane for projects that publish source but no binary: recipes, digests, signing.",
    ),
    (
        "TWEAK-FACTORY.md",
        "Tweak factory",
        "The scheduled lane that collects official .deb tweaks, injects them and publishes provenance releases.",
    ),
)

_HEADING = re.compile(r"^#\s+(.+)$", re.M)
_PARAGRAPH = re.compile(r"^(?!#|>|\||```|<!--|\s*$)(.+)$", re.M)


def _summary(text: str) -> str:
    for line in text.splitlines():
        if line.startswith(("#", ">", "|", "<!--", "```", "!", "- ", "* ", "1.")):
            continue
        clean = re.sub(r"`([^`]*)`", r"\1", line.strip())
        clean = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", clean)
        clean = re.sub(r"[*_]", "", clean).strip()
        if len(clean) > 18:
            return (clean[:157] + "…") if len(clean) > 158 else clean
    return ""


def _title(text: str, fallback: str) -> str:
    match = _HEADING.search(text)
    return match.group(1).strip() if match else fallback


def build_docs_index(docs_dir: Path, *, base_url: str) -> Path:
    """Generate ``docs/index.html`` from the Markdown files on disk."""
    listed = {name for name, _, _ in DOCUMENTS}
    cards: list[str] = []
    seen: set[str] = set()
    for name, override_title, override_summary in DOCUMENTS:
        path = docs_dir / name
        if not path.is_file():
            continue
        seen.add(name)
        text = path.read_text(encoding="utf-8", errors="replace")
        title = override_title if override_title else _title(text, name.removesuffix(".md"))
        summary = override_summary or _summary(text)
        cards.append(
            '      <a class="docs-card panel" href="' + name + '">\n'
            f"        <h2>{html.escape(title)}</h2>\n"
            f'        <p class="text-muted">{html.escape(summary)}</p>\n'
            f"        <code>{html.escape(name)}</code>\n"
            "      </a>"
        )
    archive = [
        name for name in sorted(p.name for p in docs_dir.glob("*.md")) if name not in listed and name not in seen
    ]
    archive_html = ""
    if archive:
        items = " · ".join(
            f'<a href="{html.escape(name)}">{html.escape(name.removesuffix(".md"))}</a>' for name in archive
        )
        archive_html = f'    <p class="docs-archive">Archive: {items}</p>\n'

    page = "".join(
        [
            '<!doctype html>\n<html lang="en" data-theme="auto">\n<head>\n',
            '  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n',
            '  <meta name="color-scheme" content="light dark">\n',
            '  <meta name="theme-color" content="#e8eef8" media="(prefers-color-scheme: light)">\n',
            '  <meta name="theme-color" content="#07070f" media="(prefers-color-scheme: dark)">\n',
            "  <title>Documentation — OmniSource</title>\n",
            '  <meta name="description" content="Documentation for OmniSource: architecture, API '
            'reference, feed contracts, localization, deployment and the modernization roadmap.">\n',
            f'  <link rel="canonical" href="{base_url}/docs/">\n',
            '  <meta property="og:site_name" content="OmniSource">\n',
            '  <meta property="og:title" content="Documentation — OmniSource">\n',
            '  <meta property="og:description" content="Architecture, API, deployment and feed '
            'contracts for the OmniSource platform.">\n',
            '  <meta property="og:type" content="website">\n',
            f'  <meta property="og:url" content="{base_url}/docs/">\n',
            f'  <meta property="og:image" content="{base_url}/assets/OmniSource.png">\n',
            '  <meta name="twitter:card" content="summary">\n',
            f'  <meta name="twitter:image" content="{base_url}/assets/OmniSource.png">\n',
            '  <link rel="icon" type="image/png" href="../assets/OmniSource.png">\n',
            '  <link rel="manifest" href="../manifest.webmanifest">\n',
            "  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self'; base-uri 'self'; "
            "object-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com; img-src 'self' data: https:; connect-src 'self' https:; "
            "font-src 'self' data: https://fonts.gstatic.com; form-action 'self'\">\n",
            '  <link rel="stylesheet" href="../assets/design-system/tokens.css">\n',
            '  <link rel="stylesheet" href="../assets/design-system/utilities.css">\n',
            '  <link rel="stylesheet" href="../assets/design-system/animations.css">\n',
            '  <link rel="stylesheet" href="../assets/design-system/components.css">\n',
            "  <style>\n"
            "    .docs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}\n"
            "    .docs-card{display:flex;flex-direction:column;gap:8px;padding:20px;text-decoration:none;"
            "color:inherit;transition:transform var(--dur-fast) var(--ease)}\n"
            "    .docs-card:hover,.docs-card:focus-visible{transform:translateY(-2px)}\n"
            "    .docs-card h2{font-size:17px;margin:0}\n"
            "    .docs-card code{font-size:11px;color:var(--muted)}\n"
            "    .docs-archive{margin-top:24px;color:var(--muted);font-size:13px}\n"
            "  </style>\n",
            "  <script>\n"
            "    (function () {\n"
            "      try {\n"
            "        var t = localStorage.getItem('omnisource-theme');\n"
            "        if (t !== 'light' && t !== 'dark') t = 'auto';\n"
            "        document.documentElement.dataset.theme = t;\n"
            "      } catch (e) {}\n"
            "    })();\n"
            "  </script>\n",
            "</head>\n<body>\n",
            '  <a class="skip-link" href="#main">Skip to documentation</a>\n',
            '  <main class="shell page-main" id="main" style="padding-top:44px;padding-bottom:56px">\n',
            '    <p style="margin-bottom:18px"><a href="../">&larr; Back to OmniSource</a></p>\n',
            '    <span class="kicker">DOCS</span>\n'
            '    <h1 style="margin:6px 0 6px;letter-spacing:var(--track-tighter)">Documentation</h1>\n'
            '    <p class="lead text-muted">Everything about how OmniSource generates, validates and '
            "publishes its catalog — each page is a Markdown document rendered straight from the "
            "repository, so it always matches the code.</p>\n",
            '    <div class="docs-grid" style="margin-top:24px">\n' + "\n".join(cards) + "\n    </div>\n",
            archive_html,
            "  </main>\n</body>\n</html>\n",
        ]
    )
    target = docs_dir / "index.html"
    atomic_write_text(target, page)
    return target
