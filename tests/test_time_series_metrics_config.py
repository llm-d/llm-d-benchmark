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


def _write_scrape(raw_dir: Path, pod: str, ts: str, lines: list[str]) -> None:
    (raw_dir / f"{pod}_{ts.replace(':', '').replace('-', '')}_metrics.log").write_text(
        f"# Timestamp: {ts}\n# Pod: {pod}\n# Namespace: bench\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def _summary_with(metrics: dict) -> str:
    return json.dumps({"pod-1": {"metrics": metrics}})


def test_report_embeds_time_series(tmp_path: Path) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()

    for ts, kv, mem in (
        ("2026-07-14T00:00:00Z", "0.10", "1000000000"),
        ("2026-07-14T00:00:30Z", "0.50", "2000000000"),
    ):
        _write_scrape(
            raw_dir,
            "pod-1",
            ts,
            [
                f"vllm:kv_cache_usage_perc {kv}",
                f"vllm:gpu_memory_usage_bytes {mem}",
            ],
        )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with(
            {
                "vllm:kv_cache_usage_perc": {
                    "mean": 0.3,
                    "p50": 0.3,
                    "p99": 0.5,
                    "stddev": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    obs = report["results"]["observability"]

    component = obs["components"][0]
    assert component["replica_id"] == "pod-1"
    ts_block = component["time_series"]

    assert ts_block["kv_cache_usage"]["units"] == "fraction"
    assert [p["value"] for p in ts_block["kv_cache_usage"]["series"]] == [0.10, 0.50]
    assert [p["ts"] for p in ts_block["kv_cache_usage"]["series"]] == [
        "2026-07-14T00:00:00+00:00",
        "2026-07-14T00:00:30+00:00",
    ]

    assert ts_block["gpu_memory_usage"]["units"] == "bytes"
    assert [p["value"] for p in ts_block["gpu_memory_usage"]["series"]] == [1e9, 2e9]

    assert obs["vllm_kv_cache_usage_perc"]["components"][0]["statistics"]["p99"] == 0.5


def test_embedded_time_series_validates_under_v0_2(tmp_path: Path) -> None:
    """The embedded block must satisfy the v0.2 schema, not just extra="allow"."""
    from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import Observability

    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()

    for ts, kv, util, power in (
        ("2026-07-14T00:00:00Z", "0.10", "42", "250.5"),
        ("2026-07-14T00:00:30Z", "0.50", "77", "310.0"),
    ):
        _write_scrape(
            raw_dir,
            "qwen-decode-abc",
            ts,
            [
                f"vllm:kv_cache_usage_perc {kv}",
                f"DCGM_FI_DEV_GPU_UTIL {util}",
                f"DCGM_FI_DEV_POWER_USAGE {power}",
            ],
        )
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps({"qwen-decode-abc": {"metrics": {}}}), encoding="utf-8"
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    observability = Observability(**report["results"]["observability"])

    component = observability.components[0]
    assert component.component_label == "decode-engine"
    assert component.replica_id == "qwen-decode-abc"
    populated = {
        field
        for field, value in component.time_series.model_dump().items()
        if value is not None
    }
    assert populated == {"kv_cache_usage", "gpu_utilization", "power_consumption"}


def test_embedded_time_series_covers_serving_metrics(tmp_path: Path) -> None:
    """Scheduling, prefix-cache and pool fields embed and validate under v0.2.1."""
    from llmdbenchmark.analysis.benchmark_report.schema_v0_2_1 import Observability

    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()

    for ts, running, waiting, hits, queries in (
        ("2026-07-14T00:00:00Z", "3", "1", "10", "100"),
        ("2026-07-14T00:00:30Z", "5", "2", "40", "200"),
    ):
        _write_scrape(
            raw_dir,
            "qwen-decode-abc",
            ts,
            [
                f"vllm:num_requests_running {running}",
                f"vllm:num_requests_waiting {waiting}",
                "vllm:num_preemptions_total 2",
                f"vllm:prefix_cache_hits_total {hits}",
                f"vllm:prefix_cache_queries_total {queries}",
                "vllm:prompt_tokens_total 5000",
                "vllm:generation_tokens_total 1200",
            ],
        )
        _write_scrape(
            raw_dir,
            "qwen-router-epp-xyz",
            ts,
            [
                "inference_pool_average_kv_cache_utilization 0.25",
                f"inference_pool_average_queue_size {waiting}",
                f"inference_pool_average_running_requests {running}",
                "inference_pool_ready_pods 1",
            ],
        )
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps({"qwen-decode-abc": {"metrics": {}}}), encoding="utf-8"
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    observability = Observability(**report["results"]["observability"])
    by_replica = {c.replica_id: c for c in observability.components}

    decode = by_replica["qwen-decode-abc"].time_series
    assert [p.value for p in decode.num_requests_running.series] == [3.0, 5.0]
    assert [p.value for p in decode.num_requests_waiting.series] == [1.0, 2.0]
    assert decode.num_requests_running.units == "count"
    assert decode.prompt_tokens.series[0].value == 5000.0
    assert decode.generation_tokens.series[0].value == 1200.0
    # Derived from the counters, since vLLM v1 exposes no hit-rate gauge.
    assert decode.prefix_cache_hit_rate.units == "percent"
    assert [p.value for p in decode.prefix_cache_hit_rate.series] == [10.0, 20.0]

    epp = by_replica["qwen-router-epp-xyz"].time_series
    assert epp.pool_avg_kv_cache_utilization.units == "fraction"
    assert epp.pool_avg_queue_size.units == "count"
    assert [p.value for p in epp.pool_ready_pods.series] == [1.0, 1.0]


def _metrics_dir_with(tmp_path: Path, lines: list[str]) -> Path:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    _write_scrape(raw_dir, "qwen-decode-abc", "2026-07-14T00:00:00Z", lines)
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps({"qwen-decode-abc": {"metrics": {}}}), encoding="utf-8"
    )
    return metrics_dir


def test_v0_2_1_field_bumps_declared_version(tmp_path: Path) -> None:
    """Embedding a v0.2.1-only field must bump the report's declared version."""
    metrics_dir = _metrics_dir_with(
        tmp_path, ["vllm:kv_cache_usage_perc 0.10", "vllm:num_requests_running 3"]
    )

    report = add_metrics_to_benchmark_report({"version": "0.2"}, str(metrics_dir))

    assert report["version"] == "0.2.1"


def test_v0_2_only_fields_keep_declared_version(tmp_path: Path) -> None:
    """A report with only v0.2 hardware fields must stay at v0.2."""
    metrics_dir = _metrics_dir_with(tmp_path, ["vllm:kv_cache_usage_perc 0.10"])

    report = add_metrics_to_benchmark_report({"version": "0.2"}, str(metrics_dir))

    ts = report["results"]["observability"]["components"][0]["time_series"]
    assert set(ts) == {"kv_cache_usage"}
    assert report["version"] == "0.2"


def test_report_time_series_disabled_by_env(tmp_path: Path, monkeypatch) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    _write_scrape(
        raw_dir, "pod-1", "2026-07-14T00:00:00Z", ["vllm:kv_cache_usage_perc 0.10"]
    )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with({"vllm:kv_cache_usage_perc": {"mean": 0.1}}), encoding="utf-8"
    )

    monkeypatch.setenv("METRICS_EMBED_TIME_SERIES", "false")
    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    obs = report["results"]["observability"]
    assert "components" not in obs
    assert obs["vllm_kv_cache_usage_perc"]["components"][0]["statistics"]["mean"] == 0.1


def test_report_time_series_downsampled(tmp_path: Path, monkeypatch) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    for i in range(50):
        _write_scrape(
            raw_dir,
            "pod-1",
            f"2026-07-14T00:{i // 60:02d}:{i % 60:02d}Z",
            [f"vllm:kv_cache_usage_perc {i / 100:.2f}"],
        )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with({"vllm:kv_cache_usage_perc": {"mean": 0.25}}), encoding="utf-8"
    )

    monkeypatch.setenv("METRICS_TS_MAX_POINTS", "10")
    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    series = report["results"]["observability"]["components"][0]["time_series"][
        "kv_cache_usage"
    ]["series"]
    assert len(series) <= 10
    assert series[0]["value"] == 0.0
    assert series[-1]["value"] == 0.49
