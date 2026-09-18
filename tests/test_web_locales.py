"""Every language the modern site ships must be complete.

A missing key in ``web/src/i18n/locales/*.ts`` does not crash anything: the
lookup returns ``undefined`` and the UI renders a raw key, an empty button, or
``undefined`` in the middle of a sentence. Nothing in the build catches it,
because the files are TypeScript objects typed as ``Record<string, string>``
rather than a fixed interface.

So the invariants are asserted against the filesystem and the English source of
truth: the same sections, the same keys, no empty strings, and no locale that
silently disappeared from the registry.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LOCALES_DIR = WEB / "src" / "i18n" / "locales"
DICTIONARIES = WEB / "src" / "i18n" / "dictionaries.ts"

SECTIONS = ("nav", "common", "home", "sections")
KEY_RE = re.compile(r'^\s{4}([A-Za-z0-9_]+):\s*"(.*)",\s*$')


def _parse(path: Path) -> dict[str, dict[str, str]]:
    """Pull ``{ section: { key: value } }`` out of a dictionary module."""
    out: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        start = re.match(r"^\s{2}([A-Za-z0-9_]+):\s*\{\s*$", line)
        if start:
            section = start.group(1)
            out.setdefault(section, {})
            continue
        if section and re.match(r"^\s{2}\},?\s*$", line):
            section = None
            continue
        if section:
            match = KEY_RE.match(line)
            if match:
                out[section][match.group(1)] = match.group(2)
    return out


class LocaleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dictionaries = {path.stem: _parse(path) for path in sorted(LOCALES_DIR.glob("*.ts"))}
        cls.english = cls.dictionaries.get("en", {})
        if not cls.english:
            raise AssertionError("web/src/i18n/locales/en.ts could not be parsed — did the format change?")

    def test_the_registry_lists_exactly_the_files_on_disk(self) -> None:
        registry = DICTIONARIES.read_text(encoding="utf-8")
        listed = set(re.findall(r'"([a-z]{2})"', registry.split("const loaders", 1)[0]))
        on_disk = set(self.dictionaries)
        self.assertEqual(on_disk, listed, "LOCALES and locales/*.ts disagree")

    def test_every_locale_has_the_same_sections(self) -> None:
        for code, dicts in self.dictionaries.items():
            self.assertEqual(
                set(dicts),
                set(SECTIONS),
                f"{code}.ts has sections {sorted(dicts)}; the Dictionary type expects {sorted(SECTIONS)}",
            )

    def test_every_locale_has_the_same_keys(self) -> None:
        for code, dicts in self.dictionaries.items():
            for section in SECTIONS:
                missing = sorted(set(self.english.get(section, {})) - set(dicts.get(section, {})))
                extra = sorted(set(dicts.get(section, {})) - set(self.english.get(section, {})))
                self.assertEqual([], missing, f"{code}.ts is missing {section} keys: {missing}")
                self.assertEqual([], extra, f"{code}.ts has {section} keys English does not: {extra}")

    def test_no_translation_is_blank(self) -> None:
        for code, dicts in self.dictionaries.items():
            for section, entries in dicts.items():
                blanks = sorted(key for key, value in entries.items() if not value.strip())
                self.assertEqual([], blanks, f"{code}.ts has empty {section} strings: {blanks}")

    def test_no_translation_renders_a_raw_key(self) -> None:
        # ``common.apps`` legitimately *is* the word "apps" in English, so a
        # value that equals its key is only evidence of an untranslated leftover
        # when English itself says something else there.
        for code, dicts in self.dictionaries.items():
            if code == "en":
                continue  # English is the source of truth: "apps" is a word, not a key
            offenders = []
            for section, entries in dicts.items():
                english = self.english.get(section, {})
                for key, value in entries.items():
                    if value.strip() != key and value.strip() != f"{section}.{key}":
                        continue
                    if code != "en" and english.get(key, "").strip() == key:
                        continue
                    offenders.append(key)
            self.assertEqual([], offenders, f"{code}.ts echoes key names instead of translating them")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
