"""Export a site-ready JSON for the PhyJudge-9B auto-evaluator leaderboard.

Reads the per-video qwen9b judge result JSONs under
``_wmbench_src/data/scores/ourckpt/eval_qwen9b_local_subq_human_humaneval_set_<video_model>_*.json``
and aggregates them into the same schema the human-eval leaderboard exporter
emits (``humaneval_leaderboard.json``) so the site template can render both
tables with identical code.

Aggregation mirrors ``export_humaneval_leaderboard.py``:
  - General dims (SA, PTV, persistence): per-model mean over per-video scores.
  - Domain (Solid-Body, Fluid, Optical): per-domain mean over per-(video, law)
    scores; physics overall is the count-weighted mean of the three domains.
  - Overall = 0.5 * mean(general) + 0.5 * weighted_mean(domains).

Run from the phyground.github.io project root:
    python tools/export_phyjudge_leaderboard.py
"""

import argparse
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCORES_DIR = REPO_ROOT / "_wmbench_src" / "data" / "scores" / "ourckpt"
DEFAULT_OUT = REPO_ROOT / "_wmbench_src" / "data" / "phyjudge_leaderboard.json"

# Same 8 video models the paper's PhyJudge-9B per-video-model table covers.
# `meta.video_model` in each JSON is the canonical key we match on.
VIDEO_MODELS = [
    "veo-3.1",
    "wan2.2-i2v-a14b",
    "omniweaving",
    "cosmos-predict2.5-14b",
    "ltx-2.3-22b-dev",
    "wan2.2-ti2v-5b",
    "cosmos-predict2.5-2b",
    "ltx-2-19b-dev",
    "hunyuanvideo-i2v",
    "ltx-2-19b-distilled-fp8",
    "cogvideox1.5-5b-i2v",
]

MODEL_DISPLAY = {
    "veo-3.1": "Veo-3.1",
    "wan2.2-i2v-a14b": "Wan2.2-27B-A14B",
    "omniweaving": "OmniWeaving",
    "cosmos-predict2.5-14b": "Cosmos-14B",
    "ltx-2.3-22b-dev": "LTX-2.3-22B",
    "wan2.2-ti2v-5b": "Wan2.2-TI2V-5B",
    "cosmos-predict2.5-2b": "Cosmos-2B",
    "ltx-2-19b-dev": "LTX-2-19B",
    "hunyuanvideo-i2v": "HunyuanVideo-I2V",
    "ltx-2-19b-distilled-fp8": "LTX-2-19B-Distilled-FP8",
    "cogvideox1.5-5b-i2v": "CogVideoX1.5-5B-I2V",
}

# Paper-short keys (kept aligned with humaneval_leaderboard.json's `model_key`).
SITE_TO_PAPER_KEY = {
    "veo-3.1": "veo-3.1",
    "wan2.2-i2v-a14b": "wan-i2v-a14b",
    "omniweaving": "omniweaving",
    "cosmos-predict2.5-14b": "cosmos-14b",
    "ltx-2.3-22b-dev": "ltx-2.3-22b-dev",
    "wan2.2-ti2v-5b": "wan2.2-ti2v-5b",
    "cosmos-predict2.5-2b": "cosmos-2b",
    "ltx-2-19b-dev": "ltx-2-19b-dev",
    "hunyuanvideo-i2v": "hunyuanvideo-i2v",
    "ltx-2-19b-distilled-fp8": "ltx-2-19b-distilled-fp8",
    "cogvideox1.5-5b-i2v": "cogvideox1.5-5b-i2v",
}

CLOSED_SOURCE_MODELS = {"veo-3.1"}

LAW_TO_DOMAIN = {
    "gravity": "Solid-Body", "inertia": "Solid-Body", "momentum": "Solid-Body",
    "impenetrability": "Solid-Body", "collision": "Solid-Body", "material": "Solid-Body",
    "buoyancy": "Fluid", "displacement": "Fluid",
    "flow_dynamics": "Fluid", "boundary_interaction": "Fluid", "fluid_continuity": "Fluid",
    "reflection": "Optical", "shadow": "Optical",
}

GENERAL_DIMS = ["SA", "PTV", "persistence"]
PHYSICS_DOMAINS = ["Solid-Body", "Fluid", "Optical"]
ALL_DIMS = GENERAL_DIMS + PHYSICS_DOMAINS

DIM_LABELS = {
    "SA": "SA",
    "PTV": "PTV",
    "persistence": "Persist.",
    "Solid-Body": "Solid-Body",
    "Fluid": "Fluid",
    "Optical": "Optical",
    "Overall": "Overall",
}


def latest_json(scores_dir: Path, video_model: str) -> Path:
    """Pick the most recent non-empty canonical qwen9b judge JSON for ``video_model``.

    Canonical filename:
        eval_qwen9b_local_subq_human_humaneval_set_<video_model>_<YYYYMMDD>_<HHMMSS>.json
    Ablation runs (``_fps2_``, ``_fps4_``, ``_shard0of4_`` …) get filtered out
    by requiring the suffix after ``<video_model>_`` to be exactly an 8-digit
    date + underscore + 6-digit time. Empty placeholder runs (num_videos=0)
    are also dropped.
    """
    pattern = f"eval_qwen9b_local_subq_human_humaneval_set_{video_model}_*.json"
    suffix_re = re.compile(r"^\d{8}_\d{6}$")
    candidates = sorted(scores_dir.glob(pattern))
    if not candidates:
        raise SystemExit(f"no qwen9b judge JSON for {video_model} under {scores_dir}")
    real: list[tuple[Path, dict]] = []
    for p in candidates:
        prefix = f"eval_qwen9b_local_subq_human_humaneval_set_{video_model}_"
        suffix = p.stem[len(prefix):]  # everything between the model and ".json"
        if not suffix_re.match(suffix):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("num_videos", 0) > 0 and data.get("results"):
            real.append((p, data))
    if not real:
        raise SystemExit(f"no canonical non-empty qwen9b judge JSON for {video_model}")
    real.sort(key=lambda pd: pd[0].name)
    return real[-1][0]


def aggregate(json_path: Path) -> tuple[dict[str, float], dict[str, int]]:
    """Return ({col_key: score}, {domain: per-(video,law) count}) for one model."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    results = data.get("results", [])
    if not results:
        raise SystemExit(f"empty judge results in {json_path}")

    gen_scores: dict[str, list[float]] = defaultdict(list)
    domain_scores: dict[str, list[float]] = defaultdict(list)

    for r in results:
        for dim in GENERAL_DIMS:
            v = r.get(dim)
            if v is None:
                continue
            gen_scores[dim].append(float(v))
        laws = (r.get("physical") or {}).get("laws") or {}
        for law, info in laws.items():
            if not isinstance(info, dict):
                continue
            if info.get("status") != "scored":
                continue
            score = info.get("score")
            if score is None:
                continue
            domain = LAW_TO_DOMAIN.get(law)
            if domain is None:
                continue
            domain_scores[domain].append(float(score))

    out: dict[str, float] = {}
    for d in GENERAL_DIMS:
        vals = gen_scores[d]
        out[d] = statistics.mean(vals) if vals else 0.0
    counts: dict[str, int] = {}
    for d in PHYSICS_DOMAINS:
        vals = domain_scores[d]
        out[d] = statistics.mean(vals) if vals else 0.0
        counts[d] = len(vals)

    general_vals = [out[d] for d in GENERAL_DIMS if out[d] > 0]
    general_score = statistics.mean(general_vals) if general_vals else 0.0
    phys_den = sum(counts.values())
    phys_num = sum(out[d] * counts[d] for d in PHYSICS_DOMAINS)
    physics_score = phys_num / phys_den if phys_den > 0 else 0.0
    if general_score > 0 and physics_score > 0:
        out["Overall"] = 0.5 * general_score + 0.5 * physics_score
    else:
        out["Overall"] = general_score or physics_score
    return out, counts


def round2(x: float) -> float:
    return float(f"{x:.2f}")


def build_payload(scores_dir: Path) -> tuple[dict, list[Path]]:
    cols = ALL_DIMS + ["Overall"]
    raw: dict[str, dict[str, float]] = {}
    used_paths: list[Path] = []
    n_prompts: int | None = None
    for vm in VIDEO_MODELS:
        path = latest_json(scores_dir, vm)
        used_paths.append(path)
        agg, _ = aggregate(path)
        raw[vm] = agg
        # Pull num_videos from the JSON for the n_prompts metadata field.
        nv = json.loads(path.read_text(encoding="utf-8")).get("num_videos")
        if nv:
            n_prompts = nv if n_prompts is None else max(n_prompts, nv)

    models = sorted(raw, key=lambda m: raw[m]["Overall"], reverse=True)
    rounded = {m: {c: round2(raw[m][c]) for c in cols} for m in models}

    best = {c: max(rounded[m][c] for m in models) for c in cols}
    second: dict[str, float | None] = {}
    for c in cols:
        ranked = sorted({rounded[m][c] for m in models}, reverse=True)
        second[c] = ranked[1] if len(ranked) > 1 else None

    rows = []
    for rank, m in enumerate(models, start=1):
        rows.append({
            "rank": rank,
            "model_key": SITE_TO_PAPER_KEY[m],
            "site_model_key": m,
            "display_name": MODEL_DISPLAY[m],
            "closed_source": m in CLOSED_SOURCE_MODELS,
            "scores": rounded[m],
        })

    payload = {
        "source": "_wmbench_src/data/scores/ourckpt/eval_qwen9b_local_subq_human_humaneval_set_<video_model>_*.json",
        "judge": "PhyJudge-9B (Qwen3.5-9B finetune, subq+human prompt)",
        "judge_url": "https://huggingface.co/NU-World-Model-Embodied-AI/phyjudge-9B",
        "source_files": [str(p.relative_to(REPO_ROOT)) for p in used_paths],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_prompts": n_prompts or 250,
        "scale": "1-5, higher is better",
        "aggregation": (
            "per-video judge scores → per-model mean across videos. "
            "Overall = 0.5 * mean(SA, PTV, Persist.) + 0.5 * weighted_mean(Solid-Body, Fluid, Optical) "
            "where domain weights = number of (video, law) scores contributing to each domain."
        ),
        "columns": {
            "general": [{"key": k, "label": DIM_LABELS[k]} for k in GENERAL_DIMS],
            "domain": [{"key": k, "label": DIM_LABELS[k]} for k in PHYSICS_DOMAINS],
            "overall": {"key": "Overall", "label": DIM_LABELS["Overall"]},
        },
        "best": best,
        "second": second,
        "rows": rows,
    }
    return payload, used_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores-dir", type=Path, default=DEFAULT_SCORES_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.scores_dir.is_dir():
        raise SystemExit(f"scores dir not found: {args.scores_dir}")

    payload, used_paths = build_payload(args.scores_dir)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.out} ({len(payload['rows'])} rows)")
    for row in payload["rows"]:
        s = row["scores"]
        dims = "  ".join(f"{DIM_LABELS[d]}={s[d]:.2f}" for d in ALL_DIMS)
        print(f"  [{row['rank']}] {row['display_name']:20s}  {dims}  Overall={s['Overall']:.2f}")
    print("Sources:")
    for p in used_paths:
        print(f"  {p.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
