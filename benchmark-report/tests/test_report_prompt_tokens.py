"""Tests for input-length reporting across the inference-perf field rename.

``prompt_len`` was unified into ``prompt_tokens`` upstream, and newer harness
versions emit only the latter. Pins that:

- a result file carrying only ``prompt_tokens`` still yields an input length,
  so a v0.2 report validates instead of failing on a missing value;
- an older file carrying only ``prompt_len`` keeps working;
- ``prompt_tokens`` wins where both exist, matching the harness's own
  precedence (it is what the server counted).

Without the fallback a trace-replay workload reports no input length at all:
it has no configured input distribution to fall back on, so the report fails
validation and the run is judged failed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmd_benchmark_report.native_to_br0_2 import _prompt_stat, import_inference_perf

FIXTURES = Path(__file__).parent / "fixtures"
#: Real harness output from a post-rename inference-perf (prompt_tokens only).
TOKENS_ONLY = FIXTURES / "inference_perf_stage_prompt_tokens_only.json"
#: Real harness output from a build that still emitted both.
BOTH = FIXTURES / "inference_perf_stage_lifecycle_metrics.json"


@pytest.mark.parametrize(
    "successes, expected",
    [
        ({"prompt_tokens": {"mean": 42.0}}, 42.0),
        ({"prompt_len": {"mean": 7.0}}, 7.0),
        # Both present: the newer name wins.
        ({"prompt_tokens": {"mean": 42.0}, "prompt_len": {"mean": 7.0}}, 42.0),
        ({}, None),
    ],
)
def test_prompt_stat(successes: dict, expected: float | None) -> None:
    assert _prompt_stat({"successes": successes}, "successes", "mean") == expected


@pytest.mark.parametrize("fixture", [TOKENS_ONLY, BOTH])
def test_report_carries_an_input_length(fixture: Path) -> None:
    """A missing value fails validation, so this must never be None."""
    successes = json.loads(fixture.read_text())["successes"]
    # The point of TOKENS_ONLY: without the fallback there is nothing to read.
    assert ("prompt_len" in successes) is (fixture is BOTH)

    report = import_inference_perf(str(fixture))

    assert report.scenario.load.standardized.input_seq_len.value == pytest.approx(
        successes["prompt_tokens"]["mean"]
    )
