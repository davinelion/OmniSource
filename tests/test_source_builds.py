"""Tests for the source-build lane (:mod:`omnisource.source_builds`).

Recipes exist for projects whose upstream publishes source but no attributable
binary, so the guarantees that matter here are: the revision is pinned and
digest-verified, the file stays machine-checkable, a rejected source cannot be
used as an input, and none of this leaks into what a client consumes. All
offline except the digest helper, which hashes a temp file.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnisource.constants import Paths
from omnisource.source_builds import (
    BUILDS_RELATIVE_PATH,
    candidate_findings,
    classify_build_system,
    digest_file,
    fetch_archive,
    has_ios_release,
    load_builds,
    main,
    parse_builds,
    plan,
    recipe_skeleton,
    recipe_violations,
    shipped_data_matches_schema,
    validate_builds,
    verify_archive,
)
from omnisource.validation import validate_source_builds

ROOT = Path(__file__).resolve().parents[1]


def _recipe(**overrides) -> dict:
    """One complete, valid recipe with a single field replaced."""
    recipe = {
        "slug": "demo-tweak",
        "name": "Demo Tweak",
        "kind": "tweak",
        "summary": "A tweak that upstream only ever ships as a .deb package.",
        "forge": "github",
        "repo": "someone/demo-tweak",
        "url": "https://github.com/someone/demo-tweak",
        "license": "GPL-3.0",
        "pin": {
            "ref": "1.2.3",
            "type": "tag",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "committedAt": "2026-09-15",
        },
        "source": {
            "archiveURL": "https://codeload.github.com/someone/demo-tweak/tar.gz/refs/tags/1.2.3",
            "sha256": "a" * 64,
            "bytes": 4096,
        },
        "build": {
            "system": "theos",
            "requirements": ["theos (set $THEOS)"],
            "commands": ["make package FINALPACKAGE=1"],
            "artifacts": ["packages/com.someone.demotweak_1.2.3_iphoneos-arm.deb"],
        },
        "signing": {"model": "none", "tools": [], "notes": "A .deb tweak is not code-signed."},
        "verification": {
            "method": "source-archive-sha256",
            "verifiedAt": "2026-09-15",
            "evidence": ["archive digest computed while reviewing the recipe"],
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(recipe.get(key), dict):
            recipe[key] = {**recipe[key], **value}
        else:
            recipe[key] = value
    return recipe


def _document(*recipes: dict) -> dict:
    return {
        "version": 1,
        "updated": "2026-09-15",
        "contract": {
            "scope": "Projects publishing source but no iOS artifact that could be attributed.",
            "provenance": "Pinned revision plus the digest of that revision's source archive.",
            "signing": "The builder signs with their own identity; this project never signs or hosts output.",
        },
        "builds": list(recipes) or [_recipe()],
    }


class ParseTests(unittest.TestCase):
    def test_accepts_a_well_formed_document(self) -> None:
        builds = parse_builds(_document())
        self.assertEqual(builds.error, "")
        self.assertEqual(len(builds.recipes), 1)
        self.assertEqual(validate_builds(_document()), [])

    def test_reports_the_first_structural_problem_instead_of_guessing(self) -> None:
        for document, needle in (
            ([], "expected a JSON object"),
            ({"version": 1, "updated": "2026-09-15"}, "'builds' is missing"),
            ({"version": 2, "builds": []}, "unsupported version"),
            ({**_document(), "builds": [{"name": "x"}]}, "has no slug"),
            ({**_document(), "builds": [{**_recipe(), "pin": []}]}, "'pin' must be an object"),
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, parse_builds(document).error)

    def test_a_missing_file_is_an_empty_lane_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            builds = load_builds(Path(tmp))
        self.assertEqual(builds.recipes, [])
        self.assertEqual(builds.error, "")

    def test_invalid_json_is_reported_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_builds.json").write_text("{oops", encoding="utf-8")
            self.assertIn("invalid JSON", load_builds(root).error)


class ValidateTests(unittest.TestCase):
    def _errors(self, **overrides) -> list[str]:
        return validate_builds(_document(_recipe(**overrides)))

    def test_a_short_digest_is_not_a_digest(self) -> None:
        self.assertTrue(any("sha256" in error for error in self._errors(source={"sha256": "deadbeef"})), "")

    def test_a_moving_pin_is_refused(self) -> None:
        errors = self._errors(pin={"ref": "main", "type": "branch"})
        self.assertTrue(any("branch" in error for error in errors), errors)

    def test_a_tag_name_is_not_a_commit(self) -> None:
        errors = self._errors(pin={"commit": "1.2.3"})
        self.assertTrue(any("40-character commit" in error for error in errors), errors)

    def test_the_size_that_was_hashed_must_be_recorded(self) -> None:
        self.assertTrue(any("source.bytes" in error for error in self._errors(source={"bytes": 0})))

    def test_an_app_recipe_must_name_an_installable_artifact(self) -> None:
        errors = self._errors(
            kind="app",
            build={"artifacts": ["build/App"]},
            signing={"model": "on-device", "tools": ["altstore"]},
        )
        self.assertTrue(any("iOS artifact" in error for error in errors), errors)

    def test_an_app_must_say_who_signs_it(self) -> None:
        errors = self._errors(kind="app", signing={"model": "none", "tools": []})
        self.assertTrue(any("signing model" in error for error in errors), errors)

    def test_a_tweak_deb_may_legitimately_sign_nothing(self) -> None:
        # Rootless tweak .debs are packaged, not code-signed; forcing a signing
        # step here would push authors to invent one.
        self.assertEqual(self._errors(kind="tweak", signing={"model": "none"}), [])

    def test_unsupported_forge_or_kind_is_rejected(self) -> None:
        self.assertTrue(any("forge must be" in e for e in self._errors(forge="sourcehouse")))
        self.assertTrue(any("kind must be" in e for e in self._errors(kind="firmware")))

    def test_http_is_never_accepted(self) -> None:
        errors = self._errors(url="http://github.com/someone/demo-tweak")
        self.assertTrue(any("https" in error for error in errors), errors)
        errors = self._errors(build={"docs": "http://example.com/build"})
        self.assertTrue(any("build.docs" in error for error in errors), errors)

    def test_duplicate_slugs_cannot_shadow_each_other(self) -> None:
        other = _recipe(slug="demo-tweak", repo="someone/other")
        self.assertTrue(any("duplicate slug" in e for e in validate_builds(_document(_recipe(), other))))

    def test_evidence_is_required_because_a_recipe_is_a_claim(self) -> None:
        errors = self._errors(verification={"evidence": []})
        self.assertTrue(any("evidence" in error for error in errors), errors)


class CrossCheckTests(unittest.TestCase):
    policy_document: ClassVar[dict] = {
        "version": 1,
        "rules": [
            {
                "id": "account-gated-decrypted-store",
                "description": "Login-walled decrypted app stores.",
                "reason": "nothing to verify",
                "hosts": ["armconverter.com"],
                "references": ["https://example.com/review"],
                "decidedAt": "2026-09-15",
            }
        ],
    }

    def _policy(self):
        from omnisource.source_policy import parse_policy

        return parse_policy(self.policy_document)

    def test_a_recipe_may_not_fetch_source_from_a_rejected_host(self) -> None:
        builds = parse_builds(
            _document(_recipe(source={"archiveURL": "https://armconverter.com/mirror/demo-tweak.tar.gz"}))
        )
        errors, _warnings = recipe_violations(builds, policy=self._policy())
        self.assertTrue(any("account-gated-decrypted-store" in error for error in errors), errors)

    def test_a_recipe_page_on_a_rejected_host_is_also_an_error(self) -> None:
        builds = parse_builds(_document(_recipe(url="https://armconverter.com/store/us", source={"sha256": "a" * 64})))
        errors, _warnings = recipe_violations(builds, policy=self._policy())
        self.assertTrue(errors, "expected the project URL to be checked too")

    def test_unblocked_recipes_pass_unchanged(self) -> None:
        errors, warnings = recipe_violations(parse_builds(_document()), policy=self._policy(), catalog={"apps": []})
        self.assertEqual((errors, warnings), ([], []))

    def test_an_app_cross_link_must_point_at_a_catalog_entry(self) -> None:
        builds = parse_builds(_document(_recipe(app="ghost-app")))
        errors, _warnings = recipe_violations(builds, policy=self._policy(), catalog={"apps": []})
        self.assertTrue(any("ghost-app" in error for error in errors), errors)

    def test_overlapping_with_the_catalog_is_a_warning_not_a_block(self) -> None:
        # A project can legitimately be published *and* be worth building from
        # source, so this is surfaced rather than refused.
        catalog = {"apps": [{"slug": "demo-app", "upstream": {"repo": "someone/demo-tweak"}}]}
        builds = parse_builds(_document(_recipe(app="demo-app")))
        errors, warnings = recipe_violations(builds, policy=self._policy(), catalog=catalog)
        self.assertEqual(errors, [])
        self.assertTrue(any("already publishes" in warning for warning in warnings), warnings)


class IntegrityTests(unittest.TestCase):
    def test_digest_file_matches_hashlib(self) -> None:
        payload = json.dumps(_document()).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.gz"
            path.write_bytes(payload)
            self.assertEqual(digest_file(path), hashlib.sha256(payload).hexdigest())

    def test_verify_archive_detects_a_different_tree(self) -> None:
        recipe = parse_builds(_document()).recipes[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download.tar.gz"
            path.write_bytes(b"something else entirely")
            ok, _actual, message = verify_archive(recipe, path)
        self.assertFalse(ok)
        self.assertIn("not the reviewed tree", message)

    def test_a_matching_digest_with_the_wrong_size_is_still_flagged(self) -> None:
        payload = b"x" * 10
        recipe = parse_builds(_document(_recipe(source={"sha256": hashlib.sha256(payload).hexdigest()}))).recipes[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download.tar.gz"
            path.write_bytes(payload)
            ok, _actual, message = verify_archive(recipe, path)
        self.assertTrue(ok)
        self.assertIn("size differs", message)

    def test_fetch_refuses_a_recipe_it_cannot_trust(self) -> None:
        recipe = parse_builds(_document(_recipe(source={"archiveURL": "ftp://example.com/x.tar.gz"}))).recipes[0]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            fetch_archive(recipe, Path(tmp))


class PlanTests(unittest.TestCase):
    def test_the_digest_is_checked_before_anything_is_built(self) -> None:
        lines = plan(parse_builds(_document()).recipes[0])
        check = next(index for index, line in enumerate(lines) if "sha256sum -c" in line)
        build = next(index for index, line in enumerate(lines) if "make package" in line)
        self.assertLess(check, build)

    def test_the_pinned_revision_and_exact_archive_are_named(self) -> None:
        text = "\n".join(plan(parse_builds(_document()).recipes[0]))
        self.assertIn("codeload.github.com/someone/demo-tweak/tar.gz/refs/tags/1.2.3", text)
        self.assertIn("tag 1.2.3@0123456789", text)

    def test_signing_is_an_instruction_never_a_command(self) -> None:
        for signing in (
            {"model": "none", "tools": [], "notes": "tweak .deb"},
            {"model": "on-device", "tools": ["altstore", "feather"], "notes": ""},
        ):
            with self.subTest(model=signing["model"]):
                lines = plan(parse_builds(_document(_recipe(signing=signing))).recipes[0])
                self.assertTrue(all(not line.startswith(("ldid", "codesign", "security")) for line in lines), lines)
                self.assertTrue(lines[-1].startswith("#"), lines[-1])
        signed = plan(parse_builds(_document(_recipe(signing={"model": "own-certificate"}))).recipes[0])
        self.assertIn("OmniSource does not sign", "\n".join(signed))

    def test_a_recipe_producing_no_app_says_so_in_the_plan(self) -> None:
        recipe = parse_builds(_document(_recipe(signing={"model": "none", "notes": "tweak .deb"}))).recipes[0]
        self.assertIn("no signing step", "\n".join(plan(recipe)))


class CandidateTests(unittest.TestCase):
    """The finder's judgement calls, which must stay evidence-based.

    Network probing lives in ``scripts/discovery/find_source_builds.py``; the
    decisions it makes about what it found live here, because those are the ones
    that decide whether a project is a recipe, a catalogue entry, or neither.
    """

    def test_a_makefile_is_theos_only_when_it_uses_theos(self) -> None:
        self.assertEqual(
            classify_build_system(["Makefile", "Tweak.x"], "include $(THEOS)/makefiles/common.mk"), "theos"
        )
        self.assertEqual(
            classify_build_system(["Makefile", "Cargo.toml", "src/main.rs"], "all:\n\tcargo build"), "cargo"
        )
        self.assertEqual(classify_build_system(["Makefile"], "all:\n\tgcc -o tool main.c"), "make")

    def test_known_ecosystem_files_are_recognised(self) -> None:
        self.assertEqual(classify_build_system(["Package.swift", "Sources"]), "swift")
        self.assertEqual(classify_build_system(["dub.json", "source"]), "dub")
        self.assertEqual(classify_build_system(["App.xcodeproj/project.pbxproj"]), "xcodebuild")
        self.assertEqual(classify_build_system(["README.md"]), "")

    def test_only_ios_assets_count_as_a_published_release(self) -> None:
        published, names = has_ios_release({"assets": [{"name": "App.ipa"}, {"name": "App.dmg"}]})
        self.assertTrue(published)
        self.assertEqual(names, ("App.ipa",))
        self.assertEqual(has_ios_release({"assets": [{"name": "App.dmg"}]}), (False, ()))
        self.assertEqual(has_ios_release(None), (False, ()))
        self.assertEqual(has_ios_release({"assets": [{"name": "tweak_1.0_iphoneos-arm.deb"}]})[0], True)

    def test_a_draft_leaves_every_judgement_to_the_reviewer(self) -> None:
        skeleton = recipe_skeleton(
            forge="github",
            repo="someone/demo",
            ref="1.0",
            commit="0" * 40,
            committed_at="2026-09-15",
            system="theos",
            archive_url="https://codeload.github.com/someone/demo/tar.gz/refs/tags/1.0",
            sha256="b" * 64,
            size=1234,
        )
        self.assertEqual(skeleton["slug"], "demo")
        self.assertEqual(skeleton["kind"], "tweak", "a theos project is a tweak until proven otherwise")
        self.assertEqual(skeleton["pin"]["type"], "tag")
        self.assertEqual(skeleton["verification"]["method"], "source-archive-sha256")
        # Invented prose is the failure mode: a draft arrives with the blanks left
        # for the reviewer, never with a guessed summary or a made-up artifact.
        self.assertEqual(skeleton["summary"], "")
        self.assertEqual(skeleton["notes"], "")
        self.assertEqual(skeleton["build"]["artifacts"], [])
        self.assertEqual(skeleton["verification"]["evidence"], [])
        self.assertEqual(skeleton["verification"]["verifiedAt"], "")

    def test_a_commit_pin_is_recorded_as_a_commit(self) -> None:
        skeleton = recipe_skeleton(
            forge="github",
            repo="someone/demo",
            ref="main",
            commit="c" * 40,
            committed_at="2026-09-15",
            ref_type="commit",
        )
        self.assertEqual(skeleton["pin"]["type"], "commit")
        # A draft may not claim a tag nobody resolved.
        claimed = recipe_skeleton(
            forge="github", repo="someone/demo", ref="c" * 40, commit="c" * 40, committed_at="2026-09-15"
        )
        self.assertEqual(claimed["pin"]["type"], "commit")

    def test_the_finder_applies_the_sourcing_policy(self) -> None:
        # The finder must not suggest a repository the project already rejected.
        text = (ROOT / "scripts" / "discovery" / "find_source_builds.py").read_text(encoding="utf-8")
        self.assertIn("load_policy", text)
        self.assertIn("decide(", text)
        self.assertIn("refusing to run", text, "an unusable policy must stop the run (fail-closed)")

    def test_coverage_reports_the_lanes_this_file_does_not_prove(self) -> None:
        info = candidate_findings(load_builds(ROOT))
        self.assertEqual(info["count"], len(load_builds(ROOT).recipes))
        self.assertIn("gitlab", info["uncoveredForges"] + list(info["byForge"]))


class ShippedDataTests(unittest.TestCase):
    def test_shipped_recipes_are_valid(self) -> None:
        document = json.loads((ROOT / BUILDS_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(validate_builds(document), [])

    def test_shipped_recipes_stay_inside_the_declared_schema(self) -> None:
        self.assertEqual(shipped_data_matches_schema(ROOT), [])

    def test_shipped_recipes_are_policy_clean_and_cross_linked(self) -> None:
        from omnisource.source_policy import load_policy

        catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
        errors, _warnings = recipe_violations(load_builds(ROOT), policy=load_policy(ROOT), catalog=catalog)
        self.assertEqual(errors, [])

    def test_every_recipe_records_when_and_how_it_was_verified(self) -> None:
        for recipe in load_builds(ROOT).recipes:
            with self.subTest(slug=recipe.slug):
                self.assertRegex(recipe.pin.committed_at, r"^\d{4}-\d{2}-\d{2}$")
                self.assertRegex(recipe.verification.get("verifiedAt", ""), r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(recipe.verification.get("evidence"))
                self.assertTrue(recipe.notes, "a recipe must say why it is not a catalogue entry")

    def test_recipes_are_a_separate_lane_from_the_published_apps(self) -> None:
        # Nothing in this file is published as an OmniSource build: only recipes
        # that explicitly cross-link a catalogue entry may name a published slug.
        with (ROOT / "catalog.json").open(encoding="utf-8") as handle:
            catalog_slugs = {app["slug"] for app in json.load(handle)["apps"]}
        for recipe in load_builds(ROOT).recipes:
            with self.subTest(slug=recipe.slug):
                if recipe.app:
                    self.assertIn(recipe.app, catalog_slugs)
                else:
                    self.assertNotIn(recipe.slug, catalog_slugs)


class ValidatorWiringTests(unittest.TestCase):
    def test_the_shipped_tree_is_clean(self) -> None:
        report = validate_source_builds(Paths.from_root(ROOT), {"apps": []})
        self.assertEqual(report.errors, [])

    def test_validator_warns_when_the_schema_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_builds.json").write_text(json.dumps(_document()), encoding="utf-8")
            report = validate_source_builds(Paths.from_root(root), {"apps": []})
        self.assertEqual(report.errors, [])
        self.assertTrue(any("schema-checkable" in warning for warning in report.warnings), report.warnings)

    def test_validator_reports_a_broken_recipe_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            broken = _document(_recipe(source={"sha256": "not-a-digest"}))
            (root / "data" / "source_builds.json").write_text(json.dumps(broken), encoding="utf-8")
            report = validate_source_builds(Paths.from_root(root), {"apps": []})
        self.assertTrue(any("sha256" in error for error in report.errors), report.errors)

    def test_validator_fails_closed_on_an_unparseable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_builds.json").write_text('{"builds": 7}', encoding="utf-8")
            report = validate_source_builds(Paths.from_root(root), {"apps": []})
        self.assertTrue(report.errors, "a recipe file nobody can parse must not read as 'no recipes'")


class CliTests(unittest.TestCase):
    def _run(self, *argv: str) -> tuple[int, str]:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()

    def test_check_passes_against_the_shipped_repo(self) -> None:
        code, text = self._run("--root", str(ROOT), "check")
        self.assertEqual(code, 0, text)
        self.assertIn("policy-clean", text)

    def test_list_prints_one_line_per_recipe(self) -> None:
        code, text = self._run("--root", str(ROOT), "list")
        self.assertEqual(code, 0)
        shipped = load_builds(ROOT)
        self.assertEqual(len([line for line in text.splitlines() if line.strip()]), len(shipped.recipes) + 1)

    def test_show_prints_the_reviewed_object_untouched(self) -> None:
        code, text = self._run("--root", str(ROOT), "show", shipped_first_slug())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["slug"], shipped_first_slug())

    def test_unknown_slug_is_a_failure_not_an_empty_success(self) -> None:
        code, _text = self._run("--root", str(ROOT), "plan", "no-such-recipe")
        self.assertEqual(code, 1)

    def test_an_unusable_file_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir(parents=True)
            (root / "data" / "source_builds.json").write_text("{", encoding="utf-8")
            code, _text = self._run("--root", str(root), "check")
        self.assertEqual(code, 2)

    def test_hash_digests_any_file_for_recipe_authoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.tar.gz"
            payload = b"source tree bytes"
            path.write_bytes(payload)
            code, text = self._run("hash", str(path))
        self.assertEqual(code, 0)
        self.assertEqual(text.strip(), hashlib.sha256(payload).hexdigest())


def shipped_first_slug() -> str:
    return load_builds(ROOT).recipes[0].slug


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
