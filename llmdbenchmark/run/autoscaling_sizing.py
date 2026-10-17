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

Prefill:decode ratio (pd_ratio) sizes the two pools by rate matching, the rule
DistServe (OSDI'24), Splitwise (ISCA'24), DOPD (arXiv 2511.20982) and NVIDIA
Dynamo's SLA planner share: each pool needs the token rate it must serve
divided by what one replica serves within its SLO,

  prefill replicas = input_token_rate  / V_P   (peakPrefillThroughput)
  decode replicas  = output_token_rate / V_D   (peakDecodeThroughput)
  P:D              = (ISL / V_P) : (OSL / V_D)   -- independent of the rate

with both token rates measured by the harness per stage.
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
    input_token_rate: float | None = None
    output_token_rate: float | None = None
    tpot_p90: float | None = None


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stage_windows(results_dir: Path, exp_ids: str | list[str]) -> list[Stage]:
    """Traffic stages from the harness's v0.2 benchmark reports, plus the whole run.

    A multi-treatment run passes every treatment's experiment id: each stage is
    then named after its treatment, and the whole run spans all of them.
    """
    ids = [exp_ids] if isinstance(exp_ids, str) else list(exp_ids)
    stages: dict[str, Stage] = {}
    spans: list[tuple[float, float]] = []
    result_dirs = [
        (f"{exp_id} " if len(ids) > 1 else "", result_dir)
        for exp_id in ids
        for result_dir in sorted(results_dir.glob(f"{exp_id}*"))
    ]
    for label, result_dir in result_dirs:
        for report in sorted(result_dir.glob("benchmark_report_v0.2*stage_*.yaml")):
            match = re.search(r"stage_(\d+)", report.name)
            data = yaml.safe_load(report.read_text(encoding="utf-8")) or {}
            window = (data.get("run") or {}).get("time") or {}
            if not (match and window.get("start") and window.get("end")):
                continue
            name = f"{label}stage {match.group(1)}"
            aggregate = (
                (data.get("results") or {}).get("request_performance") or {}
            ).get("aggregate", {})
            throughput = aggregate.get("throughput", {})
            tpot = (
                (aggregate.get("latency") or {}).get("time_per_output_token") or {}
            ).get("p90")
            rate, tokens_in, tokens_out = (
                (throughput.get(key) or {}).get("mean")
                for key in ("request_rate", "input_token_rate", "output_token_rate")
            )
            stages.setdefault(
                name,
                Stage(
                    name,
                    _ts(window["start"]),
                    _ts(window["end"]),
                    rate,
                    tokens_in,
                    tokens_out,
                    tpot,
                ),
            )
        metadata = result_dir / "run_metadata.yaml"
        if metadata.exists():
            data = yaml.safe_load(metadata.read_text(encoding="utf-8")) or {}
            if data.get("harness_start") and data.get("harness_stop"):
                spans.append((_ts(data["harness_start"]), _ts(data["harness_stop"])))
    ordered = sorted(stages.values(), key=lambda s: s.start)
    if spans:
        ordered.append(
            Stage("whole run", min(a for a, _ in spans), max(b for _, b in spans))
        )
    return ordered


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


def _decode_replicas(rows: list[dict], stage: str, config: dict) -> float | None:
    """Decode replicas that were running during *stage*, for an observed V_D."""
    for r in rows:
        if r["stage"] == stage and r["realTarget"].endswith("-decode"):
            if r["provisioned"]["mean"]:
                return r["provisioned"]["mean"]
    return (config.get("decode") or {}).get("replicas") or None


def pd_ratio(config: dict, stages: list[Stage], rows: list[dict]) -> dict | None:
    """Rate-matched prefill and decode replica counts per stage, and their ratio."""
    so_cfg = (config.get("eppKedaSaturation") or {}).get("scaledObject") or {}
    v_p = so_cfg.get("peakPrefillThroughput") or None
    if not v_p:
        return None
    measured = [
        s
        for s in stages
        if s.name != "whole run" and s.input_token_rate and s.output_token_rate
    ]
    if not measured:
        return None

    v_d, v_d_source = so_cfg.get("peakDecodeThroughput") or None, "peakDecodeThroughput"
    if not v_d:
        # Fall back on the busiest per-replica decode rate the run achieved. Unless
        # decode was saturated this understates capacity, so the decode count is
        # an upper bound and the P:D ratio a lower bound.
        observed = [
            s.output_token_rate / n
            for s in measured
            if (n := _decode_replicas(rows, s.name, config))
        ]
        if not observed:
            return None
        v_d, v_d_source = round(max(observed), 1), "observed (lower bound)"
    v_p, v_d = float(v_p), float(v_d)

    def counts(name, seconds, tokens_in, tokens_out):
        p, d = tokens_in / v_p, tokens_out / v_d
        return {
            "stage": name,
            "durationSeconds": round(seconds),
            "inputTokenRate": round(tokens_in, 1),
            "outputTokenRate": round(tokens_out, 1),
            "prefillReplicas": max(1, math.ceil(p - 1e-9)),
            "decodeReplicas": max(1, math.ceil(d - 1e-9)),
            "decodePerPrefill": round(d / p, 2) if p else None,
        }

    per_stage = [
        counts(s.name, s.end - s.start, s.input_token_rate, s.output_token_rate)
        for s in measured
    ]
    # Whole run: token-weighted over the stages, i.e. total tokens / total time.
    seconds = sum(s.end - s.start for s in measured)
    whole = counts(
        "whole run",
        seconds,
        sum(s.input_token_rate * (s.end - s.start) for s in measured) / seconds,
        sum(s.output_token_rate * (s.end - s.start) for s in measured) / seconds,
    )
    return {
        "vP": v_p,
        "vD": v_d,
        "vDSource": v_d_source,
        "stages": per_stage,
        "wholeRun": whole,
        "peak": {
            "prefillReplicas": max(r["prefillReplicas"] for r in per_stage),
            "decodeReplicas": max(r["decodeReplicas"] for r in per_stage),
        },
        "triggers": _trigger_ratio(rows),
    }


def _trigger_ratio(rows: list[dict]) -> list[dict]:
    """What the token-aware triggers asked of each role, where both are scaled."""
    by_role: dict[str, dict[str, dict]] = {}
    for r in rows:
        role = r["realTarget"].rsplit("-", 1)[-1]
        if role in ("prefill", "decode"):
            by_role.setdefault(r["stage"], {})[role] = r["demand"]
    return [
        {
            "stage": stage,
            "prefillPeak": roles["prefill"]["peak"],
            "decodePeak": roles["decode"]["peak"],
            "prefillMean": roles["prefill"]["mean"],
            "decodeMean": roles["decode"]["mean"],
        }
        for stage, roles in by_role.items()
        if {"prefill", "decode"} <= roles.keys()
        and roles["prefill"]["peak"] is not None
    ]


def _ratio(decode_per_prefill: float | None) -> str:
    """'1 prefill : 3.2 decode' or '8 prefill : 1 decode', whichever side is larger."""
    if not decode_per_prefill:
        return "n/a"
    if decode_per_prefill >= 1:
        return f"1 prefill : {decode_per_prefill:.3g} decode"
    return f"{1 / decode_per_prefill:.3g} prefill : 1 decode"


def explain_pd_ratio(pd: dict) -> str:
    whole, peak = pd["wholeRun"], pd["peak"]
    lines = [
        "Prefill:decode ratio (rate matching, as in DistServe / Splitwise / DOPD /",
        "Dynamo planner): replicas = token rate / per-replica throughput per phase.",
        f"  V_P = {pd['vP']:g} prefill tok/s per replica (peakPrefillThroughput)",
        f"  V_D = {pd['vD']:g} decode tok/s per replica ({pd['vDSource']})",
        f"  Ideal ratio for this traffic: {_ratio(whole['decodePerPrefill'])} "
        f"(whole run, {whole['inputTokenRate']:g} in / {whole['outputTokenRate']:g} out tok/s)",
        f"  Static pools for the busiest stage: {peak['prefillReplicas']} prefill, "
        f"{peak['decodeReplicas']} decode",
    ]
    for r in pd["stages"]:
        lines.append(
            f"  {r['stage']}: {r['inputTokenRate']:g} in / {r['outputTokenRate']:g} out tok/s"
            f" -> {r['prefillReplicas']}P : {r['decodeReplicas']}D"
            f" ({_ratio(r['decodePerPrefill'])})"
        )
    for t in pd["triggers"]:
        lines.append(
            f"  token-aware triggers, {t['stage']}: prefill peak {t['prefillPeak']} "
            f"(mean {t['prefillMean']}), decode peak {t['decodePeak']} (mean {t['decodeMean']})"
        )
    lines += [
        "  The ratio depends only on the token shape (ISL/OSL) and the two per-replica",
        "  throughputs, not on the rate. V_D must be the decode rate one replica sustains",
        "  within the TPOT target (set peakDecodeThroughput); prompt tokens served from",
        "  prefix cache need no prefill, so shared-prefix traffic needs less prefill.",
    ]
    if pd["vDSource"] != "peakDecodeThroughput":
        lines.append(
            "  ! V_D was not set: the observed per-replica decode rate understates capacity"
            " unless decode was saturated, so decode counts are upper bounds.\n"
            "    Measure it: make calibrate-peak-decode NAMESPACE=<ns> SPEC=<spec>"
        )
    return "\n".join(lines) + "\n"


def peak_decode_throughput(
    stages: list[Stage], decode_replicas: int, tpot_target: float
) -> tuple[float, Stage] | None:
    """V_D: decode tok/s per replica in the busiest stage whose p90 TPOT met the target.

    A rate ladder of decode-bound traffic against a fixed decode pool makes this a
    measurement: below saturation the rate grows with load, past it TPOT breaks
    the target and the stage no longer counts.
    """
    within = [
        s
        for s in stages
        if s.name != "whole run"
        and s.output_token_rate
        and s.tpot_p90 is not None
        and s.tpot_p90 <= tpot_target
    ]
    if not within or decode_replicas < 1:
        return None
    best = max(within, key=lambda s: s.output_token_rate)
    return round(best.output_token_rate / decode_replicas, 1), best


def write_sizing(
    stack_path: Path,
    results_dir: Path,
    exp_ids: str | list[str],
    out_dir: Path,
    fetch: Fetch,
) -> list[str]:
    """Write autoscaling-sizing.{json,txt}; return the file names written."""
    sos = scaled_objects(stack_path)
    stages = stage_windows(results_dir, exp_ids)
    if not (sos and stages):
        return []
    rows = [size_stage(so, stage, fetch) for so in sos for stage in stages]
    config_file = stack_path / "config.yaml"
    config = (
        yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        if config_file.exists()
        else {}
    )
    pd = pd_ratio(config, stages, rows)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out_dir / "autoscaling-sizing.json").write_text(
        json.dumps(
            {
                "generated": generated,
                "stepSeconds": STEP_SECONDS,
                "rows": rows,
                "pdRatio": pd,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    text = explain(rows) + ("\n" + explain_pd_ratio(pd) if pd else "")
    (out_dir / "autoscaling-sizing.txt").write_text(text, encoding="utf-8")
    return ["autoscaling-sizing.json", "autoscaling-sizing.txt"]


if __name__ == "__main__":
    # make calibrate-peak-decode: python -m llmdbenchmark.run.autoscaling_sizing
    #   peak-decode <results_dir> <decode_replicas> <tpot_target_seconds>
    import sys

    _, command, results, replicas, target = sys.argv
    assert command == "peak-decode", command
    stages = stage_windows(Path(results), "")
    for s in stages[:-1] if stages and stages[-1].name == "whole run" else stages:
        tpot = f"{s.tpot_p90 * 1000:.0f}ms" if s.tpot_p90 is not None else "n/a"
        rate = f"{s.output_token_rate:.0f}" if s.output_token_rate else "n/a"
        print(f"   {s.name}: {rate} out tok/s, p90 TPOT {tpot}")
    found = peak_decode_throughput(stages, int(replicas), float(target))
    if found is None:
        sys.exit(f"no stage met p90 TPOT <= {target}s; lower RATES")
    print(
        f"PEAK_DECODE_THROUGHPUT={found[0]:g} ({found[1].name}, {replicas} decode replica(s))"
    )
    measured = [s for s in stages if s.name != "whole run" and s.output_token_rate]
    if found[1] is max(measured, key=lambda s: s.output_token_rate):
        print("! the busiest stage still met the TPOT target: decode never saturated,")
        print("  so V_D may be higher -- raise RATES and re-run")
