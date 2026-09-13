#!/usr/bin/env python3
"""Site-wide integrity audit for the published OmniSource tree.

Walks every HTML page in the repo root (the exact bundle GitHub Pages
serves), resolves each local href/src/srcset/data-* URL against the file
system, and reports anything that would 404, plus a set of structural
checks (missing alt/lang/title, duplicate ids, unbalanced i18n keys).
Read-only. Exit code 1 when problems are found.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "_site",
    "web",
    "sdk",
    "tests",
    "scripts",
    "src",
    ".github",
    ".cache",
    "__pycache__",
}
EXTERNAL_SCHEMES = {"http:", "https:", "mailto:", "tel:", "data:", "blob:"}
# Custom URL schemes used for sideloading deep links — valid, not files.
DEEP_SCHEMES = {"altstore:", "sidestore:", "feather:", "esign:", "livecontainer:", "itms-apps:", "apple-music:"}

URL_ATTRS = {
    "a": ["href"],
    "link": ["href"],
    "script": ["src"],
    "img": ["src", "data-src"],
    "source": ["src", "srcset"],
    "iframe": ["src"],
    "video": ["src", "poster"],
    "audio": ["src"],
    "image": ["href"],
    "use": ["href"],
    "form": ["action"],
}


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[tuple[str, str, int]] = []
        self.ids: list[str] = []
        self.imgs_no_alt: list[tuple[str, int]] = []
        self.i18n_keys: set[str] = set()
        self.lang: str | None = None
        self.title = False
        self.has_h1 = 0
        self._depth_title = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "html":
            self.lang = d.get("lang")
        if tag == "title":
            self.title = True
        if tag == "h1":
            self.has_h1 += 1
        if tag == "img" and "alt" not in d:
            self.imgs_no_alt.append((d.get("src", "?"), self.getpos()[0]))
        if "id" in d:
            self.ids.append(d["id"])
        for key, val in d.items():
            # Every data-i18n* value is a bundle key worth checking, including
            # the aria/placeholder variants — a missing key shows up as the
            # raw "nav.language" string wherever it is used.
            if key.startswith("data-i18n") and val:
                self.i18n_keys.add(val)
        for attr in URL_ATTRS.get(tag, []):
            val = d.get(attr)
            if not val:
                continue
            if attr == "srcset":
                for part in val.split(","):
                    u = part.strip().split(" ")[0]
                    if u:
                        self.urls.append((u, attr, self.getpos()[0]))
            else:
                for u in val.split():
                    self.urls.append((u, attr, self.getpos()[0]))


def classify(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme:
        low = parts.scheme.lower() + ":"
        if low in DEEP_SCHEMES:
            return "deep"
        if low in EXTERNAL_SCHEMES:
            return "external"
        return "scheme?" + parts.scheme
    if url.startswith("//"):
        return "external"
    return "local"


def resolve(page: Path, url: str) -> Path:
    parts = urlsplit(url)
    path = unquote(parts.path)
    return ROOT / path.lstrip("/") if url.startswith("/") else page.parent / path


def exists(target: Path) -> bool:
    if target.is_file():
        return True
    if target.is_dir():
        return (target / "index.html").is_file()
    # GitHub Pages also serves dir/ when dir/index.html exists; a trailing
    # slash-less dir path is a redirect, still fine.
    return False


def main() -> int:
    pages = sorted(p for p in ROOT.rglob("*.html") if not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts))
    broken: list[str] = []
    dup_ids: list[str] = []
    no_lang: list[str] = []
    no_title: list[str] = []
    no_alt: list[str] = []
    missing_keys: dict[str, list[str]] = defaultdict(list)
    stats = defaultdict(int)

    en = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))

    def lookup(doc, dotted):
        cur = doc
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    locale_docs = {}
    for f in sorted((ROOT / "locales").glob("*.json")):
        locale_docs[f.stem] = json.loads(f.read_text(encoding="utf-8"))

    for page in pages:
        rel = page.relative_to(ROOT).as_posix()
        try:
            raw = page.read_text(encoding="utf-8")
        except Exception as exc:
            broken.append(f"{rel}: unreadable ({exc})")
            continue
        c = Collector()
        try:
            c.feed(raw)
        except Exception as exc:
            broken.append(f"{rel}: HTML parse error ({exc})")
            continue
        stats["pages"] += 1
        if not c.lang:
            no_lang.append(rel)
        if not c.title and "<title" not in raw:
            no_title.append(rel)
        counts = defaultdict(int)
        for i in c.ids:
            counts[i] += 1
        for i, n in counts.items():
            if n > 1:
                dup_ids.append(f"{rel}: #{i} appears {n}x")
        for src, line in c.imgs_no_alt:
            no_alt.append(f"{rel}:{line}: <img src={src}>")
        for key in sorted(c.i18n_keys):
            if lookup(en, key) is None:
                missing_keys[key].append(rel)
        for url, attr, line in c.urls:
            kind = classify(url)
            stats["url:" + kind] += 1
            if kind != "local":
                continue
            target = resolve(page, url)
            if not exists(target):
                broken.append(f'{rel}:{line}: {attr}="{url}"')

    print(f"pages scanned: {stats['pages']}")
    print(f"local urls: {stats['url:local']}  external: {stats['url:external']}  deep links: {stats['url:deep']}")

    def section(name, items, limit=40):
        print(f"\n== {name}: {len(items)}")
        for it in items[:limit]:
            print("   " + it)
        if len(items) > limit:
            print(f"   ... +{len(items) - limit} more")

    section("BROKEN LOCAL URLS", broken)
    section("DUPLICATE IDS", dup_ids)
    section("MISSING html[lang]", no_lang)
    section("MISSING <title>", no_title)
    section("IMG WITHOUT ALT", no_alt)
    section(
        "I18N KEYS MISSING FROM en.json",
        [f"{k}  (used by {len(v)} page(s): {', '.join(v[:3])})" for k, v in sorted(missing_keys.items())],
    )

    # Locale coverage relative to English
    def flat(doc, prefix=""):
        out = {}
        for k, v in doc.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(flat(v, key + "."))
            else:
                out[key] = v
        return out

    en_flat = flat(en)
    print(f"\n== LOCALE COVERAGE (vs en.json, {len(en_flat)} keys)")
    for code, doc in sorted(locale_docs.items()):
        if code == "en":
            continue
        f = flat(doc)
        miss = [k for k in en_flat if k not in f or not str(f[k]).strip()]
        extra = [k for k in f if k not in en_flat]
        pct = 100.0 * (len(en_flat) - len(miss)) / max(1, len(en_flat))
        print(
            f"   {code}: {pct:5.1f}%  missing={len(miss)}  extra={len(extra)}"
            + (f"  e.g. {', '.join(miss[:4])}" if miss else "")
        )

    problems = len(broken) + len(dup_ids) + len(missing_keys) + len(no_lang) + len(no_title)
    print(f"\nTOTAL PROBLEMS (broken urls + dup ids + missing i18n + no lang + no title): {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
