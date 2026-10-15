"""The driver-side analysis writes one report per GuideLLM benchmark, as the
in-pod benchmark-report CLI does, not only the first one."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from llmdbenchmark.analysis import _convert_via_api

FIXTURE = Path(__file__).parent / "fixtures" / "guidellm_report_v2.json"


@pytest.mark.parametrize(
    ("br_version", "prefix"),
    [("0.1", "benchmark_report"), ("0.2", "benchmark_report_v0.2")],
)
def test_every_guidellm_benchmark_gets_a_report(tmp_path, br_version, prefix):
    result_file = tmp_path / "results.json"
    shutil.copy(FIXTURE, result_file)
    output_file = tmp_path / f"{prefix},_results.json.yaml"

    assert _convert_via_api(result_file, output_file, "guidellm", br_version) is None

    # The fixture holds two benchmarks.
    assert sorted(p.name for p in tmp_path.glob("*.yaml")) == [
        f"{prefix},_results.json_0.yaml",
        f"{prefix},_results.json_1.yaml",
    ]
