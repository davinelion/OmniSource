#!/usr/bin/env python3
"""Create or verify a metadata-only OmniSource disaster-recovery snapshot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource.backup import LABELS, create_snapshot, restore_snapshot, verify_snapshot

ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--root", default=str(ROOT))
    create.add_argument("--destination", default=str(ROOT / ".backups"))
    create.add_argument("--label", choices=LABELS, default="manual")
    verify = sub.add_parser("verify")
    verify.add_argument("snapshot")
    restore = sub.add_parser("restore")
    restore.add_argument("snapshot")
    restore.add_argument("--root", default=str(ROOT))
    restore.add_argument("--apply", action="store_true", help="perform restore; default is a dry-run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "create":
        path = create_snapshot(Path(args.root), Path(args.destination), label=args.label)
        result = verify_snapshot(path)
        print(f"backup: {path} ({result['checked']} files, verified={result['ok']})")
        return 0 if result["ok"] else 1
    if args.command == "verify":
        result = verify_snapshot(Path(args.snapshot))
        print(f"backup verify: checked={result['checked']} ok={result['ok']}")
        for error in result["errors"]:
            print(f"error: {error}")
        return 0 if result["ok"] else 1
    result = restore_snapshot(Path(args.snapshot), Path(args.root), dry_run=not args.apply)
    print(f"backup restore: dry_run={not args.apply} files={len(result['restored'])} ok={result['ok']}")
    for error in result["errors"]:
        print(f"error: {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
