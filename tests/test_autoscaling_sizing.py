"""Replica sizing for a traffic shape (llmdbenchmark/run/autoscaling_sizing.py).

Behavior under test:
- demand is max over AverageValue triggers of ceil(value / threshold),
  clamped to [minReplicaCount, maxReplicaCount], with the driving trigger and
  a cap that bound the peak both reported;
- a missing sample counts as 0, as KEDA's prometheus scaler treats it;
- the KEDA decision and the real pool are each compared with demand in
  replica-seconds, so a lagging scale-up shows as short and a lagging
  scale-down as over;
- stage windows come from the harness's v0.2 benchmark reports, plus the
  whole run from run_metadata.yaml;
- step 09a's range-query helper parses Thanos and fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from llmdbenchmark.run.autoscaling_sizing import (
    STEP_SECONDS,
    Stage,
    size_stage,
    stage_windows,
    write_sizing,
)
from llmdbenchmark.run.steps.step_09a_capture_cluster_state import _thanos_range

T0 = 1_800_000_000.0


def _so(max_replicas=10, annotations=None):
    return {
        "kind": "ScaledObject",
        "metadata": {
            "name": "m-decode-saturation",
            "namespace": "bench",
            "annotations": annotations
            if annotations is not None
            else {"llm-d.ai/autoscaling-dry-run-target": "Deployment/m-decode"},
        },
        "spec": {
            "scaleTargetRef": {"name": "m-decode-dryrun"},
            "minReplicaCount": 1,
            "maxReplicaCount": max_replicas,
            "triggers": [
                {
                    "name": "pool-saturation",
                    "metadata": {"query": "sat", "threshold": "0.7"},
                },
                {
                    "name": "running-requests",
                    "metadata": {"query": "running", "threshold": "16"},
                },
            ],
        },
    }


def _fetch(series: dict[str, list[float]]):
    """Fake Thanos: the first key found in the PromQL returns its samples."""

    def fetch(query, start, end):
        for key, values in series.items():
            if key in query:
                return [(T0 + i * STEP_SECONDS, v) for i, v in enumerate(values)]
        return []

    return fetch


STAGE = Stage("stage 1", T0, T0 + 5 * STEP_SECONDS, request_rate=12.0)


def test_demand_is_the_highest_trigger_ask():
    row = size_stage(
        _so(),
        STAGE,
        _fetch({"sat": [0.1, 1.3, 0.1, 0.1, 0.1], "running": [10, 20, 48, 33, 0]}),
    )
    # per sample: max(ceil(sat/0.7), ceil(running/16)) clamped to >= 1
    assert row["demand"]["peak"] == 3
    assert row["demand"]["mean"] == round((1 + 2 + 3 + 3 + 1) / 5, 2)
    assert row["demand"]["drivenBy"] == {
        "trigger": "running-requests",
        "value": 48,
        "threshold": 16.0,
    }


def test_a_missing_sample_counts_as_zero():
    row = size_stage(_so(), STAGE, _fetch({"running": [64]}))
    assert row["demand"]["peak"] == 4  # the absent saturation series does not block


def test_the_max_replica_cap_is_reported():
    row = size_stage(_so(max_replicas=2), STAGE, _fetch({"running": [80]}))
    assert row["demand"]["peak"] == 2
    assert row["demand"]["unclampedPeak"] == 5


def test_decision_and_pool_are_compared_with_demand():
    row = size_stage(
        _so(),
        STAGE,
        _fetch(
            {
                "running": [16, 48, 48, 16, 16],  # demand 1 3 3 1 1
                "desired_replicas": [1, 2, 3, 3, 2],  # scale-up and scale-down lag
                "spec_replicas": [1, 1, 1, 1, 1],
            }
        ),
    )
    assert row["decision"]["vsDemand"] == {
        "overReplicaSeconds": 3 * STEP_SECONDS,
        "underReplicaSeconds": 1 * STEP_SECONDS,
        "underProvisionedFraction": 0.2,
    }
    assert row["provisioned"]["vsDemand"]["underReplicaSeconds"] == 4 * STEP_SECONDS
    assert row["realTarget"] == "Deployment/m-decode"
    assert row["dryRun"] is True


def test_a_live_scaled_object_sizes_its_own_target():
    row = size_stage(_so(annotations={}), STAGE, _fetch({"running": [16]}))
    assert row["realTarget"] == "Deployment/m-decode-dryrun"
    assert row["dryRun"] is False


def _report(path: Path, stage: int, start: str, end: str, rate: float):
    path.write_text(
        yaml.safe_dump(
            {
                "run": {"time": {"start": start, "end": end}},
                "results": {
                    "request_performance": {
                        "aggregate": {"throughput": {"request_rate": {"mean": rate}}}
                    }
                },
            }
        )
    )


def _results(tmp_path: Path) -> Path:
    results = tmp_path / "results"
    run = results / "exp_1"
    run.mkdir(parents=True)
    _report(
        run / "benchmark_report_v0.2,_stage_1_x.yaml",
        1,
        "2026-10-15T22:18:08Z",
        "2026-10-15T22:30:15Z",
        11.9,
    )
    _report(
        run / "benchmark_report_v0.2,_stage_0_x.yaml",
        0,
        "2026-10-15T22:16:00Z",
        "2026-10-15T22:18:04Z",
        1.0,
    )
    (run / "run_metadata.yaml").write_text(
        'harness_start: "2026-10-15T22:15:54+00:00"\nharness_stop: "2026-10-15T22:37:48+00:00"\n'
    )
    return results


def test_stage_windows_follow_the_benchmark_reports(tmp_path):
    stages = stage_windows(_results(tmp_path), "exp")
    assert [s.name for s in stages] == ["stage 0", "stage 1", "whole run"]
    assert stages[1].request_rate == 11.9
    assert stages[1].end - stages[1].start == 727
    assert stages[2].end - stages[2].start == 1314


def test_write_sizing_names_the_max_replicas(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "30_keda-scaledobject.yaml").write_text(yaml.safe_dump(_so()))
    out = tmp_path / "out"
    out.mkdir()
    written = write_sizing(
        stack, _results(tmp_path), "exp", out, _fetch({"running": [20, 50, 30]})
    )
    assert written == ["autoscaling-sizing.json", "autoscaling-sizing.txt"]
    text = (out / "autoscaling-sizing.txt").read_text()
    assert "Max replicas for this traffic shape: 4" in text
    assert "running-requests reached 50 against 16 per replica" in text
    rows = json.loads((out / "autoscaling-sizing.json").read_text())["rows"]
    assert {r["stage"] for r in rows} == {"stage 0", "stage 1", "whole run"}


def test_nothing_is_written_without_a_scaled_object(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    assert write_sizing(tmp_path, _results(tmp_path), "exp", out, _fetch({})) == []
    assert not list(out.iterdir())


@dataclass
class _Result:
    success: bool
    stdout: str


class _Cmd:
    def __init__(self, result):
        self.result, self.command, self.stdin = result, None, None

    def execute(self, command, check=False, stdin=None):
        self.command, self.stdin = command, stdin
        return self.result


def test_range_queries_parse_thanos_and_keep_the_token_off_the_command_line():
    body = {
        "status": "success",
        "data": {"result": [{"values": [[T0, "3"], [T0 + 15, "4.5"]]}]},
    }
    cmd = _Cmd(_Result(True, json.dumps(body)))
    assert _thanos_range(
        cmd, "thanos.example", "secret", 'max(x{a="b"})', T0, T0 + 15
    ) == [
        (T0, 3.0),
        (T0 + 15, 4.5),
    ]
    assert "secret" not in cmd.command and "secret" in cmd.stdin
    assert f"step={STEP_SECONDS}" in cmd.command


def test_a_failed_range_query_yields_no_samples():
    error = {"status": "error", "error": "bad query"}
    assert (
        _thanos_range(_Cmd(_Result(True, json.dumps(error))), "h", "t", "x", T0, T0)
        == []
    )
    assert _thanos_range(_Cmd(_Result(False, "")), "h", "t", "x", T0, T0) == []


def test_a_peak_between_stages_is_still_the_max(tmp_path):
    """A backlog draining between two stage reports can be the true peak."""
    from llmdbenchmark.run.autoscaling_sizing import explain

    def row(stage, peak, value):
        return {
            "scaledObject": "so",
            "realTarget": "Deployment/d",
            "dryRun": True,
            "stage": stage,
            "durationSeconds": 60,
            "requestRate": None,
            "minReplicas": 1,
            "maxReplicas": 10,
            "demand": {
                "peak": peak,
                "mean": 1.0,
                "unclampedPeak": peak,
                "drivenBy": {
                    "trigger": "running-requests",
                    "value": value,
                    "threshold": 16.0,
                },
            },
            "decision": {"peak": None, "mean": None},
            "provisioned": {"peak": None, "mean": None},
        }

    text = explain([row("stage 0", 4, 53), row("whole run", 5, 70)])
    assert "Max replicas for this traffic shape: 5 (between traffic stages" in text
    text = explain([row("stage 0", 5, 70), row("whole run", 5, 70)])
    assert "Max replicas for this traffic shape: 5 (in stage 0" in text
