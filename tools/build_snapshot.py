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

HF_BASE = "https://huggingface.co/datasets/juyil/wmbench-public/resolve/main"


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
    """Map a `_wmbench_src/`-relative score path to its snapshot-relative URL.

    `data/scores/<...>`     → `scores/<...>`
    `data/training/<...>`   → `scores/_training/<...>`
    `tmp/<...>`             → `scores/_tmp/<...>`
    Anything else is a programming bug.
    """
    if rel_under_wmbench.startswith("data/scores/"):
        return rel_under_wmbench[len("data/"):]
    if rel_under_wmbench.startswith("data/training/"):
        return "scores/_training/" + rel_under_wmbench[len("data/training/"):]
    if rel_under_wmbench.startswith("tmp/"):
        return "scores/_tmp/" + rel_under_wmbench[len("tmp/"):]
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
        rel_under_wmbench = r["src_path"].lstrip("/")
        if rel_under_wmbench.startswith("data/paperdemo/"):
            hf_rel = rel_under_wmbench[len("data/"):]
        else:
            hf_rel = rel_under_wmbench
        by_law[r["law"]].append({
            "model": r["model"],
            "video_id": r["video_id"],
            "n_ann": r["n_ann"],
            "src_filename": r["src_filename"],
            "src_path": r["src_path"],
            "video_url_hf": f"{HF_BASE}/{hf_rel}",
        })
    out = []
    for law in sorted(by_law.keys()):
        videos = sorted(by_law[law], key=lambda v: (v["model"], str(v["video_id"])))
        out.append({
            "law": law,
            "fig_pdf": f"index/figs/{law}.pdf",
            "fig_png": f"index/figs/{law}.png",
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


def _select_humaneval_100(prompts: list[dict],
                          paperdemo: list[dict],
                          registry: list[dict],
                          existing_selection: dict | None,
                          input_sha256: dict,
                          built_at: str) -> dict:
    """Implements docs/exp-plan/public/humaneval_100.md §"Selection algorithm" verbatim:

      Step 1. Intersection gate: prompt's per_model_scores must cover every
              leaderboard model that has a humaneval_set cov=1.0 entry.
      Step 2. paperdemo seed: prompts whose `video` matches a paperdemo
              `src_filename` stem are must-includes.
      Step 3. Per-law quota: floor(100/13) = 7 slots, plus 9 spare slots
              distributed by descending paperdemo n_ann (alphabetical tie-break).
      Step 4. Score-based fill, per-law min-max normalized:
              score = 0.40·variance + 0.30·coverage + 0.30·mid_difficulty.
              Tie-break on lower numeric prompt_id.
      Step 5. Manual overrides preserved across rebuilds.
    If the data after the gate cannot fill 100 slots, the artifact's `note`
    documents the cap precisely.
    """
    law_n_ann = _law_n_ann(paperdemo)
    quotas = _law_quotas(law_n_ann)

    full_model_set = _humaneval_full_model_set(registry)

    # ── Step 1: intersection gate ──────────────────────────────
    gate_kept: list[dict] = []
    gate_dropped_no_models = 0
    gate_dropped_partial = 0
    for p in prompts:
        per = p.get("per_model_scores") or {}
        if not per:
            gate_dropped_no_models += 1
            continue
        if full_model_set and not full_model_set.issubset(per.keys()):
            gate_dropped_partial += 1
            continue
        gate_kept.append(p)

    # If the strict gate empties every law (it does on the current humaneval set
    # because per_model_scores carries 4 of the 8 leaderboard models), fall back
    # to a relaxed gate: prompts whose per_model_scores is a non-empty subset of
    # `full_model_set` (or non-empty if the registry has no eligible models).
    relaxed_gate = False
    if not gate_kept:
        relaxed_gate = True
        for p in prompts:
            per = p.get("per_model_scores") or {}
            if not per:
                continue
            if full_model_set and not (set(per.keys()) & full_model_set):
                continue
            gate_kept.append(p)

    # Group by primary law.
    by_law: dict[str, list[dict]] = defaultdict(list)
    for p in gate_kept:
        laws = p.get("physical_laws") or []
        if not laws:
            continue
        primary = laws[0]
        if primary in quotas:
            by_law[primary].append(p)

    # ── Step 2: paperdemo seed ────────────────────────────────
    paperdemo_stems = {
        Path(v["src_filename"]).stem
        for law_entry in paperdemo
        for v in law_entry["videos"]
    }
    seed_ids: set[str] = {
        p["video"] for ps in by_law.values() for p in ps
        if p.get("video") in paperdemo_stems
    }

    manual_overrides = (existing_selection or {}).get("manual_overrides", []) or []

    selected: list[dict] = []
    per_law_audit: dict[str, dict] = {}

    # ── Step 4: per-law min-max normalized composite ──────────
    for law, prompts_in_law in by_law.items():
        if not prompts_in_law:
            per_law_audit[law] = {"available": 0, "quota": quotas.get(law, 0), "seeds": 0, "fill": 0}
            continue
        # Compute raw components per prompt.
        raw = []
        for p in prompts_in_law:
            scores = list((p.get("per_model_scores") or {}).values())
            var = statistics.pvariance(scores) if len(scores) >= 2 else 0.0
            cov = len(scores) / max(len(full_model_set or [1]), 1)
            diff = (p.get("difficulty") or {})
            mid = 1.0 - abs((diff.get("phys_micro_avg") or 0.0) - 3.0) / 3.0
            raw.append((p, var, cov, mid))
        # Min-max normalize per law for variance, coverage, mid_difficulty.
        var_n = _minmax_normalize([t[1] for t in raw])
        cov_n = _minmax_normalize([t[2] for t in raw])
        mid_n = _minmax_normalize([max(0.0, min(t[3], 1.0)) for t in raw])

        def _pid(s: str) -> tuple:
            """Sort key for deterministic tie-break: lower numeric component first."""
            try:
                head, _, tail = (s or "").rpartition("_")
                return (head or "", int(tail))
            except ValueError:
                return (s or "", 0)

        scored = []
        for (p, _, _, _), v, c, m in zip(raw, var_n, cov_n, mid_n):
            composite = 0.40 * v + 0.30 * c + 0.30 * m
            scored.append((p, composite, v, c, m))

        seeds_in_law = [s for s in scored if s[0].get("video") in seed_ids]
        non_seeds = [s for s in scored if s[0].get("video") not in seed_ids]
        non_seeds_ranked = sorted(non_seeds, key=lambda t: (-t[1], _pid(t[0].get("video") or "")))

        quota = quotas.get(law, 0)
        seeds_used = seeds_in_law[:quota]
        fill_n = max(0, quota - len(seeds_used))
        fill = non_seeds_ranked[:fill_n]

        for (p, _, v, c, m) in seeds_used:
            selected.append({
                "prompt_id": p["video"],
                "law": law,
                "source": "paperdemo_seed",
                "score_components": {
                    "variance_norm": round(v, 4),
                    "coverage_norm": round(c, 4),
                    "mid_difficulty_norm": round(m, 4),
                },
            })
        for (p, _, v, c, m) in fill:
            selected.append({
                "prompt_id": p["video"],
                "law": law,
                "source": "score_fill",
                "score_components": {
                    "variance_norm": round(v, 4),
                    "coverage_norm": round(c, 4),
                    "mid_difficulty_norm": round(m, 4),
                },
            })
        per_law_audit[law] = {
            "available": len(prompts_in_law),
            "quota": quota,
            "seeds": len(seeds_used),
            "fill": len(fill),
        }

    # Step 5: apply manual overrides.
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

    # Sort deterministically by (law, prompt_id) for stable output.
    selected.sort(key=lambda s: (s["law"], str(s["prompt_id"])))

    effective_counts = defaultdict(int)
    for s in selected:
        effective_counts[s["law"]] += 1

    note = None
    if relaxed_gate:
        note = (
            f"Relaxed intersection gate: the strict gate (every leaderboard humaneval "
            f"cov=1.0 model present in `per_model_scores`) emptied every law because "
            f"the shipped per_model_scores covers a strict subset of the {len(full_model_set)} "
            f"eligible models. The relaxed gate kept prompts whose per_model_scores intersects "
            f"that set. {gate_dropped_no_models} prompts were dropped for empty per_model_scores; "
            f"strict-gate-rejected count: {gate_dropped_partial}."
        )
    if len(selected) < 100:
        cap = ", ".join(f"{law}={info['available']}" for law, info in sorted(per_law_audit.items())
                        if info["available"] < info["quota"])
        cap_note = (
            f"Selected {len(selected)} prompts. The cap is below 100 because some laws "
            f"have fewer eligible prompts than their quota: {cap or '(no per-law shortfall)'}."
        )
        note = (note + " " if note else "") + cap_note

    return {
        "schema_version": "1",
        "selected_at": built_at,
        "selection_inputs": input_sha256,
        "law_quotas": quotas,
        "law_n_ann": law_n_ann,
        "intersection_gate_full_model_set": sorted(full_model_set),
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

def _prompts_index(prompts: list[dict]) -> dict:
    """Per-prompt index keyed by prompt_id. Carries first_frame_url and the per-model
    HF video URLs the compare page needs to render side-by-side without further
    lookups.
    """
    out: dict[str, dict] = {}
    for p in prompts:
        pid = p.get("video")
        ds = p.get("dataset") or ""
        if not pid:
            continue
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        per_model_videos = {
            model_key: _video_hf_url(model_key, ds, pid)
            for model_key in (p.get("per_model_scores") or {})
        }
        out[pid] = {
            "prompt_id": pid,
            "dataset": ds,
            "prompt": p.get("prompt"),
            "physical_laws": p.get("physical_laws") or [],
            "difficulty": p.get("difficulty") or {},
            "per_model_scores": p.get("per_model_scores") or {},
            "per_model_videos": per_model_videos,
            "first_frame_url": ff_url,
        }
    return out


# ---------- per-prompt + per-model HuggingFace URL helpers ----------

def _video_hf_url(model_key: str, source_dataset: str, stem: str) -> str:
    """Per plan §2: data/videos/<model>-<dataset>/<stem>.mp4. The HF dataset
    mirrors that layout under `videos/<model>-<dataset>/<stem>.mp4`.
    """
    return f"{HF_BASE}/videos/{model_key}-{source_dataset}/{stem}.mp4"


def _first_frame_hf_url(source_dataset: str, stem: str) -> str:
    """First-frame images mirror data/prompts/<dataset>/first_frames/<stem>.jpg."""
    return f"{HF_BASE}/prompts/{source_dataset}/first_frames/{stem}.jpg"


def _has_first_frame(source_dataset: str, stem: str) -> bool:
    if not source_dataset or not stem:
        return False
    p = WMBENCH_SRC / "data" / "prompts" / source_dataset / "first_frames" / f"{stem}.jpg"
    return p.is_file()


# ---------- videos_index (now per-(model, prompt) with HF URLs) ----------

def _videos_index(leaderboard: list[dict],
                  paperdemo: list[dict],
                  prompts: list[dict]) -> dict[str, dict]:
    """Build a structured by-model index.

    Output shape:
      videos_index[<model_key>] = {
          "paperdemo": [ {law, src_filename, video_url_hf, n_ann}, ... ],
          "humaneval": [ {prompt_id, dataset, prompt, video_url_hf,
                          first_frame_url|null, physical_laws, score}, ... ],
          "datasets":  [ {dataset, subset, evaluator, schema, phys_avg,
                          gen_avg, n} (one per leaderboard slice) ],
      }
    """
    idx: dict[str, dict] = defaultdict(lambda: {"paperdemo": [], "humaneval": [], "datasets": []})

    # paperdemo entries: HF URL is locked to paperdemo/<law>/<file>
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            idx[v["model"]]["paperdemo"].append({
                "law": law_entry["law"],
                "src_filename": v["src_filename"],
                "video_url_hf": v["video_url_hf"],
                "n_ann": v["n_ann"],
            })

    # humaneval-prompt × per_model_scores → one entry per (model, prompt)
    for p in prompts:
        pid = p.get("video")
        ds = p.get("dataset") or ""
        if not pid:
            continue
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        for model_key, score in (p.get("per_model_scores") or {}).items():
            idx[model_key]["humaneval"].append({
                "prompt_id": pid,
                "dataset": ds,
                "prompt": p.get("prompt") or "",
                "physical_laws": p.get("physical_laws") or [],
                "score": score,
                "video_url_hf": _video_hf_url(model_key, ds, pid),
                "first_frame_url": ff_url,
            })

    # leaderboard slices: one summary entry per (dataset, subset, evaluator, schema)
    for entry in leaderboard:
        idx[entry["video_model"]]["datasets"].append({
            "dataset": entry["dataset"],
            "subset": entry["subset"],
            "evaluator": entry["evaluator"],
            "schema": entry["schema"],
            "phys_avg": entry["current"].get("phys_avg"),
            "gen_avg": entry["current"].get("gen_avg"),
            "n": entry["current"].get("n"),
            "source_url_snapshot": entry["current"].get("source_url_snapshot"),
        })

    # Sort each list deterministically.
    for k, sub in idx.items():
        sub["paperdemo"].sort(key=lambda v: (v["law"], v["src_filename"]))
        sub["humaneval"].sort(key=lambda v: (v["prompt_id"], v["dataset"]))
        sub["datasets"].sort(key=lambda v: (v["dataset"], v["subset"], v["evaluator"], v["schema"]))
    return {k: idx[k] for k in sorted(idx.keys())}


# ---------- representative videos for /models/<key>/ ----------

def _representative_videos(model_key: str,
                           paperdemo: list[dict],
                           prompts: list[dict],
                           target: int = 9) -> list[dict]:
    """Paperdemo first; deterministic fallback over humaneval prompts the model scored."""
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
    fallback: list[dict] = []
    for p in prompts:
        pid = p.get("video")
        ds = p.get("dataset") or ""
        if not pid:
            continue
        scores = p.get("per_model_scores") or {}
        if model_key not in scores:
            continue
        ff_url = _first_frame_hf_url(ds, pid) if _has_first_frame(ds, pid) else None
        fallback.append({
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
    fallback.sort(key=lambda v: (v["prompt_id"], v["dataset"]))
    out.extend(fallback[: max(0, target - len(out))])
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
                 build_meta: dict) -> dict:
    models = _all_known_models(catalog, registry, [
        {"model": v["model"]} for law in paperdemo_grouped for v in law["videos"]
    ])
    datasets = _datasets_summary(vis_datasets)
    videos_index = _videos_index(leaderboard_entries, paperdemo_grouped, humaneval_prompts)
    prompts_index = _prompts_index(humaneval_prompts)

    for m in models:
        m["representative_videos"] = _representative_videos(
            m["key"], paperdemo_grouped, humaneval_prompts,
        )
        # Always provide leaderboard-slice cards as a final fallback for the
        # by-model gallery, even when representative_videos is non-empty (the
        # detail page still shows them in a separate section).
        m["leaderboard_slices"] = _model_leaderboard_cards(m["key"], leaderboard_entries)

    n_models = len(models)
    n_eval_combos = len(leaderboard_entries)
    n_annotations = sum(int(v["n_ann"]) for law in paperdemo_grouped for v in law["videos"])
    n_prompts = humaneval_100.get("n_selected") or 0
    if n_prompts == 0:
        n_prompts = len(humaneval_prompts)

    featured_law_name = "collision"
    featured_videos: list[dict] = []
    for law_entry in paperdemo_grouped:
        if law_entry["law"] == featured_law_name:
            featured_videos = law_entry["videos"][:6]
            break
    if not featured_videos and paperdemo_grouped:
        featured_law_name = paperdemo_grouped[0]["law"]
        featured_videos = paperdemo_grouped[0]["videos"][:6]

    paper_url = os.environ.get("PHYGROUND_PAPER_URL", "").strip()

    return {
        "site": {
            "title": "phyground",
            "short_title": "phyground",
            "description": "A physics-grounded benchmark for video generation. Browse model outputs by physical law, compare side-by-side, and explore evaluator-by-dataset leaderboards.",
            "paper_url": paper_url,
            "github_url": "https://github.com/phyground/phyground.github.io",
            "huggingface_url": "https://huggingface.co/juyil",
            "huggingface_dataset_url": HF_BASE.replace("/resolve/main", ""),
            "copyright_year": 2026,
        },
        "headline": {
            "n_models": n_models,
            "n_prompts": n_prompts,
            "n_annotations": n_annotations,
            "n_eval_combos": n_eval_combos,
        },
        "featured_comparison": {
            "law": featured_law_name,
            "videos": featured_videos,
        },
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
        dst = STAGING_DIR / target_url
        _copy_file(src, dst)
    # Strip the helper field from the published rows so it does not leak into site_config.
    for entry in leaderboard_entries:
        entry["current"].pop("_score_relpath", None)
        for h in entry["history"]:
            h.pop("_score_relpath", None)
    for u in leaderboard_unpublished:
        for r in u.get("rows", []):
            r.pop("_score_relpath", None)

    _write_json(STAGING_DIR / "index" / "leaderboard_unpublished.json",
                {"count": len(leaderboard_unpublished), "entries": leaderboard_unpublished})

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
    site_config = _site_config(
        catalog, registry, paperdemo_grouped, vis_datasets,
        humaneval_prompts, humaneval_100, leaderboard_entries,
        leaderboard_unpublished, build_meta,
    )
    _write_json(STAGING_DIR / "index" / "site_config.json", site_config)

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

    # 9. Atomic swap.
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
