# tools/ — site build infrastructure

This directory holds the source for the phyground public site:

```
tools/
├── build_site.py             # Jinja2 → static HTML renderer
├── site_config.example.json  # stub config (Round 0); overridden by built snapshot in later rounds
├── static_src/               # CSS/JS source, mirrored to /static at build time
│   └── css/base.css
└── templates/                # Jinja2 templates
    ├── base.html             # layout + navbar/footer includes
    ├── components/{navbar,footer}.html
    ├── home/index.html
    └── leaderboard/index.html
```

## Quick start

```bash
pip install jinja2
python tools/build_site.py                                  # uses site_config.example.json
python tools/build_site.py --config snapshot/index/site_config.json
```

The renderer writes to repo root: `index.html`, `leaderboard/index.html`, `static/`.

## Determinism

Same config + same templates → byte-identical output. The build does not touch the network, the wmbench checkout, or any data outside `tools/` and (in later rounds) `snapshot/`.

## What's coming next round

- `build_snapshot.py` — copy wmbench source files (eval_registry.json, paperdemo manifest, MODEL_CATALOG, etc.) into `snapshot/index/` and `_wmbench_src/`, then synthesize `snapshot/index/site_config.json` for the renderer.
- `verify_snapshot.py` — re-check sha256 vs `snapshot/MANIFEST.json`.
