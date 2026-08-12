"""Tests for llmdbenchmark.agent SLO Goodput scoring.

Validates that:
- a passing/failing gate against the shipped br_v0_2_example.yaml fixture
  is scored correctly, including throughput for a passing report
- a metric with no value at the selected percentile yields missing_percentile
  and an indeterminate verdict, never an interpolated value
- p99 reads p99, not p95
- unit normalization (ms <-> s, ms/token <-> s/token) is direction-correct,
  and an unrecognized unit yields unknown_units rather than a guess
- version handling distinguishes missing / unsupported / 0.2.1-superset
- a 100%-failure report fails a max_failure_ratio gate
- discover_agent_analysis_input ignores the v0.1 sibling
- multiple reports label by stage and aggregate goodput throughput as a max
- write_agent_session_workspace produces exactly the expected files, all
  re-parseable, byte-identical on a second call
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from llmdbenchmark.agent import (
    ExecutionFacts,
    RecommendationFacts,
    SloGate,
    SloMetric,
    WorkloadIntent,
    WorkspaceVolume,
    discover_agent_analysis_input,
    recommend,
    render_benchmark_job_manifest,
    render_run_command,
    score_slo_goodput,
    write_agent_session_workspace,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_REPORT = (
    PROJECT_ROOT
    / "llmdbenchmark"
    / "analysis"
    / "benchmark_report"
    / "br_v0_2_example.yaml"
)


def _write_report(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_ttft_gate_fails_below_observed():
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.02, units="s")
    result = score_slo_goodput([EXAMPLE_REPORT], [gate])
    report = result.reports[0]
    assert report.verdict == "fail"
    gate_result = report.gate_results[0]
    assert gate_result.metric == SloMetric.TIME_TO_FIRST_TOKEN
    assert round(gate_result.observed, 4) == 0.0452
    assert gate_result.threshold == 0.02


def test_ttft_gate_passes_and_reports_throughput():
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([EXAMPLE_REPORT], [gate])
    report = result.reports[0]
    assert report.verdict == "pass"
    assert report.output_token_rate_mean == 2262.448


def test_itl_gate_yields_missing_percentile():
    gate = SloGate(metric=SloMetric.INTER_TOKEN_LATENCY, threshold=0.1, units="s")
    result = score_slo_goodput([EXAMPLE_REPORT], [gate])
    report = result.reports[0]
    assert report.verdict == "indeterminate"
    assert report.gate_results[0].observed is None
    assert any(d.code == "missing_percentile" for d in result.slo_scoring_diagnostics)


def test_p99_gate_reads_p99_not_p95():
    gate_p95 = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=1.0, units="s")
    gate_p99 = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=1.0, units="s")
    from llmdbenchmark.agent import GatePercentile

    p95_result = score_slo_goodput(
        [EXAMPLE_REPORT], [gate_p95], slo_gate_percentile=GatePercentile.P95
    )
    p99_result = score_slo_goodput(
        [EXAMPLE_REPORT], [gate_p99], slo_gate_percentile=GatePercentile.P99
    )
    assert p95_result.reports[0].gate_results[0].observed != (
        p99_result.reports[0].gate_results[0].observed
    )


def test_ms_units_scaled_report_scores_identically_to_seconds(tmp_path):
    example = yaml.safe_load(EXAMPLE_REPORT.read_text())
    ttft = example["results"]["request_performance"]["aggregate"]["latency"][
        "time_to_first_token"
    ]
    ttft["units"] = "ms"
    for key, value in list(ttft.items()):
        if key != "units" and isinstance(value, (int, float)):
            ttft[key] = value * 1000

    path = _write_report(tmp_path, "ms_report.yaml", example)
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    seconds_result = score_slo_goodput([EXAMPLE_REPORT], [gate])
    ms_result = score_slo_goodput([path], [gate])
    assert seconds_result.reports[0].verdict == ms_result.reports[0].verdict == "pass"


def test_near_boundary_ms_conversion_direction(tmp_path):
    example = yaml.safe_load(EXAMPLE_REPORT.read_text())
    ttft = example["results"]["request_performance"]["aggregate"]["latency"][
        "time_to_first_token"
    ]
    ttft["units"] = "ms"
    ttft["p95"] = 250.0
    path = _write_report(tmp_path, "boundary_report.yaml", example)

    fails = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.2, units="s")
    passes = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.3, units="s")
    fail_result = score_slo_goodput([path], [fails])
    pass_result = score_slo_goodput([path], [passes])
    assert fail_result.reports[0].verdict == "fail"
    assert pass_result.reports[0].verdict == "pass"


def test_unrecognized_units_yields_unknown_units(tmp_path):
    example = yaml.safe_load(EXAMPLE_REPORT.read_text())
    example["results"]["request_performance"]["aggregate"]["latency"][
        "time_to_first_token"
    ]["units"] = "seconds"
    path = _write_report(tmp_path, "bad_units.yaml", example)

    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([path], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert any(d.code == "unknown_units" for d in result.slo_scoring_diagnostics)


def test_minimal_v02_document_yields_diagnostics_not_exception(tmp_path):
    path = _write_report(
        tmp_path, "minimal.yaml", {"version": "0.2", "run": {"uid": "u"}, "results": {}}
    )
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([path], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert result.slo_scoring_diagnostics


def test_full_failure_report_fails_max_failure_ratio_gate(tmp_path):
    path = _write_report(
        tmp_path,
        "all_failed.yaml",
        {
            "version": "0.2",
            "run": {"uid": "u"},
            "results": {
                "request_performance": {
                    "aggregate": {"requests": {"total": 100, "failures": 100}}
                }
            },
        },
    )
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([path], [gate], max_failure_ratio=0.5)
    assert result.reports[0].verdict == "fail"


def test_version_missing_vs_unsupported_vs_superset(tmp_path):
    example = yaml.safe_load(EXAMPLE_REPORT.read_text())

    no_version = dict(example)
    del no_version["version"]
    missing_path = _write_report(tmp_path, "no_version.yaml", no_version)

    superset = dict(example)
    superset["version"] = "0.2.1"
    superset_path = _write_report(tmp_path, "superset.yaml", superset)

    unsupported = dict(example)
    unsupported["version"] = "0.3"
    unsupported_path = _write_report(tmp_path, "unsupported.yaml", unsupported)

    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")

    missing_result = score_slo_goodput([missing_path], [gate])
    assert any(
        d.code == "missing_report_version"
        for d in missing_result.slo_scoring_diagnostics
    )

    superset_result = score_slo_goodput([superset_path], [gate])
    assert any(
        d.code == "version_superset" for d in superset_result.slo_scoring_diagnostics
    )
    assert superset_result.reports[0].verdict == "pass"

    unsupported_result = score_slo_goodput([unsupported_path], [gate])
    assert any(
        d.code == "unsupported_report_version"
        for d in unsupported_result.slo_scoring_diagnostics
    )


def test_missing_throughput_yields_diagnostic(tmp_path):
    path = _write_report(
        tmp_path,
        "no_throughput.yaml",
        {
            "version": "0.2",
            "run": {"uid": "u"},
            "results": {
                "request_performance": {
                    "aggregate": {
                        "requests": {"total": 10, "failures": 0},
                        "latency": {
                            "time_to_first_token": {
                                "units": "s",
                                "mean": 0.01,
                                "p95": 0.02,
                            }
                        },
                    }
                }
            },
        },
    )
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([path], [gate])
    assert result.reports[0].output_token_rate_mean is None
    assert any(d.code == "missing_throughput" for d in result.slo_scoring_diagnostics)


def test_v01_report_yields_diagnostic_not_exception():
    v01_report = (
        PROJECT_ROOT
        / "llmdbenchmark"
        / "analysis"
        / "benchmark_report"
        / "br_v0_1_example.yaml"
    )
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([v01_report], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert any(
        d.code == "unsupported_report_version" for d in result.slo_scoring_diagnostics
    )


def test_missing_path_yields_diagnostic_not_exception(tmp_path):
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([tmp_path / "does-not-exist.yaml"], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert any(d.code == "unreadable_report" for d in result.slo_scoring_diagnostics)


def test_directory_path_yields_diagnostic_not_exception(tmp_path):
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([tmp_path], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert any(d.code == "unreadable_report" for d in result.slo_scoring_diagnostics)


def test_scalar_yaml_document_yields_diagnostic_not_exception(tmp_path):
    path = tmp_path / "scalar.yaml"
    path.write_text("just a string\n")
    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([path], [gate])
    assert result.reports[0].verdict == "indeterminate"
    assert any(d.code == "unreadable_report" for d in result.slo_scoring_diagnostics)


def test_empty_gates_is_indeterminate_not_pass():
    """The out-of-the-box, no-gates-supplied state must never stamp a
    report 'pass' -- that would publish an SLO Goodput number with zero
    SLO evaluated."""
    result = score_slo_goodput([EXAMPLE_REPORT], [])
    assert result.reports[0].verdict == "indeterminate"
    assert any(d.code == "no_gates_supplied" for d in result.slo_scoring_diagnostics)
    assert result.slo_goodput_output_token_rate is None


def test_discover_agent_analysis_input_ignores_v01_sibling(tmp_path):
    treatment_dir = tmp_path / "treatment_0"
    treatment_dir.mkdir()
    v02 = treatment_dir / "benchmark_report_v0.2,_stage_0.yaml"
    v02.write_text("version: '0.2'\n")
    (treatment_dir / "benchmark_report,_stage_0.yaml").write_text("version: '0.1'\n")

    discovered = discover_agent_analysis_input(tmp_path)
    assert discovered == [v02]


def test_multiple_reports_labeled_by_stage_and_max_passing_throughput(tmp_path):
    example = yaml.safe_load(EXAMPLE_REPORT.read_text())
    high_rate = copy.deepcopy(example)
    high_rate["scenario"]["load"]["standardized"]["stage"] = 1
    high_rate["results"]["request_performance"]["aggregate"]["throughput"][
        "output_token_rate"
    ]["mean"] = 3000.0
    low_rate = copy.deepcopy(example)
    low_rate["scenario"]["load"]["standardized"]["stage"] = 2
    low_rate["results"]["request_performance"]["aggregate"]["throughput"][
        "output_token_rate"
    ]["mean"] = 1000.0

    high_path = _write_report(tmp_path, "stage1.yaml", high_rate)
    low_path = _write_report(tmp_path, "stage2.yaml", low_rate)

    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    result = score_slo_goodput([high_path, low_path], [gate])
    stages = {r.stage for r in result.reports}
    assert stages == {1, 2}
    assert result.slo_goodput_output_token_rate == 3000.0


def test_write_agent_session_workspace_creates_expected_files_deterministically(
    tmp_path,
):
    recommendation = recommend(
        RecommendationFacts(workload_intent=WorkloadIntent.INTERACTIVE_CHAT)
    )
    execution_facts = ExecutionFacts(
        benchmark_session_id="sess-workspace",
        endpoint_url="http://endpoint",
        model="a-model",
        namespace="ns",
        benchmark_runner_image="img:tag",
        benchmark_workspace_volume=WorkspaceVolume(claim_name="pvc"),
    )
    run_command = render_run_command(recommendation, execution_facts)
    manifest = render_benchmark_job_manifest(
        recommendation, execution_facts, run_command
    )

    gate = SloGate(metric=SloMetric.TIME_TO_FIRST_TOKEN, threshold=0.1, units="s")
    slo_goodput = score_slo_goodput([EXAMPLE_REPORT], [gate])

    first = write_agent_session_workspace(
        tmp_path, execution_facts, recommendation, run_command, manifest, slo_goodput
    )
    expected_names = {
        "recommendation.yaml",
        "run-command.txt",
        "run-command.json",
        "benchmark-job.yaml",
        "slo-goodput.yaml",
    }
    assert {p.name for p in first.iterdir()} == expected_names

    for name in ("recommendation.yaml", "slo-goodput.yaml"):
        parsed = yaml.safe_load((first / name).read_text())
        assert parsed["agent_artifact_version"] == "1"
    benchmark_job = yaml.safe_load((first / "benchmark-job.yaml").read_text())
    assert benchmark_job["kind"] == "Job"

    contents_first = {p.name: p.read_bytes() for p in first.iterdir()}
    second = write_agent_session_workspace(
        tmp_path, execution_facts, recommendation, run_command, manifest, slo_goodput
    )
    contents_second = {p.name: p.read_bytes() for p in second.iterdir()}
    assert contents_first == contents_second
