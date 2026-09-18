"""Translation key coverage: every i18n key used by the site must exist in
en.json (the fallback locale), every en.json key must actually be wired to a
call site (no dead keys), and ${placeholder} sets must match en in all
locales so interpolation never renders a raw token.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ATTR_RE = re.compile(r'data-i18n(?:-placeholder|-aria-label|-alt|-title)?="([^"]+)"')
# OS.t('key') / OmniI18n.t("key") / any t('key') call with a literal key.
CALL_RE = re.compile(r"""\b(?:t|translate)\(\s*["']([A-Za-z][\w.]*)["']""")
# setAttribute('data-i18n', 'key') wiring from injected DOM (features.js).
SETATTR_RE = re.compile(
    r"""setAttribute\(\s*["']data-i18n(?:-placeholder|-aria-label|-alt|-title)?["']\s*,\s*["']([^"']+)["']"""
)
# // i18n-keys: a.b, c.d — keys dispatched through a variable (site.js).
MARKER_RE = re.compile(r"i18n-keys:[ \t]*([A-Za-z][\w., \t]*)")
PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


def flatten(obj, prefix="", out=None):
    out = out if out is not None else {}
    for key, value in obj.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flatten(value, full, out)
        else:
            out[full] = value
    return out


def load_en():
    with (ROOT / "locales" / "en.json").open(encoding="utf-8") as fh:
        return flatten(json.load(fh))


# Directories that only ever contain generated output or third-party code.
# `_site/` copies every page verbatim, so including it would scan each page
# twice and let a stale build decide whether a key counts as used.
SKIP_PARTS = {"_site", ".git", "node_modules", ".next"}


def iter_pages():
    for pattern in ("*.html", "*/index.html", "*/*/index.html"):
        for path in sorted(ROOT.glob(pattern)):
            if not SKIP_PARTS.intersection(path.parts):
                yield path


def iter_scripts():
    yield from sorted((ROOT / "js").glob("*.js"))
    yield from sorted((ROOT / "js" / "modules").glob("*.js"))
    yield from sorted((ROOT / "src" / "js").glob("*.js"))
    yield from sorted((ROOT / "website").rglob("*.js"))


def record_markers(text, where, used):
    for match in MARKER_RE.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key:
                used.setdefault(key, []).append(where)


def collect_used():
    used = {}
    for page in iter_pages():
        text = page.read_text(encoding="utf-8")
        for match in ATTR_RE.finditer(text):
            used.setdefault(match.group(1), []).append(f"{page.name}:attr")
        for match in CALL_RE.finditer(text):
            used.setdefault(match.group(1), []).append(f"{page.name}:inline")
        for match in SETATTR_RE.finditer(text):
            used.setdefault(match.group(1), []).append(f"{page.name}:inline-attr")
        record_markers(text, f"{page.name}:marker", used)
    for script in iter_scripts():
        text = script.read_text(encoding="utf-8")
        for match in CALL_RE.finditer(text):
            used.setdefault(match.group(1), []).append(f"{script.name}:js")
        for match in SETATTR_RE.finditer(text):
            used.setdefault(match.group(1), []).append(f"{script.name}:js-attr")
        record_markers(text, f"{script.name}:marker", used)
    return used


class TestTranslationCoverage(unittest.TestCase):
    def test_used_keys_exist_in_en(self):
        en = load_en()
        used = collect_used()
        self.assertTrue(used, "usage scan found no keys - the scan itself is broken")
        unknown = sorted(set(used) - set(en))
        detail = ", ".join(f"{key} ({', '.join(used[key][:3])})" for key in unknown)
        self.assertFalse(unknown, f"keys used by the site but missing from locales/en.json: {detail}")

    def test_no_dead_keys_in_en(self):
        en = load_en()
        used = collect_used()
        dead = sorted(set(en) - set(used))
        self.assertFalse(dead, "locales/en.json keys with no call site (wire or remove): " + ", ".join(dead))

    def test_placeholder_parity_across_locales(self):
        en = load_en()
        problems = []
        for path in sorted((ROOT / "locales").glob("*.json")):
            if path.name == "en.json":
                continue
            with path.open(encoding="utf-8") as fh:
                locale = flatten(json.load(fh))
            for key, template in en.items():
                want = set(PLACEHOLDER_RE.findall(str(template)))
                got = set(PLACEHOLDER_RE.findall(str(locale.get(key, ""))))
                if want != got:
                    problems.append(f"{path.name}:{key} placeholders {sorted(got)} != en {sorted(want)}")
        self.assertFalse(problems, "placeholder mismatch vs en.json:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
