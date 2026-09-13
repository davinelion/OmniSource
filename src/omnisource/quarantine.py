"""Explicit discovery quarantine and publication gates.

A discovered URL is untrusted input.  This module makes the lifecycle
persistent and auditable:

``discovered → validating → quarantined → verified → published``

Invalid sources are stored under ``data/quarantine/`` and are never included
in ``data/discovered_sources.json`` or any generated feed.  Promotion requires
an explicit successful validation result and a verification status; there is
no implicit "best effort" publication path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnisource.io import read_json, write_json

SCHEMA_VERSION = 1
DISCOVERED = "discovered"
VALIDATING = "validating"
QUARANTINED = "quarantined"
VERIFIED = "verified"
PUBLISHED = "published"
REJECTED = "rejected"

_ALLOWED_TRANSITIONS = {
    DISCOVERED: {VALIDATING, QUARANTINED, REJECTED},
    VALIDATING: {VERIFIED, QUARANTINED, REJECTED},
    QUARANTINED: {VALIDATING, REJECTED},
    VERIFIED: {PUBLISHED, QUARANTINED},
    PUBLISHED: {QUARANTINED},
    REJECTED: {VALIDATING},
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record_id(record: dict[str, Any]) -> str:
    source_id = str(record.get("source_id") or record.get("sourceId") or "")
    url = str(record.get("url") or record.get("source_url") or "")
    if source_id:
        return source_id
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _reputation_score(record: dict[str, Any]) -> int:
    """Coerce an untrusted reputation value into ``0..100``.

    Discovery records come from third-party feeds and scraped catalog pages,
    so the field can hold any JSON value; ``int("high")`` would raise and
    crash the fail-closed quarantine path instead of isolating the record.
    """
    value = record.get("reputation_score", record.get("reputation", 0)) or 0
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _source_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Return the public naming contract while retaining legacy fields."""
    source_id = _record_id(record)
    return {
        **record,
        "source_id": source_id,
        "source_name": str(record.get("source_name") or record.get("name") or source_id),
        "source_type": str(record.get("source_type") or record.get("type") or "unknown"),
        "source_url": str(record.get("source_url") or record.get("url") or ""),
        "first_seen": str(record.get("first_seen") or record.get("discovered_at") or utcnow()),
        "last_seen": str(record.get("last_seen") or record.get("last_checked") or utcnow()),
        "health_status": str(record.get("health_status") or record.get("health") or "unknown"),
        "verification_status": str(record.get("verification_status") or "unverified"),
        "reputation_score": _reputation_score(record),
    }


@dataclass(frozen=True)
class QuarantineResult:
    accepted: list[dict[str, Any]]
    quarantined: list[dict[str, Any]]
    errors: dict[str, list[str]]


class QuarantineStore:
    """Persistent source quarantine store with transition validation."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "sources.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        document = read_json(self.path)
        if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
            return {"schemaVersion": SCHEMA_VERSION, "generatedAt": utcnow(), "count": 0, "sources": []}
        return document

    def save(self, document: dict[str, Any]) -> None:
        document = dict(document)
        document["schemaVersion"] = SCHEMA_VERSION
        document["generatedAt"] = utcnow()
        sources = [item for item in document.get("sources", []) if isinstance(item, dict)]
        document["sources"] = sorted(sources, key=lambda item: str(item.get("source_id", "")))
        document["count"] = len(sources)
        write_json(self.path, document)

    def put(
        self,
        record: dict[str, Any],
        *,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        status: str = QUARANTINED,
    ) -> dict[str, Any]:
        if status not in _ALLOWED_TRANSITIONS and status != QUARANTINED:
            raise ValueError(f"invalid quarantine status {status!r}")
        document = self.load()
        item = _source_fields(record)
        item.update(
            {
                "status": status,
                "errors": sorted({str(error) for error in (errors or [])}),
                "warnings": sorted({str(warning) for warning in (warnings or [])}),
                "quarantined_at": str(item.get("quarantined_at") or utcnow()),
            }
        )
        by_id = {str(entry.get("source_id")): entry for entry in document["sources"] if entry.get("source_id")}
        previous = by_id.get(item["source_id"])
        if previous:
            current = str(previous.get("status") or DISCOVERED)
            # Re-discovery is not a reason to demote a source that has already
            # passed verification/publication. A later failed probe can still
            # explicitly move it to QUARANTINED.
            if status == VALIDATING and current in {VERIFIED, PUBLISHED}:
                status = current
                item["status"] = current
                item["verification_status"] = previous.get("verification_status", "verified")
            elif status != current and status not in _ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"cannot transition {item['source_id']} from {current} to {status}")
            # Retain the original quarantine timestamp across revalidation.
            item["quarantined_at"] = str(previous.get("quarantined_at") or item["quarantined_at"])
        by_id[item["source_id"]] = item
        document["sources"] = list(by_id.values())
        self.save(document)
        return item

    def transition(self, source_id: str, status: str, **updates: Any) -> dict[str, Any]:
        document = self.load()
        entries = {str(item.get("source_id")): item for item in document["sources"] if isinstance(item, dict)}
        if source_id not in entries:
            raise KeyError(source_id)
        current = str(entries[source_id].get("status") or DISCOVERED)
        if status not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"cannot transition {source_id} from {current} to {status}")
        entries[source_id] = {**entries[source_id], **updates, "status": status, "updated_at": utcnow()}
        document["sources"] = list(entries.values())
        self.save(document)
        return entries[source_id]

    def get(self, source_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.load()["sources"]
                if isinstance(item, dict) and str(item.get("source_id")) == source_id
            ),
            None,
        )

    def publishable(self, source_id: str) -> bool:
        item = self.get(source_id)
        return bool(
            item
            and item.get("status") in {VERIFIED, PUBLISHED}
            and item.get("verification_status") in {"verified", "trusted", "official"}
            and not item.get("errors")
        )


def validate_candidates(
    candidates: list[dict[str, Any]],
    validator: Callable[[dict[str, Any]], list[str]],
    store: QuarantineStore,
    *,
    promote_verified: bool = False,
) -> QuarantineResult:
    """Validate candidates, accepting only clean records and isolating the rest."""
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    errors_by_id: dict[str, list[str]] = {}
    for candidate in candidates:
        record = _source_fields(candidate if isinstance(candidate, dict) else {})
        source_id = record["source_id"]
        errors = [str(error) for error in validator(record)]
        if errors:
            record["status"] = QUARANTINED
            quarantined.append(store.put(record, errors=errors))
            errors_by_id[source_id] = errors
            continue
        record["status"] = VERIFIED if promote_verified else VALIDATING
        record["verification_status"] = "verified" if promote_verified else "pending"
        accepted.append(record)
        # A structurally valid candidate remains isolated until the explicit
        # verification stage. It is an audit record, not a publication feed.
        store.put(record, status=record["status"])
    return QuarantineResult(accepted=accepted, quarantined=quarantined, errors=errors_by_id)


def can_publish(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """Pure final publication gate for feed builders."""
    failures: list[str] = []
    if record.get("status") not in {VERIFIED, PUBLISHED}:
        failures.append("source is not verified")
    if record.get("verification_status") not in {"verified", "trusted", "official"}:
        failures.append("verification_status is not trusted")
    if record.get("errors"):
        failures.append("source has validation errors")
    if not str(record.get("source_url") or record.get("url") or "").startswith("https://"):
        failures.append("source URL must be HTTPS")
    return not failures, failures


__all__ = [
    "DISCOVERED",
    "PUBLISHED",
    "QUARANTINED",
    "REJECTED",
    "VALIDATING",
    "VERIFIED",
    "QuarantineResult",
    "QuarantineStore",
    "can_publish",
    "validate_candidates",
]
