"""Harness side of time-series metrics: metric selection in process_metrics.py,
stage windows in llmdbenchmark.analysis and scrape parsing in embed_metrics.
"""

from __future__ import annotations

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path


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


def _write_scrape(raw_dir: Path, pod: str, ts: str, lines: list[str]) -> None:
    (raw_dir / f"{pod}_{ts.replace(':', '').replace('-', '')}_metrics.log").write_text(
        f"# Timestamp: {ts}\n# Pod: {pod}\n# Namespace: bench\n"
        + "\n".join(lines)
        + "\n",
        encoding="utf-8",
    )


def _summary_with(metrics: dict) -> str:
    return json.dumps({"pod-1": {"metrics": metrics}})


def test_stage_windows_parses_every_marker_variant(tmp_path: Path) -> None:
    """Rate-based, session-based and failed stages all yield a window.

    The marker strings are a contract with inference-perf's load generator.
    """
    from llmdbenchmark.analysis import _stage_windows

    prefix = "inference_perf.loadgen.load_generator - INFO -"
    (tmp_path / "stdout.log").write_text(
        f"2026-08-11 11:51:19,607 - {prefix} Stage 0 - run started\n"
        f"2026-08-11 11:51:57,120 - {prefix} Stage 0 - run completed\n"
        f"2026-08-11 11:52:00,001 - {prefix} Stage 1 - session-based run started\n"
        f"2026-08-11 11:52:40,002 - {prefix} Stage 1 - session-based run completed\n"
        f"2026-08-11 11:53:00,003 - {prefix} Stage 2 - run started\n"
        f"2026-08-11 11:53:30,004 - {prefix} Stage 2 - run failed\n"
        f"2026-08-11 11:54:00,005 - {prefix} Stage 3 - run started\n",
        encoding="utf-8",
    )

    windows = _stage_windows(tmp_path)

    # Stage 3 never terminated, so it keeps the whole-run series.
    assert sorted(windows) == [0, 1, 2]
    assert windows[1] == (
        datetime(2026, 8, 11, 11, 52, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 11, 11, 52, 40, tzinfo=timezone.utc),
    )
    assert windows[2][1] == datetime(2026, 8, 11, 11, 53, 30, tzinfo=timezone.utc)


def test_stage_windows_missing_log_returns_empty(tmp_path: Path) -> None:
    """No stdout.log leaves the caller on the whole-run series."""
    from llmdbenchmark.analysis import _stage_windows

    assert _stage_windows(tmp_path) == {}


def test_stage_windows_rejects_inverted_window(tmp_path: Path) -> None:
    """A retry that never completed leaves an end before its start.

    Each event is last-write-wins, so the second "started" overwrites the first
    while "completed" still holds the first attempt's stamp.
    """
    from llmdbenchmark.analysis import _stage_windows

    prefix = "inference_perf.loadgen.load_generator - INFO -"
    (tmp_path / "stdout.log").write_text(
        f"2026-08-11 11:51:19,607 - {prefix} Stage 0 - run started\n"
        f"2026-08-11 11:51:57,120 - {prefix} Stage 0 - run completed\n"
        f"2026-08-11 11:59:00,001 - {prefix} Stage 0 - run started\n",
        encoding="utf-8",
    )

    assert _stage_windows(tmp_path) == {}


def test_report_stage_index_matches_native_idiom() -> None:
    """Stage extraction must agree with native_to_br0_2, which names the reports."""
    from llmdbenchmark.analysis import _REPORT_STAGE_RE

    for name, expected in (
        ("benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml", 0),
        ("benchmark_report_v0.2,_stage_10_lifecycle_metrics.json.yaml", 10),
        ("benchmark_report_v0.2,_stage_0_session_lifecycle_metrics.json.yaml", 0),
        ("x_stage_2_stage_5.yaml", 5),
    ):
        match = _REPORT_STAGE_RE.match(name)
        assert match and int(match.group(1)) == expected, name

    assert _REPORT_STAGE_RE.match("benchmark_report_v0.2,_results.json.yaml") is None


def test_stage_reports_parse_the_scrapes_once(tmp_path: Path, monkeypatch) -> None:
    from llmdbenchmark.analysis.benchmark_report import timeseries
    from llmdbenchmark.analysis.metrics_embed import embed_metrics

    metrics_dir = tmp_path / "metrics"
    raw_dir = metrics_dir / "raw"
    raw_dir.mkdir(parents=True)
    (metrics_dir / "processed").mkdir()
    for ts in ("2026-08-18T10:00:30Z", "2026-08-18T10:02:30Z"):
        _write_scrape(raw_dir, "pod-1", ts, ["vllm:num_requests_running 1"])
    (metrics_dir / "processed" / "metrics_summary.json").write_text(
        _summary_with({"vllm:num_requests_running": {"mean": 1.0}}), encoding="utf-8"
    )
    (tmp_path / "stdout.log").write_text(
        "2026-08-18 10:00:00,1 INFO Stage 0 - run started\n"
        "2026-08-18 10:01:00,1 INFO Stage 0 - run completed\n"
        "2026-08-18 10:02:00,1 INFO Stage 1 - run started\n"
        "2026-08-18 10:03:00,1 INFO Stage 1 - run completed\n",
        encoding="utf-8",
    )
    for stage in (0, 1):
        (tmp_path / f"benchmark_report_v0.2,_stage_{stage}.json.yaml").write_text(
            "run:\n  uid: x\nresults: {}\n", encoding="utf-8"
        )
    calls = []
    original = timeseries.collect_time_series_data
    monkeypatch.setattr(
        timeseries,
        "collect_time_series_data",
        lambda *args, **kwargs: calls.append(args) or original(*args, **kwargs),
    )

    assert embed_metrics(metrics_dir, tmp_path, log=None) == 2
    assert len(calls) == 1
