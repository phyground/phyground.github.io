"""Tests for the site-audit harness CLI surface (Round 0 scaffold).

These tests exercise `tools/site_audit/run_audit.py` in `--dry-run` mode so
they do not require Playwright or a real browser. They lock in:

  * the four required CLI flags (`--target`, `--urls`, `--out`, `--viewport`)
    plus `--dry-run`,
  * the per-entry record schema written to `records.json`,
  * package layout (`python -m tools.site_audit.run_audit` works),
  * misuse handling (invalid `--target` exits non-zero with stderr).

The harness is the foundation for later rounds that will run real captures
against a local rebuild and the user-fork URL; here we only verify the
scaffold so subsequent rounds have a stable contract to extend.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_AUDIT_SCRIPT = REPO_ROOT / "tools" / "site_audit" / "run_audit.py"

# Exact per-entry record schema required by the Round 0 contract for
# task1. Keep this in lockstep with `tools.site_audit.AuditRecord`.
REQUIRED_RECORD_KEYS = frozenset({
    "url",
    "prefixed_url",
    "target",
    "final_url",
    "http_status",
    "viewport",
    "console_error_count",
    "failed_request_count",
    "screenshot_path",
    "console_errors",
    "failed_requests",
})


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _write_urls(tmp_path: Path, urls: list[str]) -> Path:
    p = tmp_path / "urls.txt"
    p.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return p


def test_run_audit_help_exits_zero() -> None:
    """`python tools/site_audit/run_audit.py --help` exits 0 and lists flags."""
    result = _run(str(RUN_AUDIT_SCRIPT), "--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--target", "--urls", "--out", "--viewport", "--dry-run"):
        assert flag in out, f"--help output missing {flag}:\n{out}"


def test_run_audit_module_invocation_works() -> None:
    """`python -m tools.site_audit.run_audit --help` exits 0 (package layout)."""
    result = _run("-m", "tools.site_audit.run_audit", "--help")
    assert result.returncode == 0, result.stderr
    assert "--target" in result.stdout


def test_run_audit_invalid_target_exits_nonzero(tmp_path: Path) -> None:
    """An unknown `--target` value is rejected with a clear stderr message."""
    urls = _write_urls(tmp_path, ["/"])
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "other",
        "--urls", str(urls),
        "--dry-run",
    )
    assert result.returncode != 0
    # argparse-style error or our own; either way "target" should be mentioned.
    combined = (result.stderr + result.stdout).lower()
    assert "target" in combined


def test_run_audit_dry_run_local_emits_records_json(tmp_path: Path) -> None:
    """Dry-run --target local writes records.json with the required schema."""
    urls = _write_urls(tmp_path, ["/", "/about/"])
    out_dir = tmp_path / "artifacts"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "local",
        "--urls", str(urls),
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr

    records_path = out_dir / "records.json"
    assert records_path.is_file(), f"records.json not written under {out_dir}"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    assert isinstance(records, list)
    assert len(records) == 2

    for rec, expected_url in zip(records, ["/", "/about/"]):
        assert set(rec.keys()) == REQUIRED_RECORD_KEYS, (
            f"record key mismatch: got {sorted(rec.keys())}"
        )
        assert rec["url"] == expected_url
        assert rec["target"] == "local"
        assert rec["viewport"] == "1280x800"
        assert rec["final_url"] is None
        assert rec["http_status"] is None
        assert rec["console_error_count"] == 0
        assert rec["failed_request_count"] == 0
        assert rec["console_errors"] == []
        assert rec["failed_requests"] == []
        # screenshot_path lives under the artifacts dir.
        screenshot = Path(rec["screenshot_path"])
        # Should be inside out_dir (resolve to handle relative vs absolute).
        try:
            screenshot.resolve().relative_to(out_dir.resolve())
        except ValueError:
            pytest.fail(
                f"screenshot_path {screenshot!r} is not under {out_dir!r}"
            )
        # prefixed_url for --target local starts with http://127.0.0.1: and
        # ends with the original relative url.
        assert rec["prefixed_url"].startswith("http://127.0.0.1:")
        assert rec["prefixed_url"].endswith(expected_url)


def test_run_audit_dry_run_fork_uses_fork_prefix(tmp_path: Path) -> None:
    """Dry-run --target fork stores fork-prefixed URLs in records."""
    urls = _write_urls(tmp_path, ["/", "/about/"])
    out_dir = tmp_path / "artifacts"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "fork",
        "--urls", str(urls),
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr

    records = json.loads((out_dir / "records.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    assert all(rec["target"] == "fork" for rec in records)
    # prefix.rstrip("/") + url, exactly.
    expected_prefixed = [
        "https://lukelin-web.github.io/phyground.github.io/",
        "https://lukelin-web.github.io/phyground.github.io/about/",
    ]
    assert [rec["prefixed_url"] for rec in records] == expected_prefixed
    for rec in records:
        screenshot = Path(rec["screenshot_path"])
        try:
            screenshot.resolve().relative_to(out_dir.resolve())
        except ValueError:
            pytest.fail(
                f"screenshot_path {screenshot!r} is not under {out_dir!r}"
            )
