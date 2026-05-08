# phyground.github.io

Public companion site for the **wmbench** physics-grounded video benchmark.

- **Video Gallery** — browse video-generation model outputs, side-by-side per physical law.
- **Leaderboard** — slice rankings by evaluator × dataset × subset × scoring schema.

The site is **static** and deployed via GitHub Pages from the repository root. Videos are hosted on HuggingFace at <https://huggingface.co/juyil>; this repo only carries the index/manifest, the rendered HTML, and a small CSS asset.

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
│   ├── build_site.py                ← Jinja2 → static HTML
│   ├── site_config.example.json     ← stub config (used until snapshot exists)
│   ├── templates/                   ← Jinja2 templates
│   └── static_src/                  ← CSS/JS source, mirrored to /static
│
├── snapshot/                        ← built data (Round 1+); index/manifest in git, media gitignored
├── _wmbench_src/                    ← hard-copied wmbench source (Round 1+); large media gitignored
├── docs/exp-plan/public/plan.md     ← implementation plan
└── LICENSE
```

## Build flow

```bash
pip install jinja2

# Round 0 — render with the stub config (no real wmbench data needed):
python tools/build_site.py

# Round 1+ — once the snapshot builder lands:
python tools/build_snapshot.py        # reads _wmbench_src/, writes snapshot/
python tools/build_site.py --config snapshot/index/site_config.json
```

The build is **deterministic** (same config + same templates → byte-identical HTML) and **offline** (no network, no NFS).

## Deployment

GitHub Pages is configured to serve from the repository root on the default branch:

1. `python tools/build_site.py [--config snapshot/index/site_config.json]`
2. `git add` the rendered HTML + `static/` + any `snapshot/index/` updates.
3. `git commit && git push`
4. GitHub Pages picks up the change at `https://phyground.github.io/`.

The `.nojekyll` file disables GitHub's automatic Jekyll processing so the rendered HTML is served verbatim.

## Why no Flask / Nginx?

The original implementation plan (`docs/exp-plan/public/plan.md`, §4–§7) targeted a Flask + gunicorn + Nginx stack. We pivoted to a static build for two reasons:

- GitHub Pages is the deployment target — it cannot run Python.
- Videos are heavy and need a CDN; HuggingFace's `juyil` namespace gives us free, durable hosting with direct-download URLs we can embed.

Plan §5 ("Phase 5 — 静态化") foresaw this; we promoted it to the baseline. See `.humanize/rlcr/<round>/goal-tracker.md` for the full plan-evolution log.

## Plan and progress

- Implementation plan: [`docs/exp-plan/public/plan.md`](docs/exp-plan/public/plan.md)
- RLCR loop state: `.humanize/rlcr/<timestamp>/`

## License

Code: MIT (see `LICENSE`). Generated videos: per upstream model license (HuggingFace dataset cards on `juyil`).
