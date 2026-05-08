#!/usr/bin/env python3
"""Stage every missing HF-upload manifest source from a wmbench checkout
into `_wmbench_src/data/...`. Idempotent.

Reads `snapshot/HF_UPLOAD_MANIFEST.json`; for every entry where
`exists_locally == false`, looks the file up in the supplied wmbench root
under `data/<hf_target_path>` (with a fallback to
`data/videos/<model>-humaneval/<stem>.mp4` for humaneval-specific runs)
and copies it into the corresponding `_wmbench_src/data/...` path.

`_wmbench_src/data/videos/` and `_wmbench_src/data/paperdemo/**/*.mp4`
are gitignored, so the staged bytes never land in git.

Usage:
    python3 tools/stage_hf_assets.py /path/to/wmbench [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WMBENCH_SRC = REPO_ROOT / "_wmbench_src"
MANIFEST = REPO_ROOT / "snapshot" / "HF_UPLOAD_MANIFEST.json"


def _resolve_in_wmbench(wmbench: Path, target: str) -> Path | None:
    """Return the path inside `wmbench` for an HF target path, or None."""
    primary = wmbench / "data" / target
    if primary.is_file():
        return primary
    if target.startswith("videos/"):
        parts = target.split("/", 2)
        if len(parts) == 3:
            _, model_key, rest = parts
            alt = wmbench / "data" / "videos" / f"{model_key}-humaneval" / rest
            if alt.is_file():
                return alt
    return None


def stage(wmbench: Path, *, dry_run: bool, verbose: bool = True) -> tuple[int, int, int]:
    """Returns (copied, already_present, missing_upstream)."""
    if not wmbench.is_dir():
        raise SystemExit(f"wmbench root not found: {wmbench}")
    if not MANIFEST.is_file():
        raise SystemExit(
            f"{MANIFEST.relative_to(REPO_ROOT)} not found. "
            "Run `python3 tools/build_snapshot.py --select-humaneval-100` first."
        )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    n_copied = n_already = n_missing = 0
    total_bytes = 0
    sample_missing: list[str] = []
    for entry in manifest.get("files", []):
        if entry.get("exists_locally"):
            n_already += 1
            continue
        target = entry["hf_target_path"]
        src = _resolve_in_wmbench(wmbench, target)
        if not src:
            n_missing += 1
            if len(sample_missing) < 5:
                sample_missing.append(target)
            continue
        if dry_run:
            n_copied += 1
            total_bytes += src.stat().st_size
            continue
        dst = WMBENCH_SRC / "data" / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        n_copied += 1
        total_bytes += src.stat().st_size

    if verbose:
        action = "would copy" if dry_run else "copied"
        gb = total_bytes / 1024**3
        print(f"[stage_hf_assets] {action} {n_copied} files "
              f"({total_bytes:,} bytes ~= {gb:.2f} GB).")
        print(f"[stage_hf_assets] already present: {n_already}; "
              f"unrecoverable upstream: {n_missing}.")
        if sample_missing:
            print("[stage_hf_assets] sample missing upstream:")
            for t in sample_missing:
                print(f"  - {t}")
        if not dry_run and n_missing == 0:
            print("[stage_hf_assets] OK -- every manifest source is present.")
    return n_copied, n_already, n_missing


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Stage HF assets from a wmbench checkout.")
    parser.add_argument("wmbench_root", type=Path,
                        help="Path to the wmbench repository root (the directory containing data/).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be copied without writing anything.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    stage(args.wmbench_root, dry_run=args.dry_run, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
