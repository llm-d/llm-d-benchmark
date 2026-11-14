"""Stage windows from inference-perf's scraped stamps, with the log as fallback.

stdout.log markers are cut to the second; the harness scrape carries the Unix
times inference-perf stamped, so a stage it covers takes those bounds.
"""

from datetime import datetime, timezone

import yaml

from llmdbenchmark.analysis.metrics_embed import (
    embed_metrics,
    scraped_stage_windows,
    stage_windows,
)

LOG = (
    "2026-08-18 10:00:00,900 INFO Stage 0 - run started\n"
    "2026-08-18 10:01:00,900 INFO Stage 0 - run completed\n"
    "2026-08-18 10:02:00,900 INFO Stage 1 - run started\n"
    "2026-08-18 10:03:00,900 INFO Stage 1 - run completed\n"
)
S0_START = 1787047200.9  # 2026-08-18 10:00:00.9 UTC
S0_END = 1787047260.9


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def _scrape(raw, name, source, lines):
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(
        "# Timestamp: 2026-08-18T10:04:00Z\n"
        "# Pod: harness\n"
        "# PodIP: 127.0.0.1\n"
        f"# Source: {source}\n"
        "\n" + "".join(f"{line}\n" for line in lines),
        encoding="utf-8",
    )


def _results(tmp_path, stamps):
    results = tmp_path / "results"
    results.mkdir()
    (results / "stdout.log").write_text(LOG, encoding="utf-8")
    _scrape(
        results / "metrics" / "raw",
        "harness_1_metrics.log",
        "harness_prometheus_metrics",
        stamps,
    )
    return results


def test_scraped_stamps_replace_the_log_markers(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_END}',
        ],
    )

    windows = stage_windows(results, results / "metrics")

    assert windows[0] == (_utc(S0_START), _utc(S0_END))
    # Stage 1 has no stamps, so it keeps the log's whole seconds.
    assert windows[1] == (
        datetime(2026, 8, 18, 10, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 10, 3, tzinfo=timezone.utc),
    )


def test_without_a_metrics_dir_only_the_log_is_read(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_END}',
        ],
    )

    assert stage_windows(results)[0][0] == datetime(
        2026, 8, 18, 10, 0, tzinfo=timezone.utc
    )


def test_a_stage_still_running_at_the_last_scrape_falls_back_to_the_log(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="1"}} {S0_START + 120}',
        ],
    )

    assert scraped_stage_windows(results / "metrics") == {}
    assert stage_windows(results, results / "metrics")[1][0] == datetime(
        2026, 8, 18, 10, 2, tzinfo=timezone.utc
    )


def test_a_restarted_stage_takes_its_latest_stamps(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_START + 5}',
        ],
    )
    _scrape(
        results / "metrics" / "raw",
        "harness_2_metrics.log",
        "harness_prometheus_metrics",
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START + 10}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_END}',
        ],
    )

    assert scraped_stage_windows(results / "metrics") == {
        0: (_utc(S0_START + 10), _utc(S0_END))
    }


def test_a_stage_restarted_after_its_last_scraped_end_falls_back_to_the_log(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START + 10}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_START + 5}',
        ],
    )

    assert scraped_stage_windows(results / "metrics") == {}


def test_a_non_finite_stamp_is_ignored(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            'inference_perf_stage_end_timestamp_seconds{stage="0"} +Inf',
        ],
    )

    assert scraped_stage_windows(results / "metrics") == {}


def test_only_the_harness_scrape_is_read(tmp_path):
    results = _results(tmp_path, [])
    _scrape(
        results / "metrics" / "raw",
        "vllm-0_1_metrics.log",
        "vllm_prometheus_metrics",
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_END}',
        ],
    )

    assert scraped_stage_windows(results / "metrics") == {}


def test_embedding_clips_to_the_scraped_window(tmp_path):
    results = _results(
        tmp_path,
        [
            f'inference_perf_stage_start_timestamp_seconds{{stage="0"}} {S0_START}',
            f'inference_perf_stage_end_timestamp_seconds{{stage="0"}} {S0_END}',
        ],
    )
    (results / "metrics" / "processed").mkdir()
    (results / "metrics" / "processed" / "metrics_summary.json").write_text(
        '{"_aggregated": {"metrics": {}}}', encoding="utf-8"
    )
    report = results / "benchmark_report_v0.2,_stage_0.json.yaml"
    report.write_text("run:\n  uid: x\nresults: {}\n", encoding="utf-8")

    assert embed_metrics(results / "metrics", results, log=None) == 1

    interval = yaml.safe_load(report.read_text())["results"]["observability"][
        "time_series_interval"
    ]
    assert interval["start"] == _utc(S0_START).isoformat()
    assert interval["end"] == _utc(S0_END).isoformat()
