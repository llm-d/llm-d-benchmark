---
name: llm-d-benchmark-troubleshooting
description: |
  Troubleshooting and debugging guide for llm-d-benchmark failures. TRIGGER when: user reports
  errors from llmdbenchmark CLI, pod failures, PVC issues, helm errors, timeouts, smoketest
  failures, CI pipeline failures, or asks how to debug a deployment. Also trigger when the user
  pastes error logs or stack traces from any llm-d-benchmark phase.
  DO NOT TRIGGER when: the issue is unrelated to llm-d-benchmark or Kubernetes.
---

# llm-d-benchmark Troubleshooting

Guide for diagnosing and resolving failures across all llm-d-benchmark phases (standup, smoketest, run, teardown). Covers pod failures, storage issues, networking, authentication, and CI debugging.

---

## First Steps on Any Failure

### 1. Check command logs

Every command executed is logged to the workspace:

```
{workspace}/setup/commands/{timestamp}_command.log   # What ran
{workspace}/setup/commands/{timestamp}_stdout.log    # Output
{workspace}/setup/commands/{timestamp}_stderr.log    # Errors
```

### 2. Re-run with verbose mode

```bash
llmdbenchmark --spec <spec> --verbose <command> ...
```

### 3. Use debug mode for run failures

```bash
llmdbenchmark --spec <spec> run -p <ns> -l inference-perf -w sanity_random.yaml --debug
# Pod runs "sleep infinity" instead of harness — shell in to inspect:
kubectl exec -it -n <ns> <pod-name> -- bash
```

### 4. Quick cluster diagnostics

```bash
kubectl get pods -n <ns> -o wide
kubectl get events -n <ns> --sort-by='.lastTimestamp' | tail -30
kubectl get pvc -n <ns> -o wide
```

---

## Terminal Pod States

The framework detects these states and aborts immediately:

| State | Meaning | Common Fix |
|-------|---------|------------|
| `CrashLoopBackOff` | Container keeps restarting | Check logs: `kubectl logs <pod> -n <ns> --previous` |
| `Error` | Container exited non-zero | Check logs for application error |
| `OOMKilled` | Out of memory | Increase `resources.limits.memory` in scenario |
| `CreateContainerConfigError` | Bad pod spec | Check volume mounts, secrets, configmaps exist |
| `ImagePullBackOff` | Can't pull image | Verify image exists, check pull secrets |
| `ErrImagePull` | Image pull failed | Network issue or wrong image reference |
| `InvalidImageName` | Malformed image ref | Check `images.*` in scenario YAML |

---

## Phase-Specific Troubleshooting

### Standup Phase

#### Step 00: Ensure Infrastructure

**Error:** `Missing required tools: helm, helmfile`
- **Fix:** Run `./install.sh -y` to install all tools

**Error:** `Python >= 3.11 required`
- **Fix:** Install Python 3.11+ and recreate venv

#### Step 02: Admin Prerequisites

**Error:** `Failed to install Gateway API CRDs`
- **Check:** Cluster connectivity and admin permissions
- **Debug:** `kubectl auth can-i create customresourcedefinitions`

**Error:** `Failed to install Istio via helmfile`
- **Check:** helmfile + helm-diff installed: `helm plugin list | grep diff`
- **Check:** Gateway provider namespace exists or `createNamespace: true` is set
- **Note:** Helmfile uses `use_kubeconfig=False` for gateway provider to avoid namespace resolution issues with `needs:`

**Error:** `Failed to install LWS`
- **When:** Only needed when `multinode.enabled: true`
- **Check:** LeaderWorkerSet CRD compatibility

#### Step 03: Workload Monitoring

**Error:** `Accelerator resource 'nvidia.com/gpu' declared in plan but no capacity found on any cluster node`
- **Fix:** Verify GPU nodes are available: `kubectl get nodes -o json | jq '.items[].status.capacity'`
- **For Kind/sim:** Override `accelerator.count: 0` and use `kubernetes.io/os: linux` node selector

**Error:** `Node selector label 'key=value' not found on any cluster node`
- **Fix:** Label nodes: `kubectl label node <name> <key>=<value>`
- **For Kind:** Override `standalone.acceleratorType` with `kubernetes.io/os: linux`

#### Step 04: Model Namespace

**Error:** `StorageClass not found`
- **Check:** `kubectl get storageclass`
- **For Kind:** Use `storageClassName: standard` and `accessModes: [ReadWriteOnce]`
- **For OpenShift:** Typically `gp3-csi` or `ocs-storagecluster-cephfs`

**Error:** `Model download job did not complete`
- **Check download pod logs:** `kubectl logs job/download-model -n <ns> --tail=50`
- **Check previous attempts:** `kubectl logs -n <ns> <pod> --previous`
- **Common causes:**
  - HuggingFace token invalid/missing for gated models
  - Insufficient PVC storage
  - Network timeout to HuggingFace Hub
  - Disk full on node

**Error:** `Failed to create model PVC`
- **Check:** `kubectl describe pvc <name> -n <ns>` for provisioner errors

#### Step 05: Harness Namespace

**Error:** `Data access pod not ready: Timed out after 120s`
- **Pod `Unschedulable`:** Resources too high for available nodes. Add `harness.resources` to scenario (e.g., `cpu: "1"`, `memory: "2Gi"`)
- **PVC `Pending`:** Wrong storage class or access mode. Check `storage.workloadPvc.storageClassName` and `accessModes`
- **ImagePullBackOff:** Verify `images.benchmark.repository` and tag resolve

#### Step 06: Standalone Deploy

**Error:** `Standalone deployment pods not ready`
- **Unschedulable:** GPU node not available or affinity mismatch
- **CrashLoopBackOff:** Check vLLM startup: `kubectl logs <pod> -n <ns>`
- **For sim scenarios:** Ensure `accelerator.count: 0` and `vllm.modelCommand: imageDefault`

#### Steps 07-09: Modelservice Deploy

**Error:** `Failed to apply infra helmfile`
- **Check:** `helm list -n <ns>` for stuck releases
- **Fix stuck release:** `helm uninstall <release> -n <ns>` and retry

**Error:** `Decode pods not ready` / `Prefill pods not ready`
- **Pending:** GPU resources insufficient. Check `decode.resources` and `accelerator.count`
- **CrashLoopBackOff:** Model download failed or vLLM misconfigured
- **For sim:** The inference-sim image auto-detects and skips GPU allocation. No port conflict — chart shifts vLLM to port 8200 when routing proxy is enabled

**Error:** `Inference pool not ready`
- **Check EPP logs:** `kubectl logs -n <ns> -l app.kubernetes.io/name=<model>-gaie-epp`
- **EPP stays NOT_SERVING until decode pods are Ready**

**Error:** `Failed to install WVA`
- **Note:** WVA only supports OpenShift. On vanilla K8s, this is informational only

---

### Smoketest Phase

#### Step 00: Health Check

**Error:** `No pods found with selector`
- **Fix:** Verify standup completed. Check `kubectl get pods -n <ns>`

**Error:** `Not all pods running`
- **Fix:** Wait for pods to stabilize, or check pod descriptions

**Error:** `/health endpoint not responding`
- **Fix:** Model may still be loading. Increase `control.waitTimeout` in scenario

**Error:** `/v1/models returns empty or wrong model`
- **Fix:** Check model name matches `model.name` in scenario

#### Step 01: Inference Test

**Error:** `/v1/completions failed`
- **Debug:** Run manually: `kubectl run curl --rm -it --image=curlimages/curl -- curl -s http://<svc-ip>:<port>/v1/completions -d '{"model":"<name>","prompt":"hello","max_tokens":5}'`
- **Fallback:** Tool tries `/v1/chat/completions` if `/v1/completions` fails

---

### Run Phase

#### Step 03: Detect Endpoint

**Error:** `No service/gateway IP found`
- **Fix:** Verify deployment method (`-t standalone` or `-t modelservice`)
- **Run-only mode:** Use `--endpoint-url http://host:port` to skip discovery

#### Step 07: Deploy Harness

**Error:** `Failed to deploy pod`
- **Check:** Harness image exists: verify `images.benchmark.repository:tag`
- **Check:** ServiceAccount exists in harness namespace
- **Check:** Profile ConfigMap was created

#### Step 08: Wait Completion

**Error:** `Pod failed` or `Timed out`
- **Check harness logs:** `kubectl logs <pod> -n <ns> --tail=100`
- **Increase timeout:** `--wait-timeout 7200`
- **Use debug mode:** `--debug` to shell into pod

#### Step 09: Collect Results

**Error:** `No pod with label 'role=llm-d-benchmark-data-access' found`
- **Fix:** Data access pod crashed or was deleted. Re-run standup step 05

**Error:** `No results found on PVC`
- **Fix:** Harness may have failed silently. Check harness pod logs

---

### Teardown Phase

**Error:** `Helm release not found`
- **Normal:** Release was already cleaned up or never installed

**Stuck teardown:** Use `--deep` flag to force delete all resources in namespaces

---

## HuggingFace Token Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Model is gated but no token provided` | Missing HF_TOKEN | Export `HF_TOKEN=hf_...` or `LLMDBENCH_HF_TOKEN=hf_...` |
| `Unauthorized access to gated model` | Token lacks access | Request access at `https://huggingface.co/<model>` |
| `hf auth login` fails in download job | Token injected but invalid | Check `huggingface.token` in scenario or env var |
| Download succeeds locally but fails in pod | Secret not mounted | Verify `huggingface.enabled: true` and secret exists |
| Public model works, gated doesn't | Auto-detection worked | Set `HF_TOKEN` env var before standup |

**Auto-detection:** When no token found, `huggingface.enabled` is set to `false`. This skips secret creation, auth login, and secretKeyRef mounts. Public models work; gated models fail at download.

---

## PVC / Storage Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| PVC `Pending` indefinitely | Wrong storage class | Check `kubectl get sc` and set `storageClassName` |
| PVC `Pending` on Kind | `ReadWriteMany` not supported | Use `accessModes: [ReadWriteOnce]` and `storageClassName: standard` |
| Pod `Unschedulable` mounting PVC | PVC in wrong zone/node | Use `ReadWriteOnce` or zone-aware provisioner |
| `no persistent volumes available` | No provisioner | Install a CSI driver or use hostPath |
| Download job runs out of space | PVC too small | Increase `storage.modelPvc.size` |

---

## Helm / Helmfile Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `unknown command "diff"` | helm-diff plugin missing | `helm plugin install https://github.com/databus23/helm-diff` |
| `platformHooks` error | Helm < 3.18.10 with new helm-diff | Upgrade Helm: `curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \| bash` |
| helmfile `needs` resolution wrong | kubeconfig injects wrong namespace | Gateway provider helmfile uses `use_kubeconfig=False` |
| Release stuck in `pending-install` | Previous install interrupted | `helm uninstall <release> -n <ns>` and retry |
| `no matches for kind "X"` | CRDs not installed | Re-run step 02 (admin prerequisites) |

---

## Network / Endpoint Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` to service IP | Pod not ready or wrong port | Check pod readiness and service port mapping |
| Gateway returns 404 | HTTPRoute not configured | Verify HTTPRoute exists: `kubectl get httproute -n <ns>` |
| EPP returns `NOT_SERVING` | No decode pods registered | Wait for decode pods to be Ready |
| OpenShift route not working | Route not created | Check `oc get routes -n <ns>` |
| Port 8000 conflict | Proxy + vLLM both on 8000 | Chart auto-handles: proxy on 8000, vLLM on 8200 |

---

## Kind / CI-Specific Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Pods `Unschedulable` | Resource requests too high | Reduce resources in scenario (e.g., `cpu: 500m`, `memory: 512Mi`) |
| `ReadWriteMany` not supported | Kind uses `rancher.io/local-path` | Use `ReadWriteOnce` |
| `standard-rwx` not found | Kind has `standard` only | Set `storageClassName: standard` |
| GPU labels missing | Kind has no GPUs | Override `acceleratorType` to `kubernetes.io/os: linux` |
| Prometheus CRDs missing | No Prometheus Operator | Set `monitoring.installPrometheusCrds: true` |
| Gateway pod `Unschedulable` | Default resources too large | Override `gateway.resources` (e.g., `cpu: 500m`, `memory: 512Mi`) |

---

## OpenShift-Specific Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| SCC denied | Pod security context too permissive | Use `--non-admin` or configure SCCs |
| Route not created | Missing `route.openshift.io` permission | Verify RBAC for ServiceAccount |
| `proxy_uid` mismatch | OpenShift assigns UID ranges per namespace | Tool auto-detects from namespace annotation |

---

## Useful Debug Commands

```bash
# Cluster overview
kubectl get nodes -o wide
kubectl top nodes

# Namespace overview
kubectl get all -n <ns>
kubectl get events -n <ns> --sort-by='.lastTimestamp' | tail -30

# Pod debugging
kubectl describe pod <pod> -n <ns>
kubectl logs <pod> -n <ns> --tail=100 --all-containers
kubectl logs <pod> -n <ns> --previous     # Previous crash logs
kubectl exec -it <pod> -n <ns> -- bash    # Shell into pod

# PVC debugging
kubectl get pvc -n <ns> -o wide
kubectl describe pvc <pvc-name> -n <ns>

# Helm debugging
helm list -n <ns>
helm status <release> -n <ns>
helm get values <release> -n <ns>

# CRD verification
kubectl get crd | grep -E "gateway|inference|podmonitor"

# Storage classes
kubectl get storageclass

# Service endpoint discovery
kubectl get svc -n <ns> -o wide
kubectl get endpoints -n <ns>

# Gateway/Route debugging
kubectl get gateway -n <ns>
kubectl get httproute -n <ns>
oc get routes -n <ns>                     # OpenShift only

# EPP/GAIE debugging
kubectl get inferencepool -n <ns>
kubectl get inferencemodel -n <ns>
kubectl logs -n <ns> -l app.kubernetes.io/name=<model>-gaie-epp

# Node resource inspection
kubectl get nodes -o json | jq '.items[].status.capacity'
kubectl get nodes -o json | jq '.items[].metadata.labels'
```

---

## Log Locations

| Location | Content |
|----------|---------|
| `{workspace}/setup/commands/` | All executed commands with stdout/stderr |
| `{workspace}/setup/logs/` | Phase-level logs |
| `{workspace}/plan/{stack}/` | Rendered YAML plans (23 files per stack) |
| `{workspace}/run/results/` | Benchmark results from harness pods |
| `{workspace}/run/analysis/` | Local analysis output |
| `kubectl logs` | Live pod logs on cluster |

---

## Common Workflow for Debugging

1. **Identify the failing step** from the CLI output (e.g., `[05] FAILED: harness_namespace`)
2. **Check the rendered YAML** in `{workspace}/plan/{stack}/` for the relevant template
3. **Check command logs** in `{workspace}/setup/commands/` for the failing kubectl/helm command
4. **Check cluster state** with `kubectl get pods/pvc/events` in the namespace
5. **Re-run specific step** with `--step` flag (e.g., `standup -s 5`) after fixing
6. **Use verbose mode** (`--verbose`) to see all command details
7. **Use debug mode** (`--debug`) for run phase to inspect harness pod
