#!/usr/bin/env python3
"""Emit the HuggingFace upload manifest matching the URLs the snapshot embeds.

The static site's `<video>` and `<img>` tags reference URLs of the form

    {HF_BASE}/<rel>

where `HF_BASE = https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main`.
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

HF_REPO = "juyil/phygroundwebsitevideo"           # `huggingface-cli upload <repo>` target
HF_REPO_TYPE = "dataset"

# Synthesized dataset README. Lives in the manifest and the materialized
# staging tree, but never on disk under `_wmbench_src/`. The bytes are a pure
# function of `site_config` so the manifest sha256 stays deterministic.
README_HF_TARGET = "README.md"
README_LOCAL_LABEL = "(synthesized in build_hf_upload_manifest.py)"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


HF_PREFIX = "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/"


def _hf_target_from_url(url: str) -> str | None:
    """Strip the HF base off a video/img URL the snapshot embeds; return the
    `<rel>` portion (e.g. `videos/<model>-<dataset>/<stem>.mp4`).
    """
    if not isinstance(url, str):
        return None
    if not url.startswith(HF_PREFIX):
        return None
    return url[len(HF_PREFIX):]


def _local_source_for_target(target: str) -> Path:
    """Map an HF target path back to its expected local source under
    `_wmbench_src/`. The HF layout mirrors the NU-WME-AI dataset shape:

      videos/<model>/<stem>.mp4    → _wmbench_src/data/videos/<model>/<stem>.mp4
                                     (humaneval-specific fallback below)
      first_images/<stem>.jpg      → looked up across every dataset's first_frames/

    For `videos/<model>/<stem>.mp4` we also try `data/videos/<model>-humaneval/<stem>.mp4`
    where wmbench keeps several humaneval-specific generation runs.

    For `first_images/<stem>.jpg` we walk every `data/prompts/<dataset>/first_frames/`
    directory in `_wmbench_src/` because the HF dataset is flat but the wmbench
    source side keeps them in per-dataset subdirs.
    """
    if target.startswith("videos/"):
        primary = WMBENCH_SRC / "data" / target
        if primary.is_file():
            return primary
        parts = target.split("/", 2)
        if len(parts) == 3:
            _, model_key, rest = parts
            alt = WMBENCH_SRC / "data" / "videos" / f"{model_key}-humaneval" / rest
            if alt.is_file():
                return alt
        return primary
    if target.startswith("first_images/"):
        stem_jpg = target[len("first_images/"):]
        prompts_root = WMBENCH_SRC / "data" / "prompts"
        if prompts_root.is_dir():
            for ds_dir in sorted(prompts_root.iterdir()):
                if not ds_dir.is_dir():
                    continue
                cand = ds_dir / "first_frames" / stem_jpg
                if cand.is_file():
                    return cand
        return prompts_root / "_first_images_unresolved" / stem_jpg
    return WMBENCH_SRC / "data" / target


def _collect_targets_from_site_config(site_config: dict) -> set[str]:
    """Walk every place in `site_config.json` that ever embeds an HF URL and
    collect every distinct `<rel>` the static site might reference. This is
    the source of truth for the upload manifest — if a URL appears here, it
    must appear in the manifest.

    Coverage:
      - paperdemo[*].videos[*].video_url_hf
      - featured_comparison.videos[*].video_url_hf
      - prompts_index[*].first_frame_url
      - prompts_index[*].per_model_videos.values()
      - videos_index[*].paperdemo[*].video_url_hf
      - videos_index[*].humaneval[*].{video_url_hf, first_frame_url}
      - models[*].representative_videos[*].{video_url_hf, first_frame_url}
    """
    targets: set[str] = set()
    def _add(url: str | None) -> None:
        rel = _hf_target_from_url(url or "")
        if rel:
            targets.add(rel)

    for law in site_config.get("paperdemo", []):
        for v in law.get("videos", []):
            _add(v.get("video_url_hf"))
    for v in (site_config.get("featured_comparison", {}) or {}).get("videos", []):
        _add(v.get("video_url_hf"))
    for p in (site_config.get("prompts_index", {}) or {}).values():
        _add(p.get("first_frame_url"))
        for u in (p.get("per_model_videos") or {}).values():
            _add(u)
    for sub in (site_config.get("videos_index", {}) or {}).values():
        for v in sub.get("paperdemo", []) + sub.get("humaneval", []):
            _add(v.get("video_url_hf"))
            _add(v.get("first_frame_url"))
    for m in site_config.get("models", []):
        for v in m.get("representative_videos") or []:
            _add(v.get("video_url_hf"))
            _add(v.get("first_frame_url"))
    return targets


def _render_readme_text(*, n_videos: int, n_first_images: int, n_models: int) -> str:
    """Deterministic dataset README. Bytes are a pure function of three
    integer counts derived from the site_config target set. No timestamps,
    no hash digests — re-running the build with the same snapshot produces
    byte-identical README content.
    """
    return (
        "# phyground website videos\n"
        "\n"
        "Video CDN for [phyground.github.io](https://phyground.github.io). The\n"
        "static site embeds URLs of the form\n"
        "`https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/<rel>`\n"
        "and this dataset hosts those `<rel>` files.\n"
        "\n"
        "## Contents\n"
        "\n"
        f"- {n_videos} videos under `videos/<model>/<stem>.mp4`\n"
        f"- {n_first_images} first-frame thumbnails under `first_images/<stem>.jpg`\n"
        f"- {n_models} video-generation models, each on the same humaneval-100\n"
        "  prompt subset selected by\n"
        "  [`tools/build_snapshot.py`](https://github.com/phyground/phyground.github.io/blob/master/tools/build_snapshot.py).\n"
        "\n"
        "## Layout\n"
        "\n"
        "```\n"
        "videos/\n"
        "  <model_key>/\n"
        "    <prompt_stem>.mp4\n"
        "first_images/\n"
        "  <prompt_stem>.jpg\n"
        "README.md\n"
        "```\n"
        "\n"
        "## Source & rebuild\n"
        "\n"
        "Generated by <https://github.com/phyground/phyground.github.io>. The\n"
        "manifest of every file in this dataset is checked into that repo at\n"
        "[`snapshot/HF_UPLOAD_MANIFEST.json`](https://github.com/phyground/phyground.github.io/blob/master/snapshot/HF_UPLOAD_MANIFEST.json),\n"
        "with sha256 sums for every entry. To verify the live dataset against\n"
        "that manifest, run `python3 tools/smoke_test_hf.py` from a clone.\n"
        "\n"
        "## License\n"
        "\n"
        "Generated videos: per-model upstream license (see each model card on\n"
        "the HuggingFace Hub). Hand-curated annotations and prompt set: CC-BY-4.0\n"
        "unless otherwise noted in the upstream phyground repo.\n"
    )


def _readme_inputs(site_config: dict) -> tuple[str, str]:
    """Return `(rendered_text, sha256_hex)` for the synthesized README.

    Counts are derived from the site_config target set so the README text
    stays consistent with what the manifest itself ships.
    """
    targets = sorted(_collect_targets_from_site_config(site_config))
    n_videos = sum(1 for t in targets if t.startswith("videos/"))
    n_first_images = sum(1 for t in targets if t.startswith("first_images/"))
    model_keys: set[str] = set()
    for t in targets:
        if not t.startswith("videos/"):
            continue
        parts = t.split("/", 2)
        if len(parts) >= 2 and parts[1]:
            model_keys.add(parts[1])
    text = _render_readme_text(
        n_videos=n_videos,
        n_first_images=n_first_images,
        n_models=len(model_keys),
    )
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, sha


def build_manifest(site_config: dict) -> dict:
    """Pure-function variant: takes the parsed site_config and returns the
    manifest dict.

    The manifest is built by walking every place the snapshot embeds an HF
    URL (see `_collect_targets_from_site_config`) so the manifest matches
    the URL set the rendered HTML actually references. This is enforced by
    the audit in `tools/build_snapshot.py`.

    A synthesized `README.md` entry is appended so the dataset always ships
    a documented root file; its bytes are produced by `_readme_inputs` and
    materialized directly into `hf_staging/README.md` (no on-disk source).
    """
    targets = sorted(_collect_targets_from_site_config(site_config))
    manifest_entries = []
    n_present = 0
    for target in targets:
        local = _local_source_for_target(target)
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

    _, readme_sha = _readme_inputs(site_config)
    manifest_entries.append({
        "local_source": README_LOCAL_LABEL,
        "hf_target_path": README_HF_TARGET,
        "exists_locally": True,
        "sha256": readme_sha,
    })
    n_present += 1
    manifest_entries.sort(key=lambda e: e["hf_target_path"])
    return {
        "schema_version": "1",
        "hf_repo": HF_REPO,
        "hf_repo_type": HF_REPO_TYPE,
        "hf_url_base": "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main",
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
    """Hard-link / copy every present `local_source` into a single folder
    mirroring the HF target layout, for one-shot upload via
    `huggingface-cli upload <repo> hf_staging .`.
    """
    cfg_path = SNAPSHOT_DIR / "index" / "site_config.json"
    if not cfg_path.is_file():
        raise SystemExit("snapshot/index/site_config.json not found; run build_snapshot.py first.")
    site_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    targets = sorted(_collect_targets_from_site_config(site_config))
    import shutil
    n = 0
    for target in targets:
        local = _local_source_for_target(target)
        if not local.is_file():
            continue
        dst = staging / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dst)
        n += 1

    readme_text, _ = _readme_inputs(site_config)
    readme_dst = staging / README_HF_TARGET
    readme_dst.parent.mkdir(parents=True, exist_ok=True)
    readme_dst.write_text(readme_text, encoding="utf-8")
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
