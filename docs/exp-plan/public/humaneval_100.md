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

This is the algorithm the Round-4 selector implements verbatim, after the
upstream evidence — the per-model `humaneval_set` score JSONs ingested into
`_wmbench_src/data/scores/` — replaced the prompt manifest's sparse
`per_model_scores` as the gate input. Earlier draft revisions described a
strict per-law fill; the current rule (a) sources scores from the JSONs,
not from `anonymous_humaneval_set.json`, (b) normalises each composite
component once over the entire kept pool, and (c) assigns prompts to laws
with a capacity-constrained allocation rather than a per-law top-N fill.

1. **Source the prompt → {model: phys_score} table.** For every leaderboard
   model with a `humaneval_set` `coverage=1.0` registry row, pick the newest
   resolvable score JSON (`_wmbench_src/data/scores/<evaluator>/...`).
   `_build_humaneval_score_table` walks each chosen JSON's `results[*]`
   array and reads `prompt_id = result.video`, `phys = result.physical.avg`
   (with documented fallbacks to other shapes / `general_avg`). The result
   is `{prompt_id: {model_key: phys_score}}` and per-prompt
   `physical_laws` (union over the JSONs that mention the prompt).

2. **Strict intersection gate.** Restrict the candidate pool to prompt_ids
   whose score-table entry covers every model in
   `_humaneval_full_model_set(registry)` (the leaderboard models with at
   least one humaneval_set cov=1.0 row). No fallback gate. Prompts dropped
   here are accounted for in `humaneval_100.json.gate_stats`.

3. **paperdemo seed (must-include).** A surviving prompt is "seeded" for
   law `L` if its filename stem matches a paperdemo `src_filename` whose
   `law == L`. The seed locks the prompt into that law before the
   capacity assignment runs.

4. **Per-law quota.** 13 physical laws × `floor(100/13)=7` base slots = 91,
   plus 9 spare slots distributed by descending paperdemo `n_ann` coverage
   (laws where humans annotated more thoroughly get spare slots first,
   alphabetical tie-break). Result: a fixed `{collision: 8, gravity: 8, …,
   shadow: 7}` map summing to exactly 100.

5. **Composite score, min-max normalised over the entire kept pool.**
   For every surviving prompt:
   ```
   raw(prompt) = (variance(per_model_scores),
                  coverage = len(per_model_scores) / |full_model_set|,
                  mid_difficulty = 1 − |mean(per_model_scores) − 3| / 3)
   ```
   Each axis is min-max normalised across the kept pool; the composite is
   `0.40·variance_norm + 0.30·coverage_norm + 0.30·mid_difficulty_norm`.
   The composite is law-agnostic; per-law normalisation would only re-order
   within-law ranks and not change the absolute ranking the assignment
   walks over.

6. **Capacity-constrained multi-label assignment.** A prompt is *eligible*
   for any law in its `physical_laws` array (multi-label, not just the
   primary). The assignment walks all kept prompts in deterministic order:
   - Step a. Apply paperdemo seeds first; subtract from quota.
   - Step b. For non-seed prompts in `(-composite, prompt_id_numeric)`
     order, pick the eligible law whose remaining capacity is highest
     (alphabetical tie-break) and decrement. If no eligible law has
     capacity, the prompt is skipped.
   The result: the 13 fixed quotas are filled to 100 from a pool of ~250
   strict-intersection prompts; every selected prompt has a per-prompt
   composite score and a deterministic law assignment.

7. **Manual review gate (optional, recorded).** A curator may swap a
   selected prompt for another prompt **of the same law** that survived
   step 2. Each swap is recorded in `snapshot/index/humaneval_100.json`
   under `manual_overrides` with reason. If `manual_overrides` is empty
   (default), the artifact is reproducible from steps 1–6 alone.

## Output schema (`snapshot/index/humaneval_100.json`)

```jsonc
{
  "schema_version": "1",
  "selected_at": "2026-MM-DDThh:mm:ssZ",
  "selection_inputs": {
    "registry_sha256": "<sha256 of _wmbench_src/evals/eval_registry.json at selection time>",
    "paperdemo_manifest_sha256": "<sha256 of _wmbench_src/data/paperdemo/manifest.csv>",
    "model_catalog_sha256": "<sha256 of _wmbench_src/videogen/runner/MODEL_CATALOG.py>",
    "humaneval_prompts_sha256": "<sha256 of _wmbench_src/data/prompts/anonymous_humaneval_set.json>",
    "humaneval_score_jsons": {"<model_key>": "<rel score json path under _wmbench_src/>"},
    "humaneval_score_jsons_sha256": {"<model_key>": "<sha256 of that JSON>"}
  },
  "law_quotas": {                          // step 3 result
    "collision": 8, "gravity": 8, "buoyancy": 7, ...
  },
  "intersection_gate_full_model_set": ["cosmos-predict2.5-2b", "...", "veo-3.1"],
  "gate_stats": { "kept": 250, "dropped_no_score": 0, "dropped_partial_models": 0 },
  "per_law_audit": {                       // per-law diagnostics
    "collision": {"quota": 8, "seeds": 5, "fill": 3, "available_for_law": 200},
    ...
  },
  "effective_law_counts": {"collision": 8, "gravity": 8, ...},
  "n_selected": 100,                       // hard target after the new selector
  "prompts": [
    {
      "prompt_id": "collision_156",
      "law": "collision",
      "source": "paperdemo_seed" | "score_fill" | "manual_override",
      "score_components": {                 // normalised, may be null for manual_override
        "variance_norm": 0.81,
        "coverage_norm": 1.0,
        "mid_difficulty_norm": 0.62
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
