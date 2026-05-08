"""Site-audit harness for phyground.github.io.

This package provides tooling to capture per-page audit records (console
errors, failed network requests, screenshots, structural HTML facts) for
the rendered site. The Round 0 scaffold ships only the runtime audit
driver (`run_audit.py`) and shared record schema; a sibling
`structural_audit.py` is added in a subsequent task to inspect the
on-disk HTML.

Public API:
    AuditRecord     -- dataclass describing the per-entry record schema
                       written to ``records.json`` by ``run_audit``.
    record_to_dict  -- deterministic dict serialization (used by tests).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AuditRecord:
    """One row of the per-entry audit output.

    Fields mirror the contract documented in ``tools/site_audit/README.md``
    and exercised by ``tests/test_site_audit_harness.py``. All fields are
    populated for both real and dry-run captures so downstream tooling can
    treat the schema uniformly.
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


def record_to_dict(record: AuditRecord) -> dict[str, Any]:
    """Serialize an AuditRecord to a plain dict (JSON-friendly)."""
    return asdict(record)


__all__ = ["AuditRecord", "record_to_dict"]
