# Generic KEDA ScaledObject Support

**Date:** 2026-08-07
**Status:** Approved

## Problem

The existing `eppKedaSaturation` path creates one fixed ScaledObject per stack
with two hardwired Prometheus triggers (KV cache + queue size). It only runs on
OpenShift and uses OpenShift-specific Prometheus auth (SA token minting + CA cert
extraction from `thanos-querier-tls`). This makes it unusable on vanilla
Kubernetes clusters with a standard Prometheus installation.

## Requirements

1. Support Prometheus auth beyond OpenShift: `none` (plain HTTP/HTTPS, no token)
   and `bearer-secret` (user pre-creates a Secret with the token).
2. Support multiple ScaledObjects per stack, each with arbitrary triggers.
3. The ScaledObject template must be generic — no EPP-specific trigger logic baked in.
4. The existing `eppKedaSaturation` path is untouched (additive change only).
5. Must work on any Kubernetes cluster, not just OpenShift.

## Schema

Two new sub-keys under a top-level `keda` block in the scenario YAML:

```yaml
keda:
  prometheus:
    baseUrl: http://prometheus-operated.monitoring.svc.cluster.local
    port: 9090
    authMode: none          # "none" | "bearer-secret"
    secretName: ""          # only for authMode: bearer-secret — pre-existing Secret in the deploy namespace
    unsafeSsl: false

  scaledObjects:
    - name: decode-saturation
      targetRef:
        kind: Deployment
        name: ""            # defaults to model_id_label + "-decode"
      minReplicas: 1
      maxReplicas: 10
      pollingInterval: 15
      triggers:
        - type: prometheus
          name: kv-cache
          metricType: AverageValue
          query: |
            max(inference_pool_average_kv_cache_utilization{...})
          threshold: "0.7"
          activationThreshold: "0"
      behavior: {}          # optional HPA behavior block (scaleUp/scaleDown)
```

The `triggers` list is raw KEDA prometheus trigger metadata — no abstraction over
trigger type. `targetRef.name` defaults to `model_id_label + "-decode"` to cover
the common case without requiring knowledge of the generated name.

## Templates

### `31_keda-scaledobjects.yaml.j2`

Rendered when `keda.scaledObjects` is defined and non-empty. Loops over the list
and emits one YAML document per entry separated by `---`.

- `targetRef.name` defaults to `model_id_label ~ '-decode'` when empty.
- `serverAddress` for each prometheus trigger is assembled from
  `keda.prometheus.baseUrl:port`.
- `authMode: none` — no `authenticationRef`, no `authModes` field on the trigger.
- `authMode: bearer-secret` — adds `authModes: bearer` and
  `authenticationRef: name: keda-prometheus-auth` to each prometheus trigger.
- `behavior` block is rendered under `spec.advanced.horizontalPodAutoscalerConfig.behavior`
  when present.

### `32_keda-triggerauthentication.yaml.j2`

Rendered only when `keda.prometheus.authMode == "bearer-secret"`. References the
user-supplied Secret by name — no SA token minting, no CA cert extraction:

```yaml
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-prometheus-auth     # distinct from "prometheus-auth" used by eppKedaSaturation
  namespace: {{ namespace.name }}
spec:
  secretTargetRef:
  - parameter: bearerToken
    name: {{ keda.prometheus.secretName }}
    key: bearerToken
  - parameter: ca                # included unless unsafeSsl is true
    name: {{ keda.prometheus.secretName }}
    key: ca.crt
```

The name `keda-prometheus-auth` is intentionally distinct from `prometheus-auth`
(used by `eppKedaSaturation`) so both paths can coexist in the same namespace.

## Python standup logic

### New module: `llmdbenchmark/standup/keda.py`

Parallel to `keda_saturation.py` and `wva.py`. Responsibilities:

- `stacks_enabling_keda(rendered_stacks)` — return stacks where
  `keda.scaledObjects` is defined and non-empty.
- `install_keda_for_namespace(cmd, context, stack_path, namespace, errors)` —
  apply TriggerAuthentication (only when `authMode == "bearer-secret"`), then
  apply the ScaledObjects template. No OpenShift-specific logic.

### `step_03` changes

A new `_install_keda_if_enabled` method is added and called unconditionally from
`execute()` — outside the `if context.is_openshift` block that guards
`_apply_monitoring` — so it runs on any platform:

1. Calls `keda.stacks_enabling_keda()`.
2. Not gated on `context.is_openshift`.
3. Applies TriggerAuthentication per unique namespace (bearer-secret only).
4. Does not call `verify_keda_installed` or extract any CA cert.

### `step_09` changes

`_apply_keda_stack_resources` is called after `_apply_epp_keda_stack_resources`.
Applies `31_keda-scaledobjects` for the current stack. Not gated on `is_openshift`.

## What is NOT changed

- `keda_saturation.py`, `keda_prometheus_auth.py`, `wva.py` — untouched.
- `eppKedaSaturation` scenario key and its templates — untouched.
- `21_keda-triggerauthentication.yaml.j2`, `28_wva-scaledobject.yaml.j2`,
  `30_epp-keda-saturation-scaledobject.yaml.j2` — untouched.
- The `is_openshift` gating on the existing `_install_epp_keda_saturation_if_enabled`
  and `_install_wva_if_enabled` paths — untouched.

## Auth mode summary

| `authMode`      | TriggerAuthentication created? | Secret required?       | OpenShift only? |
|-----------------|-------------------------------|------------------------|-----------------|
| `none`          | No                            | No                     | No              |
| `bearer-secret` | Yes (`keda-prometheus-auth`)  | Yes (user pre-creates) | No              |

## File list

New files:
- `llmdbenchmark/standup/keda.py`
- `config/templates/jinja/31_keda-scaledobjects.yaml.j2`
- `config/templates/jinja/32_keda-triggerauthentication.yaml.j2`

Modified files:
- `llmdbenchmark/standup/steps/step_03_workload_monitoring.py`
- `llmdbenchmark/standup/steps/step_09_deploy_modelservice.py`
