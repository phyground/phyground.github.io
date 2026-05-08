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
    """Map an `eval_registry.json` source_json value to a path under `_wmbench_src/data/scores/`.

    Returns the relative path under `_wmbench_src/` (`data/scores/...`) if the
    file is present after the Round-2 ingest, else None.
    """
    if not source_json:
        return None
    if source_json.startswith("data/scores/"):
        cand = source_json
    elif source_json.startswith("/") or source_json.startswith("tmp/"):
        # External or tmp paths: not in _wmbench_src/.
        return None
    else:
        # Bare basename or other: not ingested.
        return None
    if (WMBENCH_SRC / cand).is_file():
        return cand
    return None


def _snapshot_score_url(rel_under_wmbench: str) -> str:
    """Map `_wmbench_src/data/scores/<evaluator>/<...>` to `scores/<evaluator>/<...>`."""
    assert rel_under_wmbench.startswith("data/scores/")
    return rel_under_wmbench[len("data/"):]   # → "scores/<evaluator>/<...>"


# ---------- leaderboard dedup (coverage-aware) ----------

def _dedup_leaderboard(registry: list[dict]) -> list[dict]:
    """Group registry rows by (video_model, dataset, subset, evaluator, schema).

    Per plan §6: filter `coverage > 0` before choosing the newest row as `current`;
    the dropped coverage-zero reruns are kept under `history` so they're not lost,
    but they never replace a working result.
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

    entries: list[dict] = []
    for key, rows in groups.items():
        valid = [r for r in rows if (r.get("coverage") or 0) > 0]
        invalid = [r for r in rows if r not in valid]
        if not valid:
            # Skip groups where every result has coverage = 0; nothing publishable.
            continue
        valid_sorted = sorted(
            valid,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        invalid_sorted = sorted(
            invalid,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        current, *older_valid = valid_sorted
        history = older_valid + invalid_sorted
        video_model, dataset, subset, evaluator, schema = key

        # Annotate the row with snapshot-relative URLs for raw download.
        def _annotate(row: dict) -> dict:
            r = dict(row)
            rel = _score_relpath(r.get("source_json") or "")
            if rel:
                r["source_status"] = "available"
                r["source_url_snapshot"] = _snapshot_score_url(rel)
            else:
                r["source_status"] = "missing"
                r["source_url_snapshot"] = None
            return r

        entries.append({
            "video_model": video_model,
            "dataset": dataset,
            "subset": subset,
            "evaluator": evaluator,
            "schema": schema,
            "current": _annotate(current),
            "history": [_annotate(r) for r in history],
        })
    entries.sort(key=lambda e: (
        e["dataset"] or "",
        e["video_model"] or "",
        e["evaluator"] or "",
        e["schema"] or "",
        e["subset"] or "",
    ))
    return entries


# ---------- paperdemo grouping ----------

def _group_paperdemo(rows: list[dict]) -> list[dict]:
    by_law: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rel_under_wmbench = r["src_path"].lstrip("/")  # e.g. "data/paperdemo/<law>/<file>"
        # Snapshot relative path inside HF dataset (and inside snapshot/scores/figs is N/A).
        # Build the HF URL the gallery <video> tag points at.
        # Map "data/paperdemo/<law>/<file>" → "paperdemo/<law>/<file>" inside the HF dataset.
        if rel_under_wmbench.startswith("data/paperdemo/"):
            hf_rel = rel_under_wmbench[len("data/"):]   # "paperdemo/<law>/<file>"
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
            "videos": videos,
        })
    return out


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


def _law_quotas() -> dict[str, int]:
    base = 100 // len(LAWS_13)         # 7
    extra = 100 - base * len(LAWS_13)  # 9
    quotas = {law: base for law in LAWS_13}
    for law in LAWS_13[:extra]:        # alphabetical first 9 laws → +1
        quotas[law] += 1
    return quotas


def _select_humaneval_100(prompts: list[dict],
                          paperdemo: list[dict],
                          existing_selection: dict | None,
                          input_sha256: dict,
                          built_at: str) -> dict:
    """Run the deterministic 5-step selection from humaneval_100.md.

    The implementation uses the per-prompt `per_model_scores` field shipped in
    `_wmbench_src/data/prompts/anonymous_humaneval_set.json` for the score-based
    fill, so it does not need to re-aggregate from per-evaluator score JSONs.
    """
    quotas = _law_quotas()
    # Group prompts by primary physical law (first entry of `physical_laws`).
    by_law: dict[str, list[dict]] = defaultdict(list)
    for p in prompts:
        laws = p.get("physical_laws") or []
        if not laws:
            continue
        primary = laws[0]
        if primary not in quotas:
            continue
        by_law[primary].append(p)

    # Step 1 (intersection gate): keep prompts that have non-empty per_model_scores.
    # Prompts with no model coverage get dropped; they cannot meaningfully be ranked.
    for law in list(by_law):
        by_law[law] = [p for p in by_law[law] if p.get("per_model_scores")]

    # Step 2 (paperdemo seed): include prompts whose `video` matches a paperdemo
    # `src_filename` (without the `.mp4` extension). Paperdemo's video_id is just
    # a row index, so we anchor on filename instead.
    paperdemo_video_stems = {
        Path(v["src_filename"]).stem
        for law_entry in paperdemo
        for v in law_entry["videos"]
    }
    seed_prompt_ids: set[str] = set()
    for law in by_law:
        for p in by_law[law]:
            if p.get("video") in paperdemo_video_stems:
                seed_prompt_ids.add(p["video"])

    # Honour any manual_overrides from a previously-committed selection: their
    # added/removed prompt_ids are applied at the end. We don't bake them into
    # the candidate pool here.
    manual_overrides = (existing_selection or {}).get("manual_overrides", []) or []

    # Step 4 (composite score, normalized per law).
    def _score(p: dict, all_in_law: list[dict]) -> float:
        scores = list(p.get("per_model_scores", {}).values())
        var = statistics.pvariance(scores) if len(scores) >= 2 else 0.0
        cov = len(scores) / max(len(LAWS_13), 1)   # capped roughly at 1
        diff = p.get("difficulty", {}) or {}
        mid = 1.0 - abs((diff.get("phys_micro_avg") or 0.0) - 3.0) / 3.0
        return 0.40 * var + 0.30 * min(cov, 1.0) + 0.30 * max(0.0, min(mid, 1.0))

    selected: list[dict] = []
    for law, prompts_in_law in by_law.items():
        # Per-law normalize variance to [0,1]
        if not prompts_in_law:
            continue
        seeds = [p for p in prompts_in_law if p.get("video") in seed_prompt_ids]
        non_seeds = [p for p in prompts_in_law if p.get("video") not in seed_prompt_ids]
        # Score and rank non-seeds.
        ranked = sorted(
            non_seeds,
            key=lambda p: (-_score(p, prompts_in_law), p.get("video") or ""),
        )
        quota = quotas.get(law, 0)
        seeds_used = seeds[:quota]
        fill_n = max(0, quota - len(seeds_used))
        fill = ranked[:fill_n]
        for p in seeds_used:
            selected.append({
                "prompt_id": p["video"],
                "law": law,
                "source": "paperdemo_seed",
                "score_components": None,
            })
        for p in fill:
            scores = list(p.get("per_model_scores", {}).values())
            var = statistics.pvariance(scores) if len(scores) >= 2 else 0.0
            cov = len(scores) / max(len(LAWS_13), 1)
            diff = p.get("difficulty", {}) or {}
            mid = 1.0 - abs((diff.get("phys_micro_avg") or 0.0) - 3.0) / 3.0
            selected.append({
                "prompt_id": p["video"],
                "law": law,
                "source": "score_fill",
                "score_components": {
                    "variance": round(var, 4),
                    "coverage": round(min(cov, 1.0), 4),
                    "mid_difficulty": round(max(0.0, min(mid, 1.0)), 4),
                },
            })

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

    # Sort by (law, prompt_id) for deterministic output.
    selected.sort(key=lambda s: (s["law"], str(s["prompt_id"])))

    # Effective per-law totals after overrides.
    effective_counts = defaultdict(int)
    for s in selected:
        effective_counts[s["law"]] += 1

    return {
        "schema_version": "1",
        "selected_at": built_at,
        "selection_inputs": input_sha256,
        "law_quotas": quotas,
        "effective_law_counts": dict(sorted(effective_counts.items())),
        "n_selected": len(selected),
        "prompts": selected,
        "manual_overrides": manual_overrides,
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
    """Build a lightweight per-prompt index keyed by `video` (the prompt_id).

    Keeps only fields the compare page needs: prompt text, dataset, physical_laws,
    per_model_scores, difficulty.
    """
    out: dict[str, dict] = {}
    for p in prompts:
        pid = p.get("video")
        if not pid:
            continue
        out[pid] = {
            "prompt_id": pid,
            "dataset": p.get("dataset"),
            "prompt": p.get("prompt"),
            "physical_laws": p.get("physical_laws") or [],
            "difficulty": p.get("difficulty") or {},
            "per_model_scores": p.get("per_model_scores") or {},
        }
    return out


# ---------- videos_index ----------

def _videos_index(leaderboard: list[dict],
                 paperdemo: list[dict]) -> dict[str, list[dict]]:
    """Map "<model>::<dataset>" → list of {prompt_id?, src_filename?, video_url_hf}.

    Round 2 lacks per-(model,dataset) video filename ingestion, so the index is
    derived from paperdemo (precise filenames) plus a synthetic placeholder per
    leaderboard slice (used by the by-model gallery as "we know this combo
    exists; concrete filenames will land when /videos/<model>/<dataset>/ is
    populated upstream").
    """
    idx: dict[str, list[dict]] = defaultdict(list)
    # Paperdemo: each video's model + (we don't always know the dataset). Use law as
    # a coarse bucket so the by-model view still has thumbnails.
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            key = f"{v['model']}::paperdemo:{law_entry['law']}"
            idx[key].append({
                "src_filename": v["src_filename"],
                "video_url_hf": v["video_url_hf"],
                "law": law_entry["law"],
                "video_id": v["video_id"],
                "n_ann": v["n_ann"],
            })
    # Leaderboard slices: include a stub entry so the by-model UI knows the
    # (model, dataset, subset) combination has results.
    for entry in leaderboard:
        key = f"{entry['video_model']}::{entry['dataset']}"
        if not idx[key]:
            idx[key] = []
        # Avoid duplication; just record the slice exists.
        slice_key = f"{entry['dataset']}/{entry['subset']}"
        if not any(e.get("_slice_key") == slice_key for e in idx[key]):
            idx[key].append({
                "_slice_key": slice_key,
                "dataset": entry["dataset"],
                "subset": entry["subset"],
                "evaluator": entry["evaluator"],
                "schema": entry["schema"],
                "phys_avg": entry["current"].get("phys_avg"),
                "gen_avg": entry["current"].get("gen_avg"),
            })
    # Convert to plain dict, sort keys.
    return {k: idx[k] for k in sorted(idx.keys())}


# ---------- representative videos for /models/<key>/ ----------

def _representative_videos(model_key: str,
                           paperdemo: list[dict],
                           target: int = 9) -> list[dict]:
    out: list[dict] = []
    for law_entry in paperdemo:
        for v in law_entry["videos"]:
            if v["model"] == model_key:
                out.append({
                    "law": law_entry["law"],
                    "src_filename": v["src_filename"],
                    "video_url_hf": v["video_url_hf"],
                    "n_ann": v["n_ann"],
                })
                if len(out) >= target:
                    return out
    return out


# ---------- site_config builder ----------

def _site_config(catalog: list[dict],
                 registry: list[dict],
                 paperdemo_grouped: list[dict],
                 vis_datasets: dict,
                 humaneval_prompts: list[dict],
                 humaneval_100: dict,
                 leaderboard_entries: list[dict],
                 build_meta: dict) -> dict:
    models = _all_known_models(catalog, registry, [
        # paperdemo rows in the original schema (model column)
        {"model": v["model"]} for law in paperdemo_grouped for v in law["videos"]
    ])
    datasets = _datasets_summary(vis_datasets)
    videos_index = _videos_index(leaderboard_entries, paperdemo_grouped)
    prompts_index = _prompts_index(humaneval_prompts)

    # Add per-model representative videos (from paperdemo only this round).
    for m in models:
        m["representative_videos"] = _representative_videos(m["key"], paperdemo_grouped)

    n_models = len(models)
    n_eval_combos = len(leaderboard_entries)
    n_annotations = sum(int(v["n_ann"]) for law in paperdemo_grouped for v in law["videos"])
    n_prompts = humaneval_100.get("n_selected") or len(humaneval_prompts)

    # Pick a featured comparison law for the home page.
    # Prefer "collision" (paperdemo's largest bucket) when available.
    featured_law_name = "collision"
    featured_videos: list[dict] = []
    for law_entry in paperdemo_grouped:
        if law_entry["law"] == featured_law_name:
            featured_videos = law_entry["videos"][:6]
            break
    if not featured_videos and paperdemo_grouped:
        featured_law_name = paperdemo_grouped[0]["law"]
        featured_videos = paperdemo_grouped[0]["videos"][:6]

    return {
        "site": {
            "title": "phyground",
            "short_title": "phyground",
            "description": "A physics-grounded benchmark for video generation. Browse model outputs by physical law, compare side-by-side, and explore evaluator-by-dataset leaderboards.",
            "paper_url": "",
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
        },
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

    # Copy the 13 paperdemo law PDFs into snapshot/index/figs/ so the by-law
    # gallery can link to them (in lieu of generated SVG thumbnails).
    figs_src = WMBENCH_SRC / "data" / "paperdemo" / "figs"
    if figs_src.is_dir():
        for pdf in sorted(figs_src.glob("*.pdf")):
            _copy_file(pdf, STAGING_DIR / "index" / "figs" / pdf.name)

    # Copy the humaneval prompts into snapshot/index/.
    if humaneval_prompts and optional_paths["data/prompts/anonymous_humaneval_set.json"].is_file():
        _copy_file(
            optional_paths["data/prompts/anonymous_humaneval_set.json"],
            STAGING_DIR / "index" / "humaneval_prompts.json",
        )

    # 3. Group paperdemo by law (with HF URLs).
    paperdemo_grouped = _group_paperdemo(paperdemo_rows)

    # 4. Build leaderboard entries (coverage filter, source URL rewrite).
    leaderboard_entries = _dedup_leaderboard(registry)

    # 5. Copy the score JSONs that any leaderboard entry actually references into
    #    snapshot/scores/ so the static "Download raw JSON" link resolves.
    referenced_paths: set[str] = set()
    for entry in leaderboard_entries:
        for row in [entry["current"], *entry["history"]]:
            sj = row.get("source_json") or ""
            rel = _score_relpath(sj)
            if rel:
                referenced_paths.add(rel)
    for rel in sorted(referenced_paths):
        src = WMBENCH_SRC / rel
        # Keep the same evaluator subdir layout the row expects.
        dst = STAGING_DIR / "scores" / Path(rel).relative_to("data/scores")
        _copy_file(src, dst)

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
        humaneval_prompts, humaneval_100, leaderboard_entries, build_meta,
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
