"""SLO Goodput scoring: gate-at-percentile plus the passing reports'
throughput. This is not per-request SLO attainment -- benchmark-report
v0.2 stores aggregates only, and the per-request source is out of scope
for this slice.

Reading is confined to ``results.request_performance.aggregate`` and
traverses defensively at every level -- every field in that chain may be
absent in a real report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from llmdbenchmark.agent.facts import (
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

# Result-file glob for benchmark-report v0.2 (the comma is load-bearing --
# analysis/__init__.py:105-108, cross_treatment.py:197). v0.1 siblings are
# ignored.
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
    except (OSError, AttributeError) as exc:
        # A missing/unreadable path (FileNotFoundError, IsADirectoryError,
        # PermissionError -- all OSError) or a YAML document whose top
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

    if getattr(report, "version", None) == "0.2.1":
        diagnostics.append(
            Diagnostic(
                severity="info",
                code="version_superset",
                message=f"{report_str}: scored as a v0.2.1 additive superset of v0.2",
                subject=report_str,
            )
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
