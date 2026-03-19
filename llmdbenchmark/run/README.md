# llmdbenchmark.run

Run phase of the benchmark lifecycle. Executes benchmark workloads against deployed model-serving infrastructure, collects results, and optionally runs local analysis.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_run_steps()` and execute in order:

| Step | Name | Description |
|------|------|-------------|
| 00 | `RunPreflightStep` | Validate cluster connectivity, harness namespace existence, and output destination reachability |
| 01 | `RunCleanupPreviousStep` | Clean up leftover harness pods from a previous run |
| 02 | `DetectEndpointStep` | Detect the model-serving endpoint for each stack (standalone, gateway, or custom) |
| 03 | `VerifyModelStep` | Verify the expected model is served at the detected endpoint via `/v1/models` |
| 04 | `RenderProfilesStep` | Render workload profile templates (`REPLACE_ENV_*` tokens) with runtime values; handle experiment treatments |
| 05 | `CreateProfileConfigmapStep` | Create Kubernetes ConfigMaps for workload profiles and harness scripts |
| 06 | `DeployHarnessStep` | Deploy harness pod(s), execute treatments sequentially (deploy, wait, collect, clean per treatment) |
| 07 | `WaitCompletionStep` | Wait for harness pod(s) to complete (used when step 06 does not inline waiting) |
| 08 | `CollectResultsStep` | Collect results from PVC to local workspace |
| 09 | `UploadResultsStep` | Upload results to cloud storage (GCS/S3) as a safety-net bulk upload |
| 10 | `RunCleanupPostStep` | Post-run cleanup of harness pods and ConfigMaps |
| 11 | `AnalyzeResultsStep` | Run local analysis on collected results using the `analysis` subpackage |

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
