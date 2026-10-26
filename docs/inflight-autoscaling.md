# Inflight-Requests Autoscaling Integration

How to drive the upstream **inflight-requests autoscaling** path -- the keda-epp guide's
default signal -- from `llm-d-benchmark`.

**The upstream guide is the source of truth for the mechanism** -- why in-flight requests,
the single trigger, how the threshold is chosen, the PromQL shape and its `sum()`/`or vector(0)`
requirements, verification, and cleanup:

- [llm-d workload-autoscaling § keda-epp](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling#keda-epp) -- the comparison of autoscaling paths and when to pick this one
- [keda-epp](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling/keda-epp) -- the full guide, including the `inflight` overlay this scenario benchmarks

This document covers only what is specific to this repository: which files implement the path,
how to run it, and the integration details that are easy to get wrong here.

## Files

| path | purpose |
|---|---|
| [`config/scenarios/guides/keda-epp-inflight.yaml`](../config/scenarios/guides/keda-epp-inflight.yaml) | scenario (P+D co-located): EPP plugin set + the single inflight_requests trigger |
| [`config/specification/guides/keda-epp-inflight.yaml.j2`](../config/specification/guides/keda-epp-inflight.yaml.j2) | spec pointer |
| `workload/profiles/inference-perf/random_{prefill_heavy,symmetrical,decode_heavy}.yaml.in` | the three token shapes (general-purpose, shared with the token-aware path) |
| [`experiments/inflight-autoscaling.yaml`](../experiments/inflight-autoscaling.yaml) | DoE sweeping the three shapes |

## Running

The single trigger divides by a fixed per-replica concurrency target, so standup goes straight to the benchmark.

### 1. Stand up

```bash
llmdbenchmark --spec guides/keda-epp-inflight standup \
  -p <namespace> \
  --cluster-config config/cluster-configs/examples/<your-cluster>.yaml
```

Creates the decode Deployment (1 replica, TP=2), the EPP with `inflight-load-producer`, the EPP
`ServiceMonitor`, the Prometheus/Thanos auth Secret, and the `ScaledObject` carrying the single
inflight_requests trigger at its shipped threshold (16).

### 2. Benchmark

```bash
# one shape
llmdbenchmark --spec guides/keda-epp-inflight run \
  --harness inference-perf --workload random_decode_heavy.yaml \
  --monitoring

# all three, sequentially, against the same stack
llmdbenchmark --spec guides/keda-epp-inflight run \
  --harness inference-perf --experiments experiments/inflight-autoscaling.yaml \
  --monitoring
```

The three shapes share one autoscaled pool, so a shape can start above `minReplicas` while the
previous shape's replicas drain (the `scaleDown` policy removes one pod per 300s). For a
clean-start comparison, run each shape with `--workload` on a freshly stood-up stack.

## How it is wired here

This path **reuses the `eppKedaSaturation` machinery** -- Thanos auth, `TriggerAuthentication`,
the EPP `ServiceMonitor`, and the per-stack `ScaledObject` renderer
([`30_keda-scaledobject.yaml.j2`](../config/templates/jinja/30_keda-scaledobject.yaml.j2)) --
with a `scaledObject.triggers` list of exactly one trigger, which `defaults.yaml` explicitly
invites. There is no new standup step, no new controller, no calibration step, and no new config
block.

One `query` substitution exists for this path, because Jinja cannot reach inside a PromQL string:

| token | why |
|---|---|
| `${namespace}` | the query must pin the namespace label; on a cluster-wide store an unpinned query aggregates every EPP on the cluster |

There is **no** `${peakPrefillThroughput}`: the request-count path has no calibrated divisor. The
trigger divides by the fixed threshold 16 (a per-replica concurrency target), spelled out in the
scenario so it can be tuned per experiment without editing `defaults.yaml`.

`scaledObject.nameSuffix` renders the object as `<model>-decode-inflight`, so this and the
token-aware and saturation variants can be told apart in `kubectl get hpa`.

Two deliberate divergences from the upstream inflight overlay, so "as shipped" is precise:
the **signal and threshold are as shipped**, but the **HPA behavior policies are not** --
the overlay reacts with a 300s stabilization window and `Percent 100/15s`, while this scenario
uses 1 pod per 180s with no scale-up window, because model weights take minutes to load and a
replica bought on a burst that drains in seconds is pure cost. The query also adds `or vector(0)`
(not in the overlay), a defensive collapse of an empty/idle result to the single 0-series KEDA
requires. Neither changes whether steady-state in-flight crosses 16; both change the scale
trajectory, which is intended.

## Integration details that fail silently

### The EPP must run `inflight-load-producer`

`llm_d_epp_inflight_requests` is published by the `inflight-load-producer` plugin. **This is not a
bare-EPP default** -- the optimized-baseline guide configures it in its `EndpointPickerConfig`,
which is why the metric exists on that deployment. A bare EPP with no plugin config does not run
the producer, and the metric is simply absent (the trigger's `or vector(0)` then pins the signal at
0 and nothing scales). The scenario mirrors optimized-baseline's EPP config for this reason.

Structure matters: a producer is **not** a Filter/Scorer/Picker. The producers go in the top-level
`plugins` list; only the filter/scorer go in `schedulingProfiles`. Listing a producer under
`schedulingProfiles` crashloops the EPP (`must implement Filter, Scorer, or Picker`).

### EPP replicas must stay at 1

The `inflight-load-producer`'s request accounting is local to each EPP process. With N replicas
the summed trigger can under-count the true per-endpoint load (for example when a scrape gap or a
series-label collision drops one replica's contribution), so the autoscaler under-scales. A single
EPP keeps the accounting whole and sidesteps the question of whether a cross-replica `sum()` would
recover the total, so the scenario pins `router.epp.replicas: 1`.

### The signal is in-flight concurrency, not offered rate

`llm_d_epp_inflight_requests` tracks requests in flight, which by Little's Law is
`rate x service_time`, not the arrival rate. A request-count signal is therefore blind to
per-request size: a prefill-heavy shape (few long requests) and a decode-heavy shape (many short
requests) can sit at very different in-flight counts for the same offered rate. Whether a given
stock shape crosses the per-replica threshold of 16 under open-loop Poisson load is the question
[`experiments/inflight-autoscaling.yaml`](../experiments/inflight-autoscaling.yaml) exists to
answer. Closed-loop concurrent load pins in-flight at the concurrency level by construction and so
crosses the threshold trivially -- it exercises the plumbing but is not a realistic grounding of the
threshold; the benchmark uses the stock open-loop Poisson shapes.

### `model.maxNumBatchedTokens` must be set

`_macros.j2` falls back to a hardcoded **`256`** when nothing sets it, and no guide scenario does.
An 8192-token prompt then becomes 32 chunked prefill passes, so a prefill-heavy run measures
chunking overhead rather than prefill capacity. Set it on **`model`**
([`ModelConfig`](../llmdbenchmark/parser/config_schema.py)); `_macros.j2` also probes
`vllmCommon.maxNumBatchedTokens`, but `STRICT_CONFIG` rejects that path, so it is unreachable. This
path has no calibration, so the value is purely to measure the prefill-heavy shape fairly.
