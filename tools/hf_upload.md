# HuggingFace upload — `juyil/wmbench-public`

The static site's `<video>` and `<img>` tags reference URLs of the form

    https://huggingface.co/datasets/juyil/wmbench-public/resolve/main/<rel>

This document walks through populating that dataset so the rendered HTML resolves at runtime. The phyground build is offline and produces a deterministic upload manifest; the actual upload is a manual step with a HuggingFace write token.

## What the snapshot expects

`tools/build_hf_upload_manifest.py` enumerates every URL the snapshot embeds and emits:

- `snapshot/HF_UPLOAD_MANIFEST.json` — the file list with local source paths, HF target paths, sha256s, and `exists_locally` flags.

Three URL families:

| Family | HF target path | Local source (under `_wmbench_src/`) |
|--------|----------------|---------------------------------------|
| paperdemo videos    | `paperdemo/<law>/<model>__<file>.mp4`             | `data/paperdemo/<law>/<...>.mp4`     |
| humaneval per-model videos | `videos/<model>-<dataset>/<prompt_id>.mp4` | `data/videos/<model>-<dataset>/<prompt_id>.mp4` |
| first-frame thumbnails | `prompts/<dataset>/first_frames/<prompt_id>.jpg` | `data/prompts/<dataset>/first_frames/<prompt_id>.jpg` |

The first-frame JPGs and the paperdemo PDF→PNG thumbnails are tracked in `snapshot/index/figs/` and `snapshot/index/first_frames/`; they are also part of the upload list so a fresh HF dataset clone can rebuild the site without the wmbench checkout.

## Quick path

```bash
# 1. Generate manifest + materialize a staging tree (only files actually present locally).
python tools/build_snapshot.py --select-humaneval-100
python tools/build_hf_upload_manifest.py --materialize hf_staging/

# 2. Authenticate (one-time, with a write token scoped to juyil/wmbench-public).
pip install huggingface_hub
huggingface-cli login

# 3. Upload the staging tree in one shot.
huggingface-cli upload --repo-type dataset juyil/wmbench-public hf_staging/ .
```

## Verifying after upload

```bash
# Spot-check a paperdemo video URL the site embeds.
curl -I "https://huggingface.co/datasets/juyil/wmbench-public/resolve/main/paperdemo/collision/ltx-2-19b-dev__collision_156.mp4"
# Expect HTTP 200 + Content-Type: video/mp4
```

Open `index.html`, `videos/index.html`, and `videos/compare/index.html` in a browser and confirm the videos play. The static HTML never changes between "URLs assumed" and "URLs verified" — only the HF dataset state does.

## Files marked `exists_locally: false`

These are videos the wmbench tree has but the public repo deliberately does not (they live in `data/videos/<model>-<dataset>/<stem>.mp4`, ~100 GB). Source them from a local wmbench checkout when running the upload:

```bash
# Materialize from wmbench instead of _wmbench_src for the videos:
python -c "
import json, shutil
from pathlib import Path
WM = Path('/path/to/wmbench')
STAGE = Path('hf_staging')
m = json.load(open('snapshot/HF_UPLOAD_MANIFEST.json'))
for e in m['files']:
    if e['exists_locally']:
        continue
    src = e['local_source']
    if not src.startswith('_wmbench_src/'):
        continue
    rel = src[len('_wmbench_src/'):]
    cand = WM / rel
    if cand.is_file():
        dst = STAGE / e['hf_target_path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cand, dst)
"
```

## Repo hygiene

`hf_staging/` is gitignored. The HF dataset itself is the source of truth for video bytes; the static site is always rebuildable from `_wmbench_src/` + the HF dataset.

## Troubleshooting

- **HF returns 404 for a manifest URL**: check the path matches exactly — case and separator characters matter. The leaderboard's `Download raw JSON` links don't go through HF; they hit `snapshot/scores/...` inside the GitHub Pages repo.
- **HF returns 401**: re-run `huggingface-cli login` with a token that has write access to `juyil/wmbench-public`.
- **Repo too large**: HF datasets allow up to several hundred GB; the wmbench video set fits easily. If you ever exceed limits, prune `videos_index[<low-priority-model>]` from the snapshot's site_config.
