# Phyground site rewrite: openvla-style single-page static landing

## Goal

Replace the current multi-page, Jinja-rendered, HF-hosted-video Phyground site with a single static `index.html` modeled on `openvla.github.io`. All videos and text live in the repository — no build pipeline, no remote asset fetches, no snapshot/JSON layer.

## Inputs

Real paper metadata is available at `/shared/user60/workspace/worldmodel/wmbench/paper/`:

- **Title**: PhyGround: Benchmarking Physical Reasoning in Generative World Models
- **Authors** (15): Juyi Lin¹, Arash Akbari¹, Yumei He², Lin Zhao¹, Haichao Zhang¹, Arman Akbari¹, Xingchen Xu³, Zoe Y. Lu², Enfu Nan¹, Hokin Deng⁴, Edmund Yeh¹, Sarah Ostadabbas¹, Yun Fu¹, Jennifer Dy¹, Pu Zhao¹, Yanzhi Wang¹
- **Affiliations**: ¹Northeastern University, ²Tulane University, ³University of Washington, ⁴Carnegie Mellon University
- **Abstract**: full text in `wmbench/paper/sections/0_abstract.tex` — used verbatim
- **Teaser figure**: `wmbench/paper/figures/teaser.pdf` (convert to PNG for the page)
- **Logo**: `wmbench/paper/figures/phyground_logo.png`

Existing data still useful (read-once, then deleted with the rest of the pipeline):

- `snapshot/index/phyjudge_leaderboard.json` — leaderboard scores per model × law
- `snapshot/HF_UPLOAD_MANIFEST.json` — manifest mapping prompt × model to video paths
- `hf_staging/videos/<model>/<stem>.mp4` — local copies of all generated videos (797 MB total)

External links to preserve in the hero:

- GitHub: `https://github.com/NU-World-Model-Embodied-AI/PhyGround`
- Dataset: `https://huggingface.co/datasets/juyil/phygroundwebsitevideo`
- Model: `https://huggingface.co/NU-World-Model-Embodied-AI/phyjudge-9B`
- Paper: disabled "coming soon" button (preprint not posted)

## File layout (after rewrite)

```
phyground.github.io/
├── index.html              # single page
├── static/
│   ├── css/                # bulma.min.css, bulma-carousel.min.css, bulma-slider.min.css,
│   │                       # fontawesome.all.min.css, index.css  (copied from openvla)
│   ├── js/                 # bulma-carousel.min.js, bulma-slider.min.js,
│   │                       # fontawesome.all.min.js, index.js   (copied from openvla)
│   ├── videos/<model>/<stem>.mp4   # 30-50 curated mp4s, preserving model/stem layout
│   └── images/
│       ├── phyground_logo.png      # from paper
│       ├── teaser.png              # rasterized from paper teaser.pdf
│       ├── favicon.ico, favicon-32.png, phyground-192.png, phyground-512.png  (from current static/img/)
│       └── (optional method figure)
├── README.md               # rewritten: one-paragraph description + how to view locally
├── LICENSE                 # kept as-is
├── .nojekyll               # kept (forces Pages to serve as-is)
├── .gitignore              # trimmed
└── .github/workflows/      # kept only if it just publishes static files; no build step
```

### Deleted (top-level)

`about/`, `leaderboard/`, `videos/`, `models/`, `docs/exp-plan/`, `snapshot/`, `tools/`, `tests/`, `hf_staging/`, `.audit_artifacts/`, `.agents/`, `.codex/`, `.humanize/`, `vm-web/`, `_wmbench_src/`, `requirements.txt`, `requirements-audit.txt`, `pytest.ini`, `.pytest_cache/`.

The current `docs/` folder is kept because this design doc lives under `docs/superpowers/specs/`. Only `docs/exp-plan/` (the obsolete audit plan) is removed.

## Page structure (top → bottom in `index.html`)

Markup follows openvla's bulma sectioning so styling matches one-to-one.

1. **Hero / title block**
   - Logo + "PhyGround" wordmark, then subtitle "Benchmarking Physical Reasoning in Generative World Models".
   - 16 authors with affiliation superscripts (single column on mobile, wrapped on desktop). Exact author block taken from `main.tex` lines 47-58.
   - Affiliation legend line.
   - Link buttons (openvla button styling): **Paper** (disabled, "coming soon"), **Code** (GitHub), **Dataset** (HF), **Model** (HF phyjudge-9B). Each button gets a Font Awesome / academicons icon (consistent with openvla).

2. **Teaser image** — `static/images/teaser.png` (rasterized from `teaser.pdf`), centered, `is-max-desktop`. Caption matches the paper figure caption: *"PhyGround decomposes each video model's holistic physical reasoning score into scores for 13 physical laws. We recruited 459 annotators to conduct a large-scale, quality-controlled human study. Based on these human annotations, we released PhyJudge-9B, a fine-tuned judge model that supports reproducible automated evaluation."*

3. **Abstract** — verbatim from `wmbench/paper/sections/0_abstract.tex`, single paragraph centered/justified under an `<h2 class="title is-3">Abstract</h2>`.

4. **Featured 8-model comparison** — bulma carousel (openvla "results-carousel" pattern). One prompt (`collision_156`) shown across the 8 evaluated models: `cosmos-predict2.5-14b`, `cosmos-predict2.5-2b`, `ltx-2-19b-dev`, `ltx-2.3-22b-dev`, `omniweaving`, `veo-3.1`, `wan2.2-i2v-a14b`, `wan2.2-ti2v-5b`. Each carousel item is an autoplay-muted-loop `<video>` with the model name overlaid as a label. Section header: "One prompt, eight models" with the prompt text as a sub-line.

5. **Per-law sample grid** — `<h2>Sample videos by physical law</h2>`. 13 sub-sections (one per law: collision, impenetrability, momentum, gravity, inertia, reflection, shadow, material, buoyancy, displacement, flow_dynamics, fluid_continuity, boundary_interaction). Each sub-section shows 2 representative videos in a 2-column bulma row, each captioned with `<model> · <prompt_id>`. Selection rule: prefer the highest-scoring model and one mid/low-scoring model per law to surface contrast. Total: 26 videos.

6. **Method / PhyJudge-9B** — `<h2>Method</h2>` with two paragraphs:
   - **Benchmark design**: 250 prompts × 13 laws × observable sub-questions, anchored in social-science lab-experiment design (paraphrased from abstract).
   - **PhyJudge-9B**: open physics-specialized VLM judge, 3.3% aggregate relative bias vs Gemini-3.1-Pro's 16.6%.
   - No additional figure here; the teaser at the top already shows the method overview.

7. **Leaderboard** — `<h2>PhyJudge-9B Leaderboard</h2>`. Static HTML table, one row per model, columns = overall + 13 laws. Header row uses the per-law color palette already in the repo (see commit `69798e0`). Generated **once** by reading `snapshot/index/phyjudge_leaderboard.json` and `model_catalog.frozen.json`, then frozen into `index.html`. After freezing, the JSON files are deleted along with the rest of `snapshot/`.

8. **BibTeX** — `<h2>BibTeX</h2>` with a `<pre>` block:
   ```bibtex
   @article{lin2026phyground,
     title  = {PhyGround: Benchmarking Physical Reasoning in Generative World Models},
     author = {Lin, Juyi and Akbari, Arash and He, Yumei and Zhao, Lin and Zhang, Haichao and Akbari, Arman and Xu, Xingchen and Lu, Zoe Y. and Nan, Enfu and Deng, Hokin and Yeh, Edmund and Ostadabbas, Sarah and Fu, Yun and Dy, Jennifer and Zhao, Pu and Wang, Yanzhi},
     year   = {2026}
   }
   ```
   The exact BibTeX key/year can be adjusted once the preprint is posted.

9. **Footer** — minimal: "© 2026 PhyGround. Site template adapted from [OpenVLA](https://openvla.github.io/)."

## Video selection algorithm

A single throwaway script (`scripts/_oneoff_pick_videos.py`, not committed) walks `snapshot/HF_UPLOAD_MANIFEST.json` + `phyjudge_leaderboard.json` and produces a list of (model, stem, target_path) tuples for the ~34 videos above:

- 8 videos for the featured comparison: `collision_156` across all 8 models. If `collision_156` is missing for any model, fall back to the next prompt that is scored on every model (verify against `humaneval_100.json` + leaderboard).
- 26 videos for the per-law grid: for each of the 13 laws, pick the prompt scored by every model with the **highest** mean score (for the high-scoring exemplar) and one with a **low** mean score (for the contrast exemplar). The exact prompts will be logged in `static/videos/README.md` so the selection is reproducible without the snapshot.

Files are copied (`cp`, not symlinked) into `static/videos/<model>/<stem>.mp4`. Predicted total size: ~70-100 MB (mp4 average is 2-3 MB; veo-3.1 outliers excluded except where they fill the featured comparison).

After copying, `static/videos/README.md` lists every video path with its (law, model, prompt_id, score) so a reader can reproduce the selection without the deleted snapshot. This doubles as the alt-text source for the page.

## Leaderboard freezing

A second throwaway script (`scripts/_oneoff_render_leaderboard.py`, not committed) emits a single `<table>...</table>` HTML fragment from `phyjudge_leaderboard.json` (and `model_catalog.frozen.json` for display names). That fragment is pasted into the leaderboard section of `index.html`. Color palette: the per-law CSS variables from the current `tools/static_src/css/base.css` are copied into the new `static/css/index.css` so the table header colors carry over.

## Styling

- Adopt openvla's full asset bundle for parity: copy `static/css/bulma.min.css`, `bulma-carousel.min.css`, `bulma-slider.min.css`, `fontawesome.all.min.css` and `static/js/bulma-carousel.min.js`, `bulma-slider.min.js`, `fontawesome.all.min.js`, `index.js`. These are MIT/CC-licensed and small.
- `static/css/index.css` starts as a copy of openvla's `index.css`, then receives Phyground-specific additions: per-law header colors, leaderboard table styling, footer.
- No Tailwind, no dependency on the existing `tools/static_src/css/base.css`.

## Cleanup sequence

1. Generate `static/videos/` from `hf_staging/` via the throwaway script.
2. Generate the leaderboard HTML fragment from `snapshot/` via the throwaway script.
3. Write `index.html`, `static/css/`, `static/js/`, `static/images/` from scratch.
4. Write new `README.md`.
5. Update `.gitignore` to drop entries for deleted directories.
6. `git rm -r` the deleted directories listed above.
7. Verify locally: open `index.html` in a browser, check every video plays, every link resolves, carousel scrolls, mobile viewport doesn't break.

## Out of scope

- Server-side rendering, search, or interactivity beyond bulma-carousel and a play-on-hover video toggle.
- A separate `/leaderboard/` page (the table is inlined).
- Mobile-specific A/B styling beyond what bulma provides out of the box.
- Re-uploading or modifying the existing HF dataset; the HF link in the hero just keeps pointing at the current dataset.
- Regression tests / pytest contracts. None survive the rewrite.

## Acceptance check

- `index.html` is the only HTML at the repo root.
- No `tools/`, `snapshot/`, `tests/`, `hf_staging/`, `.audit_artifacts/`, `vm-web/`, `_wmbench_src/` remain.
- `static/videos/` is between 30 and 60 mp4 files, total < 200 MB.
- Opening `index.html` directly off disk renders every section, plays every video, and triggers no console errors or 404s in DevTools.
- Every external link in the hero opens the expected destination.
- The leaderboard table shows all 8 models × 14 columns (overall + 13 laws) with consistent per-law header colors.
- `git status` is clean after the changes are committed; the repo size is reasonable (no LFS).
