# `_wmbench_src/` provenance

This directory is a **frozen, hard copy** of selected files from the wmbench
repository, ingested for use by `tools/build_snapshot.py`. After Round 1 the
phyground.github.io build does not require the wmbench checkout to exist.

## Source

- Repository: `wmbench` (sibling working tree of this repo)
- Source path: `/shared/user60/workspace/worldmodel/wmbench`
- HEAD at copy time: **`618d5d102bcd38924765ae63f3d683e162f594b2`**
- Source working-tree state: dirty (untracked `vm-web/`, `docs/exp-plan/public/`; modified `.gitignore`). The files listed below were copied from the working tree, not from a tagged commit, but their sha256 sums are recorded so the snapshot pipeline can detect drift independent of git state.
- Copy date: **2026-05-08**
- Copy method: byte-exact copy (`cp` from the source paths verbatim).

## Files & sha256

| Path under `_wmbench_src/` | sha256 |
|----------------------------|--------|
| `data/paperdemo/figs/boundary_interaction.pdf` | `52868ec0ae753f308a8029f253e6cf010f9687de03c1f3226956e5324a1bda91` |
| `data/paperdemo/figs/buoyancy.pdf` | `5d86a075d7f1a1025080a05b134cad1bc516cad090f0d7f374faa211622b95c8` |
| `data/paperdemo/figs/collision.pdf` | `3c75da9095e61c5d5111d7e49a7a07c4b23d24f736d88d66841e7c385fac61cb` |
| `data/paperdemo/figs/displacement.pdf` | `971f9c446e82079e9bc825b1753a66e2a5c53ffe679e86a9527c24bf4bd0dead` |
| `data/paperdemo/figs/flow_dynamics.pdf` | `ed02b7662aff792740bdd780b5ef3b3dfd0828eb6b4f16fbd95bd609184ddf81` |
| `data/paperdemo/figs/fluid_continuity.pdf` | `d7ef5126b85e1ebd7bda9c97fc89f17fce141f9ec5a22d29a1a1b6749fa33386` |
| `data/paperdemo/figs/gravity.pdf` | `0cb2e85ebbbd9db6db049d1e7e9f3f168dd802e11726935f7c7dff0c07729422` |
| `data/paperdemo/figs/impenetrability.pdf` | `166d9c82bf43d5ada658c8639223310173715eab0e6d775e2bcc6e8d15578ed8` |
| `data/paperdemo/figs/inertia.pdf` | `ef3a1b24713e4510d33d566a07bfbe849060f60fdabff3d8e6f105250af0390b` |
| `data/paperdemo/figs/material.pdf` | `a325438d2197378025435737db3520c2d7778c357bd3322660a1c1951f1262fe` |
| `data/paperdemo/figs/momentum.pdf` | `259c532987a22e5250e42767a5895c60dbd1ff7e34f83ee9f08a53dcaf40dd23` |
| `data/paperdemo/figs/reflection.pdf` | `959161a1ffac0444e6e1e35a3f21427a7ea7d3d078a4bb90ee3589d8391bef1e` |
| `data/paperdemo/figs/shadow.pdf` | `856adb352de360cdb2226128b718daab0b62786a7d87b226c7d369606736ec79` |
| `data/paperdemo/manifest.csv` | `55fe4d712c24c103598c6a3647931ef719dfb551c95c675ad138aee609fadd17` |
| `data/vis_datasets.json` | `69e4761736b5865072345b6c8a753c80b109ef76e5284fb90581c206f591e08e` |
| `evals/eval_registry.json` | `0d791883c6ec8dec03d1f6db890d6681cdf987a3313e97314d0af83b4ed616fe` |
| `evals/eval_types.py` | `0ec24f2652e7c56e080c202fb3468e074d5e43f9d06d5691718130d6847b7f5b` |
| `videogen/runner/MODEL_CATALOG.py` | `6c88b41361242b3ed0275c9618f54bca140b42876a0d8bd6f9caf71301b4fb57` |

## Layout (mirrors source paths)

```
_wmbench_src/
├── PROVENANCE.md
├── data/
│   ├── paperdemo/
│   │   ├── figs/*.pdf            # 13 physical-law illustrations
│   │   └── manifest.csv          # law,video_id,n_ann,dataset(=model),src_filename,dst_path
│   └── vis_datasets.json
├── evals/
│   ├── eval_registry.json        # master leaderboard registry
│   └── eval_types.py             # scoring-schema enums
└── videogen/
    └── runner/
        └── MODEL_CATALOG.py      # video-model catalog
```

Larger artifacts that the plan references but are **not** copied here:

- `data/videos/<model>-<dataset>/*.mp4` — video outputs are hosted on HuggingFace at <https://huggingface.co/juyil>; the snapshot only carries URL references, never bytes.
- `data/scores/<evaluator>/<id>.json` — per-evaluator raw score JSONs are not yet materialized into `_wmbench_src/`. They will be added on demand in a later round when the leaderboard "Download raw JSON" action is wired up.

## Refresh procedure

When the upstream wmbench files change and the public site needs the update:

1. Re-run the byte-exact copy (or a small `tools/refresh_wmbench_src.sh` once that exists).
2. Recompute sha256 sums and update this file.
3. Run `python tools/build_snapshot.py` to regenerate `snapshot/`.
4. Run `python tools/verify_snapshot.py` to assert the manifest is still consistent.
5. Commit the diff (so this `PROVENANCE.md` always reflects the working tree).
