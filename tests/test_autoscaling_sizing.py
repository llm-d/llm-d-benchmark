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
    explain_pd_ratio,
    pd_ratio,
    peak_decode_throughput,
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


# --- prefill:decode ratio (rate matching) -----------------------------------


def _stage(name, seconds, tokens_in, tokens_out, start=T0):
    return Stage(name, start, start + seconds, None, tokens_in, tokens_out)


def _pd_config(v_p=10000, v_d=None, decode_replicas=1):
    so = {"peakPrefillThroughput": v_p}
    if v_d is not None:
        so["peakDecodeThroughput"] = v_d
    return {
        "eppKedaSaturation": {"scaledObject": so},
        "decode": {"replicas": decode_replicas},
    }


def test_pools_are_rate_matched_per_phase():
    """10 req/s, ISL 2000, OSL 200: 20000 in / 2000 out tok/s."""
    pd = pd_ratio(
        _pd_config(v_p=10000, v_d=400), [_stage("stage 0", 60, 20000, 2000)], []
    )
    stage = pd["stages"][0]
    assert (stage["prefillReplicas"], stage["decodeReplicas"]) == (2, 5)
    assert stage["decodePerPrefill"] == 2.5
    assert pd["vDSource"] == "peakDecodeThroughput"


def test_the_ratio_depends_on_token_shape_not_rate():
    stages = [
        _stage("stage 0", 60, 20000, 2000),
        _stage("stage 1", 60, 60000, 6000, T0 + 60),
    ]
    pd = pd_ratio(_pd_config(v_p=10000, v_d=400), stages, [])
    assert [s["decodePerPrefill"] for s in pd["stages"]] == [2.5, 2.5]
    assert pd["peak"] == {"prefillReplicas": 6, "decodeReplicas": 15}


def test_the_whole_run_is_token_weighted():
    # 300 s prefill-heavy, 100 s decode-heavy: total tokens / total time.
    stages = [
        _stage("stage 0", 300, 40000, 400),
        _stage("stage 1", 100, 4000, 4000, T0 + 300),
    ]
    whole = pd_ratio(_pd_config(v_p=10000, v_d=400), stages, [])["wholeRun"]
    assert whole["inputTokenRate"] == (40000 * 300 + 4000 * 100) / 400
    assert whole["outputTokenRate"] == (400 * 300 + 4000 * 100) / 400


def test_without_peak_decode_throughput_the_observed_rate_is_a_lower_bound():
    rows = [
        {
            "stage": "stage 0",
            "realTarget": "Deployment/m-decode",
            "provisioned": {"mean": 2.0},
            "demand": {"peak": 1, "mean": 1.0},
        }
    ]
    pd = pd_ratio(_pd_config(v_p=10000), [_stage("stage 0", 60, 20000, 1000)], rows)
    assert pd["vD"] == 500.0  # 1000 out tok/s over 2 running decode replicas
    assert pd["vDSource"] == "observed (lower bound)"
    assert "! V_D was not set" in explain_pd_ratio(pd)


def test_no_ratio_without_a_calibrated_prefill_throughput():
    assert pd_ratio({}, [_stage("stage 0", 60, 20000, 2000)], []) is None


def test_the_triggers_view_reports_each_role():
    def row(target, peak):
        return {
            "stage": "stage 0",
            "realTarget": target,
            "demand": {"peak": peak, "mean": peak},
        }

    pd = pd_ratio(
        _pd_config(v_p=10000, v_d=400),
        [_stage("stage 0", 60, 20000, 2000)],
        [row("Deployment/m-prefill", 3), row("Deployment/m-decode", 4)],
    )
    assert pd["triggers"] == [
        {
            "stage": "stage 0",
            "prefillPeak": 3,
            "decodePeak": 4,
            "prefillMean": 3,
            "decodeMean": 4,
        }
    ]


def test_write_sizing_adds_the_ratio(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "30_keda-scaledobject.yaml").write_text(yaml.safe_dump(_so()))
    (stack / "config.yaml").write_text(yaml.safe_dump(_pd_config(v_p=1000, v_d=100)))
    results = _results(tmp_path)
    report = next(results.glob("exp_1/*stage_1*"))
    data = yaml.safe_load(report.read_text())
    data["results"]["request_performance"]["aggregate"]["throughput"].update(
        {"input_token_rate": {"mean": 3000.0}, "output_token_rate": {"mean": 600.0}}
    )
    report.write_text(yaml.safe_dump(data))
    out = tmp_path / "out"
    out.mkdir()
    write_sizing(stack, results, "exp", out, _fetch({"running": [20]}))
    text = (out / "autoscaling-sizing.txt").read_text()
    assert "Ideal ratio for this traffic: 1 prefill : 2 decode" in text
    assert json.loads((out / "autoscaling-sizing.json").read_text())["pdRatio"][
        "peak"
    ] == {
        "prefillReplicas": 3,
        "decodeReplicas": 6,
    }


# --- peakDecodeThroughput (make calibrate-peak-decode) ----------------------


def _ladder(*points):
    """Stages of (output tok/s, p90 TPOT seconds) at increasing rate."""
    return [
        Stage(f"stage {i}", T0 + i * 60, T0 + (i + 1) * 60, None, 1.0, out, tpot)
        for i, (out, tpot) in enumerate(points)
    ] + [Stage("whole run", T0, T0 + 60 * len(points), None, 1.0, 9e9, 0.0)]


def test_v_d_is_the_busiest_stage_within_the_tpot_target():
    # 2 decode replicas; the 4th stage is past saturation (TPOT broke the target).
    v_d, stage = peak_decode_throughput(
        _ladder((400, 0.03), (800, 0.05), (1500, 0.09), (1600, 0.2)), 2, 0.1
    )
    assert (v_d, stage.name) == (750.0, "stage 2")


def test_no_v_d_when_no_stage_met_the_target():
    assert peak_decode_throughput(_ladder((400, 0.3)), 1, 0.1) is None
    assert peak_decode_throughput(_ladder((400, 0.03)), 0, 0.1) is None


def test_stage_windows_read_p90_tpot(tmp_path):
    results = _results(tmp_path)
    report = next(results.glob("exp_1/*stage_1*"))
    data = yaml.safe_load(report.read_text())
    data["results"]["request_performance"]["aggregate"]["latency"] = {
        "time_per_output_token": {"p90": 0.042}
    }
    report.write_text(yaml.safe_dump(data))
    assert stage_windows(results, "exp")[1].tpot_p90 == 0.042


def _treatment(results: Path, exp_id: str, start: str, end: str, rate: float):
    run = results / f"{exp_id}_1"
    run.mkdir(parents=True)
    _report(run / "benchmark_report_v0.2,_stage_0_x.yaml", 0, start, end, rate)
    (run / "run_metadata.yaml").write_text(
        f'harness_start: "{start}"\nharness_stop: "{end}"\n'
    )


def test_every_treatment_of_a_run_is_sized(tmp_path):
    """Each treatment has its own experiment id; all of them make up the run."""
    results = tmp_path / "results"
    _treatment(results, "ip-a-1", "2026-10-15T22:00:00Z", "2026-10-15T22:05:00Z", 1.0)
    _treatment(results, "ip-b-2", "2026-10-15T22:10:00Z", "2026-10-15T22:15:00Z", 9.0)

    stages = stage_windows(results, ["ip-a-1", "ip-b-2"])

    assert [s.name for s in stages] == [
        "ip-a-1 stage 0",
        "ip-b-2 stage 0",
        "whole run",
    ]
    assert stages[1].request_rate == 9.0
    assert stages[-1].end - stages[-1].start == 900


def test_step_09a_sizes_every_treatment_not_just_the_first(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from llmdbenchmark.run.steps import step_09a_capture_cluster_state as step

    seen = []
    monkeypatch.setattr(
        step,
        "write_sizing",
        lambda stack, results, ids, out, fetch: seen.append(ids) or [],
    )
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "config.yaml").write_text(
        yaml.safe_dump({"eppKedaSaturation": {"enabled": True}})
    )

    def kube(*args, check=False):
        if args[:2] == ("get", "route"):
            return _Result(True, "thanos.example")
        if args[:2] == ("whoami", "-t"):
            return _Result(True, "token")
        return SimpleNamespace(success=False, stdout="", stderr="")

    cmd = SimpleNamespace(
        kube=kube,
        execute=lambda *a, **k: _Result(True, '{"status": "success"}'),
    )
    ids = ["ip-a-1", "ip-b-2", "ip-c-3"]
    context = SimpleNamespace(
        namespace="bench",
        experiment_ids=ids,
        run_results_dir=lambda: tmp_path / "results",
        require_cmd=lambda: cmd,
        logger=SimpleNamespace(
            log_info=lambda *a, **k: None, log_warning=lambda *a, **k: None
        ),
    )

    step.CaptureClusterStateStep().execute(context, stack)

    assert seen == [ids]
