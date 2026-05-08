# HuggingFace upload — `juyil/phygroundwebsitevideo`

The static site references HF URLs of the form:

    https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/<rel>

This document walks through populating that dataset so every `<video>` and
`<img poster=...>` resolves at runtime. The repo handles every step except
the actual `huggingface-cli upload` (which needs a write token).

## URL scheme (matches the NU-World-Model-Embodied-AI/phyground layout)

| Family | HF target | Local source (under `_wmbench_src/`)  |
|--------|-----------|----------------------------------------|
| Per-(model, prompt) videos | `videos/<model>/<stem>.mp4`        | `data/videos/<model>/<stem>.mp4` (or `data/videos/<model>-humaneval/<stem>.mp4`) |
| First-frame thumbnails     | `first_images/<stem>.jpg`          | walked across `data/prompts/<dataset>/first_frames/<stem>.jpg` |

There is **no** `paperdemo/` folder and **no** per-source-dataset subdir
under `first_images/`; the dataset is flat. Paperdemo videos that share a
stem with a humaneval prompt for the same model are emitted as
`videos/<model>/<stem>.mp4`. Paperdemo entries whose model is not in the
8 published model dirs (or whose stem is not a humaneval-100 prompt) are
shown without a `<video>` tag — the by-law card still names the model
and `n_ann`, but does not embed a broken URL.

## Scope

`snapshot/HF_UPLOAD_MANIFEST.json` is **scoped to the published
humaneval-100 set + first-frames**, ~885 files / ~890 MB:

- ~802 humaneval videos (100 prompts × 8 strict-intersection models, plus
  the few paperdemo entries that share humaneval stems for a published
  model)
- ~83 first-frame JPGs (the humaneval-100 prompts that have an upstream
  first_frame on disk; a handful of `wmb` and `physics_iq` prompts have
  no first_frame upstream, so the count is below 100)

The 8 published model dirs are:

```
cosmos-predict2.5-14b   ltx-2.3-22b-dev    veo-3.1
cosmos-predict2.5-2b    omniweaving        wan2.2-i2v-a14b
ltx-2-19b-dev                              wan2.2-ti2v-5b
```

## Quick path

```bash
git clone https://github.com/phyground/phyground.github.io.git
cd phyground.github.io

pip install -r requirements.txt huggingface_hub

# 1. Build the snapshot (this also writes HF_UPLOAD_MANIFEST.json):
python3 tools/build_snapshot.py --select-humaneval-100

# 2. Stage the missing source bytes from a wmbench checkout (gitignored):
python3 tools/stage_hf_assets.py /path/to/wmbench

# 3. Re-build so the manifest's `n_missing_locally` reflects the staging:
python3 tools/build_snapshot.py --select-humaneval-100
python3 tools/verify_snapshot.py
python3 tools/build_site.py --config snapshot/index/site_config.json

# 4. Materialise the staging tree (also gitignored):
python3 tools/build_hf_upload_manifest.py --materialize hf_staging/

# 5. Authenticate (one-time, with a write token for juyil/phygroundwebsitevideo):
huggingface-cli login

# 6. Create the dataset (one-time) and upload the entire layout in one shot:
huggingface-cli repo create juyil/phygroundwebsitevideo --type dataset
huggingface-cli upload --repo-type dataset juyil/phygroundwebsitevideo hf_staging .
```

After step 6 every `<video>` and `<img poster=...>` on the rendered site
plays / loads.

## Smoke tests after upload

```bash
# Per-(model, prompt) video the compare page embeds:
curl -I "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/videos/cosmos-predict2.5-2b/collision_156.mp4"
# Expect HTTP/2 302 (redirect to a CDN URL that returns the video)

# A first-frame thumbnail (flat layout):
curl -I "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/first_images/collision_156.jpg"
```

Then open the live site and verify playback on:

- `/`                                — home Featured Comparison plays.
- `/videos/`                         — by-law and by-model panes show videos.
- `/videos/compare/?prompt_id=collision_156` — every model card has a video.
- `/models/cosmos-predict2.5-2b/`    — representative-video grid plays.
- `/models/ltx-2.3-22b-dev/`         — same.

## Repo hygiene

- `_wmbench_src/data/videos/` and `_wmbench_src/data/paperdemo/**/*.mp4`
  stay gitignored. The staging script only writes there; nothing
  multi-MB-per-file ever lands in git.
- `hf_staging/` is gitignored. Delete it after the upload completes if
  disk is tight.
- The HF dataset is the source of truth for video bytes; the static site
  is rebuildable from `_wmbench_src/` (small index files in git) + the HF
  dataset (videos + first-frames).
