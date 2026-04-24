# Workload Variant Autoscaler (WVA) Integration

`llm-d-benchmark` integrates with the **Workload Variant Autoscaler (WVA)** so
benchmarking scenarios can exercise model autoscaling end-to-end. This guide
covers how WVA is wired in, how to enable it on a scenario, what each knob in
the scenario YAML controls, what the smoketest validates, how to tear it down
safely on a shared cluster, and how to debug the most common failure modes.

For background on the autoscaler itself, see:

- [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) — the controller source
- [llm-d well-lit-path WVA guide](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.wva.md) — the upstream install reference our integration mirrors

> **Platform support:** WVA install is currently only verified on **OpenShift**.
> On other platforms, every WVA-related step (install, smoketest, teardown) is
> deliberately skipped — the scenario YAML can still render the WVA blocks, but
> nothing is applied to the cluster.

---

## Quick start

End-to-end on a fresh machine, against a logged-in OpenShift cluster:

```bash
# 1. Clone (replace the branch if you're targeting a specific one)
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark

# 2. Install (creates .venv, installs the llmdbenchmark CLI + planner)
./install.sh

# Or one-shot via curl, optionally pinning a branch:
#   LLMDBENCH_BRANCH=<BRANCH_HERE> \
#     curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
# (the curl form clones into ./llm-d-benchmark/ for you)

# 3. Activate the venv created by install.sh
source .venv/bin/activate

# 4. Confirm you're pointed at the right cluster
oc whoami

# 5. Standup the WVA-enabled scenario (substitute your namespace)
llmdbenchmark --spec guides/inference-scheduling-wva standup -p <namespace>
```

When standup completes, the smoketest will have already verified the full
*Namespaced* WVA pipeline (controller, prometheus-adapter, VA, HPA, end-to-end metric
flow). The HPA's `TARGETS` column should read a numeric value
(e.g. `q/1`) rather than `<unknown>`:

```bash
oc get hpa -n <namespace>
```

To redeploy after editing the scenario YAML, please teardown then standup:

```bash
llmdbenchmark --spec guides/inference-scheduling-wva teardown -p <namespace>
llmdbenchmark --spec guides/inference-scheduling-wva standup  -p <namespace>
```

The shared cluster-wide infrastructure (`prometheus-adapter`, ClusterRole,
prometheus-ca ConfigMap) survives teardown automatically — see
[Section 4](#4-cluster-wide-vs-per-tenant-resources--teardown-semantics)
for the full preservation policy.

---

## 1. Architecture at a glance

When a scenario has `wva.enabled: true` and the cluster is OpenShift, standup
provisions the following resources, in this order:

```
cluster-wide / shared
    prometheus-adapter      v5.2.0, in openshift-user-workload-monitoring
                            serves wva_desired_replicas via external-metrics API
    prometheus-ca           ConfigMap, same ns — CA cert for thanos-querier auth
    allow-thanos-querier-api-access
                            ClusterRole granting prometheus-adapter access
                            to OCP's monitoring stack

  <wva namespace>           = deploy namespace by default
      workload-variant-autoscaler   Helm chart v0.6.0, namespaced mode
                                    (reconciles only VAs in this namespace)

      per stack (per model)
          VariantAutoscaling/{model_id_label}-decode
              labels:
                  wva.llmd.ai/controller-instance = <wva.namespace>
              spec.scaleTargetRef -> Deployment/{model_id_label}-decode

          HorizontalPodAutoscaler/{model_id_label}-decode
              spec.scaleTargetRef -> Deployment/{model_id_label}-decode
              metric.selector.matchLabels:
                  variant_name        = {model_id_label}-decode
                  exported_namespace  = <wva.namespace>
                  controller_instance = <wva.namespace>
```

**The data flow** that turns this into actual pod scaling:

1. WVA controller reconciles each `VariantAutoscaling` it owns and
   queries thanos-querier for that variant's vLLM saturation metrics.
2. The controller emits `wva_desired_replicas` on its `:8443/metrics`
   endpoint (Prometheus scrapes via the chart's ServiceMonitor).
3. `prometheus-adapter` discovers `wva_desired_replicas` from
   user-workload-monitoring Prometheus and exposes it via the
   `external.metrics.k8s.io/v1beta1` API.
4. The `HorizontalPodAutoscaler` polls that external-metrics API,
   matches its `selector.matchLabels`, and scales the decode Deployment
   between `spec.minReplicas` and `spec.maxReplicas`.

Our integration ensures **every join along that chain is byte-aligned**
(`controllerInstance` value, VA label, HPA selector). Misalignment in any
one of them surfaces as `TARGETS: <unknown>` on the HPA — see the
[smoketest validations](#5-smoketest-checks) for what catches each case.

---

## 2. Three ways to enable WVA on a scenario

| Method | When to use |
|---|---|
| `-u / --wva` CLI flag on any existing scenario | Quick toggle without editing files; uses defaults from `config/templates/values/defaults.yaml` |
| `--spec guides/inference-scheduling-wva` | Dedicated scenario where every WVA knob is spelled out inline so you can tweak them per-experiment |
| `--spec guides/multi-model-wva` | Multi-model scenario: two or more pools under one gateway, each with its own VA + HPA, one shared WVA controller |

### 2a. Via the CLI flag

```bash
llmdbenchmark --spec guides/inference-scheduling standup -p <namespace> --wva
```

That sets `wva.enabled: true` at render time. All other WVA settings come from
defaults — fine for a quick test, but you can't tweak per-experiment HPA
behavior without editing the defaults file.

### 2b. Via the dedicated `inference-scheduling-wva` scenario

```bash
llmdbenchmark --spec guides/inference-scheduling-wva standup -p <namespace>
```

Same model and inference setup as `inference-scheduling`, plus a fully spelled-out
`wva:` block in the scenario YAML. The `-u/--wva` flag is **not required** here
because `wva.enabled: true` is already set in the file.

You'd choose this scenario when you want to:

- See/tweak every HPA knob in one place
- Override the controller image tag, prometheus-adapter version, etc.
- Author a DoE experiment that sweeps over `wva.hpa.maxReplicas` or
  `wva.variantAutoscaling.variantCost`

### 2c. Via the `multi-model-wva` scenario (multiple pools, one WVA controller)

```bash
llmdbenchmark --spec guides/multi-model-wva standup -p <namespace>
```

Deploys N models behind a single gateway, each with its own EPP +
InferencePool + VariantAutoscaling + HPA. One WVA controller in the
namespace watches every VA (deduplicated by `wva.namespace`), so the
Prometheus/adapter/controller wiring is identical to the single-model
case — only the number of autoscaling targets scales.

The scenario uses the top-level `shared:` block to hold scenario-wide
settings (controller image, chart versions, EPP plugin config, shared
HTTPRoute); per-stack blocks hold only model-specific knobs (model name,
decode resources, VA + HPA min/max). To add a third model, copy one of
the stack entries and change `name` + `model`. See
[`guides/multi-model-wva.yaml`](../config/scenarios/guides/multi-model-wva.yaml).

Topology:

```
         Gateway (shared infra-llmdbench-inference-gateway)
           │
   ┌───────┴─────────── HTTPRoute multi-model-route ─────────────┐
   │ /qwen3-06b/*                                 /llama-31-8b/* │
   ▼                                                             ▼
EPP+InferencePool (qwen3-06b)           EPP+InferencePool (llama-31-8b)
   │                                                             │
vLLM decode + VA + HPA                   vLLM decode + VA + HPA
             ▲                               ▲
             └─────── WVA controller (1) ────┘
```

What the scenario layout buys you:

- **Shared control plane** — one `infra-llmdbench` gateway release, one
  istio control plane, one WVA controller, one prometheus-adapter, one
  shared model PVC (sized for the sum of all models; each stack's weights
  live in its own `model.path` subdirectory). Rendered once in the
  scenario's "shared-infra-owner" stack (first non-standalone stack) and
  skipped on siblings to avoid parallel-helmfile races.
- **Per-stack scaling intent** — each stack's VA caps what the controller
  is willing to compute (`variantAutoscaling.{min,max}Replicas`,
  `variantCost`) and each stack's HPA caps what actually gets applied to
  the Deployment. They're independent per pool, so pool A scaling up to
  its max doesn't push pool B past its own cap.
- **One routing URL per pool** — the shared HTTPRoute uses
  `httpRoute.pathPrefix: /{stack.name}` so every pool is reachable at
  `http://<gateway>/{stack-name}/v1/...`. Gateway rewrites the prefix
  away before the request reaches upstream vLLM, so pods continue to see
  plain `/v1/*` paths.
- **`flowControl` feature gate on every pool** — enabled in the
  `shared.inferenceExtension.pluginsCustomConfig` block and inherited by
  every stack. This is non-optional for WVA: the controller reads EPP
  queue depth to compute scale signals, and flow-control is what
  exposes queue depth in the metrics.

---

## 3. The WVA knobs in the scenario YAML

All settings live under the `wva:` block in
[`config/scenarios/guides/inference-scheduling-wva.yaml`](../config/scenarios/guides/inference-scheduling-wva.yaml).
Each is documented inline in the file too. Below is what each does and the
typical reasons you'd touch it.

### 3.1 Top-level WVA controller settings

```yaml
wva:
  enabled: true                  # master switch (same as -u/--wva on CLI)
  wellLitPath: inference-scheduling   # surfaced as `llm-d.ai/guide` label on the VA
  namespace: ""                  # empty = use the deploy namespace from -p
  replicaCount: 1                # WVA controller pod replicas

  controller:
    enabled: true                # disable to render VA+HPA without installing the controller

  namespaceScoped: true          # controller watches only its own ns; one per ns

  image:
    repository: ghcr.io/llm-d/llm-d-workload-variant-autoscaler
    tag: v0.6.0                  # NOTE: image tags use a leading "v"

  metrics:
    enabled: true
    port: 8443                   # /metrics port the controller exposes
    secure: true                 # HTTPS + bearer-token auth

  prometheus:
    baseUrl: https://thanos-querier.openshift-monitoring.svc.cluster.local
    port: 9091
```

**Image tag note:** the *helm chart* version is bare semver (`chartVersions.wva: 0.6.0`),
the *container image* tag uses a leading `v` (`v0.6.0`). They're set independently.

### 3.2 VariantAutoscaling spec — per-model scaling intent

```yaml
wva:
  variantAutoscaling:
    enabled: true
    minReplicas: 1               # controller floor
    maxReplicas: 10              # controller ceiling
    variantCost: "10.0"          # relative GPU cost weight (H100=10, A100=8, L40S=5)
    slo:
      tpot: 10                   # Time-Per-Output-Token target (ms)
      ttft: 1000                 # Time-To-First-Token target (ms)
```

`variantCost` is what the WVA saturation solver uses to decide which model to
scale when several share GPU capacity. Lower `slo.tpot`/`slo.ttft` = more
aggressive scale-up under load.

### 3.3 HorizontalPodAutoscaler spec — what actually changes the replica count

```yaml
wva:
  hpa:
    enabled: true
    minReplicas: 1               # never scale below this; must be ≥ 1
    maxReplicas: 10              # safety ceiling regardless of controller computation
    targetAvgValue: 1            # 1 = "match controller's desiredReplicas exactly"

    behavior:
      scaleUp:
        stabilizationWindowSeconds: 120
        policies:
          - type: Percent
            value: 100           # 100% per period = double replicas
            periodSeconds: 15
      scaleDown:
        stabilizationWindowSeconds: 120
        policies:
          - type: Percent
            value: 100           # 100% per period = halve replicas
            periodSeconds: 15
```

Keep `wva.hpa.{min,max}Replicas` aligned with `wva.variantAutoscaling.{min,max}Replicas`
— the VA caps what the controller is willing to compute, the HPA caps what
actually gets applied to the Deployment.

For more `behavior` tuning options:
[Kubernetes HPA: configurable scaling behavior](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior).

### 3.4 Chart version pins

```yaml
chartVersions:
  wva: 0.6.0                     # WVA controller chart (oci://ghcr.io/llm-d/workload-variant-autoscaler)
  prometheusAdapter: 5.2.0       # bumped charts have broken external-metric rule format
```

---

## 4. Cluster-wide vs per-tenant resources & teardown semantics

WVA installs a mix of cluster-wide and per-tenant resources. To keep
multi-tenant clusters healthy, our standup and teardown follow this policy:

| Resource | Scope | Standup | Plain teardown | `teardown -d/--deep` |
|---|---|---|---|---|
| `prometheus-adapter` | `openshift-user-workload-monitoring` (shared) | install if absent; reuse if any tenant already installed it | preserved | **preserved** |
| `prometheus-ca` ConfigMap | shared monitoring ns | created | preserved | **preserved** |
| `allow-thanos-querier-api-access` ClusterRole | cluster-scoped | applied | preserved | **preserved** |
| WVA controller helm release | per-namespace | installed | preserved | uninstalled |
| `VariantAutoscaling` for this stack | namespace-local | applied | **removed** | removed |
| `HorizontalPodAutoscaler` for this stack | namespace-local | applied | **removed** | removed |

**The principle:** `--deep` only removes resources that live in the target namespace.
Cluster-shared infrastructure (`prometheus-adapter` + its supporting CRBs/CMs)
is **never** removed by us — it's used by every WVA tenant in the cluster, so
its lifecycle belongs to the platform admin, not to a per-tenant teardown.

If you need to fully remove the shared adapter, do it explicitly:

```bash
helm uninstall -n openshift-user-workload-monitoring prometheus-adapter
oc delete clusterrole allow-thanos-querier-api-access
oc delete configmap -n openshift-user-workload-monitoring prometheus-ca
```

---

## 5. Smoketest checks

When `wva.enabled: true`, the smoketest runs eight extra checks beyond the
standard pod/inference validation. Each one tells you exactly what it's
verifying so a failure points at the broken link rather than a vague
"WVA is sad" symptom.

| Check | What it verifies | Failure means |
|---|---|---|
| `wva_platform_gate` | Cluster is OpenShift (or stack is correctly skipped on other platforms) | informational only |
| `wva_controller_deployment` | Polls `Deployment/workload-variant-autoscaler-controller-manager` until `Available` with all replicas Ready (≤180s). **Fails fast** if the manager container's `restartCount` grows mid-wait | controller pod is crash-looping; check `oc logs -n <ns> deploy/workload-variant-autoscaler-controller-manager --previous` |
| `wva_prometheus_adapter` | `Deployment/prometheus-adapter` in the user-workload monitoring ns is `Available` | adapter wasn't installed, or another tenant's broken install is squatting the cluster role |
| `wva_variantautoscaling` | The per-stack `VariantAutoscaling/{model_id_label}-decode` exists | step_09 didn't apply the rendered VA |
| `wva_va_controller_instance_label` | The VA carries `wva.llmd.ai/controller-instance=<value>` matching the controller's `CONTROLLER_INSTANCE` | the controller's predicate filters out the VA → "No active VariantAutoscalings found" loop, no metric ever emitted. **The most subtle WVA gate.** |
| `wva_hpa_target` | The HPA's `scaleTargetRef.name` equals `{model_id_label}-decode` | template drift between VA and HPA |
| `wva_hpa_selector_alignment` | The HPA's `metric.selector.matchLabels` has all three of `variant_name`, `exported_namespace`, `controller_instance` matching what the controller emits | HPA selector misalignment — controller's metric is in Prometheus but the HPA's selector matches zero rows |
| `wva_hpa_able_to_scale` (best-effort) | HPA's `AbleToScale` condition is `True` | HPA hasn't initialized yet, or scale subresource lookup failed |
| `wva_hpa_targets_resolved` | Polls the HPA's `.status.currentMetrics[*].external.current` until the value resolves from `<unknown>` to a number (≤180s). Includes the most recent `ScalingActive=False` reason in the failure message if it times out | full pipeline isn't producing a metric value — could be controller not reconciling, Prometheus not scraping, adapter rule missing, or selector mismatch |

All polling timeouts are constants at the top of
[`llmdbenchmark/smoketests/validators/wva.py`](../llmdbenchmark/smoketests/validators/wva.py)
(`_WVA_CONTROLLER_TIMEOUT_SECS`, `_HPA_TARGETS_TIMEOUT_SECS`). Bump them if
your cluster is unusually slow.

### Replica-count check (in `BaseSmoketest.validate_role_pods`)

The standard `decode_replicas` check is HPA-aware: when WVA is enabled and
the role is HPA-managed (currently only `decode`), the check passes if the
actual pod count is within `[wva.hpa.minReplicas, wva.hpa.maxReplicas]`,
not just strictly equal to `decode.replicas`. Without this relaxation, an
idle stack at `minReplicas: 1` would falsely fail when `decode.replicas: 2`.

The role allow-list lives at module top in
[`llmdbenchmark/smoketests/base.py`](../llmdbenchmark/smoketests/base.py)
as `_WVA_HPA_MANAGED_ROLES = frozenset({"decode"})` — extend it if upstream
WVA grows native prefill autoscaling.

---

## 6. Quick verification commands

After a successful standup with WVA, these one-liners confirm the full chain
is up. Run them in order; each one verifies a downstream link in the pipeline.

```bash
# 1. Controller pod is Available, no recent restarts
oc get pod -n <ns> -l control-plane=controller-manager -o wide

# 2. The VA is reconciled (METRICSREADY=True, OPTIMIZED=<a number>)
oc get va -n <ns>

# 3. The VA has the controller-instance label
oc get va -n <ns> <model-id>-decode -o jsonpath='{.metadata.labels}'

# 4. The controller actually emits the metric
oc -n openshift-user-workload-monitoring exec -c prometheus prometheus-user-workload-0 -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=wva_desired_replicas{controller_instance="<ns>"}' \
  | jq '.data.result'

# 5. prometheus-adapter exposes it via the external-metrics API
oc get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/<ns>/wva_desired_replicas" | jq

# 6. The HPA's TARGETS shows a numeric value (no longer <unknown>)
oc get hpa -n <ns>
```

If 1–3 are green but 4 is empty → controller isn't reconciling (most likely
`wva.llmd.ai/controller-instance` label mismatch — check #3 against the
controller's `CONTROLLER_INSTANCE` env var).

If 4 has a value but 5 is empty → prometheus-adapter doesn't have the rule
(install was wrong namespace, or rule values file wasn't applied).

If 5 has a value but 6 is `<unknown>` → HPA selector doesn't match the
metric's labels.

---

## 7. Common failure modes & fixes

### "No active VariantAutoscalings found" loop in controller logs

Controller is running but says it sees no VAs to reconcile, even though one
exists in the watched namespace.

**Cause:** the VA is missing the `wva.llmd.ai/controller-instance` label
(or it doesn't match the controller's `CONTROLLER_INSTANCE` env). The
controller's predicate silently filters it out.

**Fix:**

```bash
oc label va -n <ns> <model-id>-decode \
  wva.llmd.ai/controller-instance=<controller-instance> --overwrite
```

…or re-run standup so the rendered template (which already includes the
label) is applied.

### HPA shows `TARGETS: <unknown>` indefinitely

Something in the metric pipeline isn't lining up. Walk the chain in
[Section 6](#6-quick-verification-commands) to find which link is broken.

### `release: already exists` when installing prometheus-adapter

Another tenant installed `prometheus-adapter` in their *own* namespace
(not `openshift-user-workload-monitoring`). The chart's cluster-scoped
`prometheus-adapter-resource-reader` ClusterRole is helm-owned by their
release, blocking ours.

**Fix:** ask that tenant to `helm uninstall -n <their-ns> prometheus-adapter`.
Once the ClusterRole is freed, re-run our standup and we'll install it
into the correct namespace per the upstream WVA guide.

### Controller pod CrashLoopBackOff with `context deadline exceeded` in logs

The controller can't reach the Kubernetes API server reliably (leader-election
lease renewal times out). This is a **cluster network / CNI** issue, not a
WVA bug. Verify:

```bash
oc get clusteroperator network -o yaml | yq '.status.conditions'
oc get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status'
```

If the network operator is `Degraded=True` or any node is `Ready=False`,
escalate to the cluster admin.

### Image pull fails with `manifest unknown`

The image tag doesn't exist. Check what's actually published:

```bash
TOKEN=$(curl -sL "https://ghcr.io/token?scope=repository:llm-d/llm-d-workload-variant-autoscaler:pull" \
  | jq -r .token)
curl -sH "Authorization: Bearer $TOKEN" \
  https://ghcr.io/v2/llm-d/llm-d-workload-variant-autoscaler/tags/list | jq
```

Note that **chart versions are bare semver** (`0.6.0`) but **image tags use
a leading `v`** (`v0.6.0`). Mixing them up is a common source of pull failures.

---

## 8. Multi-tenant cluster considerations

On a shared cluster, multiple groups may run their own WVA controllers and
HPAs. Our integration is designed to coexist:

- **Namespaced mode** (`wva.namespaceScoped: true`) keeps each controller
  scoped to its own namespace, so different tenants' controllers don't race
  on each other's `VariantAutoscaling` resources.
- **`controllerInstance` label** + matching VA label + HPA selector ensures
  a tenant's HPA only consumes metrics from their own controller, even if
  another tenant's controller is incidentally watching the same namespace.
- **Shared `prometheus-adapter`** is installed once and reused. Our standup
  detects an existing install (via the `prometheus-adapter-resource-reader`
  ClusterRole's helm-owner annotation) and skips re-installing.

Tenants who run a *cluster-scoped* WVA controller (i.e., `namespaceScoped: false`)
will reconcile other tenants' VAs too. The `controllerInstance` label gate
on the HPA selector prevents their emitted metrics from ever satisfying our
HPA, but it's still considered cluster-hygiene rude to run cluster-scoped.

---

## 9. Where each piece lives in the repo

| Artifact | File |
|---|---|
| Chart values rendered into the helm install | [`config/templates/jinja/19_wva-values.yaml.j2`](../config/templates/jinja/19_wva-values.yaml.j2) |
| WVA namespace label patch | [`config/templates/jinja/23_wva-namespace.yaml.j2`](../config/templates/jinja/23_wva-namespace.yaml.j2) |
| Per-stack `VariantAutoscaling` | [`config/templates/jinja/27_wva-variantautoscaling.yaml.j2`](../config/templates/jinja/27_wva-variantautoscaling.yaml.j2) |
| Per-stack `HorizontalPodAutoscaler` | [`config/templates/jinja/28_wva-hpa.yaml.j2`](../config/templates/jinja/28_wva-hpa.yaml.j2) |
| `prometheus-adapter` values | [`config/templates/jinja/21_prometheus-adapter-values.yaml.j2`](../config/templates/jinja/21_prometheus-adapter-values.yaml.j2) |
| `allow-thanos-querier-api-access` ClusterRole | [`config/templates/jinja/22_prometheus-rbac.yaml.j2`](../config/templates/jinja/22_prometheus-rbac.yaml.j2) |
| Cluster-wide WVA defaults (chart version, image, monitoring URL) | [`config/templates/values/defaults.yaml`](../config/templates/values/defaults.yaml) (`wva:` and `chartVersions.wva` blocks) |
| Standup admin install (controller + adapter) | [`llmdbenchmark/standup/steps/step_03_workload_monitoring.py`](../llmdbenchmark/standup/steps/step_03_workload_monitoring.py) |
| Standup per-stack VA/HPA apply | [`llmdbenchmark/standup/steps/step_09_deploy_modelservice.py`](../llmdbenchmark/standup/steps/step_09_deploy_modelservice.py) |
| Shared install/teardown helpers | [`llmdbenchmark/standup/wva.py`](../llmdbenchmark/standup/wva.py) |
| Teardown logic | [`llmdbenchmark/teardown/steps/step_01_uninstall_helm.py`](../llmdbenchmark/teardown/steps/step_01_uninstall_helm.py) |
| Smoketest WVA mixin | [`llmdbenchmark/smoketests/validators/wva.py`](../llmdbenchmark/smoketests/validators/wva.py) |
| WVA-enabled scenario (the one to copy/edit for new experiments) | [`config/scenarios/guides/inference-scheduling-wva.yaml`](../config/scenarios/guides/inference-scheduling-wva.yaml) |
| Multi-model WVA scenario (N pools, 1 gateway, 1 controller) | [`config/scenarios/guides/multi-model-wva.yaml`](../config/scenarios/guides/multi-model-wva.yaml) |

---

## 10. Multi-model operations cookbook

Recipes for the day-to-day lifecycle + benchmarking against the
`multi-model-wva` scenario. All commands assume you've installed
`llmdbenchmark` and pointed `KUBECONFIG` at a cluster where you have
(or will have) namespace admin in `<namespace>`. Stack names
(`qwen3-06b`, `llama-31-8b`) mirror the shipped scenario; substitute
your own if you've customized.

### 10.1 First-time standup

```bash
llmdbenchmark --spec guides/multi-model-wva standup -p <namespace>
```

Renders both stacks, installs shared infra (istio, Gateway,
`infra-llmdbench`, WVA controller, prometheus-adapter, model PVC) once,
then deploys each pool's `-ms` + `-gaie` + VA + HPA. Downloads run in
parallel — wall time ≈ slowest model, not the sum. Standup
auto-chains into the smoketest phase unless you pass
`--skip-smoketest`.

### 10.2 Discover what's deployed (`--list-endpoints`)

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> --list-endpoints
```

Prints a table of per-stack endpoint URLs + a copy-paste block of
ready-to-run `llmdbenchmark run` invocations. Runs the full render
pipeline (so the detected endpoints match exactly what standup would
have produced) and exits before launching any harness pods.

### 10.3 Benchmark a single pool

**Preferred — let `--stack` auto-resolve the endpoint:**

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> \
  --stack qwen3-06b \
  -l inference-perf -w sanity_random.yaml -j 1
```

With `--stack qwen3-06b`, step 03 auto-detects the gateway endpoint,
bakes in the `/qwen3-06b` path prefix, and the harness pod hits
`http://<gateway>/qwen3-06b/v1/completions`. The gateway rewrites
`/qwen3-06b/*` → `/*` so vLLM sees plain `/v1/completions`.

**Alternative — pin `--endpoint-url` yourself** (useful for run-only
mode without the scenario file locally):

```bash
llmdbenchmark run \
  --endpoint-url http://<gateway>/qwen3-06b \
  --model Qwen/Qwen3-0.6B \
  --namespace <namespace> \
  -l guidellm -w sanity_random.yaml -j 2
```

### 10.4 Two parallel guidellm jobs against one pool

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> \
  --stack qwen3-06b \
  -l guidellm -w sanity_random.yaml \
  -j 2
```

`-j 2` launches two guidellm pods hitting the same endpoint
simultaneously. Both run the same workload, but each writes to its own
`{experiment_id}_1` / `{experiment_id}_2` results subdirectory on the
workload PVC, so metrics don't collide. The harness `wait` step polls
both pods; result collection pulls both directories back.

### 10.5 Compare two pools side-by-side (two shells)

```bash
# Shell 1 — --workspace is a global option, placed before the subcommand
llmdbenchmark --spec guides/multi-model-wva --workspace /tmp/run-qwen run -p <namespace> \
  --stack qwen3-06b \
  -l guidellm -w sanity_random.yaml -j 2

# Shell 2 (in parallel)
llmdbenchmark --spec guides/multi-model-wva --workspace /tmp/run-llama run -p <namespace> \
  --stack llama-31-8b \
  -l guidellm -w sanity_random.yaml -j 2
```

Distinct `--workspace` dirs keep the two invocations' render plans,
logs, and collected results fully isolated. The WVA controller will
see load on both pools' VAs simultaneously and scale them
independently. `--workspace` (and `--spec`, `--base-dir`, `--dry-run`,
`--verbose`, `--non-admin`) are **global options** — they must appear
before the subcommand name (`run`, `standup`, etc.), not after.

### 10.6 Rerun one pool against a different model

`--stack NAME` scopes `-m/--models` to that one stack; siblings keep
their scenario-defined models untouched:

```bash
llmdbenchmark --spec guides/multi-model-wva run -p <namespace> \
  --stack qwen3-06b \
  --model meta-llama/Llama-3.2-3B \
  -l inference-perf -w sanity_random.yaml
```

Without `--stack`, `-m` applies to every stack and emits a warning —
it would collapse the multi-model scenario into N copies of one model,
which is rarely desired.

### 10.7 Re-deploy one pool after a scenario edit

Edit the scenario YAML's stack for `llama-31-8b` (e.g. bump
`decode.replicas`, swap the model, tweak `wva.hpa.maxReplicas`), then:

```bash
llmdbenchmark --spec guides/multi-model-wva standup -p <namespace> \
  --stack llama-31-8b
```

Global steps (admin prereqs, shared-infra helmfile, WVA controller,
model PVC) still run (they're scenario-wide and idempotent). Per-stack
steps only fire for `llama-31-8b` — qwen3-06b's running pods and VA
are left completely alone.

### 10.8 Tear down one pool, keep siblings running

```bash
llmdbenchmark --spec guides/multi-model-wva teardown -p <namespace> \
  --stack llama-31-8b
```

Uninstalls the `llama-31-8b-ms` and `llama-31-8b-gaie` Helm releases
(plus their VA + HPA), leaves `qwen3-06b` and the shared
`infra-llmdbench` + WVA controller + prometheus-adapter in place.
Useful for cost management — shrink to one pool over a weekend
without disturbing the other.

### 10.9 Observe scaling events

With both pools running, watch the VA + HPA state in real time:

```bash
# Per-pool VariantAutoscaling
kubectl get variantautoscaling -n <namespace> -w

# Per-pool HPA with current/target metric
kubectl get hpa -n <namespace> -w

# Controller logs (look for "Reconciling" per VA)
kubectl logs -n <namespace> -l control-plane=controller-manager -f

# Raw wva_desired_replicas metric for a pool
kubectl exec -n <namespace> \
  $(kubectl get pod -n <namespace> -l app.kubernetes.io/name=workload-variant-autoscaler -o name | head -1) \
  -- curl -sk https://localhost:8443/metrics \
  | grep wva_desired_replicas
```

Every query that takes a label selector can be filtered to one pool:
`-l wva.llmd.ai/controller-instance=<namespace>,llm-d.ai/model=qwen-qwen3-0-6b`
for VAs / HPAs; pods don't carry the routing-prefix label but do carry
`llm-d.ai/model` keyed on each stack's `model.shortName`.

### 10.10 Full teardown

```bash
llmdbenchmark --spec guides/multi-model-wva teardown -p <namespace>
```

Removes every Helm release in both stacks plus shared infra and the
WVA controller. Prometheus-adapter and istio control-plane persist by
design (shared across tenants); add `--deep` to remove all cluster
resources in the deploy + harness namespaces.
