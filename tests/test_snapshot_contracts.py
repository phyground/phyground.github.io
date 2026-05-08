"""Snapshot-emission contract tests (Round 19+ backfill).

These tests lock in invariants established by Rounds 13-18 of the RLCR
loop: each test corresponds to a regression that was caught and fixed,
and would re-fire if that regression returned. They read on-disk
artifacts (snapshot/index/site_config.json, snapshot/HF_UPLOAD_MANIFEST.json)
only -- no imports from tools/, no pipeline rebuild.
"""
from __future__ import annotations

from typing import Iterable


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _iter_hf_urls(site_config: dict) -> Iterable[tuple[str, str]]:
    """Yield (location_label, url) pairs for every HF-shaped URL we surface
    in site_config. `url` may be None (callers filter); the label is for
    diagnostic messages.
    """
    for i, p in enumerate(site_config.get("paperdemo", []) or []):
        for j, v in enumerate(p.get("videos", []) or []):
            yield (f"paperdemo[{i}].videos[{j}].video_url_hf", v.get("video_url_hf"))

    fc = site_config.get("featured_comparison") or {}
    for j, v in enumerate(fc.get("videos", []) or []):
        yield (f"featured_comparison.videos[{j}].video_url_hf", v.get("video_url_hf"))

    for pid, p in (site_config.get("prompts_index") or {}).items():
        yield (f"prompts_index[{pid}].first_frame_url", p.get("first_frame_url"))
        for mk, vu in (p.get("per_model_videos") or {}).items():
            yield (f"prompts_index[{pid}].per_model_videos[{mk}]", vu)

    for mk, mv in (site_config.get("videos_index") or {}).items():
        for j, x in enumerate(mv.get("paperdemo", []) or []):
            yield (f"videos_index[{mk}].paperdemo[{j}].video_url_hf", x.get("video_url_hf"))
            yield (f"videos_index[{mk}].paperdemo[{j}].first_frame_url", x.get("first_frame_url"))
        for j, x in enumerate(mv.get("humaneval", []) or []):
            yield (f"videos_index[{mk}].humaneval[{j}].video_url_hf", x.get("video_url_hf"))
            yield (f"videos_index[{mk}].humaneval[{j}].first_frame_url", x.get("first_frame_url"))

    for m in site_config.get("models", []) or []:
        mk = m.get("key")
        for j, rv in enumerate(m.get("representative_videos", []) or []):
            yield (f"models[{mk}].representative_videos[{j}].video_url_hf", rv.get("video_url_hf"))
            yield (f"models[{mk}].representative_videos[{j}].first_frame_url", rv.get("first_frame_url"))


def _walk_strings(node):
    """Recursively yield every str value in a nested JSON structure."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)


# --------------------------------------------------------------------------
# Test classes
# --------------------------------------------------------------------------

class TestLeaderboardContract:
    def test_entries_nonempty(self, site_config):
        """Round 15-16: leaderboard_entries must be populated after publish-set filter."""
        assert len(site_config["leaderboard_entries"]) > 0

    def test_rows_use_video_model_field(self, site_config):
        """Round 15-16: rows are keyed on `video_model` (not `model_key`)."""
        for i, row in enumerate(site_config["leaderboard_entries"]):
            assert "video_model" in row, f"row[{i}] missing 'video_model': keys={list(row)}"

    def test_video_model_values_published(self, site_config, published_keys):
        """Round 15-16: every row's video_model is in the published set (no hidden leak)."""
        for i, row in enumerate(site_config["leaderboard_entries"]):
            vm = row["video_model"]
            assert vm in published_keys, f"row[{i}].video_model={vm!r} not in published_keys"

    def test_entry_count_locked(self, site_config):
        """Round 18: lock the current published leaderboard row count at 51."""
        assert len(site_config["leaderboard_entries"]) == 51

    def test_headline_n_eval_combos_matches_entries(self, site_config):
        """Round 15-16: headline.n_eval_combos must equal len(leaderboard_entries)."""
        assert site_config["headline"]["n_eval_combos"] == len(site_config["leaderboard_entries"])


class TestModelsContract:
    def test_models_count_is_eight(self, site_config):
        """Round 15/18: models[] is reduced to the 8 published keys."""
        assert len(site_config["models"]) == 8

    def test_models_keys_match_published(self, site_config, published_keys):
        """Round 15/18: models[] keys match the published frozenset exactly."""
        keys = {m["key"] for m in site_config["models"]}
        assert keys == set(published_keys)

    def test_representative_videos_card_count(self, site_config):
        """AC5 (Rounds 13-18): every model has 6-9 representative video cards."""
        for m in site_config["models"]:
            n = len(m.get("representative_videos") or [])
            assert 6 <= n <= 9, f"model {m['key']!r} has {n} representative_videos (need 6-9)"

    def test_models_have_leaderboard_slices(self, site_config):
        """Round 15: every model exposes >=1 leaderboard slice for the model page."""
        for m in site_config["models"]:
            n = len(m.get("leaderboard_slices") or [])
            assert n >= 1, f"model {m['key']!r} has 0 leaderboard_slices"

    def test_headline_n_models_is_eight(self, site_config):
        """Round 15/18: headline.n_models == 8."""
        assert site_config["headline"]["n_models"] == 8


class TestPerModelScoresContract:
    def test_no_hidden_model_in_per_model_scores(self, site_config, published_keys, hidden_keys):
        """Round 16: prompts_index[*].per_model_scores must only expose published keys
        (this fix closed the compare-page hidden-model leak).
        """
        for pid, p in site_config["prompts_index"].items():
            scores = p.get("per_model_scores") or {}
            for mk in scores:
                assert mk in published_keys, (
                    f"prompts_index[{pid}].per_model_scores has non-published key {mk!r}"
                )
                assert mk not in hidden_keys, (
                    f"prompts_index[{pid}].per_model_scores leaks hidden key {mk!r}"
                )


class TestVideosIndexContract:
    def test_videos_index_keys_match_published(self, site_config, published_keys):
        """Round 15: videos_index keys == the 8 published model keys."""
        assert set(site_config["videos_index"].keys()) == set(published_keys)


class TestHFUrlPrefixContract:
    def test_all_embedded_urls_use_juyil_prefix(self, site_config):
        """Round 9: every HF-shaped URL we surface targets juyil/phygroundwebsitevideo."""
        from tests.conftest import HF_PREFIX  # noqa: WPS433  -- intentional test-only import

        bad = []
        for label, url in _iter_hf_urls(site_config):
            if url is None:
                continue
            if not url.startswith(HF_PREFIX):
                bad.append((label, url))
        assert not bad, f"{len(bad)} URL(s) do not use HF_PREFIX; first: {bad[:3]}"


class TestHFManifestCoverageContract:
    def test_manifest_covers_every_embedded_url(self, site_config, hf_manifest):
        """Round 6: every HF URL embedded in site_config has a manifest entry."""
        from tests.conftest import HF_PREFIX  # noqa: WPS433

        manifest_paths = {e["hf_target_path"] for e in hf_manifest["files"]}

        missing = []
        for label, url in _iter_hf_urls(site_config):
            if url is None or not url.startswith(HF_PREFIX):
                continue
            rel = url[len(HF_PREFIX):]
            if rel not in manifest_paths:
                missing.append((label, rel))
        assert not missing, (
            f"{len(missing)} embedded HF URL(s) have no manifest entry; first: {missing[:3]}"
        )


class TestHFManifestHealthContract:
    def test_total_file_count_locked(self, hf_manifest):
        """Round 12+: lock the total HF manifest file count at 884."""
        assert hf_manifest["n_total_files"] == 884

    def test_all_files_present_locally(self, hf_manifest):
        """Round 12: every manifest entry must resolve to a local file."""
        assert hf_manifest["n_present_locally"] == 884

    def test_no_missing_files(self, hf_manifest):
        """Round 12: zero missing-locally entries (no dangling HF references)."""
        assert hf_manifest["n_missing_locally"] == 0

    def test_hf_repo_is_juyil(self, hf_manifest):
        """Round 9: manifest targets the juyil/phygroundwebsitevideo dataset."""
        assert hf_manifest["hf_repo"] == "juyil/phygroundwebsitevideo"

    def test_exactly_one_readme_entry(self, hf_manifest):
        """Round 12: README.md is synthesized exactly once in the manifest."""
        readmes = [e for e in hf_manifest["files"] if e.get("hf_target_path") == "README.md"]
        assert len(readmes) == 1, f"expected 1 README.md entry, found {len(readmes)}"


class TestNoHiddenModelLeakContract:
    def test_no_hidden_key_appears_anywhere_in_site_config(self, site_config, hidden_keys):
        """Paranoid catch-all: no hidden model key appears as a path component
        in any string value anywhere in site_config (e.g. videos/baseline_i2v_*/).
        """
        offenders: list[tuple[str, str]] = []
        for s in _walk_strings(site_config):
            for hk in hidden_keys:
                # match as a path segment to avoid spurious substring hits
                needle_slash = f"/{hk}/"
                needle_prefix = f"{hk}/"
                if needle_slash in s or s.startswith(needle_prefix) or s == hk:
                    offenders.append((hk, s))
                    break
        assert not offenders, (
            f"{len(offenders)} hidden-key leak(s) in site_config; first: {offenders[:3]}"
        )
