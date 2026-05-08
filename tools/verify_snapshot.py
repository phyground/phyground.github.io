#!/usr/bin/env python3
"""Verify snapshot/MANIFEST.json against the actual files under snapshot/.

Exits 0 on a clean match. Exits 1 if any file's sha256 disagrees, if a tracked
file is missing, or if an untracked file appears under snapshot/. Drift is
reported with one path per line, classified as MISMATCH / MISSING / EXTRA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = REPO_ROOT / "snapshot"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(snapshot_dir: Path = SNAPSHOT_DIR, *, verbose: bool = True) -> int:
    if not snapshot_dir.is_dir():
        print(f"ERROR: snapshot directory not found: {snapshot_dir}", file=sys.stderr)
        return 1
    manifest_path = snapshot_dir / "MANIFEST.json"
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected: dict[str, str] = manifest.get("files", {})

    actual: dict[str, str] = {}
    for root, _dirs, files in os.walk(snapshot_dir):
        for fname in files:
            p = Path(root) / fname
            rel = str(p.relative_to(snapshot_dir)).replace(os.sep, "/")
            if rel == "MANIFEST.json":
                continue
            actual[rel] = _sha256_file(p)

    mismatches: list[str] = []
    missing: list[str] = []
    extra: list[str] = []

    for rel, want in sorted(expected.items()):
        got = actual.get(rel)
        if got is None:
            missing.append(rel)
        elif got != want:
            mismatches.append(f"{rel}\n  expected {want}\n  got      {got}")

    for rel in sorted(actual):
        if rel not in expected:
            extra.append(rel)

    n_problems = len(mismatches) + len(missing) + len(extra)

    if verbose:
        print(f"[verify_snapshot] tracked files: {len(expected)}")
        print(f"[verify_snapshot] actual  files: {len(actual)}")
    if n_problems == 0:
        if verbose:
            print("[verify_snapshot] OK")
        return 0

    print("[verify_snapshot] FAIL", file=sys.stderr)
    for line in mismatches:
        print(f"  MISMATCH {line}", file=sys.stderr)
    for line in missing:
        print(f"  MISSING  {line}", file=sys.stderr)
    for line in extra:
        print(f"  EXTRA    {line}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify snapshot/MANIFEST.json.")
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return verify(args.snapshot_dir, verbose=not args.quiet)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
