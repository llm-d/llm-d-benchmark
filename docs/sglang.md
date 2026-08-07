# Benchmarking SGLang

`llm-d-benchmark` can stand up and benchmark [SGLang](https://github.com/sgl-project/sglang)
as the inference engine instead of the default vLLM. SGLang support reuses the
**SGLang model-server overlays that ship in the upstream llm-d guides**, so it is
available exclusively through the [kustomize deploy method](kustomize.md)
(`-t kustomize`). The workload/harness and analysis stages are engine-agnostic
and work unchanged.

> [!IMPORTANT]
> SGLang is **only** supported with the `kustomize` standup method. The
> `modelservice` and `standalone` methods render vLLM `vllm serve` commands from
> the scenario templates and have no SGLang equivalent. If you need SGLang, you
> must deploy an upstream guide via `-t kustomize` (see below).

## How it works

Under `-t kustomize`, standup applies an upstream llm-d guide directly
(`guides/<guideName>` in the [llm-d repo](https://github.com/llm-d/llm-d)). Each
guide ships a per-engine model-server overlay tree:

```
guides/<guideName>/modelserver/
  gpu/
    vllm/      # default
    sglang/    # SGLang overlay  <-- selected by acceleratorBackend
    trtllm/
  amd/
    sglang/
  ...
```

The `kustomize.acceleratorBackend` key selects which overlay is applied. The
variable resolver rewrites the guide README's kustomize path by string
substitution — `modelserver/gpu/vllm` → `modelserver/<acceleratorBackend>`
(see [`llmdbenchmark/kustomize/variable_resolver.py`](../llmdbenchmark/kustomize/variable_resolver.py),
`_apply_accelerator_backend`). Setting `acceleratorBackend: "gpu/sglang"`
therefore deploys the SGLang overlay in place of vLLM, with everything else in
the guide (router, GAIE, gateway) unchanged.

The SGLang overlay launches `python3 -m sglang.launch_server` from the
`lmsysorg/sglang` image and labels the pods `llm-d.ai/engine-type: sglang`, which
tells the llm-d router to read SGLang's metric names (`sglang:num_queue_reqs`,
`page_size`/`num_pages`, …) instead of vLLM's.

## Which guides support SGLang

These upstream guides ship a `gpu/sglang` model-server overlay and can be
benchmarked with SGLang today:

| Guide (`guideName`) | Model (from overlay) | Notes |
|---|---|---|
| `optimized-baseline` | Qwen3-32B | Single-engine baseline. Simplest starting point. Overlays: `base`, `gke`, `amd`. |
| `precise-prefix-cache-routing` | Qwen3-32B | Prefix-cache-aware routing. **Flat** overlay (`gpu/sglang/`, no provider split). |
| `tiered-prefix-cache` | Qwen3-32B | Tiered KV cache (HiCache). Overlays under `gpu/sglang/native/cpu/{base,gke}`. |

> [!NOTE]
> Always confirm the overlay exists in the llm-d ref you are deploying:
> `ls guides/<guideName>/modelserver/gpu/sglang` in your llm-d clone. Overlay
> availability tracks upstream and may change per `repoRef`.

## Prerequisites

Same as any kustomize deploy (see [Quickstart](quickstart.md) and
[Kustomize deploy method](kustomize.md)):

- A Kubernetes cluster with GPUs and cluster/namespace admin as required.
- `llm-d-benchmark` installed (`install.sh`), virtualenv activated.
- `HF_TOKEN` exported (for gated models) — standup auto-creates the
  `llm-d-hf-token` Secret from it. See the HF_TOKEN section in
  [kustomize.md](kustomize.md#hf_token-handling).

## Quick start

Any guide scenario can be run with SGLang without a second scenario file, by
overriding `kustomize.acceleratorBackend` from the CLI. `-t kustomize`
already flips `kustomize.enabled`, so the backend is the only value left to
set:

```bash
export NS=llmdbench
export HF_TOKEN=hf_...     # if the model is gated

# 1. Stand up the SGLang stack (kustomize deploy method).
llmdbenchmark --spec guides/optimized-baseline standup -t kustomize -p "$NS" --set kustomize.acceleratorBackend=gpu/sglang

# 2. Smoketest: send real requests through the gateway and validate responses.
llmdbenchmark --spec guides/optimized-baseline smoketest -t kustomize -p "$NS" --set kustomize.acceleratorBackend=gpu/sglang

# 3. Run a workload and collect + analyze results.
llmdbenchmark --spec guides/optimized-baseline run -t kustomize -p "$NS" --set kustomize.acceleratorBackend=gpu/sglang \
    -l inference-perf -w shared_prefix_synthetic.yaml

# 4. Tear down.
llmdbenchmark --spec guides/optimized-baseline teardown -t kustomize -p "$NS" --set kustomize.acceleratorBackend=gpu/sglang
```

Pass the override to **every** phase: each one re-renders the plan, and a
phase that misses it renders a vLLM plan for an SGLang deployment. See
[standup.md](standup.md#overriding-scenario-values-from-the-cli---set).

To benchmark a different guide, swap the `--spec` (e.g.
`guides/tiered-prefix-cache`). For AMD accelerators on `optimized-baseline`,
use `--set kustomize.acceleratorBackend=amd/sglang`.

## Comparing SGLang vs vLLM

Because only `acceleratorBackend` changes, a fair engine A/B is straightforward:
run the same guide + same workload profile twice, once with
`acceleratorBackend: "gpu/vllm"` and once with `"gpu/sglang"`, into separate
`workDir`s (or namespaces), then compare with the
[analysis pipeline](analysis.md) / [benchmark report](benchmark_report.md). The
benchmark report's `stack` schema already models the engine as an
interchangeable component (vLLM, SGLang, …), so both runs slot into the same
comparison.

## Tuning the SGLang deployment

Under kustomize, the scenario's `model.*`, `decode.*`, parallelism, and resource
keys are **ignored** — the guide's manifests define the deployment. Change the
SGLang pods only through the `kustomize.*` keys (full reference in
[kustomize.md](kustomize.md)):

- `patches` / `overlayPath` → the SGLang model-server pods (e.g. bump replicas,
  add SGLang server flags, pin a `priorityClassName`).
- `extraHelmValues` / `extraHelmSets` → the router/GAIE helm release.
- `guideVariableOverrides` → fill/override `${VAR}` tokens in the guide README.

Example — override the decode replica count for the SGLang deployment:

```yaml
kustomize:
  enabled: true
  guideName: "optimized-baseline"
  acceleratorBackend: "gpu/sglang"
  patches:
    - patch: |
        apiVersion: apps/v1
        kind: Deployment
        metadata: { name: decode }
        spec: { replicas: 4 }
```

> [!TIP]
> SGLang's memory knob is `--mem-fraction-static` (the upstream overlay documents
> it as the equivalent of vLLM's `--gpu-memory-utilization`). Add or change
> SGLang server flags with a strategic-merge `patch` on the model-server
> container rather than the scenario's `vllm`/`vllmCommon` keys, which do not
> apply under kustomize.

## Running on specific clusters (GKE / OpenShift / CoreWeave / Kind)

The deploy method is the same everywhere (`-t kustomize`); what changes is the
`INFRA_PROVIDER` overlay the guide applies. `INFRA_PROVIDER` is a **separate knob
from the backend** — set it via `kustomize.guideVariableOverrides.INFRA_PROVIDER`
(this is exactly what the nightly CI does). The composed path is
`modelserver/gpu/sglang/${INFRA_PROVIDER}`, so a value only works if that overlay
exists. Availability is **not uniform** across guides:

| Guide | `base` | `gke` | `aws` | `coreweave` | Notes |
|---|:---:|:---:|:---:|:---:|---|
| `optimized-baseline` | ✅ | ✅ | — | — | |
| `precise-prefix-cache-routing` | — | — | — | — | **flat overlay** (`gpu/sglang/`, no provider split) → use `INFRA_PROVIDER: ""` (already set in the preset) |
| `tiered-prefix-cache` | ✅ | ✅ | — | — | HiCache path `gpu/sglang/native/cpu/{base,gke}` |

The presets ship **portable** (`INFRA_PROVIDER: base`, except `precise` which is
`""`). Override per cluster:

### GKE

```yaml
# in the scenario's kustomize block
guideVariableOverrides:
  INFRA_PROVIDER: gke
```

The `gke` overlay differs from `base` by **two env vars** —
`NCCL_TUNER_PLUGIN=none`, `NCCL_NET_PLUGIN=""` — which disable GKE's gIB NCCL
tuner. You **only need it when** running **tensor parallelism (TP ≥ 2)** on GKE
node pools that have the gIB RDMA libraries installed; otherwise `base` runs on
GKE unchanged. If you skip it on a TP≥2 + gIB node, the server **crashes at
startup** with `RuntimeError: NCCL error: internal error`. At **TP = 1** (single
GPU per pod) the tweak is a no-op — `base` is fine. `precise` has no `gke`
variant; it uses the flat overlay on GKE too.

### OpenShift (OCP)

No OCP-specific overlay exists — use `base` (the default). The SGLang overlays
run privileged (`runAsUser: 0`, `IPC_LOCK`/`SYS_RAWIO` capabilities), so the
target namespace needs a permissive **SCC** (e.g. `privileged`/`anyuid`), which
requires cluster-admin at standup. The benchmark's standup provisions the SCC it
needs; OCP is covered by the repo's own `cicd/ocp` nightly runs.

### CoreWeave

None of the shipped presets ship a CoreWeave-specific overlay — use `base`.

### Kind (local)

**Not feasible for SGLang.** Kind clusters have no NVIDIA GPUs; SGLang requires
real GPUs. Kind is only for the `sim`/CPU accelerator paths, which SGLang does
not have. Use a GPU cloud (GKE/CoreWeave/AWS) or on-prem GPU cluster instead.

> [!IMPORTANT]
> `precise-prefix-cache-routing-sglang` and `tiered-prefix-cache-sglang` deploy
> from non-standard upstream overlay layouts (flat, and `native/cpu`
> respectively). The local `plan` check validates the benchmark templates but
> **not** the guide's kustomize path, so **smoke-test these two with a real
> `standup`** before trusting them. Check the resolved path in the standup log:
> `[modelserver] kubectl apply -n <ns> -k .../modelserver/gpu/sglang/...`.
> `optimized-baseline` uses the standard layout and is the safest starting point.

## Verifying a run

### Functional — did it actually deploy and serve? (needs cluster)

1. **Right overlay resolved** — check the standup log:
   `[modelserver] kubectl apply -n <ns> -k .../modelserver/gpu/sglang/...`
2. **It's really SGLang, configured as intended:**
   ```bash
   kubectl get deploy -n <ns> -o jsonpath='{..image}'   # lmsysorg/sglang:...
   kubectl get deploy -n <ns> -o jsonpath='{..args}'    # --model-path, --tensor-parallel-size
   ```
3. **It serves** — `llmdbenchmark --spec <spec> smoketest -t kustomize -p <ns>`
   (health + a real inference request).
   > [!NOTE]
   > Under kustomize the per-scenario config validator is bypassed
   > (`get_validator` → `"ignore"` → `BaseSmoketest`), so smoketest checks
   > health + inference only — **not** that pod resources/flags match the
   > scenario. Do step 2's `kubectl` inspection by hand to confirm that.
4. **Output is coherent** — one manual `/v1/completions` call, or an
   `examples/eval-containers-*` run, catches misconfig that still "answers"
   (wrong dtype, bad TP).

### Benchmark validity

- **Reproducible:** re-run the same scenario+profile; variance should be small
  (the workspace captures the exact rendered config).
- **Report percentiles**, not just means (use the analysis pipeline /
  benchmark report), and sweep load rather than trusting a single point.
- **Fair vLLM-vs-SGLang comparison:** hold model/hardware/workload/routing/harness
  constant, and **diff the two overlays' launch flags** (prefix caching,
  max-model-len, TP, chunked prefill). Under kustomize those flags come from the
  upstream overlays, so differences there are confounds you must equalize —
  no tool does this for you.

## Known caveats

- **Workload profiles carry `server: { type: vllm }`.** In the
  `inference-perf` profiles this selects the *server-side* metric client. The
  core client-side results — TTFT, TPOT, ITL, throughput, failure rate — come
  from hitting the OpenAI-compatible endpoint and are engine-agnostic, so they
  are valid for SGLang. Engine-*internal* metric scraping keyed to
  `type: vllm`, however, expects vLLM's `vllm:` metric names and will not map to
  SGLang's `sglang:` names. Treat client-side latency/throughput as the source
  of truth for SGLang runs.
- **PD disaggregation differs at the transport layer.** vLLM uses NIXL
  (decode pulls the KV cache); SGLang routes peer discovery through a bootstrap
  server and has prefill push the KV cache. This is handled entirely by the
  guide's SGLang overlay + the llm-d router — no benchmark-side configuration —
  but expect the two engines' PD numbers to reflect different transfer
  mechanisms.
- **Overlay coverage tracks upstream.** Not every guide has an SGLang overlay
  (e.g. `wide-ep-lws`, `workload-autoscaling`). Only the guides listed above are
  supported.

## Continuous integration

The nightly benchmark workflow already accepts SGLang as a backend:
[`.github/workflows/reusable-ci-nightly-benchmark.yaml`](../.github/workflows/reusable-ci-nightly-benchmark.yaml)
exposes a `backend_type` input (`vllm`, `sglang`; valid only for the `kustomize`
standup method) and injects it into the scenario via
`yq -i '.scenario[0].kustomize.acceleratorBackend = "<gpu-or-amd>/<backend>"'`
before standup. Use `backend_type: sglang` when dispatching the workflow to run
an SGLang benchmark in CI.

## See also

- [Kustomize deploy method](kustomize.md) — full `kustomize.*` reference.
- [Quickstart](quickstart.md) — end-to-end walkthrough on Kind.
- [Run](run.md) — harnesses, workload profiles, and metrics.
- [Analysis Pipeline](analysis.md) / [Benchmark Report](benchmark_report.md) — comparing results.
- Upstream engine support in llm-d: `docs/architecture/core/model-servers.md` in the [llm-d repo](https://github.com/llm-d/llm-d).
