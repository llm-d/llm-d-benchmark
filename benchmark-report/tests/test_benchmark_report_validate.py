"""BenchmarkReportV02.validate(): the baseline sanity and completeness rules (#2204)."""

import copy
import importlib.resources
from typing import Any

import pytest
import yaml

from llmd_benchmark_report.artifacts import ValidationIssue, load_artifact
from llmd_benchmark_report.artifacts.benchmark_report.v0_2.schema import (
    BenchmarkReportV02,
)

PACKAGE = importlib.resources.files("llmd_benchmark_report")
AGGREGATE = "results.request_performance.aggregate"
SESSIONS = "results.session_performance.sessions"


# The packaged v0.2 example report as a dict, a fresh copy on every call.
def _example() -> dict[str, Any]:
    return yaml.safe_load((PACKAGE / "br_v0_2_example.yaml").read_text())


# A partial report a standalone producer writes before base-file composition:
# run time, producer and one aggregate request block with 10 requests, no scenario.
def _partial() -> dict[str, Any]:
    return {
        "kind": "benchmark-report",
        "version": "0.2.1",
        "run": {
            "uid": "p1",
            "time": {"start": "2026-11-23T10:00:00Z", "end": "2026-11-23T10:05:00Z"},
        },
        "producer": {"name": "inference-perf", "version": "0.7.0"},
        "results": {
            "request_performance": {
                "aggregate": {
                    "requests": {"total": 10, "failures": 1, "incomplete": 0},
                    "latency": {
                        "time_to_first_token": {"units": "s", "mean": 0.2, "p99": 0.5}
                    },
                    "throughput": {"request_rate": {"units": "queries/s", "mean": 2}},
                }
            }
        },
    }


# The rule ids validate() returns, in order, for a report dict.
def _rules(data: dict[str, Any], complete: bool = False) -> list[str]:
    return [issue.rule for issue in load_artifact(data).validate(complete=complete)]


# The shipped example passes both the sanity rules and the completeness rules,
# loaded through load_artifact() and through model_validate() without its kind.
def test_example_report_passes_every_rule() -> None:
    assert load_artifact(_example()).validate() == []
    assert load_artifact(_example()).validate(complete=True) == []
    legacy = _example()
    del legacy["kind"]
    assert BenchmarkReportV02.model_validate(legacy).validate(complete=True) == []


# A partial report (no scenario) passes the sanity rules. complete=True adds
# incomplete-scenario-load and incomplete-scenario-stack, in that order.
def test_partial_report_passes_sanity_and_fails_completeness() -> None:
    assert _rules(_partial()) == []
    assert load_artifact(_partial()).validate(complete=True) == [
        ValidationIssue(
            rule="incomplete-scenario-load",
            path="scenario.load",
            message="workload is not set",
        ),
        ValidationIssue(
            rule="incomplete-scenario-stack",
            path="scenario.stack",
            message="stack is not set",
        ),
    ]


# The example with scenario.stack [] keeps its load: complete=True reports only
# incomplete-scenario-stack. Without scenario.load it reports only the load rule.
def test_completeness_rules_report_each_missing_part() -> None:
    no_stack = _example()
    no_stack["scenario"]["stack"] = []
    assert _rules(no_stack, complete=True) == ["incomplete-scenario-stack"]
    no_load = _example()
    del no_load["scenario"]["load"]
    assert _rules(no_load, complete=True) == ["incomplete-scenario-load"]


# run.time absent gives run-time-missing twice, for run.time.start and run.time.end.
# Only start set gives one, for run.time.end.
def test_missing_run_time() -> None:
    data = _partial()
    del data["run"]["time"]
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [
        ("run-time-missing", "run.time.start"),
        ("run-time-missing", "run.time.end"),
    ]
    data = _partial()
    del data["run"]["time"]["end"]
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [("run-time-missing", "run.time.end")]


# end 09:00 before start 10:00 gives run-time-order at run.time.end. end == start passes.
def test_run_time_order() -> None:
    data = _partial()
    data["run"]["time"]["end"] = "2026-11-23T09:00:00Z"
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [("run-time-order", "run.time.end")]
    data["run"]["time"]["end"] = data["run"]["time"]["start"]
    assert _rules(data) == []


# A naive end 2026-11-23T09:00:00 (read as UTC) before start 10:00Z gives
# run-time-order instead of a TypeError, and a naive end at 10:05 passes.
def test_run_time_order_with_a_naive_timestamp() -> None:
    data = _partial()
    data["run"]["time"]["end"] = "2026-11-23T09:00:00"
    assert _rules(data) == ["run-time-order"]
    data["run"]["time"]["end"] = "2026-11-23T10:05:00"
    assert _rules(data) == []


# No producer gives producer-missing at producer. An empty name or a blank version
# gives producer-missing at that field.
def test_producer_missing_or_empty() -> None:
    data = _partial()
    del data["producer"]
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [("producer-missing", "producer")]
    data = _partial()
    data["producer"] = {"name": "", "version": " "}
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [
        ("producer-missing", "producer.name"),
        ("producer-missing", "producer.version"),
    ]


# results {} (neither request_performance nor session_performance) gives
# results-missing. Session results alone are enough.
def test_results_missing() -> None:
    data = _partial()
    data["results"] = {}
    assert _rules(data) == ["results-missing"]
    data["results"] = {"session_performance": {"sessions": {"total": 3}}}
    assert _rules(data) == []


# failures 11 or incomplete 12 out of 10 requests each give
# request-count-exceeds-total at that field. failures 10 of 10 passes.
@pytest.mark.parametrize("field,value", [("failures", 11), ("incomplete", 12)])
def test_request_counts_above_total(field: str, value: int) -> None:
    data = _partial()
    requests = data["results"]["request_performance"]["aggregate"]["requests"]
    requests[field] = value
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path, i.message) for i in issues] == [
        (
            "request-count-exceeds-total",
            f"{AGGREGATE}.requests.{field}",
            f"{value} is more than {AGGREGATE}.requests.total (10)",
        )
    ]
    requests[field] = 10
    assert _rules(data) == []


# total 4 sessions with succeeded 5 or failed 5, and total_events 6 with 7
# completed or 7 cancelled events, each give session-count-exceeds-total.
@pytest.mark.parametrize(
    "field,value",
    [
        ("succeeded", 5),
        ("failed", 5),
        ("total_events_completed", 7),
        ("total_events_cancelled", 7),
    ],
)
def test_session_counts_above_total(field: str, value: int) -> None:
    data = _partial()
    sessions: dict[str, Any] = {"total": 4, "total_events": 6}
    data["results"]["session_performance"] = {"sessions": sessions}
    assert _rules(data) == []
    sessions[field] = value
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path) for i in issues] == [
        ("session-count-exceeds-total", f"{SESSIONS}.{field}")
    ]


# Completed events with no total_events set are not compared to anything.
def test_session_events_without_total_pass() -> None:
    data = _partial()
    data["results"]["session_performance"] = {
        "sessions": {"total": 4, "total_events_completed": 100}
    }
    assert _rules(data) == []


# A TTFT p99 of -0.5 gives negative-latency, and a request_rate min of -1 gives
# negative-throughput, each at the offending statistic.
def test_negative_latency_and_throughput() -> None:
    data = _partial()
    aggregate = data["results"]["request_performance"]["aggregate"]
    aggregate["latency"]["time_to_first_token"]["p99"] = -0.5
    aggregate["throughput"]["request_rate"]["min"] = -1
    issues = load_artifact(data).validate()
    assert [(i.rule, i.path, i.message) for i in issues] == [
        (
            "negative-latency",
            f"{AGGREGATE}.latency.time_to_first_token.p99",
            "-0.5 is negative",
        ),
        (
            "negative-throughput",
            f"{AGGREGATE}.throughput.request_rate.min",
            "-1 is negative",
        ),
    ]


# Every numeric statistic of a latency metric is checked: a negative value at each
# of mean, mode, min, the percentiles and max is reported at that field.
def test_every_latency_statistic_is_checked() -> None:
    fields = ["mean", "mode", "min", "p0p1", "p1", "p5", "p10", "p25", "p50"]
    fields += ["p75", "p90", "p95", "p99", "p99p9", "max"]
    data = _partial()
    latency = data["results"]["request_performance"]["aggregate"]["latency"]
    latency["request_latency"] = {"units": "ms", **{f: -1 for f in fields}}
    issues = load_artifact(data).validate()
    assert [i.path for i in issues] == [
        f"{AGGREGATE}.latency.request_latency.{f}" for f in fields
    ]


# Several broken rules are all reported in one call, sanity rules first and then
# completeness rules, and validate() does not change the report.
def test_all_issues_are_reported_together() -> None:
    data = _partial()
    del data["producer"]
    data["run"]["time"]["end"] = "2026-11-23T09:00:00Z"
    data["results"]["request_performance"]["aggregate"]["requests"]["failures"] = 99
    report = load_artifact(data)
    before = copy.deepcopy(report.dump())
    assert [i.rule for i in report.validate(complete=True)] == [
        "run-time-order",
        "producer-missing",
        "request-count-exceeds-total",
        "incomplete-scenario-load",
        "incomplete-scenario-stack",
    ]
    assert report.dump() == before
