#!/usr/bin/env python3
"""Structural HTML/link auditor for phyground.github.io.

Walks one or more HTML files on disk, extracts every ``href`` / ``src``
reference from a known set of ``(tag, attribute)`` pairs, and verifies
that each *relative* reference resolves to an existing file under the
repo. Absolute URLs (``http://``, ``https://``, ``data:``, ``mailto:``,
``javascript:``) and same-document anchors (``#...``) are catalogued
separately and never trigger a disk check.

The auditor is intentionally pure-Python (``html.parser`` plus
``urllib.parse`` plus ``pathlib``) so importing this module never pulls
in Playwright or any third-party HTML parser.

Resolution rules
----------------

* A href starting with ``/`` resolves against ``--repo-root``.
* Any other relative href resolves against the directory of the HTML
  file that contains it.
* Query strings and fragments are stripped before the on-disk check
  (``foo.css?v=2`` and ``foo.css#section`` both resolve to ``foo.css``).
* An empty ``href=""`` is treated as a self-link and recorded under
  ``fragments``; it is never reported as broken.

JSON report
-----------

When invoked with ``--report <path>`` the CLI writes::

    {
      "audited": [
        {
          "file": "<absolute path to html file>",
          "broken": [
            {"original_href": "...", "resolved_path": "...",
             "tag": "...", "attribute": "..."},
            ...
          ],
          "absolute": ["https://example.com", ...],
          "fragments": ["#main", ...]
        },
        ...
      ],
      "summary": {
        "total_refs": <int>,
        "broken_refs": <int>,
        "files_audited": <int>
      }
    }

Exit codes
----------

* ``0`` — every relative reference resolves on disk.
* ``2`` — at least one relative reference is broken.

Usage::

    python tools/site_audit/structural_audit.py snapshot/index.html
    python -m tools.site_audit.structural_audit snapshot/about/index.html \\
        --repo-root . --report .audit_artifacts/structural.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

# Support both `python tools/site_audit/structural_audit.py` and
# `python -m tools.site_audit.structural_audit`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.site_audit import (
        DEFAULT_ALLOW_PREFIXES,
        STRUCTURAL_REF_ATTRIBUTES,
        BrokenRef,
        StructuralAuditResult,
    )
else:
    from . import (
        DEFAULT_ALLOW_PREFIXES,
        STRUCTURAL_REF_ATTRIBUTES,
        BrokenRef,
        StructuralAuditResult,
    )


REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


class _RefCollector(HTMLParser):
    """Collect (tag, attribute, value) triples for the auditor.

    Only the (tag, attribute) pairs in :data:`STRUCTURAL_REF_ATTRIBUTES`
    are kept. The parser tolerates malformed input (``convert_charrefs``
    is on; missing attributes are skipped) and never raises.
    """

    def __init__(self, watched: set[tuple[str, str]]) -> None:
        # convert_charrefs=True lets the parser ignore stray ``&`` etc.
        super().__init__(convert_charrefs=True)
        self._watched = watched
        self.refs: list[tuple[str, str, str]] = []

    # The parser yields lowercased tag names; attribute lookup is also
    # case-insensitive per the HTML spec, so we lowercase attribute
    # names before matching.
    def _emit(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for raw_name, value in attrs:
            if value is None:
                continue
            name = raw_name.lower()
            if (tag, name) in self._watched:
                self.refs.append((tag, name, value))

    def handle_starttag(self, tag, attrs):  # type: ignore[override]
        self._emit(tag, attrs)

    def handle_startendtag(self, tag, attrs):  # type: ignore[override]
        self._emit(tag, attrs)


def _extract_refs(html_text: str) -> list[tuple[str, str, str]]:
    parser = _RefCollector(set(STRUCTURAL_REF_ATTRIBUTES))
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        # html.parser is permissive but a final close() can occasionally
        # raise on truncated input; the partial ref list is still useful.
        pass
    return parser.refs


# ---------------------------------------------------------------------------
# Reference classification
# ---------------------------------------------------------------------------


def _matches_prefix(value: str, prefixes: Iterable[str]) -> bool:
    return any(value.startswith(p) for p in prefixes)


def _resolve_on_disk(
    href: str,
    *,
    html_path: Path,
    repo_root: Path,
) -> Path:
    """Resolve a relative href to an absolute on-disk path.

    Strips query string and fragment; root-relative hrefs (starting with
    ``/``) resolve against ``repo_root``, others against the directory
    containing the HTML file. Percent-encoded path segments are decoded
    so that ``my%20pic.png`` resolves to the on-disk file ``my pic.png``.
    """
    parts = urlsplit(href)
    # Decode percent-escapes in the path component before joining with the
    # filesystem so ``my%20pic.png`` matches ``my pic.png`` on disk. The
    # original (raw) href is preserved in BrokenRef.original_href by the
    # caller — only the on-disk lookup uses the decoded form.
    raw_path = unquote(parts.path or "")
    # If a relative href is just "?foo" or "#frag" its path is empty;
    # callers should have classified it as fragment beforehand. We still
    # guard here so the function never returns html_path itself.
    if not raw_path:
        raw_path = ""
    if raw_path.startswith("/"):
        rel = raw_path.lstrip("/")
        return (repo_root / rel).resolve()
    return (html_path.parent / raw_path).resolve()


def _is_outside_root(resolved: Path, repo_root: Path) -> bool:
    """Return True if ``resolved`` does not lie under ``repo_root``.

    Uses ``Path.relative_to`` (Python 3.9+) wrapped in try/except for
    portability — ``Path.is_relative_to`` is only 3.9+ and we want to keep
    the dependency surface minimal.
    """
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return True
    return False


def _classify(
    tag: str,
    attribute: str,
    value: str,
    *,
    html_path: Path,
    repo_root: Path,
    allow_prefixes: tuple[str, ...],
    result: StructuralAuditResult,
) -> None:
    """Sort one ref into ``broken`` / ``absolute`` / ``fragments``."""
    # Empty href: self-link, treat as fragment.
    if value == "":
        result.fragments.append(value)
        return
    if _matches_prefix(value, allow_prefixes):
        # "#..." anchors live on the fragment list, not the absolute one.
        if value.startswith("#"):
            result.fragments.append(value)
        else:
            result.absolute.append(value)
        return

    parts = urlsplit(value)
    # Protocol-relative URLs (``//cdn.example.com/x.js``) and any href that
    # parses to a non-empty network location are absolute references the
    # browser will fetch from another origin; never look them up on disk.
    if parts.netloc:
        result.absolute.append(value)
        return
    # Query-only (``?foo=1``) or fragment-only (``#section``) hrefs have an
    # empty path; they are self-references and must not trigger a disk
    # lookup against the HTML's parent directory.
    if (parts.path or "") == "":
        result.fragments.append(value)
        return

    resolved = _resolve_on_disk(value, html_path=html_path, repo_root=repo_root)
    # Sandbox to repo_root: a resolved path that escapes the deployed tree
    # (e.g. via ``../../outside.txt``) would 404 in production even when
    # the file happens to exist on the build machine.
    if _is_outside_root(resolved, repo_root):
        result.broken.append(
            BrokenRef(
                original_href=value,
                resolved_path=str(resolved),
                tag=tag,
                attribute=attribute,
            )
        )
        return
    if not resolved.exists():
        result.broken.append(
            BrokenRef(
                original_href=value,
                resolved_path=str(resolved),
                tag=tag,
                attribute=attribute,
            )
        )
        return
    # Directory-style links (`href="about/"`) are served as the directory's
    # index document by GitHub Pages and Python's stdlib http.server. A bare
    # directory without `index.html` (or `index.htm` as a fallback) 404s in
    # production even though the directory itself exists on disk; report it
    # as broken with the would-be index path so the rendered defect names
    # the file the build pipeline must produce.
    if resolved.is_dir():
        index_html = resolved / "index.html"
        index_htm = resolved / "index.htm"
        if not index_html.is_file() and not index_htm.is_file():
            result.broken.append(
                BrokenRef(
                    original_href=value,
                    resolved_path=str(index_html),
                    tag=tag,
                    attribute=attribute,
                )
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit_html_file(
    html_path,
    *,
    repo_root,
    allow_prefixes: tuple[str, ...] = DEFAULT_ALLOW_PREFIXES,
) -> StructuralAuditResult:
    """Audit one HTML file and return a :class:`StructuralAuditResult`.

    ``html_path`` and ``repo_root`` accept either ``str`` or ``Path``.
    """
    html_path = Path(html_path).resolve()
    repo_root = Path(repo_root).resolve()
    text = html_path.read_text(encoding="utf-8", errors="replace")
    refs = _extract_refs(text)

    result = StructuralAuditResult(file=str(html_path))
    for tag, attribute, value in refs:
        result.total_refs += 1
        _classify(
            tag,
            attribute,
            value,
            html_path=html_path,
            repo_root=repo_root,
            allow_prefixes=allow_prefixes,
            result=result,
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _nonempty_prefix(value: str) -> str:
    """argparse ``type=`` callable that rejects empty allow-prefix values.

    An empty prefix would make ``startswith("") == True`` for every href
    and silently classify every reference as absolute, neutering the
    auditor. ``argparse`` converts the ``ArgumentTypeError`` into a clear
    "argument --allow-prefix: ..." message and exit code 2.
    """
    if value == "":
        raise argparse.ArgumentTypeError(
            "--allow-prefix must be a non-empty string; "
            "an empty prefix would match every href and silence the auditor"
        )
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="structural_audit",
        description=(
            "Audit one or more HTML files on disk: every relative href / "
            "src must resolve to an existing file. Absolute URLs and "
            "same-document fragments are catalogued separately. Exits 2 "
            "when any relative reference is broken."
        ),
    )
    parser.add_argument(
        "html_paths",
        nargs="+",
        type=Path,
        metavar="HTML",
        help="One or more HTML files to audit. Each must exist on disk.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT_DEFAULT,
        help=(
            "Resolve root-relative paths (those starting with '/') against "
            "this directory. Default: the repo root containing tools/."
        ),
    )
    parser.add_argument(
        "--allow-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        type=_nonempty_prefix,
        help=(
            "Treat any href starting with PREFIX as an absolute reference "
            "and skip the on-disk check. Repeat to allow more prefixes. "
            "The defaults http:// https:// data: mailto: javascript: # "
            "are always included. PREFIX must be non-empty."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Write a JSON report to this path. Without this flag, only a "
            "human-readable summary is printed to stdout."
        ),
    )
    return parser


def _result_to_payload(result: StructuralAuditResult) -> dict:
    return {
        "file": result.file,
        "broken": [asdict(b) for b in result.broken],
        "absolute": list(result.absolute),
        "fragments": list(result.fragments),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    for path in args.html_paths:
        if not path.is_file():
            sys.stderr.write(f"error: HTML file not found: {path}\n")
            return 2

    allow_prefixes: tuple[str, ...] = tuple(DEFAULT_ALLOW_PREFIXES) + tuple(args.allow_prefix)

    results: list[StructuralAuditResult] = []
    for path in args.html_paths:
        results.append(
            audit_html_file(
                path,
                repo_root=args.repo_root,
                allow_prefixes=allow_prefixes,
            )
        )

    total_refs = sum(r.total_refs for r in results)
    broken_refs = sum(r.broken_refs for r in results)
    summary = {
        "total_refs": total_refs,
        "broken_refs": broken_refs,
        "files_audited": len(results),
    }

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "audited": [_result_to_payload(r) for r in results],
            "summary": summary,
        }
        args.report.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    # Human-readable summary on stdout.
    sys.stdout.write(
        f"audited {summary['files_audited']} file(s); "
        f"{summary['total_refs']} ref(s); "
        f"{summary['broken_refs']} broken\n"
    )
    if broken_refs:
        for r in results:
            for b in r.broken:
                sys.stdout.write(
                    f"  BROKEN {r.file}: <{b.tag} {b.attribute}=\"{b.original_href}\"> "
                    f"-> {b.resolved_path}\n"
                )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
