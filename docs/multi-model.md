# Multi-model operations cookbook

Recipes for the day-to-day lifecycle + benchmarking against the shipped
multi-model scenario,
[`examples/multi-model-optimized-baseline`](../config/scenarios/examples/multi-model-optimized-baseline.yaml)
- the [optimized-baseline](../config/scenarios/guides/optimized-baseline.yaml)
guide deployed twice, two models behind one gateway.

All commands assume you've installed `llmdbenchmark` and pointed
`KUBECONFIG` at a cluster where you have (or will have) namespace admin in
`<namespace>`. Stack names (`qwen3-06b`, `llama-31-8b`) mirror the shipped
scenario; substitute your own if you've customized.

Related reading:

- [`config/README.md`](../config/README.md#method-1-scenario-file-recommended-for-deployment-specific-config)
  - the `shared:` block merge semantics.
- [developer-guide](developer-guide.md#multi-stack-scenarios-and-the-shared-block)
  - the render-engine details (auto-named download Jobs, shared HTTPRoute,
  stack-index guards).
- [standup.md](standup.md#multi-stack-scenarios) and
  [run.md](run.md#multi-stack-runs) - what each lifecycle phase does per
  stack.

## Topology

```
         Gateway (shared infra-llmdbench-inference-gateway)
           |
   +-------+----------- HTTPRoute multi-model-route -------------+
   | /qwen3-06b/*                                 /llama-31-8b/* |
   v                                                             v
EPP+InferencePool (qwen3-06b)           EPP+InferencePool (llama-31-8b)
   |                                                             |
vLLM decode                                            vLLM decode
```

What the scenario layout buys you:

- **Shared control plane** - one `infra-llmdbench` gateway release, one
  istio control plane, one shared model PVC (sized for the sum of all
  models; each stack's weights live in its own `model.path` subdirectory).
  Rendered once in the scenario's "shared-infra-owner" stack (first
  non-standalone stack) and skipped on siblings to avoid parallel-helmfile
  races.
- **One routing URL per pool** - the shared HTTPRoute uses
  `httpRoute.pathPrefix: /{stack.name}` so every pool is reachable at
  `http://<gateway>/{stack-name}/v1/...`. The gateway rewrites the prefix
  away before the request reaches upstream vLLM, so pods continue to see
  plain `/v1/*` paths.
- **Per-pool EPP scheduling** - each stack gets its own EPP +
  InferencePool. The InferencePool selector is derived per stack
  (`llm-d.ai/model: {model_id_label}`), so pools never pick up each other's
  decode pods.
- **The optimized-baseline tuning, applied uniformly** - EPP scheduling
  profile, Envoy sidecar args/resources, InferencePool connection-pool
  limits and HTTPRoute timeouts all live in the scenario's `shared:` block
  and are inherited by every stack.

## 1. First-time standup

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline standup -p <namespace>
```

Renders both stacks, installs shared infra (istio, Gateway,
`infra-llmdbench`, model PVC) once, then deploys each pool's `-ms` +
`-router` releases. Downloads run in parallel - wall time ~ slowest model,
not the sum. Standup auto-chains into the smoketest phase unless you pass
`--skip-smoketest`.

## 2. Discover what's deployed (`--list-endpoints`)

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline run -p <namespace> --list-endpoints
```

Prints a table of per-stack endpoint URLs + a copy-paste block of
ready-to-run `llmdbenchmark run` invocations. Runs the full render
pipeline (so the detected endpoints match exactly what standup would
have produced) and exits before launching any harness pods.

## 3. Benchmark a single pool

**Preferred - let `--stack` auto-resolve the endpoint:**

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline run -p <namespace> \
  --stack qwen3-06b \
  -l inference-perf -w sanity_random.yaml -j 1
```

With `--stack qwen3-06b`, step 03 auto-detects the gateway endpoint,
bakes in the `/qwen3-06b` path prefix, and the harness pod hits
`http://<gateway>/qwen3-06b/v1/completions`. The gateway rewrites
`/qwen3-06b/*` -> `/*` so vLLM sees plain `/v1/completions`.

**Alternative - pin `--endpoint-url` yourself** (useful for run-only
mode without the scenario file locally):

```bash
llmdbenchmark run \
  --endpoint-url http://<gateway>/qwen3-06b \
  --model Qwen/Qwen3-0.6B \
  --namespace <namespace> \
  -l guidellm -w sanity_random.yaml -j 2
```

## 4. Two parallel guidellm jobs against one pool

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline run -p <namespace> \
  --stack qwen3-06b \
  -l guidellm -w sanity_random.yaml \
  -j 2
```

`-j 2` launches two guidellm pods hitting the same endpoint
simultaneously. Both run the same workload, but each writes to its own
`{experiment_id}_1` / `{experiment_id}_2` results subdirectory on the
workload PVC, so metrics don't collide. The harness `wait` step polls
both pods; result collection pulls both directories back.

## 5. Compare two pools side-by-side (two shells)

```bash
# Shell 1 - --workspace is a global option, placed before the subcommand
llmdbenchmark --spec examples/multi-model-optimized-baseline --workspace /tmp/run-qwen run -p <namespace> \
  --stack qwen3-06b \
  -l guidellm -w sanity_random.yaml -j 2

# Shell 2 (in parallel)
llmdbenchmark --spec examples/multi-model-optimized-baseline --workspace /tmp/run-llama run -p <namespace> \
  --stack llama-31-8b \
  -l guidellm -w sanity_random.yaml -j 2
```

Distinct `--workspace` dirs keep the two invocations' render plans,
logs, and collected results fully isolated. `--workspace` (and `--spec`,
`--base-dir`, `--dry-run`, `--verbose`, `--non-admin`) are **global
options** - they must appear before the subcommand name (`run`, `standup`,
etc.), not after.

## 6. Rerun one pool against a different model

`--stack NAME` scopes `-m/--models` to that one stack; siblings keep
their scenario-defined models untouched:

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline run -p <namespace> \
  --stack qwen3-06b \
  --model meta-llama/Llama-3.2-3B \
  -l inference-perf -w sanity_random.yaml
```

Without `--stack`, `-m` applies to every stack and emits a warning -
it would collapse the multi-model scenario into N copies of one model,
which is rarely desired.

## 7. Re-deploy one pool after a scenario edit

Edit the scenario YAML's stack for `llama-31-8b` (e.g. bump
`decode.replicas`, swap the model, resize `decode.resources`), then:

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline standup -p <namespace> \
  --stack llama-31-8b
```

Global steps (admin prereqs, shared-infra helmfile, model PVC) still run
(they're scenario-wide and idempotent). Per-stack steps only fire for
`llama-31-8b` - qwen3-06b's running pods are left completely alone.

## 8. Tear down one pool, keep siblings running

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline teardown -p <namespace> \
  --stack llama-31-8b
```

Uninstalls the `llama-31-8b-ms` and `llama-31-8b-router` Helm releases,
leaves `qwen3-06b` and the shared `infra-llmdbench` in place. Useful for
cost management - shrink to one pool over a weekend without disturbing
the other.

## 9. Scope a config override to one pool (`--set`)

```bash
# every stack
llmdbenchmark --spec examples/multi-model-optimized-baseline standup --set decode.replicas=2

# a common floor with one exception (exact name beats the global)
llmdbenchmark --spec examples/multi-model-optimized-baseline standup \
  --set 'decode.resources.limits.memory=64Gi' \
  --set 'llama-31-8b:decode.resources.limits.memory=32Gi'
```

A selector matching no stack is a hard error, not a silent no-op. Full
reference: [standup.md](standup.md#scoping-overrides-in-multi-stack-scenarios).

## 10. Full teardown

```bash
llmdbenchmark --spec examples/multi-model-optimized-baseline teardown -p <namespace>
```

Removes every Helm release in both stacks plus shared infra. The istio
control-plane persists by design (shared across tenants); add `--deep` to
remove all cluster resources in the deploy + harness namespaces.

## Adding a third model

Copy one of the stack blocks in
[`config/scenarios/examples/multi-model-optimized-baseline.yaml`](../config/scenarios/examples/multi-model-optimized-baseline.yaml),
give it a unique short descriptive `name` and a unique `model.*`, and size
`decode.resources` for it. The parser auto-derives unique `downloadJob.name`
and `router.monitoring.secretName` values from the model ID label, so
nothing else needs per-stack customization. Remember to grow
`shared.storage.modelPvc.size` to cover the extra weights.

## Autoscaling a multi-model deployment

The shipped scenario is not autoscaled. To add the Workload Variant
Autoscaler on top, add a `wva:` block to the scenario's `shared:` block
(controller settings, one controller per `wva.namespace`) plus a per-stack
`wva.hpa` / `wva.variantAutoscaling` block for each pool's scaling intent.
See [workload-variant-autoscaler.md](workload-variant-autoscaler.md) for
every knob and the verification commands.
