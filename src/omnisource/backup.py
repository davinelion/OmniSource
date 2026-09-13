"""Content-addressed catalog/feed backup and restore helpers.

Backups are metadata and generated JSON/XML, never IPA payloads or secrets.
Each snapshot contains a manifest with SHA-256 hashes, allowing a deployment
to verify a restore before it replaces the active catalog.  Retention is
explicitly tiered for daily, weekly, and monthly schedules.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnisource.io import atomic_write_text

SCHEMA_VERSION = 1
DEFAULT_INCLUDE = ("catalog.json", "feeds", "data", "api")
EXCLUDE_NAMES = {".cache", "node_modules", ".git", "__pycache__", "*.ipa", "*.tipa"}
# The retention tiers a snapshot may be labeled with. ``backup.yml`` maps the
# cron that fired a scheduled run onto one of them (and a manual dispatch onto
# ``manual``); ``tests/test_ops.py`` pins that mapping to this tuple, so a
# workflow label the CLI does not accept cannot ship again.
LABELS = ("daily", "weekly", "monthly", "manual")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _iter_files(root: Path, include: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for item in include:
        path = (root / item).resolve()
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(child for child in path.rglob("*") if child.is_file())
    return sorted(set(files))


def _excluded(path: Path) -> bool:
    return any(
        part in {".cache", "node_modules", ".git", "__pycache__", "quarantine"} for part in path.parts
    ) or path.suffix in {
        ".ipa",
        ".tipa",
    }


def build_manifest(root: Path, *, include: tuple[str, ...] = DEFAULT_INCLUDE) -> dict[str, Any]:
    """Hash backup candidates without copying anything."""
    root = Path(root).resolve()
    entries: list[dict[str, Any]] = []
    for path in _iter_files(root, include):
        if _excluded(path):
            continue
        relative = _safe_relative(root, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": relative, "sha256": digest, "bytes": path.stat().st_size})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": utcnow(),
        "fileCount": len(entries),
        "bytes": sum(int(item["bytes"]) for item in entries),
        "files": entries,
    }


def create_snapshot(root: Path, destination: Path, *, label: str = "manual") -> Path:
    """Create a verified snapshot directory and return its path."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snapshot = destination / f"{label}-{stamp}"
    staging = Path(tempfile.mkdtemp(prefix="omnisource-backup-", dir=destination))
    try:
        manifest = build_manifest(root)
        for entry in manifest["files"]:
            source = root / entry["path"]
            target = staging / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        atomic_write_text(staging / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        staging.replace(snapshot)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return snapshot


def verify_snapshot(snapshot: Path) -> dict[str, Any]:
    """Verify every file in a snapshot against its manifest."""
    snapshot = Path(snapshot).resolve()
    try:
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"ok": False, "errors": [f"manifest unavailable: {error}"], "checked": 0}
    errors: list[str] = []
    checked = 0
    for entry in manifest.get("files", []) if isinstance(manifest, dict) else []:
        if not isinstance(entry, dict):
            continue
        relative = str(entry.get("path") or "")
        path = (snapshot / relative).resolve()
        if snapshot not in path.parents:
            errors.append(f"path escaped snapshot: {relative}")
            continue
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checked += 1
        if digest != entry.get("sha256"):
            errors.append(f"checksum mismatch: {relative}")
    return {"ok": not errors, "errors": errors, "checked": checked}


def restore_snapshot(snapshot: Path, root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Restore only a verified snapshot; dry-run is the safe default."""
    verification = verify_snapshot(snapshot)
    if not verification["ok"]:
        return {"ok": False, "restored": [], "errors": verification["errors"]}
    snapshot = Path(snapshot).resolve()
    root = Path(root).resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    restored: list[str] = []
    for entry in manifest.get("files", []):
        relative = str(entry["path"])
        source = snapshot / relative
        target = root / relative
        restored.append(relative)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return {"ok": True, "dryRun": dry_run, "restored": restored, "errors": []}


__all__ = ["LABELS", "build_manifest", "create_snapshot", "restore_snapshot", "verify_snapshot"]
