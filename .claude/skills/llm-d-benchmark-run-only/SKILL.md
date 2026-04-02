---
name: llm-d-benchmark-run-only
description: |
  Reference for the run_only.sh standalone bash utility that launches benchmarks against
  existing LLM inference stacks on Kubernetes. Independent of the Python llmdbenchmark CLI.
  TRIGGER when: user asks about run_only.sh, existing_stack/, running benchmarks against
  a pre-deployed endpoint using the bash script, run_only config files, or the --repeat flag.
  DO NOT TRIGGER for the Python CLI's run command (use llm-d-benchmark-run skill instead).
---

# run_only.sh — Standalone Harness Launcher

A bash utility that executes benchmarks against an **already-deployed** LLM inference stack.
No standup or teardown — just launch a harness pod, run workloads, and collect results.
Independent of the Python `llmdbenchmark` CLI.

**Location:** `existing_stack/run_only.sh`

---

## Quick Start

```bash
# Basic run against existing endpoint
./existing_stack/run_only.sh -c my-config.yaml

# With cloud upload
./existing_stack/run_only.sh -c my-config.yaml -o gs://my-bucket/results/

# Repeat 5 times and aggregate
./existing_stack/run_only.sh -c my-config.yaml -R 5

# With pre/post hooks
./existing_stack/run_only.sh -c my-config.yaml \
  --pre-workload "echo 'clearing cache'" \
  --post-workload "echo 'collecting metrics'"

# Debug mode (harness sleeps infinity)
./existing_stack/run_only.sh -c my-config.yaml -d

# Dry run (print commands without executing)
./existing_stack/run_only.sh -c my-config.yaml -n

# Verbose output
./existing_stack/run_only.sh -c my-config.yaml -v
```

---

## CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-c/--config <path>` | — | Config YAML file (**required**) |
| `-o/--output <dest>` | PVC | Results destination: `local`, `gs://bucket`, `s3://bucket` |
| `-R/--repeat <N>` | 1 | Repeat each workload N times and aggregate results |
| `--pre-workload <cmd>` | from config | Bash command run on pod before each workload |
| `--post-workload <cmd>` | from config | Bash command run on pod after each workload |
| `-v/--verbose` | off | Print executed commands |
| `-d/--debug` | off | Debug mode (sets `LLMDBENCH_HARNESS_DEBUG=1`) |
| `-n/--dry-run` | off | Print what would execute without running |
| `-h/--help` | — | Show usage |

**Env var overrides:** `LLMDBENCH_HARNESS_REPEAT` overrides `-R` flag.

---

## Config File Structure

```yaml
# Endpoint configuration
endpoint:
  base_url: "http://my-model-service.my-ns.svc.cluster.local:8000"
  model: "meta-llama/Llama-3.1-8B"
  namespace: "my-ns"                    # Namespace where model is deployed

# Harness configuration
harness:
  name: "inference-perf"                # Harness to use
  image: "ghcr.io/llm-d/llm-d-benchmark:latest"
  pod_label: "llmdbench-harness"        # Pod name prefix
  wait_timeout: 3600                    # Per-workload timeout (seconds)
  namespace: "my-ns"                    # Namespace for harness pod

# HuggingFace token
huggingface:
  secret_name: "hf-token"              # K8s secret name
  secret_key: "token"                   # Key within secret

# Storage
storage:
  pvc_name: "workload-pvc"             # PVC for results (if using PVC mode)

# Pre/post workload hooks (overridden by CLI flags)
hooks:
  pre_workload: "echo 'preparing'"
  post_workload: "echo 'cleaning up'"

# Stack metadata
stack:
  name: "my-stack"

# Custom environment variables injected into harness pod
env:
  MY_CUSTOM_VAR: "value"
  ANOTHER_VAR: "value2"

# Workloads to execute (one or more)
workload:
  - name: "chatbot-low"
    profile: chatbot_synthetic.yaml
    data:
      max_concurrency: 4
      num_prompts: 50

  - name: "chatbot-high"
    profile: chatbot_synthetic.yaml
    data:
      max_concurrency: 64
      num_prompts: 500
```

---

## Execution Flow

```
1. Parse CLI args + load config YAML
   ↓
2. Validate prerequisites
   ├── PVC exists (if PVC mode)
   ├── Cloud bucket reachable (if cloud mode)
   ├── HF token secret exists
   └── Endpoint reachable (curl /v1/completions)
   ↓
3. Create RBAC in harness namespace
   ├── ServiceAccount: llmdbench-harness-sa
   ├── Role: llmdbench-metrics-reader
   └── RoleBinding
   ↓
4. Create ConfigMap with workload profiles
   ↓
5. Launch harness pod
   ├── Image: harness container
   ├── Resources: 16 CPU, 32Gi memory
   ├── Volumes: PVC/emptyDir + ConfigMap + HF secret
   └── Wait for Ready (180s timeout)
   ↓
6. Execute workload loop
   For repeat = 1..N:
     For each workload in config:
       ├── Run pre-workload hook (kubectl exec)
       ├── Execute: llm-d-benchmark.sh --harness=<name> --workload=<name>
       ├── Apply timeout (harness_wait_timeout)
       └── Run post-workload hook (kubectl exec)
   ↓
7. Aggregate results (if --repeat > 1)
   └── Runs aggregate_runs.py for mean/stddev across runs
   ↓
8. Collect/upload results
   ├── PVC mode: print kubectl cp instructions
   ├── Local mode: results already at destination
   └── Cloud mode: upload via gcloud/aws CLI
```

---

## Pre/Post Workload Hooks

Hooks run **inside the harness pod** via `kubectl exec` before/after each workload execution.

```bash
# CLI flags override config values
./existing_stack/run_only.sh -c config.yaml \
  --pre-workload "python -c 'import requests; requests.post(\"http://reset-endpoint\")'" \
  --post-workload "cat /tmp/metrics.log"
```

**Use cases:**
- **Pre:** Clear caches, reset metrics, warm up model, prepare test data
- **Post:** Collect debug info, capture GPU metrics, save profiling data

**Failure handling:** Hook failures log a warning but do **not** block workload execution.

---

## Repeat & Aggregation (`--repeat`)

```bash
./existing_stack/run_only.sh -c config.yaml -R 5
```

Runs each workload 5 times with unique experiment IDs:

```
{uid}_{workload}_run1/
{uid}_{workload}_run2/
{uid}_{workload}_run3/
{uid}_{workload}_run4/
{uid}_{workload}_run5/
{uid}_{workload}_aggregated/    # Mean/stddev across runs
```

Aggregation uses `analysis/aggregate_runs.py` to compute statistical summaries.

---

## Output Destinations

### PVC Mode (Default)

Results written to `/requests/` on the workload PVC. Retrieve manually:

```bash
# List results
kubectl exec -n <ns> $(kubectl get pod -n <ns> -l role=llm-d-benchmark-data-access \
  -o jsonpath='{.items[0].metadata.name}') -- ls /requests/

# Copy results
kubectl cp <ns>/<pod>:/requests/<experiment-id> ./results/
```

### Local Mode

```bash
./existing_stack/run_only.sh -c config.yaml -o /path/to/results/
```

Results written directly to local filesystem via emptyDir + copy.

### Cloud Mode

```bash
# Google Cloud Storage
./existing_stack/run_only.sh -c config.yaml -o gs://my-bucket/benchmarks/

# Amazon S3
./existing_stack/run_only.sh -c config.yaml -o s3://my-bucket/benchmarks/
```

Requires `gcloud` or `aws` CLI installed and authenticated.

---

## Harness Pod Specification

The script creates a pod with:

| Resource | Value |
|----------|-------|
| CPU requests/limits | 16 cores |
| Memory requests/limits | 32Gi |
| Image | From config `harness.image` |
| ServiceAccount | `llmdbench-harness-sa` |
| Volumes | Workload PVC, profile ConfigMap, HF token secret |

### Environment Variables Injected

| Variable | Source | Description |
|----------|--------|-------------|
| `LLMDBENCH_RUN_WORKSPACE_DIR` | Hardcoded | `/workspace` |
| `LLMDBENCH_MAGIC_ENVAR` | Hardcoded | `harness_pod` |
| `LLMDBENCH_HARNESS_NAME` | Config | Harness name |
| `LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX` | Hardcoded | `/requests` |
| `LLMDBENCH_RUN_DATASET_DIR` | Hardcoded | `/workspace` |
| `LLMDBENCH_RUN_DATASET_URL` | Config | Dataset URL (optional) |
| `LLMDBENCH_HARNESS_STACK_NAME` | Config | Stack name |
| `LLMDBENCH_RUN_EXPERIMENT_ID` | Generated | Per workload/repeat |
| `HF_TOKEN` | K8s Secret | HuggingFace token |
| Custom vars | Config `.env` | User-defined |

---

## Endpoint Verification

Before launching workloads, the script verifies the endpoint is reachable:

```bash
# Launches ephemeral curl pod to test connectivity
kubectl run verify-endpoint --rm -it --image=alpine/curl -- \
  curl -s -o /dev/null -w '%{http_code}' \
  -X POST "${endpoint_base_url}/v1/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"<model>","prompt":"hello","max_tokens":1}'
```

Fails the script if endpoint is unreachable.

---

## Differences from Python CLI `run` Command

| Aspect | `run_only.sh` | `llmdbenchmark run` |
|--------|---------------|---------------------|
| **Dependencies** | bash, kubectl, yq | Python 3.11+, full llmdbenchmark package |
| **Deployment** | Assumes stack exists | Can deploy or use existing |
| **Config format** | Single YAML file | Spec + scenario + defaults |
| **Parallelism** | Single pod only | Configurable `-j N` |
| **Repeat/aggregate** | Built-in `--repeat` | Via experiment treatments |
| **Pre/post hooks** | Built-in | Not available |
| **DoE support** | No | Full factorial sweeps |
| **Profile rendering** | Direct from config | Jinja2 templates with token resolution |
| **Analysis** | Optional aggregation only | Full analysis pipeline |
| **Result format** | Raw harness output | Standardized benchmark reports |

### When to Use Which

- **`run_only.sh`**: Quick ad-hoc benchmarks against existing endpoints. Minimal setup. Built-in repeat + aggregation.
- **`llmdbenchmark run`**: Integrated with the full deployment lifecycle. DoE support. Parallel pods. Standardized analysis.

---

## Available Harnesses

Located in `workload/harnesses/`:

| Script | Harness |
|--------|---------|
| `inference-perf-llm-d-benchmark.sh` | inference-perf |
| `vllm-benchmark-llm-d-benchmark.sh` | vllm-benchmark |
| `guidellm-llm-d-benchmark.sh` | guidellm |
| `inferencemax-llm-d-benchmark.sh` | inferencemax |
| `nop-llm-d-benchmark.py` | nop (testing) |
| `collect_metrics.sh` | Metrics collection utility |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `PVC not found` | Verify PVC exists: `kubectl get pvc -n <ns>` |
| `HF token secret not found` | Create secret: `kubectl create secret generic hf-token --from-literal=token=hf_...` |
| `Endpoint unreachable` | Check service: `kubectl get svc -n <ns>`, verify model is loaded |
| `Pod startup timeout` | Check image exists, resources available on nodes |
| `Workload timeout` | Increase `harness.wait_timeout` in config |
| `Aggregation fails` | Verify `analysis/aggregate_runs.py` exists |
| `Cloud upload fails` | Verify `gcloud`/`aws` CLI installed and authenticated |

---

## Key Files

| File | Purpose |
|------|---------|
| `existing_stack/run_only.sh` | Main script (644 lines) |
| `workload/harnesses/` | Harness implementation scripts |
| `build/llm-d-benchmark.sh` | Harness pod entrypoint (called inside pod) |
| `analysis/aggregate_runs.py` | Result aggregation for `--repeat` |
