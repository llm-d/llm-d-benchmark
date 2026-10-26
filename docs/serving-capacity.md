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
