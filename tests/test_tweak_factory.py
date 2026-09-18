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
from omnisource.apt_index import Relation
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
                # An empty catalogApp is the operator-only lane: a build that
                # never reaches a feed must not pretend to be a catalog app.
                if build.catalog_app:
                    self.assertIn(build.catalog_app, catalog_slugs, f"{build.slug}: catalogApp must be a catalog app")
                self.assertIn(build.base_app, registry.base_apps, f"{build.slug}: base app must exist in baseApps")
        self.assertTrue(any(build.is_bundle for build in registry.builds), "no bundle lane is registered")
        self.assertTrue(registry.conflict_groups, "a bundle without conflictGroups could mix mutually exclusive tweaks")

    def test_the_shipped_bundle_is_all_upstream_and_never_promoted(self) -> None:
        # Both properties are policy statements, so they are asserted rather
        # than documented and hoped for: a bundle may only collect from a
        # registered official upstream, and it may not be promoted into a feed.
        registry = tweak_factory.load_registry()
        bundles = [build for build in registry.builds if build.is_bundle]
        self.assertTrue(bundles, "the shipped registry curates no bundle")
        for build in bundles:
            with self.subTest(slug=build.slug):
                self.assertEqual(build.catalog_app, "", "a bundle may not be promoted into a feed")
                self.assertGreaterEqual(len(build.members), 2)
                for spec in build.members:
                    self.assertIn(spec.source, tweak_factory.DEB_SOURCES)
                    self.assertTrue(spec.repo or spec.index_url)

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

    def test_a_build_registers_either_one_deb_or_a_bundle(self) -> None:
        both = _registry_payload()
        both["builds"][0]["debs"] = [both["builds"][0]["deb"]]
        neither = _registry_payload()
        neither["builds"][0].pop("deb")
        empty = _registry_payload()
        empty["builds"][0]["deb"] = None
        empty["builds"][0]["debs"] = []
        for label, payload in (("both", both), ("neither", neither), ("empty list", empty)):
            with self.subTest(case=label), self.assertRaises(tweak_factory.FactoryError):
                _registry(payload)

    def test_bundle_members_are_shape_checked(self) -> None:
        def member(**overrides) -> dict:
            base = {
                "label": "youmod",
                "source": "github-release",
                "repo": "Tonwalter888/YouMod",
                "assetRegex": "^dev\\.water888\\.youmod_.+\\.deb$",
            }
            base.update(overrides)
            return base

        def with_members(members: list[dict]) -> dict:
            payload = _registry_payload()
            payload["builds"][0].pop("deb")
            payload["builds"][0]["debs"] = members
            return payload

        single = with_members([member()])
        _registry(single)  # a one-member bundle is legal, just pointless
        cases = {
            "duplicate labels": with_members([member(), member()]),
            "nine members": with_members([member(label=f"t{i}") for i in range(9)]),
            "apt member without a package": with_members(
                [
                    member(label="a"),
                    {"label": "b", "source": "apt-repository", "indexUrl": "https://r.example/Packages"},
                ]
            ),
            "apt index that is not a Packages file": with_members(
                [
                    member(label="a"),
                    {
                        "label": "b",
                        "source": "apt-repository",
                        "indexUrl": "https://r.example/html/index.html",
                        "package": "com.b",
                    },
                ]
            ),
            "apt member with both layouts": with_members(
                [
                    member(label="a"),
                    {
                        "label": "b",
                        "source": "apt-repository",
                        "indexUrl": "https://r.example/Packages",
                        "repo": "https://r.example",
                        "suite": "stable",
                        "package": "com.b",
                    },
                ]
            ),
            "unknown deb source": with_members([member(source="telegram-bot")]),
            "bad package id": with_members([member(packageId="Not_An_Id")]),
            "unpromotable catalog app": {
                **single,
                "builds": [{**single["builds"][0], "catalogApp": "nope-not-here"}],
            },
        }
        for label, payload in cases.items():
            with self.subTest(case=label), self.assertRaises(tweak_factory.FactoryError):
                _registry(payload)

    def test_conflict_groups_are_shape_checked(self) -> None:
        group = {"name": "primary-enhancer", "packages": ["com.a.one", "com.b.two"], "reason": "same settings host"}

        def with_groups(groups: object) -> dict:
            payload = _registry_payload()
            payload["conflictGroups"] = groups
            return payload

        ok = _registry(with_groups([group]))
        self.assertEqual(len(ok.conflict_groups), 1)
        self.assertEqual(ok.conflict_groups[0].packages, ("com.a.one", "com.b.two"))
        cases = {
            "one package": with_groups([{**group, "packages": ["com.a.one"]}]),
            "no reason": with_groups([{key: value for key, value in group.items() if key != "reason"}]),
            "duplicate name": with_groups([group, dict(group)]),
            "bad package id": with_groups([{**group, "packages": ["com.a.one", "not an id"]}]),
            "not an array": with_groups("nope"),
        }
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


def _bundle_payload(**overrides: object) -> dict[str, object]:
    """A two-member bundle: one GitHub-release tweak and one apt-only tweak."""
    payload = {
        "version": 1,
        "baseApps": {"youtube": _base_release_cfg()},
        "builds": [
            {
                "slug": "open-bundle",
                "name": "Open Bundle",
                "enabled": True,
                "catalogApp": "",
                "base": "youtube",
                "debs": [
                    {
                        "label": "youmod",
                        "source": "github-release",
                        "repo": "Tonwalter888/YouMod",
                        "packageId": "dev.water888.youmod",
                        "assetRegex": "^dev\\.water888\\.youmod_.+\\.deb$",
                    },
                    {
                        "label": "youpip",
                        "source": "apt-repository",
                        "indexUrl": "https://poomsmart.github.io/repo/Packages",
                        "package": "com.ps.youpip",
                    },
                ],
            }
        ],
    }
    payload.update(overrides)  # type: ignore[arg-type]
    return payload


_VERSIONS = {"youmod": "2.0.0", "youpip": "1.12.14", "overlay": "2.3.8"}


def _bundle_members(build: object, spec: object, **_kwargs: object) -> tweak_factory.Resolved:
    versions = _VERSIONS
    depends = () if spec.label == "youmod" else ("com.ps.ytvideooverlay",)
    return tweak_factory.Resolved(
        version=versions[spec.label],
        url=f"https://upstream.example/{spec.label}.deb",
        name=f"{spec.label}.deb",
        architecture="iphoneos-arm64",
        sha256="a" * 64 if spec.source == "apt-repository" else "",
        depends=depends,
        package_id=spec.package_id or spec.package,
    )


class BundlePlanTests(unittest.TestCase):
    """A bundle is one build over N tweaks: identity, conflicts and advisories."""

    def _plan(self, registry: tweak_factory.Registry, state: dict | None = None, **patches: object):
        getters = {
            "resolve_member": _bundle_members,
            "resolve_base": lambda *a, **k: tweak_factory.Resolved(
                version="youtube-21.36.6", url="https://me.example/base.ipa", tag="youtube-21.36.6"
            ),
            "release_exists": lambda *a, **k: False,
            "decide": lambda *a, **k: Decision(False, "", "", ""),
        }
        getters.update(patches)  # type: ignore[arg-type]
        with mock.patch.multiple(
            tweak_factory,
            **{
                name: (mock.MagicMock(side_effect=value) if callable(value) else mock.MagicMock(return_value=value))
                for name, value in getters.items()
            },
        ):
            return tweak_factory.plan_builds(registry, state or {}, token=None)  # type: ignore[arg-type]

    def test_a_bundle_hashes_its_members_into_one_release_tag(self) -> None:
        result = self._plan(_registry(_bundle_payload()))
        self.assertEqual(result["skipped"], [])
        entry = result["include"][0]
        self.assertEqual(entry["is_bundle"], "true")
        self.assertEqual(entry["member_count"], "2")
        self.assertRegex(entry["tag"], r"^tweak-build/open-bundle/v21\.36\.6-[0-9a-f]{8}$")
        self.assertRegex(entry["asset_name"], r"^OpenBundle-21\.36\.6-[0-9a-f]{8}\.ipa$")
        members = json.loads(entry["deb_entries"])
        self.assertEqual([member["label"] for member in members], ["youmod", "youpip"])
        self.assertEqual(members[1]["sha256"], "a" * 64)
        # The base app's version is part of the identity: a new dump rebuilds.
        self.assertEqual(entry["deb_version"].rsplit("-", 1)[0], "21.36.6")

    def test_the_tag_is_stable_and_moves_when_any_member_moves(self) -> None:
        registry = _registry(_bundle_payload())
        first = self._plan(registry)["include"][0]["tag"]
        second = self._plan(registry)["include"][0]["tag"]
        self.assertEqual(first, second)

        def bumped(build: object, spec: object, **kwargs: object) -> tweak_factory.Resolved:
            resolved = _bundle_members(build, spec, **kwargs)
            if spec.label == "youpip":
                resolved = tweak_factory.Resolved(**{**resolved.__dict__, "version": "1.12.15"})
            return resolved

        moved = self._plan(registry, resolve_member=bumped)["include"][0]["tag"]
        self.assertNotEqual(first, moved)

    def test_an_unchanged_published_bundle_is_skipped(self) -> None:
        registry = _registry(_bundle_payload())
        entry = self._plan(registry)["include"][0]
        state = {
            "builds": {
                "open-bundle": {
                    "tweakVersion": entry["deb_version"],
                    "debSha256": entry["bundle_sha256"],
                    "baseVersion": "youtube-21.36.6",
                    "baseURL": "https://me.example/base.ipa",
                }
            }
        }
        result = self._plan(registry, state, release_exists=lambda *a, **k: True)
        self.assertEqual(result["include"], [])
        self.assertEqual(len(result["uptodate"]), 1)
        # A recorded build whose members differ is stale even at the same base.
        stale = {
            "builds": {
                "open-bundle": {
                    "tweakVersion": "21.36.6-deadbeef",
                    "debSha256": "d" * 64,
                    "baseVersion": "youtube-21.36.6",
                    "baseURL": "https://me.example/base.ipa",
                }
            }
        }
        self.assertEqual(len(self._plan(registry, stale)["include"]), 1)

    def test_members_that_conflict_are_never_built(self) -> None:
        registry = _registry(
            _bundle_payload(
                conflictGroups=[
                    {
                        "name": "primary-enhancer",
                        "packages": ["dev.water888.youmod", "com.ps.youpip"],
                        "reason": "both patch the player controls",
                    }
                ]
            )
        )
        result = self._plan(registry)
        self.assertEqual(result["include"], [])
        reason = result["skipped"][0]["reason"]
        self.assertIn("mixes conflicting tweaks", reason)
        self.assertIn("both patch the player controls", reason)

    def test_a_blocked_member_url_skips_the_whole_bundle(self) -> None:
        def decide(url: str = "", **_kwargs: object) -> Decision:
            return (
                Decision(True, "cracked-package-repository", "cracked repo", "host evil.example")
                if "youpip" in url
                else Decision(False)
            )

        result = self._plan(_registry(_bundle_payload()), decide=decide)
        self.assertEqual(result["include"], [])
        self.assertIn("cracked-package-repository", result["skipped"][0]["reason"])

    def test_a_missing_dependency_is_reported_as_a_warning(self) -> None:
        result = self._plan(_registry(_bundle_payload()))
        self.assertTrue(result["warnings"])
        self.assertIn("com.ps.ytvideooverlay", result["warnings"][0]["reason"])
        text = tweak_factory.summary_markdown(result)
        self.assertIn("### Warnings", text)
        self.assertIn("bundle x2", text)

        # Adding the library as a member satisfies the dependency, and the plan
        # still builds: warnings never block, they inform.
        payload = _bundle_payload()
        payload["builds"][0]["debs"].append(  # type: ignore[index]
            {
                "label": "overlay",
                "source": "apt-repository",
                "indexUrl": "https://poomsmart.github.io/repo/Packages",
                "package": "com.ps.ytvideooverlay",
            }
        )
        widened = self._plan(_registry(payload))
        self.assertEqual(len(json.loads(widened["include"][0]["deb_entries"])), 3)
        self.assertEqual(widened["warnings"], [])

    def test_a_slice_mismatch_between_members_is_advisory(self) -> None:
        # Architecture is what the repository claims, not what the binary
        # contains, so this warns instead of refusing to build.
        def skewed(build: object, spec: object, **kwargs: object) -> tweak_factory.Resolved:
            resolved = _bundle_members(build, spec, **kwargs)
            if spec.label == "youpip":
                return tweak_factory.Resolved(**{**resolved.__dict__, "architecture": "iphoneos-arm"})
            return resolved

        result = self._plan(_registry(_bundle_payload()), resolve_member=skewed)
        self.assertEqual(len(result["include"]), 1, "an advisory must not stop a build")
        self.assertIn("different Architecture", result["warnings"][0]["reason"])

    def test_all_members_sharing_a_slice_is_silent(self) -> None:
        result = self._plan(_registry(_bundle_payload()))
        self.assertFalse([note for note in (result["warnings"] or []) if "Architecture" in note["reason"]])


class AptMemberResolutionTests(unittest.TestCase):
    def test_an_apt_index_entry_reaches_the_resolution(self) -> None:
        from omnisource.apt_index import DebFile

        registry = _registry(_bundle_payload())
        spec = registry.builds[0].members[1]
        deb = DebFile(
            package="com.ps.youpip",
            version="1.12.14",
            architecture="iphoneos-arm64",
            url="https://poomsmart.github.io/repo/debs/youtube/youpip/com.ps.youpip_1.12.14_iphoneos-arm64.deb",
            name="com.ps.youpip_1.12.14_iphoneos-arm64.deb",
            sha256="b" * 64,
            size=25552,
            depends=(Relation(names=("com.ps.ytvideooverlay",), constraint=">= 2.0.0"),),
        )
        with mock.patch.object(tweak_factory, "resolve_apt_deb", return_value=deb) as resolver:
            resolved = tweak_factory.resolve_member(registry.builds[0], spec, token=None)
        kwargs = resolver.call_args.kwargs
        self.assertEqual(kwargs["package"], "com.ps.youpip")
        self.assertEqual(kwargs["index_url"], "https://poomsmart.github.io/repo/Packages")
        self.assertEqual(kwargs["architectures"], ("iphoneos-arm64", "iphoneos-arm64e", "iphoneos-arm"))
        self.assertEqual(resolved.sha256, "b" * 64)
        self.assertEqual(resolved.package_id, "com.ps.youpip")
        self.assertEqual(resolved.depends, ("com.ps.ytvideooverlay",))
        self.assertEqual(resolved.architecture, "iphoneos-arm64")

    def test_an_unreadable_index_is_a_skip_with_the_upstream_message(self) -> None:
        from omnisource.apt_index import AptIndexError

        registry = _registry(_bundle_payload())
        spec = registry.builds[0].members[1]
        with (
            mock.patch.object(
                tweak_factory,
                "resolve_apt_deb",
                side_effect=AptIndexError("no readable Packages index at: https://x/Packages"),
            ),
            self.assertRaises(tweak_factory.FactoryError) as caught,
        ):
            tweak_factory.resolve_member(registry.builds[0], spec, token=None)
        self.assertIn("no readable Packages index", str(caught.exception))

    def test_a_pinned_release_tag_narrows_the_candidates(self) -> None:
        payload = _registry_payload()
        payload["builds"][0]["deb"]["pinVersion"] = "v5.2.2"  # type: ignore[index]
        registry = _registry(payload)
        spec = registry.builds[0].members[0]
        releases = [
            _release("v5.3.0", ["com.dvntm.ytlite_5.3.0_iphoneos-arm64.deb"]),
            _release("v5.2.2", ["com.dvntm.ytlite_5.2.2_iphoneos-arm64.deb"]),
        ]
        with mock.patch.object(tweak_factory, "_get_json", return_value=releases):
            resolved = tweak_factory.resolve_member(registry.builds[0], spec, token=None)
        self.assertEqual(resolved.version, "v5.2.2")
        unresolved = _registry(
            {
                **payload,
                "builds": [{**payload["builds"][0], "deb": {**payload["builds"][0]["deb"], "pinVersion": "v9.9.9"}}],
            }  # type: ignore[index]
        )
        with (
            mock.patch.object(tweak_factory, "_get_json", return_value=releases),
            self.assertRaises(tweak_factory.FactoryError),
        ):
            tweak_factory.resolve_deb(unresolved.builds[0], token=None)


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

    def _bundle_args(self, tmp: str, *, members: list[dict], **overrides: object) -> object:
        import argparse

        registry = _registry(_bundle_payload())
        build = registry.builds[0]
        manifest = tweak_factory.manifest_from_members(members)
        version = f"21.36.6-{manifest[:8]}"
        payload = {
            "slug": build.slug,
            "tag": f"{build.tag_prefix}/v{version}",
            "deb_url": "",
            "deb_version": version,
            "deb_sha256": "",
            "deb_name": "",
            "members_json": json.dumps(members),
            "base_app": "youtube",
            "base_version": "youtube-21.36.6",
            "base_url": "https://me.example/base.ipa",
            "asset_name": f"OpenBundle-{version}.ipa",
            "ipa": str(Path(tmp) / "out.ipa"),
            "fragment": str(Path(tmp) / "frag.json"),
            "run_url": "https://github.com/iamsmmh/OmniSource/actions/runs/9",
        }
        payload.update(overrides)
        Path(payload["ipa"]).write_bytes(b"bundle-ipa")
        return argparse.Namespace(**payload)

    def test_a_bundle_records_every_member_and_their_digests(self) -> None:
        members = [
            {
                "label": "youmod",
                "name": "youmod.deb",
                "version": "2.0.0",
                "url": "https://a.example/youmod.deb",
                "sha256": "1" * 64,
            },
            {
                "label": "youpip",
                "name": "youpip.deb",
                "version": "1.12.14",
                "url": "https://b.example/youpip.deb",
                "sha256": "2" * 64,
            },
        ]
        registry = _registry(_bundle_payload())
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tweak_factory, "load_registry", return_value=registry):
                fragment = tweak_factory.publish_fragment(self._bundle_args(tmp, members=members))  # type: ignore[arg-type]
            record = fragment["builds"]["open-bundle"]
            self.assertTrue(record["bundle"])
            self.assertEqual([member["label"] for member in record["members"]], ["youmod", "youpip"])
            self.assertEqual(record["debSha256"], tweak_factory.manifest_from_members(members))
            self.assertEqual(record["tweakVersion"], f"21.36.6-{record['debSha256'][:8]}")
            # The digest of the injected app is recorded next to the inputs.
            self.assertEqual(record["size"], len(b"bundle-ipa"))

    def test_a_bundle_cannot_record_a_different_tweak_set(self) -> None:
        members = [
            {
                "label": "youmod",
                "name": "youmod.deb",
                "version": "2.0.0",
                "url": "https://a.example/youmod.deb",
                "sha256": "1" * 64,
            },
            {
                "label": "youpip",
                "name": "youpip.deb",
                "version": "1.12.14",
                "url": "https://b.example/youpip.deb",
                "sha256": "2" * 64,
            },
        ]
        registry = _registry(_bundle_payload())
        cases = {
            # The run claims one release tag while recording different bytes:
            # the digest derived from the members no longer matches the tag.
            "a swapped url": json.dumps([{**members[0]}, {**members[1], "url": "https://evil.example/youpip.deb"}]),
            "a dropped member": json.dumps(members[:1]),
            "an extra member": json.dumps(
                [
                    *members,
                    {
                        "label": "third",
                        "name": "t.deb",
                        "version": "1",
                        "url": "https://c.example/t.deb",
                        "sha256": "3" * 64,
                    },
                ]
            ),
            "no members at all": "",
        }
        for label, members_json in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "out.ipa").write_bytes(b"bundle-ipa")
                args = self._bundle_args(tmp, members=members)  # type: ignore[arg-type]
                args.members_json = members_json
                with (
                    mock.patch.object(tweak_factory, "load_registry", return_value=registry),
                    self.assertRaises(tweak_factory.FactoryError),
                ):
                    tweak_factory.publish_fragment(args)  # type: ignore[arg-type]

    def test_a_bundle_and_a_single_tweak_use_the_right_flags(self) -> None:
        members = [
            {"label": "youmod", "version": "2.0.0", "url": "https://a.example/youmod.deb"},
            {"label": "youpip", "version": "1.12.14", "url": "https://b.example/youpip.deb"},
        ]
        registry = _registry(_bundle_payload())
        with tempfile.TemporaryDirectory() as tmp:
            args = self._bundle_args(tmp, members=members)
            # --deb-url is meaningless for a bundle and must be refused.
            args.deb_url = "https://a.example/youmod.deb"
            with (
                mock.patch.object(tweak_factory, "load_registry", return_value=registry),
                self.assertRaises(tweak_factory.FactoryError),
            ):
                tweak_factory.publish_fragment(args)  # type: ignore[arg-type]
            args.deb_url = ""
            with mock.patch.object(tweak_factory, "load_registry", return_value=registry):
                notes = tweak_factory.release_notes(args)  # type: ignore[arg-type]
            # ... while a single-tweak build must not smuggle in a member list.
            with tempfile.TemporaryDirectory() as inner:
                Path(inner, "x.ipa").write_bytes(b"x")
                with (
                    mock.patch.object(tweak_factory, "load_registry", return_value=_registry(_registry_payload())),
                    self.assertRaises(tweak_factory.FactoryError),
                ):
                    tweak_factory.publish_fragment(
                        self._args(ipa=str(Path(inner) / "x.ipa"), members_json=json.dumps(members))  # type: ignore[arg-type]
                    )
        self.assertIn("youmod 2.0.0", notes)
        self.assertIn("youpip 1.12.14", notes)
        self.assertIn("Bundle SHA-256", notes)
        self.assertIn("https://b.example/youpip.deb", notes)

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


class WorkflowContractTests(unittest.TestCase):
    """The YAML side of the factory cannot drift from the script contract.

    Parsed with regexes on purpose: the suite is stdlib-only (CI installs no
    PyYAML), and the workflows are flat enough that anchored text checks are
    the honest way to pin them.
    """

    _INJECT_INPUTS = ("app_name", "base_ipa_url", "bundle_id", "tweak_deb_url", "tweak_deb_urls_json", "tweak_name")

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

    def test_the_inject_workflow_handles_a_bundle_safely(self) -> None:
        text = self._inject()
        # The bundle list is parsed in exactly one place and nothing trusts it.
        self.assertIn("workspace/tweaks.tsv", text)
        self.assertIn("a bundle injects at most 8 tweaks", text)
        self.assertIn("duplicate tweak label", text)
        self.assertIn("url must be an https URL", text)
        # Every URL is policy-gated, not just the first, and every digest the
        # upstream published is verified before anything is injected.
        self.assertIn('check-url "${urls[@]}"', text)
        self.assertIn("does not match the digest the upstream published", text)
        # One cyan pass per deb, in a stable order, so a bad member fails the
        # build instead of producing an app nobody can attribute.
        self.assertIn("for deb in $(find debs -name '*.deb' -type f | sort)", text)
        self.assertIn("members_json=", text)
        self.assertIn("value: ${{ jobs.build.outputs.members_json }}", text)

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

    def test_the_factory_records_the_tweaks_actually_injected(self) -> None:
        text = self._factory()
        self.assertIn("tweak_deb_urls_json: ${{ matrix.deb_entries }}", text)
        self.assertIn("--members-json members.json", text)
        # The recorded digests are the ones cyan consumed. Re-downloading the
        # URL to hash it again would describe something other than what shipped.
        self.assertNotIn('--max-time 600 "${M_DEB_URL}" -o /tmp/tweak.deb', text)
        self.assertIn("MEMBERS_JSON: ${{ steps.inject.outputs.members_json }}", text)


class SchemaContractTests(unittest.TestCase):
    """The published schema must keep describing what the validator enforces.

    The schema is documentation (the runtime is stdlib-only), which makes it
    exactly the kind of file that silently rots; these checks are the cheap
    version of a code generator.
    """

    def _schema(self) -> dict:
        return json.loads((_ROOT / "schemas" / "tweak-builds.schema.json").read_text(encoding="utf-8"))

    def test_the_schema_documents_the_bundle_and_apt_lanes(self) -> None:
        schema = self._schema()
        self.assertIn("conflictGroups", schema["properties"])
        self.assertIn("definitions", schema)
        build = schema["properties"]["builds"]["items"]
        self.assertEqual(
            sorted(build["properties"]),
            sorted(["slug", "name", "enabled", "catalogApp", "note", "deb", "debs", "base", "bundleId", "appName", "publish"]),
        )
        reference = build["properties"]["deb"]
        self.assertIn("$ref", reference, "the member shape is shared by deb and debs")
        definition = str(reference["$ref"]).rsplit("/", 1)[-1]
        member = schema["definitions"][definition]
        self.assertEqual(build["properties"]["debs"]["items"]["$ref"], reference["$ref"])
        for field in ("label", "source", "indexUrl", "suite", "component", "package", "pinVersion", "packageId", "archPreference"):
            with self.subTest(field=field):
                self.assertIn(field, member["properties"])
        self.assertIn("apt-repository", str(member["properties"]["source"]["enum"]))

    def test_the_empty_catalog_app_is_documented_as_operator_only(self) -> None:
        schema = self._schema()
        description = schema["properties"]["builds"]["items"]["properties"]["catalogApp"]["description"]
        self.assertIn("operator-only", description)
        self.assertIn("feed", description)


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
