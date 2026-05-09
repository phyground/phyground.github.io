"""Snapshot URL-prefix contract tests.

These tests lock in the invariant that every snapshot-relative URL emitted
into ``snapshot/index/site_config.json`` (and consumed by Jinja templates
via the ``rel(...)`` helper) starts with ``snapshot/`` so it resolves on
disk under the repo root.

The invariant is enforced at the source -- the URL emission helpers in
``tools.build_snapshot`` -- and at the artifact level by reading the
committed ``site_config.json`` and checking each emitted URL exists on
disk under ``<repo_root>/<url>``. A separate test runs the full
build_site.py pipeline against the committed config and asserts the
rendered HTML has zero broken local references via ``audit_html_file``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_CONFIG_PATH = REPO_ROOT / "snapshot" / "index" / "site_config.json"

# Pages that must be audited for zero broken local refs after rebuild.
PUBLISHED_HTML_RELPATHS = (
    "index.html",
    "leaderboard/index.html",
    "videos/index.html",
    "videos/compare/index.html",
    "about/index.html",
    "models/cosmos-predict2.5-14b/index.html",
    "models/cosmos-predict2.5-2b/index.html",
    "models/ltx-2-19b-dev/index.html",
    "models/ltx-2.3-22b-dev/index.html",
    "models/omniweaving/index.html",
    "models/veo-3.1/index.html",
    "models/wan2.2-i2v-a14b/index.html",
    "models/wan2.2-ti2v-5b/index.html",
)


# ---------------------------------------------------------------------------
# Unit tests for the URL emission helper itself
# ---------------------------------------------------------------------------

class TestSnapshotScoreUrl:
    """`_snapshot_score_url` must emit `snapshot/`-prefixed URLs so the
    rendered HTML resolves the file under `<repo_root>/snapshot/scores/...`.
    """

    def test_data_scores_gemini_uses_snapshot_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("data/scores/gemini/foo.json")
        assert out == "snapshot/scores/gemini/foo.json"

    def test_data_scores_ourckpt_uses_snapshot_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("data/scores/ourckpt/eval_qwen9b_x.json")
        assert out == "snapshot/scores/ourckpt/eval_qwen9b_x.json"

    def test_data_scores_claude_uses_snapshot_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("data/scores/claude/bar.json")
        assert out == "snapshot/scores/claude/bar.json"

    def test_data_scores_external_uses_snapshot_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("data/scores/_external/external.json")
        assert out == "snapshot/scores/_external/external.json"

    def test_data_training_uses_snapshot_training_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("data/training/cot/sample.json")
        assert out == "snapshot/scores/_training/cot/sample.json"

    def test_tmp_uses_snapshot_tmp_prefix(self):
        from tools.build_snapshot import _snapshot_score_url

        out = _snapshot_score_url("tmp/eval/sample.json")
        assert out == "snapshot/scores/_tmp/eval/sample.json"

    def test_unrecognized_prefix_returns_none(self):
        from tools.build_snapshot import _snapshot_score_url

        assert _snapshot_score_url("nonsense/xyz.json") is None


# ---------------------------------------------------------------------------
# Site config artifact-level invariants
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site_config() -> dict:
    if not SITE_CONFIG_PATH.is_file():
        pytest.skip(f"{SITE_CONFIG_PATH.relative_to(REPO_ROOT)} not present.")
    return json.loads(SITE_CONFIG_PATH.read_text(encoding="utf-8"))


class TestPaperdemoFigUrls:
    def test_paperdemo_fig_pdf_uses_snapshot_prefix(self, site_config):
        for entry in site_config.get("paperdemo", []):
            url = entry.get("fig_pdf")
            assert url, f"paperdemo entry {entry.get('law')!r} missing fig_pdf"
            assert url.startswith("snapshot/index/figs/"), (
                f"paperdemo[{entry.get('law')}].fig_pdf={url!r} must start with "
                "'snapshot/index/figs/'"
            )

    def test_paperdemo_fig_png_uses_snapshot_prefix(self, site_config):
        for entry in site_config.get("paperdemo", []):
            url = entry.get("fig_png")
            assert url, f"paperdemo entry {entry.get('law')!r} missing fig_png"
            assert url.startswith("snapshot/index/figs/"), (
                f"paperdemo[{entry.get('law')}].fig_png={url!r} must start with "
                "'snapshot/index/figs/'"
            )

    def test_paperdemo_figs_resolve_against_repo_root(self, site_config):
        missing = []
        for entry in site_config.get("paperdemo", []):
            for field in ("fig_pdf", "fig_png"):
                url = entry.get(field)
                if not url:
                    continue
                abs_path = REPO_ROOT / url
                if not abs_path.is_file():
                    missing.append((entry.get("law"), field, url))
        assert not missing, (
            f"{len(missing)} paperdemo fig URL(s) do not resolve on disk; "
            f"first: {missing[:3]}"
        )


class TestSnapshotScoreUrlsResolve:
    def _iter_score_urls(self, site_config: dict):
        for i, entry in enumerate(site_config.get("leaderboard_entries", [])):
            cur = entry.get("current") or {}
            url = cur.get("source_url_snapshot")
            if url:
                yield (f"leaderboard_entries[{i}].current.source_url_snapshot", url)
            for j, h in enumerate(entry.get("history") or []):
                url = h.get("source_url_snapshot")
                if url:
                    yield (
                        f"leaderboard_entries[{i}].history[{j}].source_url_snapshot",
                        url,
                    )
        for m in site_config.get("models", []):
            mk = m.get("key")
            for j, slc in enumerate(m.get("leaderboard_slices") or []):
                url = slc.get("source_url_snapshot")
                if url:
                    yield (f"models[{mk}].leaderboard_slices[{j}].source_url_snapshot", url)
        for mk, mv in (site_config.get("videos_index") or {}).items():
            for j, ds in enumerate(mv.get("datasets") or []):
                url = ds.get("source_url_snapshot")
                if url:
                    yield (f"videos_index[{mk}].datasets[{j}].source_url_snapshot", url)

    def test_every_score_url_uses_snapshot_prefix(self, site_config):
        bad = []
        for label, url in self._iter_score_urls(site_config):
            if not url.startswith("snapshot/scores/"):
                bad.append((label, url))
        assert not bad, (
            f"{len(bad)} score URL(s) do not start with 'snapshot/scores/'; "
            f"first: {bad[:3]}"
        )

    def test_every_score_url_resolves_on_disk(self, site_config):
        missing = []
        for label, url in self._iter_score_urls(site_config):
            abs_path = REPO_ROOT / url
            if not abs_path.is_file():
                missing.append((label, url))
        assert not missing, (
            f"{len(missing)} score URL(s) do not resolve under repo root; "
            f"first: {missing[:3]}"
        )


# ---------------------------------------------------------------------------
# End-to-end: every published HTML file in the committed repo has zero
# broken local refs once `build_site.py` has been run against the fixed
# `site_config.json`. This is the structural lock for the URL contract:
# any regression that re-broke the score / fig URLs would re-fire here.
# ---------------------------------------------------------------------------

class TestBuiltSiteHasZeroBrokenLocalRefs:
    def test_zero_broken_local_refs(self):
        from tools.site_audit.structural_audit import audit_html_file

        broken_summary = []
        for relpath in PUBLISHED_HTML_RELPATHS:
            html_path = REPO_ROOT / relpath
            if not html_path.is_file():
                broken_summary.append((relpath, "MISSING_HTML"))
                continue
            res = audit_html_file(html_path, repo_root=REPO_ROOT)
            if res.broken:
                broken_summary.append(
                    (
                        relpath,
                        [(b.original_href, b.resolved_path) for b in res.broken[:3]],
                        len(res.broken),
                    )
                )
        assert not broken_summary, (
            f"{len(broken_summary)} page(s) have broken local refs: "
            f"{broken_summary}"
        )
