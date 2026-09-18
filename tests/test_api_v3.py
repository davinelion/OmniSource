"""Tests for the API v3 helpers and static documents."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from omnisource.api_v3 import (
    SAFE_DOC_ID_CHARS,
    apply_filters,
    build_static_documents,
    envelope,
    etag_for,
    feed_version_for,
    paginate,
    safe_doc_id,
    slim_app,
    sort_items,
)

ROOT = Path(__file__).resolve().parents[1]


class PaginateTests(TestCase):
    def test_pages(self) -> None:
        page = paginate(list(range(5)), page=2, per_page=2)
        self.assertEqual(page["items"], [2, 3])
        self.assertEqual(page["pages"], 3)
        self.assertTrue(page["has_next"])
        self.assertTrue(page["has_prev"])

    def test_clamps(self) -> None:
        page = paginate([1], page=99, per_page=50)
        self.assertEqual(page["page"], 1)
        self.assertFalse(page["has_next"])


class FilterSortTests(TestCase):
    def test_apply_filters(self) -> None:
        items = [{"category": "Games", "name": "A"}, {"category": "Utilities", "name": "B"}]
        self.assertEqual(len(apply_filters(items, {"category": "games"})), 1)
        self.assertEqual(len(apply_filters(items, {"name": "b"})), 1)
        self.assertEqual(len(apply_filters(items, {})), 2)

    def test_sort_items(self) -> None:
        items = [{"name": "B", "score": 1}, {"name": "A", "score": 9}]
        self.assertEqual([item["name"] for item in sort_items(items, "name")], ["A", "B"])
        self.assertEqual([item["name"] for item in sort_items(items, "-score")], ["A", "B"])


class EnvelopeTests(TestCase):
    def test_envelope_with_pagination(self) -> None:
        page = paginate([1, 2, 3], page=1, per_page=2)
        body = envelope(page["items"], feed_version="abc", pagination=page)
        self.assertEqual(body["apiVersion"], "3.0.0")
        self.assertEqual(body["pagination"]["total"], 3)
        self.assertEqual(body["data"], [1, 2])

    def test_etag_stable(self) -> None:
        self.assertEqual(etag_for("x"), etag_for("x"))
        self.assertNotEqual(etag_for("x"), etag_for("y"))

    def test_feed_version_length(self) -> None:
        self.assertEqual(len(feed_version_for({"a": "1"})), 12)


class StaticDocumentsTests(TestCase):
    def test_build_documents(self) -> None:
        bundle = {
            "apps": [
                {
                    "slug": "demo",
                    "name": "Demo",
                    "bundleIdentifier": "com.demo",
                    "developerName": "D",
                    "category": "Utilities",
                    "version": "1.0",
                    "versionDate": "2026-01-01",
                    "iconURL": "",
                    "downloadURL": "https://example.com/d.ipa",
                    "size": 1,
                }
            ],
            "sources": [{"id": "s1", "source": "S"}],
            "trending": {},
            "search_index": {},
            "status": {},
            "security": {},
            "analytics": {},
            "releases": [],
            "generated_at": "2026-01-01",
        }
        docs = build_static_documents(bundle)
        self.assertIn("index.json", docs)
        self.assertIn("apps/demo.json", docs)
        self.assertIn("sources/s1.json", docs)
        self.assertEqual(docs["index.json"]["schemaVersion"], 3)
        self.assertEqual(docs["apps.json"]["pagination"]["total"], 1)

    def test_slim_app(self) -> None:
        slim = slim_app({"slug": "d", "name": "D", "bundleIdentifier": "b", "extra": "gone"})
        self.assertNotIn("extra", slim)
        self.assertEqual(slim["id"], "d")


def _bundle(sources: list[dict]) -> dict:
    return {
        "apps": [],
        "sources": sources,
        "trending": {},
        "search_index": {},
        "status": {},
        "security": {},
        "analytics": {},
        "releases": [],
        "generated_at": "2026-01-01",
    }


class SafeDocIdTests(TestCase):
    def test_flattens_the_id_shapes_the_catalog_actually_uses(self) -> None:
        self.assertEqual(safe_doc_id("Aidoku/Aidoku"), "Aidoku-Aidoku")
        self.assertEqual(safe_doc_id("manual:cercube"), "manual-cercube")
        self.assertEqual(safe_doc_id("https://repo.ikghd.me/repo.json"), "https-repo.ikghd.me-repo.json")

    def test_always_returns_one_safe_path_segment(self) -> None:
        shapes = ("Aidoku/Aidoku", "manual:cercube", "https://a.b/c.json", "../../etc/passwd", "..", "", None, 0)
        for raw in shapes:
            with self.subTest(raw=raw):
                segment = safe_doc_id(raw)
                self.assertFalse(SAFE_DOC_ID_CHARS.search(segment))
                self.assertNotIn(segment, {"", ".", ".."})
                self.assertNotIn("/", segment)


class SourceDocumentTests(TestCase):
    def test_prefers_the_records_slug_over_its_raw_id(self) -> None:
        docs = build_static_documents(
            _bundle(
                [
                    {"id": "https://repo.ikghd.me/repo.json", "slug": "https-repo-ikghd-me-repo-json"},
                    {"id": "Aidoku/Aidoku", "slug": "aidoku-aidoku"},
                    {"id": "manual:cercube", "slug": "manual-cercube"},
                ]
            )
        )
        self.assertEqual(
            sorted(name for name in docs if name.startswith("sources/")),
            ["sources/aidoku-aidoku.json", "sources/https-repo-ikghd-me-repo-json.json", "sources/manual-cercube.json"],
        )

    def test_sanitises_a_bare_id_when_no_slug_is_present(self) -> None:
        docs = build_static_documents(_bundle([{"id": "https://source.ryuksign.com/ig410"}]))
        self.assertIn("sources/https-source.ryuksign.com-ig410.json", docs)

    def test_colliding_ids_keep_every_document(self) -> None:
        docs = build_static_documents(_bundle([{"id": "a/b"}, {"id": "a-b"}, {"id": "a/b"}]))
        self.assertEqual(
            sorted(name for name in docs if name.startswith("sources/")),
            ["sources/a-b-2.json", "sources/a-b-3.json", "sources/a-b.json"],
        )

    def test_skips_a_source_with_no_identity(self) -> None:
        docs = build_static_documents(_bundle([{"source": "no id, no slug"}]))
        self.assertEqual([name for name in docs if name.startswith("sources/")], [])


class CommittedApiSurfaceTests(TestCase):
    """Pins the invariant whose violation broke the scheduled backup job.

    ``actions/upload-artifact`` refuses any path containing ``" : < > | * ?``
    or a newline, and until ``safe_doc_id`` landed the per-source documents
    were named after raw ids — so five committed files carried a colon and
    ``Backup and Recovery`` failed on its upload step on every scheduled run
    from 2026-09-13 to 2026-09-18.
    """

    def test_no_committed_api_path_is_illegal_on_ntfs(self) -> None:
        api_dir = ROOT / "api"
        if not api_dir.is_dir():
            self.skipTest("api/ is not present in this checkout")
        offenders = [
            str(path.relative_to(ROOT))
            for path in sorted(api_dir.rglob("*"))
            if any(SAFE_DOC_ID_CHARS.search(part) for part in path.relative_to(api_dir).parts)
        ]
        self.assertEqual(offenders, [])

    def test_source_documents_are_one_level_deep(self) -> None:
        sources_dir = ROOT / "api" / "v3" / "sources"
        if not sources_dir.is_dir():
            self.skipTest("api/v3/sources/ is not present in this checkout")
        stray = sorted(
            str(path.relative_to(sources_dir)) for path in sources_dir.rglob("*") if path.parent != sources_dir
        )
        self.assertEqual(stray, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
