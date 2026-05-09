"""Canonical 14-URL audit set, sourced from ``snapshot/index/site_config.json``.

The site-audit driver previously consumed a hand-rolled ``urls.txt`` file
which was easy to drift from the live site state. This module replaces
that with a pure-Python resolver that reads the snapshot config and
produces a deterministic 14-URL list:

  1.  ``/``
  2.  ``/leaderboard/``
  3.  ``/videos/``
  4.  ``/about/``
  5.  ``/videos/compare/``                                (placeholder state)
  6.  ``/videos/compare/?prompt_id=<chosen_pid>``         (populated state)
  7-14. ``/models/<key>/`` for each published model, alphabetical order.

``<chosen_pid>`` is the alphabetically first prompt in
``site_config["prompts_index"]`` whose ``per_model_videos`` AND
``per_model_scores`` cover all 8 published models. If no prompt has full
coverage the resolver raises ``ValueError`` instead of silently picking
a partially-rendered prompt — that would let the audit pass against an
incomplete site state.

The published-model set is derived from ``site_config["videos_index"]``
keys (the canonical post-build source of truth) and cross-checked
against ``tests/conftest.py``'s ``PUBLISHED_MODEL_KEYS`` constant; any
divergence raises ``ValueError`` so a snapshot rebuild that adds or
drops a model surfaces here loudly instead of producing the wrong URL
set.

This module is deliberately import-light. It uses only the standard
library, never imports Playwright (the site_audit package keeps that
dependency lazy), and is safe to import from tests, the run_audit CLI,
and ad-hoc scripts.

Usage::

    from tools.site_audit.url_set import resolve_repo_url_set
    urls = resolve_repo_url_set()              # 14 entries

    # Debugging:
    python -m tools.site_audit.url_set
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The canonical 8 published model keys. Mirrored from
# ``tests/conftest.py``; the resolver verifies the live
# ``videos_index`` matches this set exactly so a snapshot drift surfaces
# loudly. Kept as a tuple so the order is deterministic in error
# messages.
PUBLISHED_MODEL_KEYS: frozenset[str] = frozenset({
    "cosmos-predict2.5-14b",
    "cosmos-predict2.5-2b",
    "ltx-2-19b-dev",
    "ltx-2.3-22b-dev",
    "omniweaving",
    "veo-3.1",
    "wan2.2-i2v-a14b",
    "wan2.2-ti2v-5b",
})

# Top-level pages and the placeholder compare state. Pinned in this
# exact order (matches the audit plan and the test contract).
_TOP_LEVEL_URLS: tuple[str, ...] = (
    "/",
    "/leaderboard/",
    "/videos/",
    "/about/",
    "/videos/compare/",
)

# Repo root is the parent of the directory that contains ``tools/site_audit``.
# Anchored to ``__file__`` so the resolver works regardless of the caller's
# current working directory; the previous cwd-relative default crashed any
# ``--url-set repo`` invocation launched from a path other than the repo
# root, which the README documents as a supported entry point.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_SITE_CONFIG_PATH: Path = _REPO_ROOT / "snapshot" / "index" / "site_config.json"


def _load_site_config(site_config_path: Path | str) -> dict[str, Any]:
    path = Path(site_config_path)
    if not path.is_file():
        raise FileNotFoundError(f"site_config.json not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_videos_index_matches_published_keys(videos_index_keys: set[str]) -> None:
    """Ensure ``videos_index`` keys match the canonical published set.

    If they diverge, raise ``ValueError`` with the symmetric difference
    so a future snapshot rebuild that adds or drops a model surfaces
    here instead of producing a silently-wrong URL set.
    """
    expected = set(PUBLISHED_MODEL_KEYS)
    if videos_index_keys != expected:
        extra = sorted(videos_index_keys - expected)
        missing = sorted(expected - videos_index_keys)
        raise ValueError(
            "site_config['videos_index'] keys do not match the canonical "
            "PUBLISHED_MODEL_KEYS set: "
            f"unexpected={extra}, missing={missing}"
        )


def choose_populated_prompt_id(site_config: dict[str, Any]) -> str:
    """Return the alphabetically first prompt_id with full 8-model coverage.

    A prompt qualifies when both ``per_model_videos`` and
    ``per_model_scores`` are supersets of ``PUBLISHED_MODEL_KEYS``.
    Selection is alphabetical-first so the populated compare URL is
    stable across rebuilds.

    Raises:
        ValueError: if no prompt in ``prompts_index`` has full coverage.
    """
    prompts_index = site_config.get("prompts_index") or {}
    fully_covered: list[str] = []
    expected = set(PUBLISHED_MODEL_KEYS)
    for pid, entry in prompts_index.items():
        videos = set((entry or {}).get("per_model_videos") or {})
        scores = set((entry or {}).get("per_model_scores") or {})
        if expected.issubset(videos) and expected.issubset(scores):
            fully_covered.append(pid)
    if not fully_covered:
        raise ValueError(
            "No prompt in prompts_index has all 8 published models scored "
            "and rendered"
        )
    return sorted(fully_covered)[0]


def resolve_repo_url_set(
    site_config_path: Path | str = DEFAULT_SITE_CONFIG_PATH,
) -> list[str]:
    """Return the canonical 14 relative URLs for the audit.

    Order::

        1.  /
        2.  /leaderboard/
        3.  /videos/
        4.  /about/
        5.  /videos/compare/
        6.  /videos/compare/?prompt_id=<chosen_pid>
        7-14.  /models/<key>/   (sorted alphabetical, 8 entries)

    Args:
        site_config_path: Path to ``snapshot/index/site_config.json``.
            Defaults to the repo-relative location; callers using a
            different working directory should pass an absolute path.

    Returns:
        A new list of 14 relative URLs, in the exact order above.

    Raises:
        FileNotFoundError: if the site_config path does not exist.
        ValueError: if the videos_index diverges from
            ``PUBLISHED_MODEL_KEYS``, or if no prompt has full coverage.
    """
    site_config = _load_site_config(site_config_path)

    videos_index_keys = set((site_config.get("videos_index") or {}).keys())
    _verify_videos_index_matches_published_keys(videos_index_keys)

    chosen_pid = choose_populated_prompt_id(site_config)

    sorted_keys = sorted(videos_index_keys)
    model_urls = [f"/models/{key}/" for key in sorted_keys]

    urls: list[str] = list(_TOP_LEVEL_URLS)
    urls.append(f"/videos/compare/?prompt_id={chosen_pid}")
    urls.extend(model_urls)

    if len(urls) != 14:
        # Defensive guard: should be unreachable given the checks above.
        raise ValueError(f"resolved URL set has {len(urls)} entries, expected 14")
    return urls


__all__ = [
    "PUBLISHED_MODEL_KEYS",
    "DEFAULT_SITE_CONFIG_PATH",
    "choose_populated_prompt_id",
    "resolve_repo_url_set",
]


if __name__ == "__main__":
    for url in resolve_repo_url_set():
        print(url)
