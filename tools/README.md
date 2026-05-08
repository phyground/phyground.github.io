# tools/ — site build infrastructure

This directory holds the source for the phyground public site:

```
tools/
├── build_snapshot.py         # _wmbench_src/ → snapshot/ (deterministic, atomic)
├── verify_snapshot.py        # sha256 check vs snapshot/MANIFEST.json
├── build_site.py             # snapshot/index/site_config.json → static HTML
├── site_config.example.json  # offline-preview stub config
├── static_src/               # CSS/JS source, mirrored to /static at build time
│   └── css/base.css
└── templates/                # Jinja2 templates
    ├── base.html             # layout + navbar/footer includes
    ├── components/{navbar,footer}.html
    ├── home/index.html
    ├── leaderboard/index.html
    ├── videos/index.html
    └── about/index.html
```

## Quick start

```bash
pip install -r requirements.txt

# Build snapshot from _wmbench_src/, verify it, then render HTML.
python tools/build_snapshot.py
python tools/verify_snapshot.py
python tools/build_site.py --config snapshot/index/site_config.json

# Or, for an offline preview without a snapshot:
python tools/build_site.py
```

## Determinism

- `build_snapshot.py` writes byte-identical `snapshot/` output for the same `_wmbench_src/` (the `built_at` timestamp can be pinned via `--now <ISO>`).
- `build_site.py` writes byte-identical HTML for the same config + templates.
- `verify_snapshot.py` exits non-zero on any drift between `snapshot/MANIFEST.json` and the file tree.

## Render context

`build_site.py` exposes the full snapshot data model to every template:

- `site` — title / paper_url / huggingface_url / etc.
- `headline` — `n_models`, `n_prompts`, `n_annotations`, `n_eval_combos`.
- `models` — flattened MODEL_CATALOG ∪ external models seen in registry/paperdemo.
- `datasets` — humaneval / wmb / video_phy_2 / physics_iq / openvid summaries.
- `leaderboard_entries` — deduplicated to newest per `(video_model, dataset, subset, evaluator, schema)`, with `history` for older runs.
- `paperdemo` — grouped by physical law, each with its `videos` list.
- `videos_index` — `"<model>::<dataset>"` → `[file, ...]` (Round 2 will populate from HuggingFace URLs).
- `build_meta` — provenance for the footer (built_at, snapshot_sha, source-file sha256s).

Templates that don't consume a key simply ignore it.

## What's coming next round (Round 2)

- Real leaderboard table with sortable columns, filter UI, query-param sync, expandable rows.
- Video Gallery by-law / by-model / `/videos/compare?prompt_id=` rendering.
- Per-model `models/<key>/index.html` detail pages.
- Home Featured Comparison populated from one paperdemo law.
- HuggingFace video URL wiring once the `juyil` dataset structure is finalized.
