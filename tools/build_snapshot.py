#!/usr/bin/env python3
"""Build snapshot/ from _wmbench_src/ deterministically.

Reads frozen wmbench inputs (`_wmbench_src/`) and writes:
  snapshot/
  ├── MANIFEST.json           # sha256 over every file under snapshot/
  └── index/
      ├── site_config.json
      ├── eval_registry.frozen.json
      ├── paperdemo.manifest.csv
      ├── vis_datasets.frozen.json
      ├── model_catalog.frozen.json    # extracted from MODEL_CATALOG.py via ast
      └── humaneval_100.json           # stub: schema fixed, prompts empty until Round 2

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
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WMBENCH_SRC = REPO_ROOT / "_wmbench_src"
SNAPSHOT_DIR = REPO_ROOT / "snapshot"
STAGING_DIR = REPO_ROOT / "snapshot.staging"

# ---------- helpers ----------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj) -> None:
    """Write JSON deterministically: sorted keys, 2-space indent, trailing newline, UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


# ---------- MODEL_CATALOG.py parser ----------

def _extract_model_catalog(catalog_py: Path) -> list[dict]:
    """Parse `_wmbench_src/videogen/runner/MODEL_CATALOG.py` without executing it.

    Looks for module-level `_<NAME>_RAW = { "key": {...}, ... }` literals and
    flattens all entries into a single list of dicts shaped:
        {"key": str, "wrapper_module": str, "wrapper_class": str,
         "model": str, "description": str, "family": str,
         "kwargs": dict | None}
    """
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
            entry = {
                "key": key,
                "wrapper_module": cfg.get("wrapper_module"),
                "wrapper_class": cfg.get("wrapper_class"),
                "model": cfg.get("model"),
                "description": cfg.get("description"),
                "family": cfg.get("family"),
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


# ---------- site_config builders ----------

def _dedup_leaderboard(registry: list[dict]) -> list[dict]:
    """Group registry rows by (video_model, dataset, subset, evaluator, schema).

    The newest row (by datetime descending, ties broken by source_json) becomes
    the entry's `current`; all older rows go into `history`.
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
        rows_sorted = sorted(
            rows,
            key=lambda r: (r.get("datetime") or "", r.get("source_json") or ""),
            reverse=True,
        )
        current, *history = rows_sorted
        video_model, dataset, subset, evaluator, schema = key
        entries.append({
            "video_model": video_model,
            "dataset": dataset,
            "subset": subset,
            "evaluator": evaluator,
            "schema": schema,
            "current": current,
            "history": history,
        })
    entries.sort(key=lambda e: (
        e["dataset"] or "",
        e["video_model"] or "",
        e["evaluator"] or "",
        e["schema"] or "",
        e["subset"] or "",
    ))
    return entries


def _group_paperdemo(rows: list[dict]) -> list[dict]:
    by_law: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_law[r["law"]].append({
            "model": r["model"],
            "video_id": r["video_id"],
            "n_ann": r["n_ann"],
            "src_filename": r["src_filename"],
            "src_path": r["src_path"],
            # Round-1 placeholder; real HuggingFace URLs land when the HF dataset is wired.
            "video_url_hf": None,
        })
    out = []
    for law in sorted(by_law.keys()):
        videos = sorted(by_law[law], key=lambda v: (v["model"], v["video_id"]))
        out.append({
            "law": law,
            "fig_svg": f"index/figs/{law}.svg",   # not yet generated; populated when figs are converted
            "fig_pdf_src": f"data/paperdemo/figs/{law}.pdf",
            "videos": videos,
        })
    return out


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
            "wrapper_module": None,
            "model": None,
            "source": "paperdemo",
        }
    return [by_key[k] for k in sorted(by_key.keys())]


def _datasets_summary(vis_datasets: dict) -> list[dict]:
    """Collapse vis_datasets.json's per-(model,dataset) entries into a per-dataset summary."""
    seen: dict[str, dict] = {}
    for entry in vis_datasets.get("datasets", []):
        name = entry.get("name", "")
        # Names like "cosmos2.5-2b-video_phy_2" → dataset is the suffix after the model.
        # The plan calls these out explicitly: humaneval / wmb / video_phy_2 / physics_iq / openvid.
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


def _site_config(catalog: list[dict],
                 registry: list[dict],
                 paperdemo: list[dict],
                 vis_datasets: dict,
                 build_meta: dict) -> dict:
    paperdemo_grouped = _group_paperdemo(paperdemo)
    leaderboard_entries = _dedup_leaderboard(registry)
    models = _all_known_models(catalog, registry, paperdemo)
    datasets = _datasets_summary(vis_datasets)

    # videos_index: empty in Round 1 (videos live on HuggingFace and are not yet wired).
    videos_index: dict[str, list[str]] = {}

    # Headline numbers: best-effort from current data.
    n_models = len(models)
    n_eval_combos = len(leaderboard_entries)
    n_annotations = sum(int(r["n_ann"]) for r in paperdemo)
    n_prompts = 0  # Round 2 will populate after _wmbench_src/data/prompts/ is ingested.

    return {
        "site": {
            "title": "phyground",
            "short_title": "phyground",
            "description": "A physics-grounded benchmark for video generation. Browse model outputs by physical law, compare side-by-side, and explore evaluator-by-dataset leaderboards.",
            "paper_url": "",
            "github_url": "https://github.com/phyground/phyground.github.io",
            "huggingface_url": "https://huggingface.co/juyil",
            "copyright_year": 2026,
        },
        "headline": {
            "n_models": n_models,
            "n_prompts": n_prompts,
            "n_annotations": n_annotations,
            "n_eval_combos": n_eval_combos,
        },
        "models": models,
        "datasets": datasets,
        "leaderboard_entries": leaderboard_entries,
        "paperdemo": paperdemo_grouped,
        "videos_index": videos_index,
        "build_meta": build_meta,
    }


# ---------- humaneval-100 stub ----------

def _humaneval_100_stub(input_sha256: dict) -> dict:
    """See docs/exp-plan/public/humaneval_100.md.

    Round 1 commits a stub: schema and selection_inputs are fixed, prompts is
    empty (real selection runs in a later round once prompt JSONs are ingested).
    The 13 paperdemo laws each get a placeholder quota that sums to 100.
    """
    laws = [
        "boundary_interaction", "buoyancy", "collision", "displacement",
        "flow_dynamics", "fluid_continuity", "gravity", "impenetrability",
        "inertia", "material", "momentum", "reflection", "shadow",
    ]
    base = 100 // len(laws)         # 7
    extra = 100 - base * len(laws)  # 9
    quotas = {law: base for law in laws}
    for law in laws[:extra]:        # alphabetical first 9 laws get +1 (deterministic tie-break)
        quotas[law] += 1
    assert sum(quotas.values()) == 100
    return {
        "schema_version": "1",
        "selected_at": None,
        "selection_inputs": input_sha256,
        "law_quotas": quotas,
        "prompts": [],
        "manual_overrides": [],
        "note": "Round 1 stub. Real selection requires _wmbench_src/data/prompts/ which is not ingested yet. See docs/exp-plan/public/humaneval_100.md.",
    }


# ---------- main ----------

def build(now_iso: str | None = None, *, verbose: bool = True) -> dict:
    """Run the full build. Returns the parsed manifest."""
    if not WMBENCH_SRC.is_dir():
        raise SystemExit(f"_wmbench_src/ not found at {WMBENCH_SRC}. Hard-copy wmbench inputs first.")

    # Required source files; missing = hard error.
    src_paths = {
        "evals/eval_registry.json": WMBENCH_SRC / "evals" / "eval_registry.json",
        "evals/eval_types.py": WMBENCH_SRC / "evals" / "eval_types.py",
        "data/vis_datasets.json": WMBENCH_SRC / "data" / "vis_datasets.json",
        "data/paperdemo/manifest.csv": WMBENCH_SRC / "data" / "paperdemo" / "manifest.csv",
        "videogen/runner/MODEL_CATALOG.py": WMBENCH_SRC / "videogen" / "runner" / "MODEL_CATALOG.py",
    }
    missing = [k for k, p in src_paths.items() if not p.is_file()]
    if missing:
        raise SystemExit(f"missing _wmbench_src/ inputs: {missing}")

    # Stage build under snapshot.staging/.
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    (STAGING_DIR / "index").mkdir(parents=True)

    # 1. Parse inputs.
    catalog = _extract_model_catalog(src_paths["videogen/runner/MODEL_CATALOG.py"])
    registry = _read_eval_registry(src_paths["evals/eval_registry.json"])
    paperdemo = _read_paperdemo_manifest(src_paths["data/paperdemo/manifest.csv"])
    vis_datasets = _read_vis_datasets(src_paths["data/vis_datasets.json"])

    # 2. Freeze the raw inputs into snapshot/index/ (verbatim copies + parsed catalog dump).
    _copy_file(src_paths["evals/eval_registry.json"], STAGING_DIR / "index" / "eval_registry.frozen.json")
    _copy_file(src_paths["data/paperdemo/manifest.csv"], STAGING_DIR / "index" / "paperdemo.manifest.csv")
    _copy_file(src_paths["data/vis_datasets.json"], STAGING_DIR / "index" / "vis_datasets.frozen.json")
    _write_json(STAGING_DIR / "index" / "model_catalog.frozen.json", catalog)

    # 3. humaneval_100 stub.
    input_sha256 = {
        "registry_sha256": _sha256_file(src_paths["evals/eval_registry.json"]),
        "paperdemo_manifest_sha256": _sha256_file(src_paths["data/paperdemo/manifest.csv"]),
        "model_catalog_sha256": _sha256_file(src_paths["videogen/runner/MODEL_CATALOG.py"]),
        "humaneval_prompts_sha256": None,    # not yet ingested
    }
    _write_json(STAGING_DIR / "index" / "humaneval_100.json", _humaneval_100_stub(input_sha256))

    # 4. site_config.json.
    build_meta = {
        "built_at": now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_sha256": input_sha256["registry_sha256"],
        "paperdemo_manifest_sha256": input_sha256["paperdemo_manifest_sha256"],
        "model_catalog_sha256": input_sha256["model_catalog_sha256"],
        "vis_datasets_sha256": _sha256_file(src_paths["data/vis_datasets.json"]),
        "snapshot_sha": None,                 # filled in below after MANIFEST is computed
    }
    site_config = _site_config(catalog, registry, paperdemo, vis_datasets, build_meta)
    _write_json(STAGING_DIR / "index" / "site_config.json", site_config)

    # 5. MANIFEST.json — sha256 every file under the staging directory.
    manifest_files: dict[str, str] = {}
    for root, _dirs, files in os.walk(STAGING_DIR):
        for fname in files:
            p = Path(root) / fname
            rel = str(p.relative_to(STAGING_DIR)).replace(os.sep, "/")
            if rel == "MANIFEST.json":
                continue
            manifest_files[rel] = _sha256_file(p)
    manifest_obj = {
        "schema_version": "1",
        "built_at": build_meta["built_at"],
        "files": dict(sorted(manifest_files.items())),
    }
    manifest_text = json.dumps(manifest_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    snapshot_sha = _sha256_bytes(manifest_text.encode("utf-8"))

    # 6. Patch site_config.build_meta.snapshot_sha now that we know it. Rewrite both files.
    site_config["build_meta"]["snapshot_sha"] = snapshot_sha
    _write_json(STAGING_DIR / "index" / "site_config.json", site_config)
    # site_config changed → its sha256 changed → recompute the manifest one more time.
    manifest_files["index/site_config.json"] = _sha256_file(STAGING_DIR / "index" / "site_config.json")
    manifest_obj["files"] = dict(sorted(manifest_files.items()))
    (STAGING_DIR / "MANIFEST.json").write_text(
        json.dumps(manifest_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 7. Atomic swap.
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    os.rename(STAGING_DIR, SNAPSHOT_DIR)

    if verbose:
        print(f"[build_snapshot] wrote {len(manifest_files)} files to snapshot/")
        print(f"[build_snapshot] snapshot_sha = {snapshot_sha}")
        print(f"[build_snapshot] site_config.headline = {site_config['headline']}")
    return manifest_obj


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build snapshot/ from _wmbench_src/.")
    parser.add_argument("--now", default=None,
                        help="Override the built_at ISO timestamp (for deterministic tests).")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    build(now_iso=args.now, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
