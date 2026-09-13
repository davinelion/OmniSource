from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnisource.quarantine import (
    PUBLISHED,
    QUARANTINED,
    VALIDATING,
    VERIFIED,
    QuarantineStore,
    can_publish,
    validate_candidates,
)


class QuarantineLifecycleTests(unittest.TestCase):
    def _candidate(self) -> dict[str, object]:
        return {
            "source_id": "example-source",
            "name": "Example",
            "url": "https://example.test/apps.json",
            "type": "altstore",
            "discovered_at": "2026-09-12T00:00:00Z",
            "last_checked": "2026-09-12T00:00:00Z",
            "health": "online",
            "reputation": 80,
        }

    def test_structural_success_stays_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = QuarantineStore(Path(directory))
            result = validate_candidates([self._candidate()], lambda _record: [], store)
            self.assertEqual(len(result.accepted), 1)
            saved = store.get("example-source")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["status"], VALIDATING)
            self.assertFalse(store.publishable("example-source"))

    def test_invalid_candidate_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = QuarantineStore(Path(directory))
            result = validate_candidates([self._candidate()], lambda _record: ["bad feed"], store)
            self.assertEqual(result.accepted, [])
            self.assertEqual(result.quarantined[0]["status"], QUARANTINED)
            self.assertEqual(store.get("example-source")["errors"], ["bad feed"])

    def test_verified_then_published_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = QuarantineStore(Path(directory))
            validate_candidates([self._candidate()], lambda _record: [], store)
            verified = store.transition(
                "example-source",
                VERIFIED,
                verification_status="verified",
                errors=[],
            )
            allowed, failures = can_publish(verified)
            self.assertTrue(allowed, failures)
            published = store.transition("example-source", PUBLISHED, published_at="2026-09-12T00:00:00Z")
            self.assertEqual(published["status"], PUBLISHED)
            self.assertTrue(store.publishable("example-source"))

    def test_invalid_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = QuarantineStore(Path(directory))
            validate_candidates([self._candidate()], lambda _record: [], store)
            with self.assertRaises(ValueError):
                store.transition("example-source", PUBLISHED)

    def test_untrusted_reputation_is_coerced_not_fatal(self) -> None:
        # Discovered records are untrusted input: int("high") used to raise
        # inside the fail-closed quarantine path, so a malformed candidate
        # crashed the gate instead of being isolated.
        for value, expected in (("high", 0), (None, 0), ("55", 55), (550, 100), (-4, 0), (55.9, 55)):
            candidate = self._candidate()
            candidate["reputation"] = value
            with tempfile.TemporaryDirectory() as directory, self.subTest(value=value):
                store = QuarantineStore(Path(directory))
                result = validate_candidates([candidate], lambda _record: ["bad feed"], store)
                self.assertEqual(result.accepted, [])
                self.assertEqual(store.get("example-source")["reputation_score"], expected)


if __name__ == "__main__":
    unittest.main()
