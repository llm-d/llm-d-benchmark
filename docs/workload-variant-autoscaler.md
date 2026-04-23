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
    prometheus-adapter      v5.3.0, in openshift-user-workload-monitoring
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

## 2. Two ways to enable WVA on a scenario

| Method | When to use |
|---|---|
| `-u / --wva` CLI flag on any existing scenario | Quick toggle without editing files; uses defaults from `config/templates/values/defaults.yaml` |
| `--spec guides/inference-scheduling-wva` | Dedicated scenario where every WVA knob is spelled out inline so you can tweak them per-experiment |

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
  prometheusAdapter: 5.3.0       # bumped charts have broken external-metric rule format
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
