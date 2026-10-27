# Serving Capacity (R)

`R` is the request rate one model-server replica serves while its p90 TTFT stays within an SLO and no
request fails. An autoscaler uses it to turn an arrival rate into replicas: `replicas = arrivals / R`.

`R` is lower than the saturation rate. Past the SLO knee a replica still completes requests, but not
quickly enough to count. In the example below, one replica still served 3.6 req/s with no failures,
but p90 TTFT had reached 49 s. `R` is 1.8 req/s.

## Measure it

Stand up the guide, then run the target against it:

```bash
llmdbenchmark --spec guides/epp-keda-saturation standup -p <ns>
make calibrate-serving-capacity NAMESPACE=<ns> SPEC=guides/epp-keda-saturation R_RATES=0.6,1.2,1.8,2.4,3.6
```

Output from Qwen3-32B on H100 (TP1):

```text
🅿️  paused 1 ScaledObject(s) at 1 replica(s) for the measurement
▶️  the guide's own profile at 0.6,1.2,1.8,2.4,3.6 req/s, 120s each, against 1 replica(s)
...
📊 per stage (a stage counts only while p90 TTFT <= 2s and nothing failed):
   stage     offered req/s failed  p90 TTFT  within 2s?
   stage 0             0.6      0     0.37s  yes
   stage 1             1.2      0     0.32s  yes
   stage 2             1.8      0     1.63s  yes
   stage 3             2.4      0     9.39s  no
   stage 4             3.6      0    48.87s  no
SERVING_CAPACITY=1.8 (stage 2, 1 replica(s))
✅ SERVING_CAPACITY (R)=1.8 req/s per replica
   Use it in a ScaledObject trigger query as ${servingCapacity}:
     --set eppKedaSaturation.scaledObject.servingCapacity=1.8
```

What the target does:

1. **Holds the pool at `REPLICAS`** (default 1). It pauses every ScaledObject in the namespace with
   `autoscaling.keda.sh/paused-replicas`, waits until that many `llm-d.ai/role=decode` pods are Ready,
   and removes the pause on exit. It fails if the Ready count changes during the run.
2. **Runs a rate ladder of the guide's own workload profile.** `llmdbenchmark run` uses the scenario's
   `harness.experimentProfile` and overrides only `load.stages` (and `load.type=poisson`), so the
   token shape is the guide's.
3. **Picks R.** From the per-stage v0.2 benchmark reports, R is the highest offered rate whose stage
   had p90 TTFT ≤ `TTFT_TARGET` and zero failed requests, divided by `REPLICAS`.

If the highest rate still met the SLO, the target warns that R may be higher. Raise `R_RATES` and
re-run.

| Variable | Default | Meaning |
|---|---|---|
| `NAMESPACE`, `SPEC` | required | The stood-up stack and the guide spec it was stood up with |
| `R_RATES` | `0.25,0.5,1,1.5,2,3` | Offered req/s per stage, ascending. Bracket the knee: the top rate should fail the SLO. |
| `R_STAGE_SECONDS` | `120` | Duration of each stage |
| `TTFT_TARGET` | `2` | p90 TTFT SLO in seconds. The bundled profiles' goodput constraint uses 2. |
| `REPLICAS` | `1` | Replicas held during the measurement. R is per replica. |
| `WORKLOAD` | guide's `experimentProfile` | Another profile from `workload/profiles/inference-perf` |
| `OVERRIDES` | none | Extra `k=v` profile overrides, as for `calibrate-peak-decode` |
| `RUN_ARGS` | none | Extra arguments to `llmdbenchmark run` |

## Use it

Set the measured value with `--set eppKedaSaturation.scaledObject.servingCapacity=<R>`, or in the
scenario. `30_keda-scaledobject.yaml.j2` substitutes it into trigger queries as `${servingCapacity}`,
the same way as `${peakPrefillThroughput}`. For example, a trigger that asks for replicas from the
arrival rate:

```yaml
eppKedaSaturation:
  scaledObject:
    servingCapacity: 1.8
    triggers:
      - name: arrival-replicas
        query: 'sum(rate(llm_d_epp_request_total{namespace="${namespace}"}[1m])) / ${servingCapacity}'
        threshold: "1"
```

With `metricType: AverageValue` the HPA computes `ceil(value / threshold)`, so this trigger asks for
`ceil(arrivals / R)` replicas.

## Notes

- **Re-measure when the stack changes:** model, accelerator, parallelism, or the traffic's token
  shape. R is a request rate, so it holds for the request mix it was measured with.
- **Disaggregated (P/D) stacks:** the target counts `llm-d.ai/role=decode` pods, so R is per
  prefill/decode pair when both roles are held at one replica each.
- **A future source for R:** kubernetes-sigs/inference-perf#904 plans a measured saturation rate
  that could generate `R_RATES` automatically. GuideLLM's `goodput` profile is prior art for an
  SLO-bounded search.

## Pending-aware scaling: a dynamic stabilization window

### The problem it solves

A model-server replica takes minutes to start (model load, warmup). During that time KEDA's HPA keeps
re-reading the guide's demand signal (queue depth, running requests, KV usage, in-flight tokens). The
signal stays high, because the new replica serves nothing yet and the backlog grows. The HPA sees the
same high reading every cycle and asks for more replicas each time. This is **pod overshoot**: the
pool is sized for the backlog of the startup gap, not for the traffic.

The HPA has its own correction for pods that are not Ready, but only for `Pods` and `Resource` metrics.
KEDA serves every trigger as an `External` metric, which the HPA sizes as `ceil(value / target)`
without looking at pod readiness.

The guides' answer today is a **fixed scale-up stabilization window** (for example 300 s, about the
replica start time). It stops the overshoot by delaying every scale-up for that long. It cannot tell a
backlog that a starting replica will absorb from a real traffic spike, so a **spike also waits** the
full window, and requests queue, slow down or fail in the meantime.

### What it does

`pendingAware` replaces that fixed window with one that applies only when it should:

| Situation | Fixed 300 s scale-up window | Pending-aware |
|---|---|---|
| No replica starting | Every scale-up still waits 300 s | Scales on the guide's own signal immediately |
| Replica starting, traffic unchanged (backlog) | Waits | **Holds:** the guide's signal can hold or shrink the pool but not grow it. No overshoot. |
| Replica starting, traffic jumps (spike) | Waits 300 s | **Scales now** to `arrivals / R`, the replicas the new arrival rate needs |
| Load drops | Scales down (scale-down window) | Scales down (the guide's scale-down behavior is kept) |

The arrival rate tells a backlog from a spike. A backlog raises the guide's signal but not the rate of
incoming requests. A spike raises both. R is the request rate one replica serves within its SLO, so
`arrivals / R` is how many replicas the new traffic needs. Measure it with
`make calibrate-serving-capacity`.

It applies to any `eppKedaSaturation` guide (saturation, token-aware, inflight, P/D): the guide's
triggers are kept as the demand signal, and each P/D role is gated on its own pods.

### Use it

It is off by default. To turn it on for a stood-up guide:

```bash
llmdbenchmark --spec guides/<guide> standup -p <ns>
make pending-aware-standup NAMESPACE=<ns> SPEC=guides/<guide>     # measures R, then turns it on
llmdbenchmark --spec guides/<guide> run -p <ns>
```

`pending-aware-standup` measures R with `calibrate-serving-capacity`, or reuses `SERVING_CAPACITY=<R>`
if given. It then cleans up before the benchmark: it removes the calibration's ScaledObject pause,
restarts the router so its in-flight counts start at 0, and waits for a Ready replica. Finally it re-runs
standup with the flag on. Only the ScaledObject changes; the model servers keep running. To set it
directly instead:

```yaml
eppKedaSaturation:
  scaledObject:
    pendingAware: true     # off by default
    servingCapacity: 1.8   # R; required when pendingAware is true
```

What the template renders for each ScaledObject (each role of a P/D guide gets its own):

```
need     = max over the guide's triggers of (value / threshold)    # what the HPA would ask
pods     = pods of the role that exist           (kube-state-metrics, llm-d.ai/role)
pending  = pods not yet Ready, held 2 min after Ready, ignored after 15 min stuck
arrivals = requests/s reaching the router        (dispatched + shed + queued)

replicas = pending > 0 ?  max(min(need, pods), arrivals / R)  :  need
```

It renders as one `AverageValue` trigger with threshold 1, so the HPA asks for `ceil(replicas)`. The
scale-up stabilization window becomes 0 s, since the rule does the waiting. The guide's scale-up
policies and scale-down behavior are kept. It needs kube-state-metrics in the same Prometheus, which
OpenShift monitoring provides.
