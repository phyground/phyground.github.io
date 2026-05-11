# phyground.github.io

Public companion site for the **wmbench** physics-grounded video benchmark.

- **Video Gallery** — browse video-generation model outputs, side-by-side per physical law.
- **Leaderboard** — slice rankings by evaluator × dataset × subset × scoring schema.

The site is **static** and deployed via GitHub Pages from the repository root. Videos are hosted on HuggingFace; this repo only carries the index/manifest, rendered HTML, and a small CSS asset.

## Architecture

```
phyground.github.io/                 ← GitHub Pages serves this root, with Jekyll disabled
├── .nojekyll                        ← disable Jekyll on GitHub Pages
├── index.html                       ← rendered home page          (BUILT)
├── leaderboard/index.html           ← leaderboard                 (BUILT)
├── videos/index.html                ← video gallery               (BUILT)
├── about/index.html                 ← about / citation            (BUILT)
├── static/css/base.css              ← mirrored from tools/static_src
│
├── tools/                           ← build infrastructure (source of truth)
│   ├── build_snapshot.py            ← _wmbench_src/ → snapshot/
│   ├── verify_snapshot.py           ← sha256 check vs snapshot/MANIFEST.json
│   ├── build_site.py                ← Jinja2 + snapshot → static HTML
│   ├── site_config.example.json     ← stub config (offline preview without a snapshot)
│   ├── templates/                   ← Jinja2 templates
│   └── static_src/                  ← CSS/JS source, mirrored to /static
│
├── _wmbench_src/                    ← hard-copied wmbench source (frozen; no network deps)
│   ├── PROVENANCE.md                ← wmbench HEAD sha + per-file sha256 at copy time
│   ├── data/{vis_datasets.json, paperdemo/{manifest.csv, figs/*.pdf}}
│   ├── evals/{eval_registry.json, eval_types.py}
│   └── videogen/runner/MODEL_CATALOG.py
│
├── snapshot/                        ← deterministic build output, the site's only data source
│   ├── MANIFEST.json                ← sha256 over every snapshot file
│   └── index/{site_config.json, eval_registry.frozen.json,
│              paperdemo.manifest.csv, vis_datasets.frozen.json,
│              model_catalog.frozen.json, humaneval_100.json}
│
├── docs/exp-plan/public/{plan.md, humaneval_100.md}
├── requirements.txt
└── LICENSE
```

## Quick start

```bash
git clone https://github.com/phyground/phyground.github.io.git
cd phyground.github.io
python -m venv .venv && source .venv/bin/activate    # optional
pip install -r requirements.txt

# 1. Build the data snapshot from _wmbench_src/.
python tools/build_snapshot.py

# 2. Verify the manifest.
python tools/verify_snapshot.py

# 3. Render HTML against the snapshot.
python tools/build_site.py --config snapshot/index/site_config.json
```

After step 3 the rendered files at the repo root (`index.html`, `leaderboard/index.html`, `videos/index.html`, `about/index.html`, `static/css/base.css`) are exactly what GitHub Pages will serve.

For an offline preview without a snapshot (the renderer reads templates only):

```bash
python tools/build_site.py                            # uses tools/site_config.example.json
```

## Determinism

- `tools/build_snapshot.py` produces byte-identical `snapshot/` output for the same `_wmbench_src/` inputs (any timestamp can be pinned via `--now <ISO>`; the default uses UTC `now()` only for `build_meta.built_at`).
- `tools/build_site.py` produces byte-identical HTML for the same config + templates.
- `tools/verify_snapshot.py` exits non-zero on any drift between `snapshot/MANIFEST.json` and the actual file tree.

## Refreshing wmbench data

When upstream wmbench files change:

1. Re-copy the affected files into `_wmbench_src/` (preserving paths). The expected layout is documented in `_wmbench_src/PROVENANCE.md`.
2. Update `_wmbench_src/PROVENANCE.md` (HEAD sha, sha256 per file, copy date).
3. `python tools/build_snapshot.py && python tools/verify_snapshot.py`.
4. `python tools/build_site.py --config snapshot/index/site_config.json`.
5. Commit the diff (the `snapshot/index/*.json` files travel with git so the deployment is reproducible).

## Deployment

GitHub Pages serves the repo root on the default branch:

1. Run the four-step Quick Start above on your laptop.
2. `git add` the rendered HTML, `static/`, `snapshot/index/`, `snapshot/MANIFEST.json`.
3. `git commit && git push`.
4. GitHub Pages picks up the change at <https://phyground.github.io/>.

The `.nojekyll` file disables GitHub's automatic Jekyll processing so the rendered HTML is served verbatim.

## Plan and progress

- Implementation plan: [`docs/exp-plan/public/plan.md`](docs/exp-plan/public/plan.md)
- humaneval-100 selection spec: [`docs/exp-plan/public/humaneval_100.md`](docs/exp-plan/public/humaneval_100.md)
- `_wmbench_src/` provenance: [`_wmbench_src/PROVENANCE.md`](_wmbench_src/PROVENANCE.md)
- RLCR loop state: `.humanize/rlcr/<timestamp>/`

## License

Code: MIT (see `LICENSE`). Generated videos: per upstream model license.
