#!/usr/bin/env python3
"""Render Jinja2 templates into static HTML for the phyground.github.io site.

Inputs:
  - tools/templates/**/*.html (Jinja2 templates)
  - tools/static_src/**       (CSS / JS source, copied verbatim to static/)
  - --config <path>           site_config.json (defaults to tools/site_config.example.json)

Output:
  - <repo_root>/index.html
  - <repo_root>/leaderboard/index.html
  - <repo_root>/static/...   (mirrored from static_src/)

The build is deterministic: same config + same templates produce byte-identical
HTML. No network access required.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
except ImportError:
    sys.stderr.write(
        "ERROR: jinja2 is not installed. Install with: pip install jinja2\n"
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
TEMPLATES_DIR = TOOLS_DIR / "templates"
STATIC_SRC_DIR = TOOLS_DIR / "static_src"
DEFAULT_CONFIG = TOOLS_DIR / "site_config.example.json"
STATIC_OUT_DIR = REPO_ROOT / "static"


@dataclass(frozen=True)
class Page:
    template: str          # path within templates/, e.g. "home/index.html"
    out_path: str          # path within REPO_ROOT, e.g. "index.html" or "leaderboard/index.html"
    extra_ctx: dict | None = None   # extra Jinja context (e.g. {"model": <model dict>})


# Top-level pages. Per-model pages are appended at render time.
STATIC_PAGES: tuple[Page, ...] = (
    Page(template="home/index.html", out_path="index.html"),
    Page(template="leaderboard/index.html", out_path="leaderboard/index.html"),
    Page(template="videos/index.html", out_path="videos/index.html"),
    Page(template="videos/compare.html", out_path="videos/compare/index.html"),
    Page(template="about/index.html", out_path="about/index.html"),
)


def _model_pages(models: list[dict]) -> list[Page]:
    """One /models/<key>/index.html per model in the snapshot."""
    out: list[Page] = []
    for m in models:
        key = m.get("key")
        if not key:
            continue
        out.append(Page(
            template="models/detail.html",
            out_path=f"models/{key}/index.html",
            extra_ctx={"model": m},
        ))
    return out


def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _make_rel(out_rel: str):
    """Return a Jinja-callable `rel(target)` that resolves a site-relative URL.

    `out_rel` is the page's path relative to repo root (e.g. "leaderboard/index.html").
    The resulting `rel("static/css/base.css")` produces a path that works regardless
    of where on disk the rendered HTML is opened (file://) and on GitHub Pages.
    """
    out_dir = os.path.dirname(out_rel)
    depth = 0 if out_dir == "" else out_dir.count("/") + 1

    def rel(target: str) -> str:
        target = target.lstrip("/")
        if depth == 0:
            return target if target else "./"
        prefix = "../" * depth
        return prefix + target if target else prefix.rstrip("/") + "/"

    return rel


def _mirror_static(src: Path, dst: Path) -> int:
    """Replace dst with the contents of src. Returns the number of files copied."""
    if dst.exists():
        shutil.rmtree(dst)
    if not src.exists():
        return 0
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        out_dir = dst / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        for fname in files:
            shutil.copy2(Path(root) / fname, out_dir / fname)
            n += 1
    return n


HF_URL_RE = re.compile(
    r"https://huggingface\.co/datasets/NU-World-Model-Embodied-AI/phyground/resolve/main/([^\s'\"<>]+)"
)


def _audit_embedded_urls(rendered_pages: list[Path], manifest_path: Path) -> None:
    """Parse every rendered HTML / inline-JSON file and confirm every embedded
    HF URL has a matching `hf_target_path` in the manifest. Raises SystemExit
    on any miss.
    """
    if not manifest_path.is_file():
        raise SystemExit(f"audit: manifest not found at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_targets = {entry["hf_target_path"] for entry in manifest.get("files", [])}
    embedded: set[str] = set()
    for page in rendered_pages:
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        for m in HF_URL_RE.finditer(text):
            embedded.add(m.group(1))
    missing = sorted(embedded - manifest_targets)
    if missing:
        raise SystemExit(
            f"audit: {len(missing)} embedded HF URLs missing from manifest "
            f"({manifest_path.relative_to(REPO_ROOT) if manifest_path.is_relative_to(REPO_ROOT) else manifest_path}). "
            f"First few: {missing[:5]}"
        )


def render(config_path: Path, *, verbose: bool = True) -> None:
    if not config_path.is_file():
        raise SystemExit(f"config file not found: {config_path}")

    config = _load_config(config_path)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )

    n_static = _mirror_static(STATIC_SRC_DIR, STATIC_OUT_DIR)
    if verbose:
        print(f"[build_site] mirrored {n_static} file(s) to static/")

    # Pass the full snapshot data model into every template. Templates that
    # don't consume a key simply ignore it; templates that need real data find
    # everything they need in `models` / `datasets` / `leaderboard_entries` /
    # `paperdemo` / `videos_index`. Keys default to safe empty containers when
    # the config (e.g. site_config.example.json stub) doesn't carry them.
    snapshot_ctx = {
        "models": config.get("models", []),
        "datasets": config.get("datasets", []),
        "leaderboard_entries": config.get("leaderboard_entries", []),
        "paperdemo": config.get("paperdemo", []),
        "videos_index": config.get("videos_index", {}),
        "prompts_index": config.get("prompts_index", {}),
        "featured_comparison": config.get("featured_comparison", {}),
        "humaneval_100_summary": config.get("humaneval_100_summary", {}),
    }

    pages: list[Page] = list(STATIC_PAGES) + _model_pages(snapshot_ctx["models"])
    rendered: list[Path] = []

    for page in pages:
        out_path = REPO_ROOT / page.out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        template = env.get_template(page.template)
        ctx = {
            "site": config["site"],
            "headline": config["headline"],
            "build_meta": config.get("build_meta", {}),
            "rel": _make_rel(page.out_path),
            **snapshot_ctx,
        }
        if page.extra_ctx:
            ctx.update(page.extra_ctx)
        html = template.render(**ctx)
        out_path.write_text(html, encoding="utf-8")
        rendered.append(out_path)
        if verbose:
            print(f"[build_site] rendered {page.template} -> {page.out_path}")
    if verbose:
        print(f"[build_site] {len(pages)} pages total")

    # Audit: every embedded HF URL must be in the manifest. Skips quietly when
    # the manifest doesn't exist (e.g. when rendering against the stub config).
    manifest_path = REPO_ROOT / "snapshot" / "HF_UPLOAD_MANIFEST.json"
    if manifest_path.is_file():
        _audit_embedded_urls(rendered, manifest_path)
        if verbose:
            print(f"[build_site] HF URL audit: every embedded URL is in manifest.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render the phyground site.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"Path to site_config.json (default: {DEFAULT_CONFIG.relative_to(REPO_ROOT)})")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    render(args.config, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
