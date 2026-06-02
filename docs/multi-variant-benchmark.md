# Multi-Variant Benchmark (WVA Topology B)

End-to-end recipe for benchmarking the
[Workload Variant Autoscaler (WVA)](https://github.com/llm-d/llm-d-workload-variant-autoscaler)
in a **two-variant** setup: one model, two `Deployment`s of differing
`variantCost`, both registered into the same `InferencePool` / EPP.
Exercises WVA's cost-aware optimizer, which steers scale-up toward the
cheaper variant first when the shared pool saturates.

This is the canonical recipe for [issue #1425](
https://github.com/llm-d/llm-d-benchmark/issues/1425).

---

## Prerequisites

- A Kubernetes / OpenShift cluster with vLLM-capable GPUs (the bundled
  scenario targets H100 and `unsloth/Meta-Llama-3.1-8B`; both knobs are
  in the scenario yaml).
- `llmdbenchmark` CLI installed, plus `kubectl`, `helm`, and `python3` on
  the workstation.
- A namespace you have admin in; the steps below use `<ns>` as a stand-in.

> **Important — WVA chart version.** This scenario demonstrates
> cost-aware behavior correctly only with a WVA chart that includes
> [llm-d/llm-d-workload-variant-autoscaler#1198](
> https://github.com/llm-d/llm-d-workload-variant-autoscaler/pull/1198)
> (drop `model_name` filter from the `cache_config_info` query).
> Without that fix, V2's per-replica capacity reading falls back to the
> per-step batch budget (~6.5K tokens/pod for typical vLLM defaults vs.
> ~412K real KV memory on Llama-3.1-8B / H100), causing both variants
> to run away to `maxReplicas` and the cost-aware demonstration to
> collapse. The fix landed on `main` 2026-05-27 but is **not** in the
> tagged `v0.6.0` or `v0.7.0` chart releases.

---

## The five steps

### 1. Stand up the primary variant

```bash
llmdbenchmark --spec guides/two-variant-wva standup -p <ns>
```

This installs the four standard helm releases (`llm-d-infra`,
`inferencepool-gaie`, `llm-d-modelservice`, `workload-variant-autoscaler`)
with the primary variant only — `unsloth/Meta-Llama-3.1-8B` at
`variantCost: "10.0"`, `min/maxReplicas: 1/10`.

### 2. Enable WVA's V2 saturation analyzer

Apply the included override ConfigMap:

```bash
kubectl apply -n <ns> -f config/scenarios/guides/two-variant-wva-v2-config.yaml
```

This sets `analyzerName: saturation` on the WVA controller's saturation
config so it runs the V2 (token-based, cost-aware) path. The WVA chart
template at v0.7.0 does not yet expose `analyzerName` in its values, so
this is a separate manifest until that lands; see the [chart-side
follow-up](#chart-side-follow-up) below.

Verify V2 is active:

```bash
kubectl logs -n <ns> -l app.kubernetes.io/name=workload-variant-autoscaler \
  --tail=50 | grep "Processing model (V2)"
```

You should see `Processing model (V2)` lines once per reconcile.

### 3. Layer on the secondary variant

```bash
python tools/add_variant.py -n <ns> \
    --config config/scenarios/guides/variants/v2-cost-only.yaml
```

`add_variant.py` reads a small variant config yaml (cost, replica
bounds, optional shape overrides) and creates the secondary
`Deployment`, `VariantAutoscaling`, and `HPA` from the primary's, with
the label trick that makes both deployments register into the same
`InferencePool`. See [Variant config schema](#variant-config-schema)
below.

The bundled `v2-cost-only.yaml` keeps the secondary's operational shape
identical to the primary (TP=1, 1 GPU/pod), changing only the
`variantCost` to `5.0`. The result: one model served by two
deployments, identical capacity per pod, asymmetric cost.

Verify both Deployments and VAs are present:

```bash
kubectl get va,hpa,deploy -n <ns> -l llm-d.ai/inferenceServing=true
```

### 4. Run the benchmark

```bash
llmdbenchmark --spec guides/two-variant-wva run \
    -p <ns> -l guidellm -w prefill_heavy.yaml
```

Defaults to `prefill_heavy.yaml` (4000-token prompts, 1000-token
outputs, Poisson at the workload's `rate`). Substitute any other
profile you want.

### 5. Teardown

```bash
llmdbenchmark --spec guides/two-variant-wva teardown -p <ns>
```

The secondary variant's `Deployment` / `VA` / `HPA` are owned by the
modelservice helm release, so teardown removes them along with the
primary.

---

## Variant config schema

Each `tools/variants/*.yaml` declares only what differs from the
primary; everything else is inherited.

```yaml
# Required
suffix: v2                     # secondary Deployment/VA/HPA name suffix

# WVA / scaling (all optional; defaults shown)
variantCost: "5.0"             # default "5.0"
minReplicas: 1                 # default 1
maxReplicas: 10                # default 10

# vLLM model-server overrides (all optional; omitted = inherit from primary)
parallelism:
  tensor: 2                    # rewrites --tensor-parallel-size

# Pod-level resource overrides
resources:
  nvidia.com/gpu: 2            # mirrors limits + requests
```

The bundled `v2-cost-only.yaml` shows the simplest case (cost only).
Future PRs can ship richer variants (e.g. TP=2 for "different hardware
shape" demonstrations on a single-hardware cluster).

---

## What the demo shows

Under sustained load, WVA's cost-aware optimizer should:

- Keep **primary at minReplicas** (cost 10) until the cheaper variant
  hits its own ceiling.
- Scale **secondary** up to absorb the load (cost 5).
- On scale-down, drop the **expensive variant first**.

Compared to a per-Deployment HPA reading model-level signals
independently — which would scale **both** variants symmetrically
because each HPA observes the same gateway-side metric in isolation —
WVA's holistic view is the differentiator.

---

## Chart-side follow-up

The two-step workflow above (separate `kubectl apply` for the V2
ConfigMap) exists because the WVA chart's
`templates/manager/wva-cp-configmap.yaml` only emits the V1 fields
(`kvCacheThreshold`, `queueLengthThreshold`, `kvSpareTrigger`,
`queueSpareTrigger`). It does not template `analyzerName`, so V2 cannot
be enabled via helm values today.

A planned WVA-chart PR will add `analyzerName` (and V2's
`scaleUpThreshold` / `scaleDownBoundary`) to the chart's values surface.
Once that lands and is released, this scenario yaml can hard-code
`analyzerName: saturation` in its `wva.capacityScaling.default` block,
and the standalone `two-variant-wva-v2-config.yaml` manifest + Step 2
above can be removed.

---

## Files in this PR

| Path | What it is |
|---|---|
| `config/scenarios/guides/two-variant-wva.yaml` | Scenario yaml for the primary variant standup |
| `config/specification/guides/two-variant-wva.yaml.j2` | Specification wrapper |
| `config/scenarios/guides/two-variant-wva-v2-config.yaml` | Standalone ConfigMap that enables V2 saturation (Step 2) |
| `config/scenarios/guides/variants/v2-cost-only.yaml` | Variant override config (cost-only secondary) |
| `tools/add_variant.py` | Helper that creates the secondary Deployment / VA / HPA |
| `docs/multi-variant-benchmark.md` | This file |
