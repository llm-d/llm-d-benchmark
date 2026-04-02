---
name: llm-d-benchmark-run
description: |
  Deep reference for the llm-d-benchmark run phase — executing benchmarks against deployed or
  external LLM inference endpoints. TRIGGER when: user asks about running benchmarks, harness
  configuration, workload profiles, result collection, profile overrides, parallelism, run-only
  mode (--endpoint-url), or the 13 run-phase steps. DO NOT TRIGGER for standup, teardown,
  scenario creation, or template rendering (use the llm-d-benchmark skill instead).
---

# llm-d-benchmark Run Phase

The run phase deploys harness pods, executes benchmarks against an inference endpoint, collects
results, and optionally uploads them to cloud storage. It works in two modes: **full mode** (after
standup) or **run-only mode** (against an existing endpoint via `--endpoint-url`).

---

## CLI Usage

```bash
# Full mode (after standup)
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w chatbot_sharegpt.yaml

# Run-only mode (existing endpoint, no standup needed)
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w sanity_random.yaml \
  --endpoint-url http://10.0.0.1:8000

# With parallelism (4 harness pods per treatment)
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w chatbot_sharegpt.yaml \
  -j 4

# With profile overrides
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w chatbot_synthetic.yaml \
  -o "data.max_concurrency=32,data.num_prompts=500"

# With experiment treatments
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf \
  -e workload/experiments/tiered-prefix-cache.yaml

# Debug mode (pod sleeps infinity for inspection)
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w sanity_random.yaml --debug

# Generate config only (no execution)
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w sanity_random.yaml \
  --generate-config

# Upload results to cloud
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w chatbot_sharegpt.yaml \
  -r gs://my-bucket/results/

# Run with local analysis
llmdbenchmark --spec examples/gpu run -p my-ns -l inference-perf -w chatbot_sharegpt.yaml \
  --analyze
```

---

## All Flags

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `-p/--namespace` | `LLMDBENCH_NAMESPACE` | from plan | Namespace(s) (`deploy,harness` format) |
| `-t/--methods` | `LLMDBENCH_METHODS` | from plan | Deployment method (standalone, modelservice) |
| `-l/--harness` | `LLMDBENCH_HARNESS` | — | Harness name (**required**) |
| `-w/--workload` | `LLMDBENCH_WORKLOAD` | — | Workload profile name (e.g., `sanity_random.yaml`) |
| `-m/--model` | `LLMDBENCH_MODEL` | from plan | Model name override |
| `-e/--experiments` | `LLMDBENCH_EXPERIMENTS` | — | Experiment YAML with treatments |
| `-o/--overrides` | `LLMDBENCH_OVERRIDES` | — | Profile overrides (`key=value,key2=value2`) |
| `-r/--output` | `LLMDBENCH_OUTPUT` | `local` | Results destination (local, gs://, s3://) |
| `-j/--parallelism` | `LLMDBENCH_PARALLELISM` | 1 | Parallel harness pods per treatment |
| `--wait-timeout` | `LLMDBENCH_WAIT_TIMEOUT` | 3600 | Seconds to wait (0 = no wait) |
| `-x/--dataset` | `LLMDBENCH_DATASET` | — | Dataset URL for replay workloads |
| `-f/--monitoring` | — | false | Enable vLLM metrics scraping + pod logs |
| `-q/--serviceaccount` | `LLMDBENCH_SERVICE_ACCOUNT` | — | ServiceAccount for harness pods |
| `-g/--envvarspod` | `LLMDBENCH_HARNESS_ENVVARS_TO_YAML` | — | CSV of env vars to propagate to pods |
| `-z/--skip` | — | false | Skip execution, only collect existing results |
| `-d/--debug` | — | false | Debug mode (sleep infinity) |
| `--analyze` | `LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY` | false | Run local analysis |
| `-U/--endpoint-url` | `LLMDBENCH_ENDPOINT_URL` | — | Explicit endpoint (run-only mode) |
| `-c/--config` | — | — | Run config YAML path (run-only mode) |
| `--generate-config` | — | false | Generate config and exit |
| `-s/--step` | — | — | Run specific steps (e.g., `0,3-7,12`) |
| `-k/--kubeconfig` | `LLMDBENCH_KUBECONFIG` | — | Kubeconfig path |

---

## 13 Run Steps

| # | Step | Description |
|---|------|-------------|
| 00 | `RunPreflightStep` | Validate cluster connectivity, namespace exists, output destination reachable |
| 01 | `RunCleanupPreviousStep` | Remove previous harness pods and ConfigMaps (skipped with `--skip`) |
| 02 | `HarnessNamespaceStep` | Ensure harness namespace exists |
| 03 | `DetectEndpointStep` | Auto-detect inference endpoint. **Skipped** with `--endpoint-url` |
| 04 | `VerifyModelStep` | Confirm model is loaded via /v1/models |
| 05 | `RenderProfilesStep` | Render `.yaml.in` profile templates with experiment parameters |
| 06 | `CreateProfileConfigmapStep` | Upload rendered profiles as K8s ConfigMap |
| 07 | `DeployHarnessStep` | Deploy harness pods (parallelism × treatments) |
| 08 | `WaitCompletionStep` | Poll pods until complete. **Skipped** with `--debug` or `--wait-timeout=0` |
| 09 | `CollectResultsStep` | kubectl cp results from PVC via data-access pod |
| 10 | `UploadResultsStep` | Push to GCS/S3. **Skipped** if `--output local` |
| 11 | `RunCleanupPostStep` | Delete harness pods and ConfigMaps. **Skipped** with `--debug` |
| 12 | `AnalyzeResultsStep` | Run local analysis. Only runs with `--analyze` |

---

## Endpoint Detection (Step 03)

Three discovery methods based on deployment method:

| Method | Discovery | Stack Type |
|--------|-----------|------------|
| `standalone` | `find_standalone_endpoint()` — looks for vLLM service on inference port | `vllm-prod` |
| `modelservice` | `find_gateway_endpoint()` — looks for gateway/EPP service | `llm-d` |
| `--endpoint-url` | Uses provided URL directly, skips detection | from `--methods` |

Also auto-discovers HuggingFace token from cluster secrets matching `llm-d-hf*token*`.

---

## Profile Rendering (Step 05)

### Profile Templates

Located in `workload/profiles/<harness>/`. Files are `.yaml.in` templates with `REPLACE_ENV_*` placeholders:

```yaml
# Example: workload/profiles/inference-perf/chatbot_synthetic.yaml.in
backend: vllm
base_url: "REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL"
model: "REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL"
data:
  type: synthetic
  num_prompts: 100
  max_concurrency: 16
```

### Token Resolution

Placeholders are resolved from plan config and runtime values:

| Token | Source | Description |
|-------|--------|-------------|
| `LLMDBENCH_DEPLOY_CURRENT_MODEL` | `model.name` from plan | Model being served |
| `LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` | Detected in step 03 | Endpoint URL |
| `LLMDBENCH_RUN_DATASET_DIR` | `experiment.datasetDir` | Dataset path |

### Override Application (`--overrides`)

```bash
-o "data.max_concurrency=32,data.num_prompts=500,api.streaming=true"
```

Dotted keys walk the YAML tree. Values are type-coerced: `true`→bool, `100`→int, `1.5`→float.

### Treatment Processing (`--experiments`)

Each treatment in the experiment YAML creates a separate rendered profile:

```
base_profile.yaml.in  →  base_profile-treatment1.yaml
                      →  base_profile-treatment2.yaml
                      →  base_profile-treatment3.yaml
```

---

## Harness Pod Deployment (Step 07)

### Pod Naming

```
{harness}-{random8}     # Pod name
{harness}-{treatment}-{timestamp}-{random6}  # Experiment ID
```

### Parallelism

With `-j 4`, each treatment deploys 4 pods simultaneously:

```
Treatment "qlen100":
  Pod 1 → results: /requests/{exp-id}_1/
  Pod 2 → results: /requests/{exp-id}_2/
  Pod 3 → results: /requests/{exp-id}_3/
  Pod 4 → results: /requests/{exp-id}_4/
```

All pods wait together, then results collected per-pod.

### Sequential Treatment Processing

```
Treatment 1: deploy pods → wait → collect → cleanup
Treatment 2: deploy pods → wait → collect → cleanup
Treatment 3: ...
```

Treatments run sequentially. Parallelism is within a single treatment.

### Debug Mode

With `--debug`, the harness command is replaced with `sleep infinity`. The pod stays alive for interactive debugging:

```bash
kubectl exec -it -n <ns> <pod-name> -- bash
```

Steps 08 (wait), 09 (collect), 11 (cleanup) are skipped.

---

## Result Collection (Step 09)

1. Finds data-access pod by label `role=llm-d-benchmark-data-access`
2. Lists `/requests/` directory on the workload PVC
3. Matches directories to `context.experiment_ids`
4. Copies via `kubectl cp --retries=5` to local workspace
5. Reports file counts per experiment/parallel-pod

### Result Directory Structure

```
workspace/results/
├── {experiment_id}_1/
│   ├── requests.jsonl          # Raw request/response data
│   ├── results.yaml            # Aggregate metrics
│   └── ...
├── {experiment_id}_2/
│   └── ...
└── cross-treatment-comparison/ # Generated by --analyze
    ├── summary.yaml
    └── plots/
```

---

## Output Destinations (`--output`)

| Value | Behavior |
|-------|----------|
| `local` (default) | Results stay in `workspace/results/` |
| `gs://bucket/path` | Upload via gsutil after collection |
| `s3://bucket/path` | Upload via aws s3 cp after collection |

---

## Run-Only Mode

For benchmarking existing endpoints without standup:

```bash
# Direct endpoint
llmdbenchmark --spec examples/gpu run -p my-ns \
  -l inference-perf -w chatbot_sharegpt.yaml \
  --endpoint-url http://model-service.default.svc:8000

# From config file
llmdbenchmark --spec examples/gpu run -p my-ns \
  -c run-config.yaml
```

Steps 03 (detect endpoint) and namespace validation are skipped. All other steps execute normally.

---

## Available Harnesses

| Harness | Description |
|---------|-------------|
| `inference-perf` | Kubernetes-native benchmark (primary) |
| `vllm-benchmark` | vLLM project native benchmark |
| `guidellm` | Guided LLM benchmark |
| `inferencemax` | Max throughput benchmark |
| `nop` | No-op (testing only) |

## Available Profiles

| Profile | Description |
|---------|-------------|
| `chatbot_sharegpt` | Real ShareGPT conversation traces |
| `chatbot_synthetic` | Synthetic chat (configurable ISL/OSL) |
| `code_completion_synthetic` | Code completion workload |
| `shared_prefix_synthetic` | Shared system prompt + varying questions |
| `summarization_synthetic` | Long input, short output |
| `random_concurrent` | Random prompts at fixed concurrency |
| `sanity_random` | Minimal CI/CD sanity check |

---

## Run Summary Banner

Displayed at completion:

```
============================================================
BENCHMARK RUN SUMMARY
============================================================
  Harness:       inference-perf
  Workload:      chatbot_sharegpt.yaml
  Model:         meta-llama/Llama-3.1-8B
  Namespace:     my-ns
  Mode:          full
  Parallelism:   4
  Treatments:    2
    - inference-perf-qlen100-1704067200-aB3xYz
      [1/4] ... (45 files)
      [2/4] ... (48 files)
  Local results: /path/to/workspace/results
  PVC results:   kubectl exec -n my-ns $(...) -- ls /requests/
============================================================
```

---

## Key Files

| File | Purpose |
|------|---------|
| `llmdbenchmark/interface/run.py` | Run subcommand flag definitions |
| `llmdbenchmark/cli.py` (`_do_run`, `_execute_run`) | Run orchestration and summary |
| `llmdbenchmark/run/steps/` | All 13 step implementations |
| `llmdbenchmark/utilities/profile_renderer.py` | Profile template rendering |
| `llmdbenchmark/utilities/endpoint.py` | Endpoint discovery |
| `llmdbenchmark/utilities/kube_helpers.py` | Pod waiting, result collection |
| `llmdbenchmark/analysis/` | Local analysis module |
| `workload/profiles/` | Profile templates (.yaml.in) |
| `config/templates/jinja/20_harness_pod.yaml.j2` | Harness pod template |
