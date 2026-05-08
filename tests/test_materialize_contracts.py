"""Lock the fail-closed contract of `tools/build_hf_upload_manifest.py --materialize`.

Backfills (Round 19+) the contract Codex established across Rounds 13-14
(materialize preflight + non-empty dir guard) and Rounds 15-18 (manifest
not rewritten on staging failure, --clean requires --materialize, dest
type check fires before rmtree). Each contract is one test below; all of
them shell out to the real CLI via the `run_tool` helper.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import HF_MANIFEST_PATH, REPO_ROOT, run_tool


MATERIALIZE_TOOL = "tools/build_hf_upload_manifest.py"
EXPECTED_FILE_COUNT = 884  # 883 manifest targets + synthesized README.md (Round 13)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


def _first_first_images_entry(manifest: dict) -> dict:
    for entry in manifest["files"]:
        if entry["hf_target_path"].startswith("first_images/"):
            return entry
    raise AssertionError("manifest has no first_images/* entries")


def test_dirty_dest_without_clean_fails(tmp_path: Path) -> None:
    """Round 13: materialize must refuse a non-empty staging dir without --clean."""
    staging = tmp_path / "staging"
    staging.mkdir()
    stale = staging / "stale.txt"
    stale.write_text("stale", encoding="utf-8")

    result = run_tool(MATERIALIZE_TOOL, "--materialize", str(staging))

    assert result.returncode != 0, (
        f"expected nonzero exit; got 0\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "refusing to materialize into non-empty" in result.stderr, (
        f"missing expected refusal message\nstderr: {result.stderr}"
    )
    assert stale.exists(), "fail-closed: stale file must survive a refused materialize"
    assert stale.read_text(encoding="utf-8") == "stale"


def test_dirty_dest_with_clean_succeeds(tmp_path: Path) -> None:
    """Round 13/14: --clean wipes the dir, then materializes the canonical tree."""
    staging = tmp_path / "staging"
    staging.mkdir()
    stale = staging / "stale.txt"
    stale.write_text("stale", encoding="utf-8")

    result = run_tool(
        MATERIALIZE_TOOL, "--materialize", str(staging), "--clean",
    )

    assert result.returncode == 0, (
        f"expected success; got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # No error pattern in stderr (stderr may legitimately be empty).
    assert "refusing" not in result.stderr
    assert "missing locally" not in result.stderr

    n_files = _count_files(staging)
    assert n_files == EXPECTED_FILE_COUNT, (
        f"expected {EXPECTED_FILE_COUNT} files in staging; got {n_files}"
    )
    assert not stale.exists(), "stale file must be wiped by --clean"

    readme = staging / "README.md"
    assert readme.is_file(), "synthesized README.md missing from staging tree"

    manifest = json.loads(HF_MANIFEST_PATH.read_text(encoding="utf-8"))
    readme_entry = next(
        e for e in manifest["files"] if e["hf_target_path"] == "README.md"
    )
    assert _sha256(readme) == readme_entry["sha256"], (
        "materialized README sha256 must match the manifest entry"
    )


def test_empty_dest_succeeds(tmp_path: Path) -> None:
    """Round 13: an empty pre-existing dir is acceptable without --clean."""
    staging = tmp_path / "staging"
    staging.mkdir()

    result = run_tool(MATERIALIZE_TOOL, "--materialize", str(staging))

    assert result.returncode == 0, (
        f"expected success; got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    n_files = _count_files(staging)
    assert n_files == EXPECTED_FILE_COUNT, (
        f"expected {EXPECTED_FILE_COUNT} files in staging; got {n_files}"
    )


def test_clean_alone_fails(tmp_path: Path) -> None:
    """Round 14: --clean is meaningless without --materialize and must abort."""
    result = run_tool(MATERIALIZE_TOOL, "--clean")

    assert result.returncode != 0, (
        f"expected nonzero exit; got 0\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "--clean requires --materialize" in combined, (
        f"missing guard message in stdout/stderr\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_non_directory_dest_fails(tmp_path: Path) -> None:
    """Round 15-18: dest type check fires before any mutation, even with --clean.

    A bare file at the staging path must abort with the type-check message
    *before* the --clean rmtree path executes; otherwise --clean would
    silently delete the operator's unrelated file.
    """
    notdir = tmp_path / "notdir"
    original_bytes = b"i am a regular file, not a directory\n"
    notdir.write_bytes(original_bytes)

    # Without --clean.
    result = run_tool(MATERIALIZE_TOOL, "--materialize", str(notdir))
    assert result.returncode != 0, (
        f"expected nonzero exit; got 0\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "exists but is not a directory" in result.stderr, (
        f"missing expected message\nstderr: {result.stderr}"
    )
    assert notdir.is_file(), "regular file must not be mutated"
    assert notdir.read_bytes() == original_bytes

    # With --clean — type check must still fire first (must NOT rmtree the file).
    result_clean = run_tool(
        MATERIALIZE_TOOL, "--materialize", str(notdir), "--clean",
    )
    assert result_clean.returncode != 0, (
        f"expected nonzero exit even with --clean; got 0\n"
        f"stdout: {result_clean.stdout}\nstderr: {result_clean.stderr}"
    )
    assert "exists but is not a directory" in result_clean.stderr, (
        f"--clean must hit type check first\nstderr: {result_clean.stderr}"
    )
    assert notdir.is_file(), "regular file must not be removed by --clean"
    assert notdir.read_bytes() == original_bytes


def test_missing_source_preflight(tmp_path: Path) -> None:
    """Round 14: missing local source aborts before any file is staged.

    Mutates `_wmbench_src/` — uses try/finally with sha256 check to guarantee
    the moved file is restored byte-identically even on assertion failure.
    A leaked move would break the rest of the test suite and hf_staging.
    """
    manifest = json.loads(HF_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = _first_first_images_entry(manifest)
    src = REPO_ROOT / entry["local_source"]
    assert src.is_file(), f"prerequisite: {src} must exist before we hide it"
    pre_sha = _sha256(src)

    hidden = tmp_path / "__hidden_first_image.jpg"
    staging = tmp_path / "staging"

    shutil.move(str(src), str(hidden))
    try:
        assert not src.exists(), "src must be hidden before invoking materialize"
        result = run_tool(
            MATERIALIZE_TOOL, "--materialize", str(staging), "--clean",
        )

        assert result.returncode != 0, (
            f"expected nonzero exit; got 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "manifest target(s) missing locally" in result.stderr, (
            f"missing expected preflight message\nstderr: {result.stderr}"
        )
        # No partial tree must have been written.
        if staging.exists():
            n_files = _count_files(staging)
            assert n_files == 0, (
                f"preflight failure must not write any files; got {n_files}"
            )
    finally:
        # Restore unconditionally and verify byte-identical restoration.
        if hidden.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(hidden), str(src))
        assert src.is_file(), (
            f"CRITICAL: failed to restore {src}; "
            f"hf_staging and other tests will break"
        )
        post_sha = _sha256(src)
        assert post_sha == pre_sha, (
            f"CRITICAL: restored {src} differs from original "
            f"(pre={pre_sha}, post={post_sha})"
        )


def test_manifest_unchanged_on_failed_staging(tmp_path: Path) -> None:
    """Round 14 (Codex): a failed --materialize must not rewrite the on-disk manifest.

    The CLI's materialize-only mode reads but never re-emits
    snapshot/HF_UPLOAD_MANIFEST.json; a bug in an earlier round caused the
    manifest md5 to change even when staging aborted. This test pins the
    no-rewrite guarantee.
    """
    manifest = json.loads(HF_MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = _first_first_images_entry(manifest)
    src = REPO_ROOT / entry["local_source"]
    assert src.is_file()
    pre_sha = _sha256(src)

    hidden = tmp_path / "__hidden_first_image.jpg"
    staging = tmp_path / "staging"

    md5_before = _md5(HF_MANIFEST_PATH)

    shutil.move(str(src), str(hidden))
    try:
        result = run_tool(
            MATERIALIZE_TOOL, "--materialize", str(staging), "--clean",
        )
        assert result.returncode != 0, (
            f"setup error: expected materialize to fail with hidden source\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        md5_after = _md5(HF_MANIFEST_PATH)
        assert md5_after == md5_before, (
            f"failed materialize must not rewrite snapshot/HF_UPLOAD_MANIFEST.json "
            f"(before={md5_before}, after={md5_after})"
        )
    finally:
        if hidden.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(hidden), str(src))
        assert src.is_file(), (
            f"CRITICAL: failed to restore {src}; "
            f"hf_staging and other tests will break"
        )
        post_sha = _sha256(src)
        assert post_sha == pre_sha, (
            f"CRITICAL: restored {src} differs from original "
            f"(pre={pre_sha}, post={post_sha})"
        )
