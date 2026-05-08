#!/usr/bin/env python3
"""Smoke-test the live HuggingFace dataset against the URLs the rendered site embeds.

Reads `snapshot/HF_UPLOAD_MANIFEST.json`, picks one representative URL from
each prefix family (`videos/<model>/...`, `first_images/...`), plus the dataset
root, and issues HEAD requests via stdlib `urllib`. Reports `OK <status>` or
`FAIL <status_or_error>` per probe and exits non-zero if any probe fails.

Standalone — no extra dependencies. Designed to run from a fresh clone after
the user does `huggingface-cli upload`. Every probe is read-only and follows
HF's redirect chain (HEAD on a `/resolve/main/...` URL typically returns 302
to a CDN URL; the script accepts 200/302/307 as success).

Usage:
    python3 tools/smoke_test_hf.py
    python3 tools/smoke_test_hf.py --hf-base "https://huggingface.co/datasets/<other>/<repo>/resolve/main"
    python3 tools/smoke_test_hf.py --quiet         # only print on failure
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "snapshot" / "HF_UPLOAD_MANIFEST.json"
DEFAULT_HF_BASE = "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main"
DATASET_ROOT_README = "README.md"

OK_STATUSES = {200, 301, 302, 303, 307, 308}


def _head(url: str, *, timeout: float = 15.0) -> tuple[int | None, str]:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", "") if e.headers else ""
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}"
    except (TimeoutError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def _pick_sample(manifest: dict, prefix: str) -> str | None:
    for entry in manifest.get("files", []):
        target = entry.get("hf_target_path") or ""
        if target.startswith(prefix + "/"):
            return target
    return None


def run(manifest_path: Path, hf_base: str, *, verbose: bool = True) -> int:
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found at {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hf_base = hf_base.rstrip("/")

    probes: list[tuple[str, str]] = []
    sample_video = _pick_sample(manifest, "videos")
    if sample_video:
        probes.append(("video", f"{hf_base}/{sample_video}"))
    sample_first_frame = _pick_sample(manifest, "first_images")
    if sample_first_frame:
        probes.append(("first_frame", f"{hf_base}/{sample_first_frame}"))
    probes.append(("dataset_root", f"{hf_base}/{DATASET_ROOT_README}"))

    n_fail = 0
    for label, url in probes:
        status, ctype = _head(url)
        ok = status in OK_STATUSES
        if not ok:
            n_fail += 1
        if verbose or not ok:
            tag = "OK  " if ok else "FAIL"
            shown_status = status if status is not None else "-"
            print(f"[smoke_test_hf] {tag} {shown_status} ({label}) {url}")
            if ctype and verbose:
                print(f"                content-type: {ctype}")
    if verbose:
        if n_fail:
            print(f"[smoke_test_hf] {n_fail}/{len(probes)} probes FAILED")
        else:
            print(f"[smoke_test_hf] {len(probes)}/{len(probes)} probes OK")
    return 0 if n_fail == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test HF asset URLs.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help=f"Path to HF_UPLOAD_MANIFEST.json (default: {DEFAULT_MANIFEST.relative_to(REPO_ROOT)})")
    parser.add_argument("--hf-base", default=DEFAULT_HF_BASE,
                        help="HF resolve base URL (default: %(default)s)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return run(args.manifest, args.hf_base, verbose=not args.quiet)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
