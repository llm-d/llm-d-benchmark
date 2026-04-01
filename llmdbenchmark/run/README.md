# llmdbenchmark.run

Run phase of the benchmark lifecycle. Executes benchmark workloads against
deployed model-serving infrastructure, collects results, and optionally runs
local analysis.

## Quick Start

### Run against a stood-up stack

After `standup`, run a benchmark with a specific harness and workload:

```bash
# Sanity check with inference-perf
llmdbenchmark --spec guides/pd-disaggregation run -p <NS> \
  -l inference-perf -w sanity_random.yaml

# Run with monitoring (metrics scraping + pod log capture)
llmdbenchmark --spec guides/pd-disaggregation run -p <NS> \
  -l inference-perf -w sanity_random.yaml -f

# Run with a different harness
llmdbenchmark --spec guides/pd-disaggregation run -p <NS> \
  -l vllm-benchmark -w random_concurrent.yaml
```

### Run-only mode (no standup needed)

Point directly at an existing endpoint -- no `--spec` or prior `standup` required:

```bash
# Against a known service URL
llmdbenchmark run -p <NS> \
  -U http://my-model-service.<NS>.svc.cluster.local:80 \
  -l inference-perf -w sanity_random.yaml -m Qwen/Qwen3-32B

# Against an external URL
llmdbenchmark run -p <NS> \
  -U https://my-model.example.com/v1 \
  -l inference-perf -w chatbot_synthetic.yaml -m Qwen/Qwen3-32B
```

When `-U` is provided, the run skips endpoint auto-detection (step 02) and
model verification (step 03), and goes straight to profile rendering and
harness deployment.

### Finding the service URL from an existing deployment

If a stack is already deployed but you don't know the endpoint URL:

```bash
# Modelservice -- get the gateway service URL
oc get svc -n <NS> -l app.kubernetes.io/name=llm-d-infra -o jsonpath='{.items[0].metadata.name}'
# Typically: http://infra-llmdbench-inference-gateway-istio.<NS>.svc.cluster.local:80

# Standalone -- get the standalone service URL
oc get svc -n <NS> -l app.kubernetes.io/managed-by=llm-d-benchmark -o jsonpath='{.items[0].metadata.name}'
# Typically: http://vllm-standalone-<model-id>.<NS>.svc.cluster.local:8000

# OpenShift route (external access)
oc get route -n <NS> -o jsonpath='{.items[0].spec.host}'
# Use: http://<route-host>

# Verify the endpoint is serving
oc exec -n <NS> $(oc get pod -n <NS> -l role=llm-d-benchmark-data-access -o jsonpath='{.items[0].metadata.name}') \
  -- curl -s http://infra-llmdbench-inference-gateway-istio.<NS>.svc.cluster.local:80/v1/models
```

### Generate a run config for reuse

```bash
# Generate a config YAML from current settings
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml --generate-config

# Use the generated config for subsequent runs
llmdbenchmark run -c /path/to/run-config.yaml
```

### Debug mode

Start the harness pod with `sleep infinity` instead of running the benchmark.
Useful for exec-ing into the pod to debug issues:

```bash
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml -d

# Then exec into the pod:
oc exec -it -n <NS> $(oc get pod -n <NS> -l app=llmdbench-harness-launcher -o name) -- bash
```

### Skip execution, collect existing results

If a previous run left results on the PVC, collect them without re-running:

```bash
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -z
```

## CLI Flags

| Flag | Env Var | Description |
|------|---------|-------------|
| `-l HARNESS` | `LLMDBENCH_HARNESS` | Harness name: `inference-perf`, `guidellm`, `vllm-benchmark`, `inferencemax`, `nop` |
| `-w WORKLOAD` | `LLMDBENCH_WORKLOAD` | Workload profile name (e.g., `sanity_random.yaml`, `chatbot_synthetic.yaml`) |
| `-p NS` | `LLMDBENCH_NAMESPACE` | Namespace(s) -- `deploy_ns,harness_ns` or single namespace for both |
| `-m MODEL` | `LLMDBENCH_MODEL` | Model name override (e.g., `Qwen/Qwen3-32B`) |
| `-t METHODS` | `LLMDBENCH_METHODS` | Deploy method used during standup (`standalone`, `modelservice`) |
| `-U URL` | `LLMDBENCH_ENDPOINT_URL` | Explicit endpoint URL -- enables run-only mode, skips auto-detection |
| `-c FILE` | | Run config YAML file -- enables run-only mode |
| `--generate-config` | | Generate a run config YAML from current settings and exit |
| `-f` | `LLMDBENCH_MONITORING` | Enable vLLM metrics scraping and pod log capture |
| `-q SA` | `LLMDBENCH_SERVICE_ACCOUNT` | Service account for harness pods |
| `-g VARS` | `LLMDBENCH_HARNESS_ENVVARS_TO_YAML` | Comma-separated env var names to propagate into harness pod |
| `-e FILE` | `LLMDBENCH_EXPERIMENTS` | Experiment treatments YAML for parameter sweeping |
| `-o OVERRIDES` | `LLMDBENCH_OVERRIDES` | Workload parameter overrides (`param=value,...`) |
| `-j N` | `LLMDBENCH_PARALLELISM` | Number of parallel harness pods (default: 1) |
| `-r DEST` | `LLMDBENCH_OUTPUT` | Results destination: local path, `gs://bucket`, or `s3://bucket` |
| `-x DATASET` | `LLMDBENCH_DATASET` | Dataset URL for harness replay |
| `--wait-timeout N` | `LLMDBENCH_WAIT_TIMEOUT` | Seconds to wait for harness completion (default: 3600) |
| `-z` | `LLMDBENCH_SKIP` | Skip execution, only collect existing results from PVC |
| `-d` | `LLMDBENCH_DEBUG` | Debug mode -- start harness with `sleep infinity` |
| `--analyze` | | Run local analysis on collected results |
| `-s STEPS` | | Step filter (e.g., `0,1,6` or `2-8`) |
| `-k FILE` | `LLMDBENCH_KUBECONFIG` | Kubeconfig path |

## Step Details

Steps are registered in `steps/__init__.py` via `get_run_steps()`:

| Step | Name | Description |
|------|------|-------------|
| 00 | `RunPreflightStep` | Validate cluster connectivity, harness namespace, output destination |
| 01 | `RunCleanupPreviousStep` | Delete leftover harness pods/configmaps from previous runs |
| 02 | `HarnessNamespaceStep` | Prepare harness namespace (PVC, data access pod) |
| 03 | `DetectEndpointStep` | Auto-detect model-serving endpoint (standalone service, gateway, or `-U` override) |
| 04 | `VerifyModelStep` | Verify model is served at endpoint via `/v1/models` |
| 05 | `RenderProfilesStep` | Render workload profile templates with runtime values; handle experiment treatments |
| 06 | `CreateProfileConfigmapStep` | Create ConfigMaps for workload profiles and harness scripts |
| 07 | `DeployHarnessStep` | Deploy harness pod(s), wait for completion, collect results, capture logs |
| 08 | `WaitCompletionStep` | Wait for harness pods (used when step 07 does not inline waiting) |
| 09 | `CollectResultsStep` | Collect results from PVC to local workspace |
| 12 | `AnalyzeResultsStep` | Run local analysis on results (before upload so artifacts are included) |
| 10 | `UploadResultsStep` | Upload results to cloud storage (GCS/S3) |
| 11 | `RunCleanupPostStep` | Delete harness pods and ConfigMaps |

Note: Step 12 (analyze) runs before step 10 (upload) so analysis artifacts are included in the upload.

## Common Patterns

### Run against different harnesses

The `-l` flag overrides the scenario's default harness. The harness name determines
which scripts run inside the harness pod and which profiles are available:

```bash
# inference-perf (default for most well-lit paths)
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -l inference-perf -w sanity_random.yaml

# vllm-benchmark (built-in vLLM benchmarking)
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -l vllm-benchmark -w random_concurrent.yaml

# guidellm
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -l guidellm -w chatbot_synthetic.yaml

# nop (no-op -- measures model load time only)
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -l nop -w nop.yaml
```

### Run with workload parameter overrides

Override individual workload profile parameters without editing the profile YAML:

```bash
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml \
  -o "concurrency=32,duration=300,max_tokens=512"
```

### Run with experiment treatments

Execute a matrix of parameter combinations automatically:

```bash
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml \
  -e experiments/concurrency_sweep.yaml
```

The experiment YAML defines factors and levels:

```yaml
run:
  factors:
    - name: concurrency
      levels: [1, 8, 32, 64]
    - name: max_tokens
      levels: [128, 512]
```

Each combination becomes a treatment. Step 06 runs them sequentially:
deploy pod, wait, collect, clean, then next treatment.

### Run with parallel harness pods

Deploy multiple harness pods per treatment for higher aggregate load:

```bash
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml -j 4
```

Each pod gets a unique experiment ID suffix (`_1`, `_2`, etc.) and writes
results to a separate subdirectory on the PVC.

### Run with monitoring enabled

```bash
llmdbenchmark --spec guides/pd-disaggregation run -p <NS> \
  -l inference-perf -w sanity_random.yaml -f
```

With `-f`, the run:

1. Sets `LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED=true` on the harness pod --
   the harness entrypoint scrapes vLLM `/metrics` before and after each benchmark
2. After each treatment, captures logs from:
   - Harness pods
   - EPP (Endpoint Picker) pods
   - IGW (Inference Gateway) pods
   - Model-serving (decode/prefill) pods
3. Runs `process_epp_logs.py` on EPP logs to extract scheduling metrics

Results appear in the workspace under:

```
results/
  {experiment_id}/
    metrics/raw/          -- raw Prometheus-format metrics per pod
    metrics/processed/    -- aggregated metrics_summary.json
logs/
  epp_pods.log            -- EPP pod logs
  igw_pods.log            -- Gateway pod logs
  modelserving_pods.log   -- Decode/prefill pod logs
  pod_status.txt          -- Pod status snapshot
epp_metrics/              -- EPP analysis output (if available)
```

### Upload results to cloud storage

```bash
# Google Cloud Storage
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml -r gs://my-bucket/results/

# Amazon S3
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml -r s3://my-bucket/results/
```

### Run specific steps only

```bash
# Only deploy and wait (skip cleanup, analysis, upload)
llmdbenchmark --spec guides/inference-scheduling run -p <NS> \
  -l inference-perf -w sanity_random.yaml -s 0-8

# Only collect existing results and analyze
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -s 8,11

# Only clean up leftover pods
llmdbenchmark --spec guides/inference-scheduling run -p <NS> -s 10
```

## Treatment System

The run supports three modes of treatment generation:

1. **Single treatment (default)** -- one harness pod runs the workload profile as-is
2. **Override treatments** -- `-o` modifies profile parameters for a single treatment
3. **Experiment treatments** -- `-e` generates a matrix of treatments from factor/level combinations

Step 04 handles the treatment rendering. Step 06 executes them sequentially:
for each treatment, it deploys harness pod(s), waits for completion, collects
results and logs, then cleans up before the next treatment.

## Result Collection

Results are collected to two locations:

- **Local workspace** -- Step 08 copies results from the harness PVC to the local
  workspace under `results/`. Each treatment gets its own subdirectory named
  `{experiment_id}_{parallel_idx}`.
- **PVC** -- Results persist on the harness PVC (`workload-pvc`) until teardown.

The run summary at the end shows both locations:

```
BENCHMARK RUN SUMMARY
  Local results: /Users/user/data/pd-disaggregation/vezio-20260321-211419-773/results
  PVC results:   oc exec -n ns $(oc get pod -n ns -l role=llm-d-benchmark-data-access ...) -- ls /requests/
```

## Dry-Run Behavior

In dry-run mode (`--dry-run`):

- Steps 00-05 log what they would do without modifying the cluster
- Step 06 logs the harness pod spec without deploying
- Steps 08-11 skip file operations and cloud uploads, logging what would happen
- All logged commands show the exact kubectl/oc command that would execute

## Files

```
run/
├── __init__.py              -- Package marker
└── steps/
    ├── __init__.py           -- Step registry (get_run_steps)
    ├── step_00_preflight.py
    ├── step_01_cleanup_previous.py
    ├── step_02_detect_endpoint.py
    ├── step_03_verify_model.py
    ├── step_04_render_profiles.py
    ├── step_05_create_profile_configmap.py
    ├── step_06_deploy_harness.py
    ├── step_07_wait_completion.py
    ├── step_08_collect_results.py
    ├── step_09_upload_results.py
    ├── step_10_cleanup_post.py
    └── step_11_analyze_results.py
```
