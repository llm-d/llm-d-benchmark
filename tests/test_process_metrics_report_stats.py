"""Aggregates that process_metrics.py writes into pod_startup_times.json and
replica_status.json are embedded into the v0.2 benchmark report as
``Statistics``, which requires ``units``."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import (
    PodStartupTimes,
    Statistics,
)


def test_embedded_aggregates_validate_as_statistics(
    tmp_path: Path, monkeypatch
) -> None:
    processed = tmp_path / "metrics" / "processed"
    processed.mkdir(parents=True)
    snapshot = {"controllers": [{"name": "m-decode", "ready_replicas": 2}]}
    (processed / "replica_status.json").write_text(json.dumps(snapshot))
    (processed / "replica_status_timeseries.json").write_text(
        json.dumps({"snapshots": [snapshot, snapshot]})
    )
    (processed / "pod_startup_times.json").write_text(
        json.dumps(
            {
                "pods": [
                    {"name": f"m-decode-{i}", "startup_seconds": 70.0 + i}
                    for i in range(2)
                ]
            }
        )
    )
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))

    module = runpy.run_path(
        "workload/harnesses/process_metrics.py", run_name="process_metrics_test"
    )
    module["aggregate_pod_startup_stats"]()
    module["aggregate_replica_stats"]()

    startup = json.loads((processed / "pod_startup_times.json").read_text())[
        "aggregate"
    ]
    replicas = json.loads((processed / "replica_status.json").read_text())[
        "aggregate_ready_replicas"
    ]
    assert Statistics.model_validate(startup).mean == 70.5
    assert Statistics.model_validate(replicas).mean == 2


def test_requester_runtime_aggregate_validates_in_pod_startup_times(
    tmp_path: Path, monkeypatch
) -> None:
    processed = tmp_path / "metrics" / "processed"
    processed.mkdir(parents=True)
    (processed / "replica_status_timeseries.json").write_text(
        json.dumps({"snapshots": [{"timestamp": "2026-10-01T00:01:00Z"}]})
    )
    (processed / "pod_startup_times.json").write_text(
        json.dumps(
            {
                "pods": [
                    {
                        "name": "fma-requester-0",
                        "role": "requester",
                        "ready_timestamp": "2026-10-01T00:05:30Z",
                        "startup_seconds": 30.0,
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))

    module = runpy.run_path(
        "workload/harnesses/process_metrics.py", run_name="process_metrics_test"
    )
    module["aggregate_pod_startup_stats"]()
    module["aggregate_requester_startup_stats"]()

    startup = PodStartupTimes.model_validate(
        json.loads((processed / "pod_startup_times.json").read_text())
    )
    assert startup.requester_runtime_aggregate.mean == 30.0
