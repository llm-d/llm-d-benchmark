"""Tests that KV-cache usage statistics carry the unit of their values.

vLLM documents ``vllm:kv_cache_usage_perc`` as "KV-cache usage. 1 means 100
percent usage", so it is a 0-1 fraction despite its name, and the same report's
time series already labels it (and the EPP pool average) as a fraction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmdbenchmark.analysis.benchmark_report.metrics_processor import (
    add_metrics_to_benchmark_report,
)

# Exercises the Benchmark Report package, so CI runs it against the checkout.
pytestmark = pytest.mark.local_benchmark_report


@pytest.mark.parametrize(
    "prom_name,report_key",
    [
        ("vllm:kv_cache_usage_perc", "vllm_kv_cache_usage_perc"),
        (
            "inference_pool_average_kv_cache_utilization",
            "epp_pool_avg_kv_cache_utilization",
        ),
    ],
)
def test_kv_cache_usage_is_a_fraction(tmp_path: Path, prom_name, report_key) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps(
            {
                "pod-1": {
                    "metrics": {
                        prom_name: {"mean": 0.5, "p50": 0.5, "p99": 0.9, "stddev": 0.1}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    report = add_metrics_to_benchmark_report({}, str(tmp_path))
    statistics = report["results"]["observability"][report_key]["components"][0][
        "statistics"
    ]

    assert statistics["mean"] == 0.5
    assert statistics["units"] == "fraction"
