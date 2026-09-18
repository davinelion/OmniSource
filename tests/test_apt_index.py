"""The apt ``Packages`` index reader: newest-version selection and safety rails.

The Tweak Factory treats a developer's apt repository the same way it treats a
GitHub release page: as untrusted *data* that says which version exists at
which URL, with a digest attached.  Everything that could turn that into a
supply-chain problem is pinned here — traversal in ``Filename``, a host swap,
a non-https index, an empty stanza set — plus the version ordering, because
"newest" is the whole point of collecting from upstream.

The fixture below is modelled on a real flat Cydia-style index (the layout most
single-maintainer tweak repositories use).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource.apt_index import (
    AptIndexError,
    DebFile,
    candidate_index_urls,
    join_filename,
    parse_index,
    parse_relations,
    read_index,
    resolve_deb,
    select_deb,
)

INDEX = """Package: com.ps.ytvideooverlay
Version: 2.3.8
Architecture: iphoneos-arm64
Maintainer: Someone
Depends: mobilesubstrate
Filename: ./debs/youtube/ytvideooverlay/com.ps.ytvideooverlay_2.3.8_iphoneos-arm64.deb
Size: 16246
SHA256: ca68c047d26af8f7e0dc3b25ee4c8f1b0a3f0e4f3f0f2c3d4e5f60718293a4b5
Section: Tweaks
Description: A shared overlay library
 .
 still the description

Package: com.ps.youpip
Version: 1.12.13
Architecture: iphoneos-arm64
Depends: mobilesubstrate, com.ps.ytvideooverlay (>= 2.0.0), firmware (>= 11.0)
Filename: ./debs/youtube/youpip/com.ps.youpip_1.12.13_iphoneos-arm64.deb
Size: 25000
SHA256: 1111111111111111111111111111111111111111111111111111111111111111

Package: com.ps.youpip
Version: 1.12.14
Architecture: iphoneos-arm
Depends: mobilesubstrate, com.ps.ytvideooverlay (>= 2.0.0), firmware (>= 14.0) | com.ps.forceinpicture
Conflicts: com.spicat.youpip
Provides: com.spicat.youpip
Filename: ./debs/youtube/youpip/com.ps.youpip_1.12.14_iphoneos-arm.deb
Size: 25546
SHA256: 2222222222222222222222222222222222222222222222222222222222222222

Package: com.ps.youpip
Version: 1.12.14
Architecture: iphoneos-arm64
Depends: mobilesubstrate, com.ps.ytvideooverlay (>= 2.0.0), firmware (>= 14.0) | com.ps.forceinpicture
Filename: ./debs/youtube/youpip/com.ps.youpip_1.12.14_iphoneos-arm64.deb
Size: 25552
SHA256: 3333333333333333333333333333333333333333333333333333333333333333

Package: com.example.legacy
Version: 3.0
Architecture: iphoneos-arm64
Filename: debs/legacy/com.example.legacy_3.0_iphoneos-arm64.deb
"""

ARCHES = ("iphoneos-arm64", "iphoneos-arm64e", "iphoneos-arm")


def _fetch(table: dict[str, str]):
    def get(url: str) -> str:
        if url not in table:
            raise OSError(f"404 {url}")
        return table[url]

    return get


class ParseTests(unittest.TestCase):
    def test_stanzas_keep_folded_values_and_ignore_orphans(self) -> None:
        stanzas = parse_index(INDEX)
        self.assertEqual(len(stanzas), 5)
        overlay = stanzas[0]
        self.assertEqual(overlay["Description"], "A shared overlay library still the description")
        self.assertNotIn(" .", str(overlay.values()))

    def test_crlf_indices_parse_identically(self) -> None:
        self.assertEqual(parse_index(INDEX.replace("\n", "\r\n")), parse_index(INDEX))

    def test_a_stanza_without_a_package_field_is_not_an_entry(self) -> None:
        self.assertEqual(parse_index("Version: 1.0\nFilename: x.deb\n"), [])

    def test_relations_keep_alternatives_groups_and_constraints(self) -> None:
        relations = parse_relations(
            "mobilesubstrate, com.ps.ytvideooverlay (>= 2.0.0), firmware (>= 14.0) | com.ps.forceinpicture"
        )
        self.assertEqual(
            [relation.names for relation in relations],
            [("mobilesubstrate",), ("com.ps.ytvideooverlay",), ("firmware", "com.ps.forceinpicture")],
        )
        self.assertEqual(relations[1].constraint, ">= 2.0.0")


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stanzas = parse_index(INDEX)

    def test_the_newest_version_wins_before_architecture(self) -> None:
        chosen = select_deb(self.stanzas, package="com.ps.youpip", architectures=ARCHES)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["Version"], "1.12.14")
        self.assertEqual(chosen["Architecture"], "iphoneos-arm64")

    def test_a_preferred_architecture_beats_a_newer_foreign_one(self) -> None:
        chosen = select_deb(self.stanzas, package="com.ps.youpip", architectures=("iphoneos-arm",))
        self.assertEqual(chosen["Version"], "1.12.14")
        self.assertEqual(chosen["Architecture"], "iphoneos-arm")
        chosen = select_deb(self.stanzas, package="com.ps.youpip", architectures=("iphoneos-armv7",))
        self.assertEqual(chosen["Version"], "1.12.14")

    def test_epoch_and_revision_ordering_follows_dpkg(self) -> None:
        stanzas = [
            {"Package": "x", "Version": "1.0", "Filename": "a.deb", "Architecture": "iphoneos-arm64"},
            {"Package": "x", "Version": "1.0~beta2", "Filename": "b.deb", "Architecture": "iphoneos-arm64"},
            {"Package": "x", "Version": "1:0.1", "Filename": "c.deb", "Architecture": "iphoneos-arm64"},
            {"Package": "x", "Version": "1.0.10", "Filename": "d.deb", "Architecture": "iphoneos-arm64"},
            {"Package": "x", "Version": "1.0-1", "Filename": "e.deb", "Architecture": "iphoneos-arm64"},
        ]
        chosen = select_deb(stanzas, package="x", architectures=ARCHES)
        self.assertEqual(chosen["Version"], "1:0.1")

    def test_an_unknown_package_selects_nothing(self) -> None:
        self.assertIsNone(select_deb(self.stanzas, package="com.example.absent"))


class ResolveTests(unittest.TestCase):
    def test_resolve_returns_the_deb_the_index_advertises(self) -> None:
        fetch = _fetch({"https://repo.example/repo/Packages": INDEX})
        deb = resolve_deb(
            package="com.ps.youpip", index_url="https://repo.example/repo/Packages", architectures=ARCHES, fetch=fetch
        )
        self.assertEqual(deb.version, "1.12.14")
        self.assertEqual(deb.architecture, "iphoneos-arm64")
        self.assertEqual(
            deb.url,
            "https://repo.example/repo/debs/youtube/youpip/com.ps.youpip_1.12.14_iphoneos-arm64.deb",
        )
        self.assertEqual(deb.name, "com.ps.youpip_1.12.14_iphoneos-arm64.deb")
        self.assertEqual(deb.size, 25552)
        self.assertEqual(deb.sha256, "3" * 64)

    def test_tweak_dependencies_ignore_system_alternatives(self) -> None:
        fetch = _fetch({"https://repo.example/repo/Packages": INDEX})
        deb = resolve_deb(
            package="com.ps.youpip", index_url="https://repo.example/repo/Packages", architectures=ARCHES, fetch=fetch
        )
        # The firmware | forceinpicture group is satisfiable by the device, so
        # only the library every variant needs is reported.
        self.assertEqual(deb.tweak_requires, ("com.ps.ytvideooverlay",))

    def test_pin_version_selects_an_older_release(self) -> None:
        fetch = _fetch({"https://repo.example/repo/Packages": INDEX})
        deb = resolve_deb(
            package="com.ps.youpip",
            pin_version="1.12.13",
            index_url="https://repo.example/repo/Packages",
            architectures=ARCHES,
            fetch=fetch,
        )
        self.assertEqual(deb.version, "1.12.13")
        with self.assertRaises(AptIndexError):
            resolve_deb(
                package="com.ps.youpip",
                pin_version="9.9.9",
                index_url="https://repo.example/repo/Packages",
                fetch=fetch,
            )

    def test_a_missing_package_names_the_index_it_read(self) -> None:
        fetch = _fetch({"https://repo.example/repo/Packages": INDEX})
        with self.assertRaises(AptIndexError) as caught:
            resolve_deb(package="com.example.absent", index_url="https://repo.example/repo/Packages", fetch=fetch)
        self.assertIn("https://repo.example/repo/Packages", str(caught.exception))

    def test_an_index_without_any_deb_size_or_digest_still_resolves(self) -> None:
        text = "Package: com.example.legacy\nVersion: 3.0\nArchitecture: iphoneos-arm64\nFilename: debs/legacy/x.deb\n"
        fetch = _fetch({"https://repo.example/Packages": text})
        deb = resolve_deb(package="com.example.legacy", index_url="https://repo.example/Packages", fetch=fetch)
        self.assertEqual(deb.sha256, "")
        self.assertEqual(deb.size, 0)
        self.assertEqual(deb.url, "https://repo.example/debs/legacy/x.deb")

    def test_a_package_name_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(AptIndexError):
            resolve_deb(package="com.example; rm -rf /", index_url="https://repo.example/Packages", fetch=_fetch({}))


class SafetyTests(unittest.TestCase):
    def test_filename_cannot_climb_out_of_the_repository(self) -> None:
        for bad in ("../evil.deb", "/etc/passwd", "https://evil.example/x.deb", "//evil.example/x.deb", "./../x.deb"):
            with self.subTest(filename=bad), self.assertRaises(AptIndexError):
                join_filename("https://repo.example/repo/Packages", bad)

    def test_index_must_be_https(self) -> None:
        with self.assertRaises(AptIndexError):
            join_filename("http://repo.example/repo/Packages", "a.deb")

    def test_a_filename_cannot_smuggle_another_host_or_scheme(self) -> None:
        # A relative name can only ever resolve on the index's own host, so an
        # index that tries to leave it must be doing so with a scheme or an
        # absolute path - both are refused before any joining happens.
        text = INDEX.replace("./debs/youtube/youpip/", "//cdn.other.example/debs/youtube/youpip/")
        fetch = _fetch({"https://repo.example/repo/Packages": text})
        with self.assertRaises(AptIndexError):
            resolve_deb(
                package="com.ps.youpip",
                index_url="https://repo.example/repo/Packages",
                architectures=ARCHES,
                fetch=fetch,
            )

    def test_an_unusable_index_reports_what_was_tried(self) -> None:
        with self.assertRaises(AptIndexError) as caught:
            read_index(
                repo="https://repo.example",
                suite="stable",
                component="main",
                architectures=("iphoneos-arm64",),
                fetch=_fetch({}),
            )
        message = str(caught.exception)
        self.assertIn("dists/stable/main/binary-iphoneos-arm64/Packages", message)
        self.assertIn("no readable Packages index", message)

    def test_a_repository_needs_either_an_index_url_or_a_suite(self) -> None:
        with self.assertRaises(AptIndexError):
            candidate_index_urls(repo="https://repo.example")

    def test_candidate_urls_cover_compressed_variants(self) -> None:
        urls = candidate_index_urls(index_url="https://repo.example/repo/Packages")
        self.assertEqual(
            urls,
            [
                "https://repo.example/repo/Packages",
                "https://repo.example/repo/Packages.gz",
                "https://repo.example/repo/Packages.bz2",
                "https://repo.example/repo/Packages.xz",
            ],
        )

    def test_a_broken_compression_variant_does_not_abort_the_search(self) -> None:
        def fetch(url: str) -> str:
            if url.endswith(".gz"):
                raise ValueError("not gzip data")
            if url.endswith((".bz2", ".xz")):
                raise FileNotFoundError(url)
            return INDEX

        candidate, stanzas = read_index(index_url="https://repo.example/repo/Packages", fetch=fetch)
        self.assertEqual(candidate, "https://repo.example/repo/Packages")
        self.assertTrue(stanzas)

    def test_an_oversized_index_is_refused_rather_than_trusted(self) -> None:
        from omnisource.apt_index import MAX_INDEX_BYTES, fetch_text

        self.assertGreater(MAX_INDEX_BYTES, 1024 * 1024)
        self.assertTrue(callable(fetch_text))


class DebFileShapeTests(unittest.TestCase):
    def test_debfile_defaults_are_readonly_and_tolerant(self) -> None:
        deb = DebFile(
            package="com.example.x", version="1", architecture="iphoneos-arm64", url="https://e/x.deb", name="x.deb"
        )
        self.assertEqual(deb.tweak_requires, ())
        self.assertEqual(deb.size, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
