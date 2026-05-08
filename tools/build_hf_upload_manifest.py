#!/usr/bin/env python3
"""Emit the HuggingFace upload manifest matching the URLs the snapshot embeds.

The static site's `<video>` and `<img>` tags reference URLs of the form

    {HF_BASE}/<rel>

where `HF_BASE = https://huggingface.co/datasets/juyil/wmbench-public/resolve/main`.
This script enumerates every `<rel>` the snapshot needs and writes:

  - `snapshot/HF_UPLOAD_MANIFEST.json`  (machine-readable: list of
    {local_source, hf_target_path, sha256?, exists_locally})
  - prints a copy-paste-ready `huggingface-cli upload` command sequence on stdout

The script does no network I/O. It is safe to commit; the user runs it once
locally and follows the printed commands.

Usage:
    python tools/build_hf_upload_manifest.py [--out snapshot/HF_UPLOAD_MANIFEST.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WMBENCH_SRC = REPO_ROOT / "_wmbench_src"
SNAPSHOT_DIR = REPO_ROOT / "snapshot"

HF_REPO = "juyil/wmbench-public"           # `huggingface-cli upload <repo>` target
HF_REPO_TYPE = "dataset"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_paperdemo_videos() -> list[tuple[Path, str]]:
    """Map `_wmbench_src/data/paperdemo/<law>/<file>.mp4` → `paperdemo/<law>/<file>.mp4`.

    Returns a list of (local_source_path, hf_target_path). The local source
    files do not exist in this repo (videos are not copied; only the manifest
    + figs are), so `exists_locally` will be False and the user must source
    the .mp4s from upstream wmbench before uploading.
    """
    out: list[tuple[Path, str]] = []
    pdroot = WMBENCH_SRC / "data" / "paperdemo"
    if not pdroot.is_dir():
        return out
    # We don't have the videos in repo; emit one entry per row in the manifest.
    manifest = pdroot / "manifest.csv"
    if not manifest.is_file():
        return out
    import csv
    with manifest.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            dst = row.get("dst_path", "")    # data/paperdemo/<law>/<file>.mp4
            if not dst.startswith("data/paperdemo/"):
                continue
            local = WMBENCH_SRC / dst
            target = "paperdemo/" + dst[len("data/paperdemo/"):]
            out.append((local, target))
    return out


def _walk_humaneval_videos(site_config: dict) -> list[tuple[Path, str]]:
    """For every `videos_index[<model>][humaneval][*].video_url_hf`, infer the
    HF target path and record an upload entry. Local source path is best-effort
    (the wmbench tree has them under `data/videos/<model>-<dataset>/<stem>.mp4`).
    """
    out: list[tuple[Path, str]] = []
    for model_key, sub in site_config.get("videos_index", {}).items():
        for entry in sub.get("humaneval", []):
            url = entry.get("video_url_hf") or ""
            stem = entry.get("prompt_id") or ""
            ds = entry.get("dataset") or ""
            if not stem or not ds:
                continue
            target = f"videos/{model_key}-{ds}/{stem}.mp4"
            local = WMBENCH_SRC / "data" / "videos" / f"{model_key}-{ds}" / f"{stem}.mp4"
            out.append((local, target))
    return out


def _walk_first_frames(site_config: dict) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for prompt_id, p in site_config.get("prompts_index", {}).items():
        ff = p.get("first_frame_url")
        ds = p.get("dataset") or ""
        if not ff or not ds:
            continue
        local = WMBENCH_SRC / "data" / "prompts" / ds / "first_frames" / f"{prompt_id}.jpg"
        target = f"prompts/{ds}/first_frames/{prompt_id}.jpg"
        out.append((local, target))
    return out


def _dedup(entries: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    seen = set()
    out = []
    for local, target in entries:
        if target in seen:
            continue
        seen.add(target)
        out.append((local, target))
    out.sort(key=lambda t: t[1])
    return out


def build_manifest(site_config: dict) -> dict:
    """Pure-function variant: takes the parsed site_config and returns the
    manifest dict. Useful for callers that want to embed the manifest into
    a larger build without writing it to disk first.
    """
    entries = _dedup(
        _walk_paperdemo_videos()
        + _walk_humaneval_videos(site_config)
        + _walk_first_frames(site_config),
    )
    manifest_entries = []
    n_present = 0
    for local, target in entries:
        exists = local.is_file()
        if exists:
            n_present += 1
        try:
            local_str = str(local.relative_to(REPO_ROOT))
        except ValueError:
            local_str = str(local)
        manifest_entries.append({
            "local_source": local_str,
            "hf_target_path": target,
            "exists_locally": exists,
            "sha256": _sha256_file(local) if exists else None,
        })
    return {
        "schema_version": "1",
        "hf_repo": HF_REPO,
        "hf_repo_type": HF_REPO_TYPE,
        "hf_url_base": "https://huggingface.co/datasets/juyil/wmbench-public/resolve/main",
        "n_total_files": len(manifest_entries),
        "n_present_locally": n_present,
        "n_missing_locally": len(manifest_entries) - n_present,
        "files": manifest_entries,
    }


def build(*, out_path: Path) -> dict:
    cfg_path = SNAPSHOT_DIR / "index" / "site_config.json"
    if not cfg_path.is_file():
        raise SystemExit(f"snapshot/index/site_config.json not found; run build_snapshot.py first.")
    site_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    manifest_obj = build_manifest(site_config)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        printable = str(out_path.relative_to(REPO_ROOT))
    except ValueError:
        printable = str(out_path)
    print(f"[hf_upload_manifest] wrote {printable}")
    manifest_entries = manifest_obj["files"]
    print(f"[hf_upload_manifest] {len(manifest_entries)} target files; "
          f"{manifest_obj['n_present_locally']} present locally, "
          f"{manifest_obj['n_missing_locally']} need upstream sourcing")
    print()
    print("# Once the upstream files are colocated under _wmbench_src/, run:")
    print(f"#   pip install huggingface_hub")
    print(f"#   huggingface-cli login    # one-time, with a write token for {HF_REPO}")
    print(f"#   huggingface-cli upload --repo-type {HF_REPO_TYPE} {HF_REPO} \\")
    print(f"#       <local-source>  <hf-target-path>")
    print(f"# Or, to upload the entire layout in one shot, materialize a staging tree:")
    print(f"#   python tools/build_hf_upload_manifest.py --materialize hf_staging/")
    print(f"#   huggingface-cli upload --repo-type {HF_REPO_TYPE} {HF_REPO} hf_staging .")
    return manifest_obj


def materialize(staging: Path) -> None:
    """Optionally hard-link / copy every present `local_source` into a single
    folder mirroring the HF target layout, for one-shot upload via
    `huggingface-cli upload <repo> hf_staging .`.
    """
    cfg_path = SNAPSHOT_DIR / "index" / "site_config.json"
    if not cfg_path.is_file():
        raise SystemExit("snapshot/index/site_config.json not found; run build_snapshot.py first.")
    site_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    entries = _dedup(
        _walk_paperdemo_videos()
        + _walk_humaneval_videos(site_config)
        + _walk_first_frames(site_config),
    )
    import shutil
    n = 0
    for local, target in entries:
        if not local.is_file():
            continue
        dst = staging / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dst)
        n += 1
    print(f"[hf_upload_manifest] materialized {n} files into {staging}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Emit HuggingFace upload manifest.")
    parser.add_argument("--out", type=Path, default=SNAPSHOT_DIR / "HF_UPLOAD_MANIFEST.json")
    parser.add_argument("--materialize", type=Path, default=None,
                        help="Copy every locally-present file into <dir>/<hf_target_path>.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    build(out_path=args.out)
    if args.materialize:
        materialize(args.materialize)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
