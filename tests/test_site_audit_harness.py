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
STRUCTURAL_AUDIT_SCRIPT = REPO_ROOT / "tools" / "site_audit" / "structural_audit.py"

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


# ---------------------------------------------------------------------------
# Structural auditor (tools/site_audit/structural_audit.py)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_structural_audit_help_exits_zero() -> None:
    """`python tools/site_audit/structural_audit.py --help` exits 0."""
    result = _run(str(STRUCTURAL_AUDIT_SCRIPT), "--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--repo-root", "--allow-prefix", "--report"):
        assert flag in out, f"--help output missing {flag}:\n{out}"


def test_structural_audit_module_invocation_works() -> None:
    """`python -m tools.site_audit.structural_audit --help` exits 0."""
    result = _run("-m", "tools.site_audit.structural_audit", "--help")
    assert result.returncode == 0, result.stderr
    assert "--repo-root" in result.stdout


def test_structural_audit_clean_html_passes(tmp_path: Path) -> None:
    """An HTML page whose every relative ref resolves on disk exits 0."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        """
        <html><head>
          <link rel="stylesheet" href="static/css/base.css">
          <script src="static/js/app.js"></script>
        </head><body>
          <a href="about/">About</a>
          <img src="static/img/logo.png">
          <a href="https://example.com">External</a>
          <a href="#main">Self</a>
        </body></html>
        """,
    )
    _touch(site / "static" / "css" / "base.css")
    _touch(site / "static" / "js" / "app.js")
    _touch(site / "static" / "img" / "logo.png")
    _touch(site / "about" / "index.html")
    report = tmp_path / "report.json"

    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["broken_refs"] == 0
    assert payload["summary"]["files_audited"] == 1
    assert payload["summary"]["total_refs"] >= 6
    entry = payload["audited"][0]
    assert entry["broken"] == []
    # at least the https://example.com link
    assert any("example.com" in u for u in entry["absolute"])
    # at least the #main fragment
    assert any(f.endswith("#main") or f == "#main" for f in entry["fragments"])


def test_structural_audit_broken_relative_path_reports_exact_missing_path(
    tmp_path: Path,
) -> None:
    """Missing relative ref triggers exit 2 and the resolved on-disk path."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><script src="static/js/missing.js"></script></body></html>',
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 2, (result.returncode, result.stderr, result.stdout)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["broken_refs"] == 1
    entry = payload["audited"][0]
    assert len(entry["broken"]) == 1
    broken = entry["broken"][0]
    assert broken["original_href"] == "static/js/missing.js"
    assert Path(broken["resolved_path"]) == site / "static" / "js" / "missing.js"
    assert broken["tag"] == "script"
    assert broken["attribute"] == "src"


def test_structural_audit_root_relative_path_resolved_against_repo_root(
    tmp_path: Path,
) -> None:
    """Paths starting with '/' resolve against --repo-root, not the file dir."""
    site = tmp_path / "site"
    html = _write(
        site / "sub" / "page.html",
        '<html><body><link rel="stylesheet" href="/static/css/base.css"></body></html>',
    )
    target = _touch(site / "static" / "css" / "base.css")

    report = tmp_path / "report1.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["broken_refs"] == 0

    # Now remove the target and re-run; auditor must report the same resolved path.
    target.unlink()
    report2 = tmp_path / "report2.json"
    result2 = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report2),
    )
    assert result2.returncode == 2
    payload2 = json.loads(report2.read_text(encoding="utf-8"))
    broken = payload2["audited"][0]["broken"]
    assert len(broken) == 1
    assert Path(broken[0]["resolved_path"]) == site / "static" / "css" / "base.css"
    assert broken[0]["original_href"] == "/static/css/base.css"


def test_structural_audit_video_source_src_is_inspected(tmp_path: Path) -> None:
    """`<source src=...>` inside `<video>` is part of the inspected ref set."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><video><source src="missing.mp4"></video></body></html>',
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 2, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    broken = payload["audited"][0]["broken"]
    assert len(broken) == 1
    assert broken[0]["tag"] == "source"
    assert broken[0]["attribute"] == "src"
    assert broken[0]["original_href"] == "missing.mp4"


def test_structural_audit_allow_prefix_skips_on_disk_check(tmp_path: Path) -> None:
    """Default and custom allow-prefix URLs land in `absolute`, never `broken`."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        """
        <html><body>
          <a href="https://huggingface.co/datasets/foo/bar/resolve/main/x.mp4">hf</a>
          <a href="custom://something/here">custom</a>
        </body></html>
        """,
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--allow-prefix", "custom://",
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    entry = payload["audited"][0]
    assert entry["broken"] == []
    abs_urls = entry["absolute"]
    assert any("huggingface.co" in u for u in abs_urls)
    assert any(u.startswith("custom://") for u in abs_urls)


def test_audit_html_file_python_api_returns_structured_result(tmp_path: Path) -> None:
    """Direct Python API yields a populated dataclass with expected fields."""
    from tools.site_audit import (
        DEFAULT_ALLOW_PREFIXES,
        STRUCTURAL_REF_ATTRIBUTES,
        audit_html_file,
    )

    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        """
        <html><body>
          <a href="exists/">ok</a>
          <a href="ghost/missing.txt">bad</a>
          <a href="https://example.com">ext</a>
          <a href="#frag">frag</a>
        </body></html>
        """,
    )
    _touch(site / "exists" / "index.html")

    result = audit_html_file(html, repo_root=site)
    # Required attributes per the contract.
    assert Path(result.file) == html
    assert result.total_refs == 4
    assert result.broken_refs == 1
    assert len(result.broken) == 1
    bad = result.broken[0]
    assert bad.original_href == "ghost/missing.txt"
    assert Path(bad.resolved_path) == site / "ghost" / "missing.txt"
    assert bad.tag == "a"
    assert bad.attribute == "href"
    assert any("example.com" in u for u in result.absolute)
    assert any("frag" in f for f in result.fragments)

    # Default allow prefixes include the documented schemes.
    for prefix in ("http://", "https://", "data:", "mailto:", "javascript:", "#"):
        assert prefix in DEFAULT_ALLOW_PREFIXES

    # Inspected (tag, attribute) pairs include the documented minimum set.
    must_have = {
        ("a", "href"), ("link", "href"), ("script", "src"), ("img", "src"),
        ("source", "src"), ("video", "src"), ("audio", "src"), ("iframe", "src"),
    }
    assert must_have.issubset(set(STRUCTURAL_REF_ATTRIBUTES))


def test_structural_audit_no_playwright_at_import() -> None:
    """Importing the structural auditor must not pull in Playwright."""
    code = (
        "import sys\n"
        "import tools.site_audit\n"
        "import tools.site_audit.structural_audit\n"
        "assert 'playwright' not in sys.modules, sorted(m for m in sys.modules if 'play' in m)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_structural_audit_writes_json_report(tmp_path: Path) -> None:
    """The --report JSON has the documented top-level + per-file shape."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><a href="exists/">ok</a></body></html>',
    )
    _touch(site / "exists" / "index.html")
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert set(payload.keys()) >= {"audited", "summary"}
    summary = payload["summary"]
    assert set(summary.keys()) >= {"total_refs", "broken_refs", "files_audited"}
    assert summary["files_audited"] == 1
    assert isinstance(payload["audited"], list)
    entry = payload["audited"][0]
    assert set(entry.keys()) >= {"file", "broken", "absolute", "fragments"}
