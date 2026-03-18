# Run Phase

The run phase executes benchmark workloads against a deployed model-serving endpoint, collects results, and optionally uploads them to cloud storage. It is composed of 12 numbered steps orchestrated by the shared [`StepExecutor`](../executor/README.md).

## Run Steps

| Step | Name | Scope | Description | Skip Condition |
|------|------|-------|-------------|----------------|
| 00 | preflight | Global | Validate cluster connectivity and run-phase prerequisites | `--skip-run` |
| 01 | cleanup_previous | Global | Remove leftover harness pods from previous runs | `--skip-run` |
| 02 | detect_endpoint | Per-stack | Discover or accept the model-serving endpoint | Never |
| 03 | verify_model | Per-stack | Verify the expected model is served at the endpoint | `--skip-run` |
| 04 | render_profiles | Per-stack | Render workload profile templates with runtime values | Never |
| 05 | create_profile_configmap | Per-stack | Create profile and harness-scripts ConfigMaps | Never |
| 06 | deploy_harness | Per-stack | Deploy harness pod(s) and execute the full treatment cycle | `--skip-run` |
| 07 | wait_completion | Per-stack | Wait for harness pod(s) to complete | `--skip-run` |
| 08 | collect_results | Per-stack | Collect results from PVC to local workspace | Never |
| 09 | upload_results | Global | Upload results to cloud storage (safety-net bulk upload) | `--output local` |
| 10 | cleanup_post | Global | Clean up harness pods and ConfigMaps | `--debug` |
| 11 | analyze_results | Global | Run local analysis on collected results | `--debug` or `--no-analyze` |

**Scope:** Global steps run once per invocation. Per-stack steps run once per rendered stack (parallelizable via `--parallel`).

## Execution Modes

### Full Pipeline

The typical flow: standup deploys infrastructure, run benchmarks against it, teardown cleans up.

```bash
llmdbenchmark --spec gpu standup
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml
llmdbenchmark --spec gpu teardown
```

### Run-Only Mode (`--endpoint-url`)

Benchmark an existing endpoint without deploying infrastructure:

```bash
llmdbenchmark --spec gpu run \
  --endpoint-url http://10.131.0.42:80 \
  --model meta-llama/Llama-3.1-8B \
  --namespace my-namespace \
  --harness inference-perf \
  --workload sanity_random.yaml
```

Step 02 (detect_endpoint) accepts the URL directly instead of discovering it from the cluster.

### Skip-Run Mode (`--skip-run` / `-z`)

Collect results from a previous run without re-executing the benchmark:

```bash
llmdbenchmark --spec gpu run --skip-run
```

Steps 00, 01, 03, 06, and 07 are skipped. Only endpoint detection, profile rendering, result collection, upload, and analysis execute.

### Debug Mode (`--debug` / `-d`)

Deploy harness pods with `sleep infinity` for manual inspection:

```bash
llmdbenchmark --spec gpu run --debug -l inference-perf -w sanity_random.yaml
```

Pods remain running after the run phase completes. Step 10 (cleanup) and step 11 (analysis) are skipped.

## Sequential Per-Treatment Execution

Step 06 (deploy_harness) is the core orchestration step. When experiments define multiple treatments, step 06 executes them **sequentially** to prevent treatments from competing for cluster resources:

```text
For each treatment:
  1. Render and deploy harness pod(s)
  2. Wait for all pods to complete
  3. Collect results from each pod
  4. Upload per-pod results to cloud storage
  5. Capture pod logs and infrastructure logs
  6. Clean up treatment pods
```

This sequential model means steps 07-10 become **harmless no-ops** when step 06 runs the full cycle, because:

- **Step 07** (wait): Step 06 already waited for each treatment's pods.
- **Step 08** (collect): Step 06 already collected per-pod results.
- **Step 09** (upload): Step 06 already uploaded per-pod results. Step 09 serves as a safety-net bulk upload.
- **Step 10** (cleanup): Step 06 already deleted treatment pods between cycles.

Steps 07-10 remain in the pipeline for backward compatibility and for scenarios where step 06 is skipped (e.g., `--skip-run` mode uses step 08 to collect results from a previous run).

## Result Directory Structure

Results are collected into the workspace under a per-stack results directory:

```text
{workspace}/
└── {run-subdirectory}/
    ├── plan/
    │   └── stack-1/
    │       ├── config.yaml
    │       └── ...
    ├── results/
    │   ├── {experiment_id}_0/          Per-pod results (parallel index 0)
    │   │   ├── stage_0.json            Harness output
    │   │   ├── benchmark_report,...     Converted benchmark reports
    │   │   └── analysis/               Local analysis output
    │   ├── {experiment_id}_1/          Per-pod results (parallel index 1)
    │   │   └── ...
    │   └── {experiment_id_2}_0/        Second treatment results
    │       └── ...
    └── logs/
        ├── harness/                    Per-pod harness logs
        │   ├── pod-name-0.log
        │   └── pod-name-1.log
        └── infrastructure/             Infrastructure snapshots
            ├── pod-status.txt
            ├── model-serving.log
            └── epp.log
```

The `_{i}` suffix on result directories corresponds to the parallel pod index (`-j N` flag). With parallelism of 1 (default), only `_0` appears.

## Cloud Upload Flow

Results are uploaded to cloud storage (GCS or S3) in two stages:

1. **Per-pod upload** (step 06): Each pod's results are uploaded immediately after collection during the treatment cycle. This ensures partial results are preserved even if later treatments fail.

2. **Bulk upload** (step 09): A safety-net upload of the entire results directory. Catches any results missed by the per-pod upload.

The upload destination is set via `--output` / `-r`:

```bash
# Google Cloud Storage
llmdbenchmark ... run -r gs://my-bucket/benchmark-results/

# Amazon S3
llmdbenchmark ... run -r s3://my-bucket/benchmark-results/

# Local only (no upload)
llmdbenchmark ... run -r local
```

## Experiment Integration

Run treatments can be defined in an experiment YAML file via `--experiments`:

```bash
llmdbenchmark --spec gpu run \
  --experiments workload/experiments/inference-scheduling.yaml
```

Step 04 (render_profiles) reads the experiment file and generates one treatment per entry in the `treatments` list. Step 06 then iterates through all treatments sequentially.

For full DoE support (infrastructure × workload sweeps), use the `experiment` command instead, which orchestrates standup/run/teardown cycles per setup treatment. See [`experiment/README.md`](../experiment/README.md).

## Harness Support

The run phase supports multiple benchmark harnesses, selected via `--harness` / `-l`:

| Harness | Description |
|---------|-------------|
| `inference-perf` | Kubernetes-native inference performance testing |
| `guidellm` | vLLM project's load generation tool |
| `vllm-benchmark` | vLLM's built-in benchmark suite |
| `inferencemax` | InferenceMAX benchmark |
| `nop` | No-operation (model load time measurement only) |

Each harness uses the same profile rendering and result collection pipeline. Harness-specific analysis scripts convert raw output to standardized benchmark report format (v0.1 and v0.2).
