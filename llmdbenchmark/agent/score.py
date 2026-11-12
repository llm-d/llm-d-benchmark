"""SLO Goodput scoring: gate-at-percentile plus the passing reports'
throughput. This is not per-request SLO attainment -- benchmark-report
v0.2 stores aggregates only, and the per-request source is out of scope
for this slice.

Reading is confined to ``results.request_performance.aggregate`` and
traverses defensively at every level -- every field in that chain may be
absent in a real report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import yaml
from pydantic import ValidationError

from llmdbenchmark.agent.facts import (
    AsyncGoodput,
    AsyncGoodputGate,
    AsyncGoodputReport,
    Diagnostic,
    GatePercentile,
    GateResult,
    SloGate,
    SloGoodput,
    SloGoodputReport,
)
from llmdbenchmark.analysis.benchmark_report import import_benchmark_report
from llmdbenchmark.analysis.benchmark_report.base import UNITS_GEN_LATENCY, UNITS_TIME

# ms -> s, ms/token -> s/token; anything else is not a guessed conversion.
_SECONDS_PER_UNIT = {
    "s": 1.0,
    "ms": 0.001,
}
_SECONDS_PER_TOKEN_PER_UNIT = {
    "s/token": 1.0,
    "ms/token": 0.001,
}

# Result-file glob for benchmark-report v0.2 (the comma is load-bearing).
# v0.1 siblings are ignored.
_AGENT_ANALYSIS_INPUT_GLOB = "**/benchmark_report_v0.2,_*.yaml"


def discover_agent_analysis_input(results_dir: Path) -> list[Path]:
    """Glob a results tree for benchmark-report v0.2 files (the Agent
    Analysis Input), ignoring the v0.1 siblings written alongside them."""
    return sorted(Path(results_dir).glob(_AGENT_ANALYSIS_INPUT_GLOB))


def _normalize(value: float, units: str, allowed: dict[str, float]) -> float | None:
    factor = allowed.get(units)
    if factor is None:
        return None
    return value * factor


def _score_gate(
    gate: SloGate,
    aggregate,
    percentile: GatePercentile,
    diagnostics: list[Diagnostic],
    report_path: str,
) -> GateResult:
    latency = getattr(aggregate, "latency", None) if aggregate else None
    stat = getattr(latency, gate.metric.value, None) if latency else None

    if stat is None:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="missing_percentile",
                message=(
                    f"{report_path}: metric '{gate.metric.value}' not present "
                    f"in aggregate.latency"
                ),
                subject=gate.metric.value,
            )
        )
        return GateResult(
            metric=gate.metric,
            threshold=gate.threshold,
            threshold_units=gate.units,
            observed=None,
            observed_units=None,
            passed=None,
        )

    observed = getattr(stat, percentile.value, None)
    if observed is None:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="missing_percentile",
                message=(
                    f"{report_path}: '{gate.metric.value}' has no "
                    f"'{percentile.value}' value"
                ),
                subject=gate.metric.value,
            )
        )
        return GateResult(
            metric=gate.metric,
            threshold=gate.threshold,
            threshold_units=gate.units,
            observed=None,
            observed_units=stat.units.value,
            passed=None,
        )

    if stat.units.value in UNITS_TIME:
        allowed = _SECONDS_PER_UNIT
    elif stat.units.value in UNITS_GEN_LATENCY:
        allowed = _SECONDS_PER_TOKEN_PER_UNIT
    else:
        allowed = {}

    normalized_observed = _normalize(observed, stat.units.value, allowed)
    normalized_threshold = _normalize(gate.threshold, gate.units, allowed)
    if normalized_observed is None or normalized_threshold is None:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="unknown_units",
                message=(
                    f"{report_path}: cannot compare units "
                    f"'{stat.units.value}' (observed) with '{gate.units}' "
                    f"(gate) for '{gate.metric.value}'"
                ),
                subject=gate.metric.value,
            )
        )
        return GateResult(
            metric=gate.metric,
            threshold=gate.threshold,
            threshold_units=gate.units,
            observed=observed,
            observed_units=stat.units.value,
            passed=None,
        )

    return GateResult(
        metric=gate.metric,
        threshold=gate.threshold,
        threshold_units=gate.units,
        observed=observed,
        observed_units=stat.units.value,
        passed=normalized_observed <= normalized_threshold,
    )


def _score_report(
    path: Path,
    gates: Sequence[SloGate],
    percentile: GatePercentile,
    max_failure_ratio: float | None,
) -> tuple[SloGoodputReport, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    report_str = str(path)

    try:
        report = import_benchmark_report(report_str)
    except (OSError, AttributeError, yaml.YAMLError) as exc:
        # A missing/unreadable path (FileNotFoundError, IsADirectoryError,
        # PermissionError -- all OSError), a file cut off mid-token
        # (yaml.YAMLError), or a YAML document whose top
        # level isn't a mapping (AttributeError from core.py's dict-style
        # access): routine on a stale or mid-write artifact, never a
        # reason to kill the whole scoring run.
        message = str(exc) or repr(exc)
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="unreadable_report",
                message=f"{report_str}: {message}",
                subject=report_str,
            )
        )
        return (
            SloGoodputReport(
                report_path=report_str,
                stage=None,
                rate_qps=None,
                concurrency=None,
                verdict="indeterminate",
                gate_results=[],
                output_token_rate_mean=None,
                request_rate_mean=None,
            ),
            diagnostics,
        )
    except ValidationError as exc:
        # ValidationError is a ValueError subclass, so it must be caught
        # ahead of the plain-ValueError version-dispatch branch below.
        message = str(exc)
        code = "unknown_units" if "units" in message.lower() else "invalid_report"
        diagnostics.append(
            Diagnostic(severity="error", code=code, message=message, subject=report_str)
        )
        return (
            SloGoodputReport(
                report_path=report_str,
                stage=None,
                rate_qps=None,
                concurrency=None,
                verdict="indeterminate",
                gate_results=[],
                output_token_rate_mean=None,
                request_rate_mean=None,
            ),
            diagnostics,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            "missing_report_version"
            if "Unsupported schema version: None" in message
            else "unsupported_report_version"
        )
        diagnostics.append(
            Diagnostic(severity="error", code=code, message=message, subject=report_str)
        )
        return (
            SloGoodputReport(
                report_path=report_str,
                stage=None,
                rate_qps=None,
                concurrency=None,
                verdict="indeterminate",
                gate_results=[],
                output_token_rate_mean=None,
                request_rate_mean=None,
            ),
            diagnostics,
        )

    if getattr(report, "version", None) not in ("0.2", "0.2.1"):
        # load_benchmark_report happily loads a v0.1 report (it predates
        # the "standardized"/"aggregate" shape this module reads); a v0.1
        # file sitting next to its v0.2 sibling on disk must yield the
        # same diagnostic as any other unsupported version, not an
        # AttributeError three lines down.
        message = (
            f"{report_str}: unsupported report version "
            f"{getattr(report, 'version', None)!r} (only 0.2/0.2.1 are scored)"
        )
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="unsupported_report_version",
                message=message,
                subject=report_str,
            )
        )
        return (
            SloGoodputReport(
                report_path=report_str,
                stage=None,
                rate_qps=None,
                concurrency=None,
                verdict="indeterminate",
                gate_results=[],
                output_token_rate_mean=None,
                request_rate_mean=None,
            ),
            diagnostics,
        )

    standardized = None
    if report.scenario and report.scenario.load:
        standardized = report.scenario.load.standardized
    stage = standardized.stage if standardized else None
    rate_qps = standardized.rate_qps if standardized else None
    concurrency = standardized.concurrency if standardized else None

    aggregate = None
    if report.results and report.results.request_performance:
        aggregate = report.results.request_performance.aggregate

    if aggregate is None:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="missing_aggregate",
                message=f"{report_str}: results.request_performance.aggregate is absent",
                subject=report_str,
            )
        )

    gate_results = [
        _score_gate(gate, aggregate, percentile, diagnostics, report_str)
        for gate in gates
    ]

    if not gates:
        # No SLO Gates supplied is the documented out-of-the-box state (the
        # map ships no thresholds), but "pass" with nothing evaluated is
        # the worst failure mode for a gating tool: it reads as every SLO
        # having been met. Score as indeterminate instead.
        verdict = "indeterminate"
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="no_gates_supplied",
                message=f"{report_str}: no SLO Gates supplied; SLO Goodput not evaluated",
                subject=report_str,
            )
        )
    elif any(g.passed is False for g in gate_results):
        verdict = "fail"
    elif any(g.passed is None for g in gate_results):
        verdict = "indeterminate"
    else:
        verdict = "pass"

    requests = getattr(aggregate, "requests", None) if aggregate else None
    if max_failure_ratio is not None:
        if requests is not None and requests.total and requests.failures is not None:
            failure_ratio = requests.failures / requests.total
            if failure_ratio > max_failure_ratio:
                verdict = "fail"
        else:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="missing_failure_ratio",
                    message=(
                        f"{report_str}: cannot evaluate max_failure_ratio, "
                        f"requests.{{total,failures}} missing"
                    ),
                    subject=report_str,
                )
            )

    throughput = getattr(aggregate, "throughput", None) if aggregate else None
    output_token_rate_mean = None
    request_rate_mean = None
    if throughput is not None:
        if throughput.output_token_rate is not None:
            output_token_rate_mean = throughput.output_token_rate.mean
        if throughput.request_rate is not None:
            request_rate_mean = throughput.request_rate.mean
    elif aggregate is not None:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="missing_throughput",
                message=f"{report_str}: aggregate.throughput is absent",
                subject=report_str,
            )
        )

    return (
        SloGoodputReport(
            report_path=report_str,
            stage=stage,
            rate_qps=rate_qps,
            concurrency=concurrency,
            verdict=verdict,
            gate_results=gate_results,
            output_token_rate_mean=output_token_rate_mean,
            request_rate_mean=request_rate_mean,
        ),
        diagnostics,
    )


def score_slo_goodput(
    agent_analysis_input: Sequence[Path],
    gates: Sequence[SloGate],
    *,
    slo_gate_percentile: GatePercentile = GatePercentile.P95,
    max_failure_ratio: float | None = None,
) -> SloGoodput:
    """Score each Agent Analysis Input report against the given SLO Gates
    at the given SLO Gate Percentile."""
    reports: list[SloGoodputReport] = []
    all_diagnostics: list[Diagnostic] = []

    for path in agent_analysis_input:
        report, diagnostics = _score_report(
            Path(path), gates, slo_gate_percentile, max_failure_ratio
        )
        reports.append(report)
        all_diagnostics.extend(diagnostics)

    passing_rates = [
        r.output_token_rate_mean
        for r in reports
        if r.verdict == "pass" and r.output_token_rate_mean is not None
    ]
    slo_goodput_output_token_rate = max(passing_rates) if passing_rates else None

    return SloGoodput(
        slo_gate_percentile=slo_gate_percentile,
        reports=reports,
        slo_scoring_diagnostics=all_diagnostics,
        slo_goodput_output_token_rate=slo_goodput_output_token_rate,
    )


def _indeterminate_async_report(
    report_str: str, treatment: str | None = None
) -> AsyncGoodputReport:
    return AsyncGoodputReport(
        report_path=report_str,
        treatment=treatment,
        verdict="indeterminate",
        total_requests=0,
        on_time_requests=0,
        exceeded_deadline_requests=0,
        failed_requests=0,
        deadline_attainment_rate=0.0,
        allocated_gpu_hours=None,
        request_goodput_per_gpu_hour=None,
        request_goodput_per_gpu_min=None,
        output_token_goodput_per_gpu_hour=None,
    )


def _extract_async_summary_from_dir(run_dir: Path) -> dict[str, Any] | None:
    """Resolve an async goodput summary dict from a results directory."""
    summary_file = run_dir / "async_goodput_summary.json"
    if summary_file.is_file():
        return json.loads(summary_file.read_text(encoding="utf-8"))

    v02_files = sorted(run_dir.glob("benchmark_report_v0.2*.yaml"))
    for br_path in v02_files:
        report = import_benchmark_report(str(br_path))
        obs = getattr(getattr(report, "results", None), "observability", None)
        extra = getattr(obs, "__pydantic_extra__", None) or {}
        summary = extra.get("async_goodput") or getattr(obs, "async_goodput", None)
        if isinstance(summary, dict):
            return summary

    results_file = run_dir / "results.json"
    if not results_file.is_file():
        return None
    results_data = json.loads(results_file.read_text(encoding="utf-8"))
    agg = results_data.get("aggregate") or {}
    replica_file = run_dir / "metrics" / "processed" / "replica_status.json"
    startup_file = run_dir / "metrics" / "processed" / "pod_startup_times.json"
    replica_data = (
        json.loads(replica_file.read_text(encoding="utf-8"))
        if replica_file.is_file()
        else {}
    )
    startup_data = (
        json.loads(startup_file.read_text(encoding="utf-8"))
        if startup_file.is_file()
        else {}
    )
    integral = replica_data.get("resource_integral") or {}
    startup_agg = startup_data.get("aggregate") or {}
    total_req = int(agg.get("total_requests") or 0)
    on_time_req = int(agg.get("on_time_requests") or 0)
    on_time_tok = int(agg.get("on_time_output_tokens") or 0)
    alloc_gpu_hr = integral.get("allocated_gpu_hours")
    ready_pod_hr = integral.get("ready_pod_hours")

    req_gp_hr = (
        round(on_time_req / float(alloc_gpu_hr), 4)
        if isinstance(alloc_gpu_hr, (int, float)) and alloc_gpu_hr > 0
        else None
    )
    tok_gp_hr = (
        round(on_time_tok / float(alloc_gpu_hr), 4)
        if isinstance(alloc_gpu_hr, (int, float)) and alloc_gpu_hr > 0
        else None
    )
    cal_rps = (
        round(on_time_req / (float(ready_pod_hr) * 3600.0), 6)
        if isinstance(ready_pod_hr, (int, float)) and ready_pod_hr > 0
        else None
    )

    return {
        "total_requests": total_req,
        "on_time_requests": on_time_req,
        "exceeded_deadline_requests": int(agg.get("exceeded_deadline_requests") or 0),
        "failed_requests": int(agg.get("failed_requests") or 0),
        "deadline_attainment_rate": (
            round(on_time_req / total_req, 6) if total_req > 0 else 0.0
        ),
        "on_time_output_tokens": on_time_tok,
        "allocated_gpu_hours": alloc_gpu_hr,
        "ready_gpu_hours": integral.get("ready_gpu_hours"),
        "allocated_pod_hours": integral.get("allocated_pod_hours"),
        "overhead_gpu_fraction": integral.get("overhead_gpu_fraction"),
        "accelerator_mode": integral.get("accelerator_mode") or "simulated_pod_units",
        "request_goodput_per_gpu_hour": req_gp_hr,
        "request_goodput_per_gpu_min": (
            round(req_gp_hr / 60.0, 4) if req_gp_hr is not None else None
        ),
        "output_token_goodput_per_gpu_hour": tok_gp_hr,
        "calibrated_replica_rps": cal_rps,
        "calibrated_cold_start_p90_s": startup_agg.get("p90"),
        "scale_down_settled": results_data.get("scale_down_settled"),
    }


def _score_async_input(
    target: Path,
    gate: AsyncGoodputGate,
) -> tuple[AsyncGoodputReport, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    report_str = str(target)
    treatment = target.name if target.is_dir() else target.parent.name

    try:
        summary: dict[str, Any] | None = None
        if target.is_dir():
            summary = _extract_async_summary_from_dir(target)
        elif target.suffix == ".json":
            raw = json.loads(target.read_text(encoding="utf-8"))
            if (
                isinstance(raw, dict)
                and "aggregate" in raw
                and "total_requests" not in raw
            ):
                summary = _extract_async_summary_from_dir(target.parent)
            elif isinstance(raw, dict):
                summary = raw
        else:
            br = import_benchmark_report(report_str)
            obs = getattr(getattr(br, "results", None), "observability", None)
            extra = getattr(obs, "__pydantic_extra__", None) or {}
            cand = extra.get("async_goodput") or getattr(obs, "async_goodput", None)
            if isinstance(cand, dict):
                summary = cand
    except (OSError, ValueError, ValidationError, AttributeError) as exc:
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="unreadable_async_report",
                message=f"{report_str}: {exc}",
                subject=report_str,
            )
        )
        return _indeterminate_async_report(report_str, treatment), diagnostics

    if not isinstance(summary, dict):
        diagnostics.append(
            Diagnostic(
                severity="error",
                code="missing_async_goodput",
                message=f"{report_str}: no async_goodput summary found",
                subject=report_str,
            )
        )
        return _indeterminate_async_report(report_str, treatment), diagnostics

    total_requests = int(summary.get("total_requests") or 0)
    on_time_requests = int(summary.get("on_time_requests") or 0)
    exceeded_deadline = int(summary.get("exceeded_deadline_requests") or 0)
    failed_requests = int(summary.get("failed_requests") or 0)
    attainment = (
        float(summary["deadline_attainment_rate"])
        if summary.get("deadline_attainment_rate") is not None
        else (on_time_requests / total_requests if total_requests > 0 else 0.0)
    )

    alloc_gpu_hr_raw = summary.get("allocated_gpu_hours")
    alloc_gpu_hr = (
        float(alloc_gpu_hr_raw) if isinstance(alloc_gpu_hr_raw, (int, float)) else None
    )
    ready_gpu_hr = (
        float(summary["ready_gpu_hours"])
        if isinstance(summary.get("ready_gpu_hours"), (int, float))
        else None
    )
    alloc_pod_hr = (
        float(summary["allocated_pod_hours"])
        if isinstance(summary.get("allocated_pod_hours"), (int, float))
        else None
    )
    overhead_frac = (
        float(summary["overhead_gpu_fraction"])
        if isinstance(summary.get("overhead_gpu_fraction"), (int, float))
        else None
    )
    accel_mode = summary.get("accelerator_mode")
    if accel_mode not in ("physical_gpu", "simulated_pod_units"):
        accel_mode = "simulated_pod_units"

    on_time_out_tok = summary.get("on_time_output_tokens")
    req_gp_hr = summary.get("request_goodput_per_gpu_hour")
    if req_gp_hr is None and alloc_gpu_hr is not None and alloc_gpu_hr > 0:
        req_gp_hr = round(on_time_requests / alloc_gpu_hr, 4)
    req_gp_min = summary.get("request_goodput_per_gpu_min")
    if req_gp_min is None and isinstance(req_gp_hr, (int, float)):
        req_gp_min = round(float(req_gp_hr) / 60.0, 4)
    tok_gp_hr = summary.get("output_token_goodput_per_gpu_hour")
    if (
        tok_gp_hr is None
        and isinstance(on_time_out_tok, (int, float))
        and alloc_gpu_hr is not None
        and alloc_gpu_hr > 0
    ):
        tok_gp_hr = round(float(on_time_out_tok) / alloc_gpu_hr, 4)

    scale_down_settled = summary.get("scale_down_settled")

    if total_requests <= 0:
        verdict = "indeterminate"
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="zero_requests",
                message=f"{report_str}: total_requests is 0",
                subject=report_str,
            )
        )
    elif alloc_gpu_hr is None or alloc_gpu_hr <= 0:
        verdict = "indeterminate"
        diagnostics.append(
            Diagnostic(
                severity="warning",
                code="missing_allocated_gpu_hours",
                message=(
                    f"{report_str}: allocated_gpu_hours is missing or <= 0; "
                    f"ensure replica_status_timeseries.json was collected"
                ),
                subject=report_str,
            )
        )
    elif attainment < gate.min_deadline_attainment:
        verdict = "fail"
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="deadline_attainment_below_gate",
                message=(
                    f"{report_str}: deadline_attainment_rate {attainment:.4f} "
                    f"< min_deadline_attainment {gate.min_deadline_attainment:.4f}"
                ),
                subject=report_str,
            )
        )
    elif gate.require_scale_down and scale_down_settled is not True:
        verdict = "fail"
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="scale_down_not_settled",
                message=(
                    f"{report_str}: require_scale_down=True but "
                    f"scale_down_settled={scale_down_settled!r}"
                ),
                subject=report_str,
            )
        )
    else:
        verdict = "pass"

    return (
        AsyncGoodputReport(
            report_path=report_str,
            treatment=str(summary.get("treatment") or treatment) if treatment else None,
            verdict=verdict,
            total_requests=total_requests,
            on_time_requests=on_time_requests,
            exceeded_deadline_requests=exceeded_deadline,
            failed_requests=failed_requests,
            deadline_attainment_rate=round(attainment, 6),
            allocated_gpu_hours=alloc_gpu_hr,
            ready_gpu_hours=ready_gpu_hr,
            allocated_pod_hours=alloc_pod_hr,
            overhead_gpu_fraction=overhead_frac,
            accelerator_mode=accel_mode,
            request_goodput_per_gpu_hour=(
                float(req_gp_hr) if isinstance(req_gp_hr, (int, float)) else None
            ),
            request_goodput_per_gpu_min=(
                float(req_gp_min) if isinstance(req_gp_min, (int, float)) else None
            ),
            output_token_goodput_per_gpu_hour=(
                float(tok_gp_hr) if isinstance(tok_gp_hr, (int, float)) else None
            ),
            calibrated_replica_rps=(
                float(summary["calibrated_replica_rps"])
                if isinstance(summary.get("calibrated_replica_rps"), (int, float))
                else None
            ),
            calibrated_cold_start_p90_s=(
                float(summary["calibrated_cold_start_p90_s"])
                if isinstance(summary.get("calibrated_cold_start_p90_s"), (int, float))
                else None
            ),
            scale_down_settled=(
                bool(scale_down_settled)
                if isinstance(scale_down_settled, bool)
                else None
            ),
        ),
        diagnostics,
    )


def score_async_goodput(
    inputs: Sequence[Path | str],
    gate: AsyncGoodputGate | None = None,
) -> AsyncGoodput:
    """Score async/batch autoscaling runs by Allocated GPU-Hour Goodput gated on
    per-request deadline attainment.

    Accepts paths to ``async_goodput_summary.json``, ``benchmark_report_v0.2*.yaml``,
    or run/treatment directories. Only runs with ``verdict == 'pass'`` compete
    for ``best_request_goodput_per_gpu_hour`` and ``winning_report_path``.
    """
    active_gate = gate if gate is not None else AsyncGoodputGate()
    reports: list[AsyncGoodputReport] = []
    all_diagnostics: list[Diagnostic] = []

    for item in inputs:
        rep, diags = _score_async_input(Path(item), active_gate)
        reports.append(rep)
        all_diagnostics.extend(diags)

    passing = [
        r
        for r in reports
        if r.verdict == "pass" and r.request_goodput_per_gpu_hour is not None
    ]
    best_req_gp: float | None = None
    best_tok_gp: float | None = None
    winner_path: str | None = None
    if passing:
        winner = max(passing, key=lambda r: r.request_goodput_per_gpu_hour or 0.0)
        best_req_gp = winner.request_goodput_per_gpu_hour
        winner_path = winner.report_path
        tok_candidates = [
            r.output_token_goodput_per_gpu_hour
            for r in passing
            if r.output_token_goodput_per_gpu_hour is not None
        ]
        best_tok_gp = max(tok_candidates) if tok_candidates else None

    return AsyncGoodput(
        gate=active_gate,
        reports=reports,
        diagnostics=all_diagnostics,
        best_request_goodput_per_gpu_hour=best_req_gp,
        best_output_token_goodput_per_gpu_hour=best_tok_gp,
        winning_report_path=winner_path,
    )
