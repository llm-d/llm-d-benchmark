#!/usr/bin/env python3
# Copyright 2026 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

"""Render an Async/Batch Autoscaling Goodput comparison table in Markdown.

Compares multiple autoscaling strategies (e.g. Always-On Baseline, M1 Cron
Window, M2 Backlog) by Allocated GPU-Hours, Deadline Attainment SLO gate,
and Async Goodput.

Usage:
    python3 llmdbenchmark/analysis/scripts/async_goodput_comparison_table.py \\
        --run-dir "Always-On=/path/to/baseline" \\
        --run-dir "M1 Cron=/path/to/cron" \\
        --run-dir "M2 Backlog=/path/to/backlog" \\
        --min-attainment 0.99
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Any

import yaml

try:
    import zstandard
except ImportError:
    zstandard = None


def _find_newest(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.rglob(pattern))
    files = [m for m in matches if m.is_file()]
    return files[-1] if files else None


def _read_from_archives(root: Path, basename: str) -> bytes | None:
    if zstandard is None:
        return None
    archives = sorted(p for p in root.rglob("*.tar.zst") if p.is_file())
    for archive in reversed(archives):
        try:
            with archive.open("rb") as raw:
                with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
                    with tarfile.open(fileobj=stream, mode="r|") as tar:
                        for member in tar:
                            if (
                                member.isfile()
                                and os.path.basename(member.name) == basename
                            ):
                                extracted = tar.extractfile(member)
                                if extracted is not None:
                                    return extracted.read()
        except (OSError, tarfile.TarError, Exception):
            continue
    return None


def _load_json_anywhere(root: Path, basename: str) -> dict[str, Any]:
    found = _find_newest(root, basename)
    if found is not None:
        try:
            return json.loads(found.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    raw = _read_from_archives(root, basename)
    if raw is not None:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {}


def load_arm_metrics(
    label: str,
    run_path: Path,
    min_attainment: float = 0.99,
    require_scale_down: bool = False,
) -> dict[str, Any]:
    """Load and normalize Async Goodput metrics for one strategy arm."""
    summary: dict[str, Any] = {}
    results_data: dict[str, Any] = {}

    if run_path.is_file():
        if run_path.suffix == ".json":
            loaded = json.loads(run_path.read_text(encoding="utf-8"))
            if "aggregate" in loaded and "total_requests" not in loaded:
                results_data = loaded
            else:
                summary = loaded
        else:
            doc = yaml.safe_load(run_path.read_text(encoding="utf-8")) or {}
            summary = ((doc.get("results") or {}).get("observability") or {}).get(
                "async_goodput"
            ) or {}
    else:
        summary = _load_json_anywhere(run_path, "async_goodput_summary.json")
        results_data = _load_json_anywhere(run_path, "results.json")
        if not summary:
            br_file = _find_newest(run_path, "benchmark_report_v0.2*.yaml")
            if br_file is not None:
                doc = yaml.safe_load(br_file.read_text(encoding="utf-8")) or {}
                summary = ((doc.get("results") or {}).get("observability") or {}).get(
                    "async_goodput"
                ) or {}

    if not summary and results_data:
        replica = _load_json_anywhere(run_path, "replica_status.json")
        startup = _load_json_anywhere(run_path, "pod_startup_times.json")
        integral = replica.get("resource_integral") or {}
        agg = results_data.get("aggregate") or {}
        total_req = int(agg.get("total_requests") or 0)
        good_req = int(agg.get("on_time_requests") or 0)
        good_tok = int(agg.get("on_time_output_tokens") or 0)
        alloc_hr = integral.get("allocated_gpu_hours")
        ready_pod_hr = integral.get("ready_pod_hours")
        req_gp_hr = (
            round(good_req / float(alloc_hr), 4)
            if isinstance(alloc_hr, (int, float)) and alloc_hr > 0
            else None
        )
        tok_gp_hr = (
            round(good_tok / float(alloc_hr), 4)
            if isinstance(alloc_hr, (int, float)) and alloc_hr > 0
            else None
        )
        summary = {
            "total_requests": total_req,
            "on_time_requests": good_req,
            "exceeded_deadline_requests": int(
                agg.get("exceeded_deadline_requests") or 0
            ),
            "failed_requests": int(agg.get("failed_requests") or 0),
            "deadline_attainment_rate": (
                good_req / total_req if total_req > 0 else 0.0
            ),
            "on_time_output_tokens": good_tok,
            "allocated_gpu_hours": alloc_hr,
            "ready_gpu_hours": integral.get("ready_gpu_hours"),
            "overhead_gpu_fraction": integral.get("overhead_gpu_fraction"),
            "accelerator_mode": integral.get("accelerator_mode")
            or "simulated_pod_units",
            "request_goodput_per_gpu_hour": req_gp_hr,
            "request_goodput_per_gpu_min": (
                round(req_gp_hr / 60.0, 4) if req_gp_hr is not None else None
            ),
            "output_token_goodput_per_gpu_hour": tok_gp_hr,
            "calibrated_replica_rps": (
                round(good_req / (float(ready_pod_hr) * 3600.0), 6)
                if isinstance(ready_pod_hr, (int, float)) and ready_pod_hr > 0
                else None
            ),
            "calibrated_cold_start_p90_s": (
                (startup.get("aggregate") or {}).get("p90")
            ),
            "scale_down_settled": results_data.get("scale_down_settled"),
            "slack_s": agg.get("slack_s"),
        }

    total_req = int(summary.get("total_requests") or 0)
    good_req = int(summary.get("on_time_requests") or 0)
    expired_req = int(summary.get("exceeded_deadline_requests") or 0)
    failed_req = int(summary.get("failed_requests") or 0)
    attainment = float(
        summary.get("deadline_attainment_rate")
        if summary.get("deadline_attainment_rate") is not None
        else (good_req / total_req if total_req > 0 else 0.0)
    )
    alloc_hr = summary.get("allocated_gpu_hours")
    ready_hr = summary.get("ready_gpu_hours")
    overhead = summary.get("overhead_gpu_fraction")
    slack = (
        summary.get("slack_s")
        or (
            (results_data.get("aggregate") or {}).get("slack_s") if results_data else {}
        )
        or {}
    )

    if total_req <= 0 or alloc_hr is None or float(alloc_hr) <= 0:
        verdict = "INDETERMINATE"
    elif attainment < min_attainment:
        verdict = "FAIL"
    elif require_scale_down and summary.get("scale_down_settled") is not True:
        verdict = "FAIL"
    else:
        verdict = "PASS"

    req_gp_hr = summary.get("request_goodput_per_gpu_hour")
    if req_gp_hr is None and isinstance(alloc_hr, (int, float)) and alloc_hr > 0:
        req_gp_hr = round(good_req / float(alloc_hr), 4)
    req_gp_min = summary.get("request_goodput_per_gpu_min")
    if req_gp_min is None and isinstance(req_gp_hr, (int, float)):
        req_gp_min = round(float(req_gp_hr) / 60.0, 4)

    return {
        "label": label,
        "verdict": verdict,
        "total_requests": total_req,
        "on_time_requests": good_req,
        "expired_requests": expired_req,
        "failed_requests": failed_req,
        "attainment": attainment,
        "slack_p10": slack.get("p10"),
        "slack_p50": slack.get("p50"),
        "alloc_gpu_hr": float(alloc_hr) if isinstance(alloc_hr, (int, float)) else None,
        "ready_gpu_hr": float(ready_hr) if isinstance(ready_hr, (int, float)) else None,
        "overhead_frac": (
            float(overhead) if isinstance(overhead, (int, float)) else None
        ),
        "accel_mode": summary.get("accelerator_mode") or "simulated_pod_units",
        "req_goodput_hr": (
            float(req_gp_hr) if isinstance(req_gp_hr, (int, float)) else None
        ),
        "req_goodput_min": (
            float(req_gp_min) if isinstance(req_gp_min, (int, float)) else None
        ),
        "tok_goodput_hr": (
            float(summary["output_token_goodput_per_gpu_hour"])
            if isinstance(
                summary.get("output_token_goodput_per_gpu_hour"), (int, float)
            )
            else None
        ),
        "calibrated_r": summary.get("calibrated_replica_rps"),
        "calibrated_c": summary.get("calibrated_cold_start_p90_s"),
    }


def _fmt_num(val: float | int | None, fmt: str = ".2f", suffix: str = "") -> str:
    if val is None:
        return "—"
    return f"{format(val, fmt)}{suffix}"


def render_markdown_table(
    arms: list[dict[str, Any]],
    min_attainment: float = 0.99,
    baseline_label: str | None = None,
) -> str:
    """Render a multi-arm Async Autoscaling Goodput Markdown report."""
    if not arms:
        return "No runs provided.\n"

    base_arm = arms[0]
    if baseline_label:
        for a in arms:
            if a["label"] == baseline_label:
                base_arm = a
                break
    base_gpu_hr = base_arm.get("alloc_gpu_hr")

    passing = [
        a for a in arms if a["verdict"] == "PASS" and a["req_goodput_hr"] is not None
    ]
    winner_label = (
        max(passing, key=lambda x: x["req_goodput_hr"])["label"] if passing else None
    )

    headers = ["Metric"] + [
        f"**{a['label']}**" + (" (Winner)" if a["label"] == winner_label else "")
        for a in arms
    ]
    sep = [":---"] + ["---:" for _ in arms]

    def _savings_str(arm: dict[str, Any]) -> str:
        cur = arm.get("alloc_gpu_hr")
        if cur is None or base_gpu_hr is None or base_gpu_hr <= 0:
            return "—"
        if arm is base_arm:
            return "0.0% (ref)"
        pct = (1.0 - (cur / base_gpu_hr)) * 100.0
        return f"{pct:+.1f}%"

    rows = [
        ["SLO Verdict"] + [f"`{a['verdict']}`" for a in arms],
        ["Total / On-Time / Expired"]
        + [
            f"{a['total_requests']} / {a['on_time_requests']} / {a['expired_requests']}"
            for a in arms
        ],
        [f"Deadline Attainment (gate >= {min_attainment * 100:.1f}%)"]
        + [f"{a['attainment'] * 100:.2f}%" for a in arms],
        ["Deadline Slack p10 / p50 (s)"]
        + [
            f"{_fmt_num(a['slack_p10'], '.1f')} / {_fmt_num(a['slack_p50'], '.1f')}"
            for a in arms
        ],
        ["Allocated GPU-Hours (H_alloc)"]
        + [_fmt_num(a["alloc_gpu_hr"], ".4f") for a in arms],
        ["Ready GPU-Hours (H_ready)"]
        + [_fmt_num(a["ready_gpu_hr"], ".4f") for a in arms],
        ["Cold-Start & Cooldown Overhead"]
        + [
            _fmt_num(
                a["overhead_frac"] * 100.0 if a["overhead_frac"] is not None else None,
                ".1f",
                "%",
            )
            for a in arms
        ],
        [f"GPU-Hour Savings vs. {base_arm['label']}"] + [_savings_str(a) for a in arms],
        ["**Async Request Goodput (good req / GPU-hr)**"]
        + [_fmt_num(a["req_goodput_hr"], ".1f") for a in arms],
        ["Async Request Goodput (good req / GPU-min)"]
        + [_fmt_num(a["req_goodput_min"], ".2f") for a in arms],
        ["**Async Token Goodput (good tok / GPU-hr)**"]
        + [_fmt_num(a["tok_goodput_hr"], ".0f") for a in arms],
        ["Calibrated R (req / replica-s)"]
        + [_fmt_num(a["calibrated_r"], ".3f") for a in arms],
        ["Calibrated C (p90 pod startup s)"]
        + [_fmt_num(a["calibrated_c"], ".1f", "s") for a in arms],
        ["Accelerator Mode"] + [f"`{a['accel_mode']}`" for a in arms],
    ]

    lines = [
        "## Async/Batch Autoscaling Goodput Comparison",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    lines.append("")
    if winner_label:
        lines.append(
            f"**Winning Strategy**: **{winner_label}** "
            f"(highest `good req / GPU-hr` meeting deadline attainment >= {min_attainment * 100:.1f}%)."
        )
    else:
        lines.append(
            f"**Winning Strategy**: None (no strategy met deadline attainment >= {min_attainment * 100:.1f}% with valid GPU-hours)."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare Async Autoscaling Goodput across benchmark runs."
    )
    parser.add_argument(
        "--run-dir",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Strategy label and path to results dir / summary JSON / v0.2 report YAML (repeatable).",
    )
    parser.add_argument(
        "--min-attainment",
        type=float,
        default=0.99,
        help="Minimum fraction of requests completing on-time for PASS verdict (default: 0.99).",
    )
    parser.add_argument(
        "--require-scale-down",
        action="store_true",
        help="Require scale_down_settled == true for PASS verdict.",
    )
    parser.add_argument(
        "--baseline-label",
        type=str,
        default=None,
        help="Label of reference arm for GPU-hour savings (defaults to first --run-dir).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Optional path to write Markdown table.",
    )
    args = parser.parse_args(argv)

    arms: list[dict[str, Any]] = []
    for spec in args.run_dir:
        if "=" not in spec:
            parser.error(f"--run-dir must be formatted as LABEL=PATH, got: {spec!r}")
        label, raw_path = spec.split("=", 1)
        arms.append(
            load_arm_metrics(
                label.strip(),
                Path(raw_path.strip()),
                min_attainment=args.min_attainment,
                require_scale_down=args.require_scale_down,
            )
        )

    md = render_markdown_table(
        arms,
        min_attainment=args.min_attainment,
        baseline_label=args.baseline_label,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
