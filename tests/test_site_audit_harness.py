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
# ``error`` was added in the Round 1 hardening pass: it is ``None`` for
# successful captures and a short ``"<ExcClass>: <msg>"`` string for
# per-URL Playwright failures. The field is always present on the
# dataclass so downstream tooling can rely on a stable shape.
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
    "error",
    # Geometry fields stamped from the live DOM after networkidle. They
    # are ``None`` in dry-run and on geometry-eval failure but the keys
    # are always present in fresh JSON output. (Existing on-disk
    # records.json files from earlier rounds may lack them — the schema-
    # parity assertions below only apply to records emitted by the
    # current run_audit binary.)
    "body_scroll_height",
    "main_scroll_height",
    "chrome_height",
    "main_non_empty",
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


# ---------------------------------------------------------------------------
# Code-quality follow-up tests (correctness gaps surfaced by review of the
# initial structural-auditor commit).
# ---------------------------------------------------------------------------


def test_structural_audit_protocol_relative_url_treated_as_absolute(
    tmp_path: Path,
) -> None:
    """`<img src="//cdn.example.com/x.js">` is absolute, not a broken local path.

    `urlsplit("//cdn...")` yields a non-empty netloc with an empty scheme.
    Such hrefs must never trigger an on-disk lookup; they belong on the
    ``absolute`` list.
    """
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body>'
        '<a href="//cdn.example.com/x.js">protocol-relative</a>'
        '</body></html>',
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    entry = payload["audited"][0]
    assert entry["broken"] == []
    assert any("cdn.example.com" in u for u in entry["absolute"])


def test_structural_audit_empty_allow_prefix_rejected(tmp_path: Path) -> None:
    """`--allow-prefix ""` must be rejected so the auditor cannot be silenced.

    An empty string would make `startswith("") == True` for every href and
    classify everything as absolute, neutering the auditor.
    """
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><a href="missing.txt">x</a></body></html>',
    )
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--allow-prefix", "",
    )
    assert result.returncode != 0, (
        f"empty --allow-prefix must not exit 0; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    combined = (result.stderr + result.stdout).lower()
    assert "allow-prefix" in combined or "allow_prefix" in combined or "empty" in combined, (
        f"stderr should explain why empty --allow-prefix is rejected; got:\n{result.stderr}"
    )


def test_structural_audit_path_escape_classified_as_broken(tmp_path: Path) -> None:
    """A relative href that resolves outside --repo-root must be reported broken.

    Even if the resolved file actually exists on disk, it would 404 in the
    rendered site because it is not part of the deployed tree. The auditor
    sandboxes resolution to the repo root.
    """
    site = tmp_path / "site"
    html = _write(
        site / "about" / "index.html",
        '<html><body><a href="../../outside.txt">escape</a></body></html>',
    )
    # Plant an actual file at the resolved escape target so the auditor
    # cannot rely on a missing-file check alone.
    outside = site.parent / "outside.txt"
    outside.write_text("escaped\n", encoding="utf-8")

    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 2, (
        f"path escape should be broken; got rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    broken = payload["audited"][0]["broken"]
    assert len(broken) == 1
    assert broken[0]["original_href"] == "../../outside.txt"


def test_structural_audit_percent_encoded_path_decoded(tmp_path: Path) -> None:
    """`<img src="my%20pic.png">` must resolve to the on-disk file `my pic.png`."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><img src="my%20pic.png"></body></html>',
    )
    _touch(site / "my pic.png")
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


def test_structural_audit_standalone_source_src_is_inspected(
    tmp_path: Path,
) -> None:
    """A `<source src=...>` outside `<picture>`/`<video>`/`<audio>` is still inspected."""
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body><source src="missing-standalone.mp4"></body></html>',
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
    assert broken[0]["original_href"] == "missing-standalone.mp4"


def test_structural_audit_default_allow_prefixes_route_correctly(
    tmp_path: Path,
) -> None:
    """Each of the 6 default prefixes routes to absolute or fragments, never broken.

    Run with NO `--allow-prefix` flag so this exercises the defaults baked
    into ``DEFAULT_ALLOW_PREFIXES``.
    """
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        """
        <html><body>
          <a href="http://example.com/a">http</a>
          <a href="https://example.com/b">https</a>
          <a href="data:text/plain,hello">data</a>
          <a href="mailto:foo@example.com">mailto</a>
          <a href="javascript:void(0)">js</a>
          <a href="#x">frag</a>
        </body></html>
        """,
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    entry = payload["audited"][0]
    assert entry["broken"] == []
    # http, https, data, mailto, javascript -> absolute (5 entries)
    assert len(entry["absolute"]) >= 5
    # #x -> fragments
    assert len(entry["fragments"]) >= 1
    assert any(f == "#x" for f in entry["fragments"])


def test_structural_audit_query_only_href_treated_as_fragment(
    tmp_path: Path,
) -> None:
    """`<a href="?foo=1">` and `<a href="#section">` route to fragments, never broken.

    A query-only or fragment-only href is a self-reference; it should never
    trigger an on-disk lookup against the HTML's parent directory.
    """
    site = tmp_path / "site"
    html = _write(
        site / "index.html",
        '<html><body>'
        '<a href="?foo=1">query</a>'
        '<a href="#section">frag</a>'
        '</body></html>',
    )
    report = tmp_path / "report.json"
    result = _run(
        str(STRUCTURAL_AUDIT_SCRIPT),
        str(html),
        "--repo-root", str(site),
        "--report", str(report),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    entry = payload["audited"][0]
    assert entry["broken"] == []
    # Both refs land in fragments.
    assert len(entry["fragments"]) >= 2
    assert any("?foo=1" in f for f in entry["fragments"])
    assert any(f == "#section" for f in entry["fragments"])


# ---------------------------------------------------------------------------
# Round 1 hardening: per-URL error isolation, free-port race fix,
# stale-PNG cleanup. Tests below exercise the contracts added in this round.
# ---------------------------------------------------------------------------


def test_run_audit_emits_record_per_url_even_on_capture_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-URL Playwright failure does not abort the rest of the matrix.

    The driver must:
      * exit 0 (per-URL failures are not run-fatal),
      * write one record per URL (3 in this test),
      * stamp ``error`` on the failing record with a non-empty string,
      * leave ``error`` as ``None`` on successful records,
      * remove the ``records.json.tmp`` staging file after the atomic
        replace at the end of the run.
    """
    import importlib

    run_audit_module = importlib.import_module("tools.site_audit.run_audit")

    out_dir = tmp_path / "artifacts"
    urls_path = _write_urls(tmp_path, ["/", "/about/", "/leaderboard/"])

    call_log: list[str] = []

    def fake_capture_one(url, *, prefix, target, viewport, out_dir):
        call_log.append(url)
        if url == "/about/":
            raise RuntimeError("boom: simulated goto failure")
        from tools.site_audit import AuditRecord

        return AuditRecord(
            url=url,
            prefixed_url=f"{prefix.rstrip('/')}{url}",
            target=target,
            final_url=f"{prefix.rstrip('/')}{url}",
            http_status=200,
            viewport=viewport,
            console_error_count=0,
            failed_request_count=0,
            screenshot_path=str(out_dir / "fake.png"),
            console_errors=[],
            failed_requests=[],
        )

    # Skip the real HTTP server and Playwright entirely: we patch the
    # serve helper to a no-op context manager and the per-URL capture
    # helper to the fake above.
    from contextlib import contextmanager

    @contextmanager
    def fake_serve():
        yield "http://127.0.0.1:0"

    monkeypatch.setattr(run_audit_module, "_serve_repo_root", fake_serve)
    monkeypatch.setattr(run_audit_module, "_capture_one_url", fake_capture_one)

    rc = run_audit_module.run_audit([
        "--target", "local",
        "--urls", str(urls_path),
        "--out", str(out_dir),
    ])
    assert rc == 0
    assert call_log == ["/", "/about/", "/leaderboard/"]

    records_path = out_dir / "records.json"
    assert records_path.is_file()
    records = json.loads(records_path.read_text(encoding="utf-8"))
    assert len(records) == 3

    by_url = {r["url"]: r for r in records}
    assert by_url["/"]["error"] is None
    assert by_url["/leaderboard/"]["error"] is None
    err = by_url["/about/"]["error"]
    assert isinstance(err, str) and err, "failing record must carry a non-empty error string"
    assert "RuntimeError" in err
    assert "boom" in err

    # Atomic-write staging file must be cleaned up after the run.
    assert not (out_dir / "records.json.tmp").exists()


def test_run_audit_dry_run_records_have_error_field_none(tmp_path: Path) -> None:
    """Dry-run records always include ``error`` and the value is exactly None."""
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
    for rec in records:
        assert "error" in rec, "error field must be present (not absent)"
        assert rec["error"] is None


def test_run_audit_local_drops_find_free_port_helper() -> None:
    """The free-port race is closed by binding port 0 directly.

    This test pins the implementation choice: ``_find_free_port`` is
    removed so callers cannot reintroduce the race. We assert the
    symbol is gone from the module namespace.
    """
    import importlib

    run_audit_module = importlib.import_module("tools.site_audit.run_audit")
    # The helper is removed; bind happens directly via ThreadingHTTPServer
    # with port 0. See _serve_repo_root.
    assert not hasattr(run_audit_module, "_find_free_port"), (
        "_find_free_port must be removed; bind port 0 directly via "
        "ThreadingHTTPServer to avoid the close-then-rebind race."
    )


def test_run_audit_cleans_stale_pngs_in_out_dir(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A rerun deletes top-level *.png files from --out before capturing.

    Non-PNG files (logs, sub-directories) must be preserved.
    """
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    (out_dir / "old1.png").write_bytes(b"old1")
    (out_dir / "old2.png").write_bytes(b"old2")
    (out_dir / "keep.txt").write_bytes(b"keep me")
    sub = out_dir / "logs"
    sub.mkdir()
    (sub / "nested.png").write_bytes(b"nested")  # below top level: must survive

    urls = _write_urls(tmp_path, ["/"])
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "fork",
        "--urls", str(urls),
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr

    assert not (out_dir / "old1.png").exists(), "stale top-level *.png must be removed"
    assert not (out_dir / "old2.png").exists(), "stale top-level *.png must be removed"
    assert (out_dir / "keep.txt").exists(), "non-PNG files must be preserved"
    assert (sub / "nested.png").exists(), "nested *.png must NOT be touched"

    # Stderr advertises the cleanup with a count.
    assert "cleaned" in result.stderr.lower()
    assert "2" in result.stderr, (
        f"stderr should mention the cleaned count; got: {result.stderr!r}"
    )


def test_run_audit_partial_records_json_written_after_each_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """records.json grows monotonically as each URL completes.

    A mid-run abort must therefore leave a usable evidence file on disk.
    We verify this by reading records.json from inside the per-URL
    capture stub: each call sees one more record than the last.
    """
    import importlib

    run_audit_module = importlib.import_module("tools.site_audit.run_audit")

    out_dir = tmp_path / "artifacts"
    urls_path = _write_urls(tmp_path, ["/", "/about/", "/leaderboard/", "/contact/"])
    records_path = out_dir / "records.json"

    seen_counts: list[int] = []

    def fake_capture_one(url, *, prefix, target, viewport, out_dir):
        # Read what's on disk BEFORE this call's record is appended.
        if records_path.is_file():
            current = json.loads(records_path.read_text(encoding="utf-8"))
            seen_counts.append(len(current))
        else:
            seen_counts.append(0)

        from tools.site_audit import AuditRecord

        return AuditRecord(
            url=url,
            prefixed_url=f"{prefix.rstrip('/')}{url}",
            target=target,
            final_url=f"{prefix.rstrip('/')}{url}",
            http_status=200,
            viewport=viewport,
            console_error_count=0,
            failed_request_count=0,
            screenshot_path=str(out_dir / "fake.png"),
            console_errors=[],
            failed_requests=[],
        )

    from contextlib import contextmanager

    @contextmanager
    def fake_serve():
        yield "http://127.0.0.1:0"

    monkeypatch.setattr(run_audit_module, "_serve_repo_root", fake_serve)
    monkeypatch.setattr(run_audit_module, "_capture_one_url", fake_capture_one)

    rc = run_audit_module.run_audit([
        "--target", "local",
        "--urls", str(urls_path),
        "--out", str(out_dir),
    ])
    assert rc == 0

    # Before each call, records.json contained 0, 1, 2, 3 records.
    assert seen_counts == [0, 1, 2, 3], seen_counts

    # End state: 4 records on disk, no leftover .tmp.
    final = json.loads(records_path.read_text(encoding="utf-8"))
    assert len(final) == 4
    assert not (out_dir / "records.json.tmp").exists()


# ---------------------------------------------------------------------------
# URL-set resolver: canonical 14-URL set sourced from
# snapshot/index/site_config.json plus the run_audit `--url-set repo` wiring.
# Both surfaces must agree on order, model coverage, and the deterministic
# populated-compare prompt selection so the audit driver can ditch
# hand-rolled urls.txt files.
# ---------------------------------------------------------------------------


from urllib.parse import parse_qs, urlsplit  # noqa: E402

from tests.conftest import PUBLISHED_MODEL_KEYS, SITE_CONFIG_PATH  # noqa: E402


_PUBLISHED_KEYS_SORTED = sorted(PUBLISHED_MODEL_KEYS)
_TOP_LEVEL_PREFIX = ("/", "/leaderboard/", "/videos/", "/about/", "/videos/compare/")


def _load_site_config() -> dict:
    if not SITE_CONFIG_PATH.is_file():
        pytest.skip(f"{SITE_CONFIG_PATH} missing; run build_snapshot.py first.")
    return json.loads(SITE_CONFIG_PATH.read_text(encoding="utf-8"))


def _make_synthetic_site_config(
    *,
    model_keys: list[str],
    prompt_specs: dict[str, dict[str, list[str]]],
) -> dict:
    """Build a minimal site_config dict for the resolver's negative tests.

    ``prompt_specs`` maps each prompt_id to a dict with ``per_model_videos``
    and ``per_model_scores`` keys whose values are the model-key lists that
    should be considered "covered". The resolver only inspects
    ``videos_index`` and ``prompts_index`` so we keep this fixture small.
    """
    return {
        "videos_index": {k: {} for k in model_keys},
        "prompts_index": {
            pid: {
                "per_model_videos": {m: {} for m in spec["per_model_videos"]},
                "per_model_scores": {m: {} for m in spec["per_model_scores"]},
            }
            for pid, spec in prompt_specs.items()
        },
    }


def test_resolve_repo_url_set_returns_exactly_14() -> None:
    """The canonical audit URL set is exactly 14 entries long."""
    from tools.site_audit.url_set import resolve_repo_url_set

    urls = resolve_repo_url_set()
    assert isinstance(urls, list)
    assert len(urls) == 14, urls


def test_resolve_repo_url_set_canonical_ordering() -> None:
    """First 5 entries are pinned; entry 6 is the populated compare query.

    Entries 7-14 are the per-model pages in alphabetical order.
    """
    from tools.site_audit.url_set import resolve_repo_url_set

    urls = resolve_repo_url_set()
    assert tuple(urls[:5]) == _TOP_LEVEL_PREFIX
    assert urls[5].startswith("/videos/compare/?prompt_id=")
    expected_model_urls = [f"/models/{key}/" for key in _PUBLISHED_KEYS_SORTED]
    assert urls[6:] == expected_model_urls


def test_resolve_repo_url_set_uses_valid_prompt_id() -> None:
    """The populated compare URL's prompt_id has 8/8 video and score coverage."""
    from tools.site_audit.url_set import resolve_repo_url_set

    urls = resolve_repo_url_set()
    populated = urls[5]
    qs = parse_qs(urlsplit(populated).query)
    assert "prompt_id" in qs and len(qs["prompt_id"]) == 1, populated
    pid = qs["prompt_id"][0]

    site_config = _load_site_config()
    assert pid in site_config["prompts_index"], pid
    entry = site_config["prompts_index"][pid]
    assert PUBLISHED_MODEL_KEYS.issubset(set(entry["per_model_videos"].keys()))
    assert PUBLISHED_MODEL_KEYS.issubset(set(entry["per_model_scores"].keys()))


def test_resolve_repo_url_set_alphabetical_first_pid() -> None:
    """Among prompts with full 8/8 coverage, the resolver picks the alphabetical-first pid."""
    from tools.site_audit.url_set import resolve_repo_url_set

    site_config = _load_site_config()
    fully_covered = sorted(
        pid
        for pid, entry in site_config["prompts_index"].items()
        if PUBLISHED_MODEL_KEYS.issubset(set(entry["per_model_videos"].keys()))
        and PUBLISHED_MODEL_KEYS.issubset(set(entry["per_model_scores"].keys()))
    )
    assert fully_covered, "expected at least one fully-covered prompt in the live config"
    expected_pid = fully_covered[0]

    urls = resolve_repo_url_set()
    qs = parse_qs(urlsplit(urls[5]).query)
    assert qs["prompt_id"] == [expected_pid]


def test_resolve_repo_url_set_models_match_published_keys() -> None:
    """Entries 7-14 mirror sorted PUBLISHED_MODEL_KEYS exactly."""
    from tools.site_audit.url_set import resolve_repo_url_set

    urls = resolve_repo_url_set()
    model_urls = urls[6:]
    assert len(model_urls) == 8
    extracted = [u[len("/models/"):-1] for u in model_urls]
    assert extracted == _PUBLISHED_KEYS_SORTED


def test_resolve_repo_url_set_raises_when_no_full_coverage(tmp_path: Path) -> None:
    """If no prompt has 8/8 video AND score coverage, raise ValueError."""
    from tools.site_audit.url_set import resolve_repo_url_set

    keys = list(_PUBLISHED_KEYS_SORTED)
    # All prompts are missing one model on either videos or scores.
    short_videos = keys[:-1]  # 7 of 8
    short_scores = keys[1:]   # 7 of 8 (different gap)
    cfg = _make_synthetic_site_config(
        model_keys=keys,
        prompt_specs={
            "p1": {"per_model_videos": short_videos, "per_model_scores": keys},
            "p2": {"per_model_videos": keys, "per_model_scores": short_scores},
        },
    )
    cfg_path = tmp_path / "site_config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    with pytest.raises(ValueError, match="No prompt"):
        resolve_repo_url_set(cfg_path)


def test_resolve_repo_url_set_raises_when_videos_index_diverges(tmp_path: Path) -> None:
    """videos_index keys must equal PUBLISHED_MODEL_KEYS exactly."""
    from tools.site_audit.url_set import resolve_repo_url_set

    keys = list(_PUBLISHED_KEYS_SORTED) + ["impostor-model-9000"]
    cfg = _make_synthetic_site_config(
        model_keys=keys,
        prompt_specs={
            "p1": {"per_model_videos": keys, "per_model_scores": keys},
        },
    )
    cfg_path = tmp_path / "site_config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    with pytest.raises(ValueError):
        resolve_repo_url_set(cfg_path)

    # And the symmetric case: missing one of the canonical keys.
    keys_short = _PUBLISHED_KEYS_SORTED[:-1]
    cfg2 = _make_synthetic_site_config(
        model_keys=keys_short,
        prompt_specs={
            "p1": {
                "per_model_videos": keys_short,
                "per_model_scores": keys_short,
            },
        },
    )
    cfg2_path = tmp_path / "site_config2.json"
    cfg2_path.write_text(json.dumps(cfg2), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_repo_url_set(cfg2_path)


def test_run_audit_url_set_repo_emits_14_records(tmp_path: Path) -> None:
    """`--url-set repo --dry-run` produces records.json with the canonical 14 URLs."""
    from tools.site_audit.url_set import resolve_repo_url_set

    out_dir = tmp_path / "out"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "local",
        "--url-set", "repo",
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr + result.stdout

    records = json.loads((out_dir / "records.json").read_text(encoding="utf-8"))
    assert len(records) == 14
    expected = resolve_repo_url_set()
    assert [rec["url"] for rec in records] == expected


def test_run_audit_requires_urls_or_url_set(tmp_path: Path) -> None:
    """Without --urls or --url-set the CLI exits non-zero with a clear error."""
    out_dir = tmp_path / "out"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "local",
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode != 0
    combined = (result.stderr + result.stdout).lower()
    assert "--urls" in combined or "url-set" in combined or "url_set" in combined, combined


def test_run_audit_urls_and_url_set_mutually_exclusive(tmp_path: Path) -> None:
    """Passing both --urls and --url-set is rejected with a clear error."""
    urls_file = _write_urls(tmp_path, ["/"])
    out_dir = tmp_path / "out"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "local",
        "--urls", str(urls_file),
        "--url-set", "repo",
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode != 0
    combined = (result.stderr + result.stdout).lower()
    assert (
        "mutually exclusive" in combined
        or "not allowed with" in combined
        or "cannot" in combined
    ), combined


# ---------------------------------------------------------------------------
# Main-content geometry fields. Captured from the live DOM after
# ``wait_until="networkidle"`` and stamped onto every record emitted by the
# current run_audit binary. Dry-run keeps all four fields ``None``; the real
# capture path computes them from a single ``page.evaluate(...)`` call. A
# geometry-eval failure must not abort the per-URL capture — the four fields
# go to ``None`` and a short note lands in the existing ``error`` field.
# ---------------------------------------------------------------------------


def test_audit_record_has_geometry_fields_with_dry_run_defaults_none(
    tmp_path: Path,
) -> None:
    """Dry-run records carry all four geometry fields with value ``None``."""
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

    records = json.loads((out_dir / "records.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    for rec in records:
        for key in (
            "body_scroll_height",
            "main_scroll_height",
            "chrome_height",
            "main_non_empty",
        ):
            assert key in rec, f"dry-run record missing geometry key {key!r}"
            assert rec[key] is None, (
                f"dry-run record {rec['url']!r} key {key!r} must be None, "
                f"got {rec[key]!r}"
            )


def test_capture_one_url_records_geometry_on_real_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI wires geometry numbers from ``_capture_one_url`` onto each record.

    We stub ``_capture_one_url`` to return geometry payloads that exercise
    both branches of ``main_non_empty``. The driver is responsible for
    forwarding the four fields through to records.json untouched.
    """
    import importlib

    run_audit_module = importlib.import_module("tools.site_audit.run_audit")

    out_dir = tmp_path / "artifacts"
    urls_path = _write_urls(tmp_path, ["/", "/about/"])

    # First URL: body > chrome and main > 0  -> main_non_empty True.
    # Second URL: main == 0                   -> main_non_empty False.
    geometry_by_url = {
        "/": {
            "body_scroll_height": 2400,
            "main_scroll_height": 1800,
            "chrome_height": 600,
            "main_non_empty": True,
        },
        "/about/": {
            "body_scroll_height": 600,
            "main_scroll_height": 0,
            "chrome_height": 600,
            "main_non_empty": False,
        },
    }

    def fake_capture_one(url, *, prefix, target, viewport, out_dir):
        from tools.site_audit import AuditRecord

        g = geometry_by_url[url]
        return AuditRecord(
            url=url,
            prefixed_url=f"{prefix.rstrip('/')}{url}",
            target=target,
            final_url=f"{prefix.rstrip('/')}{url}",
            http_status=200,
            viewport=viewport,
            console_error_count=0,
            failed_request_count=0,
            screenshot_path=str(out_dir / "fake.png"),
            console_errors=[],
            failed_requests=[],
            body_scroll_height=g["body_scroll_height"],
            main_scroll_height=g["main_scroll_height"],
            chrome_height=g["chrome_height"],
            main_non_empty=g["main_non_empty"],
        )

    from contextlib import contextmanager

    @contextmanager
    def fake_serve():
        yield "http://127.0.0.1:0"

    monkeypatch.setattr(run_audit_module, "_serve_repo_root", fake_serve)
    monkeypatch.setattr(run_audit_module, "_capture_one_url", fake_capture_one)

    rc = run_audit_module.run_audit([
        "--target", "local",
        "--urls", str(urls_path),
        "--out", str(out_dir),
    ])
    assert rc == 0

    records = json.loads((out_dir / "records.json").read_text(encoding="utf-8"))
    by_url = {r["url"]: r for r in records}
    for url, expected in geometry_by_url.items():
        rec = by_url[url]
        assert rec["body_scroll_height"] == expected["body_scroll_height"]
        assert rec["main_scroll_height"] == expected["main_scroll_height"]
        assert rec["chrome_height"] == expected["chrome_height"]
        assert rec["main_non_empty"] is expected["main_non_empty"]


def test_main_non_empty_computation() -> None:
    """Pure unit test on the (body, main, chrome) -> main_non_empty formula.

    The contract: True iff ``main_scroll_height > 0`` AND
    ``body_scroll_height > chrome_height``. ``None`` inputs flip the result
    to False so callers cannot read a positive signal off a record where
    geometry was not captured.
    """
    from tools.site_audit.run_audit import _compute_main_non_empty

    cases = [
        # (body, main, chrome, expected)
        (10, 5, 5, True),     # body > chrome AND main > 0
        (10, 0, 5, False),    # main == 0
        (5, 1, 10, False),    # body < chrome
        (0, 0, 0, False),     # all zero
        (1280, 800, 200, True),   # realistic full-page case
        (5, 5, 5, False),     # body == chrome (must be strict)
        (None, 5, 5, False),  # missing body
        (5, None, 5, False),  # missing main
        (5, 5, None, False),  # missing chrome
    ]
    for body, main, chrome, expected in cases:
        got = _compute_main_non_empty(body, main, chrome)
        assert got is expected, (
            f"_compute_main_non_empty({body!r}, {main!r}, {chrome!r}) "
            f"-> {got!r}, expected {expected!r}"
        )


def test_capture_geometry_failure_keeps_record_with_nones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A geometry-eval failure on one URL must not abort the run.

    The failing URL emits a record with ``body_scroll_height``,
    ``main_scroll_height``, ``chrome_height``, ``main_non_empty`` all set
    to ``None``. The other URLs continue to capture geometry normally.
    Round 1's per-URL error isolation contract still holds.
    """
    import importlib

    run_audit_module = importlib.import_module("tools.site_audit.run_audit")

    out_dir = tmp_path / "artifacts"
    urls_path = _write_urls(tmp_path, ["/", "/broken/", "/last/"])

    def fake_capture_one(url, *, prefix, target, viewport, out_dir):
        from tools.site_audit import AuditRecord

        if url == "/broken/":
            # Simulate the path where ``_evaluate_main_geometry`` raised:
            # the surrounding code stamps ``None`` for all four fields and
            # records a short note in ``error``.
            return AuditRecord(
                url=url,
                prefixed_url=f"{prefix.rstrip('/')}{url}",
                target=target,
                final_url=f"{prefix.rstrip('/')}{url}",
                http_status=200,
                viewport=viewport,
                console_error_count=0,
                failed_request_count=0,
                screenshot_path=str(out_dir / "fake.png"),
                console_errors=[],
                failed_requests=[],
                error="geometry: RuntimeError: simulated evaluate failure",
                body_scroll_height=None,
                main_scroll_height=None,
                chrome_height=None,
                main_non_empty=None,
            )
        return AuditRecord(
            url=url,
            prefixed_url=f"{prefix.rstrip('/')}{url}",
            target=target,
            final_url=f"{prefix.rstrip('/')}{url}",
            http_status=200,
            viewport=viewport,
            console_error_count=0,
            failed_request_count=0,
            screenshot_path=str(out_dir / "fake.png"),
            console_errors=[],
            failed_requests=[],
            body_scroll_height=1000,
            main_scroll_height=800,
            chrome_height=200,
            main_non_empty=True,
        )

    from contextlib import contextmanager

    @contextmanager
    def fake_serve():
        yield "http://127.0.0.1:0"

    monkeypatch.setattr(run_audit_module, "_serve_repo_root", fake_serve)
    monkeypatch.setattr(run_audit_module, "_capture_one_url", fake_capture_one)

    rc = run_audit_module.run_audit([
        "--target", "local",
        "--urls", str(urls_path),
        "--out", str(out_dir),
    ])
    assert rc == 0

    records = json.loads((out_dir / "records.json").read_text(encoding="utf-8"))
    assert len(records) == 3
    by_url = {r["url"]: r for r in records}

    broken = by_url["/broken/"]
    for key in (
        "body_scroll_height",
        "main_scroll_height",
        "chrome_height",
        "main_non_empty",
    ):
        assert broken[key] is None, (
            f"geometry-failure record key {key!r} must be None, got {broken[key]!r}"
        )
    assert isinstance(broken["error"], str) and "geometry" in broken["error"]

    for url in ("/", "/last/"):
        good = by_url[url]
        assert good["body_scroll_height"] == 1000
        assert good["main_scroll_height"] == 800
        assert good["chrome_height"] == 200
        assert good["main_non_empty"] is True
        assert good["error"] is None


def test_geometry_fields_serialize_in_records_json(tmp_path: Path) -> None:
    """Every entry in dry-run records.json carries the four geometry keys."""
    urls = _write_urls(tmp_path, ["/", "/about/", "/leaderboard/"])
    out_dir = tmp_path / "artifacts"
    result = _run(
        str(RUN_AUDIT_SCRIPT),
        "--target", "fork",
        "--urls", str(urls),
        "--out", str(out_dir),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr

    raw = (out_dir / "records.json").read_text(encoding="utf-8")
    records = json.loads(raw)
    assert len(records) == 3

    geom_keys = (
        "body_scroll_height",
        "main_scroll_height",
        "chrome_height",
        "main_non_empty",
    )
    for rec in records:
        for k in geom_keys:
            # JSON shape, not just dataclass shape: the key must literally
            # be present in the serialized object on disk.
            assert k in rec, (
                f"records.json entry for {rec['url']!r} missing geometry key {k!r}; "
                f"got keys {sorted(rec.keys())}"
            )
        # Dry-run leaves them all as JSON null.
        assert rec["body_scroll_height"] is None
        assert rec["main_scroll_height"] is None
        assert rec["chrome_height"] is None
        assert rec["main_non_empty"] is None


def test_audit_record_record_to_dict_includes_geometry_fields() -> None:
    """``record_to_dict`` exposes geometry keys on the serialized output."""
    from tools.site_audit import AuditRecord, record_to_dict

    rec = AuditRecord(
        url="/",
        prefixed_url="http://127.0.0.1:0/",
        target="local",
        final_url=None,
        http_status=None,
        viewport="1280x800",
        console_error_count=0,
        failed_request_count=0,
        screenshot_path="root.png",
        console_errors=[],
        failed_requests=[],
        body_scroll_height=1024,
        main_scroll_height=768,
        chrome_height=128,
        main_non_empty=True,
    )
    d = record_to_dict(rec)
    assert d["body_scroll_height"] == 1024
    assert d["main_scroll_height"] == 768
    assert d["chrome_height"] == 128
    assert d["main_non_empty"] is True


# ---------------------------------------------------------------------------
# Round 5 regression: directory-style links require a directory index file
# (index.html or index.htm) to actually be reachable. A bare directory
# without an index document 404s on GitHub Pages and python http.server.
# ---------------------------------------------------------------------------


class TestStructuralAuditDirectoryIndex:
    def _write_html(self, root, rel, body):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def _audit(self, root, html):
        from tools.site_audit import audit_html_file

        return audit_html_file(html, repo_root=root)

    def test_directory_link_without_index_html_is_broken(self, tmp_path):
        html = self._write_html(
            tmp_path,
            "index.html",
            "<!doctype html><html><body><a href='about/'>About</a></body></html>",
        )
        # Create the directory but NOT index.html inside it.
        (tmp_path / "about").mkdir()

        result = self._audit(tmp_path, html)

        assert len(result.broken) == 1, (
            f"expected 1 broken ref for empty directory, got: {result.broken}"
        )
        broken = result.broken[0]
        assert broken.original_href == "about/"
        assert broken.tag == "a"
        assert broken.attribute == "href"
        # The resolved path names the would-be index file so the defect
        # report tells the operator which file the build pipeline should
        # produce.
        assert broken.resolved_path == str((tmp_path / "about" / "index.html").resolve())

    def test_directory_link_with_index_html_passes(self, tmp_path):
        html = self._write_html(
            tmp_path,
            "index.html",
            "<!doctype html><html><body><a href='about/'>About</a></body></html>",
        )
        self._write_html(tmp_path, "about/index.html", "<html>about</html>")

        result = self._audit(tmp_path, html)

        assert result.broken == []

    def test_directory_link_with_index_htm_fallback_passes(self, tmp_path):
        html = self._write_html(
            tmp_path,
            "index.html",
            "<!doctype html><html><body><a href='about/'>About</a></body></html>",
        )
        self._write_html(tmp_path, "about/index.htm", "<html>about</html>")

        result = self._audit(tmp_path, html)

        assert result.broken == []

    def test_root_relative_directory_link_without_index_html_is_broken(self, tmp_path):
        # Sibling regression for root-relative directory hrefs that resolve
        # under repo_root rather than the html file's parent.
        sub = tmp_path / "models" / "veo-3.1"
        sub.mkdir(parents=True)
        html = self._write_html(
            sub,
            "index.html",
            "<!doctype html><html><body><a href='/models/cosmos-predict2.5-14b/'>Other</a></body></html>",
        )
        (tmp_path / "models" / "cosmos-predict2.5-14b").mkdir()
        # Deliberately do NOT create the sibling's index.html.

        result = self._audit(tmp_path, html)

        assert len(result.broken) == 1
        assert result.broken[0].original_href == "/models/cosmos-predict2.5-14b/"
        expected = (tmp_path / "models" / "cosmos-predict2.5-14b" / "index.html").resolve()
        assert result.broken[0].resolved_path == str(expected)


# ---------------------------------------------------------------------------
# Round 5 regression: --url-set repo must work regardless of cwd. The
# default site_config path is anchored to __file__ so a launch from /tmp
# (or any other directory) does not crash with FileNotFoundError.
# ---------------------------------------------------------------------------


class TestUrlSetDefaultPath:
    def test_default_path_is_absolute_and_anchored_to_repo_root(self):
        from tools.site_audit.url_set import DEFAULT_SITE_CONFIG_PATH

        assert DEFAULT_SITE_CONFIG_PATH.is_absolute(), DEFAULT_SITE_CONFIG_PATH
        assert DEFAULT_SITE_CONFIG_PATH.name == "site_config.json"
        assert DEFAULT_SITE_CONFIG_PATH.parent.name == "index"
        assert DEFAULT_SITE_CONFIG_PATH.parent.parent.name == "snapshot"

    def test_resolve_repo_url_set_works_from_outside_repo(self, tmp_path, monkeypatch):
        from tools.site_audit.url_set import resolve_repo_url_set

        # Switch cwd to an unrelated directory; the resolver must still
        # find site_config.json via the __file__-anchored default.
        monkeypatch.chdir(tmp_path)
        urls = resolve_repo_url_set()

        assert len(urls) == 14
        assert urls[0] == "/"
        assert urls[5].startswith("/videos/compare/?prompt_id=")
