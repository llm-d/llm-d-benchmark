"""Tests for configurable Prometheus time-series metric selection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llmd_benchmark_report.metrics_processor import (
    add_metrics_to_benchmark_report,
)


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

    # Statistics come from the same scrapes as the series: p99 of [0.10, 0.50].
    stats = obs["vllm_kv_cache_usage_perc"]["components"][0]["statistics"]
    assert stats["p99"] == pytest.approx(0.496)
    assert obs["time_series_interval"]["statistics_scope"] == "run"


def test_embedded_time_series_validates_under_v0_2(tmp_path: Path) -> None:
    """The embedded block must satisfy the v0.2 schema, not just extra="allow"."""
    from llmd_benchmark_report.schema_v0_2 import Observability

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
    from llmd_benchmark_report.schema_v0_2 import Observability

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
    # Derived from the counters, since vLLM v1 exposes no hit-rate gauge, and
    # from their deltas, since a cache reset leaves the counters running.
    assert decode.prefix_cache_hit_rate.units == "percent"
    assert [p.value for p in decode.prefix_cache_hit_rate.series] == [30.0]

    epp = by_replica["qwen-router-epp-xyz"].time_series
    assert epp.pool_avg_kv_cache_utilization.units == "fraction"
    assert epp.pool_avg_queue_size.units == "count"
    assert [p.value for p in epp.pool_ready_pods.series] == [1.0, 1.0]


def test_embedded_time_series_splits_kv_offload_by_direction(tmp_path: Path) -> None:
    """Both transfer_type directions embed separately, not as one interleaved series."""
    from llmd_benchmark_report.schema_v0_2_1 import Observability

    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()

    for ts, store_bytes, load_bytes, store_time, load_time in (
        ("2026-07-14T00:00:00Z", "1000", "10", "1.5", "0.5"),
        ("2026-07-14T00:00:30Z", "4000", "40", "3.0", "1.0"),
    ):
        _write_scrape(
            raw_dir,
            "qwen-decode-abc",
            ts,
            [
                f'vllm:kv_offload_total_bytes_total{{engine="0",transfer_type="GPU_to_CPU"}} {store_bytes}',
                f'vllm:kv_offload_total_bytes_total{{engine="0",transfer_type="CPU_to_GPU"}} {load_bytes}',
                f'vllm:kv_offload_total_time_total{{engine="0",transfer_type="GPU_to_CPU"}} {store_time}',
                f'vllm:kv_offload_total_time_total{{engine="0",transfer_type="CPU_to_GPU"}} {load_time}',
            ],
        )
    (processed_dir / "metrics_summary.json").write_text(
        json.dumps({"qwen-decode-abc": {"metrics": {}}}), encoding="utf-8"
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    observability = Observability(**report["results"]["observability"])
    decode = observability.components[0].time_series

    assert [p.value for p in decode.kv_offload_store_bytes.series] == [1000.0, 4000.0]
    assert [p.value for p in decode.kv_offload_load_bytes.series] == [10.0, 40.0]
    assert [p.value for p in decode.kv_offload_store_time.series] == [1.5, 3.0]
    assert [p.value for p in decode.kv_offload_load_time.series] == [0.5, 1.0]
    assert decode.kv_offload_store_bytes.units == "bytes"
    assert decode.kv_offload_store_time.units == "s"


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
    assert obs["time_series_interval"]["scope"] == "disabled"


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


def _windowed_series(tmp_path: Path, window) -> list[dict]:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    for i in range(6):
        _write_scrape(
            raw_dir,
            "pod-1",
            f"2026-07-14T00:00:{i:02d}Z",
            [f"vllm:kv_cache_usage_perc {i / 100:.2f}"],
        )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with({"vllm:kv_cache_usage_perc": {"mean": 0.03}}), encoding="utf-8"
    )
    report = add_metrics_to_benchmark_report(
        {}, str(metrics_dir), time_series_window=window
    )
    return report["results"]["observability"]["components"][0]["time_series"][
        "kv_cache_usage"
    ]["series"]


def test_report_time_series_clipped_to_window(tmp_path: Path) -> None:
    """A stage window keeps only that stage's samples, not the whole run's.

    Half-open, so the sample at the end bound belongs to the next stage alone.
    """
    window = (
        datetime(2026, 7, 14, 0, 0, 2, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 0, 0, 4, tzinfo=timezone.utc),
    )
    assert [p["value"] for p in _windowed_series(tmp_path, window)] == [0.02, 0.03]


def test_report_version_stable_when_window_clips_everything(tmp_path: Path) -> None:
    """An empty clip must not change the declared version.

    Otherwise sibling reports in one sweep disagree, and a short stage validates
    against the stricter v0.2 model while its neighbours declare v0.2.1.
    """
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    _write_scrape(
        raw_dir,
        "pod-1",
        "2026-07-14T00:00:00Z",
        ["vllm:num_requests_running 3"],
    )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with({"vllm:num_requests_running": {"mean": 3.0}}), encoding="utf-8"
    )
    empty = (
        datetime(2026, 7, 14, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 1, 0, 5, tzinfo=timezone.utc),
    )

    clipped = add_metrics_to_benchmark_report(
        {"version": "0.2"}, str(metrics_dir), time_series_window=empty
    )
    whole_run = add_metrics_to_benchmark_report({"version": "0.2"}, str(metrics_dir))

    assert clipped["version"] == whole_run["version"] == "0.2.1"
    interval = clipped["results"]["observability"]["time_series_interval"]
    assert interval["scope"] == "stage"
    assert interval["datapoints"] == 0
    assert interval["datapoints_available"] == 1
    assert interval["scraped_from"] == "2026-07-14T00:00:00+00:00"
    assert (
        whole_run["results"]["observability"]["time_series_interval"]["scope"] == "run"
    )


def test_stage_statistics_cover_the_stage_and_weight_the_hit_rate(
    tmp_path: Path,
) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    # Before the stage the pod served 50,000 queries without a hit. In the
    # stage, a quiet interval (10 queries, 0 hits) is followed by a busy one
    # (10,000 queries, 9,000 hits).
    scrapes = (
        ("2026-07-14T00:00:00Z", 50_000, 0, 7),
        ("2026-07-14T00:01:00Z", 50_000, 0, 1),
        ("2026-07-14T00:01:30Z", 50_010, 0, 2),
        ("2026-07-14T00:02:00Z", 60_010, 9_000, 3),
    )
    for ts, queries, hits, running in scrapes:
        _write_scrape(
            raw_dir,
            "pod-1",
            ts,
            [
                f"vllm:prefix_cache_queries_total {queries}",
                f"vllm:prefix_cache_hits_total {hits}",
                f"vllm:num_requests_running {running}",
            ],
        )
    (processed_dir / "time_series_metrics.json").write_text(
        '["vllm:prefix_cache_hit_rate", "vllm:num_requests_running"]',
        encoding="utf-8",
    )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with(
            {
                "vllm:prefix_cache_hit_rate": {"mean": 4.2, "unit": "%"},
                "vllm:num_requests_running": {"mean": 3.25, "unit": "requests"},
            }
        ),
        encoding="utf-8",
    )
    window = (
        datetime(2026, 7, 14, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 0, 3, tzinfo=timezone.utc),
    )

    report = add_metrics_to_benchmark_report(
        {}, str(metrics_dir), time_series_window=window
    )
    obs = report["results"]["observability"]

    assert obs["time_series_interval"]["statistics_scope"] == "stage"
    hit_rate = obs["vllm_prefix_cache_hit_rate"]["components"][0]["statistics"]
    assert hit_rate["mean"] == pytest.approx(9_000 / 10_010 * 100)
    running = obs["vllm_num_requests_running"]["components"][0]["statistics"]
    assert running["mean"] == pytest.approx(2.0)


def test_hit_rate_sums_the_engines_of_a_pod(tmp_path: Path) -> None:
    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    processed_dir = metrics_dir / "processed"
    raw_dir.mkdir(parents=True)
    processed_dir.mkdir()
    # Engine 0 hits 90 of 100 queries, engine 1 hits 0 of 100.
    for ts, queries, hits in (
        ("2026-07-14T00:00:00Z", 1000, 0),
        ("2026-07-14T00:01:00Z", 1100, 90),
    ):
        _write_scrape(
            raw_dir,
            "pod-1",
            ts,
            [
                f'vllm:prefix_cache_queries_total{{engine="0"}} {queries}',
                f'vllm:prefix_cache_hits_total{{engine="0"}} {hits}',
                f'vllm:prefix_cache_queries_total{{engine="1"}} {queries}',
                'vllm:prefix_cache_hits_total{engine="1"} 0',
            ],
        )
    (processed_dir / "time_series_metrics.json").write_text(
        '["vllm:prefix_cache_hit_rate"]', encoding="utf-8"
    )
    (processed_dir / "metrics_summary.json").write_text(
        _summary_with({"vllm:prefix_cache_hit_rate": {"mean": 0.0, "unit": "%"}}),
        encoding="utf-8",
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))
    obs = report["results"]["observability"]

    stats = obs["vllm_prefix_cache_hit_rate"]["components"][0]["statistics"]
    assert stats["mean"] == pytest.approx(45.0)
    series = obs["components"][0]["time_series"]["prefix_cache_hit_rate"]["series"]
    assert [p["value"] for p in series] == [pytest.approx(45.0)]
