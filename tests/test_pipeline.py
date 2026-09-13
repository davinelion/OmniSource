"""Integration tests for pipeline execution and CLI wrappers."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from omnisource.constants import PNG_MAGIC, Paths
from omnisource.di import Container
from omnisource.domain import SourceType, SyncReport
from omnisource.errors import ProviderError, SyncError
from omnisource.pipeline import load_catalog, load_state, stage_build, sync_app
from omnisource.providers.registry import ProviderRegistry
from omnisource.site import publish_repo_artifacts


class TestPipeline(unittest.TestCase):
    def test_pipeline_builds_canonical_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = Paths.from_root(root)
            paths.feeds.mkdir(parents=True, exist_ok=True)
            paths.assets.mkdir(parents=True, exist_ok=True)

            (paths.assets / "OmniSource.png").write_bytes(PNG_MAGIC + b"source-icon")
            (paths.assets / "TestApp.png").write_bytes(PNG_MAGIC + b"app-icon")

            (root / "locales").mkdir()
            (root / "locales" / "en.json").write_text('{"nav":{"home":"Home"}}', encoding="utf-8")
            (root / "locales" / "es.json").write_text('{"nav":{"home":"Inicio"}}', encoding="utf-8")

            catalog_data = {
                "source": {
                    "name": "OmniSource",
                    "identifier": "com.iamsmmh.omnisource",
                    "baseURL": "https://iamsmmh.github.io/OmniSource",
                    "icon": "OmniSource.png",
                    "banner": "OmniSource.png",
                },
                "apps": [
                    {
                        "slug": "testapp",
                        "name": "Test App",
                        "bundleIdentifier": "com.example.testapp",
                        "developerName": "Tester",
                        "icon": "TestApp.png",
                        "status": "stable",
                        "compatibility": {"minOSVersion": "16.0", "clients": ["altstore"]},
                        "upstream": {
                            "provider": "github",
                            "repo": "example/testapp",
                        },
                    }
                ],
            }
            import json

            paths.catalog.write_text(json.dumps(catalog_data, indent=2), encoding="utf-8")

            state_data = {
                "testapp": {
                    "versions": [
                        {
                            "version": "1.0.0",
                            "date": "2026-09-07",
                            "localizedDescription": "Test release",
                            "downloadURL": "https://example.com/app.ipa",
                            "size": 5000,
                            "minOSVersion": "16.0",
                        }
                    ],
                    "health": {"reachable": True, "detail": "HTTP 200", "since": "2026-09-07"},
                }
            }
            (paths.feeds / "state.json").write_text(json.dumps(state_data, indent=2), encoding="utf-8")

            container = Container(
                paths=paths,
                http=MagicMock(),
                providers=ProviderRegistry(),
            )

            catalog = load_catalog(container)
            state = load_state(container)
            report = SyncReport()

            changed_feeds, health_doc, analytics_doc = stage_build(container, catalog, state, report)
            self.assertGreater(len(changed_feeds), 0)
            self.assertTrue((paths.feeds / "testapp.json").exists())
            self.assertTrue((paths.feeds / "apps.json").exists())
            self.assertTrue((paths.feeds / "health.json").exists())
            # Translation coverage is a build document derived from locales/.
            self.assertTrue((paths.feeds / "translation-status.json").exists())
            self.assertEqual(
                json.loads((paths.feeds / "translation-status.json").read_text(encoding="utf-8")),
                {"en": 100, "es": 100},
            )
            # stage_build only writes the canonical feeds; the flat/API URL
            # families are published from them (see publish_repo_artifacts).
            self.assertFalse((paths.root / "testapp.json").exists())
            self.assertFalse((paths.root / "apps.json").exists())

            # Publishing keeps the repository root clean: only /apps.json (the
            # installable source URL) is mirrored, byte-identical to feeds/.
            # Every other feed stays in feeds/ and is served by the _site/
            # artifact for the GitHub Actions deployment.
            summary = publish_repo_artifacts(root, health_doc=health_doc, analytics_doc=analytics_doc)
            self.assertGreater(summary["flat_files"], 0)
            self.assertFalse((root / "testapp.json").exists(), "per-app feeds must not be mirrored to the root")
            self.assertEqual((root / "apps.json").read_bytes(), (paths.feeds / "apps.json").read_bytes())
            self.assertTrue((root / "api" / "apps.json").is_file())
            self.assertEqual((root / "api" / "apps.json").read_bytes(), (paths.feeds / "apps.json").read_bytes())
            # Publisher-owned API documents are mirrored byte-for-byte.
            self.assertTrue((root / "api" / "translation-status.json").is_file())
            self.assertEqual(
                (root / "api" / "translation-status.json").read_bytes(),
                (paths.feeds / "translation-status.json").read_bytes(),
            )
            for name in ("apps.json", "sitemap.xml", "robots.txt", ".nojekyll", "api/index.json"):
                self.assertTrue((root / name).is_file(), f"publisher did not write {name}")

            # Idempotent: a second run changes nothing.
            again = publish_repo_artifacts(root, health_doc=health_doc, analytics_doc=analytics_doc)
            self.assertEqual(again["written"], [])
            # ...and the prune never drops a document the publisher owns
            # (the historical api/translation-status.json failure).
            self.assertTrue((root / "api" / "translation-status.json").is_file())

            # Stale generated copies disappear instead of lingering as dead URLs.
            stale = root / "removedapp.json"
            stale.write_text("{}", encoding="utf-8")
            publish_repo_artifacts(root, health_doc=health_doc, analytics_doc=analytics_doc)
            self.assertFalse(stale.exists(), "a feed that no longer exists must not stay published")

            # ...but a root document another workflow publishes is kept:
            # security.yml commits the root copy of its machine-readable report,
            # and pruning it made sync.yml publish the deletion (the URL 404'd
            # until the next daily scan restored it).
            external = root / "security-report.json"
            external.write_text("{}", encoding="utf-8")
            summary = publish_repo_artifacts(root, health_doc=health_doc, analytics_doc=analytics_doc)
            self.assertTrue(external.is_file(), "the publisher must not prune another workflow's root document")
            self.assertNotIn("security-report.json", summary["removed"])


class _UnreachableProvider:
    """Stands in for a provider whose every leg errors (host blocked, API down)."""

    name = "github"
    source_type = SourceType.GITHUB_RELEASES

    def fetch_releases(self, source, *, previous_latest_url=None, incremental=False):
        raise ProviderError("upstream unreachable")


class TestManualReleaseFallback(unittest.TestCase):
    """A brand-new entry must survive an unreachable upstream."""

    def _container(self, root: Path) -> Container:
        paths = Paths.from_root(root)
        paths.feeds.mkdir(parents=True, exist_ok=True)
        paths.assets.mkdir(parents=True, exist_ok=True)
        (paths.assets / "TestApp.png").write_bytes(PNG_MAGIC + b"app-icon")
        paths.catalog.write_text(
            json.dumps(
                {
                    "source": {
                        "name": "OmniSource",
                        "identifier": "com.iamsmmh.omnisource",
                        "baseURL": "https://iamsmmh.github.io/OmniSource",
                        "icon": "TestApp.png",
                    },
                    "apps": [
                        {
                            "slug": "testapp",
                            "name": "Test App",
                            "bundleIdentifier": "com.example.testapp",
                            "developerName": "Tester",
                            "icon": "TestApp.png",
                            "status": "stable",
                            "compatibility": {"minOSVersion": "15.0", "clients": ["altstore"]},
                            "upstream": {"provider": "github", "repo": "example/testapp"},
                            "manualRelease": {
                                "version": "3.0.9",
                                "date": "2026-08-22",
                                "localizedDescription": "Snapshot of the last known good build.",
                                "downloadURL": "https://example.com/TestApp.ipa",
                                "size": 123456,
                                "minOSVersion": "15.0",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        registry = ProviderRegistry()
        registry.register(_UnreachableProvider())
        return Container(paths=paths, http=MagicMock(), providers=registry)

    def test_manual_release_is_the_last_resort_without_cached_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            container = self._container(Path(tmpdir))
            app = load_catalog(container).apps[0]

            # No cached snapshot yet: the catalog's manualRelease keeps the app
            # in the build instead of dropping it while the upstream is down.
            versions, source = sync_app(container, app, incremental=False, previous=None)
            self.assertEqual(source, "manual")
            self.assertEqual(versions[0]["version"], "3.0.9")
            self.assertEqual(versions[0]["downloadURL"], "https://example.com/TestApp.ipa")

            # With cached state the existing "keep the last good build"
            # behaviour still wins: a transient outage must not rewrite state.
            with self.assertRaises(SyncError):
                sync_app(container, app, incremental=False, previous={"versions": versions})


if __name__ == "__main__":
    unittest.main()
