# HuggingFace upload — `NU-World-Model-Embodied-AI/phyground`

The static site references HF URLs of the form:

    https://huggingface.co/datasets/NU-World-Model-Embodied-AI/phyground/resolve/main/<rel>

This document walks through populating that dataset so every `<video>` and
`<img poster=...>` resolves at runtime. The repo handles every step except
the actual `huggingface-cli upload` (which needs a write token).

## URL scheme

The site embeds three families of HF URLs:

| Family | HF target | Local source (under `_wmbench_src/`)  |
|--------|-----------|----------------------------------------|
| paperdemo videos        | `paperdemo/<law>/<model>__<file>.mp4`        | `data/paperdemo/<law>/<model>__<file>.mp4` |
| humaneval-100 videos    | `videos/<model>/<stem>.mp4`                  | `data/videos/<model>/<stem>.mp4` (or `data/videos/<model>-humaneval/<stem>.mp4`) |
| first-frame thumbnails  | `prompts/<dataset>/first_frames/<stem>.jpg` | `data/prompts/<dataset>/first_frames/<stem>.jpg` |

The HF dataset must mirror this layout exactly. The `tools/build_site.py`
audit walks every rendered HTML and fails the build if any embedded URL
is missing from `snapshot/HF_UPLOAD_MANIFEST.json`, so the manifest is
authoritative.

## Scope: ~1,000 files

`snapshot/HF_UPLOAD_MANIFEST.json` is **scoped to the published
humaneval-100 set + paperdemo + first-frames**, NOT every model × every
prompt the registry knows. That keeps the dataset small (~900 MB) while
still covering the published evidence end-to-end.

Current counts: **62 paperdemo videos + 83 first-frames + ~875 humaneval
videos = ~1020 target files** (≈ 100 prompts × 8 strict-intersection
models, plus a few additional models that scored some humaneval-100
prompts via the prompt manifest). Total bytes ≈ 900 MB.

## Quick path

```bash
git clone https://github.com/phyground/phyground.github.io.git
cd phyground.github.io

pip install -r requirements.txt huggingface_hub

# 1. Build the snapshot (this also writes HF_UPLOAD_MANIFEST.json):
python3 tools/build_snapshot.py --select-humaneval-100

# 2. Stage missing video bytes from a wmbench checkout (gitignored):
python3 tools/stage_hf_assets.py /path/to/wmbench

# 3. Re-build so the manifest's `n_missing_locally` reflects the staging:
python3 tools/build_snapshot.py --select-humaneval-100
python3 tools/verify_snapshot.py
python3 tools/build_site.py --config snapshot/index/site_config.json

# 4. Materialise the staging tree (also gitignored):
python3 tools/build_hf_upload_manifest.py --materialize hf_staging/

# 5. Authenticate (one-time, with a write token for NU-World-Model-Embodied-AI/phyground):
huggingface-cli login

# 6. Upload the entire layout in one shot:
huggingface-cli upload --repo-type dataset NU-World-Model-Embodied-AI/phyground hf_staging .
```

After step 6 every `<video>` and `<img poster=...>` on the rendered site
plays / loads.

## Dry-run / inspection

```bash
python3 tools/stage_hf_assets.py /path/to/wmbench --dry-run
python3 -c "import json; m=json.load(open('snapshot/HF_UPLOAD_MANIFEST.json')); print(m['n_total_files'], m['n_present_locally'], m['n_missing_locally'])"
```

## Smoke tests after upload

```bash
# Spot-check a paperdemo URL the home page embeds:
curl -I "https://huggingface.co/datasets/NU-World-Model-Embodied-AI/phyground/resolve/main/paperdemo/collision/ltx-2-19b-dev__collision_156.mp4"
# Expect: HTTP/2 200 + Content-Type: video/mp4

# A humaneval per-(model, prompt) URL the compare page embeds:
curl -I "https://huggingface.co/datasets/NU-World-Model-Embodied-AI/phyground/resolve/main/videos/cosmos-predict2.5-2b/collision_156.mp4"

# A first-frame thumbnail:
curl -I "https://huggingface.co/datasets/NU-World-Model-Embodied-AI/phyground/resolve/main/prompts/video_phy_2/first_frames/collision_156.jpg"
```

Then open the live site and verify playback on:

- `/`                                — home Featured Comparison plays.
- `/videos/`                         — by-law and by-model panes show videos.
- `/videos/compare/?prompt_id=<pid>` — every model card has a video.
- `/models/cosmos-predict2.5-2b/`    — representative-video grid plays.
- `/models/ltx-2.3-22b-dev/`         — same.

## Files marked `exists_locally: false`

There is a small residual (currently 2 entries for `cogvideox1.5-5b-i2v`
prompts the upstream tree never finished generating). These are noted in
`_wmbench_src/PROVENANCE.md`'s "missing upstream" section. After upload
their HF URLs will 404 — that's a known gap in the published evidence,
not a bug in the static site.

## Repo hygiene

- `_wmbench_src/data/videos/` and `_wmbench_src/data/paperdemo/**/*.mp4`
  stay gitignored. The staging script only writes there; nothing
  multi-MB-per-file ever lands in git.
- `hf_staging/` is gitignored. Delete it after the upload completes if
  disk is tight.
- The HF dataset itself is the source of truth for video bytes; the
  static site is always rebuildable from `_wmbench_src/` (snapshot small
  files in git) + the HF dataset (videos and first-frames).
