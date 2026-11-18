"""Tests for offered load and SLO attainment from inference-perf's per-request records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report.load_series import (
    build_load_component,
    iter_json_array,
    load_generator_requests,
    met_slo,
)
from llmdbenchmark.analysis.benchmark_report.metrics_processor import (
    add_metrics_to_benchmark_report,
)

# perf_counter origin: epoch = mono + OFFSET.
OFFSET = 1_800_000_000.0
STAGE0_EPOCH = OFFSET + 100.0  # stage 0 starts at mono 100, schedules from 101


def _record(start, ttft=0.2, gap=0.01, tokens=10, failed=False, budget=10):
    """One inference-perf record sent at mono *start* (None ttft = no stream)."""
    if failed:
        return {
            "start_time": start,
            "end_time": start + 1.0,
            "request": json.dumps({"prompt": "x", "max_tokens": budget}),
            "info": {"request_metrics": {"text": {"input_tokens": 0}}},
            "error": {"error_type": "ClientPayloadError", "error_msg": "cut"},
        }
    times = [] if ttft is None else [start + ttft + k * gap for k in range(tokens)]
    end = (times[-1] if times else start + 0.5) + 0.01
    return {
        "start_time": start,
        "end_time": end,
        "request": json.dumps({"prompt": "x", "max_tokens": budget}),
        "info": {
            "request_metrics": {"text": {"input_tokens": 100}},
            "response_metrics": {
                "output_tokens": tokens,
                "server_usage": {"completion_tokens": tokens},
                "chunk_times": times,
                "output_token_times": times,
            },
        },
        "error": None,
    }


def _partial(results: Path, stage: int, start: float, end: float, total: int):
    iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat()  # noqa: E731
    (results / f"inference-perf.partial.stage_{stage}.yaml").write_text(
        yaml.safe_dump(
            {
                "run": {"time": {"start": iso(start), "end": iso(end)}},
                "results": {
                    "request_performance": {"aggregate": {"requests": {"total": total}}}
                },
            }
        )
    )


def _run(
    tmp_path: Path,
    records: list[dict],
    stages: list[tuple[float, float, int]],
    load_type: str | None = "poisson",
):
    results = tmp_path / "results"
    results.mkdir()
    (results / "per_request_lifecycle_metrics.json").write_text(json.dumps(records))
    if load_type:
        (results / "config.yaml").write_text(
            yaml.safe_dump({"load": {"type": load_type}})
        )
    for i, (start, end, total) in enumerate(stages):
        _partial(results, i, start, end, total)
    return results


def _report(constraints=None, load_type="poisson"):
    config = {"load": {"type": load_type}}
    if constraints is not None:
        config["report"] = {"goodput": {"constraints": constraints}}
    return {
        "version": "0.2.1",
        "kind": "benchmark-report",
        "run": {"uid": "run"},
        "scenario": {"load": {"native": {"config": config}}},
        "results": {},
    }


def test_iter_json_array_streams_across_chunks(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    items = [{"i": i, "pad": "y" * 50} for i in range(40)]
    path.write_text("[\n " + ",\n ".join(json.dumps(x) for x in items) + "\n]\n")
    assert [x["i"] for x in iter_json_array(path, chunk_size=7)] == list(range(40))


def test_iter_json_array_rejects_a_truncated_file(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text('[{"i": 1}, {"i": 2')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        list(iter_json_array(path, chunk_size=4))


def test_output_budget_reads_the_top_level_key(tmp_path: Path) -> None:
    """A nested max_tokens (tools, response_format) does not win; null falls back."""
    record = _record(101.0)
    record["request"] = json.dumps(
        {"max_tokens": 64, "tools": [{"parameters": {"max_tokens": 5}}]}
    )
    other = _record(102.0)
    other["request"] = {"max_tokens": None, "max_completion_tokens": 32}
    results = _run(tmp_path, [record, other], [(STAGE0_EPOCH, OFFSET + 102.4, 2)])
    assert [q.output_budget for q in load_generator_requests(results).requests] == [
        64,
        32,
    ]


def test_partial_names_must_match_exactly(tmp_path: Path) -> None:
    """A copied partial (old-...yaml) is not read as a stage's epochs."""
    from llmdbenchmark.analysis.benchmark_report.load_series import _stage_epochs

    results = _run(tmp_path, [_record(101.0)], [(STAGE0_EPOCH, OFFSET + 101.4, 1)])
    _partial(results, 7, OFFSET, OFFSET + 1.0, 1)
    (results / "inference-perf.partial.stage_7.yaml").rename(
        results / "old-inference-perf.partial.stage_7.yaml"
    )
    assert sorted(_stage_epochs(results)) == [0]


def test_output_budget_ignores_an_escaped_key_in_the_prompt(tmp_path: Path) -> None:
    record = _record(101.0, budget=64)
    record["request"] = json.dumps(
        {"max_tokens": 64, "prompt": 'say "max_tokens": 9999'}
    )
    results = _run(tmp_path, [record], [(STAGE0_EPOCH, STAGE0_EPOCH + 5, 1)])
    assert load_generator_requests(results).requests[0].output_budget == 64


def test_clock_is_bounded_per_stage(tmp_path: Path) -> None:
    """Each stage's start (+1 s lead) and end bound the offset."""
    records = [_record(101.0), _record(103.0), _record(201.0), _record(204.0)]
    s1 = OFFSET + 200.0
    stages = [
        (STAGE0_EPOCH, OFFSET + 103.3, 2),  # last end ~103.3 (0.2 + 9*0.01 + 0.01)
        (s1, OFFSET + 204.4, 2),
    ]
    loaded = load_generator_requests(_run(tmp_path, records, stages))
    assert "per stage (rate)" in loaded.clock
    assert loaded.offset == pytest.approx(OFFSET, abs=0.05)
    assert loaded.offset_uncertainty < 0.05


def test_clock_falls_back_to_first_and_last_stage(tmp_path: Path) -> None:
    records = [_record(101.0), _record(201.0)]
    stages = [
        (STAGE0_EPOCH, OFFSET + 150.0, None),
        (OFFSET + 200.0, OFFSET + 201.4, None),
    ]
    loaded = load_generator_requests(_run(tmp_path, records, stages))
    assert "first and last stage" in loaded.clock
    assert (
        loaded.offset - loaded.offset_uncertainty
        <= OFFSET
        <= (loaded.offset + loaded.offset_uncertainty)
    )


def test_inconsistent_epochs_raise(tmp_path: Path) -> None:
    """A request sent after the run's end epoch cannot be placed."""
    from llmdbenchmark.analysis.benchmark_report.load_series import ClockAnchorError

    records = [_record(101.0), _record(140.0)]
    results = _run(tmp_path, records, [(STAGE0_EPOCH, STAGE0_EPOCH + 5.0, 2)])
    with pytest.raises(ClockAnchorError):
        load_generator_requests(results)


def test_session_replay_has_no_lead_and_ends_after_its_epoch(tmp_path: Path) -> None:
    """run_session_stage sends at its start and stamps its end before teardown."""
    # Sent at mono 100.0 (no lead) and 104.0; the second ends ~104.3, after the
    # stage's end epoch (104.1), as in-flight requests do at teardown.
    records = [_record(100.0), _record(104.0)]
    stages = [(OFFSET + 100.0, OFFSET + 104.1, 2)]
    loaded = load_generator_requests(
        _run(tmp_path, records, stages, load_type="trace_session_replay")
    )
    assert "(session)" in loaded.clock
    assert "send bound" not in loaded.clock  # no fallback needed: the bound fits
    assert loaded.offset == pytest.approx(OFFSET, abs=0.06)
    assert loaded.load_type == "trace_session_replay"


def test_rate_stage_cut_short_falls_back_to_the_send_bound(tmp_path: Path) -> None:
    """A stage stopped with requests in flight (a circuit breaker) is still placed."""
    records = [_record(101.0), _record(103.0, ttft=5.0)]  # ends ~108.1
    stages = [(STAGE0_EPOCH, OFFSET + 104.0, 2)]
    loaded = load_generator_requests(_run(tmp_path, records, stages))
    assert loaded.clock.endswith("send bound (rate)")
    assert loaded.offset - loaded.offset_uncertainty <= OFFSET + 1e-6


def test_no_per_request_file_gives_nothing(tmp_path: Path) -> None:
    assert load_generator_requests(tmp_path) is None


def test_slo_verdict_matches_inference_perf_rules(tmp_path: Path) -> None:
    """Failures and requests with no measurable TTFT are misses; every constraint counts."""
    records = [
        _record(101.0, ttft=0.2, gap=0.01),  # ttft 0.2, tpot 0.01: good
        _record(102.0, ttft=3.0, gap=0.01),  # ttft over 2 s
        _record(103.0, ttft=0.2, gap=0.1),  # tpot over 0.05
        _record(104.0, ttft=None),  # no stream: TTFT undefined
        _record(105.0, failed=True),
    ]
    results = _run(tmp_path, records, [(STAGE0_EPOCH, OFFSET + 107.0, 5)])
    loaded = load_generator_requests(results)
    constraints = {"ttft": 2.0, "tpot": 0.05}
    assert [met_slo(q, constraints, False) for q in loaded.requests] == [
        True,
        False,
        False,
        False,
        False,
    ]
    # A failure is a miss even when no constraint is declared.
    assert not met_slo(loaded.requests[-1], {}, False)


def test_component_bins_by_send_time_with_zero_bins(tmp_path: Path) -> None:
    """Empty bins carry 0 offered load and no attainment point."""
    records = [
        _record(101.0),
        _record(105.0),
        _record(106.0, failed=True),
        _record(171.0, ttft=3.0),
    ]
    # A Poisson stage's partial ends at its last completion (~174.1 here).
    results = _run(tmp_path, records, [(STAGE0_EPOCH, OFFSET + 174.11, 4)])
    loaded = load_generator_requests(results)
    window = (
        datetime.fromtimestamp(STAGE0_EPOCH, tz=timezone.utc),
        datetime.fromtimestamp(STAGE0_EPOCH + 90, tz=timezone.utc),
    )

    component, meta = build_load_component(_report({"ttft": 2.0}), loaded, window)

    ts = component["time_series"]
    assert [p["value"] for p in ts["requests_sent"]["series"]] == [3, 0, 1]
    assert [p["value"] for p in ts["requests_errored"]["series"]] == [1, 0, 0]
    assert ts["offered_request_rate"]["series"][0]["value"] == pytest.approx(0.1)
    assert ts["offered_output_budget_rate"]["series"][0]["value"] == pytest.approx(1.0)
    assert ts["offered_input_token_rate"]["units"] == "tokens/s"
    # 2 of 3 met in the first bin (the failure is a miss), none in the last.
    assert [p["value"] for p in ts["slo_attainment"]["series"]] == [
        pytest.approx(2 / 3),
        0.0,
    ]
    assert ts["requests_sent"]["series"][0]["ts"] == window[0].isoformat()
    assert meta["slo_constraints_seconds"] == {"ttft": 2.0}
    assert meta["clock_uncertainty_seconds"] < 0.1


def test_no_declared_slo_means_no_attainment(tmp_path: Path) -> None:
    results = _run(tmp_path, [_record(101.0)], [(STAGE0_EPOCH, OFFSET + 102.0, 1)])
    component, meta = build_load_component(_report(), load_generator_requests(results))
    assert "slo_attainment" not in component["time_series"]
    assert meta["slo_constraints_seconds"] is None


def test_report_carries_the_load_component_at_0_2_2(tmp_path: Path) -> None:
    from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import Observability

    results = _run(tmp_path, [_record(101.0)], [(STAGE0_EPOCH, OFFSET + 102.0, 1)])
    metrics_dir = tmp_path / "metrics"
    (metrics_dir / "processed").mkdir(parents=True)

    report = add_metrics_to_benchmark_report(
        _report({"ttft": 2.0}),
        str(metrics_dir),
        load_requests=load_generator_requests(results),
    )

    obs = report["results"]["observability"]
    Observability(**obs)
    assert report["version"] == "0.2.2"
    (component,) = obs["components"]
    assert (component["component_label"], component["replica_id"]) == (
        "Load generator (inference-perf)",
        "inference-perf",
    )
    assert obs["load_generator"]["harness"] == "inference-perf"


def test_embed_metrics_adds_the_load_component_per_stage(tmp_path: Path) -> None:
    """The in-pod entry point reads the per-request file once and clips per stage."""
    from llmdbenchmark.analysis.metrics_embed import embed_metrics

    s1 = OFFSET + 200.0
    records = [_record(101.0), _record(102.0), _record(201.0)]
    results = _run(
        tmp_path, records, [(STAGE0_EPOCH, OFFSET + 102.4, 2), (s1, OFFSET + 201.4, 1)]
    )
    stamp = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime(  # noqa: E731
        "%Y-%m-%d %H:%M:%S"
    )
    (results / "stdout.log").write_text(
        f"{stamp(STAGE0_EPOCH)},000 - INFO - Stage 0 - run started\n"
        f"{stamp(OFFSET + 103)},000 - INFO - Stage 0 - run completed\n"
        f"{stamp(s1)},000 - INFO - Stage 1 - run started\n"
        f"{stamp(OFFSET + 202)},000 - INFO - Stage 1 - run completed\n"
    )
    processed = results / "metrics" / "processed"
    processed.mkdir(parents=True)
    (processed / "metrics_summary.json").write_text("{}")
    for stage in (0, 1):
        (
            results
            / f"benchmark_report_v0.2,_stage_{stage}_lifecycle_metrics.json.yaml"
        ).write_text(yaml.safe_dump(_report({"ttft": 2.0})))

    assert embed_metrics(results / "metrics", results) == 2

    sent = {}
    for stage in (0, 1):
        doc = yaml.safe_load(
            (
                results
                / f"benchmark_report_v0.2,_stage_{stage}_lifecycle_metrics.json.yaml"
            ).read_text()
        )
        (component,) = [
            c
            for c in doc["results"]["observability"]["components"]
            if c["replica_id"] == "inference-perf"
        ]
        sent[stage] = sum(
            p["value"] for p in component["time_series"]["requests_sent"]["series"]
        )
    assert sent == {0: 2, 1: 1}


def test_failed_requests_count_at_the_mean_prompt_length(tmp_path: Path) -> None:
    """inference-perf records 0 prompt tokens for a failure; the offered rate should not drop."""
    records = [_record(101.0), _record(102.0, failed=True)]
    results = _run(tmp_path, records, [(STAGE0_EPOCH, OFFSET + 103.0, 2)])
    component, meta = build_load_component(_report(), load_generator_requests(results))
    rate = component["time_series"]["offered_input_token_rate"]["series"][0]["value"]
    assert rate == pytest.approx(200 / 30)
    assert meta["failed_requests_with_estimated_input_tokens"] == 1


def test_ignore_eos_takes_the_budget_from_generated_tokens(tmp_path: Path) -> None:
    """With no request body kept (npd-fcoff profiles), ignore_eos makes generated == asked."""
    records = [_record(101.0, tokens=12), _record(102.0, tokens=8)]
    for r in records:
        del r["request"]
    results = _run(tmp_path, records, [(STAGE0_EPOCH, OFFSET + 102.4, 2)])
    report = _report()
    report["scenario"]["load"]["native"]["config"]["server"] = {"ignore_eos": True}
    component, meta = build_load_component(report, load_generator_requests(results))
    budget = component["time_series"]["offered_output_budget_rate"]["series"][0]
    assert budget["value"] == pytest.approx(20 / 30)
    assert meta["output_budget_from_generated_tokens"] == 2
    # Without ignore_eos there is no honest budget, so no series.
    component, meta = build_load_component(_report(), load_generator_requests(results))
    assert "offered_output_budget_rate" not in component["time_series"]
    assert meta["requests_without_output_budget"] == 2


def test_failures_are_also_binned_by_end_time(tmp_path: Path) -> None:
    """A burst of failures sent earlier lands where it ended, next to its cause."""
    records = [_record(101.0, failed=True), _record(102.0, failed=True)]
    for r in records:
        r["end_time"] = 140.0  # both cut off together 40 s later
    results = _run(tmp_path, records, [(STAGE0_EPOCH, OFFSET + 140.0, 2)])
    window = (
        datetime.fromtimestamp(STAGE0_EPOCH, tz=timezone.utc),
        datetime.fromtimestamp(STAGE0_EPOCH + 60, tz=timezone.utc),
    )
    component, _ = build_load_component(
        _report(), load_generator_requests(results), window
    )
    ts = component["time_series"]
    assert [p["value"] for p in ts["requests_errored"]["series"]] == [2, 0]
    assert [p["value"] for p in ts["requests_errored_at_end"]["series"]] == [0, 2]


def test_loop_and_schedule_delay_are_recorded(tmp_path: Path) -> None:
    results = _run(tmp_path, [_record(101.0)], [(STAGE0_EPOCH, OFFSET + 101.4, 1)])
    (results / "stage_0_lifecycle_metrics.json").write_text(
        json.dumps({"load_summary": {"schedule_delay": {"max": 0.002, "p99": 0.001}}})
    )
    _, meta = build_load_component(_report(), load_generator_requests(results))
    assert meta["loop"] == "open"
    assert meta["schedule_delay_seconds"] == {"0": {"max": 0.002, "p99": 0.001}}


def test_embed_records_why_the_series_is_missing(tmp_path: Path, monkeypatch) -> None:
    from llmdbenchmark.analysis.metrics_embed import embed_metrics

    results = _run(
        tmp_path,
        [_record(101.0), _record(140.0)],
        [(STAGE0_EPOCH, STAGE0_EPOCH + 5, 2)],
    )
    processed = results / "metrics" / "processed"
    processed.mkdir(parents=True)
    (processed / "metrics_summary.json").write_text("{}")
    report_path = results / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml"
    report_path.write_text(yaml.safe_dump(_report()))
    warnings = []

    embed_metrics(
        results / "metrics", results, log=lambda m, w=False: w and warnings.append(m)
    )

    obs = yaml.safe_load(report_path.read_text())["results"]["observability"]
    assert "unavailable" in obs["load_generator"]
    assert any("No offered-load series" in m for m in warnings)


def test_embed_skips_the_per_request_parse_when_embedding_is_off(
    tmp_path: Path, monkeypatch
) -> None:
    from llmdbenchmark.analysis import metrics_embed
    from llmdbenchmark.analysis.benchmark_report import load_series

    results = _run(tmp_path, [_record(101.0)], [(STAGE0_EPOCH, OFFSET + 101.4, 1)])
    processed = results / "metrics" / "processed"
    processed.mkdir(parents=True)
    (processed / "metrics_summary.json").write_text("{}")
    (results / "benchmark_report_v0.2,_stage_0_lifecycle_metrics.json.yaml").write_text(
        yaml.safe_dump(_report())
    )
    monkeypatch.setenv("LLMDBENCH_METRICS_EMBED_TIME_SERIES", "false")
    calls = []
    monkeypatch.setattr(
        load_series, "load_generator_requests", lambda d: calls.append(d)
    )
    assert metrics_embed.embed_metrics(results / "metrics", results) == 1
    assert calls == []
