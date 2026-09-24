# Token-Aware Autoscaling Integration

How to drive the upstream **token-aware autoscaling** path from `llm-d-benchmark`.

**The upstream guide is the source of truth for the mechanism** — why tokens, the two
triggers, how the thresholds are derived, the PromQL shapes and their `sum()`/`or vector(0)`
requirements, verification, and cleanup:

- [llm-d workload-autoscaling § Token-Aware Autoscaling](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling#token-aware-autoscaling) — the comparison of autoscaling paths and when to pick this one
- [keda-epp-token-aware](https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling/keda-epp-token-aware) — the full guide, including "Choosing the two thresholds"
- [`calibrate.sh`](https://github.com/llm-d/llm-d/tree/main/guides/recipes/router/calibration) — the `peakPrefillThroughput` calibration recipe

This document covers only what is specific to this repository: which files implement the path,
how to run it, and the integration details that are easy to get wrong here.

> **Experimental.** The upstream guide is experimental; its metrics and thresholds may change.

## Files

| path | purpose |
|---|---|
| [`config/scenarios/guides/keda-epp-token-aware.yaml`](../config/scenarios/guides/keda-epp-token-aware.yaml) | scenario: EPP plugin set + the token-aware trigger list |
| [`config/specification/guides/keda-epp-token-aware.yaml.j2`](../config/specification/guides/keda-epp-token-aware.yaml.j2) | spec pointer |
| `workload/profiles/inference-perf/random_{prefill_heavy,symmetrical,decode_heavy}.yaml.in` | the three token shapes (general-purpose, shared) |
| [`experiments/token-aware-autoscaling.yaml`](../experiments/token-aware-autoscaling.yaml) | DoE sweeping the three shapes |

## Running

Three commands, in this order. Calibration sits between standup and the benchmark
deliberately -- see [why it cannot be part of standup](#why-calibration-is-a-separate-command).

### 1. Stand up

```bash
llmdbenchmark --spec guides/keda-epp-token-aware standup \
  -p <namespace> \
  --cluster-config config/cluster-configs/examples/openshift-pokprod.yaml
```

Creates the decode Deployment (1 replica, TP=2), the EPP with `inflight-load-producer`, the EPP
`ServiceMonitor`, the Prometheus/Thanos auth Secret, and the `ScaledObject` — the last carrying the
scenario's **placeholder** `peakPrefillThroughput`.

### 2. Calibrate, and write the value in

```bash
make calibrate-peak-prefill NAMESPACE=<namespace> APPLY=1
```

Leave `APPLY=1` off to measure and print without touching the stack.

### 3. Benchmark

```bash
# one shape
llmdbenchmark --spec guides/keda-epp-token-aware run \
  --harness inference-perf --workload random_prefill_heavy.yaml \
  --monitoring --analyze

# all three, sequentially
llmdbenchmark --spec guides/keda-epp-token-aware experiment \
  --experiments experiments/token-aware-autoscaling.yaml \
  --monitoring -g METRICS_COLLECTION_INTERVAL --analyze
```

### Why calibration is a separate command

The `ScaledObject` is applied in **standup step 03** (`workload_monitoring`), while the model servers
are not deployed until **step 09**. The autoscaler therefore exists minutes before anything is
serving — KEDA tolerates this, and the HPA reports `<unknown>` until the pods and their metrics
appear.

That ordering is the whole problem: `V_P` can only be measured against a stack that is already
serving, which is four steps after the object that consumes it was rendered. So the value cannot be
templated in; it has to be written afterwards. Step 2 is what does that.

### How the measured value reaches both consumers

`calibrate.sh` only measures and prints. `make calibrate-peak-prefill APPLY=1` then writes the value
into the two places that must agree, both of which hold the number *inside a string* that neither
Helm nor Jinja can reach:

| consumer | how it is written |
|---|---|
| EPP plugins `ConfigMap` (`prefix-cache-affinity-filter.parameters`) | `sed -E "s/(peakPrefillThroughput: *)[0-9]+/\1$VP/g"` on the live object, re-applied |
| `ScaledObject` prefill trigger divisor | the parked manifest is rewritten with `re.sub(r'(llm_d_epp_inflight_tokens[\s\S]*?/\s*)\d+', ...)`, then re-applied |

The ScaledObject regex anchors on the metric name, so it touches only the divisor that follows
`llm_d_epp_inflight_tokens` — the decode trigger's `kv_cache_usage_perc` query is left alone.

Afterwards the target runs `kubectl rollout restart deployment/<release>-epp`, because the EPP reads
its plugin config only at startup. That restart is gated on the ConfigMap having actually changed, so
a stack with no `prefix-cache-affinity-filter` is not bounced for nothing.

### The autoscaler is removed while measuring

Step 2 saves the `ScaledObject`, **deletes it**, measures, then recreates it already carrying the
measured divisor. An `EXIT` trap restores it if calibration fails or is interrupted, so parking is
never a one-way door.

The load itself is not what forces this. `calibrate.sh` sends requests **sequentially** with
`max_tokens: 1`, so at most one `CHUNK_SIZE` request is in flight, which at the shipped
`V_P` = 15845 and a 350,208-token KV cache is nowhere near either threshold:

| trigger | value during calibration | shipped threshold | replicas requested |
|---|---|---|---|
| prefill backlog | 8192 / 15845 = **0.52 s** | 1.5 s | 1 |
| decode KV | 8192 / 350208 = **2.3 %** | 0.8 | 1 |

Reaching the prefill threshold would take ~2.9 concurrent 8192-token requests; calibration never
exceeds one. The autoscaler is parked anyway, for two reasons that do not depend on those numbers:

1. **A scale-up mid-measurement would corrupt the result.** `V_P = chunkSize / median(TTFT)`; adding a
   replica partway through sends traffic to a cold pod, TTFT jumps, and the median is poisoned.
   Parking makes that impossible rather than merely unlikely.
2. **The divisor being replaced is the one in use.** Patching a live trigger means KEDA is evaluating
   the old constant at the moment it is being swapped. Delete-and-recreate removes that window.

The thresholds are also configurable: at a prefill threshold of `0.4` a single calibration request
*would* trip the trigger, so the guarantee should not rest on a value nobody re-checked.

## How it is wired here

This path **reuses the `eppKedaSaturation` machinery** — Thanos auth, `TriggerAuthentication`,
the EPP `ServiceMonitor`, and the per-stack `ScaledObject` renderer
([`30_keda-scaledobject.yaml.j2`](../config/templates/jinja/30_keda-scaledobject.yaml.j2)) —
with a redefined `scaledObject.triggers` list, which `defaults.yaml` explicitly invites. There is
no new standup step, no new controller, and no new config block.

Two `query` substitutions exist for this path, because Jinja cannot reach inside a PromQL string:

| token | why |
|---|---|
| `${namespace}` | both queries must pin the namespace label; on a cluster-wide store an unpinned query aggregates every EPP on the cluster |
| `${peakPrefillThroughput}` | keeps the calibrated constant in config instead of as a magic number in the query |

`scaledObject.nameSuffix` renders the object as `<model>-decode-token-aware`, so this and the
saturation variant can be told apart in `kubectl get hpa`.

## Integration details that fail silently

Upstream documents the EPP invariants (single EPP replica, an EPP carrying
[llm-d-router#2577](https://github.com/llm-d/llm-d-router/pull/2577), model-server monitoring
applied). These are the ones specific to *this* repo:

### `model.maxNumBatchedTokens` must be set

`_macros.j2` falls back to a hardcoded **`256`** when nothing sets it, and no guide scenario does.
An 8192-token prompt then becomes 32 chunked prefill passes, so a prefill-heavy run measures
chunking overhead rather than prefill capacity.

Set it on **`model`** ([`ModelConfig`](../llmdbenchmark/parser/config_schema.py)). `_macros.j2`
also probes `vllmCommon.maxNumBatchedTokens`, but `STRICT_CONFIG` rejects that path, so it is
unreachable. It must equal the calibration's `CHUNK_SIZE`.

### What `make calibrate-peak-prefill` adds around the recipe

A standalone target on purpose: V_P is needed only by this path (and by any router config using
`prefix-cache-affinity-filter`), so it stays out of the standup pipeline, `defaults.yaml` and the
shared step registry. It **fetches llm-d's own `calibrate.sh` at run time** (`CALIBRATION_REF`,
default `main`) rather than vendoring a copy, so there is one implementation of the measurement and
nothing in this repo to keep in step with it.

Around that it supplies the four things the recipe leaves to the operator:

1. **Endpoint override.** `calibrate.sh` auto-discovers a Service named `${GUIDE_NAME}-epp`; under
   modelservice the EPP Service is named after the *model*, so that lookup misses. The target finds
   the real `*-epp` Service and passes `VLLM_ENDPOINT`.
2. **Chunk-size guard.** Refuses to run when `CHUNK_SIZE` disagrees with the serving
   `VLLM_MAX_NUM_BATCHED_TOKENS` read off the live pods — a larger chunk is prefilled in several
   passes, so the measured TTFT would not be one prefill pass.
3. **Idle guard.** Refuses to run unless `kv_cache_usage_perc` and `num_requests_running` are quiet
   on every decode pod.
4. **Spread report.** `calibrate.sh` prints only the median, so the target reads the per-sample TTFT
   lines out of the Job it leaves behind and warns above 50%.

It is conservative about what it changes: the EPP is restarted only if a ConfigMap was actually
patched, and it fails if the ScaledObject was updated while the router was not — that combination
leaves the two scaling on different constants. On a stack whose router has no
`prefix-cache-affinity-filter` it reports that nothing consumes V_P and changes nothing.

Variables: `NAMESPACE` (required), `APPLY` (default 0), `CHUNK_SIZE` (default 8192),
`CALIBRATION_REF` (default `main`).

Needs `envsubst`, which `calibrate.sh` requires and `install.sh` does not provision
(`brew install gettext` / `apt-get install gettext-base`); the target's `check-envsubst`
prerequisite says so if it is missing.

### Why those checks matter

Evidence from one reference stack (Qwen3-32B / H100-80GB / TP=2), same command, minutes apart:

| run | V_P | sample spread | verdict |
|---|---|---|---|
| load still draining | 14241 | **846%** of median (one 5.39 s outlier) | reject |
| verified idle | **15845** | 5.4% of median | use |

The difference was harness pods still draining when the first run started. That is a 10% error in a
constant that sets replica counts, from a command that reported success both times — which is why
the spread is worth a look before you commit the number.

### V_P is an estimate, not a fixed property

A clean measurement is reproducible, but not identical run to run. Eight clean measurements on the
*same* stack (Qwen3-32B / H100-80GB / TP=2, `max_num_batched_tokens=8192`) spanned
**15389-16037 tok/s — about 4%**:

```
15845  15810  15652  15661  15743  16037  15460  15389
```

Two consequences:

- **Do not chase small differences.** A 2-3% move between runs is normal scatter, not a regression.
  Only a departure on the scale of the contaminated run above (~10%) indicates something real.
- **Re-measure when the serving path changes**, not on a schedule. `V_P` is a property of the
  (model, accelerator, TP, `max_num_batched_tokens`, vLLM version, routing path) tuple; any of those
  moving invalidates it. The threshold arithmetic tolerates a few percent, but not a figure borrowed
  from different hardware — the same offered load asked for 8 replicas at `V_P` = 2696 and 3 at
  `V_P` = 15928 on one reference stack.

The value shipped in the scenario is one such measurement. Treat it as a starting point and run the
calibration on your own stack.

## The three profiles

General-purpose token-shape workloads, not specific to autoscaling — this guide is one consumer.
They differ **only** in ISL/OSL and offered rate, so the token shape is the independent variable:

| profile | ISL/OSL | ratio | expected to bind on |
|---|---|---|---|
| `random_prefill_heavy` | 8192/256 | 32:1 | prefill backlog |
| `random_symmetrical` | 2048/2048 | 1:1 | either — the crossover case |
| `random_decode_heavy` | 256/4096 | 1:16 | decode KV |

Two constraints, both load-bearing:

1. **Open loop** (`load.type: poisson` with a `rate`), never `concurrent`. Upstream explains why
   in its closed-loop note: `inflight_tokens` settles at `concurrency x ISL` and is invariant to
   capacity, so the prefill trigger pegs at `maxReplicaCount` by construction. Every existing
   `*_concurrent*` profile is therefore unusable for this guide.
2. **`data.type: random`**, never `shared_prefix`. `inflight_tokens` counts *uncached* prompt
   tokens, so a shared-prefix dataset turns most of the prompt into a cache hit and collapses the
   prefill signal — prefill-heavy then behaves like decode-heavy. Confirm with
   `vllm:prompt_tokens_by_source_total{source="local_cache_hit"}`.

Stage duration should be at least ~2x the `scaleUp` period (180 s) or the run measures the HPA's
rate limiter rather than the token signal; the shipped profiles use 1200 s.

Override without editing a profile (`apply_overrides` accepts integer path segments):

```bash
--overrides load.stages.0.rate=1.5,load.stages.0.duration=600
```

### Deriving the offered rate

`V_P` governs the **prefill** dimension only. For the decode-bound shapes the binding constraint
is decode throughput, so take each rate as the minimum of both targets:

```text
prefill capacity (fleet) = V_P / ISL x replicas            [req/s]
decode capacity  (fleet) = decode_tokens_per_sec / OSL      [req/s]
rate = min( util_p x prefill_capacity, util_d x decode_capacity )
```

**Measure fleet decode capacity; do not derive it from a single-stream ITL.** An ITL taken at low
concurrency overstates decode throughput badly — on one reference stack an ITL of 0.017 s measured
at ~18 concurrency implied ~3.6x the throughput actually delivered at the ~190 concurrency the
decode-bound shapes reach, and the resulting rate saturated KV at 99.9% with 350 requests queued.
Sample `vllm:request_success_total` over a fixed interval while the pool is saturated — that is
capacity by definition.

## Reading a run

Beyond the harness report, `--monitoring` captures
`metrics/processed/replica_status_timeseries.json` (desired/ready replicas over time), which is
what makes time-to-scale and replica flapping measurable.

**Trigger attribution** — which trigger's `ceil()` won, from the two HPA `TARGETS` values — is the
most informative output, because it is what distinguishes token-aware from any request-counting
autoscaler. Prefill should win prefill-heavy; decode should win decode-heavy. If prefill-heavy and
decode-heavy scale identically, something in the setup is wrong (EPP replicas > 1, a shared-prefix
dataset, or closed-loop load) — that is not a finding about token-awareness.

Before trusting any number, check three things: failure count, achieved-vs-nominal ISL/OSL, and
whether TTFT settled or climbed monotonically. A saturated run measures the queue, not the pool.
