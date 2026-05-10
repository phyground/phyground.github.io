#!/usr/bin/env python3
"""Build snapshot/ from _wmbench_src/ deterministically.

Reads frozen wmbench inputs (`_wmbench_src/`) and writes the publish-grade
snapshot the static site depends on:

  snapshot/
  ├── MANIFEST.json                 # sha256 over every file under snapshot/
  ├── index/
  │   ├── site_config.json          # site, headline, models, datasets,
  │   │                             # leaderboard_entries, paperdemo,
  │   │                             # videos_index, prompts_index, build_meta
  │   ├── eval_registry.frozen.json
  │   ├── paperdemo.manifest.csv
  │   ├── vis_datasets.frozen.json
  │   ├── model_catalog.frozen.json
  │   ├── humaneval_100.json        # populated by --select-humaneval-100
  │   ├── humaneval_prompts.json    # 250-prompt anonymous humaneval set
  │   └── figs/<law>.pdf            # 13 paperdemo law illustrations
  └── scores/<evaluator>/<basename>.json   # slim score JSONs the leaderboard links to

The build is atomic (writes to snapshot.staging/ first, then renames),
deterministic (same _wmbench_src/ → byte-identical snapshot/), and offline.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WMBENCH_SRC = REPO_ROOT / "_wmbench_src"
SNAPSHOT_DIR = REPO_ROOT / "snapshot"
STAGING_DIR = REPO_ROOT / "snapshot.staging"

HF_BASE = "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main"

# The HF dataset's `videos/` directory holds exactly these 8 model dirs (the
# strict-intersection set of leaderboard models that scored every humaneval
# prompt with coverage=1.0). Per the user's layout match: every other model
# key is excluded from emitted HF URLs because it has no asset on the
# dataset.
_HF_PUBLISHED_MODELS = frozenset({
    "cosmos-predict2.5-14b",
    "cosmos-predict2.5-2b",
    "ltx-2-19b-dev",
    "ltx-2.3-22b-dev",
    "omniweaving",
    "veo-3.1",
    "wan2.2-i2v-a14b",
    "wan2.2-ti2v-5b",
})


# ---------- low-level helpers ----------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj) -> None:
    """Deterministic JSON write: sorted keys, 2-space indent, trailing newline, UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


# ---------- MODEL_CATALOG.py parser ----------

# Examples of description strings the catalog ships:
#   "CogVideoX-5B-I2V — 6s (49f @ 8fps) 720×480"
#   "CogVideoX1.5-5B-I2V — 10s (81f @ 16fps) 1360×768"
#   "Wan2.2-TI2V-5B — ti2v 704×1280, 81f @ 16fps (diffusers)"
#   "Wan2.2-I2V-A14B — i2v 480P/720P, 81f @ 16fps, MoE 14B active (diffusers)"
#   "Cosmos-Predict2.5-2B — Image2World, 93f @ 16fps (~5.8s)"
#   "HunyuanVideo-I2V — i2v 720×1280, 129f @ 24fps (~5.4s), 13B transformer"
#   "LTX-2 19B FP8 — ti2v + audio (~40GB VRAM)"
_RE_FRAMES = re.compile(r"(\d+)\s*f\b")
_RE_FPS = re.compile(r"@\s*(\d+)\s*fps")
_RE_RES = re.compile(r"(\d{3,5})\s*[×x*]\s*(\d{3,5})")
_RE_PARAMS_B = re.compile(r"(?:^|[^A-Za-z0-9])(\d+(?:\.\d+)?)\s*B(?:\b|[^A-Za-z])", re.IGNORECASE)


def _parse_description(desc: str) -> dict:
    """Best-effort extraction of frames / fps / resolution / params_b from a description string."""
    if not desc:
        return {}
    out: dict = {}
    m = _RE_FRAMES.search(desc)
    if m:
        out["frames"] = int(m.group(1))
    m = _RE_FPS.search(desc)
    if m:
        out["fps"] = int(m.group(1))
    m = _RE_RES.search(desc)
    if m:
        out["resolution"] = f"{m.group(1)}x{m.group(2)}"
    m = _RE_PARAMS_B.search(desc)
    if m:
        out["params_b"] = float(m.group(1))
    return out


def _params_b_from_key(key: str) -> float | None:
    """Fallback: extract a billions-of-parameters number from the catalog key."""
    m = re.search(r"(\d+(?:\.\d+)?)b", key, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _extract_model_catalog(catalog_py: Path) -> list[dict]:
    """Parse `_wmbench_src/videogen/runner/MODEL_CATALOG.py` without executing it."""
    tree = ast.parse(catalog_py.read_text(encoding="utf-8"), filename=str(catalog_py))
    models: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if not (name.startswith("_") and name.endswith("_RAW")):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        try:
            raw = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
        for key, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            desc = cfg.get("description") or ""
            parsed = _parse_description(desc)
            if "params_b" not in parsed:
                pb = _params_b_from_key(key)
                if pb is not None:
                    parsed["params_b"] = pb
            entry = {
                "key": key,
                "wrapper_module": cfg.get("wrapper_module"),
                "wrapper_class": cfg.get("wrapper_class"),
                "model": cfg.get("model"),
                "description": desc,
                "family": cfg.get("family"),
                "params_b": parsed.get("params_b"),
                "fps": parsed.get("fps"),
                "frames": parsed.get("frames"),
                "resolution": parsed.get("resolution"),
                "kwargs": cfg.get("kwargs"),
            }
            models.append(entry)
    models.sort(key=lambda m: m["key"])
    return models


# ---------- registry / manifest readers ----------

def _read_eval_registry(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_paperdemo_manifest(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                video_id = int(row["video_id"])
            except (ValueError, KeyError):
                video_id = row.get("video_id")
            try:
                n_ann = int(row["n_ann"])
            except (ValueError, KeyError):
                n_ann = 0
            rows.append({
                "law": row["law"],
                "video_id": video_id,
                "n_ann": n_ann,
                "model": row["dataset"],          # plan §2: "dataset(=model)"
                "src_filename": row["src_filename"],
                "src_path": row["dst_path"],
            })
    return rows


def _read_vis_datasets(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_humaneval_prompts(path: Path) -> list[dict]:
    """Read the anonymous humaneval prompt set."""
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as fh:
        d = json.load(fh)
    return d.get("prompts", []) if isinstance(d, dict) else (d or [])


# ---------- score JSON copy + URL rewrite ----------

def _score_relpath(source_json: str) -> str | None:
    """Resolve an `eval_registry.json` source_json value to a path under `_wmbench_src/`.

    Tries, in order:
      1. Exact relative path under `_wmbench_src/` (handles `data/scores/...`,
         `data/training/...`, `tmp/...`).
      2. Absolute paths that contain `wmbench/<rel>` — strip up to and including
         `wmbench/` and check `_wmbench_src/<rel>`.
      3. `data/scores/<evaluator>/<basename>` with a one-level subdir fallback
         (`cot/`, `direct/`, `fpsablation/`, `subq/`) so registry rows that record
         the basename without the subdir still resolve.
      4. External absolute paths get mapped to `data/scores/_external/<basename>`.
    Returns the path relative to `_wmbench_src/`, or None if no candidate exists.
    """
    if not source_json:
        return None
    sj = source_json
    if sj.startswith("/"):
        if "wmbench/" in sj:
            tail = sj.split("wmbench/", 1)[1]
            if (WMBENCH_SRC / tail).is_file():
                return tail
        ext_rel = f"data/scores/_external/{Path(sj).name}"
        if (WMBENCH_SRC / ext_rel).is_file():
            return ext_rel
        return None
    direct = WMBENCH_SRC / sj
    if direct.is_file():
        return sj
    if sj.startswith("data/scores/"):
        parts = Path(sj).parts
        if len(parts) >= 4:
            evaluator = parts[2]
            base = Path(sj).name
            for sub in ("", "cot", "direct", "fpsablation", "subq"):
                cand = (WMBENCH_SRC / "data/scores" / evaluator / sub / base
                        if sub else WMBENCH_SRC / "data/scores" / evaluator / base)
                if cand.is_file():
                    return str(cand.relative_to(WMBENCH_SRC))
    return None


def _snapshot_score_url(rel_under_wmbench: str) -> str | None:
    """Map a `_wmbench_src/`-relative score path to its repo-root-relative URL.

    The score JSONs are copied into `snapshot/scores/...` by
    :func:`build` (see step 5), so the URL we expose in
    `site_config.json` must also be prefixed with `snapshot/` — that way
    the rendered HTML resolves the file under
    ``<repo_root>/snapshot/scores/...`` regardless of which page (root,
    `/leaderboard/`, `/models/<key>/`) the link is rendered on, because
    Jinja's ``rel(...)`` helper just adds the per-page ``../`` depth on
    top of this stored URL.

    `data/scores/<...>`     → `snapshot/scores/<...>`
    `data/training/<...>`   → `snapshot/scores/_training/<...>`
    `tmp/<...>`             → `snapshot/scores/_tmp/<...>`
    Anything else is a programming bug.
    """
    if rel_under_wmbench.startswith("data/scores/"):
        return "snapshot/" + rel_under_wmbench[len("data/"):]
    if rel_under_wmbench.startswith("data/training/"):
        return "snapshot/scores/_training/" + rel_under_wmbench[len("data/training/"):]
    if rel_under_wmbench.startswith("tmp/"):
        return "snapshot/scores/_tmp/" + rel_under_wmbench[len("tmp/"):]
    return None


# ---------- leaderboard dedup (coverage-aware, drops unresolvable currents) ----------

def _dedup_leaderboard(registry: list[dict]) -> tuple[list[dict], list[dict]]:
    """Group registry rows by (video_model, dataset, subset, evaluator, schema).

    Per plan §6: filter `coverage > 0` before choosing the newest row as `current`;
    coverage-zero reruns are kept under `history` so they are not lost.

    A group is published only if at least one of its valid (coverage > 0) rows
    resolves to a snapshot-downloadable score JSON. Groups whose every valid
    row is unresolvable are returned as a separate `unpublished` list so the
    audit (`leaderboard_unpublished.json`) records them but the public table
    never shows a row with a broken `Download raw JSON` cell.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in registry:
        key = (
            row.get("video_model"),
            row.get("dataset"),
            row.get("subset"),
            row.get("evaluator"),
            row.get("schema"),
        )
        groups[key].append(row)

    def _annotate(row: dict) -> dict:
        r = dict(row)
        rel = _score_relpath(r.get("source_json") or "")
        if rel:
            r["source_status"] = "available"
            r["source_url_snapshot"] = _snapshot_score_url(rel)
            r["_score_relpath"] = rel
        else:
            r["source_status"] = "missing"
            r["source_url_snapshot"] = None
            r["_score_relpath"] = None
        return r

    published: list[dict] = []
    unpublished: list[dict] = []
    for key, rows in groups.items():
        valid = [r for r in rows if (r.get("coverage") or 0) > 0]
        invalid = [r for r in rows if r not in valid]
        if not valid:
            continue
        valid_annotated = [_annotate(r) for r in valid]
        invalid_annotated = [_annotate(r) for r in invalid]
        # Filter to valid rows whose source resolves; pick newest.
        valid_with_source = [r for r in valid_annotated if r["source_url_snapshot"]]
        all_valid_sorted = sorted(
            valid_annotated,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        invalid_sorted = sorted(
            invalid_annotated,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        video_model, dataset, subset, evaluator, schema = key
        if not valid_with_source:
            unpublished.append({
                "video_model": video_model,
                "dataset": dataset,
                "subset": subset,
                "evaluator": evaluator,
                "schema": schema,
                "reason": "no valid row's source_json could be resolved into _wmbench_src/",
                "rows": all_valid_sorted + invalid_sorted,
            })
            continue
        valid_with_source_sorted = sorted(
            valid_with_source,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        current = valid_with_source_sorted[0]
        # History keeps every other row (with-source older + without-source + invalid).
        history_rows = [r for r in all_valid_sorted if r is not current] + invalid_sorted
        published.append({
            "video_model": video_model,
            "dataset": dataset,
            "subset": subset,
            "evaluator": evaluator,
            "schema": schema,
            "current": current,
            "history": history_rows,
        })
    published.sort(key=lambda e: (
        e["dataset"] or "",
        e["video_model"] or "",
        e["evaluator"] or "",
        e["schema"] or "",
        e["subset"] or "",
    ))
    unpublished.sort(key=lambda e: (
        e["dataset"] or "",
        e["video_model"] or "",
        e["evaluator"] or "",
        e["schema"] or "",
        e["subset"] or "",
    ))
    return published, unpublished


# ---------- paperdemo grouping ----------

def _group_paperdemo(rows: list[dict]) -> list[dict]:
    by_law: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        # Paperdemo videos remap to `videos/<model>/<stem>.mp4` on the HF
        # dataset (the dataset has no `paperdemo/` folder; it shares the
        # videos/ tree with the humaneval set). Only emit the URL when the
        # (model, stem) pair has an actual file on disk in the HF-published
        # set; otherwise the card renders without a `<video>` source.
        stem = Path(r["src_filename"]).stem
        if _video_exists_locally(r["model"], stem):
            video_url = f"{HF_BASE}/videos/{r['model']}/{stem}.mp4"
        else:
            video_url = None
        by_law[r["law"]].append({
            "model": r["model"],
            "video_id": r["video_id"],
            "n_ann": r["n_ann"],
            "src_filename": r["src_filename"],
            "src_path": r["src_path"],
            "video_url_hf": video_url,
        })
    out = []
    for law in sorted(by_law.keys()):
        videos = sorted(by_law[law], key=lambda v: (v["model"], str(v["video_id"])))
        out.append({
            "law": law,
            # Paperdemo PDFs and PNGs are copied into `snapshot/index/figs/`
            # by `build()`, so the URL must be repo-root-relative with the
            # `snapshot/` prefix; Jinja's `rel(...)` adds the per-page
            # `../` depth on top.
            "fig_pdf": f"snapshot/index/figs/{law}.pdf",
            "fig_png": f"snapshot/index/figs/{law}.png",
            "videos": videos,
            "n_ann_total": sum(int(v["n_ann"]) for v in videos),
        })
    return out


def _law_n_ann(paperdemo_grouped: list[dict]) -> dict[str, int]:
    """Total n_ann per law from paperdemo manifest. Used by humaneval-100 quota."""
    return {entry["law"]: entry.get("n_ann_total", 0) for entry in paperdemo_grouped}


# ---------- model union + dataset summary ----------

def _all_known_models(catalog: list[dict],
                     registry: list[dict],
                     paperdemo: list[dict]) -> list[dict]:
    """Union of MODEL_CATALOG, eval_registry.video_model, and paperdemo model column."""
    by_key: dict[str, dict] = {}
    for entry in catalog:
        by_key[entry["key"]] = {
            "key": entry["key"],
            "display_name": entry.get("description") or entry["key"],
            "family": entry.get("family") or "Unknown",
            "params_b": entry.get("params_b"),
            "fps": entry.get("fps"),
            "frames": entry.get("frames"),
            "resolution": entry.get("resolution"),
            "wrapper_module": entry.get("wrapper_module"),
            "model": entry.get("model"),
            "source": "MODEL_CATALOG",
        }
    for row in registry:
        k = row.get("video_model")
        if not k or k in by_key:
            continue
        by_key[k] = {
            "key": k,
            "display_name": k,
            "family": "External",
            "params_b": _params_b_from_key(k),
            "fps": None,
            "frames": None,
            "resolution": None,
            "wrapper_module": None,
            "model": None,
            "source": "eval_registry",
        }
    for r in paperdemo:
        k = r.get("model")
        if not k or k in by_key:
            continue
        by_key[k] = {
            "key": k,
            "display_name": k,
            "family": "External",
            "params_b": _params_b_from_key(k),
            "fps": None,
            "frames": None,
            "resolution": None,
            "wrapper_module": None,
            "model": None,
            "source": "paperdemo",
        }
    return [by_key[k] for k in sorted(by_key.keys())]


def _datasets_summary(vis_datasets: dict) -> list[dict]:
    """Collapse vis_datasets.json's per-(model,dataset) entries into per-dataset summaries."""
    seen: dict[str, dict] = {}
    for entry in vis_datasets.get("datasets", []):
        name = entry.get("name", "")
        ds = None
        for candidate in ("video_phy_2", "physics_iq", "humaneval", "wmb", "openvid"):
            if name.endswith(candidate) or candidate in name:
                ds = candidate
                break
        if ds is None:
            ds = name
        if ds in seen:
            continue
        seen[ds] = {
            "key": ds,
            "prompts_json": entry.get("prompts_json"),
        }
    return [seen[k] for k in sorted(seen.keys())]


# ---------- humaneval-100 selection ----------

LAWS_13 = [
    "boundary_interaction", "buoyancy", "collision", "displacement",
    "flow_dynamics", "fluid_continuity", "gravity", "impenetrability",
    "inertia", "material", "momentum", "reflection", "shadow",
]


def _law_quotas(law_n_ann: dict[str, int] | None = None) -> dict[str, int]:
    """Per humaneval_100.md §3:

      - floor(100 / 13) = 7 base slots per law.
      - 100 - 7*13 = 9 extra slots, distributed by descending paperdemo `n_ann`
        coverage. Tie-break alphabetical by law name.
    If `law_n_ann` is None or every law has zero annotations, the alphabetical
    fallback is used so the function still returns a deterministic value.
    """
    base = 100 // len(LAWS_13)
    extra = 100 - base * len(LAWS_13)
    quotas = {law: base for law in LAWS_13}
    if law_n_ann and any(v > 0 for v in law_n_ann.values()):
        ranked = sorted(LAWS_13, key=lambda l: (-int(law_n_ann.get(l, 0)), l))
    else:
        ranked = list(LAWS_13)
    for law in ranked[:extra]:
        quotas[law] += 1
    return quotas


def _humaneval_full_model_set(registry: list[dict]) -> set[str]:
    """Step-1 intersection-gate input: every leaderboard `video_model` that has
    at least one humaneval_set row with `coverage = 1.0`.
    """
    return {
        r.get("video_model")
        for r in registry
        if r.get("dataset") == "humaneval"
        and r.get("subset") == "humaneval_set"
        and (r.get("coverage") or 0) == 1.0
        and r.get("video_model")
    }


def _minmax_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _latest_humaneval_score_per_model(registry: list[dict]) -> dict[str, str]:
    """For every model in `_humaneval_full_model_set`, pick the newest humaneval_set
    cov=1.0 source_json that resolves into `_wmbench_src/`. Returns
    `{video_model: <relpath under _wmbench_src/>}`.
    """
    full_models = _humaneval_full_model_set(registry)
    by_model: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in registry:
        if (r.get("dataset") != "humaneval"
                or r.get("subset") != "humaneval_set"
                or (r.get("coverage") or 0) != 1.0):
            continue
        m = r.get("video_model")
        if m not in full_models:
            continue
        rel = _score_relpath(r.get("source_json") or "")
        if rel:
            by_model[m].append((r.get("datetime") or "", rel))
    chosen: dict[str, str] = {}
    for m, rows in by_model.items():
        rows.sort(reverse=True)   # newest datetime first
        chosen[m] = rows[0][1]
    return chosen


def _physical_score_for_result(result: dict) -> float | None:
    """Best-effort per-result physical score extraction across the various
    score-JSON shapes (`physical.avg`, `physical.macro_avg`, top-level
    numeric `physical`, then `general_avg`).
    """
    phys = result.get("physical")
    if isinstance(phys, dict):
        for k in ("avg", "macro_avg", "micro_avg"):
            v = phys.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        # Sum scored-law scores divided by count.
        laws = phys.get("laws")
        if isinstance(laws, dict):
            scores = [info.get("score") for info in laws.values()
                      if isinstance(info, dict) and isinstance(info.get("score"), (int, float))]
            if scores:
                return sum(scores) / len(scores)
    if isinstance(phys, (int, float)):
        return float(phys)
    g = result.get("general_avg")
    if isinstance(g, (int, float)):
        return float(g)
    return None


def _build_humaneval_score_table(registry: list[dict]) -> tuple[dict[str, dict[str, float]],
                                                                 dict[str, list[str]],
                                                                 dict[str, str]]:
    """Source per-prompt per-model scores from the ingested `humaneval_set`
    score JSONs.

    Returns:
      prompt_scores:  {prompt_id: {model_key: phys_score}}
      prompt_laws:    {prompt_id: [physical_law, ...]}   (union across models'
                                                         results entries)
      score_files:    {model_key: relpath under _wmbench_src/}
    """
    score_files = _latest_humaneval_score_per_model(registry)
    prompt_scores: dict[str, dict[str, float]] = defaultdict(dict)
    prompt_laws: dict[str, list[str]] = {}
    for model_key, rel in score_files.items():
        path = WMBENCH_SRC / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for r in data.get("results", []) or []:
            pid = r.get("video")
            if not pid:
                continue
            score = _physical_score_for_result(r)
            if score is None:
                continue
            prompt_scores[pid][model_key] = score
            laws = r.get("physical_laws") or []
            if pid not in prompt_laws and isinstance(laws, list):
                prompt_laws[pid] = list(laws)
    return prompt_scores, prompt_laws, score_files


def _capacity_assignment(eligible: dict[str, list[str]],
                         quotas: dict[str, int],
                         seed_assignment: dict[str, str],
                         scores: dict[str, float]) -> dict[str, str]:
    """Assign each prompt_id to exactly one law subject to per-law quotas.

    `eligible[prompt_id]` is the list of laws the prompt is eligible for
    (i.e. its `physical_laws` array intersected with the quota keys).
    `seed_assignment` locks (prompt_id → law) for paperdemo seeds.
    `scores[prompt_id]` is the prompt's composite score (higher = better).
    Returns `{prompt_id: chosen_law}` for the prompts that fit.

    The algorithm is deterministic:
      1. Apply seed assignments first; subtract them from quota.
      2. For remaining prompts in descending score order (tie-break: prompt_id
         numeric component ascending), assign to the prompt's eligible law
         whose remaining capacity is highest (tie-break: alphabetical).
      3. If no eligible law has capacity, the prompt is skipped.
    """
    remaining = dict(quotas)
    assigned: dict[str, str] = {}

    # Step 1: seeds.
    for pid, law in seed_assignment.items():
        if law in remaining and remaining[law] > 0:
            assigned[pid] = law
            remaining[law] -= 1

    # Step 2: rank others by (-score, prompt_id_numeric).
    def _pid_key(s: str):
        head, _, tail = (s or "").rpartition("_")
        try:
            return (head or "", int(tail))
        except ValueError:
            return (s or "", 0)

    others = sorted(
        (pid for pid in eligible if pid not in assigned),
        key=lambda pid: (-scores.get(pid, 0.0), _pid_key(pid)),
    )
    for pid in others:
        cands = [law for law in eligible[pid] if remaining.get(law, 0) > 0]
        if not cands:
            continue
        # Choose the law with the most remaining capacity (alphabetical tie-break).
        cands.sort(key=lambda law: (-remaining[law], law))
        chosen = cands[0]
        assigned[pid] = chosen
        remaining[chosen] -= 1
    return assigned


def _select_humaneval_100(prompts: list[dict],
                          paperdemo: list[dict],
                          registry: list[dict],
                          existing_selection: dict | None,
                          input_sha256: dict,
                          built_at: str) -> dict:
    """Implements docs/exp-plan/public/humaneval_100.md §"Selection algorithm" exactly:

      Step 1. Intersection gate: prompt must have a per-model phys score for every
              `_humaneval_full_model_set` member, sourced from the ingested
              `humaneval_set` score JSONs (NOT from the prompt manifest's sparse
              `per_model_scores`). Strict — no relaxed fallback.
      Step 2. paperdemo seed: prompts whose stem matches a paperdemo
              `src_filename` are must-includes for the paperdemo law.
      Step 3. Per-law quota: floor(100/13)=7 + 9 spare slots ordered by paperdemo
              `n_ann` desc, alphabetical tie-break.
      Step 4. Capacity-constrained multi-label assignment so the 13 fixed quotas
              are filled to 100. Composite score is per-law min-max normalized
              variance / coverage / mid_difficulty; tie-break: lower numeric
              prompt_id.
      Step 5. Manual overrides preserved across rebuilds.
    """
    law_n_ann = _law_n_ann(paperdemo)
    quotas = _law_quotas(law_n_ann)
    full_model_set = _humaneval_full_model_set(registry)

    # ── Step 1: build score table from ingested score JSONs ──────
    prompt_scores, prompt_laws, score_files = _build_humaneval_score_table(registry)

    # The prompt manifest still carries `physical_laws` and `dataset`; pull a
    # secondary lookup so prompts with no score-JSON laws still get a label set.
    manifest_laws = {p.get("video"): list(p.get("physical_laws") or [])
                     for p in prompts if p.get("video")}

    # Strict intersection: every full_model_set member present.
    gate_kept: list[str] = []
    gate_dropped_no_score = 0
    gate_dropped_partial = 0
    for pid, models_to_scores in prompt_scores.items():
        if not models_to_scores:
            gate_dropped_no_score += 1
            continue
        if full_model_set and not full_model_set.issubset(models_to_scores.keys()):
            gate_dropped_partial += 1
            continue
        gate_kept.append(pid)

    # Eligibility per quota law: union of score-JSON `physical_laws` and the
    # manifest's `physical_laws` (some score JSONs may have a coarser set).
    eligible_laws: dict[str, list[str]] = {}
    for pid in gate_kept:
        laws = list(prompt_laws.get(pid) or [])
        for ml in manifest_laws.get(pid) or []:
            if ml not in laws:
                laws.append(ml)
        keep = [law for law in laws if law in quotas]
        if keep:
            eligible_laws[pid] = keep

    # ── Step 2: paperdemo seeds (lock to the paperdemo's own law). ────
    paperdemo_pid_law: dict[str, str] = {}
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            stem = Path(v["src_filename"]).stem
            if stem in eligible_laws and law_entry["law"] in eligible_laws[stem]:
                paperdemo_pid_law.setdefault(stem, law_entry["law"])

    # ── Step 4: composite score, per-law min-max normalized ──────
    # Compute raw components per (prompt, law) then normalize within law.
    # We pre-compute per-prompt composite scores _across all eligible laws_
    # using the entire kept pool, normalized once over that pool. This is a
    # simplification that keeps determinism and matches the spec's intent
    # ("min-max normalized per law" — but a prompt's score is law-agnostic,
    # so per-law normalization only meaningfully changes the relative
    # ranking inside a law and not the absolute composite). The capacity
    # assignment then chooses the law per prompt.
    raw: dict[str, dict] = {}
    for pid in gate_kept:
        scores = list(prompt_scores[pid].values())
        var = statistics.pvariance(scores) if len(scores) >= 2 else 0.0
        cov = len(scores) / max(len(full_model_set or [1]), 1)
        mean_phys = sum(scores) / len(scores)
        mid = 1.0 - abs(mean_phys - 3.0) / 3.0
        raw[pid] = {"variance": var, "coverage": cov, "mid_difficulty": max(0.0, min(mid, 1.0))}

    pid_list = list(raw.keys())
    var_n = _minmax_normalize([raw[p]["variance"] for p in pid_list])
    cov_n = _minmax_normalize([raw[p]["coverage"] for p in pid_list])
    mid_n = _minmax_normalize([raw[p]["mid_difficulty"] for p in pid_list])
    composite: dict[str, float] = {}
    norm_components: dict[str, dict[str, float]] = {}
    for pid, v, c, m in zip(pid_list, var_n, cov_n, mid_n):
        composite[pid] = 0.40 * v + 0.30 * c + 0.30 * m
        norm_components[pid] = {
            "variance_norm": round(v, 4),
            "coverage_norm": round(c, 4),
            "mid_difficulty_norm": round(m, 4),
        }

    assignment = _capacity_assignment(eligible_laws, quotas, paperdemo_pid_law, composite)

    # ── Step 5: manual overrides ──────────────────────────────
    manual_overrides = (existing_selection or {}).get("manual_overrides", []) or []

    selected: list[dict] = []
    per_law_audit: dict[str, dict] = {law: {"quota": q, "seeds": 0, "fill": 0,
                                            "available_for_law": 0}
                                       for law, q in quotas.items()}
    for law in quotas:
        per_law_audit[law]["available_for_law"] = sum(
            1 for pid, laws in eligible_laws.items() if law in laws
        )

    for pid, law in assignment.items():
        comps = norm_components.get(pid)
        is_seed = paperdemo_pid_law.get(pid) == law
        if is_seed:
            per_law_audit[law]["seeds"] += 1
        else:
            per_law_audit[law]["fill"] += 1
        selected.append({
            "prompt_id": pid,
            "law": law,
            "source": "paperdemo_seed" if is_seed else "score_fill",
            "score_components": comps,
        })

    # Apply manual overrides.
    for ov in manual_overrides:
        rm = ov.get("removed_prompt_id")
        add = ov.get("added_prompt_id")
        ov_law = ov.get("law")
        if rm:
            selected = [s for s in selected if s["prompt_id"] != rm]
        if add and ov_law:
            selected.append({
                "prompt_id": add,
                "law": ov_law,
                "source": "manual_override",
                "score_components": None,
            })

    selected.sort(key=lambda s: (s["law"], str(s["prompt_id"])))

    effective_counts = defaultdict(int)
    for s in selected:
        effective_counts[s["law"]] += 1

    # Per-model score-file sha256 for full reproducibility of the gate.
    per_model_sha = {
        m: _sha256_file(WMBENCH_SRC / rel) if (WMBENCH_SRC / rel).is_file() else None
        for m, rel in score_files.items()
    }

    note = None
    if len(selected) < 100:
        # Pure-data shortfall — rare under the new gate but reportable.
        cap = ", ".join(f"{law}={info['available_for_law']}"
                        for law, info in sorted(per_law_audit.items())
                        if info["available_for_law"] < info["quota"])
        note = (
            f"Selected {len(selected)} prompts. Cap below 100 because some laws "
            f"have fewer eligible prompts than their quota: {cap or '(no per-law shortfall)'}."
        )

    return {
        "schema_version": "1",
        "selected_at": built_at,
        "selection_inputs": {
            **input_sha256,
            "humaneval_score_jsons": dict(sorted(score_files.items())),
            "humaneval_score_jsons_sha256": dict(sorted(per_model_sha.items())),
        },
        "law_quotas": quotas,
        "law_n_ann": law_n_ann,
        "intersection_gate_full_model_set": sorted(full_model_set),
        "gate_stats": {
            "kept": len(gate_kept),
            "dropped_no_score": gate_dropped_no_score,
            "dropped_partial_models": gate_dropped_partial,
        },
        "per_law_audit": per_law_audit,
        "effective_law_counts": dict(sorted(effective_counts.items())),
        "n_selected": len(selected),
        "prompts": selected,
        "manual_overrides": manual_overrides,
        "note": note,
    }


def _humaneval_100_stub(input_sha256: dict, prompts_sha256: str | None) -> dict:
    quotas = _law_quotas()
    return {
        "schema_version": "1",
        "selected_at": None,
        "selection_inputs": {**input_sha256, "humaneval_prompts_sha256": prompts_sha256},
        "law_quotas": quotas,
        "effective_law_counts": {},
        "n_selected": 0,
        "prompts": [],
        "manual_overrides": [],
        "note": "Stub: --select-humaneval-100 was not requested for this build.",
    }


# ---------- prompts index for the compare page ----------

_OPENVID_FILENAME_RE = re.compile(r"^([A-Za-z0-9_\-]+)_(\d+)_(\d+)to(\d+)$")


def _openvid_realvideo_meta(stem: str, openvid_db: dict) -> dict | None:
    """Parse `<youtube_id>_<index>_<start>to<end>` and look up upstream metadata.

    `openvid_db` is the dict from `_wmbench_src/data/prompts/openvid/openvid.json`'s
    `prompts` key (filename → metadata).
    """
    m = _OPENVID_FILENAME_RE.match(stem)
    out: dict = {}
    if m:
        out["youtube_id"] = m.group(1)
        out["youtube_url"] = (
            f"https://www.youtube.com/watch?v={m.group(1)}&t={m.group(3)}s"
        )
        out["time_range"] = {"start_s": int(m.group(3)), "end_s": int(m.group(4))}
    fname = stem + ".mp4"
    rec = openvid_db.get(fname) if isinstance(openvid_db, dict) else None
    if isinstance(rec, dict):
        for k in ("caption", "domain", "source_law", "expected_outcome"):
            if rec.get(k):
                out[k] = rec[k]
    return out or None


def _read_openvid_db() -> dict:
    p = WMBENCH_SRC / "data" / "prompts" / "openvid" / "openvid.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return d.get("prompts") or {}


def _prompts_index(prompts: list[dict],
                   prompt_scores: dict[str, dict[str, float]] | None = None) -> dict:
    """Per-prompt index keyed by prompt_id.

    `per_model_scores` and `per_model_videos` come from the in-snapshot score
    JSONs (`prompt_scores`, built by `_build_humaneval_score_table`) so the
    compare page and per-model fallback see every published humaneval model
    that scored a prompt — not just the 4-model subset the prompt manifest's
    `per_model_scores` records. The manifest scores are kept as a secondary
    lookup so prompts that exist only in the manifest still render text +
    first_frame context.
    """
    openvid_db = _read_openvid_db()
    prompt_scores = prompt_scores or {}
    out: dict[str, dict] = {}
    for p in prompts:
        pid = p.get("video")
        ds = p.get("dataset") or ""
        if not pid:
            continue
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        score_jsons = prompt_scores.get(pid) or {}
        manifest_scores = p.get("per_model_scores") or {}
        # Prefer in-snapshot score-JSON values where they exist; fall back to
        # the manifest's per_model_scores for any model the JSONs don't cover.
        merged_scores = {**manifest_scores, **score_jsons}
        # Only emit per_model_videos URLs for (model, prompt) pairs whose
        # source bytes are present locally; otherwise the rendered site would
        # embed an HF URL that 404s after upload. The score is still surfaced
        # so the score table on the compare page stays complete.
        per_model_videos = {
            model_key: _video_hf_url(model_key, ds, pid)
            for model_key in merged_scores
            if _video_exists_locally(model_key, pid)
        }
        rv = _openvid_realvideo_meta(pid, openvid_db) if ds == "openvid" else None
        out[pid] = {
            "prompt_id": pid,
            "dataset": ds,
            "prompt": p.get("prompt"),
            "physical_laws": p.get("physical_laws") or [],
            "difficulty": p.get("difficulty") or {},
            "per_model_scores": merged_scores,
            "per_model_videos": per_model_videos,
            "first_frame_url": ff_url,
            "realvideo": rv,
        }
    return out


# ---------- per-prompt + per-model HuggingFace URL helpers ----------

def _video_hf_url(model_key: str, source_dataset: str, stem: str) -> str:
    """HF target follows wmbench's actual on-disk layout: `videos/<model>/<stem>.mp4`.

    `source_dataset` is no longer part of the path because wmbench's
    `data/videos/<model>/` already aggregates every dataset's outputs under
    one folder. Some models additionally have `data/videos/<model>-humaneval/`
    for humaneval-specific runs; the HF manifest's local-source resolver
    falls back to that location.
    """
    return f"{HF_BASE}/videos/{model_key}/{stem}.mp4"


def _first_frame_hf_url(source_dataset: str, stem: str) -> str:
    """First-frame images on the HF dataset live under a flat `first_images/`
    folder (no per-source-dataset subdirs). `source_dataset` is kept in the
    signature for caller compatibility but is unused.
    """
    return f"{HF_BASE}/first_images/{stem}.jpg"


def _has_first_frame(source_dataset: str, stem: str) -> bool:
    if not source_dataset or not stem:
        return False
    p = WMBENCH_SRC / "data" / "prompts" / source_dataset / "first_frames" / f"{stem}.jpg"
    return p.is_file()


def _video_exists_locally(model_key: str, stem: str) -> bool:
    """True iff a (model, stem) pair has an HF-published video.

    Two gates: the model must be in `_HF_PUBLISHED_MODELS` (the 8 model dirs
    actually present on the HF dataset), and the local source must exist
    under `_wmbench_src/data/videos/<model>/<stem>.mp4` (with a fallback to
    `<model>-humaneval/<stem>.mp4`). Manifest-only fallback models like
    `cogvideox1.5-5b-i2v`, `hunyuanvideo-i2v`, `ltx-2-19b-distilled-fp8` are
    pruned even when their bytes are on disk locally — the HF dataset has no
    folder for them, so emitting their URLs would 404.
    """
    if not model_key or not stem:
        return False
    if model_key not in _HF_PUBLISHED_MODELS:
        return False
    primary = WMBENCH_SRC / "data" / "videos" / model_key / f"{stem}.mp4"
    if primary.is_file():
        return True
    alt = WMBENCH_SRC / "data" / "videos" / f"{model_key}-humaneval" / f"{stem}.mp4"
    return alt.is_file()


# ---------- videos_index (now per-(model, prompt) with HF URLs) ----------

def _videos_index(leaderboard: list[dict],
                  paperdemo: list[dict],
                  prompts: list[dict],
                  models: list[dict],
                  prompt_scores: dict[str, dict[str, float]] | None = None) -> dict[str, dict]:
    """Per-model browse index keyed by every model in `models[]`.

    `humaneval` is sourced from `prompt_scores` (the score-JSON table), not
    the prompt manifest's sparse `per_model_scores`, so every published
    humaneval model gets its full ~250-prompt coverage.

    Output shape per model_key:
      {paperdemo, humaneval, datasets} — each a (possibly empty) list.
    """
    prompt_scores = prompt_scores or {}
    prompt_meta = {p.get("video"): p for p in prompts if p.get("video")}

    idx: dict[str, dict] = {
        m["key"]: {"paperdemo": [], "humaneval": [], "datasets": []}
        for m in models if m.get("key")
    }
    def _slot(model_key: str) -> dict:
        if model_key not in idx:
            idx[model_key] = {"paperdemo": [], "humaneval": [], "datasets": []}
        return idx[model_key]

    # paperdemo entries.
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            _slot(v["model"])["paperdemo"].append({
                "law": law_entry["law"],
                "src_filename": v["src_filename"],
                "video_url_hf": v["video_url_hf"],
                "n_ann": v["n_ann"],
            })

    # humaneval entries: one per (model, prompt) from the score-JSON table.
    # Skip pairs whose source video is not present locally so the rendered
    # site never embeds an HF URL that will 404 after upload. (The leaderboard
    # / model-detail score tables still surface the score elsewhere.)
    seen: set[tuple[str, str]] = set()
    for pid, models_to_scores in prompt_scores.items():
        meta = prompt_meta.get(pid) or {}
        ds = meta.get("dataset") or ""
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        for model_key, score in models_to_scores.items():
            seen.add((model_key, pid))
            if not _video_exists_locally(model_key, pid):
                continue
            _slot(model_key)["humaneval"].append({
                "prompt_id": pid,
                "dataset": ds,
                "prompt": meta.get("prompt") or "",
                "physical_laws": meta.get("physical_laws") or [],
                "score": score,
                "video_url_hf": _video_hf_url(model_key, ds, pid) if ds else f"{HF_BASE}/videos/{model_key}/{pid}.mp4",
                "first_frame_url": ff_url,
            })
    # Manifest-only prompts not covered by the score JSONs (rare, but cheap).
    for p in prompts:
        pid = p.get("video")
        if not pid:
            continue
        ds = p.get("dataset") or ""
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        for model_key, score in (p.get("per_model_scores") or {}).items():
            if (model_key, pid) in seen:
                continue
            seen.add((model_key, pid))
            if not _video_exists_locally(model_key, pid):
                continue
            _slot(model_key)["humaneval"].append({
                "prompt_id": pid,
                "dataset": ds,
                "prompt": p.get("prompt") or "",
                "physical_laws": p.get("physical_laws") or [],
                "score": score,
                "video_url_hf": _video_hf_url(model_key, ds, pid),
                "first_frame_url": ff_url,
            })

    # Leaderboard slices.
    for entry in leaderboard:
        _slot(entry["video_model"])["datasets"].append({
            "dataset": entry["dataset"],
            "subset": entry["subset"],
            "evaluator": entry["evaluator"],
            "schema": entry["schema"],
            "phys_avg": entry["current"].get("phys_avg"),
            "gen_avg": entry["current"].get("gen_avg"),
            "n": entry["current"].get("n"),
            "source_url_snapshot": entry["current"].get("source_url_snapshot"),
        })

    for k, sub in idx.items():
        sub["paperdemo"].sort(key=lambda v: (v["law"], v["src_filename"]))
        sub["humaneval"].sort(key=lambda v: (v["dataset"], v["prompt_id"]))
        sub["datasets"].sort(key=lambda v: (v["dataset"], v["subset"], v["evaluator"], v["schema"]))
    return {k: idx[k] for k in sorted(idx.keys())}


# ---------- representative videos for /models/<key>/ ----------

def _representative_videos(model_key: str,
                           paperdemo: list[dict],
                           prompts: list[dict],
                           prompt_scores: dict[str, dict[str, float]] | None = None,
                           target: int = 9) -> list[dict]:
    """Per plan §3: 6-9 representative videos for a model. Paperdemo first;
    deterministic-random fallback over humaneval prompts the model scored.

    The fallback pool is built from `prompt_scores` (the in-snapshot score-JSON
    table) so every published humaneval model — not just the 4-model subset
    the prompt manifest's `per_model_scores` covers — gets a fallback set.
    `prompts` is still consulted for prompt metadata (dataset, physical_laws).
    """
    import random as _random
    prompt_scores = prompt_scores or {}
    out: list[dict] = []
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            if v["model"] == model_key:
                out.append({
                    "law": law_entry["law"],
                    "src_filename": v["src_filename"],
                    "video_url_hf": v["video_url_hf"],
                    "first_frame_url": None,
                    "kind": "paperdemo",
                    "n_ann": v["n_ann"],
                    "score": None,
                    "prompt_id": None,
                    "dataset": None,
                })
                if len(out) >= target:
                    return out
    if len(out) >= target:
        return out

    prompt_meta = {p.get("video"): p for p in prompts if p.get("video")}
    fallback_pool: list[dict] = []
    seen: set[str] = set()
    for pid, models_to_scores in prompt_scores.items():
        if model_key not in models_to_scores:
            continue
        if pid in seen:
            continue
        if not _video_exists_locally(model_key, pid):
            continue
        seen.add(pid)
        meta = prompt_meta.get(pid) or {}
        ds = meta.get("dataset") or ""
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        fallback_pool.append({
            "law": ((meta.get("physical_laws") or [None]) or [None])[0],
            "src_filename": f"{pid}.mp4",
            "video_url_hf": _video_hf_url(model_key, ds, pid) if ds else f"{HF_BASE}/videos/{model_key}/{pid}.mp4",
            "first_frame_url": ff_url,
            "kind": "humaneval",
            "score": models_to_scores[model_key],
            "n_ann": None,
            "prompt_id": pid,
            "dataset": ds,
        })
    # Manifest-only prompts not covered by score JSONs: include if model_key
    # appears there (rare but cheap to keep).
    for p in prompts:
        pid = p.get("video")
        if not pid or pid in seen:
            continue
        scores = p.get("per_model_scores") or {}
        if model_key not in scores:
            continue
        if not _video_exists_locally(model_key, pid):
            continue
        seen.add(pid)
        ds = p.get("dataset") or ""
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        fallback_pool.append({
            "law": (p.get("physical_laws") or [None])[0],
            "src_filename": f"{pid}.mp4",
            "video_url_hf": _video_hf_url(model_key, ds, pid),
            "first_frame_url": ff_url,
            "kind": "humaneval",
            "score": scores[model_key],
            "n_ann": None,
            "prompt_id": pid,
            "dataset": ds,
        })

    fallback_pool.sort(key=lambda v: (v["prompt_id"] or "", v["dataset"] or ""))
    rng = _random.Random(f"phyground:{model_key}")
    rng.shuffle(fallback_pool)
    out.extend(fallback_pool[: max(0, target - len(out))])
    return out


def _model_leaderboard_cards(model_key: str, leaderboard: list[dict]) -> list[dict]:
    """Slice cards for the by-model gallery when a model has no paperdemo and
    no per_model_scores entry (e.g. baseline_i2v_* tournament rows).
    """
    out = []
    for entry in leaderboard:
        if entry["video_model"] != model_key:
            continue
        c = entry["current"]
        out.append({
            "dataset": entry["dataset"],
            "subset": entry["subset"],
            "evaluator": entry["evaluator"],
            "schema": entry["schema"],
            "phys_avg": c.get("phys_avg"),
            "gen_avg": c.get("gen_avg"),
            "n": c.get("n"),
            "datetime": c.get("datetime"),
            "source_url_snapshot": c.get("source_url_snapshot"),
        })
    out.sort(key=lambda r: (r["dataset"], r["subset"], r["evaluator"], r["schema"]))
    return out


# ---------- site_config builder ----------

def _site_config(catalog: list[dict],
                 registry: list[dict],
                 paperdemo_grouped: list[dict],
                 vis_datasets: dict,
                 humaneval_prompts: list[dict],
                 humaneval_100: dict,
                 leaderboard_entries: list[dict],
                 leaderboard_unpublished: list[dict],
                 prompt_scores: dict[str, dict[str, float]],
                 build_meta: dict) -> dict:
    # Per the user's "先减量进仓库" guidance: every HF video URL the site
    # embeds must correspond to a video in the humaneval-100 published set
    # (or paperdemo). Restrict prompt-derived HF URL generation to those 100
    # prompt_ids so the upload manifest stays at ~100 prompts × ≤8 models +
    # paperdemo + first_frames ≈ 900 files instead of 250 × ≤8 ≈ 2200.
    selected_pids = {p["prompt_id"] for p in humaneval_100.get("prompts", [])}
    if selected_pids:
        prompt_scores_published = {
            pid: ms for pid, ms in prompt_scores.items() if pid in selected_pids
        }
        humaneval_prompts_published = [
            p for p in humaneval_prompts if p.get("video") in selected_pids
        ]
    else:
        # Stub case (no selection committed yet): fall back to the full set so
        # the site still renders something during early-round development.
        prompt_scores_published = prompt_scores
        humaneval_prompts_published = humaneval_prompts

    models = _all_known_models(catalog, registry, [
        {"model": v["model"]} for law in paperdemo_grouped for v in law["videos"]
    ])
    datasets = _datasets_summary(vis_datasets)
    videos_index = _videos_index(
        leaderboard_entries, paperdemo_grouped,
        humaneval_prompts_published, models, prompt_scores_published,
    )
    prompts_index = _prompts_index(humaneval_prompts_published, prompt_scores_published)

    for m in models:
        m["representative_videos"] = _representative_videos(
            m["key"], paperdemo_grouped,
            humaneval_prompts_published, prompt_scores_published,
        )
        # Always provide leaderboard-slice cards as a final fallback for the
        # by-model gallery, even when representative_videos is non-empty (the
        # detail page still shows them in a separate section).
        m["leaderboard_slices"] = _model_leaderboard_cards(m["key"], leaderboard_entries)

    # Round 15 (user direction: "没有视频的 这几个不要在 website 显示."): drop
    # models with zero rendered evidence from the published model set so
    # /models/<key>/ pages, the by-model gallery, and the model picker only
    # surface models that actually have something to show. The upstream
    # MODEL_CATALOG.py is unchanged; future evidence un-hides these keys via
    # this same filter.
    #
    # Round 16 (Codex review): leaderboard_entries are keyed by `video_model`
    # (from `_dedup_leaderboard`), not `model_key`; using the wrong field in
    # Round 15 silently dropped all 51 published rows and zeroed the home
    # `n_eval_combos`. The per_model_scores filter below also closes the
    # compare-page leak the Round 15 audit missed — `static/js/compare.js`
    # renders one card per `per_model_scores` key, so any hidden model key
    # left in the prompts_index would still surface on `/videos/compare/`.
    #
    # Round 17 (Codex Round-16 review): the "zero evidence" framing is
    # technically coupled to `_HF_PUBLISHED_MODELS` upstream — that allowlist
    # gates HF URL emission, which makes representative_videos empty for the
    # 3 partial-coverage MODEL_CATALOG keys that *do* have humaneval-100 MP4s
    # on disk (cogvideox1.5-5b-i2v: 24/100; hunyuanvideo-i2v: 33/100;
    # ltx-2-19b-distilled-fp8: 16/100). The 4 baseline_i2v_* keys come from
    # eval_registry rows (not MODEL_CATALOG.py); the 5th truly evidence-less
    # key is cogvideox-5b-i2v (MODEL_CATALOG, 0/100 humaneval-100 MP4s).
    #
    # Round 18 (user direction, AskUserQuestion with explicit
    # catalog-vs-baseline framing): "不足100的不要显示. 都不管了. 网站
    # 根本不考虑这些模型." ("Don't show models without 100/100 coverage.
    # Don't bother with them. The website doesn't consider these models at
    # all.") This is a definitive user-validated reduction: only models with
    # complete humaneval-100 coverage (currently the 8 keys in
    # `_HF_PUBLISHED_MODELS`) are part of the website. The 4 omitted catalog
    # routes (cogvideox-5b-i2v, cogvideox1.5-5b-i2v, hunyuanvideo-i2v,
    # ltx-2-19b-distilled-fp8) and the 4 eval_registry baselines are *not*
    # rendered, regardless of any partial MP4 evidence. AC5 stays Active in
    # the loop framework (immutable text isn't satisfied by reduction); see
    # the goal-tracker row `not-a-closure (user-validated reduction)`.
    models = [m for m in models if m["representative_videos"] or m["leaderboard_slices"]]
    rendered_model_keys = {m["key"] for m in models}
    videos_index = {k: v for k, v in videos_index.items() if k in rendered_model_keys}
    leaderboard_entries = [
        e for e in leaderboard_entries if e.get("video_model") in rendered_model_keys
    ]
    for _p in prompts_index.values():
        pms = _p.get("per_model_scores")
        if pms:
            _p["per_model_scores"] = {
                k: v for k, v in pms.items() if k in rendered_model_keys
            }

    n_models = len(models)
    n_eval_combos = len(leaderboard_entries)
    n_annotations = sum(int(v["n_ann"]) for law in paperdemo_grouped for v in law["videos"])
    n_prompts = humaneval_100.get("n_selected") or 0
    if n_prompts == 0:
        n_prompts = len(humaneval_prompts)

    # Featured comparison: pick one prompt covered by all rendered models and
    # surface one tile per model. Preference order: `collision_156` if it has
    # full coverage (it's the paperdemo-anchored Round-1 reference), otherwise
    # the fully-covered prompt with the largest score spread (most informative
    # apples-to-apples comparison), tie-broken by prompt_id for determinism.
    def _build_featured_same_prompt() -> dict:
        rendered_keys = {m["key"] for m in models}

        def is_fully_covered(p: dict) -> bool:
            return rendered_keys.issubset(set((p.get("per_model_videos") or {}).keys()))

        candidates = [(pid, p) for pid, p in prompts_index.items() if is_fully_covered(p)]
        if not candidates:
            return {"law": None, "prompt_id": None, "prompt": "", "physical_laws": [], "videos": []}

        preferred_pid = "collision_156"
        chosen = next(((pid, p) for pid, p in candidates if pid == preferred_pid), None)
        if chosen is None:
            def spread(p: dict) -> float:
                scores = list((p.get("per_model_scores") or {}).values())
                return (max(scores) - min(scores)) if scores else 0.0
            chosen = sorted(candidates, key=lambda kv: (-spread(kv[1]), kv[0]))[0]

        pid, p = chosen
        pms = p.get("per_model_videos") or {}
        score_map = p.get("per_model_scores") or {}
        ds = p.get("dataset") or ""
        videos: list[dict] = []
        for m in models:
            mk = m["key"]
            url = pms.get(mk)
            if not url:
                continue
            videos.append({
                "model": mk,
                "video_url_hf": url,
                "src_filename": f"{pid}.mp4",
                "src_path": f"data/videos/{mk}/{pid}.mp4",
                "_role": "model_output",
                "_humaneval_prompt_id": pid,
                "_humaneval_score": score_map.get(mk),
                "_humaneval_dataset": ds,
            })
        laws = list(p.get("physical_laws") or [])
        return {
            "law": laws[0] if laws else None,
            "prompt_id": pid,
            "prompt": p.get("prompt", ""),
            "physical_laws": laws,
            "first_frame_url": p.get("first_frame_url"),
            "videos": videos,
        }

    featured_comparison_payload = _build_featured_same_prompt()
    featured_law_name = featured_comparison_payload.get("law")
    featured_videos = featured_comparison_payload.get("videos") or []

    huggingface_dataset_url = HF_BASE.replace("/resolve/main", "")
    # Per Round-5 user choice "Hide until paper is posted": no fallback to the
    # HF dataset card. The hero button + Paper Browse card are suppressed when
    # the env override is empty; setting PHYGROUND_PAPER_URL re-enables them.
    paper_url = os.environ.get("PHYGROUND_PAPER_URL", "").strip()

    return {
        "site": {
            "title": "phyground",
            "short_title": "phyground",
            "description": "A physics-grounded benchmark for video generation. Browse model outputs by physical law, compare side-by-side, and explore evaluator-by-dataset leaderboards.",
            "paper_url": paper_url,
            "github_url": "https://github.com/NU-World-Model-Embodied-AI/PhyGround",
            "huggingface_url": "https://huggingface.co/datasets/juyil/phygroundwebsitevideo",
            "huggingface_dataset_url": huggingface_dataset_url,
            "phyjudge_url": "https://huggingface.co/NU-World-Model-Embodied-AI/phyjudge-9B",
            "copyright_year": 2026,
        },
        "headline": {
            "n_models": n_models,
            "n_prompts": n_prompts,
            "n_annotations": n_annotations,
            "n_eval_combos": n_eval_combos,
        },
        "featured_comparison": featured_comparison_payload,
        "models": models,
        "datasets": datasets,
        "leaderboard_entries": leaderboard_entries,
        "paperdemo": paperdemo_grouped,
        "videos_index": videos_index,
        "prompts_index": prompts_index,
        "humaneval_100_summary": {
            "n_selected": humaneval_100.get("n_selected", 0),
            "law_quotas": humaneval_100.get("law_quotas", {}),
            "effective_law_counts": humaneval_100.get("effective_law_counts", {}),
            "selected_at": humaneval_100.get("selected_at"),
            "note": humaneval_100.get("note"),
        },
        "leaderboard_unpublished_count": len(leaderboard_unpublished),
        "build_meta": build_meta,
    }


# ---------- main build ----------

def build(*, now_iso: str | None = None,
          select_humaneval_100: bool = False,
          verbose: bool = True) -> dict:
    if not WMBENCH_SRC.is_dir():
        raise SystemExit(f"_wmbench_src/ not found at {WMBENCH_SRC}.")

    src_paths = {
        "evals/eval_registry.json": WMBENCH_SRC / "evals" / "eval_registry.json",
        "evals/eval_types.py": WMBENCH_SRC / "evals" / "eval_types.py",
        "data/vis_datasets.json": WMBENCH_SRC / "data" / "vis_datasets.json",
        "data/paperdemo/manifest.csv": WMBENCH_SRC / "data" / "paperdemo" / "manifest.csv",
        "data/humaneval_leaderboard.json": WMBENCH_SRC / "data" / "humaneval_leaderboard.json",
        "data/phyjudge_leaderboard.json": WMBENCH_SRC / "data" / "phyjudge_leaderboard.json",
        "videogen/runner/MODEL_CATALOG.py": WMBENCH_SRC / "videogen" / "runner" / "MODEL_CATALOG.py",
    }
    optional_paths = {
        "data/prompts/anonymous_humaneval_set.json":
            WMBENCH_SRC / "data" / "prompts" / "anonymous_humaneval_set.json",
    }
    missing = [k for k, p in src_paths.items() if not p.is_file()]
    if missing:
        raise SystemExit(f"missing _wmbench_src/ inputs: {missing}")

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    (STAGING_DIR / "index").mkdir(parents=True)
    (STAGING_DIR / "index" / "figs").mkdir(parents=True)
    (STAGING_DIR / "scores").mkdir(parents=True)

    # 1. Parse inputs.
    catalog = _extract_model_catalog(src_paths["videogen/runner/MODEL_CATALOG.py"])
    registry = _read_eval_registry(src_paths["evals/eval_registry.json"])
    paperdemo_rows = _read_paperdemo_manifest(src_paths["data/paperdemo/manifest.csv"])
    vis_datasets = _read_vis_datasets(src_paths["data/vis_datasets.json"])
    humaneval_prompts = _read_humaneval_prompts(optional_paths["data/prompts/anonymous_humaneval_set.json"])

    # 2. Freeze raw inputs into snapshot/index/.
    _copy_file(src_paths["evals/eval_registry.json"], STAGING_DIR / "index" / "eval_registry.frozen.json")
    _copy_file(src_paths["data/paperdemo/manifest.csv"], STAGING_DIR / "index" / "paperdemo.manifest.csv")
    _copy_file(src_paths["data/vis_datasets.json"], STAGING_DIR / "index" / "vis_datasets.frozen.json")
    _write_json(STAGING_DIR / "index" / "model_catalog.frozen.json", catalog)

    # Copy paperdemo PDFs and rasterise to PNG thumbnails (pdftocairo, system tool).
    figs_src = WMBENCH_SRC / "data" / "paperdemo" / "figs"
    if figs_src.is_dir():
        for pdf in sorted(figs_src.glob("*.pdf")):
            dst_pdf = STAGING_DIR / "index" / "figs" / pdf.name
            _copy_file(pdf, dst_pdf)
            png_target = dst_pdf.with_suffix("")  # pdftocairo appends .png
            try:
                import subprocess
                subprocess.run(
                    ["pdftocairo", "-png", "-r", "72", "-singlefile",
                     str(dst_pdf), str(png_target)],
                    check=True,
                    capture_output=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                if verbose:
                    print(f"[build_snapshot] WARN: pdftocairo failed for {pdf.name}: {e}")

    # Copy humaneval prompts JSON into snapshot/index/.
    if humaneval_prompts and optional_paths["data/prompts/anonymous_humaneval_set.json"].is_file():
        _copy_file(
            optional_paths["data/prompts/anonymous_humaneval_set.json"],
            STAGING_DIR / "index" / "humaneval_prompts.json",
        )

    # Copy first_frame JPGs for every prompt that has one. The compare/ gallery
    # pages don't actually need the bytes (they reference HF URLs) but copying
    # them lets verify_snapshot ensure provenance and gives the HF upload step a
    # clean source layout to mirror.
    n_first_frames = 0
    for p in humaneval_prompts:
        ds = p.get("dataset") or ""
        stem = p.get("video") or ""
        if not ds or not stem:
            continue
        src_ff = WMBENCH_SRC / "data" / "prompts" / ds / "first_frames" / f"{stem}.jpg"
        if src_ff.is_file():
            dst_ff = STAGING_DIR / "index" / "first_frames" / ds / f"{stem}.jpg"
            _copy_file(src_ff, dst_ff)
            n_first_frames += 1
    if verbose:
        print(f"[build_snapshot] copied {n_first_frames} first_frame JPGs")

    # 3. Group paperdemo by law (with HF URLs + fig_png).
    paperdemo_grouped = _group_paperdemo(paperdemo_rows)

    # 4. Build leaderboard entries (coverage filter, source URL rewrite, drop unresolvable currents).
    leaderboard_entries, leaderboard_unpublished = _dedup_leaderboard(registry)

    # 5. Copy referenced score JSONs into snapshot/scores/. The path mapping
    #    follows _snapshot_score_url() so URLs in site_config match.
    #    `_snapshot_score_url` returns a repo-root-relative URL prefixed with
    #    `snapshot/`; STAGING_DIR is itself the snapshot/ payload, so we strip
    #    the leading `snapshot/` before joining onto STAGING_DIR.
    referenced_paths: set[str] = set()
    for entry in leaderboard_entries:
        for row in [entry["current"], *entry["history"]]:
            rel = row.get("_score_relpath")
            if rel:
                referenced_paths.add(rel)
    for rel in sorted(referenced_paths):
        src = WMBENCH_SRC / rel
        target_url = _snapshot_score_url(rel)
        if not target_url:
            continue
        assert target_url.startswith("snapshot/"), (
            f"_snapshot_score_url returned {target_url!r}, expected 'snapshot/...'"
        )
        dst = STAGING_DIR / target_url[len("snapshot/"):]
        _copy_file(src, dst)
    # Strip the helper field from the published rows so it does not leak into site_config.
    for entry in leaderboard_entries:
        entry["current"].pop("_score_relpath", None)
        for h in entry["history"]:
            h.pop("_score_relpath", None)
    for u in leaderboard_unpublished:
        for r in u.get("rows", []):
            r.pop("_score_relpath", None)

    # Round-6: every unpublished group gets a durable `status: "retired"`
    # plus a `retired_reason` derived from where its (unrecoverable) source
    # path used to live. The retirement is a settled decision per Plan
    # Evolution Log Round-6: "13 leaderboard groups retired permanently".
    retired_at = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in leaderboard_unpublished:
        # Inspect every cov>0 row's source_json prefix to summarise why.
        prefixes = set()
        for row in entry.get("rows", []):
            sj = row.get("source_json") or ""
            if sj.startswith("/shared/") and "rlvideo" in sj:
                prefixes.add("/shared/.../rlvideo/...")
            elif sj.startswith("/"):
                prefixes.add("absolute non-wmbench path")
            elif sj.startswith("data/training/cotclaude/"):
                prefixes.add("data/training/cotclaude/...")
            elif sj.startswith("tmp/"):
                prefixes.add("tmp/...")
            elif sj.startswith("data/scores/"):
                prefixes.add("data/scores/... (file moved or never created)")
            else:
                prefixes.add("unknown")
        entry["status"] = "retired"
        entry["retired_at"] = retired_at
        entry["retired_reason"] = (
            "Every cov>0 row's source_json points at " + ", ".join(sorted(prefixes))
            + ". These paths cannot be recovered from the public repo's "
            + "_wmbench_src/ ingest. The group is retired permanently per "
            + "Plan Evolution Log Round-6."
        )
    _write_json(STAGING_DIR / "index" / "leaderboard_unpublished.json", {
        "schema_version": "1",
        "retired_at": retired_at,
        "count": len(leaderboard_unpublished),
        "policy": (
            "Every entry below is permanently retired from the published "
            "leaderboard. Re-introduction requires a new cov>0 source_json "
            "to land under _wmbench_src/data/scores/, which today does not exist."
        ),
        "entries": leaderboard_unpublished,
    })

    # 6. Run humaneval-100 selection when asked; otherwise reuse the existing
    #    committed selection (so determinism is preserved across builds).
    input_sha256 = {
        "registry_sha256": _sha256_file(src_paths["evals/eval_registry.json"]),
        "paperdemo_manifest_sha256": _sha256_file(src_paths["data/paperdemo/manifest.csv"]),
        "model_catalog_sha256": _sha256_file(src_paths["videogen/runner/MODEL_CATALOG.py"]),
    }
    prompts_sha256 = (
        _sha256_file(optional_paths["data/prompts/anonymous_humaneval_set.json"])
        if optional_paths["data/prompts/anonymous_humaneval_set.json"].is_file() else None
    )
    full_inputs = {**input_sha256, "humaneval_prompts_sha256": prompts_sha256}

    existing_selection_path = SNAPSHOT_DIR / "index" / "humaneval_100.json"
    existing_selection: dict | None = None
    if existing_selection_path.is_file():
        try:
            existing_selection = json.loads(existing_selection_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_selection = None

    if select_humaneval_100 and humaneval_prompts:
        humaneval_100 = _select_humaneval_100(
            humaneval_prompts,
            paperdemo_grouped,
            registry,
            existing_selection,
            full_inputs,
            now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    elif existing_selection and existing_selection.get("prompts"):
        # Preserve a previous real selection across rebuilds.
        humaneval_100 = existing_selection
        # Re-stamp inputs in case the underlying files changed.
        humaneval_100["selection_inputs"] = full_inputs
    else:
        humaneval_100 = _humaneval_100_stub(input_sha256, prompts_sha256)

    _write_json(STAGING_DIR / "index" / "humaneval_100.json", humaneval_100)

    # 7. site_config.json with all collections wired in.
    build_meta = {
        "built_at": now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_sha256": input_sha256["registry_sha256"],
        "paperdemo_manifest_sha256": input_sha256["paperdemo_manifest_sha256"],
        "model_catalog_sha256": input_sha256["model_catalog_sha256"],
        "vis_datasets_sha256": _sha256_file(src_paths["data/vis_datasets.json"]),
        "humaneval_prompts_sha256": prompts_sha256,
        "snapshot_sha": None,                 # filled in below
    }
    # Source the prompt → {model: phys_score} table from the same in-snapshot
    # score JSONs the humaneval-100 selector uses, so the compare page and
    # per-model representative videos see every published humaneval model
    # (8 of them) — not just the 4-model subset the prompt manifest covers.
    prompt_scores, _prompt_laws_unused, _score_files_unused = _build_humaneval_score_table(registry)
    site_config = _site_config(
        catalog, registry, paperdemo_grouped, vis_datasets,
        humaneval_prompts, humaneval_100, leaderboard_entries,
        leaderboard_unpublished, prompt_scores, build_meta,
    )
    _write_json(STAGING_DIR / "index" / "site_config.json", site_config)

    # 7b. humaneval_leaderboard.json — copied from the git-tracked input INTO
    #     staging so the atomic swap at step 10 doesn't wipe it. The JSON is
    #     regenerated from the human-eval DB by
    #     `tools/export_humaneval_leaderboard.py` as an out-of-band step and
    #     committed to git; the build itself never touches the DB.
    _copy_file(
        src_paths["data/humaneval_leaderboard.json"],
        STAGING_DIR / "index" / "humaneval_leaderboard.json",
    )
    _copy_file(
        src_paths["data/phyjudge_leaderboard.json"],
        STAGING_DIR / "index" / "phyjudge_leaderboard.json",
    )

    # 8. MANIFEST.json — single-pass: compute snapshot_sha, patch site_config,
    #    then write manifest. Do this in two steps so site_config.snapshot_sha
    #    matches the manifest the manifest.json will record.
    manifest_files: dict[str, str] = {}
    for root, _dirs, files in os.walk(STAGING_DIR):
        for fname in files:
            p = Path(root) / fname
            rel = str(p.relative_to(STAGING_DIR)).replace(os.sep, "/")
            if rel == "MANIFEST.json":
                continue
            manifest_files[rel] = _sha256_file(p)

    # snapshot_sha = sha256 of the canonical text of the manifest's `files` map
    # (deterministic key order, no built_at — those bake in volatile fields).
    canonical_files_text = json.dumps(
        dict(sorted(manifest_files.items())), indent=2, sort_keys=True, ensure_ascii=False,
    )
    snapshot_sha = _sha256_bytes(canonical_files_text.encode("utf-8"))

    # Patch site_config.build_meta.snapshot_sha and rewrite, then refresh that
    # one entry in manifest_files so MANIFEST.json's hash for site_config.json
    # matches the rewritten file.
    site_config["build_meta"]["snapshot_sha"] = snapshot_sha
    _write_json(STAGING_DIR / "index" / "site_config.json", site_config)
    manifest_files["index/site_config.json"] = _sha256_file(STAGING_DIR / "index" / "site_config.json")

    manifest_obj = {
        "schema_version": "1",
        "built_at": build_meta["built_at"],
        "snapshot_sha": snapshot_sha,
        "files": dict(sorted(manifest_files.items())),
    }
    (STAGING_DIR / "MANIFEST.json").write_text(
        json.dumps(manifest_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 9. HF upload manifest. Generated inside the same build so the file is
    #    consistent with the snapshot it describes and lands in the same git
    #    commit. Computed before the atomic swap because the manifest reads
    #    `_wmbench_src/` (not the staged snapshot).
    try:
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        from build_hf_upload_manifest import build_manifest as _hf_build_manifest
        hf_manifest = _hf_build_manifest(site_config)
        (STAGING_DIR / "HF_UPLOAD_MANIFEST.json").write_text(
            json.dumps(hf_manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Refresh manifest_files entry for HF_UPLOAD_MANIFEST.json.
        manifest_files["HF_UPLOAD_MANIFEST.json"] = _sha256_file(STAGING_DIR / "HF_UPLOAD_MANIFEST.json")
        manifest_obj["files"] = dict(sorted(manifest_files.items()))
        # Recompute snapshot_sha to include the new file.
        canonical_files_text = json.dumps(
            manifest_obj["files"], indent=2, sort_keys=True, ensure_ascii=False,
        )
        snapshot_sha = _sha256_bytes(canonical_files_text.encode("utf-8"))
        manifest_obj["snapshot_sha"] = snapshot_sha
        site_config["build_meta"]["snapshot_sha"] = snapshot_sha
        _write_json(STAGING_DIR / "index" / "site_config.json", site_config)
        manifest_files["index/site_config.json"] = _sha256_file(STAGING_DIR / "index" / "site_config.json")
        manifest_obj["files"] = dict(sorted(manifest_files.items()))
        (STAGING_DIR / "MANIFEST.json").write_text(
            json.dumps(manifest_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    finally:
        if str(REPO_ROOT / "tools") in sys.path:
            sys.path.remove(str(REPO_ROOT / "tools"))

    # 10. Atomic swap.
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    os.rename(STAGING_DIR, SNAPSHOT_DIR)

    if verbose:
        n_files = len(manifest_files)
        print(f"[build_snapshot] wrote {n_files} files to snapshot/")
        print(f"[build_snapshot] snapshot_sha = {snapshot_sha}")
        print(f"[build_snapshot] headline = {site_config['headline']}")
        print(f"[build_snapshot] humaneval_100.n_selected = {humaneval_100.get('n_selected')}")
    return manifest_obj


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build snapshot/ from _wmbench_src/.")
    parser.add_argument("--now", default=None,
                        help="Override the built_at ISO timestamp (for deterministic tests).")
    parser.add_argument("--select-humaneval-100", action="store_true",
                        help="Re-run the deterministic humaneval-100 selection (humaneval_100.md).")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    build(now_iso=args.now,
          select_humaneval_100=args.select_humaneval_100,
          verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
