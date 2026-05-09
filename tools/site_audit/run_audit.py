#!/usr/bin/env python3
"""Capture per-page runtime audit records for phyground.github.io.

Two source modes are supported:

  * ``--target local``  serves the repo root over a localhost HTTP server
    on a free port and prefixes each URL in ``--urls`` with that origin.
  * ``--target fork``   prefixes each URL with the user-fork origin
    ``https://lukelin-web.github.io/phyground.github.io``.

For every URL the driver records console errors, failed network requests,
the post-redirect URL, the main-document HTTP status, and writes a
viewport-sized PNG screenshot. Records are emitted as a single JSON array
to ``<out>/records.json`` and screenshots are written next to it.

The Playwright dependency is imported lazily so ``--help`` and
``--dry-run`` work without a browser installed. ``--dry-run`` skips the
HTTP server and Playwright entirely and emits a skeleton record per URL,
which the test suite uses to lock in the CLI surface and schema.

The URL list comes from one of two mutually-exclusive sources: ``--urls
path/to/urls.txt`` for ad-hoc lists, or ``--url-set repo`` for the
canonical 14-URL audit set resolved from
``snapshot/index/site_config.json`` (preferred for round-level audits).

Usage::

    python tools/site_audit/run_audit.py --target local --url-set repo
    python tools/site_audit/run_audit.py --target local --urls urls.txt
    python tools/site_audit/run_audit.py --target fork --urls urls.txt --dry-run
    python -m tools.site_audit.run_audit --help
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

# Support both `python tools/site_audit/run_audit.py` (no parent package)
# and `python -m tools.site_audit.run_audit` (package import).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.site_audit import AuditRecord, record_to_dict
else:
    from . import AuditRecord, record_to_dict


REPO_ROOT = Path(__file__).resolve().parents[2]
FORK_PREFIX = "https://lukelin-web.github.io/phyground.github.io"
DEFAULT_VIEWPORT = "1280x800"
VALID_TARGETS = ("local", "fork")
VIEWPORT_RE = re.compile(r"^(\d+)x(\d+)$")


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_audit",
        description=(
            "Capture per-page runtime audit records (console errors, failed "
            "requests, screenshots) for the local rebuild or the user fork. "
            "Use --dry-run to emit a skeleton without launching a browser."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=VALID_TARGETS,
        help="Source to audit: 'local' (serve the repo over localhost) or "
             "'fork' (the published user-fork URL).",
    )
    # --urls and --url-set are mutually exclusive; exactly one must be
    # provided. We use a mutually-exclusive group with required=True so
    # argparse renders a clear error on both the missing-both and
    # both-given misuse paths.
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--urls",
        type=Path,
        help="Path to a text file with one relative URL per line (e.g. '/' "
             "or '/about/'). Blank lines and lines starting with '#' are "
             "ignored. Mutually exclusive with --url-set.",
    )
    source_group.add_argument(
        "--url-set",
        choices=("repo",),
        help="Use a built-in canonical URL set instead of --urls. "
             "'repo' resolves the 14-URL audit set from "
             "snapshot/index/site_config.json (4 top-level pages + the "
             "placeholder and populated compare states + the 8 per-model "
             "pages). Mutually exclusive with --urls.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory for records.json + screenshots. Default: "
             ".audit_artifacts/<round>/<target>/ relative to the repo root, "
             "with <round> defaulting to 'current'.",
    )
    parser.add_argument(
        "--viewport",
        default=DEFAULT_VIEWPORT,
        help=f"Browser viewport, e.g. '{DEFAULT_VIEWPORT}' (default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the HTTP server and Playwright; emit a skeleton record "
             "per URL with placeholder values. Used by the test suite to "
             "exercise the CLI surface without a browser.",
    )
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_urls(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"--urls file not found: {path}")
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    if not out:
        raise SystemExit(f"--urls file is empty: {path}")
    return out


def _parse_viewport(spec: str) -> tuple[int, int]:
    m = VIEWPORT_RE.match(spec)
    if not m:
        raise SystemExit(
            f"--viewport must look like '1280x800', got: {spec!r}"
        )
    return int(m.group(1)), int(m.group(2))


def _join_url(prefix: str, relative_url: str) -> str:
    """Join a prefix with a relative URL using the contracted semantics.

    Matches `prefix.rstrip("/") + url` so callers get exactly one slash
    at the boundary regardless of how the prefix was supplied.
    """
    return prefix.rstrip("/") + relative_url


def _slugify(relative_url: str) -> str:
    """Map a relative URL to a filesystem-safe screenshot stem.

    '/' becomes 'root'; other paths replace '/' with '_' and strip
    leading/trailing separators so the on-disk name is human-readable.
    """
    if relative_url in ("/", ""):
        return "root"
    s = relative_url.strip("/").replace("/", "_")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s)
    return s or "root"


def _default_out_dir(target: str) -> Path:
    return REPO_ROOT / ".audit_artifacts" / "current" / target


@contextmanager
def _serve_repo_root() -> Iterator[str]:
    """Serve REPO_ROOT over a localhost HTTP server; yield the origin.

    Binds on port 0 directly via ``ThreadingHTTPServer`` and reads the
    actual port from ``server.server_address``. The previous implementation
    asked the OS for a free port via a throwaway socket and then re-bound
    a few microseconds later, which left a window for another process to
    grab the port. Binding once closes that race.
    """
    handler_root = str(REPO_ROOT)

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=handler_root, **kw)

        def log_message(self, format, *args):  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Dry-run + real capture
# ---------------------------------------------------------------------------


def _skeleton_record(
    *,
    url: str,
    prefixed_url: str,
    target: str,
    viewport: str,
    out_dir: Path,
) -> AuditRecord:
    screenshot = out_dir / f"{_slugify(url)}.png"
    return AuditRecord(
        url=url,
        prefixed_url=prefixed_url,
        target=target,
        final_url=None,
        http_status=None,
        viewport=viewport,
        console_error_count=0,
        failed_request_count=0,
        screenshot_path=str(screenshot),
        console_errors=[],
        failed_requests=[],
    )


_ERROR_TRUNCATE_CHARS = 500


# JS evaluated on the live page to capture deterministic main-content
# geometry. Returns three integer measurements; the Python wrapper computes
# ``main_non_empty`` from them. The selectors mirror the rendered chrome:
# ``<header>``, ``<footer>``, and a top-level ``<nav>`` that sits as a
# direct child of ``<body>`` (so a nav nested inside the header is not
# double-counted).
_GEOMETRY_JS = """
() => {
    const body = document.body;
    const main = document.querySelector('main');
    const header = document.querySelector('header');
    const footer = document.querySelector('footer');
    const standaloneNav = document.querySelector('body > nav');
    const headerH = header ? header.scrollHeight : 0;
    const footerH = footer ? footer.scrollHeight : 0;
    const navH = (standaloneNav && (!header || !header.contains(standaloneNav))) ? standaloneNav.scrollHeight : 0;
    return {
        body_scroll_height: body ? body.scrollHeight : 0,
        main_scroll_height: main ? main.scrollHeight : 0,
        chrome_height: headerH + footerH + navH,
    };
}
"""


def _compute_main_non_empty(
    body_scroll_height: int | None,
    main_scroll_height: int | None,
    chrome_height: int | None,
) -> bool:
    """Pure helper: return True iff the page has non-empty main content.

    The contract from the runtime audit plan is:
      * ``main_scroll_height`` must be strictly positive (a ``<main>`` with
        zero scroll height is empty), AND
      * ``body_scroll_height`` must strictly exceed ``chrome_height`` (the
        page has at least one pixel of content beyond header/footer/nav).

    Any ``None`` input flips the result to ``False`` so callers cannot
    accidentally read a positive signal off a record where geometry was
    not captured.
    """
    if body_scroll_height is None or main_scroll_height is None or chrome_height is None:
        return False
    return main_scroll_height > 0 and body_scroll_height > chrome_height


def _evaluate_main_geometry(page) -> dict[str, int]:
    """Run the geometry JS against ``page`` and return the three integers.

    Split out as a private helper so tests can monkeypatch the inner
    Playwright call without faking the entire ``_capture_one_url`` body.
    The returned dict has exactly the three keys ``body_scroll_height``,
    ``main_scroll_height``, ``chrome_height``; the wrapper composes
    ``main_non_empty`` afterwards.
    """
    raw = page.evaluate(_GEOMETRY_JS)
    return {
        "body_scroll_height": int(raw["body_scroll_height"]),
        "main_scroll_height": int(raw["main_scroll_height"]),
        "chrome_height": int(raw["chrome_height"]),
    }


def _format_capture_error(exc: BaseException) -> str:
    """Produce a stable ``"<ExcClass>: <message>"`` string capped at 500 chars.

    The exact wording is part of the public records contract, so keep this
    helper deterministic: class name + colon + repr-stripped message,
    truncated to ``_ERROR_TRUNCATE_CHARS`` so a giant traceback string
    cannot bloat ``records.json``.
    """
    msg = f"{type(exc).__name__}: {exc}"
    if len(msg) > _ERROR_TRUNCATE_CHARS:
        msg = msg[:_ERROR_TRUNCATE_CHARS]
    return msg


def _write_records_atomic(records_path: Path, records: list[AuditRecord]) -> None:
    """Atomically rewrite ``records.json`` so a crash leaves prior contents intact.

    Writes to ``records.json.tmp`` next to the target and then ``os.replace``s
    it into place; downstream readers therefore see either the previous
    snapshot or the new one, never a half-written file. Called after every
    per-URL completion (success or failure) so a mid-run abort still leaves
    usable evidence on disk.
    """
    tmp = records_path.with_name(records_path.name + ".tmp")
    payload = json.dumps(
        [record_to_dict(r) for r in records], indent=2, sort_keys=False
    ) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, records_path)


def _capture_one_url(
    url: str,
    *,
    prefix: str,
    target: str,
    viewport: str,
    out_dir: Path,
) -> AuditRecord:
    """Run Playwright against a single URL and return one ``AuditRecord``.

    Extracted from the per-URL body so the test suite can ``monkeypatch``
    this helper to simulate failures without touching the public CLI
    surface. Lazily imports Playwright; importing this module does not
    pull the browser dependency in.
    """
    # Lazy import so --help / --dry-run work without playwright installed.
    from playwright.sync_api import sync_playwright  # type: ignore

    width, height = _parse_viewport(viewport)
    prefixed = _join_url(prefix, url)

    console_errors: list[dict[str, object]] = []
    failed_requests: list[dict[str, object]] = []

    def _on_console(msg, _bucket=console_errors):
        if msg.type == "error":
            loc = msg.location or {}
            _bucket.append({
                "text": msg.text,
                "location": {
                    "url": loc.get("url", ""),
                    "lineNumber": loc.get("lineNumber", 0),
                    "columnNumber": loc.get("columnNumber", 0),
                },
            })

    def _on_requestfailed(req, _bucket=failed_requests):
        _bucket.append({
            "url": req.url,
            "status": None,
            "failure": (req.failure or ""),
        })

    def _on_response(resp, _bucket=failed_requests):
        if resp.status >= 400:
            _bucket.append({
                "url": resp.url,
                "status": resp.status,
                "failure": "",
            })

    geometry: dict[str, int] | None = None
    geometry_error: str | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.on("console", _on_console)
            page.on("requestfailed", _on_requestfailed)
            page.on("response", _on_response)

            response = page.goto(prefixed, wait_until="networkidle")
            http_status = response.status if response is not None else None
            final_url = page.url

            screenshot_path = out_dir / f"{_slugify(url)}.png"
            page.screenshot(path=str(screenshot_path), full_page=False)

            # Capture deterministic main-content geometry. A failure in the
            # evaluate call must not abort the per-URL capture: we record
            # the four geometry fields as ``None`` and append a short note
            # to ``error`` so downstream tooling can see what happened.
            try:
                geometry = _evaluate_main_geometry(page)
            except Exception as geom_exc:  # noqa: BLE001 - geometry isolation by design
                geometry = None
                geometry_error = (
                    "geometry: " + _format_capture_error(geom_exc)
                )

            page.close()
        finally:
            browser.close()

    if geometry is not None:
        body_scroll_height: int | None = geometry["body_scroll_height"]
        main_scroll_height: int | None = geometry["main_scroll_height"]
        chrome_height: int | None = geometry["chrome_height"]
        main_non_empty: bool | None = _compute_main_non_empty(
            body_scroll_height, main_scroll_height, chrome_height
        )
    else:
        body_scroll_height = None
        main_scroll_height = None
        chrome_height = None
        main_non_empty = None

    return AuditRecord(
        url=url,
        prefixed_url=prefixed,
        target=target,
        final_url=final_url,
        http_status=http_status,
        viewport=viewport,
        console_error_count=len(console_errors),
        failed_request_count=len(failed_requests),
        screenshot_path=str(screenshot_path),
        console_errors=console_errors,
        failed_requests=failed_requests,
        error=geometry_error,
        body_scroll_height=body_scroll_height,
        main_scroll_height=main_scroll_height,
        chrome_height=chrome_height,
        main_non_empty=main_non_empty,
    )


def _bootstrap_playwright_or_raise() -> None:
    """Validate that Playwright is importable AND a Chromium browser launches.

    Per-URL error isolation should NOT swallow environment-wide failures
    (Playwright not installed, Chromium not provisioned, missing system
    libs). Without this guard a missing browser stack raises the same
    exception on every URL, the per-URL ``try/except`` records 14 errors,
    and ``run_audit()`` exits 0 — telling CI the audit succeeded when no
    page was actually tested. This bootstrap check happens once before
    the per-URL loop; failures propagate as ``RuntimeError`` and
    surface as a non-zero exit.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run "
            "`pip install -r requirements-audit.txt` and "
            "`python -m playwright install chromium`."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                pass  # launch alone is enough to validate the browser stack
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 — surface any launch failure
        raise RuntimeError(
            f"Playwright browser launch failed during bootstrap. "
            f"Run `python -m playwright install chromium` and ensure system "
            f"dependencies are present. Underlying error: {exc}"
        ) from exc


def _capture_with_playwright(
    *,
    urls: list[str],
    prefix: str,
    target: str,
    viewport: str,
    out_dir: Path,
    records_path: Path,
) -> list[AuditRecord]:
    """Drive ``_capture_one_url`` over each URL with per-URL error isolation.

    A failing ``page.goto`` (or any other exception inside
    ``_capture_one_url``) no longer aborts the run: the failing URL gets
    a record stamped with ``error="<ExcClass>: <msg>"`` and the loop moves
    on. ``records.json`` is rewritten atomically after every URL so an
    external abort still leaves a usable evidence file.

    A bootstrap check (``_bootstrap_playwright_or_raise``) runs once
    before the per-URL loop. If Playwright isn't installed or the
    browser fails to launch, the check raises ``RuntimeError`` and we
    exit non-zero rather than producing a fake-success run of
    bootstrap-error records.
    """
    _bootstrap_playwright_or_raise()

    records: list[AuditRecord] = []

    for url in urls:
        prefixed = _join_url(prefix, url)
        try:
            record = _capture_one_url(
                url,
                prefix=prefix,
                target=target,
                viewport=viewport,
                out_dir=out_dir,
            )
        except Exception as exc:  # noqa: BLE001 — per-URL isolation by design
            err_msg = _format_capture_error(exc)
            sys.stderr.write(
                f"[run_audit] capture failed for {url!r}: {err_msg}\n"
            )
            record = AuditRecord(
                url=url,
                prefixed_url=prefixed,
                target=target,
                final_url=None,
                http_status=None,
                viewport=viewport,
                console_error_count=0,
                failed_request_count=0,
                screenshot_path=str(out_dir / f"{_slugify(url)}.png"),
                console_errors=[],
                failed_requests=[],
                error=err_msg,
            )

        records.append(record)
        _write_records_atomic(records_path, records)

    return records


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _clean_stale_pngs(out_dir: Path) -> int:
    """Remove top-level ``*.png`` files from ``out_dir``; return the count.

    A rerun against the same ``--out`` would otherwise leave screenshots
    from a previous URL set lingering on disk and pollute the audit
    artifacts. We clean only the top level so subdirectories (e.g. logs)
    are preserved. Non-PNG files are never touched.
    """
    if not out_dir.exists():
        return 0
    removed = 0
    for entry in out_dir.iterdir():
        if entry.is_file() and entry.suffix == ".png":
            entry.unlink()
            removed += 1
    return removed


def run_audit(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate viewport early so even dry-run rejects malformed values.
    _parse_viewport(args.viewport)

    if args.url_set is not None:
        # The mutually-exclusive group guarantees args.urls is None here.
        # Lazy import keeps the resolver close to the call site so module
        # import remains import-light for tests.
        if __package__ in (None, ""):
            from tools.site_audit.url_set import resolve_repo_url_set
        else:
            from .url_set import resolve_repo_url_set
        urls = resolve_repo_url_set()
    else:
        urls = _read_urls(args.urls)
    out_dir: Path = args.out if args.out is not None else _default_out_dir(args.target)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wipe stale PNGs from a previous run so the artifacts directory only
    # ever reflects the current URL matrix. Non-PNG files (audit logs,
    # any sub-directories) survive.
    cleaned = _clean_stale_pngs(out_dir)
    sys.stderr.write(
        f"[run_audit] cleaned {cleaned} stale screenshot(s) from {out_dir}\n"
    )

    records_path = out_dir / "records.json"

    if args.dry_run:
        # Use a stable placeholder origin for local so prefixed_url still
        # matches the documented `http://127.0.0.1:<port>` shape without
        # binding a real port. Tests assert the prefix and suffix only.
        if args.target == "local":
            prefix = "http://127.0.0.1:0"
        else:
            prefix = FORK_PREFIX
        records = [
            _skeleton_record(
                url=url,
                prefixed_url=_join_url(prefix, url),
                target=args.target,
                viewport=args.viewport,
                out_dir=out_dir,
            )
            for url in urls
        ]
        _write_records_atomic(records_path, records)
    elif args.target == "local":
        with _serve_repo_root() as origin:
            records = _capture_with_playwright(
                urls=urls,
                prefix=origin,
                target="local",
                viewport=args.viewport,
                out_dir=out_dir,
                records_path=records_path,
            )
    else:
        records = _capture_with_playwright(
            urls=urls,
            prefix=FORK_PREFIX,
            target="fork",
            viewport=args.viewport,
            out_dir=out_dir,
            records_path=records_path,
        )

    sys.stdout.write(
        f"wrote {len(records)} record(s) to {records_path}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_audit())
