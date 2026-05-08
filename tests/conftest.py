"""Shared fixtures for the phyground.github.io test suite.

These tests are a backfill (Round 19+) — they lock in the contracts
established by Rounds 13–18 so any future regression is caught locally
instead of in Codex review. The suite reads the *current* repo state
(snapshot/index/site_config.json, rendered HTML, etc.) by default; tests
that need to mutate state run inside `tmp_path` and shell out to the
real CLIs in tools/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = REPO_ROOT / "snapshot"
SITE_CONFIG_PATH = SNAPSHOT_DIR / "index" / "site_config.json"
HF_MANIFEST_PATH = SNAPSHOT_DIR / "HF_UPLOAD_MANIFEST.json"


# 8 published model keys. Pinned per Round-15/17/18 user direction:
# only models with 100/100 humaneval-100 coverage are part of the
# website. This list is the source of truth for the test contracts.
PUBLISHED_MODEL_KEYS = frozenset({
    "cosmos-predict2.5-14b",
    "cosmos-predict2.5-2b",
    "ltx-2-19b-dev",
    "ltx-2.3-22b-dev",
    "omniweaving",
    "veo-3.1",
    "wan2.2-i2v-a14b",
    "wan2.2-ti2v-5b",
})

# 8 hidden keys: 4 baseline_i2v_* (eval_registry-derived) + 4 omitted
# MODEL_CATALOG keys (3 with partial humaneval coverage + 1 truly empty).
HIDDEN_MODEL_KEYS = frozenset({
    "baseline_i2v_1258d20d06f0",
    "baseline_i2v_1fb20a34810f",
    "baseline_i2v_92bad6f89f53",
    "baseline_i2v_d85c358f8627",
    "cogvideox-5b-i2v",
    "cogvideox1.5-5b-i2v",
    "hunyuanvideo-i2v",
    "ltx-2-19b-distilled-fp8",
})

HF_PREFIX = "https://huggingface.co/datasets/juyil/phygroundwebsitevideo/resolve/main/"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def site_config() -> dict:
    """Parsed `snapshot/index/site_config.json` from the current repo state."""
    if not SITE_CONFIG_PATH.is_file():
        pytest.skip(f"{SITE_CONFIG_PATH.relative_to(REPO_ROOT)} not present; run build_snapshot.py first.")
    return json.loads(SITE_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def hf_manifest() -> dict:
    if not HF_MANIFEST_PATH.is_file():
        pytest.skip(f"{HF_MANIFEST_PATH.relative_to(REPO_ROOT)} not present; run build_snapshot.py first.")
    return json.loads(HF_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def published_keys() -> frozenset[str]:
    return PUBLISHED_MODEL_KEYS


@pytest.fixture(scope="session")
def hidden_keys() -> frozenset[str]:
    return HIDDEN_MODEL_KEYS


def run_tool(*argv: str, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a python script under tools/ and capture stdout+stderr.

    Tests that need a CLI invocation (e.g. materialize) use this helper
    rather than importing the module so the CLI argparse path is exercised
    and stdout/stderr are captured for assertions.
    """
    return subprocess.run(
        [sys.executable, *argv],
        cwd=str(cwd) if cwd else str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )
