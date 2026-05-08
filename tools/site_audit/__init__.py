"""Site-audit harness for phyground.github.io.

This package provides tooling to capture per-page audit records (console
errors, failed network requests, screenshots, structural HTML facts) for
the rendered site. The Round 0 scaffold ships two complementary drivers:

* ``run_audit.py`` — runtime audit via Playwright.
* ``structural_audit.py`` — pure-Python on-disk HTML/link auditor.

Public API:
    AuditRecord                -- per-entry record schema written by ``run_audit``.
    record_to_dict             -- deterministic dict serialization.
    StructuralAuditResult      -- per-file result of the structural auditor.
    BrokenRef                  -- one missing relative reference.
    DEFAULT_ALLOW_PREFIXES     -- URL prefixes treated as absolute (no on-disk check).
    STRUCTURAL_REF_ATTRIBUTES  -- (tag, attribute) pairs the auditor inspects.
    audit_html_file            -- audit a single HTML file and return a result.

Both ``tools.site_audit`` and ``tools.site_audit.structural_audit`` are
pure Python; importing them must not pull in Playwright. The runtime
driver imports Playwright lazily inside the capture path only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class AuditRecord:
    """One row of the per-entry audit output.

    Fields mirror the contract documented in ``tools/site_audit/README.md``
    and exercised by ``tests/test_site_audit_harness.py``. All fields are
    populated for both real and dry-run captures so downstream tooling can
    treat the schema uniformly.

    ``error`` was introduced in the Round 1 hardening pass: it is ``None``
    on successful captures and a short ``"<ExceptionClass>: <message>"``
    string when a per-URL Playwright capture failed. Per-URL failures no
    longer abort the entire run; the field is always present so downstream
    consumers can rely on a stable schema.
    """

    url: str                      # original relative path from urls.txt
    prefixed_url: str             # absolute URL the auditor would fetch
    target: str                   # "local" | "fork"
    final_url: str | None         # post-redirect URL, None in dry-run
    http_status: int | None       # main-document HTTP status, None in dry-run
    viewport: str                 # e.g. "1280x800"
    console_error_count: int
    failed_request_count: int
    screenshot_path: str          # path to the PNG (would-be path in dry-run)
    console_errors: list[dict[str, Any]] = field(default_factory=list)
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None   # exception summary on per-URL failure


def record_to_dict(record: AuditRecord) -> dict[str, Any]:
    """Serialize an AuditRecord to a plain dict (JSON-friendly)."""
    return asdict(record)


# ---------------------------------------------------------------------------
# Structural auditor public API
# ---------------------------------------------------------------------------

#: Default URL prefixes that are treated as absolute references and
#: therefore skipped during the on-disk existence check. Extend with
#: ``--allow-prefix`` on the CLI or the ``allow_prefixes`` argument of
#: :func:`audit_html_file`.
DEFAULT_ALLOW_PREFIXES: tuple[str, ...] = (
    "http://",
    "https://",
    "data:",
    "mailto:",
    "javascript:",
    "#",
)


#: ``(tag, attribute)`` pairs that the structural auditor inspects when
#: walking an HTML document. Each occurrence becomes one entry in the
#: per-file ref tally and is classified as ``broken``, ``absolute``, or
#: ``fragment`` depending on its value.
STRUCTURAL_REF_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("a", "href"),
    ("link", "href"),
    ("script", "src"),
    ("img", "src"),
    ("source", "src"),
    ("video", "src"),
    ("audio", "src"),
    ("iframe", "src"),
)


@dataclass
class BrokenRef:
    """A relative reference whose resolved on-disk target does not exist."""

    original_href: str
    resolved_path: str
    tag: str
    attribute: str


@dataclass
class StructuralAuditResult:
    """Per-file output of :func:`audit_html_file`.

    ``file`` is the absolute path of the HTML document that was audited.
    ``broken`` is the list of missing relative references; ``absolute``
    holds the raw href values for URLs that matched an allow-prefix;
    ``fragments`` holds same-document anchors (``#...``) and empty
    ``href=""`` values, neither of which is checked on disk.
    """

    file: str
    broken: list[BrokenRef] = field(default_factory=list)
    absolute: list[str] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)
    total_refs: int = 0

    @property
    def broken_refs(self) -> int:
        return len(self.broken)


def audit_html_file(
    html_path,
    *,
    repo_root,
    allow_prefixes: tuple[str, ...] = DEFAULT_ALLOW_PREFIXES,
) -> StructuralAuditResult:
    """Audit a single HTML file. See :mod:`tools.site_audit.structural_audit`.

    The implementation lives in ``structural_audit`` to keep this package
    ``__init__`` import-light; this thin wrapper exists so callers can
    ``from tools.site_audit import audit_html_file`` without pulling in
    the CLI module symbols.
    """
    from .structural_audit import audit_html_file as _impl

    return _impl(html_path, repo_root=repo_root, allow_prefixes=allow_prefixes)


def resolve_repo_url_set(*args, **kwargs):
    """Re-export of :func:`tools.site_audit.url_set.resolve_repo_url_set`.

    Lazy passthrough so ``from tools.site_audit import resolve_repo_url_set``
    works without forcing the resolver module to load on every package
    import.
    """
    from .url_set import resolve_repo_url_set as _impl

    return _impl(*args, **kwargs)


def choose_populated_prompt_id(*args, **kwargs):
    """Re-export of :func:`tools.site_audit.url_set.choose_populated_prompt_id`."""
    from .url_set import choose_populated_prompt_id as _impl

    return _impl(*args, **kwargs)


__all__ = [
    "AuditRecord",
    "record_to_dict",
    "BrokenRef",
    "StructuralAuditResult",
    "DEFAULT_ALLOW_PREFIXES",
    "STRUCTURAL_REF_ATTRIBUTES",
    "audit_html_file",
    "resolve_repo_url_set",
    "choose_populated_prompt_id",
]
