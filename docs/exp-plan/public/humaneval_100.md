# humaneval-100 selection spec

> Status: **specification frozen**, artifact (`snapshot/index/humaneval_100.json`) is currently a stub awaiting prompt-data ingest in a later round. The selection rule below is deterministic and reproducible once prompts are available.

## Why a 100-prompt subset?

The full `humaneval` set in wmbench is **250 prompts**. The plan's §6 wants the public site to host video evidence for a **100-prompt** subset (matching vm-web's 100 task × 9 model layout) so the GitHub-Pages snapshot stays under ~1 GB while still covering every physical law and every leaderboard model. Prompts outside the 100 still appear in the leaderboard table — they just say "video not public, scores only" when expanded.

This file is the rule that produces the 100. Anyone re-running the selection with the same inputs must get the same 100.

## Inputs (all from `_wmbench_src/`)

- `_wmbench_src/data/paperdemo/manifest.csv` — paperdemo selections per law (the curator-approved seed pool)
- `_wmbench_src/evals/eval_registry.json` — every (model, dataset, subset, evaluator, schema) result on humaneval
- `_wmbench_src/data/vis_datasets.json` — humaneval dataset entry → `prompts_json` path
- `_wmbench_src/data/prompts/humaneval/<file>.json` — prompt records, keyed by `prompt_id` (added in a future round)
- `_wmbench_src/data/scores/<evaluator>/<id>.json` — per-prompt evaluator scores (added on demand)

`humaneval-100` is a **set of 100 `prompt_id` values** chosen entirely from these inputs.

## Selection algorithm (deterministic)

1. **Intersection gate.** Restrict the candidate pool to `prompt_id` values that appear in:
   - the `humaneval_set` subset, and
   - **every** leaderboard model in `MODEL_CATALOG` that has a row in `eval_registry.json` for `dataset=humaneval, subset=humaneval_set` with `coverage = 1.0`.
   This guarantees that for each surviving prompt, every published model has a video.

2. **paperdemo seed (must-include).** Force-include every `(law, video_id)` pair listed in `paperdemo/manifest.csv` whose `video_id` resolves to a prompt that survived step 1. These are curator-blessed; they cannot be dropped.

3. **Per-law quota.** With the 13 physical laws (`paperdemo` law column), allocate a target of `floor(100 / 13) = 7` slots per law plus `100 - 7*13 = 9` extra slots distributed across laws by descending paperdemo n_ann coverage (i.e. laws where humans annotated more thoroughly get the spare slots first; tie-break by alphabetical law name). After this step the per-law target is fixed, e.g. `{collision: 8, gravity: 8, ..., shadow: 7}`.

4. **Score-based fill.** Within each law's remaining quota (after deducting the paperdemo seed for that law), rank the surviving candidate prompts on a deterministic composite score:

   ```
   score(prompt) =
       0.40 · variance(scores across leaderboard models)        # prompts that separate models > prompts where everyone agrees
     + 0.30 · coverage(non-null evaluator results)              # prefer prompts with full evaluator support
     + 0.30 · mid-difficulty(distance from mean phys_avg = 3.0) # prefer mid-table prompts over trivially easy / hard
   ```
   - Each component is min-max normalized to `[0,1]` per law.
   - Tie-break on lower numeric `prompt_id` (so the rule is total-order deterministic).
   Take the top `quota - len(seed_for_law)` prompts to fill the law.

5. **Manual review gate (optional, recorded).** A human curator may swap a selected prompt for another prompt **of the same law** that survived step 1. Each swap is recorded in `snapshot/index/humaneval_100.json` under `manual_overrides` with reason. If `manual_overrides` is empty (default), the file is reproducible from steps 1–4 alone.

## Output schema (`snapshot/index/humaneval_100.json`)

```jsonc
{
  "schema_version": "1",
  "selected_at": "2026-MM-DDThh:mm:ssZ",
  "selection_inputs": {
    "registry_sha256": "<sha256 of _wmbench_src/evals/eval_registry.json at selection time>",
    "paperdemo_manifest_sha256": "<sha256 of _wmbench_src/data/paperdemo/manifest.csv>",
    "model_catalog_sha256": "<sha256 of _wmbench_src/videogen/runner/MODEL_CATALOG.py>",
    "humaneval_prompts_sha256": "<sha256 of _wmbench_src/data/prompts/humaneval/<file>.json>"
  },
  "law_quotas": {                          // step 3 result
    "collision": 8, "gravity": 8, "buoyancy": 7, ...
  },
  "prompts": [                             // length 100 once populated
    {
      "prompt_id": 1303,
      "law": "collision",
      "source": "paperdemo_seed" | "score_fill" | "manual_override",
      "score_components": {                 // null for paperdemo_seed
        "variance": 0.81,
        "coverage": 1.0,
        "mid_difficulty": 0.62
      }
    }
  ],
  "manual_overrides": [
    {
      "law": "shadow",
      "removed_prompt_id": 814,
      "added_prompt_id": 919,
      "reason": "video for 814 has corrupted last frame in cosmos-predict2.5-2b output"
    }
  ]
}
```

## Reproducibility

1. Run `tools/build_snapshot.py --select-humaneval-100`. The builder:
   - Hashes the four inputs above.
   - Executes steps 1–4 deterministically (no randomness, no time-dependent ordering).
   - Applies any `manual_overrides` that already exist in the prior `humaneval_100.json` (so re-running does not silently drop curator decisions).
   - Writes `snapshot/index/humaneval_100.json`.
2. Run `tools/verify_snapshot.py`. The manifest sha256 must match.
3. Any change in `selection_inputs.*_sha256` flags the file as out-of-date and forces a rebuild.

## What changes between rounds

- **Round 1 (now):** spec frozen; `snapshot/index/humaneval_100.json` is committed as a stub (`prompts: []`, `selection_inputs` filled with the actual sha256 of the four inputs at this moment, `law_quotas` precomputed but `prompts` empty pending Round 2's prompt-data ingest). The build does not crash; downstream pages just see an empty selection.
- **Round 2:** add `_wmbench_src/data/prompts/humaneval/<file>.json` and the per-evaluator score JSONs, then run `--select-humaneval-100` for real. The selected 100 land in `snapshot/index/humaneval_100.json`; the leaderboard / video gallery start using it as the gate for "video public" vs "scores only".

## Open questions

- Should `score_fill` look at all evaluators or only at one canonical evaluator? Current spec averages across all evaluators with `coverage=1.0`. If we later switch to a single canonical evaluator (e.g. `claude-opus-4.7`), bump `schema_version`.
- Should we re-select on every wmbench refresh, or pin until manually re-rolled? Current default is **pin** (do not auto-rotate); the build flow re-runs the selection only when `--select-humaneval-100` is passed explicitly.
