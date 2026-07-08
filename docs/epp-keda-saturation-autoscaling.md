# EPP+KEDA Saturation Autoscaling Guide

Direct KEDA autoscaling using EPP pool metrics, without WVA controller. KEDA queries Prometheus for `inference_pool_average_kv_cache_utilization` and `inference_pool_average_queue_size` directly and auto-generates the `HorizontalPodAutoscaler`. No WVA controller, no `VariantAutoscaling` CR, no `prometheus-adapter`, no external-metrics API.

Reference: [upstream PR #1356](https://github.com/llm-d/llm-d-workload-variant-autoscaler/pull/1356) — `docs/developer-guide/saturation-keda.md` is the authoritative source.

## When to use this approach

| Situation | Recommendation |
|---|---|
| Single Deployment per model, no variant cost-optimization needed | **This guide** |
| Multiple GPU variants per model, cost-aware placement | Use the [WVA controller](/docs/workload-variant-autoscaler.md) |
| You need scale-to-zero | KEDA supports `minReplicaCount: 0` |

## Quick start

```bash
# 1. Clone
git clone https://github.com/llm-d/llm-d-benchmark.git
cd llm-d-benchmark

# 2. Install
./install.sh
source .venv/bin/activate

# 3. Confirm you're logged in to your OpenShift cluster
oc whoami

# 4. Standup the EPP+KEDA guide
llmdbenchmark --spec guides/epp-keda-saturation standup -p <namespace>
```

Verify the setup:
```bash
oc get scaledobject -n <namespace>
oc get hpa -n <namespace>
```

Expected: `READY=True` on the ScaledObject, and HPA `TARGETS` resolve to real numbers (not `<unknown>`).

To teardown:
```bash
llmdbenchmark --spec guides/epp-keda-saturation teardown -p <namespace>
```

## Architecture

EPP exposes two pool-level metrics on port 9090:

- **`inference_pool_average_kv_cache_utilization`** — fraction of KV cache blocks in use (0.0–1.0)
- **`inference_pool_average_queue_size`** — average requests waiting for scheduling

KEDA `ScaledObject` has two triggers:

1. **KV cache**: scales up when pool-avg KV cache utilization > threshold (default 0.7)
2. **Queue size**: scales up when pool-avg queue > threshold (default 2 requests)

KEDA takes the **maximum** across both triggers (OR logic). Generated HPA respects min/max replica bounds and behavior policies (scale-up/scale-down stabilization windows).

```
EPP /metrics:9090
    ↓ (Prometheus scrape via ServiceMonitor)
Prometheus (thanos-querier)
    ↓ (KEDA query via TriggerAuthentication bearer token)
KEDA ScaledObject (dual Prometheus triggers)
    ↓ (auto-creates HPA with metric queries)
HorizontalPodAutoscaler
    ↓ (watches Deployment replicas)
Decode Deployment
```

## Prerequisites

Before standup, verify:

### 1. EPP is deployed with metrics enabled

```bash
kubectl get svc <router-service> -n <namespace> -o wide
# Should show port 9090 (http-metrics)
```

### 2. Prometheus is scraping EPP

The harness auto-creates a `ServiceMonitor` that scrapes EPP's metrics endpoint with bearer-token auth. Verify the setup:

```bash
oc get servicemonitor -n <namespace>
```

### 3. KEDA is installed

```bash
helm list -n keda | grep keda
```

If missing:
```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
```

### 4. Prometheus has RBAC access to read EPP `/metrics`

The harness creates a `ClusterRole` (`epp-metrics-reader-<namespace>`) and binds it to Prometheus's ServiceAccount. Verify:

```bash
oc get clusterrole epp-metrics-reader-<namespace>
oc get clusterrolebinding epp-metrics-reader-<namespace>
```

## Configuration reference

Edit the scenario YAML (`config/scenarios/guides/epp-keda-saturation.yaml`) to tune behavior:

```yaml
eppKedaSaturation:
  enabled: true
  namespace: ""              # defaults to deploy namespace

  prometheus:
    baseUrl: https://thanos-querier.openshift-monitoring.svc.cluster.local
    port: 9091

  epp:
    poolName: ""             # defaults to model_id_label
    metricsPortName: http-metrics
    metricsPort: 9090
    prometheusServiceAccount: thanos-querier      # Prometheus SA name
    prometheusServiceAccountNamespace: openshift-monitoring

  scaledObject:
    minReplicas: 1
    maxReplicas: 10
    pollingInterval: 15      # how often KEDA queries Prometheus
    kvCacheThreshold: "0.7"  # 70% utilization
    queueSizeThreshold: "2"  # 2 requests average per replica
    behavior:
      scaleUp:
        stabilizationWindowSeconds: 0   # react immediately
        policies:
          - type: Pods
            value: 1
            periodSeconds: 180  # pod startup time
      scaleDown:
        stabilizationWindowSeconds: 300  # wait 5 min before scaling down
        policies:
          - type: Pods
            value: 1
            periodSeconds: 300
```

## Tuning thresholds

**KV cache threshold** (`kvCacheThreshold`): pool-avg KV cache utilization at which to scale up. Default 0.7 (70%).
- Lower → scale up earlier (more headroom, higher cost)
- Higher → tolerate more saturation (squeeze GPUs, higher latency)

**Queue threshold** (`queueSizeThreshold`): average requests per replica waiting to be scheduled. Default 2.
- Lower → scale up earlier
- Higher → tolerate longer queues before scaling

**Common profiles:**

| Profile | KV threshold | Queue threshold | Use case |
|---|---|---|---|
| Default | 0.7 | 2 | Balanced workloads |
| Aggressive | 0.85 | 10 | Throughput-optimized (higher GPU utilization, accept latency) |
| Conservative | 0.55 | 1 | Latency-sensitive SLOs |

Change thresholds live:
```bash
oc edit scaledobject epp-saturation-scaledobject -n <namespace>
```

KEDA reconciles immediately; no pod restarts needed.

## Troubleshooting

### ScaledObject READY = False

```bash
oc describe scaledobject epp-saturation-scaledobject -n <namespace>
```

Common causes:

1. **Prometheus unreachable**: Verify `prometheus.baseUrl:port` is correct and DNS resolves.
2. **Bearer token invalid**: Check that `prometheus-auth` Secret exists in the namespace.
3. **Query returns no data**: Verify EPP is emitting metrics:
   ```bash
   oc port-forward svc/<epp-service> 9090:9090 -n <namespace>
   curl -s http://localhost:9090/metrics | grep inference_pool_average
   ```

### HPA TARGETS = \<unknown\>

The generated HPA can't read metric values from KEDA. Causes:

1. ScaledObject is not `READY` (see above).
2. KEDA operator not running: `oc get pods -n keda`.

### Replicas not scaling

1. Verify HPA targets are resolved (not `<unknown>`).
2. Check current metric values match the ScaledObject query:
   ```bash
   oc logs -n keda deployment/keda-operator | grep -i "epp-saturation"
   ```
3. Verify load is actually pushing metrics past the thresholds.

## Behavior policies

**Scale-up stabilization window** (`scaleUp.stabilizationWindowSeconds`): how long a metric must exceed the threshold before scaling up. Default 0 (react immediately).

**Scale-down stabilization window** (`scaleDown.stabilizationWindowSeconds`): how long a metric must stay below the threshold before scaling down. Default 300 (5 minutes). Prevents flapping under bursty traffic.

**Policy period** (`periodSeconds`): must be at least as long as your pod startup time. For GPU hardware (H100/A100), 180 seconds is typical. For simulators, 30 seconds.

## Multi-model (prefill/decode disaggregation)

> **TODO**: Document ScaledObject setup for prefill/decode disaggregated deployments (one ScaledObject per Deployment).

## FAQ

**Q: Can I use this without KEDA pre-installed?**  
A: KEDA must be pre-installed by cluster admins. The harness does not install KEDA.

**Q: What if I want to scale multiple models?**  
A: Each model gets its own `ScaledObject`. The guide scenario handles one. For multiple, duplicate the scenario YAML per model or use WVA's cost-aware multi-variant mode.

**Q: How do I scale to zero?**  
A: Set `minReplicas: 0` in the scenario. However, HPAs with `minReplicas: 0` and `metricType: AverageValue` need special handling (divide-by-zero). Check [KEDA docs](https://keda.sh/) for best practices.

**Q: Can I use this on GKE / EKS / vanilla Kubernetes?**  
A: Not yet verified. The guide is currently tested on OpenShift. Standup skips the setup on non-OCP platforms. Contributions welcome.

## See also

- [WVA controller guide](/docs/workload-variant-autoscaler.md) — cost-aware multi-variant autoscaling
- [KEDA documentation](https://keda.sh/)
- [Upstream PR #1356](https://github.com/llm-d/llm-d-workload-variant-autoscaler/pull/1356) — saturation-keda.md
