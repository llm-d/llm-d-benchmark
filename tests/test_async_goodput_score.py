"""Tests for AsyncGoodput scoring, Benchmark Report v0.2 import_async_batch, and Markdown comparison table."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmd_benchmark_report.base import WorkloadGenerator
from llmd_benchmark_report.native_to_br0_2 import import_async_batch
from llmdbenchmark.agent import (
    AsyncGoodputGate,
    score_async_goodput,
)
from llmdbenchmark.analysis.scripts.async_goodput_comparison_table import (
    load_arm_metrics,
    render_markdown_table,
)


def _write_async_run_dir(
    root: Path,
    name: str,
    *,
    submitted: int,
    good: int,
    good_tokens: int,
    allocated_gpu_hours: float,
    ready_gpu_hours: float,
    scale_down_settled: bool,
    cold_start_s: float | None = 58.0,
) -> Path:
    run_dir = root / name
    (run_dir / "metrics" / "processed").mkdir(parents=True, exist_ok=True)
    attainment = good / submitted if submitted > 0 else 0.0
    req_gp_hr = (
        round(good / allocated_gpu_hours, 4) if allocated_gpu_hours > 0 else None
    )
    req_gp_min = round(req_gp_hr / 60.0, 4) if req_gp_hr is not None else None
    tok_gp_hr = (
        round(good_tokens / allocated_gpu_hours, 4) if allocated_gpu_hours > 0 else None
    )
    cal_r = round(good / (ready_gpu_hours * 3600.0), 6) if ready_gpu_hours > 0 else None
    overhead = (
        round(max(0.0, 1.0 - ready_gpu_hours / allocated_gpu_hours), 6)
        if allocated_gpu_hours > 0
        else 0.0
    )

    results_payload = {
        "schema_version": "1.0",
        "workload_name": "two-cohort-deadlines",
        "transport_mode": "redis-sortedset",
        "drain_duration_seconds": 300.0,
        "accounting_window_seconds": 600.0,
        "wait_for_scale_down": True,
        "scale_down_settled": scale_down_settled,
        "slo_gate": {"min_deadline_attainment": 0.99},
        "aggregate": {
            "total_requests": submitted,
            "on_time_requests": good,
            "exceeded_deadline_requests": submitted - good,
            "failed_requests": 0,
            "total_input_tokens": submitted * 256,
            "total_output_tokens": good_tokens,
            "on_time_output_tokens": good_tokens,
            "turnaround_s": {
                "mean": 12.5,
                "stddev": 4.2,
                "min": 2.0,
                "p50": 11.0,
                "p90": 24.0,
                "p95": 28.0,
                "p99": 35.0,
                "max": 40.0,
            },
            "slack_s": {"mean": 45.0, "min": 5.0, "p10": 12.0, "p50": 44.0},
        },
        "cohorts": {
            "urgent": {
                "total_requests": submitted // 2,
                "on_time_requests": good // 2,
                "deadline_attainment_rate": attainment,
            },
            "relaxed": {
                "total_requests": submitted - submitted // 2,
                "on_time_requests": good - good // 2,
                "deadline_attainment_rate": attainment,
            },
        },
    }
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(results_payload, indent=2), encoding="utf-8")

    summary = {
        "schema_version": "1.0",
        "verdict": "pass" if attainment >= 0.99 else "fail",
        "min_deadline_attainment": 0.99,
        "total_requests": submitted,
        "on_time_requests": good,
        "exceeded_deadline_requests": submitted - good,
        "failed_requests": 0,
        "deadline_attainment_rate": round(attainment, 6),
        "on_time_output_tokens": good_tokens,
        "allocated_gpu_hours": allocated_gpu_hours,
        "ready_gpu_hours": ready_gpu_hours,
        "allocated_pod_hours": allocated_gpu_hours,
        "overhead_gpu_fraction": overhead,
        "accelerator_mode": "physical_gpu",
        "request_goodput_per_gpu_hour": req_gp_hr,
        "request_goodput_per_gpu_min": req_gp_min,
        "output_token_goodput_per_gpu_hour": tok_gp_hr,
        "calibrated_replica_rps": cal_r,
        "calibrated_cold_start_p90_s": cold_start_s,
        "scale_down_settled": scale_down_settled,
        "slack_s": {"p10": 12.0, "p50": 44.0},
    }
    summary_path = run_dir / "async_goodput_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Convert to Benchmark Report v0.2 and validate schema round-trip!
    br = import_async_batch(str(results_path))
    assert br.scenario.load.standardized.tool == WorkloadGenerator.ASYNC_BATCH
    br_yaml_path = run_dir / "benchmark_report_v0.2,_results.json.yaml"
    br.export_yaml(str(br_yaml_path))
    return br_yaml_path


def test_async_goodput_disqualifies_sla_violators_and_ranks_best_goodput_first(
    tmp_path: Path,
):
    """Verify Baseline vs M1 Cron vs M2 Backlog vs SLA-violating run scoring & comparison table."""
    # Baseline Always-On: 360/360 good, 2 GPUs for full 1h duty cycle = 2.0 GPU-hr -> 180 req/GPU-hr
    baseline_report = _write_async_run_dir(
        tmp_path,
        "baseline_always_on",
        submitted=360,
        good=360,
        good_tokens=92160,
        allocated_gpu_hours=2.0,
        ready_gpu_hours=2.0,
        scale_down_settled=False,
        cold_start_s=None,
    )
    # M1 Cron Window 0<->2: 360/360 good, holds 2 GPUs for 30m window -> 1.0 GPU-hr -> 360 req/GPU-hr
    m1_report = _write_async_run_dir(
        tmp_path,
        "m1_cron_window",
        submitted=360,
        good=360,
        good_tokens=92160,
        allocated_gpu_hours=1.0,
        ready_gpu_hours=0.95,
        scale_down_settled=True,
    )
    # M2 Backlog 0<->2: 360/360 good, reactive scale-from-zero and scale-to-zero -> 0.40 GPU-hr -> 900 req/GPU-hr
    m2_report = _write_async_run_dir(
        tmp_path,
        "m2_backlog",
        submitted=360,
        good=360,
        good_tokens=92160,
        allocated_gpu_hours=0.40,
        ready_gpu_hours=0.33,
        scale_down_settled=True,
    )
    # Cheating/under-scaled run: uses only 0.10 GPU-hr (2880 req/GPU-hr raw!) by dropping 20% of deadlines (attainment=0.80 < 0.99)
    cheater_report = _write_async_run_dir(
        tmp_path,
        "under_scaled_dropper",
        submitted=360,
        good=288,
        good_tokens=73728,
        allocated_gpu_hours=0.10,
        ready_gpu_hours=0.08,
        scale_down_settled=True,
    )

    reports = [baseline_report, m1_report, m2_report, cheater_report]

    # 1. Standard gate (min_deadline_attainment=0.99, require_scale_down=False)
    scored = score_async_goodput(
        reports, AsyncGoodputGate(min_deadline_attainment=0.99)
    )
    by_treatment = {r.treatment: r for r in scored.reports}
    assert by_treatment["baseline_always_on"].verdict == "pass"
    assert by_treatment["m1_cron_window"].verdict == "pass"
    assert by_treatment["m2_backlog"].verdict == "pass"
    assert by_treatment["under_scaled_dropper"].verdict == "fail"

    # Best Goodput MUST be M2 (900.0), NOT the disqualified 2880 req/GPU-hr dropper!
    assert scored.best_request_goodput_per_gpu_hour == pytest.approx(
        round(360 / 0.40, 4)
    )
    assert (
        by_treatment["m2_backlog"].request_goodput_per_gpu_hour
        > by_treatment["m1_cron_window"].request_goodput_per_gpu_hour
        > by_treatment["baseline_always_on"].request_goodput_per_gpu_hour
    )

    # 2. Scale-to-zero compliance gate (require_scale_down=True)
    s2z_scored = score_async_goodput(
        reports,
        AsyncGoodputGate(min_deadline_attainment=0.99, require_scale_down=True),
    )
    s2z_by_treatment = {r.treatment: r for r in s2z_scored.reports}
    assert s2z_by_treatment["baseline_always_on"].verdict == "fail"
    assert s2z_by_treatment["baseline_always_on"].scale_down_settled is False
    assert s2z_by_treatment["m1_cron_window"].verdict == "pass"
    assert s2z_by_treatment["m2_backlog"].verdict == "pass"

    # 3. Markdown comparison table check
    arms = [
        load_arm_metrics(p.parent.name, p.parent, min_attainment=0.99) for p in reports
    ]
    assert len(arms) == 4
    md = render_markdown_table(
        arms, min_attainment=0.99, baseline_label="baseline_always_on"
    )
    assert "m2_backlog" in md
    assert "PASS" in md
    assert "FAIL" in md
    assert "Goodput" in md
