# Visual + Structural Audit and Repair of phyground.github.io Pages, with Concurrent HF Video Staging

## Goal Description

Walk through every published page of the phyground.github.io site (rendered locally and on the user-fork deployment at https://phyground.github.io/), identify display defects with both visual and structural checks, classify each by symptom and root cause, and fix the repo-owned defects in the source of truth (Jinja2 templates under `tools/templates/`, asset sources under `tools/static_src/`, snapshot/build logic under `tools/build_*.py`, or `snapshot/index/site_config.json` upstream). The audit uses Playwright with Chromium for screenshots and console capture, plus a structural HTML/link auditor for static checks. Subagents may parallelize independent page-class audits. Existing pytest contracts (snapshot, materialize, determinism) must remain green; new regression tests are added when a fix exposes a contract gap.

In parallel with the repair work, stage the videos and first-frame images that the fixed site references into the existing `hf_staging/` directory by feeding `tools/stage_hf_assets.py` from the local source tree at `/shared/user60/workspace/worldmodel/wmbench/data`. After every repair cycle that changes the referenced video set, regenerate `snapshot/HF_UPLOAD_MANIFEST.json`, re-stage from the local source, and then materialize `hf_staging/` canonically via `tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` so the staging tree always matches the current snapshot exactly. When repairs are finalized, run the same canonical materialize step one final time, then upload the staging tree to the existing dataset repo `juyil/phygroundwebsitevideo` on HuggingFace and verify with `tools/smoke_test_hf.py`.

### Page Set Under Audit (13 pages plus 1 populated state)

- 4 top-level: `/`, `/leaderboard/`, `/videos/`, `/about/`
- 1 sub: `/videos/compare/` (placeholder state)
- 1 sub-state: `/videos/compare/?prompt_id=<valid>` for at least one valid prompt id taken from `snapshot/index/site_config.json`
- 8 model: `/models/{cosmos-predict2.5-14b,cosmos-predict2.5-2b,ltx-2-19b-dev,ltx-2.3-22b-dev,omniweaving,veo-3.1,wan2.2-i2v-a14b,wan2.2-ti2v-5b}/`

### Defect Reporting Model

Each defect record carries BOTH a symptom and a root cause:

Symptom (`symptom_class`, what the user sees):
- `runtime_error` — uncaught JS error or console error
- `failed_request` — repo-owned asset/network request returned non-2xx (CSS, JS, inline-referenced media)
- `empty_or_hidden_main` — main content area renders empty or only chrome (nav/footer)
- `broken_link_or_asset` — relative `href`/`src` that does not resolve
- `media_unavailable` — external media (HuggingFace) fails to load
- `interaction_broken` — primary interaction (filter, modal, deep-link) does not work
- `other_visual` — defects not covered by the above (screenshots attached)

Root cause (`root_cause_class`, where the fix lives):
1. `template` — `tools/templates/**`
2. `css` — `tools/static_src/css/base.css`
3. `js` — `tools/static_src/js/{gallery,compare,leaderboard}.js`
4. `data` — `tools/build_snapshot.py` or `snapshot/index/**`
5. `external` — HuggingFace media or other off-repo asset
6. `deploy` — divergence between fork and locally rebuilt site

## Acceptance Criteria

Following TDD philosophy, each criterion includes positive and negative tests for deterministic verification.

- AC-1: Audit covers all 13 pages plus one populated `/videos/compare/?prompt_id=<valid>` state, run against both the local rebuild and the fork URL.
  - Positive Tests (expected to PASS):
    - Audit report enumerates 14 page entries × 2 sources (local, fork). Each entry records final URL, HTTP status, viewport, console-error count, failed-request count, and screenshot path.
    - The chosen `prompt_id` for the populated compare state matches an entry under `snapshot/index/site_config.json`'s prompts index.
  - Negative Tests (expected to FAIL):
    - Report missing any page in the 14-entry × 2-source matrix.
    - Report covers only one source (local or fork) instead of both.
    - The populated compare state uses a `prompt_id` not present in `snapshot/index/site_config.json`.

- AC-2: Repaired defects land only in source-of-truth files; rendered HTML is not hand-edited.
  - Positive Tests (expected to PASS):
    - `python tools/build_site.py --config snapshot/index/site_config.json` regenerates published HTML, and `git diff` against HEAD on the rendered files (`index.html`, `leaderboard/index.html`, `videos/index.html`, `videos/compare/index.html`, `about/index.html`, `models/<key>/index.html`) is empty after fixes are committed.
  - Negative Tests (expected to FAIL):
    - A fix applied directly to any rendered HTML file without a corresponding change in `tools/templates/**`, `tools/static_src/**`, `tools/build_*.py`, or `snapshot/index/**`.
    - A clean rebuild after a fix produces a diff against committed HTML.

- AC-3: No broken local relative link or asset reference on any audited page.
  - Positive Tests (expected to PASS):
    - The structural auditor parses each rendered HTML, resolves every relative `href`/`src`/`<source src>`, and confirms each target file exists under repo root or is an explicit absolute URL.
  - Negative Tests (expected to FAIL):
    - A deliberately broken `rel('static/js/...')` path or a deleted figure file causes the auditor to fail with the exact missing path.

- AC-4: Visual + runtime capture taken for every audited page entry at desktop viewport (1280×800).
  - Positive Tests (expected to PASS):
    - Each of the 14 × 2 entries has a screenshot file (non-zero size), a captured console-error log, and a captured failed-request log.
    - Screenshots show rendered content beyond chrome (navbar/footer); body scroll height exceeds combined chrome height.
  - Negative Tests (expected to FAIL):
    - Any missing screenshot, missing log, or zero-byte capture.
    - A screenshot showing only chrome and an empty `<main>` after fixes.

- AC-5: Every defect record carries `symptom_class`, `root_cause_class`, and `root_cause_path` (or external URL for `external`).
  - Positive Tests (expected to PASS):
    - Each row has all three fields populated; rows are addressable by `(page, viewport, source)`.
  - Negative Tests (expected to FAIL):
    - A row missing any of `symptom_class`, `root_cause_class`, or `root_cause_path`.

- AC-6: All defects classified `template`, `css`, `js`, or `data` are repaired; no repo-owned defect surfaced by the audit remains open after the final re-audit, including non-visual structural and runtime defects (broken local links, failed repo-owned requests, console errors, empty `<main>`).
  - Positive Tests (expected to PASS):
    - The post-fix re-run of the audit shows zero open defects of class `template`/`css`/`js`/`data`.
  - Negative Tests (expected to FAIL):
    - A remaining repo-owned defect after rebuild.
    - Any non-visual structural defect (broken local link, failed repo-owned request, console error, empty `<main>`) ignored on the grounds that it does not appear in screenshots.

- AC-7: External media defects (`external`) are explicitly classified and recorded; they are never silently hidden. Hides happen only with an explicit per-card decision.
  - Positive Tests (expected to PASS):
    - Each `external` row carries one of {DATA_FIX_OPEN, HIDE_DECIDED, UPSTREAM_REPORTED, ACCEPTED_AS_IS} with rationale.
    - Any template-level hide of an external asset has a paired `HIDE_DECIDED` row referencing the affected `(page, asset)`.
  - Negative Tests (expected to FAIL):
    - A template change that hides cards or sections without a recorded triage decision.
    - An `external` row missing the triage value or rationale.

- AC-8: Existing pytest contracts remain green; new regression tests are added when a repaired defect exposes a contract gap.
  - Positive Tests (expected to PASS):
    - `pytest -q` passes after fixes (baseline 28 tests stay green).
    - For each `data` defect repaired, a new test in `tests/test_snapshot_contracts.py` (or sibling) locks the invariant.
    - For each newly invariant template behavior repaired, a corresponding contract test is added.
  - Negative Tests (expected to FAIL):
    - Removing or weakening any existing contract test to make a fix pass.
    - A `data` repair that leaves no regression test guarding the fixed invariant.

- AC-9: Runtime/render health is captured and gated on every audited page entry.
  - Positive Tests (expected to PASS):
    - Each audited entry shows zero uncaught JS/console errors and zero failed requests for repo-owned assets after fixes.
    - The main content area is non-empty (body scroll height exceeds chrome height) on every audited page.
  - Negative Tests (expected to FAIL):
    - Any audited page with a console error after fixes.
    - Any audited page with a failed repo-owned request after fixes.
    - Any audited page where `<main>` renders empty or only contains chrome after fixes.

- AC-10: A reusable Playwright + structural-audit harness is committed under `tools/site_audit/`.
  - Positive Tests (expected to PASS):
    - `tools/site_audit/run_audit.py` and `tools/site_audit/structural_audit.py` exist and are invocable from the repo root.
    - `tools/site_audit/README.md` documents install steps, command-line usage, and artifact location.
    - A pinned Playwright dependency is added to `requirements.txt` (or a sibling `requirements-audit.txt` referenced by the README).
  - Negative Tests (expected to FAIL):
    - Audit driven only from `/tmp` or scratch paths with no committed entry point.
    - No README or no documented install steps for Playwright.

- AC-11: Video and first-frame assets the fixed site references are staged into `hf_staging/` from the local source tree at `/shared/user60/workspace/worldmodel/wmbench/data`, in lockstep with site repairs.
  - Positive Tests (expected to PASS):
    - After every repair cycle that changes the referenced video set, `python tools/build_snapshot.py --select-humaneval-100` regenerates `snapshot/HF_UPLOAD_MANIFEST.json`, and `python tools/stage_hf_assets.py /shared/user60/workspace/worldmodel/wmbench/data` reports `n_missing_upstream == 0` (every manifest target resolves under the local wmbench tree).
    - The local source root used by every stage invocation is exactly `/shared/user60/workspace/worldmodel/wmbench/data`.
  - Negative Tests (expected to FAIL):
    - `stage_hf_assets.py` reports any missing upstream files after staging (manifest target without a matching local source).
    - Staging from any path other than `/shared/user60/workspace/worldmodel/wmbench/data` (e.g. an unrelated checkout) without a recorded reason in the audit log.

- AC-12: `hf_staging/` is canonically materialized at the end of every repair cycle that changes the referenced video set, and once again as the final step before upload, via the existing `--materialize --clean` postcondition.
  - Positive Tests (expected to PASS):
    - After every repair cycle that changes the referenced video set, `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` exits 0; the post-condition `set(files in hf_staging/) == set(targets in HF_UPLOAD_MANIFEST.json)` holds with exactly `n_total_files` entries against the just-regenerated manifest.
    - The same canonical materialize is re-run one final time after the audit reports zero open repo-owned defects, and `python tools/verify_snapshot.py` passes after that final staging.
  - Negative Tests (expected to FAIL):
    - A repair cycle that changes the referenced video set without re-running `--materialize hf_staging/ --clean` afterwards (so `hf_staging/` lags behind `snapshot/HF_UPLOAD_MANIFEST.json`).
    - A stale file lingering in `hf_staging/` after rerun (i.e. running materialize without `--clean` on a non-empty dir).
    - `hf_staging/` containing fewer or more files than the manifest declares at any cycle boundary.

- AC-13: The staged tree is uploaded to the existing dataset repo `juyil/phygroundwebsitevideo` on HuggingFace and the smoke test passes.
  - Positive Tests (expected to PASS):
    - `huggingface-cli upload --repo-type dataset juyil/phygroundwebsitevideo hf_staging .` completes successfully; `huggingface-cli repo create juyil/phygroundwebsitevideo --type dataset` is run only when the repo does not already exist (idempotent first-time path).
    - `python tools/smoke_test_hf.py` reports `OK` on every probe (representative video URL, representative first-frame URL, dataset root README).
    - Existing site HF URLs in `snapshot/index/site_config.json` and `HF_PREFIX` in `tests/conftest.py` remain pointed at `juyil/phygroundwebsitevideo` (no rewriting needed).
  - Negative Tests (expected to FAIL):
    - Uploading to any repo other than `juyil/phygroundwebsitevideo`.
    - `tools/smoke_test_hf.py` reporting any `FAIL` after upload.
    - Modifying `HF_PREFIX` or any HF URL in `snapshot/index/site_config.json` to a different repo name.

## Path Boundaries

Path boundaries define the acceptable range of implementation quality and choices.

### Upper Bound (Maximum Acceptable Scope)
The implementation includes: a reusable Playwright + structural-audit harness committed under `tools/site_audit/` with a documented entry point and pinned dependency; a defect log produced for all 13 pages plus one populated compare state, on both local and fork sources, at desktop viewport; root-cause fixes for every `template`/`css`/`js`/`data` defect surfaced by the audit (including non-visual structural and runtime defects); explicit triage records for every `external` defect; new pytest contracts locking invariants exposed by fixes; a final re-audit producing zero open repo-owned defects; a short verification note confirming the local rebuild matches committed HTML; an `hf_staging/` tree materialized in lockstep with site repairs from `/shared/user60/workspace/worldmodel/wmbench/data`; a successful `huggingface-cli upload` to `juyil/phygroundwebsitevideo`; and `python tools/smoke_test_hf.py` reporting all probes OK.

### Lower Bound (Minimum Acceptable Scope)
The implementation includes: a Playwright-driven screenshot + console-capture run wired into a committed entry point under `tools/site_audit/`; a structural HTML/link audit covering all 13 pages plus one populated compare state, on local rebuild and the fork URL, at desktop viewport; root-cause fixes for every repo-owned defect surfaced by the audit on audited pages — including non-visual structural and runtime defects (broken local links, failed repo-owned requests, console errors, empty `<main>`), not just defects that are visible in screenshots; explicit triage records for every `external` defect found; existing pytest contracts continue to pass; `hf_staging/` materialized via `tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` after every repair cycle that changes the referenced video set (and once more as the final step before upload), with every manifest target resolving from `/shared/user60/workspace/worldmodel/wmbench/data`; the staging tree uploaded to `juyil/phygroundwebsitevideo`; and `tools/smoke_test_hf.py` returning OK on at least one video probe and one first-frame probe.

### Allowed Choices
- Can use:
  - Playwright Python (`playwright`) with Chromium; either headless or headed.
  - A pure-Python structural auditor (stdlib `html.parser`/`urllib`/`pathlib`).
  - Optional Node-based Playwright if Python bindings are unavailable in the environment, provided `tools/site_audit/README.md` documents both paths.
  - Subagents (Explore, general-purpose) for per-page-class audits where convenient.
  - Artifact directory either gitignored under `.audit_artifacts/<round>/` (default) or committed under `docs/exp-plan/audit/<round>/` for review.
  - Existing HF tooling: `tools/build_hf_upload_manifest.py` (manifest + materialize), `tools/stage_hf_assets.py` (copies missing manifest sources from a wmbench root into `_wmbench_src/data/...`), `tools/smoke_test_hf.py`, and the documented flow in `tools/hf_upload.md`.
  - `huggingface-cli login` once with a write token for `juyil/phygroundwebsitevideo`; `huggingface-cli repo create ... --type dataset` only when the repo does not already exist.
- Cannot use:
  - Hand edits to any rendered HTML file (`index.html`, `leaderboard/index.html`, `videos/index.html`, `videos/compare/index.html`, `about/index.html`, `models/<key>/index.html`) without a corresponding change in `tools/templates/**`, `tools/static_src/**`, `tools/build_*.py`, or `snapshot/index/**`.
  - Removing or weakening any existing pytest contract to make audit pass.
  - Hiding cards/sections to mask `external` defects without a recorded triage decision.
  - Skipping the audit for any of the 13 pages or the populated compare state.
  - Mobile-viewport audits are out of scope for this round.
  - Staging from any source path other than `/shared/user60/workspace/worldmodel/wmbench/data` without an explicit recorded reason.
  - Uploading to any HF repo other than `juyil/phygroundwebsitevideo`.
  - Renaming or relocating the staging directory away from `hf_staging/`.
  - Rewriting `HF_PREFIX` in `tests/conftest.py` or HF URLs in `snapshot/index/site_config.json` to a different repo name.

> **Note on Deterministic Designs**: The user-confirmed scope (desktop only, committed harness under `tools/site_audit/`, external defects recorded only) narrows the bounds; the upper and lower bounds differ mainly in the breadth of regression tests added, not in the audit configuration.

## Feasibility Hints and Suggestions

> **Note**: This section is for reference and understanding only. These are conceptual suggestions, not prescriptive requirements.

### Conceptual Approach

1. Add an audit driver (e.g. `tools/site_audit/run_audit.py`) that, given `--target {local,fork}`, drives Playwright/Chromium across the 14 audited URL entries (13 pages + one populated compare state) at desktop viewport (1280×800) and saves per-entry records (final URL, HTTP status, console errors, failed requests, screenshot path).
2. Add a structural HTML auditor (`tools/site_audit/structural_audit.py`) that parses each rendered HTML and verifies every relative `href`/`src`/`<source>` resolves on disk, and that every external URL inside the rendered HTML is either an explicit absolute URL or allow-listed against `snapshot/HF_UPLOAD_MANIFEST.json` for HuggingFace assets.
3. Optionally spawn subagents in parallel for: (a) top-level + about, (b) videos + compare, (c) all 8 model pages. Each returns a per-class defect log.
4. Aggregate logs and populate `symptom_class` + `root_cause_class` + `root_cause_path` on every row.
5. Apply repairs in `tools/templates/**`, `tools/static_src/**`, or `tools/build_snapshot.py` / `snapshot/index/**`; rebuild via `python tools/build_site.py --config snapshot/index/site_config.json`; re-audit until zero open repo-owned defects remain.
6. Add regression tests under `tests/` and confirm `pytest -q` still passes.

### Relevant References
- `tools/build_site.py` — Jinja2 renderer; sole writer of `models/<key>/`.
- `tools/templates/{home,leaderboard,videos,about,models,components,base.html}` — Jinja templates (source of truth).
- `tools/static_src/css/base.css`, `tools/static_src/js/{gallery,compare,leaderboard}.js` — asset source of truth (mirrored to `static/` at build time).
- `tools/build_snapshot.py`, `snapshot/index/site_config.json`, `snapshot/HF_UPLOAD_MANIFEST.json` — snapshot pipeline.
- `tests/conftest.py` — `PUBLISHED_MODEL_KEYS`, `HIDDEN_MODEL_KEYS`, `HF_PREFIX`, `HF_MANIFEST_PATH`.
- `tests/test_snapshot_contracts.py`, `tests/test_materialize_contracts.py`, `tests/test_determinism.py` — existing pytest contracts (28 baseline tests).
- `.github/workflows/` — GitHub Actions deploy to GitHub Pages on push to master.

## Dependencies and Sequence

### Milestones
1. Audit infrastructure
   - Phase A: Playwright bootstrap (`tools/site_audit/run_audit.py`, README, dependency pin).
   - Phase B: Structural auditor (`tools/site_audit/structural_audit.py`).
2. Capture and classify
   - Phase A: Run captures for 13 pages + populated compare state, on local rebuild and fork URL, at desktop viewport. Subagents may parallelize across page-classes.
   - Phase B: Aggregate defect log; populate `symptom_class` and `root_cause_class` on every row.
3. Repair (with concurrent HF staging)
   - Phase A: Template/CSS/JS root-cause fixes (parallelizable across pages).
   - Phase B: Snapshot/build fixes (sequential — can affect multiple pages).
   - Phase C: External-media triage records (record only, no auto-hide; per-card `HIDE_DECIDED` only when explicitly chosen).
   - Phase D: Concurrent HF staging — after every repair cycle that changes the referenced video set, re-run `python tools/build_snapshot.py --select-humaneval-100` to refresh `snapshot/HF_UPLOAD_MANIFEST.json`, then `python tools/stage_hf_assets.py /shared/user60/workspace/worldmodel/wmbench/data` to pull missing sources, and finally `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` to canonically rematerialize the staging tree against the just-regenerated manifest.
4. Regression coverage
   - Phase A: Add pytest contracts for invariants exposed by repaired defects.
   - Phase B: Re-run full audit; confirm zero open repo-owned defects.
5. HF dataset materialization and upload
   - Phase A: One final `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` after the audit reports zero open repo-owned defects, to produce the canonical staging tree against the final manifest. (Per-cycle materialize already happened inside Milestone 3 Phase D.)
   - Phase B: `huggingface-cli repo create juyil/phygroundwebsitevideo --type dataset` (only if the repo does not already exist) and `huggingface-cli upload --repo-type dataset juyil/phygroundwebsitevideo hf_staging .`
   - Phase C: `python tools/smoke_test_hf.py` and confirm all probes OK.
6. Deployment verification
   - Phase A: Local rebuild confirms templates produce committed HTML (no diff after rebuild).
   - Phase B: Smoke fork URL after Pages deploy.

Milestone 2 depends on Milestone 1; Milestone 3 depends on Milestone 2; Milestone 4 depends on Milestone 3; Milestone 5 depends on Milestone 4 (the staging tree must reflect the final fixed snapshot); Milestone 6 depends on Milestone 5 (the fork URL only plays HF media correctly after upload completes). Within Milestone 3, Phases A–C and Phase D run in lockstep: Phase D rebuilds the manifest after each fix that changes the referenced video set.

## Task Breakdown

Each task includes exactly one routing tag:
- `coding`: implemented by Claude
- `analyze`: executed via Codex (`/humanize:ask-codex`)

| Task ID | Description | Target AC | Tag (`coding`/`analyze`) | Depends On |
|---------|-------------|-----------|----------------------------|------------|
| task1 | Implement audit driver under `tools/site_audit/run_audit.py` (Playwright + Chromium, console + failed-request capture); add README + Playwright dependency pin | AC-1, AC-4, AC-9, AC-10 | coding | - |
| task2 | Implement structural HTML/link auditor at `tools/site_audit/structural_audit.py` | AC-3, AC-10 | coding | - |
| task3 | Resolve URL set: 13 pages + one populated compare state from `snapshot/index/site_config.json`; wire into the audit driver | AC-1 | coding | task1 |
| task4 | Run audit captures across local + fork sources at desktop viewport (subagents may parallelize per page-class) | AC-1, AC-4, AC-9 | coding | task2, task3 |
| task5 | Classify each defect with `symptom_class` + `root_cause_class` + `root_cause_path` | AC-5 | analyze | task4 |
| task6 | Repair `template`/`css`/`js` defects in `tools/templates/**` and `tools/static_src/**` | AC-2, AC-6 | coding | task5 |
| task7 | Repair `data` defects via `tools/build_snapshot.py` and `snapshot/index/**` | AC-2, AC-6 | coding | task5 |
| task8 | Record explicit triage decisions for `external` media defects (record-only policy; no auto-hide) | AC-7 | analyze | task5 |
| task9 | Add pytest contracts locking invariants exposed by repaired defects | AC-8 | coding | task6, task7 |
| task10 | Re-run full audit; confirm zero open repo-owned defects | AC-6, AC-9 | coding | task9 |
| task11 | After every repair cycle that changes the referenced video set, re-run `python tools/build_snapshot.py --select-humaneval-100`, `python tools/stage_hf_assets.py /shared/user60/workspace/worldmodel/wmbench/data`, and `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` so `hf_staging/` is canonically rematerialized against the just-regenerated manifest | AC-11, AC-12 | coding | task6, task7 |
| task12 | After the audit reports zero open repo-owned defects, run `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` one final time and `python tools/verify_snapshot.py` to lock the canonical staging tree before upload | AC-12 | coding | task9, task11 |
| task13 | Upload `hf_staging/` to `juyil/phygroundwebsitevideo` (`huggingface-cli repo create` only if missing, then `huggingface-cli upload`); run `python tools/smoke_test_hf.py` and confirm all probes OK | AC-13 | coding | task12 |
| task14 | Smoke fork URL post-rebuild and post-HF-upload; verify local rebuild matches committed HTML and that videos play on `/`, `/videos/`, `/videos/compare/?prompt_id=<valid>`, and at least one model page | AC-2, AC-6, AC-13 | analyze | task10, task13 |

## Claude-Codex Deliberation

### Agreements
- The 13 published pages are the right base page set for the audit.
- The populated `/videos/compare/?prompt_id=<valid>` state is required because the substantive content is hidden behind the query param.
- Source of truth for fixes is `tools/templates/**`, `tools/static_src/**`, `tools/build_*.py`, and `snapshot/index/**`; rendered HTML must not be hand-edited.
- Existing pytest contracts (`tests/test_snapshot_contracts.py`, `tests/test_materialize_contracts.py`, `tests/test_determinism.py`) must remain green; targeted regression tests are added per repaired defect.
- External media issues are recorded, never silently hidden.
- Audit covers local rebuild plus the named fork URL; canonical org URL is not in the baseline gate.
- Defect reporting splits into `symptom_class` (what users see) plus `root_cause_class` (where the fix lives).

### Resolved Disagreements
- Topic: Hard-required committed Playwright harness (Claude v1) vs harness optional (Codex Round 1). Resolution: harness is required at both upper and lower bounds because the user opted to commit it (DEC-2 = commit). Rationale: user prefers repeatable audits across releases.
- Topic: Subagent parallelism as an AC (Claude v1) vs implementation hint (Codex Round 1). Resolution: removed from ACs and recorded in Implementation Notes. Rationale: draft mentions subagents as parallelism guidance, not a deliverable.
- Topic: Lower-bound "fix one representative defect per class" (Claude v1) vs "fix every repo-owned defect found" (Codex Round 1) vs "fix every visible repo-owned defect" (Claude v2). Resolution: Lower Bound now reads "every repo-owned defect surfaced by the audit, including non-visual structural and runtime defects". Rationale: matches AC-6 instead of weakening it; matches the page-centric framing of the draft.
- Topic: Defect taxonomy as root-cause-only (Claude v1) vs symptom + root cause (Codex Round 1). Resolution: split into `symptom_class` + `root_cause_class`. Rationale: "无法显示" is symptom language; root-cause-only blurs which check finds vs which file fixes.
- Topic: Compare-page placeholder-only coverage (Claude v1) vs include populated state (Codex Round 1). Resolution: include `/videos/compare/?prompt_id=<valid>` for at least one valid prompt id.
- Topic: Canonical-URL spot-check in baseline (Claude v1) vs fork-only (Codex Round 1). Resolution: removed from baseline; canonical URL can be a manual sanity check outside the audit gate.
- Topic: Snapshot/build edits in scope and all 8 model pages audited as DEC items (Claude v1) vs defaulted (Codex Round 1). Resolution: defaulted in plan; both are clearly required for the goal.
- Topic: Mobile viewport in scope (DEC-1, originally PENDING). Resolution: desktop only for this round per user decision.
- Topic: Commit reusable audit tooling (DEC-2, originally PENDING). Resolution: commit minimal harness under `tools/site_audit/` per user decision.
- Topic: External-media triage policy (DEC-3, originally PENDING). Resolution: record only, do not auto-hide; per-card `HIDE_DECIDED` only when explicitly chosen.
- Topic: HF video staging scope (added by user during Phase 6 after the audit/repair plan converged). Resolution: stage videos and first-frames concurrently with site repairs from `/shared/user60/workspace/worldmodel/wmbench/data` into `hf_staging/`, materialize canonically via `tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean`, then upload to `juyil/phygroundwebsitevideo` via the existing `tools/hf_upload.md` flow. Tests' `HF_PREFIX` and snapshot HF URLs remain unchanged.
- Topic: HF target repo (DEC-4, originally PENDING). Resolution: reuse existing `juyil/phygroundwebsitevideo` (no URL rewriting required across `snapshot/index/site_config.json`, `tests/conftest.py`, or `tools/hf_upload.md`).
- Topic: Staging directory (DEC-5, originally PENDING). Resolution: reuse `hf_staging/` (matches existing `--materialize` behavior; already gitignored).

### Convergence Status
- Final Status: `converged`. Rounds executed: 1 first-pass Codex analysis + 3 challenge/refine rounds for the audit/repair plan; the HF staging+upload extension was added by the user post-convergence and uses entirely existing committed tooling (`tools/stage_hf_assets.py`, `tools/build_hf_upload_manifest.py`, `tools/smoke_test_hf.py`, `tools/hf_upload.md`), so no further Codex round was required.

## Pending User Decisions

All originally pending decisions were resolved by the user during plan generation:

- DEC-1: Mobile viewport in scope.
  - Claude Position: desktop-only by default; mobile is opt-in.
  - Codex Position: desktop-only is acceptable; the draft does not require mobile.
  - Tradeoff Summary: mobile coverage doubles capture/repair surface (especially for `/leaderboard/` tables); leaving it out may miss a real class of defects.
  - Decision Status: Resolved by user — Desktop only.

- DEC-2: Commit reusable audit tooling.
  - Claude Position: commit a minimal harness under `tools/site_audit/`.
  - Codex Position: a one-shot inspection does not require committed tooling.
  - Tradeoff Summary: committing pays a small dependency-pin cost but enables re-audit on every release; not committing keeps the repo lean but loses repeatability.
  - Decision Status: Resolved by user — Commit minimal harness under `tools/site_audit/`.

- DEC-3: How to act on confirmed `external` media failures.
  - Claude Position: record the triage decision but do not auto-redesign; if a card must be hidden, that decision is explicit per card.
  - Codex Position: recording is in scope; hiding/redesigning around external failures should not be auto-assumed.
  - Tradeoff Summary: aggressive hiding risks masking systemic upstream issues; passive recording risks visible broken media.
  - Decision Status: Resolved by user — Record only, do not auto-hide; per-card `HIDE_DECIDED` only with explicit rationale.

- DEC-4: HF target repo for the staged videos.
  - Claude Position: reuse existing `juyil/phygroundwebsitevideo`.
  - Codex Position: not posed in this round (added after convergence).
  - Tradeoff Summary: reusing avoids URL rewriting in `snapshot/index/site_config.json`, `tests/conftest.py` (`HF_PREFIX`), and `tools/hf_upload.md`; a fresh repo would force coordinated edits across all three.
  - Decision Status: Resolved by user — Reuse `juyil/phygroundwebsitevideo`.

- DEC-5: On-disk staging directory.
  - Claude Position: reuse `hf_staging/`.
  - Codex Position: not posed in this round (added after convergence).
  - Tradeoff Summary: reusing matches existing `--materialize --clean` behavior and the gitignore entry; a fresh path would require parallel edits to materialize logic.
  - Decision Status: Resolved by user — Reuse `hf_staging/`.

## Implementation Notes

### Execution Model (mandatory)
- Drive this plan with the **superpowers `subagent-driven-development`** skill using **opus** subagents, and follow **TDD** (red-green-refactor) inside every subagent: write or extend a failing test before any production code, make it pass, then refactor.
- Concretely: every `coding` task in the breakdown above is dispatched to an opus subagent via the superpowers harness; the subagent first lands a failing pytest (or a failing audit assertion), then implements the minimum change to make it green, then commits. `analyze` tasks are still routed through `/humanize:ask-codex`.

### Code Style Requirements
- Implementation code, comments, file names, and CLI flags MUST NOT contain plan-progress markers like "AC-", "Milestone", "Step", or "Phase". Use descriptive, domain-appropriate names instead (e.g. `defect_class`, `symptom`, `root_cause_path`, `audit_artifacts/`).
- These markers are for plan documentation only, not for the resulting codebase.

### Implementation Hints
- Subagent parallelism is a hint, not a deliverable. Where convenient, split per-page-class audit work across subagents (e.g. one for top + about, one for videos + compare, one for the 8 model pages); where serial is simpler, serial is fine.
- Lightweight a11y spot-checks (keyboard nav, label presence on key controls, severe contrast issues) are optional and outside the AC gate.
- Artifact directory should default to `.audit_artifacts/<round>/` and be gitignored. Committing under `docs/exp-plan/audit/<round>/` is allowed when the round produces evidence worth review.
- The structural auditor and runtime auditor produce independent rows in the same defect log; deduplicate by `(page, source, symptom_class, root_cause_path)` when aggregating.
- For the populated compare state, prefer a `prompt_id` that has all 8 published models scored (verify against `snapshot/index/site_config.json`'s prompts index) so the comparison renders the maximum content surface.

### HF Staging and Upload Hints
- The video source root is exactly `/shared/user60/workspace/worldmodel/wmbench/data`. `tools/stage_hf_assets.py` already handles the `data/videos/<model>/<stem>.mp4` and `data/videos/<model>-humaneval/<stem>.mp4` lookup variants.
- The flow is fully documented in `tools/hf_upload.md`. The plan reuses that flow verbatim for the `--materialize`, `huggingface-cli upload`, and `tools/smoke_test_hf.py` steps.
- Stage and rematerialize per cycle: every time a repair changes which videos the snapshot references, re-run `python tools/build_snapshot.py --select-humaneval-100`, then `python tools/stage_hf_assets.py /shared/user60/workspace/worldmodel/wmbench/data`, then `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean`. All three scripts are idempotent and the `--clean` flag ensures stale files cannot accumulate across cycles.
- Run the same `python tools/build_hf_upload_manifest.py --materialize hf_staging/ --clean` one final time after the audit reports zero open repo-owned defects, so the staging tree's post-condition `set(files) == set(targets)` holds against the final manifest immediately before upload.
- `huggingface-cli login` requires a write token for `juyil/phygroundwebsitevideo`. The `repo create ... --type dataset` step is one-time and idempotent — failures from an existing repo are safe to ignore.
- After upload, run `python tools/smoke_test_hf.py` (which probes one video URL, one first-frame URL, and the dataset README). Any `FAIL` is a hard regression for AC-13.
- Do not modify `HF_PREFIX` in `tests/conftest.py` or any `huggingface.co/datasets/juyil/phygroundwebsitevideo/...` URL inside `snapshot/index/site_config.json`; the staged tree is uploaded into the same existing repo so URLs remain valid.

--- Original Design Draft Start ---

1. 去https://phyground.github.io/ 每个网页读一读, 用 playwright, chrome screenshot.让codex起截图软件可视化看. 把无法显示的部分修一修. 
2. 可以并行的地方, 多开几个 subagent.
--- Original Design Draft End ---
