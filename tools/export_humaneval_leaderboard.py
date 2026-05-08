"""Export a site-ready JSON whose rows/columns mirror the paper's TABLE_leaderboard.tex.

Reads the same SQLite source as `wmbench/paperscript/gen_table_leaderboard.py`
(`evals/human_eval/human_eval_filtered.db`) and applies the same per-video-mean →
per-model-mean aggregation. Output schema is designed for the site's leaderboard
template to render the paper's six-column view (SA / PTV / Persist. ; Solid-Body
/ Fluid / Optical) plus an Overall column, with best/second markers so the page
can reproduce the paper's bold/underline highlighting.

Run from the phyground.github.io project root:
    python tools/export_humaneval_leaderboard.py
"""

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT.parent / "wmbench" / "evals" / "human_eval" / "human_eval_filtered.db"
DEFAULT_OUT = REPO_ROOT / "snapshot" / "index" / "humaneval_leaderboard.json"

COMPARISON_DATASETS = {
    "wan2.2-ti2v-5b", "ltx-2-19b-dev", "cosmos-predict2.5-2b",
    "cosmos-predict2.5-14b", "veo-3.1", "wan2.2-i2v-a14b",
    "omniweaving", "ltx-2.3-22b-dev",
}

MODEL_DISPLAY = {
    "veo-3.1": "Veo-3.1",
    "wan-i2v-a14b": "Wan2.2-27B-A14B",
    "omniweaving": "OmniWeaving",
    "cosmos-14b": "Cosmos-14B",
    "ltx-2.3-22b-dev": "LTX-2.3-22B",
    "wan2.2-ti2v-5b": "Wan2.2-TI2V-5B",
    "ltx-2-19b-dev": "LTX-2-19B",
    "cosmos-2b": "Cosmos-2B",
}

CLOSED_SOURCE_MODELS = {"veo-3.1"}

# Map paper-short keys (what `gen_table_leaderboard.py` uses internally) to the
# canonical registry keys the site's model pages live under.
PAPER_TO_SITE_KEY = {
    "wan-i2v-a14b": "wan2.2-i2v-a14b",
    "cosmos-14b": "cosmos-predict2.5-14b",
    "cosmos-2b": "cosmos-predict2.5-2b",
    "ltx-2.3-22b-dev": "ltx-2.3-22b-dev",
    "ltx-2-19b-dev": "ltx-2-19b-dev",
    "wan2.2-ti2v-5b": "wan2.2-ti2v-5b",
    "omniweaving": "omniweaving",
    "veo-3.1": "veo-3.1",
}

LAW_TO_DOMAIN = {
    "gravity": "Solid-Body", "inertia": "Solid-Body", "momentum": "Solid-Body",
    "impenetrability": "Solid-Body", "collision": "Solid-Body", "material": "Solid-Body",
    "buoyancy": "Fluid", "displacement": "Fluid",
    "flow_dynamics": "Fluid", "boundary_interaction": "Fluid", "fluid_continuity": "Fluid",
    "reflection": "Optical", "shadow": "Optical",
}

_MODEL_KEY_PREFIXES = [
    ("wan2.2-ti2v-5b", "wan2.2-ti2v-5b"),
    ("ltx-2.3-22b-dev", "ltx-2.3-22b-dev"),
    ("ltx-2-19b", "ltx-2-19b-dev"),
    ("cosmos-predict2.5-2b", "cosmos-2b"),
    ("cosmos-predict2.5-14b", "cosmos-14b"),
    ("veo-3.1", "veo-3.1"),
    ("wan2.2-i2v-a14b", "wan-i2v-a14b"),
    ("omniweaving", "omniweaving"),
]

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


def model_key(dataset: str) -> str:
    for prefix, key in _MODEL_KEY_PREFIXES:
        if dataset.startswith(prefix):
            return key
    return dataset


def load_scores(db_path: Path) -> dict[str, dict[str, float]]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    rows = conn.execute(
        """
        SELECT v.dataset, v.id, ai.dimension, ai.law, ai.score
        FROM annotation_items ai
        JOIN annotations ann ON ann.id = ai.annotation_id
        JOIN assignments a ON a.id = ann.assignment_id
        JOIN videos v ON v.id = a.video_id
        WHERE a.status = 'completed'
        """
    ).fetchall()
    conn.close()

    gen_per_video: dict[tuple[str, int, str], list[int]] = defaultdict(list)
    law_per_video: dict[tuple[str, int, str], list[int]] = defaultdict(list)
    for dataset, vid, dim, law, score in rows:
        if dataset not in COMPARISON_DATASETS:
            continue
        mk = model_key(dataset)
        if dim in GENERAL_DIMS:
            gen_per_video[(mk, vid, dim)].append(score)
        elif law and LAW_TO_DOMAIN.get(law) in PHYSICS_DOMAINS:
            law_per_video[(mk, vid, law)].append(score)

    gen_means: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (mk, _, dim), scores in gen_per_video.items():
        gen_means[mk][dim].append(statistics.mean(scores))

    domain_law_scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (mk, _, law), scores in law_per_video.items():
        domain_law_scores[mk][LAW_TO_DOMAIN[law]].append(statistics.mean(scores))

    result: dict[str, dict[str, float]] = {}
    all_models = {mk for mk, _, _ in gen_per_video} | {mk for mk, _, _ in law_per_video}
    for mk in all_models:
        result[mk] = {}
        for d in GENERAL_DIMS:
            vals = gen_means[mk][d]
            result[mk][d] = statistics.mean(vals) if vals else 0.0
        physics_counts: dict[str, int] = {}
        for d in PHYSICS_DOMAINS:
            vals = domain_law_scores[mk][d]
            result[mk][d] = statistics.mean(vals) if vals else 0.0
            physics_counts[d] = len(vals)
        general_vals = [v for v in (result[mk][d] for d in GENERAL_DIMS) if v > 0]
        general_score = statistics.mean(general_vals) if general_vals else 0.0
        phys_den = sum(physics_counts.values())
        phys_num = sum(result[mk][d] * physics_counts[d] for d in PHYSICS_DOMAINS)
        physics_score = phys_num / phys_den if phys_den > 0 else 0.0
        if general_score > 0 and physics_score > 0:
            result[mk]["Overall"] = 0.5 * general_score + 0.5 * physics_score
        else:
            result[mk]["Overall"] = general_score or physics_score
    return result


def round2(x: float) -> float:
    return float(f"{x:.2f}")


def build_payload(scores: dict[str, dict[str, float]], db_path: Path) -> dict:
    cols = ALL_DIMS + ["Overall"]
    models = sorted(scores, key=lambda m: scores[m]["Overall"], reverse=True)

    rounded: dict[str, dict[str, float]] = {
        m: {c: round2(scores[m][c]) for c in cols} for m in models
    }

    best: dict[str, float] = {c: max(rounded[m][c] for m in models) for c in cols}
    second: dict[str, float | None] = {}
    for c in cols:
        ranked = sorted({rounded[m][c] for m in models}, reverse=True)
        second[c] = ranked[1] if len(ranked) > 1 else None

    rows = []
    for rank, m in enumerate(models, start=1):
        rows.append({
            "rank": rank,
            "model_key": m,
            "site_model_key": PAPER_TO_SITE_KEY.get(m, m),
            "display_name": MODEL_DISPLAY.get(m, m),
            "closed_source": m in CLOSED_SOURCE_MODELS,
            "scores": rounded[m],
        })

    return {
        "source": "wmbench/evals/human_eval/human_eval_filtered.db",
        "source_resolved": str(db_path),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_prompts": 250,
        "scale": "1-5, higher is better",
        "aggregation": (
            "per-annotator scores → per-video mean → per-model mean across videos. "
            "Overall = 0.5 * mean(SA, PTV, Persist.) + 0.5 * weighted_mean(Solid-Body, Fluid, Optical) "
            "where domain weights = number of (video, law) means contributing to each domain."
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB not found: {args.db}")

    scores = load_scores(args.db)
    payload = build_payload(scores, args.db)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.out} ({len(payload['rows'])} rows)")
    for row in payload["rows"]:
        s = row["scores"]
        dims = "  ".join(f"{DIM_LABELS[d]}={s[d]:.2f}" for d in ALL_DIMS)
        print(f"  [{row['rank']}] {row['display_name']:20s}  {dims}  Overall={s['Overall']:.2f}")


if __name__ == "__main__":
    main()
