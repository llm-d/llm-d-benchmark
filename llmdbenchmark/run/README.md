# llmdbenchmark.run

Run phase of the benchmark lifecycle. Executes benchmark workloads against deployed model-serving infrastructure, collects results, and optionally runs local analysis.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_run_steps()` and execute in order:

| Step | Name | Scope | Description |
|------|------|-------|-------------|
| 00 | `RunPreflightStep` | global | Validate cluster connectivity, harness namespace existence, and output destination reachability |
| 01 | `RunCleanupPreviousStep` | global | Clean up leftover harness pods from a previous run |
| 02 | `DetectEndpointStep` | global | Detect the model-serving endpoint for each stack (standalone, gateway, or custom) |
| 03 | `VerifyModelStep` | global | Verify the expected model is served at the detected endpoint via `/v1/models` |
| 04 | `RenderProfilesStep` | global | Render workload profile templates (`REPLACE_ENV_*` tokens) with runtime values; handle experiment treatments |
| 05 | `CreateProfileConfigmapStep` | global | Create Kubernetes ConfigMaps for workload profiles and harness scripts |
| 06 | `DeployHarnessStep` | global | Deploy harness pod(s), execute treatments sequentially (deploy, wait, collect, clean per treatment) |
| 07 | `WaitCompletionStep` | global | Wait for harness pod(s) to complete (used when step 06 does not inline waiting) |
| 08 | `CollectResultsStep` | global | Collect results from PVC to local workspace |
| 11 | `AnalyzeResultsStep` | global | Run local analysis on collected results using the `analysis` subpackage |
| 09 | `UploadResultsStep` | global | Upload results to cloud storage (GCS/S3) as a safety-net bulk upload |
| 10 | `RunCleanupPostStep` | global | Post-run cleanup of harness pods and ConfigMaps |

Note: The execution order in `get_run_steps()` places `AnalyzeResultsStep` (step 11) before `UploadResultsStep` (step 09) so that analysis artifacts are included in the upload.

## Key Flags

### `-f` / `--monitoring` -- Enable metrics scraping and log capture

When passed, `-f` enables three things during the run:

1. Sets `LLMDBENCH_VLLM_COMMON_METRICS_SCRAPE_ENABLED=true` as an environment variable on the harness pod, which tells the harness entrypoint to scrape vLLM `/metrics` before and after each benchmark.
2. After each treatment completes, captures pod logs from harness pods, EPP (inference scheduler) pods, IGW (inference gateway) pods, and model-serving pods into the `logs/` directory under the run workspace.
3. Runs `process_epp_logs.py` on captured EPP logs to extract flow control and scheduling metrics.

### `-l` / `--harness` -- Harness override

Overrides the scenario's `harness.name` value for the run. This affects both the ConfigMap name used for workload profiles and the pod template selected for the harness pod. Supported values: `inference-perf`, `guidellm`, `vllm-benchmark`, `inferencemax`, `nop`.

### `-g` / `--envvarspod` -- Propagate env vars into harness pod

Accepts a comma-separated list of environment variable names (e.g., `-g MY_TOKEN,MY_FLAG`). Each named variable is read from the current shell and injected into the harness pod's environment. Useful for passing credentials or feature flags that the harness entrypoint needs at runtime.

### `-q` / `--serviceaccount` -- Service account for harness pods

Sets the `serviceAccountName` on the harness pod spec. Required when the harness needs RBAC permissions beyond the default service account (e.g., for metrics scraping or PVC access in locked-down namespaces).

## Treatment System

Step 04 handles experiment treatments from the `--experiments` YAML file. Multiple treatments execute against a single stood-up stack. For each treatment, step 06 deploys a harness pod, waits for completion, and collects results before proceeding to the next treatment.

## Result Collection

Results are collected to two locations:

- **Local workspace** -- Step 08 copies results from the harness PVC to the local workspace directory under `results/`. Each treatment gets its own subdirectory named `{experiment_id}_{parallel_idx}`.
- **PVC** -- Results are written to the harness PVC by the harness pod during execution. The PVC persists across runs until teardown.

Optionally, step 09 uploads results to cloud storage (GCS or S3) when `-r` points to a `gs://` or `s3://` destination.

## EPP Log Processing

When monitoring is enabled, `capture_infrastructure_logs()` in `kube_helpers.py` captures EPP pod logs and runs `process_epp_logs.py` to extract scheduling and flow control metrics. The script reads the raw EPP logs and produces visualizations in the results directory.

## Dry-Run Behavior

In dry-run mode:

- Steps 00-05 log what they would do without modifying the cluster.
- Step 06 logs the harness pod spec without deploying. Wait helpers return success immediately.
- Steps 08-11 skip actual file operations and cloud uploads, logging what would happen.

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
