"""Replica sizing for a traffic shape, from a run's KEDA ScaledObjects.

For each ScaledObject the run deployed (30_keda-scaledobject) and each traffic
stage the harness reported, replay the ScaledObject's own trigger queries over
that stage's window and compare three replica counts:

  demand       what the strategy's thresholds ask for at each instant, before
               the HPA behavior paces it: max over triggers of
               ceil(value / threshold), clamped to [minReplicaCount,
               maxReplicaCount]. Its peak is the static size that keeps every
               trigger at or under its threshold for this traffic.
  decision     the replica count KEDA's HPA actually chose (paced by behavior
               and stabilization windows). Under eppKedaSaturation.dryRun this
               is the count it would have set.
  provisioned  the replica count the real model-server Deployment had.

Over/under-provisioning of both the decision and the real pool is measured
against demand, in replica-seconds: a slow scale-up shows as under, a slow
scale-down or a static pool sized for the peak as over.

Only AverageValue triggers are sized (every guide trigger is); the HPA
computes those as ceil(value / threshold) independent of pod counts.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

STEP_SECONDS = 15

# fetch(promql, start, end) -> [(unix_ts, value), ...] at STEP_SECONDS resolution.
Fetch = Callable[[str, float, float], list[tuple[float, float]]]


@dataclass
class Stage:
    name: str
    start: float
    end: float
    request_rate: float | None = None


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stage_windows(results_dir: Path, exp_id: str) -> list[Stage]:
    """Traffic stages from the harness's v0.2 benchmark reports, plus the whole run."""
    stages: dict[str, Stage] = {}
    whole: Stage | None = None
    for result_dir in sorted(results_dir.glob(f"{exp_id}*")):
        for report in sorted(result_dir.glob("benchmark_report_v0.2*stage_*.yaml")):
            match = re.search(r"stage_(\d+)", report.name)
            data = yaml.safe_load(report.read_text(encoding="utf-8")) or {}
            window = (data.get("run") or {}).get("time") or {}
            if not (match and window.get("start") and window.get("end")):
                continue
            name = f"stage {match.group(1)}"
            throughput = (
                ((data.get("results") or {}).get("request_performance") or {})
                .get("aggregate", {})
                .get("throughput", {})
            )
            rate = (throughput.get("request_rate") or {}).get("mean")
            stages.setdefault(
                name, Stage(name, _ts(window["start"]), _ts(window["end"]), rate)
            )
        metadata = result_dir / "run_metadata.yaml"
        if whole is None and metadata.exists():
            data = yaml.safe_load(metadata.read_text(encoding="utf-8")) or {}
            if data.get("harness_start") and data.get("harness_stop"):
                whole = Stage(
                    "whole run", _ts(data["harness_start"]), _ts(data["harness_stop"])
                )
    ordered = sorted(stages.values(), key=lambda s: s.start)
    return ordered + ([whole] if whole else [])


def scaled_objects(stack_path: Path) -> list[dict]:
    rendered = next(stack_path.glob("30_keda-scaledobject*.yaml"), None)
    if rendered is None:
        return []
    docs = yaml.safe_load_all(rendered.read_text(encoding="utf-8"))
    return [d for d in docs if d and d.get("kind") == "ScaledObject"]


def _series(fetch: Fetch, query: str, stage: Stage) -> dict[float, float]:
    return dict(fetch(query, stage.start, stage.end))


def _stats(values: list[float]) -> dict:
    if not values:
        return {"peak": None, "mean": None}
    return {"peak": int(max(values)), "mean": round(sum(values) / len(values), 2)}


def _against_demand(
    series: dict[float, float], timestamps: list[float], demand: list[int]
) -> dict | None:
    """Replica-seconds above and below demand, at the demand sample times."""
    pairs = [
        (series[ts], d)
        for ts, d in zip(timestamps, demand)
        if series.get(ts) is not None
    ]
    if not pairs:
        return None
    return {
        "overReplicaSeconds": int(sum(max(r - d, 0) for r, d in pairs) * STEP_SECONDS),
        "underReplicaSeconds": int(sum(max(d - r, 0) for r, d in pairs) * STEP_SECONDS),
        "underProvisionedFraction": round(sum(d > r for r, d in pairs) / len(pairs), 2),
    }


def size_stage(so: dict, stage: Stage, fetch: Fetch) -> dict:
    meta, spec = so["metadata"], so["spec"]
    ns = meta.get("namespace", "")
    lo = int(spec.get("minReplicaCount", 1))
    hi = int(spec.get("maxReplicaCount", 10))
    triggers = [
        t
        for t in spec.get("triggers", [])
        if t.get("metricType", "AverageValue") == "AverageValue"
    ]

    # demand(t) = clamp(max_i ceil(value_i(t) / threshold_i)); an absent sample
    # is 0, as KEDA's prometheus scaler treats an empty result.
    per_trigger = []
    for t in triggers:
        threshold = float(t["metadata"]["threshold"])
        series = _series(fetch, f"max({t['metadata']['query'].strip()})", stage)
        per_trigger.append((t.get("name", "trigger"), threshold, series))
    timestamps = sorted({ts for _, _, s in per_trigger for ts in s})
    demand, driver_at_peak, raw_peak = [], None, 0
    for ts in timestamps:
        wants = [
            (math.ceil(s.get(ts, 0.0) / th - 1e-9), name, s.get(ts, 0.0), th)
            for name, th, s in per_trigger
        ]
        raw, name, value, th = max(wants)
        demand.append(min(hi, max(lo, raw)))
        if raw > raw_peak or driver_at_peak is None:
            raw_peak, driver_at_peak = (
                raw,
                {"trigger": name, "value": value, "threshold": th},
            )

    hpa = (spec.get("advanced", {}).get("horizontalPodAutoscalerConfig", {}) or {}).get(
        "name"
    ) or f"keda-hpa-{meta['name']}"
    decision = _series(
        fetch,
        f'max(kube_horizontalpodautoscaler_status_desired_replicas{{namespace="{ns}",horizontalpodautoscaler="{hpa}"}})',
        stage,
    )
    real = (
        (meta.get("annotations") or {}).get("llm-d.ai/autoscaling-dry-run-target")
        or f"{spec['scaleTargetRef'].get('kind', 'Deployment')}/{spec['scaleTargetRef']['name']}"
    )
    real_kind, real_name = real.split("/", 1)
    provisioned = (
        _series(
            fetch,
            f'max(kube_deployment_spec_replicas{{namespace="{ns}",deployment="{real_name}"}})',
            stage,
        )
        if real_kind == "Deployment"
        else {}
    )

    result = {
        "scaledObject": meta["name"],
        "realTarget": real,
        "dryRun": "llm-d.ai/autoscaling-dry-run-target"
        in (meta.get("annotations") or {}),
        "stage": stage.name,
        "durationSeconds": round(stage.end - stage.start),
        "requestRate": stage.request_rate,
        "minReplicas": lo,
        "maxReplicas": hi,
        "demand": {
            **_stats(demand),
            "drivenBy": driver_at_peak,
            "unclampedPeak": raw_peak,
        },
        "decision": {
            **_stats(list(decision.values())),
            "vsDemand": _against_demand(decision, timestamps, demand),
        },
        "provisioned": {
            **_stats(list(provisioned.values())),
            "vsDemand": _against_demand(provisioned, timestamps, demand),
        },
    }
    return result


def _versus(stats: dict) -> str:
    if stats["peak"] is None:
        return "n/a"
    text = f"peak {stats['peak']}, mean {stats['mean']}"
    vs = stats.get("vsDemand")
    if not vs:
        return text
    parts = []
    if vs["underReplicaSeconds"]:
        parts.append(
            f"short {vs['underReplicaSeconds']} replica-s "
            f"({int(vs['underProvisionedFraction'] * 100)}% of the time)"
        )
    if vs["overReplicaSeconds"]:
        parts.append(f"over by {vs['overReplicaSeconds']} replica-s")
    return f"{text} -> {', '.join(parts) or 'matches demand'}"


def explain(rows: list[dict]) -> str:
    """Plain-language summary of size_stage() rows."""
    lines = []
    for so in dict.fromkeys(r["scaledObject"] for r in rows):
        mine = [r for r in rows if r["scaledObject"] == so]
        first = mine[0]
        mode = "dry-run (decisions not actuated)" if first["dryRun"] else "live"
        lines.append(f"{so} -> {first['realTarget']} [{mode}]")
        # The whole run also covers the gaps between stage reports, where a
        # stage's backlog drains; prefer naming a stage when one has the peak.
        peak = max(
            (r for r in mine if r["demand"]["peak"] is not None),
            key=lambda r: (r["demand"]["peak"], r["stage"] != "whole run"),
            default=None,
        )
        if peak is None:
            lines.append("  no trigger samples in the run window; nothing to size")
            continue
        d = peak["demand"]
        why = d["drivenBy"]
        where = (
            "between traffic stages"
            if peak["stage"] == "whole run" and len(mine) > 1
            else f"in {peak['stage']}"
        )
        lines.append(
            f"  Max replicas for this traffic shape: {d['peak']} "
            f"({where}: {why['trigger']} reached {why['value']:g} "
            f"against {why['threshold']:g} per replica)"
        )
        if d["unclampedPeak"] > first["maxReplicas"]:
            lines.append(
                f"  ! demand wanted {d['unclampedPeak']}, above maxReplicaCount "
                f"{first['maxReplicas']}: the cap, not the traffic, set the peak"
            )
        for r in mine:
            rate = f", {r['requestRate']:.1f} req/s" if r["requestRate"] else ""
            lines.append(
                f"  {r['stage']} ({r['durationSeconds']}s{rate}): "
                f"demand peak {r['demand']['peak']}, mean {r['demand']['mean']}"
            )
            for label, key in (
                ("KEDA decision", "decision"),
                ("provisioned", "provisioned"),
            ):
                lines.append(f"    {label:<14}{_versus(r[key])}")
    lines += [
        "",
        "demand: replicas the triggers ask for, max over triggers of",
        "  ceil(value / threshold), clamped to min/max - its peak is the static size",
        "  that keeps every trigger at or under threshold for this traffic.",
        "KEDA decision: what the HPA chose, paced by the behavior policy; short",
        "  means scale-up lagged demand, over means scale-down lagged it.",
        "provisioned: replicas the real Deployment had. Both are compared with",
        "  demand in replica-seconds (1 replica short for 60s = 60 replica-s).",
        "Measured against the pool that was running: in a dry-run, or while",
        "under-provisioned, per-replica load and latency are higher than they would",
        "be at the demanded size, so demand is an upper bound.",
    ]
    return "\n".join(lines) + "\n"


def write_sizing(
    stack_path: Path, results_dir: Path, exp_id: str, out_dir: Path, fetch: Fetch
) -> list[str]:
    """Write autoscaling-sizing.{json,txt}; return the file names written."""
    sos = scaled_objects(stack_path)
    stages = stage_windows(results_dir, exp_id)
    if not (sos and stages):
        return []
    rows = [size_stage(so, stage, fetch) for so in sos for stage in stages]
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out_dir / "autoscaling-sizing.json").write_text(
        json.dumps(
            {"generated": generated, "stepSeconds": STEP_SECONDS, "rows": rows},
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "autoscaling-sizing.txt").write_text(explain(rows), encoding="utf-8")
    return ["autoscaling-sizing.json", "autoscaling-sizing.txt"]
