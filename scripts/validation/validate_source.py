#!/usr/bin/env python3
"""Validate discovery-store records (``data/discovered_sources.json``).

Fails (exit 1) when any record violates the schema, so the discovery
workflow can quarantine the file instead of publishing it.

``--quarantine-invalid`` is the self-healing mode ``discovery.yml`` runs: an
invalid record is isolated in ``data/quarantine/sources.json`` and dropped
from the discovery store — fail-closed, since an isolated record can never
reach a feed — and the run stays green so the workflow's commit step persists
that quarantine. Without it a single malformed record fails the run *before*
anything is committed, which leaves the record in place and wedges every
later run; the operator action documented in ``docs/OPERATIONS.md`` was
exactly "quarantine the offending record, re-run", so the gate now does it
itself. Isolation still fails the run when it cannot be performed, or when
the offending record was already verified/published (a publication breach
must never be resolved by silently dropping the record).

Usage
-----
    python3 scripts/validation/validate_source.py
    python3 scripts/validation/validate_source.py --store data/discovered_sources.json --strict
    python3 scripts/validation/validate_source.py --quarantine-invalid
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource import autodiscovery
from omnisource.quarantine import PUBLISHED, QUARANTINED, VERIFIED, QuarantineStore
from omnisource.remote_validation import assert_publishable

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "discovered_sources.json"
QUARANTINE_DIR = ROOT / "data" / "quarantine"
# Quarantine is the intended fail-closed destination for malformed or
# not-yet-verifiable candidates: an isolated record is reported, not failed.
ISOLATED_STATUSES = {"quarantined", "rejected"}
PUBLISHED_STATUSES = {VERIFIED, PUBLISHED}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default=str(STORE), help="discovery store path")
    parser.add_argument("--quarantine-dir", default=str(QUARANTINE_DIR), help="quarantine store directory")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument(
        "--quarantine-invalid",
        action="store_true",
        help="isolate invalid records in the quarantine store instead of failing the run",
    )
    return parser.parse_args(argv)


def _annotate(level: str, message: str) -> None:
    """Print a GitHub Actions annotation when running on Actions."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level}::{message}")
    else:
        print(f"{level}: {message}")


def _record_url(record: dict[str, Any]) -> str:
    return str(record.get("url", record.get("source_url", "?")))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store_path = Path(args.store)
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"validate_source: cannot read {args.store}: {exc}")
        return 1
    sources = store.get("sources", []) if isinstance(store, dict) else []

    errors: list[str] = []
    warnings: list[str] = []
    invalid: list[tuple[dict[str, Any], list[str]]] = []
    quarantined = 0
    for record in sources:
        if not isinstance(record, dict):
            errors.append("?: record must be an object")
            continue
        if str(record.get("status", "")).casefold() in ISOLATED_STATUSES:
            quarantined += 1
            warnings.append(f"{_record_url(record)}: retained in quarantine")
            continue
        _ok, record_errors, record_warnings = assert_publishable(record)
        if record_errors:
            invalid.append((record, [str(error) for error in record_errors]))
        warnings.extend(f"{_record_url(record)}: {warning}" for warning in record_warnings)

    # Without --quarantine-invalid every finding is a hard error (the strict,
    # read-only behaviour validation.yml and local runs rely on).
    quarantine_store = QuarantineStore(Path(args.quarantine_dir)) if args.quarantine_invalid else None
    isolated = 0
    isolated_ids: set[str] = set()
    for record, record_errors in invalid:
        label = _record_url(record)
        if quarantine_store is None or str(record.get("status", "")).casefold() in PUBLISHED_STATUSES:
            errors.extend(f"{label}: {error}" for error in record_errors)
            continue
        try:
            quarantine_store.put(record, status=QUARANTINED, errors=record_errors)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"{label}: cannot quarantine ({exc})")
            continue
        isolated += 1
        isolated_ids.add(str(record.get("source_id", "")))
        _annotate("warning", f"{label}: invalid discovery record quarantined ({', '.join(record_errors)})")

    if isolated:
        retained = [
            record
            for record in sources
            if isinstance(record, dict) and str(record.get("source_id", "")) not in isolated_ids
        ]
        if autodiscovery.save_store(store_path, retained):
            print(f"validate_source: quarantined {isolated} invalid record(s); {len(retained)} remain in the store")
        else:
            errors.append(f"{store_path}: cannot rewrite the discovery store")

    for message in errors:
        print(f"error: {message}")
    for message in warnings:
        print(f"warning: {message}")
    failed = bool(errors) or (args.strict and bool(warnings))
    print(
        f"validate_source: {len(sources)} record(s), {len(errors)} error(s), "
        f"{len(warnings)} warning(s), {quarantined + isolated} quarantined"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
