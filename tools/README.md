# tools/ — site build infrastructure

This directory holds the source for the phyground public site:

```
tools/
├── build_snapshot.py         # _wmbench_src/ → snapshot/ (deterministic, atomic)
│                             # supports --select-humaneval-100 and --now <ISO>
├── verify_snapshot.py        # sha256 check vs snapshot/MANIFEST.json
├── build_site.py             # snapshot/index/site_config.json → static HTML
├── site_config.example.json  # offline-preview stub config
├── static_src/               # CSS/JS source, mirrored to /static at build time
│   ├── css/base.css
│   └── js/{leaderboard,gallery,compare}.js
└── templates/                # Jinja2 templates
    ├── base.html             # layout + navbar/footer includes
    ├── components/{navbar,footer}.html
    ├── home/index.html
    ├── leaderboard/index.html
    ├── videos/{index,compare}.html
    ├── models/detail.html
    └── about/index.html
```

## Quick start

```bash
pip install -r requirements.txt

# Build snapshot from _wmbench_src/, verify it, then render HTML.
python tools/build_snapshot.py --select-humaneval-100
python tools/verify_snapshot.py
python tools/build_site.py --config snapshot/index/site_config.json

# Or, for an offline preview without a snapshot:
python tools/build_site.py
```

## Determinism

- `build_snapshot.py` writes byte-identical `snapshot/` output for the same `_wmbench_src/` (`--now <ISO>` pins the `built_at` timestamp).
- `build_site.py` writes byte-identical HTML for the same config + templates.
- `verify_snapshot.py` exits non-zero on any drift between `snapshot/MANIFEST.json` and the file tree.

## Render context

`build_site.py` exposes the full snapshot data model to every template:

- `site` — title / paper_url / huggingface_url / etc.
- `headline` — `n_models`, `n_prompts` (from humaneval-100), `n_annotations`, `n_eval_combos`.
- `models` — flattened MODEL_CATALOG ∪ external models seen in registry/paperdemo, with `params_b` / `fps` / `frames` / `resolution` parsed from MODEL_CATALOG description strings, plus `representative_videos` (up to 9 paperdemo entries per model).
- `datasets` — coarse summaries derived from `vis_datasets.json`. Round 2 surfaces three concrete datasets (humaneval, video_phy_2, physics_iq); humaneval-derived published prompts come from `humaneval_100`.
- `leaderboard_entries` — coverage-filtered, deduplicated to newest per `(video_model, dataset, subset, evaluator, schema)`. Coverage-zero rows are kept under `history` but never elevated to `current`. Each row carries a `source_url_snapshot` pointing into `scores/<evaluator>/<basename>.json` when the file is available, and `source_status: "missing"` otherwise.
- `paperdemo` — grouped by physical law; each video carries `model`, `video_id`, `n_ann`, `src_filename`, `src_path`, `video_url_hf`.
- `videos_index` — `"<model>::<dataset>"` and `"<model>::paperdemo:<law>"` keys describing where videos live.
- `prompts_index` — humaneval prompts keyed by `prompt_id`, used by the `videos/compare` page (read inline as JSON in the rendered HTML).
- `featured_comparison` — one paperdemo law (`collision` by default) lifted onto the home page.
- `humaneval_100_summary` — `n_selected`, `law_quotas`, `effective_law_counts`.
- `build_meta` — provenance for the footer (built_at, snapshot_sha, source-file sha256s).

Per-page extras:

- `models/detail.html` receives `model` (a single dict from `models`).

Pages rendered: `index.html`, `leaderboard/index.html`, `videos/index.html`, `videos/compare/index.html`, `about/index.html`, and one `models/<key>/index.html` per model in the snapshot (16 in the current snapshot, ~21 pages total).
