"""Lock byte-identical snapshot+site rebuild determinism (Round 16-18 backfill).

Codex Round 16 review fingered three sources of non-determinism (timestamp
embedding in MANIFEST.json, dict ordering in HF_UPLOAD_MANIFEST.json, and
`set()` iteration order in site_config target collection). Rounds 17-18
fixed each one. This module pins the post-fix property: two consecutive
runs of the snapshot+site pipeline produce byte-identical artifacts.

Marked `slow` and `determinism` — the snapshot rebuild takes ~10s and the
site render takes <1s, so two passes finish well under 2 minutes.

NOTE: This test mutates the working tree (rewrites snapshot/ and the
top-level rendered HTML files). Do not commit the mutations; the contents
are deterministic so a second pipeline run restores byte-identity anyway.
"""
from __future__ import annotations

import hashlib
import pytest
from pathlib import Path

from tests.conftest import REPO_ROOT, run_tool


pytestmark = [pytest.mark.determinism, pytest.mark.slow]


SNAPSHOT_ARTIFACTS = (
    "snapshot/MANIFEST.json",
    "snapshot/HF_UPLOAD_MANIFEST.json",
    "snapshot/index/site_config.json",
)
SITE_ARTIFACTS = (
    "index.html",
    "about/index.html",
    "leaderboard/index.html",
    "videos/compare/index.html",
)
ALL_ARTIFACTS = SNAPSHOT_ARTIFACTS + SITE_ARTIFACTS


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_pipeline() -> None:
    """Run snapshot+site build twice the same way each time.

    Pinned `--now` so MANIFEST.json's `generated_at` cannot drift between
    invocations. `--select-humaneval-100` matches the production build.
    """
    snap = run_tool(
        "tools/build_snapshot.py",
        "--now", "2026-05-08T15:00:00Z",
        "--select-humaneval-100",
        "--quiet",
    )
    if snap.returncode != 0:
        raise AssertionError(
            f"build_snapshot.py failed (rc={snap.returncode})\n"
            f"stdout: {snap.stdout}\nstderr: {snap.stderr}"
        )
    site = run_tool(
        "tools/build_site.py",
        "--config", "snapshot/index/site_config.json",
        "--quiet",
    )
    if site.returncode != 0:
        raise AssertionError(
            f"build_site.py failed (rc={site.returncode})\n"
            f"stdout: {site.stdout}\nstderr: {site.stderr}"
        )


def _hash_artifacts() -> dict[str, str]:
    digests: dict[str, str] = {}
    for rel in ALL_ARTIFACTS:
        path = REPO_ROOT / rel
        assert path.is_file(), f"expected artifact missing after build: {rel}"
        digests[rel] = _md5(path)
    return digests


def test_two_consecutive_builds_byte_identical() -> None:
    """Round 16-18: snapshot+site rebuild is byte-deterministic.

    Pin: re-running the pipeline with identical inputs produces md5-identical
    snapshot/MANIFEST.json, snapshot/HF_UPLOAD_MANIFEST.json,
    snapshot/index/site_config.json, and the four published HTML pages.
    """
    _run_pipeline()
    first = _hash_artifacts()

    _run_pipeline()
    second = _hash_artifacts()

    mismatches = [
        (rel, first[rel], second[rel])
        for rel in ALL_ARTIFACTS
        if first[rel] != second[rel]
    ]
    assert not mismatches, (
        "non-deterministic artifacts detected:\n"
        + "\n".join(
            f"  {rel}: first={a} second={b}" for rel, a, b in mismatches
        )
    )
