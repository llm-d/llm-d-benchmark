"""Tests for configurable Prometheus time-series metric selection."""

from __future__ import annotations

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path

from llmdbenchmark.analysis import visualize_metrics
from llmdbenchmark.analysis.benchmark_report.metrics_processor import (
    add_metrics_to_benchmark_report,
)


def test_process_metrics_uses_configured_metric_list(
    tmp_path: Path, monkeypatch
) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    (raw_dir / "pod-1_metrics.log").write_text(
        "# Timestamp: 2026-07-14T00:00:00Z\n"
        "# Pod: pod-1\n"
        "# Namespace: bench\n"
        "vllm:custom_metric 42\n"
        "vllm:num_requests_running 7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("METRICS_DIR", str(metrics_dir))
    monkeypatch.setenv("LLMDBENCH_TIME_SERIES_METRICS", '["vllm:custom_metric"]')

    module = runpy.run_path(
        "workload/harnesses/process_metrics.py", run_name="process_metrics_test"
    )
    summary = module["aggregate_metrics"]()

    assert set(summary["pod-1"]["metrics"]) == {"vllm:custom_metric"}
    assert set(summary["_aggregated"]["metrics"]) == {"vllm:custom_metric"}
    assert json.loads(
        (processed_dir / "time_series_metrics.json").read_text(encoding="utf-8")
    ) == ["vllm:custom_metric"]


def test_visualization_loads_persisted_metric_list(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    (processed_dir / "time_series_metrics.json").write_text(
        '["vllm:custom_metric"]', encoding="utf-8"
    )

    assert visualize_metrics._load_time_series_metrics(str(tmp_path)) == [
        "vllm:custom_metric"
    ]


def test_visualization_plots_configured_custom_metric(
    tmp_path: Path, monkeypatch
) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    (processed_dir / "time_series_metrics.json").write_text(
        '["vllm:custom_metric"]', encoding="utf-8"
    )
    plotted: list[str] = []
    monkeypatch.setattr(visualize_metrics, "MATPLOTLIB_AVAILABLE", True)
    monkeypatch.setattr(
        visualize_metrics,
        "collect_time_series_data",
        lambda _metrics_dir: {
            "pod-1": {
                "vllm:custom_metric": [
                    (datetime(2026, 7, 14, tzinfo=timezone.utc), 42.0)
                ]
            }
        },
    )
    monkeypatch.setattr(
        visualize_metrics,
        "plot_metric_time_series",
        lambda _pod_data, metric_name, *_args, **_kwargs: plotted.append(metric_name),
    )
    monkeypatch.setattr(
        visualize_metrics, "plot_pod_startup_times", lambda *_args: None
    )
    monkeypatch.setattr(visualize_metrics, "plot_replica_status", lambda *_args: None)

    count = visualize_metrics.generate_all_visualizations(str(tmp_path))

    assert plotted == ["vllm:custom_metric"]
    assert count == 3


def test_report_includes_configured_custom_metric(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    (processed_dir / "time_series_metrics.json").write_text(
        '["vllm:custom_metric"]', encoding="utf-8"
    )
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps(
            {
                "pod-1": {
                    "metrics": {
                        "vllm:custom_metric": {
                            "mean": 42.0,
                            "p50": 42.0,
                            "p99": 42.0,
                            "stddev": 0.0,
                            "unit": "requests",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    report = add_metrics_to_benchmark_report({}, str(tmp_path))
    metric = report["results"]["observability"]["vllm_custom_metric"]

    assert metric["components"][0]["statistics"]["mean"] == 42.0
    assert metric["components"][0]["statistics"]["units"] == "requests"
    assert metric["components"][0]["statistics"]["graph_path"].endswith(
        "vllm_custom_metric.png"
    )
