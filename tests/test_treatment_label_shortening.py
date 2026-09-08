"""Tests for _shorten_treatment_label harness handling.

The hardcoded prefix list covers only the harnesses shipped today, so any other
keeps its harness name in the label. Callers that know the harness pass it
instead, which treats every harness alike.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.analysis.cross_treatment import (
    _KNOWN_HARNESS_PREFIXES,
    _shorten_treatment_label,
)

ALL_HARNESSES = [
    "inference-perf",
    "guidellm",
    "vllm-benchmark",
    "inferencemax",
    "nop",
    "aiperf",
    "lm-eval",
    "eval-containers",
]


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_every_harness_yields_the_same_label(harness) -> None:
    """A named harness is stripped whether or not it is in the hardcoded list."""
    label = _shorten_treatment_label(f"{harness}-conc32-1786024743-hipkpq_1", harness)

    assert label == "conc32"


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_compound_treatment_survives_for_every_harness(harness) -> None:
    label = _shorten_treatment_label(
        f"{harness}-grp40-splen8k-1773947901-i5e39v_1", harness
    )

    assert label == "grp40-splen8k"


def test_aiperf_no_longer_needs_the_argument() -> None:
    """aiperf is in the hardcoded list, so it shortens with or without the name.

    It used to be absent and kept its prefix when the caller had no harness
    name to hand; both paths now agree.
    """
    name = "aiperf-conc32-1786024743-aaaaaa_1"

    assert _shorten_treatment_label(name) == "conc32"
    assert _shorten_treatment_label(name, "aiperf") == "conc32"


def test_harnesses_outside_the_hardcoded_list_need_the_argument() -> None:
    """Without the harness name an unlisted harness keeps its prefix.

    Documents why callers should pass it; this is the pre-existing behaviour the
    hardcoded list leaves in place for any harness added later.
    """
    name = "future-harness-conc32-1786024743-aaaaaa_1"

    assert _shorten_treatment_label(name) == name.rsplit("-", 2)[0]
    assert _shorten_treatment_label(name, "future-harness") == "conc32"


def test_prefix_is_stripped_once() -> None:
    """A treatment may legitimately begin with the harness name."""
    prefixed = _shorten_treatment_label(
        "inference-perf-inference-perf-conc32-1786024743-aaaaaa", "inference-perf"
    )
    plain = _shorten_treatment_label(
        "inference-perf-conc32-1786024743-bbbbbb", "inference-perf"
    )

    assert prefixed == "inference-perf-conc32"
    assert prefixed != plain


def test_unknown_harness_name_is_not_stripped() -> None:
    """A harness name that does not prefix the ID leaves the label alone."""
    label = _shorten_treatment_label(
        "inference-perf-conc32-1786024743-hipkpq_1", "guidellm"
    )

    assert label == "inference-perf-conc32"


@pytest.mark.parametrize("harness", _KNOWN_HARNESS_PREFIXES)
def test_no_context_fallback_is_unchanged(harness) -> None:
    """The 8 cross_treatment callers pass no harness and must keep working."""
    assert _shorten_treatment_label(f"{harness}-conc32-1773947901-abc123_1") == "conc32"


def test_empty_harness_name_falls_back_to_the_hardcoded_list() -> None:
    assert (
        _shorten_treatment_label("guidellm-conc32-1773947901-abc123_1", "") == "conc32"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_bare_harness_id_shortens_to_the_harness_name(harness) -> None:
    """<harness>-<ts>-<rand> encodes no treatment, for every harness alike.

    Nothing is left to strip a prefix from, so the harness name is returned;
    callers treat that as "no treatment".
    """
    assert (
        _shorten_treatment_label(f"{harness}-1773947901-xyz789_1", harness) == harness
    )
