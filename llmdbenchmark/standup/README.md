# llmdbenchmark.standup

Standup phase of the benchmark lifecycle. Provisions infrastructure, creates namespaces, deploys model-serving pods, and validates deployment health.

## Step Ordering

Steps are registered in `steps/__init__.py` via `get_standup_steps()` and execute in order:

| Step | Name | Description |
|------|------|-------------|
| 00 | `EnsureInfraStep` | Validate system dependencies (kubectl, helm, etc.) and print cluster summary banner |
| 02 | `AdminPrerequisitesStep` | Install cluster-level admin prerequisites (CRDs, gateways, LeaderWorkerSet, SCCs) |
| 03 | `WorkloadMonitoringStep` | Validate cluster resources and configure workload monitoring (PodMonitors) |
| 04 | `ModelNamespaceStep` | Prepare the model namespace (PVC, secrets, model download job) |
| 05 | `HarnessNamespaceStep` | Prepare the harness namespace (PVC, data access pod, secrets) |
| 06 | `StandaloneDeployStep` | Deploy vLLM as standalone Kubernetes Deployments and Services |
| 07 | `DeploySetupStep` | Set up Helm repos and deploy gateway infrastructure for modelservice mode |
| 08 | `DeployGaieStep` | Deploy GAIE (Gateway API Inference Extension) |
| 09 | `DeployModelserviceStep` | Deploy the model via the llm-d modelservice Helm chart |
| 10 | `SmoketestStep` | Health check, inference test, per-scenario config validation (delegates to `llmdbenchmark.smoketests`) |
| 11 | `InferenceTestStep` | Run sample inference request against deployed model |

Note: Step 01 is intentionally absent (reserved or removed).

## Post-standup smoketests

After standup completes, smoketests run automatically. The smoketest phase has three steps:

1. **Health check** (step 00) -- pod status, `/health`, `/v1/models`, service reachability, pod direct IP, OpenShift route
2. **Inference test** (step 01) -- sends a sample request via `/v1/completions` (falls back to `/v1/chat/completions`), logs the response and a demo curl command
3. **Config validation** (step 02) -- per-scenario validators compare live pod specs against the rendered config

Use `--skip-smoketest` to skip the automatic post-standup smoketests. They can also be run independently via `llmdbenchmark smoketest`. See [smoketests/README.md](../smoketests/README.md) for details.

## `-f` / `--monitoring` flag

When passed, `-f` enables monitoring infrastructure during standup:

- Creates PodMonitor resources for Prometheus to scrape vLLM pods
- Sets EPP (inference scheduler) log verbosity to level 4 for detailed scheduling diagnostics

This is separate from the run-phase `-f` flag, which controls metrics scraping and log capture during benchmark execution.

## preprocess/ Subdirectory

Contains scripts executed during standalone deployment setup:

| File | Description |
|------|-------------|
| `set_llmdbench_environment.py` | Network environment detection (IP addresses, RDMA/IB devices, GID mapping) for NIXL connectivity |
| `standalone-preprocess.py` | Serialize tensorizer files if needed; runs as a pre-deployment step |

## Files

```
standup/
├── __init__.py              -- Package marker
├── preprocess/
│   ├── set_llmdbench_environment.py
│   └── standalone-preprocess.py
└── steps/
    ├── __init__.py           -- Step registry (get_standup_steps)
    ├── step_00_ensure_infra.py
    ├── step_02_admin_prerequisites.py
    ├── step_03_workload_monitoring.py
    ├── step_04_model_namespace.py
    ├── step_05_harness_namespace.py
    ├── step_06_standalone_deploy.py
    ├── step_07_deploy_setup.py
    ├── step_08_deploy_gaie.py
    ├── step_09_deploy_modelservice.py
    ├── step_10_smoketest.py
    └── step_11_inference_test.py
```
