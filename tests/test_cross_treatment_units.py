"""Tests that the cross-treatment "*_s" columns hold seconds for every harness.

Benchmark reports keep each latency statistic in its harness's own unit (vLLM
reports ms, aiperf s), so the comparison has to convert by the statistic's
``units`` rather than copy the number.
"""

import csv
import shutil
from pathlib import Path

import pytest

from llmdbenchmark.analysis.cross_treatment import generate_cross_treatment_summary

GOLDEN = Path(__file__).parent / "fixtures" / "br_v0_2_golden"


def test_latency_columns_are_in_seconds(tmp_path):
    results = tmp_path / "results"
    for treatment, golden in (
        ("vllm", "vllm_benchmark.yaml"),
        ("aiperf", "aiperf.yaml"),
    ):
        (results / treatment).mkdir(parents=True)
        shutil.copy(GOLDEN / golden, results / treatment / "benchmark_report_v0.2.yaml")

    generate_cross_treatment_summary(results, output_dir=tmp_path / "comparison")

    with open(
        tmp_path / "comparison" / "treatment_comparison.csv", encoding="utf-8"
    ) as f:
        rows = {row["treatment"]: row for row in csv.DictReader(f)}
    # vllm_benchmark.yaml reports TTFT 53.8569 ms and E2E 13834.4001 ms; aiperf.yaml
    # reports TTFT 0.1209825 s and E2E 2.4582684 s.
    assert float(rows["vllm"]["ttft_mean_s"]) == pytest.approx(0.0538569)
    assert float(rows["vllm"]["e2e_mean_s"]) == pytest.approx(13.8344001)
    assert float(rows["aiperf"]["ttft_mean_s"]) == pytest.approx(0.1209825)
    assert float(rows["aiperf"]["e2e_mean_s"]) == pytest.approx(2.4582684)
