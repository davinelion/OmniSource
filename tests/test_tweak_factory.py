"""Tweak Factory: collect official debs, inject, publish — with provenance.

The factory is a scheduled pipeline, so its safety lives in three places and
each is pinned here:

* the registry is hand-curated and strictly validated (a typo must fail the
  plan, never ship a wrong build);
* resolution prefers the newest official release asset and every resolved URL
  passes the sourcing policy before anything is downloaded;
* the workflow contract (reusable inject workflow, matrix wiring, state
  merge) cannot silently drift because the contract tests read the YAML.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tweak_factory
from omnisource.source_policy import Decision


def _registry(payload: object) -> tweak_factory.Registry:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        path = Path(handle.name)
    try:
        return tweak_factory.load_registry(path)
    finally:
        path.unlink(missing_ok=True)


def _base_release_cfg() -> dict[str, object]:
    return {"source": "release", "repo": "me/base-ipas", "tagPrefix": "youtube-", "assetGlob": "*.ipa"}


def _registry_payload() -> dict[str, object]:
    return {
        "version": 1,
        "baseApps": {"youtube": _base_release_cfg()},
        "builds": [
            {
                "slug": "ytlite",
                "name": "YTLite",
                "enabled": True,
                "catalogApp": "ytlite",
                "deb": {
                    "source": "github-release",
                    "repo": "Dayanch96/YTLite",
                    "assetRegex": "^com\\.dvntm\\.ytlite_.+_iphoneos-(arm64|arm64e|arm)\\.deb$",
                    "archPreference": ["arm64", "arm64e", "arm"],
                },
                "base": "youtube",
            }
        ],
    }


def _release(tag: str, names: list[str]) -> dict[str, object]:
    return {
        "tag_name": tag,
        "draft": False,
        "assets": [{"name": name, "browser_download_url": f"https://example.com/{name}"} for name in names],
    }


class RegistryValidationTests(unittest.TestCase):
    def test_the_shipped_registry_is_valid_and_seeded_from_the_catalog(self) -> None:
        registry = tweak_factory.load_registry()
        self.assertTrue(registry.builds, "the factory registry curates no builds")
        catalog = json.loads((_ROOT / "catalog.json").read_text(encoding="utf-8"))
        catalog_slugs = {app["slug"] for app in catalog["apps"]}
        for build in registry.builds:
            with self.subTest(slug=build.slug):
                self.assertIn(build.slug, catalog_slugs, f"{build.slug} must be a catalog app")
                self.assertIn(build.base_app, registry.base_apps, f"{build.slug}: base app must exist in baseApps")

    def test_invalid_registries_are_rejected(self) -> None:
        def with_slug(slug: object) -> dict[str, object]:
            payload = _registry_payload()
            payload["builds"][0]["slug"] = slug  # type: ignore[index]
            return payload

        cases = {
            "bad slug": with_slug("Not_A_Slug"),
            "unknown base": None,
            "duplicate slug": None,
        }
        unknown = _registry_payload()
        unknown["builds"][0]["base"] = "missing"  # type: ignore[index]
        cases["unknown base"] = unknown
        duplicate = _registry_payload()
        duplicate["builds"] = [duplicate["builds"][0], dict(duplicate["builds"][0])]  # type: ignore[index]
        cases["duplicate slug"] = duplicate
        bad_regex = _registry_payload()
        bad_regex["builds"][0]["deb"]["assetRegex"] = "("  # type: ignore[index]
        cases["bad regex"] = bad_regex
        for label, payload in cases.items():
            with self.subTest(case=label), self.assertRaises(tweak_factory.FactoryError):
                _registry(payload)

    def test_base_app_sources_are_shape_checked(self) -> None:
        for cfg, ok in (
            ({"source": "release", "repo": "me/base", "tagPrefix": "yt-", "assetGlob": "*.ipa"}, True),
            ({"source": "release", "repo": "me/base"}, False),
            ({"source": "url", "url": "https://example.com/base.ipa"}, True),
            ({"source": "url", "url": "http://example.com/base.ipa"}, False),
            ({"source": "variable", "envVar": "BASE_URL"}, True),
            ({"source": "variable", "envVar": "not an env"}, False),
            ({"source": "teleport"}, False),
        ):
            with self.subTest(cfg=cfg):
                payload = _registry_payload()
                payload["baseApps"]["youtube"] = cfg  # type: ignore[index]
                if ok:
                    _registry(payload)
                else:
                    with self.assertRaises(tweak_factory.FactoryError):
                        _registry(payload)


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = _registry(_registry_payload())
        self.build = self.registry.builds[0]

    def test_the_newest_nondraft_release_asset_wins(self) -> None:
        releases = [
            _release("v1.2.2", ["com.dvntm.ytlite_1.2.2_iphoneos-arm64.deb"]),
            _release("v1.2.10", ["com.dvntm.ytlite_1.2.10_iphoneos-arm64.deb"]),
            _release("v1.2.9-draft", ["com.dvntm.ytlite_1.2.9_iphoneos-arm64.deb"]),
        ]
        releases[2]["draft"] = True
        with mock.patch.object(tweak_factory, "_get_json", return_value=releases):
            resolved = tweak_factory.resolve_deb(self.build, token=None)
        self.assertEqual(resolved.version, "v1.2.10")
        self.assertIn("1.2.10", resolved.url)

    def test_arm64_is_preferred_over_arm(self) -> None:
        release = _release(
            "v5.2.2",
            [
                "com.dvntm.ytlite_5.2.2_iphoneos-arm.deb",
                "com.dvntm.ytlite_5.2.2_iphoneos-arm64.deb",
                "com.dvntm.ytlite_5.2.2_iphoneos-arm64e.deb",
            ],
        )
        asset = tweak_factory._pick_asset(release, self.build.deb_regex, self.build.arch_preference)
        self.assertIsNotNone(asset)
        self.assertIn("arm64.deb", asset["name"])  # type: ignore[index]

    def test_base_resolution_matches_the_tag_prefix_and_glob(self) -> None:
        releases = [
            _release("other-1.0", ["base.ipa"]),
            _release("youtube-19.19.3", ["YouTube.ipa", "notes.txt"]),
        ]
        with mock.patch.object(tweak_factory, "_get_json", return_value=releases):
            resolved = tweak_factory.resolve_base("youtube", _base_release_cfg(), token=None)
        self.assertEqual(resolved.version, "youtube-19.19.3")
        self.assertEqual(resolved.url, "https://example.com/YouTube.ipa")

    def test_a_missing_base_repo_reads_as_configuration_guidance(self) -> None:
        def fail_404(url: str, *, token: str | None) -> object:
            raise tweak_factory.FactoryError("GitHub API 404 for /repos/me/base-ipas/releases")

        with (
            mock.patch.object(tweak_factory, "_get_json", side_effect=fail_404),
            self.assertRaises(tweak_factory.FactoryError) as caught,
        ):
            tweak_factory.resolve_base("youtube", _base_release_cfg(), token=None)
        self.assertIn("create it", str(caught.exception))


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = _registry(_registry_payload())
        self.deb = tweak_factory.Resolved(
            version="v5.2.2", url="https://example.com/ytlite.deb", name="ytlite.deb", tag="v5.2.2"
        )
        self.base = tweak_factory.Resolved(
            version="youtube-19", url="https://example.com/YouTube.ipa", tag="youtube-19"
        )

    def _plan(self, state: dict[str, object], *, force: bool = False, blocked: bool = False) -> dict[str, object]:
        def fake_get(url: str, *, token: str | None) -> object:
            if "/releases" in url:
                return [_release("v5.2.2", ["com.dvntm.ytlite_5.2.2_iphoneos-arm64.deb"])]
            return {"value": "https://example.com/x.ipa"}

        with (
            mock.patch.object(tweak_factory, "_get_json", side_effect=fake_get),
            mock.patch.object(tweak_factory, "resolve_deb", return_value=self.deb),
            mock.patch.object(tweak_factory, "resolve_base", return_value=self.base),
            mock.patch.object(tweak_factory, "release_exists", return_value=True),
            mock.patch.object(
                tweak_factory, "decide", return_value=Decision(blocked, "blocked-rule", "reason", "host")
            ),
        ):
            return tweak_factory.plan_builds(self.registry, state, token=None, force=force)  # type: ignore[arg-type]

    def test_an_unbuilt_tweak_is_included_in_the_matrix(self) -> None:
        result = self._plan({})
        self.assertEqual(len(result["include"]), 1)
        entry = result["include"][0]
        self.assertEqual(entry["slug"], "ytlite")  # type: ignore[index]
        self.assertEqual(entry["tag"], "tweak-build/ytlite/v5.2.2")  # type: ignore[index]
        self.assertEqual(entry["asset_name"], "YTLite-5.2.2.ipa")  # type: ignore[index]
        self.assertEqual(result["skipped"], [])

    def test_a_v_prefixed_upstream_tag_never_doubles_the_v(self) -> None:
        # Regression: YTLite tags its releases "v5.2.2"; the release tag must
        # stay "tweak-build/ytlite/v5.2.2", not ".../vv5.2.2".
        self.assertEqual(tweak_factory.build_tag(self.registry.builds[0], "v9.9.9"), "tweak-build/ytlite/v9.9.9")
        self.assertEqual(tweak_factory.asset_name_for(self.registry.builds[0], "v9.9.9"), "YTLite-9.9.9.ipa")

    def test_an_unchanged_published_build_is_skipped_without_force(self) -> None:
        state = {
            "builds": {
                "ytlite": {
                    "tweakVersion": "v5.2.2",
                    "debURL": self.deb.url,
                    "baseVersion": self.base.version,
                    "baseURL": self.base.url,
                }
            }
        }
        result = self._plan(state)  # type: ignore[arg-type]
        self.assertEqual(result["include"], [])
        self.assertEqual(len(result["uptodate"]), 1)
        # --force must rebuild anyway.
        result = self._plan(state, force=True)  # type: ignore[arg-type]
        self.assertEqual(len(result["include"]), 1)

    def test_a_changed_base_version_triggers_a_rebuild(self) -> None:
        state = {
            "builds": {
                "ytlite": {
                    "tweakVersion": "v5.2.2",
                    "debURL": self.deb.url,
                    "baseVersion": "youtube-OLD",
                    "baseURL": self.base.url,
                }
            }
        }
        result = self._plan(state)  # type: ignore[arg-type]
        self.assertEqual(len(result["include"]), 1)

    def test_a_policy_blocked_url_is_skipped_never_built(self) -> None:
        result = self._plan({}, blocked=True)
        self.assertEqual(result["include"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("blocked", result["skipped"][0]["reason"])  # type: ignore[index]

    def test_the_summary_renders_every_bucket(self) -> None:
        text = tweak_factory.summary_markdown(self._plan({}))
        self.assertIn("## Tweak Factory plan", text)
        self.assertIn("tweak-build/ytlite/v5.2.2", text)


class TweaksCatalogTests(unittest.TestCase):
    def test_the_shipped_registry_curates_a_selectable_tweaks_catalog(self) -> None:
        registry = tweak_factory.load_registry()
        ids = [spec.id for spec in registry.tweaks]
        self.assertIn("youmod", ids, "the uProVid picker needs the YouMod tweak")
        self.assertIn("ytlite", ids, "the uProVid picker needs the YTLite tweak")
        for spec in registry.tweaks:
            with self.subTest(tweak=spec.id):
                self.assertTrue(spec.repo, "every catalog tweak resolves from its own upstream")
                self.assertIsNotNone(spec.asset_regex)

    def test_invalid_tweak_entries_are_rejected(self) -> None:
        payload = _registry_payload()
        payload["tweaks"] = [
            {
                "id": "youmod",
                "name": "YouMod",
                "repo": "Tonwalter888/YouMod",
                "assetRegex": "^dev\\.water888\\.youmod_.+\\.deb$",
            }
        ]
        _registry(payload)  # the good shape must load

        bad_duplicate = _registry_payload()
        bad_duplicate["tweaks"] = [
            {"id": "youmod", "name": "A", "repo": "me/a", "assetRegex": ".*"},
            {"id": "youmod", "name": "B", "repo": "me/b", "assetRegex": ".*"},
        ]
        bad_repo = _registry_payload()
        bad_repo["tweaks"] = [{"id": "youmod", "name": "A", "repo": "not-a-repo", "assetRegex": ".*"}]
        bad_regex = _registry_payload()
        bad_regex["tweaks"] = [{"id": "youmod", "name": "A", "repo": "me/a", "assetRegex": "("}]
        for label, case in (("duplicate id", bad_duplicate), ("bad repo", bad_repo), ("bad regex", bad_regex)):
            with self.subTest(case=label), self.assertRaises(tweak_factory.FactoryError):
                _registry(case)

    def test_extra_debs_entries_are_shape_checked(self) -> None:
        payload = _registry_payload()
        payload["tweaks"] = [
            {"id": "youmod", "name": "YouMod", "repo": "Tonwalter888/YouMod", "assetRegex": ".*\\.deb$"}
        ]
        cases = {
            "id string": ["youmod"],
            "id object": [{"id": "youmod"}],
            "id object with pin": [{"id": "youmod", "version": "2.0.0"}],
            "custom url": [{"name": "Gonerino", "url": "https://example.com/Gonerino.deb"}],
        }
        for label, extra in cases.items():
            with self.subTest(case=label):
                payload["builds"][0]["extraDebs"] = extra
                registry = _registry(payload)
                self.assertEqual(len(registry.builds[0].extra_debs), 1)

        bad_cases = {
            "unknown id": ["nosuchtweak"],
            "http url": [{"name": "X", "url": "http://example.com/x.deb"}],
            "url without name": [{"url": "https://example.com/x.deb"}],
            "empty object": [{}],
        }
        for label, extra in bad_cases.items():
            with self.subTest(case=label), self.assertRaises(tweak_factory.FactoryError):
                payload["builds"][0]["extraDebs"] = extra
                _registry(payload)


class ExtraDebsPlanTests(unittest.TestCase):
    """Multi-tweak builds: the matrix carries every .deb, state detects changes."""

    def setUp(self) -> None:
        payload = _registry_payload()
        payload["tweaks"] = [
            {"id": "youmod", "name": "YouMod", "repo": "Tonwalter888/YouMod", "assetRegex": "youmod.*\\.deb$"},
            {"id": "ytlite", "name": "YTLite", "repo": "Dayanch96/YTLite", "assetRegex": "ytlite.*\\.deb$"},
        ]
        payload["builds"][0]["extraDebs"] = [
            {"id": "youmod"},
            {"name": "Gonerino", "url": "https://repo.example.debs.me/Gonerino.deb"},
        ]
        self.registry = _registry(payload)
        self.build = self.registry.builds[0]
        self.deb = tweak_factory.Resolved(
            version="v5.2.2", url="https://example.com/ytlite.deb", name="ytlite.deb", tag="v5.2.2"
        )
        self.base = tweak_factory.Resolved(
            version="youtube-19", url="https://example.com/YouTube.ipa", tag="youtube-19"
        )

    def _resolved_extras(self) -> list[tweak_factory.Resolved]:
        return [
            tweak_factory.Resolved(version="v2.0.0", url="https://example.com/youmod.deb", name="youmod.deb"),
            tweak_factory.Resolved(
                version="custom", url="https://repo.example.debs.me/Gonerino.deb", name="Gonerino.deb"
            ),
        ]

    def _plan(self, state: dict[str, object], *, force: bool = False) -> dict[str, object]:
        with (
            mock.patch.object(tweak_factory, "resolve_deb", return_value=self.deb),
            mock.patch.object(tweak_factory, "resolve_base", return_value=self.base),
            mock.patch.object(tweak_factory, "release_exists", return_value=True),
            mock.patch.object(tweak_factory, "decide", return_value=Decision(False, "", "", "")),
            mock.patch.object(tweak_factory, "resolve_extra_deb", side_effect=iter(self._resolved_extras())),
        ):
            return tweak_factory.plan_builds(self.registry, state, token=None, force=force)  # type: ignore[arg-type]

    def test_the_matrix_carries_every_extra_deb_newline_joined(self) -> None:
        entry = self._plan({})["include"][0]
        self.assertEqual(
            entry["extra_deb_urls"],
            "https://example.com/youmod.deb\nhttps://repo.example.debs.me/Gonerino.deb",
        )
        self.assertEqual(
            entry["extra_deb_labels"],
            "YouMod v2.0.0\nGonerino custom",
        )
        payload = json.loads(entry["extra_debs_json"])
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["name"], "YouMod")
        self.assertEqual(payload[1]["sha256"], "", "digests are filled in by the build job")

    def test_a_change_to_the_extra_selection_forces_a_rebuild(self) -> None:
        state = {
            "builds": {
                "ytlite": {
                    "tweakVersion": self.deb.version,
                    "debURL": self.deb.url,
                    "baseVersion": self.base.version,
                    "baseURL": self.base.url,
                    "extraDebs": [
                        {
                            "name": "YouMod",
                            "version": "v1.0.0",
                            "url": "https://example.com/old.deb",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            }
        }
        self.assertEqual(len(self._plan(state)["include"]), 1, "different extras must rebuild")

    def test_identical_including_extras_is_up_to_date(self) -> None:
        state = {
            "builds": {
                "ytlite": {
                    "tweakVersion": self.deb.version,
                    "debURL": self.deb.url,
                    "baseVersion": self.base.version,
                    "baseURL": self.base.url,
                    "extraDebs": [
                        {
                            "name": "YouMod",
                            "version": "v2.0.0",
                            "url": "https://example.com/youmod.deb",
                            "sha256": "a" * 64,
                        },
                        {
                            "name": "Gonerino",
                            "version": "custom",
                            "url": "https://repo.example.debs.me/Gonerino.deb",
                            "sha256": "b" * 64,
                        },
                    ],
                }
            }
        }
        result = self._plan(state)
        self.assertEqual(result["include"], [])
        self.assertEqual(len(result["uptodate"]), 1)


def _on(doc: dict) -> dict:
    """GitHub reads ``on:`` as the trigger map; PyYAML pre-1.2 reads ``True``."""
    return doc["on"] if isinstance(doc.get("on"), dict) else doc[True]


def _step(doc: dict, uses: str) -> dict:
    for step in doc["jobs"]["build"]["steps"]:
        if isinstance(step, dict) and step.get("uses") == uses:
            return step
    raise AssertionError(f"no step uses {uses}")


class PublishAndMergeTests(unittest.TestCase):
    def _args(self, **overrides: object) -> object:
        import argparse

        payload = {
            "slug": "ytlite",
            "tag": "tweak-build/ytlite/v5.2.2",
            "deb_url": "https://example.com/ytlite.deb",
            "deb_version": "v5.2.2",
            "deb_sha256": "a" * 64,
            "base_app": "youtube",
            "base_version": "youtube-19",
            "base_url": "https://example.com/YouTube.ipa",
            "asset_name": "YTLite-v5.2.2.ipa",
            "ipa": "",
            "fragment": "",
            "run_url": "https://github.com/iamsmmh/OmniSource/actions/runs/1",
            "deb_name": "ytlite.deb",
        }
        payload.update(overrides)
        return argparse.Namespace(**payload)

    def test_publish_records_digest_size_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp) / "out.ipa"
            ipa.write_bytes(b"ipa-bytes")
            args = self._args(ipa=str(ipa), fragment=str(Path(tmp) / "frag.json"))
            tweak_factory.publish_fragment(args)  # type: ignore[arg-type]
            first = (Path(tmp) / "frag.json").read_bytes()
            tweak_factory.publish_fragment(args)  # type: ignore[arg-type]
            second = (Path(tmp) / "frag.json").read_bytes()
            self.assertEqual(first, second)
            record = json.loads(first)["builds"]["ytlite"]
            self.assertEqual(record["size"], len(b"ipa-bytes"))
            self.assertEqual(record["sha256"], __import__("hashlib").sha256(b"ipa-bytes").hexdigest())

    def test_publish_rejects_a_tag_that_does_not_match_the_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp) / "out.ipa"
            ipa.write_bytes(b"x")
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.publish_fragment(self._args(ipa=str(ipa), tag="wrong/tag"))  # type: ignore[arg-type]
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.publish_fragment(self._args(ipa=str(ipa), slug="unknown"))  # type: ignore[arg-type]

    def test_merge_keeps_the_newest_fragment_and_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = {"version": 1, "builds": {"ytlite": {"tweakVersion": "v1", "builtAt": "2026-09-01"}}}
            new = {"version": 1, "builds": {"ytlite": {"tweakVersion": "v2", "builtAt": "2026-09-10"}}}
            for name, payload in (("old.json", old), ("new.json", new)):
                (Path(tmp) / name).write_text(json.dumps(payload), encoding="utf-8")
            out = Path(tmp) / "state.json"
            tweak_factory.merge_fragments([Path(tmp) / "old.json", Path(tmp) / "new.json"], out)
            first = out.read_bytes()
            self.assertIn("v2", out.read_text(encoding="utf-8"))
            tweak_factory.merge_fragments([Path(tmp) / "new.json", Path(tmp) / "old.json"], out)
            self.assertEqual(first, out.read_bytes(), "merge must be order-independent")

    def test_merge_with_no_fragments_reports_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps({"version": 1, "builds": {}}), encoding="utf-8")
            out = Path(tmp) / "state.json"
            self.assertFalse(tweak_factory.merge_fragments([empty], out))
            self.assertFalse(out.exists())

    def test_publish_records_extra_debs_and_notes_render_them(self) -> None:
        extras = [
            {
                "name": "YouMod",
                "version": "2.0.0",
                "url": "https://example.com/youmod.deb",
                "sha256": "c" * 64,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp) / "out.ipa"
            ipa.write_bytes(b"ipa-bytes")
            args = self._args(
                ipa=str(ipa),
                fragment=str(Path(tmp) / "frag.json"),
                extra_debs_json=json.dumps(extras),
            )
            tweak_factory.publish_fragment(args)  # type: ignore[arg-type]
            record = json.loads((Path(tmp) / "frag.json").read_bytes())["builds"]["ytlite"]
            self.assertEqual(record["extraDebs"], extras)
            notes = tweak_factory.release_notes(args)  # type: ignore[arg-type]
            self.assertIn("Extra tweak: YouMod 2.0.0", notes)
            self.assertIn("c" * 64, notes)

    def test_publish_rejects_an_extra_deb_without_a_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp) / "out.ipa"
            ipa.write_bytes(b"x")
            args = self._args(
                ipa=str(ipa),
                fragment=str(Path(tmp) / "frag.json"),
                extra_debs_json=json.dumps(
                    [{"name": "X", "version": "1", "url": "https://example.com/x.deb", "sha256": ""}]
                ),
            )
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.publish_fragment(args)  # type: ignore[arg-type]


def _fake_ipa(directory: Path, *, bundle_id: str = "com.google.ios.youtube", version: str = "21.37.4") -> Path:
    """A minimal valid IPA: a zip holding Payload/<App>.app/Info.plist."""
    import plistlib
    import zipfile

    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": "12345",
        "CFBundleDisplayName": "YouTube",
    }
    path = directory / "YouTube.ipa"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/YouTube.app/Info.plist", plistlib.dumps(info))
        archive.writestr("Payload/YouTube.app/YouTube", b"fake-binary")
    return path


class BuildCommandTests(unittest.TestCase):
    """The operator's `build` flow: base (local or link) + tweaks + options."""

    def _build_args(self, **overrides: object) -> object:
        import argparse

        payload = {
            "base": "",
            "tweaks": "",
            "custom_deb": None,
            "app_name": None,
            "bundle_id": "",
            "tag_prefix": "",
            "notes": "",
            "slug": "uprovid",
            "base_app": "youtube",
            "base_repo": "",
            "publish_base": False,
            "run": False,
            "save_registry": False,
            "factory": False,
            "matrix_out": None,
        }
        payload.update(overrides)
        return argparse.Namespace(**payload)

    def test_read_local_ipa_validates_the_plist_and_hashes_the_file(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            path = _fake_ipa(Path(tmp))
            base = tweak_factory.read_local_ipa(path)
            self.assertEqual(base["bundle_id"], "com.google.ios.youtube")
            self.assertEqual(base["version"], "21.37.4")
            self.assertEqual(base["build"], "12345")
            self.assertEqual(base["display_name"], "YouTube")
            self.assertEqual(base["size"], path.stat().st_size)
            self.assertEqual(base["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_read_local_ipa_rejects_non_ipa_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            not_a_zip = Path(tmp) / "not.zip"
            not_a_zip.write_bytes(b"plain text")
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.read_local_ipa(not_a_zip)
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.read_local_ipa(Path(tmp) / "missing.ipa")

    def test_selections_parse_all_none_dedup_and_customs(self) -> None:
        registry = tweak_factory.load_registry()
        selections = tweak_factory.selections_from_args(registry, "youmod,ytlite,all,none", [])
        self.assertEqual([s["id"] for s in selections], [])
        selections = tweak_factory.selections_from_args(
            registry, "youmod,youmod,ytlite", ["Gonerino=https://ex.com/g.deb"]
        )
        self.assertEqual([s["name"] for s in selections], ["YouMod", "YTLite", "Gonerino"])
        with self.assertRaises(tweak_factory.FactoryError):
            tweak_factory.selections_from_args(registry, "nosuchtweak", [])
        with self.assertRaises(tweak_factory.FactoryError):
            tweak_factory.selections_from_args(registry, "", ["Gonerino=ftp://ex.com/g.deb"])

    def test_run_build_is_a_read_only_plan_without_action_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ipa = _fake_ipa(tmp_path)
            plan_out = tmp_path / "plan.json"
            before = (tweak_factory.REGISTRY_PATH).read_bytes()
            by_id = {
                "youmod": tweak_factory.Resolved(
                    version="v2.0.0", url="https://example.com/youmod.deb", name="youmod.deb"
                ),
                "ytlite": tweak_factory.Resolved(
                    version="v5.2.2", url="https://example.com/ytlite.deb", name="ytlite.deb"
                ),
            }
            with mock.patch.object(tweak_factory, "resolve_tweak", side_effect=lambda spec, token: by_id[spec.id]):
                tweak_factory.run_build(self._build_args(base=str(ipa), tweaks="youmod,ytlite", matrix_out=plan_out))
            plan = json.loads(plan_out.read_text(encoding="utf-8"))
            self.assertEqual([t["name"] for t in plan["tweaks"]], ["YouMod", "YTLite"])
            self.assertEqual(plan["base"]["bundle_id"], "com.google.ios.youtube")
            self.assertEqual(plan["tag_prefix"], "tweak-build/uprovid")
            self.assertEqual(tweak_factory.REGISTRY_PATH.read_bytes(), before, "a dry run must not touch the registry")

    def test_run_build_dispatches_the_inject_workflow_with_every_deb(self) -> None:
        base_url = "https://dumps.example.me/YouTube_21.37.4.ipa"
        base_info = {
            "kind": "url",
            "url": base_url,
            "name": "YouTube_21.37.4.ipa",
            "size": 129607592,
            "sha256": "",
            "bundle_id": "com.google.ios.youtube",
            "version": "21.37.4",
            "build": "",
            "display_name": "YouTube",
        }
        by_id = {
            "youmod": tweak_factory.Resolved(version="v2.0.0", url="https://example.com/youmod.deb", name="youmod.deb"),
            "ytlite": tweak_factory.Resolved(version="v5.2.2", url="https://example.com/ytlite.deb", name="ytlite.deb"),
        }
        with (
            mock.patch.object(tweak_factory, "probe_remote_ipa", return_value=base_info),
            mock.patch.object(tweak_factory, "resolve_tweak", side_effect=lambda spec, token: by_id[spec.id]),
            mock.patch("shutil.which", return_value="/usr/bin/gh"),
            mock.patch.object(
                tweak_factory, "_gh", return_value=mock.Mock(stdout="https://github.com/x/runs/42")
            ) as gh_mock,
        ):
            tweak_factory.run_build(
                self._build_args(
                    base=base_url,
                    tweaks="youmod,ytlite",
                    custom_deb=["Gonerino=https://ex.com/g.deb"],
                    run=True,
                )
            )
        command = gh_mock.call_args[0][0]
        self.assertEqual(command[:3], ["workflow", "run", "build-tweak.yml"])
        flat = " ".join(command)
        self.assertIn(f"base_ipa_url={base_url}", flat)
        self.assertIn("tweak_deb_url=https://example.com/youmod.deb", flat)
        self.assertIn("extra_deb_urls=https://example.com/ytlite.deb\nhttps://ex.com/g.deb", flat)
        self.assertIn("app_name=uProVid", flat)

    def test_run_build_rejects_a_local_base_for_dispatch_without_a_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ipa = _fake_ipa(Path(tmp))
            by_id = {"youmod": tweak_factory.Resolved(version="v2.0.0", url="https://example.com/y.deb", name="y.deb")}
            with (
                mock.patch.object(tweak_factory, "resolve_tweak", side_effect=lambda spec, token: by_id[spec.id]),
                self.assertRaises(tweak_factory.FactoryError) as caught,
            ):
                tweak_factory.run_build(self._build_args(base=str(ipa), tweaks="youmod", run=True))
            self.assertIn("URL", str(caught.exception))

    def test_save_selection_upserts_the_registry_and_swaps_the_base_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "tweak-builds.json"
            reg_path.write_bytes(tweak_factory.REGISTRY_PATH.read_bytes())
            selections = [
                {"id": "youmod", "name": "YouMod", "repo": "Tonwalter888/YouMod", "url": ""},
                {"id": "ytlite", "name": "YTLite", "repo": "Dayanch96/YTLite", "url": ""},
            ]
            tweak_factory.save_selection(
                tweak_factory.load_registry(),
                reg_path,
                slug="uprovid",
                selections=selections,
                app_name="uProVid",
                bundle_id="com.iamsmmh.uprovid",
                tag_prefix="tweak-build/uprovid",
                base_key="youtube",
                base_url="https://dumps.example.me/YouTube.ipa",
                base_repo=None,
            )
            raw = json.loads(reg_path.read_text(encoding="utf-8"))
            entry = next(b for b in raw["builds"] if b["slug"] == "uprovid")
            self.assertEqual(entry["deb"]["repo"], "Tonwalter888/YouMod")
            self.assertEqual(entry["extraDebs"], [{"id": "ytlite"}])
            self.assertEqual(entry["publish"]["tagPrefix"], "tweak-build/uprovid")
            self.assertEqual(raw["baseApps"]["youtube"]["source"], "url")
            self.assertEqual(raw["baseApps"]["youtube"]["url"], "https://dumps.example.me/YouTube.ipa")
            tweak_factory.load_registry(reg_path)  # the written file must validate

    def test_save_selection_restores_the_registry_when_the_result_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "tweak-builds.json"
            before = tweak_factory.REGISTRY_PATH.read_bytes()
            reg_path.write_bytes(before)
            custom_only = [{"id": "", "name": "X", "repo": "", "url": "https://ex.com/x.deb"}]
            with self.assertRaises(tweak_factory.FactoryError):
                tweak_factory.save_selection(
                    tweak_factory.load_registry(),
                    reg_path,
                    slug="uprovid",
                    selections=custom_only,
                    app_name="uProVid",
                    bundle_id="",
                    tag_prefix="tweak-build/uprovid",
                    base_key="youtube",
                    base_url=None,
                    base_repo=None,
                )
            self.assertEqual(reg_path.read_bytes(), before, "an invalid selection must not corrupt the registry")


class WorkflowContractTests(unittest.TestCase):
    """The YAML side of the factory cannot drift from the script contract.

    Parsed with regexes on purpose: the suite is stdlib-only (CI installs no
    PyYAML), and the workflows are flat enough that anchored text checks are
    the honest way to pin them.
    """

    _INJECT_INPUTS = ("app_name", "base_ipa_url", "bundle_id", "extra_deb_urls", "tweak_deb_url", "tweak_name")

    def _factory(self) -> str:
        return (_ROOT / ".github" / "workflows" / "tweak-factory.yml").read_text(encoding="utf-8")

    def _inject(self) -> str:
        return (_ROOT / ".github" / "workflows" / "build-tweak.yml").read_text(encoding="utf-8")

    @staticmethod
    def _section(text: str, header: str, terminator: str) -> str:
        start = text.index(header)
        stop = text.find(terminator, start + len(header))
        return text[start : stop if stop != -1 else len(text)]

    def test_both_workflows_declare_a_name_and_jobs(self) -> None:
        for label, text in (("tweak-factory.yml", self._factory()), ("build-tweak.yml", self._inject())):
            with self.subTest(workflow=label):
                self.assertRegex(text, r"(?m)^name: ")
                self.assertRegex(text, r"(?m)^jobs:")
                self.assertIn("set -euo pipefail", text)

    def test_the_inject_workflow_is_reusable_with_matching_inputs(self) -> None:
        text = self._inject()
        dispatch_inputs = set(
            re.findall(r"(?m)^      (\w+):", self._section(text, "  workflow_dispatch:", "  workflow_call:"))
        )
        call = self._section(text, "  workflow_call:", "    outputs:")
        call_inputs = set(re.findall(r"(?m)^      (\w+):", call))
        self.assertEqual(
            sorted(dispatch_inputs),
            list(self._INJECT_INPUTS),
            "dispatch inputs changed - update the workflow_call mirror and this pin",
        )
        self.assertEqual(
            sorted(call_inputs),
            list(self._INJECT_INPUTS),
            "workflow_call must mirror the dispatch inputs exactly",
        )
        self.assertIn("value: ${{ jobs.build.outputs.safe_name }}", self._section(text, "    outputs:", "permissions:"))
        self.assertIn("safe_name: ${{ steps.sanitize.outputs.safe_name }}", text)

    def test_the_factory_calls_the_inject_workflow_with_every_input(self) -> None:
        factory = self._factory()
        step = self._section(factory, "uses: ./.github/workflows/build-tweak.yml", "      - name:")
        wired = set(re.findall(r"(?m)^          (\w+):", step))
        self.assertEqual(sorted(wired), list(self._INJECT_INPUTS), "the inject call must wire every input, by name")

    def test_the_factory_keeps_its_safety_rails(self) -> None:
        text = self._factory()
        self.assertIn("group: tweak-factory", text)
        self.assertRegex(text, r'cron: "\d+ \d+ \* \* \d"')
        self.assertIn("needs.plan.outputs.has_builds == 'true'", text)
        self.assertIn("source_policy check-url", text, "publishing must re-run the sourcing policy gate")
        self.assertIn("[skip ci]", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        # Matrix values may only appear as YAML mapping values (name/if/env:/with:),
        # never inside a run: script - those must read the M_* environment.
        offenders = [
            line
            for line in text.splitlines()
            if (
                "${{ matrix." in line
                and not line.lstrip().startswith("#")
                and not re.match(r"^\s*(?:- )?[A-Za-z_][\w.-]*:\s", line)
            )
        ]
        self.assertEqual(offenders, [], "matrix values must go through env:, never into run: scripts")

    def test_the_plan_script_is_the_only_matrix_source(self) -> None:
        self.assertIn("include: ${{ fromJSON(needs.plan.outputs.matrix).include }}", self._factory())


class CliTests(unittest.TestCase):
    def test_validate_accepts_the_shipped_registry(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "tweak_factory.py"), "validate"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("registry OK", result.stdout)

    def test_plan_is_a_pure_offline_report_for_an_unconfigured_base(self) -> None:
        # Without GH_TOKEN the plan still works: the deb resolves from the
        # public API and the unconfigured base app is a skip, not a crash.
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / "tweak_factory.py"), "plan"],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": __import__("os").environ["PATH"]},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = result.stdout + result.stderr
        self.assertIn("Tweak Factory plan", combined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
